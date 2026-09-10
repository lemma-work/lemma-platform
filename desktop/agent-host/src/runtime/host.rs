//! The host runtime: one worker per target, and the tasks around them.

use super::{
    AcpDriver, AdapterManifest, AdapterWarmup, AgentDriver, ApiError, Arc, DISK_SCAN_INTERVAL,
    Duration, HARNESS_REFRESH_INTERVAL, HashMap, HostConfig, HostPaths, JOURNAL_CLEANUP_INTERVAL,
    JoinHandle, Journal, PathBuf, Semaphore, TRANSIENT_RETRY_INTERVAL, TargetWorker, Utc, Uuid,
    watch,
};

/// A supervisor owns its tasks even when its future is cancelled or errors.
/// Dropping Tokio's bare handle would detach the task and its subprocesses.
pub(crate) struct OwnedTask<T>(pub(crate) JoinHandle<T>);

impl<T> OwnedTask<T> {
    pub(crate) fn is_finished(&self) -> bool {
        self.0.is_finished()
    }

    pub(crate) fn abort(&self) {
        self.0.abort();
    }

    pub(crate) async fn join(mut self) -> Result<T, tokio::task::JoinError> {
        (&mut self.0).await
    }
}

impl<T> Drop for OwnedTask<T> {
    fn drop(&mut self) {
        self.0.abort();
    }
}

/// The last fingerprint of the agents installed on this machine.
///
/// Detection, separated from probing. Resolving four commands is a handful of
/// `stat` calls against directories already being searched, cheap enough to ask
/// every `DISK_SCAN_INTERVAL`; only a *change* pays for spawning agents.
#[derive(Default)]
pub(crate) struct InstalledAgents {
    pub(crate) fingerprint: String,
}

impl InstalledAgents {
    /// Record a fresh sweep, and answer whether it is worth re-probing for.
    ///
    /// The first sweep establishes the baseline and answers `false`: every
    /// worker probes when it starts, so announcing here as well would spawn
    /// every agent twice for one event.
    pub(crate) fn note(&mut self, fingerprint: String) -> bool {
        if fingerprint == self.fingerprint {
            return false;
        }
        let baseline = self.fingerprint.is_empty();
        self.fingerprint = fingerprint;
        !baseline
    }
}

/// How soon to re-probe after a round that failed for something transient.
///
/// Separate from the loop so the decision can be tested without one. The two
/// failures it stands between want opposite things: a probe that lost a race
/// with the adapter install wants another go almost immediately, while a machine
/// slow enough to lose that race every time must not be re-probed forever. So it
/// doubles, and the ceiling is the sweep the host already had — the worst case
/// is exactly the old behaviour.
pub(crate) struct TransientBackoff {
    pub(crate) next: Duration,
}

impl TransientBackoff {
    pub(crate) fn new() -> Self {
        Self {
            next: TRANSIENT_RETRY_INTERVAL,
        }
    }

    /// How long to wait before trying again, or `None` to leave the schedule be.
    pub(crate) fn note(&mut self, retry_soon: bool) -> Option<Duration> {
        if !retry_soon {
            self.next = TRANSIENT_RETRY_INTERVAL;
            return None;
        }
        let delay = self.next;
        self.next = (self.next * 2).min(HARNESS_REFRESH_INTERVAL);
        Some(delay)
    }
}

pub struct HostRuntime {
    pub(crate) config: HostConfig,
    pub(crate) paths: HostPaths,
    pub(crate) journal: Journal,
    pub(crate) manifest: AdapterManifest,
    pub(crate) driver: Arc<dyn AgentDriver>,
    pub(crate) mcp_bridge_executable: PathBuf,
}

pub(crate) async fn shutdown_signal() -> std::io::Result<()> {
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

    pub fn start_adapter_installation(&self) -> std::io::Result<AdapterWarmup> {
        self.manifest
            .start_cache_warmup(self.paths.adapters.clone())
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
            HashMap::<Uuid, (watch::Sender<bool>, OwnedTask<anyhow::Result<()>>)>::new();
        // What the scan falls back to when the file on disk stops parsing, and
        // whether that has already been said once. See the scan loop below.
        let mut last_good_config = self.config.clone();
        let mut config_unreadable = false;
        let mut scan = tokio::time::interval(DISK_SCAN_INTERVAL);
        scan.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
        // One sweep for the process, announced to every worker.
        //
        // Each worker used to run its own, so a machine paired to two workspaces
        // resolved four commands through `PATH` and stat'd them twice over, every
        // two seconds, to answer one question about one disk. The answer is a
        // property of the machine, not of a pairing.
        let (agents_changed, agents_changed_rx) = watch::channel(0_u64);
        let mut installed_fingerprint = InstalledAgents::default();
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
                        // Retention is housekeeping. Letting a transient
                        // journal error out of this loop ends `serve`, and
                        // locald restarts the host straight back into it.
                        match self.journal.cleanup_retained(Utc::now()) {
                            Ok(deleted) if deleted > 0 => tracing::info!(
                                deleted,
                                "cleaned retained Agent Host journal records"
                            ),
                            Ok(_) => {}
                            Err(error) => tracing::warn!(
                                %error,
                                "could not clean retained Agent Host journal records"
                            ),
                        }
                        cleanup_due =
                            std::time::Instant::now() + JOURNAL_CLEANUP_INTERVAL;
                    }
                    if installed_fingerprint.note(self.manifest.installed_fingerprint()) {
                        tracing::info!("agents on this computer changed; re-probing");
                        agents_changed.send_modify(|generation| *generation += 1);
                    }
                    // A config this scan cannot read is not a reason to stop
                    // serving the targets already running. Exiting here ended
                    // `serve`, and locald restarted the host into the same
                    // unreadable file -- so one bad edit took the Agent Host
                    // away until somebody found and fixed it by hand, with the
                    // reason only in a log.
                    let current = match HostConfig::load_or_create(&self.paths)
                        .and_then(|config| config.validate().map(|()| config))
                    {
                        Ok(config) => {
                            config_unreadable = false;
                            last_good_config = config.clone();
                            config
                        }
                        Err(error) => {
                            if !config_unreadable {
                                config_unreadable = true;
                                tracing::error!(
                                    %error,
                                    "the Agent Host configuration could not be read; \
                                     continuing with the last good one"
                                );
                            }
                            last_good_config.clone()
                        }
                    };
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
                            match handle.join().await {
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
                            agents_changed_rx.clone(),
                        )?;
                        targets.insert(target_id, (
                            shutdown_tx,
                            OwnedTask(tokio::spawn(async move { worker.run().await })),
                        ));
                    }
                }
            }
        }
        for (shutdown, _) in targets.values() {
            let _ = shutdown.send(true);
        }
        for (target_id, (_, handle)) in targets {
            if let Ok(Err(error)) = handle.join().await {
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
    pub(crate) fn disable_target(paths: &HostPaths, target_id: Uuid) -> anyhow::Result<()> {
        HostConfig::mutate(paths, |config| {
            let mut changed = false;
            for target in &mut config.targets {
                if target.target_id == target_id && target.enabled {
                    target.enabled = false;
                    changed = true;
                }
            }
            Ok(changed)
        })?;
        Ok(())
    }
}
