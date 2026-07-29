//! Multi-target Agent Host supervisor.

use std::collections::{BTreeMap, HashMap};
use std::path::PathBuf;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

use agent_client_protocol::schema::v1::{EnvVariable, McpServer, McpServerStdio};
use chrono::Utc;
use serde_json::Value;
use sha2::{Digest, Sha256};
use tokio::sync::{OwnedSemaphorePermit, Semaphore, watch};
use tokio::task::JoinHandle;
use uuid::Uuid;

use crate::acp::{AcpCallbacks, AcpDriver, AcpRunRequest, AgentDriver};
use crate::adapters::{AdapterManifest, ResolvedAdapter};
use crate::api::{PublishedHarness, TargetClient};
use crate::config::{HostConfig, HostPaths, TargetConfig};
use crate::crypto::SecretVault;
use crate::journal::{AcceptOutcome, Journal};
use crate::protocol::{
    Checkpoint, Command, CommandKind, CommandRejection, EventType, HarnessCapabilities,
    HarnessHealth, HarnessSnapshot, HostCapacity, HostStatus, JsonMap, RejectionCode, RunSpec,
    RunState,
};

const HARNESS_REFRESH_INTERVAL: Duration = Duration::from_secs(15 * 60);
const JOURNAL_CLEANUP_INTERVAL: Duration = Duration::from_secs(24 * 60 * 60);
const RETRY_MIN: Duration = Duration::from_millis(500);
const RETRY_MAX: Duration = Duration::from_secs(30);
const SHUTDOWN_GRACE: Duration = Duration::from_secs(30);

pub struct HostRuntime {
    config: HostConfig,
    paths: HostPaths,
    journal: Journal,
    manifest: AdapterManifest,
    vault: Arc<dyn SecretVault>,
    driver: Arc<dyn AgentDriver>,
    mcp_bridge_executable: PathBuf,
    instance_id: Uuid,
}

impl HostRuntime {
    pub fn new(
        config: HostConfig,
        paths: HostPaths,
        vault: Arc<dyn SecretVault>,
    ) -> anyhow::Result<Self> {
        config.validate()?;
        let journal = Journal::open(&paths.journal)?;
        let manifest = AdapterManifest::builtin()?.with_cache_root(paths.adapters.clone());
        Ok(Self {
            config,
            paths,
            journal,
            manifest,
            vault,
            driver: Arc::new(AcpDriver),
            mcp_bridge_executable: std::env::current_exe()?,
            instance_id: Uuid::new_v4(),
        })
    }

    #[cfg(test)]
    #[must_use]
    pub fn with_driver(mut self, driver: Arc<dyn AgentDriver>) -> Self {
        self.driver = driver;
        self
    }

    /// Overrides the executable used for the internal MCP bridge.
    ///
    /// This exists for integration tests, whose `current_exe()` is the test
    /// harness rather than the Agent Host binary.
    #[doc(hidden)]
    #[must_use]
    pub fn with_mcp_bridge_executable(mut self, executable: PathBuf) -> Self {
        self.mcp_bridge_executable = executable;
        self
    }

    pub async fn serve(self) -> anyhow::Result<()> {
        let deleted = self.journal.cleanup_retained(Utc::now())?;
        if deleted > 0 {
            tracing::info!(deleted, "cleaned retained Agent Host journal records");
        }
        let global_capacity = Arc::new(Semaphore::new(usize::from(self.config.max_runs)));
        let mut targets =
            HashMap::<Uuid, (watch::Sender<bool>, JoinHandle<anyhow::Result<()>>)>::new();
        let mut scan = tokio::time::interval(Duration::from_secs(2));
        scan.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
        let mut cleanup_due = std::time::Instant::now() + JOURNAL_CLEANUP_INTERVAL;
        let shutdown = shutdown_signal();
        tokio::pin!(shutdown);
        loop {
            tokio::select! {
                signal = &mut shutdown => {
                    signal?;
                    tracing::info!("shutdown requested");
                    break;
                }
                _ = scan.tick() => {
                    if std::time::Instant::now() >= cleanup_due {
                        let deleted = self.journal.cleanup_retained(Utc::now())?;
                        if deleted > 0 {
                            tracing::info!(
                                deleted,
                                "cleaned retained Agent Host journal records"
                            );
                        }
                        cleanup_due =
                            std::time::Instant::now() + JOURNAL_CLEANUP_INTERVAL;
                    }
                    let current = HostConfig::load_or_create(&self.paths)?;
                    current.validate()?;
                    let enabled = current
                        .targets
                        .iter()
                        .filter(|target| target.enabled)
                        .map(|target| (target.target_id, target))
                        .collect::<HashMap<_, _>>();
                    let stopped = targets
                        .iter()
                        .filter_map(|(target_id, (_, handle))| {
                            (handle.is_finished() || !enabled.contains_key(target_id))
                                .then_some(*target_id)
                        })
                        .collect::<Vec<_>>();
                    for target_id in stopped {
                        if let Some((shutdown, handle)) = targets.remove(&target_id) {
                            let _ = shutdown.send(true);
                            match handle.await {
                                Ok(Ok(())) => {}
                                Ok(Err(error)) => tracing::warn!(
                                    %target_id,
                                    %error,
                                    "target worker stopped; it will be restarted if still enabled"
                                ),
                                Err(error) => tracing::warn!(
                                    %target_id,
                                    %error,
                                    "target worker task failed"
                                ),
                            }
                        }
                    }
                    for (target_id, target) in enabled {
                        if targets.contains_key(&target_id) {
                            continue;
                        }
                        let (shutdown_tx, shutdown_rx) = watch::channel(false);
                        let worker = TargetWorker::new(
                            target.clone(),
                            current.installation_id.clone(),
                            self.instance_id,
                            self.paths.clone(),
                            self.journal.clone(),
                            self.manifest.clone(),
                            self.vault.as_ref(),
                            Arc::clone(&self.driver),
                            self.mcp_bridge_executable.clone(),
                            Arc::clone(&global_capacity),
                            current.max_runs,
                            shutdown_rx,
                        )?;
                        targets.insert(target_id, (
                            shutdown_tx,
                            tokio::spawn(async move { worker.run().await }),
                        ));
                    }
                }
            }
        }
        for (shutdown, _) in targets.values() {
            let _ = shutdown.send(true);
        }
        for (target_id, (_, handle)) in targets {
            if let Ok(Err(error)) = handle.await {
                tracing::warn!(%target_id, %error, "target worker failed during shutdown");
            }
        }
        Ok(())
    }
}

async fn shutdown_signal() -> std::io::Result<()> {
    #[cfg(unix)]
    {
        let mut terminate =
            tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())?;
        tokio::select! {
            result = tokio::signal::ctrl_c() => result,
            _ = terminate.recv() => Ok(()),
        }
    }
    #[cfg(not(unix))]
    {
        tokio::signal::ctrl_c().await
    }
}

struct TargetWorker {
    target: TargetConfig,
    client: TargetClient,
    paths: HostPaths,
    journal: Journal,
    manifest: AdapterManifest,
    driver: Arc<dyn AgentDriver>,
    mcp_bridge_executable: PathBuf,
    global_capacity: Arc<Semaphore>,
    max_runs: u16,
    shutdown: watch::Receiver<bool>,
    harnesses: BTreeMap<Uuid, PublishedHarness>,
    active_runs: HashMap<Uuid, JoinHandle<anyhow::Result<()>>>,
    draining: bool,
    refresh_due: std::time::Instant,
}

impl TargetWorker {
    #[allow(clippy::too_many_arguments)]
    fn new(
        target: TargetConfig,
        installation_id: String,
        instance_id: Uuid,
        paths: HostPaths,
        journal: Journal,
        manifest: AdapterManifest,
        vault: &dyn SecretVault,
        driver: Arc<dyn AgentDriver>,
        mcp_bridge_executable: PathBuf,
        global_capacity: Arc<Semaphore>,
        max_runs: u16,
        shutdown: watch::Receiver<bool>,
    ) -> anyhow::Result<Self> {
        let client = TargetClient::new(
            target.clone(),
            installation_id,
            instance_id,
            &manifest,
            vault,
        )?;
        journal.register_target(target.target_id)?;
        let draining = target.draining;
        Ok(Self {
            target,
            client,
            paths,
            journal,
            manifest,
            driver,
            mcp_bridge_executable,
            global_capacity,
            max_runs,
            shutdown,
            harnesses: BTreeMap::new(),
            active_runs: HashMap::new(),
            draining,
            refresh_due: std::time::Instant::now(),
        })
    }

    async fn run(mut self) -> anyhow::Result<()> {
        self.recover_interrupted_runs()?;
        let mut retry = RETRY_MIN;
        loop {
            if *self.shutdown.borrow() {
                return self.graceful_shutdown().await;
            }
            self.reap_finished().await;
            self.apply_local_controls()?;
            if self.refresh_due <= std::time::Instant::now() {
                if let Err(error) = self.refresh_harnesses().await {
                    tracing::warn!(
                        target = %self.target.name,
                        %error,
                        "harness refresh failed"
                    );
                }
                self.refresh_due = std::time::Instant::now() + HARNESS_REFRESH_INTERVAL;
            }
            if let Err(error) = self.flush_events().await {
                self.note_offline(&error.to_string())?;
                self.wait_retry(retry).await;
                retry = (retry * 2).min(RETRY_MAX);
                continue;
            }
            let (command_ids, checkpoints, rejections) =
                self.journal.pending_control(self.target.target_id)?;
            let available =
                u16::try_from(self.global_capacity.available_permits()).unwrap_or(u16::MAX);
            let active = self.max_runs.saturating_sub(available);
            let capacity = HostCapacity {
                max_runs: self.max_runs,
                active_runs: active,
                available_runs: if self.draining { 0 } else { available },
            };
            match self
                .client
                .poll(
                    capacity,
                    command_ids.clone(),
                    checkpoints.clone(),
                    rejections.clone(),
                )
                .await
            {
                Ok(response) => {
                    retry = RETRY_MIN;
                    self.journal
                        .update_target_state(self.target.target_id, "ONLINE", None)?;
                    self.journal.mark_control_applied(
                        self.target.target_id,
                        &command_ids,
                        &checkpoints,
                        &rejections,
                    )?;
                    if response.host_status == HostStatus::Revoked {
                        anyhow::bail!("target revoked this Agent Host");
                    }
                    if response.host_status == HostStatus::UpgradeRequired {
                        anyhow::bail!("target requires a newer Agent Host protocol");
                    }
                    for command in response.commands {
                        if let Err(error) = self.handle_command(&command) {
                            if let Some(rejection) = command_rejection(&command, &error) {
                                self.journal
                                    .record_rejection(self.target.target_id, &rejection)?;
                            }
                            tracing::error!(
                                target = %self.target.name,
                                %error,
                                "Agent Host command failed"
                            );
                        }
                    }
                    if response.poll_after_ms > 0 {
                        self.wait_retry(Duration::from_millis(response.poll_after_ms))
                            .await;
                    }
                }
                Err(error) => {
                    if error.is_unauthorized() {
                        self.cancel_all(
                            "Lemma rejected this Agent Host; the target may have been revoked",
                        )?;
                        return Err(error.into());
                    }
                    self.note_offline(&error.to_string())?;
                    self.wait_retry(retry).await;
                    retry = (retry * 2).min(RETRY_MAX);
                }
            }
        }
    }

    fn apply_local_controls(&mut self) -> anyhow::Result<()> {
        let config = HostConfig::load_or_create(&self.paths)?;
        let Some(current) = config
            .targets
            .iter()
            .find(|target| target.target_id == self.target.target_id)
        else {
            return Ok(());
        };
        self.draining = current.draining;
        if current.refresh_generation != self.target.refresh_generation {
            self.refresh_due = std::time::Instant::now();
        }
        self.target.draining = current.draining;
        self.target.refresh_generation = current.refresh_generation;
        Ok(())
    }

    fn handle_command(&mut self, command: &Command) -> anyhow::Result<()> {
        anyhow::ensure!(command.expires_at >= Utc::now(), "command is expired");
        command.verify_payload()?;
        match command.kind {
            CommandKind::StartRun => self.handle_start(command),
            CommandKind::CancelRun => self.handle_cancel(command),
            CommandKind::Drain => {
                self.journal
                    .record_simple_command(self.target.target_id, command)?;
                self.draining = true;
                Ok(())
            }
            CommandKind::Resume => {
                self.journal
                    .record_simple_command(self.target.target_id, command)?;
                self.draining = false;
                Ok(())
            }
            CommandKind::RefreshHarness => {
                self.journal
                    .record_simple_command(self.target.target_id, command)?;
                self.refresh_due = std::time::Instant::now();
                Ok(())
            }
            CommandKind::CloseSession => {
                self.journal
                    .record_simple_command(self.target.target_id, command)?;
                Ok(())
            }
            CommandKind::RotateDeviceKey => {
                self.journal
                    .record_simple_command(self.target.target_id, command)?;
                anyhow::bail!("device-key rotation requires a new user-authorized pairing")
            }
        }
    }

    fn handle_start(&mut self, command: &Command) -> anyhow::Result<()> {
        anyhow::ensure!(!self.draining, "Agent Host is draining");
        let spec: RunSpec = serde_json::from_value(command.payload.clone())?;
        let published = self
            .harnesses
            .get(&spec.harness_id)
            .cloned()
            .ok_or_else(|| anyhow::anyhow!("command references an unknown harness"))?;
        anyhow::ensure!(
            published.config_revision == spec.profile_revision,
            "harness configuration revision changed"
        );
        if self.active_runs.contains_key(&spec.agent_run_id) {
            let outcome = self.journal.accept_start(
                self.target.target_id,
                command,
                &spec,
                &published.harness_key,
                &published.adapter_version,
            )?;
            anyhow::ensure!(
                outcome == AcceptOutcome::Duplicate,
                "active run did not have a durable command receipt"
            );
            return Ok(());
        }
        // Resolve the adapter and reserve real process capacity before writing
        // ACCEPTED. Once ACCEPTED is durable, Lemma must not start a cloud
        // fallback, so waiting on the semaphore after that point can duplicate
        // provider work.
        let adapter = self.manifest.resolve(&published.harness_key)?;
        let permit = Arc::clone(&self.global_capacity)
            .try_acquire_owned()
            .map_err(|_| anyhow::anyhow!("Agent Host capacity changed; command will be retried"))?;
        let outcome = self.journal.accept_start(
            self.target.target_id,
            command,
            &spec,
            &published.harness_key,
            &published.adapter_version,
        )?;
        if outcome == AcceptOutcome::Duplicate {
            return Ok(());
        }
        self.spawn_run(spec, adapter, permit);
        Ok(())
    }

    fn spawn_run(&mut self, spec: RunSpec, adapter: ResolvedAdapter, permit: OwnedSemaphorePermit) {
        let target_id = self.target.target_id;
        let client = self.client.clone();
        let journal = self.journal.clone();
        let driver = Arc::clone(&self.driver);
        let mcp_bridge_executable = self.mcp_bridge_executable.clone();
        let paths = self.paths.clone();
        let run_id = spec.agent_run_id;
        let handle = tokio::spawn(async move {
            let _permit = permit;
            let lease_epoch = journal
                .get_run(target_id, run_id)?
                .ok_or_else(|| anyhow::anyhow!("accepted run disappeared"))?
                .lease_epoch;
            journal.checkpoint(
                target_id,
                run_id,
                lease_epoch,
                Checkpoint::Accepted,
                RunState::Accepted,
                &JsonMap::new(),
            )?;
            let route = match client.resolve_mcp_route(spec.mcp_route_id).await {
                Ok(route) => route,
                Err(error) => {
                    terminal_failure(
                        &journal,
                        target_id,
                        run_id,
                        lease_epoch,
                        RunState::Failed,
                        &format!("could not obtain the run-scoped MCP route: {error}"),
                    )?;
                    return Ok(());
                }
            };
            anyhow::ensure!(
                route.run_id == run_id && route.lease_epoch == lease_epoch,
                "MCP route lease does not match the run"
            );
            let scratch = scratch_directory(&paths, target_id, run_id);
            std::fs::create_dir_all(&scratch)?;
            let mcp_server = McpServer::Stdio(
                McpServerStdio::new("lemma", mcp_bridge_executable)
                    .args(vec![
                        "--data-dir".to_owned(),
                        paths.root.to_string_lossy().into_owned(),
                        "mcp-bridge".to_owned(),
                        "--target-id".to_owned(),
                        target_id.to_string(),
                        "--route-id".to_owned(),
                        spec.mcp_route_id.to_string(),
                    ])
                    .env(vec![EnvVariable::new("LEMMA_AGENT_HOST_BRIDGE", "1")]),
            );
            let callbacks: Arc<dyn AcpCallbacks> = Arc::new(JournalCallbacks {
                journal: journal.clone(),
                target_id,
                run_id,
                lease_epoch,
                provider_seen: AtomicBool::new(false),
            });
            let remaining = (spec.run_deadline - Utc::now())
                .to_std()
                .unwrap_or(Duration::ZERO);
            let request = AcpRunRequest {
                adapter,
                run_spec: spec,
                scratch_directory: scratch.clone(),
                mcp_server: Some(mcp_server),
            };
            let outcome = tokio::time::timeout(remaining, driver.run(request, callbacks)).await;
            let _ = std::fs::remove_dir_all(&scratch);
            match outcome {
                Ok(Ok(outcome)) => {
                    let mut payload = JsonMap::new();
                    payload.insert("state".to_owned(), serde_json::to_value(outcome.state)?);
                    payload.insert("stop_reason".to_owned(), Value::String(outcome.stop_reason));
                    journal.append_event(
                        target_id,
                        run_id,
                        lease_epoch,
                        EventType::Terminal,
                        None,
                        payload,
                    )?;
                    journal.checkpoint(
                        target_id,
                        run_id,
                        lease_epoch,
                        Checkpoint::Terminal,
                        outcome.state,
                        &JsonMap::new(),
                    )?;
                }
                Ok(Err(error)) => {
                    let run = journal
                        .get_run(target_id, run_id)?
                        .ok_or_else(|| anyhow::anyhow!("run disappeared after adapter failure"))?;
                    let state = if run.checkpoint == Checkpoint::DispatchIntent {
                        RunState::DispatchUnknown
                    } else {
                        RunState::Failed
                    };
                    terminal_failure(
                        &journal,
                        target_id,
                        run_id,
                        lease_epoch,
                        state,
                        &redact_error(&error.to_string()),
                    )?;
                }
                Err(_) => {
                    terminal_failure(
                        &journal,
                        target_id,
                        run_id,
                        lease_epoch,
                        RunState::Failed,
                        "Agent Host run deadline elapsed; the provider process was terminated",
                    )?;
                }
            }
            Ok(())
        });
        self.active_runs.insert(run_id, handle);
    }

    fn handle_cancel(&mut self, command: &Command) -> anyhow::Result<()> {
        self.journal
            .record_simple_command(self.target.target_id, command)?;
        let run_id = command
            .run_id
            .ok_or_else(|| anyhow::anyhow!("cancel command has no run ID"))?;
        if let Some(handle) = self.active_runs.remove(&run_id) {
            handle.abort();
        }
        if let Some(run) = self.journal.get_run(self.target.target_id, run_id)?
            && !run.state.is_terminal()
        {
            terminal_failure(
                &self.journal,
                self.target.target_id,
                run_id,
                run.lease_epoch,
                RunState::Cancelled,
                "run cancelled by Lemma",
            )?;
        }
        Ok(())
    }

    fn recover_interrupted_runs(&self) -> anyhow::Result<()> {
        for run in self.journal.recoverable_runs(self.target.target_id)? {
            if run.prompt_dispatched {
                terminal_failure(
                    &self.journal,
                    self.target.target_id,
                    run.run_id,
                    run.lease_epoch,
                    RunState::DispatchUnknown,
                    "Agent Host restarted after prompt dispatch; the turn was not repeated",
                )?;
            } else {
                terminal_failure(
                    &self.journal,
                    self.target.target_id,
                    run.run_id,
                    run.lease_epoch,
                    RunState::Failed,
                    "Agent Host restarted before provider dispatch; Lemma may safely retry",
                )?;
            }
        }
        Ok(())
    }

    async fn refresh_harnesses(&mut self) -> anyhow::Result<()> {
        let mut snapshots = self.manifest.discover();
        for snapshot in &mut snapshots {
            if snapshot.health != HarnessHealth::Ready {
                continue;
            }
            let Ok(adapter) = self.manifest.resolve(&snapshot.harness_key) else {
                continue;
            };
            let scratch = self.paths.root.join("probe").join(&snapshot.harness_key);
            match tokio::time::timeout(Duration::from_secs(20), self.driver.probe(adapter, scratch))
                .await
            {
                Ok(Ok(probe)) => {
                    snapshot.config_options = probe.config_options;
                    snapshot.capabilities = capabilities_from_acp(&probe.capabilities);
                    snapshot.config_revision = snapshot_revision(snapshot);
                    snapshot
                        .metadata
                        .insert("acp_capabilities".into(), probe.capabilities);
                }
                Ok(Err(error)) => {
                    snapshot.health = HarnessHealth::ProbeFailed;
                    snapshot.stale_reason = Some(redact_error(&error.to_string()));
                }
                Err(_) => {
                    snapshot.health = HarnessHealth::ProbeFailed;
                    snapshot.stale_reason = Some("ACP probe timed out".to_owned());
                }
            }
        }
        let published = self.client.publish_harnesses(snapshots).await?;
        self.harnesses = published
            .into_iter()
            .map(|harness| (harness.id, harness))
            .collect();
        Ok(())
    }

    async fn flush_events(&self) -> anyhow::Result<()> {
        for batch in self.journal.pending_events(self.target.target_id, 1024)? {
            let ack = self.client.append_events(&batch).await?;
            self.journal
                .acknowledge_events(self.target.target_id, &ack)?;
        }
        Ok(())
    }

    async fn reap_finished(&mut self) {
        let finished = self
            .active_runs
            .iter()
            .filter_map(|(run_id, handle)| handle.is_finished().then_some(*run_id))
            .collect::<Vec<_>>();
        for run_id in finished {
            if let Some(handle) = self.active_runs.remove(&run_id)
                && let Err(error) = handle.await
            {
                tracing::error!(%run_id, %error, "agent run task terminated unexpectedly");
            }
        }
    }

    async fn graceful_shutdown(&mut self) -> anyhow::Result<()> {
        self.draining = true;
        let deadline = tokio::time::Instant::now() + SHUTDOWN_GRACE;
        loop {
            self.reap_finished().await;
            if let Err(error) = self.flush_events().await {
                tracing::warn!(%error, "could not flush Agent Host events during shutdown");
            }
            let (command_ids, checkpoints, rejections) =
                self.journal.pending_control(self.target.target_id)?;
            let capacity = HostCapacity {
                max_runs: self.max_runs,
                active_runs: self.max_runs.saturating_sub(
                    u16::try_from(self.global_capacity.available_permits()).unwrap_or(u16::MAX),
                ),
                available_runs: 0,
            };
            if let Ok(response) = self
                .client
                .poll(
                    capacity,
                    command_ids.clone(),
                    checkpoints.clone(),
                    rejections.clone(),
                )
                .await
            {
                self.journal.mark_control_applied(
                    self.target.target_id,
                    &command_ids,
                    &checkpoints,
                    &rejections,
                )?;
                for command in response.commands {
                    if command.kind == CommandKind::CancelRun {
                        let _ = self.handle_cancel(&command);
                    }
                }
            }
            if self.active_runs.is_empty() {
                return Ok(());
            }
            if tokio::time::Instant::now() >= deadline {
                break;
            }
            tokio::time::sleep(Duration::from_millis(250)).await;
        }
        self.cancel_all("Agent Host shutdown grace elapsed")?;
        self.flush_events().await?;
        Ok(())
    }

    fn cancel_all(&mut self, reason: &str) -> anyhow::Result<()> {
        let run_ids = self.active_runs.keys().copied().collect::<Vec<_>>();
        for run_id in run_ids {
            if let Some(handle) = self.active_runs.remove(&run_id) {
                handle.abort();
            }
            if let Some(run) = self.journal.get_run(self.target.target_id, run_id)?
                && !run.state.is_terminal()
            {
                terminal_failure(
                    &self.journal,
                    self.target.target_id,
                    run_id,
                    run.lease_epoch,
                    RunState::Cancelled,
                    reason,
                )?;
            }
        }
        Ok(())
    }

    fn note_offline(&self, error: &str) -> anyhow::Result<()> {
        self.journal.update_target_state(
            self.target.target_id,
            "OFFLINE",
            Some(&redact_error(error)),
        )?;
        Ok(())
    }

    async fn wait_retry(&mut self, duration: Duration) {
        tokio::select! {
            () = tokio::time::sleep(duration) => {}
            _ = self.shutdown.changed() => {}
        }
    }
}

fn command_rejection(command: &Command, error: &anyhow::Error) -> Option<CommandRejection> {
    if command.kind != CommandKind::StartRun {
        return None;
    }
    let run_id = command.run_id?;
    let lease_epoch = command.lease_epoch?;
    let detail = redact_error(&error.to_string());
    let normalized = detail.to_ascii_lowercase();
    let (code, retryable) = if normalized.contains("draining") {
        (RejectionCode::Draining, true)
    } else if normalized.contains("expired") {
        (RejectionCode::CommandExpired, false)
    } else if normalized.contains("unknown harness") {
        (RejectionCode::HarnessNotFound, false)
    } else if normalized.contains("revision changed") {
        (RejectionCode::ConfigRevisionStale, false)
    } else if normalized.contains("capacity changed") {
        (RejectionCode::CapacityLost, true)
    } else if normalized.contains("adapter") || normalized.contains("executable") {
        (RejectionCode::AdapterUnavailable, false)
    } else {
        (RejectionCode::InvalidCommand, false)
    };
    Some(CommandRejection {
        command_id: command.command_id,
        run_id,
        lease_epoch,
        code,
        retryable,
        detail: Some(detail.chars().take(1_000).collect()),
    })
}

struct JournalCallbacks {
    journal: Journal,
    target_id: Uuid,
    run_id: Uuid,
    lease_epoch: u32,
    provider_seen: AtomicBool,
}

impl AcpCallbacks for JournalCallbacks {
    fn before_prompt(&self, provider_session_id: &str) -> anyhow::Result<()> {
        self.journal.mark_dispatch_intent(
            self.target_id,
            self.run_id,
            self.lease_epoch,
            provider_session_id,
        )?;
        Ok(())
    }

    fn event(
        &self,
        event_type: EventType,
        object_id: Option<String>,
        payload: JsonMap,
    ) -> anyhow::Result<()> {
        if !self.provider_seen.swap(true, Ordering::SeqCst) {
            self.journal.checkpoint(
                self.target_id,
                self.run_id,
                self.lease_epoch,
                Checkpoint::ProviderAccepted,
                RunState::Running,
                &JsonMap::new(),
            )?;
        }
        self.journal.append_event(
            self.target_id,
            self.run_id,
            self.lease_epoch,
            event_type,
            object_id,
            payload,
        )?;
        Ok(())
    }
}

fn terminal_failure(
    journal: &Journal,
    target_id: Uuid,
    run_id: Uuid,
    lease_epoch: u32,
    state: RunState,
    message: &str,
) -> anyhow::Result<()> {
    let mut payload = JsonMap::new();
    payload.insert("state".to_owned(), serde_json::to_value(state)?);
    payload.insert("message".to_owned(), Value::String(message.to_owned()));
    journal.append_event(
        target_id,
        run_id,
        lease_epoch,
        EventType::Terminal,
        None,
        payload,
    )?;
    journal.checkpoint(
        target_id,
        run_id,
        lease_epoch,
        Checkpoint::Terminal,
        state,
        &JsonMap::new(),
    )?;
    Ok(())
}

fn scratch_directory(paths: &HostPaths, target_id: Uuid, run_id: Uuid) -> PathBuf {
    paths
        .root
        .join("scratch")
        .join(target_id.to_string())
        .join(run_id.to_string())
}

fn snapshot_revision(snapshot: &HarnessSnapshot) -> String {
    let value = serde_json::json!({
        "adapter_version": snapshot.adapter_version,
        "upstream_version": snapshot.upstream_version,
        "config_options": snapshot.config_options,
        "capabilities": snapshot.capabilities,
    });
    hex::encode(Sha256::digest(
        serde_json::to_vec(&value).expect("snapshot revision serialization"),
    ))
}

fn capabilities_from_acp(value: &Value) -> HarnessCapabilities {
    HarnessCapabilities {
        load_session: value.get("loadSession") == Some(&Value::Bool(true)),
        resume_session: value.pointer("/sessionCapabilities/resume").is_some(),
        close_session: value.pointer("/sessionCapabilities/close").is_some(),
        images: value.pointer("/promptCapabilities/image") == Some(&Value::Bool(true)),
        plans: true,
        usage: true,
        // Session loading/resume can support cross-turn continuity, but ACP
        // does not provide a durable fence proving an in-flight prompt is safe
        // to replay after a crash.
        durable_session_recovery: false,
    }
}

fn redact_error(value: &str) -> String {
    let mut redacted = value.to_owned();
    for marker in ["Bearer ", "authorization", "token="] {
        if let Some(index) = redacted
            .to_ascii_lowercase()
            .find(&marker.to_ascii_lowercase())
        {
            redacted.truncate(index);
            redacted.push_str("[redacted]");
        }
    }
    redacted.chars().take(2048).collect()
}

#[cfg(test)]
mod capability_tests {
    use super::capabilities_from_acp;

    #[test]
    fn maps_structured_acp_capabilities_without_string_heuristics() {
        let capabilities = capabilities_from_acp(&serde_json::json!({
            "loadSession": true,
            "promptCapabilities": {"image": true},
            "sessionCapabilities": {"resume": {}, "close": {}},
        }));
        assert!(capabilities.load_session);
        assert!(capabilities.resume_session);
        assert!(capabilities.close_session);
        assert!(capabilities.images);
        assert!(!capabilities.durable_session_recovery);
    }
}
