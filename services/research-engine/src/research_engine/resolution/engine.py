"""Entity resolution as a resumable background job (phase 06).

    ingest (batches of candidates, checkpoint after each)
      └─ compare (deterministic: recomputed in memory on every run, checks for pause and cancel)
           └─ apply (pairs, then clusters and canonical values; idempotent writes)

A job resolves one project. Control is written to the store first (`jobs.apply_control`) and read
back between batches, so pause, cancel and a worker restart leave the job resumable; a job a dead
worker left running is paused at startup, never resumed silently (D-027).
"""

import asyncio
import contextlib
import logging
import sqlite3
import threading
from collections.abc import Callable
from typing import Final

from pydantic import JsonValue

from research_engine import jobs, repository
from research_engine.domain import (
    DiscoveryControlRequest,
    EventLevel,
    Job,
    JobStatus,
    JsonObject,
    ResolutionRun,
    ResolutionRunPage,
    ResolutionStartRequest,
    ResolutionStats,
    RunControlAction,
)
from research_engine.resolution import matching, store

logger = logging.getLogger(__name__)

JOB_KIND: Final = store.JOB_KIND
ACTOR: Final = store.ACTOR
INGEST_BATCH: Final = 200
CHECK_EVERY: Final = 2_000


def run_resolution(conn: sqlite3.Connection, job_id: str, should_stop: Callable[[], bool]) -> bool:
    """Runs or resumes one job to its end. False when it stopped early (paused or cancelled)."""
    job = repository.get(conn, Job, job_id)
    if job.status not in (JobStatus.QUEUED, JobStatus.RUNNING):
        return False
    project_id = str(job.params_json["project_id"])
    jobs.mark_running(conn, job_id, ACTOR)
    state = store.latest_state(conn, job_id)
    saved = state.get("stats")
    stats = {name: 0 for name in ResolutionStats.model_fields}
    if isinstance(saved, dict):
        stats |= {name: value for name, value in saved.items()
                  if name in stats and isinstance(value, int)}  # fmt: skip

    def save(stage: str, done: int, total: int | None, **extra: JsonValue) -> None:
        state: JsonObject = {"stage": stage, "stats": dict[str, JsonValue](stats), **extra}
        with repository.atomic(conn):
            store.set_progress(conn, job_id, done, total)
            store.checkpoint(conn, job_id, state)

    def event(name: str, **detail: JsonValue) -> None:
        with repository.atomic(conn):
            jobs.write_event(conn, job_id, None, name, EventLevel.INFO, dict(detail))

    if state.get("stage") in (None, "ingest"):
        cursor = state.get("cursor") if isinstance(state.get("cursor"), str) else None
        total = store.count_candidates(conn, project_id)
        done = store.count_candidates(conn, project_id, str(cursor)) if cursor else 0
        site_runs = store.site_runs_by_candidate(conn, project_id)
        event("stage_started", stage="ingest", candidates=total)
        while True:
            if should_stop():
                return False
            batch = store.ingest_batch(
                conn, project_id, str(cursor) if cursor else None, INGEST_BATCH, site_runs
            )
            if batch.candidates == 0:
                break
            cursor, done = batch.last_id, done + batch.candidates
            stats["ingested"] += batch.created
            stats["observations"] += batch.observations
            save("ingest", done, total, cursor=cursor)

    if should_stop():
        return False
    entities = store.load_entities(conn, project_id)
    features: list[matching.Features] = []
    for index, entity in enumerate(entities):
        if index % CHECK_EVERY == 0 and should_stop():
            return False
        features.append(matching.features(entity))
    blocking = matching.candidate_pairs(features)
    by_id = {item.business_id: item for item in features}
    pairs = sorted(blocking.pairs.items())
    stats |= {
        "records": len(entities),
        "blocks": blocking.blocks,
        "skipped_blocks": blocking.skipped_blocks,
        "largest_block": blocking.largest_block,
        "pairs_compared": len(pairs),
    }
    save("compare", 0, len(pairs))
    event("stage_started", stage="compare", records=len(entities), pairs=len(pairs))
    comparisons: dict[tuple[str, str], matching.Comparison] = {}
    for index, (pair, keys) in enumerate(pairs):
        if index % CHECK_EVERY == 0 and index:
            if should_stop():
                return False
            with repository.atomic(conn):
                store.set_progress(conn, job_id, index, len(pairs))
        comparisons[pair] = matching.compare(by_id[pair[0]], by_id[pair[1]], keys)

    if should_stop():
        return False
    save("apply", len(pairs), len(pairs))
    written = store.persist_pairs(conn, project_id, comparisons, job_id)
    clusters = store.recluster(
        conn, project_id, actor=ACTOR,
        reason=f"resolution run {job_id} (rules {matching.RULES_VERSION})",
    )  # fmt: skip
    outcomes = store.outcome_counts(conn, project_id)
    stats |= {
        "auto_merged": outcomes["auto_merge"],
        "review": outcomes["manual_review"],
        "distinct": outcomes["distinct"],
        "clusters": clusters.clusters,
        "merged_records": clusters.merged_records,
        "manual_decisions": outcomes["manual"],
    }
    event("stage_finished", stage="apply", inserted=written.inserted, updated=written.updated,
          retired=written.retired, merges=clusters.merges, unmerges=clusters.unmerges,
          refused=clusters.refused, canonical_changes=clusters.canonical_changes)  # fmt: skip
    if repository.get(conn, Job, job_id).status is not JobStatus.RUNNING:
        return False  # paused or cancelled while applying: the writes are idempotent
    store.finish_run(conn, job_id, {"stage": "done", "stats": dict[str, JsonValue](stats)})
    return True


class ResolutionService:
    """Owns the resolution jobs this worker runs. The work happens on a thread with its own
    connection; pause, cancel and shutdown are observed between batches."""

    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._stops: dict[str, threading.Event] = {}

    async def _in_store[T](self, action: Callable[[sqlite3.Connection], T]) -> T:
        def run() -> T:
            conn = self._connect()
            try:
                return action(conn)
            finally:
                conn.close()

        return await asyncio.to_thread(run)

    async def start(self, request: ResolutionStartRequest) -> ResolutionRunPage:
        created, listed = await self._in_store(
            lambda conn: store.plan_runs(conn, request.project_id, actor=ACTOR)
        )
        for job_id in created:
            self._spawn(job_id)
        return await self._in_store(lambda conn: store.runs_by_id(conn, [*created, *listed]))

    async def control(self, request: DiscoveryControlRequest) -> ResolutionRun:
        def apply(conn: sqlite3.Connection) -> None:
            jobs.live_job(conn, request.job_id, JOB_KIND)
            jobs.apply_control(conn, request.job_id, request.action, ACTOR)

        await self._in_store(apply)
        if request.action is RunControlAction.RESUME:
            self._spawn(request.job_id)
        else:
            await self._halt(request.job_id)
        return await self._in_store(lambda conn: store.get_resolution_run(conn, request.job_id))

    async def idle(self, job_id: str) -> None:
        """Waits until this job's background work has stopped, if this worker runs it."""
        if (task := self._tasks.get(job_id)) is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def recover(self) -> int:
        """At startup: runs a dead worker left running are paused, never resumed silently."""
        return await self._in_store(lambda conn: jobs.pause_interrupted(conn, JOB_KIND, ACTOR))

    async def shutdown(self) -> None:
        for job_id in list(self._tasks):
            await self._halt(job_id)

            def pause(conn: sqlite3.Connection, job_id: str = job_id) -> None:
                jobs.pause_job(conn, job_id, "worker_stopping", ACTOR)

            with contextlib.suppress(Exception):
                await self._in_store(pause)

    def _spawn(self, job_id: str) -> None:
        existing = self._tasks.get(job_id)
        if existing is not None and not existing.done():
            return
        stop = threading.Event()
        task = asyncio.create_task(self._execute(job_id, stop), name=f"resolution:{job_id}")
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
            logger.exception("resolution job failed", extra={"job_id": job_id})
            await self._in_store(lambda conn: jobs.fail_job(conn, job_id, "internal error", ACTOR))

    def _work(self, job_id: str, stop: threading.Event) -> None:
        conn = self._connect()
        try:

            def should_stop() -> bool:
                return stop.is_set() or (
                    repository.get(conn, Job, job_id).status is not JobStatus.RUNNING
                )

            finished = run_resolution(conn, job_id, should_stop)
            logger.info("resolution job stopped", extra={"job_id": job_id, "finished": finished})
        finally:
            conn.close()
