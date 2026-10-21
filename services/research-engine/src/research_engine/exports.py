"""Local exports (phase 13): a file on this machine with its manifest, never sent anywhere.

    draft ─ preview: the first rows exactly as the file will hold them, with counts and warnings
          ─ start: the desktop core adds the destination its save dialog returned
          ─ job: rows streamed in batches into `<destination>.partial`, flushed and hashed; the
                 manifest written beside it; both renamed into place. A cancel, a failure or a
                 restart removes the partial files and records why
          ─ history: the `exports` row (who, what, where, how, the hash) and an audit entry

Rows come from a Lead Explorer query (the leads it matches now) or a dataset snapshot (its rows at
the cut-off). Leads on the do-not-contact list are left out here, whatever asked for the export,
unless the draft includes them with a reason. In CSV and XLSX, text a spreadsheet would run as a
formula is made inert and values are bounded to what the format accepts.
"""

import contextlib
import csv
import errno
import hashlib
import logging
import math
import os
import re
import sqlite3
import zipfile
from collections.abc import Callable, Generator, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from io import TextIOWrapper
from pathlib import Path
from typing import Final
from xml.sax.saxutils import escape

import duckdb
import orjson
import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberFormat

from research_engine import __version__, datasets, jobs, repository
from research_engine.domain import (
    EXPLORER_FILTER_ONLY,
    STALE_AFTER_DAYS,
    AuditAction,
    ContactOrigin,
    ContactType,
    DatasetSnapshot,
    EntityType,
    EventLevel,
    ExplorerField,
    ExplorerRow,
    Export,
    ExportCatalog,
    ExportCatalogQuery,
    ExportColumn,
    ExportColumnInfo,
    ExportColumnType,
    ExportDefinition,
    ExportDraft,
    ExportFormat,
    ExportItem,
    ExportListQuery,
    ExportPage,
    ExportPreset,
    ExportPresetSaveRequest,
    ExportPresetView,
    ExportPreviewRequest,
    ExportPreviewResult,
    ExportSourceKind,
    ExportStartRequest,
    ExportStatus,
    Job,
    JobStatus,
    PhoneTwin,
    RunControlAction,
    ScoreRun,
    ValidationStatus,
    format_ts,
    new_id,
    utc_now,
)
from research_engine.leads import explorer, index
from research_engine.resolution import normalize

logger = logging.getLogger(__name__)

JOB_KIND: Final = "export"
ACTOR: Final = "export-engine"
BATCH: Final = 500
#: Data rows under the header row of one worksheet.
XLSX_MAX_ROWS: Final = 1_048_575
MAX_CELL_CHARS: Final = 32_767
SAMPLE_ROWS: Final = 20
EXTENSIONS: Final = {
    ExportFormat.CSV: ".csv",
    ExportFormat.XLSX: ".xlsx",
    ExportFormat.JSON: ".json",
    ExportFormat.JSONL: ".jsonl",
    ExportFormat.PARQUET: ".parquet",
}
SPREADSHEETS: Final = frozenset({ExportFormat.CSV, ExportFormat.XLSX})
#: A channel counts as verified with a working mail domain or a person's confirmation (phase 09).
VERIFIED: Final = frozenset({ValidationStatus.LIKELY_VALID, ValidationStatus.DOMAIN_VALID})
#: Text starting with one of these runs as a formula in a spreadsheet (OWASP, CSV injection).
_FORMULA: Final = ("=", "+", "-", "@", chr(9), chr(13))
#: Characters XML 1.0 cannot hold (the tab, line feed and carriage return are kept).
_CONTROL: Final = re.compile(
    "["
    + "".join(chr(code) for code in (*range(0, 9), 11, 12, *range(14, 32), 0xFFFE, 0xFFFF))
    + "]"
)
_T = ExportColumnType
_F = ExplorerField


class ExportError(ValueError):
    """An export refused before anything is written: the draft, its source or its destination."""


class ExportFailure(RuntimeError):
    """Writing failed: `code` is `disk_full`, `permission_denied`, `io_error` or `too_many_rows`."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ExportStopped(Exception):
    """The job was cancelled, or the worker is stopping."""


# ---- columns ------------------------------------------------------------------------------------

#: Contact values an export can carry, by the key that names the column. The explorer keeps only
#: the kind of the channel (`main_channel`), never its value: a call list needs the value itself.
CONTACT_COLUMNS: Final[dict[str, ContactType]] = {
    "phone": ContactType.PHONE,
    "email": ContactType.EMAIL,
}
#: Query columns: explorer field (or a `CONTACT_COLUMNS` key) -> (type, the `ExplorerRow` attribute
#: or that same contact key, what it means).
QUERY_COLUMNS: Final[dict[str, tuple[ExportColumnType, str, str]]] = {
    _F.NAME: (_T.TEXT, "display_name", "The name the business shows (chosen among its records)."),
    "phone": (
        _T.TEXT,
        "phone",
        "The number to call: the most validated phone the business published, never one guessed "
        "from a pattern and never one on the do-not-contact list. Empty when there is none.",
    ),
    "email": (
        _T.TEXT,
        "email",
        "The address to write to, chosen the same way as the phone.",
    ),
    _F.LOCALITY: (_T.TEXT, "locality", "City of the location the business shows."),
    _F.REGION: (_T.TEXT, "region", "State of that location."),
    _F.CATEGORY: (_T.TEXT, "category", "Category the business shows."),
    _F.DOMAIN: (_T.TEXT, "domain", "Registrable domain of its website."),
    _F.LEAD_STAGE: (_T.TEXT, "lead_stage", "Funnel stage now (recorded by hand)."),
    _F.RESEARCH_STATUS: (
        _T.TEXT,
        "research_status",
        "Research status now: sales_ready, needs_research or disqualified, each set with a reason.",
    ),
    _F.ASSIGNEE: (_T.TEXT, "assignee", "Who researches the lead now."),
    _F.TAG: (_T.LIST, "tags", "Project tags on the business now."),
    _F.TIER: (
        _T.TEXT,
        "tier",
        "Tier in the score run: priority, review, low, insufficient_data or excluded.",
    ),
    _F.RANK: (_T.NUMBER, "rank", "Place in the whole finished run, whatever the filters."),
    _F.TOTAL_SCORE: (_T.NUMBER, "total_score", "Final lead score, 0 to 100; empty when excluded."),
    _F.CONFIDENCE: (
        _T.NUMBER,
        "confidence",
        "Certainty of the score, 0 to 1: data confidence "
        "times the share of the formula that was known.",
    ),
    _F.DATA_COVERAGE: (
        _T.NUMBER,
        "data_coverage",
        "Share of the final formula's weight that was known, 0 to 1.",
    ),
    _F.ICP_MATCH: (_T.NUMBER, "icp_match", "ICP_MATCH component, 0 to 100."),
    _F.OPPORTUNITY_SCORE: (
        _T.NUMBER,
        "opportunity_score",
        "OPPORTUNITY_SCORE component, 0 to 100.",
    ),
    _F.DIGITAL_GAP: (
        _T.NUMBER,
        "digital_gap",
        "DIGITAL_GAP component, 0 to 100: problems observed on the website.",
    ),
    _F.BUSINESS_HEALTH: (
        _T.NUMBER,
        "business_health",
        "BUSINESS_HEALTH component, 0 to 100: an estimate from explicit proxies.",
    ),
    _F.BUYING_CAPACITY: (
        _T.NUMBER,
        "buying_capacity",
        "BUYING_CAPACITY component, 0 to 100: an "
        "estimate from explicit proxies, never a financial claim.",
    ),
    _F.CONTACTABILITY: (_T.NUMBER, "contactability", "CONTACTABILITY component, 0 to 100."),
    _F.COMMERCIAL_INTENT: (
        _T.NUMBER,
        "commercial_intent",
        "COMMERCIAL_INTENT component, 0 to 100.",
    ),
    _F.DATA_CONFIDENCE: (_T.NUMBER, "data_confidence", "DATA_CONFIDENCE component, 0 to 100."),
    _F.LATINO_STATUS: (
        _T.TEXT,
        "latino_status",
        "Latino evidence status: verified, high "
        "confidence and probable ownership need ownership evidence; market affinity "
        "never means ownership.",
    ),
    _F.LATINO_CONFIDENCE: (_T.NUMBER, "latino_confidence", "Confidence of that status, 0 to 1."),
    _F.MAIN_CHANNEL: (
        _T.TEXT,
        "main_channel",
        "The channel the lead shows first: phone, email or "
        "a form the business published. Never an address guessed from a pattern.",
    ),
    _F.CHANNEL_STATUS: (
        _T.TEXT,
        "channel_status",
        "How far that channel was validated (phase 09 "
        "statuses: observed, structurally_valid, domain_valid, likely_valid...).",
    ),
    _F.USABLE_CHANNELS: (_T.NUMBER, "usable_channels", "Published channels counted as reachable."),
    _F.PROBLEM: (_T.LIST, "problems", "Problems observed on the business's website."),
    _F.TOP_PROBLEM: (_T.TEXT, "top_problem", "The problem that weighed most."),
    _F.OFFER: (_T.TEXT, "offer", "The offer the scorecard recommends for the observed problems."),
    _F.FACTS_AGE_DAYS: (
        _T.NUMBER,
        "facts_age_days",
        "Days since the facts behind the score were read.",
    ),
    _F.AUDIT_STATUS: (
        _T.TEXT,
        "audit_status",
        "Status of the newest website audit: complete, partial or empty.",
    ),
    _F.CONFLICTS: (_T.NUMBER, "conflicts", "Fields whose records disagree."),
    _F.SUPPRESSED: (_T.FLAG, "suppressed", "On the do-not-contact list now."),
    _F.STALE: (_T.FLAG, "stale", f"Facts read more than {STALE_AFTER_DAYS} days ago."),
    _F.PARTIAL: (_T.FLAG, "partial", "The score could not read everything."),
    "searches": (
        _T.LIST,
        "searches",
        "The searches that found it, by name: two when another search delivered it already.",
    ),
    "same_phone": (
        _T.LIST,
        "same_phone",
        "Other leads that publish its phone, in any folder, as «name (folder · searches)»: "
        "one line called twice.",
    ),
}
#: Snapshot columns -> (type, what it means).
SNAPSHOT_COLUMNS: Final[dict[str, tuple[ExportColumnType, str]]] = {
    "project_id": (_T.TEXT, "Identifier of its project."),
    "display_name": (_T.TEXT, "The name the business showed at the cut-off."),
    "status": (_T.TEXT, "Business status at the cut-off: candidate, active or closed."),
    "lead_stage": (_T.TEXT, "Funnel stage at the cut-off."),
    "locality": (_T.TEXT, "City at the cut-off."),
    "region": (_T.TEXT, "State at the cut-off."),
    "postal_code": (_T.TEXT, "Postal code at the cut-off."),
    "primary_domain": (_T.TEXT, "Website domain at the cut-off."),
    "primary_phone": (_T.TEXT, "Phone the business showed; empty when suppressed or a hypothesis."),
    "primary_category": (_T.TEXT, "Category at the cut-off."),
    "source_count": (_T.NUMBER, "Raw records the business was built from."),
    "evidence_count": (_T.NUMBER, "Active evidence items at the cut-off."),
    "first_seen_at": (_T.TIMESTAMP, "When a source first showed the business (UTC)."),
    "last_seen_at": (_T.TIMESTAMP, "When a source last showed it (UTC)."),
    "score_run_id": (_T.TEXT, "The score run the snapshot carried (schema 2)."),
    "tier": (_T.TEXT, "Tier in that run (schema 2)."),
    "total_score": (_T.NUMBER, "Final lead score in that run, 0 to 100 (schema 2)."),
    "suppressed": (_T.FLAG, "On the do-not-contact list at the cut-off (schema 2)."),
}
#: Columns every export appends after the chosen ones, by source.
QUERY_APPENDED: Final[dict[str, tuple[ExportColumnType, str]]] = {
    "business_id": (_T.TEXT, "Identifier of the canonical business: the same in every export."),
    "score_run_id": (
        _T.TEXT,
        "The score run the leads were read from; its versions are in the manifest.",
    ),
    "exported_at": (_T.TIMESTAMP, "When this file was written (UTC)."),
    "facts_collected_at": (_T.TIMESTAMP, "When the facts behind the score were read (UTC)."),
    "facts_age_days": (_T.NUMBER, "Days between reading those facts and writing this file."),
    "stale": (_T.FLAG, f"Facts read more than {STALE_AFTER_DAYS} days ago."),
    "partial": (
        _T.FLAG,
        "The score could not read everything: insufficient data, or a partial or "
        "empty website audit.",
    ),
    "contact_unverified": (
        _T.FLAG,
        "The main channel was published but not verified: neither a "
        "working mail domain nor confirmed by a person.",
    ),
    "do_not_contact": (
        _T.FLAG,
        "On the do-not-contact list when the file was written; true only in "
        "an export that includes those leads on purpose.",
    ),
}
SNAPSHOT_APPENDED: Final[dict[str, tuple[ExportColumnType, str]]] = {
    "business_id": (_T.TEXT, "Identifier of the canonical business: the same in every export."),
    "snapshot_id": (_T.TEXT, "The dataset snapshot the rows come from."),
    "snapshot_version": (_T.NUMBER, "Its version in the dataset."),
    "cutoff_at": (_T.TIMESTAMP, "The moment its rows were read (UTC)."),
    "do_not_contact": (
        _T.FLAG,
        "On the do-not-contact list when this file was written, checked "
        "again rather than as the snapshot saw it.",
    ),
}


def _appended(kind: ExportSourceKind) -> dict[str, tuple[ExportColumnType, str]]:
    return QUERY_APPENDED if kind is ExportSourceKind.QUERY else SNAPSHOT_APPENDED


def column_catalog() -> tuple[list[ExportColumnInfo], list[ExportColumnInfo]]:
    """(the columns an export can choose, the columns it always appends)."""
    chosen = [
        ExportColumnInfo(source_kind=ExportSourceKind.QUERY, key=key, type=kind, description=text)
        for key, (kind, _, text) in QUERY_COLUMNS.items()
    ] + [
        ExportColumnInfo(
            source_kind=ExportSourceKind.SNAPSHOT, key=key, type=kind, description=text
        )
        for key, (kind, text) in SNAPSHOT_COLUMNS.items()
    ]
    appended = [
        ExportColumnInfo(source_kind=source, key=key, type=kind, description=text)
        for source in ExportSourceKind
        for key, (kind, text) in _appended(source).items()
    ]
    return chosen, appended


def _types(kind: ExportSourceKind, keys: Sequence[str]) -> list[ExportColumnType]:
    if kind is ExportSourceKind.QUERY:
        return [QUERY_COLUMNS[key][0] for key in keys]
    return [SNAPSHOT_COLUMNS[key][0] for key in keys]


def check_columns(kind: ExportSourceKind, columns: Sequence[ExportColumn]) -> None:
    available = QUERY_COLUMNS if kind is ExportSourceKind.QUERY else SNAPSHOT_COLUMNS
    unknown = [column.key for column in columns if column.key not in available]
    if unknown:
        raise ExportError(f"not a column of a {kind.value} export: {', '.join(unknown)}")
    taken = {key.casefold() for key in _appended(kind)}
    clashing = [column.header for column in columns if column.header.casefold() in taken]
    if clashing:
        raise ExportError(f"these headers are appended to every export: {', '.join(clashing)}")


# ---- presets ------------------------------------------------------------------------------------


def _columns(*pairs: tuple[str, str]) -> list[ExportColumn]:
    return [ExportColumn(key=key, header=header) for key, header in pairs]


BUILT_IN: Final[tuple[tuple[str, str, str, ExportDefinition], ...]] = (
    (
        "sales_ready_concise",
        "Sales-ready concise",
        "One row per lead with what a first contact needs: who and where, the phone or email to "
        "reach them, the score, the route and the offer. Leads on the do-not-contact list stay "
        "out.",
        ExportDefinition(
            source_kind=ExportSourceKind.QUERY,
            format=ExportFormat.CSV,
            bom=True,
            columns=_columns(
                ("name", "Business"),
                ("phone", "Phone"),
                ("email", "Email"),
                ("locality", "City"),
                ("region", "State"),
                ("category", "Category"),
                ("domain", "Website"),
                ("tier", "Tier"),
                ("total_score", "Lead score"),
                ("main_channel", "Channel"),
                ("channel_status", "Channel status"),
                ("top_problem", "Top problem"),
                ("offer", "Offer"),
                ("research_status", "Research status"),
            ),
        ),
    ),
    (
        "full_evidence_audit",
        "Full evidence audit",
        "Every explorer field, to audit why each lead scored as it did.",
        ExportDefinition(
            source_kind=ExportSourceKind.QUERY,
            format=ExportFormat.XLSX,
            bom=False,
            columns=_columns(
                *(
                    (field.value, field.value)
                    for field in ExplorerField
                    if field not in EXPLORER_FILTER_ONLY
                )
            ),
        ),
    ),
    (
        "contact_review",
        "Contact review",
        "The route to each lead and how far it was verified, to review contacts before any "
        "outreach.",
        ExportDefinition(
            source_kind=ExportSourceKind.QUERY,
            format=ExportFormat.XLSX,
            bom=False,
            columns=_columns(
                ("name", "Business"),
                ("domain", "Website"),
                ("main_channel", "Channel"),
                ("channel_status", "Channel status"),
                ("usable_channels", "Usable channels"),
                ("conflicts", "Conflicting fields"),
                ("research_status", "Research status"),
                ("assignee", "Assignee"),
                ("lead_stage", "Lead stage"),
            ),
        ),
    ),
    (
        "website_opportunity_report",
        "Website opportunity report",
        "The digital gap, the audit status and the problems each site shows, with the offer that "
        "answers them.",
        ExportDefinition(
            source_kind=ExportSourceKind.QUERY,
            format=ExportFormat.XLSX,
            bom=False,
            columns=_columns(
                ("name", "Business"),
                ("domain", "Website"),
                ("category", "Category"),
                ("region", "State"),
                ("digital_gap", "Digital gap"),
                ("audit_status", "Audit status"),
                ("problem", "Problems"),
                ("top_problem", "Top problem"),
                ("offer", "Offer"),
                ("total_score", "Lead score"),
            ),
        ),
    ),
    (
        "dataset_analytical",
        "Dataset analytical",
        "The rows of a dataset snapshot as they were at its cut-off, typed for analysis.",
        ExportDefinition(
            source_kind=ExportSourceKind.SNAPSHOT,
            format=ExportFormat.PARQUET,
            bom=False,
            columns=_columns(*((key, key) for key in SNAPSHOT_COLUMNS)),
        ),
    ),
)


def _current(conn: sqlite3.Connection, key: str) -> ExportPreset | None:
    row = conn.execute(
        f"SELECT {', '.join(ExportPreset.model_fields)} FROM export_presets "  # noqa: S608
        "WHERE key = ? AND superseded_at IS NULL",
        (key,),
    ).fetchone()
    return None if row is None else repository.from_row(ExportPreset, row)


def _append_version(
    conn: sqlite3.Connection,
    current: ExportPreset | None,
    key: str,
    name: str,
    description: str | None,
    definition: ExportDefinition,
    *,
    built_in: bool,
    cloned_from: str | None,
    actor: str,
) -> ExportPreset:
    now = utc_now()
    preset = ExportPreset(
        id=new_id(),
        key=key,
        version=(current.version + 1) if current else 1,
        name=name,
        description=description,
        definition_json=definition.model_dump(mode="json"),
        built_in=built_in,
        cloned_from_id=cloned_from,
        created_by=actor,
        created_at=now,
        superseded_at=None,
    )
    with repository.atomic(conn):
        if current is not None:
            conn.execute(
                "UPDATE export_presets SET superseded_at = ? WHERE id = ?",
                (format_ts(now), current.id),
            )
        repository.insert(conn, preset)
        repository.audit(
            conn,
            AuditAction.CREATE,
            EntityType.EXPORT_PRESET,
            preset.id,
            actor=actor,
            detail={"key": key, "version": preset.version},
        )
    return preset


def ensure_presets(conn: sqlite3.Connection) -> None:
    """Writes the built-in presets, and a new version of one whose definition changed in code."""
    for key, name, description, definition in BUILT_IN:
        current = _current(conn, key)
        if (
            current is not None
            and ExportDefinition.model_validate(current.definition_json) == definition
            and (current.name, current.description) == (name, description)
        ):
            continue
        _append_version(
            conn,
            current,
            key,
            name,
            description,
            definition,
            built_in=True,
            cloned_from=None,
            actor=ACTOR,
        )


def catalog(conn: sqlite3.Connection, query: ExportCatalogQuery) -> ExportCatalog:
    ensure_presets(conn)
    where = "" if query.include_superseded else "WHERE p.superseded_at IS NULL "
    rows = conn.execute(
        f"SELECT {', '.join('p.' + name for name in ExportPreset.model_fields)}, "  # noqa: S608
        "src.key AS cloned_from_key, (SELECT group_concat(v.version) FROM (SELECT version FROM "
        "export_presets x WHERE x.key = p.key ORDER BY version DESC) v) AS versions "
        "FROM export_presets p LEFT JOIN export_presets src ON src.id = p.cloned_from_id "
        f"{where}ORDER BY p.built_in DESC, p.key, p.version DESC"
    ).fetchall()
    presets = [
        ExportPresetView(
            preset=repository.from_row(ExportPreset, row),
            definition=ExportDefinition.model_validate(orjson.loads(row["definition_json"])),
            versions=[int(version) for version in str(row["versions"]).split(",")],
            cloned_from_key=row["cloned_from_key"],
        )
        for row in rows
    ]
    chosen, appended = column_catalog()
    return ExportCatalog(
        presets=presets,
        columns=chosen,
        appended=appended,
        formats=list(ExportFormat),
        xlsx_max_rows=XLSX_MAX_ROWS,
        max_columns=len(ExplorerField),
    )


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_")[:40] or "preset"


def save_preset(conn: sqlite3.Connection, request: ExportPresetSaveRequest, *, actor: str) -> None:
    ensure_presets(conn)
    check_columns(request.definition.source_kind, request.definition.columns)
    if request.key is not None:
        current = _current(conn, request.key)
        if current is None:
            raise repository.NotFoundError(f"export preset {request.key} not found")
        if current.built_in:
            raise ExportError("a built-in preset is cloned, never changed")
        unchanged = ExportDefinition.model_validate(
            current.definition_json
        ) == request.definition and (current.name, current.description) == (
            request.name,
            request.description,
        )
        if not unchanged:
            _append_version(
                conn,
                current,
                current.key,
                request.name,
                request.description,
                request.definition,
                built_in=False,
                cloned_from=current.cloned_from_id,
                actor=actor,
            )
        return
    source = None
    if request.clone_from is not None:
        source = _current(conn, request.clone_from)
        if source is None:
            raise repository.NotFoundError(f"export preset {request.clone_from} not found")
    base = f"custom_{_slug(request.name)}"
    key, suffix = base, 1
    while conn.execute("SELECT 1 FROM export_presets WHERE key = ?", (key,)).fetchone():
        suffix += 1
        key = f"{base}_{suffix}"
    _append_version(
        conn,
        None,
        key,
        request.name,
        request.description,
        request.definition,
        built_in=False,
        cloned_from=source.id if source else None,
        actor=actor,
    )


# ---- rows ---------------------------------------------------------------------------------------


@dataclass(slots=True)
class Plan:
    """What an export reads and writes, checked before a job is queued."""

    project_id: str
    kind: ExportSourceKind
    run: ScoreRun | None
    snapshot: DatasetSnapshot | None
    keys: list[str]
    headers: list[str]
    types: list[ExportColumnType]
    is_synthetic: bool
    skipped: list[int] = field(default_factory=lambda: [0])


def plan(conn: sqlite3.Connection, draft: ExportDraft) -> Plan:
    check_columns(draft.source_kind, draft.columns)
    keys = [column.key for column in draft.columns]
    appended = _appended(draft.source_kind)
    run = snapshot = None
    if draft.source_kind is ExportSourceKind.QUERY:
        if draft.query is None:
            raise ExportError("a query export names its query")
        run = explorer.run_of(conn, draft.query.score_run_id)
        if run is None:
            raise ExportError("there is no finished score run to export leads from")
        index.ensure_indexed(conn, run)
        project_id = run.project_id
        chosen_types = _types(draft.source_kind, keys)
    else:
        if draft.snapshot_id is None:
            raise ExportError("a snapshot export names its snapshot")
        item = datasets.snapshot_item(conn, draft.snapshot_id)
        snapshot, project_id = item.snapshot, item.project_id
        missing = [key for key in keys if key not in datasets.snapshot_columns(snapshot)]
        if missing:
            raise ExportError(
                f"snapshot schema {snapshot.schema_version} has no {', '.join(missing)}"
            )
        chosen_types = _types(draft.source_kind, keys)
    (synthetic,) = conn.execute(
        "SELECT is_synthetic FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    return Plan(
        project_id=project_id,
        kind=draft.source_kind,
        run=run,
        snapshot=snapshot,
        keys=keys + list(appended),
        headers=[column.header for column in draft.columns] + list(appended),
        types=chosen_types + [kind for kind, _ in appended.values()],
        is_synthetic=bool(synthetic),
    )


def phone_text(normalized: str | None, raw: str) -> str:
    """A phone the same way in every file: (281) 844-1292 for a North American number, as a US
    caller reads and dials it and as Trato's importer (country US) reads it back; the international
    form for any other; the published text when it never parsed. Never a leading "+" on a
    North American number, which a spreadsheet would run as a formula."""
    if not normalized:
        return raw
    try:
        number = phonenumbers.parse(normalized)
    except NumberParseException:
        return raw
    with contextlib.suppress(NumberParseException):  # E.164 drops the extension the text gave
        region = phonenumbers.region_code_for_number(number)
        number.extension = phonenumbers.parse(raw, region).extension
    # ponytail: another country keeps its "+" and so the formula guard's apostrophe; the lists
    # are of US businesses, a national form there would lose the country code
    national = number.country_code == 1
    return phonenumbers.format_number(
        number, PhoneNumberFormat.NATIONAL if national else PhoneNumberFormat.INTERNATIONAL
    )


def twin_text(twin: PhoneTwin) -> str:
    """Another lead on the same line, said with the folder and searches that found it."""
    return f"{twin.display_name} ({' · '.join([twin.project_name, *twin.searches])})"


def _plain(value: object) -> object:
    if isinstance(value, PhoneTwin):
        return twin_text(value)
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, datetime):
        return format_ts(value if value.tzinfo else value.replace(tzinfo=UTC))
    if isinstance(value, list):
        return [_plain(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _labelled(value: object, labels: dict[str, str] | None) -> object:
    """A code in the words the page gave for it (`ExportColumn.labels`), a list code by code."""
    if not labels:
        return value
    if isinstance(value, list):
        return [labels.get(item, item) if isinstance(item, str) else item for item in value]
    return labels.get(value, value) if isinstance(value, str) else value


def unverified(row: ExplorerRow) -> bool:
    return row.main_channel is not None and row.channel_status not in VERIFIED


def best_contacts(conn: sqlite3.Connection, ids: Sequence[str]) -> dict[str, dict[str, str]]:
    """Per business, the phone and the email to use: the most validated published one of each kind,
    the primary before the rest, off the do-not-contact list. Invalid, stale and suppressed
    addresses are no route, the same rule the explorer's channel follows (`leads.index`). A merge
    moves no contact, so the records merged into a business lend it theirs, as its page shows.
    A phone is written in one form whatever the page published (`phone_text`). An email is the
    business's own only: never a placeholder, domain-sale or platform address, nor one another
    business publishes too (D-106, `_shared_emails`)."""
    found: dict[str, dict[str, str]] = {}
    ranked: dict[tuple[str, str], tuple[int, int]] = {}
    marks = ",".join("?" * len(ids))
    kinds = ",".join("?" * len(CONTACT_COLUMNS))
    rows = conn.execute(
        "SELECT coalesce(b.merged_into_id, b.id) AS root, c.contact_type, c.value_raw, "  # noqa: S608
        "c.value_normalized, "
        "c.validation_status, c.is_primary FROM contact_points c "
        f"JOIN businesses b ON b.id = c.business_id WHERE (b.id IN ({marks}) "
        f"OR b.merged_into_id IN ({marks})) AND c.contact_type IN ({kinds}) "
        # equals keep the oldest, as the business's page picks
        "AND c.deleted_at IS NULL AND c.do_not_contact = 0 AND c.origin <> ? ORDER BY c.id",
        [
            *ids,
            *ids,
            *(kind.value for kind in CONTACT_COLUMNS.values()),
            ContactOrigin.HYPOTHESIS.value,
        ],
    ).fetchall()
    shared = _shared_emails(conn, rows)
    for row in rows:
        rank = index.STATUS_RANK.get(ValidationStatus(row["validation_status"]))
        if rank is None:
            continue
        if row["contact_type"] == ContactType.EMAIL and (
            normalize.not_the_business(row["value_raw"])
            or (row["root"], row["value_normalized"]) in shared
        ):
            continue
        key = (row["root"], row["contact_type"])
        order = (rank, 0 if row["is_primary"] else 1)
        if key in ranked and ranked[key] <= order:
            continue
        ranked[key] = order
        value = row["value_raw"]
        if row["contact_type"] == ContactType.PHONE:
            value = phone_text(row["value_normalized"], value)
        found.setdefault(row["root"], {})[row["contact_type"]] = value
    return found


def _shared_emails(conn: sqlite3.Connection, rows: Sequence[sqlite3.Row]) -> set[tuple[str, str]]:
    """(business, address) pairs whose address a different business publishes too: a booking
    platform's, a site template's or a chain's address, not this business's own. Two businesses
    are different when both have phones and share none, or both have sites on other domains; two
    records on one line (D-104), or one business two projects found, keep theirs."""
    # ponytail: exact match on the stored address; compare lower() if case variants ever leak
    emails = {row["value_normalized"] for row in rows if row["contact_type"] == ContactType.EMAIL}
    emails.discard(None)
    if not emails:
        return set()
    published: dict[str, set[str]] = {}
    for value, root in conn.execute(
        "SELECT c.value_normalized, coalesce(b.merged_into_id, b.id) FROM contact_points c "  # noqa: S608
        "JOIN businesses b ON b.id = c.business_id WHERE c.contact_type = 'email' "
        f"AND c.value_normalized IN ({','.join('?' * len(emails))}) "
        "AND c.deleted_at IS NULL AND c.origin <> ?",
        [*emails, ContactOrigin.HYPOTHESIS.value],
    ):
        published.setdefault(value, set()).add(root)
    owners = {root for roots in published.values() if len(roots) > 1 for root in roots}
    if not owners:
        return set()
    marks = ",".join("?" * len(owners))
    phones: dict[str, set[str]] = {root: set() for root in owners}
    sites: dict[str, set[str]] = {root: set() for root in owners}
    for root, phone in conn.execute(
        "SELECT coalesce(b.merged_into_id, b.id), c.value_normalized FROM contact_points c "  # noqa: S608
        f"JOIN businesses b ON b.id = c.business_id WHERE (b.id IN ({marks}) "
        f"OR b.merged_into_id IN ({marks})) AND c.contact_type = 'phone' "
        "AND c.value_normalized IS NOT NULL AND c.deleted_at IS NULL AND c.origin <> ?",
        [*owners, *owners, ContactOrigin.HYPOTHESIS.value],
    ):
        phones[root].add(phone)
    for root, host in conn.execute(
        "SELECT coalesce(b.merged_into_id, b.id), w.domain FROM websites w "  # noqa: S608
        f"JOIN businesses b ON b.id = w.business_id WHERE b.id IN ({marks}) "
        f"OR b.merged_into_id IN ({marks})",
        [*owners, *owners],
    ):
        sites[root].add(normalize.registrable_domain(host))

    def different(one: str, other: str) -> bool:
        if phones[one] & phones[other]:
            return False
        if phones[one] and phones[other]:
            return True
        return bool(sites[one] and sites[other] and not sites[one] & sites[other])

    return {
        (root, value)
        for value, roots in published.items()
        for root in roots
        if any(different(root, other) for other in roots - {root})
    }


def query_rows(
    conn: sqlite3.Connection,
    plan_: Plan,
    draft: ExportDraft,
    now: datetime,
    should_stop: Callable[[], bool] = lambda: False,
) -> Generator[list[object], None, None]:
    """The leads a query matches now, in its order, off the do-not-contact list unless included."""
    if plan_.run is None or draft.query is None:
        raise ExportError("a query export names its query")
    chosen = [QUERY_COLUMNS[key][1] for key in plan_.keys[: len(draft.columns)]]
    wanted = any(attribute in CONTACT_COLUMNS for attribute in chosen)
    exported_at = format_ts(now)
    for ids in explorer.stream_ids(conn, draft.query, plan_.run, now, BATCH):
        if should_stop():
            raise ExportStopped
        contacts = best_contacts(conn, ids) if wanted else {}
        for row in explorer.rows_for(conn, plan_.run, ids, now):
            if row.suppressed and not draft.include_suppressed:
                plan_.skipped[0] += 1
                continue
            reachable = contacts.get(row.business_id, {})
            yield [
                *(
                    reachable.get(CONTACT_COLUMNS[attribute])
                    if attribute in CONTACT_COLUMNS
                    else _labelled(_plain(getattr(row, attribute)), column.labels)
                    for attribute, column in zip(chosen, draft.columns, strict=True)
                ),
                row.business_id,
                plan_.run.id,
                exported_at,
                format_ts(row.facts_collected_at),
                row.facts_age_days,
                row.stale,
                row.partial,
                unverified(row),
                row.suppressed,
            ]


def snapshot_rows(
    conn: sqlite3.Connection,
    data_dir: Path,
    plan_: Plan,
    draft: ExportDraft,
    should_stop: Callable[[], bool] = lambda: False,
) -> Generator[list[object], None, None]:
    """The rows of a snapshot by business id, checked against the do-not-contact list now."""
    snapshot = plan_.snapshot
    if snapshot is None:
        raise ExportError("a snapshot export names its snapshot")
    suppressed = explorer.suppressed_roots(conn)
    keys = plan_.keys[: len(draft.columns)]
    chosen = ", ".join(keys)
    phones = [key == "primary_phone" for key in keys]
    cutoff = format_ts(snapshot.cutoff_at or snapshot.created_at)
    duck = duckdb.connect()
    try:
        source = datasets.relation(duck, data_dir, snapshot)
        cursor = duck.execute(f"SELECT {chosen}, business_id FROM {source} ORDER BY business_id")  # noqa: S608
        while batch := cursor.fetchmany(BATCH):
            if should_stop():
                raise ExportStopped
            for values in batch:
                listed = values[-1] in suppressed
                if listed and not draft.include_suppressed:
                    plan_.skipped[0] += 1
                    continue
                yield [
                    *(
                        phone_text(value, value)
                        if phone and isinstance(value, str)
                        else _labelled(_plain(value), column.labels)
                        for value, column, phone in zip(
                            values[:-1], draft.columns, phones, strict=True
                        )
                    ),
                    values[-1],
                    snapshot.id,
                    snapshot.version,
                    cutoff,
                    listed,
                ]
    finally:
        duck.close()


def rows(
    conn: sqlite3.Connection,
    data_dir: Path,
    plan_: Plan,
    draft: ExportDraft,
    now: datetime,
    should_stop: Callable[[], bool] = lambda: False,
) -> Generator[list[object], None, None]:
    if plan_.kind is ExportSourceKind.QUERY:
        return query_rows(conn, plan_, draft, now, should_stop)
    return snapshot_rows(conn, data_dir, plan_, draft, should_stop)


# ---- cells --------------------------------------------------------------------------------------


def spreadsheet_cell(value: object) -> object:
    """A value a spreadsheet shows as data: text that would run as a formula starts with an
    apostrophe, characters XML cannot hold are dropped, and text stops at Excel's cell limit."""
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, list):
        value = " | ".join(str(item) for item in value)
    text = _CONTROL.sub("", str(value))
    if text.startswith(_FORMULA):
        text = "'" + text
    return text[:MAX_CELL_CHARS]


def display_row(export_format: ExportFormat, values: Sequence[object]) -> list[object]:
    """A row as the file will hold it: sanitised for spreadsheets, exact for data formats."""
    if export_format in SPREADSHEETS:
        return [spreadsheet_cell(value) for value in values]
    return list(values)


def _csv_text(value: object) -> object:
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else value


# ---- writers ------------------------------------------------------------------------------------

Progress = Callable[[int], None]


def _fsync(path: Path) -> None:
    with path.open("r+b") as handle:
        os.fsync(handle.fileno())


def write_csv(
    path: Path,
    headers: Sequence[str],
    source: Iterator[list[object]],
    bom: bool,
    progress: Progress,
) -> int:
    count = 0
    with path.open("w", encoding="utf-8-sig" if bom else "utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator=chr(13) + chr(10))
        writer.writerow([spreadsheet_cell(header) for header in headers])
        for values in source:
            writer.writerow([_csv_text(spreadsheet_cell(value)) for value in values])
            count += 1
            if count % BATCH == 0:
                progress(count)
        handle.flush()
        os.fsync(handle.fileno())
    return count


def write_json(
    path: Path,
    headers: Sequence[str],
    source: Iterator[list[object]],
    lines: bool,
    progress: Progress,
) -> int:
    count = 0
    newline = chr(10).encode()
    with path.open("wb") as handle:
        if not lines:
            handle.write(b"[" + newline)
        for values in source:
            record = orjson.dumps(dict(zip(headers, values, strict=True)))
            if lines:
                handle.write(record + newline)
            else:
                handle.write((b"," + newline if count else b"") + record)
            count += 1
            if count % BATCH == 0:
                progress(count)
        if not lines:
            handle.write(newline + b"]" + newline)
        handle.flush()
        os.fsync(handle.fileno())
    return count


_XLSX_PARTS: Final = {
    "[Content_Types].xml": (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'  # noqa: E501
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'  # noqa: E501
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'  # noqa: E501
        "</Types>"
    ),
    "_rels/.rels": (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'  # noqa: E501
        "</Relationships>"
    ),
    "xl/workbook.xml": (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Export" sheetId="1" r:id="rId1"/></sheets></workbook>'
    ),
    "xl/_rels/workbook.xml.rels": (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'  # noqa: E501
        "</Relationships>"
    ),
}


def _entry(name: str) -> zipfile.ZipInfo:
    # a fixed timestamp: the same rows always make the same bytes (and the same hash)
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


def _column_ref(number: int) -> str:
    letters = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _xlsx_row(index: int, values: Sequence[object]) -> str:
    cells = []
    for position, raw in enumerate(values, 1):
        value = spreadsheet_cell(raw)
        ref = f"{_column_ref(position)}{index}"
        if value is None:
            continue
        if isinstance(value, bool):
            cells.append(f'<c r="{ref}" t="b"><v>{int(value)}</v></c>')
        elif isinstance(value, int | float):
            cells.append(f'<c r="{ref}"><v>{value!r}</v></c>')
        else:
            text = escape(str(value), {'"': "&quot;"})
            cells.append(
                f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'
            )
    return f'<row r="{index}">{"".join(cells)}</row>'


def write_xlsx(
    path: Path, headers: Sequence[str], source: Iterator[list[object]], progress: Progress
) -> int:
    """One worksheet streamed into the archive, in constant memory."""
    count = 0
    with zipfile.ZipFile(path, "w") as book:
        for name, content in _XLSX_PARTS.items():
            book.writestr(_entry(name), content)
        with book.open(_entry("xl/worksheets/sheet1.xml"), "w", force_zip64=True) as raw:
            sheet = TextIOWrapper(raw, encoding="utf-8", newline="")
            sheet.write(
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                "<sheetData>"
            )
            sheet.write(_xlsx_row(1, headers))
            for values in source:
                if count >= XLSX_MAX_ROWS:
                    raise ExportFailure(
                        "too_many_rows",
                        f"a worksheet holds {XLSX_MAX_ROWS:,} rows: use CSV, JSONL or Parquet",
                    )
                count += 1
                sheet.write(_xlsx_row(count + 1, values))
                if count % BATCH == 0:
                    progress(count)
            sheet.write("</sheetData></worksheet>")
            sheet.flush()
            sheet.detach()
    _fsync(path)
    return count


_DUCK_TYPES: Final = {
    _T.TEXT: "VARCHAR",
    _T.NUMBER: "DOUBLE",
    _T.FLAG: "BOOLEAN",
    _T.TIMESTAMP: "TIMESTAMP",
    _T.LIST: "VARCHAR[]",
}


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def write_parquet(
    path: Path,
    headers: Sequence[str],
    types: Sequence[ExportColumnType],
    source: Iterator[list[object]],
    progress: Progress,
) -> int:
    """Rows staged as JSON lines beside the file, then typed column by column into Parquet."""
    staging = path.with_name(path.name + ".jsonl")
    count = 0
    newline = chr(10).encode()
    try:
        with staging.open("wb") as out:
            for values in source:
                record = {}
                for position, (value, kind) in enumerate(zip(values, types, strict=True)):
                    if kind is _T.TIMESTAMP and isinstance(value, str):
                        value = value.removesuffix("Z").replace("T", " ")
                    record[f"c{position}"] = value
                out.write(orjson.dumps(record) + newline)
                count += 1
                if count % BATCH == 0:
                    progress(count)
        columns = ", ".join(
            f"'c{position}': '{_DUCK_TYPES[kind]}'" for position, kind in enumerate(types)
        )
        select = ", ".join(
            f"c{position} AS {_quoted(header)}" for position, header in enumerate(headers)
        )
        literal = "'" + staging.as_posix().replace("'", "''") + "'"
        target = "'" + path.as_posix().replace("'", "''") + "'"
        duck = duckdb.connect()
        try:
            duck.execute("SET threads = 1")  # one writer keeps the row order and the bytes stable
            duck.execute(
                f"COPY (SELECT {select} FROM read_json({literal}, format = 'newline_delimited', "  # noqa: S608
                f"columns = {{{columns}}})) TO {target} (FORMAT parquet, COMPRESSION zstd)"
            )
        finally:
            duck.close()
    finally:
        staging.unlink(missing_ok=True)
    _fsync(path)
    return count


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def io_code(error: OSError) -> str:
    if error.errno == errno.ENOSPC or getattr(error, "winerror", None) in (39, 112):
        return "disk_full"
    if isinstance(error, PermissionError) or error.errno in (errno.EACCES, errno.EPERM):
        return "permission_denied"
    return "io_error"


# ---- destinations -------------------------------------------------------------------------------


def partial_paths(destination: Path) -> tuple[Path, Path, Path]:
    """(the data file while written, the manifest, the manifest while written)."""
    manifest = destination.with_name(destination.name + ".manifest.json")
    return (
        destination.with_name(destination.name + ".partial"),
        manifest,
        manifest.with_name(manifest.name + ".partial"),
    )


def check_destination(data_dir: Path | None, destination: str, export_format: ExportFormat) -> Path:
    """A local file the save dialog returned: absolute, in an existing folder, not on a network
    share or a device, and never inside the app's own stores."""
    path = Path(destination)
    if not path.is_absolute() or path.drive.startswith(("\\\\", "//")):
        raise ExportError("choose a folder on this computer")
    if path.suffix.lower() != EXTENSIONS[export_format]:
        raise ExportError(f"a {export_format.value} export ends in {EXTENSIONS[export_format]}")
    if not path.parent.is_dir():
        raise ExportError("the folder chosen does not exist")
    if path.is_dir():
        raise ExportError("the name chosen is a folder")
    if data_dir is not None:
        resolved, root = path.resolve(), data_dir.resolve()
        if resolved.is_relative_to(root) and not resolved.is_relative_to(root / "exports"):
            raise ExportError("exports stay out of the app's data folder (use its exports folder)")
    return path


# ---- preview ------------------------------------------------------------------------------------


def preview(
    conn: sqlite3.Connection, data_dir: Path, draft: ExportDraft, now: datetime
) -> ExportPreviewResult:
    plan_ = plan(conn, draft)
    warnings: list[str] = []
    if draft.source_kind is ExportSourceKind.QUERY:
        if draft.query is None or plan_.run is None:
            raise ExportError("a query export names its query")
        counted = explorer.export_preview(
            conn,
            ExportPreviewRequest(
                query=draft.query,
                # counting reads explorer fields only; a contact value is none (`CONTACT_COLUMNS`)
                columns=[
                    ExplorerField(key)
                    for key in plan_.keys[: len(draft.columns)]
                    if key not in CONTACT_COLUMNS
                ]
                or [ExplorerField.NAME],
                include_suppressed=draft.include_suppressed,
                sample_size=1,
            ),
            now,
        )
        matching, excluded, exported = (
            counted.matching,
            counted.excluded_suppressed,
            counted.exported,
        )
        warnings += [code for code in counted.warnings if code != "synthetic_rows"]
        verified = ", ".join(f"'{status.value}'" for status in VERIFIED)
        if explorer.count(
            conn,
            plan_.run,
            draft.query.model_copy(update={"offset": 0, "limit": 1}),
            now,
            [f"li.main_channel IS NOT NULL AND coalesce(li.channel_status NOT IN ({verified}), 1)"],
        ):
            warnings.append("unverified_channels")
    else:
        if plan_.snapshot is None:
            raise ExportError("a snapshot export names its snapshot")
        snapshot = plan_.snapshot
        suppressed = explorer.suppressed_roots(conn)
        duck = duckdb.connect()
        try:
            source = datasets.relation(duck, data_dir, snapshot)
            listed = duck.execute(
                f"SELECT count(*) FROM {source} WHERE list_contains(?, business_id)",  # noqa: S608
                [sorted(suppressed)],
            ).fetchone()
        finally:
            duck.close()
        matching = snapshot.row_count
        excluded = 0 if draft.include_suppressed else int(listed[0] if listed else 0)
        exported = matching - excluded
        if draft.include_suppressed and listed and listed[0]:
            warnings.append("suppressed_included")
        quality = datasets.snapshot_detail(conn, data_dir, snapshot.id).quality
        if quality.stale_rows:
            warnings.append("stale_rows")
        if snapshot.schema_version == 1:
            warnings.append("schema_1_snapshot")
        warnings.append("hypotheses_never_exported")
    if plan_.is_synthetic:
        warnings.append("synthetic_rows")
    if draft.format is ExportFormat.XLSX and exported > XLSX_MAX_ROWS:
        warnings.append("too_many_rows")
    sample: list[list[object]] = []
    with contextlib.closing(rows(conn, data_dir, plan_, draft, now)) as produced:
        for values in produced:
            sample.append(display_row(draft.format, values))
            if len(sample) >= SAMPLE_ROWS:
                break
    return ExportPreviewResult(
        matching=matching,
        excluded_suppressed=excluded,
        exported=exported,
        headers=plan_.headers,
        sample=sample,
        warnings=list(dict.fromkeys(warnings)),
        generated_at=now,
    )


# ---- the job ------------------------------------------------------------------------------------


def export_item(conn: sqlite3.Connection, export_id: str) -> ExportItem:
    row = conn.execute(
        f"SELECT {', '.join('e.' + name for name in Export.model_fields)}, j.status AS job_status, "  # noqa: S608
        "j.progress_done, j.progress_total, p.is_synthetic FROM exports e "
        "JOIN projects p ON p.id = e.project_id LEFT JOIN jobs j ON j.id = e.job_id WHERE e.id = ?",
        (export_id,),
    ).fetchone()
    if row is None:
        raise repository.NotFoundError(f"export {export_id} not found")
    return ExportItem(
        export=repository.from_row(Export, row),
        job_status=JobStatus(row["job_status"]) if row["job_status"] else None,
        rows_written=row["progress_done"] or 0,
        rows_total=row["progress_total"],
        is_synthetic=bool(row["is_synthetic"]),
    )


def list_exports(conn: sqlite3.Connection, query: ExportListQuery) -> ExportPage:
    where, params = list[str](), list[object]()
    if query.project_id is not None:
        where.append("project_id = ?")
        params.append(query.project_id)
    clause = f"WHERE {' AND '.join(where)} " if where else ""
    (total,) = conn.execute(f"SELECT count(*) FROM exports {clause}", params).fetchone()  # noqa: S608
    if query.cursor is not None:
        last_key, last_id = repository.decode_cursor(query.cursor, JOB_KIND)
        where.append("(requested_at, id) < (?, ?)")
        params += [last_key, last_id]
        clause = f"WHERE {' AND '.join(where)} "
    found = conn.execute(
        f"SELECT id, requested_at FROM exports {clause}ORDER BY requested_at DESC, id DESC LIMIT ?",  # noqa: S608
        [*params, query.limit + 1],
    ).fetchall()
    page = found[: query.limit]
    next_cursor = (
        repository.encode_cursor(JOB_KIND, page[-1]["requested_at"], page[-1]["id"])
        if len(found) > query.limit
        else None
    )
    return ExportPage(
        items=[export_item(conn, row["id"]) for row in page], next_cursor=next_cursor, total=total
    )


def plan_export(
    conn: sqlite3.Connection,
    data_dir: Path | None,
    request: ExportStartRequest,
    *,
    actor: str,
    request_id: str | None,
    now: datetime,
) -> str:
    """Checks the draft and the destination, then queues the job. Returns the export id."""
    destination = check_destination(data_dir, request.destination, request.format)
    plan_ = plan(conn, request)
    if request.source_kind is ExportSourceKind.QUERY:
        if request.query is None or plan_.run is None:
            raise ExportError("a query export names its query")
        extra = [] if request.include_suppressed else [explorer.unsuppressed_clause()]
        total = explorer.count(conn, plan_.run, request.query, now, extra)
    else:
        if plan_.snapshot is None:
            raise ExportError("a snapshot export names its snapshot")
        total = plan_.snapshot.row_count
    if request.format is ExportFormat.XLSX and total > XLSX_MAX_ROWS:
        raise ExportError(
            f"{total:,} rows do not fit a worksheet ({XLSX_MAX_ROWS:,}): use CSV, JSONL or Parquet"
        )
    draft = request.model_dump(mode="json")
    job = Job(
        id=new_id(),
        project_id=plan_.project_id,
        kind=JOB_KIND,
        status=JobStatus.QUEUED,
        priority=0,
        params_json={"export_id": None},
        idempotency_key=None,
        progress_done=0,
        progress_total=total,
        requested_at=now,
        started_at=None,
        finished_at=None,
        created_at=now,
        updated_at=now,
    )
    export = Export(
        id=new_id(),
        project_id=plan_.project_id,
        dataset_snapshot_id=plan_.snapshot.id if plan_.snapshot else None,
        status=ExportStatus.PENDING,
        format=request.format,
        file_name=destination.name,
        row_count=None,
        file_sha256=None,
        manifest_json=None,
        requested_at=now,
        started_at=None,
        finished_at=None,
        error_message=None,
        created_at=now,
        updated_at=now,
        job_id=job.id,
        destination=str(destination),
        columns_json=[
            {"key": key, "header": header, "type": kind.value}
            for key, header, kind in zip(plan_.keys, plan_.headers, plan_.types, strict=True)
        ],
        config_json={"draft": draft, "score_run_id": plan_.run.id if plan_.run else None},
        actor=actor,
        preset_key=request.preset_key,
        preset_version=request.preset_version,
        error_code=None,
    )
    job = job.model_copy(update={"params_json": {"export_id": export.id}})
    with repository.atomic(conn):
        repository.insert(conn, job, export)
        detail: dict[str, object] = {
            "export_id": export.id,
            "format": request.format.value,
            "source": request.source_kind.value,
        }
        if request_id is not None:
            detail["request_id"] = request_id
        jobs.write_event(conn, job.id, None, "export_queued", EventLevel.INFO, detail)  # type: ignore[arg-type]
    return export.id


def _manifest(
    conn: sqlite3.Connection,
    export: Export,
    draft: ExportStartRequest,
    plan_: Plan,
    row_count: int,
    digest: str,
    size: int,
    now: datetime,
) -> dict[str, object]:
    source: dict[str, object]
    if plan_.run is not None:
        versions = conn.execute(
            "SELECT ip.name AS icp_name, iv.version AS icp_version, sm.key AS model_key, "
            "mv.version AS model_version, sr.engine_version FROM score_runs sr "
            "JOIN icp_profile_versions iv ON iv.id = sr.icp_profile_version_id "
            "JOIN icp_profiles ip ON ip.id = iv.icp_profile_id "
            "JOIN scoring_model_versions mv ON mv.id = sr.scoring_model_version_id "
            "JOIN scoring_models sm ON sm.id = mv.scoring_model_id WHERE sr.id = ?",
            (plan_.run.id,),
        ).fetchone()
        source = {
            "kind": "query",
            "score_run_id": plan_.run.id,
            "view_id": draft.view_id,
            "query": draft.query.model_dump(mode="json") if draft.query else None,
            "icp_profile": f"{versions['icp_name']} v{versions['icp_version']}",
            "scoring_model": f"{versions['model_key']} v{versions['model_version']}",
            "scoring_engine": versions["engine_version"],
        }
    else:
        if plan_.snapshot is None:
            raise ExportError("a snapshot export names its snapshot")
        snapshot = plan_.snapshot
        source = {
            "kind": "snapshot",
            "snapshot_id": snapshot.id,
            "version": snapshot.version,
            "schema_version": snapshot.schema_version,
            "dataset_id": snapshot.dataset_id,
            "cutoff_at": format_ts(snapshot.cutoff_at or snapshot.created_at),
            "pipeline_versions": snapshot.pipeline_versions_json,
        }
    return {
        "schema_version": 1,
        "export_id": export.id,
        "file": export.file_name,
        "format": draft.format.value,
        "sha256": digest,
        "bytes": size,
        "row_count": row_count,
        "columns": export.columns_json,
        "source": source,
        "include_suppressed": draft.include_suppressed,
        "suppressed_reason": draft.suppressed_reason,
        "excluded_suppressed": plan_.skipped[0],
        "hypotheses_exported": False,
        "options": {"bom": draft.bom},
        "preset": {"key": draft.preset_key, "version": draft.preset_version}
        if draft.preset_key
        else None,
        "reproducible": plan_.kind is ExportSourceKind.SNAPSHOT,
        "exported_at": format_ts(now),
        "actor": export.actor,
        "app_version": __version__,
    }


def write_export(
    conn: sqlite3.Connection,
    data_dir: Path,
    export: Export,
    should_stop: Callable[[], bool],
    progress: Progress,
    now: datetime,
) -> tuple[int, str, dict[str, object]]:
    """Writes the file and its manifest atomically; a stop or a failure removes the partials."""
    draft = ExportStartRequest.model_validate(export.config_json["draft"])
    destination = Path(str(export.destination))
    partial, manifest_path, manifest_partial = partial_paths(destination)
    plan_ = plan(conn, draft)
    try:
        source = rows(conn, data_dir, plan_, draft, now, should_stop)
        headers, types = plan_.headers, plan_.types
        match draft.format:
            case ExportFormat.CSV:
                count = write_csv(partial, headers, source, draft.bom, progress)
            case ExportFormat.JSON | ExportFormat.JSONL:
                count = write_json(
                    partial, headers, source, draft.format is ExportFormat.JSONL, progress
                )
            case ExportFormat.XLSX:
                count = write_xlsx(partial, headers, source, progress)
            case ExportFormat.PARQUET:
                count = write_parquet(partial, headers, types, source, progress)
        if should_stop():
            raise ExportStopped
        digest = sha256_of(partial)
        manifest = _manifest(conn, export, draft, plan_, count, digest, partial.stat().st_size, now)
        with manifest_partial.open("wb") as handle:
            handle.write(orjson.dumps(manifest, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS))
            handle.flush()
            os.fsync(handle.fileno())
        partial.replace(destination)
        manifest_partial.replace(manifest_path)
    except OSError as exc:
        raise ExportFailure(io_code(exc), str(exc)) from exc
    except duckdb.Error as exc:
        raise ExportFailure("io_error", str(exc)) from exc
    finally:
        partial.unlink(missing_ok=True)
        manifest_partial.unlink(missing_ok=True)
        destination.with_name(destination.name + ".partial.jsonl").unlink(missing_ok=True)
    return count, digest, manifest


def _finish_export(
    conn: sqlite3.Connection, export_id: str, status: ExportStatus, **fields: object
) -> None:
    now = format_ts(utc_now())
    assignments = "".join(f"{name} = ?, " for name in fields)
    with repository.atomic(conn):
        current = repository.get(conn, Export, export_id).status
        if current is ExportStatus.PENDING and status is not ExportStatus.CANCELLED:
            repository.change_status(conn, Export, export_id, ExportStatus.RUNNING, actor=ACTOR)
        repository.change_status(conn, Export, export_id, status, actor=ACTOR)
        conn.execute(
            f"UPDATE exports SET {assignments}finished_at = ?, updated_at = ? "  # noqa: S608
            "WHERE id = ?",
            [
                *(
                    orjson.dumps(value).decode() if isinstance(value, dict) else value
                    for value in fields.values()
                ),
                now,
                now,
                export_id,
            ],
        )


def _close_job(
    conn: sqlite3.Connection,
    job_id: str,
    status: JobStatus,
    event: str,
    code: str | None = None,
    message: str | None = None,
) -> None:
    now = format_ts(utc_now())
    with repository.atomic(conn):
        current = repository.get(conn, Job, job_id).status
        if current is JobStatus.QUEUED and status is not JobStatus.CANCELLED:
            repository.change_status(conn, Job, job_id, JobStatus.RUNNING, actor=ACTOR)
        if current is JobStatus.PAUSED and status is not JobStatus.CANCELLED:
            status = JobStatus.CANCELLED
        repository.change_status(conn, Job, job_id, status, actor=ACTOR)
        conn.execute(
            "UPDATE jobs SET started_at = coalesce(started_at, ?), finished_at = ?, updated_at = ? "
            "WHERE id = ?",
            (now, now, now, job_id),
        )
        level = EventLevel.ERROR if code else EventLevel.INFO
        jobs.write_event(
            conn,
            job_id,
            None,
            event,
            level,
            {"message": message} if message else None,
            code,
            False if code else None,
        )


def recover_exports(conn: sqlite3.Connection) -> int:
    """At startup: an export a dead worker left open never resumes mid-file. Its partial files are
    removed and it is recorded as interrupted; start it again."""
    rows_ = conn.execute(
        "SELECT e.id, e.job_id, e.destination FROM exports e JOIN jobs j ON j.id = e.job_id "
        "WHERE j.status IN ('queued', 'running', 'paused')"
    ).fetchall()
    for row in rows_:
        if row["destination"]:
            for path in partial_paths(Path(row["destination"]))[::2]:
                with contextlib.suppress(OSError):
                    path.unlink(missing_ok=True)
        _finish_export(
            conn,
            row["id"],
            ExportStatus.FAILED,
            error_code="interrupted",
            error_message="the worker stopped while the file was written",
        )
        _close_job(conn, row["job_id"], JobStatus.CANCELLED, "export_interrupted")
    return len(rows_)


class ExportService(jobs.BackgroundJobs[ExportItem, ExportPage]):
    """Writes exports on threads; a cancel stops between batches and cleans up."""

    kind = JOB_KIND
    actor = ACTOR

    def __init__(self, data_dir: Path | None, connect: Callable[[], sqlite3.Connection]) -> None:
        super().__init__(connect)
        self._data_dir = data_dir

    async def start(
        self, request: ExportStartRequest, *, actor: str, request_id: str | None
    ) -> ExportItem:
        export_id = await self._in_store(
            lambda conn: plan_export(
                conn, self._data_dir, request, actor=actor, request_id=request_id, now=utc_now()
            )
        )
        item = await self._in_store(lambda conn: export_item(conn, export_id))
        if item.export.job_id is not None:
            self._spawn(item.export.job_id)
        return item

    async def cancel(self, export_id: str) -> ExportItem:
        def apply(conn: sqlite3.Connection) -> str | None:
            export = repository.get(conn, Export, export_id)
            if export.job_id is None:
                return None
            job = repository.get(conn, Job, export.job_id)
            if job.status in (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.PAUSED):
                jobs.apply_control(conn, job.id, RunControlAction.CANCEL, ACTOR)
            if export.status is ExportStatus.PENDING:
                _finish_export(conn, export_id, ExportStatus.CANCELLED, error_code="cancelled")
            return job.id

        job_id = await self._in_store(apply)
        if job_id is not None:
            await self._halt(job_id)
        return await self._in_store(lambda conn: export_item(conn, export_id))

    async def recover(self) -> int:
        return await self._in_store(recover_exports)

    def plan(self, conn: sqlite3.Connection, project_id: str | None) -> tuple[list[str], list[str]]:
        raise ExportError("an export is started with its request")

    def view(self, conn: sqlite3.Connection, job_id: str) -> ExportItem:
        (export_id,) = conn.execute("SELECT id FROM exports WHERE job_id = ?", (job_id,)).fetchone()
        return export_item(conn, export_id)

    def page(self, conn: sqlite3.Connection, job_ids: Sequence[str]) -> ExportPage:
        items = [self.view(conn, job_id) for job_id in job_ids]
        return ExportPage(items=items, next_cursor=None, total=len(items))

    def run(self, conn: sqlite3.Connection, job_id: str, should_stop: Callable[[], bool]) -> bool:
        job = repository.get(conn, Job, job_id)
        row = conn.execute("SELECT id FROM exports WHERE job_id = ?", (job_id,)).fetchone()
        if row is None or job.status is not JobStatus.QUEUED or self._data_dir is None:
            return False
        export = repository.get(conn, Export, row["id"])
        if export.status is not ExportStatus.PENDING:
            return False
        now = utc_now()
        with repository.atomic(conn):
            jobs.mark_running(conn, job_id, ACTOR)
            repository.change_status(conn, Export, export.id, ExportStatus.RUNNING, actor=ACTOR)
            conn.execute(
                "UPDATE exports SET started_at = ? WHERE id = ?", (format_ts(now), export.id)
            )
        writer = self._connect()  # the reading cursor stays open while progress is written

        def progress(done: int) -> None:
            with repository.atomic(writer):
                jobs.set_progress(writer, job_id, done, job.progress_total)

        try:
            count, digest, manifest = write_export(
                conn, self._data_dir, export, should_stop, progress, now
            )
        except ExportStopped:
            cancelled = repository.get(conn, Job, job_id).status is JobStatus.CANCELLED
            _finish_export(
                conn,
                export.id,
                ExportStatus.CANCELLED if cancelled else ExportStatus.FAILED,
                error_code="cancelled" if cancelled else "interrupted",
            )
            if not cancelled:
                _close_job(conn, job_id, JobStatus.CANCELLED, "export_interrupted")
            return False
        except ExportFailure as exc:
            _finish_export(
                conn,
                export.id,
                ExportStatus.FAILED,
                error_code=exc.code,
                error_message=str(exc)[:2000],
            )
            _close_job(conn, job_id, JobStatus.FAILED, "export_failed", exc.code, str(exc)[:2000])
            return True
        except Exception as exc:
            logger.exception("export failed", extra={"export_id": export.id})
            _finish_export(
                conn,
                export.id,
                ExportStatus.FAILED,
                error_code="internal",
                error_message=f"{type(exc).__name__}: {exc}"[:2000],
            )
            _close_job(conn, job_id, JobStatus.FAILED, "export_failed", "internal", str(exc)[:2000])
            return True
        finally:
            writer.close()
        _finish_export(
            conn,
            export.id,
            ExportStatus.SUCCEEDED,
            row_count=count,
            file_sha256=digest,
            manifest_json=manifest,
        )
        with repository.atomic(conn):
            jobs.set_progress(conn, job_id, count, max(count, job.progress_total or 0))
            repository.audit(
                conn,
                AuditAction.CREATE,
                EntityType.EXPORT,
                export.id,
                actor=export.actor,
                detail={"file": export.file_name, "rows": count, "sha256": digest},
            )
        _close_job(conn, job_id, JobStatus.SUCCEEDED, "export_succeeded")
        return True
