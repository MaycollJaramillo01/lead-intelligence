"""Store side of entity resolution (phase 06): the only SQL the resolution engine runs.

- Ingest: each discovery candidate becomes one record business with normalised observations, each
  through a versioned extraction over its raw record; pages crawled from its website add the
  emails, phones and social profiles they publish.
- Pairs: what `matching.compare` decided, with its evidence, stored so it can be explained.
- Clusters: `merged_into_id` always points at the root. They are recomputed from the stored pairs
  (manual decisions first), so a merge, an unmerge and a new rules version go through one path.
- Canonical values: one observation per field per cluster, by policy unless a person chose.

Nothing here updates or deletes an observation or a raw record.
"""

import re
import sqlite3
from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

import orjson

from research_engine import jobs, repository
from research_engine.domain import (
    AuditAction,
    Business,
    BusinessClassification,
    BusinessLocation,
    BusinessMerge,
    BusinessName,
    BusinessStatus,
    CanonicalChoiceRequest,
    CanonicalField,
    CanonicalValue,
    ClassificationKind,
    ContactOrigin,
    ContactPoint,
    ContactType,
    ContactValidation,
    DiscoveryCandidate,
    EntityRecord,
    EntityType,
    EventLevel,
    Extraction,
    FieldComparison,
    Job,
    JobStatus,
    JsonObject,
    LeadStage,
    MatchAction,
    MatchActionRequest,
    MatchCandidate,
    MatchDetail,
    MatchListItem,
    MatchListQuery,
    MatchOutcome,
    MatchPage,
    MatchReason,
    MatchSide,
    MatchStatus,
    NameKind,
    Project,
    ReasonDirection,
    Record,
    ResolutionRun,
    ResolutionRunListQuery,
    ResolutionRunPage,
    ResolutionStats,
    ReviewDecision,
    SelectionMethod,
    ValidationStatus,
    Website,
    can_transition,
    format_ts,
    new_id,
    sort_key,
    utc_now,
)
from research_engine.resolution import matching, normalize
from research_engine.resolution.normalize import NORMALIZER_VERSION, Presence

JOB_KIND: Final = "resolution"
ACTOR: Final = "resolution-engine"
EXTRACTOR_CANDIDATE: Final = "resolution.candidate"
EXTRACTOR_SITE: Final = "resolution.site"
#: The key `crawl.engine` registers its connector under (not imported: it loads the browser stack).
CRAWL_CONNECTOR_KEY: Final = "web.crawler"
#: Version of the canonical value policy (`policy_choice`).
POLICY_VERSION: Final = "1.0.0"
WRITE_BATCH: Final = 500
MAX_REASON_CODES: Final = 8
MAX_MERGES_SHOWN: Final = 100

_ADDRESS_KEYS: Final = ("address", "streetaddress", "street", "address1", "addressline1",
                        "fulladdress", "direccion")  # fmt: skip
_EMAIL_KEYS: Final = ("email", "emailaddress", "email1", "correo", "correoelectronico")
_LATITUDE_KEYS: Final = ("latitude", "lat")
_LONGITUDE_KEYS: Final = ("longitude", "lng", "lon", "long")
#: How far a source is trusted when observations disagree (higher wins).
SOURCE_TRUST: Final = {
    "official_api": 3, "public_registry": 3, "directory": 2, "public_dataset": 2,
    "platform_api": 2, "business_website": 2, "web_search": 1, "file_import": 1,
    "manual_seed": 1, "synthetic": 1,
}  # fmt: skip
_SOURCE_CONFIDENCE: Final = {3: 0.9, 2: 0.8, 1: 0.7}
#: Observations of the newest extraction of each extractor over a raw record. Older versions
#: stay stored (history) but no longer describe the business.
_CURRENT: Final = (
    "NOT EXISTS (SELECT 1 FROM extractions x2 WHERE x2.raw_record_id = x.raw_record_id "
    "AND x2.extractor = x.extractor AND x2.extracted_at > x.extracted_at)"
)
_ENGINE_STATUS: Final = {
    MatchOutcome.AUTO_MERGE: MatchStatus.MERGED,
    MatchOutcome.MANUAL_REVIEW: MatchStatus.PENDING,
    MatchOutcome.DISTINCT: MatchStatus.SEPARATE,
}
DEFAULT_REASONS: Final = {
    MatchAction.MERGE: "Merged after comparing both records side by side.",
    MatchAction.KEEP_SEPARATE: "Kept separate after comparing both records side by side.",
    MatchAction.DEFER: "Deferred for a later review.",
    MatchAction.UNDO: "Undid the latest decision on this pair.",
}
DEFAULT_CHOICE_REASON: Final = "Chosen by a person in the business inspector."


class ResolutionError(ValueError):
    """A resolution request that cannot be carried out as asked; the message says why."""


def _json(value: object) -> str:
    return orjson.dumps(value, option=orjson.OPT_SORT_KEYS).decode()


def _object(text: str | None) -> JsonObject:
    loaded = orjson.loads(text) if text else None
    return loaded if isinstance(loaded, dict) else {}


def _batches[T](items: Sequence[T], size: int = WRITE_BATCH) -> Iterable[Sequence[T]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


# ---- ingest ------------------------------------------------------------------------------------


def payload_text(payload: Mapping[str, object], keys: Sequence[str]) -> str | None:
    """A value from a raw payload by column name, ignoring case, spaces and punctuation."""
    folded = {
        re.sub(r"[^a-z0-9]", "", str(key).casefold()): value for key, value in payload.items()
    }
    for key in keys:
        value = folded.get(key)
        if isinstance(value, bool) or not isinstance(value, str | int | float):
            continue
        text, _ = normalize.clean_text(value)
        if text is not None:
            return text
    return None


@dataclass(slots=True)
class _Observations:
    """Observation rows of one extraction, sharing its business, moment and provenance."""

    business_id: str
    extraction_id: str
    observed_at: datetime
    now: datetime
    origin: ContactOrigin
    confidence: float
    records: list[Record] = field(default_factory=list)

    def base(self) -> dict[str, Any]:
        return {
            "business_id": self.business_id,
            "extraction_id": self.extraction_id,
            "observed_at": self.observed_at,
            "created_at": self.now,
        }

    def contact(
        self,
        contact_type: ContactType,
        raw: str,
        normalized: str | None,
        *,
        valid: bool,
        primary: bool = False,
        platform: str | None = None,
        context: str | None = None,
        confidence: float | None = None,
        detail: JsonObject | None = None,
    ) -> None:
        status = ValidationStatus.STRUCTURALLY_VALID if valid else ValidationStatus.INVALID
        contact = ContactPoint(
            id=new_id(),
            business_id=self.business_id,
            location_id=None,
            decision_maker_id=None,
            contact_type=contact_type,
            value_raw=raw,
            value_normalized=normalized if valid else None,
            origin=self.origin,
            platform=platform,
            web_page_id=None,
            role_or_department=None,
            published_context=context,
            is_primary=primary and valid,
            validation_status=status,
            validated_at=self.now,
            do_not_contact=False,
            suppression_reason=None,
            confidence=self.confidence if confidence is None else confidence,
            extraction_id=self.extraction_id,
            first_seen_at=self.observed_at,
            last_seen_at=self.observed_at,
            created_at=self.now,
            updated_at=self.now,
            deleted_at=None,
            deleted_reason=None,
        )
        self.records += [
            contact,
            ContactValidation(
                id=new_id(),
                contact_point_id=contact.id,
                status=status,
                method="structure",
                validator_version=NORMALIZER_VERSION,
                detail_json=detail,
                validated_at=self.now,
                expires_at=None,
            ),
        ]


def _contact_key(contact_type: ContactType, value: str | None) -> str | None:
    if value is None:
        return None
    if contact_type is ContactType.SOCIAL_PROFILE:
        social = normalize.social_profile(value)
        return f"social:{social.platform}:{social.handle}" if social.handle else None
    return f"{contact_type.value}:{value.casefold()}"


def candidate_observations(
    candidate: DiscoveryCandidate,
    payload: Mapping[str, object],
    source_kind: str,
    business_id: str,
    extraction_id: str,
    now: datetime,
    *,
    primary: bool,
) -> list[Record]:
    """The normalised observations of one candidate. Raw values are stored as they arrived."""
    trust = SOURCE_TRUST.get(source_kind, 1)
    imported = source_kind in ("file_import", "manual_seed")
    obs = _Observations(
        business_id,
        extraction_id,
        candidate.discovered_at,
        now,
        ContactOrigin.USER_IMPORT if imported else ContactOrigin.PUBLISHED,
        _SOURCE_CONFIDENCE[trust],
    )
    base = obs.base()
    name = normalize.business_name(candidate.name_raw)
    kind = NameKind.DBA if name.dba else NameKind.LEGAL if name.legal_suffix else NameKind.TRADE
    obs.records.append(
        BusinessName(id=new_id(), name_raw=candidate.name_raw,
                     name_normalized=name.key or sort_key(candidate.name_raw), name_kind=kind,
                     is_primary=primary, confidence=obs.confidence, **base)
    )  # fmt: skip
    if name.legal:
        legal = normalize.business_name(name.legal)
        obs.records.append(
            BusinessName(id=new_id(), name_raw=candidate.name_raw,
                         name_normalized=legal.key or sort_key(name.legal),
                         name_kind=NameKind.LEGAL, is_primary=False, confidence=obs.confidence,
                         **base)
        )  # fmt: skip

    street_line = payload_text(payload, _ADDRESS_KEYS)
    address = normalize.us_address(
        street_line,
        locality_value=candidate.locality,
        region=candidate.region,
        postal_code=candidate.postal_code,
        country_code=candidate.country_code,
        latitude=payload_text(payload, _LATITUDE_KEYS),
        longitude=payload_text(payload, _LONGITUDE_KEYS),
    )
    raw_parts = [part for part in (street_line, candidate.locality, candidate.region,
                                   candidate.postal_code) if part]  # fmt: skip
    if address.status is Presence.PRESENT and (raw_parts or address.latitude is not None):
        obs.records.append(
            BusinessLocation(
                id=new_id(),
                address_raw=", ".join(raw_parts) or None,
                street=address.street,
                unit=address.unit,
                locality=address.locality,
                region=address.region,
                postal_code=address.postal_code,
                country_code=address.country_code,
                latitude=address.latitude,
                longitude=address.longitude,
                is_primary=primary,
                confidence=obs.confidence,
                **base,
            )
        )

    site_raw = candidate.website_url or candidate.domain
    site = normalize.website(site_raw)
    if site_raw and site.status is Presence.PRESENT and site.url and site.host:
        if not site.is_platform:
            obs.records.append(
                Website(id=new_id(), url_raw=site_raw, url_normalized=site.url, domain=site.host,
                        is_primary=primary, confidence=obs.confidence, **base)
            )  # fmt: skip
        elif (social := normalize.social_profile(site_raw)).handle:
            obs.contact(ContactType.SOCIAL_PROFILE, site_raw, social.url, valid=True,
                        primary=primary, platform=social.platform)  # fmt: skip

    if candidate.phone_raw:
        number = normalize.phone(candidate.phone_raw)
        if number.status in (Presence.PRESENT, Presence.INVALID):
            obs.contact(
                ContactType.PHONE,
                candidate.phone_raw,
                number.e164,
                valid=number.status is Presence.PRESENT,
                primary=primary,
                context=f"extension {number.extension}" if number.extension else None,
                detail={"kind": number.kind.value if number.kind else None,
                        "extension": number.extension},
            )  # fmt: skip
    if email_raw := payload_text(payload, _EMAIL_KEYS):
        found = normalize.email(email_raw)
        if found.status in (Presence.PRESENT, Presence.INVALID):
            obs.contact(
                ContactType.EMAIL,
                email_raw,
                found.address,
                valid=found.status is Presence.PRESENT,
                primary=primary,
                detail={"role": found.is_role, "free_provider": found.is_free_provider},
            )
    if candidate.category_raw:
        label = normalize.category(candidate.category_raw)
        if label.label:
            obs.records.append(
                BusinessClassification(
                    id=new_id(), kind=ClassificationKind.CATEGORY, label_raw=candidate.category_raw,
                    taxonomy_key=label.taxonomy_key, taxonomy_version=label.taxonomy_version,
                    is_primary=primary, confidence=obs.confidence, **base,
                )
            )  # fmt: skip
    return obs.records


def site_observations(
    page: Mapping[str, object],
    page_url: str,
    business_id: str,
    extraction_id: str,
    observed_at: datetime,
    now: datetime,
    seen: set[str],
) -> list[Record]:
    """Emails, phones and social profiles a crawled page of the business's own site publishes.

    Only values not already observed for the business are added: a footer repeated on every page
    is one observation, with the first page that showed it as its provenance.
    """
    obs = _Observations(business_id, extraction_id, observed_at, now, ContactOrigin.PUBLISHED, 0.6)

    def findings(name: str) -> Iterable[tuple[str, str]]:
        items = page.get(name)
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict) and isinstance(item.get("value"), str):
                yield str(item["value"]), str(item.get("origin") or "text")

    for value, origin in findings("emails"):
        address = normalize.email(value)
        key = _contact_key(ContactType.EMAIL, address.address)
        if address.status is Presence.PRESENT and key and key not in seen:
            seen.add(key)
            where = "mailto link" if origin == "mailto" else "page text"
            obs.contact(
                ContactType.EMAIL,
                value,
                address.address,
                valid=True,
                context=f"{where} on {page_url}",
                confidence=0.7 if origin == "mailto" else 0.5,
                detail={"role": address.is_role, "free_provider": address.is_free_provider},
            )
    for value, origin in findings("phones"):
        number = normalize.phone(value)
        key = _contact_key(ContactType.PHONE, number.e164)
        if number.status is Presence.PRESENT and key and key not in seen:
            seen.add(key)
            where = "tel link" if origin == "tel_link" else "page text"
            obs.contact(ContactType.PHONE, value, number.e164, valid=True,
                        context=f"{where} on {page_url}",
                        confidence=0.7 if origin == "tel_link" else 0.5,
                        detail={"kind": number.kind.value if number.kind else None,
                                "extension": number.extension})  # fmt: skip
    links = page.get("links")
    for link in links if isinstance(links, list) else []:
        url = link.get("url") if isinstance(link, dict) else None
        if not isinstance(url, str):
            continue
        social = normalize.social_profile(url)
        key = _contact_key(ContactType.SOCIAL_PROFILE, social.url)
        if social.url and key and key not in seen:
            seen.add(key)
            obs.contact(ContactType.SOCIAL_PROFILE, url, social.url, valid=True,
                        platform=social.platform, context=f"link on {page_url}")  # fmt: skip
    return obs.records


def site_runs_by_candidate(conn: sqlite3.Connection, project_id: str) -> dict[str, list[str]]:
    """Crawl site runs of each candidate: the pages of the website the candidate listed."""
    runs: dict[str, list[str]] = defaultdict(list)
    for row in conn.execute(
        "SELECT r.id, json_extract(r.effective_config_json, '$.candidate_id') AS candidate_id "
        "FROM source_runs r JOIN source_connectors sc ON sc.id = r.connector_id "
        "WHERE r.project_id = ? AND sc.key = ? ORDER BY r.created_at, r.id",
        (project_id, CRAWL_CONNECTOR_KEY),
    ):
        if isinstance(row["candidate_id"], str):
            runs[row["candidate_id"]].append(row["id"])
    return runs


def count_candidates(conn: sqlite3.Connection, project_id: str, up_to: str | None = None) -> int:
    """Candidates of a project, or only those up to an id (ids sort by creation)."""
    sql = "SELECT count(*) FROM discovery_candidates WHERE project_id = ?"
    params: list[object] = [project_id]
    if up_to is not None:
        sql += " AND id <= ?"
        params.append(up_to)
    (count,) = conn.execute(sql, params).fetchone()
    return int(count)


@dataclass(frozen=True, slots=True)
class IngestBatch:
    last_id: str | None
    candidates: int
    created: int
    observations: int


_OBSERVATION_RECORDS: Final = (BusinessName, BusinessLocation, Website, ContactPoint,
                               BusinessClassification)  # fmt: skip


def ingest_batch(
    conn: sqlite3.Connection,
    project_id: str,
    after: str | None,
    limit: int,
    site_runs: Mapping[str, Sequence[str]],
) -> IngestBatch:
    """The next `limit` candidates after `after` (ids are UUIDv7), in one transaction.

    Idempotent: a candidate keeps its record business, and an extraction of the same version over
    the same raw record is never written twice, so a resumed or repeated run adds nothing new.
    """
    rows = conn.execute(
        f"SELECT {repository._columns(DiscoveryCandidate, 'dc')}, "  # noqa: S608 - model names
        "rr.payload_json AS p_payload, sc.kind AS p_kind, er.business_id AS p_business_id, "
        "EXISTS (SELECT 1 FROM extractions x WHERE x.raw_record_id = dc.raw_record_id "
        "AND x.extractor = ? AND x.extractor_version = ?) AS p_extracted "
        "FROM discovery_candidates dc JOIN raw_records rr ON rr.id = dc.raw_record_id "
        "JOIN source_connectors sc ON sc.id = dc.connector_id "
        "LEFT JOIN entity_records er ON er.candidate_id = dc.id "
        "WHERE dc.project_id = ? AND dc.id > ? ORDER BY dc.id LIMIT ?",
        (EXTRACTOR_CANDIDATE, NORMALIZER_VERSION, project_id, after or "", limit),
    ).fetchall()
    now = utc_now()
    created = observations = 0
    with repository.atomic(conn):
        for row in rows:
            candidate = repository.from_row(DiscoveryCandidate, row)
            business_id: str | None = row["p_business_id"]
            records: list[Record] = []
            new = business_id is None
            if business_id is None:
                display = candidate.name_raw.strip()[:300]
                business = Business(
                    id=new_id(), project_id=project_id, display_name=display,
                    name_sort_key=sort_key(display), status=BusinessStatus.CANDIDATE,
                    lead_stage=LeadStage.NOT_CONTACTED, merged_into_id=None,
                    first_seen_at=candidate.discovered_at, last_seen_at=candidate.discovered_at,
                    created_at=now, updated_at=now, deleted_at=None, deleted_reason=None,
                )  # fmt: skip
                business_id = business.id
                records += [
                    business,
                    EntityRecord(candidate_id=candidate.id, business_id=business.id,
                                 project_id=project_id, created_at=now),
                ]  # fmt: skip
                created += 1
            if not row["p_extracted"]:
                extraction = Extraction(
                    id=new_id(), raw_record_id=candidate.raw_record_id,
                    extractor=EXTRACTOR_CANDIDATE, extractor_version=NORMALIZER_VERSION,
                    extracted_at=now,
                )  # fmt: skip
                records.append(extraction)
                records += candidate_observations(
                    candidate, _object(row["p_payload"]), row["p_kind"], business_id,
                    extraction.id, now, primary=new,
                )  # fmt: skip
            if run_ids := site_runs.get(candidate.id):
                records += _site_records(conn, run_ids, business_id, records, now, new)
            repository.insert(conn, *records)
            observations += sum(isinstance(record, _OBSERVATION_RECORDS) for record in records)
    return IngestBatch(rows[-1]["id"] if rows else None, len(rows), created, observations)


def _site_records(
    conn: sqlite3.Connection,
    run_ids: Sequence[str],
    business_id: str,
    pending: Sequence[Record],
    now: datetime,
    new: bool,
) -> list[Record]:
    placeholders = ", ".join("?" * len(run_ids))
    pages = conn.execute(
        "SELECT rr.id, rr.payload_json, rr.source_url, rr.acquired_at, "  # noqa: S608
        "EXISTS (SELECT 1 FROM extractions x WHERE x.raw_record_id = rr.id AND x.extractor = ? "
        "AND x.extractor_version = ?) AS extracted "
        "FROM crawl_urls cu JOIN raw_records rr ON rr.id = cu.raw_record_id "
        f"WHERE cu.source_run_id IN ({placeholders}) AND cu.status = 'fetched' "
        "AND cu.target = 'page' AND cu.duplicate_of_id IS NULL ORDER BY cu.id",
        (EXTRACTOR_SITE, NORMALIZER_VERSION, *run_ids),
    ).fetchall()
    # a page answered 304 points at the record of an earlier crawl: extract it once
    todo = list({page["id"]: page for page in pages if not page["extracted"]}.values())
    if not todo:
        return []
    seen: set[str] = set()
    existing = [] if new else conn.execute(
        "SELECT contact_type, value_normalized FROM contact_points "
        "WHERE business_id = ? AND deleted_at IS NULL AND value_normalized IS NOT NULL",
        (business_id,),
    ).fetchall()  # fmt: skip
    for contact_type, value in existing:
        if key := _contact_key(ContactType(contact_type), value):
            seen.add(key)
    for record in pending:
        if isinstance(record, ContactPoint) and (
            key := _contact_key(record.contact_type, record.value_normalized)
        ):
            seen.add(key)
    records: list[Record] = []
    for page in todo:
        extraction = Extraction(
            id=new_id(), raw_record_id=page["id"], extractor=EXTRACTOR_SITE,
            extractor_version=NORMALIZER_VERSION, extracted_at=now,
        )  # fmt: skip
        records.append(extraction)
        records += site_observations(
            _object(page["payload_json"]), page["source_url"] or "", business_id, extraction.id,
            datetime.fromisoformat(page["acquired_at"]), now, seen,
        )  # fmt: skip
    return records


# ---- the records to resolve --------------------------------------------------------------------

_SOURCE_JOIN: Final = (
    "JOIN businesses b ON b.id = o.business_id JOIN extractions x ON x.id = o.extraction_id "
)
_SOURCE_WHERE: Final = f"WHERE b.project_id = ? AND b.deleted_at IS NULL AND {_CURRENT}"


def business_ids(conn: sqlite3.Connection, project_id: str) -> list[str]:
    return [
        row["id"]
        for row in conn.execute(
            "SELECT id FROM businesses WHERE project_id = ? AND deleted_at IS NULL ORDER BY id",
            (project_id,),
        )
    ]


def load_entities(conn: sqlite3.Connection, project_id: str) -> list[matching.EntityInput]:
    """Every live business of a project as the raw values of its current observations.

    Matching normalises them again with this build's normaliser, so a comparison never mixes
    values normalised by different versions.
    """
    names: dict[str, list[str]] = defaultdict(list)
    addresses: dict[str, list[matching.AddressInput]] = defaultdict(list)
    websites: dict[str, list[str]] = defaultdict(list)
    phones: dict[str, list[str]] = defaultdict(list)
    emails: dict[str, list[str]] = defaultdict(list)
    categories: dict[str, list[str]] = defaultdict(list)
    sources: dict[str, list[str]] = defaultdict(list)

    def rows(select: str, extra: str = "") -> Iterable[sqlite3.Row]:
        return conn.execute(f"{select} {_SOURCE_JOIN}{extra}{_SOURCE_WHERE} ORDER BY o.id",
                            (project_id,))  # fmt: skip

    for row in rows("SELECT o.business_id, o.name_raw FROM business_names o"):
        names[row[0]].append(row[1])
    for row in rows(
        "SELECT o.business_id, o.address_raw, o.street, o.unit, o.locality, o.region, "
        "o.postal_code, o.latitude, o.longitude FROM business_locations o"
    ):
        addresses[row[0]].append(matching.AddressInput(*row[1:]))
    for row in rows("SELECT o.business_id, o.url_raw FROM websites o"):
        websites[row[0]].append(row[1])
    for row in conn.execute(
        f"SELECT o.business_id, o.contact_type, o.value_raw FROM contact_points o {_SOURCE_JOIN}"  # noqa: S608
        f"{_SOURCE_WHERE} AND o.deleted_at IS NULL AND o.contact_type IN ('phone', 'email') "
        "ORDER BY o.id",
        (project_id,),
    ):
        (phones if row[1] == "phone" else emails)[row[0]].append(row[2])
    for row in rows("SELECT o.business_id, o.label_raw FROM business_classifications o"):
        categories[row[0]].append(row[1])
    for row in rows(
        "SELECT o.business_id, sc.key, sc.kind, r.external_id FROM business_names o",
        "JOIN raw_records r ON r.id = x.raw_record_id "
        "JOIN source_connectors sc ON sc.id = r.connector_id ",
    ):
        if row[3] and row[2] in matching.STABLE_ID_KINDS:
            sources[row[0]].append(f"{row[1]}:{row[3]}")

    def unique(values: Iterable[str]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(values))

    return [
        matching.EntityInput(
            business_id=business_id,
            names=unique(names[business_id]),
            addresses=tuple(dict.fromkeys(addresses[business_id])),
            websites=unique(websites[business_id]),
            phones=unique(phones[business_id]),
            emails=unique(emails[business_id]),
            categories=unique(categories[business_id]),
            source_ids=unique(sources[business_id]),
        )
        for business_id in business_ids(conn, project_id)
    ]


# ---- pairs -------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PairCounts:
    inserted: int
    updated: int
    unchanged: int
    retired: int


def persist_pairs(
    conn: sqlite3.Connection,
    project_id: str,
    comparisons: Mapping[tuple[str, str], matching.Comparison],
    job_id: str | None,
) -> PairCounts:
    """Stores what the rules decided. A pair a person decided keeps its status; its evidence is
    refreshed. A pair the rules no longer compare is retired as distinct, with that reason."""
    existing = {
        (row["left_business_id"], row["right_business_id"]): row
        for row in conn.execute(
            "SELECT id, left_business_id, right_business_id, rules_version, score, outcome, risk, "
            "hard_conflict, status, is_manual, features_json FROM match_candidates "
            "WHERE project_id = ?",
            (project_id,),
        )
    }
    now = utc_now()
    stamp = format_ts(now)
    inserted = updated = unchanged = retired = 0
    for batch in _batches(sorted(comparisons.items())):
        with repository.atomic(conn):
            for pair, comparison in batch:
                row = existing.get(pair)
                if row is None and not comparison.worth_storing:
                    continue
                status = _ENGINE_STATUS[comparison.outcome]
                if row is None:
                    repository.insert(
                        conn,
                        MatchCandidate(
                            id=new_id(), project_id=project_id, left_business_id=pair[0],
                            right_business_id=pair[1], rules_version=matching.RULES_VERSION,
                            score=comparison.score, outcome=comparison.outcome,
                            risk=comparison.risk, hard_conflict=comparison.hard_conflict,
                            status=status, is_manual=False,
                            features_json=comparison.features_json(),
                            reasons_json=comparison.reasons_json(), job_id=job_id,
                            decided_by=ACTOR, created_at=now, updated_at=now,
                        ),
                    )  # fmt: skip
                    inserted += 1
                    continue
                if row["is_manual"]:
                    status = MatchStatus(row["status"])
                current = (
                    row["rules_version"],
                    row["score"],
                    row["outcome"],
                    row["risk"],
                    bool(row["hard_conflict"]),
                    row["status"],
                )
                wanted = (
                    matching.RULES_VERSION,
                    comparison.score,
                    comparison.outcome.value,
                    comparison.risk.value,
                    comparison.hard_conflict,
                    status.value,
                )
                if current == wanted:
                    unchanged += 1
                    continue
                conn.execute(
                    "UPDATE match_candidates SET rules_version = ?, score = ?, outcome = ?, "
                    "risk = ?, hard_conflict = ?, status = ?, features_json = ?, "
                    "reasons_json = ?, job_id = ?, updated_at = ? WHERE id = ?",
                    (matching.RULES_VERSION, comparison.score, comparison.outcome.value,
                     comparison.risk.value, int(comparison.hard_conflict), status.value,
                     _json(comparison.features_json()), _json(comparison.reasons_json()), job_id,
                     stamp, row["id"]),
                )  # fmt: skip
                updated += 1
    stale = [
        row
        for pair, row in sorted(existing.items())
        if pair not in comparisons and not row["is_manual"]
        and (row["outcome"], row["status"], row["rules_version"])
        != ("distinct", "separate", matching.RULES_VERSION)
    ]  # fmt: skip
    for stale_rows in _batches(stale):
        with repository.atomic(conn):
            for row in stale_rows:
                conn.execute(
                    "UPDATE match_candidates SET rules_version = ?, outcome = 'distinct', "
                    "risk = 'low', hard_conflict = 0, status = 'separate', features_json = ?, "
                    "reasons_json = ?, job_id = ?, updated_at = ? WHERE id = ?",
                    (matching.RULES_VERSION,
                     _json({"rules_version": matching.RULES_VERSION, "blocking_keys": [],
                            "fields": [], "previous": _object(row["features_json"])}),
                     _json({"reasons": [{"code": "no_longer_compared", "direction": "rule",
                                         "weight": 0.0,
                                         "detail": "these rules no longer pair the two records"}]}),
                     job_id, stamp, row["id"]),
                )  # fmt: skip
                retired += 1
    return PairCounts(inserted, updated, unchanged, retired)


# ---- clusters ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClusterCounts:
    clusters: int
    merged_records: int
    merges: int
    unmerges: int
    refused: int
    canonical_changes: int


def recluster(
    conn: sqlite3.Connection, project_id: str, *, actor: str, reason: str
) -> ClusterCounts:
    """Recomputes every cluster of a project from its stored pairs and writes the differences.

    People's merges are applied first and always; automatic merges follow unless they would join
    records a person kept apart (or a hard conflict separates), in which case the pair goes back to
    the review queue with the reason. Every change is audited and kept in `business_merges`.
    """
    ids = business_ids(conn, project_id)
    state = {
        row["id"]: (row["status"], row["merged_into_id"])
        for row in conn.execute(
            "SELECT id, status, merged_into_id FROM businesses "
            "WHERE project_id = ? AND deleted_at IS NULL",
            (project_id,),
        )
    }
    pairs = conn.execute(
        "SELECT id, left_business_id, right_business_id, score, outcome, status, is_manual, "
        "hard_conflict, rules_version, features_json, reasons_json FROM match_candidates "
        "WHERE project_id = ? AND (status = 'merged' OR hard_conflict = 1 "
        "OR (status = 'separate' AND is_manual = 1)) ORDER BY id",
        (project_id,),
    ).fetchall()
    merges: list[matching.Edge] = []
    separations: list[tuple[str, str]] = []
    by_pair: dict[tuple[str, str], sqlite3.Row] = {}
    for row in pairs:
        pair = (row["left_business_id"], row["right_business_id"])
        by_pair[pair] = row
        if row["status"] == "merged":
            merges.append(matching.Edge(pair[0], pair[1], row["score"], bool(row["is_manual"])))
        elif row["is_manual"] or row["hard_conflict"]:
            separations.append(pair)
    roots, refused = matching.clusters(ids, merges, separations)
    now = utc_now()
    stamp = format_ts(now)

    with repository.atomic(conn):
        for pair in sorted(refused):
            row = by_pair[pair]
            reasons = _object(row["reasons_json"])
            listed = reasons.get("reasons")
            items = list(listed) if isinstance(listed, list) else []
            items.append({"code": "cluster_conflict", "direction": "rule", "weight": 0.0,
                          "detail": "merging would join records that were kept apart"})  # fmt: skip
            conn.execute(
                "UPDATE match_candidates SET outcome = 'manual_review', risk = 'high', "
                "status = 'pending', reasons_json = ?, updated_at = ? WHERE id = ?",
                (_json({"reasons": items}), stamp, row["id"]),
            )

    incident: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for pair, row in by_pair.items():
        if row["status"] == "merged" and pair not in refused:
            incident[pair[0]].append(row)
            incident[pair[1]].append(row)
    unmerges = [b for b in ids if roots[b] == b and state[b][0] == "merged"]
    joins = [b for b in ids if roots[b] != b and (state[b][1] or b) != roots[b]]

    for batch in _batches(unmerges):
        with repository.atomic(conn):
            for business_id in batch:
                previous = _close_merge(conn, business_id, actor, reason, stamp)
                target = previous if previous in ("candidate", "active", "closed") else "candidate"
                conn.execute(
                    "UPDATE businesses SET status = ?, merged_into_id = NULL, updated_at = ? "
                    "WHERE id = ?",
                    (target, stamp, business_id),
                )
                repository.audit(
                    conn, AuditAction.UNMERGE, EntityType.BUSINESS, business_id, actor=actor,
                    detail={"from": state[business_id][1], "status": target, "reason": reason},
                )  # fmt: skip
    for batch in _batches(joins):
        with repository.atomic(conn):
            for business_id in batch:
                root = roots[business_id]
                status, _ = state[business_id]
                previous = _close_merge(conn, business_id, actor, f"moved to {root}", stamp)
                edge = max(
                    (row for row in incident[business_id]
                     if roots[row["left_business_id"]] == roots[row["right_business_id"]] == root),
                    key=lambda row: (bool(row["is_manual"]), row["score"]),
                    default=None,
                )  # fmt: skip
                conn.execute(
                    "UPDATE businesses SET status = 'merged', merged_into_id = ?, updated_at = ? "
                    "WHERE id = ?",
                    (root, stamp, business_id),
                )
                repository.insert(
                    conn,
                    BusinessMerge(
                        id=new_id(), project_id=project_id, business_id=business_id,
                        into_business_id=root, match_id=edge["id"] if edge else None,
                        is_manual=bool(edge["is_manual"]) if edge else False,
                        score=edge["score"] if edge else None,
                        rules_version=edge["rules_version"] if edge else None,
                        explanation_json={
                            "previous_status": (previous if status == "merged" else status),
                            "cluster_root": root,
                            "reason": reason,
                            "features": _object(edge["features_json"]) if edge else None,
                            "reasons": _object(edge["reasons_json"]) if edge else None,
                        },
                        actor=actor, merged_at=now, unmerged_at=None, unmerged_by=None,
                        unmerge_reason=None,
                    ),
                )  # fmt: skip
                repository.audit(
                    conn, AuditAction.MERGE, EntityType.BUSINESS, business_id, actor=actor,
                    detail={"into": root, "match_id": edge["id"] if edge else None,
                            "manual": bool(edge["is_manual"]) if edge else False,
                            "reason": reason},
                )  # fmt: skip
    changes = refresh_canonical(conn, project_id, roots, actor=actor)
    sizes: dict[str, int] = defaultdict(int)
    for root in roots.values():
        sizes[root] += 1
    return ClusterCounts(
        clusters=sum(size > 1 for size in sizes.values()),
        merged_records=sum(root != business for business, root in roots.items()),
        merges=len(joins),
        unmerges=len(unmerges),
        refused=len(refused),
        canonical_changes=changes,
    )


def _close_merge(
    conn: sqlite3.Connection, business_id: str, actor: str, reason: str, stamp: str
) -> str | None:
    """Closes the open merge of a record, if any, and returns the status it had before joining."""
    row = conn.execute(
        "SELECT id, explanation_json FROM business_merges "
        "WHERE business_id = ? AND unmerged_at IS NULL",
        (business_id,),
    ).fetchone()
    if row is None:
        return None
    conn.execute(
        "UPDATE business_merges SET unmerged_at = ?, unmerged_by = ?, unmerge_reason = ? "
        "WHERE id = ?",
        (stamp, actor, reason, row["id"]),
    )
    previous = _object(row["explanation_json"]).get("previous_status")
    return previous if isinstance(previous, str) else None


def cluster_members(conn: sqlite3.Connection, business_id: str) -> list[str]:
    """The business and, when it is a root, every record merged into it."""
    return [
        row[0]
        for row in conn.execute(
            "SELECT ? UNION ALL SELECT id FROM businesses "
            "WHERE merged_into_id = ? AND deleted_at IS NULL ORDER BY 1",
            (business_id, business_id),
        )
    ]


# ---- canonical values --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Observation:
    field: str
    id: str
    business_id: str
    display: str
    normalized: str
    confidence: float
    observed_at: str
    kind: str


_OBSERVATIONS_TEMPLATE: Final = """
SELECT 'name', o.id, o.business_id, o.name_raw, o.name_normalized, o.confidence, o.observed_at,
    sc.kind
FROM business_names o {join} {where}
UNION ALL
SELECT 'location', o.id, o.business_id,
    coalesce(nullif(concat_ws(', ', nullif(concat_ws(' ', o.street, o.unit), ''), o.locality,
        nullif(concat_ws(' ', o.region, o.postal_code), '')), ''), o.address_raw, ''),
    lower(concat_ws('|', o.street, o.unit, o.locality, o.region, o.postal_code, o.latitude,
        o.longitude)),
    o.confidence, o.observed_at, sc.kind
FROM business_locations o {join} {where}
UNION ALL
SELECT 'website', o.id, o.business_id, o.url_normalized, o.domain, o.confidence, o.observed_at,
    sc.kind
FROM websites o {join} {where}
UNION ALL
SELECT o.contact_type, o.id, o.business_id, coalesce(o.value_normalized, o.value_raw),
    lower(coalesce(o.value_normalized, o.value_raw)), o.confidence, o.last_seen_at, sc.kind
FROM contact_points o {join} {where} AND o.contact_type IN ('phone', 'email')
    AND o.deleted_at IS NULL AND o.validation_status <> 'invalid'
UNION ALL
SELECT 'category', o.id, o.business_id, o.label_raw, coalesce(o.taxonomy_key, lower(o.label_raw)),
    o.confidence, o.observed_at, sc.kind
FROM business_classifications o {join} {where} AND o.kind = 'category'
"""
#: Every current observation of a project that can represent a field, with its source kind.
_OBSERVATIONS_SQL: Final = _OBSERVATIONS_TEMPLATE.replace(
    "{join}",
    _SOURCE_JOIN + "JOIN raw_records r ON r.id = x.raw_record_id "
    "JOIN source_connectors sc ON sc.id = r.connector_id",
).replace("{where}", _SOURCE_WHERE)


def observations(conn: sqlite3.Connection, project_id: str) -> list[Observation]:
    return [Observation(*row) for row in conn.execute(_OBSERVATIONS_SQL, (project_id,) * 5)]


def policy_choice(field_name: str, candidates: Sequence[Observation]) -> Observation:
    """The most trusted source first; then confidence and freshness (freshness first for a
    location, so a move is followed); then the oldest id, so the choice is deterministic."""

    def rank(item: Observation) -> tuple[int, str | float, str | float]:
        trust = SOURCE_TRUST.get(item.kind, 1)
        if field_name == CanonicalField.LOCATION:
            return (trust, item.observed_at, item.confidence)
        return (trust, item.confidence, item.observed_at)

    return max(sorted(candidates, key=lambda item: item.id), key=rank)


def refresh_canonical(
    conn: sqlite3.Connection, project_id: str, roots: Mapping[str, str], *, actor: str
) -> int:
    """Chooses, for every cluster root, the observation each field shows. Writes differences only.

    A person's choice stays while its observation is still in the cluster. A record that is no
    longer a root loses its choices (superseded, not deleted).
    """
    by_root: dict[str, dict[str, list[Observation]]] = defaultdict(lambda: defaultdict(list))
    for item in observations(conn, project_id):
        if (root := roots.get(item.business_id)) is not None:
            by_root[root][item.field].append(item)
    current = {
        (row["business_id"], row["field"]): row
        for row in conn.execute(
            "SELECT v.id, v.business_id, v.field, v.observation_id, v.method, "
            "v.alternative_count, v.reason, v.actor FROM canonical_values v "
            "JOIN businesses b ON b.id = v.business_id "
            "WHERE b.project_id = ? AND v.superseded_at IS NULL",
            (project_id,),
        )
    }
    shown = {
        row["id"]: row
        for row in conn.execute(
            "SELECT id, display_name, first_seen_at, last_seen_at FROM businesses "
            "WHERE project_id = ? AND deleted_at IS NULL",
            (project_id,),
        )
    }
    now = utc_now()
    stamp = format_ts(now)
    supersede: list[str] = []
    inserts: list[CanonicalValue] = []
    renames: list[tuple[str, str, str, str, str, str]] = []

    def choose(business_id: str, item: Observation, method: SelectionMethod, count: int,
               why: str, who: str) -> None:  # fmt: skip
        inserts.append(
            CanonicalValue(
                id=new_id(), business_id=business_id, field=CanonicalField(item.field),
                observation_id=item.id, value=item.display, method=method,
                policy_version=POLICY_VERSION, alternative_count=count, reason=why, actor=who,
                chosen_at=now, superseded_at=None,
            )
        )  # fmt: skip

    for business_id in sorted(roots):
        if roots[business_id] != business_id:
            supersede += [row["id"] for (owner, _), row in current.items() if owner == business_id]
            continue
        fields = by_root.get(business_id, {})
        name_shown: Observation | None = None
        for canonical in CanonicalField:
            found = fields.get(canonical.value, [])
            row = current.get((business_id, canonical.value))
            if not found:
                if row is not None:
                    supersede.append(row["id"])
                continue
            count = len({item.normalized for item in found})
            by_id = {item.id: item for item in found}
            if row is not None and row["method"] == "manual" and row["observation_id"] in by_id:
                chosen = by_id[row["observation_id"]]
                if row["alternative_count"] != count:
                    supersede.append(row["id"])
                    choose(business_id, chosen, SelectionMethod.MANUAL, count, row["reason"],
                           row["actor"])  # fmt: skip
            else:
                chosen = policy_choice(canonical.value, found)
                unchanged = (
                    row is not None
                    and row["method"] == "policy"
                    and row["observation_id"] == chosen.id
                    and row["alternative_count"] == count
                )
                if not unchanged:
                    if row is not None:
                        supersede.append(row["id"])
                    order = "freshness" if canonical is CanonicalField.LOCATION else "confidence"
                    why = (f"most trusted source ({chosen.kind}), then {order}; "
                           f"{count} distinct value(s) in the cluster")  # fmt: skip
                    choose(business_id, chosen, SelectionMethod.POLICY, count, why, actor)
            if canonical is CanonicalField.NAME:
                name_shown = chosen
        seen = [item.observed_at for items in fields.values() for item in items]
        first = min((item.observed_at for item in fields.get("name", [])), default=None)
        business = shown[business_id]
        display = (name_shown.display.strip()[:300] if name_shown else "") or business[
            "display_name"
        ]
        first_seen = first or business["first_seen_at"]
        last_seen = max([*seen, first_seen]) if seen else business["last_seen_at"]
        if (display, first_seen, last_seen) != (
            business["display_name"],
            business["first_seen_at"],
            business["last_seen_at"],
        ):
            renames.append((display, sort_key(display), first_seen, last_seen, stamp, business_id))

    for batch in _batches(supersede):
        with repository.atomic(conn):
            conn.executemany(
                "UPDATE canonical_values SET superseded_at = ? WHERE id = ?",
                [(stamp, value_id) for value_id in batch],
            )
    for chunk in _batches(inserts):
        with repository.atomic(conn):
            repository.insert(conn, *chunk)
    for rows in _batches(renames):
        with repository.atomic(conn):
            conn.executemany(
                "UPDATE businesses SET display_name = ?, name_sort_key = ?, first_seen_at = ?, "
                "last_seen_at = ?, updated_at = ? WHERE id = ?",
                rows,
            )
    return len(supersede) + len(inserts) + len(renames)


# ---- jobs --------------------------------------------------------------------------------------


def plan_runs(
    conn: sqlite3.Connection, project_id: str | None, *, actor: str
) -> tuple[list[str], list[str]]:
    """Creates one queued job per project to resolve. Returns (created, already open) job ids.

    A project with an open run gets no second one: when a project was named, that is an error;
    when every project was asked for, the open run is listed instead.
    """
    if project_id is not None:
        project = repository.get(conn, Project, project_id)
        if project.deleted_at is not None:
            raise repository.NotFoundError(f"project {project_id} has been deleted")
        projects = [project_id]
    else:
        projects = [
            row["id"]
            for row in conn.execute(
                "SELECT p.id FROM projects p WHERE p.deleted_at IS NULL AND ("
                "EXISTS (SELECT 1 FROM discovery_candidates c WHERE c.project_id = p.id) OR "
                "EXISTS (SELECT 1 FROM businesses b WHERE b.project_id = p.id "
                "AND b.deleted_at IS NULL)) ORDER BY p.created_at, p.id"
            )
        ]
        if not projects:
            raise ResolutionError("there is nothing to resolve yet: discover or import businesses")
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
                    raise ResolutionError(
                        "a resolution run is already open for this project: resume or cancel it"
                    )
                listed.append(open_job["id"])
                continue
            job = Job(
                id=new_id(), project_id=pid, kind=JOB_KIND, status=JobStatus.QUEUED, priority=0,
                params_json={"project_id": pid, "normalizer_version": NORMALIZER_VERSION,
                             "rules_version": matching.RULES_VERSION,
                             "policy_version": POLICY_VERSION},
                idempotency_key=None, progress_done=0, progress_total=None, requested_at=now,
                started_at=None, finished_at=None, created_at=now, updated_at=now,
            )  # fmt: skip
            repository.insert(conn, job)
            repository.audit(conn, AuditAction.CREATE, EntityType.JOB, job.id, actor=actor)
            jobs.write_event(conn, job.id, None, "run_queued", EventLevel.INFO, {"project_id": pid})
            created.append(job.id)
    return created, listed


def guard_idle(conn: sqlite3.Connection, project_id: str) -> None:
    """Refuses a decision while a run is rewriting the same project's clusters."""
    busy = conn.execute(
        "SELECT 1 FROM jobs WHERE kind = ? AND status IN ('queued', 'running') "
        "AND json_extract(params_json, '$.project_id') = ?",
        (JOB_KIND, project_id),
    ).fetchone()
    if busy is not None:
        raise ResolutionError(
            "a resolution run is working on this project: wait for it to finish or pause it"
        )


def outcome_counts(conn: sqlite3.Connection, project_id: str) -> dict[str, int]:
    counts = {outcome.value: 0 for outcome in MatchOutcome}
    for row in conn.execute(
        "SELECT outcome, count(*) FROM match_candidates WHERE project_id = ? GROUP BY outcome",
        (project_id,),
    ):
        counts[row[0]] = int(row[1])
    (manual,) = conn.execute(
        "SELECT count(*) FROM match_candidates WHERE project_id = ? AND is_manual = 1",
        (project_id,),
    ).fetchone()
    counts["manual"] = int(manual)
    return counts


# ---- reads -------------------------------------------------------------------------------------


def _resolution_run(conn: sqlite3.Connection, job: Job) -> ResolutionRun:
    state = jobs.latest_state(conn, job.id)
    stats = state.get("stats")
    counters = stats if isinstance(stats, dict) else {}
    project = repository.get(conn, Project, job.project_id)
    stage = state.get("stage")
    return ResolutionRun(
        job_id=job.id,
        project_id=job.project_id,
        project_name=project.name,
        status=job.status,
        stage=stage if isinstance(stage, str) and stage else "queued",
        progress_done=job.progress_done,
        progress_total=job.progress_total,
        normalizer_version=str(job.params_json.get("normalizer_version") or NORMALIZER_VERSION),
        rules_version=str(job.params_json.get("rules_version") or matching.RULES_VERSION),
        stats=ResolutionStats.model_validate(
            {name: repository._counter(counters.get(name)) for name in ResolutionStats.model_fields}
        ),
        requested_at=job.requested_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        events=repository._run_events(conn, job.id),
        is_synthetic=project.is_synthetic,
    )


def get_resolution_run(conn: sqlite3.Connection, job_id: str) -> ResolutionRun:
    return _resolution_run(conn, jobs.live_job(conn, job_id, JOB_KIND))


def runs_by_id(conn: sqlite3.Connection, job_ids: Sequence[str]) -> ResolutionRunPage:
    items = [get_resolution_run(conn, job_id) for job_id in job_ids]
    return ResolutionRunPage(items=items, next_cursor=None, total=len(items))


def list_resolution_runs(
    conn: sqlite3.Connection, query: ResolutionRunListQuery
) -> ResolutionRunPage:
    where, params = ["j.kind = ?"], list[object]([JOB_KIND])
    if query.project_id is not None:
        where.append("j.project_id = ?")
        params.append(query.project_id)
    (total,) = conn.execute(
        f"SELECT count(*) FROM jobs j WHERE {' AND '.join(where)}",  # noqa: S608
        params,
    ).fetchone()
    if query.cursor is not None:
        last_key, last_id = repository.decode_cursor(query.cursor, "resolution")
        where.append("(j.requested_at, j.id) < (?, ?)")
        params += [last_key, last_id]
    rows = conn.execute(
        f"SELECT {repository._columns(Job, 'j')} FROM jobs j "  # noqa: S608
        f"WHERE {' AND '.join(where)} ORDER BY j.requested_at DESC, j.id DESC LIMIT ?",
        [*params, query.limit + 1],
    ).fetchall()
    page = rows[: query.limit]
    next_cursor = (
        repository.encode_cursor("resolution", page[-1]["requested_at"], page[-1]["id"])
        if len(rows) > query.limit
        else None
    )
    return ResolutionRunPage(
        items=[_resolution_run(conn, repository.from_row(Job, row)) for row in page],
        next_cursor=next_cursor,
        total=total,
    )


_SIDE_SQL: Final = """
    b{s}.display_name AS {s}_name, coalesce(b{s}.merged_into_id, b{s}.id) AS {s}_root,
    (SELECT l.locality FROM business_locations l WHERE l.business_id = b{s}.id
        ORDER BY l.is_primary DESC, l.observed_at DESC, l.id LIMIT 1) AS {s}_locality,
    (SELECT l.region FROM business_locations l WHERE l.business_id = b{s}.id
        ORDER BY l.is_primary DESC, l.observed_at DESC, l.id LIMIT 1) AS {s}_region,
    (SELECT json_group_array(DISTINCT sc.name) FROM business_names n
        JOIN extractions x ON x.id = n.extraction_id JOIN raw_records r ON r.id = x.raw_record_id
        JOIN source_connectors sc ON sc.id = r.connector_id WHERE n.business_id = b{s}.id)
        AS {s}_sources
"""


def _side(row: Mapping[str, Any], side: str) -> MatchSide:
    sources = orjson.loads(row[f"{side}_sources"] or "[]")
    return MatchSide(
        business_id=row[f"{side}_business_id"],
        root_business_id=row[f"{side}_root"],
        display_name=row[f"{side}_name"],
        locality=row[f"{side}_locality"],
        region=row[f"{side}_region"],
        connector_names=sorted(str(name) for name in sources if name),
    )


def _reason_codes(reasons: JsonObject) -> list[str]:
    listed = reasons.get("reasons")
    items = [item for item in listed if isinstance(item, dict)] if isinstance(listed, list) else []
    order = {ReasonDirection.RULE.value: 0, ReasonDirection.FOR.value: 1}

    def rank(item: dict[str, Any]) -> tuple[int, float]:
        weight = item.get("weight")
        return (order.get(str(item.get("direction")), 2),
                -abs(weight) if isinstance(weight, int | float) else 0.0)  # fmt: skip

    codes = [str(item.get("code")) for item in sorted(items, key=rank)]
    return list(dict.fromkeys(code for code in codes if re.fullmatch(r"[a-z][a-z0-9_.]*", code)))[
        :MAX_REASON_CODES
    ]


def list_matches(conn: sqlite3.Connection, query: MatchListQuery) -> MatchPage:
    where, params = ["p.deleted_at IS NULL"], list[object]()
    if query.project_id is not None:
        where.append("m.project_id = ?")
        params.append(query.project_id)
    if query.status is not None:
        where.append("m.status = ?")
        params.append(query.status.value)
    if query.risk is not None:
        where.append("m.risk = ?")
        params.append(query.risk.value)
    if query.min_score is not None:
        where.append("m.score >= ?")
        params.append(query.min_score)
    if query.connector_key is not None:
        where.append(
            "EXISTS (SELECT 1 FROM business_names n JOIN extractions x ON x.id = n.extraction_id "
            "JOIN raw_records r ON r.id = x.raw_record_id "
            "JOIN source_connectors sc ON sc.id = r.connector_id "
            "WHERE n.business_id IN (m.left_business_id, m.right_business_id) AND sc.key = ?)"
        )
        params.append(query.connector_key)
    base = "FROM match_candidates m JOIN projects p ON p.id = m.project_id"
    (total,) = conn.execute(
        f"SELECT count(*) {base} WHERE {' AND '.join(where)}",
        params,
    ).fetchone()
    if query.cursor is not None:
        last_key, last_id = repository.decode_cursor(query.cursor, "match")
        try:
            last_score = float(last_key)
        except ValueError as exc:
            raise repository.InvalidCursorError("the cursor is not valid") from exc
        where.append("(m.score, m.id) < (?, ?)")
        params += [last_score, last_id]
    rows = conn.execute(
        "SELECT m.id, m.project_id, m.left_business_id AS left_business_id, "
        "m.right_business_id AS right_business_id, m.score, m.outcome, m.risk, m.status, "
        "m.is_manual, m.reasons_json, m.rules_version, m.updated_at, p.is_synthetic, "
        f"{_SIDE_SQL.format(s='left')}, {_SIDE_SQL.format(s='right')} "
        f"{base} JOIN businesses bleft ON bleft.id = m.left_business_id "
        "JOIN businesses bright ON bright.id = m.right_business_id "
        f"WHERE {' AND '.join(where)} ORDER BY m.score DESC, m.id DESC LIMIT ?",
        [*params, query.limit + 1],
    ).fetchall()
    page = rows[: query.limit]
    next_cursor = (
        repository.encode_cursor("match", repr(page[-1]["score"]), page[-1]["id"])
        if len(rows) > query.limit
        else None
    )
    items = [
        MatchListItem(
            id=row["id"],
            project_id=row["project_id"],
            left=_side(row, "left"),
            right=_side(row, "right"),
            score=row["score"],
            outcome=row["outcome"],
            risk=row["risk"],
            status=row["status"],
            is_manual=bool(row["is_manual"]),
            reasons=_reason_codes(_object(row["reasons_json"])),
            rules_version=row["rules_version"],
            updated_at=row["updated_at"],
            is_synthetic=bool(row["is_synthetic"]),
        )
        for row in page
    ]
    return MatchPage(items=items, next_cursor=next_cursor, total=total)


def _merges_of(
    conn: sqlite3.Connection, where: str, params: Sequence[object]
) -> list[BusinessMerge]:
    rows = conn.execute(
        f"SELECT {repository._columns(BusinessMerge)} FROM business_merges "  # noqa: S608
        f"WHERE {where} ORDER BY merged_at DESC, id DESC LIMIT ?",
        [*params, MAX_MERGES_SHOWN],
    ).fetchall()
    return [repository.from_row(BusinessMerge, row) for row in rows]


def get_match_detail(conn: sqlite3.Connection, match_id: str) -> MatchDetail:
    match = repository.get(conn, MatchCandidate, match_id)
    features = match.features_json
    listed_fields = features.get("fields")
    listed_reasons = match.reasons_json.get("reasons")
    listed_keys = features.get("blocking_keys")
    decisions = conn.execute(
        f"SELECT {repository._columns(ReviewDecision)} FROM review_decisions "  # noqa: S608
        "WHERE entity_type = 'match_candidate' AND entity_id = ? ORDER BY decided_at DESC, id DESC",
        (match.id,),
    ).fetchall()
    return MatchDetail(
        match=match,
        left=repository.get_business_detail(conn, match.left_business_id),
        right=repository.get_business_detail(conn, match.right_business_id),
        fields=[
            FieldComparison.model_validate(item)
            for item in (listed_fields if isinstance(listed_fields, list) else [])
            if isinstance(item, dict)
        ],
        reasons=[
            MatchReason.model_validate(item)
            for item in (listed_reasons if isinstance(listed_reasons, list) else [])
            if isinstance(item, dict)
        ],
        blocking_keys=[
            key
            for key in (listed_keys if isinstance(listed_keys, list) else [])
            if isinstance(key, str)
        ],
        decisions=[repository.from_row(ReviewDecision, row) for row in decisions],
        merges=_merges_of(
            conn,
            "match_id = ? OR business_id IN (?, ?)",
            (match.id, match.left_business_id, match.right_business_id),
        ),
    )


def cluster_merges(conn: sqlite3.Connection, members: Collection[str]) -> list[BusinessMerge]:
    placeholders = ", ".join("?" * len(members))
    return _merges_of(
        conn,
        f"business_id IN ({placeholders}) OR into_business_id IN ({placeholders})",
        [*members, *members],
    )


# ---- decisions ---------------------------------------------------------------------------------


def act_on_match(conn: sqlite3.Connection, request: MatchActionRequest, *, actor: str) -> None:
    """Merge, keep separate, defer or undo one pair, then recompute the project's clusters."""
    match = repository.get(conn, MatchCandidate, request.match_id)
    guard_idle(conn, match.project_id)
    current = match.status
    if request.action is MatchAction.UNDO:
        latest = conn.execute(
            "SELECT previous_value FROM review_decisions WHERE entity_type = 'match_candidate' "
            "AND entity_id = ? ORDER BY decided_at DESC, id DESC LIMIT 1",
            (match.id,),
        ).fetchone()
        if latest is not None and latest["previous_value"] in MatchStatus._value2member_map_:
            target = MatchStatus(latest["previous_value"])
        elif current is MatchStatus.PENDING:
            raise ResolutionError("there is no decision to undo on this pair")
        else:  # undoing what the engine decided on its own sends the pair to a person
            target = MatchStatus.PENDING
    else:
        target = {
            MatchAction.MERGE: MatchStatus.MERGED,
            MatchAction.KEEP_SEPARATE: MatchStatus.SEPARATE,
            MatchAction.DEFER: MatchStatus.DEFERRED,
        }[request.action]
        if target is current:
            raise ResolutionError(f"this pair is already {current.value}")
    if not can_transition(current, target):
        raise repository.InvalidTransitionError(
            f"a {current.value} pair cannot become {target.value}"
        )
    now = utc_now()
    with repository.atomic(conn):
        conn.execute(
            "UPDATE match_candidates SET status = ?, is_manual = 1, decided_by = ?, updated_at = ? "
            "WHERE id = ?",
            (target.value, actor, format_ts(now), match.id),
        )
        repository.insert(
            conn,
            ReviewDecision(
                id=new_id(), entity_type=EntityType.MATCH_CANDIDATE, entity_id=match.id,
                decision=request.action.value, previous_value=current.value,
                new_value=target.value, reason=request.reason or DEFAULT_REASONS[request.action],
                actor=actor, decided_at=now,
            ),
        )  # fmt: skip
        recluster(conn, match.project_id, actor=actor,
                  reason=f"{request.action.value} on pair {match.id}")  # fmt: skip


def choose_canonical(
    conn: sqlite3.Connection, request: CanonicalChoiceRequest, *, actor: str
) -> None:
    """Makes one observation of a business's cluster the value it shows for a field."""
    business = repository.get(conn, Business, request.business_id)
    if business.deleted_at is not None:
        raise repository.NotFoundError(f"business {business.id} has been deleted")
    if business.status is BusinessStatus.MERGED:
        raise ResolutionError("this record belongs to another business: choose the value there")
    guard_idle(conn, business.project_id)
    members = set(cluster_members(conn, business.id))
    found = [
        item
        for item in observations(conn, business.project_id)
        if item.business_id in members and item.field == request.field.value
    ]
    chosen = next((item for item in found if item.id == request.observation_id), None)
    if chosen is None:
        raise repository.NotFoundError(
            f"observation {request.observation_id} is not a {request.field.value} of this business"
        )
    row = conn.execute(
        "SELECT id, observation_id, method FROM canonical_values "
        "WHERE business_id = ? AND field = ? AND superseded_at IS NULL",
        (business.id, request.field.value),
    ).fetchone()
    if row is not None and row["observation_id"] == chosen.id and row["method"] == "manual":
        return
    now = utc_now()
    reason = request.reason or DEFAULT_CHOICE_REASON
    with repository.atomic(conn):
        if row is not None:
            conn.execute(
                "UPDATE canonical_values SET superseded_at = ? WHERE id = ?",
                (format_ts(now), row["id"]),
            )
        repository.insert(
            conn,
            CanonicalValue(
                id=new_id(), business_id=business.id, field=request.field,
                observation_id=chosen.id, value=chosen.display, method=SelectionMethod.MANUAL,
                policy_version=POLICY_VERSION,
                alternative_count=len({item.normalized for item in found}), reason=reason,
                actor=actor, chosen_at=now, superseded_at=None,
            ),
            ReviewDecision(
                id=new_id(), entity_type=EntityType.BUSINESS, entity_id=business.id,
                decision="choose_canonical_value",
                previous_value=row["observation_id"] if row is not None else None,
                new_value=chosen.id, reason=reason, actor=actor, decided_at=now,
            ),
        )  # fmt: skip
        if request.field is CanonicalField.NAME:
            display = chosen.display.strip()[:300] or business.display_name
            conn.execute(
                "UPDATE businesses SET display_name = ?, name_sort_key = ?, updated_at = ? "
                "WHERE id = ?",
                (display, sort_key(display), format_ts(now), business.id),
            )
