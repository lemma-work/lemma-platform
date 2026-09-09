// Lemma desktop shell: thin Tauri client for the durable local daemon.
//
// The shell owns native chrome (window, tray, menus); lemma-locald owns service
// lifecycle. Managed releases use native host packs and private runtime
// providers; the daemon retains an unbundled compatibility adapter only for
// development and existing external-runtime installations.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::Serialize;
mod agent_host_ui;
mod app;
mod app_update;
mod appearance;
mod config_store;
mod confirmation;
mod connection;
mod control_center;
mod diagnostics;
mod ipc_read;
mod local_recovery;
mod locald_client;
mod locald_events;
mod locald_process;
mod menus;
mod native_assets;
mod navigation;
mod operator_settings;
mod pod_windows;
mod prompts;
mod quitting;
mod recovery;
mod release_info;
mod runtime_layout;
mod runtime_setup;
mod shell_paths;
mod shutdown;
mod stack_control;
mod state;
mod update_policy;
mod window_placement;
mod windowing;
mod workspace;

use agent_host_ui::*;
use app_update::*;
use appearance::*;
use connection::*;
use control_center::*;
use locald_client::*;
use locald_events::*;
use locald_process::*;
use menus::*;
use navigation::*;
use pod_windows::*;
use prompts::*;
use quitting::*;
use recovery::RecoveryOutcome;
use release_info::*;
use runtime_layout::*;
use runtime_setup::*;
use serde_json::{json, Value};
use shell_paths::*;
use stack_control::*;
use state::*;
use std::io::{BufReader, Read, Seek, SeekFrom, Write};
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
use window_placement::*;
use windowing::*;
use workspace::*;

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

/// What a refused operation says. Callers match on it to tell "the daemon is
/// busy" apart from "the daemon is broken", so it is one string in one place.
const LOCALD_BUSY: &str =
    "Lemma is still finishing another operation. Wait for that to finish, then try again.";

/// The window label pod apps open into.
///
/// One label, reused: clicking "open in new window" five times should raise the
/// same window five times, not leave five identical ones behind.
const POD_APP_WINDOW: &str = "pod-app";

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

const MAX_DIAGNOSTIC_LOG_READ: u64 = 128 * 1024;

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

#[cfg(test)]
mod tests;

fn main() {
    app::run();
}
