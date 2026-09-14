"""Lead scoring as a resumable background job (phase 11).

    score (batches of canonical businesses: facts read from the store, or loaded from the run whose
           stored facts are scored again; scorecards and components written; checkpoint after each)

One open run per project. A business the run already scored is skipped, so a pause, a cancel or a
restart never scores it twice. Control is written to the store first and read back between
batches; a job a dead worker left running is paused at startup, never resumed (D-027).
"""

import sqlite3
from collections.abc import Callable, Sequence
from typing import Final

from pydantic import JsonValue

from research_engine import jobs, repository
from research_engine.domain import (
    BusinessFacts,
    EventLevel,
    Job,
    JobStatus,
    ScoreRunItem,
    ScoreRunPage,
    ScoringStartRequest,
    utc_now,
)
from research_engine.scoring import facts, store
from research_engine.scoring.model import ScoringError

JOB_KIND: Final = store.JOB_KIND
ACTOR: Final = store.ACTOR
BATCH: Final = 200


def run_scoring(conn: sqlite3.Connection, job_id: str, should_stop: Callable[[], bool]) -> bool:
    """Runs or resumes one job to its end. False when it stopped early (paused or cancelled)."""
    job = repository.get(conn, Job, job_id)
    if job.status not in (JobStatus.QUEUED, JobStatus.RUNNING):
        return False
    run = store.run_of_job(conn, job_id)
    icp, scoring, versions = store.definitions(conn, run)
    jobs.mark_running(conn, job_id, ACTOR)
    source = run.facts_from_run_id
    ids = store.scored_ids(conn, source) if source else facts.root_ids(conn, run.project_id)
    audits = {} if source else facts.audits_by_root(conn, run.project_id)

    def load(chunk: Sequence[str]) -> list[BusinessFacts]:
        if source:
            return store.stored_facts(conn, source, chunk)
        return facts.collect(conn, chunk, audits, utc_now())

    done = set(store.scored_ids(conn, run.id))
    todo = [business for business in ids if business not in done]
    progress = len(ids) - len(todo)
    with repository.atomic(conn):
        jobs.set_progress(conn, job_id, progress, len(ids))
        jobs.checkpoint(conn, job_id, {"stage": "score"})
        if not done:
            detail: dict[str, JsonValue] = {"businesses": len(ids),
                                            "facts": "stored" if source else "store"}  # fmt: skip
            jobs.write_event(conn, job_id, None, "stage_started", EventLevel.INFO, detail)
    for start in range(0, len(todo), BATCH):
        if should_stop():
            return False
        chunk = todo[start : start + BATCH]
        store.write_scores(conn, run, load(chunk), icp, scoring, versions, utc_now())
        progress += len(chunk)
        with repository.atomic(conn):
            jobs.set_progress(conn, job_id, progress, len(ids))
            jobs.checkpoint(conn, job_id, {"stage": "score", "cursor": chunk[-1]})
    if repository.get(conn, Job, job_id).status is not JobStatus.RUNNING:
        return False  # paused or cancelled at the very end: the writes are idempotent
    store.finish_run(conn, run.id, utc_now())
    stats: dict[str, JsonValue] = dict(store.tier_stats(conn, run.id))
    jobs.finish_job(conn, job_id, {"stage": "done", "stats": stats}, ACTOR)
    return True


class ScoringService(jobs.BackgroundJobs[ScoreRunItem, ScoreRunPage]):
    """Owns the scoring jobs this worker runs (threads, control and recovery: `jobs`)."""

    kind = JOB_KIND
    actor = ACTOR

    async def start(self, request: ScoringStartRequest) -> ScoreRunPage:
        created, listed = await self._in_store(
            lambda conn: store.plan_run(conn, request, actor=ACTOR)
        )
        for job_id in created:
            self._spawn(job_id)
        return await self._in_store(lambda conn: self.page(conn, [*created, *listed]))

    def plan(self, conn: sqlite3.Connection, project_id: str | None) -> tuple[list[str], list[str]]:
        raise ScoringError("a scoring run needs an ICP version and a scoring version")

    def run(self, conn: sqlite3.Connection, job_id: str, should_stop: Callable[[], bool]) -> bool:
        return run_scoring(conn, job_id, should_stop)

    def view(self, conn: sqlite3.Connection, job_id: str) -> ScoreRunItem:
        return store.get_run(conn, job_id)

    def page(self, conn: sqlite3.Connection, job_ids: Sequence[str]) -> ScoreRunPage:
        return store.runs_by_id(conn, job_ids)
