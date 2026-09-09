//! Every child in its own group, and every group reaped.

// Every guard here drives a real process group, so all of them are
// `#[cfg(unix)]` -- which leaves the module empty on Windows, and an
// import with nothing to import is an error under `-D warnings`.
#[cfg(unix)]
use super::*;

/// A CLI call that times out takes what it spawned with it.
///
/// `run_cli` is how `refresh` runs, and a refresh re-probes every installed
/// agent -- which spawns each one. The timeout path used `child.kill()`,
/// which reaches the CLI and nothing it started, so a refresh that hit its
/// 180-second ceiling left a probe of every agent on the machine behind.
/// And two of the lines between the spawn and the wait used `?`, which
/// drops a `Child` -- neither killing nor reaping it.
#[cfg(unix)]
#[test]
fn a_cli_call_that_is_dropped_takes_its_process_group_with_it() {
    use std::os::unix::process::CommandExt;

    let mut command = Command::new("/bin/sh");
    command
        .args(["-c", "sleep 30 & sleep 30"])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    command.process_group(0);
    let child = command.spawn().expect("sh is available");
    let group = i32::try_from(child.id()).expect("a pid fits in i32");

    drop(Reaped(Some(child)));

    assert_ne!(
        unsafe { libc::kill(-group, 0) },
        0,
        "the group outlived the guard, so a spawned probe would too",
    );
}

/// And the shape that makes that possible is not accidental.
#[test]
fn run_cli_puts_its_child_in_its_own_group_and_never_returns_unreaped() {
    // The module that owns `run_cli`, so this fails loudly if it moves rather
    // than quietly finding nothing.
    let source = include_str!("../cli.rs").replace("\r\n", "\n");
    let start = source.find("fn run_cli(").expect("run_cli exists");
    // Character-bounded: the sources these guards read are as much prose as
    // code, and a byte offset that lands inside an em dash panics.
    let body: String = source[start..].chars().take(2000).collect();
    let body = body.as_str();

    assert!(
        body.contains("command.process_group(0)"),
        "a CLI call spawns agents; killing only the CLI orphans them",
    );
    assert!(
        body.contains("Reaped(Some(command.spawn()?))"),
        "every path out of run_cli has to reap",
    );
    assert!(
        !body.contains("let _ = child.kill();"),
        "killing the process rather than the group is what leaked",
    );
}

/// A supervisor that goes out of scope takes its sidecar with it.
///
/// The failure this guards is not subtle once seen: one `make desktop-test`
/// left two `lemma-agent-host serve` processes running forever, with no
/// terminal, no parent that would reap them, and nothing on screen. They
/// accumulate one pair per run until somebody notices the machine is warm.
///
/// The stand-in is spawned exactly the way `spawn_locked` spawns the real
/// sidecar -- `process_group(0)`, so it leads its own group. That is not
/// incidental: `terminate_process_tree` signals the *negative* pid, so a
/// child that is not a group leader is not the thing being signalled. The
/// first version of this test got that wrong, and `child.wait()` then sat
/// out the full ten minutes of a `sleep 600` before the assertion passed
/// for entirely the wrong reason.
///
/// Unix-only because it signals a real process and reads its liveness.
#[cfg(unix)]
#[test]
fn dropping_a_supervisor_kills_the_process_it_started() {
    use std::os::unix::process::CommandExt;

    let home = tempdir().unwrap();
    // Long enough that surviving is unambiguous, short enough that a bug
    // here costs seconds rather than the suite.
    let mut command = Command::new("/bin/sh");
    command.args(["-c", "sleep 30"]);
    command.process_group(0);
    let child = command.spawn().expect("sh is available");
    let pid = i32::try_from(child.id()).expect("a pid fits in i32");

    {
        let supervisor = AgentHostSupervisor::discover(&home.path().join("locald"));
        supervisor
            .state
            .lock()
            .expect("Agent Host state lock poisoned")
            .child = Some(child);
    }

    // `terminate_process_tree` signals, waits, and reaps, so by the time
    // the drop returns the process is gone rather than merely doomed.
    // Asserted on the group, which is what was signalled and what would
    // still hold an adapter the host had spawned.
    let group_alive = unsafe { libc::kill(-pid, 0) } == 0;
    assert!(
        !group_alive,
        "the sidecar's process group outlived the supervisor (pgid {pid})"
    );
}

/// A sibling in the group must not outlive the drop either.
///
/// `dropping_a_supervisor_kills_the_process_it_started` asserts the same
/// property, but it could only catch this by luck: `/bin/sh -c "sleep 30"`
/// usually execs, leaving one process, and when it forked instead the race
/// was whether the sibling had finished dying before the assertion ran. It
/// failed on CI about one run in fifty and passed everywhere else.
///
/// This makes the sibling refuse `SIGTERM` and the leader exit at once, so
/// "the leader has been reaped" is decisively not "the group is empty".
/// Against the old `terminate_process_tree` -- which returned as soon as
/// `try_wait` succeeded -- the drop returns while a live process is still in
/// the group, every time.
///
/// Unix-only because it signals a real process and reads its liveness.
#[cfg(unix)]
#[test]
fn dropping_a_supervisor_kills_a_sibling_that_refuses_to_go() {
    use std::os::unix::process::CommandExt;

    let home = tempdir().unwrap();
    let mut command = Command::new("/bin/sh");
    // The leader forks a child that ignores SIGTERM, then exits. Both sit
    // in the group the supervisor signals.
    command.args(["-c", "sh -c 'trap \"\" TERM; sleep 30' & exit 0"]);
    command.process_group(0);
    let child = command.spawn().expect("sh is available");
    let pid = i32::try_from(child.id()).expect("a pid fits in i32");

    {
        let supervisor = AgentHostSupervisor::discover(&home.path().join("locald"));
        supervisor
            .state
            .lock()
            .expect("Agent Host state lock poisoned")
            .child = Some(child);
    }

    let group_alive = unsafe { libc::kill(-pid, 0) } == 0;
    assert!(
        !group_alive,
        "a sibling that ignored SIGTERM outlived the supervisor (pgid {pid})"
    );
}
