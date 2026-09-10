use super::*;

/// Match the Dock icon to whether anything is actually on screen.
///
/// `Accessory` removes the Dock tile and the app menu bar, which is right when
/// the last window has gone: there is nothing for those menus to act on, and
/// every verb they carry is in the tray menu too.
///
/// Conditional on *every* window, not just the main one. A pod app opens in a
/// window of its own, so closing the workspace while an app is still up used to
/// drop the Dock tile out from under a window that was still visible -- leaving
/// it unreachable by ⌘-tab and belonging to an app the Dock said was not there.
#[cfg(target_os = "macos")]
pub(crate) fn settle_dock_presence(app: &AppHandle) {
    // Minimised counts. `is_visible()` is `[NSWindow isVisible]`, which is NO
    // for a miniaturised window -- so minimising a pod app and then closing the
    // workspace dropped the Dock tile while that app was still there, sitting
    // in the Dock as a window belonging to an application the Dock no longer
    // showed, with no way to ⌘-tab back to it.
    let anything_on_screen = app.windows().values().any(|window| {
        window.is_visible().unwrap_or(false) || window.is_minimized().unwrap_or(false)
    });
    let _ = app.set_activation_policy(if anything_on_screen {
        tauri::ActivationPolicy::Regular
    } else {
        tauri::ActivationPolicy::Accessory
    });
}

/// Come back to the Dock. Called on every path that puts the window back on
/// screen, because a visible window with no Dock icon cannot be ⌘-tabbed to and
/// looks like a different app's stray panel.
#[cfg(target_os = "macos")]
pub(crate) fn restore_dock_presence(app: &AppHandle) {
    let _ = app.set_activation_policy(tauri::ActivationPolicy::Regular);
}

/// No Dock to leave or return to off macOS; the tray behaviour is the same.
#[cfg(not(target_os = "macos"))]
pub(crate) fn restore_dock_presence(_app: &AppHandle) {}

/// Bring a window to the front, rather than merely making it visible.
///
/// `show()` un-hides a window. On macOS it does not make the *application*
/// active, so a window shown while something else is frontmost stays behind it
/// — and a Dock or menu Quit is exactly that case. The quit confirmation was
/// created correctly, focused its own webview correctly, and appeared behind
/// whatever the person was actually looking at, so the app read as having
/// ignored them.
///
/// The steps are passed in so the order can be tested. It is the order that
/// went wrong: focusing the overlay is not focusing the window that holds it,
/// and neither is showing it.
pub(crate) fn bring_to_front(
    show: impl FnOnce() -> Result<(), String>,
    unminimize: impl FnOnce() -> Result<(), String>,
    focus: impl FnOnce() -> Result<(), String>,
) -> Result<(), String> {
    show()?;
    // A minimized window cannot take focus, and a restore that fails is not a
    // reason to skip asking: the window may not have been minimized at all.
    let _ = unminimize();
    focus()
}

/// The same three steps against a real window.
pub(crate) fn bring_window_to_front(window: &tauri::Window) -> Result<(), String> {
    bring_to_front(
        || window.show().map_err(|error| error.to_string()),
        || window.unminimize().map_err(|error| error.to_string()),
        || window.set_focus().map_err(|error| error.to_string()),
    )
}

pub(crate) fn open_app_window(app: &AppHandle, url: &str) -> Result<(), String> {
    let target = tauri::Url::parse(url).map_err(|error| format!("invalid app URL: {error}"))?;
    // Rebuilt rather than refused when it is missing. The window is destroyed
    // and recreated when the user changes servers, so "not available" is now a
    // state the app can legitimately be in -- and if the recreate failed, this
    // is the path the tray's Open uses to ask for it again. Refusing here would
    // leave a running app whose only interface is a tray icon that cannot open
    // anything, with no way back except quitting.
    if app.get_window("main").is_none() {
        let mode = current_mode(app);
        build_main_window(app, &mode, WebviewUrl::App("index.html".into()), true)
            .map_err(|error| format!("could not reopen the window: {error}"))?;
    }
    let window = app
        .get_window("main")
        .ok_or("main window is not available")?;
    app.get_webview("main")
        .ok_or("main webview is not available")?
        .navigate(target)
        .map_err(|error| format!("could not open {url}: {error}"))?;
    // Before showing, so the icon and the window arrive together rather than
    // the window appearing under a Dock that has not noticed yet.
    restore_dock_presence(app);
    // Reported rather than discarded: a window that would not show or take
    // focus has not opened, and returning `Ok` there told the caller it had.
    bring_window_to_front(&window)
}

/// Should a `ready` event navigate the main window to `workspace`?
///
/// Yes from the splash, which is the ordinary first start. Also yes from a
/// *different* workspace origin, which is the case an optimistic resume creates:
/// the window opened the workspace the last session left, and then the stack it
/// was pointing at was replaced and came back on new ports. Nothing was showing
/// the splash at that point, so the old splash-only test left the window on a
/// dead port forever.
///
/// Still no from the current workspace. Product spec §3.5 is explicit that a
/// stable workspace must not be navigated for transient component recovery, and
/// re-navigating it would throw away whatever the user was doing.
pub(crate) fn main_window_needs_workspace(app: &AppHandle, workspace: &str) -> bool {
    let Some(url) = app.get_webview("main").and_then(|window| window.url().ok()) else {
        return false;
    };
    if native_splash_url(&url) {
        return true;
    }
    !same_origin(&url, workspace)
}

pub(crate) fn navigate_app_window(app: &AppHandle, url: &str) -> Result<(), String> {
    open_app_window(app, url)
}

/// Where the Dock icon should take somebody when every window is closed.
///
/// Separate from the arm that uses it because that arm needs a Tauri runtime
/// and this is the part with cases in it. Splash is the fallback on purpose:
/// while the stack is still coming up, or after it failed, the splash is where
/// the state and the actions are, and a workspace URL that is not serving yet
/// would open on an error page instead.
#[cfg(target_os = "macos")]
#[derive(Debug, PartialEq, Eq)]
pub(crate) enum ReopenTarget {
    Hosted,
    Workspace(String),
    Splash,
}

#[cfg(target_os = "macos")]
pub(crate) fn reopen_target(mode: &str, ready: bool, error: bool, url: &str) -> ReopenTarget {
    match mode {
        // Nothing local has to be ready for hosted to be reachable.
        "hosted" => ReopenTarget::Hosted,
        "local" if ready && !error && !url.is_empty() => ReopenTarget::Workspace(url.to_owned()),
        _ => ReopenTarget::Splash,
    }
}

pub(crate) fn build_main_window(
    handle: &AppHandle,
    mode: &str,
    initial_url: WebviewUrl,
    partitioned: bool,
) -> tauri::Result<tauri::WebviewWindow> {
    build_main_window_at(handle, mode, initial_url, partitioned, false, None)
}

pub(crate) fn build_main_window_at(
    handle: &AppHandle,
    mode: &str,
    initial_url: WebviewUrl,
    partitioned: bool,
    // True only for a rebuild: a window already existed and the user was looking
    // at it, so this one stays off screen until it has something to show.
    replacing: bool,
    // Where that window was, when it could be asked. Absent is not a reason to
    // guess -- an unplaced window lands where the OS puts it, which is what
    // happened before and is still better than moving somebody's window
    // somewhere arbitrary.
    placement: Option<WindowPlacement>,
) -> tauri::Result<tauri::WebviewWindow> {
    let main_builder = WebviewWindowBuilder::new(handle, "main", initial_url)
        .title("Lemma")
        .inner_size(1280.0, 860.0)
        .min_inner_size(980.0, 680.0)
        .devtools(true)
        // Corrected to the real appearance immediately after build.
        // Light is the safer guess to start from: a white flash reads
        // as a page loading, a black one reads as a broken app.
        .background_color(CANVAS_LIGHT)
        .initialization_script(desktop_context_script(mode))
        .on_navigation({
            let handle = handle.clone();
            move |url| {
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
            let handle = handle.clone();
            move |url, _features| {
                let (mode, app_base, api_base) = navigation_context(&handle);
                match new_window_disposition(&url, &mode, &app_base, &api_base) {
                    NewWindowDisposition::NavigateInApp => {
                        let _ = navigate_app_window(&handle, url.as_str());
                    }
                    NewWindowDisposition::OpenAppWindow => {
                        // Logged rather than discarded: every failure here ends
                        // with the user clicking "open in new window" and
                        // nothing happening at all, which is indistinguishable
                        // from a dead button.
                        if let Err(error) = open_pod_app_window(&handle, url.as_str()) {
                            append_install_log(&format!(
                                "could not open a pod app window: {error}"
                            ));
                        }
                    }
                    NewWindowDisposition::OpenExternal => {
                        open_external(url.as_str());
                    }
                    NewWindowDisposition::Deny => {}
                }
                NewWindowResponse::Deny
            }
        })
        .on_download({
            let handle = handle.clone();
            move |_webview, event| match event {
                DownloadEvent::Requested { url, .. } => {
                    let (mode, app_base, api_base) = navigation_context(&handle);
                    download_disposition(&url, &mode, &app_base, &api_base)
                }
                _ => true,
            }
        });

    // Native materials. Vibrancy is only ever visible where the web
    // content declines to paint, so the window has to be transparent
    // for any of it to show — which also means every surface that
    // *should* stay opaque has to say so itself. That sweep is not
    // done, so this stays behind a flag: without it the app composites
    // exactly as it did before, and with it the [data-desktop-vibrancy]
    // rules in styles/tokens.css open up the shell rail.
    #[cfg(target_os = "macos")]
    let main_builder = if desktop_vibrancy_enabled() {
        main_builder.transparent(true)
    } else {
        main_builder
    };

    // Which storage this window gets. Set here because it is a
    // builder-time property: a live webview cannot be moved between
    // stores, which is why switching servers rebuilds the window
    // rather than clearing the one store both used to share.
    #[cfg(target_os = "macos")]
    let main_builder = if partitioned {
        main_builder.data_store_identifier(session_partition_id(mode))
    } else {
        main_builder
    };
    #[cfg(target_os = "windows")]
    let main_builder = if partitioned {
        main_builder.data_directory(session_partition_dir(mode))
    } else {
        main_builder
    };
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    let _ = partitioned;

    // A replacement window is built where the old one stood and stays off screen
    // until it has something to show. Built visible, the swap is a window
    // vanishing, a gap, and a different window appearing at the OS default
    // placement with a blank page loading in it.
    let main_builder = if replacing {
        main_builder.visible(false).on_page_load(|window, payload| {
            if matches!(payload.event(), tauri::webview::PageLoadEvent::Finished) {
                let _ = window.show();
                let _ = window.set_focus();
            }
        })
    } else {
        main_builder
    };
    // A rebuild is told exactly where to sit. A cold start has only what the
    // last session left behind -- and `None` from either is not a reason to
    // guess: an unplaced window lands where the OS puts it, which is right for
    // a first-ever launch and safe for everything else.
    let placement = placement.or_else(|| remembered_placement(handle));
    let main_builder = match placement
        .and_then(|saved| placement_in_logical(&saved, placement_scale_factor(handle, &saved)))
    {
        Some((position, size)) => main_builder
            .position(position.x, position.y)
            .inner_size(size.width, size.height),
        None => main_builder,
    };

    let main = main_builder.build()?;

    // The builder had to guess an appearance before the window existed.
    // Now that it does, ask it, and keep asking: a window whose layer
    // stays light while the page goes dark flashes white on every
    // navigation, which is the same bug with the colours swapped.
    if let Ok(theme) = main.theme() {
        let _ = main.set_background_color(Some(canvas_color(theme)));
    }
    main.on_window_event({
        let window = main.clone();
        move |event| match event {
            tauri::WindowEvent::ThemeChanged(theme) => {
                let _ = window.set_background_color(Some(canvas_color(*theme)));
            }
            // Where the user put the window, kept as they put it. Recorded here
            // rather than on quit alone: an app that is force-killed or
            // replaced by an update never sees a close event, and those are
            // the launches where coming back wrong is most annoying.
            tauri::WindowEvent::Moved(_) | tauri::WindowEvent::Resized(_) => {
                remember_placement(&window);
            }
            _ => {}
        }
    });

    #[cfg(target_os = "macos")]
    if desktop_vibrancy_enabled() {
        use window_vibrancy::{apply_vibrancy, NSVisualEffectMaterial, NSVisualEffectState};

        // Sidebar is the material AppKit itself uses behind source
        // lists, which is what the pod shell rail is.
        // The attribute itself rides in the initialization script, so it
        // is already set on this document and on every document the
        // window navigates to afterwards. Only the failure path needs
        // to say anything here, and it takes the attribute back off so
        // the page is not styled for a material that is not there.
        if let Err(error) = apply_vibrancy(
            &main,
            NSVisualEffectMaterial::Sidebar,
            Some(NSVisualEffectState::FollowsWindowActiveState),
            None,
        ) {
            eprintln!("lemma: could not apply window vibrancy: {error}");
            let _ = main.eval("document.documentElement.removeAttribute('data-desktop-vibrancy')");
        }
    }

    // Only relevant when the OS accent is driving the palette. The
    // accent is read once at launch, so it would otherwise go stale the
    // moment the user changes it in System Settings; re-reading on focus
    // catches exactly that — they leave to change it and come back.
    if desktop_system_accent_enabled() {
        main.on_window_event({
            let window = main.clone();
            move |event| {
                if matches!(
                    event,
                    tauri::WindowEvent::Focused(true) | tauri::WindowEvent::ThemeChanged(_)
                ) {
                    let _ = window.eval(format!(
                        "document.documentElement.style.setProperty('--accent-rgb','{}')",
                        accent_channel_triple(),
                    ));
                }
            }
        });
    }

    if replacing {
        // Shown by the page-load hook once there is something to show, and by
        // this backstop if that never arrives. An invisible window is worse than
        // a flicker, so the deadline gives up rather than leaving the app with
        // no interface -- the same shape as the label wait above it.
        let pending = main.clone();
        std::thread::spawn(move || {
            std::thread::sleep(REPLACEMENT_REVEAL_TIMEOUT);
            if !pending.is_visible().unwrap_or(false) {
                let _ = pending.show();
                let _ = pending.set_focus();
            }
        });
    } else {
        main.show()?;
        main.set_focus()?;
    }
    if std::env::var("LEMMA_DESKTOP_DEVTOOLS").as_deref() == Ok("1") {
        main.open_devtools();
    }
    Ok(main)
}

/// Poll `still_registered` until it goes false, or `timeout` elapses.
///
/// Returns whether the label came free. Takes the predicate rather than an
/// `AppHandle` so the waiting itself can be tested without a running event
/// loop -- which is the half that was wrong, and the half a source-text
/// assertion could not have caught.
pub(crate) fn wait_until_label_released(
    mut still_registered: impl FnMut() -> bool,
    timeout: Duration,
    interval: Duration,
) -> bool {
    let deadline = Instant::now() + timeout;
    loop {
        if !still_registered() {
            return true;
        }
        if Instant::now() >= deadline {
            return false;
        }
        std::thread::sleep(interval);
    }
}

pub(crate) fn rebuild_main_window_for_mode(app: &AppHandle, mode: &str) {
    let Some(existing) = app.get_window("main") else {
        // Nothing built yet -- `setup` will create it against the right store.
        return;
    };
    // Held across both halves, and cleared on every path out. `destroy` is
    // deliberate rather than `close`: the app hides to tray on CloseRequested,
    // so `close` would leave the old window alive on the old store and no new
    // one would ever be built.
    // Before the swap flag, so a pod app window cannot be mistaken for the
    // "no windows left" state the flag exists to cover.
    close_pod_app_window(app);
    let shell: State<Shell> = app.state();
    shell.swapping_window.store(true, Ordering::Release);
    let _reset = ExitGuard(app.clone());
    // Read while there is still a window to read it from.
    let placement = placement_of(&existing);
    if let Err(error) = existing.destroy() {
        append_install_log(&format!(
            "[connection-mode] could not close the previous server's window: {error}"
        ));
        return;
    }
    // `destroy` only *posts* the request. Tauri frees the label when the event
    // loop processes `Destroyed` and the manager drops it from its webview map,
    // and this function runs on a blocking thread -- so building here races the
    // event loop and loses, every time, with `a webview with label \`main\`
    // already exists`. The retry below inherited the same failure in the same
    // millisecond, which turned the fallback into a second identical attempt
    // and left the app with no window at all.
    if !wait_until_label_released(
        || app.get_window("main").is_some(),
        LABEL_RELEASE_TIMEOUT,
        LABEL_RELEASE_POLL,
    ) {
        append_install_log(
            "[connection-mode] the previous window still holds the `main` label; \
             building anyway",
        );
    }
    // Rebuilt on the splash rather than on the destination: `set_mode`'s caller
    // navigates immediately afterwards, and for local it has to wait for the
    // daemon first. Starting anywhere else would show one server's page against
    // the other's storage for as long as that takes.
    let initial = WebviewUrl::App("index.html".into());
    // `Some` even when the geometry could not be read: it is what marks this a
    // replacement, and a replacement is hidden until it paints whether or not it
    // also knows where to sit.
    if let Err(error) = build_main_window_at(app, mode, initial.clone(), true, true, placement) {
        // Falling back to the shared store, not to nothing. Per-webview storage
        // is the newer half of this: macOS needs 14 (which the bundle already
        // requires) and Windows gives each webview its own WebView2 environment,
        // which is not something this can prove on every machine it will run on.
        // Losing the partition costs the isolation and restores exactly the
        // behaviour that shipped before it; losing the window leaves the user
        // with an app that has no interface at all.
        append_install_log(&format!(
            "[connection-mode] partitioned window failed for {mode}, \
             falling back to shared storage: {error}"
        ));
        let _ = wait_until_label_released(
            || app.get_window("main").is_some(),
            LABEL_RELEASE_TIMEOUT,
            LABEL_RELEASE_POLL,
        );
        if let Err(error) = build_main_window_at(app, mode, initial, false, true, placement) {
            append_install_log(&format!(
                "[connection-mode] could not reopen the window for {mode}: {error}"
            ));
        }
    }
}

// ---------------------------------------------------------------------------
// Navigation policy: ordinary web navigations stay in the primary webview so
// cross-origin app and widget iframes behave exactly as they do in a browser.
// Explicit new-window requests and marked desktop auth still belong in the
// system browser.
// ---------------------------------------------------------------------------
