"""Request routing: every request type maps to a payload model, a handler and a timeout."""

import asyncio
import os
import platform
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from research_engine import __version__, repository
from research_engine.contracts import (
    DATA_CONTRACT_VERSION,
    PROTOCOL_VERSION,
    DiagnosticsResult,
    EmptyPayload,
    HealthResult,
    MessageType,
    ShutdownResult,
)
from research_engine.discovery import registry
from research_engine.discovery.engine import DiscoveryService
from research_engine.discovery.sources import file_import
from research_engine.domain import (
    BusinessDetail,
    BusinessDetailQuery,
    BusinessListQuery,
    BusinessPage,
    CampaignListQuery,
    CampaignPage,
    CampaignSaved,
    CampaignSaveRequest,
    CandidateDetail,
    CandidateDetailQuery,
    CandidateListQuery,
    CandidatePage,
    CriteriaCheck,
    CriteriaCheckQuery,
    DatasetListQuery,
    DatasetPage,
    DiscoveryControlRequest,
    DiscoveryRun,
    DiscoveryRunListQuery,
    DiscoveryRunPage,
    DiscoveryStartRequest,
    ImportPreview,
    ImportPreviewRequest,
    SourceCatalog,
    SourceCatalogQuery,
    SourceInfo,
    SyntheticSeedResult,
    utc_now,
)
from research_engine.gpu import probe_gpu
from research_engine.ollama import probe_ollama
from research_engine.settings import Settings
from research_engine.storage import check_duckdb, check_sqlite
from research_engine.testdata import seed_synthetic_workspace

#: Actor recorded for anything the operator triggered from the interface.
ACTOR_UI = "ui"


class NotAllowedError(RuntimeError):
    """The request is valid but this worker is not allowed to serve it (e.g. dev tools off)."""


@dataclass(frozen=True, slots=True)
class Context:
    settings: Settings
    started_at: datetime
    request_shutdown: Callable[[], None]
    #: Owns the running discovery jobs; see discovery/engine.py.
    discovery: DiscoveryService


@dataclass(frozen=True, slots=True)
class Route:
    payload: type[BaseModel]
    handler: Callable[[Context, Any], Awaitable[BaseModel]]
    timeout_s: float


def health(ctx: Context) -> HealthResult:
    return HealthResult(
        status="ok",
        worker_version=__version__,
        protocol_version=PROTOCOL_VERSION,
        data_contract_version=DATA_CONTRACT_VERSION,
        python_version=platform.python_version(),
        pid=os.getpid(),
        started_at=ctx.started_at,
        uptime_s=round((datetime.now(UTC) - ctx.started_at).total_seconds(), 3),
    )


async def _health(ctx: Context, _: EmptyPayload) -> HealthResult:
    return health(ctx)


async def _diagnostics(ctx: Context, _: EmptyPayload) -> DiagnosticsResult:
    settings = ctx.settings
    sqlite, duckdb, ollama, gpu = await asyncio.gather(
        asyncio.to_thread(check_sqlite, settings.data_dir),
        asyncio.to_thread(check_duckdb, settings.data_dir),
        probe_ollama(settings.ollama_url, settings.ollama_timeout_s),
        asyncio.to_thread(probe_gpu),
    )
    return DiagnosticsResult(
        health=health(ctx), sqlite=sqlite, duckdb=duckdb, ollama=ollama, gpu=gpu
    )


async def _shutdown(ctx: Context, _: EmptyPayload) -> ShutdownResult:
    ctx.request_shutdown()
    return ShutdownResult(accepted=True)


async def _with_store[ResultT](
    ctx: Context, action: Callable[[sqlite3.Connection], ResultT]
) -> ResultT:
    """Runs blocking store work on a thread with its own short-lived connection."""

    def run() -> ResultT:
        conn = repository.connect(ctx.settings.data_dir)
        try:
            return action(conn)
        finally:
            conn.close()

    return await asyncio.to_thread(run)


async def _businesses_list(ctx: Context, query: BusinessListQuery) -> BusinessPage:
    return await _with_store(ctx, lambda conn: repository.list_businesses(conn, query))


async def _businesses_get(ctx: Context, query: BusinessDetailQuery) -> BusinessDetail:
    return await _with_store(
        ctx, lambda conn: repository.get_business_detail(conn, query.business_id)
    )


async def _datasets_list(ctx: Context, query: DatasetListQuery) -> DatasetPage:
    return await _with_store(ctx, lambda conn: repository.list_datasets(conn, query))


async def _dev_seed_synthetic(ctx: Context, _: EmptyPayload) -> SyntheticSeedResult:
    data_dir = ctx.settings.data_dir
    if not ctx.settings.dev_tools or data_dir is None:
        raise NotAllowedError("synthetic test data can only be created by a development build")
    return await _with_store(ctx, lambda conn: seed_synthetic_workspace(conn, data_dir))


# ---- discovery (phase 04) ----------------------------------------------------------------------


def _by_key(catalog: SourceCatalog) -> dict[str, SourceInfo]:
    return {info.metadata.key: info for info in catalog.sources}


async def _sources_list(ctx: Context, query: SourceCatalogQuery) -> SourceCatalog:
    return await ctx.discovery.catalog(query.project_id)


async def _sources_preview_import(ctx: Context, request: ImportPreviewRequest) -> ImportPreview:
    return await asyncio.to_thread(file_import.preview, request)


async def _campaigns_list(ctx: Context, query: CampaignListQuery) -> CampaignPage:
    return await _with_store(ctx, lambda conn: repository.list_campaigns(conn, query))


async def _campaigns_save(ctx: Context, request: CampaignSaveRequest) -> CampaignSaved:
    campaign_id = await _with_store(
        ctx, lambda conn: repository.save_campaign(conn, request, actor=ACTOR_UI)
    )
    item = await _with_store(ctx, lambda conn: repository.get_campaign_item(conn, campaign_id))
    catalog = await ctx.discovery.catalog(item.project_id)
    return CampaignSaved(
        campaign=item, problems=registry.check_criteria(item.criteria, _by_key(catalog))
    )


async def _campaigns_validate(ctx: Context, query: CriteriaCheckQuery) -> CriteriaCheck:
    catalog = await ctx.discovery.catalog(query.project_id)
    problems = registry.check_criteria(query.criteria, _by_key(catalog))
    estimates = (
        await ctx.discovery.estimates(query.criteria, catalog) if query.with_estimates else []
    )
    return CriteriaCheck(problems=problems, estimates=estimates, checked_at=utc_now())


async def _discovery_start(ctx: Context, request: DiscoveryStartRequest) -> DiscoveryRun:
    return await ctx.discovery.start(request)


async def _discovery_control(ctx: Context, request: DiscoveryControlRequest) -> DiscoveryRun:
    return await ctx.discovery.control(request)


async def _discovery_runs(ctx: Context, query: DiscoveryRunListQuery) -> DiscoveryRunPage:
    return await _with_store(ctx, lambda conn: repository.list_discovery_runs(conn, query))


async def _discovery_candidates(ctx: Context, query: CandidateListQuery) -> CandidatePage:
    return await _with_store(ctx, lambda conn: repository.list_candidates(conn, query))


async def _discovery_candidate(ctx: Context, query: CandidateDetailQuery) -> CandidateDetail:
    return await _with_store(
        ctx, lambda conn: repository.get_candidate_detail(conn, query.candidate_id)
    )


ROUTES: dict[str, Route] = {
    MessageType.HEALTH: Route(EmptyPayload, _health, timeout_s=2.0),
    MessageType.DIAGNOSTICS: Route(EmptyPayload, _diagnostics, timeout_s=20.0),
    MessageType.SHUTDOWN: Route(EmptyPayload, _shutdown, timeout_s=2.0),
    MessageType.BUSINESSES_LIST: Route(BusinessListQuery, _businesses_list, timeout_s=10.0),
    MessageType.BUSINESSES_GET: Route(BusinessDetailQuery, _businesses_get, timeout_s=10.0),
    MessageType.DATASETS_LIST: Route(DatasetListQuery, _datasets_list, timeout_s=10.0),
    MessageType.SOURCES_LIST: Route(SourceCatalogQuery, _sources_list, timeout_s=15.0),
    MessageType.SOURCES_PREVIEW_IMPORT: Route(
        ImportPreviewRequest, _sources_preview_import, timeout_s=30.0
    ),
    MessageType.CAMPAIGNS_LIST: Route(CampaignListQuery, _campaigns_list, timeout_s=10.0),
    MessageType.CAMPAIGNS_SAVE: Route(CampaignSaveRequest, _campaigns_save, timeout_s=15.0),
    MessageType.CAMPAIGNS_VALIDATE: Route(CriteriaCheckQuery, _campaigns_validate, timeout_s=60.0),
    MessageType.DISCOVERY_START: Route(DiscoveryStartRequest, _discovery_start, timeout_s=60.0),
    MessageType.DISCOVERY_CONTROL: Route(
        DiscoveryControlRequest, _discovery_control, timeout_s=15.0
    ),
    MessageType.DISCOVERY_RUNS: Route(DiscoveryRunListQuery, _discovery_runs, timeout_s=15.0),
    MessageType.DISCOVERY_CANDIDATES: Route(
        CandidateListQuery, _discovery_candidates, timeout_s=15.0
    ),
    MessageType.DISCOVERY_CANDIDATE: Route(
        CandidateDetailQuery, _discovery_candidate, timeout_s=10.0
    ),
    MessageType.DEV_SEED_SYNTHETIC: Route(EmptyPayload, _dev_seed_synthetic, timeout_s=60.0),
}
