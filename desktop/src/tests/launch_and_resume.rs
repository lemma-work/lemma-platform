//! Coming back to Lemma: the launch log, the resume probe, and what closing
//! the window or clicking the tray does instead of quitting.

use super::*;
use std::net::TcpListener;

/// Many writers appending at once still leave one whole line each.
///
/// The launch log had lines like `17904032898721790403289872 0ms daemon
/// reported ready`: `writeln!` on an unbuffered file is a write per formatted
/// piece, and two writers interleaved the pieces.
#[test]
fn concurrent_appends_never_split_a_line() {
    let directory = tempfile::tempdir().expect("temp dir");
    let log = directory.path().join("launch.log");
    const WRITERS: usize = 8;
    const LINES: usize = 50;

    std::thread::scope(|scope| {
        for writer in 0..WRITERS {
            let log = &log;
            scope.spawn(move || {
                for line in 0..LINES {
                    append_bounded_log(log, &format!("writer {writer} line {line} done"));
                }
            });
        }
    });

    let written = std::fs::read_to_string(&log).expect("the log");
    let lines: Vec<&str> = written.lines().collect();
    assert_eq!(lines.len(), WRITERS * LINES, "a line was lost or split");
    for line in lines {
        let (timestamp, message) = line.split_once(' ').expect("timestamp then message");
        assert!(
            timestamp.len() == 13 && timestamp.bytes().all(|byte| byte.is_ascii_digit()),
            "a line began with something other than one timestamp: {line:?}"
        );
        assert!(
            message.starts_with("writer ") && message.ends_with(" done"),
            "two writers' pieces landed in one line: {line:?}"
        );
    }
}

/// The shell's tests write their logs somewhere of their own.
#[test]
fn tests_never_write_the_installed_apps_launch_log() {
    let installed = runtime_install_root();
    assert!(
        !launch_log_path().starts_with(&installed) && !install_log_path().starts_with(&installed),
        "a test that logs would append to {}",
        installed.display()
    );
}

/// The daemon says `state ready` just before `ready`. Keyed on `ui.ready`,
/// the launch already looked ready when `ready` arrived, so no launch ever
/// recorded its time-to-ready -- the launch log only ever got the line from
/// tests.
#[test]
fn ready_after_the_state_that_precedes_it_still_records_time_to_ready() {
    let mut ui = UiState::default();
    apply_locald_event(
        &mut ui,
        "state",
        &json!({"status": "running", "running": true, "ready": true}),
    );
    assert!(ui.ready);
    let ready =
        json!({"url": "http://app.lemma.localhost:1/", "api_url": "http://app.lemma.localhost:2/"});
    assert!(apply_locald_event(&mut ui, "ready", &ready)
        .became_ready
        .is_some());
    assert!(
        apply_locald_event(&mut ui, "ready", &ready)
            .became_ready
            .is_none(),
        "once per launch"
    );
}

/// Folding an event is pure: the trace is written by the side effects, once.
#[test]
fn folding_ready_writes_nothing_to_the_launch_log() {
    let source = include_str!("../locald_events.rs").replace("\r\n", "\n");
    let fold = function_body(&source, "pub(crate) fn apply_locald_event(");
    assert!(
        !fold.contains("launch_trace("),
        "the fold is run by tests in parallel; a trace from it lands in a log"
    );
    let effects = function_body(&source, "fn perform_event_side_effects(");
    assert!(effects.contains("launch_trace(\"daemon reported ready\")"));
}

fn recorded(release: &str, url: &str, api_url: &str) -> Value {
    json!({"resumeTarget": {
        "url": url, "apiUrl": api_url, "generation": "gen-42",
        "release": release, "route": "/t/pod/conversation",
    }})
}

const APP: &str = "http://app.lemma.localhost:49180";
const API: &str = "http://app.lemma.localhost:49181";

/// Each reason a launch cannot resume is told apart, because they mean
/// different things: after a quit, a miss is the design working.
#[test]
fn a_resume_says_why_it_missed() {
    assert_eq!(
        resume_target_from(&json!({}), "1.0.0").unwrap_err(),
        ResumeMiss::NoneRecorded
    );
    assert_eq!(
        resume_target_from(&recorded("0.9.0", APP, API), "1.0.0").unwrap_err(),
        ResumeMiss::OtherRelease
    );
    assert_eq!(
        resume_target_from(
            &recorded(
                "1.0.0",
                "https://elsewhere.example",
                "https://elsewhere.example"
            ),
            "1.0.0"
        )
        .unwrap_err(),
        ResumeMiss::Untrusted
    );
    let target = resume_target_from(&recorded("1.0.0", APP, API), "1.0.0").unwrap();
    assert_eq!(target.route, "/t/pod/conversation");
    assert_eq!(
        decide_resume(Ok(target), |_| false).unwrap_err(),
        ResumeMiss::NotServing
    );
    assert!(ResumeMiss::NotServing.launch_trace().contains("stopped"));
}

/// Serve `answers` bodies, one per connection, then stop.
fn serve(answers: Vec<String>) -> (u16, std::thread::JoinHandle<()>) {
    let listener = TcpListener::bind("127.0.0.1:0").expect("a local port");
    let port = listener.local_addr().unwrap().port();
    let server = std::thread::spawn(move || {
        for body in answers {
            let Ok((mut stream, _)) = listener.accept() else {
                return;
            };
            let mut request = [0_u8; 2048];
            let _ = stream.read(&mut request);
            let _ = write!(
                stream,
                "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            );
        }
    });
    (port, server)
}

fn serving_target(port: u16, generation: &str) -> ResumeTarget {
    let origin = format!("http://127.0.0.1:{port}");
    ResumeTarget {
        url: origin.clone(),
        api_url: origin,
        generation: generation.into(),
        release: env!("CARGO_PKG_VERSION").into(),
        route: "/".into(),
    }
}

/// A stack still serving the recorded generation is resumed straight into:
/// the case resume exists for, a shell that exited without stopping locald.
#[test]
fn a_stack_still_serving_the_recorded_generation_is_resumed() {
    let (port, server) = serve(vec![
        r#"{"status":"ok","instance_id":"gen-42"}"#.into(),
        r#"window.__LEMMA_RUNTIME__={"instance":"gen-42"}"#.into(),
    ]);

    let decision = decide_resume(Ok(serving_target(port, "gen-42")), resume_target_is_serving);

    server.join().unwrap();
    assert!(decision.is_ok(), "{decision:?}");
}

/// Something answering on the port with another generation is not our stack.
#[test]
fn a_port_answering_with_another_generation_is_not_resumed() {
    let (port, server) = serve(vec![r#"{"instance_id":"someone-else"}"#.into()]);

    let decision = decide_resume(Ok(serving_target(port, "gen-42")), resume_target_is_serving);

    server.join().unwrap();
    assert_eq!(decision.unwrap_err(), ResumeMiss::NotServing);
}

/// Closing the workspace window hides Lemma on every platform, Windows
/// included; only Quit stops anything. A pod app's window just closes.
#[test]
fn closing_the_workspace_hides_to_the_tray_and_other_windows_close() {
    assert!(close_hides_to_tray("main"));
    assert!(!close_hides_to_tray("pod-app-1"));
    assert!(!close_hides_to_tray("control"));
    // And the hide is not an exit: nothing stops until a quit is confirmed.
    assert_eq!(
        exit_disposition(false, false, false, false),
        ExitDisposition::Quit,
        "an exit nobody confirmed becomes a quit that stops the stack"
    );
}

/// Every Quit -- the app menu's, the tray's, ⌘Q -- is the one full quit that
/// stops the daemon, never a hide or a window close.
#[test]
fn every_quit_stops_the_daemon_rather_than_hiding() {
    let source = shell_source();
    let menu = function_body(&source, "pub(crate) fn handle_menu_action(");
    let quit_arm = menu
        .split("\"quit\" =>")
        .nth(1)
        .expect("the menus have a quit")
        .split("_ =>")
        .next()
        .unwrap();
    assert!(quit_arm.contains("request_quit(&app)"), "{quit_arm}");
    assert!(!quit_arm.contains(".hide()") && !quit_arm.contains(".close()"));
    let tray = function_body(&source, "pub(crate) fn build_tray_menu(");
    assert!(tray.contains("MenuItem::with_id(app, \"quit\", \"Quit Lemma\""));
    let stop = function_body(&source, "pub(crate) fn stop_then_quit(");
    assert!(stop.contains("\"shutdown-daemon\""));
    let leave = function_body(&source, "pub(crate) fn leave_nothing_running(");
    assert!(leave.contains("finish_locald_stop") && leave.contains("stop_locald"));
}

/// A click on the tray icon opens Lemma on Windows, where that is the
/// convention and how a closed window comes back; on macOS it opens the menu.
#[test]
fn the_tray_icon_follows_each_platforms_convention() {
    use tauri::tray::{MouseButton, MouseButtonState, TrayIconEvent, TrayIconId};
    let click = TrayIconEvent::Click {
        id: TrayIconId::new("lemma-tray"),
        position: tauri::PhysicalPosition::new(0.0, 0.0),
        rect: tauri::Rect::default(),
        button: MouseButton::Left,
        button_state: MouseButtonState::Up,
    };
    let right = TrayIconEvent::Click {
        id: TrayIconId::new("lemma-tray"),
        position: tauri::PhysicalPosition::new(0.0, 0.0),
        rect: tauri::Rect::default(),
        button: MouseButton::Right,
        button_state: MouseButtonState::Up,
    };
    assert!(
        !tray_click_opens_lemma(&right),
        "the right button is the menu"
    );
    if cfg!(windows) {
        assert!(!tray_left_click_shows_menu());
        assert!(tray_click_opens_lemma(&click));
    } else {
        assert!(tray_left_click_shows_menu());
        assert!(!tray_click_opens_lemma(&click));
    }
}

/// A second launch brings the running app back, window or no window.
#[test]
fn a_second_launch_brings_the_running_app_back() {
    let source = shell_source();
    let run = function_body(&source, "pub(crate) fn run(");
    let single_instance = run
        .split("tauri_plugin_single_instance::init(")
        .nth(1)
        .expect("the single-instance plugin")
        .split("}))")
        .next()
        .unwrap();
    assert!(
        single_instance.contains("bring_lemma_back(app)"),
        "with no main window left, a second launch showed nothing: {single_instance}"
    );
}
