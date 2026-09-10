//! The journal's guards, grouped the way the code they cover is grouped.

mod concurrency;
mod events;
mod runs;
mod schema;

use chrono::Duration as ChronoDuration;
use tempfile::TempDir;

use super::*;
use crate::protocol::CommandKind;

pub(super) fn fixture() -> (TempDir, Journal, Uuid, Command, RunSpec) {
    let directory = TempDir::new().unwrap();
    let journal = Journal::open(directory.path().join("journal.db")).unwrap();
    let target_id = Uuid::new_v4();
    let run_id = Uuid::new_v4();
    let spec = RunSpec {
        agent_run_id: run_id,
        conversation_id: Uuid::new_v4(),
        harness_id: Uuid::new_v4(),
        profile_revision: "revision".into(),
        model_name: None,
        config_selections: JsonMap::new(),
        system_prompt: "system".into(),
        prompt: vec![serde_json::json!({"role": "user", "content": "hello"})],
        resume_session_id: None,
        workspace_cwd: None,
        context: JsonMap::new(),
        mcp: serde_json::json!({
            "url": "https://lemma.test/mcp",
            "authorization": "Bearer test"
        }),
        run_deadline: Utc::now() + ChronoDuration::minutes(5),
        system_prompt_delivery: None,
    };
    let payload = serde_json::to_value(&spec).unwrap();
    let command = Command {
        command_id: Uuid::new_v4(),
        kind: CommandKind::StartRun,
        created_at: Utc::now(),
        expires_at: Utc::now() + ChronoDuration::minutes(1),
        run_id: Some(run_id),
        lease_epoch: Some(1),
        payload,
    };
    (directory, journal, target_id, command, spec)
}

pub(super) fn stored_events(journal: &Journal, target: Uuid) -> i64 {
    journal
        .connection()
        .query_row(
            "SELECT COUNT(*) FROM event_outbox WHERE target_id=?1",
            params![target.to_string()],
            |row| row.get(0),
        )
        .unwrap()
}

pub(super) fn ack_through(journal: &Journal, target: Uuid, run_id: Uuid, sequence: u64) {
    journal
        .acknowledge_events(
            target,
            &EventAck {
                run_id,
                lease_epoch: 1,
                acked_through: sequence,
            },
        )
        .unwrap();
}

pub(super) fn append_three(journal: &Journal, target: Uuid, run_id: Uuid) {
    for index in 0..3 {
        let mut payload = JsonMap::new();
        payload.insert("index".into(), serde_json::Value::from(index));
        journal
            .append_event(
                target,
                run_id,
                1,
                EventType::AgentMessageChunk,
                None,
                payload,
            )
            .unwrap();
    }
}
