//! What Lemma leaves on the Mac's disk, and when it takes it back.
//!
//! Three things grew without bound. The APFS clone of the data disk taken
//! before each migration (`data.raw.before-migration`) stayed for ever -- tens
//! of gigabytes once the live disk had diverged from it. Container images of
//! every earlier release stayed inside the guest. And the guest's freed blocks
//! stayed allocated in `data.raw`, a sparse file that only ever grew.
//!
//! The rules live here as pure functions, so each can be asserted without a
//! disk, a VM or a clock; `daemon::disk_ops` is what applies them.

use std::fs;
use std::io;
use std::path::Path;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

#[cfg(test)]
mod tests;

/// The longest a pre-migration backup is kept, whatever else happens.
pub(crate) const BACKUP_KEPT_AT_MOST: Duration = Duration::from_secs(3 * 24 * 60 * 60);

/// How often unused images are looked for when no update prompted it.
pub(crate) const IMAGE_PRUNE_INTERVAL: Duration = Duration::from_secs(7 * 24 * 60 * 60);

/// The live data disk and its pre-migration clone, relative to locald's root.
pub(crate) const DATA_DISK: &str = "runtime/macos/data.raw";
pub(crate) const UPDATE_BACKUP: &str = "runtime/macos/data.raw.before-migration";

/// When the last image prune ran, beside locald's other records.
const HYGIENE_RECORD: &str = "disk-hygiene.json";

/// What to do with the pre-migration backup.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum BackupVerdict {
    Keep,
    /// The release it was taken for migrated and then served: the backup is a
    /// copy of a database nobody is going back to.
    RemoveAfterCleanStart,
    /// Kept as long as it may be.
    RemoveExpired,
}

/// The rule for the backup. Pure.
///
/// - A migration recorded as failed keeps it: that is the case the copy exists
///   for, and restoring it is the support step that follows. Neither a clean
///   start nor the clock removes it then; the next successful migration
///   replaces it anyway.
/// - A clean start of the release the database was migrated to removes it.
/// - Otherwise it goes after three days. An age that cannot be read (`None`)
///   is not old: the cost of guessing wrong that way is disk, not data.
pub(crate) fn backup_verdict(
    age: Option<Duration>,
    migration_failed: bool,
    started_cleanly_on_migrated_release: bool,
) -> BackupVerdict {
    if migration_failed {
        return BackupVerdict::Keep;
    }
    if started_cleanly_on_migrated_release {
        return BackupVerdict::RemoveAfterCleanStart;
    }
    match age {
        Some(age) if age >= BACKUP_KEPT_AT_MOST => BackupVerdict::RemoveExpired,
        _ => BackupVerdict::Keep,
    }
}

/// When the backup was taken, from its inode change time.
///
/// Not the birth or modification time: `clonefile` copies both from the live
/// disk, so a clone taken today reports the disk's creation date. The change
/// time is set when the clone's inode is made and moves only forward
/// afterwards, so reading it can only make the backup look younger than it is
/// -- which delays removal and never hastens it.
#[cfg(unix)]
pub(crate) fn backup_taken_at(path: &Path) -> Option<SystemTime> {
    use std::os::unix::fs::MetadataExt;
    let metadata = fs::symlink_metadata(path).ok()?;
    let seconds = u64::try_from(metadata.ctime()).ok()?;
    Some(UNIX_EPOCH + Duration::from_secs(seconds))
}

#[cfg(not(unix))]
pub(crate) fn backup_taken_at(path: &Path) -> Option<SystemTime> {
    fs::symlink_metadata(path).ok()?.created().ok()
}

/// Bytes the file actually occupies: allocated blocks, not its length.
///
/// `data.raw` is sparse and always 24 GiB long, so `len()` would report the
/// same size for an empty disk and a full one.
#[cfg(unix)]
pub(crate) fn allocated_bytes(path: &Path) -> Option<u64> {
    use std::os::unix::fs::MetadataExt;
    let metadata = fs::symlink_metadata(path).ok()?;
    metadata.is_file().then(|| metadata.blocks() * 512)
}

#[cfg(not(unix))]
pub(crate) fn allocated_bytes(path: &Path) -> Option<u64> {
    let metadata = fs::symlink_metadata(path).ok()?;
    metadata.is_file().then(|| metadata.len())
}

/// Bytes deleting this file would give back right now.
///
/// An APFS clone shares every block it has not diverged on with its source,
/// so its allocated size overstates what removing it frees -- by up to the
/// whole disk. APFS reports the private part (`ATTR_CMNEXT_PRIVATESIZE`:
/// "bytes ... which would be freed immediately if the file were deleted").
/// `None` where that cannot be asked, and the caller says "up to".
#[cfg(target_os = "macos")]
pub(crate) fn private_bytes(path: &Path) -> Option<u64> {
    use std::os::unix::ffi::OsStrExt;
    let path = std::ffi::CString::new(path.as_os_str().as_bytes()).ok()?;
    let mut request = libc::attrlist {
        bitmapcount: libc::ATTR_BIT_MAP_COUNT,
        reserved: 0,
        commonattr: libc::ATTR_CMN_RETURNED_ATTRS,
        volattr: 0,
        dirattr: 0,
        fileattr: 0,
        forkattr: libc::ATTR_CMNEXT_PRIVATESIZE,
    };
    // u32 length, attribute_set_t (five u32), then the off_t.
    let mut buffer = [0_u8; 64];
    // SAFETY: `path` is NUL-terminated, `request` and `buffer` outlive the
    // call, and the size passed is the buffer's own.
    let status = unsafe {
        libc::getattrlist(
            path.as_ptr(),
            (&mut request as *mut libc::attrlist).cast(),
            buffer.as_mut_ptr().cast(),
            buffer.len(),
            libc::FSOPT_ATTR_CMN_EXTENDED | libc::FSOPT_NOFOLLOW,
        )
    };
    if status != 0 {
        return None;
    }
    parse_private_size(&buffer)
}

#[cfg(not(target_os = "macos"))]
pub(crate) fn private_bytes(_path: &Path) -> Option<u64> {
    None
}

/// Read `getattrlist`'s answer: present only when the returned-attributes set
/// says the volume supplied it (a non-APFS volume does not).
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
pub(crate) fn parse_private_size(buffer: &[u8]) -> Option<u64> {
    const PRIVATE_SIZE: u32 = 0x0000_0008;
    let word = |offset: usize| -> Option<u32> {
        Some(u32::from_ne_bytes(
            buffer.get(offset..offset + 4)?.try_into().ok()?,
        ))
    };
    let length = word(0)? as usize;
    // attribute_set_t: common, vol, dir, file, fork -- the extended common
    // attributes come back in the fork word.
    let returned_fork = word(4 + 16)?;
    if length < 32 || returned_fork & PRIVATE_SIZE == 0 {
        return None;
    }
    let size = i64::from_ne_bytes(buffer.get(24..32)?.try_into().ok()?);
    u64::try_from(size).ok()
}

/// When the last image prune ran, and for which release.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub(crate) struct HygieneRecord {
    pub(crate) last_image_prune_unix: u64,
    pub(crate) last_image_prune_release: String,
}

pub(crate) fn read_record(root: &Path) -> Option<HygieneRecord> {
    serde_json::from_slice(&fs::read(root.join(HYGIENE_RECORD)).ok()?).ok()
}

pub(crate) fn write_record(root: &Path, record: &HygieneRecord) -> io::Result<()> {
    let bytes = serde_json::to_vec(record).map_err(io::Error::other)?;
    let path = root.join(HYGIENE_RECORD);
    let staged = root.join(format!("{HYGIENE_RECORD}.tmp"));
    fs::write(&staged, bytes)?;
    fs::rename(staged, path)
}

/// Whether unused images should be looked for now. Pure.
///
/// After every update -- the release changed since the last prune, which is
/// when a whole set of images stopped being the current one -- and otherwise
/// weekly. A clock that went backwards past the last prune counts as due:
/// waiting for it to catch up could postpone a prune for years.
pub(crate) fn image_prune_due(
    record: Option<&HygieneRecord>,
    release: &str,
    now_unix: u64,
) -> bool {
    let Some(record) = record else {
        return true;
    };
    if record.last_image_prune_release != release {
        return true;
    }
    match now_unix.checked_sub(record.last_image_prune_unix) {
        Some(elapsed) => elapsed >= IMAGE_PRUNE_INTERVAL.as_secs(),
        None => true,
    }
}

pub(crate) fn unix_seconds(time: SystemTime) -> u64 {
    time.duration_since(UNIX_EPOCH)
        .map_or(0, |elapsed| elapsed.as_secs())
}

/// The numbers This Mac shows about locald's own files.
///
/// `update_backup` is null when there is none. Its `reclaimable_bytes` is what
/// deleting it frees when APFS can say; otherwise `size_is_upper_bound` is set
/// and the allocated size -- mostly shared with the live disk -- is the most it
/// could be.
pub(crate) fn disk_usage(root: &Path, now: SystemTime) -> Value {
    let backup_path = root.join(UPDATE_BACKUP);
    let backup = allocated_bytes(&backup_path).map(|allocated| {
        let private = private_bytes(&backup_path);
        let taken = backup_taken_at(&backup_path).map(unix_seconds);
        json!({
            "allocated_bytes": allocated,
            "reclaimable_bytes": private,
            "size_is_upper_bound": private.is_none(),
            "taken_at_unix": taken,
            "expires_at_unix": taken.map(|taken| taken + BACKUP_KEPT_AT_MOST.as_secs()),
        })
    });
    let record = read_record(root);
    json!({
        "data_disk": allocated_bytes(&root.join(DATA_DISK)).map(|allocated| json!({
            "allocated_bytes": allocated,
        })),
        "update_backup": backup,
        "last_image_prune_unix": record.map(|record| record.last_image_prune_unix),
        "measured_at_unix": unix_seconds(now),
    })
}

/// Remove the backup. Absent is success: the outcome asked for already holds.
pub(crate) fn remove_backup(root: &Path) -> io::Result<Option<u64>> {
    let path = root.join(UPDATE_BACKUP);
    let freed = private_bytes(&path).or_else(|| allocated_bytes(&path));
    match fs::remove_file(&path) {
        Ok(()) => Ok(freed),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error),
    }
}

/// Byte counts as a person reads them: decimal units, as Finder shows sizes.
pub(crate) fn format_bytes(bytes: u64) -> String {
    const UNITS: [&str; 4] = ["KB", "MB", "GB", "TB"];
    if bytes < 1000 {
        return format!("{bytes} bytes");
    }
    let mut value = bytes as f64 / 1000.0;
    let mut unit = 0;
    while value >= 999.95 && unit + 1 < UNITS.len() {
        value /= 1000.0;
        unit += 1;
    }
    if value >= 10.0 {
        format!("{value:.0} {}", UNITS[unit])
    } else {
        format!("{value:.1} {}", UNITS[unit])
    }
}
