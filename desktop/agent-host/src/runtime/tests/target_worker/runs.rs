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

/// Every terminal path in `spawn_run` wakes the poll that reports it.
///
/// `poll_target` snapshots the control batch when it builds the request, so a
/// checkpoint written a moment later waits out the whole 25-second long poll.
/// Two of the three terminal paths notified; the third did not, and a run that
/// failed for want of an MCP configuration sat unreported for up to that long.
///
/// Asserted on the source: the property is "every one of them", and reaching
/// each from a test needs a different half-broken start command.
#[test]
fn every_terminal_path_wakes_the_poll_that_reports_it() {
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
        "these terminal paths return without waking the poll, so the run they \
         ended is reported up to a long poll late: run.rs lines {silent:?}",
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
