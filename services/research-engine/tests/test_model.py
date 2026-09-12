"""The SQL schema and the Pydantic data contract describe the same model, and the database enforces
its rules on its own (enumerations, foreign keys, timestamps, state machines, immutability)."""

import sqlite3
import types
import typing
import uuid
from collections.abc import Iterator
from contextlib import closing
from datetime import UTC, datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

import pytest
from pydantic import AwareDatetime, ValidationError

from research_engine import repository
from research_engine.domain import (
    ENUMS,
    RECORDS,
    TRANSITIONS,
    AcquiredPage,
    Business,
    BusinessStatus,
    EntityType,
    EvidenceItem,
    Note,
    Record,
    ValidationStatus,
    can_transition,
    format_ts,
    new_id,
    sort_key,
    utc_now,
)
from research_engine.storage import SQLITE_FILE, bundled_migrations, check_sqlite
from research_engine.testdata import SyntheticContext, synthetic_business

_SQL_TYPES: dict[object, str] = {
    str: "TEXT",
    AwareDatetime: "TEXT",
    bool: "INTEGER",
    int: "INTEGER",
    float: "REAL",
}


def _column(name: str, annotation: Any) -> tuple[str, bool]:
    """(SQL type, nullable) a record field must have in its table."""
    nullable = False
    if typing.get_origin(annotation) in (typing.Union, types.UnionType):
        args = typing.get_args(annotation)
        nullable = type(None) in args
        (annotation,) = [arg for arg in args if arg is not type(None)]
    while typing.get_origin(annotation) is typing.Annotated:
        annotation = typing.get_args(annotation)[0]
    if name.endswith("_json"):
        # JsonValue itself admits null; objects are nullable only when declared optional
        return "TEXT", nullable or typing.get_origin(annotation) is not dict
    if isinstance(annotation, type) and issubclass(annotation, StrEnum):
        return "TEXT", nullable
    return _SQL_TYPES[annotation], nullable


def _enum_table(annotation: Any) -> str | None:
    for arg in (annotation, *typing.get_args(annotation)):
        if isinstance(arg, type) and issubclass(arg, StrEnum):
            return next(table for table, enum in ENUMS.items() if enum is arg)
    return None


def _business(ctx: SyntheticContext, index: int = 0) -> list[Record]:
    return synthetic_business(ctx, index)


@pytest.fixture(scope="module")
def schema(tmp_path_factory: pytest.TempPathFactory) -> Iterator[sqlite3.Connection]:
    """One migrated store shared by the read-only schema checks."""
    data_dir = tmp_path_factory.mktemp("schema")
    assert check_sqlite(data_dir).state == "ok"
    connection = repository.connect(data_dir)
    yield connection
    connection.close()


# ---- schema and contract agree -----------------------------------------------------------------


def test_migrating_an_empty_database_creates_every_table(data_dir: Path) -> None:
    with closing(sqlite3.connect(data_dir / SQLITE_FILE)) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        (version,) = db.execute("SELECT max(version) FROM schema_migrations").fetchone()
        contract = db.execute(
            "SELECT value FROM store_metadata WHERE key = 'data_contract_version'"
        ).fetchone()
    assert version == len(bundled_migrations("sqlite"))
    assert {record.TABLE for record in RECORDS} <= tables
    assert set(ENUMS) <= tables
    assert {"status_transitions", "business_search", "schema_migrations"} <= tables
    assert contract == ("1",)


@pytest.mark.parametrize("record", RECORDS, ids=lambda record: record.TABLE)
def test_every_record_matches_its_table_column_by_column(
    schema: sqlite3.Connection, record: type[Record]
) -> None:
    conn = schema
    columns = {
        row["name"]: (row["type"], not row["notnull"])
        for row in conn.execute(f"PRAGMA table_info({record.TABLE})")
        if row["name"] != "seq"  # rowid alias of businesses, storage-only
    }
    expected = {name: _column(name, f.annotation) for name, f in record.model_fields.items()}
    assert columns == expected


@pytest.mark.parametrize("record", RECORDS, ids=lambda record: record.TABLE)
def test_every_enum_column_references_its_lookup_table(
    schema: sqlite3.Connection, record: type[Record]
) -> None:
    conn = schema
    references = {
        row["from"]: row["table"]
        for row in conn.execute(f"PRAGMA foreign_key_list({record.TABLE})")
    }
    for name, field in record.model_fields.items():
        if (table := _enum_table(field.annotation)) is not None:
            assert references.get(name) == table, f"{record.TABLE}.{name}"


def test_lookup_tables_hold_exactly_the_enum_values(schema: sqlite3.Connection) -> None:
    conn = schema
    for table, enum in ENUMS.items():
        values = {row[0] for row in conn.execute(f"SELECT value FROM {table}")}  # noqa: S608
        assert values == {member.value for member in enum}, table


def test_state_machines_match_the_transition_table(schema: sqlite3.Connection) -> None:
    conn = schema
    stored = set(conn.execute("SELECT enum_name, from_value, to_value FROM status_transitions"))
    expected = {
        (next(t for t, e in ENUMS.items() if e is enum), source, target)
        for enum, machine in TRANSITIONS.items()
        for source, targets in machine.items()
        for target in targets
    }
    assert {tuple(row) for row in stored} == expected
    for enum, machine in TRANSITIONS.items():
        assert set(machine) == {member.value for member in enum}, enum.__name__


@pytest.mark.parametrize("record", RECORDS, ids=lambda record: record.TABLE)
def test_every_status_column_is_guarded_by_a_trigger(
    schema: sqlite3.Connection, record: type[Record]
) -> None:
    conn = schema
    triggers = [
        " ".join(row[0].split())
        for row in conn.execute("SELECT sql FROM sqlite_master WHERE type='trigger'")
    ]
    if any(f"BEFORE UPDATE ON {record.TABLE} BEGIN" in sql for sql in triggers):
        return  # the whole row is immutable, so its status can never change
    for name, field in record.model_fields.items():
        if field.annotation in TRANSITIONS:
            enum_table = _enum_table(field.annotation)
            assert any(
                f"UPDATE OF {name} ON {record.TABLE}" in sql and f"'{enum_table}'" in sql
                for sql in triggers
            ), f"{record.TABLE}.{name}"


# ---- the database enforces the rules -----------------------------------------------------------


def _insert_business(conn: sqlite3.Connection, ctx: SyntheticContext) -> Business:
    records = _business(ctx)
    repository.insert(conn, *records)
    return next(r for r in records if isinstance(r, Business))


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("status", "bogus"),  # unknown enumeration value
        ("project_id", str(uuid.uuid4())),  # dangling foreign key
        ("created_at", "2026-01-01 00:00:00"),  # not the contract timestamp format
        ("created_at", "2026-02-30T00:00:00.000Z"),  # not a real date
        ("id", "short"),
        ("display_name", "   "),
    ],
)
def test_constraints_reject_invalid_rows(
    conn: sqlite3.Connection, synthetic: SyntheticContext, column: str, value: str
) -> None:
    # the repository's own value conversion, but bypassing Pydantic on purpose
    business = next(r for r in _business(synthetic) if isinstance(r, Business))
    values = {
        name: repository._db_value(name, getattr(business, name)) for name in Business.model_fields
    }
    values[column] = value
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            f"INSERT INTO businesses ({', '.join(values)}) VALUES ({', '.join('?' * len(values))})",  # noqa: S608
            list(values.values()),
        )


def test_confidence_json_and_paths_are_checked(
    conn: sqlite3.Connection, synthetic: SyntheticContext
) -> None:
    business = _insert_business(conn, synthetic)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE business_names SET confidence = 1.5 WHERE business_id = ?", (business.id,)
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE source_runs SET effective_config_json = '{broken' WHERE id = ?",
            (synthetic.source_run_id,),
        )
    now = format_ts(utc_now())
    for path in ("../outside.html", "C:/absolute.html", "/root.html", "a\\b.html", "x/../../y"):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO acquired_pages (id, source_run_id, url, artifact_path, fetched_at) "
                "VALUES (?, ?, 'https://example.com', ?, ?)",
                (new_id(), synthetic.source_run_id, path, now),
            )
    conn.execute(
        "INSERT INTO acquired_pages (id, source_run_id, url, artifact_path, fetched_at) "
        "VALUES (?, ?, 'https://example.com', 'artifacts/pages/home.html', ?)",
        (new_id(), synthetic.source_run_id, now),
    )


def test_raw_records_and_extractions_are_immutable(
    conn: sqlite3.Connection, synthetic: SyntheticContext
) -> None:
    _insert_business(conn, synthetic)
    for statement in (
        "UPDATE raw_records SET external_id = 'changed'",
        "DELETE FROM raw_records",
        "UPDATE extractions SET extractor_version = '9'",
        "DELETE FROM extractions",
    ):
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(statement)


def test_evidence_only_changes_its_status_along_the_machine(
    conn: sqlite3.Connection, synthetic: SyntheticContext
) -> None:
    _insert_business(conn, synthetic)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("UPDATE evidence_items SET claim = 'rewritten'")
    conn.execute("UPDATE evidence_items SET status = 'revoked'")
    with pytest.raises(sqlite3.IntegrityError, match="evidence_status"):
        conn.execute("UPDATE evidence_items SET status = 'active'")  # revoked is final


def test_invalid_status_transitions_are_refused_by_the_database(
    conn: sqlite3.Connection, synthetic: SyntheticContext
) -> None:
    business = _insert_business(conn, synthetic)
    with pytest.raises(sqlite3.IntegrityError, match="lead_stage"):
        conn.execute("UPDATE businesses SET lead_stage = 'won' WHERE id = ?", (business.id,))
    with pytest.raises(sqlite3.IntegrityError):  # merged needs merged_into_id
        conn.execute("UPDATE businesses SET status = 'merged' WHERE id = ?", (business.id,))
    conn.execute("UPDATE businesses SET status = 'active' WHERE id = ?", (business.id,))
    with pytest.raises(sqlite3.IntegrityError, match="source_run_status"):
        conn.execute(
            "UPDATE source_runs SET status = 'running' WHERE id = ?", (synthetic.source_run_id,)
        )
    conn.execute("UPDATE contact_points SET validation_status = 'suppressed'")
    with pytest.raises(sqlite3.IntegrityError, match="validation_status"):
        conn.execute("UPDATE contact_points SET validation_status = 'likely_valid'")
    conn.execute("UPDATE contact_points SET validation_status = 'observed'")


def test_live_rows_cannot_be_hard_deleted(
    conn: sqlite3.Connection, synthetic: SyntheticContext
) -> None:
    business = _insert_business(conn, synthetic)
    with pytest.raises(sqlite3.IntegrityError, match="soft-delete"):
        conn.execute("DELETE FROM businesses WHERE id = ?", (business.id,))
    now = utc_now()
    note = Note(
        id=new_id(),
        project_id=synthetic.project_id,
        entity_type=EntityType.BUSINESS,
        entity_id=business.id,
        body="synthetic note",
        author="test",
        created_at=now,
        updated_at=now,
        deleted_at=None,
        deleted_reason=None,
    )
    repository.insert(conn, note)
    with pytest.raises(sqlite3.IntegrityError, match="soft-delete"):
        conn.execute("DELETE FROM notes WHERE id = ?", (note.id,))
    repository.soft_delete(conn, Note, note.id, actor="test", reason="purge test")
    conn.execute("DELETE FROM notes WHERE id = ?", (note.id,))  # purge after the audited step


def test_audit_log_is_append_only(conn: sqlite3.Connection, synthetic: SyntheticContext) -> None:
    business = _insert_business(conn, synthetic)
    repository.change_status(conn, Business, business.id, BusinessStatus.ACTIVE, actor="test")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE audit_log SET actor = 'someone else'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM audit_log")


def test_snapshot_lineage_needs_exactly_one_source(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = OFF")  # isolate the CHECK from the dangling references
    try:
        for sources in ({}, {"source_run_id": new_id(), "score_run_id": new_id()}):
            values = {"id": new_id(), "snapshot_id": new_id(), "created_at": format_ts(utc_now())}
            values |= sources
            with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
                conn.execute(
                    f"INSERT INTO dataset_snapshot_sources ({', '.join(values)}) "  # noqa: S608
                    f"VALUES ({', '.join('?' * len(values))})",
                    list(values.values()),
                )
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


# ---- the Pydantic contract ---------------------------------------------------------------------


def test_records_reject_invalid_values() -> None:
    ctx = SyntheticContext(new_id(), new_id(), new_id(), utc_now())
    business = next(r for r in synthetic_business(ctx, 0) if isinstance(r, Business))
    data = business.model_dump()
    for change in (
        {"id": "not-a-uuid"},
        {"created_at": datetime(2026, 1, 1)},  # naive
        {"status": "bogus"},
        {"unexpected": 1},
    ):
        with pytest.raises(ValidationError):
            Business.model_validate(data | change)
    evidence = next(r for r in synthetic_business(ctx, 0) if isinstance(r, EvidenceItem))
    with pytest.raises(ValidationError):
        EvidenceItem.model_validate(evidence.model_dump() | {"confidence": 1.01})
    page: dict[str, object] = {"id": new_id(), "source_run_id": new_id(), "url": "u"}
    page |= {"source_query_id": None, "final_url": None, "http_status": None}
    page |= {"content_type": None, "content_sha256": None, "byte_size": None}
    page |= {"fetched_at": utc_now()}
    AcquiredPage.model_validate(page | {"artifact_path": "artifacts/a.html"})
    with pytest.raises(ValidationError):
        AcquiredPage.model_validate(page | {"artifact_path": "../a.html"})


def test_timestamps_are_utc_with_milliseconds() -> None:
    local = datetime(2026, 3, 1, 8, 30, 15, 123456, tzinfo=timezone(timedelta(hours=-6)))
    assert format_ts(local) == "2026-03-01T14:30:15.123Z"
    ctx = SyntheticContext(new_id(), new_id(), new_id(), local)
    business = next(r for r in synthetic_business(ctx, 0) if isinstance(r, Business))
    assert business.created_at == datetime(2026, 3, 1, 14, 30, 15, 123000, tzinfo=UTC)
    assert '"created_at":"2026-03-01T14:30:15.123Z"' in business.model_dump_json()


def test_ids_are_uuid7_and_sort_by_creation() -> None:
    ids = [new_id() for _ in range(10_000)]
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)
    assert {uuid.UUID(value).version for value in ids} == {7}


def test_state_machine_helper() -> None:
    assert can_transition(BusinessStatus.CANDIDATE, BusinessStatus.ACTIVE)
    assert can_transition(BusinessStatus.ACTIVE, BusinessStatus.ACTIVE)
    assert not can_transition(BusinessStatus.MERGED, BusinessStatus.CLOSED)
    assert can_transition(ValidationStatus.SUPPRESSED, ValidationStatus.OBSERVED)
    assert not can_transition(ValidationStatus.SUPPRESSED, ValidationStatus.LIKELY_VALID)
    with pytest.raises(TypeError):
        can_transition(BusinessStatus.ACTIVE, ValidationStatus.OBSERVED)


def test_sort_key_ignores_case_accents_and_spacing() -> None:
    assert sort_key("  Panadería  EL Sol ") == sort_key("panaderia el sol") == "panaderia el sol"
