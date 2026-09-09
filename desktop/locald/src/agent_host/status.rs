//! What the shell is told about the Agent Host.

use super::*;

/// How long a merged status may be reused. A UI polls this while its page is
/// open, and every miss forks the sidecar to read its own SQLite journal.
pub(crate) const DETAILS_CACHE: Duration = Duration::from_secs(2);

impl AgentHostSupervisor {
    /// Whether the process is alive, and nothing about what it is doing.
    pub fn status(&self) -> Value {
        let mut state = self.state.lock().expect("Agent Host state lock poisoned");
        let running = child_running(&mut state);
        json!({
            "available": self.executable.is_some(),
            "running": running,
            "desired_running": state.desired_running,
            "pid": state.child.as_ref().map(Child::id),
            "executable": self.executable,
            "data_dir": self.data_dir,
            "log": self.log_path,
            "restart_count": state.restart_count,
            "restart_circuit_open": state.circuit_open,
            "started_at_ms": state.started_at_ms,
            "uptime_seconds": state.started_at.map(|started| started.elapsed().as_secs()),
            "last_exit_code": state.last_exit_code,
            "last_error": state.last_error,
        })
    }

    /// Process state plus what the host itself knows: which workspaces it is
    /// paired to, whether it is actually reaching them, and what work it holds.
    ///
    /// "Running" alone is a poor answer to "is this working?" - a paired host
    /// with an expired secret and an unpaired host that has nothing to do are
    /// both live processes. Only the host's own journal can tell them apart,
    /// and it answers over its CLI rather than over locald's socket.
    pub fn detailed_status(&self) -> Value {
        let mut status = self.status();
        let targets = self.cached_targets();
        let paired = targets.as_ref().is_some_and(|items| !items.is_empty());
        status["paired"] = json!(paired);
        status["targets"] = json!(targets.unwrap_or_default());
        status
    }

    pub(crate) fn cached_targets(&self) -> Option<Vec<Value>> {
        let mut cache = self
            .details
            .lock()
            .expect("Agent Host details lock poisoned");
        if let Some((read_at, value)) = cache.as_ref() {
            if read_at.elapsed() < DETAILS_CACHE {
                return value.as_array().cloned();
            }
        }
        let report = self.run_cli(&["status", "--json"]).ok()?;
        let parsed: Value = serde_json::from_str(&report).ok()?;
        let targets: Vec<Value> = parsed
            .get("targets")
            .and_then(Value::as_array)
            .map(|items| items.iter().map(summarize_target).collect())
            .unwrap_or_default();
        let value = Value::Array(targets.clone());
        *cache = Some((Instant::now(), value));
        Some(targets)
    }

    pub(crate) fn invalidate_details(&self) {
        *self
            .details
            .lock()
            .expect("Agent Host details lock poisoned") = None;
    }
}
