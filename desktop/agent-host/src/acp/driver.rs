//! Driving one ACP agent through a run.

use super::{
    AcpCallbacks, AcpError, AcpProbeOutcome, AcpRunOutcome, AcpRunRequest, Agent, AgentDriver,
    AlwaysAllowOffer, Arc, AtomicBool, AtomicU64, ConnectionTo, EventType, InitializeRequest, Map,
    McpServer, NewSessionRequest, Ordering, PathBuf, PermissionGate, ProtocolVersion,
    RequestPermissionOutcome, RequestPermissionRequest, RequestPermissionResponse, ResolvedAdapter,
    SelectedPermissionOutcome, SessionNotification, SupervisedAgent, TurnSteering, Value,
    allow_once, always_allow_offer, async_trait, before_prompt_deadline, build_agent,
    capture_stderr, configure_session, convert_config_option, effective_options, internal, invalid,
    is_scoped_mcp_tool_approval, open_session, outcome_for_decision, permission_payload,
    plan_configuration, prompt_blocks, prompt_turn, scoped_mcp_tool_names, session_to_resume,
    steering_advertised, tool_call_id,
};
use super::{
    ConfigOption, Duration, PreApprovals, START_ATTEMPTS, SessionConfigOptionValue,
    SetSessionConfigOptionRequest, StartGates, StartPermit, is_transient_start_failure,
    option_value, session_options,
};
use crate::normalize::{Dialect, Normalizer, RunContext};
use crate::protocol::{MODEL_OPTION_SET_KEY, RunSpec};
use agent_client_protocol::schema::v1::SessionId;

/// One run's normalizer, shared by the update and permission handlers. Both
/// run on the ACP receive loop in order, so the lock is never contended; it
/// is there because the two handlers are separate closures.
type SharedNormalizer = Arc<std::sync::Mutex<Normalizer>>;

/// Drives ACP agents: one process per run, started through the host's
/// start-up gates (see `start_gate`).
#[derive(Clone, Default)]
pub struct AcpDriver {
    starts: Arc<StartGates>,
}

impl AcpDriver {
    /// A driver whose runs start through `starts` rather than the host's
    /// default per-agent gates.
    #[must_use]
    pub fn with_start_gates(starts: StartGates) -> Self {
        Self {
            starts: Arc::new(starts),
        }
    }
}

#[async_trait]
impl AgentDriver for AcpDriver {
    async fn probe(
        &self,
        adapter: ResolvedAdapter,
        scratch_directory: PathBuf,
    ) -> anyhow::Result<AcpProbeOutcome> {
        std::fs::create_dir_all(&scratch_directory)?;
        // A probe asks a binary its version; it gets no credential.
        let agent = build_agent(
            &adapter,
            std::collections::BTreeMap::default(),
            std::collections::BTreeMap::default(),
        );
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
                let steering = steering_advertised(initialization.meta.as_ref());
                let session = connection
                    .send_request(NewSessionRequest::new(scratch_directory))
                    .block_task()
                    .await?;
                let auth_methods = serde_json::to_value(&initialization.auth_methods)
                    .unwrap_or_else(|_| Value::Array(Vec::new()));
                let capabilities = serde_json::to_value(initialization.agent_capabilities)
                    .unwrap_or_else(|_| Value::Object(Map::new()));
                let mut config_options: Vec<ConfigOption> = session
                    .config_options
                    .unwrap_or_default()
                    .iter()
                    .filter_map(|option| convert_config_option(&adapter.spec.key, option))
                    .collect();
                attach_model_option_sets(
                    &connection,
                    &adapter.spec.key,
                    &session.session_id,
                    &mut config_options,
                )
                .await;
                Ok(AcpProbeOutcome {
                    config_options,
                    capabilities,
                    auth_methods,
                    steering,
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
        let harness = request.adapter.spec.key.clone();
        let mut attempt = 1;
        loop {
            let Some(permit) = self.starts.acquire(&harness, request.cancel.clone()).await else {
                return Ok(cancelled_before_start());
            };
            let prompted = Arc::new(AtomicBool::new(false));
            let error = match run_once(
                request.clone(),
                Arc::clone(&callbacks),
                permit,
                Arc::clone(&prompted),
            )
            .await
            {
                Ok(outcome) => return Ok(outcome),
                Err(error) => error,
            };
            let retry = attempt < START_ATTEMPTS
                && !prompted.load(Ordering::SeqCst)
                && !*request.cancel.borrow()
                && is_transient_start_failure(&format!("{error:#}"));
            if !retry {
                return Err(error);
            }
            tracing::warn!(
                %harness,
                attempt,
                %error,
                "the agent failed to start in time; starting it once more"
            );
            attempt += 1;
        }
    }
}

/// The outcome of a run Lemma stopped while it was still waiting to start.
fn cancelled_before_start() -> AcpRunOutcome {
    AcpRunOutcome {
        provider_session_id: String::new(),
        state: crate::protocol::RunState::Cancelled,
        stop_reason: "cancelled".to_owned(),
        message: Some("run cancelled by Lemma before the agent started".to_owned()),
        usage: None,
    }
}

/// One attempt at a run: spawn the agent, open and configure its session,
/// then -- having given the start-up slot back -- prompt it.
///
/// `prompted` is raised just before the prompt is dispatched. Past that point
/// a failure is never tried again, because the agent may have acted on it.
async fn run_once(
    request: AcpRunRequest,
    callbacks: Arc<dyn AcpCallbacks>,
    permit: StartPermit,
    prompted: Arc<AtomicBool>,
) -> anyhow::Result<AcpRunOutcome> {
    std::fs::create_dir_all(&request.scratch_directory)?;
    let session_options = session_options(
        &request.adapter.spec.key,
        &request.adapter.environment(),
        &request.run_spec,
        request.own_settings,
        request.lemma_cli_bin.as_deref(),
        request.mcp_server.as_ref(),
        request.adapter.upstream_version.as_deref(),
    );
    let agent = build_agent(
        &request.adapter,
        request.agent_environment.clone(),
        session_options.environment.clone(),
    );
    let (mut supervised, transport, stderr) = SupervisedAgent::spawn(&agent)?;
    let stderr = capture_stderr(stderr);
    let AcpRunRequest {
        adapter,
        run_spec,
        scratch_directory,
        mcp_server,
        can_load_session,
        published_config_options,
        permissions,
        permission_timeout,
        mut cancel,
        cancel_grace,
        steer,
        ..
    } = request;
    let resume_session_id = session_to_resume(&run_spec, can_load_session);
    // Instructions given in the session's `_meta` are not repeated as a
    // `<system>` block opening the prompt.
    let prompt_spec = if session_options.system_prompt_in_meta {
        RunSpec {
            system_prompt: String::new(),
            ..run_spec.clone()
        }
    } else {
        run_spec.clone()
    };
    let normalizer: SharedNormalizer = Arc::new(std::sync::Mutex::new(Normalizer::new(
        Dialect::for_harness(&adapter.spec.key),
        RunContext::from_mcp(&run_spec.mcp),
    )));
    let updates = UpdateSink {
        streaming: Arc::new(AtomicBool::new(false)),
        session: Arc::new(std::sync::OnceLock::new()),
        callbacks: Arc::clone(&callbacks),
        normalizer: Arc::clone(&normalizer),
    };
    let permission_handler = PermissionHandler {
        gate: permissions,
        sequence: Arc::new(AtomicU64::new(0)),
        timeout: permission_timeout,
        run_id: run_spec.agent_run_id,
        // Lemma tells us, in the run-scoped MCP config, exactly which
        // tools it serves. That is an exact answer to "is this one of
        // ours?", which the name-shape heuristic can only approximate.
        scoped_mcp_tools: mcp_server
            .is_some()
            .then(|| Arc::new(scoped_mcp_tool_names(&run_spec.mcp))),
        callbacks: Arc::clone(&callbacks),
        normalizer: Arc::clone(&normalizer),
        pre_approvals: PreApprovals::of(&run_spec),
    };
    let turn_updates = updates.clone();
    let owed_callbacks = Arc::clone(&callbacks);
    let outcome = agent_client_protocol::Client
        .builder()
        .name("lemma-agent-host")
        // First, so nothing typed sees what an agent newer than this host
        // sent before it has been settled. See `tolerance`.
        .with_handler(super::Tolerance)
        .on_receive_notification(
            async move |notification: SessionNotification, _context| updates.forward(&notification),
            agent_client_protocol::on_receive_notification!(),
        )
        .on_receive_request(
            async move |request: RequestPermissionRequest, responder, connection| {
                let ask = match permission_handler.triage(&request)? {
                    Triage::Answer(outcome) => {
                        return responder.respond(RequestPermissionResponse::new(outcome));
                    }
                    Triage::Ask(ask) => ask,
                };
                // Handlers share the ACP receive loop. Waiting here blocks
                // later tool updates, approvals and cancellation responses.
                // The connection owns this task and drops it on shutdown.
                let gate = permission_handler.gate.clone();
                connection.spawn(async move {
                    let decision = gate
                        .wait(ask.run_id, ask.request_id, ask.timeout, ask.always)
                        .await;
                    responder.respond(RequestPermissionResponse::new(outcome_for_decision(
                        decision,
                        &request.options,
                    )))
                })
            },
            agent_client_protocol::on_receive_request!(),
        )
        .connect_with(transport, |connection: ConnectionTo<Agent>| async move {
            let initialization = before_prompt_deadline(
                "initialize",
                connection
                    .send_request(InitializeRequest::new(ProtocolVersion::V1))
                    .block_task(),
            )
            .await?;
            let steering_supported = steering_advertised(initialization.meta.as_ref());
            // Named once: an agent given the server in its own config would
            // otherwise hold it twice, and the session's entry -- without the
            // limits or `alwaysLoad` the config gave it -- replaces that one.
            let mcp_servers: Vec<McpServer> = mcp_server
                .filter(|_| !session_options.mcp_server_in_agent_config)
                .into_iter()
                .collect();
            let mut session = open_session(
                &connection,
                resume_session_id,
                scratch_directory,
                mcp_servers,
                session_options.meta,
            )
            .await?;
            let options = effective_options(
                &adapter.spec.key,
                session.config_options.take(),
                published_config_options,
                session.origin,
            );
            let mut plan = plan_configuration(
                &options,
                run_spec.model_name.as_deref(),
                &run_spec.config_selections,
            )
            .map_err(invalid)?;
            plan.adapter_key.clone_from(&adapter.spec.key);
            configure_session(&connection, &session, plan, callbacks.as_ref()).await?;
            // Started: the next run of this agent may begin its own.
            drop(permit);
            // Taken only now, so a start that is tried again still has it.
            let steering = TurnSteering::new(steering_supported, steer.take(), callbacks.as_ref());
            prompted.store(true, Ordering::SeqCst);
            callbacks
                .before_prompt(&session.session_id.to_string())
                .map_err(internal)?;
            // Past this point every session update belongs to this turn --
            // as long as it names this session, which is what the handler
            // checks.
            turn_updates.open(&session.session_id.to_string());
            prompt_turn(
                &connection,
                session.session_id,
                prompt_blocks(&prompt_spec, session.origin),
                &mut cancel,
                cancel_grace,
                steering,
            )
            .await
        })
        .await
        .map_err(anyhow::Error::from);
    // Whatever the turn still owes: calls the adapter opened and never
    // settled -- the turn ended, was cancelled, or the adapter died --
    // still happened and still get a card, and the adapter's token count
    // for the turn, when it reported one. Before the terminal event, which
    // the runtime writes once this returns.
    let usage = outcome
        .as_ref()
        .ok()
        .and_then(|outcome| outcome.usage.clone());
    let owed = normalizer
        .lock()
        .expect("normalizer poisoned")
        .finish(usage.as_ref());
    for event in owed {
        if let Err(error) = owed_callbacks.event(event.event_type, event.object_id, event.payload) {
            tracing::error!(%error, "could not persist an event the turn still owed");
        }
    }
    // See `SupervisedAgent`: the protocol has read to stdout EOF, so every
    // chunk the agent streamed is already journalled. A non-zero exit
    // explains the failure; it no longer replaces the answer.
    match outcome {
        Ok(outcome) => Ok(outcome),
        Err(error) => Err(supervised.explain(&error, stderr).await),
    }
}

/// The most models a probe asks for their own options. A catalogue past this
/// is published with the rest left to the top level, rather than holding the
/// probe for one round trip per model.
pub(crate) const MODEL_OPTION_SET_LIMIT: usize = 32;

/// How long one model's options may take to come back. Shorter than a run's
/// setup deadline: nothing waits on this but the probe, and an agent that
/// does not answer one model is not asked about the next.
pub(crate) const MODEL_OPTION_SET_DEADLINE: Duration = Duration::from_secs(10);

/// How long all the models' options may take together.
///
/// The whole probe has 20s (`refresh_harnesses`), and asking each model in
/// turn is most of it: Claude Code answers about one model a second, so its
/// thirteen took 15s of the 20 on an idle Mac -- and on a busy one the probe
/// ran out, and the agent was published as failed rather than with fewer
/// per-model sets. Past this, the rest keep the top level's options.
pub(crate) const MODEL_OPTION_SETS_BUDGET: Duration = Duration::from_secs(8);

/// Give each model the options it offers, under `MODEL_OPTION_SET_KEY` on
/// that model's entry.
///
/// `session/new` describes the machine's default model only, and the options
/// beside it are that model's: Claude's and Codex's adapters publish a
/// different set of reasoning levels once another model is chosen. Publishing
/// only the default's let a profile pick an effort its model does not have,
/// and hid the ones it does. A model without the key is one whose set
/// is the top level's -- which is all a failure here costs, so nothing in it
/// fails the probe.
async fn attach_model_option_sets(
    connection: &ConnectionTo<Agent>,
    adapter_key: &str,
    session_id: &SessionId,
    options: &mut [ConfigOption],
) {
    let Some(model) = options.iter_mut().find(|option| option.category == "model") else {
        return;
    };
    let model_id = model.id.clone();
    let default = model.current_value.as_str().map(str::to_owned);
    let mut last_asked: Option<String> = None;
    let budget_ends = tokio::time::Instant::now() + MODEL_OPTION_SETS_BUDGET;
    for entry in model.options.iter_mut().take(MODEL_OPTION_SET_LIMIT) {
        let remaining = budget_ends.saturating_duration_since(tokio::time::Instant::now());
        if remaining.is_zero() {
            tracing::info!(
                harness = %adapter_key,
                budget_secs = MODEL_OPTION_SETS_BUDGET.as_secs(),
                "out of time for models' own options; the rest keep the top level's"
            );
            break;
        }
        let Some(value) = option_value(entry)
            .and_then(Value::as_str)
            .map(str::to_owned)
        else {
            continue;
        };
        last_asked = Some(value.clone());
        let request = SetSessionConfigOptionRequest::new(
            session_id.clone(),
            model_id.clone(),
            SessionConfigOptionValue::value_id(value.clone()),
        );
        match tokio::time::timeout(
            MODEL_OPTION_SET_DEADLINE.min(remaining),
            connection.send_request(request).block_task(),
        )
        .await
        {
            // An empty answer says nothing about the model, so it is left to
            // the top level rather than published as a model with no options.
            Ok(Ok(response)) if !response.config_options.is_empty() => {
                let set = response
                    .config_options
                    .iter()
                    .filter_map(|option| convert_config_option(adapter_key, option))
                    .filter(|option| option.category != "model")
                    .filter_map(|option| serde_json::to_value(option).ok())
                    .collect();
                entry.insert(MODEL_OPTION_SET_KEY.to_owned(), Value::Array(set));
            }
            Ok(Ok(_)) => {}
            Ok(Err(error)) => {
                tracing::warn!(
                    harness = %adapter_key,
                    model = %value,
                    %error,
                    "could not read a model's own options; publishing the default's for it"
                );
            }
            Err(_) => {
                tracing::warn!(
                    harness = %adapter_key,
                    model = %value,
                    deadline_secs = MODEL_OPTION_SET_DEADLINE.as_secs(),
                    "the agent did not answer choosing a model; publishing the default's options for the rest"
                );
                break;
            }
        }
    }
    // The probe's session is thrown away, but an adapter may remember the last
    // model chosen as the machine's default, and the probe must not move it.
    if let Some(default) = default
        && last_asked.as_ref().is_some_and(|asked| *asked != default)
    {
        let restore = SetSessionConfigOptionRequest::new(
            session_id.clone(),
            model_id,
            SessionConfigOptionValue::value_id(default),
        );
        match tokio::time::timeout(
            MODEL_OPTION_SET_DEADLINE,
            connection.send_request(restore).block_task(),
        )
        .await
        {
            Ok(Ok(_)) => {}
            Ok(Err(error)) => {
                tracing::warn!(harness = %adapter_key, %error, "could not restore the default model after the probe");
            }
            Err(_) => {
                tracing::warn!(
                    harness = %adapter_key,
                    "the agent did not answer restoring the default model after the probe"
                );
            }
        }
    }
}

/// Where the agent's session updates go: into this run's transcript, once
/// the run's own prompt is out, and only for the session the run holds.
#[derive(Clone)]
struct UpdateSink {
    /// `session/load` replays the whole conversation back as session updates
    /// before it returns. Those are turns Lemma already has, so forwarding
    /// them would duplicate every earlier message in the transcript.
    /// Streaming opens when this run's own prompt goes out.
    streaming: Arc<AtomicBool>,
    /// Which session this run's transcript belongs to.
    ///
    /// ACP carries a `sessionId` on every update and nothing here read it, so
    /// an adapter holding more than one session open would have written
    /// another conversation's output into this one's transcript.
    session: Arc<std::sync::OnceLock<String>>,
    callbacks: Arc<dyn AcpCallbacks>,
    normalizer: SharedNormalizer,
}

impl UpdateSink {
    fn open(&self, session_id: &str) {
        let _ = self.session.set(session_id.to_owned());
        self.streaming.store(true, Ordering::SeqCst);
    }

    fn forward(&self, notification: &SessionNotification) -> Result<(), AcpError> {
        if !self.streaming.load(Ordering::SeqCst) {
            return Ok(());
        }
        // An update for a session this run does not own belongs to somebody
        // else's transcript, not the end of this one.
        if let Some(session) = self.session.get()
            && notification.session_id.to_string().as_str() != session.as_str()
        {
            tracing::warn!(
                claimed = %notification.session_id,
                "dropped an ACP update for another session"
            );
            return Ok(());
        }
        let events = self
            .normalizer
            .lock()
            .expect("normalizer poisoned")
            .session_update(&notification.update);
        for event in events {
            let event_type = event.event_type;
            self.callbacks
                .event(event_type, event.object_id, event.payload)
                .map_err(|error| {
                    tracing::error!(%error, ?event_type, "could not persist ACP notification");
                    internal(error)
                })?;
        }
        Ok(())
    }
}

/// How a run answers the agent's permission requests.
struct PermissionHandler {
    gate: PermissionGate,
    sequence: Arc<AtomicU64>,
    timeout: std::time::Duration,
    run_id: uuid::Uuid,
    /// The tools Lemma's run-scoped MCP server publishes, when the run has one.
    scoped_mcp_tools: Option<Arc<std::collections::HashSet<String>>>,
    callbacks: Arc<dyn AcpCallbacks>,
    normalizer: SharedNormalizer,
    /// Approvals the person gave after an earlier turn that asked had ended.
    pre_approvals: PreApprovals,
}

enum Triage {
    /// Answered here, without asking anyone.
    Answer(RequestPermissionOutcome),
    /// Announced to Lemma; the run waits on the gate for the decision.
    Ask(PendingAsk),
}

/// A permission request that has to wait for Lemma to decide.
struct PendingAsk {
    run_id: uuid::Uuid,
    request_id: String,
    timeout: std::time::Duration,
    always: Option<AlwaysAllowOffer>,
}

impl PermissionHandler {
    fn triage(&self, request: &RequestPermissionRequest) -> Result<Triage, AcpError> {
        // A call to one of the tools Lemma itself published to this run.
        if self
            .scoped_mcp_tools
            .as_ref()
            .is_some_and(|tools| is_scoped_mcp_tool_approval(request, tools))
        {
            return Ok(Triage::Answer(allow_once(&request.options)));
        }
        // An "always" the user already gave for exactly this scope is answered
        // here, without asking again. The agent's own rule lives in an adapter
        // process that is new every run, and is set only once the answer
        // arrives -- so without this the same grant is asked for on the next
        // message, and once more for every call of a parallel batch.
        let always = always_allow_offer(request);
        if let Some(offer) = always
            .as_ref()
            .filter(|offer| self.gate.is_granted(&offer.scope))
        {
            return Ok(Triage::Answer(RequestPermissionOutcome::Selected(
                SelectedPermissionOutcome::new(offer.option_id.clone()),
            )));
        }
        let mut payload = permission_payload(request);
        // The call being gated goes on the record first, so the approval card
        // follows the call it asks about -- and carries that call's canonical
        // name and input rather than the adapter's own shapes.
        let (released, gated_call, call_fields) = self
            .normalizer
            .lock()
            .expect("normalizer poisoned")
            .permission_request(&serde_json::to_value(request).unwrap_or(Value::Null));
        for event in released {
            self.callbacks
                .event(event.event_type, event.object_id, event.payload)
                .map_err(internal)?;
        }
        payload.extend(call_fields);
        // Without a toolCallId every request in a session would collapse onto
        // one gate key, so concurrent prompts would deny each other and
        // overwrite each other's approval card in Lemma. The counter makes the
        // fallback unique per request; the id round-trips as the event's
        // object_id and comes back verbatim in RESOLVE_PERMISSION. It is the
        // gated call's own id, shortened the same way, so the two cannot stop
        // matching however long the adapter's id was.
        let request_id = gated_call
            .or_else(|| tool_call_id(&payload))
            .unwrap_or_else(|| {
                format!(
                    "{}:{}",
                    request.session_id,
                    self.sequence.fetch_add(1, Ordering::Relaxed)
                )
            });
        // The person already said yes to exactly this, after the turn that
        // asked had ended. It goes on the record as approved earlier -- so the
        // transcript says why nobody was asked -- and is allowed this once.
        if let Some(approval) = self.pre_approvals.take(&payload, &request.options) {
            payload.insert(
                "pre_approved".to_owned(),
                serde_json::json!({ "approval_id": approval.approval_id }),
            );
            self.callbacks
                .event(EventType::PermissionRequest, Some(request_id), payload)
                .map_err(internal)?;
            return Ok(Triage::Answer(allow_once(&request.options)));
        }
        self.callbacks
            .event(
                EventType::PermissionRequest,
                Some(request_id.clone()),
                payload,
            )
            .map_err(internal)?;
        Ok(Triage::Ask(PendingAsk {
            run_id: self.run_id,
            request_id,
            timeout: self.timeout,
            always,
        }))
    }
}
