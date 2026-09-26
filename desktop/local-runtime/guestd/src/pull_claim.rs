//! One download of one image at a time, across every process in the guest.

use super::*;
use std::collections::HashSet;
use std::os::fd::AsRawFd;

/// Exclusive permission to fetch one image, for as long as this is held.
///
/// A lock on a file under the guest's own state, rather than only an entry in
/// a table in memory. Memory alone is the wrong place because on Windows the
/// guest is not one process: `wsl.exe --exec lemma-guestd request` starts a
/// fresh one per request and it exits with the reply. A table of what was in
/// flight was therefore empty at the start of every request, so each
/// `sandbox.ensure` began another download of the image the previous one was
/// still fetching -- several transfers of the same gigabyte competing for one
/// connection and one content store.
///
/// The kernel releases it, which matters more than it sounds. The holder is a
/// process the host can end by ending `wsl.exe`, and a claim that needed its
/// holder to tidy up would be left held for ever by exactly the failure it
/// exists to survive.
///
/// The lock is a POSIX record lock (`fcntl(F_SETLK)`), not a `flock`, and
/// that is the difference between a claim that is free when its holder lets go
/// and one that is free some time later. A `flock` belongs to the open file
/// description, and `fork` copies every descriptor into the child -- close-on-
/// exec closes them only when the child reaches `exec`. guestd forks all the
/// time, from every thread (`nerdctl`, `ctr`, `curl`, the engine itself), so a
/// fork that landed while a pull held its claim left a child holding that
/// claim after the pull had returned. The next pull of the same image was told
/// somebody else was downloading it, when nobody was: a failed download that
/// was retried started nothing. A record lock belongs to the process and is
/// never inherited across `fork`, so no child can keep one alive.
///
/// A record lock does not exclude the process that holds it, so the threads of
/// one guestd are excluded by `held_here` instead, and checked first. That
/// order also keeps the lock's one trap shut: a process loses its record lock
/// when it closes *any* descriptor for the file, so a second thread must never
/// open the file while the first holds the claim -- and it does not, because
/// `held_here` turns it away before it opens anything.
pub(crate) struct PullClaim {
    /// Held open, not read: closing the descriptor is what releases the lock.
    /// An `Option` only so `Drop` can close it before leaving `held_here`.
    file: Option<fs::File>,
    path: PathBuf,
}

/// Claim files this process holds a claim on right now.
fn held_here() -> &'static Mutex<HashSet<PathBuf>> {
    static HELD: OnceLock<Mutex<HashSet<PathBuf>>> = OnceLock::new();
    HELD.get_or_init(|| Mutex::new(HashSet::new()))
}

impl Drop for PullClaim {
    fn drop(&mut self) {
        // Close, then forget -- never the other way round. Forgetting first
        // would let another thread open the file and lock it (a no-op for a
        // process that already holds it), and this close would then take that
        // thread's lock away with ours.
        drop(self.file.take());
        held_here()
            .lock()
            .expect("pull claim table poisoned")
            .remove(&self.path);
    }
}

/// Take the claim on `image`, or report that somebody else has it.
///
/// `Ok(None)` is not a failure. It is the answer that lets a caller hand back
/// a retryable `image_pulling` instead of starting a second download.
///
/// It is answered at once, not after a grace period. A claim released by its
/// holder is free to the very next attempt, from this process or another,
/// because nothing but the holder can be holding it.
pub(crate) fn claim_pull(directory: &Path, image: &str) -> io::Result<Option<PullClaim>> {
    fs::create_dir_all(directory)?;
    // Best effort. An existing directory from an older release keeps whatever
    // mode it has, and a claim file is not secret -- it is empty.
    let _ = fs::set_permissions(directory, fs::Permissions::from_mode(0o700));
    let path = directory.join(claim_name(image));
    if !held_here()
        .lock()
        .expect("pull claim table poisoned")
        .insert(path.clone())
    {
        return Ok(None);
    }
    // From here the entry is ours, and `PullClaim`'s drop gives it back; on
    // the way out without a claim it has to be given back by hand.
    let release_entry = |path: &Path| {
        held_here()
            .lock()
            .expect("pull claim table poisoned")
            .remove(path);
    };
    let file = match OpenOptions::new()
        .create(true)
        .append(true)
        .mode(0o600)
        .open(&path)
    {
        Ok(file) => file,
        Err(error) => {
            release_entry(&path);
            return Err(error);
        }
    };
    match lock_whole_file(&file) {
        Ok(true) => Ok(Some(PullClaim {
            file: Some(file),
            path,
        })),
        // Another guestd has it. Closing our descriptor releases nothing of
        // theirs: a record lock belongs to the process that took it.
        Ok(false) => {
            drop(file);
            release_entry(&path);
            Ok(None)
        }
        Err(error) => {
            drop(file);
            release_entry(&path);
            Err(error)
        }
    }
}

/// An exclusive record lock on all of `file`, without waiting.
///
/// `Ok(false)` when another process holds it.
fn lock_whole_file(file: &fs::File) -> io::Result<bool> {
    // SAFETY: an all-zero `flock` is a valid value of a plain C struct; the
    // fields that matter are set below. Built by field rather than by literal
    // because the field order differs between Linux and macOS.
    let mut lock: libc::flock = unsafe { std::mem::zeroed() };
    lock.l_type = libc::F_WRLCK as libc::c_short;
    lock.l_whence = libc::SEEK_SET as libc::c_short;
    lock.l_start = 0;
    // Zero length is "to the end of the file, however long it grows".
    lock.l_len = 0;
    // SAFETY: `fcntl` is given a descriptor this scope borrows for the call
    // and a pointer to a `flock` that outlives it. The kernel releases the
    // lock when the descriptor is closed or the process ends.
    if unsafe { libc::fcntl(file.as_raw_fd(), libc::F_SETLK, &lock) } == 0 {
        return Ok(true);
    }
    let error = io::Error::last_os_error();
    // POSIX allows either for "somebody else holds a conflicting lock".
    match error.raw_os_error() {
        Some(libc::EAGAIN | libc::EACCES) => Ok(false),
        _ => Err(error),
    }
}

/// A filesystem-safe name for one image reference.
///
/// Readable at the front and hashed at the back. An OCI reference contains
/// `/` and `:` and runs to hundreds of characters, so it cannot be a filename
/// as it stands -- and truncating one to fit would let two images that share a
/// registry and repository share a claim, which is a download waiting on a
/// download of something else.
pub(crate) fn claim_name(image: &str) -> String {
    let readable: String = image
        .chars()
        .take(40)
        .map(|character| {
            if character.is_ascii_alphanumeric() || character == '.' || character == '-' {
                character
            } else {
                '_'
            }
        })
        .collect();
    format!("{readable}-{:016x}.pull", fnv1a(image))
}

/// FNV-1a, because the alternative is a dependency.
///
/// Nothing here is defending against a chosen collision: the input is an image
/// reference this guest was asked to fetch, and the worst a collision costs is
/// one download queueing behind another.
fn fnv1a(value: &str) -> u64 {
    let mut hash: u64 = 0xcbf2_9ce4_8422_2325;
    for byte in value.as_bytes() {
        hash ^= u64::from(*byte);
        hash = hash.wrapping_mul(0x0000_0100_0000_01b3);
    }
    hash
}
