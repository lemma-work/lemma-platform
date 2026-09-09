use super::*;

pub(crate) fn start_impl(app: AppHandle) -> Result<(), String> {
    let mode = current_mode(&app);
    if mode == "undecided" {
        return Err("choose a connection mode first".into());
    }
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

pub(crate) fn stop_impl(app: AppHandle, include_infra: Option<bool>) -> Result<(), String> {
    if app.state::<Shell>().quit_confirmed.load(Ordering::Acquire) {
        stop_then_quit(&app);
        return Ok(());
    }
    if current_mode(&app) != "local" {
        return Err("local services are not active in Lemma Cloud mode".into());
    }
    // Stop never installs a runtime or starts a replacement daemon. In
    // particular, quitting a damaged installation must not start a download.
    if app.state::<Shell>().locald_writer.lock().unwrap().is_none() {
        let connection = connect_locald()?;
        install_locald_connection(&app, connection);
    }
    // Only put the splash up once the daemon has actually taken the stop.
    // Showing it first meant a refused operation left a "stopping Lemma"
    // screen in front of a stack that was never asked to stop.
    send_local_operation(
        &app,
        json!({"cmd": "stop", "infra": include_infra.unwrap_or(false)}),
        operation_id("shell-stop"),
    )?;
    show_splash_with_intent(&app, "stop");
    Ok(())
}

pub(crate) fn restart_impl(app: AppHandle) -> Result<(), String> {
    if current_mode(&app) != "local" {
        return Err("local services are not active in Lemma Cloud mode".into());
    }
    ensure_locald(&app)?;
    send_local_operation(
        &app,
        json!({"cmd": "restart"}),
        operation_id("shell-restart"),
    )?;
    show_splash(&app);
    Ok(())
}

pub(crate) fn open_app_impl(app: AppHandle) -> Result<(), String> {
    let target = app_base_url(&app)?;
    open_app_window(&app, &target)
}

pub(crate) fn open_logs_impl() -> Result<(), String> {
    reveal_path(&locald_root())
}

#[tauri::command]
/// Runs off the UI thread. A synchronous `#[tauri::command]` is dispatched on
/// the main thread, so any command that waits on the daemon, the network or a
/// child process freezes the window for its whole duration -- which is how a
/// first launch showed a black, unresponsive app for minutes while the runtime
/// installed and the daemon came up.
pub(crate) async fn start(window: Webview, app: AppHandle) -> Result<(), String> {
    require_agent_host_caller(&window, &app)?;
    require_local_native_window(&window)?;
    tauri::async_runtime::spawn_blocking(move || start_impl(app))
        .await
        .map_err(|error| error.to_string())?
}

#[tauri::command]
/// Runs off the UI thread. A synchronous `#[tauri::command]` is dispatched on
/// the main thread, so any command that waits on the daemon, the network or a
/// child process freezes the window for its whole duration -- which is how a
/// first launch showed a black, unresponsive app for minutes while the runtime
/// installed and the daemon came up.
pub(crate) async fn stop(
    window: Webview,
    app: AppHandle,
    include_infra: Option<bool>,
) -> Result<(), String> {
    require_local_native_window(&window)?;
    tauri::async_runtime::spawn_blocking(move || stop_impl(app, include_infra))
        .await
        .map_err(|error| error.to_string())?
}

#[tauri::command]
/// Runs off the UI thread. A synchronous `#[tauri::command]` is dispatched on
/// the main thread, so any command that waits on the daemon, the network or a
/// child process freezes the window for its whole duration -- which is how a
/// first launch showed a black, unresponsive app for minutes while the runtime
/// installed and the daemon came up.
pub(crate) async fn restart(window: Webview, app: AppHandle) -> Result<(), String> {
    require_local_native_window(&window)?;
    tauri::async_runtime::spawn_blocking(move || restart_impl(app))
        .await
        .map_err(|error| error.to_string())?
}

#[tauri::command]
/// Runs off the UI thread. A synchronous `#[tauri::command]` is dispatched on
/// the main thread, so any command that waits on the daemon, the network or a
/// child process freezes every window for its whole duration.
pub(crate) async fn open_app(app: AppHandle) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || open_app_impl(app))
        .await
        .map_err(|error| error.to_string())?
}

#[tauri::command(async)]
pub(crate) fn open_logs(window: Webview) -> Result<(), String> {
    require_local_native_window(&window)?;
    open_logs_impl()
}
