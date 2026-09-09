//! Files only this user may read, written so a crash cannot leave one
//! half-written.
//!
//! There were six copies of this, one per module that needed it, and they had
//! drifted into six different guarantees. Two never fsynced the directory, so
//! the rename that made the file visible could be lost by a power cut that the
//! `Ok(())` had already promised through. One picked a temporary name from the
//! process id alone, so two daemons writing at once collided. One left the
//! result unchecked, so a file the user had made group-readable stayed that
//! way. The daemon's own `state.json` had none of it: no private mode, no
//! fsync, no unique name.
//!
//! So there is one, and it is the strongest of the six:
//!
//! * the parent directory exists;
//! * the temporary is created with `create_new` and a name no other writer can
//!   choose, at mode 0600 where the platform has modes;
//! * the contents are written and `fsync`ed before anything is renamed;
//! * the rename is `MOVEFILE_WRITE_THROUGH` on Windows, which is the only form
//!   that is durable there;
//! * the parent directory is `fsync`ed, so the rename survives too;
//! * and the result is checked, not assumed.

use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

/// Write `contents` to `path`, atomically, readable only by this user.
///
/// Either the previous contents are there or the new ones are; never a prefix
/// of either, and never nothing.
pub fn write_atomic(path: &Path, contents: &[u8]) -> io::Result<()> {
    let parent = path.parent().ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("{} has no parent directory", path.display()),
        )
    })?;
    fs::create_dir_all(parent)?;

    let temporary = temporary_beside(path);
    // `create_new`, so two writers cannot land on the same file even if the
    // name somehow repeats; the name itself already carries a counter.
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let outcome = (|| -> io::Result<()> {
        let mut file = options.open(&temporary)?;
        file.write_all(contents)?;
        file.sync_all()?;
        drop(file);
        replace(&temporary, path)?;
        sync_directory(parent)
    })();
    if outcome.is_err() {
        let _ = fs::remove_file(&temporary);
        return outcome;
    }
    ensure_private(path)
}

/// Replace `destination` with `source`, durably.
pub fn replace(source: &Path, destination: &Path) -> io::Result<()> {
    #[cfg(not(windows))]
    {
        fs::rename(source, destination)
    }
    // No delete-then-rename. `MoveFileExW` with `MOVEFILE_REPLACE_EXISTING`
    // already replaces the destination, so unlinking first bought nothing and
    // cost two things: a window in which the file simply did not exist, and a
    // second way to fail -- removing a file something has open, a virus scanner
    // moments after it was written, is a sharing violation.
    //
    // `MOVEFILE_WRITE_THROUGH` is what makes the rename itself durable on
    // Windows; `fs::rename` does not pass it.
    #[cfg(windows)]
    {
        use std::os::windows::ffi::OsStrExt;
        use windows_sys::Win32::Storage::FileSystem::{
            MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH,
        };

        let wide =
            |path: &Path| -> Vec<u16> { path.as_os_str().encode_wide().chain(Some(0)).collect() };
        let source = wide(source);
        let destination = wide(destination);
        // SAFETY: both strings are NUL-terminated and outlive the call, and
        // the return value is checked.
        let result = unsafe {
            MoveFileExW(
                source.as_ptr(),
                destination.as_ptr(),
                MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
            )
        };
        if result == 0 {
            Err(io::Error::last_os_error())
        } else {
            Ok(())
        }
    }
}

/// Refuse a file anyone but this user can read.
///
/// Checked rather than corrected: a private file that is not private is a
/// question about how it got that way, and answering it by widening our own
/// mode and carrying on is how a leaked credential stays leaked.
pub fn ensure_private(path: &Path) -> io::Result<()> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        let metadata = fs::symlink_metadata(path)?;
        if !metadata.file_type().is_file() || metadata.mode() & 0o077 != 0 {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                format!("{} is readable by more than this user", path.display()),
            ));
        }
    }
    #[cfg(not(unix))]
    let _ = path;
    Ok(())
}

/// Narrow a file's mode to this user, and refuse anything that is not a file.
///
/// The repairing counterpart to `ensure_private`, and the difference between
/// them is deliberate. Some of these files are ones a person can legitimately
/// end up owning with the wrong mode: `cp -R` instead of `cp -Rp` lands 0644
/// on a whole state directory, and refusing there was permanently fatal, and
/// silent. Where the exposure itself is the question -- a secret, a
/// credential -- `ensure_private` refuses instead.
///
/// A symlink or a directory is refused either way. That is not a permissions
/// accident, and following one would be the bug.
pub fn make_private(path: &Path) -> io::Result<()> {
    let metadata = fs::symlink_metadata(path)?;
    if !metadata.file_type().is_file() {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            format!("{} is not a regular file", path.display()),
        ));
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::{MetadataExt, PermissionsExt};
        if metadata.mode() & 0o077 != 0 {
            fs::set_permissions(path, fs::Permissions::from_mode(0o600))?;
        }
    }
    Ok(())
}

/// A directory only this user may enter.
pub fn ensure_private_directory(path: &Path) -> io::Result<()> {
    fs::create_dir_all(path)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o700))?;
    }
    Ok(())
}

/// A log only this user may read, opened for appending.
pub fn appending_log(path: &Path) -> io::Result<File> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let mut options = OpenOptions::new();
    options.create(true).append(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    options.open(path)
}

/// A name beside `path` that no other writer will choose.
///
/// The process id is not enough on its own: one process writing the same file
/// twice at once -- two threads persisting state, which is exactly what the
/// daemon does -- picks the same name both times, and `create_new` then fails
/// the second one for a reason that reads like a permissions problem.
fn temporary_beside(path: &Path) -> PathBuf {
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    let sequence = COUNTER.fetch_add(1, Ordering::Relaxed);
    let stem = path.file_name().map_or_else(
        || "file".to_owned(),
        |name| name.to_string_lossy().into_owned(),
    );
    let parent = path.parent().unwrap_or_else(|| Path::new("."));
    parent.join(format!(
        ".{stem}.{}-{nonce}-{sequence}.tmp",
        std::process::id()
    ))
}

/// Make the rename durable as well as the contents.
///
/// A file whose bytes are on the disk but whose directory entry is not is a
/// file that is not there. Best-effort on the platforms where a directory
/// cannot be opened for this.
#[cfg_attr(
    not(unix),
    expect(
        clippy::unnecessary_wraps,
        reason = "the unix arm returns a real result; the signature is one shape"
    )
)]
fn sync_directory(path: &Path) -> io::Result<()> {
    #[cfg(unix)]
    {
        File::open(path)?.sync_all()
    }
    #[cfg(not(unix))]
    {
        let _ = path;
        Ok(())
    }
}

#[cfg(test)]
mod tests;
