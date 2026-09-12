"""Exports the contract JSON Schema and golden fixtures to packages/shared-types.

    python -m research_engine.export_contracts          # (re)write the files
    python -m research_engine.export_contracts --check  # exit 1 if committed files are stale

Fixtures are synthetic samples with obviously fake values. The Rust and TypeScript suites use
them to prove every layer reads and writes the same shapes; they are never product output.
Data-model fixtures are produced by the real repository queries over a temporary store seeded with
synthetic test data, deterministic ids and a fixed clock.
"""

import argparse
import json
import sys
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from itertools import count
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from pydantic.json_schema import models_json_schema

from research_engine import repository
from research_engine.contracts import (
    DATA_CONTRACT_VERSION,
    EXPORTED_MODELS,
    PROTOCOL_VERSION,
    AppEnvironment,
    CommandError,
    CommandErrorCode,
    ComponentState,
    CoreStatus,
    DatabaseStatus,
    DiagnosticsReport,
    DiagnosticsResult,
    Envelope,
    ErrorCode,
    ErrorInfo,
    GpuDevice,
    GpuStatus,
    HealthResult,
    MessageType,
    OllamaModel,
    OllamaStatus,
    UiErrorReport,
    WorkerState,
    WorkerStatus,
)
from research_engine.datasets import BUSINESS_SNAPSHOT_TABLE, SNAPSHOT_SCHEMA_VERSION
from research_engine.discovery import registry
from research_engine.discovery.sources import file_import
from research_engine.domain import (
    DATA_MODELS,
    BusinessListQuery,
    BusinessSort,
    CampaignListQuery,
    CampaignSaved,
    CandidateListQuery,
    CriteriaCheck,
    CriteriaProblem,
    Dataset,
    DatasetListQuery,
    DatasetSnapshot,
    DiscoveryRunListQuery,
    DiscoveryStartRequest,
    ImportPreviewRequest,
    ProblemSeverity,
    SourceCatalog,
    SourceEstimate,
    SourceHealth,
    SourceInfo,
    SyntheticSeedResult,
)
from research_engine.storage import check_sqlite
from research_engine.testdata import (
    SYNTHETIC_DATASET_NAME,
    SYNTHETIC_IMPORT_CSV,
    SYNTHETIC_IMPORT_FILE,
    DiscoveryFixture,
    SyntheticContext,
    seed_businesses,
    seed_discovery,
    synthetic_connector,
    synthetic_project,
    synthetic_run,
)

DEFAULT_OUT = Path(__file__).resolve().parents[4] / "packages" / "shared-types"
_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_NESTED_LISTS = ("anyOf", "allOf", "oneOf", "prefixItems")


def _clean(schema: dict[str, Any], keep_title: bool) -> dict[str, Any]:
    """Drops per-field titles (json-schema-to-typescript would emit one alias type per field)."""
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "title" and not keep_title:
            continue
        if key == "properties":
            out[key] = {name: _clean(sub, False) for name, sub in value.items()}
        elif key == "$defs":
            out[key] = {name: _clean(sub, True) for name, sub in value.items()}
        elif key in _NESTED_LISTS:
            out[key] = [_clean(sub, False) for sub in value]
        elif key in ("items", "additionalProperties") and isinstance(value, dict):
            out[key] = _clean(value, False)
        else:
            out[key] = value
    return out


def build_schema() -> dict[str, Any]:
    models = (*EXPORTED_MODELS, *DATA_MODELS)
    _, top = models_json_schema([(model, "validation") for model in models])
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "LeadIntelligenceContracts",
        "description": (
            "Generated from services/research-engine/src/research_engine/contracts.py and "
            f"domain.py (protocol version {PROTOCOL_VERSION}, data contract version "
            f"{DATA_CONTRACT_VERSION}). Do not edit by hand."
        ),
        "x-protocol-version": PROTOCOL_VERSION,
        "x-data-contract-version": DATA_CONTRACT_VERSION,
        "type": "object",
        "additionalProperties": False,
        "$defs": _clean(top, True)["$defs"],
    }


def _dump(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")


def _envelope(message_type: str, **fields: Any) -> dict[str, Any]:
    values: dict[str, Any] = {"payload": None, "error": None}
    values.update(fields)
    return _dump(
        Envelope(
            request_id=UUID("01900000-0000-7000-8000-000000000001"),
            protocol_version=PROTOCOL_VERSION,
            type=message_type,
            timestamp=_T0,
            **values,
        )
    )


def build_fixtures() -> dict[str, dict[str, Any]]:
    health = HealthResult(
        status="ok",
        worker_version="0.0.0-fixture",
        protocol_version=PROTOCOL_VERSION,
        data_contract_version=DATA_CONTRACT_VERSION,
        python_version="3.12.0",
        pid=4242,
        started_at=_T0,
        uptime_s=12.5,
    )
    diagnostics = DiagnosticsResult(
        health=health,
        sqlite=DatabaseStatus(
            state=ComponentState.OK,
            summary="synthetic fixture: sqlite ok",
            error=None,
            checked_at=_T0,
            duration_ms=1.5,
            engine="sqlite",
            path="C:/synthetic/data/lead.sqlite3",
            engine_version="3.0.0",
            schema_version=1,
            initialized_at="2026-01-01T00:00:00.000Z",
        ),
        duckdb=DatabaseStatus(
            state=ComponentState.ERROR,
            summary="synthetic fixture: duckdb failure",
            error="synthetic failure",
            checked_at=_T0,
            duration_ms=2.25,
            engine="duckdb",
            path="C:/synthetic/data/analytics.duckdb",
            engine_version=None,
            schema_version=None,
            initialized_at=None,
        ),
        ollama=OllamaStatus(
            state=ComponentState.DEGRADED,
            summary="synthetic fixture: ollama running",
            error=None,
            checked_at=_T0,
            duration_ms=3.0,
            installed=True,
            running=True,
            base_url="http://127.0.0.1:11434",
            version="0.0.0",
            models=[
                OllamaModel(
                    name="synthetic-model:latest",
                    size_bytes=1024,
                    digest="sha256:" + "0" * 64,
                    family="synthetic",
                    parameter_size="1B",
                    quantization=None,
                )
            ],
        ),
        gpu=GpuStatus(
            state=ComponentState.UNAVAILABLE,
            summary="synthetic fixture: no gpu",
            error="nvidia-smi not found",
            checked_at=_T0,
            duration_ms=0.5,
            devices=[
                GpuDevice(
                    index=0,
                    name="Synthetic GPU",
                    memory_total_mib=8192,
                    memory_free_mib=None,
                    driver_version=None,
                )
            ],
            cuda_available=False,
            cuda_driver_version=None,
        ),
    )
    core = CoreStatus(
        state=ComponentState.OK,
        summary="synthetic fixture: core ok",
        app_version="0.0.0-fixture",
        environment=AppEnvironment.DEVELOPMENT,
        os="windows",
        arch="x86_64",
        data_dir="C:/synthetic/data",
        log_dir="C:/synthetic/logs",
        config_sources=["embedded:default.toml"],
        config_warnings=[],
        started_at=_T0,
        uptime_s=30.0,
    )
    running = WorkerStatus(
        state=WorkerState.RUNNING,
        pid=4242,
        started_at=_T0,
        restart_count=0,
        last_error=None,
        stderr_tail=[],
        health=health,
    )
    failed = WorkerStatus(
        state=WorkerState.FAILED,
        pid=None,
        started_at=None,
        restart_count=3,
        last_error="synthetic fixture: worker exited with code 1",
        stderr_tail=["Traceback (most recent call last):", "SyntheticError: fixture"],
        health=None,
    )
    return {
        "envelope.request.health.json": _envelope(MessageType.HEALTH, payload={}),
        "envelope.event.ready.json": _envelope(MessageType.WORKER_READY, payload=_dump(health)),
        "envelope.result.diagnostics.json": _envelope(
            "diagnostics.result", payload=_dump(diagnostics)
        ),
        "envelope.result.error.json": _envelope(
            "health.result",
            error=ErrorInfo(code=ErrorCode.TIMEOUT, message="synthetic timeout", retryable=True),
        ),
        "diagnostics_report.json": _dump(
            DiagnosticsReport(
                generated_at=_T0,
                core=core,
                worker=running,
                components=diagnostics,
                components_error=None,
            )
        ),
        "diagnostics_report.worker_down.json": _dump(
            DiagnosticsReport(
                generated_at=_T0,
                core=core,
                worker=failed,
                components=None,
                components_error=CommandError(
                    code=CommandErrorCode.WORKER_UNAVAILABLE,
                    message="synthetic fixture: worker unavailable",
                ),
            )
        ),
        "ui_error_report.json": _dump(
            UiErrorReport(source="fixture", message="synthetic UI error", stack=None)
        ),
        **build_data_fixtures(),
    }


def _fixture_ids() -> Callable[[], str]:
    counter = count(1)
    return lambda: f"01900000-0000-7000-8000-{next(counter):012d}"


def build_data_fixtures() -> dict[str, dict[str, Any]]:
    ids = _fixture_ids()
    business_query = BusinessListQuery(
        project_id=None,
        search="synthetic",
        status=None,
        sort=BusinessSort.NAME,
        limit=2,
        cursor=None,
    )
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        data_dir = Path(tmp)
        check_sqlite(data_dir)
        conn = repository.connect(data_dir)
        try:
            project = synthetic_project(_T0, ids)
            connector = synthetic_connector(_T0, ids)
            run = synthetic_run(project.id, connector.id, _T0, ids)
            repository.insert(conn, project, connector, run)
            seed_businesses(conn, SyntheticContext(project.id, connector.id, run.id, _T0, ids), 3)
            dataset = Dataset(
                id=ids(),
                project_id=project.id,
                name=SYNTHETIC_DATASET_NAME,
                description="Synthetic test data",
                created_at=_T0,
                updated_at=_T0,
                deleted_at=None,
                deleted_reason=None,
            )
            snapshot_id = ids()
            snapshot = DatasetSnapshot(
                id=snapshot_id,
                dataset_id=dataset.id,
                version=1,
                row_count=3,
                duckdb_table=BUSINESS_SNAPSHOT_TABLE,
                parquet_path=f"datasets/{dataset.id}/{snapshot_id}",
                schema_version=SNAPSHOT_SCHEMA_VERSION,
                filters_json={"project_id": project.id},
                created_at=_T0,
            )
            repository.insert(conn, dataset, snapshot)
            dataset_query = DatasetListQuery(project_id=project.id, limit=50, cursor=None)
            business_page = repository.list_businesses(conn, business_query)
            detail = repository.get_business_detail(conn, business_page.items[0].id)
            dataset_page = repository.list_datasets(conn, dataset_query)
            seeded = seed_discovery(
                conn, SyntheticContext(project.id, connector.id, run.id, _T0, ids)
            )
            discovery = build_discovery_fixtures(conn, project.id, ids, seeded)
        finally:
            conn.close()
    return {
        "business_list_query.json": _dump(business_query),
        "business_page.json": _dump(business_page),
        "business_detail.json": _dump(detail),
        "dataset_list_query.json": _dump(dataset_query),
        "dataset_page.json": _dump(dataset_page),
        "synthetic_seed_result.json": _dump(
            SyntheticSeedResult(
                project_id=project.id,
                business_id=business_page.items[0].id,
                business_count=3,
                dataset_id=dataset.id,
                created=True,
            )
        ),
        **discovery,
    }


def build_discovery_fixtures(
    conn: Any, project_id: str, ids: Callable[[], str], seeded: DiscoveryFixture
) -> dict[str, dict[str, Any]]:
    """Discovery shapes, read back through the real repository queries where there is one."""
    campaign_query = CampaignListQuery(project_id=project_id, limit=50, cursor=None)
    campaign_page = repository.list_campaigns(conn, campaign_query)
    run_query = DiscoveryRunListQuery(
        project_id=project_id, campaign_id=seeded.campaign_id, limit=25, cursor=None
    )
    run_page = repository.list_discovery_runs(conn, run_query)
    candidate_query = CandidateListQuery(
        project_id=project_id,
        campaign_id=seeded.campaign_id,
        source_run_id=None,
        search=None,
        include_duplicates=True,
        limit=50,
        cursor=None,
    )
    candidate_page = repository.list_candidates(conn, candidate_query)
    detail = repository.get_candidate_detail(conn, seeded.candidate_ids[-1])
    catalog = SourceCatalog(
        sources=[
            SourceInfo(
                connector_id=ids(),
                metadata=source.metadata,
                enabled=not source.metadata.requires_credentials,
                health=(
                    SourceHealth.NEEDS_CONFIGURATION
                    if source.metadata.requires_credentials
                    else SourceHealth.READY
                ),
                health_detail=(
                    "synthetic fixture: not configured"
                    if source.metadata.requires_credentials
                    else None
                ),
                requests_per_minute=source.metadata.default_requests_per_minute,
                daily_request_budget=source.metadata.default_daily_request_budget,
                max_concurrency=1,
                last_run_at=None,
                last_run_status=None,
                last_error_code=None,
                last_error_message=None,
            )
            for source in registry.SOURCES.values()
        ],
        checked_at=_T0,
    )
    check = CriteriaCheck(
        problems=[
            CriteriaProblem(
                severity=ProblemSeverity.WARNING,
                code="language_is_a_signal",
                path="market_languages",
                message=("Market language is an affinity signal, never evidence of ownership."),
            )
        ],
        estimates=[
            SourceEstimate(
                connector_key="file.import",
                connector_name="Local file import (CSV, TSV, JSON, JSONL)",
                supported=True,
                value=3,
                basis="rows in the imported file, before the mapping",
                unsupported_fields=["categories"],
            )
        ],
        checked_at=_T0,
    )
    preview = file_import.preview(
        ImportPreviewRequest(
            format=None,
            filename=SYNTHETIC_IMPORT_FILE,
            content=SYNTHETIC_IMPORT_CSV,
            mapping=None,
        )
    )
    return {
        "source_catalog.json": _dump(catalog),
        "campaign_list_query.json": _dump(campaign_query),
        "campaign_page.json": _dump(campaign_page),
        "campaign_saved.json": _dump(
            CampaignSaved(campaign=campaign_page.items[0], problems=check.problems)
        ),
        "criteria_check.json": _dump(check),
        "discovery_start_request.json": _dump(
            DiscoveryStartRequest(
                campaign_id=seeded.campaign_id,
                connector_keys=["file.import"],
                inputs={
                    "file.import": {
                        "filename": SYNTHETIC_IMPORT_FILE,
                        "format": "csv",
                        "content": SYNTHETIC_IMPORT_CSV,
                        "mapping": None,
                    }
                },
            )
        ),
        "discovery_run_list_query.json": _dump(run_query),
        "discovery_run_page.json": _dump(run_page),
        "candidate_list_query.json": _dump(candidate_query),
        "candidate_page.json": _dump(candidate_page),
        "candidate_detail.json": _dump(detail),
        "import_preview.json": _dump(preview),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export contract schema and fixtures.")
    parser.add_argument("--check", action="store_true", help="fail if files on disk are stale")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="packages/shared-types dir")
    args = parser.parse_args(argv)

    outputs: dict[Path, dict[str, Any]] = {Path("schema/contracts.schema.json"): build_schema()}
    outputs.update({Path("fixtures") / name: data for name, data in build_fixtures().items()})

    stale: list[str] = []
    for relative, data in outputs.items():
        target = args.out / relative
        content = (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode()
        current = target.read_bytes().replace(b"\r\n", b"\n") if target.exists() else None
        if current == content:
            continue
        if args.check:
            stale.append(relative.as_posix())
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            print(f"wrote {relative.as_posix()}")
    if stale:
        print("stale contract files (run `pnpm contracts`):", *stale, sep="\n  ", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
