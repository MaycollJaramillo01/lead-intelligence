"""The discovery engine: it runs campaigns, the adapters only answer questions.

One job per start, one `source_run` per selected source, one `job_task` per source run. A source is
worked page by page and every page is persisted in a single transaction together with its
checkpoint, so a run that is paused, cancelled or interrupted by a restart resumes exactly where it
stopped and never asks a source for the same page twice.

What the engine owns, so no adapter has to think about it:

- provenance: `source_queries` -> `acquired_pages` -> `raw_records` -> `discovery_candidates`;
- limits: candidates and requests per run, records and pages per source, a request rate and a daily
  request budget per connector;
- failures: the `DiscoveryErrorCode` decides whether an attempt is retried (transient only), with
  exponential backoff and at least the `Retry-After` the source asked for;
- control: pause, resume and cancel are cooperative and observed between pages;
- light duplicate detection: a candidate repeating a `dedup_key` already seen in the campaign points
  at the first one. Nothing is merged - entity resolution is phase 06.

ponytail: one page at a time, sources in priority order. Add per-source workers only when a real
run turns out to be limited by waiting rather than by the sources' own rate limits.
"""

import asyncio
import contextlib
import hashlib
import logging
import random
import re
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import httpx
import orjson

from research_engine import jobs, repository
from research_engine.discovery import registry
from research_engine.discovery.sources.base import (
    DiscoveryError,
    DiscoveryPage,
    DiscoverySource,
    SourceContext,
    SourceRequest,
)
from research_engine.discovery.sources.file_import import KEY as FILE_IMPORT_KEY
from research_engine.domain import (
    AcquiredPage,
    AcquisitionMethod,
    AuditAction,
    Campaign,
    CriteriaProblem,
    DiscoveryCandidate,
    DiscoveryControlRequest,
    DiscoveryCriteria,
    DiscoveryRun,
    DiscoveryStartRequest,
    EntityType,
    EventLevel,
    ImportFormat,
    Job,
    JobCheckpoint,
    JobStatus,
    JobTask,
    JsonObject,
    RawRecord,
    RunControlAction,
    SourceCatalog,
    SourceConnector,
    SourceEstimate,
    SourceHealth,
    SourceInfo,
    SourceKind,
    SourceQuery,
    SourceRun,
    SourceRunStatus,
    SourceSelection,
    TaskAttempt,
    format_ts,
    new_id,
    sort_key,
    utc_now,
)
from research_engine.storage import relative_data_path

logger = logging.getLogger(__name__)

JOB_KIND: Final = repository.DISCOVERY_JOB_KIND
ACTOR: Final = "discovery-engine"
STAGE: Final = "discover"
MAX_ATTEMPTS: Final = 4
BACKOFF_BASE_S: Final = 2.0
MAX_BACKOFF_S: Final = 60.0
#: Never sleep longer than this in one go, so pause and cancel stay responsive.
SLEEP_SLICE_S: Final = 1.0
IMPORT_DIR: Final = "imports"
_SAFE_NAME: Final = re.compile(r"[^A-Za-z0-9._-]+")

#: How a source's records were acquired, for `raw_records.method`.
_METHOD: Final[dict[SourceKind, AcquisitionMethod]] = {
    SourceKind.FILE_IMPORT: AcquisitionMethod.FILE_IMPORT,
    SourceKind.MANUAL_SEED: AcquisitionMethod.MANUAL_ENTRY,
    SourceKind.OFFICIAL_API: AcquisitionMethod.API_REQUEST,
    SourceKind.PLATFORM_API: AcquisitionMethod.API_REQUEST,
    SourceKind.PUBLIC_DATASET: AcquisitionMethod.HTTP_FETCH,
    SourceKind.DIRECTORY: AcquisitionMethod.HTTP_FETCH,
    SourceKind.PUBLIC_REGISTRY: AcquisitionMethod.HTTP_FETCH,
    SourceKind.WEB_SEARCH: AcquisitionMethod.HTTP_FETCH,
    SourceKind.SYNTHETIC: AcquisitionMethod.SYNTHETIC,
}


class DiscoveryStartError(ValueError):
    """The run cannot start: the campaign, its criteria or a source input is not usable."""


@dataclass(frozen=True, slots=True)
class EngineSettings:
    data_dir: Path | None
    user_agent: str
    request_timeout_s: float = 30.0
    #: True in tests and offline runs: sources that need the network refuse instead of trying.
    offline: bool = False
    #: Replaces the HTTP stack. Only tests set it, to replay recorded responses.
    transport: httpx.AsyncBaseTransport | None = None


def _as_object(value: object) -> JsonObject:
    return dict(value) if isinstance(value, dict) else {}


def _as_int(value: object, fallback: int) -> int:
    return value if isinstance(value, int) and value > 0 else fallback


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and value > 0 else None


def _text_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


@dataclass
class _Progress:
    """The counters a checkpoint carries, so a resume continues instead of recounting."""

    cursor: str | None = None
    pages_done: int = 0
    records_seen: int = 0
    candidates: int = 0
    duplicates: int = 0
    requests: int = 0
    adapter: JsonObject = field(default_factory=dict)

    def as_state(self, connector_key: str) -> JsonObject:
        return {
            "connector_key": connector_key,
            "cursor": self.cursor,
            "pages_done": self.pages_done,
            "records_seen": self.records_seen,
            "candidates": self.candidates,
            "duplicates": self.duplicates,
            "requests": self.requests,
            "adapter": self.adapter,
        }

    @classmethod
    def from_state(cls, state: Mapping[str, object] | None) -> "_Progress":
        if not state:
            return cls()
        cursor = state.get("cursor")
        return cls(
            cursor=cursor if isinstance(cursor, str) else None,
            pages_done=_as_int(state.get("pages_done"), 0),
            records_seen=_as_int(state.get("records_seen"), 0),
            candidates=_as_int(state.get("candidates"), 0),
            duplicates=_as_int(state.get("duplicates"), 0),
            requests=_as_int(state.get("requests"), 0),
            adapter=_as_object(state.get("adapter")),
        )


class _RateLimiter:
    """One gate per connector: at most N requests a minute, with a little jitter on top."""

    def __init__(self) -> None:
        self._next: dict[str, float] = {}

    def delay_for(self, key: str, requests_per_minute: int, now: float) -> float:
        interval = 60.0 / max(1, requests_per_minute)
        start = max(now, self._next.get(key, 0.0))
        self._next[key] = start + interval * random.uniform(1.0, 1.15)  # noqa: S311 - jitter
        return max(0.0, start - now)


def _digest(payload: JsonObject) -> str:
    return hashlib.sha256(orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)).hexdigest()


def _safe_filename(name: str) -> str:
    bare = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return _SAFE_NAME.sub("_", bare).strip("._")[:120] or "import.csv"


def _backoff(attempt: int, asked: float | None) -> float:
    """Exponential with jitter, but never shorter than the wait the source asked for."""
    computed = min(MAX_BACKOFF_S, BACKOFF_BASE_S**attempt) * random.uniform(0.5, 1.0)  # noqa: S311
    return min(MAX_BACKOFF_S, max(computed, asked or 0.0))


@dataclass(slots=True)
class _Unit:
    """One source inside a run: its rows, its limits and where it got to."""

    job_id: str
    connector: SourceConnector
    run: SourceRun
    task_id: str
    selection: SourceSelection
    run_input: JsonObject
    page_size: int
    requests_per_minute: int
    daily_budget: int | None
    daily_spent: int
    progress: _Progress
    last_error: DiscoveryError | None = None


@dataclass(slots=True)
class _Plan:
    """Everything one execution of a job needs, read from the store in one pass."""

    job_id: str
    project_id: str
    campaign_id: str
    criteria: DiscoveryCriteria
    units: list[_Unit]
    spent_requests: int

    def candidates_total(self) -> int:
        return sum(unit.progress.candidates for unit in self.units)


class DiscoveryService:
    """Owns the running jobs. One instance per worker; every store access runs on a thread."""

    def __init__(self, settings: EngineSettings, connect: Callable[[], sqlite3.Connection]) -> None:
        self._settings = settings
        self._connect = connect
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._wake: dict[str, asyncio.Event] = {}
        self._limiter = _RateLimiter()

    def _new_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self._settings.request_timeout_s,
            headers={"User-Agent": self._settings.user_agent},
            follow_redirects=True,
            transport=self._settings.transport,
        )

    # ---- store access --------------------------------------------------------------------

    async def _in_store[T](self, action: Callable[[sqlite3.Connection], T]) -> T:
        def run() -> T:
            conn = self._connect()
            try:
                return action(conn)
            finally:
                conn.close()

        return await asyncio.to_thread(run)

    # ---- catalogue and validation --------------------------------------------------------

    async def catalog(self, project_id: str | None) -> SourceCatalog:
        """Every registered source with its limits and whether it could run right now."""
        rows = await self._in_store(lambda conn: _catalog_rows(conn, project_id))
        sources: list[SourceInfo] = []
        for connector, config, last in rows:
            health, detail = await registry.health_of(connector, config.config_json)
            sources.append(
                SourceInfo(
                    connector_id=connector.id,
                    metadata=registry.source_for(connector.key).metadata,
                    enabled=connector.enabled,
                    health=health,
                    health_detail=detail,
                    requests_per_minute=config.requests_per_minute,
                    daily_request_budget=config.daily_request_budget,
                    max_concurrency=config.max_concurrency,
                    last_run_at=last.started_at if last else None,
                    last_run_status=last.status if last else None,
                    last_error_code=last.error_code if last else None,
                    last_error_message=last.error_message if last else None,
                )
            )
        return SourceCatalog(sources=sources, checked_at=utc_now())

    async def validate(
        self, criteria: DiscoveryCriteria, catalog: SourceCatalog, project_id: str | None
    ) -> list[CriteriaProblem]:
        """Validates a campaign with its own settings applied, not only the connector defaults."""
        merged = await self._in_store(lambda conn: _selection_configs(conn, criteria, project_id))
        readiness = {
            key: await registry.health_of(connector, config)
            for key, (connector, config) in merged.items()
        }
        by_key = {info.metadata.key: info for info in catalog.sources}
        return registry.check_criteria(criteria, by_key, readiness)

    async def estimates(
        self, criteria: DiscoveryCriteria, catalog: SourceCatalog
    ) -> list[SourceEstimate]:
        """One marked estimate per selected source. A source that cannot count says so."""
        by_key = {info.metadata.key: info for info in catalog.sources}
        results: list[SourceEstimate] = []
        client: httpx.AsyncClient | None = None
        try:
            for selection in criteria.sources:
                info = by_key.get(selection.connector_key)
                if info is None:
                    continue
                source = registry.source_for(selection.connector_key)
                ignored = registry.unsupported_fields(criteria, info.metadata)
                if info.health is not SourceHealth.READY:
                    results.append(
                        SourceEstimate(
                            connector_key=info.metadata.key,
                            connector_name=info.metadata.name,
                            supported=False,
                            value=None,
                            basis=info.health_detail or "this source cannot run as configured",
                            unsupported_fields=ignored,
                        )
                    )
                    continue
                if client is None and not self._settings.offline:
                    client = self._new_client()
                request = SourceRequest(
                    criteria=criteria,
                    selection=selection,
                    page_size=info.metadata.default_page_size,
                    run_input={},
                )
                context = SourceContext(
                    data_dir=self._settings.data_dir, http=client, now=utc_now()
                )
                try:
                    estimate = await source.estimate(request, context)
                except DiscoveryError as error:
                    results.append(
                        SourceEstimate(
                            connector_key=info.metadata.key,
                            connector_name=info.metadata.name,
                            supported=True,
                            value=None,
                            basis=f"{error.code.value}: {error.message[:250]}",
                            unsupported_fields=ignored,
                        )
                    )
                    continue
                results.append(
                    SourceEstimate(
                        connector_key=info.metadata.key,
                        connector_name=info.metadata.name,
                        supported=True,
                        value=estimate.value,
                        basis=estimate.basis,
                        unsupported_fields=ignored,
                    )
                )
        finally:
            if client is not None:
                await client.aclose()
        return results

    # ---- public surface ------------------------------------------------------------------

    async def start(self, request: DiscoveryStartRequest) -> DiscoveryRun:
        """Creates the job, its source runs and their tasks, then runs it in the background."""
        campaign = await self._in_store(lambda conn: _live_campaign(conn, request.campaign_id))
        catalog = await self.catalog(campaign.project_id)
        criteria = criteria_of(campaign)
        problems = registry.blocking(await self.validate(criteria, catalog, campaign.project_id))
        if problems:
            raise DiscoveryStartError("; ".join(problem.message for problem in problems))
        job_id = await self._in_store(lambda conn: self._plan(conn, request, campaign, catalog))
        self._spawn(job_id)
        return await self.run(job_id)

    async def control(self, request: DiscoveryControlRequest) -> DiscoveryRun:
        """Pause, resume or cancel. Written to the store first, so a restart keeps the decision."""

        def apply(conn: sqlite3.Connection) -> None:
            jobs.live_job(conn, request.job_id, JOB_KIND)
            jobs.apply_control(conn, request.job_id, request.action, ACTOR)

        await self._in_store(apply)
        if request.action is RunControlAction.RESUME:
            self._spawn(request.job_id)
        else:
            self._nudge(request.job_id)
        return await self.run(request.job_id)

    async def run(self, job_id: str) -> DiscoveryRun:
        return await self._in_store(lambda conn: repository.get_discovery_run(conn, job_id))

    async def idle(self, job_id: str) -> None:
        """Waits until this run's background loop has stopped, if it is running in this worker."""
        task = self._tasks.get(job_id)
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def recover(self) -> int:
        """At startup: a job left running belonged to a worker that is gone. Pause it, say so."""
        return await self._in_store(lambda conn: jobs.pause_interrupted(conn, JOB_KIND, ACTOR))

    async def shutdown(self) -> None:
        """Stops the loops and leaves every in-flight run paused, never half-written."""
        job_ids = list(self._tasks)
        for task in self._tasks.values():
            task.cancel()
        for task in list(self._tasks.values()):
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
        for job_id in job_ids:
            with contextlib.suppress(Exception):
                await self._in_store(
                    lambda conn: jobs.pause_job(conn, job_id, "worker_stopping", ACTOR)  # noqa: B023
                )

    # ---- background execution ------------------------------------------------------------

    def _spawn(self, job_id: str) -> None:
        existing = self._tasks.get(job_id)
        if existing is not None and not existing.done():
            return
        self._wake[job_id] = asyncio.Event()
        task = asyncio.create_task(self._execute(job_id), name=f"discovery:{job_id}")
        self._tasks[job_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(job_id, None))

    def _nudge(self, job_id: str) -> None:
        if (event := self._wake.get(job_id)) is not None:
            event.set()

    async def _sleep(self, job_id: str, seconds: float) -> None:
        """Waits, but wakes up at once when this job is paused or cancelled."""
        event = self._wake.get(job_id)
        remaining = seconds
        while remaining > 0:
            slice_s = min(SLEEP_SLICE_S, remaining)
            if event is None:
                await asyncio.sleep(slice_s)
            else:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(event.wait(), slice_s)
                if event.is_set():
                    event.clear()
                    return
            remaining -= slice_s

    async def _execute(self, job_id: str) -> None:
        client: httpx.AsyncClient | None = None
        try:
            if not self._settings.offline:
                client = self._new_client()
            await self._run_job(job_id, client)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("discovery job failed", extra={"job_id": job_id})
            await self._in_store(lambda conn: jobs.fail_job(conn, job_id, "internal error", ACTOR))
        finally:
            self._wake.pop(job_id, None)
            if client is not None:
                await client.aclose()

    async def _status(self, job_id: str) -> JobStatus:
        return await self._in_store(lambda conn: repository.get(conn, Job, job_id).status)

    async def _run_job(self, job_id: str, client: httpx.AsyncClient | None) -> None:
        plan = await self._in_store(lambda conn: _load_plan(conn, job_id))
        if plan is None:
            return
        await self._in_store(lambda conn: jobs.mark_running(conn, job_id, ACTOR))
        for unit in plan.units:
            if await self._status(job_id) is not JobStatus.RUNNING:
                break
            if plan.spent_requests >= plan.criteria.limits.max_requests:
                await self._event(
                    job_id, None, "limit_reached", EventLevel.INFO, {"limit": "max_requests"}
                )
                break
            plan.spent_requests += await self._run_source(plan, unit, client)
        await self._in_store(lambda conn: _settle_job(conn, job_id))

    async def _run_source(self, plan: _Plan, unit: _Unit, client: httpx.AsyncClient | None) -> int:
        """Pages one source until it ends, hits a limit, fails, or the job stops."""
        source = registry.source_for(unit.connector.key)
        request = SourceRequest(
            criteria=plan.criteria,
            selection=unit.selection,
            page_size=unit.page_size,
            run_input=unit.run_input,
        )
        attempt = await self._in_store(lambda conn: _begin_attempt(conn, unit))
        spent = 0
        failure: DiscoveryError | None = None
        # False until the source itself is done (exhausted or capped): anything else - a pause, a
        # cancellation, the worker stopping - leaves the source resumable, never "succeeded".
        completed = False
        try:
            while await self._status(plan.job_id) is JobStatus.RUNNING:
                if not await self._within_limits(plan, unit, spent):
                    completed = True
                    break
                delay = self._limiter.delay_for(
                    unit.connector.key,
                    unit.requests_per_minute,
                    asyncio.get_running_loop().time(),
                )
                if delay > 0:
                    await self._sleep(plan.job_id, delay)
                    if await self._status(plan.job_id) is not JobStatus.RUNNING:
                        break
                page, used = await self._fetch(plan, unit, source, request, client)
                spent += used
                unit.progress.requests += used
                if page is None:
                    failure = unit.last_error
                    break
                await self._in_store(lambda conn: _persist_page(conn, plan, unit, page))  # noqa: B023
                if unit.progress.cursor is None:
                    await self._event(
                        plan.job_id,
                        unit.task_id,
                        "source_exhausted",
                        EventLevel.INFO,
                        {"connector_key": unit.connector.key},
                    )
                    completed = True
                    break
        finally:
            await self._in_store(
                lambda conn: _finish_attempt(conn, unit, attempt, failure, completed=completed)
            )
        return spent

    async def _within_limits(self, plan: _Plan, unit: _Unit, spent: int) -> bool:
        limits = plan.criteria.limits
        progress = unit.progress
        reached = {
            "max_pages_per_source": progress.pages_done >= limits.max_pages_per_source,
            "max_records_for_source": unit.selection.max_records is not None
            and progress.records_seen >= unit.selection.max_records,
            "max_candidates": plan.candidates_total() >= limits.max_candidates,
            "max_requests": plan.spent_requests + spent >= limits.max_requests,
            "daily_request_budget": unit.daily_budget is not None
            and unit.daily_spent >= unit.daily_budget,
        }
        for limit, hit in reached.items():
            if hit:
                await self._event(
                    plan.job_id,
                    unit.task_id,
                    "limit_reached",
                    EventLevel.INFO,
                    {"limit": limit, "connector_key": unit.connector.key},
                )
                return False
        return True

    async def _fetch(
        self,
        plan: _Plan,
        unit: _Unit,
        source: DiscoverySource,
        request: SourceRequest,
        client: httpx.AsyncClient | None,
    ) -> tuple[DiscoveryPage | None, int]:
        """One page, retried only for transient failures. Returns the page and requests spent."""
        context = SourceContext(data_dir=self._settings.data_dir, http=client, now=utc_now())
        used = 0
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                page = await source.discover(request, unit.progress.cursor, context)
            except DiscoveryError as error:
                used += 1
                unit.last_error = error
                unit.daily_spent += 1
                # a failed attempt is an error event even when it will be retried; the store
                # only lets an error carry a code, and the code is what the operator needs
                await self._event(
                    plan.job_id,
                    unit.task_id,
                    "page_failed",
                    EventLevel.ERROR,
                    {
                        "connector_key": unit.connector.key,
                        "attempt": attempt,
                        "message": error.message[:500],
                        "cursor": unit.progress.cursor,
                    },
                    error_code=error.code.value,
                    retryable=error.retryable,
                )
                if not error.retryable or attempt == MAX_ATTEMPTS:
                    return None, used
                await self._sleep(plan.job_id, _backoff(attempt, error.retry_after_s))
                if await self._status(plan.job_id) is not JobStatus.RUNNING:
                    return None, used
            else:
                used += 1
                unit.last_error = None
                unit.daily_spent += 1
                return page, used
        return None, used

    async def _event(
        self,
        job_id: str,
        task_id: str | None,
        name: str,
        level: EventLevel,
        detail: JsonObject | None = None,
        *,
        error_code: str | None = None,
        retryable: bool | None = None,
    ) -> None:
        await self._in_store(
            lambda conn: jobs.write_event(
                conn, job_id, task_id, name, level, detail, error_code, retryable
            )
        )

    # ---- planning ------------------------------------------------------------------------

    def _plan(
        self,
        conn: sqlite3.Connection,
        request: DiscoveryStartRequest,
        campaign: Campaign,
        catalog: SourceCatalog,
    ) -> str:
        """Creates every row the run needs, in one transaction, and returns the new job id."""
        criteria = criteria_of(campaign)
        by_key = {info.metadata.key: info for info in catalog.sources}
        wanted = set(request.connector_keys)
        selections = [
            selection
            for selection in sorted(criteria.sources, key=lambda item: -item.priority)
            if not wanted or selection.connector_key in wanted
        ]
        if not selections:
            raise DiscoveryStartError("none of the requested sources belongs to this campaign")
        now = utc_now()
        job = Job(
            id=new_id(),
            project_id=campaign.project_id,
            kind=JOB_KIND,
            status=JobStatus.QUEUED,
            priority=0,
            params_json={
                "campaign_id": campaign.id,
                "connector_keys": [selection.connector_key for selection in selections],
            },
            idempotency_key=None,
            progress_done=0,
            progress_total=None,
            requested_at=now,
            started_at=None,
            finished_at=None,
            created_at=now,
            updated_at=now,
        )
        with repository.atomic(conn):
            repository.insert(conn, job)
            for selection in selections:
                info = by_key[selection.connector_key]
                config = registry.effective_config(
                    conn,
                    repository.get(conn, SourceConnector, info.connector_id),
                    campaign.project_id,
                )
                run_id = new_id()
                run_input = self._materialise_input(
                    run_id, selection.connector_key, request.inputs.get(selection.connector_key, {})
                )
                page_size = max(
                    1,
                    min(
                        info.metadata.default_page_size,
                        selection.max_records or criteria.limits.max_candidates,
                    ),
                )
                repository.insert(
                    conn,
                    SourceRun(
                        id=run_id,
                        project_id=campaign.project_id,
                        campaign_id=campaign.id,
                        connector_id=info.connector_id,
                        connector_version=info.metadata.version,
                        config_id=config.config_id,
                        job_id=job.id,
                        status=SourceRunStatus.QUEUED,
                        effective_config_json={
                            "connector_key": selection.connector_key,
                            "connector_version": info.metadata.version,
                            "criteria": criteria.model_dump(mode="json"),
                            "selection": selection.model_dump(mode="json"),
                            "page_size": page_size,
                            "run_input": run_input,
                            "requests_per_minute": config.requests_per_minute,
                            "daily_request_budget": config.daily_request_budget,
                        },
                        started_at=None,
                        finished_at=None,
                        error_code=None,
                        error_message=None,
                        created_at=now,
                        updated_at=now,
                    ),
                    JobTask(
                        id=new_id(),
                        job_id=job.id,
                        stage=STAGE,
                        task_key=selection.connector_key,
                        status=JobStatus.QUEUED,
                        attempt_count=0,
                        max_attempts=MAX_ATTEMPTS,
                        created_at=now,
                        updated_at=now,
                    ),
                )
            repository.audit(conn, AuditAction.CREATE, EntityType.JOB, job.id, actor=ACTOR)
        return job.id

    def _materialise_input(self, run_id: str, connector_key: str, given: JsonObject) -> JsonObject:
        """Turns a run's input into something a resumed run can still use.

        An imported file is written once under `imports/<run>/` and only its path is stored, so the
        text never travels twice and the bytes a candidate came from stay on disk next to it.
        """
        if connector_key != FILE_IMPORT_KEY:
            return dict(given)
        content = given.get("content")
        if not isinstance(content, str) or not content.strip():
            raise DiscoveryStartError("pick a file before starting a run that imports one")
        if self._settings.data_dir is None:
            raise DiscoveryStartError("the data directory is not configured")
        filename = _safe_filename(str(given.get("filename") or "import.csv"))
        target = self._settings.data_dir / IMPORT_DIR / run_id / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8")
        target.write_bytes(data)
        raw_format = given.get("format")
        known = isinstance(raw_format, str) and raw_format in {item.value for item in ImportFormat}
        return {
            "artifact_path": relative_data_path(self._settings.data_dir, target),
            "filename": filename,
            "format": raw_format if known else None,
            "mapping": given.get("mapping"),
            "sha256": hashlib.sha256(data).hexdigest(),
            "byte_size": len(data),
        }


# ---- reading the plan back ----------------------------------------------------------------------


def criteria_of(campaign: Campaign) -> DiscoveryCriteria:
    try:
        return DiscoveryCriteria.model_validate(campaign.criteria_json)
    except ValueError as exc:
        raise DiscoveryStartError(f"the campaign's criteria are not valid: {exc}") from exc


def _live_campaign(conn: sqlite3.Connection, campaign_id: str) -> Campaign:
    campaign = repository.get(conn, Campaign, campaign_id)
    if campaign.deleted_at is not None:
        raise DiscoveryStartError("this campaign has been deleted")
    return campaign


def _selection_configs(
    conn: sqlite3.Connection, criteria: DiscoveryCriteria, project_id: str | None
) -> dict[str, tuple[SourceConnector, JsonObject]]:
    """Per selected source: its connector and the configuration it would actually see.

    The campaign's own settings sit on top of the connector's, so a per-campaign value (a seed
    list) counts as configured while a connector-level one (an API key) still has to be set up.
    """
    connectors = registry.sync_connectors(conn)
    merged: dict[str, tuple[SourceConnector, JsonObject]] = {}
    for selection in criteria.sources:
        connector = connectors.get(selection.connector_key)
        if connector is None:
            continue
        config = registry.effective_config(conn, connector, project_id)
        merged[selection.connector_key] = (
            connector,
            {**config.config_json, **selection.config_json},
        )
    return merged


def _catalog_rows(
    conn: sqlite3.Connection, project_id: str | None
) -> list[tuple[SourceConnector, registry.EffectiveConfig, SourceRun | None]]:
    connectors = registry.sync_connectors(conn)
    return [
        (
            connector,
            registry.effective_config(conn, connector, project_id),
            repository.last_source_run(conn, connector.id, project_id),
        )
        for connector in connectors.values()
    ]


def _load_plan(conn: sqlite3.Connection, job_id: str) -> _Plan | None:
    job = repository.get(conn, Job, job_id)
    if job.status not in (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.PAUSED):
        return None
    campaign_id = str(job.params_json.get("campaign_id"))
    criteria = criteria_of(repository.get(conn, Campaign, campaign_id))
    units: list[_Unit] = []
    spent = 0
    for run in repository.source_runs_of_job(conn, job_id):
        if run.status in (SourceRunStatus.SUCCEEDED, SourceRunStatus.CANCELLED):
            continue
        config = run.effective_config_json
        connector = repository.get(conn, SourceConnector, run.connector_id)
        task = repository.task_of(conn, job_id, connector.key)
        progress = _Progress.from_state(repository.latest_checkpoint(conn, task.id))
        spent += progress.requests
        units.append(
            _Unit(
                job_id=job_id,
                connector=connector,
                run=run,
                task_id=task.id,
                selection=SourceSelection.model_validate(_as_object(config.get("selection"))),
                run_input=_as_object(config.get("run_input")),
                page_size=_as_int(config.get("page_size"), 50),
                requests_per_minute=_as_int(config.get("requests_per_minute"), 30),
                daily_budget=_optional_int(config.get("daily_request_budget")),
                daily_spent=repository.requests_today(conn, connector.id),
                progress=progress,
            )
        )
    return _Plan(
        job_id=job_id,
        project_id=job.project_id,
        campaign_id=campaign_id,
        criteria=criteria,
        units=units,
        spent_requests=spent,
    )


# ---- store writes -------------------------------------------------------------------------------


def _requeue(conn: sqlite3.Connection, unit: _Unit) -> None:
    """A failed source is resumable: it goes back to queued before it runs again."""
    task = repository.get(conn, JobTask, unit.task_id)
    if task.status is JobStatus.FAILED:
        repository.change_status(conn, JobTask, task.id, JobStatus.QUEUED, actor=ACTOR)
    run = repository.get(conn, SourceRun, unit.run.id)
    if run.status is SourceRunStatus.FAILED:
        repository.change_status(conn, SourceRun, run.id, SourceRunStatus.QUEUED, actor=ACTOR)


def _begin_attempt(conn: sqlite3.Connection, unit: _Unit) -> TaskAttempt:
    now = utc_now()
    with repository.atomic(conn):
        _requeue(conn, unit)
        task = repository.get(conn, JobTask, unit.task_id)
        repository.change_status(conn, JobTask, task.id, JobStatus.RUNNING, actor=ACTOR)
        conn.execute(
            "UPDATE job_tasks SET attempt_count = ?, updated_at = ? WHERE id = ?",
            (task.attempt_count + 1, format_ts(now), task.id),
        )
        repository.change_status(conn, SourceRun, unit.run.id, SourceRunStatus.RUNNING, actor=ACTOR)
        if unit.run.started_at is None:
            conn.execute(
                "UPDATE source_runs SET started_at = ?, updated_at = ? WHERE id = ?",
                (format_ts(now), format_ts(now), unit.run.id),
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
    return attempt


def _finish_attempt(
    conn: sqlite3.Connection,
    unit: _Unit,
    attempt: TaskAttempt,
    failure: DiscoveryError | None,
    *,
    completed: bool,
) -> None:
    """Closes the attempt, the task and the source run with one outcome.

    `completed` is the only thing that can call a source successful: a run that merely stopped -
    paused, cancelled, or cut short by the worker exiting - stays resumable.
    """
    job_status = repository.get(conn, Job, unit.job_id).status
    if job_status is JobStatus.CANCELLED:
        task_status, run_status = JobStatus.CANCELLED, SourceRunStatus.CANCELLED
    elif failure is not None:
        task_status, run_status = JobStatus.FAILED, SourceRunStatus.FAILED
    elif completed:
        task_status, run_status = JobStatus.SUCCEEDED, SourceRunStatus.SUCCEEDED
    else:
        task_status, run_status = JobStatus.PAUSED, SourceRunStatus.PAUSED
    now = format_ts(utc_now())
    finished = task_status is not JobStatus.PAUSED
    with repository.atomic(conn):
        conn.execute(
            "UPDATE task_attempts SET status = ?, finished_at = ?, error_code = ?, "
            "error_message = ?, retryable = ? WHERE id = ?",
            (
                task_status.value,
                now,
                failure.code.value if failure else None,
                failure.message[:2000] if failure else None,
                int(failure.retryable) if failure else None,
                attempt.id,
            ),
        )
        repository.change_status(conn, JobTask, unit.task_id, task_status, actor=ACTOR)
        repository.change_status(conn, SourceRun, unit.run.id, run_status, actor=ACTOR)
        conn.execute(
            "UPDATE source_runs SET finished_at = ?, error_code = ?, error_message = ?, "
            "updated_at = ? WHERE id = ?",
            (
                now if finished else None,
                failure.code.value if failure else None,
                failure.message[:2000] if failure else None,
                now,
                unit.run.id,
            ),
        )


def _persist_page(conn: sqlite3.Connection, plan: _Plan, unit: _Unit, page: DiscoveryPage) -> None:
    """One page: its query, its acquisition, its raw records, its candidates and its checkpoint.

    All of it in one transaction, the checkpoint last, so a resume never repeats a page that was
    already stored and never skips one that was not.
    """
    now = utc_now()
    progress = unit.progress
    method = _METHOD.get(unit.connector.kind, AcquisitionMethod.HTTP_FETCH)
    imported = unit.run_input if unit.connector.key == FILE_IMPORT_KEY else {}
    with repository.atomic(conn):
        query = SourceQuery(
            id=new_id(),
            source_run_id=unit.run.id,
            query_json={
                "connector_key": unit.connector.key,
                "cursor": progress.cursor,
                "page_size": unit.page_size,
                "request_url": page.request_url,
            },
            cursor=progress.cursor,
            issued_at=now,
            result_count=len(page.candidates),
        )
        acquired = AcquiredPage(
            id=new_id(),
            source_run_id=unit.run.id,
            source_query_id=query.id,
            url=page.request_url or f"{unit.connector.key}:page",
            final_url=None,
            http_status=page.http_status,
            content_type=page.content_type,
            content_sha256=hashlib.sha256(page.body).hexdigest()
            if page.body is not None
            else _text_or_none(imported.get("sha256")),
            byte_size=len(page.body)
            if page.body is not None
            else _optional_int(imported.get("byte_size")),
            artifact_path=_text_or_none(imported.get("artifact_path")),
            fetched_at=now,
        )
        repository.insert(conn, query, acquired)
        for candidate in page.candidates:
            progress.records_seen += 1
            payload_sha = _digest(candidate.payload_json)
            if repository.raw_record_with_payload(conn, unit.run.id, payload_sha) is not None:
                progress.duplicates += 1
                continue
            raw = RawRecord(
                id=new_id(),
                source_run_id=unit.run.id,
                acquired_page_id=acquired.id,
                connector_id=unit.connector.id,
                method=method,
                external_id=candidate.external_id,
                source_url=candidate.source_url,
                payload_json=candidate.payload_json,
                payload_sha256=payload_sha,
                acquired_at=now,
                created_at=now,
            )
            first = repository.candidate_with_key(conn, plan.campaign_id, candidate.dedup_key)
            repository.insert(
                conn,
                raw,
                DiscoveryCandidate(
                    id=new_id(),
                    project_id=plan.project_id,
                    campaign_id=plan.campaign_id,
                    source_run_id=unit.run.id,
                    raw_record_id=raw.id,
                    connector_id=unit.connector.id,
                    dedup_key=candidate.dedup_key,
                    duplicate_of_id=first,
                    name_raw=candidate.name_raw,
                    name_normalized=sort_key(candidate.name_raw),
                    locality=candidate.locality,
                    region=candidate.region,
                    postal_code=candidate.postal_code,
                    country_code=candidate.country_code,
                    domain=candidate.domain,
                    website_url=candidate.website_url,
                    phone_raw=candidate.phone_raw,
                    category_raw=candidate.category_raw,
                    external_id=candidate.external_id,
                    source_url=candidate.source_url,
                    discovered_at=now,
                    created_at=now,
                ),
            )
            if first is None:
                progress.candidates += 1
            else:
                progress.duplicates += 1
        progress.pages_done += 1
        progress.cursor = page.next_cursor
        progress.adapter = registry.source_for(unit.connector.key).checkpoint(page.next_cursor)
        repository.insert(
            conn,
            JobCheckpoint(
                id=new_id(),
                job_id=unit.job_id,
                task_id=unit.task_id,
                state_json=progress.as_state(unit.connector.key),
                created_at=now,
            ),
        )
        conn.execute(
            "UPDATE jobs SET progress_done = ?, updated_at = ? WHERE id = ?",
            (plan.candidates_total(), format_ts(now), unit.job_id),
        )


def _settle_job(conn: sqlite3.Connection, job_id: str) -> None:
    """The pass is over: the job takes the outcome of its source runs, or stays paused."""
    job = repository.get(conn, Job, job_id)
    if job.status is not JobStatus.RUNNING:
        return
    runs = repository.source_runs_of_job(conn, job_id)
    statuses = {run.status for run in runs}
    if statuses & set(jobs.OPEN_RUNS):
        jobs.pause_job(conn, job_id, "stopped_before_finishing", ACTOR)
        return
    target = JobStatus.FAILED if statuses == {SourceRunStatus.FAILED} else JobStatus.SUCCEEDED
    done = repository.candidate_count_for_job(conn, job_id)
    now = format_ts(utc_now())
    with repository.atomic(conn):
        repository.change_status(conn, Job, job_id, target, actor=ACTOR)
        conn.execute(
            "UPDATE jobs SET finished_at = ?, progress_done = ?, progress_total = ?, "
            "updated_at = ? WHERE id = ?",
            (now, done, done, now, job_id),
        )
        jobs.write_event(conn, job_id, None, f"run_{target.value}", EventLevel.INFO)
