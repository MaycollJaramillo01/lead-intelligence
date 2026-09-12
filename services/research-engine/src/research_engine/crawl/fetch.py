"""Level 1 of acquisition: plain HTTP with httpx, bounded in every direction.

- Only public addresses (`guard.GuardedBackend`), no proxy from the environment, no credentials.
- Redirects are followed by hand, at most `MAX_REDIRECTS`, each hop checked and recorded.
- Separate connect/read/write/pool timeouts plus a deadline per attempt, so a server trickling bytes
  cannot hold a slot forever.
- Retries only for transient failures (timeouts, resets, 408, 429, 5xx), with exponential backoff
  and jitter, never shorter than the `Retry-After` the server asked for.
- Bodies are read as a stream and cut at a byte limit counted after decompression; the declared
  type is checked before reading and the first bytes are sniffed: a PDF served as HTML is refused.
- `If-None-Match` / `If-Modified-Since` when an earlier fetch left validators.
- Politeness per host: a concurrency slot and a minimum interval, stretched when a host pushes back.
"""

import asyncio
import contextlib
import email.utils
import random
import socket
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final

import httpx

from research_engine.crawl.extract import looks_binary
from research_engine.crawl.guard import GuardedBackend, UnsafeTargetError
from research_engine.crawl.urls import canonical, host_of
from research_engine.domain import JsonObject

MAX_REDIRECTS: Final = 5
MAX_ATTEMPTS: Final = 3
BACKOFF_BASE_S: Final = 1.0
#: A server asking for a longer wait than this gets the URL marked rate limited instead.
MAX_WAIT_S: Final = 60.0
TRANSIENT_STATUS: Final = frozenset({408, 429, 500, 502, 503, 504})
BLOCKED_STATUS: Final = frozenset({401, 403, 407, 451})
KEPT_HEADERS: Final = (
    "age", "cache-control", "content-encoding", "content-language", "content-length",
    "content-type", "date", "etag", "expires", "last-modified", "location", "retry-after",
    "server", "vary", "x-robots-tag",
)  # fmt: skip
HTML_TYPES: Final = frozenset({"text/html", "application/xhtml+xml"})
#: robots.txt is plain text; an HTML answer is parsed as text too and simply holds no rules.
ROBOTS_TYPES: Final = frozenset({"text/plain", "text/html"})
SITEMAP_TYPES: Final = frozenset(
    {"application/xml", "text/xml", "application/gzip", "application/x-gzip", "text/plain",
     "application/octet-stream"}
)  # fmt: skip


@dataclass(frozen=True, slots=True)
class FetchPolicy:
    """Timeouts are settings so the tests can shorten them; the defaults are the documented ones."""

    connect_timeout_s: float = 5.0
    read_timeout_s: float = 15.0
    attempt_deadline_s: float = 30.0
    backoff_base_s: float = BACKOFF_BASE_S
    #: Scales the per-host interval. 1.0 in the worker; the test suite shrinks it.
    spacing_factor: float = 1.0
    allow_private: bool = False


@dataclass(slots=True)
class Fetched:
    """The outcome of one URL, including every hop and retry it took. `error` is a reason code."""

    url: str
    final_url: str
    status: int | None = None
    content_type: str | None = None
    headers: JsonObject = field(default_factory=dict)
    body: bytes | None = None
    redirects: list[JsonObject] = field(default_factory=list)
    requests: int = 0
    retries: int = 0
    elapsed_ms: int = 0
    error: str | None = None
    message: str | None = None
    retry_after_s: float | None = None

    @property
    def not_modified(self) -> bool:
        return self.status == 304


class _Failure(Exception):
    def __init__(
        self, code: str, message: str, *, transient: bool, retry_after_s: float | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.transient = transient
        self.retry_after_s = retry_after_s


def retry_after(value: str | None, now: datetime | None = None) -> float | None:
    """Seconds from a `Retry-After` header, in either of its two forms."""
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - (now or datetime.now(UTC))).total_seconds())


def backoff_s(attempt: int, asked: float | None, base: float) -> float:
    computed = base * 2.0 ** (attempt - 1) * random.uniform(0.5, 1.0)  # noqa: S311 - jitter
    return max(computed, asked or 0.0)


def media_type(content_type: str | None) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


class HostGate:
    """Per-host politeness: `per_host` concurrent requests and at least `interval` between starts.

    A host that answers 429 or 5xx gets a cool-down added to its interval, which decays again as it
    answers normally: the crawl slows down for that host alone, not for every site.
    """

    def __init__(
        self, per_host: int, requests_per_minute: int, spacing_factor: float = 1.0
    ) -> None:
        self._per_host = per_host
        self._interval = 60.0 / requests_per_minute * spacing_factor
        self._slots: dict[str, asyncio.Semaphore] = {}
        self._next: dict[str, float] = {}
        self._penalty: dict[str, float] = {}
        self._delay: dict[str, float] = {}

    def set_crawl_delay(self, host: str, seconds: float) -> None:
        self._delay[host] = max(self._delay.get(host, 0.0), seconds)

    def penalise(self, host: str, seconds: float) -> None:
        self._penalty[host] = min(MAX_WAIT_S, max(self._penalty.get(host, 0.0) * 2, seconds, 1.0))

    def relax(self, host: str) -> None:
        if host in self._penalty:
            self._penalty[host] /= 2
            if self._penalty[host] < 0.5:
                del self._penalty[host]

    @contextlib.asynccontextmanager
    async def slot(self, host: str, overall: asyncio.Semaphore) -> AsyncIterator[None]:
        """The host's slot, then one of the `overall` ones, then the host's spacing.

        The spacing is measured right before the request starts, so it holds however long the wait
        for a free slot was. The engine runs at most `overall / per_host` sites at once, so a slot
        held during that short wait never starves another host.
        """
        semaphore = self._slots.setdefault(host, asyncio.Semaphore(self._per_host))
        async with semaphore, overall:
            loop = asyncio.get_running_loop()
            wait = self._next.get(host, 0.0) - loop.time()
            if wait > 0:
                await asyncio.sleep(wait)
            spacing = max(self._interval, self._delay.get(host, 0.0))
            jitter = random.uniform(1.0, 1.1)  # noqa: S311 - politeness jitter
            self._next[host] = loop.time() + spacing * jitter + self._penalty.get(host, 0.0)
            yield


def guarded_client(user_agent: str, policy: FetchPolicy, max_connections: int) -> httpx.AsyncClient:
    transport = httpx.AsyncHTTPTransport(
        limits=httpx.Limits(max_connections=max_connections, max_keepalive_connections=8),
        trust_env=False,
    )
    # ponytail: swaps the pool's network backend, a private attribute of httpx. It is the one place
    # the resolved address can be checked right before connecting; test_crawl's SSRF tests fail
    # loudly if an httpx upgrade moves it.
    transport._pool._network_backend = GuardedBackend(allow_private=policy.allow_private)
    return httpx.AsyncClient(
        transport=transport,
        timeout=httpx.Timeout(
            connect=policy.connect_timeout_s,
            read=policy.read_timeout_s,
            write=policy.read_timeout_s,
            pool=policy.attempt_deadline_s,
        ),
        follow_redirects=False,
        trust_env=False,
        headers={
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8",
            "Accept-Language": "en-US,en;q=0.8,es;q=0.6",
            "Accept-Encoding": "gzip, deflate",
        },
    )


class HttpFetcher:
    def __init__(
        self,
        client: httpx.AsyncClient,
        gate: HostGate,
        policy: FetchPolicy,
        concurrency: asyncio.Semaphore,
    ) -> None:
        self._client = client
        self._gate = gate
        self._policy = policy
        self._concurrency = concurrency

    async def fetch(
        self,
        url: str,
        *,
        accept: frozenset[str],
        max_bytes: int,
        validators: JsonObject | None = None,
        permit: Callable[[str], str | None] = lambda _: None,
    ) -> Fetched:
        """GET `url`. `permit` vets every redirect target and returns a reason to stop at it.

        `elapsed_ms` is the time spent talking to servers, over every hop and attempt: politeness
        waits and backoff sleeps are not latency, so they are not counted.
        """
        result = Fetched(url=url, final_url=url)
        current = url
        seen = {url}
        try:
            for _hop in range(MAX_REDIRECTS + 1):
                response = await self._with_retries(current, result, validators, accept, max_bytes)
                location = response.headers.get("location")
                if not (300 <= response.status_code < 400 and response.status_code != 304):
                    break
                target = canonical(location or "", current)
                result.redirects.append(
                    {"url": current, "status": response.status_code, "location": target}
                )
                if target is None:
                    raise _Failure(
                        "redirect_invalid", f"unusable Location {location!r}", transient=False
                    )
                if target in seen:
                    raise _Failure(
                        "redirect_loop", f"{target} was already visited", transient=False
                    )
                if (refusal := permit(target)) is not None:
                    raise _Failure(refusal, f"redirect to {target} refused", transient=False)
                seen.add(target)
                current = result.final_url = target
                validators = None  # validators belong to the URL they came from
            else:
                raise _Failure(
                    "too_many_redirects", f"more than {MAX_REDIRECTS} redirects", transient=False
                )
        except _Failure as failure:
            result.error, result.message = failure.code, str(failure)[:2_000]
            result.retry_after_s = failure.retry_after_s
            result.body = None
        return result

    async def _with_retries(
        self,
        url: str,
        result: Fetched,
        validators: JsonObject | None,
        accept: frozenset[str],
        max_bytes: int,
    ) -> httpx.Response:
        host = host_of(url)
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                async with self._gate.slot(host, self._concurrency):
                    result.requests += 1
                    started = time.perf_counter()
                    try:
                        response = await asyncio.wait_for(
                            self._attempt(url, result, validators, accept, max_bytes),
                            self._policy.attempt_deadline_s,
                        )
                    finally:
                        result.elapsed_ms += round((time.perf_counter() - started) * 1000)
                self._gate.relax(host)
                return response
            except TimeoutError:
                failure = _Failure(
                    "timeout",
                    f"no complete answer within {self._policy.attempt_deadline_s:g}s",
                    transient=True,
                )
            except _Failure as raised:
                failure = raised
            if not failure.transient or attempt == MAX_ATTEMPTS:
                raise failure
            wait = backoff_s(attempt, failure.retry_after_s, self._policy.backoff_base_s)
            if wait > MAX_WAIT_S:
                raise failure
            if failure.code in ("rate_limited", "server_error"):
                self._gate.penalise(host, wait)
            result.retries += 1
            await asyncio.sleep(wait)
        raise AssertionError("unreachable")  # pragma: no cover

    async def _attempt(
        self,
        url: str,
        result: Fetched,
        validators: JsonObject | None,
        accept: frozenset[str],
        max_bytes: int,
    ) -> httpx.Response:
        headers: dict[str, str] = {}
        if validators:
            if isinstance(etag := validators.get("etag"), str):
                headers["If-None-Match"] = etag
            if isinstance(modified := validators.get("last-modified"), str):
                headers["If-Modified-Since"] = modified
        try:
            async with self._client.stream("GET", url, headers=headers) as response:
                result.status = response.status_code
                result.content_type = response.headers.get("content-type")
                result.headers = {
                    name: response.headers[name]
                    for name in KEPT_HEADERS
                    if name in response.headers
                }
                self._check_status(response)
                if response.status_code == 200:
                    result.body = await self._read(response, accept, max_bytes)
                return response
        except (UnsafeTargetError, socket.gaierror) as exc:
            code = "unsafe_target" if isinstance(exc, UnsafeTargetError) else "dns_error"
            raise _Failure(code, str(exc), transient=False) from exc
        except httpx.TimeoutException as exc:
            raise _Failure("timeout", f"timed out: {type(exc).__name__}", transient=True) from exc
        except httpx.ConnectError as exc:
            raise _Failure(
                "connection_failed", str(exc) or "connection failed", transient=True
            ) from exc
        except (httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError) as exc:
            raise _Failure("network_error", str(exc) or type(exc).__name__, transient=True) from exc
        except httpx.DecodingError as exc:
            raise _Failure("decoding_error", str(exc), transient=False) from exc

    @staticmethod
    def _check_status(response: httpx.Response) -> None:
        status = response.status_code
        asked = retry_after(response.headers.get("retry-after"))
        if status == 429:
            raise _Failure(
                "rate_limited",
                "the server asked us to slow down",
                transient=True,
                retry_after_s=asked,
            )
        if status in TRANSIENT_STATUS:
            raise _Failure("server_error", f"HTTP {status}", transient=True, retry_after_s=asked)
        if status in BLOCKED_STATUS:
            raise _Failure("blocked", f"HTTP {status}: access refused", transient=False)
        if status in (404, 410):
            raise _Failure("not_found", f"HTTP {status}", transient=False)
        if status >= 400 or (status >= 200 and status not in (200, 204) and status < 300):
            raise _Failure("http_error", f"HTTP {status}", transient=False)

    @staticmethod
    async def _read(response: httpx.Response, accept: frozenset[str], max_bytes: int) -> bytes:
        declared = media_type(response.headers.get("content-type"))
        if declared and declared not in accept:
            raise _Failure(
                "unsupported_type", f"{declared} is not an accepted type", transient=False
            )
        length = response.headers.get("content-length", "")
        if length.isdigit() and int(length) > max_bytes:
            raise _Failure(
                "too_large", f"declared {length} bytes, limit {max_bytes}", transient=False
            )
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():  # decoded bytes: a gzip bomb is cut here too
            size += len(chunk)
            if size > max_bytes:
                raise _Failure("too_large", f"more than {max_bytes} bytes", transient=False)
            chunks.append(chunk)
        body = b"".join(chunks)
        if accept is not SITEMAP_TYPES and looks_binary(body):
            raise _Failure("unsupported_type", "the body is a binary file", transient=False)
        return body
