"""Sales briefs (phase 12): what an analyst needs to open a conversation, restated from stored data.

Every claim of a brief cites the rows it rests on (at least one); an inference (a probable impact, a
recommended offer, a conversation hook) is marked as one. Nothing is invented: a section without
data says why it is empty. A local model may reword a brief (`sales_brief`, phase 10 checks), citing
the brief's claims only; a reworded sentence is kept only while every claim it cites is still in the
brief, so a model can neither add a fact nor keep one the data no longer supports.
"""

import sqlite3
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from typing import Final

import orjson

from research_engine.contacts import store as contacts_store
from research_engine.domain import (
    STALE_AFTER_DAYS,
    AiInputKind,
    AiReviewStatus,
    BriefClaim,
    BriefDraft,
    BriefDraftSection,
    BriefDraftSentence,
    BriefRef,
    BriefSection,
    BriefSectionView,
    ContactChannel,
    ContactType,
    DecisionMakerReview,
    EvidencePin,
    EvidenceRefKind,
    FeatureState,
    GateOutcome,
    ProblemType,
    ResearchStatus,
    SalesBrief,
    SalesBriefOutput,
    ScoreFeature,
    ValidationStatus,
)
from research_engine.leads import common

MAX_CLAIMS: Final = 6
MAX_EVIDENCE: Final = 8
#: Neutral openings for an observed problem: a question or an offer to look, never a verdict.
HOOKS: Final[dict[ProblemType, str]] = {
    ProblemType.NO_WEBSITE: "Ask how new customers find them today: no source lists a website.",
    ProblemType.WEBSITE_UNREACHABLE: (
        "Offer to look at why the website did not load when it was checked."
    ),
    ProblemType.WEAK_CONVERSION_PATH: (
        "Ask how website visitors get in touch: no clear call to action or form was found."
    ),
    ProblemType.NO_ONLINE_BOOKING: "Ask whether customers can book or request a quote online.",
    ProblemType.WEAK_LOCAL_SEO: "Ask how the business shows up in local search and maps.",
    ProblemType.WEAK_TECHNICAL_SEO: "Offer a short review of the website's search basics.",
    ProblemType.POOR_MOBILE_EXPERIENCE: "Ask how the website works for customers on a phone.",
    ProblemType.MEASUREMENT_NOT_DETECTED: (
        "Ask how they measure which channels bring customers; analytics may be hidden."
    ),
}
_CHANNEL_LABEL: Final = {
    ContactType.PHONE: "Phone",
    ContactType.EMAIL: "Email",
    ContactType.CONTACT_FORM: "Contact form",
    ContactType.BOOKING_FORM: "Booking form",
    ContactType.SOCIAL_PROFILE: "Social profile",
}
_STATUS_RANK: Final = {
    ValidationStatus.LIKELY_VALID: 0,
    ValidationStatus.DOMAIN_VALID: 1,
    ValidationStatus.STRUCTURALLY_VALID: 2,
    ValidationStatus.OBSERVED: 3,
    ValidationStatus.UNKNOWN: 4,
}


class _Claims:
    """The brief's claims, numbered B1, B2... in section order."""

    def __init__(self) -> None:
        self.sections: dict[BriefSection, list[BriefClaim]] = {s: [] for s in BriefSection}
        self.reasons: dict[BriefSection, str] = {}
        self.count = 0

    def add(
        self,
        section: BriefSection,
        text: str,
        evidence: Iterable[BriefRef],
        *,
        inference: bool = False,
    ) -> None:
        refs = list({(ref.kind, ref.ref): ref for ref in evidence}.values())[:MAX_EVIDENCE]
        if not refs or len(self.sections[section]) >= MAX_CLAIMS:
            return
        self.count += 1
        self.sections[section].append(
            BriefClaim(ref=f"B{self.count}", text=text[:400], is_inference=inference, evidence=refs)
        )

    def empty_when(self, section: BriefSection, reason: str) -> None:
        self.reasons.setdefault(section, reason)

    def views(self) -> list[BriefSectionView]:
        return [
            BriefSectionView(
                section=section,
                claims=self.sections[section],
                empty_reason=None
                if self.sections[section]
                else self.reasons.get(section, "no_data"),
            )
            for section in BriefSection
        ]


def claim_text(section: BriefSection, claim: BriefClaim) -> str:
    """How a claim is shown to a model, and matched back when its answer is read."""
    return f"{section.value}: {claim.text}{' (inference)' if claim.is_inference else ''}"


def ai_pieces(brief: SalesBrief) -> list[tuple[AiInputKind, str | None, str | None, str]]:
    """The claims of a brief as the inputs of a `sales_brief` task (`ai.tasks.bounded_refs`)."""
    return [
        (AiInputKind.BRIEF_CLAIM, None, view.section.value, claim_text(view.section, claim))
        for view in brief.sections
        for claim in view.claims
    ]


def _channel_ref(channel: ContactChannel) -> BriefRef:
    return BriefRef(
        kind=EvidenceRefKind.CONTACT_CHANNEL,
        ref=channel.key,
        label=f"{channel.contact_type.value}: {channel.value}"[:300],
        url=None,
    )


def _shown(feature: ScoreFeature) -> bool:
    return (
        feature.problem is not None
        and feature.state is FeatureState.KNOWN
        and (feature.value or 0.0) >= 0.5
    )


def _day(value: datetime) -> str:
    return value.date().isoformat()


def build(
    conn: sqlite3.Connection, business_id: str, score_run_id: str | None, now: datetime
) -> SalesBrief:
    business = common.business_of(conn, business_id)
    scored = common.latest_score(conn, business.id, score_run_id)
    contacts = contacts_store.get_contact_detail(conn, business.id, now)
    research = conn.execute(
        "SELECT status FROM lead_research WHERE business_id = ?", (business.id,)
    ).fetchone()
    pins = [
        EvidencePin.model_validate(dict(row))
        for row in conn.execute(
            "SELECT * FROM evidence_pins WHERE business_id = ? AND unpinned_at IS NULL "
            "ORDER BY pinned_at, id",
            (business.id,),
        )
    ]
    me = common.business_ref(business)
    claims = _Claims()
    facts, card = (scored.facts, scored.card) if scored else (None, None)
    audit_ref = (
        BriefRef(
            kind=EvidenceRefKind.WEBSITE_AUDIT, ref=facts.audit.id, label=facts.audit.site, url=None
        )
        if facts and facts.audit
        else None
    )
    dates: list[datetime] = []

    # business snapshot
    if facts:
        place = facts.locations[0] if facts.locations else None
        where = ", ".join(filter(None, (place.locality, place.region))) if place else ""
        what = f" as {facts.categories[0]}" if facts.categories else ""
        claims.add(
            BriefSection.BUSINESS_SNAPSHOT,
            f"{business.display_name} is listed{what}{f' in {where}' if where else ''}.",
            [me],
        )
        claims.add(
            BriefSection.BUSINESS_SNAPSHOT,
            f"Listed by {facts.source_count} acquired record(s), "
            f"last on {_day(facts.last_seen_at)}.",
            [me],
        )
        if facts.domains:
            claims.add(
                BriefSection.BUSINESS_SNAPSHOT, f"Website: {facts.domains[0]}.", [audit_ref or me]
            )
    else:
        claims.add(BriefSection.BUSINESS_SNAPSHOT, f"{business.display_name}.", [me])
        for section in (BriefSection.ICP_MATCH, BriefSection.VERIFIED_OPPORTUNITY):
            claims.empty_when(section, "not_scored")

    observed = sorted(
        (feature for feature in (card.features if card else []) if _shown(feature)),
        key=lambda feature: (-(feature.impact or 0.0), feature.feature),
    )
    if card:
        # why it matches the ICP
        for view in card.criteria:
            if view.outcome is GateOutcome.PASS and view.evidence:
                name = view.criterion.split(".")[-1].replace("_", " ").capitalize()
                claims.add(
                    BriefSection.ICP_MATCH,
                    f"{name}: {view.detail}.",
                    [common.ref_of(evidence) for evidence in view.evidence],
                )
        claims.empty_when(BriefSection.ICP_MATCH, "no_icp_criterion_passed")
        # verified opportunity: observed problems, each with its probable impact
        for feature in observed:
            evidence = [common.ref_of(item) for item in feature.evidence]
            claims.add(BriefSection.VERIFIED_OPPORTUNITY, f"{feature.detail}.", evidence)
            impact = next((i.text for i in card.impacts if i.problem is feature.problem), None)
            if impact:
                claims.add(BriefSection.VERIFIED_OPPORTUNITY, impact, evidence, inference=True)
        claims.empty_when(BriefSection.VERIFIED_OPPORTUNITY, "no_observed_problem")

    # evidence: what the analyst pinned, then the score's strongest reasons
    for pin in pins:
        note = f" ({pin.note})" if pin.note else ""
        claims.add(
            BriefSection.EVIDENCE,
            f"Pinned: {pin.label}{note}",
            [BriefRef(kind=pin.ref_kind, ref=pin.ref, label=pin.label, url=None)],
        )
    for reason in card.reasons if card else []:
        claims.add(
            BriefSection.EVIDENCE, reason.text, [common.ref_of(item) for item in reason.evidence]
        )
    claims.empty_when(BriefSection.EVIDENCE, "no_evidence")

    # recommended offer
    if card and card.offer:
        claims.add(
            BriefSection.RECOMMENDED_OFFER,
            f"{card.offer.offer_name}: {card.offer.why}",
            [common.ref_of(item) for item in card.offer.evidence],
            inference=True,
        )
    claims.empty_when(BriefSection.RECOMMENDED_OFFER, "no_offer_recommended")

    # contact route: never one around the do-not-contact list
    if contacts.business_suppression is not None:
        claims.add(
            BriefSection.CONTACT_ROUTE,
            "The business is on the do-not-contact list: it must not be contacted.",
            [me],
        )
    else:
        usable = sorted(
            common.routes(contacts),
            key=lambda c: (not c.is_primary, _STATUS_RANK.get(c.validation_status, 9), c.key),
        )
        for channel in usable[:3]:
            status = channel.validation_status.value.replace("_", " ")
            claims.add(
                BriefSection.CONTACT_ROUTE,
                f"{_CHANNEL_LABEL[channel.contact_type]}: {channel.value} ({status}, "
                f"{channel.freshness.value}).",
                [_channel_ref(channel)],
            )
        claims.empty_when(BriefSection.CONTACT_ROUTE, "no_usable_contact")

    # decision maker, only when the business published one
    for person in contacts.decision_makers:
        maker = person.decision_maker
        if maker.review_status is DecisionMakerReview.REJECTED:
            continue
        role = f", {maker.role_raw}" if maker.role_raw else ""
        claims.add(
            BriefSection.DECISION_MAKER,
            f"{maker.full_name_raw}{role} (published by the business).",
            [
                BriefRef(
                    kind=EvidenceRefKind.DECISION_MAKER,
                    ref=maker.id,
                    label=maker.full_name_raw[:300],
                    url=None,
                )
            ],
        )
    claims.empty_when(BriefSection.DECISION_MAKER, "no_decision_maker_published")

    # conversation hooks: neutral questions about what the evidence shows
    for feature in observed:
        if feature.problem is not None:
            claims.add(
                BriefSection.CONVERSATION_HOOKS,
                HOOKS[feature.problem],
                [common.ref_of(item) for item in feature.evidence],
                inference=True,
            )
    for reason in card.contact_now if card else []:
        claims.add(
            BriefSection.CONVERSATION_HOOKS,
            f"Recent signal: {reason.text}",
            [common.ref_of(item) for item in reason.evidence],
        )
    claims.empty_when(BriefSection.CONVERSATION_HOOKS, "no_hook")

    # risks and unknowns
    if card:
        for risk in card.risks:
            claims.add(
                BriefSection.RISKS_UNKNOWNS,
                f"{risk.detail}.",
                [common.ref_of(item) for item in risk.evidence] or [me],
            )
        for missing in card.missing[:3]:
            claims.add(BriefSection.RISKS_UNKNOWNS, f"Unknown: {missing.detail}.", [me])
    else:
        claims.add(
            BriefSection.RISKS_UNKNOWNS, "No finished score run has scored this business.", [me]
        )
    claims.empty_when(BriefSection.RISKS_UNKNOWNS, "no_known_risk")

    # last verified
    if scored and facts and card:
        run_ref = BriefRef(
            kind=EvidenceRefKind.SCORE_RUN,
            ref=scored.run.id,
            label=f"{card.versions.icp_profile_name} v{card.versions.icp_profile_version} · "
            f"{card.versions.scoring_model_key} v{card.versions.scoring_model_version}"[:300],
            url=None,
        )
        age = now - facts.collected_at
        claims.add(
            BriefSection.LAST_VERIFIED, f"Facts read on {_day(facts.collected_at)}.", [run_ref]
        )
        if age > timedelta(days=STALE_AFTER_DAYS):
            claims.add(
                BriefSection.RISKS_UNKNOWNS,
                f"The facts were read {age.days} days ago: score the business again.",
                [run_ref],
            )
        dates.append(facts.collected_at)
        if facts.audit and audit_ref:
            claims.add(
                BriefSection.LAST_VERIFIED,
                f"Website audited on {_day(facts.audit.created_at)}.",
                [audit_ref],
            )
            dates.append(facts.audit.created_at)
    checked = [channel for channel in contacts.channels if channel.validated_at is not None]
    if checked:
        newest = max(checked, key=lambda channel: (channel.validated_at, channel.key))
        if newest.validated_at is not None:
            claims.add(
                BriefSection.LAST_VERIFIED,
                f"Contact channels checked on {_day(newest.validated_at)}.",
                [_channel_ref(newest)],
            )
            dates.append(newest.validated_at)
    claims.empty_when(BriefSection.LAST_VERIFIED, "never_verified")

    sections = claims.views()
    return SalesBrief(
        business_id=business.id,
        project_id=business.project_id,
        display_name=business.display_name,
        score_run_id=scored.run.id if scored else None,
        versions=card.versions if card else None,
        generated_at=now,
        last_verified_at=max(dates) if dates else None,
        suppressed=contacts.business_suppression is not None,
        research_status=ResearchStatus(research["status"])
        if research and research["status"]
        else None,
        is_synthetic=common.is_synthetic(conn, business.project_id),
        sections=sections,
        draft=draft_of(conn, business.id, sections),
    )


def draft_of(
    conn: sqlite3.Connection, root: str, sections: Sequence[BriefSectionView]
) -> BriefDraft | None:
    """The newest valid, not rejected model wording of the brief, reduced to the sentences whose
    every cited claim is still in it."""
    row = conn.execute(
        "SELECT r.id, r.model, r.review_status, r.finished_at, r.input_json, "
        "o.validated_output_json FROM ai_inference_runs r "
        "JOIN ai_inference_outputs o ON o.inference_run_id = r.id "
        "AND o.validated_output_json IS NOT NULL "
        "WHERE r.business_id = ? AND r.task_type = 'sales_brief' AND r.status = 'succeeded' "
        "AND r.review_status <> 'rejected' "
        "ORDER BY r.finished_at DESC, r.id DESC, o.attempt DESC LIMIT 1",
        (root,),
    ).fetchone()
    if row is None:
        return None
    refs = orjson.loads(row["input_json"]).get("refs") or []
    given: dict[str, tuple[str, str]] = {
        str(ref.get("ref")): (str(ref.get("source")), str(ref.get("text")))
        for ref in refs
        if isinstance(ref, dict)
    }
    current = {
        (view.section.value, claim_text(view.section, claim)): claim
        for view in sections
        for claim in view.claims
    }
    output = SalesBriefOutput.model_validate(orjson.loads(row["validated_output_json"]))
    kept: list[BriefDraftSection] = []
    dropped = 0
    for section in output.sections:
        sentences: list[BriefDraftSentence] = []
        for point in section.sentences:
            cited = [current.get(given.get(ref, ("", ""))) for ref in point.evidence_ids]
            found = [claim for claim in cited if claim is not None]
            if len(found) != len(cited):
                dropped += 1
                continue
            evidence = {(e.kind, e.ref): e for claim in found for e in claim.evidence}
            sentences.append(
                BriefDraftSentence(
                    text=point.text,
                    claims=[claim.ref for claim in found],
                    evidence=list(evidence.values()),
                )
            )
        if sentences:
            kept.append(BriefDraftSection(section=section.section, sentences=sentences))
    return BriefDraft(
        inference_run_id=row["id"],
        model=row["model"],
        review_status=AiReviewStatus(row["review_status"]),
        finished_at=row["finished_at"],
        sections=kept,
        dropped=dropped,
    )
