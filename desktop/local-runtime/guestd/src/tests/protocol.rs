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
        Some("127.0.0.1".into()),
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

    // `published`, not `ready`. This test is about the purge fencing; `ready`
    // is now the answer to a live health probe, and asserting it here would
    // make an unrelated test depend on a socket round-trip. Readiness has its
    // own tests, which stand a listener up on purpose.
    assert_eq!(
        status.result.unwrap()["status"]["apps"]["runtime"]["published"],
        true
    );
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

/// A runtime that answers with `response`, on a port this test bound itself.
///
/// Only the runtime is mapped: fixtures that hard-code ports like 49152 sit
/// inside Linux's ephemeral range and can be answered by another test's
/// listener.
fn status_for_runtime_answering(response: &'static [u8]) -> Value {
    use std::io::{Read, Write};

    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    std::thread::spawn(move || {
        for stream in listener.incoming() {
            let Ok(mut stream) = stream else { return };
            // The whole request, not one read of it. Closing a socket with
            // request bytes still unread makes Linux answer with a reset,
            // which reaches the probe before the response does -- so a
            // single `read` passed on macOS and failed in CI.
            let mut request = Vec::new();
            let mut chunk = [0_u8; 256];
            while !request.ends_with(b"\r\n\r\n") {
                match stream.read(&mut chunk) {
                    Ok(0) | Err(_) => break,
                    Ok(count) => request.extend_from_slice(&chunk[..count]),
                }
            }
            let _ = stream.write_all(response);
        }
    });
    let inspected = json!([{
        "Id": "sha256:exact-generation",
        "State": {"Running": true, "Status": "running"},
        "Config": {"Labels": {
            "lemma.work/workload-kind": "workspace",
            "lemma.work/image-ref": "ghcr.io/lemma/workspace@sha256:abc",
            "lemma.work/metadata": "{\"managed-by\":\"lemma-workspace\"}"
        }},
        "NetworkSettings": {"Ports": {
            "8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": port.to_string()}]
        }}
    }])
    .to_string();

    let root = tempdir().unwrap();
    let service = GuestService::new(
        FakeEngine::new(vec![output(true, &inspected)]),
        root.path().into(),
        Some("127.0.0.1".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();
    service
        .handle(GuestRequest {
            version: 1,
            capability: None,
            operation: "sandbox.status".into(),
            parameters: json!({"sandbox_id": "box-1"}),
        })
        .result
        .expect("status succeeds")
}

/// The production probe, through the request path, in both directions.
///
/// The snapshot tests decide the probe's answer and the purge test asserts
/// only `published`, so nothing else covers `snapshot_optional` actually
/// asking. Both directions, because each catches a different broken wiring: a
/// probe that never succeeds fails the first, and one that always does -- the
/// mapped-port check this replaced -- fails the second.
#[test]
fn status_reports_a_runtime_that_answers_as_ready() {
    let status = status_for_runtime_answering(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n");
    assert_eq!(status["status"]["apps"]["runtime"]["ready"], true);
    assert_eq!(status["status"]["ready"], true);
}

#[test]
fn status_reports_a_failing_runtime_as_not_ready() {
    let status = status_for_runtime_answering(
        b"HTTP/1.1 500 Internal Server Error\r\nContent-Length: 0\r\n\r\n",
    );
    assert_eq!(status["status"]["apps"]["runtime"]["published"], true);
    assert_eq!(status["status"]["apps"]["runtime"]["ready"], false);
    assert_eq!(status["status"]["ready"], false);
}
