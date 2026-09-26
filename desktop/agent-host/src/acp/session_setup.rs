//! Everything a run does between `initialize` and the end of its turn: which
//! provider session it holds, the configuration that session is given, and
//! the prompt itself.

use agent_client_protocol::schema::v1::SessionId;

use super::{
    AcpCallbacks, AcpRunOutcome, Agent, CancelNotification, ConfigOption, ConnectionTo,
    ContentBlock, Duration, EventType, JsonMap, LoadSessionRequest, McpServer, NewSessionRequest,
    PathBuf, PromptRequest, SessionConfigOption, SessionConfigOptionValue, SessionOrigin,
    SetSessionConfigOptionRequest, SteerEvent, TurnSteering, Value, before_prompt_deadline,
    cancel_requested, convert_config_option, model_unavailable_payload, run_outcome,
    selection_is_allowed, session_config_value, session_lost_payload, watch,
};

pub(crate) type AcpError = agent_client_protocol::schema::v1::Error;

/// A failure on Lemma's side of the connection, in the form ACP can carry.
pub(crate) fn internal(error: impl std::fmt::Display) -> AcpError {
    AcpError::internal_error().data(error.to_string())
}

pub(crate) fn invalid(message: impl Into<String>) -> AcpError {
    AcpError::invalid_params().data(message.into())
}

/// The provider session a run ended up holding, and how it came by it.
pub(crate) struct OpenedSession {
    pub(crate) session_id: SessionId,
    pub(crate) config_options: Option<Vec<SessionConfigOption>>,
    /// Captured against the branch it describes, so the two cannot drift. It
    /// says what the agent is actually holding -- and a load that failed
    /// leaves a session that is new no matter what Lemma expected, which is
    /// exactly the case that has to keep its instructions.
    pub(crate) origin: SessionOrigin,
    /// The session Lemma asked to resume and the provider no longer had.
    pub(crate) lost_session: Option<String>,
}

/// Resume the conversation's provider session, or open a new one.
///
/// A Lemma conversation is one provider session: resuming is what lets the
/// agent answer "what did I just say" instead of meeting the user again every
/// turn.
pub(crate) async fn open_session(
    connection: &ConnectionTo<Agent>,
    resume: Option<String>,
    scratch_directory: PathBuf,
    mcp_servers: Vec<McpServer>,
    meta: Option<serde_json::Map<String, Value>>,
) -> Result<OpenedSession, AcpError> {
    let mut lost_session = None;
    if let Some(existing) = resume {
        // The same `_meta` as a new session: an adapter reads it on either
        // (see `session_options`), and a resumed session is given its
        // instructions and settings afresh by the process this run started.
        let load = LoadSessionRequest::new(existing.clone(), scratch_directory.clone())
            .mcp_servers(mcp_servers.clone())
            .meta(meta.clone());
        match before_prompt_deadline("session/load", connection.send_request(load).block_task())
            .await
        {
            Ok(loaded) => {
                return Ok(OpenedSession {
                    session_id: existing.into(),
                    config_options: loaded.config_options,
                    origin: SessionOrigin::Loaded,
                    lost_session: None,
                });
            }
            // A provider is free to forget a session — Codex prunes its
            // rollout files, a Claude Code session can be deleted from disk.
            // Losing history is survivable; losing the answer is not, so a
            // failed load starts fresh.
            //
            // Not silently, though. See `SessionOrigin::Recovered`: the fresh
            // session it leaves behind is the one case where the prompt has no
            // history either, so the agent is told and so is Lemma.
            Err(error) => {
                tracing::warn!(
                    %error,
                    "could not resume the conversation's provider session; starting a new one"
                );
                lost_session = Some(existing);
            }
        }
    }
    let origin = if lost_session.is_some() {
        SessionOrigin::Recovered
    } else {
        SessionOrigin::New
    };
    let session = before_prompt_deadline(
        "session/new",
        connection
            .send_request(
                NewSessionRequest::new(scratch_directory)
                    .mcp_servers(mcp_servers)
                    .meta(meta),
            )
            .block_task(),
    )
    .await?;
    Ok(OpenedSession {
        session_id: session.session_id,
        config_options: session.config_options,
        origin,
        lost_session,
    })
}

/// The options this run validates against: what the session reported, or,
/// when it reported nothing, what the probe published.
///
/// `configOptions` is optional on `session/load`, and a Lemma conversation
/// resumes on every turn after its first. An adapter that reports its models
/// on `session/new` and nothing on `session/load` therefore left the second
/// turn looking for a model in an empty list -- and failing the run over it.
/// The published options are the answer Lemma validated the profile against.
pub(crate) fn effective_options(
    adapter_key: &str,
    reported: Option<Vec<SessionConfigOption>>,
    published: Vec<ConfigOption>,
    origin: SessionOrigin,
) -> Vec<ConfigOption> {
    let reported = reported
        .unwrap_or_default()
        .iter()
        .filter_map(|option| convert_config_option(adapter_key, option))
        .collect::<Vec<_>>();
    if !reported.is_empty() {
        return reported;
    }
    if !published.is_empty() {
        tracing::info!(
            harness = %adapter_key,
            published = published.len(),
            origin = origin.as_str(),
            "session reported no configuration; using the probed options"
        );
    }
    published
}

/// One `session/set_config_option` a run will send.
pub(crate) struct ConfigWrite {
    pub(crate) option_id: String,
    pub(crate) value: SessionConfigOptionValue,
}

pub(crate) enum ModelChoice {
    /// The profile names no model; the harness's own default stands.
    Unrequested,
    Set(ConfigWrite),
    /// The harness does not offer the requested model. Reported, not raised:
    /// nothing about it is the user's doing -- an agent renamed its models
    /// between releases, or resumed a session that reports fewer than it
    /// opened with -- and the answer they asked for is still available on the
    /// harness's own default. Losing the turn over the label was the worse of
    /// the two outcomes.
    Unavailable(JsonMap),
}

/// Every configuration write a run makes, decided before any is sent.
///
/// Planned as a whole so that a selection Lemma cannot apply fails the run
/// before the session has been half-configured, rather than after.
pub(crate) struct ConfigurationPlan {
    /// A user's provider configuration may have been left in an unrestricted
    /// mode outside Lemma. Every session resets a policy-bearing option to a
    /// known safe value before applying the profile's validated selections.
    pub(crate) policy_resets: Vec<ConfigWrite>,
    pub(crate) model: ModelChoice,
    pub(crate) selections: Vec<ConfigWrite>,
    /// Selections this agent does not offer, each as the `config_update`
    /// that says so. Reported and skipped: the turn is still worth having on
    /// the agent's own setting, as with a model it no longer offers.
    pub(crate) skipped: Vec<JsonMap>,
    /// What was asked for, so the selections can be planned again against
    /// the options the agent reports once the model is set -- which a model
    /// switch changes (a reasoning level one model has and another lacks).
    pub(crate) requested: JsonMap,
    pub(crate) adapter_key: String,
}

/// How long one configuration write may take. An agent that never answers
/// one would otherwise hold the run before its prompt was ever sent.
pub(crate) const SET_OPTION_DEADLINE: Duration = Duration::from_secs(30);

pub(crate) fn plan_configuration(
    options: &[ConfigOption],
    model_name: Option<&str>,
    selections: &JsonMap,
) -> Result<ConfigurationPlan, String> {
    let policy_resets = options
        .iter()
        .filter(|option| {
            option
                .metadata
                .get("hostPolicyDefaultOverride")
                .and_then(Value::as_bool)
                .unwrap_or(false)
        })
        .map(|option| {
            Ok(ConfigWrite {
                option_id: option.id.clone(),
                value: session_config_value(&option.id, &option.current_value)?,
            })
        })
        .collect::<Result<Vec<_>, String>>()?;
    let model = match model_name {
        None => ModelChoice::Unrequested,
        Some(model_name) => options
            .iter()
            .find(|option| option.category == "model")
            .filter(|option| selection_is_allowed(option, &Value::String(model_name.to_owned())))
            .map_or_else(
                || ModelChoice::Unavailable(model_unavailable_payload(model_name, options)),
                |option| {
                    ModelChoice::Set(ConfigWrite {
                        option_id: option.id.clone(),
                        value: SessionConfigOptionValue::value_id(model_name.to_owned()),
                    })
                },
            ),
    };
    let (writes, skipped) = plan_selections(options, selections)?;
    Ok(ConfigurationPlan {
        policy_resets,
        model,
        selections: writes,
        skipped,
        requested: selections.clone(),
        adapter_key: String::new(),
    })
}

/// Each requested selection as a write, or -- when this agent does not offer
/// that option or that value -- as the `config_update` that says so.
///
/// Only a selection Lemma itself got wrong fails the run: a model sent as a
/// selection, or a value of a type no option can take. An option or value the
/// agent does not offer is the agent's version talking, not Lemma's mistake,
/// and failing the turn over it cost the answer for the sake of a setting.
/// Policy resets have already put every policy-bearing option somewhere safe,
/// so a skipped selection leaves the session there.
pub(crate) fn plan_selections(
    options: &[ConfigOption],
    selections: &JsonMap,
) -> Result<(Vec<ConfigWrite>, Vec<JsonMap>), String> {
    let mut writes = Vec::new();
    let mut skipped = Vec::new();
    for (key, selection) in selections {
        match plan_selection(options, key, selection)? {
            Ok(write) => writes.push(write),
            Err(payload) => skipped.push(payload),
        }
    }
    Ok((writes, skipped))
}

fn plan_selection(
    options: &[ConfigOption],
    key: &str,
    selection: &Value,
) -> Result<Result<ConfigWrite, JsonMap>, String> {
    let value = session_config_value(key, selection)?;
    let Some(option) = options
        .iter()
        .find(|option| option.id == key || option.category == key)
    else {
        return Ok(Err(selection_skipped_payload(
            key,
            selection,
            "this agent does not offer that setting",
        )));
    };
    if option.category == "model" {
        return Err("model must be supplied through model_name".to_owned());
    }
    if !selection_is_allowed(option, selection) {
        return Ok(Err(selection_skipped_payload(
            key,
            selection,
            "this agent does not offer that value, or it is not one Lemma allows",
        )));
    }
    Ok(Ok(ConfigWrite {
        option_id: option.id.clone(),
        value,
    }))
}

/// The `config_update` for a selection that was not applied.
fn selection_skipped_payload(key: &str, selection: &Value, why: &str) -> JsonMap {
    let mut payload = JsonMap::new();
    payload.insert("status".to_owned(), Value::from("selection_unavailable"));
    payload.insert("option".to_owned(), Value::from(key));
    payload.insert("requested_value".to_owned(), selection.clone());
    payload.insert(
        "detail".to_owned(),
        Value::from(format!(
            "The profile asks for {key} = {selection}, but {why}. This turn used the agent's own setting."
        )),
    );
    payload
}

/// Send the plan, and tell Lemma what it could not honour.
pub(crate) async fn configure_session(
    connection: &ConnectionTo<Agent>,
    session: &OpenedSession,
    plan: ConfigurationPlan,
    callbacks: &dyn AcpCallbacks,
) -> Result<(), AcpError> {
    for write in plan.policy_resets {
        set_option(connection, &session.session_id, write).await?;
    }
    // Said to Lemma as well as to the agent. The prompt note tells the agent
    // it is missing the conversation; this is what lets a person see why the
    // answer they got starts from nothing.
    if let Some(lost) = session.lost_session.as_deref() {
        callbacks
            .event(EventType::ConfigUpdate, None, session_lost_payload(lost))
            .map_err(internal)?;
    }
    let (mut selections, mut skipped) = (plan.selections, plan.skipped);
    match plan.model {
        ModelChoice::Unrequested => {}
        ModelChoice::Set(write) => {
            let after = set_option(connection, &session.session_id, write).await?;
            // The options a model offers are that model's: plan again against
            // what the agent reports now, not what it offered before.
            let now: Vec<ConfigOption> = after
                .iter()
                .filter_map(|option| convert_config_option(&plan.adapter_key, option))
                .collect();
            if !now.is_empty() {
                (selections, skipped) = plan_selections(&now, &plan.requested).map_err(invalid)?;
            }
        }
        ModelChoice::Unavailable(payload) => callbacks
            .event(EventType::ConfigUpdate, None, payload)
            .map_err(internal)?,
    }
    for payload in skipped {
        callbacks
            .event(EventType::ConfigUpdate, None, payload)
            .map_err(internal)?;
    }
    for write in selections {
        set_option(connection, &session.session_id, write).await?;
    }
    Ok(())
}

/// One configuration write, bounded by `SET_OPTION_DEADLINE`. Answers with
/// the options the agent reports afterwards.
async fn set_option(
    connection: &ConnectionTo<Agent>,
    session_id: &SessionId,
    write: ConfigWrite,
) -> Result<Vec<SessionConfigOption>, AcpError> {
    let option_id = write.option_id.clone();
    let response = tokio::time::timeout(
        SET_OPTION_DEADLINE,
        connection
            .send_request(SetSessionConfigOptionRequest::new(
                session_id.clone(),
                write.option_id,
                write.value,
            ))
            .block_task(),
    )
    .await
    .map_err(|_| {
        internal(format!(
            "the agent did not answer setting {option_id} within {SET_OPTION_DEADLINE:?}"
        ))
    })??;
    Ok(response.config_options)
}

/// Send the prompt and wait for the turn to end, asking the agent to stop if
/// Lemma cancels.
///
/// A cancel is asked for, not inflicted. Killing the process here is what the
/// supervisor falls back to, and it is the worst moment to do it: the provider
/// has not flushed the session file the *next* turn resumes from, so killing it
/// can cost the conversation its whole history.
pub(crate) async fn prompt_turn(
    connection: &ConnectionTo<Agent>,
    session_id: SessionId,
    blocks: Vec<ContentBlock>,
    cancel: &mut watch::Receiver<bool>,
    cancel_grace: Duration,
    mut steering: TurnSteering<'_>,
) -> Result<AcpRunOutcome, AcpError> {
    let turn = connection
        .send_request(PromptRequest::new(session_id.clone(), blocks))
        .block_task();
    tokio::pin!(turn);
    let mut asked_to_stop = false;
    // One loop rather than one `select!`, because a turn can now be told
    // things while it runs: every steer Lemma sends goes out as it arrives,
    // and every answer is recorded as it comes back, until the turn ends.
    let response = loop {
        tokio::select! {
            // A stop outranks everything. Then steering, before the turn: an
            // adapter answers a steer before the prompt it joined, and the
            // connection hands responses over in the order they arrived -- so
            // when both are ready, taking the turn first would report a
            // message the agent did see as one it did not.
            biased;
            () = cancel_requested(cancel) => {
                connection.send_notification(CancelNotification::new(session_id.clone()))?;
                asked_to_stop = true;
                break tokio::time::timeout(cancel_grace, &mut turn)
                    .await
                    .map_err(|_| internal(
                        "the agent did not stop within the cancellation grace period",
                    ))?;
            }
            next = steering.next() => match next {
                SteerEvent::Arrived(steer) => steering.send(connection, &session_id, steer),
                SteerEvent::Answered(message_id, answer) => {
                    if steering.settle(&message_id, answer) {
                        // The adapter started a turn of its own for a message
                        // that arrived too late for this one. Stop it: nothing
                        // will read what it writes. See `steering`.
                        connection
                            .send_notification(CancelNotification::new(session_id.clone()))?;
                    }
                }
            },
            result = &mut turn => break result,
        }
    };
    steering.finish();
    let response = response?;
    let stop_reason = serde_json::to_value(response.stop_reason)
        .ok()
        .and_then(|value| value.as_str().map(str::to_owned))
        .unwrap_or_else(|| "unknown".to_owned());
    let (state, message) = run_outcome(asked_to_stop, &stop_reason);
    // ACP's end-of-turn token count. Still marked unstable in the protocol,
    // and it is the only place an adapter reports tokens at all: the
    // `usage_update` notification is the context window, not usage.
    let usage = response
        .usage
        .as_ref()
        .and_then(|usage| serde_json::to_value(usage).ok());
    Ok(AcpRunOutcome {
        provider_session_id: session_id.to_string(),
        state,
        stop_reason,
        message,
        usage,
    })
}
