//! Getting a run's events to the workspace, and retrying when they do
//! not land.

use super::{
    Arc, EVENT_RETRY_MAX, EVENT_RETRY_QUIET, HashMap, HashSet, Journal, MAX_EVENT_REJECTIONS,
    RETRY_MIN, TargetClient, Uuid, redact_error, watch,
};

/// Delivery of journaled events to Lemma, off the poll loop.
///
/// Owns everything that delivery touches — the journal, the client, and how
/// often each run's batches have been refused — so it can run as its own task
/// while the poll loop holds its poll open.
///
/// That separation is the whole point. Flushing used to be an arm of the same
/// `select!` as the poll, so every event a run streamed abandoned the poll in
/// flight and opened a new one. The server had no idea the old one was gone and
/// held it for the rest of its 25 seconds: one host streaming a single answer
/// was measured stacking 26 concurrent polls, against exactly one while idle.
pub(crate) struct EventFlusher {
    pub(crate) target_id: Uuid,
    pub(crate) journal: Journal,
    pub(crate) client: TargetClient,
    /// Runs whose event batches Lemma has rejected, and how often. Kept per
    /// run so one unhappy run cannot stop delivery for every other.
    pub(crate) rejections: HashMap<Uuid, u32>,
}

impl EventFlusher {
    /// Hand journaled events to Lemma, keeping each run's failures to itself.
    ///
    /// Only a target-level failure -- unreachable, unauthenticated, throttled,
    /// server fault -- is returned as an error. A batch Lemma rejects on its own
    /// merits belongs to exactly one run, so it is contained there: the poll
    /// loop must still poll, because the poll is what heartbeats the lease of
    /// every *other* run on this host.
    pub(crate) async fn flush(&mut self) -> anyhow::Result<()> {
        let target_id = self.target_id;
        // Runs whose remaining batches this pass must leave alone, because the
        // journal no longer matches the batches read at the top of the loop.
        let mut stale = HashSet::new();
        for batch in self.journal.pending_events(target_id, 1024)? {
            let Some(first) = batch.events.first() else {
                continue;
            };
            let (run_id, lease_epoch) = (first.run_id, first.lease_epoch);
            if stale.contains(&run_id) {
                continue;
            }
            if self.rejections.get(&run_id).copied().unwrap_or(0) >= MAX_EVENT_REJECTIONS {
                let dropped = self
                    .journal
                    .discard_events(target_id, run_id, lease_epoch)?;
                stale.insert(run_id);
                tracing::warn!(
                    %run_id,
                    dropped,
                    "dropping events Lemma will not accept for this run"
                );
                continue;
            }
            match self.client.append_events(&batch).await {
                Ok(ack) => {
                    self.rejections.remove(&run_id);
                    self.journal.acknowledge_events(target_id, &ack)?;
                }
                Err(error) if error.is_request_rejected() => {
                    stale.insert(run_id);
                    self.reject_run_events(run_id, lease_epoch, &error)?;
                }
                Err(error) => return Err(error.into()),
            }
        }
        Ok(())
    }

    /// Contain a run whose event batch Lemma refused.
    ///
    /// The first refusal is treated as a lost server-side stream, which is the
    /// only recoverable cause: the stream is transient and its watermark drops
    /// to zero when it is evicted, so it starts expecting sequence 1 again from
    /// a run that is far past it. Replaying the run's retained events answers
    /// exactly that, and is safe against every other cause because the server
    /// keeps the first write for a sequence it already holds.
    pub(crate) fn reject_run_events(
        &mut self,
        run_id: Uuid,
        lease_epoch: u32,
        error: &crate::api::ApiError,
    ) -> anyhow::Result<()> {
        let rejections = self.rejections.entry(run_id).or_default();
        *rejections += 1;
        if *rejections >= MAX_EVENT_REJECTIONS {
            tracing::error!(
                %run_id,
                error = %redact_error(&error.to_string()),
                "Lemma rejected this run's events after a replay; giving up on its transcript"
            );
            return Ok(());
        }
        let replayed = self
            .journal
            .rewind_acknowledgements(self.target_id, run_id, lease_epoch)?;
        tracing::warn!(
            %run_id,
            replayed,
            error = %redact_error(&error.to_string()),
            "Lemma rejected this run's events; replaying its journaled history"
        );
        Ok(())
    }
}

/// Deliver events as they are journaled, until the host shuts down.
///
/// A run's output reaching Lemma is not something the poll loop should be able
/// to delay, and not something that should be able to disturb the poll: this
/// waits on the same `events_ready` the run tasks raise, and takes the lock the
/// shutdown flush also takes, so the two can never be in flight together.
///
/// Failures are logged and retried rather than reported: the poll loop is the
/// one authority on whether this target is reachable, and having two writers of
/// the connection state produced an ONLINE/OFFLINE flap between them.
pub(crate) async fn deliver_events(
    flusher: Arc<tokio::sync::Mutex<EventFlusher>>,
    events_ready: Arc<tokio::sync::Notify>,
    mut shutdown: watch::Receiver<bool>,
) {
    let mut retry = RETRY_MIN;
    let mut consecutive_failures: u32 = 0;
    loop {
        tokio::select! {
            () = events_ready.notified() => {}
            _ = shutdown.changed() => return,
        }
        if *shutdown.borrow() {
            return;
        }
        match flusher.lock().await.flush().await {
            Ok(()) => {
                if consecutive_failures >= EVENT_RETRY_QUIET {
                    tracing::warn!(
                        failures = consecutive_failures,
                        "Agent Host event delivery recovered"
                    );
                }
                retry = RETRY_MIN;
                consecutive_failures = 0;
            }
            Err(error) => {
                consecutive_failures += 1;
                // The first failures are ordinary and stay quiet. A run of them
                // is the thing nobody could see: events are what a person is
                // waiting on, so silence here reads to them as an agent that
                // stopped, and `debug` alone meant neither a CI log nor a
                // support bundle could tell them apart.
                if consecutive_failures == EVENT_RETRY_QUIET {
                    tracing::warn!(
                        %error,
                        failures = consecutive_failures,
                        "Agent Host events are not being delivered; still retrying"
                    );
                } else {
                    tracing::debug!(%error, "could not deliver Agent Host events; retrying");
                }
                tokio::select! {
                    () = tokio::time::sleep(retry) => {}
                    _ = shutdown.changed() => return,
                }
                retry = (retry * 2).min(EVENT_RETRY_MAX);
                // Nothing consumed the notification that brought us here, so
                // re-raise it: the events are still pending and the next pass
                // has to be woken by something.
                events_ready.notify_one();
            }
        }
    }
}
