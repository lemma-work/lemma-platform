//! The vocabulary of host execution: op methods, failure kinds, and the JSON
//! lines the exec-server speaks on stdio.
//!
//! The method and kind names are the contract with the backend's
//! `AgentHostSandboxProvider`, held to `tests/fixtures/wire_contract.json`
//! (`host_execution`) from both sides. See
//! docs/architecture/desktop-host-execution.md §4.

use serde::{Deserialize, Serialize};
use serde_json::Value;

/// The `op` methods, as the backend names them.
pub mod method {
    pub const WORKSPACE_OPEN: &str = "workspace.open";
    pub const WORKSPACE_CLOSE: &str = "workspace.close";
    pub const PROCESS_START: &str = "process.start";
    pub const PROCESS_READ: &str = "process.read";
    pub const PROCESS_INPUT: &str = "process.input";
    pub const PROCESS_RESIZE: &str = "process.resize";
    pub const PROCESS_TERMINATE: &str = "process.terminate";
    pub const PROCESS_LIST: &str = "process.list";
    pub const FILE_STAT: &str = "file.stat";
    pub const FILE_LIST: &str = "file.list";
    pub const FILE_MKDIR: &str = "file.mkdir";
    pub const FILE_READ: &str = "file.read";
    pub const FILE_WRITE: &str = "file.write";
    pub const FILE_MOVE: &str = "file.move";
    pub const FILE_DELETE: &str = "file.delete";
    pub const SECRET_DELIVER: &str = "secret.deliver";

    /// Every method, for the contract test.
    pub const ALL: [&str; 16] = [
        WORKSPACE_OPEN,
        WORKSPACE_CLOSE,
        PROCESS_START,
        PROCESS_READ,
        PROCESS_INPUT,
        PROCESS_RESIZE,
        PROCESS_TERMINATE,
        PROCESS_LIST,
        FILE_STAT,
        FILE_LIST,
        FILE_MKDIR,
        FILE_READ,
        FILE_WRITE,
        FILE_MOVE,
        FILE_DELETE,
        SECRET_DELIVER,
    ];
}

/// `detail.kind` on a failed op. The provider maps each onto a
/// `sandbox_runtime` error, so a new kind here is a change on both sides.
pub mod kind {
    pub const NOT_FOUND: &str = "not_found";
    pub const ALREADY_EXISTS: &str = "already_exists";
    pub const NOT_A_DIRECTORY: &str = "not_a_directory";
    pub const IS_A_DIRECTORY: &str = "is_a_directory";
    /// Including a Seatbelt denial, which reaches us as `EPERM`.
    pub const PERMISSION_DENIED: &str = "permission_denied";
    pub const OUTSIDE_WORKSPACE: &str = "outside_workspace";
    pub const DIGEST_MISMATCH: &str = "digest_mismatch";
    pub const TOO_LARGE: &str = "too_large";
    pub const PROCESS_NOT_FOUND: &str = "process_not_found";
    pub const WORKSPACE_NOT_OPEN: &str = "workspace_not_open";
    pub const EXEC_SERVER_UNAVAILABLE: &str = "exec_server_unavailable";
    pub const TIMEOUT: &str = "timeout";
    /// The request itself was malformed: an unknown method, a missing or
    /// mistyped parameter. Retrying it cannot succeed.
    pub const INVALID_REQUEST: &str = "invalid_request";
    /// Any other operating-system failure: a full disk, an I/O error.
    pub const IO_ERROR: &str = "io_error";

    /// Every kind, for the contract test.
    pub const ALL: [&str; 14] = [
        NOT_FOUND,
        ALREADY_EXISTS,
        NOT_A_DIRECTORY,
        IS_A_DIRECTORY,
        PERMISSION_DENIED,
        OUTSIDE_WORKSPACE,
        DIGEST_MISMATCH,
        TOO_LARGE,
        PROCESS_NOT_FOUND,
        WORKSPACE_NOT_OPEN,
        EXEC_SERVER_UNAVAILABLE,
        TIMEOUT,
        INVALID_REQUEST,
        IO_ERROR,
    ];
}

/// The error code an op failure carries on the link. `detail.kind` says which.
pub const OP_FAILED: &str = "OP_FAILED";

/// The most bytes one frame's `data` carries, before base64.
pub const OP_MAX_DATA_BYTES: usize = 1024 * 1024;

/// Why an op failed, in the terms the provider maps.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, thiserror::Error)]
#[error("{kind}: {message}")]
pub struct OpFailure {
    pub kind: String,
    pub message: String,
    /// Whether the same op may succeed if sent again unchanged. Only the
    /// transient conditions -- the exec-server restarting, a deadline -- are.
    #[serde(default)]
    pub retryable: bool,
}

impl OpFailure {
    pub fn new(kind: &str, message: impl Into<String>) -> Self {
        Self {
            kind: kind.to_owned(),
            message: message.into(),
            retryable: matches!(kind, kind::EXEC_SERVER_UNAVAILABLE | kind::TIMEOUT),
        }
    }

    pub fn invalid(message: impl Into<String>) -> Self {
        Self::new(kind::INVALID_REQUEST, message)
    }

    #[must_use]
    pub fn outside(path: &std::path::Path) -> Self {
        Self::new(
            kind::OUTSIDE_WORKSPACE,
            format!(
                "{} is outside the workspace, its temporary directory and the folders it was granted",
                path.display()
            ),
        )
    }

    pub fn unavailable(message: impl Into<String>) -> Self {
        Self::new(kind::EXEC_SERVER_UNAVAILABLE, message)
    }

    /// An operating-system error, named for what the provider can do about it.
    #[must_use]
    pub fn io(error: &std::io::Error, subject: &std::path::Path) -> Self {
        use std::io::ErrorKind;
        let kind = match error.kind() {
            ErrorKind::NotFound => kind::NOT_FOUND,
            ErrorKind::AlreadyExists => kind::ALREADY_EXISTS,
            ErrorKind::PermissionDenied => kind::PERMISSION_DENIED,
            ErrorKind::NotADirectory => kind::NOT_A_DIRECTORY,
            ErrorKind::IsADirectory | ErrorKind::DirectoryNotEmpty => kind::IS_A_DIRECTORY,
            ErrorKind::TimedOut => kind::TIMEOUT,
            _ => kind::IO_ERROR,
        };
        Self::new(kind, format!("{}: {error}", subject.display()))
    }
}

/// One request to the exec-server: an `op` body with a correlation id.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct ExecRequest {
    pub id: String,
    pub workspace: String,
    pub method: String,
    #[serde(default)]
    pub params: Value,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub deadline_ms: Option<u64>,
}

/// One answer from the exec-server. Exactly one of `result` and `error`.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct ExecResponse {
    pub id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub result: Option<Value>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error: Option<OpFailure>,
}

impl ExecResponse {
    #[must_use]
    pub fn from_outcome(id: String, outcome: Result<Value, OpFailure>) -> Self {
        match outcome {
            Ok(result) => Self {
                id,
                result: Some(result),
                error: None,
            },
            Err(error) => Self {
                id,
                result: None,
                error: Some(error),
            },
        }
    }

    pub fn into_outcome(self) -> Result<Value, OpFailure> {
        match (self.result, self.error) {
            (_, Some(error)) => Err(error),
            (Some(result), None) => Ok(result),
            (None, None) => Ok(Value::Object(serde_json::Map::new())),
        }
    }
}

/// What a host tells Lemma about host execution, in `hello` and every
/// `control`: whether the owner turned it on, and whether this machine can do
/// it at all. Lemma routes an owner's run here only when both are true.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct HostExecutionStatus {
    pub enabled: bool,
    /// `macos`, `linux` or `windows`.
    pub platform: String,
    pub available: bool,
}

impl HostExecutionStatus {
    #[must_use]
    pub fn current(enabled: bool) -> Self {
        Self {
            enabled,
            platform: platform().to_owned(),
            available: super::seatbelt::available(),
        }
    }
}

/// This machine's platform, as `workspace.open` and `hello` name it.
#[must_use]
pub const fn platform() -> &'static str {
    if cfg!(target_os = "macos") {
        "macos"
    } else if cfg!(target_os = "windows") {
        "windows"
    } else {
        "linux"
    }
}
