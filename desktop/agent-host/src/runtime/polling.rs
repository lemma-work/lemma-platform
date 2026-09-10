//! Asking the workspace for work, and answering with what happened.

use super::{
    ApiError, CANCEL_KILL_AFTER, CommandKind, ControlBatch, Duration, HostCapacity,
    MAX_CONTROL_PROBES, PollResponse, REFUSED_HEARTBEAT_RETRY_POLLS, RunState, SHUTDOWN_GRACE,
    TargetWorker, redact_error, terminal_failure,
};

impl TargetWorker {
    /// The control updates due for delivery, minus the ones Lemma refuses.
    ///
    /// A refused run keeps its *terminal* checkpoint in the batch: giving up on
    /// a run's last word is the one thing that would leave it unresolved.
    ///
    /// A refused *liveness* checkpoint is held back for a bounded number of
    /// polls and then tried again. Holding one back forever meant a single
    /// transient refusal — a deploy, a blip — permanently stopped heartbeating
    /// a run the host was still healthily executing, so its lease expired and
    /// Lemma recovered a live run to `DISPATCH_UNKNOWN`.
    pub(crate) fn control_batch(&mut self) -> anyhow::Result<ControlBatch> {
        let (command_ids, mut checkpoints, rejections) =
            self.journal.pending_control(self.target.target_id)?;
        self.refused_heartbeats.retain(|_, polls| {
            *polls = polls.saturating_sub(1);
            *polls > 0
        });
        checkpoints.retain(|checkpoint| {
            checkpoint.state.is_terminal()
                || !self.refused_heartbeats.contains_key(&checkpoint.run_id)
        });
        Ok(ControlBatch {
            command_ids,
            checkpoints,
            rejections,
        })
    }

    pub(crate) async fn poll_control(
        &self,
        capacity: &HostCapacity,
        batch: &ControlBatch,
    ) -> Result<PollResponse, ApiError> {
        self.client
            .poll(
                capacity.clone(),
                batch.command_ids.clone(),
                batch.checkpoints.clone(),
                batch.rejections.clone(),
            )
            .await
    }

    /// Poll Lemma, bisecting the control batch around anything it refuses.
    ///
    /// One poll carries the whole host's control updates, and that same request
    /// is the capacity heartbeat and the only way commands -- cancellations
    /// included -- come back down. Failing it outright over a single update the
    /// server objects to would take the entire host offline until it restarts,
    /// with every run's lease expiring underneath it while its provider carried
    /// on working. So a refusal is bisected instead: probing halves of the
    /// undelivered range narrows to the one update at fault in a logarithmic
    /// number of requests, delivers everything either side of it, and ends at a
    /// poll the server accepts. The heartbeat can no longer be held hostage by
    /// anything the host is trying to report.
    ///
    /// A probe only ever covers updates that have not been accepted yet, so
    /// nothing is delivered twice, and every response's commands are carried
    /// through rather than discarded with the attempt that produced them.
    pub(crate) async fn poll_target(
        &mut self,
        capacity: HostCapacity,
    ) -> anyhow::Result<PollResponse> {
        let batch = self.control_batch()?;
        let total = batch.len();
        // Everything below this index has been accepted, or given up on.
        let mut delivered = 0;
        let mut settled = ControlBatch::default();
        let mut commands = Vec::new();
        let mut budget = MAX_CONTROL_PROBES;

        let mut response = loop {
            let attempt = batch.slice(delivered, total);
            match self.poll_control(&capacity, &attempt).await {
                Ok(response) => {
                    settled.absorb(attempt);
                    break response;
                }
                // An empty batch carries nothing to blame, and a failure that
                // is not a rejection is about the target, not its payload.
                Err(error) if attempt.is_empty() || !error.is_request_rejected() => {
                    return Err(error.into());
                }
                Err(error) => {
                    if budget == 0 {
                        // Out of probes. Land the heartbeat with an empty batch
                        // and leave the rest pending for the next cycle.
                        delivered = total;
                        continue;
                    }
                    // Invariant: `low..high` holds at least one refused update.
                    let (mut low, mut high) = (delivered, total);
                    while high - low > 1 && budget > 0 {
                        budget -= 1;
                        let middle = low + (high - low) / 2;
                        let probe = batch.slice(low, middle);
                        match self.poll_control(&capacity, &probe).await {
                            Ok(mut accepted) => {
                                commands.append(&mut accepted.commands);
                                settled.absorb(probe);
                                low = middle;
                            }
                            Err(refusal) if refusal.is_request_rejected() => high = middle,
                            Err(refusal) => return Err(refusal.into()),
                        }
                    }
                    if high - low == 1 {
                        settled.absorb(self.refuse_control(&batch.slice(low, high), &error));
                    }
                    delivered = high;
                }
            }
        };

        self.journal.mark_control_applied(
            self.target.target_id,
            &settled.command_ids,
            &settled.checkpoints,
            &settled.rejections,
        )?;
        commands.append(&mut response.commands);
        response.commands = commands;
        Ok(response)
    }

    /// Decide what to do with the single control update Lemma refused, and
    /// return whatever the host is done trying to deliver.
    ///
    /// Only a run's *liveness* checkpoint is given up on. Its lease then
    /// expires and the server's own recovery resolves the run, which is the
    /// path built for a host that stops reporting. Everything else -- a run's
    /// terminal state above all, but also command acknowledgements and command
    /// rejections -- carries information the server cannot reconstruct, so it
    /// stays pending and is retried on every later cycle. Retrying costs a
    /// bounded handful of extra requests per poll and blocks nothing, because
    /// the narrowing lets the rest of the batch through regardless.
    pub(crate) fn refuse_control(
        &mut self,
        batch: &ControlBatch,
        error: &ApiError,
    ) -> ControlBatch {
        let detail = redact_error(&error.to_string());
        let mut settled = ControlBatch::default();
        for checkpoint in &batch.checkpoints {
            if checkpoint.state.is_terminal() {
                tracing::error!(
                    run_id = %checkpoint.run_id,
                    state = ?checkpoint.state,
                    error = %detail,
                    "Lemma refused this run's final state; it stays queued for retry"
                );
                continue;
            }
            self.refused_heartbeats
                .insert(checkpoint.run_id, REFUSED_HEARTBEAT_RETRY_POLLS);
            settled.checkpoints.push(checkpoint.clone());
            tracing::error!(
                run_id = %checkpoint.run_id,
                state = ?checkpoint.state,
                error = %detail,
                retry_after_polls = REFUSED_HEARTBEAT_RETRY_POLLS,
                "Lemma refused this run's heartbeat; backing off before trying again"
            );
        }
        for command_id in &batch.command_ids {
            tracing::error!(
                %command_id,
                error = %detail,
                "Lemma refused this command acknowledgement; it stays queued for retry"
            );
        }
        for rejection in &batch.rejections {
            tracing::error!(
                command_id = %rejection.command_id,
                error = %detail,
                "Lemma refused this command rejection; it stays queued for retry"
            );
        }
        settled
    }

    /// Hand journaled events to Lemma. See [`EventFlusher::flush`].
    pub(crate) async fn flush_events(&mut self) -> anyhow::Result<()> {
        self.flusher.lock().await.flush().await
    }

    pub(crate) async fn reap_finished(&mut self) {
        let finished = self
            .active_runs
            .iter()
            .filter_map(|(run_id, active)| active.handle.is_finished().then_some(*run_id))
            .collect::<Vec<_>>();
        for run_id in finished {
            if let Some(active) = self.active_runs.remove(&run_id)
                && let Err(error) = active.handle.join().await
                && !error.is_cancelled()
            {
                tracing::error!(%run_id, %error, "agent run task terminated unexpectedly");
            }
            // The task is provably gone, so nothing can answer a request it
            // left parked -- including one it parked while being aborted.
            self.permissions.abandon_run(run_id);
        }
    }

    pub(crate) async fn graceful_shutdown(&mut self) -> anyhow::Result<()> {
        self.draining = true;
        // Ask every run in flight to stop, the same way Stop does.
        //
        // Waiting for turns to end on their own and then aborting the ones that
        // did not is how quitting Lemma mid-answer lost a conversation its
        // history: `cancel_all` drops the ACP connection, the child guard
        // SIGKILLs the process group, and the provider never writes the session
        // file the *next* turn resumes from. Signalling first gives each agent
        // its ten seconds to finish through ACP, and `enforce_cancellations`
        // still kills whatever ignores that -- inside this grace, because
        // CANCEL_KILL_AFTER is half of SHUTDOWN_GRACE.
        let signalled = tokio::time::Instant::now() + CANCEL_KILL_AFTER;
        for active in self.active_runs.values_mut() {
            active.cancel.send_replace(true);
            if active.kill_at.is_none() {
                active.kill_at = Some(signalled);
            }
        }
        let abandoned = self.active_runs.keys().copied().collect::<Vec<_>>();
        for run_id in abandoned {
            // As in `handle_cancel`: an adapter blocked on an approval nobody
            // is going to answer cannot act on the cancel it was just sent.
            self.permissions.abandon_run(run_id);
        }
        let deadline = tokio::time::Instant::now() + SHUTDOWN_GRACE;
        loop {
            self.reap_finished().await;
            self.enforce_cancellations()?;
            if let Err(error) = self.flush_events().await {
                tracing::warn!(%error, "could not flush Agent Host events during shutdown");
            }
            let capacity = HostCapacity {
                max_runs: self.max_runs,
                active_runs: self.max_runs.saturating_sub(
                    u16::try_from(self.global_capacity.available_permits()).unwrap_or(u16::MAX),
                ),
                available_runs: 0,
            };
            if let Ok(response) = self.poll_target(capacity).await {
                for command in response.commands {
                    if command.kind == CommandKind::CancelRun {
                        let _ = self.handle_cancel(&command);
                    }
                }
            }
            if self.active_runs.is_empty() {
                return Ok(());
            }
            if tokio::time::Instant::now() >= deadline {
                break;
            }
            tokio::time::sleep(Duration::from_millis(250)).await;
        }
        self.cancel_all("Agent Host shutdown grace elapsed")?;
        self.flush_events().await?;
        Ok(())
    }

    pub(crate) fn cancel_all(&mut self, reason: &str) -> anyhow::Result<()> {
        let run_ids = self.active_runs.keys().copied().collect::<Vec<_>>();
        for run_id in run_ids {
            if let Some(active) = self.active_runs.remove(&run_id) {
                active.handle.abort();
            }
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
                    reason,
                )?;
            }
        }
        Ok(())
    }
}
