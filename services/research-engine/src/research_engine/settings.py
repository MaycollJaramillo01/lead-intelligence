"""Worker settings, read once from the environment prepared by the desktop core."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Self
from urllib.parse import urlsplit

LOG_LEVELS = frozenset({"debug", "info", "warning", "error", "critical"})
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
#: The local AI runtime is only ever reached on this machine (phase 10).
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path | None
    log_level: str
    ollama_url: str
    ollama_timeout_s: float
    # development builds only: enables the synthetic test-data seed (LEAD_DEV_TOOLS=1)
    dev_tools: bool = False
    # LEAD_DISCOVERY_OFFLINE=1: sources that need the network refuse instead of reaching out, and
    # contact validation looks up no mail domain (phase 09)
    discovery_offline: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> Self:
        """Raises ValueError with a readable message on invalid values."""
        level = env.get("LEAD_LOG_LEVEL", "info").strip().lower()
        if level not in LOG_LEVELS:
            raise ValueError(f"LEAD_LOG_LEVEL must be one of {sorted(LOG_LEVELS)}, got {level!r}")

        ollama_url = env.get("LEAD_OLLAMA_URL", DEFAULT_OLLAMA_URL).strip().rstrip("/")
        parts = urlsplit(ollama_url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError(f"LEAD_OLLAMA_URL must be an http(s) URL, got {ollama_url!r}")
        if parts.hostname not in LOOPBACK_HOSTS:
            raise ValueError(
                f"LEAD_OLLAMA_URL must point at this machine (loopback), got {ollama_url!r}"
            )

        raw_timeout = env.get("LEAD_OLLAMA_TIMEOUT_S", "2")
        try:
            timeout = float(raw_timeout)
        except ValueError:
            timeout = -1.0
        if not 0 < timeout <= 60:
            raise ValueError(f"LEAD_OLLAMA_TIMEOUT_S must be in (0, 60], got {raw_timeout!r}")

        dev_tools = env.get("LEAD_DEV_TOOLS", "0").strip()
        if dev_tools not in {"0", "1"}:
            raise ValueError(f"LEAD_DEV_TOOLS must be 0 or 1, got {dev_tools!r}")

        offline = env.get("LEAD_DISCOVERY_OFFLINE", "0").strip()
        if offline not in {"0", "1"}:
            raise ValueError(f"LEAD_DISCOVERY_OFFLINE must be 0 or 1, got {offline!r}")

        data_dir = env.get("LEAD_DATA_DIR", "").strip()
        return cls(
            data_dir=Path(data_dir) if data_dir else None,
            log_level=level,
            ollama_url=ollama_url,
            ollama_timeout_s=timeout,
            dev_tools=dev_tools == "1",
            discovery_offline=offline == "1",
        )
