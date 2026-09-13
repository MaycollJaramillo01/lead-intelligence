"""Website audits against the store and the crawler (phase 08): snapshots, evidence and history,
recalculation with newer rules, partial audits, jobs, performance, and a crawler the audits only
read. Synthetic test data only; the local fixture site binds to 127.0.0.1, the explicit test mode
of the SSRF guard (as in test_crawl.py)."""

import asyncio
import contextlib
import shutil
import sqlite3
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from research_engine import jobs, repository
from research_engine.crawl.engine import CrawlerSettings, CrawlService
from research_engine.crawl.fetch import FetchPolicy
from research_engine.domain import (
    AcquisitionMethod,
    BrowserMode,
    CrawlRun,
    CrawlStartRequest,
    DiscoveryControlRequest,
    JobStatus,
    RunControlAction,
    WebsiteAuditDetail,
    WebsiteAuditListQuery,
    WebsiteAuditPage,
    WebsiteAuditStatus,
    WebsiteCategory,
    WebsiteFindingResult,
    WebsiteFindingSeverity,
    WebsiteStartRequest,
    default_crawl_settings,
    utc_now,
)
from research_engine.repository import InvalidCursorError
from research_engine.testdata import (
    WEBSITE_SITES,
    SyntheticContext,
    insert_site_pages,
    seed_website,
    synthetic_complete_site,
    synthetic_connector,
    synthetic_page_html,
    synthetic_project,
    synthetic_run,
)
from research_engine.website import engine, rules, store

pytestmark = pytest.mark.anyio

PASS, WARNING, FAIL = (
    WebsiteFindingResult.PASS,
    WebsiteFindingResult.WARNING,
    WebsiteFindingResult.FAIL,
)
SETTLE_TIMEOUT_S = 60.0


def audits(
    conn: sqlite3.Connection, project_id: str, site: str | None = None, **query: object
) -> WebsiteAuditPage:
    values: dict[str, object] = {
        "project_id": project_id,
        "site": site,
        "limit": 50,
        "cursor": None,
    }
    return store.list_website_audits(conn, WebsiteAuditListQuery.model_validate(values | query))


def audit_project(conn: sqlite3.Connection, data_dir: Path, project_id: str) -> str:
    created, _ = store.plan_runs(conn, project_id, [], actor="test")
    assert engine.run_website(conn, data_dir, created[0], lambda: False)
    return created[0]


def results(detail: WebsiteAuditDetail) -> dict[str, WebsiteFindingResult]:
    return {finding.rule_id: finding.result for finding in detail.findings}


def table_counts(conn: sqlite3.Connection, *tables: str) -> dict[str, int]:
    return {
        table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # noqa: S608
        for table in tables
    }


# ---- audits of stored crawls ---------------------------------------------------------------------


def test_seeded_sites_are_audited_with_evidence_history_and_their_difference(
    conn: sqlite3.Connection, data_dir: Path, synthetic: SyntheticContext
) -> None:
    seeded = seed_website(conn, synthetic, data_dir)
    page = audits(conn, synthetic.project_id)
    assert [item.site for item in page.items] == sorted(WEBSITE_SITES.values())
    complete = next(item for item in page.items if item.site == seeded.sites["complete"])
    assert (complete.status, complete.pages_analyzed, complete.failed) == (
        WebsiteAuditStatus.COMPLETE,
        7,
        0,
    )
    assert complete.previous_audit_id is None

    changed = next(item for item in page.items if item.site == seeded.sites["changed"])
    detail = store.get_website_audit(conn, changed.id)
    assert detail.audit == changed
    assert detail.diff is not None
    assert detail.diff.previous_audit_id == changed.previous_audit_id
    assert detail.diff.technologies_added == ["google_analytics_4"]
    assert {item.rule_id for item in detail.diff.findings} >= {
        "conversion.click_to_call",
        "mobile.viewport",
        "measurement.analytics",
    }
    assert [item.id for item in detail.history] == [changed.id, changed.previous_audit_id]
    assert detail.summary.subscores == changed.subscores
    counted = results(detail)
    assert sum(result is FAIL for result in counted.values()) == changed.failed
    assert sum(result is WARNING for result in counted.values()) == changed.warnings
    assert detail.findings[0].result is FAIL, "actionable findings first"

    # evidence points at what the audited crawl stored
    crawl_ids = {
        row[0]
        for row in conn.execute(
            "SELECT id FROM crawl_urls WHERE source_run_id = ?", (changed.source_run_id,)
        )
    }
    cited = {ref.crawl_url_id for item in detail.findings for ref in item.evidence}
    assert cited - {None}
    assert cited - {None} <= crawl_ids
    previous = store.get_website_audit(conn, str(changed.previous_audit_id))
    assert previous.audit.source_run_id != changed.source_run_id
    assert results(previous)["conversion.click_to_call"] is FAIL

    # the second run audited only what changed; a third writes nothing
    run = store.get_website_run(conn, seeded.job_ids[1])
    assert (run.status, run.stats.audited, run.stats.unchanged) == (JobStatus.SUCCEEDED, 1, 1)
    before = table_counts(conn, "website_audits", "website_findings")
    third = store.get_website_run(conn, audit_project(conn, data_dir, synthetic.project_id))
    assert (third.stats.audited, third.stats.unchanged) == (0, 2)
    assert table_counts(conn, "website_audits", "website_findings") == before

    # history pages newest first and is never edited nor deleted
    first = audits(conn, synthetic.project_id, seeded.sites["changed"], limit=1)
    assert first.total == 2
    assert first.next_cursor is not None
    rest = audits(conn, synthetic.project_id, seeded.sites["changed"], cursor=first.next_cursor)
    assert [item.id for item in rest.items] == [changed.previous_audit_id]
    with pytest.raises(InvalidCursorError):
        audits(conn, synthetic.project_id, cursor=first.next_cursor)
    with pytest.raises(sqlite3.IntegrityError, match="history"):
        conn.execute("UPDATE website_audits SET coverage = 0")
    with pytest.raises(sqlite3.IntegrityError, match="permanent"):
        conn.execute("DELETE FROM website_findings")


def test_newer_rules_recalculate_from_the_snapshot_without_touching_history(
    conn: sqlite3.Connection, data_dir: Path, synthetic: SyntheticContext
) -> None:
    seeded = seed_website(conn, synthetic, data_dir)
    site = seeded.sites["complete"]
    (old,) = audits(conn, synthetic.project_id, site).items
    old_detail = store.get_website_audit(conn, old.id)
    (crawl,) = store.crawled_sites(conn, synthetic.project_id, [site])
    stricter = rules.Ruleset(
        "1.1.0",
        tuple(
            replace(rule, version="1.1.0", severity=WebsiteFindingSeverity.CRITICAL)
            if rule.id == "seo.duplicate_metadata"
            else replace(rule, version="1.1.0")
            for rule in rules.RULES
        ),
    )
    shutil.rmtree(data_dir / "artifacts")  # the stored snapshot is all a recalculation needs
    result = store.audit_site(
        conn, data_dir, synthetic.project_id, crawl, None, utc_now(), stricter
    )
    assert result is not None
    assert result.pages_analyzed == 7
    assert (
        store.audit_site(conn, data_dir, synthetic.project_id, crawl, None, utc_now(), stricter)
        is None
    )

    new, _ = audits(conn, synthetic.project_id, site).items
    assert (new.ruleset_version, new.previous_audit_id, new.source_run_id) == (
        "1.1.0",
        old.id,
        old.source_run_id,
    )
    detail = store.get_website_audit(conn, new.id)
    assert detail.diff is not None
    assert detail.diff.ruleset_changed
    seo = {score.category: score.score for score in new.subscores}[WebsiteCategory.SEO]
    old_seo = {score.category: score.score for score in old.subscores}[WebsiteCategory.SEO]
    assert seo is not None
    assert old_seo is not None
    assert seo < old_seo
    again = store.get_website_audit(conn, old.id)
    assert (again.audit, again.summary, again.findings) == (
        old_detail.audit,
        old_detail.summary,
        old_detail.findings,
    )


def test_pages_without_stored_html_make_partial_and_empty_audits(
    conn: sqlite3.Connection, data_dir: Path, synthetic: SyntheticContext
) -> None:
    gone, patchy = "synthetic-gone-0704.example", "synthetic-patchy-0705.example"
    insert_site_pages(conn, synthetic, gone, {"/": synthetic_page_html("en", "Gone", "Test data.")})
    insert_site_pages(conn, synthetic, patchy, synthetic_complete_site(patchy, synthetic.now.year),
                      data_dir=data_dir)  # fmt: skip
    (artifact,) = conn.execute(
        "SELECT a.artifact_path FROM crawl_urls c JOIN raw_records r ON r.id = c.raw_record_id "
        "JOIN acquired_pages a ON a.id = r.acquired_page_id WHERE c.url = ?",
        (f"https://{patchy}/about",),
    ).fetchone()
    (data_dir / artifact).unlink()
    audit_project(conn, data_dir, synthetic.project_id)
    by_site = {item.site: item for item in audits(conn, synthetic.project_id).items}

    empty = store.get_website_audit(conn, by_site[gone].id)
    assert (empty.audit.status, empty.audit.pages_analyzed) == (WebsiteAuditStatus.EMPTY, 0)
    assert empty.summary.coverage.gaps == ["artifact_missing"]
    assert results(empty)["seo.title"] is WebsiteFindingResult.UNKNOWN
    assert empty.audit.coverage < 0.5

    partial = store.get_website_audit(conn, by_site[patchy].id)
    assert (partial.audit.status, partial.audit.pages_analyzed, partial.audit.pages_expected) == (
        WebsiteAuditStatus.PARTIAL,
        6,
        7,
    )
    (missing,) = [view for view in partial.summary.pages if not view.analyzed]
    assert (missing.url, missing.gap) == (f"https://{patchy}/about", "artifact_missing")


# ---- audits of real crawls -----------------------------------------------------------------------

POLICY = FetchPolicy(
    connect_timeout_s=2.0,
    read_timeout_s=1.0,
    attempt_deadline_s=3.0,
    backoff_base_s=0.05,
    spacing_factor=0.01,
    allow_private=True,
)
Route = tuple[int, str, str, dict[str, str]]


class LocalSite:
    """A website on 127.0.0.1: path -> (status, content type, body, headers). `{base}` in a body or
    a header is replaced with the site's own address."""

    def __init__(self, routes: dict[str, Route]) -> None:
        self.routes = routes
        site = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                return

            def do_GET(self) -> None:
                status, content_type, body, headers = site.routes.get(
                    self.path, (404, "text/plain", "not here", {})
                )
                data = body.replace("{base}", site.base).encode()
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                for key, value in headers.items():
                    self.send_header(key, value.replace("{base}", site.base))
                self.end_headers()
                with contextlib.suppress(OSError):
                    self.wfile.write(data)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.base = f"http://127.0.0.1:{self._server.server_address[1]}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


async def crawl(data_dir: Path, url: str, browser: BrowserMode) -> CrawlRun:
    service = CrawlService(
        CrawlerSettings(
            data_dir=data_dir,
            user_agent="LeadIntelligence/test (+website fixture)",
            policy=POLICY,
            http_slots=4,
            browser_slots=1,
        ),
        lambda: repository.connect(data_dir),
    )
    settings = default_crawl_settings().model_copy(
        update={"requests_per_minute_per_host": 120, "browser": browser}
    )
    started = await service.start(
        CrawlStartRequest(project_id=None, campaign_id=None, urls=[url], settings=settings)
    )
    try:
        async with asyncio.timeout(SETTLE_TIMEOUT_S):
            while True:
                if (await service.run(started.job_id)).status not in (
                    JobStatus.QUEUED,
                    JobStatus.RUNNING,
                ):
                    break
                await asyncio.sleep(0.05)
        await service.idle(started.job_id)
        return await service.run(started.job_id)
    finally:
        await service.shutdown()


def html(title: str, body: str, head: str = "") -> str:
    return (
        f"<!doctype html><html lang='en'><head><title>{title}</title>{head}</head>"
        f"<body>{body}</body></html>"
    )


async def test_a_crawled_site_is_audited_from_what_the_crawler_stored(
    conn: sqlite3.Connection, data_dir: Path
) -> None:
    page = "text/html; charset=utf-8"
    home = html(
        "Synthetic Bikes 0706 | Bike repair (test data)",
        "<header><nav><a href='/about'>About us</a> <a href='/missing'>Old offers</a></nav>"
        "</header>"
        "<main><h1>Bike repair</h1><a class='btn' href='tel:+15125550176'>Call now</a>"
        "<p>Synthetic test data.</p></main>",
        head="<script async src='https://www.googletagmanager.com/gtag/js?id=G-TEST0706'></script>",
    )
    site = LocalSite({
        "/robots.txt": (200, "text/plain",
                        "User-agent: GPTBot\nDisallow: /\n\nUser-agent: *\nDisallow: /private/\n"
                        "Sitemap: {base}/sitemap.xml\n", {}),
        "/sitemap.xml": (200, "application/xml",
                         "<urlset><url><loc>{base}/about</loc></url></urlset>", {}),
        "/": (301, page, "", {"Location": "/home"}),
        "/home": (200, page, home, {}),
        "/about": (200, page, html("About us | Synthetic Bikes 0706", "<h1>About us</h1>"), {}),
    })  # fmt: skip
    try:
        run = await crawl(data_dir, f"{site.base}/", BrowserMode.NEVER)
    finally:
        site.close()
    crawl_tables = ("crawl_urls", "raw_records", "acquired_pages", "source_runs")
    stored = table_counts(conn, *crawl_tables)
    audit_project(conn, data_dir, run.project_id)
    assert table_counts(conn, *crawl_tables) == stored, "an audit only reads what the crawl stored"

    (item,) = audits(conn, run.project_id).items
    detail = store.get_website_audit(conn, item.id)
    found = results(detail)
    assert item.final_url == f"{site.base}/home"
    assert found["technical.redirect_chain"] is PASS
    assert found["technical.domain_redirect"] is PASS
    assert found["technical.broken_links"] is FAIL
    assert found["technical.page_errors"] is FAIL
    assert found["technical.https"] is FAIL
    assert found["seo.sitemap"] is PASS
    assert found["seo.robots_txt"] is PASS
    assert found["aeo_geo.ai_crawlers"] is WARNING
    assert found["measurement.analytics"] is PASS
    assert found["conversion.click_to_call"] is PASS
    assert item.status is WebsiteAuditStatus.PARTIAL, "a linked page failed"
    views = {view.url: view for view in detail.summary.pages}
    assert views[f"{site.base}/missing"].gap == "fetch_failed"
    assert views[f"{site.base}/"].redirects == 1
    assert all(view.response_ms is not None for view in views.values() if view.analyzed)


async def test_a_rendered_page_is_audited_from_its_rendered_html(
    conn: sqlite3.Connection, data_dir: Path
) -> None:
    shell = (
        "<!doctype html><html lang='en'><head><title>Synthetic Tutors 0707</title></head><body>"
        "<div id='root'></div><script>document.getElementById('root').innerHTML = "
        '\'<h1>Synthetic Tutors 0707</h1><a class="btn" href="tel:+15125550177">Call now</a>\';'
        "</script></body></html>"
    )
    site = LocalSite({"/": (200, "text/html; charset=utf-8", shell, {})})
    try:
        run = await crawl(data_dir, f"{site.base}/", BrowserMode.AUTO)
    finally:
        site.close()
    audit_project(conn, data_dir, run.project_id)
    (item,) = audits(conn, run.project_id).items
    detail = store.get_website_audit(conn, item.id)
    found = results(detail)
    assert found["aeo_geo.content_without_javascript"] is FAIL
    assert found["conversion.click_to_call"] is PASS, "the call link only exists once rendered"
    (view,) = detail.summary.pages
    assert (view.method, view.device_profile) == (
        AcquisitionMethod.BROWSER_RENDER,
        "crawler_browser",
    )
    assert (view.render_ms or 0) > 0


# ---- jobs and performance ------------------------------------------------------------------------


async def test_audit_jobs_are_controlled_and_recovered(data_dir: Path) -> None:
    conn = repository.connect(data_dir)
    try:
        stamp = utc_now()
        project = synthetic_project(stamp)
        connector = synthetic_connector(stamp)
        seed_run = synthetic_run(project.id, connector.id, stamp)
        repository.insert(conn, project, connector, seed_run)
        ctx = SyntheticContext(project.id, connector.id, seed_run.id, stamp)
        site = "synthetic-jobs-0708.example"
        insert_site_pages(conn, ctx, site, {"/": synthetic_page_html("en", "Jobs", "Test data.")},
                          data_dir=data_dir)  # fmt: skip
        service = engine.WebsiteService(data_dir, lambda: repository.connect(data_dir))

        (started,) = (
            await service.start(WebsiteStartRequest(project_id=project.id, sites=[]))
        ).items
        await service.idle(started.job_id)
        assert store.get_website_run(conn, started.job_id).status is JobStatus.SUCCEEDED
        with pytest.raises(store.WebsiteError, match="none of these sites"):
            await service.start(
                WebsiteStartRequest(project_id=project.id, sites=["synthetic-unknown.example"])
            )
        with pytest.raises(store.WebsiteError, match="within one project"):
            await service.start(WebsiteStartRequest(project_id=None, sites=[site]))

        # a run a dead worker left running is paused at startup, then resumed by the operator
        created, _ = store.plan_runs(conn, project.id, [site], actor="test")
        jobs.mark_running(conn, created[0], "test")
        with pytest.raises(store.WebsiteError, match="already open"):
            await service.start(WebsiteStartRequest(project_id=project.id, sites=[]))
        assert await service.recover() == 1
        assert store.get_website_run(conn, created[0]).status is JobStatus.PAUSED
        await service.control(
            DiscoveryControlRequest(job_id=created[0], action=RunControlAction.RESUME)
        )
        await service.idle(created[0])
        resumed = store.get_website_run(conn, created[0])
        assert (resumed.status, resumed.stats.unchanged) == (JobStatus.SUCCEEDED, 1)

        queued, _ = store.plan_runs(conn, project.id, [], actor="test")
        cancelled = await service.control(
            DiscoveryControlRequest(job_id=queued[0], action=RunControlAction.CANCEL)
        )
        assert cancelled.status is JobStatus.CANCELLED
        assert not engine.run_website(conn, data_dir, queued[0], lambda: False)
        with pytest.raises(repository.NotFoundError):
            store.get_website_run(conn, seed_run.id)
        await service.shutdown()
    finally:
        conn.close()


def test_performance_on_many_crawled_sites(
    conn: sqlite3.Connection, data_dir: Path, synthetic: SyntheticContext
) -> None:
    sites = 80
    pages = synthetic_complete_site("synthetic-perf.example", synthetic.now.year)
    for index in range(sites):
        name = f"synthetic-perf-{index:04d}.example"
        insert_site_pages(conn, synthetic, name, dict(list(pages.items())[:5]), data_dir=data_dir)
    started = time.perf_counter()
    job_id = audit_project(conn, data_dir, synthetic.project_id)
    elapsed = time.perf_counter() - started
    run = store.get_website_run(conn, job_id)
    assert (run.stats.audited, run.stats.pages_analyzed) == (sites, sites * 5)
    assert elapsed < 60, f"{sites} sites of 5 pages took {elapsed:.1f} s"

    started = time.perf_counter()
    page = audits(conn, synthetic.project_id, limit=50)
    listed = time.perf_counter() - started
    started = time.perf_counter()
    store.get_website_audit(conn, page.items[0].id)
    detailed = time.perf_counter() - started
    assert page.total == sites
    assert listed < 1.0, listed
    assert detailed < 0.5, detailed
