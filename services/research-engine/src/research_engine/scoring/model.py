"""The scoring engine (phase 11): the facts of one business, an ICP version and a scoring version
become a scorecard.

    facts ─ features (evidence or unknown) ─ category rules and offers (not applicable)
      ─ gates (system, exclusions, Latino requirement, observable problem, must-have criteria)
      ─ components (0-100, unknown left out) ─ OPPORTUNITY_SCORE ─ FINAL_LEAD_SCORE ─ tier
      ─ reasons, contact-now signals, inferred impacts, risks, missing data, offer, message draft

Pure and deterministic: no store, clock, network or model. The same inputs always give the same
scorecard, which is what lets a stored run be scored again and compared (docs/scoring.md).
"""

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from math import fsum
from typing import Final

from research_engine.domain import (
    Area,
    BusinessFacts,
    BusinessStatus,
    CalibrationBand,
    CapacityCriterion,
    CategoryCriterion,
    ChannelCriterion,
    CriteriaProblem,
    CriterionImportance,
    CriterionView,
    DecileLift,
    DecisionMakerReview,
    DigitalMaturity,
    FactAudit,
    FactFinding,
    FeatureInfo,
    FeatureState,
    FeatureWeight,
    FinalInput,
    FinalWeight,
    FreshnessCriterion,
    GateOutcome,
    GateView,
    GeographyCriterion,
    IcpDefinition,
    InferredImpact,
    LatinoRequirement,
    LatinoStatus,
    LeadScorecard,
    LeadStage,
    LeadTier,
    MaturityCriterion,
    MessageDraft,
    MissingData,
    NotApplicableRule,
    Offer,
    OfferRecommendation,
    OpportunityInput,
    OpportunityWeight,
    PrecisionAtK,
    ProblemCriterion,
    ProblemSeverity,
    ProblemType,
    ReputationCriterion,
    ScoreBin,
    ScoreComponent,
    ScoreComponentView,
    ScoreEvidence,
    ScoreEvidenceKind,
    ScoreFeature,
    ScoreReason,
    ScoreRisk,
    ScoreVersions,
    ScoringDefinition,
    SizeCriterion,
    TierCohort,
    TierCounts,
    ValidationStatus,
    WebsiteAuditStatus,
    WebsiteCategory,
    WebsiteFindingResult,
    sort_key,
)

ENGINE_VERSION: Final = "1.0.0"
FACTS_VERSION: Final = "1.0.0"
DEFAULT_MODEL_KEY: Final = "lead_intelligence.heuristic"
MAX_EVIDENCE: Final = 8
PAID_TOOL_CATEGORIES: Final = frozenset({"booking", "ecommerce", "call_tracking"})
#: Rules the features read; failures and warnings of every rule are kept too (facts).
FACT_RULES: Final = frozenset(
    {
        "technical.page_errors",
        "conversion.primary_cta",
        "conversion.lead_form",
        "conversion.booking_or_quote",
        "conversion.trust_signals",
        "mobile.viewport",
        "measurement.analytics",
        "measurement.advertising",
        "content.freshness",
    }
)
POSITIVE_STAGES: Final = (
    LeadStage.QUALIFIED,
    LeadStage.MEETING_BOOKED,
    LeadStage.PROPOSAL_SENT,
    LeadStage.WON,
)
NEGATIVE_STAGES: Final = (LeadStage.LOST, LeadStage.NOT_INTERESTED, LeadStage.INVALID_CONTACT)
PRECISION_KS: Final = (10, 25, 50, 100)
MIN_LABELS: Final = 30
TIER_ORDER: Final = {
    LeadTier.PRIORITY: 0,
    LeadTier.REVIEW: 1,
    LeadTier.LOW: 2,
    LeadTier.INSUFFICIENT_DATA: 3,
    LeadTier.EXCLUDED: 4,
}
_NOT_WORD: Final = re.compile(r"[^\w]+")
_LATINO_AFFINITY: Final = frozenset(
    status for status in LatinoStatus if status is not LatinoStatus.UNKNOWN
)
_UNUSABLE: Final = frozenset(
    {ValidationStatus.INVALID, ValidationStatus.SUPPRESSED, ValidationStatus.STALE}
)
_BASE_FORMULA: Final = (
    "100 * Σ(value * weight) / Σ weight over known features with a weight; unknown and not "
    "applicable features are left out and lower the coverage"
)


class ScoringError(ValueError):
    """A definition, a run or an action the engine refuses; the message says why."""


# ---- features ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Observation:
    state: FeatureState
    value: float | None
    detail: str
    reason: str | None = None
    evidence: tuple[ScoreEvidence, ...] = ()


def known(value: float, detail: str, evidence: Iterable[ScoreEvidence]) -> Observation:
    """A known value rests on evidence; without any it is unknown, whatever it would have been."""
    found = tuple(evidence)[:MAX_EVIDENCE]
    if not found:
        return unknown("no_evidence", detail)
    return Observation(FeatureState.KNOWN, round(value, 4), detail, None, found)


def unknown(reason: str, detail: str) -> Observation:
    return Observation(FeatureState.UNKNOWN, None, detail, reason)


def not_applicable(reason: str, detail: str) -> Observation:
    return Observation(FeatureState.NOT_APPLICABLE, None, detail, reason)


@dataclass(frozen=True, slots=True)
class Context:
    facts: BusinessFacts
    icp: IcpDefinition
    scoring: ScoringDefinition
    findings: Mapping[str, FactFinding]
    subscores: Mapping[WebsiteCategory, int | None]
    offers: tuple[Offer, ...]

    def finding(self, rule_id: str) -> FactFinding | None:
        found = self.findings.get(rule_id)
        results = (WebsiteFindingResult.UNKNOWN, WebsiteFindingResult.NOT_APPLICABLE)
        return None if found is None or found.result in results else found


def business_evidence(facts: BusinessFacts) -> ScoreEvidence:
    return ScoreEvidence(
        kind=ScoreEvidenceKind.BUSINESS,
        ref=facts.business_id,
        label=facts.display_name[:300] or facts.business_id,
        url=None,
    )


def audit_evidence(audit: FactAudit) -> ScoreEvidence:
    return ScoreEvidence(
        kind=ScoreEvidenceKind.WEBSITE_AUDIT, ref=audit.id, label=audit.site, url=None
    )


def finding_evidence(finding: FactFinding) -> ScoreEvidence:
    label = f"{finding.rule_id}: {finding.result.value}"
    return ScoreEvidence(
        kind=ScoreEvidenceKind.WEBSITE_FINDING, ref=finding.id, label=label, url=finding.url
    )


def _site(ctx: Context) -> Observation | None:
    """Not applicable without a known website, unknown while it has no audit, else None."""
    if not ctx.facts.domains and ctx.facts.audit is None:
        return not_applicable("no_website", "No website is known for this business")
    if ctx.facts.audit is None:
        return unknown("no_audit", "The website has not been crawled and audited")
    return None


def _from_rule(
    ctx: Context, rule_id: str, values: Mapping[WebsiteFindingResult, float], detail: str
) -> Observation:
    if (early := _site(ctx)) is not None:
        return early
    finding = ctx.finding(rule_id)
    if finding is None or finding.result not in values:
        return unknown("finding_unknown", f"{rule_id} could not be evaluated")
    observed = f": {finding.observed_value}" if finding.observed_value else ""
    return known(values[finding.result], f"{detail}{observed}"[:300], [finding_evidence(finding)])


_PROBLEM: Final = {
    WebsiteFindingResult.FAIL: 1.0,
    WebsiteFindingResult.WARNING: 0.5,
    WebsiteFindingResult.PASS: 0.0,
}
_HEALTHY: Final = {
    WebsiteFindingResult.PASS: 1.0,
    WebsiteFindingResult.WARNING: 0.5,
    WebsiteFindingResult.FAIL: 0.0,
}


def _no_website(ctx: Context) -> Observation:
    if ctx.facts.domains or ctx.facts.audit is not None:
        return not_applicable("website_known", "A website is known for this business")
    return known(1.0, "No source lists a website for this business", [business_evidence(ctx.facts)])


def _unreachable(ctx: Context) -> Observation:
    if (early := _site(ctx)) is not None:
        return early
    audit = ctx.facts.audit
    assert audit is not None  # noqa: S101 - _site returned None
    if audit.status is WebsiteAuditStatus.EMPTY:
        return known(
            1.0, "The crawl could not analyze any page of the site", [audit_evidence(audit)]
        )
    return _from_rule(ctx, "technical.page_errors", _PROBLEM, "Pages that failed to answer")


def _conversion_path(ctx: Context) -> Observation:
    if (early := _site(ctx)) is not None:
        return early
    found = [
        f
        for rule in ("conversion.primary_cta", "conversion.lead_form")
        if (f := ctx.finding(rule)) is not None and f.result in _PROBLEM
    ]
    if not found:
        return unknown("finding_unknown", "Calls to action and forms could not be evaluated")
    worst = max(found, key=lambda f: _PROBLEM[f.result])
    detail = f"{worst.rule_id}: {worst.observed_value or worst.result.value}"
    return known(_PROBLEM[worst.result], detail[:300], [finding_evidence(f) for f in found])


def _weak_category(category: WebsiteCategory, label: str) -> Callable[[Context], Observation]:
    def evaluate(ctx: Context) -> Observation:
        if (early := _site(ctx)) is not None:
            return early
        score = ctx.subscores.get(category)
        if score is None:
            return unknown("subscore_unknown", f"No {label} rule could be evaluated")
        audit = ctx.facts.audit
        assert audit is not None  # noqa: S101 - _site returned None
        failing = [
            f
            for f in audit.findings
            if f.category is category
            and f.result in (WebsiteFindingResult.FAIL, WebsiteFindingResult.WARNING)
        ]
        weak = score < ctx.scoring.weak_subscore_below
        detail = f"{label.capitalize()} subscore {score}/100"
        return known(
            1.0 if weak else 0.0,
            detail,
            [*(finding_evidence(f) for f in failing), audit_evidence(audit)],
        )

    return evaluate


def _recently_seen(ctx: Context) -> Observation:
    days = (ctx.facts.collected_at - ctx.facts.last_seen_at).days
    value = 1.0 if days <= 180 else 0.5 if days <= 365 else 0.0
    return known(
        value,
        f"Last listed by a source {days} days before these facts were read",
        [business_evidence(ctx.facts)],
    )


def _multiple_sources(ctx: Context) -> Observation:
    count = ctx.facts.source_count
    return known(
        1.0 if count >= 2 else 0.0,
        f"Listed by {count} acquired record(s)",
        [business_evidence(ctx.facts)],
    )


def _site_answers(ctx: Context) -> Observation:
    audit = ctx.facts.audit
    if audit is not None and audit.status is WebsiteAuditStatus.EMPTY:
        return known(0.0, "No page of the site could be analyzed", [audit_evidence(audit)])
    return _from_rule(ctx, "technical.page_errors", _HEALTHY, "Pages answered")


def _locations(ctx: Context) -> set[tuple[str, str, str]]:
    return {
        (sort_key(loc.locality or ""), (loc.region or "").upper(), (loc.postal_code or "")[:5])
        for loc in ctx.facts.locations
        if loc.locality or loc.region or loc.postal_code
    }


def _multiple_locations(ctx: Context) -> Observation:
    places = _locations(ctx)
    if not places:
        return unknown("no_location", "No location observed")
    return known(
        1.0 if len(places) >= 2 else 0.0,
        f"{len(places)} distinct location(s)",
        [business_evidence(ctx.facts)],
    )


def _paid_tools(ctx: Context) -> Observation:
    audit = ctx.facts.audit
    if audit is None:
        return unknown("no_audit", "No crawled site to detect tools on")
    tools = [tech for tech in audit.technologies if tech.category in PAID_TOOL_CATEGORIES]
    if not tools:
        return unknown("not_detected", "No booking, ecommerce or call tracking tool detected")
    evidence = [
        ScoreEvidence(
            kind=ScoreEvidenceKind.WEBSITE_TECHNOLOGY,
            ref=tech.id,
            label=f"{tech.name} ({tech.category})",
            url=None,
        )
        for tech in tools
    ]
    return known(1.0, "Uses " + ", ".join(tech.name for tech in tools), evidence)


def _team(ctx: Context) -> Observation:
    people = [p for p in ctx.facts.people if p.review_status is not DecisionMakerReview.REJECTED]
    audit = ctx.facts.audit
    if not people and audit is None:
        return unknown("no_audit", "No crawled site to find a published team on")
    evidence = [
        ScoreEvidence(
            kind=ScoreEvidenceKind.DECISION_MAKER,
            ref=person.id,
            label=person.role or person.relationship.value,
            url=None,
        )
        for person in people
    ]
    if not people and audit is not None:
        evidence = [audit_evidence(audit)]
    value = 1.0 if len(people) >= 2 else 0.5 if people else 0.0
    return known(value, f"{len(people)} published decision maker(s)", evidence)


def _site_depth(ctx: Context) -> Observation:
    if (early := _site(ctx)) is not None:
        return early
    audit = ctx.facts.audit
    assert audit is not None  # noqa: S101 - _site returned None
    pages = audit.pages_analyzed
    value = 1.0 if pages >= 5 else 0.5 if pages >= 2 else 0.0
    return known(value, f"{pages} page(s) analyzed", [audit_evidence(audit)])


def _advertising(ctx: Context) -> Observation:
    finding = ctx.finding("measurement.advertising")
    if finding is None or finding.result is not WebsiteFindingResult.PASS:
        return unknown("not_detected", "No advertising pixel detected (which does not mean none)")
    return known(
        1.0,
        f"Advertising detected: {finding.observed_value or 'pixel'}"[:300],
        [finding_evidence(finding)],
    )


def _site_changed(ctx: Context) -> Observation:
    if (early := _site(ctx)) is not None:
        return early
    audit = ctx.facts.audit
    assert audit is not None  # noqa: S101 - _site returned None
    if audit.previous_audit_id is None:
        return unknown("single_audit", "Only one audit of the site: nothing to compare")
    recent = (ctx.facts.collected_at - audit.created_at).days <= 90
    value = 1.0 if recent and audit.changes else 0.0
    return known(
        value, f"{audit.changes} change(s) since the previous audit", [audit_evidence(audit)]
    )


def _reputation_without_conversion(ctx: Context) -> Observation:
    if (early := _site(ctx)) is not None:
        return early
    trust = ctx.finding("conversion.trust_signals")
    conversion = ctx.subscores.get(WebsiteCategory.CONVERSION)
    if trust is None or conversion is None:
        return unknown("finding_unknown", "Trust signals or the conversion subscore are unknown")
    audit = ctx.facts.audit
    assert audit is not None  # noqa: S101 - _site returned None
    strong = trust.result is WebsiteFindingResult.PASS
    weak = conversion < ctx.scoring.weak_subscore_below
    detail = f"Trust signals {trust.result.value}, conversion subscore {conversion}/100"
    return known(
        1.0 if strong and weak else 0.0, detail, [finding_evidence(trust), audit_evidence(audit)]
    )


def _freshness(ctx: Context) -> Observation:
    facts = ctx.facts
    newest = max(facts.last_seen_at, facts.audit.created_at if facts.audit else facts.last_seen_at)
    days = (facts.collected_at - newest).days
    value = 1.0 if days <= 30 else 0.7 if days <= 90 else 0.4 if days <= 180 else 0.1
    evidence = [audit_evidence(facts.audit)] if facts.audit else [business_evidence(facts)]
    return known(value, f"Newest data is {days} days old", evidence)


def _agreement(ctx: Context) -> Observation:
    conflicts = ctx.facts.conflict_count
    return known(
        max(0.0, 1 - 0.25 * conflicts),
        f"{conflicts} field(s) whose records disagree",
        [business_evidence(ctx.facts)],
    )


def _audit_coverage(ctx: Context) -> Observation:
    if (early := _site(ctx)) is not None:
        return early
    audit = ctx.facts.audit
    assert audit is not None  # noqa: S101 - _site returned None
    return known(
        audit.coverage, f"Audit coverage {round(audit.coverage * 100)}%", [audit_evidence(audit)]
    )


@dataclass(frozen=True, slots=True)
class Feature:
    id: str
    component: ScoreComponent
    problem: ProblemType | None
    #: The declared heuristic weight of the default model.
    weight: int
    description: str
    evaluate: Callable[[Context], Observation]


_GAP, _HEALTH, _CAPACITY, _INTENT, _DATA = (
    ScoreComponent.DIGITAL_GAP,
    ScoreComponent.BUSINESS_HEALTH,
    ScoreComponent.BUYING_CAPACITY,
    ScoreComponent.COMMERCIAL_INTENT,
    ScoreComponent.DATA_CONFIDENCE,
)
_COVERAGE = "data.coverage"

FEATURES: Final[tuple[Feature, ...]] = (
    Feature(
        "gap.no_website",
        _GAP,
        ProblemType.NO_WEBSITE,
        3,
        "No source lists a website for the business.",
        _no_website,
    ),
    Feature(
        "gap.website_unreachable",
        _GAP,
        ProblemType.WEBSITE_UNREACHABLE,
        3,
        "The crawl could not analyze the site, or its pages failed (technical.page_errors).",
        _unreachable,
    ),
    Feature(
        "gap.conversion_path",
        _GAP,
        ProblemType.WEAK_CONVERSION_PATH,
        3,
        "No main call to action or lead form (conversion.primary_cta, conversion.lead_form).",
        _conversion_path,
    ),
    Feature(
        "gap.online_booking",
        _GAP,
        ProblemType.NO_ONLINE_BOOKING,
        1,
        "No booking tool, booking or quote action (conversion.booking_or_quote).",
        lambda ctx: _from_rule(
            ctx,
            "conversion.booking_or_quote",
            {WebsiteFindingResult.WARNING: 1.0, WebsiteFindingResult.PASS: 0.0},
            "Booking or quote",
        ),
    ),
    Feature(
        "gap.local_seo",
        _GAP,
        ProblemType.WEAK_LOCAL_SEO,
        2,
        "The local SEO subscore is below the model's weak threshold.",
        _weak_category(WebsiteCategory.LOCAL_SEO, "local SEO"),
    ),
    Feature(
        "gap.technical_seo",
        _GAP,
        ProblemType.WEAK_TECHNICAL_SEO,
        1,
        "The SEO subscore is below the model's weak threshold.",
        _weak_category(WebsiteCategory.SEO, "SEO"),
    ),
    Feature(
        "gap.mobile",
        _GAP,
        ProblemType.POOR_MOBILE_EXPERIENCE,
        2,
        "No usable mobile viewport (mobile.viewport).",
        lambda ctx: _from_rule(ctx, "mobile.viewport", _PROBLEM, "Mobile viewport"),
    ),
    Feature(
        "gap.measurement",
        _GAP,
        ProblemType.MEASUREMENT_NOT_DETECTED,
        1,
        "Analytics not detected (measurement.analytics); not detected is not absent.",
        lambda ctx: _from_rule(
            ctx,
            "measurement.analytics",
            {WebsiteFindingResult.WARNING: 1.0, WebsiteFindingResult.PASS: 0.0},
            "Analytics",
        ),
    ),
    Feature(
        "health.recently_seen",
        _HEALTH,
        None,
        2,
        "A source listed the business recently (180 days: 1, a year: 0.5).",
        _recently_seen,
    ),
    Feature(
        "health.site_answers",
        _HEALTH,
        None,
        2,
        "The site's pages answered (technical.page_errors).",
        _site_answers,
    ),
    Feature(
        "health.site_maintained",
        _HEALTH,
        None,
        1,
        "The site shows a current copyright year (content.freshness).",
        lambda ctx: _from_rule(
            ctx,
            "content.freshness",
            {WebsiteFindingResult.PASS: 1.0, WebsiteFindingResult.WARNING: 0.0},
            "Copyright year",
        ),
    ),
    Feature(
        "health.trust_signals",
        _HEALTH,
        None,
        1,
        "Testimonials, licences, reviews or similar shown (conversion.trust_signals).",
        lambda ctx: _from_rule(ctx, "conversion.trust_signals", _HEALTHY, "Trust signals"),
    ),
    Feature(
        "health.multiple_sources",
        _HEALTH,
        None,
        1,
        "More than one acquired record lists the business.",
        _multiple_sources,
    ),
    Feature(
        "capacity.multiple_locations",
        _CAPACITY,
        None,
        2,
        "Two or more distinct locations observed (a size proxy).",
        _multiple_locations,
    ),
    Feature(
        "capacity.paid_tools",
        _CAPACITY,
        None,
        1,
        "A booking, ecommerce or call tracking tool detected (a spending proxy).",
        _paid_tools,
    ),
    Feature(
        "capacity.team_published",
        _CAPACITY,
        None,
        2,
        "Decision makers published (two or more: 1, one: 0.5).",
        _team,
    ),
    Feature(
        "capacity.site_depth",
        _CAPACITY,
        None,
        1,
        "Pages analyzed on the site (five or more: 1, two: 0.5).",
        _site_depth,
    ),
    Feature(
        "intent.advertising_active",
        _INTENT,
        None,
        2,
        "An advertising pixel or call tracking detected (measurement.advertising).",
        _advertising,
    ),
    Feature(
        "intent.site_changed",
        _INTENT,
        None,
        1,
        "The site changed since its previous audit, audited within 90 days.",
        _site_changed,
    ),
    Feature(
        "intent.reputation_without_conversion",
        _INTENT,
        None,
        2,
        "Trust signals pass while the conversion subscore is weak.",
        _reputation_without_conversion,
    ),
    Feature(
        _COVERAGE,
        _DATA,
        None,
        3,
        "Share of weighted opportunity features that are known.",
        lambda ctx: unknown("computed_later", "Computed from the other features"),
    ),
    Feature(
        "data.freshness",
        _DATA,
        None,
        2,
        "Age of the newest data (30 days: 1, 90: 0.7, 180: 0.4, older: 0.1).",
        _freshness,
    ),
    Feature(
        "data.agreement",
        _DATA,
        None,
        1,
        "Fields whose records disagree (each one removes 0.25).",
        _agreement,
    ),
    Feature(
        "data.audit_coverage",
        _DATA,
        None,
        1,
        "Share of the site's expected pages the audit analyzed.",
        _audit_coverage,
    ),
)
FEATURE_BY_ID: Final = {feature.id: feature for feature in FEATURES}
PROBLEM_FEATURE: Final = {f.problem: f.id for f in FEATURES if f.problem is not None}
_OPPORTUNITY_COMPONENTS: Final = (_GAP, _HEALTH, _CAPACITY, _INTENT)

PROBLEM_PHRASES: Final[Mapping[ProblemType, tuple[str, str]]] = {
    ProblemType.NO_WEBSITE: (
        "no website is listed for the business",
        "Customers searching online probably cannot find or evaluate the business.",
    ),
    ProblemType.WEBSITE_UNREACHABLE: (
        "the website could not be loaded reliably",
        "Visitors who reach the site probably leave before they can make contact.",
    ),
    ProblemType.WEAK_CONVERSION_PATH: (
        "the site has no clear call to action or enquiry form",
        "Visitors ready to buy have no obvious next step, so some enquiries are probably lost.",
    ),
    ProblemType.NO_ONLINE_BOOKING: (
        "there is no way to book or request a quote online",
        "Customers who prefer to book online may choose a competitor that lets them.",
    ),
    ProblemType.WEAK_LOCAL_SEO: (
        "local search signals such as business details and structured data are weak",
        "The business probably appears less often in local search and maps.",
    ),
    ProblemType.WEAK_TECHNICAL_SEO: (
        "basic search engine settings need work",
        "Search engines may index and present the site poorly.",
    ),
    ProblemType.POOR_MOBILE_EXPERIENCE: (
        "the site is hard to use on a phone",
        "Mobile visitors probably struggle to read the site and act on it.",
    ),
    ProblemType.MEASUREMENT_NOT_DETECTED: (
        "we could not see analytics on the site",
        "The business may not know which channels bring customers; analytics could also be hidden.",
    ),
}


def feature_catalog() -> list[FeatureInfo]:
    return [
        FeatureInfo(
            feature=f.id, component=f.component, problem=f.problem, description=f.description
        )
        for f in FEATURES
    ]


# ---- definitions ---------------------------------------------------------------------------------


def default_scoring() -> ScoringDefinition:
    """The declared heuristic model: every weight is a judgement, not fitted to outcomes."""
    return ScoringDefinition(
        schema_version=1,
        features=[FeatureWeight(feature=f.id, weight=f.weight) for f in FEATURES],
        opportunity=[
            OpportunityWeight(component=OpportunityInput.DIGITAL_GAP, weight=45),
            OpportunityWeight(component=OpportunityInput.BUSINESS_HEALTH, weight=20),
            OpportunityWeight(component=OpportunityInput.BUYING_CAPACITY, weight=20),
            OpportunityWeight(component=OpportunityInput.COMMERCIAL_INTENT, weight=15),
        ],
        final=[
            FinalWeight(component=FinalInput.ICP_MATCH, weight=35),
            FinalWeight(component=FinalInput.OPPORTUNITY_SCORE, weight=40),
            FinalWeight(component=FinalInput.CONTACTABILITY, weight=25),
            FinalWeight(component=FinalInput.LATINO_CONFIDENCE, weight=0),
        ],
        not_applicable=[
            NotApplicableRule(
                feature="gap.online_booking",
                categories=[
                    "roofing",
                    "construction",
                    "general contractor",
                    "wholesale",
                    "manufacturing",
                ],
            ),
        ],
        min_known_weight=0.6,
        priority_threshold=70,
        review_threshold=50,
        weak_subscore_below=60,
        require_observable_problem=True,
    )


def icp_template() -> IcpDefinition:
    """The starting point of a new profile: a valid shape with nothing invented in it."""
    ignore = CriterionImportance.IGNORE
    return IcpDefinition.model_validate(
        {
            "schema_version": 1,
            "geography": {"importance": ignore, "weight": 1, "include": [], "exclude": []},
            "categories": {"importance": ignore, "weight": 1, "terms": [], "excluded_terms": []},
            "size": {"importance": ignore, "weight": 1, "min_locations": 1},
            "freshness": {"importance": ignore, "weight": 1, "max_days_since_seen": 365},
            "reputation": {
                "importance": ignore,
                "weight": 1,
                "min_rating": None,
                "min_reviews": None,
            },
            "digital_maturity": {"importance": ignore, "weight": 1, "wanted": []},
            "problems": {"importance": ignore, "weight": 1, "wanted": []},
            "channels": {
                "importance": ignore,
                "weight": 1,
                "any_of": [],
                "require_decision_maker": False,
            },
            "buying_capacity": {"importance": ignore, "weight": 1, "min_proxies": 1},
            "latino": LatinoRequirement.NONE,
            "exclusions": {
                "names": [],
                "domains": [],
                "categories": [],
                "regions": [],
                "postal_codes": [],
            },
            "offers": [],
        }
    )


def _problem(severity: ProblemSeverity, code: str, path: str, message: str) -> CriteriaProblem:
    return CriteriaProblem(severity=severity, code=code, path=path, message=message)


def check_icp(definition: IcpDefinition) -> list[CriteriaProblem]:
    """Why a profile cannot run (errors) or deserves a second look (warnings)."""
    error, warning = ProblemSeverity.ERROR, ProblemSeverity.WARNING
    problems: list[CriteriaProblem] = []
    if not definition.offers:
        problems.append(
            _problem(
                error,
                "no_offers",
                "offers",
                "Declare at least one offer the seller can actually deliver",
            )
        )
    keys = [offer.key for offer in definition.offers]
    for index, offer in enumerate(definition.offers):
        if not offer.solves:
            problems.append(
                _problem(
                    error,
                    "offer_solves_nothing",
                    f"offers[{index}].solves",
                    f"Offer '{offer.name}' must solve at least one problem",
                )
            )
        if keys.count(offer.key) > 1:
            problems.append(
                _problem(
                    error,
                    "duplicate_offer_key",
                    f"offers[{index}].key",
                    f"Offer key '{offer.key}' is used twice",
                )
            )
    must = CriterionImportance.MUST_HAVE
    empty = {
        "geography": definition.geography.importance is must and not definition.geography.include,
        "categories": definition.categories.importance is must and not definition.categories.terms,
        "digital_maturity": definition.digital_maturity.importance is must
        and not definition.digital_maturity.wanted,
        "problems": definition.problems.importance is must and not definition.problems.wanted,
        "channels": definition.channels.importance is must
        and not definition.channels.any_of
        and not definition.channels.require_decision_maker,
    }
    problems += [
        _problem(error, "empty_must_have", name, f"The must-have '{name}' criterion names nothing")
        for name, is_empty in empty.items()
        if is_empty
    ]
    if definition.reputation.importance is not CriterionImportance.IGNORE:
        problems.append(
            _problem(
                warning,
                "reputation_unknown",
                "reputation",
                "No connected source provides ratings or reviews: this criterion is always unknown",
            )
        )
    if all(
        criterion.importance is CriterionImportance.IGNORE
        for _, criterion in _criteria_of(definition)
    ):
        problems.append(
            _problem(
                warning,
                "nothing_required",
                "",
                "Every criterion is ignored: any business matches this profile",
            )
        )
    return problems


def check_scoring(definition: ScoringDefinition) -> list[CriteriaProblem]:
    error, warning = ProblemSeverity.ERROR, ProblemSeverity.WARNING
    problems: list[CriteriaProblem] = []
    ids = [weight.feature for weight in definition.features]
    if sorted(ids) != sorted(FEATURE_BY_ID) or len(set(ids)) != len(ids):
        problems.append(
            _problem(
                error,
                "features_mismatch",
                "features",
                "Weigh every feature of the engine exactly once",
            )
        )
    for index, rule in enumerate(definition.not_applicable):
        if rule.feature not in FEATURE_BY_ID:
            problems.append(
                _problem(
                    error,
                    "unknown_feature",
                    f"not_applicable[{index}].feature",
                    f"'{rule.feature}' is not a feature of the engine",
                )
            )
    formulas: tuple[tuple[str, Sequence[OpportunityWeight | FinalWeight], int], ...] = (
        ("opportunity", definition.opportunity, len(OpportunityInput)),
        ("final", definition.final, len(FinalInput)),
    )
    for name, weights, expected in formulas:
        if (
            len({weight.component for weight in weights}) != len(weights)
            or len(weights) != expected
        ):
            problems.append(
                _problem(
                    error,
                    "inputs_mismatch",
                    name,
                    f"Weigh every input of the {name} formula exactly once",
                )
            )
        if not any(weight.weight for weight in weights):
            problems.append(
                _problem(
                    error,
                    "nothing_weighted",
                    name,
                    f"The {name} formula needs at least one weighted input",
                )
            )
    if definition.review_threshold > definition.priority_threshold:
        problems.append(
            _problem(
                error,
                "thresholds_order",
                "review_threshold",
                "The review threshold cannot be above the priority threshold",
            )
        )
    if any(w.component is FinalInput.LATINO_CONFIDENCE and w.weight for w in definition.final):
        problems.append(
            _problem(
                warning,
                "latino_weighted",
                "final",
                "Latino confidence moves the final score; campaigns usually "
                "filter by the Latino requirement instead",
            )
        )
    return problems


# ---- matching ------------------------------------------------------------------------------------


def words(text: str) -> str:
    """Lower case, no accents, words separated by one space: what terms are matched against."""
    return " ".join(_NOT_WORD.sub(" ", sort_key(text)).split())


def matches_term(labels: Iterable[str], terms: Iterable[str]) -> str | None:
    """The first term found as whole words in a label, or None."""
    padded = [f" {words(label)} " for label in labels]
    for term in terms:
        wanted = words(term)
        if wanted and any(f" {wanted} " in label for label in padded):
            return term
    return None


def _in_area(facts: BusinessFacts, area: Area) -> bool:
    for loc in facts.locations:
        parts: list[tuple[str | None, str | None]] = [
            (area.country_code, loc.country_code),
            (area.region, loc.region),
            (area.locality and words(area.locality), loc.locality and words(loc.locality)),
        ]
        if loc.country_code is None and area.country_code == "US":
            parts = parts[1:]  # most US listings carry no country; the region decides
        if all(
            wanted is None or (found or "").upper() == wanted.upper() for wanted, found in parts
        ) and (
            area.postal_code is None
            or (loc.postal_code or "")
            .replace(" ", "")
            .upper()
            .startswith(area.postal_code.replace(" ", "").upper())
        ):
            return True
    return False


def applicable_offers(facts: BusinessFacts, icp: IcpDefinition) -> tuple[Offer, ...]:
    return tuple(
        o for o in icp.offers if matches_term(facts.categories, o.not_for_categories) is None
    )


# ---- criteria and gates --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Decision:
    outcome: GateOutcome
    detail: str
    evidence: tuple[ScoreEvidence, ...] = ()


Criterion = (
    GeographyCriterion
    | CategoryCriterion
    | SizeCriterion
    | FreshnessCriterion
    | ReputationCriterion
    | MaturityCriterion
    | ProblemCriterion
    | ChannelCriterion
    | CapacityCriterion
)


def _criteria_of(icp: IcpDefinition) -> tuple[tuple[str, Criterion], ...]:
    return (
        ("geography", icp.geography),
        ("categories", icp.categories),
        ("size", icp.size),
        ("freshness", icp.freshness),
        ("reputation", icp.reputation),
        ("digital_maturity", icp.digital_maturity),
        ("problems", icp.problems),
        ("channels", icp.channels),
        ("buying_capacity", icp.buying_capacity),
    )


def maturity(facts: BusinessFacts) -> tuple[DigitalMaturity | None, str]:
    if not facts.domains and facts.audit is None:
        return DigitalMaturity.LOW, "No website is known"
    if facts.audit is None:
        return None, "The website has not been audited"
    scores = [s.score for s in facts.audit.subscores if s.score is not None]
    if not scores:
        return None, "No website rule could be evaluated"
    mean = fsum(scores) / len(scores)
    band = (
        DigitalMaturity.LOW
        if mean < 50
        else DigitalMaturity.MEDIUM
        if mean < 80
        else DigitalMaturity.HIGH
    )
    return band, f"Mean website subscore {round(mean)}/100"


def _decide_criterion(name: str, ctx: Context, observations: Mapping[str, Observation]) -> Decision:
    facts, icp = ctx.facts, ctx.icp
    business = (business_evidence(facts),)
    pass_, fail, undecided = GateOutcome.PASS, GateOutcome.FAIL, GateOutcome.UNKNOWN
    match name:
        case "geography":
            if not facts.locations:
                return Decision(undecided, "No location observed")
            place = ", ".join(
                filter(None, (facts.locations[0].locality, facts.locations[0].region))
            )
            if any(_in_area(facts, area) for area in icp.geography.exclude):
                return Decision(fail, f"In an excluded area ({place})", business)
            if not icp.geography.include or any(_in_area(facts, a) for a in icp.geography.include):
                return Decision(pass_, f"In the target geography ({place or 'observed'})", business)
            return Decision(fail, f"Outside the target geography ({place or 'observed'})", business)
        case "categories":
            if not facts.categories:
                return Decision(undecided, "No category or service observed")
            if (term := matches_term(facts.categories, icp.categories.excluded_terms)) is not None:
                return Decision(fail, f"Excluded category '{term}'", business)
            if not icp.categories.terms:
                return Decision(pass_, "Any category", business)
            if (term := matches_term(facts.categories, icp.categories.terms)) is not None:
                return Decision(pass_, f"Category matches '{term}'", business)
            return Decision(
                fail, f"Categories do not match: {', '.join(facts.categories[:3])}"[:300], business
            )
        case "size":
            places = len(_locations(ctx))
            if not places:
                return Decision(undecided, "No location observed")
            outcome = pass_ if places >= icp.size.min_locations else fail
            return Decision(
                outcome, f"{places} location(s), {icp.size.min_locations} wanted", business
            )
        case "freshness":
            days = (facts.collected_at - facts.last_seen_at).days
            outcome = pass_ if days <= icp.freshness.max_days_since_seen else fail
            return Decision(outcome, f"Last listed {days} days ago", business)
        case "reputation":
            return Decision(undecided, "No connected source provides ratings or reviews")
        case "digital_maturity":
            band, detail = maturity(facts)
            if band is None:
                return Decision(undecided, detail)
            shown = (audit_evidence(facts.audit),) if facts.audit else business
            bands = icp.digital_maturity.wanted
            return Decision(
                pass_ if not bands or band in bands else fail, f"{detail}: {band.value}", shown
            )
        case "problems":
            problems = icp.problems.wanted
            if not problems:
                return Decision(pass_, "Any problem", business)
            seen = [observations[PROBLEM_FEATURE[p]] for p in problems]
            hits = [(p, o) for p, o in zip(problems, seen, strict=True) if _observed(o)]
            if hits:
                cited = tuple(e for _, o in hits for e in o.evidence)[:MAX_EVIDENCE]
                return Decision(pass_, "Shows " + ", ".join(p.value for p, _ in hits), cited)
            if any(o.state is FeatureState.UNKNOWN for o in seen):
                return Decision(undecided, "A wanted problem could not be evaluated")
            return Decision(fail, "None of the wanted problems is observed", business)
        case "channels":
            usable = [c for c in facts.channels if c.validation_status not in _UNUSABLE]
            wanted_types = icp.channels.any_of
            typed = [c for c in usable if not wanted_types or c.contact_type in wanted_types]
            people = [
                p for p in facts.people if p.review_status is not DecisionMakerReview.REJECTED
            ]
            channels = tuple(
                ScoreEvidence(
                    kind=ScoreEvidenceKind.CONTACT_CHANNEL,
                    ref=c.key[:400],
                    label=f"{c.contact_type.value}: {c.validation_status.value}",
                    url=None,
                )
                for c in typed
            )[:MAX_EVIDENCE]
            if typed and (people or not icp.channels.require_decision_maker):
                return Decision(pass_, f"{len(typed)} usable channel(s)", channels)
            if not facts.channels and facts.audit is None:
                return Decision(undecided, "No channel observed and no site crawled")
            missing = "no usable channel of the wanted types" if not typed else "no decision maker"
            return Decision(fail, f"Published: {missing}", channels or business)
        case "buying_capacity":
            proxies = [observations[f.id] for f in FEATURES if f.component is _CAPACITY]
            count = sum(
                1 for o in proxies if o.state is FeatureState.KNOWN and (o.value or 0) >= 0.5
            )
            open_ = sum(1 for o in proxies if o.state is FeatureState.UNKNOWN)
            needed = icp.buying_capacity.min_proxies
            found = tuple(e for o in proxies if (o.value or 0) >= 0.5 for e in o.evidence)
            if count >= needed:
                return Decision(pass_, f"{count} capacity proxies observed", found[:MAX_EVIDENCE])
            if count + open_ < needed:
                return Decision(
                    fail, f"{count} capacity proxies observed, {needed} wanted", business
                )
            return Decision(undecided, f"{count} capacity proxies observed, {open_} unknown")
    raise AssertionError(name)


def _observed(observation: Observation) -> bool:
    return observation.state is FeatureState.KNOWN and (observation.value or 0) >= 0.5


def _gates(ctx: Context, observations: Mapping[str, Observation]) -> list[GateView]:
    facts, icp = ctx.facts, ctx.icp
    business = [business_evidence(facts)]
    gates: list[tuple[str, Decision]] = [
        (
            "gate.do_not_contact",
            Decision(
                GateOutcome.FAIL if facts.business_suppressed else GateOutcome.PASS,
                "The business is on the do-not-contact list"
                if facts.business_suppressed
                else "Not on the do-not-contact list",
                tuple(business),
            ),
        ),
        (
            "gate.business_closed",
            Decision(
                GateOutcome.FAIL if facts.status is BusinessStatus.CLOSED else GateOutcome.PASS,
                f"Business status: {facts.status.value}",
                tuple(business),
            ),
        ),
    ]
    ex = icp.exclusions
    hits = (
        [f"name '{term}'" for term in [matches_term(facts.names, ex.names)] if term]
        + [f"category '{term}'" for term in [matches_term(facts.categories, ex.categories)] if term]
        + [
            f"domain {domain}"
            for domain in facts.domains
            if any(domain == d or domain.endswith(f".{d}") for d in ex.domains)
        ]
        + [
            f"region {loc.region}"
            for loc in facts.locations
            if loc.region and loc.region in ex.regions
        ]
        + [
            f"postal code {loc.postal_code}"
            for loc in facts.locations
            if loc.postal_code and any(loc.postal_code.startswith(code) for code in ex.postal_codes)
        ]
    )
    gates.append(
        (
            "gate.exclusions",
            Decision(
                GateOutcome.FAIL if hits else GateOutcome.PASS,
                ("Excluded by " + ", ".join(sorted(set(hits))))[:300]
                if hits
                else "No exclusion applies",
                tuple(business),
            ),
        )
    )
    if icp.latino is not LatinoRequirement.NONE:
        gates.append(("gate.latino", _latino_gate(facts, icp.latino)))
    if ctx.scoring.require_observable_problem:
        problems = [observations[f.id] for f in FEATURES if f.problem is not None]
        seen = [o for o in problems if _observed(o)]
        # a problem no offer solves was still evaluated: it decides the gate, it cannot pass it
        decided = [
            o for o in problems if o.state is FeatureState.KNOWN or o.reason == "no_offer_solves"
        ]
        if seen:
            decision = Decision(
                GateOutcome.PASS,
                f"{len(seen)} problem(s) an offer can solve",
                tuple(e for o in seen for e in o.evidence)[:MAX_EVIDENCE],
            )
        elif decided:
            decision = Decision(
                GateOutcome.FAIL,
                "No problem an offer can solve is observed",
                tuple(e for o in decided for e in o.evidence)[:MAX_EVIDENCE],
            )
        else:
            decision = Decision(
                GateOutcome.UNKNOWN, "No problem an offer can solve could be evaluated"
            )
        gates.append(("gate.observable_problem", decision))
    for name, criterion in _criteria_of(icp):
        if criterion.importance is CriterionImportance.MUST_HAVE:
            gates.append((f"criterion.{name}", _decide_criterion(name, ctx, observations)))
    return [
        GateView(gate=gate, outcome=d.outcome, detail=d.detail[:300], evidence=list(d.evidence))
        for gate, d in gates
    ]


def _latino_gate(facts: BusinessFacts, requirement: LatinoRequirement) -> Decision:
    latino = facts.latino
    if latino is None:
        return Decision(GateOutcome.UNKNOWN, "The Latino evidence has not been evaluated")
    evidence = (
        ScoreEvidence(
            kind=ScoreEvidenceKind.LATINO_ASSESSMENT,
            ref=latino.assessment_id,
            label=f"{latino.status.value} ({latino.method.value})",
            url=None,
        ),
    )
    wanted = (
        {LatinoStatus.VERIFIED_LATINO_OWNED}
        if requirement is LatinoRequirement.VERIFIED_LATINO_OWNED
        else _LATINO_AFFINITY
    )
    outcome = GateOutcome.PASS if latino.status in wanted else GateOutcome.FAIL
    return Decision(
        outcome, f"Latino evidence: {latino.status.value}, {requirement.value} wanted", evidence
    )


# ---- the scorecard -------------------------------------------------------------------------------


@dataclass(slots=True)
class _Formula:
    """A weighted mean over the known inputs of one formula."""

    weights: dict[str, float]
    values: dict[str, float | None]
    known: float = 0.0
    total: float = 0.0
    value: float | None = None
    contributions: dict[str, float | None] = field(default_factory=dict)

    def solve(self) -> "_Formula":
        weighted = {k: w for k, w in self.weights.items() if w > 0}
        self.total = fsum(weighted.values())
        present = {k: w for k, w in weighted.items() if self.values.get(k) is not None}
        self.known = fsum(present.values())
        self.contributions = {
            k: (self.values[k] or 0.0) * present[k] / self.known if k in present else None
            for k in self.weights
        }
        if self.known:
            self.value = fsum(c for c in self.contributions.values() if c is not None)
        return self

    def share(self, key: str) -> float:
        """The share of this formula's value one known input moves."""
        return (
            self.weights.get(key, 0.0) / self.known
            if self.known and self.values.get(key) is not None
            else 0.0
        )

    def coverage(self) -> float:
        return round(self.known / self.total, 4) if self.total else 0.0


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def score(
    facts: BusinessFacts, icp: IcpDefinition, scoring: ScoringDefinition, versions: ScoreVersions
) -> LeadScorecard:
    """The scorecard of one business. Deterministic for the same facts and definitions."""
    audit = facts.audit
    ctx = Context(
        facts=facts,
        icp=icp,
        scoring=scoring,
        findings={f.rule_id: f for f in audit.findings} if audit else {},
        subscores={s.category: s.score for s in audit.subscores} if audit else {},
        offers=applicable_offers(facts, icp),
    )
    weights = {w.feature: w.weight for w in scoring.features}
    solvable = {problem for offer in ctx.offers for problem in offer.solves}
    observations: dict[str, Observation] = {}
    for feature in FEATURES:
        observation = feature.evaluate(ctx)
        rule = next(
            (
                r
                for r in scoring.not_applicable
                if r.feature == feature.id and matches_term(facts.categories, r.categories)
            ),
            None,
        )
        if rule is not None:
            observation = not_applicable(
                "not_applicable_to_category",
                f"Does not apply to {matches_term(facts.categories, rule.categories)}",
            )
        elif (
            feature.problem is not None
            and feature.problem not in solvable
            and observation.state is FeatureState.KNOWN
        ):
            observation = replace(
                observation, state=FeatureState.NOT_APPLICABLE, value=None, reason="no_offer_solves"
            )
        observations[feature.id] = observation
    opportunity_ids = [
        f.id for f in FEATURES if f.component in _OPPORTUNITY_COMPONENTS and weights.get(f.id)
    ]
    decided = [
        observations[i]
        for i in opportunity_ids
        if observations[i].state is not FeatureState.NOT_APPLICABLE
    ]
    known_weight = fsum(
        weights[i] for i in opportunity_ids if observations[i].state is FeatureState.KNOWN
    )
    open_weight = fsum(
        weights[i] for i in opportunity_ids if observations[i].state is FeatureState.UNKNOWN
    )
    if decided and known_weight + open_weight:
        observations[_COVERAGE] = known(
            known_weight / (known_weight + open_weight),
            f"{round(100 * known_weight / (known_weight + open_weight))}% "
            "of the weighted opportunity features are known",
            [business_evidence(facts)],
        )
    else:
        observations[_COVERAGE] = unknown(
            "nothing_weighted", "No weighted opportunity feature applies"
        )

    base: dict[ScoreComponent, _Formula] = {}
    for component in (*_OPPORTUNITY_COMPONENTS, _DATA):
        members = [f for f in FEATURES if f.component is component]
        base[component] = _Formula(
            {f.id: float(weights.get(f.id, 0)) for f in members},
            {
                f.id: observations[f.id].value
                if observations[f.id].state is FeatureState.KNOWN
                else None
                for f in members
            },
        ).solve()

    criteria_views, icp_formula = _icp_match(ctx, observations)
    contact = facts.contactability
    latino = facts.latino
    latino_value = (
        round(latino.confidence * 100, 4)
        if latino is not None and latino.status is not LatinoStatus.UNKNOWN
        else None
    )
    to_points = {k: (None if f.value is None else 100 * f.value) for k, f in base.items()}
    opportunity = _Formula(
        {w.component.value: float(w.weight) for w in scoring.opportunity},
        {c.value: to_points[c] for c in _OPPORTUNITY_COMPONENTS},
    ).solve()
    final = _Formula(
        {w.component.value: float(w.weight) for w in scoring.final},
        {
            FinalInput.ICP_MATCH.value: icp_formula.value,
            FinalInput.OPPORTUNITY_SCORE.value: opportunity.value,
            FinalInput.CONTACTABILITY.value: None
            if contact.score is None
            else float(contact.score),
            FinalInput.LATINO_CONFIDENCE.value: latino_value,
        },
    ).solve()
    gates = _gates(ctx, observations)

    def impact_of(component: ScoreComponent, points: float | None) -> float | None:
        if points is None:
            return None
        if component in _OPPORTUNITY_COMPONENTS:
            return (
                points
                * opportunity.share(component.value)
                * final.share(FinalInput.OPPORTUNITY_SCORE.value)
            )
        return points * final.share(component.value) if component.value in final.weights else None

    features: list[ScoreFeature] = []
    for feature in FEATURES:
        observation = observations[feature.id]
        formula = base[feature.component]
        weight = weights.get(feature.id, 0)
        points = (
            100 * (observation.value or 0.0) * weight / formula.known
            if observation.state is FeatureState.KNOWN and weight and formula.known
            else None
        )
        features.append(
            ScoreFeature(
                feature=feature.id,
                component=feature.component,
                problem=feature.problem,
                state=observation.state,
                value=observation.value,
                weight=weight,
                points=_round(points),
                impact=None
                if feature.component is _DATA
                else _round(impact_of(feature.component, points)),
                detail=observation.detail[:300],
                reason=observation.reason,
                evidence=list(observation.evidence),
            )
        )
    contact_total = fsum(c.weight for c in contact.components)
    for part in contact.components:
        points = (
            100 * part.value * part.weight / contact_total
            if contact_total and contact.score is not None
            else None
        )
        features.append(
            ScoreFeature(
                feature=f"contact.{part.code}",
                component=ScoreComponent.CONTACTABILITY,
                problem=None,
                state=FeatureState.KNOWN if points is not None else FeatureState.UNKNOWN,
                value=part.value if points is not None else None,
                weight=part.weight,
                points=_round(points),
                impact=_round(impact_of(ScoreComponent.CONTACTABILITY, points)),
                detail=f"{part.code}: {part.detail or 'none'}"[:300],
                reason=None if points is not None else "business_suppressed",
                evidence=[
                    ScoreEvidence(
                        kind=ScoreEvidenceKind.CONTACT_CHANNEL,
                        ref=c.key[:400],
                        label=f"{c.contact_type.value}: {c.validation_status.value}",
                        url=None,
                    )
                    for c in facts.channels
                ][:MAX_EVIDENCE]
                or [business_evidence(facts)],
            )
        )

    data = base[_DATA]
    components = [
        _component_view(
            ScoreComponent.ICP_MATCH,
            icp_formula.value,
            final,
            "100 * Σ(criterion * weight) / Σ weight over decided criteria that are not "
            "ignored (pass 1, fail 0); undecided criteria are left out",
            icp_formula.coverage(),
        ),
        _component_view(
            ScoreComponent.LATINO_CONFIDENCE,
            latino_value,
            final,
            "100 * confidence of the Latino assessment the business shows, when its status "
            "is not unknown; campaigns filter by the Latino requirement",
            1.0 if latino_value is not None else 0.0,
        ),
        *(
            _component_view(c, to_points[c], opportunity, _BASE_FORMULA, base[c].coverage())
            for c in (_HEALTH, _GAP, _CAPACITY)
        ),
        _component_view(
            ScoreComponent.CONTACTABILITY,
            None if contact.score is None else float(contact.score),
            final,
            contact.formula,
            1.0 if contact.score is not None else 0.0,
        ),
        _component_view(
            _INTENT, to_points[_INTENT], opportunity, _BASE_FORMULA, base[_INTENT].coverage()
        ),
        _component_view(_DATA, to_points[_DATA], None, _BASE_FORMULA, data.coverage()),
        _component_view(
            ScoreComponent.OPPORTUNITY_SCORE,
            opportunity.value,
            final,
            _formula_text("OPPORTUNITY_SCORE", scoring.opportunity),
            opportunity.coverage(),
        ),
        _component_view(
            ScoreComponent.FINAL_LEAD_SCORE,
            final.value,
            None,
            _formula_text("FINAL_LEAD_SCORE", scoring.final)
            + "; gates are decided first, and the tier follows the model's thresholds",
            final.coverage(),
        ),
    ]

    known_share = final.coverage()
    total = _round(final.value)
    if any(g.outcome is GateOutcome.FAIL for g in gates):
        tier, total = LeadTier.EXCLUDED, None
    elif (
        any(g.outcome is GateOutcome.UNKNOWN for g in gates)
        or total is None
        or known_share < scoring.min_known_weight
    ):
        tier = LeadTier.INSUFFICIENT_DATA
    elif total >= scoring.priority_threshold:
        tier = LeadTier.PRIORITY
    elif total >= scoring.review_threshold:
        tier = LeadTier.REVIEW
    else:
        tier = LeadTier.LOW
    data_value = to_points[_DATA]
    confidence = round((known_share if data_value is None else data_value / 100 * known_share), 4)

    reasons = _reasons(features, criteria_views, icp_formula, final)
    offer = _recommend(ctx, features)
    return LeadScorecard(
        business_id=facts.business_id,
        tier=tier,
        total_score=total,
        confidence=confidence,
        known_weight=known_share,
        versions=versions,
        components=components,
        features=features,
        gates=gates,
        criteria=criteria_views,
        reasons=reasons[:3],
        contact_now=[r for r in _feature_reasons(features) if r.feature.startswith("intent.")][:5],
        impacts=[
            InferredImpact(problem=f.problem, text=PROBLEM_PHRASES[f.problem][1], is_inference=True)
            for f in features
            if f.problem is not None and _shown_problem(f)
        ],
        risks=_risks(facts, features, gates),
        missing=_missing(features, criteria_views, icp_formula, opportunity, final, base),
        offer=offer,
        message=_message(facts, features, offer, ctx) if tier is not LeadTier.EXCLUDED else None,
    )


def _shown_problem(feature: ScoreFeature) -> bool:
    return feature.state is FeatureState.KNOWN and (feature.value or 0) >= 0.5


def _formula_text(name: str, weights: Sequence[OpportunityWeight | FinalWeight]) -> str:
    parts = " + ".join(f"{w.component.value} * {w.weight}" for w in weights if w.weight)
    return f"{name} = ({parts}) / Σ weight of the known inputs"


def _component_view(
    component: ScoreComponent,
    value: float | None,
    parent: _Formula | None,
    formula: str,
    coverage: float,
) -> ScoreComponentView:
    weight = int(parent.weights.get(component.value, 0)) if parent else 0
    contribution = parent.contributions.get(component.value) if parent else None
    return ScoreComponentView(
        component=component,
        value=_round(value),
        weight=weight,
        contribution=_round(contribution),
        coverage=coverage,
        formula=formula,
    )


def _icp_match(
    ctx: Context, observations: Mapping[str, Observation]
) -> tuple[list[CriterionView], _Formula]:
    views: list[CriterionView] = []
    weights: dict[str, float] = {}
    values: dict[str, float | None] = {}
    for name, criterion in _criteria_of(ctx.icp):
        importance, weight = criterion.importance, criterion.weight
        if importance is CriterionImportance.IGNORE:
            continue
        decision = _decide_criterion(name, ctx, observations)
        views.append(
            CriterionView(
                criterion=f"criterion.{name}",
                importance=importance,
                outcome=decision.outcome,
                weight=weight,
                detail=decision.detail[:300],
                evidence=list(decision.evidence),
            )
        )
        weights[name] = float(weight)
        values[name] = {GateOutcome.PASS: 100.0, GateOutcome.FAIL: 0.0}.get(decision.outcome)
    return views, _Formula(weights, values).solve()


def _feature_reasons(features: Sequence[ScoreFeature]) -> list[ScoreReason]:
    found = [
        ScoreReason(feature=f.feature, text=f.detail, impact=f.impact or 0.0, evidence=f.evidence)
        for f in features
        if f.state is FeatureState.KNOWN and (f.impact or 0) > 0 and (f.value or 0) >= 0.5
    ]
    return sorted(found, key=lambda r: (-r.impact, r.feature))


def _reasons(
    features: Sequence[ScoreFeature],
    criteria: Sequence[CriterionView],
    icp: _Formula,
    final: _Formula,
) -> list[ScoreReason]:
    share = final.share(FinalInput.ICP_MATCH.value)
    from_criteria = [
        ScoreReason(
            feature=view.criterion,
            text=view.detail,
            impact=round(100 * view.weight / icp.known * share, 4),
            evidence=view.evidence,
        )
        for view in criteria
        if view.outcome is GateOutcome.PASS and view.weight and icp.known and share
    ]
    return sorted(
        [*_feature_reasons(features), *from_criteria], key=lambda r: (-r.impact, r.feature)
    )


def _risks(
    facts: BusinessFacts, features: Sequence[ScoreFeature], gates: Sequence[GateView]
) -> list[ScoreRisk]:
    business = [business_evidence(facts)]
    risks: list[ScoreRisk] = []
    if facts.conflict_count:
        risks.append(
            ScoreRisk(
                code="records_disagree",
                evidence=business,
                detail=f"{facts.conflict_count} field(s) whose records disagree",
            )
        )
    if facts.latino is not None and facts.latino.contradictions:
        risks.append(
            ScoreRisk(
                code="latino_contradicted",
                detail="Latino evidence is contradicted",
                evidence=[
                    ScoreEvidence(
                        kind=ScoreEvidenceKind.LATINO_ASSESSMENT,
                        ref=facts.latino.assessment_id,
                        label=facts.latino.status.value,
                        url=None,
                    )
                ],
            )
        )
    by_id = {f.feature: f for f in features}
    if _shown_problem(by_id["gap.measurement"]):
        risks.append(
            ScoreRisk(
                code="measurement_uncertain",
                evidence=by_id["gap.measurement"].evidence,
                detail="Analytics not detected: tags inside a tag manager or consent "
                "tool are invisible",
            )
        )
    if facts.audit is not None and facts.audit.status is WebsiteAuditStatus.PARTIAL:
        risks.append(
            ScoreRisk(
                code="partial_audit",
                evidence=[audit_evidence(facts.audit)],
                detail="Some pages of the site could not be analyzed",
            )
        )
    freshness = by_id["data.freshness"]
    if freshness.value is not None and freshness.value <= 0.1:
        risks.append(
            ScoreRisk(code="stale_data", detail=freshness.detail, evidence=freshness.evidence)
        )
    if "no_usable_channel" in facts.contactability.gates:
        risks.append(
            ScoreRisk(
                code="no_usable_channel", evidence=business, detail="No usable published channel"
            )
        )
    risks += [
        ScoreRisk(code="gate_undecided", detail=f"{g.gate}: {g.detail}"[:300], evidence=g.evidence)
        for g in gates
        if g.outcome is GateOutcome.UNKNOWN
    ]
    return risks


def _missing(
    features: Sequence[ScoreFeature],
    criteria: Sequence[CriterionView],
    icp: _Formula,
    opportunity: _Formula,
    final: _Formula,
    base: Mapping[ScoreComponent, _Formula],
) -> list[MissingData]:
    """Unknown inputs and the share of the whole final formula (known or not) they stand for."""

    def of_final(key: str) -> float:
        return final.weights.get(key, 0.0) / final.total if final.total else 0.0

    missing: list[MissingData] = []
    for f in features:
        if (
            f.state is not FeatureState.UNKNOWN
            or not f.weight
            or f.component not in _OPPORTUNITY_COMPONENTS
        ):
            continue
        formula = base[f.component]
        share = (
            (f.weight / formula.total if formula.total else 0.0)
            * (
                opportunity.weights.get(f.component.value, 0.0) / opportunity.total
                if opportunity.total
                else 0.0
            )
            * of_final(FinalInput.OPPORTUNITY_SCORE.value)
        )
        if share:
            missing.append(
                MissingData(
                    feature=f.feature,
                    component=f.component,
                    detail=f.detail,
                    weight_share=round(share, 4),
                )
            )
    for view in criteria:
        if view.outcome is GateOutcome.UNKNOWN and view.weight and icp.total:
            share = view.weight / icp.total * of_final(FinalInput.ICP_MATCH.value)
            missing.append(
                MissingData(
                    feature=view.criterion,
                    component=ScoreComponent.ICP_MATCH,
                    detail=view.detail,
                    weight_share=round(share, 4),
                )
            )
    for key, component, detail in (
        (
            FinalInput.CONTACTABILITY.value,
            ScoreComponent.CONTACTABILITY,
            "Contactability has no score",
        ),
        (
            FinalInput.LATINO_CONFIDENCE.value,
            ScoreComponent.LATINO_CONFIDENCE,
            "No Latino status is known",
        ),
    ):
        if final.values.get(key) is None and of_final(key):
            missing.append(
                MissingData(
                    feature=key.lower(),
                    component=component,
                    detail=detail,
                    weight_share=round(of_final(key), 4),
                )
            )
    return sorted(missing, key=lambda m: (-m.weight_share, m.feature))


def _recommend(ctx: Context, features: Sequence[ScoreFeature]) -> OfferRecommendation | None:
    shown = {f.problem: f for f in features if f.problem is not None and _shown_problem(f)}
    best: tuple[float, int, Offer, list[ScoreFeature]] | None = None
    for index, offer in enumerate(ctx.offers):
        solved = [shown[p] for p in offer.solves if p in shown]
        if not solved:
            continue
        value = fsum((f.impact or 0.0) + (f.value or 0.0) * f.weight / 1000 for f in solved)
        if best is None or value > best[0]:
            best = (value, index, offer, solved)
    if best is None:
        return None
    _, _, offer, solved = best
    names = ", ".join(PROBLEM_PHRASES[f.problem][0] for f in solved if f.problem is not None)
    return OfferRecommendation(
        offer_key=offer.key,
        offer_name=offer.name,
        problems=[f.problem for f in solved if f.problem],
        why=f"It addresses what the evidence shows: {names}."[:600],
        evidence=[e for f in solved for e in f.evidence][:MAX_EVIDENCE],
    )


def _message(
    facts: BusinessFacts,
    features: Sequence[ScoreFeature],
    offer: OfferRecommendation | None,
    ctx: Context,
) -> MessageDraft | None:
    if offer is None:
        return None
    chosen = next(o for o in ctx.offers if o.key == offer.offer_key)
    site = facts.audit.site if facts.audit else (facts.domains[0] if facts.domains else None)
    lines = "\n".join(f"- {PROBLEM_PHRASES[p][0]}" for p in offer.problems)
    what = f"{chosen.name}: {chosen.description}" if chosen.description else chosen.name
    body = (
        f"Hello {facts.display_name} team,\n\n"
        f"While reviewing {site or 'your business listing'} we noticed that:\n{lines}\n\n"
        f"We help businesses like yours with {what}\n\n"
        "Would a short call to go over what we found be useful?\n\n"
        "(Draft for review: adapt it before sending. Lead Intelligence never sends messages.)"
    )
    return MessageDraft(
        subject=f"{chosen.name} for {facts.display_name}"[:200],
        body=body[:2000],
        language="en",
        is_draft=True,
    )


# ---- ranking and metrics -------------------------------------------------------------------------


def rank_key(
    tier: LeadTier, total: float | None, confidence: float, business_id: str
) -> tuple[int, int, float, float, str]:
    """Tier, then total (a missing total after), then confidence, then business id: stable, and
    equal to the SQL order of `scoring.store.RANK_ORDER`."""
    return (TIER_ORDER[tier], total is None, -(total or 0.0), -confidence, business_id)


def tier_counts(tiers: Iterable[LeadTier]) -> TierCounts:
    counts = {tier: 0 for tier in LeadTier}
    for tier in tiers:
        counts[tier] += 1
    return TierCounts.model_validate({tier.value: count for tier, count in counts.items()})


def distribution(totals: Iterable[float | None]) -> list[ScoreBin]:
    bins = [0] * 10
    for total in totals:
        if total is not None:
            bins[min(9, int(total // 10))] += 1
    return [ScoreBin(low=10 * index, businesses=count) for index, count in enumerate(bins)]


def label_of(stage: LeadStage | None) -> bool | None:
    """True for a lead that qualified or went further, False for a lost or invalid one, None when
    it has no outcome yet (not contacted, attempted, connected)."""
    if stage in POSITIVE_STAGES:
        return True
    if stage in NEGATIVE_STAGES:
        return False
    return None


def precision_at_k(labels: Sequence[bool | None], k: int) -> PrecisionAtK:
    """Among the labeled leads in the top k of the ranking, the share that were positive."""
    top = [label for label in labels[:k] if label is not None]
    positives = sum(top)
    return PrecisionAtK(
        k=k,
        labeled=len(top),
        positives=positives,
        precision=round(positives / len(top), 4) if top else None,
    )


def lift_by_decile(labels: Sequence[bool | None]) -> list[DecileLift]:
    """Ten groups of the ranking (sizes differ by at most one, larger first) and the positive rate
    of each against the overall rate of the labeled leads."""
    labeled = [label for label in labels if label is not None]
    overall = sum(labeled) / len(labeled) if labeled else None
    size, extra = divmod(len(labels), 10)
    out: list[DecileLift] = []
    start = 0
    for decile in range(10):
        end = start + size + (1 if decile < extra else 0)
        group = [label for label in labels[start:end] if label is not None]
        rate = sum(group) / len(group) if group else None
        out.append(
            DecileLift(
                decile=decile + 1,
                businesses=end - start,
                labeled=len(group),
                positives=sum(group),
                rate=None if rate is None else round(rate, 4),
                lift=None if rate is None or not overall else round(rate / overall, 4),
            )
        )
        start = end
    return out


def calibration_bands(scores: Sequence[tuple[float | None, bool | None]]) -> list[CalibrationBand]:
    bands: list[CalibrationBand] = []
    for low in range(0, 100, 20):
        high = low + 20
        inside = [
            (t, lab)
            for t, lab in scores
            if t is not None and (low <= t < high or (high == 100 and t == 100))
        ]
        labeled = [lab for _, lab in inside if lab is not None]
        bands.append(
            CalibrationBand(
                low=low,
                high=high,
                businesses=len(inside),
                labeled=len(labeled),
                mean_score=round(fsum(t for t, _ in inside if t is not None) / len(inside), 4)
                if inside
                else None,
                observed_rate=round(sum(labeled) / len(labeled), 4) if labeled else None,
            )
        )
    return bands


def tier_cohorts(rows: Sequence[tuple[LeadTier, bool | None]]) -> list[TierCohort]:
    cohorts: list[TierCohort] = []
    for tier in LeadTier:
        labels = [label for found, label in rows if found is tier]
        labeled = [label for label in labels if label is not None]
        cohorts.append(
            TierCohort(
                tier=tier,
                businesses=len(labels),
                labeled=len(labeled),
                positives=sum(labeled),
                conversion=round(sum(labeled) / len(labeled), 4) if labeled else None,
            )
        )
    return cohorts
