use std::io;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::PROTOCOL_VERSION;

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct StateSnapshot {
    pub revision: u64,
    pub status: String,
    pub ready: bool,
    pub running: bool,
    pub provider: Option<String>,
    pub phase_key: String,
    pub phase_label: String,
    pub phase_progress: u64,
    pub detail: String,
    pub url: String,
    pub api_url: String,
    pub last_error: Option<String>,
    pub updated_at_ms: u128,
}

impl Default for StateSnapshot {
    fn default() -> Self {
        Self {
            revision: 0,
            status: "stopped".into(),
            ready: false,
            running: false,
            provider: None,
            phase_key: "boot".into(),
            phase_label: "Booting local services".into(),
            phase_progress: 4,
            detail: String::new(),
            // There is deliberately no legacy localhost fallback. The daemon
            // fills these from its reserved application ports after loading
            // the authenticated host pack.
            url: String::new(),
            api_url: String::new(),
            last_error: None,
            updated_at_ms: now_ms(),
        }
    }
}

impl StateSnapshot {
    pub fn load(path: &Path) -> Self {
        let mut state: Self = std::fs::read_to_string(path)
            .ok()
            .and_then(|raw| serde_json::from_str(&raw).ok())
            .unwrap_or_default();
        state.api_url = migrate_legacy_local_api_url(&state.api_url);
        if state.url == "http://app.lemma.localhost:3711"
            && state.api_url == "http://app.lemma.localhost:8711"
        {
            // Do not carry the old shared development ports into the managed
            // Desktop lifecycle. A loaded host pack replaces these with its
            // reserved dynamic pair before the state reaches a client.
            state.url.clear();
            state.api_url.clear();
        }
        // Older releases persisted the last progress phase independently from
        // the supervised lifecycle. A clean shutdown could therefore leave
        // `ready=false` alongside `phase=ready` and `progress=100`, causing a
        // newly opened Desktop shell to present a terminal state that was no
        // longer true. Normalize that legacy state before it reaches a client.
        state.normalize_loaded_state();
        state
    }

    pub fn observe(&mut self, event: &Value) {
        let kind = event
            .get("event")
            .and_then(Value::as_str)
            .unwrap_or_default();
        match kind {
            "state" | "status" => {
                let preserve_inflight_phase = kind == "status"
                    && self.status == "starting"
                    && event.get("status").and_then(Value::as_str) == Some("stopped");
                if let Some(status) = event.get("status").and_then(Value::as_str) {
                    self.status = status.into();
                }
                self.ready = event
                    .get("ready")
                    .and_then(Value::as_bool)
                    .unwrap_or(self.ready);
                self.running = event
                    .get("running")
                    .and_then(Value::as_bool)
                    .unwrap_or(self.running);
                if preserve_inflight_phase {
                    // The host-process supervisor remains stopped while the
                    // managed VM and its private services are starting. Its
                    // periodic status must not replace the active VM/database
                    // phase with a false user-facing stopped state.
                    self.status = "starting".into();
                } else if self.status == "stopped" && !self.ready && !self.running {
                    // A `state: stopped` event is an explicit successful stop
                    // and clears an older failure. A periodic supervised
                    // `status: stopped` must not erase a startup error before
                    // the user has had a chance to read or retry it.
                    if kind == "state" || self.last_error.is_none() {
                        self.set_stopped();
                    } else {
                        self.set_error_phase();
                    }
                } else if self.status == "error" && !self.ready {
                    self.set_error_phase();
                }
            }
            "phase" => {
                self.phase_key = string_field(event, "key", &self.phase_key);
                self.phase_label = string_field(event, "label", &self.phase_label);
                self.phase_progress = event
                    .get("progress")
                    .and_then(Value::as_u64)
                    .unwrap_or(self.phase_progress);
                self.detail = string_field(event, "detail", &self.detail);
                if self.phase_key != "ready" {
                    self.status = "starting".into();
                    self.ready = false;
                    self.last_error = None;
                }
            }
            "provider" => {
                self.provider = event
                    .get("provider")
                    .and_then(Value::as_str)
                    .map(str::to_owned);
            }
            "ready" => {
                self.status = "running".into();
                self.ready = true;
                self.running = true;
                self.phase_key = "ready".into();
                self.phase_label = "Lemma is ready".into();
                self.phase_progress = 100;
                self.detail.clear();
                self.url = string_field(event, "url", &self.url);
                self.api_url = string_field(event, "api_url", &self.api_url);
                self.last_error = None;
            }
            "error" if event.get("scope").and_then(Value::as_str) == Some("sharing") => {
                return;
            }
            "error" => {
                self.status = "error".into();
                self.ready = false;
                self.running = false;
                self.last_error = event
                    .get("message")
                    .and_then(Value::as_str)
                    .map(str::to_owned);
                self.set_error_phase();
            }
            "bye" => {
                self.ready = false;
                self.running = false;
                self.set_stopped();
            }
            _ => return,
        }
        self.revision = self.revision.saturating_add(1);
        self.updated_at_ms = now_ms();
    }

    pub fn event(&self, id: Option<&Value>) -> Value {
        let mut event = json!({
            "v": PROTOCOL_VERSION,
            "event": "status",
            "status": self.status,
            "ready": self.ready,
            "running": self.running,
            "provider": self.provider,
            "url": self.url,
            "api_url": self.api_url,
            "phase": {
                "key": self.phase_key,
                "label": self.phase_label,
                "progress": self.phase_progress,
                "detail": self.detail,
            },
            "last_error": self.last_error,
            "revision": self.revision,
            "updated_at_ms": self.updated_at_ms,
        });
        if let Some(id) = id {
            event["id"] = id.clone();
        }
        event
    }

    pub fn persist(&self, path: &Path) -> io::Result<()> {
        let temporary = path.with_extension("json.next");
        let bytes = serde_json::to_vec_pretty(self).map_err(io::Error::other)?;
        std::fs::write(&temporary, bytes)?;
        std::fs::rename(temporary, path)
    }

    fn normalize_loaded_state(&mut self) {
        if self.ready {
            self.status = "running".into();
            self.running = true;
            self.phase_key = "ready".into();
            self.phase_label = "Lemma is ready".into();
            self.phase_progress = 100;
            self.detail.clear();
            self.last_error = None;
        } else if self.status == "error" || self.last_error.is_some() {
            self.status = "error".into();
            self.set_error_phase();
        } else if !self.running {
            self.set_stopped();
        } else if self.phase_key == "ready" || self.phase_progress >= 100 {
            self.status = "starting".into();
            self.phase_key = "boot".into();
            self.phase_label = "Checking local services".into();
            self.phase_progress = 4;
            self.detail.clear();
        }
    }

    fn set_stopped(&mut self) {
        self.status = "stopped".into();
        self.ready = false;
        self.running = false;
        self.phase_key = "stopped".into();
        self.phase_label = "Local services are stopped".into();
        self.phase_progress = 0;
        self.detail.clear();
        self.last_error = None;
    }

    fn set_error_phase(&mut self) {
        self.status = "error".into();
        self.ready = false;
        self.phase_key = "error".into();
        self.phase_label = "Local services need attention".into();
        self.phase_progress = 0;
        self.detail = self.last_error.clone().unwrap_or_default();
    }
}

fn migrate_legacy_local_api_url(value: &str) -> String {
    const LEGACY_PREFIX: &str = "http://api.lemma.localhost";
    let Some(suffix) = value.strip_prefix(LEGACY_PREFIX) else {
        return value.to_owned();
    };
    if !suffix.is_empty() && !suffix.starts_with(':') && !suffix.starts_with('/') {
        return value.to_owned();
    }
    format!("http://app.lemma.localhost{suffix}")
}

fn string_field(event: &Value, name: &str, fallback: &str) -> String {
    event
        .get(name)
        .and_then(Value::as_str)
        .unwrap_or(fallback)
        .to_owned()
}

fn now_ms() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn observes_component_lifecycle_events() {
        let mut state = StateSnapshot::default();
        state.observe(&json!({
            "event": "phase", "key": "backend", "label": "Preparing backend",
            "progress": 82, "detail": "starting backend"
        }));
        state.observe(&json!({"event": "provider", "provider": "docker"}));
        state.observe(&json!({
            "event": "ready", "url": "http://app.lemma.localhost:4000",
            "api_url": "http://app.lemma.localhost:9000"
        }));

        assert_eq!(state.phase_key, "ready");
        assert_eq!(state.provider.as_deref(), Some("docker"));
        assert!(state.ready);
        assert_eq!(state.url, "http://app.lemma.localhost:4000");
        assert!(state.revision >= 3);
    }

    #[test]
    fn errors_clear_readiness_but_remain_diagnosable() {
        let mut state = StateSnapshot {
            ready: true,
            ..Default::default()
        };
        state.observe(&json!({"event": "error", "message": "database unavailable"}));
        assert!(!state.ready);
        assert_eq!(state.status, "error");
        assert_eq!(state.last_error.as_deref(), Some("database unavailable"));
    }

    #[test]
    fn sharing_errors_do_not_mark_a_healthy_application_unready() {
        let mut state = StateSnapshot {
            status: "running".into(),
            ready: true,
            running: true,
            ..Default::default()
        };
        state.observe(&json!({
            "event": "error",
            "scope": "sharing",
            "message": "tunnel could not start"
        }));
        assert!(state.ready);
        assert!(state.running);
        assert_eq!(state.status, "running");
    }

    #[test]
    fn persisted_api_origin_migrates_to_the_webkit_safe_cookie_host() {
        assert_eq!(
            migrate_legacy_local_api_url("http://api.lemma.localhost:8711"),
            "http://app.lemma.localhost:8711"
        );
        assert_eq!(
            migrate_legacy_local_api_url("https://api.example.com"),
            "https://api.example.com"
        );
        assert_eq!(
            migrate_legacy_local_api_url("http://api.lemma.localhost.evil:8711"),
            "http://api.lemma.localhost.evil:8711"
        );
    }

    #[test]
    fn legacy_shared_development_ports_are_not_loaded_as_desktop_identity() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("state.json");
        std::fs::write(
            &path,
            serde_json::to_vec(&StateSnapshot {
                url: "http://app.lemma.localhost:3711".into(),
                api_url: "http://app.lemma.localhost:8711".into(),
                ..Default::default()
            })
            .unwrap(),
        )
        .unwrap();

        let state = StateSnapshot::load(&path);
        assert!(state.url.is_empty());
        assert!(state.api_url.is_empty());
    }

    #[test]
    fn supervised_status_updates_persisted_readiness() {
        let mut state = StateSnapshot {
            ready: true,
            running: true,
            status: "running".into(),
            ..Default::default()
        };

        state.observe(&json!({
            "event": "status", "mode": "host-packs", "status": "error",
            "ready": false, "running": false,
        }));

        assert_eq!(state.status, "error");
        assert!(!state.ready);
        assert!(!state.running);
    }

    #[test]
    fn stopped_status_cannot_reuse_a_stale_ready_phase() {
        let mut state = StateSnapshot {
            status: "running".into(),
            ready: true,
            running: true,
            phase_key: "ready".into(),
            phase_label: "Lemma is ready".into(),
            phase_progress: 100,
            ..Default::default()
        };

        state.observe(&json!({
            "event": "status", "status": "stopped",
            "ready": false, "running": false,
        }));

        assert_eq!(state.status, "stopped");
        assert_eq!(state.phase_key, "stopped");
        assert_eq!(state.phase_progress, 0);
        assert!(!state.ready);
    }

    #[test]
    fn loading_legacy_stopped_ready_state_normalizes_it() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("state.json");
        std::fs::write(
            &path,
            serde_json::to_vec(&StateSnapshot {
                status: "stopped".into(),
                ready: false,
                running: false,
                phase_key: "ready".into(),
                phase_label: "Lemma is ready".into(),
                phase_progress: 100,
                ..Default::default()
            })
            .unwrap(),
        )
        .unwrap();

        let state = StateSnapshot::load(&path);
        assert_eq!(state.phase_key, "stopped");
        assert_eq!(state.phase_progress, 0);
    }

    #[test]
    fn periodic_stopped_status_does_not_erase_a_startup_error() {
        let mut state = StateSnapshot::default();
        state.observe(&json!({"event": "error", "message": "database unavailable"}));
        state.observe(&json!({
            "event": "status", "status": "stopped",
            "ready": false, "running": false,
        }));

        assert_eq!(state.status, "error");
        assert_eq!(state.phase_key, "error");
        assert_eq!(state.last_error.as_deref(), Some("database unavailable"));
    }

    #[test]
    fn periodic_host_status_does_not_replace_an_active_vm_phase() {
        let mut state = StateSnapshot::default();
        state.observe(&json!({
            "event": "phase",
            "key": "vm",
            "label": "Starting private runtime",
            "progress": 32,
        }));
        state.observe(&json!({
            "event": "status", "status": "stopped",
            "ready": false, "running": false,
        }));

        assert_eq!(state.status, "starting");
        assert_eq!(state.phase_key, "vm");
        assert_eq!(state.phase_progress, 32);
    }
}
