//! Generic ACP v1 adapter driver.

use std::path::PathBuf;
use std::sync::Arc;

use agent_client_protocol::schema::ProtocolVersion;
use agent_client_protocol::schema::v1::{
    ContentBlock, InitializeRequest, McpServer, NewSessionRequest, PermissionOptionKind,
    PromptRequest, RequestPermissionOutcome, RequestPermissionRequest, RequestPermissionResponse,
    SelectedPermissionOutcome, SessionConfigOption, SessionConfigOptionValue, SessionNotification,
    SetSessionConfigOptionRequest, TextContent,
};
use agent_client_protocol::{AcpAgent, AcpAgentConfig, Agent, ConnectionTo};
use async_trait::async_trait;
use serde_json::{Map, Value};

use crate::adapters::ResolvedAdapter;
use crate::protocol::{ConfigOption, EventType, JsonMap, RunSpec, RunState};

#[derive(Clone)]
pub struct AcpRunRequest {
    pub adapter: ResolvedAdapter,
    pub run_spec: RunSpec,
    pub scratch_directory: PathBuf,
    pub mcp_server: Option<McpServer>,
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
        let run_spec = request.run_spec;
        let scratch_directory = request.scratch_directory;
        let mcp_server = request.mcp_server;
        let has_scoped_mcp_server = mcp_server.is_some();
        let outcome = agent_client_protocol::Client
            .builder()
            .name("lemma-agent-host")
            .on_receive_notification(
                async move |notification: SessionNotification, _context| {
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
                    let _ = permission_callbacks.event(
                        EventType::PermissionRequest,
                        tool_call_id(&payload),
                        payload,
                    );
                    responder.respond(RequestPermissionResponse::new(
                        RequestPermissionOutcome::Cancelled,
                    ))
                },
                agent_client_protocol::on_receive_request!(),
            )
            .connect_with(agent, |connection: ConnectionTo<Agent>| async move {
                connection
                    .send_request(InitializeRequest::new(ProtocolVersion::V1))
                    .block_task()
                    .await?;
                let mcp_servers = mcp_server.into_iter().collect();
                let session = connection
                    .send_request(
                        NewSessionRequest::new(scratch_directory).mcp_servers(mcp_servers),
                    )
                    .block_task()
                    .await?;
                let session_id = session.session_id.clone();
                let safe_options = session
                    .config_options
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
            "Lemma Agent Host denied a native permission request; only the scoped Lemma MCP server is allowed."
                .to_owned(),
        ),
    );
    object
        .iter()
        .map(|(key, value)| (key.clone(), value.clone()))
        .collect()
}

fn is_scoped_mcp_tool_approval(request: &RequestPermissionRequest) -> bool {
    serde_json::to_value(request)
        .ok()
        .and_then(|value| value.get("_meta").cloned())
        .and_then(|meta| meta.get("is_mcp_tool_approval").cloned())
        .and_then(|value| value.as_bool())
        .unwrap_or(false)
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

fn find_string(object: &Map<String, Value>, keys: &[&str]) -> Option<String> {
    keys.iter()
        .find_map(|key| object.get(*key).and_then(Value::as_str).map(str::to_owned))
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

    #[test]
    fn prompt_rendering_keeps_system_and_text() {
        let spec = RunSpec {
            agent_run_id: uuid::Uuid::new_v4(),
            conversation_id: uuid::Uuid::new_v4(),
            harness_id: uuid::Uuid::new_v4(),
            profile_revision: "r".into(),
            model_name: None,
            config_selections: JsonMap::new(),
            system_prompt: "Be exact.".into(),
            prompt: vec![serde_json::json!({"type": "text", "text": "Hello"})],
            context: JsonMap::new(),
            mcp_route_id: uuid::Uuid::new_v4(),
            run_deadline: chrono::Utc::now(),
        };
        assert_eq!(
            render_prompt(&spec),
            "<system>\nBe exact.\n</system>\n\nHello"
        );
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
