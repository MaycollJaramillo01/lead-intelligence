"""SYNTHETIC TEST DATA: factories for tests, contract fixtures and the development-only seed.

Nothing here is product output. Every value is fictitious on purpose: business names start with
"Synthetic", domains use the reserved `.example` top-level domain, phone numbers use the 555-01xx
range reserved for fiction, addresses are on "Example Ave", payloads carry `"test_data": true`,
every project created here is flagged `is_synthetic`, and no person is represented at all.
"""

import csv
import hashlib
import io
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import orjson

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
    NameKind,
    Project,
    RawRecord,
    Record,
    SourceConnector,
    SourceKind,
    SourceQuery,
    SourceRun,
    SourceRunStatus,
    SourceSelection,
    SyntheticSeedResult,
    ValidationStatus,
    Website,
    empty_criteria,
    new_id,
    sort_key,
    utc_now,
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
        "ORDER BY name_sort_key, id LIMIT 1"
    )
    find_dataset = (
        "SELECT id FROM datasets WHERE project_id = ? AND name = ? AND deleted_at IS NULL"
    )
    count_live = "SELECT count(*) FROM businesses WHERE project_id = ? AND deleted_at IS NULL"
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
