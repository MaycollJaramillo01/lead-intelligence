from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from research_engine import export_contracts
from research_engine.contracts import PROTOCOL_VERSION, Envelope


def _frame(**overrides: Any) -> dict[str, Any]:
    frame: dict[str, Any] = {
        "request_id": str(uuid4()),
        "protocol_version": PROTOCOL_VERSION,
        "type": "health",
        "timestamp": "2026-01-01T00:00:00Z",
        "payload": {},
        "error": None,
    }
    frame.update(overrides)
    return frame


def test_valid_envelope_round_trips() -> None:
    envelope = Envelope.model_validate(_frame())
    assert envelope.timestamp == datetime(2026, 1, 1, tzinfo=UTC)
    assert Envelope.model_validate_json(envelope.model_dump_json()) == envelope


@pytest.mark.parametrize(
    "overrides",
    [
        {"extra": 1},
        {"timestamp": "2026-01-01T00:00:00"},  # naive timestamps are ambiguous
        {"type": "Health"},
        {"type": "health."},
        {"type": "a" * 65},
        {"request_id": "not-a-uuid"},
        {"protocol_version": 0},
        {"error": {"code": "timeout", "message": "x", "retryable": True}},  # with a payload
        {"error": {"code": "made_up", "message": "x", "retryable": True}, "payload": None},
    ],
)
def test_invalid_envelopes_are_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Envelope.model_validate(_frame(**overrides))


def test_exported_schema_and_fixtures_are_current() -> None:
    assert export_contracts.main(["--check"]) == 0, "run `pnpm contracts`"


def test_schema_has_no_per_field_titles() -> None:
    schema = export_contracts.build_schema()
    envelope = schema["$defs"]["Envelope"]
    assert envelope["title"] == "Envelope"
    assert all("title" not in prop for prop in envelope["properties"].values())
