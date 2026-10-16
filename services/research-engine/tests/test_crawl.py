"""The crawl engine end to end, against a local fixture site the test controls completely.

The fixture server binds to 127.0.0.1, so these crawlers run with `allow_private=True`: the one
explicit test mode of the SSRF guard. `test_the_guard_refuses_private_targets_before_any_request`
runs without it and proves the default refuses the very same server.
"""

import asyncio
import contextlib
import gzip
import os
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from research_engine import repository
from research_engine.crawl import guard
from research_engine.crawl.engine import CrawlerSettings, CrawlService, CrawlStartError
from research_engine.crawl.engine import _plan as plan_crawl
from research_engine.crawl.extract import sha256
from research_engine.crawl.fetch import FetchPolicy
from research_engine.discovery.engine import DiscoveryService, EngineSettings
from research_engine.domain import (
    AcquisitionMethod,
    BrowserMode,
    CampaignSaveRequest,
    CrawlRun,
    CrawlRunListQuery,
    CrawlSettings,
    CrawlStartRequest,
    CrawlUrlDetail,
    CrawlUrlItem,
    CrawlUrlListQuery,
    CrawlUrlStatus,
    DiscoveryControlRequest,
    DiscoveryLimits,
    DiscoveryStartRequest,
    JobStatus,
    RunControlAction,
    SourceRunStatus,
    SourceSelection,
    campaign_crawl_settings,
    default_crawl_settings,
    empty_criteria,
)

pytestmark = pytest.mark.anyio

SETTLE_TIMEOUT_S = 60.0


# ---- the fixture site ----------------------------------------------------------------------------


@dataclass
class Reply:
    status: int = 200
    body: str | bytes = ""
    content_type: str = "text/html; charset=utf-8"
    headers: dict[str, str] = field(default_factory=dict)
    delay_s: float = 0.0
    gzip: bool = False


Route = Reply | Callable[[int, dict[str, str]], Reply]


class FixtureSite:
    """A website on 127.0.0.1. Routes answer with a `Reply`, or a function of (hit number, headers).

    `{base}` in a body is replaced with the site's own address. A path in `gates` blocks until its
    event is set, which lets a test catch a crawl in the middle of a request.
    """

    def __init__(self, routes: dict[str, Route]) -> None:
        self.routes = routes
        self.requests: list[tuple[str, dict[str, str]]] = []
        self.gates: dict[str, threading.Event] = {}
        site = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                return

            def do_GET(self) -> None:
                headers = {key.lower(): value for key, value in self.headers.items()}
                site.requests.append((self.path, headers))
                if (gate := site.gates.get(self.path)) is not None:
                    gate.wait(timeout=30)
                route = site.routes.get(self.path, Reply(404, "not here"))
                reply = route if isinstance(route, Reply) else route(site.hits(self.path), headers)
                if reply.delay_s:
                    time.sleep(reply.delay_s)
                body = (
                    reply.body.replace("{base}", site.base).encode("utf-8")
                    if isinstance(reply.body, str)
                    else reply.body
                )
                if reply.gzip:
                    body = gzip.compress(body)
                self.send_response(reply.status)
                self.send_header("Content-Type", reply.content_type)
                if reply.gzip:
                    self.send_header("Content-Encoding", "gzip")
                for key, value in reply.headers.items():
                    self.send_header(key, value.replace("{base}", site.base))
                if "Content-Length" not in reply.headers:
                    self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                # the crawler may give up on an answer halfway: exactly what some tests want
                with contextlib.suppress(OSError):
                    self.wfile.write(body)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.base = f"http://127.0.0.1:{self._server.server_address[1]}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def url(self, path: str = "/") -> str:
        return f"{self.base}{path}"

    def hits(self, path: str) -> int:
        return sum(1 for requested, _ in self.requests if requested == path)

    def close(self) -> None:
        for gate in self.gates.values():
            gate.set()
        self._server.shutdown()
        self._server.server_close()


def html(title: str, body: str) -> str:
    filler = "Family bakery in Austin, Texas, baking bread and pastries every single morning. "
    head = f"<!doctype html><html><head><title>{title}</title></head>"
    return f"{head}<body>{body}<p>{filler}</p></body></html>"


HOME = html(
    "Panadería El Sol",
    """<script type="application/ld+json">{"@type": "Bakery", "name": "Panadería El Sol"}</script>
    <nav><a href="/about-us">Quiénes somos</a> <a href="/contact">Contact</a>
    <a href="/services">Services</a> <a href="/cart">Cart</a> <a href="/login">Log in</a>
    <a href="/events/calendar?month=2026-09">Calendar</a> <a href="/private/notes">Notes</a>
    <a href="/copy-of-home">Copy</a> <a href="/sponsored" rel="nofollow">Sponsor</a>
    <a href="https://elsewhere.example/page">Elsewhere</a></nav>
    <main><h1>Pan fresco</h1><p>Call (512) 555-0101 or write to hola@sol.example.</p></main>""",
)


@pytest.fixture
def bakery() -> Iterator[FixtureSite]:
    about = html("Quiénes somos", "<main><p>Panadería familiar desde 1990.</p></main>")
    site = FixtureSite(
        {
            "/robots.txt": Reply(
                content_type="text/plain",
                body="User-agent: *\nDisallow: /private/\nSitemap: {base}/sitemap_index.xml\n",
            ),
            "/sitemap_index.xml": Reply(
                content_type="application/xml",
                body="<sitemapindex><sitemap><loc>{base}/sitemap-pages.xml.gz</loc></sitemap>"
                "<sitemap><loc>https://elsewhere.example/sitemap.xml</loc></sitemap></sitemapindex>",
            ),
            "/sitemap-pages.xml.gz": Reply(
                content_type="application/gzip",
                body=gzip.compress(
                    b"<urlset><url><loc>__BASE__/about-us</loc></url>"
                    b"<url><loc>__BASE__/private/secret</loc></url></urlset>"
                ),
            ),
            "/": Reply(body=HOME, gzip=True),
            "/copy-of-home": Reply(body=HOME.replace("<nav>", "\n\n<nav>")),
            "/about-us": Reply(
                content_type="text/html",
                body=f'<meta charset="windows-1252">{about}'.encode("cp1252"),
            ),
            "/contact": Reply(body=html("Contact", "<main><p>Mon-Sat 7am to 3pm.</p></main>")),
            "/services": Reply(301, headers={"Location": "/our-services"}),
            "/our-services": Reply(
                body=html("Services", "<main><p>Cakes and catering.</p></main>")
            ),
        }
    )
    # the gzipped sitemap cannot carry a {base} placeholder, so it is rebuilt with the real address
    site.routes["/sitemap-pages.xml.gz"] = Reply(
        content_type="application/gzip",
        body=gzip.compress(
            f"<urlset><url><loc>{site.base}/about-us</loc></url>"
            f"<url><loc>{site.base}/private/secret</loc></url></urlset>".encode()
        ),
    )
    yield site
    site.close()


# ---- helpers -------------------------------------------------------------------------------------

POLICY = FetchPolicy(
    connect_timeout_s=2.0,
    read_timeout_s=1.0,
    attempt_deadline_s=3.0,
    backoff_base_s=0.05,
    spacing_factor=0.01,
    allow_private=True,
)


def crawler(data_dir: Path, *, policy: FetchPolicy = POLICY, **options: object) -> CrawlService:
    settings = CrawlerSettings(
        data_dir=data_dir,
        user_agent="LeadIntelligence/test (+crawl fixture)",
        policy=policy,
        http_slots=4,
        browser_slots=1,
        **options,  # type: ignore[arg-type]
    )
    return CrawlService(settings, lambda: repository.connect(data_dir))


def crawl_settings(**changes: object) -> CrawlSettings:
    base = default_crawl_settings().model_copy(
        update={"requests_per_minute_per_host": 120, "browser": BrowserMode.NEVER}
    )
    return CrawlSettings.model_validate(base.model_dump() | changes)


async def crawl(
    service: CrawlService, *urls: str, campaign_id: str | None = None, **changes: object
) -> CrawlRun:
    run = await service.start(
        CrawlStartRequest(
            project_id=None,
            campaign_id=campaign_id,
            urls=list(urls),
            settings=crawl_settings(**changes),
        )
    )
    return await settle(service, run.job_id)


async def settle(service: CrawlService, job_id: str) -> CrawlRun:
    async with asyncio.timeout(SETTLE_TIMEOUT_S):
        while True:
            run = await service.run(job_id)
            if run.status not in (JobStatus.QUEUED, JobStatus.RUNNING):
                await service.idle(job_id)
                return await service.run(job_id)
            await asyncio.sleep(0.05)


def urls_of(conn: sqlite3.Connection, run: CrawlRun) -> dict[str, CrawlUrlItem]:
    page = repository.list_crawl_urls(
        conn,
        CrawlUrlListQuery(
            job_id=run.job_id, source_run_id=None, status=None, limit=500, cursor=None
        ),
    )
    return {item.url: item for item in page.items}


def detail(conn: sqlite3.Connection, item: CrawlUrlItem) -> CrawlUrlDetail:
    return repository.get_crawl_url_detail(conn, item.id)


# ---- a whole site --------------------------------------------------------------------------------


async def test_a_business_site_is_crawled_within_its_policies(
    conn: sqlite3.Connection, data_dir: Path, bakery: FixtureSite
) -> None:
    service = crawler(data_dir)
    run = await crawl(service, bakery.url("/"))
    await service.shutdown()
    assert run.status is JobStatus.SUCCEEDED
    assert run.sites[0].status is SourceRunStatus.SUCCEEDED
    rows = urls_of(conn, run)

    def row(path: str) -> CrawlUrlItem:
        return rows[bakery.url(path)]

    # robots.txt is the very first request, and its rules hold
    assert bakery.requests[0][0] == "/robots.txt"
    assert (row("/private/notes").status, row("/private/notes").reason) == (
        CrawlUrlStatus.SKIPPED,
        "robots",
    )
    assert row("/private/secret").reason == "robots", "sitemap URLs obey robots.txt too"
    assert bakery.hits("/private/notes") == bakery.hits("/private/secret") == 0
    # the product identifies itself on every request
    assert all(
        headers["user-agent"].startswith("LeadIntelligence/") for _, headers in bakery.requests
    )

    # sitemap index -> gzipped child sitemap; another site's sitemap is not followed
    assert row("/sitemap_index.xml").status is CrawlUrlStatus.FETCHED
    assert row("/sitemap-pages.xml.gz").status is CrawlUrlStatus.FETCHED
    assert "https://elsewhere.example/sitemap.xml" not in rows

    # private areas, calendars, nofollow and other sites are not followed
    assert row("/cart").reason == row("/login").reason == "private_area"
    assert row("/events/calendar?month=2026-09").reason == "trap"
    assert bakery.url("/sponsored") not in rows
    assert "https://elsewhere.example/page" not in rows
    assert bakery.hits("/cart") == bakery.hits("/login") == 0

    # the home page (served gzipped) as raw findings, with provenance down to the stored bytes
    home = detail(conn, row("/"))
    assert home.raw_record is not None
    assert home.acquired_page is not None
    payload = home.raw_record.payload_json
    assert payload["title"] == "Panadería El Sol"
    assert payload["json_ld"] == [{"@type": "Bakery", "name": "Panadería El Sol"}]
    assert {"value": "hola@sol.example", "origin": "text"} in payload["emails"]  # type: ignore[operator]
    assert home.raw_record.method is AcquisitionMethod.HTTP_FETCH
    assert home.acquired_page.artifact_path is not None
    stored = (data_dir / home.acquired_page.artifact_path).read_bytes()
    assert stored == HOME.encode("utf-8"), "the artifact holds the decoded body"
    assert sha256(stored) == home.acquired_page.content_sha256 == home.crawl_url.body_sha256
    assert home.crawl_url.headers_json is not None
    assert home.crawl_url.headers_json["content-encoding"] == "gzip"

    # an odd charset, a redirect chain and an identical page
    about = detail(conn, row("/about-us"))
    assert about.raw_record is not None
    assert "Panadería familiar desde 1990." in str(about.raw_record.payload_json["main_text"])
    services = detail(conn, row("/services"))
    assert services.crawl_url.final_url == bakery.url("/our-services")
    assert services.crawl_url.redirects_json == {
        "hops": [
            {"url": bakery.url("/services"), "status": 301, "location": bakery.url("/our-services")}
        ]
    }
    assert services.crawl_url.request_count == 2
    copy = detail(conn, row("/copy-of-home"))
    assert copy.crawl_url.duplicate_of_id == home.crawl_url.id
    assert copy.raw_record is None, "an identical page is linked, not stored again"

    # the numbers come from the fetch log
    metrics = run.metrics
    assert metrics.pages_fetched == 5  # home, copy, about, contact, services
    assert metrics.duplicates == 1
    assert (metrics.http_pages, metrics.browser_pages) == (5, 0)
    assert metrics.requests == sum(bakery.hits(path) for path in {p for p, _ in bakery.requests})
    assert metrics.latency_p50_ms is not None
    assert metrics.latency_max_ms is not None
    assert run.errors == []
    assert run.site_count == run.sites_done == 1

    # a decision is final
    with pytest.raises(sqlite3.IntegrityError, match="final"):
        conn.execute(
            "UPDATE crawl_urls SET reason = 'rewritten' WHERE id = ?", (home.crawl_url.id,)
        )
    with pytest.raises(sqlite3.IntegrityError, match="permanent"):
        conn.execute("DELETE FROM crawl_urls")


async def test_sitemaps_with_the_same_findings_share_one_raw_record(
    conn: sqlite3.Connection, data_dir: Path
) -> None:
    # sitemap findings carry no address, so two empty sitemaps hash alike (2026-10-16 crawls)
    site = FixtureSite(
        {
            "/robots.txt": Reply(
                content_type="text/plain",
                body="User-agent: *\nSitemap: {base}/sitemap.xml\nSitemap: {base}/sitemap.rss\n",
            ),
            "/sitemap.xml": Reply(content_type="application/xml", body="<urlset></urlset>"),
            "/sitemap.rss": Reply(content_type="application/xml", body="<urlset></urlset>"),
            "/": Reply(body=html("Home", "<main><p>Hello.</p></main>")),
        }
    )
    try:
        service = crawler(data_dir)
        run = await crawl(service, site.url("/"))
        await service.shutdown()
        rows = urls_of(conn, run)
        first, second = (
            detail(conn, rows[site.url(path)]) for path in ("/sitemap.xml", "/sitemap.rss")
        )
        assert first.crawl_url.status is second.crawl_url.status is CrawlUrlStatus.FETCHED
        assert first.crawl_url.raw_record_id is not None
        assert first.crawl_url.raw_record_id == second.crawl_url.raw_record_id
        assert run.errors == []
    finally:
        site.close()


# ---- redirects -----------------------------------------------------------------------------------


async def test_redirect_loops_chains_and_escapes_are_audited(
    conn: sqlite3.Connection, data_dir: Path
) -> None:
    site = FixtureSite(
        {
            "/robots.txt": Reply(404),
            "/": Reply(
                body=html(
                    "Links", '<a href="/loop-a">a</a> <a href="/far">f</a> <a href="/out">o</a>'
                )
            ),
            "/loop-a": Reply(302, headers={"Location": "/loop-b"}),
            "/loop-b": Reply(302, headers={"Location": "/loop-a"}),
            **{
                f"/far{'-' * n}": Reply(302, headers={"Location": f"/far{'-' * (n + 1)}"})
                for n in range(8)
            },
            "/out": Reply(302, headers={"Location": "https://elsewhere.example/"}),
        }
    )
    try:
        service = crawler(data_dir)
        run = await crawl(service, site.url("/"))
        await service.shutdown()
        rows = urls_of(conn, run)
        loop = detail(conn, rows[site.url("/loop-a")])
        assert (loop.crawl_url.status, loop.crawl_url.reason) == (
            CrawlUrlStatus.FAILED,
            "redirect_loop",
        )
        assert loop.crawl_url.redirects_json is not None
        assert len(loop.crawl_url.redirects_json["hops"]) == 2  # type: ignore[arg-type]
        assert rows[site.url("/far")].reason == "too_many_redirects"
        # five redirects are followed, the sixth is refused without a request
        assert (site.hits("/far-----"), site.hits("/far------")) == (1, 0)
        assert rows[site.url("/out")].reason == "redirect_out_of_scope"
        assert {group.reason for group in run.errors} == {
            "redirect_loop",
            "too_many_redirects",
            "redirect_out_of_scope",
        }
    finally:
        site.close()


# ---- failures ------------------------------------------------------------------------------------


async def test_transient_failures_are_retried_and_final_ones_are_not(
    conn: sqlite3.Connection, data_dir: Path
) -> None:
    def throttled(hit: int, _: dict[str, str]) -> Reply:
        if hit == 1:
            return Reply(429, "slow down", headers={"Retry-After": "1"})
        return Reply(body=html("Throttled", "<p>finally</p>"))

    def flaky(hit: int, _: dict[str, str]) -> Reply:
        return Reply(503, "busy") if hit <= 2 else Reply(body=html("Flaky", "<p>back</p>"))

    links = "".join(
        f'<a href="/{path}">{path}</a> '
        for path in ("throttled", "flaky", "broken", "gone", "slow")
    )
    site = FixtureSite(
        {
            "/robots.txt": Reply(404),
            "/": Reply(body=html("Failures", links)),
            "/throttled": throttled,
            "/flaky": flaky,
            "/broken": Reply(500, "boom"),
            "/gone": Reply(404),
            "/slow": Reply(body=html("Slow", ""), delay_s=2.5),
        }
    )
    try:
        service = crawler(data_dir)
        run = await crawl(service, site.url("/"))
        await service.shutdown()
        rows = urls_of(conn, run)
        throttled_row = detail(conn, rows[site.url("/throttled")]).crawl_url
        assert (throttled_row.status, throttled_row.retry_count) == (CrawlUrlStatus.FETCHED, 1)
        assert rows[site.url("/flaky")].status is CrawlUrlStatus.FETCHED
        assert site.hits("/flaky") == 3
        assert rows[site.url("/broken")].reason == "server_error"
        assert site.hits("/broken") == 3, "a 500 is transient: three attempts, then it fails"
        assert rows[site.url("/gone")].reason == "not_found"
        assert site.hits("/gone") == 1, "a 404 is final: never retried"
        assert rows[site.url("/slow")].reason == "timeout"
        assert run.metrics.retries >= 1 + 2 + 2
        assert {group.reason: group.count for group in run.errors} == {
            "server_error": 1,
            "not_found": 1,
            "timeout": 1,
        }
    finally:
        site.close()


async def test_retry_after_is_honoured(data_dir: Path) -> None:
    moments: list[float] = []

    def throttled(hit: int, _: dict[str, str]) -> Reply:
        moments.append(time.monotonic())
        if hit == 1:
            return Reply(429, "slow down", headers={"Retry-After": "1"})
        return Reply(body=html("ok", ""))

    site = FixtureSite({"/robots.txt": Reply(404), "/": throttled})
    try:
        service = crawler(data_dir)
        run = await crawl(service, site.url("/"))
        await service.shutdown()
        assert run.metrics.pages_fetched == 1
        assert moments[1] - moments[0] >= 0.95, "the retry waited for what the server asked"
    finally:
        site.close()


async def test_oversized_and_wrongly_typed_answers_are_refused(
    conn: sqlite3.Connection, data_dir: Path
) -> None:
    big = "<p>" + "x" * (6 * 1024 * 1024) + "</p>"
    links = "".join(
        f'<a href="/{path}">{path}</a> ' for path in ("declared", "streamed", "image", "pdf")
    )
    site = FixtureSite(
        {
            "/robots.txt": Reply(404),
            "/": Reply(body=html("Types", links)),
            "/declared": Reply(body=big),
            # gzip: the declared length is small, the decompressed stream is not
            "/streamed": Reply(body=big, gzip=True),
            "/image": Reply(body=b"\x89PNG\r\n\x1a\n....", content_type="image/png"),
            "/pdf": Reply(body=b"%PDF-1.7 not a web page", content_type="text/html"),
        }
    )
    try:
        service = crawler(data_dir)
        run = await crawl(service, site.url("/"))
        await service.shutdown()
        rows = urls_of(conn, run)
        assert rows[site.url("/declared")].reason == "too_large"
        assert rows[site.url("/streamed")].reason == "too_large", "counted after decompression"
        assert rows[site.url("/image")].reason == "unsupported_type"
        assert rows[site.url("/pdf")].reason == "unsupported_type", "sniffed, not trusted"
        assert rows[site.url("/")].status is CrawlUrlStatus.FETCHED
    finally:
        site.close()


async def test_a_blocked_site_is_not_worked_around(
    conn: sqlite3.Connection, data_dir: Path
) -> None:
    challenge = "<title>Just a moment...</title><div id='cf-chl-widget'></div><script></script>"
    forbidden = FixtureSite({"/robots.txt": Reply(404), "/": Reply(403, "no bots")})
    walled = FixtureSite(
        {
            "/robots.txt": Reply(404),
            "/": Reply(body=challenge + '<a href="/contact">c</a>'),
            "/contact": Reply(body=html("Contact", "")),
        }
    )
    try:
        service = crawler(data_dir)
        run = await crawl(service, forbidden.url("/"), walled.url("/"), browser=BrowserMode.ALWAYS)
        await service.shutdown()
        by_site = {site.root_url: site for site in run.sites}
        for fixture in (forbidden, walled):
            site = by_site[fixture.url("/")]
            assert (site.status, site.error_code) == (SourceRunStatus.FAILED, "blocked")
        assert walled.hits("/contact") == 0, "nothing else is requested from a blocked site"
        assert run.metrics.browser_fallbacks == 0, "a challenge is never rendered to get past it"
        assert run.status is JobStatus.FAILED
    finally:
        forbidden.close()
        walled.close()


async def test_an_unreadable_robots_txt_stops_the_site(data_dir: Path) -> None:
    site = FixtureSite({"/robots.txt": Reply(503, "down"), "/": Reply(body=html("Home", ""))})
    try:
        service = crawler(data_dir)
        run = await crawl(service, site.url("/"))
        await service.shutdown()
        assert run.sites[0].error_code == "robots_unreachable"
        assert site.hits("/") == 0, "RFC 9309: an unreachable robots.txt disallows everything"
        assert site.hits("/robots.txt") == 3
    finally:
        site.close()


# ---- scope, budgets and filters ------------------------------------------------------------------


async def test_include_exclude_and_budgets(
    conn: sqlite3.Connection, data_dir: Path, bakery: FixtureSite
) -> None:
    service = crawler(data_dir)
    filtered = await crawl(
        service, bakery.url("/"), include_paths=["/about-*", "/contact"], exclude_paths=["/contact"]
    )
    rows = urls_of(conn, filtered)
    assert rows[bakery.url("/contact")].reason == "excluded"
    assert rows[bakery.url("/services")].reason == "not_included"
    assert rows[bakery.url("/about-us")].status is CrawlUrlStatus.FETCHED
    assert rows[bakery.url("/")].status is CrawlUrlStatus.FETCHED, "the home page always goes"

    capped = await crawl(service, bakery.url("/"), max_pages_per_site=2)
    await service.shutdown()
    assert capped.metrics.pages_fetched == 2
    skipped = [row for row in urls_of(conn, capped).values() if row.reason == "page_budget"]
    assert skipped, "what the budget left out is recorded, not silently dropped"
    assert any(
        event.event == "limit_reached" and (event.detail_json or {}).get("limit") == "page_budget"
        for event in capped.events
    )


async def test_the_request_budget_is_shared_by_every_site(
    data_dir: Path, bakery: FixtureSite
) -> None:
    service = crawler(data_dir)
    run = await crawl(service, bakery.url("/"), max_requests=3)
    await service.shutdown()
    assert run.metrics.requests <= 3 + 1, "at most one request in flight when the budget ran out"
    assert run.metrics.urls_skipped > 0


# ---- the browser fallback ------------------------------------------------------------------------

SHELL = """<!doctype html><html><head><title>Contact</title></head><body><div id="root"></div>
<script>document.getElementById('root').innerHTML =
  '<h1>Contacto</h1><p>Escríbanos a pedidos@sol.example o llame al 512-555-0199.</p>';</script>
</body></html>"""


@pytest.fixture
def js_site() -> Iterator[FixtureSite]:
    site = FixtureSite(
        {
            "/robots.txt": Reply(404),
            "/": Reply(body=html("Home", '<main><a href="/contact">Contact</a></main>')),
            "/contact": Reply(body=SHELL),
        }
    )
    yield site
    site.close()


async def test_a_javascript_page_is_rendered_only_when_a_criterion_asks(
    conn: sqlite3.Connection, data_dir: Path, js_site: FixtureSite
) -> None:
    service = crawler(data_dir)
    automatic = urls_of(conn, await crawl(service, js_site.url("/"), browser=BrowserMode.AUTO))
    never = urls_of(conn, await crawl(service, js_site.url("/"), browser=BrowserMode.NEVER))
    always_run = await crawl(
        service, js_site.url("/"), browser=BrowserMode.ALWAYS, screenshots=True
    )
    always = urls_of(conn, always_run)
    await service.shutdown()

    contact = detail(conn, automatic[js_site.url("/contact")])
    assert contact.crawl_url.method is AcquisitionMethod.BROWSER_RENDER
    assert contact.crawl_url.fallback_reason == "empty_shell"
    assert (contact.crawl_url.browser_ms or 0) > 0
    assert contact.raw_record is not None
    payload = contact.raw_record.payload_json
    assert {"value": "pedidos@sol.example", "origin": "text"} in payload["emails"]  # type: ignore[operator]
    fallback = payload["fallback"]
    assert isinstance(fallback, dict)
    assert fallback["reason"] == "empty_shell"
    signals = fallback["http_signals"]
    assert isinstance(signals, dict)
    assert signals["text_chars"] == 0
    assert automatic[js_site.url("/")].method is AcquisitionMethod.HTTP_FETCH, "HTTP was enough"

    plain = detail(conn, never[js_site.url("/contact")])
    assert plain.crawl_url.method is AcquisitionMethod.HTTP_FETCH
    assert plain.raw_record is not None
    assert plain.raw_record.payload_json["emails"] == []

    home = detail(conn, always[js_site.url("/")])
    assert (home.crawl_url.method, home.crawl_url.fallback_reason) == (
        AcquisitionMethod.BROWSER_RENDER,
        "configured",
    )
    assert home.crawl_url.screenshot_path is not None
    assert (data_dir / home.crawl_url.screenshot_path).read_bytes().startswith(b"\x89PNG")
    assert always_run.metrics.browser_fallbacks == 2


def _chromium_processes(marker: str) -> list[str]:
    """PIDs of browser processes started with `marker` on their command line."""
    if sys.platform == "win32":
        command = (
            "Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'chrom|msedge' "
            "-and $_.CommandLine -like '*" + marker + "*' } | ForEach-Object { $_.ProcessId }"
        )
        found = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        )
    else:
        found = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True, check=False)  # noqa: S607
    return [line.strip() for line in found.stdout.splitlines() if line.strip()]


async def test_cancelling_a_render_closes_the_browser_and_leaves_no_process(
    data_dir: Path,
) -> None:
    marker = f"--lead-test-marker={uuid.uuid4().hex}"
    site = FixtureSite(
        {
            "/robots.txt": Reply(404),
            "/": Reply(body='<body><div id="root"></div><script src="/app.js"></script></body>'),
            "/app.js": Reply(
                body="document.body.innerHTML='late';", content_type="text/javascript"
            ),
        }
    )
    site.gates["/app.js"] = threading.Event()
    try:
        service = crawler(data_dir, browser_args=(marker,))
        started = await service.start(
            CrawlStartRequest(
                project_id=None,
                campaign_id=None,
                urls=[site.url("/")],
                settings=crawl_settings(browser=BrowserMode.AUTO),
            )
        )
        async with asyncio.timeout(30):
            while site.hits("/app.js") == 0:  # noqa: ASYNC110 - waiting on the browser's own request
                await asyncio.sleep(0.05)
        assert _chromium_processes(marker), "the browser is rendering"
        cancelled = await service.control(
            DiscoveryControlRequest(job_id=started.job_id, action=RunControlAction.CANCEL)
        )
        assert cancelled.status is JobStatus.CANCELLED
        assert cancelled.sites[0].status is SourceRunStatus.CANCELLED
        deadline = time.monotonic() + 15
        while _chromium_processes(marker) and time.monotonic() < deadline:  # noqa: ASYNC110
            await asyncio.sleep(0.25)
        assert _chromium_processes(marker) == [], "no Chromium outlives a cancelled crawl"
        await service.shutdown()
    finally:
        site.close()


CONTAINED_WORKER = """
import asyncio, sys
from research_engine.crawl.browser import BrowserPool
async def main():
    pool = BrowserPool(
        user_agent="test", allow_private=True, concurrency=1, launch_args=(sys.argv[1],)
    )
    await pool._ensure()
    print("ready", flush=True)
    await asyncio.sleep(120)
asyncio.run(main())
"""


@pytest.mark.skipif(sys.platform != "win32", reason="the kill-on-close job object is Windows-only")
def test_chromium_dies_with_a_worker_that_is_killed_outright(tmp_path: Path) -> None:
    marker = f"--lead-test-marker={uuid.uuid4().hex}"
    worker = subprocess.Popen(
        [sys.executable, "-c", CONTAINED_WORKER, marker],
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    try:
        assert worker.stdout is not None
        assert worker.stdout.readline().strip() == "ready"
        assert _chromium_processes(marker), "the contained worker launched a browser"
        worker.kill()  # no cleanup at all, like a crash
        worker.wait(timeout=10)
        deadline = time.monotonic() + 15
        while _chromium_processes(marker) and time.monotonic() < deadline:
            time.sleep(0.25)
        assert _chromium_processes(marker) == [], "Chromium outlived the worker that started it"
    finally:
        if worker.poll() is None:
            worker.kill()


# ---- SSRF ----------------------------------------------------------------------------------------


async def test_the_guard_refuses_private_targets_before_any_request(
    data_dir: Path, bakery: FixtureSite, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = crawler(data_dir, policy=FetchPolicy(read_timeout_s=1.0, attempt_deadline_s=3.0))
    run = await crawl(
        service,
        bakery.url("/"),
        "http://localhost:9/",
        "http://169.254.169.254/latest/meta-data/",
    )
    assert bakery.requests == [], "the default crawler never reached the local server"
    assert {site.error_code for site in run.sites} == {"unsafe_target"}
    assert run.status is JobStatus.FAILED

    # a public-looking name that resolves to a private address is refused at connect time
    async def rebinding(host: str, port: int) -> list[str]:
        return ["127.0.0.1"]

    monkeypatch.setattr(guard, "resolve", rebinding)
    port = bakery.base.rsplit(":", 1)[1]
    rebound = await crawl(service, f"http://rebind.example:{port}/")
    await service.shutdown()
    assert rebound.sites[0].error_code == "unsafe_target"
    assert bakery.requests == []


# ---- pause, resume, restart and cache ------------------------------------------------------------


async def test_a_paused_crawl_resumes_without_fetching_or_storing_twice(
    conn: sqlite3.Connection, data_dir: Path, bakery: FixtureSite
) -> None:
    bakery.gates["/contact"] = threading.Event()
    service = crawler(data_dir)
    started = await service.start(
        CrawlStartRequest(
            project_id=None,
            campaign_id=None,
            urls=[bakery.url("/")],
            settings=crawl_settings(per_host_concurrency=1),
        )
    )
    async with asyncio.timeout(30):
        while bakery.hits("/contact") == 0:  # noqa: ASYNC110 - the server is the signal
            await asyncio.sleep(0.02)
    paused = await service.control(
        DiscoveryControlRequest(job_id=started.job_id, action=RunControlAction.PAUSE)
    )
    assert paused.status is JobStatus.PAUSED
    assert paused.sites[0].status is SourceRunStatus.PAUSED
    bakery.gates["/contact"].set()
    await service.shutdown()
    before = list(bakery.requests)

    resumed_service = crawler(data_dir)  # a new worker, as after a restart
    assert await resumed_service.recover() == 0, "it was paused, not left running"
    await resumed_service.control(
        DiscoveryControlRequest(job_id=started.job_id, action=RunControlAction.RESUME)
    )
    run = await settle(resumed_service, started.job_id)
    await resumed_service.shutdown()
    assert run.status is JobStatus.SUCCEEDED

    again = [path for path, _ in bakery.requests[len(before) :]]
    fetched_before = {path for path, _ in before} - {"/contact"}
    assert not fetched_before & set(again), "a resumed crawl never repeats a stored URL"
    assert "/contact" in again, "the URL in flight at the pause is fetched on resume"
    (records,) = conn.execute(
        "SELECT count(*) FROM raw_records r JOIN source_runs s ON s.id = r.source_run_id "
        "WHERE s.job_id = ?",
        (started.job_id,),
    ).fetchone()
    stored_urls = conn.execute(
        "SELECT count(*), count(DISTINCT c.url) FROM crawl_urls c "
        "JOIN source_runs s ON s.id = c.source_run_id "
        "WHERE s.job_id = ?",
        (started.job_id,),
    ).fetchone()
    assert stored_urls[0] == stored_urls[1]
    assert records == run.metrics.pages_fetched - run.metrics.duplicates + 3, (
        "pages + robots + 2 sitemaps"
    )


async def test_a_crawl_left_running_by_a_dead_worker_is_paused(
    conn: sqlite3.Connection, data_dir: Path, bakery: FixtureSite
) -> None:
    bakery.gates["/about-us"] = threading.Event()
    service = crawler(data_dir)
    started = await service.start(
        CrawlStartRequest(
            project_id=None, campaign_id=None, urls=[bakery.url("/")], settings=crawl_settings()
        )
    )
    async with asyncio.timeout(30):
        while bakery.hits("/about-us") == 0:  # noqa: ASYNC110
            await asyncio.sleep(0.02)
    # the worker dies without a word: its task is gone, the store still says running
    task = service._tasks[started.job_id]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    bakery.gates["/about-us"].set()
    assert (await service.run(started.job_id)).status is JobStatus.RUNNING

    restarted = crawler(data_dir)
    assert await restarted.recover() == 1
    run = await restarted.run(started.job_id)
    assert run.status is JobStatus.PAUSED
    assert any(event.event == "run_interrupted" for event in run.events)
    await restarted.control(
        DiscoveryControlRequest(job_id=started.job_id, action=RunControlAction.RESUME)
    )
    finished = await settle(restarted, started.job_id)
    await restarted.shutdown()
    assert finished.status is JobStatus.SUCCEEDED


async def test_unchanged_pages_are_answered_from_the_cache(
    conn: sqlite3.Connection, data_dir: Path
) -> None:
    def tagged(_: int, headers: dict[str, str]) -> Reply:
        if headers.get("if-none-match") == '"v1"':
            return Reply(304, headers={"ETag": '"v1"'})
        return Reply(body=html("Tagged", "<main><p>unchanged</p></main>"), headers={"ETag": '"v1"'})

    site = FixtureSite({"/robots.txt": Reply(404), "/": tagged})
    try:
        service = crawler(data_dir)
        first = await crawl(service, site.url("/"))
        second = await crawl(service, site.url("/"))
        await service.shutdown()
        original = detail(conn, urls_of(conn, first)[site.url("/")])
        cached = detail(conn, urls_of(conn, second)[site.url("/")])
        assert (cached.crawl_url.http_status, cached.crawl_url.reason) == (304, "not_modified")
        assert cached.crawl_url.raw_record_id == original.crawl_url.raw_record_id
        assert cached.crawl_url.content_sha256 == original.crawl_url.content_sha256
        assert second.metrics.cache_hits == 1
        assert second.metrics.bytes == 0, "nothing was downloaded again"
    finally:
        site.close()


# ---- campaigns, listings and refusals ------------------------------------------------------------


async def found_campaign(
    conn: sqlite3.Connection, data_dir: Path, rows: list[str], *, candidates: int = 10
) -> tuple[str, str]:
    """A campaign whose discovery imported these `name,city,state,website` rows: its id and job."""
    spec = empty_criteria().model_copy(
        update={
            "sources": [
                SourceSelection(
                    connector_key="file.import", priority=50, max_records=None, config_json={}
                )
            ],
            "limits": DiscoveryLimits(
                max_candidates=candidates, max_requests=5, max_pages_per_source=5
            ),
        }
    )
    campaign_id = repository.save_campaign(
        conn,
        CampaignSaveRequest(
            campaign_id=None, project_id=None, name="Crawl me", objective=None, criteria=spec
        ),
        actor="test",
    )
    discovery = DiscoveryService(
        EngineSettings(data_dir=data_dir, user_agent="test", offline=True),
        lambda: repository.connect(data_dir),
    )
    found = await discovery.start(
        DiscoveryStartRequest(
            campaign_id=campaign_id,
            connector_keys=[],
            inputs={
                "file.import": {
                    "filename": "list.csv",
                    "content": "\n".join(["name,city,state,website", *rows]) + "\n",
                    "format": None,
                    "mapping": None,
                }
            },
        )
    )
    await discovery.idle(found.job_id)
    return campaign_id, found.job_id


async def test_a_campaign_crawls_its_candidates_websites(
    conn: sqlite3.Connection, data_dir: Path, bakery: FixtureSite
) -> None:
    rows = [
        f"Panaderia El Sol,Austin,TX,{bakery.url('/')}",
        f"Panaderia El Sol,Austin,TX,{bakery.url('/')}",
        "No Website Bakery,Austin,TX,",
    ]
    campaign_id, discovery_job = await found_campaign(conn, data_dir, rows)

    service = crawler(data_dir)
    run = await crawl(service, campaign_id=campaign_id, max_pages_per_site=1)
    await service.shutdown()
    assert run.campaign_name == "Crawl me"
    assert run.site_count == 1, (
        "the repeated candidate is not crawled twice, the one without a site not at all"
    )
    root = detail(conn, urls_of(conn, run)[bakery.url("/")])
    assert root.crawl_url.candidate_id == run.sites[0].candidate_id is not None

    listed = repository.list_crawl_runs(
        conn, CrawlRunListQuery(project_id=None, campaign_id=campaign_id, limit=10, cursor=None)
    )
    assert [item.job_id for item in listed.items] == [run.job_id]
    with pytest.raises(repository.NotFoundError):
        repository.get_crawl_run(conn, discovery_job)  # a discovery job is not a crawl


async def test_the_crawl_of_a_pipeline_takes_every_site_of_its_campaign(
    conn: sqlite3.Connection, data_dir: Path
) -> None:
    """The first hundred sites used to be all a search read: the rest of its list had no Latino
    evidence, no audit and no reason to call (D-103). Planned only; nothing is requested."""
    rows = [f"Negocio {n},Houston,TX,https://negocio-{n}.example/" for n in range(130)]
    campaign_id, _ = await found_campaign(conn, data_dir, rows, candidates=200)

    def planned(settings: CrawlSettings) -> tuple[int, int]:
        request = CrawlStartRequest(
            project_id=None, campaign_id=campaign_id, urls=[], settings=settings
        )
        job_id = plan_crawl(conn, request, [])
        (sites,) = conn.execute(
            "SELECT count(*) FROM source_runs WHERE job_id = ?", (job_id,)
        ).fetchone()
        (capped,) = conn.execute(
            "SELECT count(*) FROM job_events WHERE job_id = ? AND event = 'sites_capped'", (job_id,)
        ).fetchone()
        return sites, capped

    assert planned(default_crawl_settings()) == (100, 1), "a crawl someone starts by hand"
    assert planned(campaign_crawl_settings()) == (130, 0)
    # room for the largest search the simple screen asks for (1 200 businesses), 20 requests a site
    whole = campaign_crawl_settings()
    assert whole.max_sites >= 1_200
    assert whole.max_requests >= whole.max_sites * 20
    assert CrawlSettings.model_validate(whole.model_dump()) == whole, "inside the contract's bounds"


async def test_url_listings_page_and_filter(
    conn: sqlite3.Connection, data_dir: Path, bakery: FixtureSite
) -> None:
    service = crawler(data_dir)
    run = await crawl(service, bakery.url("/"))
    await service.shutdown()
    seen: list[str] = []
    cursor = None
    while True:
        page = repository.list_crawl_urls(
            conn,
            CrawlUrlListQuery(
                job_id=run.job_id, source_run_id=None, status=None, limit=4, cursor=cursor
            ),
        )
        seen += [item.id for item in page.items]
        if page.next_cursor is None:
            break
        cursor = page.next_cursor
    assert len(seen) == len(set(seen)) == page.total
    skipped = repository.list_crawl_urls(
        conn,
        CrawlUrlListQuery(
            job_id=run.job_id,
            source_run_id=run.sites[0].source_run_id,
            status=CrawlUrlStatus.SKIPPED,
            limit=50,
            cursor=None,
        ),
    )
    assert skipped.total == run.metrics.urls_skipped
    assert {item.status for item in skipped.items} == {CrawlUrlStatus.SKIPPED}


async def test_a_crawl_needs_something_to_crawl(data_dir: Path) -> None:
    service = crawler(data_dir)
    with pytest.raises(CrawlStartError, match="campaign or give"):
        await service.start(
            CrawlStartRequest(project_id=None, campaign_id=None, urls=[], settings=crawl_settings())
        )
    with pytest.raises(CrawlStartError, match="usable"):
        await service.start(
            CrawlStartRequest(
                project_id=None,
                campaign_id=None,
                urls=["mailto:x@y.example"],
                settings=crawl_settings(),
            )
        )
