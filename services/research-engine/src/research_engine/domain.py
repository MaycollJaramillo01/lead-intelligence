"""Data contract: enumerations, state machines and canonical records of Lead Intelligence.

Source of truth for every shape stored in the operational SQLite store and sent over IPC.
The SQL in `migrations/sqlite/0002_core_model.sql` is written by hand; `tests/test_model.py` proves
each record matches its table column by column, each enumeration matches its lookup table and each
state machine matches `status_transitions`, so the two cannot drift apart silently.

Rules the model enforces (docs/data-model.md):
- ids are UUIDv7 strings; names and domains are never keys;
- timestamps are UTC, serialised as `YYYY-MM-DDTHH:MM:SS.sssZ` in the database and over IPC;
- every observed value points at an extraction -> raw record -> source run -> connector;
- raw values are kept next to normalised ones; AI inferences live in their own tables;
- important rows are soft-deleted (`deleted_at` + `deleted_reason`) and audited.

Compatibility: DATA_CONTRACT_VERSION (contracts.py) is bumped for breaking changes (a removed or
renamed field, table or enumeration value). New nullable fields, new records and new enumeration
values are additive: they ship with a forward migration and keep the version.
"""

import os
import re
import threading
import time
import unicodedata
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, ClassVar, Literal
from uuid import UUID

from pydantic import AfterValidator, AwareDatetime, Field, JsonValue, PlainSerializer

from research_engine.contracts import Contract

MAX_PAGE_SIZE = 500

# ---- scalar types ------------------------------------------------------------------------------

UUID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
_RELATIVE_PATH = re.compile(r"^(?!/)(?!.*\\)(?!.*:)(?!(?:.*/)?\.\.(?:/|$)).+$")


def format_ts(value: datetime) -> str:
    """The one timestamp format of the data contract: UTC, milliseconds, `Z` suffix."""
    value = value.astimezone(UTC)
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"


def _to_utc_ms(value: datetime) -> datetime:
    value = value.astimezone(UTC)
    return value.replace(microsecond=value.microsecond // 1000 * 1000)


def _relative_path(value: str) -> str:
    if not _RELATIVE_PATH.match(value):
        raise ValueError("must be a relative path with forward slashes and no '..' segments")
    return value


Id = Annotated[str, Field(pattern=UUID_PATTERN)]
UtcTimestamp = Annotated[
    AwareDatetime,
    AfterValidator(_to_utc_ms),
    PlainSerializer(format_ts, return_type=str, when_used="json"),
]
Confidence = Annotated[float, Field(ge=0, le=1)]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Label = Annotated[str, Field(min_length=1, max_length=300)]
Code = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.]*$", max_length=64)]
RelativePath = Annotated[str, AfterValidator(_relative_path)]
JsonObject = dict[str, JsonValue]


def utc_now() -> datetime:
    return _to_utc_ms(datetime.now(UTC))


_id_lock = threading.Lock()
_id_state = [0, 0]  # last millisecond, 12-bit counter


def new_id() -> str:
    """UUIDv7 (RFC 9562) with a per-process monotonic counter: ids sort by creation order."""
    with _id_lock:
        ms = time.time_ns() // 1_000_000
        if ms > _id_state[0]:
            _id_state[0], _id_state[1] = ms, int.from_bytes(os.urandom(2)) & 0x3FF
        else:  # same millisecond (or clock moved back): keep counting on the last one
            _id_state[1] += 1
            if _id_state[1] > 0xFFF:
                _id_state[0], _id_state[1] = _id_state[0] + 1, 0
        ms, counter = _id_state
    tail = int.from_bytes(os.urandom(8)) & ((1 << 62) - 1)
    return str(UUID(int=(ms << 80) | (0x7 << 76) | (counter << 64) | (0b10 << 62) | tail))


def sort_key(text: str) -> str:
    """Accent- and case-insensitive ordering key (storage concern, not entity normalisation)."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(stripped.split())


# ---- enumerations (each one is a lookup table of the same name) --------------------------------


class BusinessStatus(StrEnum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    CLOSED = "closed"
    MERGED = "merged"


class SourceRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EvidenceType(StrEnum):
    IDENTITY = "identity"
    LOCATION = "location"
    CONTACT = "contact"
    OWNERSHIP_DECLARATION = "ownership_declaration"
    CERTIFICATION = "certification"
    DIRECTORY_MEMBERSHIP = "directory_membership"
    MARKET_LANGUAGE = "market_language"
    WEBSITE_SIGNAL = "website_signal"
    TECHNOLOGY = "technology"
    REPUTATION = "reputation"
    OPERATING_STATUS = "operating_status"


class EvidenceStrength(StrEnum):
    STRONG = "strong"
    MODERATE = "moderate"
    WEAK = "weak"


class EvidenceStatus(StrEnum):
    ACTIVE = "active"
    STALE = "stale"
    CONTRADICTED = "contradicted"
    REVOKED = "revoked"


class LatinoStatus(StrEnum):
    VERIFIED_LATINO_OWNED = "verified_latino_owned"
    HIGH_CONFIDENCE_LATINO_OWNED = "high_confidence_latino_owned"
    PROBABLE_LATINO_OWNED = "probable_latino_owned"
    SPANISH_MARKET_AFFINITY = "spanish_market_affinity"
    UNKNOWN = "unknown"


class ContactType(StrEnum):
    PHONE = "phone"
    EMAIL = "email"
    CONTACT_FORM = "contact_form"
    BOOKING_FORM = "booking_form"
    SOCIAL_PROFILE = "social_profile"


class ValidationStatus(StrEnum):
    OBSERVED = "observed"
    STRUCTURALLY_VALID = "structurally_valid"
    DOMAIN_VALID = "domain_valid"
    LIKELY_VALID = "likely_valid"
    INVALID = "invalid"
    UNKNOWN = "unknown"
    STALE = "stale"
    SUPPRESSED = "suppressed"


class WebsiteFindingSeverity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class WebsiteFindingResult(StrEnum):
    PASS = "pass"  # noqa: S105 - a rule outcome, not a password
    FAIL = "fail"
    WARNING = "warning"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class InferenceStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    INVALID_OUTPUT = "invalid_output"
    FAILED = "failed"
    CANCELLED = "cancelled"


class LeadStage(StrEnum):
    NOT_CONTACTED = "not_contacted"
    ATTEMPTED = "attempted"
    CONNECTED = "connected"
    QUALIFIED = "qualified"
    MEETING_BOOKED = "meeting_booked"
    PROPOSAL_SENT = "proposal_sent"
    WON = "won"
    LOST = "lost"
    INVALID_CONTACT = "invalid_contact"
    NOT_INTERESTED = "not_interested"


class ExportStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SourceKind(StrEnum):
    OFFICIAL_API = "official_api"
    PUBLIC_DATASET = "public_dataset"
    DIRECTORY = "directory"
    PUBLIC_REGISTRY = "public_registry"
    WEB_SEARCH = "web_search"
    PLATFORM_API = "platform_api"
    FILE_IMPORT = "file_import"
    MANUAL_SEED = "manual_seed"
    SYNTHETIC = "synthetic"


class AcquisitionMethod(StrEnum):
    API_REQUEST = "api_request"
    HTTP_FETCH = "http_fetch"
    BROWSER_RENDER = "browser_render"
    FILE_IMPORT = "file_import"
    MANUAL_ENTRY = "manual_entry"
    SYNTHETIC = "synthetic"


class EntityType(StrEnum):
    PROJECT = "project"
    CAMPAIGN = "campaign"
    ICP_PROFILE = "icp_profile"
    SOURCE_CONNECTOR = "source_connector"
    SOURCE_RUN = "source_run"
    RAW_RECORD = "raw_record"
    BUSINESS = "business"
    CONTACT_POINT = "contact_point"
    DECISION_MAKER = "decision_maker"
    EVIDENCE_ITEM = "evidence_item"
    LATINO_ASSESSMENT = "latino_assessment"
    WEBSITE_AUDIT = "website_audit"
    WEBSITE_FINDING = "website_finding"
    INFERENCE_RUN = "inference_run"
    SCORING_MODEL = "scoring_model"
    SCORE_RUN = "score_run"
    JOB = "job"
    DATASET = "dataset"
    DATASET_SNAPSHOT = "dataset_snapshot"
    EXPORT = "export"
    TAG = "tag"
    NOTE = "note"


class AuditAction(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    STATUS_CHANGE = "status_change"
    SOFT_DELETE = "soft_delete"
    RESTORE = "restore"
    PURGE = "purge"
    MERGE = "merge"
    UNMERGE = "unmerge"


class NameKind(StrEnum):
    LEGAL = "legal"
    TRADE = "trade"
    DBA = "dba"
    ALTERNATE = "alternate"


class ServiceAreaKind(StrEnum):
    CITY = "city"
    COUNTY = "county"
    STATE = "state"
    POSTAL_CODE = "postal_code"
    METRO = "metro"
    RADIUS = "radius"


class ClassificationKind(StrEnum):
    CATEGORY = "category"
    SERVICE = "service"


class PageKind(StrEnum):
    HOME = "home"
    ABOUT = "about"
    CONTACT = "contact"
    SERVICES = "services"
    OTHER = "other"


class ContactOrigin(StrEnum):
    PUBLISHED = "published"
    USER_IMPORT = "user_import"
    HYPOTHESIS = "hypothesis"


class ConversionElementKind(StrEnum):
    PRIMARY_CTA = "primary_cta"
    SECONDARY_CTA = "secondary_cta"
    CONTACT_FORM = "contact_form"
    BOOKING_WIDGET = "booking_widget"
    CHAT_WIDGET = "chat_widget"
    CLICK_TO_CALL = "click_to_call"
    MESSAGING_LINK = "messaging_link"
    OTHER = "other"


class AssessmentMethod(StrEnum):
    RULES = "rules"
    MANUAL_REVIEW = "manual_review"


class EvidenceRole(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    CONTEXT = "context"


class ExplanationKind(StrEnum):
    REASON = "reason"
    GATE = "gate"
    PENALTY = "penalty"
    MISSING_DATA = "missing_data"


class EventLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class ExportFormat(StrEnum):
    CSV = "csv"
    XLSX = "xlsx"
    JSON = "json"
    JSONL = "jsonl"
    PARQUET = "parquet"


def _table_name(enum: type[StrEnum]) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", enum.__name__).lower()


ENUMS: dict[str, type[StrEnum]] = {
    _table_name(enum): enum
    for enum in (
        BusinessStatus,
        SourceRunStatus,
        JobStatus,
        EvidenceType,
        EvidenceStrength,
        EvidenceStatus,
        LatinoStatus,
        ContactType,
        ValidationStatus,
        WebsiteFindingSeverity,
        WebsiteFindingResult,
        InferenceStatus,
        LeadStage,
        ExportStatus,
        SourceKind,
        AcquisitionMethod,
        EntityType,
        AuditAction,
        NameKind,
        ServiceAreaKind,
        ClassificationKind,
        PageKind,
        ContactOrigin,
        ConversionElementKind,
        AssessmentMethod,
        EvidenceRole,
        ExplanationKind,
        EventLevel,
        ExportFormat,
    )
}

# ---- state machines ----------------------------------------------------------------------------
# Allowed changes of a mutable status column; staying on the same value is always allowed.
# Enumerations that are results rather than states (LatinoStatus: each assessment is a new row)
# have no machine. The database enforces the same table through triggers.

_RUN_MACHINE: dict[str, tuple[str, ...]] = {
    "queued": ("running", "cancelled"),
    "running": ("paused", "succeeded", "failed", "cancelled"),
    "paused": ("running", "cancelled"),
    "failed": ("queued",),
    "succeeded": (),
    "cancelled": (),
}

TRANSITIONS: dict[type[StrEnum], dict[str, frozenset[str]]] = {
    enum: {source: frozenset(targets) for source, targets in machine.items()}
    for enum, machine in {
        BusinessStatus: {
            "candidate": ("active", "closed", "merged"),
            "active": ("closed", "merged"),
            "closed": ("active", "merged"),
            "merged": ("active",),  # unmerge
        },
        SourceRunStatus: _RUN_MACHINE,
        JobStatus: _RUN_MACHINE,
        EvidenceStatus: {
            "active": ("stale", "contradicted", "revoked"),
            "stale": ("active", "revoked"),
            "contradicted": ("active", "revoked"),
            "revoked": (),
        },
        ValidationStatus: {  # any re-validation result, but a suppression is only lifted explicitly
            status.value: (
                ("observed",)
                if status is ValidationStatus.SUPPRESSED
                else tuple(other.value for other in ValidationStatus if other is not status)
            )
            for status in ValidationStatus
        },
        InferenceStatus: {
            "queued": ("running", "cancelled"),
            "running": (
                "succeeded",
                "insufficient_evidence",
                "invalid_output",
                "failed",
                "cancelled",
            ),
            "failed": ("queued",),
            "invalid_output": ("queued",),
            "succeeded": (),
            "insufficient_evidence": (),
            "cancelled": (),
        },
        LeadStage: {
            "not_contacted": ("attempted", "invalid_contact", "not_interested"),
            "attempted": ("connected", "invalid_contact", "not_interested", "lost"),
            "connected": ("qualified", "not_interested", "lost"),
            "qualified": ("meeting_booked", "proposal_sent", "not_interested", "lost"),
            "meeting_booked": ("proposal_sent", "won", "lost"),
            "proposal_sent": ("won", "lost"),
            "won": (),
            "lost": ("not_contacted",),
            "invalid_contact": ("not_contacted",),
            "not_interested": ("not_contacted",),
        },
        ExportStatus: {
            "pending": ("running", "cancelled"),
            "running": ("succeeded", "failed", "cancelled"),
            "failed": ("pending",),
            "succeeded": (),
            "cancelled": (),
        },
    }.items()
}


def can_transition(current: StrEnum, target: StrEnum) -> bool:
    if type(current) is not type(target):
        raise TypeError(f"cannot compare {type(current).__name__} with {type(target).__name__}")
    return current == target or target.value in TRANSITIONS[type(current)][current.value]


# ---- records -----------------------------------------------------------------------------------


class Record(Contract):
    """One row of the operational store: field names mirror the table's columns."""

    TABLE: ClassVar[str]
    ENTITY: ClassVar[EntityType | None] = None


class Project(Record):
    """A local workspace. Synthetic projects hold test data only."""

    TABLE = "projects"
    ENTITY = EntityType.PROJECT
    id: Id
    name: Label
    description: str | None
    is_synthetic: bool
    created_at: UtcTimestamp
    updated_at: UtcTimestamp
    deleted_at: UtcTimestamp | None
    deleted_reason: str | None


class IcpProfile(Record):
    TABLE = "icp_profiles"
    ENTITY = EntityType.ICP_PROFILE
    id: Id
    project_id: Id
    name: Label
    description: str | None
    created_at: UtcTimestamp
    updated_at: UtcTimestamp
    deleted_at: UtcTimestamp | None
    deleted_reason: str | None


class IcpProfileVersion(Record):
    """Immutable definition of an ICP profile at one version."""

    TABLE = "icp_profile_versions"
    id: Id
    icp_profile_id: Id
    version: Annotated[int, Field(ge=1)]
    definition_json: JsonObject
    schema_version: Annotated[int, Field(ge=1)]
    created_by: Label
    created_at: UtcTimestamp


class Campaign(Record):
    """What to look for; `criteria_json` follows the discovery criteria schema version."""

    TABLE = "campaigns"
    ENTITY = EntityType.CAMPAIGN
    id: Id
    project_id: Id
    icp_profile_version_id: Id | None
    name: Label
    objective: str | None
    criteria_json: JsonObject
    criteria_schema_version: Annotated[int, Field(ge=1)]
    created_at: UtcTimestamp
    updated_at: UtcTimestamp
    deleted_at: UtcTimestamp | None
    deleted_reason: str | None


class SourceConnector(Record):
    TABLE = "source_connectors"
    ENTITY = EntityType.SOURCE_CONNECTOR
    id: Id
    key: Code
    name: Label
    kind: SourceKind
    connector_version: Label
    terms_url: str | None
    requires_credentials: bool
    enabled: bool
    created_at: UtcTimestamp
    updated_at: UtcTimestamp


class SourceConnectorConfig(Record):
    """Effective configuration and limits of a connector, globally or for one project."""

    TABLE = "source_connector_configs"
    id: Id
    connector_id: Id
    project_id: Id | None
    config_json: JsonObject
    requests_per_minute: Annotated[int, Field(gt=0)] | None
    daily_request_budget: Annotated[int, Field(gt=0)] | None
    max_concurrency: Annotated[int, Field(gt=0)] | None
    created_at: UtcTimestamp
    superseded_at: UtcTimestamp | None


class SourceRun(Record):
    TABLE = "source_runs"
    ENTITY = EntityType.SOURCE_RUN
    id: Id
    project_id: Id
    campaign_id: Id | None
    connector_id: Id
    connector_version: Label
    config_id: Id | None
    job_id: Id | None
    status: SourceRunStatus
    effective_config_json: JsonObject
    started_at: UtcTimestamp | None
    finished_at: UtcTimestamp | None
    error_code: Code | None
    error_message: str | None
    created_at: UtcTimestamp
    updated_at: UtcTimestamp


class SourceQuery(Record):
    TABLE = "source_queries"
    id: Id
    source_run_id: Id
    query_json: JsonObject
    cursor: str | None
    issued_at: UtcTimestamp
    result_count: Annotated[int, Field(ge=0)] | None


class AcquiredPage(Record):
    """One fetch: request URL, outcome and where its body is stored (relative to the data dir)."""

    TABLE = "acquired_pages"
    id: Id
    source_run_id: Id
    source_query_id: Id | None
    url: str
    final_url: str | None
    http_status: Annotated[int, Field(ge=100, le=599)] | None
    content_type: str | None
    content_sha256: Sha256 | None
    byte_size: Annotated[int, Field(ge=0)] | None
    artifact_path: RelativePath | None
    fetched_at: UtcTimestamp


class RawRecord(Record):
    """Immutable payload exactly as acquired. Either `source_url` or `external_id` identifies it."""

    TABLE = "raw_records"
    ENTITY = EntityType.RAW_RECORD
    id: Id
    source_run_id: Id
    acquired_page_id: Id | None
    connector_id: Id
    method: AcquisitionMethod
    external_id: str | None
    source_url: str | None
    payload_json: JsonObject
    payload_sha256: Sha256
    acquired_at: UtcTimestamp
    created_at: UtcTimestamp


class Extraction(Record):
    """One extractor pass over a raw record; every observed value points at one."""

    TABLE = "extractions"
    id: Id
    raw_record_id: Id
    extractor: Code
    extractor_version: Label
    extracted_at: UtcTimestamp


class DiscoveryCandidate(Record):
    """A discovered reference, not a business: the light projection of one raw record (phase 04).

    `dedup_key` is the connector's cheap identity guess; a row repeating a key already seen in the
    same campaign points at the first one through `duplicate_of_id`. Nothing is merged here - entity
    resolution is phase 06.
    """

    TABLE = "discovery_candidates"
    id: Id
    project_id: Id
    campaign_id: Id | None
    source_run_id: Id
    raw_record_id: Id
    connector_id: Id
    dedup_key: Label
    duplicate_of_id: Id | None
    name_raw: Label
    name_normalized: str
    locality: str | None
    region: str | None
    postal_code: str | None
    country_code: str | None
    domain: str | None
    website_url: str | None
    phone_raw: str | None
    category_raw: str | None
    external_id: str | None
    source_url: str | None
    discovered_at: UtcTimestamp
    created_at: UtcTimestamp


class Business(Record):
    """Canonical business. Display values are chosen from observations, which keep raw values."""

    TABLE = "businesses"
    ENTITY = EntityType.BUSINESS
    id: Id
    project_id: Id
    display_name: Label
    name_sort_key: str
    status: BusinessStatus
    lead_stage: LeadStage
    merged_into_id: Id | None
    first_seen_at: UtcTimestamp
    last_seen_at: UtcTimestamp
    created_at: UtcTimestamp
    updated_at: UtcTimestamp
    deleted_at: UtcTimestamp | None
    deleted_reason: str | None


class BusinessName(Record):
    TABLE = "business_names"
    id: Id
    business_id: Id
    extraction_id: Id
    name_raw: str
    name_normalized: str
    name_kind: NameKind
    is_primary: bool
    confidence: Confidence
    observed_at: UtcTimestamp
    created_at: UtcTimestamp


class BusinessLocation(Record):
    TABLE = "business_locations"
    id: Id
    business_id: Id
    extraction_id: Id
    address_raw: str | None
    street: str | None
    unit: str | None
    locality: str | None
    region: str | None
    postal_code: str | None
    country_code: Annotated[str, Field(pattern=r"^[A-Z]{2}$")] | None
    latitude: Annotated[float, Field(ge=-90, le=90)] | None
    longitude: Annotated[float, Field(ge=-180, le=180)] | None
    is_primary: bool
    confidence: Confidence
    observed_at: UtcTimestamp
    created_at: UtcTimestamp


class ServiceArea(Record):
    TABLE = "service_areas"
    id: Id
    business_id: Id
    extraction_id: Id
    area_raw: str
    area_kind: ServiceAreaKind
    area_normalized: str | None
    radius_km: Annotated[float, Field(gt=0)] | None
    confidence: Confidence
    observed_at: UtcTimestamp
    created_at: UtcTimestamp


class BusinessClassification(Record):
    """A category or a service as the source labelled it, optionally mapped to a taxonomy."""

    TABLE = "business_classifications"
    id: Id
    business_id: Id
    extraction_id: Id
    kind: ClassificationKind
    label_raw: str
    taxonomy_key: str | None
    taxonomy_version: str | None
    is_primary: bool
    confidence: Confidence
    observed_at: UtcTimestamp
    created_at: UtcTimestamp


class Website(Record):
    TABLE = "websites"
    id: Id
    business_id: Id
    extraction_id: Id
    url_raw: str
    url_normalized: str
    domain: str
    is_primary: bool
    confidence: Confidence
    observed_at: UtcTimestamp
    created_at: UtcTimestamp


class WebPage(Record):
    TABLE = "web_pages"
    id: Id
    website_id: Id
    acquired_page_id: Id | None
    url_raw: str
    url_normalized: str
    page_kind: PageKind
    title: str | None
    observed_at: UtcTimestamp
    created_at: UtcTimestamp


class DecisionMaker(Record):
    """A person only when published by a legitimate business source, with context."""

    TABLE = "decision_makers"
    ENTITY = EntityType.DECISION_MAKER
    id: Id
    business_id: Id
    extraction_id: Id
    full_name_raw: str
    full_name_normalized: str
    role_raw: str | None
    role_normalized: str | None
    published_context: str | None
    confidence: Confidence
    observed_at: UtcTimestamp
    created_at: UtcTimestamp
    updated_at: UtcTimestamp
    deleted_at: UtcTimestamp | None
    deleted_reason: str | None


class ContactPoint(Record):
    """Phone, email, form or social profile of a business, location or published person."""

    TABLE = "contact_points"
    ENTITY = EntityType.CONTACT_POINT
    id: Id
    business_id: Id
    location_id: Id | None
    decision_maker_id: Id | None
    contact_type: ContactType
    value_raw: str
    value_normalized: str | None
    origin: ContactOrigin
    platform: str | None
    web_page_id: Id | None
    role_or_department: str | None
    published_context: str | None
    is_primary: bool
    validation_status: ValidationStatus
    validated_at: UtcTimestamp | None
    do_not_contact: bool
    suppression_reason: str | None
    confidence: Confidence
    extraction_id: Id
    first_seen_at: UtcTimestamp
    last_seen_at: UtcTimestamp
    created_at: UtcTimestamp
    updated_at: UtcTimestamp
    deleted_at: UtcTimestamp | None
    deleted_reason: str | None


class ContactValidation(Record):
    TABLE = "contact_validations"
    id: Id
    contact_point_id: Id
    status: ValidationStatus
    method: Code
    validator_version: Label
    detail_json: JsonObject | None
    validated_at: UtcTimestamp
    expires_at: UtcTimestamp | None


class EvidenceItem(Record):
    """An observed claim: value, snippet and hash, strength, confidence. Only `status` changes."""

    TABLE = "evidence_items"
    ENTITY = EntityType.EVIDENCE_ITEM
    id: Id
    business_id: Id
    evidence_type: EvidenceType
    claim: Annotated[str, Field(min_length=1, max_length=500)]
    value_json: JsonValue
    strength: EvidenceStrength
    status: EvidenceStatus
    confidence: Confidence
    extraction_id: Id
    web_page_id: Id | None
    field_path: str | None
    snippet: Annotated[str, Field(max_length=2000)] | None
    snippet_sha256: Sha256 | None
    observed_at: UtcTimestamp
    created_at: UtcTimestamp
    updated_at: UtcTimestamp


class LatinoAssessment(Record):
    """One evaluation; a newer one supersedes it. Separate from the evidence it cites."""

    TABLE = "latino_assessments"
    ENTITY = EntityType.LATINO_ASSESSMENT
    id: Id
    business_id: Id
    status: LatinoStatus
    assessment_confidence: Confidence
    method: AssessmentMethod
    rules_version: Label
    inference_run_id: Id | None
    rationale: Annotated[str, Field(min_length=1)]
    limitations: str | None
    assessed_at: UtcTimestamp
    superseded_at: UtcTimestamp | None
    created_at: UtcTimestamp


class LatinoAssessmentEvidence(Record):
    TABLE = "latino_assessment_evidence"
    assessment_id: Id
    evidence_item_id: Id
    role: EvidenceRole


class WebsiteAudit(Record):
    TABLE = "website_audits"
    ENTITY = EntityType.WEBSITE_AUDIT
    id: Id
    website_id: Id
    ruleset_version: Label
    started_at: UtcTimestamp
    finished_at: UtcTimestamp | None
    pages_analyzed: Annotated[int, Field(ge=0)]
    created_at: UtcTimestamp


class WebsiteTechnology(Record):
    TABLE = "website_technologies"
    id: Id
    audit_id: Id
    name: Label
    category: str | None
    version: str | None
    confidence: Confidence
    evidence_item_id: Id | None


class ConversionElement(Record):
    TABLE = "conversion_elements"
    id: Id
    audit_id: Id
    web_page_id: Id | None
    element_kind: ConversionElementKind
    label: str | None
    confidence: Confidence
    evidence_item_id: Id | None


class WebsiteFinding(Record):
    TABLE = "website_findings"
    ENTITY = EntityType.WEBSITE_FINDING
    id: Id
    audit_id: Id
    rule_id: Code
    rule_version: Label
    category: Code
    result: WebsiteFindingResult
    severity: WebsiteFindingSeverity
    observed_value: str | None
    expected_condition: str | None
    remediation: str | None
    confidence: Confidence
    web_page_id: Id | None
    evidence_item_id: Id | None
    created_at: UtcTimestamp


class InferenceRun(Record):
    """One local-model call: task, runtime, model, prompt and schema versions, input hash."""

    TABLE = "ai_inference_runs"
    ENTITY = EntityType.INFERENCE_RUN
    id: Id
    task_type: Code
    task_version: Label
    provider: Code
    model: Label
    model_digest: str | None
    parameters_json: JsonObject
    prompt_template_id: Code
    prompt_template_version: Label
    output_schema_version: Label
    input_sha256: Sha256
    status: InferenceStatus
    started_at: UtcTimestamp | None
    finished_at: UtcTimestamp | None
    error_message: str | None
    created_at: UtcTimestamp
    updated_at: UtcTimestamp


class InferenceEvidence(Record):
    TABLE = "ai_inference_evidence"
    inference_run_id: Id
    evidence_item_id: Id


class InferenceOutput(Record):
    """Raw model output next to the validated structure. Never a fact by itself."""

    TABLE = "ai_inference_outputs"
    id: Id
    inference_run_id: Id
    business_id: Id | None
    raw_output: str
    validated_output_json: JsonObject | None
    confidence: Confidence | None
    created_at: UtcTimestamp


class ScoringModel(Record):
    TABLE = "scoring_models"
    ENTITY = EntityType.SCORING_MODEL
    id: Id
    key: Code
    name: Label
    description: str | None
    created_at: UtcTimestamp
    updated_at: UtcTimestamp
    deleted_at: UtcTimestamp | None
    deleted_reason: str | None


class ScoringModelVersion(Record):
    TABLE = "scoring_model_versions"
    id: Id
    scoring_model_id: Id
    version: Annotated[int, Field(ge=1)]
    definition_json: JsonObject
    is_heuristic: bool
    created_at: UtcTimestamp


class ScoreRun(Record):
    TABLE = "score_runs"
    ENTITY = EntityType.SCORE_RUN
    id: Id
    scoring_model_version_id: Id
    icp_profile_version_id: Id | None
    dataset_snapshot_id: Id | None
    job_id: Id | None
    started_at: UtcTimestamp
    finished_at: UtcTimestamp | None
    created_at: UtcTimestamp


class BusinessScore(Record):
    TABLE = "business_scores"
    id: Id
    score_run_id: Id
    business_id: Id
    total_score: Annotated[float, Field(ge=0, le=100)] | None
    confidence: Confidence | None
    created_at: UtcTimestamp


class ComponentScore(Record):
    TABLE = "component_scores"
    id: Id
    business_score_id: Id
    component: Annotated[str, Field(pattern=r"^[A-Z][A-Z_]*$", max_length=64)]
    value: Annotated[float, Field(ge=0, le=100)] | None
    weight: Annotated[float, Field(ge=0)]
    contribution: float | None
    confidence: Confidence | None


class ScoreExplanation(Record):
    TABLE = "score_explanations"
    id: Id
    business_score_id: Id
    component: str | None
    kind: ExplanationKind
    message: Annotated[str, Field(min_length=1)]
    evidence_item_id: Id | None
    sort_order: Annotated[int, Field(ge=0)]


class Job(Record):
    TABLE = "jobs"
    ENTITY = EntityType.JOB
    id: Id
    project_id: Id
    kind: Code
    status: JobStatus
    priority: int
    params_json: JsonObject
    idempotency_key: str | None
    progress_done: Annotated[int, Field(ge=0)]
    progress_total: Annotated[int, Field(ge=0)] | None
    requested_at: UtcTimestamp
    started_at: UtcTimestamp | None
    finished_at: UtcTimestamp | None
    created_at: UtcTimestamp
    updated_at: UtcTimestamp


class JobTask(Record):
    TABLE = "job_tasks"
    id: Id
    job_id: Id
    stage: Code
    task_key: Label
    status: JobStatus
    attempt_count: Annotated[int, Field(ge=0)]
    max_attempts: Annotated[int, Field(ge=1)]
    created_at: UtcTimestamp
    updated_at: UtcTimestamp


class TaskAttempt(Record):
    TABLE = "task_attempts"
    id: Id
    task_id: Id
    attempt: Annotated[int, Field(ge=1)]
    status: JobStatus
    started_at: UtcTimestamp
    finished_at: UtcTimestamp | None
    error_code: Code | None
    error_message: str | None
    retryable: bool | None


class JobCheckpoint(Record):
    TABLE = "job_checkpoints"
    id: Id
    job_id: Id
    task_id: Id | None
    state_json: JsonObject
    created_at: UtcTimestamp


class JobEvent(Record):
    """Progress, warnings and errors of a job; errors carry a code and retry classification."""

    TABLE = "job_events"
    id: Id
    job_id: Id
    task_id: Id | None
    level: EventLevel
    event: Code
    error_code: Code | None
    retryable: bool | None
    detail_json: JsonObject | None
    created_at: UtcTimestamp


class JobMetric(Record):
    TABLE = "job_metrics"
    id: Id
    job_id: Id
    task_id: Id | None
    name: Code
    value: float
    unit: str | None
    recorded_at: UtcTimestamp


class Dataset(Record):
    TABLE = "datasets"
    ENTITY = EntityType.DATASET
    id: Id
    project_id: Id
    name: Label
    description: str | None
    created_at: UtcTimestamp
    updated_at: UtcTimestamp
    deleted_at: UtcTimestamp | None
    deleted_reason: str | None


class DatasetSnapshot(Record):
    """Immutable, reproducible rows in DuckDB plus a partitioned Parquet copy."""

    TABLE = "dataset_snapshots"
    ENTITY = EntityType.DATASET_SNAPSHOT
    id: Id
    dataset_id: Id
    version: Annotated[int, Field(ge=1)]
    row_count: Annotated[int, Field(ge=0)]
    duckdb_table: Code
    parquet_path: RelativePath
    schema_version: Annotated[int, Field(ge=1)]
    filters_json: JsonObject
    created_at: UtcTimestamp


class DatasetSnapshotSource(Record):
    """Lineage of a snapshot: exactly one of a source run, a score run or a parent snapshot."""

    TABLE = "dataset_snapshot_sources"
    id: Id
    snapshot_id: Id
    source_run_id: Id | None
    score_run_id: Id | None
    parent_snapshot_id: Id | None
    created_at: UtcTimestamp


class Export(Record):
    TABLE = "exports"
    ENTITY = EntityType.EXPORT
    id: Id
    project_id: Id
    dataset_snapshot_id: Id | None
    status: ExportStatus
    format: ExportFormat
    file_name: str | None
    row_count: Annotated[int, Field(ge=0)] | None
    file_sha256: Sha256 | None
    manifest_json: JsonObject | None
    requested_at: UtcTimestamp
    started_at: UtcTimestamp | None
    finished_at: UtcTimestamp | None
    error_message: str | None
    created_at: UtcTimestamp
    updated_at: UtcTimestamp


class Tag(Record):
    TABLE = "tags"
    ENTITY = EntityType.TAG
    id: Id
    project_id: Id
    name: Annotated[str, Field(min_length=1, max_length=64)]
    created_at: UtcTimestamp
    deleted_at: UtcTimestamp | None
    deleted_reason: str | None


class EntityTag(Record):
    TABLE = "entity_tags"
    tag_id: Id
    entity_type: EntityType
    entity_id: Id
    created_at: UtcTimestamp


class Note(Record):
    TABLE = "notes"
    ENTITY = EntityType.NOTE
    id: Id
    project_id: Id
    entity_type: EntityType
    entity_id: Id
    body: Annotated[str, Field(min_length=1, max_length=20000)]
    author: Label
    created_at: UtcTimestamp
    updated_at: UtcTimestamp
    deleted_at: UtcTimestamp | None
    deleted_reason: str | None


class ReviewDecision(Record):
    """A human decision with its mandatory reason. Never edited; a new decision supersedes it."""

    TABLE = "review_decisions"
    id: Id
    entity_type: EntityType
    entity_id: Id
    decision: Code
    previous_value: str | None
    new_value: str | None
    reason: Annotated[str, Field(min_length=1)]
    actor: Label
    decided_at: UtcTimestamp


class AuditEntry(Record):
    """Append-only trail of important changes (status, soft delete, restore, purge, merge)."""

    TABLE = "audit_log"
    id: Id
    occurred_at: UtcTimestamp
    actor: Label
    action: AuditAction
    entity_type: EntityType
    entity_id: Id
    detail_json: JsonObject | None


RECORDS: tuple[type[Record], ...] = (
    Project,
    IcpProfile,
    IcpProfileVersion,
    Campaign,
    SourceConnector,
    SourceConnectorConfig,
    SourceRun,
    SourceQuery,
    AcquiredPage,
    RawRecord,
    Extraction,
    DiscoveryCandidate,
    Business,
    BusinessName,
    BusinessLocation,
    ServiceArea,
    BusinessClassification,
    Website,
    WebPage,
    DecisionMaker,
    ContactPoint,
    ContactValidation,
    EvidenceItem,
    LatinoAssessment,
    LatinoAssessmentEvidence,
    WebsiteAudit,
    WebsiteTechnology,
    ConversionElement,
    WebsiteFinding,
    InferenceRun,
    InferenceEvidence,
    InferenceOutput,
    ScoringModel,
    ScoringModelVersion,
    ScoreRun,
    BusinessScore,
    ComponentScore,
    ScoreExplanation,
    Job,
    JobTask,
    TaskAttempt,
    JobCheckpoint,
    JobEvent,
    JobMetric,
    Dataset,
    DatasetSnapshot,
    DatasetSnapshotSource,
    Export,
    Tag,
    EntityTag,
    Note,
    ReviewDecision,
    AuditEntry,
)

# ---- read models, queries and pages (IPC) ------------------------------------------------------


class Provenance(Contract):
    """Where one observed value came from: extraction, raw record, run and connector."""

    extraction_id: Id
    extractor: Code
    extractor_version: Label
    extracted_at: UtcTimestamp
    raw_record_id: Id
    source_run_id: Id
    acquired_page_id: Id | None
    connector_key: Code
    connector_name: Label
    connector_kind: SourceKind
    method: AcquisitionMethod
    source_url: str | None
    external_id: str | None
    acquired_at: UtcTimestamp


class Observed[ObservedT: Record](Contract):
    value: ObservedT
    provenance: Provenance


class ObservedName(Observed[BusinessName]):
    pass


class ObservedLocation(Observed[BusinessLocation]):
    pass


class ObservedWebsite(Observed[Website]):
    pass


class ObservedContact(Observed[ContactPoint]):
    pass


class ObservedClassification(Observed[BusinessClassification]):
    pass


class ObservedEvidence(Observed[EvidenceItem]):
    pass


class BusinessSort(StrEnum):
    NAME = "name"
    LAST_SEEN = "last_seen"


PageSize = Annotated[int, Field(ge=1, le=MAX_PAGE_SIZE)]
# the cursor carries the last sort key: a 300-character name in UTF-8 fits comfortably
Cursor = Annotated[str, Field(min_length=1, max_length=2048, pattern=r"^[A-Za-z0-9_-]+$")]


class BusinessListQuery(Contract):
    project_id: Id | None
    search: Annotated[str, Field(max_length=200)] | None
    status: BusinessStatus | None
    sort: BusinessSort
    limit: PageSize
    cursor: Cursor | None


class BusinessListItem(Contract):
    id: Id
    project_id: Id
    display_name: str
    status: BusinessStatus
    lead_stage: LeadStage
    locality: str | None
    region: str | None
    primary_domain: str | None
    primary_phone: str | None
    primary_category: str | None
    source_count: Annotated[int, Field(ge=0)]
    evidence_count: Annotated[int, Field(ge=0)]
    last_seen_at: UtcTimestamp
    is_synthetic: bool


class BusinessPage(Contract):
    """One keyset page. `total` counts every match of the filters, not only this page."""

    items: list[BusinessListItem]
    next_cursor: Cursor | None
    total: Annotated[int, Field(ge=0)]


class BusinessDetailQuery(Contract):
    business_id: Id


class BusinessDetail(Contract):
    business: Business
    is_synthetic: bool
    names: list[ObservedName]
    locations: list[ObservedLocation]
    websites: list[ObservedWebsite]
    contacts: list[ObservedContact]
    classifications: list[ObservedClassification]
    evidence: list[ObservedEvidence]


class DatasetListQuery(Contract):
    project_id: Id | None
    limit: PageSize
    cursor: Cursor | None


class DatasetListItem(Contract):
    dataset: Dataset
    is_synthetic: bool
    snapshot_count: Annotated[int, Field(ge=0)]
    latest_snapshot: DatasetSnapshot | None


class DatasetPage(Contract):
    items: list[DatasetListItem]
    next_cursor: Cursor | None
    total: Annotated[int, Field(ge=0)]


class SyntheticSeedResult(Contract):
    """Development-only seed. `created` is false when the synthetic workspace already existed."""

    project_id: Id
    business_id: Id
    business_count: Annotated[int, Field(ge=0)]
    dataset_id: Id
    created: bool


# ---- discovery: criteria DSL (phase 04) ---------------------------------------------------------
# `campaigns.criteria_json` holds one `DiscoveryCriteria` document and `criteria_schema_version`
# says which version it follows, so a campaign stored today keeps its meaning after the schema
# grows. Criteria are data, never free text. `market_languages` and `size_proxies` are signals to
# look for, never a claim about a business.

DISCOVERY_CRITERIA_SCHEMA_VERSION = 1
MAX_GEOGRAPHIES = 25
MAX_TERMS = 50
MAX_SOURCES = 12
#: An imported file travels once, inside one IPC frame; bigger files need a path-based connector.
MAX_IMPORT_CHARS = 4_000_000

CountryCode = Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
RegionCode = Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
PostalCode = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9 -]{1,11}$")]
NaicsCode = Annotated[str, Field(pattern=r"^\d{2,6}$")]
LanguageTag = Annotated[str, Field(pattern=r"^[a-z]{2}(-[A-Z]{2})?$")]
DomainName = Annotated[str, Field(pattern=r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9-]+)+$")]
IsoDate = Annotated[str, Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")]
Term = Annotated[str, Field(min_length=1, max_length=120)]
Terms = Annotated[list[Term], Field(max_length=MAX_TERMS)]
Message = Annotated[str, Field(min_length=1, max_length=500)]


class DiscoveryStrategy(StrEnum):
    """Broad first (cast wide, filter later) or narrow (only the tightest matches)."""

    BROAD = "broad"
    NARROW = "narrow"


class SizeProxy(StrEnum):
    """Observable stand-ins for company size. No source is asked to guess one it cannot see."""

    AWARD_VOLUME = "award_volume"
    EMPLOYEE_BAND = "employee_band"
    REVENUE_BAND = "revenue_band"
    LOCATION_COUNT = "location_count"


class ProblemSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class GeoScope(Contract):
    """One place to search. Sources declare how deep they can go (`Capability.GEOGRAPHY_*`)."""

    country_code: CountryCode
    region: RegionCode | None
    metro: Term | None
    locality: Term | None
    postal_code: PostalCode | None
    radius_km: Annotated[float, Field(gt=0, le=500)] | None


class Exclusions(Contract):
    """Filtered out before a candidate is stored, so an excluded name never reaches the list."""

    names: Terms
    domains: Annotated[list[DomainName], Field(max_length=MAX_TERMS)]
    categories: Terms
    postal_codes: Annotated[list[PostalCode], Field(max_length=MAX_TERMS)]


class DiscoveryLimits(Contract):
    """Hard ceilings for one run. The engine stops at the first one reached and says which."""

    max_candidates: Annotated[int, Field(ge=1, le=100_000)]
    max_requests: Annotated[int, Field(ge=1, le=5_000)]
    max_pages_per_source: Annotated[int, Field(ge=1, le=500)]


class SourceSelection(Contract):
    """A connector this campaign uses. `config_json` holds per-campaign settings, never secrets."""

    connector_key: Code
    priority: Annotated[int, Field(ge=0, le=100)]
    max_records: Annotated[int, Field(ge=1, le=100_000)] | None
    config_json: JsonObject


class DiscoveryCriteria(Contract):
    """What a campaign looks for, stored verbatim in `campaigns.criteria_json`."""

    schema_version: Literal[1]
    geography: Annotated[list[GeoScope], Field(max_length=MAX_GEOGRAPHIES)]
    verticals: Annotated[list[NaicsCode], Field(max_length=MAX_TERMS)]
    categories: Terms
    services: Terms
    keywords: Terms
    exclusions: Exclusions
    # affinity signals to look for; never evidence of ownership (that is phase 07)
    market_languages: Annotated[list[LanguageTag], Field(max_length=MAX_TERMS)]
    size_proxies: list[SizeProxy]
    freshness_since: IsoDate | None
    strategy: DiscoveryStrategy
    # a draft may have none, and `check_criteria` is what refuses to run without one or with
    # more than MAX_SOURCES; a bound here would generate a tuple type in TypeScript
    sources: list[SourceSelection]
    limits: DiscoveryLimits


class CriteriaProblem(Contract):
    """One reason a campaign cannot run (error) or deserves a second look (warning)."""

    severity: ProblemSeverity
    code: Code
    path: Annotated[str, Field(max_length=120)]
    message: Message


def empty_criteria() -> DiscoveryCriteria:
    """The starting point of a new campaign: a valid shape with nothing invented in it."""
    return DiscoveryCriteria(
        schema_version=DISCOVERY_CRITERIA_SCHEMA_VERSION,
        geography=[],
        verticals=[],
        categories=[],
        services=[],
        keywords=[],
        exclusions=Exclusions(names=[], domains=[], categories=[], postal_codes=[]),
        market_languages=[],
        size_proxies=[],
        freshness_since=None,
        strategy=DiscoveryStrategy.BROAD,
        sources=[],
        limits=DiscoveryLimits(max_candidates=1000, max_requests=200, max_pages_per_source=50),
    )


# ---- discovery: sources, runs and candidates (IPC) ----------------------------------------------


class Capability(StrEnum):
    """What a source can actually do. The builder shows these; the engine enforces them."""

    PAGINATION = "pagination"
    ESTIMATE = "estimate"
    CHECKPOINT = "checkpoint"
    GEOGRAPHY_REGION = "geography_region"
    GEOGRAPHY_LOCALITY = "geography_locality"
    GEOGRAPHY_POSTAL_CODE = "geography_postal_code"
    GEOGRAPHY_RADIUS = "geography_radius"
    VERTICAL_NAICS = "vertical_naics"
    CATEGORY_TERMS = "category_terms"
    KEYWORD_SEARCH = "keyword_search"
    FRESHNESS = "freshness"
    SIZE_PROXY = "size_proxy"


class DiscoveryErrorCode(StrEnum):
    """Why a page did not arrive. The engine retries the transient ones and only those."""

    RATE_LIMITED = "rate_limited"
    AUTH_REQUIRED = "auth_required"
    BLOCKED = "blocked"
    INVALID_QUERY = "invalid_query"
    SCHEMA_CHANGED = "schema_changed"
    NETWORK_ERROR = "network_error"
    SOURCE_UNAVAILABLE = "source_unavailable"


class ConfigFieldKind(StrEnum):
    TEXT = "text"
    NUMBER = "number"
    SECRET = "secret"  # noqa: S105 - the kind of a field, not a credential
    FILE_TEXT = "file_text"


class SourceHealth(StrEnum):
    """Whether this source could run right now, and why not when it could not."""

    READY = "ready"
    NEEDS_CONFIGURATION = "needs_configuration"
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"


class ImportFormat(StrEnum):
    CSV = "csv"
    TSV = "tsv"
    JSON = "json"
    JSONL = "jsonl"


class RunControlAction(StrEnum):
    PAUSE = "pause"
    RESUME = "resume"
    CANCEL = "cancel"


class ConfigField(Contract):
    """One setting a source needs. Secrets live in the connector configuration, not in criteria."""

    key: Code
    label: Label
    kind: ConfigFieldKind
    required: bool
    help: Annotated[str, Field(max_length=400)]


class SourceMetadata(Contract):
    """Everything the app can say about a source without running it."""

    key: Code
    name: Label
    kind: SourceKind
    version: Label
    capabilities: list[Capability]
    terms_url: str | None
    terms_summary: Annotated[str, Field(min_length=1, max_length=600)]
    requires_credentials: bool
    #: False when the source forbids automated access; the registry keeps those disabled.
    automation_allowed: bool
    #: Conservative defaults; a `source_connector_configs` row overrides them per project.
    default_requests_per_minute: Annotated[int, Field(gt=0)]
    default_daily_request_budget: Annotated[int, Field(gt=0)] | None
    default_page_size: Annotated[int, Field(ge=1, le=MAX_PAGE_SIZE)]
    config_fields: list[ConfigField]


class SourceInfo(Contract):
    """One registered source: what it is, what it may do and whether it could run right now."""

    connector_id: Id
    metadata: SourceMetadata
    enabled: bool
    health: SourceHealth
    health_detail: Annotated[str, Field(max_length=400)] | None
    requests_per_minute: Annotated[int, Field(gt=0)]
    daily_request_budget: Annotated[int, Field(gt=0)] | None
    max_concurrency: Annotated[int, Field(gt=0)]
    last_run_at: UtcTimestamp | None
    last_run_status: SourceRunStatus | None
    last_error_code: Code | None
    last_error_message: Annotated[str, Field(max_length=2000)] | None


class SourceCatalogQuery(Contract):
    project_id: Id | None


class SourceCatalog(Contract):
    sources: list[SourceInfo]
    checked_at: UtcTimestamp


class CampaignListQuery(Contract):
    project_id: Id | None
    limit: PageSize
    cursor: Cursor | None


class CampaignListItem(Contract):
    """A campaign with its criteria and what its runs have produced so far."""

    id: Id
    project_id: Id
    name: Label
    objective: str | None
    criteria: DiscoveryCriteria
    run_count: Annotated[int, Field(ge=0)]
    candidate_count: Annotated[int, Field(ge=0)]
    last_run_at: UtcTimestamp | None
    last_run_status: JobStatus | None
    created_at: UtcTimestamp
    updated_at: UtcTimestamp
    is_synthetic: bool


class CampaignPage(Contract):
    items: list[CampaignListItem]
    next_cursor: Cursor | None
    total: Annotated[int, Field(ge=0)]


class CampaignSaveRequest(Contract):
    """Creates a campaign when `campaign_id` is null, otherwise replaces its editable fields."""

    campaign_id: Id | None
    project_id: Id | None
    name: Label
    objective: str | None
    criteria: DiscoveryCriteria


class CampaignSaved(Contract):
    """The stored campaign plus what validation says about it; problems never block a draft."""

    campaign: CampaignListItem
    problems: list[CriteriaProblem]


class SourceEstimate(Contract):
    """Always an estimate, never a promise. `value` is null when the source cannot count."""

    connector_key: Code
    connector_name: Label
    supported: bool
    value: Annotated[int, Field(ge=0)] | None
    basis: Annotated[str, Field(min_length=1, max_length=300)]
    unsupported_fields: list[Code]


class CriteriaCheckQuery(Contract):
    project_id: Id | None
    criteria: DiscoveryCriteria
    #: Estimates cost a request per source; a builder that only validates asks for none.
    with_estimates: bool


class CriteriaCheck(Contract):
    """Validation of one criteria document. `estimates` are marked estimates, never counts."""

    problems: list[CriteriaProblem]
    estimates: list[SourceEstimate]
    checked_at: UtcTimestamp


class DiscoveryStartRequest(Contract):
    """Starts one discovery job for a campaign.

    `inputs` carry what a run needs but a campaign cannot hold: the text of an imported file, a
    list of manual seeds. They are keyed by connector and stored with the run.
    """

    campaign_id: Id
    #: Empty means every source the campaign selected.
    connector_keys: list[Code]
    inputs: dict[str, JsonObject]


class DiscoveryControlRequest(Contract):
    job_id: Id
    action: RunControlAction


class RunEvent(Contract):
    """One line of a run's log: progress, a limit reached, or a structured error."""

    level: EventLevel
    event: Code
    error_code: Code | None
    retryable: bool | None
    detail_json: JsonObject | None
    created_at: UtcTimestamp


class DiscoverySourceRun(Contract):
    """What one source did inside a run. `cursor` is where a resume would continue."""

    source_run_id: Id
    connector_key: Code
    connector_name: Label
    connector_kind: SourceKind
    status: SourceRunStatus
    pages_done: Annotated[int, Field(ge=0)]
    records_seen: Annotated[int, Field(ge=0)]
    candidate_count: Annotated[int, Field(ge=0)]
    duplicate_count: Annotated[int, Field(ge=0)]
    request_count: Annotated[int, Field(ge=0)]
    cursor: str | None
    error_code: Code | None
    error_message: Annotated[str, Field(max_length=2000)] | None
    started_at: UtcTimestamp | None
    finished_at: UtcTimestamp | None


class DiscoveryRun(Contract):
    """One discovery job: its sources, its totals and the last things it said."""

    job_id: Id
    project_id: Id
    campaign_id: Id
    campaign_name: Label
    status: JobStatus
    progress_done: Annotated[int, Field(ge=0)]
    progress_total: Annotated[int, Field(ge=0)] | None
    candidate_count: Annotated[int, Field(ge=0)]
    duplicate_count: Annotated[int, Field(ge=0)]
    request_count: Annotated[int, Field(ge=0)]
    error_count: Annotated[int, Field(ge=0)]
    requested_at: UtcTimestamp
    started_at: UtcTimestamp | None
    finished_at: UtcTimestamp | None
    sources: list[DiscoverySourceRun]
    events: list[RunEvent]


class DiscoveryRunListQuery(Contract):
    project_id: Id | None
    campaign_id: Id | None
    limit: PageSize
    cursor: Cursor | None


class DiscoveryRunPage(Contract):
    items: list[DiscoveryRun]
    next_cursor: Cursor | None
    total: Annotated[int, Field(ge=0)]


class CandidateListQuery(Contract):
    """Candidates never cross campaigns: a listing is scoped by campaign or by run."""

    project_id: Id | None
    campaign_id: Id | None
    source_run_id: Id | None
    search: Annotated[str, Field(max_length=200)] | None
    include_duplicates: bool
    limit: PageSize
    cursor: Cursor | None


class CandidateListItem(Contract):
    """A candidate reference. It is not a lead and carries no score: phase 04 only finds it."""

    id: Id
    campaign_id: Id | None
    campaign_name: Label | None
    source_run_id: Id
    connector_key: Code
    connector_name: Label
    connector_kind: SourceKind
    name_raw: str
    locality: str | None
    region: str | None
    postal_code: str | None
    domain: str | None
    phone_raw: str | None
    category_raw: str | None
    source_url: str | None
    external_id: str | None
    is_duplicate: bool
    duplicate_of_id: Id | None
    discovered_at: UtcTimestamp


class CandidatePage(Contract):
    items: list[CandidateListItem]
    next_cursor: Cursor | None
    total: Annotated[int, Field(ge=0)]


class CandidateDetailQuery(Contract):
    candidate_id: Id


class CandidateDetail(Contract):
    """The candidate with the whole chain behind it, down to the payload as it was received."""

    candidate: DiscoveryCandidate
    campaign_name: Label | None
    connector: SourceConnector
    source_run: SourceRun
    source_query: SourceQuery | None
    acquired_page: AcquiredPage | None
    raw_record: RawRecord
    duplicate_of: DiscoveryCandidate | None


class ColumnMapping(Contract):
    """Which column of an imported file feeds which candidate field. Unmapped fields stay empty."""

    name: str | None
    locality: str | None
    region: str | None
    postal_code: str | None
    country_code: str | None
    domain: str | None
    website_url: str | None
    phone: str | None
    category: str | None
    external_id: str | None
    source_url: str | None


class ImportPreviewRequest(Contract):
    """Preview of a file the operator picked. The content travels once, inside this request."""

    format: ImportFormat | None
    filename: Annotated[str, Field(min_length=1, max_length=260)]
    content: Annotated[str, Field(max_length=MAX_IMPORT_CHARS)]
    mapping: ColumnMapping | None


class ImportProblem(Contract):
    """A row the import cannot use, named by its line so the operator can fix the file."""

    row: Annotated[int, Field(ge=1)] | None
    code: Code
    message: Message


class ImportPreviewRow(Contract):
    row: Annotated[int, Field(ge=1)]
    values: dict[str, str]
    name_raw: str | None
    problem: Message | None


class ImportPreview(Contract):
    """What the file holds and what the mapping would make of it. Nothing is stored yet."""

    format: ImportFormat
    columns: list[str]
    suggested_mapping: ColumnMapping
    rows: list[ImportPreviewRow]
    total_rows: Annotated[int, Field(ge=0)]
    usable_rows: Annotated[int, Field(ge=0)]
    problems: list[ImportProblem]


DATA_MODELS: tuple[type[Contract], ...] = (
    *RECORDS,
    Provenance,
    ObservedName,
    ObservedLocation,
    ObservedWebsite,
    ObservedContact,
    ObservedClassification,
    ObservedEvidence,
    BusinessListQuery,
    BusinessPage,
    BusinessDetailQuery,
    BusinessDetail,
    DatasetListQuery,
    DatasetPage,
    SyntheticSeedResult,
    DiscoveryCriteria,
    CriteriaProblem,
    SourceMetadata,
    SourceCatalogQuery,
    SourceCatalog,
    CampaignListQuery,
    CampaignPage,
    CampaignSaveRequest,
    CampaignSaved,
    CriteriaCheckQuery,
    CriteriaCheck,
    DiscoveryStartRequest,
    DiscoveryControlRequest,
    DiscoveryRun,
    DiscoveryRunListQuery,
    DiscoveryRunPage,
    CandidateListQuery,
    CandidatePage,
    CandidateDetailQuery,
    CandidateDetail,
    ImportPreviewRequest,
    ImportPreview,
)
