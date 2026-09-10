use super::*;

/// The shell and the daemon must derive the same endpoint name.
///
/// This exact assertion is duplicated in locald/src/paths.rs, over the same
/// path and the same expected string, because the code that produces it is
/// duplicated too and cannot cheaply be shared -- locald is a sidecar
/// binary, not a library this crate links. They drifted once: this side
/// hashed the root unnormalised while the daemon lowercased it, so on every
/// default Windows install the app opened a pipe its own daemon never
/// listened on and called it "control endpoint unavailable". Changing this
/// value without changing the other one is the bug.
#[cfg(windows)]
#[test]
fn named_pipe_name_matches_the_one_locald_listens_on() {
    assert_eq!(
        super::locald_pipe_name(std::path::Path::new(
            r"C:\Users\Example\AppData\Local\Lemma\locald"
        )),
        r"LOCAL\work.lemma.locald.a5c86f3cbfe10caf"
    );
    // Every spelling of one directory is one endpoint.
    assert_eq!(
        super::locald_pipe_name(std::path::Path::new(
            r"C:\Users\Example\AppData\Local\Lemma\locald"
        )),
        super::locald_pipe_name(std::path::Path::new(
            r"c:/users/example/appdata/local/lemma/locald/"
        ))
    );
}

#[test]
fn a_busy_daemon_refuses_an_operation_rather_than_dropping_it() {
    let mut ui = UiState::default();
    reserve_ui_operation(&mut ui, "start", "starting").unwrap();
    assert_eq!(
        reserve_ui_operation(&mut ui, "restart", "retry"),
        Err(LOCALD_BUSY.into())
    );
    assert_eq!(ui.active_operation_id, "starting");
    reserve_ui_operation(&mut ui, "shutdown-daemon", "quitting").unwrap();
    assert_eq!(ui.active_operation_id, "quitting");
    assert!(ui.completed_operation_ids.iter().any(|id| id == "starting"));
    assert_eq!(
        reserve_ui_operation(&mut ui, "start", "late-start"),
        Err(LOCALD_BUSY.into())
    );
}

#[test]
fn a_daemon_from_a_replaced_app_bundle_is_not_this_build() {
    // A real file, because identity is now the build at the path and not only
    // the path: the shell measures the sidecar it ships.
    let bundle = tempfile::tempdir().expect("a temporary bundle");
    let expected = bundle.path().join("lemma-locald");
    std::fs::write(&expected, b"the daemon this app ships").unwrap();
    let expected = expected.as_path();
    let (size, modified) = executable_stamp(expected).expect("the sidecar can be measured");
    let this_build = json!({
        "daemon_api_revision": REQUIRED_LOCALD_API_REVISION,
        "executable": path_identity(expected),
        "executable_size": size,
        "executable_modified_ms": modified.to_string(),
    });
    assert!(locald_is_this_build(&this_build, Some(expected)));

    // The failure this exists for on macOS. Updating the app moves the
    // previous bundle to the Trash; its daemon keeps running and keeps the
    // socket, reporting the same version and the same revision as the one that
    // replaced it.
    let from_the_trash = json!({
        "daemon_api_revision": REQUIRED_LOCALD_API_REVISION,
        "daemon_version": env!("CARGO_PKG_VERSION"),
        "executable": "/Users/someone/.Trash/Lemma 4.23.56 PM.app/Contents/MacOS/lemma-locald",
        "executable_size": size,
        "executable_modified_ms": modified.to_string(),
    });
    assert!(
        !locald_is_this_build(&from_the_trash, Some(expected)),
        "a daemon running from another bundle must be replaced, not adopted",
    );

    // And the failure on Windows, which the path can never catch: an in-place
    // update writes to the *same* path, so the previous version's daemon
    // answers with a path identical to the one the new shell expects.
    for (label, wrong) in [
        (
            "a different size at the same path",
            json!({
                "daemon_api_revision": REQUIRED_LOCALD_API_REVISION,
                "executable": path_identity(expected),
                "executable_size": size + 1,
                "executable_modified_ms": modified.to_string(),
            }),
        ),
        (
            "a different build time at the same path",
            json!({
                "daemon_api_revision": REQUIRED_LOCALD_API_REVISION,
                "executable": path_identity(expected),
                "executable_size": size,
                "executable_modified_ms": (modified + 1).to_string(),
            }),
        ),
        (
            "a daemon that will not say which build it is",
            json!({
                "daemon_api_revision": REQUIRED_LOCALD_API_REVISION,
                "executable": path_identity(expected),
            }),
        ),
    ] {
        assert!(
            !locald_is_this_build(&wrong, Some(expected)),
            "{label} was adopted",
        );
    }

    // Every build older than the field predates the check, so silence is a
    // mismatch rather than a pass.
    let before_the_field = json!({ "daemon_api_revision": REQUIRED_LOCALD_API_REVISION });
    assert!(!locald_is_this_build(&before_the_field, Some(expected)));

    let wrong_revision = json!({
        "daemon_api_revision": REQUIRED_LOCALD_API_REVISION + 1,
        "executable": path_identity(expected),
        "executable_size": size,
        "executable_modified_ms": modified.to_string(),
    });
    assert!(!locald_is_this_build(&wrong_revision, Some(expected)));

    // A source checkout has no packaged sidecar to be, and its daemon is
    // whatever the developer just built.
    assert!(locald_is_this_build(&before_the_field, None));
}

#[test]
fn local_navigation_accepts_only_locald_owned_loopback_origins() {
    let app_base = "http://app.lemma.localhost:63844";
    let api_base = "http://app.lemma.localhost:63845";
    for raw_url in [
        "http://app.lemma.localhost:63844/auth",
        "http://app.lemma.localhost:63845/files/download",
        "http://sales.apps.lemma.localhost:63845/",
    ] {
        let url = tauri::Url::parse(raw_url).unwrap();
        assert_eq!(
            navigation_disposition(&url, "local", app_base, api_base),
            NavigationDisposition::Allow
        );
    }

    for raw_url in [
        "http://app.lemma.localhost:3710/",
        "http://app.lemma.localhost:3711/verify-email",
        "http://app.lemma.localhost:8710/",
        "http://127.0.0.1:3000/",
        "http://192.168.1.20:8000/",
    ] {
        let url = tauri::Url::parse(raw_url).unwrap();
        assert_eq!(
            navigation_disposition(&url, "local", app_base, api_base),
            NavigationDisposition::Deny
        );
    }
}

#[test]
fn local_workspace_origin_requires_isolated_locald_ports_or_a_canonical_gateway() {
    assert!(trusted_workspace_urls(
        "http://app.lemma.localhost:63844",
        "http://app.lemma.localhost:63845"
    ));
    assert!(trusted_workspace_urls(
        "http://192.168.1.20:51324",
        "http://192.168.1.20:51324/_lemma/api"
    ));
    assert!(trusted_workspace_urls(
        "https://lemma-example.ngrok.app",
        "https://lemma-example.ngrok.app/_lemma/api"
    ));
    assert!(!trusted_workspace_urls(
        "http://app.lemma.localhost:3711",
        "http://app.lemma.localhost:8711"
    ));
    assert!(!trusted_workspace_urls(
        "http://app.lemma.localhost:63844",
        "http://app.lemma.localhost:8710"
    ));
}

/// Which operation's progress the splash shows, in every case.
///
/// The daemon serves one operation at a time, but several surfaces ask and
/// their replies interleave, so an event carries the operation it belongs
/// to and this decides whether it is the one on screen. It had no test at
/// all: `handle_locald_event` needs an `AppHandle` and a Tauri runtime, so
/// the only way to exercise any of this was to use the app and watch.
///
/// Showing the wrong one is not cosmetic. Its phases and its failures
/// describe work the person watching did not start.
#[test]
fn only_the_operation_on_screen_moves_the_splash() {
    let finished = vec!["install-1".to_string(), "start-1".to_string()];

    // Nothing on screen: the first event of an unfinished operation takes it.
    assert_eq!(
        admit_locald_event("", &finished, "start-2"),
        EventAdmission::Adopt
    );

    // Nothing on screen, but this one already ran to completion. A late
    // straggler must not reopen a finished run's progress.
    assert_eq!(
        admit_locald_event("", &finished, "start-1"),
        EventAdmission::Ignore
    );

    // The one being shown.
    assert_eq!(
        admit_locald_event("start-2", &finished, "start-2"),
        EventAdmission::Apply
    );

    // Another surface's, while one is on screen.
    assert_eq!(
        admit_locald_event("start-2", &finished, "install-9"),
        EventAdmission::Ignore
    );

    // Completion is only consulted when nothing is active: an operation
    // that is on screen keeps its own events even if a stale entry for it
    // survives in the ring.
    assert_eq!(
        admit_locald_event("start-1", &finished, "start-1"),
        EventAdmission::Apply
    );
}

/// The size cap has to be applied by the reader, not by a measurement after.
///
/// `lines()` and `read_line` both build the whole line and only then let
/// anything look at it, so a daemon that sent a gigabyte with no newline made
/// the desktop process allocate a gigabyte before the guard could object. Two
/// readers did that: the event stream and the quit path's release
/// confirmation.
#[test]
fn a_line_without_an_end_is_refused_rather_than_buffered() {
    use std::io::BufReader;

    // Well past the cap and with no newline anywhere.
    let flood = vec![b'x'; 4096];
    let mut reader = BufReader::new(flood.as_slice());
    let error = ipc_read::bounded_line(&mut reader, 1024)
        .expect_err("an unterminated line over the cap has to be an error");
    assert_eq!(error.kind(), std::io::ErrorKind::InvalidData);

    // A line exactly at the cap still frames.
    let exact = format!("{}\n", "x".repeat(1024));
    let mut reader = BufReader::new(exact.as_bytes());
    assert_eq!(
        ipc_read::bounded_line(&mut reader, 1024).expect("at the cap"),
        Some("x".repeat(1024))
    );

    // A limit at the top of `usize` must not wrap the cap to zero, which
    // would turn every message into a clean end of stream.
    let mut reader = BufReader::new("one\n".as_bytes());
    assert_eq!(
        ipc_read::bounded_line(&mut reader, usize::MAX).unwrap(),
        Some("one".into())
    );

    // Several messages on one connection, and a clean end of stream after.
    let mut reader = BufReader::new("one\ntwo\r\n".as_bytes());
    assert_eq!(
        ipc_read::bounded_line(&mut reader, 1024).unwrap(),
        Some("one".into())
    );
    assert_eq!(
        ipc_read::bounded_line(&mut reader, 1024).unwrap(),
        Some("two".into()),
        "a CRLF checkout must not leave the carriage return in the message"
    );
    assert_eq!(ipc_read::bounded_line(&mut reader, 1024).unwrap(), None);
}
