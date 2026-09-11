"""Ollama detection. Mock transports simulate API answers; one test probes a real closed port,
and one (marked local_services) talks to a real local Ollama when it is running."""

import httpx
import pytest

from research_engine.contracts import ComponentState
from research_engine.ollama import probe_ollama
from research_engine.settings import DEFAULT_OLLAMA_URL

pytestmark = pytest.mark.anyio

URL = "http://127.0.0.1:11434"
VERSION = httpx.Response(200, json={"version": "0.0.0-test"})
TAGS = {
    "models": [
        {
            "name": "example-model:8b",
            "model": "example-model:8b",
            "size": 5_000_000_000,
            "digest": "0" * 64,
            "details": {
                "family": "example",
                "parameter_size": "8B",
                "quantization_level": "Q4_K_M",
            },
        }
    ]
}


def _transport(
    version: httpx.Response | type[Exception], tags: httpx.Response | None = None
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(version, type):
            raise version("simulated", request=request)  # type: ignore[call-arg]
        if request.url.path == "/api/version":
            return version
        assert tags is not None
        return tags

    return httpx.MockTransport(handler)


async def test_running_with_models_is_ok() -> None:
    status = await probe_ollama(URL, 1.0, _transport(VERSION, httpx.Response(200, json=TAGS)))
    assert status.state is ComponentState.OK
    assert status.running
    assert status.installed
    assert status.version == "0.0.0-test"
    [model] = status.models
    assert (model.name, model.parameter_size, model.quantization) == (
        "example-model:8b",
        "8B",
        "Q4_K_M",
    )


async def test_running_without_models_is_degraded() -> None:
    status = await probe_ollama(
        URL, 1.0, _transport(VERSION, httpx.Response(200, json={"models": []}))
    )
    assert status.state is ComponentState.DEGRADED
    assert status.models == []


@pytest.mark.parametrize(
    "tags",
    [
        httpx.Response(500, text="boom"),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"x": 1}),
    ],
)
async def test_bad_answers_are_errors(tags: httpx.Response) -> None:
    status = await probe_ollama(URL, 1.0, _transport(VERSION, tags))
    assert status.state is ComponentState.ERROR
    assert status.error


async def test_read_timeout_means_running_but_unresponsive() -> None:
    status = await probe_ollama(URL, 1.0, _transport(httpx.ReadTimeout))
    assert status.state is ComponentState.ERROR
    assert "did not answer" in status.summary


@pytest.mark.parametrize("failure", [httpx.ConnectError, httpx.ConnectTimeout])
async def test_unreachable_endpoint_is_unavailable(failure: type[Exception]) -> None:
    status = await probe_ollama(URL, 1.0, _transport(failure))
    assert status.state is ComponentState.UNAVAILABLE
    assert not status.running
    assert status.models == []


async def test_real_closed_port_is_reported_absent() -> None:
    status = await probe_ollama("http://127.0.0.1:9", 0.5)
    assert status.state is ComponentState.UNAVAILABLE
    assert not status.running


@pytest.mark.local_services
async def test_real_local_ollama_when_running() -> None:
    status = await probe_ollama(DEFAULT_OLLAMA_URL, 3.0)
    if not status.running:
        pytest.skip(f"Ollama is not running at {DEFAULT_OLLAMA_URL}: {status.summary}")
    assert status.state in {ComponentState.OK, ComponentState.DEGRADED}
    assert status.version
