use super::*;

/// A restart is never a quit.
///
/// `AppHandle::restart` raises `ExitRequested` with `RESTART_EXIT_CODE` and
/// ignores `prevent_exit`, so answering it with `Quit` started a stop -- and
/// possibly a "Stop Lemma and quit?" prompt -- that raced the relaunch.
#[test]
fn a_restart_is_let_through_rather_than_turned_into_a_quit() {
    for swapping in [false, true] {
        for may_exit in [false, true] {
            for confirmed in [false, true] {
                assert_eq!(
                    exit_disposition(true, swapping, may_exit, confirmed),
                    ExitDisposition::Allow,
                );
            }
        }
    }
}

/// Dock → Quit, log out and shut down reach the same stop ⌘Q does.
#[test]
fn an_os_issued_terminate_waits_for_the_stop_and_only_asks_when_nobody_already_said() {
    assert_eq!(
        os_terminate_disposition(true, false, false),
        OsTerminate::Now
    );
    assert_eq!(os_terminate_disposition(true, true, true), OsTerminate::Now);
    assert_eq!(
        os_terminate_disposition(false, true, false),
        OsTerminate::AwaitRunningQuit,
        "a quit already in flight must not be asked about again"
    );
    assert_eq!(
        os_terminate_disposition(false, false, true),
        OsTerminate::StopWithoutAsking,
        "a logout must not be held behind a modal"
    );
    assert_eq!(
        os_terminate_disposition(false, false, false),
        OsTerminate::AskThenStop
    );
}

#[test]
fn only_logout_restart_and_shutdown_count_as_the_session_ending() {
    for code in [b"logo", b"rlgo", b"rrst", b"rest", b"rsdn", b"shut"] {
        assert!(quit_reason_ends_session(u32::from_be_bytes(*code)));
    }
    assert!(!quit_reason_ends_session(0));
    assert!(!quit_reason_ends_session(u32::from_be_bytes(*b"quit")));
}

/// Every way a quit ends answers the terminate macOS may be holding.
///
/// A missed `true` leaves AppKit in its NSTerminateLater modal loop, where the
/// app never exits; a missed `false` holds a logout until loginwindow gives up.
#[test]
fn every_quit_outcome_answers_a_held_os_terminate() {
    let source = include_str!("../quitting.rs").replace("\r\n", "\n");
    for exit in [
        "pub(crate) fn finish_quit(",
        "pub(crate) fn finish_quit_after_daemon(",
    ] {
        let body = function_body(&source, exit);
        assert!(
            body.contains("leave_app(&exiting)"),
            "{exit} must exit through leave_app"
        );
        assert!(
            !body.contains("exiting.exit(0)"),
            "{exit} exits without answering macOS"
        );
    }
    let leave = function_body(&source, "fn leave_app(");
    let answered = leave
        .find("answer_os_quit(app, true)")
        .expect("leave_app answers");
    let exited = leave.find("app.exit(0)").expect("leave_app exits");
    assert!(answered < exited, "reply before exiting: {leave}");
    let request = function_body(&source, "pub(crate) fn request_quit(");
    assert!(
        request.contains("Ok(false) => answer_os_quit(&handle, false)"),
        "declining the quit must release the terminate: {request}"
    );
    let stop = function_body(&source, "pub(crate) fn stop_then_quit(");
    assert!(stop.contains("answer_os_quit(app, false)"), "{stop}");
    let app = include_str!("../app.rs").replace("\r\n", "\n");
    assert!(app.contains("install_os_quit_handler(&handle)"));
}

/// A translocated or disk-image launch is refused before it creates a second
/// installation's worth of path-keyed state.
#[test]
fn lemma_refuses_to_run_from_a_translocated_or_mounted_location() {
    use std::path::Path;
    assert!(launch_location_problem(Path::new(
        "/private/var/folders/x/T/AppTranslocation/AB12/d/Lemma.app/Contents/MacOS/Lemma"
    ))
    .is_some_and(|message| message.contains("Applications")));
    assert!(
        launch_location_problem(Path::new("/Volumes/Lemma/Lemma.app/Contents/MacOS/Lemma"))
            .is_some_and(|message| message.contains("eject"))
    );
    assert!(
        launch_location_problem(Path::new("/Applications/Lemma.app/Contents/MacOS/Lemma"))
            .is_none()
    );
    assert!(launch_location_problem(Path::new(
        "/Users/me/Applications/Lemma.app/Contents/MacOS/Lemma"
    ))
    .is_none());
}
