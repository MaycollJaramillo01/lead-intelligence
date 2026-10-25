"""The scoring engine on hand-built facts: gates and exclusions, unknown against failed, features
that do not apply, formulas, determinism, evidence, Latino requirements, ranking, offers and the
offline metrics (phase 11)."""

from datetime import UTC, datetime, timedelta
from math import fsum, isclose
from typing import Any

from research_engine.domain import (
    BusinessFacts,
    FeatureState,
    FinalInput,
    GateOutcome,
    IcpDefinition,
    LeadScorecard,
    LeadStage,
    LeadTier,
    OpportunityInput,
    PrecisionAtK,
    ProblemType,
    ScoreComponent,
    ScoreComponentView,
    ScoreFeature,
    ScoreVersions,
    ScoringDefinition,
    new_id,
)
from research_engine.scoring import model

T0 = datetime(2026, 9, 1, 12, tzinfo=UTC)
PROJECT = new_id()
VERSIONS = ScoreVersions(
    engine_version=model.ENGINE_VERSION,
    facts_version=model.FACTS_VERSION,
    scoring_model_key="test.model",
    scoring_model_version=1,
    is_heuristic=True,
    icp_profile_name="Test ICP",
    icp_profile_version=1,
)
CATEGORIES = (
    "technical",
    "seo",
    "local_seo",
    "content",
    "conversion",
    "mobile",
    "accessibility",
    "measurement",
    "aeo_geo_readiness",
)
ALL_PROBLEMS = [problem.value for problem in ProblemType]
PASS, FAIL, UNKNOWN = GateOutcome.PASS, GateOutcome.FAIL, GateOutcome.UNKNOWN
T, F, N = True, False, None


def finding(rule_id: str, result: str, observed: str | None = None) -> dict[str, Any]:
    return {
        "id": new_id(),
        "rule_id": rule_id,
        "category": rule_id.split(".")[0],
        "result": result,
        "severity": "high",
        "confidence": 0.9,
        "observed_value": observed,
        "url": "https://synthetic-a.example/",
    }


def site(findings: list[dict[str, Any]] | None = None, score: int = 70) -> dict[str, Any]:
    """An audited site. By default its calls to action, form and viewport fail and analytics is
    not detected, while its pages answer and it shows trust signals."""
    return {
        "id": new_id(),
        "site": "synthetic-a.example",
        "status": "complete",
        "coverage": 1.0,
        "pages_analyzed": 3,
        "created_at": T0 - timedelta(days=2),
        "subscores": [{"category": category, "score": score} for category in CATEGORIES],
        "findings": findings
        if findings is not None
        else [
            finding("technical.page_errors", "pass"),
            finding("conversion.primary_cta", "fail", "no call to action"),
            finding("conversion.lead_form", "fail"),
            finding("conversion.booking_or_quote", "warning"),
            finding("conversion.trust_signals", "pass"),
            finding("mobile.viewport", "fail"),
            finding("measurement.analytics", "warning"),
            finding("measurement.advertising", "pass", "Meta Pixel"),
            finding("content.freshness", "pass"),
        ],
        "technologies": [],
        "previous_audit_id": None,
        "changes": 0,
    }


def without(rule_id: str) -> dict[str, Any]:
    audit = site()
    return audit | {"findings": [f for f in audit["findings"] if f["rule_id"] != rule_id]}


def latino(status: str) -> dict[str, Any]:
    return {
        "assessment_id": new_id(),
        "status": status,
        "confidence": 0.95,
        "method": "rules",
        "contradictions": 0,
    }


def facts(**changes: Any) -> BusinessFacts:
    data = {
        "facts_version": model.FACTS_VERSION,
        "business_id": new_id(),
        "project_id": PROJECT,
        "display_name": "Synthetic Plumbing 0901",
        "status": "active",
        "is_synthetic": True,
        "first_seen_at": T0 - timedelta(days=60),
        "last_seen_at": T0 - timedelta(days=3),
        "collected_at": T0,
        "source_count": 2,
        "conflict_count": 0,
        "names": ["Synthetic Plumbing 0901"],
        "locations": [
            {"country_code": "US", "region": "TX", "locality": "Austin", "postal_code": "78701"}
        ],
        "categories": ["Plumbing"],
        "domains": ["synthetic-a.example"],
        "audit": site(),
        "channels": [
            {
                "key": "phone:+15125550191",
                "contact_type": "phone",
                "validation_status": "structurally_valid",
                "level": "business",
                "decision_maker_id": None,
            }
        ],
        "contactability": {
            "score": 60,
            "version": "1.0.0",
            "formula": "synthetic formula",
            "gates": [],
            "channels_counted": 1,
            "components": [
                {"code": code, "weight": weight, "value": 0.6, "detail": None}
                for code, weight in (
                    ("availability", 25),
                    ("validation", 30),
                    ("role", 15),
                    ("freshness", 15),
                    ("source", 15),
                )
            ],
        },
        "people": [],
        "business_suppressed": False,
        "latino": None,
    }
    return BusinessFacts.model_validate(data | changes)


def icp(**criteria: Any) -> IcpDefinition:
    offer = {
        "key": "web",
        "name": "Website rebuild",
        "description": "Calls to action and forms.",
        "solves": ALL_PROBLEMS,
        "capabilities": [],
        "not_for_categories": [],
    }
    data = model.icp_template().model_dump(mode="json") | {"offers": [offer]}
    return IcpDefinition.model_validate(data | criteria)


def score(
    found: BusinessFacts,
    definition: IcpDefinition | None = None,
    scoring: ScoringDefinition | None = None,
) -> LeadScorecard:
    return model.score(found, definition or icp(), scoring or model.default_scoring(), VERSIONS)


def gate(card: LeadScorecard, name: str) -> GateOutcome:
    return next(g.outcome for g in card.gates if g.gate == name)


def feature(card: LeadScorecard, name: str) -> ScoreFeature:
    return next(f for f in card.features if f.feature == name)


def component(card: LeadScorecard, name: ScoreComponent) -> ScoreComponentView:
    return next(c for c in card.components if c.component is name)


def area(region: str) -> dict[str, Any]:
    return {"country_code": "US", "region": region, "locality": None, "postal_code": None}


def test_a_failed_gate_excludes_and_an_undecided_one_asks_for_more_data() -> None:
    texas = icp(
        geography={"importance": "must_have", "weight": 2, "include": [area("TX")], "exclude": []}
    )
    inside = score(facts(), texas)
    assert gate(inside, "criterion.geography") is PASS
    assert inside.tier in (LeadTier.PRIORITY, LeadTier.REVIEW, LeadTier.LOW)

    miami = [{"country_code": "US", "region": "FL", "locality": "Miami", "postal_code": "33130"}]
    elsewhere = score(facts(locations=miami), texas)
    assert gate(elsewhere, "criterion.geography") is FAIL
    assert elsewhere.tier is LeadTier.EXCLUDED
    assert elsewhere.total_score is None
    assert elsewhere.message is None

    nowhere = score(facts(locations=[]), texas)
    assert gate(nowhere, "criterion.geography") is UNKNOWN
    assert nowhere.tier is LeadTier.INSUFFICIENT_DATA
    assert nowhere.total_score is not None
    assert "gate_undecided" in {risk.code for risk in nowhere.risks}


def test_exclusions_the_do_not_contact_list_and_closed_businesses_are_hard_gates() -> None:
    base = facts()
    assert score(base).tier is not LeadTier.EXCLUDED
    none: dict[str, list[str]] = {
        "names": [], "domains": [], "categories": [], "regions": [], "postal_codes": []
    }  # fmt: skip
    cases = (
        (facts(business_suppressed=True), icp(), "gate.do_not_contact"),
        (facts(status="closed"), icp(), "gate.business_closed"),
        (facts(names=["Plumber Austin TX"]), icp(), "gate.lead_generation"),
        (base, icp(exclusions=none | {"names": ["plumbing"]}), "gate.exclusions"),
        (base, icp(exclusions=none | {"domains": ["synthetic-a.example"]}), "gate.exclusions"),
        (base, icp(exclusions=none | {"regions": ["TX"]}), "gate.exclusions"),
        (base, icp(exclusions=none | {"postal_codes": ["787"]}), "gate.exclusions"),
    )
    for found, definition, name in cases:
        card = score(found, definition)
        assert gate(card, name) is FAIL, name
        assert card.tier is LeadTier.EXCLUDED, name
        assert card.total_score is None, name


def test_unknown_facts_are_left_out_while_failures_count() -> None:
    unaudited = score(facts(audit=None))  # a website is known but was never audited
    gap = [f for f in unaudited.features if f.component is ScoreComponent.DIGITAL_GAP]
    assert all(f.state is FeatureState.UNKNOWN for f in gap if f.feature != "gap.no_website")
    assert feature(unaudited, "gap.no_website").state is FeatureState.NOT_APPLICABLE
    assert component(unaudited, ScoreComponent.DIGITAL_GAP).value is None
    assert gate(unaudited, "gate.observable_problem") is UNKNOWN
    assert unaudited.tier is LeadTier.INSUFFICIENT_DATA
    assert {"gap.conversion_path", "gap.mobile"} <= {m.feature for m in unaudited.missing}
    assert all(m.weight_share > 0 for m in unaudited.missing)

    failed_answers = site()
    failed_answers["findings"][0] = finding("technical.page_errors", "fail")
    failed = score(facts(audit=failed_answers))
    unknown_answers = score(facts(audit=without("technical.page_errors")))
    assert feature(unknown_answers, "health.site_answers").state is FeatureState.UNKNOWN
    health_failed = component(failed, ScoreComponent.BUSINESS_HEALTH)
    health_unknown = component(unknown_answers, ScoreComponent.BUSINESS_HEALTH)
    assert health_failed.value is not None
    assert health_unknown.value is not None
    assert health_failed.value < health_unknown.value
    assert health_unknown.coverage < health_failed.coverage == 1.0

    ratings = {"importance": "nice_to_have", "weight": 5, "min_rating": 4.5, "min_reviews": 20}
    recent = {"importance": "nice_to_have", "weight": 1, "max_days_since_seen": 30}
    rated = score(facts(), icp(reputation=ratings, freshness=recent))
    assert component(rated, ScoreComponent.ICP_MATCH).value == 100.0  # unknown is not a zero
    assert component(rated, ScoreComponent.ICP_MATCH).coverage < 1


def test_a_feature_that_does_not_apply_never_counts() -> None:
    assert feature(score(facts()), "gap.online_booking").state is FeatureState.KNOWN
    roofer = facts(categories=["Roofing contractor"])
    booking = feature(score(roofer), "gap.online_booking")
    assert booking.state is FeatureState.NOT_APPLICABLE
    assert booking.reason == "not_applicable_to_category"
    same_without_booking = facts(
        categories=["Roofing contractor"], audit=without("conversion.booking_or_quote")
    )
    assert (
        component(score(roofer), ScoreComponent.DIGITAL_GAP).value
        == component(score(same_without_booking), ScoreComponent.DIGITAL_GAP).value
    )

    other = {
        "key": "web",
        "name": "Website rebuild",
        "description": "",
        "solves": ALL_PROBLEMS,
        "capabilities": [],
        "not_for_categories": ["plumbing"],
    }
    unserved = score(facts(), icp(offers=[other]))
    problems = [f for f in unserved.features if f.problem is not None]
    assert all(f.state is not FeatureState.KNOWN for f in problems)
    assert {f.reason for f in problems if f.state is FeatureState.NOT_APPLICABLE} == {
        "no_offer_solves",
        "website_known",
    }
    assert gate(unserved, "gate.observable_problem") is FAIL
    assert unserved.offer is None
    assert unserved.tier is LeadTier.EXCLUDED


def test_contributions_add_up_to_each_formula_and_to_the_total() -> None:
    texas = {"importance": "nice_to_have", "weight": 2, "include": [area("TX")], "exclude": []}
    card = score(facts(latino=latino("verified_latino_owned")), icp(geography=texas))
    assert card.total_score is not None
    final_inputs = {i.value for i in FinalInput}
    opportunity_inputs = {i.value for i in OpportunityInput}
    contributions = [c.contribution or 0 for c in card.components if c.component in final_inputs]
    assert isclose(fsum(contributions), card.total_score, abs_tol=1e-3)
    assert component(card, ScoreComponent.FINAL_LEAD_SCORE).value == card.total_score
    opportunity = component(card, ScoreComponent.OPPORTUNITY_SCORE)
    assert opportunity.value is not None
    assert isclose(
        fsum(c.contribution or 0 for c in card.components if c.component in opportunity_inputs),
        opportunity.value,
        abs_tol=1e-3,
    )
    for name in (*(ScoreComponent(i) for i in opportunity_inputs), ScoreComponent.CONTACTABILITY):
        view = component(card, name)
        points = [f.points for f in card.features if f.component is name and f.points is not None]
        assert view.value is not None, name
        assert isclose(fsum(points), view.value, abs_tol=1e-3), name
    impacts = [f.impact or 0 for f in card.features if f.component in opportunity_inputs]
    assert isclose(fsum(impacts), opportunity.contribution or 0, abs_tol=1e-3)


def test_the_same_facts_and_versions_always_give_the_same_scorecard() -> None:
    found = facts()
    first = score(found)
    again = score(BusinessFacts.model_validate_json(found.model_dump_json()))
    assert first == again
    assert first.model_dump_json() == again.model_dump_json()
    assert first.versions == VERSIONS
    stricter = model.default_scoring().model_copy(
        update={"priority_threshold": 99, "review_threshold": 98}
    )
    moved = score(found, scoring=stricter)
    assert moved.total_score == first.total_score
    assert moved.tier is LeadTier.LOW


def test_nothing_counts_without_evidence() -> None:
    claimed = model.known(1.0, "claimed without a source", [])
    assert claimed.state is FeatureState.UNKNOWN
    assert claimed.reason == "no_evidence"
    card = score(facts(latino=latino("probable_latino_owned")))
    assert all(f.evidence for f in card.features if f.state is FeatureState.KNOWN)
    assert all(g.evidence for g in card.gates if g.outcome is not UNKNOWN)
    assert all(r.evidence for r in (*card.reasons, *card.contact_now))
    assert card.offer is not None
    assert card.offer.evidence
    assert "measurement_uncertain" in {risk.code for risk in card.risks}


def test_latino_requirements_filter_without_moving_the_number() -> None:
    affinity = facts(latino=latino("spanish_market_affinity"))
    verified = facts(latino=latino("verified_latino_owned"))
    unevaluated = facts(latino=None)
    for_affinity, for_verified = icp(latino="market_affinity"), icp(latino="verified_latino_owned")
    assert gate(score(affinity, for_affinity), "gate.latino") is PASS
    assert gate(score(verified, for_affinity), "gate.latino") is PASS
    assert gate(score(affinity, for_verified), "gate.latino") is FAIL
    assert score(affinity, for_verified).tier is LeadTier.EXCLUDED
    assert gate(score(verified, for_verified), "gate.latino") is PASS
    assert gate(score(unevaluated, for_verified), "gate.latino") is UNKNOWN
    assert all(g.gate != "gate.latino" for g in score(affinity).gates)
    totals = {score(found).total_score for found in (affinity, verified, unevaluated)}
    assert len(totals) == 1  # weighed 0 by default: it filters, it does not score
    shown = component(score(verified), ScoreComponent.LATINO_CONFIDENCE)
    assert (shown.value, shown.weight) == (95.0, 0)
    weighted = model.default_scoring().model_dump(mode="json")
    weighted["final"][-1]["weight"] = 10
    assert "latino_weighted" in {
        p.code for p in model.check_scoring(ScoringDefinition.model_validate(weighted))
    }


def test_ranking_is_stable_and_ties_break_by_confidence_then_business_id() -> None:
    rows = [
        (LeadTier.LOW, 40.0, 0.9, "c"),
        (LeadTier.PRIORITY, 80.0, 0.5, "b"),
        (LeadTier.PRIORITY, 80.0, 0.5, "a"),
        (LeadTier.PRIORITY, 80.0, 0.7, "z"),
        (LeadTier.INSUFFICIENT_DATA, None, 0.9, "d"),
        (LeadTier.INSUFFICIENT_DATA, 90.0, 0.1, "e"),
        (LeadTier.EXCLUDED, None, 1.0, "f"),
    ]
    ranked = sorted(rows, key=lambda row: model.rank_key(*row))
    assert [row[3] for row in ranked] == ["z", "a", "b", "c", "e", "d", "f"]
    assert sorted(reversed(rows), key=lambda row: model.rank_key(*row)) == ranked


def test_offline_metrics_on_a_labeled_fixture() -> None:
    labels = [T, T, F, N, T, F, N, N, F, T, F, N, N, F, F, N, T, N, N, F]
    assert model.precision_at_k(labels, 5) == PrecisionAtK(
        k=5, labeled=4, positives=3, precision=0.75
    )
    top10 = model.precision_at_k(labels, 10)
    assert (top10.labeled, top10.positives, top10.precision) == (7, 4, 0.5714)
    assert model.precision_at_k(labels, 100).labeled == 12
    assert model.precision_at_k([N, N], 2).precision is None

    deciles = model.lift_by_decile(labels)
    assert [d.businesses for d in deciles] == [2] * 10
    assert (deciles[0].rate, deciles[0].lift) == (1.0, 2.4)  # 5 positives of 12 labeled overall
    assert [d.businesses for d in model.lift_by_decile([T] * 13)] == [2, 2, 2, 1, 1, 1, 1, 1, 1, 1]

    bands = model.calibration_bands(
        [(95.0, T), (85.0, F), (100.0, T), (10.0, F), (None, T), (55.0, None)]
    )
    top = bands[-1]
    assert (top.businesses, top.labeled, top.observed_rate, top.mean_score) == (
        3,
        3,
        0.6667,
        93.3333,
    )
    assert bands[0].observed_rate == 0.0
    assert bands[2].labeled == 0
    assert bands[2].observed_rate is None

    cohorts = {
        c.tier: c
        for c in model.tier_cohorts(
            [(LeadTier.PRIORITY, T), (LeadTier.PRIORITY, F), (LeadTier.LOW, N)]
        )
    }
    assert (cohorts[LeadTier.PRIORITY].labeled, cohorts[LeadTier.PRIORITY].conversion) == (2, 0.5)
    assert cohorts[LeadTier.LOW].conversion is None
    assert model.label_of(LeadStage.CONNECTED) is None
    assert model.label_of(LeadStage.WON) is True
    assert model.label_of(LeadStage.INVALID_CONTACT) is False


def test_the_offer_and_the_draft_follow_the_observed_problems() -> None:
    offers = [
        {
            "key": "seo",
            "name": "Local SEO",
            "description": "",
            "solves": ["weak_local_seo"],
            "capabilities": [],
            "not_for_categories": [],
        },
        {
            "key": "web",
            "name": "Website rebuild",
            "description": "Calls to action and forms.",
            "solves": ["weak_conversion_path", "poor_mobile_experience"],
            "capabilities": [],
            "not_for_categories": [],
        },
    ]
    card = score(facts(), icp(offers=offers))
    assert card.offer is not None
    assert card.offer.offer_key == "web"
    assert set(card.offer.problems) == {
        ProblemType.WEAK_CONVERSION_PATH,
        ProblemType.POOR_MOBILE_EXPERIENCE,
    }
    assert card.message is not None
    assert card.message.is_draft
    assert "no clear call to action" in card.message.body
    assert card.impacts
    assert all(impact.is_inference for impact in card.impacts)

    healthy = site([finding(rule, "pass") for rule in sorted(model.FACT_RULES)], score=90)
    quiet = score(facts(audit=healthy), icp(offers=offers))
    assert gate(quiet, "gate.observable_problem") is FAIL
    assert quiet.tier is LeadTier.EXCLUDED
    assert quiet.offer is None
    assert quiet.message is None


def test_definitions_say_what_keeps_them_from_running() -> None:
    def codes(problems: list[Any]) -> set[str]:
        return {problem.code for problem in problems}

    assert model.check_scoring(model.default_scoring()) == []
    assert codes(model.check_icp(model.icp_template())) == {"no_offers", "nothing_required"}
    empty_geography = {"importance": "must_have", "weight": 1, "include": [], "exclude": []}
    ratings = {"importance": "nice_to_have", "weight": 1, "min_rating": 4.0, "min_reviews": 10}
    offer = icp().offers[0].model_dump(mode="json")
    found = codes(
        model.check_icp(
            icp(
                geography=empty_geography,
                reputation=ratings,
                offers=[offer, offer | {"solves": []}],
            )
        )
    )
    assert {
        "empty_must_have",
        "reputation_unknown",
        "duplicate_offer_key",
        "offer_solves_nothing",
    } <= found
    default = model.default_scoring()
    broken = default.model_copy(update={"features": default.features[1:], "review_threshold": 80})
    assert {"features_mismatch", "thresholds_order"} <= codes(model.check_scoring(broken))


def test_a_name_that_is_only_a_trade_and_a_place_is_a_lead_generation_listing() -> None:
    """D-107: names read in the Houston, Dallas and San Antonio searches of 2026-10-25."""

    def listing(name: str, locality: str = "Sugar Land") -> str | None:
        return model.lead_generation_listing([name], [(locality, "TX")])

    caught = (
        "Plumber Sugar Land TX", "Plumber in Sugar Land TX", "911 Water Heater Sugar Land TX",
        "Toilet Repair Mission bend TX", "Plumbing Telfair TX", "Carpet Cleaning TX",
        "Tile & Grout Cleaning New Territory TX", "Locksmith Sugar Land", "Plumbing Sugar Land",
        "24 Hour Locksmith Houston Texas", "Best Cleaning Service Of Texas",
        "Air Duct Cleaner Sugar Land", "Tank & Tankless Water Heaters Sugar Land TX",
        "Sugar Land TX Water Heater Repairs", "Sugar Land TX Air Duct Vents",
    )  # fmt: skip
    for name in caught:
        assert listing(name) == name, name
    assert listing("Channelview Carpet Cleaning TX", "Channelview")
    assert listing("Carpet Cleaning Katy", "Katy")
    assert listing("TX Sugar Land Plumbing")
    assert listing("Missouri City TX Air Duct Cleaning", "Missouri City")
    assert listing("Residential Plumbing Sugar Land")
    assert listing("Plumbing Mission Bend", "Mission Bend TX")  # state written into the locality
    assert listing("Texas Style Roofing") is None
    kept = (
        "Plumbing Plus", "Plumbing Express Service", "Painting By Mejia", "Roofing Hidalgo Llc",
        "Compass Roofing TX", "Morgal Roofing TX", "Sugar Land Plumbing", "The Maids in Sugar Land",
        "Professional Janitorial Services of Sugar Land", "Garage Door Guys Grand Prairie",
        "Synthetic Plumbing 0901", "Plumbing Colony Lakes",
    )  # fmt: skip
    for name in kept:
        assert listing(name) is None, name
    assert listing("Plumbing Colony Lakes", "Colony Lakes")  # its own suburb, without the state
    assert listing("Carpet Cleaning Katy", "Houston") is None  # a place it does not list
    assert model.lead_generation_listing(["Plumber Austin TX"], []) is None  # no state to end in
