"""The research workspace of a lead (phase 12): where an analyst checks, completes and decides.

Everything here is a person's decision over stored data, and all of it is history:

- a research status (`sales_ready`, `needs_research`, `disqualified`) always carries a reason and is
  also a `review_decisions` row, so the last change can be undone; `sales_ready` is refused unless
  every `SalesReadyCriterion` holds (docs/lead-explorer.md);
- checklists say what to verify per campaign or vertical: automatic items read the stored facts,
  manual items are ticked with immutable `research_checks` rows;
- tasks track what is left, pins mark evidence, notes are soft-deleted with a reason;
- a correction never edits a source: it adds an observation from the analyst (connector
  `analyst.corrections`, the reason in its raw record) and makes it the value the business shows;
- a refresh opens a task and starts the engines' jobs; a bulk action applies one change to many
  leads, each one audited as its own decision.
"""

import hashlib
import sqlite3
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

import orjson

from research_engine import repository
from research_engine.contacts import store as contacts_store
from research_engine.domain import (
    BULK_MAX_MATCHING,
    CHECKLIST_SCHEMA_VERSION,
    STALE_AFTER_DAYS,
    AcquisitionMethod,
    AuditAction,
    BriefRef,
    BulkAction,
    BulkMode,
    BulkRequest,
    BulkResult,
    BulkSkip,
    Business,
    BusinessClassification,
    BusinessLocation,
    BusinessName,
    BusinessStatus,
    CanonicalChoiceRequest,
    CanonicalField,
    ChecklistCatalog,
    ChecklistCatalogQuery,
    ChecklistChangeRequest,
    ChecklistDefinition,
    ChecklistItem,
    ChecklistItemKind,
    ChecklistItemState,
    ChecklistRule,
    ChecklistSaveRequest,
    ChecklistState,
    ChecklistView,
    ClassificationKind,
    ContactDetail,
    ContactOrigin,
    ContactPoint,
    ContactType,
    CorrectionView,
    CriterionCheck,
    Cursor,
    DecisionMakerReview,
    EntityType,
    EvidenceDetail,
    EvidencePin,
    EvidenceQuery,
    EvidenceRefKind,
    Extraction,
    LeadResearch,
    LeadTier,
    NameKind,
    Note,
    PinView,
    Provenance,
    RawRecord,
    RefreshScope,
    ResearchAction,
    ResearchActionRequest,
    ResearchCheck,
    ResearchChecklist,
    ResearchChecklistVersion,
    ResearchStatus,
    ResearchTask,
    ResearchTaskKind,
    ResearchTaskStatus,
    ResearchWorkspace,
    ReviewDecision,
    SalesReadyCriterion,
    ScoringChangeAction,
    SourceConnector,
    SourceKind,
    SourceRun,
    SourceRunStatus,
    Tag,
    TimelineEntry,
    TimelineKind,
    TimelinePage,
    TimelineQuery,
    ValidationStatus,
    Website,
    format_ts,
    new_id,
    utc_now,
)
from research_engine.leads import LeadError, common, explorer
from research_engine.resolution import normalize
from research_engine.resolution import store as resolution_store
from research_engine.scoring.model import matches_term

CORRECTION_CONNECTOR: Final = "analyst.corrections"
CORRECTION_EXTRACTOR: Final = "analyst.correction"
CORRECTION_VERSION: Final = "1.0.0"
DEFAULT_CHECKLIST: Final = "Sales-ready checklist"
ACTOR_SYSTEM: Final = "lead-research"
MAX_LISTED: Final = 100
MAX_DECISIONS: Final = 50
MAX_SYNCED_MISSING: Final = 10
MAX_SKIPS: Final = 100
MAX_REFRESH_URLS: Final = 5
BATCH: Final = 500
STATUS_DECISION: Final = "research_status"
ASSIGNEE_DECISION: Final = "research_assignee"
RESEARCH_DECISIONS: Final = (STATUS_DECISION, ASSIGNEE_DECISION, "choose_canonical_value")
_QUALIFYING: Final = (LeadTier.PRIORITY, LeadTier.REVIEW)
_CRITERION_RULES: Final = {
    SalesReadyCriterion.SCORED_RECENTLY: ChecklistRule.SCORED_RECENTLY,
    SalesReadyCriterion.QUALIFYING_TIER: ChecklistRule.QUALIFYING_TIER,
    SalesReadyCriterion.VERIFIED_OPPORTUNITY: ChecklistRule.VERIFIED_OPPORTUNITY,
    SalesReadyCriterion.USABLE_CONTACT: ChecklistRule.USABLE_CONTACT,
    SalesReadyCriterion.CONTACT_CONFLICTS_RESOLVED: ChecklistRule.CONTACT_CONFLICTS_RESOLVED,
}


def _marks(items: Sequence[object]) -> str:
    return ", ".join("?" * len(items))


def _chunks[T](items: Sequence[T], size: int = BATCH) -> Iterator[Sequence[T]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _text(value: str) -> str:
    return value.replace("_", " ")


# ---- the facts a workspace reads ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Lead:
    business: Business
    members: list[str]
    scored: common.Scored | None
    contacts: ContactDetail
    now: datetime


def _lead(conn: sqlite3.Connection, business_id: str, now: datetime) -> _Lead:
    business = common.business_of(conn, business_id)
    return _Lead(
        business=business,
        members=common.members_of(conn, business.id),
        scored=common.latest_score(conn, business.id),
        contacts=contacts_store.get_contact_detail(conn, business.id, now),
        now=now,
    )


def _open_contact_conflicts(conn: sqlite3.Connection, lead: _Lead) -> list[str]:
    """Phone or email values the records disagree on that no person chose between."""
    disputed = {
        conflict.contact_type.value
        for conflict in lead.contacts.conflicts
        if conflict.code == "values_disagree"
        and conflict.contact_type in (ContactType.PHONE, ContactType.EMAIL)
    }
    chosen = {
        row["field"]
        for row in conn.execute(
            "SELECT field FROM canonical_values WHERE business_id = ? AND superseded_at IS NULL "
            "AND method = 'manual'",
            (lead.business.id,),
        )
    }
    return sorted(disputed - chosen)


def _rule(conn: sqlite3.Connection, lead: _Lead, rule: ChecklistRule) -> tuple[bool, str]:
    """Whether an automatic item holds for the lead, and why."""
    scored = lead.scored
    if rule is ChecklistRule.SCORED_RECENTLY:
        if scored is None:
            return False, "Not scored by a finished run yet"
        age = lead.now - scored.facts.collected_at
        if age > timedelta(days=STALE_AFTER_DAYS):
            return False, f"Facts read {age.days} days ago: score the business again"
        return True, f"Scored on facts read {age.days} days ago"
    if rule is ChecklistRule.QUALIFYING_TIER:
        if scored is None:
            return False, "Not scored by a finished run yet"
        card = scored.card
        total = "no total" if card.total_score is None else f"total {card.total_score:.1f}"
        return card.tier in _QUALIFYING, f"Tier {_text(card.tier.value)}, {total}"
    if rule is ChecklistRule.VERIFIED_OPPORTUNITY:
        offer = scored.card.offer if scored else None
        if offer is None:
            return False, "No observed problem that an offer solves"
        problems = ", ".join(_text(problem.value) for problem in offer.problems)
        return True, f"{offer.offer_name} addresses {problems}"
    if rule is ChecklistRule.USABLE_CONTACT:
        if lead.contacts.business_suppression is not None:
            return False, "The business is on the do-not-contact list"
        usable = common.routes(lead.contacts)
        if not usable:
            return False, "No usable published channel"
        best = usable[0]
        return True, f"{_text(best.contact_type.value)} {_text(best.validation_status.value)}"
    if rule is ChecklistRule.CONTACT_CONFLICTS_RESOLVED:
        disputed = _open_contact_conflicts(conn, lead)
        if disputed:
            return (
                False,
                f"Records disagree on the {' and '.join(disputed)}: choose the value to show",
            )
        return True, "No disagreement on the contact values"
    if rule is ChecklistRule.DECISION_MAKER_PUBLISHED:
        people = [
            view
            for view in lead.contacts.decision_makers
            if view.decision_maker.review_status is not DecisionMakerReview.REJECTED
        ]
        return bool(people), (
            f"{len(people)} published decision maker(s)"
            if people
            else "No published decision maker"
        )
    if rule is ChecklistRule.WEBSITE_AUDITED:
        if scored is None:
            return False, "Not scored by a finished run yet"
        if scored.facts.audit is not None:
            audit = scored.facts.audit
            return True, f"Audit {audit.status.value} of {audit.site}"
        if not scored.facts.domains:
            return True, "No website is known: nothing to audit"
        return False, "The website has not been audited"
    latino = scored.facts.latino if scored else None
    if latino is None:
        return False, "Latino evidence not evaluated"
    return True, f"Latino evidence evaluated: {_text(latino.status.value)}"


# ---- checklists ---------------------------------------------------------------------------------


def checklist_template() -> ChecklistDefinition:
    auto, manual = ChecklistItemKind.AUTOMATIC, ChecklistItemKind.MANUAL
    rule = ChecklistRule
    items = (
        ("scored_recently", "Scored on facts read in the last 90 days", auto,
         rule.SCORED_RECENTLY, True),
        ("qualifying_tier", "Priority or review tier", auto, rule.QUALIFYING_TIER, True),
        ("verified_opportunity", "An observed problem the recommended offer solves", auto,
         rule.VERIFIED_OPPORTUNITY, True),
        ("usable_contact", "A usable contact route off the do-not-contact list", auto,
         rule.USABLE_CONTACT, True),
        ("contact_conflicts_resolved", "No disagreement on the contact values", auto,
         rule.CONTACT_CONFLICTS_RESOLVED, True),
        ("decision_maker_published", "A decision maker the business published", auto,
         rule.DECISION_MAKER_PUBLISHED, False),
        ("website_audited", "The website audited, when there is one", auto, rule.WEBSITE_AUDITED,
         False),
        ("latino_evaluated", "Latino evidence evaluated", auto, rule.LATINO_EVALUATED, False),
        ("offer_fit_confirmed", "The recommended offer fits what the business sells", manual, None,
         True),
        ("contact_route_reviewed", "A person reviewed the contact route", manual, None, True),
    )  # fmt: skip
    return ChecklistDefinition(
        schema_version=CHECKLIST_SCHEMA_VERSION,
        campaign_ids=[],
        categories=[],
        items=[
            ChecklistItem(key=key, label=label, kind=kind, rule=found, required=required)
            for key, label, kind, found, required in items
        ],
    )


def _versions(conn: sqlite3.Connection, checklist_id: str) -> list[ResearchChecklistVersion]:
    return [
        repository.from_row(ResearchChecklistVersion, row)
        for row in conn.execute(
            f"SELECT {repository._columns(ResearchChecklistVersion)} "  # noqa: S608
            "FROM research_checklist_versions WHERE checklist_id = ? ORDER BY version DESC",
            (checklist_id,),
        )
    ]


def save_checklist(conn: sqlite3.Connection, request: ChecklistSaveRequest, *, actor: str) -> str:
    """Creates a checklist (its first version active), or a new version of one, activated later
    with a reason. An unchanged definition keeps the newest version."""
    definition = request.definition.model_dump(mode="json")
    name, now = request.name.strip(), utc_now()
    try:
        with repository.atomic(conn):
            if request.checklist_id is None:
                checklist = ResearchChecklist(
                    id=new_id(), name=name, description=request.description,
                    active_version_id=None, archived_at=None, created_at=now, updated_at=now,
                )  # fmt: skip
                repository.insert(conn, checklist)
                repository.audit(
                    conn,
                    AuditAction.CREATE,
                    EntityType.RESEARCH_CHECKLIST,
                    checklist.id,
                    actor=actor,
                )
                number = 1
            else:
                checklist = repository.get(conn, ResearchChecklist, request.checklist_id)
                if checklist.archived_at is not None:
                    raise LeadError("restore this checklist before saving a new version")
                if (checklist.name, checklist.description) != (name, request.description):
                    conn.execute(
                        "UPDATE research_checklists SET name = ?, description = ?, updated_at = ? "
                        "WHERE id = ?",
                        (name, request.description, format_ts(now), checklist.id),
                    )
                latest = _versions(conn, checklist.id)[:1]
                if latest and latest[0].definition_json == definition:
                    return checklist.id
                number = latest[0].version + 1 if latest else 1
            version = ResearchChecklistVersion(
                id=new_id(), checklist_id=checklist.id, version=number, definition_json=definition,
                schema_version=CHECKLIST_SCHEMA_VERSION, created_by=actor, created_at=now,
            )  # fmt: skip
            repository.insert(conn, version)
            if number == 1:
                conn.execute(
                    "UPDATE research_checklists SET active_version_id = ? WHERE id = ?",
                    (version.id, checklist.id),
                )
            repository.audit(
                conn, AuditAction.UPDATE, EntityType.RESEARCH_CHECKLIST, checklist.id, actor=actor,
                detail={"version": number, "version_id": version.id},
            )  # fmt: skip
    except sqlite3.IntegrityError as exc:
        if "research_checklists" in str(exc):
            raise LeadError(f"a checklist named '{name}' already exists") from exc
        raise
    return checklist.id


def change_checklist(
    conn: sqlite3.Connection, request: ChecklistChangeRequest, *, actor: str
) -> None:
    now = format_ts(utc_now())
    with repository.atomic(conn):
        checklist = repository.get(conn, ResearchChecklist, request.checklist_id)
        if request.action is ScoringChangeAction.ACTIVATE:
            if request.version_id is None:
                raise LeadError("choose the version to activate")
            version = repository.get(conn, ResearchChecklistVersion, request.version_id)
            if version.checklist_id != checklist.id:
                raise LeadError("that version belongs to another checklist")
            if checklist.archived_at is not None:
                raise LeadError("restore the checklist before activating a version")
            conn.execute(
                "UPDATE research_checklists SET active_version_id = ?, updated_at = ? WHERE id = ?",
                (version.id, now, checklist.id),
            )
        elif request.action is ScoringChangeAction.ARCHIVE:
            conn.execute(
                "UPDATE research_checklists SET archived_at = coalesce(archived_at, ?), "
                "updated_at = ? WHERE id = ?",
                (now, now, checklist.id),
            )
        else:
            try:
                conn.execute(
                    "UPDATE research_checklists SET archived_at = NULL, updated_at = ? "
                    "WHERE id = ?",
                    (now, checklist.id),
                )
            except sqlite3.IntegrityError as exc:
                raise LeadError("a live checklist already uses this name") from exc
        repository.audit(
            conn, AuditAction.UPDATE, EntityType.RESEARCH_CHECKLIST, checklist.id, actor=actor,
            detail={"action": request.action.value, "version_id": request.version_id,
                    "reason": request.reason},
        )  # fmt: skip


def ensure_default_checklist(conn: sqlite3.Connection) -> None:
    """The built-in checklist, created once so every lead has one."""
    if conn.execute("SELECT 1 FROM research_checklists LIMIT 1").fetchone():
        return
    save_checklist(
        conn,
        ChecklistSaveRequest(
            checklist_id=None,
            name=DEFAULT_CHECKLIST,
            description="Built-in: what every lead needs before it is sales-ready.",
            definition=checklist_template(),
        ),
        actor=ACTOR_SYSTEM,
    )


def checklist_catalog(conn: sqlite3.Connection, query: ChecklistCatalogQuery) -> ChecklistCatalog:
    ensure_default_checklist(conn)
    live = "" if query.include_archived else " WHERE archived_at IS NULL"
    checklists = [
        repository.from_row(ResearchChecklist, row)
        for row in conn.execute(
            f"SELECT {repository._columns(ResearchChecklist)} FROM research_checklists{live} "  # noqa: S608
            "ORDER BY archived_at IS NOT NULL, name, id"
        )
    ]
    return ChecklistCatalog(
        checklists=[
            ChecklistView(checklist=checklist, versions=_versions(conn, checklist.id))
            for checklist in checklists
        ],
        template=checklist_template(),
    )


def _checklist_state(conn: sqlite3.Connection, lead: _Lead) -> ChecklistState:
    ensure_default_checklist(conn)
    active: list[tuple[ResearchChecklist, ResearchChecklistVersion, ChecklistDefinition]] = []
    for row in conn.execute(
        f"SELECT {repository._columns(ResearchChecklist, 'c')} FROM research_checklists c "  # noqa: S608
        "WHERE c.archived_at IS NULL AND c.active_version_id IS NOT NULL "
        "ORDER BY c.updated_at DESC, c.id"
    ).fetchall():
        checklist = repository.from_row(ResearchChecklist, row)
        version = repository.get(conn, ResearchChecklistVersion, str(checklist.active_version_id))
        active.append(
            (checklist, version, ChecklistDefinition.model_validate(version.definition_json))
        )
    if not active:
        raise LeadError("every checklist is archived: restore one")
    marks = _marks(lead.members)
    campaigns = {
        row["campaign_id"]
        for row in conn.execute(
            "SELECT DISTINCT dc.campaign_id FROM entity_records er "  # noqa: S608
            "JOIN discovery_candidates dc ON dc.id = er.candidate_id "
            f"WHERE er.business_id IN ({marks}) AND dc.campaign_id IS NOT NULL",
            lead.members,
        )
    }
    categories = lead.scored.facts.categories if lead.scored else []
    chosen = next(
        ((c, v, d, "campaign") for c, v, d in active if campaigns & set(d.campaign_ids)), None
    ) or next(
        (
            (c, v, d, "category")
            for c, v, d in active
            if d.categories and matches_term(categories, d.categories) is not None
        ),
        None,
    )
    if chosen is None:
        defaults = [(c, v, d) for c, v, d in active if not d.campaign_ids and not d.categories]
        checklist, version, definition = min(
            defaults or active, key=lambda item: (item[0].created_at, item[0].id)
        )
        chosen = (checklist, version, definition, "default")
    checklist, version, definition, matched_by = chosen
    manual: dict[str, sqlite3.Row] = {}
    for row in conn.execute(
        "SELECT item_key, done, note, actor, decided_at FROM research_checks "
        "WHERE business_id = ? ORDER BY decided_at, id",
        (lead.business.id,),
    ):
        manual[row["item_key"]] = row
    items: list[ChecklistItemState] = []
    for item in definition.items:
        if item.rule is not None:
            done, detail = _rule(conn, lead, item.rule)
            items.append(
                ChecklistItemState(
                    item=item, done=done, detail=detail[:300], decided_by=None, decided_at=None
                )
            )
            continue
        found = manual.get(item.key)
        done = bool(found and found["done"])
        detail = (
            found["note"]
            if found and found["note"]
            else ("Checked by a person" if done else "Not checked yet")
        )
        items.append(
            ChecklistItemState(
                item=item,
                done=done,
                detail=str(detail)[:300],
                decided_by=found["actor"] if found else None,
                decided_at=found["decided_at"] if found else None,
            )
        )
    return ChecklistState(
        checklist_id=checklist.id,
        checklist_version_id=version.id,
        name=checklist.name,
        version=version.version,
        matched_by=matched_by,
        items=items,
        complete=all(state.done for state in items if state.item.required),
    )


def sales_ready_checks(
    conn: sqlite3.Connection, lead: _Lead, checklist: ChecklistState
) -> list[CriterionCheck]:
    """Every criterion a lead must meet to be marked sales-ready, in their documented order."""
    suppression = lead.contacts.business_suppression
    found: dict[SalesReadyCriterion, tuple[bool, str]] = {
        SalesReadyCriterion.NOT_SUPPRESSED: (
            suppression is None,
            f"On the do-not-contact list: {suppression.reason}"
            if suppression
            else "Not on the do-not-contact list",
        ),
        SalesReadyCriterion.ACTIVE_BUSINESS: (
            lead.business.status not in (BusinessStatus.CLOSED, BusinessStatus.MERGED),
            f"Business status: {lead.business.status.value}",
        ),
    }
    for criterion, rule in _CRITERION_RULES.items():
        found[criterion] = _rule(conn, lead, rule)
    required = [state for state in checklist.items if state.item.required]
    found[SalesReadyCriterion.CHECKLIST_COMPLETE] = (
        checklist.complete,
        f"{sum(state.done for state in required)} of {len(required)} required items of "
        f"'{checklist.name}' done",
    )
    return [
        CriterionCheck(
            criterion=criterion, passed=found[criterion][0], detail=found[criterion][1][:300]
        )
        for criterion in SalesReadyCriterion
    ]


# ---- evidence -----------------------------------------------------------------------------------

_PROVENANCE_JOIN: Final = (
    "JOIN extractions x ON x.id = o.extraction_id JOIN raw_records r ON r.id = x.raw_record_id "
    "JOIN source_connectors sc ON sc.id = r.connector_id"
)


def _audited(conn: sqlite3.Connection, members: Sequence[str], audit_id: str) -> bool:
    """An audit belongs to a business when one of its stored scores read it."""
    return (
        conn.execute(
            "SELECT 1 FROM business_scores "  # noqa: S608
            f"WHERE business_id IN ({_marks(members)}) "
            "AND json_extract(facts_json, '$.audit.id') = ? "
            "LIMIT 1",
            [*members, audit_id],
        ).fetchone()
        is not None
    )


def _evidence(
    conn: sqlite3.Connection, lead: _Lead, kind: EvidenceRefKind, ref: str
) -> EvidenceDetail:
    members, marks = lead.members, _marks(lead.members)
    root = lead.business.id
    missing = repository.NotFoundError(f"{kind.value} {ref} is not evidence of business {root}")

    def detail(**fields: object) -> EvidenceDetail:
        return EvidenceDetail.model_validate(
            {"business_id": root, "kind": kind, "ref": ref, **fields}
        )

    if kind is EvidenceRefKind.BUSINESS:
        if ref not in members:
            raise missing
        business = repository.get(conn, Business, ref)
        return detail(
            label=business.display_name, excerpt=None, status=business.status.value,
            confidence=None, observed_at=business.last_seen_at, source_name=None, source_url=None,
            provenance=None,
        )  # fmt: skip
    if kind is EvidenceRefKind.CONTACT_CHANNEL:
        channel = next((c for c in lead.contacts.channels if c.key == ref), None)
        if channel is None:
            raise missing
        source = channel.sources[0] if channel.sources else None
        return detail(
            label=f"{channel.contact_type.value}: {channel.value}"[:300], excerpt=channel.value,
            status=channel.validation_status.value, confidence=None,
            observed_at=channel.last_seen_at,
            source_name=source.provenance.connector_name if source else None,
            source_url=source.provenance.source_url if source else None,
            provenance=source.provenance if source else None,
        )  # fmt: skip
    if kind in (EvidenceRefKind.EVIDENCE_ITEM, EvidenceRefKind.DECISION_MAKER):
        table, text = (
            ("evidence_items", "coalesce(o.snippet, o.claim)")
            if kind is EvidenceRefKind.EVIDENCE_ITEM
            else ("decision_makers", "o.full_name_raw || coalesce(' — ' || o.role_raw, '')")
        )
        status = "o.status" if kind is EvidenceRefKind.EVIDENCE_ITEM else "o.review_status"
        label = "o.claim" if kind is EvidenceRefKind.EVIDENCE_ITEM else "o.full_name_raw"
        row = conn.execute(
            f"SELECT {label} AS label, {text} AS excerpt, {status} AS status, o.confidence, "  # noqa: S608
            f"o.observed_at, {repository._PROVENANCE} FROM {table} o {_PROVENANCE_JOIN} "
            f"WHERE o.id = ? AND o.business_id IN ({marks})",
            [ref, *members],
        ).fetchone()
        if row is None:
            raise missing
        provenance = repository.from_row(Provenance, row, prefix="p_")
        return detail(
            label=str(row["label"])[:300], excerpt=str(row["excerpt"])[:2000], status=row["status"],
            confidence=row["confidence"], observed_at=row["observed_at"],
            source_name=provenance.connector_name, source_url=provenance.source_url,
            provenance=provenance,
        )  # fmt: skip
    if kind is EvidenceRefKind.WEBSITE_AUDIT:
        row = conn.execute(
            "SELECT id, site, status, coverage, final_url, root_url, created_at "
            "FROM website_audits "
            "WHERE id = ?",
            (ref,),
        ).fetchone()
        if row is None or not _audited(conn, members, ref):
            raise missing
        return detail(
            label=row["site"],
            excerpt=f"Audit {row['status']}, coverage {round(row['coverage'] * 100)}%",
            status=row["status"], confidence=row["coverage"], observed_at=row["created_at"],
            source_name="Website intelligence", source_url=row["final_url"] or row["root_url"],
            provenance=None,
        )  # fmt: skip
    if kind in (EvidenceRefKind.WEBSITE_FINDING, EvidenceRefKind.WEBSITE_TECHNOLOGY):
        finding = kind is EvidenceRefKind.WEBSITE_FINDING
        row = conn.execute(
            (
                "SELECT f.audit_id, f.title AS label, f.observed_value, f.expected_condition, "
                "f.result AS status, f.confidence, f.created_at, f.evidence_json "
                "FROM website_findings f WHERE f.id = ?"
                if finding
                else "SELECT t.audit_id, t.name AS label, t.category AS observed_value, "
                "NULL AS expected_condition, NULL AS status, t.confidence, t.created_at, "
                "NULL AS evidence_json FROM website_technologies t WHERE t.id = ?"
            ),
            (ref,),
        ).fetchone()
        if row is None or not _audited(conn, members, row["audit_id"]):
            raise missing
        refs = orjson.loads(row["evidence_json"]).get("refs") if row["evidence_json"] else None
        url = next(
            (item.get("url") for item in refs or [] if isinstance(item, dict) and item.get("url")),
            None,
        )
        observed = row["observed_value"] or "not observed"
        excerpt = f"Observed: {observed}" + (
            f". Expected: {row['expected_condition']}" if row["expected_condition"] else ""
        )
        return detail(
            label=str(row["label"])[:300], excerpt=excerpt[:2000], status=row["status"],
            confidence=row["confidence"], observed_at=row["created_at"],
            source_name="Website intelligence", source_url=url, provenance=None,
        )  # fmt: skip
    if kind is EvidenceRefKind.LATINO_ASSESSMENT:
        row = conn.execute(
            "SELECT status, assessment_confidence, rationale, assessed_at, method "  # noqa: S608
            f"FROM latino_assessments WHERE id = ? AND business_id IN ({marks})",
            [ref, *members],
        ).fetchone()
        if row is None:
            raise missing
        return detail(
            label=f"{_text(row['status'])} ({_text(row['method'])})",
            excerpt=row["rationale"][:2000],
            status=row["status"], confidence=row["assessment_confidence"],
            observed_at=row["assessed_at"], source_name="Latino evidence", source_url=None,
            provenance=None,
        )  # fmt: skip
    if kind is EvidenceRefKind.INFERENCE_RUN:
        row = conn.execute(
            "SELECT task_type, status, model, review_status, finished_at, started_at "  # noqa: S608
            f"FROM ai_inference_runs WHERE id = ? AND business_id IN ({marks})",
            [ref, *members],
        ).fetchone()
        if row is None:
            raise missing
        return detail(
            label=f"{_text(row['task_type'])} by {row['model']}", excerpt=None,
            status=row["review_status"], confidence=None,
            observed_at=row["finished_at"] or row["started_at"], source_name="Local AI",
            source_url=None, provenance=None,
        )  # fmt: skip
    row = conn.execute(
        "SELECT r.id, r.finished_at FROM score_runs r "  # noqa: S608
        "JOIN business_scores s ON s.score_run_id = r.id "
        f"WHERE r.id = ? AND s.business_id IN ({marks}) LIMIT 1",
        [ref, *members],
    ).fetchone()
    if row is None:
        raise missing
    return detail(
        label=f"Score run {ref[-12:]}", excerpt=None, status=None, confidence=None,
        observed_at=row["finished_at"], source_name="Lead scoring", source_url=None,
        provenance=None,
    )  # fmt: skip


def evidence_detail(
    conn: sqlite3.Connection, query: EvidenceQuery, now: datetime
) -> EvidenceDetail:
    return _evidence(conn, _lead(conn, query.business_id, now), query.kind, query.ref)


# ---- the workspace ------------------------------------------------------------------------------


def _corrections(conn: sqlite3.Connection, lead: _Lead) -> list[CorrectionView]:
    marks = _marks(lead.members)
    select = (
        "SELECT {field} AS field, o.id, {value} AS value, r.payload_json, x.extracted_at "  # noqa: S608
        "FROM {table} o JOIN extractions x ON x.id = o.extraction_id AND x.extractor = ? "
        f"JOIN raw_records r ON r.id = x.raw_record_id WHERE o.business_id IN ({marks})"
    )
    parts = (
        ("'name'", "o.name_raw", "business_names"),
        ("'location'", "coalesce(o.address_raw, o.locality)", "business_locations"),
        ("'website'", "o.url_normalized", "websites"),
        ("o.contact_type", "coalesce(o.value_normalized, o.value_raw)", "contact_points"),
        ("'category'", "o.label_raw", "business_classifications"),
    )
    sql = " UNION ALL ".join(
        select.format(field=field, value=value, table=table) for field, value, table in parts
    )
    params: list[object] = []
    for _ in parts:
        params += [CORRECTION_EXTRACTOR, *lead.members]
    shown = {
        row["observation_id"]
        for row in conn.execute(
            "SELECT observation_id FROM canonical_values WHERE business_id = ? "
            "AND superseded_at IS NULL",
            (lead.business.id,),
        )
    }
    views: list[CorrectionView] = []
    for row in conn.execute(f"{sql} ORDER BY extracted_at DESC", params):
        payload = orjson.loads(row["payload_json"])
        views.append(
            CorrectionView(
                field=CanonicalField(row["field"]),
                value=row["value"],
                observation_id=row["id"],
                reason=str(payload.get("reason") or "")[:2000] or "correction",
                actor=str(payload.get("actor") or "unknown"),
                corrected_at=row["extracted_at"],
                shown=row["id"] in shown,
            )
        )
    return views


def _tags(conn: sqlite3.Connection, root: str) -> list[str]:
    return [
        row["name"]
        for row in conn.execute(
            "SELECT t.name FROM entity_tags et JOIN tags t ON t.id = et.tag_id "
            "WHERE et.entity_type = 'business' AND et.entity_id = ? AND t.deleted_at IS NULL "
            "ORDER BY t.name",
            (root,),
        )
    ]


def workspace(conn: sqlite3.Connection, business_id: str, now: datetime) -> ResearchWorkspace:
    lead = _lead(conn, business_id, now)
    root = lead.business.id
    found = conn.execute(
        f"SELECT {repository._columns(LeadResearch)} FROM lead_research WHERE business_id = ?",  # noqa: S608
        (root,),
    ).fetchone()
    checklist = _checklist_state(conn, lead)
    checks = sales_ready_checks(conn, lead, checklist)
    pins: list[PinView] = []
    for row in conn.execute(
        f"SELECT {repository._columns(EvidencePin)} FROM evidence_pins "  # noqa: S608
        "WHERE business_id = ? AND unpinned_at IS NULL ORDER BY pinned_at DESC, id DESC LIMIT ?",
        (root, MAX_LISTED),
    ).fetchall():
        pin = repository.from_row(EvidencePin, row)
        try:
            _evidence(conn, lead, pin.ref_kind, pin.ref)
            available = True
        except repository.NotFoundError:
            available = False
        pins.append(PinView(pin=pin, available=available))
    decision_marks = _marks(RESEARCH_DECISIONS)
    return ResearchWorkspace(
        business_id=root,
        project_id=lead.business.project_id,
        display_name=lead.business.display_name,
        is_synthetic=common.is_synthetic(conn, lead.business.project_id),
        score_run_id=lead.scored.run.id if lead.scored else None,
        lead_stage=lead.business.lead_stage,
        research=repository.from_row(LeadResearch, found) if found else None,
        can_undo=conn.execute(
            "SELECT 1 FROM review_decisions WHERE entity_type = 'business' AND entity_id = ? "
            "AND decision = ? LIMIT 1",
            (root, STATUS_DECISION),
        ).fetchone()
        is not None,
        suppressed=lead.contacts.business_suppression is not None,
        sales_ready=checks,
        sales_ready_allowed=all(check.passed for check in checks),
        checklist=checklist,
        tasks=[
            repository.from_row(ResearchTask, row)
            for row in conn.execute(
                f"SELECT {repository._columns(ResearchTask)} FROM research_tasks "  # noqa: S608
                "WHERE business_id = ? ORDER BY status = 'open' DESC, created_at DESC, id DESC "
                "LIMIT ?",
                (root, MAX_LISTED),
            )
        ],
        pins=pins,
        notes=[
            repository.from_row(Note, row)
            for row in conn.execute(
                f"SELECT {repository._columns(Note)} FROM notes WHERE entity_type = 'business' "  # noqa: S608
                "AND entity_id = ? AND deleted_at IS NULL "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (root, MAX_LISTED),
            )
        ],
        tags=_tags(conn, root),
        corrections=_corrections(conn, lead),
        decisions=[
            repository.from_row(ReviewDecision, row)
            for row in conn.execute(
                f"SELECT {repository._columns(ReviewDecision)} FROM review_decisions "  # noqa: S608
                "WHERE entity_type = 'business' AND entity_id = ? "
                f"AND decision IN ({decision_marks}) "
                "ORDER BY decided_at DESC, id DESC LIMIT ?",
                (root, *RESEARCH_DECISIONS, MAX_DECISIONS),
            )
        ],
    )


# ---- actions ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RefreshPlan:
    """A refresh the caller starts: the engine job of one scope, recorded on its task."""

    scope: RefreshScope
    task_id: str
    project_id: str
    urls: tuple[str, ...]


def _audit(conn: sqlite3.Connection, root: str, actor: str, detail: dict[str, object]) -> None:
    repository.audit(
        conn, AuditAction.UPDATE, EntityType.BUSINESS, root, actor=actor,
        detail={key: value for key, value in detail.items()},  # type: ignore[misc]
    )  # fmt: skip


def _ensure_research(conn: sqlite3.Connection, business: Business, now: datetime) -> None:
    conn.execute(
        "INSERT INTO lead_research (business_id, project_id, status, status_reason, status_by, "
        "status_at, assignee, assigned_at, updated_at) VALUES (?, ?, NULL, NULL, NULL, NULL, NULL, "
        "NULL, ?) ON CONFLICT (business_id) DO NOTHING",
        (business.id, business.project_id, format_ts(now)),
    )


def _write_status(
    conn: sqlite3.Connection,
    business: Business,
    status: ResearchStatus | None,
    reason: str,
    now: datetime,
    *,
    actor: str,
    batch_id: str | None = None,
) -> None:
    with repository.atomic(conn):
        _ensure_research(conn, business, now)
        (previous,) = conn.execute(
            "SELECT status FROM lead_research WHERE business_id = ?", (business.id,)
        ).fetchone()
        stamp = format_ts(now)
        conn.execute(
            "UPDATE lead_research SET status = ?, status_reason = ?, status_by = ?, status_at = ?, "
            "updated_at = ? WHERE business_id = ?",
            (
                status.value if status else None,
                reason if status else None,
                actor if status else None,
                stamp if status else None,
                stamp,
                business.id,
            ),
        )
        repository.insert(
            conn,
            ReviewDecision(
                id=new_id(), entity_type=EntityType.BUSINESS, entity_id=business.id,
                decision=STATUS_DECISION, previous_value=previous,
                new_value=status.value if status else None, reason=reason, actor=actor,
                decided_at=now,
            ),
        )  # fmt: skip
        _audit(
            conn, business.id, actor,
            {"field": STATUS_DECISION, "from": previous, "to": status.value if status else None,
             "reason": reason, "batch_id": batch_id},
        )  # fmt: skip


def _set_status(
    conn: sqlite3.Connection,
    business: Business,
    status: ResearchStatus | None,
    reason: str,
    now: datetime,
    *,
    actor: str,
) -> None:
    current = conn.execute(
        "SELECT status FROM lead_research WHERE business_id = ?", (business.id,)
    ).fetchone()
    if (current["status"] if current else None) == (status.value if status else None):
        raise LeadError(
            f"the lead is already {_text(status.value) if status else 'without a status'}"
        )
    if status is ResearchStatus.SALES_READY:
        lead = _lead(conn, business.id, now)
        failed = [
            check
            for check in sales_ready_checks(conn, lead, _checklist_state(conn, lead))
            if not check.passed
        ]
        if failed:
            raise LeadError(
                "not sales-ready: "
                + "; ".join(f"{check.criterion.value} ({check.detail})" for check in failed)
            )
    _write_status(conn, business, status, reason, now, actor=actor)


def _write_assignee(
    conn: sqlite3.Connection,
    business: Business,
    assignee: str | None,
    reason: str | None,
    now: datetime,
    *,
    actor: str,
    batch_id: str | None = None,
) -> bool:
    with repository.atomic(conn):
        _ensure_research(conn, business, now)
        (previous,) = conn.execute(
            "SELECT assignee FROM lead_research WHERE business_id = ?", (business.id,)
        ).fetchone()
        if previous == assignee:
            return False
        stamp = format_ts(now)
        conn.execute(
            "UPDATE lead_research SET assignee = ?, assigned_at = ?, updated_at = ? "
            "WHERE business_id = ?",
            (assignee, stamp if assignee else None, stamp, business.id),
        )
        repository.insert(
            conn,
            ReviewDecision(
                id=new_id(), entity_type=EntityType.BUSINESS, entity_id=business.id,
                decision=ASSIGNEE_DECISION, previous_value=previous, new_value=assignee,
                reason=reason or ("Assigned" if assignee else "Unassigned"), actor=actor,
                decided_at=now,
            ),
        )  # fmt: skip
        _audit(
            conn, business.id, actor,
            {"field": ASSIGNEE_DECISION, "from": previous, "to": assignee, "batch_id": batch_id},
        )  # fmt: skip
    return True


def _tag_id(
    conn: sqlite3.Connection, project_id: str, name: str, now: datetime, *, create: bool, actor: str
) -> str | None:
    row = conn.execute(
        "SELECT id FROM tags WHERE project_id = ? AND lower(name) = lower(?) "
        "AND deleted_at IS NULL",
        (project_id, name.strip()),
    ).fetchone()
    if row is not None:
        return str(row["id"])
    if not create:
        return None
    tag = Tag(
        id=new_id(), project_id=project_id, name=name.strip(), created_at=now, deleted_at=None,
        deleted_reason=None,
    )  # fmt: skip
    repository.insert(conn, tag)
    repository.audit(conn, AuditAction.CREATE, EntityType.TAG, tag.id, actor=actor)
    return tag.id


def _write_tag(
    conn: sqlite3.Connection,
    business: Business,
    name: str,
    add: bool,
    now: datetime,
    *,
    actor: str,
    batch_id: str | None = None,
) -> bool:
    with repository.atomic(conn):
        tag_id = _tag_id(conn, business.project_id, name, now, create=add, actor=actor)
        if tag_id is None:
            return False
        if add:
            changed = conn.execute(
                "INSERT OR IGNORE INTO entity_tags (tag_id, entity_type, entity_id, created_at) "
                "VALUES (?, 'business', ?, ?)",
                (tag_id, business.id, format_ts(now)),
            ).rowcount
        else:
            changed = conn.execute(
                "DELETE FROM entity_tags WHERE tag_id = ? AND entity_type = 'business' "
                "AND entity_id = ?",
                (tag_id, business.id),
            ).rowcount
        if changed:
            _audit(
                conn, business.id, actor,
                {"field": "tag", "added" if add else "removed": name.strip(), "batch_id": batch_id},
            )  # fmt: skip
    return bool(changed)


def _task(conn: sqlite3.Connection, business: Business, task_id: str) -> ResearchTask:
    task = repository.get(conn, ResearchTask, task_id)
    if task.business_id != business.id:
        raise repository.NotFoundError(f"task {task_id} is not a task of business {business.id}")
    return task


def _new_task(
    business: Business,
    kind: ResearchTaskKind,
    key: str,
    title: str,
    detail: dict[str, object] | None,
    now: datetime,
    actor: str,
) -> ResearchTask:
    return ResearchTask.model_validate(
        {
            "id": new_id(), "business_id": business.id, "project_id": business.project_id,
            "kind": kind, "key": key, "title": title[:300], "detail_json": detail,
            "status": ResearchTaskStatus.OPEN, "created_by": actor, "created_at": now,
            "updated_at": now, "closed_by": None, "closed_at": None, "close_reason": None,
        }
    )  # fmt: skip


def _sync_tasks(conn: sqlite3.Connection, business: Business, now: datetime, *, actor: str) -> int:
    """Opens a task for every weighted unknown of the score and every required checklist item left
    undone, unless one is already open for it."""
    lead = _lead(conn, business.id, now)
    open_keys = {
        row["key"]
        for row in conn.execute(
            "SELECT key FROM research_tasks WHERE business_id = ? AND status = 'open'",
            (business.id,),
        )
    }
    tasks: list[ResearchTask] = []
    for missing in lead.scored.card.missing[:MAX_SYNCED_MISSING] if lead.scored else []:
        key = f"missing:{missing.feature}"
        if key not in open_keys:
            tasks.append(
                _new_task(
                    business, ResearchTaskKind.MISSING_DATA, key, f"Find out: {missing.detail}",
                    {"feature": missing.feature, "component": missing.component.value,
                     "weight_share": missing.weight_share},
                    now, actor,
                )
            )  # fmt: skip
            open_keys.add(key)
    for state in _checklist_state(conn, lead).items:
        key = f"checklist:{state.item.key}"
        if state.item.required and not state.done and key not in open_keys:
            tasks.append(
                _new_task(
                    business, ResearchTaskKind.CHECKLIST, key, state.item.label,
                    {"item_key": state.item.key, "detail": state.detail}, now, actor,
                )
            )  # fmt: skip
            open_keys.add(key)
    if tasks:
        with repository.atomic(conn):
            repository.insert(conn, *tasks)
            _audit(conn, business.id, actor, {"field": "tasks", "opened": len(tasks)})
    return len(tasks)


def _close_task(
    conn: sqlite3.Connection,
    business: Business,
    task_id: str,
    status: ResearchTaskStatus,
    reason: str | None,
    now: datetime,
    *,
    actor: str,
) -> None:
    with repository.atomic(conn):
        task = _task(conn, business, task_id)
        stamp = format_ts(now)
        if status is ResearchTaskStatus.OPEN:
            if task.status is ResearchTaskStatus.OPEN:
                raise LeadError("the task is already open")
            try:
                conn.execute(
                    "UPDATE research_tasks SET status = 'open', closed_by = NULL, "
                    "closed_at = NULL, "
                    "close_reason = NULL, updated_at = ? WHERE id = ?",
                    (stamp, task.id),
                )
            except sqlite3.IntegrityError as exc:
                raise LeadError("another open task already covers the same subject") from exc
        else:
            if task.status is not ResearchTaskStatus.OPEN:
                raise LeadError("only an open task can be completed or dismissed")
            conn.execute(
                "UPDATE research_tasks SET status = ?, closed_by = ?, closed_at = ?, "
                "close_reason = ?, updated_at = ? WHERE id = ?",
                (status.value, actor, stamp, reason, stamp, task.id),
            )
        _audit(
            conn, business.id, actor,
            {"field": "task", "task_id": task.id, "to": status.value, "reason": reason},
        )  # fmt: skip


def _check_item(
    conn: sqlite3.Connection,
    business: Business,
    item_key: str,
    done: bool,
    note: str | None,
    now: datetime,
    *,
    actor: str,
) -> None:
    lead = _lead(conn, business.id, now)
    checklist = _checklist_state(conn, lead)
    state = next((item for item in checklist.items if item.item.key == item_key), None)
    if state is None:
        raise LeadError(f"'{item_key}' is not an item of the checklist '{checklist.name}'")
    if state.item.kind is ChecklistItemKind.AUTOMATIC:
        raise LeadError("an automatic item follows the stored facts: it cannot be ticked by hand")
    if state.done == done:
        raise LeadError(f"the item is already {'checked' if done else 'unchecked'}")
    with repository.atomic(conn):
        repository.insert(
            conn,
            ResearchCheck(
                id=new_id(), business_id=business.id,
                checklist_version_id=checklist.checklist_version_id, item_key=item_key, done=done,
                note=note[:1000] if note else None, actor=actor, decided_at=now,
            ),
        )  # fmt: skip
        _audit(conn, business.id, actor, {"field": "checklist", "item_key": item_key, "done": done})


def _correction_source(
    conn: sqlite3.Connection, project_id: str, now: datetime
) -> tuple[SourceConnector, SourceRun]:
    row = conn.execute(
        f"SELECT {repository._columns(SourceConnector)} FROM source_connectors WHERE key = ?",  # noqa: S608
        (CORRECTION_CONNECTOR,),
    ).fetchone()
    if row is not None:
        connector = repository.from_row(SourceConnector, row)
    else:
        connector = SourceConnector(
            id=new_id(), key=CORRECTION_CONNECTOR, name="Analyst corrections",
            kind=SourceKind.MANUAL_SEED, connector_version=CORRECTION_VERSION, terms_url=None,
            requires_credentials=False, enabled=True, created_at=now, updated_at=now,
        )  # fmt: skip
        repository.insert(conn, connector)
    row = conn.execute(
        f"SELECT {repository._columns(SourceRun)} FROM source_runs "  # noqa: S608
        "WHERE project_id = ? AND connector_id = ? ORDER BY created_at, id LIMIT 1",
        (project_id, connector.id),
    ).fetchone()
    if row is not None:
        return connector, repository.from_row(SourceRun, row)
    run = SourceRun(
        id=new_id(), project_id=project_id, campaign_id=None, connector_id=connector.id,
        connector_version=CORRECTION_VERSION, config_id=None, job_id=None,
        status=SourceRunStatus.SUCCEEDED, effective_config_json={"purpose": "analyst corrections"},
        started_at=now, finished_at=now, error_code=None, error_message=None, created_at=now,
        updated_at=now,
    )  # fmt: skip
    repository.insert(conn, run)
    return connector, run


def _observation(
    field: CanonicalField,
    value: str,
    business_id: str,
    extraction_id: str,
    now: datetime,
    context: str,
) -> BusinessName | BusinessLocation | Website | ContactPoint | BusinessClassification:
    """The analyst's value as an observation of the business, or a refusal saying why it cannot be
    read. Normalised exactly as the engines normalise what sources publish."""
    common_fields = {"id": new_id(), "business_id": business_id, "extraction_id": extraction_id}
    seen = {"confidence": 1.0, "observed_at": now, "created_at": now}
    if field is CanonicalField.NAME:
        name = normalize.business_name(value)
        if not name.display:
            raise LeadError("that is not a business name")
        return BusinessName(
            **common_fields, name_raw=name.display,
            name_normalized=name.key or name.display.casefold(),
            name_kind=NameKind.TRADE, is_primary=False, **seen,
        )  # fmt: skip
    if field is CanonicalField.LOCATION:
        address = normalize.us_address(value)
        if not (address.street or address.locality or address.postal_code):
            raise LeadError("that is not an address this build can read")
        return BusinessLocation(
            **common_fields, address_raw=value, street=address.street, unit=address.unit,
            locality=address.locality, region=address.region, postal_code=address.postal_code,
            country_code=address.country_code, latitude=None, longitude=None, is_primary=False,
            **seen,
        )  # fmt: skip
    if field is CanonicalField.WEBSITE:
        site = normalize.website(value)
        if not site.url or not site.host:
            raise LeadError("that is not a website address")
        if site.is_platform:
            raise LeadError("a profile or listing page does not identify the business's website")
        return Website(
            **common_fields, url_raw=value, url_normalized=site.url,
            domain=(site.registrable_domain or site.host).lower(), is_primary=False, **seen,
        )  # fmt: skip
    if field is CanonicalField.CATEGORY:
        category = normalize.category(value)
        if not category.label:
            raise LeadError("that is not a category")
        return BusinessClassification(
            **common_fields, kind=ClassificationKind.CATEGORY, label_raw=category.label,
            taxonomy_key=category.taxonomy_key, taxonomy_version=category.taxonomy_version,
            is_primary=False, **seen,
        )  # fmt: skip
    if field is CanonicalField.PHONE:
        phone = normalize.phone(value)
        if not phone.e164:
            raise LeadError("that is not a phone number this build can place")
        kind, normalized = ContactType.PHONE, phone.e164
    else:
        email = normalize.email(value)
        if not email.address:
            raise LeadError("that is not an email address")
        kind, normalized = ContactType.EMAIL, email.match_key or email.address.lower()
    return ContactPoint(
        **common_fields, location_id=None, decision_maker_id=None, contact_type=kind,
        value_raw=value, value_normalized=normalized, origin=ContactOrigin.USER_IMPORT,
        platform=None, web_page_id=None, role_or_department=None, published_context=context[:500],
        is_primary=False, validation_status=ValidationStatus.STRUCTURALLY_VALID,
        validated_at=now, validation_method="analyst_correction", do_not_contact=False,
        suppression_reason=None, confidence=1.0, first_seen_at=now, last_seen_at=now,
        created_at=now, updated_at=now, deleted_at=None, deleted_reason=None,
    )  # fmt: skip


def _correct(
    conn: sqlite3.Connection,
    business: Business,
    field: CanonicalField,
    value: str,
    reason: str,
    now: datetime,
    *,
    actor: str,
) -> None:
    with repository.atomic(conn):
        connector, run = _correction_source(conn, business.project_id, now)
        payload: dict[str, object] = {
            "business_id": business.id, "field": field.value, "value": value, "reason": reason,
            "actor": actor, "corrected_at": format_ts(now),
        }  # fmt: skip
        raw = RawRecord.model_validate(
            {
                "id": new_id(), "source_run_id": run.id, "acquired_page_id": None,
                "connector_id": connector.id, "method": AcquisitionMethod.MANUAL_ENTRY,
                "external_id": f"correction:{new_id()}", "source_url": None,
                "payload_json": payload,
                "payload_sha256": hashlib.sha256(
                    orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)
                ).hexdigest(),
                "acquired_at": now, "created_at": now,
            }
        )  # fmt: skip
        extraction = Extraction(
            id=new_id(), raw_record_id=raw.id, extractor=CORRECTION_EXTRACTOR,
            extractor_version=CORRECTION_VERSION, extracted_at=now,
        )  # fmt: skip
        observation = _observation(
            field, value, business.id, extraction.id, now, f"Corrected by {actor}: {reason}"
        )
        repository.insert(conn, raw, extraction, observation)
        _audit(
            conn, business.id, actor,
            {"field": "correction", "canonical_field": field.value,
             "observation_id": observation.id,
             "reason": reason},
        )  # fmt: skip
        try:
            resolution_store.choose_canonical(
                conn,
                CanonicalChoiceRequest(
                    business_id=business.id,
                    field=field,
                    observation_id=observation.id,
                    reason=reason,
                ),
                actor=actor,
            )
        except resolution_store.ResolutionError as exc:
            raise LeadError(str(exc)) from exc


def _refresh(
    conn: sqlite3.Connection,
    business: Business,
    scopes: Sequence[RefreshScope],
    reason: str | None,
    now: datetime,
    *,
    actor: str,
) -> list[RefreshPlan]:
    members = common.members_of(conn, business.id)
    urls = tuple(
        dict.fromkeys(
            row["url_normalized"]
            for row in conn.execute(
                "SELECT url_normalized FROM websites "  # noqa: S608
                f"WHERE business_id IN ({_marks(members)}) "
                "ORDER BY is_primary DESC, confidence DESC, id",
                members,
            )
        )
    )[:MAX_REFRESH_URLS]
    if RefreshScope.PAGES in scopes and not urls:
        raise LeadError("no website is known for this business: there are no pages to refresh")
    plans: list[RefreshPlan] = []
    with repository.atomic(conn):
        for scope in dict.fromkeys(scopes):
            key = f"refresh:{scope.value}"
            row = conn.execute(
                "SELECT id FROM research_tasks WHERE business_id = ? AND key = ? "
                "AND status = 'open'",
                (business.id, key),
            ).fetchone()
            if row is not None:
                task_id = str(row["id"])
            else:
                task = _new_task(
                    business, ResearchTaskKind.REFRESH, key, f"Refresh the {_text(scope.value)}",
                    {"scope": scope.value, "reason": reason, "jobs": []}, now, actor,
                )  # fmt: skip
                repository.insert(conn, task)
                task_id = task.id
            plans.append(
                RefreshPlan(
                    scope=scope,
                    task_id=task_id,
                    project_id=business.project_id,
                    urls=urls if scope is RefreshScope.PAGES else (),
                )
            )
        _audit(
            conn, business.id, actor,
            {"field": "refresh", "scopes": [scope.value for scope in scopes], "reason": reason},
        )  # fmt: skip
    return plans


def attach_jobs(conn: sqlite3.Connection, task_id: str, job_ids: Sequence[str]) -> None:
    """Records the jobs a refresh started on its task."""
    with repository.atomic(conn):
        task = repository.get(conn, ResearchTask, task_id)
        detail: dict[str, object] = dict(task.detail_json or {})
        listed = detail.get("jobs")
        previous = [str(job) for job in listed] if isinstance(listed, list) else []
        detail["jobs"] = sorted({*previous, *job_ids})
        conn.execute(
            "UPDATE research_tasks SET detail_json = ?, updated_at = ? WHERE id = ?",
            (orjson.dumps(detail).decode(), format_ts(utc_now()), task.id),
        )


def act(
    conn: sqlite3.Connection, request: ResearchActionRequest, now: datetime, *, actor: str
) -> list[RefreshPlan]:
    """Applies one research change. Returns the refreshes the caller must start."""
    business = common.business_of(conn, request.business_id)
    action = request.action
    if action is ResearchAction.SET_STATUS:
        _set_status(conn, business, request.status, str(request.reason), now, actor=actor)
    elif action is ResearchAction.UNDO_STATUS:
        last = conn.execute(
            "SELECT previous_value FROM review_decisions WHERE entity_type = 'business' "
            "AND entity_id = ? AND decision = ? ORDER BY decided_at DESC, id DESC LIMIT 1",
            (business.id, STATUS_DECISION),
        ).fetchone()
        if last is None:
            raise LeadError("there is no status change to undo")
        target = ResearchStatus(last["previous_value"]) if last["previous_value"] else None
        _set_status(conn, business, target, f"Undo: {request.reason}", now, actor=actor)
    elif action in (ResearchAction.ASSIGN, ResearchAction.UNASSIGN):
        assignee = (
            request.assignee.strip()
            if action is ResearchAction.ASSIGN and request.assignee
            else None
        )
        if not _write_assignee(conn, business, assignee, request.reason, now, actor=actor):
            raise LeadError("the lead already has that assignee")
    elif action in (ResearchAction.ADD_TAG, ResearchAction.REMOVE_TAG):
        added = action is ResearchAction.ADD_TAG
        if not _write_tag(conn, business, str(request.tag), added, now, actor=actor):
            raise LeadError(f"the lead {'already has' if added else 'has no'} tag '{request.tag}'")
    elif action is ResearchAction.ADD_NOTE:
        with repository.atomic(conn):
            note = Note(
                id=new_id(), project_id=business.project_id, entity_type=EntityType.BUSINESS,
                entity_id=business.id, body=str(request.note), author=actor, created_at=now,
                updated_at=now, deleted_at=None, deleted_reason=None,
            )  # fmt: skip
            repository.insert(conn, note)
            repository.audit(
                conn, AuditAction.CREATE, EntityType.NOTE, note.id, actor=actor,
                detail={"business_id": business.id},
            )  # fmt: skip
    elif action is ResearchAction.DELETE_NOTE:
        note = repository.get(conn, Note, str(request.note_id))
        if note.entity_id != business.id or note.deleted_at is not None:
            raise repository.NotFoundError(
                f"note {request.note_id} is not a live note of this lead"
            )
        repository.soft_delete(conn, Note, note.id, actor=actor, reason=str(request.reason))
    elif action is ResearchAction.PIN:
        lead = _lead(conn, business.id, now)
        found = _evidence(
            conn, lead, request.ref_kind or EvidenceRefKind.BUSINESS, str(request.ref)
        )
        try:
            with repository.atomic(conn):
                pin = EvidencePin(
                    id=new_id(), business_id=business.id, ref_kind=found.kind, ref=found.ref,
                    label=found.label[:300] or found.ref[:300], note=request.note, pinned_by=actor,
                    pinned_at=now, unpinned_by=None, unpinned_at=None, unpin_reason=None,
                )  # fmt: skip
                repository.insert(conn, pin)
                _audit(
                    conn,
                    business.id,
                    actor,
                    {"field": "pin", "ref_kind": found.kind.value, "ref": found.ref},
                )
        except sqlite3.IntegrityError as exc:
            raise LeadError("that evidence is already pinned") from exc
    elif action is ResearchAction.UNPIN:
        with repository.atomic(conn):
            changed = conn.execute(
                "UPDATE evidence_pins SET unpinned_by = ?, unpinned_at = ?, unpin_reason = ? "
                "WHERE business_id = ? AND ref_kind = ? AND ref = ? AND unpinned_at IS NULL",
                (actor, format_ts(now), request.reason, business.id,
                 request.ref_kind.value if request.ref_kind else None, request.ref),
            ).rowcount  # fmt: skip
            if not changed:
                raise repository.NotFoundError("that evidence is not pinned")
            _audit(
                conn,
                business.id,
                actor,
                {"field": "unpin", "ref": request.ref, "reason": request.reason},
            )
    elif action is ResearchAction.ADD_TASK:
        with repository.atomic(conn):
            task_id = new_id()
            task = _new_task(
                business, ResearchTaskKind.CUSTOM, f"custom:{task_id}", str(request.title), None,
                now, actor,
            ).model_copy(update={"id": task_id})  # fmt: skip
            repository.insert(conn, task)
            _audit(conn, business.id, actor, {"field": "task", "task_id": task.id, "to": "open"})
    elif action is ResearchAction.COMPLETE_TASK:
        _close_task(
            conn,
            business,
            str(request.task_id),
            ResearchTaskStatus.DONE,
            request.reason,
            now,
            actor=actor,
        )
    elif action is ResearchAction.DISMISS_TASK:
        _close_task(
            conn,
            business,
            str(request.task_id),
            ResearchTaskStatus.DISMISSED,
            request.reason,
            now,
            actor=actor,
        )
    elif action is ResearchAction.REOPEN_TASK:
        _close_task(
            conn,
            business,
            str(request.task_id),
            ResearchTaskStatus.OPEN,
            request.reason,
            now,
            actor=actor,
        )
    elif action is ResearchAction.SYNC_TASKS:
        _sync_tasks(conn, business, now, actor=actor)
    elif action in (ResearchAction.CHECK_ITEM, ResearchAction.UNCHECK_ITEM):
        done = action is ResearchAction.CHECK_ITEM
        _check_item(
            conn, business, str(request.item_key), done, request.note if done else request.reason,
            now, actor=actor,
        )  # fmt: skip
    elif action is ResearchAction.CORRECT_VALUE:
        if request.field is None or request.value is None:
            raise LeadError("a correction needs a field and a value")
        _correct(
            conn,
            business,
            request.field,
            request.value.strip(),
            str(request.reason),
            now,
            actor=actor,
        )
    elif action is ResearchAction.REQUEST_REFRESH:
        return _refresh(conn, business, request.scopes, request.reason, now, actor=actor)
    return []


# ---- bulk actions -------------------------------------------------------------------------------


def bulk(
    conn: sqlite3.Connection, request: BulkRequest, now: datetime, *, actor: str
) -> BulkResult:
    """One action over many leads: previewed first, then applied lead by lead, each audited."""
    if request.query is not None:
        _, ids = explorer.matching_ids(conn, request.query, now, BULK_MAX_MATCHING)
        if (
            request.mode is BulkMode.APPLY
            and request.expected is not None
            and len(ids) != request.expected
        ):
            raise LeadError(
                f"{len(ids)} leads match now, not the {request.expected} previewed: preview again"
            )
    else:
        ids = list(dict.fromkeys(request.business_ids))
    roots: dict[str, Business] = {}
    skips: list[BulkSkip] = []
    for chunk in _chunks(ids):
        found = {
            row["id"]: row
            for row in conn.execute(
                "SELECT id, coalesce(merged_into_id, id) AS root, display_name, deleted_at "  # noqa: S608
                f"FROM businesses WHERE id IN ({_marks(chunk)})",
                list(chunk),
            )
        }
        for business_id in chunk:
            row = found.get(business_id)
            if row is None or row["deleted_at"] is not None:
                skips.append(
                    BulkSkip(business_id=business_id, display_name=business_id, reason="not_found")
                )
            elif row["root"] not in roots:
                roots[row["root"]] = repository.get(conn, Business, row["root"])
    changes: list[Business] = []
    suppressed_sql = explorer.suppressed_sql("b.id")
    for chunk in _chunks(list(roots)):
        marks = _marks(chunk)
        state = {
            row["id"]: row
            for row in conn.execute(
                f"SELECT b.id, lr.status, lr.assignee, {suppressed_sql} AS suppressed "  # noqa: S608
                "FROM businesses b LEFT JOIN lead_research lr ON lr.business_id = b.id "
                f"WHERE b.id IN ({marks})",
                list(chunk),
            )
        }
        tagged: set[str] = set()
        if request.tag is not None:
            tagged = {
                row["entity_id"]
                for row in conn.execute(
                    "SELECT et.entity_id FROM entity_tags et JOIN tags t ON t.id = et.tag_id "  # noqa: S608
                    f"WHERE et.entity_type = 'business' AND et.entity_id IN ({marks}) "
                    "AND t.deleted_at IS NULL AND lower(t.name) = lower(?)",
                    [*chunk, request.tag.strip()],
                )
            }
        for root in chunk:
            business, row = roots[root], state[root]
            action = request.action
            unchanged = (
                (action is BulkAction.ADD_TAG and root in tagged)
                or (action is BulkAction.REMOVE_TAG and root not in tagged)
                or (
                    action is BulkAction.ASSIGN
                    and row["assignee"] == (request.assignee or "").strip()
                )
                or (action is BulkAction.UNASSIGN and row["assignee"] is None)
                or (
                    action is BulkAction.NEEDS_RESEARCH
                    and row["status"] == ResearchStatus.NEEDS_RESEARCH.value
                )
                or (
                    action is BulkAction.DISQUALIFY
                    and row["status"] == ResearchStatus.DISQUALIFIED.value
                )
            )
            if unchanged:
                skips.append(
                    BulkSkip(
                        business_id=root, display_name=business.display_name, reason="unchanged"
                    )
                )
            elif action is BulkAction.NEEDS_RESEARCH and row["suppressed"]:
                skips.append(
                    BulkSkip(
                        business_id=root, display_name=business.display_name, reason="suppressed"
                    )
                )
            else:
                changes.append(business)
    batch_id = new_id() if request.mode is BulkMode.APPLY and changes else None
    if batch_id is not None:
        reason = str(request.reason)
        for batch in _chunks(changes):
            with repository.atomic(conn):
                for business in batch:
                    if request.action in (BulkAction.ADD_TAG, BulkAction.REMOVE_TAG):
                        _write_tag(
                            conn, business, str(request.tag), request.action is BulkAction.ADD_TAG,
                            now, actor=actor, batch_id=batch_id,
                        )  # fmt: skip
                    elif request.action in (BulkAction.ASSIGN, BulkAction.UNASSIGN):
                        assignee = (request.assignee or "").strip() or None
                        _write_assignee(
                            conn, business,
                            assignee if request.action is BulkAction.ASSIGN else None,
                            reason, now, actor=actor, batch_id=batch_id,
                        )  # fmt: skip
                    else:
                        status = (
                            ResearchStatus.NEEDS_RESEARCH
                            if request.action is BulkAction.NEEDS_RESEARCH
                            else ResearchStatus.DISQUALIFIED
                        )
                        _write_status(
                            conn, business, status, reason, now, actor=actor, batch_id=batch_id
                        )
    return BulkResult(
        mode=request.mode,
        action=request.action,
        targeted=len(ids),
        changed=len(changes),
        skipped=len(skips),
        skips=skips[:MAX_SKIPS],
        batch_id=batch_id,
    )


# ---- timeline -----------------------------------------------------------------------------------

_TIMELINE_TAG: Final = "timeline"
_BLANK_ENTRY: Final = dict.fromkeys(
    ("detail", "actor", "ref", "job_id", "score_run_id", "source_key")
)


def _entry(kind: TimelineKind, **fields: object) -> TimelineEntry:
    return TimelineEntry.model_validate({**_BLANK_ENTRY, "kind": kind, **fields})


def _timeline_cursor(entry: TimelineEntry) -> str:
    """A key is `<table>:<id>`: the id travels as the cursor's id, the time and the table as its
    key."""
    table, _, row_id = entry.key.partition(":")
    return repository.encode_cursor(_TIMELINE_TAG, f"{format_ts(entry.at)}|{table}", row_id)


def _timeline_bound(cursor: str) -> tuple[str, str]:
    key, row_id = repository.decode_cursor(cursor, _TIMELINE_TAG)
    stamp, _, table = key.partition("|")
    return stamp, f"{table}:{row_id}"


def _timeline_sources(
    conn: sqlite3.Connection, members: Sequence[str], bound: tuple[str, str] | None, limit: int
) -> list[TimelineEntry]:
    marks = _marks(members)
    tables = (
        "business_names",
        "business_locations",
        "websites",
        "contact_points",
        "evidence_items",
    )
    extraction_ids = " UNION ".join(
        f"SELECT extraction_id FROM {table} WHERE business_id IN ({marks})"  # noqa: S608
        for table in tables
    )
    where, params = "", list(members) * len(tables)
    if bound is not None:
        where = "WHERE (r.acquired_at, 'raw_record:' || r.id) < (?, ?)"
        params += bound
    rows = conn.execute(
        "SELECT DISTINCT r.id, r.acquired_at, r.source_url, sc.name, sc.key, sr.job_id "  # noqa: S608
        "FROM raw_records r JOIN source_connectors sc ON sc.id = r.connector_id "
        "JOIN source_runs sr ON sr.id = r.source_run_id "
        f"JOIN extractions x ON x.raw_record_id = r.id AND x.id IN ({extraction_ids}) {where} "
        "ORDER BY r.acquired_at DESC, r.id DESC LIMIT ?",
        [*params, limit],
    ).fetchall()
    return [
        _entry(
            TimelineKind.SOURCE,
            key=f"raw_record:{row['id']}",
            at=row["acquired_at"],
            code="acquired",
            title=f"Acquired from {row['name']}"[:300],
            detail=str(row["source_url"])[:600] if row["source_url"] else None,
            job_id=row["job_id"],
            source_key=row["key"],
        )
        for row in rows
    ]


def _entries(
    conn: sqlite3.Connection,
    sql: str,
    params: Sequence[object],
    bound: tuple[str, str] | None,
    limit: int,
    build: Callable[[sqlite3.Row], TimelineEntry],
) -> list[TimelineEntry]:
    """Runs one timeline source: `sql` selects `at` and `key` (plus its own columns)."""
    where, bound_params = "", list[object]()
    if bound is not None:
        where, bound_params = "WHERE (e.at, e.key) < (?, ?)", list(bound)
    rows = conn.execute(
        f"SELECT * FROM ({sql}) e {where} ORDER BY e.at DESC, e.key DESC LIMIT ?",  # noqa: S608
        [*params, *bound_params, limit],
    ).fetchall()
    return [build(row) for row in rows]


def timeline(conn: sqlite3.Connection, query: TimelineQuery) -> TimelinePage:
    """What happened to a lead, newest first: where its data came from, what changed, which analyses
    read it, what people decided, researched and recorded. Keyset pages over (time, key)."""
    business = common.business_of(conn, query.business_id)
    root, members = business.id, common.members_of(conn, business.id)
    marks = _marks(members)
    kinds = set(query.kinds) or set(TimelineKind)
    bound = _timeline_bound(query.cursor) if query.cursor else None
    limit = query.limit + 1
    entries: list[TimelineEntry] = []

    if TimelineKind.SOURCE in kinds:
        entries += _timeline_sources(conn, members, bound, limit)
    if TimelineKind.CHANGE in kinds:
        entries += _entries(
            conn,
            "SELECT occurred_at AS at, 'audit_log:' || id AS key, action, actor, detail_json "  # noqa: S608
            f"FROM audit_log WHERE entity_type = 'business' AND entity_id IN ({marks})",
            members,
            bound,
            limit,
            lambda row: _entry(
                TimelineKind.CHANGE,
                key=row["key"],
                at=row["at"],
                code=row["action"],
                title=_change_title(row["action"], row["detail_json"]),
                actor=row["actor"],
                detail=(row["detail_json"] or "")[:600] or None,
            ),
        )
    if TimelineKind.ANALYSIS in kinds:
        entries += _entries(
            conn,
            "SELECT s.created_at AS at, 'business_score:' || s.id AS key, s.score_run_id, s.tier, "  # noqa: S608
            f"s.total_score FROM business_scores s WHERE s.business_id IN ({marks})",
            members,
            bound,
            limit,
            lambda row: _entry(
                TimelineKind.ANALYSIS,
                key=row["key"],
                at=row["at"],
                code="scored",
                title=f"Scored {_text(row['tier'])}"
                + ("" if row["total_score"] is None else f" at {row['total_score']:.1f}"),
                score_run_id=row["score_run_id"],
                ref=BriefRef(
                    kind=EvidenceRefKind.SCORE_RUN,
                    ref=row["score_run_id"],
                    label="Score run",
                    url=None,
                ),
            ),
        )
        entries += _entries(
            conn,
            "SELECT assessed_at AS at, 'latino_assessment:' || id AS key, id, status, method "  # noqa: S608
            f"FROM latino_assessments WHERE business_id IN ({marks})",
            members,
            bound,
            limit,
            lambda row: _entry(
                TimelineKind.ANALYSIS,
                key=row["key"],
                at=row["at"],
                code="latino_assessed",
                title=f"Latino evidence: {_text(row['status'])} ({_text(row['method'])})",
                ref=BriefRef(
                    kind=EvidenceRefKind.LATINO_ASSESSMENT,
                    ref=row["id"],
                    label=row["status"],
                    url=None,
                ),
            ),
        )
        entries += _entries(
            conn,
            "SELECT coalesce(finished_at, started_at, created_at) AS at, "  # noqa: S608
            "'inference:' || id AS key, id, "
            f"task_type, status, job_id FROM ai_inference_runs WHERE business_id IN ({marks})",
            members,
            bound,
            limit,
            lambda row: _entry(
                TimelineKind.ANALYSIS,
                key=row["key"],
                at=row["at"],
                code="inference",
                title=f"Local AI {_text(row['task_type'])}: {_text(row['status'])}",
                job_id=row["job_id"],
                ref=BriefRef(
                    kind=EvidenceRefKind.INFERENCE_RUN,
                    ref=row["id"],
                    label=row["task_type"],
                    url=None,
                ),
            ),
        )
    if TimelineKind.REVIEW in kinds:
        entries += _entries(
            conn,
            "SELECT decided_at AS at, 'review_decision:' || id AS key, decision, previous_value, "  # noqa: S608
            f"new_value, reason, actor FROM review_decisions WHERE entity_type = 'business' "
            f"AND entity_id IN ({marks})",
            members,
            bound,
            limit,
            lambda row: _entry(
                TimelineKind.REVIEW,
                key=row["key"],
                at=row["at"],
                code=row["decision"][:64],
                title=f"{_text(row['decision']).capitalize()}: "
                f"{row['previous_value'] or '—'} → {row['new_value'] or '—'}"[:300],
                detail=str(row["reason"])[:600],
                actor=row["actor"],
            ),
        )
    if TimelineKind.RESEARCH in kinds:
        entries += _entries(
            conn,
            "SELECT created_at AS at, 'research_task:' || id AS key, title, status, kind, "
            "created_by "
            "FROM research_tasks WHERE business_id = ?",
            [root],
            bound,
            limit,
            lambda row: _entry(
                TimelineKind.RESEARCH,
                key=row["key"],
                at=row["at"],
                code=f"task_{row['kind']}",
                title=f"Task: {row['title']} ({row['status']})"[:300],
                actor=row["created_by"],
            ),
        )
        entries += _entries(
            conn,
            "SELECT pinned_at AS at, 'evidence_pin:' || id AS key, ref_kind, ref, label, "
            "pinned_by, "
            "unpinned_at FROM evidence_pins WHERE business_id = ?",
            [root],
            bound,
            limit,
            lambda row: _entry(
                TimelineKind.RESEARCH,
                key=row["key"],
                at=row["at"],
                code="pinned",
                title=f"Pinned {row['label']}"[:300]
                + (" (unpinned since)" if row["unpinned_at"] else ""),
                actor=row["pinned_by"],
                ref=BriefRef(
                    kind=EvidenceRefKind(row["ref_kind"]),
                    ref=row["ref"],
                    label=row["label"],
                    url=None,
                ),
            ),
        )
        entries += _entries(
            conn,
            "SELECT created_at AS at, 'note:' || id AS key, body, author, deleted_at FROM notes "
            "WHERE entity_type = 'business' AND entity_id = ?",
            [root],
            bound,
            limit,
            lambda row: _entry(
                TimelineKind.RESEARCH,
                key=row["key"],
                at=row["at"],
                code="note",
                title="Note" + (" (deleted)" if row["deleted_at"] else ""),
                detail=None if row["deleted_at"] else str(row["body"])[:600],
                actor=row["author"],
            ),
        )
        entries += _entries(
            conn,
            "SELECT decided_at AS at, 'research_check:' || id AS key, item_key, done, actor, note "
            "FROM research_checks WHERE business_id = ?",
            [root],
            bound,
            limit,
            lambda row: _entry(
                TimelineKind.RESEARCH,
                key=row["key"],
                at=row["at"],
                code="checklist",
                title=f"{'Checked' if row['done'] else 'Unchecked'} {_text(row['item_key'])}"[:300],
                detail=row["note"],
                actor=row["actor"],
            ),
        )
    if TimelineKind.OUTCOME in kinds:
        entries += _entries(
            conn,
            "SELECT occurred_at AS at, 'lead_outcome:' || id AS key, stage, previous_stage, "
            "attempt_result, channel, reason, actor, score_run_id FROM lead_outcomes "
            "WHERE business_id = ?",
            [root],
            bound,
            limit,
            lambda row: _entry(
                TimelineKind.OUTCOME,
                key=row["key"],
                at=row["at"],
                code="attempt" if row["stage"] == row["previous_stage"] else "outcome",
                title=(
                    f"Attempt by {_text(row['channel'] or 'unknown channel')}: "
                    f"{_text(row['attempt_result'] or 'no result')}"
                    if row["stage"] == row["previous_stage"]
                    else f"Stage {_text(row['previous_stage'])} → {_text(row['stage'])}"
                )[:300],
                detail=str(row["reason"])[:600],
                actor=row["actor"],
                score_run_id=row["score_run_id"],
            ),
        )
    entries.sort(key=lambda entry: (format_ts(entry.at), entry.key), reverse=True)
    page = entries[: query.limit]
    next_cursor: Cursor | None = _timeline_cursor(page[-1]) if len(entries) > query.limit else None
    return TimelinePage(business_id=root, entries=page, next_cursor=next_cursor)


def _change_title(action: str, detail_json: str | None) -> str:
    detail = orjson.loads(detail_json) if detail_json else {}
    if not isinstance(detail, dict):
        detail = {}
    field = detail.get("field")
    if action == "status_change":
        return f"{_text(str(field or 'status'))} {detail.get('from')} → {detail.get('to')}"[:300]
    if field:
        return f"{_text(str(field)).capitalize()} updated"[:300]
    return _text(action).capitalize()[:300]
