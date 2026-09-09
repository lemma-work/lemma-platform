//! Driving one ACP agent through a run.

use super::{
    AcpCallbacks, AcpProbeOutcome, AcpRunOutcome, AcpRunRequest, Agent, AgentDriver, Arc,
    AtomicBool, AtomicU64, CancelNotification, ConnectionTo, EventType, InitializeRequest,
    LoadSessionRequest, Map, McpServer, NewSessionRequest, Ordering, PathBuf, PermissionDecision,
    PermissionOptionKind, PromptRequest, ProtocolVersion, RequestPermissionOutcome,
    RequestPermissionRequest, RequestPermissionResponse, ResolvedAdapter,
    SelectedPermissionOutcome, SessionConfigOptionValue, SessionNotification, SessionOrigin,
    SetSessionConfigOptionRequest, SupervisedAgent, Value, always_allow_offer, async_trait,
    before_prompt_deadline, build_agent, cancel_requested, capture_stderr, convert_config_option,
    is_scoped_mcp_tool_approval, model_unavailable_payload, normalize_session_update,
    permission_payload, prompt_blocks, run_outcome, scoped_mcp_tool_names, selection_is_allowed,
    session_config_value, session_to_resume, tool_call_id,
};

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
        let (mut supervised, transport, stderr) = SupervisedAgent::spawn(&agent)?;
        let stderr = capture_stderr(stderr);
        let outcome = agent_client_protocol::Client
            .builder()
            .name("lemma-agent-host-probe")
            .connect_with(transport, |connection: ConnectionTo<Agent>| async move {
                let initialization = before_prompt_deadline(
                    "initialize",
                    connection
                        .send_request(InitializeRequest::new(ProtocolVersion::V1))
                        .block_task(),
                )
                .await?;
                let session = connection
                    .send_request(NewSessionRequest::new(scratch_directory))
                    .block_task()
                    .await?;
                let auth_methods = serde_json::to_value(&initialization.auth_methods)
                    .unwrap_or_else(|_| Value::Array(Vec::new()));
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
                    auth_methods,
                })
            })
            .await
            .map_err(anyhow::Error::from);
        // The protocol is done with the process, so stdout has reached EOF and
        // everything the agent sent has been dispatched. Only now may the exit
        // status speak.
        match outcome {
            Ok(outcome) => Ok(outcome),
            Err(error) => Err(supervised.explain(&error, stderr).await),
        }
    }

    async fn run(
        &self,
        request: AcpRunRequest,
        callbacks: Arc<dyn AcpCallbacks>,
    ) -> anyhow::Result<AcpRunOutcome> {
        std::fs::create_dir_all(&request.scratch_directory)?;
        let adapter_key = request.adapter.spec.key.clone();
        let agent = build_agent(&request.adapter);
        let (mut supervised, transport, stderr) = SupervisedAgent::spawn(&agent)?;
        let stderr = capture_stderr(stderr);
        let notification_callbacks = Arc::clone(&callbacks);
        let permission_callbacks = Arc::clone(&callbacks);
        let permission_gate = request.permissions.clone();
        let permission_sequence = Arc::new(AtomicU64::new(0));
        let permission_timeout = request.permission_timeout;
        let permission_run_id = request.run_spec.agent_run_id;
        let can_load_session = request.can_load_session;
        let published_config_options = request.published_config_options;
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
        // Which session this run's transcript belongs to.
        //
        // ACP carries a `sessionId` on every update and nothing here read it,
        // so an adapter holding more than one session open would have written
        // another conversation's output into this one's transcript. Set once
        // the session is established, and only updates naming it are journaled.
        let turn_session: Arc<std::sync::OnceLock<String>> = Arc::new(std::sync::OnceLock::new());
        let notification_session = Arc::clone(&turn_session);
        let outcome = agent_client_protocol::Client
            .builder()
            .name("lemma-agent-host")
            .on_receive_notification(
                async move |notification: SessionNotification, _context| {
                    if !notification_streaming.load(Ordering::SeqCst) {
                        return Ok(());
                    }
                    // An update for a session this run does not own belongs to
                    // somebody else's transcript, not the end of this one.
                    if let Some(session) = notification_session.get()
                        && notification.session_id.to_string().as_str() != session.as_str()
                    {
                        tracing::warn!(
                            claimed = %notification.session_id,
                            "dropped an ACP update for another session"
                        );
                        return Ok(());
                    }
                    if let Some((event_type, object_id, payload)) =
                        normalize_session_update(&notification.update)
                    {
                        tracing::debug!(?event_type, "ACP notification received");
                        notification_callbacks
                            .event(event_type, object_id, payload)
                            .map_err(|error| {
                                tracing::error!(%error, ?event_type, "could not persist ACP notification");
                                agent_client_protocol::schema::v1::Error::internal_error()
                                    .data(error.to_string())
                            })?;
                        tracing::debug!(?event_type, "ACP notification persisted");
                    }
                    Ok(())
                },
                agent_client_protocol::on_receive_notification!(),
            )
            .on_receive_request(
                async move |request: RequestPermissionRequest, responder, connection| {
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
                    permission_callbacks.event(
                        EventType::PermissionRequest,
                        Some(request_id.clone()),
                        payload,
                    ).map_err(|error| {
                        agent_client_protocol::schema::v1::Error::internal_error()
                            .data(error.to_string())
                    })?;
                    // Handlers share the ACP receive loop. Waiting here blocks
                    // later tool updates, approvals and cancellation responses.
                    // The connection owns this task and drops it on shutdown.
                    let permission_gate = permission_gate.clone();
                    connection.spawn(async move {
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
                    })
                },
                agent_client_protocol::on_receive_request!(),
            )
            .connect_with(transport, |connection: ConnectionTo<Agent>| async move {
                before_prompt_deadline(
                    "initialize",
                    connection
                        .send_request(InitializeRequest::new(ProtocolVersion::V1))
                        .block_task(),
                )
                .await?;
                let mcp_servers: Vec<McpServer> = mcp_server.into_iter().collect();
                // A Lemma conversation is one provider session: resuming is what
                // lets the agent answer "what did I just say" instead of meeting
                // the user again every turn.
                let mut established = None;
                let attempted_resume = resume_session_id.is_some();
                if let Some(existing) = resume_session_id {
                    match before_prompt_deadline(
                        "session/load",
                        connection
                            .send_request(
                                LoadSessionRequest::new(existing.clone(), scratch_directory.clone())
                                    .mcp_servers(mcp_servers.clone()),
                            )
                            .block_task(),
                    )
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
                // Captured against the branch it describes, so the two cannot
                // drift. `attempted_resume` is not the same fact: it says what
                // this run meant to do, and is decided before the load. Only
                // this says what the agent is actually holding — and a load
                // that failed leaves a session that is new no matter what Lemma
                // expected, which is exactly the case that has to keep its
                // instructions.
                let origin = if established.is_some() {
                    SessionOrigin::Loaded
                } else {
                    SessionOrigin::New
                };
                let (session_id, config_options) = if let Some(established) = established {
                    established
                } else {
                    let session = before_prompt_deadline(
                        "session/new",
                        connection
                            .send_request(
                                NewSessionRequest::new(scratch_directory).mcp_servers(mcp_servers),
                            )
                            .block_task(),
                    )
                    .await?;
                    (session.session_id, session.config_options)
                };
                let session_options = config_options
                    .unwrap_or_default()
                    .iter()
                    .filter_map(|option| convert_config_option(&adapter_key, option))
                    .collect::<Vec<_>>();
                // `configOptions` is optional on `session/load`, and a Lemma
                // conversation resumes on every turn after its first. An
                // adapter that reports its models on `session/new` and nothing
                // on `session/load` therefore left the second turn looking for
                // a model in an empty list -- and failing the run over it. Fall
                // back to what the probe published, which is the answer Lemma
                // validated the profile against.
                let safe_options = if session_options.is_empty() {
                    if !published_config_options.is_empty() {
                        tracing::info!(
                            harness = %adapter_key,
                            published = published_config_options.len(),
                            attempted_resume,
                            origin = origin.as_str(),
                            "session reported no configuration; using the probed options"
                        );
                    }
                    published_config_options
                } else {
                    session_options
                };

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
                // A model this harness will not take is a preference we cannot
                // honour, not a reason to lose the turn. Both of these used to
                // fail the run outright -- and the way they were reached was
                // never the user's doing: a coding agent renaming its models
                // between releases, or a `session/load` that reported fewer
                // than `session/new` did. So say what happened and let the
                // agent answer on its own default.
                if let Some(model_name) = &run_spec.model_name {
                    let usable = safe_options
                        .iter()
                        .find(|option| option.category == "model")
                        .filter(|option| {
                            selection_is_allowed(option, &Value::String(model_name.clone()))
                        });
                    if let Some(option) = usable {
                        connection
                            .send_request(SetSessionConfigOptionRequest::new(
                                session_id.clone(),
                                option.id.clone(),
                                SessionConfigOptionValue::value_id(model_name.clone()),
                            ))
                            .block_task()
                            .await?;
                    } else {
                        // Reported, not raised. Nothing about the failure is
                        // the user's doing -- an agent renamed its models
                        // between releases, or resumed a session that reports
                        // fewer than it opened with -- and the answer they
                        // asked for is still available on the harness's own
                        // default. Losing the turn over the label was the
                        // worse of the two outcomes.
                        callbacks
                            .event(
                                EventType::ConfigUpdate,
                                None,
                                model_unavailable_payload(model_name, &safe_options),
                            )
                            .map_err(|error| {
                                agent_client_protocol::schema::v1::Error::internal_error()
                                    .data(error.to_string())
                            })?;
                    }
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
                // Past this point every session update belongs to this turn --
                // as long as it names this session, which is what the handler
                // now checks.
                let _ = turn_session.set(session_id.to_string());
                streaming.store(true, Ordering::SeqCst);
                let turn = connection
                    .send_request(PromptRequest::new(
                        session_id.clone(),
                        prompt_blocks(&run_spec, origin),
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
            .await
            .map_err(anyhow::Error::from);
        // See `SupervisedAgent`: the protocol has read to stdout EOF, so every
        // chunk the agent streamed is already journalled. A non-zero exit
        // explains the failure; it no longer replaces the answer.
        match outcome {
            Ok(outcome) => Ok(outcome),
            Err(error) => Err(supervised.explain(&error, stderr).await),
        }
    }
}
