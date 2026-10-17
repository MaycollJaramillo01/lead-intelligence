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
from typing import Annotated, ClassVar, Literal, Self
from uuid import UUID

from pydantic import (
    AfterValidator,
    AwareDatetime,
    Field,
    JsonValue,
    PlainSerializer,
    model_validator,
)

from research_engine.contracts import Contract, GpuStatus, OllamaStatus

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


class WebsiteCategory(StrEnum):
    """What a website rule speaks to; each category has its own subscore."""

    TECHNICAL = "technical"
    SEO = "seo"
    LOCAL_SEO = "local_seo"
    CONTENT = "content"
    CONVERSION = "conversion"
    MOBILE = "mobile"
    ACCESSIBILITY = "accessibility"
    MEASUREMENT = "measurement"
    AEO_GEO_READINESS = "aeo_geo_readiness"


class WebsiteAuditStatus(StrEnum):
    """complete: every page the crawl acquired was analyzed; partial: some were not (the coverage
    says which and why); empty: no page could be analyzed."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    EMPTY = "empty"


class InferenceStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    INVALID_OUTPUT = "invalid_output"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AiTaskType(StrEnum):
    """What a local model may be asked (phase 10). Each task has its own contract, prompt template,
    limits, timeout and failure strategy (`ai.tasks`); none of them decides a fact on its own."""

    EVIDENCE_EXTRACTION = "evidence_extraction"
    BUSINESS_CLASSIFICATION = "business_classification"
    FINDING_SYNTHESIS = "finding_synthesis"
    OPPORTUNITY_REASONING = "opportunity_reasoning"
    LANGUAGE_DETECTION = "language_detection"
    VISUAL_REVIEW = "visual_review"
    #: Phase 12: rewords a deterministic sales brief; may only cite the brief's own claims.
    SALES_BRIEF = "sales_brief"


class AiProfile(StrEnum):
    """A model assignment the operator configures; each task runs on one profile."""

    FAST = "fast"
    BALANCED = "balanced"
    QUALITY = "quality"


class AiDevice(StrEnum):
    """Where the runtime placed the model while it answered, as the runtime reported it."""

    GPU = "gpu"
    PARTIAL = "partial"
    CPU = "cpu"
    UNKNOWN = "unknown"


class AiReviewStatus(StrEnum):
    UNREVIEWED = "unreviewed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


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
    BUSINESS_WEBSITE = "business_website"


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
    MATCH_CANDIDATE = "match_candidate"
    CONTACT_SUPPRESSION = "contact_suppression"
    SAVED_VIEW = "saved_view"
    RESEARCH_CHECKLIST = "research_checklist"
    EXPORT_PRESET = "export_preset"


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


class LeadTier(StrEnum):
    """Where a business lands in one score run (phase 11). `priority`, `review` and `low` are scored
    by the thresholds of the scoring model; `insufficient_data`: a gate could not be decided or too
    much of the formula is unknown; `excluded`: a gate failed, and there is no total."""

    PRIORITY = "priority"
    REVIEW = "review"
    LOW = "low"
    INSUFFICIENT_DATA = "insufficient_data"
    EXCLUDED = "excluded"


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


class CrawlUrlStatus(StrEnum):
    """Where one URL of a site crawl stands. Everything but `queued` is final."""

    QUEUED = "queued"
    FETCHED = "fetched"
    SKIPPED = "skipped"
    FAILED = "failed"


class CrawlTarget(StrEnum):
    PAGE = "page"
    ROBOTS = "robots"
    SITEMAP = "sitemap"


class MatchOutcome(StrEnum):
    """What the resolution rules recommend for a pair of records (phase 06)."""

    AUTO_MERGE = "auto_merge"
    MANUAL_REVIEW = "manual_review"
    DISTINCT = "distinct"


class MatchStatus(StrEnum):
    """Where a pair stands: waiting for a person, put aside, merged, or kept separate."""

    PENDING = "pending"
    DEFERRED = "deferred"
    MERGED = "merged"
    SEPARATE = "separate"


class MatchRisk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CanonicalField(StrEnum):
    NAME = "name"
    LOCATION = "location"
    WEBSITE = "website"
    PHONE = "phone"
    EMAIL = "email"
    CATEGORY = "category"


class SelectionMethod(StrEnum):
    POLICY = "policy"
    MANUAL = "manual"


class SuppressionKind(StrEnum):
    """What a do-not-contact entry covers (phase 09): one address, one number, every address and
    form of a domain, or every contact of a business."""

    EMAIL = "email"
    PHONE = "phone"
    DOMAIN = "domain"
    BUSINESS = "business"


class DecisionMakerReview(StrEnum):
    """unreviewed: extracted with nothing that makes it doubtful; ambiguous: waits in the review
    queue (its reasons say why); confirmed or rejected: a person decided."""

    UNREVIEWED = "unreviewed"
    AMBIGUOUS = "ambiguous"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class DecisionMakerRelationship(StrEnum):
    """The relationship a published role states; `unknown` when the role says none of these."""

    OWNER = "owner"
    FOUNDER = "founder"
    EXECUTIVE = "executive"
    PARTNER = "partner"
    MANAGER = "manager"
    UNKNOWN = "unknown"


class MailDomainResult(StrEnum):
    """What a mail-domain lookup answered. `timeout` and `error` say nothing about the domain."""

    MX = "mx"
    #: No MX record, but the domain has an address: mail goes there (RFC 5321 section 5.1).
    IMPLICIT_MX = "implicit_mx"
    #: The domain publishes that it accepts no mail (RFC 7505).
    NULL_MX = "null_mx"
    NO_MAIL = "no_mail"
    NXDOMAIN = "nxdomain"
    TIMEOUT = "timeout"
    ERROR = "error"


class ResearchStatus(StrEnum):
    """Where an analyst left a lead (phase 12), always with a reason. `sales_ready` is only allowed
    when every criterion of `SalesReadyCriterion` holds; the others are a person's call."""

    SALES_READY = "sales_ready"
    NEEDS_RESEARCH = "needs_research"
    DISQUALIFIED = "disqualified"


class ResearchTaskKind(StrEnum):
    MISSING_DATA = "missing_data"
    CHECKLIST = "checklist"
    REFRESH = "refresh"
    CUSTOM = "custom"


class ResearchTaskStatus(StrEnum):
    OPEN = "open"
    DONE = "done"
    DISMISSED = "dismissed"


class EvidenceRefKind(StrEnum):
    """The stored rows a brief, a pin or a timeline entry can point at. `contact_channel` refs are
    channel keys (phase 09 consolidates contact points into channels); every other ref is an id."""

    BUSINESS = "business"
    EVIDENCE_ITEM = "evidence_item"
    WEBSITE_AUDIT = "website_audit"
    WEBSITE_FINDING = "website_finding"
    WEBSITE_TECHNOLOGY = "website_technology"
    CONTACT_CHANNEL = "contact_channel"
    DECISION_MAKER = "decision_maker"
    LATINO_ASSESSMENT = "latino_assessment"
    INFERENCE_RUN = "inference_run"
    SCORE_RUN = "score_run"


class AttemptResult(StrEnum):
    """What one attempt to work a lead produced, as the analyst recorded it. Nothing is sent."""

    NO_ANSWER = "no_answer"
    LEFT_MESSAGE = "left_message"
    REACHED = "reached"
    REPLIED = "replied"
    BOUNCED = "bounced"
    WRONG_CONTACT = "wrong_contact"
    CALL_BACK_LATER = "call_back_later"
    MEETING_SET = "meeting_set"
    PROPOSAL_SENT = "proposal_sent"
    WON = "won"
    LOST = "lost"


class ContactQuality(StrEnum):
    """Whom the contact route actually reached."""

    DECISION_MAKER = "decision_maker"
    GATEKEEPER = "gatekeeper"
    WRONG_PERSON = "wrong_person"
    UNREACHABLE = "unreachable"
    UNKNOWN = "unknown"


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
        WebsiteCategory,
        WebsiteAuditStatus,
        InferenceStatus,
        AiTaskType,
        AiProfile,
        AiDevice,
        AiReviewStatus,
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
        LeadTier,
        EventLevel,
        ExportFormat,
        CrawlUrlStatus,
        CrawlTarget,
        MatchOutcome,
        MatchStatus,
        MatchRisk,
        CanonicalField,
        SelectionMethod,
        SuppressionKind,
        DecisionMakerReview,
        DecisionMakerRelationship,
        MailDomainResult,
        ResearchStatus,
        ResearchTaskKind,
        ResearchTaskStatus,
        EvidenceRefKind,
        AttemptResult,
        ContactQuality,
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
            "merged": ("active", "candidate", "closed"),  # unmerge: back to what it was before
        },
        MatchStatus: {
            "pending": ("merged", "separate", "deferred"),
            "deferred": ("pending", "merged", "separate"),
            "merged": ("pending", "separate"),
            "separate": ("pending", "merged"),
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
            # an export may fail before it writes (a destination refused, nothing to write)
            "pending": ("running", "failed", "cancelled"),
            "running": ("succeeded", "failed", "cancelled"),
            "failed": ("pending",),
            "succeeded": (),
            "cancelled": (),
        },
        CrawlUrlStatus: {
            "queued": ("fetched", "skipped", "failed"),
            "fetched": (),
            "skipped": (),
            "failed": (),
        },
        ResearchTaskStatus: {
            "open": ("done", "dismissed"),
            "done": ("open",),
            "dismissed": ("open",),
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
    """A named ideal-customer profile (phase 11): which version is active and whether it is
    archived. Its definitions are `icp_profile_versions`."""

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
    active_version_id: Id | None
    archived_at: UtcTimestamp | None


class IcpProfileVersion(Record):
    """Immutable, permanent definition of an ICP profile at one version (`IcpDefinition`)."""

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


class CrawlUrl(Record):
    """One URL of a site crawl: frontier entry while queued, fetch log once final (phase 05).

    The body is an `acquired_page` artifact and the parsed page a `raw_record`; this row says how
    they were obtained (method, timings, redirects, allowed headers) or why the URL was not fetched
    (`reason`). `content_sha256` hashes the normalised visible text, so identical pages under
    different URLs point at the first one through `duplicate_of_id`.
    """

    TABLE = "crawl_urls"
    id: Id
    source_run_id: Id
    candidate_id: Id | None
    discovered_from_id: Id | None
    url: Annotated[str, Field(min_length=1, max_length=2048)]
    target: CrawlTarget
    page_kind: PageKind
    depth: Annotated[int, Field(ge=0)]
    priority: int
    status: CrawlUrlStatus
    reason: Code | None
    method: AcquisitionMethod | None
    final_url: str | None
    http_status: Annotated[int, Field(ge=100, le=599)] | None
    content_type: str | None
    byte_size: Annotated[int, Field(ge=0)] | None
    body_sha256: Sha256 | None
    content_sha256: Sha256 | None
    duplicate_of_id: Id | None
    acquired_page_id: Id | None
    raw_record_id: Id | None
    request_count: Annotated[int, Field(ge=0)]
    retry_count: Annotated[int, Field(ge=0)]
    fetch_ms: Annotated[int, Field(ge=0)] | None
    browser_ms: Annotated[int, Field(ge=0)] | None
    fallback_reason: Code | None
    redirects_json: JsonObject | None
    headers_json: JsonObject | None
    screenshot_path: RelativePath | None
    error_message: str | None
    created_at: UtcTimestamp
    updated_at: UtcTimestamp


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
    #: What the published role states (phase 09); never inferred from the name.
    relationship: DecisionMakerRelationship
    published_context: str | None
    review_status: DecisionMakerReview
    #: `{"codes": [...]}`: why the person waits in the review queue.
    review_reasons_json: JsonObject | None
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
    #: How `validation_status` was reached (phase 09): `structure`, `numbering_plan`, `mail_domain`,
    #: `form_structure`, `sources_agree`, `freshness`, `suppression_list`, `manual_review`...
    validation_method: Code | None
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


class ContactSuppression(Record):
    """One entry of the local do-not-contact list (phase 09). `value` is the normalised address,
    E.164 number, registrable domain or business id. Lifting keeps the entry as history."""

    TABLE = "contact_suppressions"
    ENTITY = EntityType.CONTACT_SUPPRESSION
    id: Id
    kind: SuppressionKind
    value: Annotated[str, Field(min_length=1, max_length=320)]
    business_id: Id | None
    reason: Annotated[str, Field(min_length=1, max_length=2000)]
    #: `manual`, or `import:<file name>`.
    source: Label
    actor: Label
    created_at: UtcTimestamp
    lifted_at: UtcTimestamp | None
    lifted_by: str | None
    lift_reason: str | None


class MailDomainCheck(Record):
    """The cached answer of one mail-domain lookup; asked again once it expires."""

    TABLE = "mail_domain_checks"
    domain: Annotated[str, Field(min_length=3, max_length=253)]
    result: MailDomainResult
    #: `{"exchanges": [...]}`, by preference.
    exchanges_json: JsonObject
    checked_at: UtcTimestamp
    expires_at: UtcTimestamp
    created_at: UtcTimestamp
    updated_at: UtcTimestamp


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
    #: Warnings, what the evidence cannot conclude and, for a review, the calculation it reviewed.
    details_json: JsonObject | None


class LatinoAssessmentEvidence(Record):
    """Evidence an assessment cites, with the factors the rules gave it at that moment."""

    TABLE = "latino_assessment_evidence"
    assessment_id: Id
    evidence_item_id: Id
    role: EvidenceRole
    source_reliability: Confidence
    freshness: Confidence
    score: Confidence
    #: Whether it decided the status: stale, revoked or contradicted evidence is cited, not counted.
    counted: bool
    #: Whether it describes an organization several locations share (a brand's website).
    organizational: bool


class WebsiteAudit(Record):
    """One audit of a crawled site: rules of one version over a snapshot of what one crawl acquired.
    History: a newer crawl or ruleset makes a new audit that points at the previous one."""

    TABLE = "website_audits"
    ENTITY = EntityType.WEBSITE_AUDIT
    id: Id
    project_id: Id
    #: The crawled site as the crawler names it: host and non-default port, without `www.`.
    site: Label
    root_url: str
    #: Where the home page ended up after its redirects.
    final_url: str | None
    source_run_id: Id
    job_id: Id | None
    previous_audit_id: Id | None
    status: WebsiteAuditStatus
    ruleset_version: Label
    extractor_version: Label
    pages_expected: Annotated[int, Field(ge=0)]
    pages_analyzed: Annotated[int, Field(ge=0)]
    #: Share of the rule weight that could be evaluated; unknown results are not evaluated.
    coverage: Confidence
    #: Formula, subscores, coverage and pages with lab measurements (`WebsiteAuditSummary`).
    summary_json: JsonObject
    #: Every fact the rules read (`website.rules.SiteInput`): the audit is reproducible from it.
    snapshot_json: JsonObject
    #: What changed since the previous audit of the same site (`WebsiteDiff`).
    diff_json: JsonObject | None
    crawled_at: UtcTimestamp
    created_at: UtcTimestamp


class WebsiteTechnology(Record):
    TABLE = "website_technologies"
    id: Id
    audit_id: Id
    technology_key: Code
    name: Label
    category: Code
    version: str | None
    confidence: Confidence
    #: Where it was seen: `{"refs": [WebsiteEvidence, ...]}`.
    evidence_json: JsonObject


class ConversionElement(Record):
    TABLE = "conversion_elements"
    id: Id
    audit_id: Id
    element_kind: ConversionElementKind
    label: str | None
    page_url: str
    target: str | None
    confidence: Confidence
    evidence_json: JsonObject


class WebsiteFinding(Record):
    TABLE = "website_findings"
    ENTITY = EntityType.WEBSITE_FINDING
    id: Id
    audit_id: Id
    rule_id: Code
    rule_version: Label
    title: Label
    category: WebsiteCategory
    result: WebsiteFindingResult
    severity: WebsiteFindingSeverity
    observed_value: str | None
    expected_condition: Annotated[str, Field(min_length=1)]
    remediation: str | None
    commercial_relevance: str | None
    confidence: Confidence
    evidence_json: JsonObject
    created_at: UtcTimestamp


class InferenceRun(Record):
    """One local-model task (phase 10): task, runtime, model, digest, parameters, prompt and schema
    versions, the exact input it saw and its hash, what it cost and how it ended. Rebuilt by
    migration 0009; `cache_key` makes an identical request reuse a valid result."""

    TABLE = "ai_inference_runs"
    ENTITY = EntityType.INFERENCE_RUN
    id: Id
    task_type: AiTaskType
    task_version: Label
    project_id: Id | None
    business_id: Id | None
    job_id: Id | None
    provider: Code
    model: Label
    model_digest: str | None
    profile: AiProfile
    #: What was asked of the runtime (temperature, context, seed...) and any documented reduction.
    parameters_json: JsonObject
    prompt_template_id: Code
    prompt_template_version: Label
    output_schema_version: Label
    input_sha256: Sha256
    #: `{"facts": {...}, "refs": [AiInputRef...], "warnings": [...]}`: the minimal evidence sent.
    input_json: JsonObject
    cache_key: Sha256
    status: InferenceStatus
    device: AiDevice
    #: Model calls made: the first answer plus at most one repair.
    attempts: Annotated[int, Field(ge=0)]
    prompt_tokens: Annotated[int, Field(ge=0)] | None
    completion_tokens: Annotated[int, Field(ge=0)] | None
    duration_ms: Annotated[int, Field(ge=0)] | None
    error_code: Code | None
    error_message: Annotated[str, Field(max_length=2000)] | None
    review_status: AiReviewStatus
    started_at: UtcTimestamp | None
    finished_at: UtcTimestamp | None
    created_at: UtcTimestamp
    updated_at: UtcTimestamp


class InferenceOutput(Record):
    """One answer of the model, raw, next to the structure it validated to (or why it did not).
    Immutable, and never a fact by itself."""

    TABLE = "ai_inference_outputs"
    id: Id
    inference_run_id: Id
    attempt: Annotated[int, Field(ge=1)]
    raw_output: str
    validated_output_json: JsonObject | None
    #: `{"errors": [...]}` when this answer was refused.
    validation_errors_json: JsonObject | None
    created_at: UtcTimestamp


class AiProfileSetting(Record):
    """The model a profile runs on and its context length; no model means the profile is off."""

    TABLE = "ai_profile_settings"
    profile: AiProfile
    model: Label | None
    num_ctx: Annotated[int, Field(ge=512, le=32768)]
    #: Whether a model that does not fit in VRAM may run partly or fully on the CPU.
    allow_cpu: bool
    updated_at: UtcTimestamp


class AiTaskSetting(Record):
    TABLE = "ai_task_settings"
    task_type: AiTaskType
    enabled: bool
    profile: AiProfile
    updated_at: UtcTimestamp


class AiResourceLimits(Record):
    """Free memory below which crawling and browser renders wait while local AI works."""

    TABLE = "ai_resource_limits"
    scope: Code
    min_free_ram_mib: Annotated[int, Field(ge=256, le=65536)]
    min_free_vram_mib: Annotated[int, Field(ge=0, le=65536)]
    updated_at: UtcTimestamp


class AiBenchmarkResult(Record):
    """One model on one task over the synthetic labelled corpus, measured on this machine."""

    TABLE = "ai_benchmark_results"
    id: Id
    provider: Code
    model: Label
    model_digest: str | None
    task_type: AiTaskType
    task_version: Label
    corpus_version: Label
    num_ctx: Annotated[int, Field(ge=512, le=32768)]
    cases: Annotated[int, Field(ge=1)]
    valid_outputs: Annotated[int, Field(ge=0)]
    correct: Annotated[int, Field(ge=0)]
    failures: Annotated[int, Field(ge=0)]
    latency_p50_ms: Annotated[int, Field(ge=0)] | None
    latency_p95_ms: Annotated[int, Field(ge=0)] | None
    tokens_per_second: Annotated[float, Field(ge=0)] | None
    peak_vram_used_mib: Annotated[int, Field(ge=0)] | None
    min_free_ram_mib: Annotated[int, Field(ge=0)] | None
    device: AiDevice
    started_at: UtcTimestamp
    finished_at: UtcTimestamp
    created_at: UtcTimestamp


class ScoringModel(Record):
    """A named scoring formula (phase 11): which version is active and whether it is archived."""

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
    active_version_id: Id | None
    archived_at: UtcTimestamp | None


class ScoringModelVersion(Record):
    """Immutable, permanent definition of a scoring model at one version (`ScoringDefinition`).
    `is_heuristic`: its weights are a declared judgement, not fitted to outcomes."""

    TABLE = "scoring_model_versions"
    id: Id
    scoring_model_id: Id
    version: Annotated[int, Field(ge=1)]
    definition_json: JsonObject
    schema_version: Annotated[int, Field(ge=1)]
    is_heuristic: bool
    created_by: Label
    created_at: UtcTimestamp


class ScoreRun(Record):
    """One ICP version and one scoring version applied to a project's businesses, or to the facts
    another run stored (`facts_from_run_id`). Only `finished_at` is written after the insert."""

    TABLE = "score_runs"
    ENTITY = EntityType.SCORE_RUN
    id: Id
    project_id: Id
    job_id: Id | None
    scoring_model_version_id: Id
    icp_profile_version_id: Id
    facts_from_run_id: Id | None
    engine_version: Label
    started_at: UtcTimestamp
    finished_at: UtcTimestamp | None
    created_at: UtcTimestamp


class BusinessScore(Record):
    """Immutable, permanent result for one business in one run: the facts read and the scorecard."""

    TABLE = "business_scores"
    id: Id
    score_run_id: Id
    business_id: Id
    tier: LeadTier
    total_score: Annotated[float, Field(ge=0, le=100)] | None
    confidence: Confidence
    facts_collected_at: UtcTimestamp
    facts_json: JsonObject
    scorecard_json: JsonObject
    created_at: UtcTimestamp


class ComponentScore(Record):
    TABLE = "component_scores"
    id: Id
    business_score_id: Id
    component: Annotated[str, Field(pattern=r"^[A-Z][A-Z_]*$", max_length=64)]
    value: Annotated[float, Field(ge=0, le=100)] | None
    weight: Annotated[float, Field(ge=0)]
    contribution: float | None
    coverage: Confidence


class LeadOutcome(Record):
    """Immutable, permanent record of what happened when a lead was worked (phase 11): the feedback
    loop that calibration reads. A `lost` outcome says why."""

    TABLE = "lead_outcomes"
    id: Id
    business_id: Id
    score_run_id: Id | None
    stage: LeadStage
    previous_stage: LeadStage
    lost_reason: Annotated[str, Field(min_length=1, max_length=500)] | None
    reason: Annotated[str, Field(min_length=1, max_length=2000)]
    actor: Label
    occurred_at: UtcTimestamp
    created_at: UtcTimestamp
    #: Phase 12: how the lead was worked. An attempt keeps the stage (`stage == previous_stage`).
    channel: ContactType | None
    attempt_result: AttemptResult | None
    value_amount: Annotated[float, Field(ge=0, le=1_000_000_000)] | None
    value_currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] | None
    contact_quality: ContactQuality | None
    notes: Annotated[str, Field(min_length=1, max_length=4000)] | None


class LeadIndexRow(Record):
    """One business in one score run, projected from its stored score for the Lead Explorer
    (phase 12). Written with the score, rebuilt from `business_scores` when missing; `rank` is the
    business's place in the run once it finished. A projection: the scorecard stays the record."""

    TABLE = "lead_index"
    score_run_id: Id
    business_id: Id
    project_id: Id
    rank: Annotated[int, Field(ge=1)] | None
    tier: LeadTier
    tier_order: Annotated[int, Field(ge=0, le=4)]
    total_score: Annotated[float, Field(ge=0, le=100)] | None
    confidence: Confidence
    #: The share of the final formula's weight that was known.
    data_coverage: Confidence
    icp_match: Annotated[float, Field(ge=0, le=100)] | None
    opportunity_score: Annotated[float, Field(ge=0, le=100)] | None
    digital_gap: Annotated[float, Field(ge=0, le=100)] | None
    business_health: Annotated[float, Field(ge=0, le=100)] | None
    buying_capacity: Annotated[float, Field(ge=0, le=100)] | None
    contactability: Annotated[float, Field(ge=0, le=100)] | None
    commercial_intent: Annotated[float, Field(ge=0, le=100)] | None
    data_confidence: Annotated[float, Field(ge=0, le=100)] | None
    display_name: Label
    name_sort_key: str
    locality: str | None
    region: str | None
    primary_category: str | None
    primary_domain: str | None
    latino_status: LatinoStatus
    latino_confidence: Confidence | None
    main_channel: ContactType | None
    channel_status: ValidationStatus | None
    usable_channels: Annotated[int, Field(ge=0)]
    problem_mask: Annotated[int, Field(ge=0, le=255)]
    top_problem: Code | None
    top_reason: Annotated[str, Field(min_length=1, max_length=300)] | None
    offer_key: Code | None
    facts_collected_at: UtcTimestamp
    audit_status: WebsiteAuditStatus | None
    conflicts: Annotated[int, Field(ge=0)]
    suppressed_when_scored: bool
    is_synthetic: bool


class LeadResearch(Record):
    """The research status of a canonical business and whom it is assigned to (phase 12). Current
    state only: every change is also a `review_decisions` row, which is its history."""

    TABLE = "lead_research"
    business_id: Id
    project_id: Id
    status: ResearchStatus | None
    status_reason: Annotated[str, Field(min_length=1, max_length=2000)] | None
    status_by: str | None
    status_at: UtcTimestamp | None
    assignee: Annotated[str, Field(min_length=1, max_length=120)] | None
    assigned_at: UtcTimestamp | None
    updated_at: UtcTimestamp


class SavedView(Record):
    """A named Lead Explorer view (phase 12); its definitions are `saved_view_versions`."""

    TABLE = "saved_views"
    id: Id
    scope: Code
    name: Annotated[str, Field(min_length=1, max_length=120)]
    description: Annotated[str, Field(max_length=2000)] | None
    created_at: UtcTimestamp
    updated_at: UtcTimestamp
    archived_at: UtcTimestamp | None


class SavedViewVersion(Record):
    """Immutable, permanent definition of a view at one version. `schema_version` says which shape
    `definition_json` has; older shapes are upgraded when read, never rewritten."""

    TABLE = "saved_view_versions"
    id: Id
    view_id: Id
    version: Annotated[int, Field(ge=1)]
    definition_json: JsonObject
    schema_version: Annotated[int, Field(ge=1)]
    created_by: Label
    created_at: UtcTimestamp


class ResearchChecklist(Record):
    """What an analyst verifies before a lead is sales-ready, for a vertical or a campaign."""

    TABLE = "research_checklists"
    id: Id
    name: Annotated[str, Field(min_length=1, max_length=120)]
    description: Annotated[str, Field(max_length=2000)] | None
    active_version_id: Id | None
    archived_at: UtcTimestamp | None
    created_at: UtcTimestamp
    updated_at: UtcTimestamp


class ResearchChecklistVersion(Record):
    TABLE = "research_checklist_versions"
    id: Id
    checklist_id: Id
    version: Annotated[int, Field(ge=1)]
    definition_json: JsonObject
    schema_version: Annotated[int, Field(ge=1)]
    created_by: Label
    created_at: UtcTimestamp


class ResearchCheck(Record):
    """Immutable, permanent: a person ticked or unticked one manual checklist item."""

    TABLE = "research_checks"
    id: Id
    business_id: Id
    checklist_version_id: Id
    item_key: Code
    done: bool
    note: Annotated[str, Field(min_length=1, max_length=1000)] | None
    actor: Label
    decided_at: UtcTimestamp


class ResearchTask(Record):
    """Work left on a lead. Closed with a reason when dismissed; reopened, never deleted."""

    TABLE = "research_tasks"
    id: Id
    business_id: Id
    project_id: Id
    kind: ResearchTaskKind
    key: Annotated[str, Field(min_length=1, max_length=160)]
    title: Label
    detail_json: JsonObject | None
    status: ResearchTaskStatus
    created_by: Label
    created_at: UtcTimestamp
    updated_at: UtcTimestamp
    closed_by: str | None
    closed_at: UtcTimestamp | None
    close_reason: Annotated[str, Field(min_length=1, max_length=2000)] | None


class EvidencePin(Record):
    """Evidence an analyst marked as important for a lead. Unpinning keeps the row, with a
    reason."""

    TABLE = "evidence_pins"
    id: Id
    business_id: Id
    ref_kind: EvidenceRefKind
    ref: Annotated[str, Field(min_length=1, max_length=400)]
    label: Label
    note: Annotated[str, Field(min_length=1, max_length=1000)] | None
    pinned_by: Label
    pinned_at: UtcTimestamp
    unpinned_by: str | None
    unpinned_at: UtcTimestamp | None
    unpin_reason: Annotated[str, Field(min_length=1, max_length=2000)] | None


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
    #: Phase 13: what was asked for (project, score run), the moment the rows were read, the
    #: versions of what produced them and the quality report. Schema 1 snapshots have none of it.
    query_json: JsonObject
    cutoff_at: UtcTimestamp | None
    pipeline_versions_json: dict[str, str]
    quality_json: JsonObject | None


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
    #: Phase 13: the job that writes it, where the save dialog put it (history only), the columns
    #: as written, what was exported and how, who asked, the preset used and why it failed.
    job_id: Id | None
    destination: str | None
    columns_json: list[dict[str, str]]
    config_json: JsonObject
    actor: Label
    preset_key: Code | None
    preset_version: Annotated[int, Field(ge=1)] | None
    error_code: Code | None


class ExportPreset(Record):
    """One immutable version of an export preset (built in, or cloned and saved by a person)."""

    TABLE = "export_presets"
    ENTITY = EntityType.EXPORT_PRESET
    id: Id
    key: Code
    version: Annotated[int, Field(ge=1)]
    name: Annotated[str, Field(min_length=1, max_length=120)]
    description: Annotated[str, Field(max_length=2000)] | None
    definition_json: JsonObject
    built_in: bool
    cloned_from_id: Id | None
    created_by: Label
    created_at: UtcTimestamp
    superseded_at: UtcTimestamp | None


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


class EntityRecord(Record):
    """The record business a candidate became (phase 06). One per candidate, for ever."""

    TABLE = "entity_records"
    candidate_id: Id
    business_id: Id
    project_id: Id
    created_at: UtcTimestamp


class MatchCandidate(Record):
    """A pair of records the resolution rules looked at: the evidence, the outcome and its status.

    `features_json` holds the compared values and blocking keys, `reasons_json` every weight and
    rule that produced the score. The pair never changes; evidence is refreshed by a new run, and
    `status` follows the person who reviewed it (`is_manual`) or else the engine.
    """

    TABLE = "match_candidates"
    ENTITY = EntityType.MATCH_CANDIDATE
    id: Id
    project_id: Id
    left_business_id: Id
    right_business_id: Id
    rules_version: Label
    score: Confidence
    outcome: MatchOutcome
    risk: MatchRisk
    hard_conflict: bool
    status: MatchStatus
    is_manual: bool
    features_json: JsonObject
    reasons_json: JsonObject
    job_id: Id | None
    decided_by: Label
    created_at: UtcTimestamp
    updated_at: UtcTimestamp


class BusinessMerge(Record):
    """One record joining a cluster: why (a snapshot of the pair's explanation), by whom, and when
    it was undone. History: only the unmerge fields are ever written after the insert."""

    TABLE = "business_merges"
    id: Id
    project_id: Id
    business_id: Id
    into_business_id: Id
    match_id: Id | None
    is_manual: bool
    score: Confidence | None
    rules_version: str | None
    explanation_json: JsonObject
    actor: Label
    merged_at: UtcTimestamp
    unmerged_at: UtcTimestamp | None
    unmerged_by: str | None
    unmerge_reason: str | None


class CanonicalValue(Record):
    """Which observation represents a cluster for one field, chosen by policy or by a person.

    `alternative_count` is how many different values the cluster holds for that field: above one,
    the business has a conflict the interface must show. Superseded, never overwritten.
    """

    TABLE = "canonical_values"
    id: Id
    business_id: Id
    field: CanonicalField
    observation_id: Id
    value: str
    method: SelectionMethod
    policy_version: Label
    alternative_count: Annotated[int, Field(ge=1)]
    reason: Annotated[str, Field(min_length=1)]
    actor: Label
    chosen_at: UtcTimestamp
    superseded_at: UtcTimestamp | None


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
    CrawlUrl,
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
    InferenceOutput,
    ScoringModel,
    ScoringModelVersion,
    ScoreRun,
    BusinessScore,
    ComponentScore,
    LeadOutcome,
    LeadIndexRow,
    LeadResearch,
    SavedView,
    SavedViewVersion,
    ResearchChecklist,
    ResearchChecklistVersion,
    ResearchCheck,
    ResearchTask,
    EvidencePin,
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
    ExportPreset,
    Tag,
    EntityTag,
    Note,
    ReviewDecision,
    AuditEntry,
    EntityRecord,
    MatchCandidate,
    BusinessMerge,
    CanonicalValue,
    ContactSuppression,
    MailDomainCheck,
    AiProfileSetting,
    AiTaskSetting,
    AiResourceLimits,
    AiBenchmarkResult,
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


class LatinoReviewFilter(StrEnum):
    """Businesses by the state of a person's review of their Latino evidence (phase 07)."""

    REVIEWED = "reviewed"
    UNREVIEWED = "unreviewed"
    #: Reviewed, but the automatic calculation has changed since the review.
    OUTDATED = "outdated"


PageSize = Annotated[int, Field(ge=1, le=MAX_PAGE_SIZE)]
# the cursor carries the last sort key: a 300-character name in UTF-8 fits comfortably
Cursor = Annotated[str, Field(min_length=1, max_length=2048, pattern=r"^[A-Za-z0-9_-]+$")]


class BusinessListQuery(Contract):
    project_id: Id | None
    search: Annotated[str, Field(max_length=200)] | None
    status: BusinessStatus | None
    #: Phase 07: the Latino evidence status a business shows, its minimum confidence, a kind of
    #: source whose evidence that assessment counted, and the state of a person's review.
    latino_status: LatinoStatus | None
    latino_min_confidence: Confidence | None
    latino_source_kind: SourceKind | None
    latino_review: LatinoReviewFilter | None
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
    #: Records resolved into this business, itself included.
    member_count: Annotated[int, Field(ge=1)]
    #: Fields whose records disagree (different phones, addresses...): shown, never hidden.
    conflict_count: Annotated[int, Field(ge=0)]
    #: What the business shows (phase 07): a person's review, else the rules; `unknown` and a null
    #: confidence when it was never evaluated.
    latino_status: LatinoStatus
    latino_confidence: Confidence | None
    latino_reviewed: bool
    last_seen_at: UtcTimestamp
    is_synthetic: bool


class BusinessPage(Contract):
    """One keyset page. `total` counts every match of the filters, not only this page."""

    items: list[BusinessListItem]
    next_cursor: Cursor | None
    total: Annotated[int, Field(ge=0)]


class BusinessDetailQuery(Contract):
    business_id: Id


class ClusterMember(Contract):
    """A record resolved into a business, with the source it came from."""

    business_id: Id
    display_name: str
    status: BusinessStatus
    connector_name: Label | None
    connector_kind: SourceKind | None
    source_url: str | None
    external_id: str | None
    first_seen_at: UtcTimestamp


class BusinessDetail(Contract):
    """A business with every observation of every record resolved into it (phase 06), the value
    chosen for each field and the merge history. A record that is itself merged shows only its own
    observations; `business.merged_into_id` says where it belongs."""

    business: Business
    is_synthetic: bool
    names: list[ObservedName]
    locations: list[ObservedLocation]
    websites: list[ObservedWebsite]
    contacts: list[ObservedContact]
    classifications: list[ObservedClassification]
    evidence: list[ObservedEvidence]
    members: list[ClusterMember]
    canonical: list[CanonicalValue]
    merges: list[BusinessMerge]


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


# ---- web acquisition (phase 05) -----------------------------------------------------------------
# A crawl turns candidates and URLs into acquired pages and parsed content. It never decides whether
# a business is a good lead: that is for later phases, reading what this one stored.

MAX_CRAWL_SEEDS = 200
MAX_PATH_RULES = 25
#: The most one crawl may take: every site of the largest campaign, 20 requests each (D-103).
MAX_CRAWL_SITES = 2_000
MAX_CRAWL_REQUESTS = 40_000
PathRule = Annotated[str, Field(min_length=1, max_length=200, pattern=r"^/")]
SeedUrl = Annotated[str, Field(min_length=1, max_length=2048)]


class BrowserMode(StrEnum):
    """When a page may be rendered in Chromium: never, on a verifiable criterion, or always."""

    NEVER = "never"
    AUTO = "auto"
    ALWAYS = "always"


class CrawlSettings(Contract):
    """Budget and policy of one crawl. The bounds are the safe limits in docs/crawling.md."""

    max_sites: Annotated[int, Field(ge=1, le=MAX_CRAWL_SITES)]
    max_pages_per_site: Annotated[int, Field(ge=1, le=100)]
    max_depth: Annotated[int, Field(ge=0, le=4)]
    max_bytes_per_site: Annotated[int, Field(ge=65_536, le=100_000_000)]
    max_seconds_per_site: Annotated[int, Field(ge=10, le=1_800)]
    max_requests: Annotated[int, Field(ge=1, le=MAX_CRAWL_REQUESTS)]
    requests_per_minute_per_host: Annotated[int, Field(ge=1, le=120)]
    per_host_concurrency: Annotated[int, Field(ge=1, le=4)]
    include_subdomains: bool
    #: Path globs (`/blog/*`). Empty includes everything; the home page and robots.txt always go.
    include_paths: Annotated[list[PathRule], Field(max_length=MAX_PATH_RULES)]
    exclude_paths: Annotated[list[PathRule], Field(max_length=MAX_PATH_RULES)]
    browser: BrowserMode
    #: Only when a later module asks for it: screenshots cost time and disk.
    screenshots: bool


def default_crawl_settings() -> CrawlSettings:
    """Gentle defaults for a business site: the pages that matter, not the whole site."""
    return CrawlSettings(
        max_sites=100,
        max_pages_per_site=12,
        max_depth=2,
        max_bytes_per_site=25_000_000,
        max_seconds_per_site=180,
        max_requests=2_000,
        requests_per_minute_per_host=30,
        per_host_concurrency=2,
        include_subdomains=False,
        include_paths=[],
        exclude_paths=[],
        browser=BrowserMode.AUTO,
        screenshots=False,
    )


def campaign_crawl_settings() -> CrawlSettings:
    """Every site a campaign found, for a pipeline: the Latino evidence, the audit and most
    reasons to call exist only for the sites that were read (D-103)."""
    return default_crawl_settings().model_copy(
        update={"max_sites": MAX_CRAWL_SITES, "max_requests": MAX_CRAWL_REQUESTS}
    )


class CrawlStartRequest(Contract):
    """Crawls the websites of a campaign's candidates, the URLs given, or both."""

    project_id: Id | None
    campaign_id: Id | None
    urls: Annotated[list[SeedUrl], Field(max_length=MAX_CRAWL_SEEDS)]
    settings: CrawlSettings


class CrawlErrorGroup(Contract):
    """Pages that failed for the same reason. robots.txt and sitemaps report through their site."""

    reason: Code
    count: Annotated[int, Field(ge=1)]
    example_url: str


class CrawlMetrics(Contract):
    """Measured from the fetch log, never estimated. Latencies cover fetches a server answered."""

    requests: Annotated[int, Field(ge=0)]
    pages_fetched: Annotated[int, Field(ge=0)]
    pages_failed: Annotated[int, Field(ge=0)]
    urls_skipped: Annotated[int, Field(ge=0)]
    queued: Annotated[int, Field(ge=0)]
    bytes: Annotated[int, Field(ge=0)]
    cache_hits: Annotated[int, Field(ge=0)]
    retries: Annotated[int, Field(ge=0)]
    blocked: Annotated[int, Field(ge=0)]
    http_pages: Annotated[int, Field(ge=0)]
    browser_pages: Annotated[int, Field(ge=0)]
    browser_fallbacks: Annotated[int, Field(ge=0)]
    duplicates: Annotated[int, Field(ge=0)]
    pages_per_minute: Annotated[float, Field(ge=0)] | None
    latency_p50_ms: Annotated[int, Field(ge=0)] | None
    latency_p95_ms: Annotated[int, Field(ge=0)] | None
    latency_max_ms: Annotated[int, Field(ge=0)] | None


class CrawlSite(Contract):
    """One site of a crawl: its own source run, budget spent and outcome."""

    source_run_id: Id
    site: Label
    root_url: str
    candidate_id: Id | None
    status: SourceRunStatus
    pages_fetched: Annotated[int, Field(ge=0)]
    pages_failed: Annotated[int, Field(ge=0)]
    urls_skipped: Annotated[int, Field(ge=0)]
    queued: Annotated[int, Field(ge=0)]
    bytes: Annotated[int, Field(ge=0)]
    requests: Annotated[int, Field(ge=0)]
    browser_pages: Annotated[int, Field(ge=0)]
    error_code: Code | None
    error_message: Annotated[str, Field(max_length=2000)] | None
    started_at: UtcTimestamp | None
    finished_at: UtcTimestamp | None


class CrawlRun(Contract):
    """One crawl job. `in_flight`: URLs being fetched right now, when this worker runs the job."""

    job_id: Id
    project_id: Id
    campaign_id: Id | None
    campaign_name: Label | None
    status: JobStatus
    settings: CrawlSettings
    site_count: Annotated[int, Field(ge=0)]
    sites_done: Annotated[int, Field(ge=0)]
    metrics: CrawlMetrics
    errors: list[CrawlErrorGroup]
    in_flight: list[str]
    sites: list[CrawlSite]
    events: list[RunEvent]
    requested_at: UtcTimestamp
    started_at: UtcTimestamp | None
    finished_at: UtcTimestamp | None


class CrawlRunListQuery(Contract):
    project_id: Id | None
    campaign_id: Id | None
    limit: PageSize
    cursor: Cursor | None


class CrawlRunPage(Contract):
    items: list[CrawlRun]
    next_cursor: Cursor | None
    total: Annotated[int, Field(ge=0)]


class CrawlUrlListQuery(Contract):
    """URLs of one crawl, in the order they were discovered."""

    job_id: Id
    source_run_id: Id | None
    status: CrawlUrlStatus | None
    limit: PageSize
    cursor: Cursor | None


class CrawlUrlItem(Contract):
    id: Id
    source_run_id: Id
    site: Label
    url: str
    target: CrawlTarget
    page_kind: PageKind
    depth: Annotated[int, Field(ge=0)]
    status: CrawlUrlStatus
    reason: Code | None
    method: AcquisitionMethod | None
    http_status: Annotated[int, Field(ge=100, le=599)] | None
    byte_size: Annotated[int, Field(ge=0)] | None
    fetch_ms: Annotated[int, Field(ge=0)] | None
    browser_ms: Annotated[int, Field(ge=0)] | None
    fallback_reason: Code | None
    duplicate_of_id: Id | None
    title: str | None
    updated_at: UtcTimestamp


class CrawlUrlPage(Contract):
    items: list[CrawlUrlItem]
    next_cursor: Cursor | None
    total: Annotated[int, Field(ge=0)]


class CrawlUrlDetailQuery(Contract):
    crawl_url_id: Id


class CrawlUrlDetail(Contract):
    """A URL with how it was obtained, the stored bytes and the parsed page, down to the hashes."""

    crawl_url: CrawlUrl
    job_id: Id
    site: Label
    source_run: SourceRun
    acquired_page: AcquiredPage | None
    raw_record: RawRecord | None
    discovered_from: CrawlUrl | None
    duplicate_of: CrawlUrl | None


# ---- entity resolution (phase 06) ---------------------------------------------------------------
# Normalised observations resolved into canonical businesses. The engine recommends; people decide
# what is ambiguous, and every decision can be explained and undone.


class ReasonDirection(StrEnum):
    FOR = "for"
    AGAINST = "against"
    RULE = "rule"


class FieldAgreement(StrEnum):
    MATCH = "match"
    PARTIAL = "partial"
    CONFLICT = "conflict"
    MISSING = "missing"


class MatchAction(StrEnum):
    MERGE = "merge"
    KEEP_SEPARATE = "keep_separate"
    DEFER = "defer"
    #: Reverts the latest decision on the pair, whatever it was.
    UNDO = "undo"


ReviewReason = Annotated[str, Field(min_length=1, max_length=2000)]


class ResolutionStartRequest(Contract):
    """Resolves one project, or every live project when `project_id` is null (one job each)."""

    project_id: Id | None


class ResolutionStats(Contract):
    """Counts of one resolution run, read back from its last checkpoint."""

    records: Annotated[int, Field(ge=0)]
    ingested: Annotated[int, Field(ge=0)]
    observations: Annotated[int, Field(ge=0)]
    blocks: Annotated[int, Field(ge=0)]
    skipped_blocks: Annotated[int, Field(ge=0)]
    largest_block: Annotated[int, Field(ge=0)]
    pairs_compared: Annotated[int, Field(ge=0)]
    auto_merged: Annotated[int, Field(ge=0)]
    review: Annotated[int, Field(ge=0)]
    distinct: Annotated[int, Field(ge=0)]
    clusters: Annotated[int, Field(ge=0)]
    merged_records: Annotated[int, Field(ge=0)]
    manual_decisions: Annotated[int, Field(ge=0)]


class ResolutionRun(Contract):
    job_id: Id
    project_id: Id
    project_name: Label
    status: JobStatus
    stage: Code
    progress_done: Annotated[int, Field(ge=0)]
    progress_total: Annotated[int, Field(ge=0)] | None
    normalizer_version: Label
    rules_version: Label
    stats: ResolutionStats
    requested_at: UtcTimestamp
    started_at: UtcTimestamp | None
    finished_at: UtcTimestamp | None
    events: list[RunEvent]
    is_synthetic: bool


class ResolutionRunListQuery(Contract):
    project_id: Id | None
    limit: PageSize
    cursor: Cursor | None


class ResolutionRunPage(Contract):
    items: list[ResolutionRun]
    next_cursor: Cursor | None
    total: Annotated[int, Field(ge=0)]


class MatchListQuery(Contract):
    """The review queue: pairs by score, filtered by status, risk, minimum score and source."""

    project_id: Id | None
    status: MatchStatus | None
    risk: MatchRisk | None
    min_score: Confidence | None
    connector_key: Code | None
    limit: PageSize
    cursor: Cursor | None


class MatchSide(Contract):
    business_id: Id
    #: The business this record currently belongs to (itself when it is not merged).
    root_business_id: Id
    display_name: str
    locality: str | None
    region: str | None
    connector_names: list[Label]


class MatchListItem(Contract):
    id: Id
    project_id: Id
    left: MatchSide
    right: MatchSide
    score: Confidence
    outcome: MatchOutcome
    risk: MatchRisk
    status: MatchStatus
    is_manual: bool
    #: Reason codes, rules first, then agreements, then conflicts.
    reasons: list[Code]
    rules_version: Label
    updated_at: UtcTimestamp
    is_synthetic: bool


class MatchPage(Contract):
    items: list[MatchListItem]
    next_cursor: Cursor | None
    total: Annotated[int, Field(ge=0)]


class MatchDetailQuery(Contract):
    match_id: Id


class FieldComparison(Contract):
    field: Code
    left: list[str]
    right: list[str]
    agreement: FieldAgreement
    similarity: Confidence | None


class MatchReason(Contract):
    code: Code
    direction: ReasonDirection
    weight: float
    detail: str | None


class MatchDetail(Contract):
    """One pair side by side: both records with their evidence, the comparison field by field, the
    reasons behind the score, and every decision and merge that touched it."""

    match: MatchCandidate
    left: BusinessDetail
    right: BusinessDetail
    fields: list[FieldComparison]
    reasons: list[MatchReason]
    blocking_keys: list[str]
    decisions: list[ReviewDecision]
    merges: list[BusinessMerge]


class MatchActionRequest(Contract):
    match_id: Id
    action: MatchAction
    reason: ReviewReason | None


class CanonicalChoiceRequest(Contract):
    """Makes one observation the value a business shows for a field."""

    business_id: Id
    field: CanonicalField
    observation_id: Id
    reason: ReviewReason | None


# ---- Latino evidence (phase 07) -----------------------------------------------------------------


class LatinoTrack(StrEnum):
    """What a piece of evidence can speak to. Market affinity never speaks to ownership."""

    OWNERSHIP = "ownership"
    MARKET = "market"
    CONTRADICTION = "contradiction"


class EvidenceFreshness(StrEnum):
    FRESH = "fresh"
    AGING = "aging"
    STALE = "stale"


class LatinoStartRequest(Contract):
    """Evaluates one project, or every live project when `project_id` is null (one job each)."""

    project_id: Id | None


class LatinoStats(Contract):
    """Counts of one evaluation run, read back from its last checkpoint."""

    sources_scanned: Annotated[int, Field(ge=0)]
    pages_scanned: Annotated[int, Field(ge=0)]
    evidence_found: Annotated[int, Field(ge=0)]
    businesses: Annotated[int, Field(ge=0)]
    changed: Annotated[int, Field(ge=0)]
    ownership_evidence: Annotated[int, Field(ge=0)]
    market_evidence: Annotated[int, Field(ge=0)]
    contradictions: Annotated[int, Field(ge=0)]
    stale: Annotated[int, Field(ge=0)]
    revoked: Annotated[int, Field(ge=0)]
    verified: Annotated[int, Field(ge=0)]
    high_confidence: Annotated[int, Field(ge=0)]
    probable: Annotated[int, Field(ge=0)]
    affinity: Annotated[int, Field(ge=0)]
    unknown: Annotated[int, Field(ge=0)]
    reviewed: Annotated[int, Field(ge=0)]


class LatinoRun(Contract):
    job_id: Id
    project_id: Id
    project_name: Label
    status: JobStatus
    stage: Code
    progress_done: Annotated[int, Field(ge=0)]
    progress_total: Annotated[int, Field(ge=0)] | None
    extractor_version: Label
    rules_version: Label
    stats: LatinoStats
    requested_at: UtcTimestamp
    started_at: UtcTimestamp | None
    finished_at: UtcTimestamp | None
    events: list[RunEvent]
    is_synthetic: bool


class LatinoRunListQuery(Contract):
    project_id: Id | None
    limit: PageSize
    cursor: Cursor | None


class LatinoRunPage(Contract):
    items: list[LatinoRun]
    next_cursor: Cursor | None
    total: Annotated[int, Field(ge=0)]


class LatinoDetailQuery(Contract):
    business_id: Id


class LatinoNote(Contract):
    code: Code
    detail: str | None


class LatinoAssessmentView(Contract):
    """One assessment with its warnings and what it cannot conclude (from `details_json`)."""

    assessment: LatinoAssessment
    warnings: list[LatinoNote]
    cannot_conclude: list[Code]


class LatinoEvidence(Contract):
    """One piece of evidence with the factors the rules give it now."""

    evidence: ObservedEvidence
    claim: Code
    track: LatinoTrack
    #: Its role in the assessment the business shows; null when that assessment does not cite it.
    role: EvidenceRole | None
    counted: bool
    source_reliability: Confidence
    freshness: Confidence
    freshness_label: EvidenceFreshness
    organizational: bool


class LatinoDetail(Contract):
    """The Latino evidence of a canonical business: what it shows (a person's review, else the
    rules), the automatic calculation, every piece of evidence of its records ordered by track,
    strength and freshness, and the history of assessments and review decisions."""

    business_id: Id
    #: The canonical business the assessments belong to (a merged record shows its business's).
    root_business_id: Id
    effective: LatinoAssessmentView | None
    automatic: LatinoAssessmentView | None
    #: A person reviewed an automatic calculation that has changed since.
    review_outdated: bool
    evidence: list[LatinoEvidence]
    history: list[LatinoAssessmentView]
    decisions: list[ReviewDecision]
    rules_version: Label
    extractor_version: Label


class LatinoReviewRequest(Contract):
    """A person's assessment with its mandatory reason. A null status withdraws the review, so the
    business shows the automatic calculation again."""

    business_id: Id
    status: LatinoStatus | None
    reason: ReviewReason


# ---- website intelligence (phase 08) ------------------------------------------------------------
# Deterministic audits of crawled sites: versioned rules over a stored snapshot, findings with their
# evidence, subscores that never count what could not be evaluated, and the difference between runs.

MAX_AUDIT_SITES = 200
NonNegative = Annotated[int, Field(ge=0)]
Score = Annotated[int, Field(ge=0, le=100)]


class WebsiteStartRequest(Contract):
    """Audits the crawled sites of one project, or of every live project when `project_id` is null
    (one job each). `sites` narrows one project's audit to some of its sites, to retry them."""

    project_id: Id | None
    sites: Annotated[list[Label], Field(max_length=MAX_AUDIT_SITES)]


class WebsiteStats(Contract):
    """Counts of one audit run, read back from its last checkpoint."""

    sites: NonNegative
    audited: NonNegative
    unchanged: NonNegative
    partial: NonNegative
    empty: NonNegative
    pages_analyzed: NonNegative
    pages_missing: NonNegative
    failed: NonNegative
    warnings: NonNegative
    unknown: NonNegative


class WebsiteRun(Contract):
    job_id: Id
    project_id: Id
    project_name: Label
    status: JobStatus
    stage: Code
    progress_done: NonNegative
    progress_total: NonNegative | None
    extractor_version: Label
    ruleset_version: Label
    stats: WebsiteStats
    requested_at: UtcTimestamp
    started_at: UtcTimestamp | None
    finished_at: UtcTimestamp | None
    events: list[RunEvent]
    is_synthetic: bool


class WebsiteRunListQuery(Contract):
    project_id: Id | None
    limit: PageSize
    cursor: Cursor | None


class WebsiteRunPage(Contract):
    items: list[WebsiteRun]
    next_cursor: Cursor | None
    total: NonNegative


class WebsiteEvidence(Contract):
    """Where something was observed: the crawled URL, the field read and the fragment."""

    url: str | None
    crawl_url_id: Id | None
    field: Code
    snippet: Annotated[str, Field(max_length=500)] | None


class WebsiteSubscore(Contract):
    """`score` is null when no rule of the category could be evaluated."""

    category: WebsiteCategory
    score: Score | None
    coverage: Confidence
    confidence: Confidence
    passed: NonNegative
    warnings: NonNegative
    failed: NonNegative
    unknown: NonNegative
    not_applicable: NonNegative


class WebsitePageView(Contract):
    """A page the crawl acquired or tried, whether it could be analyzed (and why not), with the
    crawler's lab measurements: one sample, no device or network emulation, never field data."""

    crawl_url_id: Id
    url: str
    final_url: str | None
    page_kind: PageKind
    depth: NonNegative
    status: CrawlUrlStatus
    http_status: Annotated[int, Field(ge=100, le=599)] | None
    method: AcquisitionMethod | None
    analyzed: bool
    gap: Code | None
    title: str | None
    words: NonNegative | None
    redirects: NonNegative
    response_ms: NonNegative | None
    render_ms: NonNegative | None
    html_bytes: NonNegative | None
    scripts: NonNegative | None
    images: NonNegative | None
    device_profile: Code
    measured_at: UtcTimestamp


class WebsiteCoverage(Contract):
    pages_expected: NonNegative
    pages_analyzed: NonNegative
    pages_failed: NonNegative
    #: Why expected pages could not be analyzed.
    gaps: list[Code]
    home_analyzed: bool
    crawl_status: SourceRunStatus
    #: Crawl budgets that left pages out (they are not expected pages).
    limits_hit: list[Code]
    rules: NonNegative
    rules_evaluated: NonNegative
    rules_unknown: NonNegative
    rules_not_applicable: NonNegative


class WebsiteAuditSummary(Contract):
    """What an audit shows besides its findings; stored with it, so history reads the same."""

    formula: Annotated[str, Field(min_length=1, max_length=2000)]
    subscores: list[WebsiteSubscore]
    coverage: WebsiteCoverage
    pages: list[WebsitePageView]
    languages: list[Code]
    lab_tool: Label
    notes: list[Code]


class WebsiteAuditItem(Contract):
    id: Id
    project_id: Id
    site: Label
    root_url: str
    final_url: str | None
    source_run_id: Id
    previous_audit_id: Id | None
    status: WebsiteAuditStatus
    ruleset_version: Label
    extractor_version: Label
    pages_expected: NonNegative
    pages_analyzed: NonNegative
    coverage: Confidence
    subscores: list[WebsiteSubscore]
    failed: NonNegative
    warnings: NonNegative
    unknown: NonNegative
    crawled_at: UtcTimestamp
    created_at: UtcTimestamp


class WebsiteAuditListQuery(Contract):
    """The latest audit of every site of a project (or of every project), or every audit of one
    site when `site` is given, newest first."""

    project_id: Id | None
    site: Label | None
    limit: PageSize
    cursor: Cursor | None


class WebsiteAuditPage(Contract):
    items: list[WebsiteAuditItem]
    next_cursor: Cursor | None
    total: NonNegative


class WebsiteAuditQuery(Contract):
    audit_id: Id


class WebsiteFindingView(Contract):
    rule_id: Code
    rule_version: Label
    title: Label
    category: WebsiteCategory
    result: WebsiteFindingResult
    severity: WebsiteFindingSeverity
    observed_value: str | None
    expected_condition: Annotated[str, Field(min_length=1)]
    remediation: str | None
    commercial_relevance: str | None
    confidence: Confidence
    evidence: list[WebsiteEvidence]


class WebsiteTechnologyView(Contract):
    key: Code
    name: Label
    category: Code
    version: str | None
    confidence: Confidence
    evidence: list[WebsiteEvidence]


class WebsiteConversionView(Contract):
    kind: ConversionElementKind
    label: str | None
    page_url: str
    target: str | None
    confidence: Confidence
    evidence: list[WebsiteEvidence]


class WebsiteFindingChange(Contract):
    """A rule whose result changed; null when the rule did not exist on that side."""

    rule_id: Code
    before: WebsiteFindingResult | None
    after: WebsiteFindingResult | None


class WebsiteScoreChange(Contract):
    category: WebsiteCategory
    before: Score | None
    after: Score | None


class WebsiteDiff(Contract):
    """What changed since the previous audit of the same site."""

    previous_audit_id: Id
    previous_created_at: UtcTimestamp
    ruleset_changed: bool
    final_url_before: str | None
    final_url_after: str | None
    domain_changed: bool
    subscores: list[WebsiteScoreChange]
    findings: list[WebsiteFindingChange]
    technologies_added: list[Code]
    technologies_removed: list[Code]
    pages_added: list[str]
    pages_removed: list[str]


class WebsiteAuditDetail(Contract):
    audit: WebsiteAuditItem
    summary: WebsiteAuditSummary
    findings: list[WebsiteFindingView]
    technologies: list[WebsiteTechnologyView]
    conversion: list[WebsiteConversionView]
    diff: WebsiteDiff | None
    #: Every audit of the site, newest first (at most `MAX_AUDIT_HISTORY`).
    history: list[WebsiteAuditItem]


# ---- contact intelligence (phase 09) ------------------------------------------------------------
# Business contact channels published by legitimate sources or imported by the operator: extracted
# with their source, consolidated per canonical business, validated without contacting anyone,
# reviewed and suppressed. Nothing here calls, emails or submits; a hypothesis is never a contact.

MAX_SUPPRESSION_IMPORT_CHARS = 1_000_000
ChannelKey = Annotated[str, Field(min_length=1, max_length=400)]


class ContactLevel(StrEnum):
    """Whom a channel reaches: the business, one of its locations, or a published person."""

    BUSINESS = "business"
    LOCATION = "location"
    PERSON = "person"


class ContactRole(StrEnum):
    """general: the business's own line, form or profile; department: a named department;
    role_address: a shared mailbox (`info@`, `sales@`); personal: a published person's address or
    number."""

    GENERAL = "general"
    DEPARTMENT = "department"
    ROLE_ADDRESS = "role_address"
    PERSONAL = "personal"


class ContactFreshness(StrEnum):
    FRESH = "fresh"
    AGING = "aging"
    STALE = "stale"


class ContactAction(StrEnum):
    """validate: run the non-intrusive checks again now; confirm / mark_invalid: a person's review
    (with a reason); clear_review: withdraw it; mark_primary: the channel the business shows;
    suppress: add the phone or email to the do-not-contact list."""

    VALIDATE = "validate"
    CONFIRM = "confirm"
    MARK_INVALID = "mark_invalid"
    CLEAR_REVIEW = "clear_review"
    MARK_PRIMARY = "mark_primary"
    SUPPRESS = "suppress"


class DecisionMakerDecision(StrEnum):
    CONFIRM = "confirm"
    REJECT = "reject"
    #: Back to what the extraction said (unreviewed or ambiguous).
    RESET = "reset"


class ContactStartRequest(Contract):
    """Checks the contacts of one project, or of every live project when `project_id` is null (one
    job each). `check_mail_domains` allows DNS lookups of mail domains missing from the cache; off,
    or with the worker offline, the run never touches the network."""

    project_id: Id | None
    check_mail_domains: bool


class ContactStats(Contract):
    """Counts of one contact run, read back from its last checkpoint."""

    pages_scanned: NonNegative
    contacts_found: NonNegative
    forms_found: NonNegative
    people_found: NonNegative
    hypotheses: NonNegative
    businesses: NonNegative
    channels: NonNegative
    changed: NonNegative
    mail_domains_checked: NonNegative
    mail_domain_failures: NonNegative
    likely_valid: NonNegative
    domain_valid: NonNegative
    structurally_valid: NonNegative
    invalid: NonNegative
    stale: NonNegative
    suppressed: NonNegative
    ambiguous_people: NonNegative


class ContactRun(Contract):
    job_id: Id
    project_id: Id
    project_name: Label
    status: JobStatus
    stage: Code
    progress_done: NonNegative
    progress_total: NonNegative | None
    extractor_version: Label
    rules_version: Label
    check_mail_domains: bool
    stats: ContactStats
    requested_at: UtcTimestamp
    started_at: UtcTimestamp | None
    finished_at: UtcTimestamp | None
    events: list[RunEvent]
    is_synthetic: bool


class ContactRunListQuery(Contract):
    project_id: Id | None
    limit: PageSize
    cursor: Cursor | None


class ContactRunPage(Contract):
    items: list[ContactRun]
    next_cursor: Cursor | None
    total: NonNegative


class ContactSignal(Contract):
    """One fact a validation read: `line_type`, `role_address`, `disposable`, `mail_domain`,
    `catch_all` (always `not_checked`), `form_fields`... with its value when it has one."""

    code: Code
    value: Annotated[str, Field(max_length=300)] | None


class ContactSource(Contract):
    """One observation of a channel: the record business it belongs to and where it came from.
    `current` is false when a newer crawl of the same page no longer shows it."""

    contact_point_id: Id
    business_id: Id
    origin: ContactOrigin
    value_raw: str
    published_context: str | None
    role_or_department: str | None
    confidence: Confidence
    observed_at: UtcTimestamp
    current: bool
    provenance: Provenance


class ContactChannel(Contract):
    """A contact channel of a canonical business: every observation of the same value,
    consolidated. `validation_status` is the exact status its observations carry (see
    docs/contact-intelligence.md)."""

    key: ChannelKey
    contact_type: ContactType
    value: str
    platform: str | None
    level: ContactLevel
    location_id: Id | None
    decision_maker_id: Id | None
    role: ContactRole
    role_or_department: str | None
    origin: ContactOrigin
    validation_status: ValidationStatus
    validation_method: Code | None
    validated_at: UtcTimestamp | None
    signals: list[ContactSignal]
    freshness: ContactFreshness
    first_seen_at: UtcTimestamp
    last_seen_at: UtcTimestamp
    #: Distinct acquired records (pages, listings, imports) that show it.
    source_count: NonNegative
    source_kinds: list[SourceKind]
    is_primary: bool
    suppression: ContactSuppression | None
    #: Other canonical businesses of the project that publish the same value.
    shared_with: NonNegative
    #: A person's latest decision about it, if any.
    review: ReviewDecision | None
    sources: list[ContactSource]


class ContactabilityComponent(Contract):
    code: Code
    weight: NonNegative
    value: Confidence
    detail: Annotated[str, Field(max_length=300)] | None


class Contactability(Contract):
    """An explainable subscore of how reachable a business is through the channels it publishes or
    the operator imported. Not the lead score; `score` is null when a gate stops it."""

    score: Score | None
    version: Label
    formula: Annotated[str, Field(min_length=1, max_length=2000)]
    components: list[ContactabilityComponent]
    gates: list[Code]
    channels_counted: NonNegative


class ContactConflict(Contract):
    """Values that disagree (`values_disagree`), or a value other businesses publish too
    (`shared_value`). Shown, never resolved silently."""

    code: Code
    contact_type: ContactType | None
    values: list[str]


class DecisionMakerView(Contract):
    decision_maker: DecisionMaker
    provenance: Provenance
    reasons: list[Code]
    #: Channels published for this person.
    channel_keys: list[ChannelKey]


class ContactDetailQuery(Contract):
    business_id: Id


class ContactDetail(Contract):
    """The contacts of a canonical business: channels (published or imported), hypotheses kept
    apart, published people, conflicts, the contactability subscore and the review history."""

    business_id: Id
    root_business_id: Id
    channels: list[ContactChannel]
    hypotheses: list[ContactChannel]
    decision_makers: list[DecisionMakerView]
    conflicts: list[ContactConflict]
    contactability: Contactability
    business_suppression: ContactSuppression | None
    decisions: list[ReviewDecision]
    rules_version: Label
    extractor_version: Label


class ContactActionRequest(Contract):
    """An action on one channel of a business. Every action but `validate` and `mark_primary` needs
    a reason; `check_mail_domains` lets `validate` ask DNS for an uncached mail domain."""

    business_id: Id
    key: ChannelKey
    action: ContactAction
    reason: ReviewReason | None
    check_mail_domains: bool


class ContactListQuery(Contract):
    """Channels of the canonical businesses of a project (or of every project). Hypotheses and
    suppressed channels are left out unless asked for."""

    project_id: Id | None
    contact_type: ContactType | None
    validation_status: ValidationStatus | None
    role: ContactRole | None
    freshness: ContactFreshness | None
    include_hypotheses: bool
    include_suppressed: bool
    limit: PageSize
    cursor: Cursor | None


class ContactListItem(Contract):
    business_id: Id
    business_name: str
    project_id: Id
    key: ChannelKey
    contact_type: ContactType
    value: str
    platform: str | None
    level: ContactLevel
    role: ContactRole
    role_or_department: str | None
    origin: ContactOrigin
    validation_status: ValidationStatus
    freshness: ContactFreshness
    source_count: NonNegative
    last_seen_at: UtcTimestamp
    is_primary: bool
    suppressed: bool
    is_synthetic: bool


class ContactPage(Contract):
    items: list[ContactListItem]
    next_cursor: Cursor | None
    total: NonNegative


class DecisionMakerListQuery(Contract):
    """Published people of a project, by review status (the review queue asks for `ambiguous`)."""

    project_id: Id | None
    review_status: DecisionMakerReview | None
    limit: PageSize
    cursor: Cursor | None


class DecisionMakerItem(Contract):
    decision_maker: DecisionMaker
    business_name: str
    root_business_id: Id
    reasons: list[Code]
    provenance: Provenance
    is_synthetic: bool


class DecisionMakerPage(Contract):
    items: list[DecisionMakerItem]
    next_cursor: Cursor | None
    total: NonNegative


class DecisionMakerReviewRequest(Contract):
    decision_maker_id: Id
    decision: DecisionMakerDecision
    reason: ReviewReason


class SuppressionListQuery(Contract):
    kind: SuppressionKind | None
    include_lifted: bool
    limit: PageSize
    cursor: Cursor | None


class SuppressionPage(Contract):
    items: list[ContactSuppression]
    next_cursor: Cursor | None
    total: NonNegative


class SuppressionChangeRequest(Contract):
    """Adds an entry (`suppression_id` null, with `kind` and `value`) or lifts one
    (`suppression_id`); both need a reason."""

    suppression_id: Id | None
    kind: SuppressionKind | None
    value: Annotated[str, Field(min_length=1, max_length=320)] | None
    reason: ReviewReason


class SuppressionImportRequest(Contract):
    """A CSV of `kind,value,reason` rows the operator picked; the content travels once."""

    filename: Annotated[str, Field(min_length=1, max_length=260)]
    content: Annotated[str, Field(max_length=MAX_SUPPRESSION_IMPORT_CHARS)]


class SuppressionImportProblem(Contract):
    row: Annotated[int, Field(ge=1)]
    code: Code
    message: Annotated[str, Field(max_length=500)]


class SuppressionImportResult(Contract):
    rows: NonNegative
    added: NonNegative
    already_suppressed: NonNegative
    problems: list[SuppressionImportProblem]


class SuppressionExportRequest(Contract):
    include_lifted: bool


class SuppressionExport(Contract):
    """The list as CSV; cells that a spreadsheet would run as a formula are neutralised."""

    filename: Annotated[str, Field(min_length=1, max_length=260)]
    content: str
    rows: NonNegative
    exported_at: UtcTimestamp


# ---- local AI (phase 10) ------------------------------------------------------------------------
# A local model extracts, classifies, summarises and explains; it never decides a fact. Every answer
# follows a strict schema, cites the input references it rests on and is stored raw next to what it
# validated to. Everything works without a runtime: tasks then say why they are unavailable.

#: Reference to one piece of the input a model saw: `F3` a page fragment, `W2` a website finding,
#: `C1` the contactability subscore, `L1` the Latino evidence status, `S1` a screenshot.
EvidenceRef = Annotated[str, Field(pattern=r"^[A-Z][1-9][0-9]{0,2}$")]
EvidenceRefs = Annotated[list[EvidenceRef], Field(min_length=1, max_length=8)]
#: Ollama model names: `name`, `namespace/name`, with an optional `:tag`.
ModelName = Annotated[
    str,
    Field(
        min_length=1,
        max_length=200,
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*(/[a-zA-Z0-9._-]+)*(:[a-zA-Z0-9._-]+)?$",
    ),
]
AiText = Annotated[str, Field(min_length=1, max_length=300)]


def _between(low: int, high: int) -> AfterValidator:
    """A list length bound checked on input only: `min_length`/`max_length` would export tuple
    types to TypeScript (see DiscoveryCriteria.sources)."""

    def check[T](items: list[T]) -> list[T]:
        if not low <= len(items) <= high:
            raise ValueError(f"expected {low} to {high} items, got {len(items)}")
        return items

    return AfterValidator(check)


class AiOutcome(StrEnum):
    """`insufficient_evidence`: the input does not support an answer; the output says nothing."""

    OK = "ok"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class ExtractedClaimKind(StrEnum):
    """The last four are the Latino declarations a model may propose (latino-evidence.md section 7):
    the rules verify them against the page before any becomes evidence."""

    SERVICE = "service"
    SERVICE_AREA = "service_area"
    LANGUAGE_OFFERED = "language_offered"
    YEARS_IN_BUSINESS = "years_in_business"
    LICENSE = "license"
    HOURS = "hours"
    TEAM = "team"
    OTHER = "other"
    OWNERSHIP_STATEMENT = "ownership_statement"
    OWNERSHIP_MENTION = "ownership_mention"
    CERTIFICATION_STATEMENT = "certification_statement"
    OWNERSHIP_DENIED = "ownership_denied"


class BusinessVertical(StrEnum):
    PLUMBING = "plumbing"
    HVAC = "hvac"
    ELECTRICAL = "electrical"
    ROOFING = "roofing"
    LANDSCAPING = "landscaping"
    CLEANING = "cleaning"
    CONSTRUCTION = "construction"
    AUTO_SERVICES = "auto_services"
    RESTAURANT_FOOD = "restaurant_food"
    BEAUTY_PERSONAL_CARE = "beauty_personal_care"
    HEALTH_MEDICAL = "health_medical"
    DENTAL = "dental"
    LEGAL = "legal"
    ACCOUNTING_FINANCIAL = "accounting_financial"
    REAL_ESTATE = "real_estate"
    RETAIL = "retail"
    PROFESSIONAL_SERVICES = "professional_services"
    OTHER = "other"


class CommercialSignal(StrEnum):
    ONLINE_BOOKING = "online_booking"
    FREE_ESTIMATES = "free_estimates"
    EMERGENCY_SERVICE = "emergency_service"
    FINANCING = "financing"
    BILINGUAL_SERVICE = "bilingual_service"
    MULTIPLE_LOCATIONS = "multiple_locations"
    HIRING = "hiring"
    PROMOTIONS = "promotions"
    REVIEWS_DISPLAYED = "reviews_displayed"
    LICENSED_INSURED = "licensed_insured"


class OpportunityProblem(StrEnum):
    WEAK_CONVERSION_PATH = "weak_conversion_path"
    NO_ONLINE_BOOKING = "no_online_booking"
    POOR_MOBILE_EXPERIENCE = "poor_mobile_experience"
    WEAK_LOCAL_SEO = "weak_local_seo"
    SLOW_OR_BROKEN_SITE = "slow_or_broken_site"
    MISSING_TRUST_SIGNALS = "missing_trust_signals"
    MISSING_MEASUREMENT = "missing_measurement"
    LOW_CONTACTABILITY = "low_contactability"
    OTHER = "other"


class DetectedLanguage(StrEnum):
    EN = "en"
    ES = "es"
    PT = "pt"
    FR = "fr"
    MIXED = "mixed"
    OTHER = "other"


class VisualAspect(StrEnum):
    ABOVE_THE_FOLD_CTA = "above_the_fold_cta"
    READABILITY = "readability"
    VISUAL_TRUST = "visual_trust"
    LAYOUT = "layout"
    IMAGERY = "imagery"
    OTHER = "other"


class ExtractedClaim(Contract):
    kind: ExtractedClaimKind
    statement: AiText
    #: Copied literally from one of the cited fragments; checked before the answer is accepted.
    quote: AiText
    evidence_ids: EvidenceRefs
    confidence: Confidence


class EvidenceExtractionOutput(Contract):
    outcome: AiOutcome
    claims: Annotated[list[ExtractedClaim], Field(max_length=25)]


class NamedEvidence(Contract):
    name: Annotated[str, Field(min_length=1, max_length=80)]
    evidence_ids: EvidenceRefs


class SignalEvidence(Contract):
    signal: CommercialSignal
    evidence_ids: EvidenceRefs


class BusinessClassificationOutput(Contract):
    outcome: AiOutcome
    vertical: BusinessVertical | None
    vertical_evidence_ids: Annotated[list[EvidenceRef], Field(max_length=8)]
    services: Annotated[list[NamedEvidence], Field(max_length=12)]
    signals: Annotated[list[SignalEvidence], Field(max_length=10)]


class CitedPoint(Contract):
    text: AiText
    evidence_ids: EvidenceRefs


class FindingSynthesisOutput(Contract):
    outcome: AiOutcome
    summary: Annotated[str, Field(min_length=1, max_length=600)] | None
    points: Annotated[list[CitedPoint], Field(max_length=8)]


class OpportunityPoint(Contract):
    problem: OpportunityProblem
    explanation: AiText
    evidence_ids: EvidenceRefs


class OpportunityReasoningOutput(Contract):
    """Structured reasoning only: problems and strengths with their evidence and what is missing.
    There is no score here, by design (scoring is phase 11)."""

    outcome: AiOutcome
    problems: Annotated[list[OpportunityPoint], Field(max_length=8)]
    strengths: Annotated[list[CitedPoint], Field(max_length=5)]
    missing_information: Annotated[list[AiText], Field(max_length=5)]


class FragmentLanguage(Contract):
    evidence_id: EvidenceRef
    language: DetectedLanguage
    #: An English rendering, only for a fragment that is not in English.
    english: AiText | None


class LanguageDetectionOutput(Contract):
    outcome: AiOutcome
    fragments: Annotated[list[FragmentLanguage], Field(max_length=40)]


class VisualObservation(Contract):
    aspect: VisualAspect
    observation: AiText
    evidence_ids: EvidenceRefs


class VisualReviewOutput(Contract):
    outcome: AiOutcome
    observations: Annotated[list[VisualObservation], Field(max_length=8)]


class AiInputKind(StrEnum):
    PAGE_FRAGMENT = "page_fragment"
    WEBSITE_FINDING = "website_finding"
    CONTACTABILITY = "contactability"
    LATINO_STATUS = "latino_status"
    SCREENSHOT = "screenshot"
    #: A claim of a deterministic sales brief (phase 12), with the evidence it rests on.
    BRIEF_CLAIM = "brief_claim"


class AiInputRef(Contract):
    """One piece of evidence a model saw, as it saw it: the page, finding or subscore it came from
    and the exact text (a screenshot keeps only its path and hash)."""

    ref: EvidenceRef
    kind: AiInputKind
    #: raw_records id (page fragment, screenshot), website_findings id, business id (subscores).
    entity_id: Id | None
    source: Annotated[str, Field(max_length=2048)] | None
    text: Annotated[str, Field(max_length=2000)]
    sha256: Sha256


class AiUnavailableReason(StrEnum):
    TASK_DISABLED = "task_disabled"
    NO_MODEL_ASSIGNED = "no_model_assigned"
    RUNTIME_UNAVAILABLE = "runtime_unavailable"
    MODEL_NOT_INSTALLED = "model_not_installed"
    NO_VISION_MODEL = "no_vision_model"
    CIRCUIT_OPEN = "circuit_open"


class AiCircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class AiActivity(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AiVramEstimate(Contract):
    """An estimate from the model's own metadata (weights file, layers, KV heads) for one context
    length; the runtime's report of a loaded model (`size_vram_bytes`) is what actually happened."""

    num_ctx: Annotated[int, Field(ge=512, le=32768)]
    weights_mib: NonNegative
    kv_cache_mib: NonNegative
    overhead_mib: NonNegative
    total_mib: NonNegative
    device: AiDevice
    #: The context length it would run with after the documented reduction, if any.
    fitted_ctx: Annotated[int, Field(ge=512, le=32768)] | None
    note: Annotated[str, Field(max_length=300)] | None


class AiModelInfo(Contract):
    name: ModelName
    digest: str | None
    size_bytes: NonNegative | None
    family: str | None
    parameter_size: str | None
    quantization: str | None
    context_length: NonNegative | None
    #: What the runtime says the model can do (`completion`, `vision`, `thinking`, `embedding`).
    capabilities: list[Code]
    loaded: bool
    size_vram_bytes: NonNegative | None
    estimate: AiVramEstimate | None


class AiTaskInfo(Contract):
    task_type: AiTaskType
    task_version: Label
    output_schema_version: Label
    prompt_template_id: Code
    prompt_template_version: Label
    enabled: bool
    profile: AiProfile
    model: ModelName | None
    available: bool
    unavailable_reason: AiUnavailableReason | None
    requires_vision: bool
    batchable: bool
    max_input_chars: NonNegative
    max_refs: NonNegative
    timeout_s: NonNegative
    temperature: Annotated[float, Field(ge=0, le=1)]


class AiRecommendation(Contract):
    """A recommendation, never a setting: the best benchmarked model on this machine for a task
    (accuracy first, then median latency), among models still installed."""

    task_type: AiTaskType
    model: ModelName
    accuracy: Confidence
    latency_p50_ms: NonNegative | None
    benchmark_id: Id
    benchmarked_at: UtcTimestamp


class AiResources(Contract):
    ram_total_mib: NonNegative | None
    ram_free_mib: NonNegative | None
    vram_total_mib: NonNegative | None
    vram_free_mib: NonNegative | None
    #: `low_ram`, `low_vram`: below the configured limits while local AI works.
    pressure: list[Code]
    #: Crawling and browser renders wait while this is true.
    crawl_waiting: bool
    sampled_at: UtcTimestamp


class AiSchedulerState(Contract):
    queued: NonNegative
    running: NonNegative
    capacity: NonNegative
    circuit: AiCircuitState
    consecutive_failures: NonNegative
    open_until: UtcTimestamp | None
    last_error: Annotated[str, Field(max_length=500)] | None
    #: Since the worker started.
    completed: NonNegative
    failed: NonNegative
    cache_hits: NonNegative
    #: Median of the last 50 model calls.
    latency_p50_ms: NonNegative | None


class AiBenchmarkProgress(Contract):
    state: AiActivity
    models: list[ModelName]
    task_types: list[AiTaskType]
    done: NonNegative
    total: NonNegative
    current: Annotated[str, Field(max_length=300)] | None
    error: Annotated[str, Field(max_length=500)] | None
    started_at: UtcTimestamp | None
    finished_at: UtcTimestamp | None


class AiModelChangeAction(StrEnum):
    PULL = "pull"
    DELETE = "delete"


class AiModelChange(Contract):
    action: AiModelChangeAction
    model: ModelName
    state: AiActivity
    completed_bytes: NonNegative | None
    total_bytes: NonNegative | None
    detail: Annotated[str, Field(max_length=300)] | None
    started_at: UtcTimestamp
    finished_at: UtcTimestamp | None


class LlmFitModel(Contract):
    name: Annotated[str, Field(max_length=300)]
    ollama_name: Annotated[str, Field(max_length=300)] | None
    fit_level: Annotated[str, Field(max_length=64)] | None
    run_mode: Annotated[str, Field(max_length=64)] | None
    runtime: Annotated[str, Field(max_length=64)] | None
    quantization: Annotated[str, Field(max_length=64)] | None
    memory_required_gb: Annotated[float, Field(ge=0)] | None
    estimated_tps: Annotated[float, Field(ge=0)] | None
    installed: bool | None
    notes: list[Annotated[str, Field(max_length=300)]]


class LlmFitReport(Contract):
    """What the external LLM Fit tool says, when installed and asked. An outside opinion: nothing
    here is applied, installed or changed."""

    available: bool
    version: Annotated[str, Field(max_length=100)] | None
    models: list[LlmFitModel]
    error: Annotated[str, Field(max_length=500)] | None
    checked_at: UtcTimestamp


class AiStatusQuery(Contract):
    #: Runs the external LLM Fit tool (a few seconds) when it is installed.
    include_llm_fit: bool


class AiStatus(Contract):
    runtime: OllamaStatus
    gpu: GpuStatus
    models: list[AiModelInfo]
    profiles: list[AiProfileSetting]
    tasks: list[AiTaskInfo]
    limits: AiResourceLimits
    resources: AiResources
    scheduler: AiSchedulerState
    recommendations: list[AiRecommendation]
    benchmark: AiBenchmarkProgress
    #: The latest result of each model on each task.
    benchmark_results: list[AiBenchmarkResult]
    model_change: AiModelChange | None
    llm_fit: LlmFitReport | None
    #: False with `LEAD_DISCOVERY_OFFLINE=1`: the runtime may not download models then.
    downloads_allowed: bool
    checked_at: UtcTimestamp


class AiProfileChange(Contract):
    profile: AiProfile
    model: ModelName | None
    num_ctx: Annotated[int, Field(ge=512, le=32768)]
    allow_cpu: bool


class AiTaskChange(Contract):
    task_type: AiTaskType
    enabled: bool
    profile: AiProfile


class AiConfigureRequest(Contract):
    profiles: Annotated[list[AiProfileChange], _between(0, 3)]
    tasks: Annotated[list[AiTaskChange], _between(0, len(AiTaskType))]
    min_free_ram_mib: Annotated[int, Field(ge=256, le=65536)]
    min_free_vram_mib: Annotated[int, Field(ge=0, le=65536)]


class AiStartRequest(Contract):
    """Runs tasks over the canonical businesses of one project (every project when null), or over
    the businesses listed (at most 200). Identical requests reuse valid stored results."""

    project_id: Id | None
    business_ids: Annotated[list[Id], _between(0, 200)]
    task_types: Annotated[list[AiTaskType], _between(1, len(AiTaskType))]

    @model_validator(mode="after")
    def _briefs_lead_by_lead(self) -> Self:
        if AiTaskType.SALES_BRIEF in self.task_types and not self.business_ids:
            raise ValueError("a sales brief is worded lead by lead: name the businesses")
        return self


class AiStats(Contract):
    targets: NonNegative
    done: NonNegative
    succeeded: NonNegative
    insufficient_evidence: NonNegative
    invalid_output: NonNegative
    failed: NonNegative
    cancelled: NonNegative
    cache_hits: NonNegative
    skipped_no_input: NonNegative
    skipped_unavailable: NonNegative
    #: Latino declarations proposed by extractions and what the rules made of them.
    latino_candidates_accepted: NonNegative
    latino_candidates_rejected: NonNegative


class AiRun(Contract):
    job_id: Id
    project_id: Id
    project_name: Label
    status: JobStatus
    stage: Code
    progress_done: NonNegative
    progress_total: NonNegative | None
    task_types: list[AiTaskType]
    business_count: NonNegative | None
    stats: AiStats
    requested_at: UtcTimestamp
    started_at: UtcTimestamp | None
    finished_at: UtcTimestamp | None
    events: list[RunEvent]
    is_synthetic: bool


class AiRunListQuery(Contract):
    project_id: Id | None
    limit: PageSize
    cursor: Cursor | None


class AiRunPage(Contract):
    items: list[AiRun]
    next_cursor: Cursor | None
    total: NonNegative


class AiBenchmarkAction(StrEnum):
    START = "start"
    CANCEL = "cancel"


class AiBenchmarkRequest(Contract):
    action: AiBenchmarkAction
    models: Annotated[list[ModelName], _between(0, 4)]
    task_types: Annotated[list[AiTaskType], _between(0, len(AiTaskType))]


class AiInferenceListQuery(Contract):
    task_type: AiTaskType | None
    status: InferenceStatus | None
    business_id: Id | None
    review_status: AiReviewStatus | None
    limit: PageSize
    cursor: Cursor | None


class AiInferenceItem(Contract):
    id: Id
    task_type: AiTaskType
    status: InferenceStatus
    review_status: AiReviewStatus
    model: Label
    device: AiDevice
    business_id: Id | None
    business_name: str | None
    job_id: Id | None
    duration_ms: NonNegative | None
    attempts: NonNegative
    error_code: Code | None
    created_at: UtcTimestamp
    is_synthetic: bool


class AiInferencePage(Contract):
    items: list[AiInferenceItem]
    next_cursor: Cursor | None
    total: NonNegative


class AiInferenceQuery(Contract):
    inference_run_id: Id


class AiInferenceDetail(Contract):
    """A model's answer as evidence-backed output: the run, every raw answer next to what it
    validated to, the exact inputs it cited and the people's decisions. Not a fact."""

    run: InferenceRun
    business_name: str | None
    outputs: list[InferenceOutput]
    inputs: list[AiInputRef]
    #: `suspected_prompt_injection:F3`, `context_reduced`, `ran_on_cpu`...
    warnings: list[Annotated[str, Field(max_length=200)]]
    decisions: list[ReviewDecision]
    is_synthetic: bool


class AiReviewDecision(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"
    #: Withdraws the decision.
    RESET = "reset"


class AiReviewRequest(Contract):
    inference_run_id: Id
    decision: AiReviewDecision
    reason: ReviewReason


class AiModelChangeRequest(Contract):
    """Downloads or deletes a model through the runtime. `confirmation` must repeat the model name:
    nothing is downloaded or deleted without that explicit step."""

    action: AiModelChangeAction
    model: ModelName
    confirmation: ModelName


# ---- ICP, opportunity and lead scoring (phase 11) ----------------------------------------------
# Which businesses deserve research and contact, why, with which offer and how sure. ICP profiles
# and scoring models are versioned declarative definitions; a score run applies one of each to the
# facts of every canonical business. Gates run before weights, unknown is never zero, every point
# carries its evidence, and nothing here contacts a business or lets a model change a number.

ICP_SCHEMA_VERSION = 1
SCORING_SCHEMA_VERSION = 1
MAX_AREAS = 25
MAX_OFFERS = 20
Weight = Annotated[int, Field(ge=0, le=10)]
FormulaWeight = Annotated[int, Field(ge=0, le=100)]
Points = Annotated[float, Field(ge=0, le=100)]
ScoreText = Annotated[str, Field(min_length=1, max_length=300)]


class ScoreComponent(StrEnum):
    """The independent components of a scorecard, 0 to 100 each (a null value is unknown)."""

    ICP_MATCH = "ICP_MATCH"
    LATINO_CONFIDENCE = "LATINO_CONFIDENCE"
    BUSINESS_HEALTH = "BUSINESS_HEALTH"
    DIGITAL_GAP = "DIGITAL_GAP"
    BUYING_CAPACITY = "BUYING_CAPACITY"
    CONTACTABILITY = "CONTACTABILITY"
    COMMERCIAL_INTENT = "COMMERCIAL_INTENT"
    DATA_CONFIDENCE = "DATA_CONFIDENCE"
    OPPORTUNITY_SCORE = "OPPORTUNITY_SCORE"
    FINAL_LEAD_SCORE = "FINAL_LEAD_SCORE"


class OpportunityInput(StrEnum):
    """What OPPORTUNITY_SCORE may weigh. Latino evidence is not one of them, by design."""

    DIGITAL_GAP = "DIGITAL_GAP"
    BUSINESS_HEALTH = "BUSINESS_HEALTH"
    BUYING_CAPACITY = "BUYING_CAPACITY"
    COMMERCIAL_INTENT = "COMMERCIAL_INTENT"


class FinalInput(StrEnum):
    ICP_MATCH = "ICP_MATCH"
    OPPORTUNITY_SCORE = "OPPORTUNITY_SCORE"
    CONTACTABILITY = "CONTACTABILITY"
    LATINO_CONFIDENCE = "LATINO_CONFIDENCE"


class ProblemType(StrEnum):
    """Observable problems the opportunity engine detects from stored facts."""

    NO_WEBSITE = "no_website"
    WEBSITE_UNREACHABLE = "website_unreachable"
    WEAK_CONVERSION_PATH = "weak_conversion_path"
    NO_ONLINE_BOOKING = "no_online_booking"
    WEAK_LOCAL_SEO = "weak_local_seo"
    WEAK_TECHNICAL_SEO = "weak_technical_seo"
    POOR_MOBILE_EXPERIENCE = "poor_mobile_experience"
    #: Not detected is not absent: tags inside a tag manager or consent tool are invisible.
    MEASUREMENT_NOT_DETECTED = "measurement_not_detected"


class CriterionImportance(StrEnum):
    """must_have: a gate before any weight; nice_to_have: counts in ICP_MATCH; ignore: not read."""

    MUST_HAVE = "must_have"
    NICE_TO_HAVE = "nice_to_have"
    IGNORE = "ignore"


class LatinoRequirement(StrEnum):
    """What a campaign asks of Latino evidence: nothing, market affinity or better, or a verified
    Latino-owned business. It filters; it never measures quality or buying capacity."""

    NONE = "none"
    MARKET_AFFINITY = "market_affinity"
    VERIFIED_LATINO_OWNED = "verified_latino_owned"


class DigitalMaturity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class GateOutcome(StrEnum):
    PASS = "pass"  # noqa: S105 - a gate outcome, not a password
    FAIL = "fail"
    UNKNOWN = "unknown"


class FeatureState(StrEnum):
    """known: evaluated from evidence; unknown: the facts cannot show it (never a zero);
    not_applicable: does not apply to this business (never a penalty)."""

    KNOWN = "known"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class ScoreEvidenceKind(StrEnum):
    BUSINESS = "business"
    WEBSITE_AUDIT = "website_audit"
    WEBSITE_FINDING = "website_finding"
    WEBSITE_TECHNOLOGY = "website_technology"
    CONTACT_CHANNEL = "contact_channel"
    DECISION_MAKER = "decision_maker"
    LATINO_ASSESSMENT = "latino_assessment"


class Area(Contract):
    """A place an ICP includes or excludes. A part left empty matches any value."""

    country_code: CountryCode
    region: RegionCode | None
    locality: Term | None
    postal_code: PostalCode | None


class GeographyCriterion(Contract):
    importance: CriterionImportance
    weight: Weight
    include: Annotated[list[Area], Field(max_length=MAX_AREAS)]
    exclude: Annotated[list[Area], Field(max_length=MAX_AREAS)]


class CategoryCriterion(Contract):
    """Terms matched as whole words against the category and service labels sources observed."""

    importance: CriterionImportance
    weight: Weight
    terms: Terms
    excluded_terms: Terms


class SizeCriterion(Contract):
    """Size by its only observable proxy: distinct locations. Employees and revenue are not seen."""

    importance: CriterionImportance
    weight: Weight
    min_locations: Annotated[int, Field(ge=1, le=1000)]


class FreshnessCriterion(Contract):
    """A source listed the business within this many days."""

    importance: CriterionImportance
    weight: Weight
    max_days_since_seen: Annotated[int, Field(ge=1, le=3650)]


class ReputationCriterion(Contract):
    """Rating and reviews with their source. No connected source provides them yet: this criterion
    is always unknown, never a failure."""

    importance: CriterionImportance
    weight: Weight
    min_rating: Annotated[float, Field(ge=0, le=5)] | None
    min_reviews: Annotated[int, Field(ge=0)] | None


class MaturityCriterion(Contract):
    """Digital maturity from the website subscores: low below 50, medium below 80, high otherwise;
    a business without a website is low."""

    importance: CriterionImportance
    weight: Weight
    wanted: list[DigitalMaturity]


class ProblemCriterion(Contract):
    importance: CriterionImportance
    weight: Weight
    wanted: list[ProblemType]


class ChannelCriterion(Contract):
    """At least one usable published channel of these types, and a published decision maker when
    asked for."""

    importance: CriterionImportance
    weight: Weight
    any_of: list[ContactType]
    require_decision_maker: bool


class CapacityCriterion(Contract):
    """Buying capacity through explicit proxies only (locations, paid tools, a published team, site
    depth), never a financial claim."""

    importance: CriterionImportance
    weight: Weight
    min_proxies: Annotated[int, Field(ge=1, le=4)]


class IcpExclusions(Contract):
    """Hard exclusions, checked before anything is weighed."""

    names: Terms
    domains: Annotated[list[DomainName], Field(max_length=MAX_TERMS)]
    categories: Terms
    regions: Annotated[list[RegionCode], Field(max_length=MAX_TERMS)]
    postal_codes: Annotated[list[PostalCode], Field(max_length=MAX_TERMS)]


class Offer(Contract):
    """Something the seller can actually deliver, and the problems it solves."""

    key: Code
    name: Label
    description: Annotated[str, Field(max_length=600)]
    solves: list[ProblemType]
    capabilities: Terms
    #: Categories where this offer does not apply (matched like `CategoryCriterion.terms`).
    not_for_categories: Terms


class IcpDefinition(Contract):
    """One version of an ICP profile, stored verbatim in `icp_profile_versions.definition_json`."""

    schema_version: Literal[1]
    geography: GeographyCriterion
    categories: CategoryCriterion
    size: SizeCriterion
    freshness: FreshnessCriterion
    reputation: ReputationCriterion
    digital_maturity: MaturityCriterion
    problems: ProblemCriterion
    channels: ChannelCriterion
    buying_capacity: CapacityCriterion
    latino: LatinoRequirement
    exclusions: IcpExclusions
    offers: Annotated[list[Offer], _between(0, MAX_OFFERS)]


class FeatureWeight(Contract):
    feature: Code
    weight: Weight


class OpportunityWeight(Contract):
    component: OpportunityInput
    weight: FormulaWeight


class FinalWeight(Contract):
    component: FinalInput
    weight: FormulaWeight


class NotApplicableRule(Contract):
    """A feature that does not apply to businesses in these categories (booking for roofers...)."""

    feature: Code
    categories: Terms


class ScoringDefinition(Contract):
    """One version of a scoring model, stored verbatim in `scoring_model_versions.definition_json`.
    Weights are relative within each formula; a weight of 0 leaves a feature or input out."""

    schema_version: Literal[1]
    features: list[FeatureWeight]
    opportunity: list[OpportunityWeight]
    final: list[FinalWeight]
    not_applicable: list[NotApplicableRule]
    #: Share of the final formula's weight that must be known, else `insufficient_data`.
    min_known_weight: Confidence
    priority_threshold: Score
    review_threshold: Score
    #: A website subscore below this is a weak area.
    weak_subscore_below: Score
    #: Without an observable problem an offer can solve there is no opportunity: gate it.
    require_observable_problem: bool


# ---- facts: what the engine read, independent of any ICP or model --------------------------------


class FactLocation(Contract):
    country_code: str | None
    region: str | None
    locality: str | None
    postal_code: str | None


class FactFinding(Contract):
    id: Id
    rule_id: Code
    category: WebsiteCategory
    result: WebsiteFindingResult
    severity: WebsiteFindingSeverity
    confidence: Confidence
    observed_value: Annotated[str, Field(max_length=300)] | None
    url: Annotated[str, Field(max_length=2048)] | None


class FactSubscore(Contract):
    category: WebsiteCategory
    score: Score | None


class FactTechnology(Contract):
    id: Id
    key: Code
    name: Label
    category: Code


class FactAudit(Contract):
    """The latest website audit of the business's crawled site."""

    id: Id
    site: Label
    status: WebsiteAuditStatus
    coverage: Confidence
    pages_analyzed: NonNegative
    created_at: UtcTimestamp
    subscores: list[FactSubscore]
    #: The findings features read, plus every failure and warning.
    findings: list[FactFinding]
    technologies: list[FactTechnology]
    previous_audit_id: Id | None
    #: Subscores and rule results that changed since the previous audit.
    changes: NonNegative


class FactChannel(Contract):
    key: ChannelKey
    contact_type: ContactType
    validation_status: ValidationStatus
    level: ContactLevel
    decision_maker_id: Id | None


class FactPerson(Contract):
    id: Id
    relationship: DecisionMakerRelationship
    review_status: DecisionMakerReview
    role: Annotated[str, Field(max_length=300)] | None


class FactLatino(Contract):
    assessment_id: Id
    status: LatinoStatus
    confidence: Confidence
    method: AssessmentMethod
    #: Evidence the assessment cites as contradicting.
    contradictions: NonNegative


class BusinessFacts(Contract):
    """Everything a score read about one canonical business, stored with the score, so the same
    facts can be scored again with other versions (`facts_from_run_id`)."""

    facts_version: Label
    business_id: Id
    project_id: Id
    display_name: str
    status: BusinessStatus
    is_synthetic: bool
    first_seen_at: UtcTimestamp
    last_seen_at: UtcTimestamp
    collected_at: UtcTimestamp
    #: Distinct acquired records behind its names.
    source_count: NonNegative
    #: Fields whose records disagree.
    conflict_count: NonNegative
    names: list[str]
    locations: list[FactLocation]
    categories: list[str]
    domains: list[str]
    audit: FactAudit | None
    #: Published or imported channels; hypotheses are never facts.
    channels: list[FactChannel]
    contactability: Contactability
    people: list[FactPerson]
    business_suppressed: bool
    latino: FactLatino | None


# ---- scorecards ----------------------------------------------------------------------------------


class ScoreEvidence(Contract):
    """What a point rests on: the stored row it came from, and where to see it."""

    kind: ScoreEvidenceKind
    ref: Annotated[str, Field(min_length=1, max_length=400)]
    label: Label
    url: Annotated[str, Field(max_length=2048)] | None


class ScoreFeature(Contract):
    """One evaluated feature. `points`: what it adds to its component; `impact`: to the total."""

    feature: Code
    component: ScoreComponent
    problem: ProblemType | None
    state: FeatureState
    value: Confidence | None
    weight: NonNegative
    points: float | None
    impact: float | None
    detail: ScoreText
    #: Why it is unknown or not applicable.
    reason: Code | None
    evidence: list[ScoreEvidence]


class ScoreComponentView(Contract):
    """`weight` and `contribution`: in the formula that uses this component (0 and null for
    DATA_CONFIDENCE and FINAL_LEAD_SCORE); `coverage`: share of its own weight that was known."""

    component: ScoreComponent
    value: Points | None
    weight: NonNegative
    contribution: float | None
    coverage: Confidence
    formula: Annotated[str, Field(min_length=1, max_length=2000)]


class GateView(Contract):
    gate: Code
    outcome: GateOutcome
    detail: ScoreText
    evidence: list[ScoreEvidence]


class CriterionView(Contract):
    criterion: Code
    importance: CriterionImportance
    outcome: GateOutcome
    weight: NonNegative
    detail: ScoreText
    evidence: list[ScoreEvidence]


class ScoreReason(Contract):
    feature: Code
    text: ScoreText
    impact: float
    evidence: list[ScoreEvidence]


class ScoreRisk(Contract):
    """A contradiction, a caveat or a reason to double-check before acting."""

    code: Code
    detail: ScoreText
    evidence: list[ScoreEvidence]


class MissingData(Contract):
    """An unknown feature and the share of the final formula it could have moved."""

    feature: Code
    component: ScoreComponent
    detail: ScoreText
    weight_share: Confidence


class InferredImpact(Contract):
    """The probable commercial impact of a problem: an inference, labelled as one."""

    problem: ProblemType
    text: ScoreText
    is_inference: Literal[True]


class OfferRecommendation(Contract):
    offer_key: Code
    offer_name: Label
    problems: list[ProblemType]
    why: Annotated[str, Field(min_length=1, max_length=600)]
    evidence: list[ScoreEvidence]


class MessageDraft(Contract):
    """A commercial message to adapt by hand. It is never sent."""

    subject: Annotated[str, Field(min_length=1, max_length=200)]
    body: Annotated[str, Field(min_length=1, max_length=2000)]
    language: Literal["en"]
    is_draft: Literal[True]


class ScoreVersions(Contract):
    engine_version: Label
    facts_version: Label
    scoring_model_key: Code
    scoring_model_version: Annotated[int, Field(ge=1)]
    is_heuristic: bool
    icp_profile_name: Label
    icp_profile_version: Annotated[int, Field(ge=1)]


class LeadScorecard(Contract):
    """The explanation of one lead in one run, stored with its score (`business_scores`)."""

    business_id: Id
    tier: LeadTier
    total_score: Points | None
    confidence: Confidence
    #: Share of the final formula's weight that was known.
    known_weight: Confidence
    versions: ScoreVersions
    components: list[ScoreComponentView]
    features: list[ScoreFeature]
    gates: list[GateView]
    criteria: list[CriterionView]
    #: The top three positive reasons.
    reasons: list[ScoreReason]
    #: Reasons to contact now.
    contact_now: list[ScoreReason]
    impacts: list[InferredImpact]
    risks: list[ScoreRisk]
    missing: list[MissingData]
    offer: OfferRecommendation | None
    message: MessageDraft | None


# ---- scoring IPC ---------------------------------------------------------------------------------


class ScoringCatalogQuery(Contract):
    project_id: Id | None


class IcpProfileView(Contract):
    """A profile with every version, newest first, and what validation says about the newest."""

    profile: IcpProfile
    versions: list[IcpProfileVersion]
    problems: list[CriteriaProblem]
    is_synthetic: bool


class ScoringModelView(Contract):
    model: ScoringModel
    versions: list[ScoringModelVersion]
    problems: list[CriteriaProblem]


class FeatureInfo(Contract):
    feature: Code
    component: ScoreComponent
    problem: ProblemType | None
    description: Annotated[str, Field(min_length=1, max_length=600)]


class ScoringCatalog(Contract):
    """Profiles and models to build with, the features a model can weigh and blank templates."""

    icp_profiles: list[IcpProfileView]
    models: list[ScoringModelView]
    features: list[FeatureInfo]
    engine_version: Label
    icp_template: IcpDefinition
    scoring_template: ScoringDefinition


class IcpSaveRequest(Contract):
    """A new profile when `profile_id` is null (clone = a new profile from another's definition),
    otherwise a new version of it; an identical definition reuses the latest version."""

    profile_id: Id | None
    project_id: Id | None
    name: Label
    description: Annotated[str, Field(max_length=2000)] | None
    definition: IcpDefinition


class ScoringModelSaveRequest(Contract):
    model_id: Id | None
    name: Label
    description: Annotated[str, Field(max_length=2000)] | None
    definition: ScoringDefinition


class ScoringTarget(StrEnum):
    ICP_PROFILE = "icp_profile"
    SCORING_MODEL = "scoring_model"


class ScoringChangeAction(StrEnum):
    ACTIVATE = "activate"
    ARCHIVE = "archive"
    RESTORE = "restore"


class ScoringChangeRequest(Contract):
    """Activates a version (`version_id`), archives or restores a profile or a model."""

    target: ScoringTarget
    id: Id
    action: ScoringChangeAction
    version_id: Id | None
    reason: ReviewReason


class ScoringSimulationRequest(Contract):
    """Scores drafts in memory, over a project's facts read now or a run's stored facts. Writes
    nothing."""

    project_id: Id | None
    facts_from_run_id: Id | None
    icp: IcpDefinition
    scoring: ScoringDefinition
    top: Annotated[int, Field(ge=1, le=100)]


class GateExample(Contract):
    business_id: Id
    display_name: str
    detail: ScoreText


class GatePreview(Contract):
    """How many businesses a gate lets through, stops or cannot decide, with examples of why."""

    gate: Code
    passed: NonNegative
    failed: NonNegative
    unknown: NonNegative
    examples: list[GateExample]


class TierCounts(Contract):
    priority: NonNegative
    review: NonNegative
    low: NonNegative
    insufficient_data: NonNegative
    excluded: NonNegative


class ScoreBin(Contract):
    """Scored businesses whose total is in [low, low + 10) (the last bin includes 100)."""

    low: Annotated[int, Field(ge=0, le=90)]
    businesses: NonNegative


class ComponentValue(Contract):
    component: ScoreComponent
    value: Points | None


class LeadScoreItem(Contract):
    business_id: Id
    project_id: Id
    display_name: str
    locality: str | None
    region: str | None
    primary_category: str | None
    lead_stage: LeadStage
    latino_status: LatinoStatus
    #: Position in the run: tier, then total, then confidence, then business id (docs/scoring.md).
    rank: Annotated[int, Field(ge=1)]
    tier: LeadTier
    total_score: Points | None
    confidence: Confidence
    components: list[ComponentValue]
    top_reason: ScoreText | None
    offer_key: Code | None
    problems: list[ProblemType]
    is_synthetic: bool


class ScoringSimulation(Contract):
    businesses: NonNegative
    tiers: TierCounts
    distribution: list[ScoreBin]
    gates: list[GatePreview]
    top: list[LeadScoreItem]
    facts_from_run_id: Id | None
    collected_at: UtcTimestamp
    problems: list[CriteriaProblem]


class ScoringStartRequest(Contract):
    project_id: Id
    icp_profile_version_id: Id
    scoring_model_version_id: Id
    facts_from_run_id: Id | None


class ScoreRunStats(Contract):
    businesses: NonNegative
    priority: NonNegative
    review: NonNegative
    low: NonNegative
    insufficient_data: NonNegative
    excluded: NonNegative


class ScoreRunItem(Contract):
    job_id: Id
    score_run_id: Id
    project_id: Id
    project_name: Label
    status: JobStatus
    stage: Code
    progress_done: NonNegative
    progress_total: NonNegative | None
    scoring_model_id: Id
    scoring_model_key: Code
    scoring_model_name: Label
    scoring_model_version: Annotated[int, Field(ge=1)]
    is_heuristic: bool
    icp_profile_id: Id
    icp_profile_name: Label
    icp_profile_version: Annotated[int, Field(ge=1)]
    engine_version: Label
    facts_from_run_id: Id | None
    stats: ScoreRunStats
    distribution: list[ScoreBin]
    requested_at: UtcTimestamp
    started_at: UtcTimestamp | None
    finished_at: UtcTimestamp | None
    events: list[RunEvent]
    is_synthetic: bool


class ScoreRunListQuery(Contract):
    project_id: Id | None
    limit: PageSize
    cursor: Cursor | None


class ScoreRunPage(Contract):
    items: list[ScoreRunItem]
    next_cursor: Cursor | None
    total: NonNegative


class LeadScoreQuery(Contract):
    """The ranked leads of a run; `score_run_id` null reads the newest finished run."""

    score_run_id: Id | None
    tier: LeadTier | None
    min_score: Points | None
    problem: ProblemType | None
    search: Annotated[str, Field(max_length=200)] | None
    limit: PageSize
    cursor: Cursor | None


class LeadScorePage(Contract):
    score_run: ScoreRunItem | None
    items: list[LeadScoreItem]
    next_cursor: Cursor | None
    total: NonNegative


class ScorecardQuery(Contract):
    """`score_run_id` null reads the newest finished run that scored the business."""

    business_id: Id
    score_run_id: Id | None


class LeadScoreHistoryItem(Contract):
    score_run_id: Id
    created_at: UtcTimestamp
    scoring_model_key: Code
    scoring_model_version: Annotated[int, Field(ge=1)]
    icp_profile_name: Label
    icp_profile_version: Annotated[int, Field(ge=1)]
    tier: LeadTier
    total_score: Points | None
    confidence: Confidence


class AiDraftedReason(Contract):
    """A local model's wording of a problem, kept only when every finding it cites is evidence of
    this scorecard. It never changes a number."""

    inference_run_id: Id
    model: Label
    problem: OpportunityProblem
    explanation: ScoreText
    evidence: list[ScoreEvidence]


class LeadScorecardDetail(Contract):
    score_run: ScoreRunItem
    business_id: Id
    display_name: str
    lead_stage: LeadStage
    rank: Annotated[int, Field(ge=1)]
    scorecard: LeadScorecard
    #: Every run that scored this business, newest first.
    history: list[LeadScoreHistoryItem]
    outcomes: list[LeadOutcome]
    decisions: list[ReviewDecision]
    ai_reasons: list[AiDraftedReason]
    #: Model points dropped because they cited something this scorecard does not rest on.
    ai_dropped: NonNegative
    is_synthetic: bool


class ScoreCompareQuery(Contract):
    left_run_id: Id
    right_run_id: Id
    top_k: Annotated[int, Field(ge=1, le=500)]


class RankChange(Contract):
    business_id: Id
    display_name: str
    left_rank: Annotated[int, Field(ge=1)] | None
    right_rank: Annotated[int, Field(ge=1)] | None
    left_tier: LeadTier | None
    right_tier: LeadTier | None
    left_total: Points | None
    right_total: Points | None


class ScoreComparison(Contract):
    """Two runs side by side: who entered or left the top K and how ranks moved."""

    left: ScoreRunItem
    right: ScoreRunItem
    #: The right run scored the left run's stored facts (or the other way round).
    same_facts: bool
    top_k: Annotated[int, Field(ge=1, le=500)]
    entered: NonNegative
    left_out: NonNegative
    mean_abs_rank_change: Annotated[float, Field(ge=0)] | None
    changes: list[RankChange]


class CalibrationQuery(Contract):
    score_run_id: Id


class PrecisionAtK(Contract):
    k: Annotated[int, Field(ge=1)]
    labeled: NonNegative
    positives: NonNegative
    precision: Confidence | None


class DecileLift(Contract):
    decile: Annotated[int, Field(ge=1, le=10)]
    businesses: NonNegative
    labeled: NonNegative
    positives: NonNegative
    rate: Confidence | None
    lift: Annotated[float, Field(ge=0)] | None


class CalibrationBand(Contract):
    low: Annotated[int, Field(ge=0, le=80)]
    high: Annotated[int, Field(ge=20, le=100)]
    businesses: NonNegative
    labeled: NonNegative
    mean_score: Points | None
    observed_rate: Confidence | None


class TierCohort(Contract):
    tier: LeadTier
    businesses: NonNegative
    labeled: NonNegative
    positives: NonNegative
    conversion: Confidence | None


class CalibrationReport(Contract):
    """Offline metrics of one run against the outcomes recorded after its facts were read. Nothing
    is trained; with few labels every number is a warning, not a verdict."""

    score_run: ScoreRunItem
    positive_stages: list[LeadStage]
    negative_stages: list[LeadStage]
    businesses: NonNegative
    labeled: NonNegative
    positives: NonNegative
    #: Outcomes recorded before the facts were read: left out to avoid leakage.
    leakage_excluded: NonNegative
    precision_at_k: list[PrecisionAtK]
    lift_by_decile: list[DecileLift]
    calibration: list[CalibrationBand]
    cohorts: list[TierCohort]
    #: Share of businesses the run could score (not excluded, not insufficient data).
    coverage: Confidence
    labeled_share: Confidence
    weights_are_heuristic: bool
    warnings: list[Code]


class LeadAction(StrEnum):
    """confirm or dismiss a score after reviewing it, reset that review, or record an outcome."""

    CONFIRM = "confirm"
    DISMISS = "dismiss"
    RESET = "reset"
    RECORD_OUTCOME = "record_outcome"
    #: Phase 12: an attempt to work the lead that keeps its stage (channel and result required).
    RECORD_ATTEMPT = "record_attempt"


class LeadActionRequest(Contract):
    score_run_id: Id
    business_id: Id
    action: LeadAction
    #: Required to record an outcome, refused otherwise.
    stage: LeadStage | None
    #: Required when the outcome is `lost`.
    lost_reason: Annotated[str, Field(min_length=1, max_length=500)] | None
    reason: ReviewReason
    #: Phase 12: how the lead was worked. `record_attempt` needs a channel and a result; an outcome
    #: may carry them too.
    channel: ContactType | None
    attempt_result: AttemptResult | None
    value_amount: Annotated[float, Field(ge=0, le=1_000_000_000)] | None
    value_currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] | None
    contact_quality: ContactQuality | None
    notes: Annotated[str, Field(min_length=1, max_length=4000)] | None
    #: When it happened; null means now. Never in the future.
    occurred_at: UtcTimestamp | None


# ---- Lead explorer, research workspace and sales briefs (phase 12) ------------------------------
# The analyst's experience over what the engines stored; nothing here computes a fact. The explorer
# reads `lead_index`, filtered by a bounded tree of conditions the store compiles to parameterised
# SQL; research decisions, checks, tasks, pins and corrections are audited history; a sales brief
# restates stored facts and inferences with their evidence, and a model may only reword it.

EXPLORER_MAX_DEPTH = 3
EXPLORER_MAX_CONDITIONS = 40
EXPLORER_MAX_CHILDREN = 20
EXPLORER_MAX_SORT = 4
EXPLORER_MAX_OFFSET = 5_000_000
EXPLORER_MAX_VALUES = 50
BULK_MAX_IDS = 1000
BULK_MAX_MATCHING = 5000
VIEW_SCHEMA_VERSION = 2
CHECKLIST_SCHEMA_VERSION = 1
#: Facts read longer ago than this are stale: shown as such, and never sales-ready.
STALE_AFTER_DAYS = 90
MAX_COMPARED = 4
ExplorerText = Annotated[str, Field(min_length=1, max_length=200)]


class ExplorerField(StrEnum):
    """What the explorer filters, sorts and shows. Live fields read the current state (stage,
    research, tags, suppression, age of the facts); the others are the run's snapshot."""

    NAME = "name"
    LOCALITY = "locality"
    REGION = "region"
    CATEGORY = "category"
    DOMAIN = "domain"
    LEAD_STAGE = "lead_stage"
    RESEARCH_STATUS = "research_status"
    ASSIGNEE = "assignee"
    TAG = "tag"
    TIER = "tier"
    RANK = "rank"
    TOTAL_SCORE = "total_score"
    CONFIDENCE = "confidence"
    DATA_COVERAGE = "data_coverage"
    ICP_MATCH = "icp_match"
    OPPORTUNITY_SCORE = "opportunity_score"
    DIGITAL_GAP = "digital_gap"
    BUSINESS_HEALTH = "business_health"
    BUYING_CAPACITY = "buying_capacity"
    CONTACTABILITY = "contactability"
    COMMERCIAL_INTENT = "commercial_intent"
    DATA_CONFIDENCE = "data_confidence"
    LATINO_STATUS = "latino_status"
    LATINO_CONFIDENCE = "latino_confidence"
    MAIN_CHANNEL = "main_channel"
    CHANNEL_STATUS = "channel_status"
    USABLE_CHANNELS = "usable_channels"
    PROBLEM = "problem"
    TOP_PROBLEM = "top_problem"
    OFFER = "offer"
    FACTS_AGE_DAYS = "facts_age_days"
    AUDIT_STATUS = "audit_status"
    CONFLICTS = "conflicts"
    SUPPRESSED = "suppressed"
    STALE = "stale"
    PARTIAL = "partial"
    #: Phase 16, filter only: the campaigns whose discovery found one of the lead's records.
    CAMPAIGN = "campaign"
    #: Phase 16, filter only: the kinds of published contact an export would write for the lead.
    REACHABLE_BY = "reachable_by"
    #: D-105, filter only: the leads the search named by the value repeats. A client is listed by
    #: the first search that found it; a later one repeats it when an earlier search found it, or
    #: a business on one of its phones, and when a better-ranked lead it found has its phone.
    REPEATED = "repeated"


#: Fields a lead is filtered by but never shows: no column, no sort, no export column.
EXPLORER_FILTER_ONLY: frozenset[ExplorerField] = frozenset(
    {ExplorerField.CAMPAIGN, ExplorerField.REACHABLE_BY, ExplorerField.REPEATED}
)


class ExplorerFieldType(StrEnum):
    TEXT = "text"
    NUMBER = "number"
    ENUM = "enum"
    FLAG = "flag"
    #: Several values per lead (tags, observed problems): a lead `has` one of them or not.
    SET = "set"


class ExplorerOperator(StrEnum):
    EQ = "eq"
    NEQ = "neq"
    IN = "in"
    NOT_IN = "not_in"
    LT = "lt"
    LTE = "lte"
    GT = "gt"
    GTE = "gte"
    CONTAINS = "contains"
    STARTS_WITH = "starts_with"
    IS_NULL = "is_null"
    IS_NOT_NULL = "is_not_null"
    IS_TRUE = "is_true"
    IS_FALSE = "is_false"
    HAS = "has"
    HAS_NOT = "has_not"


_OP = ExplorerOperator
EXPLORER_UNARY: frozenset[ExplorerOperator] = frozenset(
    {_OP.IS_NULL, _OP.IS_NOT_NULL, _OP.IS_TRUE, _OP.IS_FALSE}
)
_OPERATORS: dict[ExplorerFieldType, tuple[ExplorerOperator, ...]] = {
    ExplorerFieldType.TEXT: (
        _OP.EQ, _OP.NEQ, _OP.CONTAINS, _OP.STARTS_WITH, _OP.IN, _OP.NOT_IN, _OP.IS_NULL,
        _OP.IS_NOT_NULL,
    ),
    ExplorerFieldType.NUMBER: (
        _OP.EQ, _OP.NEQ, _OP.LT, _OP.LTE, _OP.GT, _OP.GTE, _OP.IS_NULL, _OP.IS_NOT_NULL,
    ),
    ExplorerFieldType.ENUM: (_OP.EQ, _OP.NEQ, _OP.IN, _OP.NOT_IN, _OP.IS_NULL, _OP.IS_NOT_NULL),
    ExplorerFieldType.FLAG: (_OP.IS_TRUE, _OP.IS_FALSE),
    ExplorerFieldType.SET: (_OP.HAS, _OP.HAS_NOT),
}  # fmt: skip


class ExplorerFieldInfo(Contract):
    field: ExplorerField
    type: ExplorerFieldType
    operators: list[ExplorerOperator]
    #: The values an enum or set field can take; empty for free text.
    values: list[str]
    sortable: bool
    #: Read from the current state rather than from the run's snapshot.
    live: bool


def _info(
    field: ExplorerField,
    kind: ExplorerFieldType,
    values: type[StrEnum] | None = None,
    *,
    live: bool = False,
) -> ExplorerFieldInfo:
    return ExplorerFieldInfo(
        field=field,
        type=kind,
        operators=list(_OPERATORS[kind]),
        values=[member.value for member in values] if values else [],
        sortable=kind is not ExplorerFieldType.SET,
        live=live,
    )


_F, _T = ExplorerField, ExplorerFieldType
EXPLORER_FIELDS: dict[ExplorerField, ExplorerFieldInfo] = {
    info.field: info
    for info in (
        _info(_F.NAME, _T.TEXT), _info(_F.LOCALITY, _T.TEXT), _info(_F.REGION, _T.TEXT),
        _info(_F.CATEGORY, _T.TEXT), _info(_F.DOMAIN, _T.TEXT),
        _info(_F.LEAD_STAGE, _T.ENUM, LeadStage, live=True),
        _info(_F.RESEARCH_STATUS, _T.ENUM, ResearchStatus, live=True),
        _info(_F.ASSIGNEE, _T.TEXT, live=True), _info(_F.TAG, _T.SET, live=True),
        _info(_F.TIER, _T.ENUM, LeadTier), _info(_F.RANK, _T.NUMBER),
        _info(_F.TOTAL_SCORE, _T.NUMBER), _info(_F.CONFIDENCE, _T.NUMBER),
        _info(_F.DATA_COVERAGE, _T.NUMBER), _info(_F.ICP_MATCH, _T.NUMBER),
        _info(_F.OPPORTUNITY_SCORE, _T.NUMBER), _info(_F.DIGITAL_GAP, _T.NUMBER),
        _info(_F.BUSINESS_HEALTH, _T.NUMBER), _info(_F.BUYING_CAPACITY, _T.NUMBER),
        _info(_F.CONTACTABILITY, _T.NUMBER), _info(_F.COMMERCIAL_INTENT, _T.NUMBER),
        _info(_F.DATA_CONFIDENCE, _T.NUMBER), _info(_F.LATINO_STATUS, _T.ENUM, LatinoStatus),
        _info(_F.LATINO_CONFIDENCE, _T.NUMBER), _info(_F.MAIN_CHANNEL, _T.ENUM, ContactType),
        _info(_F.CHANNEL_STATUS, _T.ENUM, ValidationStatus),
        _info(_F.USABLE_CHANNELS, _T.NUMBER), _info(_F.PROBLEM, _T.SET, ProblemType),
        _info(_F.TOP_PROBLEM, _T.ENUM, ProblemType), _info(_F.OFFER, _T.TEXT),
        _info(_F.FACTS_AGE_DAYS, _T.NUMBER, live=True),
        _info(_F.AUDIT_STATUS, _T.ENUM, WebsiteAuditStatus), _info(_F.CONFLICTS, _T.NUMBER),
        _info(_F.SUPPRESSED, _T.FLAG, live=True), _info(_F.STALE, _T.FLAG, live=True),
        _info(_F.PARTIAL, _T.FLAG), _info(_F.CAMPAIGN, _T.SET, live=True),
        _info(_F.REACHABLE_BY, _T.SET, ContactType, live=True),
        _info(_F.REPEATED, _T.SET, live=True),
    )
}  # fmt: skip

ExplorerValue = (
    ExplorerText | float | Annotated[list[ExplorerText], _between(1, EXPLORER_MAX_VALUES)] | None
)


class ExplorerCondition(Contract):
    """One test on one field. Unary operators take no value; `in` and `not_in` take a list; number
    fields compare with a number; enum and set fields accept only their own values."""

    kind: Literal["condition"]
    field: ExplorerField
    operator: ExplorerOperator
    value: ExplorerValue

    @model_validator(mode="after")
    def _fits_the_field(self) -> Self:
        info = EXPLORER_FIELDS[self.field]
        if self.operator not in info.operators:
            raise ValueError(f"'{self.operator}' does not apply to {self.field}")
        if self.operator in EXPLORER_UNARY:
            if self.value is not None:
                raise ValueError(f"'{self.operator}' takes no value")
            return self
        if self.operator in (_OP.IN, _OP.NOT_IN):
            if not isinstance(self.value, list):
                raise ValueError(f"'{self.operator}' takes a list of values")
            wanted = self.value
        elif info.type is ExplorerFieldType.NUMBER:
            if not isinstance(self.value, float):
                raise ValueError(f"{self.field} compares with a number")
            return self
        elif isinstance(self.value, str):
            wanted = [self.value]
        else:
            raise ValueError(f"{self.field} compares with text")
        if info.values and (unknown := [value for value in wanted if value not in info.values]):
            raise ValueError(f"{self.field} has no value {unknown[0]!r}")
        return self


class ExplorerGroup(Contract):
    """Conditions and groups joined by `and` or `or`, optionally negated. At most
    `EXPLORER_MAX_DEPTH` levels and `EXPLORER_MAX_CONDITIONS` conditions in the whole tree."""

    kind: Literal["group"]
    join: Literal["and", "or"]
    negate: bool
    children: Annotated[list["ExplorerNode"], _between(0, EXPLORER_MAX_CHILDREN)]

    @model_validator(mode="after")
    def _bounded(self) -> Self:
        if group_depth(self) > EXPLORER_MAX_DEPTH:
            raise ValueError(f"filters nest at most {EXPLORER_MAX_DEPTH} groups deep")
        if condition_count(self) > EXPLORER_MAX_CONDITIONS:
            raise ValueError(f"filters hold at most {EXPLORER_MAX_CONDITIONS} conditions")
        return self


ExplorerNode = Annotated[ExplorerCondition | ExplorerGroup, Field(discriminator="kind")]
ExplorerGroup.model_rebuild()


def group_depth(group: ExplorerGroup) -> int:
    nested = (group_depth(child) for child in group.children if isinstance(child, ExplorerGroup))
    return 1 + max(nested, default=0)


def condition_count(group: ExplorerGroup) -> int:
    return sum(
        condition_count(child) if isinstance(child, ExplorerGroup) else 1
        for child in group.children
    )


def no_filters() -> ExplorerGroup:
    return ExplorerGroup(kind="group", join="and", negate=False, children=[])


class SortDirection(StrEnum):
    ASC = "asc"
    DESC = "desc"


class ExplorerSort(Contract):
    """A sort key. Missing values always come last; the business id breaks the remaining ties."""

    field: ExplorerField
    direction: SortDirection

    @model_validator(mode="after")
    def _sortable(self) -> Self:
        if not EXPLORER_FIELDS[self.field].sortable:
            raise ValueError(f"{self.field} cannot be sorted")
        return self


def _unique_fields(fields: list[ExplorerField], what: str) -> None:
    if len(set(fields)) != len(fields):
        raise ValueError(f"a field appears twice in the {what}")


class ExplorerQuery(Contract):
    """One window of the explorer over a score run (the newest finished one when null): filters, a
    quick search, up to four sort keys (the run's rank order when none) and an offset."""

    score_run_id: Id | None
    filters: ExplorerGroup
    search: Annotated[str, Field(max_length=200)] | None
    sort: Annotated[list[ExplorerSort], _between(0, EXPLORER_MAX_SORT)]
    offset: Annotated[int, Field(ge=0, le=EXPLORER_MAX_OFFSET)]
    limit: Annotated[int, Field(ge=1, le=MAX_PAGE_SIZE)]
    #: Count every matching lead too: the first window of a query asks, later windows do not.
    with_total: bool

    @model_validator(mode="after")
    def _unique_sort(self) -> Self:
        _unique_fields([key.field for key in self.sort], "sort")
        return self


class PhoneTwin(Contract):
    """Another lead that publishes one of a lead's phones, in any project: calling both reaches
    one line (the same place listed twice, or one another folder's search found too)."""

    business_id: Id
    display_name: str
    #: Its project: the folder its searches sit in.
    project_name: str
    #: The searches that found it, by name; none when it came another way.
    searches: list[str]


class ExplorerRow(Contract):
    business_id: Id
    project_id: Id
    #: The place in the whole finished run, whatever the filters.
    rank: Annotated[int, Field(ge=1)] | None
    display_name: str
    locality: str | None
    region: str | None
    category: str | None
    domain: str | None
    lead_stage: LeadStage
    research_status: ResearchStatus | None
    assignee: str | None
    tags: list[str]
    tier: LeadTier
    total_score: Points | None
    confidence: Confidence
    data_coverage: Confidence
    icp_match: Points | None
    opportunity_score: Points | None
    digital_gap: Points | None
    #: An estimate from explicit proxies, as the scorecard labels it.
    business_health: Points | None
    #: An estimate from explicit proxies, never a financial claim.
    buying_capacity: Points | None
    contactability: Points | None
    commercial_intent: Points | None
    data_confidence: Points | None
    latino_status: LatinoStatus
    latino_confidence: Confidence | None
    main_channel: ContactType | None
    channel_status: ValidationStatus | None
    usable_channels: NonNegative
    problems: list[ProblemType]
    top_problem: ProblemType | None
    top_reason: str | None
    offer: str | None
    facts_collected_at: UtcTimestamp
    facts_age_days: NonNegative
    audit_status: WebsiteAuditStatus | None
    conflicts: NonNegative
    #: On the do-not-contact list now, not only when it was scored.
    suppressed: bool
    #: Facts read more than `STALE_AFTER_DAYS` ago.
    stale: bool
    #: The score could not read everything: insufficient data, or a partial or empty audit.
    partial: bool
    is_synthetic: bool
    #: The searches that found it, by name: more than one when another search found it again.
    searches: list[str]
    #: Other leads on one of its usable phones, by name, up to `explorer.MAX_TWINS_SHOWN`.
    same_phone: list[PhoneTwin]


class ExplorerPage(Contract):
    score_run: ScoreRunItem | None
    offset: NonNegative
    rows: list[ExplorerRow]
    #: Every matching lead, when the query asked for it.
    total: NonNegative | None
    generated_at: UtcTimestamp


class ViewColumn(Contract):
    field: ExplorerField
    visible: bool
    pinned: bool
    width: Annotated[int, Field(ge=48, le=800)] | None


class ViewDefinition(Contract):
    """Schema version 2 of a saved view. Version 1 (flat `and` conditions, one sort key, the visible
    columns only) is upgraded when read, never rewritten."""

    schema_version: Literal[2]
    score_run_id: Id | None
    filters: ExplorerGroup
    search: Annotated[str, Field(max_length=200)] | None
    sort: Annotated[list[ExplorerSort], _between(0, EXPLORER_MAX_SORT)]
    columns: Annotated[list[ViewColumn], _between(1, len(ExplorerField))]

    @model_validator(mode="after")
    def _unique(self) -> Self:
        _unique_fields([key.field for key in self.sort], "sort")
        _unique_fields([column.field for column in self.columns], "columns")
        return self


class ExplorerCatalogQuery(Contract):
    scope: Literal["lead_explorer"]


class ExplorerCatalog(Contract):
    fields: list[ExplorerFieldInfo]
    max_depth: int
    max_conditions: int
    max_children: int
    max_sort: int
    max_page: int
    max_offset: int
    bulk_max_ids: int
    bulk_max_matching: int
    stale_after_days: int
    max_compared: int
    view_schema_version: int
    default_view: ViewDefinition


class SavedViewQuery(Contract):
    scope: Literal["lead_explorer"]
    include_archived: bool


class SavedViewItem(Contract):
    view: SavedView
    #: Newest first, at most 20.
    versions: list[SavedViewVersion]
    #: The newest definition, upgraded to the current schema when it was older.
    definition: ViewDefinition
    upgraded_from: Annotated[int, Field(ge=1)] | None


class SavedViewList(Contract):
    items: list[SavedViewItem]
    schema_version: int


class SavedViewSaveRequest(Contract):
    """Creates a view, or a new version of one; an unchanged definition keeps the newest version."""

    view_id: Id | None
    scope: Literal["lead_explorer"]
    name: Annotated[str, Field(min_length=1, max_length=120)]
    description: Annotated[str, Field(max_length=2000)] | None
    definition: ViewDefinition


class SavedViewAction(StrEnum):
    ARCHIVE = "archive"
    RESTORE = "restore"


class SavedViewChangeRequest(Contract):
    view_id: Id
    action: SavedViewAction
    reason: ReviewReason


class ExportPreviewRequest(Contract):
    """What an export of the explorer's query would hold. Nothing is written (exports: phase 13)."""

    query: ExplorerQuery
    columns: Annotated[list[ExplorerField], _between(1, len(ExplorerField))]
    include_suppressed: bool
    sample_size: Annotated[int, Field(ge=1, le=50)]


class ExportPreview(Contract):
    score_run: ScoreRunItem | None
    matching: NonNegative
    #: Suppressed leads left out (zero when they were included on purpose).
    excluded_suppressed: NonNegative
    exported: NonNegative
    columns: list[ExplorerField]
    sample: list[ExplorerRow]
    #: `suppressed_included`, `stale_rows`, `partial_rows`, `hypotheses_never_exported`...
    warnings: list[Code]
    generated_at: UtcTimestamp


class BulkAction(StrEnum):
    """Safe actions over many leads. `sales_ready` needs its criteria per lead: never in bulk."""

    ADD_TAG = "add_tag"
    REMOVE_TAG = "remove_tag"
    ASSIGN = "assign"
    UNASSIGN = "unassign"
    NEEDS_RESEARCH = "needs_research"
    DISQUALIFY = "disqualify"


class BulkMode(StrEnum):
    PREVIEW = "preview"
    APPLY = "apply"


class BulkRequest(Contract):
    """One action over leads chosen by id (at most 1 000), or over every lead a query matches (at
    most 5 000, applied only while that is still the count the analyst previewed)."""

    mode: BulkMode
    action: BulkAction
    business_ids: Annotated[list[Id], _between(0, BULK_MAX_IDS)]
    query: ExplorerQuery | None
    expected: NonNegative | None
    tag: Annotated[str, Field(min_length=1, max_length=64)] | None
    assignee: Annotated[str, Field(min_length=1, max_length=120)] | None
    reason: ReviewReason | None

    @model_validator(mode="after")
    def _complete(self) -> Self:
        if bool(self.business_ids) == (self.query is not None):
            raise ValueError("choose leads by id or by query, not both")
        if self.action in (BulkAction.ADD_TAG, BulkAction.REMOVE_TAG) and self.tag is None:
            raise ValueError(f"{self.action} needs a tag")
        if self.action is BulkAction.ASSIGN and self.assignee is None:
            raise ValueError("assign needs an assignee")
        if self.mode is BulkMode.APPLY and self.reason is None:
            raise ValueError("an applied bulk action needs a reason")
        if self.mode is BulkMode.APPLY and self.query is not None and self.expected is None:
            raise ValueError("an apply over a query needs the count that was previewed")
        return self


class BulkSkip(Contract):
    business_id: Id
    display_name: str
    #: `unchanged`, `suppressed`, `not_found`.
    reason: Code


class BulkResult(Contract):
    mode: BulkMode
    action: BulkAction
    targeted: NonNegative
    #: Changed (apply) or that would change (preview).
    changed: NonNegative
    skipped: NonNegative
    #: The first 100 skipped leads and why.
    skips: list[BulkSkip]
    batch_id: Id | None


class LeadCompareQuery(Contract):
    business_ids: Annotated[list[Id], _between(2, MAX_COMPARED)]
    score_run_id: Id | None


class ComparedLead(Contract):
    row: ExplorerRow
    scorecard: LeadScorecard


class LeadComparison(Contract):
    score_run: ScoreRunItem
    leads: list[ComparedLead]
    #: Asked for, but not scored by that run.
    missing: list[Id]


class SalesReadyCriterion(StrEnum):
    """Every one must hold before a lead can be marked sales-ready (docs/lead-explorer.md)."""

    NOT_SUPPRESSED = "not_suppressed"
    ACTIVE_BUSINESS = "active_business"
    SCORED_RECENTLY = "scored_recently"
    QUALIFYING_TIER = "qualifying_tier"
    VERIFIED_OPPORTUNITY = "verified_opportunity"
    USABLE_CONTACT = "usable_contact"
    CONTACT_CONFLICTS_RESOLVED = "contact_conflicts_resolved"
    CHECKLIST_COMPLETE = "checklist_complete"


class ChecklistItemKind(StrEnum):
    AUTOMATIC = "automatic"
    MANUAL = "manual"


class ChecklistRule(StrEnum):
    """What an automatic checklist item reads from the stored facts."""

    SCORED_RECENTLY = "scored_recently"
    QUALIFYING_TIER = "qualifying_tier"
    VERIFIED_OPPORTUNITY = "verified_opportunity"
    USABLE_CONTACT = "usable_contact"
    CONTACT_CONFLICTS_RESOLVED = "contact_conflicts_resolved"
    DECISION_MAKER_PUBLISHED = "decision_maker_published"
    WEBSITE_AUDITED = "website_audited"
    LATINO_EVALUATED = "latino_evaluated"


class ChecklistItem(Contract):
    key: Code
    label: Label
    kind: ChecklistItemKind
    rule: ChecklistRule | None
    required: bool

    @model_validator(mode="after")
    def _rule_when_automatic(self) -> Self:
        if (self.kind is ChecklistItemKind.AUTOMATIC) != (self.rule is not None):
            raise ValueError("an automatic item names its rule, and only an automatic item does")
        return self


class ChecklistDefinition(Contract):
    """A checklist applies to the businesses of its campaigns first, then of its categories (whole
    words); with neither it applies to every lead no other checklist claims."""

    schema_version: Literal[1]
    campaign_ids: Annotated[list[Id], _between(0, 50)]
    categories: Terms
    items: Annotated[list[ChecklistItem], _between(1, 30)]

    @model_validator(mode="after")
    def _unique_keys(self) -> Self:
        keys = [item.key for item in self.items]
        if len(set(keys)) != len(keys):
            raise ValueError("an item key appears twice")
        return self


class ChecklistCatalogQuery(Contract):
    include_archived: bool


class ChecklistView(Contract):
    checklist: ResearchChecklist
    versions: list[ResearchChecklistVersion]


class ChecklistCatalog(Contract):
    checklists: list[ChecklistView]
    template: ChecklistDefinition


class ChecklistSaveRequest(Contract):
    checklist_id: Id | None
    name: Annotated[str, Field(min_length=1, max_length=120)]
    description: Annotated[str, Field(max_length=2000)] | None
    definition: ChecklistDefinition


class ChecklistChangeRequest(Contract):
    checklist_id: Id
    action: ScoringChangeAction
    version_id: Id | None
    reason: ReviewReason


class CriterionCheck(Contract):
    criterion: SalesReadyCriterion
    passed: bool
    detail: ScoreText


class ChecklistItemState(Contract):
    item: ChecklistItem
    done: bool
    #: Why an automatic item holds or not; the note of a manual one.
    detail: ScoreText
    decided_by: str | None
    decided_at: UtcTimestamp | None


class ChecklistState(Contract):
    checklist_id: Id
    checklist_version_id: Id
    name: str
    version: Annotated[int, Field(ge=1)]
    #: `campaign`, `category` or `default`.
    matched_by: Code
    items: list[ChecklistItemState]
    #: Every required item is done.
    complete: bool


class PinView(Contract):
    pin: EvidencePin
    #: The pinned row still exists and belongs to this business.
    available: bool


class CorrectionView(Contract):
    """A value an analyst corrected: a manual observation with its reason, never an edited
    source."""

    field: CanonicalField
    value: str
    observation_id: Id
    reason: str
    actor: str
    corrected_at: UtcTimestamp
    #: Still the value the business shows.
    shown: bool


class ResearchWorkspace(Contract):
    business_id: Id
    project_id: Id
    display_name: str
    is_synthetic: bool
    #: The newest finished run that scored the business, read by the criteria.
    score_run_id: Id | None
    lead_stage: LeadStage
    research: LeadResearch | None
    #: The last status change can be undone back to the status before it.
    can_undo: bool
    suppressed: bool
    sales_ready: list[CriterionCheck]
    sales_ready_allowed: bool
    checklist: ChecklistState
    tasks: list[ResearchTask]
    pins: list[PinView]
    notes: list[Note]
    tags: list[str]
    corrections: list[CorrectionView]
    decisions: list[ReviewDecision]


class ResearchWorkspaceQuery(Contract):
    business_id: Id


class RefreshScope(StrEnum):
    """What a refresh asks the engines for: the website pages (a crawl), the published contacts, or
    the Latino evidence. Each one starts its job; nothing is sent to the business."""

    PAGES = "pages"
    CONTACTS = "contacts"
    EVIDENCE = "evidence"


class ResearchAction(StrEnum):
    SET_STATUS = "set_status"
    UNDO_STATUS = "undo_status"
    ASSIGN = "assign"
    UNASSIGN = "unassign"
    ADD_TAG = "add_tag"
    REMOVE_TAG = "remove_tag"
    ADD_NOTE = "add_note"
    DELETE_NOTE = "delete_note"
    PIN = "pin"
    UNPIN = "unpin"
    ADD_TASK = "add_task"
    COMPLETE_TASK = "complete_task"
    DISMISS_TASK = "dismiss_task"
    REOPEN_TASK = "reopen_task"
    SYNC_TASKS = "sync_tasks"
    CHECK_ITEM = "check_item"
    UNCHECK_ITEM = "uncheck_item"
    CORRECT_VALUE = "correct_value"
    REQUEST_REFRESH = "request_refresh"


_ACTION_NEEDS: dict[ResearchAction, tuple[str, ...]] = {
    ResearchAction.SET_STATUS: ("status", "reason"),
    ResearchAction.UNDO_STATUS: ("reason",),
    ResearchAction.ASSIGN: ("assignee",),
    ResearchAction.ADD_TAG: ("tag",),
    ResearchAction.REMOVE_TAG: ("tag",),
    ResearchAction.ADD_NOTE: ("note",),
    ResearchAction.DELETE_NOTE: ("note_id", "reason"),
    ResearchAction.PIN: ("ref_kind", "ref"),
    ResearchAction.UNPIN: ("ref_kind", "ref", "reason"),
    ResearchAction.ADD_TASK: ("title",),
    ResearchAction.COMPLETE_TASK: ("task_id",),
    ResearchAction.DISMISS_TASK: ("task_id", "reason"),
    ResearchAction.REOPEN_TASK: ("task_id",),
    ResearchAction.CHECK_ITEM: ("item_key",),
    ResearchAction.UNCHECK_ITEM: ("item_key", "reason"),
    ResearchAction.CORRECT_VALUE: ("field", "value", "reason"),
}


class ResearchActionRequest(Contract):
    """One change to a lead's research. Everything that removes or overrides something needs a
    reason; a status, a correction and a refresh are audited."""

    business_id: Id
    action: ResearchAction
    status: ResearchStatus | None
    assignee: Annotated[str, Field(min_length=1, max_length=120)] | None
    tag: Annotated[str, Field(min_length=1, max_length=64)] | None
    #: A note's body, or the short note of a pin or a checked item (at most 1 000 characters there).
    note: Annotated[str, Field(min_length=1, max_length=4000)] | None
    note_id: Id | None
    ref_kind: EvidenceRefKind | None
    ref: Annotated[str, Field(min_length=1, max_length=400)] | None
    task_id: Id | None
    title: Label | None
    item_key: Code | None
    field: CanonicalField | None
    value: Annotated[str, Field(min_length=1, max_length=500)] | None
    scopes: Annotated[list[RefreshScope], _between(0, len(RefreshScope))]
    reason: ReviewReason | None

    @model_validator(mode="after")
    def _fields_of_the_action(self) -> Self:
        missing = [
            name for name in _ACTION_NEEDS.get(self.action, ()) if getattr(self, name) is None
        ]
        if missing:
            raise ValueError(f"{self.action} needs {', '.join(missing)}")
        if self.action is ResearchAction.REQUEST_REFRESH and not self.scopes:
            raise ValueError("choose what to refresh")
        short = self.action in (ResearchAction.PIN, ResearchAction.CHECK_ITEM)
        if short and self.note is not None and len(self.note) > 1000:
            raise ValueError("a pin or a checked item keeps at most 1000 characters of note")
        return self


class BriefRef(Contract):
    """A stored row a brief, a pin or a timeline entry points at."""

    kind: EvidenceRefKind
    ref: Annotated[str, Field(min_length=1, max_length=400)]
    label: Label
    url: Annotated[str, Field(max_length=2048)] | None


class TimelineKind(StrEnum):
    SOURCE = "source"
    CHANGE = "change"
    ANALYSIS = "analysis"
    REVIEW = "review"
    RESEARCH = "research"
    OUTCOME = "outcome"


class TimelineQuery(Contract):
    business_id: Id
    #: Empty: every kind.
    kinds: Annotated[list[TimelineKind], _between(0, len(TimelineKind))]
    limit: PageSize
    cursor: Cursor | None


class TimelineEntry(Contract):
    #: `<table>:<id>`, unique in a timeline.
    key: Annotated[str, Field(min_length=1, max_length=120)]
    at: UtcTimestamp
    kind: TimelineKind
    code: Code
    title: ScoreText
    detail: Annotated[str, Field(max_length=600)] | None
    actor: str | None
    ref: BriefRef | None
    job_id: Id | None
    score_run_id: Id | None
    source_key: Code | None


class TimelinePage(Contract):
    business_id: Id
    entries: list[TimelineEntry]
    next_cursor: Cursor | None


class EvidenceQuery(Contract):
    business_id: Id
    kind: EvidenceRefKind
    ref: Annotated[str, Field(min_length=1, max_length=400)]


class EvidenceDetail(Contract):
    """One stored row behind a claim, looked up only within the business it belongs to."""

    business_id: Id
    kind: EvidenceRefKind
    ref: str
    label: str
    #: What the row says: a finding's observed value, an evidence snippet, a channel's value...
    excerpt: Annotated[str, Field(max_length=2000)] | None
    #: Its state: a finding result, a validation status, an assessment status...
    status: Code | None
    confidence: Confidence | None
    observed_at: UtcTimestamp | None
    source_name: str | None
    source_url: Annotated[str, Field(max_length=2048)] | None
    provenance: Provenance | None


class BriefSection(StrEnum):
    BUSINESS_SNAPSHOT = "business_snapshot"
    ICP_MATCH = "icp_match"
    VERIFIED_OPPORTUNITY = "verified_opportunity"
    EVIDENCE = "evidence"
    RECOMMENDED_OFFER = "recommended_offer"
    CONTACT_ROUTE = "contact_route"
    DECISION_MAKER = "decision_maker"
    CONVERSATION_HOOKS = "conversation_hooks"
    RISKS_UNKNOWNS = "risks_unknowns"
    LAST_VERIFIED = "last_verified"


class BriefClaim(Contract):
    """One statement of a brief, restating stored data. `ref` (B1, B2...) is what a model cites;
    `is_inference` marks a probable consequence, never a fact."""

    ref: EvidenceRef
    text: Annotated[str, Field(min_length=1, max_length=400)]
    is_inference: bool
    evidence: Annotated[list[BriefRef], _between(1, 8)]


class BriefSectionView(Contract):
    section: BriefSection
    claims: list[BriefClaim]
    #: Why a section holds no claim: `no_decision_maker_published`, `no_usable_contact`...
    empty_reason: Code | None


class SalesBriefQuery(Contract):
    business_id: Id
    score_run_id: Id | None


class BriefDraftSentence(Contract):
    text: str
    #: The brief claims the sentence restates, all still in this brief.
    claims: list[EvidenceRef]
    evidence: list[BriefRef]


class BriefDraftSection(Contract):
    section: BriefSection
    sentences: list[BriefDraftSentence]


class BriefDraft(Contract):
    """The newest valid, not rejected `sales_brief` answer, reduced to sentences whose every cited
    claim is still in the brief; the others are dropped and counted."""

    inference_run_id: Id
    model: str
    review_status: AiReviewStatus
    finished_at: UtcTimestamp | None
    sections: list[BriefDraftSection]
    dropped: NonNegative


class SalesBrief(Contract):
    business_id: Id
    project_id: Id
    display_name: str
    score_run_id: Id | None
    versions: ScoreVersions | None
    generated_at: UtcTimestamp
    #: The newest date the facts behind the brief were read or checked.
    last_verified_at: UtcTimestamp | None
    suppressed: bool
    research_status: ResearchStatus | None
    is_synthetic: bool
    #: Every section in order; an empty one says why.
    sections: list[BriefSectionView]
    draft: BriefDraft | None


class SalesBriefSectionOutput(Contract):
    section: BriefSection
    sentences: Annotated[list[CitedPoint], Field(max_length=4)]


class SalesBriefOutput(Contract):
    """A model's wording of a brief: sentences per section, each citing the brief claims it
    restates. It adds no fact, number, person or accusation."""

    outcome: AiOutcome
    sections: Annotated[list[SalesBriefSectionOutput], Field(max_length=10)]


# ---- operations: pipelines, events, datasets and exports (phase 13) -----------------------------


class PipelineStage(StrEnum):
    """The stages of a campaign pipeline, in order. Each runs the engine job of its phase."""

    DISCOVERY = "discovery"
    ACQUISITION = "acquisition"
    NORMALIZATION = "normalization"
    ENTITY_RESOLUTION = "entity_resolution"
    LATINO_EVIDENCE = "latino_evidence"
    WEBSITE_INTELLIGENCE = "website_intelligence"
    CONTACT_INTELLIGENCE = "contact_intelligence"
    LOCAL_AI = "local_ai"
    SCORING = "scoring"
    EXPORT_ELIGIBILITY = "review_export_eligibility"


class PipelineResource(StrEnum):
    HTTP = "http"
    BROWSER = "browser"
    CPU = "cpu"
    RAM = "ram"
    DISK = "disk"
    GPU = "gpu"


IdempotencyKey = Annotated[str, Field(min_length=8, max_length=120, pattern=r"^[A-Za-z0-9_.:-]+$")]


class PipelineStartRequest(Contract):
    """Runs every stage over one project; a campaign adds discovery and acquisition. A repeated key
    (a double click, a retry after a timeout) returns the pipeline it already started."""

    project_id: Id
    campaign_id: Id | None
    check_mail_domains: bool
    priority: Annotated[int, Field(ge=-100, le=100)]
    idempotency_key: IdempotencyKey


class DeadLetterAction(StrEnum):
    #: Queue the stage again as a new attempt.
    RETRY = "retry"
    #: Go on without it: the stage counts as done with nothing produced.
    SKIP = "skip"


class DeadLetterRequest(Contract):
    job_id: Id
    task_id: Id
    action: DeadLetterAction
    reason: ReviewReason


class PipelineListQuery(Contract):
    project_id: Id | None
    limit: PageSize
    cursor: Cursor | None


class ChildJob(Contract):
    """An engine job a stage started, or attached because it was already open for the project."""

    job_id: Id
    kind: Code
    status: JobStatus
    reused: bool
    progress_done: NonNegative
    progress_total: NonNegative | None
    started_at: UtcTimestamp | None
    finished_at: UtcTimestamp | None
    #: Items per minute since it started: an estimate, null until there is progress.
    rate_per_minute: Annotated[float, Field(ge=0)] | None
    #: Seconds left at that rate: an estimate, null when unknown.
    eta_seconds: NonNegative | None
    #: The stats the engine wrote with its last checkpoint.
    stats: JsonObject | None


class PipelineAttempt(Contract):
    attempt: Annotated[int, Field(ge=1)]
    status: JobStatus
    started_at: UtcTimestamp
    finished_at: UtcTimestamp | None
    error_code: Code | None
    error_message: str | None
    retryable: bool | None
    jobs: list[ChildJob]


class PipelineStageView(Contract):
    task_id: Id
    stage: PipelineStage
    ordinal: NonNegative
    version: Label
    inputs: list[Code]
    outputs: list[Code]
    depends_on: list[PipelineStage]
    resources: list[PipelineResource]
    max_attempts: Annotated[int, Field(ge=1)]
    retryable_codes: list[Code]
    status: JobStatus
    #: Why the stage had nothing to do (`no_campaign`, `no_businesses`...); it counts as done.
    skipped: Code | None
    attempts: list[PipelineAttempt]
    #: Warnings and errors of its engine jobs, newest first (at most 20).
    events: list[RunEvent]


class DeadLetterStatus(StrEnum):
    OPEN = "open"
    RETRIED = "retried"
    SKIPPED = "skipped"


class DeadLetter(Contract):
    id: Id
    task_id: Id
    stage: PipelineStage
    attempt: Annotated[int, Field(ge=1)]
    error_code: Code
    error_message: str
    retryable: bool
    review_status: DeadLetterStatus
    created_at: UtcTimestamp
    resolved_at: UtcTimestamp | None
    resolved_by: str | None
    resolution_reason: str | None


class StageCounts(Contract):
    queued: NonNegative
    running: NonNegative
    paused: NonNegative
    succeeded: NonNegative
    skipped: NonNegative
    failed: NonNegative
    cancelled: NonNegative


class PipelineRun(Contract):
    job_id: Id
    project_id: Id
    project_name: Label
    campaign_id: Id | None
    campaign_name: Label | None
    status: JobStatus
    priority: int
    check_mail_domains: bool
    #: Stages done (succeeded or skipped) out of every stage.
    progress_done: NonNegative
    progress_total: NonNegative
    counts: StageCounts
    requested_at: UtcTimestamp
    started_at: UtcTimestamp | None
    finished_at: UtcTimestamp | None
    stages: list[PipelineStageView]
    dead_letters: list[DeadLetter]
    #: What the eligibility stage produced: the score run read, the leads eligible to export
    #: (priority or review, not on the do-not-contact list) and the snapshot written.
    score_run_id: Id | None
    eligible_leads: NonNegative | None
    snapshot_id: Id | None
    #: The controls the pipeline's status allows now.
    actions: list[RunControlAction]
    is_synthetic: bool


class ResourceSlot(Contract):
    resource: PipelineResource
    running: NonNegative
    limit: NonNegative


class ResourceUsage(Contract):
    """This machine now, as the orchestrator sees it. Null means the OS did not say."""

    sampled_at: UtcTimestamp
    cpu_count: NonNegative
    cpu_percent: Annotated[float, Field(ge=0, le=100)] | None
    ram_total_mib: NonNegative | None
    ram_free_mib: NonNegative | None
    vram_total_mib: NonNegative | None
    vram_free_mib: NonNegative | None
    disk_total_mib: NonNegative | None
    disk_free_mib: NonNegative | None
    active_browsers: NonNegative | None
    slots: list[ResourceSlot]
    #: `low_ram`, `low_vram`, `low_disk`: stages that need the resource wait until it clears.
    pressure: list[Code]


class PipelinePage(Contract):
    items: list[PipelineRun]
    next_cursor: Cursor | None
    total: NonNegative
    resources: ResourceUsage


class JobEventQuery(Contract):
    """The persisted log. A pipeline's events include those of its engine jobs."""

    job_id: Id | None
    task_id: Id | None
    business_id: Id | None
    request_id: Id | None
    levels: Annotated[list[EventLevel], _between(0, len(EventLevel))]
    limit: PageSize
    cursor: Cursor | None


class JobEventItem(Contract):
    event: JobEvent
    job_kind: Code
    project_id: Id
    stage: Code | None


class JobEventPage(Contract):
    items: list[JobEventItem]
    next_cursor: Cursor | None
    total: NonNegative


class LevelCount(Contract):
    level: EventLevel
    count: NonNegative


class EventCleanupRequest(Contract):
    """Deletes old events of finished jobs. Errors are never deleted: they are the root causes."""

    mode: BulkMode
    older_than_days: Annotated[int, Field(ge=1, le=3650)]
    levels: Annotated[list[EventLevel], _between(1, 3)]
    reason: ReviewReason | None

    @model_validator(mode="after")
    def _safe(self) -> Self:
        if EventLevel.ERROR in self.levels:
            raise ValueError("error events are kept: they explain what failed")
        if len(set(self.levels)) != len(self.levels):
            raise ValueError("a level appears twice")
        if self.mode is BulkMode.APPLY and self.reason is None:
            raise ValueError("deleting events needs a reason")
        return self


class EventCleanupResult(Contract):
    mode: BulkMode
    cutoff: UtcTimestamp
    matched: NonNegative
    deleted: NonNegative
    by_level: list[LevelCount]
    kept_errors: NonNegative


class DatasetSnapshotRequest(Contract):
    """Captures a project as the next version of a dataset: a consistent read at one moment. The
    rows carry the tiers and scores of `score_run_id`, or of the newest finished run when null."""

    project_id: Id
    dataset_name: Annotated[str, Field(min_length=1, max_length=300)]
    description: Annotated[str, Field(max_length=2000)] | None
    score_run_id: Id | None


class FieldCoverage(Contract):
    field: Code
    known: NonNegative
    unknown: NonNegative
    share: Confidence


class ValueCount(Contract):
    value: str
    count: NonNegative


class DatasetQuality(Contract):
    """Computed from the snapshot's Parquet copy when it is written; never estimated."""

    schema_version: Annotated[int, Field(ge=1)]
    rows: NonNegative
    duplicates: NonNegative
    coverage: list[FieldCoverage]
    statuses: list[ValueCount]
    tiers: list[ValueCount]
    #: Leads per total score band (0-9, 10-19 ... 90-100); unscored rows are counted apart.
    score_bins: list[NonNegative]
    unscored: NonNegative
    suppressed: NonNegative
    oldest_seen_at: UtcTimestamp | None
    newest_seen_at: UtcTimestamp | None
    #: Rows last seen more than STALE_AFTER_DAYS before the cut-off.
    stale_rows: NonNegative
    errors: list[str]


class LineageKind(StrEnum):
    SOURCE_RUN = "source_run"
    SCORE_RUN = "score_run"
    PARENT_SNAPSHOT = "parent_snapshot"


class LineageItem(Contract):
    kind: LineageKind
    id: Id
    label: str
    at: UtcTimestamp | None


class DatasetSnapshotItem(Contract):
    snapshot: DatasetSnapshot
    dataset_name: Label
    project_id: Id
    lineage_count: NonNegative
    export_count: NonNegative
    is_synthetic: bool


class DatasetSnapshotListQuery(Contract):
    dataset_id: Id
    limit: PageSize
    cursor: Cursor | None


class DatasetSnapshotPage(Contract):
    items: list[DatasetSnapshotItem]
    next_cursor: Cursor | None
    total: NonNegative


class DatasetSnapshotQuery(Contract):
    snapshot_id: Id


class DatasetSnapshotDetail(Contract):
    item: DatasetSnapshotItem
    #: Schema 1 snapshots have no stored report; theirs is computed from the Parquet copy.
    quality: DatasetQuality
    lineage: list[LineageItem]
    #: The newest 20 exports of this snapshot.
    exports: list[Export]


class DatasetDiffQuery(Contract):
    before_snapshot_id: Id
    after_snapshot_id: Id
    limit: Annotated[int, Field(ge=1, le=500)]

    @model_validator(mode="after")
    def _two(self) -> Self:
        if self.before_snapshot_id == self.after_snapshot_id:
            raise ValueError("compare two different snapshots")
        return self


class DiffRow(Contract):
    business_id: Id
    display_name: str


class ChangedRow(Contract):
    business_id: Id
    display_name: str
    #: The compared columns whose values differ: the reason the row changed.
    fields: list[Code]


class DatasetDiff(Contract):
    before: DatasetSnapshotItem
    after: DatasetSnapshotItem
    added_count: NonNegative
    removed_count: NonNegative
    changed_count: NonNegative
    unchanged_count: NonNegative
    #: The first `limit` rows of each list, by business id.
    added: list[DiffRow]
    removed: list[DiffRow]
    changed: list[ChangedRow]
    #: The columns both snapshots hold, compared value by value.
    compared_fields: list[Code]
    truncated: bool


class DatasetCleanupRequest(Contract):
    """Deletes old snapshot versions of a dataset, keeping the newest `keep_latest` and whatever
    lineage or exports still need. Preview first; applying needs a reason."""

    dataset_id: Id
    keep_latest: Annotated[int, Field(ge=1, le=100)]
    mode: BulkMode
    reason: ReviewReason | None

    @model_validator(mode="after")
    def _reason(self) -> Self:
        if self.mode is BulkMode.APPLY and self.reason is None:
            raise ValueError("deleting snapshots needs a reason")
        return self


class CleanupSnapshot(Contract):
    snapshot_id: Id
    version: Annotated[int, Field(ge=1)]
    row_count: NonNegative
    bytes: NonNegative
    #: Why a snapshot is kept: `latest`, `parent_of_kept`, `exported`.
    kept_because: Code | None


class DatasetCleanupResult(Contract):
    mode: BulkMode
    deleted: list[CleanupSnapshot]
    kept: list[CleanupSnapshot]
    freed_bytes: NonNegative


class ExportSourceKind(StrEnum):
    #: The leads a Lead Explorer query matches now.
    QUERY = "query"
    #: The rows of a dataset snapshot, as they were at its cut-off.
    SNAPSHOT = "snapshot"


MAX_EXPORT_LABELS = 50


class ExportColumn(Contract):
    #: An explorer field for a query export, a snapshot column for a snapshot export.
    key: Code
    header: Annotated[str, Field(min_length=1, max_length=64)]
    #: Phase 16: the words written instead of the codes the column holds (a tier, a problem, an
    #: offer), in the language of the screen that asked; a code without one is written as it is.
    #: The only defaulted field of a contract: presets and drafts stored before it stay readable.
    labels: Annotated[dict[Code, Label], Field(max_length=MAX_EXPORT_LABELS)] | None = None


MAX_EXPORT_COLUMNS = 64


def _unique_columns(columns: list[ExportColumn]) -> None:
    if len({column.key for column in columns}) != len(columns):
        raise ValueError("a column appears twice")
    if len({column.header.casefold() for column in columns}) != len(columns):
        raise ValueError("two columns have the same header")


class ExportDefinition(Contract):
    """What a preset fixes: the source kind, the format, the columns and their headers."""

    source_kind: ExportSourceKind
    format: ExportFormat
    columns: Annotated[list[ExportColumn], _between(1, MAX_EXPORT_COLUMNS)]
    #: CSV only: start the file with a UTF-8 byte order mark (Excel reads accents with it).
    bom: bool

    @model_validator(mode="after")
    def _columns(self) -> Self:
        _unique_columns(self.columns)
        return self


class ExportDraft(Contract):
    """Everything an export needs but its destination, which the desktop core adds from the save
    dialog: the interface never names a path."""

    source_kind: ExportSourceKind
    query: ExplorerQuery | None
    #: The saved view the query came from, recorded in the manifest.
    view_id: Id | None
    snapshot_id: Id | None
    format: ExportFormat
    columns: Annotated[list[ExportColumn], _between(1, MAX_EXPORT_COLUMNS)]
    bom: bool
    #: Leads on the do-not-contact list stay out unless included on purpose, with a reason.
    include_suppressed: bool
    suppressed_reason: ReviewReason | None
    preset_key: Code | None
    preset_version: Annotated[int, Field(ge=1)] | None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        _unique_columns(self.columns)
        if self.source_kind is ExportSourceKind.QUERY:
            if self.query is None or self.snapshot_id is not None:
                raise ValueError("a query export names its query and no snapshot")
        elif self.snapshot_id is None or self.query is not None or self.view_id is not None:
            raise ValueError("a snapshot export names its snapshot and no query")
        if self.include_suppressed != (self.suppressed_reason is not None):
            raise ValueError("including do-not-contact leads needs a reason, and only then")
        if (self.preset_key is None) != (self.preset_version is None):
            raise ValueError("a preset is named with its version")
        return self


class ExportStartRequest(ExportDraft):
    #: An absolute path the save dialog returned; the worker writes `<destination>.partial` first.
    destination: Annotated[str, Field(min_length=3, max_length=1024)]


class ExportColumnType(StrEnum):
    TEXT = "text"
    NUMBER = "number"
    FLAG = "flag"
    TIMESTAMP = "timestamp"
    LIST = "list"


class ExportColumnInfo(Contract):
    source_kind: ExportSourceKind
    key: Code
    type: ExportColumnType
    #: What the value means and where it comes from; shown next to the column when choosing it.
    description: str


class ExportPresetView(Contract):
    preset: ExportPreset
    definition: ExportDefinition
    #: Every version of this key, newest first.
    versions: list[Annotated[int, Field(ge=1)]]
    cloned_from_key: Code | None


class ExportCatalogQuery(Contract):
    include_superseded: bool


class ExportCatalog(Contract):
    presets: list[ExportPresetView]
    columns: list[ExportColumnInfo]
    #: Columns every export appends after the chosen ones, by source kind.
    appended: list[ExportColumnInfo]
    formats: list[ExportFormat]
    xlsx_max_rows: NonNegative
    max_columns: NonNegative


class ExportPresetSaveRequest(Contract):
    """A new preset (cloned from `clone_from`, or from nothing) or the next version of a saved one.
    Built-in presets are cloned, never changed."""

    key: Code | None
    clone_from: Code | None
    name: Annotated[str, Field(min_length=1, max_length=120)]
    description: Annotated[str, Field(max_length=2000)] | None
    definition: ExportDefinition

    @model_validator(mode="after")
    def _one(self) -> Self:
        if self.key is not None and self.clone_from is not None:
            raise ValueError("save a version of a preset or clone one, not both")
        return self


class ExportPreviewResult(Contract):
    """The first rows exactly as the file would hold them, with the counts and warnings."""

    matching: NonNegative
    excluded_suppressed: NonNegative
    exported: NonNegative
    headers: list[str]
    #: Values after spreadsheet sanitising, in header order (at most 20 rows).
    sample: list[list[JsonValue]]
    #: `suppressed_included`, `stale_rows`, `partial_rows`, `synthetic_rows`, `too_many_rows`,
    #: `unverified_channels`, `hypotheses_never_exported`...
    warnings: list[Code]
    generated_at: UtcTimestamp


class ExportCancelRequest(Contract):
    export_id: Id


class ExportListQuery(Contract):
    project_id: Id | None
    limit: PageSize
    cursor: Cursor | None


class ExportItem(Contract):
    export: Export
    job_status: JobStatus | None
    rows_written: NonNegative
    rows_total: NonNegative | None
    is_synthetic: bool


class ExportPage(Contract):
    items: list[ExportItem]
    next_cursor: Cursor | None
    total: NonNegative


class ExportDestination(Contract):
    """What the desktop core's save dialog returned: a one-time grant the interface passes back to
    start the export, and the file name and folder to show. The path never reaches the page."""

    grant_id: Id
    file_name: Annotated[str, Field(min_length=1, max_length=260)]
    folder: Annotated[str, Field(min_length=1, max_length=1024)]
    format: ExportFormat


DATA_MODELS: tuple[type[Contract], ...] = (
    *RECORDS,
    ExportDestination,
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
    CrawlStartRequest,
    CrawlRun,
    CrawlRunListQuery,
    CrawlRunPage,
    CrawlUrlListQuery,
    CrawlUrlPage,
    CrawlUrlDetailQuery,
    CrawlUrlDetail,
    ResolutionStartRequest,
    ResolutionRun,
    ResolutionRunListQuery,
    ResolutionRunPage,
    MatchListQuery,
    MatchPage,
    MatchDetailQuery,
    MatchDetail,
    MatchActionRequest,
    CanonicalChoiceRequest,
    LatinoStartRequest,
    LatinoRun,
    LatinoRunListQuery,
    LatinoRunPage,
    LatinoDetailQuery,
    LatinoDetail,
    LatinoReviewRequest,
    WebsiteStartRequest,
    WebsiteRun,
    WebsiteRunListQuery,
    WebsiteRunPage,
    WebsiteAuditListQuery,
    WebsiteAuditPage,
    WebsiteAuditQuery,
    WebsiteAuditDetail,
    ContactStartRequest,
    ContactRun,
    ContactRunListQuery,
    ContactRunPage,
    ContactListQuery,
    ContactPage,
    ContactDetailQuery,
    ContactDetail,
    ContactActionRequest,
    DecisionMakerListQuery,
    DecisionMakerPage,
    DecisionMakerItem,
    DecisionMakerReviewRequest,
    SuppressionListQuery,
    SuppressionPage,
    SuppressionChangeRequest,
    SuppressionImportRequest,
    SuppressionImportResult,
    SuppressionExportRequest,
    SuppressionExport,
    AiStatusQuery,
    AiStatus,
    AiConfigureRequest,
    AiStartRequest,
    AiRun,
    AiRunListQuery,
    AiRunPage,
    AiBenchmarkRequest,
    AiInferenceListQuery,
    AiInferencePage,
    AiInferenceQuery,
    AiInferenceDetail,
    AiReviewRequest,
    AiModelChangeRequest,
    IcpDefinition,
    ScoringDefinition,
    BusinessFacts,
    LeadScorecard,
    ScoringCatalogQuery,
    ScoringCatalog,
    IcpSaveRequest,
    IcpProfileView,
    ScoringModelSaveRequest,
    ScoringModelView,
    ScoringChangeRequest,
    ScoringSimulationRequest,
    ScoringSimulation,
    ScoringStartRequest,
    ScoreRunListQuery,
    ScoreRunPage,
    LeadScoreQuery,
    LeadScorePage,
    ScorecardQuery,
    LeadScorecardDetail,
    ScoreCompareQuery,
    ScoreComparison,
    CalibrationQuery,
    CalibrationReport,
    LeadActionRequest,
    ExplorerFieldInfo,
    ExplorerCondition,
    ExplorerGroup,
    ExplorerSort,
    ExplorerQuery,
    PhoneTwin,
    ExplorerRow,
    ExplorerPage,
    ViewColumn,
    ViewDefinition,
    ExplorerCatalogQuery,
    ExplorerCatalog,
    SavedViewQuery,
    SavedViewItem,
    SavedViewList,
    SavedViewSaveRequest,
    SavedViewChangeRequest,
    ExportPreviewRequest,
    ExportPreview,
    BulkRequest,
    BulkSkip,
    BulkResult,
    LeadCompareQuery,
    ComparedLead,
    LeadComparison,
    ChecklistItem,
    ChecklistDefinition,
    ChecklistCatalogQuery,
    ChecklistView,
    ChecklistCatalog,
    ChecklistSaveRequest,
    ChecklistChangeRequest,
    CriterionCheck,
    ChecklistItemState,
    ChecklistState,
    PinView,
    CorrectionView,
    ResearchWorkspace,
    ResearchWorkspaceQuery,
    ResearchActionRequest,
    BriefRef,
    TimelineQuery,
    TimelineEntry,
    TimelinePage,
    EvidenceQuery,
    EvidenceDetail,
    BriefClaim,
    BriefSectionView,
    SalesBriefQuery,
    BriefDraftSentence,
    BriefDraftSection,
    BriefDraft,
    SalesBrief,
    SalesBriefSectionOutput,
    SalesBriefOutput,
    PipelineStartRequest,
    DeadLetterRequest,
    PipelineListQuery,
    ChildJob,
    PipelineAttempt,
    PipelineStageView,
    DeadLetter,
    StageCounts,
    PipelineRun,
    ResourceSlot,
    ResourceUsage,
    PipelinePage,
    JobEventQuery,
    JobEventItem,
    JobEventPage,
    LevelCount,
    EventCleanupRequest,
    EventCleanupResult,
    DatasetSnapshotRequest,
    FieldCoverage,
    ValueCount,
    DatasetQuality,
    LineageItem,
    DatasetSnapshotItem,
    DatasetSnapshotListQuery,
    DatasetSnapshotPage,
    DatasetSnapshotQuery,
    DatasetSnapshotDetail,
    DatasetDiffQuery,
    DiffRow,
    ChangedRow,
    DatasetDiff,
    DatasetCleanupRequest,
    CleanupSnapshot,
    DatasetCleanupResult,
    ExportColumn,
    ExportDefinition,
    ExportDraft,
    ExportStartRequest,
    ExportColumnInfo,
    ExportPresetView,
    ExportCatalogQuery,
    ExportCatalog,
    ExportPresetSaveRequest,
    ExportPreviewResult,
    ExportCancelRequest,
    ExportListQuery,
    ExportItem,
    ExportPage,
)
