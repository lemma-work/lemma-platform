//! Rotating the sidecar's log out from under it.

use super::*;

#[test]
fn rotation_keeps_the_running_hosts_own_log_descriptor_live() {
    use std::io::Write;

    let home = tempdir().unwrap();
    let log = home.path().join("agent-host.log");
    // The descriptor the host inherited as its stdout.
    let mut inherited = append_log(&log).unwrap();
    inherited
        .write_all(&vec![b'x'; LOG_LIMIT_BYTES as usize])
        .unwrap();
    inherited.flush().unwrap();

    rotate_log(&log).unwrap();
    inherited.write_all(b"after rotation").unwrap();
    inherited.flush().unwrap();

    assert_eq!(
        std::fs::read(&log).unwrap(),
        b"after rotation",
        "the host's inherited descriptor must keep writing to the live log"
    );
    assert_eq!(
        std::fs::metadata(home.path().join("agent-host.log.previous"))
            .unwrap()
            .len(),
        LOG_LIMIT_BYTES,
        "the rotated copy keeps what was there before"
    );
}

#[test]
fn a_long_lived_host_gets_its_log_rotated_without_respawning() {
    let home = tempdir().unwrap();
    let mut supervisor = AgentHostSupervisor::discover(&home.path().join("locald"));
    // Pinned so the test does not depend on a sidecar being installed.
    supervisor.executable = Some(home.path().join("lemma-agent-host"));
    std::fs::create_dir_all(&supervisor.data_dir).unwrap();
    std::fs::write(&supervisor.log_path, vec![b'x'; LOG_LIMIT_BYTES as usize]).unwrap();
    // Nothing to spawn: this is the tick a healthy, already-running host
    // takes, which used to leave the log untouched forever.
    supervisor.state.lock().unwrap().desired_running = false;

    supervisor.reconcile().unwrap();

    assert_eq!(std::fs::metadata(&supervisor.log_path).unwrap().len(), 0);
    assert!(supervisor.log_path.with_extension("log.previous").is_file());
}
