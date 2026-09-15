"""RAM and VRAM as the local AI layer sees them, and the gate crawling waits on under pressure.

While a model works, crawling and browser renders wait whenever free RAM or free VRAM is below the
configured limits (`ai_resource_limits`), so a model and Chromium never race each other into an
out-of-memory crash. The wait is bounded: after `MAX_WAIT_S` one URL goes ahead and the gate is
checked again. With no AI work the gate never holds crawling back.
"""

import asyncio
import logging
import os
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from research_engine.domain import utc_now
from research_engine.gpu import probe_gpu

logger = logging.getLogger(__name__)

MIB: Final = 1024 * 1024
SAMPLE_MAX_AGE_S: Final = 2.0
POLL_S: Final = 2.0
MAX_WAIT_S: Final = 120.0


def memory() -> tuple[int, int] | None:
    """(total, available) physical memory in bytes, or None when the OS does not say."""
    if sys.platform == "win32":
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = (
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            )

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return int(status.ullTotalPhys), int(status.ullAvailPhys)
    try:
        page = os.sysconf("SC_PAGE_SIZE")
        return os.sysconf("SC_PHYS_PAGES") * page, os.sysconf("SC_AVPHYS_PAGES") * page
    except (ValueError, OSError):
        return None


def vram() -> tuple[int, int] | None:
    """(total, free) MiB summed over the NVIDIA GPUs nvidia-smi reports, or None."""
    devices = [
        device
        for device in probe_gpu().devices
        if device.memory_total_mib is not None and device.memory_free_mib is not None
    ]
    if not devices:
        return None
    return (
        sum(device.memory_total_mib or 0 for device in devices),
        sum(device.memory_free_mib or 0 for device in devices),
    )


_cpu_lock = threading.Lock()
_cpu_last: list[int] = []  # idle, total in 100-ns ticks at the previous call


def cpu_percent() -> float | None:
    """The busy share of every CPU since the previous call, 0 to 100. None on the first call and
    wherever the OS does not say (only Windows is read: `GetSystemTimes`)."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
    if not ctypes.windll.kernel32.GetSystemTimes(
        ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
    ):
        return None

    def ticks(value: wintypes.FILETIME) -> int:
        return (value.dwHighDateTime << 32) | value.dwLowDateTime

    # the kernel time includes the idle time
    now_idle, now_total = ticks(idle), ticks(kernel) + ticks(user)
    with _cpu_lock:
        previous = list(_cpu_last)
        _cpu_last[:] = [now_idle, now_total]
    if not previous or now_total <= previous[1]:
        return None
    busy = 1 - (now_idle - previous[0]) / (now_total - previous[1])
    return round(min(100.0, max(0.0, busy * 100)), 1)


@dataclass(frozen=True, slots=True)
class Sample:
    ram_total_mib: int | None
    ram_free_mib: int | None
    vram_total_mib: int | None
    vram_free_mib: int | None
    sampled_at: datetime


def take_sample() -> Sample:
    ram = memory()
    gpu = vram()
    return Sample(
        ram_total_mib=ram[0] // MIB if ram else None,
        ram_free_mib=ram[1] // MIB if ram else None,
        vram_total_mib=gpu[0] if gpu else None,
        vram_free_mib=gpu[1] if gpu else None,
        sampled_at=utc_now(),
    )


class ResourceMonitor:
    """Samples at most every SAMPLE_MAX_AGE_S (nvidia-smi is a process) and holds the limits."""

    def __init__(
        self,
        sampler: Callable[[], Sample] = take_sample,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sampler = sampler
        self._clock = clock
        self._lock = threading.Lock()
        self._last: tuple[float, Sample] | None = None
        self.min_free_ram_mib = 1024
        self.min_free_vram_mib = 512
        #: Whether local AI is working now; set by the AI service.
        self.busy: Callable[[], bool] = lambda: False

    def sample(self) -> Sample:
        with self._lock:
            now = self._clock()
            if self._last is None or now - self._last[0] > SAMPLE_MAX_AGE_S:
                self._last = (now, self._sampler())
            return self._last[1]

    def pressure(self, sample: Sample) -> list[str]:
        reasons = []
        if sample.ram_free_mib is not None and sample.ram_free_mib < self.min_free_ram_mib:
            reasons.append("low_ram")
        if sample.vram_free_mib is not None and sample.vram_free_mib < self.min_free_vram_mib:
            reasons.append("low_vram")
        return reasons

    def holding(self) -> bool:
        """True when crawling must wait: AI works and memory is below a limit."""
        return self.busy() and bool(self.pressure(self.sample()))

    async def wait_clear(self, max_wait_s: float = MAX_WAIT_S, poll_s: float = POLL_S) -> float:
        """Waits while `holding`, at most `max_wait_s`. Returns the seconds waited."""
        started = self._clock()
        logged = False
        while await asyncio.to_thread(self.holding):
            waited = self._clock() - started
            if waited >= max_wait_s:
                break
            if not logged:
                logger.info("crawling waits for memory while local AI works")
                logged = True
            await asyncio.sleep(poll_s)
        return self._clock() - started
