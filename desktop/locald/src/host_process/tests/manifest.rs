//! Refusing a host pack that does not describe a stack we can run.

use super::*;

#[test]
fn health_requires_two_xx_and_the_expected_runtime_identity() {
    for status in [401, 404, 503] {
        let (unhealthy, server) = one_response(status, "runtime-123");
        assert!(probe_http(&unhealthy).is_err());
        crate::join_within(server, "the health endpoint");
    }

    let (stale, stale_server) = one_response(200, "runtime-old");
    let error = probe_http(&stale).unwrap_err();
    assert!(
        error.to_string().contains("different runtime instance"),
        "{error}"
    );
    crate::join_within(stale_server, "the stale health endpoint");

    let (healthy, healthy_server) = one_response(200, "runtime-123");
    probe_http(&healthy).unwrap();
    crate::join_within(healthy_server, "the healthy endpoint");
}

#[test]
fn validates_exact_two_process_contract_and_dependency_order() {
    let manifest = manifest(vec![
        service("frontend", &["backend"]),
        service("backend", &[]),
    ]);
    let order = validate_and_order(&manifest).unwrap();
    assert_eq!(order, vec!["backend", "frontend"]);
}

#[test]
fn compatibility_host_packs_expose_app_ports_for_the_sharing_gateway() {
    let mut frontend = service("frontend", &["backend"]);
    frontend.health = Some(HttpHealthSpec {
        url: "http://127.0.0.1:3711/runtime-config.js".into(),
        timeout_seconds: 1,
        expected_body: None,
        stabilization_seconds: 0,
    });
    let mut backend = service("backend", &[]);
    backend.health = Some(HttpHealthSpec {
        url: "http://localhost:8711/health/ready".into(),
        timeout_seconds: 1,
        expected_body: None,
        stabilization_seconds: 0,
    });
    let root = tempdir().unwrap();
    let manager = manager_in(&root, manifest(vec![frontend, backend]));

    assert_eq!(manager.application_ports(), Some((3711, 8711)));
    assert_eq!(loopback_http_port("https://127.0.0.1:3711/"), None);
    assert_eq!(loopback_http_port("http://0.0.0.0:3711/"), None);
}

#[test]
fn rejects_missing_processes_and_cycles() {
    let missing = manifest(vec![service("backend", &[])]);
    assert!(validate_and_order(&missing)
        .unwrap_err()
        .to_string()
        .contains("frontend"));

    let cycle = manifest(vec![
        service("backend", &["frontend"]),
        service("frontend", &["backend"]),
    ]);
    assert!(validate_and_order(&cycle)
        .unwrap_err()
        .to_string()
        .contains("cycle"));
}

#[test]
fn rejects_missing_migration_setup() {
    let mut value = manifest(vec![
        service("backend", &[]),
        service("frontend", &["backend"]),
    ]);
    value.setup.clear();

    assert!(validate_and_order(&value)
        .unwrap_err()
        .to_string()
        .contains("migrations setup"));
}
