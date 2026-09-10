//! What a caller puts in and gets back.

use super::{CommandRejection, RunCheckpoint, RunSpec, RunState, Uuid};

pub(crate) type PendingControl = (Vec<Uuid>, Vec<RunCheckpoint>, Vec<CommandRejection>);

/// Local dispatch-progress bookkeeping for a journaled run.
///
/// This is host-internal only (crash recovery and replay deduplication); the
/// server tracks run progress through the reported run state alone.
#[derive(Clone, Copy, Debug, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum Checkpoint {
    Accepted,
    DispatchIntent,
    ProviderAccepted,
    Running,
    Recovering,
    Terminal,
}

impl Checkpoint {
    pub(crate) fn for_state(state: RunState) -> Self {
        match state {
            RunState::Dispatching => Self::DispatchIntent,
            RunState::Running => Self::ProviderAccepted,
            RunState::Recovering => Self::Recovering,
            RunState::WaitingInput
            | RunState::Succeeded
            | RunState::Failed
            | RunState::Cancelled
            | RunState::DispatchUnknown => Self::Terminal,
            RunState::QueuedForHost | RunState::Leased | RunState::Accepted => Self::Accepted,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum AcceptOutcome {
    New,
    Duplicate,
}

#[derive(Clone, Debug)]
pub struct JournalRun {
    pub target_id: Uuid,
    pub run_id: Uuid,
    pub lease_epoch: u32,
    pub command_id: Uuid,
    pub harness_key: String,
    pub adapter_version: String,
    pub state: RunState,
    pub checkpoint: Checkpoint,
    pub spec: RunSpec,
    pub provider_session_id: Option<String>,
    pub prompt_dispatched: bool,
}

#[derive(Clone, Debug, serde::Serialize)]
pub struct TargetJournalStatus {
    pub target_id: Uuid,
    pub connection_state: String,
    pub last_error: Option<String>,
    pub last_connected_at: Option<String>,
    pub active_runs: u64,
    pub pending_events: u64,
}

#[derive(Debug, thiserror::Error)]
pub enum JournalError {
    #[error("SQLite journal error: {0}")]
    Sql(#[from] rusqlite::Error),
    #[error("journal JSON error: {0}")]
    Json(#[from] serde_json::Error),
    #[error("command {0} was replayed with a different payload")]
    CommandConflict(Uuid),
    #[error("run {0} was replayed with a different lease")]
    LeaseConflict(Uuid),
    #[error("run {0} does not exist in the journal")]
    RunMissing(Uuid),
    #[error("invalid persisted enum value {0}")]
    InvalidEnum(String),
    #[error("event acknowledgement does not match an active run")]
    AckMismatch,
}

pub(crate) fn enum_json<T: serde::Serialize>(value: T) -> Result<String, serde_json::Error> {
    serde_json::to_string(&value).map(|value| value.trim_matches('"').to_owned())
}

pub(crate) fn enum_parse<T: serde::de::DeserializeOwned>(value: &str) -> Result<T, JournalError> {
    serde_json::from_str(&format!("\"{value}\""))
        .map_err(|_| JournalError::InvalidEnum(value.to_owned()))
}
