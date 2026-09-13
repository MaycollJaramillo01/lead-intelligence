"""JSON Lines server on stdin/stdout.

stdout carries protocol frames only (stray prints are redirected to stderr); logs go to stderr.
The worker exits when asked to (`shutdown`) or when stdin closes, which is what happens when the
desktop core exits or crashes - so the worker can never outlive its parent.
"""

import asyncio
import contextlib
import logging
import os
import sys
import threading
import time
from datetime import UTC, datetime
from typing import BinaryIO, Final
from uuid import UUID, uuid4

import orjson
from pydantic import ValidationError

from research_engine import __version__
from research_engine.contracts import (
    MAX_LINE_BYTES,
    PROTOCOL_VERSION,
    RESULT_SUFFIX,
    Envelope,
    ErrorCode,
    ErrorInfo,
    MessageType,
)
from research_engine.crawl.engine import CrawlerSettings, CrawlService, CrawlStartError
from research_engine.discovery.engine import DiscoveryService, DiscoveryStartError, EngineSettings
from research_engine.handlers import ROUTES, Context, NotAllowedError, health
from research_engine.latino.engine import LatinoService
from research_engine.latino.store import LatinoError
from research_engine.logs import configure_logging, request_id_var
from research_engine.repository import (
    InvalidCursorError,
    InvalidTransitionError,
    NotFoundError,
    StoreUnavailableError,
    connect,
)
from research_engine.resolution.engine import ResolutionService
from research_engine.resolution.store import ResolutionError
from research_engine.settings import Settings
from research_engine.storage import check_duckdb, check_sqlite

logger = logging.getLogger("research_engine.worker")

SHUTDOWN_GRACE_S: Final = 5.0
#: Identifies this app to every public source it queries, as their guidance asks.
USER_AGENT: Final = f"LeadIntelligence/{__version__} (local desktop research tool)"
_OVERSIZE: Final = b"\x00oversize"  # sentinel frame, cannot be valid JSON


class WorkerError(Exception):
    def __init__(self, code: ErrorCode, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.info = ErrorInfo(code=code, message=message, retryable=retryable)


def _arm_hard_exit(reason: str) -> None:
    """Guarantees termination if the graceful path hangs (e.g. a stuck handler thread)."""

    def expire() -> None:
        logger.error("graceful shutdown exceeded %ss (%s); forcing exit", SHUTDOWN_GRACE_S, reason)
        os._exit(1)

    timer = threading.Timer(SHUTDOWN_GRACE_S, expire)
    timer.daemon = True
    timer.start()


def _salvage_request_id(frame: bytes) -> UUID | None:
    try:
        data = orjson.loads(frame)
        return UUID(str(data["request_id"])) if isinstance(data, dict) else None
    except (orjson.JSONDecodeError, KeyError, ValueError):
        return None


class Worker:
    def __init__(self, settings: Settings, out: BinaryIO, stdin_fd: int = 0) -> None:
        self._settings = settings
        self._out = out
        self._stdin_fd = stdin_fd
        self._stopping = False
        self._stop = asyncio.Event()
        self._tasks: set[asyncio.Task[None]] = set()
        self._discovery = DiscoveryService(
            EngineSettings(
                data_dir=settings.data_dir,
                user_agent=USER_AGENT,
                offline=settings.discovery_offline,
            ),
            lambda: connect(settings.data_dir),
        )
        self._crawl = CrawlService(
            CrawlerSettings(data_dir=settings.data_dir, user_agent=USER_AGENT),
            lambda: connect(settings.data_dir),
        )
        self._resolution = ResolutionService(lambda: connect(settings.data_dir))
        self._latino = LatinoService(lambda: connect(settings.data_dir))
        self._ctx = Context(
            settings,
            datetime.now(UTC),
            self._request_shutdown,
            self._discovery,
            self._crawl,
            self._resolution,
            self._latino,
        )

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        inbox: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=64)
        threading.Thread(
            target=self._read_stdin, args=(loop, inbox), name="stdin-reader", daemon=True
        ).start()

        for check in (check_sqlite, check_duckdb):
            store = await asyncio.to_thread(check, self._settings.data_dir)
            logger.info("store checked", extra={"engine": store.engine, "state": store.state})
        engines: tuple[
            tuple[str, DiscoveryService | CrawlService | ResolutionService | LatinoService], ...
        ] = (
            ("discovery", self._discovery),
            ("crawl", self._crawl),
            ("resolution", self._resolution),
            ("latino", self._latino),
        )
        for name, engine in engines:
            try:
                paused = await engine.recover()
            except Exception:
                logger.warning("interrupted %s runs could not be recovered", name, exc_info=True)
            else:
                if paused:
                    logger.info("interrupted %s runs paused", name, extra={"jobs": paused})
        self._send(
            uuid4(), MessageType.WORKER_READY, payload=health(self._ctx).model_dump(mode="json")
        )
        logger.info(
            "worker ready", extra={"protocol_version": PROTOCOL_VERSION, "pid": os.getpid()}
        )

        stop_wait = asyncio.create_task(self._stop.wait())
        try:
            while True:
                next_frame = asyncio.create_task(inbox.get())
                done, _ = await asyncio.wait(
                    {next_frame, stop_wait}, return_when=asyncio.FIRST_COMPLETED
                )
                if next_frame not in done:
                    next_frame.cancel()
                    break
                frame = next_frame.result()
                if frame is None:
                    logger.info("stdin closed by the desktop core; shutting down")
                    _arm_hard_exit("stdin closed")
                    break
                self._dispatch(frame)
        finally:
            stop_wait.cancel()
            self._stopping = True
            await self._discovery.shutdown()
            await self._crawl.shutdown()
            await self._resolution.shutdown()
            await self._latino.shutdown()
            await self._drain()
        logger.info("worker stopped")

    # ---- input ---------------------------------------------------------------------------

    def _read_stdin(
        self, loop: asyncio.AbstractEventLoop, inbox: asyncio.Queue[bytes | None]
    ) -> None:
        """Frames stdin into lines on a thread; blocks when the inbox is full (backpressure)."""

        def put(item: bytes | None) -> None:
            asyncio.run_coroutine_threadsafe(inbox.put(item), loop).result()

        buffer = b""
        discarding = False
        first = True
        try:
            while chunk := os.read(self._stdin_fd, 65536):
                if first:  # tolerate a UTF-8 BOM that some Windows shells prepend to a stream
                    chunk, first = chunk.removeprefix(b"\xef\xbb\xbf"), False
                buffer += chunk
                while (newline := buffer.find(b"\n")) != -1:
                    line, buffer = buffer[:newline], buffer[newline + 1 :]
                    if discarding:
                        discarding = False
                        put(_OVERSIZE)
                    elif line.strip():
                        put(line if len(line) < MAX_LINE_BYTES else _OVERSIZE)
                if len(buffer) >= MAX_LINE_BYTES:
                    buffer, discarding = b"", True
        except OSError:
            logger.warning("stdin read failed; treating as closed", exc_info=True)
        except RuntimeError:
            return  # event loop already closed: the worker is exiting anyway
        with contextlib.suppress(RuntimeError):  # same: nothing left to notify
            put(None)

    def _dispatch(self, frame: bytes) -> None:
        if frame == _OVERSIZE:
            self._protocol_error(None, f"frame exceeds {MAX_LINE_BYTES} bytes")
            return
        try:
            request = Envelope.model_validate_json(frame)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(map(str, e['loc'])) or 'frame'}: {e['msg']}" for e in exc.errors()[:5]
            )
            self._protocol_error(_salvage_request_id(frame), f"invalid envelope: {problems}")
            return
        task = asyncio.create_task(self._handle(request))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _handle(self, request: Envelope) -> None:
        token = request_id_var.set(str(request.request_id))
        result_type = f"{request.type}{RESULT_SUFFIX}"
        started = time.perf_counter()
        try:
            result = await self._route(request)
            self._send(request.request_id, result_type, payload=result)
            logger.info(
                "request handled",
                extra={"msg_type": request.type, "duration_ms": _elapsed_ms(started)},
            )
        except WorkerError as exc:
            logger.warning(
                "request rejected",
                extra={"msg_type": request.type, "code": exc.info.code, "reason": exc.info.message},
            )
            self._send(request.request_id, result_type, error=exc.info)
        except Exception:
            logger.exception("request failed", extra={"msg_type": request.type})
            self._send(
                request.request_id,
                result_type,
                error=ErrorInfo(
                    code=ErrorCode.INTERNAL,
                    message=f"internal error while handling '{request.type}'; see the worker log",
                    retryable=False,
                ),
            )
        finally:
            request_id_var.reset(token)

    async def _route(self, request: Envelope) -> dict[str, object]:
        if request.protocol_version != PROTOCOL_VERSION:
            raise WorkerError(
                ErrorCode.UNSUPPORTED_PROTOCOL,
                f"worker speaks protocol {PROTOCOL_VERSION}, got {request.protocol_version}",
            )
        if request.error is not None:
            raise WorkerError(ErrorCode.INVALID_MESSAGE, "requests must not carry an error")
        route = ROUTES.get(request.type)
        if route is None:
            raise WorkerError(ErrorCode.UNKNOWN_TYPE, f"unknown request type '{request.type}'")
        if self._stopping and request.type != MessageType.SHUTDOWN:
            raise WorkerError(ErrorCode.SHUTTING_DOWN, "worker is shutting down", retryable=True)
        try:
            params = route.payload.model_validate(request.payload or {})
        except ValidationError as exc:
            raise WorkerError(
                ErrorCode.INVALID_PAYLOAD, f"invalid payload: {exc.errors()[0]['msg']}"
            ) from exc
        try:
            result = await asyncio.wait_for(route.handler(self._ctx, params), route.timeout_s)
        except TimeoutError as exc:
            raise WorkerError(
                ErrorCode.TIMEOUT, f"'{request.type}' exceeded {route.timeout_s:g}s", retryable=True
            ) from exc
        except StoreUnavailableError as exc:
            raise WorkerError(ErrorCode.STORE_UNAVAILABLE, str(exc), retryable=True) from exc
        except NotFoundError as exc:
            raise WorkerError(ErrorCode.NOT_FOUND, str(exc)) from exc
        except NotAllowedError as exc:
            raise WorkerError(ErrorCode.NOT_ALLOWED, str(exc)) from exc
        except (
            InvalidCursorError,
            InvalidTransitionError,
            DiscoveryStartError,
            CrawlStartError,
            ResolutionError,
            LatinoError,
        ) as exc:
            raise WorkerError(ErrorCode.INVALID_PAYLOAD, str(exc)) from exc
        return result.model_dump(mode="json")

    # ---- output --------------------------------------------------------------------------

    def _protocol_error(self, request_id: UUID | None, message: str) -> None:
        logger.warning("protocol error", extra={"reason": message})
        self._send(
            request_id or uuid4(),
            MessageType.PROTOCOL_ERROR,
            error=ErrorInfo(
                code=ErrorCode.INVALID_MESSAGE, message=message[:2000], retryable=False
            ),
        )

    def _send(
        self,
        request_id: UUID,
        message_type: str,
        *,
        payload: dict[str, object] | None = None,
        error: ErrorInfo | None = None,
    ) -> None:
        envelope = Envelope(
            request_id=request_id,
            protocol_version=PROTOCOL_VERSION,
            type=message_type,
            timestamp=datetime.now(UTC),
            payload=payload,
            error=error,
        )
        frame = envelope.model_dump_json().encode() + b"\n"
        if len(frame) > MAX_LINE_BYTES:
            logger.error(
                "result too large to send", extra={"msg_type": message_type, "bytes": len(frame)}
            )
            self._send(
                request_id,
                message_type,
                error=ErrorInfo(
                    code=ErrorCode.INTERNAL, message="result too large", retryable=False
                ),
            )
            return
        try:
            self._out.write(frame)
            self._out.flush()
        except OSError:
            logger.warning("desktop core stopped reading stdout; shutting down", exc_info=True)
            self._request_shutdown()

    # ---- shutdown ------------------------------------------------------------------------

    def _request_shutdown(self) -> None:
        if not self._stop.is_set():
            _arm_hard_exit("shutdown requested")
            self._stop.set()

    async def _drain(self) -> None:
        if not self._tasks:
            return
        _, pending = await asyncio.wait(set(self._tasks), timeout=SHUTDOWN_GRACE_S - 1)
        for task in pending:
            task.cancel()
        if pending:
            logger.warning("cancelled %d in-flight request(s) at shutdown", len(pending))


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


def main() -> None:
    protocol_out = sys.stdout.buffer
    sys.stdout = sys.stderr  # a stray print() must never corrupt protocol frames
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        configure_logging("info")
        logger.error("invalid worker settings: %s", exc)
        sys.exit(2)
    configure_logging(settings.log_level)
    asyncio.run(Worker(settings, protocol_out).run())
