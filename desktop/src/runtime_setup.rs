use super::*;

pub(crate) fn ensure_runtime_artifacts(app: &AppHandle) -> Result<(), String> {
    prepare_runtime_artifacts(app, false)
}

pub(crate) fn prepare_runtime_artifacts(app: &AppHandle, reinstall: bool) -> Result<(), String> {
    require_no_recovery(&app.state::<Shell>())?;
    match ensure_runtime_artifacts_inner(app, reinstall) {
        Ok(()) => Ok(()),
        Err(error) => {
            let message = actionable_runtime_install_error(&error);
            append_install_log(&format!("ERROR {message}"));
            emit_log(app, &message);
            emit_runtime_install_error(app, &message);
            Err(message)
        }
    }
}

pub(crate) fn ensure_runtime_artifacts_inner(
    app: &AppHandle,
    reinstall: bool,
) -> Result<(), String> {
    if runtime_root().join("desktop/locald/Cargo.toml").is_file() {
        return Ok(());
    }
    let config = read_config();
    if let Some(bundled_host) = bundled_host_pack_root() {
        if bundled_managed_runtime_root().is_none() {
            return Err("the bundled managed runtime is incomplete".into());
        }
        let release = host_pack_release(&bundled_host)
            .ok_or("the bundled native runtime has no valid release marker")?;
        if release != env!("CARGO_PKG_VERSION") {
            return Err(format!(
                "bundled runtime release {release} does not match desktop release {}",
                env!("CARGO_PKG_VERSION")
            ));
        }
        return Ok(());
    }
    let bundled_manifest = bundled_release_manifest();
    // A successfully installed and activated runtime is self-contained. Its
    // recorded artifact identity was written only after the manifest, archive
    // digests, extracted layout, and release markers were verified. Reuse that
    // exact release without consulting an artifact host when no manifest is
    // bundled, so ordinary Finder / Start-menu launches and later cached
    // runtime restarts keep working offline.
    //
    // When a manifest is bundled, compare its artifact digests even if the
    // semantic release is unchanged. This lets signed test builds replace a
    // same-version runtime pack without silently retaining stale components.
    if let Some(runtime) = configured_runtime(&config, "installedRuntime").filter(|runtime| {
        !reinstall
            && runtime.release == env!("CARGO_PKG_VERSION")
            && runtime.has_recorded_artifact_identity()
    }) {
        let Some(manifest) = bundled_manifest.as_ref() else {
            return Ok(());
        };
        let matches = artifact_install::runtime_matches_manifest(
            &runtime,
            manifest,
            env!("CARGO_PKG_VERSION"),
        )
        .map_err(|error| format!("could not verify the installed local runtime: {error}"))?;
        if matches {
            return Ok(());
        }
    }
    let manifest = bundled_manifest.ok_or_else(|| {
        "this online installer is missing its signed local release manifest".to_string()
    })?;
    let manifest_release = artifact_install::manifest_release(&manifest)
        .map_err(|error| format!("could not read the signed local release manifest: {error}"))?;
    if manifest_release != env!("CARGO_PKG_VERSION") {
        return Err(format!(
            "signed runtime release {manifest_release} does not match desktop release {}",
            env!("CARGO_PKG_VERSION")
        ));
    }
    if let Some(runtime) = configured_runtime(&config, "installedRuntime").filter(|_| !reinstall) {
        let matches = artifact_install::runtime_matches_manifest(
            &runtime,
            &manifest,
            env!("CARGO_PKG_VERSION"),
        )
        .map_err(|error| format!("could not verify the installed local runtime: {error}"))?;
        if matches {
            return Ok(());
        }
    }
    let install_operation_id = operation_id("runtime-install");
    {
        let shell: State<Shell> = app.state();
        let mut ui = shell.ui.lock().unwrap();
        ui.active_operation_id = install_operation_id.clone();
    }
    telemetry::note(telemetry::InstallEvent::RuntimeInstallStarted);
    {
        let shell: State<Shell> = app.state();
        shell.ui.lock().unwrap().installed_this_launch = true;
    }
    // Where the install got to, for the failure event. A install that dies is
    // only useful to hear about if we know which step died, and the progress
    // callback is the only thing that knows.
    let reached = std::sync::Arc::new(std::sync::Mutex::new("resolve"));
    emit_runtime_install_progress(
        app,
        "resolve",
        "runtime",
        "Preparing local runtime",
        1,
        None,
        None,
        None,
        None,
    );
    let install_started = std::time::Instant::now();
    let install = if reinstall {
        artifact_install::reinstall_from_manifest
    } else {
        artifact_install::install_from_manifest
    };
    let installed = install(
        &manifest,
        &runtime_install_root(),
        env!("CARGO_PKG_VERSION"),
        &mut |progress| {
            if let Some(step) = install_step(progress.stage) {
                *reached.lock().expect("install step lock poisoned") = step;
            }
            let fraction = progress
                .current
                .saturating_mul(1000)
                .checked_div(progress.total)
                .unwrap_or(0);
            let percent = match progress.stage {
                "download" => 2 + fraction.saturating_mul(44) / 1000,
                "verify" => 47,
                "host-extract" | "guest-extract" => 49 + fraction.saturating_mul(39) / 1000,
                "validate" => 90,
                _ => 1,
            };
            let (eta_seconds, throughput_bytes_per_second) =
                if progress.stage == "download" && progress.current > 0 {
                    let elapsed = install_started.elapsed().as_secs_f64();
                    let rate = progress.current as f64 / elapsed.max(0.001);
                    (
                        (progress.current < progress.total).then_some(
                            ((progress.total - progress.current) as f64 / rate).ceil() as u64,
                        ),
                        Some(rate.round() as u64),
                    )
                } else {
                    (None, None)
                };
            emit_runtime_install_progress(
                app,
                progress.stage,
                progress.component,
                progress.label,
                percent.min(90),
                progress.bytes.then_some(progress.current),
                progress.bytes.then_some(progress.total),
                eta_seconds,
                throughput_bytes_per_second,
            );
        },
    )
    .map_err(|error| {
        let detail = format!("could not install the local runtime: {error}");
        telemetry::note(telemetry::InstallEvent::RuntimeInstallFailed {
            step: *reached.lock().expect("install step lock poisoned"),
            class: runtime_install_failure_class(&detail),
        });
        detail
    })?;
    stop_locald_for_runtime_maintenance(app).map_err(|error| {
        format!("could not stop the previous local runtime before activation: {error}")
    })?;
    activate_installed_runtime(&installed)?;
    emit_runtime_install_progress(
        app,
        "activate",
        "runtime",
        "Local runtime installed",
        92,
        None,
        None,
        None,
        None,
    );
    {
        let shell: State<Shell> = app.state();
        let mut ui = shell.ui.lock().unwrap();
        if ui.active_operation_id == install_operation_id {
            ui.active_operation_id.clear();
        }
    }
    telemetry::note(telemetry::InstallEvent::RuntimeInstallCompleted);
    Ok(())
}

/// The installer's own stage names, narrowed to the ones worth reporting.
///
/// A closed set on purpose: the event carries this verbatim, and an unbounded
/// string from the installer is how a path or a URL ends up in an analytics
/// database.
fn install_step(stage: &str) -> Option<&'static str> {
    match stage {
        "download" => Some("download"),
        "verify" => Some("verify"),
        "host-extract" => Some("host-extract"),
        "guest-extract" => Some("guest-extract"),
        "validate" => Some("validate"),
        _ => None,
    }
}

/// Why an install failed, as one of a fixed set of words.
///
/// The same taxonomy `actionable_runtime_install_error` uses to decide what to
/// tell the person, reduced to something countable. Never the error text: that
/// carries paths, hostnames and occasionally a URL with a token in it.
fn runtime_install_failure_class(error: &str) -> &'static str {
    let lowered = error.to_ascii_lowercase();
    if error.contains("artifact download failed with HTTP 404") {
        return "artifact-missing";
    }
    if lowered.contains("http 401") || lowered.contains("http 403") || lowered.contains("http 429")
    {
        return "download-blocked";
    }
    if lowered.contains("could not connect") || lowered.contains("dns") {
        return "network-unreachable";
    }
    if lowered.contains("no space") || lowered.contains("not enough space") {
        return "disk-full";
    }
    if lowered.contains("digest") || lowered.contains("signature") || lowered.contains("checksum") {
        return "verification-failed";
    }
    if lowered.contains("permission denied") || lowered.contains("read-only") {
        return "permission-denied";
    }
    "other"
}

/// Turn an installer failure into something the person reading it can do.
///
/// This had exactly one branch, and it answered the one case *we* hit: a 404
/// told the reader to "publish its runtime artifacts", which is an instruction
/// to a maintainer shipped to a stranger. Everything else fell through
/// verbatim, so a corporate proxy became "artifact download failed with HTTP
/// 403" and a dropped connection became a reqwest debug string.
///
/// Each arm names what happened and what to try. The raw text stays in the
/// installer log, which the error screen links to.
pub(crate) fn actionable_runtime_install_error(error: &str) -> String {
    let lowered = error.to_ascii_lowercase();
    let version = env!("CARGO_PKG_VERSION");

    if error.contains("artifact download failed with HTTP 404") {
        return format!(
            "Lemma {version}'s runtime is not available for download. If this is a \
             nightly build, it may have been superseded — download the current one \
             and install it again."
        );
    }
    if lowered.contains("http 401") || lowered.contains("http 403") || lowered.contains("http 429")
    {
        return "The download was blocked or rate-limited. A VPN, proxy or firewall \
                may be intercepting github.com. Try again on a different network."
            .to_owned();
    }
    if lowered.contains("could not connect") || lowered.contains("dns") {
        return "Lemma could not reach github.com to download its runtime. Check \
                your internet connection and try again."
            .to_owned();
    }
    if lowered.contains("timed out") || lowered.contains("timeout") {
        return "The download stopped responding. Try again — it resumes from where \
                it stopped rather than starting over."
            .to_owned();
    }
    if lowered.contains("sha-256") || lowered.contains("digest") {
        return "The downloaded runtime did not match what Lemma expected. This is \
                usually a network that modifies downloads, such as a captive Wi-Fi \
                portal — sign in to the network first, then try again."
            .to_owned();
    }
    if lowered.contains("not enough disk space") {
        // Already actionable and carries real numbers; do not flatten it.
        return error.to_owned();
    }
    if lowered.contains("does not match desktop release") {
        return format!(
            "This copy of Lemma and its runtime do not match. Reinstalling Lemma \
             {version} fixes it."
        );
    }
    error.to_owned()
}

pub(crate) fn activate_installed_runtime(
    installed: &artifact_install::InstalledRuntime,
) -> Result<(), String> {
    let root = installed
        .host_pack_root
        .parent()
        .ok_or("installed runtime has no release root")?
        .to_string_lossy()
        .into_owned();
    let next = json!({"release": installed.release, "root": root});
    write_config(|config| {
        let current = config
            .get("installedRuntime")
            .cloned()
            .unwrap_or(Value::Null);
        if runtime_from_config_value(&current).is_some() && current != next {
            config["previousRuntime"] = current;
        }
        config["installedRuntime"] = next;
    })?;

    // Only after the config records the pair, so a crash between the two
    // leaves a release too many rather than a release too few. Failure is not
    // propagated: disk that could not be reclaimed is not a reason to fail an
    // upgrade that has already succeeded.
    let config = read_config();
    let keep: Vec<std::path::PathBuf> = ["installedRuntime", "previousRuntime"]
        .iter()
        .filter_map(|key| configured_runtime(&config, key))
        .filter_map(|runtime| {
            runtime
                .host_pack_root
                .parent()
                .map(std::path::Path::to_path_buf)
        })
        .collect();
    for release in artifact_install::prune_retired_releases(&runtime_install_root(), &keep) {
        append_install_log(&format!("removed retired runtime {}", release.display()));
    }
    Ok(())
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn emit_runtime_install_progress(
    app: &AppHandle,
    stage: &str,
    component: &str,
    label: &str,
    progress: u64,
    downloaded_bytes: Option<u64>,
    total_bytes: Option<u64>,
    eta_seconds: Option<u64>,
    throughput_bytes_per_second: Option<u64>,
) {
    let detail = match (downloaded_bytes, total_bytes) {
        (Some(downloaded), Some(total)) if total > 0 => format!(
            "{label}: {} MB of {} MB",
            downloaded / (1024 * 1024),
            total.div_ceil(1024 * 1024)
        ),
        _ => label.to_owned(),
    };
    append_install_log(&detail);
    emit_log(app, &detail);
    let shell: State<Shell> = app.state();
    let snapshot = {
        let mut ui = shell.ui.lock().unwrap();
        ui.setup = true;
        ui.phase = label.to_owned();
        ui.phase_key = stage.to_owned();
        ui.component = component.to_owned();
        ui.progress = progress;
        ui.status = detail;
        ui.downloaded_bytes = downloaded_bytes;
        ui.total_bytes = total_bytes;
        ui.eta_seconds = eta_seconds;
        ui.throughput_bytes_per_second = throughput_bytes_per_second;
        ui.clone()
    };
    let _ = app.emit("lemma:state", snapshot);
}

pub(crate) fn emit_runtime_install_error(app: &AppHandle, message: &str) {
    let shell: State<Shell> = app.state();
    let snapshot = {
        let mut ui = shell.ui.lock().unwrap();
        ui.setup = true;
        ui.phase = "Local runtime setup".into();
        ui.phase_key = "runtime-install".into();
        ui.status = message.to_owned();
        ui.downloaded_bytes = None;
        ui.total_bytes = None;
        ui.throughput_bytes_per_second = None;
        ui.error = true;
        ui.error_code = "runtime-install-failed".into();
        ui.ready = false;
        ui.running = false;
        ui.active_operation_id.clear();
        ui.clone()
    };
    let _ = app.emit("lemma:state", snapshot);
    show_splash(app);
}

pub(crate) fn prepare_runtime_impl(app: AppHandle) -> Result<(), String> {
    if current_mode(&app) != "local" {
        return Err("choose the local workspace before preparing its runtime".into());
    }
    ensure_locald(&app)?;
    send_to_locald(
        &app,
        json!({"cmd":"runtime.prepare", "id":"shell-runtime-prepare"}),
    )
}

/// The Postgres major this installation's data was created with, if recorded.
pub(crate) fn installed_postgres_major() -> Option<u64> {
    read_config()
        .pointer("/installedRuntime/dataCompatibility/postgres_major")
        .and_then(Value::as_u64)
}

/// Where each platform keeps the disk holding this installation's databases.
///
/// macOS has a sparse `data.raw`; Windows has the WSL distribution's
/// `ext4.vhdx` under `runtime/wsl`. The Windows path was written as
/// `runtime/windows`, which nothing creates -- so on Windows this answered "no
/// data" for a real installation, and only the config check kept the update
/// guard honest.
pub(crate) fn managed_data_disk() -> std::path::PathBuf {
    if cfg!(windows) {
        locald_root().join("runtime/wsl/ext4.vhdx")
    } else {
        locald_root().join("runtime/macos/data.raw")
    }
}

pub(crate) fn has_local_runtime_data() -> bool {
    configured_runtime(&read_config(), "installedRuntime").is_some() || managed_data_disk().exists()
}

pub(crate) fn ensure_update_preserves_data(
    reset_requested: bool,
    has_runtime: bool,
    compatibility: &str,
    windows: bool,
) -> Result<(), String> {
    if reset_requested {
        return Err("Updates never reset local data. Factory reset is a separate destructive action in recovery.".into());
    }
    if has_runtime && (windows || compatibility != "compatible") {
        return Err("This update has no supported data-preserving migration for this installation. Your current version and data have been kept. Wait for a compatible update.".into());
    }
    Ok(())
}

pub(crate) fn repair_runtime_impl(app: AppHandle) -> Result<(), String> {
    if current_mode(&app) != "local" {
        return Err("runtime repair is available only for a local workspace".into());
    }
    let shell: State<Shell> = app.state();
    let _install_guard = shell.runtime_install.lock().unwrap();
    require_no_recovery(&shell)?;
    let config = read_config();
    if config
        .pointer("/installedRuntime/release")
        .and_then(Value::as_str)
        != Some(env!("CARGO_PKG_VERSION"))
    {
        return Err(
            "this retained runtime cannot be repaired with the current signed manifest".into(),
        );
    }
    emit_runtime_install_progress(
        &app,
        "repair",
        "runtime",
        "Preparing a verified replacement runtime",
        1,
        None,
        None,
        None,
        None,
    );
    prepare_runtime_artifacts(&app, true)?;
    drop(_install_guard);
    start_after_runtime_maintenance(&app, "shell-start-after-runtime-repair")
}

#[tauri::command]
/// Runs off the UI thread. A synchronous `#[tauri::command]` is dispatched on
/// the main thread, so any command that waits on the daemon, the network or a
/// child process freezes the window for its whole duration -- which is how a
/// first launch showed a black, unresponsive app for minutes while the runtime
/// installed and the daemon came up.
pub(crate) async fn prepare_runtime(window: Webview, app: AppHandle) -> Result<(), String> {
    require_local_native_window(&window)?;
    tauri::async_runtime::spawn_blocking(move || prepare_runtime_impl(app))
        .await
        .map_err(|error| error.to_string())?
}

#[tauri::command]
/// Runs off the UI thread. A synchronous `#[tauri::command]` is dispatched on
/// the main thread, so any command that waits on the daemon, the network or a
/// child process freezes the window for its whole duration -- which is how a
/// first launch showed a black, unresponsive app for minutes while the runtime
/// installed and the daemon came up.
pub(crate) async fn repair_runtime(window: Webview, app: AppHandle) -> Result<(), String> {
    require_control_window(&window)?;
    tauri::async_runtime::spawn_blocking(move || repair_runtime_impl(app))
        .await
        .map_err(|error| error.to_string())?
}
