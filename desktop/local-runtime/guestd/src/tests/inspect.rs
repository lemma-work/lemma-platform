//! Reading the engine's view of a container back into ours.

use super::*;

#[test]
fn snapshot_uses_guest_ip_and_exact_container_generation() {
    let app = serving_app();
    let parsed: Value = serde_json::from_str(&inspect_serving(app.port)).unwrap();
    let snapshot =
        snapshot_from_inspect("box-1", parsed[0].as_object().unwrap(), "127.0.0.1").unwrap();

    assert_eq!(snapshot["provider_id"], "sha256:exact-generation");
    assert_eq!(
        snapshot["status"]["runtime_url"],
        format!("http://127.0.0.1:{}", app.port)
    );
    assert_eq!(snapshot["status"]["ready"], true);
}

/// The lie this whole probe exists to stop telling.
///
/// A mapped port and a running container were reported as `ready: true`. On a
/// real install that is exactly what the browser and its relay looked like
/// while both refused every connection, so the backend dialled an endpoint the
/// guest had just promised was good and got ECONNREFUSED.
#[test]
fn an_eager_app_nothing_is_serving_is_published_but_not_ready() {
    // A port that is mapped in the engine's view and bound by nobody.
    let parsed: Value = serde_json::from_str(&inspect()).unwrap();
    let snapshot =
        snapshot_from_inspect("box-1", parsed[0].as_object().unwrap(), "127.0.0.1").unwrap();

    let runtime = &snapshot["status"]["apps"]["runtime"];
    assert_eq!(runtime["published"], true, "the engine did map the port");
    assert_eq!(runtime["ready"], false, "but nothing answered on it");
    assert_eq!(snapshot["status"]["ready"], false);
}

/// A lazy app that has not started is published and not ready.
///
/// This is the case the probe exists for. The browser and its relay are both
/// lazy, and both were reported `ready: true` off a mapped port while refusing
/// every connection -- so the backend dialled an endpoint the guest had just
/// promised was good and got ECONNREFUSED.
#[test]
fn a_lazy_app_that_has_not_started_says_so() {
    let parsed: Value = serde_json::from_str(&inspect()).unwrap();
    let snapshot =
        snapshot_from_inspect("box-1", parsed[0].as_object().unwrap(), "127.0.0.1").unwrap();

    let browser = &snapshot["status"]["apps"]["browser"];
    assert_eq!(browser["published"], true, "the engine did map the port");
    assert_eq!(browser["ready"], false, "but nothing answered on it");
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

/// The browser relay is a port a viewer's whole experience hangs off, and
/// for as long as this list was compiled in rather than read back, the guest
/// said it was not served.
///
/// The container really did listen: `build_run_arguments` publishes every app
/// the caller declared, and the backend has declared three since the relay
/// existed. Only the *reporting* side disagreed, so `reach_port(4850)` refused
/// a live port and the VNC pane, `browser_sign_in` and saved logins were all
/// unreachable on Desktop with nothing broken to find in the sandbox.
#[test]
fn a_sandbox_reports_the_apps_it_was_created_with_including_the_relay() {
    let declared = serde_json::to_string(&workspace_apps()).unwrap();
    let inspected = json!({
        "Id": "sha256:exact-generation",
        "State": {"Running": true, "Status": "running"},
        "Config": {"Labels": {
            "lemma.work/workload-kind": "workspace",
            "lemma.work/image-ref": "ghcr.io/lemma/workspace@sha256:abc",
            "lemma.work/metadata": "{\"managed-by\":\"lemma-workspace\"}",
            "lemma.work/apps": declared
        }},
        "NetworkSettings": {"Ports": {
            "8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": "49152"}],
            "4848/tcp": [{"HostIp": "0.0.0.0", "HostPort": "49153"}],
            "4850/tcp": [{"HostIp": "0.0.0.0", "HostPort": "49154"}]
        }}
    });

    let snapshot =
        snapshot_from_inspect("box-1", inspected.as_object().unwrap(), "192.168.64.2").unwrap();

    let relay = &snapshot["status"]["apps"]["relay"];
    assert_eq!(relay["port"], 4850);
    assert_eq!(relay["private_url"], "http://192.168.64.2:49154");
    // Published, because the container declared and mapped it. Not ready:
    // nothing has started the relay, and saying otherwise is the bug this
    // whole probe exists to stop.
    assert_eq!(relay["published"], true);
    assert_eq!(relay["ready"], false);
}

/// A container created before the label existed still has to be answered for.
///
/// Its ports were published from the same declaration; only the record of what
/// they were is missing. The compiled-in list stands in, and it now names the
/// relay too -- so an installation upgrading into this fix gets its browser
/// back without the sandbox being recreated.
#[test]
fn a_sandbox_created_before_the_label_falls_back_to_the_compiled_list() {
    let runtime = serving_app();
    let inspected = json!({
        "Id": "sha256:exact-generation",
        "State": {"Running": true, "Status": "running"},
        "Config": {"Labels": {
            "lemma.work/workload-kind": "workspace",
            "lemma.work/image-ref": "ghcr.io/lemma/workspace@sha256:abc",
            "lemma.work/metadata": "{\"managed-by\":\"lemma-workspace\"}"
        }},
        "NetworkSettings": {"Ports": {
            "8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": runtime.port.to_string()}],
            "4850/tcp": [{"HostIp": "0.0.0.0", "HostPort": "49154"}]
        }}
    });

    let snapshot =
        snapshot_from_inspect("box-1", inspected.as_object().unwrap(), "127.0.0.1").unwrap();

    assert_eq!(
        snapshot["status"]["apps"]["relay"]["private_url"],
        "http://127.0.0.1:49154"
    );
    // Lazy, so a relay nobody has reached for does not hold the sandbox back
    // from being ready -- but the eager runtime has to actually answer.
    assert_eq!(snapshot["status"]["ready"], true);
}

/// A label that is not a valid app list is ignored rather than trusted.
///
/// It is written by this guest, so a malformed one means a container this
/// guest did not create or a record that was damaged -- and the compiled-in
/// list is a better answer than a parse failure, which would take an
/// otherwise healthy sandbox out of `sandbox.list` entirely.
#[test]
fn an_unreadable_apps_label_falls_back_instead_of_failing_the_snapshot() {
    for damaged in [
        "not json",
        "[]",
        "[{\"name\":\"\",\"public_slug\":\"x\",\"port\":0}]",
    ] {
        let inspected = json!({
            "Id": "sha256:exact-generation",
            "State": {"Running": true, "Status": "running"},
            "Config": {"Labels": {
                "lemma.work/workload-kind": "workspace",
                "lemma.work/image-ref": "ghcr.io/lemma/workspace@sha256:abc",
                "lemma.work/metadata": "{\"managed-by\":\"lemma-workspace\"}",
                "lemma.work/apps": damaged
            }},
            "NetworkSettings": {"Ports": {
                "8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": "49152"}]
            }}
        });

        let snapshot =
            snapshot_from_inspect("box-1", inspected.as_object().unwrap(), "192.168.64.2")
                .unwrap_or_else(|error| panic!("{damaged:?} failed the snapshot: {error:?}"));

        assert_eq!(snapshot["status"]["apps"]["runtime"]["port"], 8080);
    }
}
