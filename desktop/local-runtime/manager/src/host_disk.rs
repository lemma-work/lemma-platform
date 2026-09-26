//! The Mac side of the guest's data disk: whether it has ever been formatted,
//! and whether the Mac has room for it to grow.

use super::*;

/// Create the data disk if it is missing, and say whether the guest may format it.
///
/// "May format" used to mean "created by this very boot", so quitting during a
/// first boot -- before `lemma-mount-data` had run `mkfs` -- left a disk with
/// no filesystem that the next boot correctly refused to format and reported
/// as needing repair: a brand-new installation asking to be reset. The answer
/// now outlives the boot. `never_mounted` is written *before* the disk is
/// created, so no crash can leave a disk without it, and removed only once a
/// boot has reached health -- which requires the disk to be mounted -- so it
/// can never be present on a disk that holds user data. The guest still
/// formats only a disk with no filesystem signature at all.
pub(crate) fn prepare_data_disk(disk: &Path, never_mounted: &Path, bytes: u64) -> io::Result<bool> {
    if !disk.exists() {
        write_private_atomic(never_mounted, b"1\n")?;
    }
    create_private_sparse_file(disk, bytes)?;
    Ok(never_mounted.exists())
}

/// Below this, the VM does not start.
///
/// `data.raw` is sparse and grows as the guest writes. A Mac that runs out of
/// space underneath it fails the guest's writes -- Postgres's among them --
/// which is how a full disk becomes a damaged database rather than a clear
/// error. Refusing the boot is the recoverable version.
pub(crate) const HOST_FREE_SPACE_FLOOR: u64 = 2 * 1024 * 1024 * 1024;

pub(crate) fn require_host_free_space(available: u64) -> io::Result<()> {
    if available >= HOST_FREE_SPACE_FLOOR {
        return Ok(());
    }
    Err(io::Error::new(
        io::ErrorKind::StorageFull,
        format!(
            "This Mac has only {} MB of free disk space. Lemma needs at least {} GB free \
             to start its local services safely; free up space and try again.",
            available / (1024 * 1024),
            HOST_FREE_SPACE_FLOOR / (1024 * 1024 * 1024),
        ),
    ))
}

/// Bytes available to this user on the volume holding `path`.
#[cfg(unix)]
pub(crate) fn host_free_bytes(path: &Path) -> io::Result<u64> {
    use std::os::unix::ffi::OsStrExt;
    let path = std::ffi::CString::new(path.as_os_str().as_bytes())
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidInput, error))?;
    let mut stats = std::mem::MaybeUninit::<libc::statvfs>::uninit();
    // SAFETY: `path` is NUL-terminated and `stats` is written before it is read.
    if unsafe { libc::statvfs(path.as_ptr(), stats.as_mut_ptr()) } != 0 {
        return Err(io::Error::last_os_error());
    }
    // SAFETY: statvfs returned success, so the structure is initialised.
    let stats = unsafe { stats.assume_init() };
    Ok(u64::from(stats.f_bavail).saturating_mul(stats.f_frsize))
}
