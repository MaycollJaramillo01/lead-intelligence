"""The local AI service of the worker (phase 10): status, settings, analysis jobs, benchmarks and
model downloads, over one GPU queue.

    ai.start ─ one job per project (jobs.BackgroundJobs): for each canonical business and task, on a
      thread: input from the store ─ nothing to read? skipped ─ runner on the event loop through
      the queue ─ stats, checkpoint every 10, one aggregated event every 25 ─ Latino declarations
      an extraction proposes go to the Latino rules, which alone decide whether they are evidence

The model never orchestrates anything: jobs decide what is asked, the deterministic engines keep
deciding facts. Without a runtime every task reports why it is unavailable and a job counts what it
skipped; a runtime that keeps failing opens the breaker and pauses the job instead of looping.
"""

import asyncio
import contextlib
import logging
import sqlite3
from collections.abc import Callable, Sequence
from datetime import timedelta
from pathlib import Path
from typing import Final

from pydantic import JsonValue

from research_engine import jobs, repository
from research_engine.ai import benchmark, inputs, llmfit, store, tasks, vram
from research_engine.ai.resources import ResourceMonitor, take_sample
from research_engine.ai.runner import Assignment, Outcome, Runner
from research_engine.ai.scheduler import CircuitBreaker, GpuQueue, Priority, QueueFullError
from research_engine.contracts import OllamaModel, OllamaStatus
from research_engine.domain import (
    AiActivity,
    AiBenchmarkAction,
    AiBenchmarkProgress,
    AiBenchmarkRequest,
    AiBenchmarkResult,
    AiCircuitState,
    AiConfigureRequest,
    AiDevice,
    AiModelChange,
    AiModelChangeAction,
    AiModelChangeRequest,
    AiModelInfo,
    AiProfile,
    AiProfileSetting,
    AiRecommendation,
    AiResourceLimits,
    AiResources,
    AiRun,
    AiRunPage,
    AiSchedulerState,
    AiStartRequest,
    AiStats,
    AiStatus,
    AiStatusQuery,
    AiTaskInfo,
    AiTaskSetting,
    AiTaskType,
    AiUnavailableReason,
    DiscoveryControlRequest,
    EventLevel,
    ExtractedClaimKind,
    InferenceStatus,
    Job,
    JobStatus,
    LlmFitReport,
    RunControlAction,
    new_id,
    utc_now,
)
from research_engine.gpu import probe_gpu
from research_engine.latino import store as latino_store
from research_engine.latino.store import LatinoError, ModelCandidate
from research_engine.ollama import Provider, ProviderError

logger = logging.getLogger(__name__)

JOB_KIND: Final = store.JOB_KIND
ACTOR: Final = store.ACTOR
CHECKPOINT_EVERY: Final = 10
EVENT_EVERY: Final = 25
BENCHMARK_OWNER: Final = "benchmark"
BENCHMARK_CTX: Final = 4096
#: A benchmarked model is recommended only if at least this share of its answers validated.
MIN_VALID_SHARE: Final = 0.8
_LATINO_KINDS: Final = frozenset({
    ExtractedClaimKind.OWNERSHIP_STATEMENT, ExtractedClaimKind.OWNERSHIP_MENTION,
    ExtractedClaimKind.CERTIFICATION_STATEMENT, ExtractedClaimKind.OWNERSHIP_DENIED,
})  # fmt: skip
_STATUS_STATS: Final = {
    InferenceStatus.SUCCEEDED: "succeeded",
    InferenceStatus.INSUFFICIENT_EVIDENCE: "insufficient_evidence",
    InferenceStatus.INVALID_OUTPUT: "invalid_output",
    InferenceStatus.FAILED: "failed",
    InferenceStatus.CANCELLED: "cancelled",
}


class AiService(jobs.BackgroundJobs[AiRun, AiRunPage]):
    kind = JOB_KIND
    actor = ACTOR

    def __init__(
        self,
        data_dir: Path | None,
        connect: Callable[[], sqlite3.Connection],
        provider: Provider,
        monitor: ResourceMonitor,
        *,
        downloads_allowed: bool,
        llm_fit: Callable[[], LlmFitReport] = llmfit.report,
    ) -> None:
        super().__init__(connect)
        self._data_dir = data_dir
        self.provider = provider
        self.monitor = monitor
        self.queue = GpuQueue()
        self.breaker = CircuitBreaker()
        self.runner = Runner(provider, self.queue, self.breaker, connect, monitor)
        self.downloads_allowed = downloads_allowed
        self._llm_fit = llm_fit
        monitor.busy = lambda: self.queue.busy
        self._loop: asyncio.AbstractEventLoop | None = None
        self._benchmark = AiBenchmarkProgress(
            state=AiActivity.IDLE, models=[], task_types=[], done=0, total=0, current=None,
            error=None, started_at=None, finished_at=None,
        )  # fmt: skip
        self._benchmark_task: asyncio.Task[None] | None = None
        self._change: AiModelChange | None = None
        self._change_task: asyncio.Task[None] | None = None

    # ---- models and assignments ---------------------------------------------------------------

    async def _catalog(self) -> tuple[OllamaStatus, dict[str, OllamaModel]]:
        runtime = await self.provider.probe()
        return runtime, {model.name: model for model in runtime.models}

    async def resolve(
        self,
        task: AiTaskType,
        settings: AiTaskSetting,
        profiles: Sequence[AiProfileSetting],
        runtime: OllamaStatus,
        catalog: dict[str, OllamaModel],
    ) -> Assignment | AiUnavailableReason:
        """The model a task would run on now, or the first reason it cannot run."""
        if not settings.enabled:
            return AiUnavailableReason.TASK_DISABLED
        profile = next(p for p in profiles if p.profile is settings.profile)
        if profile.model is None:
            return AiUnavailableReason.NO_MODEL_ASSIGNED
        if not runtime.running:
            return AiUnavailableReason.RUNTIME_UNAVAILABLE
        installed = catalog.get(profile.model)
        if installed is None:
            return AiUnavailableReason.MODEL_NOT_INSTALLED
        try:
            details = await self.runner.model_details(installed.name, installed.digest)
        except ProviderError:
            return AiUnavailableReason.RUNTIME_UNAVAILABLE
        if tasks.TASKS[task].requires_vision and "vision" not in details.capabilities:
            return AiUnavailableReason.NO_VISION_MODEL
        if self.breaker.state is AiCircuitState.OPEN:
            return AiUnavailableReason.CIRCUIT_OPEN
        return Assignment(
            profile=profile.profile, model=installed.name, digest=installed.digest,
            num_ctx=profile.num_ctx, allow_cpu=profile.allow_cpu,
            capabilities=details.capabilities, size_bytes=installed.size_bytes,
        )  # fmt: skip

    async def _assignments(
        self, wanted: Sequence[AiTaskType]
    ) -> dict[AiTaskType, Assignment | AiUnavailableReason]:
        settings, profiles = await self._in_store(
            lambda conn: (store.task_settings(conn), store.profiles(conn))
        )
        runtime, catalog = await self._catalog()
        return {
            task: await self.resolve(task, settings[task], profiles, runtime, catalog)
            for task in wanted
        }

    # ---- status and settings ------------------------------------------------------------------

    async def status(self, query: AiStatusQuery) -> AiStatus:
        runtime, catalog = await self._catalog()
        gpu, sample = await asyncio.gather(
            asyncio.to_thread(probe_gpu), asyncio.to_thread(self.monitor.sample)
        )

        def read(
            conn: sqlite3.Connection,
        ) -> tuple[
            dict[AiTaskType, AiTaskSetting],
            list[AiProfileSetting],
            AiResourceLimits,
            list[AiBenchmarkResult],
        ]:
            return (
                store.task_settings(conn),
                store.profiles(conn),
                store.limits(conn),
                store.latest_benchmarks(conn),
            )

        settings, profiles, limits, results = await self._in_store(read)
        self.monitor.min_free_ram_mib = limits.min_free_ram_mib
        self.monitor.min_free_vram_mib = limits.min_free_vram_mib
        loaded = {}
        if runtime.running:
            with contextlib.suppress(ProviderError):
                loaded = {model.name: model for model in await self.provider.loaded()}
        models: list[AiModelInfo] = []
        for model in catalog.values():
            capabilities: tuple[str, ...] = ()
            estimate = None
            context_length = None
            try:
                details = await self.runner.model_details(model.name, model.digest)
            except ProviderError:
                details = None
            if details is not None:
                capabilities = details.capabilities
                shape = vram.shape_from_info(model.size_bytes or 0, details.model_info)
                context_length = shape.context_length
                wanted_ctx = max(
                    [p.num_ctx for p in profiles if p.model == model.name] or [BENCHMARK_CTX]
                )
                if "embedding" not in capabilities:
                    free = sample.vram_free_mib
                    if free is not None and model.name not in loaded:
                        free += sum((m.size_vram or 0) // vram.MIB for m in loaded.values())
                    estimate = vram.estimate(shape, wanted_ctx, free)
            running = loaded.get(model.name)
            models.append(
                AiModelInfo(
                    name=model.name, digest=model.digest, size_bytes=model.size_bytes,
                    family=model.family, parameter_size=model.parameter_size,
                    quantization=model.quantization, context_length=context_length,
                    capabilities=[c for c in capabilities if c.replace("_", "").isalnum()],
                    loaded=running is not None,
                    size_vram_bytes=running.size_vram if running else None, estimate=estimate,
                )
            )  # fmt: skip
        task_infos = []
        for task, spec in tasks.TASKS.items():
            setting = settings[task]
            resolved = await self.resolve(task, setting, profiles, runtime, catalog)
            profile = next(p for p in profiles if p.profile is setting.profile)
            prompt = tasks.template(task)
            task_infos.append(
                AiTaskInfo(
                    task_type=task, task_version=spec.version,
                    output_schema_version=spec.schema_version, prompt_template_id=prompt.id,
                    prompt_template_version=prompt.version, enabled=setting.enabled,
                    profile=setting.profile, model=profile.model,
                    available=isinstance(resolved, Assignment),
                    unavailable_reason=None if isinstance(resolved, Assignment) else resolved,
                    requires_vision=spec.requires_vision, batchable=spec.batchable,
                    max_input_chars=spec.max_input_chars, max_refs=spec.max_refs,
                    timeout_s=spec.timeout_s, temperature=spec.temperature,
                )
            )  # fmt: skip
        pressure = self.monitor.pressure(sample)
        open_for = self.breaker.seconds_open()
        return AiStatus(
            runtime=runtime,
            gpu=gpu,
            models=models,
            profiles=profiles,
            tasks=task_infos,
            limits=limits,
            resources=AiResources(
                ram_total_mib=sample.ram_total_mib,
                ram_free_mib=sample.ram_free_mib,
                vram_total_mib=sample.vram_total_mib,
                vram_free_mib=sample.vram_free_mib,
                pressure=pressure,
                crawl_waiting=self.queue.busy and bool(pressure),
                sampled_at=sample.sampled_at,
            ),
            scheduler=AiSchedulerState(
                queued=self.queue.queued,
                running=1 if self.queue.running_group() else 0,
                capacity=self.queue.capacity,
                circuit=self.breaker.state,
                consecutive_failures=self.breaker.failures,
                open_until=utc_now() + timedelta(seconds=open_for)
                if open_for is not None
                else None,
                last_error=self.breaker.last_error,
                completed=self.runner.counters.completed,
                failed=self.runner.counters.failed,
                cache_hits=self.runner.counters.cache_hits,
                latency_p50_ms=self.runner.counters.latency_p50(),
            ),
            recommendations=recommend(results, catalog),
            benchmark=self._benchmark,
            benchmark_results=results,
            model_change=self._change,
            llm_fit=await asyncio.to_thread(self._llm_fit) if query.include_llm_fit else None,
            downloads_allowed=self.downloads_allowed,
            checked_at=utc_now(),
        )

    async def configure(self, request: AiConfigureRequest) -> AiStatus:
        chosen = {change.model for change in request.profiles if change.model is not None}
        if chosen:
            runtime, catalog = await self._catalog()
            missing = sorted(chosen - set(catalog))
            if missing:
                raise store.AiError(
                    f"not installed in the runtime: {', '.join(missing)}"
                    if runtime.running
                    else "the runtime is not running: models cannot be checked or assigned"
                )
        await self._in_store(lambda conn: store.configure(conn, request))
        return await self.status(AiStatusQuery(include_llm_fit=False))

    # ---- jobs ---------------------------------------------------------------------------------

    def _spawn(self, job_id: str) -> None:
        self._loop = asyncio.get_running_loop()
        super()._spawn(job_id)

    async def start(self, request: AiStartRequest) -> AiRunPage:
        created, listed = await self._in_store(
            lambda conn: store.plan_runs(conn, request, actor=ACTOR)
        )
        for job_id in created:
            self._spawn(job_id)
        return await self._in_store(lambda conn: self.page(conn, [*created, *listed]))

    async def control(self, request: DiscoveryControlRequest) -> AiRun:
        def apply(conn: sqlite3.Connection) -> None:
            jobs.live_job(conn, request.job_id, self.kind)
            jobs.apply_control(conn, request.job_id, request.action, self.actor)

        await self._in_store(apply)
        if request.action is RunControlAction.RESUME:
            self._spawn(request.job_id)
        else:
            # written first: the job's thread then sees it is no longer running and stops
            self.queue.cancel_owner(request.job_id)
            await self._halt(request.job_id)
        return await self._in_store(lambda conn: self.view(conn, request.job_id))

    async def recover(self) -> int:
        cancelled, failed = await self._in_store(store.recover)
        if cancelled or failed:
            logger.info(
                "interrupted inferences closed", extra={"queued": cancelled, "running": failed}
            )
        return await super().recover()

    async def shutdown(self) -> None:
        self.queue.cancel_all()
        for task in (self._benchmark_task, self._change_task):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        await super().shutdown()
        await self.queue.close()

    def plan(self, conn: sqlite3.Connection, project_id: str | None) -> tuple[list[str], list[str]]:
        return store.plan_runs(
            conn,
            AiStartRequest(project_id=project_id, business_ids=[], task_types=list(AiTaskType)),
            actor=ACTOR,
        )

    def view(self, conn: sqlite3.Connection, job_id: str) -> AiRun:
        return store.get_run(conn, job_id)

    def page(self, conn: sqlite3.Connection, job_ids: Sequence[str]) -> AiRunPage:
        return store.runs_by_id(conn, job_ids)

    def run(self, conn: sqlite3.Connection, job_id: str, should_stop: Callable[[], bool]) -> bool:
        loop = self._loop
        job = repository.get(conn, Job, job_id)
        if loop is None or job.status not in (JobStatus.QUEUED, JobStatus.RUNNING):
            return False
        jobs.mark_running(conn, job_id, ACTOR)
        state = jobs.latest_state(conn, job_id)
        saved = state.get("stats")
        stats = {name: 0 for name in AiStats.model_fields}
        if isinstance(saved, dict):
            stats |= {k: v for k, v in saved.items() if k in stats and isinstance(v, int)}
        cursor = state.get("cursor")
        wanted = store.job_tasks(job)
        targets = store.job_targets(conn, job)
        units = [(root, task) for root in targets for task in wanted]
        start_at = cursor if isinstance(cursor, int) and 0 <= cursor <= len(units) else 0
        stats["targets"] = len(units)
        pages = inputs.project_pages(conn, job.project_id)
        assignments = asyncio.run_coroutine_threadsafe(self._assignments(wanted), loop).result()
        priority = Priority.INTERACTIVE if job.priority == 0 else Priority.JOB

        def save(done: int) -> None:
            with repository.atomic(conn):
                jobs.set_progress(conn, job_id, done, len(units))
                jobs.checkpoint(conn, job_id, {"stage": "infer", "cursor": done,
                                               "stats": dict[str, JsonValue](stats)})  # fmt: skip

        save(start_at)
        with repository.atomic(conn):
            jobs.write_event(conn, job_id, None, "stage_started", EventLevel.INFO, {
                "stage": "infer", "units": len(units), "resume_at": start_at,
                "unavailable": {t.value: r.value for t, r in assignments.items()
                                if isinstance(r, AiUnavailableReason)},
            })  # fmt: skip
        for index in range(start_at, len(units)):
            if should_stop():
                save(index)
                return False
            root, task = units[index]
            resolved = assignments[task]
            if isinstance(resolved, AiUnavailableReason):
                stats["skipped_unavailable"] += 1
            elif (prepared := inputs.prepare(conn, self._data_dir, root, task, pages)) is None:
                stats["skipped_no_input"] += 1
            elif should_stop():
                save(index)
                return False
            else:
                try:
                    outcome = asyncio.run_coroutine_threadsafe(
                        self.runner.infer(
                            prepared, resolved, project_id=job.project_id, business_id=root,
                            job_id=job_id, priority=priority, owner=job_id,
                        ),
                        loop,
                    ).result()  # fmt: skip
                except QueueFullError:
                    save(index)
                    jobs.pause_job(conn, job_id, "queue_full", ACTOR)
                    return False
                if outcome.skipped is AiUnavailableReason.CIRCUIT_OPEN:
                    save(index)
                    jobs.pause_job(conn, job_id, "runtime_unavailable", ACTOR)
                    return False
                if outcome.status is InferenceStatus.CANCELLED:
                    save(index)
                    return False
                self._count(stats, outcome)
                if task is AiTaskType.EVIDENCE_EXTRACTION:
                    self._propose_latino(conn, root, prepared, outcome, stats)
            stats["done"] = index + 1
            if (index + 1) % CHECKPOINT_EVERY == 0 or index + 1 == len(units):
                save(index + 1)
            if (index + 1) % EVENT_EVERY == 0:
                with repository.atomic(conn):
                    jobs.write_event(conn, job_id, None, "batch_finished", EventLevel.INFO,
                                     dict[str, JsonValue](stats))  # fmt: skip
        if repository.get(conn, Job, job_id).status is not JobStatus.RUNNING:
            return False
        jobs.finish_job(conn, job_id, {"stage": "done", "cursor": len(units),
                                       "stats": dict[str, JsonValue](stats)}, ACTOR)  # fmt: skip
        return True

    @staticmethod
    def _count(stats: dict[str, int], outcome: Outcome) -> None:
        if outcome.cached:
            stats["cache_hits"] += 1
        if outcome.status is not None:
            stats[_STATUS_STATS[outcome.status]] += 1

    @staticmethod
    def _propose_latino(
        conn: sqlite3.Connection,
        root: str,
        prepared: tasks.PreparedInput,
        outcome: Outcome,
        stats: dict[str, int],
    ) -> None:
        """Hands the Latino declarations of a valid extraction to the Latino rules, which accept a
        candidate only when its quote is in the page and reads as that claim."""
        if outcome.run_id is None or outcome.output is None:
            return
        claims = outcome.output.get("claims")
        refs = {ref.ref: ref for ref in prepared.refs}
        for claim in claims if isinstance(claims, list) else []:
            if not isinstance(claim, dict) or claim.get("kind") not in _LATINO_KINDS:
                continue
            quote = str(claim.get("quote") or "")
            listed = claim.get("evidence_ids")
            cited = [
                refs[str(ref)]
                for ref in (listed if isinstance(listed, list) else [])
                if str(ref) in refs
            ]
            page = next(
                (ref for ref in cited if " ".join(quote.split()).casefold() in ref.text.casefold()),
                None,
            )
            if page is None or page.entity_id is None:
                stats["latino_candidates_rejected"] += 1
                continue
            try:
                found = latino_store.ingest_model_candidates(
                    conn, inference_run_id=outcome.run_id, raw_record_id=page.entity_id,
                    business_id=root, candidates=[ModelCandidate(str(claim["kind"]), quote)],
                )  # fmt: skip
            except (LatinoError, repository.NotFoundError):
                stats["latino_candidates_rejected"] += 1
                continue
            stats["latino_candidates_accepted"] += len(found.accepted)
            stats["latino_candidates_rejected"] += len(found.rejected)

    # ---- benchmarks ---------------------------------------------------------------------------

    async def benchmark(self, request: AiBenchmarkRequest) -> AiStatus:
        if request.action is AiBenchmarkAction.CANCEL:
            self.queue.cancel_owner(BENCHMARK_OWNER)
            if self._benchmark_task is not None and not self._benchmark_task.done():
                self._benchmark_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._benchmark_task
            return await self.status(AiStatusQuery(include_llm_fit=False))
        if self._benchmark_task is not None and not self._benchmark_task.done():
            raise store.AiError("a benchmark is already running: wait for it or cancel it")
        if not request.models:
            raise store.AiError("choose at least one installed model to benchmark")
        runtime, catalog = await self._catalog()
        if not runtime.running:
            raise store.AiError("the runtime is not running: start Ollama first")
        missing = sorted(set(request.models) - set(catalog))
        if missing:
            raise store.AiError(f"not installed in the runtime: {', '.join(missing)}")
        wanted = [
            t for t in (request.task_types or benchmark.BENCHMARKED) if t in benchmark.BENCHMARKED
        ]
        if not wanted:
            raise store.AiError("visual review has no labelled corpus to benchmark")
        models = [catalog[name] for name in dict.fromkeys(request.models)]
        total = sum(len(benchmark.cases_for(task)) for task in wanted) * len(models)
        self._benchmark = AiBenchmarkProgress(
            state=AiActivity.RUNNING, models=[m.name for m in models], task_types=wanted, done=0,
            total=total, current=None, error=None, started_at=utc_now(), finished_at=None,
        )  # fmt: skip
        self._benchmark_task = asyncio.create_task(self._run_benchmark(models, wanted))
        return await self.status(AiStatusQuery(include_llm_fit=False))

    async def _run_benchmark(
        self, models: Sequence[OllamaModel], wanted: Sequence[AiTaskType]
    ) -> None:
        done = 0
        try:
            for model in models:
                details = await self.runner.model_details(model.name, model.digest)
                assignment = Assignment(
                    profile=AiProfile.BALANCED, model=model.name, digest=model.digest,
                    num_ctx=BENCHMARK_CTX, allow_cpu=True, capabilities=details.capabilities,
                    size_bytes=model.size_bytes,
                )  # fmt: skip
                for task in wanted:
                    started = utc_now()
                    latencies: list[int] = []
                    tokens = 0
                    seconds = 0.0
                    valid = correct = failures = 0
                    peak_vram: int | None = None
                    min_ram: int | None = None
                    devices: set[AiDevice] = set()
                    cases = benchmark.cases_for(task)
                    for case in cases:
                        self._benchmark = self._benchmark.model_copy(
                            update={
                                "current": f"{model.name} · {task.value} · {case.name}",
                                "done": done,
                            }
                        )
                        outcome = await self.runner.infer(
                            case.prepared(), assignment, project_id=None, business_id=None,
                            job_id=None, priority=Priority.BENCHMARK, owner=BENCHMARK_OWNER,
                            record=False,
                        )  # fmt: skip
                        done += 1
                        sample = await asyncio.to_thread(take_sample)
                        if sample.vram_total_mib is not None and sample.vram_free_mib is not None:
                            used = sample.vram_total_mib - sample.vram_free_mib
                            peak_vram = used if peak_vram is None else max(peak_vram, used)
                        if sample.ram_free_mib is not None:
                            min_ram = (
                                sample.ram_free_mib
                                if min_ram is None
                                else min(min_ram, sample.ram_free_mib)
                            )
                        if outcome.skipped is not None:
                            raise store.AiError(
                                f"the runtime became unavailable: {outcome.skipped.value}"
                            )
                        devices.add(outcome.device)
                        if outcome.duration_ms is not None:
                            latencies.append(outcome.duration_ms)
                        if outcome.output is not None:
                            valid += 1
                            correct += int(case.score(outcome.output))
                        else:
                            failures += int(outcome.status is not InferenceStatus.INVALID_OUTPUT)
                        tokens += outcome.completion_tokens or 0
                        seconds += (outcome.eval_ms or 0) / 1000
                    result = AiBenchmarkResult(
                        id=new_id(), provider=self.provider.name, model=model.name,
                        model_digest=model.digest, task_type=task,
                        task_version=tasks.TASKS[task].version,
                        corpus_version=benchmark.CORPUS_VERSION, num_ctx=BENCHMARK_CTX,
                        cases=len(cases), valid_outputs=valid, correct=correct,
                        failures=failures, latency_p50_ms=benchmark.percentile(latencies, 0.5),
                        latency_p95_ms=benchmark.percentile(latencies, 0.95),
                        tokens_per_second=round(tokens / seconds, 1) if seconds > 0 else None,
                        peak_vram_used_mib=peak_vram, min_free_ram_mib=min_ram,
                        device=_worst(devices), started_at=started, finished_at=utc_now(),
                        created_at=utc_now(),
                    )  # fmt: skip

                    def keep(conn: sqlite3.Connection, found: AiBenchmarkResult = result) -> None:
                        store.add_benchmark(conn, found)

                    await self._in_store(keep)
            self._benchmark = self._benchmark.model_copy(
                update={"state": AiActivity.SUCCEEDED, "done": done, "current": None,
                        "finished_at": utc_now()}
            )  # fmt: skip
        except asyncio.CancelledError:
            self._benchmark = self._benchmark.model_copy(
                update={"state": AiActivity.CANCELLED, "current": None, "finished_at": utc_now()}
            )
            raise
        except (store.AiError, ProviderError, QueueFullError) as exc:
            self._benchmark = self._benchmark.model_copy(
                update={"state": AiActivity.FAILED, "error": str(exc)[:500], "current": None,
                        "finished_at": utc_now()}
            )  # fmt: skip
        except Exception:
            logger.exception("benchmark failed")
            self._benchmark = self._benchmark.model_copy(
                update={"state": AiActivity.FAILED, "error": "internal error; see the worker log",
                        "current": None, "finished_at": utc_now()}
            )  # fmt: skip

    # ---- model downloads and deletions --------------------------------------------------------

    async def change_model(self, request: AiModelChangeRequest) -> AiStatus:
        if request.confirmation != request.model:
            raise store.AiError("to confirm, type the exact model name")
        if self._change_task is not None and not self._change_task.done():
            raise store.AiError("another download is still running")
        runtime, catalog = await self._catalog()
        if not runtime.running:
            raise store.AiError("the runtime is not running: start Ollama first")
        if request.action is AiModelChangeAction.DELETE:
            if request.model not in catalog:
                raise store.AiError(f"{request.model} is not installed")
            if self.queue.running_group() == request.model or (
                self._benchmark_task is not None and not self._benchmark_task.done()
            ):
                raise store.AiError(f"{request.model} is in use: wait until its work ends")
            await self.provider.delete(request.model)
            now = utc_now()
            self._change = AiModelChange(
                action=request.action, model=request.model, state=AiActivity.SUCCEEDED,
                completed_bytes=None, total_bytes=None, detail="deleted", started_at=now,
                finished_at=now,
            )  # fmt: skip
        else:
            if not self.downloads_allowed:
                raise store.AiError("downloads are off while the worker is offline")
            self._change = AiModelChange(
                action=request.action, model=request.model, state=AiActivity.RUNNING,
                completed_bytes=None, total_bytes=None, detail="starting", started_at=utc_now(),
                finished_at=None,
            )  # fmt: skip
            self._change_task = asyncio.create_task(self._pull(request.model))
        return await self.status(AiStatusQuery(include_llm_fit=False))

    async def _pull(self, model: str) -> None:
        def progress(detail: str, completed: int | None, total: int | None) -> None:
            if self._change is not None:
                self._change = self._change.model_copy(
                    update={"detail": detail or None, "completed_bytes": completed,
                            "total_bytes": total}
                )  # fmt: skip

        try:
            await self.provider.pull(model, progress)
            state, detail = AiActivity.SUCCEEDED, "installed"
        except asyncio.CancelledError:
            state, detail = AiActivity.CANCELLED, "cancelled"
            raise
        except ProviderError as exc:
            state, detail = AiActivity.FAILED, str(exc)[:300]
        finally:
            if self._change is not None:
                self._change = self._change.model_copy(
                    update={"state": state, "detail": detail, "finished_at": utc_now()}
                )


def _worst(devices: set[AiDevice]) -> AiDevice:
    for device in (AiDevice.CPU, AiDevice.PARTIAL, AiDevice.GPU):
        if device in devices:
            return device
    return AiDevice.UNKNOWN


def recommend(
    results: Sequence[AiBenchmarkResult], catalog: dict[str, OllamaModel]
) -> list[AiRecommendation]:
    """Per task: the most accurate installed model whose answers mostly validated, then the fastest.
    A recommendation only; nothing is assigned."""
    best: dict[AiTaskType, AiBenchmarkResult] = {}
    for result in results:
        installed = catalog.get(result.model)
        if installed is None or (result.model_digest and installed.digest != result.model_digest):
            continue
        if result.valid_outputs < MIN_VALID_SHARE * result.cases:
            continue
        current = best.get(result.task_type)
        key = (-result.correct / result.cases, result.latency_p50_ms or 10**9)
        if current is None or key < (
            -current.correct / current.cases,
            current.latency_p50_ms or 10**9,
        ):
            best[result.task_type] = result
    return [
        AiRecommendation(
            task_type=task,
            model=result.model,
            accuracy=round(result.correct / result.cases, 3),
            latency_p50_ms=result.latency_p50_ms,
            benchmark_id=result.id,
            benchmarked_at=result.created_at,
        )
        for task, result in sorted(best.items(), key=lambda item: list(AiTaskType).index(item[0]))
    ]
