"""The crawl engine: websites in, acquired pages and parsed content out.

One job per start, one `source_run` + `job_task` per site, one `crawl_urls` row per URL the crawler
decided something about. The frontier lives in the store, so a paused, cancelled or interrupted
crawl resumes from exactly the URLs still queued and never fetches or stores one twice.

A site is crawled in this order, within strict limits: robots.txt first (nothing else is requested
before its rules are known), then its sitemaps and the home page, then the pages they point to by
priority - contact, about and services pages before anything else, shallow before deep.

Scheduling (docs/crawling.md):

- `max(1, http_slots // per_host_concurrency)` sites run at once, each with `per_host_concurrency`
  workers draining its own `asyncio.PriorityQueue`;
- every request takes a slot of its host (`HostGate`: concurrency, spacing, cool-down) and one of
  the overall HTTP slots; renders take one of the separate browser slots, so a slow render never
  holds an HTTP slot;
- the number of slots comes from the machine: CPU count for HTTP, free memory too for browsers.

What this engine never does: decide whether a business is Latino-owned, a good fit or worth
contacting. It stores what the sites say, with the evidence to check it.
"""

import asyncio
import contextlib
import hashlib
import logging
import os
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit, urlunsplit

import orjson
from playwright.async_api import Error as PlaywrightError

from research_engine import jobs, repository
from research_engine.ai.resources import ResourceMonitor, memory
from research_engine.crawl.browser import BrowserPool, BrowserUnavailableError
from research_engine.crawl.extract import (
    decode,
    fallback_reason,
    gunzip,
    is_challenge,
    sha256,
    sitemap_locations,
    snapshot,
)
from research_engine.crawl.fetch import (
    HTML_TYPES,
    MAX_ATTEMPTS,
    ROBOTS_TYPES,
    SITEMAP_TYPES,
    Fetched,
    FetchPolicy,
    HostGate,
    HttpFetcher,
    guarded_client,
)
from research_engine.crawl.robots import ALLOW_ALL, DISALLOW_ALL, MAX_ROBOTS_BYTES, Robots, parse
from research_engine.crawl.urls import (
    ROBOTS_PRIORITY,
    SITEMAP_PRIORITY,
    canonical,
    host_of,
    in_scope,
    page_key,
    page_kind,
    path_and_query,
    priority,
    seed_url,
    site_of,
    skip_reason,
)
from research_engine.domain import (
    AcquiredPage,
    AcquisitionMethod,
    AuditAction,
    BrowserMode,
    Campaign,
    CrawlRun,
    CrawlRunListQuery,
    CrawlRunPage,
    CrawlSettings,
    CrawlStartRequest,
    CrawlTarget,
    CrawlUrl,
    CrawlUrlStatus,
    DiscoveryControlRequest,
    EntityType,
    EventLevel,
    Job,
    JobStatus,
    JobTask,
    JsonObject,
    PageKind,
    RawRecord,
    RunControlAction,
    SourceConnector,
    SourceKind,
    SourceRun,
    SourceRunStatus,
    TaskAttempt,
    format_ts,
    new_id,
    utc_now,
)
from research_engine.storage import relative_data_path

logger = logging.getLogger(__name__)

JOB_KIND: Final = repository.CRAWL_JOB_KIND
CONNECTOR_KEY: Final = "web.crawler"
CONNECTOR_NAME: Final = "Business websites (HTTP, browser when needed)"
CONNECTOR_VERSION: Final = "1.0.0"
#: The product token robots.txt groups are matched against.
PRODUCT_TOKEN: Final = "LeadIntelligence"  # noqa: S105 - a robots.txt name
ACTOR: Final = "crawl-engine"
STAGE: Final = "crawl"
ARTIFACT_DIR: Final = "artifacts/web"
MAX_PAGE_BYTES: Final = 5 * 1024 * 1024
MAX_ROBOTS_DOWNLOAD: Final = 2 * 1024 * 1024
MAX_SITEMAP_BYTES: Final = 10 * 1024 * 1024
MAX_SITEMAPS_PER_SITE: Final = 4
MAX_SITEMAP_URLS: Final = 200
MAX_URLS_PER_SITE: Final = 400
MAX_IN_FLIGHT_SHOWN: Final = 20
GIB: Final = 1024**3
ROOT_PRIORITY: Final = 100
#: A refusal of the whole site: nothing else is requested from it.
SITE_STOPPERS: Final = frozenset({"blocked", "unsafe_target", "dns_error", "robots_unreachable"})
#: Limits that end the rest of a site at once instead of URL by URL.
SITE_LIMITS: Final = frozenset({"request_budget", "time_budget", "page_budget", "byte_budget"})
#: robots.txt answered 4xx (or was not text): RFC 9309 says there are no rules.
_ROBOTS_ABSENT: Final = frozenset(
    {"not_found", "http_error", "blocked", "unsupported_type", "empty_response"}
)
_FINISHED_RUNS: Final = (
    SourceRunStatus.SUCCEEDED,
    SourceRunStatus.FAILED,
    SourceRunStatus.CANCELLED,
)
_ARTIFACT_SUFFIX: Final = {
    CrawlTarget.PAGE: ".html",
    CrawlTarget.ROBOTS: ".txt",
    CrawlTarget.SITEMAP: ".xml",
}
#: Columns a queued row may still change; `crawl_urls_final` refuses any change after that.
_RESULT_FIELDS: Final = tuple(
    name for name in CrawlUrl.model_fields if name not in ("id", "source_run_id", "created_at")
)


class CrawlStartError(ValueError):
    """The crawl cannot start: nothing to crawl, or an input is not usable."""


# ---- resources ----------------------------------------------------------------------------------


def available_memory() -> int | None:
    found = memory()
    return found[1] if found else None


def slots_for(cpus: int, free_memory: int | None) -> tuple[int, int]:
    """(HTTP slots, browser slots). Requests mostly wait on the network; renders need CPU and RAM.

    A headless render takes roughly a core and a few hundred MiB; two GiB stay free for the rest of
    the machine. Unknown free memory means one render at a time, too little means none.
    """
    http = max(2, min(16, cpus * 2))
    browsers = 1 if free_memory is None else max(0, min(2, (free_memory - 2 * GIB) // GIB))
    return http, min(browsers, max(1, cpus // 4))


@dataclass(frozen=True, slots=True)
class CrawlerSettings:
    data_dir: Path | None
    user_agent: str
    policy: FetchPolicy = field(default_factory=FetchPolicy)
    #: None: derived from the machine (`slots_for`).
    http_slots: int | None = None
    browser_slots: int | None = None
    #: Extra Chromium arguments; tests use one to find the processes they started.
    browser_args: tuple[str, ...] = ()


# ---- state of a running crawl -------------------------------------------------------------------


@dataclass(slots=True)
class _Job:
    job_id: str
    project_id: str
    settings: CrawlSettings
    fetcher: HttpFetcher
    gate: HostGate
    connector_id: str
    requests_used: int


@dataclass(slots=True)
class _Site:
    run: SourceRun
    task_id: str
    site: str
    frontier: asyncio.PriorityQueue[tuple[int, str, CrawlUrl]]
    known: set[str]
    pages_used: int
    bytes_used: int
    sitemaps_known: int
    content_seen: dict[str, str]
    started: float
    robots: Robots | None = None
    robots_ready: asyncio.Event = field(default_factory=asyncio.Event)
    stop_reason: str | None = None


@dataclass(frozen=True, slots=True)
class _Plan:
    project_id: str
    settings: CrawlSettings
    units: list[tuple[SourceRun, str]]
    requests_used: int
    connector_id: str


@dataclass(frozen=True, slots=True)
class _Render:
    """What a browser fallback produced, or why it produced nothing (`error`)."""

    reason: str
    payload: JsonObject | None = None
    html: bytes | None = None
    final_url: str | None = None
    status: int | None = None
    elapsed_ms: int | None = None
    screenshot: bytes | None = None
    error: str | None = None
    message: str | None = None


@dataclass(frozen=True, slots=True)
class _Cached:
    """An earlier fetch of the same URL that left validators: what a 304 lets us reuse."""

    validators: JsonObject
    payload: JsonObject
    raw_record_id: str
    content_sha256: str | None
    body_sha256: str | None
    byte_size: int | None
    content_type: str | None


def _digest(payload: JsonObject) -> str:
    return hashlib.sha256(orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)).hexdigest()


def robots_policy(error: str | None, text: str | None) -> tuple[Robots, str | None]:
    """RFC 9309 2.3.1: the rules when robots.txt was read, none when it is absent (4xx), and
    nothing allowed when it could not be read (5xx, 429, network), which stops the site."""
    if error is None:
        return parse(text or "", PRODUCT_TOKEN), None
    if error in _ROBOTS_ABSENT:
        return ALLOW_ALL, None
    if error in ("unsafe_target", "dns_error"):
        return DISALLOW_ALL, error
    return DISALLOW_ALL, "robots_unreachable"


def _nofollow(page: JsonObject, header: object) -> bool:
    meta = page.get("meta")
    directive = meta.get("robots") if isinstance(meta, dict) else None
    return "nofollow" in f"{directive or ''} {header or ''}".lower()


class CrawlService:
    """Owns the running crawl jobs of this worker. Every store access runs on a thread."""

    def __init__(
        self,
        settings: CrawlerSettings,
        connect: Callable[[], sqlite3.Connection],
        pressure: ResourceMonitor | None = None,
    ) -> None:
        self._settings = settings
        self._connect = connect
        #: While local AI works under memory pressure, each URL waits here first (phase 10).
        self._pressure = pressure
        cpu_http, cpu_browsers = slots_for(os.cpu_count() or 2, available_memory())
        self._http_slots = settings.http_slots or cpu_http
        browsers = cpu_browsers if settings.browser_slots is None else settings.browser_slots
        self._browser = (
            BrowserPool(
                user_agent=settings.user_agent,
                allow_private=settings.policy.allow_private,
                concurrency=browsers,
                launch_args=settings.browser_args,
            )
            if browsers > 0
            else None
        )
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._in_flight: dict[str, dict[str, str]] = {}

    async def _in_store[T](self, action: Callable[[sqlite3.Connection], T]) -> T:
        def run() -> T:
            conn = self._connect()
            try:
                return action(conn)
            finally:
                conn.close()

        return await asyncio.to_thread(run)

    # ---- public surface ------------------------------------------------------------------

    def active_renders(self) -> int | None:
        """Pages Chromium renders now; None when this crawler has no browser."""
        return None if self._browser is None else self._browser.rendering

    async def start(self, request: CrawlStartRequest) -> CrawlRun:
        manual: list[str] = []
        for given in request.urls:
            url = seed_url(given)
            if url is None:
                raise CrawlStartError(f"not a usable http(s) address: {given[:200]!r}")
            manual.append(url)
        if request.campaign_id is None and not manual:
            raise CrawlStartError("choose a campaign or give at least one website address")
        job_id = await self._in_store(lambda conn: _plan(conn, request, manual))
        self._spawn(job_id)
        return await self.run(job_id)

    async def control(self, request: DiscoveryControlRequest) -> CrawlRun:
        """Written to the store first. Pause and cancel stop at once; URLs in flight stay queued."""

        def apply(conn: sqlite3.Connection) -> None:
            jobs.live_job(conn, request.job_id, JOB_KIND)
            jobs.apply_control(conn, request.job_id, request.action, ACTOR)
            if request.action is not RunControlAction.RESUME:
                _halt_units(conn, request.job_id, request.action)

        await self._in_store(apply)
        if request.action is RunControlAction.RESUME:
            self._spawn(request.job_id)
        elif (task := self._tasks.get(request.job_id)) is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        return await self.run(request.job_id)

    async def run(self, job_id: str) -> CrawlRun:
        found = await self._in_store(lambda conn: repository.get_crawl_run(conn, job_id))
        return self._with_flight(found)

    async def runs(self, query: CrawlRunListQuery) -> CrawlRunPage:
        page = await self._in_store(lambda conn: repository.list_crawl_runs(conn, query))
        return page.model_copy(update={"items": [self._with_flight(item) for item in page.items]})

    async def idle(self, job_id: str) -> None:
        """Waits until this crawl's background task has stopped, if this worker runs it."""
        task = self._tasks.get(job_id)
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def recover(self) -> int:
        """At startup: crawls a dead worker left running are paused, never resumed silently."""

        def pause(conn: sqlite3.Connection) -> int:
            rows = conn.execute(
                "SELECT id FROM jobs WHERE kind = ? AND status = 'running'", (JOB_KIND,)
            ).fetchall()
            for row in rows:
                jobs.pause_job(conn, row["id"], "worker_restarted", ACTOR)
                _halt_units(conn, row["id"], RunControlAction.PAUSE)
            return len(rows)

        return await self._in_store(pause)

    async def shutdown(self) -> None:
        """Stops every crawl, leaves it paused and resumable, and closes the browser."""
        running = list(self._tasks.items())
        for _, task in running:
            task.cancel()
        for job_id, task in running:
            with contextlib.suppress(asyncio.CancelledError):
                await task

            def pause(conn: sqlite3.Connection, job_id: str = job_id) -> None:
                jobs.pause_job(conn, job_id, "worker_stopping", ACTOR)
                _halt_units(conn, job_id, RunControlAction.PAUSE)

            with contextlib.suppress(Exception):
                await self._in_store(pause)
        if self._browser is not None:
            await self._browser.close()

    def _with_flight(self, run: CrawlRun) -> CrawlRun:
        flight = self._in_flight.get(run.job_id)
        if not flight:
            return run
        return run.model_copy(update={"in_flight": sorted(flight.values())[:MAX_IN_FLIGHT_SHOWN]})

    # ---- background execution ------------------------------------------------------------

    def _spawn(self, job_id: str) -> None:
        existing = self._tasks.get(job_id)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(self._execute(job_id), name=f"crawl:{job_id}")
        self._tasks[job_id] = task
        task.add_done_callback(lambda done: self._forget(job_id, done))

    def _forget(self, job_id: str, task: asyncio.Task[None]) -> None:
        if self._tasks.get(job_id) is task:
            del self._tasks[job_id]

    async def _execute(self, job_id: str) -> None:
        try:
            await self._run_job(job_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("crawl job failed", extra={"job_id": job_id})
            await self._in_store(lambda conn: jobs.fail_job(conn, job_id, "internal error", ACTOR))
        finally:
            self._in_flight.pop(job_id, None)
            others = any(not task.done() for other, task in self._tasks.items() if other != job_id)
            if self._browser is not None and not others:
                await asyncio.shield(self._browser.close())

    async def _run_job(self, job_id: str) -> None:
        plan = await self._in_store(lambda conn: _load_plan(conn, job_id))
        if plan is None:
            return
        await self._in_store(lambda conn: jobs.mark_running(conn, job_id, ACTOR))
        settings = plan.settings
        policy = self._settings.policy
        client = guarded_client(self._settings.user_agent, policy, self._http_slots)
        gate = HostGate(
            settings.per_host_concurrency,
            settings.requests_per_minute_per_host,
            policy.spacing_factor,
        )
        job = _Job(
            job_id=job_id,
            project_id=plan.project_id,
            settings=settings,
            fetcher=HttpFetcher(client, gate, policy, asyncio.Semaphore(self._http_slots)),
            gate=gate,
            connector_id=plan.connector_id,
            requests_used=plan.requests_used,
        )
        self._in_flight[job_id] = {}
        sites_at_once = asyncio.Semaphore(max(1, self._http_slots // settings.per_host_concurrency))
        try:
            async with asyncio.TaskGroup() as group:
                for run, task_id in plan.units:
                    group.create_task(self._site(job, run, task_id, sites_at_once))
        finally:
            await client.aclose()
        await self._in_store(lambda conn: _settle_job(conn, job_id))

    async def _site(
        self, job: _Job, run: SourceRun, task_id: str, sites_at_once: asyncio.Semaphore
    ) -> None:
        async with sites_at_once:
            started = asyncio.get_running_loop().time()
            site = await self._in_store(lambda conn: _open_site(conn, job, run, task_id, started))
            if site is None:
                return
            workers = [
                asyncio.create_task(self._worker(job, site))
                for _ in range(job.settings.per_host_concurrency)
            ]
            try:
                await site.frontier.join()
            finally:
                for worker in workers:
                    worker.cancel()
                await asyncio.gather(*workers, return_exceptions=True)
            await self._in_store(lambda conn: _close_site(conn, job, site))

    async def _worker(self, job: _Job, site: _Site) -> None:
        while True:
            _, _, row = await site.frontier.get()
            try:
                if self._pressure is not None:
                    # the time spent waiting for memory does not count against the site's budget
                    site.started += await self._pressure.wait_clear()
                await self._process(job, site, row)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("crawl url failed", extra={"job_id": job.job_id, "url": row.url})
                failed = _final(row, CrawlUrlStatus.FAILED, "internal_error", str(exc)[:2_000])

                def write(conn: sqlite3.Connection, failed: CrawlUrl = failed) -> None:
                    _write_url(conn, failed)

                with contextlib.suppress(Exception):
                    await self._in_store(write)
            finally:
                if row.target is CrawlTarget.ROBOTS:
                    site.robots_ready.set()
                site.frontier.task_done()

    async def _process(self, job: _Job, site: _Site, row: CrawlUrl) -> None:
        if row.target is CrawlTarget.ROBOTS:
            await self._robots(job, site, row)
            return
        await site.robots_ready.wait()
        refusal = self._refusal(job, site, row)
        if refusal in SITE_LIMITS or refusal in SITE_STOPPERS:
            await self._drain(job, site, row, refusal)
        elif refusal is not None:
            skipped = _final(row, CrawlUrlStatus.SKIPPED, refusal)
            await self._in_store(lambda conn: _write_url(conn, skipped))
        elif row.target is CrawlTarget.SITEMAP:
            await self._sitemap(job, site, row)
        else:
            site.pages_used += 1  # reserved before any await, so workers cannot overshoot it
            await self._page(job, site, row)

    def _refusal(self, job: _Job, site: _Site, row: CrawlUrl) -> str | None:
        settings = job.settings
        if site.stop_reason is not None:
            return site.stop_reason
        if job.requests_used >= settings.max_requests:
            return "request_budget"
        if asyncio.get_running_loop().time() - site.started > settings.max_seconds_per_site:
            return "time_budget"
        if row.target is CrawlTarget.PAGE:
            if site.pages_used >= settings.max_pages_per_site:
                return "page_budget"
            if site.bytes_used >= settings.max_bytes_per_site:
                return "byte_budget"
        if site.robots is not None and not site.robots.allowed(path_and_query(row.url)):
            return "robots"
        return None

    async def _drain(self, job: _Job, site: _Site, row: CrawlUrl, reason: str) -> None:
        """A limit or a refusal that ends the site: every queued URL is skipped in one write."""
        rows = [row]
        while not site.frontier.empty():
            rows.append(site.frontier.get_nowait()[2])
            site.frontier.task_done()

        def write(conn: sqlite3.Connection) -> None:
            with repository.atomic(conn):
                for queued in rows:
                    _write_url(conn, _final(queued, CrawlUrlStatus.SKIPPED, reason))
                if reason in SITE_LIMITS:
                    jobs.write_event(
                        conn,
                        job.job_id,
                        site.task_id,
                        "limit_reached",
                        EventLevel.INFO,
                        {"limit": reason, "site": site.site, "skipped": len(rows)},
                    )

        await self._in_store(write)

    def _permit(self, job: _Job, site: _Site) -> Callable[[str], str | None]:
        def permit(target: str) -> str | None:
            if not in_scope(target, site.site, job.settings.include_subdomains):
                return "redirect_out_of_scope"
            if site.robots is not None and not site.robots.allowed(path_and_query(target)):
                return "robots"
            return None

        return permit

    async def _fetch(
        self,
        job: _Job,
        row: CrawlUrl,
        *,
        accept: frozenset[str],
        max_bytes: int,
        permit: Callable[[str], str | None] = lambda _: None,
        validators: JsonObject | None = None,
    ) -> Fetched:
        flight = self._in_flight.setdefault(job.job_id, {})
        flight[row.id] = row.url
        job.requests_used += 1  # reserved now, so parallel workers see the budget shrink
        try:
            result = await job.fetcher.fetch(
                row.url, accept=accept, max_bytes=max_bytes, validators=validators, permit=permit
            )
        finally:
            flight.pop(row.id, None)
        job.requests_used += max(0, result.requests - 1)
        return result

    # ---- robots.txt and sitemaps ---------------------------------------------------------

    async def _robots(self, job: _Job, site: _Site, row: CrawlUrl) -> None:
        result = await self._fetch(job, row, accept=ROBOTS_TYPES, max_bytes=MAX_ROBOTS_DOWNLOAD)
        text = decode(result.body, result.content_type)[0] if result.body is not None else None
        robots, stop = robots_policy(result.error, text)
        site.robots, site.stop_reason = robots, stop
        if robots.crawl_delay_s:
            job.gate.set_crawl_delay(host_of(row.url), robots.crawl_delay_s)
        payload: JsonObject | None = None
        if text is not None and result.error is None:
            payload = {
                "text": text[:MAX_ROBOTS_BYTES],
                "product_token": PRODUCT_TOKEN,
                "rules": len(robots.rules),
                "sitemaps": list(robots.sitemaps),
                "crawl_delay_s": robots.crawl_delay_s,
            }
        declared = [canonical(value) for value in robots.sitemaps]
        parts = urlsplit(row.url)
        sitemaps = [
            url
            for url in declared
            if url is not None and in_scope(url, site.site, job.settings.include_subdomains)
        ] or [urlunsplit((parts.scheme, parts.netloc, "/sitemap.xml", "", ""))]
        found = [] if stop else self._new_rows(job, site, row, sitemaps, CrawlTarget.SITEMAP)
        await self._store(job, site, row, result, payload=payload, new_rows=found)
        if stop is not None:
            event: JsonObject = {"site": site.site, "reason": stop}
            await self._in_store(
                lambda conn: jobs.write_event(
                    conn, job.job_id, site.task_id, "site_refused", EventLevel.WARNING, event
                )
            )

    async def _sitemap(self, job: _Job, site: _Site, row: CrawlUrl) -> None:
        result = await self._fetch(
            job,
            row,
            accept=SITEMAP_TYPES,
            max_bytes=MAX_SITEMAP_BYTES,
            permit=self._permit(job, site),
        )
        payload: JsonObject | None = None
        found: list[CrawlUrl] = []
        if result.body is not None:
            try:
                body = result.body
                if body.startswith(b"\x1f\x8b"):
                    body = gunzip(body, MAX_SITEMAP_BYTES)
            except (ValueError, OSError) as exc:
                result.error, result.message = "too_large", str(exc)
            else:
                is_index, locations = sitemap_locations(decode(body, result.content_type)[0])
                urls = [url for url in map(canonical, locations) if url is not None]
                scope = job.settings.include_subdomains
                in_site = [url for url in urls if in_scope(url, site.site, scope)]
                target = CrawlTarget.SITEMAP if is_index else CrawlTarget.PAGE
                found = self._new_rows(job, site, row, in_site[:MAX_SITEMAP_URLS], target)
                payload = {
                    "kind": "sitemap_index" if is_index else "urlset",
                    "locations": list(locations[:MAX_SITEMAP_URLS]),
                    "location_count": len(locations),
                    "out_of_scope": len(urls) - len(in_site),
                    "enqueued": len(found),
                }
        await self._store(job, site, row, result, payload=payload, new_rows=found)

    def _new_rows(
        self,
        job: _Job,
        site: _Site,
        parent: CrawlUrl,
        urls: Iterable[str | tuple[str, str]],
        target: CrawlTarget,
    ) -> list[CrawlUrl]:
        """Rows for URLs this site has not seen yet, within the per-site cap."""
        now = utc_now()
        rows: list[CrawlUrl] = []
        for item in urls:
            url, anchor = item if isinstance(item, tuple) else (item, None)
            key = page_key(url)
            if key in site.known or len(site.known) >= MAX_URLS_PER_SITE:
                continue
            reason = None
            if target is CrawlTarget.SITEMAP:
                if site.sitemaps_known >= MAX_SITEMAPS_PER_SITE:
                    continue
                site.sitemaps_known += 1
                kind, depth, rank = PageKind.OTHER, parent.depth, SITEMAP_PRIORITY
            else:
                depth = parent.depth + 1 if parent.target is CrawlTarget.PAGE else 1
                if depth > job.settings.max_depth:
                    continue  # a sitemap URL counts as one link away from the home page
                kind = page_kind(url, anchor)
                rank = priority(kind, depth)
                reason = skip_reason(url, job.settings)
            site.known.add(key)
            rows.append(
                _row(site.run.id, url, target, kind, depth, rank, now, parent.id, reason=reason)
            )
        return rows

    # ---- pages ---------------------------------------------------------------------------

    async def _page(self, job: _Job, site: _Site, row: CrawlUrl) -> None:
        cached = await self._in_store(lambda conn: _cached_fetch(conn, job.project_id, row.url))
        result = await self._fetch(
            job,
            row,
            accept=HTML_TYPES,
            max_bytes=MAX_PAGE_BYTES,
            permit=self._permit(job, site),
            validators=cached.validators if cached else None,
        )
        reusable = result.not_modified and cached is not None
        if result.error is not None or (result.body is None and not reusable):
            if result.error in SITE_STOPPERS:
                site.stop_reason = result.error
            if result.error is None:  # 204, or a 304 we did not ask for: nothing to read
                result.error = "empty_response"
                result.message = f"HTTP {result.status} without a body"
            await self._store(job, site, row, result, payload=None, new_rows=[])
            return
        header = result.headers.get("x-robots-tag")
        if cached is not None and reusable:
            links = self._links(job, site, row, cached.payload, header)
            await self._store(job, site, row, result, payload=None, new_rows=links, reused=cached)
            return
        html = decode(result.body or b"", result.content_type)[0]
        page = snapshot(row.url, result.final_url, html, rendered=False)
        reason = {
            BrowserMode.NEVER: None,
            BrowserMode.ALWAYS: "configured",
            BrowserMode.AUTO: fallback_reason(page, result.status or 200),
        }[job.settings.browser]
        render = None
        if reason is not None and not is_challenge(page):
            render = await self._render(job, site, row, result, page, reason)
            page = render.payload or page
        if is_challenge(page):
            site.stop_reason = result.error = "blocked"
            result.message = (
                "a CAPTCHA or bot challenge answered this page; it is not worked around"
            )
            await self._store(job, site, row, result, payload=None, new_rows=[], render=render)
            return
        duplicate_of = site.content_seen.get(str(page.get("content_sha256")))
        links = [] if duplicate_of else self._links(job, site, row, page, header)
        await self._store(
            job,
            site,
            row,
            result,
            payload=page,
            new_rows=links,
            render=render,
            duplicate_of=duplicate_of,
        )

    async def _render(
        self, job: _Job, site: _Site, row: CrawlUrl, result: Fetched, page: JsonObject, reason: str
    ) -> _Render:
        """The browser fallback. If it fails, the HTTP content stays and the reason says why."""
        if self._browser is None:
            return _Render(reason, error="browser_unavailable", message="not enough free memory")
        flight = self._in_flight.setdefault(job.job_id, {})
        flight[row.id] = row.url
        try:
            rendered = await self._browser.render(
                result.final_url, screenshot=job.settings.screenshots
            )
        except BrowserUnavailableError as exc:
            return _Render(reason, error="browser_unavailable", message=str(exc)[:2_000])
        except (PlaywrightError, TimeoutError) as exc:
            return _Render(reason, error="browser_failed", message=str(exc)[:2_000])
        finally:
            flight.pop(row.id, None)
        final = canonical(rendered.final_url)
        if final is None or not in_scope(final, site.site, job.settings.include_subdomains):
            # a script navigated elsewhere: what it shows is not this site's page
            message = f"the page navigated to {rendered.final_url[:500]}"
            return _Render(reason, error="redirect_out_of_scope", message=message)
        payload = snapshot(row.url, rendered.final_url, rendered.html, rendered=True)
        payload["fallback"] = {
            "reason": reason,
            "browser": rendered.browser,
            "blocked_requests": rendered.blocked_requests,
            "http_signals": page.get("signals"),
            "http_body_sha256": sha256(result.body or b""),
        }
        return _Render(
            reason,
            payload=payload,
            html=rendered.html.encode("utf-8"),
            final_url=rendered.final_url,
            status=rendered.status,
            elapsed_ms=rendered.elapsed_ms,
            screenshot=rendered.screenshot,
        )

    def _links(
        self, job: _Job, site: _Site, row: CrawlUrl, page: JsonObject, header: object
    ) -> list[CrawlUrl]:
        if row.depth >= job.settings.max_depth or _nofollow(page, header):
            return []
        found = page.get("links")
        pairs: list[tuple[str, str]] = []
        for link in found if isinstance(found, list) else []:
            if not isinstance(link, dict) or link.get("nofollow") is True:
                continue
            url, text = link.get("url"), link.get("text")
            if isinstance(url, str) and in_scope(url, site.site, job.settings.include_subdomains):
                pairs.append((url, text if isinstance(text, str) else ""))
        return self._new_rows(job, site, row, pairs, CrawlTarget.PAGE)

    async def _store(
        self,
        job: _Job,
        site: _Site,
        row: CrawlUrl,
        result: Fetched,
        *,
        payload: JsonObject | None,
        new_rows: list[CrawlUrl],
        render: _Render | None = None,
        duplicate_of: str | None = None,
        reused: _Cached | None = None,
    ) -> None:
        data_dir = self._settings.data_dir
        if data_dir is None:
            raise CrawlStartError("the data directory is not configured")
        acquisition = _Acquisition(result, payload, new_rows, render, duplicate_of, reused)
        stored = await self._in_store(
            lambda conn: _persist(conn, data_dir, job.connector_id, row, acquisition)
        )
        if stored.status is CrawlUrlStatus.FETCHED and reused is None:
            site.bytes_used += stored.byte_size or 0
        if payload is not None and stored.content_sha256 and stored.duplicate_of_id is None:
            site.content_seen.setdefault(stored.content_sha256, stored.id)
        for new in new_rows:
            if new.status is CrawlUrlStatus.QUEUED:
                site.frontier.put_nowait((-new.priority, new.id, new))


# ---- store: planning ----------------------------------------------------------------------------


def _row(
    run_id: str,
    url: str,
    target: CrawlTarget,
    kind: PageKind,
    depth: int,
    rank: int,
    now: datetime,
    discovered_from_id: str | None,
    *,
    reason: str | None = None,
    candidate_id: str | None = None,
) -> CrawlUrl:
    """A URL the crawler just learnt about: queued, or skipped right away with its reason."""
    return CrawlUrl(
        id=new_id(),
        source_run_id=run_id,
        candidate_id=candidate_id,
        discovered_from_id=discovered_from_id,
        url=url,
        target=target,
        page_kind=kind,
        depth=depth,
        priority=rank,
        status=CrawlUrlStatus.SKIPPED if reason else CrawlUrlStatus.QUEUED,
        reason=reason,
        method=None,
        final_url=None,
        http_status=None,
        content_type=None,
        byte_size=None,
        body_sha256=None,
        content_sha256=None,
        duplicate_of_id=None,
        acquired_page_id=None,
        raw_record_id=None,
        request_count=0,
        retry_count=0,
        fetch_ms=None,
        browser_ms=None,
        fallback_reason=None,
        redirects_json=None,
        headers_json=None,
        screenshot_path=None,
        error_message=None,
        created_at=now,
        updated_at=now,
    )


def _connector(conn: sqlite3.Connection) -> SourceConnector:
    row = conn.execute(
        f"SELECT {', '.join(SourceConnector.model_fields)} FROM source_connectors "  # noqa: S608
        "WHERE key = ?",
        (CONNECTOR_KEY,),
    ).fetchone()
    if row is not None:
        return repository.from_row(SourceConnector, row)
    now = utc_now()
    connector = SourceConnector(
        id=new_id(),
        key=CONNECTOR_KEY,
        name=CONNECTOR_NAME,
        kind=SourceKind.BUSINESS_WEBSITE,
        connector_version=CONNECTOR_VERSION,
        terms_url=None,
        requires_credentials=False,
        enabled=True,
        created_at=now,
        updated_at=now,
    )
    repository.insert(conn, connector)
    return connector


def _plan(conn: sqlite3.Connection, request: CrawlStartRequest, manual: list[str]) -> str:
    """Creates the job, one source run + task per site, and each site's first two URLs."""
    settings = request.settings
    campaign: Campaign | None = None
    if request.campaign_id is not None:
        campaign = repository.get(conn, Campaign, request.campaign_id)
        if campaign.deleted_at is not None:
            raise CrawlStartError("this campaign has been deleted")
        project_id = campaign.project_id
    else:
        project_id = request.project_id or repository.ensure_default_project(conn, actor=ACTOR).id
    roots: dict[str, tuple[str, str | None]] = {}
    if campaign is not None:
        for candidate in conn.execute(
            "SELECT id, website_url, domain FROM discovery_candidates "
            "WHERE campaign_id = ? AND duplicate_of_id IS NULL ORDER BY created_at, id",
            (campaign.id,),
        ):
            url = seed_url(candidate["website_url"] or candidate["domain"] or "")
            if url is not None:
                roots.setdefault(site_of(url), (url, candidate["id"]))
    for url in manual:
        roots.setdefault(site_of(url), (url, None))
    if not roots:
        raise CrawlStartError(
            "nothing to crawl: no candidate of this campaign has a website and no address was given"
        )
    chosen = list(roots.items())[: settings.max_sites]
    now = utc_now()
    dumped = settings.model_dump(mode="json")
    job = Job(
        id=new_id(),
        project_id=project_id,
        kind=JOB_KIND,
        status=JobStatus.QUEUED,
        priority=0,
        params_json={
            "campaign_id": campaign.id if campaign else None,
            "settings": dumped,
            "sites": len(chosen),
            "sites_available": len(roots),
        },
        idempotency_key=None,
        progress_done=0,
        progress_total=len(chosen),
        requested_at=now,
        started_at=None,
        finished_at=None,
        created_at=now,
        updated_at=now,
    )
    with repository.atomic(conn):
        connector = _connector(conn)
        repository.insert(conn, job)
        for site, (url, candidate_id) in chosen:
            run = SourceRun(
                id=new_id(),
                project_id=project_id,
                campaign_id=campaign.id if campaign else None,
                connector_id=connector.id,
                connector_version=CONNECTOR_VERSION,
                config_id=None,
                job_id=job.id,
                status=SourceRunStatus.QUEUED,
                effective_config_json={
                    "connector_key": CONNECTOR_KEY,
                    "connector_version": CONNECTOR_VERSION,
                    "product_token": PRODUCT_TOKEN,
                    "site": site,
                    "root_url": url,
                    "candidate_id": candidate_id,
                    "settings": dumped,
                },
                started_at=None,
                finished_at=None,
                error_code=None,
                error_message=None,
                created_at=now,
                updated_at=now,
            )
            parts = urlsplit(url)
            robots_url = urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))
            robots = _row(run.id, robots_url, CrawlTarget.ROBOTS, PageKind.OTHER, 0,
                          ROBOTS_PRIORITY, now, None)  # fmt: skip
            root = _row(run.id, url, CrawlTarget.PAGE, page_kind(url), 0, ROOT_PRIORITY, now,
                        None, candidate_id=candidate_id)  # fmt: skip
            task = JobTask(
                id=new_id(),
                job_id=job.id,
                stage=STAGE,
                task_key=site,
                status=JobStatus.QUEUED,
                attempt_count=0,
                max_attempts=MAX_ATTEMPTS,
                created_at=now,
                updated_at=now,
            )
            repository.insert(conn, run, task, robots, *([root] if root.url != robots.url else []))
        if len(roots) > len(chosen):
            jobs.write_event(
                conn,
                job.id,
                None,
                "sites_capped",
                EventLevel.INFO,
                {"available": len(roots), "crawled": len(chosen)},
            )
        repository.audit(conn, AuditAction.CREATE, EntityType.JOB, job.id, actor=ACTOR)
    return job.id


def _load_plan(conn: sqlite3.Connection, job_id: str) -> _Plan | None:
    job = jobs.live_job(conn, job_id, JOB_KIND)
    if job.status not in (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.PAUSED):
        return None
    runs = repository.source_runs_of_job(conn, job_id)
    (requests,) = conn.execute(
        "SELECT coalesce(sum(c.request_count), 0) FROM crawl_urls c "
        "JOIN source_runs r ON r.id = c.source_run_id WHERE r.job_id = ?",
        (job_id,),
    ).fetchone()
    units = [
        (run, repository.task_of(conn, job_id, str(run.effective_config_json["site"])).id)
        for run in runs
        if run.status not in _FINISHED_RUNS
    ]
    return _Plan(
        project_id=job.project_id,
        settings=CrawlSettings.model_validate(job.params_json["settings"]),
        units=units,
        requests_used=int(requests),
        connector_id=runs[0].connector_id if runs else "",
    )


def _halt_units(conn: sqlite3.Connection, job_id: str, action: RunControlAction) -> None:
    """Tasks and attempts follow their job when it is paused or cancelled."""
    target = JobStatus.PAUSED if action is RunControlAction.PAUSE else JobStatus.CANCELLED
    movable = (
        (JobStatus.RUNNING,)
        if target is JobStatus.PAUSED
        else (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.PAUSED)
    )
    now = format_ts(utc_now())
    with repository.atomic(conn):
        for task in conn.execute("SELECT id, status FROM job_tasks WHERE job_id = ?", (job_id,)):
            if JobStatus(task["status"]) in movable:
                repository.change_status(conn, JobTask, task["id"], target, actor=ACTOR)
        conn.execute(
            "UPDATE task_attempts SET status = ?, finished_at = ? WHERE status = 'running' "
            "AND task_id IN (SELECT id FROM job_tasks WHERE job_id = ?)",
            (target.value, now if target is JobStatus.CANCELLED else None, job_id),
        )


# ---- store: sites -------------------------------------------------------------------------------


def _open_site(
    conn: sqlite3.Connection, job: _Job, run: SourceRun, task_id: str, started: float
) -> _Site | None:
    """Starts an attempt on one site and rebuilds its frontier and counters from the store."""
    if repository.get(conn, Job, job.job_id).status is not JobStatus.RUNNING:
        return None
    columns = ", ".join(CrawlUrl.model_fields)
    rows = [
        repository.from_row(CrawlUrl, row)
        for row in conn.execute(
            f"SELECT {columns} FROM crawl_urls WHERE source_run_id = ? ORDER BY id",  # noqa: S608
            (run.id,),
        )
    ]
    now = utc_now()
    with repository.atomic(conn):
        task = repository.get(conn, JobTask, task_id)
        repository.change_status(conn, JobTask, task_id, JobStatus.RUNNING, actor=ACTOR)
        conn.execute(
            "UPDATE job_tasks SET attempt_count = ?, updated_at = ? WHERE id = ?",
            (task.attempt_count + 1, format_ts(now), task_id),
        )
        repository.change_status(conn, SourceRun, run.id, SourceRunStatus.RUNNING, actor=ACTOR)
        if run.started_at is None:
            conn.execute(
                "UPDATE source_runs SET started_at = ?, updated_at = ? WHERE id = ?",
                (format_ts(now), format_ts(now), run.id),
            )
        repository.insert(
            conn,
            TaskAttempt(
                id=new_id(),
                task_id=task_id,
                attempt=task.attempt_count + 1,
                status=JobStatus.RUNNING,
                started_at=now,
                finished_at=None,
                error_code=None,
                error_message=None,
                retryable=None,
            ),
        )
    fetched = [row for row in rows if row.status is CrawlUrlStatus.FETCHED]
    site = _Site(
        run=run,
        task_id=task_id,
        site=str(run.effective_config_json["site"]),
        frontier=asyncio.PriorityQueue(),
        known={page_key(row.url) for row in rows},
        pages_used=sum(
            1
            for row in rows
            if row.target is CrawlTarget.PAGE
            and row.status in (CrawlUrlStatus.FETCHED, CrawlUrlStatus.FAILED)
        ),
        bytes_used=sum(row.byte_size or 0 for row in fetched if row.reason != "not_modified"),
        sitemaps_known=sum(1 for row in rows if row.target is CrawlTarget.SITEMAP),
        content_seen={
            row.content_sha256: row.id
            for row in reversed(fetched)
            if row.content_sha256 and row.duplicate_of_id is None
        },
        started=started,
    )
    for row in rows:
        if row.status is CrawlUrlStatus.QUEUED:
            site.frontier.put_nowait((-row.priority, row.id, row))
        elif row.target is CrawlTarget.ROBOTS:
            text = None
            if row.raw_record_id is not None:
                stored = repository.get(conn, RawRecord, row.raw_record_id).payload_json.get("text")
                text = stored if isinstance(stored, str) else None
            error = row.reason if row.status is CrawlUrlStatus.FAILED else None
            site.robots, site.stop_reason = robots_policy(error, text)
            site.robots_ready.set()
        elif row.status is CrawlUrlStatus.FAILED and row.reason in SITE_STOPPERS:
            site.stop_reason = row.reason
    return site


def _close_site(conn: sqlite3.Connection, job: _Job, site: _Site) -> None:
    """The frontier is empty: the site succeeded if at least one page was fetched."""
    if repository.get(conn, Job, job.job_id).status is not JobStatus.RUNNING:
        return
    (pages,) = conn.execute(
        "SELECT count(*) FROM crawl_urls WHERE source_run_id = ? AND target = 'page' "
        "AND status = 'fetched'",
        (site.run.id,),
    ).fetchone()
    root = conn.execute(
        "SELECT reason, error_message FROM crawl_urls WHERE source_run_id = ? AND target = 'page' "
        "ORDER BY id LIMIT 1",
        (site.run.id,),
    ).fetchone()
    failed = pages == 0
    code = (
        (site.stop_reason or (root["reason"] if root else None) or "nothing_fetched")
        if failed
        else None
    )
    message = (root["error_message"] if root else None) if failed else None
    task_status = JobStatus.FAILED if failed else JobStatus.SUCCEEDED
    run_status = SourceRunStatus.FAILED if failed else SourceRunStatus.SUCCEEDED
    now = format_ts(utc_now())
    with repository.atomic(conn):
        conn.execute(
            "UPDATE task_attempts SET status = ?, finished_at = ?, error_code = ?, "
            "error_message = ?, retryable = ? WHERE task_id = ? AND status = 'running'",
            (task_status.value, now, code, message, 0 if failed else None, site.task_id),
        )
        repository.change_status(conn, JobTask, site.task_id, task_status, actor=ACTOR)
        repository.change_status(conn, SourceRun, site.run.id, run_status, actor=ACTOR)
        conn.execute(
            "UPDATE source_runs SET finished_at = ?, error_code = ?, error_message = ?, "
            "updated_at = ? WHERE id = ?",
            (now, code, message, now, site.run.id),
        )
        conn.execute(
            "UPDATE jobs SET progress_done = (SELECT count(*) FROM source_runs WHERE job_id = ? "
            "AND status IN ('succeeded', 'failed', 'cancelled')), updated_at = ? WHERE id = ?",
            (job.job_id, now, job.job_id),
        )
        jobs.write_event(
            conn,
            job.job_id,
            site.task_id,
            "site_failed" if failed else "site_finished",
            EventLevel.ERROR if failed else EventLevel.INFO,
            {"site": site.site, "pages": int(pages), "stopped_by": site.stop_reason},
            code,
            False if failed else None,
        )


def _settle_job(conn: sqlite3.Connection, job_id: str) -> None:
    job = repository.get(conn, Job, job_id)
    if job.status is not JobStatus.RUNNING:
        return
    runs = repository.source_runs_of_job(conn, job_id)
    statuses = {run.status for run in runs}
    if statuses & set(jobs.OPEN_RUNS):
        jobs.pause_job(conn, job_id, "stopped_before_finishing", ACTOR)
        _halt_units(conn, job_id, RunControlAction.PAUSE)
        return
    target = JobStatus.FAILED if statuses == {SourceRunStatus.FAILED} else JobStatus.SUCCEEDED
    now = format_ts(utc_now())
    with repository.atomic(conn):
        repository.change_status(conn, Job, job_id, target, actor=ACTOR)
        conn.execute(
            "UPDATE jobs SET finished_at = ?, progress_done = ?, progress_total = ?, "
            "updated_at = ? WHERE id = ?",
            (now, len(runs), len(runs), now, job_id),
        )
        jobs.write_event(conn, job_id, None, f"run_{target.value}", EventLevel.INFO)


# ---- store: URLs --------------------------------------------------------------------------------


def _final(
    row: CrawlUrl, status: CrawlUrlStatus, reason: str, message: str | None = None
) -> CrawlUrl:
    return row.model_copy(
        update={
            "status": status,
            "reason": reason,
            "error_message": message,
            "updated_at": utc_now(),
        }
    )


def _write_url(conn: sqlite3.Connection, row: CrawlUrl) -> None:
    if repository.update(conn, row, *_RESULT_FIELDS) != 1:
        raise repository.NotFoundError(f"crawl_urls {row.id} not found")


def _cached_fetch(conn: sqlite3.Connection, project_id: str, url: str) -> _Cached | None:
    """The newest plain HTTP fetch of this URL in the project that left ETag or Last-Modified."""
    row = conn.execute(
        "SELECT c.headers_json, c.raw_record_id, c.content_sha256, c.body_sha256, c.byte_size, "
        "c.content_type, r.payload_json FROM crawl_urls c "
        "JOIN source_runs s ON s.id = c.source_run_id "
        "JOIN raw_records r ON r.id = c.raw_record_id "
        "WHERE c.url = ? AND c.status = 'fetched' AND c.method = 'http_fetch' "
        "AND s.project_id = ? ORDER BY c.updated_at DESC, c.id DESC LIMIT 1",
        (url, project_id),
    ).fetchone()
    if row is None or row["headers_json"] is None:
        return None
    headers = orjson.loads(row["headers_json"])
    validators: JsonObject = {
        name: headers[name]
        for name in ("etag", "last-modified")
        if isinstance(headers.get(name), str)
    }
    if not validators:
        return None
    return _Cached(
        validators=validators,
        payload=orjson.loads(row["payload_json"]),
        raw_record_id=row["raw_record_id"],
        content_sha256=row["content_sha256"],
        body_sha256=row["body_sha256"],
        byte_size=row["byte_size"],
        content_type=row["content_type"],
    )


def store_artifact(data_dir: Path, data: bytes, suffix: str) -> tuple[str, str]:
    """Content-addressed: identical bodies are stored once. Returns (relative path, sha256)."""
    digest = sha256(data)
    target = data_dir / ARTIFACT_DIR / digest[:2] / f"{digest}{suffix}"
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        # short on purpose: Windows paths stop at 260 characters (the core bounds the data dir)
        partial = target.with_name(f"{digest[:16]}.{new_id()[-8:]}.partial")
        partial.write_bytes(data)
        try:
            partial.replace(target)
        except PermissionError:
            # Two crawls stored the same body at once and Windows refused the second rename: the
            # name is the content's hash, so the file already there is this one.
            if not target.exists():
                raise
            partial.unlink(missing_ok=True)
    return relative_data_path(data_dir, target), digest


@dataclass(frozen=True, slots=True)
class _Acquisition:
    result: Fetched
    payload: JsonObject | None
    new_rows: list[CrawlUrl]
    render: _Render | None
    duplicate_of: str | None
    reused: _Cached | None


def _persist(
    conn: sqlite3.Connection,
    data_dir: Path,
    connector_id: str,
    row: CrawlUrl,
    got: _Acquisition,
) -> CrawlUrl:
    """One URL in one transaction: the pages fetched, the parsed record, the row and its links."""
    result, render, reused = got.result, got.render, got.reused
    now = utc_now()
    failed = result.error is not None
    pages: list[AcquiredPage] = []
    if result.status is not None:
        artifact = digest = None
        if result.body is not None:
            artifact, digest = store_artifact(data_dir, result.body, _ARTIFACT_SUFFIX[row.target])
        pages.append(
            AcquiredPage(
                id=new_id(),
                source_run_id=row.source_run_id,
                source_query_id=None,
                url=row.url,
                final_url=result.final_url,
                http_status=result.status,
                content_type=result.content_type,
                content_sha256=digest,
                byte_size=len(result.body) if result.body is not None else None,
                artifact_path=artifact,
                fetched_at=now,
            )
        )
    rendered = render is not None and render.html is not None
    if render is not None and render.html is not None:
        artifact, digest = store_artifact(data_dir, render.html, ".rendered.html")
        pages.append(
            AcquiredPage(
                id=new_id(),
                source_run_id=row.source_run_id,
                source_query_id=None,
                url=row.url,
                final_url=render.final_url,
                http_status=render.status,
                content_type="text/html; charset=utf-8",
                content_sha256=digest,
                byte_size=len(render.html),
                artifact_path=artifact,
                fetched_at=now,
            )
        )
    content = pages[-1] if pages else None
    screenshot = (
        store_artifact(data_dir, render.screenshot, ".png")[0]
        if render is not None and render.screenshot is not None
        else None
    )
    method = AcquisitionMethod.BROWSER_RENDER if rendered else AcquisitionMethod.HTTP_FETCH
    raw: RawRecord | None = None
    if not failed and got.payload is not None and got.duplicate_of is None:
        raw = RawRecord(
            id=new_id(),
            source_run_id=row.source_run_id,
            acquired_page_id=content.id if content else None,
            connector_id=connector_id,
            method=method,
            external_id=None,
            source_url=result.final_url,
            payload_json=got.payload,
            payload_sha256=_digest(got.payload),
            acquired_at=now,
            created_at=now,
        )
    content_sha = got.payload.get("content_sha256") if got.payload is not None else None
    reason = result.error
    if not failed and reused is not None:
        reason = "not_modified"
    elif not failed and render is not None and render.error is not None:
        reason = render.error
    stored = (
        (reused.byte_size, reused.body_sha256, reused.content_sha256)
        if reused is not None
        else (
            content.byte_size if content else None,
            content.content_sha256 if content else None,
            content_sha if isinstance(content_sha, str) else None,
        )
    )
    final = row.model_copy(
        update={
            "status": CrawlUrlStatus.FAILED if failed else CrawlUrlStatus.FETCHED,
            "reason": reason,
            "method": method,
            "final_url": render.final_url if render and rendered else result.final_url,
            "http_status": result.status,
            "content_type": content.content_type if content else result.content_type,
            "byte_size": stored[0],
            "body_sha256": stored[1],
            "content_sha256": stored[2],
            "duplicate_of_id": got.duplicate_of,
            "acquired_page_id": content.id if content else None,
            "raw_record_id": raw.id if raw else (reused.raw_record_id if reused else None),
            "request_count": result.requests,
            "retry_count": result.retries,
            "fetch_ms": result.elapsed_ms,
            "browser_ms": render.elapsed_ms if render else None,
            "fallback_reason": render.reason if render else None,
            "redirects_json": {"hops": list(result.redirects)} if result.redirects else None,
            "headers_json": result.headers or None,
            "screenshot_path": screenshot,
            "error_message": result.message or (render.message if render else None),
            "updated_at": now,
        }
    )
    with repository.atomic(conn):
        # Identical findings are stored once per run: sitemap findings name no address, so two
        # empty sitemaps of one site hash alike. Both URLs then point at the first record.
        if raw is not None and (
            first := repository.raw_record_with_payload(conn, raw.source_run_id, raw.payload_sha256)
        ):
            raw, final = None, final.model_copy(update={"raw_record_id": first})
        repository.insert(conn, *pages, *([raw] if raw else []), *got.new_rows)
        _write_url(conn, final)
    return final
