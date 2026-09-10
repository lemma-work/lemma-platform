//! The folder a conversation was bound to, chosen on this computer.
//!
//! An agent that runs natively on someone's machine should be able to work in
//! their project, not only in a directory Lemma invented under `~/lemma`. The
//! obstacle is not the filesystem, it is who is allowed to name a path:
//! `conversation_directory` accepts only a `/workspace/`-relative suffix
//! precisely so that a remote command cannot point an agent at `~/.ssh`.
//!
//! So the choice does not travel through the backend at all. The desktop shell
//! raises a native folder dialog, which is the person's consent, and records
//! the result in a file beside Agent Host's own configuration. This process
//! reads it. The path is only ever written by the app the person clicked in and
//! only ever read on the same machine; nothing about it crosses the network,
//! and a compromised backend gains no way to name a directory.
//!
//! A binding that no longer resolves is ignored rather than fatal. Folders get
//! moved, renamed and deleted, and a conversation whose folder is gone should
//! carry on in the ordinary place rather than refuse to run.

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use uuid::Uuid;

/// What the shell writes: conversation id to absolute path.
pub(crate) type Bindings = HashMap<String, String>;

pub(crate) fn read_bindings(store: &Path) -> Bindings {
    let Ok(raw) = std::fs::read_to_string(store) else {
        return Bindings::new();
    };
    serde_json::from_str::<Bindings>(&raw).unwrap_or_default()
}

/// The folder this conversation is bound to, if it is usable.
///
/// Every rejection is silent and falls back to the ordinary directory. The
/// checks are about what this process is willing to hand an agent as a working
/// directory, not about trusting the file: it is written by the shell on this
/// machine, and if that is compromised the agent's own binary is too.
pub(crate) fn bound_folder(bindings: &Bindings, conversation: Uuid) -> Option<PathBuf> {
    let raw = bindings.get(&conversation.to_string())?;
    usable_folder(Path::new(raw))
}

fn usable_folder(path: &Path) -> Option<PathBuf> {
    // Relative paths would resolve against whatever this process's cwd happens
    // to be, which is not a directory anybody chose.
    if !path.is_absolute() {
        return None;
    }
    // `symlink_metadata`, so a binding pointing at a link to somewhere else is
    // not silently followed -- the person picked a folder, and a link is not
    // the folder they were shown.
    let metadata = std::fs::symlink_metadata(path).ok()?;
    if !metadata.is_dir() || metadata.file_type().is_symlink() {
        return None;
    }
    Some(path.to_path_buf())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn bindings(pairs: &[(&str, &str)]) -> Bindings {
        pairs
            .iter()
            .map(|(key, value)| ((*key).to_owned(), (*value).to_owned()))
            .collect()
    }

    #[test]
    fn a_bound_folder_is_used_when_it_is_a_real_directory() {
        let temporary = tempfile::tempdir().expect("temp dir");
        let conversation = Uuid::now_v7();
        let store = bindings(&[(
            &conversation.to_string(),
            temporary.path().to_str().expect("utf-8 path"),
        )]);

        assert_eq!(
            bound_folder(&store, conversation).as_deref(),
            Some(temporary.path())
        );
    }

    #[test]
    fn a_conversation_nobody_bound_has_no_folder() {
        let temporary = tempfile::tempdir().expect("temp dir");
        let store = bindings(&[(
            &Uuid::now_v7().to_string(),
            temporary.path().to_str().expect("utf-8 path"),
        )]);

        assert_eq!(bound_folder(&store, Uuid::now_v7()), None);
    }

    /// Folders get moved, renamed and deleted. A conversation whose folder is
    /// gone carries on in the ordinary place rather than refusing to run.
    #[test]
    fn a_binding_that_no_longer_resolves_is_ignored_rather_than_fatal() {
        let conversation = Uuid::now_v7();
        for unusable in ["/definitely/not/here", "relative/folder", ""] {
            let store = bindings(&[(&conversation.to_string(), unusable)]);
            assert_eq!(bound_folder(&store, conversation), None, "{unusable}");
        }
    }

    #[test]
    fn a_file_is_not_a_working_directory() {
        let temporary = tempfile::tempdir().expect("temp dir");
        let file = temporary.path().join("notes.txt");
        std::fs::write(&file, b"x").expect("write");
        let conversation = Uuid::now_v7();
        let store = bindings(&[(
            &conversation.to_string(),
            file.to_str().expect("utf-8 path"),
        )]);

        assert_eq!(bound_folder(&store, conversation), None);
    }

    /// The person picked a folder in a dialog. A link is not the folder they
    /// were shown, so following it would put the agent somewhere else.
    #[cfg(unix)]
    #[test]
    fn a_symlink_is_not_followed_to_somewhere_nobody_chose() {
        let temporary = tempfile::tempdir().expect("temp dir");
        let real = temporary.path().join("real");
        std::fs::create_dir(&real).expect("create");
        let link = temporary.path().join("link");
        std::os::unix::fs::symlink(&real, &link).expect("symlink");
        let conversation = Uuid::now_v7();
        let store = bindings(&[(
            &conversation.to_string(),
            link.to_str().expect("utf-8 path"),
        )]);

        assert_eq!(bound_folder(&store, conversation), None);
    }

    /// The shell parks a choice under a reserved key while its conversation is
    /// still being created. It is looked up by UUID here, so the reserved key
    /// cannot be reached -- and a folder waiting for a conversation must not
    /// become the folder of some other one.
    #[test]
    fn the_shells_waiting_choice_is_not_a_conversations_folder() {
        let temporary = tempfile::tempdir().expect("temp dir");
        let store = bindings(&[("pending", temporary.path().to_str().expect("utf-8 path"))]);

        for _ in 0..8 {
            assert_eq!(bound_folder(&store, Uuid::now_v7()), None);
        }
        assert!(Uuid::parse_str("pending").is_err(), "and it is not an id");
    }

    #[test]
    fn a_store_that_is_missing_or_malformed_binds_nothing() {
        let temporary = tempfile::tempdir().expect("temp dir");
        assert!(read_bindings(&temporary.path().join("absent.json")).is_empty());

        let malformed = temporary.path().join("folders.json");
        std::fs::write(&malformed, b"{not json").expect("write");
        assert!(read_bindings(&malformed).is_empty());

        std::fs::write(&malformed, b"[\"a\", \"b\"]").expect("write");
        assert!(read_bindings(&malformed).is_empty());
    }

    #[test]
    fn a_store_the_shell_wrote_is_read_back() {
        let temporary = tempfile::tempdir().expect("temp dir");
        let store = temporary.path().join("folders.json");
        std::fs::write(&store, br#"{"abc":"/tmp/project"}"#).expect("write");

        assert_eq!(
            read_bindings(&store).get("abc").map(String::as_str),
            Some("/tmp/project")
        );
    }
}
