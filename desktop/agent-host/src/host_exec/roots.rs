//! Which folder each conversation's host workspace opened in, kept on this Mac.
//!
//! The Mac owns the disk, so it is the one that remembers. Lemma sends the
//! inputs a folder is named from (the conversation, its day and slug, and a
//! hint) on every `workspace.open`; this record is what makes a re-open --
//! after this process restarted, or a later run in the same conversation --
//! land in the folder the conversation already works in, whatever those inputs
//! say now. Lemma stores no such thing: `workspace.open`'s answer is the
//! authority, and this is where it comes from.
//!
//! A remembered folder is only reused while `admissible` still admits it, so
//! the record never widens what a workspace may be given: a folder the owner
//! has since unbound is not reused, and a default folder under the root base
//! that was deleted is made again. See docs/architecture/desktop-host-execution.md §5.
//!
//! The file sits beside `conversation-folders.json`, which is the shell's and
//! records the owner's choices; this one is the Agent Host's and records its
//! own. One process writes it (the single-instance lock), so a process-wide
//! mutex around each read-modify-write is enough.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, PoisonError};

/// Conversation id to the absolute root its workspace opened in.
type Roots = BTreeMap<String, String>;

static WRITER: Mutex<()> = Mutex::new(());

/// The record, or an error when it exists and cannot be read.
///
/// A missing record is an empty one. One that cannot be read (permissions,
/// an I/O failure) is an error rather than an empty record, because the next
/// `remember` would write the empty record over every conversation's folder.
/// One that does not parse is set aside beside itself and started afresh:
/// there is nothing in it to keep, and refusing every open for ever would be
/// worse.
fn read(store: &Path) -> std::io::Result<Roots> {
    let raw = match std::fs::read_to_string(store) {
        Ok(raw) => raw,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(Roots::new()),
        Err(error) => return Err(error),
    };
    match serde_json::from_str(&raw) {
        Ok(roots) => Ok(roots),
        Err(error) => {
            tracing::warn!(%error, store = %store.display(), "the record of conversation folders did not parse; setting it aside");
            std::fs::rename(store, store.with_extension("json.unreadable"))?;
            Ok(Roots::new())
        }
    }
}

/// The root this conversation's workspace last opened in, if one is recorded.
#[must_use]
pub fn remembered(store: &Path, conversation: uuid::Uuid) -> Option<PathBuf> {
    read(store)
        .inspect_err(|error| {
            tracing::warn!(%error, "could not read the record of conversation folders");
        })
        .ok()?
        .get(&conversation.to_string())
        .map(PathBuf::from)
        .filter(|path| path.is_absolute())
}

/// Record the root a conversation's workspace opened in.
///
/// Written to a temporary file, synced, and renamed over the record, so a
/// crash or a power cut leaves the old record or the new one, never half of
/// either and never an empty file.
pub fn remember(store: &Path, conversation: uuid::Uuid, root: &Path) -> std::io::Result<()> {
    let _writer = WRITER.lock().unwrap_or_else(PoisonError::into_inner);
    let mut roots = read(store)?;
    let key = conversation.to_string();
    let value = root.to_string_lossy().into_owned();
    if roots.get(&key) == Some(&value) {
        return Ok(());
    }
    roots.insert(key, value);
    lemma_private_file::write_atomic(store, &serde_json::to_vec_pretty(&roots)?)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_root_is_remembered_per_conversation_and_survives_a_rewrite() {
        let directory = tempfile::tempdir().unwrap();
        let store = directory.path().join("conversation-roots.json");
        let (first, second) = (uuid::Uuid::now_v7(), uuid::Uuid::now_v7());

        assert_eq!(remembered(&store, first), None);
        remember(&store, first, Path::new("/Users/o/lemma/c/2026-09-25/a")).unwrap();
        remember(&store, second, Path::new("/Users/o/code/app")).unwrap();
        remember(&store, first, Path::new("/Users/o/lemma/c/2026-09-25/a")).unwrap();

        assert_eq!(
            remembered(&store, first),
            Some(PathBuf::from("/Users/o/lemma/c/2026-09-25/a"))
        );
        assert_eq!(
            remembered(&store, second),
            Some(PathBuf::from("/Users/o/code/app"))
        );
    }

    #[test]
    fn an_unreadable_record_remembers_nothing() {
        let directory = tempfile::tempdir().unwrap();
        let store = directory.path().join("conversation-roots.json");
        std::fs::write(&store, "not json").unwrap();
        assert_eq!(remembered(&store, uuid::Uuid::now_v7()), None);
        // And is set aside and started afresh by the next open.
        let conversation = uuid::Uuid::now_v7();
        remember(&store, conversation, Path::new("/x")).unwrap();
        assert_eq!(remembered(&store, conversation), Some(PathBuf::from("/x")));
        assert!(store.with_extension("json.unreadable").exists());
    }

    #[cfg(unix)]
    #[test]
    fn a_record_that_cannot_be_read_is_not_written_over() {
        use std::os::unix::fs::PermissionsExt;
        let directory = tempfile::tempdir().unwrap();
        let store = directory.path().join("conversation-roots.json");
        let kept = uuid::Uuid::now_v7();
        remember(&store, kept, Path::new("/kept")).unwrap();
        std::fs::set_permissions(&store, std::fs::Permissions::from_mode(0o000)).unwrap();
        let refused = remember(&store, uuid::Uuid::now_v7(), Path::new("/new"));
        std::fs::set_permissions(&store, std::fs::Permissions::from_mode(0o600)).unwrap();
        assert!(refused.is_err());
        assert_eq!(remembered(&store, kept), Some(PathBuf::from("/kept")));
    }
}
