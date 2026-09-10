//! What the workspace is told when a run cannot be started.
//!
//! The reason used to be recovered by matching English substrings against the
//! error's message, after `redact_error` had already rewritten parts of it.
//! These guards are about the two ways that was wrong: a message that no
//! longer carries the words, and words that carry the wrong meaning.

use chrono::Utc;
use uuid::Uuid;

use super::{RefusedBecause, command_rejection, refuse};
use crate::protocol::{Command, CommandKind, RejectionCode};

fn start_command() -> Command {
    Command {
        command_id: Uuid::new_v4(),
        kind: CommandKind::StartRun,
        created_at: Utc::now(),
        expires_at: Utc::now() + chrono::Duration::minutes(1),
        run_id: Some(Uuid::new_v4()),
        lease_epoch: Some(1),
        payload: serde_json::Value::Null,
    }
}

/// Each reason has one code, and only the two that describe a host being
/// momentarily full or on its way out may be retried without a person.
#[test]
fn every_reason_reaches_the_workspace_as_its_own_code() {
    let expected = [
        (RefusedBecause::Draining, RejectionCode::Draining, true),
        (
            RefusedBecause::CommandExpired,
            RejectionCode::CommandExpired,
            false,
        ),
        (
            RefusedBecause::HarnessNotFound,
            RejectionCode::HarnessNotFound,
            false,
        ),
        (
            RefusedBecause::ConfigRevisionStale,
            RejectionCode::ConfigRevisionStale,
            false,
        ),
        (
            RefusedBecause::CapacityLost,
            RejectionCode::CapacityLost,
            true,
        ),
        (
            RefusedBecause::AdapterUnavailable,
            RejectionCode::AdapterUnavailable,
            false,
        ),
    ];
    for (because, code, retryable) in expected {
        let rejection = command_rejection(&start_command(), &refuse(because, "why"))
            .expect("a refused start is reported");
        assert_eq!(rejection.code, code, "{because:?}");
        assert_eq!(rejection.retryable, retryable, "{because:?}");
        assert_eq!(rejection.detail.as_deref(), Some("why"), "{because:?}");
    }
}

/// `redact_error` truncates at the first credential marker it finds, so a
/// message whose tail carried the meaning arrives at the classifier without
/// it. The manifest names the registry URL it could not install from, and a
/// registry URL is exactly where a `token=` turns up.
#[test]
fn a_refusal_survives_having_its_message_redacted() {
    let error = refuse(
        RefusedBecause::AdapterUnavailable,
        "could not install from https://registry.example/pkg?token=abc123 -- \
         the adapter is not on this computer",
    );

    let rejection =
        command_rejection(&start_command(), &error).expect("a refused start is reported");

    assert_eq!(rejection.code, RejectionCode::AdapterUnavailable);
    // The detail still goes through redaction: the reason travels beside the
    // message, it does not exempt it.
    let detail = rejection.detail.unwrap();
    assert!(detail.contains("[redacted]"), "{detail}");
    assert!(!detail.contains("abc123"), "{detail}");
    // And the truncation took the reason with it: nothing left in this string
    // says "adapter" at all.
    assert!(!detail.contains("adapter"), "{detail}");
}

/// The words are not specific enough to carry the meaning. An adapter whose
/// signing certificate has expired is an unavailable adapter, not a command
/// that arrived too late -- and the two differ in whether Lemma may retry.
#[test]
fn a_reason_is_not_guessed_from_words_that_mean_something_else() {
    let error = refuse(
        RefusedBecause::AdapterUnavailable,
        "the adapter's signing certificate expired on 2026-01-01",
    );

    let rejection =
        command_rejection(&start_command(), &error).expect("a refused start is reported");

    assert_eq!(rejection.code, RejectionCode::AdapterUnavailable);
    assert!(!rejection.retryable);
}

/// A failure the start path did not classify is a command the host cannot act
/// on, whatever its message happens to mention. A serde error naming the field
/// it could not read is the ordinary case, and `adapter_version` is a field.
#[test]
fn a_failure_nobody_classified_is_an_invalid_command() {
    let error = anyhow::anyhow!("missing field `adapter_version` at line 1 column 2");

    let rejection =
        command_rejection(&start_command(), &error).expect("a failed start is reported");

    assert_eq!(rejection.code, RejectionCode::InvalidCommand);
    assert!(!rejection.retryable);
}

/// Only a start is reported this way. A cancel or a permission resolution that
/// fails has no run to fence and nothing for Lemma to re-mint.
#[test]
fn only_a_start_is_reported_as_a_rejection() {
    for kind in [
        CommandKind::CancelRun,
        CommandKind::ResolvePermission,
        CommandKind::RefreshCredential,
    ] {
        let command = Command {
            kind,
            ..start_command()
        };
        assert!(
            command_rejection(&command, &refuse(RefusedBecause::Draining, "why")).is_none(),
            "{kind:?}"
        );
    }
}
