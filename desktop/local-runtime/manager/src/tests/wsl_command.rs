//! Budgets, exits and pipes around one `wsl.exe` invocation.

use super::*;

/// A first install downloads about a gigabyte, and that is the user's
/// connection's business, not the guest's.
///
/// Eight minutes bounded a slow download rather than a stuck one. On a
/// real Windows machine the image phase failed on every attempt while
/// making progress on every attempt -- 317 MB in the content store after
/// ten minutes, no image completed, and no way for it ever to finish.
///
/// Larger than the guest's own pull timeout on purpose. If the host gave
/// up first the guest would keep pulling into a request nobody is waiting
/// on, and the retry would contend with it.
#[test]
fn pulling_images_is_given_longer_than_the_guest_spends_pulling_them() {
    // `ENGINE_PULL_TIMEOUT` in the guest daemon; a different crate, so the
    // number is named rather than imported.
    let guest_pull_timeout = Duration::from_secs(60 * 60);
    for operation in ["core.images", "core.sandbox_images"] {
        assert!(
            guest_request_budget(operation) > guest_pull_timeout,
            "{operation} must outlast the guest's own pull"
        );
    }
    // And nothing else grew: a wedged health probe still fails fast.
    assert_eq!(guest_request_budget("health"), Duration::from_secs(5));
    assert_eq!(
        guest_request_budget("core.postgres"),
        Duration::from_secs(8 * 60)
    );
}

#[test]
fn budgets_leave_room_for_slow_work_without_letting_a_query_hang() {
    assert!(wsl_budget(&["--import", "LemmaRuntime"]) >= Duration::from_secs(15 * 60));
    // The queries are what a wedged WSL blocks, and what the start path
    // waits on when it does, so their budget is how long a broken install
    // stays silent.
    assert!(wsl_budget(&["--list", "--quiet"]) <= Duration::from_secs(2 * 60));
    assert!(wsl_budget(&["--status"]) <= Duration::from_secs(2 * 60));
    // Guest work is not a query: `--exec lemma-runtime-init` brings the
    // whole stack up and must not be held to the query budget.
    assert!(
        wsl_budget(&[
            "--distribution",
            "LemmaRuntime",
            "--exec",
            "/usr/local/bin/lemma-runtime-init",
        ]) >= Duration::from_secs(10 * 60)
    );
    assert!(wsl_budget(&[]) > Duration::ZERO);
}

/// A wedged WSL service hangs even `--status`, and every wsl.exe call used
/// to end in an unbounded `wait_with_output()`. The start path stopped
/// there with no error and no log line, and the daemon had no way to give
/// up and report one.
///
/// Driven against `/bin/sh` rather than wsl.exe, because the defect was
/// never in wsl.exe -- it was in waiting for it. That also makes this the
/// only executed coverage the Windows runner has anywhere.
#[cfg(unix)]
#[test]
fn a_command_that_never_returns_is_killed_and_reported() {
    let root = tempdir().unwrap();
    let log = root.path().join("wsl.log");
    let started = Instant::now();
    let error = run_wsl_command(
        Path::new("/bin/sh"),
        &["-c", "sleep 120"],
        None,
        Duration::from_millis(400),
        &log,
    )
    .expect_err("a command past its budget must fail, not be waited on");
    assert_eq!(error.kind(), io::ErrorKind::TimedOut);
    assert!(
        started.elapsed() < Duration::from_secs(30),
        "waited {:?}, so nothing bounded it",
        started.elapsed()
    );
    let recorded = fs::read_to_string(&log).unwrap();
    assert!(
        recorded.contains("sleep 120"),
        "a hang has to leave evidence behind; the log says {recorded:?}"
    );
}

/// `wsl_allowing_failure` exists so a non-zero exit can be an *answer*:
/// `--terminate` against a distribution that is not running fails, and
/// that failure means "already stopped".
#[cfg(unix)]
#[test]
fn a_non_zero_exit_is_returned_rather_than_raised() {
    let root = tempdir().unwrap();
    let output = run_wsl_command(
        Path::new("/bin/sh"),
        &["-c", "echo nope >&2; exit 3"],
        None,
        Duration::from_secs(20),
        &root.path().join("wsl.log"),
    )
    .expect("a failing command is still a completed command");
    assert_eq!(output.status.code(), Some(3));
    assert_eq!(wsl_message(&output.stderr), "nope");
}

/// stdin used to be written with a blocking `write_all` before anything
/// drained stdout. A child that echoes what it reads fills its output pipe
/// at 64 KiB and blocks; this end is still blocked filling the input pipe;
/// neither side moves again. A megabyte through `cat` is exactly that
/// shape, and hangs forever against the old implementation.
#[cfg(unix)]
#[test]
fn input_larger_than_a_pipe_buffer_does_not_deadlock() {
    let root = tempdir().unwrap();
    let payload = vec![b'x'; 1024 * 1024];
    let output = run_wsl_command(
        Path::new("/bin/sh"),
        &["-c", "cat"],
        Some(&payload),
        Duration::from_secs(30),
        &root.path().join("wsl.log"),
    )
    .expect("a megabyte of stdin must not wedge the runner");
    assert!(output.status.success());
    assert_eq!(output.stdout.len(), payload.len());
}

/// The kind of a spawn failure is load-bearing: `unregister_windows_guest`
/// reads `NotFound` as "there is no WSL on this machine to unregister
/// from" and completes the uninstall. Wrapping it would turn a clean
/// uninstall into a failure the user cannot clear.
#[cfg(unix)]
#[test]
fn a_missing_wsl_executable_keeps_its_not_found_kind() {
    let root = tempdir().unwrap();
    let error = run_wsl_command(
        &root.path().join("no-such-wsl"),
        &["--list", "--quiet"],
        None,
        Duration::from_secs(5),
        &root.path().join("wsl.log"),
    )
    .expect_err("a missing executable cannot succeed");
    assert_eq!(error.kind(), io::ErrorKind::NotFound);
}
