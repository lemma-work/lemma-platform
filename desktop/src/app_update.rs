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
/// How long a check may take before Settings says it could not check.
///
/// The updater has no timeout of its own, so a stalled connection to the feed
/// left the page on "Checking for updates..." with no error and no way on. The
/// feed is one small JSON document behind a redirect; this is generous for it.
/// The install path has none: it downloads the app bundle, which a slow link
/// may legitimately take longer over.
pub(crate) const UPDATE_CHECK_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(30);

/// Ask this build's feed what it offers, bounded by `UPDATE_CHECK_TIMEOUT`.
///
/// Shared by the Settings check and the launch-time one, so both ask the same
/// feed with the same channel policy (`update_policy::candidate_allowed`, the
/// plugin's version comparator).
pub(crate) async fn fetch_offered_update(
    app: &AppHandle,
) -> Result<Option<tauri_plugin_updater::Update>, String> {
    app.updater_builder()
        .endpoints(parsed_updater_endpoints())
        .map_err(|error| format!("could not check for updates: {error}"))?
        .timeout(UPDATE_CHECK_TIMEOUT)
        .build()
        .map_err(|error| format!("could not check for updates: {error}"))?
        .check()
        .await
        .map_err(|error| format!("could not check for updates: {error}"))
}

/// How long after launch the background check waits.
///
/// Long enough that it never competes with the launch it follows -- a cold
/// local start is downloading and booting in these seconds -- and short
/// enough that someone who opens Lemma to do one thing still sees it.
pub(crate) const LAUNCH_UPDATE_CHECK_DELAY: std::time::Duration =
    std::time::Duration::from_secs(20);

/// Whether this launch should ask the feed on its own.
///
/// Not from Recovery, which exists to start nothing, and not from a build that
/// cannot update itself -- it would find a version and then refuse to
/// install it.
pub(crate) fn launch_update_check_wanted(updates_enabled: bool, recovery_launch: bool) -> bool {
    updates_enabled && !recovery_launch
}

/// Look for a newer Lemma once, in the background, after launch.
///
/// Until now nothing checked unless someone opened the update panel, and a
/// cloud user had no update panel they could reach. A hit adds one menu row
/// (`update_available_label`) in the tray and the Lemma menu that opens the
/// panel; nothing is downloaded or installed from here. A failure costs a
/// launch-log line: an offline launch is not an error worth a dialog.
pub(crate) fn schedule_launch_update_check(app: &AppHandle, recovery_launch: bool) {
    if !launch_update_check_wanted(updates_enabled(), recovery_launch) {
        return;
    }
    let handle = app.clone();
    // A plain thread for the wait, so no async worker is parked for it.
    std::thread::spawn(move || {
        std::thread::sleep(LAUNCH_UPDATE_CHECK_DELAY);
        match tauri::async_runtime::block_on(fetch_offered_update(&handle)) {
            Ok(Some(update)) => announce_available_update(&handle, update.version.clone()),
            Ok(None) => {}
            Err(error) => {
                append_bounded_log(&launch_log_path(), &format!("launch update check: {error}"))
            }
        }
    });
}

/// Put "Lemma X is available" in the menus.
///
/// Rebuilt rather than inserted into, so the one place that builds each menu
/// stays the one place that decides what is in it; the tray's two live lines
/// are then rewritten from the state the shell already holds.
pub(crate) fn announce_available_update(app: &AppHandle, version: String) {
    append_bounded_log(
        &launch_log_path(),
        &format!("launch update check: Lemma {version} is available"),
    );
    {
        let shell: State<Shell> = app.state();
        *shell.available_update.lock_or_recover() = Some(version);
    }
    let handle = app.clone();
    let _ = app.run_on_main_thread(move || {
        refresh_menus_for_connection_mode(&handle);
        refresh_tray_status(&handle);
        let status = {
            let shell: State<Shell> = handle.state();
            let status = shell.agent_host_status.lock_or_recover().clone();
            status
        };
        if let Some(status) = status {
            refresh_agent_host_tray(&handle, &status);
        }
    });
}

#[tauri::command]
pub(crate) async fn check_for_app_update(
    window: Webview,
    app: AppHandle,
) -> Result<AppUpdateStatus, String> {
    require_settings_caller(&window, &app)?;
    let mut status = AppUpdateStatus {
        channel: release_channel(),
        current_version: env!("CARGO_PKG_VERSION"),
        build_commit: build_commit(),
        updates_supported: updates_enabled(),
        available_version: None,
        runtime_download_bytes: None,
        data_compatibility: "compatible",
        installed_postgres_major: None,
        candidate_postgres_major: None,
    };
    if !updates_enabled() {
        return Ok(status);
    }
    let Some(update) = fetch_offered_update(&app).await? else {
        return Ok(status);
    };
    status.available_version = Some(update.version.clone());
    // The feed's own `lemma` block. The updater ignores unknown top-level keys
    // and hands back the parsed document, so this costs no extra request.
    let metadata = lemma_update_metadata(&update.raw_json);
    status.runtime_download_bytes = metadata.runtime_download_bytes;
    // No Windows exception: its data lives in a separate holder distribution,
    // and replacing the runtime already refuses to run until that holder says
    // it has the data (`refuse_replacement_without_holder`) -- a check at the
    // point of risk, where a blanket refusal here only hid every update.
    if has_local_runtime_data() {
        let installed = installed_postgres_major();
        status.data_compatibility = metadata.compatibility_with(installed);
        status.installed_postgres_major = installed;
        status.candidate_postgres_major = metadata.postgres_major;
    }
    Ok(status)
}

/// Whether the feed is still offering the version the user agreed to install.
///
/// Its own function because it is the whole of the consent check and the rest
/// of `install_app_update` needs a live updater to reach. An empty expectation
/// is refused rather than waved through: it means the caller had nothing to
/// show the user, and "install whatever is there" is not something anyone
/// agreed to.
pub(crate) fn offered_is_what_was_agreed(offered: &str, expected: &str) -> Result<(), String> {
    if expected.is_empty() {
        return Err(
            "Check for updates before installing one, so you can see what it changes.".into(),
        );
    }
    if offered != expected {
        return Err(format!(
            "The available update changed while you were deciding: it now offers \
             {offered} rather than {expected}. Check for updates again and read what \
             it says before installing."
        ));
    }
    Ok(())
}

/// One installation at a time, for the life of this process.
///
/// Two of them are not two updates, they are two downloads of the same bytes
/// racing to replace the same application while each stops the daemon the
/// other is relying on. There is nothing here that makes the second attempt
/// wait usefully -- the first is already doing the only work there is -- so it
/// is refused, and refused in words the user can act on.
static INSTALLING: AtomicBool = AtomicBool::new(false);

/// Held for the length of one installation, released however it ends.
pub(crate) struct InstallInFlight;

impl InstallInFlight {
    pub(crate) fn claim() -> Result<Self, String> {
        if INSTALLING.swap(true, Ordering::SeqCst) {
            return Err("An update is already being installed. Wait for it to finish.".into());
        }
        Ok(Self)
    }
}

impl Drop for InstallInFlight {
    fn drop(&mut self) {
        INSTALLING.store(false, Ordering::SeqCst);
    }
}

/// How many agent runs the Agent Host says are in flight, across every
/// workspace it is paired with. Zero when it is not running or has not said.
pub(crate) fn active_agent_runs(status: Option<&Value>) -> u64 {
    let Some(status) = status else { return 0 };
    if status["running"].as_bool() != Some(true) {
        return 0;
    }
    status["targets"]
        .as_array()
        .map(|targets| {
            targets
                .iter()
                .filter_map(|target| target["active_runs"].as_u64())
                .sum()
        })
        .unwrap_or(0)
}

/// The consent's account of the runs an install interrupts, or nothing.
///
/// Said only when there is something to lose: a line about zero runs is one
/// more thing to read past on every update.
pub(crate) fn interrupted_runs_sentence(active_runs: u64) -> String {
    match active_runs {
        0 => String::new(),
        1 => " 1 agent run on this computer is in progress and will be interrupted.".into(),
        many => {
            format!(" {many} agent runs on this computer are in progress and will be interrupted.")
        }
    }
}

/// Download and install a newer Lemma, then offer to restart.
///
/// `expected_version` is the version the user was actually shown and agreed
/// to. This command has to ask the feed again -- an `Update` is a live handle
/// on a download and does not survive the trip back to the webview -- and
/// between the two questions the feed can answer differently: a release is
/// published, a bad one is pulled. Without this the user consented to one
/// version and installed whichever the feed happened to be offering a moment
/// later, including a *downgrade*, and the only sign was the version in the
/// restart dialog.
#[tauri::command]
pub(crate) async fn install_app_update(
    window: Webview,
    app: AppHandle,
    reset_data: bool,
    expected_version: String,
) -> Result<(), String> {
    require_settings_caller(&window, &app)?;
    if !updates_enabled() {
        return Err(
            "this build does not update itself; download the current release instead".into(),
        );
    }
    let _in_flight = InstallInFlight::claim()?;
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
    offered_is_what_was_agreed(&update.version.to_string(), &expected_version)?;

    ensure_update_preserves_data(
        reset_data,
        has_local_runtime_data(),
        installed_postgres_major(),
        lemma_update_metadata(&update.raw_json).postgres_major,
    )?;

    // The person agrees here, natively, before anything is downloaded or
    // stopped. The command is reachable from the workspace page, and a page
    // asking is not the person agreeing: without this, a page that passed the
    // caller check could replace the application and restart the stack with
    // nobody at the keyboard having said yes. Asked after the checks above,
    // so a refusal they would make anyway is not preceded by a question, and
    // before the download, so saying no costs nothing. Off the async runtime,
    // for the reason given at the restart question below.
    // Said before, not after: the workspace is unusable from the moment the
    // stack stops until the new runtime has downloaded on the next launch,
    // and that is a cost somebody deciding *when* to update needs to know.
    let runtime_download = match lemma_update_metadata(&update.raw_json).runtime_download_bytes {
        Some(bytes) => format!(" (about {} MB)", bytes.div_ceil(1024 * 1024)),
        None => String::new(),
    };
    // Stopping locald stops the Agent Host with it, so a coding agent mid-run
    // is cut off. Read from the status the shell already holds rather than
    // asked for: this is a sentence in a question, and a stack too sick to
    // answer is not a reason to withhold the update.
    let active_runs = {
        let shell: State<Shell> = app.state();
        let status = shell.agent_host_status.lock_or_recover().clone();
        active_agent_runs(status.as_ref())
    };
    let consent = format!(
        "Lemma {} will be downloaded and installed. Lemma's local runtime stops \
         while it installs, and Lemma restarts as soon as it is installed.{} Your \
         local workspace opens again once the updated runtime{runtime_download} \
         has downloaded.",
        update.version,
        interrupted_runs_sentence(active_runs),
    );
    let handle = app.clone();
    let agreed = tauri::async_runtime::spawn_blocking(move || {
        confirm_destructive_action_impl(
            handle,
            "Install the update?".into(),
            consent,
            "Install".into(),
        )
    })
    .await
    .map_err(|join| join.to_string())??;
    if !agreed {
        return Err("The update was not installed.".into());
    }

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

    // Not a question any more. The stack was stopped above and the bundle on
    // disk is now the new version: "Later" left the old shell running over a
    // stopped stack it could only restart from the *new* locald binary, which
    // then met the old guest -- a mixed-version runtime nobody tested. The
    // consent above says the restart is part of installing.
    append_install_log(&format!(
        "update: Lemma {} installed; restarting to finish",
        update.version
    ));
    app.restart();
}
