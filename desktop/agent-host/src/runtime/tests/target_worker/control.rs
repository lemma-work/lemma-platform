//! What the host reports up on the link, and how the loop treats the link.

use super::harnesses::echo_manifest;
use super::*;

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
    harness.worker.send_control(&harness.link).await.unwrap();
    assert!(harness.worker.refused_heartbeats.contains_key(&poisoned));

    // Held back while the backoff lasts...
    harness.stub.refused_checkpoints.lock().unwrap().clear();
    harness.worker.send_control(&harness.link).await.unwrap();
    let applied = harness.stub.applied_checkpoints.lock().unwrap().clone();
    assert!(applied.iter().all(|(run_id, _)| *run_id != poisoned));

    // ...and offered again once it lapses.
    harness
        .worker
        .refused_heartbeats
        .insert(poisoned, std::time::Instant::now());
    harness.worker.send_control(&harness.link).await.unwrap();
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

/// Lemma applies each control update on its own and names only the ones it
/// refused. One refused checkpoint must therefore cost nothing but itself:
/// every other run's heartbeat -- which is what renews its lease -- still
/// lands in the same frame.
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

    harness
        .worker
        .send_control(&harness.link)
        .await
        .expect("one refused checkpoint is not the link failing");

    let applied = harness.stub.applied_checkpoints.lock().unwrap().clone();
    assert!(
        applied.iter().any(|(run_id, _)| *run_id == healthy),
        "the healthy run's checkpoint must still be applied, got {applied:?}"
    );
    assert!(applied.iter().all(|(run_id, _)| *run_id != poisoned));
    assert!(harness.worker.refused_heartbeats.contains_key(&poisoned));
    assert_eq!(
        *harness.stub.controls.lock().unwrap(),
        1,
        "the refusal must not cost a second request"
    );
}

/// A run's final state is the one update the host never gives up on.
#[tokio::test]
async fn a_refused_terminal_checkpoint_stays_queued() {
    let mut harness = Harness::new().await;
    let run_id = harness.seed_run(0);
    super::terminal_failure(
        &harness.journal,
        harness.target_id,
        run_id,
        1,
        RunState::Failed,
        "stopped",
    )
    .unwrap();
    harness.worker.flush_events(&harness.link).await.unwrap();
    harness
        .stub
        .refused_checkpoints
        .lock()
        .unwrap()
        .push(run_id);

    harness.worker.send_control(&harness.link).await.unwrap();

    let (_, checkpoints, _) = harness.journal.pending_control(harness.target_id).unwrap();
    assert!(
        checkpoints
            .iter()
            .any(|checkpoint| checkpoint.run_id == run_id && checkpoint.state.is_terminal()),
        "a refused final state must be offered again"
    );
    assert!(!harness.worker.refused_heartbeats.contains_key(&run_id));
}

/// Commands handed back on a control answer are taken, and acknowledged on
/// the next frame -- the same at-least-once path as a pushed command.
#[tokio::test]
async fn a_command_on_a_control_answer_is_taken_and_acknowledged() {
    let mut harness = Harness::new().await;
    let cancel = cancel_command(Uuid::new_v4());
    harness
        .stub
        .undelivered_commands
        .lock()
        .unwrap()
        .push_back(cancel.clone());

    harness.worker.send_control(&harness.link).await.unwrap();
    harness.worker.send_control(&harness.link).await.unwrap();

    assert!(
        harness
            .stub
            .acknowledged
            .lock()
            .unwrap()
            .contains(&cancel.command_id),
        "the command must be acknowledged, or Lemma hands it out again forever"
    );
}

/// A link that drops is a reason to reconnect, not to fail the worker, and
/// its reporting must fail loudly enough for the loop to notice.
#[tokio::test]
async fn a_dropped_link_fails_the_control_frame() {
    let mut harness = Harness::new().await;
    harness.link.close(1000, "test");
    within(Duration::from_secs(5), "the link to close", || {
        harness.link.is_closed()
    })
    .await;

    assert!(harness.worker.send_control(&harness.link).await.is_err());
}

/// A closed supervisor channel stops the worker without probing anything,
/// and without taking on work while it winds down.
#[tokio::test]
async fn a_closed_supervisor_channel_stops_the_worker_without_refreshing() {
    let mut harness = Harness::with_manifest(echo_manifest()).await;
    harness.worker.refresh_due = std::time::Instant::now() + Duration::from_secs(60);
    // This fixture's change sender has already gone away, as it does when the
    // supervisor errors or is cancelled. Closure is not an install.
    tokio::time::timeout(Duration::from_secs(5), harness.worker.link_loop())
        .await
        .expect("a closed channel must not spin the link loop")
        .unwrap();
    assert!(harness.worker.probe_task.is_none());
    assert!(harness.worker.draining);
}

/// A host Lemma does not know is not dropped on the first refusal -- that
/// could be a database restored behind its writes -- but is after a run of
/// them, which is a revocation.
#[tokio::test]
async fn a_pairing_lemma_keeps_refusing_is_dropped() {
    let mut harness = Harness::new().await;
    *harness.stub.refuse_hello_with.lock().unwrap() = None;
    let stub = Arc::clone(&harness.stub);
    // Refuse every hello from now on.
    let refuser = tokio::spawn(async move {
        loop {
            stub.refuse_hello_with
                .lock()
                .unwrap()
                .get_or_insert(crate::link::protocol::close::REVOKED_OR_MISSING);
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
    });
    let (_changes, agents_changed) = watch::channel(0_u64);
    harness.worker.agents_changed = agents_changed;
    let outcome = tokio::time::timeout(Duration::from_secs(20), harness.worker.link_loop()).await;
    refuser.abort();
    let error = outcome
        .expect("a revoked pairing must end the loop, not retry forever")
        .expect_err("a revoked pairing is an error");
    assert!(
        error
            .downcast_ref::<crate::link::LinkError>()
            .is_some_and(crate::link::LinkError::is_revoked_or_missing),
        "{error}"
    );
    assert_eq!(harness.worker.revoked_refusals, super::REVOKED_REFUSALS);
}

/// Lemma routes an owner's commands here on what `control` says, so turning
/// host execution on or off reaches it on the next frame -- no reconnect, no
/// waiting for a heartbeat that is up to twenty seconds away.
#[tokio::test]
async fn turning_host_execution_on_is_reported_on_the_next_control() {
    let mut harness = Harness::new().await;
    harness.worker.apply_local_controls().unwrap();
    harness.worker.send_control(&harness.link).await.unwrap();
    let off = harness.stub.host_execution_reports.lock().unwrap().clone();
    assert_eq!(off.last().unwrap()["enabled"], false);

    // The stand-in is on loopback over plain HTTP: the local pairing.
    let mut paired = harness.worker.target.clone();
    paired.host_execution = true;
    crate::config::HostConfig::mutate(&harness.worker.paths, |config| {
        config.targets.push(paired.clone());
        Ok(true)
    })
    .unwrap();
    harness.worker.apply_local_controls().unwrap();
    assert!(harness.worker.host_execution);
    #[cfg(unix)]
    assert!(
        harness
            .worker
            .exec_relay
            .as_ref()
            .is_some_and(|relay| relay.enabled()),
        "the relay must accept ops once the owner turns it on"
    );
    harness.worker.send_control(&harness.link).await.unwrap();
    let on = harness.stub.host_execution_reports.lock().unwrap().clone();
    let report = on.last().unwrap();
    assert_eq!(report["enabled"], true);
    assert_eq!(
        report["platform"],
        crate::host_exec::wire::platform(),
        "{report}"
    );
    assert_eq!(
        report["available"],
        cfg!(target_os = "macos") && std::path::Path::new("/usr/bin/sandbox-exec").exists()
    );
}

/// The switch is the local pairing's. A pairing with a server elsewhere that
/// claims it -- a config edited by hand, or written by an older build whose
/// switch was host-wide -- still runs nothing here.
#[tokio::test]
async fn a_pairing_with_a_server_elsewhere_never_turns_host_execution_on() {
    let mut harness = Harness::new().await;
    let mut remote = harness.worker.target.clone();
    remote.host_execution = true;
    remote.allow_insecure_http = false;
    remote.base_url = url::Url::parse("https://lemma.example/").unwrap();
    crate::config::HostConfig::mutate(&harness.worker.paths, |config| {
        config.targets.push(remote.clone());
        Ok(true)
    })
    .unwrap();
    harness.worker.apply_local_controls().unwrap();
    assert!(!harness.worker.host_execution);
    harness.worker.send_control(&harness.link).await.unwrap();
    let reports = harness.stub.host_execution_reports.lock().unwrap().clone();
    assert_eq!(reports.last().unwrap()["enabled"], false);
}

/// 4403 is a credential Lemma could not read this time, and the host keeps
/// trying with backoff; it used to give the pairing up for good at once.
#[tokio::test]
async fn a_refused_credential_is_retried_not_given_up() {
    let mut harness = Harness::new().await;
    *harness.stub.refuse_hello_with.lock().unwrap() =
        Some(crate::link::protocol::close::INVALID_CREDENTIAL);
    let (_changes, agents_changed) = watch::channel(0_u64);
    harness.worker.agents_changed = agents_changed;
    let stub = Arc::clone(&harness.stub);
    let target_id = harness.target_id;
    let journal = harness.journal.clone();
    let reconnected = async move {
        // The refusal has been used up, and the host is back.
        while stub.refuse_hello_with.lock().unwrap().is_some() || !stub.connected() {
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
        while journal
            .target_status(target_id)
            .ok()
            .and_then(|status| serde_json::to_value(status).ok())
            .is_none_or(|status| status["connection_state"] != "ONLINE")
        {
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
    };
    tokio::time::timeout(Duration::from_secs(10), async {
        tokio::select! {
            outcome = harness.worker.link_loop() => panic!("the loop ended: {outcome:?}"),
            () = reconnected => {}
        }
    })
    .await
    .expect("the host must come back after a 4403");
}

/// Two hosts holding one pairing take the link from each other. The one
/// superseded within moments waits longer each time; a link that lasted was
/// an ordinary hand-over and is taken back at once.
#[tokio::test]
async fn a_host_superseded_again_and_again_backs_off() {
    let mut harness = Harness::new().await;
    let superseded = crate::link::LinkError::Closed {
        code: crate::link::protocol::close::SUPERSEDED,
        reason: "superseded".into(),
    };
    let just_now = std::time::Instant::now();
    let waits: Vec<Duration> = (0..4)
        .map(|_| {
            harness
                .worker
                .superseded_wait(&superseded, just_now)
                .unwrap()
        })
        .collect();
    assert!(waits.windows(2).all(|pair| pair[1] > pair[0]), "{waits:?}");
    let long_ago = std::time::Instant::now()
        .checked_sub(Duration::from_secs(120))
        .unwrap();
    assert_eq!(harness.worker.superseded_wait(&superseded, long_ago), None);
    assert!(
        harness
            .worker
            .superseded_wait(&superseded, just_now)
            .unwrap()
            < waits[1]
    );
    let other = crate::link::LinkError::Closed {
        code: crate::link::protocol::close::RESTARTING,
        reason: String::new(),
    };
    assert_eq!(harness.worker.superseded_wait(&other, just_now), None);
}
