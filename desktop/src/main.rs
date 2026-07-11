// Lemma desktop shell: thin Tauri wrapper around the Python supervisor.
//
// The shell owns native chrome (window, tray, menus) and the supervisor
// process lifecycle. All orchestration intelligence lives in
// `lemma-stack supervise`, which speaks JSON lines over stdio.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::Serialize;
use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use tauri::menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::webview::NewWindowResponse;
use tauri::{AppHandle, Emitter, Manager, State, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_autostart::ManagerExt as _;

const DEFAULT_HOSTED_URL: &str = "https://lemma.work";
const DEFAULT_LOCAL_URL: &str = "http://localhost:3711";
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
    supervisor: Mutex<Option<Child>>,
    supervisor_stdin: Mutex<Option<std::process::ChildStdin>>,
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
            supervisor: Mutex::new(None),
            supervisor_stdin: Mutex::new(None),
        }
    }
}

fn home_dir() -> PathBuf {
    PathBuf::from(std::env::var("HOME").expect("HOME is not set"))
}

fn app_support_dir() -> PathBuf {
    home_dir().join("Library/Application Support/Lemma")
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

fn write_config(update: impl FnOnce(&mut Value)) {
    let mut config = read_config();
    update(&mut config);
    let _ = std::fs::create_dir_all(app_support_dir());
    let _ = std::fs::write(
        config_path(),
        serde_json::to_string_pretty(&config).unwrap_or_default(),
    );
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

/// Where the lemma-stack checkout lives, used only for the dev fallback (when
/// no bundled sidecar is present). Dev default: this repo. Packaged builds set
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

/// The compiled supervisor sidecar shipped next to the app executable
/// (Contents/MacOS/lemma-supervisor in a bundle).
fn bundled_supervisor() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?;
    let candidate = exe.parent()?.join("lemma-supervisor");
    candidate.exists().then_some(candidate)
}

fn enriched_path() -> String {
    let current = std::env::var("PATH").unwrap_or_default();
    let extras = [
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
    ];
    let mut parts: Vec<&str> = current.split(':').filter(|p| !p.is_empty()).collect();
    for extra in extras {
        if !parts.contains(&extra) {
            parts.push(extra);
        }
    }
    parts.join(":")
}

// ---------------------------------------------------------------------------
// Supervisor lifecycle
// ---------------------------------------------------------------------------

fn ensure_supervisor(app: &AppHandle) -> Result<(), String> {
    let shell: State<Shell> = app.state();
    {
        let mut guard = shell.supervisor.lock().unwrap();
        if let Some(child) = guard.as_mut() {
            if child.try_wait().map_err(|e| e.to_string())?.is_none() {
                return Ok(()); // already running
            }
            *guard = None;
            *shell.supervisor_stdin.lock().unwrap() = None;
        }
    }

    let root = runtime_root();
    let have_checkout = root.join("lemma-stack/pyproject.toml").exists();

    // Resolution order: explicit binary → bundled sidecar → lemma-stack from a
    // checkout. The bundled sidecar is self-contained: it runs `lemma-stack
    // supervise`, which pulls the released images itself — no checkout or
    // runtime download required.
    let supervisor_bin = std::env::var("LEMMA_DESKTOP_SUPERVISOR_BIN")
        .ok()
        .map(PathBuf::from)
        .filter(|p| p.exists())
        .or_else(bundled_supervisor);

    let mut command = match &supervisor_bin {
        Some(bin) => Command::new(bin),
        None => {
            if !have_checkout {
                return Err(format!(
                    "runtime not found: {} has no lemma-stack checkout and no \
                     bundled supervisor is available",
                    root.display()
                ));
            }
            // Dev fallback: run lemma-stack from the checkout.
            let mut fallback = Command::new("uv");
            fallback.args([
                "run",
                "--project",
                "lemma-stack",
                "lemma-stack",
                "supervise",
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
        .env(
            "AGENTBOX_PROVIDER",
            std::env::var("AGENTBOX_PROVIDER").unwrap_or_else(|_| "auto".into()),
        )
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    let mut child = command
        .spawn()
        .map_err(|e| format!("failed to spawn supervisor: {e}"))?;

    let stdout = child.stdout.take().expect("piped stdout");
    let stderr = child.stderr.take().expect("piped stderr");
    let stdin = child.stdin.take().expect("piped stdin");

    *shell.supervisor_stdin.lock().unwrap() = Some(stdin);
    *shell.supervisor.lock().unwrap() = Some(child);

    let handle = app.clone();
    std::thread::spawn(move || {
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            match serde_json::from_str::<Value>(&line) {
                Ok(event) => handle_supervisor_event(&handle, &event),
                Err(_) => emit_log(&handle, &line),
            }
        }
        supervisor_gone(&handle);
    });

    let handle = app.clone();
    std::thread::spawn(move || {
        for line in BufReader::new(stderr).lines().map_while(Result::ok) {
            emit_log(&handle, &line);
        }
    });

    Ok(())
}

fn send_to_supervisor(app: &AppHandle, message: Value) -> Result<(), String> {
    let shell: State<Shell> = app.state();
    let mut guard = shell.supervisor_stdin.lock().unwrap();
    let stdin = guard.as_mut().ok_or("supervisor is not running")?;
    writeln!(stdin, "{message}").map_err(|e| format!("supervisor write failed: {e}"))?;
    stdin
        .flush()
        .map_err(|e| format!("supervisor flush failed: {e}"))
}

fn supervisor_gone(app: &AppHandle) {
    let shell: State<Shell> = app.state();
    *shell.supervisor.lock().unwrap() = None;
    *shell.supervisor_stdin.lock().unwrap() = None;
    let snapshot = {
        let mut ui = shell.ui.lock().unwrap();
        if ui.running {
            ui.status = "Supervisor exited unexpectedly".into();
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

fn handle_supervisor_event(app: &AppHandle, event: &Value) {
    if std::env::var("LEMMA_DESKTOP_DEBUG").as_deref() == Ok("1") {
        eprintln!("[supervisor] {event}");
    }
    let shell: State<Shell> = app.state();
    let kind = event["event"].as_str().unwrap_or_default();

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
            "ready" => {
                ui.ready = true;
                ui.running = true;
                ui.error = false;
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

    let _ = app.emit("lemma:state", snapshot);
    if kind == "error" {
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
    ensure_supervisor(&app)?;
    let setup = std::env::var("LEMMA_DESKTOP_START_SETUP").as_deref() == Ok("1");
    send_to_supervisor(
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
    send_to_supervisor(
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
    ensure_supervisor(&app)?;
    send_to_supervisor(&app, json!({"cmd": "restart", "id": "shell-restart"}))
}

#[tauri::command]
fn open_app(app: AppHandle) -> Result<(), String> {
    let target = app_base_url(&app);
    open_app_window(&app, &target)
}

#[tauri::command]
fn open_logs(_app: AppHandle) -> Result<(), String> {
    let logs = runtime_root().join(".local/lemma/logs");
    Command::new("/usr/bin/open")
        .arg(logs)
        .spawn()
        .map_err(|e| e.to_string())?;
    Ok(())
}

#[tauri::command]
fn set_connection_mode(app: AppHandle, mode: String) -> Result<(), String> {
    if mode != "local" && mode != "hosted" {
        return Err(format!("unknown mode {mode:?}"));
    }
    set_mode(&app, &mode);
    if mode == "hosted" {
        return open_app_window(&app, &hosted_url());
    }
    ensure_supervisor(&app)?;
    let setup = std::env::var("LEMMA_DESKTOP_START_SETUP").as_deref() == Ok("1");
    send_to_supervisor(
        &app,
        json!({"cmd": "start", "setup": setup, "id": "shell-start"}),
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
    set_mode(&app, new_mode);
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

fn set_mode(app: &AppHandle, mode: &str) {
    {
        let shell: State<Shell> = app.state();
        shell.ui.lock().unwrap().mode = mode.to_string();
    }
    write_config(|config| {
        config["connectionMode"] = json!(mode);
        config["connectionModePromptRevision"] = json!(CONNECTION_MODE_PROMPT_REVISION);
    });
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
    let _ = Command::new("/usr/bin/open").arg(url).spawn();
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
                "logs" => {
                    let _ = open_logs(app);
                }
                "quit" => {
                    shutdown_supervisor(&app);
                    app.exit(0);
                }
                _ => {}
            }
        })
        .build(app)?;
    Ok(())
}

fn shutdown_supervisor(app: &AppHandle) {
    // Leave services running (hide-to-tray semantics survive shell restarts);
    // the supervisor exits when it sees the shutdown command or stdin EOF.
    let _ = send_to_supervisor(app, json!({"cmd": "shutdown", "stop_services": false}));
    let taken = {
        let shell: State<Shell> = app.state();
        let mut guard = shell.supervisor.lock().unwrap();
        guard.take()
    };
    if let Some(mut child) = taken {
        std::thread::spawn(move || {
            std::thread::sleep(std::time::Duration::from_secs(3));
            let _ = child.kill();
        });
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
            choose_connection_mode,
            set_connection_mode,
            get_state,
            login
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

            // Local mode: bring the supervisor up immediately so the splash
            // has a live event stream the moment it loads.
            if connection_mode() == "local" {
                if let Err(error) = ensure_supervisor(&handle) {
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
                shutdown_supervisor(app);
            }
            _ => {}
        });
}

#[cfg(test)]
mod tests {
    use super::*;

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
    fn ordinary_web_navigation_stays_in_the_webview() {
        let urls = [
            "https://sales.apps.lemma.work/",
            "https://api.lemma.work/widgets/serve/conversation/tool",
            "http://sales.127-0-0-1.sslip.io:8711/",
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
        assert!(plist.contains("127-0-0-1.sslip.io"));
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
            desktop_auth_url("http://localhost:3711/", "signin"),
            "http://localhost:3711/auth/desktop?mode=signin"
        );
    }
}
