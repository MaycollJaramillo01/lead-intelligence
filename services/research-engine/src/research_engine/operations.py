"""Campaign pipelines (phase 13): one durable DAG over the engines, never a second scheduler.

    queued ─ claim: dependencies done, one stage at a time per project (stages write the same
             businesses), a free slot for every resource the stage uses, no memory or disk pressure
           ─ attempt: attach the engine job already open for the project, or start one
           ─ wait: the engine does the work with its own checkpoints, limits and cancellation;
                   pause, resume and cancel are passed on to it
           ─ succeeded, or skipped when there was nothing to do ─ the next stages
           ─ failed ─ queued again when the error is recoverable and attempts remain
                    ─ otherwise a dead letter: the pipeline fails until a person retries or skips it

Everything is written to the store before it is acted on. A restart finds the pipeline paused
(D-027) with its attempts and the engine jobs they started; resuming continues those jobs instead of
starting them again. Pipelines of different projects run side by side within the resource slots.
"""

import asyncio
import contextlib
import logging
import os
import shutil
import sqlite3
import time
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final, Protocol

import orjson

from research_engine import datasets, jobs, repository
from research_engine.ai import resources
from research_engine.ai.resources import ResourceMonitor
from research_engine.ai.store import JOB_KIND as AI_KIND
from research_engine.contacts.store import JOB_KIND as CONTACTS_KIND
from research_engine.domain import (
    AiStartRequest,
    AiTaskType,
    AuditAction,
    BulkMode,
    ChildJob,
    ContactStartRequest,
    CrawlStartRequest,
    DeadLetter,
    DeadLetterAction,
    DeadLetterRequest,
    DeadLetterStatus,
    DiscoveryControlRequest,
    DiscoveryStartRequest,
    EntityType,
    EventCleanupRequest,
    EventCleanupResult,
    EventLevel,
    Job,
    JobCheckpoint,
    JobEvent,
    JobEventItem,
    JobEventPage,
    JobEventQuery,
    JobStatus,
    JobTask,
    JsonObject,
    LatinoStartRequest,
    LevelCount,
    PipelineAttempt,
    PipelineListQuery,
    PipelinePage,
    PipelineResource,
    PipelineRun,
    PipelineStage,
    PipelineStageView,
    PipelineStartRequest,
    ResolutionStartRequest,
    ResourceSlot,
    ResourceUsage,
    RunControlAction,
    RunEvent,
    ScoreRun,
    ScoringStartRequest,
    StageCounts,
    TaskAttempt,
    WebsiteStartRequest,
    campaign_crawl_settings,
    format_ts,
    new_id,
    utc_now,
)
from research_engine.latino.store import JOB_KIND as LATINO_KIND
from research_engine.leads import explorer, index
from research_engine.repository import CRAWL_JOB_KIND, DISCOVERY_JOB_KIND
from research_engine.resolution.store import JOB_KIND as RESOLUTION_KIND
from research_engine.scoring.store import JOB_KIND as SCORING_KIND
from research_engine.website import store as website_store

logger = logging.getLogger(__name__)

JOB_KIND: Final = "pipeline"
ACTOR: Final = "pipeline"
PIPELINE_VERSION: Final = 1
POLL_S: Final = 1.0
#: Stages that write to disk wait below this much free space.
MIN_FREE_DISK_MIB: Final = 1024
#: A stage waiting for resources or another pipeline logs it at most this often.
WAITING_EVENT_S: Final = 300.0
ENGINE_CALL_TIMEOUT_S: Final = 120.0
MAX_BACKOFF_S: Final = 60.0

#: Errors worth another attempt: the cause may be gone next time.
RETRYABLE_CODES: Final = frozenset(
    {"timeout", "rate_limited", "network", "store_unavailable", "store_busy", "worker_interrupted"}
)
_OPEN: Final = (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.PAUSED)
_DONE: Final = (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED)
_R = PipelineResource
_S = PipelineStage


@dataclass(frozen=True, slots=True)
class StageSpec:
    stage: PipelineStage
    version: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    depends_on: tuple[PipelineStage, ...]
    max_attempts: int
    retryable: frozenset[str]
    resources: tuple[PipelineResource, ...]
    #: The engine job kind that does the work; None when the orchestrator does it itself.
    job_kind: str | None


_NET: Final = RETRYABLE_CODES
_STORE: Final = frozenset({"store_unavailable", "store_busy", "worker_interrupted", "timeout"})
PIPELINE: Final[tuple[StageSpec, ...]] = (
    StageSpec(
        _S.DISCOVERY,
        "1",
        ("campaign",),
        ("discovery_candidates",),
        (),
        3,
        _NET,
        (_R.HTTP,),
        DISCOVERY_JOB_KIND,
    ),
    StageSpec(
        _S.ACQUISITION,
        "1",
        ("discovery_candidates",),
        ("raw_pages",),
        (_S.DISCOVERY,),
        3,
        _NET,
        (_R.HTTP, _R.BROWSER, _R.DISK),
        CRAWL_JOB_KIND,
    ),
    StageSpec(
        _S.NORMALIZATION,
        "1",
        ("discovery_candidates", "raw_pages"),
        ("normalized_records",),
        (_S.ACQUISITION,),
        2,
        _STORE,
        (_R.CPU, _R.RAM),
        RESOLUTION_KIND,
    ),
    StageSpec(
        _S.ENTITY_RESOLUTION,
        "1",
        ("normalized_records",),
        ("businesses", "match_candidates"),
        (_S.NORMALIZATION,),
        2,
        _STORE,
        (_R.CPU, _R.RAM, _R.DISK),
        RESOLUTION_KIND,
    ),
    StageSpec(
        _S.LATINO_EVIDENCE,
        "1",
        ("businesses", "raw_pages"),
        ("latino_assessments",),
        (_S.ENTITY_RESOLUTION,),
        2,
        _STORE,
        (_R.CPU,),
        LATINO_KIND,
    ),
    StageSpec(
        _S.WEBSITE_INTELLIGENCE,
        "1",
        ("raw_pages",),
        ("website_audits",),
        (_S.ENTITY_RESOLUTION,),
        2,
        _STORE,
        (_R.CPU, _R.DISK),
        website_store.JOB_KIND,
    ),
    StageSpec(
        _S.CONTACT_INTELLIGENCE,
        "1",
        ("businesses", "raw_pages"),
        ("contact_channels",),
        (_S.ENTITY_RESOLUTION,),
        3,
        _NET,
        (_R.HTTP, _R.CPU),
        CONTACTS_KIND,
    ),
    StageSpec(
        _S.LOCAL_AI,
        "1",
        ("businesses", "raw_pages", "website_audits"),
        ("inferences",),
        (_S.LATINO_EVIDENCE, _S.WEBSITE_INTELLIGENCE, _S.CONTACT_INTELLIGENCE),
        2,
        _STORE | {"out_of_memory"},
        (_R.GPU, _R.RAM),
        AI_KIND,
    ),
    StageSpec(
        _S.SCORING,
        "1",
        ("businesses", "latino_assessments", "website_audits", "contact_channels", "inferences"),
        ("score_run", "lead_index"),
        (_S.LOCAL_AI,),
        2,
        _STORE,
        (_R.CPU,),
        SCORING_KIND,
    ),
    StageSpec(
        _S.EXPORT_ELIGIBILITY,
        "1",
        ("score_run", "lead_index"),
        ("eligible_leads", "dataset_snapshot"),
        (_S.SCORING,),
        2,
        _STORE,
        (_R.DISK,),
        None,
    ),
)
SPECS: Final = {spec.stage: spec for spec in PIPELINE}
KINDS: Final = frozenset(spec.job_kind for spec in PIPELINE if spec.job_kind)


def default_limits() -> dict[PipelineResource, int]:
    """How many pipeline stages may use each resource at once, across projects."""
    return {
        _R.HTTP: 2,
        _R.BROWSER: 1,
        _R.CPU: max(1, (os.cpu_count() or 2) // 2),
        _R.RAM: 2,
        _R.DISK: 1,
        _R.GPU: 1,
    }


class OperationsError(ValueError):
    """A pipeline request the store cannot serve as asked."""


class StageFailure(Exception):
    """Why an attempt failed, and whether trying again could help."""

    def __init__(self, code: str, message: str, retryable: bool) -> None:
        super().__init__(message)
        self.code, self.message, self.retryable = code, message[:4000] or code, retryable


def classify(exc: BaseException) -> StageFailure:
    """Recoverable causes (a busy or missing store, a timeout, the network) retry; a refusal by an
    engine (its input or configuration is wrong) and anything unexpected need a person."""
    if isinstance(exc, StageFailure):
        return exc
    if isinstance(exc, repository.StoreUnavailableError):
        return StageFailure("store_unavailable", str(exc), True)
    if isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc):
        return StageFailure("store_busy", str(exc), True)
    if isinstance(exc, TimeoutError):
        return StageFailure("timeout", str(exc) or "an engine did not answer in time", True)
    if isinstance(exc, ConnectionError):
        return StageFailure("network", str(exc), True)
    if isinstance(exc, (ValueError, LookupError)):
        return StageFailure("configuration", str(exc), False)
    return StageFailure("internal", f"{type(exc).__name__}: {exc}", False)


@dataclass(frozen=True, slots=True)
class StageTarget:
    project_id: str
    campaign_id: str | None
    check_mail_domains: bool
    icp_profile_version_id: str | None = None
    scoring_model_version_id: str | None = None


class Engines(Protocol):
    """The engine services a pipeline drives."""

    async def start(self, stage: PipelineStage, target: StageTarget) -> list[str]:
        """Starts the stage's engine job over the target; returns the job ids."""
        ...

    async def control(self, kind: str, job_id: str, action: RunControlAction) -> None: ...


# ---- planning -----------------------------------------------------------------------------------


def plan_pipeline(
    conn: sqlite3.Connection, request: PipelineStartRequest, *, actor: str, request_id: str | None
) -> tuple[str, bool]:
    """(job id, created). A key already used returns its pipeline, created or not."""
    key = f"pipeline:{request.idempotency_key}"
    existing = conn.execute("SELECT id FROM jobs WHERE idempotency_key = ?", (key,)).fetchone()
    if existing is not None:
        return str(existing[0]), False
    project = conn.execute(
        "SELECT deleted_at FROM projects WHERE id = ?", (request.project_id,)
    ).fetchone()
    if project is None or project[0] is not None:
        raise repository.NotFoundError(f"project {request.project_id} not found")
    if request.campaign_id is not None:
        campaign = conn.execute(
            "SELECT project_id, deleted_at FROM campaigns WHERE id = ?", (request.campaign_id,)
        ).fetchone()
        if campaign is None or campaign[1] is not None:
            raise repository.NotFoundError(f"campaign {request.campaign_id} not found")
        if campaign[0] != request.project_id:
            raise OperationsError("the campaign belongs to another project")
    now = utc_now()
    job = Job(
        id=new_id(),
        project_id=request.project_id,
        kind=JOB_KIND,
        status=JobStatus.QUEUED,
        priority=request.priority,
        params_json={
            "project_id": request.project_id,
            "campaign_id": request.campaign_id,
            "check_mail_domains": request.check_mail_domains,
            "pipeline_version": PIPELINE_VERSION,
        },
        idempotency_key=key,
        progress_done=0,
        progress_total=len(PIPELINE),
        requested_at=now,
        started_at=None,
        finished_at=None,
        created_at=now,
        updated_at=now,
    )
    with repository.atomic(conn):
        repository.insert(conn, job)
        for ordinal, spec in enumerate(PIPELINE):
            conn.execute(
                "INSERT INTO pipeline_stage_specs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    job.id,
                    spec.stage.value,
                    ordinal,
                    spec.version,
                    _json(list(spec.inputs)),
                    _json(list(spec.outputs)),
                    _json(
                        {
                            "max_attempts": spec.max_attempts,
                            "retryable_codes": sorted(spec.retryable),
                        }
                    ),
                    _json([resource.value for resource in spec.resources]),
                ),
            )
            repository.insert(
                conn,
                JobTask(
                    id=new_id(),
                    job_id=job.id,
                    stage=spec.stage.value,
                    task_key=spec.stage.value,
                    status=JobStatus.QUEUED,
                    attempt_count=0,
                    max_attempts=spec.max_attempts,
                    created_at=now,
                    updated_at=now,
                ),
            )
        for spec in PIPELINE:
            conn.executemany(
                "INSERT INTO pipeline_stage_dependencies VALUES (?, ?, ?)",
                [(job.id, spec.stage.value, dependency.value) for dependency in spec.depends_on],
            )
        detail: JsonObject = {"stages": len(PIPELINE), "campaign_id": request.campaign_id}
        if request_id is not None:
            detail["request_id"] = request_id
        jobs.write_event(conn, job.id, None, "pipeline_queued", EventLevel.INFO, detail)
        repository.audit(
            conn,
            AuditAction.CREATE,
            EntityType.JOB,
            job.id,
            actor=actor,
            detail={"kind": JOB_KIND, "project_id": request.project_id},
        )
    return job.id, True


def _json(value: object) -> str:
    return orjson.dumps(value).decode()


def _task_of(conn: sqlite3.Connection, job_id: str, stage: PipelineStage) -> JobTask:
    row = conn.execute(
        f"SELECT {', '.join(JobTask.model_fields)} FROM job_tasks WHERE job_id = ? AND stage = ?",  # noqa: S608
        (job_id, stage.value),
    ).fetchone()
    if row is None:
        raise repository.NotFoundError(f"pipeline {job_id} has no stage {stage}")
    return repository.from_row(JobTask, row)


def _latest_attempt(conn: sqlite3.Connection, task_id: str) -> TaskAttempt | None:
    row = conn.execute(
        f"SELECT {', '.join(TaskAttempt.model_fields)} FROM task_attempts WHERE task_id = ? "  # noqa: S608
        "ORDER BY attempt DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    return None if row is None else repository.from_row(TaskAttempt, row)


def attached_jobs(conn: sqlite3.Connection, attempt_id: str) -> list[tuple[str, str, JobStatus]]:
    """(job id, kind, status) of the engine jobs an attempt started or attached, oldest first."""
    return [
        (row["id"], row["kind"], JobStatus(row["status"]))
        for row in conn.execute(
            "SELECT j.id, j.kind, j.status FROM task_attempt_jobs a JOIN jobs j ON j.id = a.job_id "
            "WHERE a.attempt_id = ? ORDER BY a.created_at, j.id",
            (attempt_id,),
        )
    ]


def claim_next_task(
    conn: sqlite3.Connection,
    job_id: str,
    limits: dict[PipelineResource, int],
    pressure: Sequence[str] = (),
) -> tuple[JobTask, TaskAttempt] | str | None:
    """The next stage of this pipeline, claimed atomically; a reason code when it must wait (for
    another pipeline of the project, a resource slot or pressure), None when nothing can run."""
    with repository.atomic(conn):
        job = repository.get(conn, Job, job_id)
        if job.status is not JobStatus.RUNNING:
            return None
        row = conn.execute(
            "SELECT t.id, s.resources_json FROM job_tasks t JOIN pipeline_stage_specs s "
            "ON s.job_id = t.job_id AND s.stage = t.stage "
            "WHERE t.job_id = ? AND t.status = 'queued' "
            "AND NOT EXISTS (SELECT 1 FROM pipeline_stage_dependencies d JOIN job_tasks p "
            "ON p.job_id = d.job_id AND p.stage = d.depends_on WHERE d.job_id = t.job_id "
            "AND d.stage = t.stage AND p.status <> 'succeeded') ORDER BY s.ordinal LIMIT 1",
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        # stages of one project write the same businesses: one at a time, whichever pipeline
        busy = conn.execute(
            "SELECT 1 FROM job_tasks t JOIN jobs j ON j.id = t.job_id WHERE j.kind = ? "
            "AND j.project_id = ? AND j.id <> ? AND t.status = 'running' LIMIT 1",
            (JOB_KIND, job.project_id, job_id),
        ).fetchone()
        if busy is not None:
            return "project_busy"
        needs = [PipelineResource(value) for value in orjson.loads(row["resources_json"])]
        held = set(pressure)
        if "low_ram" in held and {_R.RAM, _R.BROWSER} & set(needs):
            return "low_ram"
        if "low_disk" in held and _R.DISK in needs:
            return "low_disk"
        for resource, running in running_by_resource(conn).items():
            if resource in needs and running >= limits[resource]:
                return f"slot_{resource.value}"
        task = repository.get(conn, JobTask, str(row["id"]))
        now = utc_now()
        repository.change_status(conn, JobTask, task.id, JobStatus.RUNNING, actor=ACTOR)
        conn.execute(
            "UPDATE job_tasks SET attempt_count = attempt_count + 1, updated_at = ? WHERE id = ?",
            (format_ts(now), task.id),
        )
        attempt = TaskAttempt(
            id=new_id(),
            task_id=task.id,
            attempt=task.attempt_count + 1,
            status=JobStatus.RUNNING,
            started_at=now,
            finished_at=None,
            error_code=None,
            error_message=None,
            retryable=None,
        )
        repository.insert(conn, attempt)
        jobs.write_event(
            conn,
            job_id,
            task.id,
            "stage_started",
            EventLevel.INFO,
            {"stage": task.stage, "attempt": attempt.attempt},
        )
    return repository.get(conn, JobTask, task.id), attempt


def running_by_resource(conn: sqlite3.Connection) -> dict[PipelineResource, int]:
    counts = dict.fromkeys(PipelineResource, 0)
    for row in conn.execute(
        "SELECT s.resources_json FROM job_tasks t JOIN pipeline_stage_specs s "
        "ON s.job_id = t.job_id AND s.stage = t.stage WHERE t.status = 'running'"
    ):
        for value in orjson.loads(row[0]):
            counts[PipelineResource(value)] += 1
    return counts


def _progress(conn: sqlite3.Connection, job_id: str) -> None:
    (done,) = conn.execute(
        "SELECT count(*) FROM job_tasks WHERE job_id = ? AND status = 'succeeded'", (job_id,)
    ).fetchone()
    conn.execute(
        "UPDATE jobs SET progress_done = ?, updated_at = ? WHERE id = ?",
        (done, format_ts(utc_now()), job_id),
    )


def finish_task(conn: sqlite3.Connection, task_id: str, state: JsonObject) -> None:
    """The attempt succeeded (or found nothing to do): its outputs are recorded as a checkpoint."""
    task = repository.get(conn, JobTask, task_id)
    attempt = _latest_attempt(conn, task_id)
    now = utc_now()
    with repository.atomic(conn):
        if attempt is not None and attempt.status is JobStatus.RUNNING:
            repository.change_status(
                conn, TaskAttempt, attempt.id, JobStatus.SUCCEEDED, actor=ACTOR
            )
            conn.execute(
                "UPDATE task_attempts SET finished_at = ? WHERE id = ?",
                (format_ts(now), attempt.id),
            )
        repository.change_status(conn, JobTask, task_id, JobStatus.SUCCEEDED, actor=ACTOR)
        repository.insert(
            conn,
            JobCheckpoint(
                id=new_id(), job_id=task.job_id, task_id=task_id, state_json=state, created_at=now
            ),
        )
        _progress(conn, task.job_id)
        skipped = state.get("skipped")
        jobs.write_event(
            conn,
            task.job_id,
            task_id,
            "stage_skipped" if skipped else "stage_succeeded",
            EventLevel.INFO,
            {"stage": task.stage, **({"reason": skipped} if skipped else {})},
        )


def fail_task(conn: sqlite3.Connection, task_id: str, failure: StageFailure) -> bool:
    """Records the failed attempt. True when the stage is queued again; otherwise a dead letter
    holds it and the pipeline fails until a person retries or skips the stage."""
    task = repository.get(conn, JobTask, task_id)
    (retry_json,) = conn.execute(
        "SELECT retry_json FROM pipeline_stage_specs WHERE job_id = ? AND stage = ?",
        (task.job_id, task.stage),
    ).fetchone()
    policy = orjson.loads(retry_json)
    retry = (
        failure.retryable
        and failure.code in policy["retryable_codes"]
        and task.attempt_count < task.max_attempts
    )
    attempt = _latest_attempt(conn, task_id)
    now = format_ts(utc_now())
    with repository.atomic(conn):
        if attempt is not None and attempt.status in (JobStatus.RUNNING, JobStatus.PAUSED):
            if attempt.status is JobStatus.PAUSED:
                repository.change_status(
                    conn, TaskAttempt, attempt.id, JobStatus.RUNNING, actor=ACTOR
                )
            repository.change_status(conn, TaskAttempt, attempt.id, JobStatus.FAILED, actor=ACTOR)
            conn.execute(
                "UPDATE task_attempts SET finished_at = ?, error_code = ?, error_message = ?, "
                "retryable = ? WHERE id = ?",
                (now, failure.code, failure.message, int(retry), attempt.id),
            )
        if task.status is JobStatus.PAUSED:
            repository.change_status(conn, JobTask, task_id, JobStatus.RUNNING, actor=ACTOR)
        repository.change_status(conn, JobTask, task_id, JobStatus.FAILED, actor=ACTOR)
        jobs.write_event(
            conn,
            task.job_id,
            task_id,
            "stage_failed",
            EventLevel.ERROR,
            {"stage": task.stage, "attempt": task.attempt_count, "message": failure.message},
            failure.code,
            retry,
        )
        if retry:
            repository.change_status(conn, JobTask, task_id, JobStatus.QUEUED, actor=ACTOR)
            jobs.write_event(
                conn,
                task.job_id,
                task_id,
                "stage_retry_scheduled",
                EventLevel.WARNING,
                {"stage": task.stage},
            )
            return True
        conn.execute(
            "INSERT INTO dead_letter_tasks (id, task_id, job_id, attempt, error_code, "
            "error_message, "
            "retryable, review_status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?)",
            (
                new_id(),
                task_id,
                task.job_id,
                max(1, task.attempt_count),
                failure.code,
                failure.message,
                int(failure.retryable),
                now,
            ),
        )
        job = repository.get(conn, Job, task.job_id)
        if job.status is JobStatus.PAUSED:
            repository.change_status(conn, Job, job.id, JobStatus.RUNNING, actor=ACTOR)
        if repository.get(conn, Job, task.job_id).status is JobStatus.RUNNING:
            repository.change_status(conn, Job, job.id, JobStatus.FAILED, actor=ACTOR)
            conn.execute(
                "UPDATE jobs SET finished_at = ?, updated_at = ? WHERE id = ?", (now, now, job.id)
            )
        jobs.write_event(
            conn,
            task.job_id,
            None,
            "pipeline_failed",
            EventLevel.ERROR,
            {"stage": task.stage, "message": failure.message},
            failure.code,
            False,
        )
    return False


def resolve_dead_letter(
    conn: sqlite3.Connection, request: DeadLetterRequest, *, actor: str
) -> None:
    """Retry: the stage is queued as a new attempt. Skip: the stage counts as done, with nothing
    produced. Either way the failed pipeline is queued again; the dead letter keeps its failure."""
    job = jobs.live_job(conn, request.job_id, JOB_KIND)
    row = conn.execute(
        "SELECT id FROM dead_letter_tasks WHERE job_id = ? AND task_id = ? "
        "AND review_status = 'open'",
        (request.job_id, request.task_id),
    ).fetchone()
    if row is None:
        raise repository.NotFoundError("this stage has no open dead letter")
    task = repository.get(conn, JobTask, request.task_id)
    now = format_ts(utc_now())
    status = (
        DeadLetterStatus.RETRIED
        if request.action is DeadLetterAction.RETRY
        else DeadLetterStatus.SKIPPED
    )
    with repository.atomic(conn):
        conn.execute(
            "UPDATE dead_letter_tasks SET review_status = ?, resolved_at = ?, resolved_by = ?, "
            "resolution_reason = ? WHERE id = ?",
            (status.value, now, actor, request.reason, row["id"]),
        )
        if task.status is JobStatus.FAILED:
            repository.change_status(conn, JobTask, task.id, JobStatus.QUEUED, actor=actor)
        if request.action is DeadLetterAction.SKIP:
            repository.change_status(conn, JobTask, task.id, JobStatus.RUNNING, actor=actor)
            repository.change_status(conn, JobTask, task.id, JobStatus.SUCCEEDED, actor=actor)
            repository.insert(
                conn,
                JobCheckpoint(
                    id=new_id(),
                    job_id=job.id,
                    task_id=task.id,
                    state_json={"skipped": "skipped_by_person", "reason": request.reason},
                    created_at=utc_now(),
                ),
            )
            _progress(conn, job.id)
        else:
            conn.execute(
                "UPDATE job_tasks SET max_attempts = max(max_attempts, attempt_count + 1) "
                "WHERE id = ?",
                (task.id,),
            )
        if job.status is JobStatus.FAILED:
            repository.change_status(conn, Job, job.id, JobStatus.QUEUED, actor=actor)
            conn.execute(
                "UPDATE jobs SET finished_at = NULL, updated_at = ? WHERE id = ?", (now, job.id)
            )
        jobs.write_event(
            conn,
            job.id,
            task.id,
            f"dead_letter_{status.value}",
            EventLevel.INFO,
            {"stage": task.stage, "reason": request.reason, "by": actor},
        )


def settle_cancelled(conn: sqlite3.Connection, job_id: str) -> list[tuple[str, str]]:
    """A cancelled pipeline's open stages and attempts are cancelled too; returns the (kind, id)
    of its engine jobs still open, for the caller to cancel through their engines."""
    now = format_ts(utc_now())
    children: list[tuple[str, str]] = []
    with repository.atomic(conn):
        for row in conn.execute(
            "SELECT id, status FROM job_tasks WHERE job_id = ? AND status IN ('queued', 'running', "
            "'paused')",
            (job_id,),
        ).fetchall():
            repository.change_status(conn, JobTask, row["id"], JobStatus.CANCELLED, actor=ACTOR)
        for row in conn.execute(
            "SELECT a.id FROM task_attempts a JOIN job_tasks t ON t.id = a.task_id "
            "WHERE t.job_id = ? AND a.status IN ('running', 'paused')",
            (job_id,),
        ).fetchall():
            repository.change_status(conn, TaskAttempt, row["id"], JobStatus.CANCELLED, actor=ACTOR)
            conn.execute("UPDATE task_attempts SET finished_at = ? WHERE id = ?", (now, row["id"]))
        conn.execute(
            "UPDATE jobs SET finished_at = coalesce(finished_at, ?), started_at = "
            "coalesce(started_at, ?), updated_at = ? WHERE id = ?",
            (now, now, now, job_id),
        )
        children = [
            (row["kind"], row["id"])
            for row in conn.execute(
                "SELECT DISTINCT j.id, j.kind FROM task_attempt_jobs x "
                "JOIN task_attempts a ON a.id = x.attempt_id JOIN job_tasks t ON t.id = a.task_id "
                "JOIN jobs j ON j.id = x.job_id WHERE t.job_id = ? "
                "AND j.status IN ('queued', 'running', 'paused')",
                (job_id,),
            )
        ]
        jobs.write_event(
            conn,
            job_id,
            None,
            "pipeline_cancelled",
            EventLevel.INFO,
            {"engine_jobs": len(children)},
        )
    return children


# ---- what a stage needs -------------------------------------------------------------------------


def _count(conn: sqlite3.Connection, sql: str, *params: object) -> int:
    return int(conn.execute(sql, params).fetchone()[0])


def _has_businesses(conn: sqlite3.Connection, project_id: str) -> bool:
    return bool(
        _count(
            conn,
            "SELECT count(*) FROM (SELECT 1 FROM businesses WHERE project_id = ? "
            "AND deleted_at IS NULL LIMIT 1)",
            project_id,
        )
    )


def active_versions(conn: sqlite3.Connection, project_id: str) -> tuple[str, str] | None:
    """The active ICP version of the project and the active scoring version most recently set."""
    icp = conn.execute(
        "SELECT active_version_id FROM icp_profiles WHERE project_id = ? AND deleted_at IS NULL "
        "AND archived_at IS NULL AND active_version_id IS NOT NULL "
        "ORDER BY updated_at DESC, id DESC "
        "LIMIT 1",
        (project_id,),
    ).fetchone()
    model = conn.execute(
        "SELECT active_version_id FROM scoring_models WHERE deleted_at IS NULL "
        "AND archived_at IS NULL AND active_version_id IS NOT NULL "
        "ORDER BY updated_at DESC, id DESC "
        "LIMIT 1"
    ).fetchone()
    return None if icp is None or model is None else (str(icp[0]), str(model[0]))


def skip_reason(conn: sqlite3.Connection, stage: PipelineStage, target: StageTarget) -> str | None:
    """Why a stage has nothing to do over this project, or None when it has work."""
    project = target.project_id
    match stage:
        case _S.DISCOVERY | _S.ACQUISITION if target.campaign_id is None:
            return "no_campaign"
        case _S.NORMALIZATION if not _has_businesses(conn, project) and not _count(
            conn,
            "SELECT count(*) FROM (SELECT 1 FROM discovery_candidates WHERE project_id = ? "
            "LIMIT 1)",
            project,
        ):
            return "nothing_to_resolve"
        case _S.LATINO_EVIDENCE | _S.CONTACT_INTELLIGENCE | _S.LOCAL_AI | _S.SCORING if (
            not _has_businesses(conn, project)
        ):
            return "no_businesses"
        case _S.WEBSITE_INTELLIGENCE if not website_store.crawled_sites(conn, project):
            return "no_crawled_sites"
    return None


def open_jobs(conn: sqlite3.Connection, kind: str, target: StageTarget) -> list[str]:
    """Engine jobs of this kind already open for the project (and campaign): attached, not
    started again. AI jobs are left to the AI engine, which lists an identical open request."""
    if kind == AI_KIND:
        return []
    sql = (
        "SELECT id FROM jobs WHERE kind = ? AND project_id = ? "
        "AND status IN ('queued', 'running', 'paused')"
    )
    params: list[object] = [kind, target.project_id]
    if kind in (DISCOVERY_JOB_KIND, CRAWL_JOB_KIND):
        sql += " AND json_extract(params_json, '$.campaign_id') IS ?"
        params.append(target.campaign_id)
    return [str(row[0]) for row in conn.execute(sql + " ORDER BY requested_at, id", params)]


def attach(conn: sqlite3.Connection, attempt_id: str, job_ids: Sequence[str], reused: bool) -> None:
    now = format_ts(utc_now())
    with repository.atomic(conn):
        conn.executemany(
            "INSERT OR IGNORE INTO task_attempt_jobs VALUES (?, ?, ?, ?)",
            [(attempt_id, job_id, int(reused), now) for job_id in job_ids],
        )


def child_failure(
    conn: sqlite3.Connection, job_id: str, kind: str, status: JobStatus
) -> StageFailure:
    if status is JobStatus.CANCELLED:
        return StageFailure(
            "engine_job_cancelled",
            f"the {kind} job {job_id} was cancelled outside the pipeline",
            False,
        )
    row = conn.execute(
        "SELECT error_code, retryable, detail_json FROM job_events WHERE job_id = ? "
        "AND level = 'error' ORDER BY created_at DESC, id DESC LIMIT 1",
        (job_id,),
    ).fetchone()
    code = str(row["error_code"]) if row and row["error_code"] else "engine_job_failed"
    detail = orjson.loads(row["detail_json"]) if row and row["detail_json"] else {}
    message = detail.get("message") if isinstance(detail, dict) else None
    retryable = bool(row and row["retryable"]) and code in RETRYABLE_CODES
    return StageFailure(code, str(message or f"the {kind} job {job_id} failed"), retryable)


def eligibility(
    conn: sqlite3.Connection, data_dir: Path, job: Job, attempt_jobs: Sequence[str]
) -> JsonObject:
    """The last stage: the leads eligible to export from the run the pipeline scored (priority or
    review, off the do-not-contact list), and a dataset snapshot of the project with that run."""
    project_id = job.project_id
    row = None
    if attempt_jobs:
        row = conn.execute(
            f"SELECT id FROM score_runs WHERE job_id IN ({', '.join('?' * len(attempt_jobs))}) "  # noqa: S608
            "AND finished_at IS NOT NULL ORDER BY finished_at DESC, id DESC LIMIT 1",
            list(attempt_jobs),
        ).fetchone()
    run_id = str(row[0]) if row else datasets.newest_finished_run(conn, project_id)
    if run_id is None:
        return {"skipped": "no_score_run"}
    run = repository.get(conn, ScoreRun, run_id)
    index.ensure_indexed(conn, run)
    suppressed = explorer.suppressed_roots(conn)
    eligible = sum(
        1
        for (business_id,) in conn.execute(
            "SELECT business_id FROM lead_index WHERE score_run_id = ? "
            "AND tier IN ('priority', 'review')",
            (run.id,),
        )
        if business_id not in suppressed
    )
    names = conn.execute(
        "SELECT p.name, c.name FROM projects p LEFT JOIN campaigns c ON c.id = ? WHERE p.id = ?",
        (job.params_json.get("campaign_id"), project_id),
    ).fetchone()
    label = names[1] or names[0]
    versions = {f"stage.{spec.stage.value}": spec.version for spec in PIPELINE}
    snapshot = datasets.snapshot_businesses(
        conn,
        data_dir,
        project_id=project_id,
        dataset_name=f"Pipeline: {label}"[:300],
        actor=ACTOR,
        description="Written by the export eligibility stage of a pipeline",
        score_run_id=run.id,
        pipeline_versions=versions | {"pipeline": str(PIPELINE_VERSION)},
    )
    return {"score_run_id": run.id, "eligible_leads": eligible, "snapshot_id": snapshot.id}


# ---- the service --------------------------------------------------------------------------------


class PipelineService(jobs.BackgroundJobs[PipelineRun, PipelinePage]):
    """Runs pipelines on threads (one per pipeline), driving the engines on the worker's loop."""

    kind = JOB_KIND
    actor = ACTOR

    def __init__(
        self,
        data_dir: Path | None,
        connect: Callable[[], sqlite3.Connection],
        engines: Engines,
        monitor: ResourceMonitor,
        *,
        browsers: Callable[[], int | None] = lambda: None,
        limits: dict[PipelineResource, int] | None = None,
        poll_s: float = POLL_S,
    ) -> None:
        super().__init__(connect)
        self._data_dir = data_dir
        self._engines = engines
        self._monitor = monitor
        self._browsers = browsers
        self.limits = limits or default_limits()
        self._poll_s = poll_s
        self._loop: asyncio.AbstractEventLoop | None = None
        self._waiting: dict[str, float] = {}

    def _bind(self) -> None:
        self._loop = asyncio.get_running_loop()

    # -- requests

    async def start(
        self, request: PipelineStartRequest, *, actor: str, request_id: str | None
    ) -> PipelineRun:
        self._bind()
        job_id, created = await self._in_store(
            lambda conn: plan_pipeline(conn, request, actor=actor, request_id=request_id)
        )
        if created:
            self._spawn(job_id)
        return await self._in_store(lambda conn: self.view(conn, job_id))

    async def control(self, request: DiscoveryControlRequest) -> PipelineRun:
        self._bind()

        def apply(conn: sqlite3.Connection) -> None:
            jobs.live_job(conn, request.job_id, JOB_KIND)
            jobs.apply_control(conn, request.job_id, request.action, "ui")

        await self._in_store(apply)
        if request.action is RunControlAction.RESUME:
            self._spawn(request.job_id)
        else:
            await self._halt(request.job_id)
            if request.action is RunControlAction.CANCEL:
                children = await self._in_store(lambda conn: settle_cancelled(conn, request.job_id))
                for kind, child in children:
                    try:
                        await self._engines.control(kind, child, RunControlAction.CANCEL)
                    except Exception:
                        logger.warning(
                            "an engine job of a cancelled pipeline could not be cancelled",
                            exc_info=True,
                            extra={"job_id": child},
                        )
        return await self._in_store(lambda conn: self.view(conn, request.job_id))

    async def resolve(self, request: DeadLetterRequest, *, actor: str) -> PipelineRun:
        self._bind()
        await self._in_store(lambda conn: resolve_dead_letter(conn, request, actor=actor))
        self._spawn(request.job_id)
        return await self._in_store(lambda conn: self.view(conn, request.job_id))

    async def listing(self, query: PipelineListQuery) -> PipelinePage:
        page = await self._in_store(lambda conn: list_pipelines(conn, query, utc_now()))
        usage = await self._in_store(self.resources)
        return page.model_copy(update={"resources": usage})

    def view(self, conn: sqlite3.Connection, job_id: str) -> PipelineRun:
        return pipeline_view(conn, job_id, utc_now())

    def page(self, conn: sqlite3.Connection, job_ids: Sequence[str]) -> PipelinePage:
        items = [pipeline_view(conn, job_id, utc_now()) for job_id in job_ids]
        return PipelinePage(
            items=items, next_cursor=None, total=len(items), resources=self.resources(conn)
        )

    def plan(self, conn: sqlite3.Connection, project_id: str | None) -> tuple[list[str], list[str]]:
        raise OperationsError("a pipeline is started with its request")

    def pressure(self) -> list[str]:
        reasons = [
            reason
            for reason in self._monitor.pressure(self._monitor.sample())
            if reason == "low_ram"
        ]
        if self._data_dir is not None:
            with contextlib.suppress(OSError):
                if shutil.disk_usage(self._data_dir).free // (1024 * 1024) < MIN_FREE_DISK_MIB:
                    reasons.append("low_disk")
        return reasons

    def resources(self, conn: sqlite3.Connection) -> ResourceUsage:
        sample = self._monitor.sample()
        disk_total = disk_free = None
        if self._data_dir is not None:
            with contextlib.suppress(OSError):
                usage = shutil.disk_usage(self._data_dir)
                disk_total, disk_free = usage.total // (1024 * 1024), usage.free // (1024 * 1024)
        running = running_by_resource(conn)
        return ResourceUsage(
            sampled_at=utc_now(),
            cpu_count=os.cpu_count() or 1,
            cpu_percent=resources.cpu_percent(),
            ram_total_mib=sample.ram_total_mib,
            ram_free_mib=sample.ram_free_mib,
            vram_total_mib=sample.vram_total_mib,
            vram_free_mib=sample.vram_free_mib,
            disk_total_mib=disk_total,
            disk_free_mib=disk_free,
            active_browsers=self._browsers(),
            slots=[
                ResourceSlot(
                    resource=resource, running=running[resource], limit=self.limits[resource]
                )
                for resource in PipelineResource
            ],
            pressure=self.pressure()
            + [r for r in self._monitor.pressure(sample) if r == "low_vram"],
        )

    # -- the thread

    def _call[T](self, coroutine: Coroutine[Any, Any, T]) -> T:
        if self._loop is None:
            coroutine.close()
            raise StageFailure("worker_interrupted", "the pipeline lost its event loop", True)
        return asyncio.run_coroutine_threadsafe(coroutine, self._loop).result(ENGINE_CALL_TIMEOUT_S)

    def _sleep(self, should_stop: Callable[[], bool], seconds: float | None = None) -> None:
        deadline = time.monotonic() + (self._poll_s if seconds is None else seconds)
        while time.monotonic() < deadline:
            if should_stop():
                return
            time.sleep(min(0.25, self._poll_s))

    def run(self, conn: sqlite3.Connection, job_id: str, should_stop: Callable[[], bool]) -> bool:
        job = repository.get(conn, Job, job_id)
        if job.status not in (JobStatus.QUEUED, JobStatus.RUNNING):
            return False
        jobs.mark_running(conn, job_id, ACTOR)
        # what a pause, a restart or a crash left in flight goes first, with the jobs it started
        for row in conn.execute(
            "SELECT t.id FROM job_tasks t JOIN pipeline_stage_specs s ON s.job_id = t.job_id "
            "AND s.stage = t.stage WHERE t.job_id = ? AND t.status IN ('running', 'paused') "
            "ORDER BY s.ordinal",
            (job_id,),
        ).fetchall():
            if not self._run_stage(
                conn, job, repository.get(conn, JobTask, row["id"]), should_stop
            ):
                return False
        while not should_stop():
            claimed = claim_next_task(conn, job_id, self.limits, self.pressure())
            if isinstance(claimed, tuple):
                if not self._run_stage(conn, job, claimed[0], should_stop):
                    return False
                continue
            if claimed is None and self._settle(conn, job_id):
                return True
            if isinstance(claimed, str):
                self._note_waiting(conn, job_id, claimed)
            self._sleep(should_stop)
        return False

    def _settle(self, conn: sqlite3.Connection, job_id: str) -> bool:
        """True when the pipeline is over: every stage done, or failed until a person acts."""
        job = repository.get(conn, Job, job_id)
        if job.status is not JobStatus.RUNNING:
            return True
        (left,) = conn.execute(
            "SELECT count(*) FROM job_tasks WHERE job_id = ? AND status <> 'succeeded'", (job_id,)
        ).fetchone()
        if left:
            return False
        now = format_ts(utc_now())
        with repository.atomic(conn):
            repository.change_status(conn, Job, job_id, JobStatus.SUCCEEDED, actor=ACTOR)
            conn.execute(
                "UPDATE jobs SET finished_at = ?, updated_at = ? WHERE id = ?", (now, now, job_id)
            )
            jobs.write_event(conn, job_id, None, "pipeline_succeeded", EventLevel.INFO)
        return True

    def _note_waiting(self, conn: sqlite3.Connection, job_id: str, reason: str) -> None:
        now = time.monotonic()
        if now - self._waiting.get(job_id, -WAITING_EVENT_S) < WAITING_EVENT_S:
            return
        self._waiting[job_id] = now
        with repository.atomic(conn):
            jobs.write_event(
                conn, job_id, None, "pipeline_waiting", EventLevel.INFO, {"reason": reason}
            )

    def _target(self, conn: sqlite3.Connection, job: Job, stage: PipelineStage) -> StageTarget:
        target = StageTarget(
            project_id=job.project_id,
            campaign_id=str(job.params_json["campaign_id"])
            if job.params_json.get("campaign_id")
            else None,
            check_mail_domains=bool(job.params_json.get("check_mail_domains")),
        )
        if stage is _S.SCORING:
            versions = active_versions(conn, job.project_id)
            if versions is None:
                raise StageFailure(
                    "configuration",
                    "no active ICP version for this project or no active scoring version: "
                    "activate them in ICP Builder and Scoring Models",
                    False,
                )
            target = replace(
                target, icp_profile_version_id=versions[0], scoring_model_version_id=versions[1]
            )
        return target

    def _run_stage(
        self, conn: sqlite3.Connection, job: Job, task: JobTask, should_stop: Callable[[], bool]
    ) -> bool:
        """Runs one claimed or resumed stage to its end. False when the pipeline was stopped."""
        stage = PipelineStage(task.stage)
        spec = SPECS[stage]
        attempt = _latest_attempt(conn, task.id)
        if attempt is None:
            return True
        try:
            with repository.atomic(conn):
                if task.status is JobStatus.PAUSED:
                    repository.change_status(conn, JobTask, task.id, JobStatus.RUNNING, actor=ACTOR)
                if attempt.status is JobStatus.PAUSED:
                    repository.change_status(
                        conn, TaskAttempt, attempt.id, JobStatus.RUNNING, actor=ACTOR
                    )
            children = attached_jobs(conn, attempt.id)
            if not children:
                target = self._target(conn, job, stage)
                if (reason := skip_reason(conn, stage, target)) is not None:
                    finish_task(conn, task.id, {"skipped": reason})
                    return True
                if stage is _S.EXPORT_ELIGIBILITY:
                    if self._data_dir is None:
                        raise StageFailure(
                            "configuration", "the data directory is not configured", False
                        )
                    scoring = _task_of(conn, job.id, _S.SCORING)
                    scored = _latest_attempt(conn, scoring.id)
                    jobs_scored = (
                        [child for child, _, _ in attached_jobs(conn, scored.id)] if scored else []
                    )
                    finish_task(conn, task.id, eligibility(conn, self._data_dir, job, jobs_scored))
                    return True
                self._attach_or_start(conn, job, task, attempt.id, stage, target)
                children = attached_jobs(conn, attempt.id)
                if not children:
                    finish_task(conn, task.id, {"skipped": "nothing_to_do"})
                    return True
            for child, kind, status in children:
                if status is JobStatus.PAUSED:
                    self._call(self._engines.control(kind, child, RunControlAction.RESUME))
            done = self._wait(conn, stage, [child for child, _, _ in children], should_stop)
            if not done:
                self._stopped(conn, job.id, task, attempt.id, spec)
                return False
            finish_task(conn, task.id, {"jobs": [child for child, _, _ in children]})
            return True
        except Exception as exc:
            if should_stop() and repository.get(conn, Job, job.id).status is not JobStatus.RUNNING:
                return False
            failure = classify(exc)
            if failure.code == "internal":
                logger.exception("pipeline stage failed", extra={"job_id": job.id, "stage": stage})
            if fail_task(conn, task.id, failure):
                self._sleep(should_stop, min(MAX_BACKOFF_S, 2.0**attempt.attempt))
            return True

    def _attach_or_start(
        self,
        conn: sqlite3.Connection,
        job: Job,
        task: JobTask,
        attempt_id: str,
        stage: PipelineStage,
        target: StageTarget,
    ) -> None:
        spec = SPECS[stage]
        if spec.job_kind is None:
            raise OperationsError(f"{stage} runs no engine job")
        if stage is _S.ENTITY_RESOLUTION:
            # the resolution job that normalised the records resolves them too (phase 06 engine)
            normalizing = _task_of(conn, job.id, _S.NORMALIZATION)
            previous = _latest_attempt(conn, normalizing.id)
            found = [child for child, _, _ in attached_jobs(conn, previous.id)] if previous else []
            attach(conn, attempt_id, found or open_jobs(conn, spec.job_kind, target), reused=True)
            return
        found = open_jobs(conn, spec.job_kind, target)
        if found:
            attach(conn, attempt_id, found, reused=True)
            return
        started = self._call(self._engines.start(stage, target))
        attach(conn, attempt_id, started, reused=False)

    def _wait(
        self,
        conn: sqlite3.Connection,
        stage: PipelineStage,
        children: Sequence[str],
        should_stop: Callable[[], bool],
    ) -> bool:
        marks = ", ".join("?" * len(children))
        while True:
            if should_stop():
                return False
            rows = conn.execute(
                f"SELECT id, kind, status FROM jobs WHERE id IN ({marks})",  # noqa: S608
                list(children),
            ).fetchall()
            statuses = {row["id"]: (row["kind"], JobStatus(row["status"])) for row in rows}
            for child, (kind, status) in statuses.items():
                if status in (JobStatus.FAILED, JobStatus.CANCELLED):
                    raise child_failure(conn, child, kind, status)
            if all(status is JobStatus.SUCCEEDED for _, status in statuses.values()):
                return True
            if stage is _S.NORMALIZATION and all(
                status is JobStatus.SUCCEEDED
                or jobs.latest_state(conn, child).get("stage") not in (None, "ingest")
                for child, (_, status) in statuses.items()
            ):
                return True  # the records are normalised; entity resolution waits for the rest
            self._sleep(should_stop)

    def _stopped(
        self, conn: sqlite3.Connection, job_id: str, task: JobTask, attempt_id: str, spec: StageSpec
    ) -> None:
        """A pause reaches the engine jobs and the stage; a cancel is settled by `control`; a
        worker stopping leaves everything as it is, for the engines and recovery to pause."""
        if repository.get(conn, Job, job_id).status is not JobStatus.PAUSED:
            return
        for child, kind, status in attached_jobs(conn, attempt_id):
            if status is JobStatus.RUNNING:
                try:
                    self._call(self._engines.control(kind, child, RunControlAction.PAUSE))
                except Exception:
                    logger.warning(
                        "an engine job could not be paused", exc_info=True, extra={"job_id": child}
                    )
        with repository.atomic(conn):
            if repository.get(conn, JobTask, task.id).status is JobStatus.RUNNING:
                repository.change_status(conn, JobTask, task.id, JobStatus.PAUSED, actor=ACTOR)
            if repository.get(conn, TaskAttempt, attempt_id).status is JobStatus.RUNNING:
                repository.change_status(
                    conn, TaskAttempt, attempt_id, JobStatus.PAUSED, actor=ACTOR
                )
            jobs.write_event(
                conn, job_id, task.id, "stage_paused", EventLevel.INFO, {"stage": spec.stage.value}
            )


class ServiceEngines:
    """The worker's engine services, as a pipeline drives them."""

    def __init__(
        self,
        *,
        discovery: Any,
        crawl: Any,
        resolution: Any,
        latino: Any,
        website: Any,
        contacts: Any,
        ai: Any,
        scoring: Any,
    ) -> None:
        self._start = {
            _S.DISCOVERY: discovery,
            _S.ACQUISITION: crawl,
            _S.NORMALIZATION: resolution,
            _S.LATINO_EVIDENCE: latino,
            _S.WEBSITE_INTELLIGENCE: website,
            _S.CONTACT_INTELLIGENCE: contacts,
            _S.LOCAL_AI: ai,
            _S.SCORING: scoring,
        }
        self._by_kind = {
            DISCOVERY_JOB_KIND: discovery,
            CRAWL_JOB_KIND: crawl,
            RESOLUTION_KIND: resolution,
            LATINO_KIND: latino,
            website_store.JOB_KIND: website,
            CONTACTS_KIND: contacts,
            AI_KIND: ai,
            SCORING_KIND: scoring,
        }

    async def start(self, stage: PipelineStage, target: StageTarget) -> list[str]:
        service = self._start[stage]
        project = target.project_id
        match stage:
            case _S.DISCOVERY:
                if target.campaign_id is None:
                    raise OperationsError("discovery needs a campaign")
                run = await service.start(
                    DiscoveryStartRequest(
                        campaign_id=target.campaign_id, connector_keys=[], inputs={}
                    )
                )
                return [run.job_id]
            case _S.ACQUISITION:
                run = await service.start(
                    CrawlStartRequest(
                        project_id=None,
                        campaign_id=target.campaign_id,
                        urls=[],
                        settings=campaign_crawl_settings(),
                    )
                )
                return [run.job_id]
            case _S.NORMALIZATION:
                page = await service.start(ResolutionStartRequest(project_id=project))
            case _S.LATINO_EVIDENCE:
                page = await service.start(LatinoStartRequest(project_id=project))
            case _S.WEBSITE_INTELLIGENCE:
                page = await service.start(WebsiteStartRequest(project_id=project, sites=[]))
            case _S.CONTACT_INTELLIGENCE:
                page = await service.start(
                    ContactStartRequest(
                        project_id=project, check_mail_domains=target.check_mail_domains
                    )
                )
            case _S.LOCAL_AI:
                tasks = [task for task in AiTaskType if task is not AiTaskType.SALES_BRIEF]
                page = await service.start(
                    AiStartRequest(project_id=project, business_ids=[], task_types=tasks)
                )
            case _S.SCORING:
                if not (target.icp_profile_version_id and target.scoring_model_version_id):
                    raise OperationsError("scoring needs an ICP version and a scoring version")
                page = await service.start(
                    ScoringStartRequest(
                        project_id=project,
                        icp_profile_version_id=target.icp_profile_version_id,
                        scoring_model_version_id=target.scoring_model_version_id,
                        facts_from_run_id=None,
                    )
                )
            case _:
                raise OperationsError(f"{stage} runs no engine job")
        return [item.job_id for item in page.items if item.project_id == project]

    async def control(self, kind: str, job_id: str, action: RunControlAction) -> None:
        await self._by_kind[kind].control(DiscoveryControlRequest(job_id=job_id, action=action))


# ---- reading ------------------------------------------------------------------------------------


def _actions(status: JobStatus) -> list[RunControlAction]:
    return {
        JobStatus.QUEUED: [RunControlAction.CANCEL],
        JobStatus.RUNNING: [RunControlAction.PAUSE, RunControlAction.CANCEL],
        JobStatus.PAUSED: [RunControlAction.RESUME, RunControlAction.CANCEL],
    }.get(status, [])


def _child(row: sqlite3.Row, now: datetime) -> ChildJob:
    status = JobStatus(row["status"])
    done, total = int(row["progress_done"]), row["progress_total"]
    rate: float | None = None
    eta: int | None = None
    if row["started_at"] and done and status is JobStatus.RUNNING:
        begun = datetime.fromisoformat(str(row["started_at"]).replace("Z", "+00:00"))
        minutes = (now - begun).total_seconds() / 60
        if minutes > 0:
            rate = round(done / minutes, 2)
            if total is not None and rate > 0:
                eta = max(0, int((int(total) - done) / rate * 60))
    state = orjson.loads(row["state"]) if row["state"] else None
    stats = state.get("stats") if isinstance(state, dict) else None
    return ChildJob(
        job_id=row["id"],
        kind=row["kind"],
        status=status,
        reused=bool(row["reused"]),
        progress_done=done,
        progress_total=total,
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        rate_per_minute=rate,
        eta_seconds=eta,
        stats=stats if isinstance(stats, dict) else None,
    )


def pipeline_view(conn: sqlite3.Connection, job_id: str, now: datetime) -> PipelineRun:
    job = jobs.live_job(conn, job_id, JOB_KIND)
    campaign_id = job.params_json.get("campaign_id")
    meta = conn.execute(
        "SELECT p.name AS project_name, p.is_synthetic, c.name AS campaign_name FROM projects p "
        "LEFT JOIN campaigns c ON c.id = ? WHERE p.id = ?",
        (campaign_id, job.project_id),
    ).fetchone()
    specs = conn.execute(
        "SELECT stage, ordinal, version, inputs_json, outputs_json, retry_json, resources_json "
        "FROM pipeline_stage_specs WHERE job_id = ? ORDER BY ordinal",
        (job_id,),
    ).fetchall()
    depends: dict[str, list[PipelineStage]] = {}
    for row in conn.execute(
        "SELECT d.stage, d.depends_on FROM pipeline_stage_dependencies d "
        "JOIN pipeline_stage_specs s "
        "ON s.job_id = d.job_id AND s.stage = d.depends_on WHERE d.job_id = ? ORDER BY s.ordinal",
        (job_id,),
    ):
        depends.setdefault(row["stage"], []).append(PipelineStage(row["depends_on"]))
    tasks = {
        row["stage"]: repository.from_row(JobTask, row)
        for row in conn.execute(
            f"SELECT {', '.join(JobTask.model_fields)} FROM job_tasks WHERE job_id = ?",  # noqa: S608
            (job_id,),
        )
    }
    states: dict[str, JsonObject] = {}
    for row in conn.execute(
        "SELECT task_id, state_json FROM job_checkpoints WHERE job_id = ? AND task_id IS NOT NULL "
        "ORDER BY created_at, id",
        (job_id,),
    ):
        loaded = orjson.loads(row["state_json"])
        states[row["task_id"]] = loaded if isinstance(loaded, dict) else {}
    counts = dict.fromkeys(
        ("queued", "running", "paused", "succeeded", "skipped", "failed", "cancelled"), 0
    )
    stages: list[PipelineStageView] = []
    for spec_row in specs:
        task = tasks[spec_row["stage"]]
        skipped = (
            states.get(task.id, {}).get("skipped") if task.status is JobStatus.SUCCEEDED else None
        )
        counts["skipped" if skipped else task.status.value] += 1
        attempts: list[PipelineAttempt] = []
        children: list[str] = []
        for attempt_row in conn.execute(
            f"SELECT {', '.join(TaskAttempt.model_fields)} FROM task_attempts WHERE task_id = ? "  # noqa: S608
            "ORDER BY attempt",
            (task.id,),
        ).fetchall():
            attempt = repository.from_row(TaskAttempt, attempt_row)
            child_rows = conn.execute(
                "SELECT a.reused, j.id, j.kind, j.status, j.progress_done, j.progress_total, "
                "j.started_at, j.finished_at, (SELECT c.state_json FROM job_checkpoints c "
                "WHERE c.job_id = j.id AND c.task_id IS NULL ORDER BY c.created_at DESC, c.id DESC "
                "LIMIT 1) AS state FROM task_attempt_jobs a JOIN jobs j ON j.id = a.job_id "
                "WHERE a.attempt_id = ? ORDER BY a.created_at, j.id",
                (attempt.id,),
            ).fetchall()
            children += [row["id"] for row in child_rows]
            attempts.append(
                PipelineAttempt(
                    attempt=attempt.attempt,
                    status=attempt.status,
                    started_at=attempt.started_at,
                    finished_at=attempt.finished_at,
                    error_code=attempt.error_code,
                    error_message=attempt.error_message,
                    retryable=attempt.retryable,
                    jobs=[_child(row, now) for row in child_rows],
                )
            )
        marks = ", ".join("?" * len(children)) or "NULL"
        events = [
            RunEvent(
                level=row["level"],
                event=row["event"],
                error_code=row["error_code"],
                retryable=None if row["retryable"] is None else bool(row["retryable"]),
                detail_json=orjson.loads(row["detail_json"]) if row["detail_json"] else None,
                created_at=row["created_at"],
            )
            for row in conn.execute(
                "SELECT level, event, error_code, retryable, detail_json, created_at "  # noqa: S608
                f"FROM job_events WHERE level IN ('warning', 'error') AND (job_id IN ({marks}) OR "
                "(job_id = ? AND task_id = ?)) ORDER BY created_at DESC, id DESC LIMIT 20",
                [*children, job_id, task.id],
            )
        ]
        policy = orjson.loads(spec_row["retry_json"])
        stages.append(
            PipelineStageView(
                task_id=task.id,
                stage=PipelineStage(spec_row["stage"]),
                ordinal=spec_row["ordinal"],
                version=spec_row["version"],
                inputs=orjson.loads(spec_row["inputs_json"]),
                outputs=orjson.loads(spec_row["outputs_json"]),
                depends_on=depends.get(spec_row["stage"], []),
                resources=[PipelineResource(v) for v in orjson.loads(spec_row["resources_json"])],
                max_attempts=task.max_attempts,
                retryable_codes=list(policy["retryable_codes"]),
                status=task.status,
                skipped=str(skipped) if skipped else None,
                attempts=attempts,
                events=events,
            )
        )
    letters = [
        DeadLetter(
            id=row["id"],
            task_id=row["task_id"],
            stage=PipelineStage(row["stage"]),
            attempt=row["attempt"],
            error_code=row["error_code"],
            error_message=row["error_message"],
            retryable=bool(row["retryable"]),
            review_status=DeadLetterStatus(row["review_status"]),
            created_at=row["created_at"],
            resolved_at=row["resolved_at"],
            resolved_by=row["resolved_by"],
            resolution_reason=row["resolution_reason"],
        )
        for row in conn.execute(
            "SELECT d.*, t.stage FROM dead_letter_tasks d JOIN job_tasks t ON t.id = d.task_id "
            "WHERE d.job_id = ? ORDER BY d.created_at, d.id",
            (job_id,),
        )
    ]
    outputs = states.get(tasks[_S.EXPORT_ELIGIBILITY.value].id, {})
    eligible = outputs.get("eligible_leads")
    return PipelineRun(
        job_id=job.id,
        project_id=job.project_id,
        project_name=meta["project_name"],
        campaign_id=campaign_id if isinstance(campaign_id, str) else None,
        campaign_name=meta["campaign_name"],
        status=job.status,
        priority=job.priority,
        check_mail_domains=bool(job.params_json.get("check_mail_domains")),
        progress_done=job.progress_done,
        progress_total=len(specs),
        counts=StageCounts(**counts),
        requested_at=job.requested_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        stages=stages,
        dead_letters=letters,
        score_run_id=str(outputs["score_run_id"]) if outputs.get("score_run_id") else None,
        eligible_leads=int(eligible) if isinstance(eligible, int) else None,
        snapshot_id=str(outputs["snapshot_id"]) if outputs.get("snapshot_id") else None,
        actions=_actions(job.status),
        is_synthetic=bool(meta["is_synthetic"]),
    )


def list_pipelines(
    conn: sqlite3.Connection, query: PipelineListQuery, now: datetime
) -> PipelinePage:
    where, params = ["kind = ?"], list[object]([JOB_KIND])
    if query.project_id is not None:
        where.append("project_id = ?")
        params.append(query.project_id)
    (total,) = conn.execute(
        f"SELECT count(*) FROM jobs WHERE {' AND '.join(where)}",  # noqa: S608
        params,
    ).fetchone()
    if query.cursor is not None:
        last_key, last_id = repository.decode_cursor(query.cursor, JOB_KIND)
        where.append("(requested_at, id) < (?, ?)")
        params += [last_key, last_id]
    rows = conn.execute(
        f"SELECT id, requested_at FROM jobs WHERE {' AND '.join(where)} "  # noqa: S608
        "ORDER BY requested_at DESC, id DESC LIMIT ?",
        [*params, query.limit + 1],
    ).fetchall()
    page = rows[: query.limit]
    next_cursor = (
        repository.encode_cursor(JOB_KIND, page[-1]["requested_at"], page[-1]["id"])
        if len(rows) > query.limit
        else None
    )
    return PipelinePage(
        items=[pipeline_view(conn, row["id"], now) for row in page],
        next_cursor=next_cursor,
        total=total,
        resources=_idle_usage(now),
    )


def _idle_usage(now: datetime) -> ResourceUsage:
    """A placeholder the service replaces with a real sample before answering."""
    return ResourceUsage(
        sampled_at=now,
        cpu_count=os.cpu_count() or 1,
        cpu_percent=None,
        ram_total_mib=None,
        ram_free_mib=None,
        vram_total_mib=None,
        vram_free_mib=None,
        disk_total_mib=None,
        disk_free_mib=None,
        active_browsers=None,
        slots=[],
        pressure=[],
    )


# ---- the event log ------------------------------------------------------------------------------


def list_events(conn: sqlite3.Connection, query: JobEventQuery) -> JobEventPage:
    """Newest first. A pipeline's log holds its engine jobs' events too."""
    where: list[str] = []
    params: list[object] = []
    if query.job_id is not None:
        ids = [query.job_id] + [
            str(row[0])
            for row in conn.execute(
                "SELECT DISTINCT x.job_id FROM task_attempt_jobs x "
                "JOIN task_attempts a ON a.id = x.attempt_id JOIN job_tasks t ON t.id = a.task_id "
                "WHERE t.job_id = ?",
                (query.job_id,),
            )
        ]
        where.append(f"e.job_id IN ({', '.join('?' * len(ids))})")
        params += ids
    if query.task_id is not None:
        where.append("e.task_id = ?")
        params.append(query.task_id)
    if query.business_id is not None:
        where.append("json_extract(e.detail_json, '$.business_id') = ?")
        params.append(query.business_id)
    if query.request_id is not None:
        where.append("json_extract(e.detail_json, '$.request_id') = ?")
        params.append(query.request_id)
    if query.levels:
        where.append(f"e.level IN ({', '.join('?' * len(query.levels))})")
        params += [level.value for level in query.levels]
    clause = f"WHERE {' AND '.join(where)} " if where else ""
    (total,) = conn.execute(f"SELECT count(*) FROM job_events e {clause}", params).fetchone()  # noqa: S608
    if query.cursor is not None:
        last_key, last_id = repository.decode_cursor(query.cursor, "event")
        where.append("(e.created_at, e.id) < (?, ?)")
        params += [last_key, last_id]
        clause = f"WHERE {' AND '.join(where)} "
    rows = conn.execute(
        f"SELECT {', '.join('e.' + name for name in JobEvent.model_fields)}, j.kind AS job_kind, "  # noqa: S608
        "j.project_id, t.stage FROM job_events e JOIN jobs j ON j.id = e.job_id "
        f"LEFT JOIN job_tasks t ON t.id = e.task_id {clause}"
        "ORDER BY e.created_at DESC, e.id DESC LIMIT ?",
        [*params, query.limit + 1],
    ).fetchall()
    page = rows[: query.limit]
    next_cursor = (
        repository.encode_cursor("event", page[-1]["created_at"], page[-1]["id"])
        if len(rows) > query.limit
        else None
    )
    return JobEventPage(
        items=[
            JobEventItem(
                event=repository.from_row(JobEvent, row),
                job_kind=row["job_kind"],
                project_id=row["project_id"],
                stage=row["stage"],
            )
            for row in page
        ],
        next_cursor=next_cursor,
        total=total,
    )


def cleanup_events(
    conn: sqlite3.Connection, request: EventCleanupRequest, now: datetime
) -> EventCleanupResult:
    """Old events of finished jobs at the levels asked for; errors always stay."""
    cutoff = now - timedelta(days=request.older_than_days)
    base = (
        "FROM job_events e JOIN jobs j ON j.id = e.job_id "
        "WHERE j.status IN ('succeeded', 'failed', 'cancelled') AND e.created_at < ? "
    )
    by_level = [
        LevelCount(
            level=level,
            count=_count(
                conn, f"SELECT count(*) {base}AND e.level = ?", format_ts(cutoff), level.value
            ),
        )
        for level in request.levels
    ]
    kept = _count(conn, f"SELECT count(*) {base}AND e.level = 'error'", format_ts(cutoff))
    matched = sum(item.count for item in by_level)
    deleted = 0
    if request.mode is BulkMode.APPLY and matched:
        marks = ", ".join("?" * len(request.levels))
        with repository.atomic(conn):
            deleted = conn.execute(
                f"DELETE FROM job_events WHERE id IN (SELECT e.id {base}AND e.level IN ({marks}))",  # noqa: S608
                [format_ts(cutoff), *(level.value for level in request.levels)],
            ).rowcount
        logger.info(
            "job events deleted",
            extra={"events": deleted, "reason": request.reason, "cutoff": format_ts(cutoff)},
        )
    return EventCleanupResult(
        mode=request.mode,
        cutoff=cutoff,
        matched=matched,
        deleted=deleted,
        by_level=by_level,
        kept_errors=kept,
    )
