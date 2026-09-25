//! A stand-in for the workspace's MCP tools.
//!
//! The agent's Lemma tool calls travel over the link now: the `mcp-bridge`
//! the adapter spawns hands them to the host's relay, which sends each
//! `tools/list` and `tools/call` as an `mcp` frame and waits on a parked call
//! with `interaction_wait`. So this stand-in serves no HTTP. It answers those
//! frames, reached through [`ControlPlane::serve_mcp`], with the result objects
//! `app/mcp_server.py` produces, and records every request the way Lemma sees
//! it: which run, which conversation, which credential.

use super::*;

/// One `mcp` frame, as Lemma received it.
#[derive(Clone, Debug)]
pub struct McpRequestRecord {
    pub conversation_id: String,
    pub run_id: String,
    /// The run's own credential, which Lemma authorizes every call against.
    pub token: String,
    pub method: String,
    pub params: Value,
}

/// A failure to serve instead of the next real answer.
///
/// The endpoint could not fail at all once: it answered and nothing else, so
/// the bridge's own behaviour on a restarting backend -- the case that took
/// every Lemma tool away from a running agent -- had no way to be exercised.
#[derive(Clone, Debug)]
pub enum ScriptedFailure {
    /// Answer with a retryable `UNAVAILABLE` error, as a Lemma that cannot
    /// reach its own dependencies does.
    Unavailable,
    /// Answer with `UNAUTHORIZED`, the shape a dead run token takes.
    Unauthorized,
    /// Close the link without answering, as a replica restarting under the
    /// request does. Whether the request ran is then unknowable to the host.
    DropLink,
}

/// How a request is refused, for the control plane to deliver.
pub(crate) enum McpFailure {
    Refused {
        code: &'static str,
        message: String,
        retryable: bool,
    },
    DropLink,
}

/// The tool that parks, and the durable id it parks under.
pub const PARK_TOOL: &str = "lemma_park";
pub const PARK_CALL_ID: &str = "parked-call-1";
/// How long a person takes to decide, unless a test decides for them.
///
/// Long enough that an answer cannot have been sitting there before the wait
/// was asked for, short enough not to slow the suite.
const DEFAULT_DECISION_DELAY: Duration = Duration::from_millis(500);

#[derive(Clone)]
pub struct LemmaMcpEndpoint {
    pub conversation_id: Uuid,
    inner: Arc<McpState>,
}

struct McpState {
    requests: Mutex<Vec<McpRequestRecord>>,
    /// Tokens this endpoint will serve. More than one because a run's
    /// credential is rotated in flight, and a real Lemma accepts the
    /// replacement it just issued alongside the one still in use.
    accepted: Mutex<Vec<String>>,
    scripted: Mutex<Vec<ScriptedFailure>>,
    waits: Mutex<u32>,
    /// Whether decisions wait for [`LemmaMcpEndpoint::decide`] rather than
    /// arriving on their own after [`DEFAULT_DECISION_DELAY`].
    held: AtomicBool,
    decided: tokio::sync::watch::Sender<bool>,
}

impl Default for LemmaMcpEndpoint {
    fn default() -> Self {
        Self::new()
    }
}

impl LemmaMcpEndpoint {
    #[must_use]
    pub fn new() -> Self {
        Self {
            conversation_id: Uuid::new_v4(),
            inner: Arc::new(McpState {
                requests: Mutex::new(Vec::new()),
                accepted: Mutex::new(vec![MCP_BEARER.to_owned()]),
                scripted: Mutex::new(Vec::new()),
                waits: Mutex::new(0),
                held: AtomicBool::new(false),
                decided: tokio::sync::watch::channel(false).0,
            }),
        }
    }

    /// How many times a parked call was waited on.
    ///
    /// # Panics
    /// If the mutex is poisoned.
    #[must_use]
    pub fn interaction_waits(&self) -> u32 {
        *self.inner.waits.lock().unwrap()
    }

    /// Hold every decision until [`Self::decide`], so a test can prove the
    /// wait was already open when the person answered.
    pub fn hold_decisions(&self) {
        self.inner.held.store(true, Ordering::SeqCst);
    }

    /// The person answers.
    pub fn decide(&self) {
        self.inner.decided.send_replace(true);
    }

    /// Fail the next calls in order, then serve normally again.
    ///
    /// # Panics
    /// If the mutex is poisoned.
    pub fn fail_next(&self, failures: impl IntoIterator<Item = ScriptedFailure>) {
        self.inner.scripted.lock().unwrap().extend(failures);
    }

    /// Also serve `token`, as Lemma does for a credential it has just
    /// re-issued for a run that is still in flight.
    ///
    /// # Panics
    /// If the mutex is poisoned.
    pub fn also_accept(&self, token: &str) {
        self.inner.accepted.lock().unwrap().push(token.to_owned());
    }

    /// The `mcp` object Lemma puts in the encrypted `START_RUN` payload.
    ///
    /// No URL: the host reaches Lemma's tools over its link, and the relay
    /// reads the run's `token` and `conversation_id` from here on every call.
    #[must_use]
    pub fn run_configuration(&self) -> Value {
        json!({
            "server_name": "lemma_tools",
            "token": MCP_BEARER,
            "conversation_id": self.conversation_id,
        })
    }

    /// # Panics
    /// If the recording mutex is poisoned.
    #[must_use]
    pub fn requests(&self) -> Vec<McpRequestRecord> {
        self.inner.requests.lock().unwrap().clone()
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

    fn authorized(&self, body: &Value) -> Result<(), McpFailure> {
        let presented = body["token"].as_str().unwrap_or_default();
        if self
            .inner
            .accepted
            .lock()
            .unwrap()
            .iter()
            .any(|allowed| allowed == presented)
        {
            Ok(())
        } else {
            Err(McpFailure::Refused {
                code: "UNAUTHORIZED",
                message: "Unauthorized MCP token".to_owned(),
                retryable: false,
            })
        }
    }

    /// Answer one `mcp` frame's body with its result object.
    pub(crate) fn answer(&self, body: &Value) -> Result<Value, McpFailure> {
        let method = body["method"].as_str().unwrap_or_default().to_owned();
        let params = body.get("params").cloned().unwrap_or(Value::Null);
        self.inner.requests.lock().unwrap().push(McpRequestRecord {
            conversation_id: body["conversation_id"]
                .as_str()
                .unwrap_or_default()
                .to_owned(),
            run_id: body["run_id"].as_str().unwrap_or_default().to_owned(),
            token: body["token"].as_str().unwrap_or_default().to_owned(),
            method: method.clone(),
            params: params.clone(),
        });
        let scripted = {
            let mut scripted = self.inner.scripted.lock().unwrap();
            (!scripted.is_empty()).then(|| scripted.remove(0))
        };
        match scripted {
            Some(ScriptedFailure::Unavailable) => {
                return Err(McpFailure::Refused {
                    code: "UNAVAILABLE",
                    message: "Lemma is restarting".to_owned(),
                    retryable: true,
                });
            }
            Some(ScriptedFailure::Unauthorized) => {
                return Err(McpFailure::Refused {
                    code: "UNAUTHORIZED",
                    message: "Unauthorized MCP token".to_owned(),
                    retryable: false,
                });
            }
            Some(ScriptedFailure::DropLink) => return Err(McpFailure::DropLink),
            None => {}
        }
        self.authorized(body)?;
        match method.as_str() {
            "tools/list" => Ok(json!({"tools": [{
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
                "description": "Ask the user a question and wait for their answer.",
                "inputSchema": {"type": "object", "properties": {}},
                "_meta": {"lemma_tool_name": "ask_user"},
            }]})),
            "tools/call" => {
                let name = params["name"].as_str().unwrap_or_default();
                let text = params
                    .pointer("/arguments/text")
                    .and_then(Value::as_str)
                    .unwrap_or_default();
                Ok(if name == PARK_TOOL {
                    json!({
                        "content": [{"type": "text", "text": "{\"parked\":true}"}],
                        "structuredContent": {
                            "success": true,
                            "parked_tool_call_id": PARK_CALL_ID,
                        },
                        "isError": false,
                    })
                } else if name == ECHO_TOOL {
                    json!({
                        "content": [{"type": "text", "text": format!("lemma-echo:{text}")}],
                        "structuredContent": {"echoed": text},
                        "isError": false,
                    })
                } else {
                    json!({
                        "content": [{"type": "text", "text": format!("unknown tool {name}")}],
                        "isError": true,
                    })
                })
            }
            other => Err(McpFailure::Refused {
                code: "INVALID_FRAME",
                message: format!("unsupported method {other}"),
                retryable: false,
            }),
        }
    }

    /// Answer one `interaction_wait` once the person has decided.
    ///
    /// Lemma holds the wait open rather than answering "not yet", so the
    /// bridge asks once and nothing polls.
    pub(crate) async fn wait_for_decision(&self, body: &Value) -> Result<Value, McpFailure> {
        self.authorized(body)?;
        *self.inner.waits.lock().unwrap() += 1;
        if self.inner.held.load(Ordering::SeqCst) {
            let mut decided = self.inner.decided.subscribe();
            let _ = decided.wait_for(|decided| *decided).await;
        } else {
            tokio::time::sleep(DEFAULT_DECISION_DELAY).await;
        }
        Ok(json!({
            "success": true,
            "answers": {"Pick one": "Blue"},
            "decided_for": body["tool_call_id"],
        }))
    }
}
