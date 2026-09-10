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
    let guest_pull_timeout = guest_pull_timeout();
    for operation in ["core.images", "core.sandbox_images"] {
        for transport in [GuestTransport::Resident, GuestTransport::PerRequest] {
            assert!(
                guest_request_budget(operation, transport) > guest_pull_timeout,
                "{operation} must outlast the guest's own pull"
            );
        }
    }
    // And nothing else grew: a wedged health probe still fails fast.
    assert_eq!(
        guest_request_budget("health", GuestTransport::PerRequest),
        Duration::from_secs(5)
    );
    assert_eq!(
        guest_request_budget("core.postgres", GuestTransport::PerRequest),
        Duration::from_secs(8 * 60)
    );
}

/// Starting a sandbox may have to fetch its image, and only one transport
/// can do that after the request has been answered.
///
/// A resident guest downloads on a worker thread and replies in seconds, so
/// its budget stays short -- lengthening it there buys nothing and makes a
/// wedged guest hold the caller for an hour. A per-request guest is
/// `wsl.exe --exec lemma-guestd request`: the process ends with the reply,
/// so the download happens inside the request or it does not happen at all,
/// and the budget has to leave room for it.
#[test]
fn a_per_request_guest_may_download_inside_the_start_it_is_answering() {
    assert!(
        guest_request_budget("sandbox.ensure", GuestTransport::PerRequest) > guest_pull_timeout(),
        "a WSL sandbox start fetches its own image, and must outlast that fetch"
    );
    assert_eq!(
        guest_request_budget("sandbox.ensure", GuestTransport::Resident),
        Duration::from_secs(8 * 60),
        "a resident guest answers a missing image in seconds; it needs no room to download"
    );
}

/// The guest's own `ENGINE_PULL_TIMEOUT`, read from the guest daemon.
///
/// A different crate -- the host links nothing from the Linux guest binary --
/// and the two numbers have to stay ordered, so this reads the constant out
/// of its source rather than restating it. A restated number is one somebody
/// changes on one side, and the failure it causes is the guest carrying on
/// downloading into a request nobody is waiting on any more.
fn guest_pull_timeout() -> Duration {
    let source = std::fs::read_to_string(
        std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../guestd/src/lib.rs"),
    )
    .expect("the guest daemon's source");
    let declaration = source
        .split("ENGINE_PULL_TIMEOUT: Duration = Duration::from_secs(")
        .nth(1)
        .expect("the guest's pull timeout is declared in one place");
    let expression = declaration
        .split(')')
        .next()
        .expect("a terminated declaration");
    let seconds: u64 = expression
        .split('*')
        .map(|factor| {
            factor
                .trim()
                .parse::<u64>()
                .expect("a product of plain integers")
        })
        .product();
    assert!(
        seconds >= 10 * 60,
        "parsed {seconds}s as the guest's pull timeout, which is too small to be \
         the real one -- the declaration this reads has probably changed shape",
    );
    Duration::from_secs(seconds)
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

/// The host waits longer than the stop it asked for can take.
///
/// This was eight seconds, for an operation whose own worst case is
/// sixty-one: sandboxes at one second each up to the ceiling of sixteen, then
/// three data services at fifteen. `nerdctl stop` works through its arguments
/// one at a time, so those add rather than overlap.
///
/// Whichever container was still stopping when the budget expired had the
/// guest terminated underneath it -- and the one most likely to still be
/// stopping is the one that takes longest, which is the database. Past its
/// grace the engine sends SIGKILL, and the next start replays the WAL instead
/// of opening.
#[test]
fn a_shutdown_is_given_longer_than_the_guest_can_spend_stopping() {
    for transport in [GuestTransport::Resident, GuestTransport::PerRequest] {
        let budget = guest_request_budget("system.shutdown", transport);
        assert!(
            budget.as_secs() > GUEST_STOP_WORST_CASE_SECONDS,
            "a shutdown gets {budget:?}, and the guest may legitimately spend \
             {GUEST_STOP_WORST_CASE_SECONDS}s. Raising the guest's grace \
             periods means raising this too, or the guest is terminated while \
             a database is still checkpointing.",
        );
    }
}

/// The one arithmetic this rests on, read from the guest rather than restated.
///
/// The numbers live in `lemma-guestd`, which does not compile for Windows and
/// so cannot be a dependency of this crate. Restating them here made two
/// independent copies of one contract -- the failure being a guest that raises
/// its grace periods while the host keeps its old deadline, and terminates a
/// shutdown that was still going. `guest_pull_timeout` above solves the same
/// problem the same way.
#[test]
fn the_worst_case_is_the_sum_the_guest_computes() {
    let source = std::fs::read_to_string(
        std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../guestd/src/capacity.rs"),
    )
    .expect("the guest daemon's capacity source");

    let declared = |name: &str| -> u64 {
        source
            .split(&format!("{name}: u32 = "))
            .nth(1)
            .or_else(|| source.split(&format!("{name}: usize = ")).nth(1))
            .unwrap_or_else(|| panic!("{name} is declared in one place"))
            .split(';')
            .next()
            .expect("a terminated declaration")
            .trim()
            .parse()
            .unwrap_or_else(|_| panic!("{name} is a plain integer"))
    };
    let sandbox_grace = declared("SANDBOX_STOP_GRACE_SECONDS");
    let core_grace = declared("CORE_STOP_GRACE_SECONDS");
    let ceiling = declared("MAX_SANDBOX_CEILING");
    let core_services = source
        .split("CORE_CONTAINERS: [&str; ")
        .nth(1)
        .expect("the core container list is declared with its length")
        .split(']')
        .next()
        .expect("a terminated array type")
        .parse::<u64>()
        .expect("a plain length");

    assert_eq!(
        GUEST_STOP_WORST_CASE_SECONDS,
        sandbox_grace * ceiling + core_grace * core_services,
        "the guest's own numbers say {}s; this crate's budget is derived from \
         {GUEST_STOP_WORST_CASE_SECONDS}s and has to move with them",
        sandbox_grace * ceiling + core_grace * core_services,
    );
}
