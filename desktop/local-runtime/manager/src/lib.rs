//! The host half of the local runtime: one managed guest, and the
//! platform-specific machinery that starts it.
//!
//! Was one 2,850-line file. Split by what the host is doing to the guest.

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
#[cfg(target_os = "macos")]
use std::process::Child;
use std::process::{Command, Stdio};
#[cfg(target_os = "macos")]
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

mod clock;
mod diagnostics;
mod guest_image;
#[cfg(any(target_os = "macos", test))]
mod kernel_health;
mod lifecycle;
mod macos;
mod private_files;
mod request;
mod windows;
mod windows_data;
mod wsl_command;

pub(crate) use diagnostics::*;
pub(crate) use guest_image::*;
pub(crate) use private_files::*;
pub(crate) use request::*;
// Only the guards reach these by bare name; `windows_data` uses its own.
#[cfg(test)]
pub(crate) use windows_data::*;
#[cfg(any(windows, test))]
pub(crate) use wsl_command::*;

#[cfg(test)]
mod tests;

const CAPABILITY_BYTES: usize = 32;
const MAX_RESPONSE_BYTES: usize = 4 * 1024 * 1024;
/// Spawn a child without flashing up a console window.
///
/// The packaged app is a GUI process with no console of its own, and nearly
/// everything the runtime spawns -- the guest bridge, wsl.exe, powershell.exe
/// -- is a console-subsystem program. Creating one of those from a process that
/// has no console makes Windows allocate a fresh conhost window for it, which
/// the user sees sitting next to the app and can close, taking the child with
/// it. Redirecting stdio does not suppress that window; only this flag does.
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

/// The distribution name used when a caller does not choose one.
///
/// It used to be the only name: a bare constant, while the daemon's own control
/// endpoint is keyed to the state root. So two installations -- a second user
/// profile, a dev root, a reinstall pointed elsewhere -- got two daemons and
/// then quietly shared one guest, which means one install's capability file
/// overwriting the other's, one install's stop terminating the other's runtime,
/// and the second install's pods running against the first install's data disk.
pub const DEFAULT_WSL_DISTRIBUTION: &str = "LemmaRuntime";
/// The phrase that turns a runtime failure into an offer to reset local data.
///
/// Duplicated from `lemma_locald::paths::DATA_RESET_MARKER` and pinned by a
/// test there: this crate is a dependency of locald, not the other way round.
///
/// macOS-only because the one detector that raises it is: Windows runs the
/// guest under WSL, where the data disk is a distribution rather than a raw
/// image and nothing yet reads a console log for a repair verdict. When that
/// detector is written it raises this same phrase and needs no new transport --
/// which is the whole point of the phrase being the contract.
#[cfg(target_os = "macos")]
const DATA_RESET_MARKER: &str = "local data must be reset";
#[cfg(target_os = "macos")]
const DATA_DISK_BYTES: u64 = 24 * 1024 * 1024 * 1024;

#[derive(Clone, Debug)]
pub struct ManagedRuntimeConfig {
    pub local_root: PathBuf,
    /// Which private WSL distribution this installation owns.
    pub wsl_distribution: String,
    pub artifact_root: PathBuf,
    pub bridge_executable: PathBuf,
    #[cfg(target_os = "macos")]
    pub vz_executable: PathBuf,
    #[cfg(windows)]
    pub wsl_executable: PathBuf,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct ManagedRuntimeStatus {
    /// Absent when the guest holds no DHCP lease.
    ///
    /// A guest without one is still healthy: core services reach the host over
    /// the private socket bridges, which need no address. Only sandboxes are
    /// unreachable. Typed as a required `String`, a null here failed to
    /// deserialise and became "invalid guest health response" -- which the
    /// probe then read as a dead runtime, turning a denied Local Network
    /// permission back into the failure the guest fix removed.
    #[serde(default)]
    pub endpoint_host: Option<String>,
    pub host_gateway: String,
    pub engine: String,
    #[serde(default)]
    pub active_sandboxes: usize,
    #[serde(default)]
    pub balloon_state: Option<String>,
    #[serde(default)]
    pub balloon_target_bytes: Option<u64>,
}

pub struct ManagedRuntime {
    config: ManagedRuntimeConfig,
    capability_file: PathBuf,
    control_socket: PathBuf,
    #[cfg(target_os = "macos")]
    host_epoch_file: PathBuf,
    #[cfg(target_os = "macos")]
    vm_process_marker: PathBuf,
    /// Tells the guest whether the data disk it is about to mount was created
    /// by this very boot.
    ///
    /// Only the host can know that -- the guest sees a block device either way
    /// -- and without it the boot script has to guess whether an unrecognised
    /// filesystem is a new disk to format or user data it must not touch.
    #[cfg(target_os = "macos")]
    data_disk_fresh_marker: PathBuf,
    #[cfg(target_os = "macos")]
    vm: Mutex<Option<Child>>,
}

impl ManagedRuntime {
    pub fn new(config: ManagedRuntimeConfig) -> io::Result<Self> {
        let run_root = config.local_root.join("run/guest-control");
        fs::create_dir_all(&run_root)?;
        set_private_directory(&run_root)?;
        Ok(Self {
            capability_file: run_root.join("guest.capability"),
            #[cfg(target_os = "macos")]
            host_epoch_file: run_root.join("host.epoch"),
            #[cfg(target_os = "macos")]
            vm_process_marker: run_root.join("vz-process.json"),
            #[cfg(target_os = "macos")]
            data_disk_fresh_marker: run_root.join("data-disk-fresh"),
            control_socket: config.local_root.join("run/guest.sock"),
            config,
            #[cfg(target_os = "macos")]
            vm: Mutex::new(None),
        })
    }
}
