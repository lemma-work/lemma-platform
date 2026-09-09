//! What the shell is told, and what it must never contain.

use super::*;

#[test]
fn status_exposes_sidecar_lifecycle_paths() {
    let home = tempdir().unwrap();
    // The data directory has to be the sibling of the locald root, because
    // that is where a CLI-paired host keeps its config and journal.
    let supervisor = AgentHostSupervisor::discover(&home.path().join("locald"));
    let status = supervisor.status();
    assert_eq!(
        status["data_dir"],
        home.path().join("agent-host").to_string_lossy().as_ref()
    );
    assert_eq!(
        status["log"],
        home.path()
            .join("agent-host")
            .join("agent-host.log")
            .to_string_lossy()
            .as_ref()
    );
    assert_eq!(status["running"], false);
}

#[test]
fn reconcile_is_inert_without_a_sidecar() {
    let home = tempdir().unwrap();
    let mut supervisor = AgentHostSupervisor::discover(&home.path().join("locald"));
    supervisor.executable = None;

    supervisor.reconcile().expect("reconcile stays quiet");

    assert_eq!(supervisor.status()["available"], false);
    assert!(!home.path().join("agent-host").exists());
}

#[test]
fn detailed_status_reports_reachability_not_just_liveness() {
    // A live process says nothing about whether it can take work: a paired
    // host with a dead connection and an unpaired one both look "running".
    let target = json!({
        "target_id": "target-1",
        "host_id": "host-1",
        "name": "Work",
        "url": "https://api.lemma.work",
        "enabled": true,
        "journal": {
            "connection_state": "OFFLINE",
            "last_error": "connection refused",
            "active_runs": 2,
            "pending_events": 7,
            "last_connected_at": "2026-07-31T12:00:00Z"
        }
    });

    let summary = summarize_target(&target);
    assert_eq!(summary["host_id"], "host-1");
    assert_eq!(summary["connection_state"], "OFFLINE");
    assert_eq!(summary["last_error"], "connection refused");
    assert_eq!(summary["active_runs"], 2);
    assert_eq!(summary["pending_events"], 7);
}

#[test]
fn plain_http_is_opted_into_only_on_loopback() {
    use crate::local_domain::LocalDomain;
    let sslip = LocalDomain::parse(Some("sslip"));
    let is_loopback_http = |url: &str| is_loopback_http_for(url, &sslip);

    assert!(is_loopback_http("http://localhost:8710"));
    assert!(is_loopback_http("http://127.0.0.1:8710/api"));
    assert!(is_loopback_http("http://[::1]:8710"));
    assert!(is_loopback_http("http://localhost"));
    // The hostname Lemma Desktop serves its own workspace and API on.
    // Refusing this is refusing a desktop install the right to pair with
    // itself, which is exactly what it did.
    assert!(is_loopback_http("http://app.lemma.localhost:52502"));
    assert!(is_loopback_http(
        "http://apps.lemma.localhost:52502/internal"
    ));
    // And the hostname it serves itself on now. A shipped install resolves
    // to the loopback wildcard, because a browser derives no registrable
    // domain from `*.localhost` and a framed pod app needs one. This is the
    // same failure as the line above, one domain later: pairing died
    // silently and onboarding sat on "Connecting this computer" for ever.
    assert!(is_loopback_http("http://app.127.0.0.1.sslip.io:61624"));
    assert!(is_loopback_http(
        "http://apps.127.0.0.1.sslip.io:61624/internal"
    ));
    // A LAN or public address over plain HTTP stays refused.
    // Somebody else's sslip host is somebody else's machine, not ours.
    assert!(!is_loopback_http("http://app.10.0.0.7.sslip.io:61624"));
    assert!(!is_loopback_http(
        "http://app.127.0.0.1.sslip.io.evil:61624"
    ));
    assert!(!is_loopback_http("http://192.168.1.10:8710"));
    assert!(!is_loopback_http("http://localhost.evil.example:8710"));
    // ".localhost" must be the suffix, not a substring someone else owns.
    assert!(!is_loopback_http("http://localhost.attacker.example:8710"));
    assert!(!is_loopback_http("http://notlocalhost:8710"));
    assert!(!is_loopback_http("https://api.lemma.work"));
}

#[test]
fn a_failure_never_echoes_the_pairing_code() {
    // The host quotes its argument list back on error, and one of those
    // arguments is a live, single-use credential.
    let arguments = [
        "connect",
        "--url",
        "https://api.lemma.work",
        "--pairing-code",
        "s3cret-code",
    ];
    let detail = redact_secrets(
        "connect --pairing-code s3cret-code failed: already paired",
        &arguments,
    );
    assert!(!detail.contains("s3cret-code"));
    assert!(detail.contains("<pairing code>"));
    assert!(detail.contains("already paired"));
}
