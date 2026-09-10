//! Files only this user may read, created that way rather than fixed up
//! afterwards.

use super::*;

// The implementations were here, and in five other modules, each with a
// slightly different guarantee -- this one never fsynced the directory the
// rename happened in, so a power cut could take back a write that had
// already reported success.
pub(crate) use lemma_private_file::{
    appending_log as private_appending_log, ensure_private as ensure_private_file,
    ensure_private_directory as set_private_directory, write_atomic as write_private_atomic,
};

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
