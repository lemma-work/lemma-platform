use super::*;

/// Say so when a menu action fails.
///
/// Every arm of `handle_menu_action` used to discard its `Result`, so a menu
/// item could not report a failure even in principle: a local start that spent
/// ninety seconds failing to reach the private runtime, or an Agent Host
/// command that never left the shell, both looked exactly like a dead button.
/// The daemon wrote the reason to its log and the person pressing the item was
/// told nothing.
pub(crate) fn report_action_failure(app: &AppHandle, action: &str, error: &str) {
    append_bounded_log(
        &launch_log_path(),
        &format!("menu {action} failed: {error}"),
    );
    let handle = app.clone();
    let title = action.to_owned();
    let message = error.to_owned();
    std::thread::spawn(move || {
        let _ = show_app_prompt(handle, title, message, "Close".into(), false);
    });
}

/// Run a menu action, and surface whatever it has to say about failing.
pub(crate) fn menu_attempt(
    app: &AppHandle,
    action: &str,
    work: impl FnOnce() -> Result<(), String>,
) {
    if let Err(error) = work() {
        report_action_failure(app, action, &error);
    }
}

/// Run a menu action that waits on something, without freezing the app.
///
/// Menu and tray handlers are called on the main thread, so a verb that talks
/// to the daemon from one blocks every window for as long as it takes -- the
/// same failure the Tauri commands had, reached through the tray instead of the
/// page. Starting Lemma from the tray could freeze the app for the length of a
/// runtime install.
///
/// Failure still surfaces the same way: `report_action_failure` opens a dialog,
/// and that dispatches to the main thread on its own, so it is safe from here.
pub(crate) fn menu_background(
    app: &AppHandle,
    action: &'static str,
    work: impl FnOnce() -> Result<(), String> + Send + 'static,
) {
    let handle = app.clone();
    std::thread::spawn(move || {
        if let Err(error) = work() {
            report_action_failure(&handle, action, &error);
        }
    });
}

/// Every menu action, wherever it was invoked from.
///
/// The app menu and the tray now name the same verbs, so routing them through
/// one function is what keeps them from drifting into two different products'
/// worth of behaviour.
pub(crate) fn handle_menu_action(app: &AppHandle, id: &str) {
    let app = app.clone();
    match id {
        "open" | "home" => {
            let handle = app.clone();
            menu_attempt(&handle, "Open Lemma", || open_app_impl(app).map(|_| ()));
        }
        "login" => {
            tauri::async_runtime::spawn(async move {
                let _ = login(app, Some("signin".into())).await;
            });
        }
        "back" => {
            if let Some(window) = app.get_webview("main") {
                let _ = window.eval("window.history.back()");
            }
        }
        "forward" => {
            if let Some(window) = app.get_webview("main") {
                let _ = window.eval("window.history.forward()");
            }
        }
        "reload" => {
            if let Some(window) = app.get_webview("main") {
                let _ = window.eval("window.location.reload()");
            }
        }
        "start" => {
            let handle = app.clone();
            menu_background(&app, "Start Lemma", move || start_impl(handle).map(|_| ()));
        }
        "stop" => {
            let handle = app.clone();
            menu_background(&app, "Stop Lemma", move || {
                stop_impl(handle, Some(false)).map(|_| ())
            });
        }
        "stop-all" => {
            let handle = app.clone();
            menu_background(&app, "Stop Lemma completely", move || {
                stop_impl(handle, Some(true)).map(|_| ())
            });
        }
        "restart" => {
            let handle = app.clone();
            menu_background(&app, "Restart Lemma", move || {
                restart_impl(handle).map(|_| ())
            });
        }
        "mode" => {
            confirm_then_switch_connection(app);
        }
        "autostart" => {
            // Reading and writing a launch-agent plist.
            let handle = app.clone();
            menu_background(&app, "Open Lemma at login", move || {
                let autolaunch = handle.autolaunch();
                if autolaunch.is_enabled().unwrap_or(false) {
                    let _ = autolaunch.disable();
                } else {
                    let _ = autolaunch.enable();
                }
                Ok(())
            });
        }
        "control" => {
            menu_attempt(&app, "Local settings", || show_control_center(&app));
        }
        "control-ai" => {
            let _ = show_control_center_page(&app, Some("ai"));
        }
        "control-sharing" => {
            let _ = show_control_center_page(&app, Some("sharing"));
        }
        "diagnostics" => {
            let _ = show_control_center_page(&app, Some("diagnostics"));
        }
        "recovery" => {
            let _ = show_control_center_page(&app, Some("recovery"));
        }
        "agent-host-log" => {
            menu_background(&app, "Open Agent Host log", || {
                reveal_path(&agent_host_log_path())
            });
        }
        "logs" => {
            menu_background(&app, "Open logs", open_logs_impl);
        }
        "docs" => {
            menu_background(&app, "Open documentation", || {
                open_external(&format!("{}/docs", hosted_url().trim_end_matches('/')));
                Ok(())
            });
        }
        "devtools" => {
            if let Some(window) = app.get_webview("main") {
                window.open_devtools();
                let _ = window.window().show();
                let _ = window.set_focus();
            }
        }
        "quit" => {
            request_quit(&app);
        }
        _ => {}
    }
}

/// The macOS menu bar.
///
/// Until now there was none: the shipped build used Tauri's default (app name /
/// Edit / View / Window / Help) and every Lemma verb lived in the tray, which
/// meant no ⌘, for settings and no discoverable way to do anything without
/// going to the menu bar extra. The names here are the product's, not the
/// supervisor's: Stop Lemma completely, rather than the old Stop Services and
/// Infra.
pub(crate) fn build_app_menu(app: &AppHandle) -> tauri::Result<Menu<tauri::Wry>> {
    let local = connection_mode() == "local";
    let about = PredefinedMenuItem::about(
        app,
        Some("About Lemma"),
        Some(AboutMetadata {
            name: Some("Lemma".into()),
            version: Some(env!("CARGO_PKG_VERSION").into()),
            website: Some("https://lemma.work".into()),
            ..Default::default()
        }),
    )?;
    let settings = MenuItem::with_id(
        app,
        "control",
        "Desktop settings…",
        true,
        Some("CmdOrCtrl+,"),
    )?;
    let connection = MenuItem::with_id(app, "mode", "Connection…", true, None::<&str>)?;
    let recovery = MenuItem::with_id(app, "recovery", "Recovery…", true, None::<&str>)?;

    // Services / Hide / Hide Others / Show All are AppKit application-menu
    // conventions. muda will happily construct them elsewhere, where they
    // become inert rows — dead entries in a Windows menu — so they are built
    // only where they mean something.
    #[cfg(target_os = "macos")]
    let platform_items: Vec<Box<dyn tauri::menu::IsMenuItem<tauri::Wry>>> = vec![
        Box::new(PredefinedMenuItem::services(app, None)?),
        Box::new(PredefinedMenuItem::separator(app)?),
        Box::new(PredefinedMenuItem::hide(app, None)?),
        Box::new(PredefinedMenuItem::hide_others(app, None)?),
        Box::new(PredefinedMenuItem::show_all(app, None)?),
        Box::new(PredefinedMenuItem::separator(app)?),
    ];
    #[cfg(not(target_os = "macos"))]
    let platform_items: Vec<Box<dyn tauri::menu::IsMenuItem<tauri::Wry>>> = Vec::new();

    let mut lemma_items: Vec<&dyn tauri::menu::IsMenuItem<tauri::Wry>> = Vec::new();
    let lemma_separator = PredefinedMenuItem::separator(app)?;
    // Deliberately app-owned rather than AppKit's predefined quit, which is
    // `terminate:` and cannot be intercepted. Quit stops the local server, and
    // that is worth one sentence first, so the app has to own ⌘Q.
    let quit = MenuItem::with_id(app, "quit", "Quit Lemma", true, Some("CmdOrCtrl+Q"))?;
    lemma_items.push(&about);
    lemma_items.push(&lemma_separator);
    lemma_items.push(&settings);
    lemma_items.push(&recovery);
    lemma_items.push(&connection);
    lemma_items.push(&lemma_separator);
    lemma_items.extend(platform_items.iter().map(|item| item.as_ref()));
    lemma_items.push(&quit);

    let lemma_menu = Submenu::with_items(app, "Lemma", true, &lemma_items)?;

    let edit_menu = Submenu::with_items(
        app,
        "Edit",
        true,
        &[
            &PredefinedMenuItem::undo(app, None)?,
            &PredefinedMenuItem::redo(app, None)?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::cut(app, None)?,
            &PredefinedMenuItem::copy(app, None)?,
            &PredefinedMenuItem::paste(app, None)?,
            &PredefinedMenuItem::select_all(app, None)?,
        ],
    )?;

    let view_menu = Submenu::with_items(
        app,
        "View",
        true,
        &[
            &MenuItem::with_id(app, "home", "Lemma Home", true, Some("Shift+CmdOrCtrl+H"))?,
            &MenuItem::with_id(app, "back", "Back", true, Some("CmdOrCtrl+["))?,
            &MenuItem::with_id(app, "forward", "Forward", true, Some("CmdOrCtrl+]"))?,
            &MenuItem::with_id(app, "reload", "Reload", true, Some("CmdOrCtrl+R"))?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::fullscreen(app, None)?,
            &PredefinedMenuItem::separator(app)?,
            // Enabled only in a development build. Shipping a web inspector in
            // the top-level View menu, on Cmd-Alt-I, invites a stranger into a
            // surface that talks to the workspace over IPC -- and there is
            // nothing there for them. Diagnostics is the supported path, and
            // Troubleshoot still carries this for anyone who needs it.
            &MenuItem::with_id(
                app,
                "devtools",
                "Developer Tools",
                cfg!(debug_assertions),
                Some("CmdOrCtrl+Alt+I"),
            )?,
        ],
    )?;

    let window_menu = Submenu::with_items(
        app,
        "Window",
        true,
        &[
            &PredefinedMenuItem::minimize(app, None)?,
            &PredefinedMenuItem::close_window(app, None)?,
        ],
    )?;

    // Troubleshooting lives under Help rather than at the top level because
    // starting and stopping services is what you do when something is wrong,
    // not part of using Lemma.
    let help_menu = Submenu::with_items(
        app,
        "Help",
        true,
        &[
            &MenuItem::with_id(app, "docs", "Lemma Docs", true, None::<&str>)?,
            &PredefinedMenuItem::separator(app)?,
            &MenuItem::with_id(app, "diagnostics", "Diagnostics…", local, None::<&str>)?,
            &MenuItem::with_id(app, "recovery", "Recovery…", true, None::<&str>)?,
            &MenuItem::with_id(app, "logs", "Open Logs", local, None::<&str>)?,
            &PredefinedMenuItem::separator(app)?,
            &MenuItem::with_id(app, "start", "Start Lemma", local, None::<&str>)?,
            &MenuItem::with_id(app, "restart", "Restart Lemma", local, None::<&str>)?,
            &MenuItem::with_id(app, "stop", "Stop Lemma", local, None::<&str>)?,
            &MenuItem::with_id(
                app,
                "stop-all",
                "Stop the local server",
                local,
                None::<&str>,
            )?,
        ],
    )?;

    Menu::with_items(
        app,
        &[
            &lemma_menu,
            &edit_menu,
            &view_menu,
            &window_menu,
            &help_menu,
        ],
    )
}

pub(crate) fn build_tray(app: &AppHandle) -> tauri::Result<()> {
    let menu = build_tray_menu(app)?;
    TrayIconBuilder::with_id("lemma-tray")
        .icon(tauri::include_image!("icons/tray-icon.png"))
        .icon_as_template(false)
        .menu(&menu)
        .show_menu_on_left_click(true)
        // No handler here. `app.on_menu_event` in setup already receives menu
        // events from every menu this app owns, the tray's included, so
        // registering a second one meant every tray verb ran twice -- two
        // confirmation dialogs stacked on each other, two stops, two restarts.
        .build(app)?;
    Ok(())
}

pub(crate) fn build_tray_menu(app: &AppHandle) -> tauri::Result<Menu<tauri::Wry>> {
    let local = connection_mode() == "local";
    // A glance and a few verbs. This menu used to carry eighteen items of
    // supervisor vocabulary — starting and stopping named services, switching
    // connection mode — which is a maintainer's console, not the thing you
    // reach for from the menu bar. Everything operational moved into
    // Troubleshoot; everything standard moved into the app menu.
    let status_item =
        MenuItem::with_id(app, "tray-state", "Lemma: checking…", false, None::<&str>)?;
    let open_item = MenuItem::with_id(app, "open", "Open Lemma", true, None::<&str>)?;
    let login_item = MenuItem::with_id(app, "login", "Log In…", true, None::<&str>)?;
    let control_item = MenuItem::with_id(app, "control", "Desktop settings…", true, None::<&str>)?;

    // Disabled: a label, not an action. The tray is where you glance at whether
    // this computer is currently able to run coding agents.
    let agent_host_state_item = MenuItem::with_id(
        app,
        "agent-host-state",
        "Agent Host: checking…",
        false,
        None::<&str>,
    )?;
    {
        let shell: State<Shell> = app.state();
        *shell.tray_agent_host.lock().unwrap() = Some(agent_host_state_item.clone());
        *shell.tray_status.lock().unwrap() = Some(status_item.clone());
    }

    let autostart_enabled = app.autolaunch().is_enabled().unwrap_or(false);
    let troubleshoot = Submenu::with_items(
        app,
        "Troubleshoot",
        true,
        &[
            &MenuItem::with_id(app, "start", "Start Lemma", local, None::<&str>)?,
            &MenuItem::with_id(app, "restart", "Restart Lemma", local, None::<&str>)?,
            &MenuItem::with_id(app, "stop", "Stop Lemma", local, None::<&str>)?,
            &MenuItem::with_id(
                app,
                "stop-all",
                "Stop the local server",
                local,
                None::<&str>,
            )?,
            &PredefinedMenuItem::separator(app)?,
            &MenuItem::with_id(app, "diagnostics", "Diagnostics…", local, None::<&str>)?,
            &MenuItem::with_id(app, "logs", "Open Logs", local, None::<&str>)?,
            &MenuItem::with_id(
                app,
                "agent-host-log",
                "Open Agent Host Log",
                true,
                None::<&str>,
            )?,
            &PredefinedMenuItem::separator(app)?,
            &MenuItem::with_id(app, "reload", "Reload", true, None::<&str>)?,
            &MenuItem::with_id(
                app,
                "devtools",
                "Developer Tools",
                cfg!(debug_assertions),
                None::<&str>,
            )?,
            &PredefinedMenuItem::separator(app)?,
            &MenuItem::with_id(app, "mode", "Connection…", true, None::<&str>)?,
            &CheckMenuItem::with_id(
                app,
                "autostart",
                "Start at Login",
                true,
                autostart_enabled,
                None::<&str>,
            )?,
        ],
    )?;

    let menu = Menu::with_items(
        app,
        &[
            &status_item,
            &PredefinedMenuItem::separator(app)?,
            &open_item,
            &login_item,
            &control_item,
            &PredefinedMenuItem::separator(app)?,
            &agent_host_state_item,
            &PredefinedMenuItem::separator(app)?,
            &troubleshoot,
            &PredefinedMenuItem::separator(app)?,
            &MenuItem::with_id(app, "quit", "Quit Lemma", true, None::<&str>)?,
        ],
    )?;

    Ok(menu)
}

/// Keep the tray's status line honest about the stack.
///
/// The Agent Host already had a glanceable line; the stack itself did not, so
/// "is Lemma actually up?" meant opening the app to find out. Hosted mode has
/// no local stack to report on and says so instead of inventing a state.
pub(crate) fn refresh_tray_status(app: &AppHandle) {
    let shell: State<Shell> = app.state();
    let label = {
        let ui = shell.ui.lock().unwrap();
        if ui.mode != "local" {
            "Lemma Cloud".to_string()
        } else if ui.error {
            "Lemma: needs attention".to_string()
        } else if ui.ready {
            "Lemma: running".to_string()
        } else if ui.running || !ui.phase.is_empty() {
            "Lemma: starting…".to_string()
        } else {
            "Lemma: stopped".to_string()
        }
    };
    let item = shell.tray_status.lock().unwrap().clone();
    if let Some(item) = item {
        let _ = item.set_text(label);
    }
}
