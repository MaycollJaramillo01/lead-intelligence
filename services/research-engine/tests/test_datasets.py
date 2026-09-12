"""Dataset snapshots: DuckDB rows, partitioned Parquet with an explicit schema, SQLite lineage."""

import sqlite3
from pathlib import Path

import duckdb
import pytest

from research_engine import datasets, repository
from research_engine.domain import (
    Business,
    BusinessListQuery,
    BusinessSort,
    Dataset,
    DatasetListQuery,
    DatasetSnapshot,
    Record,
    new_id,
    utc_now,
)
from research_engine.storage import DUCKDB_FILE, data_path
from research_engine.testdata import SyntheticContext, seed_businesses

_COLUMNS = list(datasets.BUSINESS_SNAPSHOT_SCHEMA)


def _snapshot(conn: sqlite3.Connection, data_dir: Path, ctx: SyntheticContext) -> DatasetSnapshot:
    return datasets.snapshot_businesses(
        conn, data_dir, project_id=ctx.project_id, dataset_name="Synthetic snapshot", actor="test"
    )


def test_duckdb_table_matches_the_explicit_schema(data_dir: Path) -> None:
    with duckdb.connect(str(data_dir / DUCKDB_FILE)) as duck:
        described = duck.execute(f"DESCRIBE {datasets.BUSINESS_SNAPSHOT_TABLE}").fetchall()
    assert {row[0]: row[1] for row in described} == datasets.BUSINESS_SNAPSHOT_SCHEMA
    assert [row[0] for row in described] == _COLUMNS


def test_snapshot_rows_are_identical_in_duckdb_and_parquet(
    data_dir: Path, conn: sqlite3.Connection, synthetic: SyntheticContext
) -> None:
    seed_businesses(conn, synthetic, 30)
    snapshot = _snapshot(conn, data_dir, synthetic)
    assert snapshot.row_count == 30
    assert snapshot.parquet_path == f"datasets/{snapshot.dataset_id}/{snapshot.id}"

    columns = ", ".join(_COLUMNS)
    with duckdb.connect(str(data_dir / DUCKDB_FILE)) as duck:
        stored = duck.execute(
            f"SELECT {columns} FROM {datasets.BUSINESS_SNAPSHOT_TABLE} "  # noqa: S608
            "WHERE snapshot_id = ? ORDER BY business_id",
            [snapshot.id],
        ).fetchall()
    with duckdb.connect() as reader:
        scan = datasets.parquet_scan(data_dir, snapshot)
        copied = reader.execute(f"SELECT {columns} FROM {scan} ORDER BY business_id").fetchall()  # noqa: S608
        described = reader.execute(f"DESCRIBE SELECT {columns} FROM {scan}").fetchall()  # noqa: S608
    assert stored == copied
    assert len(stored) == 30
    assert {row[0]: row[1] for row in described} == datasets.BUSINESS_SNAPSHOT_SCHEMA
    partitions = {p.name for p in data_path(data_dir, snapshot.parquet_path).iterdir()}
    assert partitions == {
        "region=AZ",
        "region=CA",
        "region=FL",
        "region=IL",
        "region=NJ",
        "region=TX",
    }

    summary = repository.list_businesses(
        conn,
        BusinessListQuery(
            project_id=None,
            search=None,
            status=None,
            sort=BusinessSort.NAME,
            limit=500,
            cursor=None,
        ),
    )
    by_id = {item.id: item for item in summary.items}
    for row in stored:
        values = dict(zip(_COLUMNS, row, strict=True))
        item = by_id[values["business_id"]]
        assert values["display_name"] == item.display_name
        assert values["primary_phone"] == item.primary_phone
        assert values["source_count"] == item.source_count
        assert values["last_seen_at"] == item.last_seen_at.replace(tzinfo=None)


def test_snapshots_are_versioned_with_lineage_and_audit(
    data_dir: Path, conn: sqlite3.Connection, synthetic: SyntheticContext
) -> None:
    ids = seed_businesses(conn, synthetic, 4)
    first = _snapshot(conn, data_dir, synthetic)
    repository.soft_delete(conn, Business, ids[0], actor="test", reason="excluded from v2")
    second = _snapshot(conn, data_dir, synthetic)
    assert (first.dataset_id, first.version) == (second.dataset_id, 1)
    assert (second.version, second.row_count) == (2, 3)

    sources = conn.execute(
        "SELECT source_run_id FROM dataset_snapshot_sources WHERE snapshot_id = ?", (second.id,)
    ).fetchall()
    assert [row[0] for row in sources] == [synthetic.source_run_id]
    actions = conn.execute(
        "SELECT action FROM audit_log WHERE entity_id IN (?, ?) ORDER BY id",
        (first.id, first.dataset_id),
    ).fetchall()
    assert [row[0] for row in actions] == ["create", "create"]
    assert repository.get(conn, DatasetSnapshot, second.id) == second
    with duckdb.connect(str(data_dir / DUCKDB_FILE)) as duck:
        (kept,) = duck.execute(
            "SELECT count(*) FROM business_snapshot_rows WHERE snapshot_id = ?", [first.id]
        ).fetchone() or (0,)
    assert kept == 4  # older versions are never rewritten


def test_a_failed_registration_leaves_no_partial_snapshot(
    data_dir: Path,
    conn: sqlite3.Connection,
    synthetic: SyntheticContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed_businesses(conn, synthetic, 5)

    def refuse(connection: sqlite3.Connection, *records: Record) -> None:
        if any(isinstance(record, DatasetSnapshot) for record in records):
            raise sqlite3.OperationalError("disk full (simulated)")
        repository.insert(connection, *records)

    monkeypatch.setattr(datasets, "insert", refuse)
    with pytest.raises(sqlite3.OperationalError, match="simulated"):
        _snapshot(conn, data_dir, synthetic)

    assert conn.execute("SELECT count(*) FROM dataset_snapshots").fetchone()[0] == 0
    with duckdb.connect(str(data_dir / DUCKDB_FILE)) as duck:
        assert duck.execute("SELECT count(*) FROM business_snapshot_rows").fetchone() == (0,)
    (dataset_dir,) = (data_dir / "datasets").iterdir()
    assert list(dataset_dir.iterdir()) == []  # the Parquet copy was removed
    assert list((data_dir / "tmp").iterdir()) == []  # and the staging file


def test_dataset_list_is_paginated_newest_first(
    conn: sqlite3.Connection, synthetic: SyntheticContext
) -> None:
    now = utc_now()
    created = []
    for index in range(5):
        dataset = Dataset(
            id=new_id(),
            project_id=synthetic.project_id,
            name=f"Synthetic dataset {index}",
            description=None,
            created_at=now,
            updated_at=now,
            deleted_at=None,
            deleted_reason=None,
        )
        repository.insert(conn, dataset)
        created.append(dataset.id)
    query = DatasetListQuery(project_id=None, limit=2, cursor=None)
    seen = []
    while True:
        page = repository.list_datasets(conn, query)
        assert page.total == 5
        seen += [item.dataset.id for item in page.items]
        assert all(item.is_synthetic and item.snapshot_count == 0 for item in page.items)
        assert all(item.latest_snapshot is None for item in page.items)
        if page.next_cursor is None:
            break
        query = query.model_copy(update={"cursor": page.next_cursor})
    assert seen == list(reversed(created))  # same timestamp: ids break the tie, newest first
