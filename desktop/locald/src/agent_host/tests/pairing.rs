//! Whether a paired machine runs, and an unpaired one does not.

use super::*;

#[test]
fn an_unpaired_machine_stays_off_and_a_paired_one_runs() {
    // Nothing has been chosen yet, so the default has to come from whether
    // the host has work: an unpaired sidecar would only idle.
    let home = tempdir().unwrap();
    let locald_root = home.path().join("locald");
    assert!(!AgentHostSupervisor::discover(&locald_root).desired_running());

    write(
        &home.path().join("agent-host/config.json"),
        r#"{"targets": [{"name": "work", "enabled": true}]}"#,
    );
    assert!(AgentHostSupervisor::discover(&locald_root).desired_running());
}

#[test]
fn stopping_never_outlives_the_daemon_that_stopped_it() {
    // The inverse of what this used to assert. `stop()` wrote
    // `supervisor.json` so an off switch could survive a restart; with no
    // switch left anywhere, the only writers of "off" are shutdown paths —
    // a full stack stop calls `stop()` too — and persisting it there left a
    // paired machine dead with no UI able to revive it.
    let home = tempdir().unwrap();
    let locald_root = home.path().join("locald");
    write(
        &home.path().join("agent-host/config.json"),
        r#"{"targets": [{"name": "work", "enabled": true}]}"#,
    );

    let supervisor = AgentHostSupervisor::discover(&locald_root);
    supervisor.stop().unwrap();
    // This daemon stops wanting it, so `reconcile` will not respawn it...
    assert!(!supervisor.desired_running());
    // ...and the next one derives the answer from the pairing instead.
    assert!(AgentHostSupervisor::discover(&locald_root).desired_running());
    assert!(!home.path().join("agent-host/supervisor.json").exists());
}

#[test]
fn quitting_the_app_does_not_stop_it_wanting_to_run() {
    let home = tempdir().unwrap();
    let locald_root = home.path().join("locald");
    write(
        &home.path().join("agent-host/config.json"),
        r#"{"targets": [{"name": "work", "enabled": true}]}"#,
    );

    let supervisor = AgentHostSupervisor::discover(&locald_root);
    supervisor.suspend().unwrap();
    assert!(AgentHostSupervisor::discover(&locald_root).desired_running());
}
