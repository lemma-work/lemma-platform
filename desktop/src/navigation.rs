use super::*;

pub(crate) fn same_origin(url: &tauri::Url, target: &str) -> bool {
    let Ok(target) = tauri::Url::parse(target) else {
        return false;
    };
    url.scheme() == target.scheme()
        && url.host_str() == target.host_str()
        && url.port_or_known_default() == target.port_or_known_default()
}

/// Whether `host` is the workspace host of a domain this build knows.
pub(crate) fn trusted_local_workspace_host(host: &str) -> bool {
    TRUSTED_LOCAL_BASES
        .iter()
        .any(|base| host == format!("app.{base}"))
}

pub(crate) fn trusted_workspace_urls(app_base: &str, api_base: &str) -> bool {
    let (Ok(app), Ok(api)) = (tauri::Url::parse(app_base), tauri::Url::parse(api_base)) else {
        return false;
    };
    if app.username() != ""
        || app.password().is_some()
        || app.query().is_some()
        || app.fragment().is_some()
        || api.username() != ""
        || api.password().is_some()
        || api.query().is_some()
        || api.fragment().is_some()
        || app.path() != "/"
    {
        return false;
    }

    if app.host_str().is_some_and(trusted_local_workspace_host) {
        let (Some(app_port), Some(api_port)) = (app.port(), api.port()) else {
            return false;
        };
        return app.scheme() == "http"
            && api.scheme() == "http"
            // The same host as the workspace, which the allowlist above has
            // already vetted. Checking the literal twice let the two drift;
            // what this arrangement actually requires is one hostname on two
            // ports.
            && api.host_str() == app.host_str()
            && api.path() == "/"
            && app_port >= 49_152
            && api_port >= 49_152
            && app_port != api_port;
    }

    same_origin(&api, app_base)
        && api.path() == "/_lemma/api"
        && matches!(app.scheme(), "http" | "https")
        && (app.scheme() == "https" || local_destination(&app, api_base))
}

pub(crate) fn is_desktop_browser_auth_url(url: &tauri::Url) -> bool {
    matches!(url.scheme(), "http" | "https")
        && url.path().starts_with("/auth")
        && url
            .query_pairs()
            .any(|(key, value)| key == "desktop_browser" && value == "1")
}

pub(crate) fn navigation_context(app: &AppHandle) -> (String, String, String) {
    let shell: State<Shell> = app.state();
    let ui = shell.ui.lock().unwrap();
    (ui.mode.clone(), ui.url.clone(), ui.api_url.clone())
}

/// The domain this installation is served under, from the API base it was given.
///
/// `http://app.127.0.0.1.sslip.io:63288` -> `127.0.0.1.sslip.io`. Derived rather
/// than compiled in, because the shell does not link locald -- it launches it --
/// so the hostname arrives at runtime in the `ready` event and this is the only
/// honest source for it.
pub(crate) fn local_base_domain(api_base: &str) -> Option<String> {
    let host = tauri::Url::parse(api_base)
        .ok()?
        .host_str()?
        .to_ascii_lowercase();
    let (_first, rest) = host.split_once('.')?;
    (!rest.is_empty()).then(|| rest.to_owned())
}

pub(crate) fn local_destination(url: &tauri::Url, api_base: &str) -> bool {
    let Some(host) = url.host_str() else {
        return false;
    };
    let host = host.to_ascii_lowercase();
    if host == "localhost" || host.ends_with(".localhost") {
        return true;
    }
    // The domain this installation serves itself under is a local destination
    // whatever it resolves through.
    //
    // This is the security-relevant half of moving off `*.localhost`. In local
    // mode the gate below *allows* anything that is not a local destination, on
    // the reasoning that an ordinary internet site is not a way to reach this
    // machine. A public name that answers 127.0.0.1 breaks that reasoning: every
    // `<anything>.127.0.0.1.sslip.io` is loopback, so without this the workspace
    // could be navigated to an attacker-chosen name and reach any port on the
    // user's machine -- a hole that does not exist today, because
    // `*.lemma.localhost` matches the check above and is denied unless it is
    // ours.
    if let Some(base) = local_base_domain(api_base) {
        if host == base || host.ends_with(&format!(".{base}")) {
            return true;
        }
    }
    let Ok(address) = host.parse::<IpAddr>() else {
        return false;
    };
    match address {
        IpAddr::V4(address) => {
            address.is_loopback()
                || address.is_private()
                || address.is_link_local()
                || address.is_unspecified()
        }
        IpAddr::V6(address) => {
            address.is_loopback()
                || address.is_unique_local()
                || address.is_unicast_link_local()
                || address.is_unspecified()
        }
    }
}

pub(crate) fn owned_published_app(url: &tauri::Url, api_base: &str) -> bool {
    let Ok(api) = tauri::Url::parse(api_base) else {
        return false;
    };
    url.scheme() == "http"
        && api.scheme() == "http"
        && url.port() == api.port()
        && url.host_str().is_some_and(|host| {
            TRUSTED_LOCAL_BASES
                .iter()
                .any(|base| host.ends_with(&format!(".apps.{base}")))
        })
}

/// The documents a frame renders without fetching anything: the content document
/// of an `srcdoc` iframe, and the blank document a frame starts life on.
pub(crate) fn frame_content_url(url: &tauri::Url) -> bool {
    url.scheme() == "about" && matches!(url.path(), "srcdoc" | "blank")
}

pub(crate) fn navigation_disposition(
    url: &tauri::Url,
    mode: &str,
    app_base: &str,
    api_base: &str,
) -> NavigationDisposition {
    if is_desktop_browser_auth_url(url) {
        NavigationDisposition::OpenExternal
    } else if frame_content_url(url) {
        // This handler sees every navigation in the window, not just the top
        // frame's: WKWebView asks its navigation delegate about subframes too,
        // and nothing between here and it filters on isMainFrame. So an iframe
        // rendering inline HTML arrives here as `about:srcdoc`, the scheme gate
        // below answered Cancel, and the frame stayed blank — no error, no
        // console entry. That was every HTML and .docx preview in the document
        // viewer, on macOS only, because the Windows webview reports main-frame
        // navigation alone.
        //
        // Admitting these two costs nothing at the top level: WebKit will not
        // navigate a main frame to `about:srcdoc` at all, and `about:blank` is
        // already reachable by any script the page can already run on itself.
        // `new_window_disposition` still refuses `about:blank` popups.
        NavigationDisposition::Allow
    } else if trusted_native_asset_url(url) {
        // Our own bundled pages, whichever origin this build serves them from.
        // Testing `scheme() == "tauri"` alone was right for a packaged app and
        // silently wrong for a dev one: `cargo tauri dev` serves those same
        // files over loopback http, and the local-mode branch below denies
        // every local http destination that is not the workspace. So the
        // splash was refused before it could paint, and the window stayed white
        // until the workspace URL replaced it a minute later.
        NavigationDisposition::Allow
    } else if !matches!(url.scheme(), "http" | "https") {
        NavigationDisposition::Deny
    } else if mode != "local"
        || same_origin(url, app_base)
        || same_origin(url, api_base)
        || owned_published_app(url, api_base)
        || !local_destination(url, api_base)
    {
        NavigationDisposition::Allow
    } else {
        NavigationDisposition::Deny
    }
}

pub(crate) fn new_window_disposition(
    url: &tauri::Url,
    mode: &str,
    app_base: &str,
    api_base: &str,
) -> NewWindowDisposition {
    if url.as_str() == "about:blank" {
        NewWindowDisposition::Deny
    } else if is_desktop_browser_auth_url(url) {
        NewWindowDisposition::OpenExternal
    } else if navigation_disposition(url, mode, app_base, api_base) == NavigationDisposition::Allow
        && owned_published_app(url, api_base)
    {
        // Tested before the in-app branch, which used to claim these: an app
        // opened "in a new window" replaced the workspace in the only window
        // there was, and the user's way back was to quit.
        NewWindowDisposition::OpenAppWindow
    } else if navigation_disposition(url, mode, app_base, api_base) == NavigationDisposition::Allow
        && (url.scheme() == "tauri" || same_origin(url, app_base) || same_origin(url, api_base))
    {
        NewWindowDisposition::NavigateInApp
    } else if navigation_disposition(url, mode, app_base, api_base) == NavigationDisposition::Allow
    {
        NewWindowDisposition::OpenExternal
    } else {
        NewWindowDisposition::Deny
    }
}

/// Where a download's bytes come from, for the purpose of trusting it. Every
/// download the workspace triggers is an `a[download]` click on an object URL
/// the page minted itself, which arrives as `blob:http://origin/uuid` — the
/// creating origin is the opaque path, and it is the only part worth judging.
pub(crate) fn download_source_url(url: &tauri::Url) -> Option<tauri::Url> {
    if url.scheme() == "blob" {
        return tauri::Url::parse(url.path()).ok();
    }
    Some(url.clone())
}

/// Whether to let a download proceed. Registering any policy at all is what
/// makes downloads work: with no download handler the webview cancels the
/// navigation outright, which is why Download buttons did nothing on macOS and
/// said nothing about it. The destination is left as the webview computed it —
/// the user's Downloads folder, uniquified against what is already there.
pub(crate) fn download_disposition(
    url: &tauri::Url,
    mode: &str,
    app_base: &str,
    api_base: &str,
) -> bool {
    let Some(source) = download_source_url(url) else {
        return false;
    };
    // Held to the same test as navigating there would be, which among other
    // things keeps `file:` and `data:` out.
    matches!(source.scheme(), "http" | "https")
        && navigation_disposition(&source, mode, app_base, api_base) == NavigationDisposition::Allow
}

/// Hand a URL to the user's browser.
///
/// The failure is logged rather than dropped. Every path that decides a link
/// belongs outside the app ends here, and a discarded error made a launch that
/// never happened look exactly like a link that was never clicked -- nothing
/// moves, nothing is said, and the only thing left to suspect is the link.
pub(crate) fn open_external(url: &str) {
    #[cfg(target_os = "macos")]
    let mut command = Command::new("/usr/bin/open");
    #[cfg(target_os = "windows")]
    let mut command = Command::new("explorer.exe");
    #[cfg(all(unix, not(target_os = "macos")))]
    let mut command = Command::new("xdg-open");
    if let Err(error) = command.arg(url).spawn() {
        append_install_log(&format!("could not open {url} in the browser: {error}"));
    }
}

pub(crate) fn handle_deep_link(app: &AppHandle, url: &tauri::Url) {
    if url.scheme() != "lemma" || url.host_str() != Some("auth") || url.path() != "/complete" {
        return;
    }
    if let Some(window) = app.get_window("main") {
        let _ = window.show();
        let _ = window.set_focus();
    }
    // Older builds used a second native auth webview. Hide it if it still
    // exists; the main window now owns the one-time session exchange.
    if let Some(window) = app.get_webview_window("auth") {
        let _ = window.hide();
    }
}
