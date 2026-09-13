"""Entity resolution as a resumable background job (phase 06).

    ingest (batches of candidates, checkpoint after each)
      └─ compare (deterministic: recomputed in memory on every run, checks for pause and cancel)
           └─ apply (pairs, then clusters and canonical values; idempotent writes)

A job resolves one project. Control is written to the store first (`jobs.apply_control`) and read
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
    ResolutionRun,
    ResolutionRunPage,
    ResolutionStartRequest,
    ResolutionStats,
)
from research_engine.resolution import matching, store

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
    state = jobs.latest_state(conn, job_id)
    saved = state.get("stats")
    stats = {name: 0 for name in ResolutionStats.model_fields}
    if isinstance(saved, dict):
        stats |= {name: value for name, value in saved.items()
                  if name in stats and isinstance(value, int)}  # fmt: skip

    def save(stage: str, done: int, total: int | None, **extra: JsonValue) -> None:
        state: JsonObject = {"stage": stage, "stats": dict[str, JsonValue](stats), **extra}
        with repository.atomic(conn):
            jobs.set_progress(conn, job_id, done, total)
            jobs.checkpoint(conn, job_id, state)

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
                jobs.set_progress(conn, job_id, index, len(pairs))
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
    jobs.finish_job(conn, job_id, {"stage": "done", "stats": dict[str, JsonValue](stats)}, ACTOR)
    return True


class ResolutionService(jobs.BackgroundJobs[ResolutionRun, ResolutionRunPage]):
    """Owns the resolution jobs this worker runs (threads, control and recovery: `jobs`)."""

    kind = JOB_KIND
    actor = ACTOR

    async def start(self, request: ResolutionStartRequest) -> ResolutionRunPage:
        return await self.start_project(request.project_id)

    def plan(self, conn: sqlite3.Connection, project_id: str | None) -> tuple[list[str], list[str]]:
        return store.plan_runs(conn, project_id, actor=ACTOR)

    def run(self, conn: sqlite3.Connection, job_id: str, should_stop: Callable[[], bool]) -> bool:
        return run_resolution(conn, job_id, should_stop)

    def view(self, conn: sqlite3.Connection, job_id: str) -> ResolutionRun:
        return store.get_resolution_run(conn, job_id)

    def page(self, conn: sqlite3.Connection, job_ids: Sequence[str]) -> ResolutionRunPage:
        return store.runs_by_id(conn, job_ids)
