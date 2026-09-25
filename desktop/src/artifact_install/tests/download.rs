//! Resuming a partial download.

use super::*;

#[test]
fn accepts_only_exact_resume_content_ranges() {
    assert!(valid_content_range("bytes 12-99/100", 12, 100));
    assert!(!valid_content_range("bytes 11-99/100", 12, 100));
    assert!(!valid_content_range("bytes 12-100/100", 12, 100));
    assert!(!valid_content_range("bytes 12-99/*", 12, 100));
    assert!(!valid_content_range("items 12-99/100", 12, 100));
}

/// A dropped or stalled first-run download resumes on its own.
///
/// There was no retry at all: one reset connection over a gigabyte download
/// ended the setup with an error, and the user had to press Retry.
#[test]
fn a_dropped_download_is_resumed_until_the_backoff_runs_out() {
    let mut attempts = 0;
    let mut waited = Vec::new();
    let result = with_download_retries(
        &DOWNLOAD_RETRY_BACKOFF,
        |wait| waited.push(wait),
        || {
            attempts += 1;
            if attempts < 3 {
                Err(io::Error::new(io::ErrorKind::UnexpectedEof, "ended early"))
            } else {
                Ok(attempts)
            }
        },
    );
    assert_eq!(result.unwrap(), 3);
    assert_eq!(waited, DOWNLOAD_RETRY_BACKOFF[..2].to_vec());

    let mut attempts = 0;
    let error = with_download_retries(
        &DOWNLOAD_RETRY_BACKOFF,
        |_| {},
        || -> io::Result<()> {
            attempts += 1;
            Err(io::Error::new(io::ErrorKind::TimedOut, "stalled"))
        },
    )
    .unwrap_err();
    assert_eq!(error.kind(), io::ErrorKind::TimedOut);
    assert_eq!(attempts, DOWNLOAD_RETRY_BACKOFF.len() + 1);
}

#[test]
fn a_bad_artifact_or_a_refused_request_is_not_retried() {
    for kind in [
        io::ErrorKind::InvalidData,
        io::ErrorKind::InvalidInput,
        io::ErrorKind::StorageFull,
        io::ErrorKind::PermissionDenied,
    ] {
        let mut attempts = 0;
        let _ = with_download_retries(
            &DOWNLOAD_RETRY_BACKOFF,
            |_| {},
            || -> io::Result<()> {
                attempts += 1;
                Err(io::Error::new(kind, "no"))
            },
        );
        assert_eq!(attempts, 1, "{kind:?} must fail at once");
    }
}

#[test]
fn a_stalled_connection_is_noticed_in_a_minute_not_two_hours() {
    assert!(DOWNLOAD_STALL_TIMEOUT <= std::time::Duration::from_secs(120));
}
