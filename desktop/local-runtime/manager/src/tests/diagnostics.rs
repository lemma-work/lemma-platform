//! What a failed start writes down.

use super::*;

/// A macOS start that failed used to leave nothing but the boot log.
///
/// `capture_diagnostics` returned success on macOS having written nothing
/// whatsoever -- no addresses, no routes, no listening sockets, no
/// containers, and none of the guest's own service logs -- while the
/// Windows arm collected all of it. A guest that boots fine and then fails
/// to start its services is the common failure, and it is precisely the
/// one the serial console cannot explain.
#[cfg(target_os = "macos")]
#[test]
fn a_failed_macos_start_writes_down_what_the_guest_saw() {
    use std::os::unix::fs::PermissionsExt;

    let root = tempdir().unwrap();
    fs::create_dir_all(root.path().join("artifacts/macos-aarch64")).unwrap();
    let bridge = root.path().join("lemma-runtime");
    fs::write(
        &bridge,
        "#!/bin/sh\ncat >/dev/null\ncat <<'RESPONSE'\n\
         {\"ok\":true,\"result\":{\"text\":\"--- addresses ---\\n\
         2: enp0s1    inet 192.168.64.2/24 scope global\\n\"}}\nRESPONSE\n",
    )
    .unwrap();
    fs::set_permissions(&bridge, std::fs::Permissions::from_mode(0o755)).unwrap();
    let config = ManagedRuntimeConfig {
        wsl_distribution: DEFAULT_WSL_DISTRIBUTION.to_string(),
        local_root: root.path().join("local"),
        artifact_root: root.path().join("artifacts"),
        bridge_executable: bridge,
        vz_executable: root.path().join("lemma-vz"),
    };
    let runtime = ManagedRuntime::new(config).unwrap();

    runtime.capture_diagnostics().unwrap();

    let log = fs::read_to_string(root.path().join("local/logs/guest.log")).unwrap();
    assert!(
        log.contains("--- addresses ---") && log.contains("192.168.64.2/24"),
        "the guest's own account of the failure has to reach the log: {log}"
    );
}
/// A Windows failure used to record the words "-- No entries --".
///
/// Diagnostics ran `journalctl`, and the Windows guest ships
/// `systemd=false` -- on purpose, since `lemma-runtime-init` starts
/// containerd itself -- so there was no journal and never had been. Every
/// failure on that platform captured nothing, which is how a start that
/// failed because the guest reported an unreachable address left no
/// account of itself anywhere on the machine it happened on.
///
/// The two state lines are not padding: the address list is what makes
/// that exact failure obvious at a glance.
#[test]
fn a_windows_guest_is_asked_for_the_logs_it_actually_writes() {
    assert!(
        !GUEST_DIAGNOSTICS.contains("journalctl"),
        "there is no journal in a guest that runs without systemd"
    );
    assert!(
        GUEST_DIAGNOSTICS.contains("/var/log/lemma/"),
        "these are the logs the guest does write"
    );
    for state in ["ip -4 -o addr show", "nerdctl ps -a"] {
        assert!(
            GUEST_DIAGNOSTICS.contains(state),
            "a failed guest has to be asked `{state}`"
        );
    }
    assert!(
        GUEST_DIAGNOSTICS.contains("tail -n 200"),
        "bounded per file: this runs on a machine that is already unhappy"
    );
    assert!(
        GUEST_DIAGNOSTICS.contains("set +e"),
        "a collector that fails halfway is still worth what it printed first"
    );
}
