use super::*;

pub(crate) fn show_control_center(app: &AppHandle) -> Result<(), String> {
    show_control_center_page(app, None)
}

pub(crate) fn control_navigation_allowed(url: &tauri::Url) -> bool {
    trusted_control_url(url)
}

/// Normalise a Local settings page name, or say it is not one.
pub(crate) fn control_center_page(page: Option<&str>) -> Result<String, String> {
    let page = match page.unwrap_or("overview") {
        "connectors" => "integrations",
        "services" => "runtime",
        "surfaces" => "channels",
        page => page,
    };
    if !matches!(
        page,
        "overview"
            | "computer"
            | "ai"
            | "sharing"
            | "integrations"
            | "channels"
            | "runtime"
            | "updates"
            | "recovery"
            | "diagnostics"
    ) {
        return Err(format!("unknown Local settings page: {page}"));
    }
    Ok(page.to_owned())
}

/// Bring Local settings up on `page`, and finish only when it is up.
///
/// Blocking on purpose, and never to be called from the main thread: Tauri
/// documents a Windows deadlock when child webviews are created from
/// synchronous commands or event handlers. Run from a worker, `add_child`
/// marshals the build onto the main thread by itself.
pub(crate) fn open_control_center_blocking(app: &AppHandle, page: &str) -> Result<(), String> {
    if let Some(webview) = app.get_webview("control") {
        if let Some(main) = app.get_window("main") {
            restore_dock_presence(app);
            let _ = main.show();
            let _ = main.set_focus();
        }
        webview.set_focus().map_err(|error| error.to_string())?;
        let _ = app.emit_to("control", "lemma:control-page", page);
        return Ok(());
    }
    create_control_child(app, page)
}

pub(crate) fn show_control_center_page(app: &AppHandle, page: Option<&str>) -> Result<(), String> {
    let page = control_center_page(page)?;
    let handle = app.clone();
    // `menu_background`, rather than a bare thread that swallowed the result.
    // A failure here used to be announced as `lemma:control-error`, an event
    // with no listener anywhere in the app: choosing Local settings from the
    // menu and having it fail produced no window, no message, and nothing in
    // any log a person could reach. This writes the launch log and puts the
    // reason on screen, like every other menu action that fails.
    menu_background(app, "Local settings", move || {
        open_control_center_blocking(&handle, &page)
    });
    Ok(())
}

pub(crate) fn create_control_child(app: &AppHandle, page: &str) -> Result<(), String> {
    if app.get_webview("control").is_some() {
        let _ = app.emit_to("control", "lemma:control-page", page);
        return Ok(());
    }
    let main = app
        .get_window("main")
        .ok_or("main window is not available")?;
    restore_dock_presence(app);
    main.show().map_err(|error| error.to_string())?;
    let initial_script = format!(
        "{}window.__LEMMA_CONTROL_PAGE__={};",
        desktop_context_script(&current_mode(app)),
        serde_json::to_string(page).unwrap_or_else(|_| "\"overview\"".into())
    );
    let builder = WebviewBuilder::new("control", WebviewUrl::App("control.html".into()))
        .auto_resize()
        .focused(true)
        .initialization_script(initial_script)
        .on_navigation(move |url| {
            let allowed = control_navigation_allowed(url);
            if std::env::var("LEMMA_DESKTOP_CONTROL_DEBUG").as_deref() == Ok("1") || !allowed {
                eprintln!("[control-navigation] allowed={allowed} url={url}");
            }
            allowed
        })
        .on_new_window(move |url, _features| {
            if matches!(url.scheme(), "http" | "https") {
                open_external(url.as_str());
            }
            NewWindowResponse::Deny
        });
    let parent = &main;
    let size = parent.inner_size().map_err(|error| error.to_string())?;
    let webview = parent
        .add_child(builder, PhysicalPosition::new(0, 0), size)
        .map_err(|error| error.to_string())?;
    webview
        .set_auto_resize(true)
        .map_err(|error| error.to_string())?;
    webview.set_focus().map_err(|error| error.to_string())?;
    let _ = app.emit_to("control", "lemma:control-page", page);
    Ok(())
}

// ---------------------------------------------------------------------------
// Commands (same verbs as the Electron IPC surface)
// ---------------------------------------------------------------------------

pub(crate) fn is_control_window_label(label: &str) -> bool {
    label == "control"
}

pub(crate) fn trusted_control_url(url: &tauri::Url) -> bool {
    trusted_native_asset_url(url) && url.path() == "/control.html"
}

pub(crate) fn trusted_native_asset_url(url: &tauri::Url) -> bool {
    native_assets::is_trusted(url, cfg!(debug_assertions).then_some(DEV_ASSET_PORT))
}

pub(crate) fn require_control_window(window: &Webview) -> Result<(), String> {
    if !is_control_window_label(window.label()) {
        return Err(
            "this operation is available only in the privileged Local settings view".into(),
        );
    }
    let url = window
        .url()
        .map_err(|error| format!("could not inspect Local settings: {error}"))?;
    if !trusted_control_url(&url) {
        return Err("remote pages cannot use Local settings privileges".into());
    }
    Ok(())
}

pub(crate) fn require_local_native_window(window: &Webview) -> Result<(), String> {
    if !matches!(window.label(), "main" | "control") {
        return Err("this operation is available only in a Lemma native window".into());
    }
    let url = window
        .url()
        .map_err(|error| format!("could not inspect native window: {error}"))?;
    if !trusted_native_asset_url(&url)
        || (window.label() == "control" && !trusted_control_url(&url))
    {
        return Err("remote workspace pages cannot prepare the local runtime".into());
    }
    Ok(())
}

/// Open Local settings, and tell the caller whether it opened.
///
/// Awaited rather than fire-and-forget. This used to spawn a thread, return
/// `Ok` at once, and report failure by emitting `lemma:control-error` -- which
/// nothing in the app listens for. The splash's recovery button has a `.catch`
/// that therefore could never run, so a Local settings window that failed to
/// open left the user pressing a button that did nothing at all.
///
/// Async, so it is dispatched off the main thread and can wait for the answer;
/// that is also what keeps it clear of the Windows child-webview deadlock,
/// which is a hazard for *synchronous* commands.
#[tauri::command]
pub(crate) async fn open_control_center(
    app: AppHandle,
    page: Option<String>,
) -> Result<(), String> {
    let page = control_center_page(page.as_deref())?;
    tauri::async_runtime::spawn_blocking(move || open_control_center_blocking(&app, &page))
        .await
        .map_err(|error| error.to_string())?
}
