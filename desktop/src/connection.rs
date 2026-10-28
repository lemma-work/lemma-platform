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

/// The Lemma server a hosted launch opens: Lemma Cloud, unless the person
/// connected this app to another one -- a self-hosted deployment, a staging
/// server -- from the chooser.
pub(crate) fn hosted_url() -> String {
    // The capability that grants this origin the shell's commands is gated on
    // `dev_override`; the navigation has to be gated the same way, or a
    // release build would be sent to an environment-named host with no
    // permissions and no explanation. A server saved in the config is not
    // that: the person typed it into the app, which checked it answers as a
    // Lemma server, and the same choice grants it its commands.
    dev_override("LEMMA_DESKTOP_HOSTED_URL")
        .and_then(|value| value.into_string().ok())
        .or_else(|| configured_hosted_server(&read_config()))
        .unwrap_or_else(|| DEFAULT_HOSTED_URL.into())
}

/// The server saved by "Connect to your own server", if it is still one this
/// app would accept. A config edited by hand into something else is ignored
/// rather than trusted, and the app falls back to Lemma Cloud.
pub(crate) fn configured_hosted_server(config: &Value) -> Option<String> {
    config[HOSTED_SERVER_KEY]
        .as_str()
        .and_then(|raw| normalize_server_url(raw).ok())
}

pub(crate) const HOSTED_SERVER_KEY: &str = "hostedServer";

/// How long the check that an address is a Lemma server may take.
const SERVER_PROBE_TIMEOUT: Duration = Duration::from_secs(10);

/// The origin of a Lemma server a person typed, or why it cannot be one.
///
/// Only the origin is kept: the workspace, its sign-in and the Agent Host's
/// pairing are all decided against it, and a path would only be dropped later.
/// HTTPS, except a server on this computer by its loopback name -- a
/// developer's `make dev`. Not `*.localhost`: `lemma.localhost` is this
/// installation's own workspace, whose alias ports serve pod apps' code, and
/// must never be granted the shell's commands by an address someone typed.
pub(crate) fn normalize_server_url(raw: &str) -> Result<String, String> {
    let raw = raw.trim();
    if raw.is_empty() {
        return Err("Enter your Lemma server's address.".into());
    }
    let candidate = if raw.contains("://") {
        raw.to_owned()
    } else {
        format!("https://{raw}")
    };
    let url = tauri::Url::parse(&candidate).map_err(|_| format!("{raw} is not a web address."))?;
    let host = url
        .host_str()
        .filter(|host| !host.is_empty())
        .ok_or_else(|| format!("{raw} is not a web address."))?
        .to_ascii_lowercase();
    if !url.username().is_empty() || url.password().is_some() {
        return Err(
            "Leave the sign-in out of the address. You sign in on the server itself.".into(),
        );
    }
    // Refused by name, whatever the scheme: the guard above must not rest on
    // the alias ports happening to serve plain http today.
    if host.ends_with(".localhost") {
        return Err(format!(
            "{host} is this computer's own Lemma. Choose Use Local Lemma instead."
        ));
    }
    let loopback = matches!(host.as_str(), "localhost" | "127.0.0.1" | "[::1]");
    match url.scheme() {
        "https" => {}
        "http" if loopback => {}
        "http" => {
            return Err(
                "Use an https:// address. Plain http works only for a server on this computer."
                    .into(),
            );
        }
        _ => return Err(format!("{raw} is not a web address.")),
    }
    Ok(url.origin().ascii_serialization())
}

/// Does `origin` answer as a Lemma server?
///
/// Every Lemma frontend serves `/site-config.js`, which sets
/// `window.__LEMMA_SITE__` before the page runs. Asked before the choice is
/// saved, so a typo is said on the chooser rather than as a blank window, and
/// so the shell's commands are never granted to a site that is not Lemma.
pub(crate) fn check_lemma_server(origin: &str) -> Result<(), String> {
    let client = reqwest::blocking::Client::builder()
        .timeout(SERVER_PROBE_TIMEOUT)
        .build()
        .map_err(|error| error.to_string())?;
    let response = client
        .get(format!("{origin}/site-config.js"))
        .send()
        .map_err(|_| format!("Couldn't reach {origin}. Check the address and your connection."))?;
    let status = response.status();
    let body = response.text().unwrap_or_default();
    if status.is_success() && is_lemma_site_config(&body) {
        Ok(())
    } else {
        Err(format!("{origin} doesn't look like a Lemma server."))
    }
}

pub(crate) fn is_lemma_site_config(body: &str) -> bool {
    body.contains("window.__LEMMA_SITE__")
}

/// Save where hosted mode connects: `None` is Lemma Cloud.
///
/// Lemma Cloud's own address is saved as nothing, so choosing it is the same as
/// never having chosen another server.
pub(crate) fn save_hosted_server(server: Option<&str>) -> Result<(), String> {
    let origin = server.map(normalize_server_url).transpose()?;
    let default_origin = tauri::Url::parse(DEFAULT_HOSTED_URL)
        .map(|url| url.origin().ascii_serialization())
        .unwrap_or_default();
    let custom = origin.filter(|origin| *origin != default_origin);
    if let Some(origin) = custom.as_deref() {
        check_lemma_server(origin)?;
    }
    write_config(|config| {
        if let Some(config) = config.as_object_mut() {
            match custom {
                Some(origin) => {
                    config.insert(HOSTED_SERVER_KEY.into(), json!(origin));
                }
                None => {
                    config.remove(HOSTED_SERVER_KEY);
                }
            }
        }
    })
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

pub(crate) fn set_connection_mode_impl(
    app: AppHandle,
    mode: String,
    server: Option<String>,
) -> Result<(), String> {
    if mode != "local" && mode != "hosted" {
        return Err(format!("unknown mode {mode:?}"));
    }
    if mode == "hosted" {
        // Before anything else changes, so an address that is not a Lemma
        // server leaves the app exactly where it was.
        save_hosted_server(server.as_deref())?;
        grant_hosted_workspace_capability(&app, &hosted_url());
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
    let ui = shell.ui.lock_or_recover();
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
        let mut ui = shell.ui.lock_or_recover();
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
        let ui = shell.ui.lock_or_recover();
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
        let ui = shell.ui.lock_or_recover();
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
    server: Option<String>,
) -> Result<(), String> {
    require_local_native_window(&window)?;
    tauri::async_runtime::spawn_blocking(move || set_connection_mode_impl(app, mode, server))
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

/// Whether a page may send this app back to the Cloud-or-Local chooser.
///
/// Narrow on purpose: the command is granted to the workspace origins, and
/// "forget which server this app uses" is not something any page there should
/// be able to ask. Only the hosted site's own sign-in screens -- where a person
/// who picked Lemma Cloud and pressed Cancel is standing -- in the main window,
/// while this app is in hosted mode. `/auth/desktop` is excluded: it is the
/// *browser's* half of the handoff and never runs in the app. A signed-in
/// workspace page is not on these paths, so nothing in the workspace can.
pub(crate) fn mode_chooser_return_allowed(
    label: &str,
    mode: &str,
    page: &tauri::Url,
    hosted: &str,
) -> Result<(), String> {
    if label != "main" {
        return Err("only Lemma's main window can go back to choosing how to run Lemma".into());
    }
    if mode != "hosted" {
        return Err("Lemma is not using Lemma Cloud, so there is no sign-in to cancel".into());
    }
    let hosted = tauri::Url::parse(hosted).map_err(|error| error.to_string())?;
    if page.origin() != hosted.origin() {
        return Err(
            "only Lemma Cloud's sign-in page can go back to choosing how to run Lemma".into(),
        );
    }
    let path = page.path();
    let sign_in = path == "/auth" || path.starts_with("/auth/");
    let browser_half = path == "/auth/desktop" || path.starts_with("/auth/desktop/");
    if !sign_in || browser_half {
        return Err(
            "only Lemma Cloud's sign-in page can go back to choosing how to run Lemma".into(),
        );
    }
    Ok(())
}

/// Forget the Lemma Cloud choice and show the chooser again.
///
/// The config goes back to having no mode, which is exactly what a first
/// launch reads as "undecided" -- so a quit here reopens on the chooser too,
/// rather than on the sign-in the person just cancelled. The window is
/// rebuilt on the splash (`rebuild_main_window_for_mode`), which shows the
/// chooser for an undecided mode. Nothing else changes: an Agent Host already
/// running for this computer keeps running.
pub(crate) fn return_to_mode_chooser_impl(app: AppHandle) -> Result<(), String> {
    write_config(|config| {
        if let Some(config) = config.as_object_mut() {
            config.remove("connectionMode");
        }
    })?;
    {
        let shell: State<Shell> = app.state();
        shell.ui.lock_or_recover().mode = "undecided".into();
    }
    refresh_menus_for_connection_mode(&app);
    rebuild_main_window_for_mode(&app, "undecided");
    Ok(())
}

#[tauri::command]
/// Off the UI thread: rebuilding the main window waits for the old one's label
/// to be released by the event loop, which cannot happen while this blocks it.
pub(crate) async fn return_to_mode_chooser(window: Webview, app: AppHandle) -> Result<(), String> {
    let page = window
        .url()
        .map_err(|error| format!("could not inspect the sign-in page: {error}"))?;
    mode_chooser_return_allowed(window.label(), &current_mode(&app), &page, &hosted_url())?;
    tauri::async_runtime::spawn_blocking(move || return_to_mode_chooser_impl(app))
        .await
        .map_err(|error| error.to_string())?
}
