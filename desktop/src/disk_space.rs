//! This Mac → Overview's disk row: what Lemma occupies, and giving it back.
//!
//! locald owns the data disk and its pre-migration backup and reports both in
//! `control.snapshot` (`disk_usage`); the runtime releases are this app's, so
//! their size is added here. Two commands act on them, each refused unless
//! the caller is this installation's own workspace
//! (`require_local_settings_caller`):
//!
//! - `delete_update_backup` removes the backup from before the last update,
//!   after asking natively -- it is the one copy a failed migration could be
//!   restored from;
//! - `free_up_disk_space` runs every safe cleanup: retired runtime releases,
//!   images no sandbox uses, a trim of the data disk -- and the backup, when
//!   there is one, only after the same native question.

use super::*;

/// Runtime releases, keyed by the directory listing that produced the figure.
///
/// Walking a release is tens of thousands of files, and This Mac's snapshot is
/// polled every fifteen seconds. A release directory's contents never change
/// once installed, so its name stands for its size.
static RELEASE_SIZES: Mutex<Option<(Vec<String>, Value)>> = Mutex::new(None);

/// Bytes a directory tree occupies on disk: allocated blocks, symlinks not
/// followed.
pub(crate) fn tree_allocated_bytes(root: &Path) -> u64 {
    let mut total = 0;
    let mut pending = vec![root.to_path_buf()];
    while let Some(path) = pending.pop() {
        let Ok(metadata) = std::fs::symlink_metadata(&path) else {
            continue;
        };
        total += allocated(&metadata);
        if metadata.is_dir() {
            if let Ok(entries) = std::fs::read_dir(&path) {
                pending.extend(entries.flatten().map(|entry| entry.path()));
            }
        }
    }
    total
}

#[cfg(unix)]
fn allocated(metadata: &std::fs::Metadata) -> u64 {
    use std::os::unix::fs::MetadataExt;
    metadata.blocks() * 512
}

#[cfg(not(unix))]
fn allocated(metadata: &std::fs::Metadata) -> u64 {
    metadata.len()
}

/// What `runtime/releases` holds: every release directory, its size, and
/// whether it is the running one or the one kept for rolling back.
pub(crate) fn runtime_releases_usage(install_root: &Path, kept: &[PathBuf]) -> Value {
    let releases = install_root.join("releases");
    let mut names: Vec<String> = std::fs::read_dir(&releases)
        .map(|entries| {
            entries
                .flatten()
                .filter(|entry| entry.file_type().is_ok_and(|kind| kind.is_dir()))
                .filter_map(|entry| entry.file_name().to_str().map(str::to_owned))
                .collect()
        })
        .unwrap_or_default();
    names.sort();
    // The directory and the kept set are part of the key: a different root, or
    // a release that became the rollback copy, is a different answer.
    let mut key = vec![releases.display().to_string()];
    key.extend(kept.iter().map(|path| path.display().to_string()));
    key.extend(names.iter().cloned());
    let mut cache = RELEASE_SIZES
        .lock()
        .unwrap_or_else(|poison| poison.into_inner());
    if let Some((cached, value)) = cache.as_ref() {
        if *cached == key {
            return value.clone();
        }
    }
    let resolved: Vec<PathBuf> = kept
        .iter()
        .map(|path| std::fs::canonicalize(path).unwrap_or_else(|_| path.clone()))
        .collect();
    let entries: Vec<Value> = names
        .iter()
        .map(|name| {
            let path = releases.join(name);
            let canonical = std::fs::canonicalize(&path).unwrap_or_else(|_| path.clone());
            json!({
                "name": name,
                "allocated_bytes": tree_allocated_bytes(&path),
                "kept": resolved.contains(&canonical),
            })
        })
        .collect();
    let total: u64 = entries
        .iter()
        .filter_map(|entry| entry["allocated_bytes"].as_u64())
        .sum();
    let value = json!({ "allocated_bytes": total, "releases": entries });
    *cache = Some((key, value.clone()));
    value
}

/// The release directories activation keeps: the running one and the one
/// before it. Empty when no downloaded runtime is recorded (a bundled or
/// development runtime), and then nothing is pruned.
pub(crate) fn retained_release_roots(config: &Value) -> Vec<PathBuf> {
    if configured_runtime(config, "installedRuntime").is_none() {
        return Vec::new();
    }
    ["installedRuntime", "previousRuntime"]
        .iter()
        .filter_map(|key| configured_runtime(config, key))
        .filter_map(|runtime| runtime.host_pack_root.parent().map(Path::to_path_buf))
        .collect()
}

/// Remove release directories beyond the running one and one previous.
///
/// Activation already does this, but only at the moment of an upgrade, and a
/// removal that failed then (Windows keeps a release in use locked) stayed for
/// ever. Run again after each successful start, when nothing can be using a
/// retired release any more.
pub(crate) fn prune_retired_releases_now() -> Vec<PathBuf> {
    let keep = retained_release_roots(&read_config());
    if keep.is_empty() {
        return Vec::new();
    }
    let removed = artifact_install::prune_retired_releases(&runtime_install_root(), &keep);
    for release in &removed {
        append_install_log(&format!("removed retired runtime {}", release.display()));
    }
    removed
}

/// The first `ready` of a launch: prune off the event thread.
pub(crate) fn prune_retired_releases_after_start() {
    std::thread::spawn(|| {
        prune_retired_releases_now();
    });
}

/// The disk block of This Mac's view: locald's figures, narrowed, plus the
/// runtime releases this app keeps.
pub(crate) fn disk_usage_view(daemon: &Value, releases: Value) -> Value {
    let pick = |value: &Value, keys: &[&str]| -> Value {
        let mut picked = serde_json::Map::new();
        for key in keys {
            if let Some(field) = value.get(*key) {
                picked.insert((*key).to_owned(), field.clone());
            }
        }
        Value::Object(picked)
    };
    let backup = daemon
        .get("update_backup")
        .filter(|value| value.is_object())
        .map(|backup| {
            pick(
                backup,
                &[
                    "allocated_bytes",
                    "reclaimable_bytes",
                    "size_is_upper_bound",
                    "expires_at_unix",
                ],
            )
        })
        .unwrap_or(Value::Null);
    let data_disk = daemon
        .get("data_disk")
        .filter(|value| value.is_object())
        .map(|disk| pick(disk, &["allocated_bytes"]))
        .unwrap_or(Value::Null);
    json!({
        "data_disk": data_disk,
        "update_backup": backup,
        "runtime_releases": releases,
    })
}

/// A backup's size in the words the question uses: what deleting it frees
/// when APFS says, "up to" the allocated size when it cannot.
pub(crate) fn backup_size_words(backup: &Value) -> String {
    match (
        backup["reclaimable_bytes"].as_u64(),
        backup["allocated_bytes"].as_u64(),
    ) {
        (Some(bytes), _) => format_bytes(bytes),
        (None, Some(bytes)) => format!("up to {}", format_bytes(bytes)),
        (None, None) => "some space".into(),
    }
}

/// Decimal units, as Finder shows sizes. The same rule as locald's.
pub(crate) fn format_bytes(bytes: u64) -> String {
    const UNITS: [&str; 4] = ["KB", "MB", "GB", "TB"];
    if bytes < 1000 {
        return format!("{bytes} bytes");
    }
    let mut value = bytes as f64 / 1000.0;
    let mut unit = 0;
    while value >= 999.95 && unit + 1 < UNITS.len() {
        value /= 1000.0;
        unit += 1;
    }
    if value >= 10.0 {
        format!("{value:.0} {}", UNITS[unit])
    } else {
        format!("{value:.1} {}", UNITS[unit])
    }
}

/// The native question before the backup goes.
pub(crate) fn backup_consent(backup: &Value) -> NativeConsent {
    NativeConsent {
        title: "Delete the backup from before the last update?".into(),
        message: format!(
            "Lemma copied its data before it last updated, in case the update went \
             wrong. Deleting the copy frees {} on {THIS_COMPUTER}. Your pods, files and \
             accounts are not touched. Lemma deletes it on its own once the update has \
             started cleanly, or after three days.",
            backup_size_words(backup)
        ),
        confirm: "Delete Backup".into(),
    }
}

/// The locald request for a cleanup. Pure, so what reaches the daemon can be
/// asserted: two booleans and nothing a page chose beyond them.
pub(crate) fn disk_cleanup_request(delete_backup: bool, prune_images: bool) -> Value {
    json!({
        "cmd": "disk.cleanup",
        "id": operation_id("workspace-disk-cleanup"),
        "delete_backup": delete_backup,
        "prune_images": prune_images,
    })
}

fn current_backup() -> Result<Value, String> {
    let snapshot = locald_request(
        json!({"cmd": "control.snapshot", "id": operation_id("workspace-disk-usage")}),
        Duration::from_secs(15),
    )?;
    Ok(snapshot["disk_usage"]["update_backup"].clone())
}

fn cleanup_answer(answer: &Value, removed_releases: usize) -> Value {
    json!({
        "cancelled": false,
        "backup_freed_bytes": answer.get("backup_freed_bytes").cloned().unwrap_or(Value::Null),
        "images": answer.get("images").cloned().unwrap_or(Value::Null),
        "images_error": answer.get("images_error").cloned().unwrap_or(Value::Null),
        "removed_releases": removed_releases,
    })
}

fn delete_update_backup_impl(app: AppHandle) -> Result<Value, String> {
    if current_mode(&app) != "local" {
        return Err(format!("{THIS_COMPUTER} runs no local Lemma"));
    }
    ensure_locald_without_host_pack(&app)?;
    let backup = current_backup()?;
    if !backup.is_object() {
        return Ok(json!({ "cancelled": false, "backup_freed_bytes": null }));
    }
    if !backup_consent(&backup).ask(&app)? {
        return Ok(json!({ "cancelled": true }));
    }
    let answer = locald_request(disk_cleanup_request(true, false), Duration::from_secs(60))?;
    Ok(cleanup_answer(&answer, 0))
}

fn free_up_disk_space_impl(app: AppHandle) -> Result<Value, String> {
    if current_mode(&app) != "local" {
        return Err(format!("{THIS_COMPUTER} runs no local Lemma"));
    }
    ensure_locald_without_host_pack(&app)?;
    let backup = current_backup()?;
    let delete_backup = backup.is_object();
    if delete_backup && !backup_consent(&backup).ask(&app)? {
        return Ok(json!({ "cancelled": true }));
    }
    let removed_releases = prune_retired_releases_now().len();
    // Pruning and trimming run in the guest; the budget covers a slow trim.
    let answer = locald_request(
        disk_cleanup_request(delete_backup, true),
        Duration::from_secs(10 * 60),
    )?;
    Ok(cleanup_answer(&answer, removed_releases))
}

#[tauri::command]
/// Delete the pre-migration backup, after a native question. A daemon round
/// trip and a dialog, so off the UI thread.
pub(crate) async fn delete_update_backup(window: Webview, app: AppHandle) -> Result<Value, String> {
    require_local_settings_caller(&window, &app)?;
    tauri::async_runtime::spawn_blocking(move || delete_update_backup_impl(app))
        .await
        .map_err(|error| error.to_string())?
}

#[tauri::command]
/// "Free up space": every cleanup that cannot lose anything, and the backup
/// after a native question.
pub(crate) async fn free_up_disk_space(window: Webview, app: AppHandle) -> Result<Value, String> {
    require_local_settings_caller(&window, &app)?;
    tauri::async_runtime::spawn_blocking(move || free_up_disk_space_impl(app))
        .await
        .map_err(|error| error.to_string())?
}
