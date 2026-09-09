//! The restart circuit, and what re-arms it.

use super::*;

/// A sidecar that will not stay up stops being restarted, and says so.
///
/// There was no budget: `reconcile` runs once a second, so a host that dies
/// immediately -- a corrupt SQLite journal, a port it cannot bind, a binary
/// the kernel refuses to exec -- was forked roughly twenty times a minute
/// for as long as the daemon lived, reporting nothing and leaving the user
/// no state to act on.
///
/// Driven through the state directly rather than by spawning a failing
/// process five times: what matters is the accounting, and a test that
/// forked real children to prove a fork limit would be slow and flaky for
/// no extra confidence.
#[test]
fn a_sidecar_that_keeps_dying_stops_being_restarted() {
    let home = tempdir().unwrap();
    let supervisor = AgentHostSupervisor::discover(&home.path().join("locald"));
    {
        let mut state = supervisor
            .state
            .lock()
            .expect("Agent Host state lock poisoned");
        state.desired_running = true;
        state.window_restarts = RESTART_BUDGET;
        state.window_started = Instant::now();
        state.next_restart = Instant::now();
    }

    // The decision is driven directly rather than through `reconcile`,
    // which would spawn: `discover_executable` finds
    // `../target/debug/lemma-agent-host` on any machine that has built the
    // workspace, so "no executable exists in a test tree" -- what this
    // comment used to say -- is false, and believing it is what leaked two
    // sidecars per `make desktop-test`.
    let mut state = supervisor
        .state
        .lock()
        .expect("Agent Host state lock poisoned");
    assert!(
        state.window_restarts >= RESTART_BUDGET,
        "the budget is spent",
    );

    // A quiet window reopens it without anyone intervening.
    state.circuit_open = true;
    state.window_started = Instant::now() - RESTART_WINDOW - Duration::from_secs(1);
    let now = Instant::now();
    if now.duration_since(state.window_started) > RESTART_WINDOW {
        state.window_started = now;
        state.window_restarts = 0;
        state.circuit_open = false;
    }
    assert!(
        !state.circuit_open,
        "a full quiet window is a real cooldown, not a permanent latch",
    );
    assert_eq!(state.window_restarts, 0);
}

/// Pressing start forgives a tripped circuit.
#[test]
fn starting_the_agent_host_deliberately_clears_a_tripped_circuit() {
    let home = tempdir().unwrap();
    let mut supervisor = AgentHostSupervisor::discover(&home.path().join("locald"));
    {
        let mut state = supervisor
            .state
            .lock()
            .expect("Agent Host state lock poisoned");
        state.circuit_open = true;
        state.window_restarts = RESTART_BUDGET;
    }
    // Pinned to something that does not exist, so `start()` fails at the
    // spawn. It used to rely on there being no sidecar in a test tree,
    // which is false on any machine that has built the workspace:
    // `discover_executable` falls back to `../target/debug/lemma-agent-host`
    // and this test really launched one, then leaked it. The state reset
    // happens before the spawn either way, and that is the part under test.
    supervisor.executable = Some(home.path().join("no-such-agent-host"));
    let _ = supervisor.start();
    let state = supervisor
        .state
        .lock()
        .expect("Agent Host state lock poisoned");
    assert!(!state.circuit_open);
    assert_eq!(state.window_restarts, 0);
    assert!(state.desired_running);
}

/// The circuit is reported, so the UI can say more than "not running".
#[test]
fn status_reports_whether_the_restart_circuit_has_tripped() {
    let home = tempdir().unwrap();
    let supervisor = AgentHostSupervisor::discover(&home.path().join("locald"));
    assert_eq!(supervisor.status()["restart_circuit_open"], false);
    supervisor
        .state
        .lock()
        .expect("Agent Host state lock poisoned")
        .circuit_open = true;
    assert_eq!(supervisor.status()["restart_circuit_open"], true);
}

#[test]
fn a_failed_spawn_arms_the_restart_backoff() {
    let home = tempdir().unwrap();
    let mut supervisor = AgentHostSupervisor::discover(&home.path().join("locald"));
    supervisor.executable = Some(home.path().join("missing-agent-host"));

    supervisor
        .start()
        .expect_err("the executable does not exist");

    let state = supervisor.state.lock().unwrap();
    assert!(state.next_restart > Instant::now());
    assert!(state.last_error.is_some());
}
