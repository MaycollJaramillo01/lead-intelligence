"""Read-only integrity queries over a Lead Intelligence data directory (phase 15 audit).

Run from the repository root:
`uv run --directory services/research-engine python ../../scripts/integrity_check.py <data dir>`.
Prints one JSON report; exits 1 when a critical check finds rows. Nothing is written.
"""

import json
import sqlite3
import sys
from pathlib import Path

from research_engine.storage import SQLITE_FILE, sqlite_problems

OWNERSHIP = ("verified_latino_owned", "high_confidence_latino_owned", "probable_latino_owned")

# (name, severity, query returning offending rows)
CHECKS: list[tuple[str, str, str]] = [
    (
        "ownership_status_without_linked_evidence",
        "critical",
        "SELECT a.id, a.business_id, a.status FROM latino_assessments a WHERE a.superseded_at IS NULL "
        f"AND a.status IN {OWNERSHIP} AND NOT EXISTS (SELECT 1 FROM latino_assessment_evidence e "
        "WHERE e.assessment_id = a.id)",
    ),
    (
        "more_than_one_current_latino_assessment",
        "high",
        "SELECT business_id, count(*) FROM latino_assessments WHERE superseded_at IS NULL "
        "GROUP BY business_id HAVING count(*) > 1",
    ),
    (
        "linked_evidence_of_another_business",
        "critical",
        "SELECT a.id, i.id FROM latino_assessment_evidence e JOIN latino_assessments a "
        "ON a.id = e.assessment_id JOIN evidence_items i ON i.id = e.evidence_item_id "
        # evidence of a record merged into the assessed business legitimately counts for it
        "WHERE i.business_id <> a.business_id AND i.business_id NOT IN "
        "(SELECT id FROM businesses WHERE merged_into_id = a.business_id)",
    ),
    (
        "scored_lead_without_components",
        "critical",
        "SELECT s.id, s.tier FROM business_scores s WHERE s.tier IN ('priority', 'review', 'low') "
        "AND NOT EXISTS (SELECT 1 FROM component_scores c WHERE c.business_score_id = s.id)",
    ),
    (
        "scored_lead_whose_components_have_no_value",
        "critical",
        "SELECT s.id, s.tier, s.total_score FROM business_scores s "
        "WHERE s.tier IN ('priority', 'review', 'low') AND EXISTS (SELECT 1 FROM component_scores c "
        "WHERE c.business_score_id = s.id) AND NOT EXISTS (SELECT 1 FROM component_scores c "
        "WHERE c.business_score_id = s.id AND c.value IS NOT NULL)",
    ),
    (
        "succeeded_job_with_unfinished_tasks",
        "high",
        "SELECT j.id, j.kind, t.stage, t.status FROM jobs j JOIN job_tasks t ON t.job_id = j.id "
        "WHERE j.status = 'succeeded' AND t.status NOT IN ('succeeded', 'cancelled')",
    ),
    (
        "finished_job_with_open_attempts",
        "high",
        "SELECT j.id, j.kind, a.id, a.status FROM jobs j JOIN job_tasks t ON t.job_id = j.id "
        "JOIN task_attempts a ON a.task_id = t.id WHERE j.status IN ('succeeded', 'failed', 'cancelled') "
        "AND a.status IN ('running', 'paused')",
    ),
    (
        "running_task_under_idle_job",
        "medium",
        "SELECT j.id, j.status, t.stage FROM jobs j JOIN job_tasks t ON t.job_id = j.id "
        "WHERE t.status = 'running' AND j.status NOT IN ('running', 'paused')",
    ),
    (
        "snapshot_without_lineage",
        "high",
        "SELECT s.id, s.version FROM dataset_snapshots s WHERE NOT EXISTS "
        "(SELECT 1 FROM dataset_snapshot_sources l WHERE l.snapshot_id = s.id)",
    ),
    (
        "written_export_without_hash_or_count",
        "high",
        "SELECT id, file_name FROM exports WHERE status = 'succeeded' "
        "AND (file_sha256 IS NULL OR row_count IS NULL)",
    ),
    (
        "finished_score_run_without_scores",
        "low",
        "SELECT r.id FROM score_runs r WHERE r.finished_at IS NOT NULL AND NOT EXISTS "
        "(SELECT 1 FROM business_scores s WHERE s.score_run_id = r.id)",
    ),
    (
        "live_businesses_sharing_a_domain",
        "low",
        "SELECT w.domain, count(DISTINCT b.id) FROM websites w JOIN businesses b ON b.id = w.business_id "
        "WHERE b.deleted_at IS NULL AND b.status <> 'merged' GROUP BY b.project_id, w.domain "
        "HAVING count(DISTINCT b.id) > 1",
    ),
]


def main(data_dir: Path) -> int:
    database = data_dir / SQLITE_FILE
    report: dict[str, object] = {"store": str(database), "storage_problems": sqlite_problems(database)}
    critical = bool(report["storage_problems"])
    conn = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        for name, severity, sql in CHECKS:
            rows = conn.execute(sql).fetchall()
            report[name] = {"severity": severity, "rows": len(rows), "sample": rows[:5]}
            critical |= severity == "critical" and bool(rows)
    finally:
        conn.close()
    print(json.dumps(report, indent=1, default=str))
    return 1 if critical else 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
