"""Exports the contract JSON Schema and golden fixtures to packages/shared-types.

    python -m research_engine.export_contracts          # (re)write the files
    python -m research_engine.export_contracts --check  # exit 1 if committed files are stale

Fixtures are synthetic samples with obviously fake values. The Rust and TypeScript suites use
them to prove every layer reads and writes the same shapes; they are never product output.
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from pydantic.json_schema import models_json_schema

from research_engine.contracts import (
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
    _, top = models_json_schema([(model, "validation") for model in EXPORTED_MODELS])
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "LeadIntelligenceContracts",
        "description": (
            "Generated from services/research-engine/src/research_engine/contracts.py "
            f"(protocol version {PROTOCOL_VERSION}). Do not edit by hand."
        ),
        "x-protocol-version": PROTOCOL_VERSION,
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
