"""Drives the real worker process through its stdio protocol, exactly as the desktop core does."""

import json
import os
import queue
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from research_engine.contracts import DATA_CONTRACT_VERSION, MAX_LINE_BYTES, PROTOCOL_VERSION
from research_engine.domain import ResearchActionRequest

Frame = dict[str, Any]


class WorkerProcess:
    def __init__(self, data_dir: Path, **extra_env: str) -> None:
        env = {
            **os.environ,
            "LEAD_DATA_DIR": str(data_dir),
            "LEAD_LOG_LEVEL": "debug",
            "LEAD_OLLAMA_URL": "http://127.0.0.1:9",  # closed port: fast, deterministic
            "LEAD_OLLAMA_TIMEOUT_S": "0.5",
            **extra_env,
        }
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "research_engine"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        self.frames: queue.Queue[Frame | None] = queue.Queue()
        self.stderr: list[str] = []
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        threading.Thread(target=self._pump_stderr, daemon=True).start()

    def _pump_stdout(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            try:
                self.frames.put(json.loads(line))
            except ValueError:
                self.frames.put({"__not_json__": line.decode(errors="replace")})
        self.frames.put(None)

    def _pump_stderr(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            self.stderr.append(line.decode(errors="replace"))

    def send(self, data: bytes) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(data + b"\n")
        self.proc.stdin.flush()

    def next_frame(self, timeout: float = 20) -> Frame:
        frame = self.frames.get(timeout=timeout)
        assert frame is not None, "worker closed stdout. stderr:\n" + "".join(self.stderr)
        assert "__not_json__" not in frame, f"non-protocol output on stdout: {frame}"
        return frame

    def wait_for(self, predicate: Callable[[Frame], bool], timeout: float = 20) -> Frame:
        deadline = time.monotonic() + timeout
        while True:
            frame = self.next_frame(max(0.1, deadline - time.monotonic()))
            if predicate(frame):
                return frame

    def request(self, message_type: str, payload: Any = None, **overrides: Any) -> Frame:
        request_id = str(uuid4())
        frame = {
            "request_id": request_id,
            "protocol_version": PROTOCOL_VERSION,
            "type": message_type,
            "timestamp": "2026-01-01T00:00:00Z",
            "payload": {} if payload is None else payload,
            "error": None,
            **overrides,
        }
        self.send(json.dumps(frame).encode())
        return self.wait_for(lambda f: f["request_id"] == request_id)


def _stop(process: WorkerProcess) -> None:
    if process.proc.poll() is None:
        process.proc.kill()
    process.proc.wait(timeout=10)


@pytest.fixture
def worker(tmp_path: Path) -> Iterator[WorkerProcess]:
    process = WorkerProcess(tmp_path)
    yield process
    _stop(process)


@pytest.fixture
def dev_worker(tmp_path: Path) -> Iterator[WorkerProcess]:
    process = WorkerProcess(tmp_path, LEAD_DEV_TOOLS="1")
    yield process
    _stop(process)


def _ready(worker: WorkerProcess) -> Frame:
    ready = worker.next_frame()
    assert ready["type"] == "worker.ready"
    assert ready["payload"]["protocol_version"] == PROTOCOL_VERSION
    assert ready["payload"]["data_contract_version"] == DATA_CONTRACT_VERSION
    assert ready["payload"]["pid"] > 0
    return ready


def test_health_and_diagnostics_answer_with_real_data(
    worker: WorkerProcess, tmp_path: Path
) -> None:
    _ready(worker)
    health = worker.request("health")
    assert health["type"] == "health.result"
    assert health["error"] is None
    assert health["payload"]["status"] == "ok"

    diagnostics = worker.request("diagnostics")["payload"]
    assert diagnostics["sqlite"]["state"] == "ok"
    assert diagnostics["duckdb"]["state"] == "ok"
    assert diagnostics["ollama"]["state"] == "unavailable"
    assert diagnostics["gpu"]["state"] in {"ok", "degraded", "unavailable"}
    assert (tmp_path / "lead.sqlite3").is_file()
    assert (tmp_path / "analytics.duckdb").is_file()


@pytest.mark.parametrize(
    ("message_type", "payload", "overrides", "code"),
    [
        ("no_such_request", None, {}, "unknown_type"),
        ("health", None, {"protocol_version": 99}, "unsupported_protocol"),
        ("health", {"unexpected": True}, {}, "invalid_payload"),
    ],
)
def test_bad_requests_get_correlated_errors(
    worker: WorkerProcess, message_type: str, payload: Any, overrides: dict[str, Any], code: str
) -> None:
    _ready(worker)
    answer = worker.request(message_type, payload, **overrides)
    assert answer["type"] == f"{message_type}.result"
    assert answer["payload"] is None
    assert answer["error"]["code"] == code


def test_garbage_frames_produce_protocol_errors_and_keep_the_stream_usable(
    worker: WorkerProcess,
) -> None:
    _ready(worker)
    worker.send(b"not json")
    assert worker.next_frame()["type"] == "worker.protocol_error"

    request_id = str(uuid4())
    worker.send(json.dumps({"request_id": request_id, "type": "health"}).encode())
    salvaged = worker.next_frame()
    assert salvaged["type"] == "worker.protocol_error"
    assert salvaged["request_id"] == request_id  # correlated, so the caller fails fast

    worker.send(b"x" * (MAX_LINE_BYTES + 10))
    oversized = worker.next_frame()
    assert oversized["type"] == "worker.protocol_error"
    assert "exceeds" in oversized["error"]["message"]

    assert worker.request("health")["payload"]["status"] == "ok"


def test_leading_utf8_bom_is_tolerated(worker: WorkerProcess) -> None:
    request_id = str(uuid4())
    frame = {
        "request_id": request_id,
        "protocol_version": PROTOCOL_VERSION,
        "type": "health",
        "timestamp": "2026-01-01T00:00:00Z",
        "payload": {},
        "error": None,
    }
    worker.send(b"\xef\xbb\xbf" + json.dumps(frame).encode())
    _ready(worker)
    assert worker.wait_for(lambda f: f["request_id"] == request_id)["type"] == "health.result"


def test_shutdown_request_exits_cleanly(worker: WorkerProcess) -> None:
    _ready(worker)
    assert worker.request("shutdown")["payload"] == {"accepted": True}
    assert worker.proc.wait(timeout=10) == 0


def test_closing_stdin_exits_cleanly(worker: WorkerProcess) -> None:
    _ready(worker)
    assert worker.proc.stdin is not None
    worker.proc.stdin.close()
    assert worker.proc.wait(timeout=10) == 0


def test_invalid_settings_fail_fast_with_a_readable_error(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "research_engine"],
        env={**os.environ, "LEAD_DATA_DIR": str(tmp_path), "LEAD_LOG_LEVEL": "loud"},
        input=b"",
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 2
    assert completed.stdout == b""
    assert b"LEAD_LOG_LEVEL" in completed.stderr


_LIST = {
    "project_id": None,
    "search": None,
    "status": None,
    "latino_status": None,
    "latino_min_confidence": None,
    "latino_source_kind": None,
    "latino_review": None,
    "sort": "name",
    "limit": 100,
}


def test_data_requests_are_served_from_the_store(worker: WorkerProcess) -> None:
    _ready(worker)
    page = worker.request("businesses.list", {**_LIST, "cursor": None})
    assert page["error"] is None
    assert page["payload"] == {"items": [], "next_cursor": None, "total": 0}
    datasets = worker.request("datasets.list", {"project_id": None, "limit": 50, "cursor": None})
    assert datasets["payload"] == {"items": [], "next_cursor": None, "total": 0}

    missing = worker.request("businesses.get", {"business_id": str(uuid4())})
    assert missing["error"]["code"] == "not_found"
    tampered = worker.request("businesses.list", {**_LIST, "cursor": "bm90LWEtY3Vyc29y"})
    assert tampered["error"]["code"] == "invalid_payload"
    oversized = worker.request("businesses.list", {**_LIST, "limit": 100_000, "cursor": None})
    assert oversized["error"]["code"] == "invalid_payload"
    seed = worker.request("dev.seed_synthetic")
    assert seed["error"]["code"] == "not_allowed"  # only development builds enable the seed


def test_development_seed_creates_openable_synthetic_data(dev_worker: WorkerProcess) -> None:
    _ready(dev_worker)
    created = dev_worker.request("dev.seed_synthetic")["payload"]
    assert created["created"] is True
    # 24 businesses, 6 records to resolve (two are the same plumber), 8 records whose Latino
    # evidence differs (two describe the same grocery) and 2 businesses whose contacts are checked
    assert created["business_count"] == 38
    again = dev_worker.request("dev.seed_synthetic")["payload"]
    assert again == {**created, "created": False}  # seeded once, then reused

    page = dev_worker.request("businesses.list", {**_LIST, "limit": 10, "cursor": None})["payload"]
    assert page["total"] == 38
    assert page["next_cursor"] is not None
    assert all(item["is_synthetic"] for item in page["items"])
    assert page["items"][0]["id"] == created["business_id"]

    detail = dev_worker.request("businesses.get", {"business_id": created["business_id"]})
    assert detail["payload"]["is_synthetic"] is True
    provenance = detail["payload"]["names"][0]["provenance"]
    assert provenance["connector_kind"] == "synthetic"

    datasets = dev_worker.request(
        "datasets.list", {"project_id": None, "limit": 50, "cursor": None}
    )
    (dataset,) = datasets["payload"]["items"]
    assert dataset["dataset"]["id"] == created["dataset_id"]
    assert dataset["latest_snapshot"]["row_count"] == 38


_EXPLORE: dict[str, Any] = {
    "score_run_id": None,
    "filters": {"kind": "group", "join": "and", "negate": False, "children": []},
    "search": None,
    "sort": [],
    "offset": 0,
    "limit": 50,
    "with_total": True,
}


def test_research_work_survives_a_worker_crash(tmp_path: Path) -> None:
    first = WorkerProcess(tmp_path, LEAD_DEV_TOOLS="1")
    try:
        _ready(first)
        assert first.request("dev.seed_synthetic")["error"] is None
        page = first.request("leads.explore", _EXPLORE)["payload"]
        assert page["total"] == 38
        lead = page["rows"][0]["business_id"]
        action = dict.fromkeys(ResearchActionRequest.model_fields) | {
            "business_id": lead,
            "action": "set_status",
            "status": "disqualified",
            "reason": "Out of the service area (test)",
            "scopes": [],
        }
        workspace = first.request("research.act", action)["payload"]
        assert workspace["research"]["status"] == "disqualified"
        catalog = first.request("leads.catalog", {"scope": "lead_explorer"})["payload"]
        view = {
            "view_id": None,
            "scope": "lead_explorer",
            "name": "Disqualified (test)",
            "description": None,
            "definition": catalog["default_view"],
        }
        assert first.request("views.save", view)["error"] is None
    finally:
        _stop(first)  # killed, as a crash would
    second = WorkerProcess(tmp_path, LEAD_DEV_TOOLS="1")
    try:
        _ready(second)
        again = second.request("research.get", {"business_id": lead})["payload"]
        assert again["research"]["status"] == "disqualified"
        views = second.request("views.list", {"scope": "lead_explorer", "include_archived": False})
        assert "Disqualified (test)" in [item["view"]["name"] for item in views["payload"]["items"]]
        status = {"kind": "condition", "field": "research_status", "operator": "eq",
                  "value": "disqualified"}  # fmt: skip
        filters = {**_EXPLORE["filters"], "children": [status]}
        filtered = second.request("leads.explore", {**_EXPLORE, "filters": filters})["payload"]
        assert [row["business_id"] for row in filtered["rows"]] == [lead]
    finally:
        _stop(second)


def _until(
    worker: WorkerProcess,
    message_type: str,
    payload: dict[str, Any],
    finished: Callable[[Any], bool],
    timeout: float = 300.0,
) -> Any:
    """Asks again every half second until `finished` holds for the answer."""
    deadline = time.monotonic() + timeout
    while True:
        answer = worker.request(message_type, payload)
        assert answer["error"] is None, answer["error"]
        if finished(answer["payload"]):
            return answer["payload"]
        assert time.monotonic() < deadline, answer["payload"]
        time.sleep(0.5)


def test_a_pipeline_drives_the_real_engines_and_an_export_writes_its_file(tmp_path: Path) -> None:
    data, chosen = tmp_path / "data", tmp_path / "chosen-folder"
    data.mkdir()
    chosen.mkdir()
    worker = WorkerProcess(data, LEAD_DEV_TOOLS="1")
    try:
        _ready(worker)
        seeded = worker.request("dev.seed_synthetic")["payload"]
        start = {
            "project_id": seeded["project_id"],
            "campaign_id": None,
            "check_mail_domains": False,
            "priority": 0,
            "idempotency_key": "fixture-real-pipeline",
        }
        started = worker.request("pipelines.start", start)
        assert started["error"] is None, started["error"]
        job_id = started["payload"]["job_id"]

        def mine(page: Any) -> Any:
            return next(item for item in page["items"] if item["job_id"] == job_id)

        page = _until(
            worker,
            "pipelines.list",
            {"project_id": seeded["project_id"], "limit": 5, "cursor": None},
            lambda page: mine(page)["status"] in ("succeeded", "failed", "cancelled"),
        )
        item = mine(page)
        failures = [
            (stage["stage"], attempt["error_code"], attempt["error_message"])
            for stage in item["stages"]
            for attempt in stage["attempts"]
            if attempt["error_code"]
        ]
        assert item["status"] == "succeeded", failures
        stages = {stage["stage"]: stage for stage in item["stages"]}
        assert stages["discovery"]["skipped"] == "no_campaign"
        for name in (
            "normalization",
            "latino_evidence",
            "website_intelligence",
            "contact_intelligence",
            "local_ai",
            "scoring",
        ):
            assert stages[name]["attempts"][0]["jobs"], name  # a real engine job did the work
        assert item["snapshot_id"] is not None
        assert item["eligible_leads"] is not None
        assert page["resources"]["cpu_count"] >= 1
        query = dict.fromkeys(("task_id", "business_id", "request_id", "cursor"))
        events = worker.request(
            "jobs.events", query | {"job_id": job_id, "levels": [], "limit": 100}
        )
        assert events["payload"]["total"] >= 10

        draft = {
            "source_kind": "snapshot",
            "query": None,
            "view_id": None,
            "snapshot_id": item["snapshot_id"],
            "format": "csv",
            "columns": [
                {"key": "display_name", "header": "Business"},
                {"key": "tier", "header": "Tier"},
            ],
            "bom": True,
            "include_suppressed": False,
            "suppressed_reason": None,
            "preset_key": None,
            "preset_version": None,
        }
        preview = worker.request("exports.preview", draft)
        assert preview["error"] is None, preview["error"]
        export = worker.request(
            "exports.start", {**draft, "destination": str(chosen / "leads.csv")}
        )
        assert export["error"] is None, export["error"]
        export_id = export["payload"]["export"]["id"]

        def written(page: Any) -> list[Any]:
            return [entry for entry in page["items"] if entry["export"]["id"] == export_id]

        exports = _until(
            worker,
            "exports.list",
            {"project_id": None, "limit": 10, "cursor": None},
            lambda page: written(page)[0]["export"]["status"] in ("succeeded", "failed"),
        )
        (entry,) = written(exports)
        assert entry["export"]["status"] == "succeeded", entry["export"]["error_message"]
        lines = (chosen / "leads.csv").read_text(encoding="utf-8-sig").splitlines()
        assert len(lines) - 1 == preview["payload"]["exported"] == entry["export"]["row_count"]
        assert (chosen / "leads.csv.manifest.json").exists()
    finally:
        _stop(worker)
