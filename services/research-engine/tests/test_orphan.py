"""The worker must never outlive its parent, even when the parent is killed without warning."""

import os
import subprocess
import sys
import time
from pathlib import Path

# Stand-in for the desktop core: starts the worker, reports its interpreter PID, then idles.
PARENT = """
import json, subprocess, sys, time
worker = subprocess.Popen(
    [sys.executable, "-m", "research_engine"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
)
ready = json.loads(worker.stdout.readline())
print(ready["payload"]["pid"], flush=True)
time.sleep(120)
"""


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return False
        try:
            return bool(kernel32.WaitForSingleObject(handle, 0) == 0x102)  # WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def test_worker_exits_when_its_parent_is_killed(tmp_path: Path) -> None:
    parent = subprocess.Popen(
        [sys.executable, "-c", PARENT],
        stdout=subprocess.PIPE,
        env={**os.environ, "LEAD_DATA_DIR": str(tmp_path)},
    )
    try:
        assert parent.stdout is not None
        worker_pid = int(parent.stdout.readline())
        assert _alive(worker_pid)

        parent.kill()  # no shutdown message, no cleanup: like a crash or power loss of the core
        parent.wait(timeout=10)

        deadline = time.monotonic() + 10
        while _alive(worker_pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not _alive(worker_pid), "worker outlived its parent"
    finally:
        if parent.poll() is None:
            parent.kill()
