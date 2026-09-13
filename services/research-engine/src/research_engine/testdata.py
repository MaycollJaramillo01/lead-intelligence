"""SYNTHETIC TEST DATA: factories for tests, contract fixtures and the development-only seed.

Nothing here is product output. Every value is fictitious on purpose: business names start with
"Synthetic", domains use the reserved `.example` top-level domain, phone numbers use the 555-01xx
range reserved for fiction, addresses are on "Example Ave", payloads carry `"test_data": true`,
every project created here is flagged `is_synthetic`, and the only people are obviously fictional
names on synthetic sites ("Alex Example", "Jordan Sample").
"""

import csv
import hashlib
import io
import re
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path

import orjson

from research_engine.contracts import ComponentState, OllamaModel, OllamaStatus
from research_engine.datasets import snapshot_businesses
from research_engine.discovery.normalize import dedup_key, domain_of
from research_engine.domain import (
    AcquiredPage,
    AcquisitionMethod,
    AuditAction,
    Business,
    BusinessClassification,
    BusinessLocation,
    BusinessName,
    BusinessStatus,
    Campaign,
    ClassificationKind,
    ContactOrigin,
    ContactPoint,
    ContactType,
    CrawlTarget,
    CrawlUrl,
    CrawlUrlStatus,
    DiscoveryCandidate,
    DiscoveryCriteria,
    DiscoveryLimits,
    EntityType,
    EventLevel,
    EvidenceItem,
    EvidenceStatus,
    EvidenceStrength,
    EvidenceType,
    Extraction,
    GeoScope,
    Job,
    JobCheckpoint,
    JobEvent,
    JobStatus,
    JobTask,
    JsonObject,
    LeadStage,
    MailDomainResult,
    NameKind,
    PageKind,
    Project,
    RawRecord,
    Record,
    SourceConnector,
    SourceKind,
    SourceQuery,
    SourceRun,
    SourceRunStatus,
    SourceSelection,
    SuppressionKind,
    SyntheticSeedResult,
    ValidationStatus,
    Website,
    default_crawl_settings,
    empty_criteria,
    new_id,
    sort_key,
    utc_now,
)
from research_engine.ollama import (
    ChatAnswer,
    ChatRequest,
    LoadedModel,
    ModelDetails,
    ProviderError,
)
from research_engine.repository import DISCOVERY_JOB_KIND, atomic, audit, insert

SYNTHETIC_CONNECTOR_KEY = "synthetic.fixture"
SYNTHETIC_EXTRACTOR = "synthetic.extractor"
SYNTHETIC_PROJECT_NAME = "Synthetic test workspace"
SYNTHETIC_DATASET_NAME = "Synthetic businesses"
SEED_BUSINESS_COUNT = 24
ACTOR_DEV_SEED = "dev-seed"

_PLACES = (
    ("Austin", "TX", "78701", "512"),
    ("Miami", "FL", "33130", "305"),
    ("Phoenix", "AZ", "85004", "602"),
    ("Chicago", "IL", "60601", "312"),
    ("San Diego", "CA", "92101", "619"),
    ("Newark", "NJ", "07102", "973"),
)
_TRADES = (
    "Bakery",
    "Auto Repair",
    "Dental Clinic",
    "Landscaping",
    "Cleaning Services",
    "Roofing",
    "Grocery",
    "Tax Preparation",
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True, slots=True)
class SyntheticContext:
    project_id: str
    connector_id: str
    source_run_id: str
    now: datetime
    ids: Callable[[], str] = new_id


def synthetic_project(now: datetime, ids: Callable[[], str] = new_id) -> Project:
    return Project(
        id=ids(),
        name=SYNTHETIC_PROJECT_NAME,
        description="Synthetic test data for development and tests. Not real businesses.",
        is_synthetic=True,
        created_at=now,
        updated_at=now,
        deleted_at=None,
        deleted_reason=None,
    )


def synthetic_connector(now: datetime, ids: Callable[[], str] = new_id) -> SourceConnector:
    return SourceConnector(
        id=ids(),
        key=SYNTHETIC_CONNECTOR_KEY,
        name="Synthetic fixture source (test data)",
        kind=SourceKind.SYNTHETIC,
        connector_version="1.0.0",
        terms_url=None,
        requires_credentials=False,
        enabled=True,
        created_at=now,
        updated_at=now,
    )


def synthetic_run(
    project_id: str, connector_id: str, now: datetime, ids: Callable[[], str] = new_id
) -> SourceRun:
    return SourceRun(
        id=ids(),
        project_id=project_id,
        campaign_id=None,
        connector_id=connector_id,
        connector_version="1.0.0",
        config_id=None,
        job_id=None,
        status=SourceRunStatus.SUCCEEDED,
        effective_config_json={"test_data": True},
        started_at=now,
        finished_at=now,
        error_code=None,
        error_message=None,
        created_at=now,
        updated_at=now,
    )


def synthetic_business(ctx: SyntheticContext, index: int) -> list[Record]:
    """One business with its raw record, extraction and observations, in insertion order."""
    trade = _TRADES[index % len(_TRADES)]
    city, region, postal_code, area_code = _PLACES[index % len(_PLACES)]
    name = f"Synthetic {trade} {index:04d}"
    slug = f"synthetic-{trade.lower().replace(' ', '-')}-{index:04d}"
    line = f"555-01{index % 100:02d}"
    payload: JsonObject = {
        "test_data": True,
        "name": f"{name} LLC",
        "address": f"{100 + index} Example Ave, {city}, {region} {postal_code}",
        "phone": f"({area_code}) {line}",
        "website": f"https://www.{slug}.example/",
        "category": trade,
    }
    now, ids = ctx.now, ctx.ids
    raw = RawRecord(
        id=ids(),
        source_run_id=ctx.source_run_id,
        acquired_page_id=None,
        connector_id=ctx.connector_id,
        method=AcquisitionMethod.SYNTHETIC,
        external_id=f"synthetic-{index:06d}",
        source_url=None,
        payload_json=payload,
        payload_sha256=_sha256(orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)),
        acquired_at=now,
        created_at=now,
    )
    extraction = Extraction(
        id=ids(),
        raw_record_id=raw.id,
        extractor=SYNTHETIC_EXTRACTOR,
        extractor_version="1.0.0",
        extracted_at=now,
    )
    business = Business(
        id=ids(),
        project_id=ctx.project_id,
        display_name=name,
        name_sort_key=sort_key(name),
        status=BusinessStatus.CANDIDATE,
        lead_stage=LeadStage.NOT_CONTACTED,
        merged_into_id=None,
        first_seen_at=now,
        last_seen_at=now,
        created_at=now,
        updated_at=now,
        deleted_at=None,
        deleted_reason=None,
    )
    observed = {
        "business_id": business.id,
        "extraction_id": extraction.id,
        "observed_at": now,
        "created_at": now,
    }
    snippet = str(payload["name"])
    return [
        raw,
        extraction,
        business,
        BusinessName(
            id=ids(),
            name_raw=snippet,
            name_normalized=sort_key(name),
            name_kind=NameKind.LEGAL,
            is_primary=True,
            confidence=0.9,
            **observed,
        ),
        BusinessLocation(
            id=ids(),
            address_raw=str(payload["address"]),
            street=f"{100 + index} Example Ave",
            unit=None,
            locality=city,
            region=region,
            postal_code=postal_code,
            country_code="US",
            latitude=None,
            longitude=None,
            is_primary=True,
            confidence=0.85,
            **observed,
        ),
        Website(
            id=ids(),
            url_raw=str(payload["website"]),
            url_normalized=f"https://{slug}.example/",
            domain=f"{slug}.example",
            is_primary=True,
            confidence=0.95,
            **observed,
        ),
        BusinessClassification(
            id=ids(),
            kind=ClassificationKind.CATEGORY,
            label_raw=trade,
            taxonomy_key=None,
            taxonomy_version=None,
            is_primary=True,
            confidence=0.8,
            **observed,
        ),
        ContactPoint(
            id=ids(),
            business_id=business.id,
            location_id=None,
            decision_maker_id=None,
            contact_type=ContactType.PHONE,
            value_raw=str(payload["phone"]),
            value_normalized=f"+1{area_code}{line.replace('-', '')}",
            origin=ContactOrigin.PUBLISHED,
            platform=None,
            web_page_id=None,
            role_or_department=None,
            published_context="Synthetic listing (test data)",
            is_primary=True,
            validation_status=ValidationStatus.OBSERVED,
            validated_at=None,
            validation_method=None,
            do_not_contact=False,
            suppression_reason=None,
            confidence=0.9,
            extraction_id=extraction.id,
            first_seen_at=now,
            last_seen_at=now,
            created_at=now,
            updated_at=now,
            deleted_at=None,
            deleted_reason=None,
        ),
        EvidenceItem(
            id=ids(),
            business_id=business.id,
            evidence_type=EvidenceType.IDENTITY,
            claim="Business name as listed by the synthetic source",
            value_json=snippet,
            strength=EvidenceStrength.MODERATE,
            status=EvidenceStatus.ACTIVE,
            confidence=0.9,
            extraction_id=extraction.id,
            web_page_id=None,
            field_path="name",
            snippet=snippet,
            snippet_sha256=_sha256(snippet.encode()),
            observed_at=now,
            created_at=now,
            updated_at=now,
        ),
    ]


def seed_businesses(
    conn: sqlite3.Connection, ctx: SyntheticContext, count: int, start: int = 0
) -> list[str]:
    """Inserts `count` synthetic businesses in one transaction; returns their ids."""
    ids: list[str] = []
    with atomic(conn):
        for index in range(start, start + count):
            records = synthetic_business(ctx, index)
            insert(conn, *records)
            ids.extend(record.id for record in records if isinstance(record, Business))
    return ids


def _scalar(conn: sqlite3.Connection, sql: str, *params: object) -> str | None:
    row = conn.execute(sql, params).fetchone()
    return None if row is None else str(row[0])


def seed_synthetic_workspace(
    conn: sqlite3.Connection, data_dir: Path, count: int = SEED_BUSINESS_COUNT
) -> SyntheticSeedResult:
    """Creates the synthetic workspace once (businesses + a dataset snapshot); then reuses it."""
    live_businesses = (
        "SELECT id FROM businesses WHERE project_id = ? AND deleted_at IS NULL "
        "AND status <> 'merged' ORDER BY name_sort_key, id LIMIT 1"
    )
    find_dataset = (
        "SELECT id FROM datasets WHERE project_id = ? AND name = ? AND deleted_at IS NULL"
    )
    count_live = (
        "SELECT count(*) FROM businesses WHERE project_id = ? AND deleted_at IS NULL "
        "AND status <> 'merged'"
    )
    now = utc_now()
    with atomic(conn):
        project_id = _scalar(
            conn,
            "SELECT id FROM projects WHERE name = ? AND is_synthetic = 1 AND deleted_at IS NULL",
            SYNTHETIC_PROJECT_NAME,
        )
        if project_id is not None:
            business_id = _scalar(conn, live_businesses, project_id)
            dataset_id = _scalar(conn, find_dataset, project_id, SYNTHETIC_DATASET_NAME)
            if business_id is not None and dataset_id is not None:
                return SyntheticSeedResult(
                    project_id=project_id,
                    business_id=business_id,
                    business_count=int(_scalar(conn, count_live, project_id) or 0),
                    dataset_id=dataset_id,
                    created=False,
                )
        else:
            project = synthetic_project(now)
            insert(conn, project)
            audit(conn, AuditAction.CREATE, EntityType.PROJECT, project.id, actor=ACTOR_DEV_SEED)
            project_id = project.id
        connector_id = _scalar(
            conn, "SELECT id FROM source_connectors WHERE key = ?", SYNTHETIC_CONNECTOR_KEY
        )
        if connector_id is None:
            connector = synthetic_connector(now)
            insert(conn, connector)
            connector_id = connector.id
        run = synthetic_run(project_id, connector_id, now)
        insert(conn, run)
        seed_businesses(conn, SyntheticContext(project_id, connector_id, run.id, now), count)
    # duplicates to review, so the review queue can be exercised end to end in development, and
    # businesses whose evidence reaches every Latino evidence status
    seed_resolution(conn, SyntheticContext(project_id, connector_id, run.id, now))
    seed_latino(conn, SyntheticContext(project_id, connector_id, run.id, now), data_dir)
    seed_website(conn, SyntheticContext(project_id, connector_id, run.id, now), data_dir)
    seed_contacts(conn, SyntheticContext(project_id, connector_id, run.id, now), data_dir)
    snapshot = snapshot_businesses(
        conn,
        data_dir,
        project_id=project_id,
        dataset_name=SYNTHETIC_DATASET_NAME,
        actor=ACTOR_DEV_SEED,
        description="Synthetic test data",
    )
    return SyntheticSeedResult(
        project_id=project_id,
        business_id=str(_scalar(conn, live_businesses, project_id)),
        business_count=int(_scalar(conn, count_live, project_id) or 0),
        dataset_id=snapshot.dataset_id,
        created=True,
    )


# ---- discovery (phase 04) -----------------------------------------------------------------------

SYNTHETIC_CAMPAIGN_NAME = "Synthetic discovery campaign"
SYNTHETIC_IMPORT_FILE = "synthetic-import.csv"
#: The third row repeats the first business on purpose: the duplicate check needs something to do.
_IMPORT_ROWS = (
    ("Synthetic Bakery 0001", "Austin", "TX", "78701", "512", "Bakery"),
    ("Synthetic Roofing 0002", "Miami", "FL", "33130", "305", "Roofing"),
    ("Synthetic Bakery 0001", "Austin", "TX", "78701", "512", "Bakery"),
)


def _import_csv() -> str:
    lines = ["name,city,state,zip,website,phone,category"]
    for index, (name, city, state, zip_code, area, category) in enumerate(_IMPORT_ROWS, start=1):
        site = f"https://{name.lower().replace(' ', '-')}.example"
        lines.append(f"{name},{city},{state},{zip_code},{site},({area}) 555-010{index},{category}")
    return "\n".join(lines) + "\n"


SYNTHETIC_IMPORT_CSV = _import_csv()


@dataclass(frozen=True, slots=True)
class DiscoveryFixture:
    """What `seed_discovery` created, so a caller can query it back."""

    campaign_id: str
    job_id: str
    task_id: str
    source_run_id: str
    candidate_ids: list[str]


def synthetic_criteria() -> DiscoveryCriteria:
    """A small, valid campaign: one place, one vertical, one source, low limits."""
    return empty_criteria().model_copy(
        update={
            "geography": [
                GeoScope(
                    country_code="US",
                    region="TX",
                    metro=None,
                    locality="Austin",
                    postal_code=None,
                    radius_km=None,
                )
            ],
            "verticals": ["722"],
            "categories": ["Bakery"],
            "market_languages": ["es"],
            "sources": [
                SourceSelection(
                    connector_key="file.import",
                    priority=50,
                    max_records=100,
                    config_json={},
                )
            ],
            "limits": DiscoveryLimits(max_candidates=100, max_requests=10, max_pages_per_source=5),
        }
    )


def seed_discovery(conn: sqlite3.Connection, ctx: SyntheticContext) -> DiscoveryFixture:
    """A finished discovery run over the synthetic import file, with its whole provenance chain."""
    now, ids = ctx.now, ctx.ids
    criteria = synthetic_criteria()
    campaign = Campaign(
        id=ids(),
        project_id=ctx.project_id,
        icp_profile_version_id=None,
        name=SYNTHETIC_CAMPAIGN_NAME,
        objective="Exercise the discovery flow with test data. Not a real campaign.",
        criteria_json=criteria.model_dump(mode="json"),
        criteria_schema_version=criteria.schema_version,
        created_at=now,
        updated_at=now,
        deleted_at=None,
        deleted_reason=None,
    )
    job = Job(
        id=ids(),
        project_id=ctx.project_id,
        kind=DISCOVERY_JOB_KIND,
        status=JobStatus.SUCCEEDED,
        priority=0,
        params_json={"campaign_id": campaign.id, "connector_keys": ["file.import"]},
        idempotency_key=None,
        progress_done=2,
        progress_total=2,
        requested_at=now,
        started_at=now,
        finished_at=now,
        created_at=now,
        updated_at=now,
    )
    task = JobTask(
        id=ids(),
        job_id=job.id,
        stage="discover",
        task_key=SYNTHETIC_CONNECTOR_KEY,
        status=JobStatus.SUCCEEDED,
        attempt_count=1,
        max_attempts=4,
        created_at=now,
        updated_at=now,
    )
    run = SourceRun(
        id=ids(),
        project_id=ctx.project_id,
        campaign_id=campaign.id,
        connector_id=ctx.connector_id,
        connector_version="1.0.0",
        config_id=None,
        job_id=job.id,
        status=SourceRunStatus.SUCCEEDED,
        effective_config_json={
            "connector_key": SYNTHETIC_CONNECTOR_KEY,
            "connector_version": "1.0.0",
            "criteria": criteria.model_dump(mode="json"),
            "selection": criteria.sources[0].model_dump(mode="json"),
            "page_size": 200,
            "run_input": {"artifact_path": f"imports/{SYNTHETIC_IMPORT_FILE}"},
            "requests_per_minute": 600,
            "daily_request_budget": None,
        },
        started_at=now,
        finished_at=now,
        error_code=None,
        error_message=None,
        created_at=now,
        updated_at=now,
    )
    query = SourceQuery(
        id=ids(),
        source_run_id=run.id,
        query_json={
            "connector_key": SYNTHETIC_CONNECTOR_KEY,
            "cursor": None,
            "page_size": 200,
            "request_url": f"file:imports/{SYNTHETIC_IMPORT_FILE}#rows=0",
        },
        cursor=None,
        issued_at=now,
        result_count=3,
    )
    body = SYNTHETIC_IMPORT_CSV.encode()
    page = AcquiredPage(
        id=ids(),
        source_run_id=run.id,
        source_query_id=query.id,
        url=f"file:imports/{SYNTHETIC_IMPORT_FILE}#rows=0",
        final_url=None,
        http_status=None,
        content_type=None,
        content_sha256=_sha256(body),
        byte_size=len(body),
        artifact_path=f"imports/{SYNTHETIC_IMPORT_FILE}",
        fetched_at=now,
    )
    records: list[Record] = [campaign, job, task, run, query, page]
    candidate_ids: list[str] = []
    first_of_key: dict[str, str] = {}
    rows = list(csv.DictReader(io.StringIO(SYNTHETIC_IMPORT_CSV)))
    for number, row in enumerate(rows, start=1):
        payload: JsonObject = {**row, "_row": number, "_source_file": SYNTHETIC_IMPORT_FILE}
        raw = RawRecord(
            id=ids(),
            source_run_id=run.id,
            acquired_page_id=page.id,
            connector_id=ctx.connector_id,
            method=AcquisitionMethod.FILE_IMPORT,
            external_id=f"{SYNTHETIC_IMPORT_FILE}#row={number}",
            source_url=None,
            payload_json=payload,
            payload_sha256=_sha256(orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)),
            acquired_at=now,
            created_at=now,
        )
        name = row["name"]
        key = dedup_key(name=name, locality=row["city"], region=row["state"], domain=None)
        candidate = DiscoveryCandidate(
            id=ids(),
            project_id=ctx.project_id,
            campaign_id=campaign.id,
            source_run_id=run.id,
            raw_record_id=raw.id,
            connector_id=ctx.connector_id,
            dedup_key=key,
            duplicate_of_id=first_of_key.get(key),
            name_raw=name,
            name_normalized=sort_key(name),
            locality=row["city"],
            region=row["state"],
            postal_code=row["zip"],
            country_code="US",
            domain=domain_of(row["website"]),
            website_url=row["website"],
            phone_raw=row["phone"],
            category_raw=row["category"],
            external_id=f"{SYNTHETIC_IMPORT_FILE}#row={number}",
            source_url=None,
            discovered_at=now,
            created_at=now,
        )
        first_of_key.setdefault(key, candidate.id)
        records += [raw, candidate]
        candidate_ids.append(candidate.id)
    records.append(
        JobCheckpoint(
            id=ids(),
            job_id=job.id,
            task_id=task.id,
            state_json={
                "connector_key": SYNTHETIC_CONNECTOR_KEY,
                "cursor": None,
                "pages_done": 1,
                "records_seen": 3,
                "candidates": 2,
                "duplicates": 1,
                "requests": 1,
                "adapter": {"row_offset": 3},
            },
            created_at=now,
        )
    )
    records.append(
        JobEvent(
            id=ids(),
            job_id=job.id,
            task_id=task.id,
            level=EventLevel.INFO,
            event="source_exhausted",
            error_code=None,
            retryable=None,
            detail_json={"connector_key": SYNTHETIC_CONNECTOR_KEY},
            created_at=now,
        )
    )
    with atomic(conn):
        insert(conn, *records)
        audit(conn, AuditAction.CREATE, EntityType.CAMPAIGN, campaign.id, actor=ACTOR_DEV_SEED)
        audit(conn, AuditAction.CREATE, EntityType.JOB, job.id, actor=ACTOR_DEV_SEED)
    return DiscoveryFixture(
        campaign_id=campaign.id,
        job_id=job.id,
        task_id=task.id,
        source_run_id=run.id,
        candidate_ids=candidate_ids,
    )


# ---- web acquisition (phase 05) -----------------------------------------------------------------

SYNTHETIC_SITE = "synthetic-bakery-0001.example"
_SYNTHETIC_ROBOTS = "User-agent: *\nDisallow: /about-us\n"
_SYNTHETIC_HOME = (
    "<!doctype html><html lang='en'><head><title>Synthetic Bakery 0001</title></head><body><main>"
    "<h1>Synthetic Bakery 0001</h1><p>Test data: a fictitious bakery on Example Ave. "
    "Call (512) 555-0101.</p><a href='/contact'>Contact</a> <a href='/about-us'>About</a> "
    "<a href='/gallery'>Gallery</a> <a href='/cart'>Cart</a></main></body></html>"
)
_SYNTHETIC_SHELL = "<!doctype html><html><body><div id='root'></div><script src='/app.js'></script>"
_SYNTHETIC_RENDERED = (
    "<html><head><title>Contact</title></head><body><div id='root'><h1>Contact</h1>"
    f"<p>Test data only. Write to hello@{SYNTHETIC_SITE}.</p></div></body></html>"
)


@dataclass(frozen=True, slots=True)
class CrawlFixture:
    job_id: str
    source_run_id: str
    #: The contact page: fetched over HTTP, then rendered because it was an empty shell.
    rendered_url_id: str


def seed_crawl(
    conn: sqlite3.Connection, ctx: SyntheticContext, campaign_id: str, candidate_id: str
) -> CrawlFixture:
    """A finished crawl of one synthetic site: fetched, rendered, skipped and failed URLs."""
    from research_engine.crawl.engine import (
        CONNECTOR_KEY,
        CONNECTOR_NAME,
        CONNECTOR_VERSION,
        JOB_KIND,
        PRODUCT_TOKEN,
    )
    from research_engine.crawl.extract import snapshot

    now, ids = ctx.now, ctx.ids
    base = f"https://{SYNTHETIC_SITE}"
    settings = default_crawl_settings().model_dump(mode="json")
    connector = SourceConnector(
        id=ids(),
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
    job = Job(
        id=ids(),
        project_id=ctx.project_id,
        kind=JOB_KIND,
        status=JobStatus.SUCCEEDED,
        priority=0,
        params_json={
            "campaign_id": campaign_id,
            "settings": settings,
            "sites": 1,
            "sites_available": 1,
        },
        idempotency_key=None,
        progress_done=1,
        progress_total=1,
        requested_at=now,
        started_at=now,
        finished_at=now,
        created_at=now,
        updated_at=now,
    )
    run = SourceRun(
        id=ids(),
        project_id=ctx.project_id,
        campaign_id=campaign_id,
        connector_id=connector.id,
        connector_version=CONNECTOR_VERSION,
        config_id=None,
        job_id=job.id,
        status=SourceRunStatus.SUCCEEDED,
        effective_config_json={
            "connector_key": CONNECTOR_KEY,
            "connector_version": CONNECTOR_VERSION,
            "product_token": PRODUCT_TOKEN,
            "site": SYNTHETIC_SITE,
            "root_url": f"{base}/",
            "candidate_id": candidate_id,
            "settings": settings,
            "test_data": True,
        },
        started_at=now,
        finished_at=now,
        error_code=None,
        error_message=None,
        created_at=now,
        updated_at=now,
    )
    task = JobTask(
        id=ids(),
        job_id=job.id,
        stage="crawl",
        task_key=SYNTHETIC_SITE,
        status=JobStatus.SUCCEEDED,
        attempt_count=1,
        max_attempts=3,
        created_at=now,
        updated_at=now,
    )
    records: list[Record] = [connector, job, run, task]

    def page(url: str, status: int, content_type: str, body: bytes | None) -> AcquiredPage:
        digest = _sha256(body) if body is not None else None
        acquired = AcquiredPage(
            id=ids(),
            source_run_id=run.id,
            source_query_id=None,
            url=url,
            final_url=url,
            http_status=status,
            content_type=content_type,
            content_sha256=digest,
            byte_size=len(body) if body is not None else None,
            artifact_path=f"artifacts/web/{digest[:2]}/{digest}.html" if digest else None,
            fetched_at=now,
        )
        records.append(acquired)
        return acquired

    def raw(acquired: AcquiredPage, method: AcquisitionMethod, payload: JsonObject) -> RawRecord:
        record = RawRecord(
            id=ids(),
            source_run_id=run.id,
            acquired_page_id=acquired.id,
            connector_id=connector.id,
            method=method,
            external_id=None,
            source_url=acquired.url,
            payload_json=payload,
            payload_sha256=_sha256(orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)),
            acquired_at=now,
            created_at=now,
        )
        records.append(record)
        return record

    def url_row(
        path: str, target: CrawlTarget, kind: PageKind, depth: int, **fields: object
    ) -> CrawlUrl:
        values: dict[str, object] = dict.fromkeys(CrawlUrl.model_fields)
        values |= {
            "id": ids(),
            "source_run_id": run.id,
            "url": f"{base}{path}",
            "target": target,
            "page_kind": kind,
            "depth": depth,
            "priority": 100 - 10 * depth,
            "request_count": 0,
            "retry_count": 0,
            "created_at": now,
            "updated_at": now,
        }
        row = CrawlUrl.model_validate(values | fields)
        records.append(row)
        return row

    robots_page = page(f"{base}/robots.txt", 200, "text/plain", _SYNTHETIC_ROBOTS.encode())
    robots_raw = raw(
        robots_page,
        AcquisitionMethod.HTTP_FETCH,
        {"text": _SYNTHETIC_ROBOTS, "product_token": PRODUCT_TOKEN, "rules": 1, "sitemaps": [],
         "crawl_delay_s": None, "test_data": True},
    )  # fmt: skip
    url_row(
        "/robots.txt", CrawlTarget.ROBOTS, PageKind.OTHER, 0, status=CrawlUrlStatus.FETCHED,
        method=AcquisitionMethod.HTTP_FETCH, final_url=f"{base}/robots.txt", http_status=200,
        content_type="text/plain", byte_size=robots_page.byte_size,
        body_sha256=robots_page.content_sha256, acquired_page_id=robots_page.id,
        raw_record_id=robots_raw.id, request_count=1, fetch_ms=42,
        headers_json={"content-type": "text/plain"},
    )  # fmt: skip
    home_body = _SYNTHETIC_HOME.encode()
    home_page = page(f"{base}/", 200, "text/html; charset=utf-8", home_body)
    home_payload = snapshot(f"{base}/", f"{base}/", _SYNTHETIC_HOME, rendered=False)
    home_raw = raw(home_page, AcquisitionMethod.HTTP_FETCH, home_payload | {"test_data": True})
    home = url_row(
        "/", CrawlTarget.PAGE, PageKind.HOME, 0, candidate_id=candidate_id,
        status=CrawlUrlStatus.FETCHED, method=AcquisitionMethod.HTTP_FETCH, final_url=f"{base}/",
        http_status=200, content_type="text/html; charset=utf-8", byte_size=len(home_body),
        body_sha256=home_page.content_sha256, content_sha256=home_payload["content_sha256"],
        acquired_page_id=home_page.id, raw_record_id=home_raw.id, request_count=1, fetch_ms=120,
        headers_json={"content-type": "text/html; charset=utf-8", "etag": '"synthetic-v1"'},
    )  # fmt: skip
    missing_sitemap = page(f"{base}/sitemap.xml", 404, "text/html", None)
    url_row(
        "/sitemap.xml", CrawlTarget.SITEMAP, PageKind.OTHER, 0, status=CrawlUrlStatus.FAILED,
        reason="not_found", method=AcquisitionMethod.HTTP_FETCH, final_url=f"{base}/sitemap.xml",
        http_status=404, content_type="text/html", acquired_page_id=missing_sitemap.id,
        request_count=1, fetch_ms=30, discovered_from_id=None, error_message="HTTP 404",
    )  # fmt: skip
    shell = _SYNTHETIC_SHELL.encode()
    http_contact = page(f"{base}/contact", 200, "text/html; charset=utf-8", shell)
    rendered_body = _SYNTHETIC_RENDERED.encode()
    rendered_page = page(f"{base}/contact", 200, "text/html; charset=utf-8", rendered_body)
    http_view = snapshot(f"{base}/contact", f"{base}/contact", _SYNTHETIC_SHELL, rendered=False)
    rendered = snapshot(f"{base}/contact", f"{base}/contact", _SYNTHETIC_RENDERED, rendered=True)
    rendered["fallback"] = {
        "reason": "empty_shell",
        "browser": "chromium (synthetic)",
        "blocked_requests": 2,
        "http_signals": http_view["signals"],
        "http_body_sha256": http_contact.content_sha256,
    }
    contact_raw = raw(
        rendered_page, AcquisitionMethod.BROWSER_RENDER, rendered | {"test_data": True}
    )
    contact = url_row(
        "/contact", CrawlTarget.PAGE, PageKind.CONTACT, 1, discovered_from_id=home.id,
        status=CrawlUrlStatus.FETCHED, method=AcquisitionMethod.BROWSER_RENDER,
        final_url=f"{base}/contact", http_status=200, content_type="text/html; charset=utf-8",
        byte_size=len(rendered_body), body_sha256=rendered_page.content_sha256,
        content_sha256=rendered["content_sha256"], acquired_page_id=rendered_page.id,
        raw_record_id=contact_raw.id, request_count=1, fetch_ms=95, browser_ms=900,
        fallback_reason="empty_shell", headers_json={"content-type": "text/html; charset=utf-8"},
    )  # fmt: skip
    url_row(
        "/about-us", CrawlTarget.PAGE, PageKind.ABOUT, 1, discovered_from_id=home.id,
        status=CrawlUrlStatus.SKIPPED, reason="robots",
    )  # fmt: skip
    gone = page(f"{base}/gallery", 404, "text/html", None)
    url_row(
        "/gallery", CrawlTarget.PAGE, PageKind.OTHER, 1, discovered_from_id=home.id,
        status=CrawlUrlStatus.FAILED, reason="not_found", method=AcquisitionMethod.HTTP_FETCH,
        final_url=f"{base}/gallery", http_status=404, content_type="text/html",
        acquired_page_id=gone.id, request_count=1, fetch_ms=51, error_message="HTTP 404",
    )  # fmt: skip
    url_row(
        "/cart", CrawlTarget.PAGE, PageKind.OTHER, 1, discovered_from_id=home.id,
        status=CrawlUrlStatus.SKIPPED, reason="private_area",
    )  # fmt: skip
    for name, detail in (
        ("site_finished", {"site": SYNTHETIC_SITE, "pages": 2, "stopped_by": None}),
        ("run_succeeded", None),
    ):
        records.append(
            JobEvent(
                id=ids(),
                job_id=job.id,
                task_id=task.id if detail else None,
                level=EventLevel.INFO,
                event=name,
                error_code=None,
                retryable=None,
                detail_json=detail,
                created_at=now,
            )
        )
    with atomic(conn):
        insert(conn, *records)
        audit(conn, AuditAction.CREATE, EntityType.JOB, job.id, actor=ACTOR_DEV_SEED)
    return CrawlFixture(job_id=job.id, source_run_id=run.id, rendered_url_id=contact.id)


# ---- entity resolution (phase 06) ---------------------------------------------------------------

#: One plumber written two ways (merged automatically, with two different phones to show a
#: conflict), one bakery that moved (sent to review) and a generic name in two states (distinct).
SYNTHETIC_RESOLUTION_ROWS: tuple[Mapping[str, str], ...] = (
    {"name": "Synthetic Plumbing 0100 LLC", "address": "100 Example Ave Suite 4", "city": "Austin",
     "state": "TX", "zip": "78701", "phone": "(512) 555-0110", "category": "Plumbing",
     "website": "http://www.synthetic-plumbing-0100.example/?utm_source=seed"},
    {"name": "Synthetic Plumbing 0100, Inc.", "address": "100 Example Avenue, Ste 4",
     "city": "Austin", "state": "Texas", "zip": "78701-2233", "phone": "512.555.0111",
     "category": "Plumbing, Heating, and Air-Conditioning Contractors",
     "website": "https://synthetic-plumbing-0100.example"},
    {"name": "Synthetic Bakery 0200", "address": "12 Sample Mill Rd", "city": "Denver",
     "state": "CO", "zip": "80202", "phone": "(303) 555-0140", "category": "Bakery",
     "website": "synthetic-bakery-0200.example", "email": "info@synthetic-bakery-0200.example"},
    {"name": "Synthetic Bakery 0200 LLC", "address": "880 Demo Market St", "city": "Denver",
     "state": "CO", "zip": "80205", "phone": "303-555-0140", "category": "Retail Bakeries",
     "website": "https://synthetic-bakery-0200.example/"},
    {"name": "Synthetic Cleaning Services", "address": "10 Fixture Ln", "city": "Houston",
     "state": "TX", "zip": "77002", "phone": "(713) 555-0130"},
    {"name": "Synthetic Cleaning Services", "address": "22 Fixture Ln", "city": "Tampa",
     "state": "FL", "zip": "33602", "phone": "(813) 555-0131"},
)  # fmt: skip


def insert_candidates(
    conn: sqlite3.Connection,
    ctx: SyntheticContext,
    rows: Sequence[Mapping[str, str]],
    *,
    campaign_id: str | None = None,
) -> list[str]:
    """Discovery candidates over raw records, the way a finished run stores them (test data).

    Each row needs a `name`; `address`, `city`, `state`, `zip`, `website`, `phone`, `email` and
    `category` are optional. The whole row is kept in the raw payload.
    """
    now, ids = ctx.now, ctx.ids
    records: list[Record] = []
    candidate_ids: list[str] = []
    for number, row in enumerate(rows, start=1):
        raw_id = ids()
        payload: JsonObject = {**row, "test_data": True, "_row": number, "_record": raw_id}
        website = row.get("website")
        raw = RawRecord(
            id=raw_id,
            source_run_id=ctx.source_run_id,
            acquired_page_id=None,
            connector_id=ctx.connector_id,
            method=AcquisitionMethod.SYNTHETIC,
            external_id=f"synthetic-candidate-{raw_id}",
            source_url=None,
            payload_json=payload,
            payload_sha256=_sha256(orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)),
            acquired_at=now,
            created_at=now,
        )
        candidate = DiscoveryCandidate(
            id=ids(),
            project_id=ctx.project_id,
            campaign_id=campaign_id,
            source_run_id=ctx.source_run_id,
            raw_record_id=raw.id,
            connector_id=ctx.connector_id,
            dedup_key=dedup_key(
                name=row["name"],
                locality=row.get("city"),
                region=row.get("state"),
                domain=domain_of(website),
            ),
            duplicate_of_id=None,
            name_raw=row["name"],
            name_normalized=sort_key(row["name"]),
            locality=row.get("city"),
            region=row.get("state"),
            postal_code=row.get("zip"),
            country_code="US",
            domain=domain_of(website),
            website_url=website,
            phone_raw=row.get("phone"),
            category_raw=row.get("category"),
            external_id=raw.external_id,
            source_url=None,
            discovered_at=now,
            created_at=now,
        )
        records += [raw, candidate]
        candidate_ids.append(candidate.id)
    with atomic(conn):
        insert(conn, *records)
    return candidate_ids


@dataclass(frozen=True, slots=True)
class ResolutionFixture:
    job_id: str
    candidate_ids: list[str]


def seed_resolution(conn: sqlite3.Connection, ctx: SyntheticContext) -> ResolutionFixture:
    """The synthetic duplicates above, resolved by a finished run of the real engine."""
    from research_engine.resolution import engine, store

    candidate_ids = insert_candidates(conn, ctx, SYNTHETIC_RESOLUTION_ROWS)
    created, _ = store.plan_runs(conn, ctx.project_id, actor=ACTOR_DEV_SEED)
    engine.run_resolution(conn, created[0], lambda: False)
    return ResolutionFixture(job_id=created[0], candidate_ids=candidate_ids)


# ---- Latino evidence (phase 07) -----------------------------------------------------------------


def synthetic_source(
    conn: sqlite3.Connection, ctx: SyntheticContext, key: str, kind: SourceKind
) -> SyntheticContext:
    """Another synthetic source of the same project, of a kind the Latino rules trust differently.
    Records inserted through the returned context come from it."""
    now, ids = ctx.now, ctx.ids
    connector_id = _scalar(conn, "SELECT id FROM source_connectors WHERE key = ?", key)
    records: list[Record] = []
    if connector_id is None:
        connector = SourceConnector(
            id=ids(), key=key, name=f"Synthetic {kind.value.replace('_', ' ')} (test data)",
            kind=kind, connector_version="1.0.0", terms_url=None, requires_credentials=False,
            enabled=True, created_at=now, updated_at=now,
        )  # fmt: skip
        records.append(connector)
        connector_id = connector.id
    run = synthetic_run(ctx.project_id, connector_id, now, ids)
    with atomic(conn):
        insert(conn, *records, run)
    return replace(ctx, connector_id=connector_id, source_run_id=run.id)


def synthetic_page_html(lang: str, title: str, text: str) -> str:
    return (
        f"<!doctype html><html lang='{lang}'><head><title>{title}</title></head><body><main>"
        f"<h1>{title}</h1><p>{text}</p></main></body></html>"
    )


def insert_site_pages(
    conn: sqlite3.Connection,
    ctx: SyntheticContext,
    site: str,
    pages: Mapping[str, str],
    *,
    candidate_id: str | None = None,
    data_dir: Path | None = None,
    robots: str | None = None,
) -> list[str]:
    """A finished crawl of `site` whose pages (path -> HTML) were fetched at `ctx.now`, the way the
    crawler stores them (test data). With `data_dir` the bodies are stored as artifacts too, and
    `robots` adds a robots.txt the crawl read. Returns the raw record id of each page, in order."""
    from research_engine.crawl.engine import (
        CONNECTOR_KEY,
        CONNECTOR_NAME,
        CONNECTOR_VERSION,
        store_artifact,
    )
    from research_engine.crawl.extract import snapshot

    now, ids = ctx.now, ctx.ids
    connector_id = _scalar(conn, "SELECT id FROM source_connectors WHERE key = ?", CONNECTOR_KEY)
    records: list[Record] = []
    if connector_id is None:
        connector = SourceConnector(
            id=ids(), key=CONNECTOR_KEY, name=CONNECTOR_NAME, kind=SourceKind.BUSINESS_WEBSITE,
            connector_version=CONNECTOR_VERSION, terms_url=None, requires_credentials=False,
            enabled=True, created_at=now, updated_at=now,
        )  # fmt: skip
        records.append(connector)
        connector_id = connector.id
    run = SourceRun(
        id=ids(), project_id=ctx.project_id, campaign_id=None, connector_id=connector_id,
        connector_version=CONNECTOR_VERSION, config_id=None, job_id=None,
        status=SourceRunStatus.SUCCEEDED,
        effective_config_json={"connector_key": CONNECTOR_KEY, "site": site,
                               "candidate_id": candidate_id, "test_data": True},
        started_at=now, finished_at=now, error_code=None, error_message=None, created_at=now,
        updated_at=now,
    )  # fmt: skip
    records.append(run)
    raw_ids: list[str] = []
    if robots is not None:
        robots_url = f"https://{site}/robots.txt"
        robots_payload: JsonObject = {"text": robots, "test_data": True}
        robots_raw = RawRecord(
            id=ids(), source_run_id=run.id, acquired_page_id=None, connector_id=connector_id,
            method=AcquisitionMethod.HTTP_FETCH, external_id=None, source_url=robots_url,
            payload_json=robots_payload,
            payload_sha256=_sha256(orjson.dumps(robots_payload, option=orjson.OPT_SORT_KEYS)),
            acquired_at=now, created_at=now,
        )  # fmt: skip
        row: dict[str, object] = dict.fromkeys(CrawlUrl.model_fields)
        row |= {
            "id": ids(), "source_run_id": run.id, "candidate_id": candidate_id, "url": robots_url,
            "target": CrawlTarget.ROBOTS, "page_kind": PageKind.OTHER, "depth": 0,
            "priority": 1000, "status": CrawlUrlStatus.FETCHED,
            "method": AcquisitionMethod.HTTP_FETCH, "final_url": robots_url, "http_status": 200,
            "content_type": "text/plain", "byte_size": len(robots.encode()),
            "raw_record_id": robots_raw.id, "request_count": 1, "retry_count": 0, "fetch_ms": 20,
            "created_at": now, "updated_at": now,
        }  # fmt: skip
        records += [robots_raw, CrawlUrl.model_validate(row)]
    for path, html in pages.items():
        url, body = f"https://{site}{path}", html.encode()
        digest = _sha256(body)
        if data_dir is not None:
            store_artifact(data_dir, body, ".html")
        acquired = AcquiredPage(
            id=ids(), source_run_id=run.id, source_query_id=None, url=url, final_url=url,
            http_status=200, content_type="text/html; charset=utf-8", content_sha256=digest,
            byte_size=len(body), artifact_path=f"artifacts/web/{digest[:2]}/{digest}.html",
            fetched_at=now,
        )  # fmt: skip
        payload = snapshot(url, url, html, rendered=False) | {"test_data": True}
        raw = RawRecord(
            id=ids(), source_run_id=run.id, acquired_page_id=acquired.id,
            connector_id=connector_id, method=AcquisitionMethod.HTTP_FETCH, external_id=None,
            source_url=url, payload_json=payload,
            payload_sha256=_sha256(orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)),
            acquired_at=now, created_at=now,
        )  # fmt: skip
        values: dict[str, object] = dict.fromkeys(CrawlUrl.model_fields)
        values |= {
            "id": ids(), "source_run_id": run.id, "candidate_id": candidate_id, "url": url,
            "target": CrawlTarget.PAGE,
            "page_kind": PageKind.HOME if path == "/" else PageKind.OTHER,
            "depth": 0 if path == "/" else 1, "priority": 100, "status": CrawlUrlStatus.FETCHED,
            "method": AcquisitionMethod.HTTP_FETCH, "final_url": url, "http_status": 200,
            "content_type": "text/html; charset=utf-8", "byte_size": len(body),
            "body_sha256": digest, "content_sha256": payload["content_sha256"],
            "acquired_page_id": acquired.id, "raw_record_id": raw.id, "request_count": 1,
            "retry_count": 0, "fetch_ms": 40, "created_at": now, "updated_at": now,
        }  # fmt: skip
        records += [acquired, raw, CrawlUrl.model_validate(values)]
        raw_ids.append(raw.id)
    with atomic(conn):
        insert(conn, *records)
    return raw_ids


@dataclass(frozen=True, slots=True)
class LatinoFixture:
    job_id: str
    #: Candidate ids by the situation each one stands for (see `seed_latino`).
    candidates: Mapping[str, str]


def seed_latino(
    conn: sqlite3.Connection, ctx: SyntheticContext, data_dir: Path | None = None
) -> LatinoFixture:
    """Businesses whose sources and websites say different things about ownership and market,
    resolved and evaluated by the real engines (test data). Their names say nothing on purpose:

    - `certified`: a public registry records a Hispanic-owned certification (verified);
    - `listed`: a directory of Latino-owned businesses lists it (probable);
    - `declared`: its own website declares Latina ownership (high confidence);
    - `spanish`: its website is in Spanish and serves the Hispanic community (market affinity);
    - `brand_austin`, `brand_denver`: two locations of one brand whose shared website declares
      Hispanic ownership (organizational evidence: probable, with a warning);
    - `disputed_listing`, `disputed_registry`: records of one business that an ownership directory
      lists and a newer registry record says is not Hispanic-owned (contradicted: unknown).
    """
    from research_engine.latino import engine as latino_engine
    from research_engine.latino import store as latino_store
    from research_engine.resolution import engine, store

    registry = synthetic_source(conn, ctx, "synthetic.registry", SourceKind.PUBLIC_REGISTRY)
    directory = synthetic_source(conn, ctx, "synthetic.directory", SourceKind.DIRECTORY)
    disputed = {"name": "Synthetic Grocery 0507", "address": "507 Example Ave", "city": "Austin",
                "state": "TX", "zip": "78702", "phone": "(512) 555-0157",
                "website": "https://synthetic-grocery-0507.example/"}  # fmt: skip
    rows: dict[str, tuple[SyntheticContext, Mapping[str, str]]] = {
        "certified": (registry, {
            "name": "Synthetic Auto Repair 0501", "address": "501 Example Ave", "city": "Austin",
            "state": "TX", "zip": "78701", "phone": "(512) 555-0151",
            "certification": "Hispanic-owned business enterprise (synthetic certificate)",
            "certification_expires": "2030-12-31"}),
        "listed": (directory, {
            "name": "Synthetic Bakery 0502", "address": "502 Example Ave", "city": "Phoenix",
            "state": "AZ", "zip": "85004", "phone": "(602) 555-0152",
            "directory": "Latino-owned business directory (synthetic)"}),
        "declared": (ctx, {
            "name": "Synthetic Dental Clinic 0503", "address": "503 Example Ave", "city": "Miami",
            "state": "FL", "zip": "33130", "phone": "(305) 555-0153",
            "website": "https://synthetic-dental-clinic-0503.example/"}),
        "spanish": (ctx, {
            "name": "Synthetic Landscaping 0504", "address": "504 Example Ave", "city": "Chicago",
            "state": "IL", "zip": "60601", "phone": "(312) 555-0154",
            "website": "https://synthetic-landscaping-0504.example/"}),
        "brand_austin": (ctx, {
            "name": "Synthetic Tax Preparation 0505", "address": "505 Example Ave",
            "city": "Austin", "state": "TX", "zip": "78701", "phone": "(512) 555-0155",
            "website": "https://synthetic-tax-0505.example/"}),
        "brand_denver": (ctx, {
            "name": "Synthetic Tax Preparation 0505", "address": "12 Sample Mill Rd",
            "city": "Denver", "state": "CO", "zip": "80202", "phone": "(303) 555-0156",
            "website": "https://synthetic-tax-0505.example/"}),
        "disputed_listing": (directory, disputed | {
            "directory": "Hispanic-owned business directory (synthetic)"}),
        "disputed_registry": (replace(registry, now=ctx.now + timedelta(days=1)),
                              disputed | {"hispanic_owned": "no"}),
    }  # fmt: skip
    candidates: dict[str, str] = {}
    for situation, (source, row) in rows.items():
        (candidates[situation],) = insert_candidates(conn, source, [row])
    sites = {
        "declared": ("synthetic-dental-clinic-0503.example", "en", "About the clinic",
                     "We are a proud Latina-owned family practice on Example Ave. Test data only."),
        "spanish": ("synthetic-landscaping-0504.example", "es", "Jardinería",
                    "Se habla español. Atendemos a la comunidad hispana de Example Ave. "
                    "Datos de prueba."),
        "brand_austin": ("synthetic-tax-0505.example", "en", "Two offices, one team",
                         "Proudly Hispanic-owned since 1998, with offices in two states. "
                         "Test data only."),
    }  # fmt: skip
    for situation, (site, lang, title, text) in sites.items():
        insert_site_pages(conn, ctx, site, {"/": synthetic_page_html(lang, title, text)},
                          candidate_id=candidates[situation], data_dir=data_dir)  # fmt: skip
    resolved, _ = store.plan_runs(conn, ctx.project_id, actor=ACTOR_DEV_SEED)
    engine.run_resolution(conn, resolved[0], lambda: False)
    planned, _ = latino_store.plan_runs(conn, ctx.project_id, actor=ACTOR_DEV_SEED)
    latino_engine.run_latino(conn, planned[0], lambda: False)
    return LatinoFixture(job_id=planned[0], candidates=candidates)


# ---- website intelligence (phase 08) ------------------------------------------------------------

#: Synthetic sites by the situation each one stands for (see `seed_website`).
WEBSITE_SITES: Mapping[str, str] = {
    "complete": "synthetic-roofing-0601.example",
    "changed": "synthetic-cleaning-0602.example",
}
SYNTHETIC_ROBOTS = "User-agent: *\nDisallow: /admin/\n# synthetic test data\n"


def synthetic_complete_site(site: str, year: int) -> dict[str, str]:
    """A bilingual business site that does most things right (test data): path -> HTML."""
    base = f"https://{site}"
    schema = orjson.dumps({
        "@context": "https://schema.org", "@type": "RoofingContractor",
        "name": "Synthetic Roofing 0601", "telephone": "(512) 555-0161",
        "address": {"@type": "PostalAddress", "streetAddress": "601 Example Ave",
                    "addressLocality": "Austin", "addressRegion": "TX", "postalCode": "78701"},
        "openingHours": "Mo-Fr 08:00-17:00", "areaServed": "Example County",
        "sameAs": ["https://profiles.example/synthetic-roofing-0601"],
    }).decode()  # fmt: skip
    faq = orjson.dumps({
        "@context": "https://schema.org", "@type": "FAQPage",
        "mainEntity": [{"@type": "Question", "name": "How long does a repair take?",
                        "acceptedAnswer": {"@type": "Answer", "text": "One day (test data)."}}],
    }).decode()  # fmt: skip
    head = (
        "<meta name='description' content='Synthetic Roofing 0601 (test data): roof repair, "
        "inspection and replacement in Austin, with free written estimates.'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<link rel='canonical' href='{base}/'><link rel='icon' href='/favicon.ico'>"
        f"<link rel='alternate' hreflang='en' href='{base}/'>"
        f"<link rel='alternate' hreflang='es' href='{base}/es/'>"
        f"<meta property='og:title' content='Synthetic Roofing 0601'>"
        f"<meta property='og:image' content='{base}/og.png'>"
        "<script async src='https://www.googletagmanager.com/gtag/js?id=G-TEST0601'></script>"
        "<script>function gtag(){} gtag('config', 'G-TEST0601');</script>"
        f'<script type="application/ld+json">{schema}</script>'
    )
    nav = (
        "<header><nav><a href='/about'>About us</a> <a href='/services'>Services</a> "
        "<a href='/faq'>FAQ</a> <a href='/contact'>Contact</a> <a href='/es/'>Español</a> "
        "<a class='btn' href='tel:+15125550161'>Call (512) 555-0161</a></nav></header>"
    )
    footer = (
        "<footer><p>601 Example Ave, Austin, TX 78701. Mon-Fri 8am to 5pm.</p>"
        f"<a href='/privacy-policy'>Privacy policy</a><p>&copy; {year} Synthetic Roofing 0601 "
        "(test data)</p></footer>"
    )
    filler = " ".join(
        ["Synthetic test data: this roofing company repairs, inspects and replaces roofs for "
         "homes and small businesses across Example County, with written estimates."] * 12
    )  # fmt: skip

    def page(title: str, main: str, lang: str = "en", extra: str = "") -> str:
        return (
            f"<!doctype html><html lang='{lang}'><head><title>{title}</title>{head}{extra}</head>"
            f"<body>{nav}<main>{main}</main>{footer}</body></html>"
        )

    spanish = " ".join(
        ["Datos de prueba: reparamos y cambiamos los techos de las casas y de los negocios de la "
         "zona, con un presupuesto por escrito y un trabajo limpio en cada casa."] * 8
    )  # fmt: skip
    return {
        "/": page(
            "Synthetic Roofing 0601 | Roof repair in Austin",
            f"<h1>Roof repair in Austin</h1><p>{filler}</p><h2>Why homeowners call us</h2>"
            "<p>Licensed and fully insured since 2005. What our customers say: on time and tidy. "
            "Proudly serving Austin and surrounding areas.</p>"
            "<a class='btn' href='/contact'>Request a free quote</a>"
            "<img src='/crew.jpg' alt='The synthetic crew'>",
        ),
        "/about": page("About us | Synthetic Roofing 0601",
                       "<h1>About us</h1><p>A synthetic family business, licensed and fully "
                       "insured since 2005. Testimonials from synthetic customers.</p>"),
        "/services": page("Services | Synthetic Roofing 0601",
                          "<h1>Services</h1><h2>Roof repair</h2><h2>Roof replacement</h2>"
                          "<h2>Inspections</h2>"),
        "/contact": page(
            "Contact | Synthetic Roofing 0601",
            "<h1>Contact us</h1><form action='/contact/send' method='post'>"
            "<label for='name'>Name</label><input id='name' name='name' required>"
            "<label for='email'>Email</label><input id='email' name='email' type='email' required>"
            "<label for='message'>Message</label><textarea id='message' name='message'></textarea>"
            "<button type='submit'>Send message</button></form>"
            "<iframe src='https://www.google.com/maps/embed?pb=synthetic-test-data'></iframe>",
        ),
        "/faq": page("FAQ | Synthetic Roofing 0601",
                     "<h1>Frequently asked questions</h1><h2>How long does a repair take?</h2>"
                     "<p>One day.</p><h2>Do you give written estimates?</h2><p>Yes.</p>"
                     "<h2>What areas do you serve?</h2><p>Example County.</p>",
                     extra=f'<script type="application/ld+json">{faq}</script>'),
        "/privacy-policy": page("Privacy policy | Synthetic Roofing 0601",
                                "<h1>Privacy policy</h1><p>Synthetic test data.</p>"),
        "/es/": page("Reparación de techos en Austin | Synthetic Roofing 0601",
                     f"<h1>Reparación de techos</h1><p>{spanish}</p>", lang="es"),
    }  # fmt: skip


def synthetic_weak_home(site: str, *, improved: bool) -> str:
    """A home page with no call to action or form, mixed content and broken structured data (test
    data); `improved` adds a viewport, a tap-to-call link and analytics, and fixes the data."""
    schema = (
        '{"@type": "LocalBusiness", "name": "Synthetic Cleaning 0602"}'
        if improved
        else '{"@type": "LocalBusiness", "name": '
    )
    head = (
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<script async src='https://www.googletagmanager.com/gtag/js?id=G-TEST0602'></script>"
        if improved
        else ""
    )
    phone = "<a href='tel:+13125550162'>(312) 555-0162</a>" if improved else "(312) 555-0162"
    return (
        f"<!doctype html><html><head><title>Home</title>{head}"
        f'<script type="application/ld+json">{schema}</script></head><body>'
        f"<h1>Synthetic Cleaning 0602</h1><h3>Clean homes</h3><img src='http://{site}/photo.jpg'>"
        f"<img src='/van.jpg'><p>Synthetic test data. Call {phone} for house cleaning.</p>"
        "</body></html>"
    )


@dataclass(frozen=True, slots=True)
class WebsiteFixture:
    #: The first audit run, and the run after the `changed` site was crawled again.
    job_ids: tuple[str, str]
    sites: Mapping[str, str]


def seed_website(conn: sqlite3.Connection, ctx: SyntheticContext, data_dir: Path) -> WebsiteFixture:
    """Crawled sites audited by the real engine (test data). Every crawled site of the project is
    audited, so sites seeded by other phases are too.

    - `complete`: a bilingual roofing site with structured data, analytics, a quote action, a
      labelled form, an FAQ and a privacy policy;
    - `changed`: a cleaning site without a call to action or a form, with mixed content and broken
      structured data, crawled again a month later with a viewport, a tap-to-call link and
      analytics, so its second audit shows what changed.
    """
    from research_engine.website import engine as website_engine
    from research_engine.website import store as website_store

    def audit() -> str:
        created, _ = website_store.plan_runs(conn, ctx.project_id, [], actor=ACTOR_DEV_SEED)
        website_engine.run_website(conn, data_dir, created[0], lambda: False)
        return created[0]

    earlier = replace(ctx, now=ctx.now - timedelta(days=30))
    complete, changed = WEBSITE_SITES["complete"], WEBSITE_SITES["changed"]
    insert_site_pages(conn, earlier, complete, synthetic_complete_site(complete, ctx.now.year),
                      data_dir=data_dir, robots=SYNTHETIC_ROBOTS)  # fmt: skip
    insert_site_pages(conn, earlier, changed, {"/": synthetic_weak_home(changed, improved=False)},
                      data_dir=data_dir, robots=SYNTHETIC_ROBOTS)  # fmt: skip
    first = audit()
    insert_site_pages(conn, ctx, changed, {"/": synthetic_weak_home(changed, improved=True)},
                      data_dir=data_dir, robots=SYNTHETIC_ROBOTS)  # fmt: skip
    return WebsiteFixture(job_ids=(first, audit()), sites=dict(WEBSITE_SITES))


# ---- contact intelligence (phase 09) -------------------------------------------------------------

#: Synthetic sites by the situation each one stands for (see `seed_contacts`).
CONTACT_SITES: Mapping[str, str] = {
    "published": "synthetic-plumbing-0701.example",
    "moved": "synthetic-painting-0702.example",
}
#: The fictional people those sites publish (test data).
CONTACT_PEOPLE: Mapping[str, str] = {
    "owner": "Alex Example",
    "manager": "Jordan Sample",
    "office": "Casey Demo",
    "founder": "Morgan Placeholder",
}


def synthetic_mail_lookup(domain: str) -> tuple[MailDomainResult, tuple[str, ...]]:
    """A mail-domain lookup that never touches the network (test data): synthetic `.example`
    domains accept mail and every other domain does not exist."""
    if domain.startswith("synthetic-") and domain.endswith(".example"):
        return MailDomainResult.MX, (f"mx.{domain}",)
    return MailDomainResult.NXDOMAIN, ()


def synthetic_contact_site(site: str) -> dict[str, str]:
    """A small business site that publishes its contacts in every usual way (test data)."""
    business = orjson.dumps({
        "@context": "https://schema.org", "@type": "Plumber", "name": "Synthetic Plumbing 0701",
        "telephone": "+1 512-555-0171", "email": f"info@{site}",
        "address": {"@type": "PostalAddress", "streetAddress": "701 Example Ave",
                    "addressLocality": "Austin", "addressRegion": "TX", "postalCode": "78701"},
        "contactPoint": {"@type": "ContactPoint", "contactType": "sales",
                         "telephone": "+1 512-555-0172", "email": f"sales@{site}"},
        "sameAs": ["https://www.facebook.com/syntheticplumbing0701"],
    }).decode()  # fmt: skip
    manager = orjson.dumps({
        "@context": "https://schema.org", "@type": "Person", "name": CONTACT_PEOPLE["manager"],
        "jobTitle": "General Manager", "email": f"jordan.sample@{site}",
    }).decode()  # fmt: skip

    def page(title: str, main: str, extra: str = "") -> str:
        return (
            f"<!doctype html><html lang='en'><head><title>{title}</title>{extra}</head><body>"
            f"<main>{main}</main></body></html>"
        )

    return {
        "/": page(
            "Synthetic Plumbing 0701",
            "<h1>Synthetic Plumbing 0701</h1><p>Synthetic test data. Call "
            "<a href='tel:+15125550171'>(512) 555-0171</a> or write to "
            f"<a href='mailto:info@{site}'>info@{site}</a>.</p>"
            "<a href='https://calendly.com/synthetic-plumbing-0701/visit'>Book a visit</a> "
            "<a href='https://www.facebook.com/syntheticplumbing0701'>Facebook</a>",
            f'<script type="application/ld+json">{business}</script>',
        ),
        "/about": page(
            "About us | Synthetic Plumbing 0701",
            f"<h1>About us</h1><p>{CONTACT_PEOPLE['owner']}, Owner, started this synthetic "
            f"business. {CONTACT_PEOPLE['office']}, Office Manager, answers the phone. Test data "
            "only.</p>",
            f'<script type="application/ld+json">{manager}</script>',
        ),
        "/contact": page(
            "Contact | Synthetic Plumbing 0701",
            "<h1>Contact us</h1><form action='/contact/send' method='post'>"
            "<label for='name'>Name</label><input id='name' name='name' required>"
            "<label for='email'>Email</label><input id='email' name='email' type='email' required>"
            "<label for='message'>Message</label><textarea id='message' name='message'></textarea>"
            "<button type='submit'>Send message</button></form>",
        ),
    }


def synthetic_moved_home(site: str, *, moved: bool) -> str:
    """The home page of a business before and after it changed its number (test data)."""
    number = "(312) 555-0182" if moved else "(312) 555-0181"
    return (
        "<!doctype html><html lang='en'><head><title>Synthetic Painting 0702</title></head><body>"
        f"<main><h1>Synthetic Painting 0702</h1><p>Founded by {CONTACT_PEOPLE['founder']} in 1998. "
        f"Call {number} or email hello@{site}. Test data only.</p></main></body></html>"
    )


@dataclass(frozen=True, slots=True)
class ContactFixture:
    job_id: str
    #: Candidate ids by situation (see `seed_contacts`).
    candidates: Mapping[str, str]
    sites: Mapping[str, str]
    suppression_id: str | None


def seed_contacts(
    conn: sqlite3.Connection, ctx: SyntheticContext, data_dir: Path
) -> ContactFixture:
    """Businesses whose listings and websites publish contacts, checked by the real engine with a
    mail-domain lookup that never touches the network (test data):

    - `published`: a site with a tap-to-call number and a mail link, structured data for the
      business with a sales contact point and a manager, a booking link, a profile, a contact form
      and an about page naming its owner and office manager; the manager's published address follows
      a pattern, so the engine suggests hypotheses for the other two, kept apart as hypotheses;
    - `moved`: a business whose listing and first crawl show one number and whose crawl a month
      later shows another, so the old number is stale and the values disagree; its founder is only
      named historically (ambiguous) and its address is on the do-not-contact list.
    """
    from research_engine.contacts import engine as contacts_engine
    from research_engine.contacts import store as contacts_store
    from research_engine.resolution import engine, store

    published, moved = CONTACT_SITES["published"], CONTACT_SITES["moved"]
    earlier = replace(ctx, now=ctx.now - timedelta(days=30))
    rows = {
        "published": {"name": "Synthetic Plumbing 0701", "address": "701 Example Ave",
                      "city": "Austin", "state": "TX", "zip": "78701", "phone": "(512) 555-0171",
                      "website": f"https://{published}/"},
        "moved": {"name": "Synthetic Painting 0702", "address": "702 Example Ave",
                  "city": "Chicago", "state": "IL", "zip": "60601", "phone": "(312) 555-0181",
                  "website": f"https://{moved}/"},
    }  # fmt: skip
    candidates = {
        situation: insert_candidates(conn, earlier, [row])[0] for situation, row in rows.items()
    }
    insert_site_pages(conn, earlier, published, synthetic_contact_site(published),
                      candidate_id=candidates["published"], data_dir=data_dir)  # fmt: skip
    insert_site_pages(conn, earlier, moved, {"/": synthetic_moved_home(moved, moved=False)},
                      candidate_id=candidates["moved"], data_dir=data_dir)  # fmt: skip
    insert_site_pages(conn, ctx, moved, {"/": synthetic_moved_home(moved, moved=True)},
                      candidate_id=candidates["moved"], data_dir=data_dir)  # fmt: skip
    resolved, _ = store.plan_runs(conn, ctx.project_id, actor=ACTOR_DEV_SEED)
    engine.run_resolution(conn, resolved[0], lambda: False)
    suppression = contacts_store.add_suppression(
        conn, SuppressionKind.EMAIL, f"hello@{moved}", business_id=None,
        reason="Asked not to be contacted (synthetic test data)", source="manual",
        actor=ACTOR_DEV_SEED, now=ctx.now,
    )  # fmt: skip
    planned, _ = contacts_store.plan_runs(conn, ctx.project_id, True, actor=ACTOR_DEV_SEED)
    contacts_engine.run_contacts(conn, data_dir, planned[0], lambda: False, synthetic_mail_lookup)
    return ContactFixture(job_id=planned[0], candidates=candidates, sites=dict(CONTACT_SITES),
                          suppression_id=suppression.id if suppression else None)  # fmt: skip


# ---- local AI (phase 10) -------------------------------------------------------------------------

#: Synthetic models a scripted runtime reports: a small text model and a small vision model.
SYNTHETIC_MODELS: Mapping[str, tuple[int, tuple[str, ...]]] = {
    "synthetic-text:1b": (1024**3, ("completion",)),
    "synthetic-vision:2b": (2 * 1024**3, ("completion", "vision")),
}
SYNTHETIC_MODEL_INFO: JsonObject = {
    "general.architecture": "synthetic",
    "synthetic.block_count": 16,
    "synthetic.attention.head_count": 16,
    "synthetic.attention.head_count_kv": 4,
    "synthetic.attention.key_length": 64,
    "synthetic.embedding_length": 1024,
    "synthetic.context_length": 8192,
}

Answer = str | ProviderError
Script = Callable[[ChatRequest], Answer]


class ScriptedProvider:
    """A SYNTHETIC runtime for tests and contract fixtures only: it answers from a script and never
    runs a model. The worker never uses it (a test checks it runs `OllamaProvider`)."""

    name = "synthetic"

    def __init__(
        self,
        answers: Sequence[Answer] | Script = (),
        *,
        running: bool = True,
        models: Mapping[str, tuple[int, tuple[str, ...]]] = SYNTHETIC_MODELS,
    ) -> None:
        self._script = answers if callable(answers) else None
        self._answers = list(answers) if not callable(answers) else []
        self.running = running
        self.models = dict(models)
        self.calls: list[ChatRequest] = []
        self.loaded_models: dict[str, LoadedModel] = {}
        self.unloaded: list[str] = []
        self.gate: object | None = None

    async def probe(self) -> OllamaStatus:
        models = [
            OllamaModel(name=name, size_bytes=size, digest="sha256:" + _sha256(name.encode()),
                        family="synthetic", parameter_size="1B", quantization="Q4_K_M")
            for name, (size, _) in sorted(self.models.items())
        ] if self.running else []  # fmt: skip
        return OllamaStatus(
            state=ComponentState.OK if self.running else ComponentState.UNAVAILABLE,
            summary="synthetic runtime" if self.running else "synthetic runtime stopped",
            error=None if self.running else "not running", checked_at=utc_now(), duration_ms=0.0,
            installed=True, running=self.running, base_url="http://127.0.0.1:11434",
            version="0.0.0-synthetic" if self.running else None, models=models,
        )  # fmt: skip

    def _require(self) -> None:
        if not self.running:
            raise ProviderError("unavailable", "synthetic runtime stopped", retryable=True)

    async def show(self, model: str) -> ModelDetails:
        self._require()
        if model not in self.models:
            raise ProviderError("model_not_found", f"model '{model}' not found", retryable=False)
        return ModelDetails(self.models[model][1], SYNTHETIC_MODEL_INFO)

    async def loaded(self) -> Sequence[LoadedModel]:
        self._require()
        return list(self.loaded_models.values())

    async def chat(self, request: ChatRequest) -> ChatAnswer:
        import asyncio

        self._require()
        self.calls.append(request)
        if isinstance(self.gate, asyncio.Event):
            await self.gate.wait()
        answer = self._script(request) if self._script else self._answers.pop(0)
        if isinstance(answer, ProviderError):
            raise answer
        size = self.models.get(request.model, (1024**3, ()))[0]
        self.loaded_models = {
            request.model: LoadedModel(
                request.model, None, size, size, int(str(request.options.get("num_ctx", 4096)))
            )
        }
        return ChatAnswer(content=answer, prompt_tokens=100, completion_tokens=50, total_ms=20,
                          load_ms=5, eval_ms=10, done_reason="stop")  # fmt: skip

    async def pull(
        self, model: str, progress: Callable[[str, int | None, int | None], None]
    ) -> None:
        self._require()
        progress("pulling synthetic layer", 512, 1024)
        progress("success", 1024, 1024)
        self.models[model] = (1024**3, ("completion",))

    async def delete(self, model: str) -> None:
        self._require()
        if self.models.pop(model, None) is None:
            raise ProviderError("model_not_found", f"model '{model}' not found", retryable=False)

    async def unload(self, model: str) -> None:
        self.unloaded.append(model)
        self.loaded_models.pop(model, None)


_REF_LINE = re.compile(r"^\[([A-Z][0-9]+)\] (.*)$", re.MULTILINE)


def synthetic_answer(request: ChatRequest) -> str:
    """What a careful model would answer to a request, from its fenced input (test data): every
    claim cites a ref it was given and quotes it literally; no ref means insufficient evidence."""
    user = next(message.content for message in reversed(request.messages) if message.role == "user")
    refs = dict(_REF_LINE.findall(user))
    properties = request.schema.get("properties")
    fields = set(properties) if isinstance(properties, dict) else set()
    first = next(iter(refs), None)

    def dumps(value: object) -> str:
        return orjson.dumps(value).decode()

    if "claims" in fields:
        owned = next((ref for ref, text in refs.items() if "owned" in text), None)
        if owned is not None:
            quote = refs[owned].split(".")[0].strip()
            claim = {"kind": "ownership_statement", "statement": "The page declares its ownership",
                     "quote": quote, "evidence_ids": [owned], "confidence": 0.8}  # fmt: skip
        elif first is not None:
            claim = {"kind": "other", "statement": "The page describes the business",
                     "quote": refs[first][:40].strip(), "evidence_ids": [first],
                     "confidence": 0.5}  # fmt: skip
        else:
            return dumps({"outcome": "insufficient_evidence", "claims": []})
        return dumps({"outcome": "ok", "claims": [claim]})
    if "vertical" in fields:
        if first is None:
            return dumps({"outcome": "insufficient_evidence", "vertical": None,
                          "vertical_evidence_ids": [], "services": [], "signals": []})  # fmt: skip
        text = " ".join(refs.values()).lower()
        vertical = "plumbing" if "plumb" in text else "dental" if "dental" in text else "other"
        return dumps({"outcome": "ok", "vertical": vertical, "vertical_evidence_ids": [first],
                      "services": [], "signals": []})  # fmt: skip
    if "fragments" in fields:
        return dumps({"outcome": "ok" if refs else "insufficient_evidence",
                      "fragments": [{"evidence_id": ref, "language": "en", "english": None}
                                    for ref in refs]})  # fmt: skip
    if "points" in fields:
        if first is None:
            return dumps({"outcome": "insufficient_evidence", "summary": None, "points": []})
        return dumps({"outcome": "ok", "summary": "The audit found problems to fix.",
                      "points": [{"text": "The most severe finding comes first.",
                                  "evidence_ids": [first]}]})  # fmt: skip
    if "problems" in fields:
        finding = next((ref for ref in refs if ref.startswith("W")), None)
        if finding is None:
            return dumps({"outcome": "insufficient_evidence", "problems": [], "strengths": [],
                          "missing_information": []})  # fmt: skip
        problem = {"problem": "other", "explanation": "A website finding shows a problem.",
                   "evidence_ids": [finding]}  # fmt: skip
        return dumps({"outcome": "ok", "problems": [problem], "strengths": [],
                      "missing_information": ["Opening hours"]})  # fmt: skip
    return dumps({"outcome": "insufficient_evidence", "observations": []})
