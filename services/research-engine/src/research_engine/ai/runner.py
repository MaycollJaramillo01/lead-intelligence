"""One inference, end to end: cache, queue, placement, call, check, repair, record.

    cache hit? ─ yes → the stored result, no call
      └ run row (queued) → GPU queue → breaker allows? → placement (estimate vs free VRAM)
          → call (strict schema, low temperature, fixed seed) → check the answer
              invalid and a repair left → one repair turn with the errors
              out of memory → context halved, then CPU if the profile allows (at most two steps)
          → answers stored raw and validated, run finished with tokens, duration and device

Every exit writes the run's final status; a cancellation or a shutdown marks it cancelled.
"""

import asyncio
import base64
import logging
import sqlite3
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from statistics import median
from typing import Final

from pydantic import BaseModel, JsonValue

from research_engine import repository
from research_engine.ai import store, tasks, vram
from research_engine.ai.resources import ResourceMonitor
from research_engine.ai.scheduler import CircuitBreaker, GpuQueue, Priority
from research_engine.domain import (
    AiCircuitState,
    AiDevice,
    AiOutcome,
    AiProfile,
    AiReviewStatus,
    AiUnavailableReason,
    AiVramEstimate,
    InferenceOutput,
    InferenceRun,
    InferenceStatus,
    JsonObject,
    new_id,
    utc_now,
)
from research_engine.ollama import ChatMessage, ChatRequest, ModelDetails, Provider, ProviderError

logger = logging.getLogger(__name__)

MAX_DEGRADATIONS: Final = 2
KEEP_ALIVE: Final = "5m"


@dataclass(frozen=True, slots=True)
class Assignment:
    """The model a task runs on right now, as the settings and the runtime say."""

    profile: AiProfile
    model: str
    digest: str | None
    num_ctx: int
    allow_cpu: bool
    capabilities: tuple[str, ...]
    size_bytes: int | None


@dataclass(frozen=True, slots=True)
class Outcome:
    run_id: str | None
    status: InferenceStatus | None
    output: JsonObject | None = None
    cached: bool = False
    skipped: AiUnavailableReason | None = None
    duration_ms: int | None = None
    completion_tokens: int | None = None
    #: Time the runtime spent generating (tokens per second = completion_tokens / eval_ms).
    eval_ms: int | None = None
    device: AiDevice = AiDevice.UNKNOWN
    error_code: str | None = None


@dataclass(slots=True)
class Counters:
    completed: int = 0
    failed: int = 0
    cache_hits: int = 0
    latencies: deque[int] = field(default_factory=lambda: deque(maxlen=50))

    def latency_p50(self) -> int | None:
        return round(median(self.latencies)) if self.latencies else None


class Runner:
    def __init__(
        self,
        provider: Provider,
        queue: GpuQueue,
        breaker: CircuitBreaker,
        connect: Callable[[], sqlite3.Connection],
        monitor: ResourceMonitor,
    ) -> None:
        self.provider = provider
        self.queue = queue
        self.breaker = breaker
        self._connect = connect
        self._monitor = monitor
        self._details: dict[str, ModelDetails] = {}
        self.counters = Counters()

    async def _store[T](self, action: Callable[[sqlite3.Connection], T]) -> T:
        def work() -> T:
            conn = self._connect()
            try:
                return action(conn)
            finally:
                conn.close()

        return await asyncio.to_thread(work)

    async def model_details(self, model: str, digest: str | None) -> ModelDetails:
        key = f"{model}@{digest}"
        if key not in self._details:
            self._details[key] = await self.provider.show(model)
        return self._details[key]

    # ---- public -----------------------------------------------------------------------------

    async def infer(
        self,
        prepared: tasks.PreparedInput,
        assignment: Assignment,
        *,
        project_id: str | None,
        business_id: str | None,
        job_id: str | None,
        priority: Priority,
        owner: str,
        record: bool = True,
    ) -> Outcome:
        """Runs one task. `record` false (benchmarks) keeps nothing in the store."""
        spec = tasks.TASKS[prepared.task_type]
        parameters: dict[str, JsonValue] = {
            "temperature": spec.temperature,
            "num_ctx": assignment.num_ctx,
            "num_predict": spec.max_output_tokens,
            "seed": tasks.SEED,
        }
        key = tasks.cache_key(
            prepared,
            provider=self.provider.name,
            model=assignment.model,
            model_digest=assignment.digest,
            parameters=parameters,
        )
        if record and (hit := await self._store(lambda conn: store.cached(conn, key))) is not None:
            run, output = hit
            self.counters.cache_hits += 1
            return Outcome(run.id, run.status, output, cached=True, device=run.device)
        if self.breaker.state is AiCircuitState.OPEN:
            return Outcome(None, None, skipped=AiUnavailableReason.CIRCUIT_OPEN)

        run_id = new_id()
        if record:
            now = utc_now()
            prompt = tasks.template(prepared.task_type)
            run = InferenceRun(
                id=run_id, task_type=prepared.task_type, task_version=spec.version,
                project_id=project_id, business_id=business_id, job_id=job_id,
                provider=self.provider.name, model=assignment.model,
                model_digest=assignment.digest, profile=assignment.profile,
                parameters_json=parameters, prompt_template_id=prompt.id,
                prompt_template_version=prompt.version, output_schema_version=spec.schema_version,
                input_sha256=prepared.sha256(), input_json=prepared.input_json(), cache_key=key,
                status=InferenceStatus.QUEUED, device=AiDevice.UNKNOWN, attempts=0,
                prompt_tokens=None, completion_tokens=None, duration_ms=None, error_code=None,
                error_message=None, review_status=AiReviewStatus.UNREVIEWED,
                started_at=None, finished_at=None, created_at=now, updated_at=now,
            )  # fmt: skip
            await self._store(lambda conn: repository.insert(conn, run))

        async def work() -> Outcome:
            return await self._execute(run_id, prepared, assignment, parameters, record)

        try:
            future = self.queue.submit(work, priority=priority, owner=owner, group=assignment.model)
        except Exception:
            if record:
                await self._finish(run_id, InferenceStatus.CANCELLED, error_code="queue_full")
            raise
        try:
            return await future
        except asyncio.CancelledError:
            if record:
                await asyncio.shield(
                    self._finish(run_id, InferenceStatus.CANCELLED, error_code="cancelled")
                )
            if future.cancelled() and not _current_task_cancelling():
                return Outcome(run_id if record else None, InferenceStatus.CANCELLED)
            raise

    # ---- inside the queue ---------------------------------------------------------------------

    async def _finish(self, run_id: str, status: InferenceStatus, **fields: object) -> None:
        now = utc_now()

        def write(conn: sqlite3.Connection) -> None:
            try:
                store.finish(conn, run_id, status=status, now=now, **fields)  # type: ignore[arg-type]
            except sqlite3.IntegrityError:
                # an identical request stored its valid answer first: keep that one
                store.finish(
                    conn, run_id, status=InferenceStatus.CANCELLED, now=now,
                    error_code="duplicate_result",
                    error_message="an identical request already has a valid answer",
                )  # fmt: skip

        await self._store(write)

    async def _execute(
        self,
        run_id: str,
        prepared: tasks.PreparedInput,
        assignment: Assignment,
        parameters: dict[str, JsonValue],
        record: bool,
    ) -> Outcome:
        started = time.perf_counter()
        if not self.breaker.allow():
            if record:
                await self._finish(
                    run_id, InferenceStatus.FAILED, error_code="circuit_open",
                    error_message="the runtime failed repeatedly; waiting before trying again",
                )  # fmt: skip
            return Outcome(run_id, InferenceStatus.FAILED, skipped=AiUnavailableReason.CIRCUIT_OPEN)
        if record:
            now = utc_now()
            await self._store(lambda conn: store.start(conn, run_id, now))

        spec = tasks.TASKS[prepared.task_type]
        degraded: list[JsonValue] = []
        options: dict[str, object] = {
            key: value for key, value in parameters.items() if key != "num_ctx"
        }
        num_ctx = int(str(parameters["num_ctx"]))
        tokens_in = tokens_out = eval_ms = 0
        attempts = 0
        status = InferenceStatus.FAILED
        error_code: str | None = None
        error_message: str | None = None
        output: BaseModel | None = None
        device = AiDevice.UNKNOWN
        try:
            details = await self.model_details(assignment.model, assignment.digest)
            placement = await self._placement(assignment, details, num_ctx)
            if placement.device in (AiDevice.PARTIAL, AiDevice.CPU) and not assignment.allow_cpu:
                error_code = "insufficient_vram"
                error_message = placement.note or "the model does not fit in the free VRAM"
                raise _Stop
            if placement.fitted_ctx is not None and placement.device is AiDevice.GPU:
                degraded.append(f"context_reduced:{num_ctx}->{placement.fitted_ctx}")
                num_ctx = placement.fitted_ctx
            system, user = tasks.render(prepared)
            images = tuple(base64.b64encode(image).decode() for image in prepared.images)
            messages = [ChatMessage("system", system), ChatMessage("user", user, images)]
            think = False if "thinking" in details.capabilities else None
            repairs = 0
            while True:
                request = ChatRequest(
                    model=assignment.model,
                    messages=tuple(messages),
                    schema=tasks.model_schema(prepared.task_type),
                    options={**options, "num_ctx": num_ctx},
                    timeout_s=spec.timeout_s,
                    think=think,
                    keep_alive=KEEP_ALIVE,
                )
                attempts += 1
                try:
                    answer = await self.provider.chat(request)
                except ProviderError as exc:
                    if exc.code == "out_of_memory" and len(degraded) < MAX_DEGRADATIONS:
                        if num_ctx > vram.MIN_CTX:
                            degraded.append(f"out_of_memory:context {num_ctx}->{num_ctx // 2}")
                            num_ctx = max(vram.MIN_CTX, num_ctx // 2)
                            continue
                        if assignment.allow_cpu and options.get("num_gpu") != 0:
                            degraded.append("out_of_memory:ran_on_cpu")
                            options["num_gpu"] = 0
                            continue
                    if exc.counts_against_runtime:
                        self.breaker.failure(str(exc))
                    else:
                        self.breaker.release()
                    error_code, error_message = exc.code, str(exc)
                    raise _Stop from exc
                self.breaker.success()
                tokens_in += answer.prompt_tokens or 0
                tokens_out += answer.completion_tokens or 0
                eval_ms += answer.eval_ms or 0
                checked = tasks.check_answer(answer.content, prepared)
                if record:
                    stored = InferenceOutput(
                        id=new_id(), inference_run_id=run_id, attempt=attempts,
                        raw_output=answer.content[: tasks.MAX_RAW_OUTPUT],
                        validated_output_json=(
                            checked.output.model_dump(mode="json") if checked.output else None
                        ),
                        validation_errors_json=(
                            None if checked.output else {"errors": list(checked.errors)}
                        ),
                        created_at=utc_now(),
                    )  # fmt: skip

                    def keep(conn: sqlite3.Connection, found: InferenceOutput = stored) -> None:
                        store.add_output(conn, found)

                    await self._store(keep)
                if checked.output is not None:
                    output = checked.output
                    status = (
                        InferenceStatus.INSUFFICIENT_EVIDENCE
                        if getattr(output, "outcome", None) is AiOutcome.INSUFFICIENT_EVIDENCE
                        else InferenceStatus.SUCCEEDED
                    )
                    break
                if repairs >= tasks.MAX_REPAIRS:
                    status = InferenceStatus.INVALID_OUTPUT
                    error_code = "invalid_output"
                    error_message = "; ".join(checked.errors)[:2000]
                    break
                repairs += 1
                messages += [
                    ChatMessage("assistant", answer.content[:4000]),
                    ChatMessage("user", tasks.repair_message(checked.errors)),
                ]
            device = await self._device(assignment)
        except _Stop:
            status = InferenceStatus.FAILED
        except asyncio.CancelledError:
            self.breaker.release()
            if record:
                await asyncio.shield(
                    self._finish(
                        run_id, InferenceStatus.CANCELLED, attempts=attempts, error_code="cancelled"
                    )
                )
            raise
        except ProviderError as exc:  # show() or placement failed before any answer
            if exc.counts_against_runtime:
                self.breaker.failure(str(exc))
            status, error_code, error_message = InferenceStatus.FAILED, exc.code, str(exc)
        duration = round((time.perf_counter() - started) * 1000)
        if status in store.VALID:
            self.counters.completed += 1
            self.counters.latencies.append(duration)
        else:
            self.counters.failed += 1
        if options.get("num_gpu") == 0:
            device = AiDevice.CPU
        if record:
            final = {**parameters, "num_ctx_used": num_ctx, "degraded": degraded,
                     "num_gpu": options.get("num_gpu")}  # fmt: skip
            await self._finish(
                run_id, status, device=device, attempts=attempts,
                prompt_tokens=tokens_in or None, completion_tokens=tokens_out or None,
                duration_ms=duration, error_code=error_code, error_message=error_message,
                parameters=final,
            )  # fmt: skip
        return Outcome(
            run_id if record else None,
            status,
            output.model_dump(mode="json") if output is not None else None,
            duration_ms=duration,
            completion_tokens=tokens_out or None,
            eval_ms=eval_ms or None,
            device=device,
            error_code=error_code,
        )

    async def _placement(
        self, assignment: Assignment, details: ModelDetails, num_ctx: int
    ) -> AiVramEstimate:
        """Where the model would run. Other loaded models stay only if this one fits next to them:
        two large models never share 8 GB."""
        loaded = {model.name: model for model in await self.provider.loaded()}
        shape = vram.shape_from_info(assignment.size_bytes or 0, details.model_info)
        if assignment.model in loaded:
            return vram.estimate(shape, num_ctx, None).model_copy(
                update={"device": AiDevice.GPU, "fitted_ctx": None, "note": "already loaded"}
            )
        sample = await asyncio.to_thread(self._monitor.sample)
        placement = vram.estimate(shape, num_ctx, sample.vram_free_mib)
        others = [model for name, model in loaded.items() if name != assignment.model]
        if (placement.device is AiDevice.GPU and placement.fitted_ctx is None) or not others:
            return placement
        for other in others:
            await self.provider.unload(other.name)
            logger.info("unloaded a model to make room", extra={"model": other.name})
        freed = sum((other.size_vram or 0) // vram.MIB for other in others)
        free = None if sample.vram_free_mib is None else sample.vram_free_mib + freed
        return vram.estimate(shape, num_ctx, free)

    async def _device(self, assignment: Assignment) -> AiDevice:
        try:
            loaded = {model.name: model for model in await self.provider.loaded()}
        except ProviderError:
            return AiDevice.UNKNOWN
        found = loaded.get(assignment.model)
        return vram.device_from_runtime(found.size, found.size_vram) if found else AiDevice.UNKNOWN


class _Stop(Exception):
    """Ends an inference early with the error already set."""


def _current_task_cancelling() -> bool:
    task = asyncio.current_task()
    return task is not None and task.cancelling() > 0
