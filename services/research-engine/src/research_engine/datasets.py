"""Dataset snapshots: reproducible analytical copies of canonical businesses.

A snapshot is written in three steps:
1. rows into DuckDB `business_snapshot_rows` (analytics). They are read inside one SQLite read
   transaction, so every row, the version and the lineage belong to one moment: the cut-off;
2. the same rows as Parquet, partitioned by region, under `datasets/<dataset_id>/<snapshot_id>/`
   (durable copy with an explicit schema), and the quality report computed from that copy;
3. its registration in SQLite: `dataset_snapshots` plus lineage in `dataset_snapshot_sources` (the
   source runs behind the rows and the score run whose tiers and totals they carry).
A failure undoes the earlier steps, so a registered snapshot always has its rows. A registered
snapshot is never updated (trigger); retention deletes a version nothing depends on (`cleanup`).

Rows travel from SQLite to DuckDB through a newline-delimited JSON file read with explicit column
types: DuckDB's `executemany` needs seconds for a few hundred rows, this takes milliseconds.
"""

import logging
import shutil
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import duckdb
import orjson

from research_engine import __version__, repository
from research_engine.domain import (
    STALE_AFTER_DAYS,
    AuditAction,
    BulkMode,
    ChangedRow,
    CleanupSnapshot,
    Dataset,
    DatasetCleanupRequest,
    DatasetCleanupResult,
    DatasetDiff,
    DatasetDiffQuery,
    DatasetQuality,
    DatasetSnapshot,
    DatasetSnapshotDetail,
    DatasetSnapshotItem,
    DatasetSnapshotListQuery,
    DatasetSnapshotPage,
    DatasetSnapshotSource,
    DiffRow,
    EntityType,
    Export,
    FieldCoverage,
    LineageItem,
    LineageKind,
    ScoreRun,
    ValueCount,
    new_id,
    utc_now,
)
from research_engine.leads import explorer, index
from research_engine.repository import BUSINESS_SUMMARY_SQL, atomic, audit, insert
from research_engine.storage import DUCKDB_FILE, data_path

logger = logging.getLogger(__name__)

SNAPSHOT_SCHEMA_VERSION: Final = 2
BUSINESS_SNAPSHOT_TABLE: Final = "business_snapshot_rows"
PARTITION_COLUMN: Final = "region"
# Explicit schema shared by the DuckDB table (migrations 0002 and 0003), the JSON load and the
# Parquet copy, in table order. Schema 2 appended the last four columns.
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
    "score_run_id": "VARCHAR",
    "tier": "VARCHAR",
    "total_score": "DOUBLE",
    "suppressed": "BOOLEAN",
}
SCHEMA_1_COLUMNS: Final = tuple(list(BUSINESS_SNAPSHOT_SCHEMA)[:16])
#: Columns a quality report counts as known or unknown, when the snapshot holds them.
COVERED: Final = (
    "display_name",
    "locality",
    "region",
    "postal_code",
    "primary_domain",
    "primary_phone",
    "primary_category",
    "tier",
    "total_score",
)
#: Columns a diff never compares: identities, and the run a row was scored in (its tier and total
#: are compared instead).
_NOT_COMPARED: Final = frozenset({"snapshot_id", "business_id", "score_run_id"})
_BATCH: Final = 5000
_LIST_LIMIT: Final = 20


class DatasetError(ValueError):
    """A snapshot, comparison or cleanup the data cannot serve as asked."""


def _literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def parquet_scan(data_dir: Path, snapshot: DatasetSnapshot) -> str:
    """A `read_parquet(...)` expression for a snapshot, with the partition column typed."""
    pattern = (data_path(data_dir, snapshot.parquet_path) / "**" / "*.parquet").as_posix()
    return (
        f"read_parquet({_literal(pattern)}, hive_partitioning = true, "
        f"hive_types = {{'{PARTITION_COLUMN}': VARCHAR}})"
    )


def snapshot_columns(snapshot: DatasetSnapshot) -> tuple[str, ...]:
    return SCHEMA_1_COLUMNS if snapshot.schema_version == 1 else tuple(BUSINESS_SNAPSHOT_SCHEMA)


def relation(duck: duckdb.DuckDBPyConnection, data_dir: Path, snapshot: DatasetSnapshot) -> str:
    """What SQL reads a snapshot's rows from. An empty snapshot wrote no Parquet file, so it reads
    an empty table of its schema instead."""
    if snapshot.row_count:
        return parquet_scan(data_dir, snapshot)
    name = f"empty_v{snapshot.schema_version}"
    columns = ", ".join(
        f"{column} {BUSINESS_SNAPSHOT_SCHEMA[column]}" for column in snapshot_columns(snapshot)
    )
    duck.execute(f"CREATE TEMP TABLE IF NOT EXISTS {name} ({columns})")
    return name


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


def newest_finished_run(conn: sqlite3.Connection, project_id: str) -> str | None:
    row = conn.execute(
        "SELECT id FROM score_runs WHERE project_id = ? AND finished_at IS NOT NULL "
        "ORDER BY finished_at DESC, id DESC LIMIT 1",
        (project_id,),
    ).fetchone()
    return None if row is None else str(row[0])


def _score_run(
    conn: sqlite3.Connection, project_id: str, score_run_id: str | None
) -> ScoreRun | None:
    run_id = score_run_id or newest_finished_run(conn, project_id)
    if run_id is None:
        return None
    run = repository.get(conn, ScoreRun, run_id)
    if run.project_id != project_id or run.finished_at is None:
        raise DatasetError("a snapshot carries a finished score run of its own project")
    index.ensure_indexed(conn, run)
    return run


def _stage_rows(
    conn: sqlite3.Connection, staging: Path, snapshot_id: str, project_id: str, run: ScoreRun | None
) -> int:
    suppressed = explorer.suppressed_roots(conn)
    names = list(BUSINESS_SNAPSHOT_SCHEMA)
    count = 0
    with staging.open("wb") as out:
        cursor = conn.execute(
            BUSINESS_SUMMARY_SQL.format(key="b.id")
            + "WHERE b.project_id = ? AND b.deleted_at IS NULL AND p.deleted_at IS NULL "
            "AND b.status <> 'merged' ORDER BY b.id",
            (project_id,),
        )
        while batch := cursor.fetchmany(_BATCH):
            scores: dict[str, tuple[str, float | None]] = {}
            if run is not None:
                ids = [row["id"] for row in batch]
                scores = {
                    row[0]: (row[1], row[2])
                    for row in conn.execute(
                        "SELECT business_id, tier, total_score FROM lead_index "  # noqa: S608
                        f"WHERE score_run_id = ? AND business_id IN ({', '.join('?' * len(ids))})",
                        [run.id, *ids],
                    )
                }
            for row in batch:
                tier, total = scores.get(row["id"], (None, None))
                values: dict[str, object] = {"snapshot_id": snapshot_id, "business_id": row["id"]}
                values |= {name: row[name] for name in names[2:16]}
                values |= {
                    "score_run_id": run.id if run is not None else None,
                    "tier": tier,
                    "total_score": total,
                    "suppressed": row["id"] in suppressed,
                }
                out.write(orjson.dumps(values) + b"\n")
            count += len(batch)
    return count


def snapshot_businesses(
    conn: sqlite3.Connection,
    data_dir: Path,
    *,
    project_id: str,
    dataset_name: str,
    actor: str,
    description: str | None = None,
    score_run_id: str | None = None,
    pipeline_versions: dict[str, str] | None = None,
) -> DatasetSnapshot:
    """Captures every live business of a project as a new version of the named dataset."""
    project = conn.execute("SELECT deleted_at FROM projects WHERE id = ?", (project_id,)).fetchone()
    if project is None or project[0] is not None:
        raise repository.NotFoundError(f"project {project_id} not found")
    run = _score_run(conn, project_id, score_run_id)
    with atomic(conn):
        dataset_id = _dataset_id(conn, project_id, dataset_name, description, actor)
    snapshot_id = new_id()
    parquet_rel = f"datasets/{dataset_id}/{snapshot_id}"
    parquet_dir = data_path(data_dir, parquet_rel)
    staging = data_dir / "tmp" / f"{snapshot_id}.jsonl"
    staging.parent.mkdir(parents=True, exist_ok=True)

    # ponytail: version = max + 1 in a read transaction; the UNIQUE (dataset_id, version) constraint
    # rejects a concurrent duplicate, and pipelines of one project run one stage at a time.
    conn.execute("BEGIN")
    try:
        cutoff = utc_now()
        (version,) = conn.execute(
            "SELECT coalesce(max(version), 0) + 1 FROM dataset_snapshots WHERE dataset_id = ?",
            (dataset_id,),
        ).fetchone()
        row_count = _stage_rows(conn, staging, snapshot_id, project_id, run)
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
    finally:
        conn.execute("COMMIT")

    versions = {"snapshot_schema": str(SNAPSHOT_SCHEMA_VERSION), "worker": __version__}
    if run is not None:
        versions["scoring_engine"] = run.engine_version
    versions |= pipeline_versions or {}
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
        provisional = DatasetSnapshot(
            id=snapshot_id,
            dataset_id=dataset_id,
            version=version,
            row_count=row_count,
            duckdb_table=BUSINESS_SNAPSHOT_TABLE,
            parquet_path=parquet_rel,
            schema_version=SNAPSHOT_SCHEMA_VERSION,
            filters_json={"project_id": project_id},
            created_at=utc_now(),
            query_json={
                "project_id": project_id,
                "dataset_name": dataset_name,
                "score_run_id": run.id if run is not None else None,
            },
            cutoff_at=cutoff,
            pipeline_versions_json=versions,
            quality_json=None,
        )
        quality = quality_report(data_dir, provisional)
        snapshot = provisional.model_copy(update={"quality_json": quality.model_dump(mode="json")})
        lineage = [
            DatasetSnapshotSource(
                id=new_id(),
                snapshot_id=snapshot_id,
                source_run_id=run_id,
                score_run_id=None,
                parent_snapshot_id=None,
                created_at=snapshot.created_at,
            )
            for run_id in source_runs
        ]
        if run is not None:
            lineage.append(
                DatasetSnapshotSource(
                    id=new_id(),
                    snapshot_id=snapshot_id,
                    source_run_id=None,
                    score_run_id=run.id,
                    parent_snapshot_id=None,
                    created_at=snapshot.created_at,
                )
            )
        with atomic(conn):
            insert(conn, snapshot, *lineage)
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


# ---- quality ------------------------------------------------------------------------------------


def _aware(value: object) -> datetime | None:
    return value.replace(tzinfo=UTC) if isinstance(value, datetime) else None


def quality_report(data_dir: Path, snapshot: DatasetSnapshot) -> DatasetQuality:
    """Counts from the snapshot's own rows: coverage, statuses, tiers, score bands, freshness."""
    columns = snapshot_columns(snapshot)
    covered = [name for name in COVERED if name in columns]
    cutoff = snapshot.cutoff_at or snapshot.created_at
    stale_before = (cutoff - timedelta(days=STALE_AFTER_DAYS)).astimezone(UTC).replace(tzinfo=None)
    known = ", ".join(
        f"count_if({name} IS NOT NULL AND trim(CAST({name} AS VARCHAR)) <> '')" for name in covered
    )
    errors: list[str] = []
    duck = duckdb.connect()
    try:
        source = relation(duck, data_dir, snapshot)
        row = duck.execute(
            f"SELECT count(*), count(*) - count(DISTINCT business_id), {known}, "  # noqa: S608
            f"min(last_seen_at), max(last_seen_at), count_if(last_seen_at < ?) FROM {source}",
            [stale_before],
        ).fetchone()
        if row is None:  # an aggregate always answers one row
            raise DatasetError("the snapshot's rows could not be counted")
        statuses = duck.execute(
            f"SELECT status, count(*) FROM {source} GROUP BY 1 ORDER BY 1"  # noqa: S608
        ).fetchall()
        tiers: list[tuple[object, int]] = []
        bins = [0] * 10
        unscored = int(row[0])
        suppressed = 0
        if snapshot.schema_version >= 2:
            tiers = duck.execute(
                f"SELECT tier, count(*) FROM {source} WHERE tier IS NOT NULL "  # noqa: S608
                "GROUP BY 1 ORDER BY 1"
            ).fetchall()
            for band, band_count in duck.execute(
                "SELECT least(CAST(floor(total_score / 10) AS INTEGER), 9), count(*) "  # noqa: S608
                f"FROM {source} WHERE total_score IS NOT NULL GROUP BY 1"
            ).fetchall():
                bins[int(band)] = int(band_count)
            counted = duck.execute(
                f"SELECT count_if(total_score IS NULL), count_if(suppressed) FROM {source}"  # noqa: S608
            ).fetchone()
            if counted is None:
                raise DatasetError("the snapshot's scores could not be counted")
            unscored, suppressed = int(counted[0]), int(counted[1] or 0)
        else:
            errors.append("schema_1_without_scores_or_suppression")
    finally:
        duck.close()
    rows = int(row[0])
    coverage = [
        FieldCoverage(
            field=name,
            known=int(value),
            unknown=rows - int(value),
            share=round(int(value) / rows, 6) if rows else 0.0,
        )
        for name, value in zip(covered, row[2 : 2 + len(covered)], strict=True)
    ]
    tail = row[2 + len(covered) :]
    return DatasetQuality(
        schema_version=snapshot.schema_version,
        rows=rows,
        duplicates=int(row[1]),
        coverage=coverage,
        statuses=[ValueCount(value=str(value), count=int(count)) for value, count in statuses],
        tiers=[ValueCount(value=str(value), count=int(count)) for value, count in tiers],
        score_bins=bins,
        unscored=unscored,
        suppressed=suppressed,
        oldest_seen_at=_aware(tail[0]),
        newest_seen_at=_aware(tail[1]),
        stale_rows=int(tail[2]),
        errors=errors,
    )


# ---- reading ------------------------------------------------------------------------------------

_SNAPSHOT_COLUMNS: Final = ", ".join(f"s.{name}" for name in DatasetSnapshot.model_fields)
_ITEM_SQL: Final = (
    f"SELECT {_SNAPSHOT_COLUMNS}, d.name AS dataset_name, "  # noqa: S608
    "d.project_id AS dataset_project, p.is_synthetic, "
    "(SELECT count(*) FROM dataset_snapshot_sources x WHERE x.snapshot_id = s.id) "
    "AS lineage_count, "
    "(SELECT count(*) FROM exports e WHERE e.dataset_snapshot_id = s.id) AS export_count "
    "FROM dataset_snapshots s JOIN datasets d ON d.id = s.dataset_id "
    "JOIN projects p ON p.id = d.project_id "
)


def _item(row: Any) -> DatasetSnapshotItem:
    return DatasetSnapshotItem(
        snapshot=repository.from_row(DatasetSnapshot, row),
        dataset_name=row["dataset_name"],
        project_id=row["dataset_project"],
        lineage_count=row["lineage_count"],
        export_count=row["export_count"],
        is_synthetic=bool(row["is_synthetic"]),
    )


def snapshot_item(conn: sqlite3.Connection, snapshot_id: str) -> DatasetSnapshotItem:
    row = conn.execute(_ITEM_SQL + "WHERE s.id = ?", (snapshot_id,)).fetchone()
    if row is None:
        raise repository.NotFoundError(f"dataset snapshot {snapshot_id} not found")
    return _item(row)


def list_snapshots(
    conn: sqlite3.Connection, query: DatasetSnapshotListQuery
) -> DatasetSnapshotPage:
    dataset = repository.get(conn, Dataset, query.dataset_id)
    if dataset.deleted_at is not None:
        raise repository.NotFoundError(f"dataset {query.dataset_id} has been deleted")
    (total,) = conn.execute(
        "SELECT count(*) FROM dataset_snapshots WHERE dataset_id = ?", (query.dataset_id,)
    ).fetchone()
    where, params = "s.dataset_id = ?", list[object]([query.dataset_id])
    if query.cursor is not None:
        last_version, last_id = repository.decode_cursor(query.cursor, "snapshot")
        where += " AND (s.version, s.id) < (?, ?)"
        params += [int(last_version), last_id]
    rows = conn.execute(
        _ITEM_SQL + f"WHERE {where} ORDER BY s.version DESC, s.id DESC LIMIT ?",
        [*params, query.limit + 1],
    ).fetchall()
    page = rows[: query.limit]
    next_cursor = (
        repository.encode_cursor("snapshot", str(page[-1]["version"]), page[-1]["id"])
        if len(rows) > query.limit
        else None
    )
    return DatasetSnapshotPage(
        items=[_item(row) for row in page], next_cursor=next_cursor, total=total
    )


def lineage(conn: sqlite3.Connection, snapshot_id: str) -> list[LineageItem]:
    items: list[LineageItem] = []
    for row in conn.execute(
        "SELECT x.source_run_id, x.score_run_id, x.parent_snapshot_id, c.name AS connector, "
        "coalesce(r.started_at, r.created_at) AS run_at, ip.name AS icp_name, "
        "iv.version AS icp_version, sm.key AS model_key, mv.version AS model_version, "
        "sr.finished_at AS scored_at, pd.name AS parent_dataset, ps.version AS parent_version, "
        "ps.created_at AS parent_at "
        "FROM dataset_snapshot_sources x "
        "LEFT JOIN source_runs r ON r.id = x.source_run_id "
        "LEFT JOIN source_connectors c ON c.id = r.connector_id "
        "LEFT JOIN score_runs sr ON sr.id = x.score_run_id "
        "LEFT JOIN icp_profile_versions iv ON iv.id = sr.icp_profile_version_id "
        "LEFT JOIN icp_profiles ip ON ip.id = iv.icp_profile_id "
        "LEFT JOIN scoring_model_versions mv ON mv.id = sr.scoring_model_version_id "
        "LEFT JOIN scoring_models sm ON sm.id = mv.scoring_model_id "
        "LEFT JOIN dataset_snapshots ps ON ps.id = x.parent_snapshot_id "
        "LEFT JOIN datasets pd ON pd.id = ps.dataset_id "
        "WHERE x.snapshot_id = ? ORDER BY x.created_at, x.id",
        (snapshot_id,),
    ):
        if row["source_run_id"] is not None:
            items.append(
                LineageItem(
                    kind=LineageKind.SOURCE_RUN,
                    id=row["source_run_id"],
                    label=row["connector"],
                    at=row["run_at"],
                )
            )
        elif row["score_run_id"] is not None:
            label = (
                f"{row['icp_name']} v{row['icp_version']} · "
                f"{row['model_key']} v{row['model_version']}"
            )
            items.append(
                LineageItem(
                    kind=LineageKind.SCORE_RUN,
                    id=row["score_run_id"],
                    label=label,
                    at=row["scored_at"],
                )
            )
        else:
            label = f"{row['parent_dataset']} v{row['parent_version']}"
            items.append(
                LineageItem(
                    kind=LineageKind.PARENT_SNAPSHOT,
                    id=row["parent_snapshot_id"],
                    label=label,
                    at=row["parent_at"],
                )
            )
    return items


def snapshot_detail(
    conn: sqlite3.Connection, data_dir: Path, snapshot_id: str
) -> DatasetSnapshotDetail:
    item = snapshot_item(conn, snapshot_id)
    stored = item.snapshot.quality_json
    quality = (
        DatasetQuality.model_validate(stored)
        if stored is not None
        else quality_report(data_dir, item.snapshot)
    )
    exports = [
        repository.from_row(Export, row)
        for row in conn.execute(
            f"SELECT {', '.join(Export.model_fields)} FROM exports "  # noqa: S608
            "WHERE dataset_snapshot_id = ? ORDER BY requested_at DESC, id DESC LIMIT ?",
            (snapshot_id, _LIST_LIMIT),
        )
    ]
    return DatasetSnapshotDetail(
        item=item, quality=quality, lineage=lineage(conn, snapshot_id), exports=exports
    )


# ---- comparing ----------------------------------------------------------------------------------


def diff(conn: sqlite3.Connection, data_dir: Path, query: DatasetDiffQuery) -> DatasetDiff:
    """What changed between two snapshots: rows added, removed, and changed with the columns
    whose values differ. Counts cover every row; the lists stop at `limit`."""
    before = snapshot_item(conn, query.before_snapshot_id)
    after = snapshot_item(conn, query.after_snapshot_id)
    shared = set(snapshot_columns(before.snapshot)) & set(snapshot_columns(after.snapshot))
    compared = [name for name in BUSINESS_SNAPSHOT_SCHEMA if name in shared - _NOT_COMPARED]
    differs = " OR ".join(f"a.{name} IS DISTINCT FROM b.{name}" for name in compared)
    duck = duckdb.connect()
    try:
        left = relation(duck, data_dir, before.snapshot)
        right = relation(duck, data_dir, after.snapshot)
        added_count, removed_count, changed_count, unchanged_count = duck.execute(
            f"SELECT (SELECT count(*) FROM {right} b ANTI JOIN {left} a USING (business_id)), "  # noqa: S608
            f"(SELECT count(*) FROM {left} a ANTI JOIN {right} b USING (business_id)), "
            f"(SELECT count(*) FROM {left} a JOIN {right} b USING (business_id) WHERE {differs}), "
            f"(SELECT count(*) FROM {left} a JOIN {right} b USING (business_id) "
            f"WHERE NOT ({differs}))"
        ).fetchone() or (0, 0, 0, 0)
        added = duck.execute(
            f"SELECT b.business_id, b.display_name FROM {right} b ANTI JOIN {left} a "  # noqa: S608
            "USING (business_id) ORDER BY b.business_id LIMIT ?",
            [query.limit],
        ).fetchall()
        removed = duck.execute(
            f"SELECT a.business_id, a.display_name FROM {left} a ANTI JOIN {right} b "  # noqa: S608
            "USING (business_id) ORDER BY a.business_id LIMIT ?",
            [query.limit],
        ).fetchall()
        pairs = ", ".join(f"a.{name}, b.{name}" for name in compared)
        changed_rows = duck.execute(
            f"SELECT a.business_id, b.display_name, {pairs} FROM {left} a JOIN {right} b "  # noqa: S608
            f"USING (business_id) WHERE {differs} ORDER BY a.business_id LIMIT ?",
            [query.limit],
        ).fetchall()
    finally:
        duck.close()
    changed = [
        ChangedRow(
            business_id=str(row[0]),
            display_name=str(row[1]),
            fields=[
                name
                for position, name in enumerate(compared)
                if row[2 + 2 * position] != row[3 + 2 * position]
            ],
        )
        for row in changed_rows
    ]
    return DatasetDiff(
        before=before,
        after=after,
        added_count=int(added_count),
        removed_count=int(removed_count),
        changed_count=int(changed_count),
        unchanged_count=int(unchanged_count),
        added=[DiffRow(business_id=str(row[0]), display_name=str(row[1])) for row in added],
        removed=[DiffRow(business_id=str(row[0]), display_name=str(row[1])) for row in removed],
        changed=changed,
        compared_fields=compared,
        truncated=max(added_count, removed_count, changed_count) > query.limit,
    )


# ---- retention ----------------------------------------------------------------------------------


def _bytes(data_dir: Path, snapshot: DatasetSnapshot) -> int:
    folder = data_path(data_dir, snapshot.parquet_path)
    return sum(path.stat().st_size for path in folder.rglob("*") if path.is_file())


def cleanup(
    conn: sqlite3.Connection, data_dir: Path, request: DatasetCleanupRequest, *, actor: str
) -> DatasetCleanupResult:
    """Keeps the newest `keep_latest` versions, any version a kept one derives from, and any
    version an export was written from; deletes the others when applied, rows and files included."""
    dataset = repository.get(conn, Dataset, request.dataset_id)
    if dataset.deleted_at is not None:
        raise repository.NotFoundError(f"dataset {request.dataset_id} has been deleted")
    snapshots = [
        repository.from_row(DatasetSnapshot, row)
        for row in conn.execute(
            f"SELECT {', '.join(DatasetSnapshot.model_fields)} FROM dataset_snapshots "  # noqa: S608
            "WHERE dataset_id = ? ORDER BY version DESC",
            (request.dataset_id,),
        )
    ]
    exported = {
        str(row[0])
        for row in conn.execute(
            "SELECT DISTINCT dataset_snapshot_id FROM exports WHERE dataset_snapshot_id IN "
            "(SELECT id FROM dataset_snapshots WHERE dataset_id = ?)",
            (request.dataset_id,),
        )
    }
    reasons: dict[str, str] = {s.id: "latest" for s in snapshots[: request.keep_latest]}
    for snapshot in snapshots[request.keep_latest :]:
        if snapshot.id in exported:
            reasons[snapshot.id] = "exported"
    # a version that a kept one derives from stays, and so on up its lineage
    frontier = list(reasons)
    while frontier:
        parents = [
            str(row[0])
            for row in conn.execute(
                "SELECT parent_snapshot_id FROM dataset_snapshot_sources WHERE snapshot_id IN "  # noqa: S608
                f"({', '.join('?' * len(frontier))}) AND parent_snapshot_id IS NOT NULL",
                frontier,
            )
        ]
        frontier = [parent for parent in parents if parent not in reasons]
        reasons |= dict.fromkeys(frontier, "parent_of_kept")

    def entry(snapshot: DatasetSnapshot) -> CleanupSnapshot:
        return CleanupSnapshot(
            snapshot_id=snapshot.id,
            version=snapshot.version,
            row_count=snapshot.row_count,
            bytes=_bytes(data_dir, snapshot),
            kept_because=reasons.get(snapshot.id),
        )

    kept = [entry(snapshot) for snapshot in snapshots if snapshot.id in reasons]
    doomed = [snapshot for snapshot in snapshots if snapshot.id not in reasons]
    deleted = [entry(snapshot) for snapshot in doomed]
    if request.mode is BulkMode.APPLY and doomed:
        _delete(conn, data_dir, doomed, request.reason or "", actor)
    return DatasetCleanupResult(
        mode=request.mode,
        deleted=deleted,
        kept=kept,
        freed_bytes=sum(item.bytes for item in deleted),
    )


def _delete(
    conn: sqlite3.Connection,
    data_dir: Path,
    snapshots: Sequence[DatasetSnapshot],
    reason: str,
    actor: str,
) -> None:
    """The registration goes first: a snapshot is never listed without its rows. Rows or files a
    failure leaves behind are only disk space, and are logged."""
    ids = [snapshot.id for snapshot in snapshots]
    marks = ", ".join("?" * len(ids))
    with atomic(conn):
        conn.execute(f"DELETE FROM dataset_snapshot_sources WHERE snapshot_id IN ({marks})", ids)  # noqa: S608
        conn.execute(f"DELETE FROM dataset_snapshots WHERE id IN ({marks})", ids)  # noqa: S608
        for snapshot in snapshots:
            audit(
                conn,
                AuditAction.PURGE,
                EntityType.DATASET_SNAPSHOT,
                snapshot.id,
                actor=actor,
                detail={
                    "dataset_id": snapshot.dataset_id,
                    "version": snapshot.version,
                    "rows": snapshot.row_count,
                    "reason": reason,
                },
            )
    duck = duckdb.connect(str(data_dir / DUCKDB_FILE))
    try:
        for snapshot in snapshots:
            duck.execute(
                f"DELETE FROM {snapshot.duckdb_table} WHERE snapshot_id = ?",  # noqa: S608
                [snapshot.id],
            )
    except duckdb.Error:
        logger.exception("could not remove the rows of deleted snapshots")
    finally:
        duck.close()
    for snapshot in snapshots:
        shutil.rmtree(data_path(data_dir, snapshot.parquet_path), ignore_errors=True)
    logger.info("dataset snapshots deleted", extra={"snapshots": len(ids)})
