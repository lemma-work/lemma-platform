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
