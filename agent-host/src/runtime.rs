//! Multi-target Agent Host supervisor.

use std::collections::{BTreeMap, HashMap, HashSet};
use std::path::PathBuf;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

use agent_client_protocol::schema::v1::{EnvVariable, McpServer, McpServerStdio};
use base64::Engine;
use base64::engine::general_purpose::STANDARD;
use chrono::Utc;
use serde_json::Value;
use sha2::{Digest, Sha256};
use tokio::sync::{OwnedSemaphorePermit, Semaphore, mpsc, watch};
use tokio::task::JoinHandle;
use uuid::Uuid;

use crate::acp::{AcpCallbacks, AcpDriver, AcpRunRequest, AgentDriver};
use crate::adapters::{AdapterManifest, ResolvedAdapter};
use crate::api::{ApiError, PublishedHarness, TargetClient};
use crate::config::{HostConfig, HostPaths, TargetConfig};
use crate::journal::{AcceptOutcome, Checkpoint, Journal};
use crate::permissions::{PermissionDecision, PermissionGate};
use crate::protocol::{
    Command, CommandKind, CommandRejection, EventType, HarnessCapabilities, HarnessHealth,
    HarnessSnapshot, HostCapacity, HostStatus, JsonMap, PollResponse, RejectionCode, RunCheckpoint,
    RunSpec, RunState,
};

const HARNESS_REFRESH_INTERVAL: Duration = Duration::from_secs(15 * 60);
const JOURNAL_CLEANUP_INTERVAL: Duration = Duration::from_secs(24 * 60 * 60);
const RETRY_MIN: Duration = Duration::from_millis(500);
const RETRY_MAX: Duration = Duration::from_secs(30);
const SHUTDOWN_GRACE: Duration = Duration::from_secs(30);
// How long a native permission request waits for a human before it is denied.
// Long enough for someone to actually see and answer the prompt; bounded so a
// forgotten one cannot pin an adapter open for the run's whole deadline.
const PERMISSION_DECISION_TIMEOUT: Duration = Duration::from_secs(30 * 60);
// How many times one run's event batch may be rejected outright before the
// host stops trying to deliver that run's transcript. The first rejection is
// answered with a full replay, so this allows exactly one repair attempt.
const MAX_EVENT_REJECTIONS: u32 = 2;
// How many extra requests one poll may spend bisecting a control batch the
// server refuses. Comfortably above the ceiling for the 256 updates a poll can
// carry, so the bound only ever bites on a batch that is refused wholesale.
const MAX_CONTROL_PROBES: u32 = 12;
const GENERATED_ARTIFACT_DIRECTORY: &str = ".lemma-artifacts";
const MAX_GENERATED_IMAGE_BYTES: u64 = 5 * 1024 * 1024;
const MAX_GENERATED_IMAGES: usize = 10;

pub struct HostRuntime {
    config: HostConfig,
    paths: HostPaths,
    journal: Journal,
    manifest: AdapterManifest,
    driver: Arc<dyn AgentDriver>,
    mcp_bridge_executable: PathBuf,
}

impl HostRuntime {
    pub fn new(config: HostConfig, paths: HostPaths) -> anyhow::Result<Self> {
        config.validate()?;
        let journal = Journal::open(&paths.journal)?;
        let manifest = AdapterManifest::builtin()?.with_cache_root(paths.adapters.clone());
        Ok(Self {
            config,
            paths,
            journal,
            manifest,
            driver: Arc::new(AcpDriver),
            mcp_bridge_executable: std::env::current_exe()?,
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
                                Ok(Err(error)) => {
                                    // A rejected credential never recovers by
                                    // retrying: the workspace has revoked this
                                    // host, or its row is gone. Restarting the
                                    // worker just re-authenticates and fails
                                    // again, forever, once per scan. Turn the
                                    // target off so the loop ends and the
                                    // machine reports itself disconnected;
                                    // pairing again re-enables it.
                                    if error
                                        .downcast_ref::<ApiError>()
                                        .is_some_and(ApiError::is_unauthorized)
                                    {
                                        tracing::warn!(
                                            %target_id,
                                            %error,
                                            "Lemma rejected this target's credential; disabling it until it is paired again"
                                        );
                                        Self::disable_target(&self.paths, target_id)?;
                                    } else {
                                        tracing::warn!(
                                            %target_id,
                                            %error,
                                            "target worker stopped; it will be restarted if still enabled"
                                        );
                                    }
                                }
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
                            self.paths.clone(),
                            self.journal.clone(),
                            self.manifest.clone(),
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

    /// Persist `enabled = false` for one target.
    ///
    /// Re-read and re-written rather than flipped in memory: the supervisor
    /// reloads the config on every scan, so an in-memory flag would be undone
    /// on the next tick and the failing worker would start again.
    fn disable_target(paths: &HostPaths, target_id: Uuid) -> anyhow::Result<()> {
        let mut config = HostConfig::load_or_create(paths)?;
        let mut changed = false;
        for target in &mut config.targets {
            if target.target_id == target_id && target.enabled {
                target.enabled = false;
                changed = true;
            }
        }
        if changed {
            config.save(paths)?;
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

/// The control updates one poll carries up: command acknowledgements, run
/// checkpoints, and command rejections.
///
/// They travel together on the request that is also the host's capacity
/// heartbeat and the only way commands come back down, which is why a batch the
/// server refuses has to be narrowed rather than retried whole.
#[derive(Clone, Debug, Default)]
struct ControlBatch {
    command_ids: Vec<Uuid>,
    checkpoints: Vec<RunCheckpoint>,
    rejections: Vec<CommandRejection>,
}

impl ControlBatch {
    fn len(&self) -> usize {
        self.command_ids.len() + self.checkpoints.len() + self.rejections.len()
    }

    fn is_empty(&self) -> bool {
        self.len() == 0
    }

    /// The updates in `start..end`, reading the three kinds as one sequence:
    /// acknowledgements, then checkpoints, then rejections.
    ///
    /// Addressing the batch by range is what lets a refusal be bisected down to
    /// the single update the server objects to.
    fn slice(&self, start: usize, end: usize) -> Self {
        let commands = self.command_ids.len();
        let checkpoints = self.checkpoints.len();
        let bound =
            |index: usize, offset: usize, length: usize| index.saturating_sub(offset).min(length);
        Self {
            command_ids: self.command_ids[bound(start, 0, commands)..bound(end, 0, commands)]
                .to_vec(),
            checkpoints: self.checkpoints
                [bound(start, commands, checkpoints)..bound(end, commands, checkpoints)]
                .to_vec(),
            rejections: self.rejections[bound(start, commands + checkpoints, self.rejections.len())
                ..bound(end, commands + checkpoints, self.rejections.len())]
                .to_vec(),
        }
    }

    fn absorb(&mut self, other: Self) {
        self.command_ids.extend(other.command_ids);
        self.checkpoints.extend(other.checkpoints);
        self.rejections.extend(other.rejections);
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
    permissions: PermissionGate,
    /// Runs whose event batches Lemma has rejected, and how often. Kept per
    /// run so one unhappy run cannot stop the target's poll loop.
    event_rejections: HashMap<Uuid, u32>,
    /// Runs whose liveness checkpoints Lemma refuses. Their leases are left to
    /// the server's own expiry recovery; their terminal states are not given up
    /// on, so a run is never abandoned mid-flight by this.
    refused_heartbeats: HashSet<Uuid>,
    draining: bool,
    refresh_due: std::time::Instant,
    /// Enriched harnesses from probes that ran off the poll loop's critical
    /// path. Drained each iteration so a slow probe never delays a heartbeat.
    probed: (
        mpsc::UnboundedSender<Vec<PublishedHarness>>,
        mpsc::UnboundedReceiver<Vec<PublishedHarness>>,
    ),
}

impl TargetWorker {
    #[allow(clippy::too_many_arguments)]
    fn new(
        target: TargetConfig,
        installation_id: String,
        paths: HostPaths,
        journal: Journal,
        manifest: AdapterManifest,
        driver: Arc<dyn AgentDriver>,
        mcp_bridge_executable: PathBuf,
        global_capacity: Arc<Semaphore>,
        max_runs: u16,
        shutdown: watch::Receiver<bool>,
    ) -> anyhow::Result<Self> {
        let client = TargetClient::new(target.clone(), installation_id)?;
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
            permissions: PermissionGate::new(),
            event_rejections: HashMap::new(),
            refused_heartbeats: HashSet::new(),
            draining,
            refresh_due: std::time::Instant::now(),
            probed: mpsc::unbounded_channel(),
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
            while let Ok(published) = self.probed.1.try_recv() {
                self.store_published(published);
            }
            if self.refresh_due <= std::time::Instant::now() {
                self.refresh_harnesses();
                self.refresh_due = std::time::Instant::now() + HARNESS_REFRESH_INTERVAL;
            }
            if let Err(error) = self.flush_events().await {
                self.note_offline(&error.to_string())?;
                self.wait_retry(retry).await;
                retry = (retry * 2).min(RETRY_MAX);
                continue;
            }
            let available =
                u16::try_from(self.global_capacity.available_permits()).unwrap_or(u16::MAX);
            let active = self.max_runs.saturating_sub(available);
            let capacity = HostCapacity {
                max_runs: self.max_runs,
                active_runs: active,
                available_runs: if self.draining { 0 } else { available },
            };
            match self.poll_target(capacity).await {
                Ok(response) => {
                    retry = RETRY_MIN;
                    self.journal
                        .update_target_state(self.target.target_id, "ONLINE", None)?;
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
                    if error
                        .downcast_ref::<ApiError>()
                        .is_some_and(ApiError::is_unauthorized)
                    {
                        self.cancel_all(
                            "Lemma rejected this Agent Host; the target may have been revoked",
                        )?;
                        return Err(error);
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
        match command.kind {
            CommandKind::StartRun => self.handle_start(command),
            CommandKind::CancelRun => self.handle_cancel(command),
            CommandKind::ResolvePermission => self.handle_resolve_permission(command),
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
        let journal = self.journal.clone();
        let driver = Arc::clone(&self.driver);
        let mcp_bridge_executable = self.mcp_bridge_executable.clone();
        let paths = self.paths.clone();
        let permissions = self.permissions.clone();
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
                RunState::Accepted,
                &JsonMap::new(),
            )?;
            if !spec.mcp.is_object() {
                terminal_failure(
                    &journal,
                    target_id,
                    run_id,
                    lease_epoch,
                    RunState::Failed,
                    "the start command did not carry a run-scoped MCP configuration",
                )?;
                return Ok(());
            }
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
                        "--run-id".to_owned(),
                        run_id.to_string(),
                    ])
                    .env(vec![EnvVariable::new("LEMMA_AGENT_HOST_BRIDGE", "1")]),
            );
            let callbacks: Arc<dyn AcpCallbacks> = Arc::new(JournalCallbacks {
                journal: journal.clone(),
                target_id,
                run_id,
                lease_epoch,
                provider_seen: AtomicBool::new(false),
                stream_segments: std::sync::Mutex::new(StreamSegments::default()),
            });
            let remaining = (spec.run_deadline - Utc::now())
                .to_std()
                .unwrap_or(Duration::ZERO);
            let request = AcpRunRequest {
                adapter,
                run_spec: spec,
                scratch_directory: scratch.clone(),
                mcp_server: Some(mcp_server),
                permissions: permissions.clone(),
                permission_timeout: PERMISSION_DECISION_TIMEOUT,
            };
            let outcome =
                tokio::time::timeout(remaining, driver.run(request, Arc::clone(&callbacks))).await;
            if matches!(outcome, Ok(Ok(_)))
                && let Err(error) = publish_generated_images(&scratch, callbacks.as_ref())
            {
                tracing::warn!(
                    %run_id,
                    %error,
                    "could not publish a generated image artifact"
                );
            }
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
        // Abort, but keep the handle: `abort` only requests cancellation, and
        // the task can still finish its current poll -- which is long enough to
        // park a permission request. `reap_finished` abandons the run again
        // once the task is provably gone, which is what closes that window.
        if let Some(handle) = self.active_runs.get(&run_id) {
            handle.abort();
        }
        // Anything the run left parked is now unanswerable.
        self.permissions.abandon_run(run_id);
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

    fn handle_resolve_permission(&mut self, command: &Command) -> anyhow::Result<()> {
        self.journal
            .record_simple_command(self.target.target_id, command)?;
        let run_id = command
            .run_id
            .ok_or_else(|| anyhow::anyhow!("permission decision has no run ID"))?;
        let request_id = command
            .payload
            .get("request_id")
            .and_then(Value::as_str)
            .ok_or_else(|| anyhow::anyhow!("permission decision has no request ID"))?;
        let decision = match command.payload.get("option_id").and_then(Value::as_str) {
            Some(option_id) => PermissionDecision::Allow {
                option_id: option_id.to_owned(),
            },
            None => PermissionDecision::Deny,
        };
        // A decision for a request that already timed out, or for a run that
        // ended, has nothing waiting for it. That is expected, not an error.
        if !self.permissions.resolve(run_id, request_id, decision) {
            tracing::debug!(%run_id, request_id, "no permission request was waiting");
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

    /// Publish what this machine can run, cheaply first and fully second.
    ///
    /// This is the first thing the worker loop does, and until it returns the
    /// host has not polled once - so it has no heartbeat, the workspace reports
    /// it OFFLINE, and creating a profile against it is refused. Probing is
    /// what makes that slow: every probe spawns the agent, runs an ACP
    /// `initialize` and `session/new`, and waits up to 20s. Serially, over four
    /// adapters with one that times out, a cold start took 47s to first
    /// heartbeat, measured.
    ///
    /// So the cheap half - which adapters exist and resolve - is published on
    /// its own first, and the probes then run concurrently rather than one
    /// after another. The machine appears with its agents almost immediately;
    /// their config options arrive a moment later.
    fn refresh_harnesses(&mut self) {
        let client = self.client.clone();
        let sender = self.probed.0.clone();
        // Everything the spawned work needs, taken before the task is built:
        // it outlives this borrow of `self`.
        let manifest = self.manifest.clone();
        let driver = Arc::clone(&self.driver);
        let probe_root = self.paths.root.join("probe");
        let discover_manifest = manifest.clone();
        let discover_client = client.clone();
        let discover_sender = sender.clone();
        let build_probes = move |discovered: Vec<HarnessSnapshot>| {
            discovered.into_iter().map(move |mut snapshot| {
                let manifest = manifest.clone();
                let driver = Arc::clone(&driver);
                let scratch = probe_root.join(&snapshot.harness_key);
                async move {
                    if snapshot.health != HarnessHealth::Ready {
                        return snapshot;
                    }
                    let Ok(adapter) = manifest.resolve(&snapshot.harness_key) else {
                        return snapshot;
                    };
                    match tokio::time::timeout(
                        Duration::from_secs(20),
                        driver.probe(adapter, scratch),
                    )
                    .await
                    {
                        Ok(Ok(probe)) => {
                            snapshot.config_options = probe.config_options;
                            snapshot.capabilities = capabilities_from_acp(&probe.capabilities);
                            snapshot.config_revision = snapshot_revision(&snapshot);
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
                    snapshot
                }
            })
        };
        // Spawned, never awaited. Discovery runs each adapter's binary just to
        // read its version, and probing then opens a whole ACP session per
        // adapter. Doing either before the first poll left the host with no
        // heartbeat for 47 seconds, so the workspace called a working machine
        // OFFLINE and refused to bind a profile to it.
        tokio::spawn(async move {
            let discovered = discover_manifest.discover();
            match discover_client.publish_harnesses(discovered.clone()).await {
                Ok(published) => {
                    let _ = discover_sender.send(published);
                }
                Err(error) => {
                    tracing::warn!(%error, "publishing discovered harnesses failed");
                    return;
                }
            }
            let enriched = futures_util::future::join_all(build_probes(discovered)).await;
            match client.publish_harnesses(enriched).await {
                Ok(published) => {
                    let _ = sender.send(published);
                }
                Err(error) => tracing::warn!(%error, "publishing probed harnesses failed"),
            }
        });
    }

    fn store_published(&mut self, published: Vec<PublishedHarness>) {
        self.harnesses = published
            .into_iter()
            .map(|harness| (harness.id, harness))
            .collect();
    }

    /// The control updates due for delivery, minus the ones Lemma refuses.
    ///
    /// A refused run keeps its *terminal* checkpoint in the batch: giving up on
    /// a run's last word is the one thing that would leave it unresolved.
    fn control_batch(&self) -> anyhow::Result<ControlBatch> {
        let (command_ids, mut checkpoints, rejections) =
            self.journal.pending_control(self.target.target_id)?;
        checkpoints.retain(|checkpoint| {
            checkpoint.state.is_terminal() || !self.refused_heartbeats.contains(&checkpoint.run_id)
        });
        Ok(ControlBatch {
            command_ids,
            checkpoints,
            rejections,
        })
    }

    async fn poll_control(
        &self,
        capacity: &HostCapacity,
        batch: &ControlBatch,
    ) -> Result<PollResponse, ApiError> {
        self.client
            .poll(
                capacity.clone(),
                batch.command_ids.clone(),
                batch.checkpoints.clone(),
                batch.rejections.clone(),
            )
            .await
    }

    /// Poll Lemma, bisecting the control batch around anything it refuses.
    ///
    /// One poll carries the whole host's control updates, and that same request
    /// is the capacity heartbeat and the only way commands -- cancellations
    /// included -- come back down. Failing it outright over a single update the
    /// server objects to would take the entire host offline until it restarts,
    /// with every run's lease expiring underneath it while its provider carried
    /// on working. So a refusal is bisected instead: probing halves of the
    /// undelivered range narrows to the one update at fault in a logarithmic
    /// number of requests, delivers everything either side of it, and ends at a
    /// poll the server accepts. The heartbeat can no longer be held hostage by
    /// anything the host is trying to report.
    ///
    /// A probe only ever covers updates that have not been accepted yet, so
    /// nothing is delivered twice, and every response's commands are carried
    /// through rather than discarded with the attempt that produced them.
    async fn poll_target(&mut self, capacity: HostCapacity) -> anyhow::Result<PollResponse> {
        let batch = self.control_batch()?;
        let total = batch.len();
        // Everything below this index has been accepted, or given up on.
        let mut delivered = 0;
        let mut settled = ControlBatch::default();
        let mut commands = Vec::new();
        let mut budget = MAX_CONTROL_PROBES;

        let mut response = loop {
            let attempt = batch.slice(delivered, total);
            match self.poll_control(&capacity, &attempt).await {
                Ok(response) => {
                    settled.absorb(attempt);
                    break response;
                }
                // An empty batch carries nothing to blame, and a failure that
                // is not a rejection is about the target, not its payload.
                Err(error) if attempt.is_empty() || !error.is_request_rejected() => {
                    return Err(error.into());
                }
                Err(error) => {
                    if budget == 0 {
                        // Out of probes. Land the heartbeat with an empty batch
                        // and leave the rest pending for the next cycle.
                        delivered = total;
                        continue;
                    }
                    // Invariant: `low..high` holds at least one refused update.
                    let (mut low, mut high) = (delivered, total);
                    while high - low > 1 && budget > 0 {
                        budget -= 1;
                        let middle = low + (high - low) / 2;
                        let probe = batch.slice(low, middle);
                        match self.poll_control(&capacity, &probe).await {
                            Ok(mut accepted) => {
                                commands.append(&mut accepted.commands);
                                settled.absorb(probe);
                                low = middle;
                            }
                            Err(refusal) if refusal.is_request_rejected() => high = middle,
                            Err(refusal) => return Err(refusal.into()),
                        }
                    }
                    if high - low == 1 {
                        settled.absorb(self.refuse_control(&batch.slice(low, high), &error));
                    }
                    delivered = high;
                }
            }
        };

        self.journal.mark_control_applied(
            self.target.target_id,
            &settled.command_ids,
            &settled.checkpoints,
            &settled.rejections,
        )?;
        commands.append(&mut response.commands);
        response.commands = commands;
        Ok(response)
    }

    /// Decide what to do with the single control update Lemma refused, and
    /// return whatever the host is done trying to deliver.
    ///
    /// Only a run's *liveness* checkpoint is given up on. Its lease then
    /// expires and the server's own recovery resolves the run, which is the
    /// path built for a host that stops reporting. Everything else -- a run's
    /// terminal state above all, but also command acknowledgements and command
    /// rejections -- carries information the server cannot reconstruct, so it
    /// stays pending and is retried on every later cycle. Retrying costs a
    /// bounded handful of extra requests per poll and blocks nothing, because
    /// the narrowing lets the rest of the batch through regardless.
    fn refuse_control(&mut self, batch: &ControlBatch, error: &ApiError) -> ControlBatch {
        let detail = redact_error(&error.to_string());
        let mut settled = ControlBatch::default();
        for checkpoint in &batch.checkpoints {
            if checkpoint.state.is_terminal() {
                tracing::error!(
                    run_id = %checkpoint.run_id,
                    state = ?checkpoint.state,
                    error = %detail,
                    "Lemma refused this run's final state; it stays queued for retry"
                );
                continue;
            }
            self.refused_heartbeats.insert(checkpoint.run_id);
            settled.checkpoints.push(checkpoint.clone());
            tracing::error!(
                run_id = %checkpoint.run_id,
                state = ?checkpoint.state,
                error = %detail,
                "Lemma refused this run's heartbeat; leaving its lease to server-side recovery"
            );
        }
        for command_id in &batch.command_ids {
            tracing::error!(
                %command_id,
                error = %detail,
                "Lemma refused this command acknowledgement; it stays queued for retry"
            );
        }
        for rejection in &batch.rejections {
            tracing::error!(
                command_id = %rejection.command_id,
                error = %detail,
                "Lemma refused this command rejection; it stays queued for retry"
            );
        }
        settled
    }

    /// Hand journaled events to Lemma, keeping each run's failures to itself.
    ///
    /// Only a target-level failure -- unreachable, unauthenticated, throttled,
    /// server fault -- is returned as an error. A batch Lemma rejects on its own
    /// merits belongs to exactly one run, so it is contained there: the caller
    /// must still poll, because the poll is what heartbeats the lease of every
    /// *other* run on this host.
    async fn flush_events(&mut self) -> anyhow::Result<()> {
        let target_id = self.target.target_id;
        // Runs whose remaining batches this pass must leave alone, because the
        // journal no longer matches the batches read at the top of the loop.
        let mut stale = HashSet::new();
        for batch in self.journal.pending_events(target_id, 1024)? {
            let Some(first) = batch.events.first() else {
                continue;
            };
            let (run_id, lease_epoch) = (first.run_id, first.lease_epoch);
            if stale.contains(&run_id) {
                continue;
            }
            if self.event_rejections.get(&run_id).copied().unwrap_or(0) >= MAX_EVENT_REJECTIONS {
                let dropped = self
                    .journal
                    .discard_events(target_id, run_id, lease_epoch)?;
                stale.insert(run_id);
                tracing::warn!(
                    %run_id,
                    dropped,
                    "dropping events Lemma will not accept for this run"
                );
                continue;
            }
            match self.client.append_events(&batch).await {
                Ok(ack) => {
                    self.event_rejections.remove(&run_id);
                    self.journal.acknowledge_events(target_id, &ack)?;
                }
                Err(error) if error.is_request_rejected() => {
                    stale.insert(run_id);
                    self.reject_run_events(run_id, lease_epoch, &error)?;
                }
                Err(error) => return Err(error.into()),
            }
        }
        Ok(())
    }

    /// Contain a run whose event batch Lemma refused.
    ///
    /// The first refusal is treated as a lost server-side stream, which is the
    /// only recoverable cause: the stream is transient and its watermark drops
    /// to zero when it is evicted, so it starts expecting sequence 1 again from
    /// a run that is far past it. Replaying the run's retained events answers
    /// exactly that, and is safe against every other cause because the server
    /// keeps the first write for a sequence it already holds.
    fn reject_run_events(
        &mut self,
        run_id: Uuid,
        lease_epoch: u32,
        error: &crate::api::ApiError,
    ) -> anyhow::Result<()> {
        let rejections = self.event_rejections.entry(run_id).or_default();
        *rejections += 1;
        if *rejections >= MAX_EVENT_REJECTIONS {
            tracing::error!(
                %run_id,
                error = %redact_error(&error.to_string()),
                "Lemma rejected this run's events after a replay; giving up on its transcript"
            );
            return Ok(());
        }
        let replayed =
            self.journal
                .rewind_acknowledgements(self.target.target_id, run_id, lease_epoch)?;
        tracing::warn!(
            %run_id,
            replayed,
            error = %redact_error(&error.to_string()),
            "Lemma rejected this run's events; replaying its journaled history"
        );
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
                && !error.is_cancelled()
            {
                tracing::error!(%run_id, %error, "agent run task terminated unexpectedly");
            }
            // The task is provably gone, so nothing can answer a request it
            // left parked -- including one it parked while being aborted.
            self.permissions.abandon_run(run_id);
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
            let capacity = HostCapacity {
                max_runs: self.max_runs,
                active_runs: self.max_runs.saturating_sub(
                    u16::try_from(self.global_capacity.available_permits()).unwrap_or(u16::MAX),
                ),
                available_runs: 0,
            };
            if let Ok(response) = self.poll_target(capacity).await {
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
            self.permissions.abandon_run(run_id);
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
    stream_segments: std::sync::Mutex<StreamSegments>,
}

/// Accumulated per-kind streamed text awaiting a full-text upsert.
///
/// Chunks are the cosmetic live lane (the server publishes them without
/// journaling); the upserts synthesized here are the durable, authoritative
/// text records. A segment is sealed and emitted before any event that is not
/// a text chunk of the same kind, so replaying only durable events rebuilds
/// the exact final text.
#[derive(Default)]
struct StreamSegments {
    message: String,
    thought: String,
}

impl JournalCallbacks {
    fn flush_stream_segment(&self, message: bool) -> anyhow::Result<()> {
        let text = {
            let mut segments = self
                .stream_segments
                .lock()
                .expect("stream segments poisoned");
            let segment = if message {
                &mut segments.message
            } else {
                &mut segments.thought
            };
            std::mem::take(segment)
        };
        if text.is_empty() {
            return Ok(());
        }
        let mut payload = JsonMap::new();
        payload.insert("text".to_owned(), Value::String(text));
        self.journal.append_event(
            self.target_id,
            self.run_id,
            self.lease_epoch,
            if message {
                EventType::AgentMessageUpsert
            } else {
                EventType::AgentThoughtUpsert
            },
            None,
            payload,
        )?;
        Ok(())
    }

    fn flush_stream_segments(&self) -> anyhow::Result<()> {
        self.flush_stream_segment(true)?;
        self.flush_stream_segment(false)
    }
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
                RunState::Running,
                &JsonMap::new(),
            )?;
        }
        match event_type {
            EventType::AgentMessageChunk | EventType::AgentThoughtChunk => {
                let is_message = event_type == EventType::AgentMessageChunk;
                let text = chunk_text(&payload);
                if text.is_empty() && !payload.is_empty() {
                    // Rich content (e.g. an image block) is durable and seals
                    // the current text segment ahead of itself.
                    self.flush_stream_segment(is_message)?;
                } else {
                    let mut segments = self
                        .stream_segments
                        .lock()
                        .expect("stream segments poisoned");
                    let segment = if is_message {
                        &mut segments.message
                    } else {
                        &mut segments.thought
                    };
                    segment.push_str(&text);
                }
                self.journal.append_event(
                    self.target_id,
                    self.run_id,
                    self.lease_epoch,
                    event_type,
                    object_id,
                    payload,
                )?;
            }
            _ => {
                self.flush_stream_segments()?;
                self.journal.append_event(
                    self.target_id,
                    self.run_id,
                    self.lease_epoch,
                    event_type,
                    object_id,
                    payload,
                )?;
            }
        }
        Ok(())
    }
}

/// Extract streamed text the same way the backend normalizer does.
fn chunk_text(payload: &JsonMap) -> String {
    for key in ["text", "delta"] {
        if let Some(text) = payload.get(key).and_then(Value::as_str) {
            return text.to_owned();
        }
    }
    match payload.get("content") {
        Some(Value::String(text)) => text.clone(),
        Some(Value::Object(content)) => content
            .get("text")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_owned(),
        _ => String::new(),
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
    journal.checkpoint(target_id, run_id, lease_epoch, state, &JsonMap::new())?;
    Ok(())
}

fn scratch_directory(paths: &HostPaths, target_id: Uuid, run_id: Uuid) -> PathBuf {
    paths
        .root
        .join("scratch")
        .join(target_id.to_string())
        .join(run_id.to_string())
}

fn publish_generated_images(
    scratch_directory: &std::path::Path,
    callbacks: &dyn AcpCallbacks,
) -> anyhow::Result<()> {
    for (object_id, payload) in generated_image_payloads(scratch_directory)? {
        callbacks.event(EventType::AgentMessageChunk, Some(object_id), payload)?;
    }
    Ok(())
}

fn generated_image_payloads(
    scratch_directory: &std::path::Path,
) -> anyhow::Result<Vec<(String, JsonMap)>> {
    let directory = scratch_directory.join(GENERATED_ARTIFACT_DIRECTORY);
    let Ok(entries) = std::fs::read_dir(&directory) else {
        return Ok(Vec::new());
    };
    let mut paths = entries
        .filter_map(Result::ok)
        .map(|entry| entry.path())
        .collect::<Vec<_>>();
    paths.sort();

    let mut payloads = Vec::new();
    for path in paths {
        if payloads.len() >= MAX_GENERATED_IMAGES {
            break;
        }
        let metadata = std::fs::symlink_metadata(&path)?;
        if !metadata.file_type().is_file() || metadata.len() > MAX_GENERATED_IMAGE_BYTES {
            continue;
        }
        let Some(mime_type) = generated_image_mime_type(&path) else {
            continue;
        };
        let bytes = std::fs::read(&path)?;
        if !generated_image_signature_matches(&bytes, mime_type) {
            continue;
        }
        let filename = path
            .file_name()
            .and_then(|value| value.to_str())
            .unwrap_or("generated-image")
            .to_owned();
        let mut payload = JsonMap::new();
        payload.insert(
            "content".to_owned(),
            serde_json::json!({
                "type": "image",
                "data": STANDARD.encode(bytes),
                "mimeType": mime_type,
            }),
        );
        payload.insert("filename".to_owned(), Value::String(filename));
        payloads.push((format!("generated-image-{}", payloads.len() + 1), payload));
    }
    Ok(payloads)
}

fn generated_image_mime_type(path: &std::path::Path) -> Option<&'static str> {
    match path.extension()?.to_str()?.to_ascii_lowercase().as_str() {
        "png" => Some("image/png"),
        "jpg" | "jpeg" => Some("image/jpeg"),
        "gif" => Some("image/gif"),
        "webp" => Some("image/webp"),
        "avif" => Some("image/avif"),
        _ => None,
    }
}

fn generated_image_signature_matches(bytes: &[u8], mime_type: &str) -> bool {
    match mime_type {
        "image/png" => bytes.starts_with(b"\x89PNG\r\n\x1a\n"),
        "image/jpeg" => bytes.starts_with(b"\xff\xd8\xff"),
        "image/gif" => bytes.starts_with(b"GIF87a") || bytes.starts_with(b"GIF89a"),
        "image/webp" => bytes.len() >= 12 && bytes.starts_with(b"RIFF") && &bytes[8..12] == b"WEBP",
        "image/avif" => {
            bytes.len() >= 16
                && &bytes[4..8] == b"ftyp"
                && (bytes[8..32.min(bytes.len())]
                    .windows(4)
                    .any(|brand| brand == b"avif" || brand == b"avis"))
        }
        _ => false,
    }
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
mod target_worker_tests {
    use std::collections::HashMap;
    use std::path::PathBuf;
    use std::sync::{Arc, Mutex};
    use std::time::Duration;

    use axum::extract::State;
    use axum::http::StatusCode;
    use axum::routing::post;
    use axum::{Json, Router};
    use chrono::Utc;
    use tokio::net::TcpListener;
    use tokio::sync::{Semaphore, watch};
    use uuid::Uuid;

    use super::TargetWorker;
    use crate::acp::{AcpCallbacks, AcpProbeOutcome, AcpRunOutcome, AcpRunRequest, AgentDriver};
    use crate::adapters::{AdapterManifest, ResolvedAdapter};
    use crate::config::{HostPaths, TargetConfig};
    use crate::journal::Journal;
    use crate::permissions::PermissionDecision;
    use crate::protocol::{
        Command, CommandKind, EventAck, EventBatch, EventType, HostCapacity, HostStatus, JsonMap,
        PollRequest, PollResponse, RunSpec, RunState,
    };

    fn capacity() -> HostCapacity {
        HostCapacity {
            max_runs: 2,
            active_runs: 0,
            available_runs: 2,
        }
    }

    /// A driver that is never asked to do anything; the worker needs one to
    /// exist, not to run.
    struct IdleDriver;

    #[async_trait::async_trait]
    impl AgentDriver for IdleDriver {
        async fn probe(
            &self,
            _adapter: ResolvedAdapter,
            _scratch_directory: PathBuf,
        ) -> anyhow::Result<AcpProbeOutcome> {
            anyhow::bail!("the flush tests never probe")
        }

        async fn run(
            &self,
            _request: AcpRunRequest,
            _callbacks: Arc<dyn AcpCallbacks>,
        ) -> anyhow::Result<AcpRunOutcome> {
            anyhow::bail!("the flush tests never run an agent")
        }
    }

    #[derive(Default)]
    struct StubState {
        /// Runs whose batches the stub refuses, as Lemma does when its
        /// transient event stream no longer holds the sequences a batch
        /// assumes.
        refused: Mutex<Vec<Uuid>>,
        accepted: Mutex<Vec<(Uuid, u64)>>,
        /// Runs whose checkpoints the stub refuses, standing in for any reason
        /// a future server might reject one update out of a poll's batch.
        refused_checkpoints: Mutex<Vec<Uuid>>,
        applied_checkpoints: Mutex<Vec<(Uuid, RunState)>>,
        polls: Mutex<u32>,
        /// Commands handed out one per poll, to prove none are lost while a
        /// refused control batch is being narrowed.
        undelivered_commands: Mutex<Vec<Command>>,
    }

    async fn poll(
        State(state): State<Arc<StubState>>,
        Json(request): Json<PollRequest>,
    ) -> Result<Json<PollResponse>, StatusCode> {
        *state.polls.lock().unwrap() += 1;
        let refused = state.refused_checkpoints.lock().unwrap().clone();
        if request
            .checkpoints
            .iter()
            .any(|checkpoint| refused.contains(&checkpoint.run_id))
        {
            return Err(StatusCode::CONFLICT);
        }
        state.applied_checkpoints.lock().unwrap().extend(
            request
                .checkpoints
                .iter()
                .map(|checkpoint| (checkpoint.run_id, checkpoint.state)),
        );
        let command = state.undelivered_commands.lock().unwrap().pop();
        Ok(Json(PollResponse {
            protocol_version: crate::PROTOCOL_VERSION,
            host_status: HostStatus::Online,
            commands: command.into_iter().collect(),
            poll_after_ms: 0,
        }))
    }

    async fn append_events(
        State(state): State<Arc<StubState>>,
        Json(batch): Json<EventBatch>,
    ) -> Result<Json<EventAck>, StatusCode> {
        let first = batch.events.first().expect("batches are never empty");
        if state.refused.lock().unwrap().contains(&first.run_id) {
            // The same 409 the backend raises for `event sequence gap`.
            return Err(StatusCode::CONFLICT);
        }
        let last = batch.events.last().expect("batches are never empty");
        state
            .accepted
            .lock()
            .unwrap()
            .push((first.run_id, last.sequence));
        Ok(Json(EventAck {
            run_id: first.run_id,
            lease_epoch: first.lease_epoch,
            acked_through: last.sequence,
        }))
    }

    struct Harness {
        worker: TargetWorker,
        stub: Arc<StubState>,
        journal: Journal,
        target_id: Uuid,
        _directory: tempfile::TempDir,
        _shutdown: watch::Sender<bool>,
        server: tokio::task::JoinHandle<()>,
    }

    impl Harness {
        async fn new() -> Self {
            let stub = Arc::<StubState>::default();
            let app = Router::new()
                .route("/agent-host/events:append", post(append_events))
                .route("/agent-host/poll", post(poll))
                .with_state(Arc::clone(&stub));
            let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
            let port = listener.local_addr().unwrap().port();
            let server = tokio::spawn(async move {
                axum::serve(listener, app).await.unwrap();
            });

            let directory = tempfile::TempDir::new().unwrap();
            let paths = HostPaths::under(directory.path());
            paths.ensure().unwrap();
            let journal = Journal::open(&paths.journal).unwrap();
            let target_id = Uuid::new_v4();
            let target = TargetConfig {
                target_id,
                name: "stub".into(),
                base_url: url::Url::parse(&format!("http://127.0.0.1:{port}")).unwrap(),
                host_id: Uuid::new_v4(),
                user_id: Uuid::new_v4(),
                host_secret: "test-secret".into(),
                enabled: true,
                allow_insecure_http: true,
                draining: false,
                refresh_generation: 0,
            };
            let (shutdown_tx, shutdown_rx) = watch::channel(false);
            let worker = TargetWorker::new(
                target,
                "installation".into(),
                paths,
                journal.clone(),
                AdapterManifest::builtin().unwrap(),
                Arc::new(IdleDriver),
                PathBuf::from("/nonexistent-bridge"),
                Arc::new(Semaphore::new(2)),
                2,
                shutdown_rx,
            )
            .unwrap();
            Self {
                worker,
                stub,
                journal,
                target_id,
                _directory: directory,
                _shutdown: shutdown_tx,
                server,
            }
        }

        /// Journal a run with `count` events, as a live run would.
        fn seed_run(&self, count: u64) -> Uuid {
            let run_id = Uuid::new_v4();
            let spec = RunSpec {
                agent_run_id: run_id,
                conversation_id: Uuid::new_v4(),
                harness_id: Uuid::new_v4(),
                profile_revision: "revision".into(),
                model_name: None,
                config_selections: JsonMap::new(),
                system_prompt: String::new(),
                prompt: vec![serde_json::json!({"type": "text", "text": "hi"})],
                context: JsonMap::new(),
                mcp: serde_json::json!({}),
                run_deadline: Utc::now() + chrono::Duration::minutes(5),
            };
            let command = Command {
                command_id: Uuid::new_v4(),
                kind: CommandKind::StartRun,
                created_at: Utc::now(),
                expires_at: Utc::now() + chrono::Duration::minutes(1),
                run_id: Some(run_id),
                lease_epoch: Some(1),
                payload: serde_json::to_value(&spec).unwrap(),
            };
            self.journal
                .accept_start(self.target_id, &command, &spec, "codex", "1.0")
                .unwrap();
            for _ in 0..count {
                self.journal
                    .append_event(
                        self.target_id,
                        run_id,
                        1,
                        EventType::AgentMessageChunk,
                        None,
                        JsonMap::new(),
                    )
                    .unwrap();
            }
            run_id
        }

        fn accepted(&self) -> HashMap<Uuid, u64> {
            let mut highest = HashMap::new();
            for (run_id, sequence) in self.stub.accepted.lock().unwrap().iter() {
                let entry = highest.entry(*run_id).or_insert(0);
                *entry = (*entry).max(*sequence);
            }
            highest
        }

        fn pending(&self, run_id: Uuid) -> Vec<u64> {
            self.journal
                .pending_events(self.target_id, 1024)
                .unwrap()
                .into_iter()
                .flat_map(|batch| batch.events)
                .filter(|event| event.run_id == run_id)
                .map(|event| event.sequence)
                .collect()
        }
    }

    impl Drop for Harness {
        fn drop(&mut self) {
            self.server.abort();
        }
    }

    /// The finding: one run Lemma refuses used to abort the whole flush and
    /// make the caller skip its poll, which is the lease heartbeat for every
    /// other run on the host.
    #[tokio::test]
    async fn a_refused_run_neither_stops_the_flush_nor_fails_it() {
        let mut harness = Harness::new().await;
        let poisoned = harness.seed_run(3);
        let healthy = harness.seed_run(2);
        harness.stub.refused.lock().unwrap().push(poisoned);

        harness
            .worker
            .flush_events()
            .await
            .expect("a run Lemma refuses is not a target-level failure");

        assert_eq!(
            harness.accepted().get(&healthy),
            Some(&2),
            "the healthy run's events must still reach Lemma"
        );
        assert!(harness.pending(healthy).is_empty());
    }

    /// A refusal is answered by replaying the run's journaled history, which is
    /// what an emptied server-side stream needs to see.
    #[tokio::test]
    async fn a_refusal_replays_the_run_from_its_first_event() {
        let mut harness = Harness::new().await;
        let run_id = harness.seed_run(3);
        harness.worker.flush_events().await.unwrap();
        assert_eq!(harness.accepted().get(&run_id), Some(&3));
        assert!(harness.pending(run_id).is_empty());

        // Lemma loses the stream: it now refuses a batch that starts above the
        // sequence it expects.
        harness.stub.refused.lock().unwrap().push(run_id);
        harness
            .journal
            .append_event(
                harness.target_id,
                run_id,
                1,
                EventType::AgentMessageChunk,
                None,
                JsonMap::new(),
            )
            .unwrap();

        harness.worker.flush_events().await.unwrap();

        assert_eq!(
            harness.pending(run_id),
            vec![1, 2, 3, 4],
            "the acknowledged events have to survive locally to be replayable"
        );
    }

    /// A run Lemma keeps refusing is given up on rather than left to block the
    /// flush loop, and its terminal checkpoint stops being held hostage.
    #[tokio::test]
    async fn a_run_lemma_keeps_refusing_is_eventually_dropped() {
        let mut harness = Harness::new().await;
        let poisoned = harness.seed_run(3);
        let healthy = harness.seed_run(1);
        harness.stub.refused.lock().unwrap().push(poisoned);

        for _ in 0..3 {
            harness.worker.flush_events().await.unwrap();
        }

        assert!(
            harness.pending(poisoned).is_empty(),
            "the undeliverable run must stop being retried forever"
        );
        assert_eq!(harness.accepted().get(&healthy), Some(&1));
    }

    /// An unreachable or unauthenticated target is not one run's problem, so it
    /// still surfaces as a failure that puts the worker into its retry path.
    #[tokio::test]
    async fn a_target_level_failure_still_fails_the_flush() {
        let mut harness = Harness::new().await;
        harness.seed_run(1);
        harness.server.abort();
        // Let the listener actually close before the flush tries to use it.
        tokio::time::sleep(Duration::from_millis(50)).await;

        assert!(harness.worker.flush_events().await.is_err());
    }

    /// The poll is the lease heartbeat for every run on the host and the only
    /// way commands come back down, and it carries every run's checkpoint in
    /// one batch. One refused checkpoint used to fail that whole request, which
    /// the worker read as the target being offline -- so every other run's
    /// lease expired underneath it while its provider kept working.
    #[tokio::test]
    async fn a_refused_checkpoint_does_not_hold_back_every_other_run() {
        let mut harness = Harness::new().await;
        let healthy = harness.seed_run(0);
        let poisoned = harness.seed_run(0);
        harness
            .stub
            .refused_checkpoints
            .lock()
            .unwrap()
            .push(poisoned);

        let response = harness
            .worker
            .poll_target(capacity())
            .await
            .expect("one refused checkpoint is not the target going offline");

        assert_eq!(response.host_status, HostStatus::Online);
        let applied = harness.stub.applied_checkpoints.lock().unwrap().clone();
        assert!(
            applied.iter().any(|(run_id, _)| *run_id == healthy),
            "the healthy run's checkpoint must still be applied, got {applied:?}"
        );
        assert!(applied.iter().all(|(run_id, _)| *run_id != poisoned));
        assert!(
            harness.worker.refused_heartbeats.contains(&poisoned),
            "the refused run must be named, not left to poison every later poll"
        );
    }

    /// Narrowing must not lose commands: a cancellation handed back by one of
    /// the probing requests still has to reach the caller.
    #[tokio::test]
    async fn commands_survive_a_narrowed_poll() {
        let mut harness = Harness::new().await;
        harness.seed_run(0);
        let poisoned = harness.seed_run(0);
        harness
            .stub
            .refused_checkpoints
            .lock()
            .unwrap()
            .push(poisoned);
        let cancel = Command {
            command_id: Uuid::new_v4(),
            kind: CommandKind::CancelRun,
            created_at: Utc::now(),
            expires_at: Utc::now() + chrono::Duration::minutes(1),
            run_id: Some(Uuid::new_v4()),
            lease_epoch: Some(1),
            payload: serde_json::Value::Null,
        };
        harness
            .stub
            .undelivered_commands
            .lock()
            .unwrap()
            .push(cancel.clone());

        let response = harness.worker.poll_target(capacity()).await.unwrap();

        assert_eq!(
            response
                .commands
                .iter()
                .map(|command| command.command_id)
                .collect::<Vec<_>>(),
            vec![cancel.command_id],
            "a command delivered by a probing request must not be dropped with it"
        );
    }

    /// Once the offender is named, later polls carry the batch in one request
    /// again -- the narrowing is a repair, not a permanent tax.
    #[tokio::test]
    async fn a_named_offender_stops_costing_extra_requests() {
        let mut harness = Harness::new().await;
        harness.seed_run(0);
        let poisoned = harness.seed_run(0);
        harness
            .stub
            .refused_checkpoints
            .lock()
            .unwrap()
            .push(poisoned);

        harness.worker.poll_target(capacity()).await.unwrap();
        let after_repair = *harness.stub.polls.lock().unwrap();
        harness.worker.poll_target(capacity()).await.unwrap();

        assert_eq!(
            *harness.stub.polls.lock().unwrap() - after_repair,
            1,
            "a later poll must cost exactly one request"
        );
    }

    /// The bisect addresses three lists as one sequence, so the index
    /// arithmetic is load-bearing: an off-by-one would drop a control update.
    #[test]
    fn slicing_reads_the_three_control_lists_as_one_sequence() {
        let runs = [Uuid::new_v4(), Uuid::new_v4()];
        let batch = super::ControlBatch {
            command_ids: vec![Uuid::new_v4(), Uuid::new_v4()],
            checkpoints: runs
                .iter()
                .map(|run_id| crate::protocol::RunCheckpoint {
                    run_id: *run_id,
                    lease_epoch: 1,
                    state: RunState::Running,
                    detail: JsonMap::new(),
                })
                .collect(),
            rejections: vec![crate::protocol::CommandRejection {
                command_id: Uuid::new_v4(),
                run_id: Uuid::new_v4(),
                lease_epoch: 1,
                code: crate::protocol::RejectionCode::InvalidCommand,
                retryable: false,
                detail: None,
            }],
        };
        assert_eq!(batch.len(), 5);

        // Every single-element window addresses exactly one update, and the
        // windows tile the batch without gaps or repeats.
        let windows = (0..batch.len())
            .map(|index| batch.slice(index, index + 1))
            .collect::<Vec<_>>();
        assert!(windows.iter().all(|window| window.len() == 1));
        let mut rebuilt = super::ControlBatch::default();
        for window in windows {
            rebuilt.absorb(window);
        }
        assert_eq!(rebuilt.command_ids, batch.command_ids);
        assert_eq!(rebuilt.checkpoints.len(), 2);
        assert_eq!(rebuilt.rejections.len(), 1);

        // A window straddling two kinds carries the tail of one and the head
        // of the next.
        let straddle = batch.slice(1, 4);
        assert_eq!(straddle.command_ids, vec![batch.command_ids[1]]);
        assert_eq!(straddle.checkpoints.len(), 2);
        assert!(straddle.rejections.is_empty());

        assert!(batch.slice(0, 0).is_empty());
        assert_eq!(batch.slice(0, batch.len()).len(), batch.len());
    }

    /// A target that refuses even an empty poll is genuinely unreachable, and
    /// must still put the worker into its offline retry path.
    #[tokio::test]
    async fn a_target_that_refuses_everything_still_fails_the_poll() {
        let mut harness = Harness::new().await;
        harness.server.abort();
        tokio::time::sleep(Duration::from_millis(50)).await;

        assert!(harness.worker.poll_target(capacity()).await.is_err());
    }

    /// The run task parks a permission request and is then dropped -- exactly
    /// what `spawn_run`'s deadline `timeout` does to an ACP handler that is
    /// waiting on a decision. `wait`'s own cleanup never runs in that case, so
    /// the worker has to sweep the run when it reaps the task.
    #[tokio::test]
    async fn a_run_that_ends_without_being_cancelled_leaves_nothing_parked() {
        let mut harness = Harness::new().await;
        let run_id = harness.seed_run(0);
        let gate = harness.worker.permissions.clone();
        let handle = tokio::spawn(async move {
            let _ = tokio::time::timeout(
                Duration::from_millis(20),
                gate.wait(run_id, "call-1".to_owned(), Duration::from_secs(600)),
            )
            .await;
            Ok(())
        });
        harness.worker.active_runs.insert(run_id, handle);
        while !harness.worker.active_runs[&run_id].is_finished() {
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
        assert_eq!(
            harness.worker.permissions.parked(),
            1,
            "the dropped handler is expected to leave its request behind"
        );

        harness.worker.reap_finished().await;

        assert_eq!(harness.worker.permissions.parked(), 0);
    }

    /// `abort` only requests cancellation, so the run task can still park one
    /// more request after `handle_cancel` has swept the gate. Keeping the
    /// handle until it is reaped is what closes that window.
    #[tokio::test]
    async fn cancelling_a_run_sweeps_a_request_parked_on_the_way_out() {
        let mut harness = Harness::new().await;
        let run_id = harness.seed_run(0);
        let handle = tokio::spawn(async move {
            std::future::pending::<()>().await;
            Ok(())
        });
        harness.worker.active_runs.insert(run_id, handle);

        let cancel = Command {
            command_id: Uuid::new_v4(),
            kind: CommandKind::CancelRun,
            created_at: Utc::now(),
            expires_at: Utc::now() + chrono::Duration::minutes(1),
            run_id: Some(run_id),
            lease_epoch: Some(1),
            payload: serde_json::Value::Null,
        };
        harness.worker.handle_cancel(&cancel).unwrap();

        // The aborted task gets one more poll before the runtime drops it,
        // which is long enough to register a request nobody will answer.
        let gate = harness.worker.permissions.clone();
        let late = tokio::spawn(async move {
            gate.wait(run_id, "late".to_owned(), Duration::from_secs(600))
                .await
        });
        while harness.worker.permissions.parked() == 0 {
            tokio::time::sleep(Duration::from_millis(5)).await;
        }

        for _ in 0..100 {
            harness.worker.reap_finished().await;
            if harness.worker.permissions.parked() == 0 {
                break;
            }
            tokio::time::sleep(Duration::from_millis(5)).await;
        }

        let decision = tokio::time::timeout(Duration::from_secs(5), late)
            .await
            .expect("a request parked during cancellation must not wait for its timeout")
            .unwrap();
        assert_eq!(decision, PermissionDecision::Deny);
    }
}

#[cfg(test)]
mod stream_upsert_tests {
    use std::sync::Arc;
    use std::sync::atomic::AtomicBool;

    use super::{JournalCallbacks, StreamSegments, chunk_text};
    use crate::acp::AcpCallbacks;
    use crate::journal::Journal;
    use crate::protocol::{Command, CommandKind, EventType, JsonMap, RunSpec};
    use chrono::Utc;
    use serde_json::Value;
    use tempfile::TempDir;
    use uuid::Uuid;

    fn payload(text: &str) -> JsonMap {
        let mut payload = JsonMap::new();
        payload.insert("text".to_owned(), Value::String(text.to_owned()));
        payload
    }

    fn fixture() -> (TempDir, JournalCallbacks, Uuid) {
        let directory = TempDir::new().unwrap();
        let journal = Journal::open(directory.path().join("journal.db")).unwrap();
        let target_id = Uuid::new_v4();
        let run_id = Uuid::new_v4();
        let spec = RunSpec {
            agent_run_id: run_id,
            conversation_id: Uuid::new_v4(),
            harness_id: Uuid::new_v4(),
            profile_revision: "revision".into(),
            model_name: None,
            config_selections: JsonMap::new(),
            system_prompt: String::new(),
            prompt: vec![serde_json::json!({"type": "text", "text": "hi"})],
            context: JsonMap::new(),
            mcp: Value::Null,
            run_deadline: Utc::now() + chrono::Duration::minutes(5),
        };
        let command = Command {
            command_id: Uuid::new_v4(),
            kind: CommandKind::StartRun,
            created_at: Utc::now(),
            expires_at: Utc::now() + chrono::Duration::minutes(1),
            run_id: Some(run_id),
            lease_epoch: Some(1),
            payload: serde_json::to_value(&spec).unwrap(),
        };
        journal
            .accept_start(target_id, &command, &spec, "codex", "1.0")
            .unwrap();
        let callbacks = JournalCallbacks {
            journal,
            target_id,
            run_id,
            lease_epoch: 1,
            provider_seen: AtomicBool::new(true),
            stream_segments: std::sync::Mutex::new(StreamSegments::default()),
        };
        (directory, callbacks, run_id)
    }

    fn journaled_events(callbacks: &JournalCallbacks) -> Vec<(u64, EventType, JsonMap)> {
        callbacks
            .journal
            .pending_events(callbacks.target_id, 256)
            .unwrap()
            .into_iter()
            .flat_map(|batch| batch.events)
            .map(|event| (event.sequence, event.event_type, event.payload))
            .collect()
    }

    #[test]
    fn text_chunks_flush_as_one_upsert_before_the_next_durable_event() {
        let (_directory, callbacks, _run_id) = fixture();
        let callbacks = Arc::new(callbacks);
        callbacks
            .event(EventType::AgentMessageChunk, None, payload("hello "))
            .unwrap();
        callbacks
            .event(EventType::AgentMessageChunk, None, payload("world"))
            .unwrap();
        // Only chunks are journaled so far; no upsert yet.
        let events = journaled_events(&callbacks);
        assert_eq!(
            events.iter().map(|(_, kind, _)| *kind).collect::<Vec<_>>(),
            vec![EventType::AgentMessageChunk, EventType::AgentMessageChunk]
        );

        callbacks
            .event(
                EventType::ToolCallUpsert,
                Some("call-1".into()),
                JsonMap::new(),
            )
            .unwrap();
        let events = journaled_events(&callbacks);
        let kinds = events.iter().map(|(_, kind, _)| *kind).collect::<Vec<_>>();
        assert_eq!(
            kinds,
            vec![
                EventType::AgentMessageChunk,
                EventType::AgentMessageChunk,
                EventType::AgentMessageUpsert,
                EventType::ToolCallUpsert,
            ]
        );
        assert_eq!(
            events[2].2.get("text"),
            Some(&Value::String("hello world".into()))
        );

        // A durable-only replay (skipping chunks) still yields the full text.
        let durable_text = events
            .iter()
            .filter(|(_, kind, _)| *kind == EventType::AgentMessageUpsert)
            .filter_map(|(_, _, payload)| payload.get("text").and_then(Value::as_str))
            .collect::<String>();
        assert_eq!(durable_text, "hello world");
    }

    #[test]
    fn rich_content_seals_the_segment_without_touching_it() {
        let (_directory, callbacks, _run_id) = fixture();
        callbacks
            .event(EventType::AgentMessageChunk, None, payload("before "))
            .unwrap();
        let mut image = JsonMap::new();
        image.insert(
            "content".to_owned(),
            serde_json::json!({"type": "image", "data": "...", "mimeType": "image/png"}),
        );
        callbacks
            .event(EventType::AgentMessageChunk, None, image)
            .unwrap();
        callbacks
            .event(EventType::AgentMessageChunk, None, payload("after"))
            .unwrap();
        callbacks
            .event(EventType::Terminal, None, JsonMap::new())
            .unwrap();

        let upserts = journaled_events(&callbacks)
            .into_iter()
            .filter(|(_, kind, _)| *kind == EventType::AgentMessageUpsert)
            .map(|(_, _, payload)| payload)
            .collect::<Vec<_>>();
        assert_eq!(
            upserts
                .iter()
                .filter_map(|payload| payload.get("text").and_then(Value::as_str))
                .collect::<Vec<_>>(),
            vec!["before ", "after"]
        );
    }

    #[test]
    fn thought_and_message_segments_flush_independently() {
        let (_directory, callbacks, _run_id) = fixture();
        callbacks
            .event(EventType::AgentThoughtChunk, None, payload("thinking"))
            .unwrap();
        callbacks
            .event(EventType::AgentMessageChunk, None, payload("answer"))
            .unwrap();
        callbacks
            .event(EventType::Terminal, None, JsonMap::new())
            .unwrap();
        let events = journaled_events(&callbacks);
        let kinds = events.iter().map(|(_, kind, _)| *kind).collect::<Vec<_>>();
        assert_eq!(
            kinds,
            vec![
                EventType::AgentThoughtChunk,
                EventType::AgentMessageChunk,
                EventType::AgentMessageUpsert,
                EventType::AgentThoughtUpsert,
                EventType::Terminal,
            ]
        );
    }

    #[test]
    fn chunk_text_mirrors_the_backend_extraction() {
        assert_eq!(chunk_text(&payload("plain")), "plain");
        let mut content = JsonMap::new();
        content.insert(
            "content".to_owned(),
            serde_json::json!({"type": "text", "text": "block"}),
        );
        assert_eq!(chunk_text(&content), "block");
        let mut image = JsonMap::new();
        image.insert(
            "content".to_owned(),
            serde_json::json!({"type": "image", "data": "..."}),
        );
        assert_eq!(chunk_text(&image), "");
    }
}

#[cfg(test)]
mod capability_tests {
    use base64::Engine;
    use base64::engine::general_purpose::STANDARD;
    use tempfile::tempdir;

    use super::{GENERATED_ARTIFACT_DIRECTORY, capabilities_from_acp, generated_image_payloads};

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

    #[test]
    fn generated_images_are_bounded_to_the_explicit_artifact_directory() {
        let root = tempdir().unwrap();
        let artifacts = root.path().join(GENERATED_ARTIFACT_DIRECTORY);
        std::fs::create_dir_all(&artifacts).unwrap();
        let png = b"\x89PNG\r\n\x1a\nimage";
        std::fs::write(artifacts.join("poster.png"), png).unwrap();
        std::fs::write(root.path().join("private.png"), png).unwrap();
        std::fs::write(artifacts.join("notes.txt"), b"not an image").unwrap();

        let payloads = generated_image_payloads(root.path()).unwrap();

        assert_eq!(payloads.len(), 1);
        assert_eq!(payloads[0].0, "generated-image-1");
        assert_eq!(payloads[0].1["filename"], "poster.png");
        let content = payloads[0].1["content"].as_object().unwrap();
        assert_eq!(content["mimeType"], "image/png");
        assert_eq!(
            STANDARD.decode(content["data"].as_str().unwrap()).unwrap(),
            png
        );
    }
}
