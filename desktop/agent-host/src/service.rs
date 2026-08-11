//! Per-user headless service installation and lifecycle.

use std::path::{Path, PathBuf};
use std::process::{Command, Output};

use serde::Serialize;

use crate::config::HostPaths;

#[cfg(target_os = "macos")]
const SERVICE_LABEL: &str = "ai.lemma.agent-host";

#[derive(Clone, Debug, Serialize)]
pub struct ServiceStatus {
    pub manager: &'static str,
    pub installed: bool,
    pub running: bool,
    pub definition: Option<PathBuf>,
}

pub struct ServiceManager {
    executable: PathBuf,
    paths: HostPaths,
}

impl ServiceManager {
    pub fn current(paths: HostPaths) -> anyhow::Result<Self> {
        Ok(Self {
            executable: std::env::current_exe()?,
            paths,
        })
    }

    pub fn install(&self) -> anyhow::Result<()> {
        anyhow::ensure!(
            !desktop_locald_present()
                || std::env::var_os("LEMMA_AGENT_HOST_ALLOW_STANDALONE_SERVICE")
                    .is_some_and(|value| value == "1"),
            concat!(
                "Lemma Desktop/locald is installed and already owns Agent Host lifecycle; ",
                "use the Desktop controls instead"
            )
        );
        self.paths.ensure()?;
        platform::install(&self.executable, &self.paths)
    }

    pub fn uninstall(&self) -> anyhow::Result<()> {
        platform::uninstall()
    }

    pub fn start(&self) -> anyhow::Result<()> {
        platform::start()
    }

    pub fn stop(&self) -> anyhow::Result<()> {
        platform::stop()
    }

    pub fn restart(&self) -> anyhow::Result<()> {
        if platform::status()?.running {
            platform::stop()?;
        }
        platform::start()
    }

    pub fn status(&self) -> anyhow::Result<ServiceStatus> {
        platform::status()
    }
}

fn desktop_locald_present() -> bool {
    let root = std::env::var_os("LEMMA_LOCALD_ROOT")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .or_else(|| {
            #[cfg(target_os = "macos")]
            {
                std::env::var_os("HOME")
                    .map(PathBuf::from)
                    .map(|home| home.join("Library/Application Support/Lemma/locald"))
            }
            #[cfg(target_os = "windows")]
            {
                std::env::var_os("LOCALAPPDATA")
                    .map(PathBuf::from)
                    .map(|root| root.join("Lemma/locald"))
            }
            #[cfg(all(unix, not(target_os = "macos")))]
            {
                std::env::var_os("XDG_STATE_HOME")
                    .map(PathBuf::from)
                    .or_else(|| {
                        std::env::var_os("HOME")
                            .map(PathBuf::from)
                            .map(|home| home.join(".local/state"))
                    })
                    .map(|root| root.join("lemma/locald"))
            }
        });
    root.is_some_and(|root| root.join("control.token").is_file())
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

#[cfg(unix)]
fn write_atomic(path: &Path, contents: &str) -> anyhow::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let temporary = path.with_extension("tmp");
    std::fs::write(&temporary, contents)?;
    std::fs::rename(temporary, path)?;
    Ok(())
}

#[cfg(target_os = "macos")]
mod platform {
    use super::{
        Command, HostPaths, Path, PathBuf, SERVICE_LABEL, ServiceStatus, run_checked, write_atomic,
    };

    const MANAGER: &str = "launchd";

    pub fn install(executable: &Path, paths: &HostPaths) -> anyhow::Result<()> {
        let definition = definition_path()?;
        write_atomic(
            &definition,
            &render_launch_agent(executable, &paths.root, &paths.log),
        )?;
        let domain = launchd_domain()?;
        let _ = Command::new("launchctl")
            .args(["bootout", &domain])
            .arg(&definition)
            .output();
        run_checked(
            Command::new("launchctl")
                .args(["bootstrap", &domain])
                .arg(&definition),
            "installing Agent Host launch agent",
        )?;
        start()
    }

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

    pub fn start() -> anyhow::Result<()> {
        let target = launchd_target()?;
        run_checked(
            Command::new("launchctl").args(["kickstart", "-k", &target]),
            "starting Agent Host launch agent",
        )?;
        Ok(())
    }

    pub fn stop() -> anyhow::Result<()> {
        let target = launchd_target()?;
        let status = Command::new("launchctl")
            .args(["kill", "SIGTERM", &target])
            .status()?;
        anyhow::ensure!(
            status.success() || !self::status()?.running,
            "stopping Agent Host launch agent failed with {status}"
        );
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

    fn xml(value: &Path) -> String {
        value
            .to_string_lossy()
            .replace('&', "&amp;")
            .replace('<', "&lt;")
            .replace('>', "&gt;")
            .replace('"', "&quot;")
            .replace('\'', "&apos;")
    }

    fn render_launch_agent(executable: &Path, data_dir: &Path, log: &Path) -> String {
        format!(
            r#"<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{SERVICE_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{}</string>
    <string>--data-dir</string>
    <string>{}</string>
    <string>serve</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>{}</string>
  <key>StandardErrorPath</key><string>{}</string>
</dict>
</plist>
"#,
            xml(executable),
            xml(data_dir),
            xml(log),
            xml(log),
        )
    }

    #[cfg(test)]
    mod tests {
        use std::path::Path;

        use super::render_launch_agent;

        #[test]
        fn launch_agent_uses_absolute_arguments_without_a_shell() {
            let rendered = render_launch_agent(
                Path::new("/Applications/Lemma & Co/lemma-agent-host"),
                Path::new("/tmp/lemma data"),
                Path::new("/tmp/lemma data/host.log"),
            );
            assert!(rendered.contains("Lemma &amp; Co"));
            assert!(rendered.contains("<string>serve</string>"));
            assert!(!rendered.contains("/bin/sh"));
        }
    }
}

#[cfg(all(unix, not(target_os = "macos")))]
mod platform {
    use super::{Command, HostPaths, Path, PathBuf, ServiceStatus, run_checked, write_atomic};

    const MANAGER: &str = "systemd-user";

    pub fn install(executable: &Path, paths: &HostPaths) -> anyhow::Result<()> {
        let definition = definition_path()?;
        write_atomic(
            &definition,
            &render_unit(executable, &paths.root, &paths.log),
        )?;
        run_checked(
            Command::new("systemctl").args(["--user", "daemon-reload"]),
            "reloading systemd user units",
        )?;
        run_checked(
            Command::new("systemctl").args([
                "--user",
                "enable",
                "--now",
                "lemma-agent-host.service",
            ]),
            "enabling Agent Host service",
        )?;
        Ok(())
    }

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

    pub fn start() -> anyhow::Result<()> {
        run_checked(
            Command::new("systemctl").args(["--user", "start", "lemma-agent-host.service"]),
            "starting Agent Host service",
        )?;
        Ok(())
    }

    pub fn stop() -> anyhow::Result<()> {
        run_checked(
            Command::new("systemctl").args(["--user", "stop", "lemma-agent-host.service"]),
            "stopping Agent Host service",
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

    fn systemd_quote(path: &Path) -> String {
        format!(
            "\"{}\"",
            path.to_string_lossy()
                .replace('\\', "\\\\")
                .replace('"', "\\\"")
        )
    }

    fn render_unit(executable: &Path, data_dir: &Path, log: &Path) -> String {
        format!(
            "[Unit]\nDescription=Lemma Agent Host\nAfter=network-online.target\n\n\
             [Service]\nType=simple\nExecStart={} --data-dir {} serve\nRestart=on-failure\n\
             RestartSec=3\nStandardOutput=append:{}\nStandardError=append:{}\n\n\
             [Install]\nWantedBy=default.target\n",
            systemd_quote(executable),
            systemd_quote(data_dir),
            systemd_quote(log),
            systemd_quote(log),
        )
    }

    #[cfg(test)]
    mod tests {
        use std::path::Path;

        use super::render_unit;

        #[test]
        fn unit_uses_exec_arguments_without_a_shell() {
            let rendered = render_unit(
                Path::new("/opt/Lemma Host/bin"),
                Path::new("/tmp/data"),
                Path::new("/tmp/data/log"),
            );
            assert!(rendered.contains("ExecStart=\"/opt/Lemma Host/bin\""));
            assert!(!rendered.contains("/bin/sh"));
        }
    }
}

#[cfg(windows)]
mod platform {
    // The scheduled-task calls below are the only ones that need it, and a
    // file-level import does not reach into this module.
    use super::{Command, HostPaths, Path, ServiceStatus, run_checked};
    use crate::NoConsoleWindow;

    const MANAGER: &str = "task-scheduler";
    const TASK_NAME: &str = "Lemma Agent Host";

    pub fn install(executable: &Path, paths: &HostPaths) -> anyhow::Result<()> {
        let action = format!(
            "\"{}\" --data-dir \"{}\" serve",
            executable.display(),
            paths.root.display()
        );
        run_checked(
            Command::new("schtasks").no_console_window().args([
                "/Create", "/TN", TASK_NAME, "/TR", &action, "/SC", "ONLOGON", "/RL", "LIMITED",
                "/F",
            ]),
            "installing Agent Host scheduled task",
        )?;
        start()
    }

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

    pub fn start() -> anyhow::Result<()> {
        run_checked(
            Command::new("schtasks")
                .no_console_window()
                .args(["/Run", "/TN", TASK_NAME]),
            "starting Agent Host scheduled task",
        )?;
        Ok(())
    }

    pub fn stop() -> anyhow::Result<()> {
        if status()?.running {
            run_checked(
                Command::new("schtasks")
                    .no_console_window()
                    .args(["/End", "/TN", TASK_NAME]),
                "stopping Agent Host scheduled task",
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
