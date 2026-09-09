use super::*;

pub(crate) fn agent_host_action_impl(app: AppHandle, action: String) -> Result<(), String> {
    if !matches!(action.as_str(), "start" | "stop" | "restart") {
        return Err(format!("unknown Agent Host action {action:?}"));
    }
    ensure_agent_host_daemon(&app)?;
    agent_host_request(
        &app,
        json!({
            "cmd": format!("agent-host.{action}"),
            "id": operation_id("agent-host"),
        }),
    )
}

/// Who may drive this computer's Agent Host.
///
/// Local settings qualifies as a trusted bundled page. So does the signed-in
/// workspace, which is the whole point - the Agent Host page lives there so a
/// cloud user gets it too - but only while it is on the origin this app
/// actually navigated to. Sharing republishes that same workspace on a LAN or
/// tunnel host, and a visitor loading it must not reach this Mac. The ACL in
/// capabilities/workspace.json is the primary gate; this is the second one, in
/// case a URL pattern is ever written too loosely.
pub(crate) fn require_agent_host_caller(window: &Webview, app: &AppHandle) -> Result<(), String> {
    if is_control_window_label(window.label()) {
        return require_control_window(window);
    }
    if window.label() != "main" {
        return Err("the Agent Host is controlled from Lemma, not from this window".into());
    }
    let url = window
        .url()
        .map_err(|error| format!("could not inspect the workspace: {error}"))?;
    if trusted_native_asset_url(&url) {
        return Ok(());
    }
    let expected = app_base_url(app)?;
    let expected =
        tauri::Url::parse(&expected).map_err(|_| "no workspace origin yet".to_string())?;
    if url.origin() != expected.origin() {
        return Err("only the signed-in Lemma workspace can control the Agent Host".into());
    }
    Ok(())
}

/// Connect to locald, starting it if needed, for Agent Host work only.
///
/// A cloud user has no local stack, so the shell never brings locald up for
/// them - and without it nothing supervises the Agent Host, which is exactly
/// the feature they want on their own machine. locald with no host pack does
/// nothing but hold the socket and supervise the sidecar, so it is the right
/// process for both modes; only the local one needs the runtime artifacts.
pub(crate) fn ensure_agent_host_daemon(app: &AppHandle) -> Result<(), String> {
    if current_mode(app) == "local" {
        ensure_locald(app)
    } else {
        ensure_locald_without_host_pack(app)
    }
}

pub(crate) fn agent_host_status_impl(app: AppHandle) -> Result<Value, String> {
    ensure_agent_host_daemon(&app)?;
    let response = locald_request(
        json!({"cmd": "agent-host.status", "id": operation_id("agent-host-status")}),
        Duration::from_secs(15),
    )?;
    let status = response
        .get("agent_host")
        .filter(|value| value.is_object())
        .ok_or("Lemma returned an invalid Agent Host status")?
        .clone();
    let shell: State<Shell> = app.state();
    *shell.agent_host_status.lock().unwrap() = Some(status.clone());
    Ok(status)
}

pub(crate) fn agent_host_start_impl(app: AppHandle) -> Result<(), String> {
    ensure_agent_host_daemon(&app)?;
    agent_host_request(
        &app,
        json!({
            "cmd": "agent-host.start",
            "id": operation_id("agent-host"),
        }),
    )
}

pub(crate) fn agent_host_pair_impl(
    app: AppHandle,
    url: String,
    pairing_code: String,
    name: String,
) -> Result<(), String> {
    if url.trim().is_empty() || pairing_code.trim().is_empty() {
        return Err("pairing needs a workspace URL and a pairing code".into());
    }
    ensure_agent_host_daemon(&app)?;
    agent_host_request(
        &app,
        json!({
            "cmd": "agent-host.pair",
            "id": operation_id("agent-host-pair"),
            "url": url.trim(),
            "pairing_code": pairing_code.trim(),
            "name": name.trim(),
        }),
    )
}

// No `agent_host_unpair`. Dropping the pairing of the machine you are sitting
// at is undone by the next authenticated page, which pairs it again — so the
// command could only ever be honest alongside a flag remembering that you meant
// it, and that flag was a state plane of its own that nothing else could see.
// Removing a computer is `agent.host.revoke` on the backend, where it is durable
// and where it also works for a machine you cannot reach.

pub(crate) fn agent_host_refresh_impl(app: AppHandle) -> Result<(), String> {
    ensure_agent_host_daemon(&app)?;
    agent_host_request(
        &app,
        json!({
            "cmd": "agent-host.refresh",
            "id": operation_id("agent-host-refresh"),
        }),
    )
}

/// Whether to bring locald up at launch so the sidecar is there to be reached.
///
/// Read from the files locald itself uses, so the shell can decide before locald
/// exists.
///
/// Purely derived: this machine is paired to something, so it has work waiting.
/// It used to consult `supervisor.json`'s `{"enabled": bool}` first, which was
/// the persisted half of the off switch — and with the switch gone, nothing
/// writes that file, while a `false` left behind by an older build would hold a
/// paired machine off forever with no UI left to turn it back on. An unpaired
/// machine still gets no daemon at all, which is the case this guard exists for.
pub(crate) fn agent_host_wants_to_run() -> bool {
    let root = locald_root();
    let data_dir = root.parent().unwrap_or(&root).join("agent-host");
    let Ok(raw) = std::fs::read_to_string(data_dir.join("config.json")) else {
        return false;
    };
    serde_json::from_str::<Value>(&raw)
        .ok()
        .and_then(|config| {
            config
                .get("targets")
                .and_then(Value::as_array)
                .map(|targets| !targets.is_empty())
        })
        .unwrap_or(false)
}

/// Shared with the CLI-managed host, so both write the same file.
pub(crate) fn agent_host_log_path() -> PathBuf {
    let root = locald_root();
    root.parent()
        .unwrap_or(&root)
        .join("agent-host/agent-host.log")
}

/// What the tray says about the Agent Host.
///
/// Reachability, not liveness. A running host that is unpaired or cannot reach
/// its workspace takes no work, so reporting it as simply "on" would be a lie
/// the user only discovers when a run never starts.
///
/// "Reconnecting" is a claim about a connection that is coming back, and it was
/// made for every disconnected state — including a host paired to a workspace
/// that is simply not there any more, which is what a local pairing becomes the
/// moment the local stack stops. That host retries for days behind a word that
/// promises the opposite, so a failed last attempt now says so. The journal
/// carries the error of the latest attempt only, cleared on a connect, so this
/// distinguishes "trying" from "tried and failed" rather than remembering an
/// old failure forever.
pub(crate) fn agent_host_tray_label(
    available: bool,
    running: bool,
    paired: bool,
    connected: bool,
    unreachable: bool,
    failed_to_start: bool,
) -> String {
    if !available {
        "Agent Host: not installed".into()
    } else if !running && failed_to_start {
        // The supervisor tried and could not. Saying "starting…" here is a
        // promise the process is not keeping: it arms a backoff on every failed
        // spawn, so a sidecar that cannot start says "starting…" for as long as
        // the app is open and nothing ever contradicts it.
        "Agent Host: not starting — see log".into()
    } else if !running {
        // Not "off". Nothing can switch this computer off any more, so the only
        // way to be installed, not running and not failing is to be on the way
        // up — and a tray that says "off" with no way to say "on" is a dead end.
        "Agent Host: starting…".into()
    } else if !paired {
        "Agent Host: not paired".into()
    } else if connected {
        "Agent Host: connected".into()
    } else if unreachable {
        "Agent Host: workspace unreachable".into()
    } else {
        "Agent Host: reconnecting…".into()
    }
}

/// Rewrite the tray's Agent Host entries from a status payload.
///
/// The tray is built once and never rebuilt, so without this it would keep
/// claiming whatever was true at launch.
pub(crate) fn refresh_agent_host_tray(app: &AppHandle, status: &Value) {
    let available = status.get("available").and_then(Value::as_bool) == Some(true);
    let running = status.get("running").and_then(Value::as_bool) == Some(true);
    let paired = status.get("paired").and_then(Value::as_bool) == Some(true);
    let targets = status.get("targets").and_then(Value::as_array);
    let connected = targets.is_some_and(|targets| {
        targets
            .iter()
            .any(|target| target.get("connection_state").and_then(Value::as_str) == Some("ONLINE"))
    });
    // Every paired workspace failed its last attempt: nothing here is on its way
    // back, whatever the retry loop is still doing.
    let unreachable = targets.is_some_and(|targets| {
        !targets.is_empty()
            && targets.iter().all(|target| {
                target
                    .get("last_error")
                    .and_then(Value::as_str)
                    .is_some_and(|error| !error.trim().is_empty())
            })
    });

    // The supervisor records why the last spawn or exit failed and clears it on
    // a success, so this is "it tried and could not", not "it failed once weeks
    // ago". Only meaningful while it is not running; a running host's errors are
    // about its workspaces, which the states below already cover.
    let failed_to_start = status
        .get("last_error")
        .and_then(Value::as_str)
        .is_some_and(|error| !error.trim().is_empty());

    let state = agent_host_tray_label(
        available,
        running,
        paired,
        connected,
        unreachable,
        failed_to_start,
    );

    // `running` used to be mirrored onto `Shell::ui` as well, because the tray's
    // toggle had to know which way to point. Nothing asks any more.
    //
    // Clone the handle out and drop the guard before touching it. Every
    // `set_*` below is a blocking round-trip to the main thread, and this runs on
    // the locald reader thread -- so holding the lock across them meant a busy
    // main thread stopped daemon events being read at all. Progress stopped
    // updating and `ready` was never handled, which is how a slow start became a
    // permanently dead-looking splash. `refresh_tray_status` already does this.
    let item = {
        let shell: State<Shell> = app.state();
        let guard = shell.tray_agent_host.lock().unwrap();
        guard.clone()
    };
    let Some(state_item) = item else {
        return;
    };
    let _ = state_item.set_text(state);
    if let Some(tray) = app.tray_by_id("lemma-tray") {
        let _ = tray.set_tooltip(Some(if running && connected {
            "Lemma · Agent Host connected"
        } else if running {
            "Lemma · Agent Host starting"
        } else {
            "Lemma"
        }));
    }
}

#[tauri::command]
/// Runs off the UI thread. A synchronous `#[tauri::command]` is dispatched on
/// the main thread, so any command that waits on the daemon, the network or a
/// child process freezes every window for its whole duration.
pub(crate) async fn agent_host_action(
    window: Webview,
    app: AppHandle,
    action: String,
) -> Result<(), String> {
    require_control_window(&window)?;
    tauri::async_runtime::spawn_blocking(move || agent_host_action_impl(app, action))
        .await
        .map_err(|error| error.to_string())?
}

#[tauri::command]
/// What the workspace should say about the sandbox image download.
///
/// Read straight out of the shell's own state, which locald has already
/// pushed to: no daemon round trip, so the workspace can poll it while the
/// download is running without paying for a socket each time.
pub(crate) fn sandbox_image_status(_window: Webview, app: AppHandle) -> Value {
    let shell: State<Shell> = app.state();
    let ui = shell.ui.lock().unwrap();
    json!({
        // `pending`, not the empty default, when locald has not said anything
        // yet. The workspace stops asking once the answer can no longer change,
        // and it reads a state it does not recognise as one of those -- so an
        // empty string here meant a page that opened before the first report
        // never saw the download at all.
        "state": if ui.sandbox_images.is_empty() {
            "pending"
        } else {
            ui.sandbox_images.as_str()
        },
        "detail": ui.sandbox_images_detail,
    })
}

#[tauri::command]
/// Runs off the UI thread. A synchronous `#[tauri::command]` is dispatched on
/// the main thread, so any command that waits on the daemon, the network or a
/// child process freezes every window for its whole duration.
pub(crate) async fn agent_host_status(window: Webview, app: AppHandle) -> Result<Value, String> {
    // The same check the other six Agent Host commands make, and it was the
    // only one without it. `workspace.json` grants this to the workspace
    // origin, so without the check any page the main window is showing could
    // start locald and read the pairing state back.
    require_agent_host_caller(&window, &app)?;
    tauri::async_runtime::spawn_blocking(move || agent_host_status_impl(app))
        .await
        .map_err(|error| error.to_string())?
}

#[tauri::command]
/// Runs off the UI thread. A synchronous `#[tauri::command]` is dispatched on
/// the main thread, so any command that waits on the daemon, the network or a
/// child process freezes every window for its whole duration.
/// Ask this computer's Agent Host to be running. There is no counterpart.
///
/// This used to be `set_enabled(bool)`, and the `false` half was the off switch
/// the workspace page drew as "Turn off". It also wrote a preference that had to
/// be remembered, reconciled against the automatic connection, and reported as a
/// state of its own — which is how "off" became indistinguishable from "not
/// paired", "not installed" and "cannot reach the workspace" in the one place a
/// user looks. Removing the `false` removes the preference, the reconciliation,
/// and the state. Quitting Lemma still stops the sidecar; that is a consequence
/// of the app closing, not a setting.
pub(crate) async fn agent_host_start(window: Webview, app: AppHandle) -> Result<(), String> {
    require_agent_host_caller(&window, &app)?;
    tauri::async_runtime::spawn_blocking(move || agent_host_start_impl(app))
        .await
        .map_err(|error| error.to_string())?
}

#[tauri::command]
/// Runs off the UI thread. A synchronous `#[tauri::command]` is dispatched on
/// the main thread, so any command that waits on the daemon, the network or a
/// child process freezes every window for its whole duration.
pub(crate) async fn agent_host_pair(
    window: Webview,
    app: AppHandle,
    url: String,
    pairing_code: String,
    name: String,
) -> Result<(), String> {
    require_agent_host_caller(&window, &app)?;
    tauri::async_runtime::spawn_blocking(move || agent_host_pair_impl(app, url, pairing_code, name))
        .await
        .map_err(|error| error.to_string())?
}

#[tauri::command]
/// Runs off the UI thread. A synchronous `#[tauri::command]` is dispatched on
/// the main thread, so any command that waits on the daemon, the network or a
/// child process freezes every window for its whole duration.
pub(crate) async fn agent_host_refresh(window: Webview, app: AppHandle) -> Result<(), String> {
    require_agent_host_caller(&window, &app)?;
    tauri::async_runtime::spawn_blocking(move || agent_host_refresh_impl(app))
        .await
        .map_err(|error| error.to_string())?
}

#[tauri::command(async)]
pub(crate) fn agent_host_open_log(window: Webview, app: AppHandle) -> Result<(), String> {
    require_agent_host_caller(&window, &app)?;
    let log = agent_host_log_path();
    if !log.is_file() {
        return Err("the Agent Host has not written a log yet".into());
    }
    reveal_path(&log)
}
