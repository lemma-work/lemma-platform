// Lemma desktop shell: thin Tauri client for the durable local daemon.
//
// The shell owns native chrome (window, tray, menus); lemma-locald owns service
// lifecycle. Managed releases use native host packs and private runtime
// providers; the daemon retains an unbundled compatibility adapter only for
// development and existing external-runtime installations.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::Serialize;
use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::Mutex;
use std::time::Duration;
use tauri::menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::webview::NewWindowResponse;
use tauri::{AppHandle, Emitter, Manager, State, WebviewUrl, WebviewWindow, WebviewWindowBuilder};
use tauri_plugin_autostart::ManagerExt as _;

mod artifact_install;

#[cfg(unix)]
use interprocess::local_socket::GenericFilePath;
#[cfg(windows)]
use interprocess::local_socket::GenericNamespaced;
use interprocess::local_socket::{prelude::*, Name, RecvHalf, SendHalf};

const DEFAULT_HOSTED_URL: &str = "https://lemma.work";
const DEFAULT_LOCAL_URL: &str = "http://app.lemma.localhost:3711";
// Legacy development builds persisted a mode before the released chooser
// contract was stable. Require that chooser once, then retain the new choice.
const CONNECTION_MODE_PROMPT_REVISION: u64 = 1;

#[derive(Clone, Serialize, Default)]
#[serde(rename_all = "camelCase")]
struct UiState {
    status: String,
    phase: String,
    phase_key: String,
    progress: u64,
    eta_seconds: Option<u64>,
    setup: bool,
    error: bool,
    ready: bool,
    running: bool,
    mode: String,
    url: String,
}

struct Shell {
    ui: Mutex<UiState>,
    locald_writer: Mutex<Option<SendHalf>>,
    locald_connect: Mutex<()>,
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
            url: local_url(),
            ..Default::default()
        };
        Shell {
            ui: Mutex::new(ui),
            locald_writer: Mutex::new(None),
            locald_connect: Mutex::new(()),
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
    std::fs::create_dir_all(app_support_dir())
        .map_err(|error| format!("could not create desktop config directory: {error}"))?;
    let serialized = serde_json::to_vec_pretty(&config)
        .map_err(|error| format!("could not encode desktop config: {error}"))?;
    std::fs::write(config_path(), serialized)
        .map_err(|error| format!("could not save desktop config: {error}"))
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

fn local_url() -> String {
    std::env::var("LEMMA_DESKTOP_LOCAL_URL").unwrap_or_else(|_| DEFAULT_LOCAL_URL.into())
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
    // Compile-time fallback: the monorepo containing this crate.
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("crate has a parent directory")
        .to_path_buf()
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

fn installed_runtime() -> Option<artifact_install::InstalledRuntime> {
    let config = read_config();
    let installed = config.get("installedRuntime")?;
    let release = installed.get("release")?.as_str()?;
    let root = PathBuf::from(installed.get("root")?.as_str()?);
    let runtime = artifact_install::installed_runtime(&root, release);
    runtime.is_complete().then_some(runtime)
}

fn host_pack_root() -> Option<PathBuf> {
    bundled_host_pack_root().or_else(|| installed_runtime().map(|runtime| runtime.host_pack_root))
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
    bundled_managed_runtime_root()
        .or_else(|| installed_runtime().map(|runtime| runtime.managed_runtime_root))
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

fn bundled_host_pack_release() -> Option<String> {
    let release = host_pack_root()?.join("release.json");
    let payload: Value = serde_json::from_slice(&std::fs::read(release).ok()?).ok()?;
    payload["version"].as_str().map(str::to_owned)
}

fn locald_matches_host_pack(hello: &Value, required_release: Option<&str>) -> bool {
    required_release.is_none_or(|required| {
        matches!(hello["mode"].as_str(), Some("host-packs" | "managed-local"))
            && hello["host_pack_release"].as_str() == Some(required)
    })
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

    let required_release = bundled_host_pack_release();
    if let Ok(mut connection) = connect_locald() {
        if locald_matches_host_pack(&connection.hello, required_release.as_deref()) {
            install_locald_connection(app, connection);
            return Ok(());
        }
        request_locald_replacement(&mut connection)?;
        wait_for_locald_exit()?;
    }

    spawn_locald()?;
    let mut last_error = "daemon did not create its control endpoint".to_string();
    for _ in 0..80 {
        match connect_locald() {
            Ok(connection)
                if locald_matches_host_pack(&connection.hello, required_release.as_deref()) =>
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
    if host_pack_root().is_some() && managed_runtime_root().is_some() {
        return Ok(());
    }
    if runtime_root().join("locald/Cargo.toml").is_file() {
        return Ok(());
    }
    let manifest = bundled_release_manifest().ok_or_else(|| {
        "this online installer is missing its signed local release manifest".to_string()
    })?;
    emit_runtime_install_progress(app, "Preparing local runtime", 1, None);
    let installed = artifact_install::install_from_manifest(
        &manifest,
        &runtime_install_root(),
        env!("CARGO_PKG_VERSION"),
        &mut |progress| {
            let percent = if progress.total == 0 {
                1
            } else {
                2 + progress.downloaded.saturating_mul(88) / progress.total
            };
            emit_runtime_install_progress(
                app,
                progress.label,
                percent.min(90),
                (progress.downloaded < progress.total)
                    .then_some(progress.total - progress.downloaded),
            );
        },
    )
    .map_err(|error| format!("could not install the local runtime: {error}"))?;
    let root = installed
        .host_pack_root
        .parent()
        .ok_or("installed runtime has no release root")?
        .to_string_lossy()
        .into_owned();
    write_config(|config| {
        config["installedRuntime"] = json!({
            "release": installed.release,
            "root": root,
        });
    })?;
    emit_runtime_install_progress(app, "Local runtime installed", 92, None);
    Ok(())
}

fn emit_runtime_install_progress(
    app: &AppHandle,
    label: &str,
    progress: u64,
    remaining_bytes: Option<u64>,
) {
    let shell: State<Shell> = app.state();
    let snapshot = {
        let mut ui = shell.ui.lock().unwrap();
        ui.setup = true;
        ui.phase = label.to_owned();
        ui.phase_key = "runtime-install".into();
        ui.progress = progress;
        ui.status = match remaining_bytes {
            Some(bytes) => format!("{label}: {} MB remaining", bytes.div_ceil(1024 * 1024)),
            None => label.to_owned(),
        };
        ui.clone()
    };
    let _ = app.emit("lemma:state", snapshot);
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
            bundled_sibling("lemma-vz").ok_or("bundled lemma-vz helper is missing")?,
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
        json!({"v": 1, "cmd": "hello", "token": token.trim()})
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

fn wait_for_locald_exit() -> Result<(), String> {
    let root = locald_root();
    let name = locald_socket_name(&root)?;
    for _ in 0..50 {
        if LocalSocketStream::connect(name.clone()).is_err() {
            return Ok(());
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    Err("the previous local service manager did not stop for the app update".into())
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

fn locald_gone(app: &AppHandle) {
    let shell: State<Shell> = app.state();
    *shell.locald_writer.lock().unwrap() = None;
    let snapshot = {
        let mut ui = shell.ui.lock().unwrap();
        if ui.running {
            ui.status = "Local service manager disconnected".into();
            ui.error = true;
            ui.running = false;
        }
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
    let _ = app.emit_to("control", "lemma:locald-event", event.clone());

    let snapshot = {
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
                ui.setup = event["setup"].as_bool().unwrap_or(ui.setup);
                let detail = event["detail"].as_str().unwrap_or_default();
                ui.status = if detail.is_empty() {
                    ui.phase.clone()
                } else {
                    format!("{}: {}", ui.phase, detail)
                };
                ui.error = ui.phase_key == "error";
            }
            "state" => {
                ui.running = event["running"].as_bool().unwrap_or(false);
                ui.ready = event["ready"].as_bool().unwrap_or(false);
                ui.error = event["status"].as_str() == Some("error");
            }
            "status" => {
                ui.running = event["running"].as_bool().unwrap_or(ui.running);
                ui.ready = event["ready"].as_bool().unwrap_or(ui.ready);
                ui.error = event["status"].as_str() == Some("error");
                if let Some(url) = event["url"].as_str() {
                    ui.url = url.to_string();
                }
                if let Some(phase) = event.get("phase").and_then(Value::as_object) {
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
                    let detail = phase.get("detail").and_then(Value::as_str).unwrap_or("");
                    ui.status = if detail.is_empty() {
                        ui.phase.clone()
                    } else {
                        format!("{}: {detail}", ui.phase)
                    };
                }
            }
            "ready" => {
                ui.ready = true;
                ui.running = true;
                ui.error = false;
                // Main, API, built-app, and workspace-app hosts all live below
                // the reserved lemma.localhost loopback cookie boundary.
                if let Some(url) = event["url"].as_str() {
                    ui.url = url.to_string();
                }
                // Stay on the splash: the user proceeds via its CTA.
            }
            "error" => {
                ui.error = true;
                ui.status = event["message"].as_str().unwrap_or("startup failed").into();
            }
            _ => {}
        }
        ui.clone()
    };

    let has_error = snapshot.error;
    let _ = app.emit("lemma:state", snapshot);
    if kind == "error" || has_error {
        show_splash(app);
    }
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

fn navigate_app_window(app: &AppHandle, url: &str) -> Result<(), String> {
    open_app_window(app, url)
}

fn show_splash(app: &AppHandle) {
    let _ = open_app_window(app, "tauri://localhost/index.html");
}

fn show_control_center(app: &AppHandle) -> Result<(), String> {
    if let Some(window) = app.get_webview_window("control") {
        window.show().map_err(|error| error.to_string())?;
        window.set_focus().map_err(|error| error.to_string())?;
        return Ok(());
    }
    let window = WebviewWindowBuilder::new(app, "control", WebviewUrl::App("control.html".into()))
        .title("Lemma Control Center")
        .inner_size(1180.0, 780.0)
        .min_inner_size(900.0, 640.0)
        .initialization_script(desktop_context_script(&current_mode(app)))
        .on_navigation(|url| url.scheme() == "tauri")
        .on_new_window(move |url, _features| {
            if matches!(url.scheme(), "http" | "https") {
                open_external(url.as_str());
            }
            NewWindowResponse::Deny
        })
        .build()
        .map_err(|error| error.to_string())?;
    window.show().map_err(|error| error.to_string())?;
    window.set_focus().map_err(|error| error.to_string())?;
    Ok(())
}

// ---------------------------------------------------------------------------
// Commands (same verbs as the Electron IPC surface)
// ---------------------------------------------------------------------------

#[tauri::command]
fn start(app: AppHandle) -> Result<(), String> {
    let mode = current_mode(&app);
    if mode == "undecided" {
        return Err("choose a connection mode first".into());
    }
    if mode == "hosted" {
        return open_app_window(&app, &hosted_url());
    }
    ensure_locald(&app)?;
    let setup = std::env::var("LEMMA_DESKTOP_START_SETUP").as_deref() == Ok("1");
    send_to_locald(
        &app,
        json!({"cmd": "start", "setup": setup, "id": "shell-start"}),
    )
}

#[tauri::command]
fn stop(app: AppHandle, include_infra: Option<bool>) -> Result<(), String> {
    if current_mode(&app) != "local" {
        return Err("local services are not active in Lemma Cloud mode".into());
    }
    show_splash(&app);
    ensure_locald(&app)?;
    send_to_locald(
        &app,
        json!({"cmd": "stop", "infra": include_infra.unwrap_or(false), "id": "shell-stop"}),
    )
}

#[tauri::command]
fn restart(app: AppHandle) -> Result<(), String> {
    if current_mode(&app) != "local" {
        return Err("local services are not active in Lemma Cloud mode".into());
    }
    show_splash(&app);
    ensure_locald(&app)?;
    send_to_locald(&app, json!({"cmd": "restart", "id": "shell-restart"}))
}

#[tauri::command]
fn open_app(app: AppHandle) -> Result<(), String> {
    let target = app_base_url(&app);
    open_app_window(&app, &target)
}

#[tauri::command]
fn open_logs(_app: AppHandle) -> Result<(), String> {
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
fn open_control_center(app: AppHandle) -> Result<(), String> {
    show_control_center(&app)
}

fn is_control_window_label(label: &str) -> bool {
    label == "control"
}

fn require_control_window(window: &WebviewWindow) -> Result<(), String> {
    if is_control_window_label(window.label()) {
        Ok(())
    } else {
        Err("this operation is available only in the privileged Control Center".into())
    }
}

#[tauri::command]
fn control_snapshot(window: WebviewWindow, app: AppHandle, id: String) -> Result<(), String> {
    require_control_window(&window)?;
    ensure_locald(&app)?;
    send_to_locald(&app, json!({"cmd":"control.snapshot", "id": id}))
}

#[tauri::command]
fn apply_operator_config(
    window: WebviewWindow,
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
fn set_connection_mode(app: AppHandle, mode: String) -> Result<(), String> {
    if mode != "local" && mode != "hosted" {
        return Err(format!("unknown mode {mode:?}"));
    }
    set_mode(&app, &mode)?;
    if mode == "hosted" {
        return open_app_window(&app, &hosted_url());
    }
    ensure_locald(&app)?;
    let setup = std::env::var("LEMMA_DESKTOP_START_SETUP").as_deref() == Ok("1");
    send_to_locald(
        &app,
        json!({"cmd": "start", "setup": setup, "id": "shell-start"}),
    )?;
    show_control_center(&app)
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
        start(app)?;
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
        shell.ui.lock().unwrap().mode = mode.to_string();
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

fn is_desktop_browser_auth_url(url: &tauri::Url) -> bool {
    matches!(url.scheme(), "http" | "https")
        && url.path().starts_with("/auth")
        && url
            .query_pairs()
            .any(|(key, value)| key == "desktop_browser" && value == "1")
}

fn navigation_allowed(url: &tauri::Url) -> bool {
    url.scheme() == "tauri" || same_origin(url, &hosted_url()) || same_origin(url, &local_url())
}

fn navigation_disposition(url: &tauri::Url) -> NavigationDisposition {
    if is_desktop_browser_auth_url(url) {
        NavigationDisposition::OpenExternal
    } else if matches!(url.scheme(), "tauri" | "http" | "https") {
        NavigationDisposition::Allow
    } else {
        NavigationDisposition::Deny
    }
}

fn new_window_disposition(url: &tauri::Url, app_base: &str) -> NewWindowDisposition {
    if url.as_str() == "about:blank" {
        NewWindowDisposition::Deny
    } else if is_desktop_browser_auth_url(url) {
        NewWindowDisposition::OpenExternal
    } else if navigation_allowed(url) || same_origin(url, app_base) {
        NewWindowDisposition::NavigateInApp
    } else {
        NewWindowDisposition::OpenExternal
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
    format!(
        "window.__LEMMA_DESKTOP__ = Object.freeze({});",
        serde_json::to_string(&context).unwrap_or_else(|_| "{}".into())
    )
}

// ---------------------------------------------------------------------------

fn app_base_url(app: &AppHandle) -> String {
    let (mode, url) = {
        let shell: State<Shell> = app.state();
        let ui = shell.ui.lock().unwrap();
        (ui.mode.clone(), ui.url.clone())
    };
    if mode == "hosted" {
        hosted_url()
    } else {
        url
    }
}

fn desktop_auth_url(base: &str, auth_mode: &str) -> String {
    format!(
        "{}/auth/desktop?mode={auth_mode}",
        base.trim_end_matches('/'),
    )
}

#[tauri::command]
async fn login(app: AppHandle, mode: Option<String>) -> Result<(), String> {
    let base = app_base_url(&app);
    let auth_mode = if mode.as_deref() == Some("signup") {
        "signup"
    } else {
        "signin"
    };
    // Keep `main` as the waiting/exchange surface in both modes. The frontend
    // creates a short-lived request, opens marked auth in the system browser,
    // and consumes the result when `lemma://auth/complete` returns.
    open_app_window(&app, &desktop_auth_url(&base, auth_mode))
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
    let control_item =
        MenuItem::with_id(app, "control", "Local Control Center…", true, None::<&str>)?;
    let quit_item = MenuItem::with_id(app, "quit", "Quit Lemma", true, None::<&str>)?;
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
            &PredefinedMenuItem::separator(app)?,
            &quit_item,
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
                    let _ = start(app);
                }
                "stop" => {
                    let _ = stop(app, Some(false));
                }
                "stop-all" => {
                    let _ = stop(app, Some(true));
                }
                "restart" => {
                    let _ = restart(app);
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
                    let _ = open_logs(app);
                }
                "quit" => {
                    disconnect_locald(&app);
                    app.exit(0);
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
            choose_connection_mode,
            set_connection_mode,
            get_state,
            login,
            open_control_center,
            control_snapshot,
            apply_operator_config
        ])
        .setup(move |app| {
            let handle = app.handle().clone();

            let initial_url = if mode == "hosted" {
                WebviewUrl::External(hosted_url().parse().expect("valid hosted url"))
            } else {
                WebviewUrl::App("index.html".into())
            };

            WebviewWindowBuilder::new(app, "main", initial_url)
                .title("Lemma")
                .inner_size(1280.0, 860.0)
                .min_inner_size(980.0, 680.0)
                .initialization_script(desktop_context_script(&mode))
                .on_navigation(move |url| match navigation_disposition(url) {
                    NavigationDisposition::Allow => true,
                    NavigationDisposition::OpenExternal => {
                        open_external(url.as_str());
                        false
                    }
                    NavigationDisposition::Deny => false,
                })
                .on_new_window({
                    let handle = handle.clone();
                    move |url, _features| {
                        let app_base = app_base_url(&handle);
                        match new_window_disposition(&url, &app_base) {
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

            if let Some(main) = handle.get_webview_window("main") {
                main.show()?;
                main.set_focus()?;
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
                }
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
        let current = json!({
            "event": "hello", "protocol": 1, "mode": "host-packs",
            "host_pack_release": "1.2.3",
        });
        let compatibility = json!({
            "event": "hello", "protocol": 1, "mode": "compatibility",
        });

        assert!(locald_matches_host_pack(&current, Some("1.2.3")));
        assert!(!locald_matches_host_pack(&current, Some("1.2.4")));
        assert!(!locald_matches_host_pack(&compatibility, Some("1.2.3")));
        assert!(locald_matches_host_pack(&compatibility, None));
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
            assert_eq!(navigation_disposition(&url), NavigationDisposition::Allow);
        }
    }

    #[test]
    fn unsupported_navigation_schemes_are_denied() {
        for raw_url in [
            "file:///tmp/report.html",
            "javascript:alert(1)",
            "lemma://other",
        ] {
            let url = tauri::Url::parse(raw_url).unwrap();
            assert_eq!(navigation_disposition(&url), NavigationDisposition::Deny);
        }
    }

    #[test]
    fn explicit_new_windows_keep_the_browser_policy() {
        let app_base = "https://lemma.work";
        let first_party = tauri::Url::parse("https://lemma.work/docs").unwrap();
        let external = tauri::Url::parse("https://widgets.example.com/report").unwrap();
        let blank = tauri::Url::parse("about:blank").unwrap();

        assert_eq!(
            new_window_disposition(&first_party, app_base),
            NewWindowDisposition::NavigateInApp
        );
        assert_eq!(
            new_window_disposition(&external, app_base),
            NewWindowDisposition::OpenExternal
        );
        assert_eq!(
            new_window_disposition(&blank, app_base),
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
            navigation_disposition(&desktop),
            NavigationDisposition::OpenExternal
        );
        assert_eq!(
            new_window_disposition(&desktop, "https://lemma.work"),
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
        assert!(html.contains("lemma-mark-bar-2"));
        assert!(html.contains("s.phaseKey === \"boot\""));
        assert!(html.contains("!s.error"));
        assert!(html.contains("await window.lemmaDesktop.openAuth(\"signup\")"));
        assert!(!html.contains("Nothing leaves your machine"));
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
    fn desktop_auth_uses_the_browser_handoff_in_every_mode() {
        assert_eq!(
            desktop_auth_url("https://lemma.work", "signup"),
            "https://lemma.work/auth/desktop?mode=signup"
        );
        assert_eq!(
            desktop_auth_url("http://app.lemma.localhost:3711/", "signin"),
            "http://app.lemma.localhost:3711/auth/desktop?mode=signin"
        );
    }
}
