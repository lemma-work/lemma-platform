//! Files only this user may read, created that way rather than fixed up
//! afterwards.

use super::*;

#[cfg(target_os = "macos")]
pub(crate) fn remove_if_present(path: &Path) -> io::Result<()> {
    match fs::remove_file(path) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error),
    }
}

#[cfg(target_os = "macos")]
/// Returns whether the disk was created by *this* call.
///
/// Only the host knows that. The guest sees a block device either way, and it
/// has to decide whether an unrecognised one is a brand-new disk to format or
/// user data it must not touch -- so the answer is written into the control
/// share for the boot script to read.
pub(crate) fn create_private_sparse_file(path: &Path, size: u64) -> io::Result<bool> {
    if path.exists() {
        if path.metadata()?.len() != size {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                format!(
                    "managed data disk has an unexpected size: {}",
                    path.display()
                ),
            ));
        }
        ensure_private_file(path)?;
        return Ok(false);
    }
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    use std::os::unix::fs::OpenOptionsExt;
    options.mode(0o600);
    let file = options.open(path)?;
    file.set_len(size)?;
    file.sync_all()?;
    ensure_private_file(path)?;
    Ok(true)
}

pub(crate) fn write_private_atomic(path: &Path, contents: &[u8]) -> io::Result<()> {
    let parent = path
        .parent()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "path has no parent"))?;
    fs::create_dir_all(parent)?;
    let temporary = path.with_extension(format!("tmp-{}", std::process::id()));
    let _ = fs::remove_file(&temporary);
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(&temporary)?;
    file.write_all(contents)?;
    file.sync_all()?;
    fs::rename(temporary, path)?;
    ensure_private_file(path)
}

pub(crate) fn set_private_directory(path: &Path) -> io::Result<()> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o700))?;
    }
    #[cfg(not(unix))]
    let _ = path;
    Ok(())
}

pub(crate) fn ensure_private_file(path: &Path) -> io::Result<()> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        let metadata = fs::symlink_metadata(path)?;
        if !metadata.file_type().is_file() || metadata.mode() & 0o077 != 0 {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                format!(
                    "private runtime file has unsafe permissions: {}",
                    path.display()
                ),
            ));
        }
    }
    #[cfg(not(unix))]
    let _ = path;
    Ok(())
}

pub(crate) fn private_appending_log(path: &Path) -> io::Result<std::fs::File> {
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

pub(crate) fn rotate_log(path: &Path, max_bytes: u64) -> io::Result<()> {
    if path
        .metadata()
        .is_ok_and(|metadata| metadata.len() >= max_bytes)
    {
        // Copy aside and truncate in place, rather than rename and let a new
        // file appear. The writer is a running child holding this handle: after
        // a rename it keeps writing into the rotated file, so the live log
        // stops growing and the rotated one never stops. Removing the previous
        // file first also fails outright on Windows if anything still has it
        // open. Truncating the file the writer already holds moves it back to
        // zero without either problem.
        fs::copy(path, path.with_extension("previous.log"))?;
        OpenOptions::new().write(true).open(path)?.set_len(0)?;
    }
    Ok(())
}
