//! The agent host under test, in a process or in this one.

use super::*;

// ---------------------------------------------------------------------------
// The real `lemma-agent-host serve` process.
// ---------------------------------------------------------------------------

/// A running `lemma-agent-host serve`, killed when the test drops it.
///
/// These tests drive the shipped binary rather than an in-process
/// `HostRuntime` for one reason: only the real process resolves adapters
/// through `LEMMA_AGENT_HOST_PATH` and only the real process uses its own
/// `current_exe()` as the MCP bridge, which is the wiring under test.
pub struct HostProcess {
    child: tokio::process::Child,
    pub stderr_path: PathBuf,
}

impl HostProcess {
    /// # Panics
    /// If pairing, configuration, or spawning fails.
    pub async fn start(root: &Path, control: &ControlPlane, shims: &ShimmedAgents) -> Self {
        let paths = lemma_agent_host::config::HostPaths::under(root);
        paths.ensure().unwrap();
        let installation_id = Uuid::new_v4().to_string();
        let target = lemma_agent_host::api::TargetClient::pair(
            control.base_url.clone(),
            "hermetic-pairing-code-with-entropy",
            "Hermetic host",
            &installation_id,
            true,
        )
        .await
        .unwrap();
        lemma_agent_host::config::HostConfig {
            installation_id,
            targets: vec![target],
            max_runs: 1,
        }
        .save(&paths)
        .unwrap();

        Self::resume(root, control, shims)
    }

    pub fn resume(root: &Path, control: &ControlPlane, shims: &ShimmedAgents) -> Self {
        let stderr_path = root.join("host-stderr.log");
        let stderr = std::fs::File::create(&stderr_path).unwrap();
        let child = tokio::process::Command::new(env!("CARGO_BIN_EXE_lemma-agent-host"))
            .arg("--data-dir")
            .arg(root)
            .arg("serve")
            // Adapter resolution reads this before PATH, so the pinned
            // manifest's native adapters resolve to the scripted agent.
            .env("LEMMA_AGENT_HOST_PATH", &shims.directory)
            // Hermetic means hermetic. Without this every host in this suite
            // npm-installed the Codex and Claude Agent adapters from the public
            // registry, and discovery then probed them -- launching the
            // developer's own Codex and Claude Code, which the README puts
            // behind `#[ignore]` as release qualification rather than CI.
            //
            // It also made the suite time-variable in a way that mattered: an
            // install landing mid-test makes the host re-publish its harnesses,
            // which is what used to strand a run.
            .env("LEMMA_AGENT_HOST_SKIP_ADAPTER_DOWNLOAD", "1")
            .env("LEMMA_AGENT_HOST_WORKSPACE_ROOT", root.join("lemma"))
            .env("RUST_LOG", "lemma_agent_host=debug")
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::from(stderr))
            .kill_on_drop(true)
            .spawn()
            .unwrap();
        // So a `wait_for` timeout can quote the host's own account of what it
        // did. The tests keep this whole directory in a `TempDir`, which
        // unwinding deletes -- so without this the log is gone by the time
        // anybody reads the panic.
        control.watch_host_log(&stderr_path);
        *control.state.scripted_traffic.lock().unwrap() = Some(shims.acp_log.clone());
        Self { child, stderr_path }
    }

    /// # Panics
    /// If the log cannot be read.
    #[must_use]
    pub fn stderr(&self) -> String {
        std::fs::read_to_string(&self.stderr_path).unwrap_or_default()
    }

    pub async fn shutdown(mut self) {
        let _ = self.child.kill().await;
    }
}

/// `HostRuntime` running inside the test process against real installed
/// adapters.
///
/// The subprocess variant above exists to shim adapter resolution; a test that
/// wants the developer's genuine Codex or Claude Code install wants the
/// opposite, so this one keeps the ambient environment and only redirects the
/// MCP bridge to the freshly built binary.
pub struct InProcessHost {
    handle: tokio::task::JoinHandle<anyhow::Result<()>>,
}

impl InProcessHost {
    /// `adapter_source` is a directory whose installed adapters should be
    /// reused, e.g. `LEMMA_REAL_AGENT_HOST_DATA_DIR`'s `adapters`.
    ///
    /// # Panics
    /// If pairing or configuration fails.
    #[cfg_attr(
        windows,
        expect(unused_variables, reason = "adapter reuse is a unix symlink")
    )]
    pub async fn start(
        root: &Path,
        control: &ControlPlane,
        adapter_source: &Path,
        bridge_executable: PathBuf,
    ) -> Self {
        let paths = lemma_agent_host::config::HostPaths::under(root);
        paths.ensure().unwrap();
        #[cfg(unix)]
        {
            let _ = std::fs::remove_dir_all(&paths.adapters);
            std::os::unix::fs::symlink(adapter_source, &paths.adapters).unwrap();
        }
        let installation_id = Uuid::new_v4().to_string();
        let target = lemma_agent_host::api::TargetClient::pair(
            control.base_url.clone(),
            "real-pairing-code-with-entropy",
            "Real host",
            &installation_id,
            true,
        )
        .await
        .unwrap();
        let config = lemma_agent_host::config::HostConfig {
            installation_id,
            targets: vec![target],
            max_runs: 1,
        };
        config.save(&paths).unwrap();
        let runtime = lemma_agent_host::runtime::HostRuntime::new(config, paths)
            .unwrap()
            .with_mcp_bridge_executable(bridge_executable);
        Self {
            handle: tokio::spawn(runtime.serve()),
        }
    }

    #[must_use]
    pub fn is_finished(&self) -> bool {
        self.handle.is_finished()
    }

    pub async fn shutdown(mut self) {
        self.handle.abort();
        let _ = (&mut self.handle).await;
    }
}

impl Drop for InProcessHost {
    fn drop(&mut self) {
        self.handle.abort();
    }
}
