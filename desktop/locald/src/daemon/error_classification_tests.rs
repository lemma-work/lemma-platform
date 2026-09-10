//! Turning a failure into a code the workspace acts on, and a log that says why.
//!
//! Split out of `tests.rs` under DES-09. One subject: every startup failure
//! leaves the daemon as a stable error code and a diagnostic log id, and both
//! are read by something in another process.

use tempfile::tempdir;

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
