use super::*;

pub(crate) fn connection_mode() -> String {
    if let Ok(mode) = std::env::var("LEMMA_DESKTOP_CONNECTION_MODE") {
        if mode == "hosted" || mode == "local" {
            return mode;
        }
    }
    configured_connection_mode(&read_config())
}

pub(crate) fn configured_connection_mode(config: &Value) -> String {
    if config["connectionModePromptRevision"].as_u64() != Some(CONNECTION_MODE_PROMPT_REVISION) {
        return "undecided".into();
    }
    match config["connectionMode"].as_str() {
        Some("hosted") => "hosted".into(),
        Some("local") => "local".into(),
        // First launch: the splash asks the user to choose.
        _ => "undecided".into(),
    }
}

pub(crate) fn hosted_url() -> String {
    // The capability that grants this origin the shell's commands is gated on
    // `dev_override`; the navigation has to be gated the same way, or a
    // release build would be sent to an environment-named host with no
    // permissions and no explanation.
    dev_override("LEMMA_DESKTOP_HOSTED_URL")
        .and_then(|value| value.into_string().ok())
        .unwrap_or_else(|| DEFAULT_HOSTED_URL.into())
}

/// The first URL a hosted launch opens.
///
/// `LEMMA_DESKTOP_HOSTED_URL` is read from the environment and nothing
/// validates it, so an unparseable value used to abort `setup` through an
/// `expect` -- before any window exists, which is a launch that shows nothing
/// and says nothing. The splash can say so instead.
pub(crate) fn hosted_entry_url(hosted: &str) -> WebviewUrl {
    match hosted.parse() {
        Ok(url) => WebviewUrl::External(url),
        Err(_) => WebviewUrl::App("index.html".into()),
    }
}

pub(crate) fn set_connection_mode_impl(app: AppHandle, mode: String) -> Result<(), String> {
    if mode != "local" && mode != "hosted" {
        return Err(format!("unknown mode {mode:?}"));
    }
    let _ = std::fs::remove_file(app_support_dir().join("recovery-mode"));
    app.state::<Shell>()
        .recovery_mode
        .store(false, Ordering::Release);
    set_mode(&app, &mode)?;
    if mode == "hosted" {
        return open_app_window(&app, &hosted_url());
    }
    ensure_locald(&app)?;
    let setup = std::env::var("LEMMA_DESKTOP_START_SETUP").as_deref() == Ok("1");
    send_local_operation(
        &app,
        json!({"cmd": "start", "setup": setup}),
        operation_id("shell-start"),
    )
}

pub(crate) fn choose_connection_mode_impl(app: AppHandle) -> Result<String, String> {
    let current = current_mode(&app);
    if current == "undecided" {
        show_splash(&app);
        return Ok(current);
    }
    let new_mode = if current == "local" {
        "hosted"
    } else {
        "local"
    };
    set_mode(&app, new_mode)?;
    if new_mode == "hosted" {
        open_app_window(&app, &hosted_url())?;
    } else {
        show_splash(&app);
        start_impl(app)?;
        return Ok(new_mode.into());
    }
    Ok(new_mode.into())
}

pub(crate) fn current_mode(app: &AppHandle) -> String {
    let shell: State<Shell> = app.state();
    let ui = shell.ui.lock().unwrap();
    ui.mode.clone()
}

pub(crate) fn set_mode(app: &AppHandle, mode: &str) -> Result<(), String> {
    write_config(|config| {
        config["connectionMode"] = json!(mode);
        config["connectionModePromptRevision"] = json!(CONNECTION_MODE_PROMPT_REVISION);
    })?;
    // Every path that changes the deployment choice comes through here, which
    // is why the event is here rather than in the three callers.
    telemetry::note(telemetry::InstallEvent::ModeSelected {
        local: mode == "local",
    });
    refresh_menus_for_connection_mode(app);
    let changed = {
        let shell: State<Shell> = app.state();
        let mut ui = shell.ui.lock().unwrap();
        let changed = ui.mode != mode;
        if changed && mode == "local" {
            ui.url.clear();
            ui.api_url.clear();
        }
        ui.mode = mode.to_string();
        changed
    };
    if changed {
        rebuild_main_window_for_mode(app, mode);
    }
    Ok(())
}

/// Off by default: action colour is the brand violet, and letting the OS accent
/// overwrite it would hand the product's one loud colour to a system preference.
/// The reader stays wired up because "tint the app to my Mac" is a plausible
/// What switching connection will do, in the terms the person is about to live
/// with.
///
/// The switch used to happen on the press. Choosing Local starts the private
/// runtime — a VM boot, an image pull on a cold machine, and a ninety-second
/// health gate before it will say whether it worked — and choosing Hosted takes
/// the workspace away from the stack still running on this Mac. Neither is a
/// thing to do because a menu item was next to the one you meant.
pub(crate) fn connection_switch_prompt(current: &str, running: bool) -> (String, String, String) {
    if current == "local" {
        (
            "Use the hosted workspace?".into(),
            if running {
                format!("Lemma keeps running on {THIS_COMPUTER} and your local pods stay where they are — this window just stops pointing at them. Use this menu item again to come back.")
            } else {
                format!("This window will point at the hosted workspace instead of {THIS_COMPUTER}. Your local pods stay where they are. Use this menu item again to come back.")
            },
            "Use Hosted".into(),
        )
    } else {
        (
            format!("Run Lemma on {THIS_COMPUTER}?"),
            "Starting the local stack boots a private Linux runtime and waits for its database, cache, and auth service. On a cold machine that takes a few minutes, and the window will show the splash until it is ready."
                .into(),
            "Start Local".into(),
        )
    }
}

/// Ask before switching, then switch and say so if it fails.
pub(crate) fn confirm_then_switch_connection(app: AppHandle) {
    let current = current_mode(&app);
    if current == "undecided" {
        show_splash(&app);
        return;
    }
    let running = {
        let shell: State<Shell> = app.state();
        let ui = shell.ui.lock().unwrap();
        ui.running
    };
    let (title, body, confirm) = connection_switch_prompt(&current, running);
    let handle = app.clone();
    std::thread::spawn(move || {
        let result = confirm_destructive_action_impl(handle.clone(), title, body, confirm)
            .and_then(|confirmed| {
                if confirmed {
                    choose_connection_mode_impl(handle.clone()).map(|_| ())
                } else {
                    Ok(())
                }
            });
        if let Err(error) = result {
            report_action_failure(&handle, "Switch connection", &error);
        }
    });
}

pub(crate) fn app_base_url(app: &AppHandle) -> Result<String, String> {
    let (mode, url, api_url) = {
        let shell: State<Shell> = app.state();
        let ui = shell.ui.lock().unwrap();
        (ui.mode.clone(), ui.url.clone(), ui.api_url.clone())
    };
    if mode == "hosted" {
        Ok(hosted_url())
    } else if trusted_workspace_urls(&url, &api_url) {
        Ok(url)
    } else {
        Err("the authenticated local workspace is not ready yet".into())
    }
}

pub(crate) fn desktop_auth_url(base: &str, auth_mode: &str) -> String {
    format!(
        "{}/auth/desktop?mode={auth_mode}",
        base.trim_end_matches('/'),
    )
}

pub(crate) fn local_auth_url(base: &str, auth_mode: &str) -> String {
    format!("{}/auth?show={auth_mode}", base.trim_end_matches('/'),)
}

/// The auth portal, told where to go once it is done.
///
/// Without a return address the portal has nowhere to send someone who is
/// already signed in, so it stops and offers a "Continue" button. That is the
/// right screen when a person navigated to sign-in themselves and might mean to
/// switch accounts. It is the wrong one on launch: the app asked for the
/// workspace, the session is already there, and the only thing between the two
/// was a click.
///
/// This is reached on every cold start, not just a first run. A launch mints a
/// new runtime generation, so the recorded resume target never matches and the
/// app falls back to the portal each time -- which is why the button was on
/// screen every single launch rather than occasionally.
///
/// The return address stays relative on purpose. It is resolved against the
/// portal's own origin, so it cannot point off it, and it survives locald
/// handing out a different port than the one this launch happens to use.
pub(crate) fn local_auth_url_returning_to(base: &str, auth_mode: &str, route: &str) -> String {
    // Relative means one leading slash and nothing that reads as an
    // authority. `//evil.example/x` and `/\evil.example/x` both start with a
    // slash and both resolve *off* the portal's origin -- and the portal hands
    // the decoded value to `window.location.replace`.
    let route = match route.as_bytes() {
        [b'/', rest @ ..] if !matches!(rest.first(), Some(b'/') | Some(b'\\')) => route,
        _ => "/",
    };
    let mut url = format!("{}/auth", base.trim_end_matches('/'));
    match tauri::Url::parse(&url) {
        Ok(mut parsed) => {
            parsed
                .query_pairs_mut()
                .append_pair("show", auth_mode)
                .append_pair("redirect_uri", route);
            parsed.to_string()
        }
        // A base this malformed will fail at navigation anyway; falling back to
        // the plain portal keeps that the failure rather than a panic here.
        Err(_) => {
            url.push_str("?show=");
            url.push_str(auth_mode);
            url
        }
    }
}

#[tauri::command]
/// Runs off the UI thread. A synchronous `#[tauri::command]` is dispatched on
/// the main thread, so any command that waits on the daemon, the network or a
/// child process freezes the window for its whole duration -- which is how a
/// first launch showed a black, unresponsive app for minutes while the runtime
/// installed and the daemon came up.
pub(crate) async fn set_connection_mode(
    window: Webview,
    app: AppHandle,
    mode: String,
) -> Result<(), String> {
    require_local_native_window(&window)?;
    tauri::async_runtime::spawn_blocking(move || set_connection_mode_impl(app, mode))
        .await
        .map_err(|error| error.to_string())?
}

#[tauri::command]
/// Runs off the UI thread. A synchronous `#[tauri::command]` is dispatched on
/// the main thread, so any command that waits on the daemon, the network or a
/// child process freezes the window for its whole duration -- which is how a
/// first launch showed a black, unresponsive app for minutes while the runtime
/// installed and the daemon came up.
pub(crate) async fn choose_connection_mode(app: AppHandle) -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || choose_connection_mode_impl(app))
        .await
        .map_err(|error| error.to_string())?
}

#[tauri::command]
pub(crate) async fn login(app: AppHandle, mode: Option<String>) -> Result<(), String> {
    let base = app_base_url(&app)?;
    let connection_mode = current_mode(&app);
    let auth_mode = if mode.as_deref() == Some("signup") {
        "signup"
    } else {
        "signin"
    };
    let url = if connection_mode == "local" {
        local_auth_url(&base, auth_mode)
    } else {
        // Hosted accounts keep credentials in the user's normal browser and
        // return through the one-time PKCE-style desktop handoff.
        desktop_auth_url(&base, auth_mode)
    };
    open_app_window(&app, &url)
}
