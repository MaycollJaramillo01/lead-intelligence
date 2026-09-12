"""Dataset snapshots: reproducible analytical copies of canonical businesses.

A snapshot is written in three steps:
1. rows into DuckDB `business_snapshot_rows` (analytics);
2. the same rows as Parquet, partitioned by region, under `datasets/<dataset_id>/<snapshot_id>/`
   (durable copy with an explicit schema);
3. its registration in SQLite: `dataset_snapshots` plus lineage in `dataset_snapshot_sources`.
A failure undoes the earlier steps, so a registered snapshot always has its rows.

Rows travel from SQLite to DuckDB through a newline-delimited JSON file read with explicit column
types: DuckDB's `executemany` needs seconds for a few hundred rows, this takes milliseconds.
"""

import logging
import shutil
import sqlite3
from pathlib import Path

import duckdb
import orjson

from research_engine.domain import (
    AuditAction,
    Dataset,
    DatasetSnapshot,
    DatasetSnapshotSource,
    EntityType,
    new_id,
    utc_now,
)
from research_engine.repository import BUSINESS_SUMMARY_SQL, atomic, audit, insert
from research_engine.storage import DUCKDB_FILE, data_path

logger = logging.getLogger(__name__)

SNAPSHOT_SCHEMA_VERSION = 1
BUSINESS_SNAPSHOT_TABLE = "business_snapshot_rows"
PARTITION_COLUMN = "region"
# Explicit schema shared by the DuckDB table (migration 0002), the JSON load and the Parquet copy.
BUSINESS_SNAPSHOT_SCHEMA: dict[str, str] = {
    "snapshot_id": "VARCHAR",
    "business_id": "VARCHAR",
    "project_id": "VARCHAR",
    "display_name": "VARCHAR",
    "status": "VARCHAR",
    "lead_stage": "VARCHAR",
    "locality": "VARCHAR",
    "region": "VARCHAR",
    "postal_code": "VARCHAR",
    "primary_domain": "VARCHAR",
    "primary_phone": "VARCHAR",
    "primary_category": "VARCHAR",
    "source_count": "INTEGER",
    "evidence_count": "INTEGER",
    "first_seen_at": "TIMESTAMP",
    "last_seen_at": "TIMESTAMP",
}
_BATCH = 5000


def _literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def parquet_scan(data_dir: Path, snapshot: DatasetSnapshot) -> str:
    """A `read_parquet(...)` expression for a snapshot, with the partition column typed."""
    pattern = (data_path(data_dir, snapshot.parquet_path) / "**" / "*.parquet").as_posix()
    return (
        f"read_parquet({_literal(pattern)}, hive_partitioning = true, "
        f"hive_types = {{'{PARTITION_COLUMN}': VARCHAR}})"
    )


def _dataset_id(
    conn: sqlite3.Connection, project_id: str, name: str, description: str | None, actor: str
) -> str:
    row = conn.execute(
        "SELECT id FROM datasets WHERE project_id = ? AND name = ? AND deleted_at IS NULL",
        (project_id, name),
    ).fetchone()
    if row is not None:
        return str(row[0])
    now = utc_now()
    dataset = Dataset(
        id=new_id(),
        project_id=project_id,
        name=name,
        description=description,
        created_at=now,
        updated_at=now,
        deleted_at=None,
        deleted_reason=None,
    )
    insert(conn, dataset)
    audit(conn, AuditAction.CREATE, EntityType.DATASET, dataset.id, actor=actor)
    return dataset.id


def snapshot_businesses(
    conn: sqlite3.Connection,
    data_dir: Path,
    *,
    project_id: str,
    dataset_name: str,
    actor: str,
    description: str | None = None,
) -> DatasetSnapshot:
    """Captures every live business of a project as a new version of the named dataset."""
    with atomic(conn):
        dataset_id = _dataset_id(conn, project_id, dataset_name, description, actor)
    # ponytail: version = max + 1 without a lock; the UNIQUE (dataset_id, version) constraint
    # rejects a concurrent duplicate. The job orchestrator (phase 13) serialises snapshot writes.
    (version,) = conn.execute(
        "SELECT coalesce(max(version), 0) + 1 FROM dataset_snapshots WHERE dataset_id = ?",
        (dataset_id,),
    ).fetchone()
    snapshot_id = new_id()
    parquet_rel = f"datasets/{dataset_id}/{snapshot_id}"
    parquet_dir = data_path(data_dir, parquet_rel)
    staging = data_dir / "tmp" / f"{snapshot_id}.jsonl"
    staging.parent.mkdir(parents=True, exist_ok=True)

    row_count = 0
    with staging.open("wb") as out:
        cursor = conn.execute(
            BUSINESS_SUMMARY_SQL.format(key="b.id")
            + "WHERE b.project_id = ? AND b.deleted_at IS NULL AND p.deleted_at IS NULL "
            "ORDER BY b.id",
            (project_id,),
        )
        while batch := cursor.fetchmany(_BATCH):
            for row in batch:
                values = {"snapshot_id": snapshot_id, "business_id": row["id"]}
                values |= {name: row[name] for name in list(BUSINESS_SNAPSHOT_SCHEMA)[2:]}
                out.write(orjson.dumps(values) + b"\n")
            row_count += len(batch)
    source_runs = [
        str(run_id)
        for (run_id,) in conn.execute(
            "SELECT DISTINCT r.source_run_id FROM businesses b "
            "JOIN business_names n ON n.business_id = b.id "
            "JOIN extractions x ON x.id = n.extraction_id "
            "JOIN raw_records r ON r.id = x.raw_record_id "
            "WHERE b.project_id = ? AND b.deleted_at IS NULL ORDER BY 1",
            (project_id,),
        )
    ]

    # SQL below is built from module constants and ids/paths generated here, never from input
    columns = ", ".join(f"'{name}': '{kind}'" for name, kind in BUSINESS_SNAPSHOT_SCHEMA.items())
    source = _literal(staging.as_posix())
    duck = duckdb.connect(str(data_dir / DUCKDB_FILE))
    try:
        duck.execute(
            f"INSERT INTO {BUSINESS_SNAPSHOT_TABLE} SELECT * FROM read_json("  # noqa: S608
            f"{source}, format = 'newline_delimited', columns = {{{columns}}})"
        )
        parquet_dir.mkdir(parents=True, exist_ok=True)
        duck.execute(
            f"COPY (SELECT * FROM {BUSINESS_SNAPSHOT_TABLE} "  # noqa: S608
            f"WHERE snapshot_id = {_literal(snapshot_id)}) TO {_literal(parquet_dir.as_posix())} "
            f"(FORMAT parquet, PARTITION_BY ({PARTITION_COLUMN}), COMPRESSION zstd, "
            "OVERWRITE_OR_IGNORE)"
        )
        snapshot = DatasetSnapshot(
            id=snapshot_id,
            dataset_id=dataset_id,
            version=version,
            row_count=row_count,
            duckdb_table=BUSINESS_SNAPSHOT_TABLE,
            parquet_path=parquet_rel,
            schema_version=SNAPSHOT_SCHEMA_VERSION,
            filters_json={"project_id": project_id},
            created_at=utc_now(),
        )
        with atomic(conn):
            insert(conn, snapshot)
            insert(
                conn,
                *(
                    DatasetSnapshotSource(
                        id=new_id(),
                        snapshot_id=snapshot_id,
                        source_run_id=run_id,
                        score_run_id=None,
                        parent_snapshot_id=None,
                        created_at=snapshot.created_at,
                    )
                    for run_id in source_runs
                ),
            )
            audit(
                conn,
                AuditAction.CREATE,
                EntityType.DATASET_SNAPSHOT,
                snapshot_id,
                actor=actor,
                detail={"dataset_id": dataset_id, "version": version, "rows": row_count},
            )
    except BaseException:
        _undo(duck, parquet_dir, snapshot_id)
        raise
    finally:
        duck.close()
        staging.unlink(missing_ok=True)
    logger.info(
        "dataset snapshot written",
        extra={"snapshot_id": snapshot_id, "rows": row_count, "version": version},
    )
    return snapshot


def _undo(duck: duckdb.DuckDBPyConnection, parquet_dir: Path, snapshot_id: str) -> None:
    try:
        duck.execute(
            f"DELETE FROM {BUSINESS_SNAPSHOT_TABLE} WHERE snapshot_id = ?",  # noqa: S608
            [snapshot_id],
        )
    except duckdb.Error:
        logger.exception("could not remove the rows of a failed snapshot")
    shutil.rmtree(parquet_dir, ignore_errors=True)
