//! Wire contracts shared with the Lemma Agent Host API.

use std::collections::BTreeMap;

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use uuid::Uuid;

use crate::{HOST_RELEASE, PROTOCOL_VERSION};

/// How long Lemma holds a poll open before answering it empty.
///
/// Part of the wire contract rather than a local choice — it is
/// `_LONG_POLL_SECONDS` in `agent_host_controller.py` — and worth stating
/// because two things are built on it and neither reads as depending on it.
///
/// The HTTP timeout has to clear it or every poll fails. And the worker loop
/// spends essentially all of its time inside one, so anything the host must do
/// sooner than this needs to be able to interrupt a poll; it cannot wait for one
/// to return. Assuming otherwise is how a two-second check for newly installed
/// agents came to answer in up to twenty-five.
pub const POLL_HOLD: std::time::Duration = std::time::Duration::from_secs(25);

pub type JsonMap = BTreeMap<String, Value>;

/// Give a wire enum the list of its own variants.
///
/// So that anything needing to walk one walks real data. The alternative that
/// was here — scraping this file for `pub enum X {` and reading to the next `}`,
/// then reimplementing serde's `rename_all` to guess the spelling — was two
/// guesses at things the compiler and serde already know exactly, and both
/// would have failed silently: the parser on the first variant to gain a brace,
/// the spelling on the first `#[serde(rename)]`.
///
/// Unit variants only, which is what every enum on this wire is and what the
/// backend's `str` enums can be.
macro_rules! wire_enum {
    (
        $(#[$enum_meta:meta])*
        pub enum $name:ident {
            $($(#[$variant_meta:meta])* $variant:ident),* $(,)?
        }
    ) => {
        $(#[$enum_meta])*
        pub enum $name {
            $($(#[$variant_meta])* $variant),*
        }

        impl $name {
            /// Every variant, in declaration order.
            #[must_use]
            pub const fn all() -> &'static [Self] {
                &[$(Self::$variant),*]
            }

            /// Every variant as it is spelled on the wire, straight from serde
            /// rather than from a second implementation of the naming rule.
            #[cfg(test)]
            #[must_use]
            pub fn wire_names() -> Vec<String> {
                Self::all()
                    .iter()
                    .map(|variant| {
                        serde_json::to_value(variant)
                            .expect("a unit variant serializes")
                            .as_str()
                            .expect("a wire enum serializes to a string")
                            .to_owned()
                    })
                    .collect()
            }
        }
    };
}

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

wire_enum! {
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

wire_enum! {
    #[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
    #[serde(rename_all = "SCREAMING_SNAKE_CASE")]
    pub enum RejectionCode {
        Draining,
        CommandExpired,
        HarnessNotFound,
        ConfigRevisionStale,
        CapacityLost,
        AdapterUnavailable,
        InvalidCommand,
    }
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

wire_enum! {
    #[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
    #[serde(rename_all = "SCREAMING_SNAKE_CASE")]
    pub enum HostStatus {
        Online,
        Offline,
        Draining,
        UpgradeRequired,
        Revoked,
    }
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

wire_enum! {
    #[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
    #[serde(rename_all = "SCREAMING_SNAKE_CASE")]
    pub enum CommandKind {
        StartRun,
        CancelRun,
        /// Carries a human's answer to a parked native permission request.
        ResolvePermission,
        /// Carries a replacement Lemma MCP credential for a run still in flight.
        ///
        /// The one minted at dispatch is valid for an hour and nothing used to
        /// renew it, so a long turn either had to be cut short at that expiry or
        /// carry on with every Lemma tool call returning 401 — which the agent
        /// experiences as its tools quietly vanishing mid-task.
        RefreshCredential,
    }
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
    /// The provider session this conversation has been using, when Lemma has
    /// seen one and the harness can load it back. Absent on a conversation's
    /// first turn, and absent for a harness that cannot resume — in both cases
    /// the run opens a fresh session.
    #[serde(default)]
    pub resume_session_id: Option<String>,
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
    #[serde(rename = "type")]
    pub event_type: EventType,
    pub object_id: Option<String>,
    #[serde(default)]
    pub payload: JsonMap,
}

wire_enum! {
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
        Terminal,
    }
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

impl HarnessSnapshot {
    /// The identity Lemma fences a dispatched run against.
    ///
    /// Lives here, on the snapshot, because there used to be two of these:
    /// `adapters::snapshot_ready` hashed `{adapter, upstream, config}` for a
    /// harness that had been discovered but not yet probed, and `runtime`
    /// hashed `{adapter_version, upstream_version, config_options,
    /// capabilities}` once the probe landed. Same concept, different keys — so
    /// the same harness state hashed differently depending on which path
    /// produced it, and a revision could change with nothing about the harness
    /// changing at all.
    ///
    /// `upstream_version` is deliberately **not** an input. It is the version
    /// of the agent's own CLI, which updates itself: including it meant every
    /// Claude Code patch release minted a new revision, and a run command
    /// already in flight was then rejected for naming the old one. What the
    /// fence is actually protecting is the *configuration* a profile was bound
    /// against — the options offered and the capabilities advertised — and a
    /// release that changes either of those changes them here too.
    #[must_use]
    pub fn revision(&self) -> String {
        let value = serde_json::json!({
            "adapter_version": self.adapter_version,
            "config_options": self.config_options,
            "capabilities": self.capabilities,
        });
        hex::encode(Sha256::digest(
            serde_json::to_vec(&value).expect("snapshot revision serialization"),
        ))
    }
}

wire_enum! {
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

    /// The committed client spec, which CI already holds to the backend.
    fn spec_enum(name: &str) -> Vec<String> {
        let path = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../lemma-python/lemma_sdk/openapi_spec.json"
        );
        let raw = std::fs::read_to_string(path).expect("the committed OpenAPI spec is readable");
        let spec: serde_json::Value =
            serde_json::from_str(&raw).expect("the committed OpenAPI spec parses");
        spec["components"]["schemas"][name]["enum"]
            .as_array()
            .unwrap_or_else(|| panic!("{name} is an enum in the client spec"))
            .iter()
            .map(|value| value.as_str().expect("enum members are strings").to_owned())
            .collect()
    }

    #[test]
    fn the_wire_states_match_the_backend_exactly() {
        // These enums exist twice — once here, once in the backend — and agreed
        // only because someone kept them in step by hand. A variant added on one
        // side and not the other is a run state the other end cannot parse, and
        // nothing failed until it reached a user.
        //
        // The spec is the arbiter because CI already refuses to let it drift
        // from the backend, so pinning to it pins to the backend transitively.
        // Compared as sets in both directions: an addition here that the backend
        // has never heard of is exactly as broken as the reverse.
        //
        // The names come from `serde` rather than from parsing this file. The
        // first version of this test scraped the source for `pub enum X {` and
        // read to the next `}`, then reimplemented `rename_all` to guess the
        // wire spelling. Both halves were guesses at things the compiler and
        // serde already know exactly: the parser would have silently mis-read
        // the first enum to gain a braced variant or a doc comment containing a
        // brace, and the spelling would have diverged the first time a variant
        // needed its own `#[serde(rename)]`.
        for (ours, spec_name) in [
            (RunState::wire_names(), "AgentHostRunState"),
            (HostStatus::wire_names(), "AgentHostStatus"),
            (HarnessHealth::wire_names(), "AgentHostHarnessHealth"),
            (RejectionCode::wire_names(), "AgentHostRejectionCode"),
            (CommandKind::wire_names(), "AgentHostCommandKind"),
            (EventType::wire_names(), "AgentHostEventType"),
        ] {
            let mut ours = ours;
            let mut theirs = spec_enum(spec_name);
            ours.sort();
            theirs.sort();
            assert_eq!(
                ours, theirs,
                "{spec_name} has drifted apart from this crate"
            );
        }
    }

    #[test]
    fn terminal_states_are_explicit() {
        assert!(RunState::Succeeded.is_terminal());
        assert!(RunState::DispatchUnknown.is_terminal());
        assert!(!RunState::Running.is_terminal());
    }

    fn snapshot() -> HarnessSnapshot {
        HarnessSnapshot {
            harness_key: "claude-code".into(),
            display_name: "Claude Code".into(),
            adapter_version: "0.62.0".into(),
            upstream_version: Some("2.1.0".into()),
            health: HarnessHealth::Ready,
            capabilities: HarnessCapabilities::default(),
            config_revision: String::new(),
            config_options: Vec::new(),
            stale_after: Utc::now(),
            stale_reason: None,
        }
    }

    /// The whole point of dropping `upstream_version` from the hash.
    ///
    /// Claude Code updates itself. While its version was an input, every patch
    /// release minted a revision Lemma had not dispatched against, and any run
    /// command already in flight was refused for naming the old one — a
    /// permanently failed run, caused by an agent quietly keeping itself up to
    /// date.
    #[test]
    fn an_agent_updating_itself_does_not_change_the_revision() {
        let before = snapshot();
        let mut after = snapshot();
        after.upstream_version = Some("2.4.1".into());

        assert_eq!(before.revision(), after.revision());
    }

    /// What the fence is actually protecting: a profile was bound against a
    /// set of options, and those changing is exactly when its saved selections
    /// need revalidating.
    #[test]
    fn a_changed_option_set_changes_the_revision() {
        let before = snapshot();
        let mut after = snapshot();
        after.config_options.push(ConfigOption {
            id: "model".into(),
            category: "model".into(),
            name: "Model".into(),
            description: None,
            current_value: Value::Null,
            options: Vec::new(),
            metadata: JsonMap::new(),
        });

        assert_ne!(before.revision(), after.revision());
    }

    /// Capabilities decide whether a run may resume a session, so a harness
    /// that stopped supporting `session/load` is not the one a profile bound.
    #[test]
    fn changed_capabilities_change_the_revision() {
        let before = snapshot();
        let mut after = snapshot();
        after.capabilities.load_session = true;

        assert_ne!(before.revision(), after.revision());
    }

    /// Health is reported alongside the revision, not inside it: a signed-out
    /// agent is refused by admission on `health`, and folding it in here would
    /// mint a new revision every time a session expired and was renewed.
    #[test]
    fn health_is_not_part_of_the_revision() {
        let before = snapshot();
        let mut after = snapshot();
        after.health = HarnessHealth::AuthRequired;
        after.stale_reason = Some("not signed in".into());

        assert_eq!(before.revision(), after.revision());
    }
}
