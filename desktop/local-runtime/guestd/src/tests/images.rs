//! Image warm-up, repair, and the marker that says a cache is ready.

use super::*;

#[test]
fn sandbox_image_marker_probe_is_offline_and_checks_the_runtime_entrypoint() {
    let root = tempdir().unwrap();
    let service = GuestService::new(
        FakeEngine::new(vec![output(true, "")]),
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();

    assert!(service.sandbox_image_marker_is_ready(
        "ghcr.io/lemma/workspace@sha256:abc",
        WorkloadKind::Workspace,
    ));
    assert_eq!(
        service.engine.commands.lock().unwrap()[0],
        vec![
            "run",
            "--rm",
            "--network",
            "none",
            "--platform",
            guest_platform(),
            "ghcr.io/lemma/workspace@sha256:abc",
            "/usr/bin/test",
            "-s",
            "/usr/local/bin/start-workspace-runtime",
        ]
    );
}

#[test]
fn image_repair_pull_explicitly_unpacks_the_selected_platform() {
    let root = tempdir().unwrap();
    let service = GuestService::new(
        FakeEngine::new(vec![output(true, "")]),
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();

    service
        .pull_image("ghcr.io/lemma/runtime@sha256:abc")
        .unwrap();
    assert_eq!(
        service.engine.commands.lock().unwrap()[0],
        vec![
            "pull",
            "--quiet",
            "--unpack=true",
            "--platform",
            guest_platform(),
            "ghcr.io/lemma/runtime@sha256:abc",
        ]
    );
}

#[test]
fn incomplete_sandbox_image_reference_is_replaced_before_repull() {
    let root = tempdir().unwrap();
    let service = GuestService::new(
        FakeEngine::new(vec![
            output(true, "{}"),
            output(false, ""),
            output(true, ""),
            output(true, ""),
            output(true, ""),
            output(true, ""),
        ]),
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();

    service
        .ensure_sandbox_image(
            "ghcr.io/lemma/runtime@sha256:abc",
            WorkloadKind::Workspace,
            true,
        )
        .unwrap();
    let commands = service.engine.commands.lock().unwrap();
    assert_eq!(commands[2], vec!["container", "prune", "--force"]);
    assert_eq!(
        commands[3],
        vec!["rmi", "--force", "ghcr.io/lemma/runtime@sha256:abc"]
    );
    assert_eq!(commands[4][0], "pull");
}

#[test]
fn unrecoverable_image_cache_persists_a_health_gated_reset_marker() {
    let root = tempdir().unwrap();
    let service = GuestService::new(
        FakeEngine::new(vec![
            output(true, "{}"),
            output(false, ""),
            output(true, ""),
            output(true, ""),
            output(true, ""),
            output(false, ""),
            output(true, ""),
        ]),
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();

    let error = service
        .ensure_sandbox_image(
            "ghcr.io/lemma/runtime@sha256:abc",
            WorkloadKind::Workspace,
            true,
        )
        .unwrap_err();

    assert_eq!(error.code, "guest_cache_repair_required");
    let marker = service.cache_reset_marker();
    assert!(marker.is_file());
    assert_eq!(service.health().unwrap()["status"], "ready");
    let repair_due = marker.metadata().unwrap().modified().unwrap()
        + CACHE_REPAIR_RESPONSE_GRACE
        + Duration::from_secs(1);
    assert_eq!(
        service.health_at(repair_due).unwrap_err().code,
        "guest_cache_repair_required"
    );
}
