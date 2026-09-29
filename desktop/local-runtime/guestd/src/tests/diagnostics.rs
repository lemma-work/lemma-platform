//! What a failed start can be asked, and what it may not reveal.

use super::*;

#[test]
fn sandbox_startup_error_includes_the_containers_own_last_words() {
    let root = tempdir().unwrap();
    let service = GuestService::new(
        FakeEngine::new(vec![
            output(
                true,
                &json!([{
                    "State": {
                        "Error": "OCI runtime failed",
                        "ExitCode": 126,
                        "OOMKilled": false,
                    }
                }])
                .to_string(),
            ),
            output(true, "starting\nfatal: runtime bootstrap failed\n"),
        ]),
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();

    let error = service.sandbox_startup_error(
        "lemma-sandbox-box-1",
        "sandbox runtime stopped before becoming ready",
    );

    assert_eq!(error.code, "guest_engine_failed");
    // The container's last few lines, not only its last one. The line that
    // says what went wrong is frequently not the final one -- PostgreSQL
    // refuses an unusable data directory with a paragraph, and a user was
    // shown its closing fragment, "discussion around this process, and
    // suggestions for how to do so.", as the whole explanation.
    assert_eq!(
        error.message,
        "sandbox runtime stopped before becoming ready: OCI runtime failed; container exited with code 126; starting fatal: runtime bootstrap failed"
    );
    assert_eq!(
        service.engine.commands.lock().unwrap().as_slice(),
        [
            vec!["inspect".to_owned(), "lemma-sandbox-box-1".to_owned()],
            vec![
                "logs".to_owned(),
                "--tail".to_owned(),
                "40".to_owned(),
                "lemma-sandbox-box-1".to_owned(),
            ],
        ]
    );
}

/// macOS had no way to ask the guest what it saw.
///
/// Windows drives `guest-diagnostics.sh` through `wsl.exe --exec`. A VZ
/// guest has no exec channel of any kind, so the same collection has to be
/// reachable as an operation -- and until it was, a macOS start that
/// failed with the guest up and its services broken left the serial
/// console as the only record, which says nothing about services.
#[test]
fn a_failed_start_can_ask_the_guest_what_it_saw() {
    let root = tempdir().unwrap();
    let service = GuestService::new(
        FakeEngine::new(Vec::new()),
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();

    let response = service.handle(GuestRequest {
        version: 1,
        operation: "diagnostics.guest".into(),
        parameters: json!({}),
        capability: None,
    });

    assert!(response.ok, "{:?}", response.error);
    let result = response.result.unwrap();
    assert!(
        result["text"].is_string(),
        "the host appends this to logs/guest.log as it stands: {result}"
    );
}

/// Collecting must not queue behind the thing that is stuck.
///
/// Diagnostics are asked for while a mutation is wedged. Serialising them
/// behind that mutation would make the collector wait for exactly the
/// operation it was called to explain.
#[test]
fn collecting_diagnostics_does_not_wait_on_the_mutation_lock() {
    assert!(is_observation("diagnostics.guest"));
}

/// One script, included by both ends, so they cannot drift.
///
/// The addresses line is the load-bearing one: `discover_guest_ip` reads
/// `ip -4 -o addr show`, and this collection is the only thing that can
/// say what the guest actually answered when that discovery picked wrong.
#[test]
fn the_guest_collects_what_the_windows_host_collects() {
    for fragment in [
        "ip -4 -o addr show",
        "ip -4 route show",
        "/usr/local/bin/nerdctl ps -a",
        "/var/log/lemma/",
        // Which name servers lookups went to, for a pull that failed on DNS.
        "resolvectl status",
        "cat /etc/resolv.conf",
        "set +e",
    ] {
        assert!(
            GUEST_DIAGNOSTICS.contains(fragment),
            "guest-diagnostics.sh stopped collecting {fragment}"
        );
    }
    assert!(
        !GUEST_DIAGNOSTICS.contains("journalctl"),
        "the guest runs with systemd=false, so there is no journal to read"
    );
}

#[test]
fn sandbox_diagnostics_exposes_only_sanitized_runtime_state() {
    let root = tempdir().unwrap();
    let service = GuestService::new(
        FakeEngine::new(vec![
            output(
                true,
                &json!([{
                    "Path": "tini",
                    "Args": ["--", "start-runtime"],
                    "Config": {
                        "Env": ["PRIVATE_TOKEN=never-return-this"],
                        "Entrypoint": null,
                        "Cmd": ["tini", "--", "start-runtime"],
                    },
                    "State": {
                        "Status": "exited",
                        "Running": false,
                        "ExitCode": 0,
                        "OOMKilled": false,
                        "Error": "",
                        "StartedAt": "2026-07-23T10:00:00Z",
                        "FinishedAt": "2026-07-23T10:00:02Z",
                    }
                }])
                .to_string(),
            ),
            output(true, "runtime stopped\n"),
        ]),
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();

    let diagnostics = service
        .sandbox_diagnostics(json!({"sandbox_id": "box-1"}))
        .unwrap();

    assert_eq!(diagnostics["state"]["status"], "exited");
    assert_eq!(diagnostics["state"]["exit_code"], 0);
    assert_eq!(diagnostics["process"]["path"], "tini");
    assert_eq!(
        diagnostics["process"]["cmd"],
        json!(["tini", "--", "start-runtime"])
    );
    assert_eq!(diagnostics["last_log"], "runtime stopped");
    assert!(!diagnostics.to_string().contains("PRIVATE_TOKEN"));
}

/// The servers a DNS failure names are the ones lookups actually went to.
///
/// resolved's own stub (127.0.0.53) is what resolv.conf says on the VZ guest
/// and tells nobody anything, so resolved is asked; WSL has no resolved, and
/// there resolv.conf, which Windows writes, is the answer.
#[test]
fn name_servers_come_from_resolved_or_else_resolv_conf() {
    let resolvectl = "Global: 127.0.0.2\nLink 2 (enp0s1): 192.168.64.1 fe80::1\nLink 3 (lo):\n";
    assert_eq!(
        parse_name_servers(Some(resolvectl), "nameserver 127.0.0.53\n"),
        ["127.0.0.2", "192.168.64.1", "fe80::1"]
    );
    assert_eq!(
        parse_name_servers(None, "# generated by WSL\nnameserver 10.255.255.254\n"),
        ["10.255.255.254"]
    );
    assert!(parse_name_servers(None, "nameserver 127.0.0.53\n").is_empty());
}

/// A DNS failure says so in words a person can act on, and keeps the phrase
/// locald classifies on.
#[test]
fn a_dns_failure_names_the_servers_and_what_the_mac_could_resolve() {
    let servers = ["127.0.0.2".to_owned(), "192.168.64.1".to_owned()];
    let blocked = dns_failure_hint(&servers, Some(false));
    assert!(
        blocked.starts_with("registry DNS lookup failed: "),
        "{blocked}"
    );
    assert!(
        blocked.contains("could not look up registry-1.docker.io"),
        "{blocked}"
    );
    assert!(
        blocked.contains("asked 127.0.0.2, 192.168.64.1"),
        "{blocked}"
    );
    assert!(
        blocked.contains("host DNS relay could not resolve it either"),
        "{blocked}"
    );

    let bypassed = dns_failure_hint(&servers, Some(true));
    assert!(bypassed.contains("relay can resolve it"), "{bypassed}");

    // WSL: no relay to speak of, and no server at all is its own finding.
    let wsl = dns_failure_hint(&[], None);
    assert!(wsl.contains("no name server is configured"), "{wsl}");
    assert!(!wsl.contains("relay"), "{wsl}");
}
