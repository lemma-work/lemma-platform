use super::*;

pub(crate) fn confirm_destructive_action_impl(
    app: AppHandle,
    title: String,
    message: String,
    confirm_label: String,
) -> Result<bool, String> {
    show_app_prompt(app, title, message, confirm_label, true)
}

pub(crate) fn show_app_prompt(
    app: AppHandle,
    title: String,
    message: String,
    confirm_label: String,
    cancelable: bool,
) -> Result<bool, String> {
    show_app_decision(app, title, message, confirm_label, cancelable, false)
        .map(|decision| decision == confirmation::Decision::Confirm)
}

pub(crate) fn show_app_decision(
    app: AppHandle,
    title: String,
    message: String,
    confirm_label: String,
    cancelable: bool,
    allow_discard: bool,
) -> Result<confirmation::Decision, String> {
    let shell: State<Shell> = app.state();
    let id = operation_id("confirmation");
    let receiver = shell.confirmations.begin(id.clone(), allow_discard)?;
    let result = create_confirmation_overlay(
        &app,
        &id,
        title,
        message,
        confirm_label,
        cancelable,
        allow_discard,
    );
    if let Err(error) = result {
        shell.confirmations.cancel();
        close_confirmation_overlay(&app);
        return Err(error);
    }
    receiver
        .recv()
        .map_err(|_| "The confirmation closed without a decision.".into())
}

pub(crate) fn create_confirmation_overlay(
    app: &AppHandle,
    id: &str,
    title: String,
    message: String,
    confirm_label: String,
    cancelable: bool,
    allow_discard: bool,
) -> Result<(), String> {
    let main = app
        .get_window("main")
        .ok_or("The app window is unavailable.")?;
    restore_dock_presence(app);
    main.show().map_err(|error| error.to_string())?;
    let payload = json!({"id": id, "title": title, "message": message, "confirmLabel": confirm_label, "cancelable": cancelable, "allowDiscard": allow_discard});
    let builder = WebviewBuilder::new("confirmation", WebviewUrl::App("confirmation.html".into()))
        .auto_resize()
        .focused(true)
        .initialization_script(format!("window.__LEMMA_CONFIRMATION__={payload};"))
        .on_navigation(|url| trusted_native_asset_url(url) && url.path() == "/confirmation.html")
        .on_new_window(|_, _| NewWindowResponse::Deny);
    let parent = &main;
    let size = parent.inner_size().map_err(|error| error.to_string())?;
    let overlay = parent
        .add_child(builder, PhysicalPosition::new(0, 0), size)
        .map_err(|error| error.to_string())?;
    overlay.set_focus().map_err(|error| error.to_string())
}

pub(crate) fn close_confirmation_overlay(app: &AppHandle) {
    remove_confirmation_overlay(app);
    if let Some(previous) = app
        .get_webview("control")
        .or_else(|| app.get_webview("main"))
    {
        let _ = previous.set_focus();
    }
}

pub(crate) fn remove_confirmation_overlay(app: &AppHandle) {
    if let Some(overlay) = app.get_webview("confirmation") {
        let _ = overlay.close();
    }
}

/// Ask the user to confirm something destructive, from the settings page.
///
/// `window.confirm()` is not usable here: WKWebView routes it through a
/// WKUIDelegate panel that wry does not implement, so it returns false without
/// ever drawing anything. Local settings used that to gate "Stop everything"
/// and "Verify & repair runtime", which made both buttons look inert on macOS
/// -- the click was received and then silently discarded.
///
/// The prompt uses a trusted, app-owned overlay shared with the tray and quit
/// path, with Cancel focused before any destructive action can proceed.
#[tauri::command]
pub(crate) async fn confirm_destructive_action(
    window: Webview,
    app: AppHandle,
    title: String,
    message: String,
    confirm_label: String,
) -> Result<bool, String> {
    require_control_window(&window)?;
    // Async because this waits for a dialog that can only be *shown* from
    // the main thread. As a synchronous command it ran there itself, so it
    // blocked the very thread that had to draw what it was waiting for.
    tauri::async_runtime::spawn_blocking(move || {
        confirm_destructive_action_impl(app, title, message, confirm_label)
    })
    .await
    .map_err(|error| error.to_string())?
}

#[tauri::command]
pub(crate) async fn confirm_settings_changes(
    window: Webview,
    app: AppHandle,
) -> Result<confirmation::Decision, String> {
    require_control_window(&window)?;
    tauri::async_runtime::spawn_blocking(move || {
        show_app_decision(
            app,
            "Save your settings changes?".into(),
            "Save each changed section, discard your drafts, or keep editing.".into(),
            "Save changes".into(),
            true,
            true,
        )
    })
    .await
    .map_err(|error| error.to_string())?
}

#[tauri::command]
pub(crate) async fn resolve_confirmation(
    window: Webview,
    app: AppHandle,
    id: String,
    decision: confirmation::Decision,
) -> Result<(), String> {
    if window.label() != "confirmation"
        || !window
            .url()
            .is_ok_and(|url| trusted_native_asset_url(&url) && url.path() == "/confirmation.html")
    {
        return Err("Only the app confirmation can approve this action.".into());
    }
    let (sender, receiver) = std::sync::mpsc::sync_channel(1);
    let handle = app.clone();
    app.run_on_main_thread(move || {
        let shell: State<Shell> = handle.state();
        let result = shell.confirmations.resolve(&id, decision, || {
            window.close().map_err(|error| error.to_string())?;
            if let Some(previous) = handle
                .get_webview("control")
                .or_else(|| handle.get_webview("main"))
            {
                let _ = previous.set_focus();
            }
            Ok(())
        });
        let _ = sender.send(result);
    })
    .map_err(|error| error.to_string())?;
    tauri::async_runtime::spawn_blocking(move || {
        receiver
            .recv()
            .map_err(|_| "The confirmation closed without a decision.".to_string())?
    })
    .await
    .map_err(|error| error.to_string())?
}
