"""Latino evidence evaluation as a resumable background job (phase 07).

    extract (batches of source records and crawled pages, checkpoint after each)
      └─ lifecycle (stale, revoked, contradicted evidence; idempotent)
           └─ evaluate (batches of canonical businesses, checkpoint after each; idempotent)

A job evaluates one project. Control is written to the store first (`jobs.apply_control`) and read
back between batches, so pause, cancel and a worker restart leave the job resumable; a job a dead
worker left running is paused at startup, never resumed silently (D-027).
"""

import sqlite3
from collections.abc import Callable, Sequence
from typing import Final

from pydantic import JsonValue

from research_engine import jobs, repository
from research_engine.domain import (
    EventLevel,
    Job,
    JobStatus,
    JsonObject,
    LatinoRun,
    LatinoRunPage,
    LatinoStartRequest,
    LatinoStats,
    utc_now,
)
from research_engine.latino import rules, store

JOB_KIND: Final = store.JOB_KIND
ACTOR: Final = store.ACTOR
SCAN_BATCH: Final = 200
EVALUATE_BATCH: Final = 500


def run_latino(
    conn: sqlite3.Connection,
    job_id: str,
    should_stop: Callable[[], bool],
    ruleset: rules.Ruleset = rules.DEFAULT_RULESET,
) -> bool:
    """Runs or resumes one job to its end. False when it stopped early (paused or cancelled)."""
    job = repository.get(conn, Job, job_id)
    if job.status not in (JobStatus.QUEUED, JobStatus.RUNNING):
        return False
    project_id = str(job.params_json["project_id"])
    jobs.mark_running(conn, job_id, ACTOR)
    state = jobs.latest_state(conn, job_id)
    saved = state.get("stats")
    stats = {name: 0 for name in LatinoStats.model_fields}
    if isinstance(saved, dict):
        stats |= {name: value for name, value in saved.items()
                  if name in stats and isinstance(value, int)}  # fmt: skip

    def save(stage: str, done: int, total: int | None, **extra: JsonValue) -> None:
        checkpoint: JsonObject = {"stage": stage, "stats": dict[str, JsonValue](stats), **extra}
        with repository.atomic(conn):
            jobs.set_progress(conn, job_id, done, total)
            jobs.checkpoint(conn, job_id, checkpoint)

    def event(name: str, **detail: JsonValue) -> None:
        with repository.atomic(conn):
            jobs.write_event(conn, job_id, None, name, EventLevel.INFO, dict(detail))

    stage, resume_at = state.get("stage"), state.get("cursor")
    cursor = resume_at if isinstance(resume_at, str) else None
    if stage in (None, "extract"):
        found = store.sources(conn, project_id)
        todo = [source for source in found if cursor is None or source.raw_record_id > cursor]
        done = len(found) - len(todo)
        event("stage_started", stage="extract", sources=len(found))
        for start in range(0, len(todo), SCAN_BATCH):
            if should_stop():
                return False
            batch = todo[start : start + SCAN_BATCH]
            scanned = store.scan_batch(conn, batch, utc_now())
            stats["sources_scanned"] += scanned.sources
            stats["pages_scanned"] += scanned.pages
            stats["evidence_found"] += scanned.evidence
            done += len(batch)
            save("extract", done, len(found), cursor=batch[-1].raw_record_id)
        cursor = None

    if should_stop():
        return False
    now = utc_now()
    context, changes = store.evaluation_context(conn, project_id, now, ruleset)
    if stage != "evaluate":
        cursor = None
        event("stage_started", stage="evaluate", businesses=len(context.roots),
              status_changes=dict[str, JsonValue](changes))  # fmt: skip
    roots: Sequence[str] = [root for root in context.roots if cursor is None or root > cursor]
    done = len(context.roots) - len(roots)
    save("evaluate", done, len(context.roots), cursor=cursor)
    for start in range(0, len(roots), EVALUATE_BATCH):
        if should_stop():
            return False
        chunk = roots[start : start + EVALUATE_BATCH]
        stats["changed"] += store.evaluate_batch(conn, context, chunk, now, ruleset)
        done += len(chunk)
        save("evaluate", done, len(context.roots), cursor=chunk[-1])
    store.supersede_non_roots(conn, project_id, now)
    stats["businesses"] = len(context.roots)
    stats |= store.summary(conn, project_id, ruleset)
    event("stage_finished", stage="evaluate", changed=stats["changed"])
    if repository.get(conn, Job, job_id).status is not JobStatus.RUNNING:
        return False  # paused or cancelled at the very end: the writes are idempotent
    jobs.finish_job(conn, job_id, {"stage": "done", "stats": dict[str, JsonValue](stats)}, ACTOR)
    return True


class LatinoService(jobs.BackgroundJobs[LatinoRun, LatinoRunPage]):
    """Owns the Latino evidence jobs this worker runs (threads, control and recovery: `jobs`)."""

    kind = JOB_KIND
    actor = ACTOR

    async def start(self, request: LatinoStartRequest) -> LatinoRunPage:
        return await self.start_project(request.project_id)

    def plan(self, conn: sqlite3.Connection, project_id: str | None) -> tuple[list[str], list[str]]:
        return store.plan_runs(conn, project_id, actor=ACTOR)

    def run(self, conn: sqlite3.Connection, job_id: str, should_stop: Callable[[], bool]) -> bool:
        return run_latino(conn, job_id, should_stop)

    def view(self, conn: sqlite3.Connection, job_id: str) -> LatinoRun:
        return store.get_latino_run(conn, job_id)

    def page(self, conn: sqlite3.Connection, job_ids: Sequence[str]) -> LatinoRunPage:
        return store.runs_by_id(conn, job_ids)
