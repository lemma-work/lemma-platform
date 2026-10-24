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

use crate::host_exec::wire::HostExecutionStatus;
use crate::protocol::{
    Command, CommandRejection, EventAck, EventBatch, HarnessSnapshot, HostCapacity, HostHello,
    RunCheckpoint,
};

/// Where the link lives, under the workspace's API root.
pub const LINK_PATH: &str = "agent-host/link";

/// The largest websocket message Lemma's API reads (`WS_MAX_BYTES`). A frame
/// past it closes the link with 1009, and every run's frames with it, so
/// this host never sends one: see `LinkHandle::request`.
pub const MAX_MESSAGE_BYTES: usize = 1024 * 1024;

/// The most one `events` frame carries, encoded: half the message ceiling, so
/// a batch is never the frame that reaches it. See `Journal::pending_events`.
pub const EVENT_BATCH_MAX_BYTES: usize = 512 * 1024;

/// The most one event carries once its attachments are uploaded. A bigger one
/// has its longest strings cut, rather than the run's transcript lost.
pub const EVENT_MAX_BYTES: usize = EVENT_BATCH_MAX_BYTES - 4 * 1024;

/// The most one transfer carries: one `file.read` or `file.write` chunk.
pub const TRANSFER_MAX_BYTES: usize = 1024 * 1024;

/// The most one blob carries: the ceiling an event's image had inline.
pub const BLOB_MAX_BYTES: usize = 5 * 1024 * 1024;

/// The header a transfer's or blob's SHA-256 travels in, as lowercase hex.
pub const DIGEST_HEADER: &str = "x-lemma-sha256";

/// The key an event's content block names an uploaded blob under, in place
/// of its inline `data` (or a resource's `blob`).
pub const BLOB_FIELD: &str = "lemma_blob";

/// The key a part of a frame Lemma staged instead of sending is replaced by
/// -- a large tool result, a large command payload. See
/// `TransferClient::resolve_staged`.
pub const STAGED_FIELD: &str = "lemma_staged";

/// The most one staged body carries.
pub const STAGED_BODY_MAX_BYTES: usize = 32 * 1024 * 1024;

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
    /// The answer to Lemma's `op`.
    pub const OP_OK: &str = "op_ok";
    pub const ERROR: &str = "error";

    /// Every host frame, for the contract test.
    pub const ALL: [&str; 10] = [
        PAIR,
        HELLO,
        CONTROL,
        EVENTS,
        HARNESSES,
        MCP,
        INTERACTION_WAIT,
        REVOKE,
        OP_OK,
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
    /// A request Lemma makes of the host: one host-execution operation. The
    /// only server frame that carries an `id` and expects an answer.
    pub const OP: &str = "op";
    pub const ERROR: &str = "error";

    /// Every server frame, for the contract test.
    pub const ALL: [&str; 12] = [
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
        OP,
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
    /// Whether Lemma may route an owner's commands here. Also on every
    /// `control`, so turning it on or off takes effect on the next heartbeat
    /// rather than the next reconnect.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub host_execution: Option<HostExecutionStatus>,
    /// This host fetches a part of a frame Lemma staged rather than sent
    /// (`STAGED_FIELD`). `connect_with` sets it: the link it opens can.
    #[serde(default)]
    pub staged_bodies: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct WelcomeBody {
    pub host_id: Uuid,
    pub user_id: Uuid,
    pub protocol_version: u16,
    /// The longest this host may go without a `control` frame.
    pub heartbeat_ms: u64,
    /// Lemma's clock when it answered. Command expiries and run deadlines are
    /// Lemma's times, so the host judges them against this rather than a
    /// clock of its own that may be minutes out. Absent from an older Lemma.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub server_time: Option<chrono::DateTime<chrono::Utc>>,
    /// Lemma runs a `tools/call` once per `(run_id, request_id)` and answers
    /// a repeat with the result it kept. Only then does the host send a call
    /// again after the link dropped under it. Absent from an older Lemma.
    #[serde(default)]
    pub idempotent_tool_calls: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PairBody {
    pub pairing_code: String,
    pub display_name: String,
    pub hello: HostHello,
    /// The person asked for this pairing, in the app, knowing this computer
    /// was removed from their account. Without it Lemma refuses to pair an
    /// installation that was revoked, so removing a computer sticks against
    /// the automatic connection.
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub reenable: bool,
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
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub host_execution: Option<HostExecutionStatus>,
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
    /// One per `tools/call`, the same on every try of it. Lemma runs a call
    /// once per `(run_id, request_id)` and answers a repeat with the stored
    /// result, so a call can be sent again after a dropped link without
    /// running twice.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub request_id: Option<String>,
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
    /// More about the failure. An `OP_FAILED` answer to an `op` carries
    /// `{"kind": ...}`, one of `host_exec::wire::kind`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub detail: Option<Value>,
}

/// One host-execution operation Lemma asks of this host.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct OpBody {
    /// The sandbox's logical id; `workspace.open` maps it to a root folder.
    pub workspace: String,
    pub method: String,
    #[serde(default)]
    pub params: Value,
    /// How long Lemma will wait for the answer. Past it, the host answers
    /// `timeout` itself rather than let the op run on unobserved.
    #[serde(default)]
    pub deadline_ms: Option<u64>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct OpOkBody {
    pub result: Value,
}

/// The event batch a host appends, carried as the `events` body as-is.
pub type EventsBody = EventBatch;

/// The link's `compute` role: a machine joined with `lemma machine connect`.
///
/// This host never speaks it -- it is an Agent Host, and every frame it sends
/// is the default role. The vocabulary is declared here anyway, because this
/// file and the backend's `agent_host_link_compute.py` are the two copies the
/// wire contract holds together: a role, a body field or a mux number that one
/// side changes and the other does not would be found here, not in a machine
/// whose streams silently stall. The machine's own agent runs the backend's
/// mux module (`sandbox_runtime/machine_link/mux.py`). See
/// docs/architecture/connected-machines.md.
pub mod compute {
    use serde::{Deserialize, Serialize};
    use serde_json::Value;
    use uuid::Uuid;

    use crate::protocol::HostHello;

    /// The role a compute link names on its first frame.
    pub const ROLE: &str = "compute";
    /// The role a first frame without one is read as. This host's frames.
    pub const DEFAULT_ROLE: &str = "agent_host";
    /// The protocol the role arrived in.
    pub const MIN_PROTOCOL_VERSION: u16 = 4;

    /// `host`: the runtime runs as the person's OS user under `--root`.
    /// `image`: it runs in the `lemma-workspace` image under Docker.
    pub const MODES: [&str; 2] = ["host", "image"];
    /// What a mux `OPEN` may ask a machine for.
    pub const STREAM_TARGETS: [&str; 2] = ["port", "runtime_bundle"];

    #[derive(Clone, Debug, Serialize, Deserialize)]
    pub struct PairBody {
        pub role: String,
        pub pairing_code: String,
        /// The secret half of the pairing; only the machine that asked for the
        /// code holds it.
        pub claim_key: String,
        pub hello: HostHello,
    }

    #[derive(Clone, Debug, Serialize, Deserialize)]
    pub struct PairedBody {
        pub connection_id: Uuid,
        pub user_id: Uuid,
        pub name: String,
        pub machine_secret: String,
    }

    #[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq, Eq)]
    pub struct Capabilities {
        pub processes: bool,
        pub files: bool,
        pub python: bool,
        pub browser: bool,
        pub docker: bool,
        pub previews: bool,
    }

    #[derive(Clone, Debug, Serialize, Deserialize)]
    pub struct MachineReport {
        pub mode: String,
        pub platform: String,
        pub home: String,
        #[serde(default)]
        pub runtime_version: Option<String>,
        #[serde(default)]
        pub runtime_ready: bool,
        pub runtime_port: u16,
        #[serde(default)]
        pub relay_port: Option<u16>,
        #[serde(default)]
        pub capabilities: Capabilities,
    }

    #[derive(Clone, Debug, Serialize, Deserialize)]
    pub struct HelloBody {
        pub role: String,
        pub hello: HostHello,
        pub machine: MachineReport,
    }

    #[derive(Clone, Debug, Serialize, Deserialize)]
    pub struct RuntimeGrant {
        pub token: String,
        #[serde(default)]
        pub version: Option<String>,
    }

    #[derive(Clone, Debug, Serialize, Deserialize)]
    pub struct WelcomeBody {
        pub connection_id: Uuid,
        pub user_id: Uuid,
        pub name: String,
        pub protocol_version: u16,
        pub heartbeat_ms: u64,
        #[serde(default)]
        pub server_time: Option<Value>,
        pub runtime: RuntimeGrant,
    }

    /// The binary frames a compute link's streams travel in: one WebSocket
    /// binary message each, `type (u8) | stream id (u32 BE) | payload`.
    pub mod mux {
        pub const OPEN: u8 = 1;
        pub const ACCEPT: u8 = 2;
        pub const DATA: u8 = 3;
        pub const WINDOW: u8 = 4;
        pub const FIN: u8 = 5;
        pub const RESET: u8 = 6;
        pub const HEADER_BYTES: usize = 5;
        pub const MAX_DATA_BYTES: usize = 64 * 1024;
        pub const DEFAULT_WINDOW_BYTES: usize = 256 * 1024;
        pub const MAX_WINDOW_BYTES: usize = 16 * 1024 * 1024;
        pub const MAX_STREAMS: usize = 512;
        pub const MAX_CONTROL_BYTES: usize = 8 * 1024;
        pub const RESET_CODES: [&str; 7] = [
            "refused",
            "cancelled",
            "flow_control",
            "protocol",
            "link_lost",
            "too_many_streams",
            "internal",
        ];

        /// One frame's bytes.
        pub fn encode(kind: u8, stream: u32, payload: &[u8]) -> Vec<u8> {
            let mut frame = Vec::with_capacity(HEADER_BYTES + payload.len());
            frame.push(kind);
            frame.extend_from_slice(&stream.to_be_bytes());
            frame.extend_from_slice(payload);
            frame
        }

        /// A frame's type, stream and payload, or `None` if it is too short.
        pub fn decode(frame: &[u8]) -> Option<(u8, u32, &[u8])> {
            if frame.len() < HEADER_BYTES {
                return None;
            }
            let stream = u32::from_be_bytes([frame[1], frame[2], frame[3], frame[4]]);
            Some((frame[0], stream, &frame[HEADER_BYTES..]))
        }
    }
}

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

    /// Both sides bound what rides the link, and agree on where the rest goes.
    #[test]
    fn the_bulk_byte_bounds_and_routes_match_the_contract() {
        let contract = contract();
        let bulk = &contract["bulk_bytes"];
        assert_eq!(bulk["max_message_bytes"], MAX_MESSAGE_BYTES);
        assert_eq!(bulk["event_batch_max_bytes"], EVENT_BATCH_MAX_BYTES);
        assert_eq!(
            bulk["inline_data_bytes"],
            crate::host_exec::wire::INLINE_DATA_BYTES
        );
        assert_eq!(bulk["transfer_max_bytes"], TRANSFER_MAX_BYTES);
        assert_eq!(
            bulk["transfer_max_bytes"],
            crate::host_exec::wire::OP_MAX_DATA_BYTES
        );
        assert_eq!(bulk["blob_max_bytes"], BLOB_MAX_BYTES);
        assert_eq!(bulk["digest_header"], DIGEST_HEADER);
        assert_eq!(bulk["blob_field"], BLOB_FIELD);
        assert_eq!(bulk["staged_field"], STAGED_FIELD);
        assert_eq!(bulk["staged_body_max_bytes"], STAGED_BODY_MAX_BYTES);
        assert_eq!(bulk["hello_flag"], "staged_bodies");
        let hello = serde_json::to_value(HelloBody {
            hello: crate::protocol::HostHello::current("i"),
            capacity: crate::protocol::HostCapacity::default(),
            host_execution: None,
            staged_bodies: true,
        })
        .unwrap();
        assert_eq!(hello["staged_bodies"], true);
        assert_eq!(bulk["transfer_path"], "/agent-host/transfers/{transfer_id}");
        assert_eq!(
            bulk["blob_path"],
            "/agent-host/runs/{run_id}/blobs/{sha256}"
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

    fn field_names(value: &Value) -> Vec<String> {
        let mut names: Vec<String> = value
            .as_object()
            .expect("a serialized body")
            .keys()
            .cloned()
            .collect();
        names.sort();
        names
    }

    /// The compute role is not this host's, but its vocabulary is held to the
    /// same fixture as the backend's copy of it.
    #[test]
    fn the_compute_role_matches_the_contract() {
        let contract = contract();
        let role = &contract["compute"];
        assert_eq!(role["role"], compute::ROLE);
        assert_eq!(role["default_role"], compute::DEFAULT_ROLE);
        assert_eq!(
            role["min_protocol_version"].as_u64(),
            Some(u64::from(compute::MIN_PROTOCOL_VERSION))
        );
        assert_eq!(contract["min_protocol_version"].as_u64(), Some(3));
        assert_eq!(names(&role["modes"]), {
            let mut modes: Vec<String> = compute::MODES.iter().map(|m| (*m).to_owned()).collect();
            modes.sort();
            modes
        });
        assert_eq!(names(&role["stream_targets"]), {
            let mut targets: Vec<String> = compute::STREAM_TARGETS
                .iter()
                .map(|t| (*t).to_owned())
                .collect();
            targets.sort();
            targets
        });
        let hello = crate::protocol::HostHello::current("i");
        let report = compute::MachineReport {
            mode: "host".into(),
            platform: "linux".into(),
            home: "/home/me/lemma-machine".into(),
            runtime_version: None,
            runtime_ready: false,
            runtime_port: 8080,
            relay_port: None,
            capabilities: compute::Capabilities::default(),
        };
        let bodies = [
            (
                "pair_fields",
                serde_json::to_value(compute::PairBody {
                    role: compute::ROLE.into(),
                    pairing_code: "ABCD-EFGH".into(),
                    claim_key: "k".repeat(43),
                    hello: hello.clone(),
                })
                .unwrap(),
            ),
            (
                "paired_fields",
                serde_json::to_value(compute::PairedBody {
                    connection_id: Uuid::nil(),
                    user_id: Uuid::nil(),
                    name: "laptop".into(),
                    machine_secret: "s".into(),
                })
                .unwrap(),
            ),
            (
                "hello_fields",
                serde_json::to_value(compute::HelloBody {
                    role: compute::ROLE.into(),
                    hello,
                    machine: report.clone(),
                })
                .unwrap(),
            ),
            ("machine_fields", serde_json::to_value(&report).unwrap()),
            (
                "capabilities",
                serde_json::to_value(compute::Capabilities::default()).unwrap(),
            ),
            (
                "welcome_fields",
                serde_json::to_value(compute::WelcomeBody {
                    connection_id: Uuid::nil(),
                    user_id: Uuid::nil(),
                    name: "laptop".into(),
                    protocol_version: crate::PROTOCOL_VERSION,
                    heartbeat_ms: 20_000,
                    server_time: Some(Value::Null),
                    runtime: compute::RuntimeGrant {
                        token: "t".into(),
                        version: None,
                    },
                })
                .unwrap(),
            ),
            (
                "runtime_fields",
                serde_json::to_value(compute::RuntimeGrant {
                    token: "t".into(),
                    version: None,
                })
                .unwrap(),
            ),
        ];
        for (key, body) in bodies {
            assert_eq!(field_names(&body), names(&role[key]), "{key}");
        }
    }

    #[test]
    fn the_mux_frames_and_bounds_match_the_contract() {
        use compute::mux;
        let contract = contract();
        let declared = &contract["compute"]["mux"];
        let types = &declared["frame_types"];
        for (name, number) in [
            ("open", mux::OPEN),
            ("accept", mux::ACCEPT),
            ("data", mux::DATA),
            ("window", mux::WINDOW),
            ("fin", mux::FIN),
            ("reset", mux::RESET),
        ] {
            assert_eq!(types[name].as_u64(), Some(u64::from(number)), "{name}");
        }
        assert_eq!(types.as_object().map(serde_json::Map::len), Some(6));
        assert_eq!(declared["max_data_bytes"], mux::MAX_DATA_BYTES);
        assert_eq!(declared["default_window_bytes"], mux::DEFAULT_WINDOW_BYTES);
        assert_eq!(declared["max_window_bytes"], mux::MAX_WINDOW_BYTES);
        assert_eq!(declared["max_streams"], mux::MAX_STREAMS);
        assert_eq!(declared["max_control_bytes"], mux::MAX_CONTROL_BYTES);
        let mut codes: Vec<String> = mux::RESET_CODES.iter().map(|c| (*c).to_owned()).collect();
        codes.sort();
        assert_eq!(codes, names(&declared["reset_codes"]));
    }

    #[test]
    fn a_mux_frame_round_trips_its_header() {
        use compute::mux;
        let frame = mux::encode(mux::DATA, 0x0102_0304, b"bytes");
        assert_eq!(&frame[..5], &[3, 1, 2, 3, 4]);
        assert_eq!(
            mux::decode(&frame),
            Some((mux::DATA, 0x0102_0304, &b"bytes"[..]))
        );
        assert_eq!(mux::decode(&[1, 2]), None);
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
