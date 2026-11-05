use super::{StartGates, is_transient_start_failure};

/// A run queued behind another one's start is waiting, not stuck: Stop has
/// to reach it there, and it gives up its place without ever spawning.
#[tokio::test]
async fn a_run_waiting_to_start_can_be_stopped_in_the_queue() {
    let gates = StartGates::with_limit(1);
    let _starting = gates
        .acquire("codex", super::never_cancelled())
        .await
        .expect("the first run starts at once");
    let (stop, cancel) = tokio::sync::watch::channel(false);

    let queued = gates.acquire("codex", cancel);
    tokio::pin!(queued);
    assert!(
        tokio::time::timeout(std::time::Duration::from_millis(50), &mut queued)
            .await
            .is_err(),
        "the second waits while the first is starting"
    );
    stop.send(true).unwrap();

    assert!(queued.await.is_none(), "and leaves the queue when stopped");
}

/// Agents are gated separately: a Codex burst does not hold up Claude Code.
#[tokio::test]
async fn each_agent_has_its_own_gate() {
    let gates = StartGates::with_limit(1);
    let _codex = gates.acquire("codex", super::never_cancelled()).await;

    let claude = tokio::time::timeout(
        std::time::Duration::from_secs(1),
        gates.acquire("claude-code", super::never_cancelled()),
    )
    .await;

    assert!(matches!(claude, Ok(Some(_))));
}

#[test]
fn only_a_timeout_is_worth_starting_again() {
    for transient in [
        r#"Internal error: "the agent did not answer session/new within 30s""#,
        r#"Internal error: "the agent did not answer initialize within 30s""#,
        r#"Internal error: {"details": "workspace routing discovery timed out"}"#,
    ] {
        assert!(is_transient_start_failure(transient), "{transient}");
    }
    for deterministic in [
        "Failed to authenticate: not logged in",
        r#"Internal error: {"details": "workspace routing discovery unauthorized (401)"}"#,
        r#"Invalid params: "unknown model provider \"nope\" in config.toml""#,
        "Process exited with exit status: 1",
        // A timeout that is really a sign-in problem fails the same way twice.
        "timed out waiting for provider OAuth sign-in: authentication required",
    ] {
        assert!(
            !is_transient_start_failure(deterministic),
            "{deterministic}"
        );
    }
}
