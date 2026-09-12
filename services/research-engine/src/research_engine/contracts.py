"""Cross-process contracts. This module is the single source of truth.

Every message crossing a process boundary (desktop core <-> worker, desktop core <-> UI)
is declared here. `export_contracts` turns these models into JSON Schema + TypeScript types
(packages/shared-types) and golden fixtures that the Rust core round-trips in its tests.

The data model (records, enumerations, queries and pages) lives in `domain.py` and is exported the
same way.

Compatibility: bump PROTOCOL_VERSION for any breaking change to the envelope or a payload.
Adding a new message type or a new nullable field is additive and keeps the version.
DATA_CONTRACT_VERSION versions the data model independently (see domain.py).
"""

from enum import StrEnum
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

PROTOCOL_VERSION = 1
DATA_CONTRACT_VERSION = 1
MAX_LINE_BYTES = 8 * 1024 * 1024
RESULT_SUFFIX = ".result"


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MessageType(StrEnum):
    # requests (desktop core -> worker); responses use the "<type>.result" form
    HEALTH = "health"
    DIAGNOSTICS = "diagnostics"
    SHUTDOWN = "shutdown"
    BUSINESSES_LIST = "businesses.list"
    BUSINESSES_GET = "businesses.get"
    DATASETS_LIST = "datasets.list"
    DEV_SEED_SYNTHETIC = "dev.seed_synthetic"
    # events (worker -> desktop core)
    WORKER_READY = "worker.ready"
    PROTOCOL_ERROR = "worker.protocol_error"


class ErrorCode(StrEnum):
    INVALID_MESSAGE = "invalid_message"
    UNSUPPORTED_PROTOCOL = "unsupported_protocol"
    UNKNOWN_TYPE = "unknown_type"
    INVALID_PAYLOAD = "invalid_payload"
    NOT_FOUND = "not_found"
    NOT_ALLOWED = "not_allowed"
    STORE_UNAVAILABLE = "store_unavailable"
    TIMEOUT = "timeout"
    SHUTTING_DOWN = "shutting_down"
    INTERNAL = "internal"


class ErrorInfo(Contract):
    code: ErrorCode
    message: str = Field(max_length=2000)
    retryable: bool


class Envelope(Contract):
    """One JSON Lines frame. `payload` and `error` are mutually exclusive."""

    request_id: UUID
    protocol_version: int = Field(ge=1)
    type: str = Field(max_length=64, pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")
    timestamp: AwareDatetime
    payload: dict[str, Any] | None
    error: ErrorInfo | None

    @model_validator(mode="after")
    def _payload_xor_error(self) -> Self:
        if self.payload is not None and self.error is not None:
            raise ValueError("payload and error are mutually exclusive")
        return self


class EmptyPayload(Contract):
    """Payload of requests that take no parameters (health, diagnostics, shutdown)."""


class ComponentState(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class HealthResult(Contract):
    status: Literal["ok"]
    worker_version: str
    protocol_version: int
    data_contract_version: int
    python_version: str
    pid: int
    started_at: AwareDatetime
    uptime_s: float


class Probe(Contract):
    state: ComponentState
    summary: str
    error: str | None
    checked_at: AwareDatetime
    duration_ms: float


class DatabaseStatus(Probe):
    engine: Literal["sqlite", "duckdb"]
    path: str | None
    engine_version: str | None
    schema_version: int | None
    initialized_at: str | None


class OllamaModel(Contract):
    name: str
    size_bytes: int | None
    digest: str | None
    family: str | None
    parameter_size: str | None
    quantization: str | None


class OllamaStatus(Probe):
    installed: bool
    running: bool
    base_url: str
    version: str | None
    models: list[OllamaModel]


class GpuDevice(Contract):
    index: int
    name: str
    memory_total_mib: int | None
    memory_free_mib: int | None
    driver_version: str | None


class GpuStatus(Probe):
    devices: list[GpuDevice]
    cuda_available: bool
    cuda_driver_version: str | None


class DiagnosticsResult(Contract):
    health: HealthResult
    sqlite: DatabaseStatus
    duckdb: DatabaseStatus
    ollama: OllamaStatus
    gpu: GpuStatus


class ShutdownResult(Contract):
    accepted: bool


# ---- desktop core <-> UI (Tauri IPC). Produced by Rust, consumed by React. ----


class WorkerState(StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    RESTARTING = "restarting"
    FAILED = "failed"


class WorkerStatus(Contract):
    state: WorkerState
    pid: int | None
    started_at: AwareDatetime | None
    restart_count: int
    last_error: str | None
    stderr_tail: list[str]
    health: HealthResult | None


class AppEnvironment(StrEnum):
    DEVELOPMENT = "development"
    PRODUCTION = "production"


class CoreStatus(Contract):
    state: ComponentState
    summary: str
    app_version: str
    environment: AppEnvironment
    os: str
    arch: str
    data_dir: str
    log_dir: str
    config_sources: list[str]
    config_warnings: list[str]
    started_at: AwareDatetime
    uptime_s: float


class CommandErrorCode(StrEnum):
    WORKER_UNAVAILABLE = "worker_unavailable"
    WORKER_TIMEOUT = "worker_timeout"
    WORKER_ERROR = "worker_error"
    PROTOCOL_VIOLATION = "protocol_violation"
    INVALID_INPUT = "invalid_input"
    NOT_FOUND = "not_found"
    NOT_ALLOWED = "not_allowed"
    INTERNAL = "internal"


class CommandError(Contract):
    """Error shape every Tauri command rejects with."""

    code: CommandErrorCode
    message: str


class DiagnosticsReport(Contract):
    generated_at: AwareDatetime
    core: CoreStatus
    worker: WorkerStatus
    components: DiagnosticsResult | None
    components_error: CommandError | None


class UiErrorReport(Contract):
    """Technical UI error forwarded to the desktop log (the UI never writes logs itself)."""

    source: str = Field(max_length=64)
    message: str = Field(max_length=2000)
    stack: str | None = Field(max_length=8000)


EXPORTED_MODELS: tuple[type[BaseModel], ...] = (
    Envelope,
    EmptyPayload,
    HealthResult,
    DiagnosticsResult,
    ShutdownResult,
    WorkerStatus,
    CoreStatus,
    CommandError,
    DiagnosticsReport,
    UiErrorReport,
)
