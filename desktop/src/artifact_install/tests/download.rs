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
