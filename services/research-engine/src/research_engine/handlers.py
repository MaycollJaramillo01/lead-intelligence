"""Request routing: every request type maps to a payload model, a handler and a timeout."""

import asyncio
import os
import platform
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel

from research_engine import __version__
from research_engine.contracts import (
    PROTOCOL_VERSION,
    DiagnosticsResult,
    EmptyPayload,
    HealthResult,
    MessageType,
    ShutdownResult,
)
from research_engine.gpu import probe_gpu
from research_engine.ollama import probe_ollama
from research_engine.settings import Settings
from research_engine.storage import check_duckdb, check_sqlite


@dataclass(frozen=True, slots=True)
class Context:
    settings: Settings
    started_at: datetime
    request_shutdown: Callable[[], None]


@dataclass(frozen=True, slots=True)
class Route:
    payload: type[BaseModel]
    handler: Callable[[Context, BaseModel], Awaitable[BaseModel]]
    timeout_s: float


def health(ctx: Context) -> HealthResult:
    return HealthResult(
        status="ok",
        worker_version=__version__,
        protocol_version=PROTOCOL_VERSION,
        python_version=platform.python_version(),
        pid=os.getpid(),
        started_at=ctx.started_at,
        uptime_s=round((datetime.now(UTC) - ctx.started_at).total_seconds(), 3),
    )


async def _health(ctx: Context, _: BaseModel) -> HealthResult:
    return health(ctx)


async def _diagnostics(ctx: Context, _: BaseModel) -> DiagnosticsResult:
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


async def _shutdown(ctx: Context, _: BaseModel) -> ShutdownResult:
    ctx.request_shutdown()
    return ShutdownResult(accepted=True)


ROUTES: dict[str, Route] = {
    MessageType.HEALTH: Route(EmptyPayload, _health, timeout_s=2.0),
    MessageType.DIAGNOSTICS: Route(EmptyPayload, _diagnostics, timeout_s=20.0),
    MessageType.SHUTDOWN: Route(EmptyPayload, _shutdown, timeout_s=2.0),
}
