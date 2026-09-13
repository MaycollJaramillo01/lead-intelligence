"""The external LLM Fit tool as an outside opinion on which models suit this machine.

Optional: used only when `llmfit` is on the PATH and the operator asks. Only its read-only
`recommend` subcommand runs, with `--no-dashboard` so it starts no server; nothing it says is
applied, installed or changed.
"""

import shutil
import subprocess
import sys
from typing import Any, Final

import orjson

from research_engine.domain import LlmFitModel, LlmFitReport, utc_now

TIMEOUT_S: Final = 60.0
LIMIT: Final = 10
#: The context the estimates assume: what the profiles default to on an 8 GB GPU.
CONTEXT: Final = 8192


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    extra: dict[str, Any] = {}
    if sys.platform == "win32":
        extra["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.run(  # noqa: S603 - absolute path from shutil.which, fixed arguments
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=TIMEOUT_S,
        check=False,
        **extra,
    )


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) and value >= 0 else None


def _text(value: object, limit: int) -> str | None:
    return value[:limit] if isinstance(value, str) and value else None


def parse(document: object) -> list[LlmFitModel]:
    models = document.get("models") if isinstance(document, dict) else None
    found: list[LlmFitModel] = []
    for item in models if isinstance(models, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        notes = item.get("notes")
        installed = item.get("installed")
        found.append(
            LlmFitModel(
                name=item["name"][:300],
                ollama_name=_text(item.get("ollama_name"), 300),
                fit_level=_text(item.get("fit_level"), 64),
                run_mode=_text(item.get("run_mode"), 64),
                runtime=_text(item.get("runtime"), 64),
                quantization=_text(item.get("best_quant"), 64),
                memory_required_gb=_number(item.get("memory_required_gb")),
                estimated_tps=_number(item.get("estimated_tps")),
                installed=installed if isinstance(installed, bool) else None,
                notes=[note[:300] for note in notes if isinstance(note, str)][:5]
                if isinstance(notes, list)
                else [],
            )
        )
    return found


def report() -> LlmFitReport:
    executable = shutil.which("llmfit")

    def failed(error: str, version: str | None = None) -> LlmFitReport:
        return LlmFitReport(
            available=executable is not None, version=version, models=[], error=error[:500],
            checked_at=utc_now(),
        )  # fmt: skip

    if executable is None:
        return failed("llmfit is not installed (it is optional: https://www.llmfit.org)")
    try:
        version = _run([executable, "--version"]).stdout.strip()[:100] or None
        completed = _run([
            executable, "--max-context", str(CONTEXT), "recommend", "--json", "--limit",
            str(LIMIT), "--runtime", "llamacpp", "--no-dashboard",
        ])  # fmt: skip
    except subprocess.TimeoutExpired:
        return failed(f"llmfit did not answer within {TIMEOUT_S:g}s")
    except OSError as exc:
        return failed(f"llmfit could not be executed: {exc}")
    if completed.returncode != 0:
        return failed((completed.stderr or completed.stdout).strip() or "llmfit failed", version)
    try:
        models = parse(orjson.loads(completed.stdout))
    except orjson.JSONDecodeError:
        return failed("llmfit answered something that is not JSON", version)
    return LlmFitReport(
        available=True, version=version, models=models, error=None, checked_at=utc_now()
    )
