//! Getting a run's events to the workspace, and retrying when they do not
//! land.

use crate::link::{LinkError, LinkHandle, LinkSlot};

use super::{
    Arc, EVENT_LINGER, EVENT_RETRY_MAX, EVENT_RETRY_QUIET, HashMap, HashSet, Journal,
    MAX_EVENT_REJECTIONS, RETRY_MIN, Uuid, redact_error, watch,
};

/// What the journal owes Lemma, raised by whoever wrote it.
///
/// Two waiters, because two tasks deliver: events go out through
/// `deliver_events`, and acknowledgements and checkpoints through the link
/// loop's `control` frame. A run that writes either raises both -- neither
/// knows which the other wrote, and a spurious wake costs one read of the
/// journal.
#[derive(Clone, Default)]
pub(crate) struct OutboxSignal {
    events: Arc<tokio::sync::Notify>,
    control: Arc<tokio::sync::Notify>,
}

impl OutboxSignal {
    /// Something new is in the journal.
    pub(crate) fn notify_one(&self) {
        self.events.notify_one();
        self.control.notify_one();
    }

    /// Control updates are due -- after events are acknowledged, say, which
    /// is what releases a run's terminal checkpoint.
    pub(crate) fn notify_control(&self) {
        self.control.notify_one();
    }

    pub(crate) fn control(&self) -> Arc<tokio::sync::Notify> {
        Arc::clone(&self.control)
    }

    pub(crate) fn events(&self) -> Arc<tokio::sync::Notify> {
        Arc::clone(&self.events)
    }
}

/// Delivery of journaled events to Lemma.
///
/// Owns everything delivery touches -- the journal and how often each run's
/// batches have been refused -- so it can run as its own task beside the link
/// loop, and take the link from the slot each time it delivers.
pub(crate) struct EventFlusher {
    pub(crate) target_id: Uuid,
    pub(crate) journal: Journal,
    /// Runs whose event batches Lemma has rejected, and how often. Kept per
    /// run so one unhappy run cannot stop delivery for every other.
    pub(crate) rejections: HashMap<Uuid, u32>,
}

/// Most events one delivery pass reads from the journal.
pub(crate) const FLUSH_LIMIT: usize = 1024;

/// What one delivery pass did.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub(crate) struct Flushed {
    /// Lemma acknowledged something.
    pub(crate) delivered: bool,
    /// The journal may still owe Lemma events this pass did not send: the
    /// read was cut off at [`FLUSH_LIMIT`], or a refusal rewound a run for
    /// replay or left its events to be dropped. Whoever flushes has to pass
    /// again -- nothing else is going to ask.
    pub(crate) more: bool,
}

impl EventFlusher {
    /// Hand journaled events to Lemma, keeping each run's failures to itself.
    ///
    /// One pass: at most [`FLUSH_LIMIT`] events. [`Flushed::more`] says
    /// whether another is due. Only a link failure is an error: a batch Lemma
    /// rejects on its own merits belongs to exactly one run, and is contained
    /// there.
    pub(crate) async fn flush(&mut self, link: &LinkHandle) -> anyhow::Result<Flushed> {
        let target_id = self.target_id;
        let mut flushed = Flushed::default();
        // Runs whose remaining batches this pass must leave alone, because the
        // journal no longer matches the batches read at the top of the loop.
        let mut stale = HashSet::new();
        let batches = self.journal.pending_events(target_id, FLUSH_LIMIT)?;
        let read: usize = batches.iter().map(|batch| batch.events.len()).sum();
        flushed.more = read >= FLUSH_LIMIT;
        for batch in batches {
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
                // The run's later batches were skipped, and other runs' may
                // sit behind them.
                flushed.more = true;
                tracing::warn!(
                    %run_id,
                    dropped,
                    "dropping events Lemma will not accept for this run"
                );
                continue;
            }
            match link.append_events(&batch).await {
                Ok(ack) => {
                    self.rejections.remove(&run_id);
                    self.journal.acknowledge_events(target_id, &ack)?;
                    flushed.delivered = true;
                }
                Err(error) if error.is_request_rejected() => {
                    stale.insert(run_id);
                    self.reject_run_events(run_id, lease_epoch, &error)?;
                    // Either rewound for a replay or due to be dropped; both
                    // happen on the next pass.
                    flushed.more = true;
                }
                Err(error) => return Err(error.into()),
            }
        }
        Ok(flushed)
    }

    /// Pass until the journal owes Lemma nothing this flusher can send.
    ///
    /// Bounded: each pass either acknowledges, rewinds or drops something,
    /// and a run is rewound at most once before it is dropped.
    pub(crate) async fn drain(&mut self, link: &LinkHandle) -> anyhow::Result<bool> {
        let mut delivered = false;
        loop {
            let flushed = self.flush(link).await?;
            delivered |= flushed.delivered;
            if !flushed.more {
                return Ok(delivered);
            }
        }
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
        error: &LinkError,
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
/// Waits for the same signal the run tasks raise, lingers a moment so a burst
/// of streamed chunks goes out as one frame rather than one each, then sends
/// on whichever link is open. It takes the lock the shutdown flush also takes,
/// so the two are never in flight together.
///
/// A notification starts a drain, not a pass: while a pass reports more to
/// send, the next one follows without waiting to be woken. A run that finished
/// while the link was down raised its last notification long ago, so a backlog
/// larger than one pass -- or a replay a refusal just scheduled -- would
/// otherwise sit in the journal until some unrelated event came along.
pub(crate) async fn deliver_events(
    flusher: Arc<tokio::sync::Mutex<EventFlusher>>,
    signal: OutboxSignal,
    mut link: LinkSlot,
    mut shutdown: watch::Receiver<bool>,
) {
    let ready = signal.events();
    let mut retry = RETRY_MIN;
    let mut consecutive_failures: u32 = 0;
    // Whether the journal is known to still owe Lemma something, so the next
    // pass is due without a notification.
    let mut owed = false;
    loop {
        if !owed {
            tokio::select! {
                () = ready.notified() => {}
                _ = shutdown.changed() => return,
            }
            if *shutdown.borrow() {
                return;
            }
            // Coalesce. An agent streams a chunk every few milliseconds; each
            // used to be its own request.
            tokio::time::sleep(EVENT_LINGER).await;
        }
        if *shutdown.borrow() {
            return;
        }
        let handle = tokio::select! {
            handle = link.wait() => handle,
            _ = shutdown.changed() => return,
        };
        let Some(handle) = handle else {
            return;
        };
        match flusher.lock().await.flush(&handle).await {
            Ok(pass) => {
                if consecutive_failures >= EVENT_RETRY_QUIET {
                    tracing::warn!(
                        failures = consecutive_failures,
                        "Agent Host event delivery recovered"
                    );
                }
                retry = RETRY_MIN;
                consecutive_failures = 0;
                if pass.delivered {
                    // A terminal checkpoint waits for its run's events to be
                    // acknowledged; they just were.
                    signal.notify_control();
                }
                owed = pass.more;
            }
            Err(error) => {
                consecutive_failures += 1;
                // The first failures are ordinary -- a link dropping between
                // reconnects -- and stay quiet. A run of them is what nobody
                // could see: events are what a person is waiting on, so
                // silence here reads to them as an agent that stopped.
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
                // The events are still pending.
                owed = true;
            }
        }
    }
}
