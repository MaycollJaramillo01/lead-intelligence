"""Campaign pipelines (phase 13): the DAG, claims, retries, dead letters, control, restarts and the
event log, over the synthetic workspace. The engine jobs here are rows a test finishes by hand;
tests/test_worker_process.py runs a pipeline through the real engines."""

import asyncio
import sqlite3
import time
from collections.abc import Callable, Iterable
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from research_engine import datasets, jobs, operations, repository
from research_engine.ai.resources import ResourceMonitor, Sample
from research_engine.domain import (
    BulkMode,
    CrawlStartRequest,
    DeadLetterAction,
    DeadLetterRequest,
    DeadLetterStatus,
    DiscoveryControlRequest,
    EventCleanupRequest,
    EventLevel,
    Job,
    JobEventQuery,
    JobStatus,
    PipelineListQuery,
    PipelineResource,
    PipelineRun,
    PipelineStage,
    PipelineStageView,
    PipelineStartRequest,
    RunControlAction,
    SyntheticSeedResult,
    campaign_crawl_settings,
    format_ts,
    new_id,
    utc_now,
)
from research_engine.leads import explorer
from research_engine.operations import StageTarget, classify
from research_engine.testdata import seed_synthetic_workspace

_S = PipelineStage
REQUEST_ID = "01900000-0000-7000-8000-000000000099"


@pytest.fixture
def seeded(conn: sqlite3.Connection, data_dir: Path) -> SyntheticSeedResult:
    return seed_synthetic_workspace(conn, data_dir)


class FakeEngines:
    """Engine jobs as rows: a held stage's job runs until `release`; `fail` raises on its starts."""

    def __init__(
        self,
        data_dir: Path,
        *,
        hold: Iterable[PipelineStage] = (),
        fail: dict[PipelineStage, list[BaseException]] | None = None,
    ) -> None:
        self.data_dir = data_dir
        self.hold = set(hold)
        self.fail = {stage: list(errors) for stage, errors in (fail or {}).items()}
        self.started: list[tuple[PipelineStage, str]] = []
        self.controls: list[tuple[str, RunControlAction]] = []

    async def start(self, stage: PipelineStage, target: StageTarget) -> list[str]:
        if self.fail.get(stage):
            raise self.fail[stage].pop(0)
        kind = operations.SPECS[stage].job_kind
        assert kind is not None
        now = utc_now()
        held = stage in self.hold
        job = Job(
            id=new_id(),
            project_id=target.project_id,
            kind=kind,
            status=JobStatus.RUNNING if held else JobStatus.SUCCEEDED,
            priority=0,
            params_json={"project_id": target.project_id, "campaign_id": target.campaign_id},
            idempotency_key=None,
            progress_done=0 if held else 5,
            progress_total=5,
            requested_at=now,
            started_at=now,
            finished_at=None if held else now,
            created_at=now,
            updated_at=now,
        )
        conn = repository.connect(self.data_dir)
        try:
            repository.insert(conn, job)
        finally:
            conn.close()
        self.started.append((stage, job.id))
        return [job.id]

    async def control(self, kind: str, job_id: str, action: RunControlAction) -> None:
        conn = repository.connect(self.data_dir)
        try:
            jobs.apply_control(conn, job_id, action, "fake-engine")
        finally:
            conn.close()
        self.controls.append((job_id, action))

    def job_of(self, stage: PipelineStage) -> str | None:
        found = [job_id for started, job_id in self.started if started is stage]
        return found[-1] if found else None

    def release(self, stage: PipelineStage) -> None:
        job_id = self.job_of(stage)
        assert job_id is not None
        conn = repository.connect(self.data_dir)
        try:
            if repository.get(conn, Job, job_id).status is JobStatus.PAUSED:
                repository.change_status(conn, Job, job_id, JobStatus.RUNNING, actor="fake-engine")
            repository.change_status(conn, Job, job_id, JobStatus.SUCCEEDED, actor="fake-engine")
            conn.execute(
                "UPDATE jobs SET finished_at = ? WHERE id = ?", (format_ts(utc_now()), job_id)
            )
        finally:
            conn.close()


def _service(data_dir: Path, engines: FakeEngines) -> operations.PipelineService:
    monitor = ResourceMonitor(sampler=lambda: Sample(16_000, 8_000, None, None, utc_now()))
    return operations.PipelineService(
        data_dir, lambda: repository.connect(data_dir), engines, monitor, poll_s=0.02
    )


def _start(project_id: str, key: str = "fixture-pipeline-1") -> PipelineStartRequest:
    return PipelineStartRequest(
        project_id=project_id,
        campaign_id=None,
        check_mail_domains=False,
        priority=0,
        idempotency_key=key,
    )


async def _until(check: Callable[[], bool], seconds: float = 30.0) -> None:
    deadline = time.monotonic() + seconds
    while not check():
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        await asyncio.sleep(0.05)


def _task_status(conn: sqlite3.Connection, job_id: str, stage: PipelineStage) -> str:
    (status,) = conn.execute(
        "SELECT status FROM job_tasks WHERE job_id = ? AND stage = ?", (job_id, stage.value)
    ).fetchone()
    return str(status)


def _stage(run: PipelineRun, stage: PipelineStage) -> PipelineStageView:
    return next(view for view in run.stages if view.stage is stage)


def test_a_pipeline_is_planned_once_per_key_with_its_dag_fixed(
    conn: sqlite3.Connection, seeded: SyntheticSeedResult
) -> None:
    request = _start(seeded.project_id)
    job_id, created = operations.plan_pipeline(conn, request, actor="test", request_id=None)
    again, created_again = operations.plan_pipeline(conn, request, actor="test", request_id=None)
    assert (again, created, created_again) == (job_id, True, False)
    stages = [
        row[0]
        for row in conn.execute(
            "SELECT stage FROM pipeline_stage_specs WHERE job_id = ? ORDER BY ordinal", (job_id,)
        )
    ]
    assert stages == [spec.stage.value for spec in operations.PIPELINE]
    (dependencies,) = conn.execute(
        "SELECT count(*) FROM pipeline_stage_dependencies WHERE job_id = ?", (job_id,)
    ).fetchone()
    assert dependencies == sum(len(spec.depends_on) for spec in operations.PIPELINE)
    with pytest.raises(sqlite3.DatabaseError, match="fixed"):
        conn.execute("UPDATE pipeline_stage_specs SET version = '2' WHERE job_id = ?", (job_id,))
    missing = request.model_copy(update={"campaign_id": new_id(), "idempotency_key": "fixture-2"})
    with pytest.raises(repository.NotFoundError):
        operations.plan_pipeline(conn, missing, actor="test", request_id=None)
    # the kinds a pipeline drives are the engines' own
    kinds = set(operations.KINDS)
    assert kinds == {
        "discovery",
        "crawl",
        "resolution",
        "latino_evidence",
        "website_audit",
        "contact_intelligence",
        "ai_analysis",
        "lead_scoring",
    }


@pytest.mark.anyio
async def test_stages_run_in_order_skip_what_has_nothing_to_do_and_end_with_a_snapshot(
    conn: sqlite3.Connection, data_dir: Path, seeded: SyntheticSeedResult
) -> None:
    engines = FakeEngines(data_dir)
    service = _service(data_dir, engines)
    run = await service.start(_start(seeded.project_id), actor="test", request_id=REQUEST_ID)
    await service.idle(run.job_id)
    view = service.view(conn, run.job_id)

    assert view.status is JobStatus.SUCCEEDED, view.dead_letters
    assert (view.progress_done, view.progress_total) == (10, 10)
    assert (view.counts.skipped, view.counts.succeeded) == (2, 8)
    for stage in (_S.DISCOVERY, _S.ACQUISITION):
        assert _stage(view, stage).skipped == "no_campaign"
    assert [stage for stage, _ in engines.started] == [
        _S.NORMALIZATION,
        _S.LATINO_EVIDENCE,
        _S.WEBSITE_INTELLIGENCE,
        _S.CONTACT_INTELLIGENCE,
        _S.LOCAL_AI,
        _S.SCORING,
    ]
    # entity resolution waits for the resolution job that normalised the records, never another
    normalized = _stage(view, _S.NORMALIZATION).attempts[0].jobs[0]
    resolved = _stage(view, _S.ENTITY_RESOLUTION).attempts[0].jobs[0]
    assert (resolved.job_id, resolved.reused, normalized.reused) == (normalized.job_id, True, False)
    started = [
        row[0]
        for row in conn.execute(
            "SELECT json_extract(detail_json, '$.stage') FROM job_events WHERE job_id = ? "
            "AND event = 'stage_started' ORDER BY created_at, id",
            (run.job_id,),
        )
    ]
    assert started == [spec.stage.value for spec in operations.PIPELINE]

    # the last stage counts the eligible leads of the newest run and writes a snapshot with it
    run_id = datasets.newest_finished_run(conn, seeded.project_id)
    suppressed = explorer.suppressed_roots(conn)
    eligible = sum(
        1
        for (business_id,) in conn.execute(
            "SELECT business_id FROM lead_index WHERE score_run_id = ? "
            "AND tier IN ('priority', 'review')",
            (run_id,),
        )
        if business_id not in suppressed
    )
    assert (view.score_run_id, view.eligible_leads) == (run_id, eligible)
    assert view.snapshot_id is not None
    snapshot = datasets.snapshot_item(conn, view.snapshot_id).snapshot
    assert snapshot.pipeline_versions_json["pipeline"] == "1"
    assert snapshot.query_json["score_run_id"] == run_id
    assert view.actions == []

    events = operations.list_events(
        conn,
        JobEventQuery(
            job_id=None,
            task_id=None,
            business_id=None,
            request_id=REQUEST_ID,
            levels=[],
            limit=10,
            cursor=None,
        ),
    )
    assert [item.event.event for item in events.items] == ["pipeline_queued"]


@pytest.mark.anyio
async def test_a_recoverable_failure_retries_and_a_refusal_waits_for_a_person(
    conn: sqlite3.Connection,
    data_dir: Path,
    seeded: SyntheticSeedResult,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(operations, "MAX_BACKOFF_S", 0.0)
    engines = FakeEngines(
        data_dir,
        fail={
            _S.LATINO_EVIDENCE: [repository.StoreUnavailableError("the store was busy (test)")],
            _S.WEBSITE_INTELLIGENCE: [
                ValueError("no finished crawl (test)"),
                ValueError("still none"),
            ],
        },
    )
    service = _service(data_dir, engines)
    run = await service.start(_start(seeded.project_id), actor="test", request_id=None)
    await service.idle(run.job_id)
    view = service.view(conn, run.job_id)

    latino = _stage(view, _S.LATINO_EVIDENCE)
    assert [attempt.status for attempt in latino.attempts] == [
        JobStatus.FAILED,
        JobStatus.SUCCEEDED,
    ]
    first = latino.attempts[0]
    assert (first.error_code, first.retryable) == ("store_unavailable", True)
    assert view.status is JobStatus.FAILED
    (letter,) = view.dead_letters
    assert (letter.stage, letter.error_code, letter.retryable, letter.review_status) == (
        _S.WEBSITE_INTELLIGENCE,
        "configuration",
        False,
        DeadLetterStatus.OPEN,
    )
    assert _stage(view, _S.LOCAL_AI).status is JobStatus.QUEUED

    # a person retries: the stage is refused again, then skipped
    retry = DeadLetterRequest(
        job_id=run.job_id,
        task_id=letter.task_id,
        action=DeadLetterAction.RETRY,
        reason="Crawled the sites (test)",
    )
    await service.resolve(retry, actor="tester")
    await service.idle(run.job_id)
    view = service.view(conn, run.job_id)
    assert view.status is JobStatus.FAILED
    assert [entry.review_status for entry in view.dead_letters] == [
        DeadLetterStatus.RETRIED,
        DeadLetterStatus.OPEN,
    ]
    skip = retry.model_copy(
        update={"action": DeadLetterAction.SKIP, "reason": "No website to audit (test)"}
    )
    await service.resolve(skip, actor="tester")
    await service.idle(run.job_id)
    view = service.view(conn, run.job_id)
    assert view.status is JobStatus.SUCCEEDED
    assert _stage(view, _S.WEBSITE_INTELLIGENCE).skipped == "skipped_by_person"
    assert [(entry.review_status, entry.resolved_by) for entry in view.dead_letters] == [
        (DeadLetterStatus.RETRIED, "tester"),
        (DeadLetterStatus.SKIPPED, "tester"),
    ]
    with pytest.raises(sqlite3.DatabaseError, match="keeps its failure"):
        conn.execute("UPDATE dead_letter_tasks SET error_code = 'x' WHERE id = ?", (letter.id,))
    with pytest.raises(repository.NotFoundError):
        await service.resolve(skip, actor="tester")  # nothing is open any more


@pytest.mark.anyio
async def test_pause_resume_and_cancel_reach_the_engine_job_of_the_stage(
    conn: sqlite3.Connection, data_dir: Path, seeded: SyntheticSeedResult
) -> None:
    engines = FakeEngines(data_dir, hold=[_S.LATINO_EVIDENCE])
    service = _service(data_dir, engines)
    run = await service.start(_start(seeded.project_id), actor="test", request_id=None)
    await _until(lambda: engines.job_of(_S.LATINO_EVIDENCE) is not None)
    child = engines.job_of(_S.LATINO_EVIDENCE)
    assert child is not None

    paused = await service.control(
        DiscoveryControlRequest(job_id=run.job_id, action=RunControlAction.PAUSE)
    )
    assert paused.status is JobStatus.PAUSED
    assert (child, RunControlAction.PAUSE) in engines.controls
    assert _stage(paused, _S.LATINO_EVIDENCE).status is JobStatus.PAUSED
    assert paused.actions == [RunControlAction.RESUME, RunControlAction.CANCEL]

    await service.control(
        DiscoveryControlRequest(job_id=run.job_id, action=RunControlAction.RESUME)
    )
    await _until(lambda: (child, RunControlAction.RESUME) in engines.controls)
    cancelled = await service.control(
        DiscoveryControlRequest(job_id=run.job_id, action=RunControlAction.CANCEL)
    )
    assert cancelled.status is JobStatus.CANCELLED
    assert (child, RunControlAction.CANCEL) in engines.controls
    assert repository.get(conn, Job, child).status is JobStatus.CANCELLED
    assert {stage.status for stage in cancelled.stages} <= {
        JobStatus.SUCCEEDED,
        JobStatus.CANCELLED,
    }
    assert _stage(cancelled, _S.SCORING).status is JobStatus.CANCELLED


@pytest.mark.anyio
async def test_a_restart_resumes_the_engine_job_a_stage_started_instead_of_starting_another(
    conn: sqlite3.Connection, data_dir: Path, seeded: SyntheticSeedResult
) -> None:
    engines = FakeEngines(data_dir, hold=[_S.WEBSITE_INTELLIGENCE])
    first = _service(data_dir, engines)
    run = await first.start(_start(seeded.project_id), actor="test", request_id=None)
    await _until(lambda: engines.job_of(_S.WEBSITE_INTELLIGENCE) is not None)
    child = engines.job_of(_S.WEBSITE_INTELLIGENCE)
    assert child is not None
    await first.shutdown()  # the worker stops: the pipeline is paused with its stage in flight
    assert repository.get(conn, Job, run.job_id).status is JobStatus.PAUSED
    jobs.apply_control(conn, child, RunControlAction.PAUSE, "engine")  # as the engine does too

    second = _service(data_dir, engines)
    await second.recover()
    await second.control(DiscoveryControlRequest(job_id=run.job_id, action=RunControlAction.RESUME))
    await _until(lambda: (child, RunControlAction.RESUME) in engines.controls)
    engines.release(_S.WEBSITE_INTELLIGENCE)
    await second.idle(run.job_id)
    view = second.view(conn, run.job_id)
    assert view.status is JobStatus.SUCCEEDED, view.dead_letters
    assert [stage for stage, _ in engines.started].count(_S.WEBSITE_INTELLIGENCE) == 1
    website = _stage(view, _S.WEBSITE_INTELLIGENCE)
    assert [attempt.jobs[0].job_id for attempt in website.attempts] == [child]


def test_one_stage_runs_per_project_and_every_resource_has_its_slots(
    conn: sqlite3.Connection, seeded: SyntheticSeedResult
) -> None:
    first, _ = operations.plan_pipeline(
        conn, _start(seeded.project_id, "fixture-a"), actor="test", request_id=None
    )
    second, _ = operations.plan_pipeline(
        conn, _start(seeded.project_id, "fixture-b"), actor="test", request_id=None
    )
    for job_id in (first, second):
        jobs.mark_running(conn, job_id, "test")
    limits = operations.default_limits()
    claimed = operations.claim_next_task(conn, first, limits)
    assert isinstance(claimed, tuple)
    assert claimed[0].stage == "discovery"
    assert operations.claim_next_task(conn, second, limits) == "project_busy"
    operations.finish_task(conn, claimed[0].id, {"skipped": "no_campaign"})

    # acquisition uses http, a browser and the disk
    assert operations.claim_next_task(conn, first, limits | {PipelineResource.BROWSER: 0}) == (
        "slot_browser"
    )
    assert operations.claim_next_task(conn, first, limits, ["low_disk"]) == "low_disk"
    assert operations.claim_next_task(conn, first, limits, ["low_ram"]) == "low_ram"
    claimed = operations.claim_next_task(conn, first, limits)
    assert isinstance(claimed, tuple)
    assert claimed[0].stage == "acquisition"
    assert operations.running_by_resource(conn)[PipelineResource.BROWSER] == 1


def test_errors_are_classified_as_recoverable_or_needing_a_person(
    conn: sqlite3.Connection, seeded: SyntheticSeedResult
) -> None:
    assert classify(repository.StoreUnavailableError("down")).retryable
    assert (classify(TimeoutError()).code, classify(TimeoutError()).retryable) == ("timeout", True)
    refusal = classify(ValueError("no crawl yet"))
    assert (refusal.code, refusal.retryable) == ("configuration", False)
    assert classify(RuntimeError("boom")).code == "internal"

    now = utc_now()
    job = Job(
        id=new_id(),
        project_id=seeded.project_id,
        kind="latino_evidence",
        status=JobStatus.FAILED,
        priority=0,
        params_json={},
        idempotency_key=None,
        progress_done=0,
        progress_total=None,
        requested_at=now,
        started_at=now,
        finished_at=now,
        created_at=now,
        updated_at=now,
    )
    repository.insert(conn, job)
    jobs.write_event(
        conn,
        job.id,
        None,
        "run_failed",
        EventLevel.ERROR,
        {"message": "the network was down"},
        "network",
        True,
    )
    failure = operations.child_failure(conn, job.id, job.kind, JobStatus.FAILED)
    assert (failure.code, failure.retryable, failure.message) == (
        "network",
        True,
        "the network was down",
    )
    assert operations.child_failure(conn, job.id, job.kind, JobStatus.CANCELLED).code == (
        "engine_job_cancelled"
    )


@pytest.mark.anyio
async def test_the_event_log_filters_by_job_task_level_and_business_and_keeps_errors(
    conn: sqlite3.Connection, data_dir: Path, seeded: SyntheticSeedResult
) -> None:
    engines = FakeEngines(data_dir)
    service = _service(data_dir, engines)
    run = await service.start(_start(seeded.project_id), actor="test", request_id=None)
    await service.idle(run.job_id)
    view = service.view(conn, run.job_id)
    child = _stage(view, _S.LATINO_EVIDENCE).attempts[0].jobs[0].job_id
    jobs.write_event(
        conn,
        child,
        None,
        "business_skipped",
        EventLevel.WARNING,
        {"business_id": seeded.business_id},
    )
    jobs.write_event(
        conn,
        run.job_id,
        None,
        "fixture_failure",
        EventLevel.ERROR,
        {"message": "kept"},
        "internal",
        False,
    )

    def query(**fields: object) -> JobEventQuery:
        base: dict[str, object] = dict.fromkeys(JobEventQuery.model_fields)
        return JobEventQuery.model_validate(base | {"levels": [], "limit": 500} | fields)

    everything = operations.list_events(conn, query(job_id=run.job_id))
    assert "business_skipped" in {item.event.event for item in everything.items}  # engine jobs too
    assert everything.total == len(everything.items)
    infos = operations.list_events(conn, query(job_id=run.job_id, levels=[EventLevel.INFO]))
    assert {item.event.level for item in infos.items} == {EventLevel.INFO}
    task = _stage(view, _S.SCORING).task_id
    staged = operations.list_events(conn, query(task_id=task))
    assert {item.stage for item in staged.items} == {"scoring"}
    (found,) = operations.list_events(conn, query(business_id=seeded.business_id)).items
    assert (found.event.event, found.job_kind) == ("business_skipped", "latino_evidence")
    first = operations.list_events(conn, query(job_id=run.job_id, limit=3))
    assert first.next_cursor is not None
    second = operations.list_events(
        conn, query(job_id=run.job_id, limit=3, cursor=first.next_cursor)
    )
    assert not {item.event.id for item in first.items} & {item.event.id for item in second.items}

    request = EventCleanupRequest(
        mode=BulkMode.PREVIEW,
        older_than_days=1,
        levels=[EventLevel.INFO, EventLevel.WARNING],
        reason=None,
    )
    later = utc_now() + timedelta(days=2)
    preview = operations.cleanup_events(conn, request, later)
    assert preview.matched > 0
    assert (preview.deleted, preview.kept_errors >= 1) == (0, True)
    errors_before = conn.execute("SELECT count(*) FROM job_events WHERE level = 'error'").fetchone()
    applied = operations.cleanup_events(
        conn,
        request.model_copy(update={"mode": BulkMode.APPLY, "reason": "Retention (test)"}),
        later,
    )
    assert applied.deleted == preview.matched
    left = operations.list_events(conn, query(job_id=run.job_id))
    assert {item.event.level for item in left.items} == {EventLevel.ERROR}
    errors_after = conn.execute("SELECT count(*) FROM job_events WHERE level = 'error'").fetchone()
    assert tuple(errors_after) == tuple(errors_before)
    with pytest.raises(ValidationError, match="kept"):
        EventCleanupRequest(
            mode=BulkMode.PREVIEW, older_than_days=1, levels=[EventLevel.ERROR], reason=None
        )
    with pytest.raises(ValidationError, match="reason"):
        request.model_validate(request.model_dump() | {"mode": "apply"})


@pytest.mark.anyio
async def test_the_list_shows_estimates_the_controls_allowed_and_this_machines_resources(
    conn: sqlite3.Connection, data_dir: Path, seeded: SyntheticSeedResult
) -> None:
    engines = FakeEngines(data_dir, hold=[_S.LATINO_EVIDENCE])
    service = _service(data_dir, engines)
    run = await service.start(_start(seeded.project_id), actor="test", request_id=None)
    await _until(lambda: engines.job_of(_S.LATINO_EVIDENCE) is not None)
    child = engines.job_of(_S.LATINO_EVIDENCE)
    ten_minutes_ago = format_ts(utc_now() - timedelta(minutes=10))
    conn.execute(
        "UPDATE jobs SET progress_done = 30, progress_total = 90, started_at = ? WHERE id = ?",
        (ten_minutes_ago, child),
    )
    page = await service.listing(
        PipelineListQuery(project_id=seeded.project_id, limit=10, cursor=None)
    )
    (item,) = page.items
    (engine_job,) = _stage(item, _S.LATINO_EVIDENCE).attempts[0].jobs
    assert engine_job.rate_per_minute == pytest.approx(3.0, rel=0.05)
    assert engine_job.eta_seconds == pytest.approx(1200, rel=0.05)
    assert item.actions == [RunControlAction.PAUSE, RunControlAction.CANCEL]
    slots = {slot.resource: slot for slot in page.resources.slots}
    assert slots[PipelineResource.CPU].running == 1
    assert (page.resources.ram_free_mib, page.resources.disk_free_mib is not None) == (8_000, True)
    await service.control(
        DiscoveryControlRequest(job_id=run.job_id, action=RunControlAction.CANCEL)
    )


@pytest.mark.anyio
async def test_the_acquisition_stage_asks_for_every_site_of_the_campaign() -> None:
    asked: list[CrawlStartRequest] = []

    class Crawl:
        async def start(self, request: CrawlStartRequest) -> SimpleNamespace:
            asked.append(request)
            return SimpleNamespace(job_id="crawl-job")

    engines = operations.ServiceEngines(
        discovery=None,
        crawl=Crawl(),
        resolution=None,
        latino=None,
        website=None,
        contacts=None,
        ai=None,
        scoring=None,
    )
    campaign_id = new_id()
    target = StageTarget(project_id=new_id(), campaign_id=campaign_id, check_mail_domains=False)
    assert await engines.start(_S.ACQUISITION, target) == ["crawl-job"]
    (request,) = asked
    assert (request.campaign_id, request.urls) == (campaign_id, [])
    assert request.settings == campaign_crawl_settings()  # not the first hundred sites (D-103)
