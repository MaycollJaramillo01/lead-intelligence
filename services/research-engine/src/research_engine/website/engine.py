"""Website audits as a resumable background job (phase 08).

    audit (one crawled site at a time, in site order; checkpoint after each; idempotent)

A job audits the latest finished crawl of every site of one project, or of the sites it was given.
Each site is one transaction; auditing a crawl already audited with the same versions writes
nothing, so a paused, cancelled or interrupted job resumes without duplicating an audit. A job a
dead worker left running is paused at startup, never resumed silently (D-027).
"""

import sqlite3
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Final

from pydantic import JsonValue

from research_engine import jobs, repository
from research_engine.domain import (
    EventLevel,
    Job,
    JobStatus,
    JsonObject,
    WebsiteAuditStatus,
    WebsiteFindingResult,
    WebsiteRun,
    WebsiteRunPage,
    WebsiteStartRequest,
    WebsiteStats,
    utc_now,
)
from research_engine.website import rules, store

JOB_KIND: Final = store.JOB_KIND
ACTOR: Final = store.ACTOR


def run_website(
    conn: sqlite3.Connection,
    data_dir: Path,
    job_id: str,
    should_stop: Callable[[], bool],
    ruleset: rules.Ruleset = rules.DEFAULT_RULESET,
) -> bool:
    """Runs or resumes one job to its end. False when it stopped early (paused or cancelled)."""
    job = repository.get(conn, Job, job_id)
    if job.status not in (JobStatus.QUEUED, JobStatus.RUNNING):
        return False
    project_id = str(job.params_json["project_id"])
    chosen = job.params_json.get("sites")
    sites = [site for site in chosen if isinstance(site, str)] if isinstance(chosen, list) else []
    jobs.mark_running(conn, job_id, ACTOR)
    state = jobs.latest_state(conn, job_id)
    saved = state.get("stats")
    stats = {name: 0 for name in WebsiteStats.model_fields}
    if isinstance(saved, dict):
        stats |= {name: value for name, value in saved.items()
                  if name in stats and isinstance(value, int)}  # fmt: skip
    resume_at = state.get("cursor")
    cursor = resume_at if isinstance(resume_at, str) else None

    crawls = store.crawled_sites(conn, project_id, sites)
    todo = [crawl for crawl in crawls if cursor is None or crawl.site > cursor]
    stats["sites"] = len(crawls)
    if cursor is None:
        with repository.atomic(conn):
            jobs.write_event(conn, job_id, None, "stage_started", EventLevel.INFO,
                             {"stage": "audit", "sites": len(crawls)})  # fmt: skip
    done = len(crawls) - len(todo)
    for crawl in todo:
        if should_stop():
            return False
        result = store.audit_site(conn, data_dir, project_id, crawl, job_id, utc_now(), ruleset)
        if result is None:
            stats["unchanged"] += 1
        else:
            counts = store.result_counts(result)
            stats["audited"] += 1
            stats["partial"] += result.status is WebsiteAuditStatus.PARTIAL
            stats["empty"] += result.status is WebsiteAuditStatus.EMPTY
            stats["pages_analyzed"] += result.pages_analyzed
            stats["pages_missing"] += result.pages_expected - result.pages_analyzed
            stats["failed"] += counts[WebsiteFindingResult.FAIL]
            stats["warnings"] += counts[WebsiteFindingResult.WARNING]
            stats["unknown"] += counts[WebsiteFindingResult.UNKNOWN]
        done += 1
        checkpoint: JsonObject = {
            "stage": "audit", "stats": dict[str, JsonValue](stats), "cursor": crawl.site,
        }  # fmt: skip
        with repository.atomic(conn):
            jobs.set_progress(conn, job_id, done, len(crawls))
            jobs.checkpoint(conn, job_id, checkpoint)
    if repository.get(conn, Job, job_id).status is not JobStatus.RUNNING:
        return False  # paused or cancelled at the very end: the audits are idempotent
    jobs.finish_job(conn, job_id, {"stage": "done", "stats": dict[str, JsonValue](stats)}, ACTOR)
    return True


class WebsiteService(jobs.BackgroundJobs[WebsiteRun, WebsiteRunPage]):
    """Owns the website audit jobs this worker runs (threads, control and recovery: `jobs`)."""

    kind = JOB_KIND
    actor = ACTOR

    def __init__(self, data_dir: Path | None, connect: Callable[[], sqlite3.Connection]) -> None:
        super().__init__(connect)
        self._data_dir = data_dir

    async def start(self, request: WebsiteStartRequest) -> WebsiteRunPage:
        created, listed = await self._in_store(
            lambda conn: store.plan_runs(conn, request.project_id, request.sites, actor=ACTOR)
        )
        for job_id in created:
            self._spawn(job_id)
        return await self._in_store(lambda conn: self.page(conn, [*created, *listed]))

    def plan(self, conn: sqlite3.Connection, project_id: str | None) -> tuple[list[str], list[str]]:
        return store.plan_runs(conn, project_id, [], actor=ACTOR)

    def run(self, conn: sqlite3.Connection, job_id: str, should_stop: Callable[[], bool]) -> bool:
        if self._data_dir is None:
            raise store.WebsiteError("the data directory is not configured")
        return run_website(conn, self._data_dir, job_id, should_stop)

    def view(self, conn: sqlite3.Connection, job_id: str) -> WebsiteRun:
        return store.get_website_run(conn, job_id)

    def page(self, conn: sqlite3.Connection, job_ids: Sequence[str]) -> WebsiteRunPage:
        return store.runs_by_id(conn, job_ids)
