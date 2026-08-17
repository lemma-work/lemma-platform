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
    Command, CommandKind, CommandRejection, ConfigOption, EventType, HarnessCapabilities,
    HarnessHealth, HarnessSnapshot, HostCapacity, HostStatus, JsonMap, PollResponse, RejectionCode,
    RunCheckpoint, RunSpec, RunState,
};

const HARNESS_REFRESH_INTERVAL: Duration = Duration::from_secs(15 * 60);
/// How soon to try again when publishing harnesses fails.
///
/// The refresh interval is tuned for "has anything about the installed agents
/// changed", which is rarely. It is the wrong interval for a failure: the
/// backend restarts whenever its configuration changes, and a publish that
/// happened to land during one used to leave this host with nothing published
/// for the next fifteen minutes — during which every command was rejected for
/// referencing a harness it had never announced.
const HARNESS_RETRY_INTERVAL: Duration = Duration::from_secs(10);
/// How long a queued command waits for the first harness publish before it is
/// rejected.
///
/// Was 60s, when probing every adapter ran in sequence behind four five-second
/// timeouts. Probes are concurrent now, so the slowest adapter sets the floor
/// and the old number was measuring a shape that no longer exists.
const FIRST_HARNESS_WAIT: Duration = Duration::from_secs(15);
/// How often to check whether the agents installed on this machine changed.
///
/// This is the budget line for noticing a newly installed agent, and it is
/// affordable only because detection no longer means probing: it is a handful of
/// `stat` calls, not four spawned processes.
///
/// It only means anything because the poll loop has a tick of its own to wake it
/// — see `POLL_HOLD`. As a check performed once per iteration it would have been
/// a *ceiling* on frequency and nothing at all on latency, since an iteration is
/// however long the server holds the poll.
const DISK_SCAN_INTERVAL: Duration = Duration::from_secs(2);
/// How often to re-read the local control file (drain, resume, refresh).
///
/// Its own deadline because the scan tick made the loop twelve times faster, and
/// `apply_local_controls` reads and parses `config.json` on every pass. Noticing
/// a drain request within five seconds is well inside what asked for it; doing it
/// thirty times a minute is just file I/O.
const LOCAL_CONTROL_INTERVAL: Duration = Duration::from_secs(5);
/// How many consecutive `AGENT_HOST_REVOKED_OR_MISSING` refusals it takes
/// before this host drops the pairing.
///
/// More than one because the backend cannot say which of the two it means, and
/// "missing" is survivable: a host pointed at the wrong backend, a database
/// restored behind its own writes, a workspace mid-rebuild. Three, with the
/// retry backoff doubling between them, is long enough that nothing transient
/// spans it and short enough that a real revocation is over in seconds.
const REVOKED_REFUSALS: u32 = 3;
const JOURNAL_CLEANUP_INTERVAL: Duration = Duration::from_secs(24 * 60 * 60);
const RETRY_MIN: Duration = Duration::from_millis(500);
const RETRY_MAX: Duration = Duration::from_secs(30);
const SHUTDOWN_GRACE: Duration = Duration::from_secs(30);
// How long a native permission request waits for a human before it is denied.
// Long enough for someone to actually see and answer the prompt; bounded so a
// forgotten one cannot pin an adapter open for the run's whole deadline.
const PERMISSION_DECISION_TIMEOUT: Duration = Duration::from_secs(30 * 60);
// How long the agent has to honour `session/cancel` and end its turn cleanly.
const CANCEL_GRACE: Duration = Duration::from_secs(10);
// How long the supervisor waits for a signalled run to terminalize itself
// before killing its process tree. Comfortably past CANCEL_GRACE so the ACP
// path is what normally resolves a cancellation, and the kill is the backstop
// for an adapter that ignores the notification altogether.
const CANCEL_KILL_AFTER: Duration = Duration::from_secs(15);
// How many polls a run whose liveness checkpoint Lemma refused waits before
// trying again. A refusal is usually transient (a deploy, a blip); giving up on
// the heartbeat permanently sentences a healthy run to lease expiry, so the
// host backs off rather than stopping.
const REFUSED_HEARTBEAT_RETRY_POLLS: u32 = 20;
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

/// The last fingerprint of the agents installed on this machine.
///
/// Detection, separated from probing. Resolving four commands is a handful of
/// `stat` calls against directories already being searched, cheap enough to ask
/// every `DISK_SCAN_INTERVAL`; only a *change* pays for spawning agents.
#[derive(Default)]
struct InstalledAgents {
    fingerprint: String,
}

impl InstalledAgents {
    /// Record a fresh sweep, and answer whether it is worth re-probing for.
    ///
    /// The first sweep establishes the baseline and answers `false`: every
    /// worker probes when it starts, so announcing here as well would spawn
    /// every agent twice for one event.
    fn note(&mut self, fingerprint: String) -> bool {
        if fingerprint == self.fingerprint {
            return false;
        }
        let baseline = self.fingerprint.is_empty();
        self.fingerprint = fingerprint;
        !baseline
    }
}

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
                    if installed_fingerprint.note(self.manifest.installed_fingerprint()) {
                        tracing::info!("agents on this computer changed; re-probing");
                        agents_changed.send_modify(|generation| *generation += 1);
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
                            agents_changed_rx.clone(),
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
    /// What each probe learned, keyed by harness key. Lemma is told the same
    /// thing, but a run needs it locally and synchronously — to decide whether
    /// resuming a session is on the table, and to know which configuration
    /// this harness was published as offering.
    probes: HashMap<String, ProbedHarness>,
    active_runs: HashMap<Uuid, ActiveRun>,
    permissions: PermissionGate,
    /// Event delivery, shared with the task that drives it. Behind a lock so a
    /// shutdown flush and the delivery task cannot send the same batch twice.
    flusher: Arc<tokio::sync::Mutex<EventFlusher>>,
    /// Runs whose liveness checkpoints Lemma refuses, and how many more polls
    /// they sit out before trying again. Their leases meanwhile fall to the
    /// server's own expiry recovery; their terminal states are never given up
    /// on, so a run is not abandoned mid-flight by this.
    refused_heartbeats: HashMap<Uuid, u32>,
    draining: bool,
    refresh_due: std::time::Instant,
    /// When to re-read the local control file. See `LOCAL_CONTROL_INTERVAL`.
    controls_due: std::time::Instant,
    /// Bumped by the supervisor's sweep when the agents installed on this
    /// machine change. Awaiting it is what turns "install an agent, wait up to
    /// fifteen minutes" into "install an agent, it appears".
    agents_changed: watch::Receiver<u64>,
    /// Consecutive refusals saying Lemma does not know this pairing. Reset by
    /// any answered poll, so only an unbroken run of them drops the pairing.
    revoked_refusals: u32,
    /// Raised by a run that failed because its agent is signed out.
    ///
    /// A signed-out harness is only *discovered* by probing, and probing is on
    /// a fifteen-minute timer -- so a run failing for exactly that reason was
    /// the freshest information the host had, and it threw it away. The
    /// harness stayed READY in the workspace, the next run failed the same
    /// way, and signing in did nothing until someone restarted the host.
    reprobe_requested: Arc<AtomicBool>,
    events_ready: Arc<tokio::sync::Notify>,
    /// Enriched harnesses from probes that ran off the poll loop's critical
    /// path. Drained each iteration so a slow probe never delays a heartbeat.
    probed: (
        mpsc::UnboundedSender<Option<ProbedHarnesses>>,
        mpsc::UnboundedReceiver<Option<ProbedHarnesses>>,
    ),
}

/// Delivery of journaled events to Lemma, off the poll loop.
///
/// Owns everything that delivery touches — the journal, the client, and how
/// often each run's batches have been refused — so it can run as its own task
/// while the poll loop holds its poll open.
///
/// That separation is the whole point. Flushing used to be an arm of the same
/// `select!` as the poll, so every event a run streamed abandoned the poll in
/// flight and opened a new one. The server had no idea the old one was gone and
/// held it for the rest of its 25 seconds: one host streaming a single answer
/// was measured stacking 26 concurrent polls, against exactly one while idle.
struct EventFlusher {
    target_id: Uuid,
    journal: Journal,
    client: TargetClient,
    /// Runs whose event batches Lemma has rejected, and how often. Kept per
    /// run so one unhappy run cannot stop delivery for every other.
    rejections: HashMap<Uuid, u32>,
}

impl EventFlusher {
    /// Hand journaled events to Lemma, keeping each run's failures to itself.
    ///
    /// Only a target-level failure -- unreachable, unauthenticated, throttled,
    /// server fault -- is returned as an error. A batch Lemma rejects on its own
    /// merits belongs to exactly one run, so it is contained there: the poll
    /// loop must still poll, because the poll is what heartbeats the lease of
    /// every *other* run on this host.
    async fn flush(&mut self) -> anyhow::Result<()> {
        let target_id = self.target_id;
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
            if self.rejections.get(&run_id).copied().unwrap_or(0) >= MAX_EVENT_REJECTIONS {
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
                    self.rejections.remove(&run_id);
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
        let rejections = self.rejections.entry(run_id).or_default();
        *rejections += 1;
        if *rejections >= MAX_EVENT_REJECTIONS {
            tracing::error!(
                %run_id,
                error = %redact_error(&error.to_string()),
                "Lemma rejected this run's events after a replay; giving up on its transcript"
            );
            return Ok(());
        }
        let replayed = self
            .journal
            .rewind_acknowledgements(self.target_id, run_id, lease_epoch)?;
        tracing::warn!(
            %run_id,
            replayed,
            error = %redact_error(&error.to_string()),
            "Lemma rejected this run's events; replaying its journaled history"
        );
        Ok(())
    }
}

/// Deliver events as they are journaled, until the host shuts down.
///
/// A run's output reaching Lemma is not something the poll loop should be able
/// to delay, and not something that should be able to disturb the poll: this
/// waits on the same `events_ready` the run tasks raise, and takes the lock the
/// shutdown flush also takes, so the two can never be in flight together.
///
/// Failures are logged and retried rather than reported: the poll loop is the
/// one authority on whether this target is reachable, and having two writers of
/// the connection state produced an ONLINE/OFFLINE flap between them.
async fn deliver_events(
    flusher: Arc<tokio::sync::Mutex<EventFlusher>>,
    events_ready: Arc<tokio::sync::Notify>,
    mut shutdown: watch::Receiver<bool>,
) {
    let mut retry = RETRY_MIN;
    loop {
        tokio::select! {
            () = events_ready.notified() => {}
            _ = shutdown.changed() => return,
        }
        if *shutdown.borrow() {
            return;
        }
        match flusher.lock().await.flush().await {
            Ok(()) => retry = RETRY_MIN,
            Err(error) => {
                tracing::debug!(%error, "could not deliver Agent Host events; retrying");
                tokio::select! {
                    () = tokio::time::sleep(retry) => {}
                    _ = shutdown.changed() => return,
                }
                retry = (retry * 2).min(RETRY_MAX);
                // Nothing consumed the notification that brought us here, so
                // re-raise it: the events are still pending and the next pass
                // has to be woken by something.
                events_ready.notify_one();
            }
        }
    }
}

/// One run in flight, and the two ways it can be stopped.
///
/// `cancel` is the ACP path: the driver sends `session/cancel` and the agent
/// ends its own turn, which is what lets the provider flush the session file
/// the next turn resumes from. `kill_at` is the backstop for an adapter that
/// ignores it, and is only set once a cancellation has actually been asked for.
struct ActiveRun {
    handle: JoinHandle<anyhow::Result<()>>,
    cancel: watch::Sender<bool>,
    kill_at: Option<tokio::time::Instant>,
}

/// One completed refresh: what Lemma accepted, and what the probes learned.
struct ProbedHarnesses {
    published: Vec<PublishedHarness>,
    probes: HashMap<String, ProbedHarness>,
}

/// What one adapter's probe found, kept for the runs that need it in hand.
///
/// `config_options` is here because it is the *published* answer to "what does
/// this harness offer" — the one Lemma validated a profile against. A run's own
/// session can disagree with it (see `AcpRunRequest::published_config_options`),
/// and when it does, this is the version that was actually agreed.
#[derive(Clone, Default)]
struct ProbedHarness {
    capabilities: HarnessCapabilities,
    config_options: Vec<ConfigOption>,
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
            events_ready: Arc::new(tokio::sync::Notify::new()),
            probed: mpsc::unbounded_channel(),
        })
    }

    async fn run(mut self) -> anyhow::Result<()> {
        self.recover_interrupted_runs()?;
        // Event delivery runs alongside the poll rather than competing with it.
        // Aborted at the end of this function; the shutdown path takes the same
        // lock and does the last flush itself, so nothing in the journal is left
        // behind by stopping it.
        let delivery = tokio::spawn(deliver_events(
            Arc::clone(&self.flusher),
            Arc::clone(&self.events_ready),
            self.shutdown.clone(),
        ));
        let outcome = self.poll_loop().await;
        delivery.abort();
        outcome
    }

    async fn poll_loop(&mut self) -> anyhow::Result<()> {
        let mut retry = RETRY_MIN;
        // Cloned out of `self` once, so the select below can await it without
        // borrowing `self` twice. A `watch` receiver tracks versions rather than
        // edges, so a change that lands while the loop is busy elsewhere is
        // still waiting when it next reaches the select.
        let mut agents_changed = self.agents_changed.clone();
        loop {
            if *self.shutdown.borrow() {
                return self.graceful_shutdown().await;
            }
            self.reap_finished().await;
            self.enforce_cancellations()?;
            if self.controls_due <= std::time::Instant::now() {
                self.controls_due = std::time::Instant::now() + LOCAL_CONTROL_INTERVAL;
                self.apply_local_controls()?;
            }
            while let Ok(outcome) = self.probed.1.try_recv() {
                match outcome {
                    Some(published) => self.store_published(published),
                    // Come back in seconds rather than a quarter of an hour.
                    None => {
                        self.refresh_due = std::time::Instant::now() + HARNESS_RETRY_INTERVAL;
                    }
                }
            }
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
                _ = agents_changed.changed() => {
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
                        // Harnesses now publish off the poll path, so the very
                        // first commands can arrive before that publish lands.
                        // Rejecting them as HARNESS_NOT_FOUND is permanent and
                        // wrong: the machine has the agent, it just has not said
                        // so yet. Wait for the publish instead — bounded, and
                        // only when there is work that needs it.
                        self.await_first_harnesses().await;
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
    fn forget_target(&mut self) -> anyhow::Result<()> {
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

    /// Block until the first harness publish lands, at most once and bounded.
    ///
    /// Only ever called when a command is in hand: the heartbeat must never
    /// wait on discovery, which is the whole reason publishing moved off the
    /// poll path.
    async fn await_first_harnesses(&mut self) {
        if !self.harnesses.is_empty() {
            return;
        }
        // Ask, rather than only wait. Arriving here means a command needs a
        // harness we have not published, which is exactly the moment to try
        // again — waiting alone would sit out whatever remains of the retry or
        // refresh interval and then reject the command for nothing.
        self.refresh_harnesses();
        self.refresh_due = std::time::Instant::now() + HARNESS_REFRESH_INTERVAL;

        let deadline = tokio::time::Instant::now() + FIRST_HARNESS_WAIT;
        while self.harnesses.is_empty() {
            match tokio::time::timeout_at(deadline, self.probed.1.recv()).await {
                Ok(Some(Some(published))) => self.store_published(published),
                // A publish failed while we were waiting. Retry within the
                // deadline we are already holding rather than giving up on it.
                Ok(Some(None)) => {
                    self.refresh_due = std::time::Instant::now() + HARNESS_RETRY_INTERVAL;
                    tokio::time::sleep(HARNESS_RETRY_INTERVAL).await;
                    self.refresh_harnesses();
                }
                Ok(None) => return,
                Err(_) => {
                    tracing::warn!("no harnesses published yet; the command will be rejected");
                    return;
                }
            }
        }
    }

    fn handle_command(&mut self, command: &Command) -> anyhow::Result<()> {
        anyhow::ensure!(command.expires_at >= Utc::now(), "command is expired");
        match command.kind {
            CommandKind::StartRun => self.handle_start(command),
            CommandKind::CancelRun => self.handle_cancel(command),
            CommandKind::ResolvePermission => self.handle_resolve_permission(command),
            CommandKind::RefreshCredential => self.handle_refresh_credential(command),
        }
    }

    /// Take a replacement Lemma MCP credential for a run still in flight.
    ///
    /// Journaled rather than signalled: the MCP bridge is a separate process
    /// that re-reads its endpoint from the journal on every request, so writing
    /// it here *is* the delivery. Nothing needs to interrupt the run.
    fn handle_refresh_credential(&mut self, command: &Command) -> anyhow::Result<()> {
        self.journal
            .record_simple_command(self.target.target_id, command)?;
        let run_id = command
            .run_id
            .ok_or_else(|| anyhow::anyhow!("credential refresh has no run ID"))?;
        let lease_epoch = command
            .lease_epoch
            .ok_or_else(|| anyhow::anyhow!("credential refresh has no lease epoch"))?;
        let mcp = command
            .payload
            .get("mcp")
            .filter(|value| value.is_object())
            .ok_or_else(|| anyhow::anyhow!("credential refresh carries no MCP object"))?;
        if self
            .journal
            .refresh_run_mcp(self.target.target_id, run_id, lease_epoch, mcp)?
        {
            tracing::debug!(%run_id, "refreshed the run's Lemma MCP credential");
        }
        Ok(())
    }

    fn handle_start(&mut self, command: &Command) -> anyhow::Result<()> {
        anyhow::ensure!(!self.draining, "Agent Host is draining");
        let spec: RunSpec = serde_json::from_value(command.payload.clone())?;
        let published = self
            .harnesses
            .get(&spec.harness_id)
            .cloned()
            .ok_or_else(|| anyhow::anyhow!("command references an unknown harness"))?;
        if published.config_revision != spec.profile_revision {
            // Both revisions, because the question a reader has is always "how
            // far behind was the command?", and one hash alone cannot answer
            // it. Lemma re-mints the command against the revision it is told
            // here, so this line is also the record of what it was told.
            tracing::warn!(
                harness = %published.harness_key,
                commanded = %short_revision(&spec.profile_revision),
                published = %short_revision(&published.config_revision),
                "rejecting a run minted against a superseded harness revision"
            );
            anyhow::bail!("harness configuration revision changed");
        }
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
        let probe = self.probes.get(&published.harness_key).cloned();
        let can_load_session = probe
            .as_ref()
            .is_some_and(|probe| probe.capabilities.load_session);
        let published_config_options = probe.map(|probe| probe.config_options).unwrap_or_default();
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
        self.spawn_run(
            spec,
            adapter,
            can_load_session,
            published_config_options,
            permit,
        );
        Ok(())
    }

    fn spawn_run(
        &mut self,
        spec: RunSpec,
        adapter: ResolvedAdapter,
        can_load_session: bool,
        published_config_options: Vec<ConfigOption>,
        permit: OwnedSemaphorePermit,
    ) {
        let target_id = self.target.target_id;
        let journal = self.journal.clone();
        let driver = Arc::clone(&self.driver);
        let mcp_bridge_executable = self.mcp_bridge_executable.clone();
        let paths = self.paths.clone();
        let permissions = self.permissions.clone();
        let events_ready = Arc::clone(&self.events_ready);
        let reprobe_requested = Arc::clone(&self.reprobe_requested);
        let run_id = spec.agent_run_id;
        // Captured before the task takes ownership of `adapter`, so a failure
        // can name the agent rather than describing it as an internal error.
        let adapter_name = adapter.spec.display_name.clone();
        let (cancel_tx, cancel_rx) = watch::channel(false);
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
            // `poll_target` snapshots the control batch when it builds the
            // request, so a checkpoint written a moment later waits out the
            // whole 25s long poll. Measured at 10-24s between a command being
            // delivered and the host reporting it accepted.
            events_ready.notify_one();
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
            let scratch = scratch_directory(&paths, target_id, spec.conversation_id);
            if let Some(parent) = scratch.parent() {
                prune_stale_scratch(parent);
            }
            std::fs::create_dir_all(&scratch)?;
            // The name Lemma published for this run's server, not a name of our
            // own. An agent namespaces every MCP tool with the server it came
            // from, so registering a different name here made the same tool
            // arrive as `mcp__lemma__lemma_exec_command` on this path and
            // `mcp__lemma_tools__lemma_exec_command` on every other one — one
            // tool with two names, which is exactly what the readers of those
            // names cannot tell apart.
            let scoped_server_name = spec
                .mcp
                .get("server_name")
                .and_then(Value::as_str)
                .map(str::trim)
                .filter(|name| !name.is_empty())
                .unwrap_or(crate::acp::SCOPED_MCP_SERVER)
                .to_owned();
            let mcp_server = McpServer::Stdio(
                McpServerStdio::new(scoped_server_name, mcp_bridge_executable)
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
                events_ready: Arc::clone(&events_ready),
            });
            let remaining = (spec.run_deadline - Utc::now())
                .to_std()
                .unwrap_or(Duration::ZERO);
            let request = AcpRunRequest {
                adapter,
                run_spec: spec,
                scratch_directory: scratch.clone(),
                mcp_server: Some(mcp_server),
                can_load_session,
                published_config_options,
                permissions: permissions.clone(),
                permission_timeout: PERMISSION_DECISION_TIMEOUT,
                cancel: cancel_rx,
                cancel_grace: CANCEL_GRACE,
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
            // Deliberately kept. It is the conversation's working directory, and
            // the next turn resumes the session that lives in it; deleting it
            // here is what made every resumption fail.
            match outcome {
                Ok(Ok(outcome)) => {
                    let mut payload = JsonMap::new();
                    payload.insert("state".to_owned(), serde_json::to_value(outcome.state)?);
                    payload.insert("stop_reason".to_owned(), Value::String(outcome.stop_reason));
                    // Lemma renders a failed run from `message`, so a turn that
                    // ended on a ceiling rather than a fault has to say which.
                    if let Some(message) = outcome.message {
                        payload.insert("message".to_owned(), Value::String(message));
                    }
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
                    let raw = error.to_string();
                    // A recognised failure is one we are restating in our own
                    // words -- and the adapter has already streamed its own
                    // into the transcript, so Lemma is told to drop that.
                    if authentication_hint(&adapter_name, &raw).is_some() {
                        // The freshest evidence anyone has that this agent is
                        // signed out. Probing is what publishes AUTH_REQUIRED,
                        // and it is otherwise up to fifteen minutes away, so
                        // ask for one now -- that is what makes the workspace
                        // say "Sign-in needed" while the user is still looking
                        // at the failure that told them.
                        reprobe_requested.store(true, Ordering::SeqCst);
                    }
                    let rewritten = authentication_hint(&adapter_name, &raw)
                        .or_else(|| adapter_failure_message(&adapter_name, &redact_error(&raw)));
                    let supersedes = rewritten.is_some();
                    let message = rewritten.unwrap_or_else(|| redact_error(&raw));
                    terminal_failure_detail(
                        &journal,
                        target_id,
                        run_id,
                        lease_epoch,
                        state,
                        &message,
                        supersedes,
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
            // However this run ended - success, failure, deadline - its
            // terminal checkpoint is upstream-bound and must not wait for the
            // current long poll either.
            events_ready.notify_one();
            Ok(())
        });
        self.active_runs.insert(
            run_id,
            ActiveRun {
                handle,
                cancel: cancel_tx,
                kill_at: None,
            },
        );
    }

    /// Ask a run to stop, through ACP where that is possible.
    ///
    /// Signalling rather than aborting is the whole point. `abort` kills the
    /// adapter mid-turn, and the provider has not yet written the session file
    /// that the conversation's *next* turn loads — so stopping one message used
    /// to silently cost the conversation its history. Raising the flag lets the
    /// driver send `session/cancel`, take the agent's own `cancelled` stop
    /// reason, and terminalize the run itself.
    ///
    /// The kill is still there, just deferred: a run that has not resolved by
    /// `kill_at` is torn down by `enforce_cancellations` exactly as before, so
    /// an adapter that ignores the notification cannot outlive its cancel.
    fn handle_cancel(&mut self, command: &Command) -> anyhow::Result<()> {
        self.journal
            .record_simple_command(self.target.target_id, command)?;
        let run_id = command
            .run_id
            .ok_or_else(|| anyhow::anyhow!("cancel command has no run ID"))?;
        if let Some(active) = self.active_runs.get_mut(&run_id) {
            // `send_replace`, not `send`: a run whose task has already dropped
            // its receiver has no listener, and `send` reports that as an error
            // without storing the value — which would leave the run looking
            // uncancelled to everything that reads the flag afterwards.
            active.cancel.send_replace(true);
            if active.kill_at.is_none() {
                active.kill_at = Some(tokio::time::Instant::now() + CANCEL_KILL_AFTER);
            }
            return Ok(());
        }
        // No task to ask: the run is already gone, so its terminal state is
        // this host's to write.
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

    /// Kill any run that was asked to stop and did not.
    ///
    /// This is the old `handle_cancel` behaviour, moved behind a deadline so it
    /// is the fallback rather than the first resort.
    fn enforce_cancellations(&mut self) -> anyhow::Result<()> {
        let now = tokio::time::Instant::now();
        let overdue = self
            .active_runs
            .iter()
            .filter_map(|(run_id, active)| {
                active
                    .kill_at
                    .is_some_and(|deadline| now >= deadline)
                    .then_some(*run_id)
            })
            .collect::<Vec<_>>();
        for run_id in overdue {
            // Abort, but keep the handle: `abort` only requests cancellation,
            // and the task can still finish its current poll -- which is long
            // enough to park a permission request. `reap_finished` abandons the
            // run again once the task is provably gone, which closes that
            // window.
            if let Some(active) = self.active_runs.get_mut(&run_id) {
                active.handle.abort();
                active.kill_at = None;
            }
            self.permissions.abandon_run(run_id);
            if let Some(run) = self.journal.get_run(self.target.target_id, run_id)?
                && !run.state.is_terminal()
            {
                tracing::warn!(
                    %run_id,
                    "the agent ignored session/cancel; terminating its process tree"
                );
                terminal_failure(
                    &self.journal,
                    self.target.target_id,
                    run_id,
                    run.lease_epoch,
                    RunState::Cancelled,
                    "run cancelled by Lemma; the agent did not stop on request",
                )?;
            }
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
                        tracing::info!(
                            harness = %snapshot.harness_key,
                            outcome = "unresolved",
                            "harness probe skipped"
                        );
                        return snapshot;
                    };
                    let started = std::time::Instant::now();
                    let previous_revision = snapshot.config_revision.clone();
                    match tokio::time::timeout(
                        Duration::from_secs(20),
                        driver.probe(adapter, scratch),
                    )
                    .await
                    {
                        Ok(Ok(probe)) => {
                            snapshot.config_options = probe.config_options;
                            snapshot.capabilities = capabilities_from_acp(&probe.capabilities);
                            snapshot.config_revision = snapshot.revision();
                            tracing::info!(
                                harness = %snapshot.harness_key,
                                outcome = "ready",
                                elapsed_ms = started.elapsed().as_millis(),
                                models = model_option_count(&snapshot.config_options),
                                revision_changed =
                                    previous_revision != snapshot.config_revision,
                                revision = %short_revision(&snapshot.config_revision),
                                auth_methods = %probe.auth_methods,
                                "harness probe finished"
                            );
                        }
                        Ok(Err(error)) => {
                            let raw = error.to_string();
                            // An agent that is installed but not signed in is a
                            // different thing from one that could not start, and
                            // the workspace already knows how to say so —
                            // "Sign-in needed", with the fix. It just never got
                            // told, because every probe failure looked alike.
                            if let Some(hint) = authentication_hint(&snapshot.display_name, &raw) {
                                snapshot.health = HarnessHealth::AuthRequired;
                                snapshot.stale_reason = Some(hint);
                            } else {
                                snapshot.health = HarnessHealth::ProbeFailed;
                                snapshot.stale_reason = Some(redact_error(&raw));
                            }
                            tracing::info!(
                                harness = %snapshot.harness_key,
                                outcome = ?snapshot.health,
                                elapsed_ms = started.elapsed().as_millis(),
                                detail = %redact_error(&raw),
                                "harness probe finished"
                            );
                        }
                        Err(_) => {
                            snapshot.health = HarnessHealth::ProbeFailed;
                            snapshot.stale_reason = Some("ACP probe timed out".to_owned());
                            tracing::info!(
                                harness = %snapshot.harness_key,
                                outcome = "timeout",
                                elapsed_ms = started.elapsed().as_millis(),
                                "harness probe finished"
                            );
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
        //
        // Published exactly once, after probing. An earlier revision published
        // the unprobed snapshots first so the agents would appear sooner — but
        // an unprobed snapshot has no `config_options`, and publishing it
        // *replaced* the probed ones. Every saved `config_selections` key then
        // failed validation as "unknown configuration selection". Getting the
        // machine online is what the poll does; the harnesses can wait for
        // their probe.
        tokio::spawn(async move {
            let discovered = discover_manifest.discover();
            let enriched = futures_util::future::join_all(build_probes(discovered)).await;
            let probes = enriched
                .iter()
                .map(|snapshot| {
                    (
                        snapshot.harness_key.clone(),
                        ProbedHarness {
                            capabilities: snapshot.capabilities.clone(),
                            config_options: snapshot.config_options.clone(),
                        },
                    )
                })
                .collect();
            // Logged before the request, and again with its outcome, because a
            // publish that never returns is indistinguishable in a log from one
            // that was never attempted -- and "was this host even trying?" is
            // the first question asked of a machine whose agents never answer.
            let attempted = enriched
                .iter()
                .map(|snapshot| {
                    format!(
                        "{}={:?}@{}",
                        snapshot.harness_key,
                        snapshot.health,
                        short_revision(&snapshot.config_revision)
                    )
                })
                .collect::<Vec<_>>()
                .join(" ");
            tracing::info!(harnesses = %attempted, "publishing probed harnesses");
            match client.publish_harnesses(enriched).await {
                Ok(published) => {
                    let accepted = published
                        .iter()
                        .map(|harness| {
                            format!(
                                "{}@{}",
                                harness.harness_key,
                                short_revision(&harness.config_revision)
                            )
                        })
                        .collect::<Vec<_>>()
                        .join(" ");
                    tracing::info!(harnesses = %accepted, "published probed harnesses");
                    let _ = sender.send(Some(ProbedHarnesses { published, probes }));
                }
                Err(error) => {
                    tracing::warn!(%error, "publishing probed harnesses failed");
                    // Tell the loop, so it can try again soon. Without this the
                    // next attempt is a full refresh interval away and every
                    // command in between is rejected for referencing a harness
                    // this host never got to publish.
                    let _ = sender.send(None);
                }
            }
        });
    }

    fn store_published(&mut self, probed: ProbedHarnesses) {
        self.harnesses = probed
            .published
            .into_iter()
            .map(|harness| (harness.id, harness))
            .collect();
        self.probes = probed.probes;
    }

    /// The control updates due for delivery, minus the ones Lemma refuses.
    ///
    /// A refused run keeps its *terminal* checkpoint in the batch: giving up on
    /// a run's last word is the one thing that would leave it unresolved.
    ///
    /// A refused *liveness* checkpoint is held back for a bounded number of
    /// polls and then tried again. Holding one back forever meant a single
    /// transient refusal — a deploy, a blip — permanently stopped heartbeating
    /// a run the host was still healthily executing, so its lease expired and
    /// Lemma recovered a live run to `DISPATCH_UNKNOWN`.
    fn control_batch(&mut self) -> anyhow::Result<ControlBatch> {
        let (command_ids, mut checkpoints, rejections) =
            self.journal.pending_control(self.target.target_id)?;
        self.refused_heartbeats.retain(|_, polls| {
            *polls = polls.saturating_sub(1);
            *polls > 0
        });
        checkpoints.retain(|checkpoint| {
            checkpoint.state.is_terminal()
                || !self.refused_heartbeats.contains_key(&checkpoint.run_id)
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
            self.refused_heartbeats
                .insert(checkpoint.run_id, REFUSED_HEARTBEAT_RETRY_POLLS);
            settled.checkpoints.push(checkpoint.clone());
            tracing::error!(
                run_id = %checkpoint.run_id,
                state = ?checkpoint.state,
                error = %detail,
                retry_after_polls = REFUSED_HEARTBEAT_RETRY_POLLS,
                "Lemma refused this run's heartbeat; backing off before trying again"
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

    /// Hand journaled events to Lemma. See [`EventFlusher::flush`].
    async fn flush_events(&mut self) -> anyhow::Result<()> {
        self.flusher.lock().await.flush().await
    }

    async fn reap_finished(&mut self) {
        let finished = self
            .active_runs
            .iter()
            .filter_map(|(run_id, active)| active.handle.is_finished().then_some(*run_id))
            .collect::<Vec<_>>();
        for run_id in finished {
            if let Some(active) = self.active_runs.remove(&run_id)
                && let Err(error) = active.handle.await
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
            self.enforce_cancellations()?;
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
            if let Some(active) = self.active_runs.remove(&run_id) {
                active.handle.abort();
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

    /// Register a task as an active run, as `spawn_run` does.
    #[cfg(test)]
    fn track_run(&mut self, run_id: Uuid, handle: JoinHandle<anyhow::Result<()>>) {
        self.active_runs.insert(
            run_id,
            ActiveRun {
                handle,
                cancel: watch::channel(false).0,
                kill_at: None,
            },
        );
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
    /// Raised whenever this run journals an event, so the poll loop stops
    /// waiting and flushes. Without it a run's output sits in the journal until
    /// the current 25s long poll returns — the agent answered in eight seconds
    /// and the conversation still waited twenty.
    events_ready: Arc<tokio::sync::Notify>,
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
        self.events_ready.notify_one();
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
        // `mark_dispatch_intent` just made the session id durable, and
        // `pending_control` puts it on every checkpoint this run reports. Waking
        // the poll here gets it upstream on the first one rather than a whole
        // long poll later, so Lemma has the conversation's session before the
        // user's next message arrives.
        self.events_ready.notify_one();
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
                self.events_ready.notify_one();
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
                self.events_ready.notify_one();
            }
        }
        Ok(())
    }
}

/// Extract streamed text the same way the backend normalizer does.
///
/// Public so `tests/wire_contract.rs` can hold it to the same shared fixture
/// the backend's `event_text` is held to. The two accumulate the same stream
/// into separate buffers that are reconciled at every segment boundary, so a
/// disagreement between them does not raise anything — it silently truncates a
/// persisted message.
pub fn chunk_text(payload: &JsonMap) -> String {
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

/// End a run and say why, on both paths that carry a reason upstream.
///
/// The message goes in the terminal *event* and in the terminal *checkpoint*,
/// because the two have different lifetimes. An event is pruned from the outbox
/// once Lemma acknowledges it, so a run that failed an hour ago has only its
/// checkpoint left — and that used to be written empty, which is why a dead run
/// could be inspected afterwards and offer nothing but `FAILED` and `{}`. The
/// checkpoint is also the path that survives Lemma never acknowledging the
/// event at all.
fn terminal_failure(
    journal: &Journal,
    target_id: Uuid,
    run_id: Uuid,
    lease_epoch: u32,
    state: RunState,
    message: &str,
) -> anyhow::Result<()> {
    terminal_failure_detail(
        journal,
        target_id,
        run_id,
        lease_epoch,
        state,
        message,
        false,
    )
}

/// The shared body, plus the one thing a caller may say about its message.
///
/// `supersedes_stream` means "this message is a rewrite of text the agent has
/// already streamed". An adapter reporting its own failure does it twice: once
/// as an ordinary `agent_message_chunk`, which Lemma turns into assistant text
/// in the transcript, and again when the turn ends. So a signed-out Claude Code
/// produced a bare "Failed to authenticate: OAuth session expired…" message
/// *and* the card saying the same thing in words the user can act on.
///
/// That is worse than untidy. Lemma only offers Retry on a failed run whose
/// messages are all the user's (`AgentRun.is_safely_retryable`), because
/// retrying a run that produced output can duplicate work -- so the stray
/// assistant message is also what removed the button. The one failure a retry
/// obviously fixes was the one failure that never offered one.
///
/// Set it only where the message really is a rewrite. A run that answered for
/// three paragraphs and then hit its deadline keeps all three, and keeps
/// blocking Retry, which is correct.
#[allow(clippy::fn_params_excessive_bools)]
fn terminal_failure_detail(
    journal: &Journal,
    target_id: Uuid,
    run_id: Uuid,
    lease_epoch: u32,
    state: RunState,
    message: &str,
    supersedes_stream: bool,
) -> anyhow::Result<()> {
    let mut detail = JsonMap::new();
    detail.insert("state".to_owned(), serde_json::to_value(state)?);
    detail.insert("message".to_owned(), Value::String(message.to_owned()));
    if supersedes_stream {
        detail.insert("supersedes_stream".to_owned(), Value::Bool(true));
    }
    journal.append_event(
        target_id,
        run_id,
        lease_epoch,
        EventType::Terminal,
        None,
        detail.clone(),
    )?;
    journal.checkpoint(target_id, run_id, lease_epoch, state, &detail)?;
    Ok(())
}

/// The working directory a conversation's provider session lives in.
///
/// Keyed on the conversation, not the run. ACP's `session/load` takes a working
/// directory, and a per-run directory is deleted the moment its run ends — so
/// every follow-up turn asked the agent to resume a session whose cwd no longer
/// existed. Resumption could therefore never succeed, and for `OpenCode` the
/// failed load left the connection unable to open a new session either, which
/// is why the first message answered and the second one did not.
///
/// One directory per conversation also matches what the comment in `acp.rs`
/// already claims: "a Lemma conversation is one provider session".
fn scratch_directory(paths: &HostPaths, target_id: Uuid, conversation_id: Uuid) -> PathBuf {
    paths
        .root
        .join("scratch")
        .join(target_id.to_string())
        .join(conversation_id.to_string())
}

fn publish_generated_images(
    scratch_directory: &std::path::Path,
    callbacks: &dyn AcpCallbacks,
) -> anyhow::Result<()> {
    for (object_id, payload) in generated_image_payloads(scratch_directory)? {
        callbacks.event(EventType::AgentMessageChunk, Some(object_id), payload)?;
    }
    // Cleared once published, because the directory around it now outlives the
    // run: it belongs to the conversation so the next turn can resume the
    // session in it. Leaving artifacts behind would republish this turn's
    // images on every later turn.
    let _ = std::fs::remove_dir_all(scratch_directory.join(GENERATED_ARTIFACT_DIRECTORY));
    Ok(())
}

/// How long a conversation's working directory outlives its last turn.
const SCRATCH_RETENTION: Duration = Duration::from_secs(14 * 24 * 60 * 60);

/// Drop conversation directories nothing has touched in a fortnight.
///
/// These used to be deleted after every run, so nothing needed pruning. Keeping
/// them is what makes session resumption possible, and this is what keeps that
/// from becoming an unbounded pile of working directories on someone's disk.
fn prune_stale_scratch(target_root: &std::path::Path) {
    let Ok(entries) = std::fs::read_dir(target_root) else {
        return;
    };
    for entry in entries.filter_map(Result::ok) {
        let stale = entry
            .metadata()
            .and_then(|metadata| metadata.modified())
            .is_ok_and(|modified| modified.elapsed().is_ok_and(|age| age > SCRATCH_RETENTION));
        if stale {
            let _ = std::fs::remove_dir_all(entry.path());
        }
    }
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

/// Enough of a revision to correlate two log lines, without the other 56 chars.
///
/// Revisions are only ever compared for equality, and a reader tracing a run
/// that was rejected for naming the wrong one needs to see *that* they differ,
/// not which bytes.
fn short_revision(revision: &str) -> &str {
    &revision[..revision.len().min(8)]
}

/// How many models a probe came back with, for the probe log line.
///
/// The interesting failure is a harness that probes fine and offers nothing:
/// that is what leaves a saved model unselectable and a run rejected for a
/// model "the harness does not offer".
fn model_option_count(options: &[ConfigOption]) -> usize {
    options
        .iter()
        .filter(|option| option.category == "model")
        .map(|option| option.options.len())
        .sum()
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

/// Turn an adapter's failure into something the person reading it can act on.
///
/// A coding agent that is installed but not signed in is the single most common
/// way a local run fails, and what it produces is the agent's own internal
/// error — for Claude Code, `Internal error: Failed to authenticate: OAuth
/// session expired and could not be refreshed: {"errorKind":
/// "authentication_failed"}`. That is accurate and useless: it names no agent,
/// suggests nothing to do, and reads like a defect in Lemma rather than a
/// session that needs renewing.
///
/// Returns `None` when the failure is not recognisably an authentication one,
/// so anything unfamiliar still travels verbatim rather than being flattened
/// into a guess.
fn authentication_hint(harness: &str, error: &str) -> Option<String> {
    let normalized = error.to_ascii_lowercase();
    let looks_like_auth = normalized.contains("authentication_failed")
        || normalized.contains("failed to authenticate")
        || normalized.contains("oauth session expired")
        || normalized.contains("not logged in")
        || normalized.contains("not authenticated")
        || (normalized.contains("unauthorized") && normalized.contains("session"));
    if !looks_like_auth {
        return None;
    }
    // "…and send the message again" was wrong, and wrong in a way that cost
    // people a restart: a signed-out harness is published AUTH_REQUIRED, and
    // admission refuses every run against it until the next probe — up to
    // fifteen minutes away. Sending again does nothing for that whole window.
    // What does work is asking the host to look again, which is what the
    // "Re-check" action on the failure does.
    Some(format!(
        "{harness} is installed on this computer but not signed in. \
         Sign in to it in a terminal, then press Re-check. \
         Lemma runs it with your credentials and never sees them."
    ))
}

/// Frame an adapter failure so it says which agent, and where to look.
///
/// Adapters report their own internals and the Agent Host forwarded them
/// untouched: `Internal error: OpenCode service failure: {"service":
/// "session"}` names no agent, points nowhere, and reads like a defect in
/// Lemma. This does not try to interpret the failure — guessing would bury the
/// one line that explains it — it just says whose failure it is and where the
/// detail lives.
///
/// Returns `None` for anything that is not an adapter's internal error, so
/// ordinary messages ("run deadline elapsed") stay exactly as they are.
fn adapter_failure_message(harness: &str, error: &str) -> Option<String> {
    let normalized = error.to_ascii_lowercase();
    let is_adapter_internal = normalized.contains("internal error")
        || normalized.contains("service failure")
        || normalized.contains("\"service\"");
    if !is_adapter_internal {
        return None;
    }
    Some(format!(
        "{harness} failed to start a session on this computer. \
         Check that it runs on its own in a terminal, then try again. \
         Its own error was: {}",
        error.trim()
    ))
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

    use super::{TargetWorker, deliver_events};
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

    fn cancel_command(run_id: Uuid) -> Command {
        Command {
            command_id: Uuid::new_v4(),
            kind: CommandKind::CancelRun,
            created_at: Utc::now(),
            expires_at: Utc::now() + chrono::Duration::minutes(1),
            run_id: Some(run_id),
            lease_epoch: Some(1),
            payload: serde_json::Value::Null,
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
                watch::channel(0_u64).1,
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
                resume_session_id: None,
                context: JsonMap::new(),
                mcp: serde_json::json!({}),
                run_deadline: Utc::now() + chrono::Duration::minutes(5),
                system_prompt_delivery: None,
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

    /// Wait for `predicate`, or fail rather than hang.
    async fn within(budget: Duration, what: &str, predicate: impl Fn() -> bool) {
        let deadline = tokio::time::Instant::now() + budget;
        while tokio::time::Instant::now() < deadline {
            if predicate() {
                return;
            }
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
        panic!("timed out waiting for {what}");
    }

    /// The finding: a run's output reached Lemma only by abandoning the poll.
    ///
    /// Delivery used to be an arm of the same `select!` as the poll, so every
    /// event a run streamed cancelled the poll in flight and opened a new one.
    /// The server never learned the old one was gone and held it for the rest
    /// of its 25 seconds — one host streaming a single answer stacked 26
    /// concurrent polls against exactly one while idle.
    ///
    /// This drives delivery with no poll running at all, which is only a
    /// meaningful thing to ask because the two are now independent.
    #[tokio::test]
    async fn a_runs_events_reach_lemma_with_no_poll_involved() {
        let harness = Harness::new().await;
        let run_id = harness.seed_run(3);

        let (_shutdown_tx, shutdown) = watch::channel(false);
        let delivery = tokio::spawn(deliver_events(
            Arc::clone(&harness.worker.flusher),
            Arc::clone(&harness.worker.events_ready),
            shutdown,
        ));
        // Exactly what a run task does the moment it journals an event.
        harness.worker.events_ready.notify_one();

        within(
            Duration::from_secs(5),
            "the run's events to reach Lemma",
            || harness.accepted().get(&run_id) == Some(&3),
        )
        .await;
        assert!(harness.pending(run_id).is_empty());

        // And it keeps serving. A run streams for its whole turn, so delivering
        // the first batch and then going quiet until the poll came back is the
        // same defect in a different place.
        for _ in 0..4 {
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
        }
        harness.worker.events_ready.notify_one();

        within(
            Duration::from_secs(5),
            "later events to reach Lemma",
            || harness.accepted().get(&run_id) == Some(&7),
        )
        .await;
        assert!(harness.pending(run_id).is_empty());

        delivery.abort();
    }

    /// Shutting the loop down must not strand what the journal still holds.
    ///
    /// The delivery task is aborted when the poll loop ends, so the last flush
    /// belongs to the shutdown path. Both take the same lock, which is what
    /// stops them sending one batch twice.
    #[tokio::test]
    async fn events_journaled_after_delivery_stops_are_still_sent() {
        let mut harness = Harness::new().await;

        let (shutdown_tx, shutdown) = watch::channel(false);
        let delivery = tokio::spawn(deliver_events(
            Arc::clone(&harness.worker.flusher),
            Arc::clone(&harness.worker.events_ready),
            shutdown,
        ));
        let _ = shutdown_tx.send(true);
        let _ = delivery.await;

        // Journaled with nothing left running to notice.
        let run_id = harness.seed_run(2);
        harness.worker.flush_events().await.unwrap();

        assert_eq!(harness.accepted().get(&run_id), Some(&2));
        assert!(harness.pending(run_id).is_empty());
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

    /// A dead run has to still be able to say why it died.
    ///
    /// The reason used to be written only into the terminal event, and an event
    /// is pruned once Lemma acknowledges it. So a run inspected any later than
    /// that offered `FAILED` and an empty detail, and its cause was gone for
    /// good — exactly when someone is asking why the agent stopped.
    #[tokio::test]
    async fn a_failed_run_keeps_its_reason_once_the_terminal_event_is_acknowledged() {
        let harness = Harness::new().await;
        let run_id = harness.seed_run(0);
        let reason = "the provider never answered the approved permission";

        super::terminal_failure(
            &harness.journal,
            harness.target_id,
            run_id,
            1,
            RunState::Failed,
            reason,
        )
        .unwrap();

        // Acknowledging is what makes the terminal event prunable, and it is
        // also what releases the terminal checkpoint to be sent.
        let acked_through = *harness
            .pending(run_id)
            .last()
            .expect("the failure must journal a terminal event");
        harness
            .journal
            .acknowledge_events(
                harness.target_id,
                &EventAck {
                    run_id,
                    lease_epoch: 1,
                    acked_through,
                },
            )
            .unwrap();

        let (_, checkpoints, _) = harness.journal.pending_control(harness.target_id).unwrap();
        let terminal = checkpoints
            .iter()
            .find(|checkpoint| checkpoint.run_id == run_id)
            .expect("a terminal run must report a checkpoint");
        assert_eq!(terminal.state, RunState::Failed);
        assert_eq!(
            terminal.detail.get("message"),
            Some(&serde_json::json!(reason)),
            "a dead run must still be able to say why",
        );
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
            harness.worker.refused_heartbeats.contains_key(&poisoned),
            "the refused run must be named, not left to poison every later poll"
        );
    }

    /// Every turn of a conversation must run in the same working directory.
    ///
    /// `session/load` takes a cwd, and a provider is entitled to refuse to load
    /// a session into a different one — Claude Code does. So a directory keyed
    /// on anything but the conversation makes every resume fail: each turn
    /// silently opens a new session and the agent meets the user again, with no
    /// error anywhere to say why. Nothing else in the system notices.
    #[test]
    fn a_conversation_always_gets_the_same_working_directory() {
        let paths = HostPaths::under(std::path::Path::new("/tmp/agent-host-test"));
        let target = Uuid::new_v4();
        let conversation = Uuid::new_v4();

        assert_eq!(
            super::scratch_directory(&paths, target, conversation),
            super::scratch_directory(&paths, target, conversation),
            "two turns of one conversation must share a cwd, or every \
             session/load is refused"
        );
        assert_ne!(
            super::scratch_directory(&paths, target, conversation),
            super::scratch_directory(&paths, target, Uuid::new_v4()),
            "two conversations must not share a workspace"
        );
    }

    /// The Lemma credential a run is dispatched with expires in an hour, and
    /// the bridge is a separate process that reads its endpoint from the
    /// journal. Writing the replacement there *is* the delivery.
    #[tokio::test]
    async fn a_refreshed_credential_reaches_the_runs_journal() {
        let mut harness = Harness::new().await;
        let run_id = harness.seed_run(0);
        let refreshed = serde_json::json!({
            "url": "https://lemma.example/mcp",
            "authorization": "Bearer refreshed",
        });

        harness
            .worker
            .handle_command(&Command {
                command_id: Uuid::new_v4(),
                kind: CommandKind::RefreshCredential,
                created_at: Utc::now(),
                expires_at: Utc::now() + chrono::Duration::minutes(1),
                run_id: Some(run_id),
                lease_epoch: Some(1),
                payload: serde_json::json!({"mcp": refreshed.clone()}),
            })
            .unwrap();

        let run = harness
            .journal
            .get_run(harness.target_id, run_id)
            .unwrap()
            .unwrap();
        assert_eq!(run.spec.mcp, refreshed);
    }

    /// Fenced like everything else a host is told about a run: a credential
    /// minted for a dispatch that has been superseded must not land on the one
    /// executing now.
    #[tokio::test]
    async fn a_refresh_for_a_superseded_lease_is_ignored() {
        let mut harness = Harness::new().await;
        let run_id = harness.seed_run(0);
        let before = harness
            .journal
            .get_run(harness.target_id, run_id)
            .unwrap()
            .unwrap()
            .spec
            .mcp;

        harness
            .worker
            .handle_command(&Command {
                command_id: Uuid::new_v4(),
                kind: CommandKind::RefreshCredential,
                created_at: Utc::now(),
                expires_at: Utc::now() + chrono::Duration::minutes(1),
                run_id: Some(run_id),
                lease_epoch: Some(9),
                payload: serde_json::json!({
                    "mcp": {"url": "https://elsewhere.example/mcp", "token": "x"}
                }),
            })
            .unwrap();

        assert_eq!(
            harness
                .journal
                .get_run(harness.target_id, run_id)
                .unwrap()
                .unwrap()
                .spec
                .mcp,
            before
        );
    }

    /// A refusal is usually transient. Holding the heartbeat back forever meant
    /// one blip sentenced a healthy run to lease expiry and `DISPATCH_UNKNOWN`,
    /// so the hold has to lapse on its own.
    #[tokio::test]
    async fn a_refused_heartbeat_is_retried_after_backing_off() {
        let mut harness = Harness::new().await;
        let poisoned = harness.seed_run(0);
        harness
            .stub
            .refused_checkpoints
            .lock()
            .unwrap()
            .push(poisoned);
        harness.worker.poll_target(capacity()).await.unwrap();
        assert!(harness.worker.refused_heartbeats.contains_key(&poisoned));

        // The server recovers; the host must eventually offer the run again.
        harness.stub.refused_checkpoints.lock().unwrap().clear();
        for _ in 0..super::REFUSED_HEARTBEAT_RETRY_POLLS {
            harness.worker.poll_target(capacity()).await.unwrap();
        }

        assert!(
            !harness.worker.refused_heartbeats.contains_key(&poisoned),
            "the hold must lapse, or the run's lease expires under a healthy host"
        );
        let applied = harness.stub.applied_checkpoints.lock().unwrap().clone();
        assert!(
            applied.iter().any(|(run_id, _)| *run_id == poisoned),
            "the run's heartbeat must resume once the refusal passes"
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
                gate.wait(run_id, "call-1".to_owned(), Duration::from_secs(600), None),
            )
            .await;
            Ok(())
        });
        harness.worker.track_run(run_id, handle);
        while !harness.worker.active_runs[&run_id].handle.is_finished() {
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

    /// A cancel asks first and kills second, so an adapter that ignores
    /// `session/cancel` still cannot outlive its cancellation.
    #[tokio::test]
    async fn a_cancel_asks_the_run_to_stop_before_killing_it() {
        let mut harness = Harness::new().await;
        let run_id = harness.seed_run(0);
        let handle = tokio::spawn(async move {
            std::future::pending::<()>().await;
            Ok(())
        });
        harness.worker.track_run(run_id, handle);

        harness
            .worker
            .handle_cancel(&cancel_command(run_id))
            .unwrap();

        let active = &harness.worker.active_runs[&run_id];
        assert!(
            *active.cancel.borrow(),
            "the run must be asked to stop through ACP first"
        );
        assert!(
            !active.handle.is_finished(),
            "it must not be killed outright"
        );
        assert!(
            !harness
                .journal
                .get_run(harness.target_id, run_id)
                .unwrap()
                .unwrap()
                .state
                .is_terminal(),
            "the run terminalizes itself once the agent honours the cancel"
        );

        // The grace elapses without the agent stopping.
        harness.worker.active_runs.get_mut(&run_id).unwrap().kill_at =
            Some(tokio::time::Instant::now());
        harness.worker.enforce_cancellations().unwrap();

        assert_eq!(
            harness
                .journal
                .get_run(harness.target_id, run_id)
                .unwrap()
                .unwrap()
                .state,
            RunState::Cancelled
        );
    }

    /// `abort` only requests cancellation, so the run task can still park one
    /// more request after the kill fallback has swept the gate. Keeping the
    /// handle until it is reaped is what closes that window.
    #[tokio::test]
    async fn cancelling_a_run_sweeps_a_request_parked_on_the_way_out() {
        let mut harness = Harness::new().await;
        let run_id = harness.seed_run(0);
        let handle = tokio::spawn(async move {
            std::future::pending::<()>().await;
            Ok(())
        });
        harness.worker.track_run(run_id, handle);

        harness
            .worker
            .handle_cancel(&cancel_command(run_id))
            .unwrap();
        // Skip the grace: this test is about the kill path, not its delay.
        harness.worker.active_runs.get_mut(&run_id).unwrap().kill_at =
            Some(tokio::time::Instant::now());
        harness.worker.enforce_cancellations().unwrap();

        // The aborted task gets one more poll before the runtime drops it,
        // which is long enough to register a request nobody will answer.
        let gate = harness.worker.permissions.clone();
        let late = tokio::spawn(async move {
            gate.wait(run_id, "late".to_owned(), Duration::from_secs(600), None)
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
            resume_session_id: None,
            context: JsonMap::new(),
            mcp: Value::Null,
            run_deadline: Utc::now() + chrono::Duration::minutes(5),
            system_prompt_delivery: None,
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
            events_ready: Arc::new(tokio::sync::Notify::new()),
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

#[cfg(test)]
mod adapter_failure_message_tests {
    use super::authentication_hint;

    #[test]
    fn a_signed_out_agent_is_told_to_sign_in_rather_than_reported_as_internal() {
        // Verbatim from Claude Code. Accurate and useless: it names no agent,
        // suggests nothing to do, and reads like a defect in Lemma rather than
        // a session that needs renewing.
        let raw = concat!(
            "Internal error: Failed to authenticate: OAuth session expired and ",
            r#"could not be refreshed: {"errorKind": "authentication_failed"}"#
        );
        let hint = authentication_hint("Claude Code", raw).expect("recognised as an auth failure");

        assert!(
            hint.contains("Claude Code"),
            "name the agent that needs signing in"
        );
        assert!(hint.contains("sign in") || hint.contains("signed in"));
        assert!(!hint.contains("errorKind"), "no adapter internals");
    }

    #[test]
    fn other_phrasings_of_the_same_failure_are_recognised() {
        for raw in [
            "authentication_failed",
            "Error: not logged in",
            "request failed: 401 unauthorized, session invalid",
        ] {
            assert!(
                authentication_hint("OpenCode", raw).is_some(),
                "{raw:?} is an authentication failure",
            );
        }
    }

    #[test]
    fn a_conversation_keeps_one_working_directory_across_turns() {
        // ACP's session/load takes a working directory. Keying this on the run
        // meant every follow-up turn asked the agent to resume a session whose
        // cwd had just been deleted — so resumption could never succeed, and
        // for OpenCode the failed load left the connection unable to open a new
        // session either. First message answered, second did not.
        let paths = super::HostPaths::under("/tmp/example");
        let target = uuid::Uuid::from_u128(1);
        let conversation = uuid::Uuid::from_u128(2);

        let first = super::scratch_directory(&paths, target, conversation);
        let second = super::scratch_directory(&paths, target, conversation);
        assert_eq!(
            first, second,
            "both turns share the conversation's directory"
        );

        let other = super::scratch_directory(&paths, target, uuid::Uuid::from_u128(3));
        assert_ne!(first, other, "different conversations stay isolated");
    }

    #[test]
    fn an_adapter_internal_error_says_whose_it_is() {
        // Verbatim from OpenCode. Names no agent, points nowhere, and reads
        // like a defect in Lemma rather than a session that would not start.
        let raw = r#"Internal error: OpenCode service failure: {"service": "session"}"#;
        let framed = super::adapter_failure_message("OpenCode", raw).expect("framed");

        assert!(framed.starts_with("OpenCode failed to start a session"));
        // The adapter's own words survive: they are the only thing that
        // explains an unfamiliar failure.
        assert!(framed.contains(raw));
    }

    #[test]
    fn an_ordinary_failure_is_not_dressed_up_as_an_adapter_fault() {
        for raw in [
            "Agent Host run deadline elapsed; the provider process was terminated",
            "adapter executable opencode was not found",
        ] {
            assert!(
                super::adapter_failure_message("OpenCode", raw).is_none(),
                "{raw:?}"
            );
        }
    }

    #[test]
    fn an_unfamiliar_failure_is_left_exactly_as_it_came() {
        // Guessing would bury the one line that explains an unknown failure.
        for raw in [
            "adapter executable opencode was not found",
            "provider process exited with status 1",
            "ACP probe timed out",
        ] {
            assert!(authentication_hint("OpenCode", raw).is_none(), "{raw:?}");
        }
    }
}

#[cfg(test)]
mod harness_publish_scheduling_tests {
    use super::{
        DISK_SCAN_INTERVAL, FIRST_HARNESS_WAIT, HARNESS_REFRESH_INTERVAL, HARNESS_RETRY_INTERVAL,
    };
    use crate::protocol::POLL_HOLD;

    #[test]
    fn noticing_a_new_agent_is_not_gated_on_the_refresh_interval() {
        // The refresh interval used to be the only thing that noticed a newly
        // installed agent, which put a quarter of an hour between installing
        // Claude Code and being able to use it. The supervisor's sweep answers
        // that question now, cheaply enough to ask every couple of seconds, and
        // the interval is a safety net behind it.
        assert!(
            DISK_SCAN_INTERVAL * 30 <= HARNESS_REFRESH_INTERVAL,
            "detection must be orders of magnitude faster than the safety net",
        );
        // And — the part this pair of constants cannot show on its own — the
        // scan has to be able to *reach* the worker inside its own interval.
        // It could not: the check ran once per loop iteration, and an iteration
        // is one held poll, so a two-second interval detected in up to
        // `POLL_HOLD`. `the_scan_does_not_wait_out_a_held_poll` below is the
        // one that fails if that comes back; this only fixes the budget.
        assert!(
            DISK_SCAN_INTERVAL < POLL_HOLD,
            "a scan slower than the poll hold would have nothing to add to it",
        );
    }

    /// The regression this pair of tests exists for.
    ///
    /// `DISK_SCAN_INTERVAL` was read as "an agent is noticed within two
    /// seconds". It was not: it bounded how often the check *could* run, and
    /// the check was reached once per iteration of a loop whose every iteration
    /// waits out a poll Lemma holds for `POLL_HOLD`. So the real answer was
    /// 0-25s, and the constant said 2.
    ///
    /// Both halves are asserted, because either alone still permits the bug:
    /// a select arm that does not abandon the poll would not help, and a scan
    /// that abandons the poll on every tick would mean the poll never returns.
    #[tokio::test(start_paused = true)]
    async fn the_scan_does_not_wait_out_a_held_poll() {
        use tokio::sync::watch;

        let (agents_changed, mut receiver) = watch::channel(0_u64);

        // A poll that is being held, exactly as Lemma holds it.
        let held_poll = tokio::time::sleep(POLL_HOLD);
        tokio::pin!(held_poll);

        let woke_at = {
            let started = tokio::time::Instant::now();
            // The supervisor's sweep notices a new agent one interval in.
            tokio::spawn(async move {
                tokio::time::sleep(DISK_SCAN_INTERVAL).await;
                agents_changed.send_modify(|generation| *generation += 1);
            });
            tokio::select! {
                () = &mut held_poll => tokio::time::Instant::now() - started,
                _ = receiver.changed() => tokio::time::Instant::now() - started,
            }
        };

        assert!(
            woke_at < POLL_HOLD,
            "a newly installed agent must not wait out the poll: woke after {woke_at:?}",
        );
        assert_eq!(
            woke_at, DISK_SCAN_INTERVAL,
            "and it must wake on the scan, not on anything else",
        );
    }

    #[test]
    fn only_a_change_after_the_baseline_is_worth_re_probing() {
        use super::InstalledAgents;

        let mut installed = InstalledAgents::default();
        // The baseline is not news: every worker probes on startup, so
        // announcing the first sweep too spawns every agent twice for one event.
        assert!(!installed.note("aaa".to_owned()));
        // A sweep that finds the same machine is not news either. This is the
        // one that has to hold at two-second intervals forever.
        assert!(!installed.note("aaa".to_owned()));
        // An agent installed, upgraded in place, or removed is.
        assert!(installed.note("bbb".to_owned()));
        assert!(!installed.note("bbb".to_owned()));
        assert!(installed.note("aaa".to_owned()));
    }

    /// The other half: nothing wakes the loop when the disk is unchanged.
    ///
    /// A tick that fired unconditionally would abandon the in-flight poll every
    /// two seconds, so a 25-second poll would never once return and no command
    /// would ever be delivered. The arm has to be a change notification, not a
    /// timer.
    #[tokio::test(start_paused = true)]
    async fn an_unchanged_disk_lets_the_poll_run_to_completion() {
        use tokio::sync::watch;

        let (_agents_changed, mut receiver) = watch::channel(0_u64);
        let held_poll = tokio::time::sleep(POLL_HOLD);
        tokio::pin!(held_poll);
        let started = tokio::time::Instant::now();

        tokio::select! {
            () = &mut held_poll => {}
            _ = receiver.changed() => panic!("an unchanged disk must not abandon the poll"),
        }

        assert_eq!(tokio::time::Instant::now() - started, POLL_HOLD);
    }

    #[test]
    fn a_failed_publish_is_retried_in_seconds_not_a_quarter_of_an_hour() {
        // The refresh interval is the safety net behind fingerprint detection,
        // so it fires rarely. It is the wrong answer to "the publish failed":
        // the backend restarts whenever its configuration changes, and a
        // publish that landed during one used to leave this host with nothing
        // published until the next refresh — rejecting every command in
        // between for referencing a harness it had never announced.
        assert!(
            HARNESS_RETRY_INTERVAL * 6 <= HARNESS_REFRESH_INTERVAL,
            "a failure must not wait anything like a full refresh",
        );
        // And a command already in hand has to be able to outlast a retry,
        // otherwise waiting for one is pointless.
        assert!(
            HARNESS_RETRY_INTERVAL < FIRST_HARNESS_WAIT,
            "a command's wait must cover at least one retry",
        );
    }
}
