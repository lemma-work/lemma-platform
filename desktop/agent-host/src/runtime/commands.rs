//! The commands a workspace sends, and what each one starts.

use super::{
    AcceptOutcome, Arc, AtomicBool, CANCEL_KILL_AFTER, Command, CommandKind, CommandRejection,
    JournalCallbacks, PermissionDecision, RejectionCode, RunSpec, RunState, StreamSegments,
    TargetWorker, Utc, Value, redact_error, short_revision, terminal_failure,
};

/// Why a command was refused, decided where the refusal happens.
///
/// This used to be recovered afterwards by matching English substrings against
/// the error's message -- and against the message *after* `redact_error` had
/// rewritten parts of it. Two things were wrong with that. Rewording any
/// `bail!` on the start path silently changed the machine-readable code the
/// workspace acts on, with nothing failing when it did; and the words are not
/// specific enough to carry the meaning, so an unrelated failure that happened
/// to say "expired" -- a provider credential, a certificate -- was reported to
/// Lemma as `CommandExpired`, which is not retryable, for a run that a retry
/// would have fixed.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum RefusedBecause {
    Draining,
    CommandExpired,
    HarnessNotFound,
    ConfigRevisionStale,
    CapacityLost,
    AdapterUnavailable,
}

impl RefusedBecause {
    /// The code the workspace stores and acts on.
    fn code(self) -> RejectionCode {
        match self {
            Self::Draining => RejectionCode::Draining,
            Self::CommandExpired => RejectionCode::CommandExpired,
            Self::HarnessNotFound => RejectionCode::HarnessNotFound,
            Self::ConfigRevisionStale => RejectionCode::ConfigRevisionStale,
            Self::CapacityLost => RejectionCode::CapacityLost,
            Self::AdapterUnavailable => RejectionCode::AdapterUnavailable,
        }
    }

    /// Whether Lemma may mint the same run again without a person deciding.
    ///
    /// Only the two that describe this host being momentarily full or on its
    /// way out. The rest need something to change first -- a revision to be
    /// republished, an adapter to be installed -- so retrying reproduces them.
    fn retryable(self) -> bool {
        matches!(self, Self::Draining | Self::CapacityLost)
    }
}

/// A refusal, carrying both the reason and the sentence a person reads.
///
/// The `Display` is the detail alone, so every existing log line and stored
/// rejection detail reads exactly as it did. The reason travels beside it
/// instead of inside it.
#[derive(Debug)]
pub(crate) struct Refusal {
    because: RefusedBecause,
    detail: String,
}

impl std::fmt::Display for Refusal {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.detail)
    }
}

impl std::error::Error for Refusal {}

/// Refuse a command for a stated reason.
pub(crate) fn refuse(because: RefusedBecause, detail: impl std::fmt::Display) -> anyhow::Error {
    anyhow::Error::new(Refusal {
        because,
        detail: detail.to_string(),
    })
}

pub(crate) fn command_rejection(
    command: &Command,
    error: &anyhow::Error,
) -> Option<CommandRejection> {
    if command.kind != CommandKind::StartRun {
        return None;
    }
    let run_id = command.run_id?;
    let lease_epoch = command.lease_epoch?;
    let detail = redact_error(&error.to_string());
    // Not the message. A refusal raised anywhere on the start path says why it
    // refused; anything else that reaches here is a failure this file did not
    // anticipate, and the workspace can only treat that as a command it cannot
    // act on -- which is what `InvalidCommand`, not retryable, already means.
    let (code, retryable) = error
        .downcast_ref::<Refusal>()
        .map_or((RejectionCode::InvalidCommand, false), |refusal| {
            (refusal.because.code(), refusal.because.retryable())
        });
    Some(CommandRejection {
        command_id: command.command_id,
        run_id,
        lease_epoch,
        code,
        retryable,
        detail: Some(detail.chars().take(1_000).collect()),
    })
}

impl TargetWorker {
    pub(crate) fn handle_command(&mut self, command: &Command) -> anyhow::Result<()> {
        if command.expires_at < Utc::now() {
            return Err(refuse(RefusedBecause::CommandExpired, "command is expired"));
        }
        match command.kind {
            CommandKind::StartRun => self.handle_start(command),
            CommandKind::CancelRun => self.handle_cancel(command),
            CommandKind::ResolvePermission => self.handle_resolve_permission(command),
            CommandKind::RefreshCredential => self.handle_refresh_credential(command),
        }
    }

    /// Take a replacement Lemma MCP credential for a run still in flight.
    ///
    /// Journaled rather than signalled: the MCP bridge is a separate process
    /// that re-reads its endpoint from the journal on every request, so writing
    /// it here *is* the delivery. Nothing needs to interrupt the run.
    pub(crate) fn handle_refresh_credential(&mut self, command: &Command) -> anyhow::Result<()> {
        self.journal
            .record_simple_command(self.target.target_id, command)?;
        let run_id = command
            .run_id
            .ok_or_else(|| anyhow::anyhow!("credential refresh has no run ID"))?;
        let lease_epoch = command
            .lease_epoch
            .ok_or_else(|| anyhow::anyhow!("credential refresh has no lease epoch"))?;
        let mcp = command
            .payload
            .get("mcp")
            .filter(|value| value.is_object())
            .ok_or_else(|| anyhow::anyhow!("credential refresh carries no MCP object"))?;
        if self
            .journal
            .refresh_run_mcp(self.target.target_id, run_id, lease_epoch, mcp)?
        {
            tracing::debug!(%run_id, "refreshed the run's Lemma MCP credential");
        }
        Ok(())
    }

    pub(crate) fn handle_start(&mut self, command: &Command) -> anyhow::Result<()> {
        if self.draining {
            return Err(refuse(RefusedBecause::Draining, "Agent Host is draining"));
        }
        let spec: RunSpec = serde_json::from_value(command.payload.clone())?;
        let published = self
            .harnesses
            .get(&spec.harness_id)
            .cloned()
            .ok_or_else(|| {
                refuse(
                    RefusedBecause::HarnessNotFound,
                    "command references an unknown harness",
                )
            })?;
        if published.config_revision != spec.profile_revision {
            // Both revisions, because the question a reader has is always "how
            // far behind was the command?", and one hash alone cannot answer
            // it. Lemma re-mints the command against the revision it is told
            // here, so this line is also the record of what it was told.
            tracing::warn!(
                harness = %published.harness_key,
                commanded = %short_revision(&spec.profile_revision),
                published = %short_revision(&published.config_revision),
                "rejecting a run minted against a superseded harness revision"
            );
            // Publish again, now. Reaching here means Lemma minted against a
            // revision this host does not have, and a publish is the only
            // thing that ever reconciles the two — otherwise the next one is
            // up to `HARNESS_REFRESH_INTERVAL` away and every run started in
            // between fails exactly like this one. The refresh reschedules
            // itself on the normal interval, so this cannot compound.
            self.refresh_due = std::time::Instant::now();
            // Both revisions in the detail, not only in this log line. The
            // detail is what reaches Lemma on the rejection and is stored with
            // the command, and "how far behind was it?" is the first question
            // asked of a run that died here — on a machine whose log nobody
            // reading the run will ever see.
            return Err(refuse(
                RefusedBecause::ConfigRevisionStale,
                format!(
                    "harness configuration revision changed: this computer publishes {} at {}, \
                     and the run was minted against {}",
                    published.harness_key,
                    short_revision(&published.config_revision),
                    short_revision(&spec.profile_revision),
                ),
            ));
        }
        if self.active_runs.contains_key(&spec.agent_run_id) {
            let outcome = self.journal.accept_start(
                self.target.target_id,
                command,
                &spec,
                &published.harness_key,
                &published.adapter_version,
            )?;
            anyhow::ensure!(
                outcome == AcceptOutcome::Duplicate,
                "active run did not have a durable command receipt"
            );
            return Ok(());
        }
        // Resolve the adapter and reserve real process capacity before writing
        // ACCEPTED. Once ACCEPTED is durable, Lemma must not start a cloud
        // fallback, so waiting on the semaphore after that point can duplicate
        // provider work.
        // Wrapped, not bubbled: the manifest reports a missing or unusable
        // adapter in its own words, and those words are what a person needs.
        // The reason is what Lemma needs, and only this call site knows it.
        let adapter = self
            .manifest
            .resolve(&published.harness_key)
            .map_err(|error| refuse(RefusedBecause::AdapterUnavailable, error))?;
        let probe = self.probes.get(&published.harness_key).cloned();
        let can_load_session = probe
            .as_ref()
            .is_some_and(|probe| probe.capabilities.load_session);
        let published_config_options = probe.map(|probe| probe.config_options).unwrap_or_default();
        let permit = Arc::clone(&self.global_capacity)
            .try_acquire_owned()
            .map_err(|_| {
                refuse(
                    RefusedBecause::CapacityLost,
                    "Agent Host capacity changed; command will be retried",
                )
            })?;
        let outcome = self.journal.accept_start(
            self.target.target_id,
            command,
            &spec,
            &published.harness_key,
            &published.adapter_version,
        )?;
        if outcome == AcceptOutcome::Duplicate {
            return Ok(());
        }
        self.spawn_run(
            spec,
            adapter,
            can_load_session,
            published_config_options,
            permit,
        );
        Ok(())
    }

    /// Ask a run to stop, through ACP where that is possible.
    ///
    /// Signalling rather than aborting is the whole point. `abort` kills the
    /// adapter mid-turn, and the provider has not yet written the session file
    /// that the conversation's *next* turn loads — so stopping one message used
    /// to silently cost the conversation its history. Raising the flag lets the
    /// driver send `session/cancel`, take the agent's own `cancelled` stop
    /// reason, and terminalize the run itself.
    ///
    /// The kill is still there, just deferred: a run that has not resolved by
    /// `kill_at` is torn down by `enforce_cancellations` exactly as before, so
    /// an adapter that ignores the notification cannot outlive its cancel.
    pub(crate) fn handle_cancel(&mut self, command: &Command) -> anyhow::Result<()> {
        self.journal
            .record_simple_command(self.target.target_id, command)?;
        let run_id = command
            .run_id
            .ok_or_else(|| anyhow::anyhow!("cancel command has no run ID"))?;
        if let Some(active) = self.active_runs.get_mut(&run_id) {
            // `send_replace`, not `send`: a run whose task has already dropped
            // its receiver has no listener, and `send` reports that as an error
            // without storing the value — which would leave the run looking
            // uncancelled to everything that reads the flag afterwards.
            active.cancel.send_replace(true);
            if active.kill_at.is_none() {
                active.kill_at = Some(tokio::time::Instant::now() + CANCEL_KILL_AFTER);
            }
            // Release anything parked on the user, before waiting for the turn
            // to end. An adapter that blocks its turn on an outstanding
            // `request_permission` -- which is the normal shape, not an edge
            // case -- can never answer `session/cancel` while a prompt nobody
            // will now respond to is still open. The turn then ran out the
            // grace period and the run was recorded as a *failure*, so someone
            // who pressed Stop was told their coding agent had crashed.
            // Dropping the waiters resolves them as denials, which is what
            // cancelling a turn means for a permission it will never use.
            self.permissions.abandon_run(run_id);
            return Ok(());
        }
        // No task to ask: the run is already gone, so its terminal state is
        // this host's to write.
        self.permissions.abandon_run(run_id);
        if let Some(run) = self.journal.get_run(self.target.target_id, run_id)?
            && !run.state.is_terminal()
        {
            terminal_failure(
                &self.journal,
                self.target.target_id,
                run_id,
                run.lease_epoch,
                RunState::Cancelled,
                "run cancelled by Lemma",
            )?;
        }
        Ok(())
    }

    /// Kill any run that was asked to stop and did not.
    ///
    /// This is the old `handle_cancel` behaviour, moved behind a deadline so it
    /// is the fallback rather than the first resort.
    pub(crate) fn enforce_cancellations(&mut self) -> anyhow::Result<()> {
        let now = tokio::time::Instant::now();
        let overdue = self
            .active_runs
            .iter()
            .filter_map(|(run_id, active)| {
                active
                    .kill_at
                    .is_some_and(|deadline| now >= deadline)
                    .then_some(*run_id)
            })
            .collect::<Vec<_>>();
        for run_id in overdue {
            // Abort, but keep the handle: `abort` only requests cancellation,
            // and the task can still finish its current poll -- which is long
            // enough to park a permission request. `reap_finished` abandons the
            // run again once the task is provably gone, which closes that
            // window.
            if let Some(active) = self.active_runs.get_mut(&run_id) {
                active.handle.abort();
                active.kill_at = None;
            }
            self.permissions.abandon_run(run_id);
            if let Some(run) = self.journal.get_run(self.target.target_id, run_id)?
                && !run.state.is_terminal()
            {
                tracing::warn!(
                    %run_id,
                    "the agent ignored session/cancel; terminating its process tree"
                );
                terminal_failure(
                    &self.journal,
                    self.target.target_id,
                    run_id,
                    run.lease_epoch,
                    RunState::Cancelled,
                    "run cancelled by Lemma; the agent did not stop on request",
                )?;
            }
        }
        Ok(())
    }

    pub(crate) fn handle_resolve_permission(&mut self, command: &Command) -> anyhow::Result<()> {
        self.journal
            .record_simple_command(self.target.target_id, command)?;
        let run_id = command
            .run_id
            .ok_or_else(|| anyhow::anyhow!("permission decision has no run ID"))?;
        let request_id = command
            .payload
            .get("request_id")
            .and_then(Value::as_str)
            .ok_or_else(|| anyhow::anyhow!("permission decision has no request ID"))?;
        let decision = match command.payload.get("option_id").and_then(Value::as_str) {
            Some(option_id) => PermissionDecision::Allow {
                option_id: option_id.to_owned(),
            },
            None => PermissionDecision::Deny,
        };
        // A decision for a request that already timed out, or for a run that
        // ended, has nothing waiting for it. That is expected, not an error.
        if !self.permissions.resolve(run_id, request_id, decision) {
            tracing::debug!(%run_id, request_id, "no permission request was waiting");
        }
        Ok(())
    }

    pub(crate) fn recover_interrupted_runs(&self) -> anyhow::Result<()> {
        for run in self.journal.recoverable_runs(self.target.target_id)? {
            let mut segments = StreamSegments::default();
            self.journal.visit_run_events(
                self.target.target_id,
                run.run_id,
                run.lease_epoch,
                |event| segments.recover_event(&event),
            )?;
            JournalCallbacks {
                journal: self.journal.clone(),
                target_id: self.target.target_id,
                run_id: run.run_id,
                lease_epoch: run.lease_epoch,
                host_cwd: None,
                provider_seen: AtomicBool::new(true),
                dispatched: AtomicBool::new(true),
                stream_segments: std::sync::Mutex::new(segments),
                events_ready: Arc::clone(&self.events_ready),
            }
            .flush_stream_segments()?;
            if run.prompt_dispatched {
                terminal_failure(
                    &self.journal,
                    self.target.target_id,
                    run.run_id,
                    run.lease_epoch,
                    RunState::DispatchUnknown,
                    "Agent Host restarted after prompt dispatch; the turn was not repeated",
                )?;
            } else {
                terminal_failure(
                    &self.journal,
                    self.target.target_id,
                    run.run_id,
                    run.lease_epoch,
                    RunState::Failed,
                    "Agent Host restarted before provider dispatch; Lemma may safely retry",
                )?;
            }
        }
        Ok(())
    }
}
