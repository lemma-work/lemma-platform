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
use std::io::Read;
use std::path::{Path, PathBuf};
use std::time::Duration;

use serde::{Deserialize, Serialize};

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
    Launched { cold: bool },
    RuntimeInstallStarted,
    RuntimeInstallCompleted,
    /// `step` and `class` are bounded identifiers from the installer's own
    /// failure taxonomy — never an error string, which carries paths and
    /// hostnames.
    RuntimeInstallFailed {
        step: &'static str,
        class: &'static str,
    },
    RuntimeReady { cached: bool, duration_ms: u64 },
    ModeSelected { local: bool },
    Quit { session_seconds: u64 },
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
                props.insert("source".into(), if cached { "cached" } else { "fresh" }.into());
                props.insert("duration_bucket".into(), duration_bucket(duration_ms).into());
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
    let encoded = serde_json::to_string_pretty(state)
        .map_err(|err| std::io::Error::new(std::io::ErrorKind::InvalidData, err))?;
    fs::write(state_path(root), encoded)
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

pub fn is_enabled(root: &Path) -> bool {
    let disabled = std::env::var(DISABLE_ENV)
        .map(|v| matches!(v.trim(), "0" | "false" | "off" | "no"))
        .unwrap_or(false);
    if disabled {
        return false;
    }
    if std::env::var(KEY_ENV).map(|k| k.trim().is_empty()).unwrap_or(true) {
        return false;
    }
    load_state(root).enabled != Some(false)
}

/// Fire and forget. Returns immediately; delivery happens on a detached thread.
pub fn record(root: &Path, event: InstallEvent) {
    if !is_enabled(root) {
        return;
    }
    let key = match std::env::var(KEY_ENV) {
        Ok(key) if !key.trim().is_empty() => key,
        _ => return,
    };
    let host = std::env::var(HOST_ENV).unwrap_or_else(|_| DEFAULT_HOST.to_string());
    let payload = serde_json::json!({
        "api_key": key,
        "batch": [{
            "event": event.name(),
            "distinct_id": install_id(root),
            "properties": event.properties(),
        }],
    });
    std::thread::spawn(move || {
        let client = match reqwest::blocking::Client::builder().timeout(TIMEOUT).build() {
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
