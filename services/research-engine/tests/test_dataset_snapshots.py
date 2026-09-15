"""Dataset snapshots (phase 13): one moment with its lineage and quality, immutability, comparison
and retention."""

import sqlite3
from pathlib import Path

import duckdb
import pytest
from pydantic import ValidationError

from research_engine import datasets, repository
from research_engine.contacts import store as contacts
from research_engine.domain import (
    BulkMode,
    Business,
    DatasetCleanupRequest,
    DatasetDiffQuery,
    Export,
    ExportFormat,
    ExportStatus,
    LineageKind,
    SuppressionChangeRequest,
    SuppressionKind,
    new_id,
    utc_now,
)
from research_engine.leads import explorer
from research_engine.storage import DUCKDB_FILE, data_path
from research_engine.testdata import SyntheticContext, seed_businesses, seed_synthetic_workspace

LIVE = (
    "SELECT id FROM businesses WHERE project_id = ? AND deleted_at IS NULL AND status <> 'merged' "
    "ORDER BY id"
)


def test_a_snapshot_is_one_moment_with_its_lineage_quality_and_versions(
    conn: sqlite3.Connection, data_dir: Path
) -> None:
    seeded = seed_synthetic_workspace(conn, data_dir)
    snapshot = datasets.snapshot_businesses(
        conn, data_dir, project_id=seeded.project_id, dataset_name="Fixture dataset", actor="test"
    )
    assert (snapshot.version, snapshot.schema_version) == (1, 2)
    assert snapshot.cutoff_at is not None
    assert snapshot.cutoff_at <= snapshot.created_at
    run_id = datasets.newest_finished_run(conn, seeded.project_id)
    assert snapshot.query_json["score_run_id"] == run_id
    assert snapshot.pipeline_versions_json["snapshot_schema"] == "2"

    detail = datasets.snapshot_detail(conn, data_dir, snapshot.id)
    kinds = {item.kind for item in detail.lineage}
    assert kinds == {LineageKind.SOURCE_RUN, LineageKind.SCORE_RUN}
    live = [row[0] for row in conn.execute(LIVE, (seeded.project_id,))]
    quality = detail.quality
    assert quality.rows == snapshot.row_count == len(live)
    assert quality.duplicates == 0
    tiers = dict(
        conn.execute(
            "SELECT li.tier, count(*) FROM lead_index li "
            "JOIN businesses b ON b.id = li.business_id "
            "WHERE li.score_run_id = ? AND b.deleted_at IS NULL AND b.status <> 'merged' "
            "GROUP BY li.tier",
            (run_id,),
        ).fetchall()
    )
    assert {item.value: item.count for item in quality.tiers} == tiers
    assert sum(quality.score_bins) + quality.unscored == quality.rows
    assert quality.suppressed == len(explorer.suppressed_roots(conn) & set(live))
    coverage = {item.field: item for item in quality.coverage}
    assert coverage["display_name"].share == 1.0
    assert coverage["tier"].known == sum(tiers.values())

    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        conn.execute("UPDATE dataset_snapshots SET row_count = 0 WHERE id = ?", (snapshot.id,))
    pattern = (data_path(data_dir, snapshot.parquet_path) / "**" / "*.parquet").as_posix()
    duck = duckdb.connect()
    try:
        types = dict(
            (row[0], row[1])
            for row in duck.execute(
                f"DESCRIBE SELECT * FROM read_parquet('{pattern}', hive_partitioning = true)"  # noqa: S608
            ).fetchall()
        )
    finally:
        duck.close()
    assert (types["total_score"], types["suppressed"], types["first_seen_at"]) == (
        "DOUBLE",
        "BOOLEAN",
        "TIMESTAMP",
    )


def test_two_snapshots_differ_by_rows_added_removed_and_the_fields_that_changed(
    conn: sqlite3.Connection, data_dir: Path
) -> None:
    seeded = seed_synthetic_workspace(conn, data_dir)
    first = datasets.snapshot_businesses(
        conn, data_dir, project_id=seeded.project_id, dataset_name="Fixture dataset", actor="test"
    )
    live = [row[0] for row in conn.execute(LIVE, (seeded.project_id,))]
    suppressed = explorer.suppressed_roots(conn)
    candidates = [business for business in live if business not in suppressed]
    gone, changed = candidates[0], candidates[1]
    repository.soft_delete(conn, Business, gone, actor="test", reason="A duplicate (test)")
    contacts.change_suppression(
        conn,
        SuppressionChangeRequest(
            suppression_id=None,
            kind=SuppressionKind.BUSINESS,
            value=changed,
            reason="Asked to be left alone (test)",
        ),
        actor="test",
    )
    run_id, connector_id = conn.execute(
        "SELECT id, connector_id FROM source_runs WHERE project_id = ? ORDER BY created_at LIMIT 1",
        (seeded.project_id,),
    ).fetchone()
    context = SyntheticContext(seeded.project_id, connector_id, run_id, utc_now())
    added = seed_businesses(conn, context, 2, start=900)
    second = datasets.snapshot_businesses(
        conn, data_dir, project_id=seeded.project_id, dataset_name="Fixture dataset", actor="test"
    )
    assert second.version == 2

    query = DatasetDiffQuery(before_snapshot_id=first.id, after_snapshot_id=second.id, limit=500)
    result = datasets.diff(conn, data_dir, query)
    assert (result.added_count, result.removed_count) == (2, 1)
    assert {row.business_id for row in result.added} == set(added)
    assert [row.business_id for row in result.removed] == [gone]
    fields = {row.business_id: row.fields for row in result.changed}
    assert "suppressed" in fields[changed]
    assert result.changed_count + result.unchanged_count == first.row_count - 1
    assert "score_run_id" not in result.compared_fields
    assert not result.truncated
    small = datasets.diff(conn, data_dir, query.model_copy(update={"limit": 1}))
    assert (small.truncated, len(small.added), small.added_count) == (True, 1, 2)
    with pytest.raises(ValidationError):
        DatasetDiffQuery(before_snapshot_id=first.id, after_snapshot_id=first.id, limit=10)


def test_retention_keeps_the_latest_and_what_was_exported_and_deletes_the_rest_with_its_files(
    conn: sqlite3.Connection, data_dir: Path
) -> None:
    seeded = seed_synthetic_workspace(conn, data_dir)
    versions = [
        datasets.snapshot_businesses(
            conn,
            data_dir,
            project_id=seeded.project_id,
            dataset_name="Fixture dataset",
            actor="test",
        )
        for _ in range(4)
    ]
    now = utc_now()
    repository.insert(
        conn,
        Export(
            id=new_id(),
            project_id=seeded.project_id,
            dataset_snapshot_id=versions[1].id,
            status=ExportStatus.PENDING,
            format=ExportFormat.CSV,
            file_name="fixture.csv",
            row_count=None,
            file_sha256=None,
            manifest_json=None,
            requested_at=now,
            started_at=None,
            finished_at=None,
            error_message=None,
            created_at=now,
            updated_at=now,
            job_id=None,
            destination=None,
            columns_json=[],
            config_json={},
            actor="test",
            preset_key=None,
            preset_version=None,
            error_code=None,
        ),
    )
    request = DatasetCleanupRequest(
        dataset_id=versions[0].dataset_id, keep_latest=1, mode=BulkMode.PREVIEW, reason=None
    )
    preview = datasets.cleanup(conn, data_dir, request, actor="test")
    assert [item.version for item in preview.deleted] == [3, 1]
    assert {item.version: item.kept_because for item in preview.kept} == {
        4: "latest",
        2: "exported",
    }
    assert preview.freed_bytes > 0
    (count,) = conn.execute(
        "SELECT count(*) FROM dataset_snapshots WHERE dataset_id = ?", (versions[0].dataset_id,)
    ).fetchone()
    assert count == 4

    applied = datasets.cleanup(
        conn,
        data_dir,
        request.model_copy(update={"mode": BulkMode.APPLY, "reason": "Retention (test)"}),
        actor="test",
    )
    assert [item.version for item in applied.deleted] == [3, 1]
    remaining = [
        row[0]
        for row in conn.execute(
            "SELECT version FROM dataset_snapshots WHERE dataset_id = ? ORDER BY version",
            (versions[0].dataset_id,),
        )
    ]
    assert remaining == [2, 4]
    duck = duckdb.connect(str(data_dir / DUCKDB_FILE))
    try:
        for gone in (versions[0], versions[2]):
            assert not data_path(data_dir, gone.parquet_path).exists()
            (rows,) = duck.execute(
                "SELECT count(*) FROM business_snapshot_rows WHERE snapshot_id = ?", [gone.id]
            ).fetchone() or (None,)
            assert rows == 0
    finally:
        duck.close()
    (purged,) = conn.execute(
        "SELECT count(*) FROM audit_log WHERE action = 'purge' AND entity_type = 'dataset_snapshot'"
    ).fetchone()
    assert purged == 2
    with pytest.raises(ValidationError, match="reason"):
        DatasetCleanupRequest(
            dataset_id=versions[0].dataset_id, keep_latest=1, mode=BulkMode.APPLY, reason=None
        )
