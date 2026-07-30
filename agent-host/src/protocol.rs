//! Wire contracts shared with the Lemma Agent Host API.

use std::collections::BTreeMap;

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use uuid::Uuid;

use crate::{HOST_RELEASE, PROTOCOL_VERSION};

pub type JsonMap = BTreeMap<String, Value>;

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct HostHello {
    pub installation_id: String,
    pub host_release: String,
    pub protocol_version: u16,
}

impl HostHello {
    #[must_use]
    pub fn current(installation_id: impl Into<String>) -> Self {
        Self {
            installation_id: installation_id.into(),
            host_release: HOST_RELEASE.to_owned(),
            protocol_version: PROTOCOL_VERSION,
        }
    }
}

#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq, Eq)]
pub struct HostCapacity {
    pub max_runs: u16,
    pub active_runs: u16,
    pub available_runs: u16,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct RunCheckpoint {
    pub run_id: Uuid,
    pub lease_epoch: u32,
    pub state: RunState,
    #[serde(default)]
    pub detail: JsonMap,
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum RunState {
    QueuedForHost,
    Leased,
    Accepted,
    Dispatching,
    Running,
    Recovering,
    WaitingInput,
    Succeeded,
    Failed,
    Cancelled,
    DispatchUnknown,
}

impl RunState {
    #[must_use]
    pub const fn is_terminal(self) -> bool {
        matches!(
            self,
            Self::WaitingInput
                | Self::Succeeded
                | Self::Failed
                | Self::Cancelled
                | Self::DispatchUnknown
        )
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PollRequest {
    pub hello: HostHello,
    pub capacity: HostCapacity,
    #[serde(default)]
    pub acknowledged_command_ids: Vec<Uuid>,
    #[serde(default)]
    pub checkpoints: Vec<RunCheckpoint>,
    #[serde(default)]
    pub rejections: Vec<CommandRejection>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct CommandRejection {
    pub command_id: Uuid,
    pub run_id: Uuid,
    pub lease_epoch: u32,
    pub code: RejectionCode,
    pub retryable: bool,
    pub detail: Option<String>,
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum RejectionCode {
    Draining,
    CommandExpired,
    HarnessNotFound,
    ConfigRevisionStale,
    InvalidSelections,
    CapacityLost,
    AdapterUnavailable,
    InvalidCommand,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PollResponse {
    pub protocol_version: u16,
    pub host_status: HostStatus,
    #[serde(default)]
    pub commands: Vec<Command>,
    #[serde(default)]
    pub poll_after_ms: u64,
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum HostStatus {
    Online,
    Offline,
    Draining,
    UpgradeRequired,
    Revoked,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Command {
    pub command_id: Uuid,
    pub kind: CommandKind,
    pub created_at: DateTime<Utc>,
    pub expires_at: DateTime<Utc>,
    pub run_id: Option<Uuid>,
    pub lease_epoch: Option<u32>,
    #[serde(default)]
    pub payload: Value,
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum CommandKind {
    StartRun,
    CancelRun,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RunSpec {
    pub agent_run_id: Uuid,
    pub conversation_id: Uuid,
    pub harness_id: Uuid,
    pub profile_revision: String,
    #[serde(default)]
    pub model_name: Option<String>,
    #[serde(default)]
    pub config_selections: JsonMap,
    pub system_prompt: String,
    pub prompt: Vec<Value>,
    #[serde(default)]
    pub context: JsonMap,
    /// Run-scoped Lemma MCP configuration, delivered inline with the command.
    #[serde(default)]
    pub mcp: Value,
    pub run_deadline: DateTime<Utc>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Event {
    pub run_id: Uuid,
    pub lease_epoch: u32,
    pub sequence: u64,
    pub event_id: Uuid,
    pub occurred_at: DateTime<Utc>,
    #[serde(rename = "type")]
    pub event_type: EventType,
    pub object_id: Option<String>,
    #[serde(default)]
    pub payload: JsonMap,
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum EventType {
    RunState,
    UserMessage,
    AgentMessageChunk,
    AgentMessageUpsert,
    AgentThoughtChunk,
    AgentThoughtUpsert,
    PlanUpsert,
    ToolCallUpsert,
    ToolCallUpdate,
    UsageUpdate,
    ConfigUpdate,
    PermissionRequest,
    InputRequest,
    Warning,
    Terminal,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct EventBatch {
    pub events: Vec<Event>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct EventAck {
    pub run_id: Uuid,
    pub lease_epoch: u32,
    pub acked_through: u64,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PairingCompleteRequest {
    pub pairing_code: String,
    pub display_name: String,
    pub hello: HostHello,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PairingCompleteResponse {
    pub host_id: Uuid,
    pub user_id: Uuid,
    pub organization_id: Option<Uuid>,
    pub host_secret: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct HarnessPublishRequest {
    pub harnesses: Vec<HarnessSnapshot>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct HarnessSnapshot {
    pub harness_key: String,
    pub display_name: String,
    pub adapter_version: String,
    pub upstream_version: Option<String>,
    pub health: HarnessHealth,
    #[serde(default)]
    pub capabilities: HarnessCapabilities,
    pub config_revision: String,
    #[serde(default)]
    pub config_options: Vec<ConfigOption>,
    pub stale_after: DateTime<Utc>,
    pub stale_reason: Option<String>,
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum HarnessHealth {
    Ready,
    AuthRequired,
    UnsupportedVersion,
    ConfigInvalid,
    ProbeFailed,
    Installing,
    Disabled,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct HarnessCapabilities {
    pub load_session: bool,
    pub resume_session: bool,
    pub close_session: bool,
    pub images: bool,
    pub plans: bool,
    pub usage: bool,
    pub durable_session_recovery: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ConfigOption {
    pub id: String,
    pub category: String,
    pub name: String,
    pub description: Option<String>,
    pub current_value: Value,
    #[serde(default)]
    pub options: Vec<JsonMap>,
    #[serde(default)]
    pub metadata: JsonMap,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn terminal_states_are_explicit() {
        assert!(RunState::Succeeded.is_terminal());
        assert!(RunState::DispatchUnknown.is_terminal());
        assert!(!RunState::Running.is_terminal());
    }
}
