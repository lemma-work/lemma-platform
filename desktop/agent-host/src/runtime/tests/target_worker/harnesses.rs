//! Publishing what this computer has, and judging a start against it.

use super::*;

#[tokio::test]
async fn repeated_refreshes_share_one_probe_and_worker_drop_cancels_its_tasks() {
    let mut harness = Harness::with_manifest(echo_manifest()).await;
    let (probe_owner, probe_dropped) = tokio::sync::oneshot::channel::<()>();
    let probe = tokio::spawn(async move {
        let _owner = probe_owner;
        std::future::pending::<()>().await;
    });
    let probe_id = probe.id();
    harness.worker.probe_task = Some(super::OwnedTask(probe));
    let (run_owner, run_dropped) = tokio::sync::oneshot::channel::<()>();
    harness.worker.track_run(
        Uuid::new_v4(),
        tokio::spawn(async move {
            let _owner = run_owner;
            std::future::pending::<anyhow::Result<()>>().await
        }),
    );
    for _ in 0..20 {
        harness.worker.refresh_harnesses();
    }
    assert_eq!(harness.worker.probe_task.as_ref().unwrap().0.id(), probe_id);
    assert!(
        harness
            .worker
            .reprobe_requested
            .load(std::sync::atomic::Ordering::SeqCst)
    );
    drop(harness);
    for dropped in [probe_dropped, run_dropped] {
        assert!(
            tokio::time::timeout(Duration::from_secs(2), dropped)
                .await
                .unwrap()
                .is_err()
        );
    }
}

/// An adapter that resolves without anything being installed, so a test
/// can reach the code past `handle_start`'s harness fence.
///
/// `native` is the one distribution the manifest lets go unpinned, and
/// `echo` exists on every machine this suite runs on.
pub(super) fn echo_manifest() -> AdapterManifest {
    serde_json::from_value(serde_json::json!({
        "manifest_version": 1,
        "manifest_id": "test-manifest",
        "protocol": "ACP",
        "adapters": [{
            "key": "claude-code",
            "display_name": "Claude Code",
            "adapter_version": "0.0.0-test",
            "command": "echo",
            "args": [],
            "upstream_command": "echo",
            "upstream_version_args": ["--version"],
            "minimum_upstream_version": null,
            "distribution": "native",
            "artifact_integrity": null,
            "license": "Apache-2.0"
        }]
    }))
    .unwrap()
}

/// Two revisions that stay distinct through `short_revision`, which keeps
/// only the first eight characters — as the log lines and the rejection
/// detail both do.
const REVISION_A: &str = "a1a1a1a1cafef00d";
const REVISION_B: &str = "b2b2b2b2cafef00d";

fn published_harness(id: Uuid, revision: &str) -> PublishedHarness {
    PublishedHarness {
        id,
        harness_key: "claude-code".into(),
        adapter_version: "0.0.0-test".into(),
        config_revision: revision.into(),
    }
}

fn start_command(harness_id: Uuid, run_id: Uuid, revision: &str) -> Command {
    let spec = RunSpec {
        agent_run_id: run_id,
        conversation_id: Uuid::new_v4(),
        harness_id,
        profile_revision: revision.into(),
        model_name: None,
        config_selections: JsonMap::new(),
        system_prompt: String::new(),
        prompt: vec![serde_json::json!({"type": "text", "text": "hi"})],
        resume_session_id: None,
        workspace_cwd: None,
        context: JsonMap::new(),
        // Not an object, so the spawned run journals its failure and ends
        // without going anywhere near a driver. This test is about whether
        // the command is admitted, not about what it then does.
        mcp: serde_json::Value::Null,
        run_deadline: Utc::now() + chrono::Duration::minutes(5),
        system_prompt_delivery: None,
    };
    Command {
        command_id: Uuid::new_v4(),
        kind: CommandKind::StartRun,
        created_at: Utc::now(),
        expires_at: Utc::now() + chrono::Duration::minutes(1),
        run_id: Some(run_id),
        lease_epoch: Some(1),
        payload: serde_json::to_value(&spec).unwrap(),
    }
}

/// The race that rejected a run for naming the *newest* harness revision.
///
/// A publish reaches the loop through a channel and only changes
/// `self.harnesses` when something drains it. Draining used to happen at
/// the top of an iteration and nowhere else, while commands are handled in
/// the same iteration that polled them — so a publish that landed during
/// the poll sat unread, and `handle_start` judged the command against the
/// revision this host held *before* it. Lemma mints against the revision it
/// was just told, which is exactly the one still in the channel.
///
/// Observed as: `commanded=565b1f22 published=ec0f5482`, two seconds after
/// `published probed harnesses ... claude-code@565b1f22`.
#[tokio::test]
async fn a_publish_still_in_the_channel_is_applied_before_a_command_is_judged() {
    let mut harness = Harness::with_manifest(echo_manifest()).await;
    let harness_id = Uuid::new_v4();
    harness.worker.store_published(ProbedHarnesses {
        published: vec![published_harness(harness_id, REVISION_A)],
        probes: HashMap::new(),
        retry_soon: false,
    });
    // Published, but not yet drained — the state the host is in for as
    // long as a poll is being held open.
    harness
        .worker
        .probed
        .0
        .send(Some(ProbedHarnesses {
            published: vec![published_harness(harness_id, REVISION_B)],
            probes: HashMap::new(),
            retry_soon: false,
        }))
        .unwrap();

    let run_id = Uuid::new_v4();
    let command = start_command(harness_id, run_id, REVISION_B);
    harness.worker.sync_harnesses_for_commands().await;
    let outcome = harness.worker.handle_command(&command);

    assert!(
        outcome.is_ok(),
        "a run naming the revision this host just published was refused: {:?}",
        outcome.err()
    );
    assert!(
        harness
            .journal
            .get_run(harness.target_id, run_id)
            .unwrap()
            .is_some(),
        "an accepted start leaves a run to report on"
    );
}

/// The fence itself still holds: a command naming a revision this host has
/// replaced is refused, and says so in terms someone can act on.
#[tokio::test]
async fn a_genuinely_stale_start_is_refused_with_both_revisions() {
    let mut harness = Harness::with_manifest(echo_manifest()).await;
    let harness_id = Uuid::new_v4();
    harness.worker.store_published(ProbedHarnesses {
        published: vec![published_harness(harness_id, REVISION_B)],
        probes: HashMap::new(),
        retry_soon: false,
    });
    harness.worker.refresh_due = std::time::Instant::now() + Duration::from_secs(900);

    let command = start_command(harness_id, Uuid::new_v4(), REVISION_A);
    harness.worker.sync_harnesses_for_commands().await;
    let error = harness
        .worker
        .handle_command(&command)
        .expect_err("a superseded revision is refused");

    // Lemma classifies on this substring, and stores the rest as the
    // command's rejection detail — the only record of what the machine
    // held that anyone reading the run will ever see.
    let detail = error.to_string();
    assert!(detail.contains("revision changed"), "{detail}");
    assert!(detail.contains("b2b2b2b2"), "{detail}");
    assert!(detail.contains("a1a1a1a1"), "{detail}");
    assert!(
        harness.worker.refresh_due <= std::time::Instant::now(),
        "a fenced command asks this host to publish again rather than \
         failing every run until the next refresh"
    );
}
