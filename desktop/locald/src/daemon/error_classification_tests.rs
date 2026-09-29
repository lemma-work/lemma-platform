//! Turning a failure into a code the workspace acts on, and a log that says why.
//!
//! Split out of `tests.rs` under DES-09. One subject: every startup failure
//! leaves the daemon as a stable error code and a diagnostic log id, and both
//! are read by something in another process.

use tempfile::tempdir;

use super::dispatch::{explain_dns_failure, host_resolves_within};
use super::{error_diagnostic_source, runtime_operation_error_code};

#[test]
fn windows_runtime_errors_have_stable_user_action_codes() {
    assert_eq!(
        runtime_operation_error_code(
            "WSL 2 is required for Lemma's private runtime",
            "host-operation-failed"
        ),
        "wsl-required"
    );
    assert_eq!(
        runtime_operation_error_code(
            "Windows must restart to finish enabling WSL 2",
            "host-operation-failed"
        ),
        "wsl-reboot-required"
    );
    assert_eq!(
        runtime_operation_error_code(
            "Windows did not approve or complete WSL 2 setup",
            "runtime-prepare-failed"
        ),
        "wsl-setup-denied"
    );
    assert_eq!(
        runtime_operation_error_code("database failed", "host-operation-failed"),
        "host-operation-failed"
    );
}

/// Anything that says the marker phrase gets the code the reset button
/// keys on -- however many different detectors end up raising it.
#[test]
fn stranded_local_data_is_reported_with_the_code_the_reset_button_uses() {
    assert_eq!(
        runtime_operation_error_code(
            "this installation's secret was replaced, and anything encrypted with the \
                 previous one can no longer be read; local data must be reset",
            "host-operation-failed"
        ),
        "local-data-incompatible"
    );
    // The phrase is the whole contract, so a detector nobody has written
    // yet gets the same treatment for free.
    assert_eq!(
        runtime_operation_error_code(
            &format!(
                "the workspace database was created by PostgreSQL 16 and this release \
                     runs PostgreSQL 18; {}",
                crate::paths::DATA_RESET_MARKER
            ),
            "host-operation-failed"
        ),
        "local-data-incompatible"
    );
}

/// The marker is checked before the guest is touched.
///
/// Reaching `prepare_private_infra` would boot a VM to discover a failure
/// already known on disk, and the failure it would then report is an opaque
/// auth error rather than an offer to reset.
#[test]
fn a_recorded_data_reset_requirement_survives_until_it_is_cleared() {
    let root = tempdir().unwrap();
    assert!(crate::paths::data_reset_reason(root.path()).is_none());

    crate::paths::require_data_reset(root.path(), "the passwords were replaced").unwrap();
    let reason = crate::paths::data_reset_reason(root.path()).unwrap();
    assert_eq!(reason, "the passwords were replaced");
    assert_eq!(
        runtime_operation_error_code(
            &format!("{reason}; {}", crate::paths::DATA_RESET_MARKER),
            "host-operation-failed"
        ),
        "local-data-incompatible"
    );

    crate::paths::clear_data_reset(root.path()).unwrap();
    assert!(crate::paths::data_reset_reason(root.path()).is_none());
    // Clearing twice is how a reset that retries behaves; it must not fail.
    crate::paths::clear_data_reset(root.path()).unwrap();
}

#[test]
fn startup_errors_select_the_relevant_diagnostic_log() {
    // The second half of each pair is an id the shell has to serve. "vm" is
    // logs/runtime.log; "infrastructure" was not a source at all, so the tab it
    // selected could not be read.
    let kernel_error = "backend health gate: Linux guest kernel crashed";
    assert_eq!(
        error_diagnostic_source(kernel_error),
        ("infrastructure", "vm")
    );
    assert_eq!(
        runtime_operation_error_code(kernel_error, "host-operation-failed"),
        "guest-kernel-failed"
    );
    assert_eq!(
        error_diagnostic_source("frontend failed: EADDRINUSE"),
        ("frontend", "frontend")
    );
    assert_eq!(
        error_diagnostic_source("migrations setup exited"),
        ("migrations", "migrations")
    );
    assert_eq!(
        error_diagnostic_source("registry DNS lookup failed"),
        ("infrastructure", "vm")
    );
}

/// What guestd says when a pull could not resolve the registry.
const GUEST_DNS: &str = "core.images failed: pull registry-1.docker.io/library/postgres: lookup \
     registry-1.docker.io on 127.0.0.53:53: server misbehaving; registry DNS lookup failed: \
     Lemma's VM could not look up registry-1.docker.io (asked 127.0.0.2, 192.168.64.1)";

/// A guest that cannot resolve what this computer can is being blocked, most
/// often by a VPN or DNS filter, and the person is told which.
#[test]
fn a_guest_dns_failure_this_computer_does_not_share_is_a_blocked_vm() {
    let mac = explain_dns_failure(GUEST_DNS.to_owned(), false, || true);
    assert!(
        mac.starts_with("Your Mac can reach the internet, but Lemma's VM can't look up names."),
        "{mac}"
    );
    assert!(mac.contains("Cloudflare WARP"), "{mac}");
    assert!(mac.contains("press Try again"), "{mac}");
    // The raw failure is kept, for the log and for support.
    assert!(mac.ends_with(&format!("({GUEST_DNS})")), "{mac}");
    assert_eq!(
        runtime_operation_error_code(&mac, "host-operation-failed"),
        "guest-dns-blocked"
    );
    assert_eq!(error_diagnostic_source(&mac), ("infrastructure", "vm"));

    let windows = explain_dns_failure(GUEST_DNS.to_owned(), true, || true);
    assert!(
        windows.starts_with("Your PC can reach the internet"),
        "{windows}"
    );
    assert!(windows.contains("dnsTunneling"), "{windows}");
    assert!(!windows.contains("Mac"), "{windows}");
    assert_eq!(
        runtime_operation_error_code(&windows, "runtime-prepare-failed"),
        "guest-dns-blocked"
    );
}

/// When this computer cannot resolve the name either, the VM is not the
/// problem, and saying "a VPN is blocking Lemma" would send somebody looking
/// for one.
#[test]
fn a_dns_failure_this_computer_shares_is_no_network() {
    for windows in [false, true] {
        let offline = explain_dns_failure(GUEST_DNS.to_owned(), windows, || false);
        assert!(
            offline.starts_with("This computer can't reach the internet right now."),
            "{offline}"
        );
        assert_eq!(
            runtime_operation_error_code(&offline, "host-operation-failed"),
            "network-dns-failed"
        );
        assert_eq!(error_diagnostic_source(&offline), ("infrastructure", "vm"));
    }
}

/// Only a DNS failure costs a lookup on this computer, and a message is
/// explained once however many times it passes through.
#[test]
fn other_failures_are_left_alone_and_nothing_is_explained_twice() {
    let other = "backend health gate: timed out".to_owned();
    let untouched = explain_dns_failure(other.clone(), false, || {
        panic!("resolved a name for a failure that was not DNS")
    });
    assert_eq!(untouched, other);

    let once = explain_dns_failure(GUEST_DNS.to_owned(), false, || true);
    let twice = explain_dns_failure(once.clone(), false, || panic!("asked again"));
    assert_eq!(twice, once);

    // The guest's phrase alone, before any rewording, still opens the VM log,
    // even when the raw text around it names the backend.
    assert_eq!(
        error_diagnostic_source(&format!("backend start: {GUEST_DNS}")),
        ("infrastructure", "vm")
    );
}

/// The host's own lookup is bounded: it runs while the operation still holds
/// the lifecycle, and a hung resolver would leave every later operation busy.
#[test]
fn the_host_lookup_answers_within_its_deadline() {
    let started = std::time::Instant::now();
    assert!(host_resolves_within(
        "localhost",
        std::time::Duration::from_secs(3)
    ));
    assert!(started.elapsed() < std::time::Duration::from_secs(3));
    assert!(!host_resolves_within(
        "lemma-no-such-host.invalid",
        std::time::Duration::from_secs(3)
    ));
}
