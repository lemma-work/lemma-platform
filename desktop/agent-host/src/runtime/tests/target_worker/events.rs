//! Getting a run's events to the workspace, and what a refusal costs.

use super::*;

/// The finding: a run's output reached Lemma only by abandoning the poll.
///
/// Delivery used to be an arm of the same `select!` as the poll, so every
/// event a run streamed cancelled the poll in flight and opened a new one.
/// The server never learned the old one was gone and held it for the rest
/// of its 25 seconds — one host streaming a single answer stacked 26
/// concurrent polls against exactly one while idle.
///
/// This drives delivery with no poll running at all, which is only a
/// meaningful thing to ask because the two are now independent.
#[tokio::test]
async fn a_runs_events_reach_lemma_with_no_poll_involved() {
    let harness = Harness::new().await;
    let run_id = harness.seed_run(3);

    let (_shutdown_tx, shutdown) = watch::channel(false);
    let delivery = tokio::spawn(deliver_events(
        Arc::clone(&harness.worker.flusher),
        Arc::clone(&harness.worker.events_ready),
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

    // And it keeps serving. A run streams for its whole turn, so delivering
    // the first batch and then going quiet until the poll came back is the
    // same defect in a different place.
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

/// Shutting the loop down must not strand what the journal still holds.
///
/// The delivery task is aborted when the poll loop ends, so the last flush
/// belongs to the shutdown path. Both take the same lock, which is what
/// stops them sending one batch twice.
#[tokio::test]
async fn events_journaled_after_delivery_stops_are_still_sent() {
    let mut harness = Harness::new().await;

    let (shutdown_tx, shutdown) = watch::channel(false);
    let delivery = tokio::spawn(deliver_events(
        Arc::clone(&harness.worker.flusher),
        Arc::clone(&harness.worker.events_ready),
        shutdown,
    ));
    let _ = shutdown_tx.send(true);
    let _ = delivery.await;

    // Journaled with nothing left running to notice.
    let run_id = harness.seed_run(2);
    harness.worker.flush_events().await.unwrap();

    assert_eq!(harness.accepted().get(&run_id), Some(&2));
    assert!(harness.pending(run_id).is_empty());
}

/// The finding: one run Lemma refuses used to abort the whole flush and
/// make the caller skip its poll, which is the lease heartbeat for every
/// other run on the host.
#[tokio::test]
async fn a_refused_run_neither_stops_the_flush_nor_fails_it() {
    let mut harness = Harness::new().await;
    let poisoned = harness.seed_run(3);
    let healthy = harness.seed_run(2);
    harness.stub.refused.lock().unwrap().push(poisoned);

    harness
        .worker
        .flush_events()
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
    harness.worker.flush_events().await.unwrap();
    assert_eq!(harness.accepted().get(&run_id), Some(&3));
    assert!(harness.pending(run_id).is_empty());

    // Lemma loses the stream: it now refuses a batch that starts above the
    // sequence it expects.
    harness.stub.refused.lock().unwrap().push(run_id);
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

    harness.worker.flush_events().await.unwrap();

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
    harness.stub.refused.lock().unwrap().push(poisoned);

    for _ in 0..3 {
        harness.worker.flush_events().await.unwrap();
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
            &EventAck {
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
    harness.server.abort();
    // Let the listener actually close before the flush tries to use it.
    tokio::time::sleep(Duration::from_millis(50)).await;

    assert!(harness.worker.flush_events().await.is_err());
}

/// The poll is the lease heartbeat for every run on the host and the only
/// way commands come back down, and it carries every run's checkpoint in
/// one batch. One refused checkpoint used to fail that whole request, which
/// the worker read as the target being offline -- so every other run's
/// lease expired underneath it while its provider kept working.
#[tokio::test]
async fn a_refused_checkpoint_does_not_hold_back_every_other_run() {
    let mut harness = Harness::new().await;
    let healthy = harness.seed_run(0);
    let poisoned = harness.seed_run(0);
    harness
        .stub
        .refused_checkpoints
        .lock()
        .unwrap()
        .push(poisoned);

    let response = harness
        .worker
        .poll_target(capacity())
        .await
        .expect("one refused checkpoint is not the target going offline");

    assert_eq!(response.host_status, HostStatus::Online);
    let applied = harness.stub.applied_checkpoints.lock().unwrap().clone();
    assert!(
        applied.iter().any(|(run_id, _)| *run_id == healthy),
        "the healthy run's checkpoint must still be applied, got {applied:?}"
    );
    assert!(applied.iter().all(|(run_id, _)| *run_id != poisoned));
    assert!(
        harness.worker.refused_heartbeats.contains_key(&poisoned),
        "the refused run must be named, not left to poison every later poll"
    );
}

/// Event delivery must not go quiet for a minute over transient failures.
///
/// Delivery used to share the poll loop's thirty-second ceiling. Doubling
/// from 500ms, a run of failures spends 0.5 + 1 + 2 + 4 + 8 + 16 + 30 =
/// 61.5s before the eighth attempt -- on a control plane the poll loop is
/// separately, successfully talking to the whole time. A run can finish
/// inside that window with none of its output delivered, and the retries
/// were logged at `debug`, which the host does not emit, so there was
/// nothing to find afterwards.
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
