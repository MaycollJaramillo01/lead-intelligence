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
from research_engine.domain import (
    BusinessDetail,
    BusinessDetailQuery,
    BusinessListQuery,
    BusinessPage,
    DatasetListQuery,
    DatasetPage,
    SyntheticSeedResult,
)
from research_engine.gpu import probe_gpu
from research_engine.ollama import probe_ollama
from research_engine.settings import Settings
from research_engine.storage import check_duckdb, check_sqlite
from research_engine.testdata import seed_synthetic_workspace


class NotAllowedError(RuntimeError):
    """The request is valid but this worker is not allowed to serve it (e.g. dev tools off)."""


@dataclass(frozen=True, slots=True)
class Context:
    settings: Settings
    started_at: datetime
    request_shutdown: Callable[[], None]


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


ROUTES: dict[str, Route] = {
    MessageType.HEALTH: Route(EmptyPayload, _health, timeout_s=2.0),
    MessageType.DIAGNOSTICS: Route(EmptyPayload, _diagnostics, timeout_s=20.0),
    MessageType.SHUTDOWN: Route(EmptyPayload, _shutdown, timeout_s=2.0),
    MessageType.BUSINESSES_LIST: Route(BusinessListQuery, _businesses_list, timeout_s=10.0),
    MessageType.BUSINESSES_GET: Route(BusinessDetailQuery, _businesses_get, timeout_s=10.0),
    MessageType.DATASETS_LIST: Route(DatasetListQuery, _datasets_list, timeout_s=10.0),
    MessageType.DEV_SEED_SYNTHETIC: Route(EmptyPayload, _dev_seed_synthetic, timeout_s=60.0),
}
