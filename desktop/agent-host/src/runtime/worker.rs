//! One target's worker: its poll loop, and the state it carries.

use super::{
    AdapterManifest, AgentDriver, ApiError, Arc, AtomicBool, BTreeMap, CommandRejection,
    ConfigOption, Duration, EventFlusher, HARNESS_REFRESH_INTERVAL, HARNESS_RETRY_INTERVAL,
    HarnessCapabilities, HashMap, HostCapacity, HostConfig, HostPaths, HostStatus, Journal,
    LOCAL_CONTROL_INTERVAL, Ordering, OwnedTask, PathBuf, PermissionGate, PublishedHarness,
    RETRY_MAX, RETRY_MIN, REVOKED_REFUSALS, RunCheckpoint, Semaphore, TargetClient, TargetConfig,
    TransientBackoff, Uuid, command_rejection, deliver_events, mpsc, redact_error, watch,
};

/// The control updates one poll carries up: command acknowledgements, run
/// checkpoints, and command rejections.
///
/// They travel together on the request that is also the host's capacity
/// heartbeat and the only way commands come back down, which is why a batch the
/// server refuses has to be narrowed rather than retried whole.
#[derive(Clone, Debug, Default)]
pub(crate) struct ControlBatch {
    pub(crate) command_ids: Vec<Uuid>,
    pub(crate) checkpoints: Vec<RunCheckpoint>,
    pub(crate) rejections: Vec<CommandRejection>,
}

impl ControlBatch {
    pub(crate) fn len(&self) -> usize {
        self.command_ids.len() + self.checkpoints.len() + self.rejections.len()
    }

    pub(crate) fn is_empty(&self) -> bool {
        self.len() == 0
    }

    /// The updates in `start..end`, reading the three kinds as one sequence:
    /// acknowledgements, then checkpoints, then rejections.
    ///
    /// Addressing the batch by range is what lets a refusal be bisected down to
    /// the single update the server objects to.
    pub(crate) fn slice(&self, start: usize, end: usize) -> Self {
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

    pub(crate) fn absorb(&mut self, other: Self) {
        self.command_ids.extend(other.command_ids);
        self.checkpoints.extend(other.checkpoints);
        self.rejections.extend(other.rejections);
    }
}

pub(crate) struct TargetWorker {
    pub(crate) target: TargetConfig,
    pub(crate) client: TargetClient,
    pub(crate) paths: HostPaths,
    pub(crate) journal: Journal,
    pub(crate) manifest: AdapterManifest,
    pub(crate) driver: Arc<dyn AgentDriver>,
    pub(crate) mcp_bridge_executable: PathBuf,
    pub(crate) global_capacity: Arc<Semaphore>,
    pub(crate) max_runs: u16,
    pub(crate) shutdown: watch::Receiver<bool>,
    pub(crate) harnesses: BTreeMap<Uuid, PublishedHarness>,
    /// What each probe learned, keyed by harness key. Lemma is told the same
    /// thing, but a run needs it locally and synchronously — to decide whether
    /// resuming a session is on the table, and to know which configuration
    /// this harness was published as offering.
    pub(crate) probes: HashMap<String, ProbedHarness>,
    pub(crate) active_runs: HashMap<Uuid, ActiveRun>,
    pub(crate) permissions: PermissionGate,
    /// Event delivery, shared with the task that drives it. Behind a lock so a
    /// shutdown flush and the delivery task cannot send the same batch twice.
    pub(crate) flusher: Arc<tokio::sync::Mutex<EventFlusher>>,
    /// Runs whose liveness checkpoints Lemma refuses, and how many more polls
    /// they sit out before trying again. Their leases meanwhile fall to the
    /// server's own expiry recovery; their terminal states are never given up
    /// on, so a run is not abandoned mid-flight by this.
    pub(crate) refused_heartbeats: HashMap<Uuid, u32>,
    pub(crate) draining: bool,
    pub(crate) refresh_due: std::time::Instant,
    /// When to re-read the local control file. See `LOCAL_CONTROL_INTERVAL`.
    pub(crate) controls_due: std::time::Instant,
    /// Bumped by the supervisor's sweep when the agents installed on this
    /// machine change. Awaiting it is what turns "install an agent, wait up to
    /// fifteen minutes" into "install an agent, it appears".
    pub(crate) agents_changed: watch::Receiver<u64>,
    /// Consecutive refusals saying Lemma does not know this pairing. Reset by
    /// any answered poll, so only an unbroken run of them drops the pairing.
    pub(crate) revoked_refusals: u32,
    /// Raised by a run that failed because its agent is signed out.
    ///
    /// A signed-out harness is only *discovered* by probing, and probing is on
    /// a fifteen-minute timer -- so a run failing for exactly that reason was
    /// the freshest information the host had, and it threw it away. The
    /// harness stayed READY in the workspace, the next run failed the same
    /// way, and signing in did nothing until someone restarted the host.
    pub(crate) reprobe_requested: Arc<AtomicBool>,
    /// When to re-probe after a round that failed for something transient.
    pub(crate) transient_backoff: TransientBackoff,
    pub(crate) events_ready: Arc<tokio::sync::Notify>,
    /// Enriched harnesses from probes that ran off the poll loop's critical
    /// path. Drained each iteration so a slow probe never delays a heartbeat.
    pub(crate) probed: (
        mpsc::UnboundedSender<Option<ProbedHarnesses>>,
        mpsc::UnboundedReceiver<Option<ProbedHarnesses>>,
    ),
    pub(crate) probe_task: Option<OwnedTask<()>>,
}

/// One run in flight, and the two ways it can be stopped.
///
/// `cancel` is the ACP path: the driver sends `session/cancel` and the agent
/// ends its own turn, which is what lets the provider flush the session file
/// the next turn resumes from. `kill_at` is the backstop for an adapter that
/// ignores it, and is only set once a cancellation has actually been asked for.
pub(crate) struct ActiveRun {
    pub(crate) handle: OwnedTask<anyhow::Result<()>>,
    pub(crate) cancel: watch::Sender<bool>,
    pub(crate) kill_at: Option<tokio::time::Instant>,
}

/// One completed refresh: what Lemma accepted, and what the probes learned.
pub(crate) struct ProbedHarnesses {
    pub(crate) published: Vec<PublishedHarness>,
    pub(crate) probes: HashMap<String, ProbedHarness>,
    /// Whether anything failed for a reason that may not fail twice.
    ///
    /// A probe that timed out on a busy machine is not a settled fact about the
    /// installation, but the loop had no way to know that: it pushed the next
    /// refresh a quarter of an hour out the moment this task was *spawned*, and
    /// a successful publish of a bad snapshot never brought it back. So one
    /// unlucky five-second window cost fifteen minutes of an agent being
    /// reported unusable, with no way for the user to shorten it.
    pub(crate) retry_soon: bool,
}

/// What one adapter's probe found, kept for the runs that need it in hand.
///
/// `config_options` is here because it is the *published* answer to "what does
/// this harness offer" — the one Lemma validated a profile against. A run's own
/// session can disagree with it (see `AcpRunRequest::published_config_options`),
/// and when it does, this is the version that was actually agreed.
#[derive(Clone, Default)]
pub(crate) struct ProbedHarness {
    pub(crate) capabilities: HarnessCapabilities,
    pub(crate) config_options: Vec<ConfigOption>,
}

impl TargetWorker {
    #[allow(clippy::too_many_arguments)]
    pub(crate) fn new(
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
        agents_changed: watch::Receiver<u64>,
    ) -> anyhow::Result<Self> {
        let client = TargetClient::new(target.clone(), installation_id)?;
        journal.register_target(target.target_id)?;
        let draining = target.draining;
        let flusher = Arc::new(tokio::sync::Mutex::new(EventFlusher {
            target_id: target.target_id,
            journal: journal.clone(),
            client: client.clone(),
            rejections: HashMap::new(),
        }));
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
            probes: HashMap::new(),
            active_runs: HashMap::new(),
            permissions: PermissionGate::new(),
            flusher,
            refused_heartbeats: HashMap::new(),
            draining,
            refresh_due: std::time::Instant::now(),
            controls_due: std::time::Instant::now(),
            agents_changed,
            revoked_refusals: 0,
            reprobe_requested: Arc::new(AtomicBool::new(false)),
            transient_backoff: TransientBackoff::new(),
            events_ready: Arc::new(tokio::sync::Notify::new()),
            probed: mpsc::unbounded_channel(),
            probe_task: None,
        })
    }

    pub(crate) async fn run(mut self) -> anyhow::Result<()> {
        self.recover_interrupted_runs()?;
        // Event delivery runs alongside the poll rather than competing with it.
        // Aborted at the end of this function; the shutdown path takes the same
        // lock and does the last flush itself, so nothing in the journal is left
        // behind by stopping it.
        let delivery = OwnedTask(tokio::spawn(deliver_events(
            Arc::clone(&self.flusher),
            Arc::clone(&self.events_ready),
            self.shutdown.clone(),
        )));
        let outcome = self.poll_loop().await;
        delivery.abort();
        outcome
    }

    pub(crate) async fn poll_loop(&mut self) -> anyhow::Result<()> {
        let mut retry = RETRY_MIN;
        // Cloned out of `self` once, so the select below can await it without
        // borrowing `self` twice. A `watch` receiver tracks versions rather than
        // edges, so a change that lands while the loop is busy elsewhere is
        // still waiting when it next reaches the select.
        let mut agents_changed = self.agents_changed.clone();
        loop {
            if *self.shutdown.borrow() || self.shutdown.has_changed().is_err() {
                return self.graceful_shutdown().await;
            }
            self.reap_finished().await;
            self.enforce_cancellations()?;
            if self.controls_due <= std::time::Instant::now() {
                self.controls_due = std::time::Instant::now() + LOCAL_CONTROL_INTERVAL;
                self.apply_local_controls()?;
            }
            self.drain_published();
            if self.reprobe_requested.swap(false, Ordering::SeqCst)
                || self.refresh_due <= std::time::Instant::now()
            {
                self.refresh_harnesses();
                self.refresh_due = std::time::Instant::now() + HARNESS_REFRESH_INTERVAL;
            }
            // `deliver_events` is what normally sends these, and it does so
            // without waiting for the loop to come back around. This pass is
            // the backstop and the connection check: it is the one place a
            // delivery failure is turned into OFFLINE, because two writers of
            // that state flapped it between them.
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
            // Lemma holds the poll open for `POLL_HOLD`, so this is where the
            // loop spends nearly all of its time. Anything that has to happen
            // sooner than that needs an arm here — reaching the top of the loop
            // is not something that happens on a schedule, it is something that
            // happens when the poll returns.
            //
            // Waking here abandons the poll in flight. That is cheap for the
            // host — the poll is a lease-based pull, so anything the server was
            // about to hand over comes back on the next one — but it is not free
            // for the server, which goes on holding the abandoned poll for the
            // rest of its hold. So an arm has to be worth a stranded poll.
            //
            // A run's streamed output emphatically is not: it arrives dozens of
            // times per turn, and putting it here stacked 26 concurrent polls
            // against one idle. It has its own task now — see `deliver_events`.
            // An agent being installed or removed happens approximately never.
            let polled = tokio::select! {
                result = self.poll_target(capacity) => Some(result),
                // The agents on this machine changed. This is what makes
                // `DISK_SCAN_INTERVAL` mean what it says: the scan itself was
                // always cheap, but it used to be *reached* once per iteration,
                // so noticing a newly installed agent waited out a held poll and
                // took up to `POLL_HOLD` rather than the two seconds the
                // interval reads as.
                changed = agents_changed.changed() => {
                    if changed.is_err() {
                        return self.graceful_shutdown().await;
                    }
                    self.refresh_due = std::time::Instant::now();
                    None
                }
            };
            let Some(polled) = polled else {
                continue;
            };
            match polled {
                Ok(response) => {
                    retry = RETRY_MIN;
                    // One answered poll proves the pairing is known, so any
                    // refusals before it were the transient kind.
                    self.revoked_refusals = 0;
                    self.journal
                        .update_target_state(self.target.target_id, "ONLINE", None)?;
                    if response.host_status == HostStatus::Revoked {
                        anyhow::bail!("target revoked this Agent Host");
                    }
                    if response.host_status == HostStatus::UpgradeRequired {
                        anyhow::bail!("target requires a newer Agent Host protocol");
                    }
                    if !response.commands.is_empty() {
                        // Harnesses publish off the poll path, so what this
                        // host holds can be behind what Lemma minted against
                        // in two different ways — a publish still sitting in
                        // the channel, or no publish at all yet. Both are the
                        // same mistake to make, and both are corrected here,
                        // before any command is judged against a harness.
                        self.sync_harnesses_for_commands().await;
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
                    // Lemma does not know this pairing. Stop being paired rather
                    // than retrying forever: the target task ends either way,
                    // but the supervisor respawns any target still in the config
                    // — which is how a removed computer kept polling a pairing
                    // the workspace had already destroyed, reporting
                    // "Unreachable" for as long as the app was open.
                    //
                    // Not on the first refusal, though. The backend cannot tell
                    // us whether the host was revoked or is merely missing, and
                    // "missing" includes a machine pointed at the wrong backend
                    // and a database restored behind its own writes. Dropping
                    // the pairing there costs a re-pair for a condition that
                    // heals itself. So it has to say so `REVOKED_REFUSALS`
                    // times, across a backoff that is doubling toward
                    // `RETRY_MAX` — long enough that no blip spans it, short
                    // enough that a genuine revocation is over in under a
                    // minute.
                    if error
                        .downcast_ref::<ApiError>()
                        .is_some_and(ApiError::is_revoked_or_missing)
                    {
                        self.revoked_refusals += 1;
                        if self.revoked_refusals >= REVOKED_REFUSALS {
                            tracing::warn!(
                                target = %self.target.name,
                                refusals = self.revoked_refusals,
                                "Lemma does not know this pairing; dropping it"
                            );
                            self.cancel_all("Lemma revoked this Agent Host")?;
                            self.forget_target()?;
                            return Err(error);
                        }
                        tracing::info!(
                            target = %self.target.name,
                            refusals = self.revoked_refusals,
                            "Lemma does not know this pairing; retrying before dropping it"
                        );
                        self.note_offline(&error.to_string())?;
                        self.wait_retry(retry).await;
                        retry = (retry * 2).min(RETRY_MAX);
                        continue;
                    }
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

    /// Drop this pairing from the on-disk config.
    ///
    /// Only for a refusal that cannot become valid again. Re-pairing is a fresh
    /// single-use code, which is the right bar: the machine is either signed in
    /// and welcome, in which case an authenticated page pairs it again in
    /// seconds, or it is not, in which case it should hold nothing.
    pub(crate) fn forget_target(&mut self) -> anyhow::Result<()> {
        let target_id = self.target.target_id;
        let mut dropped = false;
        HostConfig::mutate(&self.paths, |config| {
            let before = config.targets.len();
            config
                .targets
                .retain(|target| target.target_id != target_id);
            dropped = config.targets.len() != before;
            Ok(dropped)
        })?;
        if !dropped {
            return Ok(());
        }
        self.journal.update_target_state(
            self.target.target_id,
            "REVOKED",
            Some("revoked by Lemma"),
        )?;
        tracing::info!(
            target = %self.target.name,
            "dropped a revoked pairing; this computer will not poll it again"
        );
        Ok(())
    }

    pub(crate) fn apply_local_controls(&mut self) -> anyhow::Result<()> {
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

    /// Take every harness publish already waiting, without blocking.
    ///
    /// The channel is where a publish lands; `self.harnesses` only changes
    /// when something drains it. So this is not bookkeeping that can happen
    /// whenever it is convenient — until it runs, the host is judging commands
    /// against harnesses it has already replaced.
    pub(crate) fn drain_published(&mut self) {
        while let Ok(outcome) = self.probed.1.try_recv() {
            match outcome {
                Some(published) => {
                    // Read before `store_published` takes ownership.
                    let retry_soon = published.retry_soon;
                    self.store_published(published);
                    if let Some(delay) = self.transient_backoff.note(retry_soon) {
                        self.refresh_due = std::time::Instant::now() + delay;
                    }
                }
                // Come back in seconds rather than a quarter of an hour.
                None => {
                    self.refresh_due = std::time::Instant::now() + HARNESS_RETRY_INTERVAL;
                }
            }
        }
    }

    pub(crate) fn note_offline(&self, error: &str) -> anyhow::Result<()> {
        self.journal.update_target_state(
            self.target.target_id,
            "OFFLINE",
            Some(&redact_error(error)),
        )?;
        Ok(())
    }

    pub(crate) async fn wait_retry(&mut self, duration: Duration) {
        tokio::select! {
            () = tokio::time::sleep(duration) => {}
            _ = self.shutdown.changed() => {}
        }
    }

    /// Register a task as an active run, as `spawn_run` does.
    #[cfg(test)]
    pub(crate) fn track_run(
        &mut self,
        run_id: Uuid,
        handle: super::JoinHandle<anyhow::Result<()>>,
    ) {
        self.active_runs.insert(
            run_id,
            ActiveRun {
                handle: OwnedTask(handle),
                cancel: watch::channel(false).0,
                kill_at: None,
            },
        );
    }
}
