//! A stand-in for the workspace's MCP endpoint.

use super::*;

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
    polls: Arc<Mutex<u32>>,
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

/// The tool that parks, and the durable id it parks under.
pub const PARK_TOOL: &str = "lemma_park";
pub const PARK_CALL_ID: &str = "parked-call-1";
const PARKED_POLLS_BEFORE_DECISION: u32 = 2;

async fn interaction_poll(
    axum::extract::State(state): axum::extract::State<McpState>,
    axum::extract::Path((_conversation_id, tool_call_id)): axum::extract::Path<(String, String)>,
) -> axum::response::Response {
    use axum::response::IntoResponse;
    let mut polls = state.polls.lock().unwrap();
    *polls += 1;
    let answered = *polls > PARKED_POLLS_BEFORE_DECISION;
    drop(polls);
    if !answered {
        return axum::http::StatusCode::NO_CONTENT.into_response();
    }
    axum::Json(json!({
        "success": true,
        "answers": {"Pick one": "Blue"},
        "decided_for": tool_call_id,
    }))
    .into_response()
}

#[derive(Clone)]
struct McpState {
    polls: Arc<Mutex<u32>>,
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
    /// How many times the bridge asked whether the parked call was decided.
    pub fn interaction_polls(&self) -> u32 {
        *self.polls.lock().unwrap()
    }

    /// # Panics
    /// If the listener cannot bind.
    pub async fn start(transport: McpTransport) -> Self {
        let requests = Arc::new(Mutex::new(Vec::new()));
        let deletes = Arc::new(Mutex::new(Vec::new()));
        let accepted = Arc::new(Mutex::new(vec![format!("Bearer {MCP_BEARER}")]));
        let scripted = Arc::new(Mutex::new(Vec::new()));
        let polls = Arc::new(Mutex::new(0u32));
        let state = McpState {
            polls: Arc::clone(&polls),
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
            // What Lemma answers a bridge that is holding a parked tool
            // response open: 204 while the person is still deciding, then the
            // decision. Two 204s first, so the test proves the bridge actually
            // waits rather than happening to ask once after the answer landed.
            .route(
                "/agent-runtime/conversations/{conversation_id}/interactions/{tool_call_id}",
                axum::routing::get(interaction_poll),
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
            polls,
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

pub(crate) fn header(headers: &HeaderMap, name: &str) -> Option<String> {
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
            }, {
                // The parking tool has to be *offered*, not merely answered: an
                // agent cannot call a tool it was never shown, which is exactly
                // how the first real-provider run of this failed.
                "name": PARK_TOOL,
                "description":
                    "Ask the user a question and wait for their answer.",
                "inputSchema": {"type": "object", "properties": {}},
                "_meta": {"lemma_tool_name": "ask_user"},
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
            if name == PARK_TOOL {
                json!({
                    "jsonrpc": "2.0",
                    "id": id,
                    "result": {
                        "content": [{"type": "text", "text": "{\"parked\":true}"}],
                        "structuredContent": {
                            "success": true,
                            "parked_tool_call_id": PARK_CALL_ID,
                        },
                        "isError": false,
                    },
                })
            } else if name == ECHO_TOOL {
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
