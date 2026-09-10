//! State this launch derives, and what happens when it cannot be kept.

use super::*;

/// Write down the origin this launch derived, and carry on if it cannot be.
///
/// It used to be a `?`, which made an unwritable `state.json` -- a full disk, a
/// read-only volume, a permissions accident -- stop the daemon from starting at
/// all. Over a cache: the two URLs here are recomputed from the host pack's
/// reserved ports on every launch, which is what the comment above the
/// assignment says. The daemon that refused to start was refusing over
/// something it did not need.
///
/// Recorded rather than ignored. `healed` is where this daemon says what it had
/// to work around, and a state file it cannot write is worth an operator
/// hearing about even when nothing depended on it this time.
pub(super) fn remember_derived_origin(
    state: &StateSnapshot,
    path: &Path,
    healed: &mut Vec<String>,
) {
    if let Err(error) = state.persist(path) {
        healed.push(format!(
            "the local service state at {} could not be saved ({error}); Lemma is \
             running and will work this out again on the next start, but check \
             what is wrong with that file",
            path.display()
        ));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A state file that cannot be written does not stop the daemon.
    ///
    /// It used to: `Daemon::new` persisted the origin it had just derived with a
    /// `?`, so a full disk, a read-only volume or a permissions accident refused
    /// the whole start. Over a cache — those two URLs are recomputed from the host
    /// pack's reserved ports on every launch, which is exactly what the comment
    /// above them says.
    ///
    /// Said out loud, though. Working around something silently is how the next
    /// person inherits a mystery instead of a message.
    #[test]
    fn an_unwritable_state_file_is_reported_rather_than_fatal() {
        let root = tempfile::tempdir().unwrap();
        // A directory where the file belongs: the write fails for a reason that
        // has nothing to do with what is being written.
        let path = root.path().join("state.json");
        std::fs::create_dir_all(&path).unwrap();

        let mut healed = Vec::new();
        remember_derived_origin(&Default::default(), &path, &mut healed);

        assert_eq!(healed.len(), 1, "{healed:?}");
        assert!(
            healed[0].contains("could not be saved"),
            "the operator has to be told: {}",
            healed[0]
        );
        assert!(
            healed[0].contains("will work this out again"),
            "and told it is not fatal: {}",
            healed[0]
        );
    }

    /// And the ordinary case still writes it.
    #[test]
    fn a_writable_state_file_is_saved_without_comment() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("state.json");

        let mut healed = Vec::new();
        remember_derived_origin(&Default::default(), &path, &mut healed);

        assert!(healed.is_empty(), "nothing went wrong: {healed:?}");
        assert!(path.is_file(), "the state was persisted");
    }
}
