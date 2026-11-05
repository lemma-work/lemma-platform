//! A run's working directory, and the credential it is given.

use super::*;

/// Every turn of a conversation must run in the same working directory.
///
/// `session/load` takes a cwd, and a provider is entitled to refuse to load
/// a session into a different one — Claude Code does. So a directory keyed
/// on anything but the conversation makes every resume fail: each turn
/// silently opens a new session and the agent meets the user again, with no
/// error anywhere to say why. Nothing else in the system notices.
#[test]
fn a_conversation_always_gets_the_same_working_directory() {
    let paths = HostPaths::under(std::path::Path::new("/tmp/agent-host-test"));
    let target = Uuid::new_v4();
    let conversation = Uuid::new_v4();

    assert_eq!(
        super::scratch_directory(&paths, target, conversation),
        super::scratch_directory(&paths, target, conversation),
        "two turns of one conversation must share a cwd, or every \
         session/load is refused"
    );
    assert_ne!(
        super::scratch_directory(&paths, target, conversation),
        super::scratch_directory(&paths, target, Uuid::new_v4()),
        "two conversations must not share a workspace"
    );
}

/// The Lemma credential a run is dispatched with expires in an hour, and
/// the bridge is a separate process that reads its endpoint from the
/// journal. Writing the replacement there *is* the delivery.
#[tokio::test]
async fn a_refreshed_credential_reaches_the_runs_journal() {
    let mut harness = Harness::new().await;
    let run_id = harness.seed_run(0);
    let refreshed = serde_json::json!({
        "url": "https://lemma.example/mcp",
        "authorization": "Bearer refreshed",
    });

    harness
        .worker
        .handle_command(&Command {
            command_id: Uuid::new_v4(),
            kind: CommandKind::RefreshCredential,
            created_at: Utc::now(),
            expires_at: Utc::now() + chrono::Duration::minutes(1),
            run_id: Some(run_id),
            lease_epoch: Some(1),
            payload: serde_json::json!({"mcp": refreshed.clone()}),
        })
        .unwrap();

    let run = harness
        .journal
        .get_run(harness.target_id, run_id)
        .unwrap()
        .unwrap();
    assert_eq!(run.spec.mcp, refreshed);
}

/// Fenced like everything else a host is told about a run: a credential
/// minted for a dispatch that has been superseded must not land on the one
/// executing now.
#[tokio::test]
async fn a_refresh_for_a_superseded_lease_is_ignored() {
    let mut harness = Harness::new().await;
    let run_id = harness.seed_run(0);
    let before = harness
        .journal
        .get_run(harness.target_id, run_id)
        .unwrap()
        .unwrap()
        .spec
        .mcp;

    harness
        .worker
        .handle_command(&Command {
            command_id: Uuid::new_v4(),
            kind: CommandKind::RefreshCredential,
            created_at: Utc::now(),
            expires_at: Utc::now() + chrono::Duration::minutes(1),
            run_id: Some(run_id),
            lease_epoch: Some(9),
            payload: serde_json::json!({
                "mcp": {"url": "https://elsewhere.example/mcp", "token": "x"}
            }),
        })
        .unwrap();

    assert_eq!(
        harness
            .journal
            .get_run(harness.target_id, run_id)
            .unwrap()
            .unwrap()
            .spec
            .mcp,
        before
    );
}

fn steer_command(run_id: Uuid, lease_epoch: u32) -> Command {
    Command {
        command_id: Uuid::new_v4(),
        kind: CommandKind::SteerRun,
        created_at: Utc::now(),
        expires_at: Utc::now() + chrono::Duration::minutes(1),
        run_id: Some(run_id),
        lease_epoch: Some(lease_epoch),
        payload: serde_json::json!({
            "message_id": "message-1",
            "prompt": [{"type": "text", "text": "Also check the tests."}],
        }),
    }
}

/// A `STEER_RUN` is handed to the running turn, which is what sends it.
#[tokio::test]
async fn a_steer_reaches_the_turn_it_was_sent_to() {
    let mut harness = Harness::new().await;
    let run_id = harness.seed_run(0);
    let inbox = harness
        .worker
        .track_steerable_run(run_id, tokio::spawn(std::future::pending()));

    harness
        .worker
        .handle_command(&steer_command(run_id, 1))
        .unwrap();

    let steer = inbox.take().unwrap().try_recv().unwrap();
    assert_eq!(steer.message_id, "message-1");
}

/// A steer for a dispatch that has been superseded is not for this turn, and
/// one for a run that already ended has no turn to join. Neither is an error:
/// Lemma's follow-up turn carries the message.
#[tokio::test]
async fn a_steer_with_no_turn_to_join_is_dropped_quietly() {
    let mut harness = Harness::new().await;
    let run_id = harness.seed_run(0);
    let inbox = harness
        .worker
        .track_steerable_run(run_id, tokio::spawn(std::future::pending()));

    harness
        .worker
        .handle_command(&steer_command(run_id, 9))
        .unwrap();
    harness
        .worker
        .handle_command(&steer_command(Uuid::new_v4(), 1))
        .unwrap();

    assert!(inbox.take().unwrap().try_recv().is_err());
}

/// Every terminal path in `spawn_run` wakes the link that reports it.
///
/// `events_ready.notify_one()` wakes event delivery and the link loop's
/// `control` frame. A terminal path that skips it leaves its checkpoint in the
/// journal until the next heartbeat, so the run it ended is reported up to a
/// heartbeat interval late.
///
/// Asserted on the source: the property is "every one of them", and reaching
/// each from a test needs a different half-broken start command.
#[test]
fn every_terminal_path_wakes_the_link_that_reports_it() {
    let source = include_str!("../../run.rs").replace("\r\n", "\n");
    let mut silent = Vec::new();
    for (offset, _) in source.match_indices("terminal_failure(") {
        // The call, then whatever follows it up to the `return`.
        let rest = &source[offset..];
        let end = rest.find("return Ok(())").unwrap_or(rest.len());
        if !rest[..end].contains("events_ready.notify_one()") {
            let line = source[..offset].lines().count() + 1;
            silent.push(line);
        }
    }
    assert!(
        silent.is_empty(),
        "these terminal paths return without waking the link, so the run they \
         ended is reported up to a heartbeat late: run.rs lines {silent:?}",
    );
    assert!(
        source.matches("terminal_failure(").count() >= 3,
        "the scan found no terminal paths, so it is asserting nothing"
    );
}

/// A host on its way out refuses starts, and says so in a way Lemma may act
/// on: this run belongs somewhere else, so re-mint it rather than failing it.
#[tokio::test]
async fn a_start_refused_because_the_host_is_draining_is_retryable() {
    let mut harness = Harness::new().await;
    harness.worker.draining = true;
    let command = start_command(Uuid::new_v4(), Utc::now() + chrono::Duration::minutes(1));

    let error = harness.worker.handle_command(&command).unwrap_err();
    let rejection = command_rejection(&command, &error).expect("a refused start is reported");

    assert_eq!(rejection.code, RejectionCode::Draining);
    assert!(rejection.retryable);
}

/// A command that sat in a queue past its own deadline is not retried: the
/// deadline is Lemma's, and Lemma is the one that decides whether to mint
/// another.
#[tokio::test]
async fn a_start_that_arrived_too_late_is_not_retryable() {
    let mut harness = Harness::new().await;
    let command = start_command(Uuid::new_v4(), Utc::now() - chrono::Duration::seconds(1));

    let error = harness.worker.handle_command(&command).unwrap_err();
    let rejection = command_rejection(&command, &error).expect("a refused start is reported");

    assert_eq!(rejection.code, RejectionCode::CommandExpired);
    assert!(!rejection.retryable);
}

/// Minted against a harness this computer does not publish. Retrying cannot
/// help until the harness exists here, so the run fails rather than looping.
#[tokio::test]
async fn a_start_for_a_harness_this_host_never_published_is_not_retryable() {
    let mut harness = Harness::new().await;
    let command = start_command(Uuid::new_v4(), Utc::now() + chrono::Duration::minutes(1));

    let error = harness.worker.handle_command(&command).unwrap_err();
    let rejection = command_rejection(&command, &error).expect("a refused start is reported");

    assert_eq!(rejection.code, RejectionCode::HarnessNotFound);
    assert!(!rejection.retryable);
}

/// A start command for `harness_id`, due at `expires_at`.
fn start_command(harness_id: Uuid, expires_at: chrono::DateTime<Utc>) -> Command {
    let run_id = Uuid::new_v4();
    let spec = RunSpec {
        agent_run_id: run_id,
        conversation_id: Uuid::new_v4(),
        harness_id,
        profile_revision: "revision".into(),
        model_name: None,
        config_selections: JsonMap::new(),
        system_prompt: String::new(),
        prompt: vec![serde_json::json!({"type": "text", "text": "hi"})],
        resume_session_id: None,
        workspace_cwd: None,
        context: JsonMap::new(),
        mcp: serde_json::json!({}),
        run_deadline: Utc::now() + chrono::Duration::minutes(5),
        system_prompt_delivery: None,
        pre_approvals: Vec::new(),
    };
    Command {
        command_id: Uuid::new_v4(),
        kind: CommandKind::StartRun,
        created_at: Utc::now(),
        expires_at,
        run_id: Some(run_id),
        lease_epoch: Some(1),
        payload: serde_json::to_value(&spec).unwrap(),
    }
}

/// Command expiries are Lemma's times. A host whose clock runs ahead judged a
/// fresh command expired -- every command, a cancel included -- until the
/// clock was fixed; it judges by the time Lemma's `welcome` gave instead.
#[tokio::test]
async fn a_command_is_judged_by_lemmas_clock_not_this_ones() {
    let mut harness = Harness::new().await;
    let run_id = harness.seed_run(0);
    // This clock is ten minutes ahead of Lemma's.
    harness
        .worker
        .note_lemma_time(Some(Utc::now() - chrono::Duration::minutes(10)));
    let refreshed = serde_json::json!({"token": "refreshed"});
    harness
        .worker
        .handle_command(&Command {
            command_id: Uuid::new_v4(),
            kind: CommandKind::RefreshCredential,
            created_at: Utc::now() - chrono::Duration::minutes(10),
            // Lemma's "in one minute", which this clock reads as nine ago.
            expires_at: Utc::now() - chrono::Duration::minutes(9),
            run_id: Some(run_id),
            lease_epoch: Some(1),
            payload: serde_json::json!({"mcp": refreshed.clone()}),
        })
        .expect("a command Lemma has not expired is not refused as expired");
    assert_eq!(
        harness
            .journal
            .get_run(harness.target_id, run_id)
            .unwrap()
            .unwrap()
            .spec
            .mcp,
        refreshed
    );
}

/// Stopping late is still stopping: a cancel is never refused as expired.
#[tokio::test]
async fn a_late_cancel_is_still_obeyed() {
    let mut harness = Harness::new().await;
    let run_id = harness.seed_run(0);
    let mut cancel = super::cancel_command(run_id);
    cancel.expires_at = Utc::now() - chrono::Duration::minutes(30);
    harness.worker.handle_command(&cancel).unwrap();
    let run = harness
        .journal
        .get_run(harness.target_id, run_id)
        .unwrap()
        .unwrap();
    assert!(run.state.is_terminal(), "{:?}", run.state);
}

/// Wait for a tracked run's task to end, then reap it.
async fn reap_when_finished(harness: &mut Harness, run_id: Uuid) {
    within(Duration::from_secs(5), "the run task to end", || {
        harness.worker.active_runs[&run_id].handle.is_finished()
    })
    .await;
    harness.worker.reap_finished_now();
}

fn journalled_state(harness: &Harness, run_id: Uuid) -> RunState {
    harness
        .journal
        .get_run(harness.target_id, run_id)
        .unwrap()
        .unwrap()
        .state
}

/// A run task that stops on an error of its own -- a journal write it could
/// not make -- has written no final state. The run used to stay ACCEPTED in
/// the journal and heartbeat for ever: its lease never lapsed, so the turn in
/// Lemma never ended.
#[tokio::test]
async fn a_run_task_that_errors_ends_its_run_and_tells_lemma() {
    let mut harness = Harness::new().await;
    let run_id = harness.seed_run(0);
    harness.worker.track_run(
        run_id,
        tokio::spawn(async { Err(anyhow::anyhow!("database is locked")) }),
    );

    reap_when_finished(&mut harness, run_id).await;

    assert_eq!(journalled_state(&harness, run_id), RunState::Failed);
    let terminal = harness
        .journal
        .pending_events(harness.target_id, usize::MAX)
        .unwrap()
        .into_iter()
        .flat_map(|batch| batch.events)
        .find(|event| event.run_id == run_id && event.event_type == EventType::Terminal)
        .expect("Lemma hears the run end through the ordinary event path");
    assert_eq!(terminal.payload["state"], "FAILED");
    let batch = harness.worker.control_batch().unwrap();
    assert!(
        batch
            .checkpoints
            .iter()
            .filter(|checkpoint| checkpoint.run_id == run_id)
            .all(|checkpoint| checkpoint.state.is_terminal()),
        "no liveness checkpoint may keep the lease alive: {:?}",
        batch.checkpoints
    );
    assert!(harness.worker.orphaned_runs.is_empty());
}

/// Once the prompt may have reached the agent, the turn may have acted, and
/// Lemma must not repeat it: the run ends `DISPATCH_UNKNOWN`, as a restart
/// that found it would have ended it.
#[tokio::test]
async fn a_run_task_that_panics_after_dispatch_ends_dispatch_unknown() {
    let mut harness = Harness::new().await;
    let run_id = harness.seed_run(0);
    harness
        .journal
        .mark_dispatch_intent(harness.target_id, run_id, 1, "provider-session")
        .unwrap();
    harness.worker.track_run(
        run_id,
        tokio::spawn(async { panic!("a run task that panics after dispatch") }),
    );

    reap_when_finished(&mut harness, run_id).await;

    assert_eq!(
        journalled_state(&harness, run_id),
        RunState::DispatchUnknown
    );
}

/// A run that ended itself keeps the state it chose.
#[tokio::test]
async fn reaping_leaves_a_finished_runs_own_state_alone() {
    let mut harness = Harness::new().await;
    let run_id = harness.seed_run(0);
    let journal = harness.journal.clone();
    let target_id = harness.target_id;
    harness.worker.track_run(
        run_id,
        tokio::spawn(async move {
            terminal_failure(
                &journal,
                target_id,
                run_id,
                1,
                RunState::Cancelled,
                "stopped",
            )
        }),
    );

    reap_when_finished(&mut harness, run_id).await;

    assert_eq!(journalled_state(&harness, run_id), RunState::Cancelled);
}

/// A `config.json` caught half-written is not a reason to end the worker:
/// that aborted every run on the pairing. The settings last read stay in
/// force and the runs keep going.
#[tokio::test]
async fn an_unreadable_config_keeps_the_last_settings_and_the_runs() {
    let mut harness = Harness::new().await;
    let mut draining = harness.worker.target.clone();
    draining.draining = true;
    crate::config::HostConfig::mutate(&harness.worker.paths, |config| {
        config.targets.push(draining.clone());
        Ok(true)
    })
    .unwrap();
    harness.worker.apply_local_controls().unwrap();
    assert!(harness.worker.draining);
    let run_id = harness.seed_run(0);
    harness.worker.track_run(
        run_id,
        tokio::spawn(async {
            std::future::pending::<()>().await;
            Ok(())
        }),
    );

    std::fs::write(&harness.worker.paths.config, b"{ half written").unwrap();
    harness.worker.controls_due = std::time::Instant::now();
    harness.worker.housekeeping();

    assert!(harness.worker.local_controls_unreadable);
    assert!(
        harness.worker.draining,
        "the last good settings stay in force"
    );
    assert!(
        !harness.worker.active_runs[&run_id].handle.is_finished(),
        "the run on this pairing is untouched"
    );
    assert!(!journalled_state(&harness, run_id).is_terminal());
}

/// The driver gets what is left of the run when it starts, not what was left
/// before the CLI was chosen; a run whose deadline went by meanwhile never
/// starts its agent.
#[test]
fn a_run_gets_only_the_time_left_when_its_agent_starts() {
    use crate::runtime::run::time_left;
    let now = Utc::now();
    assert_eq!(
        time_left(now + chrono::Duration::seconds(30), now),
        Some(Duration::from_secs(30))
    );
    assert_eq!(time_left(now, now), None);
    assert_eq!(time_left(now - chrono::Duration::seconds(1), now), None);

    // Asserted on the source, as the terminal-path test above is: reaching
    // the order from a test needs a CLI download that outlives a deadline.
    let source = include_str!("../../run.rs").replace("\r\n", "\n");
    let chosen = source
        .find("cli_bin_for_run(")
        .expect("the run chooses its CLI");
    let measured = source
        .find("time_left(spec.run_deadline")
        .expect("the run measures its time");
    let started = source
        .find("tokio::time::timeout(remaining")
        .expect("the run starts its agent");
    assert!(
        chosen < measured && measured < started,
        "the time left is measured after the CLI is chosen and before the agent starts"
    );
}
