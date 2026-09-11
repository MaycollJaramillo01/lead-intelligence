"""Detects a local Ollama runtime and lists installed models. Never pulls or deletes models."""

import logging
import os
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from research_engine.contracts import ComponentState, OllamaModel, OllamaStatus

logger = logging.getLogger(__name__)


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
