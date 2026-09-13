"""Contact intelligence as a resumable background job (phase 09).

    extract (batches of crawled pages, checkpoint after each)
      └─ validate (batches of canonical businesses: people, hypotheses, mail domains and statuses,
                   checkpoint after each; idempotent)

A job checks one project. Mail domains are looked up only when the run allows it and the worker is
not offline; everything else reads what is stored, and nothing contacts a business. Control is
written to the store first and read back between batches, so pause, cancel and a restart leave the
job resumable; a job a dead worker left running is paused at startup, never resumed (D-027).
"""

import sqlite3
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Final

from pydantic import JsonValue

from research_engine import jobs, repository
from research_engine.contacts import store
from research_engine.contacts.mail_dns import Lookup
from research_engine.domain import (
    ContactRun,
    ContactRunPage,
    ContactStartRequest,
    ContactStats,
    EventLevel,
    Job,
    JobStatus,
    JsonObject,
    ValidationStatus,
    utc_now,
)

JOB_KIND: Final = store.JOB_KIND
ACTOR: Final = store.ACTOR
EXTRACT_BATCH: Final = 100
VALIDATE_BATCH: Final = 200
_STATUS_STATS: Final = {
    ValidationStatus.LIKELY_VALID: "likely_valid",
    ValidationStatus.DOMAIN_VALID: "domain_valid",
    ValidationStatus.STRUCTURALLY_VALID: "structurally_valid",
    ValidationStatus.INVALID: "invalid",
    ValidationStatus.STALE: "stale",
    ValidationStatus.SUPPRESSED: "suppressed",
}


def run_contacts(
    conn: sqlite3.Connection,
    data_dir: Path | None,
    job_id: str,
    should_stop: Callable[[], bool],
    lookup: Lookup | None,
) -> bool:
    """Runs or resumes one job to its end. False when it stopped early (paused or cancelled)."""
    job = repository.get(conn, Job, job_id)
    if job.status not in (JobStatus.QUEUED, JobStatus.RUNNING):
        return False
    project_id = str(job.params_json["project_id"])
    network = lookup if job.params_json.get("check_mail_domains") is True else None
    jobs.mark_running(conn, job_id, ACTOR)
    state = jobs.latest_state(conn, job_id)
    saved = state.get("stats")
    stats = {name: 0 for name in ContactStats.model_fields}
    if isinstance(saved, dict):
        stats |= {name: value for name, value in saved.items()
                  if name in stats and isinstance(value, int)}  # fmt: skip

    def save(stage: str, done: int, total: int, cursor: str | None) -> None:
        checkpoint: JsonObject = {
            "stage": stage, "stats": dict[str, JsonValue](stats), "cursor": cursor,
        }  # fmt: skip
        with repository.atomic(conn):
            jobs.set_progress(conn, job_id, done, total)
            jobs.checkpoint(conn, job_id, checkpoint)

    def event(name: str, **detail: JsonValue) -> None:
        with repository.atomic(conn):
            jobs.write_event(conn, job_id, None, name, EventLevel.INFO, dict(detail))

    stage, resume_at = state.get("stage"), state.get("cursor")
    cursor = resume_at if isinstance(resume_at, str) else None
    if stage in (None, "extract"):
        sources = store.page_sources(conn, project_id)
        statuses = store.page_statuses(conn, project_id)
        todo = [source for source in sources if cursor is None or source.raw_record_id > cursor]
        done = len(sources) - len(todo)
        event("stage_started", stage="extract", pages=len(sources))
        for start in range(0, len(todo), EXTRACT_BATCH):
            if should_stop():
                return False
            batch = todo[start : start + EXTRACT_BATCH]
            found = store.extract_batch(conn, data_dir, batch, statuses, utc_now())
            stats["pages_scanned"] += found.pages
            stats["contacts_found"] += found.contacts
            stats["forms_found"] += found.forms
            stats["people_found"] += found.people
            done += len(batch)
            save("extract", done, len(sources), batch[-1].raw_record_id)
        cursor = None

    if should_stop():
        return False
    context = store.validation_context(conn, project_id)
    roots = store.root_ids(conn, project_id)
    if stage != "validate":
        cursor = None
        event("stage_started", stage="validate", businesses=len(roots),
              mail_domains="checked" if network else "not_checked")  # fmt: skip
    todo_roots: Sequence[str] = [root for root in roots if cursor is None or root > cursor]
    done = len(roots) - len(todo_roots)
    save("validate", done, len(roots), cursor)
    budget = [max(0, store.MAX_LOOKUPS_PER_RUN - stats["mail_domains_checked"])]
    for start in range(0, len(todo_roots), VALIDATE_BATCH):
        if should_stop():
            return False
        chunk = todo_roots[start : start + VALIDATE_BATCH]
        checked = store.validate_batch(conn, context, chunk, utc_now(), network, budget)
        stats["channels"] += checked.channels
        stats["changed"] += checked.changed
        stats["hypotheses"] += checked.hypotheses
        stats["mail_domains_checked"] += checked.checked
        stats["mail_domain_failures"] += checked.failures
        stats["ambiguous_people"] += checked.ambiguous
        for status, name in _STATUS_STATS.items():
            stats[name] += checked.statuses[status]
        done += len(chunk)
        save("validate", done, len(roots), chunk[-1])
    stats["businesses"] = len(roots)
    event("stage_finished", stage="validate", changed=stats["changed"])
    if repository.get(conn, Job, job_id).status is not JobStatus.RUNNING:
        return False  # paused or cancelled at the very end: the writes are idempotent
    jobs.finish_job(conn, job_id, {"stage": "done", "stats": dict[str, JsonValue](stats)}, ACTOR)
    return True


class ContactService(jobs.BackgroundJobs[ContactRun, ContactRunPage]):
    """Owns the contact jobs this worker runs (threads, control and recovery: `jobs`)."""

    kind = JOB_KIND
    actor = ACTOR

    def __init__(
        self,
        data_dir: Path | None,
        connect: Callable[[], sqlite3.Connection],
        lookup: Lookup | None,
    ) -> None:
        super().__init__(connect)
        self._data_dir = data_dir
        #: Mail-domain lookups; None when the worker is offline.
        self.lookup = lookup

    async def start(self, request: ContactStartRequest) -> ContactRunPage:
        created, listed = await self._in_store(
            lambda conn: store.plan_runs(
                conn, request.project_id, request.check_mail_domains, actor=ACTOR
            )
        )
        for job_id in created:
            self._spawn(job_id)
        return await self._in_store(lambda conn: self.page(conn, [*created, *listed]))

    def plan(self, conn: sqlite3.Connection, project_id: str | None) -> tuple[list[str], list[str]]:
        return store.plan_runs(conn, project_id, False, actor=ACTOR)

    def run(self, conn: sqlite3.Connection, job_id: str, should_stop: Callable[[], bool]) -> bool:
        return run_contacts(conn, self._data_dir, job_id, should_stop, self.lookup)

    def view(self, conn: sqlite3.Connection, job_id: str) -> ContactRun:
        return store.get_contact_run(conn, job_id)

    def page(self, conn: sqlite3.Connection, job_ids: Sequence[str]) -> ContactRunPage:
        return store.runs_by_id(conn, job_ids)
