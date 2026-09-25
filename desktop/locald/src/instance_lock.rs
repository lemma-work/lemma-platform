//! One daemon per installation root, decided before anything is touched.
//!
//! The control socket used to be the only arbiter, and it is bound last:
//! `Daemon::new` first prepares the host pack and reclaims every process named
//! in the ledgers. A second `lemma-locald serve` -- a relaunch racing a slow
//! start, two app copies -- therefore stopped the running daemon's services and
//! only then learned, at bind, that it was the second one. An exclusive lock
//! on a file in the root answers the question first, and the OS releases it
//! however the holder exits, so a crash never leaves it stale.

use std::fs::{File, OpenOptions, TryLockError};
use std::io;
use std::path::Path;

pub const LOCK_FILE: &str = "locald.lock";

/// Take the installation lock, or say another daemon holds it.
///
/// The returned file is the lock; keep it alive for the life of the process.
pub fn claim(root: &Path) -> io::Result<File> {
    std::fs::create_dir_all(root)?;
    let file = OpenOptions::new()
        .create(true)
        .truncate(false)
        .write(true)
        .open(root.join(LOCK_FILE))?;
    match file.try_lock() {
        Ok(()) => Ok(file),
        Err(TryLockError::WouldBlock) => Err(io::Error::new(
            io::ErrorKind::AlreadyExists,
            "lemma-locald is already running",
        )),
        Err(TryLockError::Error(error)) => Err(error),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_second_daemon_is_refused_until_the_first_lets_go() {
        let root = tempfile::tempdir().unwrap();
        let first = claim(root.path()).expect("the first daemon takes the lock");
        let second = claim(root.path()).expect_err("a second daemon must be refused");
        assert_eq!(second.kind(), io::ErrorKind::AlreadyExists);
        drop(first);
        claim(root.path()).expect("the lock is free once the holder is gone");
    }
}
