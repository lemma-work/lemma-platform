//! Recovering runs across a restart, and the session that rides them.

use super::*;
use serde_json::json;

/// What a restarted host reads to find the runs it left mid-flight, and
/// the only journal method that reads rows while its own cursor is open.
/// It had no direct test: the one path that reached it drove the whole
/// binary, so a fault here surfaced as a hung process in the slow lane
/// rather than as a failure here.
#[test]
fn a_restart_recovers_exactly_the_runs_that_had_not_finished() {
    let (_directory, journal, target, command, spec) = fixture();
    journal
        .accept_start(target, &command, &spec, "codex", "1.0")
        .unwrap();

    let recovered = journal.recoverable_runs(target).unwrap();
    assert_eq!(recovered.len(), 1, "an accepted run is still in flight");
    assert_eq!(recovered[0].run_id, spec.agent_run_id);
    assert_eq!(recovered[0].harness_key, "codex");

    journal
        .checkpoint(
            target,
            spec.agent_run_id,
            1,
            RunState::Succeeded,
            &JsonMap::new(),
        )
        .unwrap();

    assert!(
        journal.recoverable_runs(target).unwrap().is_empty(),
        "a finished run must not be re-driven on the next start"
    );
}

#[test]
fn duplicate_command_is_idempotent() {
    let (_directory, journal, target, command, spec) = fixture();
    assert_eq!(
        journal
            .accept_start(target, &command, &spec, "codex", "1.0")
            .unwrap(),
        AcceptOutcome::New
    );
    assert_eq!(
        journal
            .accept_start(target, &command, &spec, "codex", "1.0")
            .unwrap(),
        AcceptOutcome::Duplicate
    );
}

#[test]
fn dispatch_intent_survives_reopen_and_prevents_blind_retry() {
    let (directory, journal, target, command, spec) = fixture();
    journal
        .accept_start(target, &command, &spec, "codex", "1.0")
        .unwrap();
    journal
        .mark_dispatch_intent(target, spec.agent_run_id, 1, "session-1")
        .unwrap();
    drop(journal);
    let reopened = Journal::open(directory.path().join("journal.db")).unwrap();
    let run = reopened
        .get_run(target, spec.agent_run_id)
        .unwrap()
        .unwrap();
    assert!(run.prompt_dispatched);
    assert_eq!(run.checkpoint, Checkpoint::DispatchIntent);
    assert!(
        reopened
            .mark_dispatch_intent(target, spec.agent_run_id, 1, "session-2")
            .is_err()
    );
}

#[test]
fn command_ack_is_cleared_but_active_checkpoint_remains_a_heartbeat() {
    let (_directory, journal, target, command, spec) = fixture();
    journal
        .accept_start(target, &command, &spec, "codex", "1.0")
        .unwrap();
    let (commands, checkpoints, rejections) = journal.pending_control(target).unwrap();
    assert_eq!(commands, vec![command.command_id]);
    assert_eq!(checkpoints.len(), 1);
    journal
        .mark_control_applied(target, &commands, &checkpoints, &rejections)
        .unwrap();
    let (commands, checkpoints, rejections) = journal.pending_control(target).unwrap();
    assert!(commands.is_empty());
    assert!(rejections.is_empty());
    assert_eq!(checkpoints.len(), 1);
    assert_eq!(checkpoints[0].state, RunState::Accepted);

    journal
        .checkpoint(
            target,
            spec.agent_run_id,
            1,
            RunState::Succeeded,
            &JsonMap::new(),
        )
        .unwrap();
    let (_, terminal, rejections) = journal.pending_control(target).unwrap();
    journal
        .mark_control_applied(target, &[], &terminal, &rejections)
        .unwrap();
    assert_eq!(
        journal.pending_control(target).unwrap(),
        (vec![], vec![], vec![])
    );
}

#[test]
fn the_provider_session_rides_every_checkpoint_not_just_the_one_that_opened_it() {
    // A run has one pending-checkpoint slot, so the RUNNING that follows the
    // first streamed token overwrites the detail written a moment earlier -
    // and the first token lands well inside a single poll. Putting the id on
    // the checkpoint that happened to be current lost it every time.
    let (_directory, journal, target, command, spec) = fixture();
    journal
        .accept_start(target, &command, &spec, "codex", "1.0")
        .unwrap();
    journal
        .mark_dispatch_intent(target, spec.agent_run_id, 1, "rollout-42")
        .unwrap();
    journal
        .checkpoint(
            target,
            spec.agent_run_id,
            1,
            RunState::Running,
            &JsonMap::new(),
        )
        .unwrap();

    let (_, checkpoints, _) = journal.pending_control(target).unwrap();
    assert_eq!(checkpoints.len(), 1);
    assert_eq!(checkpoints[0].state, RunState::Running);
    assert_eq!(
        checkpoints[0].detail.get("provider_session_id"),
        Some(&serde_json::Value::String("rollout-42".to_owned())),
        "the RUNNING checkpoint dropped the session the conversation needs"
    );
}

#[test]
fn a_run_that_never_opened_a_session_reports_no_session_id() {
    let (_directory, journal, target, command, spec) = fixture();
    journal
        .accept_start(target, &command, &spec, "codex", "1.0")
        .unwrap();

    let (_, checkpoints, _) = journal.pending_control(target).unwrap();
    assert_eq!(checkpoints.len(), 1);
    assert!(!checkpoints[0].detail.contains_key("provider_session_id"));
}

/// A run that finished while the refresh was in flight keeps its terminal spec.
///
/// `refresh_run_mcp` read the run, released the journal lock, then wrote --
/// so a `checkpoint` marking the run terminal in between was overwritten by a
/// spec belonging to a run that had already ended. Both halves are one
/// immediate transaction now.
#[test]
fn a_terminal_run_is_not_given_a_new_credential() {
    let (_directory, journal, target, command, spec) = fixture();
    journal
        .accept_start(target, &command, &spec, "codex", "1.0")
        .unwrap();
    let run_id = spec.agent_run_id;

    // Live: the refresh lands.
    assert!(
        journal
            .refresh_run_mcp(
                target,
                run_id,
                1,
                &json!({"url": "https://one.example/mcp"})
            )
            .unwrap(),
        "a live run takes a replacement credential"
    );

    journal
        .checkpoint(target, run_id, 1, RunState::Succeeded, &JsonMap::new())
        .unwrap();

    // Terminal: it does not.
    assert!(
        !journal
            .refresh_run_mcp(
                target,
                run_id,
                1,
                &json!({"url": "https://two.example/mcp"})
            )
            .unwrap(),
        "a finished run must not be given a new credential"
    );
    let stored = journal.get_run(target, run_id).unwrap().unwrap();
    assert_eq!(stored.spec.mcp, json!({"url": "https://one.example/mcp"}));

    // And a superseded lease does not, whatever the state.
    assert!(
        !journal
            .refresh_run_mcp(
                target,
                run_id,
                2,
                &json!({"url": "https://three.example/mcp"})
            )
            .unwrap(),
    );
}
