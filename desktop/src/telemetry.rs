//! Anonymous install health for Lemma Desktop.
//!
//! Desktop is the primary distribution channel, and without this the first
//! signal that a runtime install broke on a new OS release is a GitHub issue
//! three weeks later. So this reports whether the install worked — and nothing
//! else.
//!
//! It is deliberately *not* the product-analytics catalog with fields left out.
//! It is a separate contract, with a separate ingestion key, that structurally
//! cannot express a pod, an organization, a user, or the name of anything the
//! person made. In Local mode the whole backend runs on their machine and the
//! README promises exactly that; the only thing that leaves is whether the
//! software started.
//!
//! Off by every switch that should turn it off: `LEMMA_TELEMETRY=0`, the Local
//! settings toggle (persisted here), and — the default — no ingestion key
//! compiled in, which is the case for every locally built binary.

use std::fs;
#[cfg(unix)]
use std::io::Read;
use std::path::{Path, PathBuf};
use std::time::Duration;

use serde::{Deserialize, Serialize};

use crate::shell_paths::locald_root;
use crate::{require_control_window, Webview};
use serde_json::Value;

const KEY_ENV: &str = "LEMMA_TELEMETRY_KEY";
const HOST_ENV: &str = "LEMMA_TELEMETRY_HOST";
const DISABLE_ENV: &str = "LEMMA_TELEMETRY";
const DEFAULT_HOST: &str = "https://eu.i.posthog.com";

/// Two seconds, once, on a detached thread. Nothing about launching the app
/// may wait on an analytics endpoint.
const TIMEOUT: Duration = Duration::from_secs(2);

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct TelemetryState {
    pub install_id: Option<String>,
    /// `None` means "never asked or answered", which reads as enabled once a
    /// key exists. `Some(false)` is an explicit opt-out and is never overridden.
    pub enabled: Option<bool>,
}

/// What happened. A closed set: an event this enum cannot express is an event
/// Desktop does not send.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InstallEvent {
    Launched {
        cold: bool,
    },
    RuntimeInstallStarted,
    RuntimeInstallCompleted,
    /// `step` and `class` are bounded identifiers from the installer's own
    /// failure taxonomy — never an error string, which carries paths and
    /// hostnames.
    RuntimeInstallFailed {
        step: &'static str,
        class: &'static str,
    },
    RuntimeReady {
        cached: bool,
        duration_ms: u64,
    },
    ModeSelected {
        local: bool,
    },
    Quit {
        session_seconds: u64,
    },
}

impl InstallEvent {
    fn name(&self) -> &'static str {
        match self {
            Self::Launched { .. } => "desktop.launched",
            Self::RuntimeInstallStarted
            | Self::RuntimeInstallCompleted
            | Self::RuntimeInstallFailed { .. } => "desktop.runtime_install",
            Self::RuntimeReady { .. } => "desktop.runtime_ready",
            Self::ModeSelected { .. } => "desktop.mode_selected",
            Self::Quit { .. } => "desktop.quit",
        }
    }

    fn properties(&self) -> serde_json::Value {
        let mut props = serde_json::Map::new();
        props.insert("os".into(), std::env::consts::OS.into());
        props.insert("arch".into(), std::env::consts::ARCH.into());
        props.insert("app_version".into(), env!("CARGO_PKG_VERSION").into());
        match *self {
            Self::Launched { cold } => {
                props.insert("start".into(), if cold { "cold" } else { "warm" }.into());
            }
            Self::RuntimeInstallStarted => {
                props.insert("phase".into(), "started".into());
            }
            Self::RuntimeInstallCompleted => {
                props.insert("phase".into(), "completed".into());
            }
            Self::RuntimeInstallFailed { step, class } => {
                props.insert("phase".into(), "failed".into());
                props.insert("step".into(), step.into());
                props.insert("error_class".into(), class.into());
            }
            Self::RuntimeReady {
                cached,
                duration_ms,
            } => {
                props.insert(
                    "source".into(),
                    if cached { "cached" } else { "fresh" }.into(),
                );
                props.insert(
                    "duration_bucket".into(),
                    duration_bucket(duration_ms).into(),
                );
            }
            Self::ModeSelected { local } => {
                props.insert("mode".into(), if local { "local" } else { "hosted" }.into());
            }
            Self::Quit { session_seconds } => {
                props.insert(
                    "session_bucket".into(),
                    session_bucket(session_seconds).into(),
                );
            }
        }
        serde_json::Value::Object(props)
    }
}

fn duration_bucket(ms: u64) -> &'static str {
    match ms {
        0..=5_000 => "0-5s",
        5_001..=15_000 => "5-15s",
        15_001..=45_000 => "15-45s",
        45_001..=120_000 => "45-120s",
        _ => "120s+",
    }
}

fn session_bucket(seconds: u64) -> &'static str {
    match seconds {
        0..=60 => "0-1m",
        61..=600 => "1-10m",
        601..=3_600 => "10-60m",
        3_601..=14_400 => "1-4h",
        _ => "4h+",
    }
}

fn state_path(root: &Path) -> PathBuf {
    root.join("telemetry.json")
}

pub fn load_state(root: &Path) -> TelemetryState {
    fs::read_to_string(state_path(root))
        .ok()
        .and_then(|raw| serde_json::from_str(&raw).ok())
        .unwrap_or_default()
}

pub fn save_state(root: &Path, state: &TelemetryState) -> std::io::Result<()> {
    fs::create_dir_all(root)?;
    let encoded = serde_json::to_vec_pretty(state)
        .map_err(|err| std::io::Error::new(std::io::ErrorKind::InvalidData, err))?;
    // Atomically, and readable only by this user. It was a plain `write`,
    // which can leave a truncated file: this one holds the opt-out, and a
    // half-written opt-out reads as "never answered", which reads as consent.
    lemma_private_file::write_atomic(&state_path(root), &encoded)
}

/// Where the install id and the opt-out live.
///
/// The daemon's state directory rather than the app's own, so "start over"
/// takes them with it: somebody who erases this installation gets a new
/// install id, which is the behaviour the identity promises.
pub fn root() -> PathBuf {
    locald_root()
}

/// Record an event about this installation, if the person has not opted out
/// and this build has an ingestion key.
///
/// The convenience the call sites use, so none of them has to know where the
/// state lives.
pub fn note(event: InstallEvent) {
    record(&root(), event);
}

/// The Local settings toggle writes through here.
pub fn set_enabled(root: &Path, enabled: bool) -> std::io::Result<()> {
    let mut state = load_state(root);
    state.enabled = Some(enabled);
    save_state(root, &state)
}

/// A random per-installation id, minted once and kept.
///
/// Random on purpose — never derived from hostname, MAC or machine id, which
/// identify a person's computer rather than an installation of this app.
pub fn install_id(root: &Path) -> String {
    let mut state = load_state(root);
    if let Some(existing) = state.install_id.as_ref().filter(|id| !id.is_empty()) {
        return existing.clone();
    }
    let minted = random_hex();
    state.install_id = Some(minted.clone());
    let _ = save_state(root, &state);
    minted
}

fn random_hex() -> String {
    // `mut` only where something writes to it: the unix branch fills this from
    // /dev/urandom, and on Windows nothing does, where `-D warnings` rejects an
    // unused `mut`.
    #[cfg_attr(not(unix), allow(unused_mut))]
    let mut bytes = [0u8; 16];
    #[cfg(unix)]
    {
        if let Ok(mut file) = fs::File::open("/dev/urandom") {
            if file.read_exact(&mut bytes).is_ok() {
                return hex(&bytes);
            }
        }
    }
    // Fallback: still unlinked to any hardware identity.
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let pid = std::process::id() as u128;
    let mixed = nanos ^ (pid << 64) ^ (&bytes as *const _ as u128);
    hex(&mixed.to_le_bytes())
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// The ingestion key, baked in at build time or supplied at run time.
///
/// `option_env!` is what makes this work at all in a shipped app. The key was
/// read only from the process environment, and a `.app` launched from Finder
/// inherits a login environment that has never heard of it -- so telemetry
/// could not fire in *any* packaged build, which is precisely the situation
/// this module's own opening comment says it exists for: without it, the first
/// signal that a runtime install broke on a new macOS release is a GitHub issue
/// three weeks later.
///
/// The runtime variable still wins, so a developer can point a local build at a
/// throwaway project. Absent both, telemetry stays off, which remains the
/// default for every locally built binary.
fn ingestion_key() -> Option<String> {
    let candidate = std::env::var(KEY_ENV)
        .ok()
        .or_else(|| option_env!("LEMMA_TELEMETRY_KEY").map(str::to_owned))?;
    let candidate = candidate.trim().to_owned();
    (!candidate.is_empty()).then_some(candidate)
}

pub fn is_enabled(root: &Path) -> bool {
    let disabled = std::env::var(DISABLE_ENV)
        .map(|v| matches!(v.trim(), "0" | "false" | "off" | "no"))
        .unwrap_or(false);
    if disabled {
        return false;
    }
    if ingestion_key().is_none() {
        return false;
    }
    load_state(root).enabled != Some(false)
}

/// Fire and forget. Returns immediately; delivery happens on a detached thread.
pub fn record(root: &Path, event: InstallEvent) {
    if !is_enabled(root) {
        return;
    }
    let Some(key) = ingestion_key() else {
        return;
    };
    // Where this goes is not a runtime decision in a shipped build.
    //
    // The key used to be read from the environment too, so a build that had one
    // was a build somebody had deliberately configured. It is baked in now, so
    // a released app carries the ingestion key -- and an unguarded destination
    // turns that into an exfiltration primitive: set `LEMMA_TELEMETRY_HOST` in
    // the app's environment and the key and install id are posted, in the
    // clear, wherever you like. Redirecting it stays available for development,
    // which is the only place it was ever for.
    let host = if cfg!(debug_assertions) {
        std::env::var(HOST_ENV).unwrap_or_else(|_| DEFAULT_HOST.to_string())
    } else {
        DEFAULT_HOST.to_string()
    };
    let payload = serde_json::json!({
        "api_key": key,
        "batch": [{
            "event": event.name(),
            "distinct_id": install_id(root),
            "properties": event.properties(),
        }],
    });
    std::thread::spawn(move || {
        let client = match reqwest::blocking::Client::builder()
            .timeout(TIMEOUT)
            .build()
        {
            Ok(client) => client,
            Err(_) => return,
        };
        let _ = client
            .post(format!("{}/batch/", host.trim_end_matches('/')))
            .json(&payload)
            .send();
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn install_id_is_stable_across_calls() {
        let dir = std::env::temp_dir().join(format!("lemma-tel-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        let first = install_id(&dir);
        let second = install_id(&dir);
        assert_eq!(first, second);
        assert_eq!(first.len(), 32);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn disabled_without_a_key_even_when_opted_in() {
        let dir = std::env::temp_dir().join(format!("lemma-tel-key-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        set_enabled(&dir, true).unwrap();
        // No LEMMA_TELEMETRY_KEY in the test environment.
        assert!(!is_enabled(&dir));
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn an_explicit_opt_out_is_recorded() {
        let dir = std::env::temp_dir().join(format!("lemma-tel-off-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        set_enabled(&dir, false).unwrap();
        assert_eq!(load_state(&dir).enabled, Some(false));
        assert!(!is_enabled(&dir));
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn events_never_carry_free_text() {
        let event = InstallEvent::RuntimeInstallFailed {
            step: "extract",
            class: "DigestMismatch",
        };
        let rendered = event.properties().to_string();
        assert!(rendered.contains("DigestMismatch"));
        // No path, no host, no user-supplied string can appear: the variant
        // only accepts &'static str chosen at the call site.
        assert!(!rendered.contains('/'));
    }
}

/// What Local settings shows for the anonymous install-health switch.
///
/// `available` is whether this build has an ingestion key at all. Without one
/// nothing is ever sent, so a switch would be a control over nothing -- the
/// page hides the whole panel rather than offering a lie.
#[tauri::command(async)]
pub(crate) fn telemetry_status(window: Webview) -> Result<Value, String> {
    require_control_window(&window)?;
    let root = root();
    Ok(serde_json::json!({
        "available": ingestion_key().is_some(),
        "enabled": is_enabled(&root),
        "host": DEFAULT_HOST,
        "install_id": load_state(&root).install_id,
    }))
}

/// The switch itself.
///
/// An explicit `false` is never overridden by anything: not by an upgrade, not
/// by a new ingestion key. That is what makes it an opt-out rather than a
/// preference.
#[tauri::command(async)]
pub(crate) fn set_telemetry_enabled(window: Webview, enabled: bool) -> Result<(), String> {
    require_control_window(&window)?;
    set_enabled(&root(), enabled)
        .map_err(|error| format!("could not save your anonymous install-health choice: {error}"))
}

#[cfg(test)]
mod wiring_tests {

    /// Every event this module can express is one the app actually sends.
    ///
    /// The module was written, reviewed and shipped with no caller at all:
    /// `record` was never invoked from anywhere, so the first signal that a
    /// runtime install broke on a new macOS release stayed a GitHub issue
    /// three weeks later -- which is the exact failure the opening comment
    /// says this exists to prevent. A variant nobody constructs is that bug
    /// coming back one event at a time.
    #[test]
    fn every_event_is_sent_from_somewhere() {
        let source = crate::tests::shell_source();
        let mut unsent = Vec::new();
        for variant in [
            "Launched",
            "RuntimeInstallStarted",
            "RuntimeInstallCompleted",
            "RuntimeInstallFailed",
            "RuntimeReady",
            "ModeSelected",
            "Quit",
        ] {
            if !source.contains(&format!("InstallEvent::{variant}")) {
                unsent.push(variant);
            }
        }
        assert!(
            unsent.is_empty(),
            "these events exist and nothing sends them: {unsent:?}",
        );
    }

    /// The switch is offered to Local settings and to nothing else.
    #[test]
    fn the_switch_is_not_reachable_from_the_workspace() {
        for command in ["allow-telemetry-status", "allow-set-telemetry-enabled"] {
            assert!(
                crate::tests::granted("control")
                    .iter()
                    .any(|p| p == command),
                "{command} has to be granted to Local settings"
            );
            assert!(
                !crate::tests::granted("workspace")
                    .iter()
                    .any(|p| p == command),
                "{command} must not be reachable from a remote origin"
            );
            assert!(
                !crate::tests::granted("main").iter().any(|p| p == command),
                "{command} must not be reachable from the splash"
            );
        }
    }
}

#[cfg(test)]
mod documentation_tests {
    /// The switch, the destination and the events are documented where a
    /// person installing Lemma will look.
    ///
    /// The register's DOC-1 is exactly this gap: the module named a PostHog
    /// endpoint that appeared in no document a user reads, and claimed a Local
    /// settings toggle that did not exist.
    #[test]
    fn what_is_sent_and_how_to_stop_it_is_written_down() {
        let installation = include_str!("../../docs/installation.md").replace("\r\n", "\n");
        assert!(
            installation.contains("Anonymous install health"),
            "the switch is not documented where somebody installing Lemma reads",
        );
        for required in [
            super::DEFAULT_HOST,
            "LEMMA_TELEMETRY=0",
            "desktop.runtime_install",
        ] {
            assert!(
                installation.contains(required),
                "{required:?} is not in the installation guide",
            );
        }
    }
}
