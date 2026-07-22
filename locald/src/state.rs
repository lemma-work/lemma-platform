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
            url: "http://app.lemma.localhost:3711".into(),
            api_url: "http://api.lemma.localhost:8711".into(),
            last_error: None,
            updated_at_ms: now_ms(),
        }
    }
}

impl StateSnapshot {
    pub fn load(path: &Path) -> Self {
        std::fs::read_to_string(path)
            .ok()
            .and_then(|raw| serde_json::from_str(&raw).ok())
            .unwrap_or_default()
    }

    pub fn observe(&mut self, event: &Value) {
        match event
            .get("event")
            .and_then(Value::as_str)
            .unwrap_or_default()
        {
            "state" | "status" => {
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
            }
            "phase" => {
                self.phase_key = string_field(event, "key", &self.phase_key);
                self.phase_label = string_field(event, "label", &self.phase_label);
                self.phase_progress = event
                    .get("progress")
                    .and_then(Value::as_u64)
                    .unwrap_or(self.phase_progress);
                self.detail = string_field(event, "detail", &self.detail);
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
                self.url = string_field(event, "url", &self.url);
                self.api_url = string_field(event, "api_url", &self.api_url);
                self.last_error = None;
            }
            "error" => {
                self.status = "error".into();
                self.ready = false;
                self.last_error = event
                    .get("message")
                    .and_then(Value::as_str)
                    .map(str::to_owned);
            }
            "bye" => self.running = false,
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
            "api_url": "http://api.lemma.localhost:9000"
        }));

        assert_eq!(state.phase_key, "backend");
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
}
