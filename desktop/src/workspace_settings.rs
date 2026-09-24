//! This installation's settings, as the signed-in workspace reaches them.
//!
//! The settings a person changes about their own computer -- sharing, updates,
//! start at login, the OAuth apps and bot credentials this install runs its
//! connectors and channels with -- used to live only in the bundled Local
//! settings page, a second product with its own look and its own words. They
//! are in the workspace's own Settings now, under "This Mac", so the commands
//! below are granted to the workspace origin.
//!
//! That origin is remote to Tauri, and the capability that names it also names
//! the hosted site. So every command here asks one narrower question than the
//! Agent Host commands do: is the caller this installation's own workspace, on
//! the loopback origin this app navigated to? Not the hosted site, which has no
//! installation here to change, and not a shared LAN or tunnel origin, which is
//! the same workspace republished for other people's devices.
//!
//! Local settings answers the same commands from its bundled page while the
//! workspace cannot load -- `require_settings_caller` accepts either.

use super::*;

/// Whether a page at `page` is this installation's own local workspace.
///
/// Pure, so the rule can be asserted without a running app. Three things must
/// hold together:
///
/// - the app is in local mode -- a hosted workspace has no installation here;
/// - the page is on the origin this app navigated to, `workspace`;
/// - that origin is a loopback workspace host this build ships knowing, or the
///   development override `scripts/dev-local.sh` sets.
///
/// The last one is the reason this exists. While sharing is on, locald moves
/// the canonical origin -- and with it `workspace` -- to the LAN address or the
/// tunnel host, and the owner's own window follows it there. Matching
/// `workspace` alone would then accept that shared origin, which is the one a
/// visitor's device loads too.
pub(crate) fn local_settings_origin_allowed(
    mode: &str,
    page: &tauri::Url,
    workspace: &str,
    dev_local: Option<&str>,
) -> bool {
    if mode != "local" {
        return false;
    }
    let Ok(workspace) = tauri::Url::parse(workspace) else {
        return false;
    };
    if page.origin() != workspace.origin() {
        return false;
    }
    let shipped_local =
        page.scheme() == "http" && page.host_str().is_some_and(trusted_local_workspace_host);
    let development = dev_local
        .and_then(|raw| tauri::Url::parse(raw.trim()).ok())
        .is_some_and(|dev| dev.origin() == page.origin());
    shipped_local || development
}

/// Refuse unless the caller is this installation's own local workspace.
pub(crate) fn require_local_settings_caller(
    window: &Webview,
    app: &AppHandle,
) -> Result<(), String> {
    if window.label() != "main" {
        return Err("this installation's settings open in Lemma, not in this window".into());
    }
    let page = window
        .url()
        .map_err(|error| format!("could not inspect the workspace: {error}"))?;
    let workspace = app_base_url(app)?;
    let dev_local =
        dev_override("LEMMA_DESKTOP_LOCAL_URL").and_then(|value| value.into_string().ok());
    if !local_settings_origin_allowed(&current_mode(app), &page, &workspace, dev_local.as_deref()) {
        return Err(format!(
            "only the Lemma workspace on {THIS_COMPUTER} can change {THIS_COMPUTER}'s settings"
        ));
    }
    Ok(())
}

/// The bundled Local settings page, or the local workspace.
///
/// For the commands both surfaces offer: the workspace is where people change
/// these, and Local settings is where they still can when the workspace will
/// not load.
pub(crate) fn require_settings_caller(window: &Webview, app: &AppHandle) -> Result<(), String> {
    if is_control_window_label(window.label()) {
        return require_control_window(window);
    }
    require_local_settings_caller(window, app)
}

/// The fields of a control snapshot the workspace's settings read, and no more.
///
/// An allowlist rather than a copy with a few keys removed: the snapshot is the
/// daemon's to extend, and a field added there for Local settings should not
/// reach a web page because nobody thought to strip it here. The install id is
/// the one field the operator block carries that nothing here needs.
pub(crate) fn workspace_settings_view(snapshot: &Value) -> Value {
    let pick = |value: &Value, keys: &[&str]| -> Value {
        let mut picked = serde_json::Map::new();
        for key in keys {
            if let Some(field) = value.get(*key) {
                picked.insert((*key).to_owned(), field.clone());
            }
        }
        Value::Object(picked)
    };
    let operator = snapshot.get("operator").cloned().unwrap_or(Value::Null);
    let config = operator.get("config").cloned().unwrap_or(Value::Null);
    let services = snapshot
        .get("services")
        .and_then(Value::as_array)
        .map(|services| {
            services
                .iter()
                .map(|service| pick(service, &["id", "running", "circuit_open"]))
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    json!({
        "release": snapshot.get("release").cloned().unwrap_or(Value::Null),
        "state": pick(
            snapshot.get("state").unwrap_or(&Value::Null),
            &["ready", "running", "status", "last_error", "url", "api_url"],
        ),
        "services": services,
        "operator": {
            "config": pick(&config, &["revision", "ai", "integrations", "surfaces"]),
            "secrets": operator.get("secrets").cloned().unwrap_or(json!({})),
            "readiness": operator.get("readiness").cloned().unwrap_or(json!({})),
        },
        "sharing": snapshot.get("sharing").cloned().unwrap_or(Value::Null),
        "sandbox_images": snapshot.get("sandbox_images").cloned().unwrap_or(Value::Null),
        "paths": snapshot.get("paths").cloned().unwrap_or(Value::Null),
    })
}

/// The operator sections the workspace may write.
///
/// `ai` is absent on purpose. The workspace's models are the organization's
/// Models page; the one path into the operator's AI profile stays
/// `configure_ai_provider`, which merges that section alone. Integrations and
/// surfaces are the OAuth apps and bot credentials connectors and channels run
/// with on this install, and the workspace is where those are needed.
pub(crate) fn workspace_section_allowed(payload: &Value) -> Result<(), String> {
    match payload.pointer("/section/name").and_then(Value::as_str) {
        Some("integrations" | "surfaces") => Ok(()),
        Some(other) => Err(format!("the {other} section is not changed from here")),
        None => Err("a settings change names its section".into()),
    }
}

/// A sharing request from the workspace, before anything is sent.
///
/// The one field the page does not get to set is the Public consent. It is
/// forced false here and set only by the native confirmation below, so a page
/// that simply writes `true` has not agreed to anything.
pub(crate) fn workspace_sharing_request(
    action: &str,
    payload: Option<Value>,
) -> Result<(Value, bool), String> {
    if !matches!(
        action,
        "snapshot" | "preflight" | "enable" | "disable" | "access"
    ) {
        return Err(format!("unknown sharing action: {action}"));
    }
    let mut request = json!({
        "cmd": format!("sharing.{action}"),
        "id": operation_id("workspace-sharing"),
    });
    let mut needs_public_consent = false;
    if let Some(mut payload) = payload {
        if action == "preflight" {
            if let Some(provider) = payload.get("provider") {
                request["provider"] = provider.clone();
            }
        } else {
            if action == "enable" {
                needs_public_consent =
                    payload.get("mode").and_then(Value::as_str) == Some("public");
                if payload.is_object() {
                    payload["public_warning_confirmed"] = Value::Bool(false);
                }
            }
            request["payload"] = payload;
        }
    }
    Ok((request, needs_public_consent))
}

/// What the native Public confirmation says. The daemon's own sentence about
/// who may join leads, because it is the one it enforces.
pub(crate) fn public_consent_message(who_can_join_sentence: &str) -> String {
    format!(
        "{who_can_join_sentence} The workspace, sign-in, files, chat, tools and webhook callbacks \
         become reachable from the internet until you turn sharing off or quit Lemma. This \
         window reopens at the public address, where {THIS_COMPUTER}'s settings open from the \
         menu bar."
    )
}

fn local_settings_snapshot_impl(app: AppHandle) -> Result<Value, String> {
    if current_mode(&app) != "local" {
        return Err(format!("{THIS_COMPUTER} runs no local Lemma to configure"));
    }
    // Opening settings is not consent to download or repair a local runtime --
    // the same reason Local settings' own snapshot uses this.
    ensure_locald_without_host_pack(&app)?;
    let snapshot = locald_request(
        json!({"cmd": "control.snapshot", "id": operation_id("workspace-settings")}),
        Duration::from_secs(15),
    )?;
    let mut view = workspace_settings_view(&snapshot);
    view["app"] = json!({
        "version": env!("CARGO_PKG_VERSION"),
        "channel": release_channel(),
        "updates_supported": updates_enabled(),
        "start_at_login": app.autolaunch().is_enabled().unwrap_or(false),
    });
    Ok(view)
}

fn apply_local_settings_impl(app: AppHandle, payload: Value) -> Result<Value, String> {
    workspace_section_allowed(&payload)?;
    ensure_locald(&app)?;
    // Blocking on the daemon's answer rather than its event stream: the
    // workspace is granted named commands, not events, and a save it cannot
    // hear finish is one it would have to guess about. Apply restarts the
    // backend, which is most of this budget.
    let applied = locald_request(
        json!({"cmd": "config.apply", "id": operation_id("workspace-apply"), "payload": payload}),
        Duration::from_secs(180),
    )?;
    let operator = applied.get("operator").cloned().unwrap_or(Value::Null);
    Ok(workspace_settings_view(&json!({ "operator": operator }))["operator"].clone())
}

fn local_sharing_impl(
    app: AppHandle,
    action: String,
    payload: Option<Value>,
) -> Result<Value, String> {
    if current_mode(&app) != "local" {
        return Err("sharing is available only for a local workspace".into());
    }
    let (mut request, needs_public_consent) = workspace_sharing_request(&action, payload)?;
    ensure_locald(&app)?;
    if needs_public_consent {
        // Native and in Rust, not a dialog the page draws: the page asking is
        // not the person agreeing, and this is the one setting here that puts
        // the installation on the internet.
        let current = locald_request(
            json!({"cmd": "sharing.snapshot", "id": operation_id("workspace-sharing-consent")}),
            Duration::from_secs(15),
        )?;
        let sentence = current
            .pointer("/sharing/public_confirmation")
            .and_then(Value::as_str)
            .unwrap_or("Anyone with this link can reach this Lemma's sign-in page.");
        let agreed = confirm_destructive_action_impl(
            app.clone(),
            "Create a public link?".into(),
            public_consent_message(sentence),
            "I understand · create link".into(),
        )?;
        if !agreed {
            return Ok(json!({ "cancelled": true }));
        }
        request["payload"]["public_warning_confirmed"] = Value::Bool(true);
    }
    // The answer is the first event about this request: the snapshot for a
    // read, the first progress report for an enable, and the change itself for
    // a disable or an access change. An enable keeps going after this returns;
    // the page reads its progress from the snapshot.
    let answer = locald_request(request, Duration::from_secs(180))?;
    Ok(json!({
        "event": answer.get("event").cloned().unwrap_or(Value::Null),
        "sharing": answer.get("sharing").cloned().unwrap_or(Value::Null),
        "preflight": answer.get("preflight").cloned().unwrap_or(Value::Null),
    }))
}

fn set_start_at_login_impl(app: AppHandle, enabled: bool) -> Result<bool, String> {
    let autolaunch = app.autolaunch();
    let result = if enabled {
        autolaunch.enable()
    } else {
        autolaunch.disable()
    };
    result.map_err(|error| format!("could not change Start at login: {error}"))?;
    // The tray's check item is drawn from this at construction, so without a
    // rebuild it goes on showing the old answer beside the new one.
    refresh_menus_for_connection_mode(&app);
    Ok(autolaunch.is_enabled().unwrap_or(enabled))
}

#[tauri::command]
/// Off the UI thread: a daemon round trip, and a synchronous command runs on
/// the main thread, where any wait freezes every window.
pub(crate) async fn local_settings_snapshot(
    window: Webview,
    app: AppHandle,
) -> Result<Value, String> {
    require_local_settings_caller(&window, &app)?;
    tauri::async_runtime::spawn_blocking(move || local_settings_snapshot_impl(app))
        .await
        .map_err(|error| error.to_string())?
}

#[tauri::command]
/// Save one operator section -- integrations or surfaces -- and wait for it.
pub(crate) async fn apply_local_settings(
    window: Webview,
    app: AppHandle,
    payload: Value,
) -> Result<Value, String> {
    require_local_settings_caller(&window, &app)?;
    tauri::async_runtime::spawn_blocking(move || apply_local_settings_impl(app, payload))
        .await
        .map_err(|error| error.to_string())?
}

#[tauri::command]
/// Who can reach this installation. Public asks natively first.
pub(crate) async fn local_sharing(
    window: Webview,
    app: AppHandle,
    action: String,
    payload: Option<Value>,
) -> Result<Value, String> {
    require_local_settings_caller(&window, &app)?;
    tauri::async_runtime::spawn_blocking(move || local_sharing_impl(app, action, payload))
        .await
        .map_err(|error| error.to_string())?
}

#[tauri::command]
/// Start at login: reading and writing a launch agent, so off the UI thread.
pub(crate) async fn set_start_at_login(
    window: Webview,
    app: AppHandle,
    enabled: bool,
) -> Result<bool, String> {
    require_local_settings_caller(&window, &app)?;
    tauri::async_runtime::spawn_blocking(move || set_start_at_login_impl(app, enabled))
        .await
        .map_err(|error| error.to_string())?
}

/// The locald request that turns host execution on or off.
///
/// Pure, so what reaches the daemon can be asserted without one. Only a
/// boolean crosses: the page chooses on or off, never a folder, a profile or
/// anything else about how commands are confined.
pub(crate) fn host_execution_request(enabled: bool) -> Value {
    json!({
        "cmd": "agent-host.host-execution",
        "id": operation_id("workspace-host-execution"),
        "enabled": enabled,
    })
}

fn set_host_execution_impl(app: AppHandle, enabled: bool) -> Result<Value, String> {
    if current_mode(&app) != "local" {
        return Err(format!("{THIS_COMPUTER} runs no local Lemma to configure"));
    }
    // Refused here as well as in the Agent Host, so a page on a machine that
    // cannot confine commands gets a sentence rather than a failed operation.
    if enabled
        && !(cfg!(target_os = "macos") && std::path::Path::new("/usr/bin/sandbox-exec").is_file())
    {
        return Err(format!(
            "{THIS_COMPUTER} cannot run agents' commands in a sandbox, so they stay in the VM"
        ));
    }
    ensure_agent_host_daemon(&app)?;
    agent_host_request(&app, host_execution_request(enabled))?;
    // The fresh status, so the switch shows what the host now says rather
    // than what the page asked for.
    agent_host_ui::agent_host_status_impl(app)
}

#[tauri::command]
/// "Run commands on this Mac": an owner's agent commands on the host, under
/// Seatbelt, instead of in the VM. A daemon round trip, so off the UI thread.
pub(crate) async fn set_host_execution(
    window: Webview,
    app: AppHandle,
    enabled: bool,
) -> Result<Value, String> {
    require_local_settings_caller(&window, &app)?;
    tauri::async_runtime::spawn_blocking(move || set_host_execution_impl(app, enabled))
        .await
        .map_err(|error| error.to_string())?
}

/* ── the menu's way in ─────────────────────────────────────────────── */

/// Where ⌘, and the tray's "Desktop settings…" land.
#[derive(Debug, PartialEq, Eq)]
pub(crate) enum SettingsDestination {
    /// The workspace's own Settings, at a section.
    Workspace,
    /// Local settings, for when there is no healthy local workspace to ask.
    Native,
}

/// Pure, so the fallback can be asserted without a window.
///
/// The workspace only while it can answer: a local install that is up, not in
/// an error, with the page on this installation's own origin. Everything else
/// -- hosted mode, a stack that is starting or broken, the splash, a window
/// moved to a shared origin -- opens Local settings, which is exactly the page
/// that exists for those cases.
pub(crate) fn settings_destination(
    mode: &str,
    ready: bool,
    error: bool,
    page: Option<&tauri::Url>,
    workspace: &str,
    dev_local: Option<&str>,
) -> SettingsDestination {
    let healthy = mode == "local" && ready && !error;
    match page {
        Some(page)
            if healthy && local_settings_origin_allowed(mode, page, workspace, dev_local) =>
        {
            SettingsDestination::Workspace
        }
        _ => SettingsDestination::Native,
    }
}

/// The one line the shell evaluates in the workspace to open Settings.
///
/// The section is serialised, never spliced: it is a constant today, and a
/// script built by concatenation is one refactor away from carrying a string
/// it should not.
pub(crate) fn open_settings_script(section: &str) -> String {
    format!(
        "window.dispatchEvent(new CustomEvent(\"lemma:open-settings\", {{ detail: {{ section: {} }} }}))",
        serde_json::to_string(section).unwrap_or_else(|_| "\"account\"".into())
    )
}

/// Open Settings at `section` in the workspace, or Local settings at
/// `native_page` when the workspace cannot take it.
pub(crate) fn open_settings(app: &AppHandle, section: &str, native_page: &str) {
    let (mode, ready, error, workspace) = {
        let shell: State<Shell> = app.state();
        let ui = shell.ui.lock_or_recover();
        (ui.mode.clone(), ui.ready, ui.error, ui.url.clone())
    };
    let main = app.get_webview("main");
    let page = main.as_ref().and_then(|webview| webview.url().ok());
    let dev_local =
        dev_override("LEMMA_DESKTOP_LOCAL_URL").and_then(|value| value.into_string().ok());
    let destination = settings_destination(
        &mode,
        ready,
        error,
        page.as_ref(),
        &workspace,
        dev_local.as_deref(),
    );
    if let (SettingsDestination::Workspace, Some(main)) = (destination, main) {
        restore_dock_presence(app);
        let _ = main.window().show();
        let _ = main.set_focus();
        if main.eval(open_settings_script(section)).is_ok() {
            return;
        }
    }
    let _ = show_control_center_page(app, Some(native_page));
}
