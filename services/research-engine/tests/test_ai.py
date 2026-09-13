"""The local AI layer against the store with a scripted runtime (synthetic test data): what is
stored per inference, repair, failures and the breaker, out-of-memory degradation, cancellation,
recovery, the queue, memory pressure, reviews, jobs, benchmarks and model changes. The real runtime
is exercised in test_ai_live.py."""

import asyncio
import io
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import orjson
import pytest

from research_engine import jobs, repository
from research_engine.ai import engine as ai_engine
from research_engine.ai import store, tasks
from research_engine.ai.engine import AiService, recommend
from research_engine.ai.resources import ResourceMonitor, Sample
from research_engine.ai.runner import Assignment, Runner
from research_engine.ai.scheduler import CircuitBreaker, GpuQueue, Priority, QueueFullError
from research_engine.ai.tasks import PreparedInput, bounded_refs
from research_engine.contracts import ComponentState, GpuStatus
from research_engine.domain import (
    AiBenchmarkAction,
    AiBenchmarkRequest,
    AiCircuitState,
    AiConfigureRequest,
    AiDevice,
    AiInferenceListQuery,
    AiInputKind,
    AiModelChangeAction,
    AiModelChangeRequest,
    AiProfile,
    AiProfileChange,
    AiReviewDecision,
    AiReviewRequest,
    AiReviewStatus,
    AiRunListQuery,
    AiStartRequest,
    AiStatusQuery,
    AiTaskChange,
    AiTaskType,
    AiUnavailableReason,
    DiscoveryControlRequest,
    InferenceStatus,
    JobStatus,
    LlmFitReport,
    RunControlAction,
    utc_now,
)
from research_engine.ollama import ChatRequest, OllamaProvider, ProviderError
from research_engine.settings import Settings
from research_engine.testdata import (
    ScriptedProvider,
    SyntheticContext,
    seed_latino,
    synthetic_answer,
)
from research_engine.worker import Worker

TEXT_MODEL = "synthetic-text:1b"
ROOFING = orjson.dumps({
    "outcome": "ok", "vertical": "roofing", "vertical_evidence_ids": ["F1"], "services": [],
    "signals": [],
}).decode()  # fmt: skip


def _sample(vram_free: int | None = 7000, ram_free: int | None = 8000) -> Sample:
    return Sample(16_000, ram_free, 8192 if vram_free is not None else None, vram_free, utc_now())


def _monitor(**sample: int | None) -> ResourceMonitor:
    return ResourceMonitor(sampler=lambda: _sample(**sample))


def _prepared(text: str = "We repair roofs and gutters.") -> PreparedInput:
    spec = tasks.TASKS[AiTaskType.BUSINESS_CLASSIFICATION]
    refs, warnings = bounded_refs(spec, "F", [(AiInputKind.PAGE_FRAGMENT, None, "test", text)])
    return PreparedInput(AiTaskType.BUSINESS_CLASSIFICATION, {"business_name": "Synthetic"}, refs,
                         (), warnings)  # fmt: skip


def _assignment(*, allow_cpu: bool = True, num_ctx: int = 4096) -> Assignment:
    return Assignment(AiProfile.FAST, TEXT_MODEL, "sha256:synthetic", num_ctx, allow_cpu,
                      ("completion",), 1024**3)  # fmt: skip


def _runner(
    data_dir: Path,
    provider: ScriptedProvider,
    *,
    breaker: CircuitBreaker | None = None,
    **sample: int | None,
) -> Runner:
    return Runner(provider, GpuQueue(), breaker or CircuitBreaker(),
                  lambda: repository.connect(data_dir), _monitor(**sample))  # fmt: skip


async def _infer(runner: Runner, prepared: PreparedInput | None = None, **options: Any) -> Any:
    return await runner.infer(
        prepared or _prepared(), options.pop("assignment", _assignment()), project_id=None,
        business_id=None, job_id=None, priority=options.pop("priority", Priority.JOB),
        owner=options.pop("owner", "test"), **options,
    )  # fmt: skip


async def _until(condition: Callable[[], bool]) -> None:
    async with asyncio.timeout(5):
        while not condition():  # noqa: ASYNC110 - polls state the scripted runtime changes
            await asyncio.sleep(0.01)


def _runs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM ai_inference_runs ORDER BY created_at, id").fetchall()


def _outputs(conn: sqlite3.Connection, run_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM ai_inference_outputs WHERE inference_run_id = ? ORDER BY attempt", (run_id,)
    ).fetchall()


# ---- one inference ------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_valid_answer_is_stored_raw_and_validated_then_reused(
    data_dir: Path, conn: sqlite3.Connection
) -> None:
    provider = ScriptedProvider([ROOFING])
    runner = _runner(data_dir, provider)
    outcome = await _infer(runner)
    assert (outcome.status, outcome.device, outcome.cached) == (
        InferenceStatus.SUCCEEDED,
        AiDevice.GPU,
        False,
    )
    (run,) = _runs(conn)
    assert (run["status"], run["attempts"], run["prompt_tokens"], run["completion_tokens"]) == (
        "succeeded", 1, 100, 50,
    )  # fmt: skip
    assert run["model"] == TEXT_MODEL
    assert run["prompt_template_version"] == "1.0.0"
    parameters = orjson.loads(run["parameters_json"])
    assert parameters["temperature"] == 0.0
    assert parameters["seed"] == tasks.SEED
    refs = orjson.loads(run["input_json"])["refs"]
    assert refs[0]["ref"] == "F1"
    assert refs[0]["text"] == "We repair roofs and gutters."
    (output,) = _outputs(conn, run["id"])
    assert output["raw_output"] == ROOFING
    assert orjson.loads(output["validated_output_json"])["vertical"] == "roofing"
    request = provider.calls[0]
    assert request.options["temperature"] == 0.0
    assert request.schema == tasks.model_schema(AiTaskType.BUSINESS_CLASSIFICATION)
    again = await _infer(runner)
    assert (again.cached, again.run_id, len(provider.calls)) == (True, run["id"], 1)
    assert runner.counters.cache_hits == 1


@pytest.mark.anyio
async def test_invalid_answers_get_one_repair_and_never_loop(
    data_dir: Path, conn: sqlite3.Connection
) -> None:
    provider = ScriptedProvider(["Sure, the vertical is roofing!", '{"outcome": "ok"}'])
    outcome = await _infer(_runner(data_dir, provider))
    assert outcome.status is InferenceStatus.INVALID_OUTPUT
    assert len(provider.calls) == 1 + tasks.MAX_REPAIRS
    (run,) = _runs(conn)
    assert (run["attempts"], run["error_code"]) == (2, "invalid_output")
    first, second = _outputs(conn, run["id"])
    assert first["validated_output_json"] is None
    assert orjson.loads(first["validation_errors_json"])
    repair = provider.calls[1].messages
    assert [m.role for m in repair] == ["system", "user", "assistant", "user"]
    assert "refused" in repair[-1].content
    assert second["raw_output"] == '{"outcome": "ok"}'

    fixed = ScriptedProvider(["not json", ROOFING])
    repaired = await _infer(_runner(data_dir, fixed), _prepared("We repair gutters and roofs."))
    assert repaired.status is InferenceStatus.SUCCEEDED
    assert len(fixed.calls) == 2


@pytest.mark.anyio
async def test_a_failing_runtime_opens_the_breaker_and_stops_being_called(
    data_dir: Path, conn: sqlite3.Connection
) -> None:
    now = [0.0]
    breaker = CircuitBreaker(threshold=3, cooldown_s=30, clock=lambda: now[0])
    timeout = ProviderError("timeout", "Ollama did not answer in time", retryable=True)
    provider = ScriptedProvider([timeout, timeout, timeout, ROOFING])
    runner = _runner(data_dir, provider, breaker=breaker)
    for text in ("a roofer", "a plumber", "a painter"):
        outcome = await _infer(runner, _prepared(f"We are {text} fixing roofs."))
        assert (outcome.status, outcome.error_code) == (InferenceStatus.FAILED, "timeout")
    assert breaker.state is AiCircuitState.OPEN
    skipped = await _infer(runner, _prepared("We are a mason fixing roofs."))
    assert skipped.skipped is AiUnavailableReason.CIRCUIT_OPEN
    assert skipped.run_id is None
    assert len(provider.calls) == 3
    assert len(_runs(conn)) == 3
    now[0] = 31.0  # the cooldown ended: one call goes through and closes the breaker
    assert str(breaker.state) == "half_open"
    recovered = await _infer(runner, _prepared("We are a mason fixing roofs."))
    assert recovered.status is InferenceStatus.SUCCEEDED
    assert str(breaker.state) == "closed"


def test_a_failed_probe_doubles_the_cooldown() -> None:
    now = [0.0]
    breaker = CircuitBreaker(threshold=1, cooldown_s=10, clock=lambda: now[0])
    breaker.failure("down")
    assert not breaker.allow()
    now[0] = 10.0
    assert breaker.allow()
    assert not breaker.allow()
    breaker.failure("still down")
    now[0] = 25.0
    assert breaker.state is AiCircuitState.OPEN  # 20 s now
    now[0] = 30.0
    assert breaker.allow()


@pytest.mark.anyio
async def test_out_of_memory_reduces_the_context_then_runs_on_cpu(
    data_dir: Path, conn: sqlite3.Connection
) -> None:
    oom = ProviderError("out_of_memory", "CUDA error: out of memory", retryable=True)
    provider = ScriptedProvider([oom, oom, ROOFING])
    outcome = await _infer(_runner(data_dir, provider))
    assert (outcome.status, outcome.device) == (InferenceStatus.SUCCEEDED, AiDevice.CPU)
    assert [call.options["num_ctx"] for call in provider.calls] == [4096, 2048, 2048]
    assert provider.calls[2].options["num_gpu"] == 0
    (run,) = _runs(conn)
    assert orjson.loads(run["parameters_json"])["degraded"] == [
        "out_of_memory:context 4096->2048",
        "out_of_memory:ran_on_cpu",
    ]

    strict = ScriptedProvider([oom, oom, oom])
    failed = await _infer(
        _runner(data_dir, strict),
        _prepared("We repair gutters."),
        assignment=_assignment(allow_cpu=False),
    )
    assert (failed.status, failed.error_code, len(strict.calls)) == (
        InferenceStatus.FAILED, "out_of_memory", 2,
    )  # fmt: skip


@pytest.mark.anyio
async def test_a_model_that_cannot_fit_is_refused_before_any_call(
    data_dir: Path, conn: sqlite3.Connection
) -> None:
    provider = ScriptedProvider([ROOFING])
    outcome = await _infer(
        _runner(data_dir, provider, vram_free=600), assignment=_assignment(allow_cpu=False)
    )
    assert (outcome.status, outcome.error_code, provider.calls) == (
        InferenceStatus.FAILED, "insufficient_vram", [],
    )  # fmt: skip


@pytest.mark.anyio
async def test_a_model_is_unloaded_only_when_the_next_does_not_fit_next_to_it(
    data_dir: Path,
) -> None:
    provider = ScriptedProvider([ROOFING, ROOFING])
    provider.loaded_models = {"other:7b": provider_loaded("other:7b", 5 * 1024**3)}
    await _infer(_runner(data_dir, provider, vram_free=1200))
    assert provider.unloaded == ["other:7b"]
    roomy = ScriptedProvider([ROOFING])
    roomy.loaded_models = {"other:1b": provider_loaded("other:1b", 1024**3)}
    await _infer(_runner(data_dir, roomy, vram_free=6000), _prepared("We repair gutters."))
    assert roomy.unloaded == []


def provider_loaded(name: str, size: int) -> Any:
    from research_engine.ollama import LoadedModel

    return LoadedModel(name, None, size, size, 4096)


# ---- cancellation, restart, queue ---------------------------------------------------------------


@pytest.mark.anyio
async def test_queued_and_running_work_can_be_cancelled(
    data_dir: Path, conn: sqlite3.Connection
) -> None:
    provider = ScriptedProvider([ROOFING, ROOFING])
    provider.gate = asyncio.Event()
    runner = _runner(data_dir, provider)
    running = asyncio.create_task(_infer(runner, owner="job-a"))
    await _until(lambda: bool(provider.calls))
    queued = asyncio.create_task(_infer(runner, _prepared("We repair gutters."), owner="job-b"))
    await _until(lambda: runner.queue.queued > 0)
    assert runner.queue.cancel_owner("job-b") == 1
    assert (await queued).status is InferenceStatus.CANCELLED
    assert runner.queue.cancel_owner("job-a") == 1
    assert (await running).status is InferenceStatus.CANCELLED
    first, second = _runs(conn)
    assert (first["status"], first["started_at"] is not None) == ("cancelled", True)
    assert (second["status"], second["started_at"], second["finished_at"]) == (
        "cancelled",
        None,
        None,
    )


@pytest.mark.anyio
async def test_a_restart_closes_open_runs_and_never_duplicates_a_valid_result(
    data_dir: Path, conn: sqlite3.Connection
) -> None:
    valid = ScriptedProvider([ROOFING])
    done = await _infer(_runner(data_dir, valid))
    assert done.status is InferenceStatus.SUCCEEDED
    row = dict(
        conn.execute("SELECT * FROM ai_inference_runs WHERE id = ?", (done.run_id,)).fetchone()
    )

    def insert(values: dict[str, object]) -> None:
        conn.execute(
            f"INSERT INTO ai_inference_runs ({', '.join(values)}) "  # noqa: S608
            f"VALUES ({', '.join('?' * len(values))})",
            list(values.values()),
        )

    with pytest.raises(sqlite3.IntegrityError):  # a second valid result for the same request
        insert(row | {"id": "01900000-0000-7000-8000-00000000abcd"})
    # what a worker that died leaves behind: one inference waiting, one answering
    open_run = row | {"status": "running", "finished_at": None, "review_status": "unreviewed"}
    insert(open_run | {"id": "01900000-0000-7000-8000-00000000aaa1", "cache_key": "a" * 64})
    insert(open_run | {"id": "01900000-0000-7000-8000-00000000aaa2", "cache_key": "b" * 64,
                       "status": "queued", "started_at": None})  # fmt: skip
    assert store.recover(conn) == (1, 1)
    statuses = dict(conn.execute(
        "SELECT id, status || ':' || coalesce(error_code, '') FROM ai_inference_runs"
    ).fetchall())  # fmt: skip
    assert statuses["01900000-0000-7000-8000-00000000aaa1"] == "failed:worker_restarted"
    assert statuses["01900000-0000-7000-8000-00000000aaa2"] == "cancelled:worker_restarted"
    assert store.recover(conn) == (0, 0)
    after_restart = ScriptedProvider([ROOFING])
    again = await _infer(_runner(data_dir, after_restart))
    assert again.cached
    assert after_restart.calls == []


@pytest.mark.anyio
async def test_the_queue_serves_priorities_keeps_a_loaded_model_and_refuses_beyond_capacity() -> (
    None
):
    queue = GpuQueue(capacity=4)
    gate = asyncio.Event()
    order: list[str] = []

    def work(name: str) -> Callable[[], Any]:
        async def run() -> str:
            if name == "blocker":
                await gate.wait()
            order.append(name)
            return name

        return run

    futures = [queue.submit(work("blocker"), priority=Priority.JOB, owner="a", group="model-x")]
    await asyncio.sleep(0.01)
    futures += [
        queue.submit(work("benchmark"), priority=Priority.BENCHMARK, owner="b", group="model-x"),
        queue.submit(work("job-other-model"), priority=Priority.JOB, owner="c", group="model-y"),
        queue.submit(work("job-same-model"), priority=Priority.JOB, owner="d", group="model-x"),
        queue.submit(work("person"), priority=Priority.INTERACTIVE, owner="e", group="model-y"),
    ]
    with pytest.raises(QueueFullError):
        queue.submit(work("one-too-many"), priority=Priority.JOB, owner="f", group="model-x")
    gate.set()
    await asyncio.gather(*futures)
    assert order == ["blocker", "person", "job-other-model", "job-same-model", "benchmark"]
    await queue.close()


@pytest.mark.anyio
async def test_crawling_waits_for_memory_only_while_ai_works() -> None:
    monitor = _monitor(ram_free=300)
    assert monitor.pressure(monitor.sample()) == ["low_ram"]
    assert not monitor.holding()
    assert await monitor.wait_clear(max_wait_s=1, poll_s=0.01) < 0.05
    monitor.busy = lambda: True
    assert monitor.holding()
    waited = await monitor.wait_clear(max_wait_s=0.1, poll_s=0.02)
    assert 0.1 <= waited < 1  # bounded: crawling goes on after the limit
    assert not _monitor(ram_free=4000, vram_free=4000).pressure(_sample(4000, 4000))


# ---- reads and reviews --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_reviews_are_decisions_with_reasons_and_a_rejected_answer_is_asked_again(
    data_dir: Path, conn: sqlite3.Connection
) -> None:
    provider = ScriptedProvider([ROOFING, ROOFING, "not json", "still not json"])
    runner = _runner(data_dir, provider)
    first = await _infer(runner)
    accept = AiReviewRequest(inference_run_id=first.run_id, decision=AiReviewDecision.ACCEPT,
                             reason="Checked against the fragment it cites.")  # fmt: skip
    store.review(conn, accept, actor="tester")
    detail = store.get_inference(conn, first.run_id)
    assert detail.run.review_status is AiReviewStatus.ACCEPTED
    assert detail.decisions[0].decision == "ai_output_accepted"
    assert detail.inputs[0].ref == "F1"
    with pytest.raises(store.AiError, match="already accepted"):
        store.review(conn, accept, actor="tester")
    store.review(conn, accept.model_copy(update={"decision": AiReviewDecision.REJECT}), actor="t")
    assert store.cached(conn, _runs(conn)[0]["cache_key"]) is None
    second = await _infer(runner)
    assert (second.cached, second.status, len(provider.calls)) == (
        False,
        InferenceStatus.SUCCEEDED,
        2,
    )
    with pytest.raises(store.AiError, match="newer valid answer"):
        store.review(
            conn, accept.model_copy(update={"decision": AiReviewDecision.RESET}), actor="t"
        )
    invalid = await _infer(runner, _prepared("We repair gutters."))
    with pytest.raises(store.AiError, match="only an answer that validated"):
        store.review(
            conn, accept.model_copy(update={"inference_run_id": invalid.run_id}), actor="t"
        )
    page = store.list_inferences(
        conn, AiInferenceListQuery(task_type=AiTaskType.BUSINESS_CLASSIFICATION, status=None,
                                   business_id=None, review_status=None, limit=2, cursor=None),
    )  # fmt: skip
    assert page.total == 3
    assert len(page.items) == 2
    assert page.next_cursor is not None
    rest = store.list_inferences(conn, AiInferenceListQuery(
        task_type=None, status=InferenceStatus.INVALID_OUTPUT, business_id=None,
        review_status=None, limit=50, cursor=None))  # fmt: skip
    assert [item.id for item in rest.items] == [invalid.run_id]


# ---- the service ---------------------------------------------------------------------------------


@pytest.fixture
def no_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    status = GpuStatus(state=ComponentState.UNAVAILABLE, summary="no GPU in tests", error="none",
                       checked_at=utc_now(), duration_ms=0.0, devices=[], cuda_available=False,
                       cuda_driver_version=None)  # fmt: skip
    monkeypatch.setattr(ai_engine, "probe_gpu", lambda: status)
    monkeypatch.setattr(ai_engine, "take_sample", lambda: _sample())


def _service(data_dir: Path, provider: Any, **options: Any) -> AiService:
    llm_fit = LlmFitReport(available=False, version=None, models=[], error="not installed",
                           checked_at=utc_now())  # fmt: skip
    return AiService(data_dir, lambda: repository.connect(data_dir), provider, _monitor(),
                     downloads_allowed=options.get("downloads_allowed", True),
                     llm_fit=lambda: llm_fit)  # fmt: skip


def _configure(profile_model: str | None, visual: bool = False) -> AiConfigureRequest:
    return AiConfigureRequest(
        profiles=[AiProfileChange(profile=p, model=profile_model, num_ctx=4096, allow_cpu=True)
                  for p in AiProfile],
        tasks=[AiTaskChange(task_type=t, enabled=visual or t is not AiTaskType.VISUAL_REVIEW,
                            profile=AiProfile.FAST) for t in AiTaskType],
        min_free_ram_mib=1024, min_free_vram_mib=512,
    )  # fmt: skip


async def _finished(service: AiService, conn: sqlite3.Connection, job_id: str) -> Any:
    await service.idle(job_id)
    return store.get_run(conn, job_id)


@pytest.mark.anyio
@pytest.mark.usefixtures("no_gpu")
async def test_without_a_runtime_every_task_says_why_and_jobs_skip_instead_of_failing(
    data_dir: Path, conn: sqlite3.Connection, synthetic: SyntheticContext
) -> None:
    seed_latino(conn, synthetic, data_dir)
    service = _service(data_dir, OllamaProvider("http://127.0.0.1:9", 0.5))
    status = await service.status(AiStatusQuery(include_llm_fit=True))
    assert not status.runtime.running
    assert status.models == []
    assert {task.unavailable_reason for task in status.tasks} == {
        AiUnavailableReason.NO_MODEL_ASSIGNED, AiUnavailableReason.TASK_DISABLED,
    }  # fmt: skip
    assert status.llm_fit is not None
    assert not status.llm_fit.available
    with pytest.raises(store.AiError, match="not running"):
        await service.configure(_configure(TEXT_MODEL))
    request = AiStartRequest(project_id=synthetic.project_id, business_ids=[],
                             task_types=[AiTaskType.BUSINESS_CLASSIFICATION])  # fmt: skip
    page = await service.start(request)
    run = await _finished(service, conn, page.items[0].job_id)
    assert run.status is JobStatus.SUCCEEDED
    assert run.stats.skipped_unavailable == run.stats.targets > 0
    assert _runs(conn) == []
    await service.shutdown()


@pytest.mark.anyio
@pytest.mark.usefixtures("no_gpu")
async def test_a_project_job_runs_every_task_records_everything_and_resumes(
    data_dir: Path, conn: sqlite3.Connection, synthetic: SyntheticContext
) -> None:
    seed_latino(conn, synthetic, data_dir)
    failing = [True]
    timeout = ProviderError("timeout", "no answer", retryable=True)

    def script(request: ChatRequest) -> str | ProviderError:
        return timeout if failing[0] else synthetic_answer(request)

    provider = ScriptedProvider(script)
    service = _service(data_dir, provider)
    await service.configure(_configure(TEXT_MODEL))
    wanted = [AiTaskType.EVIDENCE_EXTRACTION, AiTaskType.BUSINESS_CLASSIFICATION]
    page = await service.start(AiStartRequest(project_id=synthetic.project_id, business_ids=[],
                                              task_types=wanted))  # fmt: skip
    job_id = page.items[0].job_id
    paused = await _finished(service, conn, job_id)
    assert paused.status is JobStatus.PAUSED  # the breaker opened: paused, never looping
    assert any(event.event == "run_interrupted" for event in paused.events)
    assert len(provider.calls) == 3
    service.breaker.success()
    failing[0] = False
    await service.control(DiscoveryControlRequest(job_id=job_id, action=RunControlAction.RESUME))
    run = await _finished(service, conn, job_id)
    assert run.status is JobStatus.SUCCEEDED
    stats = run.stats
    assert stats.done == stats.targets
    assert stats.failed == 3
    assert (
        stats.succeeded + stats.insufficient_evidence + stats.skipped_no_input + 3 == stats.targets
    )
    assert stats.latino_candidates_accepted >= 1  # the Latino rules confirmed a declaration
    rows = conn.execute(
        "SELECT task_type, business_id, job_id FROM ai_inference_runs WHERE status = 'succeeded'"
    ).fetchall()
    assert rows
    assert all(row["job_id"] == job_id and row["business_id"] for row in rows)
    proposed = conn.execute(
        "SELECT count(*) FROM extractions WHERE extractor = 'latino.model'"
    ).fetchone()[0]
    assert proposed >= 1

    calls = len(provider.calls)
    again = await service.start(AiStartRequest(project_id=synthetic.project_id, business_ids=[],
                                               task_types=wanted))  # fmt: skip
    repeat = await _finished(service, conn, again.items[0].job_id)
    assert repeat.stats.cache_hits == stats.succeeded + stats.insufficient_evidence
    assert len(provider.calls) == calls + 3  # only the three that had failed are asked again
    listed = store.list_runs(
        conn, AiRunListQuery(project_id=synthetic.project_id, limit=10, cursor=None)
    )
    assert listed.total == 2
    await service.shutdown()


@pytest.mark.anyio
@pytest.mark.usefixtures("no_gpu")
async def test_status_reports_models_estimates_availability_and_the_queue(
    data_dir: Path,
) -> None:
    service = _service(data_dir, ScriptedProvider())
    status = await service.configure(_configure(TEXT_MODEL, visual=True))
    assert [model.name for model in status.models] == ["synthetic-text:1b", "synthetic-vision:2b"]
    text = status.models[0]
    assert text.estimate is not None
    assert text.estimate.kv_cache_mib > 0
    assert text.context_length == 8192
    assert text.capabilities == ["completion"]
    by_task = {task.task_type: task for task in status.tasks}
    assert by_task[AiTaskType.BUSINESS_CLASSIFICATION].available
    assert (
        by_task[AiTaskType.VISUAL_REVIEW].unavailable_reason is AiUnavailableReason.NO_VISION_MODEL
    )
    assert status.scheduler.circuit is AiCircuitState.CLOSED
    assert status.downloads_allowed
    with pytest.raises(store.AiError, match="not installed"):
        await service.configure(_configure("missing-model:7b"))
    await service.shutdown()


@pytest.mark.anyio
@pytest.mark.usefixtures("no_gpu")
async def test_a_benchmark_measures_accuracy_latency_and_memory_and_recommends(
    data_dir: Path, conn: sqlite3.Connection
) -> None:
    provider = ScriptedProvider(lambda _: ROOFING)
    service = _service(data_dir, provider)
    request = AiBenchmarkRequest(action=AiBenchmarkAction.START, models=[TEXT_MODEL],
                                 task_types=[AiTaskType.BUSINESS_CLASSIFICATION])  # fmt: skip
    await service.benchmark(request)
    assert service._benchmark_task is not None
    await service._benchmark_task
    status = await service.status(AiStatusQuery(include_llm_fit=False))
    assert status.benchmark.state == "succeeded"
    assert status.benchmark.done == 6
    (result,) = status.benchmark_results
    assert (result.cases, result.valid_outputs, result.correct) == (
        6,
        6,
        1,
    )  # only roofing is right
    assert result.latency_p50_ms is not None
    assert result.tokens_per_second == 5000.0
    assert result.peak_vram_used_mib == 8192 - 7000
    (recommendation,) = status.recommendations
    assert (recommendation.model, recommendation.accuracy) == (TEXT_MODEL, 0.167)
    assert _runs(conn) == []  # benchmark answers are measured, not kept as inferences
    with pytest.raises(store.AiError, match="visual review"):
        await service.benchmark(
            request.model_copy(update={"task_types": [AiTaskType.VISUAL_REVIEW]})
        )
    await service.shutdown()


def test_recommendations_skip_models_that_mostly_failed_or_changed() -> None:
    from research_engine.contracts import OllamaModel
    from research_engine.domain import AiBenchmarkResult, new_id

    def result(
        model: str, correct: int, valid: int, latency: int, digest: str = "d"
    ) -> AiBenchmarkResult:
        now = utc_now()
        return AiBenchmarkResult(
            id=new_id(), provider="ollama", model=model, model_digest=digest,
            task_type=AiTaskType.BUSINESS_CLASSIFICATION, task_version="1.0.0",
            corpus_version="1.0.0", num_ctx=4096, cases=10, valid_outputs=valid, correct=correct,
            failures=0, latency_p50_ms=latency, latency_p95_ms=latency, tokens_per_second=None,
            peak_vram_used_mib=None, min_free_ram_mib=None, device=AiDevice.GPU, started_at=now,
            finished_at=now, created_at=now,
        )  # fmt: skip

    catalog = {name: OllamaModel(name=name, size_bytes=1, digest="d", family=None,
                                 parameter_size=None, quantization=None)
               for name in ("fast:1b", "slow:8b", "flaky:3b")}  # fmt: skip
    results = [
        result("fast:1b", 8, 10, 300),
        result("slow:8b", 8, 10, 900),
        result("flaky:3b", 7, 7, 100),  # too many invalid answers
        result("gone:9b", 10, 10, 50),  # no longer installed
    ]
    (best,) = recommend(results, catalog)
    assert (best.model, best.accuracy) == ("fast:1b", 0.8)
    assert recommend([result("fast:1b", 9, 10, 100, digest="changed")], catalog) == []


@pytest.mark.anyio
@pytest.mark.usefixtures("no_gpu")
async def test_models_change_only_with_the_exact_name_and_downloads_follow_offline_mode(
    data_dir: Path,
) -> None:
    provider = ScriptedProvider()
    service = _service(data_dir, provider)
    pull = AiModelChangeRequest(action=AiModelChangeAction.PULL, model="synthetic-new:1b",
                                confirmation="synthetic-new")  # fmt: skip
    with pytest.raises(store.AiError, match="exact model name"):
        await service.change_model(pull)
    await service.change_model(pull.model_copy(update={"confirmation": "synthetic-new:1b"}))
    assert service._change_task is not None
    await service._change_task
    assert service._change is not None
    assert service._change.state == "succeeded"
    assert service._change.completed_bytes == 1024
    assert "synthetic-new:1b" in provider.models
    delete = AiModelChangeRequest(action=AiModelChangeAction.DELETE, model="synthetic-new:1b",
                                  confirmation="synthetic-new:1b")  # fmt: skip
    status = await service.change_model(delete)
    assert "synthetic-new:1b" not in {model.name for model in status.models}
    with pytest.raises(store.AiError, match="not installed"):
        await service.change_model(delete)
    offline = _service(data_dir, ScriptedProvider(), downloads_allowed=False)
    with pytest.raises(store.AiError, match="offline"):
        await offline.change_model(pull.model_copy(update={"confirmation": "synthetic-new:1b"}))
    await service.shutdown()


def test_the_worker_talks_to_the_real_runtime_only() -> None:
    settings = Settings.from_env({"LEAD_OLLAMA_URL": "http://127.0.0.1:9"})
    worker = Worker(settings, io.BytesIO())
    assert isinstance(worker._ai.provider, OllamaProvider)
    assert worker._ai.provider.base_url == "http://127.0.0.1:9"


def test_jobs_track_open_runs_per_request(
    conn: sqlite3.Connection, synthetic: SyntheticContext, data_dir: Path
) -> None:
    seed_latino(conn, synthetic, data_dir)
    request = AiStartRequest(project_id=synthetic.project_id, business_ids=[],
                             task_types=[AiTaskType.LANGUAGE_DETECTION])  # fmt: skip
    created, listed = store.plan_runs(conn, request, actor="t")
    assert len(created) == 1
    assert listed == []
    assert store.plan_runs(conn, request, actor="t") == ([], created)
    other = request.model_copy(update={"task_types": [AiTaskType.EVIDENCE_EXTRACTION]})
    assert len(store.plan_runs(conn, other, actor="t")[0]) == 1
    jobs.apply_control(conn, created[0], RunControlAction.CANCEL, "t")
    assert store.get_run(conn, created[0]).status is JobStatus.CANCELLED
