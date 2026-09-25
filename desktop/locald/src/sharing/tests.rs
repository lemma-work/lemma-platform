//! The sharing guards.

use super::*;
use crate::sharing::gateway::*;
use std::io::{Read, Write};
use std::sync::mpsc;

/// How long a read that should succeed may take before the test fails instead
/// of hanging. Generous on purpose: it guards against a hang, not a slow
/// machine, and a loaded shared CI runner can stall a loopback socket for
/// seconds.
const HANG_GUARD: Duration = Duration::from_secs(30);

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
        release_receive.recv_timeout(HANG_GUARD).unwrap();
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
        None,
        "probe".into(),
    )
    .unwrap();
    gateway.set_open(true);
    let mut client = std::net::TcpStream::connect(gateway.address).unwrap();
    client.set_read_timeout(Some(HANG_GUARD)).unwrap();
    client
        .write_all(
            b"GET /_lemma/api/events?conversation=1 HTTP/1.1\r\nHost: shared.example\r\nX-Forwarded-For: attacker\r\nForwarded: for=attacker\r\nConnection: close\r\n\r\n",
        )
        .unwrap();
    first_received.recv_timeout(HANG_GUARD).unwrap();
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
        None,
        "probe".into(),
    )
    .unwrap();
    gateway.set_open(true);
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
        None,
        "probe".into(),
    )
    .unwrap();
    gateway.set_open(true);
    let mut client = std::net::TcpStream::connect(gateway.address).unwrap();
    client.set_read_timeout(Some(HANG_GUARD)).unwrap();
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

fn controller_at(root: &Path) -> Arc<SharingController> {
    SharingController::load(root, "http://app.lemma.localhost:3711".into(), 3711, 8711).unwrap()
}

/// An installation upgraded from before the setting starts invite-only.
///
/// Its `sharing.json` has no `who_can_join`, and the struct is
/// `deny_unknown_fields` but defaulted -- so the absent key is read as the
/// default rather than refusing the file and losing the tunnel it names.
#[test]
fn preferences_written_before_the_join_policy_read_as_invite_only() {
    let root = tempfile::tempdir().unwrap();
    fs::write(
        root.path().join("sharing.json"),
        serde_json::to_vec(&json!({
            "schema_version": SHARING_SCHEMA_VERSION,
            "last_provider": "ngrok",
            "selected_interface": "en0",
        }))
        .unwrap(),
    )
    .unwrap();

    let controller = controller_at(root.path());
    let snapshot = controller.snapshot(false);

    assert_eq!(snapshot.who_can_join, WhoCanJoin::InviteOnly);
    assert_eq!(
        snapshot.preferences.last_provider,
        Some(TunnelProvider::Ngrok)
    );
    assert_eq!(snapshot.public_confirmation, PUBLIC_WARNING_INVITE_ONLY);
}

#[test]
fn a_join_policy_change_is_saved_and_survives_a_restart() {
    let root = tempfile::tempdir().unwrap();
    let controller = controller_at(root.path());

    let (previous, live) = controller.begin_set_who_can_join(WhoCanJoin::Open).unwrap();
    assert_eq!(previous, WhoCanJoin::InviteOnly);
    assert!(
        live.is_none(),
        "nothing is shared, so there is nothing to re-apply"
    );
    controller.finish_who_can_join(None);
    assert!(!controller.transition_running.load(Ordering::Acquire));
    drop(controller);

    let reloaded = controller_at(root.path());
    let snapshot = reloaded.snapshot(false);
    assert_eq!(snapshot.who_can_join, WhoCanJoin::Open);
    assert_eq!(snapshot.public_confirmation, PUBLIC_WARNING_OPEN);
}

#[test]
fn a_join_policy_change_that_could_not_be_applied_is_undone() {
    let root = tempfile::tempdir().unwrap();
    let controller = controller_at(root.path());

    let (previous, _) = controller.begin_set_who_can_join(WhoCanJoin::Open).unwrap();
    controller.finish_who_can_join(Some(previous));
    drop(controller);

    assert_eq!(
        controller_at(root.path()).who_can_join(),
        WhoCanJoin::InviteOnly
    );
}

/// A policy change cannot land between an enable computing its environment
/// and committing -- the enable would then save a preference its running
/// backend does not have.
#[test]
fn a_join_policy_change_waits_for_a_sharing_transition() {
    let root = tempfile::tempdir().unwrap();
    let controller = controller_at(root.path());
    controller.begin_transition().unwrap();

    let error = controller
        .begin_set_who_can_join(WhoCanJoin::Open)
        .unwrap_err();

    assert_eq!(error.kind(), io::ErrorKind::WouldBlock);
    assert_eq!(controller.who_can_join(), WhoCanJoin::InviteOnly);
    controller.fail_transition("test complete".into());
}

/// The sentence confirmed before a public link is the one that will be true.
#[test]
fn the_public_confirmation_describes_the_policy_the_link_will_run_with() {
    let root = tempfile::tempdir().unwrap();
    let controller = controller_at(root.path());

    for (who_can_join, expected) in [
        (None, PUBLIC_WARNING_INVITE_ONLY),
        (Some(WhoCanJoin::InviteOnly), PUBLIC_WARNING_INVITE_ONLY),
        (Some(WhoCanJoin::Open), PUBLIC_WARNING_OPEN),
    ] {
        let error = controller
            .prepare_enable(&EnableSharingRequest {
                mode: SharingMode::Public,
                provider: Some(TunnelProvider::Ngrok),
                who_can_join,
                ..Default::default()
            })
            .unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::PermissionDenied);
        assert_eq!(error.to_string(), expected, "{who_can_join:?}");
    }
}

#[test]
fn local_network_warnings_say_who_can_create_an_account() {
    assert_eq!(
        local_join_warning(WhoCanJoin::Open),
        "Anyone on this network can create an account."
    );
    assert_eq!(
        local_join_warning(WhoCanJoin::InviteOnly),
        "Only people you invite can create an account."
    );
}

fn start_held_gateway(upstream_port: u16) -> GatewayHandle {
    GatewayHandle::start(
        IpAddr::V4(Ipv4Addr::LOCALHOST),
        upstream_port,
        upstream_port,
        SharingMode::Public,
        Some(TunnelProvider::Cloudflare),
        "the-probe-token".into(),
    )
    .unwrap()
}

fn gateway_get(gateway: &GatewayHandle, probe: Option<&str>) -> reqwest::blocking::Response {
    let client = reqwest::blocking::Client::builder()
        .no_proxy()
        .timeout(HANG_GUARD)
        .build()
        .unwrap();
    let mut request = client.get(format!("http://{}/runtime-config.js", gateway.address));
    if let Some(token) = probe {
        request = request.header(ACTIVATION_PROBE_HEADER, token);
    }
    request.send().unwrap()
}

/// A tunnel is live before the stack behind it has restarted into shared mode.
///
/// Until the hardened stack is verified and committed, a visitor must meet a
/// 503 rather than the local-mode stack -- DEBUG on, no rate limit, no ALTCHA.
/// Only locald's own activation check, carrying the per-activation token, gets
/// through, and the token is not forwarded.
#[test]
fn a_held_gateway_turns_visitors_away_until_it_is_opened() {
    let upstream = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
    let upstream_port = upstream.local_addr().unwrap().port();
    let (heads_send, heads) = mpsc::channel::<String>();
    let server = thread::spawn(move || {
        for _ in 0..2 {
            let (mut stream, _) = upstream.accept().unwrap();
            let head = String::from_utf8(read_http_head(&mut stream)).unwrap();
            heads_send.send(head).unwrap();
            stream
                .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
                .unwrap();
        }
    });
    let mut gateway = start_held_gateway(upstream_port);

    let visitor = gateway_get(&gateway, None);
    assert_eq!(visitor.status(), reqwest::StatusCode::SERVICE_UNAVAILABLE);
    assert_eq!(visitor.headers()["retry-after"], "5");
    let forged = gateway_get(&gateway, Some("a-guess"));
    assert_eq!(forged.status(), reqwest::StatusCode::SERVICE_UNAVAILABLE);

    let probe = gateway_get(&gateway, Some("the-probe-token"));
    assert_eq!(probe.status(), reqwest::StatusCode::OK);
    let head = heads.recv_timeout(HANG_GUARD).unwrap().to_ascii_lowercase();
    assert!(
        !head.contains(ACTIVATION_PROBE_HEADER),
        "the probe token was forwarded"
    );

    gateway.set_open(true);
    assert_eq!(
        gateway_get(&gateway, None).status(),
        reqwest::StatusCode::OK
    );
    heads.recv_timeout(HANG_GUARD).unwrap();

    // Held again for a restart while shared.
    gateway.set_open(false);
    assert_eq!(
        gateway_get(&gateway, None).status(),
        reqwest::StatusCode::SERVICE_UNAVAILABLE
    );
    gateway.stop();
    crate::join_within(server, "the upstream server");
}

/// Through a tunnel every visitor arrives from loopback; the backend's
/// per-client limits need the address the tunnel accepted them from.
#[test]
fn a_public_visitor_is_identified_by_the_tunnels_own_header() {
    let tunnel: SocketAddr = "127.0.0.1:50123".parse().unwrap();
    let mut headers = hyper::HeaderMap::new();
    headers.insert("cf-connecting-ip", HeaderValue::from_static("203.0.113.7"));
    headers.insert(
        "x-forwarded-for",
        HeaderValue::from_static("198.51.100.1, 203.0.113.9"),
    );
    let ip = |mode, provider, peer| client_address(mode, provider, &headers, peer);
    assert_eq!(
        ip(
            SharingMode::Public,
            Some(TunnelProvider::Cloudflare),
            tunnel
        )
        .to_string(),
        "203.0.113.7"
    );
    // ngrok appends its own view; what came before is the visitor's to write.
    assert_eq!(
        ip(SharingMode::Public, Some(TunnelProvider::Ngrok), tunnel).to_string(),
        "203.0.113.9"
    );
    // Not believed from anything but the tunnel's loopback connection, and not
    // on the LAN, where the peer is the visitor.
    let lan: SocketAddr = "192.168.1.44:50000".parse().unwrap();
    assert_eq!(
        ip(
            SharingMode::LocalNetwork,
            Some(TunnelProvider::Cloudflare),
            lan
        )
        .to_string(),
        "192.168.1.44"
    );
    assert_eq!(
        ip(SharingMode::Public, Some(TunnelProvider::Cloudflare), lan).to_string(),
        "192.168.1.44"
    );
    // A missing or malformed header falls back to the peer rather than trusting it.
    let mut junk = hyper::HeaderMap::new();
    junk.insert("cf-connecting-ip", HeaderValue::from_static("not-an-ip"));
    assert_eq!(
        client_address(
            SharingMode::Public,
            Some(TunnelProvider::Cloudflare),
            &junk,
            tunnel
        )
        .to_string(),
        "127.0.0.1"
    );
}
