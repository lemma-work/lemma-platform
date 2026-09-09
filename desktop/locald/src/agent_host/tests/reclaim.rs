//! A sidecar that outlived the daemon that started it.

use super::*;

/// A sidecar the last daemon did not live to stop is stopped by this one.
///
/// A surviving host retains its data-directory lock and prevents future
/// hosts from serving. Exercise real process identity and termination;
/// cleanup must also run if an assertion fails.
#[cfg(unix)]
#[test]
fn a_sidecar_that_outlived_its_daemon_is_reclaimed_before_the_next_spawn() {
    use std::os::unix::process::CommandExt;
    use std::os::unix::process::ExitStatusExt;

    let home = tempdir().unwrap();
    let supervisor = AgentHostSupervisor::discover(&home.path().join("locald"));
    std::fs::create_dir_all(&supervisor.data_dir).unwrap();

    let mut leftover = Reaped(Some(
        Command::new("/bin/sleep")
            .arg("30")
            .process_group(0)
            .spawn()
            .unwrap(),
    ));
    supervisor.record_running(leftover.get());
    assert!(
        supervisor.record_path.is_file(),
        "a running sidecar has to be written down, or nothing can reclaim it",
    );

    let recorded: AgentHostRecord =
        serde_json::from_slice(&std::fs::read(&supervisor.record_path).unwrap()).unwrap();
    let observed = crate::host_process::process_identity(leftover.get().id()).unwrap();
    assert_eq!(
        recorded.executable, observed.executable,
        "recorded executable changed before reclaim"
    );
    assert_eq!(
        recorded.start_identity, observed.start_identity,
        "recorded process start changed before reclaim"
    );

    // Reap concurrently because a zombie still answers kill(pid, 0).
    // A failed reclaim must fail promptly rather than waiting for sleep.
    let status = std::thread::scope(|scope| {
        let reclaim = scope.spawn(|| supervisor.reclaim_leftover());
        let deadline = Instant::now() + Duration::from_secs(7);
        let status = loop {
            if let Some(status) = leftover.get().try_wait().unwrap() {
                break Some(status);
            }
            if Instant::now() >= deadline {
                break None;
            }
            std::thread::sleep(Duration::from_millis(10));
        };
        reclaim.join().unwrap();
        status
    })
    .expect("verified leftover was not terminated within the reclaim deadline");

    assert!(
        status.signal().is_some(),
        "the leftover exited on its own rather than being reclaimed: {status:?}",
    );
    assert!(
        !supervisor.record_path.exists(),
        "a reclaimed sidecar must not be left in the record to be killed twice",
    );
}

/// Reclaiming must never kill something that merely inherited the pid.
///
/// Pids are reused, and a record can outlive its process by days. The only
/// thing that makes killing by pid safe is proving the process there now is
/// the one that was written down, which is what `start_identity` is for.
#[cfg(unix)]
#[test]
fn a_pid_that_belongs_to_something_else_now_is_left_alone() {
    let home = tempdir().unwrap();
    let supervisor = AgentHostSupervisor::discover(&home.path().join("locald"));
    std::fs::create_dir_all(&supervisor.data_dir).unwrap();

    let mut bystander = Command::new("/bin/sleep").arg("30").spawn().unwrap();
    supervisor.record_running(&mut bystander);

    // The same pid, described as a process that started at a different
    // time -- which is what a recycled pid looks like from the record's
    // side.
    let raw = std::fs::read(&supervisor.record_path).unwrap();
    let mut record: AgentHostRecord = serde_json::from_slice(&raw).unwrap();
    record.start_identity = format!("{}-not-the-same-process", record.start_identity);
    std::fs::write(
        &supervisor.record_path,
        serde_json::to_vec(&record).unwrap(),
    )
    .unwrap();

    supervisor.reclaim_leftover();

    assert!(
        bystander.try_wait().unwrap().is_none(),
        "reclaiming killed a process that only happened to hold the pid",
    );
    assert!(
        !supervisor.record_path.exists(),
        "a record that cannot be acted on is cleared, not read again every spawn",
    );
    let _ = bystander.kill();
    let _ = bystander.wait();
}

/// Another installation's record is not this installation's business.
#[cfg(unix)]
#[test]
fn a_record_from_another_installation_is_never_acted_on() {
    let home = tempdir().unwrap();
    let supervisor = AgentHostSupervisor::discover(&home.path().join("locald"));
    std::fs::create_dir_all(&supervisor.data_dir).unwrap();

    let mut other = Command::new("/bin/sleep").arg("30").spawn().unwrap();
    supervisor.record_running(&mut other);
    let raw = std::fs::read(&supervisor.record_path).unwrap();
    let mut record: AgentHostRecord = serde_json::from_slice(&raw).unwrap();
    record.installation_id = "ffffffffffffffffffffffffffffffff".into();
    std::fs::write(
        &supervisor.record_path,
        serde_json::to_vec(&record).unwrap(),
    )
    .unwrap();

    supervisor.reclaim_leftover();

    assert!(
        other.try_wait().unwrap().is_none(),
        "reclaiming crossed an installation boundary",
    );
    assert!(
        supervisor.record_path.is_file(),
        "another installation's record is left for its owner to clear",
    );
    let _ = other.kill();
    let _ = other.wait();
}

/// Stopping on purpose clears the record.
///
/// Otherwise a record outlives the process it describes, and the next
/// daemon reads it and aims `kill` at whatever holds that pid by then.
#[test]
fn stopping_clears_the_record_so_no_later_daemon_acts_on_it() {
    let home = tempdir().unwrap();
    let supervisor = AgentHostSupervisor::discover(&home.path().join("locald"));
    std::fs::create_dir_all(&supervisor.data_dir).unwrap();
    std::fs::write(&supervisor.record_path, b"{}").unwrap();

    supervisor.stop().unwrap();

    assert!(
        !supervisor.record_path.exists(),
        "a deliberate stop left the sidecar written down as still running",
    );
}
