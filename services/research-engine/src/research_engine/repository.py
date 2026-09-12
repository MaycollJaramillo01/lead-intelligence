"""Repositories over the operational SQLite store: the only code that reads or writes the model.

The UI never runs SQL and the desktop core never opens the database (D-004); every read and write
of the data model goes through these functions inside the worker.

- Records are validated by their Pydantic model before they reach SQL, and the database checks the
  same rules again (types, enumerations, foreign keys, timestamps, immutability).
- Multi-statement operations run in one transaction (`atomic`; nested calls use savepoints).
- Status changes follow the state machines and, like soft deletes and restores, write an audit
  entry in the same transaction.
- Lists use keyset pagination: an opaque cursor holds the last sort key and id, so pages stay
  stable while rows are inserted and no query ever loads the whole table.
"""

import base64
import binascii
import re
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from functools import cache
from itertools import count
from pathlib import Path
from typing import Any

import orjson
from pydantic import BaseModel

from research_engine.domain import (
    UUID_PATTERN,
    AcquiredPage,
    AuditAction,
    AuditEntry,
    Business,
    BusinessClassification,
    BusinessDetail,
    BusinessListItem,
    BusinessListQuery,
    BusinessLocation,
    BusinessName,
    BusinessPage,
    BusinessSort,
    Campaign,
    CampaignListItem,
    CampaignListQuery,
    CampaignPage,
    CampaignSaveRequest,
    CandidateDetail,
    CandidateListItem,
    CandidateListQuery,
    CandidatePage,
    ContactPoint,
    CrawlErrorGroup,
    CrawlMetrics,
    CrawlRun,
    CrawlRunListQuery,
    CrawlRunPage,
    CrawlSettings,
    CrawlSite,
    CrawlUrl,
    CrawlUrlDetail,
    CrawlUrlItem,
    CrawlUrlListQuery,
    CrawlUrlPage,
    Dataset,
    DatasetListItem,
    DatasetListQuery,
    DatasetPage,
    DatasetSnapshot,
    DiscoveryCandidate,
    DiscoveryCriteria,
    DiscoveryRun,
    DiscoveryRunListQuery,
    DiscoveryRunPage,
    DiscoverySourceRun,
    EntityType,
    EvidenceItem,
    Extraction,
    Job,
    JobTask,
    JsonObject,
    Observed,
    ObservedClassification,
    ObservedContact,
    ObservedEvidence,
    ObservedLocation,
    ObservedName,
    ObservedWebsite,
    Project,
    Provenance,
    RawRecord,
    Record,
    RunEvent,
    SourceConnector,
    SourceQuery,
    SourceRun,
    SourceRunStatus,
    Website,
    can_transition,
    format_ts,
    new_id,
    sort_key,
    utc_now,
)
from research_engine.storage import SQLITE_FILE, bundled_migrations, open_sqlite

ACTOR_SYSTEM = "system"
#: `jobs.kind` of a discovery run; the engine and the repository must agree on it.
DISCOVERY_JOB_KIND = "discovery"


class StoreUnavailableError(RuntimeError):
    """The store cannot serve requests: not configured, not created or not migrated."""


class NotFoundError(LookupError):
    pass


class InvalidTransitionError(ValueError):
    pass


class InvalidCursorError(ValueError):
    pass


# ---- connections and transactions --------------------------------------------------------------


@cache
def _expected_schema_version() -> int:
    return len(bundled_migrations("sqlite"))


def connect(data_dir: Path | None) -> sqlite3.Connection:
    """Opens the migrated store. The worker migrates it at startup and on every health check."""
    if data_dir is None:
        raise StoreUnavailableError("the data directory is not configured (LEAD_DATA_DIR)")
    path = data_dir / SQLITE_FILE
    if not path.is_file():
        raise StoreUnavailableError(f"{SQLITE_FILE} has not been created yet")
    conn = open_sqlite(path)
    try:
        (version,) = conn.execute("SELECT max(version) FROM schema_migrations").fetchone()
    except sqlite3.Error as exc:
        conn.close()
        raise StoreUnavailableError(f"{SQLITE_FILE} is not initialised: {exc}") from exc
    if version != _expected_schema_version():
        conn.close()
        raise StoreUnavailableError(
            f"{SQLITE_FILE} is at schema v{version}, this build needs v{_expected_schema_version()}"
        )
    conn.row_factory = sqlite3.Row
    return conn


_savepoints = count()


@contextmanager
def atomic(conn: sqlite3.Connection) -> Iterator[None]:
    """One write transaction; inside another one it becomes a savepoint."""
    if conn.in_transaction:
        name = f"sp_{next(_savepoints)}"
        conn.execute(f"SAVEPOINT {name}")
        try:
            yield
        except BaseException:
            conn.execute(f"ROLLBACK TO {name}")
            conn.execute(f"RELEASE {name}")
            raise
        conn.execute(f"RELEASE {name}")
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


# ---- generic record access ---------------------------------------------------------------------
# Table and column names below come from the record classes, never from input.


def _db_value(name: str, value: object) -> object:
    if name.endswith("_json"):
        return None if value is None else orjson.dumps(value, option=orjson.OPT_SORT_KEYS).decode()
    if isinstance(value, datetime):
        return format_ts(value)
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, bool):
        return int(value)
    return value


def _columns(model: type[Record], alias: str = "") -> str:
    prefix = f"{alias}." if alias else ""
    return ", ".join(prefix + name for name in model.model_fields)


def from_row[ModelT: BaseModel](
    model: type[ModelT], row: Mapping[str, Any], prefix: str = ""
) -> ModelT:
    data: dict[str, object] = {}
    for name in model.model_fields:
        value = row[prefix + name]
        data[name] = orjson.loads(value) if name.endswith("_json") and value is not None else value
    return model.model_validate(data)


def insert(conn: sqlite3.Connection, *records: Record) -> None:
    for record in records:
        names = list(type(record).model_fields)
        sql = (
            f"INSERT INTO {record.TABLE} ({', '.join(names)}) "  # noqa: S608 - names from models
            f"VALUES ({', '.join('?' * len(names))})"
        )
        conn.execute(sql, [_db_value(name, getattr(record, name)) for name in names])


def update(conn: sqlite3.Connection, record: Record, *fields: str) -> int:
    """Writes `fields` of `record` back to its row. Returns how many rows changed (0 or 1)."""
    assignments = ", ".join(f"{name} = ?" for name in fields)
    cursor = conn.execute(
        f"UPDATE {record.TABLE} SET {assignments} WHERE id = ?",  # noqa: S608 - names from models
        [*(_db_value(name, getattr(record, name)) for name in fields), getattr(record, "id")],  # noqa: B009
    )
    return cursor.rowcount


def get[RecordT: Record](conn: sqlite3.Connection, model: type[RecordT], record_id: str) -> RecordT:
    row = conn.execute(
        f"SELECT {_columns(model)} FROM {model.TABLE} WHERE id = ?",  # noqa: S608
        (record_id,),
    ).fetchone()
    if row is None:
        raise NotFoundError(f"{model.TABLE} {record_id} not found")
    return from_row(model, row)


def audit(
    conn: sqlite3.Connection,
    action: AuditAction,
    entity: EntityType,
    entity_id: str,
    *,
    actor: str,
    detail: JsonObject | None = None,
) -> AuditEntry:
    entry = AuditEntry(
        id=new_id(),
        occurred_at=utc_now(),
        actor=actor,
        action=action,
        entity_type=entity,
        entity_id=entity_id,
        detail_json=detail,
    )
    insert(conn, entry)
    return entry


def _entity(model: type[Record]) -> EntityType:
    if model.ENTITY is None:
        raise TypeError(f"{model.__name__} is not an audited entity")
    return model.ENTITY


def change_status(
    conn: sqlite3.Connection,
    model: type[Record],
    record_id: str,
    target: StrEnum,
    *,
    actor: str,
    reason: str | None = None,
) -> None:
    """Moves one status column along its state machine and audits the change."""
    fields = [name for name, f in model.model_fields.items() if f.annotation is type(target)]
    if len(fields) != 1:
        raise TypeError(f"{model.__name__} has no single {type(target).__name__} column")
    field = fields[0]
    with atomic(conn):
        current = getattr(get(conn, model, record_id), field)
        if current == target:
            return
        if not can_transition(current, target):
            raise InvalidTransitionError(
                f"{model.TABLE}.{field} cannot change from '{current}' to '{target}'"
            )
        assignments, params = f"{field} = ?", [target.value]
        if "updated_at" in model.model_fields:
            assignments += ", updated_at = ?"
            params.append(format_ts(utc_now()))
        conn.execute(
            f"UPDATE {model.TABLE} SET {assignments} WHERE id = ?",  # noqa: S608
            [*params, record_id],
        )
        if model.ENTITY is not None:
            audit(
                conn,
                AuditAction.STATUS_CHANGE,
                model.ENTITY,
                record_id,
                actor=actor,
                detail={
                    "field": field,
                    "from": current.value,
                    "to": target.value,
                    "reason": reason,
                },
            )


def soft_delete(
    conn: sqlite3.Connection, model: type[Record], record_id: str, *, actor: str, reason: str
) -> None:
    """Recoverable deletion: the row stays, hidden from lists, with an audit entry."""
    if not reason.strip():
        raise ValueError("a deletion needs a reason")
    now = format_ts(utc_now())
    touch = ", updated_at = ?" if "updated_at" in model.model_fields else ""
    with atomic(conn):
        cursor = conn.execute(
            f"UPDATE {model.TABLE} SET deleted_at = ?, deleted_reason = ?{touch} "  # noqa: S608
            "WHERE id = ? AND deleted_at IS NULL",
            [now, reason, *([now] if touch else []), record_id],
        )
        if cursor.rowcount != 1:
            raise NotFoundError(f"no live {model.TABLE} row {record_id}")
        audit(
            conn,
            AuditAction.SOFT_DELETE,
            _entity(model),
            record_id,
            actor=actor,
            detail={"reason": reason},
        )


def restore(conn: sqlite3.Connection, model: type[Record], record_id: str, *, actor: str) -> None:
    now = format_ts(utc_now())
    touch = ", updated_at = ?" if "updated_at" in model.model_fields else ""
    with atomic(conn):
        cursor = conn.execute(
            f"UPDATE {model.TABLE} SET deleted_at = NULL, deleted_reason = NULL{touch} "  # noqa: S608
            "WHERE id = ? AND deleted_at IS NOT NULL",
            [*([now] if touch else []), record_id],
        )
        if cursor.rowcount != 1:
            raise NotFoundError(f"no deleted {model.TABLE} row {record_id}")
        audit(conn, AuditAction.RESTORE, _entity(model), record_id, actor=actor)


# ---- cursors and search ------------------------------------------------------------------------

_UUID = re.compile(UUID_PATTERN)
_WORD = re.compile(r"\w+")


def encode_cursor(tag: str, key: str, last_id: str) -> str:
    return base64.urlsafe_b64encode(orjson.dumps([tag, key, last_id])).rstrip(b"=").decode()


def decode_cursor(cursor: str, tag: str) -> tuple[str, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        decoded = orjson.loads(raw)
    except (binascii.Error, ValueError) as exc:
        raise InvalidCursorError("the cursor is not valid") from exc
    match decoded:
        case [str(found), str(key), str(last_id)] if found == tag and _UUID.match(last_id):
            return key, last_id
    raise InvalidCursorError(f"the cursor does not belong to a '{tag}' listing")


def fts_query(search: str | None) -> str | None:
    """Prefix match on every word; quoting each token keeps FTS syntax out of user input."""
    tokens = _WORD.findall(search or "")[:8]
    return " ".join(f'"{token}"*' for token in tokens) or None


# ---- businesses --------------------------------------------------------------------------------

_BUSINESS_SORT: dict[BusinessSort, tuple[str, str]] = {
    BusinessSort.NAME: ("b.name_sort_key", "ASC"),
    BusinessSort.LAST_SEEN: ("b.last_seen_at", "DESC"),
}

# One summary row per canonical business; also the source of dataset snapshots.
BUSINESS_SUMMARY_SQL = """
SELECT b.id, b.project_id, b.display_name, b.status, b.lead_stage,
    l.locality, l.region, l.postal_code, w.domain AS primary_domain,
    coalesce(c.value_normalized, c.value_raw) AS primary_phone,
    cl.label_raw AS primary_category,
    (SELECT count(DISTINCT x.raw_record_id) FROM business_names n
        JOIN extractions x ON x.id = n.extraction_id WHERE n.business_id = b.id) AS source_count,
    (SELECT count(*) FROM evidence_items e
        WHERE e.business_id = b.id AND e.status = 'active') AS evidence_count,
    b.first_seen_at, b.last_seen_at, p.is_synthetic, {key} AS sort_value
FROM businesses b
JOIN projects p ON p.id = b.project_id
LEFT JOIN business_locations l ON l.business_id = b.id AND l.is_primary = 1
LEFT JOIN websites w ON w.business_id = b.id AND w.is_primary = 1
LEFT JOIN contact_points c ON c.business_id = b.id AND c.contact_type = 'phone'
    AND c.is_primary = 1 AND c.deleted_at IS NULL
LEFT JOIN business_classifications cl ON cl.business_id = b.id AND cl.kind = 'category'
    AND cl.is_primary = 1
"""


def list_businesses(conn: sqlite3.Connection, query: BusinessListQuery) -> BusinessPage:
    where, params = ["b.deleted_at IS NULL", "p.deleted_at IS NULL"], list[object]()
    if query.project_id is not None:
        where.append("b.project_id = ?")
        params.append(query.project_id)
    if query.status is not None:
        where.append("b.status = ?")
        params.append(query.status.value)
    if (match := fts_query(query.search)) is not None:
        where.append("b.seq IN (SELECT rowid FROM business_search WHERE business_search MATCH ?)")
        params.append(match)
    (total,) = conn.execute(
        "SELECT count(*) FROM businesses b JOIN projects p ON p.id = b.project_id "  # noqa: S608
        f"WHERE {' AND '.join(where)}",
        params,
    ).fetchone()

    key, direction = _BUSINESS_SORT[query.sort]
    if query.cursor is not None:
        last_key, last_id = decode_cursor(query.cursor, query.sort.value)
        where.append(f"({key}, b.id) {'>' if direction == 'ASC' else '<'} (?, ?)")
        params += [last_key, last_id]
    rows = conn.execute(
        BUSINESS_SUMMARY_SQL.format(key=key)
        + f"WHERE {' AND '.join(where)} ORDER BY {key} {direction}, b.id {direction} LIMIT ?",
        [*params, query.limit + 1],
    ).fetchall()
    page = rows[: query.limit]
    next_cursor = (
        encode_cursor(query.sort.value, page[-1]["sort_value"], page[-1]["id"])
        if len(rows) > query.limit
        else None
    )
    items = [
        BusinessListItem.model_validate({k: row[k] for k in BusinessListItem.model_fields})
        for row in page
    ]
    return BusinessPage(items=items, next_cursor=next_cursor, total=total)


_PROVENANCE = """
    x.id AS p_extraction_id, x.extractor AS p_extractor,
    x.extractor_version AS p_extractor_version, x.extracted_at AS p_extracted_at,
    r.id AS p_raw_record_id, r.source_run_id AS p_source_run_id,
    r.acquired_page_id AS p_acquired_page_id, sc.key AS p_connector_key,
    sc.name AS p_connector_name, sc.kind AS p_connector_kind, r.method AS p_method,
    r.source_url AS p_source_url, r.external_id AS p_external_id, r.acquired_at AS p_acquired_at
"""


def _observed[ObservedT: Observed[Any]](
    conn: sqlite3.Connection,
    model: type[Record],
    wrapper: type[ObservedT],
    business_id: str,
    live_only: bool = False,
) -> list[ObservedT]:
    rows = conn.execute(
        f"SELECT {_columns(model, 'f')}, {_PROVENANCE} FROM {model.TABLE} f "  # noqa: S608
        "JOIN extractions x ON x.id = f.extraction_id "
        "JOIN raw_records r ON r.id = x.raw_record_id "
        "JOIN source_connectors sc ON sc.id = r.connector_id "
        f"WHERE f.business_id = ?{' AND f.deleted_at IS NULL' if live_only else ''} "
        "ORDER BY f.confidence DESC, f.id",
        (business_id,),
    ).fetchall()
    return [
        wrapper(value=from_row(model, row), provenance=from_row(Provenance, row, prefix="p_"))
        for row in rows
    ]


def get_business_detail(conn: sqlite3.Connection, business_id: str) -> BusinessDetail:
    row = conn.execute(
        f"SELECT {_columns(Business, 'b')}, p.is_synthetic FROM businesses b "  # noqa: S608
        "JOIN projects p ON p.id = b.project_id "
        "WHERE b.id = ? AND b.deleted_at IS NULL AND p.deleted_at IS NULL",
        (business_id,),
    ).fetchone()
    if row is None:
        raise NotFoundError(f"business {business_id} not found")
    return BusinessDetail(
        business=from_row(Business, row),
        is_synthetic=bool(row["is_synthetic"]),
        names=_observed(conn, BusinessName, ObservedName, business_id),
        locations=_observed(conn, BusinessLocation, ObservedLocation, business_id),
        websites=_observed(conn, Website, ObservedWebsite, business_id),
        contacts=_observed(conn, ContactPoint, ObservedContact, business_id, live_only=True),
        classifications=_observed(
            conn, BusinessClassification, ObservedClassification, business_id
        ),
        evidence=_observed(conn, EvidenceItem, ObservedEvidence, business_id),
    )


@dataclass(frozen=True, slots=True)
class Lineage:
    """The chain behind one observed value, back to the connector that acquired it."""

    extraction: Extraction
    raw_record: RawRecord
    acquired_page: AcquiredPage | None
    source_run: SourceRun
    connector: SourceConnector


def trace_lineage(conn: sqlite3.Connection, extraction_id: str) -> Lineage:
    extraction = get(conn, Extraction, extraction_id)
    raw = get(conn, RawRecord, extraction.raw_record_id)
    return Lineage(
        extraction=extraction,
        raw_record=raw,
        acquired_page=(
            get(conn, AcquiredPage, raw.acquired_page_id) if raw.acquired_page_id else None
        ),
        source_run=get(conn, SourceRun, raw.source_run_id),
        connector=get(conn, SourceConnector, raw.connector_id),
    )


# ---- datasets ----------------------------------------------------------------------------------


def list_datasets(conn: sqlite3.Connection, query: DatasetListQuery) -> DatasetPage:
    where, params = ["d.deleted_at IS NULL", "p.deleted_at IS NULL"], list[object]()
    if query.project_id is not None:
        where.append("d.project_id = ?")
        params.append(query.project_id)
    (total,) = conn.execute(
        "SELECT count(*) FROM datasets d JOIN projects p ON p.id = d.project_id "  # noqa: S608
        f"WHERE {' AND '.join(where)}",
        params,
    ).fetchone()
    if query.cursor is not None:
        last_key, last_id = decode_cursor(query.cursor, "created")
        where.append("(d.created_at, d.id) < (?, ?)")
        params += [last_key, last_id]
    rows = conn.execute(
        f"SELECT {_columns(Dataset, 'd')}, p.is_synthetic, "  # noqa: S608
        "(SELECT count(*) FROM dataset_snapshots s WHERE s.dataset_id = d.id) AS snapshot_count "
        "FROM datasets d JOIN projects p ON p.id = d.project_id "
        f"WHERE {' AND '.join(where)} ORDER BY d.created_at DESC, d.id DESC LIMIT ?",
        [*params, query.limit + 1],
    ).fetchall()
    page = rows[: query.limit]
    items = []
    for row in page:
        latest = conn.execute(
            f"SELECT {_columns(DatasetSnapshot)} FROM dataset_snapshots "  # noqa: S608
            "WHERE dataset_id = ? ORDER BY version DESC LIMIT 1",
            (row["id"],),
        ).fetchone()
        items.append(
            DatasetListItem(
                dataset=from_row(Dataset, row),
                is_synthetic=bool(row["is_synthetic"]),
                snapshot_count=row["snapshot_count"],
                latest_snapshot=from_row(DatasetSnapshot, latest) if latest else None,
            )
        )
    next_cursor = (
        encode_cursor("created", page[-1]["created_at"], page[-1]["id"])
        if len(rows) > query.limit
        else None
    )
    return DatasetPage(items=items, next_cursor=next_cursor, total=total)


# ---- discovery: campaigns, runs and candidates (phase 04) --------------------------------------

DEFAULT_PROJECT_NAME = "Local workspace"
RUN_EVENT_LIMIT = 50


def ensure_default_project(conn: sqlite3.Connection, *, actor: str = ACTOR_SYSTEM) -> Project:
    """The workspace a campaign belongs to when the operator has not made one yet.

    Created on first use, never synthetic, and audited like any other project. The synthetic seed
    keeps its own separate project, so test data can never end up in a real campaign.
    """
    row = conn.execute(
        f"SELECT {_columns(Project)} FROM projects "  # noqa: S608 - names from the model
        "WHERE is_synthetic = 0 AND deleted_at IS NULL ORDER BY created_at, id LIMIT 1"
    ).fetchone()
    if row is not None:
        return from_row(Project, row)
    now = utc_now()
    project = Project(
        id=new_id(),
        name=DEFAULT_PROJECT_NAME,
        description="Created automatically for the first campaign of this installation.",
        is_synthetic=False,
        created_at=now,
        updated_at=now,
        deleted_at=None,
        deleted_reason=None,
    )
    with atomic(conn):
        insert(conn, project)
        audit(conn, AuditAction.CREATE, EntityType.PROJECT, project.id, actor=actor)
    return project


def save_campaign(conn: sqlite3.Connection, request: CampaignSaveRequest, *, actor: str) -> str:
    """Creates or updates one campaign and returns its id. The criteria are stored verbatim."""
    now = utc_now()
    criteria = request.criteria.model_dump(mode="json")
    with atomic(conn):
        if request.campaign_id is None:
            project_id = request.project_id or ensure_default_project(conn, actor=actor).id
            campaign = Campaign(
                id=new_id(),
                project_id=project_id,
                icp_profile_version_id=None,
                name=request.name,
                objective=request.objective,
                criteria_json=criteria,
                criteria_schema_version=request.criteria.schema_version,
                created_at=now,
                updated_at=now,
                deleted_at=None,
                deleted_reason=None,
            )
            insert(conn, campaign)
            audit(conn, AuditAction.CREATE, EntityType.CAMPAIGN, campaign.id, actor=actor)
            return campaign.id
        current = get(conn, Campaign, request.campaign_id)
        if current.deleted_at is not None:
            raise NotFoundError(f"campaign {request.campaign_id} has been deleted")
        conn.execute(
            "UPDATE campaigns SET name = ?, objective = ?, criteria_json = ?, "
            "criteria_schema_version = ?, updated_at = ? WHERE id = ?",
            (
                request.name,
                request.objective,
                _db_value("criteria_json", criteria),
                request.criteria.schema_version,
                format_ts(now),
                current.id,
            ),
        )
        audit(
            conn,
            AuditAction.UPDATE,
            EntityType.CAMPAIGN,
            current.id,
            actor=actor,
            detail={"name": request.name},
        )
        return current.id


_CAMPAIGN_SUMMARY_SQL = """
SELECT c.id, c.project_id, c.name, c.objective, c.criteria_json, c.created_at, c.updated_at,
    p.is_synthetic,
    (SELECT count(*) FROM jobs j WHERE j.project_id = c.project_id
        AND json_extract(j.params_json, '$.campaign_id') = c.id) AS run_count,
    (SELECT count(*) FROM discovery_candidates dc
        WHERE dc.campaign_id = c.id AND dc.duplicate_of_id IS NULL) AS candidate_count,
    (SELECT j.requested_at FROM jobs j WHERE json_extract(j.params_json, '$.campaign_id') = c.id
        ORDER BY j.requested_at DESC, j.id DESC LIMIT 1) AS last_run_at,
    (SELECT j.status FROM jobs j WHERE json_extract(j.params_json, '$.campaign_id') = c.id
        ORDER BY j.requested_at DESC, j.id DESC LIMIT 1) AS last_run_status
FROM campaigns c
JOIN projects p ON p.id = c.project_id
"""


def _campaign_item(row: Mapping[str, Any]) -> CampaignListItem:
    return CampaignListItem(
        id=row["id"],
        project_id=row["project_id"],
        name=row["name"],
        objective=row["objective"],
        criteria=DiscoveryCriteria.model_validate(orjson.loads(row["criteria_json"])),
        run_count=row["run_count"],
        candidate_count=row["candidate_count"],
        last_run_at=row["last_run_at"],
        last_run_status=row["last_run_status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        is_synthetic=bool(row["is_synthetic"]),
    )


def get_campaign_item(conn: sqlite3.Connection, campaign_id: str) -> CampaignListItem:
    row = conn.execute(
        f"{_CAMPAIGN_SUMMARY_SQL} WHERE c.id = ? AND c.deleted_at IS NULL", (campaign_id,)
    ).fetchone()
    if row is None:
        raise NotFoundError(f"campaign {campaign_id} not found")
    return _campaign_item(row)


def list_campaigns(conn: sqlite3.Connection, query: CampaignListQuery) -> CampaignPage:
    where, params = ["c.deleted_at IS NULL", "p.deleted_at IS NULL"], list[object]()
    if query.project_id is not None:
        where.append("c.project_id = ?")
        params.append(query.project_id)
    (total,) = conn.execute(
        "SELECT count(*) FROM campaigns c JOIN projects p ON p.id = c.project_id "  # noqa: S608
        f"WHERE {' AND '.join(where)}",
        params,
    ).fetchone()
    if query.cursor is not None:
        last_key, last_id = decode_cursor(query.cursor, "campaign")
        where.append("(c.created_at, c.id) < (?, ?)")
        params += [last_key, last_id]
    rows = conn.execute(
        f"{_CAMPAIGN_SUMMARY_SQL} WHERE {' AND '.join(where)} "
        "ORDER BY c.created_at DESC, c.id DESC LIMIT ?",
        [*params, query.limit + 1],
    ).fetchall()
    page = rows[: query.limit]
    next_cursor = (
        encode_cursor("campaign", page[-1]["created_at"], page[-1]["id"])
        if len(rows) > query.limit
        else None
    )
    return CampaignPage(
        items=[_campaign_item(row) for row in page], next_cursor=next_cursor, total=total
    )


# ---- runs ---------------------------------------------------------------------------------------


def source_runs_of_job(conn: sqlite3.Connection, job_id: str) -> list[SourceRun]:
    rows = conn.execute(
        f"SELECT {_columns(SourceRun)} FROM source_runs WHERE job_id = ? "  # noqa: S608
        "ORDER BY created_at, id",
        (job_id,),
    ).fetchall()
    return [from_row(SourceRun, row) for row in rows]


def last_source_run(
    conn: sqlite3.Connection, connector_id: str, project_id: str | None
) -> SourceRun | None:
    where, params = ["connector_id = ?"], [connector_id]
    if project_id is not None:
        where.append("project_id = ?")
        params.append(project_id)
    row = conn.execute(
        f"SELECT {_columns(SourceRun)} FROM source_runs "  # noqa: S608
        f"WHERE {' AND '.join(where)} ORDER BY created_at DESC, id DESC LIMIT 1",
        params,
    ).fetchone()
    return from_row(SourceRun, row) if row else None


def task_of(conn: sqlite3.Connection, job_id: str, task_key: str) -> JobTask:
    row = conn.execute(
        f"SELECT {_columns(JobTask)} FROM job_tasks WHERE job_id = ? AND task_key = ?",  # noqa: S608
        (job_id, task_key),
    ).fetchone()
    if row is None:
        raise NotFoundError(f"job {job_id} has no task '{task_key}'")
    return from_row(JobTask, row)


def latest_checkpoint(conn: sqlite3.Connection, task_id: str) -> JsonObject | None:
    """The newest checkpoint of one task: its cursor and its counters."""
    row = conn.execute(
        "SELECT state_json FROM job_checkpoints WHERE task_id = ? ORDER BY created_at DESC, id DESC"
        " LIMIT 1",
        (task_id,),
    ).fetchone()
    if row is None:
        return None
    state = orjson.loads(row["state_json"])
    return state if isinstance(state, dict) else None


def raw_record_exists(conn: sqlite3.Connection, source_run_id: str, payload_sha256: str) -> bool:
    """True when this run already stored this exact payload: the same page twice, or a repeat."""
    row = conn.execute(
        "SELECT 1 FROM raw_records WHERE source_run_id = ? AND payload_sha256 = ?",
        (source_run_id, payload_sha256),
    ).fetchone()
    return row is not None


def candidate_with_key(conn: sqlite3.Connection, campaign_id: str, dedup_key: str) -> str | None:
    """The first candidate of this campaign with that key, if any. Never crosses campaigns."""
    row = conn.execute(
        "SELECT id FROM discovery_candidates WHERE campaign_id = ? AND dedup_key = ? "
        "AND duplicate_of_id IS NULL ORDER BY created_at, id LIMIT 1",
        (campaign_id, dedup_key),
    ).fetchone()
    return row["id"] if row else None


def requests_today(conn: sqlite3.Connection, connector_id: str) -> int:
    """Requests this connector has issued since midnight UTC, for its daily budget."""
    (count,) = conn.execute(
        "SELECT count(*) FROM source_queries q JOIN source_runs r ON r.id = q.source_run_id "
        "WHERE r.connector_id = ? AND q.issued_at >= ?",
        (connector_id, format_ts(utc_now())[:10] + "T00:00:00.000Z"),
    ).fetchone()
    return int(count)


def candidate_count_for_job(conn: sqlite3.Connection, job_id: str) -> int:
    (count,) = conn.execute(
        "SELECT count(*) FROM discovery_candidates c JOIN source_runs r ON r.id = c.source_run_id "
        "WHERE r.job_id = ? AND c.duplicate_of_id IS NULL",
        (job_id,),
    ).fetchone()
    return int(count)


def _counter(value: object) -> int:
    """A counter read back from a checkpoint, which is JSON and so could hold anything."""
    return value if isinstance(value, int) and value >= 0 else 0


def _source_run_views(conn: sqlite3.Connection, job_id: str) -> list[DiscoverySourceRun]:
    views: list[DiscoverySourceRun] = []
    for run in source_runs_of_job(conn, job_id):
        connector = get(conn, SourceConnector, run.connector_id)
        try:
            state = latest_checkpoint(conn, task_of(conn, job_id, connector.key).id) or {}
        except NotFoundError:
            state = {}
        counts = conn.execute(
            "SELECT count(*) AS total, "
            "sum(CASE WHEN duplicate_of_id IS NULL THEN 1 ELSE 0 END) AS unique_count "
            "FROM discovery_candidates WHERE source_run_id = ?",
            (run.id,),
        ).fetchone()
        (requests,) = conn.execute(
            "SELECT count(*) FROM source_queries WHERE source_run_id = ?", (run.id,)
        ).fetchone()
        unique = int(counts["unique_count"] or 0)
        cursor = state.get("cursor")
        views.append(
            DiscoverySourceRun(
                source_run_id=run.id,
                connector_key=connector.key,
                connector_name=connector.name,
                connector_kind=connector.kind,
                status=run.status,
                pages_done=_counter(state.get("pages_done")),
                records_seen=_counter(state.get("records_seen")),
                candidate_count=unique,
                duplicate_count=int(counts["total"] or 0) - unique,
                request_count=int(requests),
                cursor=cursor if isinstance(cursor, str) else None,
                error_code=run.error_code,
                error_message=run.error_message,
                started_at=run.started_at,
                finished_at=run.finished_at,
            )
        )
    return views


def _run_events(conn: sqlite3.Connection, job_id: str) -> list[RunEvent]:
    rows = conn.execute(
        "SELECT level, event, error_code, retryable, detail_json, created_at FROM job_events "
        "WHERE job_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
        (job_id, RUN_EVENT_LIMIT),
    ).fetchall()
    return [from_row(RunEvent, row) for row in rows]


def _discovery_run(conn: sqlite3.Connection, job: Job) -> DiscoveryRun:
    campaign_id = str(job.params_json.get("campaign_id") or "")
    campaign = get(conn, Campaign, campaign_id)
    sources = _source_run_views(conn, job.id)
    (errors,) = conn.execute(
        "SELECT count(*) FROM job_events WHERE job_id = ? AND level = 'error'", (job.id,)
    ).fetchone()
    return DiscoveryRun(
        job_id=job.id,
        project_id=job.project_id,
        campaign_id=campaign.id,
        campaign_name=campaign.name,
        status=job.status,
        progress_done=job.progress_done,
        progress_total=job.progress_total,
        candidate_count=sum(source.candidate_count for source in sources),
        duplicate_count=sum(source.duplicate_count for source in sources),
        request_count=sum(source.request_count for source in sources),
        error_count=int(errors),
        requested_at=job.requested_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        sources=sources,
        events=_run_events(conn, job.id),
    )


def get_discovery_run(conn: sqlite3.Connection, job_id: str) -> DiscoveryRun:
    job = get(conn, Job, job_id)
    if job.kind != DISCOVERY_JOB_KIND:
        raise NotFoundError(f"job {job_id} is not a discovery run")
    return _discovery_run(conn, job)


def list_discovery_runs(conn: sqlite3.Connection, query: DiscoveryRunListQuery) -> DiscoveryRunPage:
    where, params = ["j.kind = ?"], [DISCOVERY_JOB_KIND]
    if query.project_id is not None:
        where.append("j.project_id = ?")
        params.append(query.project_id)
    if query.campaign_id is not None:
        where.append("json_extract(j.params_json, '$.campaign_id') = ?")
        params.append(query.campaign_id)
    (total,) = conn.execute(
        f"SELECT count(*) FROM jobs j WHERE {' AND '.join(where)}",  # noqa: S608
        params,
    ).fetchone()
    if query.cursor is not None:
        last_key, last_id = decode_cursor(query.cursor, "run")
        where.append("(j.requested_at, j.id) < (?, ?)")
        params += [last_key, last_id]
    rows = conn.execute(
        f"SELECT {_columns(Job, 'j')} FROM jobs j WHERE {' AND '.join(where)} "  # noqa: S608
        "ORDER BY j.requested_at DESC, j.id DESC LIMIT ?",
        [*params, query.limit + 1],
    ).fetchall()
    page = rows[: query.limit]
    next_cursor = (
        encode_cursor("run", page[-1]["requested_at"], page[-1]["id"])
        if len(rows) > query.limit
        else None
    )
    return DiscoveryRunPage(
        items=[_discovery_run(conn, from_row(Job, row)) for row in page],
        next_cursor=next_cursor,
        total=total,
    )


# ---- candidates ---------------------------------------------------------------------------------

_CANDIDATE_SQL = """
SELECT dc.id, dc.campaign_id, cp.name AS campaign_name, dc.source_run_id, sc.key AS connector_key,
    sc.name AS connector_name, sc.kind AS connector_kind, dc.name_raw, dc.locality, dc.region,
    dc.postal_code, dc.domain, dc.phone_raw, dc.category_raw, dc.source_url, dc.external_id,
    dc.duplicate_of_id, dc.discovered_at
FROM discovery_candidates dc
JOIN source_connectors sc ON sc.id = dc.connector_id
LEFT JOIN campaigns cp ON cp.id = dc.campaign_id
"""


def _candidate_item(row: Mapping[str, Any]) -> CandidateListItem:
    return CandidateListItem(
        id=row["id"],
        campaign_id=row["campaign_id"],
        campaign_name=row["campaign_name"],
        source_run_id=row["source_run_id"],
        connector_key=row["connector_key"],
        connector_name=row["connector_name"],
        connector_kind=row["connector_kind"],
        name_raw=row["name_raw"],
        locality=row["locality"],
        region=row["region"],
        postal_code=row["postal_code"],
        domain=row["domain"],
        phone_raw=row["phone_raw"],
        category_raw=row["category_raw"],
        source_url=row["source_url"],
        external_id=row["external_id"],
        is_duplicate=row["duplicate_of_id"] is not None,
        duplicate_of_id=row["duplicate_of_id"],
        discovered_at=row["discovered_at"],
    )


def list_candidates(conn: sqlite3.Connection, query: CandidateListQuery) -> CandidatePage:
    where, params = ["1 = 1"], list[object]()
    if query.project_id is not None:
        where.append("dc.project_id = ?")
        params.append(query.project_id)
    if query.campaign_id is not None:
        where.append("dc.campaign_id = ?")
        params.append(query.campaign_id)
    if query.source_run_id is not None:
        where.append("dc.source_run_id = ?")
        params.append(query.source_run_id)
    if not query.include_duplicates:
        where.append("dc.duplicate_of_id IS NULL")
    if search := sort_key(query.search or ""):
        # ponytail: substring match on the tidied name; add an FTS index if candidate lists grow
        for character in ("\\", "%", "_"):
            search = search.replace(character, "\\" + character)
        where.append("dc.name_normalized LIKE ? ESCAPE '\\'")
        params.append(f"%{search}%")
    (total,) = conn.execute(
        f"SELECT count(*) FROM discovery_candidates dc WHERE {' AND '.join(where)}",  # noqa: S608
        params,
    ).fetchone()
    if query.cursor is not None:
        last_key, last_id = decode_cursor(query.cursor, "candidate")
        where.append("(dc.discovered_at, dc.id) < (?, ?)")
        params += [last_key, last_id]
    rows = conn.execute(
        f"{_CANDIDATE_SQL} WHERE {' AND '.join(where)} "
        "ORDER BY dc.discovered_at DESC, dc.id DESC LIMIT ?",
        [*params, query.limit + 1],
    ).fetchall()
    page = rows[: query.limit]
    next_cursor = (
        encode_cursor("candidate", page[-1]["discovered_at"], page[-1]["id"])
        if len(rows) > query.limit
        else None
    )
    return CandidatePage(
        items=[_candidate_item(row) for row in page], next_cursor=next_cursor, total=total
    )


def get_candidate_detail(conn: sqlite3.Connection, candidate_id: str) -> CandidateDetail:
    """The candidate and the whole chain behind it, down to the payload as it was received."""
    candidate = get(conn, DiscoveryCandidate, candidate_id)
    raw = get(conn, RawRecord, candidate.raw_record_id)
    page = (
        get(conn, AcquiredPage, raw.acquired_page_id) if raw.acquired_page_id is not None else None
    )
    query = None
    if page is not None and page.source_query_id is not None:
        query = get(conn, SourceQuery, page.source_query_id)
    campaign = (
        get(conn, Campaign, candidate.campaign_id) if candidate.campaign_id is not None else None
    )
    return CandidateDetail(
        candidate=candidate,
        campaign_name=campaign.name if campaign else None,
        connector=get(conn, SourceConnector, candidate.connector_id),
        source_run=get(conn, SourceRun, candidate.source_run_id),
        source_query=query,
        acquired_page=page,
        raw_record=raw,
        duplicate_of=(
            get(conn, DiscoveryCandidate, candidate.duplicate_of_id)
            if candidate.duplicate_of_id is not None
            else None
        ),
    )


# ---- web acquisition (phase 05) -----------------------------------------------------------------
# Every number below is read from `crawl_urls`, the fetch log itself: nothing is kept on the side,
# so a crawl reads the same after a restart as while it runs.

#: `jobs.kind` of a crawl; the engine and the repository must agree on it.
CRAWL_JOB_KIND = "crawl"
CRAWL_ERROR_GROUPS = 20

_CRAWL_COUNTS = """
    coalesce(sum(c.request_count), 0) AS requests,
    coalesce(sum(c.target = 'page' AND c.status = 'fetched'), 0) AS pages_fetched,
    coalesce(sum(c.target = 'page' AND c.status = 'failed'), 0) AS pages_failed,
    coalesce(sum(c.status = 'skipped'), 0) AS urls_skipped,
    coalesce(sum(c.status = 'queued'), 0) AS queued,
    coalesce(sum(CASE WHEN c.status = 'fetched' AND c.reason IS NOT 'not_modified'
        THEN c.byte_size END), 0) AS bytes,
    coalesce(sum(c.target = 'page' AND c.status = 'fetched' AND c.method = 'browser_render'), 0)
        AS browser_pages
"""


def _percentile(values: list[int], share: float) -> int | None:
    """Nearest-rank percentile of sorted values."""
    return values[min(len(values) - 1, int(share * len(values)))] if values else None


def _crawl_metrics(conn: sqlite3.Connection, job: Job) -> CrawlMetrics:
    row = conn.execute(
        f"SELECT {_CRAWL_COUNTS}, "  # noqa: S608 - fixed SQL
        "coalesce(sum(c.http_status = 304), 0) AS cache_hits, "
        "coalesce(sum(c.retry_count), 0) AS retries, "
        "coalesce(sum(c.reason = 'blocked'), 0) AS blocked, "
        "coalesce(sum(c.target = 'page' AND c.status = 'fetched' AND c.method = 'http_fetch'), 0) "
        "AS http_pages, "
        "coalesce(sum(c.fallback_reason IS NOT NULL), 0) AS browser_fallbacks, "
        "coalesce(sum(c.duplicate_of_id IS NOT NULL), 0) AS duplicates "
        "FROM crawl_urls c JOIN source_runs r ON r.id = c.source_run_id WHERE r.job_id = ?",
        (job.id,),
    ).fetchone()
    # ponytail: percentiles over every timing of the job in Python; sample them if crawls reach
    # hundreds of thousands of URLs
    latencies = [
        value
        for (value,) in conn.execute(
            "SELECT c.fetch_ms FROM crawl_urls c JOIN source_runs r ON r.id = c.source_run_id "
            "WHERE r.job_id = ? AND c.http_status IS NOT NULL AND c.fetch_ms IS NOT NULL "
            "ORDER BY c.fetch_ms",
            (job.id,),
        )
    ]
    rate = None
    if job.started_at is not None:
        minutes = ((job.finished_at or utc_now()) - job.started_at).total_seconds() / 60
        rate = round(row["pages_fetched"] / minutes, 2) if minutes > 0 else None
    return CrawlMetrics(
        **{name: row[name] for name in CrawlMetrics.model_fields if name in row.keys()},  # noqa: SIM118
        pages_per_minute=rate,
        latency_p50_ms=_percentile(latencies, 0.5),
        latency_p95_ms=_percentile(latencies, 0.95),
        latency_max_ms=latencies[-1] if latencies else None,
    )


def _crawl_run(conn: sqlite3.Connection, job: Job) -> CrawlRun:
    campaign_id = job.params_json.get("campaign_id")
    campaign = get(conn, Campaign, campaign_id) if isinstance(campaign_id, str) else None
    sites: list[CrawlSite] = []
    for row in conn.execute(
        f"SELECT r.id, r.status, r.effective_config_json, r.error_code, r.error_message, "  # noqa: S608
        f"r.started_at, r.finished_at, {_CRAWL_COUNTS} FROM source_runs r "
        "LEFT JOIN crawl_urls c ON c.source_run_id = r.id WHERE r.job_id = ? "
        "GROUP BY r.id ORDER BY r.created_at, r.id",
        (job.id,),
    ):
        config = orjson.loads(row["effective_config_json"])
        sites.append(
            CrawlSite(
                source_run_id=row["id"],
                site=config["site"],
                root_url=config["root_url"],
                candidate_id=config.get("candidate_id"),
                status=row["status"],
                pages_fetched=row["pages_fetched"],
                pages_failed=row["pages_failed"],
                urls_skipped=row["urls_skipped"],
                queued=row["queued"],
                bytes=row["bytes"],
                requests=row["requests"],
                browser_pages=row["browser_pages"],
                error_code=row["error_code"],
                error_message=row["error_message"],
                started_at=row["started_at"],
                finished_at=row["finished_at"],
            )
        )
    errors = [
        CrawlErrorGroup(reason=row[0], count=row[1], example_url=row[2])
        for row in conn.execute(
            "SELECT c.reason, count(*), min(c.url) FROM crawl_urls c "
            "JOIN source_runs r ON r.id = c.source_run_id "
            "WHERE r.job_id = ? AND c.status = 'failed' AND c.target = 'page' "
            "GROUP BY c.reason ORDER BY count(*) DESC, c.reason LIMIT ?",
            (job.id, CRAWL_ERROR_GROUPS),
        )
    ]
    done = (SourceRunStatus.SUCCEEDED, SourceRunStatus.FAILED, SourceRunStatus.CANCELLED)
    return CrawlRun(
        job_id=job.id,
        project_id=job.project_id,
        campaign_id=campaign.id if campaign else None,
        campaign_name=campaign.name if campaign else None,
        status=job.status,
        settings=CrawlSettings.model_validate(job.params_json["settings"]),
        site_count=len(sites),
        sites_done=sum(1 for site in sites if site.status in done),
        metrics=_crawl_metrics(conn, job),
        errors=errors,
        in_flight=[],
        sites=sites,
        events=_run_events(conn, job.id),
        requested_at=job.requested_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


def _crawl_job(conn: sqlite3.Connection, job_id: str) -> Job:
    job = get(conn, Job, job_id)
    if job.kind != CRAWL_JOB_KIND:
        raise NotFoundError(f"job {job_id} is not a crawl")
    return job


def get_crawl_run(conn: sqlite3.Connection, job_id: str) -> CrawlRun:
    return _crawl_run(conn, _crawl_job(conn, job_id))


def list_crawl_runs(conn: sqlite3.Connection, query: CrawlRunListQuery) -> CrawlRunPage:
    where, params = ["j.kind = ?"], [CRAWL_JOB_KIND]
    if query.project_id is not None:
        where.append("j.project_id = ?")
        params.append(query.project_id)
    if query.campaign_id is not None:
        where.append("json_extract(j.params_json, '$.campaign_id') = ?")
        params.append(query.campaign_id)
    (total,) = conn.execute(
        f"SELECT count(*) FROM jobs j WHERE {' AND '.join(where)}",  # noqa: S608
        params,
    ).fetchone()
    if query.cursor is not None:
        last_key, last_id = decode_cursor(query.cursor, "crawl")
        where.append("(j.requested_at, j.id) < (?, ?)")
        params += [last_key, last_id]
    rows = conn.execute(
        f"SELECT {_columns(Job, 'j')} FROM jobs j WHERE {' AND '.join(where)} "  # noqa: S608
        "ORDER BY j.requested_at DESC, j.id DESC LIMIT ?",
        [*params, query.limit + 1],
    ).fetchall()
    page = rows[: query.limit]
    next_cursor = (
        encode_cursor("crawl", page[-1]["requested_at"], page[-1]["id"])
        if len(rows) > query.limit
        else None
    )
    return CrawlRunPage(
        items=[_crawl_run(conn, from_row(Job, row)) for row in page],
        next_cursor=next_cursor,
        total=total,
    )


def list_crawl_urls(conn: sqlite3.Connection, query: CrawlUrlListQuery) -> CrawlUrlPage:
    """The URLs of one crawl in discovery order (ids are UUIDv7), optionally one site or status."""
    _crawl_job(conn, query.job_id)
    where, params = ["r.job_id = ?"], list[object]([query.job_id])
    if query.source_run_id is not None:
        where.append("c.source_run_id = ?")
        params.append(query.source_run_id)
    if query.status is not None:
        where.append("c.status = ?")
        params.append(query.status.value)
    base = "FROM crawl_urls c JOIN source_runs r ON r.id = c.source_run_id"
    (total,) = conn.execute(
        f"SELECT count(*) {base} WHERE {' AND '.join(where)}",
        params,
    ).fetchone()
    if query.cursor is not None:
        _, last_id = decode_cursor(query.cursor, "crawl_url")
        where.append("c.id > ?")
        params.append(last_id)
    rows = conn.execute(
        "SELECT c.id, c.source_run_id, json_extract(r.effective_config_json, '$.site') AS site, "
        "c.url, c.target, c.page_kind, c.depth, c.status, c.reason, c.method, c.http_status, "
        "c.byte_size, c.fetch_ms, c.browser_ms, c.fallback_reason, c.duplicate_of_id, "
        "json_extract(rr.payload_json, '$.title') AS title, c.updated_at "
        f"{base} LEFT JOIN raw_records rr ON rr.id = c.raw_record_id "
        f"WHERE {' AND '.join(where)} ORDER BY c.id LIMIT ?",
        [*params, query.limit + 1],
    ).fetchall()
    page = rows[: query.limit]
    next_cursor = (
        encode_cursor("crawl_url", page[-1]["id"], page[-1]["id"])
        if len(rows) > query.limit
        else None
    )
    return CrawlUrlPage(
        items=[CrawlUrlItem.model_validate(dict(row)) for row in page],
        next_cursor=next_cursor,
        total=total,
    )


def get_crawl_url_detail(conn: sqlite3.Connection, crawl_url_id: str) -> CrawlUrlDetail:
    url = get(conn, CrawlUrl, crawl_url_id)
    run = get(conn, SourceRun, url.source_run_id)
    if run.job_id is None:
        raise NotFoundError(f"crawl url {crawl_url_id} belongs to no crawl")
    return CrawlUrlDetail(
        crawl_url=url,
        job_id=run.job_id,
        site=str(run.effective_config_json.get("site")),
        source_run=run,
        acquired_page=get(conn, AcquiredPage, url.acquired_page_id)
        if url.acquired_page_id
        else None,
        raw_record=get(conn, RawRecord, url.raw_record_id) if url.raw_record_id else None,
        discovered_from=get(conn, CrawlUrl, url.discovered_from_id)
        if url.discovered_from_id
        else None,
        duplicate_of=get(conn, CrawlUrl, url.duplicate_of_id) if url.duplicate_of_id else None,
    )
