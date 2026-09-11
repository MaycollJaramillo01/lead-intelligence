from pathlib import Path

import pytest

from research_engine.settings import DEFAULT_OLLAMA_URL, Settings


def test_defaults_are_safe_and_explicit() -> None:
    settings = Settings.from_env({})
    assert settings.data_dir is None  # never guessed
    assert settings.log_level == "info"
    assert settings.ollama_url == DEFAULT_OLLAMA_URL
    assert settings.ollama_timeout_s == 2.0


def test_values_are_read_and_normalised() -> None:
    settings = Settings.from_env(
        {
            "LEAD_DATA_DIR": " C:/data ",
            "LEAD_LOG_LEVEL": "DEBUG",
            "LEAD_OLLAMA_URL": "http://localhost:11434/",
            "LEAD_OLLAMA_TIMEOUT_S": "0.5",
        }
    )
    assert settings.data_dir == Path("C:/data")
    assert settings.log_level == "debug"
    assert settings.ollama_url == "http://localhost:11434"
    assert settings.ollama_timeout_s == 0.5


@pytest.mark.parametrize(
    "env",
    [
        {"LEAD_LOG_LEVEL": "loud"},
        {"LEAD_OLLAMA_URL": "ftp://localhost"},
        {"LEAD_OLLAMA_URL": "http://"},
        {"LEAD_OLLAMA_TIMEOUT_S": "0"},
        {"LEAD_OLLAMA_TIMEOUT_S": "soon"},
        {"LEAD_OLLAMA_TIMEOUT_S": "600"},
    ],
)
def test_invalid_values_fail_loudly(env: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="LEAD_"):
        Settings.from_env(env)
