//! Spawning one agent run.

use super::{
    AcpRunRequest, ActiveRun, Arc, AtomicBool, CANCEL_GRACE, Checkpoint, ConfigOption, Duration,
    EnvVariable, EventType, JournalCallbacks, JsonMap, McpServer, McpServerStdio, Ordering,
    OwnedSemaphorePermit, OwnedTask, PERMISSION_DECISION_TIMEOUT, ResolvedAdapter, RunSpec,
    RunState, StreamSegments, TargetWorker, Utc, Value, adapter_failure_message,
    authentication_hint, host_directory_instructions, prepare_run_directory,
    publish_generated_images, redact_error, terminal_failure, terminal_failure_detail, watch,
};

impl TargetWorker {
    pub(crate) fn spawn_run(
        &mut self,
        mut spec: RunSpec,
        adapter: ResolvedAdapter,
        can_load_session: bool,
        published_config_options: Vec<ConfigOption>,
        permit: OwnedSemaphorePermit,
    ) {
        let target_id = self.target.target_id;
        let journal = self.journal.clone();
        let driver = Arc::clone(&self.driver);
        let mcp_bridge_executable = self.mcp_bridge_executable.clone();
        let paths = self.paths.clone();
        let permissions = self.permissions.clone();
        let events_ready = Arc::clone(&self.events_ready);
        let reprobe_requested = Arc::clone(&self.reprobe_requested);
        let run_id = spec.agent_run_id;
        // Captured before the task takes ownership of `adapter`, so a failure
        // can name the agent rather than describing it as an internal error.
        let adapter_name = adapter.spec.display_name.clone();
        let (cancel_tx, cancel_rx) = watch::channel(false);
        let handle = tokio::spawn(async move {
            let _permit = permit;
            let lease_epoch = journal
                .get_run(target_id, run_id)?
                .ok_or_else(|| anyhow::anyhow!("accepted run disappeared"))?
                .lease_epoch;
            journal.checkpoint(
                target_id,
                run_id,
                lease_epoch,
                RunState::Accepted,
                &JsonMap::new(),
            )?;
            // `poll_target` snapshots the control batch when it builds the
            // request, so a checkpoint written a moment later waits out the
            // whole 25s long poll. Measured at 10-24s between a command being
            // delivered and the host reporting it accepted.
            events_ready.notify_one();
            if !spec.mcp.is_object() {
                terminal_failure(
                    &journal,
                    target_id,
                    run_id,
                    lease_epoch,
                    RunState::Failed,
                    "the start command did not carry a run-scoped MCP configuration",
                )?;
                // Like the sibling failure below and the normal exit at the
                // end. Without it this run's terminal checkpoint waits out the
                // whole long poll -- the delay the comment above measures.
                events_ready.notify_one();
                return Ok(());
            }
            let scratch = match prepare_run_directory(&paths, target_id, &spec) {
                Ok(path) => path,
                Err(error) => {
                    terminal_failure(
                        &journal,
                        target_id,
                        run_id,
                        lease_epoch,
                        RunState::Failed,
                        &format!("could not open the conversation working directory: {error}"),
                    )?;
                    events_ready.notify_one();
                    return Ok(());
                }
            };
            let host_cwd = scratch
                .to_str()
                .ok_or_else(|| anyhow::anyhow!("conversation directory is not valid Unicode"))?
                .to_owned();
            spec.system_prompt
                .push_str(&host_directory_instructions(&host_cwd));
            // The name Lemma published for this run's server, not a name of our
            // own. An agent namespaces every MCP tool with the server it came
            // from, so registering a different name here made the same tool
            // arrive as `mcp__lemma__lemma_exec_command` on this path and
            // `mcp__lemma_tools__lemma_exec_command` on every other one — one
            // tool with two names, which is exactly what the readers of those
            // names cannot tell apart.
            let scoped_server_name = spec
                .mcp
                .get("server_name")
                .and_then(Value::as_str)
                .map(str::trim)
                .filter(|name| !name.is_empty())
                .unwrap_or(crate::acp::SCOPED_MCP_SERVER)
                .to_owned();
            let mcp_server = McpServer::Stdio(
                McpServerStdio::new(scoped_server_name, mcp_bridge_executable)
                    .args(vec![
                        "--data-dir".to_owned(),
                        paths.root.to_string_lossy().into_owned(),
                        "mcp-bridge".to_owned(),
                        "--target-id".to_owned(),
                        target_id.to_string(),
                        "--run-id".to_owned(),
                        run_id.to_string(),
                    ])
                    .env(vec![EnvVariable::new("LEMMA_AGENT_HOST_BRIDGE", "1")]),
            );
            let callbacks = Arc::new(JournalCallbacks {
                journal: journal.clone(),
                target_id,
                run_id,
                lease_epoch,
                host_cwd: Some(host_cwd),
                provider_seen: AtomicBool::new(false),
                dispatched: AtomicBool::new(false),
                stream_segments: std::sync::Mutex::new(StreamSegments::default()),
                events_ready: Arc::clone(&events_ready),
            });
            let remaining = (spec.run_deadline - Utc::now())
                .to_std()
                .unwrap_or(Duration::ZERO);
            // Kept behind, so the failure path below can still ask whether the
            // user pressed Stop.
            let asked_to_stop = cancel_rx.clone();
            let request = AcpRunRequest {
                adapter,
                run_spec: spec,
                scratch_directory: scratch.clone(),
                mcp_server: Some(mcp_server),
                can_load_session,
                published_config_options,
                permissions: permissions.clone(),
                permission_timeout: PERMISSION_DECISION_TIMEOUT,
                cancel: cancel_rx,
                cancel_grace: CANCEL_GRACE,
            };
            let outcome =
                tokio::time::timeout(remaining, driver.run(request, callbacks.clone())).await;
            if matches!(outcome, Ok(Ok(_)))
                && let Err(error) = publish_generated_images(&scratch, callbacks.as_ref())
            {
                tracing::warn!(
                    %run_id,
                    %error,
                    "could not publish a generated image artifact"
                );
            }
            // Terminal events bypass the ACP callback. Seal the final text on
            // every exit path so a crash or an answer ending in a text chunk
            // cannot leave its transcript only in the transient live lane.
            callbacks.flush_stream_segments()?;
            // Deliberately kept. It is the conversation's working directory, and
            // the next turn resumes the session that lives in it; deleting it
            // here is what made every resumption fail.
            match outcome {
                Ok(Ok(outcome)) => {
                    let mut payload = JsonMap::new();
                    payload.insert("state".to_owned(), serde_json::to_value(outcome.state)?);
                    payload.insert("stop_reason".to_owned(), Value::String(outcome.stop_reason));
                    // Lemma renders a failed run from `message`, so a turn that
                    // ended on a ceiling rather than a fault has to say which.
                    if let Some(message) = outcome.message {
                        payload.insert("message".to_owned(), Value::String(message));
                    }
                    journal.append_event(
                        target_id,
                        run_id,
                        lease_epoch,
                        EventType::Terminal,
                        None,
                        payload,
                    )?;
                    journal.checkpoint(
                        target_id,
                        run_id,
                        lease_epoch,
                        outcome.state,
                        &JsonMap::new(),
                    )?;
                }
                Ok(Err(error)) => {
                    let run = journal
                        .get_run(target_id, run_id)?
                        .ok_or_else(|| anyhow::anyhow!("run disappeared after adapter failure"))?;
                    let state = if run.checkpoint == Checkpoint::DispatchIntent {
                        // Cancellation does not resolve side-effect
                        // uncertainty: we still do not know whether the
                        // provider received the prompt.
                        RunState::DispatchUnknown
                    } else if *asked_to_stop.borrow() {
                        // The user pressed Stop and the turn did not end
                        // cleanly. That is a cancellation that went the hard
                        // way, not a fault in their coding agent -- and
                        // calling it a failure put "Claude Code encountered an
                        // error on this computer" in front of someone whose
                        // only mistake was stopping a run.
                        RunState::Cancelled
                    } else {
                        RunState::Failed
                    };
                    let raw = error.to_string();
                    if authentication_hint(&adapter_name, &raw).is_some() {
                        // The freshest evidence anyone has that this agent is
                        // signed out. Probing is what publishes AUTH_REQUIRED,
                        // and it is otherwise up to fifteen minutes away, so
                        // ask for one now -- that is what makes the workspace
                        // say "Sign-in needed" while the user is still looking
                        // at the failure that told them.
                        reprobe_requested.store(true, Ordering::SeqCst);
                    }
                    let rewritten = if state == RunState::Cancelled {
                        // Say what happened. The adapter-failure wording is
                        // written for a fault and would describe the agent as
                        // broken to a user who simply stopped it.
                        Some("run cancelled by Lemma; the agent did not stop on request".to_owned())
                    } else {
                        authentication_hint(&adapter_name, &raw)
                            .or_else(|| adapter_failure_message(&adapter_name, &redact_error(&raw)))
                    };
                    // Rewriting an internal error does not mean the text it
                    // interrupted was an error. Drop only a proven duplicate.
                    let supersedes =
                        rewritten.is_some() && callbacks.stream_matches_failure(&raw)?;
                    let message = rewritten.unwrap_or_else(|| redact_error(&raw));
                    terminal_failure_detail(
                        &journal,
                        target_id,
                        run_id,
                        lease_epoch,
                        state,
                        &message,
                        supersedes,
                    )?;
                }
                Err(_) => {
                    terminal_failure(
                        &journal,
                        target_id,
                        run_id,
                        lease_epoch,
                        RunState::Failed,
                        "Agent Host run deadline elapsed; the provider process was terminated",
                    )?;
                }
            }
            // However this run ended - success, failure, deadline - its
            // terminal checkpoint is upstream-bound and must not wait for the
            // current long poll either.
            events_ready.notify_one();
            Ok(())
        });
        self.active_runs.insert(
            run_id,
            ActiveRun {
                handle: OwnedTask(handle),
                cancel: cancel_tx,
                kill_at: None,
            },
        );
    }
}
