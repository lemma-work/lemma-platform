//! Pairing this computer with a workspace, and undoing it.

use super::*;

/// Whether the host holds an identity for at least one workspace.
pub(crate) fn host_is_paired(config_path: &Path) -> bool {
    let Ok(raw) = std::fs::read_to_string(config_path) else {
        return false;
    };
    let Ok(config) = serde_json::from_str::<Value>(&raw) else {
        return false;
    };
    config
        .get("targets")
        .and_then(Value::as_array)
        .is_some_and(|targets| {
            targets.iter().any(|target| {
                target
                    .get("enabled")
                    .and_then(Value::as_bool)
                    .unwrap_or(true)
            })
        })
}

impl AgentHostSupervisor {
    /// Consume a one-time pairing code, then start serving that workspace.
    ///
    /// The desktop app mints the code with the user's own session and hands it
    /// straight here, so pairing never requires a terminal.
    pub fn pair(&self, url: &str, pairing_code: &str, name: &str) -> io::Result<()> {
        if url.trim().is_empty() || pairing_code.trim().is_empty() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "pairing needs a workspace URL and a pairing code",
            ));
        }
        let mut arguments = vec![
            "connect",
            "--url",
            url.trim(),
            "--pairing-code",
            pairing_code.trim(),
        ];
        let name = name.trim();
        if !name.is_empty() {
            arguments.extend_from_slice(&["--name", name]);
        }
        // Plain HTTP is refused off loopback by the host itself, so this only
        // widens what a local development backend already allows.
        if is_loopback_http(url.trim()) {
            arguments.push("--allow-insecure-http");
        }
        self.run_cli(&arguments)?;
        self.invalidate_details();
        self.start()
    }

    /// Revoke this computer's identity remotely, then stop serving.
    pub fn unpair(&self, target_id: Option<&str>) -> io::Result<()> {
        let mut arguments = vec!["disconnect"];
        if let Some(target) = target_id.map(str::trim).filter(|value| !value.is_empty()) {
            arguments.extend_from_slice(&["--target", target]);
        }
        let result = self.run_cli(&arguments);
        self.invalidate_details();
        result?;
        self.stop()
    }

    /// Re-probe the installed coding agents and republish them now, rather than
    /// on the host's own 15-minute cycle.
    pub fn refresh(&self) -> io::Result<()> {
        self.run_cli(&["refresh"])?;
        self.invalidate_details();
        Ok(())
    }
}
