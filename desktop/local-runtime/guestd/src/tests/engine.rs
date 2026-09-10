//! Running an engine command under a bound, and where it writes.

use super::*;

/// A sandbox and a database are not given the same grace, and the database is
/// stopped second.
///
/// This was one `stop --time 5` over every running container. Five seconds is
/// the wrong number for Postgres, whose `SIGINT` fast shutdown has to roll back
/// and checkpoint before it exits, and the total was unbounded: `nerdctl stop`
/// works through its arguments one at a time, so the ceiling of sixteen
/// sandboxes plus three data services was ninety-five seconds inside a host
/// request budget of eight. Whatever was still stopping when that expired had
/// the guest terminated underneath it.
#[test]
fn a_sandbox_is_stopped_briefly_and_the_data_services_last() {
    let root = tempdir().unwrap();
    let service = GuestService::new(
        FakeEngine::new(vec![
            // ps --quiet
            output(true, "aabbccddeeff\n001122334455\n"),
            // inspect, one per core container, in CORE_CONTAINERS order
            output(false, ""),
            output(false, ""),
            output(true, r#"[{"Id":"001122334455"}]"#),
            // the two stops
            output(true, ""),
            output(true, ""),
        ]),
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();

    let stopped = service.stop_all_containers().unwrap();

    assert_eq!(stopped.sandboxes, 1);
    assert_eq!(stopped.core, 1);
    assert_eq!(stopped.total(), 2);
    let commands = service.engine.commands.lock().unwrap().clone();
    let stops: Vec<&Vec<String>> = commands
        .iter()
        .filter(|command| command.first().map(String::as_str) == Some("stop"))
        .collect();
    assert_eq!(
        stops,
        [
            // The sandbox, and the shorter grace.
            &vec![
                "stop".to_owned(),
                "--time".to_owned(),
                SANDBOX_STOP_GRACE_SECONDS.to_string(),
                "aabbccddeeff".to_owned(),
            ],
            // Then the database, with a grace it can use.
            &vec![
                "stop".to_owned(),
                "--time".to_owned(),
                CORE_STOP_GRACE_SECONDS.to_string(),
                "001122334455".to_owned(),
            ],
        ],
        "the data services must be stopped after the sandboxes, and for longer",
    );
}

/// Nothing running is nothing to stop, and no engine call to make.
#[test]
fn an_empty_guest_issues_no_stop_at_all() {
    let root = tempdir().unwrap();
    let service = GuestService::new(
        FakeEngine::new(vec![
            output(true, "\n"),
            output(false, ""),
            output(false, ""),
            output(false, ""),
        ]),
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();

    let stopped = service.stop_all_containers().unwrap();

    assert_eq!(stopped.total(), 0);
    assert!(service
        .engine
        .commands
        .lock()
        .unwrap()
        .iter()
        .all(|command| command.first().map(String::as_str) != Some("stop")),);
}

#[test]
fn engine_timeout_kills_the_entire_process_group() {
    let root = tempdir().unwrap();
    let executable = root.path().join("forking-engine");
    let capture_root = root.path().join("captures");
    fs::create_dir(&capture_root).unwrap();
    fs::write(
        &executable,
        format!("#!/bin/sh\nsleep {FORKING_ENGINE_SLEEP_SECS}\n"),
    )
    .unwrap();
    fs::set_permissions(&executable, fs::Permissions::from_mode(0o700)).unwrap();
    let started = Instant::now();

    let error =
        run_bounded_engine_command(&executable, &capture_root, &[], Duration::from_millis(100))
            .unwrap_err();

    // Carries the error: the one CI failure this test has produced was this
    // assertion printing nothing about what it actually got, and it was
    // neither of the two conditions reproducible under load.
    assert!(error.contains("timed out"), "{error}");
    // Well short of the sleep, so returning at all means the kill landed
    // rather than the script running itself out. Half of it, rather than a
    // fixed two seconds: the elapsed time also covers spawning, polling and
    // reaping on a machine running the rest of the suite beside it.
    let elapsed = started.elapsed();
    assert!(
        elapsed < Duration::from_secs(FORKING_ENGINE_SLEEP_SECS / 2),
        "the engine outlived its timeout by {elapsed:?}"
    );
}

#[test]
fn engine_capture_and_child_tmpdir_use_explicit_writable_storage() {
    let root = tempdir().unwrap();
    let executable = root.path().join("capture-engine");
    let capture_root = root.path().join("captures");
    fs::create_dir(&capture_root).unwrap();
    fs::write(&executable, "#!/bin/sh\nprintf '%s' \"$TMPDIR\"\n").unwrap();
    fs::set_permissions(&executable, fs::Permissions::from_mode(0o700)).unwrap();

    let output = run_bounded_engine_command(
        &executable,
        &capture_root,
        &[],
        Duration::from_secs(UNHURRIED_TEST_TIMEOUT_SECS),
    )
    .unwrap();

    assert!(output.status.success());
    assert_eq!(
        String::from_utf8(output.stdout).unwrap(),
        capture_root.to_string_lossy()
    );
}

#[test]
fn immutable_guest_routes_temporary_and_network_state_to_writable_mounts() {
    let fstab = include_str!("../../../guest-image/rootfs-overlay/etc/fstab");
    // The binds moved into the script both platforms share; see
    // `both_platforms_bind_the_data_through_the_same_script`.
    let bind_data =
        include_str!("../../../guest-image/rootfs-overlay/usr/local/bin/lemma-bind-data");
    let guest_service = include_str!(
        "../../../guest-image/rootfs-overlay/usr/local/bin/lemma-runtime-guest-service"
    );

    assert!(fstab.contains("tmpfs /tmp tmpfs"));
    assert!(bind_data.contains("$data_root/cni/net.d"));
    assert!(bind_data.contains("/etc/cni/net.d"));
    assert!(guest_service.contains("HOME=/var/lib/lemma/home"));
    assert!(guest_service.contains("LEMMA_GUEST_TEMP_ROOT=/tmp/lemma-engine"));
    assert!(guest_service.contains("TMPDIR=\"$LEMMA_GUEST_TEMP_ROOT\""));
}
