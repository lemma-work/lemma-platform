// Lemma desktop shell: thin Tauri client for the durable local daemon.
//
// The shell owns native chrome (window, tray, menus); lemma-locald owns service
// lifecycle. Managed releases use native host packs and private runtime
// providers; the daemon retains an unbundled compatibility adapter only for
// development and existing external-runtime installations.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::Serialize;
use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Read, Seek, SeekFrom, Write};
use std::net::IpAddr;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
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
use tauri_plugin_dialog::{DialogExt as _, MessageDialogButtons, MessageDialogKind};

mod artifact_install;

#[cfg(unix)]
use interprocess::local_socket::GenericFilePath;
#[cfg(windows)]
use interprocess::local_socket::GenericNamespaced;
use interprocess::local_socket::{prelude::*, Name, RecvHalf, SendHalf};

const DEFAULT_HOSTED_URL: &str = "https://lemma.work";
/// Port `cargo tauri dev` serves `frontendDist` on. A packaged build has no
/// equivalent — it serves the same files from `tauri://localhost`.
const DEV_ASSET_PORT: u16 = 1430;
const MAX_INSTALL_LOG_BYTES: u64 = 1024 * 1024;
// Must match locald's handshake revision. This prevents a newly installed
// Desktop hotfix from silently reusing an older durable daemon with the same
// public release number.
const REQUIRED_LOCALD_API_REVISION: u64 = 3;
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
    // Not sent to the splash page, which has no Agent Host UI. The tray reads
    // it to know which way its toggle should point.
    #[serde(skip)]
    agent_host_running: bool,
    #[serde(skip)]
    active_operation_id: String,
    #[serde(skip)]
    completed_operation_ids: Vec<String>,
    #[serde(skip)]
    terminal_recovery_pending: bool,
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
    quit_after_stop: AtomicBool,
    // The tray is built once, so its Agent Host entries are kept here to be
    // rewritten as status arrives.
    tray_agent_host: Mutex<Option<(MenuItem<tauri::Wry>, MenuItem<tauri::Wry>)>>,
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
            quit_after_stop: AtomicBool::new(false),
            tray_agent_host: Mutex::new(None),
            tray_status: Mutex::new(None),
            agent_host_status: Mutex::new(None),
            sharing_mode: Mutex::new(None),
            quit_confirmed: AtomicBool::new(false),
        }
    }
}

fn home_dir() -> PathBuf {
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
        .expect("HOME/USERPROFILE is not set")
}

fn app_support_dir() -> PathBuf {
    if let Some(path) = std::env::var_os("LEMMA_DESKTOP_APP_SUPPORT_DIR") {
        return PathBuf::from(path);
    }
    #[cfg(target_os = "macos")]
    {
        home_dir().join("Library/Application Support/Lemma")
    }
    #[cfg(target_os = "windows")]
    {
        std::env::var_os("LOCALAPPDATA")
            .map(PathBuf::from)
            .unwrap_or_else(home_dir)
            .join("Lemma")
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
    let elapsed = LAUNCH_START
        .get_or_init(Instant::now)
        .elapsed()
        .as_millis();
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
        let pipe_name = format!(r"LOCAL\work.lemma.locald.{:016x}", stable_hash(root));
        pipe_name
            .to_ns_name::<GenericNamespaced>()
            .map(Name::into_owned)
            .map_err(|error| error.to_string())
    }
}

#[cfg(windows)]
fn stable_hash(path: &std::path::Path) -> u64 {
    path.to_string_lossy()
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
    let url = app.get_webview_window("main")?.url().ok()?;
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
        if target.route == "/" { "" } else { &target.route }
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
    response
        .text()
        .is_ok_and(|body| body.contains(generation))
}

fn write_config(update: impl FnOnce(&mut Value)) -> Result<(), String> {
    let mut config = read_config();
    update(&mut config);
    let directory = app_support_dir();
    std::fs::create_dir_all(&directory)
        .map_err(|error| format!("could not create desktop config directory: {error}"))?;
    let serialized = serde_json::to_vec_pretty(&config)
        .map_err(|error| format!("could not encode desktop config: {error}"))?;
    let destination = config_path();
    let temporary = destination.with_extension(format!("json.next-{}", std::process::id()));
    let _ = std::fs::remove_file(&temporary);
    let mut options = std::fs::OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options
        .open(&temporary)
        .map_err(|error| format!("could not stage desktop config: {error}"))?;
    file.write_all(&serialized)
        .and_then(|_| file.sync_all())
        .map_err(|error| format!("could not persist desktop config: {error}"))?;
    replace_config_file(&temporary, &destination)
        .map_err(|error| format!("could not activate desktop config: {error}"))?;
    #[cfg(unix)]
    {
        if let Ok(directory) = std::fs::File::open(directory) {
            let _ = directory.sync_all();
        }
    }
    Ok(())
}

#[cfg(not(windows))]
fn replace_config_file(
    source: &std::path::Path,
    destination: &std::path::Path,
) -> std::io::Result<()> {
    std::fs::rename(source, destination)
}

#[cfg(windows)]
fn replace_config_file(
    source: &std::path::Path,
    destination: &std::path::Path,
) -> std::io::Result<()> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Storage::FileSystem::{
        MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH,
    };

    let source: Vec<u16> = source.as_os_str().encode_wide().chain(Some(0)).collect();
    let destination: Vec<u16> = destination
        .as_os_str()
        .encode_wide()
        .chain(Some(0))
        .collect();
    let result = unsafe {
        MoveFileExW(
            source.as_ptr(),
            destination.as_ptr(),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
    };
    if result == 0 {
        Err(std::io::Error::last_os_error())
    } else {
        Ok(())
    }
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
const SHIPPED_WORKSPACE_ORIGINS: &[&str] = &["http://app.lemma.localhost:*", "https://lemma.work"];

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
        if SHIPPED_WORKSPACE_ORIGINS.contains(&origin.as_str()) {
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

fn bundled_host_pack_root() -> Option<PathBuf> {
    if let Some(root) = std::env::var_os("LEMMA_DESKTOP_HOST_PACK_ROOT") {
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
    if let Some(root) = std::env::var_os("LEMMA_DESKTOP_MANAGED_RUNTIME_ROOT") {
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
    if let Some(path) = std::env::var_os("LEMMA_DESKTOP_RELEASE_MANIFEST") {
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
            && configured
                .as_ref()
                .is_some_and(|runtime| runtime.release == env!("CARGO_PKG_VERSION")),
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

fn ensure_locald(app: &AppHandle) -> Result<(), String> {
    let shell: State<Shell> = app.state();
    if shell.locald_writer.lock().unwrap().is_some() {
        return Ok(());
    }
    let _connect_guard = shell.locald_connect.lock().unwrap();
    if shell.locald_writer.lock().unwrap().is_some() {
        return Ok(());
    }

    ensure_runtime_artifacts(app)?;

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

    spawn_locald()?;
    let mut last_error = "daemon did not create its control endpoint".to_string();
    for _ in 0..80 {
        match connect_locald() {
            Ok(connection) if acceptable(&connection.hello) => {
                install_locald_connection(app, connection);
                return Ok(());
            }
            Ok(_) => last_error = "daemon started with the wrong native host pack".into(),
            Err(error) => last_error = error,
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    Err(format!("could not connect to lemma-locald: {last_error}"))
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
    if shell.locald_writer.lock().unwrap().is_some() {
        return Ok(());
    }
    let _connect_guard = shell.locald_connect.lock().unwrap();
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

    spawn_locald()?;
    let mut last_error = "daemon did not create its control endpoint".to_string();
    for _ in 0..80 {
        match connect_locald() {
            Ok(connection) if locald_is_this_build(&connection.hello, expected.as_deref()) => {
                install_locald_connection(app, connection);
                return Ok(());
            }
            Ok(_) => last_error = "another build's daemon holds the control socket".into(),
            Err(error) => last_error = error,
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    Err(format!("could not connect to lemma-locald: {last_error}"))
}

fn ensure_runtime_artifacts(app: &AppHandle) -> Result<(), String> {
    match ensure_runtime_artifacts_inner(app) {
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

fn ensure_runtime_artifacts_inner(app: &AppHandle) -> Result<(), String> {
    if runtime_root().join("locald/Cargo.toml").is_file() {
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
        runtime.release == env!("CARGO_PKG_VERSION") && runtime.has_recorded_artifact_identity()
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
    if let Some(runtime) = configured_runtime(&config, "installedRuntime") {
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
    stop_locald_for_runtime_maintenance(app).map_err(|error| {
        format!("could not stop the previous local runtime before update: {error}")
    })?;
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
    let installed = artifact_install::install_from_manifest(
        &manifest,
        &runtime_install_root(),
        env!("CARGO_PKG_VERSION"),
        &mut |progress| {
            let fraction = if progress.total == 0 {
                0
            } else {
                progress.current.saturating_mul(1000) / progress.total
            };
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

fn actionable_runtime_install_error(error: &str) -> String {
    if error.contains("artifact download failed with HTTP 404") {
        return format!(
            "The runtime package for Lemma {} is not published yet (HTTP 404). \
             Publish its runtime artifacts, or use the compressed PR test DMG for this exact commit.",
            env!("CARGO_PKG_VERSION")
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
        if runtime_from_config_value(&current).is_some()
            && current.get("release") != next.get("release")
        {
            config["previousRuntime"] = current;
        }
        config["installedRuntime"] = next;
    })
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
            let candidate = runtime_root().join(if cfg!(windows) {
                "locald/target/debug/lemma-locald.exe"
            } else {
                "locald/target/debug/lemma-locald"
            });
            candidate.exists().then_some(candidate)
        })
}

fn spawn_locald() -> Result<(), String> {
    let root = runtime_root();
    let have_checkout = root.join("locald/Cargo.toml").exists();
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
                "locald/Cargo.toml",
                "--",
                "serve",
            ]);
            fallback
        }
    };
    if have_checkout {
        command.current_dir(&root);
    }
    command
        .env("PATH", enriched_path())
        .env("LEMMA_DESKTOP", "1")
        .env("LEMMA_LOCALD_ROOT", locald_root())
        .env("LEMMA_DESKTOP_RUNTIME_ROOT", &root)
        .env(
            "AGENTBOX_PROVIDER",
            std::env::var("AGENTBOX_PROVIDER").unwrap_or_else(|_| "auto".into()),
        )
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
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
        .spawn()
        .map(|_| ())
        .map_err(|e| format!("failed to spawn lemma-locald: {e}"))
}

fn connect_locald() -> Result<LocaldConnection, String> {
    let root = locald_root();
    let token = std::fs::read_to_string(root.join("control.token"))
        .map_err(|error| format!("control token unavailable: {error}"))?;
    let stream = LocalSocketStream::connect(locald_socket_name(&root)?)
        .map_err(|error| format!("control endpoint unavailable: {error}"))?;
    let (receive, mut send) = stream.split();
    writeln!(
        send,
        "{}",
        json!({"v": 1, "cmd": "hello", "token": token.trim(), "client": "desktop"})
    )
    .map_err(|error| format!("daemon authentication failed: {error}"))?;
    send.flush()
        .map_err(|error| format!("daemon authentication failed: {error}"))?;
    let mut reader = BufReader::new(receive);
    let mut line = String::new();
    reader
        .read_line(&mut line)
        .map_err(|error| format!("daemon handshake failed: {error}"))?;
    if line.len() > 1024 * 1024 {
        return Err("daemon handshake exceeded 1 MiB".into());
    }
    let hello: Value = serde_json::from_str(line.trim_end())
        .map_err(|error| format!("invalid daemon handshake: {error}"))?;
    if hello["event"].as_str() != Some("hello") || hello["protocol"].as_u64() != Some(1) {
        return Err("incompatible lemma-locald handshake".into());
    }
    Ok(LocaldConnection {
        reader,
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

fn wait_for_locald_exit(attempts: usize) -> Result<(), String> {
    let root = locald_root();
    let name = locald_socket_name(&root)?;
    for _ in 0..attempts {
        if LocalSocketStream::connect(name.clone()).is_err() {
            return Ok(());
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    Err("the previous local service manager did not stop for the app update".into())
}

fn replace_locald(mut connection: LocaldConnection) -> Result<(), String> {
    let original_pid = connection.hello["pid"]
        .as_u64()
        .ok_or("the previous local service manager did not report its process identity")?;
    request_locald_replacement(&mut connection)?;
    drop(connection);
    if wait_for_locald_exit(450).is_ok() {
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
        return Err(
            "the local service manager changed during the app update; reopen Lemma to retry".into(),
        );
    }
    force_terminate_packaged_locald(original_pid)?;
    wait_for_locald_exit(150)
}

#[cfg(target_os = "macos")]
fn force_terminate_packaged_locald(pid: u64) -> Result<(), String> {
    let pid =
        i32::try_from(pid).map_err(|_| "the local service manager PID is invalid".to_string())?;
    if pid <= 1 || pid == std::process::id() as i32 {
        return Err("refusing to terminate an invalid local service manager process".into());
    }
    stop_packaged_vz_child(pid)?;
    let actual = macos_process_path(pid)?;
    let expected =
        bundled_locald().ok_or("the packaged local service manager executable is missing")?;
    if actual != expected {
        return Err(format!(
            "refusing to stop an unexpected process during update: {}",
            actual.display()
        ));
    }
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
        let deadline = std::time::Instant::now() + Duration::from_secs(25);
        while std::time::Instant::now() < deadline {
            if unsafe { libc::kill(child_pid, 0) } != 0 {
                break;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        if unsafe { libc::kill(child_pid, 0) } == 0 {
            let _ = unsafe { libc::kill(child_pid, libc::SIGKILL) };
            let reap_deadline = std::time::Instant::now() + Duration::from_secs(5);
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
    if let Ok(connection) = connect_locald() {
        replace_locald(connection)?;
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

fn send_local_operation(app: &AppHandle, mut request: Value, id: String) -> Result<(), String> {
    {
        let shell: State<Shell> = app.state();
        let mut ui = shell.ui.lock().unwrap();
        if !ui.active_operation_id.is_empty() {
            return Ok(());
        }
        ui.active_operation_id = id.clone();
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
    if current_mode(app) == "local" {
        show_splash(app);
    }
}

fn emit_log(app: &AppHandle, line: &str) {
    if !line.is_empty() {
        let _ = app.emit("lemma:log", line.to_string());
    }
}

fn handle_locald_event(app: &AppHandle, event: &Value) {
    if std::env::var("LEMMA_DESKTOP_DEBUG").as_deref() == Ok("1") {
        eprintln!("[locald] {event}");
    }
    let shell: State<Shell> = app.state();
    let kind = event["event"].as_str().unwrap_or_default();
    let event_operation_id = event
        .get("operation_id")
        .and_then(Value::as_str)
        .or_else(|| {
            if matches!(kind, "ack" | "done")
                || (kind == "error" && event["code"].as_str() == Some("busy"))
            {
                event.get("id").and_then(Value::as_str)
            } else {
                None
            }
        });
    if let Some(event_operation_id) = event_operation_id {
        let mut ui = shell.ui.lock().unwrap();
        if !ui.active_operation_id.is_empty() && ui.active_operation_id != event_operation_id {
            return;
        }
        if ui.active_operation_id.is_empty() {
            if ui
                .completed_operation_ids
                .iter()
                .any(|completed| completed == event_operation_id)
            {
                return;
            }
            ui.active_operation_id = event_operation_id.to_owned();
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

    let mut start_after_prepare = false;
    let (snapshot, schedule_terminal_recovery) = {
        let mut ui = shell.ui.lock().unwrap();
        match kind {
            "log" => {
                drop(ui);
                emit_log(app, event["line"].as_str().unwrap_or_default());
                return;
            }
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
                if let (Some(url), Some(api_url)) =
                    (event["url"].as_str(), event["api_url"].as_str())
                {
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
                if let (Some(url), Some(api_url)) =
                    (event["url"].as_str(), event["api_url"].as_str())
                {
                    if trusted_workspace_urls(url, api_url) {
                        ui.url = url.to_string();
                        ui.api_url = api_url.to_string();
                        // Record what is serving, and under which generation,
                        // so the next launch can skip straight to it.
                        write_resume_target(
                            url,
                            api_url,
                            event["runtime_generation"].as_str().unwrap_or_default(),
                        );
                    }
                }
                // Stay on the splash: the user proceeds via its CTA.
            }
            "sharing.changed" => {
                if let (Some(url), Some(api_url)) =
                    (event["url"].as_str(), event["api_url"].as_str())
                {
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
                    start_after_prepare = ui.mode == "local";
                } else if reboot_required {
                    ui.error = true;
                    ui.error_code = "wsl-reboot-required".into();
                    ui.status =
                        "Restart Windows to finish setup, then reopen Lemma; setup will continue automatically"
                            .into();
                }
            }
            "done" => {
                if event_operation_id.is_some_and(|id| id == ui.active_operation_id) {
                    let completed_operation_id = ui.active_operation_id.clone();
                    ui.completed_operation_ids.push(completed_operation_id);
                    if ui.completed_operation_ids.len() > 16 {
                        ui.completed_operation_ids.remove(0);
                    }
                    ui.active_operation_id.clear();
                }
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
        (ui.clone(), schedule_terminal_recovery)
    };

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
            let target = read_resume_target()
                .filter(|target| target.url == url)
                .map(|target| resume_entry_url(&target))
                .unwrap_or(url);
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
        && event["cmd"].as_str() == Some("stop")
        && event["ok"].as_bool() == Some(true)
        && shell.quit_after_stop.swap(false, Ordering::AcqRel);
    if quit_after_stop {
        disconnect_locald(app);
        app.exit(0);
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

fn open_app_window(app: &AppHandle, url: &str) -> Result<(), String> {
    let target = tauri::Url::parse(url).map_err(|error| format!("invalid app URL: {error}"))?;
    let window = app
        .get_webview_window("main")
        .ok_or("main window is not available")?;
    window
        .navigate(target)
        .map_err(|error| format!("could not open {url}: {error}"))?;
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
    let Some(url) = app
        .get_webview_window("main")
        .and_then(|window| window.url().ok())
    else {
        return false;
    };
    if native_splash_url(&url) {
        return true;
    }
    !same_origin(&url, workspace)
}

/// Where a bundled page lives for the build we are actually running.
///
/// A packaged app serves its own assets from `tauri://localhost`. Under
/// `cargo tauri dev` there is no such origin: the CLI serves `frontendDist`
/// over a loopback asset server on a fixed port, which is why
/// `trusted_control_url` and `trusted_native_asset_url` below both carry the
/// same development exception. The splash needs it too — navigating to
/// `tauri://localhost/index.html` in a dev build lands on nothing, so the
/// window sits white until the workspace URL replaces it a minute later, and
/// every startup and install stage the splash exists to show is invisible.
fn native_asset_url(path: &str) -> String {
    if cfg!(debug_assertions) {
        format!("http://127.0.0.1:{DEV_ASSET_PORT}/{path}")
    } else {
        format!("tauri://localhost/{path}")
    }
}

fn native_splash_url(url: &tauri::Url) -> bool {
    let path = matches!(url.path(), "/" | "/index.html");
    path && trusted_native_asset_url(url)
}

fn navigate_app_window(app: &AppHandle, url: &str) -> Result<(), String> {
    open_app_window(app, url)
}

fn show_splash(app: &AppHandle) {
    let _ = open_app_window(app, &native_asset_url("index.html"));
}

/// The splash, told what it is watching.
///
/// It reloads as a fresh document every time, so it has no memory of why it was
/// opened — it asks for state and, finding nothing running, assumes it is meant
/// to start Lemma. That is right when the user opened the app and wrong when
/// they asked it to stop: the screen said "Starting Lemma" through a shutdown,
/// and worse, actually issued a start that raced the stop it was showing.
///
/// The intent rides in the query string because `show_splash` navigates, so
/// there is no live page left to tell. `native_splash_url` matches on path, so
/// the splash stays recognised as the splash.
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

fn show_control_center_page(app: &AppHandle, page: Option<&str>) -> Result<(), String> {
    let page = match page.unwrap_or("overview") {
        "connectors" => "integrations",
        "services" => "runtime",
        "surfaces" => "channels",
        page => page,
    };
    if !matches!(
        page,
        "overview"
            | "ai"
            | "sharing"
            | "integrations"
            | "channels"
            | "runtime"
            | "updates"
            | "diagnostics"
    ) {
        return Err(format!("unknown Local settings page: {page}"));
    }
    if let Some(webview) = app.get_webview("control") {
        if let Some(main) = app.get_webview_window("main") {
            let _ = main.show();
            let _ = main.set_focus();
        }
        webview.set_focus().map_err(|error| error.to_string())?;
        let _ = app.emit_to("control", "lemma:control-page", page);
        return Ok(());
    }
    let handle = app.clone();
    let page = page.to_owned();
    // Tauri documents a Windows deadlock when child webviews are created from
    // synchronous commands/event handlers. Always create the trusted overlay
    // from a worker and let add_child marshal the build onto the main thread.
    std::thread::spawn(move || {
        if let Err(error) = create_control_child(&handle, &page) {
            eprintln!("[local-settings] {error}");
            let _ = handle.emit(
                "lemma:control-error",
                format!("Could not open Local settings: {error}"),
            );
        }
    });
    Ok(())
}

fn create_control_child(app: &AppHandle, page: &str) -> Result<(), String> {
    if app.get_webview("control").is_some() {
        let _ = app.emit_to("control", "lemma:control-page", page);
        return Ok(());
    }
    let main = app
        .get_webview_window("main")
        .ok_or("main window is not available")?;
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
    let parent = main.as_ref().window();
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
        .get_webview_window("main")
        .ok_or("main window is not available")?;
    main.open_devtools();
    let _ = main.show();
    let _ = main.set_focus();
    Ok(())
}

#[tauri::command]
fn start(window: Webview, app: AppHandle) -> Result<(), String> {
    require_local_native_window(&window)?;
    start_impl(app)
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
fn stop(window: Webview, app: AppHandle, include_infra: Option<bool>) -> Result<(), String> {
    require_local_native_window(&window)?;
    stop_impl(app, include_infra)
}

fn stop_impl(app: AppHandle, include_infra: Option<bool>) -> Result<(), String> {
    if current_mode(&app) != "local" {
        return Err("local services are not active in Lemma Cloud mode".into());
    }
    show_splash_with_intent(&app, "stop");
    ensure_locald(&app)?;
    send_local_operation(
        &app,
        json!({"cmd": "stop", "infra": include_infra.unwrap_or(false)}),
        operation_id("shell-stop"),
    )
}

#[tauri::command]
fn restart(window: Webview, app: AppHandle) -> Result<(), String> {
    require_local_native_window(&window)?;
    restart_impl(app)
}

fn restart_impl(app: AppHandle) -> Result<(), String> {
    if current_mode(&app) != "local" {
        return Err("local services are not active in Lemma Cloud mode".into());
    }
    show_splash(&app);
    ensure_locald(&app)?;
    send_local_operation(
        &app,
        json!({"cmd": "restart"}),
        operation_id("shell-restart"),
    )
}

#[tauri::command]
fn open_app(app: AppHandle) -> Result<(), String> {
    let target = app_base_url(&app)?;
    open_app_window(&app, &target)
}

#[tauri::command]
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

#[tauri::command]
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

#[tauri::command]
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
    let identity = diagnostic_file_identity(&metadata);
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
fn diagnostic_file_identity(metadata: &std::fs::Metadata) -> String {
    use std::os::unix::fs::MetadataExt;
    format!("{:x}-{:x}", metadata.dev(), metadata.ino())
}

#[cfg(windows)]
fn diagnostic_file_identity(metadata: &std::fs::Metadata) -> String {
    use std::os::windows::fs::MetadataExt;
    format!(
        "{:x}-{:x}",
        metadata.volume_serial_number().unwrap_or_default(),
        metadata.file_index().unwrap_or_default()
    )
}

#[cfg(not(any(unix, windows)))]
fn diagnostic_file_identity(metadata: &std::fs::Metadata) -> String {
    format!("{:x}", metadata.len())
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
    text
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

#[tauri::command]
fn open_control_center(app: AppHandle, page: Option<String>) -> Result<(), String> {
    show_control_center_page(&app, page.as_deref())
}

fn is_control_window_label(label: &str) -> bool {
    label == "control"
}

fn trusted_control_url(url: &tauri::Url) -> bool {
    trusted_native_asset_url(url) && url.path() == "/control.html"
}

fn trusted_native_asset_url(url: &tauri::Url) -> bool {
    let bundled = url.scheme() == "tauri" && matches!(url.host_str(), None | Some("localhost"));
    // WebviewUrl::App is served by Tauri's fixed loopback asset server during
    // `cargo tauri dev`. Keep this narrow exception out of release builds and
    // accept only the exact asset host and port used by the dev runner.
    let development = cfg!(debug_assertions)
        && url.scheme() == "http"
        && matches!(url.host_str(), Some("127.0.0.1" | "localhost"))
        && url.port() == Some(DEV_ASSET_PORT);
    bundled || development
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
fn prepare_runtime(window: Webview, app: AppHandle) -> Result<(), String> {
    require_local_native_window(&window)?;
    if current_mode(&app) != "local" {
        return Err("choose the local workspace before preparing its runtime".into());
    }
    ensure_locald(&app)?;
    send_to_locald(
        &app,
        json!({"cmd":"runtime.prepare", "id":"shell-runtime-prepare"}),
    )
}

#[tauri::command]
fn runtime_info(window: Webview) -> Result<RuntimeInfo, String> {
    require_control_window(&window)?;
    Ok(runtime_info_snapshot())
}

#[tauri::command]
fn repair_runtime(window: Webview, app: AppHandle) -> Result<(), String> {
    require_control_window(&window)?;
    if current_mode(&app) != "local" {
        return Err("runtime repair is available only for a local workspace".into());
    }
    let original_config = read_config();
    let current = configured_runtime(&original_config, "installedRuntime")
        .ok_or("there is no verified downloaded runtime to repair")?;
    if current.release != env!("CARGO_PKG_VERSION") {
        return Err(
            "this retained runtime cannot be repaired with the current signed manifest".into(),
        );
    }
    let original_root = current
        .host_pack_root
        .parent()
        .ok_or("installed runtime has no release root")?
        .to_path_buf();
    stop_locald_for_runtime_maintenance(&app)?;
    emit_runtime_install_progress(
        &app,
        "repair",
        "runtime",
        "Isolating the damaged runtime",
        1,
        None,
        None,
        None,
        None,
    );
    let quarantined = artifact_install::quarantine_runtime(&current)
        .map_err(|error| format!("could not isolate the installed runtime: {error}"))?;

    let repair = ensure_runtime_artifacts(&app)
        .and_then(|_| start_after_runtime_maintenance(&app, "shell-start-after-runtime-repair"));
    if let Err(error) = repair {
        if original_root.exists() {
            let replacement =
                artifact_install::installed_runtime(&original_root, env!("CARGO_PKG_VERSION"));
            if replacement.is_complete() {
                let _ = artifact_install::quarantine_runtime(&replacement);
            } else {
                let failed = original_root
                    .with_file_name(format!(".runtime-repair-failed-{}", std::process::id()));
                let _ = std::fs::rename(&original_root, failed);
            }
        }
        let _ = std::fs::rename(&quarantined, &original_root);
        let _ = write_config(|config| *config = original_config);
        let _ = start_after_runtime_maintenance(&app, "shell-start-after-repair-rollback");
        return Err(format!(
            "runtime repair failed and the prior verified release was restored: {error}"
        ));
    }
    Ok(())
}

#[tauri::command]
fn control_snapshot(window: Webview, app: AppHandle, id: String) -> Result<(), String> {
    require_control_window(&window)?;
    ensure_locald(&app)?;
    send_to_locald(&app, json!({"cmd":"control.snapshot", "id": id}))
}

#[tauri::command]
fn agent_host_action(window: Webview, app: AppHandle, action: String) -> Result<(), String> {
    require_control_window(&window)?;
    if !matches!(action.as_str(), "start" | "stop" | "restart") {
        return Err(format!("unknown Agent Host action {action:?}"));
    }
    ensure_agent_host_daemon(&app)?;
    send_to_locald(
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
fn agent_host_status(window: Webview, app: AppHandle) -> Result<Value, String> {
    require_agent_host_caller(&window, &app)?;
    ensure_agent_host_daemon(&app)?;
    // Ask for a fresh reading, then answer with the newest one already in hand.
    // locald replies on the event stream rather than to this call, so waiting
    // for it here would mean holding a second socket open for every poll.
    let _ = send_to_locald(
        &app,
        json!({"cmd": "agent-host.status", "id": operation_id("agent-host-status")}),
    );
    let shell: State<Shell> = app.state();
    let status = shell.agent_host_status.lock().unwrap().clone();
    Ok(status.unwrap_or(Value::Null))
}

#[tauri::command]
fn agent_host_set_enabled(window: Webview, app: AppHandle, enabled: bool) -> Result<(), String> {
    require_agent_host_caller(&window, &app)?;
    ensure_agent_host_daemon(&app)?;
    let command = if enabled { "start" } else { "stop" };
    send_to_locald(
        &app,
        json!({
            "cmd": format!("agent-host.{command}"),
            "id": operation_id("agent-host"),
        }),
    )
}

#[tauri::command]
fn agent_host_pair(
    window: Webview,
    app: AppHandle,
    url: String,
    pairing_code: String,
    name: String,
) -> Result<(), String> {
    require_agent_host_caller(&window, &app)?;
    if url.trim().is_empty() || pairing_code.trim().is_empty() {
        return Err("pairing needs a workspace URL and a pairing code".into());
    }
    ensure_agent_host_daemon(&app)?;
    send_to_locald(
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

#[tauri::command]
fn agent_host_unpair(
    window: Webview,
    app: AppHandle,
    target_id: Option<String>,
) -> Result<(), String> {
    require_agent_host_caller(&window, &app)?;
    ensure_agent_host_daemon(&app)?;
    send_to_locald(
        &app,
        json!({
            "cmd": "agent-host.unpair",
            "id": operation_id("agent-host-unpair"),
            "target_id": target_id.unwrap_or_default(),
        }),
    )
}

#[tauri::command]
fn agent_host_refresh(window: Webview, app: AppHandle) -> Result<(), String> {
    require_agent_host_caller(&window, &app)?;
    ensure_agent_host_daemon(&app)?;
    send_to_locald(
        &app,
        json!({
            "cmd": "agent-host.refresh",
            "id": operation_id("agent-host-refresh"),
        }),
    )
}

/// Whether this machine has an Agent Host worth supervising, read from the
/// files locald itself uses, so the shell can decide before locald exists.
fn agent_host_wants_to_run() -> bool {
    let root = locald_root();
    let data_dir = root.parent().unwrap_or(&root).join("agent-host");
    if let Ok(raw) = std::fs::read_to_string(data_dir.join("supervisor.json")) {
        if let Ok(preference) = serde_json::from_str::<Value>(&raw) {
            if let Some(enabled) = preference.get("enabled").and_then(Value::as_bool) {
                return enabled;
            }
        }
    }
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

#[tauri::command]
fn agent_host_open_log(window: Webview, app: AppHandle) -> Result<(), String> {
    require_agent_host_caller(&window, &app)?;
    let log = agent_host_log_path();
    if !log.is_file() {
        return Err("the Agent Host has not written a log yet".into());
    }
    reveal_path(&log)
}

/// Flip the Agent Host from the tray, without needing a window open.
fn toggle_agent_host_from_tray(app: &AppHandle) -> Result<(), String> {
    let enabled = {
        let shell: State<Shell> = app.state();
        let ui = shell.ui.lock().unwrap();
        ui.agent_host_running
    };
    ensure_agent_host_daemon(app)?;
    let command = if enabled { "stop" } else { "start" };
    send_to_locald(
        app,
        json!({
            "cmd": format!("agent-host.{command}"),
            "id": operation_id("agent-host-tray"),
        }),
    )
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
) -> String {
    if !available {
        "Agent Host: not installed".into()
    } else if !running {
        "Agent Host: off".into()
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

    let state = agent_host_tray_label(available, running, paired, connected, unreachable);

    {
        let shell: State<Shell> = app.state();
        shell.ui.lock().unwrap().agent_host_running = running;
    }
    let shell: State<Shell> = app.state();
    let items = shell.tray_agent_host.lock().unwrap();
    let Some((state_item, toggle_item)) = items.as_ref() else {
        return;
    };
    let _ = state_item.set_text(state);
    let _ = toggle_item.set_text(if running {
        "Turn Agent Host Off"
    } else {
        "Turn Agent Host On"
    });
    let _ = toggle_item.set_enabled(available);
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
fn apply_operator_config(
    window: Webview,
    app: AppHandle,
    id: String,
    payload: Value,
) -> Result<(), String> {
    require_control_window(&window)?;
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
    let (sender, receiver) = std::sync::mpsc::sync_channel(1);
    std::thread::spawn(move || {
        let _ = sender.send(locald_request_blocking(command));
    });
    receiver
        .recv_timeout(timeout)
        .map_err(|_| "Lemma did not answer in time".to_string())?
}

fn locald_request_blocking(command: Value) -> Result<Value, String> {
    let id = command
        .get("id")
        .and_then(Value::as_str)
        .ok_or("request needs an id")?
        .to_owned();
    let mut connection = connect_locald()?;
    writeln!(connection.writer, "{command}")
        .and_then(|_| connection.writer.flush())
        .map_err(|error| format!("could not reach Lemma: {error}"))?;

    loop {
        let mut line = String::new();
        let bytes = connection
            .reader
            .read_line(&mut line)
            .map_err(|error| format!("could not read Lemma's answer: {error}"))?;
        if bytes == 0 {
            return Err("Lemma closed the connection".into());
        }
        if line.len() > 4 * 1024 * 1024 {
            return Err("Lemma's answer was too large".into());
        }
        let Ok(event) = serde_json::from_str::<Value>(line.trim_end()) else {
            continue;
        };
        if event.get("id").and_then(Value::as_str) != Some(id.as_str()) {
            continue;
        }
        match event.get("event").and_then(Value::as_str) {
            // Acknowledgements say the work started, not that it finished.
            Some("ack") => continue,
            Some("error") => {
                return Err(event
                    .get("message")
                    .and_then(Value::as_str)
                    .unwrap_or("Lemma could not complete that")
                    .to_string())
            }
            Some(_) => return Ok(event),
            None => continue,
        }
    }
}

/// List a candidate provider's models so the page can offer a picker.
///
/// Reachable from the workspace as well as Local settings: onboarding asks the
/// same question, and the alternative was making people type model ids from
/// memory. It reads nothing and writes nothing.
#[tauri::command]
fn discover_provider_models(
    window: Webview,
    app: AppHandle,
    payload: Value,
) -> Result<Value, String> {
    require_agent_host_caller(&window, &app)?;
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
fn configure_ai_provider(
    window: Webview,
    app: AppHandle,
    payload: Value,
) -> Result<Value, String> {
    require_agent_host_caller(&window, &app)?;
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
fn sharing_action(
    window: Webview,
    app: AppHandle,
    action: String,
    id: String,
    payload: Option<Value>,
) -> Result<(), String> {
    require_control_window(&window)?;
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
    if let Some(main) = app.get_webview_window("main") {
        let _ = main.show();
        let _ = main.set_focus();
    }
    Ok(())
}

#[tauri::command]
fn set_connection_mode(window: Webview, app: AppHandle, mode: String) -> Result<(), String> {
    require_local_native_window(&window)?;
    if mode != "local" && mode != "hosted" {
        return Err(format!("unknown mode {mode:?}"));
    }
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
fn choose_connection_mode(app: AppHandle) -> Result<String, String> {
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

fn set_mode(app: &AppHandle, mode: &str) -> Result<(), String> {
    write_config(|config| {
        config["connectionMode"] = json!(mode);
        config["connectionModePromptRevision"] = json!(CONNECTION_MODE_PROMPT_REVISION);
    })?;
    {
        let shell: State<Shell> = app.state();
        let mut ui = shell.ui.lock().unwrap();
        if ui.mode != mode && mode == "local" {
            ui.url.clear();
            ui.api_url.clear();
        }
        ui.mode = mode.to_string();
    }
    Ok(())
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

    if app.host_str() == Some("app.lemma.localhost") {
        let (Some(app_port), Some(api_port)) = (app.port(), api.port()) else {
            return false;
        };
        return app.scheme() == "http"
            && api.scheme() == "http"
            && api.host_str() == Some("app.lemma.localhost")
            && api.path() == "/"
            && app_port >= 49_152
            && api_port >= 49_152
            && app_port != api_port;
    }

    same_origin(&api, app_base)
        && api.path() == "/_lemma/api"
        && matches!(app.scheme(), "http" | "https")
        && (app.scheme() == "https" || local_destination(&app))
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

fn local_destination(url: &tauri::Url) -> bool {
    let Some(host) = url.host_str() else {
        return false;
    };
    let host = host.to_ascii_lowercase();
    if host == "localhost" || host.ends_with(".localhost") {
        return true;
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
        && url
            .host_str()
            .is_some_and(|host| host.ends_with(".apps.lemma.localhost"))
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
        || !local_destination(url)
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
        && (url.scheme() == "tauri"
            || same_origin(url, app_base)
            || same_origin(url, api_base)
            || owned_published_app(url, api_base))
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

fn open_external(url: &str) {
    #[cfg(target_os = "macos")]
    let mut command = Command::new("/usr/bin/open");
    #[cfg(target_os = "windows")]
    let mut command = Command::new("explorer.exe");
    #[cfg(all(unix, not(target_os = "macos")))]
    let mut command = Command::new("xdg-open");
    let _ = command.arg(url).spawn();
}

fn handle_deep_link(app: &AppHandle, url: &tauri::Url) {
    if url.scheme() != "lemma" || url.host_str() != Some("auth") || url.path() != "/complete" {
        return;
    }
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.set_focus();
    }
    // Older builds used a second native auth webview. Hide it if it still
    // exists; the main window now owns the one-time session exchange.
    if let Some(window) = app.get_webview_window("auth") {
        let _ = window.hide();
    }
}

/// The user's System Settings accent, as sRGB bytes.
///
/// `controlAccentColor` is a catalog colour with no component accessors of its
/// own — it has to be resolved into a real colour space before it can be read,
/// which is what the `colorUsingColorSpace` hop is for. Returns `None` if that
/// resolution fails, and callers fall back to systemBlue, the macOS default.
#[cfg(target_os = "macos")]
fn macos_accent_rgb() -> Option<(u8, u8, u8)> {
    use objc2_app_kit::{NSColor, NSColorSpace};

    let accent = NSColor::controlAccentColor();
    let srgb = accent.colorUsingColorSpace(&NSColorSpace::sRGBColorSpace())?;
    let to_byte = |v: f64| (v.clamp(0.0, 1.0) * 255.0).round() as u8;
    Some((
        to_byte(srgb.redComponent()),
        to_byte(srgb.greenComponent()),
        to_byte(srgb.blueComponent()),
    ))
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
                "Lemma keeps running on this Mac and your local pods stay where they are — this window just stops pointing at them. Use this menu item again to come back."
            } else {
                "This window will point at the hosted workspace instead of this Mac. Your local pods stay where they are. Use this menu item again to come back."
            }
            .into(),
            "Use Hosted".into(),
        )
    } else {
        (
            "Run Lemma on this Mac?".into(),
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
    app.dialog()
        .message(body)
        .title(title)
        .kind(MessageDialogKind::Warning)
        .buttons(MessageDialogButtons::OkCancelCustom(
            confirm,
            "Cancel".into(),
        ))
        .show(move |confirmed| {
            if !confirmed {
                return;
            }
            let inner = handle.clone();
            menu_attempt(&inner, "Switch connection", || {
                choose_connection_mode(handle).map(|_| ())
            });
        });
}

/// Whether the Agent Host is running, as the tray last understood it.
fn agent_host_is_running(app: &AppHandle) -> bool {
    let shell: State<Shell> = app.state();
    let ui = shell.ui.lock().unwrap();
    ui.agent_host_running
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
    app.dialog()
        .message(error)
        .title(action)
        .kind(MessageDialogKind::Warning)
        .buttons(MessageDialogButtons::Ok)
        .show(|_| {});
}

/// Run a menu action, and surface whatever it has to say about failing.
fn menu_attempt(app: &AppHandle, action: &str, work: impl FnOnce() -> Result<(), String>) {
    if let Err(error) = work() {
        report_action_failure(app, action, &error);
    }
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
            menu_attempt(&handle, "Open Lemma", || open_app(app).map(|_| ()));
        }
        "login" => {
            tauri::async_runtime::spawn(async move {
                let _ = login(app, Some("signin".into())).await;
            });
        }
        "back" => {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.eval("window.history.back()");
            }
        }
        "forward" => {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.eval("window.history.forward()");
            }
        }
        "reload" => {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.eval("window.location.reload()");
            }
        }
        "start" => {
            let handle = app.clone();
            menu_attempt(&handle, "Start Lemma", || start_impl(app).map(|_| ()));
        }
        "stop" => {
            let handle = app.clone();
            menu_attempt(&handle, "Stop Lemma", || {
                stop_impl(app, Some(false)).map(|_| ())
            });
        }
        "stop-all" => {
            let handle = app.clone();
            menu_attempt(&handle, "Stop Lemma completely", || {
                stop_impl(app, Some(true)).map(|_| ())
            });
        }
        "restart" => {
            let handle = app.clone();
            menu_attempt(&handle, "Restart Lemma", || restart_impl(app).map(|_| ()));
        }
        "mode" => {
            confirm_then_switch_connection(app);
        }
        "autostart" => {
            let autolaunch = app.autolaunch();
            if autolaunch.is_enabled().unwrap_or(false) {
                let _ = autolaunch.disable();
            } else {
                let _ = autolaunch.enable();
            }
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
        "agent-host-toggle" => {
            let action = if agent_host_is_running(&app) {
                "Turn Agent Host off"
            } else {
                "Turn Agent Host on"
            };
            menu_attempt(&app, action, || toggle_agent_host_from_tray(&app));
        }
        "agent-host-log" => {
            let _ = reveal_path(&agent_host_log_path());
        }
        "logs" => {
            let _ = open_logs_impl();
        }
        "docs" => {
            open_external(&format!("{}/docs", hosted_url().trim_end_matches('/')));
        }
        "devtools" => {
            if let Some(window) = app.get_webview_window("main") {
                window.open_devtools();
                let _ = window.show();
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
        Some(
            AboutMetadata {
                name: Some("Lemma".into()),
                version: Some(env!("CARGO_PKG_VERSION").into()),
                website: Some("https://lemma.work".into()),
                ..Default::default()
            },
        ),
    )?;
    let settings = MenuItem::with_id(app, "control", "Settings…", local, Some("CmdOrCtrl+,"))?;
    let connection = MenuItem::with_id(app, "mode", "Connection…", true, None::<&str>)?;

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
            &MenuItem::with_id(
                app,
                "devtools",
                "Developer Tools",
                true,
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
    let local = connection_mode() == "local";
    // A glance and a few verbs. This menu used to carry eighteen items of
    // supervisor vocabulary — starting and stopping named services, switching
    // connection mode — which is a maintainer's console, not the thing you
    // reach for from the menu bar. Everything operational moved into
    // Troubleshoot; everything standard moved into the app menu.
    let status_item = MenuItem::with_id(app, "tray-state", "Lemma: checking…", false, None::<&str>)?;
    let open_item = MenuItem::with_id(app, "open", "Open Lemma", true, None::<&str>)?;
    let login_item = MenuItem::with_id(app, "login", "Log In…", true, None::<&str>)?;
    let control_item = MenuItem::with_id(app, "control", "Local settings…", local, None::<&str>)?;

    // Disabled: a label, not an action. The tray is where you glance at whether
    // this computer is currently able to run coding agents.
    let agent_host_state_item = MenuItem::with_id(
        app,
        "agent-host-state",
        "Agent Host: checking…",
        false,
        None::<&str>,
    )?;
    let agent_host_toggle_item = MenuItem::with_id(
        app,
        "agent-host-toggle",
        "Turn Agent Host On",
        true,
        None::<&str>,
    )?;
    {
        let shell: State<Shell> = app.state();
        *shell.tray_agent_host.lock().unwrap() = Some((
            agent_host_state_item.clone(),
            agent_host_toggle_item.clone(),
        ));
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
            &MenuItem::with_id(app, "stop-all", "Stop the local server", local, None::<&str>)?,
            &PredefinedMenuItem::separator(app)?,
            &MenuItem::with_id(app, "diagnostics", "Diagnostics…", local, None::<&str>)?,
            &MenuItem::with_id(app, "logs", "Open Logs", local, None::<&str>)?,
            &MenuItem::with_id(app, "agent-host-log", "Open Agent Host Log", true, None::<&str>)?,
            &PredefinedMenuItem::separator(app)?,
            &MenuItem::with_id(app, "reload", "Reload", true, None::<&str>)?,
            &MenuItem::with_id(app, "devtools", "Developer Tools", true, None::<&str>)?,
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
            &agent_host_toggle_item,
            &PredefinedMenuItem::separator(app)?,
            &troubleshoot,
            &PredefinedMenuItem::separator(app)?,
            &MenuItem::with_id(app, "quit", "Quit Lemma", true, None::<&str>)?,
        ],
    )?;

    TrayIconBuilder::with_id("lemma-tray")
        .icon(tauri::include_image!("icons/tray-icon.png"))
        .icon_as_template(false)
        .menu(&menu)
        .show_menu_on_left_click(true)
        .on_menu_event(|app, event| handle_menu_action(app, event.id().as_ref()))
        .build(app)?;
    Ok(())
}

fn disconnect_locald(app: &AppHandle) {
    // Disconnect only this desktop client. The daemon and desired service
    // state survive shell exit, upgrades, and crashes.
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
    if current_mode(app) != "local" {
        return Vec::new();
    }
    let shell: State<Shell> = app.state();
    let stack_up = {
        let ui = shell.ui.lock().unwrap();
        ui.ready || ui.running
    };
    let agent_host = shell.agent_host_status.lock().unwrap().clone();
    let sharing = shell.sharing_mode.lock().unwrap().clone();
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

fn quit_prompt_body(impact: &[String]) -> String {
    let mut body = String::from("Quitting stops Lemma's local server on this Mac.\n\n");
    for line in impact {
        body.push_str("•  ");
        body.push_str(line);
        body.push('\n');
    }
    // Both halves matter. The first is why this is safe to say yes to; the
    // second is the answer for someone who pressed ⌘Q meaning "get out of my
    // way", which closing the window already does without stopping anything.
    body.push_str(
        "\nPods, files, and data stay on this Mac and come back when you reopen Lemma.\n\
         To leave Lemma running, close the window instead.",
    );
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
        // A second ⌘Q while a confirmed stop is still running is someone saying
        // "go away now". Honour it instead of asking again: the daemon is
        // durable, so whatever has not finished stopping outlives the shell
        // either way, and this is the escape hatch if a stop ever wedges.
        let shell: State<Shell> = app.state();
        if shell.quit_confirmed.load(Ordering::Acquire) {
            finish_quit(app);
            return;
        }
    }
    let impact = quit_impact(app);
    if impact.is_empty() {
        finish_quit(app);
        return;
    }
    let handle = app.clone();
    app.dialog()
        .message(quit_prompt_body(&impact))
        .title("Stop Lemma and quit?")
        .kind(MessageDialogKind::Warning)
        .buttons(MessageDialogButtons::OkCancelCustom(
            "Stop and Quit".into(),
            "Cancel".into(),
        ))
        .show(move |confirmed| {
            if confirmed {
                stop_then_quit(&handle);
            }
        });
}

/// Stop everything this installation is running, then exit when it is down.
///
/// `stop_impl` shows the stop on the splash, so a stop that fails fails in
/// front of the user rather than as an app that declines to quit. The exit
/// itself is issued by the `stop`/`done` handler once the daemon confirms.
fn stop_then_quit(app: &AppHandle) {
    let shell: State<Shell> = app.state();
    shell.quit_confirmed.store(true, Ordering::Release);
    shell.quit_after_stop.store(true, Ordering::Release);
    if stop_impl(app.clone(), Some(true)).is_err() {
        shell.quit_after_stop.store(false, Ordering::Release);
        shell.quit_confirmed.store(false, Ordering::Release);
    }
}

/// Exit without stopping anything, for the cases where there is nothing to stop.
fn finish_quit(app: &AppHandle) {
    let shell: State<Shell> = app.state();
    shell.quit_confirmed.store(true, Ordering::Release);
    disconnect_locald(app);
    app.exit(0);
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
    if let Some(route) = current_workspace_route(app, &target) {
        write_resume_route(&route);
    }
}

/// Take every window off screen before a blocking shutdown step runs.
///
/// `RunEvent::Exit` is handed to us on the main thread with the webviews
/// already torn down, so anything slow after this point is a frozen window the
/// user is still looking at. Hiding first means the app disappears when the
/// user asked it to and the cleanup finishes out of sight.
fn hide_windows_for_exit(app: &AppHandle) {
    for window in app.webview_windows().values() {
        let _ = window.hide();
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
    let mode = connection_mode();
    launch_trace(&format!("process start, mode={mode}"));

    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, argv, _cwd| {
            for argument in argv {
                if let Ok(url) = tauri::Url::parse(&argument) {
                    handle_deep_link(app, &url);
                }
            }
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            None,
        ))
        .plugin(tauri_plugin_deep_link::init())
        .plugin(tauri_plugin_dialog::init())
        .manage(Shell::new(mode.clone()))
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
            agent_host_set_enabled,
            agent_host_pair,
            agent_host_unpair,
            agent_host_refresh,
            agent_host_open_log,
            apply_operator_config,
            discover_provider_models,
            configure_ai_provider,
            sharing_action,
            close_local_settings,
            open_developer_tools
        ])
        .setup(move |app| {
            let handle = app.handle().clone();

            if connection_mode() == "hosted" && agent_host_wants_to_run() {
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

            let main_builder = WebviewWindowBuilder::new(app, "main", initial_url)
                .title("Lemma")
                .inner_size(1280.0, 860.0)
                .min_inner_size(980.0, 680.0)
                .devtools(true)
                // Corrected to the real appearance immediately after build.
                // Light is the safer guess to start from: a white flash reads
                // as a page loading, a black one reads as a broken app.
                .background_color(CANVAS_LIGHT)
                .initialization_script(desktop_context_script(&mode))
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
                move |event| {
                    if let tauri::WindowEvent::ThemeChanged(theme) = event {
                        let _ = window.set_background_color(Some(canvas_color(*theme)));
                    }
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
                    let _ = main.eval(
                        "document.documentElement.removeAttribute('data-desktop-vibrancy')",
                    );
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
                            let _ = window.eval(&format!(
                                "document.documentElement.style.setProperty('--accent-rgb','{}')",
                                accent_channel_triple(),
                            ));
                        }
                    }
                });
            }

            main.show()?;
            main.set_focus()?;
            launch_trace("window shown");
            if std::env::var("LEMMA_DESKTOP_DEVTOOLS").as_deref() == Ok("1") {
                main.open_devtools();
            }

            app.set_menu(build_app_menu(&handle)?)?;
            app.on_menu_event(|app, event| handle_menu_action(app, event.id().as_ref()));

            build_tray(&handle)?;
            refresh_tray_status(&handle);

            // Local mode: connect to the durable daemon immediately so splash
            // has a live event stream the moment it loads.
            if connection_mode() == "local" {
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
                        if let Err(error) = ensure_locald(&handle) {
                            eprintln!("[desktop-resume] {error}");
                            // The stack is serving but the daemon is not
                            // reachable, so the shell cannot supervise it. Say
                            // so on the splash rather than leaving a workspace
                            // that silently has no controls behind it.
                            show_splash(&handle);
                            return;
                        }
                        if let Err(error) = start_impl(handle.clone()) {
                            eprintln!("[desktop-resume] {error}");
                            show_splash(&handle);
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
                            show_splash(&handle);
                        }
                    });
                } else if let Err(error) = ensure_locald(&handle) {
                    let shell: State<Shell> = handle.state();
                    let snapshot = {
                        let mut ui = shell.ui.lock().unwrap();
                        ui.error = true;
                        ui.status = error;
                        ui.clone()
                    };
                    let _ = handle.emit("lemma:state", snapshot);
                } else if let Err(error) = start_impl(handle.clone()) {
                    let shell: State<Shell> = handle.state();
                    let snapshot = {
                        let mut ui = shell.ui.lock().unwrap();
                        ui.error = true;
                        ui.error_code = "startup-request-failed".into();
                        ui.status = error;
                        ui.ready = false;
                        ui.clone()
                    };
                    let _ = handle.emit("lemma:state", snapshot);
                }
            }
            if std::env::var("LEMMA_DESKTOP_OPEN_CONTROL").as_deref() == Ok("1") {
                let _ = show_control_center(&handle);
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                // Hide to tray; services keep running. Record where the user
                // was on the way out — closing the window is the most common
                // way a session ends, and it is the last chance to read the
                // route off a live webview.
                remember_workspace_route(window.app_handle());
                api.prevent_close();
                let _ = window.hide();
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
            tauri::RunEvent::Reopen { .. } => {
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.show();
                    let _ = window.set_focus();
                }
            }
            // Dock → Quit and any other OS-issued terminate arrive here without
            // passing a menu, so the prompt is armed here rather than only on
            // the items the app draws itself. Fail-safe by construction: an exit
            // is only ever held once, and only when there is something running
            // to say so about.
            tauri::RunEvent::ExitRequested { api, .. } => {
                let shell: State<Shell> = app.state();
                if shell.quit_confirmed.load(Ordering::Acquire) {
                    return;
                }
                if quit_impact(app).is_empty() {
                    shell.quit_confirmed.store(true, Ordering::Release);
                    return;
                }
                api.prevent_exit();
                request_quit(app);
            }
            tauri::RunEvent::Exit => {
                // Read the route before anything is torn down or hidden.
                remember_workspace_route(app);
                // Off screen first: everything below blocks this thread, and a
                // visible window with no live webview behind it renders black.
                hide_windows_for_exit(app);
                if current_mode(app) == "local" {
                    if let Err(error) = release_before_exit() {
                        eprintln!("[desktop-release-exit] {error}");
                    }
                }
                disconnect_locald(app);
            }
            _ => {}
        });
}

#[cfg(test)]
mod tests {
    use super::*;

    fn capability(name: &str) -> Value {
        let raw = match name {
            "main" => include_str!("../capabilities/main.json"),
            "control" => include_str!("../capabilities/control.json"),
            "workspace" => include_str!("../capabilities/workspace.json"),
            other => panic!("unknown capability {other}"),
        };
        serde_json::from_str(raw).expect("capability is valid JSON")
    }

    fn granted(name: &str) -> Vec<String> {
        capability(name)["permissions"]
            .as_array()
            .expect("permissions array")
            .iter()
            .filter_map(|value| value.as_str().map(str::to_string))
            .collect()
    }

    #[test]
    fn every_command_is_granted_to_exactly_the_surfaces_that_call_it() {
        // Declaring an app manifest means an ungranted command is rejected at
        // runtime, from the bundled pages too. Nothing in the build surfaces
        // that - the page just stops working - so pin it here instead.
        let commands = include_str!("../build.rs");
        let all: Vec<String> = commands
            .lines()
            .filter_map(|line| {
                line.trim()
                    .strip_prefix('"')?
                    .strip_suffix("\",")
                    .map(str::to_string)
            })
            .collect();
        assert!(
            all.len() > 15,
            "failed to parse the command list from build.rs"
        );

        let mut all_granted: Vec<String> = Vec::new();
        for name in ["main", "control", "workspace"] {
            all_granted.extend(granted(name));
        }
        for command in &all {
            let permission = format!("allow-{}", command.replace('_', "-"));
            assert!(
                all_granted.contains(&permission),
                "{command} is registered but no capability grants {permission}, so every call to it is rejected",
            );
        }

        // Granted *somewhere* is not enough: a capability names one webview, so
        // a page calling a command only its sibling was granted is still
        // rejected at runtime. Check each bundled page against its own grants.
        for (capability, script) in [
            ("main", include_str!("../ui/index.html")),
            ("control", include_str!("../ui/control.js")),
        ] {
            let grants = granted(capability);
            for command in invoked_commands(script) {
                let permission = format!("allow-{}", command.replace('_', "-"));
                assert!(
                    grants.contains(&permission),
                    "{capability} calls {command} but its capability lacks {permission}",
                );
            }
        }
    }

    /// Every `invoke("name")` a bundled page makes.
    fn invoked_commands(script: &str) -> Vec<String> {
        script
            .split("invoke(\"")
            .skip(1)
            .filter_map(|rest| rest.split('"').next().map(str::to_string))
            .collect()
    }

    #[test]
    fn a_daemon_from_a_replaced_app_bundle_is_not_this_build() {
        let expected = std::path::Path::new("/Applications/Lemma.app/Contents/MacOS/lemma-locald");
        let this_build = json!({
            "daemon_api_revision": REQUIRED_LOCALD_API_REVISION,
            "executable": path_identity(expected),
        });
        assert!(locald_is_this_build(&this_build, Some(expected)));

        // The failure this exists for. Updating the app moves the previous
        // bundle to the Trash; its daemon keeps running and keeps the socket,
        // reporting the same version and the same revision as the one that
        // replaced it.
        let from_the_trash = json!({
            "daemon_api_revision": REQUIRED_LOCALD_API_REVISION,
            "daemon_version": env!("CARGO_PKG_VERSION"),
            "executable": "/Users/someone/.Trash/Lemma 4.23.56 PM.app/Contents/MacOS/lemma-locald",
        });
        assert!(
            !locald_is_this_build(&from_the_trash, Some(expected)),
            "a daemon running from another bundle must be replaced, not adopted",
        );

        // Every build older than the field predates the check, so silence is a
        // mismatch rather than a pass.
        let before_the_field = json!({ "daemon_api_revision": REQUIRED_LOCALD_API_REVISION });
        assert!(!locald_is_this_build(&before_the_field, Some(expected)));

        let wrong_revision = json!({
            "daemon_api_revision": REQUIRED_LOCALD_API_REVISION + 1,
            "executable": path_identity(expected),
        });
        assert!(!locald_is_this_build(&wrong_revision, Some(expected)));

        // A source checkout has no packaged sidecar to be, and its daemon is
        // whatever the developer just built.
        assert!(locald_is_this_build(&before_the_field, None));
    }

    #[test]
    fn switching_connection_says_what_it_is_about_to_do() {
        let (title, body, confirm) = connection_switch_prompt("hosted", false);
        assert_eq!(title, "Run Lemma on this Mac?");
        assert_eq!(confirm, "Start Local");
        // The ninety-second health gate is the whole reason this prompt exists:
        // the press used to be followed by silence for minutes.
        assert!(body.contains("takes a few minutes"), "{body}");

        let (title, body, confirm) = connection_switch_prompt("local", true);
        assert_eq!(title, "Use the hosted workspace?");
        assert_eq!(confirm, "Use Hosted");
        assert!(body.contains("keeps running on this Mac"), "{body}");
        // Leaving must never read as destroying: the pods stay.
        assert!(body.contains("stay where they are"), "{body}");
    }

    #[test]
    fn the_tray_reports_reachability_rather_than_liveness() {
        // Each of these is a live process that cannot take work, and the old
        // process-only status called them all "running".
        assert_eq!(
            agent_host_tray_label(true, true, false, false, false),
            "Agent Host: not paired",
        );
        assert_eq!(
            agent_host_tray_label(true, true, true, false, false),
            "Agent Host: reconnecting…",
        );
        assert_eq!(
            agent_host_tray_label(true, true, true, true, false),
            "Agent Host: connected",
        );
        assert_eq!(
            agent_host_tray_label(true, false, true, false, false),
            "Agent Host: off"
        );
        // A build without the sidecar cannot be switched on, so say so rather
        // than offering a toggle that always fails.
        assert_eq!(
            agent_host_tray_label(false, false, false, false, false),
            "Agent Host: not installed",
        );
        // The state this distinction was added for: a workspace that answered
        // yesterday and is not there today. "Reconnecting" for a week is a
        // promise the retry loop cannot keep.
        assert_eq!(
            agent_host_tray_label(true, true, true, false, true),
            "Agent Host: workspace unreachable",
        );
        // Connected wins over a stale error on another target: one workspace
        // failing does not stop this computer taking work from the other.
        assert_eq!(
            agent_host_tray_label(true, true, true, true, true),
            "Agent Host: connected",
        );
    }

    #[test]
    fn the_workspace_origin_reaches_local_settings_and_nothing_else() {
        // The workspace is a remote origin to Tauri - locald serves it over
        // http, and the hosted build loads lemma.work - so without this
        // capability its Local settings button is silently rejected by the ACL.
        //
        // What it may reach is deliberately short: Local settings, this
        // computer's Agent Host, and the AI provider. The last one was added
        // because onboarding cannot honestly ask "which model?" and then send
        // the user to a different window for the answer — and it is safe to add
        // precisely because `configure_ai_provider` reaches `config.set-ai`,
        // which merges that one section. `allow-apply-operator-config`, which
        // would let the same page rewrite sharing and surfaces, stays out.
        let workspace = granted("workspace");
        assert!(workspace.contains(&"allow-open-control-center".to_string()));
        assert!(workspace.iter().all(|permission| {
            matches!(
                permission.as_str(),
                "allow-open-control-center"
                    | "allow-discover-provider-models"
                    | "allow-configure-ai-provider"
            ) || permission.starts_with("allow-agent-host-")
        }));
        for forbidden in [
            "allow-apply-operator-config",
            "allow-sharing-action",
            "allow-prepare-runtime",
            "allow-repair-runtime",
            "allow-stop",
            "core:default",
        ] {
            assert!(
                !workspace.contains(&forbidden.to_string()),
                "the workspace origin must not be granted {forbidden}",
            );
        }

        let patterns: Vec<tauri::utils::acl::RemoteUrlPattern> = capability("workspace")["remote"]
            ["urls"]
            .as_array()
            .expect("remote urls")
            .iter()
            .map(|value| {
                value
                    .as_str()
                    .expect("url string")
                    .parse()
                    .expect("valid pattern")
            })
            .collect();
        let matches = |raw: &str| {
            let url = tauri::Url::parse(raw).expect("valid url");
            patterns.iter().any(|pattern| pattern.test(&url))
        };

        assert!(matches("http://app.lemma.localhost:3711/pod/abc"));
        assert!(matches("http://app.lemma.localhost:63844/"));
        assert!(matches("https://lemma.work/pod/abc"));

        // Sharing publishes the same workspace on a different host. Those
        // visitors must not be able to drive this Mac's Local settings.
        assert!(!matches("http://192.168.1.24:3711/"));
        assert!(!matches("https://team.trycloudflare.com/"));
        assert!(!matches("https://lemma.work.evil.example/"));
        assert!(!matches("http://lemma.work/"));
    }

    #[test]
    fn local_settings_does_not_inherit_the_splash_commands() {
        // Local settings is a child webview of the main window, and capability
        // matching is (window OR webview). A window-scoped splash capability
        // would therefore hand its commands to Local settings as well.
        for name in ["main", "control", "workspace"] {
            assert!(
                capability(name).get("windows").is_none(),
                "{name} must scope by webview, not window",
            );
        }
        assert_eq!(capability("main")["webviews"][0], "main");
        assert_eq!(capability("control")["webviews"][0], "control");

        let splash_only = [
            "allow-prepare-runtime",
            "allow-choose-connection-mode",
            "allow-login",
        ];
        for permission in splash_only {
            assert!(granted("main").contains(&permission.to_string()));
            assert!(!granted("control").contains(&permission.to_string()));
        }
        assert!(granted("control").contains(&"allow-apply-operator-config".to_string()));
        assert!(!granted("main").contains(&"allow-apply-operator-config".to_string()));
    }

    #[test]
    fn an_overridden_workspace_origin_gets_the_same_commands() {
        let capability_for = |values: &[&str]| {
            workspace_capability_for(values.iter().map(|value| value.to_string()))
        };

        assert!(capability_for(&[]).is_none());
        // A shipped origin is already covered; re-granting it would only widen
        // the pattern set for no reason.
        assert!(capability_for(&["https://lemma.work"]).is_none());
        assert!(capability_for(&["not a url"]).is_none());

        let raw = capability_for(&["https://staging.lemma.work/", "http://127.0.0.1:3711"])
            .expect("an overridden origin produces a capability");
        let capability: Value = serde_json::from_str(&raw).expect("valid capability JSON");
        assert_eq!(
            capability["remote"]["urls"],
            json!(["https://staging.lemma.work", "http://127.0.0.1:3711"]),
        );
        // Read from the shipped file rather than restated here: a hardcoded copy
        // is exactly what drifted, and an assertion that has to be remembered
        // catches nothing.
        let shipped: Value =
            serde_json::from_str(SHIPPED_WORKSPACE_CAPABILITY).expect("valid shipped capability");
        assert_eq!(
            capability["permissions"], shipped["permissions"],
            "an override must reach neither further nor less far than the shipped capability",
        );
        // The one this drift actually cost, named so the regression reads as
        // the symptom it produced.
        assert!(
            capability["permissions"]
                .as_array()
                .expect("permissions are an array")
                .contains(&json!("allow-agent-host-status")),
            "a self-hosted workspace must be able to ask this computer for its status",
        );
        assert_eq!(capability["local"], json!(false));
    }

    #[test]
    fn local_settings_declares_no_palette_of_its_own() {
        // Local settings is a separate webview for a security reason — the
        // privileged commands are granted only to it — but that never justified
        // a second visual identity, which is what this file had: its own gold
        // accent, its own paper, its own display face. Colours now live in one
        // token block at the top and every rule reads from it, so this catches
        // the next raw hex before it becomes a third design system.
        let css = include_str!("../ui/control.css");
        let rules = css
            .split_once("* { box-sizing: border-box; }")
            .expect("control.css starts with a token block then its rules")
            .1;

        let mut stray: Vec<&str> = Vec::new();
        for (index, _) in rules.match_indices('#') {
            let literal: String = rules[index..]
                .chars()
                .take_while(|character| character.is_ascii_hexdigit() || *character == '#')
                .collect();
            // A CSS id selector is not a colour; a colour is 3, 4, 6, or 8
            // hex digits and nothing else.
            if matches!(literal.len(), 4 | 5 | 7 | 9) && literal != "#ffffff" {
                stray.push(&rules[index..index + literal.len()]);
            }
        }
        assert!(
            stray.is_empty(),
            "control.css rules must read colours from the token block, found: {stray:?}"
        );

        // And the tokens themselves must be the product's, not a parallel set.
        assert!(css.contains("--accent-rgb: 90 63 212"), "light accent is the action violet");
        assert!(css.contains("--accent-rgb: 139 122 245"), "dark accent is the action violet");
        assert!(css.contains("--canvas: #f2efe7"), "light canvas is the product's paper");
        assert!(!css.contains("Bricolage"), "the page no longer carries its own display face");
    }

    #[test]
    fn the_menu_bar_speaks_the_products_language() {
        // There was no app menu at all before this: the shipped build used
        // Tauri's default, so there was no Cmd-, and every Lemma verb was in
        // the tray. The tray in turn read as a supervisor console.
        // Scoped to the two menu builders rather than the whole file, because
        // a test that scans its own source matches the very strings it is
        // asserting are gone.
        let source = include_str!("main.rs");
        let menus = {
            let start = source
                .find("fn build_app_menu")
                .expect("the app menu builder exists");
            let end = source.find("fn disconnect_locald").expect("tray builder ends");
            &source[start..end]
        };

        assert!(menus.contains("\"Settings…\", local, Some(\"CmdOrCtrl+,\")"));
        assert!(menus.contains("\"Connection…\""));

        // Quit is one command, and it is the one that stops the local server.
        // There used to be two ways to leave — Quit, which left the VM and the
        // whole stack running with no owner on screen, and a "Stop the local
        // server and quit" buried in Troubleshoot that did what people mean by
        // quitting. Closing the window already covers "go away and keep
        // serving", and keeps the tray as a way back, so the middle state was
        // strictly worse than both neighbours.
        assert!(
            !menus.contains("quit-and-stop") && !menus.contains("Stop the local server and quit"),
            "there is exactly one Quit, and it stops the local server",
        );
        assert!(menus.contains("\"Stop the local server\""));

        // ⌘Q has to reach `request_quit`, which means owning the item. AppKit's
        // predefined quit is `terminate:` and cannot be intercepted, so it would
        // silently skip the prompt and take the stack down — or leave it up —
        // without asking. Matched as a call, because this test reads its own
        // source and the comment above the item names the thing it rules out.
        assert!(
            menus.contains("\"quit\", \"Quit Lemma\", true, Some(\"CmdOrCtrl+Q\")"),
            "Quit is an app-owned item bound to CmdOrCtrl+Q",
        );
        assert!(
            !menus.contains("PredefinedMenuItem::quit("),
            "a predefined quit cannot be asked about first",
        );

        // The operator vocabulary this replaced must not come back.
        for retired in ["Stop Services and Infra", "Switch Connection Mode", "Start Services"] {
            assert!(
                !menus.contains(retired),
                "{retired:?} is supervisor vocabulary, not a product menu item"
            );
        }
    }

    #[test]
    fn a_full_quit_cannot_hold_a_dead_window_on_screen() {
        // release_before_exit runs on the main thread from RunEvent::Exit,
        // after the webviews are torn down, so this timeout is literally how
        // long an unresponsive black window can stay in front of the user. It
        // was two minutes.
        assert!(
            RELEASE_ON_EXIT_TIMEOUT <= Duration::from_secs(5),
            "a quit may not block the main thread for {RELEASE_ON_EXIT_TIMEOUT:?}"
        );
    }

    #[test]
    fn quitting_names_the_work_it_is_about_to_stop() {
        // The whole point of the prompt is that none of this is on screen. A
        // warning that says "are you sure?" and nothing else would be worse than
        // no warning, because it teaches people to dismiss it.
        let running_host = json!({"running": true, "targets": [{"name": "work"}, {"name": "home"}]});
        let lines = quit_impact_lines(true, Some(&running_host), Some("public"));
        assert_eq!(
            lines,
            vec![
                "Schedules and background work stop running.",
                "The agents on this computer stop answering (2 paired workspaces).",
                "Your public link closes.",
            ]
        );

        // Singular reads as English, and an enabled-but-unpaired host still
        // stops answering, so it is still worth one line.
        let unpaired = json!({"running": true, "targets": []});
        assert_eq!(
            quit_impact_lines(false, Some(&unpaired), None),
            vec!["The agents on this computer stop answering."]
        );
        let one = json!({"running": true, "targets": [{"name": "work"}]});
        assert!(quit_impact_lines(false, Some(&one), None)[0].ends_with("(1 paired workspace)."));

        assert_eq!(
            quit_impact_lines(true, None, Some("local_network")),
            vec![
                "Schedules and background work stop running.",
                "Your local network link closes.",
            ]
        );
    }

    #[test]
    fn a_quit_with_nothing_running_asks_nothing() {
        // Every dialog on the way out has to earn itself. A stopped stack with
        // no Agent Host and no shared link costs the user nothing to quit, and
        // being asked anyway is how a prompt becomes noise.
        let idle_host = json!({"running": false, "targets": []});
        assert!(quit_impact_lines(false, Some(&idle_host), Some("this_computer")).is_empty());
        assert!(quit_impact_lines(false, None, None).is_empty());
    }

    #[test]
    fn the_quit_prompt_offers_the_alternative_it_is_replacing() {
        // Someone pressing ⌘Q may mean "get out of my way", which is what
        // closing the window does — and unlike this, it keeps everything
        // serving. The prompt has to say so, or the only discoverable way to
        // keep schedules running is to already know about it.
        let body = quit_prompt_body(&["Schedules and background work stop running.".into()]);
        assert!(body.contains("Schedules and background work stop running."));
        assert!(body.contains("close the window"));
        // And it has to say what is not lost, or "stop" reads as "delete".
        assert!(body.contains("stay on this Mac"));
    }

    #[test]
    fn the_window_layer_is_painted_in_both_appearances() {
        // Whatever the webview is not painting, this is. Neither may be the
        // macOS default of nothing, which composites black.
        assert_eq!(canvas_color(tauri::Theme::Dark), CANVAS_DARK);
        assert_eq!(canvas_color(tauri::Theme::Light), CANVAS_LIGHT);
    }

    #[test]
    fn a_resume_opens_the_recorded_route_rather_than_the_workspace_root() {
        // Opening the root means loading the app once to authenticate and
        // resolve the last pod, then loading it again at the pod it resolved
        // to. The recorded route skips the first of those.
        let target = ResumeTarget {
            url: "http://app.lemma.localhost:49180".into(),
            api_url: "http://app.lemma.localhost:49181".into(),
            generation: "abc123".into(),
            release: env!("CARGO_PKG_VERSION").into(),
            route: "/pod/42/conversations/7".into(),
        };
        assert_eq!(
            resume_entry_url(&target),
            "http://app.lemma.localhost:49180/pod/42/conversations/7"
        );

        // An install that has only ever seen the root still resumes; it just
        // resumes at the root, without a doubled slash.
        let root = ResumeTarget {
            route: "/".into(),
            ..target
        };
        assert_eq!(resume_entry_url(&root), "http://app.lemma.localhost:49180");
    }

    #[test]
    fn a_resume_target_from_another_release_is_refused() {
        // The failure this prevents: installing a new build over an old one,
        // whose stack is often still serving. The probe passes, the window
        // opens the old workspace, and then ensure_locald replaces the
        // mismatched daemon and brings everything back on new ports — leaving
        // the window on a dead port with nothing to navigate it away.
        let saved = |release: &str| {
            json!({
                "url": "http://app.lemma.localhost:49180",
                "apiUrl": "http://app.lemma.localhost:49181",
                "generation": "abc123",
                "release": release,
                "route": "/",
            })
        };
        let accepted = |entry: &Value| {
            entry["release"].as_str() == Some(env!("CARGO_PKG_VERSION"))
                && !entry["generation"].as_str().unwrap_or_default().is_empty()
        };

        assert!(accepted(&saved(env!("CARGO_PKG_VERSION"))));
        assert!(!accepted(&saved("0.6.9")));
        // A target written before releases were recorded has no claim either.
        assert!(!accepted(&json!({
            "url": "http://app.lemma.localhost:49180",
            "apiUrl": "http://app.lemma.localhost:49181",
            "generation": "abc123",
            "route": "/",
        })));
    }

    #[test]
    fn a_ready_event_rescues_a_window_left_on_a_stale_workspace() {
        // Three cases, and the middle one is the bug: the window is showing a
        // workspace, just not this one. The old test was "is it the splash?",
        // which answered no and left it there.
        let splash = tauri::Url::parse(&native_asset_url("index.html")).unwrap();
        let current = tauri::Url::parse("http://app.lemma.localhost:49180/pod/7").unwrap();
        let stale = tauri::Url::parse("http://app.lemma.localhost:57919/pod/7").unwrap();
        let workspace = "http://app.lemma.localhost:49180";

        let needs = |url: &tauri::Url| native_splash_url(url) || !same_origin(url, workspace);

        assert!(needs(&splash), "the ordinary first start");
        assert!(needs(&stale), "a resume whose stack was replaced under it");
        assert!(
            !needs(&current),
            "a stable workspace must never be navigated for a transient event",
        );
    }

    #[test]
    fn a_resume_target_is_only_trusted_with_a_generation_and_workspace_origins() {
        // The generation is what separates "our stack is still serving" from
        // "something is listening on that port". Without it, a resume would
        // hand the window to whatever answered.
        let saved = |generation: &str, url: &str| {
            json!({
                "resumeTarget": {
                    "url": url,
                    "apiUrl": "http://app.lemma.localhost:49181",
                    "generation": generation,
                    "route": "/",
                }
            })
        };
        let parse = |value: Value| -> bool {
            let entry = &value["resumeTarget"];
            let generation = entry["generation"].as_str().unwrap_or_default();
            !generation.is_empty()
                && trusted_workspace_urls(
                    entry["url"].as_str().unwrap_or_default(),
                    entry["apiUrl"].as_str().unwrap_or_default(),
                )
        };

        assert!(parse(saved("abc123", "http://app.lemma.localhost:49180")));
        assert!(!parse(saved("", "http://app.lemma.localhost:49180")));
        assert!(!parse(saved("abc123", "https://evil.example.com")));
    }

    #[test]
    fn privileged_configuration_commands_are_control_window_only() {
        assert!(is_control_window_label("control"));
        assert!(!is_control_window_label("main"));
        assert!(!is_control_window_label("sales.apps.lemma.localhost"));
    }

    #[test]
    fn configured_origins_are_exact() {
        let same = tauri::Url::parse("https://lemma.work/docs").unwrap();
        let subdomain = tauri::Url::parse("https://untrusted.lemma.work/").unwrap();
        let wrong_port = tauri::Url::parse("http://localhost:9999/").unwrap();

        assert!(same_origin(&same, "https://lemma.work"));
        assert!(!same_origin(&subdomain, "https://lemma.work"));
        assert!(!same_origin(&wrong_port, "http://localhost:3711"));
    }

    #[test]
    fn durable_daemon_must_match_the_bundled_host_pack_release() {
        let root = tempfile::tempdir().unwrap();
        let pack = root.path().join("local-runtime");
        std::fs::create_dir_all(&pack).unwrap();
        let current = json!({
            "event": "hello", "protocol": 1, "mode": "host-packs",
            "daemon_api_revision": REQUIRED_LOCALD_API_REVISION,
            "host_pack_release": "1.2.3",
            "host_pack_root": path_identity(&pack),
        });
        let old_same_version = json!({
            "event": "hello", "protocol": 1, "mode": "host-packs",
            "daemon_api_revision": REQUIRED_LOCALD_API_REVISION,
            "host_pack_release": "1.2.3",
        });
        let stale_daemon = json!({
            "event": "hello", "protocol": 1, "mode": "host-packs",
            "host_pack_release": "1.2.3",
            "host_pack_root": path_identity(&pack),
        });
        let compatibility = json!({
            "event": "hello", "protocol": 1, "mode": "compatibility",
        });

        assert!(locald_matches_host_pack(
            &current,
            Some("1.2.3"),
            Some(&pack)
        ));
        assert!(!locald_matches_host_pack(
            &current,
            Some("1.2.4"),
            Some(&pack)
        ));
        assert!(!locald_matches_host_pack(
            &old_same_version,
            Some("1.2.3"),
            Some(&pack)
        ));
        assert!(!locald_matches_host_pack(
            &stale_daemon,
            Some("1.2.3"),
            Some(&pack)
        ));
        assert!(!locald_matches_host_pack(
            &compatibility,
            Some("1.2.3"),
            Some(&pack)
        ));
        assert!(locald_matches_host_pack(&compatibility, None, None));
    }

    #[test]
    fn unpublished_online_runtime_error_is_actionable_and_logged_in_app() {
        let message = actionable_runtime_install_error(
            "could not install local runtime: artifact download failed with HTTP 404",
        );
        assert!(message.contains("not published yet"));
        assert!(message.contains("compressed PR test DMG"));

        let splash = include_str!("../ui/index.html");
        assert!(splash.contains("diagnosticLogs: (source, cursor = null)"));
        assert!(splash.contains("refreshDiagnosticLog"));
        assert!(splash.contains("id=\"log-tabs\""));
        assert!(splash.contains("View log"));
    }

    #[test]
    fn ordinary_web_navigation_stays_in_the_webview() {
        let urls = [
            "https://sales.apps.lemma.work/",
            "https://api.lemma.work/widgets/serve/conversation/tool",
            "http://sales.apps.lemma.localhost:8711/",
            "https://widgets.example.com/report",
        ];

        for raw_url in urls {
            let url = tauri::Url::parse(raw_url).unwrap();
            assert_eq!(
                navigation_disposition(&url, "hosted", "https://lemma.work", ""),
                NavigationDisposition::Allow
            );
        }
    }

    #[test]
    fn local_navigation_accepts_only_locald_owned_loopback_origins() {
        let app_base = "http://app.lemma.localhost:63844";
        let api_base = "http://app.lemma.localhost:63845";
        for raw_url in [
            "http://app.lemma.localhost:63844/auth",
            "http://app.lemma.localhost:63845/files/download",
            "http://sales.apps.lemma.localhost:63845/",
        ] {
            let url = tauri::Url::parse(raw_url).unwrap();
            assert_eq!(
                navigation_disposition(&url, "local", app_base, api_base),
                NavigationDisposition::Allow
            );
        }

        for raw_url in [
            "http://app.lemma.localhost:3710/",
            "http://app.lemma.localhost:3711/verify-email",
            "http://app.lemma.localhost:8710/",
            "http://127.0.0.1:3000/",
            "http://192.168.1.20:8000/",
        ] {
            let url = tauri::Url::parse(raw_url).unwrap();
            assert_eq!(
                navigation_disposition(&url, "local", app_base, api_base),
                NavigationDisposition::Deny
            );
        }
    }

    #[test]
    fn iframes_may_render_their_own_inline_content() {
        let app_base = "http://app.lemma.localhost:63844";
        let api_base = "http://app.lemma.localhost:63845";

        // The document viewer previews HTML and .docx through `srcdoc`, and the
        // navigation delegate is asked about subframes too. Denying these blanked
        // the preview.
        for raw_url in ["about:srcdoc", "about:blank"] {
            let url = tauri::Url::parse(raw_url).unwrap();
            assert_eq!(
                navigation_disposition(&url, "local", app_base, api_base),
                NavigationDisposition::Allow
            );
        }

        // Nothing else off the http/https path comes along for the ride.
        for raw_url in [
            "data:text/html,<h1>x</h1>",
            "file:///etc/passwd",
            "about:settings",
            "javascript:alert(1)",
        ] {
            let url = tauri::Url::parse(raw_url).unwrap();
            assert_eq!(
                navigation_disposition(&url, "local", app_base, api_base),
                NavigationDisposition::Deny
            );
        }
    }

    #[test]
    fn downloads_are_judged_by_the_origin_that_minted_them() {
        let app_base = "http://app.lemma.localhost:63844";
        let api_base = "http://app.lemma.localhost:63845";

        // Object URLs from the workspace itself: every Download button in the app.
        for raw_url in [
            "blob:http://app.lemma.localhost:63844/9f1c-uuid",
            "blob:http://app.lemma.localhost:63845/9f1c-uuid",
            // Bundle export navigates straight to a Content-Disposition endpoint.
            "http://app.lemma.localhost:63845/pods/demo/bundle/download",
        ] {
            let url = tauri::Url::parse(raw_url).unwrap();
            assert!(
                download_disposition(&url, "local", app_base, api_base),
                "{raw_url} should download"
            );
        }

        for raw_url in [
            // A loopback origin this install does not own.
            "blob:http://app.lemma.localhost:3710/9f1c-uuid",
            "http://127.0.0.1:3000/export.csv",
            // Schemes that should never reach the disk by themselves.
            "file:///etc/passwd",
            "data:text/csv,a%2Cb",
            "blob:not-a-url",
        ] {
            let url = tauri::Url::parse(raw_url).unwrap();
            assert!(
                !download_disposition(&url, "local", app_base, api_base),
                "{raw_url} should not download"
            );
        }
    }

    #[test]
    fn local_workspace_origin_requires_isolated_locald_ports_or_a_canonical_gateway() {
        assert!(trusted_workspace_urls(
            "http://app.lemma.localhost:63844",
            "http://app.lemma.localhost:63845"
        ));
        assert!(trusted_workspace_urls(
            "http://192.168.1.20:51324",
            "http://192.168.1.20:51324/_lemma/api"
        ));
        assert!(trusted_workspace_urls(
            "https://lemma-example.ngrok.app",
            "https://lemma-example.ngrok.app/_lemma/api"
        ));
        assert!(!trusted_workspace_urls(
            "http://app.lemma.localhost:3711",
            "http://app.lemma.localhost:8711"
        ));
        assert!(!trusted_workspace_urls(
            "http://app.lemma.localhost:63844",
            "http://app.lemma.localhost:8710"
        ));
    }

    #[test]
    fn desktop_frontend_launcher_has_no_shared_development_origin_fallback() {
        let launcher = include_str!("../runtime/frontend-launcher.mjs");
        assert!(launcher.contains("locald must provide the isolated frontend and API origins"));
        assert!(!launcher.contains("app.lemma.localhost:3711"));
        assert!(!launcher.contains("app.lemma.localhost:8711"));
    }

    #[test]
    fn unsupported_navigation_schemes_are_denied() {
        for raw_url in [
            "file:///tmp/report.html",
            "javascript:alert(1)",
            "lemma://other",
        ] {
            let url = tauri::Url::parse(raw_url).unwrap();
            assert_eq!(
                navigation_disposition(&url, "hosted", "https://lemma.work", ""),
                NavigationDisposition::Deny
            );
        }
    }

    #[test]
    fn explicit_new_windows_keep_the_browser_policy() {
        let app_base = "https://lemma.work";
        let first_party = tauri::Url::parse("https://lemma.work/docs").unwrap();
        let external = tauri::Url::parse("https://widgets.example.com/report").unwrap();
        let blank = tauri::Url::parse("about:blank").unwrap();

        assert_eq!(
            new_window_disposition(&first_party, "hosted", app_base, ""),
            NewWindowDisposition::NavigateInApp
        );
        assert_eq!(
            new_window_disposition(&external, "hosted", app_base, ""),
            NewWindowDisposition::OpenExternal
        );
        assert_eq!(
            new_window_disposition(&blank, "hosted", app_base, ""),
            NewWindowDisposition::Deny
        );
    }

    #[test]
    fn desktop_browser_login_is_explicitly_marked() {
        let desktop = tauri::Url::parse(
            "https://lemma.work/auth?desktop_browser=1&desktop_request=request-1234567890",
        )
        .unwrap();
        let ordinary = tauri::Url::parse("https://lemma.work/auth").unwrap();
        let unrelated = tauri::Url::parse("https://lemma.work/docs?desktop_browser=1").unwrap();

        assert!(is_desktop_browser_auth_url(&desktop));
        assert!(!is_desktop_browser_auth_url(&ordinary));
        assert!(!is_desktop_browser_auth_url(&unrelated));
        assert_eq!(
            navigation_disposition(&desktop, "hosted", "https://lemma.work", ""),
            NavigationDisposition::OpenExternal
        );
        assert_eq!(
            new_window_disposition(&desktop, "hosted", "https://lemma.work", ""),
            NewWindowDisposition::OpenExternal
        );
    }

    #[test]
    fn first_launch_chooser_explains_both_connection_modes() {
        let html = include_str!("../ui/index.html");

        assert!(html.contains("Connect to lemma.work"));
        assert!(html.contains("Run Lemma on this Mac"));
        assert!(html.contains("Cloud and local workspaces do not share data"));
        assert!(html.contains("Install local services"));
        assert!(html.contains("Set up Windows runtime"));
        assert!(html.contains("prepareRuntime: () => invoke(\"prepare_runtime\")"));
        assert!(html.contains("lemma-mark-bar-2"));
        assert!(html.contains("s.phaseKey === \"boot\""));
        assert!(html.contains("!s.error"));
        assert!(html.contains("await window.lemmaDesktop.openApp()"));
        assert!(html.contains("request accepted · keep lemma open"));
        assert!(html.contains("id=\"log-panel\""));
        assert!(html.contains("id=\"operation-status\""));
        assert!(html.contains("downloadedBytes"));
        assert!(html.contains("s.phaseKey === \"stopped\""));
        assert!(!html.contains("!s.running && s.phaseKey"));
        assert!(!html.contains(">Create your account</button>"));
        assert!(!html.contains("Nothing leaves your machine"));
    }

    #[test]
    fn local_settings_exposes_honest_runtime_repair_and_rollback_boundaries() {
        let html = include_str!("../ui/control.html");
        let script = include_str!("../ui/control.js");

        assert!(html.contains("Signed release lifecycle"));
        assert!(script.contains("repair_runtime"));
        assert!(script.contains("open_developer_tools"));
        assert!(html.contains("Developer tools"));
        assert!(html.contains("id=\"network-contract\""));
        assert!(html.contains("id=\"connector-callback\""));
        assert!(script.contains("snapshot.state?.api_url"));
        assert!(!html.contains("http://app.lemma.localhost:8711/api/v1/connectors"));
        assert!(html.contains(
            "Rollback stays unavailable until a release declares its data rollback boundary"
        ));
        assert!(html.contains("Databases, files, and workspaces are preserved"));
    }

    #[test]
    fn cloudflare_sharing_defaults_to_safe_automatic_provisioning() {
        let html = include_str!("../ui/control.html");
        let script = include_str!("../ui/control.js");

        assert!(html.contains("Automatic setup · recommended"));
        assert!(html.contains("Use an existing named tunnel"));
        assert!(
            html.contains("Cloudflare automatic setup stores only its generated tunnel credential")
        );
        assert!(script.contains("payload.cloudflare_setup = $(\"cloudflare-setup\").value"));
        assert!(script.contains("public_warning_confirmed: true"));
        assert!(!script.contains("--overwrite-dns"));
    }

    #[test]
    fn local_models_are_reached_through_a_provider_endpoint_not_an_app_owned_server() {
        let html = include_str!("../ui/control.html");
        let script = include_str!("../ui/control.js");

        // Ollama and LM Studio are the supported local-model path: they are
        // ordinary OpenAI-compatible endpoints the user already runs, so they
        // only prefill the provider form and never give Lemma a model process
        // of its own to install, supervise, or free memory for.
        assert!(html.contains("data-preset=\"ollama\""));
        assert!(html.contains("data-preset=\"lmstudio\""));
        assert!(script.contains("http://127.0.0.1:11434/v1"));
        assert!(script.contains("http://127.0.0.1:1234/v1"));
        assert!(!script.contains("local_ai_action"));
        assert!(!html.contains("data-page=\"models\""));
    }

    #[test]
    fn the_default_model_is_chosen_from_the_providers_own_list() {
        let html = include_str!("../ui/control.html");
        let script = include_str!("../ui/control.js");

        // Typing a model id from memory was the old contract and the reason a
        // correct provider could still be applied with a model it does not
        // serve. The default is now a <select> populated by the probe, and the
        // free-text list it replaced must not come back.
        assert!(html.contains("<select id=\"ai-model\">"));
        assert!(!html.contains("id=\"ai-models\""));
        // The probe answers the press that made it, rather than broadcasting to
        // whichever page happens to be listening.
        assert!(script.contains("const models = await invoke(\"discover_provider_models\""));
        assert!(!script.contains("config.models"));
    }

    #[test]
    fn local_settings_navigation_is_restricted_to_trusted_packaged_and_dev_assets() {
        assert!(control_navigation_allowed(
            &tauri::Url::parse("tauri://localhost/control.html").unwrap()
        ));
        assert!(control_navigation_allowed(
            &tauri::Url::parse("http://127.0.0.1:1430/control.html").unwrap()
        ));
        assert!(!control_navigation_allowed(
            &tauri::Url::parse("http://127.0.0.1:1431/control.html").unwrap()
        ));
        assert!(!control_navigation_allowed(
            &tauri::Url::parse("http://127.0.0.1:1430/index.html").unwrap()
        ));
        assert!(!control_navigation_allowed(
            &tauri::Url::parse("tauri://localhost/index.html").unwrap()
        ));
        assert!(!control_navigation_allowed(
            &tauri::Url::parse("https://example.com/control.html").unwrap()
        ));
    }

    #[test]
    fn ready_auto_navigation_only_treats_the_native_installer_as_splash() {
        assert!(native_splash_url(
            &tauri::Url::parse("tauri://localhost/index.html").unwrap()
        ));
        assert!(native_splash_url(
            &tauri::Url::parse("tauri://localhost/").unwrap()
        ));
        assert!(!native_splash_url(
            &tauri::Url::parse("http://app.lemma.localhost:3711/").unwrap()
        ));
        assert!(!native_splash_url(
            &tauri::Url::parse("tauri://localhost/control.html").unwrap()
        ));
    }

    #[test]
    fn the_splash_is_reachable_in_the_build_we_are_running() {
        // `tauri://localhost` does not exist under `cargo tauri dev` — the CLI
        // serves frontendDist over loopback instead. Navigating there anyway
        // left the window white on "index.html not found" until the workspace
        // URL replaced it a minute later, so every startup stage the splash
        // exists to show went unseen.
        let splash = tauri::Url::parse(&native_asset_url("index.html")).unwrap();
        assert!(
            native_splash_url(&splash),
            "the URL show_splash navigates to must be recognised as the splash: {splash}"
        );
        assert!(
            trusted_native_asset_url(&splash),
            "and must be a trusted native asset origin: {splash}"
        );
        if cfg!(debug_assertions) {
            assert_eq!(splash.port(), Some(DEV_ASSET_PORT));
        } else {
            assert_eq!(splash.scheme(), "tauri");
        }
    }

    #[test]
    fn the_splash_survives_the_navigation_gate_in_local_mode() {
        // Local mode denies local http destinations that are not the workspace,
        // which is what kept a dev build's splash off the screen: it is served
        // over loopback http, so it looked exactly like the thing that rule
        // exists to block.
        let splash = tauri::Url::parse(&native_asset_url("index.html")).unwrap();
        assert_eq!(
            navigation_disposition(
                &splash,
                "local",
                "http://app.lemma.localhost:52501",
                "http://app.lemma.localhost:52502",
            ),
            NavigationDisposition::Allow,
            "the splash must be allowed to load: {splash}"
        );
    }

    #[test]
    fn local_mode_still_denies_a_local_destination_that_is_not_ours() {
        // The allowance above must not become a hole: an arbitrary loopback
        // port is still refused in local mode.
        assert_eq!(
            navigation_disposition(
                &tauri::Url::parse("http://127.0.0.1:9999/").unwrap(),
                "local",
                "http://app.lemma.localhost:52501",
                "http://app.lemma.localhost:52502",
            ),
            NavigationDisposition::Deny
        );
    }

    #[test]
    fn the_dev_asset_origin_is_still_refused_for_anything_but_bundled_pages() {
        // The development exception widens the trusted origin; it must not
        // widen what a *remote* page can reach.
        assert!(!trusted_native_asset_url(
            &tauri::Url::parse("http://127.0.0.1:3000/index.html").unwrap()
        ));
        assert!(!trusted_control_url(
            &tauri::Url::parse(&native_asset_url("index.html")).unwrap()
        ));
        assert!(trusted_control_url(
            &tauri::Url::parse(&native_asset_url("control.html")).unwrap()
        ));
    }

    #[test]
    fn periodic_stopped_status_keeps_an_active_startup_phase_visible() {
        assert!(should_preserve_inflight_phase(
            "shell-start-123",
            "infrastructure-health",
            "stopped"
        ));
        assert!(!should_preserve_inflight_phase(
            "",
            "infrastructure-health",
            "stopped"
        ));
        assert!(!should_preserve_inflight_phase(
            "shell-stop-123",
            "stopped",
            "stopped"
        ));
    }

    #[test]
    fn desktop_config_replacement_never_exposes_a_partial_file() {
        let root = tempfile::tempdir().unwrap();
        let destination = root.path().join("desktop-config.json");
        let source = root.path().join("desktop-config.json.next");
        std::fs::write(&destination, br#"{"revision":1}"#).unwrap();
        std::fs::write(&source, br#"{"revision":2}"#).unwrap();

        replace_config_file(&source, &destination).unwrap();

        assert_eq!(
            std::fs::read_to_string(&destination).unwrap(),
            r#"{"revision":2}"#
        );
        assert!(!source.exists());
    }

    #[test]
    fn macos_allows_only_the_local_http_frontend_and_app_subdomains() {
        let plist = include_str!("../Info.plist");

        assert!(plist.contains("NSAllowsLocalNetworking"));
        assert!(plist.contains("lemma.localhost"));
        assert!(plist.contains("NSIncludesSubdomains"));
        assert!(!plist.contains("NSAllowsArbitraryLoads"));
        assert!(!plist.contains("NSAllowsArbitraryLoadsInWebContent"));
    }

    #[test]
    fn legacy_connection_preferences_require_the_released_chooser_once() {
        assert_eq!(
            configured_connection_mode(&json!({"connectionMode": "hosted"})),
            "undecided"
        );
        assert_eq!(
            configured_connection_mode(&json!({
                "connectionMode": "local",
                "connectionModePromptRevision": CONNECTION_MODE_PROMPT_REVISION,
            })),
            "local"
        );
    }

    #[test]
    fn hosted_auth_uses_browser_handoff_while_local_auth_stays_in_app() {
        assert_eq!(
            desktop_auth_url("https://lemma.work", "signup"),
            "https://lemma.work/auth/desktop?mode=signup"
        );
        assert_eq!(
            local_auth_url("http://app.lemma.localhost:3711/", "signup"),
            "http://app.lemma.localhost:3711/auth?show=signup"
        );
    }

    #[test]
    fn packaged_runtime_root_never_falls_back_to_the_build_checkout() {
        let executable =
            std::path::Path::new("/Applications/Lemma.app/Contents/MacOS/lemma-desktop");
        let checkout = std::path::Path::new("/Users/developer/lemma-platform/desktop");

        assert_eq!(
            default_runtime_root(Some(executable), checkout, false),
            std::path::Path::new("/Applications/Lemma.app/Contents/MacOS")
        );
        assert_eq!(
            default_runtime_root(Some(executable), checkout, true),
            std::path::Path::new("/Users/developer/lemma-platform")
        );
    }

    #[test]
    fn local_desktop_context_disables_email_verification_before_page_scripts() {
        let local = desktop_context_script("local");
        let hosted = desktop_context_script("hosted");

        assert!(local.contains("AUTH_EMAIL_VERIFICATION_REQUIRED: \"false\""));
        assert!(!hosted.contains("AUTH_EMAIL_VERIFICATION_REQUIRED"));
    }

    #[test]
    fn native_material_attributes_survive_navigation() {
        // Local mode navigates the main window from the splash to the
        // workspace. Anything set by a one-shot eval is gone by then, so both
        // attributes have to be written by the initialization script, which
        // Tauri re-runs for every document.
        let script = desktop_context_script("local");

        assert!(script.contains("setAttribute('data-desktop-platform', d.platform)"));
        assert!(script.contains("if (d.vibrancy) root.setAttribute('data-desktop-vibrancy'"));
        assert!(script.contains("if (d.systemAccent) root.style.setProperty('--accent-rgb'"));
    }
}
