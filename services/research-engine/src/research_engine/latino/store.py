"""Store side of the Latino evidence engine (phase 07): the only SQL it runs.

- Sources: the raw records behind each business, and the pages crawled from its website, linked
  through the candidate that asked for the crawl or through the website's domain, so a brand's site
  reaches every location that lists it.
- Extraction: evidence through versioned extractions (`latino.payload`, `latino.site`), at most one
  item per claim, source record and business. Evidence is never edited; only its status moves.
- Lifecycle: evidence too old or past its expiry is `stale`; a claim a newer snapshot of the same
  page no longer makes is `revoked`; ownership evidence that newer evidence contradicts is
  `contradicted`.
- Assessments: one automatic assessment per canonical business, superseded (never edited) when its
  evidence, their lifecycle or the rules change. A person's review coexists with it and is what the
  business shows; withdrawing the review shows the calculation again.
"""

import hashlib
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Final

import orjson
from pydantic import JsonValue

from research_engine import jobs, repository
from research_engine.domain import (
    AssessmentMethod,
    AuditAction,
    Business,
    EntityType,
    EventLevel,
    EvidenceItem,
    EvidenceStatus,
    EvidenceStrength,
    Extraction,
    InferenceRun,
    Job,
    JobStatus,
    JsonObject,
    LatinoAssessment,
    LatinoAssessmentEvidence,
    LatinoAssessmentView,
    LatinoDetail,
    LatinoEvidence,
    LatinoNote,
    LatinoReviewRequest,
    LatinoRun,
    LatinoRunListQuery,
    LatinoRunPage,
    LatinoStats,
    LatinoStatus,
    LatinoTrack,
    ObservedEvidence,
    Project,
    Provenance,
    Record,
    ReviewDecision,
    format_ts,
    new_id,
    utc_now,
)
from research_engine.latino import rules
from research_engine.resolution import normalize

JOB_KIND: Final = "latino_evidence"
ACTOR: Final = "latino-engine"
EXTRACTOR_PAYLOAD: Final = "latino.payload"
EXTRACTOR_SITE: Final = "latino.site"
#: Claims a local model pointed at and the patterns confirmed (phase 10 provides the model).
EXTRACTOR_MODEL: Final = "latino.model"
EXTRACTORS: Final = (EXTRACTOR_PAYLOAD, EXTRACTOR_SITE, EXTRACTOR_MODEL)
#: The key `crawl.engine` registers its connector under (not imported: it loads the browser stack).
CRAWL_CONNECTOR_KEY: Final = "web.crawler"
WRITE_BATCH: Final = 500
MAX_HISTORY: Final = 50
REVIEW_DECISION: Final = "latino_review"
WITHDRAW_DECISION: Final = "latino_review_withdrawn"
_CURRENT: Final = repository.CURRENT_EXTRACTION


class LatinoError(ValueError):
    """A Latino evidence request that cannot be carried out as asked; the message says why."""


def _object(text: str | None) -> JsonObject:
    loaded = orjson.loads(text) if text else None
    return loaded if isinstance(loaded, dict) else {}


def _batches[T](items: Sequence[T], size: int = WRITE_BATCH) -> Iterable[Sequence[T]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


# ---- sources -----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Source:
    """A raw record to read, and the businesses its claims are about."""

    raw_record_id: str
    is_page: bool
    business_ids: tuple[str, ...]


_WEBSITES_SQL: Final = (
    "SELECT w.business_id, w.url_normalized FROM websites w "
    "JOIN businesses b ON b.id = w.business_id JOIN extractions x ON x.id = w.extraction_id "
    "WHERE b.project_id = ? AND b.deleted_at IS NULL AND {current}"
).replace("{current}", _CURRENT)


def page_links(conn: sqlite3.Connection, project_id: str) -> dict[str, set[str]]:
    """Crawled pages and the businesses they speak for: the business whose candidate asked for the
    crawl, and every business whose website is on that site."""
    by_candidate: dict[str, set[str]] = defaultdict(set)
    for row in conn.execute(
        "SELECT er.candidate_id, er.business_id FROM entity_records er "
        "JOIN businesses b ON b.id = er.business_id "
        "WHERE er.project_id = ? AND b.deleted_at IS NULL",
        (project_id,),
    ):
        by_candidate[row["candidate_id"]].add(row["business_id"])
    domains: dict[str, str | None] = {}

    def domain_of(value: str) -> str | None:
        if value not in domains:
            domains[value] = normalize.website(value).registrable_domain
        return domains[value]

    by_domain: dict[str, set[str]] = defaultdict(set)
    for row in conn.execute(_WEBSITES_SQL, (project_id,)):
        if domain := domain_of(row["url_normalized"]):
            by_domain[domain].add(row["business_id"])
    pages: dict[str, set[str]] = defaultdict(set)
    for row in conn.execute(
        "SELECT DISTINCT cu.raw_record_id, "
        "json_extract(r.effective_config_json, '$.candidate_id') AS candidate_id, "
        "json_extract(r.effective_config_json, '$.site') AS site "
        "FROM crawl_urls cu JOIN source_runs r ON r.id = cu.source_run_id "
        "JOIN source_connectors sc ON sc.id = r.connector_id "
        "WHERE r.project_id = ? AND sc.key = ? AND cu.status = 'fetched' AND cu.target = 'page' "
        "AND cu.duplicate_of_id IS NULL AND cu.raw_record_id IS NOT NULL",
        (project_id, CRAWL_CONNECTOR_KEY),
    ):
        businesses = set(by_candidate.get(row["candidate_id"] or "", ()))
        if isinstance(row["site"], str) and (domain := domain_of(row["site"])):
            businesses |= by_domain.get(domain, set())
        if businesses:
            pages[row["raw_record_id"]] |= businesses
    return pages


def sources(conn: sqlite3.Connection, project_id: str) -> list[Source]:
    """Every raw record behind a live business of the project, ordered by id (resumable)."""
    linked: dict[str, set[str]] = defaultdict(set)
    for row in conn.execute(
        "SELECT DISTINCT x.raw_record_id, n.business_id FROM business_names n "
        "JOIN businesses b ON b.id = n.business_id JOIN extractions x ON x.id = n.extraction_id "
        "JOIN raw_records r ON r.id = x.raw_record_id "
        "JOIN source_connectors sc ON sc.id = r.connector_id "
        "WHERE b.project_id = ? AND b.deleted_at IS NULL AND sc.key <> ?",
        (project_id, CRAWL_CONNECTOR_KEY),
    ):
        linked[row["raw_record_id"]].add(row["business_id"])
    pages = page_links(conn, project_id)
    for raw_record_id, business_ids in pages.items():
        linked[raw_record_id] |= business_ids
    return [
        Source(raw_record_id, raw_record_id in pages, tuple(sorted(business_ids)))
        for raw_record_id, business_ids in sorted(linked.items())
    ]


# ---- extraction --------------------------------------------------------------------------------


def evidence_item(
    finding: rules.Finding,
    business_id: str,
    extraction_id: str,
    observed_at: datetime,
    now: datetime,
    ruleset: rules.Ruleset = rules.DEFAULT_RULESET,
) -> EvidenceItem:
    rule = ruleset.claims[finding.claim]
    snippet = finding.snippet[:2000] or None
    value: JsonObject = {
        "claim": finding.claim,
        "track": rule.track.value,
        "observed_value": finding.observed_value[:500],
        **finding.detail,
    }
    return EvidenceItem(
        id=new_id(), business_id=business_id, evidence_type=rule.evidence_type,
        claim=rule.label, value_json=value, strength=rule.strength, status=EvidenceStatus.ACTIVE,
        confidence=finding.confidence, extraction_id=extraction_id, web_page_id=None,
        field_path=finding.field_path, snippet=snippet,
        snippet_sha256=hashlib.sha256(snippet.encode()).hexdigest() if snippet else None,
        observed_at=observed_at, created_at=now, updated_at=now,
    )  # fmt: skip


@dataclass(frozen=True, slots=True)
class ScanBatch:
    sources: int
    pages: int
    evidence: int


def scan_batch(conn: sqlite3.Connection, batch: Sequence[Source], now: datetime) -> ScanBatch:
    """Reads a batch of sources in one transaction. Idempotent: an extraction of this version is
    written once per raw record, and a claim once per extraction and business."""
    ids = [source.raw_record_id for source in batch]
    if not ids:
        return ScanBatch(0, 0, 0)
    marks = ", ".join("?" * len(ids))
    params = [*ids, EXTRACTOR_PAYLOAD, EXTRACTOR_SITE, rules.EXTRACTOR_VERSION]
    payloads = {
        row["id"]: row
        for row in conn.execute(
            f"SELECT id, payload_json, acquired_at FROM raw_records WHERE id IN ({marks})",  # noqa: S608
            ids,
        )
    }
    extractions = {
        (row["raw_record_id"], row["extractor"]): row["id"]
        for row in conn.execute(
            "SELECT id, raw_record_id, extractor FROM extractions "  # noqa: S608 - placeholders
            f"WHERE raw_record_id IN ({marks}) AND extractor IN (?, ?) AND extractor_version = ?",
            params,
        )
    }
    existing = {
        (row["extraction_id"], row["business_id"], row["claim"])
        for row in conn.execute(
            "SELECT e.extraction_id, e.business_id, "  # noqa: S608 - placeholders
            "json_extract(e.value_json, '$.claim') AS claim FROM evidence_items e "
            f"JOIN extractions x ON x.id = e.extraction_id WHERE x.raw_record_id IN ({marks}) "
            "AND x.extractor IN (?, ?) AND x.extractor_version = ?",
            params,
        )
    }
    records: list[Record] = []
    found = 0
    for source in batch:
        row = payloads.get(source.raw_record_id)
        if row is None:
            continue
        payload = _object(row["payload_json"])
        extractor = EXTRACTOR_SITE if source.is_page else EXTRACTOR_PAYLOAD
        findings = rules.scan_page(payload) if source.is_page else rules.scan_payload(payload)
        extraction_id = extractions.get((source.raw_record_id, extractor))
        if extraction_id is None:
            extraction = Extraction(
                id=new_id(), raw_record_id=source.raw_record_id, extractor=extractor,
                extractor_version=rules.EXTRACTOR_VERSION, extracted_at=now,
            )  # fmt: skip
            records.append(extraction)
            extraction_id = extraction.id
        observed_at = datetime.fromisoformat(row["acquired_at"])
        for business_id in source.business_ids:
            for finding in findings:
                if (extraction_id, business_id, finding.claim) not in existing:
                    records.append(
                        evidence_item(finding, business_id, extraction_id, observed_at, now)
                    )
                    found += 1
    with repository.atomic(conn):
        repository.insert(conn, *records)
    return ScanBatch(len(batch), sum(source.is_page for source in batch), found)


# ---- lifecycle and evaluation ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Stored:
    fact: rules.Fact
    business_id: str
    raw_record_id: str
    source_url: str | None
    extractor: str


_EVIDENCE_SQL: Final = (
    "SELECT e.id, e.business_id, e.status, e.observed_at, "
    "json_extract(e.value_json, '$.claim') AS claim, "
    "json_extract(e.value_json, '$.expires_on') AS expires_on, x.raw_record_id, x.extractor, "
    "r.source_url, sc.kind AS source_kind, sc.key AS connector_key "
    "FROM evidence_items e JOIN businesses b ON b.id = e.business_id "
    "JOIN extractions x ON x.id = e.extraction_id JOIN raw_records r ON r.id = x.raw_record_id "
    "JOIN source_connectors sc ON sc.id = r.connector_id "
    "WHERE b.project_id = ? AND b.deleted_at IS NULL AND x.extractor IN (?, ?, ?) AND {current} "
    "ORDER BY e.id"
).replace("{current}", _CURRENT)


def stored_evidence(
    conn: sqlite3.Connection, project_id: str, page_roots: Mapping[str, set[str]]
) -> list[Stored]:
    """Current Latino evidence of a project. Evidence of one website is one source, and a page that
    speaks for several canonical businesses is organizational evidence for each of them."""
    domains: dict[str, str] = {}
    out: list[Stored] = []
    for row in conn.execute(_EVIDENCE_SQL, (project_id, *EXTRACTORS)):
        url, extractor = row["source_url"], row["extractor"]
        if extractor == EXTRACTOR_PAYLOAD or not url:
            source_key = f"connector:{row['connector_key']}"
        else:
            if url not in domains:
                domains[url] = normalize.website(url).registrable_domain or url
            source_key = f"site:{domains[url]}"
        expires = row["expires_on"]
        fact = rules.Fact(
            evidence_id=row["id"],
            claim=str(row["claim"] or ""),
            status=EvidenceStatus(row["status"]),
            source_kind=row["source_kind"],
            source_key=source_key,
            observed_at=datetime.fromisoformat(row["observed_at"]),
            organizational=extractor != EXTRACTOR_PAYLOAD
            and len(page_roots.get(row["raw_record_id"], ())) > 1,
            expires_on=date.fromisoformat(expires) if isinstance(expires, str) else None,
        )
        out.append(Stored(fact, row["business_id"], row["raw_record_id"], url, extractor))
    return out


def _revoked(
    conn: sqlite3.Connection,
    project_id: str,
    evidence: Sequence[Stored],
    links: Mapping[str, set[str]],
) -> set[str]:
    """Claims about a business that a newer read snapshot of the same page no longer makes."""
    snapshots: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for row in conn.execute(
        "SELECT x.raw_record_id, r.source_url, r.acquired_at FROM extractions x "
        "JOIN raw_records r ON r.id = x.raw_record_id "
        "JOIN source_runs sr ON sr.id = r.source_run_id "
        "WHERE sr.project_id = ? AND x.extractor = ? AND x.extractor_version = ? "
        "AND r.source_url IS NOT NULL",
        (project_id, EXTRACTOR_SITE, rules.EXTRACTOR_VERSION),
    ):
        for business_id in links.get(row["raw_record_id"], ()):
            snapshots[(business_id, row["source_url"])].append(
                (row["acquired_at"], row["raw_record_id"])
            )
    newest = {key: max(values)[1] for key, values in snapshots.items()}
    claims: dict[tuple[str, str], set[str]] = defaultdict(set)
    for item in evidence:
        claims[(item.business_id, item.raw_record_id)].add(item.fact.claim)
    out: set[str] = set()
    for item in evidence:
        if item.extractor == EXTRACTOR_PAYLOAD or item.source_url is None:
            continue
        latest = newest.get((item.business_id, item.source_url))
        if (
            latest is not None
            and latest != item.raw_record_id
            and (item.fact.claim not in claims[(item.business_id, latest)])
        ):
            out.add(item.fact.evidence_id)
    return out


_PATHS: Final = {
    (EvidenceStatus.CONTRADICTED, EvidenceStatus.STALE): (
        EvidenceStatus.ACTIVE,
        EvidenceStatus.STALE,
    ),
    (EvidenceStatus.STALE, EvidenceStatus.CONTRADICTED): (
        EvidenceStatus.ACTIVE,
        EvidenceStatus.CONTRADICTED,
    ),
}
_REASONS: Final = {
    EvidenceStatus.STALE: "too old or past its expiry date for the rules",
    EvidenceStatus.REVOKED: "a newer snapshot of the page no longer makes this claim",
    EvidenceStatus.CONTRADICTED: "newer evidence contradicts it",
    EvidenceStatus.ACTIVE: "counts again under the current evidence and rules",
}


@dataclass(frozen=True, slots=True)
class Context:
    """What evaluating a project needs: its canonical businesses, their records and evidence."""

    roots: list[str]
    members: Mapping[str, list[str]]
    facts: Mapping[str, list[rules.Fact]]


def evaluation_context(
    conn: sqlite3.Connection,
    project_id: str,
    now: datetime,
    ruleset: rules.Ruleset = rules.DEFAULT_RULESET,
) -> tuple[Context, Counter[str]]:
    """Moves evidence along its lifecycle, then gathers each canonical business's evidence.
    Returns the context and how many items changed status, by new status."""
    live = conn.execute(
        "SELECT id, merged_into_id FROM businesses WHERE project_id = ? AND deleted_at IS NULL "
        "ORDER BY id",
        (project_id,),
    ).fetchall()
    ids = {row["id"] for row in live}
    root_of = {
        row["id"]: row["merged_into_id"] if row["merged_into_id"] in ids else row["id"]
        for row in live
    }
    members: dict[str, list[str]] = defaultdict(list)
    for business_id, root in root_of.items():
        members[root].append(business_id)
    links = page_links(conn, project_id)
    page_roots = {
        raw: {root_of[business] for business in businesses if business in root_of}
        for raw, businesses in links.items()
    }
    evidence = stored_evidence(conn, project_id, page_roots)
    revoked = _revoked(conn, project_id, evidence, links)
    targets: dict[str, EvidenceStatus] = {}
    for item in evidence:
        fact = item.fact
        if fact.status is EvidenceStatus.REVOKED:
            continue
        if fact.evidence_id in revoked:
            targets[fact.evidence_id] = EvidenceStatus.REVOKED
        elif rules.is_stale(fact, now, ruleset):
            targets[fact.evidence_id] = EvidenceStatus.STALE
        elif fact.status is EvidenceStatus.STALE:
            targets[fact.evidence_id] = EvidenceStatus.ACTIVE
    by_root: dict[str, list[Stored]] = defaultdict(list)
    for item in evidence:
        if item.business_id in root_of:
            by_root[root_of[item.business_id]].append(item)
    for items in by_root.values():
        current = [
            replace(item.fact, status=targets.get(item.fact.evidence_id, item.fact.status))
            for item in items
        ]
        against = rules.contradicted(current, ruleset)
        for fact in current:
            if fact.status in (EvidenceStatus.REVOKED, EvidenceStatus.STALE):
                continue
            if fact.evidence_id in against and fact.status is EvidenceStatus.ACTIVE:
                targets[fact.evidence_id] = EvidenceStatus.CONTRADICTED
            elif fact.evidence_id not in against and fact.status is EvidenceStatus.CONTRADICTED:
                targets[fact.evidence_id] = EvidenceStatus.ACTIVE
    status_of = {item.fact.evidence_id: item.fact.status for item in evidence}
    changes: Counter[str] = Counter()
    moves = sorted((i, t) for i, t in targets.items() if status_of[i] is not t)
    for chunk in _batches(moves):
        with repository.atomic(conn):
            for evidence_id, target in chunk:
                for step in _PATHS.get((status_of[evidence_id], target), (target,)):
                    repository.change_status(conn, EvidenceItem, evidence_id, step, actor=ACTOR,
                                             reason=_REASONS[target])  # fmt: skip
                changes[target.value] += 1
    facts: dict[str, list[rules.Fact]] = defaultdict(list)
    for root, items in by_root.items():
        facts[root] = [
            replace(item.fact, status=targets.get(item.fact.evidence_id, item.fact.status))
            for item in items
        ]
    roots = sorted(root for root in members if root_of[root] == root)
    return Context(roots, members, facts), changes


def _current(
    conn: sqlite3.Connection, business_ids: Sequence[str], method: AssessmentMethod
) -> dict[str, LatinoAssessment]:
    if not business_ids:
        return {}
    marks = ", ".join("?" * len(business_ids))
    return {
        row["business_id"]: repository.from_row(LatinoAssessment, row)
        for row in conn.execute(
            f"SELECT {repository._columns(LatinoAssessment)} FROM latino_assessments "  # noqa: S608
            f"WHERE business_id IN ({marks}) AND method = ? AND superseded_at IS NULL",
            [*business_ids, method.value],
        )
    }


def _links(
    conn: sqlite3.Connection, assessment_ids: Sequence[str]
) -> dict[str, list[LatinoAssessmentEvidence]]:
    out: dict[str, list[LatinoAssessmentEvidence]] = defaultdict(list)
    if not assessment_ids:
        return out
    marks = ", ".join("?" * len(assessment_ids))
    for row in conn.execute(
        f"SELECT {repository._columns(LatinoAssessmentEvidence)} "  # noqa: S608
        f"FROM latino_assessment_evidence WHERE assessment_id IN ({marks}) "
        "ORDER BY evidence_item_id",
        list(assessment_ids),
    ):
        link = repository.from_row(LatinoAssessmentEvidence, row)
        out[link.assessment_id].append(link)
    return out


def evaluate_batch(
    conn: sqlite3.Connection,
    context: Context,
    roots: Sequence[str],
    now: datetime,
    ruleset: rules.Ruleset = rules.DEFAULT_RULESET,
) -> int:
    """Evaluates canonical businesses and writes the assessments that changed. Returns how many."""
    current = _current(conn, roots, AssessmentMethod.RULES)
    cited = _links(conn, [assessment.id for assessment in current.values()])
    supersede: list[str] = []
    records: list[Record] = []
    for root in roots:
        evaluation = rules.evaluate(context.facts.get(root, []), now, ruleset)
        details: JsonObject = {
            "warnings": [
                {"code": note.code, "detail": note.detail} for note in evaluation.warnings
            ],
            "cannot_conclude": list(evaluation.cannot_conclude),
        }
        wanted = (
            evaluation.status, evaluation.confidence, ruleset.version,
            sorted((f.evidence_id, f.role.value, f.counted) for f in evaluation.factors), details,
        )  # fmt: skip
        if (existing := current.get(root)) is not None:
            have = (
                existing.status, existing.assessment_confidence, existing.rules_version,
                sorted((link.evidence_item_id, link.role.value, link.counted)
                       for link in cited.get(existing.id, [])),
                existing.details_json,
            )  # fmt: skip
            if have == wanted:
                continue
            supersede.append(existing.id)
        assessment = LatinoAssessment(
            id=new_id(), business_id=root, status=evaluation.status,
            assessment_confidence=evaluation.confidence, method=AssessmentMethod.RULES,
            rules_version=ruleset.version, inference_run_id=None, rationale=evaluation.rationale,
            limitations=evaluation.limitations, assessed_at=now, superseded_at=None,
            created_at=now, details_json=details,
        )  # fmt: skip
        records.append(assessment)
        records += [
            LatinoAssessmentEvidence(
                assessment_id=assessment.id,
                evidence_item_id=factor.evidence_id,
                role=factor.role,
                source_reliability=factor.source_reliability,
                freshness=factor.freshness,
                score=factor.score,
                counted=factor.counted,
                organizational=factor.organizational,
            )
            for factor in evaluation.factors
        ]
    stamp = format_ts(now)
    with repository.atomic(conn):
        conn.executemany(
            "UPDATE latino_assessments SET superseded_at = ? WHERE id = ?",
            [(stamp, assessment_id) for assessment_id in supersede],
        )
        repository.insert(conn, *records)
    return sum(isinstance(record, LatinoAssessment) for record in records)


def supersede_non_roots(conn: sqlite3.Connection, project_id: str, now: datetime) -> int:
    """A record merged into another business (or deleted) no longer has an assessment of its own."""
    with repository.atomic(conn):
        cursor = conn.execute(
            "UPDATE latino_assessments SET superseded_at = ? WHERE superseded_at IS NULL "
            "AND business_id IN (SELECT id FROM businesses WHERE project_id = ? "
            "AND (merged_into_id IS NOT NULL OR deleted_at IS NOT NULL))",
            (format_ts(now), project_id),
        )
    return cursor.rowcount


_COUNTS_SQL: Final = (
    "SELECT json_extract(e.value_json, '$.claim') AS claim, e.status, count(*) AS n "
    "FROM evidence_items e JOIN businesses b ON b.id = e.business_id "
    "JOIN extractions x ON x.id = e.extraction_id "
    "WHERE b.project_id = ? AND b.deleted_at IS NULL AND x.extractor IN (?, ?, ?) AND {current} "
    "GROUP BY claim, e.status"
).replace("{current}", _CURRENT)
_STATUS_STATS: Final = {
    LatinoStatus.VERIFIED_LATINO_OWNED: "verified",
    LatinoStatus.HIGH_CONFIDENCE_LATINO_OWNED: "high_confidence",
    LatinoStatus.PROBABLE_LATINO_OWNED: "probable",
    LatinoStatus.SPANISH_MARKET_AFFINITY: "affinity",
    LatinoStatus.UNKNOWN: "unknown",
}


def summary(
    conn: sqlite3.Connection, project_id: str, ruleset: rules.Ruleset = rules.DEFAULT_RULESET
) -> dict[str, int]:
    """Evidence by track and status, and what the project's canonical businesses show."""
    counts: Counter[str] = Counter()
    track_stats = {
        LatinoTrack.OWNERSHIP: "ownership_evidence",
        LatinoTrack.MARKET: "market_evidence",
        LatinoTrack.CONTRADICTION: "contradictions",
    }
    for row in conn.execute(_COUNTS_SQL, (project_id, *EXTRACTORS)):
        rule = ruleset.claims.get(str(row["claim"]))
        if rule is None:
            continue
        if row["status"] == EvidenceStatus.ACTIVE:
            counts[track_stats[rule.track]] += row["n"]
        elif row["status"] in (EvidenceStatus.STALE, EvidenceStatus.REVOKED):
            counts[row["status"]] += row["n"]
    shown: dict[str, tuple[AssessmentMethod, LatinoStatus]] = {}
    for row in conn.execute(
        "SELECT a.business_id, a.status, a.method FROM latino_assessments a "
        "JOIN businesses b ON b.id = a.business_id WHERE b.project_id = ? "
        "AND b.deleted_at IS NULL AND b.merged_into_id IS NULL AND a.superseded_at IS NULL",
        (project_id,),
    ):
        method, status = AssessmentMethod(row["method"]), LatinoStatus(row["status"])
        if method is AssessmentMethod.MANUAL_REVIEW or row["business_id"] not in shown:
            shown[row["business_id"]] = (method, status)
    for method, status in shown.values():
        counts[_STATUS_STATS[status]] += 1
        counts["reviewed"] += method is AssessmentMethod.MANUAL_REVIEW
    return {
        name: counts[name]
        for name in (*track_stats.values(), "stale", "revoked", *_STATUS_STATS.values(), "reviewed")
    }


# ---- runs --------------------------------------------------------------------------------------


def plan_runs(
    conn: sqlite3.Connection, project_id: str | None, *, actor: str
) -> tuple[list[str], list[str]]:
    """Creates one queued job per project to evaluate. Returns (created, already open) job ids."""
    if project_id is not None:
        project = repository.get(conn, Project, project_id)
        if project.deleted_at is not None:
            raise repository.NotFoundError(f"project {project_id} has been deleted")
        projects = [project_id]
    else:
        projects = [
            row["id"]
            for row in conn.execute(
                "SELECT p.id FROM projects p WHERE p.deleted_at IS NULL AND EXISTS "
                "(SELECT 1 FROM businesses b WHERE b.project_id = p.id AND b.deleted_at IS NULL) "
                "ORDER BY p.created_at, p.id"
            )
        ]
        if not projects:
            raise LatinoError("there are no businesses to evaluate yet: discover or import some")
    created: list[str] = []
    listed: list[str] = []
    now = utc_now()
    with repository.atomic(conn):
        for pid in projects:
            open_job = conn.execute(
                "SELECT id FROM jobs WHERE kind = ? AND status IN ('queued', 'running', 'paused') "
                "AND json_extract(params_json, '$.project_id') = ? "
                "ORDER BY requested_at DESC LIMIT 1",
                (JOB_KIND, pid),
            ).fetchone()
            if open_job is not None:
                if project_id is not None:
                    raise LatinoError(
                        "an evaluation is already open for this project: resume or cancel it"
                    )
                listed.append(open_job["id"])
                continue
            job = Job(
                id=new_id(), project_id=pid, kind=JOB_KIND, status=JobStatus.QUEUED, priority=0,
                params_json={"project_id": pid, "extractor_version": rules.EXTRACTOR_VERSION,
                             "rules_version": rules.RULES_VERSION},
                idempotency_key=None, progress_done=0, progress_total=None, requested_at=now,
                started_at=None, finished_at=None, created_at=now, updated_at=now,
            )  # fmt: skip
            repository.insert(conn, job)
            repository.audit(conn, AuditAction.CREATE, EntityType.JOB, job.id, actor=actor)
            jobs.write_event(conn, job.id, None, "run_queued", EventLevel.INFO, {"project_id": pid})
            created.append(job.id)
    return created, listed


def _run_view(conn: sqlite3.Connection, job: Job) -> LatinoRun:
    state = jobs.latest_state(conn, job.id)
    stats = state.get("stats")
    counters = stats if isinstance(stats, dict) else {}
    project = repository.get(conn, Project, job.project_id)
    stage = state.get("stage")
    return LatinoRun(
        job_id=job.id,
        project_id=job.project_id,
        project_name=project.name,
        status=job.status,
        stage=stage if isinstance(stage, str) and stage else "queued",
        progress_done=job.progress_done,
        progress_total=job.progress_total,
        extractor_version=str(job.params_json.get("extractor_version") or rules.EXTRACTOR_VERSION),
        rules_version=str(job.params_json.get("rules_version") or rules.RULES_VERSION),
        stats=LatinoStats.model_validate(
            {name: repository._counter(counters.get(name)) for name in LatinoStats.model_fields}
        ),
        requested_at=job.requested_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        events=repository._run_events(conn, job.id),
        is_synthetic=project.is_synthetic,
    )


def get_latino_run(conn: sqlite3.Connection, job_id: str) -> LatinoRun:
    return _run_view(conn, jobs.live_job(conn, job_id, JOB_KIND))


def runs_by_id(conn: sqlite3.Connection, job_ids: Sequence[str]) -> LatinoRunPage:
    items = [get_latino_run(conn, job_id) for job_id in job_ids]
    return LatinoRunPage(items=items, next_cursor=None, total=len(items))


def list_latino_runs(conn: sqlite3.Connection, query: LatinoRunListQuery) -> LatinoRunPage:
    where, params = ["j.kind = ?"], list[object]([JOB_KIND])
    if query.project_id is not None:
        where.append("j.project_id = ?")
        params.append(query.project_id)
    (total,) = conn.execute(
        f"SELECT count(*) FROM jobs j WHERE {' AND '.join(where)}",  # noqa: S608
        params,
    ).fetchone()
    if query.cursor is not None:
        last_key, last_id = repository.decode_cursor(query.cursor, "latino")
        where.append("(j.requested_at, j.id) < (?, ?)")
        params += [last_key, last_id]
    rows = conn.execute(
        f"SELECT {repository._columns(Job, 'j')} FROM jobs j "  # noqa: S608
        f"WHERE {' AND '.join(where)} ORDER BY j.requested_at DESC, j.id DESC LIMIT ?",
        [*params, query.limit + 1],
    ).fetchall()
    page = rows[: query.limit]
    next_cursor = (
        repository.encode_cursor("latino", page[-1]["requested_at"], page[-1]["id"])
        if len(rows) > query.limit
        else None
    )
    return LatinoRunPage(
        items=[_run_view(conn, repository.from_row(Job, row)) for row in page],
        next_cursor=next_cursor,
        total=total,
    )


# ---- reads -------------------------------------------------------------------------------------


def _observed_evidence(
    conn: sqlite3.Connection, business_ids: Sequence[str]
) -> list[ObservedEvidence]:
    marks = ", ".join("?" * len(business_ids))
    rows = conn.execute(
        f"SELECT {repository._columns(EvidenceItem, 'f')}, {repository._PROVENANCE} "  # noqa: S608
        "FROM evidence_items f JOIN extractions x ON x.id = f.extraction_id "
        "JOIN raw_records r ON r.id = x.raw_record_id "
        "JOIN source_connectors sc ON sc.id = r.connector_id "
        f"WHERE f.business_id IN ({marks}) AND x.extractor IN (?, ?, ?) AND {_CURRENT} "
        "ORDER BY f.id",
        [*business_ids, *EXTRACTORS],
    ).fetchall()
    return [
        ObservedEvidence(
            value=repository.from_row(EvidenceItem, row),
            provenance=repository.from_row(Provenance, row, prefix="p_"),
        )
        for row in rows
    ]


def _view(assessment: LatinoAssessment) -> LatinoAssessmentView:
    details = assessment.details_json or {}
    notes = details.get("warnings")
    cannot = details.get("cannot_conclude")
    warnings = [
        LatinoNote(
            code=note["code"],
            detail=note["detail"] if isinstance(note.get("detail"), str) else None,
        )
        for note in (notes if isinstance(notes, list) else [])
        if isinstance(note, dict) and isinstance(note.get("code"), str)
    ]
    return LatinoAssessmentView(
        assessment=assessment,
        warnings=warnings,
        cannot_conclude=[code for code in (cannot if isinstance(cannot, list) else [])
                         if isinstance(code, str)],
    )  # fmt: skip


def _cluster(conn: sqlite3.Connection, business_id: str) -> tuple[Business, list[str]]:
    business = repository.get(conn, Business, business_id)
    if business.deleted_at is not None:
        raise repository.NotFoundError(f"business {business_id} has been deleted")
    root = business.merged_into_id or business.id
    merged = conn.execute(
        "SELECT id FROM businesses WHERE merged_into_id = ? AND deleted_at IS NULL ORDER BY id",
        (root,),
    ).fetchall()
    return business, [root, *(row["id"] for row in merged)]


_TRACK_ORDER: Final = {
    LatinoTrack.OWNERSHIP: 0,
    LatinoTrack.CONTRADICTION: 1,
    LatinoTrack.MARKET: 2,
}
_STRENGTH_ORDER: Final = {
    EvidenceStrength.STRONG: 0,
    EvidenceStrength.MODERATE: 1,
    EvidenceStrength.WEAK: 2,
}


def get_latino_detail(
    conn: sqlite3.Connection, business_id: str, ruleset: rules.Ruleset = rules.DEFAULT_RULESET
) -> LatinoDetail:
    business, members = _cluster(conn, business_id)
    root = members[0]
    automatic = _current(conn, [root], AssessmentMethod.RULES).get(root)
    manual = _current(conn, [root], AssessmentMethod.MANUAL_REVIEW).get(root)
    shown = manual or automatic
    links = (
        {link.evidence_item_id: link for link in _links(conn, [shown.id])[shown.id]}
        if shown
        else {}
    )
    now = utc_now()
    evidence: list[LatinoEvidence] = []
    for observed in _observed_evidence(conn, members):
        item = observed.value
        claim = item.value_json.get("claim") if isinstance(item.value_json, dict) else None
        rule = ruleset.claims.get(claim) if isinstance(claim, str) else None
        if rule is None or not isinstance(claim, str):
            continue
        if (link := links.get(item.id)) is not None:
            reliability, freshness = link.source_reliability, link.freshness
        else:
            reliability = rules.reliability(observed.provenance.connector_kind.value, ruleset)
            inactive = item.status in (EvidenceStatus.STALE, EvidenceStatus.REVOKED)
            freshness = 0.0 if inactive else rules.freshness(claim, item.observed_at, now, ruleset)
        evidence.append(
            LatinoEvidence(
                evidence=observed,
                claim=claim,
                track=rule.track,
                role=link.role if link else None,
                counted=bool(link and link.counted),
                source_reliability=reliability,
                freshness=freshness,
                freshness_label=rules.freshness_label(freshness),
                organizational=bool(link and link.organizational),
            )
        )
    evidence.sort(
        key=lambda entry: (
            _TRACK_ORDER[entry.track],
            _STRENGTH_ORDER[entry.evidence.value.strength],
            -entry.freshness,
            -entry.evidence.value.observed_at.timestamp(),
            entry.evidence.value.id,
        )
    )
    history = [
        _view(repository.from_row(LatinoAssessment, row))
        for row in conn.execute(
            f"SELECT {repository._columns(LatinoAssessment)} FROM latino_assessments "  # noqa: S608
            "WHERE business_id = ? ORDER BY assessed_at DESC, id DESC LIMIT ?",
            (root, MAX_HISTORY),
        )
    ]
    decisions = [
        repository.from_row(ReviewDecision, row)
        for row in conn.execute(
            f"SELECT {repository._columns(ReviewDecision)} FROM review_decisions "  # noqa: S608
            "WHERE entity_type = 'business' AND entity_id = ? AND decision IN (?, ?) "
            "ORDER BY decided_at DESC, id DESC LIMIT ?",
            (root, REVIEW_DECISION, WITHDRAW_DECISION, MAX_HISTORY),
        )
    ]
    reviewed = (manual.details_json or {}).get("reviewed_assessment_id") if manual else None
    return LatinoDetail(
        business_id=business.id,
        root_business_id=root,
        effective=_view(shown) if shown else None,
        automatic=_view(automatic) if automatic else None,
        review_outdated=manual is not None and reviewed != (automatic.id if automatic else None),
        evidence=evidence,
        history=history,
        decisions=decisions,
        rules_version=ruleset.version,
        extractor_version=rules.EXTRACTOR_VERSION,
    )


# ---- review ------------------------------------------------------------------------------------


def review(
    conn: sqlite3.Connection,
    request: LatinoReviewRequest,
    *,
    actor: str,
    ruleset: rules.Ruleset = rules.DEFAULT_RULESET,
) -> None:
    """Records a person's assessment, or withdraws it (`status` null). The automatic calculation is
    never touched, and a person cannot give a status the evidence does not reach at all: ownership
    needs ownership evidence, `verified` strong ownership evidence and market affinity market
    evidence."""
    reason = request.reason.strip()
    if not reason:
        raise LatinoError("a review needs a reason")
    business, members = _cluster(conn, request.business_id)
    if business.merged_into_id is not None:
        raise LatinoError("this record belongs to another business: review that business instead")
    automatic = _current(conn, [business.id], AssessmentMethod.RULES).get(business.id)
    manual = _current(conn, [business.id], AssessmentMethod.MANUAL_REVIEW).get(business.id)
    shown = manual or automatic
    previous = shown.status if shown is not None else LatinoStatus.UNKNOWN
    now = utc_now()
    stamp = format_ts(now)
    if request.status is None:
        if manual is None:
            raise LatinoError("there is no review to withdraw")
        with repository.atomic(conn):
            conn.execute(
                "UPDATE latino_assessments SET superseded_at = ? WHERE id = ?", (stamp, manual.id)
            )
            repository.insert(
                conn,
                ReviewDecision(
                    id=new_id(), entity_type=EntityType.BUSINESS, entity_id=business.id,
                    decision=WITHDRAW_DECISION, previous_value=previous.value,
                    new_value=(automatic.status if automatic else LatinoStatus.UNKNOWN).value,
                    reason=reason, actor=actor, decided_at=now,
                ),
            )  # fmt: skip
        return
    active = [
        ruleset.claims[str(item.value.value_json.get("claim"))]
        for item in _observed_evidence(conn, members)
        if isinstance(item.value.value_json, dict)
        and str(item.value.value_json.get("claim")) in ruleset.claims
        and item.value.status is EvidenceStatus.ACTIVE
    ]
    ownership = [rule for rule in active if rule.track is LatinoTrack.OWNERSHIP]
    status = request.status
    if status in rules.OWNERSHIP_STATUSES and not ownership:
        raise LatinoError(
            "an ownership status needs active ownership evidence: a declaration, a certification "
            "or an ownership directory"
        )
    if status is LatinoStatus.VERIFIED_LATINO_OWNED and not any(
        rule.strength is EvidenceStrength.STRONG for rule in ownership
    ):
        raise LatinoError(
            "verified needs strong ownership evidence: a declaration or certification"
        )
    if status is LatinoStatus.SPANISH_MARKET_AFFINITY and not any(
        rule.track is LatinoTrack.MARKET for rule in active
    ):
        raise LatinoError(
            "market affinity needs active market evidence, such as service in Spanish"
        )
    cannot = ["names_not_used"]
    if status is LatinoStatus.SPANISH_MARKET_AFFINITY:
        cannot.append("affinity_is_not_ownership")
    elif status is LatinoStatus.UNKNOWN:
        cannot.append("absence_is_not_evidence")
    details: JsonObject = {
        "reviewed_assessment_id": automatic.id if automatic else None,
        "automatic_status": automatic.status.value if automatic else None,
        "warnings": [],
        "cannot_conclude": list[JsonValue](cannot),
    }
    automatic_text = (
        f"the rules ({automatic.rules_version}) gave {automatic.status.value}"
        if automatic
        else "the rules had not evaluated it yet"
    )
    assessment = LatinoAssessment(
        id=new_id(), business_id=business.id, status=status,
        assessment_confidence=0.0 if status is LatinoStatus.UNKNOWN or automatic is None
        else automatic.assessment_confidence,
        method=AssessmentMethod.MANUAL_REVIEW, rules_version=ruleset.version,
        inference_run_id=None, rationale=f"Reviewed by {actor}: {status.value}; {automatic_text}.",
        limitations=" ".join(rules.CANNOT_CONCLUDE[code] for code in cannot), assessed_at=now,
        superseded_at=None, created_at=now, details_json=details,
    )  # fmt: skip
    cited = _links(conn, [automatic.id])[automatic.id] if automatic else []
    with repository.atomic(conn):
        if manual is not None:
            conn.execute(
                "UPDATE latino_assessments SET superseded_at = ? WHERE id = ?", (stamp, manual.id)
            )
        repository.insert(
            conn,
            assessment,
            *(link.model_copy(update={"assessment_id": assessment.id}) for link in cited),
            ReviewDecision(
                id=new_id(), entity_type=EntityType.BUSINESS, entity_id=business.id,
                decision=REVIEW_DECISION, previous_value=previous.value, new_value=status.value,
                reason=reason, actor=actor, decided_at=now,
            ),
        )  # fmt: skip


# ---- model candidates --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ModelCandidate:
    """A claim a local model proposes about a crawled page, with the quote it rests on."""

    claim: str
    quote: str


@dataclass(frozen=True, slots=True)
class CandidateOutcome:
    accepted: list[str]
    #: (index of the candidate, reason) for every candidate that was not accepted.
    rejected: list[tuple[int, str]]


def ingest_model_candidates(
    conn: sqlite3.Connection,
    *,
    inference_run_id: str,
    raw_record_id: str,
    business_id: str,
    candidates: Sequence[ModelCandidate],
) -> CandidateOutcome:
    """Stores the model's candidates the page itself confirms (`rules.verify_candidate`); the rest
    are rejected with the reason. The model adjudicates nothing: each accepted claim is evidence
    only because its quote is in the page and the patterns read it as that claim."""
    repository.get(conn, InferenceRun, inference_run_id)
    row = conn.execute(
        "SELECT r.payload_json, r.acquired_at, sc.key FROM raw_records r "
        "JOIN source_connectors sc ON sc.id = r.connector_id WHERE r.id = ?",
        (raw_record_id,),
    ).fetchone()
    if row is None:
        raise repository.NotFoundError(f"raw record {raw_record_id} not found")
    if row["key"] != CRAWL_CONNECTOR_KEY:
        raise LatinoError("model candidates are only read from crawled pages")
    page = _object(row["payload_json"])
    now = utc_now()
    existing = conn.execute(
        "SELECT id FROM extractions WHERE raw_record_id = ? AND extractor = ? "
        "AND extractor_version = ?",
        (raw_record_id, EXTRACTOR_MODEL, rules.EXTRACTOR_VERSION),
    ).fetchone()
    extraction = Extraction(
        id=existing["id"] if existing else new_id(), raw_record_id=raw_record_id,
        extractor=EXTRACTOR_MODEL, extractor_version=rules.EXTRACTOR_VERSION, extracted_at=now,
    )  # fmt: skip
    stored = {
        found["claim"]: found["id"]
        for found in conn.execute(
            "SELECT id, json_extract(value_json, '$.claim') AS claim FROM evidence_items "
            "WHERE extraction_id = ? AND business_id = ?",
            (extraction.id, business_id),
        )
    }
    accepted: list[str] = []
    rejected: list[tuple[int, str]] = []
    records: list[Record] = [] if existing else [extraction]
    for index, candidate in enumerate(candidates):
        result = rules.verify_candidate(candidate.claim, candidate.quote, page)
        if isinstance(result, str):
            rejected.append((index, result))
            continue
        if result.claim in stored:
            accepted.append(stored[result.claim])
            continue
        finding = replace(result, detail={**result.detail, "inference_run_id": inference_run_id})
        item = evidence_item(finding, business_id, extraction.id,
                             datetime.fromisoformat(row["acquired_at"]), now)  # fmt: skip
        stored[result.claim] = item.id
        records.append(item)
        accepted.append(item.id)
    if len(records) > (0 if existing else 1):
        with repository.atomic(conn):
            repository.insert(conn, *records)
    return CandidateOutcome(accepted, rejected)
