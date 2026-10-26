"""Store side of contact intelligence (phase 09): the only SQL it runs.

- Extraction: the channels and people of every crawled page that speaks for a business (the
  business whose candidate asked for the crawl and every business whose website is on that site,
  as in phase 07), through `contacts.site` extractions over the pages' raw records: at most one
  observation per channel, extraction and business, and one person per name. Observations are
  never edited; only their status, suppression and primary flags move.
- Channels: the observations of one value within a canonical business are one channel. Its status
  is decided by `rules.channel_status` from the checks, its sources, freshness, a person's review
  and the do-not-contact list. Reads decide it again; runs and actions write it to every
  observation, with the history in `contact_validations`.
- Mail domains: cached answers, asked again once expired and only when a run or an action allows.
- People: published decision makers; doubtful ones wait for review. An address pattern that a
  published address and a published name share can suggest a hypothesis for another published
  person, stored as a hypothesis and never as a contact.
- Suppressions: the local do-not-contact list, applied to observations as soon as it changes and
  checked again by every read and export query.
"""

import sqlite3
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final

import orjson
from pydantic import JsonValue

from research_engine import jobs, repository
from research_engine.contacts import rules
from research_engine.contacts.mail_dns import Lookup
from research_engine.domain import (
    AuditAction,
    Business,
    CanonicalChoiceRequest,
    CanonicalField,
    Contactability,
    ContactAction,
    ContactActionRequest,
    ContactChannel,
    ContactConflict,
    ContactDetail,
    ContactFreshness,
    ContactLevel,
    ContactListItem,
    ContactListQuery,
    ContactOrigin,
    ContactPage,
    ContactPoint,
    ContactRole,
    ContactRun,
    ContactRunListQuery,
    ContactRunPage,
    ContactSignal,
    ContactSource,
    ContactStats,
    ContactSuppression,
    ContactType,
    ContactValidation,
    DecisionMaker,
    DecisionMakerDecision,
    DecisionMakerItem,
    DecisionMakerListQuery,
    DecisionMakerPage,
    DecisionMakerRelationship,
    DecisionMakerReview,
    DecisionMakerReviewRequest,
    DecisionMakerView,
    EntityType,
    EventLevel,
    Extraction,
    Job,
    JobStatus,
    JsonObject,
    MailDomainResult,
    Project,
    Provenance,
    Record,
    ReviewDecision,
    SuppressionChangeRequest,
    SuppressionExport,
    SuppressionExportRequest,
    SuppressionImportProblem,
    SuppressionImportRequest,
    SuppressionImportResult,
    SuppressionKind,
    SuppressionListQuery,
    SuppressionPage,
    ValidationStatus,
    format_ts,
    new_id,
    sort_key,
    utc_now,
)
from research_engine.latino.store import _cluster, page_links
from research_engine.resolution import normalize
from research_engine.resolution import store as resolution_store
from research_engine.website import store as website_store

JOB_KIND: Final = "contact_intelligence"
ACTOR: Final = "contact-engine"
EXTRACTOR_SITE: Final = "contacts.site"
EXTRACTOR_PATTERN: Final = "contacts.pattern"
#: The key `crawl.engine` registers its connector under (not imported: it loads the browser stack).
CRAWL_CONNECTOR_KEY: Final = "web.crawler"
MAX_LOOKUPS_PER_RUN: Final = 1000
MAX_DECISIONS: Final = 50
CONFIRMED: Final = "contact_confirmed"
MARKED_INVALID: Final = "contact_invalid"
REVIEW_CLEARED: Final = "contact_review_cleared"
CONTACT_DECISIONS: Final = (CONFIRMED, MARKED_INVALID, REVIEW_CLEARED)
MAIL_TTL: Final[Mapping[MailDomainResult, timedelta]] = {
    MailDomainResult.MX: timedelta(days=7),
    MailDomainResult.IMPLICIT_MX: timedelta(days=7),
    MailDomainResult.NULL_MX: timedelta(days=1),
    MailDomainResult.NO_MAIL: timedelta(days=1),
    MailDomainResult.NXDOMAIN: timedelta(days=1),
    MailDomainResult.TIMEOUT: timedelta(hours=1),
    MailDomainResult.ERROR: timedelta(hours=1),
}
_FAILED_LOOKUPS: Final = frozenset({MailDomainResult.TIMEOUT, MailDomainResult.ERROR})
_FORMS: Final = (ContactType.CONTACT_FORM, ContactType.BOOKING_FORM)
_CHUNK: Final = 500
_ORIGIN_RANK: Final = {
    ContactOrigin.PUBLISHED: 0,
    ContactOrigin.USER_IMPORT: 1,
    ContactOrigin.HYPOTHESIS: 2,
}
_REVIEW_STATES: Final = {CONFIRMED: "confirmed", MARKED_INVALID: "invalid"}
_TYPE_ORDER: Final = {contact_type: index for index, contact_type in enumerate(ContactType)}


class ContactError(ValueError):
    """A contact request that cannot be carried out as asked; the message says why."""


def _object(text: str | None) -> JsonObject:
    loaded = orjson.loads(text) if text else None
    return loaded if isinstance(loaded, dict) else {}


def _json(value: object) -> str:
    return orjson.dumps(value, option=orjson.OPT_SORT_KEYS).decode()


def _chunks[T](items: Sequence[T], size: int = _CHUNK) -> Iterable[Sequence[T]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _marks(items: Sequence[object]) -> str:
    return ", ".join("?" * len(items))


def _signals(signals: rules.Signals) -> list[JsonValue]:
    return [[code, value] for code, value in signals]


def _codes(value: object) -> list[str]:
    loaded = orjson.loads(value) if isinstance(value, str) else value
    codes = loaded.get("codes") if isinstance(loaded, dict) else None
    return [code for code in codes if isinstance(code, str)] if isinstance(codes, list) else []


# ---- extraction ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PageSource:
    raw_record_id: str
    business_ids: tuple[str, ...]


def page_sources(conn: sqlite3.Connection, project_id: str) -> list[PageSource]:
    """Every crawled page that speaks for a live business of the project, by raw record id."""
    return [
        PageSource(raw_record_id, tuple(sorted(business_ids)))
        for raw_record_id, business_ids in sorted(page_links(conn, project_id).items())
    ]


def page_statuses(conn: sqlite3.Connection, project_id: str) -> dict[str, int]:
    """The HTTP status each crawled page was last served with, by raw record id."""
    return {
        row["raw_record_id"]: row["http_status"]
        for row in conn.execute(
            "SELECT cu.raw_record_id, cu.http_status FROM crawl_urls cu "
            "JOIN source_runs r ON r.id = cu.source_run_id WHERE r.project_id = ? "
            "AND cu.raw_record_id IS NOT NULL AND cu.http_status IS NOT NULL "
            "ORDER BY cu.updated_at, cu.id",
            (project_id,),
        )
    }


@dataclass(frozen=True, slots=True)
class ExtractBatch:
    pages: int
    contacts: int
    forms: int
    people: int


_PAGES_SQL: Final = (
    "SELECT rr.id, rr.payload_json, rr.source_url AS url, rr.acquired_at, a.artifact_path, "
    "a.content_type AS stored_type FROM raw_records rr "
    "LEFT JOIN acquired_pages a ON a.id = rr.acquired_page_id WHERE rr.id IN ({marks})"
)


def _base_check(
    contact: rules.FoundContact, country: str | None, page_status: int | None
) -> tuple[ValidationStatus, str, rules.Signals, JsonObject]:
    """The checks a found channel passes on its own, and the facts they read."""
    detail: JsonObject = dict(contact.detail)
    match contact.contact_type:
        case ContactType.PHONE:
            phone = rules.check_phone(contact.raw, country)
            return phone.status, "numbering_plan", phone.signals, detail
        case ContactType.EMAIL:
            email = rules.check_email(contact.raw, None, ())
            return email.status, email.method, email.signals, detail
        case ContactType.CONTACT_FORM | ContactType.BOOKING_FORM:
            if page_status is not None:
                detail["page_status"] = page_status
            status, method, signals = rules.check_form(detail)
            return status, method, signals, detail
    platform: rules.Signals = (("platform", contact.platform),)
    return ValidationStatus.STRUCTURALLY_VALID, "structure", platform, detail


def _location_of(
    locations: Sequence[tuple[str, str | None, str | None]], contact: rules.FoundContact
) -> str | None:
    """The location of the business whose address a structured-data block repeats."""
    if contact.postal_code is None:
        return None
    for location_id, postal_code, street in locations:
        number = contact.street_number
        same_street = number is None or (street or "").startswith(number)
        if postal_code and postal_code[:5] == contact.postal_code[:5] and same_street:
            return location_id
    return None


def extract_batch(
    conn: sqlite3.Connection,
    data_dir: Path | None,
    batch: Sequence[PageSource],
    statuses: Mapping[str, int],
    now: datetime,
) -> ExtractBatch:
    """Reads a batch of pages in one transaction. Idempotent: an extraction of this version is
    written once per page, a channel once per extraction and business, a person once per name. A
    page this version already read for every business it speaks for is not read again: its stored
    HTML was opened and parsed on every run of the project, 9 200 pages for one new search."""
    ids = [source.raw_record_id for source in batch]
    if not ids:
        return ExtractBatch(0, 0, 0, 0)
    marks = _marks(ids)
    version = [EXTRACTOR_SITE, rules.EXTRACTOR_VERSION]
    extractions = {
        row["raw_record_id"]: row["id"]
        for row in conn.execute(
            f"SELECT id, raw_record_id FROM extractions WHERE raw_record_id IN ({marks}) "  # noqa: S608
            "AND extractor = ? AND extractor_version = ?",
            [*ids, *version],
        )
    }
    seen_contacts: set[tuple[str, str, str]] = set()
    for row in conn.execute(
        "SELECT f.extraction_id, f.business_id, f.contact_type, f.value_normalized, f.value_raw "  # noqa: S608
        "FROM contact_points f JOIN extractions x ON x.id = f.extraction_id "
        f"WHERE x.raw_record_id IN ({marks}) AND x.extractor = ? AND x.extractor_version = ?",
        [*ids, *version],
    ):
        key = rules.channel_key(
            ContactType(row["contact_type"]), row["value_normalized"], row["value_raw"]
        )
        seen_contacts.add((row["extraction_id"], row["business_id"], key))
    seen_people = {
        (row["extraction_id"], row["business_id"], row["full_name_normalized"]): row["id"]
        for row in conn.execute(
            "SELECT p.id, p.extraction_id, p.business_id, p.full_name_normalized "  # noqa: S608
            "FROM decision_makers p JOIN extractions x ON x.id = p.extraction_id "
            f"WHERE x.raw_record_id IN ({marks}) AND x.extractor = ? AND x.extractor_version = ?",
            [*ids, *version],
        )
    }
    applied: dict[str, set[str]] = defaultdict(set)
    for extraction_id, business_id, _ in (*seen_contacts, *seen_people):
        applied[extraction_id].add(business_id)
    batch = [
        source
        for source in batch
        if not already_read(extractions.get(source.raw_record_id), applied, source.business_ids)
    ]
    if not batch:
        return ExtractBatch(len(ids), 0, 0, 0)
    marks = _marks([source.raw_record_id for source in batch])
    pages = {
        row["id"]: row
        for row in conn.execute(
            _PAGES_SQL.format(marks=marks), [source.raw_record_id for source in batch]
        )
    }
    businesses = sorted({business for source in batch for business in source.business_ids})
    countries: dict[str, set[str]] = defaultdict(set)
    locations: dict[str, list[tuple[str, str | None, str | None]]] = defaultdict(list)
    for chunk in _chunks(businesses):
        for row in conn.execute(
            "SELECT id, business_id, country_code, postal_code, street FROM business_locations "  # noqa: S608
            f"WHERE business_id IN ({_marks(chunk)})",
            list(chunk),
        ):
            if row["country_code"]:
                countries[row["business_id"]].add(row["country_code"])
            locations[row["business_id"]].append((row["id"], row["postal_code"], row["street"]))

    records: list[Record] = []
    contacts = forms = people = 0
    for source in batch:
        page = pages.get(source.raw_record_id)
        if page is None:
            continue
        payload = _object(page["payload_json"])
        final_url = str(payload.get("final_url") or page["url"] or "")
        facts = (
            website_store._facts(data_dir, page, final_url, technologies=False)[0]
            if data_dir
            else None
        )
        found_countries = {code for business in source.business_ids for code in countries[business]}
        country = next(iter(found_countries)) if len(found_countries) == 1 else None
        found = rules.scan_page(payload, facts, country=country)
        extraction_id = extractions.get(source.raw_record_id)
        if extraction_id is None:
            extraction = Extraction(
                id=new_id(),
                raw_record_id=source.raw_record_id,
                extractor=EXTRACTOR_SITE,
                extractor_version=rules.EXTRACTOR_VERSION,
                extracted_at=now,
            )
            records.append(extraction)
            extraction_id = extraction.id
        observed = datetime.fromisoformat(page["acquired_at"])
        page_status = statuses.get(source.raw_record_id)
        for business_id in source.business_ids:
            person_ids: dict[str, str] = {}
            for person in found.people:
                marker = (extraction_id, business_id, person.key)
                if marker in seen_people:
                    person_ids[person.key] = seen_people[marker]
                    continue
                codes = sorted(person.reasons)
                maker = DecisionMaker(
                    id=new_id(),
                    business_id=business_id,
                    extraction_id=extraction_id,
                    full_name_raw=person.name,
                    full_name_normalized=person.key,
                    role_raw=person.role,
                    role_normalized=sort_key(person.role) if person.role else None,
                    relationship=person.relationship,
                    published_context=person.snippet,
                    review_status=DecisionMakerReview.AMBIGUOUS
                    if codes
                    else DecisionMakerReview.UNREVIEWED,
                    review_reasons_json={
                        "codes": list[JsonValue](codes),
                        "page": list[JsonValue](codes),
                    }
                    if codes
                    else None,
                    confidence=person.confidence,
                    observed_at=observed,
                    created_at=now,
                    updated_at=now,
                    deleted_at=None,
                    deleted_reason=None,
                )
                records.append(maker)
                seen_people[marker] = person_ids[person.key] = maker.id
                people += 1
            for contact in found.contacts:
                key = rules.channel_key(contact.contact_type, contact.normalized, contact.raw)
                if (extraction_id, business_id, key) in seen_contacts:
                    continue
                seen_contacts.add((extraction_id, business_id, key))
                status, method, signals, detail = _base_check(contact, country, page_status)
                social = contact.contact_type is ContactType.SOCIAL_PROFILE
                point = ContactPoint(
                    id=new_id(),
                    business_id=business_id,
                    location_id=_location_of(locations[business_id], contact),
                    decision_maker_id=person_ids.get(contact.person) if contact.person else None,
                    contact_type=contact.contact_type,
                    value_raw=contact.raw,
                    value_normalized=contact.normalized,
                    origin=ContactOrigin.PUBLISHED,
                    platform=contact.platform if social else None,
                    web_page_id=None,
                    role_or_department=contact.role_or_department,
                    published_context=contact.context,
                    is_primary=False,
                    validation_status=status,
                    validated_at=now,
                    validation_method=method,
                    do_not_contact=False,
                    suppression_reason=None,
                    confidence=contact.confidence,
                    extraction_id=extraction_id,
                    first_seen_at=observed,
                    last_seen_at=observed,
                    created_at=now,
                    updated_at=now,
                    deleted_at=None,
                    deleted_reason=None,
                )
                records += [
                    point,
                    ContactValidation(
                        id=new_id(),
                        contact_point_id=point.id,
                        status=status,
                        method=method,
                        validator_version=rules.RULES_VERSION,
                        detail_json={
                            "field": contact.field_path,
                            "signals": _signals(signals),
                            "detail": detail,
                        },
                        validated_at=now,
                        expires_at=None,
                    ),
                ]
                contacts += 1
                forms += contact.contact_type in _FORMS
    with repository.atomic(conn):
        repository.insert(conn, *records)
    return ExtractBatch(len(ids), contacts, forms, people)


def already_read(
    extraction_id: str | None, applied: Mapping[str, set[str]], business_ids: Sequence[str]
) -> bool:
    """Whether reading the page again would add nothing: this version extracted it, and either
    every business it speaks for has what it found or it found nothing at all.
    ponytail: a business whose country changed since keeps the phones read under the old one."""
    if extraction_id is None:
        return False
    found_for = applied.get(extraction_id)
    return not found_for or set(business_ids) <= found_for


# ---- currency, clusters and mail domains -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Observation:
    point: ContactPoint
    provenance: Provenance
    #: The facts the observation's first validation read (a form's fields, a number's extension).
    detail: JsonObject
    key: str


@dataclass(frozen=True, slots=True)
class Person:
    maker: DecisionMaker
    provenance: Provenance


@dataclass(frozen=True, slots=True)
class Currency:
    """Which crawled observations the newest read of their page still shows."""

    latest: Mapping[str, str]
    keys: Mapping[str, frozenset[str]]

    def current(self, observation: Observation) -> bool:
        if observation.provenance.connector_key != CRAWL_CONNECTOR_KEY:
            return True
        newest = self.latest.get(observation.provenance.source_url or "")
        return (
            newest is None
            or newest == observation.provenance.raw_record_id
            or observation.key in self.keys.get(newest, frozenset())
        )


def currency(conn: sqlite3.Connection, project_id: str) -> Currency:
    """The newest read page of every crawled URL of a project and the channels it shows."""
    newest: dict[str, tuple[str, str]] = {}
    for row in conn.execute(
        "SELECT r.id, r.source_url, r.acquired_at FROM extractions x "
        "JOIN raw_records r ON r.id = x.raw_record_id "
        "JOIN source_runs sr ON sr.id = r.source_run_id "
        "WHERE x.extractor = ? AND x.extractor_version = ? AND sr.project_id = ? "
        "AND r.source_url IS NOT NULL",
        (EXTRACTOR_SITE, rules.EXTRACTOR_VERSION, project_id),
    ):
        marker = (row["acquired_at"], row["id"])
        if marker > newest.get(row["source_url"], ("", "")):
            newest[row["source_url"]] = marker
    keys: dict[str, set[str]] = defaultdict(set)
    for chunk in _chunks([raw_id for _, raw_id in newest.values()]):
        for row in conn.execute(
            "SELECT x.raw_record_id, f.contact_type, f.value_normalized, f.value_raw "  # noqa: S608
            "FROM contact_points f JOIN extractions x ON x.id = f.extraction_id "
            f"WHERE x.raw_record_id IN ({_marks(chunk)}) AND x.extractor = ?",
            [*chunk, EXTRACTOR_SITE],
        ):
            keys[row["raw_record_id"]].add(
                rules.channel_key(
                    ContactType(row["contact_type"]), row["value_normalized"], row["value_raw"]
                )
            )
    return Currency(
        {url: raw_id for url, (_, raw_id) in newest.items()},
        {raw_id: frozenset(found) for raw_id, found in keys.items()},
    )


@dataclass(slots=True)
class Cluster:
    """A canonical business with everything its contacts are decided from."""

    root: str
    project_id: str
    name: str
    is_synthetic: bool
    members: list[str]
    rows: list[Observation] = field(default_factory=list)
    people: list[Person] = field(default_factory=list)
    website_domains: set[str] = field(default_factory=set)
    countries: set[str] = field(default_factory=set)
    #: The observation the business shows for `phone` and `email`, and how many values disagree.
    canonical: dict[str, tuple[str, int]] = field(default_factory=dict)
    reviews: dict[str, ReviewDecision] = field(default_factory=dict)
    decisions: list[ReviewDecision] = field(default_factory=list)

    @property
    def country(self) -> str | None:
        return next(iter(self.countries)) if len(self.countries) == 1 else None


_OBSERVATIONS_SQL: Final = (
    "SELECT " + repository._columns(ContactPoint, "f") + ", " + repository._PROVENANCE + ", "  # noqa: S608
    "(SELECT v.detail_json FROM contact_validations v WHERE v.contact_point_id = f.id "
    "ORDER BY v.validated_at, v.id LIMIT 1) AS v_detail FROM contact_points f "
    "JOIN extractions x ON x.id = f.extraction_id JOIN raw_records r ON r.id = x.raw_record_id "
    "JOIN source_connectors sc ON sc.id = r.connector_id "
    "WHERE f.business_id IN ({marks}) AND f.deleted_at IS NULL AND "
    + repository.CURRENT_EXTRACTION
    + " ORDER BY f.id"
)
_PEOPLE_SQL: Final = (
    "SELECT " + repository._columns(DecisionMaker, "f") + ", " + repository._PROVENANCE + " "
    "FROM decision_makers f JOIN extractions x ON x.id = f.extraction_id "
    "JOIN raw_records r ON r.id = x.raw_record_id "
    "JOIN source_connectors sc ON sc.id = r.connector_id "
    "WHERE f.business_id IN ({marks}) AND f.deleted_at IS NULL AND "
    + repository.CURRENT_EXTRACTION
    + " ORDER BY f.full_name_normalized, f.observed_at DESC, f.id"
)


def load_clusters(conn: sqlite3.Connection, roots: Sequence[str]) -> list[Cluster]:
    """The records, observations, people, websites and contact reviews of canonical businesses."""
    clusters: dict[str, Cluster] = {}
    owner: dict[str, str] = {}
    for chunk in _chunks(roots):
        marks = _marks(chunk)
        for row in conn.execute(
            "SELECT b.id, b.project_id, b.display_name, p.is_synthetic FROM businesses b "  # noqa: S608
            f"JOIN projects p ON p.id = b.project_id WHERE b.id IN ({marks})",
            list(chunk),
        ):
            clusters[row["id"]] = Cluster(
                row["id"],
                row["project_id"],
                row["display_name"],
                bool(row["is_synthetic"]),
                [row["id"]],
            )
            owner[row["id"]] = row["id"]
        for row in conn.execute(
            f"SELECT id, merged_into_id FROM businesses WHERE merged_into_id IN ({marks}) "  # noqa: S608
            "AND deleted_at IS NULL ORDER BY id",
            list(chunk),
        ):
            if (cluster := clusters.get(row["merged_into_id"])) is not None:
                cluster.members.append(row["id"])
                owner[row["id"]] = cluster.root
    for chunk in _chunks(list(owner)):
        marks = _marks(chunk)
        for row in conn.execute(_OBSERVATIONS_SQL.format(marks=marks), list(chunk)):
            point = repository.from_row(ContactPoint, row)
            key = rules.channel_key(point.contact_type, point.value_normalized, point.value_raw)
            clusters[owner[point.business_id]].rows.append(
                Observation(
                    point,
                    repository.from_row(Provenance, row, prefix="p_"),
                    _object(row["v_detail"]),
                    key,
                )
            )
        for row in conn.execute(_PEOPLE_SQL.format(marks=marks), list(chunk)):
            maker = repository.from_row(DecisionMaker, row)
            clusters[owner[maker.business_id]].people.append(
                Person(maker, repository.from_row(Provenance, row, prefix="p_"))
            )
        for row in conn.execute(
            "SELECT w.business_id, w.domain FROM websites w "  # noqa: S608
            f"JOIN extractions x ON x.id = w.extraction_id WHERE w.business_id IN ({marks}) "
            f"AND {repository.CURRENT_EXTRACTION}",
            list(chunk),
        ):
            domain = normalize.registrable_domain(row["domain"])
            clusters[owner[row["business_id"]]].website_domains.add(domain)
        for row in conn.execute(
            "SELECT business_id, country_code FROM business_locations "  # noqa: S608
            f"WHERE business_id IN ({marks}) AND country_code IS NOT NULL",
            list(chunk),
        ):
            clusters[owner[row["business_id"]]].countries.add(row["country_code"])
    for chunk in _chunks([root for root in roots if root in clusters]):
        marks = _marks(chunk)
        for row in conn.execute(
            "SELECT business_id, field, observation_id, alternative_count "  # noqa: S608
            f"FROM canonical_values WHERE business_id IN ({marks}) AND superseded_at IS NULL "
            "AND field IN ('phone', 'email')",
            list(chunk),
        ):
            clusters[row["business_id"]].canonical[row["field"]] = (
                row["observation_id"],
                row["alternative_count"],
            )
        for row in conn.execute(
            f"SELECT {repository._columns(ReviewDecision)} FROM review_decisions "  # noqa: S608
            f"WHERE entity_type = 'business' AND entity_id IN ({marks}) "
            "AND decision IN (?, ?, ?) ORDER BY decided_at, id",
            [*chunk, *CONTACT_DECISIONS],
        ):
            decision = repository.from_row(ReviewDecision, row)
            cluster = clusters[decision.entity_id]
            cluster.decisions.append(decision)
            if decision.decision == REVIEW_CLEARED:
                cluster.reviews.pop(decision.new_value or "", None)
            else:
                cluster.reviews[decision.new_value or ""] = decision
    return [clusters[root] for root in roots if root in clusters]


def live_suppressions(conn: sqlite3.Connection) -> dict[tuple[str, str], ContactSuppression]:
    entries = (
        repository.from_row(ContactSuppression, row)
        for row in conn.execute(
            f"SELECT {repository._columns(ContactSuppression)} FROM contact_suppressions "  # noqa: S608
            "WHERE lifted_at IS NULL"
        )
    )
    return {(entry.kind.value, entry.value): entry for entry in entries}


def matching_suppression(
    contact_type: ContactType,
    value: str,
    suppressions: Mapping[tuple[str, str], ContactSuppression],
) -> ContactSuppression | None:
    """The live entry that covers a value: its number or address, or the domain of an address or
    of a form. Business entries are matched by the caller, who knows the cluster."""
    lookups: list[tuple[str, str]] = []
    if contact_type is ContactType.PHONE:
        lookups.append(("phone", value))
    elif contact_type is ContactType.EMAIL:
        found = normalize.email(value)
        if found.match_key and found.domain:
            domain = normalize.registrable_domain(found.domain)
            lookups += [("email", found.match_key), ("domain", domain)]
    elif contact_type in _FORMS and (site_domain := normalize.website(value).registrable_domain):
        lookups.append(("domain", site_domain))
    return next((suppressions[lookup] for lookup in lookups if lookup in suppressions), None)


def business_suppression(
    cluster: Cluster, suppressions: Mapping[tuple[str, str], ContactSuppression]
) -> ContactSuppression | None:
    return next(
        (
            suppressions[("business", member)]
            for member in cluster.members
            if ("business", member) in suppressions
        ),
        None,
    )


def mail_results(
    conn: sqlite3.Connection,
    domains: Iterable[str],
    now: datetime,
    lookup: Lookup | None,
    *,
    budget: list[int] | None = None,
    force: bool = False,
) -> tuple[dict[str, MailDomainResult], int, int]:
    """(answer by domain, lookups made, lookups that failed). Cached answers are used as they are; a
    lookup is made only with `lookup`, for a missing or expired answer (any answer with `force`),
    and a failed lookup never replaces an answer the domain gave before."""
    wanted = sorted(set(domains))
    cached: dict[str, tuple[MailDomainResult, str]] = {}
    for chunk in _chunks(wanted):
        for row in conn.execute(
            "SELECT domain, result, expires_at FROM mail_domain_checks "  # noqa: S608
            f"WHERE domain IN ({_marks(chunk)})",
            list(chunk),
        ):
            cached[row["domain"]] = (MailDomainResult(row["result"]), row["expires_at"])
    results = {domain: result for domain, (result, _) in cached.items()}
    if lookup is None:
        return results, 0, 0
    stamp = format_ts(now)
    checked = failed = 0
    for domain in wanted:
        known = cached.get(domain)
        if not force and known is not None and known[1] > stamp:
            continue
        if budget is not None:
            if budget[0] <= 0:
                break
            budget[0] -= 1
        result, exchanges = lookup(domain)
        checked += 1
        if result in _FAILED_LOOKUPS:
            failed += 1
            if known is not None and known[0] not in _FAILED_LOOKUPS:
                continue  # a timeout says nothing about the domain: keep what it answered before
        with repository.atomic(conn):
            conn.execute(
                "INSERT INTO mail_domain_checks (domain, result, exchanges_json, checked_at, "
                "expires_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (domain) DO UPDATE SET result = excluded.result, "
                "exchanges_json = excluded.exchanges_json, checked_at = excluded.checked_at, "
                "expires_at = excluded.expires_at, updated_at = excluded.updated_at",
                (
                    domain,
                    result.value,
                    _json({"exchanges": list(exchanges)}),
                    stamp,
                    format_ts(now + MAIL_TTL[result]),
                    stamp,
                    stamp,
                ),
            )
        results[domain] = result
    return results, checked, failed


def _email_domains(clusters: Iterable[Cluster]) -> set[str]:
    return {
        domain
        for cluster in clusters
        for observation in cluster.rows
        if observation.point.contact_type is ContactType.EMAIL
        and (
            domain := rules.mail_domain_of(
                observation.point.value_normalized or observation.point.value_raw
            )
        )
    }


# ---- channels ------------------------------------------------------------------------------------


@dataclass(slots=True)
class Channel:
    key: str
    contact_type: ContactType
    value: str
    rows: list[Observation]
    platform: str | None
    level: ContactLevel
    location_id: str | None
    decision_maker_id: str | None
    relationship: DecisionMakerRelationship | None
    role: ContactRole
    role_or_department: str | None
    origin: ContactOrigin
    status: ValidationStatus
    method: str
    signals: rules.Signals
    freshness: ContactFreshness
    first_seen_at: datetime
    last_seen_at: datetime
    source_kinds: frozenset[str]
    currents: dict[str, bool]
    is_primary: bool
    suppression: ContactSuppression | None
    review: ReviewDecision | None
    shared_with: int = 0

    @property
    def fictional(self) -> bool:
        return any(code == "fictional_range" for code, _ in self.signals)

    @property
    def normalized(self) -> str | None:
        return next(
            (row.point.value_normalized for row in self.rows if row.point.value_normalized), None
        )


def _check(
    cluster: Cluster,
    contact_type: ContactType,
    value: str,
    rows: Sequence[Observation],
    mail: Mapping[str, MailDomainResult],
) -> tuple[ValidationStatus, str, rules.Signals]:
    """The checks of a channel before its sources, freshness, reviews and suppressions count."""
    if contact_type is ContactType.PHONE:
        checks = [rules.check_phone(row.point.value_raw, cluster.country) for row in rows]
        if value.startswith("+"):
            checks.insert(0, rules.check_phone(value, None))
        chosen = checks[0]
        extension = next((check.extension for check in checks if check.extension), None)
        signals = chosen.signals
        if extension and all(code != "extension" for code, _ in signals):
            signals = (*signals, ("extension", extension))
        return chosen.status, "numbering_plan", signals
    if contact_type is ContactType.EMAIL:
        domain = rules.mail_domain_of(value)
        email = rules.check_email(
            value, mail.get(domain) if domain else None, cluster.website_domains
        )
        return email.status, email.method, email.signals
    if contact_type in _FORMS:
        for row in rows:
            detail = row.detail.get("detail")
            if isinstance(detail, dict):
                return rules.check_form(detail)
        return ValidationStatus.OBSERVED, "form_structure", ()
    social = normalize.social_profile(value)
    if social.handle:
        return ValidationStatus.STRUCTURALLY_VALID, "structure", (("platform", social.platform),)
    return ValidationStatus.INVALID, "structure", ()


def build_channels(
    cluster: Cluster,
    *,
    now: datetime,
    mail: Mapping[str, MailDomainResult],
    suppressions: Mapping[tuple[str, str], ContactSuppression],
    currency: Currency,
) -> list[Channel]:
    """Every channel of a canonical business, decided from what is stored now."""
    groups: dict[str, list[Observation]] = defaultdict(list)
    for observation in cluster.rows:
        groups[observation.key].append(observation)
    makers = {person.maker.id: person.maker for person in cluster.people}
    candidates = sorted(
        (
            maker
            for maker in makers.values()
            if maker.review_status is not DecisionMakerReview.REJECTED
        ),
        key=lambda maker: (maker.full_name_normalized, maker.id),
    )
    whole = business_suppression(cluster, suppressions)
    shown = {observation_id for observation_id, _ in cluster.canonical.values()}
    channels: list[Channel] = []
    for key, rows in sorted(groups.items()):
        contact_type = rows[0].point.contact_type
        origin = min((row.point.origin for row in rows), key=_ORIGIN_RANK.__getitem__)
        published = [
            row for row in rows if row.point.origin is not ContactOrigin.HYPOTHESIS
        ] or rows
        value = next(
            (row.point.value_normalized for row in published if row.point.value_normalized),
            published[0].point.value_raw,
        )
        base, method, signals = _check(cluster, contact_type, value, published, mail)
        signal_map = dict(signals)
        maker_id = next(
            (row.point.decision_maker_id for row in published if row.point.decision_maker_id), None
        )
        if (
            maker_id is None
            and contact_type is ContactType.EMAIL
            and "role_address" not in signal_map
        ):
            local = normalize.email(value).local
            maker_id = next(
                (
                    maker.id
                    for maker in candidates
                    if local and rules.email_pattern(local, maker.full_name_raw)
                ),
                None,
            )
        location_id = next(
            (row.point.location_id for row in published if row.point.location_id), None
        )
        department = next(
            (row.point.role_or_department for row in published if row.point.role_or_department),
            None,
        )
        if maker_id is not None:
            level, role = ContactLevel.PERSON, ContactRole.PERSONAL
        else:
            level = ContactLevel.LOCATION if location_id else ContactLevel.BUSINESS
            role_address = signal_map.get("role_address")
            if role_address is not None and department in (None, role_address):
                role = ContactRole.ROLE_ADDRESS
            else:
                role = ContactRole.DEPARTMENT if department else ContactRole.GENERAL
        last_seen = max(row.point.last_seen_at for row in published)
        currents = {row.point.id: currency.current(row) for row in rows}
        kinds = frozenset(row.provenance.connector_kind.value for row in published)
        suppression = whole or matching_suppression(contact_type, value, suppressions)
        review = cluster.reviews.get(key)
        fresh = rules.freshness(contact_type, last_seen, now)
        status, status_method = rules.channel_status(
            rules.ChannelInput(
                contact_type=contact_type,
                base=base,
                base_method=method,
                signals=signal_map,
                freshness=fresh,
                current=any(currents[row.point.id] for row in published),
                source_kinds=kinds,
                own_site=any(
                    row.provenance.connector_key == CRAWL_CONNECTOR_KEY for row in published
                ),
                hypothesis=origin is ContactOrigin.HYPOTHESIS,
                suppressed=suppression is not None,
                review=_REVIEW_STATES.get(review.decision) if review else None,
            )
        )
        if (
            contact_type in (ContactType.PHONE, ContactType.EMAIL)
            and contact_type.value in cluster.canonical
        ):
            primary = any(row.point.id in shown for row in rows)
        else:
            primary = any(row.point.is_primary for row in published)
        channels.append(
            Channel(
                key=key,
                contact_type=contact_type,
                value=value,
                rows=rows,
                platform=next((row.point.platform for row in rows if row.point.platform), None),
                level=level,
                location_id=location_id,
                decision_maker_id=maker_id,
                relationship=makers[maker_id].relationship if maker_id in makers else None,
                role=role,
                role_or_department=department,
                origin=origin,
                status=status,
                method=status_method,
                signals=signals,
                freshness=fresh,
                first_seen_at=min(row.point.first_seen_at for row in published),
                last_seen_at=last_seen,
                source_kinds=kinds,
                currents=currents,
                is_primary=primary,
                suppression=suppression,
                review=review,
            )
        )
    return channels


def write_statuses(conn: sqlite3.Connection, channels: Sequence[Channel], now: datetime) -> int:
    """Writes each channel's status, method and suppression to its observations, with history.
    A hypothesis the business now publishes itself is no longer needed. Returns rows changed."""
    stamp = format_ts(now)
    history: list[Record] = []
    changed = 0
    for channel in channels:
        reason = channel.suppression.reason if channel.suppression else None
        for observation in channel.rows:
            point = observation.point
            if (
                point.origin is ContactOrigin.HYPOTHESIS
                and channel.origin is not ContactOrigin.HYPOTHESIS
            ):
                repository.soft_delete(
                    conn,
                    ContactPoint,
                    point.id,
                    actor=ACTOR,
                    reason="the business publishes this address itself",
                )
                changed += 1
                continue
            status, method = channel.status, channel.method
            current = (
                point.validation_status,
                point.validation_method,
                point.do_not_contact,
                point.suppression_reason,
            )
            if current == (status, method, reason is not None, reason):
                continue
            if point.validation_status is ValidationStatus.SUPPRESSED and status not in (
                ValidationStatus.SUPPRESSED,
                ValidationStatus.OBSERVED,
            ):
                # a suppression is only ever lifted back to observed (the state machine)
                conn.execute(
                    "UPDATE contact_points SET validation_status = 'observed' WHERE id = ?",
                    (point.id,),
                )
            conn.execute(
                "UPDATE contact_points SET validation_status = ?, validation_method = ?, "
                "validated_at = ?, do_not_contact = ?, suppression_reason = ?, updated_at = ? "
                "WHERE id = ?",
                (status.value, method, stamp, int(reason is not None), reason, stamp, point.id),
            )
            if (point.validation_status, point.validation_method) != (status, method):
                history.append(
                    ContactValidation(
                        id=new_id(),
                        contact_point_id=point.id,
                        status=status,
                        method=method,
                        validator_version=rules.RULES_VERSION,
                        detail_json={"channel": channel.key, "signals": _signals(channel.signals)},
                        validated_at=now,
                        expires_at=None,
                    )
                )
            changed += 1
    repository.insert(conn, *history)
    return changed


def review_people(
    conn: sqlite3.Connection,
    cluster: Cluster,
    page_roots: Mapping[str, frozenset[str]],
    now: datetime,
) -> int:
    """Marks as ambiguous the people the store has doubts about: a name published with roles that
    disagree, on a site several businesses share, or that is part of the business's own name. A
    person's decision is never overridden. Returns how many people are ambiguous."""
    roles: dict[str, set[DecisionMakerRelationship]] = defaultdict(set)
    for person in cluster.people:
        if person.maker.review_status is not DecisionMakerReview.REJECTED:
            roles[person.maker.full_name_normalized].add(person.maker.relationship)
    business_name = sort_key(cluster.name)
    stamp = format_ts(now)
    ambiguous = 0
    for person in cluster.people:
        maker = person.maker
        if maker.review_status in (DecisionMakerReview.CONFIRMED, DecisionMakerReview.REJECTED):
            continue
        stored = maker.review_reasons_json or {}
        found = stored.get("page")
        page = [code for code in found if isinstance(code, str)] if isinstance(found, list) else []
        codes = set(page)
        if len(roles[maker.full_name_normalized] - {DecisionMakerRelationship.UNKNOWN}) > 1:
            codes.add("conflicting_roles")
        if len(page_roots.get(person.provenance.raw_record_id, frozenset())) > 1:
            codes.add("shared_site")
        if maker.full_name_normalized in business_name:
            codes.add("name_matches_business")
        status = DecisionMakerReview.AMBIGUOUS if codes else DecisionMakerReview.UNREVIEWED
        ambiguous += status is DecisionMakerReview.AMBIGUOUS
        reasons: JsonObject | None = (
            {"codes": list[JsonValue](sorted(codes)), "page": list[JsonValue](page)}
            if codes
            else None
        )
        if (status, reasons) != (maker.review_status, maker.review_reasons_json):
            conn.execute(
                "UPDATE decision_makers SET review_status = ?, review_reasons_json = ?, "
                "updated_at = ? WHERE id = ?",
                (status.value, _json(reasons) if reasons else None, stamp, maker.id),
            )
    return ambiguous


def _extraction(conn: sqlite3.Connection, raw_record_id: str, extractor: str, now: datetime) -> str:
    row = conn.execute(
        "SELECT id FROM extractions WHERE raw_record_id = ? AND extractor = ? "
        "AND extractor_version = ?",
        (raw_record_id, extractor, rules.RULES_VERSION),
    ).fetchone()
    if row is not None:
        return str(row["id"])
    extraction = Extraction(
        id=new_id(),
        raw_record_id=raw_record_id,
        extractor=extractor,
        extractor_version=rules.RULES_VERSION,
        extracted_at=now,
    )
    repository.insert(conn, extraction)
    return extraction.id


def add_hypotheses(
    conn: sqlite3.Connection, cluster: Cluster, channels: Sequence[Channel], now: datetime
) -> int:
    """Hypotheses for published people without an address, when a published personal address on the
    business's own domain follows a pattern of another published person's name. Stored as
    hypotheses: never primary, never validated, never exported by default."""
    makers = [
        person.maker
        for person in cluster.people
        if person.maker.review_status is not DecisionMakerReview.REJECTED
    ]
    counts: Counter[tuple[str, str]] = Counter()
    evidence: dict[tuple[str, str], tuple[Channel, DecisionMaker]] = {}
    for channel in channels:
        if (
            channel.contact_type is not ContactType.EMAIL
            or channel.origin is ContactOrigin.HYPOTHESIS
            or channel.role is ContactRole.ROLE_ADDRESS
        ):
            continue
        found = normalize.email(channel.value)
        if not found.local or not found.domain or found.is_free_provider:
            continue
        if normalize.registrable_domain(found.domain) not in cluster.website_domains:
            continue
        for maker in makers:
            pattern = rules.email_pattern(found.local, maker.full_name_raw)
            if pattern in rules.HYPOTHESIS_PATTERNS:
                counts[(pattern, found.domain)] += 1
                evidence.setdefault((pattern, found.domain), (channel, maker))
    if not counts:
        return 0
    (pattern, domain), _ = min(counts.items(), key=lambda item: (-item[1], item[0]))
    source, known = evidence[(pattern, domain)]
    names = {maker.id: maker.full_name_normalized for maker in makers}
    linked = {
        names[channel.decision_maker_id]
        for channel in channels
        if channel.contact_type is ContactType.EMAIL and channel.decision_maker_id in names
    }
    keys = {channel.key for channel in channels}
    records: list[Record] = []
    extraction_id: str | None = None
    for maker in makers:
        if maker.full_name_normalized in linked:
            continue
        address = rules.hypothesis_address(pattern, maker.full_name_raw, domain)
        key = rules.channel_key(ContactType.EMAIL, address, address) if address else None
        if address is None or key is None or key in keys:
            continue
        keys.add(key)
        linked.add(maker.full_name_normalized)
        extraction_id = extraction_id or _extraction(
            conn, source.rows[0].provenance.raw_record_id, EXTRACTOR_PATTERN, now
        )
        records.append(
            ContactPoint(
                id=new_id(),
                business_id=maker.business_id,
                location_id=None,
                decision_maker_id=maker.id,
                contact_type=ContactType.EMAIL,
                value_raw=address,
                value_normalized=address,
                origin=ContactOrigin.HYPOTHESIS,
                platform=None,
                web_page_id=None,
                role_or_department=None,
                published_context=(
                    f"hypothesis: built with the pattern {pattern} of "
                    f"{source.value}, published for {known.full_name_raw}; "
                    "not confirmed"
                ),
                is_primary=False,
                validation_status=ValidationStatus.UNKNOWN,
                validated_at=now,
                validation_method="pattern_hypothesis",
                do_not_contact=False,
                suppression_reason=None,
                confidence=0.2,
                extraction_id=extraction_id,
                first_seen_at=source.first_seen_at,
                last_seen_at=source.last_seen_at,
                created_at=now,
                updated_at=now,
                deleted_at=None,
                deleted_reason=None,
            )
        )
    repository.insert(conn, *records)
    return len(records)


# ---- validation runs -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ValidationContext:
    currency: Currency
    #: The canonical businesses each crawled page speaks for.
    page_roots: Mapping[str, frozenset[str]]
    suppressions: Mapping[tuple[str, str], ContactSuppression]


def validation_context(conn: sqlite3.Connection, project_id: str) -> ValidationContext:
    roots = {
        row["id"]: row["merged_into_id"] or row["id"]
        for row in conn.execute(
            "SELECT id, merged_into_id FROM businesses WHERE project_id = ?", (project_id,)
        )
    }
    page_roots = {
        raw_record_id: frozenset(roots.get(business, business) for business in businesses)
        for raw_record_id, businesses in page_links(conn, project_id).items()
    }
    return ValidationContext(currency(conn, project_id), page_roots, live_suppressions(conn))


def root_ids(conn: sqlite3.Connection, project_id: str) -> list[str]:
    return [
        row["id"]
        for row in conn.execute(
            "SELECT id FROM businesses WHERE project_id = ? AND deleted_at IS NULL "
            "AND merged_into_id IS NULL ORDER BY id",
            (project_id,),
        )
    ]


@dataclass(frozen=True, slots=True)
class ValidateBatch:
    channels: int
    changed: int
    hypotheses: int
    checked: int
    failures: int
    ambiguous: int
    statuses: Counter[ValidationStatus]


def validate_batch(
    conn: sqlite3.Connection,
    context: ValidationContext,
    roots: Sequence[str],
    now: datetime,
    lookup: Lookup | None,
    budget: list[int],
) -> ValidateBatch:
    """Decides and writes the channels and people of a batch of canonical businesses."""
    clusters = load_clusters(conn, roots)
    mail, checked, failures = mail_results(
        conn, _email_domains(clusters), now, lookup, budget=budget
    )
    statuses: Counter[ValidationStatus] = Counter()
    total = changed = created = ambiguous = 0
    with repository.atomic(conn):
        for cluster in clusters:
            ambiguous += review_people(conn, cluster, context.page_roots, now)
            channels = build_channels(
                cluster,
                now=now,
                mail=mail,
                suppressions=context.suppressions,
                currency=context.currency,
            )
            created += add_hypotheses(conn, cluster, channels, now)
            changed += write_statuses(conn, channels, now)
            published = [
                channel for channel in channels if channel.origin is not ContactOrigin.HYPOTHESIS
            ]
            statuses.update(channel.status for channel in published)
            total += len(published)
    return ValidateBatch(total, changed, created, checked, failures, ambiguous, statuses)


# ---- reads -------------------------------------------------------------------------------------


def _channels_now(
    conn: sqlite3.Connection, clusters: Sequence[Cluster], now: datetime
) -> list[list[Channel]]:
    """The channels of clusters as reads show them: cached mail answers only, never the network."""
    if not clusters:
        return []
    mail, _, _ = mail_results(conn, _email_domains(clusters), now, None)
    suppressions = live_suppressions(conn)
    currencies: dict[str, Currency] = {}
    out: list[list[Channel]] = []
    for cluster in clusters:
        if cluster.project_id not in currencies:
            currencies[cluster.project_id] = currency(conn, cluster.project_id)
        out.append(
            build_channels(
                cluster,
                now=now,
                mail=mail,
                suppressions=suppressions,
                currency=currencies[cluster.project_id],
            )
        )
    return out


def _channel_view(channel: Channel) -> ContactChannel:
    validated = [row.point.validated_at for row in channel.rows if row.point.validated_at]
    return ContactChannel(
        key=channel.key,
        contact_type=channel.contact_type,
        value=channel.value,
        platform=channel.platform,
        level=channel.level,
        location_id=channel.location_id,
        decision_maker_id=channel.decision_maker_id,
        role=channel.role,
        role_or_department=channel.role_or_department,
        origin=channel.origin,
        validation_status=channel.status,
        validation_method=channel.method,
        validated_at=max(validated) if validated else None,
        signals=[
            ContactSignal(code=code, value=value[:300] if value else None)
            for code, value in channel.signals
        ],
        freshness=channel.freshness,
        first_seen_at=channel.first_seen_at,
        last_seen_at=channel.last_seen_at,
        source_count=len({row.provenance.raw_record_id for row in channel.rows}),
        source_kinds=sorted({row.provenance.connector_kind for row in channel.rows}),
        is_primary=channel.is_primary,
        suppression=channel.suppression,
        shared_with=channel.shared_with,
        review=channel.review,
        sources=[
            ContactSource(
                contact_point_id=row.point.id,
                business_id=row.point.business_id,
                origin=row.point.origin,
                value_raw=row.point.value_raw,
                published_context=row.point.published_context,
                role_or_department=row.point.role_or_department,
                confidence=row.point.confidence,
                observed_at=row.point.first_seen_at,
                current=channel.currents[row.point.id],
                provenance=row.provenance,
            )
            for row in channel.rows
        ],
    )


def _contactability(channels: Sequence[Channel], suppressed: bool) -> Contactability:
    return rules.contactability(
        [
            rules.ContactabilityInput(
                contact_type=channel.contact_type,
                status=channel.status,
                hypothesis=channel.origin is ContactOrigin.HYPOTHESIS,
                role=channel.role,
                level=channel.level,
                relationship=channel.relationship,
                freshness=channel.freshness,
                source_kinds=channel.source_kinds,
                fictional=channel.fictional,
            )
            for channel in channels
        ],
        business_suppressed=suppressed,
    )


def get_contact_detail(
    conn: sqlite3.Connection, business_id: str, now: datetime | None = None
) -> ContactDetail:
    business, members = _cluster(conn, business_id)
    clusters = load_clusters(conn, [members[0]])
    if not clusters:
        raise repository.NotFoundError(f"business {business_id} not found")
    cluster = clusters[0]
    now = now or utc_now()
    (channels,) = _channels_now(conn, [cluster], now)
    for channel in channels:
        if channel.origin is ContactOrigin.HYPOTHESIS or channel.normalized is None:
            continue
        (channel.shared_with,) = conn.execute(
            "SELECT count(DISTINCT coalesce(b.merged_into_id, b.id)) FROM contact_points c "
            "JOIN businesses b ON b.id = c.business_id WHERE c.contact_type = ? "
            "AND c.value_normalized = ? AND c.deleted_at IS NULL AND b.deleted_at IS NULL "
            "AND b.project_id = ? AND coalesce(b.merged_into_id, b.id) <> ?",
            (channel.contact_type.value, channel.normalized, cluster.project_id, cluster.root),
        ).fetchone()
    published = [channel for channel in channels if channel.origin is not ContactOrigin.HYPOTHESIS]
    conflicts: list[ContactConflict] = []
    for field_name, (_, count) in sorted(cluster.canonical.items()):
        contact_type = ContactType(field_name)
        values = sorted(
            {channel.value for channel in published if channel.contact_type is contact_type}
        )
        if count > 1 and len(values) > 1:
            conflicts.append(
                ContactConflict(code="values_disagree", contact_type=contact_type, values=values)
            )
    conflicts += [
        ContactConflict(
            code="shared_value", contact_type=channel.contact_type, values=[channel.value]
        )
        for channel in published
        if channel.shared_with
    ]
    keys: dict[str, set[str]] = defaultdict(set)
    ids_by_name: dict[str, set[str]] = defaultdict(set)
    roles: dict[str, set[DecisionMakerRelationship]] = defaultdict(set)
    for person in cluster.people:
        ids_by_name[person.maker.full_name_normalized].add(person.maker.id)
        roles[person.maker.full_name_normalized].add(person.maker.relationship)
    for channel in channels:
        if channel.decision_maker_id:
            keys[channel.decision_maker_id].add(channel.key)
    people: list[DecisionMakerView] = []
    shown_names: set[str] = set()
    for person in cluster.people:  # newest observation of each name first
        name = person.maker.full_name_normalized
        if name in shown_names:
            continue
        shown_names.add(name)
        found = sorted({key for maker_id in ids_by_name[name] for key in keys.get(maker_id, ())})
        people.append(
            DecisionMakerView(
                decision_maker=person.maker,
                provenance=person.provenance,
                reasons=_codes(person.maker.review_reasons_json),
                channel_keys=found,
            )
        )
        if len(roles[name] - {DecisionMakerRelationship.UNKNOWN}) > 1:
            conflicts.append(
                ContactConflict(
                    code="person_roles_disagree",
                    contact_type=None,
                    values=[person.maker.full_name_raw],
                )
            )
    suppressions = live_suppressions(conn)
    whole = business_suppression(cluster, suppressions)
    return ContactDetail(
        business_id=business.id,
        root_business_id=cluster.root,
        channels=[_channel_view(channel) for channel in published],
        hypotheses=[
            _channel_view(channel)
            for channel in channels
            if channel.origin is ContactOrigin.HYPOTHESIS
        ],
        decision_makers=people,
        conflicts=conflicts,
        contactability=_contactability(channels, whole is not None),
        business_suppression=whole,
        decisions=list(reversed(cluster.decisions))[:MAX_DECISIONS],
        rules_version=rules.RULES_VERSION,
        extractor_version=rules.EXTRACTOR_VERSION,
    )


_Sorted = tuple[str, int, str, str]


def _project_items(
    conn: sqlite3.Connection, project_id: str | None, now: datetime
) -> list[tuple[_Sorted, ContactListItem]]:
    where, params = ["b.deleted_at IS NULL", "b.merged_into_id IS NULL", "p.deleted_at IS NULL"], []
    if project_id is not None:
        where.append("b.project_id = ?")
        params.append(project_id)
    rows = conn.execute(
        "SELECT b.id, b.name_sort_key FROM businesses b JOIN projects p ON p.id = b.project_id "  # noqa: S608
        f"WHERE {' AND '.join(where)} AND EXISTS (SELECT 1 FROM contact_points c "
        "WHERE c.deleted_at IS NULL AND (c.business_id = b.id OR c.business_id IN "
        "(SELECT m.id FROM businesses m WHERE m.merged_into_id = b.id))) "
        "ORDER BY b.name_sort_key, b.id",
        params,
    ).fetchall()
    names = {row["id"]: row["name_sort_key"] for row in rows}
    items: list[tuple[_Sorted, ContactListItem]] = []
    for chunk in _chunks([row["id"] for row in rows], 200):
        clusters = load_clusters(conn, chunk)
        for cluster, channels in zip(clusters, _channels_now(conn, clusters, now), strict=True):
            for channel in channels:
                item = ContactListItem(
                    business_id=cluster.root,
                    business_name=cluster.name,
                    project_id=cluster.project_id,
                    key=channel.key,
                    contact_type=channel.contact_type,
                    value=channel.value,
                    platform=channel.platform,
                    level=channel.level,
                    role=channel.role,
                    role_or_department=channel.role_or_department,
                    origin=channel.origin,
                    validation_status=channel.status,
                    freshness=channel.freshness,
                    source_count=len({row.provenance.raw_record_id for row in channel.rows}),
                    last_seen_at=channel.last_seen_at,
                    is_primary=channel.is_primary,
                    suppressed=channel.suppression is not None,
                    is_synthetic=cluster.is_synthetic,
                )
                order = (
                    names[cluster.root],
                    _TYPE_ORDER[channel.contact_type],
                    channel.key,
                    channel.rows[0].point.id,
                )
                items.append((order, item))
    items.sort(key=lambda pair: pair[0])
    return items


def _wanted(item: ContactListItem, query: ContactListQuery) -> bool:
    return (
        (query.include_hypotheses or item.origin is not ContactOrigin.HYPOTHESIS)
        and (query.include_suppressed or not item.suppressed)
        and query.contact_type in (None, item.contact_type)
        and query.validation_status in (None, item.validation_status)
        and query.role in (None, item.role)
        and query.freshness in (None, item.freshness)
    )


def list_contacts(
    conn: sqlite3.Connection, query: ContactListQuery, now: datetime | None = None
) -> ContactPage:
    """Channels of canonical businesses, by business name then type. Statuses are decided now."""
    # ponytail: decides every channel of the project per page; materialise them if projects grow
    # past tens of thousands of contacts
    items = [
        pair
        for pair in _project_items(conn, query.project_id, now or utc_now())
        if _wanted(pair[1], query)
    ]
    total = len(items)
    if query.cursor is not None:
        last_key, last_id = repository.decode_cursor(query.cursor, "contacts")
        sort_name, type_order, key = orjson.loads(last_key)
        marker = (str(sort_name), int(type_order), str(key), last_id)
        items = [pair for pair in items if pair[0] > marker]
    page = items[: query.limit]
    next_cursor = (
        repository.encode_cursor("contacts", _json(list(page[-1][0][:3])), page[-1][0][3])
        if len(items) > query.limit
        else None
    )
    return ContactPage(items=[item for _, item in page], next_cursor=next_cursor, total=total)


def export_channels(
    conn: sqlite3.Connection,
    project_id: str | None,
    *,
    include_hypotheses: bool = False,
    now: datetime | None = None,
) -> list[ContactListItem]:
    """What an export of contacts may carry: never a suppressed channel, and hypotheses only when
    they are asked for (phase 13 writes the files from this query)."""
    return [
        item
        for _, item in _project_items(conn, project_id, now or utc_now())
        if not item.suppressed
        and (include_hypotheses or item.origin is not ContactOrigin.HYPOTHESIS)
    ]


# ---- actions -----------------------------------------------------------------------------------


def _refresh(conn: sqlite3.Connection, root: str, now: datetime) -> None:
    """Writes the statuses of one business's channels as they are decided now."""
    clusters = load_clusters(conn, [root])
    if not clusters:
        return
    (channels,) = _channels_now(conn, clusters, now)
    with repository.atomic(conn):
        write_statuses(conn, channels, now)


def act(
    conn: sqlite3.Connection,
    request: ContactActionRequest,
    *,
    actor: str,
    lookup: Lookup | None,
    now: datetime | None = None,
) -> None:
    """One action on one channel of a canonical business; its statuses are written right away."""
    now = now or utc_now()
    business, _ = _cluster(conn, request.business_id)
    if business.merged_into_id is not None:
        raise ContactError("this record belongs to another business: act on that business instead")
    clusters = load_clusters(conn, [business.id])
    (channels,) = _channels_now(conn, clusters, now)
    channel = next((found for found in channels if found.key == request.key), None)
    if channel is None:
        raise repository.NotFoundError(
            f"channel {request.key} not found for business {business.id}"
        )
    reason = (request.reason or "").strip() or None
    action = request.action
    if reason is None and action not in (ContactAction.VALIDATE, ContactAction.MARK_PRIMARY):
        raise ContactError("this action needs a reason")
    hypothesis = channel.origin is ContactOrigin.HYPOTHESIS
    decision: str | None = None
    match action:
        case ContactAction.VALIDATE:
            domain = (
                rules.mail_domain_of(channel.value)
                if channel.contact_type is ContactType.EMAIL
                else None
            )
            if domain and request.check_mail_domains and lookup is not None:
                mail_results(conn, [domain], now, lookup, force=True)
        case ContactAction.CONFIRM:
            if hypothesis:
                raise ContactError(
                    "a hypothesis is never confirmed: it is a guess built from an address pattern"
                )
            if channel.status is ValidationStatus.INVALID and channel.method != "manual_review":
                raise ContactError("its own checks refuse this value: it cannot be confirmed")
            decision = CONFIRMED
        case ContactAction.MARK_INVALID:
            decision = MARKED_INVALID
        case ContactAction.CLEAR_REVIEW:
            if channel.review is None:
                raise ContactError("there is no review to clear")
            decision = REVIEW_CLEARED
        case ContactAction.MARK_PRIMARY:
            if hypothesis or channel.status in (
                ValidationStatus.INVALID,
                ValidationStatus.SUPPRESSED,
            ):
                raise ContactError(
                    "only a published or imported channel that is neither invalid "
                    "nor suppressed can be the one the business shows"
                )
            _mark_primary(conn, business.id, clusters[0], channel, reason, actor)
        case ContactAction.SUPPRESS:
            if channel.contact_type not in (ContactType.PHONE, ContactType.EMAIL):
                raise ContactError(
                    "only a phone or an email is suppressed on its own: suppress its "
                    "domain or the business in the do-not-contact list"
                )
            kind = SuppressionKind(channel.contact_type.value)
            value = rules.normalize_suppression(kind, channel.value)
            if value is None:
                raise ContactError(f"this {kind.value} cannot be suppressed: it is not a valid one")
            if (
                add_suppression(
                    conn,
                    kind,
                    value,
                    business_id=None,
                    reason=reason or "",
                    source="manual",
                    actor=actor,
                    now=now,
                )
                is None
            ):
                raise ContactError("this value is already on the do-not-contact list")
    if decision is not None:
        with repository.atomic(conn):
            repository.insert(
                conn,
                ReviewDecision(
                    id=new_id(),
                    entity_type=EntityType.BUSINESS,
                    entity_id=business.id,
                    decision=decision,
                    previous_value=channel.status.value,
                    new_value=channel.key,
                    reason=reason or "",
                    actor=actor,
                    decided_at=now,
                ),
            )
    _refresh(conn, business.id, now)


def _mark_primary(
    conn: sqlite3.Connection,
    root: str,
    cluster: Cluster,
    channel: Channel,
    reason: str | None,
    actor: str,
) -> None:
    rows = [row for row in channel.rows if row.point.origin is not ContactOrigin.HYPOTHESIS]
    if channel.contact_type in (ContactType.PHONE, ContactType.EMAIL):
        # the business shows its phone and email through its canonical values (phase 06)
        chosen = next(
            (row for row in rows if row.point.validation_status is not ValidationStatus.INVALID),
            None,
        )
        if chosen is None:
            raise ContactError(
                "none of this channel's observations can be shown yet: validate it first"
            )
        resolution_store.choose_canonical(
            conn,
            CanonicalChoiceRequest(
                business_id=root,
                field=CanonicalField(channel.contact_type.value),
                observation_id=chosen.point.id,
                reason=reason,
            ),
            actor=actor,
        )
        return
    same_type = [
        row.point.id for row in cluster.rows if row.point.contact_type is channel.contact_type
    ]
    with repository.atomic(conn):
        for chunk in _chunks(same_type):
            conn.execute(
                f"UPDATE contact_points SET is_primary = 0 WHERE id IN ({_marks(chunk)})",  # noqa: S608
                list(chunk),
            )
        conn.execute(
            "UPDATE contact_points SET is_primary = 1, updated_at = ? WHERE id = ?",
            (format_ts(utc_now()), rows[0].point.id),
        )
        repository.audit(
            conn,
            AuditAction.UPDATE,
            EntityType.CONTACT_POINT,
            rows[0].point.id,
            actor=actor,
            detail={"is_primary": True, "reason": reason},
        )


# ---- decision makers -----------------------------------------------------------------------------

_MAKERS_SQL: Final = (
    "SELECT " + repository._columns(DecisionMaker, "f") + ", " + repository._PROVENANCE + ", "  # noqa: S608
    "coalesce(b.merged_into_id, b.id) AS root_id, rb.display_name AS root_name, "
    "p.is_synthetic AS synthetic FROM decision_makers f JOIN businesses b ON b.id = f.business_id "
    "JOIN businesses rb ON rb.id = coalesce(b.merged_into_id, b.id) "
    "JOIN projects p ON p.id = b.project_id JOIN extractions x ON x.id = f.extraction_id "
    "JOIN raw_records r ON r.id = x.raw_record_id "
    "JOIN source_connectors sc ON sc.id = r.connector_id "
    "WHERE f.deleted_at IS NULL AND b.deleted_at IS NULL AND p.deleted_at IS NULL AND "
    + repository.CURRENT_EXTRACTION
)


def _maker_items(
    conn: sqlite3.Connection, where: str, params: Sequence[object]
) -> list[DecisionMakerItem]:
    """One item per published name within a canonical business: its newest observation, with the
    doubts of all of them."""
    groups: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for row in conn.execute(
        _MAKERS_SQL + where + " ORDER BY f.full_name_normalized, f.observed_at DESC, f.id", params
    ):
        groups[(row["root_id"], row["full_name_normalized"])].append(row)
    items = [
        DecisionMakerItem(
            decision_maker=repository.from_row(DecisionMaker, rows[0]),
            business_name=rows[0]["root_name"],
            root_business_id=rows[0]["root_id"],
            reasons=sorted({code for row in rows for code in _codes(row["review_reasons_json"])}),
            provenance=repository.from_row(Provenance, rows[0], prefix="p_"),
            is_synthetic=bool(rows[0]["synthetic"]),
        )
        for rows in groups.values()
    ]
    return sorted(
        items, key=lambda item: (item.decision_maker.full_name_normalized, item.decision_maker.id)
    )


def list_decision_makers(
    conn: sqlite3.Connection, query: DecisionMakerListQuery
) -> DecisionMakerPage:
    where, params = "", list[object]()
    if query.project_id is not None:
        where += " AND b.project_id = ?"
        params.append(query.project_id)
    if query.review_status is not None:
        where += " AND f.review_status = ?"
        params.append(query.review_status.value)
    items = _maker_items(conn, where, params)
    total = len(items)
    if query.cursor is not None:
        last_key, last_id = repository.decode_cursor(query.cursor, "people")
        items = [
            item
            for item in items
            if (item.decision_maker.full_name_normalized, item.decision_maker.id)
            > (last_key, last_id)
        ]
    page = items[: query.limit]
    next_cursor = (
        repository.encode_cursor(
            "people", page[-1].decision_maker.full_name_normalized, page[-1].decision_maker.id
        )
        if len(items) > query.limit
        else None
    )
    return DecisionMakerPage(items=page, next_cursor=next_cursor, total=total)


def review_decision_maker(
    conn: sqlite3.Connection, request: DecisionMakerReviewRequest, *, actor: str
) -> DecisionMakerItem:
    """A person's decision about a published name, for every observation of it in the business.
    Rejecting a person also withdraws the hypotheses built for them."""
    reason = request.reason.strip()
    if not reason:
        raise ContactError("a review needs a reason")
    maker = repository.get(conn, DecisionMaker, request.decision_maker_id)
    if maker.deleted_at is not None:
        raise repository.NotFoundError(f"decision maker {maker.id} has been deleted")
    business = repository.get(conn, Business, maker.business_id)
    root = business.merged_into_id or business.id
    rows = conn.execute(
        "SELECT f.id, f.review_reasons_json FROM decision_makers f "
        "JOIN businesses b ON b.id = f.business_id WHERE f.deleted_at IS NULL "
        "AND f.full_name_normalized = ? AND (b.id = ? OR b.merged_into_id = ?)",
        (maker.full_name_normalized, root, root),
    ).fetchall()
    now = utc_now()
    stamp = format_ts(now)
    target = {
        DecisionMakerDecision.CONFIRM: DecisionMakerReview.CONFIRMED,
        DecisionMakerDecision.REJECT: DecisionMakerReview.REJECTED,
    }.get(request.decision)
    with repository.atomic(conn):
        for row in rows:
            status = target or (
                DecisionMakerReview.AMBIGUOUS
                if _codes(row["review_reasons_json"])
                else DecisionMakerReview.UNREVIEWED
            )
            conn.execute(
                "UPDATE decision_makers SET review_status = ?, updated_at = ? WHERE id = ?",
                (status.value, stamp, row["id"]),
            )
        new_value = target or (
            DecisionMakerReview.AMBIGUOUS
            if _codes(maker.review_reasons_json)
            else DecisionMakerReview.UNREVIEWED
        )
        repository.insert(
            conn,
            ReviewDecision(
                id=new_id(),
                entity_type=EntityType.DECISION_MAKER,
                entity_id=maker.id,
                decision=f"decision_maker_{request.decision.value}",
                previous_value=maker.review_status.value,
                new_value=new_value.value,
                reason=reason,
                actor=actor,
                decided_at=now,
            ),
        )
        if request.decision is DecisionMakerDecision.REJECT and rows:
            ids = [row["id"] for row in rows]
            for hypothesis in conn.execute(
                "SELECT id FROM contact_points WHERE origin = 'hypothesis' "  # noqa: S608
                f"AND deleted_at IS NULL AND decision_maker_id IN ({_marks(ids)})",
                ids,
            ).fetchall():
                repository.soft_delete(
                    conn,
                    ContactPoint,
                    hypothesis["id"],
                    actor=actor,
                    reason="the person it was built for was rejected",
                )
    return _maker_items(conn, " AND f.id = ?", [maker.id])[0]


# ---- suppressions --------------------------------------------------------------------------------


def _match(conn: sqlite3.Connection, entry: ContactSuppression) -> tuple[str, list[object]]:
    """The observations one entry covers, as a condition on `contact_points c`."""
    match entry.kind:
        case SuppressionKind.PHONE:
            return "c.contact_type = 'phone' AND c.value_normalized = ?", [entry.value]
        case SuppressionKind.EMAIL:
            return "c.contact_type = 'email' AND lower(c.value_normalized) = ?", [entry.value]
        case SuppressionKind.DOMAIN:
            domain = entry.value
            return (
                "((c.contact_type = 'email' AND (lower(c.value_normalized) LIKE ? "
                "OR lower(c.value_normalized) LIKE ?)) OR (c.contact_type IN ('contact_form', "
                "'booking_form') AND (lower(c.value_normalized) LIKE ? "
                "OR lower(c.value_normalized) LIKE ?)))",
                [f"%@{domain}", f"%@%.{domain}", f"%://{domain}/%", f"%://%.{domain}/%"],
            )
    business = repository.get(conn, Business, entry.business_id or entry.value)
    root = business.merged_into_id or business.id
    members = [
        root,
        *(
            row["id"]
            for row in conn.execute("SELECT id FROM businesses WHERE merged_into_id = ?", (root,))
        ),
    ]
    return f"c.business_id IN ({_marks(members)})", list[object](members)


def _flag(conn: sqlite3.Connection, entry: ContactSuppression, now: datetime) -> int:
    clause, params = _match(conn, entry)
    rows = conn.execute(
        f"SELECT c.id, c.origin, c.validation_status FROM contact_points c "  # noqa: S608
        f"WHERE c.deleted_at IS NULL AND {clause}",
        params,
    ).fetchall()
    stamp = format_ts(now)
    history: list[Record] = []
    for row in rows:
        if row["origin"] == ContactOrigin.HYPOTHESIS:
            conn.execute(
                "UPDATE contact_points SET do_not_contact = 1, suppression_reason = ?, "
                "updated_at = ? WHERE id = ?",
                (entry.reason, stamp, row["id"]),
            )
            continue
        conn.execute(
            "UPDATE contact_points SET do_not_contact = 1, suppression_reason = ?, "
            "validation_status = 'suppressed', validation_method = 'suppression_list', "
            "validated_at = ?, updated_at = ? WHERE id = ?",
            (entry.reason, stamp, stamp, row["id"]),
        )
        if row["validation_status"] != ValidationStatus.SUPPRESSED:
            history.append(
                ContactValidation(
                    id=new_id(),
                    contact_point_id=row["id"],
                    status=ValidationStatus.SUPPRESSED,
                    method="suppression_list",
                    validator_version=rules.RULES_VERSION,
                    detail_json={"suppression_id": entry.id, "kind": entry.kind.value},
                    validated_at=now,
                    expires_at=None,
                )
            )
    repository.insert(conn, *history)
    return len(rows)


def _unflag(conn: sqlite3.Connection, entry: ContactSuppression, now: datetime) -> int:
    """Observations the lifted entry covered and no other live entry covers go back to `observed`
    (the only way out of `suppressed`); the next run or read decides them again."""
    others = live_suppressions(conn)
    clause, params = _match(conn, entry)
    rows = conn.execute(
        "SELECT c.id, c.origin, c.contact_type, c.value_normalized, c.value_raw, c.business_id, "  # noqa: S608
        "coalesce(b.merged_into_id, b.id) AS root_id FROM contact_points c "
        f"JOIN businesses b ON b.id = c.business_id WHERE c.deleted_at IS NULL "
        f"AND c.do_not_contact = 1 AND {clause}",
        params,
    ).fetchall()
    stamp = format_ts(now)
    history: list[Record] = []
    lifted = 0
    for row in rows:
        value = row["value_normalized"] or row["value_raw"]
        still = (
            matching_suppression(ContactType(row["contact_type"]), value, others)
            or others.get(("business", row["business_id"]))
            or others.get(("business", row["root_id"]))
        )
        if still is not None:
            conn.execute(
                "UPDATE contact_points SET suppression_reason = ? WHERE id = ?",
                (still.reason, row["id"]),
            )
            continue
        lifted += 1
        if row["origin"] == ContactOrigin.HYPOTHESIS:
            conn.execute(
                "UPDATE contact_points SET do_not_contact = 0, suppression_reason = NULL, "
                "updated_at = ? WHERE id = ?",
                (stamp, row["id"]),
            )
            continue
        conn.execute(
            "UPDATE contact_points SET do_not_contact = 0, suppression_reason = NULL, "
            "validation_status = 'observed', validation_method = 'suppression_lifted', "
            "validated_at = ?, updated_at = ? WHERE id = ?",
            (stamp, stamp, row["id"]),
        )
        history.append(
            ContactValidation(
                id=new_id(),
                contact_point_id=row["id"],
                status=ValidationStatus.OBSERVED,
                method="suppression_lifted",
                validator_version=rules.RULES_VERSION,
                detail_json={"suppression_id": entry.id, "kind": entry.kind.value},
                validated_at=now,
                expires_at=None,
            )
        )
    repository.insert(conn, *history)
    return lifted


def add_suppression(
    conn: sqlite3.Connection,
    kind: SuppressionKind,
    value: str,
    *,
    business_id: str | None,
    reason: str,
    source: str,
    actor: str,
    now: datetime,
) -> ContactSuppression | None:
    """Adds a normalised entry and applies it; None when the value is already suppressed."""
    if conn.execute(
        "SELECT 1 FROM contact_suppressions WHERE kind = ? AND value = ? AND lifted_at IS NULL",
        (kind.value, value),
    ).fetchone():
        return None
    if kind is SuppressionKind.BUSINESS:
        business = repository.get(conn, Business, value)
        if business.deleted_at is not None:
            raise repository.NotFoundError(f"business {value} has been deleted")
        business_id = business.id
    entry = ContactSuppression(
        id=new_id(),
        kind=kind,
        value=value,
        business_id=business_id,
        reason=reason.strip()[:2000],
        source=source[:300],
        actor=actor,
        created_at=now,
        lifted_at=None,
        lifted_by=None,
        lift_reason=None,
    )
    with repository.atomic(conn):
        repository.insert(conn, entry)
        repository.audit(
            conn,
            AuditAction.CREATE,
            EntityType.CONTACT_SUPPRESSION,
            entry.id,
            actor=actor,
            detail={"kind": kind.value, "source": entry.source},
        )
        _flag(conn, entry, now)
    return entry


def change_suppression(
    conn: sqlite3.Connection, request: SuppressionChangeRequest, *, actor: str
) -> ContactSuppression:
    reason = request.reason.strip()
    if not reason:
        raise ContactError("a change to the do-not-contact list needs a reason")
    now = utc_now()
    if request.suppression_id is None:
        if request.kind is None or request.value is None:
            raise ContactError("a new entry needs a kind and a value")
        value = rules.normalize_suppression(request.kind, request.value)
        if value is None:
            raise ContactError(f"not a valid {request.kind.value}")
        entry = add_suppression(
            conn,
            request.kind,
            value,
            business_id=None,
            reason=reason,
            source="manual",
            actor=actor,
            now=now,
        )
        if entry is None:
            raise ContactError("this value is already on the do-not-contact list")
        return entry
    entry = repository.get(conn, ContactSuppression, request.suppression_id)
    if entry.lifted_at is not None:
        raise ContactError("this entry was already lifted")
    stamp = format_ts(now)
    with repository.atomic(conn):
        conn.execute(
            "UPDATE contact_suppressions SET lifted_at = ?, lifted_by = ?, lift_reason = ? "
            "WHERE id = ?",
            (stamp, actor, reason, entry.id),
        )
        repository.audit(
            conn,
            AuditAction.UPDATE,
            EntityType.CONTACT_SUPPRESSION,
            entry.id,
            actor=actor,
            detail={"lifted": True, "reason": reason},
        )
        _unflag(conn, entry, now)
    return repository.get(conn, ContactSuppression, entry.id)


def list_suppressions(conn: sqlite3.Connection, query: SuppressionListQuery) -> SuppressionPage:
    where, params = ["1 = 1"], list[object]()
    if query.kind is not None:
        where.append("kind = ?")
        params.append(query.kind.value)
    if not query.include_lifted:
        where.append("lifted_at IS NULL")
    (total,) = conn.execute(
        f"SELECT count(*) FROM contact_suppressions WHERE {' AND '.join(where)}",  # noqa: S608
        params,
    ).fetchone()
    if query.cursor is not None:
        last_key, last_id = repository.decode_cursor(query.cursor, "suppressions")
        where.append("(created_at, id) < (?, ?)")
        params += [last_key, last_id]
    rows = conn.execute(
        f"SELECT {repository._columns(ContactSuppression)} FROM contact_suppressions "  # noqa: S608
        f"WHERE {' AND '.join(where)} ORDER BY created_at DESC, id DESC LIMIT ?",
        [*params, query.limit + 1],
    ).fetchall()
    page = rows[: query.limit]
    next_cursor = (
        repository.encode_cursor("suppressions", page[-1]["created_at"], page[-1]["id"])
        if len(rows) > query.limit
        else None
    )
    return SuppressionPage(
        items=[repository.from_row(ContactSuppression, row) for row in page],
        next_cursor=next_cursor,
        total=total,
    )


def import_suppressions(
    conn: sqlite3.Connection, request: SuppressionImportRequest, *, actor: str
) -> SuppressionImportResult:
    """Adds the valid rows of a CSV in one transaction; every other row says why it was left out."""
    rows, problems = rules.read_suppressions(request.content)
    found = [
        SuppressionImportProblem(row=problem.row, code=problem.code, message=problem.message)
        for problem in problems
    ]
    added = already = 0
    now = utc_now()
    filename = request.filename.strip() or "import.csv"
    with repository.atomic(conn):
        for row in rows:
            if (
                row.kind is SuppressionKind.BUSINESS
                and conn.execute(
                    "SELECT 1 FROM businesses WHERE id = ? AND deleted_at IS NULL", (row.value,)
                ).fetchone()
                is None
            ):
                found.append(
                    SuppressionImportProblem(
                        row=row.row, code="unknown_business", message="no live business has this id"
                    )
                )
                continue
            entry = add_suppression(
                conn,
                row.kind,
                row.value,
                business_id=None,
                reason=row.reason or f"Imported from {filename}",
                source=f"import:{filename}",
                actor=actor,
                now=now,
            )
            added += entry is not None
            already += entry is None
    found.sort(key=lambda problem: problem.row)
    return SuppressionImportResult(
        rows=len(rows) + len(problems),
        added=added,
        already_suppressed=already,
        problems=found[:500],
    )


def export_suppressions(
    conn: sqlite3.Connection, request: SuppressionExportRequest
) -> SuppressionExport:
    now = utc_now()
    where = "" if request.include_lifted else "WHERE lifted_at IS NULL "
    entries = [
        repository.from_row(ContactSuppression, row)
        for row in conn.execute(
            f"SELECT {repository._columns(ContactSuppression)} FROM contact_suppressions "  # noqa: S608
            f"{where}ORDER BY created_at, id"
        )
    ]
    content = rules.write_suppressions(
        [
            [
                entry.kind.value,
                entry.value,
                entry.reason,
                entry.source,
                format_ts(entry.created_at),
                format_ts(entry.lifted_at) if entry.lifted_at else "",
            ]
            for entry in entries
        ]
    )
    return SuppressionExport(
        filename=f"do-not-contact-{now:%Y%m%d}.csv",
        content=content,
        rows=len(entries),
        exported_at=now,
    )


# ---- runs ----------------------------------------------------------------------------------------


def plan_runs(
    conn: sqlite3.Connection, project_id: str | None, check_mail_domains: bool, *, actor: str
) -> tuple[list[str], list[str]]:
    """Creates one queued job per project to check. Returns (created, already open) job ids."""
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
            raise ContactError("there are no businesses to check yet: discover or import some")
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
                    raise ContactError(
                        "a contact run is already open for this project: resume or cancel it"
                    )
                listed.append(open_job["id"])
                continue
            job = Job(
                id=new_id(),
                project_id=pid,
                kind=JOB_KIND,
                status=JobStatus.QUEUED,
                priority=0,
                params_json={
                    "project_id": pid,
                    "check_mail_domains": check_mail_domains,
                    "extractor_version": rules.EXTRACTOR_VERSION,
                    "rules_version": rules.RULES_VERSION,
                },
                idempotency_key=None,
                progress_done=0,
                progress_total=None,
                requested_at=now,
                started_at=None,
                finished_at=None,
                created_at=now,
                updated_at=now,
            )
            repository.insert(conn, job)
            repository.audit(conn, AuditAction.CREATE, EntityType.JOB, job.id, actor=actor)
            jobs.write_event(
                conn,
                job.id,
                None,
                "run_queued",
                EventLevel.INFO,
                {"project_id": pid, "check_mail_domains": check_mail_domains},
            )
            created.append(job.id)
    return created, listed


def _run_view(conn: sqlite3.Connection, job: Job) -> ContactRun:
    state = jobs.latest_state(conn, job.id)
    stats = state.get("stats")
    counters = stats if isinstance(stats, dict) else {}
    project = repository.get(conn, Project, job.project_id)
    stage = state.get("stage")
    return ContactRun(
        job_id=job.id,
        project_id=job.project_id,
        project_name=project.name,
        status=job.status,
        stage=stage if isinstance(stage, str) and stage else "queued",
        progress_done=job.progress_done,
        progress_total=job.progress_total,
        extractor_version=str(job.params_json.get("extractor_version") or rules.EXTRACTOR_VERSION),
        rules_version=str(job.params_json.get("rules_version") or rules.RULES_VERSION),
        check_mail_domains=job.params_json.get("check_mail_domains") is True,
        stats=ContactStats.model_validate(
            {name: repository._counter(counters.get(name)) for name in ContactStats.model_fields}
        ),
        requested_at=job.requested_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        events=repository._run_events(conn, job.id),
        is_synthetic=project.is_synthetic,
    )


def get_contact_run(conn: sqlite3.Connection, job_id: str) -> ContactRun:
    return _run_view(conn, jobs.live_job(conn, job_id, JOB_KIND))


def runs_by_id(conn: sqlite3.Connection, job_ids: Sequence[str]) -> ContactRunPage:
    items = [get_contact_run(conn, job_id) for job_id in job_ids]
    return ContactRunPage(items=items, next_cursor=None, total=len(items))


def list_contact_runs(conn: sqlite3.Connection, query: ContactRunListQuery) -> ContactRunPage:
    where, params = ["j.kind = ?"], list[object]([JOB_KIND])
    if query.project_id is not None:
        where.append("j.project_id = ?")
        params.append(query.project_id)
    (total,) = conn.execute(
        f"SELECT count(*) FROM jobs j WHERE {' AND '.join(where)}",  # noqa: S608
        params,
    ).fetchone()
    if query.cursor is not None:
        last_key, last_id = repository.decode_cursor(query.cursor, "contact_runs")
        where.append("(j.requested_at, j.id) < (?, ?)")
        params += [last_key, last_id]
    rows = conn.execute(
        f"SELECT {repository._columns(Job, 'j')} FROM jobs j "  # noqa: S608
        f"WHERE {' AND '.join(where)} ORDER BY j.requested_at DESC, j.id DESC LIMIT ?",
        [*params, query.limit + 1],
    ).fetchall()
    page = rows[: query.limit]
    next_cursor = (
        repository.encode_cursor("contact_runs", page[-1]["requested_at"], page[-1]["id"])
        if len(rows) > query.limit
        else None
    )
    return ContactRunPage(
        items=[_run_view(conn, repository.from_row(Job, row)) for row in page],
        next_cursor=next_cursor,
        total=total,
    )
