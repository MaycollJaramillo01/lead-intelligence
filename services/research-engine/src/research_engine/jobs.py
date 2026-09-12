"""Job control shared by every engine that runs jobs: events, pause, resume, cancel and recovery.

A job owns its `source_runs`. Control decisions are written to the store first, so they survive a
restart, and every engine observes them cooperatively. A job left `running` by a worker that died is
paused at startup with a `run_interrupted` event and never resumed silently (D-027).
"""

import sqlite3
from collections.abc import Sequence

from research_engine import repository
from research_engine.domain import (
    EventLevel,
    Job,
    JobEvent,
    JobStatus,
    JsonObject,
    RunControlAction,
    SourceRun,
    SourceRunStatus,
    format_ts,
    new_id,
    utc_now,
)

OPEN_RUNS = (SourceRunStatus.QUEUED, SourceRunStatus.RUNNING, SourceRunStatus.PAUSED)


def write_event(
    conn: sqlite3.Connection,
    job_id: str,
    task_id: str | None,
    name: str,
    level: EventLevel,
    detail: JsonObject | None = None,
    error_code: str | None = None,
    retryable: bool | None = None,
) -> None:
    repository.insert(
        conn,
        JobEvent(
            id=new_id(),
            job_id=job_id,
            task_id=task_id,
            level=level,
            event=name,
            error_code=error_code,
            retryable=retryable,
            detail_json=detail,
            created_at=utc_now(),
        ),
    )


def open_runs(conn: sqlite3.Connection, job_id: str) -> Sequence[SourceRun]:
    return [run for run in repository.source_runs_of_job(conn, job_id) if run.status in OPEN_RUNS]


def live_job(conn: sqlite3.Connection, job_id: str, kind: str) -> Job:
    """The job, refused as not found when it belongs to another engine."""
    job = repository.get(conn, Job, job_id)
    if job.kind != kind:
        raise repository.NotFoundError(f"job {job_id} is not a {kind} run")
    return job


def mark_running(conn: sqlite3.Connection, job_id: str, actor: str) -> None:
    job = repository.get(conn, Job, job_id)
    now = format_ts(utc_now())
    with repository.atomic(conn):
        if job.status in (JobStatus.QUEUED, JobStatus.PAUSED):
            repository.change_status(conn, Job, job_id, JobStatus.RUNNING, actor=actor)
        if job.started_at is None:
            conn.execute(
                "UPDATE jobs SET started_at = ?, updated_at = ? WHERE id = ?", (now, now, job_id)
            )


def apply_control(
    conn: sqlite3.Connection, job_id: str, action: RunControlAction, actor: str
) -> None:
    job = repository.get(conn, Job, job_id)
    target = {
        RunControlAction.PAUSE: JobStatus.PAUSED,
        RunControlAction.RESUME: JobStatus.RUNNING,
        RunControlAction.CANCEL: JobStatus.CANCELLED,
    }[action]
    if job.status is target:
        return
    with repository.atomic(conn):
        repository.change_status(conn, Job, job.id, target, actor=actor)
        for run in open_runs(conn, job.id):
            if target is JobStatus.CANCELLED:
                repository.change_status(
                    conn, SourceRun, run.id, SourceRunStatus.CANCELLED, actor=actor
                )
            elif target is JobStatus.PAUSED and run.status is SourceRunStatus.RUNNING:
                repository.change_status(
                    conn, SourceRun, run.id, SourceRunStatus.PAUSED, actor=actor
                )
        write_event(conn, job.id, None, f"run_{action.value}", EventLevel.INFO)


def pause_job(conn: sqlite3.Connection, job_id: str, reason: str, actor: str) -> None:
    job = repository.get(conn, Job, job_id)
    if job.status is not JobStatus.RUNNING:
        return
    with repository.atomic(conn):
        repository.change_status(conn, Job, job_id, JobStatus.PAUSED, actor=actor)
        for run in open_runs(conn, job_id):
            if run.status is SourceRunStatus.RUNNING:
                repository.change_status(
                    conn, SourceRun, run.id, SourceRunStatus.PAUSED, actor=actor
                )
        write_event(conn, job_id, None, "run_interrupted", EventLevel.WARNING, {"reason": reason})


def fail_job(conn: sqlite3.Connection, job_id: str, message: str, actor: str) -> None:
    job = repository.get(conn, Job, job_id)
    if job.status not in (JobStatus.RUNNING, JobStatus.QUEUED):
        return
    now = format_ts(utc_now())
    with repository.atomic(conn):
        if job.status is JobStatus.QUEUED:
            repository.change_status(conn, Job, job_id, JobStatus.RUNNING, actor=actor)
        repository.change_status(conn, Job, job_id, JobStatus.FAILED, actor=actor)
        conn.execute(
            "UPDATE jobs SET finished_at = ?, updated_at = ? WHERE id = ?", (now, now, job_id)
        )
        write_event(
            conn,
            job_id,
            None,
            "run_failed",
            EventLevel.ERROR,
            {"message": message},
            "internal",
            False,
        )


def pause_interrupted(conn: sqlite3.Connection, kind: str, actor: str) -> int:
    """Jobs of this kind the previous worker left running. Paused, never silently restarted."""
    rows = conn.execute(
        "SELECT id FROM jobs WHERE kind = ? AND status = 'running'", (kind,)
    ).fetchall()
    for row in rows:
        pause_job(conn, row["id"], "worker_restarted", actor)
    return len(rows)
