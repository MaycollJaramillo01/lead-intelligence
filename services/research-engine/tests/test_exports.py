"""Local exports (phase 13): writers, destinations, presets, previews, the job and its failure
modes."""

import codecs
import csv
import errno
import hashlib
import io
import json
import sqlite3
import tracemalloc
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import duckdb
import pytest
from pydantic import ValidationError

from research_engine import datasets, exports, jobs, repository
from research_engine.contacts import store as contacts
from research_engine.domain import (
    AuditAction,
    EntityType,
    ExplorerQuery,
    Export,
    ExportCatalogQuery,
    ExportColumn,
    ExportColumnType,
    ExportDefinition,
    ExportDraft,
    ExportFormat,
    ExportListQuery,
    ExportPresetSaveRequest,
    ExportSourceKind,
    ExportStartRequest,
    ExportStatus,
    Job,
    JobStatus,
    SuppressionChangeRequest,
    SuppressionKind,
    SyntheticSeedResult,
    no_filters,
    utc_now,
)
from research_engine.leads import explorer
from research_engine.testdata import clone_lead_index, seed_synthetic_workspace

_T = ExportColumnType
HEADERS = ["name", "city", "score", "flag", "seen", "tags"]
TYPES = [_T.TEXT, _T.TEXT, _T.NUMBER, _T.FLAG, _T.TIMESTAMP, _T.LIST]
ROWS: list[list[object]] = [
    [
        '=HYPERLINK("http://example.invalid")',
        "Niño",
        12.5,
        True,
        "2026-01-01T00:00:00.000Z",
        ["a", "b"],
    ],
    ["+SUM(1,1)", "東京" + chr(7), -3.0, False, None, []],
]
_nothing = lambda _done: None  # noqa: E731 - a progress callback that ignores progress


@pytest.fixture
def seeded(conn: sqlite3.Connection, data_dir: Path) -> SyntheticSeedResult:
    return seed_synthetic_workspace(conn, data_dir)


@pytest.fixture
def out(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A folder outside the data directory, as a save dialog would pick."""
    return tmp_path_factory.mktemp("exports-out")


def _query_draft(
    export_format: ExportFormat,
    columns: tuple[tuple[str, str], ...] = (
        ("name", "Business"),
        ("tier", "Tier"),
        ("total_score", "Score"),
        ("main_channel", "Channel"),
    ),
    **fields: Any,
) -> ExportDraft:
    query = ExplorerQuery(
        score_run_id=None,
        filters=no_filters(),
        search=None,
        sort=[],
        offset=0,
        limit=100,
        with_total=False,
    )
    values: dict[str, Any] = {
        "source_kind": ExportSourceKind.QUERY,
        "query": query,
        "view_id": None,
        "snapshot_id": None,
        "format": export_format,
        "columns": [ExportColumn(key=k, header=h) for k, h in columns],
        "bom": True,
        "include_suppressed": False,
        "suppressed_reason": None,
        "preset_key": None,
        "preset_version": None,
    }
    return ExportDraft.model_validate(values | fields)


def _start(draft: ExportDraft, destination: Path) -> ExportStartRequest:
    return ExportStartRequest.model_validate(draft.model_dump() | {"destination": str(destination)})


def _suppress_first_lead(conn: sqlite3.Connection) -> str:
    run = explorer.run_of(conn, None)
    assert run is not None
    (business_id,) = conn.execute(
        "SELECT business_id FROM lead_index WHERE score_run_id = ? ORDER BY rank LIMIT 1", (run.id,)
    ).fetchone()
    if business_id not in explorer.suppressed_roots(conn):
        contacts.change_suppression(
            conn,
            SuppressionChangeRequest(
                suppression_id=None,
                kind=SuppressionKind.BUSINESS,
                value=business_id,
                reason="Asked to be left alone (test)",
            ),
            actor="test",
        )
    return str(business_id)


def test_spreadsheets_make_formulas_inert_and_every_format_keeps_unicode(tmp_path: Path) -> None:
    csv_path = tmp_path / "leads.csv"
    assert exports.write_csv(csv_path, HEADERS, iter(ROWS), True, _nothing) == 2
    raw = csv_path.read_bytes()
    assert raw.startswith(codecs.BOM_UTF8)
    text = raw.decode("utf-8-sig")
    assert "'=HYPERLINK" in text
    assert "'+SUM" in text
    assert "Niño" in text
    assert "東京" in text
    assert chr(7) not in text
    assert ",-3.0," in text  # a number is never taken for a formula
    assert "a | b" in text

    first, second = tmp_path / "one.xlsx", tmp_path / "two.xlsx"
    for path in (first, second):
        assert exports.write_xlsx(path, HEADERS, iter(ROWS), _nothing) == 2
    assert first.read_bytes() == second.read_bytes()  # the same rows make the same bytes
    with zipfile.ZipFile(first) as book:
        sheet = ElementTree.fromstring(book.read("xl/worksheets/sheet1.xml"))  # noqa: S314 - written here
        assert set(book.namelist()) == {
            "[Content_Types].xml",
            "_rels/.rels",
            "xl/workbook.xml",
            "xl/_rels/workbook.xml.rels",
            "xl/worksheets/sheet1.xml",
        }
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rows = sheet.findall(".//s:row", ns)
    assert len(rows) == 3
    texts = [node.text for node in sheet.findall(".//s:t", ns)]
    assert '\'=HYPERLINK("http://example.invalid")' in texts
    assert "東京" in texts

    json_path, lines_path = tmp_path / "leads.json", tmp_path / "leads.jsonl"
    assert exports.write_json(json_path, HEADERS, iter(ROWS), False, _nothing) == 2
    assert exports.write_json(lines_path, HEADERS, iter(ROWS), True, _nothing) == 2
    records = json.loads(json_path.read_text("utf-8"))
    assert records[1]["city"] == "東京" + chr(7)  # data formats keep the value exactly
    assert records[0]["name"].startswith("=")
    assert len(lines_path.read_text("utf-8").splitlines()) == 2


def test_parquet_columns_have_explicit_types(tmp_path: Path) -> None:
    path = tmp_path / "leads.parquet"
    assert exports.write_parquet(path, HEADERS, TYPES, iter(ROWS), _nothing) == 2
    assert not path.with_name(path.name + ".jsonl").exists()
    duck = duckdb.connect()
    try:
        described = {
            row[0]: row[1]
            for row in duck.execute(
                f"DESCRIBE SELECT * FROM read_parquet('{path.as_posix()}')"  # noqa: S608
            ).fetchall()
        }
        (seen,) = duck.execute(
            f"SELECT seen FROM read_parquet('{path.as_posix()}') LIMIT 1"  # noqa: S608
        ).fetchone() or (None,)
    finally:
        duck.close()
    assert described == {
        "name": "VARCHAR",
        "city": "VARCHAR",
        "score": "DOUBLE",
        "flag": "BOOLEAN",
        "seen": "TIMESTAMP",
        "tags": "VARCHAR[]",
    }
    assert str(seen) == "2026-01-01 00:00:00"


def test_cells_stay_within_what_a_spreadsheet_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert exports.spreadsheet_cell("@SUM(A1)") == "'@SUM(A1)"
    assert exports.spreadsheet_cell(chr(9) + "=1") == "'" + chr(9) + "=1"
    assert exports.spreadsheet_cell(-5) == -5
    assert exports.spreadsheet_cell(float("nan")) is None
    assert len(str(exports.spreadsheet_cell("x" * 40_000))) == exports.MAX_CELL_CHARS
    monkeypatch.setattr(exports, "XLSX_MAX_ROWS", 1)
    with pytest.raises(exports.ExportFailure) as refused:
        exports.write_xlsx(tmp_path / "big.xlsx", HEADERS, iter(ROWS), _nothing)
    assert refused.value.code == "too_many_rows"


def test_a_destination_is_a_local_file_outside_the_app_stores(data_dir: Path, out: Path) -> None:
    assert (
        exports.check_destination(data_dir, str(out / "leads.csv"), ExportFormat.CSV)
        == out / "leads.csv"
    )
    (data_dir / "exports").mkdir()
    inside = data_dir / "exports" / "leads.csv"
    assert exports.check_destination(data_dir, str(inside), ExportFormat.CSV) == inside
    for destination, message in (
        ("leads.csv", "on this computer"),
        (r"\\server\share\leads.csv", "on this computer"),
        (str(out / "missing" / "leads.csv"), "does not exist"),
        (str(out / "leads.txt"), "ends in"),
        (str(data_dir / "lead.csv"), "data folder"),
    ):
        with pytest.raises(exports.ExportError, match=message):
            exports.check_destination(data_dir, destination, ExportFormat.CSV)


def test_presets_are_versioned_and_cloned_and_a_built_in_never_changes(
    conn: sqlite3.Connection,
) -> None:
    catalog = exports.catalog(conn, ExportCatalogQuery(include_superseded=False))
    built_in = {view.preset.key for view in catalog.presets if view.preset.built_in}
    assert built_in == {
        "sales_ready_concise",
        "full_evidence_audit",
        "contact_review",
        "website_opportunity_report",
        "dataset_analytical",
    }
    exports.ensure_presets(conn)
    assert conn.execute("SELECT count(*) FROM export_presets").fetchone()[0] == 5
    assert {
        info.key for info in catalog.appended if info.source_kind is ExportSourceKind.QUERY
    } >= {
        "exported_at",
        "do_not_contact",
        "contact_unverified",
    }
    assert all(info.description for info in catalog.columns)

    review = next(view for view in catalog.presets if view.preset.key == "contact_review")
    definition = review.definition.model_copy(update={"format": ExportFormat.CSV})
    clone = ExportPresetSaveRequest(
        key=None,
        clone_from="contact_review",
        name="Contact review TX",
        description="Cloned (test)",
        definition=definition,
    )
    exports.save_preset(conn, clone, actor="tester")
    saved = exports.catalog(conn, ExportCatalogQuery(include_superseded=False))
    custom = next(view for view in saved.presets if not view.preset.built_in)
    assert (custom.preset.key, custom.preset.version, custom.cloned_from_key) == (
        "custom_contact_review_tx",
        1,
        "contact_review",
    )
    same = clone.model_copy(update={"key": custom.preset.key, "clone_from": None})
    exports.save_preset(conn, same, actor="tester")  # unchanged: no version
    exports.save_preset(
        conn, same.model_copy(update={"name": "Contact review (Texas)"}), actor="tester"
    )
    history = exports.catalog(conn, ExportCatalogQuery(include_superseded=True))
    versions = [
        view.preset.version for view in history.presets if view.preset.key == custom.preset.key
    ]
    assert versions == [2, 1]
    with pytest.raises(exports.ExportError, match="built-in"):
        exports.save_preset(conn, same.model_copy(update={"key": "contact_review"}), actor="tester")
    clashing = definition.model_copy(
        update={"columns": [ExportColumn(key="name", header="exported_at")]}
    )
    with pytest.raises(exports.ExportError, match="appended"):
        exports.save_preset(conn, clone.model_copy(update={"definition": clashing}), actor="tester")
    with pytest.raises(sqlite3.DatabaseError, match="permanent"):
        conn.execute("DELETE FROM export_presets")
    with pytest.raises(ValidationError):
        ExportDefinition(
            source_kind=ExportSourceKind.QUERY,
            format=ExportFormat.CSV,
            bom=True,
            columns=[ExportColumn(key="name", header="A"), ExportColumn(key="name", header="B")],
        )


@pytest.mark.anyio
async def test_an_export_writes_its_file_and_manifest_atomically_and_leaves_suppressed_leads_out(
    conn: sqlite3.Connection, data_dir: Path, seeded: SyntheticSeedResult, out: Path
) -> None:
    suppressed = _suppress_first_lead(conn)
    draft = _query_draft(ExportFormat.CSV)
    preview = exports.preview(conn, data_dir, draft, utc_now())
    assert preview.excluded_suppressed >= 1
    assert preview.exported == preview.matching - preview.excluded_suppressed
    assert {"hypotheses_never_exported", "synthetic_rows"} <= set(preview.warnings)
    assert preview.headers[:4] == ["Business", "Tier", "Score", "Channel"]
    assert preview.headers[-1] == "do_not_contact"

    service = exports.ExportService(data_dir, lambda: repository.connect(data_dir))
    item = await service.start(_start(draft, out / "leads.csv"), actor="tester", request_id=None)
    assert item.export.job_id is not None
    await service.idle(item.export.job_id)
    done = exports.export_item(conn, item.export.id)
    assert (done.export.status, done.job_status) == (ExportStatus.SUCCEEDED, JobStatus.SUCCEEDED)
    written = sorted(path.name for path in out.iterdir())  # noqa: ASYNC240
    assert written == ["leads.csv", "leads.csv.manifest.json"]
    data = (out / "leads.csv").read_bytes()
    assert hashlib.sha256(data).hexdigest() == done.export.file_sha256
    manifest = json.loads((out / "leads.csv.manifest.json").read_text("utf-8"))
    assert manifest["sha256"] == done.export.file_sha256
    assert manifest["row_count"] == done.export.row_count == preview.exported
    assert (manifest["excluded_suppressed"], manifest["hypotheses_exported"]) == (
        preview.excluded_suppressed,
        False,
    )
    assert manifest["source"]["kind"] == "query"
    rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
    assert len(rows) == preview.exported
    assert {row["do_not_contact"] for row in rows} == {"false"}
    assert suppressed not in {row["business_id"] for row in rows}
    (audited,) = conn.execute(
        "SELECT count(*) FROM audit_log WHERE entity_type = ? AND entity_id = ? AND action = ?",
        (EntityType.EXPORT.value, done.export.id, AuditAction.CREATE.value),
    ).fetchone()
    assert audited == 1
    page = exports.list_exports(conn, ExportListQuery(project_id=None, limit=10, cursor=None))
    assert [entry.export.id for entry in page.items] == [done.export.id]


def test_including_do_not_contact_leads_needs_a_reason_and_marks_them(
    conn: sqlite3.Connection, data_dir: Path, seeded: SyntheticSeedResult
) -> None:
    _suppress_first_lead(conn)
    with pytest.raises(ValidationError, match="reason"):
        _query_draft(ExportFormat.CSV, include_suppressed=True)
    draft = _query_draft(
        ExportFormat.CSV,
        include_suppressed=True,
        suppressed_reason="A legal review of the list (test)",
    )
    preview = exports.preview(conn, data_dir, draft, utc_now())
    assert preview.excluded_suppressed == 0
    assert "suppressed_included" in preview.warnings
    produced = list(exports.rows(conn, data_dir, exports.plan(conn, draft), draft, utc_now()))
    assert sum(1 for values in produced if values[-1] is True) >= 1


@pytest.mark.anyio
async def test_a_snapshot_export_is_reproducible_byte_for_byte(
    conn: sqlite3.Connection, data_dir: Path, seeded: SyntheticSeedResult, out: Path
) -> None:
    snapshot = datasets.snapshot_businesses(
        conn, data_dir, project_id=seeded.project_id, dataset_name="Fixture dataset", actor="test"
    )
    service = exports.ExportService(data_dir, lambda: repository.connect(data_dir))
    for export_format in (ExportFormat.PARQUET, ExportFormat.XLSX):
        draft = ExportDraft(
            source_kind=ExportSourceKind.SNAPSHOT,
            query=None,
            view_id=None,
            snapshot_id=snapshot.id,
            format=export_format,
            bom=False,
            include_suppressed=False,
            suppressed_reason=None,
            columns=[ExportColumn(key=key, header=key) for key in exports.SNAPSHOT_COLUMNS],
            preset_key="dataset_analytical",
            preset_version=1,
        )
        preview = exports.preview(conn, data_dir, draft, utc_now())
        digests = []
        for name in ("one", "two"):
            destination = out / f"{name}{exports.EXTENSIONS[export_format]}"
            item = await service.start(_start(draft, destination), actor="tester", request_id=None)
            assert item.export.job_id is not None
            await service.idle(item.export.job_id)
            done = exports.export_item(conn, item.export.id)
            assert done.export.status is ExportStatus.SUCCEEDED, done.export.error_message
            assert (done.export.dataset_snapshot_id, done.export.row_count) == (
                snapshot.id,
                preview.exported,
            )
            digests.append(done.export.file_sha256)
            manifest = json.loads(
                destination.with_name(destination.name + ".manifest.json").read_text("utf-8")
            )
            assert (manifest["reproducible"], manifest["source"]["snapshot_id"]) == (
                True,
                snapshot.id,
            )
        assert digests[0] == digests[1]


def test_a_query_export_carries_the_number_to_call(
    conn: sqlite3.Connection, data_dir: Path, seeded: SyntheticSeedResult
) -> None:
    """`main_channel` names the kind of the route only, so a list exported without the value
    cannot be called. A number on the do-not-contact list is still no route."""
    draft = _query_draft(
        ExportFormat.CSV,
        columns=(("name", "Business"), ("phone", "Phone"), ("email", "Email")),
    )
    preview = exports.preview(conn, data_dir, draft, utc_now())
    assert preview.headers[:3] == ["Business", "Phone", "Email"]
    calls = [row[1] for row in preview.sample if row[1]]
    published = {
        value
        for (value,) in conn.execute(
            "SELECT value_raw FROM contact_points WHERE contact_type = 'phone' "
            "AND do_not_contact = 0 AND origin <> 'hypothesis'"
        )
    }
    assert calls
    assert set(calls) <= published

    conn.execute(
        "UPDATE contact_points SET do_not_contact = 1, suppression_reason = ? WHERE value_raw = ?",
        ("asked to be left alone (test)", calls[0]),
    )
    again = exports.preview(conn, data_dir, draft, utc_now())
    assert all(row[1] != calls[0] for row in again.sample)


def test_a_column_writes_the_words_the_screen_gave_for_its_codes(
    conn: sqlite3.Connection, data_dir: Path, seeded: SyntheticSeedResult
) -> None:
    """The file of a search speaks the language of its screen (phase 16): a code with a label is
    written as the label, one without stays as it is, and a list is labelled item by item."""
    base = _query_draft(ExportFormat.CSV)
    draft = ExportDraft.model_validate(
        base.model_dump()
        | {
            "columns": [
                {"key": "name", "header": "Negocio", "labels": None},
                {"key": "tier", "header": "Prioridad", "labels": {"low": "Baja prioridad"}},
                {"key": "problem", "header": "Problemas", "labels": {"no_website": "Sin web"}},
            ]
        }
    )
    rows = list(exports.query_rows(conn, exports.plan(conn, draft), draft, utc_now()))
    tiers = {row[1] for row in rows}
    assert "Baja prioridad" in tiers
    assert "low" not in tiers
    assert "insufficient_data" in tiers  # no label: the code itself
    problems = {item for row in rows if isinstance(row[2], list) for item in row[2]}
    assert "Sin web" in problems
    assert "no_website" not in problems
    assert "weak_local_seo" in problems or "weak_conversion_path" in problems

    # presets and drafts stored before labels existed are still read
    assert ExportColumn.model_validate({"key": "tier", "header": "Tier"}).labels is None
    with pytest.raises(ValidationError):
        ExportColumn(key="tier", header="Tier", labels={"Not a code": "x"})


def test_a_stop_a_full_disk_or_a_refused_folder_leaves_no_partial_file(
    conn: sqlite3.Connection,
    data_dir: Path,
    seeded: SyntheticSeedResult,
    out: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = out / "leads.csv"
    export_id = exports.plan_export(
        conn,
        data_dir,
        _start(_query_draft(ExportFormat.CSV), destination),
        actor="tester",
        request_id=None,
        now=utc_now(),
    )
    export = repository.get(conn, Export, export_id)
    with pytest.raises(exports.ExportStopped):
        exports.write_export(conn, data_dir, export, lambda: True, _nothing, utc_now())
    assert list(out.iterdir()) == []

    def failing(error: OSError) -> Any:
        def write(path: Path, *_: object) -> int:
            path.write_bytes(b"half a file")
            raise error

        return write

    for error, code in (
        (OSError(errno.ENOSPC, "No space left on device"), "disk_full"),
        (PermissionError(errno.EACCES, "Access is denied"), "permission_denied"),
    ):
        monkeypatch.setattr(exports, "write_csv", failing(error))
        with pytest.raises(exports.ExportFailure) as failed:
            exports.write_export(conn, data_dir, export, lambda: False, _nothing, utc_now())
        assert failed.value.code == code
        assert list(out.iterdir()) == []


@pytest.mark.anyio
async def test_a_failed_export_is_recorded_with_its_cause(
    conn: sqlite3.Connection,
    data_dir: Path,
    seeded: SyntheticSeedResult,
    out: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def full(path: Path, *_: object) -> int:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(exports, "write_csv", full)
    service = exports.ExportService(data_dir, lambda: repository.connect(data_dir))
    item = await service.start(
        _start(_query_draft(ExportFormat.CSV), out / "leads.csv"), actor="tester", request_id=None
    )
    assert item.export.job_id is not None
    await service.idle(item.export.job_id)
    failed = exports.export_item(conn, item.export.id)
    assert (failed.export.status, failed.export.error_code, failed.job_status) == (
        ExportStatus.FAILED,
        "disk_full",
        JobStatus.FAILED,
    )
    (code,) = conn.execute(
        "SELECT error_code FROM job_events WHERE job_id = ? AND level = 'error'",
        (item.export.job_id,),
    ).fetchone()
    assert code == "disk_full"


def test_an_export_a_restart_interrupted_removes_its_partial_file(
    conn: sqlite3.Connection, data_dir: Path, seeded: SyntheticSeedResult, out: Path
) -> None:
    destination = out / "leads.csv"
    export_id = exports.plan_export(
        conn,
        data_dir,
        _start(_query_draft(ExportFormat.CSV), destination),
        actor="tester",
        request_id=None,
        now=utc_now(),
    )
    export = repository.get(conn, Export, export_id)
    assert export.job_id is not None
    jobs.mark_running(conn, export.job_id, "test")
    repository.change_status(conn, Export, export_id, ExportStatus.RUNNING, actor="test")
    partial = destination.with_name(destination.name + ".partial")
    partial.write_bytes(b"half a file")
    assert exports.recover_exports(conn) == 1
    assert not partial.exists()
    recovered = exports.export_item(conn, export_id)
    assert (recovered.export.status, recovered.export.error_code) == (
        ExportStatus.FAILED,
        "interrupted",
    )
    assert repository.get(conn, Job, export.job_id).status is JobStatus.CANCELLED


def test_a_large_export_streams_in_bounded_memory(
    conn: sqlite3.Connection, data_dir: Path, seeded: SyntheticSeedResult, out: Path
) -> None:
    run = explorer.run_of(conn, None)
    assert run is not None
    clone_lead_index(conn, run.id, 50_000)
    draft = _query_draft(
        ExportFormat.JSONL, (("name", "name"), ("tier", "tier"), ("total_score", "score"))
    )
    export_id = exports.plan_export(
        conn,
        data_dir,
        _start(draft, out / "big.jsonl"),
        actor="tester",
        request_id=None,
        now=utc_now(),
    )
    export = repository.get(conn, Export, export_id)
    tracemalloc.start()
    count, digest, manifest = exports.write_export(
        conn, data_dir, export, lambda: False, _nothing, utc_now()
    )
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert count >= 50_000
    assert manifest["row_count"] == count
    assert peak < 48 * 1024 * 1024  # windows of 500 leads, never the 50 038 rows at once
    lines = (out / "big.jsonl").read_bytes().count(b"\n")
    assert lines == count
    assert hashlib.sha256((out / "big.jsonl").read_bytes()).hexdigest() == digest
