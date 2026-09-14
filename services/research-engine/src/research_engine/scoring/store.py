"""Persistence and reads of lead scoring (phase 11): ICP profiles and scoring models with their
versions, score runs, lead scores, reviews and outcomes, and the reads that rank, explain, compare
and calibrate them.

Versions, runs, scores and outcomes are history: never rewritten (triggers), never deleted. A
person's review of a score is a `review_decisions` row; an outcome moves the business's
`lead_stage` along its state machine and stays in `lead_outcomes`. Nothing here contacts anyone.
"""

import re
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from math import fsum
from typing import Any, Final

import orjson
from pydantic import JsonValue

from research_engine import jobs, repository
from research_engine.domain import (
    ICP_SCHEMA_VERSION,
    SCORING_SCHEMA_VERSION,
    AiDraftedReason,
    AuditAction,
    Business,
    BusinessFacts,
    BusinessScore,
    CalibrationQuery,
    CalibrationReport,
    ComponentScore,
    CriteriaProblem,
    EntityType,
    EventLevel,
    GateExample,
    GateOutcome,
    GatePreview,
    IcpDefinition,
    IcpProfile,
    IcpProfileVersion,
    IcpProfileView,
    IcpSaveRequest,
    Job,
    JobStatus,
    LatinoStatus,
    LeadAction,
    LeadActionRequest,
    LeadOutcome,
    LeadScorecard,
    LeadScorecardDetail,
    LeadScoreHistoryItem,
    LeadScoreItem,
    LeadScorePage,
    LeadScoreQuery,
    LeadStage,
    LeadTier,
    OpportunityReasoningOutput,
    ProblemSeverity,
    Project,
    RankChange,
    ReviewDecision,
    ScoreBin,
    ScorecardQuery,
    ScoreCompareQuery,
    ScoreComparison,
    ScoreEvidence,
    ScoreEvidenceKind,
    ScoreRun,
    ScoreRunItem,
    ScoreRunListQuery,
    ScoreRunPage,
    ScoreRunStats,
    ScoreVersions,
    ScoringCatalog,
    ScoringCatalogQuery,
    ScoringChangeAction,
    ScoringChangeRequest,
    ScoringDefinition,
    ScoringModel,
    ScoringModelSaveRequest,
    ScoringModelVersion,
    ScoringModelView,
    ScoringSimulation,
    ScoringSimulationRequest,
    ScoringStartRequest,
    ScoringTarget,
    format_ts,
    new_id,
    utc_now,
)
from research_engine.leads import index as lead_index
from research_engine.scoring import facts as fact_store
from research_engine.scoring import model
from research_engine.scoring.model import ScoringError

JOB_KIND: Final = "lead_scoring"
ACTOR: Final = "lead_scoring"
DEFAULT_MODEL_NAME: Final = "Lead Intelligence heuristic"
#: Simulating over facts read now reads every business at once; bigger projects use a stored run.
MAX_SIMULATED: Final = 5000
MAX_HISTORY: Final = 50
MAX_EXAMPLES: Final = 5
REVIEW_DECISIONS: Final = {
    LeadAction.CONFIRM: "lead_score_confirmed",
    LeadAction.DISMISS: "lead_score_dismissed",
    LeadAction.RESET: "lead_score_review_withdrawn",
}
#: The order of a run, equal to `model.rank_key`: tier, total (a missing one after), confidence, id.
RANK_ORDER: Final = (
    "CASE s.tier WHEN 'priority' THEN 0 WHEN 'review' THEN 1 WHEN 'low' THEN 2 "
    "WHEN 'insufficient_data' THEN 3 ELSE 4 END, s.total_score IS NULL, s.total_score DESC, "
    "s.confidence DESC, s.business_id"
)
_SCORED: Final = (LeadTier.PRIORITY, LeadTier.REVIEW, LeadTier.LOW)
_HAS_PROBLEM: Final = (
    "EXISTS (SELECT 1 FROM json_each(s.scorecard_json, '$.features') f "
    "WHERE json_extract(f.value, '$.problem') = ? AND json_extract(f.value, '$.state') = 'known' "
    "AND json_extract(f.value, '$.value') >= 0.5)"
)


def _ranked(columns: str) -> str:
    """Every score of one run (the first parameter) with its position in the run."""
    return (
        f"SELECT {columns}, ROW_NUMBER() OVER (ORDER BY {RANK_ORDER}) AS rank "  # noqa: S608
        "FROM business_scores s WHERE s.score_run_id = ?"
    )


def _marks(items: Sequence[object]) -> str:
    return ", ".join("?" * len(items))


# ---- profiles and models -------------------------------------------------------------------------


def ensure_default_model(conn: sqlite3.Connection) -> None:
    """The declared heuristic model, created once so a first run has a formula to use."""
    find = "SELECT 1 FROM scoring_models WHERE key = ?"
    if conn.execute(find, (model.DEFAULT_MODEL_KEY,)).fetchone():
        return
    with repository.atomic(conn):
        if conn.execute(find, (model.DEFAULT_MODEL_KEY,)).fetchone():
            return
        now = utc_now()
        record = ScoringModel(
            id=new_id(),
            key=model.DEFAULT_MODEL_KEY,
            name=DEFAULT_MODEL_NAME,
            description="Heuristic weights declared in phase 11, not fitted to outcomes.",
            created_at=now,
            updated_at=now,
            deleted_at=None,
            deleted_reason=None,
            active_version_id=None,
            archived_at=None,
        )
        version = ScoringModelVersion(
            id=new_id(),
            scoring_model_id=record.id,
            version=1,
            definition_json=model.default_scoring().model_dump(mode="json"),
            schema_version=SCORING_SCHEMA_VERSION,
            is_heuristic=True,
            created_by=ACTOR,
            created_at=now,
        )
        repository.insert(conn, record, version)
        conn.execute(
            "UPDATE scoring_models SET active_version_id = ? WHERE id = ?", (version.id, record.id)
        )
        repository.audit(conn, AuditAction.CREATE, EntityType.SCORING_MODEL, record.id, actor=ACTOR)


def _versions[V: (IcpProfileVersion, ScoringModelVersion)](
    conn: sqlite3.Connection, record: type[V], column: str, owner: str
) -> list[V]:
    return [
        repository.from_row(record, row)
        for row in conn.execute(
            f"SELECT {repository._columns(record)} FROM {record.TABLE} "  # noqa: S608
            f"WHERE {column} = ? ORDER BY version DESC",
            (owner,),
        )
    ]


def icp_view(conn: sqlite3.Connection, profile_id: str) -> IcpProfileView:
    profile = repository.get(conn, IcpProfile, profile_id)
    if profile.deleted_at is not None:
        raise repository.NotFoundError(f"ICP profile {profile_id} has been deleted")
    versions = _versions(conn, IcpProfileVersion, "icp_profile_id", profile_id)
    problems = (
        model.check_icp(IcpDefinition.model_validate(versions[0].definition_json))
        if versions
        else []
    )
    project = repository.get(conn, Project, profile.project_id)
    return IcpProfileView(
        profile=profile, versions=versions, problems=problems, is_synthetic=project.is_synthetic
    )


def model_view(conn: sqlite3.Connection, model_id: str) -> ScoringModelView:
    record = repository.get(conn, ScoringModel, model_id)
    if record.deleted_at is not None:
        raise repository.NotFoundError(f"scoring model {model_id} has been deleted")
    versions = _versions(conn, ScoringModelVersion, "scoring_model_id", model_id)
    problems = (
        model.check_scoring(ScoringDefinition.model_validate(versions[0].definition_json))
        if versions
        else []
    )
    return ScoringModelView(model=record, versions=versions, problems=problems)


def catalog(conn: sqlite3.Connection, query: ScoringCatalogQuery) -> ScoringCatalog:
    ensure_default_model(conn)
    where, params = "deleted_at IS NULL", list[object]()
    if query.project_id is not None:
        where += " AND project_id = ?"
        params.append(query.project_id)
    profiles = [
        icp_view(conn, row["id"])
        for row in conn.execute(
            f"SELECT id FROM icp_profiles WHERE {where} "  # noqa: S608
            "ORDER BY archived_at IS NOT NULL, name, id",
            params,
        )
    ]
    models = [
        model_view(conn, row["id"])
        for row in conn.execute(
            "SELECT id FROM scoring_models WHERE deleted_at IS NULL "
            "ORDER BY archived_at IS NOT NULL, key <> ?, name, id",
            (model.DEFAULT_MODEL_KEY,),
        )
    ]
    return ScoringCatalog(
        icp_profiles=profiles,
        models=models,
        features=model.feature_catalog(),
        engine_version=model.ENGINE_VERSION,
        icp_template=model.icp_template(),
        scoring_template=model.default_scoring(),
    )


def _unique_name_error(exc: sqlite3.IntegrityError, name: str) -> ScoringError:
    if "icp_profiles.name" in str(exc):
        return ScoringError(f"an ICP profile named '{name}' already exists in this project")
    raise exc


def save_icp(conn: sqlite3.Connection, request: IcpSaveRequest, *, actor: str) -> str:
    """Creates a profile, or a new version of one. An unchanged definition keeps the latest."""
    definition = request.definition.model_dump(mode="json")
    name, now = request.name.strip(), utc_now()
    try:
        with repository.atomic(conn):
            if request.profile_id is None:
                project = repository.get(
                    conn,
                    Project,
                    request.project_id or repository.ensure_default_project(conn, actor=actor).id,
                )
                if project.deleted_at is not None:
                    raise repository.NotFoundError(f"project {project.id} has been deleted")
                profile = IcpProfile(
                    id=new_id(),
                    project_id=project.id,
                    name=name,
                    description=request.description,
                    created_at=now,
                    updated_at=now,
                    deleted_at=None,
                    deleted_reason=None,
                    active_version_id=None,
                    archived_at=None,
                )
                repository.insert(conn, profile)
                repository.audit(
                    conn, AuditAction.CREATE, EntityType.ICP_PROFILE, profile.id, actor=actor
                )
                number = 1
            else:
                profile = repository.get(conn, IcpProfile, request.profile_id)
                if profile.deleted_at is not None:
                    raise repository.NotFoundError(f"ICP profile {profile.id} has been deleted")
                if profile.archived_at is not None:
                    raise ScoringError("restore this profile before saving a new version")
                if (profile.name, profile.description) != (name, request.description):
                    conn.execute(
                        "UPDATE icp_profiles SET name = ?, description = ?, updated_at = ? "
                        "WHERE id = ?",
                        (name, request.description, format_ts(now), profile.id),
                    )
                latest = _versions(conn, IcpProfileVersion, "icp_profile_id", profile.id)[:1]
                if latest and latest[0].definition_json == definition:
                    return profile.id
                number = latest[0].version + 1 if latest else 1
            version = IcpProfileVersion(
                id=new_id(),
                icp_profile_id=profile.id,
                version=number,
                definition_json=definition,
                schema_version=ICP_SCHEMA_VERSION,
                created_by=actor,
                created_at=now,
            )
            repository.insert(conn, version)
            repository.audit(
                conn,
                AuditAction.UPDATE,
                EntityType.ICP_PROFILE,
                profile.id,
                actor=actor,
                detail={"version": number, "version_id": version.id},
            )
    except sqlite3.IntegrityError as exc:
        raise _unique_name_error(exc, name) from exc
    return profile.id


def _model_key(conn: sqlite3.Connection, name: str) -> str:
    slug = re.sub(r"[^a-z0-9_]", "", "_".join(model.words(name).split())) or "model"
    base = f"custom.{slug}"[:56]
    key, suffix = base, 1
    while conn.execute("SELECT 1 FROM scoring_models WHERE key = ?", (key,)).fetchone():
        suffix += 1
        key = f"{base}_{suffix}"
    return key


def save_model(conn: sqlite3.Connection, request: ScoringModelSaveRequest, *, actor: str) -> str:
    """Creates a model, or a new version of one. Every version is declared heuristic: no weight
    is fitted to outcomes (docs/scoring.md)."""
    definition = request.definition.model_dump(mode="json")
    name, now = request.name.strip(), utc_now()
    with repository.atomic(conn):
        if request.model_id is None:
            record = ScoringModel(
                id=new_id(),
                key=_model_key(conn, name),
                name=name,
                description=request.description,
                created_at=now,
                updated_at=now,
                deleted_at=None,
                deleted_reason=None,
                active_version_id=None,
                archived_at=None,
            )
            repository.insert(conn, record)
            repository.audit(
                conn, AuditAction.CREATE, EntityType.SCORING_MODEL, record.id, actor=actor
            )
            number = 1
        else:
            record = repository.get(conn, ScoringModel, request.model_id)
            if record.deleted_at is not None:
                raise repository.NotFoundError(f"scoring model {record.id} has been deleted")
            if record.archived_at is not None:
                raise ScoringError("restore this model before saving a new version")
            if (record.name, record.description) != (name, request.description):
                conn.execute(
                    "UPDATE scoring_models SET name = ?, description = ?, updated_at = ? "
                    "WHERE id = ?",
                    (name, request.description, format_ts(now), record.id),
                )
            latest = _versions(conn, ScoringModelVersion, "scoring_model_id", record.id)[:1]
            if latest and latest[0].definition_json == definition:
                return record.id
            number = latest[0].version + 1 if latest else 1
        version = ScoringModelVersion(
            id=new_id(),
            scoring_model_id=record.id,
            version=number,
            definition_json=definition,
            schema_version=SCORING_SCHEMA_VERSION,
            is_heuristic=True,
            created_by=actor,
            created_at=now,
        )
        repository.insert(conn, version)
        repository.audit(
            conn,
            AuditAction.UPDATE,
            EntityType.SCORING_MODEL,
            record.id,
            actor=actor,
            detail={"version": number, "version_id": version.id},
        )
    return record.id


def _errors(problems: Iterable[CriteriaProblem]) -> list[str]:
    return [problem.code for problem in problems if problem.severity is ProblemSeverity.ERROR]


def change(conn: sqlite3.Connection, request: ScoringChangeRequest, *, actor: str) -> None:
    """Activates a version, or archives or restores a profile or model, with an audited reason."""
    icp = request.target is ScoringTarget.ICP_PROFILE
    record: type[IcpProfile] | type[ScoringModel] = IcpProfile if icp else ScoringModel
    entity = EntityType.ICP_PROFILE if icp else EntityType.SCORING_MODEL
    now = format_ts(utc_now())
    with repository.atomic(conn):
        row = repository.get(conn, record, request.id)
        if row.deleted_at is not None:
            raise repository.NotFoundError(f"{record.TABLE} {row.id} has been deleted")
        detail: dict[str, JsonValue] = {"action": request.action.value, "reason": request.reason}
        match request.action:
            case ScoringChangeAction.ACTIVATE:
                if request.version_id is None:
                    raise ScoringError("choose the version to activate")
                if row.archived_at is not None:
                    raise ScoringError("restore it before activating a version")
                if icp:
                    version = repository.get(conn, IcpProfileVersion, request.version_id)
                    owner = version.icp_profile_id
                    errors = _errors(
                        model.check_icp(IcpDefinition.model_validate(version.definition_json))
                    )
                else:
                    other = repository.get(conn, ScoringModelVersion, request.version_id)
                    owner = other.scoring_model_id
                    errors = _errors(
                        model.check_scoring(ScoringDefinition.model_validate(other.definition_json))
                    )
                if owner != row.id:
                    raise ScoringError("that version belongs to another profile or model")
                if errors:
                    raise ScoringError(f"this version cannot be activated: {', '.join(errors)}")
                conn.execute(
                    f"UPDATE {record.TABLE} SET active_version_id = ?, updated_at = ? "  # noqa: S608
                    "WHERE id = ?",
                    (request.version_id, now, row.id),
                )
                detail["version_id"] = request.version_id
            case ScoringChangeAction.ARCHIVE if row.archived_at is None:
                conn.execute(
                    f"UPDATE {record.TABLE} SET archived_at = ?, updated_at = ? WHERE id = ?",  # noqa: S608
                    (now, now, row.id),
                )
            case ScoringChangeAction.RESTORE if row.archived_at is not None:
                conn.execute(
                    f"UPDATE {record.TABLE} SET archived_at = NULL, updated_at = ? WHERE id = ?",  # noqa: S608
                    (now, row.id),
                )
        repository.audit(conn, AuditAction.UPDATE, entity, row.id, actor=actor, detail=detail)


# ---- simulation ----------------------------------------------------------------------------------


def stored_facts(
    conn: sqlite3.Connection, score_run_id: str, business_ids: Sequence[str] | None = None
) -> list[BusinessFacts]:
    """The facts a run stored, by business id (all of them, or these)."""
    if business_ids is not None and not business_ids:
        return []
    where, params = "score_run_id = ?", list[object]([score_run_id])
    if business_ids is not None:
        where += f" AND business_id IN ({_marks(business_ids)})"
        params += list(business_ids)
    return [
        BusinessFacts.model_validate(orjson.loads(row["facts_json"]))
        for row in conn.execute(
            f"SELECT facts_json FROM business_scores WHERE {where} ORDER BY business_id",  # noqa: S608
            params,
        )
    ]


def _item(
    facts: Mapping[str, Any], card: Mapping[str, Any], rank: int, stage: str
) -> LeadScoreItem:
    """A ranked row from the stored JSON of its facts and scorecard (no model rebuilt)."""
    places = facts.get("locations") or [{}]
    categories = facts.get("categories") or [None]
    latino = facts.get("latino") or {}
    reasons = card.get("reasons") or [{}]
    offer = card.get("offer") or {}
    return LeadScoreItem.model_validate(
        {
            "business_id": facts["business_id"],
            "project_id": facts["project_id"],
            "display_name": facts["display_name"],
            "locality": places[0].get("locality"),
            "region": places[0].get("region"),
            "primary_category": categories[0],
            "lead_stage": stage,
            "latino_status": latino.get("status", LatinoStatus.UNKNOWN.value),
            "rank": rank,
            "tier": card["tier"],
            "total_score": card["total_score"],
            "confidence": card["confidence"],
            "components": [
                {"component": c["component"], "value": c["value"]} for c in card["components"]
            ],
            "top_reason": reasons[0].get("text"),
            "offer_key": offer.get("offer_key"),
            "problems": [
                f["problem"]
                for f in card["features"]
                if f.get("problem") and f.get("state") == "known" and (f.get("value") or 0) >= 0.5
            ],
            "is_synthetic": facts["is_synthetic"],
        }
    )


def _stages(conn: sqlite3.Connection, business_ids: Sequence[str]) -> dict[str, str]:
    if not business_ids:
        return {}
    return {
        row["id"]: row["lead_stage"]
        for row in conn.execute(
            f"SELECT id, lead_stage FROM businesses WHERE id IN ({_marks(business_ids)})",  # noqa: S608
            list(business_ids),
        )
    }


def simulate(
    conn: sqlite3.Connection, request: ScoringSimulationRequest, now: datetime
) -> ScoringSimulation:
    """Scores drafts in memory. Writes nothing: not a run, not a score, not a lead."""
    problems = [*model.check_icp(request.icp), *model.check_scoring(request.scoring)]
    if request.facts_from_run_id is not None:
        found = stored_facts(conn, repository.get(conn, ScoreRun, request.facts_from_run_id).id)
    elif request.project_id is not None:
        roots = fact_store.root_ids(conn, request.project_id)
        if len(roots) > MAX_SIMULATED:
            raise ScoringError(
                f"the project has more than {MAX_SIMULATED} businesses: run a score job and "
                "simulate over its stored facts"
            )
        audits = fact_store.audits_by_root(conn, request.project_id)
        found = fact_store.collect(conn, roots, audits, now)
    else:
        raise ScoringError("choose a project or a stored run to simulate over")
    versions = ScoreVersions(
        engine_version=model.ENGINE_VERSION,
        facts_version=model.FACTS_VERSION,
        scoring_model_key="draft",
        scoring_model_version=1,
        is_heuristic=True,
        icp_profile_name="Draft",
        icp_profile_version=1,
    )
    scored = [
        (facts, model.score(facts, request.icp, request.scoring, versions)) for facts in found
    ]
    scored.sort(
        key=lambda p: model.rank_key(p[1].tier, p[1].total_score, p[1].confidence, p[0].business_id)
    )
    counts: dict[str, dict[GateOutcome, int]] = {}
    examples: dict[str, list[GateExample]] = {}
    for facts, card in scored:
        for gate in card.gates:
            counts.setdefault(gate.gate, dict.fromkeys(GateOutcome, 0))[gate.outcome] += 1
            shown = examples.setdefault(gate.gate, [])
            if gate.outcome is GateOutcome.FAIL and len(shown) < MAX_EXAMPLES:
                shown.append(
                    GateExample(
                        business_id=facts.business_id,
                        display_name=facts.display_name,
                        detail=gate.detail,
                    )
                )
    top = scored[: request.top]
    stages = _stages(conn, [facts.business_id for facts, _ in top])
    return ScoringSimulation(
        businesses=len(scored),
        tiers=model.tier_counts(card.tier for _, card in scored),
        distribution=model.distribution(
            card.total_score for _, card in scored if card.tier in _SCORED
        ),
        gates=[
            GatePreview(
                gate=gate,
                passed=c[GateOutcome.PASS],
                failed=c[GateOutcome.FAIL],
                unknown=c[GateOutcome.UNKNOWN],
                examples=examples[gate],
            )
            for gate, c in counts.items()
        ],
        top=[
            _item(
                facts.model_dump(mode="json"),
                card.model_dump(mode="json"),
                rank,
                stages.get(facts.business_id, LeadStage.NOT_CONTACTED.value),
            )
            for rank, (facts, card) in enumerate(top, start=1)
        ],
        facts_from_run_id=request.facts_from_run_id,
        collected_at=now,
        problems=problems,
    )


# ---- runs ----------------------------------------------------------------------------------------


def plan_run(
    conn: sqlite3.Connection, request: ScoringStartRequest, *, actor: str
) -> tuple[list[str], list[str]]:
    """Creates the job and its score run. Refuses drafts with errors and a second open run."""
    project = repository.get(conn, Project, request.project_id)
    if project.deleted_at is not None:
        raise repository.NotFoundError(f"project {project.id} has been deleted")
    icp_version = repository.get(conn, IcpProfileVersion, request.icp_profile_version_id)
    profile = repository.get(conn, IcpProfile, icp_version.icp_profile_id)
    if profile.project_id != project.id:
        raise ScoringError("that ICP profile belongs to another project")
    model_version = repository.get(conn, ScoringModelVersion, request.scoring_model_version_id)
    scoring_model = repository.get(conn, ScoringModel, model_version.scoring_model_id)
    for label, row in (("ICP profile", profile), ("scoring model", scoring_model)):
        if row.deleted_at is not None or row.archived_at is not None:
            raise ScoringError(f"that {label} is archived or deleted: restore it first")
    errors = [
        *_errors(model.check_icp(IcpDefinition.model_validate(icp_version.definition_json))),
        *_errors(
            model.check_scoring(ScoringDefinition.model_validate(model_version.definition_json))
        ),
    ]
    if errors:
        raise ScoringError(f"these versions cannot run: {', '.join(errors)}")
    if request.facts_from_run_id is not None:
        source = repository.get(conn, ScoreRun, request.facts_from_run_id)
        if source.project_id != project.id or source.finished_at is None:
            raise ScoringError("scores can only reuse the facts of a finished run of this project")
    now = utc_now()
    with repository.atomic(conn):
        if conn.execute(
            "SELECT 1 FROM jobs WHERE kind = ? AND project_id = ? "
            "AND status IN ('queued', 'running', 'paused')",
            (JOB_KIND, project.id),
        ).fetchone():
            raise ScoringError(
                "a scoring run is already open for this project: resume or cancel it"
            )
        run_id = new_id()
        job = Job(
            id=new_id(),
            project_id=project.id,
            kind=JOB_KIND,
            status=JobStatus.QUEUED,
            priority=0,
            params_json={"project_id": project.id, "score_run_id": run_id},
            idempotency_key=None,
            progress_done=0,
            progress_total=None,
            requested_at=now,
            started_at=None,
            finished_at=None,
            created_at=now,
            updated_at=now,
        )
        run = ScoreRun(
            id=run_id,
            project_id=project.id,
            job_id=job.id,
            scoring_model_version_id=model_version.id,
            icp_profile_version_id=icp_version.id,
            facts_from_run_id=request.facts_from_run_id,
            engine_version=model.ENGINE_VERSION,
            started_at=now,
            finished_at=None,
            created_at=now,
        )
        repository.insert(conn, job, run)
        repository.audit(conn, AuditAction.CREATE, EntityType.JOB, job.id, actor=actor)
        repository.audit(conn, AuditAction.CREATE, EntityType.SCORE_RUN, run.id, actor=actor)
        jobs.write_event(
            conn,
            job.id,
            None,
            "run_queued",
            EventLevel.INFO,
            {"project_id": project.id, "score_run_id": run.id},
        )
    return [job.id], []


def run_of_job(conn: sqlite3.Connection, job_id: str) -> ScoreRun:
    row = conn.execute(
        f"SELECT {repository._columns(ScoreRun)} FROM score_runs WHERE job_id = ?",  # noqa: S608
        (job_id,),
    ).fetchone()
    if row is None:
        raise repository.NotFoundError(f"job {job_id} has no score run")
    return repository.from_row(ScoreRun, row)


def definitions(
    conn: sqlite3.Connection, run: ScoreRun
) -> tuple[IcpDefinition, ScoringDefinition, ScoreVersions]:
    icp_version = repository.get(conn, IcpProfileVersion, run.icp_profile_version_id)
    profile = repository.get(conn, IcpProfile, icp_version.icp_profile_id)
    model_version = repository.get(conn, ScoringModelVersion, run.scoring_model_version_id)
    scoring_model = repository.get(conn, ScoringModel, model_version.scoring_model_id)
    versions = ScoreVersions(
        engine_version=run.engine_version,
        facts_version=model.FACTS_VERSION,
        scoring_model_key=scoring_model.key,
        scoring_model_version=model_version.version,
        is_heuristic=model_version.is_heuristic,
        icp_profile_name=profile.name,
        icp_profile_version=icp_version.version,
    )
    return (
        IcpDefinition.model_validate(icp_version.definition_json),
        ScoringDefinition.model_validate(model_version.definition_json),
        versions,
    )


def scored_ids(conn: sqlite3.Connection, score_run_id: str) -> list[str]:
    return [
        row["business_id"]
        for row in conn.execute(
            "SELECT business_id FROM business_scores WHERE score_run_id = ? ORDER BY business_id",
            (score_run_id,),
        )
    ]


def write_scores(
    conn: sqlite3.Connection,
    run: ScoreRun,
    found: Iterable[BusinessFacts],
    icp: IcpDefinition,
    scoring: ScoringDefinition,
    versions: ScoreVersions,
    now: datetime,
) -> int:
    """Scores and stores a batch in one transaction; a business already scored is skipped."""
    written = 0
    with repository.atomic(conn):
        for facts in found:
            if conn.execute(
                "SELECT 1 FROM business_scores WHERE score_run_id = ? AND business_id = ?",
                (run.id, facts.business_id),
            ).fetchone():
                continue
            card = model.score(facts, icp, scoring, versions)
            score = BusinessScore(
                id=new_id(),
                score_run_id=run.id,
                business_id=facts.business_id,
                tier=card.tier,
                total_score=card.total_score,
                confidence=card.confidence,
                facts_collected_at=facts.collected_at,
                facts_json=facts.model_dump(mode="json"),
                scorecard_json=card.model_dump(mode="json"),
                created_at=now,
            )
            repository.insert(conn, score)
            repository.insert(
                conn,
                *(
                    ComponentScore(
                        id=new_id(),
                        business_score_id=score.id,
                        component=component.component.value,
                        value=component.value,
                        weight=component.weight,
                        contribution=component.contribution,
                        coverage=component.coverage,
                    )
                    for component in card.components
                ),
            )
            repository.insert(conn, lead_index.project(run, facts, card))
            written += 1
    return written


def finish_run(conn: sqlite3.Connection, score_run_id: str, now: datetime) -> None:
    with repository.atomic(conn):
        conn.execute(
            "UPDATE score_runs SET finished_at = ? WHERE id = ? AND finished_at IS NULL",
            (format_ts(now), score_run_id),
        )
        lead_index.rank_run(conn, score_run_id)


def tier_stats(conn: sqlite3.Connection, score_run_id: str) -> dict[str, int]:
    counts = {
        row["tier"]: row["n"]
        for row in conn.execute(
            "SELECT tier, count(*) AS n FROM business_scores WHERE score_run_id = ? GROUP BY tier",
            (score_run_id,),
        )
    }
    return {
        "businesses": sum(counts.values()),
        **{t.value: counts.get(t.value, 0) for t in LeadTier},
    }


def _run_item(conn: sqlite3.Connection, job: Job) -> ScoreRunItem:
    run = run_of_job(conn, job.id)
    project = repository.get(conn, Project, run.project_id)
    icp_version = repository.get(conn, IcpProfileVersion, run.icp_profile_version_id)
    profile = repository.get(conn, IcpProfile, icp_version.icp_profile_id)
    model_version = repository.get(conn, ScoringModelVersion, run.scoring_model_version_id)
    scoring_model = repository.get(conn, ScoringModel, model_version.scoring_model_id)
    bins = {
        row["bin"]: row["n"]
        for row in conn.execute(
            "SELECT min(9, CAST(total_score / 10 AS INTEGER)) AS bin, count(*) AS n "
            "FROM business_scores WHERE score_run_id = ? AND tier IN ('priority', 'review', 'low') "
            "GROUP BY bin",
            (run.id,),
        )
    }
    stage = jobs.latest_state(conn, job.id).get("stage")
    return ScoreRunItem(
        job_id=job.id,
        score_run_id=run.id,
        project_id=project.id,
        project_name=project.name,
        status=job.status,
        stage=stage if isinstance(stage, str) and stage else "queued",
        progress_done=job.progress_done,
        progress_total=job.progress_total,
        scoring_model_id=scoring_model.id,
        scoring_model_key=scoring_model.key,
        scoring_model_name=scoring_model.name,
        scoring_model_version=model_version.version,
        is_heuristic=model_version.is_heuristic,
        icp_profile_id=profile.id,
        icp_profile_name=profile.name,
        icp_profile_version=icp_version.version,
        engine_version=run.engine_version,
        facts_from_run_id=run.facts_from_run_id,
        stats=ScoreRunStats.model_validate(tier_stats(conn, run.id)),
        distribution=[ScoreBin(low=10 * i, businesses=bins.get(i, 0)) for i in range(10)],
        requested_at=job.requested_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        events=repository._run_events(conn, job.id),
        is_synthetic=project.is_synthetic,
    )


def get_run(conn: sqlite3.Connection, job_id: str) -> ScoreRunItem:
    return _run_item(conn, jobs.live_job(conn, job_id, JOB_KIND))


def run_item(conn: sqlite3.Connection, score_run_id: str) -> ScoreRunItem:
    run = repository.get(conn, ScoreRun, score_run_id)
    if run.job_id is None:
        raise repository.NotFoundError(f"score run {score_run_id} has no job")
    return get_run(conn, run.job_id)


def runs_by_id(conn: sqlite3.Connection, job_ids: Sequence[str]) -> ScoreRunPage:
    items = [get_run(conn, job_id) for job_id in job_ids]
    return ScoreRunPage(items=items, next_cursor=None, total=len(items))


def list_runs(conn: sqlite3.Connection, query: ScoreRunListQuery) -> ScoreRunPage:
    where, params = ["j.kind = ?"], list[object]([JOB_KIND])
    if query.project_id is not None:
        where.append("j.project_id = ?")
        params.append(query.project_id)
    (total,) = conn.execute(
        f"SELECT count(*) FROM jobs j WHERE {' AND '.join(where)}",  # noqa: S608
        params,
    ).fetchone()
    if query.cursor is not None:
        last_key, last_id = repository.decode_cursor(query.cursor, JOB_KIND)
        where.append("(j.requested_at, j.id) < (?, ?)")
        params += [last_key, last_id]
    rows = conn.execute(
        f"SELECT {repository._columns(Job, 'j')} FROM jobs j "  # noqa: S608
        f"WHERE {' AND '.join(where)} ORDER BY j.requested_at DESC, j.id DESC LIMIT ?",
        [*params, query.limit + 1],
    ).fetchall()
    page = rows[: query.limit]
    next_cursor = (
        repository.encode_cursor(JOB_KIND, page[-1]["requested_at"], page[-1]["id"])
        if len(rows) > query.limit
        else None
    )
    return ScoreRunPage(
        items=[_run_item(conn, repository.from_row(Job, row)) for row in page],
        next_cursor=next_cursor,
        total=total,
    )


# ---- leads ---------------------------------------------------------------------------------------


def _latest_run(conn: sqlite3.Connection, business_id: str | None = None) -> str | None:
    if business_id is None:
        row = conn.execute(
            "SELECT id FROM score_runs WHERE finished_at IS NOT NULL "
            "ORDER BY finished_at DESC, id DESC LIMIT 1"
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT r.id FROM score_runs r JOIN business_scores s ON s.score_run_id = r.id "
            "WHERE s.business_id = ? AND r.finished_at IS NOT NULL "
            "ORDER BY r.finished_at DESC, r.id DESC LIMIT 1",
            (business_id,),
        ).fetchone()
    return row["id"] if row else None


def list_leads(conn: sqlite3.Connection, query: LeadScoreQuery) -> LeadScorePage:
    """The ranked leads of a run. The rank is the business's place in the whole run; filters only
    choose which rows are shown."""
    run_id = query.score_run_id or _latest_run(conn)
    if run_id is None:
        return LeadScorePage(score_run=None, items=[], next_cursor=None, total=0)
    run = repository.get(conn, ScoreRun, run_id)
    where, params = ["1 = 1"], list[object]()
    if query.tier is not None:
        where.append("s.tier = ?")
        params.append(query.tier.value)
    if query.min_score is not None:
        where.append("s.total_score >= ?")
        params.append(query.min_score)
    if query.problem is not None:
        where.append(_HAS_PROBLEM)
        params.append(query.problem.value)
    if (match := repository.fts_query(query.search)) is not None:
        where.append(
            "s.business_id IN (SELECT coalesce(m.merged_into_id, m.id) FROM businesses m "
            "WHERE m.seq IN (SELECT rowid FROM business_search WHERE business_search MATCH ?))"
        )
        params.append(match)
    ranked = _ranked(
        "s.business_id, s.tier, s.total_score, s.confidence, s.scorecard_json, s.facts_json"
    )
    (total,) = conn.execute(
        f"SELECT count(*) FROM ({ranked}) s WHERE {' AND '.join(where)}",  # noqa: S608
        [run.id, *params],
    ).fetchone()
    tag = f"leads.{run.id}"
    if query.cursor is not None:
        last_rank, _ = repository.decode_cursor(query.cursor, tag)
        where.append("s.rank > ?")
        params.append(int(last_rank))
    rows = conn.execute(
        f"SELECT s.*, b.lead_stage FROM ({ranked}) s JOIN businesses b ON b.id = s.business_id "  # noqa: S608
        f"WHERE {' AND '.join(where)} ORDER BY s.rank LIMIT ?",
        [run.id, *params, query.limit + 1],
    ).fetchall()
    page = rows[: query.limit]
    next_cursor = (
        repository.encode_cursor(tag, f"{page[-1]['rank']:09d}", page[-1]["business_id"])
        if len(rows) > query.limit
        else None
    )
    items = [
        _item(
            orjson.loads(row["facts_json"]),
            orjson.loads(row["scorecard_json"]),
            row["rank"],
            row["lead_stage"],
        )
        for row in page
    ]
    return LeadScorePage(
        score_run=run_item(conn, run.id), items=items, next_cursor=next_cursor, total=total
    )


def _root(conn: sqlite3.Connection, business_id: str) -> str:
    row = conn.execute(
        "SELECT coalesce(merged_into_id, id) AS root FROM businesses "
        "WHERE id = ? AND deleted_at IS NULL",
        (business_id,),
    ).fetchone()
    if row is None:
        raise repository.NotFoundError(f"business {business_id} not found")
    return str(row["root"])


def ai_reasons(
    conn: sqlite3.Connection, business_id: str, card: LeadScorecard
) -> tuple[list[AiDraftedReason], int]:
    """The problems of the newest valid, not rejected `opportunity_reasoning` answer, each kept only
    when every reference it cites is a website finding this scorecard rests on. A model's wording
    never changes a number and never adds evidence."""
    row = conn.execute(
        "SELECT r.id, r.model, r.input_json, o.validated_output_json FROM ai_inference_runs r "
        "JOIN ai_inference_outputs o ON o.inference_run_id = r.id "
        "AND o.validated_output_json IS NOT NULL "
        "WHERE r.business_id = ? AND r.task_type = 'opportunity_reasoning' "
        "AND r.status = 'succeeded' AND r.review_status <> 'rejected' "
        "ORDER BY r.finished_at DESC, r.id DESC, o.attempt DESC LIMIT 1",
        (business_id,),
    ).fetchone()
    if row is None:
        return [], 0
    refs = orjson.loads(row["input_json"]).get("refs") or []
    by_ref = {ref.get("ref"): ref for ref in refs if isinstance(ref, dict)}
    evidence = {
        e.ref: e
        for feature in card.features
        for e in feature.evidence
        if e.kind is ScoreEvidenceKind.WEBSITE_FINDING
    }
    output = OpportunityReasoningOutput.model_validate(orjson.loads(row["validated_output_json"]))
    kept: list[AiDraftedReason] = []
    dropped = 0
    for point in output.problems:
        cited: list[ScoreEvidence] = []
        for ref in point.evidence_ids:
            source = by_ref.get(ref) or {}
            found = evidence.get(str(source.get("entity_id")))
            if source.get("kind") != "website_finding" or found is None:
                break
            cited.append(found)
        else:
            kept.append(
                AiDraftedReason(
                    inference_run_id=row["id"],
                    model=row["model"],
                    problem=point.problem,
                    explanation=point.explanation,
                    evidence=cited,
                )
            )
            continue
        dropped += 1
    return kept, dropped


def get_scorecard(conn: sqlite3.Connection, query: ScorecardQuery) -> LeadScorecardDetail:
    root = _root(conn, query.business_id)
    run_id = query.score_run_id or _latest_run(conn, root)
    if run_id is None:
        raise repository.NotFoundError(f"business {root} has not been scored by a finished run")
    row = conn.execute(
        f"SELECT s.rank, s.scorecard_json FROM ({_ranked('s.business_id, s.scorecard_json')}) s "  # noqa: S608
        "WHERE s.business_id = ?",
        (run_id, root),
    ).fetchone()
    if row is None:
        raise repository.NotFoundError(f"score run {run_id} did not score business {root}")
    card = LeadScorecard.model_validate(orjson.loads(row["scorecard_json"]))
    business = repository.get(conn, Business, root)
    history = [
        LeadScoreHistoryItem.model_validate(dict(found))
        for found in conn.execute(
            "SELECT s.score_run_id, s.created_at, m.key AS scoring_model_key, "
            "mv.version AS scoring_model_version, p.name AS icp_profile_name, "
            "pv.version AS icp_profile_version, s.tier, s.total_score, s.confidence "
            "FROM business_scores s JOIN score_runs r ON r.id = s.score_run_id "
            "JOIN scoring_model_versions mv ON mv.id = r.scoring_model_version_id "
            "JOIN scoring_models m ON m.id = mv.scoring_model_id "
            "JOIN icp_profile_versions pv ON pv.id = r.icp_profile_version_id "
            "JOIN icp_profiles p ON p.id = pv.icp_profile_id "
            "WHERE s.business_id = ? ORDER BY s.created_at DESC, s.id DESC LIMIT ?",
            (root, MAX_HISTORY),
        )
    ]
    outcomes = [
        repository.from_row(LeadOutcome, found)
        for found in conn.execute(
            f"SELECT {repository._columns(LeadOutcome)} FROM lead_outcomes "  # noqa: S608
            "WHERE business_id = ? ORDER BY occurred_at DESC, id DESC LIMIT ?",
            (root, MAX_HISTORY),
        )
    ]
    decisions = [
        repository.from_row(ReviewDecision, found)
        for found in conn.execute(
            f"SELECT {repository._columns(ReviewDecision)} FROM review_decisions "  # noqa: S608
            "WHERE entity_type = 'business' AND entity_id = ? AND decision IN (?, ?, ?) "
            "ORDER BY decided_at DESC, id DESC LIMIT ?",
            (root, *REVIEW_DECISIONS.values(), MAX_HISTORY),
        )
    ]
    kept, dropped = ai_reasons(conn, root, card)
    run = run_item(conn, run_id)
    return LeadScorecardDetail(
        score_run=run,
        business_id=root,
        display_name=business.display_name,
        lead_stage=business.lead_stage,
        rank=row["rank"],
        scorecard=card,
        history=history,
        outcomes=outcomes,
        decisions=decisions,
        ai_reasons=kept,
        ai_dropped=dropped,
        is_synthetic=run.is_synthetic,
    )


def act(conn: sqlite3.Connection, request: LeadActionRequest, *, actor: str) -> None:
    """A person's review of a score, or how the lead was worked: an outcome that moves its stage,
    or an attempt that keeps it (phase 12). None of them changes a score or its facts."""
    root = _root(conn, request.business_id)
    if not conn.execute(
        "SELECT 1 FROM business_scores WHERE score_run_id = ? AND business_id = ?",
        (request.score_run_id, root),
    ).fetchone():
        raise repository.NotFoundError(f"score run {request.score_run_id} did not score {root}")
    now = utc_now()
    worked = (
        request.stage, request.lost_reason, request.channel, request.attempt_result,
        request.value_amount, request.value_currency, request.contact_quality, request.notes,
        request.occurred_at,
    )  # fmt: skip
    if request.action in (LeadAction.RECORD_OUTCOME, LeadAction.RECORD_ATTEMPT):
        _record_work(conn, request, root, now, actor=actor)
        return
    if any(value is not None for value in worked):
        raise ScoringError("a review records no stage, attempt or outcome")
    with repository.atomic(conn):
        repository.insert(
            conn,
            ReviewDecision(
                id=new_id(),
                entity_type=EntityType.BUSINESS,
                entity_id=root,
                decision=REVIEW_DECISIONS[request.action],
                previous_value=None,
                new_value=request.score_run_id,
                reason=request.reason,
                actor=actor,
                decided_at=now,
            ),
        )


def _record_work(
    conn: sqlite3.Connection, request: LeadActionRequest, root: str, now: datetime, *, actor: str
) -> None:
    """An outcome moves the lead's stage along its machine; an attempt keeps it. The history stays
    in order: nothing is recorded in the future or before the lead's latest recorded outcome."""
    occurred = request.occurred_at or now
    target = request.stage
    if occurred > now:
        raise ScoringError("an outcome cannot be recorded in the future")
    if (request.value_amount is None) != (request.value_currency is None):
        raise ScoringError("a value needs its currency, and a currency its value")
    if request.action is LeadAction.RECORD_ATTEMPT:
        if target is not None or request.lost_reason is not None:
            raise ScoringError("an attempt keeps the stage: record an outcome to change it")
        if request.channel is None or request.attempt_result is None:
            raise ScoringError("an attempt needs the channel used and its result")
    elif target is None:
        raise ScoringError("choose the stage the lead reached")
    elif (target is LeadStage.LOST) != (request.lost_reason is not None):
        raise ScoringError("a lost lead needs a lost reason, and only a lost lead has one")
    with repository.atomic(conn):
        business = repository.get(conn, Business, root)
        (latest,) = conn.execute(
            "SELECT max(occurred_at) FROM lead_outcomes WHERE business_id = ?", (root,)
        ).fetchone()
        if latest is not None and format_ts(occurred) < latest:
            raise ScoringError(
                "record it after the lead's latest outcome: its history stays in order"
            )
        stage = business.lead_stage
        if target is not None:
            if business.lead_stage is target:
                raise ScoringError(f"the lead is already at '{target.value}'")
            repository.change_status(
                conn, Business, root, target, actor=actor, reason=request.reason
            )
            stage = target
        repository.insert(
            conn,
            LeadOutcome(
                id=new_id(),
                business_id=root,
                score_run_id=request.score_run_id,
                stage=stage,
                previous_stage=business.lead_stage,
                lost_reason=request.lost_reason,
                reason=request.reason,
                actor=actor,
                occurred_at=occurred,
                created_at=now,
                channel=request.channel,
                attempt_result=request.attempt_result,
                value_amount=request.value_amount,
                value_currency=request.value_currency,
                contact_quality=request.contact_quality,
                notes=request.notes,
            ),
        )


# ---- comparison and calibration ------------------------------------------------------------------


def _ranks(
    conn: sqlite3.Connection, score_run_id: str
) -> dict[str, tuple[int, LeadTier, float | None]]:
    return {
        row["business_id"]: (row["rank"], LeadTier(row["tier"]), row["total_score"])
        for row in conn.execute(_ranked("s.business_id, s.tier, s.total_score"), (score_run_id,))
    }


def compare(conn: sqlite3.Connection, query: ScoreCompareQuery) -> ScoreComparison:
    left = repository.get(conn, ScoreRun, query.left_run_id)
    right = repository.get(conn, ScoreRun, query.right_run_id)
    if left.project_id != right.project_id:
        raise ScoringError("only runs of the same project can be compared")
    if left.finished_at is None or right.finished_at is None:
        raise ScoringError("only finished runs can be compared")
    ranks_left, ranks_right = _ranks(conn, left.id), _ranks(conn, right.id)
    top_left = {b for b, (rank, _, _) in ranks_left.items() if rank <= query.top_k}
    top_right = {b for b, (rank, _, _) in ranks_right.items() if rank <= query.top_k}
    common = ranks_left.keys() & ranks_right.keys()
    shown = sorted(
        top_left | top_right,
        key=lambda b: (min(ranks_left.get(b, (10**9,))[0], ranks_right.get(b, (10**9,))[0]), b),
    )
    names = (
        {
            row["id"]: row["display_name"]
            for row in conn.execute(
                f"SELECT id, display_name FROM businesses WHERE id IN ({_marks(shown)})",  # noqa: S608
                shown,
            )
        }
        if shown
        else {}
    )
    changes = [
        RankChange(
            business_id=b,
            display_name=names.get(b, b),
            left_rank=ranks_left[b][0] if b in ranks_left else None,
            right_rank=ranks_right[b][0] if b in ranks_right else None,
            left_tier=ranks_left[b][1] if b in ranks_left else None,
            right_tier=ranks_right[b][1] if b in ranks_right else None,
            left_total=ranks_left[b][2] if b in ranks_left else None,
            right_total=ranks_right[b][2] if b in ranks_right else None,
        )
        for b in shown
    ]
    moves = [abs(ranks_left[b][0] - ranks_right[b][0]) for b in common]
    return ScoreComparison(
        left=run_item(conn, left.id),
        right=run_item(conn, right.id),
        same_facts=right.facts_from_run_id == left.id
        or left.facts_from_run_id == right.id
        or (
            left.facts_from_run_id is not None and left.facts_from_run_id == right.facts_from_run_id
        ),
        top_k=query.top_k,
        entered=len(top_right - top_left),
        left_out=len(top_left - top_right),
        mean_abs_rank_change=round(fsum(moves) / len(moves), 4) if moves else None,
        changes=changes,
    )


def calibration(conn: sqlite3.Connection, query: CalibrationQuery) -> CalibrationReport:
    """Offline metrics against outcomes recorded after each business's facts were read: an outcome
    that happened before the facts were read never calibrates the score built on them (leakage)."""
    run = repository.get(conn, ScoreRun, query.score_run_id)
    rows = conn.execute(
        _ranked("s.business_id, s.tier, s.total_score, s.facts_collected_at") + " ORDER BY rank",
        (run.id,),
    ).fetchall()
    collected = {row["business_id"]: row["facts_collected_at"] for row in rows}
    latest: dict[str, LeadStage] = {}
    leakage = 0
    for outcome in conn.execute(
        "SELECT o.business_id, o.stage, o.occurred_at FROM lead_outcomes o "
        "JOIN business_scores s ON s.business_id = o.business_id AND s.score_run_id = ? "
        "ORDER BY o.occurred_at, o.id",
        (run.id,),
    ):
        if outcome["occurred_at"] <= collected[outcome["business_id"]]:
            leakage += 1
        else:
            latest[outcome["business_id"]] = LeadStage(outcome["stage"])
    labels = [model.label_of(latest.get(row["business_id"])) for row in rows]
    labeled = [label for label in labels if label is not None]
    tiers = [LeadTier(row["tier"]) for row in rows]
    version = repository.get(conn, ScoringModelVersion, run.scoring_model_version_id)
    warnings = [
        code
        for code, applies in (
            ("no_labels", not labeled),
            ("too_few_labels", 0 < len(labeled) < model.MIN_LABELS),
            ("no_positives", bool(labeled) and not any(labeled)),
            ("heuristic_weights", version.is_heuristic),
        )
        if applies
    ]
    businesses = len(rows)
    return CalibrationReport(
        score_run=run_item(conn, run.id),
        positive_stages=list(model.POSITIVE_STAGES),
        negative_stages=list(model.NEGATIVE_STAGES),
        businesses=businesses,
        labeled=len(labeled),
        positives=sum(labeled),
        leakage_excluded=leakage,
        precision_at_k=[model.precision_at_k(labels, k) for k in model.PRECISION_KS],
        lift_by_decile=model.lift_by_decile(labels),
        calibration=model.calibration_bands(
            [(row["total_score"], label) for row, label in zip(rows, labels, strict=True)]
        ),
        cohorts=model.tier_cohorts(list(zip(tiers, labels, strict=True))),
        coverage=round(sum(t in _SCORED for t in tiers) / businesses, 4) if businesses else 0.0,
        labeled_share=round(len(labeled) / businesses, 4) if businesses else 0.0,
        weights_are_heuristic=version.is_heuristic,
        warnings=warnings,
    )
