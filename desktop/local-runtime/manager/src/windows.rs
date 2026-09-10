//! The WSL host: the runtime distribution, and starting it.

use super::*;

impl ManagedRuntime {
    pub fn wsl_distribution(&self) -> &str {
        &self.config.wsl_distribution
    }

    /// A cheap identity for the rootfs archive an installed guest came from.
    ///
    /// Run wsl.exe and hand back whatever it produced, exit code included.
    ///
    /// `wsl()` turns a non-zero exit into an error, which is right for a command
    /// whose success is the point. It is wrong for a query whose failure is
    /// itself an answer -- `--terminate` on a distribution that is not running,
    /// or `journalctl` in a guest that never came up.
    ///
    /// This is the real runner; `wsl()` is this plus the status check. It used
    /// to be the other way round, and the wrapper's two match arms were both
    /// `Err(error) => Err(error)` -- identical to calling `wsl()` directly,
    /// which at the time did not check the status either. So the doc comment
    /// above described a contract that neither function had.
    #[cfg(windows)]
    pub(crate) fn wsl_allowing_failure(
        &self,
        arguments: &[&str],
        input: Option<&[u8]>,
    ) -> io::Result<std::process::Output> {
        run_wsl_command(
            &self.config.wsl_executable,
            arguments,
            input,
            wsl_budget(arguments),
            &self.config.local_root.join("logs/wsl.log"),
        )
    }

    #[cfg(windows)]
    pub(crate) fn start_windows(&self) -> io::Result<()> {
        if !self.windows_wsl_ready() {
            let pending = self.wsl_setup_marker().is_file();
            return Err(io::Error::new(
                io::ErrorKind::NotFound,
                if pending {
                    "Windows must restart to finish enabling WSL 2; restart Windows, then reopen Lemma"
                } else {
                    "WSL 2 is required for Lemma's private runtime; choose Set up Windows runtime and approve the Windows prompt"
                },
            ));
        }
        let _ = fs::remove_file(self.wsl_setup_marker());
        let install = self.config.local_root.join("runtime/wsl");
        fs::create_dir_all(&install)?;
        let rootfs = self.config.artifact_root.join("windows-x86_64/rootfs.tar");
        // The holder first, and its share published, because the runtime
        // distribution's init refuses to start without it -- and because the
        // upgrade below is only safe once the data is somewhere else.
        self.ensure_data_distribution(&rootfs)?;
        let installed = self.guest_is_registered(self.wsl_distribution());
        if !installed {
            if !rootfs.is_file() {
                return Err(io::Error::new(
                    io::ErrorKind::NotFound,
                    format!("private WSL rootfs is missing: {}", rootfs.display()),
                ));
            }
            let install_path = install.to_string_lossy().into_owned();
            let rootfs_path = rootfs.to_string_lossy().into_owned();
            self.wsl(
                &[
                    "--import",
                    self.wsl_distribution(),
                    &install_path,
                    &rootfs_path,
                    "--version",
                    "2",
                ],
                None,
            )?;
            let stamp = rootfs_stamp(&rootfs)?;
            fs::write(self.guest_release_marker(), stamp)?;
            // Copies nothing here. It records that the holder is the home, so
            // the first upgrade after this one knows it may replace the
            // runtime distribution.
            self.migrate_data_into_holder()?;
        } else {
            // Always before the replacement, and against the distribution that
            // currently holds the data rather than the one about to.
            self.migrate_data_into_holder()?;
            if !self.installed_guest_is_current(&rootfs)? {
                self.replace_runtime_distribution(&install, &rootfs)?;
            }
        }
        let capability = fs::read(&self.capability_file)?;
        self.wsl(
            &[
                "--distribution",
                self.wsl_distribution(),
                "--user",
                "root",
                "--exec",
                "/bin/sh",
                "-c",
                "umask 077; mkdir -p /etc/lemma; cat > /etc/lemma/guest.capability",
            ],
            Some(&capability),
        )?;
        self.wsl(
            &[
                "--distribution",
                self.wsl_distribution(),
                "--user",
                "root",
                "--exec",
                "/usr/local/bin/lemma-runtime-init",
            ],
            None,
        )?;
        Ok(())
    }

    /// Whether WSL 2 is installed and its service will answer.
    ///
    /// The first wsl.exe call `start_windows` makes, and so the one a wedged
    /// WSL blocks first. It ran unbounded and discarded stderr, which is the
    /// worst combination available: the start path stopped here with nothing
    /// written anywhere. Now it is bounded, and logged like every other call.
    #[cfg(windows)]
    pub(crate) fn windows_wsl_ready(&self) -> bool {
        self.wsl_allowing_failure(&["--status"], None)
            .is_ok_and(|output| output.status.success())
    }

    #[cfg(windows)]
    pub(crate) fn wsl_setup_marker(&self) -> PathBuf {
        self.config
            .local_root
            .join("runtime/wsl-setup-pending.json")
    }

    #[cfg(windows)]
    pub(crate) fn prepare_windows_host(&self) -> io::Result<Value> {
        if self.windows_wsl_ready() {
            let _ = fs::remove_file(self.wsl_setup_marker());
            return Ok(json!({
                "ready": true,
                "reboot_required": false,
                "platform": "windows",
            }));
        }
        write_private_atomic(
            &self.wsl_setup_marker(),
            br#"{"schema_version":1,"operation":"wsl-install"}"#,
        )?;
        let script = concat!(
            "$ErrorActionPreference='Stop'; ",
            "try { $p=Start-Process -FilePath (Join-Path $env:WINDIR 'System32\\wsl.exe') ",
            "-ArgumentList @('--install','--no-distribution','--no-launch') ",
            "-Verb RunAs -Wait -PassThru; exit $p.ExitCode } ",
            "catch { Write-Error 'Windows administrator approval was cancelled or failed'; exit 1223 }"
        );
        let status = match Command::new("powershell.exe")
            .no_console_window()
            .args([
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
        {
            Ok(status) => status,
            Err(error) => {
                let _ = fs::remove_file(self.wsl_setup_marker());
                return Err(error);
            }
        };
        if !status.success() {
            let _ = fs::remove_file(self.wsl_setup_marker());
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "Windows did not approve or complete WSL 2 setup",
            ));
        }
        let ready = self.windows_wsl_ready();
        if ready {
            let _ = fs::remove_file(self.wsl_setup_marker());
        }
        Ok(json!({
            "ready": ready,
            "reboot_required": !ready,
            "platform": "windows",
        }))
    }

    /// Run wsl.exe and fail unless it succeeded.
    ///
    /// The status check is the whole point of this wrapper, and for a long time
    /// it was missing: every caller treated a non-zero `wsl.exe` exit as
    /// success. A failed `--import` returned Ok, `start_windows` then wrote the
    /// guest release marker recording a distribution that had never been
    /// created, and the user waited out the 120s `wait_ready` timeout to be told
    /// only that the runtime did not come up. The cause was in logs/wsl.log and
    /// nowhere else.
    ///
    /// `wsl.exe` writes its diagnostics as UTF-16, so the message is decoded
    /// rather than passed through as bytes.
    #[cfg(windows)]
    pub(crate) fn wsl(
        &self,
        arguments: &[&str],
        input: Option<&[u8]>,
    ) -> io::Result<std::process::Output> {
        let output = self.wsl_allowing_failure(arguments, input)?;
        if !output.status.success() {
            let message = wsl_message(&output.stderr);
            let detail = if message.trim().is_empty() {
                format!("wsl.exe {} failed ({})", arguments.join(" "), output.status)
            } else {
                format!(
                    "wsl.exe {} failed ({}): {}",
                    arguments.join(" "),
                    output.status,
                    message.trim()
                )
            };
            return Err(io::Error::other(detail));
        }
        Ok(output)
    }
}
