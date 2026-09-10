//! Each service's log, and the size it is kept to.

use super::*;

/// Ceiling for one service's log before it is rotated, and therefore half the
/// most it can occupy: the rotated copy sits beside it at the same size.
pub(crate) const SERVICE_LOG_MAX_BYTES: u64 = 5 * 1024 * 1024;

/// How often a running service's log is measured. Long enough to be free, short
/// enough that a service logging hard cannot get far past the ceiling between
/// checks.
pub(crate) const SERVICE_LOG_ROTATE_INTERVAL: Duration = Duration::from_secs(30);

pub(crate) fn process_log(log_dir: &Path, id: &str) -> io::Result<File> {
    let path = log_dir.join(format!("{id}.log"));
    rotate_log(&path, SERVICE_LOG_MAX_BYTES)?;
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
