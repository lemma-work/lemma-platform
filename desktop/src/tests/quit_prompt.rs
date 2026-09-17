use super::*;

#[test]
fn quit_watchdog_outlives_the_owned_runtime_cleanup_deadlines() {
    let cleanup = RELEASE_ON_EXIT_TIMEOUT
        + LOCALD_HANDSHAKE_BUDGET * 2
        + LOCALD_EXIT_POLL * (QUIT_DAEMON_GRACE_ATTEMPTS + LOCALD_FORCE_EXIT_ATTEMPTS) as u32
        + VM_STOP_GRACE_BUDGET
        + VM_STOP_REAP_BUDGET;
    assert!(
        QUIT_DAEMON_BUDGET > cleanup + Duration::from_secs(5),
        "the shell must not terminate its cleanup worker before it can stop an unresponsive VM"
    );
}

/// Choosing "Keep waiting" must bring the offer back, not retire it.
///
/// The watchdog used to ask once. On "Keep waiting" it re-armed the flag
/// and returned, so a stop that never confirmed left the app on "Winding
/// down." for ever with no way out -- repeating the shortcut does not help,
/// because `request_quit` returns early once the quit is confirmed.
#[test]
fn a_confirmed_quit_keeps_offering_to_leave_until_the_stop_finishes() {
    let asked = std::cell::Cell::new(0);
    let rearmed = std::cell::Cell::new(0);
    let left = std::cell::Cell::new(false);

    run_quit_watchdog(
        || {},
        || true, // The stop never lands.
        || {
            asked.set(asked.get() + 1);
            // Wait three times, then take the way out.
            asked.get() > 3
        },
        || rearmed.set(rearmed.get() + 1),
        || left.set(true),
    );

    assert_eq!(asked.get(), 4, "each budget must ask again");
    assert_eq!(rearmed.get(), 3, "waiting must re-arm so a late stop quits");
    assert!(left.get(), "Quit Anyway must actually leave");
}

#[test]
fn the_quit_prompt_offers_the_alternative_it_is_replacing() {
    // Someone pressing ⌘Q may mean "get out of my way", which is what
    // closing the window does — and unlike this, it keeps everything
    // serving. The prompt has to say so, or the only discoverable way to
    // keep schedules running is to already know about it.
    let body = quit_prompt_body(&["Schedules and background work stop running.".into()]);
    assert!(body.contains("Schedules and background work stop running."));
    assert!(body.contains("close the window"));
    // And it has to say what is not lost, or "stop" reads as "delete".
    assert!(body.contains(&format!("stay on {THIS_COMPUTER}")));
}

/// The prompt has to come forward, not just exist.
///
/// Dock → Quit arrives while another app is frontmost. The confirmation was
/// created, and its own webview was focused, but the window holding it was only
/// `show()`n -- which un-hides a window without making the application active.
/// So the prompt appeared behind whatever the person was looking at and the app
/// seemed to have ignored the Quit.
#[test]
fn a_prompt_asks_for_the_foreground_and_not_only_for_visibility() {
    let steps = std::cell::RefCell::new(Vec::new());
    let result = bring_to_front(
        || {
            steps.borrow_mut().push("show");
            Ok(())
        },
        || {
            steps.borrow_mut().push("unminimize");
            Ok(())
        },
        || {
            steps.borrow_mut().push("focus");
            Ok(())
        },
    );

    assert!(result.is_ok());
    assert_eq!(
        *steps.borrow(),
        vec!["show", "unminimize", "focus"],
        "focus is the step that was missing, and it comes last"
    );
}

/// A window that was never minimized still has to be asked for focus.
#[test]
fn a_restore_that_fails_does_not_cost_the_prompt_its_focus() {
    let focused = std::cell::Cell::new(false);
    let result = bring_to_front(
        || Ok(()),
        || Err("not minimized".into()),
        || {
            focused.set(true);
            Ok(())
        },
    );

    assert!(result.is_ok());
    assert!(
        focused.get(),
        "an unminimize that fails is not a reason to stop"
    );
}

/// Nothing to focus if the window would not show at all.
#[test]
fn a_window_that_will_not_show_is_not_then_focused() {
    let focused = std::cell::Cell::new(false);
    let result = bring_to_front(
        || Err("no window".into()),
        || Ok(()),
        || {
            focused.set(true);
            Ok(())
        },
    );

    assert_eq!(result, Err("no window".into()));
    assert!(!focused.get());
}
