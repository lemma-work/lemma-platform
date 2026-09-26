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
    /// straight here, so pairing never requires a terminal. The code goes to
    /// the host on stdin, not its argument list, where any process on this
    /// computer could read it while the exchange runs. `reenable` is the
    /// person asking, in the app, to turn back on a computer they removed.
    pub fn pair(
        &self,
        url: &str,
        pairing_code: &str,
        name: &str,
        reenable: bool,
    ) -> io::Result<()> {
        if url.trim().is_empty() || pairing_code.trim().is_empty() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "pairing needs a workspace URL and a pairing code",
            ));
        }
        let mut arguments = vec!["connect", "--url", url.trim(), "--pairing-code-stdin"];
        if reenable {
            arguments.push("--reenable");
        }
        let name = name.trim();
        if !name.is_empty() {
            arguments.extend_from_slice(&["--name", name]);
        }
        // Plain HTTP is refused off loopback by the host itself, so this only
        // widens what a local development backend already allows.
        if is_loopback_http(url.trim()) {
            arguments.push("--allow-insecure-http");
        }
        self.run_cli_with_input(&arguments, Some(pairing_code.trim()))?;
        self.invalidate_details();
        self.start()
    }

    /// Tell the host who is signed in to the app on `url` -- `None` when
    /// nobody is -- so pairings of anybody else take no new work meanwhile.
    pub fn session(&self, url: &str, user_id: Option<&str>) -> io::Result<()> {
        if url.trim().is_empty() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "the session needs the workspace URL",
            ));
        }
        let mut arguments = vec!["session", "--url", url.trim()];
        if let Some(user) = user_id.map(str::trim).filter(|user| !user.is_empty()) {
            arguments.extend_from_slice(&["--user", user]);
        }
        self.run_cli(&arguments)?;
        self.invalidate_details();
        Ok(())
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

    /// Turn running the owner's Lemma agents' commands on this computer on or
    /// off. The host re-reads its configuration every few seconds and tells
    /// Lemma on its next `control` frame, so there is nothing to restart.
    pub fn set_host_execution(&self, enabled: bool) -> io::Result<()> {
        self.run_cli(&["host-execution", if enabled { "enable" } else { "disable" }])?;
        self.invalidate_details();
        Ok(())
    }

    /// Whether one coding agent loads the person's own skills and settings as
    /// well as Lemma's. Each run reads it as it starts, so the next turn
    /// follows it without a restart.
    pub fn set_own_settings(&self, harness: &str, enabled: bool) -> io::Result<()> {
        if harness.is_empty() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "name the coding agent this setting is for",
            ));
        }
        self.run_cli(&[
            "own-settings",
            if enabled { "enable" } else { "disable" },
            harness,
        ])?;
        self.invalidate_details();
        Ok(())
    }
}

/// The coding agents whose person chose their own skills and settings,
/// straight from the host's config. Absent means none, the host's default.
pub(crate) fn own_settings(config_path: &Path) -> Vec<String> {
    std::fs::read_to_string(config_path)
        .ok()
        .and_then(|raw| serde_json::from_str::<Value>(&raw).ok())
        .and_then(|config| config.get("own_settings").cloned())
        .and_then(|value| serde_json::from_value::<Vec<String>>(value).ok())
        .unwrap_or_default()
}

/// The owner's host-execution setting, straight from the host's config.
/// Absent means off, which is the host's own default.
///
/// The switch is the local pairing's -- the Lemma installed on this computer,
/// paired over loopback HTTP -- and is on while that pairing's person is the
/// one signed in (`session_paused` is off). An older host kept one host-wide
/// `host_execution`, which the host moves onto its local pairing; read the
/// same way here until it has.
pub(crate) fn host_execution_enabled(config_path: &Path) -> bool {
    let Some(config) = std::fs::read_to_string(config_path)
        .ok()
        .and_then(|raw| serde_json::from_str::<Value>(&raw).ok())
    else {
        return false;
    };
    let legacy = config.get("host_execution").and_then(Value::as_bool) == Some(true);
    config
        .get("targets")
        .and_then(Value::as_array)
        .is_some_and(|targets| {
            targets.iter().any(|target| {
                let flag = |key: &str| target.get(key).and_then(Value::as_bool) == Some(true);
                let local = flag("allow_insecure_http")
                    && target
                        .get("base_url")
                        .and_then(Value::as_str)
                        .is_some_and(|url| url.starts_with("http://"));
                local && (flag("host_execution") || legacy) && !flag("session_paused")
            })
        })
}

/// Whether this computer can confine commands at all: the Agent Host's own
/// test (`host_exec::seatbelt::available`), repeated so status needs no fork.
pub(crate) fn host_execution_available() -> bool {
    cfg!(target_os = "macos") && Path::new("/usr/bin/sandbox-exec").is_file()
}
