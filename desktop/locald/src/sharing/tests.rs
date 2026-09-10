//! The sharing guards.

use super::*;
use crate::sharing::gateway::*;
use std::io::{Read, Write};
use std::sync::mpsc;

fn read_http_head(stream: &mut std::net::TcpStream) -> Vec<u8> {
    let mut received = Vec::new();
    let mut byte = [0_u8; 1];
    while !received.ends_with(b"\r\n\r\n") {
        stream.read_exact(&mut byte).unwrap();
        received.push(byte[0]);
        assert!(received.len() < 64 * 1024, "HTTP head exceeded test bound");
    }
    received
}

#[test]
fn public_activation_requires_fresh_confirmation() {
    let root = tempfile::tempdir().unwrap();
    let controller = SharingController::load(
        root.path(),
        "http://app.lemma.localhost:3711".into(),
        3711,
        8711,
    )
    .unwrap();
    let error = controller
        .prepare_enable(&EnableSharingRequest {
            mode: SharingMode::Public,
            provider: Some(TunnelProvider::Ngrok),
            ..Default::default()
        })
        .unwrap_err();
    assert_eq!(error.kind(), io::ErrorKind::PermissionDenied);
    assert!(!controller.transition_running.load(Ordering::Acquire));
}

#[test]
fn forwarding_headers_are_replaced_not_appended() {
    let mut headers = hyper::HeaderMap::new();
    headers.insert("forwarded", HeaderValue::from_static("for=attacker"));
    headers.insert("x-forwarded-for", HeaderValue::from_static("attacker"));
    headers.insert("x-forwarded-custom", HeaderValue::from_static("attacker"));
    strip_forwarding_headers(&mut headers);
    assert!(headers.get("forwarded").is_none());
    assert!(headers.get("x-forwarded-for").is_none());
    assert!(headers.get("x-forwarded-custom").is_none());
}

#[test]
fn hostname_validation_rejects_urls_and_ports() {
    assert_eq!(
        normalize_hostname("Lemma.Example.com.").unwrap(),
        "lemma.example.com"
    );
    assert!(normalize_hostname("https://lemma.example.com").is_err());
    assert!(normalize_hostname("lemma.example.com:443").is_err());
}

#[test]
fn only_public_https_urls_are_selected_from_agent_output() {
    let value = json!({
        "endpoints": [
            {"url": "http://127.0.0.1:4040"},
            {"public_url": "https://example.ngrok.app"}
        ]
    });
    assert_eq!(
        find_public_https_url(&value).as_deref(),
        Some("https://example.ngrok.app")
    );
}

#[test]
fn preferences_never_persist_an_active_mode() {
    let preferences = SharingPreferences {
        schema_version: SHARING_SCHEMA_VERSION,
        selected_interface: Some("en0".into()),
        last_provider: Some(TunnelProvider::Ngrok),
        cloudflare_setup: CloudflareSetup::Automatic,
        cloudflare_tunnel_owned: true,
        ..Default::default()
    };
    let value = serde_json::to_value(preferences).unwrap();
    assert!(value.get("mode").is_none());
    assert!(value.get("desired_state").is_none());
    assert!(value.get("credentials").is_none());
    assert!(value.get("origin_certificate").is_none());
}

#[test]
fn sharing_transitions_are_single_flight() {
    let root = tempfile::tempdir().unwrap();
    let controller = SharingController::load(
        root.path(),
        "http://app.lemma.localhost:3711".into(),
        3711,
        8711,
    )
    .unwrap();
    controller.begin_transition().unwrap();
    assert_eq!(
        controller.begin_transition().unwrap_err().kind(),
        io::ErrorKind::WouldBlock
    );
    controller.fail_transition("test complete".into());
    controller.begin_transition().unwrap();
    controller.fail_transition("test complete".into());
}

#[test]
fn cloudflare_output_parser_keeps_only_named_tunnel_identity() {
    let tunnels = parse_cloudflare_tunnels(
        br#"[{"id":"8f1","name":"lemma","connections":[]},{"name":"missing-id"}]"#,
    );
    assert_eq!(tunnels.len(), 1);
    assert_eq!(tunnels[0].id, "8f1");
    assert_eq!(tunnels[0].name, "lemma");
}

#[test]
fn cloudflare_create_parser_accepts_direct_and_wrapped_json() {
    assert_eq!(
        parse_created_cloudflare_tunnel(
            br#"{"id":"8f1","name":"lemma-desktop-abcd","credentials_file":"/secret"}"#
        )
        .unwrap()
        .id,
        "8f1"
    );
    assert_eq!(
        parse_created_cloudflare_tunnel(br#"{"result":{"id":"8f2","name":"lemma-desktop-efgh"}}"#)
            .unwrap()
            .name,
        "lemma-desktop-efgh"
    );
}

#[test]
fn managed_cloudflare_name_is_stable_and_installation_scoped() {
    let root = tempfile::tempdir().unwrap();
    let first = managed_cloudflare_tunnel_name(root.path()).unwrap();
    let second = managed_cloudflare_tunnel_name(root.path()).unwrap();
    assert_eq!(first, second);
    assert!(first.starts_with("lemma-desktop-"));
    assert_eq!(first.len(), "lemma-desktop-".len() + 12);
}

#[test]
fn sharing_preferences_migrate_existing_tunnels_to_advanced_mode() {
    let root = tempfile::tempdir().unwrap();
    write_private(
        &root.path().join("sharing.json"),
        br#"{
          "schema_version": 1,
          "last_provider": "cloudflare",
          "cloudflare_tunnel_id": "existing-id",
          "cloudflare_tunnel_name": "existing-name",
          "cloudflare_hostname": "lemma.example.com"
        }"#,
    )
    .unwrap();
    let controller = SharingController::load(
        root.path(),
        "http://app.lemma.localhost:3711".into(),
        3711,
        8711,
    )
    .unwrap();
    let preferences = controller.snapshot(false).preferences;
    assert_eq!(preferences.schema_version, SHARING_SCHEMA_VERSION);
    assert_eq!(preferences.cloudflare_setup, CloudflareSetup::Existing);
    assert!(!preferences.cloudflare_tunnel_owned);
    assert!(!preferences.cloudflare_dns_routed);
}

#[cfg(unix)]
#[test]
fn automatic_cloudflare_setup_creates_routes_and_then_reuses_one_owned_tunnel() {
    use std::os::unix::fs::PermissionsExt;

    let root = tempfile::tempdir().unwrap();
    let controller = SharingController::load(
        root.path(),
        "http://app.lemma.localhost:3711".into(),
        3711,
        8711,
    )
    .unwrap();
    let name = managed_cloudflare_tunnel_name(root.path()).unwrap();
    let executable = root.path().join("cloudflared-test");
    let calls = root.path().join("cloudflared-calls.log");
    fs::write(
        &executable,
        format!(
            "#!/bin/sh\n\
             if [ \"$3\" = \"create\" ]; then\n\
               printf '{{}}' > \"$7\"\n\
               printf '{{\"id\":\"managed-id\",\"name\":\"{name}\"}}'\n\
               printf 'create\\n' >> '{}'\n\
               exit 0\n\
             fi\n\
             if [ \"$3\" = \"route\" ] && [ \"$4\" = \"dns\" ]; then\n\
               printf 'route:%s:%s\\n' \"$5\" \"$6\" >> '{}'\n\
               exit 0\n\
             fi\n\
             exit 2\n",
            calls.display(),
            calls.display()
        ),
    )
    .unwrap();
    fs::set_permissions(&executable, fs::Permissions::from_mode(0o700)).unwrap();
    let readiness = ProviderReadiness {
        installed: true,
        authenticated: true,
        executable: Some(executable.to_string_lossy().into_owned()),
        ..Default::default()
    };

    let first = controller
        .ensure_managed_cloudflare_tunnel(&executable, &readiness, "lemma.example.com")
        .unwrap();
    assert_eq!(first.id, "managed-id");
    assert!(first.credentials.is_file());
    assert_eq!(
        fs::metadata(&first.credentials)
            .unwrap()
            .permissions()
            .mode()
            & 0o777,
        0o600
    );
    let preferences = controller.snapshot(false).preferences;
    assert_eq!(preferences.cloudflare_setup, CloudflareSetup::Automatic);
    assert!(preferences.cloudflare_tunnel_owned);
    assert!(preferences.cloudflare_dns_routed);
    assert_eq!(
        fs::read_to_string(&calls).unwrap(),
        "create\nroute:managed-id:lemma.example.com\n"
    );

    let reusable = ProviderReadiness {
        installed: true,
        authenticated: true,
        executable: Some(executable.to_string_lossy().into_owned()),
        tunnels: vec![CloudflareTunnel {
            id: "managed-id".into(),
            name,
        }],
        ..Default::default()
    };
    controller
        .ensure_managed_cloudflare_tunnel(&executable, &reusable, "lemma.example.com")
        .unwrap();
    assert_eq!(
        fs::read_to_string(&calls).unwrap(),
        "create\nroute:managed-id:lemma.example.com\n"
    );
}

#[test]
fn error_redaction_does_not_echo_token_shaped_words() {
    let redacted = redact_error("authtoken=abcdefghijklmnopqrstuvwxyz rejected");
    assert!(!redacted.contains("abcdefghijklmnopqrstuvwxyz"));
    assert!(redacted.contains("[redacted]"));
}

#[test]
fn gateway_routes_only_the_reserved_api_prefix_to_backend() {
    assert_eq!(
        proxy_target("/_lemma/api/v1/files?limit=2", 3711, 8711),
        (8711, "/v1/files?limit=2".into())
    );
    assert_eq!(proxy_target("/_lemma/api", 3711, 8711), (8711, "/".into()));
    assert_eq!(
        proxy_target("/pod/demo", 3711, 8711),
        (3711, "/pod/demo".into())
    );
}

#[test]
fn gateway_streams_sse_before_the_response_finishes_and_replaces_forwarding_headers() {
    let upstream = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
    let upstream_port = upstream.local_addr().unwrap().port();
    let (first_sent, first_received) = mpsc::channel();
    let (release_send, release_receive) = mpsc::channel();
    let server = thread::spawn(move || {
        let (mut stream, _) = upstream.accept().unwrap();
        let head = String::from_utf8(read_http_head(&mut stream)).unwrap();
        assert!(head.starts_with("GET /events?conversation=1 HTTP/1.1\r\n"));
        assert!(head
            .to_ascii_lowercase()
            .contains("\r\nhost: shared.example\r\n"));
        assert!(head
            .to_ascii_lowercase()
            .contains("\r\nx-forwarded-for: 127.0.0.1\r\n"));
        assert!(!head.contains("for=attacker"));
        stream
            .write_all(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\nB\r\ndata: one\n\n\r\n",
            )
            .unwrap();
        stream.flush().unwrap();
        first_sent.send(()).unwrap();
        release_receive
            .recv_timeout(Duration::from_secs(2))
            .unwrap();
        stream
            .write_all(b"B\r\ndata: two\n\n\r\n0\r\n\r\n")
            .unwrap();
        stream.flush().unwrap();
    });
    let mut gateway = GatewayHandle::start(
        IpAddr::V4(Ipv4Addr::LOCALHOST),
        9,
        upstream_port,
        SharingMode::Public,
    )
    .unwrap();
    let mut client = std::net::TcpStream::connect(gateway.address).unwrap();
    client
        .set_read_timeout(Some(Duration::from_secs(2)))
        .unwrap();
    client
        .write_all(
            b"GET /_lemma/api/events?conversation=1 HTTP/1.1\r\nHost: shared.example\r\nX-Forwarded-For: attacker\r\nForwarded: for=attacker\r\nConnection: close\r\n\r\n",
        )
        .unwrap();
    first_received.recv_timeout(Duration::from_secs(2)).unwrap();
    let mut observed = Vec::new();
    let mut buffer = [0_u8; 512];
    while !String::from_utf8_lossy(&observed).contains("data: one") {
        let read = client.read(&mut buffer).unwrap();
        assert!(read > 0, "gateway closed before the first SSE event");
        observed.extend_from_slice(&buffer[..read]);
    }
    release_send.send(()).unwrap();
    while !String::from_utf8_lossy(&observed).contains("data: two") {
        let read = client.read(&mut buffer).unwrap();
        assert!(read > 0, "gateway closed before the second SSE event");
        observed.extend_from_slice(&buffer[..read]);
    }
    gateway.stop();
    crate::join_within(server, "the upstream server");
}

#[test]
fn gateway_preserves_large_uploads_and_downloads() {
    let upstream = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
    let upstream_port = upstream.local_addr().unwrap().port();
    let payload = vec![b'L'; 2 * 1024 * 1024];
    let expected = payload.clone();
    let server = thread::spawn(move || {
        let (mut stream, _) = upstream.accept().unwrap();
        let head = String::from_utf8(read_http_head(&mut stream)).unwrap();
        assert!(head.starts_with("POST /files/large?roundtrip=1 HTTP/1.1\r\n"));
        let content_length = head
            .lines()
            .find_map(|line| {
                line.to_ascii_lowercase()
                    .strip_prefix("content-length:")
                    .map(str::trim)
                    .and_then(|value| value.parse::<usize>().ok())
            })
            .unwrap();
        let mut body = vec![0_u8; content_length];
        stream.read_exact(&mut body).unwrap();
        assert_eq!(body, expected);
        write!(
            stream,
            "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
            body.len()
        )
        .unwrap();
        stream.write_all(&body).unwrap();
        stream.flush().unwrap();
    });
    let mut gateway = GatewayHandle::start(
        IpAddr::V4(Ipv4Addr::LOCALHOST),
        9,
        upstream_port,
        SharingMode::LocalNetwork,
    )
    .unwrap();
    let response = reqwest::blocking::Client::builder()
        .no_proxy()
        .build()
        .unwrap()
        .post(format!(
            "http://{}/_lemma/api/files/large?roundtrip=1",
            gateway.address
        ))
        .body(payload.clone())
        .send()
        .unwrap();
    assert_eq!(response.status(), reqwest::StatusCode::OK);
    assert_eq!(response.bytes().unwrap().as_ref(), payload.as_slice());
    gateway.stop();
    crate::join_within(server, "the upstream server");
}

#[test]
fn gateway_relays_websocket_upgrades_bidirectionally() {
    let upstream = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
    let upstream_port = upstream.local_addr().unwrap().port();
    let server = thread::spawn(move || {
        let (mut stream, _) = upstream.accept().unwrap();
        let head = String::from_utf8(read_http_head(&mut stream)).unwrap();
        assert!(head.starts_with("GET /socket HTTP/1.1\r\n"));
        assert!(head
            .to_ascii_lowercase()
            .contains("\r\nupgrade: websocket\r\n"));
        stream
            .write_all(
                b"HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\nUpgrade: websocket\r\nSec-WebSocket-Accept: test\r\n\r\n",
            )
            .unwrap();
        stream.flush().unwrap();
        let mut bytes = [0_u8; 5];
        stream.read_exact(&mut bytes).unwrap();
        stream.write_all(&bytes).unwrap();
        stream.flush().unwrap();
    });
    let mut gateway = GatewayHandle::start(
        IpAddr::V4(Ipv4Addr::LOCALHOST),
        upstream_port,
        9,
        SharingMode::Public,
    )
    .unwrap();
    let mut client = std::net::TcpStream::connect(gateway.address).unwrap();
    client
        .set_read_timeout(Some(Duration::from_secs(2)))
        .unwrap();
    client
        .write_all(
            b"GET /socket HTTP/1.1\r\nHost: shared.example\r\nConnection: Upgrade\r\nUpgrade: websocket\r\nSec-WebSocket-Key: dGVzdA==\r\nSec-WebSocket-Version: 13\r\n\r\n",
        )
        .unwrap();
    let head = String::from_utf8(read_http_head(&mut client)).unwrap();
    assert!(head.starts_with("HTTP/1.1 101"));
    client.write_all(b"hello").unwrap();
    let mut echoed = [0_u8; 5];
    client.read_exact(&mut echoed).unwrap();
    assert_eq!(&echoed, b"hello");
    gateway.stop();
    crate::join_within(server, "the upstream server");
}
