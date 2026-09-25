//! Keeping people signed in across the move off `127.0.0.1.sslip.io`.
//!
//! Earlier builds served the local workspace on the public loopback wildcard
//! `app.127.0.0.1.sslip.io`, with the session cookies scoped to
//! `.127.0.0.1.sslip.io`. This build serves `app.lemma.localhost`, and a
//! browser never sends one domain's cookies to another -- so without this every
//! upgraded install would open on the sign-in page.
//!
//! Before the main window is sent to the workspace, the shell copies every
//! cookie it holds under the old domain onto the matching `lemma.localhost`
//! name (a `Domain` cookie stays a `Domain` cookie, a host-only cookie stays
//! host-only on the matching host), then deletes the old one. A cookie that
//! already exists under the new name is left alone and the old one is still
//! deleted: whatever the new domain holds is newer. That makes it idempotent,
//! and a marker file makes it run once.
//!
//! What does not move: `localStorage` and IndexedDB are keyed by origin and
//! WebKit offers no way to copy them between origins. The workspace keeps
//! only preferences there (open tabs, the last pod, a collapsed sidebar), so
//! the cost is those resetting once.

use super::*;

use tauri::webview::Cookie;

/// The domain earlier builds served under.
pub(crate) const RETIRED_BASE: &str = "127.0.0.1.sslip.io";
/// The domain this build serves under.
pub(crate) const CURRENT_BASE: &str = "lemma.localhost";
const MARKER: &str = "cookie-domain-migrated";

/// The new domain for a cookie held under the retired one, or `None` for any
/// other cookie. `.127.0.0.1.sslip.io` -> `.lemma.localhost`,
/// `app.127.0.0.1.sslip.io` -> `app.lemma.localhost`.
pub(crate) fn migrated_domain(domain: &str) -> Option<String> {
    let lowered = domain.to_ascii_lowercase();
    if lowered == RETIRED_BASE || lowered == format!(".{RETIRED_BASE}") {
        return Some(format!(".{CURRENT_BASE}"));
    }
    let prefix = lowered.strip_suffix(&format!(".{RETIRED_BASE}"))?;
    // `app.127.0.0.1.sslip.io`, not `app.10.0.0.7.sslip.io`: the suffix match
    // already requires the loopback address as whole labels.
    if prefix.is_empty() || prefix.starts_with('.') {
        return None;
    }
    Some(format!("{prefix}.{CURRENT_BASE}"))
}

/// What one cookie becomes.
#[derive(Debug, PartialEq, Eq)]
pub(crate) struct Move {
    pub(crate) name: String,
    pub(crate) from_domain: String,
    pub(crate) to_domain: String,
    pub(crate) path: String,
    /// False when the new domain already holds this cookie; the old one is
    /// deleted either way.
    pub(crate) copy: bool,
}

/// Decide the moves for a cookie jar, as `(name, domain, path)` triples.
pub(crate) fn plan(jar: &[(String, String, String)]) -> Vec<Move> {
    jar.iter()
        .filter_map(|(name, domain, path)| {
            let to_domain = migrated_domain(domain)?;
            let exists = jar.iter().any(|(other_name, other_domain, other_path)| {
                other_name == name
                    && other_path == path
                    && other_domain.eq_ignore_ascii_case(&to_domain)
            });
            Some(Move {
                name: name.clone(),
                from_domain: domain.clone(),
                to_domain,
                path: path.clone(),
                copy: !exists,
            })
        })
        .collect()
}

fn marker_path() -> PathBuf {
    app_support_dir().join(MARKER)
}

/// Move the main webview's session cookies onto `lemma.localhost`, once.
///
/// Called from a worker in local mode before the shell asks locald for the
/// workspace, so the `ready` navigation that follows lands signed in. Cookie
/// calls are proxied to the main thread by Tauri; never call this there.
pub(crate) fn migrate_session_cookies(app: &AppHandle) {
    if marker_path().exists() {
        return;
    }
    let Some(webview) = app.get_webview("main") else {
        return;
    };
    let cookies = match webview.cookies() {
        Ok(cookies) => cookies,
        Err(error) => {
            append_install_log(&format!(
                "cookie migration: could not read cookies: {error}"
            ));
            return;
        }
    };
    let jar: Vec<(String, String, String)> = cookies
        .iter()
        .map(|cookie| {
            (
                cookie.name().to_owned(),
                cookie.domain().unwrap_or_default().to_owned(),
                cookie.path().unwrap_or("/").to_owned(),
            )
        })
        .collect();
    let moves = plan(&jar);
    let mut copied = 0usize;
    let mut failed = 0usize;
    for step in &moves {
        let Some(original) = cookies.iter().find(|cookie| {
            cookie.name() == step.name
                && cookie.domain().unwrap_or_default() == step.from_domain
                && cookie.path().unwrap_or("/") == step.path
        }) else {
            continue;
        };
        if step.copy {
            let mut moved = Cookie::new(step.name.clone(), original.value().to_owned());
            moved.set_domain(step.to_domain.clone());
            moved.set_path(step.path.clone());
            moved.set_http_only(original.http_only().unwrap_or(false));
            moved.set_secure(false);
            if let Some(same_site) = original.same_site() {
                moved.set_same_site(same_site);
            }
            if let Some(expires) = original.expires() {
                moved.set_expires(expires);
            }
            if webview.set_cookie(moved).is_err() {
                failed += 1;
                continue;
            }
            copied += 1;
        }
        let _ = webview.delete_cookie(original.clone());
    }
    if failed == 0 {
        let _ = std::fs::write(marker_path(), b"lemma.localhost\n");
    }
    if !moves.is_empty() {
        append_install_log(&format!(
            "cookie migration: moved {copied} of {} cookie(s) from {RETIRED_BASE} to {CURRENT_BASE}{}",
            moves.len(),
            if failed > 0 {
                format!("; {failed} failed and will be retried next launch")
            } else {
                String::new()
            }
        ));
    }
}
