//! Framing a pod app next to the agent, on macOS.
//!
//! WKWebView treats every `*.localhost` host as its own site, so an app on
//! `<slug>.apps.lemma.localhost` framed by the workspace on
//! `app.lemma.localhost` is third-party and gets no cookies. The same host on
//! another port is same-site. So the workspace asks this command for an alias
//! of the app's canonical URL -- `http://app.lemma.localhost:<alias port>/...`,
//! served by locald (`lemma_locald::app_alias`) -- and frames that.
//!
//! The canonical URL is still what every API returns, what "open in a new
//! window" opens, and what WebView2 and every browser frame: none of them need
//! this. Off macOS the command hands the canonical URL straight back.
//!
//! What an alias origin may do in this window, and no more:
//!
//! - load in a frame on the workspace (`alias_target_for`);
//! - never become the top-level page -- the main window navigates back;
//! - never reach a command. The workspace capability names the workspace's
//!   exact origin (`grant_local_workspace_capability`), port included, and
//!   every caller check compares full origins, so an alias origin -- same
//!   host, different port -- matches neither.

use super::*;

/// How long locald gets to hand back an alias. Allocating one is a bind.
const ALIAS_TIMEOUT: Duration = Duration::from_secs(10);

/// Whether this platform's webview needs an alias to frame an app signed in.
///
/// WebView2 treats `*.lemma.localhost` as one site, like every Chromium; only
/// WebKit derives a site per `*.localhost` host.
pub(crate) const FRAMES_NEED_ALIAS: bool = cfg!(target_os = "macos");

/// The URL the workspace should frame for `canonical`, decided without I/O.
///
/// `Ok(None)` means frame the canonical URL as it is. Refuses anything that is
/// not this installation's own published app, so the command cannot be used
/// to make locald front some other address.
pub(crate) fn frame_plan(
    canonical: &str,
    api_base: &str,
    needs_alias: bool,
) -> Result<Option<String>, String> {
    let url = tauri::Url::parse(canonical).map_err(|_| "that is not an app URL".to_owned())?;
    if !owned_published_app(&url, api_base) {
        return Err("only this computer's own Lemma apps open here".into());
    }
    Ok(needs_alias.then(|| url.to_string()))
}

/// Check the alias locald answered with before letting a frame load it.
///
/// Same scheme and host as the workspace, a port that is neither the
/// workspace's nor the API's, and nothing but a path after it.
pub(crate) fn accepted_alias(alias: &str, app_base: &str, api_base: &str) -> Option<u16> {
    let (Ok(alias), Ok(app), Ok(api)) = (
        tauri::Url::parse(alias),
        tauri::Url::parse(app_base),
        tauri::Url::parse(api_base),
    ) else {
        return None;
    };
    let port = alias.port()?;
    (alias.scheme() == "http"
        && app.scheme() == "http"
        && alias.username().is_empty()
        && alias.password().is_none()
        && alias.host_str().is_some()
        && alias.host_str() == app.host_str()
        && app.host_str().is_some_and(trusted_local_workspace_host)
        && Some(port) != app.port()
        && Some(port) != api.port())
    .then_some(port)
}

/// The origin part of a canonical app URL: `http://orders.apps.lemma.localhost:52414`.
fn canonical_origin(canonical: &str) -> Option<String> {
    let url = tauri::Url::parse(canonical).ok()?;
    Some(format!(
        "{}://{}:{}",
        url.scheme(),
        url.host_str()?,
        url.port_or_known_default()?
    ))
}

fn frame_url_impl(app: AppHandle, canonical: String) -> Result<Value, String> {
    let (_mode, app_base, api_base) = navigation_context(&app);
    let Some(canonical) = frame_plan(&canonical, &api_base, FRAMES_NEED_ALIAS)? else {
        return Ok(json!({ "url": canonical, "aliased": false }));
    };
    let response = locald_request(
        json!({
            "cmd": "app-alias.resolve",
            "id": operation_id("app-alias"),
            "url": canonical,
        }),
        ALIAS_TIMEOUT,
    )?;
    let alias = response
        .get("url")
        .and_then(Value::as_str)
        .ok_or("Lemma did not answer with an address for this app")?
        .to_owned();
    let port = accepted_alias(&alias, &app_base, &api_base)
        .ok_or("Lemma answered with an address this window will not frame")?;
    let origin = canonical_origin(&canonical).ok_or("that is not an app URL")?;
    let shell: State<Shell> = app.state();
    shell.app_aliases.lock_or_recover().insert(port, origin);
    Ok(json!({ "url": alias, "aliased": true }))
}

#[tauri::command]
/// The address to frame a pod app at, from the local workspace only.
pub(crate) async fn app_frame_url(
    window: Webview,
    app: AppHandle,
    url: String,
) -> Result<Value, String> {
    require_local_settings_caller(&window, &app)?;
    tauri::async_runtime::spawn_blocking(move || frame_url_impl(app, url))
        .await
        .map_err(|error| error.to_string())?
}

/// The canonical URL an alias stands for, if `url` is one this run handed out.
pub(crate) fn alias_target_for(app: &AppHandle, url: &tauri::Url) -> Option<String> {
    let (mode, app_base, api_base) = navigation_context(app);
    if mode != "local" {
        return None;
    }
    let shell: State<Shell> = app.state();
    let aliases = shell.app_aliases.lock_or_recover();
    app_alias_target(url, &app_base, &api_base, &aliases)
}
