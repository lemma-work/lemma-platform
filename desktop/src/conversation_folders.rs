//! Binding a conversation to a folder on this computer.
//!
//! The agents run here, with the person's own tools and their own credentials,
//! so the useful working directory is usually a project they already have —
//! not a directory Lemma invented under `~/lemma`.
//!
//! What stops the workspace page simply sending a path is deliberate: Agent
//! Host accepts a `/workspace/`-relative suffix and nothing else, so that
//! nothing reachable over the network can point an agent at `~/.ssh`. Keeping
//! that, and still allowing a real folder, means the *shell* has to be the one
//! that asks. A native folder dialog is a thing only the person in front of the
//! machine can answer, and their answer is the consent.
//!
//! So the path never travels: it is written here, beside Agent Host's own
//! configuration, and read there. The backend is not told and does not need to
//! be.

use std::collections::BTreeMap;
use std::path::PathBuf;
use std::sync::Mutex;

use tauri::{AppHandle, Webview};
use tauri_plugin_dialog::DialogExt;
use uuid::Uuid;

use crate::agent_host_ui::require_agent_host_caller;
use crate::connection::current_mode;
use crate::shell_paths::locald_root;

/// Where the shell writes and Agent Host reads. Derived the same way on both
/// sides rather than configured, so the two cannot disagree about the location.
pub(crate) fn bindings_path() -> PathBuf {
    let root = locald_root();
    root.parent()
        .unwrap_or(&root)
        .join("agent-host")
        .join("conversation-folders.json")
}

type Bindings = BTreeMap<String, String>;

/// One writer at a time, for the whole read-modify-write.
///
/// Two of these commands can run at once -- a double click, or two windows --
/// and each read the same map, changed its own entry and renamed its file over
/// the other's. The later rename won and the other binding was gone. The
/// temporary file was shared too, so the two writers were also overwriting each
/// other's half-written bytes before either rename.
static WRITER: Mutex<()> = Mutex::new(());

/// Where a choice waits while its conversation is still being created.
///
/// The folder is picked in the composer, before the first message, so there is
/// no conversation to key it to yet. The alternative was handing the path back
/// to the page and letting it name the folder when it created the conversation
/// -- which is the one thing this design exists to avoid. It waits here
/// instead, under a key no conversation id can collide with.
///
/// Keyed per composer rather than globally. One shared slot meant a folder
/// chosen in a composer that was then abandoned was still sitting there when
/// the next new conversation started, and that conversation adopted it: a
/// directory the person chose for something else, and had walked away from.
const PENDING_PREFIX: &str = "pending:";

/// The slot a composer's waiting choice lives in.
///
/// The id is opaque and comes from the page, so it is prefixed rather than used
/// as a key directly -- a caller passing a conversation's own id would otherwise
/// be writing that conversation's binding.
fn pending_key(pending_id: &str) -> Result<String, String> {
    let trimmed = pending_id.trim();
    if trimmed.is_empty()
        || trimmed.len() > 128
        || !trimmed
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '-')
    {
        return Err("that is not a pending selection".into());
    }
    Ok(format!("{PENDING_PREFIX}{trimmed}"))
}

fn read_bindings() -> Bindings {
    std::fs::read_to_string(bindings_path())
        .ok()
        .and_then(|raw| serde_json::from_str::<Bindings>(&raw).ok())
        .unwrap_or_default()
}

/// Apply a change to the bindings under the writer lock.
fn update_bindings<T>(change: impl FnOnce(&mut Bindings) -> T) -> Result<T, String> {
    let _writing = WRITER.lock().unwrap_or_else(|poisoned| {
        // A panicking writer leaves the file as it was: the lock guards a
        // read-modify-write, not any state of ours.
        poisoned.into_inner()
    });
    let mut bindings = read_bindings();
    let answer = change(&mut bindings);
    write_bindings(&bindings)?;
    Ok(answer)
}

fn write_bindings(bindings: &Bindings) -> Result<(), String> {
    let path = bindings_path();
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|error| format!("could not prepare the Agent Host directory: {error}"))?;
    }
    let body = serde_json::to_vec_pretty(bindings)
        .map_err(|error| format!("could not record the folder: {error}"))?;
    // Written whole through a temporary file: Agent Host reads this on every
    // run, and a half-written file would be a conversation that silently loses
    // its folder rather than one that keeps the previous answer.
    // Named for this write, not for the file: a shared temporary is a second
    // way for two writers to corrupt each other.
    let temporary = path.with_extension(format!("{}.tmp", Uuid::new_v4()));
    std::fs::write(&temporary, &body)
        .map_err(|error| format!("could not record the folder: {error}"))?;
    std::fs::rename(&temporary, &path)
        .map_err(|error| format!("could not record the folder: {error}"))
}

/// Refuse unless this install is the local one.
///
/// The workspace capability covers a remote origin -- `https://lemma.work` is
/// in its list -- so without this a hosted page could raise a native folder
/// dialog on somebody's machine and learn paths from it. A bound folder only
/// means anything where the agents run locally anyway.
fn require_local_install(app: &AppHandle) -> Result<(), String> {
    if current_mode(app) != "local" {
        return Err("a conversation folder belongs to a local install".into());
    }
    Ok(())
}

/// The conversation's slot, or the composer's while it has no conversation.
fn slot(conversation_id: Option<&str>, pending_id: Option<&str>) -> Result<String, String> {
    match (conversation_id, pending_id) {
        (Some(id), _) => conversation_key(id),
        (None, Some(pending)) => pending_key(pending),
        (None, None) => Err("no conversation and no pending selection".into()),
    }
}

fn conversation_key(conversation_id: &str) -> Result<String, String> {
    Uuid::parse_str(conversation_id)
        .map(|id| id.to_string())
        .map_err(|_| "that is not a conversation".to_string())
}

/// The folder this conversation is bound to, as far as the shell knows.
#[tauri::command]
/// Off the UI thread: it reads a file, and a synchronous command is dispatched
/// on the main thread, where any wait freezes every window.
pub(crate) async fn conversation_folder(
    window: Webview,
    app: AppHandle,
    conversation_id: Option<String>,
    pending_id: Option<String>,
) -> Result<Option<String>, String> {
    require_agent_host_caller(&window, &app)?;
    require_local_install(&app)?;
    let key = slot(conversation_id.as_deref(), pending_id.as_deref())?;
    tauri::async_runtime::spawn_blocking(move || read_bindings().get(&key).cloned())
        .await
        .map_err(|error| error.to_string())
}

/// Ask for a folder, and hold the answer.
///
/// `conversation_id` is optional because the composer offers this before the
/// conversation exists. Absent, the choice waits under `PENDING` until
/// `adopt_conversation_folder` gives it an id.
///
/// `None` means the dialog was dismissed, which is not a failure and must not
/// change anything: someone opening the picker to look and closing it again has
/// said nothing.
#[tauri::command]
pub(crate) async fn bind_conversation_folder(
    window: Webview,
    app: AppHandle,
    conversation_id: Option<String>,
    pending_id: Option<String>,
) -> Result<Option<String>, String> {
    require_agent_host_caller(&window, &app)?;
    require_local_install(&app)?;
    let key = slot(conversation_id.as_deref(), pending_id.as_deref())?;
    // `blocking_pick_folder` would block the thread it is called on, and a
    // `#[tauri::command]` runs on the main thread — which is the thread the
    // dialog itself needs in order to appear. Asked asynchronously instead.
    let (sender, receiver) = std::sync::mpsc::channel();
    app.dialog()
        .file()
        .set_title("Choose a folder for this conversation")
        .pick_folder(move |chosen| {
            let _ = sender.send(chosen);
        });
    let chosen = tauri::async_runtime::spawn_blocking(move || receiver.recv().ok().flatten())
        .await
        .map_err(|error| format!("the folder dialog did not finish: {error}"))?;

    let Some(folder) = chosen else {
        return Ok(None);
    };
    let path = folder
        .into_path()
        .map_err(|error| format!("that folder cannot be used: {error}"))?;
    let recorded = path
        .to_str()
        .ok_or_else(|| "that folder's name is not valid text".to_string())?
        .to_owned();
    let stored = recorded.clone();
    tauri::async_runtime::spawn_blocking(move || {
        update_bindings(|bindings| {
            bindings.insert(key, stored);
        })
    })
    .await
    .map_err(|error| error.to_string())??;
    Ok(Some(recorded))
}

/// Give the waiting choice the conversation it was made for.
///
/// Returns the folder that was adopted, or `None` when nothing was waiting --
/// which is the ordinary case, because most conversations never pick one.
#[tauri::command]
/// Off the UI thread, for the same reason as `conversation_folder`.
pub(crate) async fn adopt_conversation_folder(
    window: Webview,
    app: AppHandle,
    conversation_id: String,
    pending_id: String,
) -> Result<Option<String>, String> {
    require_agent_host_caller(&window, &app)?;
    require_local_install(&app)?;
    let key = conversation_key(&conversation_id)?;
    let waiting = pending_key(&pending_id)?;
    tauri::async_runtime::spawn_blocking(move || {
        update_bindings(|bindings| {
            let folder = bindings.remove(&waiting)?;
            bindings.insert(key, folder.clone());
            Some(folder)
        })
    })
    .await
    .map_err(|error| error.to_string())?
}

/// Work in the ordinary place again.
#[tauri::command]
/// Off the UI thread, for the same reason as `conversation_folder`.
pub(crate) async fn unbind_conversation_folder(
    window: Webview,
    app: AppHandle,
    conversation_id: Option<String>,
    pending_id: Option<String>,
) -> Result<(), String> {
    require_agent_host_caller(&window, &app)?;
    require_local_install(&app)?;
    let key = slot(conversation_id.as_deref(), pending_id.as_deref())?;
    tauri::async_runtime::spawn_blocking(move || {
        update_bindings(|bindings| {
            bindings.remove(&key);
        })
    })
    .await
    .map_err(|error| error.to_string())?
}

#[cfg(test)]
mod tests {
    use super::*;

    /// One composer's waiting choice is not another's.
    ///
    /// A single shared slot meant a folder chosen in a composer that was then
    /// abandoned was still there when the next new conversation started, and
    /// that conversation adopted it -- a directory chosen for something else.
    #[test]
    fn a_waiting_choice_belongs_to_the_composer_that_made_it() {
        let first = pending_key("composer-a").expect("valid id");
        let second = pending_key("composer-b").expect("valid id");

        assert_ne!(first, second);
        // And neither can be steered onto a conversation's own slot.
        let conversation = Uuid::new_v4().to_string();
        assert_ne!(pending_key(&conversation).expect("valid id"), conversation);
    }

    #[test]
    fn a_pending_id_that_is_not_one_is_refused() {
        for hostile in ["", "   ", "../../etc", "has space", "a/b", &"x".repeat(129)] {
            assert!(pending_key(hostile).is_err(), "{hostile}");
        }
    }

    /// Neither half can be omitted: without a conversation and without a
    /// composer there is no slot to name, and defaulting to one would be
    /// choosing a binding on the caller's behalf.
    #[test]
    fn a_slot_needs_a_conversation_or_a_composer() {
        assert!(slot(None, None).is_err());
        assert!(slot(None, Some("composer-a")).is_ok());
        assert!(slot(Some(&Uuid::new_v4().to_string()), None).is_ok());
    }

    /// Two writers must not lose each other's binding.
    ///
    /// Each read the same map, changed its own entry and renamed its file over
    /// the other's, so the later rename won and the earlier binding was gone.
    /// They shared one temporary path as well, which is a second way to corrupt
    /// each other before either rename.
    #[test]
    fn concurrent_bindings_do_not_overwrite_each_other() {
        let temporary = tempfile::tempdir().expect("temp dir");
        // `update_bindings` reads and writes `bindings_path()`, which is derived
        // from the locald root, so the test points that at a scratch directory.
        let previous = std::env::var_os("LEMMA_LOCALD_ROOT");
        // SAFETY: single-threaded setup before the threads below are spawned.
        unsafe { std::env::set_var("LEMMA_LOCALD_ROOT", temporary.path().join("locald")) };

        let names: Vec<String> = (0..24)
            .map(|index| format!("conversation-{index}"))
            .collect();
        std::thread::scope(|scope| {
            for name in &names {
                scope.spawn(move || {
                    update_bindings(|bindings| {
                        bindings.insert(name.clone(), format!("/folder/{name}"));
                    })
                    .expect("write");
                });
            }
        });

        let written = read_bindings();
        for name in &names {
            assert_eq!(
                written.get(name).map(String::as_str),
                Some(format!("/folder/{name}").as_str()),
                "{name} was lost to another writer"
            );
        }

        // And nothing half-written left behind.
        let leftovers: Vec<_> = std::fs::read_dir(bindings_path().parent().expect("parent"))
            .expect("read dir")
            .filter_map(Result::ok)
            .filter(|entry| entry.file_name().to_string_lossy().contains(".tmp"))
            .collect();
        assert!(leftovers.is_empty(), "temporary files were left behind");

        match previous {
            // SAFETY: the threads above have joined.
            Some(value) => unsafe { std::env::set_var("LEMMA_LOCALD_ROOT", value) },
            None => unsafe { std::env::remove_var("LEMMA_LOCALD_ROOT") },
        }
    }
}
