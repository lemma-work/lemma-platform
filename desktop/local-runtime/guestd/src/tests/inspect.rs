//! Reading the engine's view of a container back into ours.

use super::*;

#[test]
fn snapshot_uses_guest_ip_and_exact_container_generation() {
    let parsed: Value = serde_json::from_str(&inspect()).unwrap();
    let snapshot =
        snapshot_from_inspect("box-1", parsed[0].as_object().unwrap(), "192.168.64.2").unwrap();

    assert_eq!(snapshot["provider_id"], "sha256:exact-generation");
    assert_eq!(
        snapshot["status"]["runtime_url"],
        "http://192.168.64.2:49152"
    );
    assert_eq!(snapshot["status"]["ready"], true);
}

/// The resting state of every idle workspace, read off a real guest that
/// had one: `Running: false`, a clean exit code, and no `Status` at all.
/// Calling that ERROR made the ordinary end of an idle release look like a
/// fault, in `sandbox.list` and in everything that reads it.
#[test]
fn a_cleanly_exited_container_without_a_status_field_reads_as_stopped() {
    let inspected = json!({
        "Id": "sha256:exact-generation",
        "State": {"Running": false, "ExitCode": 0},
        "Config": {"Labels": {
            "lemma.work/workload-kind": "workspace",
            "lemma.work/image-ref": "ghcr.io/lemma/workspace@sha256:abc",
            "lemma.work/metadata": "{\"managed-by\":\"lemma-workspace\"}"
        }},
        "NetworkSettings": {"Ports": {}}
    });

    let snapshot =
        snapshot_from_inspect("box-1", inspected.as_object().unwrap(), "192.168.64.2").unwrap();

    assert_eq!(snapshot["status"]["status"], "STOPPED");
    assert_eq!(snapshot["status"]["ready"], false);
}

/// The two places that set this guest's clock have to agree on what a
/// believable host epoch is, and until now only a comment said so.
///
/// `lemma-set-host-time` runs at boot from the trusted control share;
/// `system.clock` runs for the rest of the VM's life. A range that drifted
/// apart would mean a clock the daemon refuses and the boot script accepts,
/// or the reverse -- and the symptom would be a guest silently running in
/// the wrong year.
#[test]
fn the_boot_script_and_the_daemon_trust_the_same_epoch_range() {
    let script = std::fs::read_to_string(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../guest-image/rootfs-overlay/usr/local/bin/lemma-set-host-time"
    ))
    .expect("the boot-time clock script ships with the guest image");

    assert!(
        script.contains(&MIN_TRUSTED_EPOCH.to_string()),
        "lemma-set-host-time does not mention {MIN_TRUSTED_EPOCH}"
    );
    assert!(
        script.contains(&MAX_TRUSTED_EPOCH.to_string()),
        "lemma-set-host-time does not mention {MAX_TRUSTED_EPOCH}"
    );
}

/// `dead` is a container the engine could not clean up. Reporting it as the
/// ordinary end of an idle release would hide the one state here worth
/// looking at.
#[test]
fn a_dead_container_is_a_fault_not_a_resting_state() {
    let inspected = json!({
        "Id": "sha256:exact-generation",
        "State": {"Running": false, "Status": "dead", "ExitCode": 137},
        "Config": {"Labels": {
            "lemma.work/workload-kind": "workspace",
            "lemma.work/image-ref": "ghcr.io/lemma/workspace@sha256:abc",
            "lemma.work/metadata": "{\"managed-by\":\"lemma-workspace\"}"
        }},
        "NetworkSettings": {"Ports": {}}
    });

    let snapshot =
        snapshot_from_inspect("box-1", inspected.as_object().unwrap(), "192.168.64.2").unwrap();

    assert_eq!(snapshot["status"]["status"], "ERROR");
}

/// A container that never ran and reports nothing is still a fault. The
/// fallback reads "has exited", not "is not running".
#[test]
fn a_container_that_never_started_still_reads_as_an_error() {
    let inspected = json!({
        "Id": "sha256:exact-generation",
        "State": {"Running": false},
        "Config": {"Labels": {
            "lemma.work/workload-kind": "workspace",
            "lemma.work/image-ref": "ghcr.io/lemma/workspace@sha256:abc",
            "lemma.work/metadata": "{\"managed-by\":\"lemma-workspace\"}"
        }},
        "NetworkSettings": {"Ports": {}}
    });

    let snapshot =
        snapshot_from_inspect("box-1", inspected.as_object().unwrap(), "192.168.64.2").unwrap();

    assert_eq!(snapshot["status"]["status"], "ERROR");
}
