"""Lead scoring against a real store: versions as history, runs that resume, simulations that write
nothing, the rank a run keeps, outcomes and calibration without leakage, and what a local model may
and may not do to a scorecard (phase 11)."""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import orjson
import pytest

from research_engine import repository
from research_engine.contracts import MessageType
from research_engine.domain import (
    AiDevice,
    AiProfile,
    AiReviewStatus,
    AiTaskType,
    CalibrationQuery,
    IcpSaveRequest,
    InferenceOutput,
    InferenceRun,
    InferenceStatus,
    JobStatus,
    LatinoRequirement,
    LeadAction,
    LeadActionRequest,
    LeadOutcome,
    LeadScorecard,
    LeadScoreQuery,
    LeadStage,
    LeadTier,
    OpportunityProblem,
    ScorecardQuery,
    ScoreCompareQuery,
    ScoreEvidenceKind,
    ScoringCatalogQuery,
    ScoringChangeAction,
    ScoringChangeRequest,
    ScoringModelSaveRequest,
    ScoringSimulationRequest,
    ScoringStartRequest,
    ScoringTarget,
    SyntheticSeedResult,
    format_ts,
    new_id,
    utc_now,
)
from research_engine.handlers import ROUTES
from research_engine.repository import InvalidCursorError, InvalidTransitionError
from research_engine.scoring import engine, model, store
from research_engine.scoring import facts as fact_store
from research_engine.scoring.model import ScoringError
from research_engine.testdata import (
    SYNTHETIC_ICP_NAME,
    SYNTHETIC_MODEL_NAME,
    seed_synthetic_workspace,
    synthetic_icp,
)


@pytest.fixture
def seeded(conn: sqlite3.Connection, data_dir: Path) -> SyntheticSeedResult:
    """The development seed: its scoring part made a profile, a model and two runs."""
    return seed_synthetic_workspace(conn, data_dir)


def _runs(conn: sqlite3.Connection, project_id: str) -> list[str]:
    return [
        row["id"]
        for row in conn.execute(
            "SELECT id FROM score_runs WHERE project_id = ? ORDER BY created_at, id", (project_id,)
        )
    ]


def _cards(conn: sqlite3.Connection, run_id: str) -> dict[str, LeadScorecard]:
    return {
        row["business_id"]: LeadScorecard.model_validate(orjson.loads(row["scorecard_json"]))
        for row in conn.execute(
            "SELECT business_id, scorecard_json FROM business_scores WHERE score_run_id = ?",
            (run_id,),
        )
    }


def _leads(run_id: str, **filters: object) -> LeadScoreQuery:
    query = {
        "score_run_id": run_id,
        "tier": None,
        "min_score": None,
        "problem": None,
        "search": None,
        "limit": 500,
        "cursor": None,
    }
    return LeadScoreQuery.model_validate(query | filters)


def _versions(conn: sqlite3.Connection, project_id: str) -> tuple[str, str]:
    catalog = store.catalog(conn, ScoringCatalogQuery(project_id=project_id))
    profile = next(p for p in catalog.icp_profiles if p.profile.name == SYNTHETIC_ICP_NAME)
    default = next(m for m in catalog.models if m.model.key == model.DEFAULT_MODEL_KEY)
    assert profile.profile.active_version_id
    assert default.model.active_version_id
    return profile.profile.active_version_id, default.model.active_version_id


def test_versions_are_history_and_only_a_changed_definition_makes_a_new_one(
    conn: sqlite3.Connection, seeded: SyntheticSeedResult
) -> None:
    catalog = store.catalog(conn, ScoringCatalogQuery(project_id=seeded.project_id))
    assert catalog.models[0].model.key == model.DEFAULT_MODEL_KEY
    assert [f.feature for f in catalog.features] == [f.id for f in model.FEATURES]
    view = next(p for p in catalog.icp_profiles if p.profile.name == SYNTHETIC_ICP_NAME)
    profile_id = view.profile.id
    request = IcpSaveRequest(
        profile_id=profile_id,
        project_id=None,
        name=view.profile.name,
        description=view.profile.description,
        definition=synthetic_icp(),
    )
    assert store.save_icp(conn, request, actor="test") == profile_id
    assert len(store.icp_view(conn, profile_id).versions) == 1

    changed = synthetic_icp().model_copy(update={"latino": LatinoRequirement.MARKET_AFFINITY})
    store.save_icp(conn, request.model_copy(update={"definition": changed}), actor="test")
    versions = store.icp_view(conn, profile_id).versions
    assert [v.version for v in versions] == [2, 1]
    assert store.icp_view(conn, profile_id).profile.active_version_id == versions[1].id
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("UPDATE icp_profile_versions SET version = 9 WHERE id = ?", (versions[1].id,))
    with pytest.raises(sqlite3.IntegrityError, match="never deleted"):
        conn.execute("DELETE FROM icp_profile_versions WHERE id = ?", (versions[1].id,))

    # a draft with errors is kept, but never activated
    draft = synthetic_icp().model_copy(update={"offers": []})
    store.save_icp(conn, request.model_copy(update={"definition": draft}), actor="test")
    latest = store.icp_view(conn, profile_id)
    assert "no_offers" in [problem.code for problem in latest.problems]
    activate = ScoringChangeRequest(
        target=ScoringTarget.ICP_PROFILE,
        id=profile_id,
        action=ScoringChangeAction.ACTIVATE,
        version_id=latest.versions[0].id,
        reason="test",
    )
    with pytest.raises(ScoringError, match="no_offers"):
        store.change(conn, activate, actor="test")
    store.change(conn, activate.model_copy(update={"version_id": versions[0].id}), actor="test")
    assert store.icp_view(conn, profile_id).profile.active_version_id == versions[0].id

    archive = activate.model_copy(
        update={"action": ScoringChangeAction.ARCHIVE, "version_id": None}
    )
    store.change(conn, archive, actor="test")
    with pytest.raises(ScoringError, match="restore"):
        store.save_icp(conn, request, actor="test")
    store.change(
        conn, archive.model_copy(update={"action": ScoringChangeAction.RESTORE}), actor="test"
    )
    (changes,) = conn.execute(
        "SELECT count(*) FROM audit_log WHERE entity_id = ? AND actor = 'test'", (profile_id,)
    ).fetchone()
    assert changes == 5  # two versions, an activation, an archive and a restore


def test_a_new_model_version_never_rewrites_the_scores_of_earlier_runs(
    conn: sqlite3.Connection, seeded: SyntheticSeedResult
) -> None:
    first, second = _runs(conn, seeded.project_id)
    before = _cards(conn, first)
    comparison = store.compare(
        conn, ScoreCompareQuery(left_run_id=first, right_run_id=second, top_k=10)
    )
    assert comparison.same_facts
    assert comparison.left.scoring_model_key == model.DEFAULT_MODEL_KEY
    assert comparison.right.scoring_model_name == SYNTHETIC_MODEL_NAME
    facts = "SELECT business_id, facts_json FROM business_scores WHERE score_run_id = ?"
    assert dict(conn.execute(facts, (first,)).fetchall()) == dict(
        conn.execute(facts, (second,)).fetchall()
    )

    synthetic = next(
        m
        for m in store.catalog(conn, ScoringCatalogQuery(project_id=None)).models
        if m.model.name == SYNTHETIC_MODEL_NAME
    )
    stricter = model.default_scoring().model_copy(update={"priority_threshold": 90})
    store.save_model(
        conn,
        ScoringModelSaveRequest(
            model_id=synthetic.model.id,
            name=synthetic.model.name,
            description=None,
            definition=stricter,
        ),
        actor="test",
    )
    newest = store.model_view(conn, synthetic.model.id).versions[0]
    assert newest.version == 2
    assert newest.is_heuristic
    icp_version, _ = _versions(conn, seeded.project_id)
    created, _ = store.plan_run(
        conn,
        ScoringStartRequest(
            project_id=seeded.project_id,
            icp_profile_version_id=icp_version,
            scoring_model_version_id=newest.id,
            facts_from_run_id=first,
        ),
        actor="test",
    )
    assert engine.run_scoring(conn, created[0], lambda: False)
    assert _cards(conn, first) == before
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("UPDATE business_scores SET tier = 'low' WHERE score_run_id = ?", (first,))
    with pytest.raises(sqlite3.IntegrityError, match="once"):
        conn.execute(
            "UPDATE score_runs SET finished_at = ? WHERE id = ?",
            (format_ts(utc_now()), first),
        )


def test_a_run_scores_each_business_once_and_resumes_after_a_stop(
    conn: sqlite3.Connection, seeded: SyntheticSeedResult, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(engine, "BATCH", 10)
    icp_version, model_version = _versions(conn, seeded.project_id)
    request = ScoringStartRequest(
        project_id=seeded.project_id,
        icp_profile_version_id=icp_version,
        scoring_model_version_id=model_version,
        facts_from_run_id=None,
    )
    created, _ = store.plan_run(conn, request, actor="test")
    with pytest.raises(ScoringError, match="already open"):
        store.plan_run(conn, request, actor="test")
    checks = iter([False, True])
    assert not engine.run_scoring(conn, created[0], lambda: next(checks, False))
    run = store.run_of_job(conn, created[0])
    assert len(store.scored_ids(conn, run.id)) == 10
    assert engine.run_scoring(conn, created[0], lambda: False)
    scored = store.scored_ids(conn, run.id)
    assert len(scored) == len(set(scored)) == len(fact_store.root_ids(conn, seeded.project_id))
    item = store.get_run(conn, created[0])
    assert item.status is JobStatus.SUCCEEDED
    assert item.stats.businesses == len(scored)
    assert sum(b.businesses for b in item.distribution) == (
        item.stats.priority + item.stats.review + item.stats.low
    )


def test_a_simulation_writes_nothing_and_matches_the_run_over_the_same_facts(
    conn: sqlite3.Connection, seeded: SyntheticSeedResult
) -> None:
    first, _ = _runs(conn, seeded.project_id)
    tables = (
        "score_runs",
        "business_scores",
        "component_scores",
        "jobs",
        "job_events",
        "icp_profiles",
        "icp_profile_versions",
        "scoring_models",
        "scoring_model_versions",
        "audit_log",
        "lead_outcomes",
        "review_decisions",
    )

    def counts() -> dict[str, int]:
        return {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables}  # noqa: S608

    before = counts()
    request = ScoringSimulationRequest(
        project_id=seeded.project_id,
        facts_from_run_id=None,
        icp=synthetic_icp(),
        scoring=model.default_scoring(),
        top=5,
    )
    live = store.simulate(conn, request, utc_now())
    stored = store.simulate(
        conn, request.model_copy(update={"facts_from_run_id": first}), utc_now()
    )
    assert counts() == before
    assert live.businesses == stored.businesses == len(fact_store.root_ids(conn, seeded.project_id))
    run = store.run_item(conn, first)
    assert stored.tiers.model_dump() == run.stats.model_dump(exclude={"businesses"})
    exclusions = next(g for g in live.gates if g.gate == "gate.exclusions")
    assert exclusions.failed
    assert all("region NJ" in e.detail for e in exclusions.examples)
    assert [item.rank for item in live.top] == [1, 2, 3, 4, 5]


def test_the_rank_is_the_engine_order_and_pages_keep_it(
    conn: sqlite3.Connection, seeded: SyntheticSeedResult
) -> None:
    first, second = _runs(conn, seeded.project_id)
    cards = _cards(conn, first)
    expected = sorted(
        cards,
        key=lambda b: model.rank_key(cards[b].tier, cards[b].total_score, cards[b].confidence, b),
    )
    seen: list[tuple[int, str]] = []
    cursor: str | None = None
    while True:
        page = store.list_leads(conn, _leads(first, limit=7, cursor=cursor))
        seen += [(item.rank, item.business_id) for item in page.items]
        if (cursor := page.next_cursor) is None:
            break
    assert [b for _, b in seen] == expected
    assert [r for r, _ in seen] == list(range(1, len(expected) + 1))
    excluded = store.list_leads(conn, _leads(first, tier=LeadTier.EXCLUDED))
    assert excluded.items
    assert all(item.total_score is None for item in excluded.items)
    assert [i.rank for i in excluded.items] == [
        r for r, b in seen if cards[b].tier is LeadTier.EXCLUDED
    ]
    other = store.list_leads(conn, _leads(second, limit=1))
    with pytest.raises(InvalidCursorError):
        store.list_leads(conn, _leads(first, limit=1, cursor=other.next_cursor))


def test_outcomes_follow_the_stage_machine_and_calibration_ignores_earlier_ones(
    conn: sqlite3.Connection, seeded: SyntheticSeedResult
) -> None:
    first, _ = _runs(conn, seeded.project_id)
    lead = conn.execute("SELECT business_id FROM lead_outcomes").fetchone()["business_id"]

    def outcome(stage: LeadStage, lost_reason: str | None = None) -> None:
        store.act(
            conn,
            LeadActionRequest(
                score_run_id=first,
                business_id=lead,
                action=LeadAction.RECORD_OUTCOME,
                stage=stage,
                lost_reason=lost_reason,
                reason="test",
            ),
            actor="test",
        )

    with pytest.raises(InvalidTransitionError):
        outcome(LeadStage.WON)  # attempted cannot jump to won
    with pytest.raises(ScoringError, match="lost reason"):
        outcome(LeadStage.LOST)
    outcome(LeadStage.CONNECTED)
    outcome(LeadStage.QUALIFIED)
    report = store.calibration(conn, CalibrationQuery(score_run_id=first))
    assert (report.labeled, report.positives) == (1, 1)
    assert {"too_few_labels", "heuristic_weights"} <= set(report.warnings)
    assert sum(p.labeled for p in report.lift_by_decile) == 1

    other, collected = conn.execute(
        "SELECT business_id, facts_collected_at FROM business_scores WHERE score_run_id = ? "
        "AND business_id <> ? ORDER BY business_id LIMIT 1",
        (first, lead),
    ).fetchone()
    earlier = datetime.fromisoformat(collected) - timedelta(days=1)
    repository.insert(
        conn,
        LeadOutcome(
            id=new_id(),
            business_id=other,
            score_run_id=None,
            stage=LeadStage.NOT_INTERESTED,
            previous_stage=LeadStage.NOT_CONTACTED,
            lost_reason=None,
            reason="An earlier call (test)",
            actor="test",
            occurred_at=earlier,
            created_at=utc_now(),
        ),
    )
    again = store.calibration(conn, CalibrationQuery(score_run_id=first))
    assert again.leakage_excluded == report.leakage_excluded + 1
    assert (again.labeled, again.positives) == (1, 1)
    detail = store.get_scorecard(conn, ScorecardQuery(business_id=lead, score_run_id=None))
    assert detail.lead_stage is LeadStage.QUALIFIED
    assert [o.stage for o in detail.outcomes][:3] == [
        LeadStage.QUALIFIED,
        LeadStage.CONNECTED,
        LeadStage.ATTEMPTED,
    ]


def test_a_local_model_cannot_change_a_score_or_add_evidence(
    conn: sqlite3.Connection, seeded: SyntheticSeedResult
) -> None:
    first, _ = _runs(conn, seeded.project_id)
    business, card = next(
        (b, c)
        for b, c in sorted(_cards(conn, first).items())
        if any(e.kind is ScoreEvidenceKind.WEBSITE_FINDING for f in c.features for e in f.evidence)
    )
    finding = next(
        e.ref
        for f in card.features
        for e in f.evidence
        if e.kind is ScoreEvidenceKind.WEBSITE_FINDING
    )
    before = store.get_scorecard(conn, ScorecardQuery(business_id=business, score_run_id=first))
    assert before.ai_reasons == []

    def ref(name: str, kind: str, entity_id: str) -> dict[str, object]:
        return {
            "ref": name,
            "kind": kind,
            "entity_id": entity_id,
            "source": None,
            "text": "synthetic input (test)",
            "sha256": "0" * 64,
        }

    now = utc_now()
    run = InferenceRun(
        id=new_id(),
        task_type=AiTaskType.OPPORTUNITY_REASONING,
        task_version="1.0.0",
        project_id=seeded.project_id,
        business_id=business,
        job_id=None,
        provider="scripted",
        model="synthetic-text:1b",
        model_digest=None,
        profile=AiProfile.QUALITY,
        parameters_json={},
        prompt_template_id="opportunity_reasoning",
        prompt_template_version="1.0.0",
        output_schema_version="1",
        input_sha256="0" * 64,
        input_json={
            "facts": {},
            "warnings": [],
            "refs": [
                ref("W1", "website_finding", finding),
                ref("W2", "website_finding", new_id()),
                ref("C1", "contactability", business),
            ],
        },
        cache_key="1" * 64,
        status=InferenceStatus.SUCCEEDED,
        device=AiDevice.GPU,
        attempts=1,
        prompt_tokens=None,
        completion_tokens=None,
        duration_ms=None,
        error_code=None,
        error_message=None,
        review_status=AiReviewStatus.UNREVIEWED,
        started_at=now,
        finished_at=now,
        created_at=now,
        updated_at=now,
    )
    output = InferenceOutput(
        id=new_id(),
        inference_run_id=run.id,
        attempt=1,
        raw_output="{}",
        validated_output_json={
            "outcome": "ok",
            "strengths": [],
            "missing_information": [],
            "problems": [
                {
                    "problem": "weak_conversion_path",
                    "explanation": "No call to action (test).",
                    "evidence_ids": ["W1"],
                },
                {
                    "problem": "weak_local_seo",
                    "explanation": "A finding nobody stored (test).",
                    "evidence_ids": ["W2"],
                },
                {
                    "problem": "low_contactability",
                    "explanation": "A subscore, not a finding (test).",
                    "evidence_ids": ["W1", "C1"],
                },
            ],
        },
        validation_errors_json=None,
        created_at=now,
    )
    repository.insert(conn, run, output)
    after = store.get_scorecard(conn, ScorecardQuery(business_id=business, score_run_id=first))
    assert after.scorecard == before.scorecard
    assert [r.problem for r in after.ai_reasons] == [OpportunityProblem.WEAK_CONVERSION_PATH]
    assert [e.ref for e in after.ai_reasons[0].evidence] == [finding]
    assert after.ai_dropped == 2

    conn.execute("UPDATE ai_inference_runs SET review_status = 'rejected' WHERE id = ?", (run.id,))
    rejected = store.get_scorecard(conn, ScorecardQuery(business_id=business, score_run_id=first))
    assert (rejected.ai_reasons, rejected.ai_dropped) == ([], 0)


def test_every_scoring_message_has_a_route() -> None:
    messages = {message for message in MessageType if message.value.startswith("scoring.")}
    assert len(messages) == 13
    assert messages <= set(ROUTES)
