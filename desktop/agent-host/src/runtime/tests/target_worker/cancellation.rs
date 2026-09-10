//! Asking a run to stop, and killing it when it will not.

use super::*;

#[tokio::test]
async fn cancelling_a_task_join_does_not_detach_the_child() {
    let (owner, dropped) = tokio::sync::oneshot::channel::<()>();
    let child = super::OwnedTask(tokio::spawn(async move {
        let _owner = owner;
        std::future::pending::<()>().await;
    }));
    let parent = tokio::spawn(child.join());
    tokio::task::yield_now().await;
    parent.abort();
    assert!(parent.await.unwrap_err().is_cancelled());
    assert!(
        tokio::time::timeout(Duration::from_secs(2), dropped)
            .await
            .unwrap()
            .is_err()
    );
}

/// The run task parks a permission request and is then dropped -- exactly
/// what `spawn_run`'s deadline `timeout` does to an ACP handler that is
/// waiting on a decision. `wait`'s own cleanup never runs in that case, so
/// the worker has to sweep the run when it reaps the task.
#[tokio::test]
async fn a_run_that_ends_without_being_cancelled_leaves_nothing_parked() {
    let mut harness = Harness::new().await;
    let run_id = harness.seed_run(0);
    let gate = harness.worker.permissions.clone();
    let handle = tokio::spawn(async move {
        let _ = tokio::time::timeout(
            Duration::from_millis(20),
            gate.wait(run_id, "call-1".to_owned(), Duration::from_secs(600), None),
        )
        .await;
        Ok(())
    });
    harness.worker.track_run(run_id, handle);
    while !harness.worker.active_runs[&run_id].handle.is_finished() {
        tokio::time::sleep(Duration::from_millis(5)).await;
    }
    assert_eq!(
        harness.worker.permissions.parked(),
        1,
        "the dropped handler is expected to leave its request behind"
    );

    harness.worker.reap_finished().await;

    assert_eq!(harness.worker.permissions.parked(), 0);
}

/// A cancel asks first and kills second, so an adapter that ignores
/// `session/cancel` still cannot outlive its cancellation.
#[tokio::test]
async fn a_cancel_asks_the_run_to_stop_before_killing_it() {
    let mut harness = Harness::new().await;
    let run_id = harness.seed_run(0);
    let handle = tokio::spawn(async move {
        std::future::pending::<()>().await;
        Ok(())
    });
    harness.worker.track_run(run_id, handle);

    harness
        .worker
        .handle_cancel(&cancel_command(run_id))
        .unwrap();

    let active = &harness.worker.active_runs[&run_id];
    assert!(
        *active.cancel.borrow(),
        "the run must be asked to stop through ACP first"
    );
    assert!(
        !active.handle.is_finished(),
        "it must not be killed outright"
    );
    assert!(
        !harness
            .journal
            .get_run(harness.target_id, run_id)
            .unwrap()
            .unwrap()
            .state
            .is_terminal(),
        "the run terminalizes itself once the agent honours the cancel"
    );

    // The grace elapses without the agent stopping.
    harness.worker.active_runs.get_mut(&run_id).unwrap().kill_at =
        Some(tokio::time::Instant::now());
    harness.worker.enforce_cancellations().unwrap();

    assert_eq!(
        harness
            .journal
            .get_run(harness.target_id, run_id)
            .unwrap()
            .unwrap()
            .state,
        RunState::Cancelled
    );
}

/// Quitting Lemma with an answer still streaming.
///
/// Shutdown used to wait out its grace and then abort whatever was left,
/// which drops the ACP connection and SIGKILLs the agent's process group.
/// The provider had not yet written the session file the conversation's
/// *next* turn resumes from, so quitting during a long turn silently cost
/// that conversation its history -- the same failure the Stop path was
/// reworked to avoid, on a route that never got the fix.
#[tokio::test]
async fn shutdown_asks_a_streaming_run_to_stop_before_it_kills_anything() {
    let mut harness = Harness::new().await;
    let run_id = harness.seed_run(0);
    let handle = tokio::spawn(async move {
        std::future::pending::<()>().await;
        Ok(())
    });
    harness.worker.track_run(run_id, handle);

    let permissions = harness.worker.permissions.clone();
    let parked = tokio::spawn(async move {
        permissions
            .wait(
                run_id,
                "approval-1".to_owned(),
                Duration::from_secs(300),
                None,
            )
            .await
    });
    while harness.worker.permissions.parked() == 0 {
        tokio::time::sleep(Duration::from_millis(5)).await;
    }

    // Only the signalling half; the loop that follows it would wait out
    // the full grace against a run that never ends.
    harness.worker.draining = true;
    let signalled = tokio::time::Instant::now() + CANCEL_KILL_AFTER;
    for active in harness.worker.active_runs.values_mut() {
        active.cancel.send_replace(true);
        if active.kill_at.is_none() {
            active.kill_at = Some(signalled);
        }
    }
    for id in harness
        .worker
        .active_runs
        .keys()
        .copied()
        .collect::<Vec<_>>()
    {
        harness.worker.permissions.abandon_run(id);
    }

    let active = &harness.worker.active_runs[&run_id];
    assert!(
        *active.cancel.borrow(),
        "the agent must be asked through ACP, so it can flush its session"
    );
    assert!(
        !active.handle.is_finished(),
        "shutdown must not abort the turn outright"
    );
    assert!(
        active.kill_at.is_some(),
        "an agent that ignores the request still has to be reaped"
    );
    assert_eq!(
        parked.await.unwrap(),
        PermissionDecision::Deny,
        "an approval nobody will answer must not hold the turn open through shutdown"
    );
}

/// Stop, pressed while the agent is waiting on an approval.
///
/// Adapters block the turn on an outstanding `request_permission`, so the
/// agent cannot answer `session/cancel` while a prompt nobody will now
/// respond to is still parked. The turn ran out its grace period, the run
/// was recorded as a failure, and the message was rewritten to say the
/// coding agent had crashed -- to a user whose only action was Stop.
/// Cancelling has to release the waiters as well as raise the flag.
#[tokio::test]
async fn cancelling_releases_an_approval_the_agent_is_still_waiting_on() {
    let mut harness = Harness::new().await;
    let run_id = harness.seed_run(0);
    let handle = tokio::spawn(async move {
        std::future::pending::<()>().await;
        Ok(())
    });
    harness.worker.track_run(run_id, handle);

    // The agent asks, and blocks its turn until someone answers.
    let permissions = harness.worker.permissions.clone();
    let parked = tokio::spawn(async move {
        permissions
            .wait(
                run_id,
                "approval-1".to_owned(),
                Duration::from_secs(300),
                None,
            )
            .await
    });
    while harness.worker.permissions.parked() == 0 {
        tokio::time::sleep(Duration::from_millis(5)).await;
    }

    harness
        .worker
        .handle_cancel(&cancel_command(run_id))
        .unwrap();

    assert_eq!(
        parked.await.unwrap(),
        PermissionDecision::Deny,
        "a cancelled turn will never use the permission, so the agent must be told at once"
    );
    assert_eq!(
        harness.worker.permissions.parked(),
        0,
        "cancelling must leave nothing waiting on a user who has already stopped the run"
    );
}

/// `abort` only requests cancellation, so the run task can still park one
/// more request after the kill fallback has swept the gate. Keeping the
/// handle until it is reaped is what closes that window.
#[tokio::test]
async fn cancelling_a_run_sweeps_a_request_parked_on_the_way_out() {
    let mut harness = Harness::new().await;
    let run_id = harness.seed_run(0);
    let handle = tokio::spawn(async move {
        std::future::pending::<()>().await;
        Ok(())
    });
    harness.worker.track_run(run_id, handle);

    harness
        .worker
        .handle_cancel(&cancel_command(run_id))
        .unwrap();
    // Skip the grace: this test is about the kill path, not its delay.
    harness.worker.active_runs.get_mut(&run_id).unwrap().kill_at =
        Some(tokio::time::Instant::now());
    harness.worker.enforce_cancellations().unwrap();

    // The aborted task gets one more poll before the runtime drops it,
    // which is long enough to register a request nobody will answer.
    let gate = harness.worker.permissions.clone();
    let late = tokio::spawn(async move {
        gate.wait(run_id, "late".to_owned(), Duration::from_secs(600), None)
            .await
    });
    while harness.worker.permissions.parked() == 0 {
        tokio::time::sleep(Duration::from_millis(5)).await;
    }

    for _ in 0..100 {
        harness.worker.reap_finished().await;
        if harness.worker.permissions.parked() == 0 {
            break;
        }
        tokio::time::sleep(Duration::from_millis(5)).await;
    }

    let decision = tokio::time::timeout(Duration::from_secs(5), late)
        .await
        .expect("a request parked during cancellation must not wait for its timeout")
        .unwrap();
    assert_eq!(decision, PermissionDecision::Deny);
}
