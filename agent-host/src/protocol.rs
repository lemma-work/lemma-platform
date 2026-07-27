//! Wire contracts shared with the Lemma Agent Host v2 API.

use std::collections::BTreeMap;

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use uuid::Uuid;

use crate::{HOST_RELEASE, PROTOCOL_VERSION};

pub type JsonMap = BTreeMap<String, Value>;

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct HostHello {
    pub protocol_min: u16,
    pub protocol_max: u16,
    pub host_release: String,
    pub adapter_manifest_id: String,
    pub installation_id: String,
    pub instance_id: Uuid,
}

impl HostHello {
    #[must_use]
    pub fn current(
        adapter_manifest_id: impl Into<String>,
        installation_id: impl Into<String>,
        instance_id: Uuid,
    ) -> Self {
        Self {
            protocol_min: PROTOCOL_VERSION,
            protocol_max: PROTOCOL_VERSION,
            host_release: HOST_RELEASE.to_owned(),
            adapter_manifest_id: adapter_manifest_id.into(),
            installation_id: installation_id.into(),
            instance_id,
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
    pub checkpoint: Checkpoint,
    pub state: RunState,
    #[serde(default)]
    pub detail: JsonMap,
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum Checkpoint {
    Accepted,
    DispatchIntent,
    ProviderAccepted,
    Running,
    Recovering,
    Terminal,
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
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PollResponse {
    pub protocol_version: u16,
    pub policy_revision: String,
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
    Degraded,
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
    pub payload_sha256: String,
    #[serde(default)]
    pub payload: Value,
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum CommandKind {
    StartRun,
    CancelRun,
    Drain,
    Resume,
    RefreshIntegration,
    CloseSession,
    RotateDeviceKey,
}

impl Command {
    pub fn verify_payload(&self) -> Result<(), ProtocolError> {
        let encoded = canonical_json(&self.payload)?;
        let actual = hex::encode(Sha256::digest(encoded));
        if actual == self.payload_sha256 {
            Ok(())
        } else {
            Err(ProtocolError::PayloadDigestMismatch {
                command_id: self.command_id,
            })
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RunSpec {
    pub agent_run_id: Uuid,
    pub conversation_id: Uuid,
    pub integration_id: Uuid,
    pub profile_revision: String,
    #[serde(default)]
    pub config_selections: JsonMap,
    pub system_prompt: String,
    pub prompt: Vec<Value>,
    #[serde(default)]
    pub context: JsonMap,
    pub mcp_route_id: Uuid,
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
    pub integration_key: String,
    pub adapter_version: String,
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
    pub public_key: String,
    pub display_name: String,
    pub hello: HostHello,
    pub nonce: String,
    pub timestamp: i64,
    pub signature: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PairingCompleteResponse {
    pub host_id: Uuid,
    pub user_id: Uuid,
    pub organization_id: Option<Uuid>,
    pub public_key_fingerprint: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TokenExchangeRequest {
    pub host_id: Uuid,
    pub nonce: String,
    pub timestamp: i64,
    pub signature: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TokenResponse {
    pub access_token: String,
    pub expires_at: DateTime<Utc>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct McpRoute {
    pub route_id: Uuid,
    pub run_id: Uuid,
    pub lease_epoch: u32,
    pub expires_at: DateTime<Utc>,
    pub mcp: Value,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct IntegrationPublishRequest {
    pub integrations: Vec<IntegrationSnapshot>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct IntegrationSnapshot {
    pub integration_key: String,
    pub display_name: String,
    pub adapter_protocol: AdapterProtocol,
    pub adapter_version: String,
    pub upstream_version: Option<String>,
    pub auth_state: String,
    pub health: IntegrationHealth,
    #[serde(default)]
    pub capabilities: IntegrationCapabilities,
    pub config_revision: String,
    #[serde(default)]
    pub config_options: Vec<ConfigOption>,
    pub fetched_at: DateTime<Utc>,
    pub stale_after: DateTime<Utc>,
    pub stale_reason: Option<String>,
    #[serde(default)]
    pub metadata: JsonMap,
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum AdapterProtocol {
    AcpV1,
    Native,
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum IntegrationHealth {
    Ready,
    AuthRequired,
    UnsupportedVersion,
    ConfigInvalid,
    ProbeFailed,
    Installing,
    Disabled,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct IntegrationCapabilities {
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

#[derive(Debug, thiserror::Error)]
pub enum ProtocolError {
    #[error("command {command_id} payload digest does not match")]
    PayloadDigestMismatch { command_id: Uuid },
    #[error("failed to encode canonical JSON: {0}")]
    Json(#[from] serde_json::Error),
}

pub fn canonical_json(value: &Value) -> Result<Vec<u8>, serde_json::Error> {
    fn append(value: &Value, output: &mut Vec<u8>) -> Result<(), serde_json::Error> {
        match value {
            Value::Object(map) => {
                output.push(b'{');
                let mut entries = map.iter().collect::<Vec<_>>();
                entries.sort_unstable_by(|(left, _), (right, _)| left.cmp(right));
                for (index, (key, value)) in entries.into_iter().enumerate() {
                    if index > 0 {
                        output.push(b',');
                    }
                    output.extend(serde_json::to_vec(key)?);
                    output.push(b':');
                    append(value, output)?;
                }
                output.push(b'}');
            }
            Value::Array(values) => {
                output.push(b'[');
                for (index, value) in values.iter().enumerate() {
                    if index > 0 {
                        output.push(b',');
                    }
                    append(value, output)?;
                }
                output.push(b']');
            }
            primitive => output.extend(serde_json::to_vec(primitive)?),
        }
        Ok(())
    }
    let mut output = Vec::new();
    append(value, &mut output)?;
    Ok(output)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn canonical_json_is_stable_across_key_order() {
        let left = serde_json::json!({"b": 2, "a": {"d": 4, "c": 3}});
        let right = serde_json::json!({"a": {"c": 3, "d": 4}, "b": 2});
        assert_eq!(
            canonical_json(&left).unwrap(),
            canonical_json(&right).unwrap()
        );
    }

    #[test]
    fn terminal_states_are_explicit() {
        assert!(RunState::Succeeded.is_terminal());
        assert!(RunState::DispatchUnknown.is_terminal());
        assert!(!RunState::Running.is_terminal());
    }
}
