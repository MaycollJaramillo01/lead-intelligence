import shutil
import subprocess
from typing import Any

import pytest

from research_engine import gpu
from research_engine.contracts import ComponentState

SAMPLE = "0, NVIDIA GeForce RTX 4060, 8188, 6935, 610.88\n1, Synthetic, Card, 16384, [N/A], [N/A]\n"


def test_parses_devices_including_missing_values_and_commas_in_names() -> None:
    first, second = gpu.parse_nvidia_smi(SAMPLE)
    assert (first.index, first.name, first.memory_total_mib, first.memory_free_mib) == (
        0,
        "NVIDIA GeForce RTX 4060",
        8188,
        6935,
    )
    assert first.driver_version == "610.88"
    assert (second.name, second.memory_free_mib, second.driver_version) == (
        "Synthetic, Card",
        None,
        None,
    )


def test_malformed_output_is_rejected() -> None:
    with pytest.raises(ValueError, match="unexpected"):
        gpu.parse_nvidia_smi("garbage")


def _fake_smi(
    monkeypatch: pytest.MonkeyPatch,
    *,
    returncode: int = 0,
    stdout: str = SAMPLE,
    cuda: str | None = "12.8",
) -> None:
    def run(args: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, returncode, stdout, "")

    monkeypatch.setattr(shutil, "which", lambda _: "C:/fake/nvidia-smi.exe")
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(gpu, "cuda_driver_version", lambda: cuda)


def test_gpu_with_cuda_is_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_smi(monkeypatch)
    status = gpu.probe_gpu()
    assert status.state is ComponentState.OK
    assert status.cuda_available
    assert status.cuda_driver_version == "12.8"
    assert len(status.devices) == 2


def test_gpu_without_cuda_driver_is_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_smi(monkeypatch, cuda=None)
    status = gpu.probe_gpu()
    assert status.state is ComponentState.DEGRADED
    assert not status.cuda_available


def test_driver_without_devices_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_smi(monkeypatch, returncode=9, stdout="No devices were found")
    status = gpu.probe_gpu()
    assert status.state is ComponentState.UNAVAILABLE
    assert status.error == "No devices were found"


def test_missing_nvidia_smi_degrades_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _: None)
    status = gpu.probe_gpu()
    assert status.state is ComponentState.UNAVAILABLE
    assert status.devices == []
    assert "nvidia-smi not found" in status.summary


def test_hung_nvidia_smi_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def run(args: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(args, gpu.NVIDIA_SMI_TIMEOUT_S)

    monkeypatch.setattr(shutil, "which", lambda _: "C:/fake/nvidia-smi.exe")
    monkeypatch.setattr(subprocess, "run", run)
    status = gpu.probe_gpu()
    assert status.state is ComponentState.ERROR
    assert status.error == "timeout"


@pytest.mark.local_services
def test_real_gpu_probe() -> None:
    if shutil.which("nvidia-smi") is None:
        pytest.skip("nvidia-smi is not installed on this machine")
    status = gpu.probe_gpu()
    assert status.state in {ComponentState.OK, ComponentState.DEGRADED}, status.summary
    assert status.devices
    assert all(d.memory_total_mib and d.memory_total_mib > 0 for d in status.devices)
