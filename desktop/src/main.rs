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
use std::time::Duration;
use tauri::menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::webview::NewWindowResponse;
use tauri::webview::WebviewBuilder;
use tauri::{
    AppHandle, Emitter, Manager, PhysicalPosition, State, Webview, WebviewUrl, WebviewWindowBuilder,
};
use tauri_plugin_autostart::ManagerExt as _;

mod artifact_install;

#[cfg(unix)]
use interprocess::local_socket::GenericFilePath;
#[cfg(windows)]
use interprocess::local_socket::GenericNamespaced;
use interprocess::local_socket::{prelude::*, Name, RecvHalf, SendHalf};

const DEFAULT_HOSTED_URL: &str = "https://lemma.work";
const MAX_INSTALL_LOG_BYTES: u64 = 1024 * 1024;
// Must match locald's handshake revision. This prevents a newly installed
// Desktop hotfix from silently reusing an older durable daemon with the same
// public release number.
const REQUIRED_LOCALD_API_REVISION: u64 = 3;
// Legacy development builds persisted a mode before the released chooser
// contract was stable. Require that chooser once, then retain the new choice.
const CONNECTION_MODE_PROMPT_REVISION: u64 = 1;

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

fn operation_id(prefix: &str) -> String {
    let nonce = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    format!("{prefix}-{}-{nonce}", std::process::id())
}

fn append_install_log(message: &str) {
    let path = install_log_path();
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
        let _ = std::fs::rename(&path, previous);
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
    if let Ok(connection) = connect_locald() {
        if locald_matches_host_pack(
            &connection.hello,
            required_release.as_deref(),
            required_root.as_deref(),
        ) {
            install_locald_connection(app, connection);
            return Ok(());
        }
        replace_locald(connection)?;
    }

    spawn_locald()?;
    let mut last_error = "daemon did not create its control endpoint".to_string();
    for _ in 0..80 {
        match connect_locald() {
            Ok(connection)
                if locald_matches_host_pack(
                    &connection.hello,
                    required_release.as_deref(),
                    required_root.as_deref(),
                ) =>
            {
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

fn spawn_locald() -> Result<(), String> {
    let root = runtime_root();
    let have_checkout = root.join("locald/Cargo.toml").exists();
    let locald_bin = std::env::var("LEMMA_DESKTOP_LOCALD_BIN")
        .ok()
        .map(PathBuf::from)
        .filter(|p| p.exists())
        .or_else(bundled_locald)
        .or_else(|| {
            let candidate = root.join(if cfg!(windows) {
                "locald/target/debug/lemma-locald.exe"
            } else {
                "locald/target/debug/lemma-locald"
            });
            candidate.exists().then_some(candidate)
        });

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
    if let Some(url) = ready_workspace_url {
        if main_window_showing_native_splash(app) {
            let _ = open_app_window(app, &url);
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

fn main_window_showing_native_splash(app: &AppHandle) -> bool {
    app.get_webview_window("main")
        .and_then(|window| window.url().ok())
        .is_some_and(|url| native_splash_url(&url))
}

fn native_splash_url(url: &tauri::Url) -> bool {
    url.scheme() == "tauri" && matches!(url.path(), "/" | "/index.html")
}

fn navigate_app_window(app: &AppHandle, url: &str) -> Result<(), String> {
    open_app_window(app, url)
}

fn show_splash(app: &AppHandle) {
    let _ = open_app_window(app, "tauri://localhost/index.html");
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
        "local-ai" => "models",
        page => page,
    };
    if !matches!(
        page,
        "overview"
            | "models"
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
    show_splash(&app);
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
    let logs = locald_root();
    #[cfg(target_os = "macos")]
    let opener = "/usr/bin/open";
    #[cfg(target_os = "windows")]
    let opener = "explorer.exe";
    #[cfg(all(unix, not(target_os = "macos")))]
    let opener = "xdg-open";
    Command::new(opener)
        .arg(&logs)
        .spawn()
        .map_err(|e| format!("could not open {}: {e}", logs.display()))?;
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
        ("local-ai", "Local AI (MLX)", root.join("logs/local-ai.log")),
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
    let bundled = url.scheme() == "tauri"
        && matches!(url.host_str(), None | Some("localhost"))
        && url.path() == "/control.html";
    // WebviewUrl::App is served by Tauri's fixed loopback asset server during
    // `cargo tauri dev`. Keep this narrow exception out of release builds and
    // accept only the exact asset host, port, and path used by the dev runner.
    let development = cfg!(debug_assertions)
        && url.scheme() == "http"
        && matches!(url.host_str(), Some("127.0.0.1" | "localhost"))
        && url.port() == Some(1430)
        && url.path() == "/control.html";
    bundled || development
}

fn trusted_native_asset_url(url: &tauri::Url) -> bool {
    let bundled = url.scheme() == "tauri" && matches!(url.host_str(), None | Some("localhost"));
    let development = cfg!(debug_assertions)
        && url.scheme() == "http"
        && matches!(url.host_str(), Some("127.0.0.1" | "localhost"))
        && url.port() == Some(1430);
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
    ensure_locald(&app)?;
    send_to_locald(
        &app,
        json!({
            "cmd": format!("agent-host.{action}"),
            "id": operation_id("agent-host"),
        }),
    )
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

#[tauri::command]
fn local_ai_action(
    window: Webview,
    app: AppHandle,
    action: String,
    model_id: String,
    id: String,
) -> Result<(), String> {
    require_control_window(&window)?;
    if current_mode(&app) != "local" {
        return Err("local AI is available only for a local workspace".into());
    }
    if !matches!(action.as_str(), "install" | "start" | "stop" | "delete") {
        return Err(format!("unknown local AI action: {action}"));
    }
    if model_id.trim().is_empty() {
        return Err("choose a local AI model first".into());
    }
    ensure_locald(&app)?;
    send_to_locald(
        &app,
        json!({
            "cmd": format!("local-ai.{action}"),
            "id": id,
            "model_id": model_id,
        }),
    )
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

fn navigation_disposition(
    url: &tauri::Url,
    mode: &str,
    app_base: &str,
    api_base: &str,
) -> NavigationDisposition {
    if is_desktop_browser_auth_url(url) {
        NavigationDisposition::OpenExternal
    } else if url.scheme() == "tauri" {
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

fn desktop_context_script(mode: &str) -> String {
    let context = json!({
        "version": env!("CARGO_PKG_VERSION"),
        "mode": mode,
    });
    let local_auth = if mode == "local" {
        // NEXT_PUBLIC values are also rendered into the native host-pack
        // environment. Inject the local auth policy before any page script as
        // a cache-independent guard for an already-open desktop webview.
        "window.__LEMMA_AUTH_CONFIG__ = Object.freeze({AUTH_EMAIL_VERIFICATION_REQUIRED: \"false\"});"
    } else {
        ""
    };
    format!(
        "window.__LEMMA_DESKTOP__ = Object.freeze({});{}",
        serde_json::to_string(&context).unwrap_or_else(|_| "{}".into()),
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

fn build_tray(app: &AppHandle) -> tauri::Result<()> {
    let open_item = MenuItem::with_id(app, "open", "Open Lemma", true, None::<&str>)?;
    let login_item = MenuItem::with_id(app, "login", "Log In…", true, None::<&str>)?;
    let home_item = MenuItem::with_id(app, "home", "Lemma Home", true, None::<&str>)?;
    let back_item = MenuItem::with_id(app, "back", "Back", true, None::<&str>)?;
    let reload_item = MenuItem::with_id(app, "reload", "Reload", true, None::<&str>)?;
    let start_item = MenuItem::with_id(app, "start", "Start Services", true, None::<&str>)?;
    let stop_item = MenuItem::with_id(app, "stop", "Stop Services", true, None::<&str>)?;
    let stop_all_item = MenuItem::with_id(
        app,
        "stop-all",
        "Stop Services and Infra",
        true,
        None::<&str>,
    )?;
    let restart_item = MenuItem::with_id(app, "restart", "Restart Services", true, None::<&str>)?;
    let mode_item = MenuItem::with_id(app, "mode", "Switch Connection Mode", true, None::<&str>)?;
    let autostart_enabled = app.autolaunch().is_enabled().unwrap_or(false);
    let autostart_item = CheckMenuItem::with_id(
        app,
        "autostart",
        "Start at Login",
        true,
        autostart_enabled,
        None::<&str>,
    )?;
    let logs_item = MenuItem::with_id(app, "logs", "Open Logs", true, None::<&str>)?;
    let devtools_item = MenuItem::with_id(
        app,
        "devtools",
        "Developer Tools",
        true,
        Some("CmdOrCtrl+Alt+I"),
    )?;
    let control_item = MenuItem::with_id(app, "control", "Local settings…", true, None::<&str>)?;
    let quit_item = MenuItem::with_id(app, "quit", "Quit Lemma", true, None::<&str>)?;
    let quit_and_stop_item = MenuItem::with_id(
        app,
        "quit-and-stop",
        "Quit and stop Lemma",
        true,
        None::<&str>,
    )?;
    let menu = Menu::with_items(
        app,
        &[
            &open_item,
            &login_item,
            &home_item,
            &back_item,
            &reload_item,
            &PredefinedMenuItem::separator(app)?,
            &start_item,
            &stop_item,
            &stop_all_item,
            &restart_item,
            &PredefinedMenuItem::separator(app)?,
            &mode_item,
            &autostart_item,
            &control_item,
            &logs_item,
            &devtools_item,
            &PredefinedMenuItem::separator(app)?,
            &quit_item,
            &quit_and_stop_item,
        ],
    )?;

    TrayIconBuilder::with_id("lemma-tray")
        .icon(tauri::include_image!("icons/tray-icon.png"))
        .icon_as_template(false)
        .menu(&menu)
        .show_menu_on_left_click(true)
        .on_menu_event(|app, event| {
            let app = app.clone();
            match event.id().as_ref() {
                "open" => {
                    let _ = open_app(app);
                }
                "login" => {
                    tauri::async_runtime::spawn(async move {
                        let _ = login(app, Some("signin".into())).await;
                    });
                }
                "home" => {
                    let _ = open_app(app);
                }
                "back" => {
                    if let Some(window) = app.get_webview_window("main") {
                        let _ = window.eval("window.history.back()");
                    }
                }
                "reload" => {
                    if let Some(window) = app.get_webview_window("main") {
                        let _ = window.eval("window.location.reload()");
                    }
                }
                "start" => {
                    let _ = start_impl(app);
                }
                "stop" => {
                    let _ = stop_impl(app, Some(false));
                }
                "stop-all" => {
                    let _ = stop_impl(app, Some(true));
                }
                "restart" => {
                    let _ = restart_impl(app);
                }
                "mode" => {
                    let _ = choose_connection_mode(app);
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
                    let _ = show_control_center(&app);
                }
                "logs" => {
                    let _ = open_logs_impl();
                }
                "devtools" => {
                    if let Some(window) = app.get_webview_window("main") {
                        window.open_devtools();
                        let _ = window.show();
                        let _ = window.set_focus();
                    }
                }
                "quit" => {
                    app.exit(0);
                }
                "quit-and-stop" => {
                    if current_mode(&app) == "local" {
                        let shell: State<Shell> = app.state();
                        shell.quit_after_stop.store(true, Ordering::Release);
                        if stop_impl(app.clone(), Some(true)).is_err() {
                            shell.quit_after_stop.store(false, Ordering::Release);
                        }
                    } else {
                        disconnect_locald(&app);
                        app.exit(0);
                    }
                }
                _ => {}
            }
        })
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

fn release_local_ai_before_exit() -> Result<(), String> {
    let (sender, receiver) = std::sync::mpsc::sync_channel(1);
    std::thread::spawn(move || {
        let _ = sender.send(request_local_ai_release());
    });
    receiver
        .recv_timeout(Duration::from_secs(120))
        .map_err(|_| "timed out while stopping sharing and releasing MLX memory".to_string())?
}

fn request_local_ai_release() -> Result<(), String> {
    let mut connection = connect_locald()?;
    let id = format!("desktop-exit-local-ai-release-{}", std::process::id());
    writeln!(
        connection.writer,
        "{}",
        json!({"v": 1, "cmd": "local-ai.release", "id": id})
    )
    .map_err(|error| format!("could not request MLX shutdown: {error}"))?;
    connection
        .writer
        .flush()
        .map_err(|error| format!("could not request MLX shutdown: {error}"))?;

    loop {
        let mut line = String::new();
        let bytes = connection
            .reader
            .read_line(&mut line)
            .map_err(|error| format!("could not confirm MLX shutdown: {error}"))?;
        if bytes == 0 {
            return Err("locald disconnected before confirming MLX shutdown".into());
        }
        if line.len() > 1024 * 1024 {
            return Err("locald MLX shutdown response exceeded 1 MiB".into());
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
                    .unwrap_or("locald could not release MLX memory")
                    .to_string());
            }
            _ => {}
        }
    }
}

fn main() {
    let mode = connection_mode();

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
            apply_operator_config,
            local_ai_action,
            sharing_action,
            close_local_settings,
            open_developer_tools
        ])
        .setup(move |app| {
            let handle = app.handle().clone();

            let initial_url = if mode == "hosted" {
                WebviewUrl::External(hosted_url().parse().expect("valid hosted url"))
            } else {
                WebviewUrl::App("index.html".into())
            };

            let main = WebviewWindowBuilder::new(app, "main", initial_url)
                .title("Lemma")
                .inner_size(1280.0, 860.0)
                .min_inner_size(980.0, 680.0)
                .devtools(true)
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
                .build()?;

            main.show()?;
            main.set_focus()?;
            if std::env::var("LEMMA_DESKTOP_DEVTOOLS").as_deref() == Ok("1") {
                main.open_devtools();
            }

            build_tray(&handle)?;

            // Local mode: connect to the durable daemon immediately so splash
            // has a live event stream the moment it loads.
            if connection_mode() == "local" {
                if let Err(error) = ensure_locald(&handle) {
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
                // Hide to tray; services keep running.
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
            tauri::RunEvent::Exit => {
                if current_mode(app) == "local" {
                    if let Err(error) = release_local_ai_before_exit() {
                        eprintln!("[local-ai-exit] {error}");
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
    fn local_ai_is_explicit_and_hidden_without_apple_silicon_support() {
        let html = include_str!("../ui/control.html");
        let script = include_str!("../ui/control.js");

        assert!(html.contains("id=\"nav-models\" hidden"));
        assert!(script.contains("Downloads are resumable and opt-in"));
        assert!(script.contains("$(\"nav-models\").hidden = !supported"));
        assert!(script.contains("invoke(\"local_ai_action\""));
        assert!(!script.contains("localAiAction(\"install\")();"));
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
}
