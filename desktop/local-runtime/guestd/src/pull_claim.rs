//! One download of one image at a time, across every process in the guest.

use super::*;
use std::os::fd::AsRawFd;

/// Exclusive permission to fetch one image, for as long as this is held.
///
/// A `flock` on a file under the guest's own state, rather than an entry in a
/// table in memory. Memory is the wrong place because on Windows the guest is
/// not one process: `wsl.exe --exec lemma-guestd request` starts a fresh one
/// per request and it exits with the reply. A table of what was in flight was
/// therefore empty at the start of every request, so each `sandbox.ensure`
/// began another download of the image the previous one was still fetching --
/// several transfers of the same gigabyte competing for one connection and one
/// content store.
///
/// The kernel releases it, which matters more than it sounds. The holder is a
/// process the host can end by ending `wsl.exe`, and a claim that needed its
/// holder to tidy up would be left held for ever by exactly the failure it
/// exists to survive.
pub(crate) struct PullClaim {
    /// Held open, not read: closing the descriptor is what releases the lock.
    _file: fs::File,
}

/// Take the claim on `image`, or report that somebody else has it.
///
/// `Ok(None)` is not a failure. It is the answer that lets a caller hand back
/// a retryable `image_pulling` instead of starting a second download.
/// How long a claim attempt keeps trying before reporting that somebody has it.
///
/// Releasing a `flock` is closing a descriptor, and the kernel does not make
/// that visible to the next attempt instantly. Measured here, on macOS and on a
/// Linux CI runner: a claim taken immediately after its holder was dropped was
/// refused once and granted 489 microseconds later, on the very next try.
///
/// A download that is genuinely running holds its claim for minutes, so this
/// window only ever covers the instant after a release. It cannot delay the
/// answer that matters -- and without it, a guest that has just finished
/// fetching an image can tell the next caller the image is still downloading.
const CLAIM_SETTLES_WITHIN: Duration = Duration::from_millis(25);

pub(crate) fn claim_pull(directory: &Path, image: &str) -> io::Result<Option<PullClaim>> {
    fs::create_dir_all(directory)?;
    // Best effort. An existing directory from an older release keeps whatever
    // mode it has, and a claim file is not secret -- it is empty.
    let _ = fs::set_permissions(directory, fs::Permissions::from_mode(0o700));
    let path = directory.join(claim_name(image));
    let deadline = Instant::now() + CLAIM_SETTLES_WITHIN;
    loop {
        let file = OpenOptions::new()
            .create(true)
            .append(true)
            .mode(0o600)
            .open(&path)?;
        // SAFETY: `flock` is given a descriptor this scope owns, for the
        // duration of the call. The lock it takes is released by the kernel
        // when `file` is dropped, or when the process holding it ends.
        if unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } == 0 {
            return Ok(Some(PullClaim { _file: file }));
        }
        let error = io::Error::last_os_error();
        if error.raw_os_error() != Some(libc::EWOULDBLOCK) {
            return Err(error);
        }
        if Instant::now() >= deadline {
            return Ok(None);
        }
        // Yield rather than sleep: what this is waiting for is the kernel
        // finishing with a descriptor that is already closed, not another
        // process finishing a download.
        std::thread::yield_now();
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
