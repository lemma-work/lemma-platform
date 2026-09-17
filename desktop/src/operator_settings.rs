//! What Local settings writes: the operator config, the AI provider and
//! sharing. Each one is a locald round trip the page waits on.

use super::*;

pub(crate) fn control_snapshot_impl(app: AppHandle, id: String) -> Result<(), String> {
    // Opening settings is not consent to download or repair a local runtime.
    ensure_locald_without_host_pack(&app)?;
    send_to_locald(&app, json!({"cmd":"control.snapshot", "id": id}))
}

pub(crate) fn apply_operator_config_impl(
    app: AppHandle,
    id: String,
    payload: Value,
) -> Result<(), String> {
    ensure_locald(&app)?;
    send_to_locald(
        &app,
        json!({"cmd":"config.apply", "id": id, "payload": payload}),
    )
}

pub(crate) fn discover_provider_models_impl(
    app: AppHandle,
    payload: Value,
) -> Result<Value, String> {
    ensure_locald(&app)?;
    let response = locald_request(
        json!({
            "cmd": "config.discover-models",
            "id": operation_id("discover-models"),
            "payload": payload,
        }),
        Duration::from_secs(30),
    )?;
    Ok(response.get("models").cloned().unwrap_or(json!([])))
}

pub(crate) fn configure_ai_provider_impl(app: AppHandle, payload: Value) -> Result<Value, String> {
    if current_mode(&app) != "local" {
        return Err("the local AI provider is configured only on a local install".into());
    }
    ensure_locald(&app)?;
    let response = locald_request(
        json!({
            "cmd": "config.set-ai",
            "id": operation_id("set-ai"),
            "payload": payload,
        }),
        Duration::from_secs(180),
    )?;
    Ok(response.get("operator").cloned().unwrap_or(json!({})))
}

pub(crate) fn sharing_action_impl(
    app: AppHandle,
    action: String,
    id: String,
    payload: Option<Value>,
) -> Result<(), String> {
    if current_mode(&app) != "local" {
        return Err("sharing is available only for a local workspace".into());
    }
    if !matches!(
        action.as_str(),
        "snapshot" | "preflight" | "enable" | "disable"
    ) {
        return Err(format!("unknown sharing action: {action}"));
    }
    ensure_locald(&app)?;
    let mut request = json!({
        "cmd": format!("sharing.{action}"),
        "id": id,
    });
    if let Some(payload) = payload {
        if action == "preflight" {
            if let Some(provider) = payload.get("provider") {
                request["provider"] = provider.clone();
            }
        } else {
            request["payload"] = payload;
        }
    }
    send_to_locald(&app, request)
}

#[tauri::command]
/// Runs off the UI thread. A synchronous `#[tauri::command]` is dispatched on
/// the main thread, so any command that waits on the daemon, the network or a
/// child process freezes every window for its whole duration.
pub(crate) async fn control_snapshot(
    window: Webview,
    app: AppHandle,
    id: String,
) -> Result<(), String> {
    require_control_window(&window)?;
    tauri::async_runtime::spawn_blocking(move || control_snapshot_impl(app, id))
        .await
        .map_err(|error| error.to_string())?
}

#[tauri::command]
/// Runs off the UI thread. A synchronous `#[tauri::command]` is dispatched on
/// the main thread, so any command that waits on the daemon, the network or a
/// child process freezes every window for its whole duration.
pub(crate) async fn apply_operator_config(
    window: Webview,
    app: AppHandle,
    id: String,
    payload: Value,
) -> Result<(), String> {
    require_agent_host_caller(&window, &app)?;
    require_control_window(&window)?;
    tauri::async_runtime::spawn_blocking(move || apply_operator_config_impl(app, id, payload))
        .await
        .map_err(|error| error.to_string())?
}

/// List a candidate provider's models so the page can offer a picker.
///
/// Reachable from the workspace as well as Local settings: onboarding asks the
/// same question, and the alternative was making people type model ids from
/// memory. It reads nothing and writes nothing.
#[tauri::command]
/// Runs off the UI thread. A synchronous `#[tauri::command]` is dispatched on
/// the main thread, so any command that waits on the daemon, the network or a
/// child process freezes every window for its whole duration.
pub(crate) async fn discover_provider_models(
    window: Webview,
    app: AppHandle,
    payload: Value,
) -> Result<Value, String> {
    // This binds the window and checks it, where it used to take `_window` and
    // discard it -- while `configure_ai_provider`, its sibling one screen down,
    // has always checked. The command is granted to remote origins, and an
    // omitted `api_key` means "use the one in the Keychain", which is then
    // attached as a bearer token to a `base_url` the *caller* chose. So one
    // invoke from any granted origin handed the user's provider key to a host
    // of the caller's choosing, with no dialog and nothing logged.
    require_agent_host_caller(&window, &app)?;
    tauri::async_runtime::spawn_blocking(move || discover_provider_models_impl(app, payload))
        .await
        .map_err(|error| error.to_string())?
}

/// Point this installation at an AI provider.
///
/// The one piece of operator configuration the workspace may write, and the
/// reason is that onboarding cannot honestly ask "which model?" and then send
/// the user to a different window to answer. Everything else the control page
/// owns — sharing, tunnels, runtime, integrations — stays where it was: this
/// command reaches `config.set-ai`, which merges only that section.
///
/// Blocking on purpose. Applying a provider validates it against the provider
/// and restarts the backend, and both of those can fail in ways the user needs
/// the actual message for.
#[tauri::command]
/// Runs off the UI thread. A synchronous `#[tauri::command]` is dispatched on
/// the main thread, so any command that waits on the daemon, the network or a
/// child process freezes every window for its whole duration.
pub(crate) async fn configure_ai_provider(
    window: Webview,
    app: AppHandle,
    payload: Value,
) -> Result<Value, String> {
    require_agent_host_caller(&window, &app)?;
    tauri::async_runtime::spawn_blocking(move || configure_ai_provider_impl(app, payload))
        .await
        .map_err(|error| error.to_string())?
}

#[tauri::command]
/// Runs off the UI thread. A synchronous `#[tauri::command]` is dispatched on
/// the main thread, so any command that waits on the daemon, the network or a
/// child process freezes every window for its whole duration.
pub(crate) async fn sharing_action(
    window: Webview,
    app: AppHandle,
    action: String,
    id: String,
    payload: Option<Value>,
) -> Result<(), String> {
    require_control_window(&window)?;
    tauri::async_runtime::spawn_blocking(move || sharing_action_impl(app, action, id, payload))
        .await
        .map_err(|error| error.to_string())?
}

pub(crate) fn prepare_sandbox_image_impl(app: AppHandle, id: String) -> Result<(), String> {
    if current_mode(&app) != "local" {
        return Err("the sandbox image belongs to a local workspace".into());
    }
    ensure_locald(&app)?;
    send_to_locald(&app, json!({ "cmd": "sandbox.prepare", "id": id }))
}

#[tauri::command]
/// Fetch the image pods run their work in, because someone asked for it.
///
/// Starting used to do this on its own, which spent several hundred megabytes
/// on a capability a person may never use: the coding agents run natively on
/// this computer, and someone using only those has no pod workload to sandbox.
/// They paid for the download anyway, and got a toast about it.
///
/// Runs off the UI thread for the reason every other daemon command here does.
/// It returns as soon as the fetch has started; progress arrives on the
/// `sandbox-images` broadcast, which is what Settings and the workspace both
/// already listen to.
pub(crate) async fn prepare_sandbox_image(
    window: Webview,
    app: AppHandle,
    id: String,
) -> Result<(), String> {
    require_control_window(&window)?;
    tauri::async_runtime::spawn_blocking(move || prepare_sandbox_image_impl(app, id))
        .await
        .map_err(|error| error.to_string())?
}

#[tauri::command]
pub(crate) fn close_local_settings(window: Webview, app: AppHandle) -> Result<(), String> {
    require_control_window(&window)?;
    window.close().map_err(|error| error.to_string())?;
    if let Some(main) = app.get_window("main") {
        let _ = main.show();
        let _ = main.set_focus();
    }
    Ok(())
}
