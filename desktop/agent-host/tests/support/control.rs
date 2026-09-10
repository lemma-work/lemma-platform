//! A stand-in for the workspace's control plane.

use super::*;

// ---------------------------------------------------------------------------
// Stand-in Lemma control plane.
// ---------------------------------------------------------------------------

/// What the control plane should answer when the host reports a parked
/// permission request.
#[derive(Clone, Debug)]
pub enum PermissionAnswer {
    /// Send `RESOLVE_PERMISSION` selecting this option id.
    Allow(String),
    /// Select whichever option the agent labelled `allow_once`. Real adapters
    /// name their options differently, so a test that drives one cannot pin an
    /// id the way the scripted agent can.
    AllowOnce,
    /// Approve the first request and deny every later one.
    ///
    /// Two concurrent requests answered *differently* is the only way to show
    /// that each was resolved by its own id: one shared key cannot deliver two
    /// different outcomes.
    AllowThenDeny,
    /// Send `RESOLVE_PERMISSION` with no option id, i.e. a denial.
    Deny,
    /// Never answer, so the host's own timeout is the only way out.
    Ignore,
}

impl PermissionAnswer {
    /// The option id to send for the `index`-th request answered this run.
    pub(crate) fn option_for(&self, index: usize, payload: &JsonMap) -> Option<String> {
        match self {
            Self::Allow(option_id) => Some(option_id.clone()),
            Self::AllowThenDeny if index == 0 => Self::AllowOnce.option_for(index, payload),
            Self::AllowOnce => payload
                .get("options")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .find(|option| {
                    option
                        .get("kind")
                        .and_then(Value::as_str)
                        .is_some_and(|kind| kind.replace('_', "").eq_ignore_ascii_case("allowonce"))
                })
                .and_then(|option| option.get("optionId").and_then(Value::as_str))
                .map(str::to_owned),
            Self::AllowThenDeny | Self::Deny | Self::Ignore => None,
        }
    }
}

/// One decision Lemma sent, and the run's visible state at that moment.
#[derive(Clone, Debug)]
pub struct DecisionSnapshot {
    /// The request this decision named.
    pub request_id: String,
    /// The option selected, or `None` for a denial.
    pub option_id: Option<String>,
    /// Assistant text streamed so far.
    pub assistant_text: String,
    /// Whether the run had already ended.
    pub saw_terminal: bool,
}

#[derive(Clone)]
pub struct ControlPlane {
    pub base_url: url::Url,
    pub host_id: Uuid,
    pub user_id: Uuid,
    pub run_id: Uuid,
    pub(crate) state: ControlState,
}

#[derive(Clone)]
pub(crate) struct ControlState {
    pub(crate) host_id: Uuid,
    pub(crate) user_id: Uuid,
    pub(crate) run_id: Uuid,
    pub(crate) harness_key: String,
    pub(crate) prompt: String,
    pub(crate) mcp: Value,
    pub(crate) permission_answer: PermissionAnswer,
    pub(crate) published: Arc<Mutex<Option<(Uuid, String)>>>,
    pub(crate) start_sent: Arc<AtomicBool>,
    /// The `START_RUN` we are offering, until the host acknowledges it.
    ///
    /// A real control plane redelivers a command until it comes back in a
    /// poll's `acknowledged_command_ids`; this used to be a one-shot bool, marked the moment
    /// the command was written into a response body. A response that never
    /// arrived -- a dropped connection, a poll cancelled under load -- took the
    /// run with it, permanently: nothing re-offered it, so the test waited its
    /// whole 90s for events from a run that was never started. That is the
    /// `published=Some(..), start_sent=true, events=[]` failure.
    pub(crate) start_command: Arc<Mutex<Option<Command>>>,
    /// Drop the first response that carries a command, as a lost one would be.
    pub(crate) drop_first_command: Arc<AtomicBool>,
    /// Streamed text that, once seen, makes the next poll cancel the run.
    ///
    /// Keyed on the agent's own output so the cancel lands mid-turn, while the
    /// adapter is genuinely working, rather than racing the run's start.
    pub(crate) cancel_after: Arc<Mutex<Option<String>>>,
    pub(crate) cancel_sent: Arc<AtomicBool>,
    /// Streamed text that, once seen, makes the next poll hand the run a
    /// replacement Lemma MCP configuration.
    pub(crate) refresh_after: Arc<Mutex<Option<(String, Value)>>>,
    pub(crate) refresh_sent: Arc<AtomicBool>,
    /// Request ids already answered, so a decision is sent exactly once.
    pub(crate) answered: Arc<Mutex<std::collections::HashSet<String>>>,
    /// The run as it stood the instant each decision was sent.
    ///
    /// A denial is the same observable outcome as a host that never asked
    /// anyone, which makes "the agent was denied" worthless on its own. A host
    /// that answered its adapter by itself will already have streamed the
    /// outcome, and usually finished the whole run, before Lemma decided.
    pub(crate) decisions: Arc<Mutex<Vec<DecisionSnapshot>>>,
    pub(crate) events: Arc<Mutex<Vec<Event>>>,
    pub(crate) lose_append_ack: Arc<AtomicBool>,
    pub(crate) append_attempts: Arc<Mutex<Vec<Vec<u64>>>>,
    pub(crate) run_budget: Arc<Mutex<chrono::Duration>>,
    pub(crate) workspace_cwd: Arc<Mutex<Option<String>>>,
    pub(crate) snapshots: Arc<Mutex<Vec<Value>>>,
    /// Commands the host refused, and why.
    ///
    /// The poll body carries these and this double used to take no body at
    /// all, so the one field that explains a run which never starts was
    /// discarded on arrival. A `START_RUN` rejected as `HARNESS_NOT_FOUND` is
    /// permanent -- `retryable: false`, and this double sends `START_RUN`
    /// exactly once -- and presented as a 90-second wait for a terminal event
    /// that was never coming, with nothing anywhere saying why.
    pub(crate) rejections: Arc<Mutex<Vec<Value>>>,
    /// The id assigned to each harness key, once and for the life of this
    /// control plane.
    ///
    /// `agent_host_harnesses` is unique on `(host_id, harness_key)` -- see
    /// `uq_agent_host_harness_key` -- so the real backend upserts a row and a
    /// harness keeps one id for as long as the host does. This double used to
    /// mint a fresh `Uuid::new_v4()` per snapshot on *every* publish, which is
    /// the whole of an intermittent 90-second hang:
    ///
    /// The host re-publishes whenever a harness changes state. `published` was
    /// then updated to an id the host had not yet been told about -- the
    /// publish response carrying it was still in flight -- and a poll landing
    /// in that window sent `START_RUN` naming it. `handle_start` looks the id
    /// up in the map built from the *last* response, does not find it, and
    /// fails with "command references an unknown harness".
    ///
    /// Which is permanent. `command_rejection` classifies it as
    /// `HARNESS_NOT_FOUND` with `retryable: false`, and `start_sent` means this
    /// double sends `START_RUN` exactly once -- so nothing was ever going to
    /// arrive after it, and the test waited out its full 90 seconds for a
    /// terminal event with `events=[]`.
    ///
    /// Measured at a 5ms window, hit whenever an adapter install happened to
    /// finish inside it.
    pub(crate) harness_ids: Arc<Mutex<BTreeMap<String, Uuid>>>,
    /// Where the host process writes its own log, once one is running.
    ///
    /// Registered by `HostProcess::start` so a timeout can quote it. The tests
    /// keep the host in a `TempDir` that unwinding deletes, so by the time a
    /// panic reaches a human the log is already gone.
    pub(crate) host_log: Arc<Mutex<Option<PathBuf>>>,
    pub(crate) scripted_traffic: Arc<Mutex<Option<PathBuf>>>,
}

impl ControlPlane {
    /// # Panics
    /// If the listener cannot bind.
    pub async fn start(
        harness_key: &str,
        prompt: &str,
        mcp: Value,
        permission_answer: PermissionAnswer,
    ) -> Self {
        let state = ControlState {
            host_id: Uuid::new_v4(),
            user_id: Uuid::new_v4(),
            run_id: Uuid::new_v4(),
            harness_key: harness_key.to_owned(),
            prompt: prompt.to_owned(),
            mcp,
            permission_answer,
            published: Arc::new(Mutex::new(None)),
            start_sent: Arc::new(AtomicBool::new(false)),
            start_command: Arc::new(Mutex::new(None)),
            drop_first_command: Arc::new(AtomicBool::new(false)),
            cancel_after: Arc::new(Mutex::new(None)),
            cancel_sent: Arc::new(AtomicBool::new(false)),
            refresh_after: Arc::new(Mutex::new(None)),
            refresh_sent: Arc::new(AtomicBool::new(false)),
            answered: Arc::new(Mutex::new(std::collections::HashSet::new())),
            decisions: Arc::new(Mutex::new(Vec::new())),
            events: Arc::new(Mutex::new(Vec::new())),
            lose_append_ack: Arc::new(AtomicBool::new(false)),
            append_attempts: Arc::new(Mutex::new(Vec::new())),
            run_budget: Arc::new(Mutex::new(chrono::Duration::minutes(3))),
            workspace_cwd: Arc::new(Mutex::new(None)),
            snapshots: Arc::new(Mutex::new(Vec::new())),
            rejections: Arc::new(Mutex::new(Vec::new())),
            harness_ids: Arc::new(Mutex::new(BTreeMap::new())),
            host_log: Arc::new(Mutex::new(None)),
            scripted_traffic: Arc::new(Mutex::new(None)),
        };
        let app = Router::new()
            .route("/agent-host/pairings/complete", post(pairing))
            .route("/agent-host/harnesses", put(publish))
            .route("/agent-host/poll", post(poll))
            .route("/agent-host/events/append", post(append_events))
            .with_state(state.clone());
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        tokio::spawn(async move {
            let _ = axum::serve(listener, app).await;
        });
        Self {
            base_url: url::Url::parse(&format!("http://{address}/")).unwrap(),
            host_id: state.host_id,
            user_id: state.user_id,
            run_id: state.run_id,
            state,
        }
    }

    /// Cancel the run on the first poll after `marker` appears in its output.
    ///
    /// # Panics
    /// If the mutex is poisoned.
    /// Arm the stub to lose the first response that carries a command.
    pub fn lose_the_first_command(&self) {
        self.state.drop_first_command.store(true, Ordering::SeqCst);
    }

    pub fn lose_the_first_append_ack(&self) {
        self.state.lose_append_ack.store(true, Ordering::SeqCst);
    }

    pub fn append_attempts(&self) -> Vec<Vec<u64>> {
        self.state.append_attempts.lock().unwrap().clone()
    }

    pub fn set_run_budget(&self, budget: chrono::Duration) {
        *self.state.run_budget.lock().unwrap() = budget;
    }

    pub fn set_workspace_cwd(&self, cwd: &str) {
        *self.state.workspace_cwd.lock().unwrap() = Some(cwd.to_owned());
    }

    pub fn cancel_when_text_contains(&self, marker: &str) {
        *self.state.cancel_after.lock().unwrap() = Some(marker.to_owned());
    }

    /// Replace the run's Lemma MCP configuration once `marker` is streamed.
    ///
    /// # Panics
    /// If the mutex is poisoned.
    pub fn refresh_credential_when_text_contains(&self, marker: &str, mcp: Value) {
        *self.state.refresh_after.lock().unwrap() = Some((marker.to_owned(), mcp));
    }

    /// # Panics
    /// If the event mutex is poisoned.
    #[must_use]
    pub fn events(&self) -> Vec<Event> {
        self.state.events.lock().unwrap().clone()
    }

    /// # Panics
    /// If the snapshot mutex is poisoned.
    #[must_use]
    pub fn published_snapshots(&self) -> Vec<Value> {
        self.state.snapshots.lock().unwrap().clone()
    }

    #[must_use]
    pub fn saw_terminal(&self) -> bool {
        self.events()
            .iter()
            .any(|event| event.event_type == EventType::Terminal)
    }

    /// The streamed assistant text the host reported.
    ///
    /// # Panics
    /// If the event mutex is poisoned.
    #[must_use]
    pub fn assistant_text(&self) -> String {
        assistant_text_of(&self.state.events.lock().unwrap())
    }

    /// Where the run stood as each permission decision was sent.
    ///
    /// Empty until a decision is sent. This is what tells a denial Lemma made
    /// apart from a host that denies everything on its own: in the latter case
    /// the agent has already been answered, and normally the run has already
    /// finished, before Lemma gets a word in.
    ///
    /// # Panics
    /// If the recording mutex is poisoned.
    #[must_use]
    pub fn decisions(&self) -> Vec<DecisionSnapshot> {
        self.state.decisions.lock().unwrap().clone()
    }

    /// Commands the host refused, and why.
    ///
    /// # Panics
    /// If the rejection mutex is poisoned.
    #[must_use]
    pub fn rejections(&self) -> Vec<Value> {
        self.state.rejections.lock().unwrap().clone()
    }

    /// Tell this control plane where the host writes its log, so a timeout can
    /// quote it.
    ///
    /// # Panics
    /// If the mutex is poisoned.
    pub fn watch_host_log(&self, path: &Path) {
        *self.state.host_log.lock().unwrap() = Some(path.to_path_buf());
    }

    #[must_use]
    pub fn permission_requests(&self) -> Vec<Event> {
        self.events()
            .into_iter()
            .filter(|event| event.event_type == EventType::PermissionRequest)
            .collect()
    }

    /// Poll until `predicate` holds, panicking with the collected state on
    /// timeout so a failure says what the host actually did.
    ///
    /// # Panics
    /// On timeout.
    pub async fn wait_for(&self, what: &str, timeout: Duration, predicate: impl Fn(&Self) -> bool) {
        let deadline = tokio::time::Instant::now() + timeout;
        while tokio::time::Instant::now() < deadline {
            if predicate(self) {
                return;
            }
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
        let kinds = self
            .events()
            .iter()
            .map(|event| format!("{:?}", event.event_type))
            .collect::<Vec<_>>();
        let published = self.state.published.lock().unwrap().clone();
        panic!(
            "timed out waiting for {what}; published={published:?}, \
             start_sent={}, events={kinds:?}{}{}{}",
            self.state.start_sent.load(Ordering::SeqCst),
            self.rejection_summary(),
            self.host_log_tail(),
            self.scripted_traffic_tail(),
        );
    }

    /// What the host refused, if anything, phrased as the answer to "why is
    /// this run not running".
    fn rejection_summary(&self) -> String {
        let rejections = self.rejections();
        if rejections.is_empty() {
            return String::new();
        }
        let described = rejections
            .iter()
            .map(|rejection| {
                format!(
                    "{} (retryable={}) {}",
                    rejection["code"].as_str().unwrap_or("?"),
                    rejection["retryable"].as_bool().unwrap_or(false),
                    rejection["detail"].as_str().unwrap_or(""),
                )
            })
            .collect::<Vec<_>>();
        format!(
            "\n  the host REFUSED {} command(s): {described:?}\n  \
             A refusal with retryable=false is permanent, and this control \
             plane sends START_RUN exactly once, so nothing was ever going to \
             arrive after it.",
            rejections.len(),
        )
    }

    /// The last of the host's own log.
    ///
    /// Only the tail: these logs run to thousands of lines at
    /// `lemma_agent_host=debug`, and a panic message nobody can read is worth
    /// about as much as the one that said nothing.
    fn host_log_tail(&self) -> String {
        const LINES: usize = 40;
        let path = self.state.host_log.lock().unwrap().clone();
        let Some(path) = path else {
            return String::new();
        };
        let Ok(log) = std::fs::read_to_string(&path) else {
            return format!("\n  (no host log at {})", path.display());
        };
        let lines = log.lines().collect::<Vec<_>>();
        let tail = lines[lines.len().saturating_sub(LINES)..].join("\n    ");
        format!("\n  last {LINES} lines of the host log:\n    {tail}")
    }

    fn scripted_traffic_tail(&self) -> String {
        let path = self.state.scripted_traffic.lock().unwrap().clone();
        let Some(path) = path else {
            return String::new();
        };
        let log = std::fs::read_to_string(path).unwrap_or_default();
        // Keep wire ordering after TempDir cleanup without including prompts,
        // MCP credentials, or tool payloads in a test failure.
        let messages = log.lines().rev().take(40).collect::<Vec<_>>();
        let summary = messages
            .into_iter()
            .rev()
            .filter_map(|line| {
                let entry: Value = serde_json::from_str(line).ok()?;
                let message = &entry["message"];
                Some(json!({
                    "direction": entry["direction"],
                    "id": message["id"],
                    "method": message["method"],
                    "result": message.get("result").is_some(),
                    "error_code": message["error"]["code"],
                    "update": message["params"]["update"]["sessionUpdate"],
                }))
            })
            .collect::<Vec<_>>();
        format!("\n  scripted ACP wire summary: {summary:?}")
    }
}
