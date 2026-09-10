use super::*;

/// Where a bundled page lives for the build we are actually running.
///
/// Navigation and privileged IPC must agree with the asset origin Tauri serves:
/// Windows rewrites the custom protocol to HTTP; development uses its own port.
pub(crate) fn native_asset_url(path: &str) -> String {
    native_assets::url(path, cfg!(debug_assertions).then_some(DEV_ASSET_PORT))
}

pub(crate) fn native_splash_url(url: &tauri::Url) -> bool {
    let path = matches!(url.path(), "/" | "/index.html");
    path && trusted_native_asset_url(url)
}

/// Open a published pod app in its own window.
///
/// It used to navigate the *main* window, which replaced the whole Lemma UI
/// with the app and left no way back -- no tab, no back button, nothing but
/// quitting. A real window can be closed, moved and ⌘-tabbed, and Lemma is
/// still there underneath when it goes.
///
/// Deliberately not covered by any file in `desktop/capabilities/`, which are
/// scoped by webview label: this window runs user-authored code, so it gets no
/// Tauri command surface at all. Adding a capability for this label would hand
/// every pod app the IPC bridge.
/// What to call an app's window: its public slug, made readable.
///
/// From the host, not the path -- the slug identifies the app and the path is
/// wherever the user happens to be inside it, so a window would otherwise be
/// renamed by navigation.
pub(crate) fn pod_app_window_title(target: &tauri::Url) -> String {
    target
        .host_str()
        .and_then(|host| host.split('.').next())
        .filter(|label| !label.is_empty())
        .map(|label| label.replace('-', " "))
        .unwrap_or_else(|| "Lemma app".to_owned())
}

pub(crate) fn open_pod_app_window(app: &AppHandle, url: &str) -> Result<(), String> {
    let target = tauri::Url::parse(url).map_err(|error| format!("invalid app URL: {error}"))?;

    // Named from the host rather than the path: the slug is the app's identity
    // and the path is wherever the user happens to be inside it.
    let title = pod_app_window_title(&target);

    if let Some(existing) = app.get_webview_window(POD_APP_WINDOW) {
        existing
            .navigate(target)
            .map_err(|error| format!("could not open {url}: {error}"))?;
        // Retitled, because the window is reused. Opening a second app left the
        // first one's name on the title bar, the Window menu and ⌘-tab, so the
        // window said "study lab" while rendering payroll.
        let _ = existing.set_title(&title);
        let _ = existing.show();
        let _ = existing.set_focus();
        restore_dock_presence(app);
        return Ok(());
    }

    let mode = current_mode(app);
    let builder = WebviewWindowBuilder::new(app, POD_APP_WINDOW, WebviewUrl::External(target))
        .title(title)
        .inner_size(1180.0, 800.0)
        .min_inner_size(420.0, 400.0)
        .background_color(CANVAS_LIGHT);

    // The same cookie jar the workspace uses, or the app has no session.
    //
    // A webview with no explicit store gets WebKit's default one, and the main
    // window is deliberately *not* on that -- it is partitioned per server so
    // signing into Local and Cloud are separate sessions. So an app window
    // without this line opens on an empty jar: the page renders, because assets
    // are served unauthenticated, and then every SDK call 401s. That is exactly
    // the bug this whole window exists downstream of, reintroduced one window
    // over, and it is invisible to a test that drives its own WKWebView.
    #[cfg(target_os = "macos")]
    let builder = builder.data_store_identifier(session_partition_id(&mode));
    #[cfg(target_os = "windows")]
    let builder = builder.data_directory(session_partition_dir(&mode));

    builder
        .on_navigation({
            let handle = app.clone();
            move |url| {
                // Held to the same gate as anywhere else, so an app cannot walk
                // this window somewhere the main one would have refused.
                let (mode, app_base, api_base) = navigation_context(&handle);
                match navigation_disposition(url, &mode, &app_base, &api_base) {
                    NavigationDisposition::Allow => true,
                    NavigationDisposition::OpenExternal => {
                        open_external(url.as_str());
                        false
                    }
                    NavigationDisposition::Deny => false,
                }
            }
        })
        .on_new_window({
            let handle = app.clone();
            move |url, _features| {
                // The same decision the workspace makes, not a looser one.
                //
                // This used to ask `navigation_disposition`, which answers a
                // different question: it admits `about:blank` and our own
                // bundled `tauri://` pages because subframes need them. Routed
                // through here that meant `window.open('about:blank')` from app
                // code reached `open_external` and launched the user's browser,
                // and a plain link back to the workspace opened Lemma in Safari
                // -- where there is no session at all.
                let (mode, app_base, api_base) = navigation_context(&handle);
                match new_window_disposition(&url, &mode, &app_base, &api_base) {
                    // A link to another app belongs in this window, which is
                    // the one the user is already looking at an app in.
                    NewWindowDisposition::OpenAppWindow | NewWindowDisposition::NavigateInApp => {
                        if let Err(error) = open_pod_app_window(&handle, url.as_str()) {
                            append_install_log(&format!(
                                "could not follow a link out of a pod app: {error}"
                            ));
                        }
                    }
                    NewWindowDisposition::OpenExternal => open_external(url.as_str()),
                    NewWindowDisposition::Deny => {}
                }
                NewWindowResponse::Deny
            }
        })
        .on_download({
            let handle = app.clone();
            move |_webview, event| match event {
                // Registering a policy at all is what makes downloads work:
                // with no handler the webview cancels the navigation outright
                // and says nothing, which is how Download buttons came to do
                // nothing on macOS. An app that exports a CSV is an ordinary
                // app, so this window needs the same policy the workspace has.
                DownloadEvent::Requested { url, .. } => {
                    let (mode, app_base, api_base) = navigation_context(&handle);
                    download_disposition(&url, &mode, &app_base, &api_base)
                }
                _ => true,
            }
        })
        .build()
        .map_err(|error| format!("could not open the app window: {error}"))?;
    Ok(())
}

pub(crate) fn show_splash(app: &AppHandle) {
    let _ = open_app_window(app, &native_asset_url("index.html"));
}

/// The splash, told what it is watching.
///
/// The intent rides in the query string because navigation discards the old
/// page. A stop must display shutdown progress even before its first state
/// event arrives. Startup itself is owned by the shell, never page loading.
pub(crate) fn show_splash_with_intent(app: &AppHandle, intent: &str) {
    let _ = open_app_window(
        app,
        &format!("{}?intent={intent}", native_asset_url("index.html")),
    );
}

/// Move the window onto the storage the new server owns.
///
/// A webview's store is fixed when it is built, so this closes the window and
/// builds it again. That is the price of real isolation, and it is paid only on
/// an actual server change: a restart, a reconnect, or a runtime coming back on
/// new ports all keep the window they have.
///
/// Failure is deliberately not fatal to the switch. The mode has already been
/// written and the caller is about to navigate; a window that is still on the
/// previous store shows the right server with the wrong cookie jar, which is
/// the behaviour that shipped before this existed. Refusing to switch servers
/// at all would be worse.
/// Close any pod app window, because what it is showing no longer exists.
///
/// An app window holds an absolute URL on the local backend's port. Switching
/// servers, or locald reallocating ports, leaves it pointed at something that
/// is gone -- and its navigation gate re-reads the mode live, so a window
/// opened under the local policy would start answering to the hosted one, where
/// every http(s) destination is allowed. Closing it is the honest option: it is
/// a view of a pod on a server this app is no longer connected to.
pub(crate) fn close_pod_app_window(app: &AppHandle) {
    if let Some(window) = app.get_webview_window(POD_APP_WINDOW) {
        let _ = window.destroy();
    }
}
