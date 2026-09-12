//! Single error type for the desktop core. Technical detail goes to the log where the error is
//! raised; the UI receives a `CommandError` whose message is safe and actionable.

use serde::{Serialize, Serializer};

use crate::protocol::{CommandError, CommandErrorCode};

#[derive(Debug, thiserror::Error)]
pub enum AppError {
    #[error("The Python worker is not available: {0}")]
    WorkerUnavailable(String),
    #[error("The Python worker did not answer '{kind}' within {timeout_ms} ms")]
    WorkerTimeout { kind: String, timeout_ms: u64 },
    #[error("The Python worker could not complete the request: {0}")]
    Worker(String),
    #[error("Unexpected message from the Python worker: {0}")]
    Protocol(String),
    #[error("Invalid input: {0}")]
    InvalidInput(String),
    #[error("Not found: {0}")]
    NotFound(String),
    #[error("Not allowed: {0}")]
    NotAllowed(String),
}

impl AppError {
    pub fn code(&self) -> CommandErrorCode {
        match self {
            Self::WorkerUnavailable(_) => CommandErrorCode::WorkerUnavailable,
            Self::WorkerTimeout { .. } => CommandErrorCode::WorkerTimeout,
            Self::Worker(_) => CommandErrorCode::WorkerError,
            Self::Protocol(_) => CommandErrorCode::ProtocolViolation,
            Self::InvalidInput(_) => CommandErrorCode::InvalidInput,
            Self::NotFound(_) => CommandErrorCode::NotFound,
            Self::NotAllowed(_) => CommandErrorCode::NotAllowed,
        }
    }

    pub fn to_command_error(&self) -> CommandError {
        CommandError {
            code: self.code(),
            message: self.to_string(),
        }
    }
}

impl Serialize for AppError {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        self.to_command_error().serialize(serializer)
    }
}
