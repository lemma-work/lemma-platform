// Lemma desktop shell: thin Tauri client for the durable local daemon.
//
// The shell owns native chrome (window, tray, menus); lemma-locald owns service
// lifecycle. Managed releases use native host packs and private runtime
// providers; the daemon retains an unbundled compatibility adapter only for
// development and existing external-runtime installations.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::Serialize;
mod config_store;
mod confirmation;
mod ipc_read;
mod native_assets;
mod recovery;
mod shutdown;
mod update_policy;
use recovery::RecoveryOutcome;
use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Read, Seek, SeekFrom, Write};
use std::net::IpAddr;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant};
use tauri::menu::{AboutMetadata, CheckMenuItem, Menu, MenuItem, PredefinedMenuItem, Submenu};
use tauri::tray::TrayIconBuilder;
use tauri::webview::DownloadEvent;
use tauri::webview::NewWindowResponse;
use tauri::webview::WebviewBuilder;
use tauri::{
    AppHandle, Emitter, Manager, PhysicalPosition, State, Webview, WebviewUrl, WebviewWindowBuilder,
};
use tauri_plugin_autostart::ManagerExt as _;
use tauri_plugin_updater::UpdaterExt as _;

mod artifact_install;

#[cfg(unix)]
use interprocess::local_socket::GenericFilePath;
#[cfg(windows)]
use interprocess::local_socket::GenericNamespaced;
use interprocess::local_socket::{prelude::*, Name, RecvHalf, SendHalf};

const DEFAULT_HOSTED_URL: &str = "https://lemma.work";
/// Port `cargo tauri dev` serves `frontendDist` on. A packaged build has no
/// equivalent — Tauri serves the bundled files through its native asset protocol.
const DEV_ASSET_PORT: u16 = 1430;
const MAX_INSTALL_LOG_BYTES: u64 = 1024 * 1024;
// Must match locald's handshake revision. This prevents a newly installed
// Desktop hotfix from silently reusing an older durable daemon with the same
// public release number.
const REQUIRED_LOCALD_API_REVISION: u64 = 5;
// Legacy development builds persisted a mode before the released chooser
// contract was stable. Require that chooser once, then retain the new choice.
const CONNECTION_MODE_PROMPT_REVISION: u64 = 1;
/// How long a full quit may wait for locald to close LAN/public exposure.
///
/// This runs on the main thread from `RunEvent::Exit`, after the webviews are
/// gone, so every second of it is a dead window on the user's screen. The
/// daemon is durable and owns its own cleanup; the shell asking nicely is a
/// courtesy, not a guarantee, and it must not be able to hold the app open.
const RELEASE_ON_EXIT_TIMEOUT: Duration = Duration::from_secs(5);
/// How long a launch may spend asking whether the last session's workspace is
/// still serving. A miss costs this much and then falls back to the splash, so
/// it has to stay far below what the splash path would have cost anyway.
const RESUME_PROBE_TIMEOUT: Duration = Duration::from_millis(250);
/// `--bg-canvas`, the frontend's paper. Any frame where the webview is not
/// painting — navigation, hide/show, teardown — shows the window layer instead,
/// and an unset window layer on macOS is black.
const CANVAS_LIGHT: tauri::window::Color = tauri::window::Color(242, 239, 231, 255);
const CANVAS_DARK: tauri::window::Color = tauri::window::Color(19, 19, 17, 255);

#[derive(Clone, Serialize, Default)]
#[serde(rename_all = "camelCase")]
struct UiState {
    status: String,
    error_code: String,
    phase: String,
    phase_key: String,
    progress: u64,
    eta_seconds: Option<u64>,
    downloaded_bytes: Option<u64>,
    total_bytes: Option<u64>,
    throughput_bytes_per_second: Option<u64>,
    setup: bool,
    error: bool,
    ready: bool,
    running: bool,
    mode: String,
    url: String,
    api_url: String,
    log_source: String,
    component: String,
    /// The sandbox image warm-up, which runs behind a ready workspace rather
    /// than as a phase of starting one. `pending`, `downloading`, `ready`, or
    /// `failed`.
    sandbox_images: String,
    sandbox_images_detail: String,
    #[serde(skip)]
    active_operation_id: String,
    #[serde(skip)]
    completed_operation_ids: Vec<String>,
    #[serde(skip)]
    terminal_recovery_pending: bool,
}

/// Which build this is, for support and for whether it self-updates.
///
/// A compile-time stamp rather than a version suffix. A suffix would have to
/// travel through the runtime manifest, the per-version install directory and
/// the branch-test version gate, none of which care which channel a build came
/// from. This answers "what are you running?" -- which had no answer at all,
/// because a nightly and a release both report `0.7.0` with the same bundle
/// identifier.
///
/// Unrecognised stamps are development builds and cannot update themselves.
fn release_channel() -> &'static str {
    channel_of(option_env!("LEMMA_RELEASE_CHANNEL"))
}

/// The channel a build stamp names, or `dev`.
///
/// Split out so the mapping can be asserted. Testing `release_channel()`
/// directly proves nothing: it reads a compile-time stamp that a test build
/// never has, so every assertion about it holds by construction.
fn channel_of(stamp: Option<&str>) -> &'static str {
    match stamp {
        Some("stable") => "stable",
        Some("nightly") => "nightly",
        _ => "dev",
    }
}

fn build_commit() -> Option<&'static str> {
    option_env!("LEMMA_BUILD_SHA")
}

/// Whether this build may update itself in place.
///
/// Stable and nightly release builds use separate feeds and the same signed
/// update mechanism. Local development builds cannot replace themselves.
fn updates_enabled() -> bool {
    updates_allowed(
        release_channel(),
        cfg!(debug_assertions),
        updater_key_configured(),
    )
}

/// Whether a build with these three properties may update itself.
///
/// A debug build is not a release, and a build with no public key cannot verify
/// what it downloads. Both remain disqualifying.
///
/// Nightly used to be, on two grounds stated here: it had no durable feed and
/// was never given the signing key. Both are now false. Nightly builds are
/// signed with the same key as a release -- so the committed public key
/// verifies them, and `key_configured` is a real check for them too -- and they
/// publish to a tag that does not move, which is what makes a feed durable.
///
/// The point is not convenience. An update path nobody walks until release day
/// is an update path nobody has tested; letting nightly update to nightly means
/// the mechanism is exercised continuously, by people who can report what broke
/// rather than discovering it in a stable rollout.
fn updates_allowed(channel: &str, debug: bool, key_configured: bool) -> bool {
    matches!(channel, "stable" | "nightly") && !debug && key_configured
}

/// `updater_endpoints` for this build, parsed, for the plugin's builder.
///
/// A malformed constant here would be a build-time mistake, not a runtime
/// condition, so an unparseable entry is dropped rather than failing the check
/// -- leaving the plugin with whatever `tauri.conf.json` configured, which is
/// the stable feed.
fn parsed_updater_endpoints() -> Vec<tauri::Url> {
    updater_endpoints(release_channel())
        .into_iter()
        .filter_map(|endpoint| tauri::Url::parse(&endpoint).ok())
        .collect()
}

/// Where this build looks for its update feed.
///
/// Stable reads the `latest` release, which GitHub resolves to the newest
/// non-prerelease. Nightlies are prereleases and never appear there, so a
/// nightly pointed at it would either see nothing or be offered a *stable*
/// build -- neither of which tests anything. They read a tag that is rewritten
/// in place instead, so the address stays constant while its contents move.
///
/// Returned rather than baked into `tauri.conf.json` because the config is one
/// file shared by every build, and the channel is a compile-time stamp. One
/// place decides, and the tests below can ask it.
fn updater_endpoints(channel: &str) -> Vec<String> {
    const OWNER_REPO: &str = "lemma-work/lemma-platform";
    match channel {
        "nightly" => vec![format!(
            "https://github.com/{OWNER_REPO}/releases/download/desktop-nightly/latest.json"
        )],
        _ => vec![format!(
            "https://github.com/{OWNER_REPO}/releases/latest/download/latest.json"
        )],
    }
}

/// Whether this build carries a public key that can verify an update.
///
/// `tauri.conf.json` ships `"pubkey": ""` until the signing keypair exists, and
/// an empty key is not a permissive setting -- `verify_signature` decodes it and
/// fails, so *every* install fails. Without this check the app offers an update,
/// stops the user's daemon to make room for it, and only then discovers it
/// cannot verify a thing.
///
/// Read from the committed config at compile time, so a build either has a key
/// or does not; there is nothing to get out of sync at runtime.
fn updater_key_configured() -> bool {
    static CONFIGURED: std::sync::OnceLock<bool> = std::sync::OnceLock::new();
    *CONFIGURED.get_or_init(|| {
        serde_json::from_str::<Value>(include_str!("../tauri.conf.json"))
            .ok()
            .and_then(|config| {
                config
                    .pointer("/plugins/updater/pubkey")
                    .and_then(Value::as_str)
                    .map(|key| !key.trim().is_empty())
            })
            .unwrap_or(false)
    })
}

/// What a broken installation can still be offered.
#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct RecoveryOptions {
    data_reset_available: bool,
    full_reinstall_available: bool,
    installed_runtime_release: Option<String>,
    /// Allocated, not apparent. `data.raw` is sparse and always reports 24 GiB,
    /// so reporting its length would promise every user 24 GiB back.
    data_disk_allocated_bytes: u64,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct RuntimeInfo {
    desktop_release: String,
    active_release: Option<String>,
    previous_release: Option<String>,
    source: String,
    rollback_available: bool,
    repair_available: bool,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct DiagnosticLogSource {
    id: String,
    label: String,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct DiagnosticLogSnapshot {
    sources: Vec<DiagnosticLogSource>,
    source: String,
    entries: String,
    next_cursor: String,
}

struct Shell {
    ui: Mutex<UiState>,
    locald_writer: Mutex<Option<SendHalf>>,
    locald_connect: Mutex<()>,
    /// Installing the runtime is single-flighted separately from connecting
    /// to the daemon. It used to share `locald_connect`, which meant one
    /// caller's multi-hundred-megabyte download blocked every other caller
    /// -- including Local settings' heartbeat -- for the whole install.
    runtime_install: Mutex<()>,
    recovery_running: AtomicBool,
    confirmations: confirmation::Confirmations,
    recovery_mode: AtomicBool,
    quit_after_stop: AtomicBool,
    shutdown: shutdown::Shutdown,
    // The tray is built once, so its Agent Host entries are kept here to be
    // rewritten as status arrives.
    /// The tray's Agent Host line. A label, never a control: the toggle beside
    /// it was the off switch, and it is gone.
    tray_agent_host: Mutex<Option<MenuItem<tauri::Wry>>>,
    /// The tray's one-line answer to "is Lemma up?", so that question does not
    /// require opening the app to find out.
    tray_status: Mutex<Option<MenuItem<tauri::Wry>>>,
    // locald answers asynchronously on the event stream, but the workspace page
    // calls a command and expects a value back. The latest status is kept here
    // so a caller gets an answer immediately and the next poll sees the update.
    agent_host_status: Mutex<Option<Value>>,
    /// The sharing mode locald last reported, so Quit can name what it is about
    /// to take away. Read on the quit path, which must not wait on the daemon:
    /// asking for a fresh snapshot there would mean a round trip in front of a
    /// keystroke, and a stack too sick to answer is exactly when someone quits.
    sharing_mode: Mutex<Option<String>>,
    /// Set once the user has answered the quit prompt, so the exit that follows
    /// is not asked about again. Every route out funnels through
    /// `ExitRequested`, including the `app.exit` the confirmed path issues
    /// itself; without this the prompt would re-arm and the app could not leave.
    quit_confirmed: AtomicBool,
    /// Set while the main window is being swapped onto another server's
    /// storage. Destroying the only window is indistinguishable from closing
    /// the last one, so without this the swap raises `ExitRequested` -- which
    /// on an idle app has nothing to warn about and simply lets it exit, and on
    /// a running one puts a "quit?" prompt in front of a user who asked to
    /// change servers.
    swapping_window: AtomicBool,
}

struct LocaldConnection {
    reader: BufReader<RecvHalf>,
    writer: SendHalf,
    hello: Value,
}

impl Shell {
    fn new(mode: String) -> Self {
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
        }
    }
}

fn home_dir() -> PathBuf {
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
        .expect("HOME/USERPROFILE is not set")
}

/// The directory name this build keeps its data under.
///
/// "Lemma" for a real one. A candidate built for qualification sets
/// `LEMMA_DESKTOP_DATA_DIR_NAME` so it cannot share a data directory with the
/// installation already on the machine -- which would let it stop that
/// installation's daemon, adopt its runtime, and reset its pods. Baked in at
/// compile time, so the isolation travels with the artifact instead of
/// depending on how it was launched.
const DATA_DIR_NAME: &str = match option_env!("LEMMA_DESKTOP_DATA_DIR_NAME") {
    Some(name) => name,
    None => "Lemma",
};

fn app_support_dir() -> PathBuf {
    if let Some(path) = std::env::var_os("LEMMA_DESKTOP_APP_SUPPORT_DIR") {
        return PathBuf::from(path);
    }
    #[cfg(target_os = "macos")]
    {
        home_dir()
            .join("Library/Application Support")
            .join(DATA_DIR_NAME)
    }
    #[cfg(target_os = "windows")]
    {
        std::env::var_os("LOCALAPPDATA")
            .map(PathBuf::from)
            .unwrap_or_else(home_dir)
            .join(DATA_DIR_NAME)
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        std::env::var_os("XDG_STATE_HOME")
            .map(PathBuf::from)
            .unwrap_or_else(|| home_dir().join(".local/state"))
            .join("lemma")
    }
}

fn locald_root() -> PathBuf {
    std::env::var_os("LEMMA_LOCALD_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|| app_support_dir().join("locald"))
}

fn runtime_install_root() -> PathBuf {
    app_support_dir().join("runtime")
}

fn install_log_path() -> PathBuf {
    runtime_install_root().join("install.log")
}

fn launch_log_path() -> PathBuf {
    runtime_install_root().join("launch.log")
}

/// Where the daemon's own stderr goes.
///
/// Deliberately its own file rather than `install.log`. `append_bounded_log`
/// rotates by renaming, and a child holding an inherited descriptor keeps
/// writing to the renamed inode -- so sharing a file would silently split the
/// record exactly when someone is reading it.
fn locald_stderr_path() -> PathBuf {
    runtime_install_root().join("locald-stderr.log")
}

/// A sink for locald's stderr that cannot block the daemon.
///
/// This used to be `Stdio::null()`, which meant every fatal `Daemon::new`
/// failure -- a malformed control token, an unreadable operator config, a port
/// that could not be reserved -- was discarded, and the user was shown only
/// "lemma-locald exited during startup (exit status: 1)".
///
/// A file, never `Stdio::piped()`: nothing in this process would drain a pipe,
/// and a full pipe buffer blocks the writer. Falls back to `null()` rather than
/// failing the spawn, because not having a log is not a reason to have no
/// daemon.
fn locald_stderr_sink() -> Stdio {
    let path = locald_stderr_path();
    let Some(parent) = path.parent() else {
        return Stdio::null();
    };
    if std::fs::create_dir_all(parent).is_err() {
        return Stdio::null();
    }
    // Truncate per spawn: this file exists to explain *this* launch, and a
    // stale reason from a previous run is worse than none.
    match std::fs::File::create(&path) {
        Ok(file) => Stdio::from(file),
        Err(_) => Stdio::null(),
    }
}

/// The last thing locald said before it died, for the message the user sees.
///
/// Bounded read from the tail: this is an error path and the file is normally
/// empty, but a `cargo run` fallback in a source checkout puts compiler output
/// here and that can be large.
fn locald_stderr_tail() -> Option<String> {
    const MAX_TAIL_BYTES: usize = 4096;
    let raw = std::fs::read(locald_stderr_path()).ok()?;
    let start = raw.len().saturating_sub(MAX_TAIL_BYTES);
    let tail = String::from_utf8_lossy(&raw[start..]);
    tail.lines()
        .rev()
        .map(str::trim)
        .find(|line| !line.is_empty())
        .map(|line| line.trim_start_matches("lemma-locald: ").to_owned())
}

/// When this process started, for the launch trace to measure against.
static LAUNCH_START: std::sync::OnceLock<Instant> = std::sync::OnceLock::new();

/// Record how long a launch stage took.
///
/// "Opens instantly" is not a claim anyone can check by feel — a resumed launch
/// and a splash launch look the same in a screen recording once both have
/// finished. This writes the actual milliseconds for each stage so a regression
/// shows up as a number, and so the startup targets have evidence behind them
/// rather than a stopwatch and an opinion.
fn launch_trace(stage: &str) {
    let elapsed = LAUNCH_START.get_or_init(Instant::now).elapsed().as_millis();
    append_bounded_log(&launch_log_path(), &format!("{elapsed:>6}ms {stage}"));
}

fn operation_id(prefix: &str) -> String {
    let nonce = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    format!("{prefix}-{}-{nonce}", std::process::id())
}

fn append_install_log(message: &str) {
    append_bounded_log(&install_log_path(), message);
}

/// Append one timestamped line, rotating once the file reaches its ceiling.
///
/// Best-effort throughout: a log that cannot be written must never be the
/// reason an install, a launch, or a shutdown fails.
fn append_bounded_log(path: &std::path::Path, message: &str) {
    let Some(parent) = path.parent() else {
        return;
    };
    if std::fs::create_dir_all(parent).is_err() {
        return;
    }
    if path
        .metadata()
        .is_ok_and(|metadata| metadata.len() >= MAX_INSTALL_LOG_BYTES)
    {
        let previous = path.with_extension("previous.log");
        let _ = std::fs::remove_file(&previous);
        let _ = std::fs::rename(path, previous);
    }
    let mut options = std::fs::OpenOptions::new();
    options.create(true).append(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let Ok(mut file) = options.open(path) else {
        return;
    };
    let timestamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis();
    let clean = message.replace(['\r', '\n'], " ");
    let _ = writeln!(file, "{timestamp} {clean}");
}

fn locald_socket_name(root: &std::path::Path) -> Result<Name<'_>, String> {
    #[cfg(unix)]
    {
        root.join("control.sock")
            .to_fs_name::<GenericFilePath>()
            .map_err(|error| error.to_string())
    }
    #[cfg(windows)]
    {
        locald_pipe_name(root)
            .to_ns_name::<GenericNamespaced>()
            .map(Name::into_owned)
            .map_err(|error| error.to_string())
    }
}

/// What the control endpoint is called on Windows.
///
/// Split out so one assertion can pin the whole name -- the literal and the
/// hash together. Both halves are duplicated in locald, and it was the hash
/// half that drifted.
#[cfg(windows)]
fn locald_pipe_name(root: &std::path::Path) -> String {
    format!(r"LOCAL\work.lemma.locald.{:016x}", stable_hash(root))
}

/// A stable identity for a state root, for naming things keyed to it.
///
/// This has to stay byte-for-byte identical to `LocalPaths::stable_hash` in
/// locald/src/paths.rs, because the two are the only things that decide what
/// the control endpoint is called: the app opens the name this produces, and
/// the daemon listens on the name that one produces. locald is a sidecar
/// binary, not a library this crate links -- pulling in hyper, tokio, keyring
/// and reqwest to share eight lines would cost more than the app's whole
/// payload budget -- so the code is duplicated and pinned instead. Both copies
/// carry the same golden test over the same path, so a change to either one
/// fails its own crate's suite rather than shipping.
///
/// Normalised first: Windows paths are case-insensitive and accept either
/// separator, so the same directory can be spelled several ways and each
/// spelling used to hash differently. The daemon has normalised since that was
/// found; this side did not, and `%LOCALAPPDATA%` always contains uppercase --
/// so on every default Windows install the app looked for a pipe the daemon it
/// had just spawned was never going to open, and spent the whole 45s start
/// budget failing to connect to a process that was running fine.
#[cfg(windows)]
fn stable_hash(path: &std::path::Path) -> u64 {
    path.to_string_lossy()
        .replace('/', "\\")
        .trim_end_matches('\\')
        .to_ascii_lowercase()
        .bytes()
        .fold(0xcbf29ce484222325, |hash, byte| {
            (hash ^ u64::from(byte)).wrapping_mul(0x100000001b3)
        })
}

fn config_path() -> PathBuf {
    app_support_dir().join("desktop-config.json")
}

fn read_config() -> Value {
    std::fs::read_to_string(config_path())
        .ok()
        .and_then(|raw| serde_json::from_str(&raw).ok())
        .unwrap_or_else(|| json!({}))
}

/// What the last session left running, so the next launch can go straight to it.
///
/// Without this every launch is identical to a cold one: splash, a `start`
/// round trip through the daemon, then a navigation to the workspace — even
/// when the backend and frontend never stopped serving. The recorded generation
/// and release are what make trusting it safe; see [`resume_target_is_serving`].
#[derive(Clone, Debug, PartialEq, Eq)]
struct ResumeTarget {
    url: String,
    api_url: String,
    generation: String,
    /// The Desktop release that recorded this. A newer app must not resume it.
    release: String,
    route: String,
}

fn read_resume_target() -> Option<ResumeTarget> {
    let config = read_config();
    let saved = config.get("resumeTarget")?;
    let text = |key: &str| saved.get(key)?.as_str().map(str::to_string);
    let url = text("url")?;
    let api_url = text("apiUrl")?;
    let generation = text("generation")?;
    let release = text("release")?;
    // A resume target belongs to the release that wrote it.
    //
    // Without this, installing a new build over an old one resumes the *old*
    // one's workspace: the previous stack is often still serving, so the probe
    // passes, the window opens it — and then `ensure_locald` finds a daemon that
    // does not match the new host pack, replaces it, and every service comes
    // back on new ports. The window is left pointed at a port nothing is
    // listening on, which is a permanently blank app.
    if release != env!("CARGO_PKG_VERSION") {
        return None;
    }
    if generation.is_empty() || !trusted_workspace_urls(&url, &api_url) {
        return None;
    }
    // A route is a bonus, not a requirement: an install that has only ever
    // reached the workspace root still resumes, it just resumes at the root.
    let route = text("route").filter(|route| route.starts_with('/'));
    Some(ResumeTarget {
        url,
        api_url,
        generation,
        release,
        route: route.unwrap_or_else(|| "/".into()),
    })
}

fn write_resume_target(url: &str, api_url: &str, generation: &str) {
    if generation.is_empty() || !trusted_workspace_urls(url, api_url) {
        return;
    }
    let _ = write_config(|config| {
        let entry = config
            .as_object_mut()
            .map(|object| object.entry("resumeTarget").or_insert_with(|| json!({})));
        if let Some(entry) = entry {
            entry["url"] = json!(url);
            entry["apiUrl"] = json!(api_url);
            entry["generation"] = json!(generation);
            entry["release"] = json!(env!("CARGO_PKG_VERSION"));
        }
    });
}

/// Remember where in the workspace the user was, so the next launch lands there
/// rather than on the root route that only redirects to it.
fn write_resume_route(route: &str) {
    if !route.starts_with('/') {
        return;
    }
    let _ = write_config(|config| {
        if let Some(entry) = config.get_mut("resumeTarget") {
            entry["route"] = json!(route);
        }
    });
}

/// Capture the workspace route the main window is currently showing.
///
/// Returns `None` for anything that is not the recorded workspace — the splash,
/// the installer, a published app — because none of those are somewhere to
/// resume to.
fn current_workspace_route(app: &AppHandle, target: &ResumeTarget) -> Option<String> {
    let url = app.get_webview("main")?.url().ok()?;
    if !same_origin(&url, &target.url) {
        return None;
    }
    let mut route = url.path().to_string();
    if let Some(query) = url.query() {
        route.push('?');
        route.push_str(query);
    }
    Some(route)
}

/// The exact URL a resumed launch should open.
///
/// The recorded route, not the workspace root: the root only authenticates and
/// then redirects to the last pod, so opening it means loading the app twice to
/// arrive where the route would have gone directly.
fn resume_entry_url(target: &ResumeTarget) -> String {
    format!(
        "{}{}",
        target.url.trim_end_matches('/'),
        if target.route == "/" {
            ""
        } else {
            &target.route
        }
    )
}

/// Is the workspace this target points at still the one that is serving?
///
/// Matching the generation is the whole point. A 2xx alone would also be
/// returned by an unrelated listener that took the port after a crash, or by a
/// stale process from a previous runtime; the generation is minted per user
/// start and handed to the backend as `LEMMA_RUNTIME_INSTANCE_ID`, so it only
/// matches when this is literally the stack the last session left behind.
fn resume_target_is_serving(target: &ResumeTarget) -> bool {
    let Ok(client) = reqwest::blocking::Client::builder()
        .timeout(RESUME_PROBE_TIMEOUT)
        .no_proxy()
        .build()
    else {
        return false;
    };
    // Both halves, because the window opens the frontend and the page it loads
    // then talks to the backend. Checking only the backend was enough to accept
    // a resume whose frontend had gone, which lands on a blank window.
    generation_matches(
        &client,
        &format!("{}/health/ready", target.api_url.trim_end_matches('/')),
        &target.generation,
    ) && generation_matches(
        &client,
        &format!("{}/runtime-config.js", target.url.trim_end_matches('/')),
        &target.generation,
    )
}

/// Does this endpoint answer 2xx and carry the generation we left it on?
///
/// The backend answers JSON with an `instance_id`; the frontend serves its
/// generation inside `runtime-config.js`. Both are the checks locald's own
/// health gate makes, so a substring match against the body is the same
/// contract rather than a looser one.
fn generation_matches(client: &reqwest::blocking::Client, url: &str, generation: &str) -> bool {
    let Ok(response) = client.get(url).send() else {
        return false;
    };
    if !response.status().is_success() {
        return false;
    }
    response.text().is_ok_and(|body| body.contains(generation))
}

fn write_config(update: impl FnOnce(&mut Value)) -> Result<(), String> {
    config_store::update(&config_path(), update)
        .map_err(|error| format!("could not save desktop configuration: {error}"))
}

fn connection_mode() -> String {
    if let Ok(mode) = std::env::var("LEMMA_DESKTOP_CONNECTION_MODE") {
        if mode == "hosted" || mode == "local" {
            return mode;
        }
    }
    configured_connection_mode(&read_config())
}

fn configured_connection_mode(config: &Value) -> String {
    if config["connectionModePromptRevision"].as_u64() != Some(CONNECTION_MODE_PROMPT_REVISION) {
        return "undecided".into();
    }
    match config["connectionMode"].as_str() {
        Some("hosted") => "hosted".into(),
        Some("local") => "local".into(),
        // First launch: the splash asks the user to choose.
        _ => "undecided".into(),
    }
}

fn hosted_url() -> String {
    std::env::var("LEMMA_DESKTOP_HOSTED_URL").unwrap_or_else(|_| DEFAULT_HOSTED_URL.into())
}

/// The workspace origins `capabilities/workspace.json` already covers.
/// The origins the shipped capability already covers, read from the file.
///
/// Restated beside it, this list was a second copy of a rule that had already
/// drifted once in this very function -- and it silently gained a third failure
/// mode: the shipped entries carry a `:*` port pattern, while the origins
/// checked against them are concrete, so `contains` never matched a local
/// workspace and an override capability was minted for an origin that did not
/// need one.
fn shipped_workspace_origins() -> Vec<String> {
    serde_json::from_str::<Value>(SHIPPED_WORKSPACE_CAPABILITY)
        .expect("capabilities/workspace.json is valid JSON")["remote"]["urls"]
        .as_array()
        .expect("capabilities/workspace.json lists remote urls")
        .iter()
        .map(|url| {
            url.as_str()
                .expect("a shipped remote url is a string")
                .to_owned()
        })
        .collect()
}

/// Whether a shipped pattern already covers this concrete origin.
///
/// Only the port may be a wildcard, and only as the whole port: a local
/// workspace is served on whatever port was free, so `http://host:*` has to
/// cover `http://host:52413`. Nothing else is treated as a pattern, because a
/// looser match here hands shell commands to a lookalike host.
fn shipped_workspace_origin_covers(pattern: &str, origin: &str) -> bool {
    if pattern == origin {
        return true;
    }
    let Some(prefix) = pattern.strip_suffix(":*") else {
        return false;
    };
    origin
        .strip_prefix(prefix)
        .and_then(|rest| rest.strip_prefix(':'))
        .is_some_and(|port| !port.is_empty() && port.chars().all(|c| c.is_ascii_digit()))
}

/// The shipped workspace capability, read at compile time so the override below
/// cannot drift from it.
///
/// It did drift. The override was written when the shipped capability granted a
/// single command; the Agent Host and provider commands were added to the file
/// alone, and every non-shipped origin — a self-hosted workspace, a dev server —
/// lost the Computers card and the provider steps to `Command
/// agent_host_status not allowed by ACL`, with nothing the user could do about
/// it. Reading the list is what keeps the two the same next time.
const SHIPPED_WORKSPACE_CAPABILITY: &str = include_str!("../capabilities/workspace.json");

/// The permissions the shipped workspace origins get.
///
/// Panics on a malformed capability, which is a build-time fact rather than a
/// runtime one: the file is compiled in, so a bad edit fails the first test that
/// touches this rather than reaching a user.
fn shipped_workspace_permissions() -> Vec<Value> {
    serde_json::from_str::<Value>(SHIPPED_WORKSPACE_CAPABILITY)
        .expect("capabilities/workspace.json is valid JSON")["permissions"]
        .as_array()
        .expect("capabilities/workspace.json grants an array of permissions")
        .clone()
}

/// A capability granting the overridden workspace origin the same commands the
/// shipped one gets, or `None` when nothing is overridden.
///
/// Only the origin varies. The permission list is taken from
/// `capabilities/workspace.json` itself, so a dev or self-hosted build can
/// never reach further into the shell than a packaged one — nor, as it did,
/// less far.
fn overridden_workspace_capability() -> Option<String> {
    let configured = ["LEMMA_DESKTOP_HOSTED_URL", "LEMMA_DESKTOP_LOCAL_URL"]
        .into_iter()
        .filter_map(|variable| std::env::var(variable).ok());
    workspace_capability_for(configured)
}

fn workspace_capability_for(configured: impl Iterator<Item = String>) -> Option<String> {
    let shipped = shipped_workspace_origins();
    let mut urls: Vec<String> = Vec::new();
    for value in configured {
        let Ok(url) = tauri::Url::parse(value.trim()) else {
            continue;
        };
        // Match the whole origin and any path under it, never a bare host that
        // a lookalike could also satisfy.
        let Some(host) = url.host_str() else { continue };
        let origin = match url.port() {
            Some(port) => format!("{}://{host}:{port}", url.scheme()),
            None => format!("{}://{host}", url.scheme()),
        };
        if shipped
            .iter()
            .any(|pattern| shipped_workspace_origin_covers(pattern, &origin))
        {
            continue;
        }
        if !urls.contains(&origin) {
            urls.push(origin);
        }
    }
    if urls.is_empty() {
        return None;
    }
    Some(
        json!({
            "identifier": "workspace-override-capability",
            "description": "Development or self-hosted workspace origin, granted the same commands as the shipped one.",
            "local": false,
            "webviews": ["main"],
            "remote": {"urls": urls},
            "permissions": shipped_workspace_permissions(),
        })
        .to_string(),
    )
}

/// Where the monorepo checkout lives, used only for development fallbacks.
/// Dev default: this repo. Packaged builds set
/// LEMMA_DESKTOP_RUNTIME_ROOT (or persist runtimeRoot in desktop config).
fn runtime_root() -> PathBuf {
    if let Ok(root) = std::env::var("LEMMA_DESKTOP_RUNTIME_ROOT") {
        return PathBuf::from(root);
    }
    if let Some(root) = read_config()["runtimeRoot"].as_str() {
        return PathBuf::from(root);
    }
    default_runtime_root(
        std::env::current_exe().ok().as_deref(),
        std::path::Path::new(env!("CARGO_MANIFEST_DIR")),
        cfg!(debug_assertions),
    )
}

fn default_runtime_root(
    executable: Option<&std::path::Path>,
    manifest_dir: &std::path::Path,
    development: bool,
) -> PathBuf {
    if development {
        // Debug builds may use the monorepo containing this crate. A release
        // build must never trust its compile-time checkout path: that path can
        // still exist on a developer/test machine after the app is copied to
        // Applications, causing the signed package to skip artifact install.
        return manifest_dir
            .parent()
            .expect("desktop crate has a parent directory")
            .to_path_buf();
    }
    executable
        .and_then(std::path::Path::parent)
        .map(std::path::Path::to_path_buf)
        .unwrap_or_default()
}

/// The durable local daemon shipped next to the app executable.
fn bundled_locald() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?;
    let candidate = exe.parent()?.join(if cfg!(windows) {
        "lemma-locald.exe"
    } else {
        "lemma-locald"
    });
    candidate.exists().then_some(candidate)
}

/// A development override, honoured only by a development build.
///
/// These three point the app at a different runtime: a host pack, a managed
/// runtime, or the signed manifest that names both and carries the digests
/// everything else is checked against. Each was read unconditionally and took
/// precedence over the bundled resource, so in a shipped, notarized,
/// hardened-runtime app, anything already running as the user could set one and
/// have Lemma download and execute a runtime of its choosing -- and, through
/// the manifest, choose the digests that runtime was verified against.
///
/// That is a persistence and trust-laundering primitive rather than initial
/// access, but it defeats the entire point of a signed manifest.
///
/// `network.rs` has always done this correctly for the port overrides, with the
/// same reasoning: packaged releases use app-owned allocation and overrides
/// exist only to make source runs deterministic. This is that rule, applied to
/// the three places it was missing.
fn dev_override(name: &str) -> Option<std::ffi::OsString> {
    if !cfg!(debug_assertions) {
        return None;
    }
    std::env::var_os(name).filter(|value| !value.is_empty())
}

fn bundled_host_pack_root() -> Option<PathBuf> {
    if let Some(root) = dev_override("LEMMA_DESKTOP_HOST_PACK_ROOT") {
        let root = PathBuf::from(root);
        if root.join("release.json").is_file() {
            return Some(root);
        }
    }
    let exe = std::env::current_exe().ok()?;
    let bin_dir = exe.parent()?;
    let candidates = if cfg!(target_os = "macos") {
        vec![
            bin_dir.join("../Resources/local-runtime"),
            bin_dir.join("local-runtime"),
        ]
    } else {
        vec![bin_dir.join("local-runtime")]
    };
    candidates
        .into_iter()
        .find(|root| root.join("release.json").is_file())
}

fn runtime_from_config_value(installed: &Value) -> Option<artifact_install::InstalledRuntime> {
    let release = installed.get("release")?.as_str()?;
    let root = PathBuf::from(installed.get("root")?.as_str()?);
    let runtime = artifact_install::installed_runtime(&root, release);
    runtime.is_complete().then_some(runtime)
}

fn configured_runtime(config: &Value, key: &str) -> Option<artifact_install::InstalledRuntime> {
    runtime_from_config_value(config.get(key)?)
}

fn host_pack_root() -> Option<PathBuf> {
    let config = read_config();
    bundled_host_pack_root().or_else(|| {
        configured_runtime(&config, "installedRuntime").map(|runtime| runtime.host_pack_root)
    })
}

fn bundled_managed_runtime_root() -> Option<PathBuf> {
    if let Some(root) = dev_override("LEMMA_DESKTOP_MANAGED_RUNTIME_ROOT") {
        let root = PathBuf::from(root);
        if managed_runtime_marker(&root).is_file() {
            return Some(root);
        }
    }
    let exe = std::env::current_exe().ok()?;
    let bin_dir = exe.parent()?;
    let candidates = if cfg!(target_os = "macos") {
        vec![
            bin_dir.join("../Resources/managed-runtime"),
            bin_dir.join("managed-runtime"),
        ]
    } else {
        vec![bin_dir.join("managed-runtime")]
    };
    candidates
        .into_iter()
        .find(|root| managed_runtime_marker(root).is_file())
}

fn managed_runtime_root() -> Option<PathBuf> {
    let config = read_config();
    bundled_managed_runtime_root().or_else(|| {
        configured_runtime(&config, "installedRuntime").map(|runtime| runtime.managed_runtime_root)
    })
}

fn bundled_release_manifest() -> Option<PathBuf> {
    if let Some(path) = dev_override("LEMMA_DESKTOP_RELEASE_MANIFEST") {
        let path = PathBuf::from(path);
        if path.is_file() {
            return Some(path);
        }
    }
    let executable = std::env::current_exe().ok()?;
    let bin_dir = executable.parent()?;
    let candidates = if cfg!(target_os = "macos") {
        vec![
            bin_dir.join("../Resources/lemma-local.json"),
            bin_dir.join("../Resources/runtime/lemma-local.json"),
            bin_dir.join("lemma-local.json"),
        ]
    } else {
        vec![
            bin_dir.join("lemma-local.json"),
            bin_dir.join("runtime/lemma-local.json"),
        ]
    };
    candidates.into_iter().find(|path| path.is_file())
}

fn managed_runtime_marker(root: &std::path::Path) -> PathBuf {
    root.join(if cfg!(target_os = "macos") {
        "macos-aarch64/runtime.json"
    } else {
        "windows-x86_64/runtime.json"
    })
}

fn bundled_sibling(name: &str) -> Option<PathBuf> {
    let executable = std::env::current_exe().ok()?;
    let suffix = if cfg!(windows) { ".exe" } else { "" };
    let candidate = executable.parent()?.join(format!("{name}{suffix}"));
    candidate.is_file().then_some(candidate)
}

#[cfg(target_os = "macos")]
fn bundled_vz() -> Option<PathBuf> {
    if let Some(path) = std::env::var_os("LEMMA_DESKTOP_VZ_BIN")
        .map(PathBuf::from)
        .filter(|path| path.is_file())
    {
        return Some(path);
    }
    let executable = std::env::current_exe().ok()?;
    let bin_dir = executable.parent()?;
    [
        // Signed resource in packaged apps. It is deliberately not an
        // externalBin because Tauri would replace its helper entitlement.
        bin_dir.join("../Resources/lemma-vz"),
        // Development/test compatibility.
        bin_dir.join("lemma-vz"),
    ]
    .into_iter()
    .find(|path| path.is_file())
}

fn host_pack_release(root: &std::path::Path) -> Option<String> {
    let release = root.join("release.json");
    let payload: Value = serde_json::from_slice(&std::fs::read(release).ok()?).ok()?;
    payload["version"].as_str().map(str::to_owned)
}

fn path_identity(path: &std::path::Path) -> String {
    std::fs::canonicalize(path)
        .unwrap_or_else(|_| path.to_path_buf())
        .to_string_lossy()
        .into_owned()
}

fn runtime_info_snapshot() -> RuntimeInfo {
    let config = read_config();
    let configured = configured_runtime(&config, "installedRuntime");
    let previous = configured_runtime(&config, "previousRuntime");
    let bundled = bundled_host_pack_root()
        .filter(|_| bundled_managed_runtime_root().is_some())
        .and_then(|root| host_pack_release(&root));
    let (active_release, source) = if bundled.is_some() {
        (bundled, "bundled".to_string())
    } else {
        (
            configured.as_ref().map(|runtime| runtime.release.clone()),
            "downloaded".to_string(),
        )
    };
    let downloaded_active = source == "downloaded";
    RuntimeInfo {
        desktop_release: env!("CARGO_PKG_VERSION").into(),
        active_release,
        previous_release: previous.as_ref().map(|runtime| runtime.release.clone()),
        source,
        // Schema-1 releases do not declare database rollback compatibility.
        // Retain the prior immutable pack, but never offer an unsafe downgrade.
        rollback_available: false,
        repair_available: downloaded_active
            && config
                .pointer("/installedRuntime/release")
                .and_then(Value::as_str)
                == Some(env!("CARGO_PKG_VERSION")),
    }
}

/// Whether the daemon on the socket is the one this build ships.
///
/// Version and API revision are not enough: replacing an installed app leaves
/// the previous bundle's daemon running — from `~/.Trash`, once macOS has moved
/// it — holding the control socket, reporting the same `0.7.0` and the same
/// revision, and supervising the *previous* release's runtime. The hosted path
/// used to accept whatever was listening, so a new app adopted that daemon,
/// sent it Agent Host commands it answered from stale state, and started a
/// local stack out of the old host pack that could never come up.
///
/// A daemon that does not say which binary it is answers this with `false`,
/// which is the right answer: every build that does not report it predates the
/// field, and is therefore not this one.
fn locald_is_this_build(hello: &Value, expected_executable: Option<&std::path::Path>) -> bool {
    if hello["daemon_api_revision"].as_u64() != Some(REQUIRED_LOCALD_API_REVISION) {
        return false;
    }
    let Some(expected) = expected_executable else {
        // No packaged sidecar to compare against — a source checkout running
        // whatever it built. Identity is the developer's business there.
        return true;
    };
    hello["executable"].as_str() == Some(path_identity(expected).as_str())
}

fn locald_matches_host_pack(
    hello: &Value,
    required_release: Option<&str>,
    required_root: Option<&std::path::Path>,
) -> bool {
    match (required_release, required_root) {
        (None, None) => true,
        (Some(release), Some(root)) => {
            matches!(hello["mode"].as_str(), Some("host-packs" | "managed-local"))
                && hello["daemon_api_revision"].as_u64() == Some(REQUIRED_LOCALD_API_REVISION)
                && hello["host_pack_release"].as_str() == Some(release)
                && hello["host_pack_root"].as_str() == Some(path_identity(root).as_str())
        }
        _ => false,
    }
}

fn enriched_path() -> String {
    // Only the unix arm below appends to this.
    #[cfg_attr(not(unix), expect(unused_mut, reason = "extended on unix only"))]
    let mut parts: Vec<PathBuf> = std::env::var_os("PATH")
        .map(|value| std::env::split_paths(&value).collect())
        .unwrap_or_default();
    #[cfg(unix)]
    {
        for extra in [
            "/opt/homebrew/bin",
            "/usr/local/bin",
            "/usr/bin",
            "/bin",
            "/usr/sbin",
        ] {
            let extra = PathBuf::from(extra);
            if !parts.contains(&extra) {
                parts.push(extra);
            }
        }
    }
    std::env::join_paths(parts)
        .unwrap_or_default()
        .to_string_lossy()
        .into_owned()
}

// ---------------------------------------------------------------------------
// Durable local daemon lifecycle
// ---------------------------------------------------------------------------

fn require_no_recovery(shell: &Shell) -> Result<(), String> {
    if shell.recovery_mode.load(Ordering::Acquire) {
        return Err("Recovery mode: local services and downloads are paused. Use Recovery, or choose a connection mode to resume.".into());
    }
    if shell.recovery_running.load(Ordering::Acquire) {
        return Err("Installation cleanup is running. Wait for it to finish before starting local services.".into());
    }
    Ok(())
}

fn ensure_locald(app: &AppHandle) -> Result<(), String> {
    let shell: State<Shell> = app.state();
    require_no_recovery(&shell)?;
    // The runtime comes first, and before the "already connected" check rather
    // than after it.
    //
    // Both modes run this same daemon: hosted brings it up through
    // `ensure_locald_without_host_pack` so the Agent Host has a supervisor, and
    // only local needs the runtime artifacts. So by the time someone switches
    // hosted -> local, the writer is already `Some` -- and this returned `Ok`
    // having installed nothing at all. `start` then reached a daemon with no
    // private runtime and came back "private runtime is not ready for host
    // processes", the splash sat on "Lemma is starting", and the only cure was
    // relaunching the app, because a fresh process is the one thing that makes
    // the writer `None` again and lets this run properly.
    //
    // Cheap when there is nothing to do: an installed runtime is self-contained
    // and is recognised from its own recorded identity, without consulting an
    // artifact host.
    //
    // Still before the connect guard, under a lock of its own. This can take
    // minutes on a first run, and holding `locald_connect` across it turned
    // every unrelated caller into a hang of the same length.
    {
        let _install_guard = shell.runtime_install.lock().unwrap();
        require_no_recovery(&shell)?;
        ensure_runtime_artifacts(app)?;
    }
    if shell.locald_writer.lock().unwrap().is_some() {
        return Ok(());
    }

    let _connect_guard = shell.locald_connect.lock().unwrap();
    require_no_recovery(&shell)?;
    if shell.locald_writer.lock().unwrap().is_some() {
        return Ok(());
    }

    let required_root = host_pack_root();
    let required_release = required_root.as_deref().and_then(host_pack_release);
    let expected_executable = locald_binary();
    // The host pack says which runtime it serves; the executable says which
    // build is serving it. A daemon left over from a replaced app bundle can
    // satisfy the first and still be the wrong process.
    let acceptable = |hello: &Value| {
        locald_is_this_build(hello, expected_executable.as_deref())
            && locald_matches_host_pack(
                hello,
                required_release.as_deref(),
                required_root.as_deref(),
            )
    };
    if let Ok(connection) = connect_locald() {
        if acceptable(&connection.hello) {
            install_locald_connection(app, connection);
            return Ok(());
        }
        replace_locald(connection)?;
    }

    let child = spawn_locald()?;
    let connection = await_locald(child, |connection| acceptable(&connection.hello))?;
    install_locald_connection(app, connection);
    Ok(())
}

/// Bring up locald for a workspace that has no local stack.
///
/// A hosted workspace still wants the Agent Host on this machine, and locald is
/// what supervises it. Unlike the local path this downloads nothing: with no
/// host pack there is no release to match, and locald without one only holds
/// its socket and the sidecar.
///
/// It does still insist the daemon be this build's. It used to accept whatever
/// was listening, and "whatever" turned out to include the previous app
/// bundle's daemon, still running from the Trash after an update, supervising
/// the previous release's runtime. Adopting it made the Agent Host controls
/// report and command stale state, and left a local start booting a runtime the
/// installed app had already replaced.
fn ensure_locald_without_host_pack(app: &AppHandle) -> Result<(), String> {
    let shell: State<Shell> = app.state();
    require_no_recovery(&shell)?;
    if shell.locald_writer.lock().unwrap().is_some() {
        return Ok(());
    }
    let _connect_guard = shell.locald_connect.lock().unwrap();
    require_no_recovery(&shell)?;
    if shell.locald_writer.lock().unwrap().is_some() {
        return Ok(());
    }

    let expected = locald_binary();
    if let Ok(connection) = connect_locald() {
        if locald_is_this_build(&connection.hello, expected.as_deref()) {
            install_locald_connection(app, connection);
            return Ok(());
        }
        replace_locald(connection)?;
    }

    let child = spawn_locald()?;
    let connection = await_locald(child, |connection| {
        locald_is_this_build(&connection.hello, expected.as_deref())
    })?;
    install_locald_connection(app, connection);
    Ok(())
}

fn ensure_runtime_artifacts(app: &AppHandle) -> Result<(), String> {
    prepare_runtime_artifacts(app, false)
}

fn prepare_runtime_artifacts(app: &AppHandle, reinstall: bool) -> Result<(), String> {
    require_no_recovery(&app.state::<Shell>())?;
    match ensure_runtime_artifacts_inner(app, reinstall) {
        Ok(()) => Ok(()),
        Err(error) => {
            let message = actionable_runtime_install_error(&error);
            append_install_log(&format!("ERROR {message}"));
            emit_log(app, &message);
            emit_runtime_install_error(app, &message);
            Err(message)
        }
    }
}

fn ensure_runtime_artifacts_inner(app: &AppHandle, reinstall: bool) -> Result<(), String> {
    if runtime_root().join("desktop/locald/Cargo.toml").is_file() {
        return Ok(());
    }
    let config = read_config();
    if let Some(bundled_host) = bundled_host_pack_root() {
        if bundled_managed_runtime_root().is_none() {
            return Err("the bundled managed runtime is incomplete".into());
        }
        let release = host_pack_release(&bundled_host)
            .ok_or("the bundled native runtime has no valid release marker")?;
        if release != env!("CARGO_PKG_VERSION") {
            return Err(format!(
                "bundled runtime release {release} does not match desktop release {}",
                env!("CARGO_PKG_VERSION")
            ));
        }
        return Ok(());
    }
    let bundled_manifest = bundled_release_manifest();
    // A successfully installed and activated runtime is self-contained. Its
    // recorded artifact identity was written only after the manifest, archive
    // digests, extracted layout, and release markers were verified. Reuse that
    // exact release without consulting an artifact host when no manifest is
    // bundled, so ordinary Finder / Start-menu launches and later cached
    // runtime restarts keep working offline.
    //
    // When a manifest is bundled, compare its artifact digests even if the
    // semantic release is unchanged. This lets signed test builds replace a
    // same-version runtime pack without silently retaining stale components.
    if let Some(runtime) = configured_runtime(&config, "installedRuntime").filter(|runtime| {
        !reinstall
            && runtime.release == env!("CARGO_PKG_VERSION")
            && runtime.has_recorded_artifact_identity()
    }) {
        let Some(manifest) = bundled_manifest.as_ref() else {
            return Ok(());
        };
        let matches = artifact_install::runtime_matches_manifest(
            &runtime,
            manifest,
            env!("CARGO_PKG_VERSION"),
        )
        .map_err(|error| format!("could not verify the installed local runtime: {error}"))?;
        if matches {
            return Ok(());
        }
    }
    let manifest = bundled_manifest.ok_or_else(|| {
        "this online installer is missing its signed local release manifest".to_string()
    })?;
    let manifest_release = artifact_install::manifest_release(&manifest)
        .map_err(|error| format!("could not read the signed local release manifest: {error}"))?;
    if manifest_release != env!("CARGO_PKG_VERSION") {
        return Err(format!(
            "signed runtime release {manifest_release} does not match desktop release {}",
            env!("CARGO_PKG_VERSION")
        ));
    }
    if let Some(runtime) = configured_runtime(&config, "installedRuntime").filter(|_| !reinstall) {
        let matches = artifact_install::runtime_matches_manifest(
            &runtime,
            &manifest,
            env!("CARGO_PKG_VERSION"),
        )
        .map_err(|error| format!("could not verify the installed local runtime: {error}"))?;
        if matches {
            return Ok(());
        }
    }
    let install_operation_id = operation_id("runtime-install");
    {
        let shell: State<Shell> = app.state();
        let mut ui = shell.ui.lock().unwrap();
        ui.active_operation_id = install_operation_id.clone();
    }
    emit_runtime_install_progress(
        app,
        "resolve",
        "runtime",
        "Preparing local runtime",
        1,
        None,
        None,
        None,
        None,
    );
    let install_started = std::time::Instant::now();
    let install = if reinstall {
        artifact_install::reinstall_from_manifest
    } else {
        artifact_install::install_from_manifest
    };
    let installed = install(
        &manifest,
        &runtime_install_root(),
        env!("CARGO_PKG_VERSION"),
        &mut |progress| {
            let fraction = progress
                .current
                .saturating_mul(1000)
                .checked_div(progress.total)
                .unwrap_or(0);
            let percent = match progress.stage {
                "download" => 2 + fraction.saturating_mul(44) / 1000,
                "verify" => 47,
                "host-extract" | "guest-extract" => 49 + fraction.saturating_mul(39) / 1000,
                "validate" => 90,
                _ => 1,
            };
            let (eta_seconds, throughput_bytes_per_second) =
                if progress.stage == "download" && progress.current > 0 {
                    let elapsed = install_started.elapsed().as_secs_f64();
                    let rate = progress.current as f64 / elapsed.max(0.001);
                    (
                        (progress.current < progress.total).then_some(
                            ((progress.total - progress.current) as f64 / rate).ceil() as u64,
                        ),
                        Some(rate.round() as u64),
                    )
                } else {
                    (None, None)
                };
            emit_runtime_install_progress(
                app,
                progress.stage,
                progress.component,
                progress.label,
                percent.min(90),
                progress.bytes.then_some(progress.current),
                progress.bytes.then_some(progress.total),
                eta_seconds,
                throughput_bytes_per_second,
            );
        },
    )
    .map_err(|error| format!("could not install the local runtime: {error}"))?;
    stop_locald_for_runtime_maintenance(app).map_err(|error| {
        format!("could not stop the previous local runtime before activation: {error}")
    })?;
    activate_installed_runtime(&installed)?;
    emit_runtime_install_progress(
        app,
        "activate",
        "runtime",
        "Local runtime installed",
        92,
        None,
        None,
        None,
        None,
    );
    {
        let shell: State<Shell> = app.state();
        let mut ui = shell.ui.lock().unwrap();
        if ui.active_operation_id == install_operation_id {
            ui.active_operation_id.clear();
        }
    }
    Ok(())
}

/// Turn an installer failure into something the person reading it can do.
///
/// This had exactly one branch, and it answered the one case *we* hit: a 404
/// told the reader to "publish its runtime artifacts", which is an instruction
/// to a maintainer shipped to a stranger. Everything else fell through
/// verbatim, so a corporate proxy became "artifact download failed with HTTP
/// 403" and a dropped connection became a reqwest debug string.
///
/// Each arm names what happened and what to try. The raw text stays in the
/// installer log, which the error screen links to.
fn actionable_runtime_install_error(error: &str) -> String {
    let lowered = error.to_ascii_lowercase();
    let version = env!("CARGO_PKG_VERSION");

    if error.contains("artifact download failed with HTTP 404") {
        return format!(
            "Lemma {version}'s runtime is not available for download. If this is a \
             nightly build, it may have been superseded — download the current one \
             and install it again."
        );
    }
    if lowered.contains("http 401") || lowered.contains("http 403") || lowered.contains("http 429")
    {
        return "The download was blocked or rate-limited. A VPN, proxy or firewall \
                may be intercepting github.com. Try again on a different network."
            .to_owned();
    }
    if lowered.contains("could not connect") || lowered.contains("dns") {
        return "Lemma could not reach github.com to download its runtime. Check \
                your internet connection and try again."
            .to_owned();
    }
    if lowered.contains("timed out") || lowered.contains("timeout") {
        return "The download stopped responding. Try again — it resumes from where \
                it stopped rather than starting over."
            .to_owned();
    }
    if lowered.contains("sha-256") || lowered.contains("digest") {
        return "The downloaded runtime did not match what Lemma expected. This is \
                usually a network that modifies downloads, such as a captive Wi-Fi \
                portal — sign in to the network first, then try again."
            .to_owned();
    }
    if lowered.contains("not enough disk space") {
        // Already actionable and carries real numbers; do not flatten it.
        return error.to_owned();
    }
    if lowered.contains("does not match desktop release") {
        return format!(
            "This copy of Lemma and its runtime do not match. Reinstalling Lemma \
             {version} fixes it."
        );
    }
    error.to_owned()
}

fn activate_installed_runtime(
    installed: &artifact_install::InstalledRuntime,
) -> Result<(), String> {
    let root = installed
        .host_pack_root
        .parent()
        .ok_or("installed runtime has no release root")?
        .to_string_lossy()
        .into_owned();
    let next = json!({"release": installed.release, "root": root});
    write_config(|config| {
        let current = config
            .get("installedRuntime")
            .cloned()
            .unwrap_or(Value::Null);
        if runtime_from_config_value(&current).is_some() && current != next {
            config["previousRuntime"] = current;
        }
        config["installedRuntime"] = next;
    })?;

    // Only after the config records the pair, so a crash between the two
    // leaves a release too many rather than a release too few. Failure is not
    // propagated: disk that could not be reclaimed is not a reason to fail an
    // upgrade that has already succeeded.
    let config = read_config();
    let keep: Vec<std::path::PathBuf> = ["installedRuntime", "previousRuntime"]
        .iter()
        .filter_map(|key| configured_runtime(&config, key))
        .filter_map(|runtime| {
            runtime
                .host_pack_root
                .parent()
                .map(std::path::Path::to_path_buf)
        })
        .collect();
    for release in artifact_install::prune_retired_releases(&runtime_install_root(), &keep) {
        append_install_log(&format!("removed retired runtime {}", release.display()));
    }
    Ok(())
}

#[allow(clippy::too_many_arguments)]
fn emit_runtime_install_progress(
    app: &AppHandle,
    stage: &str,
    component: &str,
    label: &str,
    progress: u64,
    downloaded_bytes: Option<u64>,
    total_bytes: Option<u64>,
    eta_seconds: Option<u64>,
    throughput_bytes_per_second: Option<u64>,
) {
    let detail = match (downloaded_bytes, total_bytes) {
        (Some(downloaded), Some(total)) if total > 0 => format!(
            "{label}: {} MB of {} MB",
            downloaded / (1024 * 1024),
            total.div_ceil(1024 * 1024)
        ),
        _ => label.to_owned(),
    };
    append_install_log(&detail);
    emit_log(app, &detail);
    let shell: State<Shell> = app.state();
    let snapshot = {
        let mut ui = shell.ui.lock().unwrap();
        ui.setup = true;
        ui.phase = label.to_owned();
        ui.phase_key = stage.to_owned();
        ui.component = component.to_owned();
        ui.progress = progress;
        ui.status = detail;
        ui.downloaded_bytes = downloaded_bytes;
        ui.total_bytes = total_bytes;
        ui.eta_seconds = eta_seconds;
        ui.throughput_bytes_per_second = throughput_bytes_per_second;
        ui.clone()
    };
    let _ = app.emit("lemma:state", snapshot);
}

fn emit_runtime_install_error(app: &AppHandle, message: &str) {
    let shell: State<Shell> = app.state();
    let snapshot = {
        let mut ui = shell.ui.lock().unwrap();
        ui.setup = true;
        ui.phase = "Local runtime setup".into();
        ui.phase_key = "runtime-install".into();
        ui.status = message.to_owned();
        ui.downloaded_bytes = None;
        ui.total_bytes = None;
        ui.throughput_bytes_per_second = None;
        ui.error = true;
        ui.error_code = "runtime-install-failed".into();
        ui.ready = false;
        ui.running = false;
        ui.active_operation_id.clear();
        ui.clone()
    };
    let _ = app.emit("lemma:state", snapshot);
    show_splash(app);
}

/// The daemon binary this build starts.
///
/// Shared with the identity check rather than duplicated: comparing a running
/// daemon against a *different* resolution than the one that spawns it is how a
/// dev run — where `LEMMA_DESKTOP_LOCALD_BIN` overrides the sidecar — would
/// reject the very daemon it had just started, forever.
fn locald_binary() -> Option<PathBuf> {
    std::env::var("LEMMA_DESKTOP_LOCALD_BIN")
        .ok()
        .map(PathBuf::from)
        .filter(|p| p.exists())
        .or_else(bundled_locald)
        .or_else(|| {
            // One workspace under desktop/, so one target directory.
            let candidate = runtime_root().join(if cfg!(windows) {
                "desktop/target/debug/lemma-locald.exe"
            } else {
                "desktop/target/debug/lemma-locald"
            });
            candidate.exists().then_some(candidate)
        })
}

/// Spawn a child without flashing up a console window.
///
/// A release build sets windows_subsystem to windows, so the app has no
/// console at all. lemma-locald.exe is a console program, and starting one
/// from a process with no console makes Windows allocate a visible conhost
/// window for it -- one the user can close, which kills the daemon.
/// Redirecting stdio does not suppress it.
///
/// A no-op everywhere else, so call sites stay platform-neutral.
trait NoConsoleWindow {
    fn no_console_window(&mut self) -> &mut Self;
}

impl NoConsoleWindow for Command {
    #[cfg(windows)]
    fn no_console_window(&mut self) -> &mut Self {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        self.creation_flags(CREATE_NO_WINDOW)
    }

    #[cfg(not(windows))]
    fn no_console_window(&mut self) -> &mut Self {
        self
    }
}

/// Environment variables the *daemon* honours that redirect what it runs or
/// loads. Stripped from a release build's child; see `spawn_locald`.
///
/// Kept in step with locald by ,
/// which reads locald's own source rather than trusting this list.
const DAEMON_REDIRECT_ENV: [&str; 10] = [
    "LEMMA_AGENT_HOST_BIN",
    "LEMMA_LOCALD_SUPERVISOR_BIN",
    "LEMMA_DESKTOP_SUPERVISOR_BIN",
    "LEMMA_LOCALD_WSL_BIN",
    "LEMMA_LOCALD_SOURCE_ROOT",
    "LEMMA_LOCALD_SOURCE_RELEASE_MANIFEST",
    "LEMMA_LOCALD_HOST_PACK_ROOT",
    "LEMMA_LOCALD_HOST_PACK_MANIFEST",
    "LEMMA_LOCALD_MANAGED_RUNTIME_ARTIFACT_ROOT",
    "LEMMA_TELEMETRY_HOST",
];

/// Variables the app hands the daemon on purpose, so they are not redirects to
/// strip. Named here so the test below can tell "deliberately passed" from
/// "nobody thought about it".
#[cfg(test)]
const DAEMON_INTENDED_ENV: [&str; 4] = [
    "LEMMA_LOCALD_ROOT",
    "LEMMA_DESKTOP_RUNTIME_ROOT",
    "LEMMA_CONTAINER_RUNTIME",
    "LEMMA_RUNTIME_WSL_DISTRIBUTION",
];

fn spawn_locald() -> Result<Child, String> {
    let root = runtime_root();
    let have_checkout = root.join("desktop/locald/Cargo.toml").exists();
    let locald_bin = locald_binary();

    let mut command = match &locald_bin {
        Some(bin) => Command::new(bin),
        None => {
            if !have_checkout {
                return Err(format!(
                    "runtime not found: {} has no locald checkout and no bundled daemon",
                    root.display()
                ));
            }
            let mut fallback = Command::new("cargo");
            fallback.args([
                "run",
                "--quiet",
                "--manifest-path",
                "desktop/locald/Cargo.toml",
                "--",
                "serve",
            ]);
            fallback
        }
    };
    if have_checkout {
        command.current_dir(&root);
    }
    // The daemon inherits this process's environment, and the daemon reads
    // redirect variables of its own. So gating them in the *app* -- which
    // `dev_override` does -- stops at the process boundary: a signed, notarized
    // build would refuse to load a host pack from an environment variable and
    // then hand that same variable to the daemon, which loads it without
    // asking. Same threat model the gating exists for: anything running as the
    // user laundering trust through a hardened-runtime process.
    //
    // Removed rather than cleared. `env_clear` would take PATH, HOME and the
    // locale with it, and the ones set below are set explicitly anyway --
    // removal has to happen first so those still win.
    if !cfg!(debug_assertions) {
        for name in DAEMON_REDIRECT_ENV {
            command.env_remove(name);
        }
    }
    command
        .env("PATH", enriched_path())
        .env("LEMMA_DESKTOP", "1")
        .env("LEMMA_LOCALD_ROOT", locald_root())
        .env("LEMMA_DESKTOP_RUNTIME_ROOT", &root)
        // Which container runtime to use -- docker, podman, lemma_local, or
        // "auto" to detect. Not the sandbox provider: the backend's
        // WORKSPACE_PROVIDER is derived from this separately and takes a
        // narrower set of values.
        .env(
            "LEMMA_CONTAINER_RUNTIME",
            std::env::var("LEMMA_CONTAINER_RUNTIME").unwrap_or_else(|_| "auto".into()),
        )
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(locald_stderr_sink());
    if let Some(pack_root) = host_pack_root() {
        command.env("LEMMA_LOCALD_HOST_PACK_ROOT", pack_root);
    }
    if let Some(runtime_root) = managed_runtime_root() {
        let bridge =
            bundled_sibling("lemma-runtime").ok_or("bundled lemma-runtime bridge is missing")?;
        command
            .env("LEMMA_LOCALD_MANAGED_RUNTIME_ARTIFACT_ROOT", runtime_root)
            .env("LEMMA_LOCALD_RUNTIME_BRIDGE_BIN", bridge);
        #[cfg(target_os = "macos")]
        command.env(
            "LEMMA_LOCALD_VZ_BIN",
            bundled_vz().ok_or("bundled lemma-vz helper is missing")?,
        );
    }
    command
        .no_console_window()
        .spawn()
        .map_err(|e| format!("failed to spawn lemma-locald: {e}"))
}

/// How long a freshly spawned locald gets to open its control endpoint.
///
/// This was 8 seconds, which is generous for a warm start and nowhere near
/// enough for the one that matters. On a machine's first launch macOS verifies
/// the newly installed binary before it will run, locald creates its state
/// root, and asking the credential vault for the installation secret can put a
/// system prompt in front of all of it. Missing that window reported "could not
/// connect to lemma-locald" over a daemon that was starting perfectly normally
/// -- and Try again then worked instantly, because by then it had.
///
/// A longer budget costs nothing when the daemon is healthy: the wait returns
/// on the first successful connect. A daemon that dies is now noticed directly
/// rather than by running out the clock.
const LOCALD_START_BUDGET: Duration = Duration::from_secs(45);
const LOCALD_POLL_INTERVAL: Duration = Duration::from_millis(100);

/// Wait for the control endpoint, giving up early if the daemon exited.
fn await_locald<F>(mut child: Child, accept: F) -> Result<LocaldConnection, String>
where
    F: Fn(&LocaldConnection) -> bool,
{
    let deadline = Instant::now() + LOCALD_START_BUDGET;
    let mut last_error = "daemon did not create its control endpoint".to_string();
    while Instant::now() < deadline {
        match connect_locald() {
            Ok(connection) if accept(&connection) => return Ok(connection),
            Ok(_) => last_error = "daemon started with the wrong native host pack".into(),
            Err(error) => last_error = error,
        }
        // A daemon that has already exited will never open the endpoint, so say
        // why instead of spending the rest of the budget waiting for it.
        if let Ok(Some(status)) = child.try_wait() {
            // The status alone is "exit status: 1", which tells nobody
            // anything. The daemon writes the actual reason to its stderr, and
            // since it exited there is nothing left to race with for the read.
            return Err(match locald_stderr_tail() {
                Some(reason) => format!("lemma-locald could not start: {reason}"),
                None => format!("lemma-locald exited during startup ({status})"),
            });
        }
        std::thread::sleep(LOCALD_POLL_INTERVAL);
    }
    Err(format!("could not connect to lemma-locald: {last_error}"))
}

fn connect_locald() -> Result<LocaldConnection, String> {
    connect_locald_with_mode(false)
}

fn connect_locald_with_mode(nonblocking: bool) -> Result<LocaldConnection, String> {
    let root = locald_root();
    let token_path = root.join("control.token");
    if std::fs::metadata(&token_path).is_ok_and(|meta| meta.len() > 4096) {
        return Err("control token exceeds its size limit".into());
    }
    let token = std::fs::read_to_string(token_path)
        .map_err(|error| format!("control token unavailable: {error}"))?;
    let mut stream = LocalSocketStream::connect(locald_socket_name(&root)?)
        .map_err(|error| format!("control endpoint unavailable: {error}"))?;
    // Named pipes do not support synchronous read timeouts. Nonblocking I/O
    // bounds this handshake on both platforms without leaving a waiter thread.
    stream
        .set_nonblocking(true)
        .map_err(|error| error.to_string())?;
    writeln!(
        stream,
        "{}",
        json!({"v": 1, "cmd": "hello", "token": token.trim(), "client": "desktop"})
    )
    .map_err(|error| format!("daemon authentication failed: {error}"))?;
    stream
        .flush()
        .map_err(|error| format!("daemon authentication failed: {error}"))?;
    let line = ipc_read::handshake_line(&mut stream, LOCALD_HANDSHAKE_BUDGET, 1024 * 1024)
        .map_err(|error| format!("daemon handshake failed: {error}"))?;
    let hello: Value = serde_json::from_str(line.trim_end())
        .map_err(|error| format!("invalid daemon handshake: {error}"))?;
    if hello["event"].as_str() != Some("hello") || hello["protocol"].as_u64() != Some(1) {
        return Err("incompatible lemma-locald handshake".into());
    }
    stream
        .set_nonblocking(nonblocking)
        .map_err(|error| error.to_string())?;
    let (receive, send) = stream.split();
    Ok(LocaldConnection {
        reader: BufReader::new(receive),
        writer: send,
        hello,
    })
}

fn request_locald_replacement(connection: &mut LocaldConnection) -> Result<(), String> {
    writeln!(
        connection.writer,
        "{}",
        json!({"v": 1, "cmd": "shutdown-daemon", "id": "desktop-upgrade"})
    )
    .map_err(|error| format!("could not request daemon replacement: {error}"))?;
    connection
        .writer
        .flush()
        .map_err(|error| format!("could not request daemon replacement: {error}"))
}

fn wait_for_locald_exit(attempts: usize, reason: &str) -> Result<(), String> {
    let root = locald_root();
    let name = locald_socket_name(&root)?;
    for _ in 0..attempts {
        if LocalSocketStream::connect(name.clone()).is_err() {
            return Ok(());
        }
        std::thread::sleep(LOCALD_EXIT_POLL);
    }
    Err(format!(
        "the local service manager did not stop for {reason}"
    ))
}

fn replace_locald(connection: LocaldConnection) -> Result<(), String> {
    // An update has all the time it needs: the alternative is a new app beside
    // an old daemon, which is worse than a slow update.
    stop_locald(connection, "the app update", 450)
}

/// Stop the daemon, gracefully if it will and by verified identity if it will
/// not.
///
/// `reason` only names the occasion in the error text. The two occasions are an
/// app update, which replaces the daemon, and quitting, which must not leave
/// one behind -- see [`leave_nothing_running`].
fn stop_locald(
    mut connection: LocaldConnection,
    reason: &str,
    graceful_attempts: usize,
) -> Result<(), String> {
    let original_pid = connection.hello["pid"]
        .as_u64()
        .ok_or("the previous local service manager did not report its process identity")?;
    request_locald_replacement(&mut connection)?;
    drop(connection);
    if wait_for_locald_exit(graceful_attempts, reason).is_ok() {
        return Ok(());
    }

    // Old same-version builds can be trapped inside a runtime recovery
    // operation and reject their own graceful replacement command forever.
    // Re-authenticate immediately before the fallback, require the identical
    // PID and exact packaged executable path, then terminate only that daemon.
    // Its process ledgers let the replacement daemon reclaim app-owned
    // children without ever targeting unrelated host processes.
    let current = connect_locald().map_err(|error| {
        format!(
            "the previous local service manager remained busy and could not be verified: {error}"
        )
    })?;
    if current.hello["pid"].as_u64() != Some(original_pid) {
        return Err(format!(
            "the local service manager changed during {reason}; reopen Lemma to retry"
        ));
    }
    force_terminate_packaged_locald(original_pid)?;
    wait_for_locald_exit(LOCALD_FORCE_EXIT_ATTEMPTS, reason)
}

#[cfg(target_os = "macos")]
fn force_terminate_packaged_locald(pid: u64) -> Result<(), String> {
    let pid =
        i32::try_from(pid).map_err(|_| "the local service manager PID is invalid".to_string())?;
    if pid <= 1 || pid == std::process::id() as i32 {
        return Err("refusing to terminate an invalid local service manager process".into());
    }
    let actual = macos_process_path(pid)?;
    let expected =
        bundled_locald().ok_or("the packaged local service manager executable is missing")?;
    if actual != expected {
        return Err(format!(
            "refusing to stop an unexpected process during update: {}",
            actual.display()
        ));
    }
    stop_packaged_vz_child(pid)?;
    let result = unsafe { libc::kill(pid, libc::SIGTERM) };
    if result != 0 {
        return Err(format!(
            "could not terminate the stale local service manager: {}",
            std::io::Error::last_os_error()
        ));
    }
    Ok(())
}

#[cfg(target_os = "macos")]
fn macos_process_path(pid: i32) -> Result<PathBuf, String> {
    let mut buffer = vec![0_u8; libc::PROC_PIDPATHINFO_MAXSIZE as usize];
    let length = unsafe {
        libc::proc_pidpath(
            pid,
            buffer.as_mut_ptr().cast(),
            libc::PROC_PIDPATHINFO_MAXSIZE as u32,
        )
    };
    if length <= 0 {
        return Err("could not verify the local service manager executable".into());
    }
    let terminator = buffer
        .iter()
        .position(|byte| *byte == 0)
        .unwrap_or(buffer.len());
    Ok(PathBuf::from(
        String::from_utf8_lossy(&buffer[..terminator]).into_owned(),
    ))
}

#[cfg(target_os = "macos")]
fn stop_packaged_vz_child(parent_pid: i32) -> Result<(), String> {
    let output = Command::new("/usr/bin/pgrep")
        .args(["-P", &parent_pid.to_string()])
        .output()
        .map_err(|error| format!("could not inspect the previous runtime helpers: {error}"))?;
    if !output.status.success() {
        return Ok(());
    }
    let expected = bundled_vz()
        .ok_or("the packaged VM helper executable is missing")?
        .canonicalize()
        .map_err(|error| format!("could not verify the packaged VM helper: {error}"))?;
    for child_pid in String::from_utf8_lossy(&output.stdout)
        .lines()
        .filter_map(|value| value.trim().parse::<i32>().ok())
    {
        if child_pid <= 1 {
            continue;
        }
        let Ok(actual) = macos_process_path(child_pid) else {
            continue;
        };
        if actual.canonicalize().ok().as_ref() != Some(&expected) {
            continue;
        }
        // VZ handles SIGTERM as a graceful guest stop. Bound that path, then
        // force only the exact verified helper so an upgrade cannot leave the
        // private data disk attached to an orphan.
        if unsafe { libc::kill(child_pid, libc::SIGTERM) } != 0 {
            let error = std::io::Error::last_os_error();
            if error.raw_os_error() != Some(libc::ESRCH) {
                return Err(format!("could not stop the previous VM helper: {error}"));
            }
            continue;
        }
        let deadline = std::time::Instant::now() + VM_STOP_GRACE_BUDGET;
        while std::time::Instant::now() < deadline {
            if unsafe { libc::kill(child_pid, 0) } != 0 {
                break;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        if unsafe { libc::kill(child_pid, 0) } == 0 {
            let _ = unsafe { libc::kill(child_pid, libc::SIGKILL) };
            let reap_deadline = std::time::Instant::now() + VM_STOP_REAP_BUDGET;
            while std::time::Instant::now() < reap_deadline {
                if unsafe { libc::kill(child_pid, 0) } != 0 {
                    std::thread::sleep(Duration::from_millis(500));
                    break;
                }
                std::thread::sleep(Duration::from_millis(50));
            }
        }
    }
    Ok(())
}

#[cfg(not(target_os = "macos"))]
fn force_terminate_packaged_locald(_pid: u64) -> Result<(), String> {
    Err("the previous local service manager is still busy; quit Lemma and retry the update".into())
}

fn stop_locald_for_runtime_maintenance(app: &AppHandle) -> Result<(), String> {
    match connect_locald() {
        Ok(connection) => replace_locald(connection)?,
        Err(error) => {
            let root = locald_root();
            if LocalSocketStream::connect(locald_socket_name(&root)?).is_ok() {
                return Err(format!("A local service is still running but cannot be authenticated: {error}. Close that installation or restart this computer, then retry Recovery. No local data has been erased."));
            }
        }
    }
    let shell: State<Shell> = app.state();
    *shell.locald_writer.lock().unwrap() = None;
    Ok(())
}

fn start_after_runtime_maintenance(app: &AppHandle, request_id: &str) -> Result<(), String> {
    ensure_locald(app)?;
    send_local_operation(app, json!({"cmd":"start"}), operation_id(request_id))
}

fn install_locald_connection(app: &AppHandle, connection: LocaldConnection) {
    let shell: State<Shell> = app.state();
    *shell.locald_writer.lock().unwrap() = Some(connection.writer);
    let handle = app.clone();
    std::thread::spawn(move || {
        for line in connection.reader.lines().map_while(Result::ok) {
            if line.len() > 1024 * 1024 {
                emit_log(&handle, "locald protocol message exceeded 1 MiB");
                break;
            }
            match serde_json::from_str::<Value>(&line) {
                Ok(event) => handle_locald_event(&handle, &event),
                Err(_) => emit_log(&handle, &line),
            }
        }
        locald_gone(&handle);
    });
    // Ask once on connect so the tray reports real state instead of "checking…"
    // until something else happens to mention the Agent Host.
    let _ = send_to_locald(
        app,
        json!({"cmd": "agent-host.status", "id": operation_id("agent-host-initial")}),
    );
}

fn send_to_locald(app: &AppHandle, message: Value) -> Result<(), String> {
    let shell: State<Shell> = app.state();
    let mut guard = shell.locald_writer.lock().unwrap();
    let writer = guard.as_mut().ok_or("lemma-locald is not connected")?;
    writeln!(writer, "{message}").map_err(|e| format!("locald write failed: {e}"))?;
    writer
        .flush()
        .map_err(|e| format!("locald flush failed: {e}"))
}

/// What a refused operation says. Callers match on it to tell "the daemon is
/// busy" apart from "the daemon is broken", so it is one string in one place.
const LOCALD_BUSY: &str =
    "Lemma is still finishing another operation. Wait for that to finish, then try again.";

fn reserve_ui_operation(ui: &mut UiState, command: &str, id: &str) -> Result<(), String> {
    if !ui.active_operation_id.is_empty() && command != "shutdown-daemon" {
        return Err(LOCALD_BUSY.to_string());
    }
    if command == "shutdown-daemon" && !ui.active_operation_id.is_empty() {
        ui.completed_operation_ids
            .push(ui.active_operation_id.clone());
        if ui.completed_operation_ids.len() > 16 {
            ui.completed_operation_ids.remove(0);
        }
    }
    ui.active_operation_id = id.to_owned();
    Ok(())
}

fn send_local_operation(app: &AppHandle, mut request: Value, id: String) -> Result<(), String> {
    {
        let shell: State<Shell> = app.state();
        let mut ui = shell.ui.lock().unwrap();
        reserve_ui_operation(&mut ui, request["cmd"].as_str().unwrap_or_default(), &id)?;
    }
    request["id"] = Value::String(id.clone());
    if let Err(error) = send_to_locald(app, request) {
        let shell: State<Shell> = app.state();
        let mut ui = shell.ui.lock().unwrap();
        if ui.active_operation_id == id {
            ui.active_operation_id.clear();
        }
        return Err(error);
    }
    Ok(())
}

fn locald_gone(app: &AppHandle) {
    let shell: State<Shell> = app.state();
    *shell.locald_writer.lock().unwrap() = None;
    let snapshot = {
        let mut ui = shell.ui.lock().unwrap();
        if ui.running {
            ui.status = "Local service manager disconnected".into();
            ui.error = true;
            ui.error_code = "locald-disconnected".into();
            ui.running = false;
        }
        ui.active_operation_id.clear();
        ui.clone()
    };
    let _ = app.emit("lemma:state", snapshot);
    let _ = app.emit_to("control", "lemma:locald-disconnected", ());
    // The tray is driven from `handle_locald_event`, which by definition stops
    // arriving when the daemon does -- so the menu bar kept reading
    // "Lemma: running" and "Agent Host: connected" indefinitely after the stack
    // had gone, which is exactly when someone looks at it.
    refresh_tray_status(app);
    refresh_agent_host_tray(app, &json!({"available": false}));
    if current_mode(app) == "local" && app.get_webview("control").is_none() {
        show_splash(app);
    }
}

fn emit_log(app: &AppHandle, line: &str) {
    if !line.is_empty() {
        let _ = app.emit("lemma:log", line.to_string());
    }
}

fn locald_event_operation_id(event: &Value) -> Option<&str> {
    event
        .get("operation_id")
        .and_then(Value::as_str)
        .or_else(|| {
            matches!(event["event"].as_str(), Some("ack" | "done" | "error"))
                .then(|| event.get("id").and_then(Value::as_str))
                .flatten()
        })
}

fn event_applies_during_shutdown(event: &Value) -> bool {
    match event["event"].as_str().unwrap_or_default() {
        "log" => true,
        "status" | "state" | "ready" | "runtime.prepared" => false,
        _ => locald_event_operation_id(event).is_some(),
    }
}

/// Fold one daemon event into the shell's view of the world.
///
/// Pulled out of `handle_locald_event`, which was 391 lines mixing this with
/// window navigation, tray refreshes and quit completion — and had no test at
/// all, on the path that decides what every screen shows. Everything here is a
/// function of the previous state and the event; what the caller must *do*
/// comes back as [`EventOutcome`] rather than happening in the middle.
///
/// `log` is not handled here: it is the one kind that only forwards, and it
/// needs no state, so the caller takes it before acquiring the lock.
fn apply_locald_event(ui: &mut UiState, kind: &str, event: &Value) -> EventOutcome {
    let mut outcome = EventOutcome::default();
    let event_operation_id = locald_event_operation_id(event);
    let ui = &mut *ui;
    match kind {
        "phase" => {
            ui.phase = event["label"].as_str().unwrap_or_default().into();
            ui.phase_key = event["key"].as_str().unwrap_or_default().into();
            ui.progress = event["progress"].as_u64().unwrap_or(0);
            ui.eta_seconds = event["eta_s"].as_u64();
            ui.downloaded_bytes = None;
            ui.total_bytes = None;
            ui.throughput_bytes_per_second = None;
            ui.setup = event["setup"].as_bool().unwrap_or(ui.setup);
            if let Some(component) = event["component"].as_str() {
                ui.component = component.into();
            }
            if let Some(source) = event["log_source"].as_str() {
                ui.log_source = source.into();
            }
            let detail = event["detail"].as_str().unwrap_or_default();
            ui.status = if detail.is_empty() {
                ui.phase.clone()
            } else {
                format!("{}: {}", ui.phase, detail)
            };
            ui.ready = false;
            ui.error = ui.phase_key == "error";
            if !ui.error {
                ui.error_code.clear();
            }
        }
        "state" => {
            ui.running = event["running"].as_bool().unwrap_or(false);
            ui.ready = event["ready"].as_bool().unwrap_or(false);
            let event_status = event["status"].as_str().unwrap_or_default();
            let event_is_error = event_status == "error";
            let keep_actionable_error =
                is_actionable_runtime_error(&ui.error_code) && !ui.ready && !event_is_error;
            ui.error = event_is_error || keep_actionable_error;
            if !ui.error {
                ui.error_code.clear();
            }
            if event_status == "stopped" && !ui.error {
                ui.phase = "Stopped".into();
                ui.phase_key = "stopped".into();
                ui.progress = 0;
                ui.eta_seconds = None;
                ui.downloaded_bytes = None;
                ui.total_bytes = None;
                ui.throughput_bytes_per_second = None;
                ui.status = "Local services are stopped".into();
            }
        }
        "status" => {
            ui.running = event["running"].as_bool().unwrap_or(ui.running);
            ui.ready = event["ready"].as_bool().unwrap_or(ui.ready);
            let event_status = event["status"].as_str().unwrap_or_default();
            let preserve_inflight_phase = should_preserve_inflight_phase(
                &ui.active_operation_id,
                &ui.phase_key,
                event_status,
            );
            let event_is_error = event_status == "error";
            let keep_actionable_error =
                is_actionable_runtime_error(&ui.error_code) && !ui.ready && !event_is_error;
            let keep_terminal_error =
                ui.error && !ui.ready && event_status == "stopped" && !event_is_error;
            ui.error = event_is_error || keep_actionable_error || keep_terminal_error;
            if !ui.error {
                ui.error_code.clear();
            }
            if let (Some(url), Some(api_url)) = (event["url"].as_str(), event["api_url"].as_str()) {
                if trusted_workspace_urls(url, api_url) {
                    ui.url = url.to_string();
                    ui.api_url = api_url.to_string();
                }
            }
            if !keep_actionable_error && !keep_terminal_error && !preserve_inflight_phase {
                let phase = event.get("phase").and_then(Value::as_object);
                if event_status == "stopped" && !ui.error {
                    // Lifecycle state wins over persisted progress. Older
                    // daemons may legitimately report stopped while their
                    // last phase still says ready/100%.
                    ui.phase = "Stopped".into();
                    ui.phase_key = "stopped".into();
                    ui.progress = 0;
                    ui.eta_seconds = None;
                    ui.downloaded_bytes = None;
                    ui.total_bytes = None;
                    ui.throughput_bytes_per_second = None;
                    ui.status = "Local services are stopped".into();
                } else if let Some(phase) = phase {
                    ui.phase = phase
                        .get("label")
                        .and_then(Value::as_str)
                        .unwrap_or(&ui.phase)
                        .to_string();
                    ui.phase_key = phase
                        .get("key")
                        .and_then(Value::as_str)
                        .unwrap_or(&ui.phase_key)
                        .to_string();
                    ui.progress = phase
                        .get("progress")
                        .and_then(Value::as_u64)
                        .unwrap_or(ui.progress);
                    ui.downloaded_bytes = None;
                    ui.total_bytes = None;
                    ui.throughput_bytes_per_second = None;
                    let detail = phase.get("detail").and_then(Value::as_str).unwrap_or("");
                    ui.status = if detail.is_empty() {
                        ui.phase.clone()
                    } else {
                        format!("{}: {detail}", ui.phase)
                    };
                }
            }
        }
        "ready" => {
            if !ui.ready {
                launch_trace("daemon reported ready");
            }
            ui.ready = true;
            ui.running = true;
            ui.error = false;
            ui.error_code.clear();
            ui.downloaded_bytes = None;
            ui.total_bytes = None;
            ui.throughput_bytes_per_second = None;
            // Main, API, built-app, and workspace-app hosts all live below
            // the reserved lemma.localhost loopback cookie boundary.
            if let (Some(url), Some(api_url)) = (event["url"].as_str(), event["api_url"].as_str()) {
                if trusted_workspace_urls(url, api_url) {
                    ui.url = url.to_string();
                    ui.api_url = api_url.to_string();
                    // Record what is serving, and under which generation,
                    // so the next launch can skip straight to it.
                    //
                    // On a worker, because this writes the config with two
                    // fsyncs and we are holding `shell.ui` -- a lock the main
                    // thread takes in `navigation_context` (on every
                    // navigation, subframes included), `get_state`,
                    // `current_mode`, `refresh_tray_status` and
                    // `quit_impact`. Holding it across a disk sync stalled
                    // WebKit's navigation delegate, worst exactly when the
                    // disk is busy unpacking a runtime. The resume target is
                    // advisory, so late is fine and lost is survivable.
                    let (url, api_url, generation) = (
                        url.to_string(),
                        api_url.to_string(),
                        event["runtime_generation"]
                            .as_str()
                            .unwrap_or_default()
                            .to_string(),
                    );
                    std::thread::spawn(move || {
                        write_resume_target(&url, &api_url, &generation);
                    });
                }
            }
            // Navigation is not decided here. The tail of this
            // function owns ready -> workspace, for every event kind
            // that can carry readiness; deciding it in two places is
            // how one of them ended up never running.
        }
        "sharing.changed" => {
            if let (Some(url), Some(api_url)) = (event["url"].as_str(), event["api_url"].as_str()) {
                if trusted_workspace_urls(url, api_url) {
                    ui.url = url.to_owned();
                    ui.api_url = api_url.to_owned();
                }
            }
        }
        "error" => {
            let code = event["code"].as_str().unwrap_or_default();
            if code == "busy" {
                // Every authenticated desktop client already receives the
                // in-flight operation's broadcast progress. A repeated
                // Start click is therefore informational, not a failure.
                ui.error = false;
                ui.error_code.clear();
                ui.status = if ui.phase.is_empty() {
                    "Lemma is already working on that operation…".into()
                } else {
                    format!("{} is still in progress…", ui.phase)
                };
                if event_operation_id.is_some_and(|id| id == ui.active_operation_id) {
                    ui.active_operation_id.clear();
                }
            } else if code.starts_with("sharing-") {
                // Sharing failures are shown inside Local settings. They
                // must not replace an otherwise healthy workspace with the
                // startup error screen.
                ui.error = false;
                ui.error_code.clear();
            } else {
                ui.error = true;
                ui.error_code = code.into();
                ui.status = event["message"].as_str().unwrap_or("startup failed").into();
                if let Some(component) = event["component"].as_str() {
                    ui.component = component.into();
                }
                if let Some(source) = event["log_source"].as_str() {
                    ui.log_source = source.into();
                }
            }
        }
        "sandbox-images" => {
            // Deliberately touches nothing else. This runs after the
            // workspace is up, so writing `phase`/`ready` here would send
            // an app the user is already working in back to the splash to
            // report a download they never asked about.
            ui.sandbox_images = event["state"].as_str().unwrap_or_default().into();
            ui.sandbox_images_detail = event["detail"].as_str().unwrap_or_default().into();
        }
        "runtime.prepared" => {
            let ready = event["ready"].as_bool().unwrap_or(false);
            let reboot_required = event["reboot_required"].as_bool().unwrap_or(!ready);
            ui.ready = false;
            ui.running = false;
            ui.phase = "Preparing Windows".into();
            ui.phase_key = "runtime".into();
            if ready {
                ui.error = false;
                ui.error_code.clear();
                ui.status = "Windows runtime is ready. Starting Lemma…".into();
                outcome.start_after_prepare = ui.mode == "local";
            } else if reboot_required {
                ui.error = true;
                ui.error_code = "wsl-reboot-required".into();
                ui.status =
                        "Restart Windows to finish setup, then reopen Lemma; setup will continue automatically"
                            .into();
            }
        }
        "done" if event_operation_id.is_some_and(|id| id == ui.active_operation_id) => {
            let completed_operation_id = ui.active_operation_id.clone();
            ui.completed_operation_ids.push(completed_operation_id);
            if ui.completed_operation_ids.len() > 16 {
                ui.completed_operation_ids.remove(0);
            }
            ui.active_operation_id.clear();
        }
        _ => {}
    }
    if ui.mode == "local" && ui.ready && !trusted_workspace_urls(&ui.url, &ui.api_url) {
        ui.ready = false;
        ui.running = false;
        ui.error = true;
        ui.error_code = "untrusted-workspace-origin".into();
        ui.phase = "Local services need attention".into();
        ui.phase_key = "error".into();
        ui.progress = 0;
        ui.status = "locald did not provide an authenticated, isolated workspace origin".into();
    }
    if ui.ready || !ui.error {
        ui.terminal_recovery_pending = false;
    }
    let schedule_terminal_recovery = matches!(kind, "state" | "status")
        && ui.error
        && !ui.ready
        && !ui.terminal_recovery_pending;
    if schedule_terminal_recovery {
        ui.terminal_recovery_pending = true;
    }
    outcome.schedule_terminal_recovery = schedule_terminal_recovery;
    outcome
}

/// What the caller must do once the state has been folded.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
struct EventOutcome {
    /// A terminal error just appeared, and recovery options should be fetched.
    schedule_terminal_recovery: bool,
    /// The runtime finished preparing, so the stack should be started.
    start_after_prepare: bool,
}

/// What to do with an event that names the operation it belongs to.
///
/// The whole of a decision that decides what somebody watching the splash
/// sees, and the reason it is a function: `handle_locald_event` needs an
/// `AppHandle` and a Tauri runtime, so none of this was reachable from a test
/// and every case below was only ever exercised by using the app.
///
/// The daemon serves one operation at a time but several surfaces can ask, and
/// their replies interleave. Showing another operation's progress on the
/// splash is not cosmetic: its phases and its errors are about work the person
/// in front of it did not start.
#[derive(Debug, PartialEq, Eq)]
enum EventAdmission {
    /// It belongs to the operation already on screen.
    Apply,
    /// Nothing is on screen and this operation has not finished, so it becomes
    /// the one being shown.
    Adopt,
    /// Another operation's, or one whose completion has already been shown.
    /// The second is what stops a late straggler from reopening a finished
    /// run's progress after the splash has moved on.
    Ignore,
}

fn admit_locald_event(active: &str, completed: &[String], event: &str) -> EventAdmission {
    if !active.is_empty() {
        return if active == event {
            EventAdmission::Apply
        } else {
            EventAdmission::Ignore
        };
    }
    if completed.iter().any(|finished| finished == event) {
        return EventAdmission::Ignore;
    }
    EventAdmission::Adopt
}

fn handle_locald_event(app: &AppHandle, event: &Value) {
    if std::env::var("LEMMA_DESKTOP_DEBUG").as_deref() == Ok("1") {
        eprintln!("[locald] {event}");
    }
    let shell: State<Shell> = app.state();
    let kind = event["event"].as_str().unwrap_or_default();
    if shell.quit_after_stop.load(Ordering::Acquire) && !event_applies_during_shutdown(event) {
        return;
    }
    let event_operation_id = locald_event_operation_id(event);
    if let Some(event_operation_id) = event_operation_id {
        let mut ui = shell.ui.lock().unwrap();
        match admit_locald_event(
            &ui.active_operation_id,
            &ui.completed_operation_ids,
            event_operation_id,
        ) {
            EventAdmission::Ignore => return,
            EventAdmission::Adopt => ui.active_operation_id = event_operation_id.to_owned(),
            EventAdmission::Apply => {}
        }
    }
    let _ = app.emit_to("control", "lemma:locald-event", event.clone());
    // Every reply that carries Agent Host state refreshes the tray, so a change
    // made in one surface shows in the others without anyone polling.
    if let Some(status) = event.get("agent_host").filter(|value| value.is_object()) {
        *shell.agent_host_status.lock().unwrap() = Some(status.clone());
        refresh_agent_host_tray(app, status);
    }
    // Same reason, for sharing: several events carry it, and Quit needs the last
    // known answer without asking.
    if let Some(mode) = event
        .get("sharing")
        .and_then(|sharing| sharing.get("mode"))
        .and_then(Value::as_str)
    {
        *shell.sharing_mode.lock().unwrap() = Some(mode.to_owned());
    }

    if kind == "log" {
        emit_log(app, event["line"].as_str().unwrap_or_default());
        return;
    }
    let (snapshot, outcome) = {
        let mut ui = shell.ui.lock().unwrap();
        let outcome = apply_locald_event(&mut ui, kind, event);
        (ui.clone(), outcome)
    };
    let schedule_terminal_recovery = outcome.schedule_terminal_recovery;
    let start_after_prepare = outcome.start_after_prepare;

    let ready_workspace_url = (matches!(kind, "ready" | "state" | "status")
        && snapshot.mode == "local"
        && snapshot.ready
        && !snapshot.error)
        .then(|| snapshot.url.clone());
    let _ = app.emit("lemma:state", snapshot);
    refresh_tray_status(app);
    if let Some(url) = ready_workspace_url {
        if main_window_needs_workspace(app, &url) {
            // Prefer the route the last session ended on. Opening the root
            // instead means loading the app once to authenticate and resolve
            // the last pod, then loading it again at the pod it resolved to.
            // A cold first run has no resume target and no account, so the
            // root would load once to discover that, redirect to signup, and
            // load again. Go straight there: one page load, and the first
            // screen is deterministic instead of depending on a client redirect.
            let target = read_resume_target()
                .filter(|target| target.url == url)
                .map(|target| resume_entry_url(&target))
                .unwrap_or_else(|| local_auth_url_returning_to(&url, "signup", "/"));
            let _ = open_app_window(app, &target);
        }
    }
    if kind == "sharing.changed" {
        if let (Some(url), Some(api_url)) = (event["url"].as_str(), event["api_url"].as_str()) {
            if trusted_workspace_urls(url, api_url) {
                let _ = open_app_window(app, url);
            }
        }
    }
    let quit_after_stop = kind == "done"
        && event["cmd"].as_str() == Some("shutdown-daemon")
        && event["ok"].as_bool() == Some(true)
        && shell.quit_after_stop.swap(false, Ordering::AcqRel);
    if quit_after_stop {
        finish_quit_after_daemon(app);
        return;
    }
    if schedule_terminal_recovery {
        let app = app.clone();
        std::thread::spawn(move || {
            std::thread::sleep(Duration::from_secs(8));
            let should_recover = {
                let shell: State<Shell> = app.state();
                let ui = shell.ui.lock().unwrap();
                ui.terminal_recovery_pending && ui.error && !ui.ready && ui.mode == "local"
            };
            if should_recover {
                show_splash(&app);
            }
        });
    }
    if start_after_prepare {
        let app = app.clone();
        std::thread::spawn(move || {
            // The daemon releases its single-operation guard immediately after
            // publishing runtime.prepared. Avoid racing the follow-up start.
            std::thread::sleep(Duration::from_millis(250));
            let _ = send_local_operation(
                &app,
                json!({"cmd":"start"}),
                operation_id("shell-start-after-runtime-prepare"),
            );
        });
    }
    // Do not navigate an already-open workspace back to the installer for
    // transient component events. The splash is already visible during setup;
    // a lost daemon uses locald_gone(), the terminal recovery path.
}

fn is_actionable_runtime_error(code: &str) -> bool {
    matches!(
        code,
        "wsl-required" | "wsl-reboot-required" | "wsl-setup-denied"
    )
}

fn should_preserve_inflight_phase(
    active_operation_id: &str,
    phase_key: &str,
    status: &str,
) -> bool {
    !active_operation_id.is_empty()
        && status == "stopped"
        && !matches!(phase_key, "" | "boot" | "stopped" | "ready" | "error")
}

/// Match the Dock icon to whether anything is actually on screen.
///
/// `Accessory` removes the Dock tile and the app menu bar, which is right when
/// the last window has gone: there is nothing for those menus to act on, and
/// every verb they carry is in the tray menu too.
///
/// Conditional on *every* window, not just the main one. A pod app opens in a
/// window of its own, so closing the workspace while an app is still up used to
/// drop the Dock tile out from under a window that was still visible -- leaving
/// it unreachable by ⌘-tab and belonging to an app the Dock said was not there.
#[cfg(target_os = "macos")]
fn settle_dock_presence(app: &AppHandle) {
    // Minimised counts. `is_visible()` is `[NSWindow isVisible]`, which is NO
    // for a miniaturised window -- so minimising a pod app and then closing the
    // workspace dropped the Dock tile while that app was still there, sitting
    // in the Dock as a window belonging to an application the Dock no longer
    // showed, with no way to ⌘-tab back to it.
    let anything_on_screen = app.windows().values().any(|window| {
        window.is_visible().unwrap_or(false) || window.is_minimized().unwrap_or(false)
    });
    let _ = app.set_activation_policy(if anything_on_screen {
        tauri::ActivationPolicy::Regular
    } else {
        tauri::ActivationPolicy::Accessory
    });
}

/// Come back to the Dock. Called on every path that puts the window back on
/// screen, because a visible window with no Dock icon cannot be ⌘-tabbed to and
/// looks like a different app's stray panel.
#[cfg(target_os = "macos")]
fn restore_dock_presence(app: &AppHandle) {
    let _ = app.set_activation_policy(tauri::ActivationPolicy::Regular);
}

/// No Dock to leave or return to off macOS; the tray behaviour is the same.
#[cfg(not(target_os = "macos"))]
fn restore_dock_presence(_app: &AppHandle) {}

fn open_app_window(app: &AppHandle, url: &str) -> Result<(), String> {
    let target = tauri::Url::parse(url).map_err(|error| format!("invalid app URL: {error}"))?;
    // Rebuilt rather than refused when it is missing. The window is destroyed
    // and recreated when the user changes servers, so "not available" is now a
    // state the app can legitimately be in -- and if the recreate failed, this
    // is the path the tray's Open uses to ask for it again. Refusing here would
    // leave a running app whose only interface is a tray icon that cannot open
    // anything, with no way back except quitting.
    if app.get_window("main").is_none() {
        let mode = current_mode(app);
        build_main_window(app, &mode, WebviewUrl::App("index.html".into()), true)
            .map_err(|error| format!("could not reopen the window: {error}"))?;
    }
    let window = app
        .get_window("main")
        .ok_or("main window is not available")?;
    app.get_webview("main")
        .ok_or("main webview is not available")?
        .navigate(target)
        .map_err(|error| format!("could not open {url}: {error}"))?;
    // Before showing, so the icon and the window arrive together rather than
    // the window appearing under a Dock that has not noticed yet.
    restore_dock_presence(app);
    let _ = window.show();
    let _ = window.set_focus();
    Ok(())
}

/// Should a `ready` event navigate the main window to `workspace`?
///
/// Yes from the splash, which is the ordinary first start. Also yes from a
/// *different* workspace origin, which is the case an optimistic resume creates:
/// the window opened the workspace the last session left, and then the stack it
/// was pointing at was replaced and came back on new ports. Nothing was showing
/// the splash at that point, so the old splash-only test left the window on a
/// dead port forever.
///
/// Still no from the current workspace. Product spec §3.5 is explicit that a
/// stable workspace must not be navigated for transient component recovery, and
/// re-navigating it would throw away whatever the user was doing.
fn main_window_needs_workspace(app: &AppHandle, workspace: &str) -> bool {
    let Some(url) = app.get_webview("main").and_then(|window| window.url().ok()) else {
        return false;
    };
    if native_splash_url(&url) {
        return true;
    }
    !same_origin(&url, workspace)
}

/// Where a bundled page lives for the build we are actually running.
///
/// Navigation and privileged IPC must agree with the asset origin Tauri serves:
/// Windows rewrites the custom protocol to HTTP; development uses its own port.
fn native_asset_url(path: &str) -> String {
    native_assets::url(path, cfg!(debug_assertions).then_some(DEV_ASSET_PORT))
}

fn native_splash_url(url: &tauri::Url) -> bool {
    let path = matches!(url.path(), "/" | "/index.html");
    path && trusted_native_asset_url(url)
}

fn navigate_app_window(app: &AppHandle, url: &str) -> Result<(), String> {
    open_app_window(app, url)
}

/// The window label pod apps open into.
///
/// One label, reused: clicking "open in new window" five times should raise the
/// same window five times, not leave five identical ones behind.
const POD_APP_WINDOW: &str = "pod-app";

/// Open a published pod app in its own window.
///
/// It used to navigate the *main* window, which replaced the whole Lemma UI
/// with the app and left no way back -- no tab, no back button, nothing but
/// quitting. A real window can be closed, moved and ⌘-tabbed, and Lemma is
/// still there underneath when it goes.
///
/// Deliberately not covered by any file in `desktop/capabilities/`, which are
/// scoped by webview label: this window runs user-authored code, so it gets no
/// Tauri command surface at all. Adding a capability for this label would hand
/// every pod app the IPC bridge.
/// What to call an app's window: its public slug, made readable.
///
/// From the host, not the path -- the slug identifies the app and the path is
/// wherever the user happens to be inside it, so a window would otherwise be
/// renamed by navigation.
fn pod_app_window_title(target: &tauri::Url) -> String {
    target
        .host_str()
        .and_then(|host| host.split('.').next())
        .filter(|label| !label.is_empty())
        .map(|label| label.replace('-', " "))
        .unwrap_or_else(|| "Lemma app".to_owned())
}

fn open_pod_app_window(app: &AppHandle, url: &str) -> Result<(), String> {
    let target = tauri::Url::parse(url).map_err(|error| format!("invalid app URL: {error}"))?;

    // Named from the host rather than the path: the slug is the app's identity
    // and the path is wherever the user happens to be inside it.
    let title = pod_app_window_title(&target);

    if let Some(existing) = app.get_webview_window(POD_APP_WINDOW) {
        existing
            .navigate(target)
            .map_err(|error| format!("could not open {url}: {error}"))?;
        // Retitled, because the window is reused. Opening a second app left the
        // first one's name on the title bar, the Window menu and ⌘-tab, so the
        // window said "study lab" while rendering payroll.
        let _ = existing.set_title(&title);
        let _ = existing.show();
        let _ = existing.set_focus();
        restore_dock_presence(app);
        return Ok(());
    }

    let mode = current_mode(app);
    let builder = WebviewWindowBuilder::new(app, POD_APP_WINDOW, WebviewUrl::External(target))
        .title(title)
        .inner_size(1180.0, 800.0)
        .min_inner_size(420.0, 400.0)
        .background_color(CANVAS_LIGHT);

    // The same cookie jar the workspace uses, or the app has no session.
    //
    // A webview with no explicit store gets WebKit's default one, and the main
    // window is deliberately *not* on that -- it is partitioned per server so
    // signing into Local and Cloud are separate sessions. So an app window
    // without this line opens on an empty jar: the page renders, because assets
    // are served unauthenticated, and then every SDK call 401s. That is exactly
    // the bug this whole window exists downstream of, reintroduced one window
    // over, and it is invisible to a test that drives its own WKWebView.
    #[cfg(target_os = "macos")]
    let builder = builder.data_store_identifier(session_partition_id(&mode));
    #[cfg(target_os = "windows")]
    let builder = builder.data_directory(session_partition_dir(&mode));

    builder
        .on_navigation({
            let handle = app.clone();
            move |url| {
                // Held to the same gate as anywhere else, so an app cannot walk
                // this window somewhere the main one would have refused.
                let (mode, app_base, api_base) = navigation_context(&handle);
                match navigation_disposition(url, &mode, &app_base, &api_base) {
                    NavigationDisposition::Allow => true,
                    NavigationDisposition::OpenExternal => {
                        open_external(url.as_str());
                        false
                    }
                    NavigationDisposition::Deny => false,
                }
            }
        })
        .on_new_window({
            let handle = app.clone();
            move |url, _features| {
                // The same decision the workspace makes, not a looser one.
                //
                // This used to ask `navigation_disposition`, which answers a
                // different question: it admits `about:blank` and our own
                // bundled `tauri://` pages because subframes need them. Routed
                // through here that meant `window.open('about:blank')` from app
                // code reached `open_external` and launched the user's browser,
                // and a plain link back to the workspace opened Lemma in Safari
                // -- where there is no session at all.
                let (mode, app_base, api_base) = navigation_context(&handle);
                match new_window_disposition(&url, &mode, &app_base, &api_base) {
                    // A link to another app belongs in this window, which is
                    // the one the user is already looking at an app in.
                    NewWindowDisposition::OpenAppWindow | NewWindowDisposition::NavigateInApp => {
                        if let Err(error) = open_pod_app_window(&handle, url.as_str()) {
                            append_install_log(&format!(
                                "could not follow a link out of a pod app: {error}"
                            ));
                        }
                    }
                    NewWindowDisposition::OpenExternal => open_external(url.as_str()),
                    NewWindowDisposition::Deny => {}
                }
                NewWindowResponse::Deny
            }
        })
        .on_download({
            let handle = app.clone();
            move |_webview, event| match event {
                // Registering a policy at all is what makes downloads work:
                // with no handler the webview cancels the navigation outright
                // and says nothing, which is how Download buttons came to do
                // nothing on macOS. An app that exports a CSV is an ordinary
                // app, so this window needs the same policy the workspace has.
                DownloadEvent::Requested { url, .. } => {
                    let (mode, app_base, api_base) = navigation_context(&handle);
                    download_disposition(&url, &mode, &app_base, &api_base)
                }
                _ => true,
            }
        })
        .build()
        .map_err(|error| format!("could not open the app window: {error}"))?;
    Ok(())
}

/// Where the Dock icon should take somebody when every window is closed.
///
/// Separate from the arm that uses it because that arm needs a Tauri runtime
/// and this is the part with cases in it. Splash is the fallback on purpose:
/// while the stack is still coming up, or after it failed, the splash is where
/// the state and the actions are, and a workspace URL that is not serving yet
/// would open on an error page instead.
#[cfg(target_os = "macos")]
#[derive(Debug, PartialEq, Eq)]
enum ReopenTarget {
    Hosted,
    Workspace(String),
    Splash,
}

#[cfg(target_os = "macos")]
fn reopen_target(mode: &str, ready: bool, error: bool, url: &str) -> ReopenTarget {
    match mode {
        // Nothing local has to be ready for hosted to be reachable.
        "hosted" => ReopenTarget::Hosted,
        "local" if ready && !error && !url.is_empty() => ReopenTarget::Workspace(url.to_owned()),
        _ => ReopenTarget::Splash,
    }
}

fn show_splash(app: &AppHandle) {
    let _ = open_app_window(app, &native_asset_url("index.html"));
}

/// The splash, told what it is watching.
///
/// The intent rides in the query string because navigation discards the old
/// page. A stop must display shutdown progress even before its first state
/// event arrives. Startup itself is owned by the shell, never page loading.
fn show_splash_with_intent(app: &AppHandle, intent: &str) {
    let _ = open_app_window(
        app,
        &format!("{}?intent={intent}", native_asset_url("index.html")),
    );
}

fn show_control_center(app: &AppHandle) -> Result<(), String> {
    show_control_center_page(app, None)
}

fn control_navigation_allowed(url: &tauri::Url) -> bool {
    trusted_control_url(url)
}

/// Normalise a Local settings page name, or say it is not one.
fn control_center_page(page: Option<&str>) -> Result<String, String> {
    let page = match page.unwrap_or("overview") {
        "connectors" => "integrations",
        "services" => "runtime",
        "surfaces" => "channels",
        page => page,
    };
    if !matches!(
        page,
        "overview"
            | "computer"
            | "ai"
            | "sharing"
            | "integrations"
            | "channels"
            | "runtime"
            | "updates"
            | "recovery"
            | "diagnostics"
    ) {
        return Err(format!("unknown Local settings page: {page}"));
    }
    Ok(page.to_owned())
}

/// Bring Local settings up on `page`, and finish only when it is up.
///
/// Blocking on purpose, and never to be called from the main thread: Tauri
/// documents a Windows deadlock when child webviews are created from
/// synchronous commands or event handlers. Run from a worker, `add_child`
/// marshals the build onto the main thread by itself.
fn open_control_center_blocking(app: &AppHandle, page: &str) -> Result<(), String> {
    if let Some(webview) = app.get_webview("control") {
        if let Some(main) = app.get_window("main") {
            restore_dock_presence(app);
            let _ = main.show();
            let _ = main.set_focus();
        }
        webview.set_focus().map_err(|error| error.to_string())?;
        let _ = app.emit_to("control", "lemma:control-page", page);
        return Ok(());
    }
    create_control_child(app, page)
}

fn show_control_center_page(app: &AppHandle, page: Option<&str>) -> Result<(), String> {
    let page = control_center_page(page)?;
    let handle = app.clone();
    // `menu_background`, rather than a bare thread that swallowed the result.
    // A failure here used to be announced as `lemma:control-error`, an event
    // with no listener anywhere in the app: choosing Local settings from the
    // menu and having it fail produced no window, no message, and nothing in
    // any log a person could reach. This writes the launch log and puts the
    // reason on screen, like every other menu action that fails.
    menu_background(app, "Local settings", move || {
        open_control_center_blocking(&handle, &page)
    });
    Ok(())
}

fn create_control_child(app: &AppHandle, page: &str) -> Result<(), String> {
    if app.get_webview("control").is_some() {
        let _ = app.emit_to("control", "lemma:control-page", page);
        return Ok(());
    }
    let main = app
        .get_window("main")
        .ok_or("main window is not available")?;
    restore_dock_presence(app);
    main.show().map_err(|error| error.to_string())?;
    let initial_script = format!(
        "{}window.__LEMMA_CONTROL_PAGE__={};",
        desktop_context_script(&current_mode(app)),
        serde_json::to_string(page).unwrap_or_else(|_| "\"overview\"".into())
    );
    let builder = WebviewBuilder::new("control", WebviewUrl::App("control.html".into()))
        .auto_resize()
        .focused(true)
        .initialization_script(initial_script)
        .on_navigation(move |url| {
            let allowed = control_navigation_allowed(url);
            if std::env::var("LEMMA_DESKTOP_CONTROL_DEBUG").as_deref() == Ok("1") || !allowed {
                eprintln!("[control-navigation] allowed={allowed} url={url}");
            }
            allowed
        })
        .on_new_window(move |url, _features| {
            if matches!(url.scheme(), "http" | "https") {
                open_external(url.as_str());
            }
            NewWindowResponse::Deny
        });
    let parent = &main;
    let size = parent.inner_size().map_err(|error| error.to_string())?;
    let webview = parent
        .add_child(builder, PhysicalPosition::new(0, 0), size)
        .map_err(|error| error.to_string())?;
    webview
        .set_auto_resize(true)
        .map_err(|error| error.to_string())?;
    webview.set_focus().map_err(|error| error.to_string())?;
    let _ = app.emit_to("control", "lemma:control-page", page);
    Ok(())
}

// ---------------------------------------------------------------------------
// Commands (same verbs as the Electron IPC surface)
// ---------------------------------------------------------------------------

#[tauri::command]
fn open_developer_tools(window: Webview, app: AppHandle) -> Result<(), String> {
    require_control_window(&window)?;
    let main = app
        .get_webview("main")
        .ok_or("main window is not available")?;
    main.open_devtools();
    let _ = main.window().show();
    let _ = main.set_focus();
    Ok(())
}

#[tauri::command]
/// Runs off the UI thread. A synchronous `#[tauri::command]` is dispatched on
/// the main thread, so any command that waits on the daemon, the network or a
/// child process freezes the window for its whole duration -- which is how a
/// first launch showed a black, unresponsive app for minutes while the runtime
/// installed and the daemon came up.
async fn start(window: Webview, app: AppHandle) -> Result<(), String> {
    require_agent_host_caller(&window, &app)?;
    require_local_native_window(&window)?;
    tauri::async_runtime::spawn_blocking(move || start_impl(app))
        .await
        .map_err(|error| error.to_string())?
}

fn start_impl(app: AppHandle) -> Result<(), String> {
    let mode = current_mode(&app);
    if mode == "undecided" {
        return Err("choose a connection mode first".into());
    }
    if mode == "hosted" {
        return open_app_window(&app, &hosted_url());
    }
    ensure_locald(&app)?;
    let setup = std::env::var("LEMMA_DESKTOP_START_SETUP").as_deref() == Ok("1");
    send_local_operation(
        &app,
        json!({"cmd": "start", "setup": setup}),
        operation_id("shell-start"),
    )
}

#[tauri::command]
/// Runs off the UI thread. A synchronous `#[tauri::command]` is dispatched on
/// the main thread, so any command that waits on the daemon, the network or a
/// child process freezes the window for its whole duration -- which is how a
/// first launch showed a black, unresponsive app for minutes while the runtime
/// installed and the daemon came up.
async fn stop(window: Webview, app: AppHandle, include_infra: Option<bool>) -> Result<(), String> {
    require_local_native_window(&window)?;
    tauri::async_runtime::spawn_blocking(move || stop_impl(app, include_infra))
        .await
        .map_err(|error| error.to_string())?
}

fn stop_impl(app: AppHandle, include_infra: Option<bool>) -> Result<(), String> {
    if app.state::<Shell>().quit_confirmed.load(Ordering::Acquire) {
        stop_then_quit(&app);
        return Ok(());
    }
    if current_mode(&app) != "local" {
        return Err("local services are not active in Lemma Cloud mode".into());
    }
    // Stop never installs a runtime or starts a replacement daemon. In
    // particular, quitting a damaged installation must not start a download.
    if app.state::<Shell>().locald_writer.lock().unwrap().is_none() {
        let connection = connect_locald()?;
        install_locald_connection(&app, connection);
    }
    // Only put the splash up once the daemon has actually taken the stop.
    // Showing it first meant a refused operation left a "stopping Lemma"
    // screen in front of a stack that was never asked to stop.
    send_local_operation(
        &app,
        json!({"cmd": "stop", "infra": include_infra.unwrap_or(false)}),
        operation_id("shell-stop"),
    )?;
    show_splash_with_intent(&app, "stop");
    Ok(())
}

#[tauri::command]
/// Runs off the UI thread. A synchronous `#[tauri::command]` is dispatched on
/// the main thread, so any command that waits on the daemon, the network or a
/// child process freezes the window for its whole duration -- which is how a
/// first launch showed a black, unresponsive app for minutes while the runtime
/// installed and the daemon came up.
async fn restart(window: Webview, app: AppHandle) -> Result<(), String> {
    require_local_native_window(&window)?;
    tauri::async_runtime::spawn_blocking(move || restart_impl(app))
        .await
        .map_err(|error| error.to_string())?
}

fn restart_impl(app: AppHandle) -> Result<(), String> {
    if current_mode(&app) != "local" {
        return Err("local services are not active in Lemma Cloud mode".into());
    }
    ensure_locald(&app)?;
    send_local_operation(
        &app,
        json!({"cmd": "restart"}),
        operation_id("shell-restart"),
    )?;
    show_splash(&app);
    Ok(())
}

#[tauri::command]
/// Runs off the UI thread. A synchronous `#[tauri::command]` is dispatched on
/// the main thread, so any command that waits on the daemon, the network or a
/// child process freezes every window for its whole duration.
async fn open_app(app: AppHandle) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || open_app_impl(app))
        .await
        .map_err(|error| error.to_string())?
}

fn open_app_impl(app: AppHandle) -> Result<(), String> {
    let target = app_base_url(&app)?;
    open_app_window(&app, &target)
}

#[tauri::command(async)]
fn open_logs(window: Webview) -> Result<(), String> {
    require_local_native_window(&window)?;
    open_logs_impl()
}

fn open_logs_impl() -> Result<(), String> {
    reveal_path(&locald_root())
}

/// Hand a Lemma-owned path to the platform file handler.
fn reveal_path(path: &std::path::Path) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    let opener = "/usr/bin/open";
    #[cfg(target_os = "windows")]
    let opener = "explorer.exe";
    #[cfg(all(unix, not(target_os = "macos")))]
    let opener = "xdg-open";
    Command::new(opener)
        .arg(path)
        .spawn()
        .map_err(|e| format!("could not open {}: {e}", path.display()))?;
    Ok(())
}

#[tauri::command(async)]
fn installer_log(window: Webview) -> Result<String, String> {
    require_local_native_window(&window)?;
    let path = install_log_path();
    let raw = match std::fs::read_to_string(&path) {
        Ok(raw) => raw,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            return Ok("No local installer log entries yet.".into());
        }
        Err(error) => {
            return Err(format!(
                "could not read local installer log {}: {error}",
                path.display()
            ));
        }
    };
    let mut lines: Vec<&str> = raw.lines().rev().take(500).collect();
    lines.reverse();
    Ok(lines.join("\n"))
}

const MAX_DIAGNOSTIC_LOG_READ: u64 = 128 * 1024;

fn diagnostic_log_sources() -> Vec<(&'static str, &'static str, PathBuf)> {
    let root = locald_root();
    #[cfg(target_os = "macos")]
    let vm_log = root.join("logs/vz.log");
    #[cfg(windows)]
    let vm_log = root.join("logs/wsl.log");
    #[cfg(all(unix, not(target_os = "macos")))]
    let vm_log = root.join("logs/runtime.log");
    #[cfg(target_os = "macos")]
    let guest_log = root.join("runtime/macos/console.log");
    #[cfg(not(target_os = "macos"))]
    let guest_log = root.join("logs/guest.log");
    vec![
        ("events", "Events", root.join("events.jsonl")),
        ("migrations", "Migrations", root.join("logs/migrations.log")),
        ("backend", "Backend", root.join("logs/backend.log")),
        ("frontend", "Frontend", root.join("logs/frontend.log")),
        ("vm", "VM helper", vm_log),
        ("guest", "Guest services", guest_log),
        ("locald", "Service manager", root.join("locald.log")),
        // Separate from "locald" on purpose: this is what the daemon said on
        // its way out, which is the one thing locald.log cannot contain when
        // the failure was constructing the daemon in the first place.
        (
            "locald-stderr",
            "Service manager startup",
            locald_stderr_path(),
        ),
        (
            "agent-host",
            "Agent Host",
            root.parent()
                .unwrap_or(root.as_path())
                .join("agent-host/agent-host.log"),
        ),
        ("installer", "Installer", install_log_path()),
        ("launch", "Launch timing", launch_log_path()),
    ]
}

#[tauri::command(async)]
fn diagnostic_logs(
    window: Webview,
    source: Option<String>,
    cursor: Option<String>,
) -> Result<DiagnosticLogSnapshot, String> {
    require_local_native_window(&window)?;
    let sources = diagnostic_log_sources();
    let selected = source.as_deref().unwrap_or("events");
    let (_, _, path) = sources
        .iter()
        .find(|(id, _, _)| *id == selected)
        .ok_or_else(|| format!("unknown diagnostic log source: {selected}"))?;
    let public_sources = sources
        .iter()
        .map(|(id, label, _)| DiagnosticLogSource {
            id: (*id).into(),
            label: (*label).into(),
        })
        .collect();

    let mut file = match std::fs::File::open(path) {
        Ok(file) => file,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            return Ok(DiagnosticLogSnapshot {
                sources: public_sources,
                source: selected.into(),
                entries: format!("No {selected} log entries yet."),
                next_cursor: String::new(),
            });
        }
        Err(error) => {
            return Err(format!(
                "could not read diagnostic log {}: {error}",
                path.display()
            ));
        }
    };
    let metadata = file.metadata().map_err(|error| error.to_string())?;
    let length = metadata.len();
    let identity = diagnostic_file_identity(&file);
    let start = cursor
        .as_deref()
        .and_then(parse_diagnostic_cursor)
        .filter(|(cursor_identity, offset)| cursor_identity == &identity && *offset <= length)
        .map(|(_, offset)| offset)
        .unwrap_or_else(|| length.saturating_sub(MAX_DIAGNOSTIC_LOG_READ));
    file.seek(SeekFrom::Start(start))
        .map_err(|error| error.to_string())?;
    let mut bytes = Vec::new();
    file.take(MAX_DIAGNOSTIC_LOG_READ)
        .read_to_end(&mut bytes)
        .map_err(|error| error.to_string())?;
    let next_cursor = format!("v1:{identity}:{}", start.saturating_add(bytes.len() as u64));
    let mut entries = String::from_utf8_lossy(&bytes).into_owned();
    if start > 0 {
        if let Some(newline) = entries.find('\n') {
            entries.drain(..=newline);
        }
    }
    entries = redact_diagnostic_text(entries);
    Ok(DiagnosticLogSnapshot {
        sources: public_sources,
        source: selected.into(),
        entries,
        next_cursor,
    })
}

fn parse_diagnostic_cursor(cursor: &str) -> Option<(String, u64)> {
    let value = cursor.strip_prefix("v1:")?;
    let (identity, offset) = value.rsplit_once(':')?;
    Some((identity.to_owned(), offset.parse().ok()?))
}

#[cfg(unix)]
fn diagnostic_file_identity(file: &std::fs::File) -> String {
    use std::os::unix::fs::MetadataExt;
    match file.metadata() {
        Ok(metadata) => format!("{:x}-{:x}", metadata.dev(), metadata.ino()),
        Err(_) => String::new(),
    }
}

#[cfg(windows)]
fn diagnostic_file_identity(file: &std::fs::File) -> String {
    // (volume serial, file index) is the Windows spelling of (device, inode).
    // `Metadata` only exposes it behind the unstable `windows_by_handle`
    // feature, but the same fields are on the stable Win32 call, and we are
    // holding the open handle it wants.
    //
    // Anything derived from the file's *contents* -- size, last write -- is
    // wrong here even though it compiles: an identity that changes whenever
    // the log is appended to invalidates the cursor on every poll, which
    // re-sends the whole tail exactly while the user is watching a live log.
    use std::os::windows::io::AsRawHandle;
    use windows_sys::Win32::Storage::FileSystem::{
        GetFileInformationByHandle, BY_HANDLE_FILE_INFORMATION,
    };

    let mut information = unsafe { std::mem::zeroed::<BY_HANDLE_FILE_INFORMATION>() };
    // SAFETY: the handle is live for as long as `file` is borrowed, and
    // `information` is a correctly sized, writable destination.
    if unsafe { GetFileInformationByHandle(file.as_raw_handle() as _, &mut information) } == 0 {
        // An empty identity never matches a cursor, so the caller falls back
        // to re-reading the tail -- the same behaviour as a rotated file.
        return String::new();
    }
    let index =
        (u64::from(information.nFileIndexHigh) << 32) | u64::from(information.nFileIndexLow);
    format!("{:x}-{:x}", information.dwVolumeSerialNumber, index)
}

#[cfg(not(any(unix, windows)))]
fn diagnostic_file_identity(_file: &std::fs::File) -> String {
    // No portable file identity, so every poll re-reads the tail.
    String::new()
}

fn redact_diagnostic_text(mut text: String) -> String {
    let root = locald_root();
    let mut secrets = Vec::new();
    for path in [
        root.join("control.token"),
        root.join("host.secrets.json"),
        root.join("infra.secrets.json"),
        root.join("operator-config.json"),
    ] {
        collect_secret_file_values(&path, &mut secrets);
    }
    secrets.sort_by_key(|value| std::cmp::Reverse(value.len()));
    secrets.dedup();
    for secret in secrets {
        text = text.replace(&secret, "[redacted]");
    }
    mask_secret_shapes(text)
}

/// Mask credentials by what they look like, not by having seen them before.
///
/// Substitution alone cannot cover the ones that matter. All 19 operator
/// secrets -- the AI provider key, Slack and Telegram tokens, OAuth client
/// secrets, the encryption keyset -- live in the OS credential vault, and
/// `operator-config.json` holds none of them. So the values were unredactable
/// by construction, while the README told the user this view returned bounded,
/// redacted data.
///
/// Reading them back out of the vault to redact them would put every secret
/// this installation owns into a diagnostics buffer and possibly raise a
/// system authorisation prompt, so this recognises their shapes instead.
/// Deliberately conservative: a missed token is worse than a masked path, but a
/// log so heavily masked that nobody can read it is not a diagnostic.
fn mask_secret_shapes(text: String) -> String {
    text.lines()
        .map(|line| {
            let lowered = line.to_ascii_lowercase();
            // An Authorization header carries a credential in full, whatever
            // scheme it names.
            if let Some(index) = lowered.find("authorization:") {
                let (head, _) = line.split_at(index + "authorization:".len());
                return format!("{head} [redacted]");
            }
            // Rebuilt around the words rather than from them.
            //
            // This used to be `split_whitespace().join(" ")`, which redacts
            // correctly and flattens the line on the way past: every indent,
            // every tab, every aligned column gone. Diagnostics is where
            // somebody reads a Python traceback, and a traceback with no
            // indentation is a wall of text. The guard test could not see it --
            // its fixtures were all single-spaced.
            let mut masked = String::with_capacity(line.len());
            let mut rest = line;
            while !rest.is_empty() {
                let gap = rest
                    .find(|c: char| !c.is_whitespace())
                    .unwrap_or(rest.len());
                masked.push_str(&rest[..gap]);
                rest = &rest[gap..];
                if rest.is_empty() {
                    break;
                }
                let end = rest.find(char::is_whitespace).unwrap_or(rest.len());
                let word = &rest[..end];
                let trimmed = word.trim_matches(|c: char| {
                    c == '"' || c == '\'' || c == ',' || c == ';' || c == ')'
                });
                if looks_like_a_credential(trimmed) {
                    masked.push_str(&word.replace(trimmed, "[redacted]"));
                } else {
                    masked.push_str(word);
                }
                rest = &rest[end..];
            }
            masked
        })
        .collect::<Vec<_>>()
        .join("\n")
}

/// Whether one whitespace-delimited word is a credential rather than prose.
///
/// Prefix-anchored on the vendor forms actually stored here, plus JWTs. Length
/// alone is not enough -- a file path or a container digest would match, and
/// masking those makes a log useless for the thing it is being read for.
fn looks_like_a_credential(word: &str) -> bool {
    const VENDOR_PREFIXES: [&str; 7] = ["sk-", "xoxb-", "xoxp-", "xapp-", "re_", "ghp_", "ghs_"];
    if VENDOR_PREFIXES
        .iter()
        .any(|prefix| word.len() > prefix.len() + 12 && word.starts_with(prefix))
    {
        return true;
    }
    // A JWT: three dot-separated base64url segments, the first of which decodes
    // to a JSON header. Checking the shape rather than the length keeps
    // version strings and digests out of it.
    let segments: Vec<&str> = word.split('.').collect();
    segments.len() == 3
        && segments[0].len() >= 8
        && segments.iter().all(|segment| {
            !segment.is_empty()
                && segment
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-' || byte == b'_')
        })
        && segments[0].starts_with("eyJ")
}

fn collect_secret_file_values(path: &Path, output: &mut Vec<String>) {
    let Ok(raw) = std::fs::read_to_string(path) else {
        return;
    };
    if path.extension().and_then(|value| value.to_str()) != Some("json") {
        let value = raw.trim();
        if value.len() >= 8 {
            output.push(value.into());
        }
        return;
    }
    let Ok(value) = serde_json::from_str::<Value>(&raw) else {
        return;
    };
    collect_secret_json_values(&value, false, output);
}

fn collect_secret_json_values(value: &Value, sensitive: bool, output: &mut Vec<String>) {
    match value {
        Value::Object(values) => {
            for (key, value) in values {
                let key = key.to_ascii_lowercase();
                let child_sensitive = sensitive
                    || ["password", "secret", "token", "api_key", "apikey"]
                        .iter()
                        .any(|marker| key.contains(marker));
                collect_secret_json_values(value, child_sensitive, output);
            }
        }
        Value::Array(values) => {
            for value in values {
                collect_secret_json_values(value, sensitive, output);
            }
        }
        Value::String(value) if sensitive && value.len() >= 8 => output.push(value.clone()),
        _ => {}
    }
}

/// Open Local settings, and tell the caller whether it opened.
///
/// Awaited rather than fire-and-forget. This used to spawn a thread, return
/// `Ok` at once, and report failure by emitting `lemma:control-error` -- which
/// nothing in the app listens for. The splash's recovery button has a `.catch`
/// that therefore could never run, so a Local settings window that failed to
/// open left the user pressing a button that did nothing at all.
///
/// Async, so it is dispatched off the main thread and can wait for the answer;
/// that is also what keeps it clear of the Windows child-webview deadlock,
/// which is a hazard for *synchronous* commands.
#[tauri::command]
async fn open_control_center(app: AppHandle, page: Option<String>) -> Result<(), String> {
    let page = control_center_page(page.as_deref())?;
    tauri::async_runtime::spawn_blocking(move || open_control_center_blocking(&app, &page))
        .await
        .map_err(|error| error.to_string())?
}

fn is_control_window_label(label: &str) -> bool {
    label == "control"
}

fn trusted_control_url(url: &tauri::Url) -> bool {
    trusted_native_asset_url(url) && url.path() == "/control.html"
}

fn trusted_native_asset_url(url: &tauri::Url) -> bool {
    native_assets::is_trusted(url, cfg!(debug_assertions).then_some(DEV_ASSET_PORT))
}

fn require_control_window(window: &Webview) -> Result<(), String> {
    if !is_control_window_label(window.label()) {
        return Err(
            "this operation is available only in the privileged Local settings view".into(),
        );
    }
    let url = window
        .url()
        .map_err(|error| format!("could not inspect Local settings: {error}"))?;
    if !trusted_control_url(&url) {
        return Err("remote pages cannot use Local settings privileges".into());
    }
    Ok(())
}

fn require_local_native_window(window: &Webview) -> Result<(), String> {
    if !matches!(window.label(), "main" | "control") {
        return Err("this operation is available only in a Lemma native window".into());
    }
    let url = window
        .url()
        .map_err(|error| format!("could not inspect native window: {error}"))?;
    if !trusted_native_asset_url(&url)
        || (window.label() == "control" && !trusted_control_url(&url))
    {
        return Err("remote workspace pages cannot prepare the local runtime".into());
    }
    Ok(())
}

#[tauri::command]
/// Runs off the UI thread. A synchronous `#[tauri::command]` is dispatched on
/// the main thread, so any command that waits on the daemon, the network or a
/// child process freezes the window for its whole duration -- which is how a
/// first launch showed a black, unresponsive app for minutes while the runtime
/// installed and the daemon came up.
async fn prepare_runtime(window: Webview, app: AppHandle) -> Result<(), String> {
    require_local_native_window(&window)?;
    tauri::async_runtime::spawn_blocking(move || prepare_runtime_impl(app))
        .await
        .map_err(|error| error.to_string())?
}

fn prepare_runtime_impl(app: AppHandle) -> Result<(), String> {
    if current_mode(&app) != "local" {
        return Err("choose the local workspace before preparing its runtime".into());
    }
    ensure_locald(&app)?;
    send_to_locald(
        &app,
        json!({"cmd":"runtime.prepare", "id":"shell-runtime-prepare"}),
    )
}

#[tauri::command(async)]
fn runtime_info(window: Webview) -> Result<RuntimeInfo, String> {
    require_control_window(&window)?;
    Ok(runtime_info_snapshot())
}

#[tauri::command]
/// Runs off the UI thread. A synchronous `#[tauri::command]` is dispatched on
/// the main thread, so any command that waits on the daemon, the network or a
/// child process freezes the window for its whole duration -- which is how a
/// first launch showed a black, unresponsive app for minutes while the runtime
/// installed and the daemon came up.
async fn repair_runtime(window: Webview, app: AppHandle) -> Result<(), String> {
    require_control_window(&window)?;
    tauri::async_runtime::spawn_blocking(move || repair_runtime_impl(app))
        .await
        .map_err(|error| error.to_string())?
}

/// What the app knows about a newer version, if anything.
#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct AppUpdateStatus {
    channel: &'static str,
    current_version: &'static str,
    build_commit: Option<&'static str>,
    /// False for a nightly or a development build. The UI explains why rather
    /// than silently omitting the control.
    updates_supported: bool,
    available_version: Option<String>,
    /// Bytes of runtime the *next* launch downloads after an app update, read
    /// from the feed rather than guessed. An app update is ~24 MB; the runtime
    /// that follows is two orders of magnitude larger, and saying so before the
    /// user commits is the difference between a considered choice and a
    /// surprise.
    runtime_download_bytes: Option<u64>,
    /// Known database compatibility; this alone is not upgrade qualification.
    data_compatibility: &'static str,
}

/// Ask the release feed whether there is a newer Lemma.
///
/// Runs in Rust so the webview's CSP stays exactly as it is. A JavaScript check
/// would need `github.com` and `objects.githubusercontent.com` in
/// `connect-src`, widening the network policy of the same webview that hosts
/// the remote workspace origin.
#[tauri::command]
async fn check_for_app_update(window: Webview, app: AppHandle) -> Result<AppUpdateStatus, String> {
    require_control_window(&window)?;
    let mut status = AppUpdateStatus {
        channel: release_channel(),
        current_version: env!("CARGO_PKG_VERSION"),
        build_commit: build_commit(),
        updates_supported: updates_enabled(),
        available_version: None,
        runtime_download_bytes: None,
        data_compatibility: "unknown",
    };
    if !updates_enabled() {
        return Ok(status);
    }
    let update = app
        .updater_builder()
        .endpoints(parsed_updater_endpoints())
        .map_err(|error| format!("could not check for updates: {error}"))?
        .build()
        .map_err(|error| format!("could not check for updates: {error}"))?
        .check()
        .await
        .map_err(|error| format!("could not check for updates: {error}"))?;
    let Some(update) = update else {
        status.data_compatibility = "compatible";
        return Ok(status);
    };
    status.available_version = Some(update.version.clone());
    // The feed's own `lemma` block. The updater ignores unknown top-level keys
    // and hands back the parsed document, so this costs no extra request.
    let metadata = lemma_update_metadata(&update.raw_json);
    status.runtime_download_bytes = metadata.runtime_download_bytes;
    status.data_compatibility = if !has_local_runtime_data() {
        "compatible"
    } else if cfg!(windows) {
        "migration-unavailable"
    } else {
        metadata.compatibility_with(installed_postgres_major())
    };
    Ok(status)
}

/// The `lemma` block a release feed carries alongside the standard fields.
#[derive(Default)]
struct LemmaUpdateMetadata {
    postgres_major: Option<u64>,
    runtime_download_bytes: Option<u64>,
}

impl LemmaUpdateMetadata {
    /// Unknown compatibility blocks replacement when local runtime data exists.
    fn compatibility_with(&self, installed: Option<u64>) -> &'static str {
        match (installed, self.postgres_major) {
            (Some(installed), Some(candidate)) if installed == candidate => "compatible",
            (Some(_), Some(_)) => "migration-unavailable",
            _ => "unknown",
        }
    }
}

fn lemma_update_metadata(raw: &Value) -> LemmaUpdateMetadata {
    lemma_update_metadata_for(
        raw,
        if cfg!(windows) {
            "windows-x86_64"
        } else {
            "darwin-aarch64"
        },
    )
}

fn lemma_update_metadata_for(raw: &Value, target: &str) -> LemmaUpdateMetadata {
    let Some(block) = raw.get("lemma") else {
        return LemmaUpdateMetadata::default();
    };
    let block = if let Some(platforms) = block.get("platforms") {
        let Some(platform) = platforms.get(target) else {
            return LemmaUpdateMetadata::default();
        };
        platform
    } else {
        block
    };
    LemmaUpdateMetadata {
        postgres_major: block.get("postgres_major").and_then(Value::as_u64),
        runtime_download_bytes: block.get("runtime_download_bytes").and_then(Value::as_u64),
    }
}

/// The Postgres major this installation's data was created with, if recorded.
fn installed_postgres_major() -> Option<u64> {
    read_config()
        .pointer("/installedRuntime/dataCompatibility/postgres_major")
        .and_then(Value::as_u64)
}

/// Where each platform keeps the disk holding this installation's databases.
///
/// macOS has a sparse `data.raw`; Windows has the WSL distribution's
/// `ext4.vhdx` under `runtime/wsl`. The Windows path was written as
/// `runtime/windows`, which nothing creates -- so on Windows this answered "no
/// data" for a real installation, and only the config check kept the update
/// guard honest.
fn managed_data_disk() -> std::path::PathBuf {
    if cfg!(windows) {
        locald_root().join("runtime/wsl/ext4.vhdx")
    } else {
        locald_root().join("runtime/macos/data.raw")
    }
}

fn has_local_runtime_data() -> bool {
    configured_runtime(&read_config(), "installedRuntime").is_some() || managed_data_disk().exists()
}

fn ensure_update_preserves_data(
    reset_requested: bool,
    has_runtime: bool,
    compatibility: &str,
    windows: bool,
) -> Result<(), String> {
    if reset_requested {
        return Err("Updates never reset local data. Factory reset is a separate destructive action in recovery.".into());
    }
    if has_runtime && (windows || compatibility != "compatible") {
        return Err("This update has no supported data-preserving migration for this installation. Your current version and data have been kept. Wait for a compatible update.".into());
    }
    Ok(())
}

/// Download and install a newer Lemma, then offer to restart.
#[tauri::command]
async fn install_app_update(
    window: Webview,
    app: AppHandle,
    reset_data: bool,
) -> Result<(), String> {
    require_control_window(&window)?;
    if !updates_enabled() {
        return Err(
            "this build does not update itself; download the current release instead".into(),
        );
    }
    let update = app
        .updater_builder()
        .endpoints(parsed_updater_endpoints())
        .map_err(|error| format!("could not check for updates: {error}"))?
        .build()
        .map_err(|error| format!("could not check for updates: {error}"))?
        .check()
        .await
        .map_err(|error| format!("could not check for updates: {error}"))?
        .ok_or("Lemma is already up to date")?;

    ensure_update_preserves_data(
        reset_data,
        has_local_runtime_data(),
        lemma_update_metadata(&update.raw_json).compatibility_with(installed_postgres_major()),
        cfg!(windows),
    )?;

    // Downloaded first, and deliberately not with `download_and_install`.
    //
    // `download` is where the signature is verified, and it is the step most
    // likely to fail: a network that drops, a feed that moved, a key that
    // cannot decode. Stopping the daemon before it meant every one of those
    // outcomes took the user's whole stack down and then reported an error --
    // for an update that never began.
    let bytes = update
        .download(|_, _| {}, || {})
        .await
        .map_err(|error| format!("could not download the update: {error}"))?;

    // Now, and only now. A DMG install moves the old app to the Trash, so the
    // running daemon's executable path changes and `locald_is_this_build`
    // notices. An in-place update writes to the *same* path, so a stale daemon
    // from the previous version would report an identical path and be adopted
    // by the new app -- supervising the old runtime under a new shell.
    let handle = app.clone();
    tauri::async_runtime::spawn_blocking(move || stop_locald_for_runtime_maintenance(&handle))
        .await
        .map_err(|error| error.to_string())??;

    // Written before `install`, because on Windows `install` launches the
    // NSIS installer and exits this process: there is no line after it in
    // which to record anything. Without it, an installer the user cancelled,
    // or one interrupted by a reboot, left the app running the old version
    // with nothing anywhere saying an update had been attempted at all.
    if cfg!(windows) {
        record_update_attempt(&app, &update.version.to_string());
    }

    if let Err(error) = update.install(bytes) {
        // The stack is down and the update did not happen. Leaving it there
        // stranded the user in Local settings over a workspace whose backend
        // had gone, with nothing offering to bring it back: the reader thread
        // only re-shows the splash when the settings window is absent, and
        // this command requires it to be open. Put the previous version --
        // still the installed one -- back into service before reporting.
        // The attempt is over and it is being reported here, so the record
        // has nothing left to explain on the next launch.
        clear_update_attempt();
        let handle = app.clone();
        let restarted = tauri::async_runtime::spawn_blocking(move || {
            start_after_runtime_maintenance(&handle, "shell-update-recover")
        })
        .await
        .map_err(|join| join.to_string())?;
        return Err(failed_install_message(&error.to_string(), restarted.err()));
    }

    // The Windows updater exits this process to run the installer. Completion
    // belongs to the next launch, not a dialog after installation.
    if cfg!(windows) {
        return Ok(());
    }

    let restart = confirm_destructive_action_impl(
        app.clone(),
        "Restart to finish updating?".into(),
        format!(
            "Lemma {} is installed. Restarting now finishes the update; it downloads \
             its runtime once afterwards.",
            update.version
        ),
        "Restart Now".into(),
    )?;
    if restart {
        app.restart();
    }
    Ok(())
}

/// Where an in-flight update records what it was aiming at.
///
/// In locald's root rather than beside the app: on Windows the installer
/// replaces the whole application directory, so anything written there is gone
/// exactly when it is needed.
fn update_attempt_path() -> PathBuf {
    locald_root().join("shell-update.json")
}

fn record_update_attempt(app: &AppHandle, to: &str) {
    let from = app.package_info().version.to_string();
    let to = to.to_owned();
    if let Err(error) = config_store::update(&update_attempt_path(), |record| {
        *record = json!({"schema_version": 1, "from": from, "to": to});
    }) {
        // Not fatal: failing to record an update is no reason to refuse one.
        append_bounded_log(
            &launch_log_path(),
            &format!("could not record the update attempt: {error}"),
        );
    }
}

fn clear_update_attempt() {
    let _ = std::fs::remove_file(update_attempt_path());
}

/// What last launch's update attempt turned out to be.
#[derive(Debug, PartialEq, Eq)]
enum UpdateAttempt {
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

/// Read an update record against the version actually running.
///
/// Pure, and separate from the file handling, because the interesting part is
/// the three-way comparison and it is the part worth testing.
fn classify_update_attempt(record: Option<&Value>, running: &str) -> UpdateAttempt {
    let Some(record) = record else {
        return UpdateAttempt::None;
    };
    let from = record.get("from").and_then(Value::as_str);
    let to = record.get("to").and_then(Value::as_str);
    let (Some(from), Some(to)) = (from, to) else {
        return UpdateAttempt::Unexplained;
    };
    if to == running {
        UpdateAttempt::Landed { to: to.to_owned() }
    } else if from == running {
        UpdateAttempt::DidNotLand { to: to.to_owned() }
    } else {
        UpdateAttempt::Unexplained
    }
}

/// Settle whatever the last launch's update attempt left behind.
///
/// The record is cleared either way. Its only job is to let this launch say
/// what happened, and a record kept past that would explain the wrong launch.
fn reconcile_update_attempt(app: &AppHandle) {
    let record = std::fs::read(update_attempt_path())
        .ok()
        .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok());
    let running = app.package_info().version.to_string();
    let outcome = classify_update_attempt(record.as_ref(), &running);
    if outcome != UpdateAttempt::None {
        clear_update_attempt();
    }
    match outcome {
        UpdateAttempt::None => (),
        UpdateAttempt::Landed { to } => {
            append_bounded_log(&launch_log_path(), &format!("update to {to} completed"));
        }
        UpdateAttempt::Unexplained => {
            append_bounded_log(
                &launch_log_path(),
                &format!("an update record did not describe this version ({running}); discarded"),
            );
        }
        UpdateAttempt::DidNotLand { to } => {
            let message = format!(
                "Lemma {to} was downloaded but its installer did not finish, so this is \
                 still {running}. Nothing was changed. Check for updates again when you \
                 are ready."
            );
            append_bounded_log(
                &launch_log_path(),
                &format!("update to {to} did not finish"),
            );
            announce_incomplete_update(app, message);
        }
    }
}

/// Tell the user their update did not happen, once there is a window to tell.
///
/// On its own thread with a deadline: `setup` runs before the window is built,
/// and `report_action_failure` needs one. The launch log has the record either
/// way, so a window that never appears costs the message and not the evidence.
fn announce_incomplete_update(app: &AppHandle, message: String) {
    let handle = app.clone();
    std::thread::spawn(move || {
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(30);
        while handle.get_window("main").is_none() && std::time::Instant::now() < deadline {
            std::thread::sleep(std::time::Duration::from_millis(100));
        }
        report_action_failure(&handle, "Update", &message);
    });
}

/// What this Mac still has that a reset could remove.
///
/// Read by the splash so it can offer the right tier -- and only offer one at
/// all when there is something to reset. Reported in allocated bytes rather
/// than the disk's apparent size: `data.raw` is sparse and always claims 24
/// GiB, so `len()` would tell every user they were about to recover 24 GiB
/// regardless of what was on it.
#[tauri::command(async)]
fn local_recovery_options(window: Webview) -> Result<RecoveryOptions, String> {
    require_local_native_window(&window)?;
    let config = read_config();
    let installed = configured_runtime(&config, "installedRuntime");
    let data_disk = managed_data_disk();
    Ok(RecoveryOptions {
        // Tier 1 needs a daemon to drive it; Tier 2 exists precisely for when
        // there is not one, so it is offered whenever any state survives.
        data_reset_available: locald_root().exists(),
        full_reinstall_available: locald_root().exists() || installed.is_some(),
        installed_runtime_release: installed.map(|runtime| runtime.release),
        data_disk_allocated_bytes: allocated_bytes(&data_disk),
    })
}

#[cfg(unix)]
fn allocated_bytes(path: &std::path::Path) -> u64 {
    use std::os::unix::fs::MetadataExt;
    path.metadata().map(|meta| meta.blocks() * 512).unwrap_or(0)
}

#[cfg(not(unix))]
fn allocated_bytes(path: &std::path::Path) -> u64 {
    path.metadata().map(|meta| meta.len()).unwrap_or(0)
}

/// Destroy everything on this Mac that the user made, then start clean.
#[tauri::command]
async fn reset_local_data(window: Webview, app: AppHandle) -> Result<RecoveryOutcome, String> {
    require_local_native_window(&window)?;
    tauri::async_runtime::spawn_blocking(move || reset_local_data_impl(app))
        .await
        .map_err(|error| error.to_string())?
}

fn reset_local_data_impl(app: AppHandle) -> Result<RecoveryOutcome, String> {
    if !confirm_destructive_action_impl(
        app.clone(),
        "Reset local data?".into(),
        format!(
            "Every pod, table, file, workspace and account on {THIS_COMPUTER} is \
             deleted. Your AI provider settings and the downloaded runtime are \
             kept, so Lemma starts again in seconds.\n\nThis cannot be undone."
        ),
        "Reset Data".into(),
    )? {
        return Ok(RecoveryOutcome::Cancelled);
    }

    // Before anything is destroyed, and first, because the completion handler
    // is fire-and-forget and this gets the whole reset to finish in.
    //
    // A SuperTokens cookie minted against the database we are about to delete
    // is presented to the new one and accepted as a session that cannot do
    // anything -- an app permanently signed in and permanently broken, where
    // even signing out is an authorized call.
    clear_local_session_data(&app);
    // Not fatal: a stale resume target costs one splash-less launch that falls
    // back to the splash anyway, and refusing the reset over it would be worse.
    if let Err(error) = write_config(|config| {
        if let Some(object) = config.as_object_mut() {
            // Names a generation and an account that will not exist.
            object.remove("resumeTarget");
        }
    }) {
        append_install_log(&format!(
            "reset: could not clear the resume target: {error}"
        ));
    }

    ensure_locald(&app)?;
    send_local_operation(
        &app,
        json!({"cmd": "local.reset-data", "confirm": "reset-local-data"}),
        operation_id("reset-data"),
    )?;
    Ok(RecoveryOutcome::Started)
}

/// Return this Mac to the state of one that has never run Lemma.
#[tauri::command]
async fn restart_into_recovery(window: Webview, app: AppHandle) -> Result<(), String> {
    require_control_window(&window)?;
    std::fs::create_dir_all(app_support_dir()).map_err(|error| error.to_string())?;
    std::fs::write(app_support_dir().join("recovery-mode"), b"recovery\n")
        .map_err(|error| format!("could not request recovery mode: {error}"))?;
    app.restart();
}

/// Erase this installation only after an explicit native confirmation.
#[tauri::command]
async fn reset_full_reinstall(window: Webview, app: AppHandle) -> Result<RecoveryOutcome, String> {
    require_local_native_window(&window)?;
    tauri::async_runtime::spawn_blocking(move || reset_full_reinstall_impl(app))
        .await
        .map_err(|error| error.to_string())?
}

fn reset_full_reinstall_impl(app: AppHandle) -> Result<RecoveryOutcome, String> {
    if !confirm_destructive_action_impl(
        app.clone(),
        "Permanently erase local Lemma and reinstall?".into(),
        format!(
            "This permanently deletes local pods, databases, files, accounts, schedules, \
             AI provider settings and stored keys, downloaded runtime files, and this \
             installation's Agent Host pairings and managed working folders on {THIS_COMPUTER}.\n\n\
             Active local work stops. External project folders and Lemma Cloud workspace \
             data are kept. The Lemma app and diagnostic logs are kept. Choosing Local Lemma \
             afterwards downloads its verified services again and requires an internet connection.\n\n\
             There is no automatic backup. Export anything you need before continuing. \
             This cannot be undone."
        ),
        "Erase Local Lemma".into(),
    )? {
        return Ok(RecoveryOutcome::Cancelled);
    }

    let shell: State<Shell> = app.state();
    let _recovery = recovery::RecoveryGuard::enter(&shell.recovery_running)
        .map_err(|error| error.to_string())?;
    let _installation = shell.runtime_install.try_lock()
        .map_err(|_| "An installation is still running. Close and reopen Lemma, then use Recovery before starting setup.".to_string())?;
    let _connection = shell.locald_connect.try_lock()
        .map_err(|_| "The background service is still starting. Wait for startup to finish or close and reopen Lemma, then retry Recovery.".to_string())?;
    clear_local_session_data(&app);
    // The daemon has to be gone before its own state directory is removed, and
    // this tolerates there being no daemon at all -- which is the state this
    // tier exists for.
    stop_locald_for_runtime_maintenance(&app)?;

    let summary = run_locald_reset()?;
    append_install_log(&format!("full reinstall: {summary}"));

    let root = locald_root();
    let agent_host = root
        .parent()
        .ok_or("the local installation has no parent directory")?
        .join("agent-host");
    recovery::clear_reinstall_files(&app_support_dir(), &agent_host)
        .map_err(|error| format!("Cleanup is incomplete: {error}. Some local data has already been erased. Retry force cleanup to finish."))?;

    let snapshot = {
        let shell: State<Shell> = app.state();
        let mut ui = shell.ui.lock().unwrap();
        ui.mode = "undecided".into();
        ui.running = false;
        ui.ready = false;
        ui.error = false;
        ui.status = String::new();
        ui.error_code = String::new();
        ui.clone()
    };
    let _ = app.emit("lemma:state", snapshot);
    refresh_menus_for_connection_mode(&app);
    shell.recovery_mode.store(false, Ordering::Release);
    if let Some(control) = app.get_webview("control") {
        let _ = control.close();
    }
    show_splash(&app);
    Ok(RecoveryOutcome::Completed)
}

/// Run `lemma-locald reset` and return its JSON summary.
///
/// The wipe runs inside the daemon binary rather than here because the OS
/// credential vault keys each stored item's access control to the code identity
/// that created it -- `work.lemma.locald`. A delete issued from this process is
/// a different program as far as the vault is concerned, and would prompt or
/// silently fail.
fn run_locald_reset() -> Result<String, String> {
    let executable = bundled_sibling("lemma-locald")
        .ok_or("the bundled lemma-locald is missing, so local state cannot be reset")?;
    let mut command = Command::new(executable);
    command
        .args(["reset", "--confirm=erase-local-lemma"])
        .env("LEMMA_LOCALD_ROOT", locald_root())
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .no_console_window();
    let output = lemma_desktop_process::run(command, Duration::from_secs(120), 1024 * 1024)
        .map_err(|error| format!("could not run the local reset: {error}"))?;
    if !output.status.success() {
        let detail = String::from_utf8_lossy(&output.stderr);
        let detail = detail.lines().last().unwrap_or("no reason given");
        return Err(format!("the local reset did not finish: {detail}"));
    }
    Ok(String::from_utf8_lossy(&output.stdout).trim().to_owned())
}

/// Forget the cookies and storage of the workspace being destroyed.
///
/// Best effort and fire-and-forget: the reset is worth doing even if the
/// webview will not answer, and the alternative to trying is a user who is
/// signed in to a database that no longer exists.
fn clear_local_session_data(app: &AppHandle) {
    if let Some(window) = app.get_webview("main") {
        let _ = window.clear_all_browsing_data();
    }
}

fn repair_runtime_impl(app: AppHandle) -> Result<(), String> {
    if current_mode(&app) != "local" {
        return Err("runtime repair is available only for a local workspace".into());
    }
    let shell: State<Shell> = app.state();
    let _install_guard = shell.runtime_install.lock().unwrap();
    require_no_recovery(&shell)?;
    let config = read_config();
    if config
        .pointer("/installedRuntime/release")
        .and_then(Value::as_str)
        != Some(env!("CARGO_PKG_VERSION"))
    {
        return Err(
            "this retained runtime cannot be repaired with the current signed manifest".into(),
        );
    }
    emit_runtime_install_progress(
        &app,
        "repair",
        "runtime",
        "Preparing a verified replacement runtime",
        1,
        None,
        None,
        None,
        None,
    );
    prepare_runtime_artifacts(&app, true)?;
    drop(_install_guard);
    start_after_runtime_maintenance(&app, "shell-start-after-runtime-repair")
}

#[tauri::command]
/// Runs off the UI thread. A synchronous `#[tauri::command]` is dispatched on
/// the main thread, so any command that waits on the daemon, the network or a
/// child process freezes every window for its whole duration.
async fn control_snapshot(window: Webview, app: AppHandle, id: String) -> Result<(), String> {
    require_control_window(&window)?;
    tauri::async_runtime::spawn_blocking(move || control_snapshot_impl(app, id))
        .await
        .map_err(|error| error.to_string())?
}

fn control_snapshot_impl(app: AppHandle, id: String) -> Result<(), String> {
    // Opening settings is not consent to download or repair a local runtime.
    ensure_locald_without_host_pack(&app)?;
    send_to_locald(&app, json!({"cmd":"control.snapshot", "id": id}))
}

#[tauri::command]
/// Runs off the UI thread. A synchronous `#[tauri::command]` is dispatched on
/// the main thread, so any command that waits on the daemon, the network or a
/// child process freezes every window for its whole duration.
async fn agent_host_action(window: Webview, app: AppHandle, action: String) -> Result<(), String> {
    require_control_window(&window)?;
    tauri::async_runtime::spawn_blocking(move || agent_host_action_impl(app, action))
        .await
        .map_err(|error| error.to_string())?
}

fn agent_host_action_impl(app: AppHandle, action: String) -> Result<(), String> {
    if !matches!(action.as_str(), "start" | "stop" | "restart") {
        return Err(format!("unknown Agent Host action {action:?}"));
    }
    ensure_agent_host_daemon(&app)?;
    agent_host_request(
        &app,
        json!({
            "cmd": format!("agent-host.{action}"),
            "id": operation_id("agent-host"),
        }),
    )
}

/// Who may drive this computer's Agent Host.
///
/// Local settings qualifies as a trusted bundled page. So does the signed-in
/// workspace, which is the whole point - the Agent Host page lives there so a
/// cloud user gets it too - but only while it is on the origin this app
/// actually navigated to. Sharing republishes that same workspace on a LAN or
/// tunnel host, and a visitor loading it must not reach this Mac. The ACL in
/// capabilities/workspace.json is the primary gate; this is the second one, in
/// case a URL pattern is ever written too loosely.
fn require_agent_host_caller(window: &Webview, app: &AppHandle) -> Result<(), String> {
    if is_control_window_label(window.label()) {
        return require_control_window(window);
    }
    if window.label() != "main" {
        return Err("the Agent Host is controlled from Lemma, not from this window".into());
    }
    let url = window
        .url()
        .map_err(|error| format!("could not inspect the workspace: {error}"))?;
    if trusted_native_asset_url(&url) {
        return Ok(());
    }
    let expected = app_base_url(app)?;
    let expected =
        tauri::Url::parse(&expected).map_err(|_| "no workspace origin yet".to_string())?;
    if url.origin() != expected.origin() {
        return Err("only the signed-in Lemma workspace can control the Agent Host".into());
    }
    Ok(())
}

/// Connect to locald, starting it if needed, for Agent Host work only.
///
/// A cloud user has no local stack, so the shell never brings locald up for
/// them - and without it nothing supervises the Agent Host, which is exactly
/// the feature they want on their own machine. locald with no host pack does
/// nothing but hold the socket and supervise the sidecar, so it is the right
/// process for both modes; only the local one needs the runtime artifacts.
fn ensure_agent_host_daemon(app: &AppHandle) -> Result<(), String> {
    if current_mode(app) == "local" {
        ensure_locald(app)
    } else {
        ensure_locald_without_host_pack(app)
    }
}

#[tauri::command]
/// What the workspace should say about the sandbox image download.
///
/// Read straight out of the shell's own state, which locald has already
/// pushed to: no daemon round trip, so the workspace can poll it while the
/// download is running without paying for a socket each time.
fn sandbox_image_status(_window: Webview, app: AppHandle) -> Value {
    let shell: State<Shell> = app.state();
    let ui = shell.ui.lock().unwrap();
    json!({
        // `pending`, not the empty default, when locald has not said anything
        // yet. The workspace stops asking once the answer can no longer change,
        // and it reads a state it does not recognise as one of those -- so an
        // empty string here meant a page that opened before the first report
        // never saw the download at all.
        "state": if ui.sandbox_images.is_empty() {
            "pending"
        } else {
            ui.sandbox_images.as_str()
        },
        "detail": ui.sandbox_images_detail,
    })
}

#[tauri::command]
/// Runs off the UI thread. A synchronous `#[tauri::command]` is dispatched on
/// the main thread, so any command that waits on the daemon, the network or a
/// child process freezes every window for its whole duration.
async fn agent_host_status(_window: Webview, app: AppHandle) -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || agent_host_status_impl(app))
        .await
        .map_err(|error| error.to_string())?
}

fn agent_host_status_impl(app: AppHandle) -> Result<Value, String> {
    ensure_agent_host_daemon(&app)?;
    let response = locald_request(
        json!({"cmd": "agent-host.status", "id": operation_id("agent-host-status")}),
        Duration::from_secs(15),
    )?;
    let status = response
        .get("agent_host")
        .filter(|value| value.is_object())
        .ok_or("Lemma returned an invalid Agent Host status")?
        .clone();
    let shell: State<Shell> = app.state();
    *shell.agent_host_status.lock().unwrap() = Some(status.clone());
    Ok(status)
}

#[tauri::command]
/// Runs off the UI thread. A synchronous `#[tauri::command]` is dispatched on
/// the main thread, so any command that waits on the daemon, the network or a
/// child process freezes every window for its whole duration.
/// Ask this computer's Agent Host to be running. There is no counterpart.
///
/// This used to be `set_enabled(bool)`, and the `false` half was the off switch
/// the workspace page drew as "Turn off". It also wrote a preference that had to
/// be remembered, reconciled against the automatic connection, and reported as a
/// state of its own — which is how "off" became indistinguishable from "not
/// paired", "not installed" and "cannot reach the workspace" in the one place a
/// user looks. Removing the `false` removes the preference, the reconciliation,
/// and the state. Quitting Lemma still stops the sidecar; that is a consequence
/// of the app closing, not a setting.
async fn agent_host_start(window: Webview, app: AppHandle) -> Result<(), String> {
    require_agent_host_caller(&window, &app)?;
    tauri::async_runtime::spawn_blocking(move || agent_host_start_impl(app))
        .await
        .map_err(|error| error.to_string())?
}

fn agent_host_start_impl(app: AppHandle) -> Result<(), String> {
    ensure_agent_host_daemon(&app)?;
    agent_host_request(
        &app,
        json!({
            "cmd": "agent-host.start",
            "id": operation_id("agent-host"),
        }),
    )
}

#[tauri::command]
/// Runs off the UI thread. A synchronous `#[tauri::command]` is dispatched on
/// the main thread, so any command that waits on the daemon, the network or a
/// child process freezes every window for its whole duration.
async fn agent_host_pair(
    window: Webview,
    app: AppHandle,
    url: String,
    pairing_code: String,
    name: String,
) -> Result<(), String> {
    require_agent_host_caller(&window, &app)?;
    tauri::async_runtime::spawn_blocking(move || agent_host_pair_impl(app, url, pairing_code, name))
        .await
        .map_err(|error| error.to_string())?
}

fn agent_host_pair_impl(
    app: AppHandle,
    url: String,
    pairing_code: String,
    name: String,
) -> Result<(), String> {
    if url.trim().is_empty() || pairing_code.trim().is_empty() {
        return Err("pairing needs a workspace URL and a pairing code".into());
    }
    ensure_agent_host_daemon(&app)?;
    agent_host_request(
        &app,
        json!({
            "cmd": "agent-host.pair",
            "id": operation_id("agent-host-pair"),
            "url": url.trim(),
            "pairing_code": pairing_code.trim(),
            "name": name.trim(),
        }),
    )
}

// No `agent_host_unpair`. Dropping the pairing of the machine you are sitting
// at is undone by the next authenticated page, which pairs it again — so the
// command could only ever be honest alongside a flag remembering that you meant
// it, and that flag was a state plane of its own that nothing else could see.
// Removing a computer is `agent.host.revoke` on the backend, where it is durable
// and where it also works for a machine you cannot reach.

#[tauri::command]
/// Runs off the UI thread. A synchronous `#[tauri::command]` is dispatched on
/// the main thread, so any command that waits on the daemon, the network or a
/// child process freezes every window for its whole duration.
async fn agent_host_refresh(window: Webview, app: AppHandle) -> Result<(), String> {
    require_agent_host_caller(&window, &app)?;
    tauri::async_runtime::spawn_blocking(move || agent_host_refresh_impl(app))
        .await
        .map_err(|error| error.to_string())?
}

fn agent_host_refresh_impl(app: AppHandle) -> Result<(), String> {
    ensure_agent_host_daemon(&app)?;
    agent_host_request(
        &app,
        json!({
            "cmd": "agent-host.refresh",
            "id": operation_id("agent-host-refresh"),
        }),
    )
}

/// Whether to bring locald up at launch so the sidecar is there to be reached.
///
/// Read from the files locald itself uses, so the shell can decide before locald
/// exists.
///
/// Purely derived: this machine is paired to something, so it has work waiting.
/// It used to consult `supervisor.json`'s `{"enabled": bool}` first, which was
/// the persisted half of the off switch — and with the switch gone, nothing
/// writes that file, while a `false` left behind by an older build would hold a
/// paired machine off forever with no UI left to turn it back on. An unpaired
/// machine still gets no daemon at all, which is the case this guard exists for.
fn agent_host_wants_to_run() -> bool {
    let root = locald_root();
    let data_dir = root.parent().unwrap_or(&root).join("agent-host");
    let Ok(raw) = std::fs::read_to_string(data_dir.join("config.json")) else {
        return false;
    };
    serde_json::from_str::<Value>(&raw)
        .ok()
        .and_then(|config| {
            config
                .get("targets")
                .and_then(Value::as_array)
                .map(|targets| !targets.is_empty())
        })
        .unwrap_or(false)
}

/// Shared with the CLI-managed host, so both write the same file.
fn agent_host_log_path() -> PathBuf {
    let root = locald_root();
    root.parent()
        .unwrap_or(&root)
        .join("agent-host/agent-host.log")
}

#[tauri::command(async)]
fn agent_host_open_log(window: Webview, app: AppHandle) -> Result<(), String> {
    require_agent_host_caller(&window, &app)?;
    let log = agent_host_log_path();
    if !log.is_file() {
        return Err("the Agent Host has not written a log yet".into());
    }
    reveal_path(&log)
}

/// What the tray says about the Agent Host.
///
/// Reachability, not liveness. A running host that is unpaired or cannot reach
/// its workspace takes no work, so reporting it as simply "on" would be a lie
/// the user only discovers when a run never starts.
///
/// "Reconnecting" is a claim about a connection that is coming back, and it was
/// made for every disconnected state — including a host paired to a workspace
/// that is simply not there any more, which is what a local pairing becomes the
/// moment the local stack stops. That host retries for days behind a word that
/// promises the opposite, so a failed last attempt now says so. The journal
/// carries the error of the latest attempt only, cleared on a connect, so this
/// distinguishes "trying" from "tried and failed" rather than remembering an
/// old failure forever.
fn agent_host_tray_label(
    available: bool,
    running: bool,
    paired: bool,
    connected: bool,
    unreachable: bool,
    failed_to_start: bool,
) -> String {
    if !available {
        "Agent Host: not installed".into()
    } else if !running && failed_to_start {
        // The supervisor tried and could not. Saying "starting…" here is a
        // promise the process is not keeping: it arms a backoff on every failed
        // spawn, so a sidecar that cannot start says "starting…" for as long as
        // the app is open and nothing ever contradicts it.
        "Agent Host: not starting — see log".into()
    } else if !running {
        // Not "off". Nothing can switch this computer off any more, so the only
        // way to be installed, not running and not failing is to be on the way
        // up — and a tray that says "off" with no way to say "on" is a dead end.
        "Agent Host: starting…".into()
    } else if !paired {
        "Agent Host: not paired".into()
    } else if connected {
        "Agent Host: connected".into()
    } else if unreachable {
        "Agent Host: workspace unreachable".into()
    } else {
        "Agent Host: reconnecting…".into()
    }
}

/// Rewrite the tray's Agent Host entries from a status payload.
///
/// The tray is built once and never rebuilt, so without this it would keep
/// claiming whatever was true at launch.
fn refresh_agent_host_tray(app: &AppHandle, status: &Value) {
    let available = status.get("available").and_then(Value::as_bool) == Some(true);
    let running = status.get("running").and_then(Value::as_bool) == Some(true);
    let paired = status.get("paired").and_then(Value::as_bool) == Some(true);
    let targets = status.get("targets").and_then(Value::as_array);
    let connected = targets.is_some_and(|targets| {
        targets
            .iter()
            .any(|target| target.get("connection_state").and_then(Value::as_str) == Some("ONLINE"))
    });
    // Every paired workspace failed its last attempt: nothing here is on its way
    // back, whatever the retry loop is still doing.
    let unreachable = targets.is_some_and(|targets| {
        !targets.is_empty()
            && targets.iter().all(|target| {
                target
                    .get("last_error")
                    .and_then(Value::as_str)
                    .is_some_and(|error| !error.trim().is_empty())
            })
    });

    // The supervisor records why the last spawn or exit failed and clears it on
    // a success, so this is "it tried and could not", not "it failed once weeks
    // ago". Only meaningful while it is not running; a running host's errors are
    // about its workspaces, which the states below already cover.
    let failed_to_start = status
        .get("last_error")
        .and_then(Value::as_str)
        .is_some_and(|error| !error.trim().is_empty());

    let state = agent_host_tray_label(
        available,
        running,
        paired,
        connected,
        unreachable,
        failed_to_start,
    );

    // `running` used to be mirrored onto `Shell::ui` as well, because the tray's
    // toggle had to know which way to point. Nothing asks any more.
    //
    // Clone the handle out and drop the guard before touching it. Every
    // `set_*` below is a blocking round-trip to the main thread, and this runs on
    // the locald reader thread -- so holding the lock across them meant a busy
    // main thread stopped daemon events being read at all. Progress stopped
    // updating and `ready` was never handled, which is how a slow start became a
    // permanently dead-looking splash. `refresh_tray_status` already does this.
    let item = {
        let shell: State<Shell> = app.state();
        let guard = shell.tray_agent_host.lock().unwrap();
        guard.clone()
    };
    let Some(state_item) = item else {
        return;
    };
    let _ = state_item.set_text(state);
    if let Some(tray) = app.tray_by_id("lemma-tray") {
        let _ = tray.set_tooltip(Some(if running && connected {
            "Lemma · Agent Host connected"
        } else if running {
            "Lemma · Agent Host starting"
        } else {
            "Lemma"
        }));
    }
}

#[tauri::command]
/// Runs off the UI thread. A synchronous `#[tauri::command]` is dispatched on
/// the main thread, so any command that waits on the daemon, the network or a
/// child process freezes every window for its whole duration.
async fn apply_operator_config(
    window: Webview,
    app: AppHandle,
    id: String,
    payload: Value,
) -> Result<(), String> {
    require_agent_host_caller(&window, &app)?;
    require_control_window(&window)?;
    tauri::async_runtime::spawn_blocking(move || apply_operator_config_impl(app, id, payload))
        .await
        .map_err(|error| error.to_string())?
}

fn apply_operator_config_impl(app: AppHandle, id: String, payload: Value) -> Result<(), String> {
    ensure_locald(&app)?;
    send_to_locald(
        &app,
        json!({"cmd":"config.apply", "id": id, "payload": payload}),
    )
}

/// Ask locald something and wait for its answer.
///
/// The shared client connection is a broadcast stream: replies arrive as events
/// with no way to hand one back to a specific `invoke`. Commands whose *result*
/// is the point — a model list, an applied profile — take their own short-lived
/// connection and read until their own id comes back, so the caller gets a
/// value and a real error message instead of having to guess from a poll.
fn locald_request(command: Value, timeout: Duration) -> Result<Value, String> {
    let id = command
        .get("id")
        .and_then(Value::as_str)
        .ok_or("request needs an id")?;
    let mut connection = connect_locald_with_mode(true)?;
    writeln!(connection.writer, "{command}")
        .and_then(|_| connection.writer.flush())
        .map_err(|error| format!("could not reach Lemma: {error}"))?;
    ipc_read::response(&mut connection.reader, id, timeout)
}

fn agent_host_request(app: &AppHandle, command: Value) -> Result<(), String> {
    let response = locald_request(command, Duration::from_secs(190))?;
    if let Some(status) = response.get("agent_host").filter(|value| value.is_object()) {
        let shell: State<Shell> = app.state();
        *shell.agent_host_status.lock().unwrap() = Some(status.clone());
        refresh_agent_host_tray(app, status);
    }
    Ok(())
}

/// List a candidate provider's models so the page can offer a picker.
///
/// Reachable from the workspace as well as Local settings: onboarding asks the
/// same question, and the alternative was making people type model ids from
/// memory. It reads nothing and writes nothing.
#[tauri::command]
/// Runs off the UI thread. A synchronous `#[tauri::command]` is dispatched on
/// the main thread, so any command that waits on the daemon, the network or a
/// child process freezes every window for its whole duration.
async fn discover_provider_models(
    window: Webview,
    app: AppHandle,
    payload: Value,
) -> Result<Value, String> {
    // This binds the window and checks it, where it used to take `_window` and
    // discard it -- while `configure_ai_provider`, its sibling one screen down,
    // has always checked. The command is granted to remote origins, and an
    // omitted `api_key` means "use the one in the Keychain", which is then
    // attached as a bearer token to a `base_url` the *caller* chose. So one
    // invoke from any granted origin handed the user's provider key to a host
    // of the caller's choosing, with no dialog and nothing logged.
    require_agent_host_caller(&window, &app)?;
    tauri::async_runtime::spawn_blocking(move || discover_provider_models_impl(app, payload))
        .await
        .map_err(|error| error.to_string())?
}

fn discover_provider_models_impl(app: AppHandle, payload: Value) -> Result<Value, String> {
    ensure_locald(&app)?;
    let response = locald_request(
        json!({
            "cmd": "config.discover-models",
            "id": operation_id("discover-models"),
            "payload": payload,
        }),
        Duration::from_secs(30),
    )?;
    Ok(response.get("models").cloned().unwrap_or(json!([])))
}

/// Point this installation at an AI provider.
///
/// The one piece of operator configuration the workspace may write, and the
/// reason is that onboarding cannot honestly ask "which model?" and then send
/// the user to a different window to answer. Everything else the control page
/// owns — sharing, tunnels, runtime, integrations — stays where it was: this
/// command reaches `config.set-ai`, which merges only that section.
///
/// Blocking on purpose. Applying a provider validates it against the provider
/// and restarts the backend, and both of those can fail in ways the user needs
/// the actual message for.
#[tauri::command]
/// Runs off the UI thread. A synchronous `#[tauri::command]` is dispatched on
/// the main thread, so any command that waits on the daemon, the network or a
/// child process freezes every window for its whole duration.
async fn configure_ai_provider(
    window: Webview,
    app: AppHandle,
    payload: Value,
) -> Result<Value, String> {
    require_agent_host_caller(&window, &app)?;
    tauri::async_runtime::spawn_blocking(move || configure_ai_provider_impl(app, payload))
        .await
        .map_err(|error| error.to_string())?
}

fn configure_ai_provider_impl(app: AppHandle, payload: Value) -> Result<Value, String> {
    if current_mode(&app) != "local" {
        return Err("the local AI provider is configured only on a local install".into());
    }
    ensure_locald(&app)?;
    let response = locald_request(
        json!({
            "cmd": "config.set-ai",
            "id": operation_id("set-ai"),
            "payload": payload,
        }),
        Duration::from_secs(180),
    )?;
    Ok(response.get("operator").cloned().unwrap_or(json!({})))
}

#[tauri::command]
/// Runs off the UI thread. A synchronous `#[tauri::command]` is dispatched on
/// the main thread, so any command that waits on the daemon, the network or a
/// child process freezes every window for its whole duration.
async fn sharing_action(
    window: Webview,
    app: AppHandle,
    action: String,
    id: String,
    payload: Option<Value>,
) -> Result<(), String> {
    require_control_window(&window)?;
    tauri::async_runtime::spawn_blocking(move || sharing_action_impl(app, action, id, payload))
        .await
        .map_err(|error| error.to_string())?
}

fn sharing_action_impl(
    app: AppHandle,
    action: String,
    id: String,
    payload: Option<Value>,
) -> Result<(), String> {
    if current_mode(&app) != "local" {
        return Err("sharing is available only for a local workspace".into());
    }
    if !matches!(
        action.as_str(),
        "snapshot" | "preflight" | "enable" | "disable"
    ) {
        return Err(format!("unknown sharing action: {action}"));
    }
    ensure_locald(&app)?;
    let mut request = json!({
        "cmd": format!("sharing.{action}"),
        "id": id,
    });
    if let Some(payload) = payload {
        if action == "preflight" {
            if let Some(provider) = payload.get("provider") {
                request["provider"] = provider.clone();
            }
        } else {
            request["payload"] = payload;
        }
    }
    send_to_locald(&app, request)
}

#[tauri::command]
fn close_local_settings(window: Webview, app: AppHandle) -> Result<(), String> {
    require_control_window(&window)?;
    window.close().map_err(|error| error.to_string())?;
    if let Some(main) = app.get_window("main") {
        let _ = main.show();
        let _ = main.set_focus();
    }
    Ok(())
}

/// Ask the user to confirm something destructive, from the settings page.
///
/// `window.confirm()` is not usable here: WKWebView routes it through a
/// WKUIDelegate panel that wry does not implement, so it returns false without
/// ever drawing anything. Local settings used that to gate "Stop everything"
/// and "Verify & repair runtime", which made both buttons look inert on macOS
/// -- the click was received and then silently discarded.
///
/// The prompt uses a trusted, app-owned overlay shared with the tray and quit
/// path, with Cancel focused before any destructive action can proceed.
#[tauri::command]
async fn confirm_destructive_action(
    window: Webview,
    app: AppHandle,
    title: String,
    message: String,
    confirm_label: String,
) -> Result<bool, String> {
    require_control_window(&window)?;
    // Async because this waits for a dialog that can only be *shown* from
    // the main thread. As a synchronous command it ran there itself, so it
    // blocked the very thread that had to draw what it was waiting for.
    tauri::async_runtime::spawn_blocking(move || {
        confirm_destructive_action_impl(app, title, message, confirm_label)
    })
    .await
    .map_err(|error| error.to_string())?
}

fn confirm_destructive_action_impl(
    app: AppHandle,
    title: String,
    message: String,
    confirm_label: String,
) -> Result<bool, String> {
    show_app_prompt(app, title, message, confirm_label, true)
}

#[tauri::command]
async fn confirm_settings_changes(
    window: Webview,
    app: AppHandle,
) -> Result<confirmation::Decision, String> {
    require_control_window(&window)?;
    tauri::async_runtime::spawn_blocking(move || {
        show_app_decision(
            app,
            "Save your settings changes?".into(),
            "Save each changed section, discard your drafts, or keep editing.".into(),
            "Save changes".into(),
            true,
            true,
        )
    })
    .await
    .map_err(|error| error.to_string())?
}

fn show_app_prompt(
    app: AppHandle,
    title: String,
    message: String,
    confirm_label: String,
    cancelable: bool,
) -> Result<bool, String> {
    show_app_decision(app, title, message, confirm_label, cancelable, false)
        .map(|decision| decision == confirmation::Decision::Confirm)
}

fn show_app_decision(
    app: AppHandle,
    title: String,
    message: String,
    confirm_label: String,
    cancelable: bool,
    allow_discard: bool,
) -> Result<confirmation::Decision, String> {
    let shell: State<Shell> = app.state();
    let id = operation_id("confirmation");
    let receiver = shell.confirmations.begin(id.clone(), allow_discard)?;
    let result = create_confirmation_overlay(
        &app,
        &id,
        title,
        message,
        confirm_label,
        cancelable,
        allow_discard,
    );
    if let Err(error) = result {
        shell.confirmations.cancel();
        close_confirmation_overlay(&app);
        return Err(error);
    }
    receiver
        .recv()
        .map_err(|_| "The confirmation closed without a decision.".into())
}

fn create_confirmation_overlay(
    app: &AppHandle,
    id: &str,
    title: String,
    message: String,
    confirm_label: String,
    cancelable: bool,
    allow_discard: bool,
) -> Result<(), String> {
    let main = app
        .get_window("main")
        .ok_or("The app window is unavailable.")?;
    restore_dock_presence(app);
    main.show().map_err(|error| error.to_string())?;
    let payload = json!({"id": id, "title": title, "message": message, "confirmLabel": confirm_label, "cancelable": cancelable, "allowDiscard": allow_discard});
    let builder = WebviewBuilder::new("confirmation", WebviewUrl::App("confirmation.html".into()))
        .auto_resize()
        .focused(true)
        .initialization_script(format!("window.__LEMMA_CONFIRMATION__={payload};"))
        .on_navigation(|url| trusted_native_asset_url(url) && url.path() == "/confirmation.html")
        .on_new_window(|_, _| NewWindowResponse::Deny);
    let parent = &main;
    let size = parent.inner_size().map_err(|error| error.to_string())?;
    let overlay = parent
        .add_child(builder, PhysicalPosition::new(0, 0), size)
        .map_err(|error| error.to_string())?;
    overlay.set_focus().map_err(|error| error.to_string())
}

fn close_confirmation_overlay(app: &AppHandle) {
    remove_confirmation_overlay(app);
    if let Some(previous) = app
        .get_webview("control")
        .or_else(|| app.get_webview("main"))
    {
        let _ = previous.set_focus();
    }
}

fn remove_confirmation_overlay(app: &AppHandle) {
    if let Some(overlay) = app.get_webview("confirmation") {
        let _ = overlay.close();
    }
}

#[tauri::command]
async fn resolve_confirmation(
    window: Webview,
    app: AppHandle,
    id: String,
    decision: confirmation::Decision,
) -> Result<(), String> {
    if window.label() != "confirmation"
        || !window
            .url()
            .is_ok_and(|url| trusted_native_asset_url(&url) && url.path() == "/confirmation.html")
    {
        return Err("Only the app confirmation can approve this action.".into());
    }
    let (sender, receiver) = std::sync::mpsc::sync_channel(1);
    let handle = app.clone();
    app.run_on_main_thread(move || {
        let shell: State<Shell> = handle.state();
        let result = shell.confirmations.resolve(&id, decision, || {
            window.close().map_err(|error| error.to_string())?;
            if let Some(previous) = handle
                .get_webview("control")
                .or_else(|| handle.get_webview("main"))
            {
                let _ = previous.set_focus();
            }
            Ok(())
        });
        let _ = sender.send(result);
    })
    .map_err(|error| error.to_string())?;
    tauri::async_runtime::spawn_blocking(move || {
        receiver
            .recv()
            .map_err(|_| "The confirmation closed without a decision.".to_string())?
    })
    .await
    .map_err(|error| error.to_string())?
}

#[tauri::command]
/// Runs off the UI thread. A synchronous `#[tauri::command]` is dispatched on
/// the main thread, so any command that waits on the daemon, the network or a
/// child process freezes the window for its whole duration -- which is how a
/// first launch showed a black, unresponsive app for minutes while the runtime
/// installed and the daemon came up.
async fn set_connection_mode(window: Webview, app: AppHandle, mode: String) -> Result<(), String> {
    require_local_native_window(&window)?;
    tauri::async_runtime::spawn_blocking(move || set_connection_mode_impl(app, mode))
        .await
        .map_err(|error| error.to_string())?
}

fn set_connection_mode_impl(app: AppHandle, mode: String) -> Result<(), String> {
    if mode != "local" && mode != "hosted" {
        return Err(format!("unknown mode {mode:?}"));
    }
    let _ = std::fs::remove_file(app_support_dir().join("recovery-mode"));
    app.state::<Shell>()
        .recovery_mode
        .store(false, Ordering::Release);
    set_mode(&app, &mode)?;
    if mode == "hosted" {
        return open_app_window(&app, &hosted_url());
    }
    ensure_locald(&app)?;
    let setup = std::env::var("LEMMA_DESKTOP_START_SETUP").as_deref() == Ok("1");
    send_local_operation(
        &app,
        json!({"cmd": "start", "setup": setup}),
        operation_id("shell-start"),
    )
}

#[tauri::command]
/// Runs off the UI thread. A synchronous `#[tauri::command]` is dispatched on
/// the main thread, so any command that waits on the daemon, the network or a
/// child process freezes the window for its whole duration -- which is how a
/// first launch showed a black, unresponsive app for minutes while the runtime
/// installed and the daemon came up.
async fn choose_connection_mode(app: AppHandle) -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || choose_connection_mode_impl(app))
        .await
        .map_err(|error| error.to_string())?
}

fn choose_connection_mode_impl(app: AppHandle) -> Result<String, String> {
    let current = current_mode(&app);
    if current == "undecided" {
        show_splash(&app);
        return Ok(current);
    }
    let new_mode = if current == "local" {
        "hosted"
    } else {
        "local"
    };
    set_mode(&app, new_mode)?;
    if new_mode == "hosted" {
        open_app_window(&app, &hosted_url())?;
    } else {
        show_splash(&app);
        start_impl(app)?;
        return Ok(new_mode.into());
    }
    Ok(new_mode.into())
}

#[tauri::command]
fn get_state(app: AppHandle) -> UiState {
    let shell: State<Shell> = app.state();
    let snapshot = shell.ui.lock().unwrap().clone();
    snapshot
}

fn current_mode(app: &AppHandle) -> String {
    let shell: State<Shell> = app.state();
    let ui = shell.ui.lock().unwrap();
    ui.mode.clone()
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
struct WindowPlacement {
    position: tauri::PhysicalPosition<i32>,
    size: tauri::PhysicalSize<u32>,
}

fn placement_of(window: &tauri::Window) -> Option<WindowPlacement> {
    Some(WindowPlacement {
        position: window.outer_position().ok()?,
        size: window.inner_size().ok()?,
    })
}

/// The smallest window the app is willing to restore to.
///
/// Matches `min_inner_size` below. A saved size under it means the record is
/// from a build with different minimums, or was written mid-animation; either
/// way the OS would clamp it and the window would come back a shape the user
/// never chose.
const MIN_RESTORED: (u32, u32) = (980, 680);

/// How tall the draggable strip at the top of a window is, near enough.
///
/// Not read from the OS: this is only ever used to ask whether *some* of the
/// title bar is on a display, and being a few points out changes no answer.
const TITLE_BAR_HEIGHT: i32 = 28;

/// Where the window was when the app last closed.
///
/// Everything about a window's placement is a decision the user made with a
/// mouse, and the app threw all of it away on every quit -- so somebody who
/// works on a 34" display had Lemma come back at 1280x860 in the middle of it,
/// every single morning.
///
/// Read defensively. This is the one piece of state the app restores from disk
/// *before* it can show anything, so a bad value here is an app that opens
/// somewhere the user cannot reach it.
fn remembered_placement(handle: &AppHandle) -> Option<WindowPlacement> {
    let placement = saved_placement(&read_config())?;
    let monitors = match handle.available_monitors() {
        // Nothing to check against is not evidence of a problem. Restoring is
        // the behaviour the user asked for by moving the window in the first
        // place, and the OS still clamps a wildly wrong value.
        Err(_) => return Some(placement),
        Ok(monitors) => monitors,
    };
    let screens: Vec<_> = monitors
        .iter()
        .map(|monitor| (*monitor.position(), *monitor.size()))
        .collect();
    placement_is_reachable(&placement, &screens).then_some(placement)
}

/// Parse a recorded placement, refusing anything that would restore wrong.
///
/// Split from the monitor check so both halves can be tested: this one is
/// about a file that may have been written by another build, hand-edited, or
/// truncated mid-write.
fn saved_placement(config: &Value) -> Option<WindowPlacement> {
    let saved = config.get("window")?;
    let number = |key: &str| saved.get(key)?.as_i64();
    let width = u32::try_from(number("width")?).ok()?;
    let height = u32::try_from(number("height")?).ok()?;
    if width < MIN_RESTORED.0 || height < MIN_RESTORED.1 {
        return None;
    }
    Some(WindowPlacement {
        position: tauri::PhysicalPosition::new(
            i32::try_from(number("x")?).ok()?,
            i32::try_from(number("y")?).ok()?,
        ),
        size: tauri::PhysicalSize::new(width, height),
    })
}

/// A saved placement in the units the window builder actually reads.
///
/// Everything else about placement is in physical pixels and consistently so:
/// `outer_position` and `inner_size` return physical, and
/// `placement_is_reachable` compares them against monitor geometry that is also
/// physical. The window *builder* is the one place that is not --
/// `WebviewWindowBuilder::position` and `inner_size` are documented as logical
/// pixels -- and handing it physical values was silently wrong on every display
/// that is not 1:1.
///
/// On a 2x screen it doubled both: a window saved at 3024x1898 came back asking
/// for 3024x1898 *logical*, which is 6048x3796 physical, so macOS clamped the
/// size to the visible frame, and a saved y of 66 became 132 -- the window
/// opened lower than it was left and short of the top of the screen. Restoring
/// looked broken in a way that reads as "the app won't remember my window".
///
/// Returns None when the window would come back smaller than the app's own
/// minimum. That check belongs here rather than beside the parse, because
/// `MIN_RESTORED` is a logical size and until this point the numbers are not.
fn placement_in_logical(
    placement: &WindowPlacement,
    scale: f64,
) -> Option<(tauri::LogicalPosition<f64>, tauri::LogicalSize<f64>)> {
    if !scale.is_finite() || scale <= 0.0 {
        return None;
    }
    let position = placement.position.to_logical::<f64>(scale);
    let size = placement.size.to_logical::<f64>(scale);
    if size.width < f64::from(MIN_RESTORED.0) || size.height < f64::from(MIN_RESTORED.1) {
        return None;
    }
    Some((position, size))
}

/// The scale of the display a placement lands on, or the primary one.
///
/// Asked per placement rather than taken from the primary monitor, because a
/// second display with a different scale is exactly the case that makes the
/// conversion wrong in a way the user sees.
fn placement_scale_factor(handle: &AppHandle, placement: &WindowPlacement) -> f64 {
    handle
        .monitor_from_point(
            f64::from(placement.position.x),
            f64::from(placement.position.y),
        )
        .ok()
        .flatten()
        .or_else(|| handle.primary_monitor().ok().flatten())
        .map_or(1.0, |monitor| monitor.scale_factor())
}

/// Whether a saved placement still lands on a display that exists.
///
/// The failure this prevents is the classic one: quit with the window on a
/// second monitor, unplug it, launch, and the app restores to coordinates that
/// are now nowhere. The window is real, focused, and invisible, and the only
/// way back is deleting a config file the user does not know about.
///
/// Judged by the window's *title bar* rather than its whole frame, and by a
/// generous strip of it: a window may legitimately hang off the side of a
/// display, but if you cannot grab the top of it you cannot move it back.
fn placement_is_reachable(
    placement: &WindowPlacement,
    screens: &[(tauri::PhysicalPosition<i32>, tauri::PhysicalSize<u32>)],
) -> bool {
    if screens.is_empty() {
        return true;
    }
    // How much of the title bar has to be on a display to be worth calling
    // reachable. Any overlap at all is not enough -- three pixels of chrome
    // poking over the bottom edge is not something a person can grab, and
    // treating it as fine is how the window ends up effectively lost anyway.
    const GRABBABLE_HEIGHT: i32 = 24;
    const GRABBABLE_WIDTH: i32 = 80;

    let bar_top = placement.position.y;
    let bar_bottom = bar_top.saturating_add(TITLE_BAR_HEIGHT);
    let left = placement.position.x;
    let right = left.saturating_add(i32::try_from(placement.size.width).unwrap_or(i32::MAX));
    // Summed across displays, not tested one at a time: a window straddling two
    // monitors has a perfectly grabbable title bar even when neither display
    // holds enough of it on its own.
    screens.iter().any(|(origin, size)| {
        let monitor_right = origin
            .x
            .saturating_add(i32::try_from(size.width).unwrap_or(i32::MAX));
        let monitor_bottom = origin
            .y
            .saturating_add(i32::try_from(size.height).unwrap_or(i32::MAX));
        let visible_width = right.min(monitor_right) - left.max(origin.x);
        let visible_height = bar_bottom.min(monitor_bottom) - bar_top.max(origin.y);
        visible_width >= GRABBABLE_WIDTH && visible_height >= GRABBABLE_HEIGHT
    })
}

/// Record where the window is, so the next launch opens it there.
///
/// Written on move and resize rather than only on quit, because the app is not
/// always quit: it is force-killed, it is replaced by an update, the machine
/// restarts. A geometry that only survives a graceful exit is one that is
/// usually lost. `write_config` is a read-modify-write of a small file and
/// these events arrive at most a few times a second while a drag is in
/// progress, which is well inside what this can absorb.
fn remember_placement(window: &tauri::WebviewWindow) {
    // A minimised or fullscreen window reports a placement that is about the
    // OS's temporary arrangement, not the one the user chose to come back to.
    if window.is_minimized().unwrap_or(false) || window.is_fullscreen().unwrap_or(false) {
        return;
    }
    let Some(placement) = placement_of(&window.as_ref().window()) else {
        return;
    };
    if placement.size.width < MIN_RESTORED.0 || placement.size.height < MIN_RESTORED.1 {
        return;
    }
    let _ = write_config(|config| {
        config["window"] = json!({
            "x": placement.position.x,
            "y": placement.position.y,
            "width": placement.size.width,
            "height": placement.size.height,
        });
    });
}

fn build_main_window(
    handle: &AppHandle,
    mode: &str,
    initial_url: WebviewUrl,
    partitioned: bool,
) -> tauri::Result<tauri::WebviewWindow> {
    build_main_window_at(handle, mode, initial_url, partitioned, false, None)
}

fn build_main_window_at(
    handle: &AppHandle,
    mode: &str,
    initial_url: WebviewUrl,
    partitioned: bool,
    // True only for a rebuild: a window already existed and the user was looking
    // at it, so this one stays off screen until it has something to show.
    replacing: bool,
    // Where that window was, when it could be asked. Absent is not a reason to
    // guess -- an unplaced window lands where the OS puts it, which is what
    // happened before and is still better than moving somebody's window
    // somewhere arbitrary.
    placement: Option<WindowPlacement>,
) -> tauri::Result<tauri::WebviewWindow> {
    let main_builder = WebviewWindowBuilder::new(handle, "main", initial_url)
        .title("Lemma")
        .inner_size(1280.0, 860.0)
        .min_inner_size(980.0, 680.0)
        .devtools(true)
        // Corrected to the real appearance immediately after build.
        // Light is the safer guess to start from: a white flash reads
        // as a page loading, a black one reads as a broken app.
        .background_color(CANVAS_LIGHT)
        .initialization_script(desktop_context_script(mode))
        .on_navigation({
            let handle = handle.clone();
            move |url| {
                let (mode, app_base, api_base) = navigation_context(&handle);
                match navigation_disposition(url, &mode, &app_base, &api_base) {
                    NavigationDisposition::Allow => true,
                    NavigationDisposition::OpenExternal => {
                        open_external(url.as_str());
                        false
                    }
                    NavigationDisposition::Deny => false,
                }
            }
        })
        .on_new_window({
            let handle = handle.clone();
            move |url, _features| {
                let (mode, app_base, api_base) = navigation_context(&handle);
                match new_window_disposition(&url, &mode, &app_base, &api_base) {
                    NewWindowDisposition::NavigateInApp => {
                        let _ = navigate_app_window(&handle, url.as_str());
                    }
                    NewWindowDisposition::OpenAppWindow => {
                        // Logged rather than discarded: every failure here ends
                        // with the user clicking "open in new window" and
                        // nothing happening at all, which is indistinguishable
                        // from a dead button.
                        if let Err(error) = open_pod_app_window(&handle, url.as_str()) {
                            append_install_log(&format!(
                                "could not open a pod app window: {error}"
                            ));
                        }
                    }
                    NewWindowDisposition::OpenExternal => {
                        open_external(url.as_str());
                    }
                    NewWindowDisposition::Deny => {}
                }
                NewWindowResponse::Deny
            }
        })
        .on_download({
            let handle = handle.clone();
            move |_webview, event| match event {
                DownloadEvent::Requested { url, .. } => {
                    let (mode, app_base, api_base) = navigation_context(&handle);
                    download_disposition(&url, &mode, &app_base, &api_base)
                }
                _ => true,
            }
        });

    // Native materials. Vibrancy is only ever visible where the web
    // content declines to paint, so the window has to be transparent
    // for any of it to show — which also means every surface that
    // *should* stay opaque has to say so itself. That sweep is not
    // done, so this stays behind a flag: without it the app composites
    // exactly as it did before, and with it the [data-desktop-vibrancy]
    // rules in styles/tokens.css open up the shell rail.
    #[cfg(target_os = "macos")]
    let main_builder = if desktop_vibrancy_enabled() {
        main_builder.transparent(true)
    } else {
        main_builder
    };

    // Which storage this window gets. Set here because it is a
    // builder-time property: a live webview cannot be moved between
    // stores, which is why switching servers rebuilds the window
    // rather than clearing the one store both used to share.
    #[cfg(target_os = "macos")]
    let main_builder = if partitioned {
        main_builder.data_store_identifier(session_partition_id(mode))
    } else {
        main_builder
    };
    #[cfg(target_os = "windows")]
    let main_builder = if partitioned {
        main_builder.data_directory(session_partition_dir(mode))
    } else {
        main_builder
    };
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    let _ = partitioned;

    // A replacement window is built where the old one stood and stays off screen
    // until it has something to show. Built visible, the swap is a window
    // vanishing, a gap, and a different window appearing at the OS default
    // placement with a blank page loading in it.
    let main_builder = if replacing {
        main_builder.visible(false).on_page_load(|window, payload| {
            if matches!(payload.event(), tauri::webview::PageLoadEvent::Finished) {
                let _ = window.show();
                let _ = window.set_focus();
            }
        })
    } else {
        main_builder
    };
    // A rebuild is told exactly where to sit. A cold start has only what the
    // last session left behind -- and `None` from either is not a reason to
    // guess: an unplaced window lands where the OS puts it, which is right for
    // a first-ever launch and safe for everything else.
    let placement = placement.or_else(|| remembered_placement(handle));
    let main_builder = match placement
        .and_then(|saved| placement_in_logical(&saved, placement_scale_factor(handle, &saved)))
    {
        Some((position, size)) => main_builder
            .position(position.x, position.y)
            .inner_size(size.width, size.height),
        None => main_builder,
    };

    let main = main_builder.build()?;

    // The builder had to guess an appearance before the window existed.
    // Now that it does, ask it, and keep asking: a window whose layer
    // stays light while the page goes dark flashes white on every
    // navigation, which is the same bug with the colours swapped.
    if let Ok(theme) = main.theme() {
        let _ = main.set_background_color(Some(canvas_color(theme)));
    }
    main.on_window_event({
        let window = main.clone();
        move |event| match event {
            tauri::WindowEvent::ThemeChanged(theme) => {
                let _ = window.set_background_color(Some(canvas_color(*theme)));
            }
            // Where the user put the window, kept as they put it. Recorded here
            // rather than on quit alone: an app that is force-killed or
            // replaced by an update never sees a close event, and those are
            // the launches where coming back wrong is most annoying.
            tauri::WindowEvent::Moved(_) | tauri::WindowEvent::Resized(_) => {
                remember_placement(&window);
            }
            _ => {}
        }
    });

    #[cfg(target_os = "macos")]
    if desktop_vibrancy_enabled() {
        use window_vibrancy::{apply_vibrancy, NSVisualEffectMaterial, NSVisualEffectState};

        // Sidebar is the material AppKit itself uses behind source
        // lists, which is what the pod shell rail is.
        // The attribute itself rides in the initialization script, so it
        // is already set on this document and on every document the
        // window navigates to afterwards. Only the failure path needs
        // to say anything here, and it takes the attribute back off so
        // the page is not styled for a material that is not there.
        if let Err(error) = apply_vibrancy(
            &main,
            NSVisualEffectMaterial::Sidebar,
            Some(NSVisualEffectState::FollowsWindowActiveState),
            None,
        ) {
            eprintln!("lemma: could not apply window vibrancy: {error}");
            let _ = main.eval("document.documentElement.removeAttribute('data-desktop-vibrancy')");
        }
    }

    // Only relevant when the OS accent is driving the palette. The
    // accent is read once at launch, so it would otherwise go stale the
    // moment the user changes it in System Settings; re-reading on focus
    // catches exactly that — they leave to change it and come back.
    if desktop_system_accent_enabled() {
        main.on_window_event({
            let window = main.clone();
            move |event| {
                if matches!(
                    event,
                    tauri::WindowEvent::Focused(true) | tauri::WindowEvent::ThemeChanged(_)
                ) {
                    let _ = window.eval(format!(
                        "document.documentElement.style.setProperty('--accent-rgb','{}')",
                        accent_channel_triple(),
                    ));
                }
            }
        });
    }

    if replacing {
        // Shown by the page-load hook once there is something to show, and by
        // this backstop if that never arrives. An invisible window is worse than
        // a flicker, so the deadline gives up rather than leaving the app with
        // no interface -- the same shape as the label wait above it.
        let pending = main.clone();
        std::thread::spawn(move || {
            std::thread::sleep(REPLACEMENT_REVEAL_TIMEOUT);
            if !pending.is_visible().unwrap_or(false) {
                let _ = pending.show();
                let _ = pending.set_focus();
            }
        });
    } else {
        main.show()?;
        main.set_focus()?;
    }
    if std::env::var("LEMMA_DESKTOP_DEVTOOLS").as_deref() == Ok("1") {
        main.open_devtools();
    }
    Ok(main)
}

fn set_mode(app: &AppHandle, mode: &str) -> Result<(), String> {
    write_config(|config| {
        config["connectionMode"] = json!(mode);
        config["connectionModePromptRevision"] = json!(CONNECTION_MODE_PROMPT_REVISION);
    })?;
    refresh_menus_for_connection_mode(app);
    let changed = {
        let shell: State<Shell> = app.state();
        let mut ui = shell.ui.lock().unwrap();
        let changed = ui.mode != mode;
        if changed && mode == "local" {
            ui.url.clear();
            ui.api_url.clear();
        }
        ui.mode = mode.to_string();
        changed
    };
    if changed {
        rebuild_main_window_for_mode(app, mode);
    }
    Ok(())
}

/// The storage partition a server's session lives in.
///
/// Lemma Cloud and a local install are different servers -- different accounts,
/// different databases, different signing keys -- shown in one window. They are
/// also different origins (`lemma.work` versus `app.lemma.localhost`), so the
/// browser's own rules already stop either reading the other's cookies.
///
/// What the origin rules do *not* do is bound a session's lifetime to the
/// server that issued it. `app.lemma.localhost` is a stable hostname reused by
/// every local installation this machine ever has, and cookies ignore the port,
/// so a session minted against one local database is still presented to the
/// next one -- which rejects it, correctly, on every authorized route while
/// `/auth/session/refresh` keeps answering 200 because the refresh token itself
/// is genuinely valid. One install was measured writing 8 MB of backend log an
/// hour in that state, indefinitely.
///
/// Giving each server its own store is what makes the two independent rather
/// than merely non-overlapping. The earlier version of this fix cleared the
/// single shared store whenever the mode changed, which signed the user out of
/// the server they were leaving *and* out of the one they were returning to --
/// and still did not fix the case above, which needs no mode change at all.
///
/// The identifiers are constants, not derived: they have to name the same store
/// on every launch, or a restart would look like a new server and lose the
/// session it was meant to keep.
#[cfg(target_os = "macos")]
fn session_partition_id(mode: &str) -> [u8; 16] {
    // Arbitrary, fixed, and distinct. Never reuse or reorder these.
    const HOSTED: [u8; 16] = *b"lemma.cloud.sess";
    const LOCAL: [u8; 16] = *b"lemma.local.sess";
    if mode == "hosted" {
        HOSTED
    } else {
        LOCAL
    }
}

/// The Windows spelling of the same idea. WebView2 partitions by user-data
/// folder rather than by identifier, so the two servers get two directories.
#[cfg(target_os = "windows")]
fn session_partition_dir(mode: &str) -> PathBuf {
    let name = if mode == "hosted" { "hosted" } else { "local" };
    app_support_dir().join("webview").join(name)
}

/// Clears the swap flag however the rebuild ends, including on an early
/// return. A flag left set would make the app unquittable.
struct ExitGuard(AppHandle);

impl Drop for ExitGuard {
    fn drop(&mut self) {
        let shell: State<Shell> = self.0.state();
        shell.swapping_window.store(false, Ordering::Release);
    }
}

/// How long to wait for the runtime to let go of a destroyed window's label.
///
/// Generous on purpose. In practice the event loop frees it within a few
/// milliseconds, and this runs on a blocking thread during a server switch the
/// user has already been told will take a moment -- so waiting costs nothing
/// anyone can perceive, while giving up early costs them the entire interface.
/// How long a replacement window stays hidden waiting for its first paint.
///
/// A ceiling, not a wait anyone should reach: the page it opens on is local and
/// paints in milliseconds. It exists because the alternative to giving up is an
/// app with no window, which is the failure this whole path already has one
/// backstop for.
const REPLACEMENT_REVEAL_TIMEOUT: Duration = Duration::from_secs(3);
const LABEL_RELEASE_TIMEOUT: Duration = Duration::from_secs(2);
const LABEL_RELEASE_POLL: Duration = Duration::from_millis(10);

/// Poll `still_registered` until it goes false, or `timeout` elapses.
///
/// Returns whether the label came free. Takes the predicate rather than an
/// `AppHandle` so the waiting itself can be tested without a running event
/// loop -- which is the half that was wrong, and the half a source-text
/// assertion could not have caught.
fn wait_until_label_released(
    mut still_registered: impl FnMut() -> bool,
    timeout: Duration,
    interval: Duration,
) -> bool {
    let deadline = Instant::now() + timeout;
    loop {
        if !still_registered() {
            return true;
        }
        if Instant::now() >= deadline {
            return false;
        }
        std::thread::sleep(interval);
    }
}

/// Move the window onto the storage the new server owns.
///
/// A webview's store is fixed when it is built, so this closes the window and
/// builds it again. That is the price of real isolation, and it is paid only on
/// an actual server change: a restart, a reconnect, or a runtime coming back on
/// new ports all keep the window they have.
///
/// Failure is deliberately not fatal to the switch. The mode has already been
/// written and the caller is about to navigate; a window that is still on the
/// previous store shows the right server with the wrong cookie jar, which is
/// the behaviour that shipped before this existed. Refusing to switch servers
/// at all would be worse.
/// Close any pod app window, because what it is showing no longer exists.
///
/// An app window holds an absolute URL on the local backend's port. Switching
/// servers, or locald reallocating ports, leaves it pointed at something that
/// is gone -- and its navigation gate re-reads the mode live, so a window
/// opened under the local policy would start answering to the hosted one, where
/// every http(s) destination is allowed. Closing it is the honest option: it is
/// a view of a pod on a server this app is no longer connected to.
fn close_pod_app_window(app: &AppHandle) {
    if let Some(window) = app.get_webview_window(POD_APP_WINDOW) {
        let _ = window.destroy();
    }
}

fn rebuild_main_window_for_mode(app: &AppHandle, mode: &str) {
    let Some(existing) = app.get_window("main") else {
        // Nothing built yet -- `setup` will create it against the right store.
        return;
    };
    // Held across both halves, and cleared on every path out. `destroy` is
    // deliberate rather than `close`: the app hides to tray on CloseRequested,
    // so `close` would leave the old window alive on the old store and no new
    // one would ever be built.
    // Before the swap flag, so a pod app window cannot be mistaken for the
    // "no windows left" state the flag exists to cover.
    close_pod_app_window(app);
    let shell: State<Shell> = app.state();
    shell.swapping_window.store(true, Ordering::Release);
    let _reset = ExitGuard(app.clone());
    // Read while there is still a window to read it from.
    let placement = placement_of(&existing);
    if let Err(error) = existing.destroy() {
        append_install_log(&format!(
            "[connection-mode] could not close the previous server's window: {error}"
        ));
        return;
    }
    // `destroy` only *posts* the request. Tauri frees the label when the event
    // loop processes `Destroyed` and the manager drops it from its webview map,
    // and this function runs on a blocking thread -- so building here races the
    // event loop and loses, every time, with `a webview with label \`main\`
    // already exists`. The retry below inherited the same failure in the same
    // millisecond, which turned the fallback into a second identical attempt
    // and left the app with no window at all.
    if !wait_until_label_released(
        || app.get_window("main").is_some(),
        LABEL_RELEASE_TIMEOUT,
        LABEL_RELEASE_POLL,
    ) {
        append_install_log(
            "[connection-mode] the previous window still holds the `main` label; \
             building anyway",
        );
    }
    // Rebuilt on the splash rather than on the destination: `set_mode`'s caller
    // navigates immediately afterwards, and for local it has to wait for the
    // daemon first. Starting anywhere else would show one server's page against
    // the other's storage for as long as that takes.
    let initial = WebviewUrl::App("index.html".into());
    // `Some` even when the geometry could not be read: it is what marks this a
    // replacement, and a replacement is hidden until it paints whether or not it
    // also knows where to sit.
    if let Err(error) = build_main_window_at(app, mode, initial.clone(), true, true, placement) {
        // Falling back to the shared store, not to nothing. Per-webview storage
        // is the newer half of this: macOS needs 14 (which the bundle already
        // requires) and Windows gives each webview its own WebView2 environment,
        // which is not something this can prove on every machine it will run on.
        // Losing the partition costs the isolation and restores exactly the
        // behaviour that shipped before it; losing the window leaves the user
        // with an app that has no interface at all.
        append_install_log(&format!(
            "[connection-mode] partitioned window failed for {mode}, \
             falling back to shared storage: {error}"
        ));
        let _ = wait_until_label_released(
            || app.get_window("main").is_some(),
            LABEL_RELEASE_TIMEOUT,
            LABEL_RELEASE_POLL,
        );
        if let Err(error) = build_main_window_at(app, mode, initial, false, true, placement) {
            append_install_log(&format!(
                "[connection-mode] could not reopen the window for {mode}: {error}"
            ));
        }
    }
}

// ---------------------------------------------------------------------------
// Navigation policy: ordinary web navigations stay in the primary webview so
// cross-origin app and widget iframes behave exactly as they do in a browser.
// Explicit new-window requests and marked desktop auth still belong in the
// system browser.
// ---------------------------------------------------------------------------

#[derive(Debug, PartialEq, Eq)]
enum NavigationDisposition {
    Allow,
    OpenExternal,
    Deny,
}

#[derive(Debug, PartialEq, Eq)]
enum NewWindowDisposition {
    NavigateInApp,
    /// A published pod app, which gets a window of its own rather than taking
    /// over the one Lemma is running in.
    OpenAppWindow,
    OpenExternal,
    Deny,
}

fn same_origin(url: &tauri::Url, target: &str) -> bool {
    let Ok(target) = tauri::Url::parse(target) else {
        return false;
    };
    url.scheme() == target.scheme()
        && url.host_str() == target.host_str()
        && url.port_or_known_default() == target.port_or_known_default()
}

/// The local domains this build will serve a workspace under.
///
/// Compiled in on purpose. `trusted_workspace_urls` exists to stop a `ready`
/// event pointing the workspace somewhere else, so deriving the acceptable
/// hostname from that same event would answer the question with the thing being
/// questioned. A short list the shell ships knowing keeps the gate meaning
/// something while letting the domain move.
///
/// Kept in step with `lemma_locald::local_domain`, which is what actually picks
/// one -- the shell launches locald rather than linking it, so there is no
/// shared constant to reach for.
const TRUSTED_LOCAL_BASES: &[&str] = &["lemma.localhost", "127.0.0.1.sslip.io"];

/// Whether `host` is the workspace host of a domain this build knows.
fn trusted_local_workspace_host(host: &str) -> bool {
    TRUSTED_LOCAL_BASES
        .iter()
        .any(|base| host == format!("app.{base}"))
}

fn trusted_workspace_urls(app_base: &str, api_base: &str) -> bool {
    let (Ok(app), Ok(api)) = (tauri::Url::parse(app_base), tauri::Url::parse(api_base)) else {
        return false;
    };
    if app.username() != ""
        || app.password().is_some()
        || app.query().is_some()
        || app.fragment().is_some()
        || api.username() != ""
        || api.password().is_some()
        || api.query().is_some()
        || api.fragment().is_some()
        || app.path() != "/"
    {
        return false;
    }

    if app.host_str().is_some_and(trusted_local_workspace_host) {
        let (Some(app_port), Some(api_port)) = (app.port(), api.port()) else {
            return false;
        };
        return app.scheme() == "http"
            && api.scheme() == "http"
            // The same host as the workspace, which the allowlist above has
            // already vetted. Checking the literal twice let the two drift;
            // what this arrangement actually requires is one hostname on two
            // ports.
            && api.host_str() == app.host_str()
            && api.path() == "/"
            && app_port >= 49_152
            && api_port >= 49_152
            && app_port != api_port;
    }

    same_origin(&api, app_base)
        && api.path() == "/_lemma/api"
        && matches!(app.scheme(), "http" | "https")
        && (app.scheme() == "https" || local_destination(&app, api_base))
}

fn is_desktop_browser_auth_url(url: &tauri::Url) -> bool {
    matches!(url.scheme(), "http" | "https")
        && url.path().starts_with("/auth")
        && url
            .query_pairs()
            .any(|(key, value)| key == "desktop_browser" && value == "1")
}

fn navigation_context(app: &AppHandle) -> (String, String, String) {
    let shell: State<Shell> = app.state();
    let ui = shell.ui.lock().unwrap();
    (ui.mode.clone(), ui.url.clone(), ui.api_url.clone())
}

/// The domain this installation is served under, from the API base it was given.
///
/// `http://app.127.0.0.1.sslip.io:63288` -> `127.0.0.1.sslip.io`. Derived rather
/// than compiled in, because the shell does not link locald -- it launches it --
/// so the hostname arrives at runtime in the `ready` event and this is the only
/// honest source for it.
fn local_base_domain(api_base: &str) -> Option<String> {
    let host = tauri::Url::parse(api_base)
        .ok()?
        .host_str()?
        .to_ascii_lowercase();
    let (_first, rest) = host.split_once('.')?;
    (!rest.is_empty()).then(|| rest.to_owned())
}

fn local_destination(url: &tauri::Url, api_base: &str) -> bool {
    let Some(host) = url.host_str() else {
        return false;
    };
    let host = host.to_ascii_lowercase();
    if host == "localhost" || host.ends_with(".localhost") {
        return true;
    }
    // The domain this installation serves itself under is a local destination
    // whatever it resolves through.
    //
    // This is the security-relevant half of moving off `*.localhost`. In local
    // mode the gate below *allows* anything that is not a local destination, on
    // the reasoning that an ordinary internet site is not a way to reach this
    // machine. A public name that answers 127.0.0.1 breaks that reasoning: every
    // `<anything>.127.0.0.1.sslip.io` is loopback, so without this the workspace
    // could be navigated to an attacker-chosen name and reach any port on the
    // user's machine -- a hole that does not exist today, because
    // `*.lemma.localhost` matches the check above and is denied unless it is
    // ours.
    if let Some(base) = local_base_domain(api_base) {
        if host == base || host.ends_with(&format!(".{base}")) {
            return true;
        }
    }
    let Ok(address) = host.parse::<IpAddr>() else {
        return false;
    };
    match address {
        IpAddr::V4(address) => {
            address.is_loopback()
                || address.is_private()
                || address.is_link_local()
                || address.is_unspecified()
        }
        IpAddr::V6(address) => {
            address.is_loopback()
                || address.is_unique_local()
                || address.is_unicast_link_local()
                || address.is_unspecified()
        }
    }
}

fn owned_published_app(url: &tauri::Url, api_base: &str) -> bool {
    let Ok(api) = tauri::Url::parse(api_base) else {
        return false;
    };
    url.scheme() == "http"
        && api.scheme() == "http"
        && url.port() == api.port()
        && url.host_str().is_some_and(|host| {
            TRUSTED_LOCAL_BASES
                .iter()
                .any(|base| host.ends_with(&format!(".apps.{base}")))
        })
}

/// The documents a frame renders without fetching anything: the content document
/// of an `srcdoc` iframe, and the blank document a frame starts life on.
fn frame_content_url(url: &tauri::Url) -> bool {
    url.scheme() == "about" && matches!(url.path(), "srcdoc" | "blank")
}

fn navigation_disposition(
    url: &tauri::Url,
    mode: &str,
    app_base: &str,
    api_base: &str,
) -> NavigationDisposition {
    if is_desktop_browser_auth_url(url) {
        NavigationDisposition::OpenExternal
    } else if frame_content_url(url) {
        // This handler sees every navigation in the window, not just the top
        // frame's: WKWebView asks its navigation delegate about subframes too,
        // and nothing between here and it filters on isMainFrame. So an iframe
        // rendering inline HTML arrives here as `about:srcdoc`, the scheme gate
        // below answered Cancel, and the frame stayed blank — no error, no
        // console entry. That was every HTML and .docx preview in the document
        // viewer, on macOS only, because the Windows webview reports main-frame
        // navigation alone.
        //
        // Admitting these two costs nothing at the top level: WebKit will not
        // navigate a main frame to `about:srcdoc` at all, and `about:blank` is
        // already reachable by any script the page can already run on itself.
        // `new_window_disposition` still refuses `about:blank` popups.
        NavigationDisposition::Allow
    } else if trusted_native_asset_url(url) {
        // Our own bundled pages, whichever origin this build serves them from.
        // Testing `scheme() == "tauri"` alone was right for a packaged app and
        // silently wrong for a dev one: `cargo tauri dev` serves those same
        // files over loopback http, and the local-mode branch below denies
        // every local http destination that is not the workspace. So the
        // splash was refused before it could paint, and the window stayed white
        // until the workspace URL replaced it a minute later.
        NavigationDisposition::Allow
    } else if !matches!(url.scheme(), "http" | "https") {
        NavigationDisposition::Deny
    } else if mode != "local"
        || same_origin(url, app_base)
        || same_origin(url, api_base)
        || owned_published_app(url, api_base)
        || !local_destination(url, api_base)
    {
        NavigationDisposition::Allow
    } else {
        NavigationDisposition::Deny
    }
}

fn new_window_disposition(
    url: &tauri::Url,
    mode: &str,
    app_base: &str,
    api_base: &str,
) -> NewWindowDisposition {
    if url.as_str() == "about:blank" {
        NewWindowDisposition::Deny
    } else if is_desktop_browser_auth_url(url) {
        NewWindowDisposition::OpenExternal
    } else if navigation_disposition(url, mode, app_base, api_base) == NavigationDisposition::Allow
        && owned_published_app(url, api_base)
    {
        // Tested before the in-app branch, which used to claim these: an app
        // opened "in a new window" replaced the workspace in the only window
        // there was, and the user's way back was to quit.
        NewWindowDisposition::OpenAppWindow
    } else if navigation_disposition(url, mode, app_base, api_base) == NavigationDisposition::Allow
        && (url.scheme() == "tauri" || same_origin(url, app_base) || same_origin(url, api_base))
    {
        NewWindowDisposition::NavigateInApp
    } else if navigation_disposition(url, mode, app_base, api_base) == NavigationDisposition::Allow
    {
        NewWindowDisposition::OpenExternal
    } else {
        NewWindowDisposition::Deny
    }
}

/// Where a download's bytes come from, for the purpose of trusting it. Every
/// download the workspace triggers is an `a[download]` click on an object URL
/// the page minted itself, which arrives as `blob:http://origin/uuid` — the
/// creating origin is the opaque path, and it is the only part worth judging.
fn download_source_url(url: &tauri::Url) -> Option<tauri::Url> {
    if url.scheme() == "blob" {
        return tauri::Url::parse(url.path()).ok();
    }
    Some(url.clone())
}

/// Whether to let a download proceed. Registering any policy at all is what
/// makes downloads work: with no download handler the webview cancels the
/// navigation outright, which is why Download buttons did nothing on macOS and
/// said nothing about it. The destination is left as the webview computed it —
/// the user's Downloads folder, uniquified against what is already there.
fn download_disposition(url: &tauri::Url, mode: &str, app_base: &str, api_base: &str) -> bool {
    let Some(source) = download_source_url(url) else {
        return false;
    };
    // Held to the same test as navigating there would be, which among other
    // things keeps `file:` and `data:` out.
    matches!(source.scheme(), "http" | "https")
        && navigation_disposition(&source, mode, app_base, api_base) == NavigationDisposition::Allow
}

/// Hand a URL to the user's browser.
///
/// The failure is logged rather than dropped. Every path that decides a link
/// belongs outside the app ends here, and a discarded error made a launch that
/// never happened look exactly like a link that was never clicked -- nothing
/// moves, nothing is said, and the only thing left to suspect is the link.
fn open_external(url: &str) {
    #[cfg(target_os = "macos")]
    let mut command = Command::new("/usr/bin/open");
    #[cfg(target_os = "windows")]
    let mut command = Command::new("explorer.exe");
    #[cfg(all(unix, not(target_os = "macos")))]
    let mut command = Command::new("xdg-open");
    if let Err(error) = command.arg(url).spawn() {
        append_install_log(&format!("could not open {url} in the browser: {error}"));
    }
}

fn handle_deep_link(app: &AppHandle, url: &tauri::Url) {
    if url.scheme() != "lemma" || url.host_str() != Some("auth") || url.path() != "/complete" {
        return;
    }
    if let Some(window) = app.get_window("main") {
        let _ = window.show();
        let _ = window.set_focus();
    }
    // Older builds used a second native auth webview. Hide it if it still
    // exists; the main window now owns the one-time session exchange.
    if let Some(window) = app.get_webview_window("auth") {
        let _ = window.hide();
    }
}

/// The last accent AppKit was asked for, and the only answer anything off the
/// main thread is allowed to see.
#[cfg(target_os = "macos")]
static REMEMBERED_ACCENT: Mutex<Option<(u8, u8, u8)>> = Mutex::new(None);

/// The user's System Settings accent, as sRGB bytes.
///
/// `controlAccentColor` is a catalog colour with no component accessors of its
/// own — it has to be resolved into a real colour space before it can be read,
/// which is what the `colorUsingColorSpace` hop is for. Returns `None` if that
/// resolution fails, and callers fall back to systemBlue, the macOS default.
///
/// The main-thread gate is not a formality. AppKit is main-thread-only, and
/// this is reached from threads that are not it: `open_local_settings` builds
/// the control webview from a spawned thread on purpose, and the Rust test
/// harness runs every test on a spawned thread of a process that never
/// created an NSApplication at all. Two of those test threads landing in
/// AppKit's first-use initialization at once is what killed the whole
/// `lemma-desktop` test binary with SIGSEGV partway through a CI run — no
/// assertion failed, the process simply died, and the two tests that call
/// `desktop_context_script` were the only two that never reported a result.
///
/// So the read happens on the main thread and its answer is remembered.
/// Everyone else gets the remembered answer — which the main window's setup
/// has already stored by the time any worker can ask — or `None` before the
/// first read, which is the same fallback a machine that cannot answer gets.
#[cfg(target_os = "macos")]
fn macos_accent_rgb() -> Option<(u8, u8, u8)> {
    use objc2::MainThreadMarker;
    use objc2_app_kit::{NSColor, NSColorSpace};

    fn remembered() -> Option<(u8, u8, u8)> {
        *REMEMBERED_ACCENT
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
    }

    if MainThreadMarker::new().is_none() {
        return remembered();
    }
    let accent = NSColor::controlAccentColor();
    let Some(srgb) = accent.colorUsingColorSpace(&NSColorSpace::sRGBColorSpace()) else {
        // A failed resolution says nothing about the accent that was read
        // before it, so the remembered one stands rather than being cleared.
        return remembered();
    };
    let to_byte = |v: f64| (v.clamp(0.0, 1.0) * 255.0).round() as u8;
    let rgb = (
        to_byte(srgb.redComponent()),
        to_byte(srgb.greenComponent()),
        to_byte(srgb.blueComponent()),
    );
    *REMEMBERED_ACCENT
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner()) = Some(rgb);
    Some(rgb)
}

#[cfg(not(target_os = "macos"))]
fn macos_accent_rgb() -> Option<(u8, u8, u8)> {
    None
}

/// `"90 63 212"` — the channel triple the frontend's `--accent-rgb` expects.
///
/// Falls back to the brand violet, which is also what the web build uses, so a
/// machine that cannot answer looks like every other install rather than blue.
fn accent_channel_triple() -> String {
    let (r, g, b) = macos_accent_rgb().unwrap_or((90, 63, 212));
    format!("{r} {g} {b}")
}

/// Opt-in until every surface that must stay opaque paints its own background.
#[cfg(target_os = "macos")]
fn desktop_vibrancy_enabled() -> bool {
    std::env::var("LEMMA_DESKTOP_VIBRANCY").as_deref() == Ok("1")
}

#[cfg(not(target_os = "macos"))]
fn desktop_vibrancy_enabled() -> bool {
    false
}

/// Off by default: action colour is the brand violet, and letting the OS accent
/// overwrite it would hand the product's one loud colour to a system preference.
/// The reader stays wired up because "tint the app to my Mac" is a plausible
/// What switching connection will do, in the terms the person is about to live
/// with.
///
/// The switch used to happen on the press. Choosing Local starts the private
/// runtime — a VM boot, an image pull on a cold machine, and a ninety-second
/// health gate before it will say whether it worked — and choosing Hosted takes
/// the workspace away from the stack still running on this Mac. Neither is a
/// thing to do because a menu item was next to the one you meant.
fn connection_switch_prompt(current: &str, running: bool) -> (String, String, String) {
    if current == "local" {
        (
            "Use the hosted workspace?".into(),
            if running {
                format!("Lemma keeps running on {THIS_COMPUTER} and your local pods stay where they are — this window just stops pointing at them. Use this menu item again to come back.")
            } else {
                format!("This window will point at the hosted workspace instead of {THIS_COMPUTER}. Your local pods stay where they are. Use this menu item again to come back.")
            },
            "Use Hosted".into(),
        )
    } else {
        (
            format!("Run Lemma on {THIS_COMPUTER}?"),
            "Starting the local stack boots a private Linux runtime and waits for its database, cache, and auth service. On a cold machine that takes a few minutes, and the window will show the splash until it is ready."
                .into(),
            "Start Local".into(),
        )
    }
}

/// Ask before switching, then switch and say so if it fails.
fn confirm_then_switch_connection(app: AppHandle) {
    let current = current_mode(&app);
    if current == "undecided" {
        show_splash(&app);
        return;
    }
    let running = {
        let shell: State<Shell> = app.state();
        let ui = shell.ui.lock().unwrap();
        ui.running
    };
    let (title, body, confirm) = connection_switch_prompt(&current, running);
    let handle = app.clone();
    std::thread::spawn(move || {
        let result = confirm_destructive_action_impl(handle.clone(), title, body, confirm)
            .and_then(|confirmed| {
                if confirmed {
                    choose_connection_mode_impl(handle.clone()).map(|_| ())
                } else {
                    Ok(())
                }
            });
        if let Err(error) = result {
            report_action_failure(&handle, "Switch connection", &error);
        }
    });
}

/// Say so when a menu action fails.
///
/// Every arm of `handle_menu_action` used to discard its `Result`, so a menu
/// item could not report a failure even in principle: a local start that spent
/// ninety seconds failing to reach the private runtime, or an Agent Host
/// command that never left the shell, both looked exactly like a dead button.
/// The daemon wrote the reason to its log and the person pressing the item was
/// told nothing.
fn report_action_failure(app: &AppHandle, action: &str, error: &str) {
    append_bounded_log(
        &launch_log_path(),
        &format!("menu {action} failed: {error}"),
    );
    let handle = app.clone();
    let title = action.to_owned();
    let message = error.to_owned();
    std::thread::spawn(move || {
        let _ = show_app_prompt(handle, title, message, "Close".into(), false);
    });
}

/// Run a menu action, and surface whatever it has to say about failing.
fn menu_attempt(app: &AppHandle, action: &str, work: impl FnOnce() -> Result<(), String>) {
    if let Err(error) = work() {
        report_action_failure(app, action, &error);
    }
}

/// Run a menu action that waits on something, without freezing the app.
///
/// Menu and tray handlers are called on the main thread, so a verb that talks
/// to the daemon from one blocks every window for as long as it takes -- the
/// same failure the Tauri commands had, reached through the tray instead of the
/// page. Starting Lemma from the tray could freeze the app for the length of a
/// runtime install.
///
/// Failure still surfaces the same way: `report_action_failure` opens a dialog,
/// and that dispatches to the main thread on its own, so it is safe from here.
fn menu_background(
    app: &AppHandle,
    action: &'static str,
    work: impl FnOnce() -> Result<(), String> + Send + 'static,
) {
    let handle = app.clone();
    std::thread::spawn(move || {
        if let Err(error) = work() {
            report_action_failure(&handle, action, &error);
        }
    });
}

/// setting to offer later — it just is not the default identity.
fn desktop_system_accent_enabled() -> bool {
    std::env::var("LEMMA_DESKTOP_SYSTEM_ACCENT").as_deref() == Ok("1")
}

fn desktop_context_script(mode: &str) -> String {
    let context = json!({
        "version": env!("CARGO_PKG_VERSION"),
        "mode": mode,
        "platform": std::env::consts::OS,
        "accentRgb": accent_channel_triple(),
        "systemAccent": desktop_system_accent_enabled(),
        "vibrancy": desktop_vibrancy_enabled(),
    });
    let local_auth = if mode == "local" {
        // NEXT_PUBLIC values are also rendered into the native host-pack
        // environment. Inject the local auth policy before any page script as
        // a cache-independent guard for an already-open desktop webview.
        "window.__LEMMA_AUTH_CONFIG__ = Object.freeze({AUTH_EMAIL_VERIFICATION_REQUIRED: \"false\"});"
    } else {
        ""
    };
    // Runs before any page script, so whatever it sets is already true at first
    // paint rather than corrected a frame later — and it runs again for every
    // document, which is the reason none of this is a one-shot eval: local mode
    // navigates this same window from the splash to the workspace, and an
    // attribute written by eval would not survive that navigation.
    //
    // The platform attribute always goes on; the accent override and the
    // vibrancy attribute only when their flag asked for them.
    let bootstrap = "(function () {\
          var d = window.__LEMMA_DESKTOP__;\
          var root = document.documentElement;\
          if (!d || !root) return;\
          root.setAttribute('data-desktop-platform', d.platform);\
          if (d.systemAccent) root.style.setProperty('--accent-rgb', d.accentRgb);\
          if (d.vibrancy) root.setAttribute('data-desktop-vibrancy', 'macos');\
        })();";
    format!(
        "window.__LEMMA_DESKTOP__ = Object.freeze({});{}{}",
        serde_json::to_string(&context).unwrap_or_else(|_| "{}".into()),
        bootstrap,
        local_auth,
    )
}

// ---------------------------------------------------------------------------

fn app_base_url(app: &AppHandle) -> Result<String, String> {
    let (mode, url, api_url) = {
        let shell: State<Shell> = app.state();
        let ui = shell.ui.lock().unwrap();
        (ui.mode.clone(), ui.url.clone(), ui.api_url.clone())
    };
    if mode == "hosted" {
        Ok(hosted_url())
    } else if trusted_workspace_urls(&url, &api_url) {
        Ok(url)
    } else {
        Err("the authenticated local workspace is not ready yet".into())
    }
}

fn desktop_auth_url(base: &str, auth_mode: &str) -> String {
    format!(
        "{}/auth/desktop?mode={auth_mode}",
        base.trim_end_matches('/'),
    )
}

fn local_auth_url(base: &str, auth_mode: &str) -> String {
    format!("{}/auth?show={auth_mode}", base.trim_end_matches('/'),)
}

/// The auth portal, told where to go once it is done.
///
/// Without a return address the portal has nowhere to send someone who is
/// already signed in, so it stops and offers a "Continue" button. That is the
/// right screen when a person navigated to sign-in themselves and might mean to
/// switch accounts. It is the wrong one on launch: the app asked for the
/// workspace, the session is already there, and the only thing between the two
/// was a click.
///
/// This is reached on every cold start, not just a first run. A launch mints a
/// new runtime generation, so the recorded resume target never matches and the
/// app falls back to the portal each time -- which is why the button was on
/// screen every single launch rather than occasionally.
///
/// The return address stays relative on purpose. It is resolved against the
/// portal's own origin, so it cannot point off it, and it survives locald
/// handing out a different port than the one this launch happens to use.
fn local_auth_url_returning_to(base: &str, auth_mode: &str, route: &str) -> String {
    let route = if route.starts_with('/') { route } else { "/" };
    let mut url = format!("{}/auth", base.trim_end_matches('/'));
    match tauri::Url::parse(&url) {
        Ok(mut parsed) => {
            parsed
                .query_pairs_mut()
                .append_pair("show", auth_mode)
                .append_pair("redirect_uri", route);
            parsed.to_string()
        }
        // A base this malformed will fail at navigation anyway; falling back to
        // the plain portal keeps that the failure rather than a panic here.
        Err(_) => {
            url.push_str("?show=");
            url.push_str(auth_mode);
            url
        }
    }
}

#[tauri::command]
async fn login(app: AppHandle, mode: Option<String>) -> Result<(), String> {
    let base = app_base_url(&app)?;
    let connection_mode = current_mode(&app);
    let auth_mode = if mode.as_deref() == Some("signup") {
        "signup"
    } else {
        "signin"
    };
    let url = if connection_mode == "local" {
        local_auth_url(&base, auth_mode)
    } else {
        // Hosted accounts keep credentials in the user's normal browser and
        // return through the one-time PKCE-style desktop handoff.
        desktop_auth_url(&base, auth_mode)
    };
    open_app_window(&app, &url)
}

/// Every menu action, wherever it was invoked from.
///
/// The app menu and the tray now name the same verbs, so routing them through
/// one function is what keeps them from drifting into two different products'
/// worth of behaviour.
fn handle_menu_action(app: &AppHandle, id: &str) {
    let app = app.clone();
    match id {
        "open" | "home" => {
            let handle = app.clone();
            menu_attempt(&handle, "Open Lemma", || open_app_impl(app).map(|_| ()));
        }
        "login" => {
            tauri::async_runtime::spawn(async move {
                let _ = login(app, Some("signin".into())).await;
            });
        }
        "back" => {
            if let Some(window) = app.get_webview("main") {
                let _ = window.eval("window.history.back()");
            }
        }
        "forward" => {
            if let Some(window) = app.get_webview("main") {
                let _ = window.eval("window.history.forward()");
            }
        }
        "reload" => {
            if let Some(window) = app.get_webview("main") {
                let _ = window.eval("window.location.reload()");
            }
        }
        "start" => {
            let handle = app.clone();
            menu_background(&app, "Start Lemma", move || start_impl(handle).map(|_| ()));
        }
        "stop" => {
            let handle = app.clone();
            menu_background(&app, "Stop Lemma", move || {
                stop_impl(handle, Some(false)).map(|_| ())
            });
        }
        "stop-all" => {
            let handle = app.clone();
            menu_background(&app, "Stop Lemma completely", move || {
                stop_impl(handle, Some(true)).map(|_| ())
            });
        }
        "restart" => {
            let handle = app.clone();
            menu_background(&app, "Restart Lemma", move || {
                restart_impl(handle).map(|_| ())
            });
        }
        "mode" => {
            confirm_then_switch_connection(app);
        }
        "autostart" => {
            // Reading and writing a launch-agent plist.
            let handle = app.clone();
            menu_background(&app, "Open Lemma at login", move || {
                let autolaunch = handle.autolaunch();
                if autolaunch.is_enabled().unwrap_or(false) {
                    let _ = autolaunch.disable();
                } else {
                    let _ = autolaunch.enable();
                }
                Ok(())
            });
        }
        "control" => {
            menu_attempt(&app, "Local settings", || show_control_center(&app));
        }
        "control-ai" => {
            let _ = show_control_center_page(&app, Some("ai"));
        }
        "control-sharing" => {
            let _ = show_control_center_page(&app, Some("sharing"));
        }
        "diagnostics" => {
            let _ = show_control_center_page(&app, Some("diagnostics"));
        }
        "recovery" => {
            let _ = show_control_center_page(&app, Some("recovery"));
        }
        "agent-host-log" => {
            menu_background(&app, "Open Agent Host log", || {
                reveal_path(&agent_host_log_path())
            });
        }
        "logs" => {
            menu_background(&app, "Open logs", open_logs_impl);
        }
        "docs" => {
            menu_background(&app, "Open documentation", || {
                open_external(&format!("{}/docs", hosted_url().trim_end_matches('/')));
                Ok(())
            });
        }
        "devtools" => {
            if let Some(window) = app.get_webview("main") {
                window.open_devtools();
                let _ = window.window().show();
                let _ = window.set_focus();
            }
        }
        "quit" => {
            request_quit(&app);
        }
        _ => {}
    }
}

/// The macOS menu bar.
///
/// Until now there was none: the shipped build used Tauri's default (app name /
/// Edit / View / Window / Help) and every Lemma verb lived in the tray, which
/// meant no ⌘, for settings and no discoverable way to do anything without
/// going to the menu bar extra. The names here are the product's, not the
/// supervisor's: Stop Lemma completely, rather than the old Stop Services and
/// Infra.
fn build_app_menu(app: &AppHandle) -> tauri::Result<Menu<tauri::Wry>> {
    let local = connection_mode() == "local";
    let about = PredefinedMenuItem::about(
        app,
        Some("About Lemma"),
        Some(AboutMetadata {
            name: Some("Lemma".into()),
            version: Some(env!("CARGO_PKG_VERSION").into()),
            website: Some("https://lemma.work".into()),
            ..Default::default()
        }),
    )?;
    let settings = MenuItem::with_id(
        app,
        "control",
        "Desktop settings…",
        true,
        Some("CmdOrCtrl+,"),
    )?;
    let connection = MenuItem::with_id(app, "mode", "Connection…", true, None::<&str>)?;
    let recovery = MenuItem::with_id(app, "recovery", "Recovery…", true, None::<&str>)?;

    // Services / Hide / Hide Others / Show All are AppKit application-menu
    // conventions. muda will happily construct them elsewhere, where they
    // become inert rows — dead entries in a Windows menu — so they are built
    // only where they mean something.
    #[cfg(target_os = "macos")]
    let platform_items: Vec<Box<dyn tauri::menu::IsMenuItem<tauri::Wry>>> = vec![
        Box::new(PredefinedMenuItem::services(app, None)?),
        Box::new(PredefinedMenuItem::separator(app)?),
        Box::new(PredefinedMenuItem::hide(app, None)?),
        Box::new(PredefinedMenuItem::hide_others(app, None)?),
        Box::new(PredefinedMenuItem::show_all(app, None)?),
        Box::new(PredefinedMenuItem::separator(app)?),
    ];
    #[cfg(not(target_os = "macos"))]
    let platform_items: Vec<Box<dyn tauri::menu::IsMenuItem<tauri::Wry>>> = Vec::new();

    let mut lemma_items: Vec<&dyn tauri::menu::IsMenuItem<tauri::Wry>> = Vec::new();
    let lemma_separator = PredefinedMenuItem::separator(app)?;
    // Deliberately app-owned rather than AppKit's predefined quit, which is
    // `terminate:` and cannot be intercepted. Quit stops the local server, and
    // that is worth one sentence first, so the app has to own ⌘Q.
    let quit = MenuItem::with_id(app, "quit", "Quit Lemma", true, Some("CmdOrCtrl+Q"))?;
    lemma_items.push(&about);
    lemma_items.push(&lemma_separator);
    lemma_items.push(&settings);
    lemma_items.push(&recovery);
    lemma_items.push(&connection);
    lemma_items.push(&lemma_separator);
    lemma_items.extend(platform_items.iter().map(|item| item.as_ref()));
    lemma_items.push(&quit);

    let lemma_menu = Submenu::with_items(app, "Lemma", true, &lemma_items)?;

    let edit_menu = Submenu::with_items(
        app,
        "Edit",
        true,
        &[
            &PredefinedMenuItem::undo(app, None)?,
            &PredefinedMenuItem::redo(app, None)?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::cut(app, None)?,
            &PredefinedMenuItem::copy(app, None)?,
            &PredefinedMenuItem::paste(app, None)?,
            &PredefinedMenuItem::select_all(app, None)?,
        ],
    )?;

    let view_menu = Submenu::with_items(
        app,
        "View",
        true,
        &[
            &MenuItem::with_id(app, "home", "Lemma Home", true, Some("Shift+CmdOrCtrl+H"))?,
            &MenuItem::with_id(app, "back", "Back", true, Some("CmdOrCtrl+["))?,
            &MenuItem::with_id(app, "forward", "Forward", true, Some("CmdOrCtrl+]"))?,
            &MenuItem::with_id(app, "reload", "Reload", true, Some("CmdOrCtrl+R"))?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::fullscreen(app, None)?,
            &PredefinedMenuItem::separator(app)?,
            // Enabled only in a development build. Shipping a web inspector in
            // the top-level View menu, on Cmd-Alt-I, invites a stranger into a
            // surface that talks to the workspace over IPC -- and there is
            // nothing there for them. Diagnostics is the supported path, and
            // Troubleshoot still carries this for anyone who needs it.
            &MenuItem::with_id(
                app,
                "devtools",
                "Developer Tools",
                cfg!(debug_assertions),
                Some("CmdOrCtrl+Alt+I"),
            )?,
        ],
    )?;

    let window_menu = Submenu::with_items(
        app,
        "Window",
        true,
        &[
            &PredefinedMenuItem::minimize(app, None)?,
            &PredefinedMenuItem::close_window(app, None)?,
        ],
    )?;

    // Troubleshooting lives under Help rather than at the top level because
    // starting and stopping services is what you do when something is wrong,
    // not part of using Lemma.
    let help_menu = Submenu::with_items(
        app,
        "Help",
        true,
        &[
            &MenuItem::with_id(app, "docs", "Lemma Docs", true, None::<&str>)?,
            &PredefinedMenuItem::separator(app)?,
            &MenuItem::with_id(app, "diagnostics", "Diagnostics…", local, None::<&str>)?,
            &MenuItem::with_id(app, "recovery", "Recovery…", true, None::<&str>)?,
            &MenuItem::with_id(app, "logs", "Open Logs", local, None::<&str>)?,
            &PredefinedMenuItem::separator(app)?,
            &MenuItem::with_id(app, "start", "Start Lemma", local, None::<&str>)?,
            &MenuItem::with_id(app, "restart", "Restart Lemma", local, None::<&str>)?,
            &MenuItem::with_id(app, "stop", "Stop Lemma", local, None::<&str>)?,
            &MenuItem::with_id(
                app,
                "stop-all",
                "Stop the local server",
                local,
                None::<&str>,
            )?,
        ],
    )?;

    Menu::with_items(
        app,
        &[
            &lemma_menu,
            &edit_menu,
            &view_menu,
            &window_menu,
            &help_menu,
        ],
    )
}

fn build_tray(app: &AppHandle) -> tauri::Result<()> {
    let menu = build_tray_menu(app)?;
    TrayIconBuilder::with_id("lemma-tray")
        .icon(tauri::include_image!("icons/tray-icon.png"))
        .icon_as_template(false)
        .menu(&menu)
        .show_menu_on_left_click(true)
        // No handler here. `app.on_menu_event` in setup already receives menu
        // events from every menu this app owns, the tray's included, so
        // registering a second one meant every tray verb ran twice -- two
        // confirmation dialogs stacked on each other, two stops, two restarts.
        .build(app)?;
    Ok(())
}

/// Both menus gate their local-only verbs on the connection mode, and both are
/// built during setup — which on a machine's first launch is before the user
/// has chosen one. Everything gated was created disabled and stayed that way
/// for the whole session, so picking Local left "Local settings…" greyed out in
/// the tray and ⌘, dead in the app menu until Lemma was restarted.
///
/// Rebuilding is what makes the choice take effect. It also picks up anything
/// else that reads state at construction, such as the Start at Login check.
fn refresh_menus_for_connection_mode(app: &AppHandle) {
    if let Ok(menu) = build_app_menu(app) {
        let _ = app.set_menu(menu);
    }
    if let (Some(tray), Ok(menu)) = (app.tray_by_id("lemma-tray"), build_tray_menu(app)) {
        let _ = tray.set_menu(Some(menu));
    }
}

fn build_tray_menu(app: &AppHandle) -> tauri::Result<Menu<tauri::Wry>> {
    let local = connection_mode() == "local";
    // A glance and a few verbs. This menu used to carry eighteen items of
    // supervisor vocabulary — starting and stopping named services, switching
    // connection mode — which is a maintainer's console, not the thing you
    // reach for from the menu bar. Everything operational moved into
    // Troubleshoot; everything standard moved into the app menu.
    let status_item =
        MenuItem::with_id(app, "tray-state", "Lemma: checking…", false, None::<&str>)?;
    let open_item = MenuItem::with_id(app, "open", "Open Lemma", true, None::<&str>)?;
    let login_item = MenuItem::with_id(app, "login", "Log In…", true, None::<&str>)?;
    let control_item = MenuItem::with_id(app, "control", "Desktop settings…", true, None::<&str>)?;

    // Disabled: a label, not an action. The tray is where you glance at whether
    // this computer is currently able to run coding agents.
    let agent_host_state_item = MenuItem::with_id(
        app,
        "agent-host-state",
        "Agent Host: checking…",
        false,
        None::<&str>,
    )?;
    {
        let shell: State<Shell> = app.state();
        *shell.tray_agent_host.lock().unwrap() = Some(agent_host_state_item.clone());
        *shell.tray_status.lock().unwrap() = Some(status_item.clone());
    }

    let autostart_enabled = app.autolaunch().is_enabled().unwrap_or(false);
    let troubleshoot = Submenu::with_items(
        app,
        "Troubleshoot",
        true,
        &[
            &MenuItem::with_id(app, "start", "Start Lemma", local, None::<&str>)?,
            &MenuItem::with_id(app, "restart", "Restart Lemma", local, None::<&str>)?,
            &MenuItem::with_id(app, "stop", "Stop Lemma", local, None::<&str>)?,
            &MenuItem::with_id(
                app,
                "stop-all",
                "Stop the local server",
                local,
                None::<&str>,
            )?,
            &PredefinedMenuItem::separator(app)?,
            &MenuItem::with_id(app, "diagnostics", "Diagnostics…", local, None::<&str>)?,
            &MenuItem::with_id(app, "logs", "Open Logs", local, None::<&str>)?,
            &MenuItem::with_id(
                app,
                "agent-host-log",
                "Open Agent Host Log",
                true,
                None::<&str>,
            )?,
            &PredefinedMenuItem::separator(app)?,
            &MenuItem::with_id(app, "reload", "Reload", true, None::<&str>)?,
            &MenuItem::with_id(
                app,
                "devtools",
                "Developer Tools",
                cfg!(debug_assertions),
                None::<&str>,
            )?,
            &PredefinedMenuItem::separator(app)?,
            &MenuItem::with_id(app, "mode", "Connection…", true, None::<&str>)?,
            &CheckMenuItem::with_id(
                app,
                "autostart",
                "Start at Login",
                true,
                autostart_enabled,
                None::<&str>,
            )?,
        ],
    )?;

    let menu = Menu::with_items(
        app,
        &[
            &status_item,
            &PredefinedMenuItem::separator(app)?,
            &open_item,
            &login_item,
            &control_item,
            &PredefinedMenuItem::separator(app)?,
            &agent_host_state_item,
            &PredefinedMenuItem::separator(app)?,
            &troubleshoot,
            &PredefinedMenuItem::separator(app)?,
            &MenuItem::with_id(app, "quit", "Quit Lemma", true, None::<&str>)?,
        ],
    )?;

    Ok(menu)
}

fn disconnect_locald(app: &AppHandle) {
    // Disconnect only this desktop client. The daemon and desired service state
    // survive a crash, an upgrade, and a closed window -- which is the point of
    // closing to the tray. They do *not* survive a quit any more; see
    // `leave_nothing_running`.
    let _ = send_to_locald(app, json!({"cmd": "disconnect", "id": "shell-exit"}));
    let shell: State<Shell> = app.state();
    *shell.locald_writer.lock().unwrap() = None;
}

/// What quitting takes away, in the user's terms, or nothing.
///
/// Quit stops the local server, and the local server is the only thing that
/// runs this installation's schedules, answers for the agents on this computer,
/// and serves any link the user has shared. None of that is on screen, so a
/// silent quit is a silent loss. An empty list means there is genuinely nothing
/// to lose and quitting needs no ceremony.
///
/// Everything here is read from state the shell already holds. The quit path is
/// a keystroke, and a stack too sick to answer a snapshot is exactly the state
/// someone quits from — so it must not depend on the daemon replying.
fn quit_impact(app: &AppHandle) -> Vec<String> {
    // The mode check used to wrap the whole function, so a hosted user was
    // never told anything and quit without a prompt at all. But locald is
    // brought up in hosted mode precisely so the Agent Host can run, and a
    // full quit stops it -- so somebody with a coding agent mid-run lost it
    // silently, while a local user got a careful three-line warning.
    //
    // Only the *stack* line is local-only. The Agent Host runs in both.
    let local = current_mode(app) == "local";
    let shell: State<Shell> = app.state();
    let stack_up = local && {
        let ui = shell.ui.lock().unwrap();
        ui.ready || ui.running || !ui.active_operation_id.is_empty()
    };
    let agent_host = shell.agent_host_status.lock().unwrap().clone();
    let sharing = if local {
        shell.sharing_mode.lock().unwrap().clone()
    } else {
        None
    };
    quit_impact_lines(stack_up, agent_host.as_ref(), sharing.as_deref())
}

fn quit_impact_lines(
    stack_up: bool,
    agent_host: Option<&Value>,
    sharing: Option<&str>,
) -> Vec<String> {
    let mut impact = Vec::new();
    if stack_up {
        impact.push("Schedules and background work stop running.".into());
    }
    if let Some(status) = agent_host {
        if status["running"].as_bool() == Some(true) {
            let paired = status["targets"]
                .as_array()
                .map(|targets| targets.len())
                .unwrap_or(0);
            impact.push(match paired {
                0 => "The agents on this computer stop answering.".into(),
                1 => "The agents on this computer stop answering (1 paired workspace).".into(),
                many => format!(
                    "The agents on this computer stop answering ({many} paired workspaces)."
                ),
            });
        }
    }
    match sharing {
        Some("local_network") => impact.push("Your local network link closes.".into()),
        Some("public") => impact.push("Your public link closes.".into()),
        _ => {}
    }
    impact
}

/// What to call the machine, in native dialog copy.
///
/// The web surfaces decide this at runtime because one bundle serves both
/// platforms; a Rust binary is built for exactly one, so a `cfg!` is the whole
/// answer here. Same words either way -- see `desktop/ui/index.html`.
const THIS_COMPUTER: &str = if cfg!(target_os = "windows") {
    "this PC"
} else if cfg!(target_os = "macos") {
    "this Mac"
} else {
    "this computer"
};

fn quit_prompt_body(impact: &[String]) -> String {
    let mut body = format!("Quitting stops Lemma's local server on {THIS_COMPUTER}.\n\n");
    for line in impact {
        body.push_str("•  ");
        body.push_str(line);
        body.push('\n');
    }
    // Both halves matter. The first is why this is safe to say yes to; the
    // second is the answer for someone who pressed ⌘Q meaning "get out of my
    // way", which closing the window already does without stopping anything.
    body.push_str(&format!(
        "\nPods, files, and data stay on {THIS_COMPUTER} and come back when you reopen \
             Lemma.\nTo leave Lemma running, close the window instead."
    ));
    body
}

/// Quit, having said what that costs.
///
/// One Quit. It used to mean "close the shell and leave the server running",
/// which was both a third state — closing the window already does exactly that,
/// and keeps the tray as a way back — and a quiet inversion: it stopped sharing
/// and the Agent Host, the cheap visible things, while leaving the VM, Postgres
/// and the backend running with no owner on screen at all.
fn request_quit(app: &AppHandle) {
    {
        // Repeated shortcuts must not silently interrupt a migration. A slow
        // shutdown offers its explicit fallback in the app instead.
        let shell: State<Shell> = app.state();
        if shell.quit_confirmed.load(Ordering::Acquire) {
            return;
        }
    }
    let impact = quit_impact(app);
    if impact.is_empty() {
        let handle = app.clone();
        std::thread::spawn(move || stop_then_quit(&handle));
        return;
    }
    let handle = app.clone();
    std::thread::spawn(move || {
        match confirm_destructive_action_impl(
            handle.clone(),
            "Stop Lemma and quit?".into(),
            quit_prompt_body(&impact),
            "Stop and Quit".into(),
        ) {
            Ok(true) => stop_then_quit(&handle),
            Ok(false) => {}
            Err(error) => report_action_failure(&handle, "Quit Lemma", &error),
        }
    });
}

/// Stop everything this installation is running, then exit when it is down.
///
/// `stop_impl` shows the stop on the splash, so a stop that fails fails in
/// front of the user rather than as an app that declines to quit. The exit
/// itself is issued by the `stop`/`done` handler once the daemon confirms.
/// How long a confirmed quit waits for the stop before offering to leave anyway.
///
/// A stop that never confirms -- a wedged VM, a Postgres that will not shut
/// down -- left the app running forever on "Winding down." after the user had
/// asked it to quit. The escape existed (a second Cmd-Q reaches
/// `quit_confirmed` and exits) but nothing on screen said so, and the error
/// screen's button read "Try again", offering to *start* Lemma to somebody who
/// had asked to leave.
///
/// Generous: an ordinary stop is seconds, and the guest is given 20s to power
/// down before it is signalled.
const QUIT_STOP_BUDGET: Duration = Duration::from_secs(45);

fn stop_then_quit(app: &AppHandle) {
    let shell: State<Shell> = app.state();
    shell.quit_confirmed.store(true, Ordering::Release);
    // This worker must persist the live route before shutdown replaces it with
    // the splash. A detached write can be lost when the daemon exits quickly.
    if let Some(route) =
        read_resume_target().and_then(|target| current_workspace_route(app, &target))
    {
        write_resume_route(&route);
    }
    if let Some(control) = app.get_webview("control") {
        let _ = control.close();
    }
    shell.quit_after_stop.store(true, Ordering::Release);
    if shell.locald_writer.lock().unwrap().is_none() {
        match connect_locald() {
            Ok(connection) => install_locald_connection(app, connection),
            Err(_) => {
                finish_quit(app);
                return;
            }
        }
    }
    if let Err(error) = send_local_operation(
        app,
        json!({"cmd": "shutdown-daemon"}),
        operation_id("shell-quit"),
    ) {
        shell.quit_after_stop.store(false, Ordering::Release);
        shell.quit_confirmed.store(false, Ordering::Release);
        // Confirming "Stop and Quit" and then getting neither, silently, is the
        // worst version of this. Say why the quit did not happen; the dialog
        // also tells the user that trying again is the next move.
        report_action_failure(app, "Stop Lemma and quit", &error);
        return;
    }
    show_splash_with_intent(app, "quit");
    // Nothing else bounds this. `quit_after_stop` is consumed only by a `done`
    // event that says the stop succeeded, so any other outcome -- including no
    // outcome -- leaves the app running with the user's quit unanswered.
    let handle = app.clone();
    std::thread::spawn(move || {
        run_quit_watchdog(
            || std::thread::sleep(QUIT_STOP_BUDGET),
            || {
                handle
                    .state::<Shell>()
                    .quit_after_stop
                    .load(Ordering::Acquire)
            },
            || {
                append_install_log(&format!(
                    "quit: the stop did not finish within {}s; offering to quit anyway",
                    QUIT_STOP_BUDGET.as_secs()
                ));
                confirm_destructive_action_impl(
                    handle.clone(),
                    "Lemma is taking longer than usual to stop.".into(),
                    "Lemma is waiting for local work to stop safely. Keep waiting while \
                     a database migration or installation finishes. Quit Anyway may interrupt \
                     that work and require recovery when Lemma next starts."
                        .into(),
                    "Quit Anyway".into(),
                )
                .unwrap_or(false)
            },
            || {
                handle
                    .state::<Shell>()
                    .quit_after_stop
                    .store(true, Ordering::Release);
            },
            || finish_quit(&handle),
        );
    });
}

/// What to tell someone whose update failed after their stack was stopped.
///
/// The install runs with local services deliberately down, so a failure here
/// leaves the machine in a state the user did not ask for and cannot see the
/// cause of. Both halves matter: that the version they had is intact, and
/// whether it is running again. Saying only "could not install the update"
/// left them looking at a settings window over a dead workspace.
fn failed_install_message(install_error: &str, restart_error: Option<String>) -> String {
    match restart_error {
        None => format!(
            "could not install the update: {install_error}. Your previous version is \
             still installed and its services are starting again."
        ),
        Some(restart_error) => format!(
            "could not install the update: {install_error}. Your previous version is \
             still installed, but its services could not be restarted: {restart_error}. \
             Use Recovery to start them."
        ),
    }
}

/// Watch a confirmed quit that is waiting on a stop, and keep offering a way out.
///
/// `quit_after_stop` is consumed only by a `done` event saying the stop
/// succeeded, so any other outcome -- including no outcome -- leaves the app
/// running with the user's quit unanswered. This asks once per budget.
///
/// It loops. Asking once and then, on "Keep waiting", re-arming the flag and
/// returning meant the offer never came back: a stop that never confirmed sat
/// on "Winding down." for ever, and repeating the shortcut was no escape
/// either, because `request_quit` returns early once `quit_confirmed` is set
/// and `ExitRequested` refuses the exit in that state.
///
/// Written over its effects so the cycle can be tested without a 45 second
/// sleep, a window, or a daemon.
fn run_quit_watchdog(
    mut wait: impl FnMut(),
    still_waiting: impl Fn() -> bool,
    ask: impl Fn() -> bool,
    rearm: impl Fn(),
    leave: impl FnOnce(),
) {
    loop {
        wait();
        if !still_waiting() {
            return; // The stop finished and the app is already gone.
        }
        if ask() {
            leave();
            return;
        }
        // They chose to wait, so re-arm: a stop that lands later should still
        // complete the quit they originally asked for.
        rearm();
    }
}

/// Exit without stopping anything, for the cases where there is nothing to stop.
fn finish_quit(app: &AppHandle) {
    let shell: State<Shell> = app.state();
    shell.quit_confirmed.store(true, Ordering::Release);
    let worker = app.clone();
    let exiting = app.clone();
    shell.shutdown.start(
        move || shut_down_gracefully(&worker),
        move || exiting.exit(0),
        QUIT_DAEMON_BUDGET,
    );
}

fn finish_quit_after_daemon(app: &AppHandle) {
    let worker = app.clone();
    let exiting = app.clone();
    app.state::<Shell>().shutdown.start(
        move || {
            // The daemon has already closed sharing and reaped services. Do
            // not send another release transaction while it flushes its reply.
            if wait_for_locald_exit(QUIT_DAEMON_GRACE_ATTEMPTS, "quitting").is_err() {
                leave_nothing_running(&worker);
            }
        },
        move || exiting.exit(0),
        QUIT_DAEMON_BUDGET,
    );
}

/// The watchdog must outlive the verified VM-stop fallback. Exiting the shell
/// earlier kills its cleanup worker and leaves the VM and daemon orphaned.
/// This work runs off the UI thread; the app remains responsive throughout.
const QUIT_DAEMON_BUDGET: Duration = Duration::from_secs(70);
const LOCALD_HANDSHAKE_BUDGET: Duration = Duration::from_secs(3);
const LOCALD_EXIT_POLL: Duration = Duration::from_millis(100);
const QUIT_DAEMON_GRACE_ATTEMPTS: usize = 30;
const LOCALD_FORCE_EXIT_ATTEMPTS: usize = 150;
#[cfg(any(target_os = "macos", test))]
const VM_STOP_GRACE_BUDGET: Duration = Duration::from_secs(25);
#[cfg(any(target_os = "macos", test))]
const VM_STOP_REAP_BUDGET: Duration = Duration::from_secs(5);

/// Quit has to mean quit.
///
/// Closing the window hides Lemma to the tray and everything keeps running --
/// that is deliberate, and it is how a person leaves Lemma working while they
/// do something else. Quitting is the other half of that bargain, and it was
/// not being honoured: the app exited and `lemma-locald` stayed up, supervising
/// Postgres, Redis, the backend, the Agent Host and a virtual machine, with no
/// window, no tray icon and nothing in the Dock. The only way to see it was
/// `ps`, and the only way to stop it was `kill`.
///
/// A background service is a fine thing to have. A background service with no
/// user interface is not one the user agreed to.
///
/// So this stops the daemon on the way out, using the same graceful-then-forced
/// path an app update uses -- the forced arm re-authenticates and matches the
/// packaged executable before it signals anything, so it can never reach a
/// daemon this app does not own.
/// Everything a quit owes the machine, done once and off the main thread.
///
/// Ordered: close any LAN/public exposure first, because that is the part the
/// daemon cannot infer from its own shutdown, then stop the daemon itself.
///
/// Idempotent by flag, not by luck. The confirmed path runs this on a worker
/// and then calls `app.exit(0)`, which lands on `RunEvent::Exit` -- and doing
/// it again there would put the whole wait back on the main thread, which is
/// the thing that made quitting hang.
fn shut_down_gracefully(app: &AppHandle) {
    if current_mode(app) == "local" {
        if let Err(error) = release_before_exit() {
            append_install_log(&format!("[quit] sharing could not be closed: {error}"));
        }
    }
    leave_nothing_running(app);
}

fn leave_nothing_running(app: &AppHandle) {
    // Drop this client first. The daemon broadcasts to connected clients while
    // it shuts down, and a writer belonging to a window that is going away is
    // one more thing that can block the exit.
    disconnect_locald(app);
    let outcome = connect_locald()
        .and_then(|connection| stop_locald(connection, "quitting", QUIT_DAEMON_GRACE_ATTEMPTS));
    match outcome {
        Ok(()) => append_install_log("[quit] the local service manager stopped"),
        // Not fatal, and deliberately not a dialog. The user has asked to
        // leave; trapping them behind a modal about a daemon is worse than the
        // daemon. But it goes in the log, because "Lemma is still running after
        // I quit" is otherwise unexplainable.
        Err(error) => append_install_log(&format!(
            "[quit] the local service manager could not be stopped: {error}"
        )),
    }
}

// An exit that did not stop the stack must still close any LAN or public
// exposure — `finish_quit`, and the second ⌘Q that leaves a wedged stop behind,
// both reach here. The daemon deliberately outlives the app, so it cannot infer
// this from its own shutdown.
fn release_before_exit() -> Result<(), String> {
    let (sender, receiver) = std::sync::mpsc::sync_channel(1);
    std::thread::spawn(move || {
        let _ = sender.send(request_desktop_release());
    });
    receiver
        .recv_timeout(RELEASE_ON_EXIT_TIMEOUT)
        .map_err(|_| "timed out while stopping sharing".to_string())?
}

/// Keep the tray's status line honest about the stack.
///
/// The Agent Host already had a glanceable line; the stack itself did not, so
/// "is Lemma actually up?" meant opening the app to find out. Hosted mode has
/// no local stack to report on and says so instead of inventing a state.
fn refresh_tray_status(app: &AppHandle) {
    let shell: State<Shell> = app.state();
    let label = {
        let ui = shell.ui.lock().unwrap();
        if ui.mode != "local" {
            "Lemma Cloud".to_string()
        } else if ui.error {
            "Lemma: needs attention".to_string()
        } else if ui.ready {
            "Lemma: running".to_string()
        } else if ui.running || !ui.phase.is_empty() {
            "Lemma: starting…".to_string()
        } else {
            "Lemma: stopped".to_string()
        }
    };
    let item = shell.tray_status.lock().unwrap().clone();
    if let Some(item) = item {
        let _ = item.set_text(label);
    }
}

/// Is the workspace a resumed window is showing still the one locald reports?
///
/// Read from the shell's own state rather than by probing, because by this point
/// `ensure_locald` has told us what the daemon actually has. An empty URL means
/// the reconcile has not published one yet, which is not evidence against the
/// window and must not pull a working workspace out from under the user.
fn resume_still_serving(app: &AppHandle, resumed_url: &str) -> bool {
    let shell: State<Shell> = app.state();
    let ui = shell.ui.lock().unwrap();
    ui.url.is_empty() || ui.url == resumed_url
}

/// Persist the route the main window is on, if it is on the workspace at all.
fn remember_workspace_route(app: &AppHandle) {
    let Some(target) = read_resume_target() else {
        return;
    };
    // Reading the route needs the live webview, so that part stays here. The
    // write does not: it syncs the config to disk twice, and on the close path
    // that ran before the window was hidden -- a visible hitch between clicking
    // the red button and the window going away, seconds of it on a busy disk.
    if let Some(route) = current_workspace_route(app, &target) {
        std::thread::spawn(move || write_resume_route(&route));
    }
}

/// The window layer's colour for an appearance.
fn canvas_color(theme: tauri::Theme) -> tauri::window::Color {
    match theme {
        tauri::Theme::Dark => CANVAS_DARK,
        _ => CANVAS_LIGHT,
    }
}

fn request_desktop_release() -> Result<(), String> {
    let mut connection = connect_locald()?;
    let id = format!("desktop-exit-release-{}", std::process::id());
    writeln!(
        connection.writer,
        "{}",
        json!({"v": 1, "cmd": "desktop.release", "id": id})
    )
    .map_err(|error| format!("could not request desktop release: {error}"))?;
    connection
        .writer
        .flush()
        .map_err(|error| format!("could not request desktop release: {error}"))?;

    loop {
        let mut line = String::new();
        let bytes = connection
            .reader
            .read_line(&mut line)
            .map_err(|error| format!("could not confirm desktop release: {error}"))?;
        if bytes == 0 {
            return Err("locald disconnected before confirming desktop release".into());
        }
        if line.len() > 1024 * 1024 {
            return Err("locald desktop release response exceeded 1 MiB".into());
        }
        let Ok(event) = serde_json::from_str::<Value>(line.trim_end()) else {
            continue;
        };
        if event.get("id").and_then(Value::as_str) != Some(id.as_str()) {
            continue;
        }
        match event.get("event").and_then(Value::as_str) {
            Some("done") if event.get("ok").and_then(Value::as_bool) == Some(true) => {
                return Ok(());
            }
            Some("done" | "error") => {
                return Err(event
                    .get("message")
                    .and_then(Value::as_str)
                    .unwrap_or("locald could not stop sharing")
                    .to_string());
            }
            _ => {}
        }
    }
}

fn main() {
    LAUNCH_START.get_or_init(Instant::now);
    let recovery_launch = std::env::args().any(|argument| argument == "--recovery")
        || app_support_dir().join("recovery-mode").is_file();
    let mode = if recovery_launch {
        "undecided".into()
    } else {
        connection_mode()
    };
    launch_trace(&format!("process start, mode={mode}"));

    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, argv, _cwd| {
            for argument in argv {
                if let Ok(url) = tauri::Url::parse(&argument) {
                    handle_deep_link(app, &url);
                }
            }
            if let Some(window) = app.get_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            None,
        ))
        .plugin(tauri_plugin_deep_link::init())
        // Deliberately not `tauri-plugin-process` alongside it. That plugin
        // exists to expose `relaunch` to JavaScript; the flow here is driven
        // from Rust and `AppHandle::restart()` is core, so adding it would
        // widen the ACL for nothing.
        .plugin(
            tauri_plugin_updater::Builder::new()
                .default_version_comparator(|current, update| {
                    update_policy::candidate_allowed(release_channel(), &current, &update.version)
                })
                .build(),
        )
        .manage({
            let shell = Shell::new(mode.clone());
            shell
                .recovery_mode
                .store(recovery_launch, Ordering::Release);
            shell
        })
        .invoke_handler(tauri::generate_handler![
            start,
            stop,
            restart,
            open_app,
            open_logs,
            installer_log,
            diagnostic_logs,
            choose_connection_mode,
            set_connection_mode,
            get_state,
            login,
            open_control_center,
            prepare_runtime,
            runtime_info,
            repair_runtime,
            control_snapshot,
            agent_host_action,
            agent_host_status,
            sandbox_image_status,
            agent_host_start,
            agent_host_pair,
            agent_host_refresh,
            agent_host_open_log,
            apply_operator_config,
            discover_provider_models,
            configure_ai_provider,
            sharing_action,
            close_local_settings,
            confirm_destructive_action,
            confirm_settings_changes,
            resolve_confirmation,
            open_developer_tools,
            local_recovery_options,
            reset_local_data,
            reset_full_reinstall,
            restart_into_recovery,
            check_for_app_update,
            install_app_update
        ])
        .setup(move |app| {
            let handle = app.handle().clone();

            // Before anything else reads a version: an update that did not
            // finish is the reason this launch is on the version it is on.
            reconcile_update_attempt(&handle);

            if !recovery_launch && mode == "hosted" && agent_host_wants_to_run() {
                // "Runs while Lemma is open" has to hold for a cloud workspace
                // too, and locald is what supervises the sidecar. An unpaired
                // or switched-off machine still gets no daemon at all.
                let handle = handle.clone();
                std::thread::spawn(move || {
                    let _ = ensure_locald_without_host_pack(&handle);
                });
            }

            if let Some(capability) = overridden_workspace_capability() {
                // capabilities/workspace.json can only name the shipped origins.
                // A dev or self-hosted build points the workspace somewhere else
                // through these variables, and its Local settings button would
                // otherwise be rejected by an ACL that has never heard of it.
                app.add_capability(capability)?;
            }

            // Optimistic resume. Everything the daemon does on a warm launch is
            // reconciliation of a stack that never stopped, so the splash and
            // the navigation that follows it are pure latency. Ask the recorded
            // workspace whether it is still serving, and if it answers with the
            // generation we left it on, open it directly.
            //
            // Failure is cheap and total: a miss costs RESUME_PROBE_TIMEOUT and
            // lands on exactly the splash path this replaced.
            let resume = (mode == "local")
                .then(read_resume_target)
                .flatten()
                // Parsed before the probe, not after: this comes out of a
                // user-writable config file, and a launch that panicked on a
                // hand-edited route would be a far worse failure than a slow
                // one. An unparseable target simply is not a resume.
                .filter(|target| resume_entry_url(target).parse::<tauri::Url>().is_ok())
                .filter(resume_target_is_serving);
            launch_trace(if mode != "local" {
                "resume: skipped (hosted)"
            } else if resume.is_some() {
                "resume: hit, opening the workspace directly"
            } else {
                "resume: miss, falling back to the splash"
            });

            let initial_url = if mode == "hosted" {
                WebviewUrl::External(hosted_url().parse().expect("valid hosted url"))
            } else if let Some(target) = resume.as_ref() {
                // Parseability was established by the filter above.
                WebviewUrl::External(
                    resume_entry_url(target)
                        .parse()
                        .expect("resume targets are parsed before they are accepted"),
                )
            } else {
                WebviewUrl::App("index.html".into())
            };

            build_main_window(&handle, &mode, initial_url, true)?;
            launch_trace("window shown");

            app.set_menu(build_app_menu(&handle)?)?;
            app.on_menu_event(|app, event| handle_menu_action(app, event.id().as_ref()));

            build_tray(&handle)?;
            refresh_tray_status(&handle);

            // Local mode: connect to the durable daemon immediately so splash
            // has a live event stream the moment it loads.
            if !recovery_launch && mode == "local" {
                if let Some(target) = resume.clone() {
                    // The workspace is already on screen and already answering.
                    // Seed the state the shell would otherwise learn from the
                    // `ready` event — Local settings, the tray, and the
                    // navigation ACL all read `ui.url` — then reconcile with the
                    // daemon on a worker, because nothing below this point is
                    // allowed to hold up a window the user can already see.
                    {
                        let shell: State<Shell> = handle.state();
                        let mut ui = shell.ui.lock().unwrap();
                        ui.url = target.url.clone();
                        ui.api_url = target.api_url.clone();
                        ui.running = true;
                        ui.ready = true;
                    }
                    let handle = handle.clone();
                    let resumed_url = target.url.clone();
                    std::thread::spawn(move || {
                        // The seed above told the shell this workspace was up.
                        // Any path out of here that does not confirm that has
                        // to take it back: the splash reads `ready` on load and
                        // navigates straight to `ui.url` if it is set and there
                        // is no error, so handing it the splash while the state
                        // still claims success just bounces the user back to
                        // the workspace they were rescued from -- against a
                        // port nothing is listening on, in a loop.
                        let stand_down = |failure: Option<String>| {
                            let shell: State<Shell> = handle.state();
                            let snapshot = {
                                let mut ui = shell.ui.lock().unwrap();
                                ui.ready = false;
                                if let Some(error) = failure {
                                    eprintln!("[desktop-resume] {error}");
                                    ui.running = false;
                                    ui.error = true;
                                    ui.error_code = "resume-failed".into();
                                    ui.status = error;
                                }
                                ui.clone()
                            };
                            let _ = handle.emit("lemma:state", snapshot);
                            show_splash(&handle);
                        };
                        if let Err(error) = ensure_locald(&handle) {
                            // The stack is serving but the daemon is not
                            // reachable, so the shell cannot supervise it. Say
                            // so on the splash rather than leaving a workspace
                            // that silently has no controls behind it.
                            stand_down(Some(error));
                            return;
                        }
                        if let Err(error) = start_impl(handle.clone()) {
                            stand_down(Some(error));
                            return;
                        }
                        // Connecting can itself invalidate what the window is
                        // showing: a daemon that does not match this release is
                        // replaced, and everything comes back on new ports. The
                        // `ready` that follows will navigate there, but until it
                        // arrives the window is pointed at a port nothing is
                        // listening on — so hand it the splash, which is what
                        // reports the restart it is waiting for.
                        if !resume_still_serving(&handle, &resumed_url) {
                            // Not a failure: the daemon was replaced and the
                            // stack is coming back on new ports. But `ready`
                            // still points at the old ones, so it has to come
                            // down here too, or the splash re-opens the stale
                            // URL before the real `ready` event arrives.
                            stand_down(None);
                        }
                    });
                } else {
                    // Same rule as the resume branch above: nothing here may
                    // hold up a window the user can already see.
                    //
                    // `ensure_locald` installs the runtime artifacts before it
                    // can spawn anything, which on a first run or an upgrade is
                    // an unpack of hundreds of megabytes, and it then waits up
                    // to LOCALD_START_BUDGET for the daemon to answer. Running
                    // that here ran it inside `setup`, before the event loop
                    // started pumping — so the splash the user was looking at
                    // froze on "Starting Lemma." for the whole install, with no
                    // progress and no way to tell it apart from a hang.
                    let handle = handle.clone();
                    std::thread::spawn(move || {
                        let report = |error: String, code: Option<&str>| {
                            let shell: State<Shell> = handle.state();
                            let snapshot = {
                                let mut ui = shell.ui.lock().unwrap();
                                ui.error = true;
                                ui.status = error;
                                if let Some(code) = code {
                                    ui.error_code = code.into();
                                    ui.ready = false;
                                }
                                ui.clone()
                            };
                            let _ = handle.emit("lemma:state", snapshot);
                        };
                        if let Err(error) = ensure_locald(&handle) {
                            report(error, None);
                            return;
                        }
                        if let Err(error) = start_impl(handle.clone()) {
                            report(error, Some("startup-request-failed"));
                        }
                    });
                }
            }
            if recovery_launch {
                let _ = show_control_center_page(&handle, Some("recovery"));
            } else if std::env::var("LEMMA_DESKTOP_OPEN_CONTROL").as_deref() == Ok("1") {
                let _ = show_control_center(&handle);
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            match event {
                tauri::WindowEvent::CloseRequested { api, .. } => {
                    // Only the window Lemma runs in hides to the tray. This
                    // handler is registered on the builder, so it sees *every*
                    // window: without the guard, closing a pod app window
                    // prevented its own close and hid it, leaving an app the
                    // user could neither see nor get rid of.
                    if window.label() != "main" {
                        return;
                    }
                    // Hide to tray; services keep running. Record where the user
                    // was on the way out — closing the window is the most common
                    // way a session ends, and it is the last chance to read the
                    // route off a live webview.
                    // Hidden first. The route is still readable from a hidden
                    // webview, and the user asked for the window to go away now.
                    api.prevent_close();
                    window.app_handle().state::<Shell>().confirmations.cancel();
                    remove_confirmation_overlay(window.app_handle());
                    let _ = window.hide();
                    remember_workspace_route(window.app_handle());
                    // ...and leave the Dock, which is the half that makes this
                    // read as "closed" rather than "still open but blank".
                    // A hidden window under a live Dock icon is what makes
                    // people reach for Force Quit -- the icon says the app is
                    // running and clicking it appears to do nothing. Docker
                    // Desktop drops to the menu bar here and so do we; the tray
                    // keeps an "Open Lemma" item, so there is still a way back.
                    #[cfg(target_os = "macos")]
                    settle_dock_presence(window.app_handle());
                }
                // A pod app window going away can be the last thing on screen,
                // and closing it is an ordinary close -- so the Dock is settled
                // here too rather than only when the workspace hides.
                #[cfg(target_os = "macos")]
                tauri::WindowEvent::Destroyed => {
                    settle_dock_presence(window.app_handle());
                }
                // Belt and braces for the Dock icon. Every deliberate way back
                // calls `restore_dock_presence`, but a window that has focus
                // and no Dock icon is a state nothing should be able to reach,
                // and this costs one idempotent call to guarantee it.
                #[cfg(target_os = "macos")]
                tauri::WindowEvent::Focused(true) if window.label() == "main" => {
                    restore_dock_presence(window.app_handle());
                }
                _ => {}
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building Lemma desktop")
        .run(|app, event| match event {
            #[cfg(target_os = "macos")]
            tauri::RunEvent::Opened { urls } => {
                for url in urls {
                    handle_deep_link(app, &url);
                }
            }
            // Clicking the Dock icon with every window closed. macOS-only:
            // the variant does not exist on other platforms.
            #[cfg(target_os = "macos")]
            tauri::RunEvent::Reopen { .. } => {
                restore_dock_presence(app);
                if let Some(window) = app.get_window("main") {
                    let _ = window.show();
                    let _ = window.set_focus();
                } else {
                    // Every window closed, which on macOS leaves the app
                    // running. This arm only ever showed a window that already
                    // existed, so in exactly that state clicking the Dock icon
                    // did nothing at all -- the one gesture whose whole purpose
                    // is bringing a running app back, on the one platform where
                    // closing the last window is normal.
                    let snapshot = {
                        let shell: State<Shell> = app.state();
                        let ui = shell.ui.lock().unwrap();
                        ui.clone()
                    };
                    match reopen_target(
                        &snapshot.mode,
                        snapshot.ready,
                        snapshot.error,
                        &snapshot.url,
                    ) {
                        ReopenTarget::Hosted => {
                            let _ = open_app_window(app, &hosted_url());
                        }
                        ReopenTarget::Workspace(url) => {
                            let _ = open_app_window(app, &url);
                        }
                        ReopenTarget::Splash => show_splash(app),
                    }
                }
            }
            // Dock → Quit and any other OS-issued terminate arrive here without
            // passing a menu, so the prompt is armed here rather than only on
            // the items the app draws itself. Fail-safe by construction: an exit
            // is only ever held once, and only when there is something running
            // to say so about.
            tauri::RunEvent::ExitRequested { api, .. } => {
                let shell: State<Shell> = app.state();
                // A server switch closes the window and opens another one. In
                // between there are no windows, which looks exactly like the
                // last one closing -- so hold the exit rather than asking about
                // it or taking it.
                if shell.swapping_window.load(Ordering::Acquire) {
                    api.prevent_exit();
                    return;
                }
                if shell.shutdown.may_exit() {
                    return;
                }
                api.prevent_exit();
                if shell.quit_confirmed.load(Ordering::Acquire) {
                    return;
                }
                if quit_impact(app).is_empty() {
                    // Nothing to warn about, but still something to do: the
                    // daemon outlives the app deliberately, so quitting has to
                    // stop it. Letting the exit through here ran that on the
                    // main thread from `RunEvent::Exit` -- which is why Dock ->
                    // Quit sat "not responding" for several seconds before the
                    // window went away. `finish_quit` does the same work on a
                    // worker and exits when it is done.
                    request_quit(app);
                    return;
                }
                request_quit(app);
            }
            tauri::RunEvent::Exit => {
                // Cleanup belongs to the worker admitted by ExitRequested.
                // At this point the event loop is leaving and must never wait
                // for sockets, process shutdown, or another main-thread task.
                app.state::<Shell>().confirmations.cancel();
            }
            _ => {}
        });
}

#[cfg(test)]
#[cfg(test)]
mod tests;
