use super::*;

pub(crate) fn lemma_update_metadata(raw: &Value) -> LemmaUpdateMetadata {
    lemma_update_metadata_for(
        raw,
        if cfg!(windows) {
            "windows-x86_64"
        } else {
            "darwin-aarch64"
        },
    )
}

pub(crate) fn lemma_update_metadata_for(raw: &Value, target: &str) -> LemmaUpdateMetadata {
    let Some(block) = raw.get("lemma") else {
        return LemmaUpdateMetadata::default();
    };
    let block = if let Some(platforms) = block.get("platforms") {
        let Some(platform) = platforms.get(target) else {
            return LemmaUpdateMetadata::default();
        };
        platform
    } else {
        block
    };
    LemmaUpdateMetadata {
        postgres_major: block.get("postgres_major").and_then(Value::as_u64),
        runtime_download_bytes: block.get("runtime_download_bytes").and_then(Value::as_u64),
    }
}

/// Where an in-flight update records what it was aiming at.
///
/// In locald's root rather than beside the app: on Windows the installer
/// replaces the whole application directory, so anything written there is gone
/// exactly when it is needed.
pub(crate) fn update_attempt_path() -> PathBuf {
    locald_root().join("shell-update.json")
}

pub(crate) fn record_update_attempt(app: &AppHandle, to: &str) {
    let from = app.package_info().version.to_string();
    let to = to.to_owned();
    if let Err(error) = config_store::update(&update_attempt_path(), |record| {
        *record = json!({"schema_version": 1, "from": from, "to": to});
    }) {
        // Not fatal: failing to record an update is no reason to refuse one.
        append_bounded_log(
            &launch_log_path(),
            &format!("could not record the update attempt: {error}"),
        );
    }
}

pub(crate) fn clear_update_attempt() {
    let _ = std::fs::remove_file(update_attempt_path());
}

/// Read an update record against the version actually running.
///
/// Pure, and separate from the file handling, because the interesting part is
/// the three-way comparison and it is the part worth testing.
pub(crate) fn classify_update_attempt(record: Option<&Value>, running: &str) -> UpdateAttempt {
    let Some(record) = record else {
        return UpdateAttempt::None;
    };
    let from = record.get("from").and_then(Value::as_str);
    let to = record.get("to").and_then(Value::as_str);
    let (Some(from), Some(to)) = (from, to) else {
        return UpdateAttempt::Unexplained;
    };
    if to == running {
        UpdateAttempt::Landed { to: to.to_owned() }
    } else if from == running {
        UpdateAttempt::DidNotLand { to: to.to_owned() }
    } else {
        UpdateAttempt::Unexplained
    }
}

/// Settle whatever the last launch's update attempt left behind.
///
/// The record is cleared either way. Its only job is to let this launch say
/// what happened, and a record kept past that would explain the wrong launch.
pub(crate) fn reconcile_update_attempt(app: &AppHandle) {
    let record = std::fs::read(update_attempt_path())
        .ok()
        .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok());
    let running = app.package_info().version.to_string();
    let outcome = classify_update_attempt(record.as_ref(), &running);
    if outcome != UpdateAttempt::None {
        clear_update_attempt();
    }
    match outcome {
        UpdateAttempt::None => (),
        UpdateAttempt::Landed { to } => {
            append_bounded_log(&launch_log_path(), &format!("update to {to} completed"));
        }
        UpdateAttempt::Unexplained => {
            append_bounded_log(
                &launch_log_path(),
                &format!("an update record did not describe this version ({running}); discarded"),
            );
        }
        UpdateAttempt::DidNotLand { to } => {
            let message = format!(
                "Lemma {to} was downloaded but its installer did not finish, so this is \
                 still {running}. Nothing was changed. Check for updates again when you \
                 are ready."
            );
            append_bounded_log(
                &launch_log_path(),
                &format!("update to {to} did not finish"),
            );
            announce_incomplete_update(app, message);
        }
    }
}

/// Tell the user their update did not happen, once there is a window to tell.
///
/// On its own thread with a deadline: `setup` runs before the window is built,
/// and `report_action_failure` needs one. The launch log has the record either
/// way, so a window that never appears costs the message and not the evidence.
pub(crate) fn announce_incomplete_update(app: &AppHandle, message: String) {
    let handle = app.clone();
    std::thread::spawn(move || {
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(30);
        while handle.get_window("main").is_none() && std::time::Instant::now() < deadline {
            std::thread::sleep(std::time::Duration::from_millis(100));
        }
        report_action_failure(&handle, "Update", &message);
    });
}

/// Ask the release feed whether there is a newer Lemma.
///
/// Runs in Rust so the webview's CSP stays exactly as it is. A JavaScript check
/// would need `github.com` and `objects.githubusercontent.com` in
/// `connect-src`, widening the network policy of the same webview that hosts
/// the remote workspace origin.
#[tauri::command]
pub(crate) async fn check_for_app_update(
    window: Webview,
    app: AppHandle,
) -> Result<AppUpdateStatus, String> {
    require_control_window(&window)?;
    let mut status = AppUpdateStatus {
        channel: release_channel(),
        current_version: env!("CARGO_PKG_VERSION"),
        build_commit: build_commit(),
        updates_supported: updates_enabled(),
        available_version: None,
        runtime_download_bytes: None,
        data_compatibility: "unknown",
    };
    if !updates_enabled() {
        return Ok(status);
    }
    let update = app
        .updater_builder()
        .endpoints(parsed_updater_endpoints())
        .map_err(|error| format!("could not check for updates: {error}"))?
        .build()
        .map_err(|error| format!("could not check for updates: {error}"))?
        .check()
        .await
        .map_err(|error| format!("could not check for updates: {error}"))?;
    let Some(update) = update else {
        status.data_compatibility = "compatible";
        return Ok(status);
    };
    status.available_version = Some(update.version.clone());
    // The feed's own `lemma` block. The updater ignores unknown top-level keys
    // and hands back the parsed document, so this costs no extra request.
    let metadata = lemma_update_metadata(&update.raw_json);
    status.runtime_download_bytes = metadata.runtime_download_bytes;
    status.data_compatibility = if !has_local_runtime_data() {
        "compatible"
    } else if cfg!(windows) {
        "migration-unavailable"
    } else {
        metadata.compatibility_with(installed_postgres_major())
    };
    Ok(status)
}

/// Download and install a newer Lemma, then offer to restart.
#[tauri::command]
pub(crate) async fn install_app_update(
    window: Webview,
    app: AppHandle,
    reset_data: bool,
) -> Result<(), String> {
    require_control_window(&window)?;
    if !updates_enabled() {
        return Err(
            "this build does not update itself; download the current release instead".into(),
        );
    }
    let update = app
        .updater_builder()
        .endpoints(parsed_updater_endpoints())
        .map_err(|error| format!("could not check for updates: {error}"))?
        .build()
        .map_err(|error| format!("could not check for updates: {error}"))?
        .check()
        .await
        .map_err(|error| format!("could not check for updates: {error}"))?
        .ok_or("Lemma is already up to date")?;

    ensure_update_preserves_data(
        reset_data,
        has_local_runtime_data(),
        lemma_update_metadata(&update.raw_json).compatibility_with(installed_postgres_major()),
        cfg!(windows),
    )?;

    // Downloaded first, and deliberately not with `download_and_install`.
    //
    // `download` is where the signature is verified, and it is the step most
    // likely to fail: a network that drops, a feed that moved, a key that
    // cannot decode. Stopping the daemon before it meant every one of those
    // outcomes took the user's whole stack down and then reported an error --
    // for an update that never began.
    let bytes = update
        .download(|_, _| {}, || {})
        .await
        .map_err(|error| format!("could not download the update: {error}"))?;

    // Now, and only now. A DMG install moves the old app to the Trash, so the
    // running daemon's executable path changes and `locald_is_this_build`
    // notices. An in-place update writes to the *same* path, so a stale daemon
    // from the previous version would report an identical path and be adopted
    // by the new app -- supervising the old runtime under a new shell.
    let handle = app.clone();
    tauri::async_runtime::spawn_blocking(move || stop_locald_for_runtime_maintenance(&handle))
        .await
        .map_err(|error| error.to_string())??;

    // Written before `install`, because on Windows `install` launches the
    // NSIS installer and exits this process: there is no line after it in
    // which to record anything. Without it, an installer the user cancelled,
    // or one interrupted by a reboot, left the app running the old version
    // with nothing anywhere saying an update had been attempted at all.
    if cfg!(windows) {
        record_update_attempt(&app, &update.version.to_string());
    }

    if let Err(error) = update.install(bytes) {
        // The stack is down and the update did not happen. Leaving it there
        // stranded the user in Local settings over a workspace whose backend
        // had gone, with nothing offering to bring it back: the reader thread
        // only re-shows the splash when the settings window is absent, and
        // this command requires it to be open. Put the previous version --
        // still the installed one -- back into service before reporting.
        // The attempt is over and it is being reported here, so the record
        // has nothing left to explain on the next launch.
        clear_update_attempt();
        let handle = app.clone();
        let restarted = tauri::async_runtime::spawn_blocking(move || {
            start_after_runtime_maintenance(&handle, "shell-update-recover")
        })
        .await
        .map_err(|join| join.to_string())?;
        return Err(failed_install_message(&error.to_string(), restarted.err()));
    }

    // The Windows updater exits this process to run the installer. Completion
    // belongs to the next launch, not a dialog after installation.
    if cfg!(windows) {
        return Ok(());
    }

    let restart = confirm_destructive_action_impl(
        app.clone(),
        "Restart to finish updating?".into(),
        format!(
            "Lemma {} is installed. Restarting now finishes the update; it downloads \
             its runtime once afterwards.",
            update.version
        ),
        "Restart Now".into(),
    )?;
    if restart {
        app.restart();
    }
    Ok(())
}
