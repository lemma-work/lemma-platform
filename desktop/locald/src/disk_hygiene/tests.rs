use super::*;

const DAY: Duration = Duration::from_secs(24 * 60 * 60);

#[test]
fn a_clean_start_on_the_migrated_release_removes_the_backup() {
    assert_eq!(
        backup_verdict(Some(Duration::ZERO), false, true),
        BackupVerdict::RemoveAfterCleanStart
    );
}

#[test]
fn the_backup_goes_after_three_days_at_the_latest() {
    assert_eq!(
        backup_verdict(Some(DAY * 2), false, false),
        BackupVerdict::Keep
    );
    assert_eq!(
        backup_verdict(Some(DAY * 3), false, false),
        BackupVerdict::RemoveExpired
    );
    assert_eq!(
        backup_verdict(Some(DAY * 30), false, false),
        BackupVerdict::RemoveExpired
    );
}

#[test]
fn an_age_nobody_can_read_is_not_old() {
    assert_eq!(backup_verdict(None, false, false), BackupVerdict::Keep);
}

/// A failed migration is the one case the copy exists for.
#[test]
fn a_failed_migration_keeps_the_backup_whatever_else_holds() {
    assert_eq!(
        backup_verdict(Some(DAY * 30), true, false),
        BackupVerdict::Keep
    );
    assert_eq!(
        backup_verdict(Some(DAY * 30), true, true),
        BackupVerdict::Keep
    );
}

#[test]
fn images_are_pruned_after_every_update_and_otherwise_weekly() {
    let now = 1_000_000_000;
    assert!(image_prune_due(None, "0.8.0", now));
    let record = HygieneRecord {
        last_image_prune_unix: now - DAY.as_secs(),
        last_image_prune_release: "0.8.0".into(),
    };
    assert!(!image_prune_due(Some(&record), "0.8.0", now));
    assert!(image_prune_due(Some(&record), "0.8.1", now));
    assert!(image_prune_due(
        Some(&record),
        "0.8.0",
        now + 7 * DAY.as_secs()
    ));
    // A clock that went backwards past the record does not postpone it.
    assert!(image_prune_due(
        Some(&record),
        "0.8.0",
        now - 10 * DAY.as_secs()
    ));
}

#[test]
fn the_record_round_trips_and_a_missing_one_reads_as_none() {
    let root = tempfile::tempdir().unwrap();
    assert_eq!(read_record(root.path()), None);
    let record = HygieneRecord {
        last_image_prune_unix: 42,
        last_image_prune_release: "0.8.0".into(),
    };
    write_record(root.path(), &record).unwrap();
    assert_eq!(read_record(root.path()), Some(record));
}

#[test]
fn sizes_read_the_way_finder_shows_them() {
    assert_eq!(format_bytes(0), "0 bytes");
    assert_eq!(format_bytes(999), "999 bytes");
    assert_eq!(format_bytes(1_500), "1.5 KB");
    assert_eq!(format_bytes(999_999), "1.0 MB");
    assert_eq!(format_bytes(16_033_103_872), "16 GB");
    assert_eq!(format_bytes(2_200_000_000), "2.2 GB");
    assert_eq!(format_bytes(5_000_000_000_000_000), "5000 TB");
}

#[test]
fn the_private_size_is_read_only_when_the_volume_returned_it() {
    let mut buffer = [0_u8; 64];
    buffer[0..4].copy_from_slice(&32_u32.to_ne_bytes());
    buffer[20..24].copy_from_slice(&0x8_u32.to_ne_bytes());
    buffer[24..32].copy_from_slice(&123_456_i64.to_ne_bytes());
    assert_eq!(parse_private_size(&buffer), Some(123_456));
    // Not in the returned set: a volume that cannot say.
    buffer[20..24].copy_from_slice(&0_u32.to_ne_bytes());
    assert_eq!(parse_private_size(&buffer), None);
    assert_eq!(parse_private_size(&[0; 8]), None);
}

#[test]
fn usage_reports_allocated_blocks_and_no_backup_when_there_is_none() {
    let root = tempfile::tempdir().unwrap();
    let disk = root.path().join(DATA_DISK);
    fs::create_dir_all(disk.parent().unwrap()).unwrap();
    // Sparse: long, with almost nothing written.
    let file = fs::File::create(&disk).unwrap();
    file.set_len(1 << 30).unwrap();
    drop(file);
    let usage = disk_usage(root.path(), SystemTime::now());
    let allocated = usage["data_disk"]["allocated_bytes"].as_u64().unwrap();
    assert!(
        allocated < 1 << 20,
        "a sparse file reports its blocks, not its length: {allocated}"
    );
    assert!(usage["update_backup"].is_null());
}

#[test]
fn a_backup_reports_its_size_age_and_expiry_and_removal_is_idempotent() {
    let root = tempfile::tempdir().unwrap();
    let backup = root.path().join(UPDATE_BACKUP);
    fs::create_dir_all(backup.parent().unwrap()).unwrap();
    fs::write(&backup, vec![7_u8; 1 << 20]).unwrap();
    let usage = disk_usage(root.path(), SystemTime::now());
    let reported = &usage["update_backup"];
    assert!(reported["allocated_bytes"].as_u64().unwrap() >= 1 << 20);
    let taken = reported["taken_at_unix"].as_u64().unwrap();
    assert_eq!(
        reported["expires_at_unix"].as_u64().unwrap(),
        taken + BACKUP_KEPT_AT_MOST.as_secs()
    );
    // On APFS nothing shares this file's blocks, so all of it is private.
    if cfg!(target_os = "macos") && reported["size_is_upper_bound"] == false {
        assert!(reported["reclaimable_bytes"].as_u64().unwrap() >= 1 << 20);
    }
    assert!(remove_backup(root.path()).unwrap().is_some());
    assert!(!backup.exists());
    assert_eq!(remove_backup(root.path()).unwrap(), None);
}

/// A clone shares its blocks: its allocated size is the whole file, and only
/// the private size says what deleting it would actually free.
#[cfg(target_os = "macos")]
#[test]
fn a_clone_reports_only_what_deleting_it_frees() {
    use std::os::unix::ffi::OsStrExt;
    let root = tempfile::tempdir().unwrap();
    let source = root.path().join("source");
    fs::write(&source, vec![1_u8; 4 << 20]).unwrap();
    let clone = root.path().join("clone");
    let from = std::ffi::CString::new(source.as_os_str().as_bytes()).unwrap();
    let to = std::ffi::CString::new(clone.as_os_str().as_bytes()).unwrap();
    // SAFETY: both paths are NUL-terminated and live for the call.
    if unsafe { libc::clonefile(from.as_ptr(), to.as_ptr(), 0) } != 0 {
        return; // not APFS (a tmpfs or HFS scratch volume): nothing to measure
    }
    assert!(allocated_bytes(&clone).unwrap() >= 4 << 20);
    let private = private_bytes(&clone).expect("APFS reports a clone's private size");
    assert!(
        private < 1 << 20,
        "an untouched clone frees ~nothing: {private}"
    );
    // Diverge it by a megabyte and that megabyte is now its own.
    use std::io::{Seek, Write};
    let mut file = fs::OpenOptions::new().write(true).open(&clone).unwrap();
    file.seek(io::SeekFrom::Start(0)).unwrap();
    file.write_all(&vec![2_u8; 1 << 20]).unwrap();
    file.sync_all().unwrap();
    drop(file);
    let diverged = private_bytes(&clone).unwrap();
    assert!(
        diverged >= 1 << 20,
        "diverged blocks are private: {diverged}"
    );
}
