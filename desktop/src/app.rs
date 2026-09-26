use super::*;

pub(crate) fn run() {
    LAUNCH_START.get_or_init(Instant::now);
    let recovery_launch = std::env::args().any(|argument| argument == "--recovery")
        || app_support_dir().join("recovery-mode").is_file();
    let mode = if recovery_launch {
        "undecided".into()
    } else {
        connection_mode()
    };
    launch_trace(&format!("process start, mode={mode}"));

    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, argv, _cwd| {
            for argument in argv {
                if let Ok(url) = tauri::Url::parse(&argument) {
                    handle_deep_link(app, &url);
                }
            }
            if let Some(window) = app.get_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            None,
        ))
        .plugin(tauri_plugin_deep_link::init())
        // For the Rust API, not the JavaScript one — same reasoning as the note
        // about `tauri-plugin-process` below. No webview is granted its
        // commands: the folder picker works precisely because the *shell* is
        // what asks, so a person's click in a native dialog is the consent that
        // lets an agent work in their project. A granted JS surface would let a
        // page raise one on its own, which is the property being protected.
        .plugin(tauri_plugin_dialog::init())
        // Deliberately not `tauri-plugin-process` alongside it. That plugin
        // exists to expose `relaunch` to JavaScript; the flow here is driven
        // from Rust and `AppHandle::restart()` is core, so adding it would
        // widen the ACL for nothing.
        .plugin(
            tauri_plugin_updater::Builder::new()
                .default_version_comparator(|current, update| {
                    update_policy::candidate_allowed(release_channel(), &current, &update.version)
                })
                .build(),
        )
        .manage({
            let shell = Shell::new(mode.clone());
            shell
                .recovery_mode
                .store(recovery_launch, Ordering::Release);
            shell
        })
        .invoke_handler(tauri::generate_handler![
            stack_control::start,
            stack_control::stop,
            stack_control::restart,
            stack_control::open_app,
            stack_control::open_logs,
            diagnostics::diagnostic_logs,
            connection::choose_connection_mode,
            connection::set_connection_mode,
            state::get_state,
            connection::login,
            control_center::open_control_center,
            runtime_setup::prepare_runtime,
            runtime_layout::runtime_info,
            runtime_setup::repair_runtime,
            operator_settings::control_snapshot,
            agent_host_ui::agent_host_action,
            agent_host_ui::agent_host_status,
            agent_host_ui::sandbox_image_status,
            operator_settings::prepare_sandbox_image,
            conversation_folders::conversation_folder,
            conversation_folders::bind_conversation_folder,
            conversation_folders::unbind_conversation_folder,
            conversation_folders::adopt_conversation_folder,
            agent_host_ui::agent_host_start,
            agent_host_ui::agent_host_pair,
            agent_host_ui::agent_host_session,
            agent_host_ui::agent_host_refresh,
            agent_host_ui::agent_host_open_log,
            agent_host_ui::agent_host_own_settings,
            operator_settings::discover_provider_models,
            operator_settings::configure_ai_provider,
            pod_app_alias::app_frame_url,
            operator_settings::sharing_action,
            operator_settings::close_local_settings,
            prompts::confirm_destructive_action,
            prompts::resolve_confirmation,
            diagnostics::open_developer_tools,
            local_recovery::local_recovery_options,
            telemetry::telemetry_status,
            telemetry::set_telemetry_enabled,
            local_recovery::reset_local_data,
            local_recovery::reset_full_reinstall,
            local_recovery::restart_into_recovery,
            app_update::check_for_app_update,
            app_update::install_app_update,
            workspace_settings::local_settings_snapshot,
            workspace_settings::apply_local_settings,
            workspace_settings::local_sharing,
            workspace_settings::set_start_at_login,
            workspace_settings::set_host_execution
        ])
        .setup(move |app| setup(app, &mode, recovery_launch))
        .on_window_event(on_window_event)
        .build(tauri::generate_context!())
        .expect("error while building Lemma desktop")
        .run(on_run_event);
}

fn setup(
    app: &mut tauri::App,
    mode: &str,
    recovery_launch: bool,
) -> Result<(), Box<dyn std::error::Error>> {
    let handle = app.handle().clone();

    // Before anything is started, recorded or reclaimed under this path.
    // Release builds only: a development build runs from target/.
    if !cfg!(debug_assertions) {
        if let Some(problem) = std::env::current_exe()
            .ok()
            .and_then(|exe| launch_location_problem(&exe))
        {
            use tauri_plugin_dialog::DialogExt;
            append_install_log(&format!("launch refused: {problem}"));
            app.dialog()
                .message(problem)
                .title("Move Lemma to Applications")
                .show(|_| std::process::exit(0));
            return Ok(());
        }
    }

    // Before anything else reads a version: an update that did not finish is
    // the reason this launch is on the version it is on.
    reconcile_update_attempt(&handle);

    if !recovery_launch && mode == "hosted" && agent_host_wants_to_run() {
        // "Runs while Lemma is open" has to hold for a cloud workspace too,
        // and locald is what supervises the sidecar. An unpaired or
        // switched-off machine still gets no daemon at all.
        let handle = handle.clone();
        std::thread::spawn(move || {
            let _ = ensure_locald_without_host_pack(&handle);
        });
    }

    if let Some(capability) = overridden_workspace_capability() {
        // capabilities/workspace.json can only name the shipped origins. A dev
        // or self-hosted build points the workspace somewhere else through
        // these variables, and its Local settings button would otherwise be
        // rejected by an ACL that has never heard of it.
        app.add_capability(capability)?;
    }

    let resume = resume_attempt(mode);
    if let Some(target) = resume.as_ref() {
        // Before the window opens straight onto it.
        grant_local_workspace_capability(&handle, &target.url);
    }
    // Cold means this launch found nothing already serving and has to bring
    // the stack up. It is the launch that can go wrong, and the one whose
    // duration is worth knowing.
    telemetry::note(telemetry::InstallEvent::Launched {
        cold: resume.is_none(),
    });

    build_main_window(&handle, mode, initial_url(mode, resume.as_ref()), true)?;
    launch_trace("window shown");

    // After tao has installed its application delegate, which `build` did.
    install_os_quit_handler(&handle);

    app.set_menu(build_app_menu(&handle)?)?;
    app.on_menu_event(|app, event| handle_menu_action(app, event.id().as_ref()));

    build_tray(&handle)?;
    refresh_tray_status(&handle);

    // Local mode: connect to the durable daemon immediately so splash has a
    // live event stream the moment it loads. Either way on a worker: nothing
    // here may hold up a window the user can already see.
    if !recovery_launch && mode == "local" {
        let handle = handle.clone();
        match resume {
            Some(target) => {
                seed_resumed_state(&handle, &target);
                std::thread::spawn(move || reconnect_after_resume(&handle, &target.url));
            }
            None => {
                std::thread::spawn(move || connect_on_launch(&handle));
            }
        }
    }
    if recovery_launch {
        let _ = show_control_center_page(&handle, Some("recovery"));
    } else if std::env::var("LEMMA_DESKTOP_OPEN_CONTROL").as_deref() == Ok("1") {
        let _ = show_control_center(&handle);
    }
    Ok(())
}

/// Optimistic resume. Everything the daemon does on a warm launch is
/// reconciliation of a stack that never stopped, so the splash and the
/// navigation that follows it are pure latency. Ask the recorded workspace
/// whether it is still serving, and if it answers with the generation we left
/// it on, open it directly.
///
/// Failure is cheap and total: a miss costs RESUME_PROBE_TIMEOUT and lands on
/// exactly the splash path this replaced.
fn resume_attempt(mode: &str) -> Option<ResumeTarget> {
    let resume = (mode == "local")
        .then(read_resume_target)
        .flatten()
        // Parsed before the probe, not after: this comes out of a
        // user-writable config file, and a launch that panicked on a
        // hand-edited route would be a far worse failure than a slow one. An
        // unparseable target simply is not a resume.
        .filter(|target| resume_entry_url(target).parse::<tauri::Url>().is_ok())
        .filter(resume_target_is_serving);
    launch_trace(if mode != "local" {
        "resume: skipped (hosted)"
    } else if resume.is_some() {
        "resume: hit, opening the workspace directly"
    } else {
        "resume: miss, falling back to the splash"
    });
    resume
}

fn initial_url(mode: &str, resume: Option<&ResumeTarget>) -> WebviewUrl {
    if mode == "hosted" {
        return hosted_entry_url(&hosted_url());
    }
    // Parseability was established by `resume_attempt`, and anything that
    // somehow is not parseable gets the splash rather than a panic.
    resume
        .and_then(|target| resume_entry_url(target).parse().ok())
        .map_or_else(
            || WebviewUrl::App("index.html".into()),
            WebviewUrl::External,
        )
}

/// The workspace is already on screen and already answering. Seed the state
/// the shell would otherwise learn from the `ready` event -- Local settings,
/// the tray, and the navigation ACL all read `ui.url` -- before reconciling
/// with the daemon on a worker.
fn seed_resumed_state(handle: &AppHandle, target: &ResumeTarget) {
    let shell: State<Shell> = handle.state();
    let mut ui = shell.ui.lock_or_recover();
    ui.url = target.url.clone();
    ui.api_url = target.api_url.clone();
    ui.running = true;
    ui.ready = true;
}

/// Bring the daemon up behind a workspace that resumed without it.
///
/// The only way from here to the splash is `stand_down`, which is what clears
/// the optimistic state `seed_resumed_state` wrote.
fn reconnect_after_resume(handle: &AppHandle, resumed_url: &str) {
    cookie_migration::migrate_session_cookies(handle);
    if let Err(error) = ensure_locald(handle) {
        // The stack is serving but the daemon is not reachable, so the shell
        // cannot supervise it. Say so on the splash rather than leaving a
        // workspace that silently has no controls behind it.
        stand_down(handle, Some(error));
        return;
    }
    if let Err(error) = start_impl(handle.clone()) {
        stand_down(handle, Some(error));
        return;
    }
    // Connecting can itself invalidate what the window is showing: a daemon
    // that does not match this release is replaced, and everything comes back
    // on new ports. The `ready` that follows will navigate there, but until it
    // arrives the window is pointed at a port nothing is listening on. Not a
    // failure -- but `ready` still points at the old ports, so it has to come
    // down here too, or the splash re-opens the stale URL before the real
    // `ready` event arrives.
    if !resume_still_serving(handle, resumed_url) {
        stand_down(handle, None);
    }
}

/// Take back the optimistic resume and show the splash.
fn stand_down(handle: &AppHandle, failure: Option<String>) {
    if let Some(error) = failure.as_deref() {
        eprintln!("[desktop-resume] {error}");
    }
    let shell: State<Shell> = handle.state();
    let snapshot = {
        let mut ui = shell.ui.lock_or_recover();
        stand_down_state(&mut ui, failure);
        ui.clone()
    };
    let _ = handle.emit("lemma:state", snapshot);
    show_splash(handle);
}

/// The seed told the shell this workspace was up. Any path that does not
/// confirm that has to take it back: the splash reads `ready` on load and
/// navigates straight to `ui.url` if it is set and there is no error, so
/// handing it the splash while the state still claims success just bounces the
/// user back to the workspace they were rescued from -- against a port nothing
/// is listening on, in a loop.
pub(crate) fn stand_down_state(ui: &mut UiState, failure: Option<String>) {
    ui.ready = false;
    if let Some(error) = failure {
        ui.running = false;
        ui.error = true;
        ui.error_code = "resume-failed".into();
        ui.status = error;
    }
}

/// A cold launch: install the runtime if needed and start the stack.
///
/// `ensure_locald` installs the runtime artifacts before it can spawn
/// anything, which on a first run or an upgrade is an unpack of hundreds of
/// megabytes, and it then waits up to LOCALD_START_BUDGET for the daemon to
/// answer. Inside `setup`, before the event loop pumps, that would freeze the
/// splash for the whole install with no way to tell it from a hang.
fn connect_on_launch(handle: &AppHandle) {
    // Cookies first: the workspace is only navigated to once locald reports
    // ready, which is after `start_impl` below.
    cookie_migration::migrate_session_cookies(handle);
    let failure = match ensure_locald(handle) {
        Err(error) => Some((error, None)),
        Ok(_) => start_impl(handle.clone())
            .err()
            .map(|error| (error, Some("startup-request-failed"))),
    };
    let Some((error, code)) = failure else {
        return;
    };
    let shell: State<Shell> = handle.state();
    let snapshot = {
        let mut ui = shell.ui.lock_or_recover();
        launch_failure_state(&mut ui, error, code);
        ui.clone()
    };
    let _ = handle.emit("lemma:state", snapshot);
}

/// A failed daemon connection is shown as an error on the splash; a failed
/// start request is also no longer ready.
pub(crate) fn launch_failure_state(ui: &mut UiState, error: String, code: Option<&str>) {
    ui.error = true;
    ui.status = error;
    if let Some(code) = code {
        ui.error_code = code.into();
        ui.ready = false;
    }
}

fn on_window_event(window: &tauri::Window, event: &tauri::WindowEvent) {
    match event {
        tauri::WindowEvent::CloseRequested { api, .. } => {
            // Only the window Lemma runs in hides to the tray. This handler is
            // registered on the builder, so it sees *every* window: without
            // the guard, closing a pod app window prevented its own close and
            // hid it, leaving an app the user could neither see nor get rid of.
            if window.label() != "main" {
                return;
            }
            // Hide to tray; services keep running. Hidden first: the route is
            // still readable from a hidden webview, and the user asked for the
            // window to go away now. Then record where the user was on the way
            // out -- closing the window is the most common way a session ends,
            // and it is the last chance to read the route off a live webview.
            api.prevent_close();
            window.app_handle().state::<Shell>().confirmations.cancel();
            remove_confirmation_overlay(window.app_handle());
            let _ = window.hide();
            remember_workspace_route(window.app_handle());
            // ...and leave the Dock, which is the half that makes this read as
            // "closed" rather than "still open but blank". A hidden window
            // under a live Dock icon is what makes people reach for Force
            // Quit -- the icon says the app is running and clicking it appears
            // to do nothing. Docker Desktop drops to the menu bar here and so
            // do we; the tray keeps an "Open Lemma" item, so there is still a
            // way back.
            #[cfg(target_os = "macos")]
            settle_dock_presence(window.app_handle());
        }
        // A pod app window going away can be the last thing on screen, and
        // closing it is an ordinary close -- so the Dock is settled here too
        // rather than only when the workspace hides.
        #[cfg(target_os = "macos")]
        tauri::WindowEvent::Destroyed => {
            settle_dock_presence(window.app_handle());
        }
        // Belt and braces for the Dock icon. Every deliberate way back calls
        // `restore_dock_presence`, but a window that has focus and no Dock
        // icon is a state nothing should be able to reach, and this costs one
        // idempotent call to guarantee it.
        #[cfg(target_os = "macos")]
        tauri::WindowEvent::Focused(true) if window.label() == "main" => {
            restore_dock_presence(window.app_handle());
        }
        _ => {}
    }
}

fn on_run_event(app: &AppHandle, event: tauri::RunEvent) {
    match event {
        #[cfg(target_os = "macos")]
        tauri::RunEvent::Opened { urls } => {
            for url in urls {
                handle_deep_link(app, &url);
            }
        }
        // Clicking the Dock icon with every window closed. macOS-only: the
        // variant does not exist on other platforms.
        #[cfg(target_os = "macos")]
        tauri::RunEvent::Reopen { .. } => reopen_from_dock(app),
        // Dock → Quit and any other OS-issued terminate arrive here without
        // passing a menu, so the prompt is armed here rather than only on the
        // items the app draws itself. Fail-safe by construction: an exit is
        // only ever held once, and only when there is something running to say
        // so about.
        tauri::RunEvent::ExitRequested { api, code, .. } => {
            let shell: State<Shell> = app.state();
            match exit_disposition(
                code == Some(tauri::RESTART_EXIT_CODE),
                shell.swapping_window.load(Ordering::Acquire),
                shell.shutdown.may_exit(),
                shell.quit_confirmed.load(Ordering::Acquire),
            ) {
                ExitDisposition::Allow => {}
                ExitDisposition::Hold => api.prevent_exit(),
                ExitDisposition::Quit => {
                    api.prevent_exit();
                    request_quit(app);
                }
            }
        }
        tauri::RunEvent::Exit => {
            // Cleanup belongs to the worker admitted by ExitRequested. At this
            // point the event loop is leaving and must never wait for sockets,
            // process shutdown, or another main-thread task.
            app.state::<Shell>().confirmations.cancel();
        }
        _ => {}
    }
}

#[cfg(target_os = "macos")]
fn reopen_from_dock(app: &AppHandle) {
    restore_dock_presence(app);
    if let Some(window) = app.get_window("main") {
        let _ = window.show();
        let _ = window.set_focus();
        return;
    }
    // Every window closed, which on macOS leaves the app running. The Dock
    // icon is how a person brings it back, so with no window to show, one is
    // opened.
    let snapshot = {
        let shell: State<Shell> = app.state();
        let ui = shell.ui.lock_or_recover();
        ui.clone()
    };
    match reopen_target(
        &snapshot.mode,
        snapshot.ready,
        snapshot.error,
        &snapshot.url,
    ) {
        ReopenTarget::Hosted => {
            let _ = open_app_window(app, &hosted_url());
        }
        ReopenTarget::Workspace(url) => {
            let _ = open_app_window(app, &url);
        }
        ReopenTarget::Splash => show_splash(app),
    }
}
