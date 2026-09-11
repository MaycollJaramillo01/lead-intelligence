//! Wire contracts, mirrored from `services/research-engine/src/research_engine/contracts.py`
//! (the source of truth). `tests/contracts.rs` round-trips the generated fixtures in
//! `packages/shared-types/fixtures` through these types, so any drift fails the test suite.
//! Unknown fields are rejected everywhere: nothing from the worker or the UI is trusted blindly.

use chrono::{DateTime, Utc};
use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use uuid::Uuid;

use crate::config::Environment;

pub const PROTOCOL_VERSION: u32 = 1;
pub const MAX_FRAME_BYTES: usize = 8 * 1024 * 1024;
pub const RESULT_SUFFIX: &str = ".result";
pub const EVENT_READY: &str = "worker.ready";
pub const EVENT_PROTOCOL_ERROR: &str = "worker.protocol_error";

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Envelope {
    pub request_id: Uuid,
    pub protocol_version: u32,
    #[serde(rename = "type")]
    pub kind: String,
    pub timestamp: DateTime<Utc>,
    pub payload: Option<Map<String, Value>>,
    pub error: Option<ErrorInfo>,
}

impl Envelope {
    pub fn request(kind: &str, payload: Map<String, Value>) -> Self {
        Self {
            request_id: Uuid::now_v7(),
            protocol_version: PROTOCOL_VERSION,
            kind: kind.to_owned(),
            timestamp: Utc::now(),
            payload: Some(payload),
            error: None,
        }
    }

    /// Parses and validates one frame received from the worker.
    pub fn from_frame(frame: &[u8]) -> Result<Self, String> {
        let envelope: Self =
            serde_json::from_slice(frame).map_err(|e| format!("malformed frame: {e}"))?;
        if envelope.protocol_version != PROTOCOL_VERSION {
            return Err(format!(
                "protocol version {} (this build speaks {PROTOCOL_VERSION})",
                envelope.protocol_version
            ));
        }
        if !is_valid_type(&envelope.kind) {
            return Err(format!("invalid message type {:?}", envelope.kind));
        }
        if envelope.payload.is_some() && envelope.error.is_some() {
            return Err("payload and error are mutually exclusive".into());
        }
        Ok(envelope)
    }

    pub fn into_payload<T: DeserializeOwned>(self) -> Result<T, String> {
        let kind = self.kind;
        let payload = self
            .payload
            .ok_or_else(|| format!("'{kind}' carries no payload"))?;
        serde_json::from_value(Value::Object(payload))
            .map_err(|e| format!("invalid '{kind}' payload: {e}"))
    }
}

/// Mirrors the Python pattern `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$`, max 64 chars.
fn is_valid_type(kind: &str) -> bool {
    kind.len() <= 64
        && kind.split('.').all(|segment| {
            let mut chars = segment.chars();
            chars.next().is_some_and(|c| c.is_ascii_lowercase())
                && chars.all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_')
        })
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ErrorCode {
    InvalidMessage,
    UnsupportedProtocol,
    UnknownType,
    InvalidPayload,
    Timeout,
    ShuttingDown,
    Internal,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ErrorInfo {
    pub code: ErrorCode,
    pub message: String,
    pub retryable: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ComponentState {
    Ok,
    Degraded,
    Unavailable,
    Error,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum HealthState {
    Ok,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HealthResult {
    pub status: HealthState,
    pub worker_version: String,
    pub protocol_version: u32,
    pub python_version: String,
    pub pid: u32,
    pub started_at: DateTime<Utc>,
    pub uptime_s: f64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DatabaseEngine {
    Sqlite,
    Duckdb,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DatabaseStatus {
    pub state: ComponentState,
    pub summary: String,
    pub error: Option<String>,
    pub checked_at: DateTime<Utc>,
    pub duration_ms: f64,
    pub engine: DatabaseEngine,
    pub path: Option<String>,
    pub engine_version: Option<String>,
    pub schema_version: Option<u32>,
    pub initialized_at: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OllamaModel {
    pub name: String,
    pub size_bytes: Option<u64>,
    pub digest: Option<String>,
    pub family: Option<String>,
    pub parameter_size: Option<String>,
    pub quantization: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OllamaStatus {
    pub state: ComponentState,
    pub summary: String,
    pub error: Option<String>,
    pub checked_at: DateTime<Utc>,
    pub duration_ms: f64,
    pub installed: bool,
    pub running: bool,
    pub base_url: String,
    pub version: Option<String>,
    pub models: Vec<OllamaModel>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GpuDevice {
    pub index: u32,
    pub name: String,
    pub memory_total_mib: Option<u64>,
    pub memory_free_mib: Option<u64>,
    pub driver_version: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GpuStatus {
    pub state: ComponentState,
    pub summary: String,
    pub error: Option<String>,
    pub checked_at: DateTime<Utc>,
    pub duration_ms: f64,
    pub devices: Vec<GpuDevice>,
    pub cuda_available: bool,
    pub cuda_driver_version: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DiagnosticsResult {
    pub health: HealthResult,
    pub sqlite: DatabaseStatus,
    pub duckdb: DatabaseStatus,
    pub ollama: OllamaStatus,
    pub gpu: GpuStatus,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum WorkerState {
    Stopped,
    Starting,
    Running,
    Stopping,
    Restarting,
    Failed,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkerStatus {
    pub state: WorkerState,
    pub pid: Option<u32>,
    pub started_at: Option<DateTime<Utc>>,
    pub restart_count: u32,
    pub last_error: Option<String>,
    pub stderr_tail: Vec<String>,
    pub health: Option<HealthResult>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CoreStatus {
    pub state: ComponentState,
    pub summary: String,
    pub app_version: String,
    pub environment: Environment,
    pub os: String,
    pub arch: String,
    pub data_dir: String,
    pub log_dir: String,
    pub config_sources: Vec<String>,
    pub config_warnings: Vec<String>,
    pub started_at: DateTime<Utc>,
    pub uptime_s: f64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CommandErrorCode {
    WorkerUnavailable,
    WorkerTimeout,
    WorkerError,
    ProtocolViolation,
    InvalidInput,
    Internal,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CommandError {
    pub code: CommandErrorCode,
    pub message: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DiagnosticsReport {
    pub generated_at: DateTime<Utc>,
    pub core: CoreStatus,
    pub worker: WorkerStatus,
    pub components: Option<DiagnosticsResult>,
    pub components_error: Option<CommandError>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct UiErrorReport {
    pub source: String,
    pub message: String,
    pub stack: Option<String>,
}

#[cfg(test)]
mod tests {
    use super::*;

    const HEALTH_REQUEST: &str = r#"{"request_id":"01900000-0000-7000-8000-000000000001","protocol_version":1,"type":"health","timestamp":"2026-01-01T00:00:00Z","payload":{},"error":null}"#;

    #[test]
    fn valid_frame_parses() {
        let envelope = Envelope::from_frame(HEALTH_REQUEST.as_bytes()).expect("valid frame");
        assert_eq!(envelope.kind, "health");
        assert_eq!(envelope.payload, Some(Map::new()));
    }

    #[test]
    fn invalid_frames_are_rejected() {
        let cases = [
            HEALTH_REQUEST.replace("\"protocol_version\":1", "\"protocol_version\":2"),
            HEALTH_REQUEST.replace("\"type\":\"health\"", "\"type\":\"Health\""),
            HEALTH_REQUEST.replace("\"type\":\"health\"", "\"type\":\"health..result\""),
            HEALTH_REQUEST.replace("\"error\":null", "\"error\":null,\"extra\":1"),
            HEALTH_REQUEST.replace(
                "\"error\":null",
                "\"error\":{\"code\":\"timeout\",\"message\":\"x\",\"retryable\":true}",
            ),
            HEALTH_REQUEST.replace("01900000-0000-7000-8000-000000000001", "not-a-uuid"),
            "{\"truncated\":".to_owned(),
        ];
        for case in cases {
            assert!(
                Envelope::from_frame(case.as_bytes()).is_err(),
                "accepted: {case}"
            );
        }
    }

    #[test]
    fn message_type_validation_matches_python_pattern() {
        for valid in [
            "health",
            "diagnostics.result",
            "worker.protocol_error",
            "a1_b.c2",
        ] {
            assert!(is_valid_type(valid), "{valid}");
        }
        for invalid in [
            "",
            ".health",
            "health.",
            "1health",
            "Health",
            "a-b",
            &"a".repeat(65),
        ] {
            assert!(!is_valid_type(invalid), "{invalid}");
        }
    }

    #[test]
    fn requests_use_time_ordered_ids_and_current_protocol() {
        let first = Envelope::request("health", Map::new());
        let second = Envelope::request("health", Map::new());
        assert_eq!(first.protocol_version, PROTOCOL_VERSION);
        assert!(
            first.request_id < second.request_id,
            "UUIDv7 ids sort by creation time"
        );
    }
}
