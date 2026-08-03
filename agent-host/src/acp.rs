//! Generic ACP v1 adapter driver.

use std::path::PathBuf;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::time::Duration;

use agent_client_protocol::schema::ProtocolVersion;
use agent_client_protocol::schema::v1::{
    ContentBlock, InitializeRequest, LoadSessionRequest, McpServer, NewSessionRequest,
    PermissionOptionKind, PromptRequest, RequestPermissionOutcome, RequestPermissionRequest,
    RequestPermissionResponse, SelectedPermissionOutcome, SessionConfigOption,
    SessionConfigOptionValue, SessionNotification, SetSessionConfigOptionRequest, TextContent,
};
use agent_client_protocol::{AcpAgent, AcpAgentConfig, Agent, ConnectionTo};
use async_trait::async_trait;
use serde_json::{Map, Value};

use crate::adapters::ResolvedAdapter;
use crate::permissions::{PermissionDecision, PermissionGate};
use crate::protocol::{ConfigOption, EventType, JsonMap, RunSpec, RunState};

#[derive(Clone)]
pub struct AcpRunRequest {
    pub adapter: ResolvedAdapter,
    pub run_spec: RunSpec,
    pub scratch_directory: PathBuf,
    pub mcp_server: Option<McpServer>,
    /// Whether this harness advertised `loadSession` at probe time. A run only
    /// tries to resume `run_spec.resume_session_id` when it did.
    pub can_load_session: bool,
    /// Where a native permission request parks while Lemma decides.
    pub permissions: PermissionGate,
    /// How long a parked request waits before it is denied, so a prompt
    /// nobody answers cannot pin the adapter open.
    pub permission_timeout: Duration,
}

#[derive(Clone, Debug)]
pub struct AcpRunOutcome {
    pub provider_session_id: String,
    pub state: RunState,
    pub stop_reason: String,
}

#[derive(Clone, Debug, serde::Serialize)]
pub struct AcpProbeOutcome {
    pub config_options: Vec<ConfigOption>,
    pub capabilities: Value,
}

pub trait AcpCallbacks: Send + Sync + 'static {
    fn before_prompt(&self, provider_session_id: &str) -> anyhow::Result<()>;
    fn event(
        &self,
        event_type: EventType,
        object_id: Option<String>,
        payload: JsonMap,
    ) -> anyhow::Result<()>;
}

#[async_trait]
pub trait AgentDriver: Send + Sync {
    async fn probe(
        &self,
        adapter: ResolvedAdapter,
        scratch_directory: PathBuf,
    ) -> anyhow::Result<AcpProbeOutcome>;

    async fn run(
        &self,
        request: AcpRunRequest,
        callbacks: Arc<dyn AcpCallbacks>,
    ) -> anyhow::Result<AcpRunOutcome>;
}

#[derive(Clone, Default)]
pub struct AcpDriver;

#[async_trait]
impl AgentDriver for AcpDriver {
    async fn probe(
        &self,
        adapter: ResolvedAdapter,
        scratch_directory: PathBuf,
    ) -> anyhow::Result<AcpProbeOutcome> {
        std::fs::create_dir_all(&scratch_directory)?;
        let agent = build_agent(&adapter);
        let outcome = agent_client_protocol::Client
            .builder()
            .name("lemma-agent-host-probe")
            .connect_with(agent, |connection: ConnectionTo<Agent>| async move {
                let initialization = connection
                    .send_request(InitializeRequest::new(ProtocolVersion::V1))
                    .block_task()
                    .await?;
                let session = connection
                    .send_request(NewSessionRequest::new(scratch_directory))
                    .block_task()
                    .await?;
                let capabilities = serde_json::to_value(initialization.agent_capabilities)
                    .unwrap_or_else(|_| Value::Object(Map::new()));
                let config_options = session
                    .config_options
                    .unwrap_or_default()
                    .iter()
                    .filter_map(|option| convert_config_option(&adapter.spec.key, option))
                    .collect();
                Ok(AcpProbeOutcome {
                    config_options,
                    capabilities,
                })
            })
            .await?;
        Ok(outcome)
    }

    async fn run(
        &self,
        request: AcpRunRequest,
        callbacks: Arc<dyn AcpCallbacks>,
    ) -> anyhow::Result<AcpRunOutcome> {
        std::fs::create_dir_all(&request.scratch_directory)?;
        let adapter_key = request.adapter.spec.key.clone();
        let agent = build_agent(&request.adapter);
        let notification_callbacks = Arc::clone(&callbacks);
        let permission_callbacks = Arc::clone(&callbacks);
        let permission_gate = request.permissions.clone();
        let permission_sequence = Arc::new(AtomicU64::new(0));
        let permission_timeout = request.permission_timeout;
        let permission_run_id = request.run_spec.agent_run_id;
        let can_load_session = request.can_load_session;
        let run_spec = request.run_spec;
        let scratch_directory = request.scratch_directory;
        let mcp_server = request.mcp_server;
        let has_scoped_mcp_server = mcp_server.is_some();
        let resume_session_id = session_to_resume(&run_spec, can_load_session);
        // `session/load` replays the whole conversation back as session updates
        // before it returns. Those are turns Lemma already has, so forwarding
        // them would duplicate every earlier message in the transcript. Streaming
        // opens when this run's own prompt goes out.
        let streaming = Arc::new(AtomicBool::new(false));
        let notification_streaming = Arc::clone(&streaming);
        let outcome = agent_client_protocol::Client
            .builder()
            .name("lemma-agent-host")
            .on_receive_notification(
                async move |notification: SessionNotification, _context| {
                    if !notification_streaming.load(Ordering::SeqCst) {
                        return Ok(());
                    }
                    if let Some((event_type, object_id, payload)) =
                        normalize_session_update(&notification.update)
                    {
                        notification_callbacks
                            .event(event_type, object_id, payload)
                            .map_err(|error| {
                                agent_client_protocol::schema::v1::Error::internal_error()
                                    .data(error.to_string())
                            })?;
                    }
                    Ok(())
                },
                agent_client_protocol::on_receive_notification!(),
            )
            .on_receive_request(
                async move |request: RequestPermissionRequest, responder, _connection| {
                    if has_scoped_mcp_server && is_scoped_mcp_tool_approval(&request) {
                        let outcome = request
                            .options
                            .iter()
                            .find(|option| option.kind == PermissionOptionKind::AllowOnce)
                            .map_or(RequestPermissionOutcome::Cancelled, |option| {
                                RequestPermissionOutcome::Selected(SelectedPermissionOutcome::new(
                                    option.option_id.clone(),
                                ))
                            });
                        return responder.respond(RequestPermissionResponse::new(outcome));
                    }
                    let payload = permission_payload(&request);
                    // Without a toolCallId every request in a session would
                    // collapse onto one gate key, so concurrent prompts would
                    // deny each other and overwrite each other's approval card
                    // in Lemma. The counter makes the fallback unique per
                    // request; the id round-trips as the event's object_id and
                    // comes back verbatim in RESOLVE_PERMISSION.
                    let request_id = tool_call_id(&payload).unwrap_or_else(|| {
                        format!(
                            "{}:{}",
                            request.session_id,
                            permission_sequence.fetch_add(1, Ordering::Relaxed)
                        )
                    });
                    let _ = permission_callbacks.event(
                        EventType::PermissionRequest,
                        Some(request_id.clone()),
                        payload,
                    );
                    // Hold the agent's request open until Lemma answers. The
                    // gate denies on timeout, so this cannot hang forever.
                    let decision = permission_gate
                        .wait(permission_run_id, request_id, permission_timeout)
                        .await;
                    let outcome = match decision {
                        PermissionDecision::Allow { option_id } => request
                            .options
                            .iter()
                            .find(|option| option.option_id.to_string() == option_id)
                            .or_else(|| {
                                request
                                    .options
                                    .iter()
                                    .find(|option| option.kind == PermissionOptionKind::AllowOnce)
                            })
                            .map_or(RequestPermissionOutcome::Cancelled, |option| {
                                RequestPermissionOutcome::Selected(SelectedPermissionOutcome::new(
                                    option.option_id.clone(),
                                ))
                            }),
                        PermissionDecision::Deny => RequestPermissionOutcome::Cancelled,
                    };
                    responder.respond(RequestPermissionResponse::new(outcome))
                },
                agent_client_protocol::on_receive_request!(),
            )
            .connect_with(agent, |connection: ConnectionTo<Agent>| async move {
                connection
                    .send_request(InitializeRequest::new(ProtocolVersion::V1))
                    .block_task()
                    .await?;
                let mcp_servers: Vec<McpServer> = mcp_server.into_iter().collect();
                // A Lemma conversation is one provider session: resuming is what
                // lets the agent answer "what did I just say" instead of meeting
                // the user again every turn.
                let mut established = None;
                if let Some(existing) = resume_session_id {
                    match connection
                        .send_request(
                            LoadSessionRequest::new(existing.clone(), scratch_directory.clone())
                                .mcp_servers(mcp_servers.clone()),
                        )
                        .block_task()
                        .await
                    {
                        Ok(loaded) => {
                            established = Some((existing.into(), loaded.config_options));
                        }
                        // A provider is free to forget a session — Codex prunes
                        // its rollout files, a Claude Code session can be deleted
                        // from disk. Losing history is survivable; losing the
                        // answer is not, so a failed load starts fresh.
                        Err(error) => tracing::warn!(
                            %error,
                            "could not resume the conversation's provider session; starting a new one"
                        ),
                    }
                }
                let (session_id, config_options) = if let Some(established) = established {
                    established
                } else {
                    let session = connection
                        .send_request(
                            NewSessionRequest::new(scratch_directory).mcp_servers(mcp_servers),
                        )
                        .block_task()
                        .await?;
                    (session.session_id, session.config_options)
                };
                let safe_options = config_options
                    .unwrap_or_default()
                    .iter()
                    .filter_map(|option| convert_config_option(&adapter_key, option))
                    .collect::<Vec<_>>();

                // A user's provider configuration may have been left in an
                // unrestricted mode outside Lemma. New Agent Host sessions
                // always reset a policy-bearing option to a known safe value
                // before applying the profile's validated selections.
                for option in safe_options.iter().filter(|option| {
                    option
                        .metadata
                        .get("hostPolicyDefaultOverride")
                        .and_then(Value::as_bool)
                        .unwrap_or(false)
                }) {
                    let value = session_config_value(&option.id, &option.current_value).map_err(
                        |message| {
                            agent_client_protocol::schema::v1::Error::invalid_params().data(message)
                        },
                    )?;
                    connection
                        .send_request(SetSessionConfigOptionRequest::new(
                            session_id.clone(),
                            option.id.clone(),
                            value,
                        ))
                        .block_task()
                        .await?;
                }
                if let Some(model_name) = &run_spec.model_name {
                    let option = safe_options
                        .iter()
                        .find(|option| option.category == "model")
                        .ok_or_else(|| {
                            agent_client_protocol::schema::v1::Error::invalid_params()
                                .data("this harness does not expose model selection")
                        })?;
                    let selection = Value::String(model_name.clone());
                    if !selection_is_allowed(option, &selection) {
                        return Err(agent_client_protocol::schema::v1::Error::invalid_params()
                            .data("selected model is not offered by this harness"));
                    }
                    connection
                        .send_request(SetSessionConfigOptionRequest::new(
                            session_id.clone(),
                            option.id.clone(),
                            SessionConfigOptionValue::value_id(model_name.clone()),
                        ))
                        .block_task()
                        .await?;
                }
                for (key, selection) in &run_spec.config_selections {
                    let option = safe_options
                        .iter()
                        .find(|option| option.id == *key || option.category == *key)
                        .ok_or_else(|| {
                            agent_client_protocol::schema::v1::Error::invalid_params()
                                .data(format!("unknown or policy-blocked configuration: {key}"))
                        })?;
                    if option.category == "model" {
                        return Err(agent_client_protocol::schema::v1::Error::invalid_params()
                            .data("model must be supplied through model_name"));
                    }
                    if !selection_is_allowed(option, selection) {
                        return Err(agent_client_protocol::schema::v1::Error::invalid_params()
                            .data(format!("configuration value is not allowed for {key}")));
                    }
                    let value = session_config_value(key, selection).map_err(|message| {
                        agent_client_protocol::schema::v1::Error::invalid_params().data(message)
                    })?;
                    connection
                        .send_request(SetSessionConfigOptionRequest::new(
                            session_id.clone(),
                            option.id.clone(),
                            value,
                        ))
                        .block_task()
                        .await?;
                }
                callbacks
                    .before_prompt(&session_id.to_string())
                    .map_err(|error| {
                        agent_client_protocol::schema::v1::Error::internal_error()
                            .data(error.to_string())
                    })?;
                // Past this point every session update belongs to this turn.
                streaming.store(true, Ordering::SeqCst);
                let prompt = render_prompt(&run_spec);
                let response = connection
                    .send_request(PromptRequest::new(
                        session_id.clone(),
                        vec![ContentBlock::Text(TextContent::new(prompt))],
                    ))
                    .block_task()
                    .await?;
                let stop_reason = serde_json::to_value(response.stop_reason)
                    .ok()
                    .and_then(|value| value.as_str().map(str::to_owned))
                    .unwrap_or_else(|| "unknown".to_owned());
                let state = match stop_reason.as_str() {
                    "end_turn" => RunState::Succeeded,
                    "cancelled" => RunState::Cancelled,
                    _ => RunState::Failed,
                };
                Ok(AcpRunOutcome {
                    provider_session_id: session_id.to_string(),
                    state,
                    stop_reason,
                })
            })
            .await?;
        Ok(outcome)
    }
}

/// The session this run should continue, or `None` to open a new one.
///
/// Lemma only sends a `resume_session_id` for a harness that advertised
/// `loadSession`, but the host checks its own probe too rather than trusting a
/// stale server-side capability record: a `session/load` an agent does not
/// implement costs a round trip on every turn before falling back.
fn session_to_resume(run_spec: &RunSpec, can_load_session: bool) -> Option<String> {
    if !can_load_session {
        return None;
    }
    run_spec
        .resume_session_id
        .as_deref()
        .map(str::trim)
        .filter(|id| !id.is_empty())
        .map(str::to_owned)
}

fn build_agent(adapter: &ResolvedAdapter) -> AcpAgent {
    let config = AcpAgentConfig::new(&adapter.command)
        .args(adapter.args())
        .envs(adapter.environment());
    AcpAgent::new(config).with_debug(|line, direction| {
        if matches!(direction, agent_client_protocol::LineDirection::Stderr) {
            tracing::debug!(
                target = "agent_stderr",
                bytes = line.len(),
                "ACP adapter stderr"
            );
        }
    })
}

fn render_prompt(spec: &RunSpec) -> String {
    let mut sections = Vec::new();
    if !spec.system_prompt.trim().is_empty() {
        sections.push(format!(
            "<system>\n{}\n</system>",
            spec.system_prompt.trim()
        ));
    }
    let prompt = spec
        .prompt
        .iter()
        .filter_map(extract_text)
        .collect::<Vec<_>>()
        .join("\n");
    if !prompt.trim().is_empty() {
        sections.push(prompt);
    }
    sections.join("\n\n")
}

fn extract_text(value: &Value) -> Option<String> {
    if let Some(text) = value.as_str() {
        return Some(text.to_owned());
    }
    let object = value.as_object()?;
    if let Some(text) = object.get("text").and_then(Value::as_str) {
        return Some(text.to_owned());
    }
    match object.get("content") {
        Some(Value::String(text)) => Some(text.clone()),
        Some(Value::Array(items)) => Some(
            items
                .iter()
                .filter_map(extract_text)
                .collect::<Vec<_>>()
                .join("\n"),
        ),
        Some(value) => extract_text(value),
        None => None,
    }
}

fn normalize_session_update(
    update: &agent_client_protocol::schema::v1::SessionUpdate,
) -> Option<(EventType, Option<String>, JsonMap)> {
    let mut value = serde_json::to_value(update).ok()?;
    let object = value.as_object_mut()?;
    let update_type = object.remove("sessionUpdate")?.as_str()?.to_owned();
    let event_type = match update_type.as_str() {
        "user_message_chunk" => EventType::UserMessage,
        "agent_message_chunk" => EventType::AgentMessageChunk,
        "agent_thought_chunk" => EventType::AgentThoughtChunk,
        "tool_call" => EventType::ToolCallUpsert,
        "tool_call_update" => EventType::ToolCallUpdate,
        "plan" | "plan_update" => EventType::PlanUpsert,
        "usage_update" => EventType::UsageUpdate,
        "config_option_update" | "current_mode_update" => EventType::ConfigUpdate,
        "available_commands_update" | "session_info_update" => EventType::RunState,
        _ => return None,
    };
    flatten_content_text(object);
    let object_id = find_string(object, &["toolCallId", "tool_call_id", "id", "contentId"]);
    Some((
        event_type,
        object_id,
        object
            .iter()
            .map(|(key, value)| (key.clone(), value.clone()))
            .collect(),
    ))
}

fn permission_payload(request: &RequestPermissionRequest) -> JsonMap {
    let mut value = serde_json::to_value(request).unwrap_or_else(|_| Value::Object(Map::new()));
    let object = value
        .as_object_mut()
        .expect("permission request serializes as object");
    object.insert(
        "message".to_owned(),
        Value::String(
            "The local agent asked for permission to use a native tool. Lemma \
             is waiting for a decision; scoped Lemma MCP tools are approved \
             automatically."
                .to_owned(),
        ),
    );
    object
        .iter()
        .map(|(key, value)| (key.clone(), value.clone()))
        .collect()
}

/// The name Lemma's run-scoped MCP server is registered under.
///
/// Kept next to the matcher because that is the only thing tying the two
/// together: `runtime.rs` registers `McpServerStdio::new(SCOPED_MCP_SERVER, …)`
/// and this is how a permission request is recognised as belonging to it.
pub(crate) const SCOPED_MCP_SERVER: &str = "lemma";

/// Is this the agent asking permission to use one of *our* tools?
///
/// Lemma's own MCP tools are already scoped to the run and authorised by the
/// workspace, so prompting for each one is noise the user has to click through
/// on every single call.
///
/// Two ways to tell, because one is not enough. The `_meta` flag is a Claude
/// Code convention; an agent that does not set it — OpenCode does not — had
/// every Lemma tool call raise an approval card. So the tool name is checked
/// too, against the server name we registered ourselves, in the shapes agents
/// actually namespace MCP tools with.
fn is_scoped_mcp_tool_approval(request: &RequestPermissionRequest) -> bool {
    let Ok(value) = serde_json::to_value(request) else {
        return false;
    };
    let flagged = value
        .get("_meta")
        .and_then(|meta| meta.get("is_mcp_tool_approval"))
        .and_then(Value::as_bool)
        .unwrap_or(false);
    flagged || names_scoped_mcp_tool(&value)
}

/// Does the tool being requested belong to Lemma's own MCP server?
///
/// Agents namespace MCP tools differently — `mcp__lemma__read_table`,
/// `lemma__read_table`, `lemma.read_table`, `lemma/read_table` — so this looks
/// for the server name followed by a separator rather than assuming one
/// convention. Anchored at the start so a user's tool that merely mentions
/// "lemma" somewhere does not get silently auto-approved.
fn names_scoped_mcp_tool(value: &Value) -> bool {
    let candidates = [
        value.pointer("/toolCall/title").and_then(Value::as_str),
        value.pointer("/toolCall/toolName").and_then(Value::as_str),
        value.pointer("/toolCall/name").and_then(Value::as_str),
        value.pointer("/toolCall/toolCallId").and_then(Value::as_str),
        value.get("toolName").and_then(Value::as_str),
    ];
    candidates.into_iter().flatten().any(|name| {
        let name = name.trim().to_ascii_lowercase();
        // `mcp__` is the common prefix agents add before the server name.
        let name = name.strip_prefix("mcp__").unwrap_or(&name);
        name.strip_prefix(SCOPED_MCP_SERVER)
            .is_some_and(|rest| rest.starts_with(['_', '.', '/', ':', '-']))
    })
}

fn tool_call_id(payload: &JsonMap) -> Option<String> {
    payload
        .get("toolCall")
        .and_then(Value::as_object)
        .and_then(|object| find_string(object, &["toolCallId", "id"]))
}

fn flatten_content_text(object: &mut Map<String, Value>) {
    let text = object
        .get("content")
        .and_then(|content| {
            content
                .as_object()
                .and_then(|content| content.get("text"))
                .and_then(Value::as_str)
        })
        .map(str::to_owned);
    if let Some(text) = text {
        object.insert("text".to_owned(), Value::String(text));
    }
    if let Some(Value::Object(tool_call)) = object.get("toolCall").cloned() {
        for (key, value) in tool_call {
            object.entry(key).or_insert(value);
        }
    }
}

/// A blank value counts as absent.
///
/// An adapter may send `toolCallId: ""`. Treating that as a real id collapses
/// every permission request in a run onto one gate key, so concurrent prompts
/// merge into one approval card and only one of them can ever be answered -
/// the other blocks until its timeout and the run never terminalises.
fn find_string(object: &Map<String, Value>, keys: &[&str]) -> Option<String> {
    keys.iter().find_map(|key| {
        object
            .get(*key)
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(str::to_owned)
    })
}

fn convert_config_option(adapter_key: &str, option: &SessionConfigOption) -> Option<ConfigOption> {
    let value = serde_json::to_value(option).ok()?;
    let object = value.as_object()?;
    let option_id = object.get("id")?.as_str()?.to_owned();
    let category = object
        .get("category")
        .and_then(|value| value.as_str())
        .unwrap_or(&option_id)
        .to_owned();
    let name = object
        .get("name")
        .and_then(Value::as_str)
        .unwrap_or(&option_id)
        .to_owned();
    let description = object
        .get("description")
        .and_then(Value::as_str)
        .map(str::to_owned);
    let mut current_value = object.get("currentValue").cloned().unwrap_or(Value::Null);
    let raw_options = object
        .get("options")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|value| {
            value.as_object().map(|object| {
                object
                    .iter()
                    .map(|(key, value)| (key.clone(), value.clone()))
                    .collect()
            })
        })
        .collect::<Vec<JsonMap>>();
    let mut metadata = JsonMap::new();
    if let Some(kind) = object.get("type") {
        metadata.insert("type".to_owned(), kind.clone());
    }
    let policy_bearing = is_policy_bearing_option(&option_id, &category);
    let mut options = raw_options;
    if policy_bearing {
        let original_count = options.len();
        options.retain(|candidate| {
            option_value(candidate).is_none_or(|value| !is_disallowed_policy_value(value))
        });
        let removed = original_count.saturating_sub(options.len());
        if removed > 0 {
            metadata.insert(
                "hostPolicyFilteredValueCount".to_owned(),
                Value::from(removed),
            );
        }
        if is_disallowed_policy_value(&current_value) {
            let replacement = preferred_safe_value(adapter_key, &options)?;
            current_value = replacement;
            metadata.insert("hostPolicyDefaultOverride".to_owned(), Value::Bool(true));
        }
    }
    Some(ConfigOption {
        id: option_id,
        category,
        name,
        description,
        current_value,
        options,
        metadata,
    })
}

fn is_policy_bearing_option(option_id: &str, category: &str) -> bool {
    [option_id, category].iter().any(|value| {
        let normalized = value.to_ascii_lowercase();
        ["mode", "permission", "approval", "sandbox"]
            .iter()
            .any(|marker| normalized.contains(marker))
    })
}

fn is_disallowed_policy_value(value: &Value) -> bool {
    let Some(value) = value.as_str() else {
        return false;
    };
    let normalized = value
        .chars()
        .filter(char::is_ascii_alphanumeric)
        .flat_map(char::to_lowercase)
        .collect::<String>();
    matches!(
        normalized.as_str(),
        "bypasspermissions"
            | "agentfullaccess"
            | "acceptedits"
            | "auto"
            | "autoapprove"
            | "fullaccess"
            | "unrestricted"
            | "yolo"
            | "dangerouslyskippermissions"
    )
}

fn option_value(option: &JsonMap) -> Option<&Value> {
    option.get("value").or_else(|| option.get("id"))
}

fn preferred_safe_value(adapter_key: &str, options: &[JsonMap]) -> Option<Value> {
    let preferences: &[&str] = match adapter_key {
        "claude-code" => &["default", "plan"],
        "codex" => &["agent", "plan", "read-only"],
        "opencode" => &["build", "plan"],
        "cursor" => &["default", "agent", "plan", "ask"],
        _ => &["default", "plan", "ask", "read-only"],
    };
    preferences
        .iter()
        .find_map(|preference| {
            options
                .iter()
                .filter_map(option_value)
                .find(|value| value.as_str() == Some(*preference))
                .cloned()
        })
        .or_else(|| options.iter().find_map(option_value).cloned())
}

fn selection_is_allowed(option: &ConfigOption, selection: &Value) -> bool {
    if is_policy_bearing_option(&option.id, &option.category)
        && is_disallowed_policy_value(selection)
    {
        return false;
    }
    let allowed = option
        .options
        .iter()
        .filter_map(option_value)
        .collect::<Vec<_>>();
    allowed.is_empty() || allowed.contains(&selection)
}

fn session_config_value(key: &str, selection: &Value) -> Result<SessionConfigOptionValue, String> {
    match selection {
        Value::Bool(value) => Ok(SessionConfigOptionValue::boolean(*value)),
        Value::String(value) => Ok(SessionConfigOptionValue::value_id(value.clone())),
        other => Err(format!(
            "unsupported configuration value for {key}: {other}"
        )),
    }
}

#[cfg(test)]
mod tests {
    use agent_client_protocol::schema::v1::{
        ContentChunk, ImageContent, SessionUpdate, TextContent,
    };

    use super::*;

    fn spec_resuming(resume_session_id: Option<&str>) -> RunSpec {
        RunSpec {
            agent_run_id: uuid::Uuid::new_v4(),
            conversation_id: uuid::Uuid::new_v4(),
            harness_id: uuid::Uuid::new_v4(),
            profile_revision: "r".into(),
            model_name: None,
            config_selections: JsonMap::new(),
            system_prompt: "Be exact.".into(),
            prompt: vec![serde_json::json!({"type": "text", "text": "Hello"})],
            resume_session_id: resume_session_id.map(str::to_owned),
            context: JsonMap::new(),
            mcp: serde_json::Value::Null,
            run_deadline: chrono::Utc::now(),
        }
    }

    #[test]
    fn prompt_rendering_keeps_system_and_text() {
        assert_eq!(
            render_prompt(&spec_resuming(None)),
            "<system>\nBe exact.\n</system>\n\nHello"
        );
    }

    #[test]
    fn a_conversation_with_a_session_resumes_it() {
        assert_eq!(
            session_to_resume(&spec_resuming(Some("sess-1")), true),
            Some("sess-1".to_owned())
        );
    }

    #[test]
    fn a_conversations_first_turn_opens_a_new_session() {
        assert_eq!(session_to_resume(&spec_resuming(None), true), None);
    }

    #[test]
    fn a_harness_that_cannot_load_never_tries_to() {
        assert_eq!(
            session_to_resume(&spec_resuming(Some("sess-1")), false),
            None
        );
    }

    #[test]
    fn a_blank_session_id_is_not_worth_a_round_trip() {
        assert_eq!(session_to_resume(&spec_resuming(Some("  ")), true), None);
    }

    #[test]
    fn message_chunk_is_flattened_for_backend_normalizer() {
        let update = SessionUpdate::AgentMessageChunk(ContentChunk::new(ContentBlock::Text(
            TextContent::new("hi"),
        )));
        let (kind, _, payload) = normalize_session_update(&update).unwrap();
        assert_eq!(kind, EventType::AgentMessageChunk);
        assert_eq!(payload.get("text"), Some(&Value::String("hi".into())));
    }

    #[test]
    fn image_chunk_preserves_the_standard_acp_content_block() {
        let update = SessionUpdate::AgentMessageChunk(ContentChunk::new(ContentBlock::Image(
            ImageContent::new("cG5n", "image/png"),
        )));
        let (kind, _, payload) = normalize_session_update(&update).unwrap();
        assert_eq!(kind, EventType::AgentMessageChunk);
        assert_eq!(
            payload.get("content"),
            Some(&serde_json::json!({
                "type": "image",
                "data": "cG5n",
                "mimeType": "image/png",
            }))
        );
        assert!(!payload.contains_key("text"));
    }

    #[test]
    fn codex_scoped_mcp_approval_is_recognized() {
        let request: RequestPermissionRequest = serde_json::from_value(serde_json::json!({
            "sessionId": "session",
            "toolCall": {
                "kind": "execute",
                "status": "pending",
                "toolCallId": "call-1"
            },
            "options": [
                {
                    "kind": "allow_once",
                    "name": "Allow",
                    "optionId": "allow_once"
                },
                {
                    "kind": "reject_once",
                    "name": "Decline",
                    "optionId": "decline"
                }
            ],
            "_meta": {"is_mcp_tool_approval": true}
        }))
        .unwrap();

        assert!(is_scoped_mcp_tool_approval(&request));
    }

    #[test]
    fn native_tool_permission_is_not_treated_as_scoped_mcp() {
        let request: RequestPermissionRequest = serde_json::from_value(serde_json::json!({
            "sessionId": "session",
            "toolCall": {
                "kind": "execute",
                "status": "pending",
                "toolCallId": "call-1",
                "rawInput": {"command": "python -c 'print(42)'"}
            },
            "options": [
                {
                    "kind": "allow_once",
                    "name": "Allow once",
                    "optionId": "allow_once"
                }
            ],
            "_meta": {"codex": {"reason": "run local shell"}}
        }))
        .unwrap();

        assert!(!is_scoped_mcp_tool_approval(&request));
    }

    #[test]
    fn provider_full_access_modes_are_filtered_and_overridden() {
        let option: SessionConfigOption = serde_json::from_value(serde_json::json!({
            "id": "mode",
            "name": "Mode",
            "category": "mode",
            "type": "select",
            "currentValue": "agent-full-access",
            "options": [
                {"value": "plan", "name": "Plan"},
                {"value": "agent", "name": "Agent"},
                {"value": "acceptEdits", "name": "Accept edits"},
                {"value": "agent-full-access", "name": "Agent (full access)"},
                {"value": "bypassPermissions", "name": "Bypass permissions"}
            ]
        }))
        .unwrap();

        let converted = convert_config_option("codex", &option).unwrap();
        assert_eq!(converted.current_value, Value::String("agent".into()));
        assert_eq!(converted.options.len(), 2);
        assert_eq!(
            converted.metadata.get("hostPolicyDefaultOverride"),
            Some(&Value::Bool(true))
        );
        assert!(!selection_is_allowed(
            &converted,
            &Value::String("agent-full-access".into())
        ));
    }
}

#[cfg(test)]
mod scoped_mcp_approval_tests {
    use super::names_scoped_mcp_tool;
    use serde_json::json;

    #[test]
    fn lemmas_own_tools_are_recognised_however_the_agent_namespaces_them() {
        // The `_meta` flag this used to rely on is a Claude Code convention.
        // An agent that does not set it — OpenCode — made every Lemma tool
        // call raise an approval card the user had to click through.
        for name in [
            "mcp__lemma__read_table",
            "lemma__read_table",
            "lemma.read_table",
            "lemma/read_table",
            "lemma:read_table",
        ] {
            assert!(
                names_scoped_mcp_tool(&json!({"toolCall": {"title": name}})),
                "{name} is one of ours",
            );
        }
    }

    #[test]
    fn someone_elses_tool_is_never_auto_approved() {
        // Anchored at the start on purpose: auto-approving anything that
        // merely mentions Lemma would hand away the user's decision.
        for name in [
            "read_lemma_notes",
            "lemmatize",
            "bash",
            "write_file",
            "my-lemma-helper",
        ] {
            assert!(
                !names_scoped_mcp_tool(&json!({"toolCall": {"title": name}})),
                "{name} is not ours to approve",
            );
        }
    }
}
