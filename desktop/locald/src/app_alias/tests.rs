use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpListener;
use std::thread;

use hyper::header::{HeaderValue, HOST, LOCATION};
use hyper::{Body, Request, Response};
use tempfile::tempdir;

use super::proxy::{host_is_alias, rewrite_location, rewrite_request};
use super::*;

fn target() -> AliasTarget {
    AliasTarget {
        alias_host: "app.lemma.localhost".into(),
        alias_port: 61001,
        canonical_host: "orders.apps.lemma.localhost".into(),
        backend_port: 52414,
    }
}

#[test]
fn a_host_keeps_its_port_and_a_new_one_gets_its_own() {
    let root = tempdir().unwrap();
    let mut registry = AliasRegistry::load(root.path(), 3);
    assert_eq!(registry.assign("a.apps.lemma.localhost", 61001, 1), None);
    assert_eq!(registry.assign("b.apps.lemma.localhost", 61002, 2), None);
    // Asking again is a touch, not a second entry.
    assert_eq!(registry.assign("a.apps.lemma.localhost", 61001, 3), None);
    assert_eq!(registry.entries().len(), 2);
    assert_eq!(registry.port_for("a.apps.lemma.localhost"), Some(61001));
    assert_eq!(registry.port_for("b.apps.lemma.localhost"), Some(61002));
    assert_eq!(registry.port_for("c.apps.lemma.localhost"), None);
}

#[test]
fn the_registry_survives_a_restart() {
    let root = tempdir().unwrap();
    let mut registry = AliasRegistry::load(root.path(), 4);
    registry.assign("a.apps.lemma.localhost", 61001, 10);
    registry.assign("b.apps.lemma.localhost", 61002, 20);
    registry.save().unwrap();

    let reloaded = AliasRegistry::load(root.path(), 4);
    assert_eq!(reloaded.port_for("a.apps.lemma.localhost"), Some(61001));
    assert_eq!(reloaded.port_for("b.apps.lemma.localhost"), Some(61002));
    // Kept apart from network.json on purpose: that record denies unknown
    // fields, and an older locald that cannot read it reallocates the
    // workspace's own ports.
    assert!(root.path().join("app-aliases.json").is_file());
    assert!(!root.path().join("network.json").exists());
}

#[test]
fn a_full_registry_evicts_the_least_recently_used() {
    let root = tempdir().unwrap();
    let mut registry = AliasRegistry::load(root.path(), 2);
    registry.assign("a.apps.lemma.localhost", 61001, 10);
    registry.assign("b.apps.lemma.localhost", 61002, 20);
    // `a` is used again, so `b` is now the oldest.
    registry.assign("a.apps.lemma.localhost", 61001, 30);
    assert_eq!(
        registry.next_eviction().map(|entry| entry.host.as_str()),
        Some("b.apps.lemma.localhost")
    );
    let evicted = registry.assign("c.apps.lemma.localhost", 61003, 40);
    assert_eq!(
        evicted.map(|entry| entry.host),
        Some("b.apps.lemma.localhost".to_owned())
    );
    assert_eq!(registry.entries().len(), 2);
    assert_eq!(registry.port_for("b.apps.lemma.localhost"), None);
}

#[test]
fn a_damaged_registry_never_binds_two_apps_to_one_port() {
    let root = tempdir().unwrap();
    fs::write(
        root.path().join("app-aliases.json"),
        r#"{"schema_version":1,"aliases":[
            {"host":"a.apps.lemma.localhost","port":61001,"last_used_ms":3},
            {"host":"b.apps.lemma.localhost","port":61001,"last_used_ms":2},
            {"host":"a.apps.lemma.localhost","port":61002,"last_used_ms":1},
            {"host":"c.apps.lemma.localhost","port":80,"last_used_ms":1}
        ]}"#,
    )
    .unwrap();
    let registry = AliasRegistry::load(root.path(), 8);
    assert_eq!(registry.entries().len(), 1);
    assert_eq!(registry.port_for("a.apps.lemma.localhost"), Some(61001));

    fs::write(root.path().join("app-aliases.json"), "not json").unwrap();
    assert!(AliasRegistry::load(root.path(), 8).entries().is_empty());
}

#[test]
fn only_this_installations_app_urls_can_be_aliased() {
    let domain = LocalDomain::current();
    let parsed = parse_canonical_app_url(
        "http://orders.apps.lemma.localhost:52414/r?q=1#top",
        &domain,
        52414,
    )
    .unwrap();
    assert_eq!(parsed.host, "orders.apps.lemma.localhost");
    assert_eq!(parsed.rest, "/r?q=1#top");

    for refused in [
        // Another port is not this backend.
        "http://orders.apps.lemma.localhost:52413/",
        // The workspace itself, or the API.
        "http://app.lemma.localhost:52414/",
        // Somebody else's name.
        "http://orders.apps.lemma.localhost.evil:52414/",
        "http://example.com:52414/",
        // Not plain http, or carrying credentials.
        "https://orders.apps.lemma.localhost:52414/",
        "http://user:pw@orders.apps.lemma.localhost:52414/",
        // Two labels route nowhere in the backend.
        "http://a.b.apps.lemma.localhost:52414/",
        "not a url",
    ] {
        assert!(
            parse_canonical_app_url(refused, &domain, 52414).is_err(),
            "{refused} was accepted"
        );
    }
}

#[test]
fn the_backend_sees_the_canonical_host() {
    let mut request = Request::builder()
        .uri("/assets/app.js?v=2")
        .header(HOST, "app.lemma.localhost:61001")
        .header("x-forwarded-host", "evil.example")
        .header("forwarded", "host=evil.example")
        .body(Body::empty())
        .unwrap();
    rewrite_request(&mut request, &target()).unwrap();
    assert_eq!(request.headers()[HOST], "orders.apps.lemma.localhost:52414");
    assert_eq!(
        request.uri().to_string(),
        "http://127.0.0.1:52414/assets/app.js?v=2"
    );
    assert!(request.headers().get("x-forwarded-host").is_none());
    assert!(request.headers().get("forwarded").is_none());
}

#[test]
fn only_requests_for_this_alias_are_served() {
    let target = target();
    let host = |value: &str| HeaderValue::from_str(value).unwrap();
    assert!(host_is_alias(
        Some(&host("app.lemma.localhost:61001")),
        &target
    ));
    assert!(host_is_alias(
        Some(&host("APP.lemma.localhost:61001")),
        &target
    ));
    assert!(host_is_alias(Some(&host("127.0.0.1:61001")), &target));
    // A rebinding page arrives with its own name.
    assert!(!host_is_alias(
        Some(&host("attacker.example:61001")),
        &target
    ));
    assert!(!host_is_alias(
        Some(&host("app.lemma.localhost:61002")),
        &target
    ));
    assert!(!host_is_alias(Some(&host("app.lemma.localhost")), &target));
    assert!(!host_is_alias(None, &target));
}

#[test]
fn a_redirect_to_the_canonical_origin_stays_in_the_frame() {
    let rewrite = |location: &str| {
        let mut response = Response::builder()
            .status(302)
            .header(LOCATION, location)
            .body(Body::empty())
            .unwrap();
        rewrite_location(&mut response, &target());
        response.headers()[LOCATION].to_str().unwrap().to_owned()
    };
    assert_eq!(
        rewrite("http://orders.apps.lemma.localhost:52414/next?x=1"),
        "http://app.lemma.localhost:61001/next?x=1"
    );
    assert_eq!(
        rewrite("http://orders.apps.lemma.localhost:52414"),
        "http://app.lemma.localhost:61001"
    );
    // Everything else is left exactly as sent.
    for untouched in [
        "/relative",
        "http://app.lemma.localhost:52413/auth",
        "http://orders.apps.lemma.localhost:52414.evil/x",
        "http://other.apps.lemma.localhost:52414/",
    ] {
        assert_eq!(rewrite(untouched), untouched);
    }
}

/// A backend that answers each request with the Host it was sent.
fn echo_backend() -> (u16, thread::JoinHandle<()>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    let handle = thread::spawn(move || {
        for stream in listener.incoming().take(8) {
            let Ok(mut stream) = stream else { continue };
            let mut reader = BufReader::new(stream.try_clone().unwrap());
            let mut host = String::new();
            loop {
                let mut line = String::new();
                if reader.read_line(&mut line).unwrap_or(0) == 0 || line == "\r\n" {
                    break;
                }
                if let Some(value) = line.to_ascii_lowercase().strip_prefix("host:") {
                    host = value.trim().to_owned();
                }
            }
            let body = format!("host={host}");
            let _ = write!(
                stream,
                "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            );
        }
    });
    (port, handle)
}

fn get(port: u16, host: &str) -> String {
    let mut stream = TcpStream::connect(("127.0.0.1", port)).unwrap();
    write!(
        stream,
        "GET /x HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n"
    )
    .unwrap();
    let mut response = String::new();
    stream.read_to_string(&mut response).unwrap();
    response
}

#[test]
fn an_alias_forwards_to_the_app_and_keeps_its_port_across_a_restart() {
    let root = tempdir().unwrap();
    let (backend, _server) = echo_backend();
    let log: Arc<dyn Fn(&str) + Send + Sync> = Arc::new(|_| {});
    let canonical = format!("http://orders.apps.lemma.localhost:{backend}/reports?q=1");

    let service = AppAliasService::new(root.path(), 1, backend, Arc::clone(&log)).unwrap();
    let alias = service.alias_url(&canonical).unwrap();
    let port: u16 = alias
        .strip_prefix("http://app.lemma.localhost:")
        .and_then(|rest| rest.split('/').next())
        .unwrap()
        .parse()
        .unwrap();
    assert_eq!(
        alias,
        format!("http://app.lemma.localhost:{port}/reports?q=1")
    );
    // Asking again is the same alias.
    assert_eq!(service.alias_url(&canonical).unwrap(), alias);
    assert_eq!(service.listening_ports(), vec![port]);

    let response = get(port, &format!("app.lemma.localhost:{port}"));
    assert!(
        response.contains(&format!("host=orders.apps.lemma.localhost:{backend}")),
        "{response}"
    );
    let refused = get(port, &format!("attacker.example:{port}"));
    assert!(refused.starts_with("HTTP/1.1 421"), "{refused}");

    drop(service);
    // The next daemon brings the same port back.
    let service = AppAliasService::new(root.path(), 1, backend, log).unwrap();
    service.restore();
    assert_eq!(service.listening_ports(), vec![port]);
    assert_eq!(service.alias_url(&canonical).unwrap(), alias);
}

#[test]
fn a_full_service_closes_the_evicted_listener() {
    let root = tempdir().unwrap();
    let log: Arc<dyn Fn(&str) + Send + Sync> = Arc::new(|_| {});
    let service = AppAliasService::new(root.path(), 1, 52414, log).unwrap();
    let mut ports = Vec::new();
    for index in 0..ALIAS_CAPACITY {
        let url = service
            .alias_url(&format!("http://app{index}.apps.lemma.localhost:52414/"))
            .unwrap();
        ports.push(url);
    }
    assert_eq!(service.listening_ports().len(), ALIAS_CAPACITY);
    service
        .alias_url("http://one-more.apps.lemma.localhost:52414/")
        .unwrap();
    assert_eq!(service.listening_ports().len(), ALIAS_CAPACITY);
    let registry = AliasRegistry::load(root.path(), ALIAS_CAPACITY);
    assert_eq!(registry.port_for("app0.apps.lemma.localhost"), None);
    assert!(registry.port_for("one-more.apps.lemma.localhost").is_some());
}
