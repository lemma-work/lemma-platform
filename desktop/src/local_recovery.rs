use super::*;

/// What this Mac still has that a reset could remove.
///
/// Read by the splash so it can offer the right tier -- and only offer one at
/// all when there is something to reset. Reported in allocated bytes rather
/// than the disk's apparent size: `data.raw` is sparse and always claims 24
/// GiB, so `len()` would tell every user they were about to recover 24 GiB
/// regardless of what was on it.
#[tauri::command(async)]
pub(crate) fn local_recovery_options(window: Webview) -> Result<RecoveryOptions, String> {
    require_local_native_window(&window)?;
    let config = read_config();
    let installed = configured_runtime(&config, "installedRuntime");
    let data_disk = managed_data_disk();
    Ok(RecoveryOptions {
        // Tier 1 needs a daemon to drive it; Tier 2 exists precisely for when
        // there is not one, so it is offered whenever any state survives.
        data_reset_available: locald_root().exists(),
        full_reinstall_available: locald_root().exists() || installed.is_some(),
        installed_runtime_release: installed.map(|runtime| runtime.release),
        data_disk_allocated_bytes: allocated_bytes(&data_disk),
    })
}

#[cfg(unix)]
pub(crate) fn allocated_bytes(path: &std::path::Path) -> u64 {
    use std::os::unix::fs::MetadataExt;
    path.metadata().map(|meta| meta.blocks() * 512).unwrap_or(0)
}

#[cfg(not(unix))]
pub(crate) fn allocated_bytes(path: &std::path::Path) -> u64 {
    path.metadata().map(|meta| meta.len()).unwrap_or(0)
}

pub(crate) fn reset_local_data_impl(app: AppHandle) -> Result<RecoveryOutcome, String> {
    if !confirm_destructive_action_impl(
        app.clone(),
        "Reset local data?".into(),
        format!(
            "Every pod, table, file, workspace and account on {THIS_COMPUTER} is \
             deleted. Your AI provider settings and the downloaded runtime are \
             kept, so Lemma starts again in seconds.\n\nThis cannot be undone."
        ),
        "Reset Data".into(),
    )? {
        return Ok(RecoveryOutcome::Cancelled);
    }

    // Before anything is destroyed, and first, because the completion handler
    // is fire-and-forget and this gets the whole reset to finish in.
    //
    // A SuperTokens cookie minted against the database we are about to delete
    // is presented to the new one and accepted as a session that cannot do
    // anything -- an app permanently signed in and permanently broken, where
    // even signing out is an authorized call.
    clear_local_session_data(&app);
    // Not fatal: a stale resume target costs one splash-less launch that falls
    // back to the splash anyway, and refusing the reset over it would be worse.
    if let Err(error) = write_config(|config| {
        if let Some(object) = config.as_object_mut() {
            // Names a generation and an account that will not exist.
            object.remove("resumeTarget");
        }
    }) {
        append_install_log(&format!(
            "reset: could not clear the resume target: {error}"
        ));
    }

    ensure_locald(&app)?;
    send_local_operation(
        &app,
        json!({"cmd": "local.reset-data", "confirm": "reset-local-data"}),
        operation_id("reset-data"),
    )?;
    Ok(RecoveryOutcome::Started)
}

pub(crate) fn reset_full_reinstall_impl(app: AppHandle) -> Result<RecoveryOutcome, String> {
    if !confirm_destructive_action_impl(
        app.clone(),
        "Permanently erase local Lemma and reinstall?".into(),
        format!(
            "This permanently deletes local pods, databases, files, accounts, schedules, \
             AI provider settings and stored keys, downloaded runtime files, and this \
             installation's Agent Host pairings and managed working folders on {THIS_COMPUTER}.\n\n\
             Active local work stops. External project folders and Lemma Cloud workspace \
             data are kept. The Lemma app and diagnostic logs are kept. Choosing Local Lemma \
             afterwards downloads its verified services again and requires an internet connection.\n\n\
             There is no automatic backup. Export anything you need before continuing. \
             This cannot be undone."
        ),
        "Erase Local Lemma".into(),
    )? {
        return Ok(RecoveryOutcome::Cancelled);
    }

    let shell: State<Shell> = app.state();
    let _recovery = recovery::RecoveryGuard::enter(&shell.recovery_running)
        .map_err(|error| error.to_string())?;
    let _installation = shell.runtime_install.try_lock()
        .map_err(|_| "An installation is still running. Close and reopen Lemma, then use Recovery before starting setup.".to_string())?;
    let _connection = shell.locald_connect.try_lock()
        .map_err(|_| "The background service is still starting. Wait for startup to finish or close and reopen Lemma, then retry Recovery.".to_string())?;
    clear_local_session_data(&app);
    // The daemon has to be gone before its own state directory is removed, and
    // this tolerates there being no daemon at all -- which is the state this
    // tier exists for.
    stop_locald_for_runtime_maintenance(&app)?;

    let summary = run_locald_reset()?;
    append_install_log(&format!("full reinstall: {summary}"));

    let root = locald_root();
    let agent_host = root
        .parent()
        .ok_or("the local installation has no parent directory")?
        .join("agent-host");
    recovery::clear_reinstall_files(&app_support_dir(), &agent_host)
        .map_err(|error| format!("Cleanup is incomplete: {error}. Some local data has already been erased. Retry force cleanup to finish."))?;

    let snapshot = {
        let shell: State<Shell> = app.state();
        let mut ui = shell.ui.lock().unwrap();
        ui.mode = "undecided".into();
        ui.running = false;
        ui.ready = false;
        ui.error = false;
        ui.status = String::new();
        ui.error_code = String::new();
        ui.clone()
    };
    let _ = app.emit("lemma:state", snapshot);
    refresh_menus_for_connection_mode(&app);
    shell.recovery_mode.store(false, Ordering::Release);
    if let Some(control) = app.get_webview("control") {
        let _ = control.close();
    }
    show_splash(&app);
    Ok(RecoveryOutcome::Completed)
}

/// Run `lemma-locald reset` and return its JSON summary.
///
/// The wipe runs inside the daemon binary rather than here because the OS
/// credential vault keys each stored item's access control to the code identity
/// that created it -- `work.lemma.locald`. A delete issued from this process is
/// a different program as far as the vault is concerned, and would prompt or
/// silently fail.
pub(crate) fn run_locald_reset() -> Result<String, String> {
    let executable = bundled_sibling("lemma-locald")
        .ok_or("the bundled lemma-locald is missing, so local state cannot be reset")?;
    let mut command = Command::new(executable);
    command
        .args(["reset", "--confirm=erase-local-lemma"])
        .env("LEMMA_LOCALD_ROOT", locald_root())
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .no_console_window();
    let output = lemma_desktop_process::run(command, Duration::from_secs(120), 1024 * 1024)
        .map_err(|error| format!("could not run the local reset: {error}"))?;
    if !output.status.success() {
        let detail = String::from_utf8_lossy(&output.stderr);
        let detail = detail.lines().last().unwrap_or("no reason given");
        return Err(format!("the local reset did not finish: {detail}"));
    }
    Ok(String::from_utf8_lossy(&output.stdout).trim().to_owned())
}

/// Forget the cookies and storage of the workspace being destroyed.
///
/// Best effort and fire-and-forget: the reset is worth doing even if the
/// webview will not answer, and the alternative to trying is a user who is
/// signed in to a database that no longer exists.
pub(crate) fn clear_local_session_data(app: &AppHandle) {
    if let Some(window) = app.get_webview("main") {
        let _ = window.clear_all_browsing_data();
    }
}

/// Destroy everything on this Mac that the user made, then start clean.
#[tauri::command]
pub(crate) async fn reset_local_data(
    window: Webview,
    app: AppHandle,
) -> Result<RecoveryOutcome, String> {
    require_local_native_window(&window)?;
    tauri::async_runtime::spawn_blocking(move || reset_local_data_impl(app))
        .await
        .map_err(|error| error.to_string())?
}

/// Return this Mac to the state of one that has never run Lemma.
#[tauri::command]
pub(crate) async fn restart_into_recovery(window: Webview, app: AppHandle) -> Result<(), String> {
    require_control_window(&window)?;
    std::fs::create_dir_all(app_support_dir()).map_err(|error| error.to_string())?;
    std::fs::write(app_support_dir().join("recovery-mode"), b"recovery\n")
        .map_err(|error| format!("could not request recovery mode: {error}"))?;
    app.restart();
}

/// Erase this installation only after an explicit native confirmation.
#[tauri::command]
pub(crate) async fn reset_full_reinstall(
    window: Webview,
    app: AppHandle,
) -> Result<RecoveryOutcome, String> {
    require_local_native_window(&window)?;
    tauri::async_runtime::spawn_blocking(move || reset_full_reinstall_impl(app))
        .await
        .map_err(|error| error.to_string())?
}
