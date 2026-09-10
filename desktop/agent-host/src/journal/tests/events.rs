//! The outbox: contiguous, replayable, and retained until it is safe.

use super::*;

#[test]
fn event_outbox_is_contiguous_and_replayable() {
    let (_directory, journal, target, command, spec) = fixture();
    journal
        .accept_start(target, &command, &spec, "codex", "1.0")
        .unwrap();
    for index in 0..3 {
        let mut payload = JsonMap::new();
        payload.insert("index".into(), serde_json::Value::from(index));
        journal
            .append_event(
                target,
                spec.agent_run_id,
                1,
                EventType::AgentMessageChunk,
                None,
                payload,
            )
            .unwrap();
    }
    let batches = journal.pending_events(target, 256).unwrap();
    assert_eq!(batches.len(), 1);
    assert_eq!(
        batches[0]
            .events
            .iter()
            .map(|event| event.sequence)
            .collect::<Vec<_>>(),
        vec![1, 2, 3]
    );
    journal
        .acknowledge_events(
            target,
            &EventAck {
                run_id: spec.agent_run_id,
                lease_epoch: 1,
                acked_through: 2,
            },
        )
        .unwrap();
    assert_eq!(
        journal.pending_events(target, 256).unwrap()[0].events[0].sequence,
        3
    );
}

/// The server's event stream is transient and unbacked by persistence, so
/// a live run has to stay replayable from here after the stream is lost.
#[test]
fn acknowledged_events_are_retained_until_the_run_terminalizes() {
    let (_directory, journal, target, command, spec) = fixture();
    journal
        .accept_start(target, &command, &spec, "codex", "1.0")
        .unwrap();
    append_three(&journal, target, spec.agent_run_id);

    ack_through(&journal, target, spec.agent_run_id, 3);
    assert!(journal.pending_events(target, 256).unwrap().is_empty());
    assert_eq!(
        stored_events(&journal, target),
        3,
        "an active run must keep a replayable copy of its acknowledged events"
    );

    // Once the run is terminal the server refuses its events outright, so
    // the retained copy has nothing left to answer.
    journal
        .checkpoint(
            target,
            spec.agent_run_id,
            1,
            RunState::Succeeded,
            &JsonMap::new(),
        )
        .unwrap();
    ack_through(&journal, target, spec.agent_run_id, 3);
    assert_eq!(stored_events(&journal, target), 0);
}

#[test]
fn rewinding_acknowledgements_replays_a_run_from_its_first_event() {
    let (_directory, journal, target, command, spec) = fixture();
    journal
        .accept_start(target, &command, &spec, "codex", "1.0")
        .unwrap();
    append_three(&journal, target, spec.agent_run_id);
    ack_through(&journal, target, spec.agent_run_id, 3);
    assert!(journal.pending_events(target, 256).unwrap().is_empty());

    let replayed = journal
        .rewind_acknowledgements(target, spec.agent_run_id, 1)
        .unwrap();

    assert_eq!(replayed, 3);
    let batches = journal.pending_events(target, 256).unwrap();
    assert_eq!(
        batches[0]
            .events
            .iter()
            .map(|event| event.sequence)
            .collect::<Vec<_>>(),
        vec![1, 2, 3],
        "a resend has to start at sequence 1, which is what an emptied \
         server stream expects"
    );
}

#[test]
fn discarding_events_unblocks_a_run_the_server_keeps_refusing() {
    let (_directory, journal, target, command, spec) = fixture();
    journal
        .accept_start(target, &command, &spec, "codex", "1.0")
        .unwrap();
    append_three(&journal, target, spec.agent_run_id);
    journal
        .checkpoint(
            target,
            spec.agent_run_id,
            1,
            RunState::Failed,
            &JsonMap::new(),
        )
        .unwrap();
    // Undeliverable events would otherwise hold back the terminal
    // checkpoint, which is the only way Lemma learns the run ended.
    assert!(journal.pending_control(target).unwrap().1.is_empty());

    assert_eq!(
        journal
            .discard_events(target, spec.agent_run_id, 1)
            .unwrap(),
        3
    );

    assert!(journal.pending_events(target, 256).unwrap().is_empty());
    let (_, checkpoints, _) = journal.pending_control(target).unwrap();
    assert_eq!(checkpoints.len(), 1);
    assert_eq!(checkpoints[0].state, RunState::Failed);
}

#[test]
fn acknowledging_an_unknown_run_is_rejected() {
    let (_directory, journal, target, command, spec) = fixture();
    journal
        .accept_start(target, &command, &spec, "codex", "1.0")
        .unwrap();
    assert!(matches!(
        journal.acknowledge_events(
            target,
            &EventAck {
                run_id: Uuid::new_v4(),
                lease_epoch: 1,
                acked_through: 1,
            },
        ),
        Err(JournalError::AckMismatch)
    ));
}

#[test]
fn terminal_checkpoint_waits_for_terminal_event_acknowledgement() {
    let (_directory, journal, target, command, spec) = fixture();
    journal
        .accept_start(target, &command, &spec, "codex", "1.0")
        .unwrap();
    let event = journal
        .append_event(
            target,
            spec.agent_run_id,
            1,
            EventType::Terminal,
            None,
            JsonMap::new(),
        )
        .unwrap();
    journal
        .checkpoint(
            target,
            spec.agent_run_id,
            1,
            RunState::Succeeded,
            &JsonMap::new(),
        )
        .unwrap();

    let (_, checkpoints, _) = journal.pending_control(target).unwrap();
    assert!(
        checkpoints.is_empty(),
        "terminal checkpoint must not race ahead of its durable terminal event"
    );

    journal
        .acknowledge_events(
            target,
            &EventAck {
                run_id: spec.agent_run_id,
                lease_epoch: 1,
                acked_through: event.sequence,
            },
        )
        .unwrap();
    let (_, checkpoints, _) = journal.pending_control(target).unwrap();
    assert_eq!(checkpoints.len(), 1);
    assert_eq!(checkpoints[0].state, RunState::Succeeded);
}

#[test]
fn retention_removes_only_old_terminal_runs() {
    let (_directory, journal, target, command, spec) = fixture();
    journal
        .accept_start(target, &command, &spec, "codex", "1.0")
        .unwrap();
    journal
        .checkpoint(
            target,
            spec.agent_run_id,
            1,
            RunState::Succeeded,
            &JsonMap::new(),
        )
        .unwrap();
    journal
        .connection()
        .execute(
            "UPDATE runs SET updated_at=?1 WHERE target_id=?2 AND run_id=?3",
            params![
                (Utc::now() - ChronoDuration::days(31)).to_rfc3339(),
                target.to_string(),
                spec.agent_run_id.to_string()
            ],
        )
        .unwrap();
    journal
        .connection()
        .execute(
            "UPDATE command_receipts SET updated_at=?1 WHERE target_id=?2 AND command_id=?3",
            params![
                (Utc::now() - ChronoDuration::days(31)).to_rfc3339(),
                target.to_string(),
                command.command_id.to_string()
            ],
        )
        .unwrap();
    assert!(journal.cleanup_retained(Utc::now()).unwrap() >= 2);
    assert!(
        journal
            .get_run(target, spec.agent_run_id)
            .unwrap()
            .is_none()
    );

    let (_active_directory, active_journal, active_target, active_command, active_spec) = fixture();
    active_journal
        .accept_start(active_target, &active_command, &active_spec, "codex", "1.0")
        .unwrap();
    active_journal
        .connection()
        .execute(
            "UPDATE runs SET updated_at=?1 WHERE target_id=?2 AND run_id=?3",
            params![
                (Utc::now() - ChronoDuration::days(31)).to_rfc3339(),
                active_target.to_string(),
                active_spec.agent_run_id.to_string()
            ],
        )
        .unwrap();
    active_journal.cleanup_retained(Utc::now()).unwrap();
    assert!(
        active_journal
            .get_run(active_target, active_spec.agent_run_id)
            .unwrap()
            .is_some()
    );
}
