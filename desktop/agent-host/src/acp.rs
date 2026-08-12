//! Generic ACP v1 adapter driver.

use std::collections::HashSet;
use std::path::PathBuf;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::time::Duration;

use agent_client_protocol::schema::ProtocolVersion;
use agent_client_protocol::schema::v1::{
    CancelNotification, ContentBlock, InitializeRequest, LoadSessionRequest, McpServer,
    NewSessionRequest, PermissionOptionKind, PromptRequest, RequestPermissionOutcome,
    RequestPermissionRequest, RequestPermissionResponse, SelectedPermissionOutcome,
    SessionConfigOption, SessionConfigOptionValue, SessionNotification,
    SetSessionConfigOptionRequest, TextContent,
};
use agent_client_protocol::{AcpAgent, AcpAgentConfig, Agent, ConnectionTo};
use async_trait::async_trait;
use serde_json::{Map, Value};
use tokio::sync::watch;

use crate::adapters::ResolvedAdapter;
use crate::permissions::{AlwaysAllowOffer, AlwaysAllowScope, PermissionDecision, PermissionGate};
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
    /// Raised when Lemma wants this run stopped. Watched rather than acted on
    /// by killing the process, so the turn can end through ACP.
    pub cancel: watch::Receiver<bool>,
    /// How long the agent has to honour `session/cancel` before the run is
    /// failed and the supervisor falls back to killing the process tree.
    pub cancel_grace: Duration,
}

#[derive(Clone, Debug)]
pub struct AcpRunOutcome {
    pub provider_session_id: String,
    pub state: RunState,
    pub stop_reason: String,
    /// Why the turn ended, when that is not self-evident from the state.
    ///
    /// ACP distinguishes five ways a turn can stop and only two of them are
    /// plain success or cancellation. The other three were all reported as
    /// `FAILED` with no detail, so a run that simply ran out of context told
    /// the user "Agent Host run ended in FAILED" while its partial answer sat
    /// directly above.
    pub message: Option<String>,
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
        let mut cancel = request.cancel;
        let cancel_grace = request.cancel_grace;
        let run_spec = request.run_spec;
        let scratch_directory = request.scratch_directory;
        let mcp_server = request.mcp_server;
        let has_scoped_mcp_server = mcp_server.is_some();
        // Lemma tells us, in the run-scoped MCP config, exactly which tools it
        // serves. That is an exact answer to "is this one of ours?", which the
        // name-shape heuristic below can only approximate.
        let scoped_mcp_tools = Arc::new(scoped_mcp_tool_names(&run_spec.mcp));
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
                    if has_scoped_mcp_server
                        && is_scoped_mcp_tool_approval(&request, &scoped_mcp_tools)
                    {
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
                    // An "always" the user already gave for exactly this scope
                    // is answered here, without asking again. The agent's own
                    // rule lives in an adapter process that is new every run,
                    // and is set only once the answer arrives — so without this
                    // the same grant is asked for on the next message, and once
                    // more for every call of a parallel batch.
                    let always = always_allow_offer(&request);
                    if let Some(offer) = always.as_ref().filter(|offer| {
                        permission_gate.is_granted(&offer.scope)
                    }) {
                        return responder.respond(RequestPermissionResponse::new(
                            RequestPermissionOutcome::Selected(SelectedPermissionOutcome::new(
                                offer.option_id.clone(),
                            )),
                        ));
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
                        .wait(permission_run_id, request_id, permission_timeout, always)
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
                let turn = connection
                    .send_request(PromptRequest::new(
                        session_id.clone(),
                        prompt_blocks(&run_spec),
                    ))
                    .block_task();
                tokio::pin!(turn);
                // A cancel is asked for, not inflicted. Killing the process
                // here is what the supervisor falls back to, and it is the
                // worst moment to do it: the provider has not flushed the
                // session file the *next* turn resumes from, so cancelling one
                // message used to cost the conversation its whole history.
                let mut asked_to_stop = false;
                let response = tokio::select! {
                    result = &mut turn => result?,
                    () = cancel_requested(&mut cancel) => {
                        connection
                            .send_notification(CancelNotification::new(session_id.clone()))?;
                        asked_to_stop = true;
                        tokio::time::timeout(cancel_grace, &mut turn)
                            .await
                            .map_err(|_| {
                                agent_client_protocol::schema::v1::Error::internal_error().data(
                                    "the agent did not stop within the cancellation grace period",
                                )
                            })??
                    }
                };
                let stop_reason = serde_json::to_value(response.stop_reason)
                    .ok()
                    .and_then(|value| value.as_str().map(str::to_owned))
                    .unwrap_or_else(|| "unknown".to_owned());
                let (state, message) = run_outcome(asked_to_stop, &stop_reason);
                Ok(AcpRunOutcome {
                    provider_session_id: session_id.to_string(),
                    state,
                    stop_reason,
                    message,
                })
            })
            .await?;
        Ok(outcome)
    }
}

/// How a finished turn is recorded, given what we asked of it.
///
/// What *we* did outranks what the agent says about it. ACP requires the
/// `cancelled` stop reason for a turn stopped by `session/cancel` — and
/// `OpenCode` reports `end_turn`, verified against the real agent in
/// `real_harness_e2e`. Taking that literally records a run the user cancelled
/// as having *succeeded*, presenting a truncated answer as the whole one. We
/// know whether we asked it to stop, so that is what the run is.
fn run_outcome(asked_to_stop: bool, stop_reason: &str) -> (RunState, Option<String>) {
    if asked_to_stop {
        if stop_reason != "cancelled" {
            tracing::debug!(
                stop_reason,
                "the agent ended a cancelled turn without ACP's cancelled stop reason"
            );
        }
        return (RunState::Cancelled, None);
    }
    outcome_for(stop_reason)
}

/// How one ACP stop reason ends a Lemma run, and what to say about it.
///
/// ACP names five ways a turn can end. Only `end_turn` and `cancelled` speak
/// for themselves; the rest are distinct, actionable conditions that all used
/// to arrive as an undifferentiated `FAILED`. They stay `FAILED` — the turn
/// genuinely did not finish the work — but they now say which ceiling was hit,
/// because the difference between "out of context" and "the agent crashed" is
/// the difference between retrying usefully and retrying forever.
fn outcome_for(stop_reason: &str) -> (RunState, Option<String>) {
    match stop_reason {
        "end_turn" => (RunState::Succeeded, None),
        "cancelled" => (RunState::Cancelled, None),
        "max_tokens" => (
            RunState::Failed,
            Some(
                "The agent stopped because it reached its maximum context \
                 length. Anything it had already written is above; continue in \
                 a new conversation to give it room."
                    .to_owned(),
            ),
        ),
        "max_turn_requests" => (
            RunState::Failed,
            Some(
                "The agent stopped because it reached its limit on tool calls \
                 for a single turn. Ask it to continue, or narrow the task."
                    .to_owned(),
            ),
        ),
        "refusal" => (
            RunState::Failed,
            Some(
                "The agent declined to continue with this request. It will not \
                 see this prompt again, so rephrasing it is worth trying."
                    .to_owned(),
            ),
        ),
        other => (
            RunState::Failed,
            Some(format!("The agent ended its turn unexpectedly ({other}).")),
        ),
    }
}

/// A cancel signal nothing will ever raise.
///
/// For callers with no control plane behind them — a local smoke run, a test
/// exercising some other part of the driver — so they do not have to invent a
/// channel they will never send on.
#[must_use]
pub fn never_cancelled() -> watch::Receiver<bool> {
    watch::channel(false).1
}

/// Resolves once Lemma has asked for this run to stop.
///
/// Never resolves if the sender is dropped: the run task owns that sender for
/// its whole life, so a dropped one means the supervisor is already tearing
/// this run down by other means and a spurious `session/cancel` would only
/// race it.
async fn cancel_requested(cancel: &mut watch::Receiver<bool>) {
    if *cancel.borrow() {
        return;
    }
    while cancel.changed().await.is_ok() {
        if *cancel.borrow() {
            return;
        }
    }
    std::future::pending::<()>().await;
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

/// The prompt as ACP content blocks.
///
/// Text is still assembled into one leading block, because that is what every
/// certified adapter has been receiving and splitting it changes how agents
/// read the boundary between the system framing and the user's words.
///
/// Anything that is *not* text is then carried through as itself. It used to be
/// flattened by `extract_text` along with everything else, which for an image
/// or an embedded resource means silently dropped: `PromptRequest` takes a
/// `Vec<ContentBlock>` precisely so those can travel, and a block this build
/// cannot parse falls back to its text rather than disappearing.
fn prompt_blocks(spec: &RunSpec) -> Vec<ContentBlock> {
    let mut blocks = vec![ContentBlock::Text(TextContent::new(render_prompt(spec)))];
    blocks.extend(spec.prompt.iter().filter_map(structured_block));
    blocks
}

/// A non-text content block, or `None` for anything `render_prompt` covered.
fn structured_block(value: &Value) -> Option<ContentBlock> {
    let kind = value.as_object()?.get("type")?.as_str()?;
    if kind == "text" {
        return None;
    }
    match serde_json::from_value::<ContentBlock>(value.clone()) {
        Ok(block) => Some(block),
        Err(error) => {
            tracing::warn!(
                %error,
                kind,
                "dropping a prompt content block this Agent Host cannot represent"
            );
            None
        }
    }
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

/// The stem every name of Lemma's run-scoped MCP server starts with.
///
/// The server is registered under the name Lemma publishes for the run —
/// `lemma_tools` — and this is the stem shared by that name and the one older
/// hosts registered (`lemma`), which is what the matcher below anchors on. It
/// is also the fallback `runtime.rs` registers under when a run carries no
/// server name of its own.
pub(crate) const SCOPED_MCP_SERVER: &str = "lemma";

/// Is this the agent asking permission to use one of *our* tools?
///
/// Lemma's own MCP tools are already scoped to the run and authorised by the
/// workspace, so prompting for each one is noise the user has to click through
/// on every single call.
///
/// Three ways to tell, because no one of them is enough.
///
/// The `_meta` flag is a Claude Code convention; an agent that does not set it —
/// `OpenCode` does not — had every Lemma tool call raise an approval card. The
/// tool name is checked too, against the server name we registered ourselves, in
/// the shapes agents actually namespace MCP tools with.
///
/// Both of those can still miss: ACP's `ToolCall` carries no tool-name field at
/// all (only `title`, which the agent writes for humans), so an adapter that
/// reports `"Read table"` and no `_meta` matches neither. The third check closes
/// that: Lemma publishes the exact tool names it serves in the run's MCP
/// configuration, so a title that *is* one of those names is ours.
fn is_scoped_mcp_tool_approval(
    request: &RequestPermissionRequest,
    scoped_tools: &HashSet<String>,
) -> bool {
    let Ok(value) = serde_json::to_value(request) else {
        return false;
    };
    let flagged = value
        .get("_meta")
        .and_then(|meta| meta.get("is_mcp_tool_approval"))
        .and_then(Value::as_bool)
        .unwrap_or(false);
    flagged || names_scoped_mcp_tool(&value) || names_known_scoped_tool(&value, scoped_tools)
}

/// The tool names Lemma said it serves on this run's MCP endpoint.
///
/// Absent for an older control plane that does not publish them, in which case
/// this check simply contributes nothing and the other two still apply.
fn scoped_mcp_tool_names(mcp: &Value) -> HashSet<String> {
    mcp.get("tool_names")
        .and_then(Value::as_array)
        .map(|names| {
            names
                .iter()
                .filter_map(Value::as_str)
                .map(|name| name.trim().to_ascii_lowercase())
                .filter(|name| !name.is_empty())
                .collect()
        })
        .unwrap_or_default()
}

/// Does the request name a tool Lemma told us it serves?
///
/// Exact match against the published set, after stripping whatever namespacing
/// the agent added, so this cannot sweep in a same-named tool of the user's.
fn names_known_scoped_tool(value: &Value, scoped_tools: &HashSet<String>) -> bool {
    if scoped_tools.is_empty() {
        return false;
    }
    permission_tool_candidates(value).any(|name| {
        let name = name.trim().to_ascii_lowercase();
        let bare = name
            .rsplit_once("__")
            .map_or(name.as_str(), |(_, tail)| tail)
            .trim();
        scoped_tools.contains(name.as_str()) || scoped_tools.contains(bare)
    })
}

/// Every field of a permission request that might carry the tool's identity.
fn permission_tool_candidates(value: &Value) -> impl Iterator<Item = &str> {
    [
        value.pointer("/toolCall/title").and_then(Value::as_str),
        value.pointer("/toolCall/toolName").and_then(Value::as_str),
        value.pointer("/toolCall/name").and_then(Value::as_str),
        value
            .pointer("/toolCall/toolCallId")
            .and_then(Value::as_str),
        value.get("toolName").and_then(Value::as_str),
    ]
    .into_iter()
    .flatten()
}

/// The always-allow this request offers, named by the scope it would grant.
///
/// The agent writes that name from the permission rules it would install — the
/// difference between "Always Allow all Bash" and "Always Allow
/// WebFetch(domain:github.com)" is the whole grant — so it is both what the
/// user is shown and what a later request has to match to be covered by it.
fn always_allow_offer(request: &RequestPermissionRequest) -> Option<AlwaysAllowOffer> {
    request
        .options
        .iter()
        .find(|option| option.kind == PermissionOptionKind::AllowAlways)
        .map(|option| AlwaysAllowOffer {
            scope: AlwaysAllowScope {
                session_id: request.session_id.to_string(),
                label: option.name.trim().to_owned(),
            },
            option_id: option.option_id.to_string(),
        })
}

/// Does the tool being requested belong to Lemma's own MCP server?
///
/// Agents namespace MCP tools differently — `mcp__lemma__read_table`,
/// `lemma__read_table`, `lemma.read_table`, `lemma/read_table` — so this looks
/// for the server name followed by a separator rather than assuming one
/// convention. Anchored at the start so a user's tool that merely mentions
/// "lemma" somewhere does not get silently auto-approved.
fn names_scoped_mcp_tool(value: &Value) -> bool {
    permission_tool_candidates(value).any(|name| {
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

    /// ACP names five ways a turn can end. Three of them used to arrive as an
    /// undifferentiated `FAILED` with no detail, so a run that merely ran out
    /// of context told the user only that it had failed.
    #[test]
    fn every_acp_stop_reason_is_reported_distinguishably() {
        assert_eq!(outcome_for("end_turn"), (RunState::Succeeded, None));
        assert_eq!(outcome_for("cancelled"), (RunState::Cancelled, None));

        for reason in ["max_tokens", "max_turn_requests", "refusal"] {
            let (state, message) = outcome_for(reason);
            assert_eq!(state, RunState::Failed, "{reason} did not fail");
            assert!(
                message.is_some_and(|message| !message.is_empty()),
                "{reason} gave the user nothing to act on"
            );
        }
    }

    /// A cancelled run is cancelled, whatever the agent calls it.
    ///
    /// `OpenCode` ends a turn stopped by `session/cancel` with `end_turn`
    /// rather than the `cancelled` ACP requires — observed against the real
    /// agent. Mapping that literally recorded a run the user had stopped as
    /// SUCCEEDED, with a truncated answer standing in for the whole one.
    #[test]
    fn a_run_we_asked_to_stop_is_cancelled_whatever_the_agent_reports() {
        for reported in ["cancelled", "end_turn", "max_tokens", "unknown"] {
            assert_eq!(
                run_outcome(true, reported),
                (RunState::Cancelled, None),
                "a cancelled run reported as {reported:?} was not recorded as cancelled"
            );
        }
    }

    /// The override must not reach a turn nobody interrupted, or an ordinary
    /// failure would be filed as a user cancellation.
    #[test]
    fn a_run_nobody_stopped_keeps_its_own_stop_reason() {
        assert_eq!(run_outcome(false, "end_turn"), (RunState::Succeeded, None));
        assert_eq!(run_outcome(false, "max_tokens").0, RunState::Failed);
    }

    /// A reason this build has never heard of must still say something, and
    /// must name what it saw rather than swallowing it.
    #[test]
    fn an_unknown_stop_reason_still_explains_itself() {
        let (state, message) = outcome_for("some_future_reason");
        assert_eq!(state, RunState::Failed);
        assert!(message.is_some_and(|message| message.contains("some_future_reason")));
    }

    /// `PromptRequest` takes a `Vec<ContentBlock>` so that non-text content can
    /// travel. Flattening the whole prompt to one string meant an image or an
    /// embedded resource was not degraded but discarded.
    #[test]
    fn a_non_text_prompt_block_survives_into_the_request() {
        let mut spec = spec_resuming(None);
        spec.system_prompt = "Be brief.".to_owned();
        spec.prompt = vec![
            serde_json::json!({"type": "text", "text": "What is in this picture?"}),
            serde_json::json!({
                "type": "image",
                "data": "aGk=",
                "mimeType": "image/png",
            }),
        ];

        let blocks = prompt_blocks(&spec);

        assert_eq!(blocks.len(), 2, "expected the text block and the image");
        assert!(matches!(blocks[0], ContentBlock::Text(_)));
        assert!(
            matches!(blocks[1], ContentBlock::Image(_)),
            "the image was dropped rather than sent"
        );
    }

    /// The text half must keep behaving exactly as it did, since every
    /// certified adapter has been reading that one assembled block.
    #[test]
    fn text_blocks_are_still_assembled_into_one_leading_block() {
        let mut spec = spec_resuming(None);
        spec.system_prompt = "Be brief.".to_owned();
        spec.prompt = vec![serde_json::json!({"type": "text", "text": "Hello."})];

        let blocks = prompt_blocks(&spec);

        assert_eq!(blocks.len(), 1);
        let ContentBlock::Text(text) = &blocks[0] else {
            panic!("expected a text block");
        };
        assert!(text.text.contains("Be brief."));
        assert!(text.text.contains("Hello."));
    }

    /// A block shape this build cannot parse must not take the turn down with
    /// it, and must be visible in the logs rather than vanishing quietly.
    #[test]
    fn an_unparseable_block_is_skipped_not_fatal() {
        let mut spec = spec_resuming(None);
        spec.prompt = vec![
            serde_json::json!({"type": "text", "text": "Hello."}),
            serde_json::json!({"type": "something_new", "payload": 1}),
        ];

        let blocks = prompt_blocks(&spec);

        assert_eq!(blocks.len(), 1);
    }

    /// The three explained failures must not read alike, or the distinction
    /// exists only in the code.
    #[test]
    fn the_explanations_differ_from_each_other() {
        let messages = ["max_tokens", "max_turn_requests", "refusal"]
            .map(|reason| outcome_for(reason).1.unwrap());
        assert_ne!(messages[0], messages[1]);
        assert_ne!(messages[1], messages[2]);
        assert_ne!(messages[0], messages[2]);
    }

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

        assert!(is_scoped_mcp_tool_approval(&request, &HashSet::new()));
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

        assert!(!is_scoped_mcp_tool_approval(&request, &HashSet::new()));
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
    use std::collections::HashSet;

    use agent_client_protocol::schema::v1::RequestPermissionRequest;
    use serde_json::json;

    use super::{is_scoped_mcp_tool_approval, names_scoped_mcp_tool, scoped_mcp_tool_names};

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

    fn permission_request_titled(title: &str) -> RequestPermissionRequest {
        serde_json::from_value(json!({
            "sessionId": "session",
            "toolCall": {
                "kind": "execute",
                "status": "pending",
                "toolCallId": "call-1",
                "title": title
            },
            "options": [
                {"kind": "allow_once", "name": "Allow", "optionId": "allow_once"},
                {"kind": "reject_once", "name": "Decline", "optionId": "decline"}
            ]
        }))
        .unwrap()
    }

    fn published(names: &[&str]) -> HashSet<String> {
        scoped_mcp_tool_names(&json!({"tool_names": names}))
    }

    #[test]
    fn a_tool_lemma_published_is_approved_even_titled_for_a_human() {
        // The gap this closes. ACP's ToolCall has no tool-name field, so an
        // adapter that reports only a human-readable title matches neither the
        // `_meta` flag nor the name-shape check — and every Lemma tool call
        // raised an approval card the user had to clear.
        let request = permission_request_titled("lemma_read_table");
        let scoped = published(&["lemma_read_table", "lemma_write_record"]);

        assert!(is_scoped_mcp_tool_approval(&request, &scoped));
    }

    #[test]
    fn a_published_tool_is_recognised_through_the_agents_namespacing() {
        let scoped = published(&["lemma_final_answer"]);
        for title in [
            "lemma_final_answer",
            "mcp__lemma__lemma_final_answer",
            "LEMMA_FINAL_ANSWER",
        ] {
            assert!(
                is_scoped_mcp_tool_approval(&permission_request_titled(title), &scoped),
                "{title} is ours",
            );
        }
    }

    #[test]
    fn a_tool_lemma_never_published_still_needs_a_human() {
        // The published list must not become a blanket approval: a native tool
        // is exactly what the approval card exists for.
        let scoped = published(&["lemma_read_table"]);

        assert!(!is_scoped_mcp_tool_approval(
            &permission_request_titled("Run shell command"),
            &scoped
        ));
        assert!(!is_scoped_mcp_tool_approval(
            &permission_request_titled("bash"),
            &scoped
        ));
    }

    #[test]
    fn an_absent_published_list_falls_back_to_the_other_checks() {
        // An older control plane does not publish tool_names; the name-shape
        // check still has to carry the common case.
        let empty = HashSet::new();

        assert!(is_scoped_mcp_tool_approval(
            &permission_request_titled("mcp__lemma__read_table"),
            &empty
        ));
        assert!(!is_scoped_mcp_tool_approval(
            &permission_request_titled("bash"),
            &empty
        ));
    }
}
