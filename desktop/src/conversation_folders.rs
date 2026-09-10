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

fn read_bindings() -> Bindings {
    std::fs::read_to_string(bindings_path())
        .ok()
        .and_then(|raw| serde_json::from_str::<Bindings>(&raw).ok())
        .unwrap_or_default()
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
    let temporary = path.with_extension("json.tmp");
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
    conversation_id: String,
) -> Result<Option<String>, String> {
    require_agent_host_caller(&window, &app)?;
    require_local_install(&app)?;
    let key = conversation_key(&conversation_id)?;
    tauri::async_runtime::spawn_blocking(move || read_bindings().get(&key).cloned())
        .await
        .map_err(|error| error.to_string())
}

/// Ask for a folder, and bind this conversation to it.
///
/// `None` means the dialog was dismissed, which is not a failure and must not
/// change the binding: someone opening the picker to look at it and closing it
/// again has said nothing.
#[tauri::command]
pub(crate) async fn bind_conversation_folder(
    window: Webview,
    app: AppHandle,
    conversation_id: String,
) -> Result<Option<String>, String> {
    require_agent_host_caller(&window, &app)?;
    require_local_install(&app)?;
    let key = conversation_key(&conversation_id)?;
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
        let mut bindings = read_bindings();
        bindings.insert(key, stored);
        write_bindings(&bindings)
    })
    .await
    .map_err(|error| error.to_string())??;
    Ok(Some(recorded))
}

/// Work in the ordinary place again.
#[tauri::command]
/// Off the UI thread, for the same reason as `conversation_folder`.
pub(crate) async fn unbind_conversation_folder(
    window: Webview,
    app: AppHandle,
    conversation_id: String,
) -> Result<(), String> {
    require_agent_host_caller(&window, &app)?;
    require_local_install(&app)?;
    let key = conversation_key(&conversation_id)?;
    tauri::async_runtime::spawn_blocking(move || {
        let mut bindings = read_bindings();
        if bindings.remove(&key).is_none() {
            return Ok(());
        }
        write_bindings(&bindings)
    })
    .await
    .map_err(|error| error.to_string())?
}
