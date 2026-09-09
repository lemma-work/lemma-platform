use super::*;

pub(crate) fn read_resume_target() -> Option<ResumeTarget> {
    let config = read_config();
    let saved = config.get("resumeTarget")?;
    let text = |key: &str| saved.get(key)?.as_str().map(str::to_string);
    let url = text("url")?;
    let api_url = text("apiUrl")?;
    let generation = text("generation")?;
    let release = text("release")?;
    // A resume target belongs to the release that wrote it.
    //
    // Without this, installing a new build over an old one resumes the *old*
    // one's workspace: the previous stack is often still serving, so the probe
    // passes, the window opens it — and then `ensure_locald` finds a daemon that
    // does not match the new host pack, replaces it, and every service comes
    // back on new ports. The window is left pointed at a port nothing is
    // listening on, which is a permanently blank app.
    if release != env!("CARGO_PKG_VERSION") {
        return None;
    }
    if generation.is_empty() || !trusted_workspace_urls(&url, &api_url) {
        return None;
    }
    // A route is a bonus, not a requirement: an install that has only ever
    // reached the workspace root still resumes, it just resumes at the root.
    let route = text("route").filter(|route| route.starts_with('/'));
    Some(ResumeTarget {
        url,
        api_url,
        generation,
        release,
        route: route.unwrap_or_else(|| "/".into()),
    })
}

pub(crate) fn write_resume_target(url: &str, api_url: &str, generation: &str) {
    if generation.is_empty() || !trusted_workspace_urls(url, api_url) {
        return;
    }
    let _ = write_config(|config| {
        let entry = config
            .as_object_mut()
            .map(|object| object.entry("resumeTarget").or_insert_with(|| json!({})));
        if let Some(entry) = entry {
            entry["url"] = json!(url);
            entry["apiUrl"] = json!(api_url);
            entry["generation"] = json!(generation);
            entry["release"] = json!(env!("CARGO_PKG_VERSION"));
        }
    });
}

/// Remember where in the workspace the user was, so the next launch lands there
/// rather than on the root route that only redirects to it.
pub(crate) fn write_resume_route(route: &str) {
    if !route.starts_with('/') {
        return;
    }
    let _ = write_config(|config| {
        if let Some(entry) = config.get_mut("resumeTarget") {
            entry["route"] = json!(route);
        }
    });
}

/// Capture the workspace route the main window is currently showing.
///
/// Returns `None` for anything that is not the recorded workspace — the splash,
/// the installer, a published app — because none of those are somewhere to
/// resume to.
pub(crate) fn current_workspace_route(app: &AppHandle, target: &ResumeTarget) -> Option<String> {
    let url = app.get_webview("main")?.url().ok()?;
    if !same_origin(&url, &target.url) {
        return None;
    }
    let mut route = url.path().to_string();
    if let Some(query) = url.query() {
        route.push('?');
        route.push_str(query);
    }
    Some(route)
}

/// The exact URL a resumed launch should open.
///
/// The recorded route, not the workspace root: the root only authenticates and
/// then redirects to the last pod, so opening it means loading the app twice to
/// arrive where the route would have gone directly.
pub(crate) fn resume_entry_url(target: &ResumeTarget) -> String {
    format!(
        "{}{}",
        target.url.trim_end_matches('/'),
        if target.route == "/" {
            ""
        } else {
            &target.route
        }
    )
}

/// Is the workspace this target points at still the one that is serving?
///
/// Matching the generation is the whole point. A 2xx alone would also be
/// returned by an unrelated listener that took the port after a crash, or by a
/// stale process from a previous runtime; the generation is minted per user
/// start and handed to the backend as `LEMMA_RUNTIME_INSTANCE_ID`, so it only
/// matches when this is literally the stack the last session left behind.
pub(crate) fn resume_target_is_serving(target: &ResumeTarget) -> bool {
    let Ok(client) = reqwest::blocking::Client::builder()
        .timeout(RESUME_PROBE_TIMEOUT)
        .no_proxy()
        .build()
    else {
        return false;
    };
    // Both halves, because the window opens the frontend and the page it loads
    // then talks to the backend. Checking only the backend was enough to accept
    // a resume whose frontend had gone, which lands on a blank window.
    generation_matches(
        &client,
        &format!("{}/health/ready", target.api_url.trim_end_matches('/')),
        &target.generation,
    ) && generation_matches(
        &client,
        &format!("{}/runtime-config.js", target.url.trim_end_matches('/')),
        &target.generation,
    )
}

/// Does this endpoint answer 2xx and carry the generation we left it on?
///
/// The backend answers JSON with an `instance_id`; the frontend serves its
/// generation inside `runtime-config.js`. Both are the checks locald's own
/// health gate makes, so a substring match against the body is the same
/// contract rather than a looser one.
pub(crate) fn generation_matches(
    client: &reqwest::blocking::Client,
    url: &str,
    generation: &str,
) -> bool {
    let Ok(response) = client.get(url).send() else {
        return false;
    };
    if !response.status().is_success() {
        return false;
    }
    response.text().is_ok_and(|body| body.contains(generation))
}

/// The workspace origins `capabilities/workspace.json` already covers.
/// The origins the shipped capability already covers, read from the file.
///
/// Restated beside it, this list was a second copy of a rule that had already
/// drifted once in this very function -- and it silently gained a third failure
/// mode: the shipped entries carry a `:*` port pattern, while the origins
/// checked against them are concrete, so `contains` never matched a local
/// workspace and an override capability was minted for an origin that did not
/// need one.
pub(crate) fn shipped_workspace_origins() -> Vec<String> {
    serde_json::from_str::<Value>(SHIPPED_WORKSPACE_CAPABILITY)
        .expect("capabilities/workspace.json is valid JSON")["remote"]["urls"]
        .as_array()
        .expect("capabilities/workspace.json lists remote urls")
        .iter()
        .map(|url| {
            url.as_str()
                .expect("a shipped remote url is a string")
                .to_owned()
        })
        .collect()
}

/// The permissions the shipped workspace origins get.
///
/// Panics on a malformed capability, which is a build-time fact rather than a
/// runtime one: the file is compiled in, so a bad edit fails the first test that
/// touches this rather than reaching a user.
pub(crate) fn shipped_workspace_permissions() -> Vec<Value> {
    serde_json::from_str::<Value>(SHIPPED_WORKSPACE_CAPABILITY)
        .expect("capabilities/workspace.json is valid JSON")["permissions"]
        .as_array()
        .expect("capabilities/workspace.json grants an array of permissions")
        .clone()
}

/// A capability granting the overridden workspace origin the same commands the
/// shipped one gets, or `None` when nothing is overridden.
///
/// Only the origin varies. The permission list is taken from
/// `capabilities/workspace.json` itself, so a dev or self-hosted build can
/// never reach further into the shell than a packaged one — nor, as it did,
/// less far.
pub(crate) fn overridden_workspace_capability() -> Option<String> {
    let configured = ["LEMMA_DESKTOP_HOSTED_URL", "LEMMA_DESKTOP_LOCAL_URL"]
        .into_iter()
        .filter_map(|variable| std::env::var(variable).ok());
    workspace_capability_for(configured)
}

pub(crate) fn workspace_capability_for(configured: impl Iterator<Item = String>) -> Option<String> {
    let shipped = shipped_workspace_origins();
    let mut urls: Vec<String> = Vec::new();
    for value in configured {
        let Ok(url) = tauri::Url::parse(value.trim()) else {
            continue;
        };
        // Match the whole origin and any path under it, never a bare host that
        // a lookalike could also satisfy.
        let Some(host) = url.host_str() else { continue };
        let origin = match url.port() {
            Some(port) => format!("{}://{host}:{port}", url.scheme()),
            None => format!("{}://{host}", url.scheme()),
        };
        if shipped
            .iter()
            .any(|pattern| shipped_workspace_origin_covers(pattern, &origin))
        {
            continue;
        }
        if !urls.contains(&origin) {
            urls.push(origin);
        }
    }
    if urls.is_empty() {
        return None;
    }
    Some(
        json!({
            "identifier": "workspace-override-capability",
            "description": "Development or self-hosted workspace origin, granted the same commands as the shipped one.",
            "local": false,
            "webviews": ["main"],
            "remote": {"urls": urls},
            "permissions": shipped_workspace_permissions(),
        })
        .to_string(),
    )
}

/// Is the workspace a resumed window is showing still the one locald reports?
///
/// Read from the shell's own state rather than by probing, because by this point
/// `ensure_locald` has told us what the daemon actually has. An empty URL means
/// the reconcile has not published one yet, which is not evidence against the
/// window and must not pull a working workspace out from under the user.
pub(crate) fn resume_still_serving(app: &AppHandle, resumed_url: &str) -> bool {
    let shell: State<Shell> = app.state();
    let ui = shell.ui.lock().unwrap();
    ui.url.is_empty() || ui.url == resumed_url
}

/// Persist the route the main window is on, if it is on the workspace at all.
pub(crate) fn remember_workspace_route(app: &AppHandle) {
    let Some(target) = read_resume_target() else {
        return;
    };
    // Reading the route needs the live webview, so that part stays here. The
    // write does not: it syncs the config to disk twice, and on the close path
    // that ran before the window was hidden -- a visible hitch between clicking
    // the red button and the window going away, seconds of it on a busy disk.
    if let Some(route) = current_workspace_route(app, &target) {
        std::thread::spawn(move || write_resume_route(&route));
    }
}
