"""Profiles the Lead Explorer over a synthetic index of a million leads (phase 12).

    uv run --directory services/research-engine python scripts/profile_lead_explorer.py [rows]

Seeds the synthetic workspace in a temporary directory, clones its newest score run into `rows`
synthetic index rows (test data, deleted afterwards) and measures what the explorer does with them:
the time and the Python memory peak of each call.
"""

import shutil
import sys
import tempfile
import time
import tracemalloc
from collections.abc import Callable
from pathlib import Path

from research_engine import repository
from research_engine.domain import (
    ExplorerField,
    ExplorerQuery,
    ExportPreviewRequest,
    no_filters,
    utc_now,
)
from research_engine.leads import LeadError, explorer
from research_engine.storage import check_duckdb, check_sqlite
from research_engine.testdata import clone_lead_index, seed_synthetic_workspace

MIB = 1024 * 1024


def _query(**fields: object) -> ExplorerQuery:
    base: dict[str, object] = {
        "score_run_id": None,
        "filters": no_filters(),
        "search": None,
        "sort": [],
        "offset": 0,
        "limit": 100,
        "with_total": True,
    }
    return ExplorerQuery.model_validate(base | fields)


def _measure(name: str, action: Callable[[], str]) -> None:
    tracemalloc.start()
    started = time.perf_counter()
    result = action()
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(f"{name:<46} {elapsed * 1000:>9.1f} ms {peak / MIB:>7.2f} MiB  {result}")


def main() -> None:
    rows = int(sys.argv[1]) if len(sys.argv) > 1 else 1_000_000
    data_dir = Path(tempfile.mkdtemp(prefix="lead-explorer-"))
    try:
        check_sqlite(data_dir)
        check_duckdb(data_dir)
        conn = repository.connect(data_dir)
        seed_synthetic_workspace(conn, data_dir)
        (run_id,) = conn.execute(
            "SELECT id FROM score_runs ORDER BY finished_at DESC, id DESC LIMIT 1"
        ).fetchone()
        now = utc_now()
        filters = {
            "kind": "group", "join": "and", "negate": False,
            "children": [
                {"kind": "condition", "field": "tier", "operator": "in",
                 "value": ["priority", "review"]},
                {"kind": "group", "join": "or", "negate": False, "children": [
                    {"kind": "condition", "field": "region", "operator": "eq", "value": "TX"},
                    {"kind": "condition", "field": "problem", "operator": "has",
                     "value": "no_website"},
                ]},
            ],
        }  # fmt: skip
        sort = [
            {"field": "total_score", "direction": "desc"},
            {"field": "name", "direction": "asc"},
        ]

        def clone() -> str:
            clone_lead_index(conn, run_id, rows)
            return f"{rows} rows cloned and ranked"

        def window(**fields: object) -> Callable[[], str]:
            def run() -> str:
                page = explorer.explore(conn, _query(**fields), now)
                return f"{len(page.rows)} rows, total {page.total}"

            return run

        def export() -> str:
            request = ExportPreviewRequest(
                query=_query(filters=filters), columns=[ExplorerField.NAME],
                include_suppressed=False, sample_size=50,
            )  # fmt: skip
            preview = explorer.export_preview(conn, request, now)
            return f"{preview.matching} matching, {len(preview.sample)} in the sample"

        def refused() -> str:
            try:
                explorer.matching_ids(conn, _query(), now, 5000)
            except LeadError as exc:
                return f"refused: {exc}"
            return "not refused"

        def later(**fields: object) -> Callable[[], str]:
            return window(with_total=False, **fields)

        def one(field: str, operator: str, value: object = None) -> dict[str, object]:
            condition = {"kind": "condition", "field": field, "operator": operator, "value": value}
            return {"kind": "group", "join": "and", "negate": False, "children": [condition]}

        def narrow() -> str:
            prefix = one("name", "starts_with", "synthetic scale lead 000012")
            _, ids = explorer.matching_ids(conn, _query(filters=prefix), now, 5000)
            return f"{len(ids)} leads resolved"

        unindexed = [{"field": "contactability", "direction": "desc"}]
        lead_score = [{"field": "total_score", "direction": "desc"}]
        named = one("name", "contains", "Scale Lead 00012")
        contactable = one("suppressed", "is_false")
        tagged = one("tag", "has", "synthetic-shortlist")
        cases: list[tuple[str, Callable[[], str]]] = [
            ("clone + rank", clone),
            ("first window, rank order, with total (first call)", window()),
            ("first window, rank order, with total", window()),
            ("deepest window, rank order", later(offset=rows - 100)),
            ("first window, filters + 2 sorts, with total", window(filters=filters, sort=sort)),
            ("next window, filters + 2 sorts", later(filters=filters, sort=sort, offset=100)),
            ("filters + 2 sorts at 100 000", later(filters=filters, sort=sort, offset=100_000)),
            ("first window, unindexed sort, with total", window(sort=unindexed)),
            ("unindexed sort at 500 000", later(sort=unindexed, offset=500_000)),
            ("lead score at 500 000", later(sort=lead_score, offset=500_000)),
            ("selective name, with total", window(filters=named)),
            ("off the do-not-contact list, with total", window(filters=contactable)),
            ("tag, with total", window(filters=tagged)),
            ("export preview (counts + sample)", export),
            ("bulk over the whole query", refused),
            ("bulk over a narrow query", narrow),
        ]
        for name, action in cases:
            _measure(name, action)
        conn.close()
        size = sum(path.stat().st_size for path in data_dir.glob("*.sqlite3*"))
        print(f"{'store size':<46} {size / MIB:>9.1f} MiB")
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
