//! What the host reports up -- acknowledgements, checkpoints, rejections --
//! and winding down.

use futures_util::FutureExt;

use crate::link::protocol::RefusedUpdate;
use crate::link::{LinkHandle, Push};

use super::{
    CANCEL_KILL_AFTER, CommandKind, ControlBatch, Duration, REFUSED_HEARTBEAT_BACKOFF, RunState,
    SHUTDOWN_GRACE, TargetWorker, mpsc, terminal_failure,
};

impl TargetWorker {
    /// The control updates due for delivery, minus liveness checkpoints Lemma
    /// has refused recently.
    ///
    /// A refused run keeps its *terminal* checkpoint in the batch: giving up
    /// on a run's last word is the one thing that would leave it unresolved.
    /// A refused *liveness* checkpoint is held back for a while and then tried
    /// again. Holding one back forever meant a single transient refusal
    /// permanently stopped heartbeating a run the host was still healthily
    /// executing, so its lease expired and Lemma recovered a live run to
    /// `DISPATCH_UNKNOWN`.
    pub(crate) fn control_batch(&mut self) -> anyhow::Result<ControlBatch> {
        let (command_ids, mut checkpoints, rejections) =
            self.journal.pending_control(self.target.target_id)?;
        let now = std::time::Instant::now();
        self.refused_heartbeats.retain(|_, until| *until > now);
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

    /// Which of a batch's updates the host is done delivering.
    ///
    /// Lemma applies each update on its own and names only the ones it could
    /// not parse; a stale update it merely ignores counts as delivered. Of the
    /// refused ones, only a run's *liveness* checkpoint is given up on, for a
    /// while: its lease then expires and the server's own recovery resolves
    /// the run, which is the path built for a host that stops reporting.
    /// Everything else -- a run's terminal state above all, but also command
    /// acknowledgements and rejections -- carries information the server
    /// cannot reconstruct, so it stays pending and goes out again.
    pub(crate) fn settle_control(
        &mut self,
        batch: &ControlBatch,
        refused: &[RefusedUpdate],
    ) -> ControlBatch {
        let refused_checkpoint = |run_id| {
            refused
                .iter()
                .find(|update| update.kind == "checkpoint" && update.run_id == Some(run_id))
        };
        let refused_command = |kind: &str, command_id| {
            refused
                .iter()
                .find(|update| update.kind == kind && update.command_id == Some(command_id))
        };
        let mut settled = ControlBatch::default();
        for command_id in &batch.command_ids {
            if let Some(update) = refused_command("ack", *command_id) {
                tracing::error!(
                    %command_id,
                    reason = update.reason.as_deref().unwrap_or_default(),
                    "Lemma refused this command acknowledgement; it stays queued for retry"
                );
                continue;
            }
            settled.command_ids.push(*command_id);
        }
        for checkpoint in &batch.checkpoints {
            let Some(update) = refused_checkpoint(checkpoint.run_id) else {
                settled.checkpoints.push(checkpoint.clone());
                continue;
            };
            let reason = update.reason.as_deref().unwrap_or_default();
            if checkpoint.state.is_terminal() {
                tracing::error!(
                    run_id = %checkpoint.run_id,
                    state = ?checkpoint.state,
                    reason,
                    "Lemma refused this run's final state; it stays queued for retry"
                );
                continue;
            }
            self.refused_heartbeats.insert(
                checkpoint.run_id,
                std::time::Instant::now() + REFUSED_HEARTBEAT_BACKOFF,
            );
            settled.checkpoints.push(checkpoint.clone());
            tracing::error!(
                run_id = %checkpoint.run_id,
                state = ?checkpoint.state,
                reason,
                retry_after_secs = REFUSED_HEARTBEAT_BACKOFF.as_secs(),
                "Lemma refused this run's heartbeat; backing off before trying again"
            );
        }
        for rejection in &batch.rejections {
            if let Some(update) = refused_command("rejection", rejection.command_id) {
                tracing::error!(
                    command_id = %rejection.command_id,
                    reason = update.reason.as_deref().unwrap_or_default(),
                    "Lemma refused this command rejection; it stays queued for retry"
                );
                continue;
            }
            settled.rejections.push(rejection.clone());
        }
        settled
    }

    /// One delivery pass. See [`super::EventFlusher::flush`].
    #[cfg(test)]
    pub(crate) async fn flush_events(&mut self, link: &LinkHandle) -> anyhow::Result<()> {
        self.flusher.lock().await.flush(link).await.map(|_| ())
    }

    /// Deliver everything the journal owes. See [`super::EventFlusher::drain`].
    pub(crate) async fn drain_events(&mut self, link: &LinkHandle) -> anyhow::Result<()> {
        self.flusher.lock().await.drain(link).await.map(|_| ())
    }

    /// Collect run tasks that have finished, without waiting on any.
    pub(crate) fn reap_finished_now(&mut self) {
        let finished = self
            .active_runs
            .iter()
            .filter_map(|(run_id, active)| active.handle.is_finished().then_some(*run_id))
            .collect::<Vec<_>>();
        for run_id in finished {
            if let Some(active) = self.active_runs.remove(&run_id) {
                // Finished, so the join is already resolved.
                match active.handle.join().now_or_never() {
                    Some(Err(error)) if !error.is_cancelled() => {
                        tracing::error!(%run_id, %error, "agent run task terminated unexpectedly");
                    }
                    Some(Ok(Err(error))) => {
                        tracing::error!(%run_id, %error, "agent run task failed");
                    }
                    _ => {}
                }
            }
            // The task is provably gone, so nothing can answer a request it
            // left parked -- including one it parked while being aborted.
            self.permissions.abandon_run(run_id);
            // Its terminal checkpoint is in the journal; say so now.
            self.events_ready.notify_one();
        }
    }

    /// Stop, giving every run in flight the chance to end its own turn.
    ///
    /// Waiting for turns to end on their own and then aborting the ones that
    /// did not is how quitting Lemma mid-answer lost a conversation its
    /// history: aborting drops the ACP connection, the child guard kills the
    /// process group, and the provider never writes the session file the
    /// *next* turn resumes from. Signalling first gives each agent its ten
    /// seconds to finish through ACP, and `enforce_cancellations` still kills
    /// whatever ignores that -- inside this grace, because `CANCEL_KILL_AFTER`
    /// is half of `SHUTDOWN_GRACE`.
    pub(crate) async fn graceful_shutdown(
        &mut self,
        mut link: Option<(&LinkHandle, &mut mpsc::UnboundedReceiver<Push>)>,
    ) -> anyhow::Result<()> {
        self.draining = true;
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
            self.reap_finished_now();
            self.enforce_cancellations()?;
            if let Some((handle, pushes)) = link.as_mut() {
                if let Err(error) = self.drain_events(handle).await {
                    tracing::warn!(%error, "could not flush Agent Host events during shutdown");
                }
                if let Err(error) = self.send_control(handle).await {
                    tracing::warn!(%error, "could not report to Lemma during shutdown");
                }
                // A Stop that arrives while the host is winding down still
                // applies; nothing else is taken on.
                while let Ok(push) = pushes.try_recv() {
                    if let Push::Commands(commands) = push {
                        for command in commands {
                            if command.kind == CommandKind::CancelRun {
                                let _ = self.handle_cancel(&command);
                            }
                        }
                    }
                }
            }
            if self.active_runs.is_empty() {
                break;
            }
            if tokio::time::Instant::now() >= deadline {
                self.cancel_all("Agent Host shutdown grace elapsed")?;
                break;
            }
            tokio::time::sleep(Duration::from_millis(250)).await;
        }
        if let Some((handle, _)) = link {
            // The last chance: everything, not one pass's worth.
            self.drain_events(handle).await?;
            if let Err(error) = self.send_control(handle).await {
                tracing::warn!(%error, "could not report final run states during shutdown");
            }
        }
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
