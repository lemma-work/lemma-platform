//! Getting a run's events to the workspace, and what a refusal costs.

use super::*;

/// A run's output reaches Lemma on its own task, as it is journaled, with
/// nothing else about the worker running -- and keeps doing so for the whole
/// turn rather than delivering the first batch and going quiet.
#[tokio::test]
async fn a_runs_events_reach_lemma_as_they_are_journaled() {
    let harness = Harness::new().await;
    let run_id = harness.seed_run(3);

    let (_shutdown_tx, shutdown) = watch::channel(false);
    let delivery = tokio::spawn(deliver_events(
        Arc::clone(&harness.worker.flusher),
        harness.worker.events_ready.clone(),
        harness.worker.link.clone(),
        shutdown,
    ));
    // Exactly what a run task does the moment it journals an event.
    harness.worker.events_ready.notify_one();

    // Both halves waited for, not one waited for and the other asserted.
    // The journal is cleared *after* the server accepts, so "accepted == 3"
    // is reached first and a bare assert on `pending` races the flusher
    // finishing its own bookkeeping -- which is what failed here, at the
    // second of these two sites, roughly one run in a hundred and fifty.
    within(
        Duration::from_secs(5),
        "the run's events to reach Lemma and leave the journal",
        || harness.accepted().get(&run_id) == Some(&3) && harness.pending(run_id).is_empty(),
    )
    .await;

    // And it keeps serving: a run streams for its whole turn.
    for _ in 0..4 {
        harness
            .journal
            .append_event(
                harness.target_id,
                run_id,
                1,
                EventType::AgentMessageChunk,
                None,
                JsonMap::new(),
            )
            .unwrap();
    }
    harness.worker.events_ready.notify_one();

    within(
        Duration::from_secs(5),
        "later events to reach Lemma and leave the journal",
        || harness.accepted().get(&run_id) == Some(&7) && harness.pending(run_id).is_empty(),
    )
    .await;

    delivery.abort();
}

/// A backlog bigger than one read is delivered whole, terminal event
/// included, without anything journaling another event to prompt it.
///
/// Each pass reads at most 1,024 events. Delivery used to flush once per
/// notification, so a run that finished while the link was down -- its last
/// notification long spent -- had its first passes delivered on reconnect and
/// the rest, terminal event and all, left in the journal until something else
/// happened to wake delivery.
#[tokio::test]
async fn a_backlog_from_offline_is_drained_on_reconnect() {
    let harness = Harness::new().await;
    // The link is down while the run streams and finishes.
    harness.worker.slot_owner.set(None);
    let run_id = harness.seed_run(2_500);
    super::terminal_failure(
        &harness.journal,
        harness.target_id,
        run_id,
        1,
        RunState::Failed,
        "finished while offline",
    )
    .unwrap();
    let terminal = *harness.pending(run_id).last().unwrap();
    assert!(terminal > 2_500);

    let (_shutdown_tx, shutdown) = watch::channel(false);
    let delivery = tokio::spawn(deliver_events(
        Arc::clone(&harness.worker.flusher),
        harness.worker.events_ready.clone(),
        harness.worker.link.clone(),
        shutdown,
    ));
    // The run's own notifications, raised while there was no link.
    harness.worker.events_ready.notify_one();
    tokio::time::sleep(Duration::from_millis(50)).await;
    // Reconnecting, exactly as `session` does: publish the link, kick once.
    harness.worker.slot_owner.set(Some(harness.link.clone()));
    harness.worker.events_ready.notify_one();

    within(
        Duration::from_secs(10),
        "the whole backlog, terminal event included, to reach Lemma",
        || harness.accepted().get(&run_id) == Some(&terminal) && harness.pending(run_id).is_empty(),
    )
    .await;
    delivery.abort();
}

/// A replay is scheduled like any other delivery: it goes out without
/// waiting for the run to journal something new.
#[tokio::test]
async fn a_replay_after_a_refusal_goes_out_on_its_own() {
    let harness = Harness::new().await;
    let run_id = harness.seed_run(3);
    let (_shutdown_tx, shutdown) = watch::channel(false);
    let delivery = tokio::spawn(deliver_events(
        Arc::clone(&harness.worker.flusher),
        harness.worker.events_ready.clone(),
        harness.worker.link.clone(),
        shutdown,
    ));
    harness.worker.events_ready.notify_one();
    within(Duration::from_secs(5), "the first events to land", || {
        harness.pending(run_id).is_empty()
    })
    .await;

    // Lemma loses the stream, refuses the next batch once, and takes the
    // replay.
    harness.stub.refused_once_runs.lock().unwrap().push(run_id);
    harness
        .journal
        .append_event(
            harness.target_id,
            run_id,
            1,
            EventType::AgentMessageChunk,
            None,
            JsonMap::new(),
        )
        .unwrap();
    harness.worker.events_ready.notify_one();

    within(
        Duration::from_secs(5),
        "the replayed history to be delivered",
        || harness.pending(run_id).is_empty() && harness.accepted().get(&run_id) == Some(&4),
    )
    .await;
    assert!(
        harness
            .stub
            .accepted
            .lock()
            .unwrap()
            .iter()
            .filter(|(run, _)| *run == run_id)
            .count()
            >= 2,
        "the run's history must have been sent again after the refusal"
    );
    delivery.abort();
}

/// Shutting the loop down must not strand what the journal still holds.
///
/// The delivery task is aborted when the link loop ends, so the last flush
/// belongs to the shutdown path. Both take the same lock, which is what
/// stops them sending one batch twice.
#[tokio::test]
async fn events_journaled_after_delivery_stops_are_still_sent() {
    let mut harness = Harness::new().await;

    let (shutdown_tx, shutdown) = watch::channel(false);
    let delivery = tokio::spawn(deliver_events(
        Arc::clone(&harness.worker.flusher),
        harness.worker.events_ready.clone(),
        harness.worker.link.clone(),
        shutdown,
    ));
    let _ = shutdown_tx.send(true);
    let _ = delivery.await;

    // Journaled with nothing left running to notice.
    let run_id = harness.seed_run(2);
    harness.worker.flush_events(&harness.link).await.unwrap();

    assert_eq!(harness.accepted().get(&run_id), Some(&2));
    assert!(harness.pending(run_id).is_empty());
}

/// One run Lemma refuses must not abort the whole flush: that would starve
/// every other run on the host of delivery.
#[tokio::test]
async fn a_refused_run_neither_stops_the_flush_nor_fails_it() {
    let mut harness = Harness::new().await;
    let poisoned = harness.seed_run(3);
    let healthy = harness.seed_run(2);
    harness.stub.refused_runs.lock().unwrap().push(poisoned);

    harness
        .worker
        .flush_events(&harness.link)
        .await
        .expect("a run Lemma refuses is not a target-level failure");

    assert_eq!(
        harness.accepted().get(&healthy),
        Some(&2),
        "the healthy run's events must still reach Lemma"
    );
    assert!(harness.pending(healthy).is_empty());
}

/// A refusal is answered by replaying the run's journaled history, which is
/// what an emptied server-side stream needs to see.
#[tokio::test]
async fn a_refusal_replays_the_run_from_its_first_event() {
    let mut harness = Harness::new().await;
    let run_id = harness.seed_run(3);
    harness.worker.flush_events(&harness.link).await.unwrap();
    assert_eq!(harness.accepted().get(&run_id), Some(&3));
    assert!(harness.pending(run_id).is_empty());

    // Lemma loses the stream: it now refuses a batch that starts above the
    // sequence it expects.
    harness.stub.refused_runs.lock().unwrap().push(run_id);
    harness
        .journal
        .append_event(
            harness.target_id,
            run_id,
            1,
            EventType::AgentMessageChunk,
            None,
            JsonMap::new(),
        )
        .unwrap();

    harness.worker.flush_events(&harness.link).await.unwrap();

    assert_eq!(
        harness.pending(run_id),
        vec![1, 2, 3, 4],
        "the acknowledged events have to survive locally to be replayable"
    );
}

/// A run Lemma keeps refusing is given up on rather than left to block the
/// flush loop, and its terminal checkpoint stops being held hostage.
#[tokio::test]
async fn a_run_lemma_keeps_refusing_is_eventually_dropped() {
    let mut harness = Harness::new().await;
    let poisoned = harness.seed_run(3);
    let healthy = harness.seed_run(1);
    harness.stub.refused_runs.lock().unwrap().push(poisoned);

    for _ in 0..3 {
        harness.worker.flush_events(&harness.link).await.unwrap();
    }

    assert!(
        harness.pending(poisoned).is_empty(),
        "the undeliverable run must stop being retried forever"
    );
    assert_eq!(harness.accepted().get(&healthy), Some(&1));
}

/// A dead run has to still be able to say why it died.
///
/// The reason used to be written only into the terminal event, and an event
/// is pruned once Lemma acknowledges it. So a run inspected any later than
/// that offered `FAILED` and an empty detail, and its cause was gone for
/// good — exactly when someone is asking why the agent stopped.
#[tokio::test]
async fn a_failed_run_keeps_its_reason_once_the_terminal_event_is_acknowledged() {
    let harness = Harness::new().await;
    let run_id = harness.seed_run(0);
    let reason = "the provider never answered the approved permission";

    super::terminal_failure(
        &harness.journal,
        harness.target_id,
        run_id,
        1,
        RunState::Failed,
        reason,
    )
    .unwrap();

    // Acknowledging is what makes the terminal event prunable, and it is
    // also what releases the terminal checkpoint to be sent.
    let acked_through = *harness
        .pending(run_id)
        .last()
        .expect("the failure must journal a terminal event");
    harness
        .journal
        .acknowledge_events(
            harness.target_id,
            &crate::protocol::EventAck {
                run_id,
                lease_epoch: 1,
                acked_through,
            },
        )
        .unwrap();

    let (_, checkpoints, _) = harness.journal.pending_control(harness.target_id).unwrap();
    let terminal = checkpoints
        .iter()
        .find(|checkpoint| checkpoint.run_id == run_id)
        .expect("a terminal run must report a checkpoint");
    assert_eq!(terminal.state, RunState::Failed);
    assert_eq!(
        terminal.detail.get("message"),
        Some(&serde_json::json!(reason)),
        "a dead run must still be able to say why",
    );
}

/// An unreachable or unauthenticated target is not one run's problem, so it
/// still surfaces as a failure that puts the worker into its retry path.
#[tokio::test]
async fn a_target_level_failure_still_fails_the_flush() {
    let mut harness = Harness::new().await;
    harness.seed_run(1);
    // The link goes away underneath the flush.
    harness.link.close(1000, "test");
    within(Duration::from_secs(5), "the link to close", || {
        harness.link.is_closed()
    })
    .await;

    assert!(harness.worker.flush_events(&harness.link).await.is_err());
}

/// Event delivery must not go quiet for a minute over transient failures.
///
/// Delivery once shared the reconnect loop's thirty-second ceiling. Doubling
/// from 500ms, a run of failures spends 0.5 + 1 + 2 + 4 + 8 + 16 + 30 =
/// 61.5s before the eighth attempt. A run can finish inside that window with
/// none of its output delivered.
///
/// The numbers rather than the constant, because the constant is only
/// meaningful as the total silence it permits.
#[test]
fn event_delivery_backs_off_in_seconds_not_minutes() {
    use super::{EVENT_RETRY_MAX, RETRY_MAX, RETRY_MIN};

    fn silence_over(attempts: u32, ceiling: Duration) -> Duration {
        let mut retry = RETRY_MIN;
        let mut total = Duration::ZERO;
        for _ in 0..attempts {
            total += retry;
            retry = (retry * 2).min(ceiling);
        }
        total
    }

    // What it was: over a minute before the eighth try.
    assert!(silence_over(7, RETRY_MAX) > Duration::from_secs(60));
    // What it is: 0.5 + 1 + 2 x 6 = 13.5s for eight attempts, so a stalled
    // delivery reads as slowness rather than as an agent that stopped.
    assert!(
        silence_over(8, EVENT_RETRY_MAX) < Duration::from_secs(15),
        "eight delivery attempts spend {:?}",
        silence_over(8, EVENT_RETRY_MAX),
    );
    // And it must stay well inside the 90s a permission-flow run is given,
    // which is the budget this overran.
    assert!(silence_over(20, EVENT_RETRY_MAX) < Duration::from_secs(45));
}
