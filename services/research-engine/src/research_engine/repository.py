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
    ContactPoint,
    Dataset,
    DatasetListItem,
    DatasetListQuery,
    DatasetPage,
    DatasetSnapshot,
    EntityType,
    EvidenceItem,
    Extraction,
    JsonObject,
    Observed,
    ObservedClassification,
    ObservedContact,
    ObservedEvidence,
    ObservedLocation,
    ObservedName,
    ObservedWebsite,
    Provenance,
    RawRecord,
    Record,
    SourceConnector,
    SourceRun,
    Website,
    can_transition,
    format_ts,
    new_id,
    utc_now,
)
from research_engine.storage import SQLITE_FILE, bundled_migrations, open_sqlite

ACTOR_SYSTEM = "system"


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
