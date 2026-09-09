use super::*;
use crate::diagnostics::*;

/// Diagnostics masks the secrets it has never seen.
///
/// Every operator secret lives in the OS credential vault, and the file
/// substitution this used to be could only mask values it had read off
/// disk -- so the AI provider key, the Slack and Telegram tokens and the
/// OAuth client secrets were unredactable by construction, while the
/// README promised bounded, redacted output.
#[test]
fn diagnostics_masks_credentials_it_has_never_seen() {
    let masked = mask_secret_shapes(
        [
            "provider rejected key sk-EXAMPLE-NOT-A-REAL-KEY-FOR-TESTS",
            "slack bot xoxb-EXAMPLE-NOT-A-REAL-SLACK-TOKEN responded 200",
            "GET /v1/models Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2ln",
            "session eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhYmMifQ.QWxhZGRpbjpvcGVu expired",
            "resend re_EXAMPLE-NOT-A-REAL-RESEND-KEY accepted",
        ]
        .join("\n"),
    );

    for leaked in [
        "sk-EXAMPLE-NOT-A-REAL-KEY-FOR-TESTS",
        "xoxb-EXAMPLE-NOT-A-REAL-SLACK-TOKEN",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhYmMifQ.QWxhZGRpbjpvcGVu",
        "re_EXAMPLE-NOT-A-REAL-RESEND-KEY",
    ] {
        assert!(!masked.contains(leaked), "{leaked} survived redaction");
    }
    assert!(
        !masked.contains("Bearer eyJ"),
        "an Authorization header carries the credential in full: {masked}"
    );
    // Still a diagnostic afterwards. A log masked so heavily that nobody
    // can read it does not help the person reading it.
    assert!(masked.contains("provider rejected key"));
    assert!(masked.contains("responded 200"));
    assert!(masked.contains("expired"));
}

/// Redaction does not reformat the log on the way past.
///
/// The masker used to rebuild each line with
/// `split_whitespace().join(" ")`, which redacts correctly and flattens
/// everything else: indentation, tabs, aligned columns. Diagnostics is
/// where somebody reads a Python traceback, and a traceback with no
/// indentation is a wall of text.
///
/// The neighbouring test could not see this -- every line in its fixture is
/// single-spaced, so collapsing runs of whitespace is a no-op on it.
#[test]
fn redaction_leaves_the_shape_of_a_traceback_alone() {
    let traceback = concat!(
        "Traceback (most recent call last):\n",
        "  File \"/app/main.py\", line 42, in handler\n",
        "    raise RuntimeError(\"boom\")\n",
        "\tRuntimeError: boom\n",
        "  key   sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdef\n",
    );

    let masked = mask_secret_shapes(traceback.to_owned());

    assert!(
        masked.contains("  File \"/app/main.py\", line 42, in handler"),
        "two-space indentation is what makes a traceback readable:\n{masked}",
    );
    assert!(
        masked.contains("    raise RuntimeError"),
        "four-space indentation is gone:\n{masked}",
    );
    assert!(masked.contains('\t'), "a tab is whitespace too:\n{masked}");
    assert!(
        masked.contains("  key   [redacted]"),
        "the run of spaces between a label and its value is alignment:\n{masked}",
    );
    // And the point of the exercise still holds.
    assert!(!masked.contains("sk-ABCDEFGHIJ"), "{masked}");
}

/// Redaction must not eat the things a log is read for.
#[test]
fn diagnostics_keeps_paths_versions_and_digests_readable() {
    let text = [
        "installed /Applications/Lemma.app/Contents/MacOS/lemma-locald",
        "release 0.7.0 pinned docker.io/pgvector/pgvector:0.8.3-pg18",
        "sha256:c8a919765f2ef63681329fa21021b830cd4d79d1165bdca730dd016014e4da84",
        "listening on http://app.lemma.localhost:49180",
    ]
    .join("\n");

    assert_eq!(
        mask_secret_shapes(text.clone()),
        text,
        "paths, versions and digests are not credentials"
    );
}

#[test]
fn a_growing_log_keeps_its_identity_so_the_tail_cursor_survives() {
    // The identity exists to answer "is this still the same file", and the
    // cursor is thrown away whenever it changes. Deriving it from anything
    // that grows with the log -- size, last write time -- compiles fine and
    // then re-sends the whole tail on every poll of an active log, i.e. it
    // breaks precisely when someone is watching a failure happen.
    use std::io::Write;

    let directory = tempfile::tempdir().expect("temp dir");
    let path = directory.path().join("backend.log");
    std::fs::write(&path, b"first\n").expect("seed the log");

    let before = diagnostic_file_identity(&File::open(&path).expect("open"));
    assert_ne!(
        before, "",
        "the identity should be readable on this platform"
    );

    let mut appended = std::fs::OpenOptions::new()
        .append(true)
        .open(&path)
        .expect("append to the log");
    appended.write_all(b"second\n").expect("write");
    appended.flush().expect("flush");
    drop(appended);

    let after = diagnostic_file_identity(&File::open(&path).expect("reopen"));
    assert_eq!(before, after, "appending to a log must not re-identify it");
}

#[test]
fn replacing_a_log_changes_its_identity_so_a_stale_cursor_is_dropped() {
    let directory = tempfile::tempdir().expect("temp dir");
    let path = directory.path().join("backend.log");
    std::fs::write(&path, b"old\n").expect("seed the log");
    let rotated = diagnostic_file_identity(&File::open(&path).expect("open"));

    std::fs::rename(&path, directory.path().join("backend.log.1")).expect("rotate");
    std::fs::write(&path, b"new\n").expect("fresh log");

    let fresh = diagnostic_file_identity(&File::open(&path).expect("reopen"));
    assert_ne!(
        rotated, fresh,
        "a rotated log is a different file and must reset the cursor"
    );
}
