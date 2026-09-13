"""Ollama detection. Mock transports simulate API answers; one test probes a real closed port,
and one (marked local_services) talks to a real local Ollama when it is running."""

import json
from dataclasses import replace

import httpx
import pytest

from research_engine.contracts import ComponentState
from research_engine.ollama import (
    ChatMessage,
    ChatRequest,
    OllamaProvider,
    ProviderError,
    probe_ollama,
)
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


# ---- the provider (phase 10) --------------------------------------------------------------------


def _recording(
    responses: dict[str, httpx.Response], seen: list[httpx.Request]
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return responses[request.url.path]

    return httpx.MockTransport(handler)


def _chat_request(**changes: object) -> ChatRequest:
    base = ChatRequest(
        model="example-model:8b",
        messages=(ChatMessage("system", "rules"), ChatMessage("user", "data", ("aW1n",))),
        schema={"type": "object"},
        options={"temperature": 0, "num_ctx": 4096},
        timeout_s=30,
    )
    return replace(base, **changes)  # type: ignore[arg-type]


async def test_chat_sends_a_schema_and_options_and_never_tools() -> None:
    seen: list[httpx.Request] = []
    answer = {"message": {"role": "assistant", "content": "{}"}, "done_reason": "stop",
              "prompt_eval_count": 12, "eval_count": 3, "total_duration": 9_000_000,
              "load_duration": 1_000_000, "eval_duration": 2_000_000}  # fmt: skip
    provider = OllamaProvider(
        URL, 1.0, _recording({"/api/chat": httpx.Response(200, json=answer)}, seen)
    )
    reply = await provider.chat(_chat_request())
    assert (reply.content, reply.prompt_tokens, reply.completion_tokens, reply.eval_ms) == (
        "{}",
        12,
        3,
        2,
    )
    body = json.loads(seen[0].content)
    assert body["format"] == {"type": "object"}
    assert body["stream"] is False
    assert body["options"] == {"temperature": 0, "num_ctx": 4096}
    assert "tools" not in body
    assert "think" not in body
    assert body["messages"][1]["images"] == ["aW1n"]
    assert "images" not in body["messages"][0]
    await provider.chat(_chat_request(think=False))
    assert json.loads(seen[1].content)["think"] is False


@pytest.mark.parametrize(
    ("response", "code", "retryable"),
    [
        (httpx.Response(500, json={"error": "CUDA error: out of memory"}), "out_of_memory", True),
        (
            httpx.Response(500, json={"error": "model requires more system memory (9 GiB)"}),
            "out_of_memory",
            True,
        ),
        (httpx.Response(404, json={"error": "model 'x' not found"}), "model_not_found", False),
        (httpx.Response(400, json={"error": "invalid format"}), "bad_request", False),
        (httpx.Response(500, text="boom"), "runtime_error", True),
        (httpx.Response(200, text="not json"), "bad_response", True),
    ],
)
async def test_runtime_errors_keep_their_meaning(
    response: httpx.Response, code: str, retryable: bool
) -> None:
    provider = OllamaProvider(URL, 1.0, _recording({"/api/chat": response}, []))
    with pytest.raises(ProviderError) as raised:
        await provider.chat(_chat_request())
    assert (raised.value.code, raised.value.retryable) == (code, retryable)


async def test_an_unreachable_runtime_is_unavailable_not_an_answer() -> None:
    provider = OllamaProvider("http://127.0.0.1:9", 0.5)
    with pytest.raises(ProviderError) as raised:
        await provider.chat(_chat_request(timeout_s=0.5))
    assert raised.value.code in ("unavailable", "timeout")
    assert raised.value.counts_against_runtime


async def test_details_loaded_models_pull_delete_and_unload() -> None:
    seen: list[httpx.Request] = []
    progress_lines = "\n".join(json.dumps(line) for line in (
        {"status": "pulling manifest"},
        {"status": "pulling abc", "digest": "abc", "total": 100, "completed": 40},
        {"status": "success"},
    ))  # fmt: skip
    transport = _recording({
        "/api/show": httpx.Response(200, json={"capabilities": ["completion", "vision"],
                                               "model_info": {"general.architecture": "x"}}),
        "/api/ps": httpx.Response(200, json={"models": [{"name": "example-model:8b",
                                                        "size": 10, "size_vram": 10}]}),
        "/api/pull": httpx.Response(200, text=progress_lines),
        "/api/delete": httpx.Response(200),
        "/api/generate": httpx.Response(200, json={"done_reason": "unload"}),
    }, seen)  # fmt: skip
    provider = OllamaProvider(URL, 1.0, transport)
    details = await provider.show("example-model:8b")
    assert details.capabilities == ("completion", "vision")
    (loaded,) = await provider.loaded()
    assert (loaded.size, loaded.size_vram) == (10, 10)
    updates: list[tuple[str, int | None, int | None]] = []
    await provider.pull("example-model:8b", lambda *update: updates.append(update))
    assert updates[1] == ("pulling abc", 40, 100)
    assert updates[-1][0] == "success"
    await provider.delete("example-model:8b")
    await provider.unload("example-model:8b")
    assert json.loads(seen[-1].content) == {"model": "example-model:8b", "keep_alive": 0}
    assert [request.method for request in seen] == ["POST", "GET", "POST", "DELETE", "POST"]
