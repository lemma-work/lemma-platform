use super::*;

#[test]
fn shutdown_keeps_its_progress_when_a_background_health_probe_fails() {
    assert!(!event_applies_during_shutdown(&json!({
        "event": "error", "code": "managed-runtime-lost", "message": "health deadline",
    })));
    assert!(!event_applies_during_shutdown(&json!({
        "event": "ready", "operation_id": "old-start",
    })));
    assert!(event_applies_during_shutdown(&json!({
        "event": "phase", "operation_id": "quit", "label": "Stopping Lemma",
    })));
    assert!(event_applies_during_shutdown(&json!({
        "event": "error", "id": "quit", "code": "shutdown-failed",
    })));
    assert!(event_applies_during_shutdown(&json!({
        "event": "done", "id": "quit", "cmd": "shutdown-daemon", "ok": true,
    })));
}

#[test]
fn giving_up_on_quit_says_so() {
    // Confirming "Stop and Quit" and then getting neither, silently, is the
    // failure this guards.
    let source = include_str!("../main.rs").replace("\r\n", "\n");
    let body = function_body(&source, "fn stop_then_quit(");
    assert!(
        body.contains("report_action_failure"),
        "a quit that cannot start its stop must tell the user why"
    );
}

#[test]
fn quitting_names_the_work_it_is_about_to_stop() {
    // The whole point of the prompt is that none of this is on screen. A
    // warning that says "are you sure?" and nothing else would be worse than
    // no warning, because it teaches people to dismiss it.
    let running_host = json!({"running": true, "targets": [{"name": "work"}, {"name": "home"}]});
    let lines = quit_impact_lines(true, Some(&running_host), Some("public"));
    assert_eq!(
        lines,
        vec![
            "Schedules and background work stop running.",
            "The agents on this computer stop answering (2 paired workspaces).",
            "Your public link closes.",
        ]
    );

    // Singular reads as English, and an enabled-but-unpaired host still
    // stops answering, so it is still worth one line.
    let unpaired = json!({"running": true, "targets": []});
    assert_eq!(
        quit_impact_lines(false, Some(&unpaired), None),
        vec!["The agents on this computer stop answering."]
    );
    let one = json!({"running": true, "targets": [{"name": "work"}]});
    assert!(quit_impact_lines(false, Some(&one), None)[0].ends_with("(1 paired workspace)."));

    assert_eq!(
        quit_impact_lines(true, None, Some("local_network")),
        vec![
            "Schedules and background work stop running.",
            "Your local network link closes.",
        ]
    );
}

#[test]
fn a_quit_with_nothing_running_asks_nothing() {
    // Every dialog on the way out has to earn itself. A stopped stack with
    // no Agent Host and no shared link costs the user nothing to quit, and
    // being asked anyway is how a prompt becomes noise.
    let idle_host = json!({"running": false, "targets": []});
    assert!(quit_impact_lines(false, Some(&idle_host), Some("this_computer")).is_empty());
    assert!(quit_impact_lines(false, None, None).is_empty());
}

/// A hosted user with a running Agent Host is warned too.
///
/// `quit_impact` used to return empty for any non-local mode, so quitting
/// asked nothing at all. But locald is started in hosted mode *precisely*
/// so the Agent Host can run, and a full quit stops it -- so somebody with
/// a coding agent mid-run lost it silently, while a local user got a
/// careful three-line warning. Only the stack line is local-only.
#[test]
fn a_hosted_quit_still_names_a_running_agent_host() {
    let running = json!({"running": true, "targets": ["workspace-a"]});
    let hosted = quit_impact_lines(/* stack_up */ false, Some(&running), None);
    assert_eq!(hosted.len(), 1, "{hosted:?}");
    assert!(hosted[0].contains("agents on this computer"), "{hosted:?}");
    assert!(
        !hosted.iter().any(|line| line.contains("Schedules")),
        "a hosted workspace has no local stack to stop: {hosted:?}",
    );
}

/// A confirmed quit is never left waiting forever on a stop.
///
/// `quit_after_stop` is consumed only by a `done` event saying the stop
/// succeeded, so a wedged VM left the app running on "Winding down." with
/// the user's quit unanswered -- and the error screen's button read "Try
/// again", offering to *start* Lemma to somebody who had asked to leave.
/// The shell's view of the daemon, folded one event at a time.
///
/// `handle_locald_event` was 391 lines with no test at all — the path that
/// decides what every screen shows, checked only by running the app. These
/// drive the reducer it was split into.
mod locald_events {
    use super::super::{apply_locald_event, UiState};
    use serde_json::json;

    /// A phase event is progress, and progress is not an error.
    #[test]
    fn a_phase_event_describes_the_work_without_claiming_readiness() {
        let mut ui = UiState {
            ready: true,
            ..UiState::default()
        };

        apply_locald_event(
            &mut ui,
            "phase",
            &json!({
                "label": "Starting authentication",
                "key": "supertokens",
                "progress": 40,
                "detail": "waiting for the guest",
            }),
        );

        assert_eq!(ui.phase_key, "supertokens");
        assert_eq!(ui.progress, 40);
        assert_eq!(ui.status, "Starting authentication: waiting for the guest");
        assert!(!ui.ready, "work in progress is not a ready workspace");
        assert!(!ui.error);
    }

    /// Lifecycle state outranks a stale phase.
    ///
    /// Older daemons legitimately report `stopped` while their last phase
    /// still reads ready at 100%. Taking the phase at face value showed
    /// "It's ready" over a stack that had stopped.
    #[test]
    fn a_stopped_daemon_is_shown_as_stopped_even_if_its_last_phase_said_ready() {
        let mut ui = UiState {
            phase: "Ready".into(),
            phase_key: "ready".into(),
            progress: 100,
            ready: true,
            ..UiState::default()
        };

        apply_locald_event(
            &mut ui,
            "state",
            &json!({"running": false, "ready": false, "status": "stopped"}),
        );

        assert_eq!(ui.phase_key, "stopped");
        assert_eq!(ui.progress, 0);
        assert!(!ui.ready);
    }

    /// An error the user can act on survives the ordinary status traffic
    /// that follows it, or the screen offering the fix disappears before it
    /// can be read.
    #[test]
    fn an_actionable_error_is_not_cleared_by_the_next_status() {
        let mut ui = UiState {
            error: true,
            error_code: "wsl-required".into(),
            ..UiState::default()
        };

        apply_locald_event(
            &mut ui,
            "status",
            &json!({"running": false, "ready": false, "status": "idle"}),
        );

        assert!(ui.error, "the recovery screen must not vanish on its own");
        assert_eq!(ui.error_code, "wsl-required");
    }

    /// Reaching ready is what clears it.
    #[test]
    fn becoming_ready_clears_a_previous_error() {
        let mut ui = UiState {
            error: true,
            error_code: "locald-start-failed".into(),
            ..UiState::default()
        };

        apply_locald_event(
            &mut ui,
            "status",
            &json!({"running": true, "ready": true, "status": "ready"}),
        );

        assert!(!ui.error);
        assert!(ui.error_code.is_empty());
        assert!(ui.ready);
    }

    /// Recovery options are fetched once per error, not on every event that
    /// repeats it — the daemon reports status continuously while stopped.
    #[test]
    fn terminal_recovery_is_scheduled_once_for_one_error() {
        let mut ui = UiState::default();
        let failure = json!({"running": false, "ready": false, "status": "error", "code": "x"});

        let first = apply_locald_event(&mut ui, "state", &failure);
        assert!(first.schedule_terminal_recovery, "the first error asks");

        let second = apply_locald_event(&mut ui, "status", &failure);
        assert!(
            !second.schedule_terminal_recovery,
            "repeats of the same error must not ask again"
        );
    }

    /// A workspace URL is adopted only when both halves are trusted.
    #[test]
    fn an_untrusted_workspace_url_is_refused() {
        let mut ui = UiState::default();

        apply_locald_event(
            &mut ui,
            "status",
            &json!({
                "running": true,
                "ready": true,
                "status": "ready",
                "url": "https://evil.example",
                "api_url": "https://evil.example/api",
            }),
        );

        assert!(
            ui.url.is_empty(),
            "the shell must not navigate anywhere the daemon names: {}",
            ui.url
        );
    }
}

/// The other exit: the stop lands, and nobody is asked anything.
#[test]
fn a_quit_watchdog_stands_down_once_the_stop_finishes() {
    let asked = std::cell::Cell::new(0);
    let left = std::cell::Cell::new(false);

    run_quit_watchdog(
        || {},
        || false, // `quit_after_stop` was consumed by a `done` event.
        || {
            asked.set(asked.get() + 1);
            true
        },
        || {},
        || left.set(true),
    );

    assert_eq!(asked.get(), 0, "a finished stop must not prompt");
    assert!(!left.get(), "the quit already completed on its own");
}

#[test]
fn periodic_stopped_status_keeps_an_active_startup_phase_visible() {
    assert!(should_preserve_inflight_phase(
        "shell-start-123",
        "infrastructure-health",
        "stopped"
    ));
    assert!(!should_preserve_inflight_phase(
        "",
        "infrastructure-health",
        "stopped"
    ));
    assert!(!should_preserve_inflight_phase(
        "shell-stop-123",
        "stopped",
        "stopped"
    ));
}
