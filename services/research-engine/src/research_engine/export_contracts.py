"""Exports the contract JSON Schema and golden fixtures to packages/shared-types.

    python -m research_engine.export_contracts          # (re)write the files
    python -m research_engine.export_contracts --check  # exit 1 if committed files are stale

Fixtures are synthetic samples with obviously fake values. The Rust and TypeScript suites use
them to prove every layer reads and writes the same shapes; they are never product output.
Data-model fixtures are produced by the real repository queries over a temporary store seeded with
synthetic test data, deterministic ids and a fixed clock.
"""

import argparse
import asyncio
import json
import sys
import tempfile
from collections.abc import Callable
from contextlib import ExitStack
from datetime import UTC, datetime
from itertools import count
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch
from uuid import UUID

from pydantic import BaseModel
from pydantic.json_schema import models_json_schema

from research_engine import jobs, repository
from research_engine import testdata as testdata_module
from research_engine.ai import engine as ai_engine
from research_engine.ai import resources as ai_resources
from research_engine.ai import runner as ai_runner
from research_engine.ai import store as ai_store
from research_engine.ai.engine import AiService
from research_engine.ai.resources import ResourceMonitor, Sample
from research_engine.contacts import engine as contacts_engine
from research_engine.contacts import store as contacts_store
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
    AiBenchmarkAction,
    AiBenchmarkRequest,
    AiConfigureRequest,
    AiInferenceListQuery,
    AiInferenceQuery,
    AiModelChangeAction,
    AiModelChangeRequest,
    AiProfile,
    AiProfileChange,
    AiReviewDecision,
    AiReviewRequest,
    AiRunListQuery,
    AiStartRequest,
    AiStatusQuery,
    AiTaskChange,
    AiTaskType,
    BusinessListQuery,
    BusinessSort,
    CampaignListQuery,
    CampaignSaved,
    CandidateListQuery,
    CanonicalChoiceRequest,
    CanonicalField,
    ContactAction,
    ContactActionRequest,
    ContactListQuery,
    ContactRunListQuery,
    ContactStartRequest,
    ContactType,
    CrawlRunListQuery,
    CrawlStartRequest,
    CrawlUrlListQuery,
    CriteriaCheck,
    CriteriaProblem,
    Dataset,
    DatasetListQuery,
    DatasetSnapshot,
    DecisionMakerDecision,
    DecisionMakerListQuery,
    DecisionMakerReview,
    DecisionMakerReviewRequest,
    DiscoveryRunListQuery,
    DiscoveryStartRequest,
    ImportPreviewRequest,
    InferenceStatus,
    LatinoReviewFilter,
    LatinoReviewRequest,
    LatinoRunListQuery,
    LatinoStartRequest,
    LatinoStatus,
    LlmFitModel,
    LlmFitReport,
    MatchAction,
    MatchActionRequest,
    MatchListQuery,
    MatchStatus,
    ProblemSeverity,
    ResolutionRunListQuery,
    ResolutionStartRequest,
    SourceCatalog,
    SourceEstimate,
    SourceHealth,
    SourceInfo,
    SourceKind,
    SuppressionChangeRequest,
    SuppressionExportRequest,
    SuppressionImportRequest,
    SuppressionKind,
    SuppressionListQuery,
    SyntheticSeedResult,
    WebsiteAuditListQuery,
    WebsiteAuditQuery,
    WebsiteRunListQuery,
    WebsiteStartRequest,
    default_crawl_settings,
)
from research_engine.latino import engine as latino_engine
from research_engine.latino import store as latino_store
from research_engine.resolution import store as resolution_store
from research_engine.storage import check_sqlite
from research_engine.testdata import (
    CONTACT_SITES,
    SYNTHETIC_DATASET_NAME,
    SYNTHETIC_IMPORT_CSV,
    SYNTHETIC_IMPORT_FILE,
    SYNTHETIC_SITE,
    DiscoveryFixture,
    ScriptedProvider,
    SyntheticContext,
    seed_businesses,
    seed_contacts,
    seed_crawl,
    seed_discovery,
    seed_latino,
    seed_resolution,
    seed_website,
    synthetic_answer,
    synthetic_connector,
    synthetic_project,
    synthetic_run,
)
from research_engine.website import engine as website_engine
from research_engine.website import store as website_store

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
        latino_status=None,
        latino_min_confidence=None,
        latino_source_kind=None,
        latino_review=None,
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
            resolution = build_resolution_fixtures(
                conn, SyntheticContext(project.id, connector.id, run.id, _T0, ids)
            )
            latino = build_latino_fixtures(
                conn, SyntheticContext(project.id, connector.id, run.id, _T0, ids)
            )
            website = build_website_fixtures(
                conn, SyntheticContext(project.id, connector.id, run.id, _T0, ids), data_dir
            )
            contacts = build_contact_fixtures(
                conn, SyntheticContext(project.id, connector.id, run.id, _T0, ids), data_dir
            )
            ai = build_ai_fixtures(
                conn, SyntheticContext(project.id, connector.id, run.id, _T0, ids), data_dir
            )
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
        **resolution,
        **latino,
        **website,
        **contacts,
        **ai,
    }


def build_ai_fixtures(
    conn: Any, context: SyntheticContext, data_dir: Path
) -> dict[str, dict[str, Any]]:
    """Local AI shapes from the real service over the synthetic project, with a SYNTHETIC scripted
    runtime (never a model), no GPU, a fixed memory sample, fixture ids, a fixed clock and a
    stopped timer: an analysis job over every business, a benchmark, a download and a review,
    read back through the real queries."""
    project_id = context.project_id
    gpu = GpuStatus(
        state=ComponentState.UNAVAILABLE, summary="synthetic fixture: no gpu", error="none",
        checked_at=_T0, duration_ms=0.0, devices=[], cuda_available=False,
        cuda_driver_version=None,
    )  # fmt: skip
    sample = Sample(16_384, 9_000, 8_192, 7_100, _T0)
    llm_fit = LlmFitReport(
        available=True, version="llmfit 0.0.0-fixture",
        models=[LlmFitModel(name="Synthetic/Model-1B", ollama_name="synthetic-text:1b",
                            fit_level="Perfect", run_mode="GPU", runtime="llama.cpp",
                            quantization="Q4_K_M", memory_required_gb=1.5, estimated_tps=40.0,
                            installed=True, notes=["synthetic fixture"])],
        error=None, checked_at=_T0,
    )  # fmt: skip
    provider = ScriptedProvider(synthetic_answer)
    service = AiService(
        data_dir, lambda: repository.connect(data_dir), provider,
        ResourceMonitor(sampler=lambda: sample), downloads_allowed=True, llm_fit=lambda: llm_fit,
    )  # fmt: skip
    run_query = AiRunListQuery(project_id=project_id, limit=25, cursor=None)
    list_query = AiInferenceListQuery(
        task_type=None, status=None, business_id=None, review_status=None, limit=50, cursor=None
    )
    start = AiStartRequest(
        project_id=project_id, business_ids=[],
        task_types=[AiTaskType.EVIDENCE_EXTRACTION, AiTaskType.BUSINESS_CLASSIFICATION],
    )  # fmt: skip
    configure = AiConfigureRequest(
        profiles=[AiProfileChange(profile=profile, model="synthetic-text:1b", num_ctx=4096,
                                  allow_cpu=profile is not AiProfile.QUALITY)
                  for profile in AiProfile],
        tasks=[AiTaskChange(task_type=task, enabled=task is not AiTaskType.VISUAL_REVIEW,
                            profile=AiProfile.FAST) for task in AiTaskType],
        min_free_ram_mib=1024, min_free_vram_mib=512,
    )  # fmt: skip
    bench = AiBenchmarkRequest(
        action=AiBenchmarkAction.START, models=["synthetic-text:1b"],
        task_types=[AiTaskType.BUSINESS_CLASSIFICATION],
    )  # fmt: skip
    download = AiModelChangeRequest(
        action=AiModelChangeAction.PULL, model="synthetic-new:1b", confirmation="synthetic-new:1b"
    )

    async def exercise() -> dict[str, Any]:
        await service.configure(configure)
        page = await service.start(start)
        await service.idle(page.items[0].job_id)
        await service.benchmark(bench)
        if service._benchmark_task is not None:
            await service._benchmark_task
        await service.change_model(download)
        if service._change_task is not None:
            await service._change_task
        status = await service.status(AiStatusQuery(include_llm_fit=True))
        await service.shutdown()
        return {"status": status}

    with ExitStack() as stack:
        for module in (ai_store, ai_runner, ai_engine, jobs, repository, latino_store):
            stack.enter_context(patch.object(module, "new_id", context.ids))
            stack.enter_context(patch.object(module, "utc_now", lambda: _T0))
        for module in (ai_resources, testdata_module):
            stack.enter_context(patch.object(module, "utc_now", lambda: _T0))
        stack.enter_context(patch.object(ai_engine, "probe_gpu", lambda: gpu))
        stack.enter_context(patch.object(ai_engine, "take_sample", lambda: sample))
        stack.enter_context(
            patch.object(ai_runner, "time", SimpleNamespace(perf_counter=lambda: 0.0))
        )
        results = asyncio.run(exercise())
        inferences = ai_store.list_inferences(conn, list_query)
        chosen = next(item for item in inferences.items if item.status is InferenceStatus.SUCCEEDED
                      and item.task_type is AiTaskType.EVIDENCE_EXTRACTION)  # fmt: skip
        review = AiReviewRequest(
            inference_run_id=chosen.id, decision=AiReviewDecision.ACCEPT,
            reason="Synthetic fixture: the quote is in the page it cites.",
        )  # fmt: skip
        ai_store.review(conn, review, actor="fixture-reviewer")
        detail = ai_store.get_inference(conn, chosen.id)
        runs = ai_store.list_runs(conn, run_query)
    return {
        "ai_status_query.json": _dump(AiStatusQuery(include_llm_fit=True)),
        "ai_status.json": _dump(results["status"]),
        "ai_configure_request.json": _dump(configure),
        "ai_start_request.json": _dump(start),
        "ai_run_list_query.json": _dump(run_query),
        "ai_run_page.json": _dump(runs),
        "ai_benchmark_request.json": _dump(bench),
        "ai_inference_list_query.json": _dump(list_query),
        "ai_inference_page.json": _dump(inferences),
        "ai_inference_query.json": _dump(AiInferenceQuery(inference_run_id=chosen.id)),
        "ai_inference_detail.json": _dump(detail),
        "ai_review_request.json": _dump(review),
        "ai_model_change_request.json": _dump(download),
    }


def build_contact_fixtures(
    conn: Any, context: SyntheticContext, data_dir: Path
) -> dict[str, dict[str, Any]]:
    """Contact intelligence shapes from a real run of the engine over the synthetic contact sites
    (with a mail-domain lookup that never touches the network), a suppression import and export,
    read back through the real queries. Reads happen under the fixed clock too, because freshness
    is decided when a channel is read."""
    project_id = context.project_id
    run_query = ContactRunListQuery(project_id=project_id, limit=25, cursor=None)
    list_query = ContactListQuery(
        project_id=project_id,
        contact_type=None,
        validation_status=None,
        role=None,
        freshness=None,
        include_hypotheses=True,
        include_suppressed=True,
        limit=500,
        cursor=None,
    )
    people_query = DecisionMakerListQuery(
        project_id=project_id, review_status=None, limit=50, cursor=None
    )
    suppression_query = SuppressionListQuery(kind=None, include_lifted=True, limit=50, cursor=None)
    import_request = SuppressionImportRequest(
        filename="synthetic-do-not-contact.csv",
        content=(
            "kind,value,reason\n"
            "email,owner@synthetic-closed-0799.example,Asked by email (synthetic test data)\n"
            "domain,synthetic-closed-0799.example,\n"
            "bogus,x,\n"
        ),
    )
    export_request = SuppressionExportRequest(include_lifted=True)
    with ExitStack() as stack:
        for module in (contacts_store, resolution_store, jobs, repository):
            stack.enter_context(patch.object(module, "new_id", context.ids))
            stack.enter_context(patch.object(module, "utc_now", lambda: _T0))
        stack.enter_context(patch.object(contacts_engine, "utc_now", lambda: _T0))
        seeded = seed_contacts(conn, context, data_dir)
        (published,) = conn.execute(
            "SELECT coalesce(b.merged_into_id, b.id) FROM entity_records er "
            "JOIN businesses b ON b.id = er.business_id WHERE er.candidate_id = ?",
            (seeded.candidates["published"],),
        ).fetchone()
        people = contacts_store.list_decision_makers(conn, people_query)
        ambiguous = next(
            item
            for item in people.items
            if item.decision_maker.review_status is DecisionMakerReview.AMBIGUOUS
        )
        imported = contacts_store.import_suppressions(conn, import_request, actor="fixture")
        fixtures = {
            "contact_start_request.json": _dump(
                ContactStartRequest(project_id=project_id, check_mail_domains=False)
            ),
            "contact_run_list_query.json": _dump(run_query),
            "contact_run_page.json": _dump(contacts_store.list_contact_runs(conn, run_query)),
            "contact_list_query.json": _dump(list_query),
            "contact_page.json": _dump(contacts_store.list_contacts(conn, list_query)),
            "contact_detail.json": _dump(contacts_store.get_contact_detail(conn, published)),
            "contact_action_request.json": _dump(
                ContactActionRequest(
                    business_id=published,
                    key=f"email:info@{CONTACT_SITES['published']}",
                    action=ContactAction.CONFIRM,
                    reason="Synthetic fixture: confirmed by a colleague on the phone.",
                    check_mail_domains=False,
                )
            ),
            "decision_maker_list_query.json": _dump(people_query),
            "decision_maker_page.json": _dump(people),
            "decision_maker_review_request.json": _dump(
                DecisionMakerReviewRequest(
                    decision_maker_id=ambiguous.decision_maker.id,
                    decision=DecisionMakerDecision.CONFIRM,
                    reason="Synthetic fixture: the founder still runs the business.",
                )
            ),
            "suppression_list_query.json": _dump(suppression_query),
            "suppression_page.json": _dump(
                contacts_store.list_suppressions(conn, suppression_query)
            ),
            "suppression_change_request.json": _dump(
                SuppressionChangeRequest(
                    suppression_id=None,
                    kind=SuppressionKind.DOMAIN,
                    value="synthetic-moved-away-0798.example",
                    reason="Synthetic fixture: the business asked for its domain to be left out.",
                )
            ),
            "suppression_import_request.json": _dump(import_request),
            "suppression_import_result.json": _dump(imported),
            "suppression_export_request.json": _dump(export_request),
            "suppression_export.json": _dump(
                contacts_store.export_suppressions(conn, export_request)
            ),
        }
    return fixtures


def build_website_fixtures(
    conn: Any, context: SyntheticContext, data_dir: Path
) -> dict[str, dict[str, Any]]:
    """Website intelligence shapes from real audits of the synthetic sites (the sites of the Latino
    fixtures have no stored pages, so their audits are empty), the second of a site crawled again,
    read back through the real queries (fixture ids and fixed clock, as above)."""
    project_id = context.project_id
    with ExitStack() as stack:
        for module in (website_store, jobs, repository):
            stack.enter_context(patch.object(module, "new_id", context.ids))
            stack.enter_context(patch.object(module, "utc_now", lambda: _T0))
        stack.enter_context(patch.object(website_engine, "utc_now", lambda: _T0))
        seeded = seed_website(conn, context, data_dir)
    changed_site = seeded.sites["changed"]
    run_query = WebsiteRunListQuery(project_id=project_id, limit=25, cursor=None)
    audit_query = WebsiteAuditListQuery(project_id=project_id, site=None, limit=25, cursor=None)
    audits = website_store.list_website_audits(conn, audit_query)
    changed = next(item for item in audits.items if item.site == changed_site)
    query = WebsiteAuditQuery(audit_id=changed.id)
    return {
        "website_start_request.json": _dump(
            WebsiteStartRequest(project_id=project_id, sites=[changed_site])
        ),
        "website_run_list_query.json": _dump(run_query),
        "website_run_page.json": _dump(website_store.list_website_runs(conn, run_query)),
        "website_audit_list_query.json": _dump(audit_query),
        "website_audit_page.json": _dump(audits),
        "website_audit_query.json": _dump(query),
        "website_audit_detail.json": _dump(website_store.get_website_audit(conn, changed.id)),
    }


def build_latino_fixtures(conn: Any, context: SyntheticContext) -> dict[str, dict[str, Any]]:
    """Latino evidence shapes from real runs of the engines over the synthetic situations, with a
    person's review, read back through the real queries (fixture ids and fixed clock, as above)."""
    project_id = context.project_id
    run_query = LatinoRunListQuery(project_id=project_id, limit=25, cursor=None)
    business_query = BusinessListQuery(
        project_id=project_id,
        search=None,
        status=None,
        latino_status=LatinoStatus.PROBABLE_LATINO_OWNED,
        latino_min_confidence=0.5,
        latino_source_kind=SourceKind.BUSINESS_WEBSITE,
        latino_review=LatinoReviewFilter.UNREVIEWED,
        sort=BusinessSort.NAME,
        limit=50,
        cursor=None,
    )
    with ExitStack() as stack:
        for module in (latino_store, resolution_store, jobs, repository):
            stack.enter_context(patch.object(module, "new_id", context.ids))
            stack.enter_context(patch.object(module, "utc_now", lambda: _T0))
        stack.enter_context(patch.object(latino_engine, "utc_now", lambda: _T0))
        seeded = seed_latino(conn, context)
        (disputed,) = conn.execute(
            "SELECT coalesce(b.merged_into_id, b.id) FROM entity_records er "
            "JOIN businesses b ON b.id = er.business_id WHERE er.candidate_id = ?",
            (seeded.candidates["disputed_listing"],),
        ).fetchone()
        review = LatinoReviewRequest(
            business_id=disputed,
            status=LatinoStatus.UNKNOWN,
            reason="Synthetic fixture: the directory and the registry disagree; kept unknown.",
        )
        latino_store.review(conn, review, actor="fixture-reviewer")
        detail = latino_store.get_latino_detail(conn, disputed)
        runs = latino_store.list_latino_runs(conn, run_query)
        page = repository.list_businesses(conn, business_query)
    return {
        "latino_start_request.json": _dump(LatinoStartRequest(project_id=None)),
        "latino_run_list_query.json": _dump(run_query),
        "latino_run_page.json": _dump(runs),
        "latino_detail.json": _dump(detail),
        "latino_review_request.json": _dump(review),
        "latino_business_list_query.json": _dump(business_query),
        "latino_business_page.json": _dump(page),
    }


def build_resolution_fixtures(conn: Any, context: SyntheticContext) -> dict[str, dict[str, Any]]:
    """Entity resolution shapes from a real run of the engine over the synthetic duplicates.

    The engine stamps rows with fresh UUIDv7 ids and the wall clock; here both are the fixture
    counter and the fixed clock, so the exported files are identical on every export.
    """
    with ExitStack() as stack:
        for module in (resolution_store, jobs, repository):
            stack.enter_context(patch.object(module, "new_id", context.ids))
            stack.enter_context(patch.object(module, "utc_now", lambda: _T0))
        seed_resolution(conn, context)
    project_id = context.project_id
    run_query = ResolutionRunListQuery(project_id=project_id, limit=25, cursor=None)
    match_query = MatchListQuery(
        project_id=project_id,
        status=None,
        risk=None,
        min_score=None,
        connector_key=None,
        limit=50,
        cursor=None,
    )
    matches = resolution_store.list_matches(conn, match_query)
    pending = next(item for item in matches.items if item.status is MatchStatus.PENDING)
    merged = next(item for item in matches.items if item.status is MatchStatus.MERGED)
    cluster = repository.get_business_detail(conn, merged.left.root_business_id)
    chosen = next(value for value in cluster.canonical if value.field is CanonicalField.PHONE)
    other_phone = next(
        contact.value
        for contact in cluster.contacts
        if contact.value.contact_type is ContactType.PHONE
        and contact.value.id != chosen.observation_id
    )
    return {
        "resolution_start_request.json": _dump(ResolutionStartRequest(project_id=None)),
        "resolution_run_list_query.json": _dump(run_query),
        "resolution_run_page.json": _dump(resolution_store.list_resolution_runs(conn, run_query)),
        "match_list_query.json": _dump(match_query),
        "match_page.json": _dump(matches),
        "match_detail.json": _dump(resolution_store.get_match_detail(conn, pending.id)),
        "match_action_request.json": _dump(
            MatchActionRequest(
                match_id=pending.id,
                action=MatchAction.MERGE,
                reason="Synthetic fixture: the same bakery at its new address.",
            )
        ),
        "business_cluster_detail.json": _dump(cluster),
        "canonical_choice_request.json": _dump(
            CanonicalChoiceRequest(
                business_id=cluster.business.id,
                field=CanonicalField.PHONE,
                observation_id=other_phone.id,
                reason=None,
            )
        ),
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
    # the crawl registers its own connector and run; only the project, clock and ids are shared
    context = SyntheticContext(project_id, "", seeded.source_run_id, _T0, ids)
    crawl = seed_crawl(conn, context, seeded.campaign_id, seeded.candidate_ids[0])
    crawl_run_query = CrawlRunListQuery(
        project_id=project_id, campaign_id=seeded.campaign_id, limit=25, cursor=None
    )
    crawl_url_query = CrawlUrlListQuery(
        job_id=crawl.job_id, source_run_id=None, status=None, limit=50, cursor=None
    )
    return {
        "crawl_start_request.json": _dump(
            CrawlStartRequest(
                project_id=None,
                campaign_id=seeded.campaign_id,
                urls=[f"https://{SYNTHETIC_SITE}/"],
                settings=default_crawl_settings(),
            )
        ),
        "crawl_run_list_query.json": _dump(crawl_run_query),
        "crawl_run_page.json": _dump(repository.list_crawl_runs(conn, crawl_run_query)),
        "crawl_url_list_query.json": _dump(crawl_url_query),
        "crawl_url_page.json": _dump(repository.list_crawl_urls(conn, crawl_url_query)),
        "crawl_url_detail.json": _dump(
            repository.get_crawl_url_detail(conn, crawl.rendered_url_id)
        ),
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
