"""Level 2 of acquisition: Chromium through Playwright, only when a verifiable criterion says so.

- One browser process is launched lazily and reused; every render gets a fresh context, so cookies
  and storage never travel from one site to another. The engine closes the browser when no crawl
  is running.
- Images, media, fonts and stylesheets are not downloaded (the DOM text does not need them), only
  http(s) requests to public addresses go out, and downloads are refused.
- Waiting is based on signals (DOM ready, network idle, visible text) with hard caps, never on
  fixed sleeps. Pages and contexts are closed in `finally`, also when the crawl is cancelled.
- On Windows the worker puts itself in a kill-on-close job object before the first launch, so the
  Playwright driver and every Chromium process die with the worker, however it ends (D-007).

Which browser: Playwright's own Chromium when it is installed, otherwise the Chromium-based Edge or
Chrome already on the machine. None of them means the fallback is unavailable, which is reported.
"""

import asyncio
import contextlib
import logging
import sys
import time
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlsplit

from playwright.async_api import Browser, Playwright, Route, async_playwright
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from research_engine.crawl.guard import UnsafeTargetError, public_addresses

logger = logging.getLogger(__name__)

NAVIGATION_TIMEOUT_MS: Final = 15_000
NETWORK_IDLE_MS: Final = 5_000
VISIBLE_TEXT_MS: Final = 3_000
RENDER_DEADLINE_S: Final = 30.0
BLOCKED_RESOURCES: Final = frozenset(
    {"image", "media", "font", "stylesheet", "websocket", "eventsource", "manifest", "texttrack",
     "ping"}
)  # fmt: skip
#: Playwright's bundled Chromium first, then an installed Chromium-based browser.
CHANNELS: Final = (None, "msedge", "chrome")


class BrowserUnavailableError(RuntimeError):
    """No Chromium could be launched: the fallback is off for this crawl, and the run says so."""


@dataclass(frozen=True, slots=True)
class Rendered:
    html: str
    final_url: str
    status: int | None
    elapsed_ms: int
    blocked_requests: int
    screenshot: bytes | None
    browser: str


def contain_child_processes() -> bool:
    """Windows: every process this one starts from now on dies with it (kill-on-close job object).

    The job handle is deliberately never closed. Closing it is what kills the tree, and that must
    only happen when this process ends - even when it is killed without a chance to clean up.
    """
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = (
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        )

    class ExtendedLimits(ctypes.Structure):
        _fields_ = (
            ("BasicLimitInformation", BasicLimits),
            ("IoInfo", ctypes.c_uint64 * 6),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        )

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    kernel32.SetInformationJobObject.argtypes = (
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD
    )  # fmt: skip
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return False
    limits = ExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    extended_limit_information = 9
    if not kernel32.SetInformationJobObject(
        job, extended_limit_information, ctypes.byref(limits), ctypes.sizeof(limits)
    ):
        return False
    return bool(kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess()))


class BrowserPool:
    """At most `concurrency` renders at once, on one lazily launched, reused browser."""

    def __init__(
        self,
        *,
        user_agent: str,
        allow_private: bool,
        concurrency: int,
        launch_args: tuple[str, ...] = (),
    ) -> None:
        self._user_agent = user_agent
        self._allow_private = allow_private
        self._slots = asyncio.Semaphore(max(1, concurrency))
        self._launch_args = launch_args
        self._lock = asyncio.Lock()
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._contained = False
        #: Why no browser could be launched. Kept, so every page does not pay three failed launches.
        self._unavailable: str | None = None

    async def _ensure(self) -> Browser:
        async with self._lock:
            if self._browser is not None and self._browser.is_connected():
                return self._browser
            if self._unavailable is not None:
                raise BrowserUnavailableError(self._unavailable)
            if not self._contained:
                self._contained = contain_child_processes()
                logger.info("browser processes contained", extra={"contained": self._contained})
            if self._playwright is None:
                self._playwright = await async_playwright().start()
            problems: list[str] = []
            for channel in CHANNELS:
                try:
                    self._browser = await self._playwright.chromium.launch(
                        headless=True, channel=channel, args=list(self._launch_args)
                    )
                except PlaywrightError as exc:
                    problems.append(f"{channel or 'bundled chromium'}: {str(exc).splitlines()[0]}")
                    continue
                logger.info("browser launched", extra={"channel": channel or "chromium"})
                return self._browser
            await self._stop_playwright()
            self._unavailable = "; ".join(problems)[:1_000]
            raise BrowserUnavailableError(self._unavailable)

    async def render(self, url: str, *, screenshot: bool) -> Rendered:
        async with self._slots:
            browser = await self._ensure()
            started = time.perf_counter()
            blocked = 0

            async def guard(route: Route) -> None:
                nonlocal blocked
                request = route.request
                parts = urlsplit(request.url)
                refuse = request.resource_type in BLOCKED_RESOURCES or parts.scheme not in (
                    "http",
                    "https",
                )
                if not refuse and parts.hostname:
                    port = parts.port or (443 if parts.scheme == "https" else 80)
                    try:
                        await public_addresses(
                            parts.hostname, port, allow_private=self._allow_private
                        )
                    except (UnsafeTargetError, OSError):
                        refuse = True
                if refuse:
                    blocked += 1
                    await route.abort("blockedbyclient")
                else:
                    await route.continue_()

            context = await browser.new_context(
                user_agent=self._user_agent,
                accept_downloads=False,
                service_workers="block",
                java_script_enabled=True,
                viewport={"width": 1366, "height": 900},
                locale="en-US",
            )
            try:
                await context.route("**/*", guard)
                page = await context.new_page()
                async with asyncio.timeout(RENDER_DEADLINE_S):
                    response = await page.goto(
                        url, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS
                    )
                    with contextlib.suppress(PlaywrightTimeoutError):
                        await page.wait_for_load_state("networkidle", timeout=NETWORK_IDLE_MS)
                    with contextlib.suppress(PlaywrightTimeoutError):
                        await page.wait_for_function(
                            "() => document.body && document.body.innerText.trim().length > 30",
                            timeout=VISIBLE_TEXT_MS,
                        )
                    html = await page.content()
                    image = await page.screenshot(type="png") if screenshot else None
                return Rendered(
                    html=html,
                    final_url=page.url,
                    status=response.status if response is not None else None,
                    elapsed_ms=round((time.perf_counter() - started) * 1000),
                    blocked_requests=blocked,
                    screenshot=image,
                    browser=f"chromium {browser.version}",
                )
            finally:
                with contextlib.suppress(Exception):
                    await asyncio.shield(context.close())

    async def close(self) -> None:
        async with self._lock:
            if self._browser is not None:
                with contextlib.suppress(Exception):
                    await self._browser.close()
                self._browser = None
            await self._stop_playwright()

    async def _stop_playwright(self) -> None:
        if self._playwright is not None:
            with contextlib.suppress(Exception):
                await self._playwright.stop()
            self._playwright = None
