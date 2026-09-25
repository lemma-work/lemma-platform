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

#[test]
fn the_host_execution_setting_is_read_from_the_hosts_config_and_defaults_off() {
    let home = tempdir().unwrap();
    let config = home.path().join("agent-host/config.json");
    assert!(!crate::agent_host::pairing::host_execution_enabled(&config));
    let local = r#"{"base_url": "http://api.127.0.0.1.sslip.io:61000/", "allow_insecure_http": true, "host_execution": true}"#;
    let hosted = r#"{"base_url": "https://api.lemma.work/", "host_execution": true}"#;
    write(&config, &format!(r#"{{"targets": [{local}]}}"#));
    assert!(crate::agent_host::pairing::host_execution_enabled(&config));
    // Only the local pairing's switch counts.
    write(&config, &format!(r#"{{"targets": [{hosted}]}}"#));
    assert!(!crate::agent_host::pairing::host_execution_enabled(&config));
    // The host-wide switch an older host wrote means the local pairing's.
    write(
        &config,
        r#"{"host_execution": true, "targets": [{"base_url": "http://127.0.0.1:8710/", "allow_insecure_http": true}]}"#,
    );
    assert!(crate::agent_host::pairing::host_execution_enabled(&config));
    write(&config, r#"{"targets": [], "host_execution": true}"#);
    assert!(!crate::agent_host::pairing::host_execution_enabled(&config));
    // Paused while somebody else is signed in.
    let paused = local.replace("}", r#", "session_paused": true}"#);
    write(&config, &format!(r#"{{"targets": [{paused}]}}"#));
    assert!(!crate::agent_host::pairing::host_execution_enabled(&config));
}

/// The pairing code goes to the host on stdin: on the argument list, any
/// process on this computer can read it while the exchange runs.
#[cfg(unix)]
#[test]
fn the_pairing_code_never_reaches_the_argument_list() {
    use std::os::unix::fs::PermissionsExt;
    let home = tempdir().unwrap();
    let record = home.path().join("record");
    let fake = home.path().join("fake-agent-host");
    write(
        &fake,
        &format!(
            "#!/bin/sh\necho \"$@\" > {record}.args\nread code\necho \"$code\" > {record}.stdin\n\
             echo \"refused $code\" >&2\nexit 1\n",
            record = record.display()
        ),
    );
    std::fs::set_permissions(&fake, std::fs::Permissions::from_mode(0o755)).unwrap();
    let mut supervisor = AgentHostSupervisor::discover(&home.path().join("locald"));
    supervisor.executable = Some(fake);
    let error = supervisor
        .pair("http://127.0.0.1:8710", "s3cret-code", "My Mac", true)
        .expect_err("the fake host refuses");
    let args = std::fs::read_to_string(record.with_extension("args")).unwrap();
    assert!(!args.contains("s3cret-code"), "{args}");
    assert!(
        args.contains("--pairing-code-stdin") && args.contains("--reenable"),
        "{args}"
    );
    let stdin = std::fs::read_to_string(record.with_extension("stdin")).unwrap();
    assert_eq!(stdin.trim(), "s3cret-code");
    assert!(!error.to_string().contains("s3cret-code"), "{error}");
}
