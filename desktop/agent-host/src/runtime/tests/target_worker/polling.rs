//! Asking for work, and the answers that cost extra requests.

use super::harnesses::echo_manifest;
use super::*;

#[tokio::test]
async fn a_closed_supervisor_channel_stops_the_worker_without_refreshing() {
    let mut harness = Harness::with_manifest(echo_manifest()).await;
    harness.worker.refresh_due = std::time::Instant::now() + Duration::from_secs(60);
    // This fixture's change sender has already gone away, as it does when
    // the supervisor errors or is cancelled. Closure is not an install.
    tokio::time::timeout(Duration::from_secs(2), harness.worker.poll_loop())
        .await
        .expect("a closed channel must not spin the poll loop")
        .unwrap();
    assert!(harness.worker.probe_task.is_none());
    assert!(harness.worker.draining);
}

/// A refusal is usually transient. Holding the heartbeat back forever meant
/// one blip sentenced a healthy run to lease expiry and `DISPATCH_UNKNOWN`,
/// so the hold has to lapse on its own.
#[tokio::test]
async fn a_refused_heartbeat_is_retried_after_backing_off() {
    let mut harness = Harness::new().await;
    let poisoned = harness.seed_run(0);
    harness
        .stub
        .refused_checkpoints
        .lock()
        .unwrap()
        .push(poisoned);
    harness.worker.poll_target(capacity()).await.unwrap();
    assert!(harness.worker.refused_heartbeats.contains_key(&poisoned));

    // The server recovers; the host must eventually offer the run again.
    harness.stub.refused_checkpoints.lock().unwrap().clear();
    for _ in 0..super::REFUSED_HEARTBEAT_RETRY_POLLS {
        harness.worker.poll_target(capacity()).await.unwrap();
    }

    assert!(
        !harness.worker.refused_heartbeats.contains_key(&poisoned),
        "the hold must lapse, or the run's lease expires under a healthy host"
    );
    let applied = harness.stub.applied_checkpoints.lock().unwrap().clone();
    assert!(
        applied.iter().any(|(run_id, _)| *run_id == poisoned),
        "the run's heartbeat must resume once the refusal passes"
    );
}

/// Narrowing must not lose commands: a cancellation handed back by one of
/// the probing requests still has to reach the caller.
#[tokio::test]
async fn commands_survive_a_narrowed_poll() {
    let mut harness = Harness::new().await;
    harness.seed_run(0);
    let poisoned = harness.seed_run(0);
    harness
        .stub
        .refused_checkpoints
        .lock()
        .unwrap()
        .push(poisoned);
    let cancel = Command {
        command_id: Uuid::new_v4(),
        kind: CommandKind::CancelRun,
        created_at: Utc::now(),
        expires_at: Utc::now() + chrono::Duration::minutes(1),
        run_id: Some(Uuid::new_v4()),
        lease_epoch: Some(1),
        payload: serde_json::Value::Null,
    };
    harness
        .stub
        .undelivered_commands
        .lock()
        .unwrap()
        .push(cancel.clone());

    let response = harness.worker.poll_target(capacity()).await.unwrap();

    assert_eq!(
        response
            .commands
            .iter()
            .map(|command| command.command_id)
            .collect::<Vec<_>>(),
        vec![cancel.command_id],
        "a command delivered by a probing request must not be dropped with it"
    );
}

/// Once the offender is named, later polls carry the batch in one request
/// again -- the narrowing is a repair, not a permanent tax.
#[tokio::test]
async fn a_named_offender_stops_costing_extra_requests() {
    let mut harness = Harness::new().await;
    harness.seed_run(0);
    let poisoned = harness.seed_run(0);
    harness
        .stub
        .refused_checkpoints
        .lock()
        .unwrap()
        .push(poisoned);

    harness.worker.poll_target(capacity()).await.unwrap();
    let after_repair = *harness.stub.polls.lock().unwrap();
    harness.worker.poll_target(capacity()).await.unwrap();

    assert_eq!(
        *harness.stub.polls.lock().unwrap() - after_repair,
        1,
        "a later poll must cost exactly one request"
    );
}

/// The bisect addresses three lists as one sequence, so the index
/// arithmetic is load-bearing: an off-by-one would drop a control update.
#[test]
fn slicing_reads_the_three_control_lists_as_one_sequence() {
    let runs = [Uuid::new_v4(), Uuid::new_v4()];
    let batch = super::ControlBatch {
        command_ids: vec![Uuid::new_v4(), Uuid::new_v4()],
        checkpoints: runs
            .iter()
            .map(|run_id| crate::protocol::RunCheckpoint {
                run_id: *run_id,
                lease_epoch: 1,
                state: RunState::Running,
                detail: JsonMap::new(),
            })
            .collect(),
        rejections: vec![crate::protocol::CommandRejection {
            command_id: Uuid::new_v4(),
            run_id: Uuid::new_v4(),
            lease_epoch: 1,
            code: crate::protocol::RejectionCode::InvalidCommand,
            retryable: false,
            detail: None,
        }],
    };
    assert_eq!(batch.len(), 5);

    // Every single-element window addresses exactly one update, and the
    // windows tile the batch without gaps or repeats.
    let windows = (0..batch.len())
        .map(|index| batch.slice(index, index + 1))
        .collect::<Vec<_>>();
    assert!(windows.iter().all(|window| window.len() == 1));
    let mut rebuilt = super::ControlBatch::default();
    for window in windows {
        rebuilt.absorb(window);
    }
    assert_eq!(rebuilt.command_ids, batch.command_ids);
    assert_eq!(rebuilt.checkpoints.len(), 2);
    assert_eq!(rebuilt.rejections.len(), 1);

    // A window straddling two kinds carries the tail of one and the head
    // of the next.
    let straddle = batch.slice(1, 4);
    assert_eq!(straddle.command_ids, vec![batch.command_ids[1]]);
    assert_eq!(straddle.checkpoints.len(), 2);
    assert!(straddle.rejections.is_empty());

    assert!(batch.slice(0, 0).is_empty());
    assert_eq!(batch.slice(0, batch.len()).len(), batch.len());
}

/// A target that refuses even an empty poll is genuinely unreachable, and
/// must still put the worker into its offline retry path.
#[tokio::test]
async fn a_target_that_refuses_everything_still_fails_the_poll() {
    let mut harness = Harness::new().await;
    harness.server.abort();
    tokio::time::sleep(Duration::from_millis(50)).await;

    assert!(harness.worker.poll_target(capacity()).await.is_err());
}
