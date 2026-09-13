"""Job control shared by every engine that runs jobs: events, pause, resume, cancel and recovery.

A job owns its `source_runs`. Control decisions are written to the store first, so they survive a
restart, and every engine observes them cooperatively. A job left `running` by a worker that died is
paused at startup with a `run_interrupted` event and never resumed silently (D-027).
"""

import asyncio
import contextlib
import logging
import sqlite3
import threading
from collections.abc import Callable, Sequence

import orjson

from research_engine import repository
from research_engine.domain import (
    DiscoveryControlRequest,
    EventLevel,
    Job,
    JobCheckpoint,
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

logger = logging.getLogger(__name__)

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


# ---- checkpoints (jobs without source runs: resolution, Latino evidence) ------------------------


def checkpoint(conn: sqlite3.Connection, job_id: str, state: JsonObject) -> None:
    repository.insert(
        conn,
        JobCheckpoint(id=new_id(), job_id=job_id, task_id=None, state_json=state,
                      created_at=utc_now()),
    )  # fmt: skip


def latest_state(conn: sqlite3.Connection, job_id: str) -> JsonObject:
    row = conn.execute(
        "SELECT state_json FROM job_checkpoints WHERE job_id = ? AND task_id IS NULL "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (job_id,),
    ).fetchone()
    loaded = orjson.loads(row["state_json"]) if row else None
    return loaded if isinstance(loaded, dict) else {}


def set_progress(conn: sqlite3.Connection, job_id: str, done: int, total: int | None) -> None:
    conn.execute(
        "UPDATE jobs SET progress_done = ?, progress_total = ?, updated_at = ? WHERE id = ?",
        (done if total is None else min(done, total), total, format_ts(utc_now()), job_id),
    )


def finish_job(conn: sqlite3.Connection, job_id: str, state: JsonObject, actor: str) -> None:
    now = format_ts(utc_now())
    with repository.atomic(conn):
        checkpoint(conn, job_id, state)
        repository.change_status(conn, Job, job_id, JobStatus.SUCCEEDED, actor=actor)
        conn.execute(
            "UPDATE jobs SET finished_at = ?, updated_at = ? WHERE id = ?", (now, now, job_id)
        )
        stats = state.get("stats")
        write_event(conn, job_id, None, "run_succeeded", EventLevel.INFO,
                    stats if isinstance(stats, dict) else None)  # fmt: skip


class BackgroundJobs[RunT, PageT]:
    """The jobs of one engine this worker runs, each on a thread with its own connection.

    Control is written to the store first (`apply_control`) and read back between batches, so pause,
    cancel, shutdown and a worker restart all leave a job resumable. An engine says how to plan, run
    and show its jobs.
    """

    kind: str
    actor: str

    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._stops: dict[str, threading.Event] = {}

    def plan(self, conn: sqlite3.Connection, project_id: str | None) -> tuple[list[str], list[str]]:
        """(created, already open) job ids for one project, or for every project."""
        raise NotImplementedError

    def run(self, conn: sqlite3.Connection, job_id: str, should_stop: Callable[[], bool]) -> bool:
        """Runs or resumes a job to its end; False when it stopped early."""
        raise NotImplementedError

    def view(self, conn: sqlite3.Connection, job_id: str) -> RunT:
        raise NotImplementedError

    def page(self, conn: sqlite3.Connection, job_ids: Sequence[str]) -> PageT:
        raise NotImplementedError

    async def _in_store[T](self, action: Callable[[sqlite3.Connection], T]) -> T:
        def work() -> T:
            conn = self._connect()
            try:
                return action(conn)
            finally:
                conn.close()

        return await asyncio.to_thread(work)

    async def start_project(self, project_id: str | None) -> PageT:
        created, listed = await self._in_store(lambda conn: self.plan(conn, project_id))
        for job_id in created:
            self._spawn(job_id)
        return await self._in_store(lambda conn: self.page(conn, [*created, *listed]))

    async def control(self, request: DiscoveryControlRequest) -> RunT:
        def apply(conn: sqlite3.Connection) -> None:
            live_job(conn, request.job_id, self.kind)
            apply_control(conn, request.job_id, request.action, self.actor)

        await self._in_store(apply)
        if request.action is RunControlAction.RESUME:
            self._spawn(request.job_id)
        else:
            await self._halt(request.job_id)
        return await self._in_store(lambda conn: self.view(conn, request.job_id))

    async def idle(self, job_id: str) -> None:
        """Waits until this job's background work has stopped, if this worker runs it."""
        if (task := self._tasks.get(job_id)) is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def recover(self) -> int:
        """At startup: jobs a dead worker left running are paused, never resumed silently."""
        return await self._in_store(lambda conn: pause_interrupted(conn, self.kind, self.actor))

    async def shutdown(self) -> None:
        for job_id in list(self._tasks):
            await self._halt(job_id)

            def pause(conn: sqlite3.Connection, job_id: str = job_id) -> None:
                pause_job(conn, job_id, "worker_stopping", self.actor)

            with contextlib.suppress(Exception):
                await self._in_store(pause)

    def _spawn(self, job_id: str) -> None:
        existing = self._tasks.get(job_id)
        if existing is not None and not existing.done():
            return
        stop = threading.Event()
        task = asyncio.create_task(self._execute(job_id, stop), name=f"{self.kind}:{job_id}")
        self._tasks[job_id], self._stops[job_id] = task, stop
        task.add_done_callback(lambda done: self._forget(job_id, done))

    def _forget(self, job_id: str, task: asyncio.Task[None]) -> None:
        if self._tasks.get(job_id) is task:
            del self._tasks[job_id]
            self._stops.pop(job_id, None)

    async def _halt(self, job_id: str) -> None:
        if (stop := self._stops.get(job_id)) is not None:
            stop.set()
        await self.idle(job_id)

    async def _execute(self, job_id: str, stop: threading.Event) -> None:
        try:
            await asyncio.to_thread(self._work, job_id, stop)
        except Exception:
            logger.exception("%s job failed", self.kind, extra={"job_id": job_id})
            await self._in_store(lambda conn: fail_job(conn, job_id, "internal error", self.actor))

    def _work(self, job_id: str, stop: threading.Event) -> None:
        conn = self._connect()
        try:

            def should_stop() -> bool:
                return stop.is_set() or (
                    repository.get(conn, Job, job_id).status is not JobStatus.RUNNING
                )

            finished = self.run(conn, job_id, should_stop)
            logger.info("%s job stopped", self.kind, extra={"job_id": job_id, "finished": finished})
        finally:
            conn.close()
