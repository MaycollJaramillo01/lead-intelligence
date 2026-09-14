"""The Lead Explorer, the research workspace, sales briefs and the funnel against a real store
(phase 12): an index that pages 100 000 leads without loading them, filters that match a reference
evaluation, versioned views, criteria that gate sales-ready, audited corrections, the do-not-contact
list everywhere, and briefs a model may only reword (synthetic test data)."""

import sqlite3
import time
import tracemalloc
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any, Final

import orjson
import pytest
from pydantic import ValidationError

from research_engine import repository
from research_engine.ai import engine as ai_engine
from research_engine.ai import inputs, tasks
from research_engine.ai import store as ai_store
from research_engine.ai.engine import AiService
from research_engine.ai.resources import ResourceMonitor, Sample
from research_engine.contacts import store as contacts_store
from research_engine.contracts import ComponentState, GpuStatus, MessageType
from research_engine.domain import (
    STALE_AFTER_DAYS,
    AiConfigureRequest,
    AiProfile,
    AiProfileChange,
    AiStartRequest,
    AiTaskChange,
    AiTaskType,
    AttemptResult,
    BriefSection,
    BulkAction,
    BulkMode,
    BulkRequest,
    BulkResult,
    Business,
    CalibrationQuery,
    CanonicalField,
    ChecklistCatalogQuery,
    ChecklistChangeRequest,
    ChecklistSaveRequest,
    ContactQuality,
    ContactType,
    EvidenceQuery,
    EvidenceRefKind,
    ExplorerField,
    ExplorerQuery,
    ExplorerRow,
    ExportPreviewRequest,
    LeadAction,
    LeadActionRequest,
    LeadStage,
    LeadTier,
    LlmFitReport,
    ProblemType,
    RefreshScope,
    ResearchAction,
    ResearchActionRequest,
    ResearchStatus,
    ResearchTask,
    ResearchTaskKind,
    ResearchTaskStatus,
    SalesReadyCriterion,
    SavedView,
    SavedViewAction,
    SavedViewChangeRequest,
    SavedViewItem,
    SavedViewQuery,
    SavedViewSaveRequest,
    SavedViewVersion,
    ScoreRun,
    ScoringCatalogQuery,
    ScoringChangeAction,
    ScoringModelSaveRequest,
    SuppressionKind,
    SyntheticSeedResult,
    TimelineKind,
    TimelineQuery,
    ViewDefinition,
    format_ts,
    new_id,
    no_filters,
    utc_now,
)
from research_engine.handlers import ROUTES
from research_engine.leads import LeadError, brief, explorer, index, research, views
from research_engine.scoring import model as scoring_model
from research_engine.scoring import store as scoring_store
from research_engine.scoring.model import ScoringError
from research_engine.testdata import (
    SYNTHETIC_ICP_NAME,
    ScriptedProvider,
    _scored_run,
    clone_lead_index,
    seed_synthetic_workspace,
    synthetic_answer,
)

ACTOR: Final = "test"
TEXT_MODEL: Final = "synthetic-text:1b"
#: A seeded lead with a website, observed problems and an offer, whose only number is fictional.
TAX: Final = ("Synthetic Tax Preparation 0505", "Austin")


@pytest.fixture
def seeded(conn: sqlite3.Connection, data_dir: Path) -> SyntheticSeedResult:
    return seed_synthetic_workspace(conn, data_dir)


def _runs(conn: sqlite3.Connection) -> list[str]:
    return [row["id"] for row in conn.execute("SELECT id FROM score_runs ORDER BY created_at, id")]


def _count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])  # noqa: S608


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


def _when(field: str, operator: str, value: object = None) -> dict[str, object]:
    return {"kind": "condition", "field": field, "operator": operator, "value": value}


def _group(join: str, *children: object, negate: bool = False) -> dict[str, object]:
    return {"kind": "group", "join": join, "negate": negate, "children": list(children)}


def _rows(conn: sqlite3.Connection, **fields: object) -> list[ExplorerRow]:
    return explorer.explore(conn, _query(**fields), utc_now()).rows


def _lead(conn: sqlite3.Connection, name: str, locality: str | None = None) -> str:
    row = conn.execute(
        "SELECT business_id FROM lead_index WHERE display_name = ? AND (? IS NULL OR locality = ?) "
        "ORDER BY business_id LIMIT 1",
        (name, locality, locality),
    ).fetchone()
    assert row is not None, name
    return str(row["business_id"])


def _act(
    conn: sqlite3.Connection, business_id: str, action: ResearchAction, **fields: object
) -> list[research.RefreshPlan]:
    blank: dict[str, object] = dict.fromkeys(ResearchActionRequest.model_fields)
    request = ResearchActionRequest.model_validate(
        blank | {"business_id": business_id, "action": action, "scopes": []} | fields
    )
    return research.act(conn, request, utc_now(), actor=ACTOR)


def _failing(conn: sqlite3.Connection, business_id: str) -> set[SalesReadyCriterion]:
    workspace = research.workspace(conn, business_id, utc_now())
    return {check.criterion for check in workspace.sales_ready if not check.passed}


def _lenient_run(conn: sqlite3.Connection, project_id: str) -> str:
    """Scores the project again with thresholds the synthetic leads reach (test data)."""
    catalog = scoring_store.catalog(conn, ScoringCatalogQuery(project_id=project_id))
    profile = next(p for p in catalog.icp_profiles if p.profile.name == SYNTHETIC_ICP_NAME)
    lenient = scoring_model.default_scoring().model_copy(
        update={"priority_threshold": 40, "review_threshold": 30}
    )
    request = ScoringModelSaveRequest(
        model_id=None, name="Lenient synthetic model", description=None, definition=lenient
    )
    model_id = scoring_store.save_model(conn, request, actor=ACTOR)
    version = scoring_store.model_view(conn, model_id).versions[0].id
    icp_version = str(profile.profile.active_version_id)
    return _scored_run(conn, project_id, icp_version, version, None)[0]


# ---- the index and its queries ------------------------------------------------------------------


def test_the_index_mirrors_every_stored_score_ranks_finished_runs_and_is_rebuilt(
    conn: sqlite3.Connection, seeded: SyntheticSeedResult
) -> None:
    for run_id in _runs(conn):
        (scored,) = conn.execute(
            "SELECT count(*) FROM business_scores WHERE score_run_id = ?", (run_id,)
        ).fetchone()
        ranks = [
            row["rank"]
            for row in conn.execute(
                "SELECT rank FROM lead_index WHERE score_run_id = ? ORDER BY rank", (run_id,)
            )
        ]
        assert ranks == list(range(1, scored + 1))
    before = [(row.business_id, row.rank) for row in _rows(conn)]
    assert [rank for _, rank in before] == list(range(1, 39))  # the newest run, in its rank order

    conn.execute("DELETE FROM lead_index WHERE score_run_id = ?", (_runs(conn)[-1],))
    index._complete.clear()
    assert [(row.business_id, row.rank) for row in _rows(conn)] == before  # projected on read

    gone = before[0][0]
    repository.soft_delete(conn, Business, gone, actor=ACTOR, reason="A duplicate (test)")
    page = explorer.explore(conn, _query(), utc_now())
    assert page.total == 37
    assert gone not in {row.business_id for row in page.rows}


def test_filters_sorts_and_windows_match_a_reference_evaluation(
    conn: sqlite3.Connection, seeded: SyntheticSeedResult
) -> None:
    every = _rows(conn)
    assert len(every) == 38
    latino = {"verified_latino_owned", "probable_latino_owned"}

    def unmatched(row: ExplorerRow) -> bool:
        low = row.total_score is not None and row.total_score < 30
        return not (ProblemType.NO_WEBSITE in row.problems or low)

    cases: list[tuple[dict[str, object], Callable[[ExplorerRow], bool]]] = [
        (
            _group(
                "and",
                _when("tier", "in", ["low", "insufficient_data"]),
                _when("total_score", "gt", 40.0),
            ),
            lambda r: r.tier.value in {"low", "insufficient_data"} and (r.total_score or 0) > 40,
        ),
        (
            _group("or", _when("region", "eq", "tx"), _when("latino_status", "in", sorted(latino))),
            lambda r: (r.region or "").lower() == "tx" or r.latino_status.value in latino,
        ),
        (
            _group(
                "and",
                _when("tier", "neq", "excluded"),
                _group(
                    "or",
                    _when("problem", "has", "no_website"),
                    _when("total_score", "lt", 30.0),
                    negate=True,
                ),
            ),
            lambda r: r.tier is not LeadTier.EXCLUDED and unmatched(r),
        ),
        (
            # a negated test on a missing total keeps the lead: the test did not match it
            _group(
                "and",
                _group(
                    "or",
                    _when("problem", "has", "no_website"),
                    _when("total_score", "lt", 30.0),
                    negate=True,
                ),
            ),
            unmatched,
        ),
        (
            _group("and", _when("name", "contains", "PLUMBING")),
            lambda r: "plumbing" in r.display_name.lower(),
        ),
        (
            _group("and", _when("name", "starts_with", "synthetic tax")),
            lambda r: r.display_name.lower().startswith("synthetic tax"),
        ),
        (_group("and", _when("total_score", "is_null")), lambda r: r.total_score is None),
        (_group("and", _group("or"), _group("and", _group("or"))), lambda r: True),
    ]
    for filters, expected in cases:
        page = explorer.explore(conn, _query(filters=filters), utc_now())
        wanted = {row.business_id for row in every if expected(row)}
        assert {row.business_id for row in page.rows} == wanted, filters
        assert page.total == len(wanted)

    too_many = [_group("and", *[_when("total_score", "gt", 1.0)] * 20)] * 3
    for invalid in (
        _group("and", _when("tier", "gt", 1.0)),  # not an operator of an enum field
        _group("and", _when("tier", "eq", "gold")),  # not a value of the field
        _group("and", _when("total_score", "gt", "high")),  # a number field compares numbers
        _group("and", _when("suppressed", "is_true", True)),  # a unary test takes no value
        _group("and", _group("and", _group("and", _group("and")))),  # four levels deep
        _group("and", *too_many),  # sixty conditions
    ):
        with pytest.raises(ValidationError):
            _query(filters=invalid)

    sort = [{"field": "region", "direction": "asc"}, {"field": "total_score", "direction": "desc"}]
    order = [
        row.business_id
        for row in sorted(
            every,
            key=lambda r: (
                r.region is None,
                (r.region or "").lower(),
                r.total_score is None,
                -(r.total_score or 0.0),
                r.business_id,
            ),
        )
    ]
    assert [row.business_id for row in _rows(conn, sort=sort)] == order
    windows = [
        explorer.explore(
            conn, _query(sort=sort, offset=start, limit=7, with_total=start == 0), utc_now()
        )
        for start in range(0, 38, 7)
    ]
    assert [row.business_id for page in windows for row in page.rows] == order
    assert windows[0].total == 38
    assert all(page.total is None for page in windows[1:])  # later windows never count again

    # a selection is a set of ids: it survives another page and another order
    selected = [row.business_id for row in windows[1].rows]
    by_name = _rows(conn, sort=[{"field": "name", "direction": "desc"}])
    assert set(selected) <= {row.business_id for row in by_name}
    request = BulkRequest(
        mode=BulkMode.PREVIEW, action=BulkAction.ADD_TAG, business_ids=selected, query=None,
        expected=None, tag="picked", assignee=None, reason=None,
    )  # fmt: skip
    preview = research.bulk(conn, request, utc_now(), actor=ACTOR)
    assert (preview.targeted, preview.changed) == (7, 7)
    filters = _group("and", _when("tier", "neq", "excluded"))
    _, ranked = explorer.matching_ids(conn, _query(filters=filters), utc_now(), 5000)
    _, named = explorer.matching_ids(
        conn, _query(filters=filters, sort=[{"field": "name", "direction": "asc"}]), utc_now(), 5000
    )
    assert ranked != named
    assert set(ranked) == set(named)


def test_a_hundred_thousand_leads_are_paged_from_the_index_without_loading_them(
    conn: sqlite3.Connection, seeded: SyntheticSeedResult
) -> None:
    run_id = _runs(conn)[-1]
    clone_lead_index(conn, run_id, 100_000)
    bit = index.PROBLEM_BITS[ProblemType.NO_WEBSITE]
    expected = sum(
        1 for i in range(1, 100_001) if i % 4 in (0, 1) and (i % 3 == 0 or (i % 256) & bit)
    )
    filters = _group(
        "and",
        _when("tier", "in", ["priority", "review"]),
        _group("or", _when("region", "eq", "TX"), _when("problem", "has", "no_website")),
    )
    sort = [{"field": "total_score", "direction": "desc"}, {"field": "name", "direction": "asc"}]
    now = utc_now()

    tracemalloc.start()
    started = time.perf_counter()
    first = explorer.explore(conn, _query(filters=filters, sort=sort, limit=200), now)
    deep = explorer.explore(conn, _query(offset=99_900, limit=100, with_total=False), now)
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert first.total == expected
    totals = [row.total_score for row in first.rows]
    assert len(totals) == 200
    assert totals == sorted(totals, key=lambda total: -(total or 0.0))
    assert [row.rank for row in deep.rows] == list(range(99_901, 100_001))
    assert peak < 16 * 1024 * 1024  # two windows, never the 100 038 rows
    assert elapsed < 2  # the indexes answer; the rows of the run are never sorted

    with pytest.raises(LeadError, match="narrow"):
        explorer.matching_ids(conn, _query(), now, 5000)
    request = ExportPreviewRequest(
        query=_query(filters=filters), columns=[ExplorerField.NAME, ExplorerField.TOTAL_SCORE],
        include_suppressed=False, sample_size=20,
    )  # fmt: skip
    preview = explorer.export_preview(conn, request, now)
    assert (preview.matching, preview.exported, len(preview.sample)) == (expected, expected, 20)
    assert "synthetic_rows" in preview.warnings


def test_windows_are_found_in_indexes_and_never_sort_the_rows_of_a_run(
    conn: sqlite3.Connection, seeded: SyntheticSeedResult
) -> None:
    run = repository.get(conn, ScoreRun, _runs(conn)[-1])
    now = utc_now()

    def explain(statement: tuple[str, list[object]] | None) -> str:
        assert statement is not None
        sql, params = statement
        return " / ".join(row[-1] for row in conn.execute(f"EXPLAIN QUERY PLAN {sql}", params))

    def plans(**fields: object) -> tuple[str, str]:
        counted, ids = explorer._statements(_query(**fields), run, now)
        return explain(counted), explain(ids)

    # an order an index holds is walked through it
    for sort, walked in (
        ([], "lead_index_by_rank"),
        ([{"field": "total_score", "direction": "desc"}], "lead_index_by_total"),
        ([{"field": "name", "direction": "asc"}], "lead_index_by_name"),
    ):
        assert walked in plans(sort=sort)[1]
    assert "TEMP B-TREE" not in plans()[1]

    # a condition, a search or any other order is answered by the covering index alone
    narrowed = _group(
        "and", _when("tier", "in", ["priority", "review"]), _when("name", "contains", "bakery")
    )
    two_keys = [
        {"field": "total_score", "direction": "desc"},
        {"field": "name", "direction": "asc"},
    ]
    for fields in (
        {"filters": narrowed},
        {"filters": narrowed, "sort": two_keys},
        {"filters": _group("and", _when("suppressed", "is_false"))},
        {"filters": _group("and", _when("tag", "has", "shortlist"))},
        {"search": "bakery"},
    ):
        counted, ids = plans(**fields)
        assert "COVERING INDEX lead_index_scan" in counted, (fields, counted)
        assert "COVERING INDEX lead_index_scan" in ids, (fields, ids)
    unwalked = [{"field": "contactability", "direction": "desc"}]
    assert "COVERING INDEX lead_index_scan" in plans(sort=unwalked)[1]


# ---- saved views --------------------------------------------------------------------------------


def _definition(**fields: object) -> ViewDefinition:
    return ViewDefinition.model_validate(explorer.default_view().model_dump() | fields)


def _save(
    conn: sqlite3.Connection, name: str, definition: ViewDefinition, view_id: str | None = None
) -> SavedViewItem:
    request = SavedViewSaveRequest(
        view_id=view_id, scope="lead_explorer", name=name, description=None, definition=definition
    )
    return views.save_view(conn, request, actor=ACTOR)


def _change(conn: sqlite3.Connection, view_id: str, action: SavedViewAction) -> SavedViewItem:
    request = SavedViewChangeRequest(view_id=view_id, action=action, reason="Campaign (test)")
    return views.change_view(conn, request, actor=ACTOR)


def test_saved_views_are_versioned_archived_with_a_reason_and_upgraded_when_read(
    conn: sqlite3.Connection,
) -> None:
    first = _save(conn, "Priority in Texas", _definition())
    assert [version.version for version in first.versions] == [1]
    unchanged = _save(conn, "Priority in Texas", _definition(), first.view.id)
    assert unchanged.versions == first.versions  # the same definition makes no version
    by_total = _definition(sort=[{"field": "total_score", "direction": "desc"}])
    second = _save(conn, "Priority in Texas", by_total, first.view.id)
    assert [version.version for version in second.versions] == [2, 1]
    assert second.definition.sort[0].field is ExplorerField.TOTAL_SCORE
    with pytest.raises(LeadError, match="already exists"):
        _save(conn, "Priority in Texas", _definition())

    _change(conn, first.view.id, SavedViewAction.ARCHIVE)
    live = views.list_views(conn, SavedViewQuery(scope="lead_explorer", include_archived=False))
    assert live.items == []
    with pytest.raises(LeadError, match="restore"):
        _save(conn, "Priority in Texas", by_total, first.view.id)
    assert _change(conn, first.view.id, SavedViewAction.RESTORE).view.archived_at is None
    (audited,) = conn.execute(
        "SELECT count(*) FROM audit_log WHERE entity_type = 'saved_view' AND entity_id = ?",
        (first.view.id,),
    ).fetchone()
    assert audited == 5  # created, two versions, archived, restored

    now = utc_now()
    old = SavedView(
        id=new_id(), scope="lead_explorer", name="Saved by the first build", description=None,
        created_at=now, updated_at=now, archived_at=None,
    )  # fmt: skip
    schema_1: dict[str, Any] = {
        "schema_version": 1, "score_run_id": None, "search": "roofing",
        "conditions": [{"field": "tier", "operator": "eq", "value": "priority"},
                       {"field": "total_score", "operator": "gte", "value": 60.0}],
        "sort": {"field": "total_score", "direction": "desc"},
        "columns": ["rank", "name", "a_retired_column", "tier"],
    }  # fmt: skip
    repository.insert(
        conn,
        old,
        SavedViewVersion(
            id=new_id(), view_id=old.id, version=1, definition_json=schema_1, schema_version=1,
            created_by="first build", created_at=now,
        ),
    )  # fmt: skip
    item = views.get_view(conn, old.id)
    assert item.upgraded_from == 1
    assert [child.kind for child in item.definition.filters.children] == ["condition", "condition"]
    assert [column.field for column in item.definition.columns if column.visible] == [
        ExplorerField.RANK, ExplorerField.NAME, ExplorerField.TIER,
    ]  # fmt: skip
    assert len(item.definition.columns) == len(ExplorerField)
    assert (item.definition.search, item.definition.sort[0].field) == ("roofing", "total_score")
    again = _save(conn, old.name, item.definition, old.id)  # the next save appends version 2
    assert again.upgraded_from is None
    assert [version.schema_version for version in again.versions] == [2, 1]

    future = SavedView(
        id=new_id(), scope="lead_explorer", name="Saved by a newer build", description=None,
        created_at=now, updated_at=now, archived_at=None,
    )  # fmt: skip
    repository.insert(
        conn,
        future,
        SavedViewVersion(
            id=new_id(), view_id=future.id, version=1, definition_json={"schema_version": 3},
            schema_version=3, created_by="newer build", created_at=now,
        ),
    )  # fmt: skip
    with pytest.raises(LeadError, match="up to schema version 2"):
        views.get_view(conn, future.id)


# ---- the research workspace ---------------------------------------------------------------------


def test_a_lead_is_sales_ready_only_when_every_criterion_holds_and_the_change_can_be_undone(
    conn: sqlite3.Connection, seeded: SyntheticSeedResult
) -> None:
    lead = _lead(conn, *TAX)
    with pytest.raises(LeadError, match="qualifying_tier") as refused:
        _act(conn, lead, ResearchAction.SET_STATUS, status=ResearchStatus.SALES_READY,
             reason="Looks good (test)")  # fmt: skip
    assert "usable_contact" in str(refused.value)
    assert _failing(conn, lead) == {
        SalesReadyCriterion.QUALIFYING_TIER, SalesReadyCriterion.USABLE_CONTACT,
        SalesReadyCriterion.CHECKLIST_COMPLETE,
    }  # fmt: skip
    with pytest.raises(ValidationError):  # a status always carries a reason
        _act(conn, lead, ResearchAction.SET_STATUS, status=ResearchStatus.DISQUALIFIED)

    _lenient_run(conn, seeded.project_id)
    assert _failing(conn, lead) == {
        SalesReadyCriterion.USABLE_CONTACT, SalesReadyCriterion.CHECKLIST_COMPLETE,
    }  # fmt: skip
    _act(conn, lead, ResearchAction.CORRECT_VALUE, field=CanonicalField.EMAIL,
         value="owner@synthetic-tax-0505.example", reason="Confirmed on a call (test)")  # fmt: skip
    for item in ("offer_fit_confirmed", "contact_route_reviewed"):
        _act(conn, lead, ResearchAction.CHECK_ITEM, item_key=item)
    assert _failing(conn, lead) == set()
    later = utc_now() + timedelta(days=STALE_AFTER_DAYS + 1)
    stale = research.workspace(conn, lead, later).sales_ready
    assert SalesReadyCriterion.SCORED_RECENTLY in {c.criterion for c in stale if not c.passed}

    _act(conn, lead, ResearchAction.SET_STATUS, status=ResearchStatus.SALES_READY,
         reason="Every criterion holds (test)")  # fmt: skip
    workspace = research.workspace(conn, lead, utc_now())
    assert workspace.research is not None
    assert workspace.research.status is ResearchStatus.SALES_READY
    assert workspace.sales_ready_allowed
    assert workspace.can_undo
    (row,) = _rows(conn, filters=_group("and", _when("research_status", "eq", "sales_ready")))
    assert row.business_id == lead

    _act(conn, lead, ResearchAction.UNDO_STATUS, reason="Marked too early (test)")
    undone = research.workspace(conn, lead, utc_now())
    assert undone.research is not None
    assert undone.research.status is None
    changes = [
        (d.previous_value, d.new_value) for d in undone.decisions if d.decision == "research_status"
    ]
    assert changes == [("sales_ready", None), (None, "sales_ready")]


def test_a_correction_is_an_audited_observation_that_never_edits_a_source(
    conn: sqlite3.Connection, seeded: SyntheticSeedResult
) -> None:
    lead = _lead(conn, "Synthetic Painting 0702")

    def points() -> list[tuple[Any, ...]]:
        return [
            tuple(row)
            for row in conn.execute(
                "SELECT o.id, o.value_raw FROM contact_points o JOIN businesses b "
                "ON b.id = o.business_id WHERE coalesce(b.merged_into_id, b.id) = ? ORDER BY o.id",
                (lead,),
            )
        ]

    assert SalesReadyCriterion.CONTACT_CONFLICTS_RESOLVED in _failing(conn, lead)
    before, records = points(), _count(conn, "raw_records")
    with pytest.raises(LeadError, match="phone"):
        _act(conn, lead, ResearchAction.CORRECT_VALUE, field=CanonicalField.PHONE,
             value="call the office", reason="A typo (test)")  # fmt: skip
    assert (points(), _count(conn, "raw_records")) == (before, records)  # refused: nothing written

    _act(conn, lead, ResearchAction.CORRECT_VALUE, field=CanonicalField.PHONE,
         value="(312) 555-0199", reason="The owner confirmed the new number (test)")  # fmt: skip
    assert set(before) < set(points())  # every source row is kept; the correction is one more
    (correction,) = research.workspace(conn, lead, utc_now()).corrections
    assert (correction.field, correction.shown, correction.actor) == (
        CanonicalField.PHONE,
        True,
        ACTOR,
    )
    assert correction.reason == "The owner confirmed the new number (test)"
    assert SalesReadyCriterion.CONTACT_CONFLICTS_RESOLVED not in _failing(conn, lead)
    (manual,) = conn.execute(
        "SELECT r.method FROM raw_records r JOIN source_connectors sc ON sc.id = r.connector_id "
        "WHERE sc.key = 'analyst.corrections'"
    ).fetchone()
    assert (manual, _count(conn, "raw_records")) == ("manual_entry", records + 1)
    changes = research.timeline(
        conn, TimelineQuery(business_id=lead, kinds=[TimelineKind.CHANGE], limit=50, cursor=None)
    )
    assert any(entry.title == "Correction updated" for entry in changes.entries)


def test_pins_tasks_notes_and_checks_keep_their_history_and_the_timeline_pages(
    conn: sqlite3.Connection, seeded: SyntheticSeedResult
) -> None:
    lead, other = _lead(conn, *TAX), _lead(conn, "Synthetic Painting 0702")
    _act(conn, lead, ResearchAction.PIN, ref_kind=EvidenceRefKind.BUSINESS, ref=lead,
         note="The owner answers (test)")  # fmt: skip
    with pytest.raises(LeadError, match="already pinned"):
        _act(conn, lead, ResearchAction.PIN, ref_kind=EvidenceRefKind.BUSINESS, ref=lead)
    with pytest.raises(
        repository.NotFoundError
    ):  # another business's row is no evidence of this one
        _act(conn, lead, ResearchAction.PIN, ref_kind=EvidenceRefKind.BUSINESS, ref=other)
    (pin,) = research.workspace(conn, lead, utc_now()).pins
    assert pin.available
    assert pin.pin.note == "The owner answers (test)"
    _act(conn, lead, ResearchAction.UNPIN, ref_kind=EvidenceRefKind.BUSINESS, ref=lead,
         reason="Not relevant (test)")  # fmt: skip
    assert research.workspace(conn, lead, utc_now()).pins == []
    pins = conn.execute("SELECT count(*) FROM evidence_pins WHERE business_id = ?", (lead,))
    assert pins.fetchone()[0] == 1  # unpinned, never deleted

    def open_tasks() -> list[ResearchTask]:
        tasks_now = research.workspace(conn, lead, utc_now()).tasks
        return [task for task in tasks_now if task.status is ResearchTaskStatus.OPEN]

    _act(conn, lead, ResearchAction.SYNC_TASKS)
    opened = open_tasks()
    assert opened
    assert {task.kind for task in opened} == {
        ResearchTaskKind.MISSING_DATA, ResearchTaskKind.CHECKLIST,
    }  # fmt: skip
    _act(conn, lead, ResearchAction.SYNC_TASKS)
    assert len(open_tasks()) == len(opened)  # nothing is opened twice
    task = opened[0]
    _act(conn, lead, ResearchAction.COMPLETE_TASK, task_id=task.id)
    with pytest.raises(LeadError, match="only an open task"):
        _act(conn, lead, ResearchAction.DISMISS_TASK, task_id=task.id, reason="Duplicate (test)")
    _act(conn, lead, ResearchAction.REOPEN_TASK, task_id=task.id)
    _act(conn, lead, ResearchAction.ADD_TASK, title="Ask for the owner's email (test)")
    assert len(open_tasks()) == len(opened) + 1

    _act(conn, lead, ResearchAction.ADD_NOTE, note="Prefers calls after lunch (test)")
    (note,) = research.workspace(conn, lead, utc_now()).notes
    _act(conn, lead, ResearchAction.DELETE_NOTE, note_id=note.id, reason="Wrong lead (test)")
    assert research.workspace(conn, lead, utc_now()).notes == []

    with pytest.raises(LeadError, match="automatic"):
        _act(conn, lead, ResearchAction.CHECK_ITEM, item_key="scored_recently")
    _act(conn, lead, ResearchAction.CHECK_ITEM, item_key="offer_fit_confirmed",
         note="Sells tax services (test)")  # fmt: skip
    _act(conn, lead, ResearchAction.UNCHECK_ITEM, item_key="offer_fit_confirmed",
         reason="The offer changed (test)")  # fmt: skip
    state = next(
        item
        for item in research.workspace(conn, lead, utc_now()).checklist.items
        if item.item.key == "offer_fit_confirmed"
    )
    assert (state.done, state.decided_by) == (False, ACTOR)
    checks = conn.execute("SELECT count(*) FROM research_checks WHERE business_id = ?", (lead,))
    assert checks.fetchone()[0] == 2  # both decisions are kept

    everything = research.timeline(
        conn, TimelineQuery(business_id=lead, kinds=[], limit=500, cursor=None)
    )
    keys = [entry.key for entry in everything.entries]
    assert everything.next_cursor is None
    assert len(keys) == len(set(keys))
    stamps = [(format_ts(entry.at), entry.key) for entry in everything.entries]
    assert stamps == sorted(stamps, reverse=True)
    assert {entry.kind for entry in everything.entries} >= {
        TimelineKind.SOURCE, TimelineKind.CHANGE, TimelineKind.ANALYSIS, TimelineKind.RESEARCH,
    }  # fmt: skip
    paged: list[str] = []
    cursor: str | None = None
    while True:
        page = research.timeline(
            conn, TimelineQuery(business_id=lead, kinds=[], limit=4, cursor=cursor)
        )
        paged += [entry.key for entry in page.entries]
        if page.next_cursor is None:
            break
        cursor = page.next_cursor
    assert paged == keys


def test_checklists_are_versioned_and_matched_by_category_before_the_default(
    conn: sqlite3.Connection, seeded: SyntheticSeedResult
) -> None:
    lead, category = conn.execute(
        "SELECT business_id, primary_category FROM lead_index WHERE primary_category IS NOT NULL "
        "ORDER BY business_id LIMIT 1"
    ).fetchone()
    assert research.workspace(conn, lead, utc_now()).checklist.matched_by == "default"
    template = research.checklist_catalog(
        conn, ChecklistCatalogQuery(include_archived=False)
    ).template
    short = template.model_copy(update={"categories": [category], "items": template.items[:3]})
    request = ChecklistSaveRequest(
        checklist_id=None, name="Vertical checklist (test)", description=None, definition=short
    )
    checklist_id = research.save_checklist(conn, request, actor=ACTOR)
    state = research.workspace(conn, lead, utc_now()).checklist
    assert (state.checklist_id, state.matched_by, len(state.items)) == (checklist_id, "category", 3)

    longer = short.model_copy(update={"items": template.items[:4]})
    update = request.model_copy(update={"checklist_id": checklist_id, "definition": longer})
    assert research.save_checklist(conn, update, actor=ACTOR) == checklist_id
    assert research.workspace(conn, lead, utc_now()).checklist.version == 1  # waits for activation
    catalog = research.checklist_catalog(conn, ChecklistCatalogQuery(include_archived=False))
    view = next(item for item in catalog.checklists if item.checklist.id == checklist_id)
    assert [version.version for version in view.versions] == [2, 1]
    research.change_checklist(conn, ChecklistChangeRequest(
        checklist_id=checklist_id, action=ScoringChangeAction.ACTIVATE,
        version_id=view.versions[0].id, reason="Adds a check (test)",
    ), actor=ACTOR)  # fmt: skip
    assert len(research.workspace(conn, lead, utc_now()).checklist.items) == 4
    research.change_checklist(conn, ChecklistChangeRequest(
        checklist_id=checklist_id, action=ScoringChangeAction.ARCHIVE, version_id=None,
        reason="Retired (test)",
    ), actor=ACTOR)  # fmt: skip
    assert research.workspace(conn, lead, utc_now()).checklist.matched_by == "default"


def test_a_refresh_opens_one_task_per_scope_and_records_the_jobs_it_started(
    conn: sqlite3.Connection, seeded: SyntheticSeedResult
) -> None:
    lead = _lead(conn, *TAX)
    scopes = [RefreshScope.PAGES, RefreshScope.CONTACTS]
    plans = _act(conn, lead, ResearchAction.REQUEST_REFRESH, scopes=scopes, reason="Moved (test)")
    assert [plan.scope for plan in plans] == scopes
    assert plans[0].urls
    assert not plans[1].urls
    again = _act(conn, lead, ResearchAction.REQUEST_REFRESH, scopes=[RefreshScope.PAGES])
    assert again[0].task_id == plans[0].task_id  # the open task is reused
    job = new_id()
    research.attach_jobs(conn, plans[0].task_id, [job])
    research.attach_jobs(conn, plans[0].task_id, [job])
    task = repository.get(conn, ResearchTask, plans[0].task_id)
    assert task.detail_json is not None
    assert task.detail_json["jobs"] == [job]
    no_site = _lead(conn, "Synthetic Auto Repair 0501")
    with pytest.raises(LeadError, match="no website"):
        _act(conn, no_site, ResearchAction.REQUEST_REFRESH, scopes=[RefreshScope.PAGES])


def _bulk(
    conn: sqlite3.Connection, mode: BulkMode, action: BulkAction, **fields: object
) -> BulkResult:
    base: dict[str, object] = {
        "mode": mode, "action": action, "business_ids": [], "query": None, "expected": None,
        "tag": None, "assignee": None, "reason": None,
    }  # fmt: skip
    return research.bulk(conn, BulkRequest.model_validate(base | fields), utc_now(), actor=ACTOR)


def test_bulk_actions_are_previewed_then_applied_lead_by_lead_and_skip_what_they_cannot_change(
    conn: sqlite3.Connection, seeded: SyntheticSeedResult, monkeypatch: pytest.MonkeyPatch
) -> None:
    unresearched = _group("and", _when("research_status", "is_null"))
    ids = [row.business_id for row in _rows(conn, filters=unresearched, limit=5)]
    hot = _query(filters=_group("and", _when("tag", "has", "hot")))
    preview = _bulk(conn, BulkMode.PREVIEW, BulkAction.ADD_TAG, business_ids=ids, tag="hot")
    assert (preview.targeted, preview.changed, preview.batch_id) == (5, 5, None)
    assert explorer.explore(conn, hot, utc_now()).total == 0  # a preview writes nothing
    applied = _bulk(conn, BulkMode.APPLY, BulkAction.ADD_TAG, business_ids=ids, tag="hot",
                    reason="Campaign list (test)")  # fmt: skip
    assert applied.changed == 5
    assert applied.batch_id is not None
    assert explorer.explore(conn, hot, utc_now()).total == 5
    (audited,) = conn.execute(
        "SELECT count(*) FROM audit_log WHERE json_extract(detail_json, '$.batch_id') = ?",
        (applied.batch_id,),
    ).fetchone()
    assert audited == 5  # one audited change per lead
    again = _bulk(conn, BulkMode.APPLY, BulkAction.ADD_TAG, business_ids=ids, tag="HOT",
                  reason="Again (test)")  # fmt: skip
    assert (again.changed, again.skipped, {skip.reason for skip in again.skips}) == (
        0, 5, {"unchanged"},
    )  # fmt: skip

    analyst = "Analyst (test)"
    with pytest.raises(ValidationError):  # applying over a query needs the count previewed
        _bulk(conn, BulkMode.APPLY, BulkAction.ASSIGN, query=hot, assignee=analyst, reason="t")
    assert (
        _bulk(conn, BulkMode.PREVIEW, BulkAction.ASSIGN, query=hot, assignee=analyst).changed == 5
    )
    with pytest.raises(LeadError, match="preview again"):
        _bulk(conn, BulkMode.APPLY, BulkAction.ASSIGN, query=hot, expected=4, assignee=analyst,
              reason="Owner of the list (test)")  # fmt: skip
    assigned = _bulk(conn, BulkMode.APPLY, BulkAction.ASSIGN, query=hot, expected=5,
                     assignee=analyst, reason="Owner of the list (test)")  # fmt: skip
    assert assigned.changed == 5
    assert {row.assignee for row in explorer.explore(conn, hot, utc_now()).rows} == {analyst}

    monkeypatch.setattr(research, "BULK_MAX_MATCHING", 3)
    with pytest.raises(LeadError, match="narrow"):
        _bulk(conn, BulkMode.PREVIEW, BulkAction.UNASSIGN, query=hot)

    contacts_store.add_suppression(
        conn, SuppressionKind.BUSINESS, ids[0], business_id=None,
        reason="Asked not to be contacted (test)", source="manual", actor=ACTOR, now=utc_now(),
    )  # fmt: skip
    flagged = _bulk(conn, BulkMode.APPLY, BulkAction.NEEDS_RESEARCH, business_ids=ids,
                    reason="Find the owners (test)")  # fmt: skip
    assert (flagged.changed, [(skip.business_id, skip.reason) for skip in flagged.skips]) == (
        4, [(ids[0], "suppressed")],
    )  # fmt: skip
    assert _bulk(conn, BulkMode.PREVIEW, BulkAction.DISQUALIFY, business_ids=ids).changed == 5


def test_the_do_not_contact_list_is_shown_filtered_and_respected_everywhere(
    conn: sqlite3.Connection, data_dir: Path, seeded: SyntheticSeedResult
) -> None:
    lead = _lead(conn, *TAX)
    contacts_store.add_suppression(
        conn, SuppressionKind.BUSINESS, lead, business_id=None,
        reason="Asked not to be contacted (test)", source="manual", actor=ACTOR, now=utc_now(),
    )  # fmt: skip
    (row,) = _rows(conn, filters=_group("and", _when("suppressed", "is_true")))
    assert (row.business_id, row.suppressed) == (lead, True)

    def preview(include: bool) -> Any:
        request = ExportPreviewRequest(
            query=_query(), columns=[ExplorerField.NAME], include_suppressed=include, sample_size=50
        )
        return explorer.export_preview(conn, request, utc_now())

    left_out = preview(False)
    assert (left_out.matching, left_out.excluded_suppressed, left_out.exported) == (38, 1, 37)
    assert lead not in {sample.business_id for sample in left_out.sample}
    assert "hypotheses_never_exported" in left_out.warnings
    included = preview(True)
    assert included.exported == 38
    assert "suppressed_included" in included.warnings

    assert research.workspace(conn, lead, utc_now()).suppressed
    assert {SalesReadyCriterion.NOT_SUPPRESSED, SalesReadyCriterion.USABLE_CONTACT} <= _failing(
        conn, lead
    )
    sales = brief.build(conn, lead, None, utc_now())
    (route,) = next(
        view for view in sales.sections if view.section is BriefSection.CONTACT_ROUTE
    ).claims
    assert sales.suppressed
    assert "do-not-contact" in route.text
    pages = inputs.project_pages(conn, seeded.project_id)
    assert inputs.prepare(conn, data_dir, lead, AiTaskType.SALES_BRIEF, pages) is None


# ---- sales briefs -------------------------------------------------------------------------------


@pytest.fixture
def no_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    status = GpuStatus(state=ComponentState.UNAVAILABLE, summary="no GPU in tests", error="none",
                       checked_at=utc_now(), duration_ms=0.0, devices=[], cuda_available=False,
                       cuda_driver_version=None)  # fmt: skip
    sample = Sample(16_000, 8000, 8192, 7000, utc_now())
    monkeypatch.setattr(ai_engine, "probe_gpu", lambda: status)
    monkeypatch.setattr(ai_engine, "take_sample", lambda: sample)


def _service(data_dir: Path) -> AiService:
    report = LlmFitReport(available=False, version=None, models=[], error="not installed",
                          checked_at=utc_now())  # fmt: skip
    sample = Sample(16_000, 8000, 8192, 7000, utc_now())
    return AiService(data_dir, lambda: repository.connect(data_dir),
                     ScriptedProvider(synthetic_answer), ResourceMonitor(sampler=lambda: sample),
                     downloads_allowed=False, llm_fit=lambda: report)  # fmt: skip


def test_every_claim_of_a_brief_resolves_to_a_stored_row_and_says_nothing_offensive(
    conn: sqlite3.Connection, seeded: SyntheticSeedResult
) -> None:
    lead = _lead(conn, *TAX)
    _lenient_run(conn, seeded.project_id)
    _act(conn, lead, ResearchAction.PIN, ref_kind=EvidenceRefKind.BUSINESS, ref=lead)
    now = utc_now()
    sales = brief.build(conn, lead, None, now)
    assert [view.section for view in sales.sections] == list(BriefSection)
    claims = [claim for view in sales.sections for claim in view.claims]
    assert [claim.ref for claim in claims] == [f"B{number}" for number in range(1, len(claims) + 1)]
    for view in sales.sections:
        assert bool(view.claims) != bool(view.empty_reason)  # a section without data says why
    for claim in claims:
        assert claim.evidence
        assert tasks.accusatory_word(claim.text) is None
        for ref in claim.evidence:
            query = EvidenceQuery(business_id=lead, kind=ref.kind, ref=ref.ref)
            assert research.evidence_detail(conn, query, now).ref == ref.ref
    offer = next(view for view in sales.sections if view.section is BriefSection.RECOMMENDED_OFFER)
    assert all(claim.is_inference for claim in offer.claims)
    assert sales.versions is not None
    assert sales.last_verified_at is not None
    other = _lead(conn, "Synthetic Painting 0702")
    foreign = EvidenceQuery(business_id=lead, kind=EvidenceRefKind.BUSINESS, ref=other)
    with pytest.raises(repository.NotFoundError):
        research.evidence_detail(conn, foreign, now)


@pytest.mark.anyio
@pytest.mark.usefixtures("no_gpu")
async def test_a_model_may_only_reword_the_claims_of_a_brief(
    conn: sqlite3.Connection, data_dir: Path, seeded: SyntheticSeedResult
) -> None:
    lead = _lead(conn, *TAX)
    _act(conn, lead, ResearchAction.PIN, ref_kind=EvidenceRefKind.BUSINESS, ref=lead,
         note="The owner answers (test)")  # fmt: skip
    prepared = inputs.prepare(
        conn, data_dir, lead, AiTaskType.SALES_BRIEF, inputs.project_pages(conn, seeded.project_id)
    )
    assert prepared is not None
    first = prepared.refs[0]
    section, _, claim = first.text.partition(": ")

    def checked(*sections: tuple[str, str, list[str]]) -> tuple[str, ...]:
        answer = {"outcome": "ok", "sections": [
            {"section": where, "sentences": [{"text": text, "evidence_ids": cited}]}
            for where, text, cited in sections
        ]}  # fmt: skip
        return tasks.check_answer(orjson.dumps(answer).decode(), prepared).errors

    assert checked((section, claim, [first.ref])) == ()
    assert "unknown_evidence_id:B99" in checked((section, claim, ["B99"]))
    assert "accusatory_language:sloppy" in checked(
        (section, "Their website is sloppy.", [first.ref])
    )
    assert "number_not_in_evidence:37" in checked(
        (section, "They lose 37 customers a week.", [first.ref])
    )
    assert "section_repeated" in checked(
        (section, claim, [first.ref]), (section, claim, [first.ref])
    )

    with pytest.raises(ValidationError, match="lead by lead"):
        AiStartRequest(
            project_id=seeded.project_id, business_ids=[], task_types=[AiTaskType.SALES_BRIEF]
        )
    service = _service(data_dir)
    project_jobs, _ = service.plan(conn, seeded.project_id)
    (params,) = conn.execute(
        "SELECT params_json FROM jobs WHERE id = ?", (project_jobs[0],)
    ).fetchone()
    assert "sales_brief" not in orjson.loads(params)["task_types"]  # asked lead by lead only
    await service.configure(AiConfigureRequest(
        profiles=[AiProfileChange(profile=p, model=TEXT_MODEL, num_ctx=4096, allow_cpu=True)
                  for p in AiProfile],
        tasks=[AiTaskChange(task_type=t, enabled=True, profile=AiProfile.FAST) for t in AiTaskType],
        min_free_ram_mib=1024, min_free_vram_mib=512,
    ))  # fmt: skip
    started = await service.start(
        AiStartRequest(project_id=None, business_ids=[lead], task_types=[AiTaskType.SALES_BRIEF])
    )
    await service.idle(started.items[0].job_id)
    assert ai_store.get_run(conn, started.items[0].job_id).stats.succeeded == 1

    drafted = brief.build(conn, lead, None, utc_now())
    assert drafted.draft is not None
    assert drafted.draft.dropped == 0
    claims = {claim.ref for view in drafted.sections for claim in view.claims}
    sentences = [sentence for part in drafted.draft.sections for sentence in part.sentences]
    assert sentences
    assert all(set(sentence.claims) <= claims and sentence.evidence for sentence in sentences)

    _act(conn, lead, ResearchAction.UNPIN, ref_kind=EvidenceRefKind.BUSINESS, ref=lead,
         reason="Not relevant (test)")  # fmt: skip
    reworded = brief.build(conn, lead, None, utc_now()).draft
    assert reworded is not None
    assert reworded.dropped == 1  # the sentence that restated the pin no longer has its claim
    assert BriefSection.EVIDENCE not in {part.section for part in reworded.sections}
    await service.shutdown()


# ---- the funnel ---------------------------------------------------------------------------------


def _work(
    conn: sqlite3.Connection, run_id: str, lead: str, action: LeadAction, **fields: object
) -> None:
    blank: dict[str, object] = dict.fromkeys(LeadActionRequest.model_fields)
    request = LeadActionRequest.model_validate(
        blank
        | {"score_run_id": run_id, "business_id": lead, "action": action, "reason": "t"}
        | fields
    )
    scoring_store.act(conn, request, actor=ACTOR)


def test_attempts_and_outcomes_are_recorded_in_order_and_never_touch_a_score(
    conn: sqlite3.Connection, seeded: SyntheticSeedResult
) -> None:
    run_id, lead = _runs(conn)[0], _lead(conn, *TAX)

    def scores() -> list[tuple[Any, ...]]:
        return [
            tuple(row)
            for row in conn.execute(
                "SELECT score_run_id, facts_json, scorecard_json FROM business_scores "
                "WHERE business_id = ? ORDER BY score_run_id",
                (lead,),
            )
        ]

    snapshot = scores()
    labeled = scoring_store.calibration(conn, CalibrationQuery(score_run_id=run_id)).labeled
    phone, no_answer = ContactType.PHONE, AttemptResult.NO_ANSWER
    for invalid in (
        {"channel": phone},  # an attempt names its channel and its result
        {"stage": LeadStage.CONNECTED, "channel": phone, "attempt_result": AttemptResult.REACHED},
        {"channel": phone, "attempt_result": no_answer, "value_amount": 100.0},  # no currency
        {
            "channel": phone,
            "attempt_result": no_answer,
            "occurred_at": utc_now() + timedelta(days=1),
        },
    ):
        with pytest.raises(ScoringError):
            _work(conn, run_id, lead, LeadAction.RECORD_ATTEMPT, **invalid)

    start = utc_now() - timedelta(hours=3)
    _work(conn, run_id, lead, LeadAction.RECORD_ATTEMPT, channel=phone, attempt_result=no_answer,
          occurred_at=start)  # fmt: skip
    assert scoring_store.calibration(conn, CalibrationQuery(score_run_id=run_id)).labeled == labeled
    _work(conn, run_id, lead, LeadAction.RECORD_OUTCOME, stage=LeadStage.ATTEMPTED, channel=phone,
          attempt_result=AttemptResult.LEFT_MESSAGE,
          occurred_at=start + timedelta(hours=1))  # fmt: skip
    _work(conn, run_id, lead, LeadAction.RECORD_OUTCOME, stage=LeadStage.CONNECTED, channel=phone,
          attempt_result=AttemptResult.MEETING_SET, contact_quality=ContactQuality.DECISION_MAKER,
          value_amount=1200.0, value_currency="USD", notes="Wants a quote (test)",
          occurred_at=start + timedelta(hours=2))  # fmt: skip
    with pytest.raises(ScoringError, match="latest outcome"):
        _work(conn, run_id, lead, LeadAction.RECORD_ATTEMPT, channel=ContactType.EMAIL,
              attempt_result=AttemptResult.REPLIED,
              occurred_at=start + timedelta(minutes=90))  # fmt: skip

    recorded = conn.execute(
        "SELECT stage, previous_stage, attempt_result, value_amount, value_currency, "
        "contact_quality FROM lead_outcomes WHERE business_id = ? ORDER BY occurred_at",
        (lead,),
    ).fetchall()
    assert [tuple(row) for row in recorded] == [
        ("not_contacted", "not_contacted", "no_answer", None, None, None),
        ("attempted", "not_contacted", "left_message", None, None, None),
        ("connected", "attempted", "meeting_set", 1200.0, "USD", "decision_maker"),
    ]
    assert scores() == snapshot  # the scores the lead was worked on stay as they were
    outcomes = research.timeline(
        conn, TimelineQuery(business_id=lead, kinds=[TimelineKind.OUTCOME], limit=10, cursor=None)
    )
    assert [entry.code for entry in outcomes.entries] == ["outcome", "outcome", "attempt"]


def test_every_phase_12_message_has_a_route() -> None:
    prefixes = ("leads.", "views.", "research.")
    messages = {message for message in MessageType if message.value.startswith(prefixes)}
    assert len(messages) == 16
    assert messages <= set(ROUTES)
