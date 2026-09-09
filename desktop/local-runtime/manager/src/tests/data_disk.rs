//! The sparse disk the guest's durable trees live on.

use super::*;

/// Discarding the data disk must unlink it, never shrink it.
///
/// `create_private_sparse_file` refuses a file whose length is not exactly
/// `DATA_DISK_BYTES`, so a reset that truncated instead of removing would
/// leave the installation permanently unable to start, with "managed data
/// disk has an unexpected size" and no way back. This asserts the property
/// directly: after discarding, the next start can create the disk again.
#[cfg(target_os = "macos")]
#[test]
fn a_discarded_data_disk_can_be_created_again() {
    let root = tempdir().unwrap();
    let disk = root.path().join("data.raw");
    create_private_sparse_file(&disk, 1024 * 1024).unwrap();
    assert!(disk.exists());

    // What `discard_data_disk` does to the file, without booting a VM.
    remove_if_present(&disk).unwrap();

    assert!(!disk.exists(), "the disk is unlinked, not truncated");
    create_private_sparse_file(&disk, 1024 * 1024)
        .expect("a fresh disk of the expected size is creatable after a reset");
    // A truncate-instead-of-remove reset would land here, and this is the
    // error the user would be stuck with forever.
    std::fs::File::options()
        .write(true)
        .open(&disk)
        .unwrap()
        .set_len(0)
        .unwrap();
    let error = create_private_sparse_file(&disk, 1024 * 1024).unwrap_err();
    assert_eq!(error.kind(), io::ErrorKind::InvalidData);
    assert!(error.to_string().contains("unexpected size"), "{error}");
}

#[cfg(target_os = "macos")]
#[test]
fn creates_private_sparse_data_disk_once() {
    use std::io::{Read, Seek, SeekFrom};

    let root = tempdir().unwrap();
    let disk = root.path().join("data.raw");

    create_private_sparse_file(&disk, 1024 * 1024).unwrap();
    let mut file = OpenOptions::new()
        .read(true)
        .write(true)
        .open(&disk)
        .unwrap();
    file.seek(SeekFrom::End(-5)).unwrap();
    file.write_all(b"state").unwrap();
    create_private_sparse_file(&disk, 1024 * 1024).unwrap();
    file.seek(SeekFrom::End(-5)).unwrap();
    let mut state = String::new();
    file.read_to_string(&mut state).unwrap();

    assert_eq!(state, "state");
    ensure_private_file(&disk).unwrap();
}

#[test]
fn cache_repair_signal_is_exact_and_does_not_match_generic_failures() {
    assert!(cache_repair_required(&io::Error::other(
        "container cache repair required"
    )));
    assert!(!cache_repair_required(&io::Error::other(
        "container engine unavailable"
    )));
}
