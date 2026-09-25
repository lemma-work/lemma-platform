//! The frames the link carries.
//!
//! Every frame is one JSON text message, `{type, id?, re?, body}`. `id` names
//! a request that expects an answer, `re` on the answer points back at it, and
//! a push carries neither. The table of frames, the close codes and what each
//! side does with them are in docs/architecture/agent-host.md#the-link; the
//! backend's copy is `app/modules/agent/domain/agent_host_link.py`, and both
//! are held to `tests/fixtures/wire_contract.json`.

use serde::{Deserialize, Serialize};
use serde_json::Value;
use uuid::Uuid;

use crate::protocol::{
    Command, CommandRejection, EventAck, EventBatch, HarnessSnapshot, HostCapacity, HostHello,
    RunCheckpoint,
};

/// Where the link lives, under the workspace's API root.
pub const LINK_PATH: &str = "agent-host/link";

/// One message on the link.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct Frame {
    #[serde(rename = "type")]
    pub kind: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub re: Option<String>,
    #[serde(default)]
    pub body: Value,
}

/// The frame types a host sends.
pub mod host {
    pub const PAIR: &str = "pair";
    pub const HELLO: &str = "hello";
    pub const CONTROL: &str = "control";
    pub const EVENTS: &str = "events";
    pub const HARNESSES: &str = "harnesses";
    pub const MCP: &str = "mcp";
    pub const INTERACTION_WAIT: &str = "interaction_wait";
    pub const REVOKE: &str = "revoke";
    pub const ERROR: &str = "error";

    /// Every host frame, for the contract test.
    pub const ALL: [&str; 9] = [
        PAIR,
        HELLO,
        CONTROL,
        EVENTS,
        HARNESSES,
        MCP,
        INTERACTION_WAIT,
        REVOKE,
        ERROR,
    ];
}

/// The frame types Lemma sends.
pub mod server {
    pub const PAIRED: &str = "paired";
    pub const WELCOME: &str = "welcome";
    pub const CONTROL_OK: &str = "control_ok";
    pub const EVENTS_OK: &str = "events_ok";
    pub const HARNESSES_OK: &str = "harnesses_ok";
    pub const MCP_OK: &str = "mcp_ok";
    pub const INTERACTION_OK: &str = "interaction_ok";
    pub const REVOKED: &str = "revoked";
    pub const COMMANDS: &str = "commands";
    pub const RECONNECT: &str = "reconnect";
    pub const ERROR: &str = "error";

    /// Every server frame, for the contract test.
    pub const ALL: [&str; 11] = [
        PAIRED,
        WELCOME,
        CONTROL_OK,
        EVENTS_OK,
        HARNESSES_OK,
        MCP_OK,
        INTERACTION_OK,
        REVOKED,
        COMMANDS,
        RECONNECT,
        ERROR,
    ];
}

/// Why Lemma closed the link. See "Close codes" in the architecture doc.
pub mod close {
    pub const NORMAL: u16 = 1000;
    pub const RESTARTING: u16 = 1012;
    pub const PROTOCOL_VIOLATION: u16 = 4400;
    pub const REVOKED_OR_MISSING: u16 = 4401;
    pub const INVALID_CREDENTIAL: u16 = 4403;
    pub const HEARTBEAT_TIMEOUT: u16 = 4408;
    pub const SUPERSEDED: u16 = 4409;
    pub const UPGRADE_REQUIRED: u16 = 4426;
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct HelloBody {
    pub hello: HostHello,
    pub capacity: HostCapacity,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct WelcomeBody {
    pub host_id: Uuid,
    pub user_id: Uuid,
    pub protocol_version: u16,
    /// The longest this host may go without a `control` frame.
    pub heartbeat_ms: u64,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PairBody {
    pub pairing_code: String,
    pub display_name: String,
    pub hello: HostHello,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PairedBody {
    pub host_id: Uuid,
    pub user_id: Uuid,
    pub host_secret: String,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct ControlBody {
    pub capacity: HostCapacity,
    #[serde(default)]
    pub acknowledged_command_ids: Vec<Uuid>,
    #[serde(default)]
    pub checkpoints: Vec<RunCheckpoint>,
    #[serde(default)]
    pub rejections: Vec<CommandRejection>,
}

/// One control update Lemma could not parse. Everything else in the frame was
/// applied; an update Lemma merely found stale is ignored, not refused.
#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq, Eq)]
pub struct RefusedUpdate {
    /// `ack`, `checkpoint` or `rejection`.
    #[serde(default)]
    pub kind: String,
    #[serde(default)]
    pub run_id: Option<Uuid>,
    #[serde(default)]
    pub command_id: Option<Uuid>,
    #[serde(default)]
    pub reason: Option<String>,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct ControlOkBody {
    #[serde(default)]
    pub commands: Vec<Command>,
    #[serde(default)]
    pub refused: Vec<RefusedUpdate>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct EventsOkBody {
    pub ack: EventAck,
}

/// What Lemma recorded for one published harness: the id runs are dispatched
/// against, and the revision it accepted.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PublishedHarness {
    pub id: Uuid,
    pub harness_key: String,
    pub adapter_version: String,
    pub config_revision: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct HarnessesBody {
    pub harnesses: Vec<HarnessSnapshot>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct HarnessesOkBody {
    pub items: Vec<PublishedHarness>,
}

/// One of the agent's calls to Lemma's MCP tools, relayed.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct McpBody {
    pub run_id: Uuid,
    pub conversation_id: Uuid,
    /// The run's own credential; Lemma authorizes every call against it.
    pub token: String,
    /// `tools/list` or `tools/call`.
    pub method: String,
    #[serde(default)]
    pub params: Value,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct McpOkBody {
    pub result: Value,
}

/// Wait for the person to answer a parked `ask_user` or `request_approval`.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct InteractionWaitBody {
    pub run_id: Uuid,
    pub conversation_id: Uuid,
    pub token: String,
    pub tool_call_id: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct InteractionOkBody {
    pub answer: Value,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct CommandsBody {
    pub commands: Vec<Command>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ReconnectBody {
    pub after_ms: u64,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct ErrorBody {
    pub code: String,
    pub message: String,
    #[serde(default)]
    pub retryable: bool,
}

/// The event batch a host appends, carried as the `events` body as-is.
pub type EventsBody = EventBatch;

#[cfg(test)]
mod tests {
    use super::*;

    fn contract() -> Value {
        let raw = std::fs::read_to_string(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/tests/fixtures/wire_contract.json"
        ))
        .expect("the wire contract is readable");
        serde_json::from_str(&raw).expect("the wire contract parses")
    }

    fn names(value: &Value) -> Vec<String> {
        let mut names: Vec<String> = value
            .as_array()
            .expect("a list of frame names")
            .iter()
            .map(|name| name.as_str().expect("a frame name").to_owned())
            .collect();
        names.sort();
        names
    }

    /// The frame vocabulary exists twice, here and in the backend, and a frame
    /// one side sends that the other does not know is dropped on the floor.
    /// Both sides hold themselves to the same fixture.
    #[test]
    fn the_frame_vocabulary_matches_the_contract() {
        let contract = contract();
        let mut ours: Vec<String> = host::ALL.iter().map(|name| (*name).to_owned()).collect();
        ours.sort();
        assert_eq!(ours, names(&contract["link"]["host_frames"]));
        let mut ours: Vec<String> = server::ALL.iter().map(|name| (*name).to_owned()).collect();
        ours.sort();
        assert_eq!(ours, names(&contract["link"]["server_frames"]));
        assert_eq!(
            contract["link"]["path"].as_str(),
            Some(format!("/{LINK_PATH}").as_str())
        );
    }

    #[test]
    fn the_close_codes_match_the_contract() {
        let contract = contract();
        let codes = &contract["link"]["close_codes"];
        for (name, code) in [
            ("normal", close::NORMAL),
            ("restarting", close::RESTARTING),
            ("protocol_violation", close::PROTOCOL_VIOLATION),
            ("revoked_or_missing", close::REVOKED_OR_MISSING),
            ("invalid_credential", close::INVALID_CREDENTIAL),
            ("heartbeat_timeout", close::HEARTBEAT_TIMEOUT),
            ("superseded", close::SUPERSEDED),
            ("upgrade_required", close::UPGRADE_REQUIRED),
        ] {
            assert_eq!(codes[name].as_u64(), Some(u64::from(code)), "{name}");
        }
        assert_eq!(
            codes.as_object().map(serde_json::Map::len),
            Some(8),
            "a close code was added to the contract and not here"
        );
    }

    #[test]
    fn a_frame_omits_what_it_does_not_carry() {
        let frame = Frame {
            kind: server::COMMANDS.to_owned(),
            id: None,
            re: None,
            body: serde_json::json!({ "commands": [] }),
        };
        assert_eq!(
            serde_json::to_value(&frame).unwrap(),
            serde_json::json!({ "type": "commands", "body": { "commands": [] } })
        );
    }
}
