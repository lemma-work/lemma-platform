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
