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
    let source = shell_source();
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

    const APP: &str = "http://app.lemma.localhost:52501";
    const API: &str = "http://app.lemma.localhost:52502";

    /// `ready` is the one arm that makes a launch usable. It clears a stale
    /// error, takes the origin the daemon names -- only a trusted one -- and
    /// asks for the resume target and time-to-ready to be recorded, without
    /// recording either itself: that disk work used to run under `shell.ui`.
    #[test]
    fn ready_makes_the_launch_usable_and_asks_for_its_side_effects() {
        let mut ui = UiState {
            error: true,
            error_code: "backend-exited".into(),
            installed_this_launch: true,
            ..UiState::default()
        };

        let outcome = apply_locald_event(
            &mut ui,
            "ready",
            &json!({"url": APP, "api_url": API, "runtime_generation": "gen-7"}),
        );

        assert!(ui.ready && ui.running);
        assert!(
            !ui.error && ui.error_code.is_empty(),
            "a stale error survived ready"
        );
        assert_eq!((ui.url.as_str(), ui.api_url.as_str()), (APP, API));
        let write = outcome
            .resume_write
            .expect("a ready origin is recorded for resume");
        assert_eq!(write.generation, "gen-7");
        let reached = outcome
            .became_ready
            .expect("time-to-ready is recorded once");
        assert!(
            !reached.cached,
            "this launch installed, so it was not a cached start"
        );
    }

    /// Only the first `ready` of a launch is its time-to-ready.
    #[test]
    fn a_second_ready_does_not_record_time_to_ready_again() {
        let mut ui = UiState {
            ready: true,
            ..UiState::default()
        };
        let outcome = apply_locald_event(&mut ui, "ready", &json!({"url": APP, "api_url": API}));
        assert!(outcome.became_ready.is_none());
    }

    /// A ready event naming an untrusted origin records nothing to resume.
    #[test]
    fn ready_with_an_untrusted_origin_resumes_nothing() {
        let mut ui = UiState::default();
        let outcome = apply_locald_event(
            &mut ui,
            "ready",
            &json!({"url": "https://evil.example", "api_url": "https://evil.example/api"}),
        );
        assert!(outcome.resume_write.is_none());
        assert!(ui.url.is_empty());
    }

    /// A repeated Start is information, not failure: the in-flight operation
    /// is already broadcasting its progress to every client.
    #[test]
    fn a_busy_refusal_is_not_shown_as_an_error() {
        let mut ui = UiState {
            phase: "Starting".into(),
            active_operation_id: "op-1".into(),
            ..UiState::default()
        };

        apply_locald_event(
            &mut ui,
            "error",
            &json!({"event": "error", "code": "busy", "id": "op-1"}),
        );

        assert!(!ui.error);
        assert_eq!(ui.status, "Starting is still in progress…");
        assert!(
            ui.active_operation_id.is_empty(),
            "the refused operation is no longer active"
        );
    }

    /// Sharing failures belong in Local settings, not on the startup error
    /// screen in front of a workspace that is working.
    #[test]
    fn a_sharing_failure_does_not_replace_a_healthy_workspace() {
        let mut ui = UiState {
            ready: true,
            ..UiState::default()
        };
        apply_locald_event(&mut ui, "error", &json!({"code": "sharing-tunnel-failed"}));
        assert!(!ui.error);
        assert!(ui.ready);
    }

    /// Any other error is shown, with the component and log it came from.
    #[test]
    fn a_real_error_carries_its_code_message_and_source() {
        let mut ui = UiState::default();
        apply_locald_event(
            &mut ui,
            "error",
            &json!({
                "code": "backend-exited",
                "message": "the backend exited during startup",
                "component": "backend",
                "log_source": "backend",
            }),
        );
        assert!(ui.error);
        assert_eq!(ui.error_code, "backend-exited");
        assert_eq!(ui.status, "the backend exited during startup");
        assert_eq!(
            (ui.component.as_str(), ui.log_source.as_str()),
            ("backend", "backend")
        );
    }

    /// Sandbox images finish after the workspace is up, so this must touch
    /// nothing else -- writing phase or readiness would send an app the user
    /// is already in back to the splash.
    #[test]
    fn sandbox_image_progress_leaves_the_workspace_alone() {
        let mut ui = UiState {
            ready: true,
            running: true,
            phase: "Lemma is ready".into(),
            progress: 100,
            ..UiState::default()
        };

        apply_locald_event(
            &mut ui,
            "sandbox-images",
            &json!({"state": "downloading", "detail": "Downloading the workspace image"}),
        );

        assert_eq!(ui.sandbox_images, "downloading");
        assert_eq!(ui.sandbox_images_detail, "Downloading the workspace image");
        assert!(ui.ready && ui.running);
        assert_eq!((ui.phase.as_str(), ui.progress), ("Lemma is ready", 100));
    }

    /// A Windows runtime that is prepared starts the stack, in local mode.
    #[test]
    fn a_prepared_runtime_starts_lemma_in_local_mode() {
        let mut ui = UiState {
            mode: "local".into(),
            ..UiState::default()
        };
        let outcome = apply_locald_event(&mut ui, "runtime.prepared", &json!({"ready": true}));
        assert!(outcome.start_after_prepare);
        assert!(!ui.error);
    }

    /// One that needs a reboot says so, and does not try to start.
    #[test]
    fn a_runtime_that_needs_a_reboot_asks_for_one() {
        let mut ui = UiState {
            mode: "local".into(),
            ..UiState::default()
        };
        let outcome = apply_locald_event(&mut ui, "runtime.prepared", &json!({"ready": false}));
        assert!(!outcome.start_after_prepare);
        assert!(ui.error);
        assert_eq!(ui.error_code, "wsl-reboot-required");
    }

    /// `done` retires the active operation, and only the active one.
    #[test]
    fn done_retires_only_the_operation_it_names() {
        let mut ui = UiState {
            active_operation_id: "op-1".into(),
            ..UiState::default()
        };

        apply_locald_event(&mut ui, "done", &json!({"event": "done", "id": "op-other"}));
        assert_eq!(
            ui.active_operation_id, "op-1",
            "another operation's done retired this one"
        );

        apply_locald_event(&mut ui, "done", &json!({"event": "done", "id": "op-1"}));
        assert!(ui.active_operation_id.is_empty());
        assert_eq!(ui.completed_operation_ids, vec!["op-1".to_owned()]);
    }

    /// The completed list is bounded, so a long session does not grow it.
    #[test]
    fn completed_operations_are_bounded() {
        let mut ui = UiState::default();
        for index in 0..20 {
            ui.active_operation_id = format!("op-{index}");
            apply_locald_event(
                &mut ui,
                "done",
                &json!({"event": "done", "id": format!("op-{index}")}),
            );
        }
        assert_eq!(ui.completed_operation_ids.len(), 16);
        assert_eq!(
            ui.completed_operation_ids.first().map(String::as_str),
            Some("op-4")
        );
    }

    /// A sharing change moves the workspace origin, and only to a trusted one.
    #[test]
    fn a_sharing_change_moves_the_origin_only_to_a_trusted_one() {
        let mut ui = UiState::default();
        apply_locald_event(
            &mut ui,
            "sharing.changed",
            &json!({"url": APP, "api_url": API}),
        );
        assert_eq!(ui.url, APP);

        apply_locald_event(
            &mut ui,
            "sharing.changed",
            &json!({"url": "https://evil.example", "api_url": "https://evil.example/api"}),
        );
        assert_eq!(ui.url, APP, "an untrusted origin replaced a trusted one");
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

/// Which of the three things an `ExitRequested` can do, and when.
///
/// The arm this replaced ended in two branches that did the same thing, one
/// of them computing `quit_impact` and discarding the answer to decide
/// nothing at all. Two routes to one call is how one of them drifts, and
/// inside a `RunEvent` closure neither could be reached by a test.
#[test]
fn an_exit_is_allowed_held_or_turned_into_a_quit() {
    // The shutdown worker has finished. This is the exit it earned.
    assert_eq!(exit_disposition(false, true, false), ExitDisposition::Allow);

    // A server switch closes one window before opening the next, and no
    // windows looks exactly like the last one closing.
    assert_eq!(exit_disposition(true, false, false), ExitDisposition::Hold);
    assert_eq!(
        exit_disposition(true, false, true),
        ExitDisposition::Hold,
        "a swap outranks everything: there is nothing to quit about"
    );

    // Already quitting. Starting a second one is how a confirmed quit gets a
    // second dialog put in front of it.
    assert_eq!(exit_disposition(false, false, true), ExitDisposition::Hold);

    // Nothing else is true, so this is the gesture that starts the quit --
    // whether or not there is anything to warn about, because the daemon
    // outlives the app and has to be stopped either way.
    assert_eq!(exit_disposition(false, false, false), ExitDisposition::Quit);
}

/// A swap must never be mistaken for an exit that may proceed.
///
/// `may_exit` is the shutdown worker's own signal, and the two can be true at
/// once during a restart-into-another-server: the worker finished the stop it
/// was asked for while the window swap is still in flight. Taking the exit
/// there quits the app in the middle of changing servers.
#[test]
fn a_window_swap_outranks_a_finished_shutdown() {
    assert_eq!(exit_disposition(true, true, false), ExitDisposition::Hold);
}
