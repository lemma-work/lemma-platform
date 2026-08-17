//! Stand-ins for the two things an Agent Host talks to: Lemma's control plane
//! and Lemma's run-scoped MCP endpoint.
//!
//! Both are deliberately faithful to the real contracts rather than convenient:
//! the MCP endpoint answers the same stateless JSON-RPC-over-HTTP shape that
//! `app/mcp_server.py` mounts at `/agent-runtime/conversations/{id}/mcp`, and
//! the control plane speaks the same pairing / publish / poll / append-events
//! endpoints as `app/modules/agent`'s Agent Host API. A test that passes here
//! has exercised the host's real code paths end to end; what it has *not*
//! proven is that Lemma's own implementations of those contracts are correct.

#![allow(dead_code)]

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use axum::extract::{Path as AxumPath, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{post, put};
use axum::{Json, Router};
use chrono::Utc;
use lemma_agent_host::protocol::{Event, EventBatch, EventType, JsonMap, RunSpec};
use serde_json::{Value, json};
use tokio::net::TcpListener;
use uuid::Uuid;

pub const HOST_SECRET: &str = "hermetic-agent-host-secret-with-entropy";
pub const MCP_BEARER: &str = "hermetic-run-scoped-mcp-token";
pub const ECHO_TOOL: &str = "lemma_echo";

// ---------------------------------------------------------------------------
// Locating Python for the scripted ACP agent.
// ---------------------------------------------------------------------------

#[must_use]
pub fn python() -> PathBuf {
    let executable_names = if cfg!(windows) {
        &["python.exe", "python3.exe"][..]
    } else {
        &["python3", "python"][..]
    };
    std::env::split_paths(&std::env::var_os("PATH").unwrap_or_default())
        .find_map(|path| {
            executable_names
                .iter()
                .map(|name| path.join(name))
                .find(|candidate| candidate.is_file())
        })
        .expect("Python is required for the Agent Host integration suite")
}

#[must_use]
pub fn scripted_agent_fixture() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests")
        .join("fixtures")
        .join("scripted_acp_agent.py")
}

// ---------------------------------------------------------------------------
// A shim that makes the pinned manifest resolve to the scripted ACP agent.
// ---------------------------------------------------------------------------

/// A directory that `LEMMA_AGENT_HOST_PATH` can point at so the host's *own*
/// adapter resolution picks the scripted agent.
///
/// The alternative — constructing a `ResolvedAdapter` by hand — skips
/// `HostRuntime`'s discovery, probe, publish, and MCP-bridge wiring, which is
/// most of what these tests exist to cover. Both native adapters are shimmed so
/// a developer machine with a real `opencode` on `PATH` cannot be probed by
/// accident.
pub struct ShimmedAgents {
    pub directory: PathBuf,
    pub acp_log: PathBuf,
    /// The manifest key the shim now resolves to.
    pub harness_key: String,
}

impl ShimmedAgents {
    /// `mode` is passed through to `scripted_acp_agent.py`.
    ///
    /// # Panics
    /// If the shim cannot be written or made executable.
    #[must_use]
    pub fn install(root: &Path, mode: &str) -> Self {
        let directory = root.join("shim-bin");
        std::fs::create_dir_all(&directory).unwrap();
        let acp_log = root.join(format!("acp-{mode}.jsonl"));
        let script = format!(
            "#!/bin/sh\n\
             case \"$1\" in\n\
             --version) echo '2026.7.31' ;;\n\
             *) exec {python} {fixture} {log} {mode} ;;\n\
             esac\n",
            python = shell_quote(&python()),
            fixture = shell_quote(&scripted_agent_fixture()),
            log = shell_quote(&acp_log),
            mode = mode,
        );
        // `cursor` is the harness under test; `opencode` is shimmed only so the
        // host's discovery never launches a real local agent.
        for name in ["cursor-agent", "opencode"] {
            let path = directory.join(name);
            std::fs::write(&path, &script).unwrap();
            make_executable(&path);
        }
        Self {
            directory,
            acp_log,
            harness_key: "cursor".to_owned(),
        }
    }

    /// Every ACP/MCP message the scripted agent saw or sent.
    ///
    /// # Panics
    /// If the log exists but is not valid JSONL.
    #[must_use]
    pub fn traffic(&self) -> Vec<Value> {
        std::fs::read_to_string(&self.acp_log)
            .unwrap_or_default()
            .lines()
            .filter(|line| !line.trim().is_empty())
            .map(|line| serde_json::from_str(line).unwrap())
            .collect()
    }
}

#[cfg(unix)]
fn make_executable(path: &Path) {
    use std::os::unix::fs::PermissionsExt;
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o755)).unwrap();
}

#[cfg(not(unix))]
fn make_executable(_path: &Path) {}

fn shell_quote(path: &Path) -> String {
    format!("'{}'", path.to_string_lossy().replace('\'', r"'\''"))
}

// ---------------------------------------------------------------------------
// Stand-in Lemma MCP endpoint.
// ---------------------------------------------------------------------------

#[derive(Clone, Debug)]
pub struct McpRequestRecord {
    pub conversation_id: String,
    pub method: String,
    pub params: Value,
    pub authorization: Option<String>,
    pub agent_run_id: Option<String>,
    pub protocol_version: Option<String>,
    pub session_id: Option<String>,
    pub accept: Option<String>,
}

#[derive(Clone)]
pub struct LemmaMcpEndpoint {
    pub url: String,
    pub conversation_id: Uuid,
    requests: Arc<Mutex<Vec<McpRequestRecord>>>,
    deletes: Arc<Mutex<Vec<Option<String>>>>,
    transport: McpTransport,
    accepted: Arc<Mutex<Vec<String>>>,
    scripted: Arc<Mutex<Vec<ScriptedFailure>>>,
}

/// A failure to serve instead of the next real answer.
///
/// The endpoint could not fail at all before this: it answered 200, 202, or
/// 401 and nothing else, so the bridge's own behaviour on a restarting backend
/// -- the case that took every Lemma tool away from a running agent -- had no
/// way to be exercised.
#[derive(Clone, Debug)]
pub enum ScriptedFailure {
    /// Answer with this HTTP status and no useful body.
    Status(StatusCode),
    /// Answer 200 with a JSON-RPC error, the shape a dead run token really
    /// takes: Lemma authorizes inside the handler, so the refusal never
    /// reaches the status line.
    Unauthorized,
}

/// Which of the two wire shapes the bridge must cope with. Lemma mounts
/// `FastMCP` with `json_response=True, stateless_http=True`, but the same bridge is the
/// only client for any future streaming deployment, so both are covered.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum McpTransport {
    /// `application/json` bodies and no `mcp-session-id`, i.e. what Lemma mounts.
    StatelessJson,
    /// `text/event-stream` bodies plus a session id the client must echo back.
    ServerSentEvents,
}

#[derive(Clone)]
struct McpState {
    requests: Arc<Mutex<Vec<McpRequestRecord>>>,
    deletes: Arc<Mutex<Vec<Option<String>>>>,
    transport: McpTransport,
    /// Bearers this endpoint will serve. More than one because a run's
    /// credential is rotated in flight, and a real Lemma accepts the
    /// replacement it just issued alongside the one still in use.
    accepted: Arc<Mutex<Vec<String>>>,
    scripted: Arc<Mutex<Vec<ScriptedFailure>>>,
}

impl LemmaMcpEndpoint {
    /// # Panics
    /// If the listener cannot bind.
    pub async fn start(transport: McpTransport) -> Self {
        let requests = Arc::new(Mutex::new(Vec::new()));
        let deletes = Arc::new(Mutex::new(Vec::new()));
        let accepted = Arc::new(Mutex::new(vec![format!("Bearer {MCP_BEARER}")]));
        let scripted = Arc::new(Mutex::new(Vec::new()));
        let state = McpState {
            requests: Arc::clone(&requests),
            deletes: Arc::clone(&deletes),
            transport,
            accepted: Arc::clone(&accepted),
            scripted: Arc::clone(&scripted),
        };
        let app = Router::new()
            .route(
                "/agent-runtime/conversations/{conversation_id}/mcp",
                post(mcp_post).delete(mcp_delete),
            )
            .with_state(state);
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        tokio::spawn(async move {
            let _ = axum::serve(listener, app).await;
        });
        let conversation_id = Uuid::new_v4();
        Self {
            url: format!("http://{address}/agent-runtime/conversations/{conversation_id}/mcp"),
            conversation_id,
            requests,
            deletes,
            transport,
            accepted,
            scripted,
        }
    }

    /// Fail the next calls in order, then serve normally again.
    ///
    /// # Panics
    /// If the mutex is poisoned.
    pub fn fail_next(&self, failures: impl IntoIterator<Item = ScriptedFailure>) {
        self.scripted.lock().unwrap().extend(failures);
    }

    /// Also serve `authorization`, as Lemma does for a credential it has just
    /// re-issued for a run that is still in flight.
    ///
    /// # Panics
    /// If the mutex is poisoned.
    pub fn also_accept(&self, authorization: &str) {
        self.accepted.lock().unwrap().push(authorization.to_owned());
    }

    /// The `mcp` object Lemma puts in the encrypted `START_RUN` payload.
    #[must_use]
    pub fn run_configuration(&self) -> Value {
        json!({
            "server_name": "lemma_tools",
            "url": self.url,
            "authorization": format!("Bearer {MCP_BEARER}"),
            "token": MCP_BEARER,
            "conversation_id": self.conversation_id,
        })
    }

    /// # Panics
    /// If the recording mutex is poisoned.
    #[must_use]
    pub fn requests(&self) -> Vec<McpRequestRecord> {
        self.requests.lock().unwrap().clone()
    }

    /// # Panics
    /// If the recording mutex is poisoned.
    #[must_use]
    pub fn methods(&self) -> Vec<String> {
        self.requests()
            .into_iter()
            .map(|record| record.method)
            .collect()
    }

    /// # Panics
    /// If the recording mutex is poisoned.
    #[must_use]
    pub fn deletes(&self) -> Vec<Option<String>> {
        self.deletes.lock().unwrap().clone()
    }
}

fn header(headers: &HeaderMap, name: &str) -> Option<String> {
    headers
        .get(name)
        .and_then(|value| value.to_str().ok())
        .map(str::to_owned)
}

async fn mcp_post(
    State(state): State<McpState>,
    AxumPath(conversation_id): AxumPath<String>,
    headers: HeaderMap,
    Json(request): Json<Value>,
) -> Response {
    let method = request
        .get("method")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    state.requests.lock().unwrap().push(McpRequestRecord {
        conversation_id,
        method: method.clone(),
        params: request.get("params").cloned().unwrap_or(Value::Null),
        authorization: header(&headers, "authorization"),
        agent_run_id: header(&headers, "x-lemma-agent-run-id"),
        protocol_version: header(&headers, "mcp-protocol-version"),
        session_id: header(&headers, "mcp-session-id"),
        accept: header(&headers, "accept"),
    });
    let scripted = {
        let mut scripted = state.scripted.lock().unwrap();
        if scripted.is_empty() {
            None
        } else {
            Some(scripted.remove(0))
        }
    };
    match scripted {
        Some(ScriptedFailure::Status(status)) => {
            return (status, "scripted failure").into_response();
        }
        Some(ScriptedFailure::Unauthorized) => {
            return Json(json!({
                "jsonrpc": "2.0",
                "id": request.get("id").cloned().unwrap_or(Value::Null),
                "error": {"code": -32603, "message": "Unauthorized MCP token"},
            }))
            .into_response();
        }
        None => {}
    }
    let presented = header(&headers, "authorization");
    if !state
        .accepted
        .lock()
        .unwrap()
        .iter()
        .any(|allowed| Some(allowed.as_str()) == presented.as_deref())
    {
        return (StatusCode::UNAUTHORIZED, "bad token").into_response();
    }
    let Some(id) = request.get("id").cloned() else {
        // Notifications (`notifications/initialized`) have no id; FastMCP
        // answers 202 with no body and the bridge must not forward anything.
        return StatusCode::ACCEPTED.into_response();
    };
    let body = match method.as_str() {
        "initialize" => json!({
            "jsonrpc": "2.0",
            "id": id,
            "result": {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {"listChanged": false}},
                "serverInfo": {"name": "lemma_tools", "version": "1.0.0"},
                "instructions": "Lemma tools for the current conversation.",
            },
        }),
        "tools/list" => json!({
            "jsonrpc": "2.0",
            "id": id,
            "result": {"tools": [{
                "name": ECHO_TOOL,
                "description": "Echo text back through Lemma.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
                "_meta": {"lemma_tool_name": "echo"},
            }]},
        }),
        "tools/call" => {
            let name = request
                .pointer("/params/name")
                .and_then(Value::as_str)
                .unwrap_or_default();
            let text = request
                .pointer("/params/arguments/text")
                .and_then(Value::as_str)
                .unwrap_or_default();
            if name == ECHO_TOOL {
                json!({
                    "jsonrpc": "2.0",
                    "id": id,
                    "result": {
                        "content": [{"type": "text", "text": format!("lemma-echo:{text}")}],
                        "structuredContent": {"echoed": text},
                        "isError": false,
                    },
                })
            } else {
                json!({
                    "jsonrpc": "2.0",
                    "id": id,
                    "result": {
                        "content": [{"type": "text", "text": format!("unknown tool {name}")}],
                        "isError": true,
                    },
                })
            }
        }
        other => json!({
            "jsonrpc": "2.0",
            "id": id,
            "error": {"code": -32601, "message": format!("unsupported method {other}")},
        }),
    };
    match state.transport {
        McpTransport::StatelessJson => (
            StatusCode::OK,
            [("content-type", "application/json")],
            serde_json::to_string(&body).unwrap(),
        )
            .into_response(),
        McpTransport::ServerSentEvents => (
            StatusCode::OK,
            [
                ("content-type", "text/event-stream"),
                ("mcp-session-id", "hermetic-mcp-session"),
            ],
            format!("event: message\ndata: {body}\n\n"),
        )
            .into_response(),
    }
}

async fn mcp_delete(
    State(state): State<McpState>,
    AxumPath(_conversation_id): AxumPath<String>,
    headers: HeaderMap,
) -> StatusCode {
    state
        .deletes
        .lock()
        .unwrap()
        .push(header(&headers, "mcp-session-id"));
    StatusCode::NO_CONTENT
}

// ---------------------------------------------------------------------------
// Stand-in Lemma control plane.
// ---------------------------------------------------------------------------

/// What the control plane should answer when the host reports a parked
/// permission request.
#[derive(Clone, Debug)]
pub enum PermissionAnswer {
    /// Send `RESOLVE_PERMISSION` selecting this option id.
    Allow(String),
    /// Select whichever option the agent labelled `allow_once`. Real adapters
    /// name their options differently, so a test that drives one cannot pin an
    /// id the way the scripted agent can.
    AllowOnce,
    /// Approve the first request and deny every later one.
    ///
    /// Two concurrent requests answered *differently* is the only way to show
    /// that each was resolved by its own id: one shared key cannot deliver two
    /// different outcomes.
    AllowThenDeny,
    /// Send `RESOLVE_PERMISSION` with no option id, i.e. a denial.
    Deny,
    /// Never answer, so the host's own timeout is the only way out.
    Ignore,
}

impl PermissionAnswer {
    /// The option id to send for the `index`-th request answered this run.
    fn option_for(&self, index: usize, payload: &JsonMap) -> Option<String> {
        match self {
            Self::Allow(option_id) => Some(option_id.clone()),
            Self::AllowThenDeny if index == 0 => Self::AllowOnce.option_for(index, payload),
            Self::AllowOnce => payload
                .get("options")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .find(|option| {
                    option
                        .get("kind")
                        .and_then(Value::as_str)
                        .is_some_and(|kind| kind.replace('_', "").eq_ignore_ascii_case("allowonce"))
                })
                .and_then(|option| option.get("optionId").and_then(Value::as_str))
                .map(str::to_owned),
            Self::AllowThenDeny | Self::Deny | Self::Ignore => None,
        }
    }
}

/// One decision Lemma sent, and the run's visible state at that moment.
#[derive(Clone, Debug)]
pub struct DecisionSnapshot {
    /// The request this decision named.
    pub request_id: String,
    /// The option selected, or `None` for a denial.
    pub option_id: Option<String>,
    /// Assistant text streamed so far.
    pub assistant_text: String,
    /// Whether the run had already ended.
    pub saw_terminal: bool,
}

#[derive(Clone)]
pub struct ControlPlane {
    pub base_url: url::Url,
    pub host_id: Uuid,
    pub user_id: Uuid,
    pub run_id: Uuid,
    state: ControlState,
}

#[derive(Clone)]
struct ControlState {
    host_id: Uuid,
    user_id: Uuid,
    run_id: Uuid,
    harness_key: String,
    prompt: String,
    mcp: Value,
    permission_answer: PermissionAnswer,
    published: Arc<Mutex<Option<(Uuid, String)>>>,
    start_sent: Arc<AtomicBool>,
    /// Streamed text that, once seen, makes the next poll cancel the run.
    ///
    /// Keyed on the agent's own output so the cancel lands mid-turn, while the
    /// adapter is genuinely working, rather than racing the run's start.
    cancel_after: Arc<Mutex<Option<String>>>,
    cancel_sent: Arc<AtomicBool>,
    /// Streamed text that, once seen, makes the next poll hand the run a
    /// replacement Lemma MCP configuration.
    refresh_after: Arc<Mutex<Option<(String, Value)>>>,
    refresh_sent: Arc<AtomicBool>,
    /// Request ids already answered, so a decision is sent exactly once.
    answered: Arc<Mutex<std::collections::HashSet<String>>>,
    /// The run as it stood the instant each decision was sent.
    ///
    /// A denial is the same observable outcome as a host that never asked
    /// anyone, which makes "the agent was denied" worthless on its own. A host
    /// that answered its adapter by itself will already have streamed the
    /// outcome, and usually finished the whole run, before Lemma decided.
    decisions: Arc<Mutex<Vec<DecisionSnapshot>>>,
    events: Arc<Mutex<Vec<Event>>>,
    snapshots: Arc<Mutex<Vec<Value>>>,
}

impl ControlPlane {
    /// # Panics
    /// If the listener cannot bind.
    pub async fn start(
        harness_key: &str,
        prompt: &str,
        mcp: Value,
        permission_answer: PermissionAnswer,
    ) -> Self {
        let state = ControlState {
            host_id: Uuid::new_v4(),
            user_id: Uuid::new_v4(),
            run_id: Uuid::new_v4(),
            harness_key: harness_key.to_owned(),
            prompt: prompt.to_owned(),
            mcp,
            permission_answer,
            published: Arc::new(Mutex::new(None)),
            start_sent: Arc::new(AtomicBool::new(false)),
            cancel_after: Arc::new(Mutex::new(None)),
            cancel_sent: Arc::new(AtomicBool::new(false)),
            refresh_after: Arc::new(Mutex::new(None)),
            refresh_sent: Arc::new(AtomicBool::new(false)),
            answered: Arc::new(Mutex::new(std::collections::HashSet::new())),
            decisions: Arc::new(Mutex::new(Vec::new())),
            events: Arc::new(Mutex::new(Vec::new())),
            snapshots: Arc::new(Mutex::new(Vec::new())),
        };
        let app = Router::new()
            .route("/agent-host/pairings:complete", post(pairing))
            .route("/agent-host/harnesses", put(publish))
            .route("/agent-host/poll", post(poll))
            .route("/agent-host/events:append", post(append_events))
            .with_state(state.clone());
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        tokio::spawn(async move {
            let _ = axum::serve(listener, app).await;
        });
        Self {
            base_url: url::Url::parse(&format!("http://{address}/")).unwrap(),
            host_id: state.host_id,
            user_id: state.user_id,
            run_id: state.run_id,
            state,
        }
    }

    /// Cancel the run on the first poll after `marker` appears in its output.
    ///
    /// # Panics
    /// If the mutex is poisoned.
    pub fn cancel_when_text_contains(&self, marker: &str) {
        *self.state.cancel_after.lock().unwrap() = Some(marker.to_owned());
    }

    /// Replace the run's Lemma MCP configuration once `marker` is streamed.
    ///
    /// # Panics
    /// If the mutex is poisoned.
    pub fn refresh_credential_when_text_contains(&self, marker: &str, mcp: Value) {
        *self.state.refresh_after.lock().unwrap() = Some((marker.to_owned(), mcp));
    }

    /// # Panics
    /// If the event mutex is poisoned.
    #[must_use]
    pub fn events(&self) -> Vec<Event> {
        self.state.events.lock().unwrap().clone()
    }

    /// # Panics
    /// If the snapshot mutex is poisoned.
    #[must_use]
    pub fn published_snapshots(&self) -> Vec<Value> {
        self.state.snapshots.lock().unwrap().clone()
    }

    #[must_use]
    pub fn saw_terminal(&self) -> bool {
        self.events()
            .iter()
            .any(|event| event.event_type == EventType::Terminal)
    }

    /// The streamed assistant text the host reported.
    ///
    /// # Panics
    /// If the event mutex is poisoned.
    #[must_use]
    pub fn assistant_text(&self) -> String {
        assistant_text_of(&self.state.events.lock().unwrap())
    }

    /// Where the run stood as each permission decision was sent.
    ///
    /// Empty until a decision is sent. This is what tells a denial Lemma made
    /// apart from a host that denies everything on its own: in the latter case
    /// the agent has already been answered, and normally the run has already
    /// finished, before Lemma gets a word in.
    ///
    /// # Panics
    /// If the recording mutex is poisoned.
    #[must_use]
    pub fn decisions(&self) -> Vec<DecisionSnapshot> {
        self.state.decisions.lock().unwrap().clone()
    }

    #[must_use]
    pub fn permission_requests(&self) -> Vec<Event> {
        self.events()
            .into_iter()
            .filter(|event| event.event_type == EventType::PermissionRequest)
            .collect()
    }

    /// Poll until `predicate` holds, panicking with the collected state on
    /// timeout so a failure says what the host actually did.
    ///
    /// # Panics
    /// On timeout.
    pub async fn wait_for(&self, what: &str, timeout: Duration, predicate: impl Fn(&Self) -> bool) {
        let deadline = tokio::time::Instant::now() + timeout;
        while tokio::time::Instant::now() < deadline {
            if predicate(self) {
                return;
            }
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
        let kinds = self
            .events()
            .iter()
            .map(|event| format!("{:?}", event.event_type))
            .collect::<Vec<_>>();
        let published = self.state.published.lock().unwrap().clone();
        panic!(
            "timed out waiting for {what}; published={published:?}, \
             start_sent={}, events={kinds:?}",
            self.state.start_sent.load(Ordering::SeqCst),
        );
    }
}

fn assistant_text_of(events: &[Event]) -> String {
    events
        .iter()
        .filter(|event| event.event_type == EventType::AgentMessageChunk)
        .filter_map(|event| event.payload.get("text").and_then(Value::as_str))
        .collect()
}

fn require_auth(headers: &HeaderMap) -> Result<(), StatusCode> {
    (header(headers, "authorization").as_deref() == Some(&format!("Bearer {HOST_SECRET}")))
        .then_some(())
        .ok_or(StatusCode::UNAUTHORIZED)
}

async fn pairing(State(state): State<ControlState>, Json(_body): Json<Value>) -> Json<Value> {
    Json(json!({
        "host_id": state.host_id,
        "user_id": state.user_id,
        "organization_id": null,
        "host_secret": HOST_SECRET,
    }))
}

async fn publish(
    State(state): State<ControlState>,
    headers: HeaderMap,
    Json(body): Json<Value>,
) -> Result<Json<Value>, StatusCode> {
    require_auth(&headers)?;
    let snapshots = body["harnesses"].as_array().cloned().unwrap_or_default();
    state.snapshots.lock().unwrap().clone_from(&snapshots);
    let items = snapshots
        .iter()
        .map(|snapshot| {
            let id = Uuid::new_v4();
            if snapshot["harness_key"].as_str() == Some(state.harness_key.as_str())
                && snapshot["health"].as_str() == Some("READY")
            {
                *state.published.lock().unwrap() = Some((
                    id,
                    snapshot["config_revision"]
                        .as_str()
                        .unwrap_or("")
                        .to_owned(),
                ));
            }
            json!({
                "id": id,
                "harness_key": snapshot["harness_key"],
                "adapter_version": snapshot["adapter_version"],
                "config_revision": snapshot["config_revision"],
            })
        })
        .collect::<Vec<_>>();
    Ok(Json(json!({"items": items})))
}

async fn poll(
    State(state): State<ControlState>,
    headers: HeaderMap,
) -> Result<Json<Value>, StatusCode> {
    require_auth(&headers)?;
    let mut commands = Vec::new();
    let published = state.published.lock().unwrap().clone();
    if let Some((harness_id, revision)) = published
        && !state.start_sent.swap(true, Ordering::SeqCst)
    {
        let payload = serde_json::to_value(RunSpec {
            agent_run_id: state.run_id,
            conversation_id: Uuid::new_v4(),
            harness_id,
            profile_revision: revision,
            model_name: None,
            config_selections: JsonMap::new(),
            system_prompt: "Follow the runtime instructions exactly.".to_owned(),
            prompt: vec![json!({"type": "text", "text": state.prompt})],
            resume_session_id: None,
            context: BTreeMap::new(),
            mcp: state.mcp.clone(),
            run_deadline: Utc::now() + chrono::Duration::minutes(3),
            system_prompt_delivery: None,
        })
        .unwrap();
        commands.push(json!({
            "command_id": Uuid::new_v4(),
            "kind": "START_RUN",
            "created_at": Utc::now(),
            "expires_at": Utc::now() + chrono::Duration::minutes(2),
            "run_id": state.run_id,
            "lease_epoch": 1,
            "payload": payload,
        }));
    }
    let cancel_after = state.cancel_after.lock().unwrap().clone();
    if let Some(marker) = cancel_after {
        let seen = assistant_text_of(&state.events.lock().unwrap()).contains(&marker);
        if seen && !state.cancel_sent.swap(true, Ordering::SeqCst) {
            commands.push(json!({
                "command_id": Uuid::new_v4(),
                "kind": "CANCEL_RUN",
                "created_at": Utc::now(),
                "expires_at": Utc::now() + chrono::Duration::minutes(2),
                "run_id": state.run_id,
                "lease_epoch": 1,
                "payload": {"agent_run_id": state.run_id},
            }));
        }
    }
    let refresh_after = state.refresh_after.lock().unwrap().clone();
    if let Some((marker, mcp)) = refresh_after {
        let seen = assistant_text_of(&state.events.lock().unwrap()).contains(&marker);
        if seen && !state.refresh_sent.swap(true, Ordering::SeqCst) {
            commands.push(json!({
                "command_id": Uuid::new_v4(),
                "kind": "REFRESH_CREDENTIAL",
                "created_at": Utc::now(),
                "expires_at": Utc::now() + chrono::Duration::minutes(2),
                "run_id": state.run_id,
                "lease_epoch": 1,
                "payload": {"mcp": mcp},
            }));
        }
    }
    // Answer every parked request, not just the first: a real agent asks again
    // for each tool it wants, and a control plane that answered once would
    // leave the second request to time out.
    if !matches!(state.permission_answer, PermissionAnswer::Ignore) {
        let parked = state
            .events
            .lock()
            .unwrap()
            .iter()
            .filter(|event| event.event_type == EventType::PermissionRequest)
            .filter_map(|event| {
                event
                    .object_id
                    .clone()
                    .map(|request_id| (request_id, event.payload.clone()))
            })
            .collect::<Vec<_>>();
        let mut answered = state.answered.lock().unwrap();
        for (request_id, payload) in parked {
            let index = answered.len();
            if !answered.insert(request_id.clone()) {
                continue;
            }
            let option_id = state
                .permission_answer
                .option_for(index, &payload)
                .map_or(Value::Null, Value::String);
            let events = state.events.lock().unwrap();
            state.decisions.lock().unwrap().push(DecisionSnapshot {
                request_id: request_id.clone(),
                option_id: option_id.as_str().map(str::to_owned),
                assistant_text: assistant_text_of(&events),
                saw_terminal: events
                    .iter()
                    .any(|event| event.event_type == EventType::Terminal),
            });
            drop(events);
            commands.push(json!({
                "command_id": Uuid::new_v4(),
                "kind": "RESOLVE_PERMISSION",
                "created_at": Utc::now(),
                "expires_at": Utc::now() + chrono::Duration::minutes(2),
                "run_id": state.run_id,
                "lease_epoch": 1,
                "payload": {"request_id": request_id, "option_id": option_id},
            }));
        }
    }
    Ok(Json(json!({
        "protocol_version": 2,
        "host_status": "ONLINE",
        "commands": commands,
        "poll_after_ms": 25,
    })))
}

async fn append_events(
    State(state): State<ControlState>,
    headers: HeaderMap,
    Json(body): Json<Value>,
) -> Result<Json<Value>, StatusCode> {
    require_auth(&headers)?;
    let batch: EventBatch = serde_json::from_value(body).unwrap();
    let last = batch.events.last().unwrap();
    let response = json!({
        "run_id": last.run_id,
        "lease_epoch": last.lease_epoch,
        "acked_through": last.sequence,
    });
    state.events.lock().unwrap().extend(batch.events);
    Ok(Json(response))
}

// ---------------------------------------------------------------------------
// The real `lemma-agent-host serve` process.
// ---------------------------------------------------------------------------

/// A running `lemma-agent-host serve`, killed when the test drops it.
///
/// These tests drive the shipped binary rather than an in-process
/// `HostRuntime` for one reason: only the real process resolves adapters
/// through `LEMMA_AGENT_HOST_PATH` and only the real process uses its own
/// `current_exe()` as the MCP bridge, which is the wiring under test.
pub struct HostProcess {
    child: tokio::process::Child,
    pub stderr_path: PathBuf,
}

impl HostProcess {
    /// # Panics
    /// If pairing, configuration, or spawning fails.
    pub async fn start(root: &Path, control: &ControlPlane, shims: &ShimmedAgents) -> Self {
        let paths = lemma_agent_host::config::HostPaths::under(root);
        paths.ensure().unwrap();
        let installation_id = Uuid::new_v4().to_string();
        let target = lemma_agent_host::api::TargetClient::pair(
            control.base_url.clone(),
            "hermetic-pairing-code-with-entropy",
            "Hermetic host",
            &installation_id,
            true,
        )
        .await
        .unwrap();
        lemma_agent_host::config::HostConfig {
            installation_id,
            targets: vec![target],
            max_runs: 1,
        }
        .save(&paths)
        .unwrap();

        let stderr_path = root.join("host-stderr.log");
        let stderr = std::fs::File::create(&stderr_path).unwrap();
        let child = tokio::process::Command::new(env!("CARGO_BIN_EXE_lemma-agent-host"))
            .arg("--data-dir")
            .arg(root)
            .arg("serve")
            // Adapter resolution reads this before PATH, so the pinned
            // manifest's native adapters resolve to the scripted agent.
            .env("LEMMA_AGENT_HOST_PATH", &shims.directory)
            .env("RUST_LOG", "lemma_agent_host=debug")
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::from(stderr))
            .kill_on_drop(true)
            .spawn()
            .unwrap();
        Self { child, stderr_path }
    }

    /// # Panics
    /// If the log cannot be read.
    #[must_use]
    pub fn stderr(&self) -> String {
        std::fs::read_to_string(&self.stderr_path).unwrap_or_default()
    }

    pub async fn shutdown(mut self) {
        let _ = self.child.kill().await;
    }
}

/// `HostRuntime` running inside the test process against real installed
/// adapters.
///
/// The subprocess variant above exists to shim adapter resolution; a test that
/// wants the developer's genuine Codex or Claude Code install wants the
/// opposite, so this one keeps the ambient environment and only redirects the
/// MCP bridge to the freshly built binary.
pub struct InProcessHost {
    handle: tokio::task::JoinHandle<anyhow::Result<()>>,
}

impl InProcessHost {
    /// `adapter_source` is a directory whose installed adapters should be
    /// reused, e.g. `LEMMA_REAL_AGENT_HOST_DATA_DIR`'s `adapters`.
    ///
    /// # Panics
    /// If pairing or configuration fails.
    #[cfg_attr(
        windows,
        expect(unused_variables, reason = "adapter reuse is a unix symlink")
    )]
    pub async fn start(
        root: &Path,
        control: &ControlPlane,
        adapter_source: &Path,
        bridge_executable: PathBuf,
    ) -> Self {
        let paths = lemma_agent_host::config::HostPaths::under(root);
        paths.ensure().unwrap();
        #[cfg(unix)]
        {
            let _ = std::fs::remove_dir_all(&paths.adapters);
            std::os::unix::fs::symlink(adapter_source, &paths.adapters).unwrap();
        }
        let installation_id = Uuid::new_v4().to_string();
        let target = lemma_agent_host::api::TargetClient::pair(
            control.base_url.clone(),
            "real-pairing-code-with-entropy",
            "Real host",
            &installation_id,
            true,
        )
        .await
        .unwrap();
        let config = lemma_agent_host::config::HostConfig {
            installation_id,
            targets: vec![target],
            max_runs: 1,
        };
        config.save(&paths).unwrap();
        let runtime = lemma_agent_host::runtime::HostRuntime::new(config, paths)
            .unwrap()
            .with_mcp_bridge_executable(bridge_executable);
        Self {
            handle: tokio::spawn(runtime.serve()),
        }
    }

    #[must_use]
    pub fn is_finished(&self) -> bool {
        self.handle.is_finished()
    }

    pub async fn shutdown(self) {
        self.handle.abort();
        let _ = self.handle.await;
    }
}
