"""Latino evidence against the store (phase 07): extraction from source records and crawled pages,
the evidence lifecycle, assessments and their history, reviews, filters and jobs. Synthetic test
data only (see testdata.py)."""

import hashlib
import sqlite3
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from research_engine import jobs, repository
from research_engine.domain import (
    AiDevice,
    AiProfile,
    AiReviewStatus,
    AiTaskType,
    AssessmentMethod,
    Business,
    BusinessListQuery,
    BusinessPage,
    BusinessSort,
    DiscoveryControlRequest,
    EvidenceItem,
    EvidenceStatus,
    InferenceRun,
    InferenceStatus,
    JobStatus,
    LatinoReviewFilter,
    LatinoReviewRequest,
    LatinoRunListQuery,
    LatinoStartRequest,
    LatinoStatus,
    LatinoTrack,
    RunControlAction,
    SourceKind,
    new_id,
    utc_now,
)
from research_engine.latino import engine, rules, store
from research_engine.resolution import engine as resolution_engine
from research_engine.resolution import store as resolution_store
from research_engine.testdata import (
    LatinoFixture,
    SyntheticContext,
    insert_candidates,
    insert_site_pages,
    seed_latino,
    synthetic_connector,
    synthetic_page_html,
    synthetic_project,
    synthetic_run,
    synthetic_source,
)

pytestmark = pytest.mark.anyio

DECLARED_SITE = "synthetic-dental-clinic-0503.example"


def evaluate(
    conn: sqlite3.Connection, project_id: str, ruleset: rules.Ruleset = rules.DEFAULT_RULESET
) -> str:
    created, _ = store.plan_runs(conn, project_id, actor="test")
    assert engine.run_latino(conn, created[0], lambda: False, ruleset)
    return created[0]


def resolve(conn: sqlite3.Connection, project_id: str) -> None:
    created, _ = resolution_store.plan_runs(conn, project_id, actor="test")
    assert resolution_engine.run_resolution(conn, created[0], lambda: False)


def business_of(conn: sqlite3.Connection, candidate_id: str) -> str:
    """The canonical business a candidate became."""
    (record_id,) = conn.execute(
        "SELECT business_id FROM entity_records WHERE candidate_id = ?", (candidate_id,)
    ).fetchone()
    business = repository.get(conn, Business, record_id)
    return business.merged_into_id or business.id


def businesses(conn: sqlite3.Connection, project_id: str, **filters: object) -> BusinessPage:
    values: dict[str, object] = {
        "project_id": project_id,
        "search": None,
        "status": None,
        "latino_status": None,
        "latino_min_confidence": None,
        "latino_source_kind": None,
        "latino_review": None,
        "sort": BusinessSort.NAME,
        "limit": 500,
        "cursor": None,
    }
    return repository.list_businesses(conn, BusinessListQuery.model_validate(values | filters))


def notes(view: object) -> set[str]:
    return {note.code for note in getattr(view, "warnings", [])}


@pytest.fixture
def seeded(conn: sqlite3.Connection, synthetic: SyntheticContext) -> LatinoFixture:
    return seed_latino(conn, synthetic)


# ---- statuses, evidence and provenance ----------------------------------------------------------


def test_every_status_comes_from_evidence_and_explains_itself(
    conn: sqlite3.Connection, synthetic: SyntheticContext, seeded: LatinoFixture
) -> None:
    expected = {
        "certified": LatinoStatus.VERIFIED_LATINO_OWNED,
        "listed": LatinoStatus.PROBABLE_LATINO_OWNED,
        "declared": LatinoStatus.HIGH_CONFIDENCE_LATINO_OWNED,
        "spanish": LatinoStatus.SPANISH_MARKET_AFFINITY,
        "brand_austin": LatinoStatus.PROBABLE_LATINO_OWNED,
        "brand_denver": LatinoStatus.PROBABLE_LATINO_OWNED,
        "disputed_listing": LatinoStatus.UNKNOWN,
    }
    listed = {item.id: item for item in businesses(conn, synthetic.project_id).items}
    assert len(listed) == len(expected)  # the two disputed records are one business
    for situation, status in expected.items():
        business_id = business_of(conn, seeded.candidates[situation])
        assert listed[business_id].latino_status is status, situation
        assert not listed[business_id].latino_reviewed
        detail = store.get_latino_detail(conn, business_id)
        assert detail.effective is not None
        assert detail.effective.assessment.method is AssessmentMethod.RULES
        assert detail.effective.assessment.status is status, situation
        assert "names_not_used" in detail.effective.cannot_conclude
    assert business_of(conn, seeded.candidates["disputed_listing"]) == business_of(
        conn, seeded.candidates["disputed_registry"]
    )

    certified = store.get_latino_detail(conn, business_of(conn, seeded.candidates["certified"]))
    (evidence,) = certified.evidence
    item, provenance = evidence.evidence.value, evidence.evidence.provenance
    assert (evidence.claim, item.field_path, provenance.extractor, provenance.connector_kind) == (
        "certification",
        "payload.certification",
        "latino.payload",
        SourceKind.PUBLIC_REGISTRY,
    )
    assert item.snippet is not None
    assert item.snippet_sha256 == hashlib.sha256(item.snippet.encode()).hexdigest()
    assert isinstance(item.value_json, dict)
    assert item.value_json["expires_on"] == "2030-12-31"
    assert (evidence.counted, evidence.source_reliability) == (True, 0.95)

    declared = store.get_latino_detail(conn, business_of(conn, seeded.candidates["declared"]))
    (page,) = declared.evidence
    assert page.evidence.provenance.extractor == "latino.site"
    assert page.evidence.provenance.source_url == f"https://{DECLARED_SITE}/"
    assert "Latina-owned" in (page.evidence.value.snippet or "")
    assert "self_declared" in (declared.effective.cannot_conclude if declared.effective else [])

    spanish = store.get_latino_detail(conn, business_of(conn, seeded.candidates["spanish"]))
    assert {entry.track for entry in spanish.evidence} == {LatinoTrack.MARKET}
    assert spanish.effective is not None
    assert "affinity_is_not_ownership" in spanish.effective.cannot_conclude

    brand = store.get_latino_detail(conn, business_of(conn, seeded.candidates["brand_denver"]))
    assert brand.effective is not None
    assert "organizational_evidence" in notes(brand.effective)
    assert "location_ownership_unknown" in brand.effective.cannot_conclude
    assert all(entry.organizational for entry in brand.evidence)

    disputed = store.get_latino_detail(
        conn, business_of(conn, seeded.candidates["disputed_listing"])
    )
    assert "contradicted" in notes(disputed.effective)
    assert [(entry.track, entry.evidence.value.status) for entry in disputed.evidence] == [
        (LatinoTrack.OWNERSHIP, EvidenceStatus.CONTRADICTED),
        (LatinoTrack.CONTRADICTION, EvidenceStatus.ACTIVE),
    ]

    # names, addresses and phones are never read as evidence
    (from_identity,) = conn.execute(
        "SELECT count(*) FROM evidence_items e JOIN extractions x ON x.id = e.extraction_id "
        "WHERE x.extractor LIKE 'latino.%' AND e.field_path IN "
        "('payload.name', 'payload.address', 'payload.city', 'payload.phone', 'payload.website')"
    ).fetchone()
    assert from_identity == 0

    run = store.get_latino_run(conn, seeded.job_id)
    assert run.status is JobStatus.SUCCEEDED
    stats = run.stats
    assert (stats.businesses, stats.sources_scanned, stats.pages_scanned) == (7, 11, 3)
    assert (stats.verified, stats.high_confidence, stats.probable) == (1, 1, 3)
    assert (stats.affinity, stats.unknown, stats.reviewed) == (1, 1, 0)
    assert (stats.ownership_evidence, stats.market_evidence, stats.contradictions) == (5, 3, 1)


def test_a_second_run_changes_nothing(
    conn: sqlite3.Connection, synthetic: SyntheticContext, seeded: LatinoFixture
) -> None:
    def state() -> list[list[tuple[object, ...]]]:
        return [
            [tuple(row) for row in conn.execute(sql)]
            for sql in (
                "SELECT * FROM evidence_items ORDER BY id",
                "SELECT * FROM latino_assessments ORDER BY id",
                "SELECT * FROM latino_assessment_evidence ORDER BY assessment_id, evidence_item_id",
                "SELECT * FROM extractions WHERE extractor LIKE 'latino.%' ORDER BY id",
            )
        ]

    before = state()
    again = evaluate(conn, synthetic.project_id)
    assert state() == before
    stats = store.get_latino_run(conn, again).stats
    assert (stats.evidence_found, stats.changed) == (0, 0)


def test_a_merged_record_shows_the_evidence_of_its_whole_business(
    conn: sqlite3.Connection, seeded: LatinoFixture
) -> None:
    member = conn.execute(
        "SELECT id, merged_into_id FROM businesses WHERE merged_into_id IS NOT NULL"
    ).fetchone()
    detail = store.get_latino_detail(conn, member["id"])
    assert (detail.business_id, detail.root_business_id) == (member["id"], member["merged_into_id"])
    assert {entry.evidence.value.business_id for entry in detail.evidence} == {
        member["id"],
        member["merged_into_id"],
    }
    own = conn.execute(
        "SELECT 1 FROM latino_assessments WHERE business_id = ? AND superseded_at IS NULL",
        (member["id"],),
    ).fetchone()
    assert own is None  # only the business it belongs to is assessed


def test_assessments_and_what_they_cite_are_history(
    conn: sqlite3.Connection, seeded: LatinoFixture
) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("UPDATE latino_assessments SET rationale = 'rewritten'")
    with pytest.raises(sqlite3.IntegrityError, match="history"):
        conn.execute("UPDATE latino_assessment_evidence SET counted = 1 - counted")
    root = business_of(conn, seeded.candidates["certified"])
    current = store._current(conn, [root], AssessmentMethod.RULES)[root]
    with pytest.raises(sqlite3.IntegrityError):  # one current calculation per business and method
        repository.insert(conn, current.model_copy(update={"id": new_id()}))


# ---- lifecycle ----------------------------------------------------------------------------------


def test_a_claim_the_website_no_longer_makes_is_revoked_and_history_is_kept(
    conn: sqlite3.Connection, synthetic: SyntheticContext, seeded: LatinoFixture
) -> None:
    business_id = business_of(conn, seeded.candidates["declared"])
    later = replace(synthetic, now=synthetic.now + timedelta(days=30))
    html = synthetic_page_html("en", "About the clinic", "A family practice. Test data only.")
    insert_site_pages(
        conn, later, DECLARED_SITE, {"/": html}, candidate_id=seeded.candidates["declared"]
    )
    evaluate(conn, synthetic.project_id)

    detail = store.get_latino_detail(conn, business_id)
    assert detail.effective is not None
    assert detail.effective.assessment.status is LatinoStatus.UNKNOWN
    assert "revoked_evidence" in notes(detail.effective)
    (claim,) = [entry for entry in detail.evidence if entry.claim == "ownership_statement"]
    assert (claim.evidence.value.status, claim.counted) == (EvidenceStatus.REVOKED, False)
    assert [view.assessment.status for view in detail.history] == [
        LatinoStatus.UNKNOWN,
        LatinoStatus.HIGH_CONFIDENCE_LATINO_OWNED,
    ]
    assert detail.history[1].assessment.superseded_at is not None
    audited = conn.execute(
        "SELECT action FROM audit_log WHERE entity_id = ?", (claim.evidence.value.id,)
    ).fetchall()
    assert audited, "the status change is audited"


def test_an_expired_certification_is_stale_and_stops_counting(
    conn: sqlite3.Connection, synthetic: SyntheticContext
) -> None:
    registry = synthetic_source(conn, synthetic, "synthetic.registry", SourceKind.PUBLIC_REGISTRY)
    expired = (synthetic.now - timedelta(days=1)).date().isoformat()
    (candidate,) = insert_candidates(
        conn,
        registry,
        [
            {
                "name": "Synthetic Roofing 0601",
                "city": "Austin",
                "state": "TX",
                "zip": "78701",
                "phone": "(512) 555-0161",
                "certification": "Hispanic-owned business enterprise",
                "certification_expires": expired,
            }
        ],
    )
    resolve(conn, synthetic.project_id)
    evaluate(conn, synthetic.project_id)
    detail = store.get_latino_detail(conn, business_of(conn, candidate))
    (evidence,) = detail.evidence
    assert evidence.evidence.value.status is EvidenceStatus.STALE
    assert (evidence.counted, evidence.freshness_label.value) == (False, "stale")
    assert detail.effective is not None
    assert detail.effective.assessment.status is LatinoStatus.UNKNOWN
    assert "stale_evidence" in notes(detail.effective)


# ---- review --------------------------------------------------------------------------------------


def test_a_person_reviews_with_a_reason_and_the_rules_keep_their_own_history(
    conn: sqlite3.Connection, synthetic: SyntheticContext, seeded: LatinoFixture
) -> None:
    listed = business_of(conn, seeded.candidates["listed"])
    reason = "Checked the directory's ownership criterion."
    request = LatinoReviewRequest(
        business_id=listed, status=LatinoStatus.HIGH_CONFIDENCE_LATINO_OWNED, reason=reason
    )
    store.review(conn, request, actor="reviewer")
    detail = store.get_latino_detail(conn, listed)
    assert detail.effective is not None
    assert detail.automatic is not None
    assert (detail.effective.assessment.method, detail.effective.assessment.status) == (
        AssessmentMethod.MANUAL_REVIEW,
        LatinoStatus.HIGH_CONFIDENCE_LATINO_OWNED,
    )
    assert detail.automatic.assessment.status is LatinoStatus.PROBABLE_LATINO_OWNED
    assert not detail.review_outdated
    (decision,) = detail.decisions
    assert (decision.previous_value, decision.new_value, decision.reason, decision.actor) == (
        "probable_latino_owned",
        "high_confidence_latino_owned",
        reason,
        "reviewer",
    )
    row = next(item for item in businesses(conn, synthetic.project_id).items if item.id == listed)
    assert (row.latino_status, row.latino_reviewed) == (
        LatinoStatus.HIGH_CONFIDENCE_LATINO_OWNED,
        True,
    )

    # the evidence limits what a person can say, and a review always has a reason
    spanish = business_of(conn, seeded.candidates["spanish"])
    refused: dict[str, dict[str, object]] = {
        "ownership evidence": {"business_id": spanish},
        "strong ownership evidence": {"status": LatinoStatus.VERIFIED_LATINO_OWNED},
        "market evidence": {"status": LatinoStatus.SPANISH_MARKET_AFFINITY},
        "needs a reason": {"reason": "   "},
    }
    for message, change in refused.items():
        with pytest.raises(store.LatinoError, match=message):
            store.review(conn, request.model_copy(update=change), actor="reviewer")
    member = conn.execute("SELECT id FROM businesses WHERE merged_into_id IS NOT NULL").fetchone()
    with pytest.raises(store.LatinoError, match="belongs to another business"):
        store.review(conn, request.model_copy(update={"business_id": member["id"]}), actor="x")

    # new rules recalculate everything, keep the older calculation and flag the review
    newer = rules.Ruleset(version="1.0.1-test")
    evaluate(conn, synthetic.project_id, newer)
    recalculated = store.get_latino_detail(conn, listed, newer)
    assert recalculated.automatic is not None
    assert recalculated.effective is not None
    assert recalculated.automatic.assessment.rules_version == "1.0.1-test"
    assert recalculated.effective.assessment.method is AssessmentMethod.MANUAL_REVIEW
    assert recalculated.review_outdated
    assert [
        view.assessment.rules_version
        for view in recalculated.history
        if view.assessment.method is AssessmentMethod.RULES
    ] == ["1.0.1-test", rules.RULES_VERSION]
    outdated = businesses(conn, synthetic.project_id, latino_review=LatinoReviewFilter.OUTDATED)
    assert [item.id for item in outdated.items] == [listed]

    withdraw = LatinoReviewRequest(business_id=listed, status=None, reason="Criterion changed.")
    store.review(conn, withdraw, actor="reviewer")
    withdrawn = store.get_latino_detail(conn, listed)
    assert withdrawn.effective is not None
    assert withdrawn.effective.assessment.method is AssessmentMethod.RULES
    assert [item.decision for item in withdrawn.decisions] == [
        "latino_review_withdrawn",
        "latino_review",
    ]
    with pytest.raises(store.LatinoError, match="no review"):
        store.review(conn, withdraw, actor="reviewer")


# ---- filters -------------------------------------------------------------------------------------


def test_businesses_filter_by_status_confidence_source_kind_and_review(
    conn: sqlite3.Connection, synthetic: SyntheticContext, seeded: LatinoFixture
) -> None:
    of = {
        situation: business_of(conn, candidate)
        for situation, candidate in seeded.candidates.items()
    }

    def ids(**filters: object) -> set[str]:
        return {item.id for item in businesses(conn, synthetic.project_id, **filters).items}

    brand = {of["brand_austin"], of["brand_denver"]}
    assert ids(latino_status=LatinoStatus.VERIFIED_LATINO_OWNED) == {of["certified"]}
    assert ids(latino_status=LatinoStatus.SPANISH_MARKET_AFFINITY) == {of["spanish"]}
    assert ids(latino_status=LatinoStatus.PROBABLE_LATINO_OWNED) == {of["listed"], *brand}
    assert ids(latino_min_confidence=0.8) == {of["certified"]}
    assert ids(latino_min_confidence=0.7) == {of["certified"], of["declared"], *brand}
    assert ids(latino_source_kind=SourceKind.PUBLIC_REGISTRY) == {of["certified"]}
    assert ids(latino_source_kind=SourceKind.DIRECTORY) == {of["listed"]}
    assert ids(latino_source_kind=SourceKind.BUSINESS_WEBSITE) == {
        of["declared"],
        of["spanish"],
        *brand,
    }
    assert ids(latino_review=LatinoReviewFilter.REVIEWED) == set()
    assert len(ids(latino_review=LatinoReviewFilter.UNREVIEWED)) == 7


# ---- model candidates ---------------------------------------------------------------------------


def test_a_model_candidate_becomes_evidence_only_when_the_page_confirms_it(
    conn: sqlite3.Connection, synthetic: SyntheticContext, seeded: LatinoFixture
) -> None:
    declared = business_of(conn, seeded.candidates["declared"])
    team = synthetic_page_html(
        "en", "Our team", "We are certified as a Hispanic-owned small business. Test data only."
    )
    (raw_record_id,) = insert_site_pages(
        conn, synthetic, DECLARED_SITE, {"/team": team}, candidate_id=seeded.candidates["declared"]
    )
    now = synthetic.now
    run = InferenceRun(
        id=new_id(), task_type=AiTaskType.EVIDENCE_EXTRACTION, task_version="1.0.0",
        project_id=synthetic.project_id, business_id=declared, job_id=None, provider="ollama",
        model="synthetic-model:latest", model_digest=None, profile=AiProfile.BALANCED,
        parameters_json={"temperature": 0}, prompt_template_id="evidence_extraction",
        prompt_template_version="1.0.0", output_schema_version="1.0.0", input_sha256="0" * 64,
        input_json={"facts": {}, "refs": [], "warnings": []}, cache_key="1" * 64,
        status=InferenceStatus.SUCCEEDED, device=AiDevice.GPU, attempts=1, prompt_tokens=None,
        completion_tokens=None, duration_ms=None, error_code=None, error_message=None,
        review_status=AiReviewStatus.UNREVIEWED, started_at=now, finished_at=now, created_at=now,
        updated_at=now,
    )  # fmt: skip
    repository.insert(conn, run)
    candidates = [
        store.ModelCandidate(
            "certification_statement", "We are certified as a Hispanic-owned small business"
        ),
        store.ModelCandidate("ownership_statement", "The founders were born in Mexico"),
        store.ModelCandidate("spanish_service", "Our team"),
    ]

    def ingest(raw: str) -> store.CandidateOutcome:
        return store.ingest_model_candidates(
            conn,
            inference_run_id=run.id,
            raw_record_id=raw,
            business_id=declared,
            candidates=candidates,
        )

    outcome = ingest(raw_record_id)
    assert outcome.rejected == [(1, "quote_not_found"), (2, "claim_not_supported")]
    (accepted,) = outcome.accepted
    item = repository.get(conn, EvidenceItem, accepted)
    assert isinstance(item.value_json, dict)
    assert (item.value_json["inference_run_id"], item.value_json["proposed_by"]) == (
        run.id,
        "model",
    )
    (extractor,) = conn.execute(
        "SELECT extractor FROM extractions WHERE id = ?", (item.extraction_id,)
    ).fetchone()
    assert extractor == "latino.model"
    assert ingest(raw_record_id).accepted == outcome.accepted  # idempotent
    (stored,) = conn.execute(
        "SELECT count(*) FROM evidence_items WHERE extraction_id = ?", (item.extraction_id,)
    ).fetchone()
    assert stored == 1
    (listing,) = conn.execute(
        "SELECT raw_record_id FROM discovery_candidates WHERE id = ?",
        (seeded.candidates["listed"],),
    ).fetchone()
    with pytest.raises(store.LatinoError, match="crawled pages"):
        ingest(listing)


# ---- jobs ----------------------------------------------------------------------------------------


def test_an_interrupted_evaluation_resumes_from_its_checkpoint(
    conn: sqlite3.Connection, synthetic: SyntheticContext
) -> None:
    directory = synthetic_source(conn, synthetic, "synthetic.directory", SourceKind.DIRECTORY)
    places = (("Austin", "TX"), ("Miami", "FL"), ("Phoenix", "AZ"), ("Chicago", "IL"))
    rows = [
        {
            "name": f"Synthetic Store {index:04d}",
            "city": places[index % len(places)][0],
            "state": places[index % len(places)][1],
            "directory": "Latino-owned business directory (synthetic)",
        }
        for index in range(450)
    ]
    insert_candidates(conn, directory, rows)
    resolve(conn, synthetic.project_id)
    created, _ = store.plan_runs(conn, synthetic.project_id, actor="test")
    calls = 0

    def stop_soon() -> bool:
        nonlocal calls
        calls += 1
        return calls > 2

    assert not engine.run_latino(conn, created[0], stop_soon)
    state = jobs.latest_state(conn, created[0])
    assert state["stage"] == "extract"
    assert state["cursor"]
    (partial,) = conn.execute("SELECT count(*) FROM evidence_items").fetchone()
    assert 0 < partial < len(rows)

    assert engine.run_latino(conn, created[0], lambda: False)
    (total,) = conn.execute("SELECT count(*) FROM evidence_items").fetchone()
    assert total == len(rows)
    run = store.get_latino_run(conn, created[0])
    assert (run.status, run.stats.evidence_found, run.stats.probable) == (
        JobStatus.SUCCEEDED,
        len(rows),
        len(rows),
    )


async def test_the_service_runs_recovers_and_refuses_a_second_open_run(data_dir: Path) -> None:
    conn = repository.connect(data_dir)
    try:
        stamp = utc_now()
        project = synthetic_project(stamp)
        connector = synthetic_connector(stamp)
        run = synthetic_run(project.id, connector.id, stamp)
        repository.insert(conn, project, connector, run)
        seed_latino(conn, SyntheticContext(project.id, connector.id, run.id, stamp))
        service = engine.LatinoService(lambda: repository.connect(data_dir))

        (started,) = (await service.start(LatinoStartRequest(project_id=project.id))).items
        await service.idle(started.job_id)
        assert store.get_latino_run(conn, started.job_id).status is JobStatus.SUCCEEDED

        # a run a dead worker left running is paused at startup, then resumed by the operator
        created, _ = store.plan_runs(conn, project.id, actor="test")
        jobs.mark_running(conn, created[0], "test")
        with pytest.raises(store.LatinoError, match="already open"):
            await service.start(LatinoStartRequest(project_id=project.id))
        assert await service.recover() == 1
        assert store.get_latino_run(conn, created[0]).status is JobStatus.PAUSED
        resumed = await service.control(
            DiscoveryControlRequest(job_id=created[0], action=RunControlAction.RESUME)
        )
        assert resumed.status in (JobStatus.RUNNING, JobStatus.SUCCEEDED)
        await service.idle(created[0])
        assert store.get_latino_run(conn, created[0]).status is JobStatus.SUCCEEDED

        with pytest.raises(repository.NotFoundError):
            await service.control(
                DiscoveryControlRequest(job_id=new_id(), action=RunControlAction.CANCEL)
            )
        first = store.list_latino_runs(
            conn, LatinoRunListQuery(project_id=project.id, limit=2, cursor=None)
        )
        assert (first.total, len(first.items), first.next_cursor is not None) == (3, 2, True)
        rest = store.list_latino_runs(
            conn, LatinoRunListQuery(project_id=project.id, limit=2, cursor=first.next_cursor)
        )
        assert len(rest.items) == 1
    finally:
        conn.close()


def test_the_crawler_and_the_latino_engine_agree_on_the_crawl_connector() -> None:
    from research_engine.crawl.engine import CONNECTOR_KEY

    assert store.CRAWL_CONNECTOR_KEY == CONNECTOR_KEY
