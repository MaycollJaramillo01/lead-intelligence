"""What the lead workspace modules share: the canonical business behind an id, the records resolved
into it, and the newest finished score of it."""

import sqlite3
from dataclasses import dataclass

import orjson

from research_engine import repository
from research_engine.contacts.rules import STATUS_QUALITY
from research_engine.domain import (
    BriefRef,
    Business,
    BusinessFacts,
    ContactChannel,
    ContactDetail,
    EvidenceRefKind,
    LeadScorecard,
    Project,
    ScoreEvidence,
    ScoreRun,
    ValidationStatus,
)


@dataclass(frozen=True, slots=True)
class Scored:
    run: ScoreRun
    facts: BusinessFacts
    card: LeadScorecard


def business_of(conn: sqlite3.Connection, business_id: str) -> Business:
    """The canonical business an id belongs to: a merged record answers with its root."""
    row = conn.execute(
        "SELECT coalesce(merged_into_id, id) AS root FROM businesses "
        "WHERE id = ? AND deleted_at IS NULL",
        (business_id,),
    ).fetchone()
    if row is None:
        raise repository.NotFoundError(f"business {business_id} not found")
    business = repository.get(conn, Business, row["root"])
    if business.deleted_at is not None:
        raise repository.NotFoundError(f"business {business.id} has been deleted")
    return business


def members_of(conn: sqlite3.Connection, root: str) -> list[str]:
    """The business and every live record merged into it."""
    return [
        root,
        *(
            row["id"]
            for row in conn.execute(
                "SELECT id FROM businesses WHERE merged_into_id = ? AND deleted_at IS NULL "
                "ORDER BY id",
                (root,),
            )
        ),
    ]


def is_synthetic(conn: sqlite3.Connection, project_id: str) -> bool:
    return repository.get(conn, Project, project_id).is_synthetic


def latest_score(
    conn: sqlite3.Connection, root: str, score_run_id: str | None = None
) -> Scored | None:
    """The score of `root` in that run, or in the newest finished run that scored it."""
    if score_run_id is None:
        row = conn.execute(
            "SELECT s.score_run_id, s.facts_json, s.scorecard_json FROM business_scores s "
            "JOIN score_runs r ON r.id = s.score_run_id "
            "WHERE s.business_id = ? AND r.finished_at IS NOT NULL "
            "ORDER BY r.finished_at DESC, r.id DESC LIMIT 1",
            (root,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT score_run_id, facts_json, scorecard_json FROM business_scores "
            "WHERE business_id = ? AND score_run_id = ?",
            (root, score_run_id),
        ).fetchone()
        if row is None:
            raise repository.NotFoundError(f"score run {score_run_id} did not score {root}")
    if row is None:
        return None
    return Scored(
        run=repository.get(conn, ScoreRun, row["score_run_id"]),
        facts=BusinessFacts.model_validate(orjson.loads(row["facts_json"])),
        card=LeadScorecard.model_validate(orjson.loads(row["scorecard_json"])),
    )


def routes(detail: ContactDetail) -> list[ContactChannel]:
    """The channels an analyst may try: the ones phase 09 counts as reachable (a known quality, not
    a fictional number: `contacts.rules.contactability`), neither stale nor on the do-not-contact
    list."""
    if detail.business_suppression is not None:
        return []
    return [
        channel
        for channel in detail.channels
        if channel.validation_status in STATUS_QUALITY
        and channel.validation_status is not ValidationStatus.STALE
        and channel.suppression is None
        and not any(signal.code == "fictional_range" for signal in channel.signals)
    ]


def ref_of(evidence: ScoreEvidence) -> BriefRef:
    return BriefRef(
        kind=EvidenceRefKind(evidence.kind.value),
        ref=evidence.ref,
        label=evidence.label,
        url=evidence.url,
    )


def business_ref(business: Business) -> BriefRef:
    return BriefRef(
        kind=EvidenceRefKind.BUSINESS,
        ref=business.id,
        label=business.display_name[:300],
        url=None,
    )
