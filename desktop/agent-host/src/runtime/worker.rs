//! One target's worker: its link loop, and the state it carries.

use crate::host_exec::wire::HostExecutionStatus;
use crate::link::protocol::{ControlBody, HelloBody, close};
use crate::link::{self, Connected, LinkError, LinkHandle, LinkSlot, LinkSlotOwner, Push};
use crate::protocol::HostHello;

use super::{
    AdapterManifest, AgentDriver, Arc, AtomicBool, BTreeMap, CommandRejection, ConfigOption,
    Duration, EventFlusher, HARNESS_REFRESH_INTERVAL, HARNESS_RETRY_INTERVAL, HarnessCapabilities,
    HashMap, HostCapacity, HostConfig, HostPaths, Journal, LOCAL_CONTROL_INTERVAL, Ordering,
    OutboxSignal, OwnedTask, PathBuf, PermissionGate, RETRY_MAX, RETRY_MIN, REVOKED_REFUSALS,
    RunCheckpoint, SUPERSEDED_MAX_WAIT, SUPERSEDED_SETTLED, SUPERSEDED_WARN_AFTER, Semaphore,
    TargetConfig, TransientBackoff, Utc, Uuid, command_rejection, deliver_events, mpsc,
    redact_error, watch,
};

/// The control updates one `control` frame carries up: command
/// acknowledgements, run checkpoints, and command rejections.
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
}

pub(crate) struct TargetWorker {
    pub(crate) target: TargetConfig,
    pub(crate) installation_id: String,
    pub(crate) paths: HostPaths,
    pub(crate) journal: Journal,
    pub(crate) manifest: AdapterManifest,
    pub(crate) driver: Arc<dyn AgentDriver>,
    pub(crate) mcp_bridge_executable: PathBuf,
    pub(crate) global_capacity: Arc<Semaphore>,
    pub(crate) max_runs: u16,
    pub(crate) shutdown: watch::Receiver<bool>,
    pub(crate) harnesses: BTreeMap<Uuid, crate::link::protocol::PublishedHarness>,
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
    /// Runs whose liveness checkpoints Lemma refused, and when to try them
    /// again. Their leases meanwhile fall to the server's own expiry recovery;
    /// their terminal states are never given up on, so a run is not abandoned
    /// mid-flight by this.
    pub(crate) refused_heartbeats: HashMap<Uuid, std::time::Instant>,
    pub(crate) draining: bool,
    pub(crate) refresh_due: std::time::Instant,
    /// When to re-read the local control file. See `LOCAL_CONTROL_INTERVAL`.
    pub(crate) controls_due: std::time::Instant,
    /// Bumped by the supervisor's sweep when the agents installed on this
    /// machine change. Awaiting it is what turns "install an agent, wait up to
    /// fifteen minutes" into "install an agent, it appears".
    pub(crate) agents_changed: watch::Receiver<u64>,
    /// Consecutive refusals saying Lemma does not know this pairing. Reset by
    /// any link that completes its handshake, so only an unbroken run of them
    /// drops the pairing.
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
    /// Raised whenever the journal owes Lemma something: events to the
    /// delivery task, control updates to the link loop.
    pub(crate) events_ready: OutboxSignal,
    /// Enriched harnesses from probes that ran off the link loop's critical
    /// path. Drained each iteration so a slow probe never delays a heartbeat.
    pub(crate) probed: (
        mpsc::UnboundedSender<Option<ProbedHarnesses>>,
        mpsc::UnboundedReceiver<Option<ProbedHarnesses>>,
    ),
    pub(crate) probe_task: Option<OwnedTask<()>>,
    /// Whether the next refresh must probe every agent afresh, rather than
    /// reuse probes of unchanged versions. Raised by everything that says the
    /// agents may have changed -- an install, a run that found its agent
    /// signed out, the person pressing Re-check -- and not by the scheduled
    /// refresh, which is only a safety net.
    pub(crate) force_probe: bool,
    /// The link currently open, for the tasks that run beside this loop.
    pub(crate) slot_owner: LinkSlotOwner,
    pub(crate) link: LinkSlot,
    /// The owner's host-execution setting, as last read from `config.json`.
    pub(crate) host_execution: bool,
    /// How far Lemma's clock is ahead of this one, from the last `welcome`.
    /// Command expiries and run deadlines are Lemma's times.
    pub(crate) clock_offset: chrono::Duration,
    /// Links in a row that another connection with this pairing superseded
    /// within moments of opening: two hosts sharing one credential, each
    /// taking the link from the other. Each one waits longer before trying.
    pub(crate) superseded_streak: u32,
    /// Answers Lemma's `op` requests. `None` where host execution cannot
    /// work at all, in which case every op is answered
    /// `exec_server_unavailable` by the link itself.
    #[cfg(unix)]
    pub(crate) exec_relay: Option<Arc<crate::host_exec::relay::ExecRelay>>,
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
    /// Where a mid-run `REFRESH_CREDENTIAL` writes the new token. The run's
    /// task holds the other half and retires it on the way out.
    pub(crate) credential: std::sync::Arc<crate::runtime::credentials::RunCredential>,
    /// Where a `STEER_RUN` hands its message to the run's turn.
    pub(crate) steer: crate::acp::SteerSender,
}

/// One completed refresh: what Lemma accepted, and what the probes learned.
pub(crate) struct ProbedHarnesses {
    pub(crate) published: Vec<crate::link::protocol::PublishedHarness>,
    pub(crate) probes: HashMap<String, ProbedHarness>,
    /// Whether anything failed for a reason that may not fail twice.
    ///
    /// A probe that timed out on a busy machine is not a settled fact about the
    /// installation. Backing off from it reaches the ordinary refresh interval
    /// as its ceiling, so the worst case is the old behaviour and the common
    /// case is seconds.
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
    /// The versions this probe was of, and whether it succeeded. A scheduled
    /// refresh reuses a successful probe of the same versions rather than
    /// opening another real session in the agent -- each probe is a
    /// `session/new` that lands in the person's own session history.
    pub(crate) adapter_version: String,
    pub(crate) upstream_version: Option<String>,
    pub(crate) ready: bool,
}

/// How a connected session ended.
enum SessionEnd {
    /// The host is shutting down, and did so gracefully.
    Shutdown,
    /// The link went away; reconnect, after `after` if Lemma asked for a delay.
    Lost {
        error: LinkError,
        after: Option<Duration>,
    },
}

/// How often the loop does its own bookkeeping -- reaping finished runs,
/// enforcing cancellations, re-reading local controls, noticing probes -- while
/// nothing else wakes it. Cheap, and short enough that a cancellation's kill
/// deadline is kept to within a second.
const HOUSEKEEPING_INTERVAL: Duration = Duration::from_secs(1);

/// The longest a host waits to reconnect after Lemma restarts.
const RESTART_JITTER_MAX_MS: u64 = 5_000;

/// A random delay in `0..RESTART_JITTER_MAX_MS`, so a deploy does not bring
/// every host back in the same instant.
fn restart_jitter() -> Duration {
    let mut bytes = [0_u8; 8];
    let random = getrandom::fill(&mut bytes).map_or(0, |()| u64::from_le_bytes(bytes));
    Duration::from_millis(random % RESTART_JITTER_MAX_MS)
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
        journal.register_target(target.target_id)?;
        // Paused for another person's session counts as draining here: no new
        // runs, and the ones in flight finish.
        let draining = !target.takes_work();
        let flusher = Arc::new(tokio::sync::Mutex::new(EventFlusher {
            target_id: target.target_id,
            journal: journal.clone(),
            rejections: HashMap::new(),
        }));
        let (slot_owner, link) = LinkSlotOwner::new();
        // The exec-server is this same binary, as the MCP bridge is. Only the
        // pairing with the Lemma installed on this computer gets one: any
        // other pairing is a server elsewhere, and its `op` frames are
        // answered as a host with no handler answers them.
        #[cfg(unix)]
        let exec_relay = target
            .is_local_install()
            .then(|| {
                crate::host_exec::relay::RelayPaths::current(
                    paths.folders.clone(),
                    paths.conversation_roots.clone(),
                    target.target_id,
                )
                .inspect_err(|error| {
                    tracing::warn!(%error, "host execution is unavailable on this computer");
                })
                .ok()
            })
            .flatten()
            .map(|relay_paths| {
                crate::host_exec::relay::ExecRelay::new(
                    Arc::new(crate::host_exec::relay::ProcessLauncher {
                        executable: mcp_bridge_executable.clone(),
                        data_root: paths.root.clone(),
                        sandboxed: true,
                    }),
                    relay_paths,
                )
            });
        Ok(Self {
            target,
            installation_id,
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
            events_ready: OutboxSignal::default(),
            probed: mpsc::unbounded_channel(),
            probe_task: None,
            force_probe: true,
            slot_owner,
            link,
            host_execution: false,
            clock_offset: chrono::Duration::zero(),
            superseded_streak: 0,
            #[cfg(unix)]
            exec_relay,
        })
    }

    pub(crate) async fn run(mut self) -> anyhow::Result<()> {
        self.recover_interrupted_runs()?;
        // Event delivery and the MCP relay run beside the link loop, and wait
        // on the slot for whichever link is open. Aborted at the end of this
        // function; the shutdown path takes the flusher's lock and does the
        // last flush itself, so nothing in the journal is left behind.
        let delivery = OwnedTask(tokio::spawn(deliver_events(
            Arc::clone(&self.flusher),
            self.events_ready.clone(),
            self.link.clone(),
            self.shutdown.clone(),
        )));
        let relay = crate::mcp_relay::serve(
            &self.paths,
            self.target.target_id,
            self.journal.clone(),
            self.link.clone(),
        )
        .map(|task| OwnedTask(tokio::spawn(task)));
        if let Err(error) = &relay {
            // Without the relay an agent has no Lemma tools, but it can still
            // answer; that is a degraded run, not a reason to take this
            // computer offline.
            tracing::error!(%error, "could not start the MCP relay; runs will have no Lemma tools");
        }
        let outcome = self.link_loop().await;
        #[cfg(unix)]
        if let Some(relay) = &self.exec_relay {
            relay.close_all().await;
        }
        delivery.abort();
        if let Ok(relay) = relay {
            relay.abort();
        }
        outcome
    }

    pub(crate) fn capacity(&self) -> HostCapacity {
        let available = u16::try_from(self.global_capacity.available_permits()).unwrap_or(u16::MAX);
        HostCapacity {
            max_runs: self.max_runs,
            active_runs: self.max_runs.saturating_sub(available),
            available_runs: if self.draining { 0 } else { available },
        }
    }

    /// What Lemma is told about host execution, in `hello` and `control`.
    pub(crate) fn host_execution_status(&self) -> HostExecutionStatus {
        let mut status = HostExecutionStatus::current(self.host_execution);
        #[cfg(unix)]
        {
            status.available &= self
                .exec_relay
                .as_ref()
                .is_some_and(|relay| relay.available());
        }
        #[cfg(not(unix))]
        {
            status.available = false;
        }
        status
    }

    // The exec relay is built only on Unix; elsewhere there is none to hand
    // out and `self` goes unread.
    #[cfg_attr(not(unix), allow(clippy::unused_self))]
    fn op_handler(&self) -> Option<Arc<dyn link::OpHandler>> {
        #[cfg(unix)]
        {
            self.exec_relay
                .clone()
                .map(|relay| relay as Arc<dyn link::OpHandler>)
        }
        #[cfg(not(unix))]
        {
            None
        }
    }

    /// Stay connected until the host shuts down or the pairing is dropped.
    pub(crate) async fn link_loop(&mut self) -> anyhow::Result<()> {
        let mut retry = RETRY_MIN;
        loop {
            if self.shutting_down() {
                return self.graceful_shutdown(None).await;
            }
            self.housekeeping()?;
            let connected = tokio::select! {
                connected = link::connect_with(
                    &self.target.base_url,
                    &self.target.host_secret,
                    HelloBody {
                        hello: HostHello::current(&self.installation_id),
                        capacity: self.capacity(),
                        host_execution: Some(self.host_execution_status()),
                    },
                    self.op_handler(),
                ) => connected,
                _ = self.shutdown.changed() => continue,
            };
            let error = match connected {
                Ok(connected) => {
                    retry = RETRY_MIN;
                    // A completed handshake proves the pairing is known, so
                    // any refusals before it were the transient kind.
                    self.revoked_refusals = 0;
                    self.note_lemma_time(connected.welcome.server_time);
                    self.journal
                        .update_target_state(self.target.target_id, "ONLINE", None)?;
                    let opened = std::time::Instant::now();
                    match self.session(connected).await? {
                        SessionEnd::Shutdown => return Ok(()),
                        SessionEnd::Lost { error, after } => {
                            self.slot_owner.set(None);
                            // Lemma restarting says so with 1012, usually
                            // without the `reconnect` frame that would carry a
                            // delay -- uvicorn closes sockets before the app's
                            // own shutdown runs. Every host sees the same close
                            // at the same instant, so each picks its own delay
                            // rather than all reconnecting at once.
                            let after = after.or_else(|| {
                                matches!(
                                    error,
                                    LinkError::Closed {
                                        code: close::RESTARTING,
                                        ..
                                    }
                                )
                                .then(restart_jitter)
                            });
                            let after = after.or_else(|| self.superseded_wait(&error, opened));
                            if let Some(after) = after {
                                self.note_offline(&error.to_string())?;
                                self.wait_retry(after).await;
                                continue;
                            }
                            error
                        }
                    }
                }
                Err(error) => error,
            };
            if error.is_upgrade_required() {
                self.note_offline("this Lemma needs a newer Agent Host")?;
                anyhow::bail!("target requires a newer Agent Host protocol");
            }
            if error.is_revoked_or_missing() {
                // Lemma does not know this pairing. Stop being paired rather
                // than retrying forever -- but not on the first refusal: the
                // backend cannot say whether the host was revoked or is
                // merely missing, and "missing" includes a machine pointed at
                // the wrong backend and a database restored behind its own
                // writes. Dropping the pairing there costs a re-pair for a
                // condition that heals itself.
                self.revoked_refusals += 1;
                if self.revoked_refusals >= REVOKED_REFUSALS {
                    tracing::warn!(
                        target = %self.target.name,
                        refusals = self.revoked_refusals,
                        "Lemma does not know this pairing; dropping it"
                    );
                    self.cancel_all("Lemma revoked this Agent Host")?;
                    self.forget_target()?;
                    return Err(error.into());
                }
                tracing::info!(
                    target = %self.target.name,
                    refusals = self.revoked_refusals,
                    "Lemma does not know this pairing; retrying before dropping it"
                );
            }
            // A malformed or missing credential (4403) is retried with
            // backoff like any other refusal Lemma may recover from: giving
            // up for good on it disabled a pairing that a restarted or
            // restored Lemma would have accepted again.
            self.note_offline(&error.to_string())?;
            self.wait_retry(retry).await;
            retry = (retry * 2).min(RETRY_MAX);
        }
    }

    /// Remember how far Lemma's clock is from this one.
    pub(crate) fn note_lemma_time(&mut self, server_time: Option<chrono::DateTime<Utc>>) {
        self.clock_offset =
            server_time.map_or_else(chrono::Duration::zero, |lemma| lemma - Utc::now());
        if self.clock_offset.num_seconds().abs() > 60 {
            tracing::warn!(
                offset_seconds = self.clock_offset.num_seconds(),
                "this computer's clock is not Lemma's; judging deadlines by Lemma's"
            );
        }
    }

    /// Now, by Lemma's clock.
    pub(crate) fn lemma_now(&self) -> chrono::DateTime<Utc> {
        Utc::now() + self.clock_offset
    }

    /// How long to wait after this connection was superseded, when that is
    /// what ended it. A link that lasted is an ordinary hand-over -- the app
    /// restarted its host, say -- and is retried at once; one taken away
    /// within moments is another host holding this pairing's credential, and
    /// the two would otherwise take the link from each other for ever.
    pub(crate) fn superseded_wait(
        &mut self,
        error: &LinkError,
        opened: std::time::Instant,
    ) -> Option<Duration> {
        if !matches!(
            error,
            LinkError::Closed {
                code: close::SUPERSEDED,
                ..
            }
        ) {
            self.superseded_streak = 0;
            return None;
        }
        if opened.elapsed() >= SUPERSEDED_SETTLED {
            self.superseded_streak = 0;
            return None;
        }
        self.superseded_streak = self.superseded_streak.saturating_add(1);
        if self.superseded_streak == SUPERSEDED_WARN_AFTER {
            tracing::warn!(
                target = %self.target.name,
                "another Agent Host is connecting with this pairing; backing off"
            );
        }
        let exponent = self.superseded_streak.min(9);
        Some((RETRY_MIN * 2_u32.pow(exponent)).min(SUPERSEDED_MAX_WAIT))
    }

    /// Everything the host does while connected.
    ///
    /// The link's reader, writer and this loop are separate tasks joined by
    /// channels, so nothing here waits on a request Lemma is holding: work
    /// happens when it is due. That is what the long poll could not do -- the
    /// loop spent nearly all its time inside a held request, so anything that
    /// had to happen sooner needed its own arm, and a check at the top of the
    /// loop ran once per 25 seconds however short its own interval said.
    async fn session(&mut self, connected: Connected) -> anyhow::Result<SessionEnd> {
        let link = connected.handle.clone();
        let end = self.serve_session(connected).await;
        if !matches!(end, Ok(SessionEnd::Shutdown)) {
            // A session ends without the link having closed -- a request on
            // it timed out, say. Close it rather than abandon it: tasks
            // still holding a handle (an MCP call waits without a deadline of
            // its own) learn now that it is gone and retry on the next link,
            // and Lemma stops counting it as this host's connection.
            link.close(close::NORMAL, "reconnecting");
        }
        end
    }

    /// The body of [`Self::session`], which owns closing the link after it.
    async fn serve_session(&mut self, connected: Connected) -> anyhow::Result<SessionEnd> {
        let Connected {
            handle,
            mut pushes,
            welcome,
        } = connected;
        tracing::info!(
            target = %self.target.name,
            heartbeat_ms = welcome.heartbeat_ms,
            "connected to Lemma"
        );
        self.slot_owner.set(Some(handle.clone()));
        // Anything the journal was holding goes out now: deliveries queued
        // while offline, and the acknowledgements and checkpoints Lemma has
        // been waiting on.
        self.events_ready.notify_one();
        let heartbeat = Duration::from_millis(welcome.heartbeat_ms.clamp(1_000, 60_000));
        let mut heartbeat = tokio::time::interval(heartbeat);
        heartbeat.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
        let mut housekeeping = tokio::time::interval(HOUSEKEEPING_INTERVAL);
        housekeeping.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
        let mut agents_changed = self.agents_changed.clone();
        let control_ready = self.events_ready.control();
        let mut reconnect_after = None;
        loop {
            tokio::select! {
                biased;
                _ = self.shutdown.changed() => {
                    if self.shutting_down() {
                        self.graceful_shutdown(Some((&handle, &mut pushes))).await?;
                        handle.close(close::NORMAL, "host shutting down");
                        return Ok(SessionEnd::Shutdown);
                    }
                }
                push = pushes.recv() => match push {
                    Some(Push::Commands(commands)) => {
                        self.handle_commands(commands).await?;
                        if let Err(error) = self.send_control(&handle).await {
                            return Ok(SessionEnd::Lost { error, after: reconnect_after });
                        }
                    }
                    Some(Push::Reconnect(after)) => reconnect_after = Some(after),
                    None => {
                        let error = handle.closed().await;
                        return Ok(SessionEnd::Lost { error, after: reconnect_after });
                    }
                },
                () = control_ready.notified() => {
                    if let Err(error) = self.send_control(&handle).await {
                        return Ok(SessionEnd::Lost { error, after: reconnect_after });
                    }
                }
                _ = heartbeat.tick() => {
                    if let Err(error) = self.send_control(&handle).await {
                        return Ok(SessionEnd::Lost { error, after: reconnect_after });
                    }
                }
                changed = agents_changed.changed() => {
                    if changed.is_err() {
                        self.graceful_shutdown(Some((&handle, &mut pushes))).await?;
                        return Ok(SessionEnd::Shutdown);
                    }
                    // The agents on this machine changed: probe now, not at
                    // the next scheduled refresh.
                    self.refresh_due = std::time::Instant::now();
                    self.force_probe = true;
                    self.housekeeping()?;
                }
                _ = housekeeping.tick() => self.housekeeping()?,
                error = handle.closed() => {
                    return Ok(SessionEnd::Lost { error, after: reconnect_after });
                }
            }
        }
    }

    /// Everything the loop does on its own clock rather than on a signal.
    pub(crate) fn housekeeping(&mut self) -> anyhow::Result<()> {
        self.enforce_cancellations()?;
        self.reap_finished_now();
        if self.controls_due <= std::time::Instant::now() {
            self.controls_due = std::time::Instant::now() + LOCAL_CONTROL_INTERVAL;
            self.apply_local_controls()?;
        }
        self.drain_published();
        if self.reprobe_requested.swap(false, Ordering::SeqCst) {
            self.force_probe = true;
            self.refresh_due = std::time::Instant::now();
        }
        if self.refresh_due <= std::time::Instant::now() {
            self.refresh_harnesses();
            self.refresh_due = std::time::Instant::now() + HARNESS_REFRESH_INTERVAL;
        }
        Ok(())
    }

    /// Take a batch of commands, judging each against the harnesses this host
    /// has actually published.
    pub(crate) async fn handle_commands(
        &mut self,
        commands: Vec<crate::protocol::Command>,
    ) -> anyhow::Result<()> {
        if commands.is_empty() {
            return Ok(());
        }
        // Harnesses publish off the loop, so what this host holds can be
        // behind what Lemma minted against: a publish still sitting in the
        // channel, or no publish at all yet. Both are corrected before any
        // command is judged against a harness.
        self.sync_harnesses_for_commands().await;
        for command in commands {
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
        Ok(())
    }

    /// Send what the journal owes Lemma, and take whatever it hands back.
    ///
    /// Also the heartbeat: it carries the non-terminal checkpoint of every run
    /// in flight, and that is what renews each run's lease.
    pub(crate) async fn send_control(&mut self, link: &LinkHandle) -> Result<(), LinkError> {
        let batch = match self.control_batch() {
            Ok(batch) => batch,
            Err(error) => {
                tracing::error!(%error, "could not read the journal's pending control updates");
                ControlBatch::default()
            }
        };
        let body = ControlBody {
            capacity: self.capacity(),
            acknowledged_command_ids: batch.command_ids.clone(),
            checkpoints: batch.checkpoints.clone(),
            rejections: batch.rejections.clone(),
            host_execution: Some(self.host_execution_status()),
        };
        let answer = match link.control(&body).await {
            Ok(answer) => answer,
            // A frame Lemma could not read as a whole is not something a
            // retry fixes, and holding the rest of the host's reporting
            // hostage to it would stop every run's heartbeat. Say so, and let
            // the next heartbeat carry on.
            Err(error) if error.is_request_rejected() => {
                tracing::error!(%error, updates = batch.len(), "Lemma refused a control frame");
                return Ok(());
            }
            Err(error) => return Err(error),
        };
        let settled = self.settle_control(&batch, &answer.refused);
        if let Err(error) = self.journal.mark_control_applied(
            self.target.target_id,
            &settled.command_ids,
            &settled.checkpoints,
            &settled.rejections,
        ) {
            tracing::error!(%error, "could not record which control updates Lemma accepted");
        }
        if let Err(error) = self.handle_commands(answer.commands).await {
            tracing::error!(%error, "could not take the commands a control answer carried");
        }
        Ok(())
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
            "dropped a revoked pairing; this computer will not connect to it again"
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
        // This pairing's own switch, and only if it is the local one.
        let host_execution = current.runs_host_commands();
        if host_execution != self.host_execution {
            self.host_execution = host_execution;
            #[cfg(unix)]
            if let Some(relay) = &self.exec_relay {
                relay.set_enabled(host_execution);
            }
            // Lemma routes on this, so it hears now rather than at the next
            // heartbeat: a `control` carries it.
            self.events_ready.notify_control();
            tracing::info!(
                enabled = host_execution,
                target = %self.target.name,
                "host execution setting changed"
            );
        }
        self.draining = !current.takes_work();
        if current.refresh_generation != self.target.refresh_generation {
            self.refresh_due = std::time::Instant::now();
            self.force_probe = true;
        }
        self.target.draining = current.draining;
        self.target.session_paused = current.session_paused;
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

    pub(crate) fn shutting_down(&self) -> bool {
        *self.shutdown.borrow() || self.shutdown.has_changed().is_err()
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
                credential: crate::runtime::credentials::RunCredential::new(
                    &self.paths.root,
                    run_id,
                ),
                steer: crate::acp::SteerInbox::channel().0,
            },
        );
    }

    /// `track_run`, keeping the inbox the run's turn would read steers from.
    #[cfg(test)]
    pub(crate) fn track_steerable_run(
        &mut self,
        run_id: Uuid,
        handle: super::JoinHandle<anyhow::Result<()>>,
    ) -> crate::acp::SteerInbox {
        self.track_run(run_id, handle);
        let (sender, inbox) = crate::acp::SteerInbox::channel();
        if let Some(active) = self.active_runs.get_mut(&run_id) {
            active.steer = sender;
        }
        inbox
    }
}
