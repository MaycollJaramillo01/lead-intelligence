"""Store side of website intelligence (phase 08): the only SQL it runs.

- Sites: the latest finished crawl of every site of a project (`web.crawler` source runs), its URL
  rows and the stored HTML of each page, read again with `extract.page_facts` into a snapshot.
- Audits: at most one per site, crawl, extractor version and ruleset version, so auditing again
  without a change writes nothing. Newer rules over the same crawl reuse the stored snapshot:
  history is recalculated into new audits, never edited. Each audit points at the previous audit of
  its site and stores what changed since.
"""

import logging
import sqlite3
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final

import orjson
from pydantic import JsonValue, ValidationError

from research_engine import jobs, repository
from research_engine.crawl.extract import decode
from research_engine.domain import (
    AcquisitionMethod,
    AuditAction,
    ConversionElement,
    CrawlTarget,
    CrawlUrlStatus,
    EntityType,
    EventLevel,
    Job,
    JobStatus,
    JsonObject,
    PageKind,
    Project,
    Record,
    SourceRunStatus,
    WebsiteAudit,
    WebsiteAuditDetail,
    WebsiteAuditItem,
    WebsiteAuditListQuery,
    WebsiteAuditPage,
    WebsiteAuditStatus,
    WebsiteAuditSummary,
    WebsiteConversionView,
    WebsiteDiff,
    WebsiteEvidence,
    WebsiteFinding,
    WebsiteFindingResult,
    WebsiteFindingView,
    WebsiteRun,
    WebsiteRunListQuery,
    WebsiteRunPage,
    WebsiteStats,
    WebsiteSubscore,
    WebsiteTechnology,
    WebsiteTechnologyView,
    new_id,
    utc_now,
)
from research_engine.storage import data_path
from research_engine.website import extract, rules

logger = logging.getLogger(__name__)

JOB_KIND: Final = "website_audit"
ACTOR: Final = "website-engine"
#: The key `crawl.engine` registers its connector under (not imported: it loads the browser stack).
CRAWL_CONNECTOR_KEY: Final = "web.crawler"
MAX_AUDIT_HISTORY: Final = 20
#: The crawler stores at most 5 MiB of a page; anything larger was not written by it.
MAX_ARTIFACT_BYTES: Final = 6 * 1024 * 1024
#: Skip reasons of `crawl.engine.SITE_LIMITS`: budgets that left pages out of a crawl.
CRAWL_LIMITS: Final = frozenset({"request_budget", "time_budget", "page_budget", "byte_budget"})
HTML_TYPES: Final = ("text/html", "application/xhtml+xml")


class WebsiteError(ValueError):
    """A website audit request that cannot be carried out as asked; the message says why."""


def _object(text: str | None) -> JsonObject:
    loaded = orjson.loads(text) if text else None
    return loaded if isinstance(loaded, dict) else {}


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


# ---- sites and snapshots -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CrawledSite:
    site: str
    source_run_id: str
    root_url: str
    status: SourceRunStatus
    crawler_version: str
    finished_at: datetime


_CRAWLS_SQL: Final = (
    "SELECT r.id, r.status, r.connector_version, "
    "coalesce(r.finished_at, r.updated_at) AS finished, "
    "json_extract(r.effective_config_json, '$.site') AS site, "
    "json_extract(r.effective_config_json, '$.root_url') AS root_url "
    "FROM source_runs r JOIN source_connectors sc ON sc.id = r.connector_id "
    "WHERE r.project_id = ? AND sc.key = ? AND r.status IN ('succeeded', 'failed', 'cancelled') "
    "ORDER BY finished DESC, r.created_at DESC, r.id DESC"
)


def crawled_sites(
    conn: sqlite3.Connection, project_id: str, sites: Sequence[str] = ()
) -> list[CrawledSite]:
    """The latest finished crawl of every site of a project (or of the sites given), by site."""
    latest: dict[str, CrawledSite] = {}
    for row in conn.execute(_CRAWLS_SQL, (project_id, CRAWL_CONNECTOR_KEY)):
        site = row["site"]
        if not isinstance(site, str) or site in latest or (sites and site not in sites):
            continue
        latest[site] = CrawledSite(
            site=site,
            source_run_id=row["id"],
            root_url=row["root_url"] if isinstance(row["root_url"], str) else f"https://{site}/",
            status=SourceRunStatus(row["status"]),
            crawler_version=row["connector_version"],
            finished_at=_ts(row["finished"]),
        )
    return [latest[site] for site in sorted(latest)]


_ROWS_SQL: Final = (
    "SELECT c.id, c.url, c.target, c.page_kind, c.depth, c.status, c.reason, c.method, "
    "c.final_url, c.http_status, c.byte_size, c.duplicate_of_id, c.fetch_ms, c.browser_ms, "
    "c.fallback_reason, c.redirects_json, c.headers_json, c.updated_at, a.artifact_path, "
    "a.content_type AS stored_type, "
    "CASE WHEN c.target <> 'page' THEN rr.payload_json END AS payload_json "
    "FROM crawl_urls c LEFT JOIN raw_records rr ON rr.id = c.raw_record_id "
    "LEFT JOIN acquired_pages a ON a.id = rr.acquired_page_id "
    "WHERE c.source_run_id = ? ORDER BY c.depth, c.url, c.id"
)


def _facts(
    data_dir: Path, row: sqlite3.Row, final_url: str, *, technologies: bool = True
) -> tuple[extract.PageFacts | None, str | None]:
    """The facts of a fetched page from the bytes the crawler stored (the rendered HTML when it
    rendered one, the cached copy when the server answered 304), or why there are none."""
    if row["artifact_path"] is None:
        return None, "artifact_missing"
    content_type = row["stored_type"]
    if content_type and not str(content_type).lower().startswith(HTML_TYPES):
        return None, "not_html"
    try:
        path = data_path(data_dir, row["artifact_path"])
        if path.stat().st_size > MAX_ARTIFACT_BYTES:
            return None, "too_large"
        body = path.read_bytes()
    except (OSError, ValueError):
        return None, "artifact_missing"
    try:
        html = decode(body, content_type)[0]
        return extract.page_facts(html, row["url"], final_url, technologies=technologies), None
    except Exception:
        logger.warning("a stored page could not be read", exc_info=True, extra={"url": row["url"]})
        return None, "unreadable"


def _hops(text: str | None) -> tuple[rules.Hop, ...]:
    """The redirects the crawler followed, as `crawl_urls.redirects_json` records them."""
    found = _object(text).get("hops")
    hops: list[rules.Hop] = []
    for hop in found if isinstance(found, list) else []:
        if not isinstance(hop, dict):
            continue
        status, location = hop.get("status"), hop.get("location")
        hops.append(
            rules.Hop(
                str(hop.get("url")),
                status if isinstance(status, int) else 0,
                location if isinstance(location, str) else None,
            )
        )
    return tuple(hops)


def load_site(conn: sqlite3.Connection, data_dir: Path, crawl: CrawledSite) -> rules.SiteInput:
    """The snapshot of one crawl: every page row with its facts, robots.txt and the sitemaps."""
    robots: rules.RobotsInput | None = None
    sitemaps: list[rules.SitemapInput] = []
    pages: list[rules.PageInput] = []
    limits: set[str] = set()
    crawled_at = crawl.finished_at
    rows = conn.execute(_ROWS_SQL, (crawl.source_run_id,)).fetchall()
    if rows:
        crawled_at = max(_ts(row["updated_at"]) for row in rows)
    for row in rows:
        status = CrawlUrlStatus(row["status"])
        if status is CrawlUrlStatus.SKIPPED:
            if row["reason"] in CRAWL_LIMITS:
                limits.add(row["reason"])
            continue
        target = CrawlTarget(row["target"])
        payload = _object(row["payload_json"])
        if target is CrawlTarget.ROBOTS:
            text = payload.get("text")
            robots = rules.RobotsInput(
                row["url"],
                row["id"],
                status,
                row["reason"],
                text if isinstance(text, str) else None,
            )
            continue
        if target is CrawlTarget.SITEMAP:
            count = payload.get("location_count")
            sitemaps.append(
                rules.SitemapInput(
                    row["url"],
                    row["id"],
                    status,
                    row["reason"],
                    count if isinstance(count, int) else None,
                )
            )
            continue
        redirects = _hops(row["redirects_json"])
        headers = tuple(
            sorted((str(key), value) for key, value in _object(row["headers_json"]).items()
                   if isinstance(value, str))
        )  # fmt: skip
        final_url = row["final_url"] or row["url"]
        facts: extract.PageFacts | None = None
        if row["duplicate_of_id"] is not None:
            gap: str | None = "duplicate"
        elif status is CrawlUrlStatus.FAILED:
            gap = "fetch_failed"
        elif status is CrawlUrlStatus.QUEUED:
            gap = "not_fetched"
        else:
            facts, gap = _facts(data_dir, row, final_url)
        pages.append(
            rules.PageInput(
                crawl_url_id=row["id"],
                url=row["url"],
                final_url=row["final_url"],
                page_kind=PageKind(row["page_kind"]),
                depth=row["depth"],
                status=status,
                reason=row["reason"],
                http_status=row["http_status"],
                method=AcquisitionMethod(row["method"]) if row["method"] else None,
                fallback_reason=row["fallback_reason"],
                redirects=redirects,
                headers=headers,
                fetch_ms=row["fetch_ms"],
                browser_ms=row["browser_ms"],
                byte_size=row["byte_size"],
                duplicate_of_id=row["duplicate_of_id"],
                measured_at=_ts(row["updated_at"]),
                gap=gap,
                facts=facts,
            )
        )
    return rules.SiteInput(
        site=crawl.site,
        root_url=crawl.root_url,
        crawl_status=crawl.status,
        crawler_version=crawl.crawler_version,
        crawled_at=crawled_at,
        extractor_version=extract.EXTRACTOR_VERSION,
        limits_hit=tuple(sorted(limits)),
        robots=robots,
        sitemaps=tuple(sitemaps),
        pages=tuple(pages),
    )


# ---- audits ------------------------------------------------------------------------------------


def _refs(items: Sequence[WebsiteEvidence]) -> JsonObject:
    return {"refs": list[JsonValue](item.model_dump(mode="json") for item in items)}


def _evidence(text: str | None) -> list[WebsiteEvidence]:
    found = _object(text).get("refs")
    return (
        [WebsiteEvidence.model_validate(item) for item in found] if isinstance(found, list) else []
    )


def _comparable(conn: sqlite3.Connection, row: sqlite3.Row) -> rules.Comparable:
    audit_id = row["id"]
    return rules.Comparable(
        audit_id=audit_id,
        created_at=_ts(row["created_at"]),
        ruleset_version=row["ruleset_version"],
        final_url=row["final_url"],
        summary=WebsiteAuditSummary.model_validate(orjson.loads(row["summary_json"])),
        results={
            found["rule_id"]: WebsiteFindingResult(found["result"])
            for found in conn.execute(
                "SELECT rule_id, result FROM website_findings WHERE audit_id = ?", (audit_id,)
            )
        },
        technologies=frozenset(
            found["technology_key"]
            for found in conn.execute(
                "SELECT technology_key FROM website_technologies WHERE audit_id = ?", (audit_id,)
            )
        ),
    )


def audit_site(
    conn: sqlite3.Connection,
    data_dir: Path,
    project_id: str,
    crawl: CrawledSite,
    job_id: str | None,
    now: datetime,
    ruleset: rules.Ruleset = rules.DEFAULT_RULESET,
) -> rules.AuditResult | None:
    """Audits one crawled site in one transaction. None when this crawl was already audited with
    this extractor and these rules."""
    same_crawl = conn.execute(
        "SELECT ruleset_version, snapshot_json FROM website_audits "
        "WHERE project_id = ? AND site = ? AND source_run_id = ? AND extractor_version = ? "
        "ORDER BY created_at DESC, id DESC",
        (project_id, crawl.site, crawl.source_run_id, extract.EXTRACTOR_VERSION),
    ).fetchall()
    if any(row["ruleset_version"] == ruleset.version for row in same_crawl):
        return None
    snapshot: rules.SiteInput | None = None
    if same_crawl:
        try:
            snapshot = rules.SNAPSHOT.validate_python(orjson.loads(same_crawl[0]["snapshot_json"]))
        except ValidationError:
            logger.warning("a stored snapshot no longer validates; the crawl is read again")
    if snapshot is None:
        snapshot = load_site(conn, data_dir, crawl)
    result = rules.audit(snapshot, ruleset)
    previous_row = conn.execute(
        "SELECT id, created_at, ruleset_version, final_url, summary_json FROM website_audits "
        "WHERE project_id = ? AND site = ? ORDER BY created_at DESC, id DESC LIMIT 1",
        (project_id, crawl.site),
    ).fetchone()
    audit_id = new_id()
    change: WebsiteDiff | None = None
    if previous_row is not None:
        current = rules.Comparable(
            audit_id=audit_id,
            created_at=now,
            ruleset_version=ruleset.version,
            final_url=result.final_url,
            summary=result.summary,
            results={finding.rule_id: finding.result for finding in result.findings},
            technologies=frozenset(technology.key for technology in result.technologies),
        )
        change = rules.diff(_comparable(conn, previous_row), current)
    records: list[Record] = [
        WebsiteAudit(
            id=audit_id,
            project_id=project_id,
            site=crawl.site,
            root_url=snapshot.root_url,
            final_url=result.final_url,
            source_run_id=crawl.source_run_id,
            job_id=job_id,
            previous_audit_id=previous_row["id"] if previous_row is not None else None,
            status=result.status,
            ruleset_version=ruleset.version,
            extractor_version=snapshot.extractor_version,
            pages_expected=result.pages_expected,
            pages_analyzed=result.pages_analyzed,
            coverage=result.coverage,
            summary_json=result.summary.model_dump(mode="json"),
            snapshot_json=rules.SNAPSHOT.dump_python(snapshot, mode="json"),
            diff_json=change.model_dump(mode="json") if change is not None else None,
            crawled_at=snapshot.crawled_at,
            created_at=now,
        ),
        *(
            WebsiteFinding(
                id=new_id(),
                audit_id=audit_id,
                rule_id=finding.rule_id,
                rule_version=finding.rule_version,
                title=finding.title,
                category=finding.category,
                result=finding.result,
                severity=finding.severity,
                observed_value=finding.observed_value,
                expected_condition=finding.expected_condition,
                remediation=finding.remediation,
                commercial_relevance=finding.commercial_relevance,
                confidence=finding.confidence,
                evidence_json=_refs(finding.evidence),
                created_at=now,
            )
            for finding in result.findings
        ),
        *(
            WebsiteTechnology(
                id=new_id(),
                audit_id=audit_id,
                technology_key=technology.key,
                name=technology.name,
                category=technology.category,
                version=technology.version,
                confidence=technology.confidence,
                evidence_json=_refs(technology.evidence),
            )
            for technology in result.technologies
        ),
        *(
            ConversionElement(
                id=new_id(),
                audit_id=audit_id,
                element_kind=element.kind,
                label=element.label,
                page_url=element.page_url,
                target=element.target,
                confidence=element.confidence,
                evidence_json=_refs(element.evidence),
            )
            for element in result.conversion
        ),
    ]
    with repository.atomic(conn):
        repository.insert(conn, *records)
        repository.audit(conn, AuditAction.CREATE, EntityType.WEBSITE_AUDIT, audit_id, actor=ACTOR)
    return result


# ---- reads -------------------------------------------------------------------------------------

_ITEM_SQL: Final = (
    "SELECT a.id, a.project_id, a.site, a.root_url, a.final_url, a.source_run_id, "
    "a.previous_audit_id, a.status, a.ruleset_version, a.extractor_version, a.pages_expected, "
    "a.pages_analyzed, a.coverage, json_extract(a.summary_json, '$.subscores') AS subscores, "
    "a.crawled_at, a.created_at, "
    "(SELECT count(*) FROM website_findings f WHERE f.audit_id = a.id AND f.result = 'fail') "
    "AS failed, "
    "(SELECT count(*) FROM website_findings f WHERE f.audit_id = a.id AND f.result = 'warning') "
    "AS warnings, "
    "(SELECT count(*) FROM website_findings f WHERE f.audit_id = a.id AND f.result = 'unknown') "
    "AS unknowns"
)
_LATEST_OF_SITE: Final = (
    "NOT EXISTS (SELECT 1 FROM website_audits n WHERE n.project_id = a.project_id "
    "AND n.site = a.site AND (n.created_at, n.id) > (a.created_at, a.id))"
)
#: Actionable findings first: failures, then warnings, then what could not be evaluated.
_FINDING_ORDER: Final = (
    "CASE result WHEN 'fail' THEN 0 WHEN 'warning' THEN 1 WHEN 'unknown' THEN 2 WHEN 'pass' THEN 3 "
    "ELSE 4 END, CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 "
    "WHEN 'low' THEN 3 ELSE 4 END, rule_id"
)


def _item(row: sqlite3.Row) -> WebsiteAuditItem:
    return WebsiteAuditItem(
        id=row["id"],
        project_id=row["project_id"],
        site=row["site"],
        root_url=row["root_url"],
        final_url=row["final_url"],
        source_run_id=row["source_run_id"],
        previous_audit_id=row["previous_audit_id"],
        status=WebsiteAuditStatus(row["status"]),
        ruleset_version=row["ruleset_version"],
        extractor_version=row["extractor_version"],
        pages_expected=row["pages_expected"],
        pages_analyzed=row["pages_analyzed"],
        coverage=row["coverage"],
        subscores=[
            WebsiteSubscore.model_validate(score)
            for score in orjson.loads(row["subscores"] or "[]")
        ],
        failed=row["failed"],
        warnings=row["warnings"],
        unknown=row["unknowns"],
        crawled_at=_ts(row["crawled_at"]),
        created_at=_ts(row["created_at"]),
    )


def list_website_audits(conn: sqlite3.Connection, query: WebsiteAuditListQuery) -> WebsiteAuditPage:
    where: list[str] = []
    params: list[object] = []
    if query.project_id is not None:
        where.append("a.project_id = ?")
        params.append(query.project_id)
    if query.site is not None:
        where.append("a.site = ?")
        params.append(query.site)
        tag, key, order, after = (
            "website_history",
            "created_at",
            "a.created_at DESC, a.id DESC",
            "<",
        )
        position = "(a.created_at, a.id)"
    else:
        where.append(_LATEST_OF_SITE)
        tag, key, order, after = "website_sites", "site", "a.site, a.id", ">"
        position = "(a.site, a.id)"
    (total,) = conn.execute(
        f"SELECT count(*) FROM website_audits a WHERE {' AND '.join(where)}",  # noqa: S608
        params,
    ).fetchone()
    if query.cursor is not None:
        last_key, last_id = repository.decode_cursor(query.cursor, tag)
        where.append(f"{position} {after} (?, ?)")
        params += [last_key, last_id]
    rows = conn.execute(
        f"{_ITEM_SQL} FROM website_audits a WHERE {' AND '.join(where)} ORDER BY {order} LIMIT ?",
        [*params, query.limit + 1],
    ).fetchall()
    page = rows[: query.limit]
    next_cursor = (
        repository.encode_cursor(tag, page[-1][key], page[-1]["id"])
        if len(rows) > query.limit
        else None
    )
    return WebsiteAuditPage(
        items=[_item(row) for row in page], next_cursor=next_cursor, total=total
    )


def get_website_audit(conn: sqlite3.Connection, audit_id: str) -> WebsiteAuditDetail:
    row = conn.execute(
        f"{_ITEM_SQL}, a.summary_json, a.diff_json FROM website_audits a WHERE a.id = ?",
        (audit_id,),
    ).fetchone()
    if row is None:
        raise repository.NotFoundError(f"website audit {audit_id} not found")
    findings = [
        WebsiteFindingView(
            rule_id=found["rule_id"],
            rule_version=found["rule_version"],
            title=found["title"],
            category=found["category"],
            result=found["result"],
            severity=found["severity"],
            observed_value=found["observed_value"],
            expected_condition=found["expected_condition"],
            remediation=found["remediation"],
            commercial_relevance=found["commercial_relevance"],
            confidence=found["confidence"],
            evidence=_evidence(found["evidence_json"]),
        )
        for found in conn.execute(
            "SELECT rule_id, rule_version, title, category, result, severity, observed_value, "  # noqa: S608
            "expected_condition, remediation, commercial_relevance, confidence, evidence_json "
            f"FROM website_findings WHERE audit_id = ? ORDER BY {_FINDING_ORDER}",
            (audit_id,),
        )
    ]
    technologies = [
        WebsiteTechnologyView(
            key=found["technology_key"],
            name=found["name"],
            category=found["category"],
            version=found["version"],
            confidence=found["confidence"],
            evidence=_evidence(found["evidence_json"]),
        )
        for found in conn.execute(
            "SELECT technology_key, name, category, version, confidence, evidence_json "
            "FROM website_technologies WHERE audit_id = ? ORDER BY category, technology_key",
            (audit_id,),
        )
    ]
    conversion = [
        WebsiteConversionView(
            kind=found["element_kind"],
            label=found["label"],
            page_url=found["page_url"],
            target=found["target"],
            confidence=found["confidence"],
            evidence=_evidence(found["evidence_json"]),
        )
        for found in conn.execute(
            "SELECT element_kind, label, page_url, target, confidence, evidence_json "
            "FROM conversion_elements WHERE audit_id = ? ORDER BY rowid",
            (audit_id,),
        )
    ]
    history = conn.execute(
        f"{_ITEM_SQL} FROM website_audits a WHERE a.project_id = ? AND a.site = ? "
        "ORDER BY a.created_at DESC, a.id DESC LIMIT ?",
        (row["project_id"], row["site"], MAX_AUDIT_HISTORY),
    ).fetchall()
    diff_json = row["diff_json"]
    return WebsiteAuditDetail(
        audit=_item(row),
        summary=WebsiteAuditSummary.model_validate(orjson.loads(row["summary_json"])),
        findings=findings,
        technologies=technologies,
        conversion=conversion,
        diff=WebsiteDiff.model_validate(orjson.loads(diff_json)) if diff_json else None,
        history=[_item(item) for item in history],
    )


def result_counts(result: rules.AuditResult) -> Counter[WebsiteFindingResult]:
    return Counter(finding.result for finding in result.findings)


# ---- runs --------------------------------------------------------------------------------------


def plan_runs(
    conn: sqlite3.Connection, project_id: str | None, sites: Sequence[str], *, actor: str
) -> tuple[list[str], list[str]]:
    """Creates one queued job per project to audit. Returns (created, already open) job ids."""
    if project_id is not None:
        project = repository.get(conn, Project, project_id)
        if project.deleted_at is not None:
            raise repository.NotFoundError(f"project {project_id} has been deleted")
        if not crawled_sites(conn, project_id, sites):
            raise WebsiteError(
                "none of these sites has a finished crawl in this project: crawl them first"
                if sites
                else "this project has no finished crawl yet: crawl some websites first"
            )
        projects = [project_id]
    else:
        if sites:
            raise WebsiteError("sites can only be chosen within one project")
        projects = [
            row["id"]
            for row in conn.execute(
                "SELECT p.id FROM projects p WHERE p.deleted_at IS NULL AND EXISTS "
                "(SELECT 1 FROM source_runs r JOIN source_connectors sc ON sc.id = r.connector_id "
                "WHERE r.project_id = p.id AND sc.key = ? "
                "AND r.status IN ('succeeded', 'failed', 'cancelled')) ORDER BY p.created_at, p.id",
                (CRAWL_CONNECTOR_KEY,),
            )
        ]
        if not projects:
            raise WebsiteError("there is no finished crawl to audit yet: crawl some websites first")
    created: list[str] = []
    listed: list[str] = []
    now = utc_now()
    with repository.atomic(conn):
        for pid in projects:
            open_job = conn.execute(
                "SELECT id FROM jobs WHERE kind = ? AND status IN ('queued', 'running', 'paused') "
                "AND json_extract(params_json, '$.project_id') = ? "
                "ORDER BY requested_at DESC LIMIT 1",
                (JOB_KIND, pid),
            ).fetchone()
            if open_job is not None:
                if project_id is not None:
                    raise WebsiteError(
                        "an audit is already open for this project: resume or cancel it"
                    )
                listed.append(open_job["id"])
                continue
            job = Job(
                id=new_id(), project_id=pid, kind=JOB_KIND, status=JobStatus.QUEUED, priority=0,
                params_json={"project_id": pid, "sites": list[JsonValue](sites),
                             "extractor_version": extract.EXTRACTOR_VERSION,
                             "ruleset_version": rules.RULESET_VERSION},
                idempotency_key=None, progress_done=0, progress_total=None, requested_at=now,
                started_at=None, finished_at=None, created_at=now, updated_at=now,
            )  # fmt: skip
            repository.insert(conn, job)
            repository.audit(conn, AuditAction.CREATE, EntityType.JOB, job.id, actor=actor)
            jobs.write_event(
                conn, job.id, None, "run_queued", EventLevel.INFO,
                {"project_id": pid, "sites": len(sites) or None},
            )  # fmt: skip
            created.append(job.id)
    return created, listed


def _run_view(conn: sqlite3.Connection, job: Job) -> WebsiteRun:
    state = jobs.latest_state(conn, job.id)
    stats = state.get("stats")
    counters = stats if isinstance(stats, dict) else {}
    project = repository.get(conn, Project, job.project_id)
    stage = state.get("stage")
    return WebsiteRun(
        job_id=job.id,
        project_id=job.project_id,
        project_name=project.name,
        status=job.status,
        stage=stage if isinstance(stage, str) and stage else "queued",
        progress_done=job.progress_done,
        progress_total=job.progress_total,
        extractor_version=str(
            job.params_json.get("extractor_version") or extract.EXTRACTOR_VERSION
        ),
        ruleset_version=str(job.params_json.get("ruleset_version") or rules.RULESET_VERSION),
        stats=WebsiteStats.model_validate(
            {name: repository._counter(counters.get(name)) for name in WebsiteStats.model_fields}
        ),
        requested_at=job.requested_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        events=repository._run_events(conn, job.id),
        is_synthetic=project.is_synthetic,
    )


def get_website_run(conn: sqlite3.Connection, job_id: str) -> WebsiteRun:
    return _run_view(conn, jobs.live_job(conn, job_id, JOB_KIND))


def runs_by_id(conn: sqlite3.Connection, job_ids: Sequence[str]) -> WebsiteRunPage:
    items = [get_website_run(conn, job_id) for job_id in job_ids]
    return WebsiteRunPage(items=items, next_cursor=None, total=len(items))


def list_website_runs(conn: sqlite3.Connection, query: WebsiteRunListQuery) -> WebsiteRunPage:
    where, params = ["j.kind = ?"], list[object]([JOB_KIND])
    if query.project_id is not None:
        where.append("j.project_id = ?")
        params.append(query.project_id)
    (total,) = conn.execute(
        f"SELECT count(*) FROM jobs j WHERE {' AND '.join(where)}",  # noqa: S608
        params,
    ).fetchone()
    if query.cursor is not None:
        last_key, last_id = repository.decode_cursor(query.cursor, "website")
        where.append("(j.requested_at, j.id) < (?, ?)")
        params += [last_key, last_id]
    rows = conn.execute(
        f"SELECT {repository._columns(Job, 'j')} FROM jobs j "  # noqa: S608
        f"WHERE {' AND '.join(where)} ORDER BY j.requested_at DESC, j.id DESC LIMIT ?",
        [*params, query.limit + 1],
    ).fetchall()
    page = rows[: query.limit]
    next_cursor = (
        repository.encode_cursor("website", page[-1]["requested_at"], page[-1]["id"])
        if len(rows) > query.limit
        else None
    )
    return WebsiteRunPage(
        items=[_run_view(conn, repository.from_row(Job, row)) for row in page],
        next_cursor=next_cursor,
        total=total,
    )
