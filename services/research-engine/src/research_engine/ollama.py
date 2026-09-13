"""The local Ollama runtime: detection, installed and loaded models, and the calls the local AI
layer makes (phase 10).

`Provider` is what the AI layer needs from a runtime; `OllamaProvider` implements it over Ollama's
loopback API. A llama.cpp server would implement the same protocol (`/health`, `/v1/models`,
`/v1/chat/completions` with a JSON-schema response format). Models are downloaded or deleted only
through `pull` and `delete`, which only an explicit, confirmed request from the operator reaches.
"""

import logging
import os
import re
import shutil
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Protocol

import httpx
import orjson
from pydantic import BaseModel, ConfigDict, ValidationError

from research_engine.contracts import ComponentState, OllamaModel, OllamaStatus

logger = logging.getLogger(__name__)

PROVIDER_NAME: Final = "ollama"
#: Out-of-memory as llama.cpp, CUDA and Ollama word it.
_OUT_OF_MEMORY: Final = re.compile(
    r"out of memory|requires more system memory|cudaMalloc|unable to allocate|failed to allocate"
    r"|insufficient memory|memory allocation",
    re.IGNORECASE,
)


class _External(BaseModel):
    # Ollama's API is external input: validate what we use, ignore the rest.
    model_config = ConfigDict(extra="ignore")


class _Version(_External):
    version: str


class _Details(_External):
    family: str | None = None
    parameter_size: str | None = None
    quantization_level: str | None = None


class _Tag(_External):
    name: str
    size: int | None = None
    digest: str | None = None
    details: _Details | None = None


class _Tags(_External):
    models: list[_Tag]


class _Show(_External):
    capabilities: list[str] = []
    model_info: dict[str, object] = {}
    details: _Details | None = None


class _Loaded(_External):
    name: str
    digest: str | None = None
    size: int | None = None
    size_vram: int | None = None
    context_length: int | None = None


class _Ps(_External):
    models: list[_Loaded]


class _Message(_External):
    content: str = ""


class _Chat(_External):
    message: _Message
    done_reason: str | None = None
    prompt_eval_count: int | None = None
    eval_count: int | None = None
    total_duration: int | None = None
    load_duration: int | None = None
    eval_duration: int | None = None


def find_ollama_binary() -> Path | None:
    found = shutil.which("ollama")
    if found:
        return Path(found)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidate = Path(local_app_data) / "Programs" / "Ollama" / "ollama.exe"
        if candidate.is_file():
            return candidate
    return None


async def probe_ollama(
    base_url: str, timeout_s: float, transport: httpx.AsyncBaseTransport | None = None
) -> OllamaStatus:
    started = time.perf_counter()
    installed = find_ollama_binary() is not None

    def status(
        state: ComponentState,
        summary: str,
        *,
        running: bool = False,
        version: str | None = None,
        models: list[OllamaModel] | None = None,
        error: str | None = None,
    ) -> OllamaStatus:
        return OllamaStatus(
            state=state,
            summary=summary,
            error=error,
            checked_at=datetime.now(UTC),
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
            installed=installed or running,
            running=running,
            base_url=base_url,
            version=version,
            models=models or [],
        )

    try:
        async with httpx.AsyncClient(
            base_url=base_url, timeout=timeout_s, transport=transport, trust_env=False
        ) as client:
            version_response = await client.get("/api/version")
            version_response.raise_for_status()
            tags_response = await client.get("/api/tags")
            tags_response.raise_for_status()
        version = _Version.model_validate_json(version_response.content).version
        tags = _Tags.model_validate_json(tags_response.content)
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        # Windows retries refused loopback connections for ~2s, so "not listening" often
        # surfaces as a connect timeout rather than an immediate refusal.
        summary = (
            "Ollama is installed but not running"
            if installed
            else "Ollama not detected (not installed or not running)"
        )
        return status(ComponentState.UNAVAILABLE, summary, error=str(exc) or "not reachable")
    except httpx.TimeoutException:
        return status(
            ComponentState.ERROR, f"Ollama did not answer within {timeout_s:g}s", error="timeout"
        )
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        return status(ComponentState.ERROR, f"Ollama answered HTTP {code}", error=f"HTTP {code}")
    except (httpx.HTTPError, ValidationError) as exc:
        logger.warning("unexpected Ollama response", exc_info=True)
        return status(
            ComponentState.ERROR,
            "Ollama answered with an unexpected response",
            error=str(exc)[:500],
        )

    models = [
        OllamaModel(
            name=tag.name,
            size_bytes=tag.size,
            digest=tag.digest,
            family=tag.details.family if tag.details else None,
            parameter_size=tag.details.parameter_size if tag.details else None,
            quantization=tag.details.quantization_level if tag.details else None,
        )
        for tag in tags.models
    ]
    if not models:
        return status(
            ComponentState.DEGRADED,
            f"Ollama {version} is running but no models are installed",
            running=True,
            version=version,
        )
    return status(
        ComponentState.OK,
        f"Ollama {version} running · {len(models)} model(s) installed",
        running=True,
        version=version,
        models=models,
    )


# ---- the provider protocol ----------------------------------------------------------------------


class ProviderError(Exception):
    """A runtime call that did not produce an answer. `code`: unavailable, timeout, out_of_memory,
    model_not_found, bad_request, runtime_error, bad_response."""

    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable

    @property
    def counts_against_runtime(self) -> bool:
        """Failures that say the runtime itself is unwell (the circuit breaker counts these)."""
        return self.code in ("unavailable", "timeout", "runtime_error")


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: str
    content: str
    #: Base64 images for multimodal models.
    images: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ChatRequest:
    model: str
    messages: tuple[ChatMessage, ...]
    #: JSON schema the answer is constrained to.
    schema: Mapping[str, object]
    options: Mapping[str, object]
    timeout_s: float
    #: Only sent to models that report the `thinking` capability: structured answers need no trace.
    think: bool | None = None
    keep_alive: str = "5m"


@dataclass(frozen=True, slots=True)
class ChatAnswer:
    content: str
    prompt_tokens: int | None
    completion_tokens: int | None
    total_ms: int | None
    load_ms: int | None
    eval_ms: int | None
    done_reason: str | None


@dataclass(frozen=True, slots=True)
class ModelDetails:
    capabilities: tuple[str, ...]
    model_info: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LoadedModel:
    name: str
    digest: str | None
    size: int | None
    size_vram: int | None
    context_length: int | None


class Provider(Protocol):
    name: str

    async def probe(self) -> OllamaStatus: ...
    async def show(self, model: str) -> ModelDetails: ...
    async def loaded(self) -> Sequence[LoadedModel]: ...
    async def chat(self, request: ChatRequest) -> ChatAnswer: ...
    async def pull(
        self, model: str, progress: Callable[[str, int | None, int | None], None]
    ) -> None: ...
    async def delete(self, model: str) -> None: ...
    async def unload(self, model: str) -> None: ...


def _ms(nanoseconds: int | None) -> int | None:
    return None if nanoseconds is None else nanoseconds // 1_000_000


def _error_of(response: httpx.Response) -> ProviderError:
    try:
        message = str(orjson.loads(response.content).get("error") or "")
    except (orjson.JSONDecodeError, AttributeError):
        message = response.text
    message = (message or f"HTTP {response.status_code}")[:500]
    if _OUT_OF_MEMORY.search(message):
        return ProviderError("out_of_memory", message, retryable=True)
    if response.status_code == 404:
        return ProviderError("model_not_found", message, retryable=False)
    if 400 <= response.status_code < 500:
        return ProviderError("bad_request", message, retryable=False)
    return ProviderError("runtime_error", message, retryable=True)


class OllamaProvider:
    """Ollama over its loopback API. Every call has a timeout; nothing follows proxies."""

    name = PROVIDER_NAME

    def __init__(
        self,
        base_url: str,
        timeout_s: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url
        self._timeout_s = timeout_s
        self._transport = transport

    def _client(self, timeout: httpx.Timeout | float) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url, timeout=timeout, transport=self._transport, trust_env=False
        )

    async def _send(
        self, method: str, path: str, payload: object, limit: httpx.Timeout | float
    ) -> httpx.Response:
        try:
            async with self._client(limit) as client:
                response = await client.request(method, path, json=payload)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise ProviderError("unavailable", "Ollama is not reachable", retryable=True) from exc
        except httpx.TimeoutException as exc:
            raise ProviderError("timeout", "Ollama did not answer in time", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError("unavailable", str(exc)[:300], retryable=True) from exc
        if response.status_code >= 400:
            raise _error_of(response)
        return response

    async def probe(self) -> OllamaStatus:
        return await probe_ollama(self.base_url, self._timeout_s, self._transport)

    async def show(self, model: str) -> ModelDetails:
        response = await self._send("POST", "/api/show", {"model": model}, self._timeout_s * 5)
        try:
            shown = _Show.model_validate_json(response.content)
        except ValidationError as exc:
            raise ProviderError("bad_response", str(exc)[:300], retryable=False) from exc
        return ModelDetails(tuple(shown.capabilities), shown.model_info)

    async def loaded(self) -> Sequence[LoadedModel]:
        response = await self._send("GET", "/api/ps", None, self._timeout_s)
        try:
            listed = _Ps.model_validate_json(response.content)
        except ValidationError as exc:
            raise ProviderError("bad_response", str(exc)[:300], retryable=False) from exc
        return [
            LoadedModel(m.name, m.digest, m.size, m.size_vram, m.context_length)
            for m in listed.models
        ]

    async def chat(self, request: ChatRequest) -> ChatAnswer:
        payload: dict[str, object] = {
            "model": request.model,
            "messages": [
                {
                    "role": m.role,
                    "content": m.content,
                    **({"images": list(m.images)} if m.images else {}),
                }
                for m in request.messages
            ],
            "format": dict(request.schema),
            "options": dict(request.options),
            "stream": False,
            "keep_alive": request.keep_alive,
        }
        if request.think is not None:
            payload["think"] = request.think
        # connecting must be quick; loading a model and answering may take the whole budget
        timeout = httpx.Timeout(request.timeout_s, connect=min(5.0, request.timeout_s))
        response = await self._send("POST", "/api/chat", payload, timeout)
        try:
            chat = _Chat.model_validate_json(response.content)
        except ValidationError as exc:
            raise ProviderError("bad_response", str(exc)[:300], retryable=True) from exc
        return ChatAnswer(
            content=chat.message.content,
            prompt_tokens=chat.prompt_eval_count,
            completion_tokens=chat.eval_count,
            total_ms=_ms(chat.total_duration),
            load_ms=_ms(chat.load_duration),
            eval_ms=_ms(chat.eval_duration),
            done_reason=chat.done_reason,
        )

    async def pull(
        self, model: str, progress: Callable[[str, int | None, int | None], None]
    ) -> None:
        timeout = httpx.Timeout(None, connect=5.0, read=300.0)
        try:
            async with (
                self._client(timeout) as client,
                client.stream("POST", "/api/pull", json={"model": model, "stream": True}) as reply,
            ):
                if reply.status_code >= 400:
                    await reply.aread()
                    raise _error_of(reply)
                async for line in reply.aiter_lines():
                    if not line.strip():
                        continue
                    event = orjson.loads(line)
                    if not isinstance(event, dict):
                        continue
                    if event.get("error"):
                        raise ProviderError(
                            "runtime_error", str(event["error"])[:500], retryable=True
                        )
                    total, completed = event.get("total"), event.get("completed")
                    progress(
                        str(event.get("status") or "")[:300],
                        completed if isinstance(completed, int) else None,
                        total if isinstance(total, int) else None,
                    )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise ProviderError("unavailable", "Ollama is not reachable", retryable=True) from exc
        except httpx.TimeoutException as exc:
            raise ProviderError("timeout", "the download stalled", retryable=True) from exc
        except orjson.JSONDecodeError as exc:
            raise ProviderError(
                "bad_response", "unreadable download progress", retryable=True
            ) from exc

    async def delete(self, model: str) -> None:
        await self._send("DELETE", "/api/delete", {"model": model}, self._timeout_s * 5)

    async def unload(self, model: str) -> None:
        """Frees the VRAM a loaded model holds (a request with `keep_alive: 0`)."""
        await self._send(
            "POST", "/api/generate", {"model": model, "keep_alive": 0}, self._timeout_s * 5
        )
