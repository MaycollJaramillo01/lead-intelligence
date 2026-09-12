"""Local file import: CSV, TSV, JSON and JSON Lines the operator already has.

The only source that needs no network and no terms: the operator supplies the file. What it does
have to get right is everything a real import gets wrong - a missing header, a ragged row, a column
the mapping does not cover, a number where a name was expected. Those are reported per row, with the
line number, and the rest of the file still imports.

The file itself is written once under `imports/<source run>/` and recorded as an `acquired_page`, so
a candidate can be traced back to the exact bytes it came from and a paused run resumes by row
offset without reading the same rows twice.

Not implemented on purpose: XLSX. It is a zip of XML, not text, and nothing here would parse it
honestly today; the UI says so and asks for a CSV export instead.
"""

import csv
import io
from collections.abc import Iterator, Mapping
from typing import Any, Final

import orjson

from research_engine.discovery.normalize import clean, domain_of, website_of
from research_engine.discovery.normalize import dedup_key as make_dedup_key
from research_engine.discovery.sources.base import (
    Candidate,
    DiscoveryError,
    DiscoveryPage,
    Estimate,
    SourceContext,
    SourceRequest,
)
from research_engine.domain import (
    Capability,
    ColumnMapping,
    ConfigField,
    ConfigFieldKind,
    DiscoveryErrorCode,
    ImportFormat,
    ImportPreview,
    ImportPreviewRequest,
    ImportPreviewRow,
    ImportProblem,
    JsonObject,
    SourceKind,
    SourceMetadata,
)

KEY: Final = "file.import"
VERSION: Final = "1.0.0"
PREVIEW_ROWS: Final = 20
MAX_COLUMNS: Final = 200
MAX_VALUE_CHARS: Final = 2000
_DELIMITERS: Final[dict[ImportFormat, str]] = {ImportFormat.CSV: ",", ImportFormat.TSV: "\t"}

#: Header names we recognise without being told, lower-cased and stripped of separators.
_HINTS: Final[dict[str, tuple[str, ...]]] = {
    "name": ("name", "businessname", "companyname", "company", "business", "legalname", "dba"),
    "locality": ("city", "locality", "town", "ciudad"),
    "region": ("state", "region", "province", "estado"),
    "postal_code": ("zip", "zipcode", "postalcode", "postcode", "cp"),
    "country_code": ("country", "countrycode", "pais"),
    "domain": ("domain", "hostname"),
    "website_url": ("website", "url", "web", "site", "weburl", "sitioweb"),
    "phone": ("phone", "telephone", "tel", "phonenumber", "telefono"),
    "category": ("category", "industry", "vertical", "sector", "naics", "categoria"),
    "external_id": ("id", "externalid", "recordid", "sourceid", "uei", "duns"),
    "source_url": ("sourceurl", "listingurl", "profileurl"),
}

EMPTY_MAPPING: Final = ColumnMapping(
    name=None,
    locality=None,
    region=None,
    postal_code=None,
    country_code=None,
    domain=None,
    website_url=None,
    phone=None,
    category=None,
    external_id=None,
    source_url=None,
)


def _fail(code: DiscoveryErrorCode, message: str) -> DiscoveryError:
    return DiscoveryError(code, message)


def detect_format(filename: str, content: str) -> ImportFormat:
    """Extension first, then the first non-blank character. Never guesses silently at run time."""
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    for candidate in ImportFormat:
        if suffix == candidate.value:
            return candidate
    if suffix in ("ndjson", "jsonlines"):
        return ImportFormat.JSONL
    head = content.lstrip()[:1]
    if head == "[":
        return ImportFormat.JSON
    if head == "{":
        return ImportFormat.JSONL
    return ImportFormat.TSV if "\t" in content.split("\n", 1)[0] else ImportFormat.CSV


def _flat(value: Any) -> str:
    """One cell as text. Nested JSON keeps its shape as text instead of being thrown away."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value[:MAX_VALUE_CHARS]
    if isinstance(value, bool | int | float):
        return str(value)
    return orjson.dumps(value).decode()[:MAX_VALUE_CHARS]


def _rows_from_delimited(content: str, delimiter: str) -> tuple[list[str], list[dict[str, str]]]:
    reader = csv.reader(io.StringIO(content, newline=""), delimiter=delimiter)
    try:
        header = next(reader, None)
    except csv.Error as exc:
        raise _fail(DiscoveryErrorCode.INVALID_QUERY, f"the file is not valid CSV: {exc}") from exc
    if not header:
        raise _fail(DiscoveryErrorCode.INVALID_QUERY, "the file has no header row")
    columns = [name.strip() or f"column_{i + 1}" for i, name in enumerate(header[:MAX_COLUMNS])]
    rows: list[dict[str, str]] = []
    try:
        for values in reader:
            if not any(value.strip() for value in values):
                continue  # a blank line between records is not a row
            row = {
                name: _flat(values[i]) if i < len(values) else "" for i, name in enumerate(columns)
            }
            if len(values) > len(columns):
                row["_extra"] = _flat(values[len(columns) :])
            rows.append(row)
    except csv.Error as exc:
        raise _fail(DiscoveryErrorCode.INVALID_QUERY, f"the file is not valid CSV: {exc}") from exc
    return columns, rows


def _objects(content: str, fmt: ImportFormat) -> list[Any]:
    # ponytail: whole-file parse, bounded by MAX_IMPORT_CHARS; stream it when imports get bigger
    if fmt is ImportFormat.JSONL:
        items: list[Any] = []
        for number, line in enumerate(content.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                items.append(orjson.loads(line))
            except orjson.JSONDecodeError as exc:
                raise _fail(
                    DiscoveryErrorCode.INVALID_QUERY, f"line {number} is not valid JSON: {exc}"
                ) from exc
        return items
    try:
        parsed = orjson.loads(content or "[]")
    except orjson.JSONDecodeError as exc:
        raise _fail(DiscoveryErrorCode.INVALID_QUERY, f"the file is not valid JSON: {exc}") from exc
    if isinstance(parsed, dict):
        for key in ("items", "results", "data", "records"):
            if isinstance(parsed.get(key), list):
                return list(parsed[key])
        return [parsed]
    if not isinstance(parsed, list):
        raise _fail(DiscoveryErrorCode.INVALID_QUERY, "the file must hold a list of objects")
    return parsed


def parse(content: str, fmt: ImportFormat) -> tuple[list[str], list[dict[str, str]]]:
    """Columns and rows of a file, as text. Raises `DiscoveryError` only when nothing is usable."""
    if delimiter := _DELIMITERS.get(fmt):
        return _rows_from_delimited(content, delimiter)
    columns: list[str] = []
    rows: list[dict[str, str]] = []
    for item in _objects(content, fmt):
        if not isinstance(item, dict):
            rows.append({"_value": _flat(item)})
            continue
        row = {str(key): _flat(value) for key, value in list(item.items())[:MAX_COLUMNS]}
        for key in row:
            if key not in columns and len(columns) < MAX_COLUMNS:
                columns.append(key)
        rows.append(row)
    if not columns and rows:
        columns = ["_value"]
    return columns, rows


def _hint_key(column: str) -> str:
    return "".join(ch for ch in column.lower() if ch.isalnum())


def suggest_mapping(columns: list[str]) -> ColumnMapping:
    """Matches common header names. Anything unrecognised stays unmapped, never guessed."""
    normalised = {_hint_key(column): column for column in reversed(columns)}
    chosen: dict[str, str | None] = {}
    for field, hints in _HINTS.items():
        chosen[field] = next((normalised[hint] for hint in hints if hint in normalised), None)
    return ColumnMapping.model_validate(chosen)


def _value(row: Mapping[str, str], column: str | None) -> str | None:
    return clean(row.get(column)) if column else None


def candidate_from_row(
    row: Mapping[str, str], mapping: ColumnMapping, origin: str, number: int
) -> Candidate:
    """One row as a candidate. Raises ValueError with the reason when the row cannot be used."""
    name = _value(row, mapping.name)
    if name is None:
        column = mapping.name or "name"
        raise ValueError(f"no business name in column '{column}'")
    website = website_of(_value(row, mapping.website_url))
    domain = _value(row, mapping.domain) or domain_of(website)
    locality = _value(row, mapping.locality)
    region = _value(row, mapping.region)
    external_id = _value(row, mapping.external_id)
    payload: JsonObject = dict(row)
    payload["_row"] = number
    payload["_source_file"] = origin
    return Candidate(
        external_id=external_id or f"{origin}#row={number}",
        source_url=_value(row, mapping.source_url),
        payload_json=payload,
        dedup_key=make_dedup_key(
            name=name,
            locality=locality,
            region=region,
            domain=domain_of(domain),
            external_id=external_id,
        ),
        name_raw=name[:300],
        locality=locality,
        region=region,
        postal_code=_value(row, mapping.postal_code),
        country_code=(_value(row, mapping.country_code) or "")[:2].upper() or None,
        domain=domain_of(domain),
        website_url=website,
        phone_raw=_value(row, mapping.phone),
        category_raw=_value(row, mapping.category),
    )


def preview(request: ImportPreviewRequest) -> ImportPreview:
    """What the file holds and what the mapping would make of it. Stores nothing."""
    fmt = request.format or detect_format(request.filename, request.content)
    columns, rows = parse(request.content, fmt)
    mapping = request.mapping or suggest_mapping(columns)
    problems: list[ImportProblem] = []
    if mapping.name is None:
        problems.append(
            ImportProblem(
                row=None,
                code="name_column_missing",
                message="No column is mapped to the business name; nothing can be imported.",
            )
        )
    elif mapping.name not in columns:
        problems.append(
            ImportProblem(
                row=None,
                code="name_column_unknown",
                message=f"The file has no column named '{mapping.name}'.",
            )
        )
    sample: list[ImportPreviewRow] = []
    usable = 0
    for number, row in enumerate(rows, start=1):
        try:
            candidate = candidate_from_row(row, mapping, request.filename, number)
        except ValueError as exc:
            problem: str | None = str(exc)
            name = None
        else:
            problem, name = None, candidate.name_raw
            usable += 1
        if problem is not None and len(problems) < PREVIEW_ROWS:
            problems.append(ImportProblem(row=number, code="row_unusable", message=problem))
        if len(sample) < PREVIEW_ROWS:
            sample.append(
                ImportPreviewRow(row=number, values=dict(row), name_raw=name, problem=problem)
            )
    return ImportPreview(
        format=fmt,
        columns=columns,
        suggested_mapping=mapping,
        rows=sample,
        total_rows=len(rows),
        usable_rows=usable,
        problems=problems,
    )


def _mapping_of(run_input: Mapping[str, object]) -> ColumnMapping:
    raw = run_input.get("mapping")
    if raw is None:
        return EMPTY_MAPPING
    if not isinstance(raw, dict):
        raise _fail(DiscoveryErrorCode.INVALID_QUERY, "the column mapping must be an object")
    try:
        return ColumnMapping.model_validate(raw)
    except ValueError as exc:
        raise _fail(DiscoveryErrorCode.INVALID_QUERY, f"invalid column mapping: {exc}") from exc


def _read(request: SourceRequest, context: SourceContext) -> tuple[str, str, ImportFormat]:
    path = request.run_input.get("artifact_path")
    if not isinstance(path, str) or not path:
        raise _fail(
            DiscoveryErrorCode.INVALID_QUERY,
            "this source needs an imported file; pick one before starting the run",
        )
    resolved = context.resolve(path)
    try:
        content = resolved.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise _fail(
            DiscoveryErrorCode.SOURCE_UNAVAILABLE, f"the imported file cannot be read: {exc}"
        ) from exc
    raw_format = request.run_input.get("format")
    filename = str(request.run_input.get("filename") or path.rsplit("/", 1)[-1])
    fmt = (
        ImportFormat(raw_format)
        if isinstance(raw_format, str) and raw_format in set(ImportFormat)
        else detect_format(filename, content)
    )
    return content, filename, fmt


def _excluded(candidate: Candidate, request: SourceRequest) -> bool:
    exclusions = request.criteria.exclusions
    name = candidate.name_raw.casefold()
    if any(term.casefold() in name for term in exclusions.names):
        return True
    if candidate.domain and candidate.domain in exclusions.domains:
        return True
    if candidate.postal_code and candidate.postal_code in exclusions.postal_codes:
        return True
    category = (candidate.category_raw or "").casefold()
    return bool(category) and any(term.casefold() in category for term in exclusions.categories)


class FileImportSource:
    """Rows of a file the operator supplied, one page of `page_size` rows at a time."""

    metadata = SourceMetadata(
        key=KEY,
        name="Local file import (CSV, TSV, JSON, JSONL)",
        kind=SourceKind.FILE_IMPORT,
        version=VERSION,
        capabilities=[Capability.PAGINATION, Capability.CHECKPOINT, Capability.ESTIMATE],
        terms_url=None,
        terms_summary=(
            "The operator supplies the file and is responsible for having the right to use it. "
            "Nothing is fetched: the file is copied into the workspace and read from there."
        ),
        requires_credentials=False,
        automation_allowed=True,
        default_requests_per_minute=600,
        default_daily_request_budget=None,
        default_page_size=200,
        config_fields=[
            ConfigField(
                key="file",
                label="File",
                kind=ConfigFieldKind.FILE_TEXT,
                required=True,
                help="CSV, TSV, JSON or JSONL. XLSX is not supported; export it as CSV first.",
            )
        ],
    )

    async def validate_config(self, config: Mapping[str, object]) -> list[str]:
        mapping = config.get("mapping")
        if mapping is not None and not isinstance(mapping, dict):
            return ["the column mapping must be an object"]
        return []

    async def estimate(self, request: SourceRequest, context: SourceContext) -> Estimate:
        try:
            content, _, fmt = _read(request, context)
        except DiscoveryError as exc:
            return Estimate(value=None, basis=f"the file cannot be read yet: {exc.message}")
        _, rows = parse(content, fmt)
        return Estimate(value=len(rows), basis="rows in the imported file, before the mapping")

    async def discover(
        self, request: SourceRequest, cursor: str | None, context: SourceContext
    ) -> DiscoveryPage:
        content, filename, fmt = _read(request, context)
        columns, rows = parse(content, fmt)
        mapping = _mapping_of(request.run_input)
        if mapping.name is None:
            mapping = suggest_mapping(columns)  # no mapping given: fall back to the header names
        offset = _offset(cursor)
        window = rows[offset : offset + request.page_size]
        candidates: list[Candidate] = []
        for index, row in enumerate(window, start=offset + 1):
            try:
                candidate = candidate_from_row(row, mapping, filename, index)
            except ValueError:
                continue  # reported row by row in the preview; a bad row never stops the import
            if not _excluded(candidate, request):
                candidates.append(candidate)
        consumed = offset + len(window)
        return DiscoveryPage(
            candidates=candidates,
            next_cursor=str(consumed) if consumed < len(rows) else None,
            request_url=f"file:{request.run_input.get('artifact_path')}#rows={offset}",
            http_status=None,
            body=None,
            content_type=None,
        )

    def checkpoint(self, cursor: str | None) -> JsonObject:
        return {"row_offset": _offset(cursor)}


def _offset(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        offset = int(cursor)
    except ValueError as exc:
        raise _fail(DiscoveryErrorCode.INVALID_QUERY, f"invalid cursor {cursor!r}") from exc
    if offset < 0:
        raise _fail(DiscoveryErrorCode.INVALID_QUERY, f"invalid cursor {cursor!r}")
    return offset


def rows_of(content: str, fmt: ImportFormat) -> Iterator[dict[str, str]]:
    """Convenience for tests and the seeds source: the rows of a file, already parsed."""
    yield from parse(content, fmt)[1]


SOURCE = FileImportSource()
