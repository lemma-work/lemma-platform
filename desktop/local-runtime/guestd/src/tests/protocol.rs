//! The wire: capability, framing, and failing closed.

use super::*;

#[test]
fn stale_stopped_container_is_removed_for_safe_recreation() {
    let root = tempdir().unwrap();
    let service = GuestService::new(
        FakeEngine::new(vec![output(false, ""), output(true, "")]),
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();

    assert!(!service
        .restart_or_remove_stale("lemma-core-postgres")
        .unwrap());
    assert_eq!(
        service.engine.commands.lock().unwrap().as_slice(),
        [
            vec!["start".to_owned(), "lemma-core-postgres".to_owned()],
            vec![
                "rm".to_owned(),
                "--force".to_owned(),
                "lemma-core-postgres".to_owned(),
            ],
        ]
    );
}

#[test]
fn protocol_requires_capability_and_rejects_tags() {
    let root = tempdir().unwrap();
    let service = GuestService::new(
        FakeEngine::new(vec![]),
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        Some("a".repeat(32)),
    )
    .unwrap();
    let unauthorized = service.handle(GuestRequest {
        version: 1,
        capability: None,
        operation: "health".into(),
        parameters: json!({}),
    });
    let invalid_image = service.handle(GuestRequest {
        version: 1,
        capability: Some("a".repeat(32)),
        operation: "sandbox.ensure".into(),
        parameters: json!({
            "sandbox_id": "box-1", "image": "runtime:latest",
            "apps": [],
        }),
    });

    assert_eq!(unauthorized.error.unwrap().code, "unauthorized");
    assert_eq!(invalid_image.error.unwrap().code, "invalid_request");
}

#[test]
fn status_and_exact_purge_fail_closed() {
    let root = tempdir().unwrap();
    let service = GuestService::new(
        FakeEngine::new(vec![output(true, &inspect()), output(true, &inspect())]),
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();
    let status = service.handle(GuestRequest {
        version: 1,
        capability: None,
        operation: "sandbox.status".into(),
        parameters: json!({"sandbox_id": "box-1"}),
    });
    let conflict = service.handle(GuestRequest {
        version: 1,
        capability: None,
        operation: "sandbox.purge".into(),
        parameters: json!({
            "sandbox_id": "box-1", "provider_id": "different"
        }),
    });

    assert_eq!(status.result.unwrap()["status"]["ready"], true);
    assert_eq!(conflict.error.unwrap().code, "generation_conflict");
}

#[test]
fn bounded_json_transport_returns_one_response() {
    let root = tempdir().unwrap();
    let service = GuestService::new(
        FakeEngine::new(vec![output(true, "")]),
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();
    let mut output = Vec::new();
    let ok = handle_reader(
        br#"{"version":1,"operation":"health","parameters":{}}
"#
        .as_slice(),
        &mut output,
        &service,
    )
    .unwrap();

    assert!(ok);
    let response: Value = serde_json::from_slice(&output).unwrap();
    assert_eq!(response["result"]["engine"], "containerd");
}

#[test]
fn persistent_transport_handles_multiple_requests_on_one_connection() {
    let root = tempdir().unwrap();
    let service = GuestService::new(
        FakeEngine::new(vec![output(true, ""), output(true, "")]),
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();
    let request = concat!(
        "{\"version\":1,\"operation\":\"health\",\"parameters\":{}}\n",
        "{\"version\":1,\"operation\":\"health\",\"parameters\":{}}\n",
    );
    let mut output = Vec::new();

    handle_stream(request.as_bytes(), &mut output, &service).unwrap();

    let responses = String::from_utf8(output).unwrap();
    assert_eq!(responses.lines().count(), 2);
    for line in responses.lines() {
        let response: Value = serde_json::from_str(line).unwrap();
        assert_eq!(response["result"]["engine"], "containerd");
    }
}

#[test]
fn health_fails_closed_when_container_engine_storage_is_unwritable() {
    let root = tempdir().unwrap();
    let service = GuestService::new(
        FakeEngine::new(vec![]),
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();

    let error = service.health().unwrap_err();

    assert_eq!(error.code, "guest_engine_failed");
    assert!(error.message.contains("no fake output"));
}

#[test]
fn missing_sandbox_is_not_found() {
    let root = tempdir().unwrap();
    let service = GuestService::new(
        FakeEngine::new(vec![output(false, "")]),
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();
    let response = service.handle(GuestRequest {
        version: 1,
        capability: None,
        operation: "sandbox.status".into(),
        parameters: json!({"sandbox_id": "box-1"}),
    });

    assert_eq!(response.error.unwrap().code, "not_found");
}

#[test]
fn core_secrets_are_validated_before_engine_operations() {
    let root = tempdir().unwrap();
    let service = GuestService::new(
        FakeEngine::new(vec![]),
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();
    let response = service.handle(GuestRequest {
        version: 1,
        capability: None,
        operation: "core.ensure".into(),
        parameters: json!({
            "images": {
                "postgres": "postgres@sha256:abc",
                "redis": "redis@sha256:def",
                "supertokens": "supertokens@sha256:123"
            },
            "credentials": {
                "postgres_password": "too-short",
                "redis_password": "also-too-short"
            }
        }),
    });

    assert_eq!(response.error.unwrap().code, "invalid_request");
    assert!(service.engine.commands.lock().unwrap().is_empty());
}
