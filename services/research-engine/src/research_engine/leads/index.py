"""The Lead Explorer's projection of score runs (phase 12).

`lead_index` holds one row per business per score run: the columns the explorer filters and sorts
on, projected from the facts and scorecard the run stored. A run writes its rows with its scores and
ranks them when it finishes; a run scored before phase 12 is projected the first time it is read.
A projection only: the scorecard stays the record, and a deleted business's rows are dropped by
trigger (and projected again if it is restored).
"""

import sqlite3
from typing import Final

import orjson

from research_engine import repository
from research_engine.domain import (
    BusinessFacts,
    ContactType,
    FeatureState,
    LatinoStatus,
    LeadIndexRow,
    LeadScorecard,
    ProblemType,
    ScoreComponent,
    ScoreRun,
    ValidationStatus,
    sort_key,
)
from research_engine.scoring.model import TIER_ORDER

BATCH: Final = 500
#: One bit per problem type, in `ProblemType` order (`lead_index.problem_mask`).
PROBLEM_BITS: Final = {problem: 1 << index for index, problem in enumerate(ProblemType)}
#: The channel a lead shows first: the most validated one, then the most direct kind. Invalid,
#: suppressed and stale channels are no route (`leads.common.routes`).
_STATUS_RANK: Final = {
    ValidationStatus.LIKELY_VALID: 0,
    ValidationStatus.DOMAIN_VALID: 1,
    ValidationStatus.STRUCTURALLY_VALID: 2,
    ValidationStatus.OBSERVED: 3,
    ValidationStatus.UNKNOWN: 4,
}
_TYPE_RANK: Final = {kind: index for index, kind in enumerate(ContactType)}
#: The order of a finished run, equal to `scoring.model.rank_key` and `scoring.store.RANK_ORDER`.
_RANK_SQL: Final = (
    "UPDATE lead_index SET rank = ranked.position FROM (SELECT business_id, ROW_NUMBER() OVER ("
    "ORDER BY tier_order, total_score IS NULL, total_score DESC, confidence DESC, business_id"
    ") AS position FROM lead_index WHERE score_run_id = ?) AS ranked "
    "WHERE lead_index.score_run_id = ? AND lead_index.business_id = ranked.business_id"
)
# ponytail: a process-wide memo of runs already projected and ranked; a business restored after it
# was memoised reappears when the worker restarts.
_complete: set[str] = set()


def problems_of(mask: int) -> list[ProblemType]:
    return [problem for problem, bit in PROBLEM_BITS.items() if mask & bit]


def project(run: ScoreRun, facts: BusinessFacts, card: LeadScorecard) -> LeadIndexRow:
    """The index row of one stored score. Deterministic: the same score gives the same row."""
    values = {view.component: view.value for view in card.components}
    # phase 09 counts the reachable channels (a fictional number is not one)
    # ponytail: facts keep no fictional flag per channel, so next to a counted channel a fictional
    # one may still be shown first; add the flag to FactChannel if real data ever needs it
    counted = facts.contactability.channels_counted
    usable = [c for c in facts.channels if c.validation_status in _STATUS_RANK] if counted else []
    best = min(
        usable,
        key=lambda c: (_STATUS_RANK.get(c.validation_status, 9), _TYPE_RANK[c.contact_type], c.key),
        default=None,
    )
    observed = [
        (feature.problem, feature)
        for feature in card.features
        if feature.problem is not None
        and feature.state is FeatureState.KNOWN
        and (feature.value or 0.0) >= 0.5
    ]
    mask = 0
    for problem, _ in observed:
        mask |= PROBLEM_BITS[problem]
    top = max(observed, key=lambda pair: (pair[1].impact or 0.0, pair[1].feature), default=None)
    place = facts.locations[0] if facts.locations else None
    latino = facts.latino
    return LeadIndexRow(
        score_run_id=run.id,
        business_id=facts.business_id,
        project_id=facts.project_id,
        rank=None,
        tier=card.tier,
        tier_order=TIER_ORDER[card.tier],
        total_score=card.total_score,
        confidence=card.confidence,
        data_coverage=card.known_weight,
        icp_match=values.get(ScoreComponent.ICP_MATCH),
        opportunity_score=values.get(ScoreComponent.OPPORTUNITY_SCORE),
        digital_gap=values.get(ScoreComponent.DIGITAL_GAP),
        business_health=values.get(ScoreComponent.BUSINESS_HEALTH),
        buying_capacity=values.get(ScoreComponent.BUYING_CAPACITY),
        contactability=values.get(ScoreComponent.CONTACTABILITY),
        commercial_intent=values.get(ScoreComponent.COMMERCIAL_INTENT),
        data_confidence=values.get(ScoreComponent.DATA_CONFIDENCE),
        display_name=facts.display_name[:300] or facts.business_id,
        name_sort_key=sort_key(facts.display_name),
        locality=place.locality if place else None,
        region=place.region if place else None,
        primary_category=facts.categories[0] if facts.categories else None,
        primary_domain=facts.domains[0] if facts.domains else None,
        latino_status=latino.status if latino else LatinoStatus.UNKNOWN,
        latino_confidence=(
            latino.confidence if latino and latino.status is not LatinoStatus.UNKNOWN else None
        ),
        main_channel=best.contact_type if best else None,
        channel_status=best.validation_status if best else None,
        usable_channels=counted,
        problem_mask=mask,
        top_problem=top[0].value if top else None,
        top_reason=card.reasons[0].text[:300] if card.reasons else None,
        offer_key=card.offer.offer_key if card.offer else None,
        facts_collected_at=facts.collected_at,
        audit_status=facts.audit.status if facts.audit else None,
        conflicts=facts.conflict_count,
        suppressed_when_scored=facts.business_suppressed,
        is_synthetic=facts.is_synthetic,
    )


def rank_run(conn: sqlite3.Connection, score_run_id: str) -> None:
    """Writes each business's place in the run. Call inside the transaction that finishes it."""
    conn.execute(_RANK_SQL, (score_run_id, score_run_id))


def ensure_indexed(conn: sqlite3.Connection, run: ScoreRun) -> None:
    """Projects the stored scores of `run` the index lacks, then ranks the run once it finished."""
    if run.id in _complete:
        return
    (scored,) = conn.execute(
        "SELECT count(*) FROM business_scores WHERE score_run_id = ?", (run.id,)
    ).fetchone()
    indexed, unranked = conn.execute(
        "SELECT count(*), count(*) - count(rank) FROM lead_index WHERE score_run_id = ?",
        (run.id,),
    ).fetchone()
    while indexed < scored:
        rows = conn.execute(
            "SELECT s.facts_json, s.scorecard_json FROM business_scores s "
            "JOIN businesses b ON b.id = s.business_id "
            "WHERE s.score_run_id = ? AND b.deleted_at IS NULL AND NOT EXISTS ("
            "SELECT 1 FROM lead_index li WHERE li.score_run_id = s.score_run_id "
            "AND li.business_id = s.business_id) LIMIT ?",
            (run.id, BATCH),
        ).fetchall()
        if not rows:
            break  # the rest belong to deleted businesses
        with repository.atomic(conn):
            repository.insert(
                conn,
                *(
                    project(
                        run,
                        BusinessFacts.model_validate(orjson.loads(row["facts_json"])),
                        LeadScorecard.model_validate(orjson.loads(row["scorecard_json"])),
                    )
                    for row in rows
                ),
            )
        indexed += len(rows)
        unranked += len(rows)
    if run.finished_at is None:
        return
    if unranked:
        with repository.atomic(conn):
            rank_run(conn, run.id)
    _complete.add(run.id)
