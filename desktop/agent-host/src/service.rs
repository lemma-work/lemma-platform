//! Removing a per-user headless service a previous release installed.
//!
//! `install-service` used to write a launchd `LaunchAgent`, a systemd user unit,
//! or a per-user Task Scheduler entry, all pointing at a downloaded release. It
//! was a second install channel: a copy of the Agent Host that Desktop did not
//! start, did not supervise and could not upgrade, running against the same
//! pairing and speaking whatever protocol version it was built with. Desktop
//! owns the Agent Host's lifecycle; the README has said so for a while, and
//! said this was gone while the subcommands were still there.
//!
//! What remains is the way out. Somebody who ran `install-service` on an older
//! build still has that service on their machine, and deleting the code that
//! removes it would strand them with a second host they cannot turn off from
//! Lemma. So `status` still reports one, and `uninstall-service` still deletes
//! it. Nothing here installs, starts or stops anything.

use std::path::PathBuf;
use std::process::{Command, Output};

use serde::Serialize;

#[cfg(target_os = "macos")]
const SERVICE_LABEL: &str = "ai.lemma.agent-host";

#[derive(Clone, Debug, Serialize)]
pub struct ServiceStatus {
    pub manager: &'static str,
    pub installed: bool,
    pub running: bool,
    pub definition: Option<PathBuf>,
}

pub struct ServiceManager;

impl ServiceManager {
    /// Takes no paths: what is left addresses the OS's own service registry,
    /// which is where an older release put its definition, not Lemma's data
    /// directory.
    #[must_use]
    pub fn current() -> Self {
        Self
    }

    /// Remove a service left behind by a release that could still install one.
    pub fn uninstall(&self) -> anyhow::Result<()> {
        platform::uninstall()
    }

    /// Whether a service from an older release is still on this machine.
    ///
    /// Reported by `status` so that a second Agent Host running behind
    /// Desktop's back is something a person can see rather than something they
    /// have to suspect.
    pub fn status(&self) -> anyhow::Result<ServiceStatus> {
        platform::status()
    }
}

fn run_checked(command: &mut Command, action: &str) -> anyhow::Result<Output> {
    let output = command.output()?;
    if output.status.success() {
        return Ok(output);
    }
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_owned();
    anyhow::bail!(
        "{action} failed with {}{}",
        output.status,
        if stderr.is_empty() {
            String::new()
        } else {
            format!(": {stderr}")
        }
    )
}

#[cfg(target_os = "macos")]
mod platform {
    use super::{Command, PathBuf, SERVICE_LABEL, ServiceStatus, run_checked};

    const MANAGER: &str = "launchd";

    pub fn uninstall() -> anyhow::Result<()> {
        let definition = definition_path()?;
        if definition.exists() {
            let domain = launchd_domain()?;
            let _ = Command::new("launchctl")
                .args(["bootout", &domain])
                .arg(&definition)
                .output();
            std::fs::remove_file(definition)?;
        }
        Ok(())
    }

    pub fn status() -> anyhow::Result<ServiceStatus> {
        let definition = definition_path()?;
        let running = if definition.exists() {
            Command::new("launchctl")
                .args(["print", &launchd_target()?])
                .output()?
                .status
                .success()
        } else {
            false
        };
        Ok(ServiceStatus {
            manager: MANAGER,
            installed: definition.exists(),
            running,
            definition: Some(definition),
        })
    }

    fn definition_path() -> anyhow::Result<PathBuf> {
        let home = std::env::var_os("HOME")
            .map(PathBuf::from)
            .ok_or_else(|| anyhow::anyhow!("HOME is not set"))?;
        Ok(home
            .join("Library/LaunchAgents")
            .join(format!("{SERVICE_LABEL}.plist")))
    }

    fn user_id() -> anyhow::Result<String> {
        let output = run_checked(Command::new("id").arg("-u"), "resolving user ID")?;
        Ok(String::from_utf8(output.stdout)?.trim().to_owned())
    }

    fn launchd_domain() -> anyhow::Result<String> {
        Ok(format!("gui/{}", user_id()?))
    }

    fn launchd_target() -> anyhow::Result<String> {
        Ok(format!("{}/{SERVICE_LABEL}", launchd_domain()?))
    }
}

#[cfg(all(unix, not(target_os = "macos")))]
mod platform {
    use super::{Command, PathBuf, ServiceStatus, run_checked};

    const MANAGER: &str = "systemd-user";

    pub fn uninstall() -> anyhow::Result<()> {
        let definition = definition_path()?;
        let _ = Command::new("systemctl")
            .args(["--user", "disable", "--now", "lemma-agent-host.service"])
            .output();
        if definition.exists() {
            std::fs::remove_file(definition)?;
        }
        run_checked(
            Command::new("systemctl").args(["--user", "daemon-reload"]),
            "reloading systemd user units",
        )?;
        Ok(())
    }

    pub fn status() -> anyhow::Result<ServiceStatus> {
        let definition = definition_path()?;
        let running = definition.exists()
            && Command::new("systemctl")
                .args(["--user", "is-active", "--quiet", "lemma-agent-host.service"])
                .status()?
                .success();
        Ok(ServiceStatus {
            manager: MANAGER,
            installed: definition.exists(),
            running,
            definition: Some(definition),
        })
    }

    fn definition_path() -> anyhow::Result<PathBuf> {
        let root = std::env::var_os("XDG_CONFIG_HOME")
            .map(PathBuf::from)
            .or_else(|| std::env::var_os("HOME").map(|home| PathBuf::from(home).join(".config")))
            .ok_or_else(|| anyhow::anyhow!("HOME and XDG_CONFIG_HOME are not set"))?;
        Ok(root.join("systemd/user").join("lemma-agent-host.service"))
    }
}

#[cfg(windows)]
mod platform {
    // The scheduled-task calls below are the only ones that need it, and a
    // file-level import does not reach into this module.
    use super::{Command, ServiceStatus, run_checked};
    use crate::NoConsoleWindow;

    const MANAGER: &str = "task-scheduler";
    const TASK_NAME: &str = "Lemma Agent Host";

    pub fn uninstall() -> anyhow::Result<()> {
        if status()?.installed {
            run_checked(
                Command::new("schtasks")
                    .no_console_window()
                    .args(["/Delete", "/TN", TASK_NAME, "/F"]),
                "removing Agent Host scheduled task",
            )?;
        }
        Ok(())
    }

    pub fn status() -> anyhow::Result<ServiceStatus> {
        let output = Command::new("schtasks")
            .no_console_window()
            .args(["/Query", "/TN", TASK_NAME, "/FO", "LIST", "/V"])
            .output()?;
        let text = String::from_utf8_lossy(&output.stdout);
        Ok(ServiceStatus {
            manager: MANAGER,
            installed: output.status.success(),
            running: output.status.success()
                && text.lines().any(|line| {
                    line.to_ascii_lowercase().contains("status:")
                        && line.to_ascii_lowercase().contains("running")
                }),
            definition: None,
        })
    }
}
