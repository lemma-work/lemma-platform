use super::*;

/// Take a lock without letting one panic take the whole app down with it.
///
/// A poisoned mutex means some thread panicked while holding it. With
/// `lock().unwrap()` -- the shell's idiom at fifty-one sites -- every later
/// lock of the same mutex panics too, so one fault on a background thread
/// became a crash on the next menu refresh, tray update or quit. Everything
/// behind these locks is display state or a `Mutex<()>` used for exclusion;
/// continuing with the last value written is always better than closing the
/// window on the user. The agent-host has recovered this way throughout.
pub(crate) trait LockOrRecover<T> {
    fn lock_or_recover(&self) -> std::sync::MutexGuard<'_, T>;
}

impl<T> LockOrRecover<T> for std::sync::Mutex<T> {
    fn lock_or_recover(&self) -> std::sync::MutexGuard<'_, T> {
        self.lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
    }
}

#[derive(Clone, Serialize, Default)]
#[serde(rename_all = "camelCase")]
pub(crate) struct UiState {
    pub(crate) status: String,
    pub(crate) error_code: String,
    pub(crate) phase: String,
    pub(crate) phase_key: String,
    pub(crate) progress: u64,
    pub(crate) eta_seconds: Option<u64>,
    pub(crate) downloaded_bytes: Option<u64>,
    pub(crate) total_bytes: Option<u64>,
    pub(crate) throughput_bytes_per_second: Option<u64>,
    pub(crate) setup: bool,
    pub(crate) error: bool,
    pub(crate) ready: bool,
    pub(crate) running: bool,
    pub(crate) mode: String,
    pub(crate) url: String,
    pub(crate) api_url: String,
    pub(crate) log_source: String,
    pub(crate) component: String,
    /// The sandbox image warm-up, which runs behind a ready workspace rather
    /// than as a phase of starting one. `pending`, `downloading`, `ready`, or
    /// `failed`.
    pub(crate) sandbox_images: String,
    pub(crate) sandbox_images_detail: String,
    #[serde(skip)]
    pub(crate) active_operation_id: String,
    #[serde(skip)]
    pub(crate) completed_operation_ids: Vec<String>,
    #[serde(skip)]
    pub(crate) terminal_recovery_pending: bool,
    /// Whether this launch had to install a runtime before it could start.
    ///
    /// Only for the readiness measurement: a first run and a warm start take
    /// wildly different times, and a number that mixes them says nothing.
    #[serde(skip)]
    pub(crate) installed_this_launch: bool,
}

/// What a broken installation can still be offered.
#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct RecoveryOptions {
    pub(crate) data_reset_available: bool,
    pub(crate) full_reinstall_available: bool,
    pub(crate) installed_runtime_release: Option<String>,
    /// Allocated, not apparent. `data.raw` is sparse and always reports 24 GiB,
    /// so reporting its length would promise every user 24 GiB back.
    pub(crate) data_disk_allocated_bytes: u64,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct RuntimeInfo {
    pub(crate) desktop_release: String,
    pub(crate) active_release: Option<String>,
    pub(crate) previous_release: Option<String>,
    pub(crate) source: String,
    pub(crate) rollback_available: bool,
    pub(crate) repair_available: bool,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DiagnosticLogSource {
    pub(crate) id: String,
    pub(crate) label: String,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DiagnosticLogSnapshot {
    pub(crate) sources: Vec<DiagnosticLogSource>,
    pub(crate) source: String,
    pub(crate) entries: String,
    pub(crate) next_cursor: String,
}

pub(crate) struct Shell {
    pub(crate) ui: Mutex<UiState>,
    /// Where a message to the daemon is handed off, not written.
    ///
    /// It used to be the socket itself, written to under this lock. A daemon
    /// that stops reading -- wedged, paused, mid-crash -- fills the socket
    /// buffer, and the `write` that fills it blocks. Holding the lock, so
    /// every other caller blocks behind it: the tray, the quit path, the
    /// status poll, all of them waiting on a process that is never going to
    /// read again.
    ///
    /// The channel is bounded, so a daemon that has stopped reading is
    /// reported rather than absorbed. `Some` still means connected, which is
    /// what the rest of the shell asks this field.
    pub(crate) locald_writer: Mutex<Option<mpsc::SyncSender<String>>>,
    pub(crate) locald_connect: Mutex<()>,
    /// Installing the runtime is single-flighted separately from connecting
    /// to the daemon. It used to share `locald_connect`, which meant one
    /// caller's multi-hundred-megabyte download blocked every other caller
    /// -- including Local settings' heartbeat -- for the whole install.
    pub(crate) runtime_install: Mutex<()>,
    pub(crate) recovery_running: AtomicBool,
    pub(crate) confirmations: confirmation::Confirmations,
    pub(crate) recovery_mode: AtomicBool,
    pub(crate) quit_after_stop: AtomicBool,
    pub(crate) shutdown: shutdown::Shutdown,
    // The tray is built once, so its Agent Host entries are kept here to be
    // rewritten as status arrives.
    /// The tray's Agent Host line. A label, never a control: the toggle beside
    /// it was the off switch, and it is gone.
    pub(crate) tray_agent_host: Mutex<Option<MenuItem<tauri::Wry>>>,
    /// The tray's one-line answer to "is Lemma up?", so that question does not
    /// require opening the app to find out.
    pub(crate) tray_status: Mutex<Option<MenuItem<tauri::Wry>>>,
    // locald answers asynchronously on the event stream, but the workspace page
    // calls a command and expects a value back. The latest status is kept here
    // so a caller gets an answer immediately and the next poll sees the update.
    pub(crate) agent_host_status: Mutex<Option<Value>>,
    /// The sharing mode locald last reported, so Quit can name what it is about
    /// to take away. Read on the quit path, which must not wait on the daemon:
    /// asking for a fresh snapshot there would mean a round trip in front of a
    /// keystroke, and a stack too sick to answer is exactly when someone quits.
    pub(crate) sharing_mode: Mutex<Option<String>>,
    /// Set once the user has answered the quit prompt, so the exit that follows
    /// is not asked about again. Every route out funnels through
    /// `ExitRequested`, including the `app.exit` the confirmed path issues
    /// itself; without this the prompt would re-arm and the app could not leave.
    pub(crate) quit_confirmed: AtomicBool,
    /// Set while the main window is being swapped onto another server's
    /// storage. Destroying the only window is indistinguishable from closing
    /// the last one, so without this the swap raises `ExitRequested` -- which
    /// on an idle app has nothing to warn about and simply lets it exit, and on
    /// a running one puts a "quit?" prompt in front of a user who asked to
    /// change servers.
    pub(crate) swapping_window: AtomicBool,
    /// Set once this shell has sent the daemon `shutdown-daemon` for a quit.
    /// "Quit Anyway" then waits on that stop and escalates it, rather than
    /// sending a second request the daemon refuses as already in progress.
    pub(crate) daemon_stop_requested: AtomicBool,
    /// Local workspace origins granted the workspace capability this run.
    ///
    /// Granted one exact origin at a time, at runtime, because the port is
    /// only known once locald has allocated it -- and a `:*` pattern in the
    /// shipped file would also cover every pod-app alias port on the same
    /// host. Remembered so each is added once.
    pub(crate) granted_workspace_origins: Mutex<BTreeSet<String>>,
    /// Pod-app alias ports this run has handed the workspace, and the
    /// canonical app origin each fronts. Only these may load in a frame on the
    /// workspace host; see `pod_app_alias.rs`.
    pub(crate) app_aliases: Mutex<HashMap<u16, String>>,
}

pub(crate) struct LocaldConnection {
    pub(crate) reader: BufReader<RecvHalf>,
    pub(crate) writer: SendHalf,
    pub(crate) hello: Value,
}

impl Shell {
    pub(crate) fn new(mode: String) -> Self {
        let ui = UiState {
            status: "Waiting".into(),
            phase: "Booting local services".into(),
            phase_key: "boot".into(),
            progress: 4,
            mode,
            ..Default::default()
        };
        Shell {
            ui: Mutex::new(ui),
            locald_writer: Mutex::new(None),
            locald_connect: Mutex::new(()),
            runtime_install: Mutex::new(()),
            recovery_running: AtomicBool::new(false),
            confirmations: confirmation::Confirmations::default(),
            recovery_mode: AtomicBool::new(false),
            quit_after_stop: AtomicBool::new(false),
            shutdown: shutdown::Shutdown::default(),
            tray_agent_host: Mutex::new(None),
            tray_status: Mutex::new(None),
            agent_host_status: Mutex::new(None),
            sharing_mode: Mutex::new(None),
            quit_confirmed: AtomicBool::new(false),
            swapping_window: AtomicBool::new(false),
            daemon_stop_requested: AtomicBool::new(false),
            granted_workspace_origins: Mutex::new(BTreeSet::new()),
            app_aliases: Mutex::new(HashMap::new()),
        }
    }
}

/// When this process started, for the launch trace to measure against.
pub(crate) static LAUNCH_START: std::sync::OnceLock<Instant> = std::sync::OnceLock::new();

/// What the last session left running, so the next launch can go straight to it.
///
/// Without this every launch is identical to a cold one: splash, a `start`
/// round trip through the daemon, then a navigation to the workspace — even
/// when the backend and frontend never stopped serving. The recorded generation
/// and release are what make trusting it safe; see [`resume_target_is_serving`].
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct ResumeTarget {
    pub(crate) url: String,
    pub(crate) api_url: String,
    pub(crate) generation: String,
    /// The Desktop release that recorded this. A newer app must not resume it.
    pub(crate) release: String,
    pub(crate) route: String,
}

/// What the caller must do once the state has been folded.
#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub(crate) struct EventOutcome {
    /// A terminal error just appeared, and recovery options should be fetched.
    pub(crate) schedule_terminal_recovery: bool,
    /// The runtime finished preparing, so the stack should be started.
    pub(crate) start_after_prepare: bool,
    /// This launch just became usable, so its time-to-ready is recorded.
    pub(crate) became_ready: Option<ReadyReached>,
    /// What is serving now, for the next launch to resume straight into.
    pub(crate) resume_write: Option<ResumeWrite>,
}

/// How long a launch took to become usable, and whether it installed anything.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct ReadyReached {
    pub(crate) cached: bool,
    pub(crate) duration_ms: u64,
}

/// A resume target to record: what is serving now, and under which generation.
///
/// Not [`ResumeTarget`], which is the stored shape and carries the release and
/// route that `write_resume_target` fills in itself.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct ResumeWrite {
    pub(crate) url: String,
    pub(crate) api_url: String,
    pub(crate) generation: String,
}

/// What the app knows about a newer version, if anything.
#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct AppUpdateStatus {
    pub(crate) channel: &'static str,
    pub(crate) current_version: &'static str,
    pub(crate) build_commit: Option<&'static str>,
    /// False for a development build, or one with no updater key. Nightly is
    /// *not* excluded -- it updates to nightly on its own feed, which is what
    /// keeps the mechanism exercised between releases. The UI explains why
    /// rather than silently omitting the control.
    pub(crate) updates_supported: bool,
    pub(crate) available_version: Option<String>,
    /// Bytes of runtime the *next* launch downloads after an app update, read
    /// from the feed rather than guessed. An app update is ~24 MB; the runtime
    /// that follows is two orders of magnitude larger, and saying so before the
    /// user commits is the difference between a considered choice and a
    /// surprise.
    pub(crate) runtime_download_bytes: Option<u64>,
    /// Whether installing keeps this installation's data usable. See
    /// `LemmaUpdateMetadata::compatibility_with`.
    pub(crate) data_compatibility: &'static str,
    /// The Postgres majors behind a `postgres-major-change`, so the UI can say
    /// which change it is refusing rather than that it is refusing.
    pub(crate) installed_postgres_major: Option<u64>,
    pub(crate) candidate_postgres_major: Option<u64>,
}

/// The `lemma` block a release feed carries alongside the standard fields.
#[derive(Default)]
pub(crate) struct LemmaUpdateMetadata {
    pub(crate) postgres_major: Option<u64>,
    pub(crate) runtime_download_bytes: Option<u64>,
}

impl LemmaUpdateMetadata {
    /// Whether this update leaves the installation's data usable.
    ///
    /// Everything Lemma keeps is a Postgres data directory and a folder of
    /// files, and schema changes are migrations that run on the next start.
    /// The one change a migration cannot carry is a new Postgres *major*: it
    /// cannot open a data directory another major wrote, and Lemma ships no
    /// `pg_upgrade` step. So that, and only that, is refused.
    ///
    /// Not knowing one side is not evidence of a change, so it does not block.
    /// Even the refused case destroys nothing: Postgres will not start on a
    /// foreign data directory, and the previous runtime stays on disk.
    pub(crate) fn compatibility_with(&self, installed: Option<u64>) -> &'static str {
        match (installed, self.postgres_major) {
            (Some(installed), Some(candidate)) if installed != candidate => "postgres-major-change",
            _ => "compatible",
        }
    }
}

/// What last launch's update attempt turned out to be.
#[derive(Debug, PartialEq, Eq)]
pub(crate) enum UpdateAttempt {
    /// Nothing was attempted.
    None,
    /// The version it was aiming at is the one now running.
    Landed { to: String },
    /// Still on the version it started from: the installer never replaced the
    /// app. Cancelled at the UAC prompt, refused, or interrupted.
    DidNotLand { to: String },
    /// A record that explains nothing about the version now running -- damaged,
    /// or left by an install that has since been replaced by a third version.
    Unexplained,
}

/// Build the one window the app has, against the storage its server owns.
///
/// Extracted from `setup` so a server switch can rebuild it. Everything
/// here has to be re-applied on a rebuild, not just on first launch: the
/// navigation and download policies, the theme and accent listeners, the
/// vibrancy material, and the initialization script that carries the
/// desktop context into every document the window later navigates to.
/// Where a window was, so the one replacing it can be there too.
///
/// A rebuilt window used to come back at the OS default placement in the default
/// size, because nothing carried this across. Moving somebody's window is not a
/// thing switching servers is entitled to do.
#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct WindowPlacement {
    pub(crate) position: tauri::PhysicalPosition<i32>,
    pub(crate) size: tauri::PhysicalSize<u32>,
}

/// Clears the swap flag however the rebuild ends, including on an early
/// return. A flag left set would make the app unquittable.
pub(crate) struct ExitGuard(pub(crate) AppHandle);

impl Drop for ExitGuard {
    fn drop(&mut self) {
        let shell: State<Shell> = self.0.state();
        shell.swapping_window.store(false, Ordering::Release);
    }
}

#[derive(Debug, PartialEq, Eq)]
pub(crate) enum NavigationDisposition {
    Allow,
    OpenExternal,
    Deny,
}

#[derive(Debug, PartialEq, Eq)]
pub(crate) enum NewWindowDisposition {
    NavigateInApp,
    /// A published pod app, which gets a window of its own rather than taking
    /// over the one Lemma is running in.
    OpenAppWindow,
    OpenExternal,
    Deny,
}

/// The last accent AppKit was asked for, and the only answer anything off the
/// main thread is allowed to see.
#[cfg(target_os = "macos")]
pub(crate) static REMEMBERED_ACCENT: Mutex<Option<(u8, u8, u8)>> = Mutex::new(None);

#[tauri::command]
pub(crate) fn get_state(app: AppHandle) -> UiState {
    let shell: State<Shell> = app.state();
    let snapshot = shell.ui.lock_or_recover().clone();
    snapshot
}
