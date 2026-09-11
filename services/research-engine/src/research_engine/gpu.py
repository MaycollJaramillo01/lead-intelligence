"""NVIDIA GPU detection through nvidia-smi plus a CUDA driver API check.

Nothing is assumed about the model of GPU: absent tools or drivers degrade explicitly.
"""

import ctypes
import logging
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from typing import Any

from research_engine.contracts import ComponentState, GpuDevice, GpuStatus

logger = logging.getLogger(__name__)

NVIDIA_SMI_TIMEOUT_S = 5.0
_QUERY = "index,name,memory.total,memory.free,driver_version"


def _int_or_none(value: str) -> int | None:
    try:
        return int(float(value))
    except ValueError:
        return None  # "[N/A]", "[Not Supported]"


def parse_nvidia_smi(output: str) -> list[GpuDevice]:
    """Parses `--query-gpu=<_QUERY> --format=csv,noheader,nounits` output."""
    devices: list[GpuDevice] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 5:
            raise ValueError(f"unexpected nvidia-smi line: {line!r}")
        # parse from both ends so a comma inside the GPU name cannot shift columns
        driver = parts[-1]
        devices.append(
            GpuDevice(
                index=int(parts[0]),
                name=", ".join(parts[1:-3]),
                memory_total_mib=_int_or_none(parts[-3]),
                memory_free_mib=_int_or_none(parts[-2]),
                driver_version=None if not driver or driver.startswith("[") else driver,
            )
        )
    return devices


def cuda_driver_version() -> str | None:
    """Asks the CUDA driver API (not the toolkit) which CUDA version the driver supports."""
    library: Any
    try:
        if sys.platform == "win32":
            library = ctypes.WinDLL("nvcuda.dll")
        elif sys.platform.startswith("linux"):
            library = ctypes.CDLL("libcuda.so.1")
        else:
            return None
    except OSError:
        return None
    version = ctypes.c_int(0)
    if library.cuDriverGetVersion(ctypes.byref(version)) != 0 or version.value <= 0:
        return None
    return f"{version.value // 1000}.{(version.value % 1000) // 10}"


def probe_gpu() -> GpuStatus:
    started = time.perf_counter()
    cuda = cuda_driver_version()

    def status(
        state: ComponentState,
        summary: str,
        devices: list[GpuDevice] | None = None,
        error: str | None = None,
    ) -> GpuStatus:
        return GpuStatus(
            state=state,
            summary=summary,
            error=error,
            checked_at=datetime.now(UTC),
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
            devices=devices or [],
            cuda_available=cuda is not None,
            cuda_driver_version=cuda,
        )

    executable = shutil.which("nvidia-smi")
    if executable is None:
        return status(
            ComponentState.UNAVAILABLE,
            "No NVIDIA GPU detected (nvidia-smi not found); local AI would run on CPU",
            error="nvidia-smi not found",
        )
    extra: dict[str, Any] = {}
    if sys.platform == "win32":
        extra["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        completed = subprocess.run(  # noqa: S603 - absolute path from shutil.which, fixed args
            [executable, f"--query-gpu={_QUERY}", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=NVIDIA_SMI_TIMEOUT_S,
            check=False,
            **extra,
        )
    except subprocess.TimeoutExpired:
        return status(
            ComponentState.ERROR,
            f"nvidia-smi did not answer within {NVIDIA_SMI_TIMEOUT_S:g}s",
            error="timeout",
        )
    except OSError as exc:
        return status(ComponentState.ERROR, "nvidia-smi could not be executed", error=str(exc))

    if completed.returncode != 0:
        reason = (completed.stderr or completed.stdout).strip().splitlines()
        detail = reason[0] if reason else f"exit code {completed.returncode}"
        return status(
            ComponentState.UNAVAILABLE, f"nvidia-smi reported no usable GPU: {detail}", error=detail
        )
    try:
        devices = parse_nvidia_smi(completed.stdout)
    except ValueError as exc:
        logger.warning("could not parse nvidia-smi output", exc_info=True)
        return status(ComponentState.ERROR, "nvidia-smi output not understood", error=str(exc))
    if not devices:
        return status(ComponentState.UNAVAILABLE, "nvidia-smi listed no GPUs", error="no devices")

    first = devices[0]
    memory = (
        f"{first.memory_total_mib} MiB ({first.memory_free_mib} MiB free)"
        if first.memory_total_mib is not None
        else "VRAM unknown"
    )
    if cuda is None:
        return status(
            ComponentState.DEGRADED,
            f"{first.name} · {memory} · CUDA driver API unavailable",
            devices,
            error="nvcuda not loadable",
        )
    return status(ComponentState.OK, f"{first.name} · {memory} · CUDA {cuda}", devices)
