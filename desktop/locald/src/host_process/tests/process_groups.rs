//! Starting and stopping real process groups.

use super::*;

#[cfg(unix)]
#[test]
fn starts_and_stops_backend_and_frontend_process_groups() {
    let command = vec![
        "/bin/sh".into(),
        "-c".into(),
        "trap 'exit 0' TERM; while :; do sleep 1; done".into(),
    ];
    let mut backend = service("backend", &[]);
    backend.command = command.clone();
    let mut frontend = service("frontend", &["backend"]);
    frontend.command = command;
    let root = tempdir().unwrap();
    let mut value = manifest(vec![frontend, backend]);
    value.setup[0].command = vec!["/usr/bin/true".into()];
    let manager = manager_in(&root, value);

    manager.start_all().unwrap();
    assert!(manager.status().iter().all(|process| process.running));
    assert_eq!(manager.status_event(None)["ready"], true);
    manager.stop_all().unwrap();
    assert!(manager.status().iter().all(|process| !process.running));
    assert_eq!(manager.status_event(None)["ready"], false);
}

/// `/proc/<pid>/stat` field 22, past a command name that fights back.
///
/// This is the parse the Linux ownership ledger depends on, and it has one
/// trap: field 2 is the command name in parentheses and a process may name
/// itself anything at all, spaces and parentheses included. Splitting the
/// whole line on whitespace mis-numbers every field after it -- which would
/// mean recording a nonsense start identity, which would mean an ownership
/// record that never matches and a child this daemon can never reclaim.
///
/// Run on every platform on purpose: the failure it guards is a string
/// parse, and the machine that most needs it is the one that cannot run
/// the code around it.
#[test]
fn a_process_start_time_survives_a_command_name_full_of_parentheses() {
    let stat = |comm: &str| {
        let mut fields = vec!["4242".to_string(), format!("({comm})")];
        // Fields 3..21, then starttime at 22.
        fields.push("S".into());
        fields.extend((4..=21).map(|field| field.to_string()));
        fields.push("987654".into());
        // And the tail the kernel keeps writing after it.
        fields.extend((23..=30).map(|field| field.to_string()));
        fields.join(" ") + "\n"
    };

    assert_eq!(
        process_start_time(&stat("sleep")).as_deref(),
        Some("987654")
    );
    assert_eq!(
        process_start_time(&stat("my (weird) name")).as_deref(),
        Some("987654"),
        "the split has to happen after the LAST close paren"
    );
    assert_eq!(
        process_start_time(&stat("node --run start")).as_deref(),
        Some("987654"),
        "a name with spaces must not shift the field numbering"
    );
    // A truncated read is not a start identity, and must not be treated as
    // one -- an empty identity matches nothing and would be recorded as if
    // it did.
    assert_eq!(process_start_time("4242 (sleep) S 4 5"), None);
    assert_eq!(process_start_time("nonsense with no paren"), None);
    assert_eq!(process_start_time(""), None);
}

/// A manager that goes out of scope takes its services with it.
///
/// `stop_all()` on the success path used to be the only thing that stopped
/// them, so a test that panicked before reaching it -- and these tests
/// assert on live process state, which is exactly what flakes under load --
/// left two `sh` loops running forever, each forking a `sleep` every
/// second. `spawn_command` sets `process_group(0)`, so closing the terminal
/// sends them nothing, and the ownership ledger that could reclaim them is
/// in a `TempDir` that is already gone. This is how a laptop ends up warm
/// for a week.
///
/// Two things had to change for this to be assertable at all: the `Drop`
/// existed only on Windows, and the monitor thread held an `Arc` of the
/// manager, so the refcount never reached zero and no `Drop` could fire.
#[cfg(unix)]
#[test]
fn dropping_a_manager_stops_the_services_it_started() {
    let root = tempdir().unwrap();
    let mut backend = service("backend", &[]);
    backend.command = long_running_command();
    let mut frontend = service("frontend", &["backend"]);
    frontend.command = long_running_command();
    let mut value = manifest(vec![backend, frontend]);
    value.setup[0].command = vec!["/usr/bin/true".into()];

    let pids: Vec<u32> = {
        let manager = manager_in(&root, value);
        manager.start_all().unwrap();
        let pids = manager
            .status()
            .iter()
            .filter_map(|process| process.pid)
            .collect();
        // No `stop_all`. This is the unwind path, written as a scope.
        pids
    };

    assert_eq!(pids.len(), 2, "both services should have been running");
    // Asserted on the process *group*, which is what `spawn_command`
    // creates and what would still hold the `sleep` children.
    for pid in pids {
        let group = i32::try_from(pid).expect("a pid fits in i32");
        let deadline = Instant::now() + Duration::from_secs(5);
        while unsafe { libc::kill(-group, 0) } == 0 && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(50));
        }
        assert_ne!(
            unsafe { libc::kill(-group, 0) },
            0,
            "process group {group} outlived the manager that started it",
        );
    }
}
