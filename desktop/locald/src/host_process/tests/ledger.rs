//! Reclaiming only a process this installation actually started.

// Every guard in this file drives a real process group, so all of them
// are `#[cfg(unix)]` -- which leaves the module empty on Windows, and an
// import with nothing to import is an error under `-D warnings`.
#[cfg(unix)]
use super::*;

#[cfg(unix)]
#[test]
fn process_ledger_reclaims_only_an_exact_owned_process() {
    use std::os::unix::process::ExitStatusExt;

    let root = tempdir().unwrap();
    let ledger_path = root.path().join("processes.json");
    let installation_id = "0123456789abcdef0123456789abcdef";
    let mut child = Command::new("/bin/sleep").arg("30").spawn().unwrap();
    // Settled, the way `record_child` records one, not sampled the instant
    // after `spawn`. Between `fork` and `exec` a Linux child is a copy of its
    // parent, so `/proc/<pid>/exe` names *this test binary* rather than
    // `sleep` -- a plausible path that happens to be the wrong one. Recording
    // that made the reclaim below decline to signal a record whose executable
    // no longer matched, and this test then waited out the whole `sleep 30`
    // and failed saying the process had exited on its own. Which is exactly
    // the window `settled_process_identity` exists to wait out, and what its
    // own doc comment predicts on a loaded runner.
    let identity = settled_process_identity(&mut child).unwrap();
    let mut backend = service("backend", &[]);
    backend.command = vec!["/bin/sleep".into(), "30".into()];
    let value = manifest(vec![backend, service("frontend", &["backend"])]);
    write_process_ledger(
        &ledger_path,
        &ProcessLedger {
            schema_version: PROCESS_LEDGER_SCHEMA_VERSION,
            installation_id: installation_id.into(),
            entries: vec![ProcessLedgerEntry {
                service_id: "backend".into(),
                pid: child.id(),
                executable: identity.executable,
                start_identity: identity.start_identity,
                installation_id: installation_id.into(),
                runtime_generation: "0123456789abcdef0123456789abcdef".into(),
            }],
        },
    )
    .unwrap();

    // Reaped in parallel, which is the whole trick.
    //
    // `terminate_verified_process` signals, then waits for the PID to stop
    // existing before escalating. A dead child nobody has reaped is a
    // zombie, and a zombie still answers `kill(pid, 0)` -- so this test's
    // process could never be observed to exit, the wait ran its full five
    // seconds every time, and the assertion that followed was left racing
    // whatever the runner did next. Production never has this problem: a
    // reclaimed process belonged to a previous locald and is reaped by
    // init, so its PID really does go away.
    //
    // Reaping here restores that, and the exit status is then an exact
    // answer rather than a deadline: signalled means reclaimed, and a
    // process that was missed runs out its own 30 seconds and fails
    // saying so.
    let reaper = thread::spawn(move || child.wait().unwrap());
    reclaim_verified_processes(&ledger_path, installation_id, &value).unwrap();
    let status = reaper.join().unwrap();
    assert!(
        status.signal().is_some(),
        "the reclaimed process exited on its own rather than being killed: \
         {status:?}",
    );
    assert!(read_process_ledger(&ledger_path)
        .unwrap()
        .entries
        .is_empty());
}

#[cfg(unix)]
#[test]
fn process_ledger_never_kills_a_pid_with_the_wrong_start_identity() {
    let root = tempdir().unwrap();
    let ledger_path = root.path().join("processes.json");
    let installation_id = "0123456789abcdef0123456789abcdef";
    let mut child = Command::new("/bin/sleep").arg("30").spawn().unwrap();
    // Settled, for the reason the guard above gives. Here it decides whether
    // this test proves anything: an unsettled executable is a second reason
    // not to signal, so the assertion below would pass without the start
    // identity -- the one thing it is about -- being consulted at all.
    let identity = settled_process_identity(&mut child).unwrap();
    let mut backend = service("backend", &[]);
    backend.command = vec!["/bin/sleep".into(), "30".into()];
    let value = manifest(vec![backend, service("frontend", &["backend"])]);
    write_process_ledger(
        &ledger_path,
        &ProcessLedger {
            schema_version: PROCESS_LEDGER_SCHEMA_VERSION,
            installation_id: installation_id.into(),
            entries: vec![ProcessLedgerEntry {
                service_id: "backend".into(),
                pid: child.id(),
                executable: identity.executable,
                start_identity: "different-process-start".into(),
                installation_id: installation_id.into(),
                runtime_generation: "0123456789abcdef0123456789abcdef".into(),
            }],
        },
    )
    .unwrap();

    reclaim_verified_processes(&ledger_path, installation_id, &value).unwrap();

    assert!(child.try_wait().unwrap().is_none());
    child.kill().unwrap();
    child.wait().unwrap();
}
