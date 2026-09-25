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

/// Quitting during the first boot must not turn a new disk into one that
/// "needs repair".
///
/// The guest formats only when the host says the disk is new. That used to be
/// "created by this boot", so a boot interrupted before `mkfs` left an empty
/// disk the next boot was not allowed to format.
#[cfg(target_os = "macos")]
#[test]
fn a_disk_stays_formattable_until_a_boot_has_mounted_it() {
    let root = tempdir().unwrap();
    let disk = root.path().join("data.raw");
    let never_mounted = root.path().join("data-disk-never-mounted");

    assert!(host_disk::prepare_data_disk(&disk, &never_mounted, 1024 * 1024).unwrap());
    // Quit before the guest formatted it: the next boot is still a first one.
    assert!(
        host_disk::prepare_data_disk(&disk, &never_mounted, 1024 * 1024).unwrap(),
        "an interrupted first boot must leave the disk formattable"
    );
    // A boot reached health, so the disk was mounted and may hold data.
    remove_if_present(&never_mounted).unwrap();
    assert!(
        !host_disk::prepare_data_disk(&disk, &never_mounted, 1024 * 1024).unwrap(),
        "a disk that has been mounted must never be offered for formatting"
    );
}

#[cfg(target_os = "macos")]
#[test]
fn the_vm_does_not_start_on_a_nearly_full_mac() {
    let error =
        host_disk::require_host_free_space(host_disk::HOST_FREE_SPACE_FLOOR - 1).unwrap_err();
    assert_eq!(error.kind(), io::ErrorKind::StorageFull);
    assert!(error.to_string().contains("free up space"), "{error}");
    host_disk::require_host_free_space(host_disk::HOST_FREE_SPACE_FLOOR).unwrap();
    assert!(host_disk::host_free_bytes(tempdir().unwrap().path()).unwrap() > 0);
}

/// The VM helper leads its own process group, so a signal aimed at locald's
/// group -- which would SIGTERM the guest off -- cannot reach it.
#[cfg(target_os = "macos")]
#[test]
fn the_vm_helper_is_spawned_into_its_own_process_group() {
    let source = include_str!("../macos.rs").replace("\r\n", "\n");
    let start = source.find("fn start_macos(").expect("start_macos exists");
    let spawn = &source[start..];
    let spawn = &spawn[..spawn.find(".spawn()?").expect("the helper is spawned")];
    assert!(spawn.contains(".process_group(0)"), "{spawn}");
}
