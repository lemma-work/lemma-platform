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

#[cfg(any(target_os = "macos", test))]
mod kernel_health;

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
#[cfg(target_os = "macos")]
const VM_PROCESS_MARKER_SCHEMA_VERSION: u64 = 1;

#[cfg(target_os = "macos")]
#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct VmProcessMarker {
    schema_version: u64,
    pid: u32,
    executable: String,
    start_identity: String,
}

#[cfg(target_os = "macos")]
struct ProcessIdentity {
    executable: String,
    start_identity: String,
}

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

/// Which rootfs a registered distribution was imported from.
///
/// Size, and deliberately not modification time. The question this answers is
/// "was this guest imported from a *different release*", and what the answer is
/// used for is a prompt offering to delete the distribution -- which is where
/// the user's workspaces and databases live. Modification time changes whenever
/// the archive is written again, so a repair, a re-download or a plain reinstall
/// of the very same release all looked like a different one, and offered to
/// destroy the only copy of the data over a timestamp.
///
/// Two different releases with a byte-identical archive size would go unnoticed.
/// That is remote, and its consequence is one in-place start this check would
/// otherwise have refused; the alternative, hashing several gigabytes on every
/// launch, costs every user real time to catch it.
///
/// Free and un-gated so it is tested on every platform, not only compiled on
/// one -- the Windows guest path is its only caller, and code that exists on one
/// platform and is checked on none is how the mtime bug survived.
#[cfg_attr(not(windows), allow(dead_code))]
fn rootfs_stamp(rootfs: &Path) -> io::Result<String> {
    Ok(fs::metadata(rootfs)?.len().to_string())
}

fn guest_request_budget(operation: &str) -> Duration {
    match operation {
        "system.shutdown" => Duration::from_secs(8),
        "health" | "core.sandbox_images_status" => Duration::from_secs(5),
        // Pulling images is the one operation whose length is set by the
        // user's connection rather than by the guest. A first install fetches
        // roughly a gigabyte, and eight minutes is a limit on a slow download
        // rather than on a stuck one -- on a real Windows machine it failed
        // every attempt while making progress on every attempt.
        //
        // Longer than the guest's own `ENGINE_PULL_TIMEOUT`, deliberately. If
        // this expired first the guest would keep pulling into a request
        // nobody is waiting on, and the next attempt would contend with it.
        "core.images" | "core.sandbox_images" => Duration::from_secs(75 * 60),
        // Collected on a failure path, so the wait is added to a start that
        // has already gone wrong. The guest bounds its own collection well
        // inside this; the margin is for a guest slow enough to need it.
        "diagnostics.guest" => Duration::from_secs(45),
        _ => Duration::from_secs(8 * 60),
    }
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

    pub fn start(&self) -> io::Result<ManagedRuntimeStatus> {
        self.ensure_capability()?;
        #[cfg(target_os = "macos")]
        {
            self.refresh_host_epoch()?;
            self.start_macos()?;
        }
        #[cfg(windows)]
        self.start_windows()?;
        self.wait_ready()
    }

    pub fn prepare_host(&self) -> io::Result<Value> {
        #[cfg(target_os = "macos")]
        {
            Ok(json!({"ready": true, "reboot_required": false, "platform": "macos"}))
        }
        #[cfg(windows)]
        {
            self.prepare_windows_host()
        }
        #[cfg(not(any(target_os = "macos", windows)))]
        {
            Err(io::Error::new(
                io::ErrorKind::Unsupported,
                "managed host preparation is unsupported on this platform",
            ))
        }
    }

    pub fn request(&self, operation: &str, parameters: Value) -> io::Result<Value> {
        self.request_cancellable(
            operation,
            parameters,
            lemma_desktop_process::Cancellation::default(),
        )
    }

    pub fn request_cancellable(
        &self,
        operation: &str,
        parameters: Value,
        cancellation: lemma_desktop_process::Cancellation,
    ) -> io::Result<Value> {
        if operation.is_empty()
            || !operation
                .bytes()
                .all(|byte| byte.is_ascii_lowercase() || byte == b'.' || byte == b'_')
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid guest operation",
            ));
        }
        #[cfg(target_os = "macos")]
        if operation != "system.shutdown" && !operation.starts_with("diagnostics.") {
            self.check_guest_kernel()?;
        }
        let request = json!({
            "version": 1,
            "operation": operation,
            "parameters": parameters,
        });
        let mut encoded = serde_json::to_vec(&request)?;
        encoded.push(b'\n');
        let mut command = Command::new(&self.config.bridge_executable);
        command
            .no_console_window()
            .arg("request")
            .env("LEMMA_GUEST_CAPABILITY_FILE", &self.capability_file)
            .env("LEMMA_GUEST_CONTROL_SOCKET", &self.control_socket)
            .env("LEMMA_WSL_DISTRIBUTION", &self.config.wsl_distribution);
        let budget = guest_request_budget(operation);
        let output = lemma_desktop_process::run_with_input_cancellable(
            command,
            encoded,
            budget,
            MAX_RESPONSE_BYTES,
            cancellation,
        );
        #[cfg(target_os = "macos")]
        if operation != "system.shutdown" && !operation.starts_with("diagnostics.") {
            self.check_guest_kernel()?;
        }
        let output = output.map_err(|error| match error {
            lemma_desktop_process::SetupProcessError::TimedOut => io::Error::new(
                io::ErrorKind::TimedOut,
                "runtime bridge exceeded its request deadline",
            ),
            lemma_desktop_process::SetupProcessError::OutputLimit => io::Error::new(
                io::ErrorKind::InvalidData,
                "runtime bridge exceeded its output limit",
            ),
            lemma_desktop_process::SetupProcessError::Io(error) => error,
            lemma_desktop_process::SetupProcessError::Cancelled => {
                io::Error::new(io::ErrorKind::Interrupted, "runtime command was cancelled")
            }
        })?;
        if output.stdout.is_empty() {
            let detail = first_diagnostic(&output.stderr, "private guest did not respond");
            return Err(io::Error::new(
                io::ErrorKind::ConnectionRefused,
                format!("could not reach Lemma's private runtime: {detail}"),
            ));
        }
        let response: Value = serde_json::from_slice(&output.stdout).map_err(|error| {
            let detail = String::from_utf8_lossy(&output.stderr);
            io::Error::new(
                io::ErrorKind::InvalidData,
                format!(
                    "runtime bridge returned invalid JSON ({error}): {}",
                    detail.lines().next().unwrap_or("no diagnostic")
                ),
            )
        })?;
        if response.get("ok").and_then(Value::as_bool) != Some(true) {
            let error = response.get("error").and_then(Value::as_object);
            return Err(io::Error::other(
                error
                    .and_then(|value| value.get("message"))
                    .and_then(Value::as_str)
                    .unwrap_or("managed guest request failed"),
            ));
        }
        response
            .get("result")
            .cloned()
            .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "guest omitted result"))
    }

    pub fn capture_diagnostics(&self) -> io::Result<()> {
        #[cfg(target_os = "macos")]
        {
            // The VZ serial console is continuously appended by the helper,
            // and for a guest that never reached userspace it is the only
            // record there is. It was also the *only* record macOS ever had:
            // everything the Windows arm below collects -- addresses, routes,
            // listening sockets, containers, the guest's own service logs --
            // was written nowhere at all, so a macOS start that failed with
            // the guest up and its services half-started left a boot log and
            // nothing else. That is the failure people actually hit.
            //
            // A VZ guest has no exec channel, so the collection cannot be
            // driven from here the way `wsl.exe --exec` drives it on Windows.
            // The guest runs the same script itself -- literally the same
            // file, compiled into `lemma-guestd` -- and hands back the text.
            //
            // Allowing failure on purpose, exactly as the Windows arm does:
            // this runs *because* something has already gone wrong, so the
            // guest is often too broken to answer, and a guest that cannot
            // answer still leaves the serial console. Failing here would add
            // an error about collecting errors.
            let Ok(result) = self.request("diagnostics.guest", json!({})) else {
                return Ok(());
            };
            let text = result.get("text").and_then(Value::as_str).unwrap_or("");
            if text.is_empty() {
                return Ok(());
            }
            self.append_guest_log(text.as_bytes())
        }
        #[cfg(windows)]
        {
            // Allowing failure on purpose. This runs *because* something has
            // already gone wrong, so the guest is often exactly the kind of
            // half-up that makes a collector exit non-zero -- and whatever it
            // managed to print before giving up is the reason anyone asked for
            // diagnostics. Failing here would throw away the evidence.
            let output = self.wsl_allowing_failure(
                &[
                    "--distribution",
                    self.wsl_distribution(),
                    "--user",
                    "root",
                    "--exec",
                    "/bin/sh",
                    "-c",
                    GUEST_DIAGNOSTICS,
                ],
                None,
            )?;
            self.append_guest_log(&output.stdout)
        }
        #[cfg(not(any(target_os = "macos", windows)))]
        {
            Ok(())
        }
    }

    /// Append one capture to `logs/guest.log`, keeping the tail of it.
    ///
    /// The tail rather than the head: a collector that ran long enough to
    /// produce more than this wrote the service logs last, and those are what
    /// says why the start failed.
    #[cfg(any(target_os = "macos", windows))]
    fn append_guest_log(&self, captured: &[u8]) -> io::Result<()> {
        let log_path = self.config.local_root.join("logs/guest.log");
        rotate_log(&log_path, 5 * 1024 * 1024)?;
        let mut log = private_appending_log(&log_path)?;
        let start = captured.len().saturating_sub(128 * 1024);
        log.write_all(&captured[start..])?;
        log.write_all(b"\n")?;
        Ok(())
    }

    /// Verify both the platform runtime process and the guest control plane.
    ///
    /// This is intentionally stronger than checking whether the last start
    /// succeeded: the VM or WSL distribution may disappear while the native
    /// backend and frontend processes remain alive.
    pub fn health(&self) -> io::Result<ManagedRuntimeStatus> {
        #[cfg(target_os = "macos")]
        self.check_guest_kernel()?;
        #[cfg(target_os = "macos")]
        if let Some(error) = self.macos_exit_error()? {
            return Err(error);
        }
        let result = match self.request("health", json!({})) {
            Ok(result) => result,
            Err(error) => {
                // A torn containerd cache is disposable, but it must be reset
                // while the guest is offline. The guest persists a reset
                // marker; a clean stop lets the next boot repair only that
                // cache while preserving named volumes and workspaces.
                if cache_repair_required(&error) {
                    let _ = self.stop();
                }
                return Err(error);
            }
        };
        serde_json::from_value(result).map_err(|error| {
            io::Error::new(
                io::ErrorKind::InvalidData,
                format!("invalid guest health response: {error}"),
            )
        })
    }

    /// Discard the guest's data disk entirely, returning the bytes reclaimed.
    ///
    /// The blunt half of a local-data reset, for when the guest cannot be asked
    /// to tidy up after itself: a torn filesystem, a VM that will not boot, a
    /// disk whose size no longer matches. It takes the pulled container images
    /// with it, so `core.reset_data` inside the guest is preferred wherever the
    /// guest still answers.
    ///
    /// Stop first, then reclaim. `stop` handles the VM this process owns;
    /// `reclaim_owned_macos_vm` is the second pass for a helper left behind by
    /// a daemon that died without stopping it, and it verifies pid, executable
    /// and start identity before signalling anything. Unlinking a disk another
    /// process still has attached is the one thing that must not happen here.
    #[cfg(target_os = "macos")]
    pub fn discard_data_disk(&self) -> io::Result<u64> {
        self.stop()?;
        self.reclaim_owned_macos_vm()?;

        let disk = self.config.local_root.join("runtime/macos/data.raw");
        // Allocated blocks, not `len()`. The file is sparse and always reports
        // 24 GiB apparent size, so reporting `len()` would tell every user they
        // just recovered 24 GiB regardless of what was actually on it.
        let reclaimed = disk
            .metadata()
            .map(|metadata| {
                use std::os::unix::fs::MetadataExt;
                metadata.blocks() * 512
            })
            .unwrap_or(0);

        // Removed, never truncated. `create_private_sparse_file` refuses a file
        // whose length is not exactly `DATA_DISK_BYTES`, so a `set_len(0)` here
        // would leave the installation permanently unable to start with
        // "managed data disk has an unexpected size".
        remove_if_present(&disk)?;
        remove_if_present(&self.control_socket)?;
        Ok(reclaimed)
    }

    pub fn stop(&self) -> io::Result<()> {
        #[cfg(target_os = "macos")]
        {
            if let Some(mut child) = self.vm.lock().expect("VM lock poisoned").take() {
                let _ = self.request("system.shutdown", json!({}));
                let deadline = Instant::now() + Duration::from_secs(20);
                while Instant::now() < deadline {
                    if child.try_wait()?.is_some() {
                        remove_if_present(&self.vm_process_marker)?;
                        return Ok(());
                    }
                    thread::sleep(Duration::from_millis(100));
                }
                let result = unsafe { libc::kill(child.id() as libc::pid_t, libc::SIGTERM) };
                if result != 0 {
                    return Err(io::Error::last_os_error());
                }
                let deadline = Instant::now() + Duration::from_secs(5);
                while Instant::now() < deadline {
                    if child.try_wait()?.is_some() {
                        remove_if_present(&self.vm_process_marker)?;
                        return Ok(());
                    }
                    thread::sleep(Duration::from_millis(100));
                }
                child.kill()?;
                child.wait()?;
                remove_if_present(&self.vm_process_marker)?;
            } else {
                self.reclaim_owned_macos_vm()?;
            }
        }
        #[cfg(windows)]
        {
            // WSL's private distribution is terminated by the host rather
            // than systemd poweroff. Ask the guest to stop every managed
            // container first so databases and sandboxes flush cleanly.
            let _ = self.request("system.shutdown", json!({}));
            // Through the shared runner rather than its own `.output()`. This
            // was a second unbounded wait, on the path where a hang is most
            // visible to a person: they are watching a window refuse to close.
            self.wsl(&["--terminate", self.wsl_distribution()], None)?;
        }
        Ok(())
    }

    pub fn capability_file(&self) -> &Path {
        &self.capability_file
    }

    pub fn control_socket(&self) -> &Path {
        &self.control_socket
    }

    #[cfg(target_os = "macos")]
    pub fn service_socket(&self, port: u16) -> PathBuf {
        self.config
            .local_root
            .join(format!("run/service-{port}.sock"))
    }

    fn ensure_capability(&self) -> io::Result<()> {
        if self.capability_file.is_file() {
            let current = fs::read_to_string(&self.capability_file)?;
            if current.trim().len() == CAPABILITY_BYTES * 2 {
                return ensure_private_file(&self.capability_file);
            }
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "managed guest capability is corrupt",
            ));
        }
        let mut bytes = [0_u8; CAPABILITY_BYTES];
        getrandom::fill(&mut bytes)
            .map_err(|error| io::Error::other(format!("secure randomness failed: {error}")))?;
        let value: String = bytes.iter().map(|byte| format!("{byte:02x}")).collect();
        write_private_atomic(&self.capability_file, value.as_bytes())
    }

    /// Whether the guest reported its data disk as unmountable, and why.
    ///
    /// Read from the serial console, which the guest writes to before anything
    /// it could report over is running -- `lemma-guestd` requires the mount
    /// that just failed. That makes the console the only channel available for
    /// this class of failure, and it is already a Diagnostics source.
    #[cfg(target_os = "macos")]
    /// Only this boot's console is consulted -- see the rotation in `start`,
    /// which is what makes that true.
    fn guest_needs_data_repair(&self) -> Option<String> {
        const MARKER: &str = "lemma-data: needs-repair:";
        let console = self.config.local_root.join("runtime/macos/console.log");
        let text = fs::read_to_string(console).ok()?;
        text.lines()
            .rev()
            .find_map(|line| line.split_once(MARKER))
            .map(|(_, reason)| reason.trim().to_owned())
    }

    pub fn check_guest_kernel(&self) -> io::Result<()> {
        #[cfg(target_os = "macos")]
        kernel_health::check_console(&self.config.local_root.join("runtime/macos/console.log"))?;
        Ok(())
    }

    fn wait_ready(&self) -> io::Result<ManagedRuntimeStatus> {
        let deadline = Instant::now() + Duration::from_secs(120);
        let mut last_error = None;
        while Instant::now() < deadline {
            #[cfg(target_os = "macos")]
            self.check_guest_kernel()?;
            #[cfg(target_os = "macos")]
            if let Some(error) = self.macos_exit_error()? {
                return Err(error);
            }
            // A guest that has decided its data disk needs repair will never
            // answer: `lemma-data.service` failed, and `lemma-guestd.service`
            // requires it. Waiting out the remaining budget would turn a known,
            // named problem into "did not become ready".
            #[cfg(target_os = "macos")]
            if let Some(reason) = self.guest_needs_data_repair() {
                return Err(io::Error::other(format!(
                    "Lemma's private data disk needs repair: {reason}; {DATA_RESET_MARKER}"
                )));
            }
            match self.health() {
                Ok(status) => return Ok(status),
                Err(error) => last_error = Some(error),
            }
            thread::sleep(Duration::from_millis(250));
        }
        Err(last_error.unwrap_or_else(|| {
            io::Error::new(
                io::ErrorKind::TimedOut,
                "managed guest did not become ready",
            )
        }))
    }

    /// Put the guest's wall clock back on this machine's.
    ///
    /// The guest sets its time once, at boot, from the trusted control share.
    /// A Virtualization.framework VM does not run while the Mac sleeps, so
    /// every hour the lid is closed is an hour the guest clock falls behind and
    /// never makes up. Callers run this on a cadence and after a detected
    /// sleep; the guest reports the gap it found, so a correction worth knowing
    /// about can be logged.
    ///
    /// The control-share file is rewritten too. It is what the *next* boot
    /// reads, and leaving it on the epoch of the install would hand a freshly
    /// booted guest a clock that is already stale.
    pub fn sync_clock(&self) -> io::Result<Value> {
        #[cfg(target_os = "macos")]
        self.refresh_host_epoch()?;
        let epoch = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|error| io::Error::other(format!("host clock is invalid: {error}")))?
            .as_secs();
        self.request("system.clock", json!({"epoch": epoch}))
    }

    #[cfg(target_os = "macos")]
    fn refresh_host_epoch(&self) -> io::Result<()> {
        let epoch = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|error| io::Error::other(format!("host clock is invalid: {error}")))?
            .as_secs();
        write_private_atomic(&self.host_epoch_file, format!("{epoch}\n").as_bytes())
    }

    #[cfg(target_os = "macos")]
    fn start_macos(&self) -> io::Result<()> {
        let release = self.config.artifact_root.join("macos-aarch64");
        validate_macos_release(&release)?;
        let state = self.config.local_root.join("runtime/macos");
        fs::create_dir_all(&state)?;
        set_private_directory(&state)?;
        let mut guard = self.vm.lock().expect("VM lock poisoned");
        if let Some(child) = guard.as_mut() {
            if child.try_wait()?.is_none() {
                return Ok(());
            }
            remove_if_present(&self.vm_process_marker)?;
        }
        self.reclaim_owned_macos_vm()?;
        // Rewritten every boot, so the marker always describes *this* start
        // rather than some earlier one. A stale "fresh" marker is the one thing
        // that would let the guest format a disk holding user data.
        let disk_is_fresh = create_private_sparse_file(&state.join("data.raw"), DATA_DISK_BYTES)?;
        if disk_is_fresh {
            write_private_atomic(&self.data_disk_fresh_marker, b"1\n")?;
        } else {
            remove_if_present(&self.data_disk_fresh_marker)?;
        }
        remove_if_present(&self.control_socket)?;
        for port in [5432, 6379, 3567] {
            remove_if_present(&self.service_socket(port))?;
        }
        let log_path = self.config.local_root.join("logs/vz.log");
        rotate_log(&log_path, 5 * 1024 * 1024)?;
        // Unconditionally, not at 5 MiB. `guest_needs_data_repair` scans this
        // file for `lemma-data: needs-repair:` and the file is append-only, so
        // one bad boot condemned every boot after it -- including the boot that
        // follows a successful reset, which found the *old* line and offered the
        // same reset again. Forever, with only a full reinstall to escape.
        //
        // Kept as `.previous.log` rather than deleted: the run that failed is
        // exactly the one somebody wants to read, and it is one boot of history
        // either way.
        rotate_log(&state.join("console.log"), 0)?;
        let mut child = Command::new(&self.config.vz_executable)
            .arg("serve")
            .arg("--runtime")
            .arg(&state)
            .arg("--release")
            .arg(&release)
            .arg("--control-socket")
            .arg(&self.control_socket)
            .arg("--control-share")
            .arg(
                self.capability_file
                    .parent()
                    .ok_or_else(|| io::Error::other("capability parent is missing"))?,
            )
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::from(private_appending_log(&log_path)?))
            .spawn()?;
        if let Err(error) = self.record_macos_vm(&child) {
            let _ = child.kill();
            let _ = child.wait();
            return Err(error);
        }
        *guard = Some(child);
        Ok(())
    }

    #[cfg(target_os = "macos")]
    fn record_macos_vm(&self, child: &Child) -> io::Result<()> {
        let identity = process_identity(child.id())?;
        let expected = self.config.vz_executable.canonicalize()?;
        if Path::new(&identity.executable).canonicalize()? != expected {
            return Err(io::Error::other(
                "VM helper executable did not match the app-owned runtime",
            ));
        }
        write_private_atomic(
            &self.vm_process_marker,
            &serde_json::to_vec_pretty(&VmProcessMarker {
                schema_version: VM_PROCESS_MARKER_SCHEMA_VERSION,
                pid: child.id(),
                executable: identity.executable,
                start_identity: identity.start_identity,
            })?,
        )
    }

    /// Terminate a VM helper this installation left behind, verified by
    /// identity rather than by name.
    ///
    /// Public so `lemma-locald reset` can reach it. That path runs when the
    /// daemon that owned the VM is already gone, so the marker on disk is the
    /// only way to find the helper -- and matching by name (`pkill -x
    /// lemma-vz`) would kill a developer's separate dev-root VM, or another
    /// installation's.
    #[cfg(target_os = "macos")]
    pub fn reclaim_owned_macos_vm(&self) -> io::Result<()> {
        self.reclaim_macos_vm(true)
    }

    /// Destructive recovery also handles a helper from a replaced app bundle.
    /// Its recorded executable and start identity still have to match the
    /// running process; its path need not match the newly installed binary.
    #[cfg(target_os = "macos")]
    pub fn reclaim_owned_macos_vm_for_reset(&self) -> io::Result<()> {
        self.reclaim_macos_vm(false)
    }

    #[cfg(target_os = "macos")]
    fn reclaim_macos_vm(&self, require_current_executable: bool) -> io::Result<()> {
        let raw = match fs::read(&self.vm_process_marker) {
            Ok(raw) if raw.len() <= 64 * 1024 => raw,
            Ok(_) => return Ok(()),
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(()),
            Err(error) => return Err(error),
        };
        let Ok(marker) = serde_json::from_slice::<VmProcessMarker>(&raw) else {
            return Ok(());
        };
        if marker.schema_version != VM_PROCESS_MARKER_SCHEMA_VERSION {
            return Ok(());
        }
        let identity = match process_identity(marker.pid) {
            Ok(identity) => identity,
            Err(error) if error.kind() == io::ErrorKind::NotFound => {
                return remove_if_present(&self.vm_process_marker);
            }
            Err(error) => return Err(error),
        };
        if identity.executable == marker.executable
            && identity.start_identity == marker.start_identity
        {
            if require_current_executable {
                let expected = self.config.vz_executable.canonicalize()?;
                if !Path::new(&identity.executable)
                    .canonicalize()
                    .is_ok_and(|actual| actual == expected)
                {
                    return Err(io::Error::other("the running VM belongs to a different app release; use confirmed installation cleanup"));
                }
            }
            terminate_verified_process(marker.pid)?;
        }
        remove_if_present(&self.vm_process_marker)
    }

    #[cfg(target_os = "macos")]
    fn macos_exit_error(&self) -> io::Result<Option<io::Error>> {
        let mut guard = self.vm.lock().expect("VM lock poisoned");
        let Some(child) = guard.as_mut() else {
            return Ok(None);
        };
        let Some(status) = child.try_wait()? else {
            return Ok(None);
        };
        *guard = None;
        remove_if_present(&self.vm_process_marker)?;
        let log = fs::read(self.config.local_root.join("logs/vz.log")).unwrap_or_default();
        let detail = last_diagnostic(&log, "the runtime log holds no explanation");
        Ok(Some(io::Error::other(format!(
            "Lemma's private runtime exited ({status}): {detail}"
        ))))
    }

    pub fn wsl_distribution(&self) -> &str {
        &self.config.wsl_distribution
    }

    /// Where the identity of the imported guest is recorded.
    #[cfg(windows)]
    fn guest_release_marker(&self) -> PathBuf {
        self.config
            .local_root
            .join("runtime/wsl/.lemma-guest-release")
    }

    /// A cheap identity for the rootfs archive an installed guest came from.
    ///
    /// Run wsl.exe and hand back whatever it produced, exit code included.
    ///
    /// `wsl()` turns a non-zero exit into an error, which is right for a command
    /// whose success is the point. It is wrong for a query whose failure is
    /// itself an answer -- `--terminate` on a distribution that is not running,
    /// or `journalctl` in a guest that never came up.
    ///
    /// This is the real runner; `wsl()` is this plus the status check. It used
    /// to be the other way round, and the wrapper's two match arms were both
    /// `Err(error) => Err(error)` -- identical to calling `wsl()` directly,
    /// which at the time did not check the status either. So the doc comment
    /// above described a contract that neither function had.
    #[cfg(windows)]
    fn wsl_allowing_failure(
        &self,
        arguments: &[&str],
        input: Option<&[u8]>,
    ) -> io::Result<std::process::Output> {
        run_wsl_command(
            &self.config.wsl_executable,
            arguments,
            input,
            wsl_budget(arguments),
            &self.config.local_root.join("logs/wsl.log"),
        )
    }

    /// Whether the imported guest came from this release's rootfs.
    ///
    /// The distribution's name says nothing about which release built it, so
    /// an upgrade used to skip the import and leave the new host talking to
    /// the old guestd. Every guest operation then failed as "could not reach
    /// Lemma's private runtime", with nothing pointing at the cause.
    #[cfg(windows)]
    fn installed_guest_is_current(&self, rootfs: &Path) -> io::Result<bool> {
        if !rootfs.is_file() {
            // Nothing to compare against. An installed guest with no artifact
            // is the repair path's problem, not this one's.
            return Ok(true);
        }
        let marker = self.guest_release_marker();
        let recorded = fs::read_to_string(&marker).unwrap_or_default();
        let current = rootfs_stamp(rootfs)?;
        if recorded == current {
            return Ok(true);
        }
        if recorded.is_empty() {
            // A distribution imported before this marker existed. Adopt it
            // rather than declaring every existing installation broken.
            fs::write(&marker, &current)?;
            return Ok(true);
        }
        Ok(false)
    }

    /// Where Lemma's data lives on Windows, and why it is a second guest.
    ///
    /// The runtime distribution is replaced wholesale by every upgrade, so
    /// nothing that must survive one can live inside it -- and until this
    /// existed, everything did: `/var/lib/lemma`, the container store and the
    /// volumes were all on that distribution's own ext4.vhdx. An upgrade could
    /// therefore only refuse, and it did, telling people to reset the runtime
    /// and lose every workspace, database and pod. That refusal was correct
    /// and useless: it left Windows users pinned to whichever release they
    /// installed first.
    ///
    /// macOS attaches a second disk. WSL cannot: `wsl --mount --vhd` needs
    /// administrator rights, and Lemma does not ask for them. What WSL does
    /// give is `/mnt/wsl`, a tmpfs shared by every distribution in the same
    /// VM, and a bind published into it from one distribution is readable and
    /// writable from another. So the data gets a distribution of its own,
    /// whose ext4.vhdx no upgrade touches, and it publishes itself there.
    ///
    /// Measured on a Windows machine before any of this was written: importing
    /// the second distribution from the rootfs already on disk takes 1.9s; the
    /// share is readable from the runtime distribution immediately; it stays
    /// readable and writable after the data distribution goes idle and is
    /// reported Stopped, and even after an explicit `--terminate`, because the
    /// mount belongs to the VM rather than to the distribution's processes.
    /// It does not survive `wsl --shutdown`, which is why publishing runs on
    /// every start rather than once at import.
    #[cfg(windows)]
    fn data_distribution(&self) -> String {
        format!("{}Data", self.wsl_distribution())
    }

    /// Import the data distribution if it is absent, and publish its share.
    ///
    /// Publishing is idempotent and unconditional: the share lives in the WSL
    /// VM, and anything that stops the VM -- `wsl --shutdown`, a reboot --
    /// takes it with it while leaving the distribution registered.
    #[cfg(windows)]
    fn ensure_data_distribution(&self, rootfs: &Path) -> io::Result<()> {
        let distribution = self.data_distribution();
        if !self.guest_is_registered(&distribution) {
            if !rootfs.is_file() {
                return Err(io::Error::new(
                    io::ErrorKind::NotFound,
                    format!("private WSL rootfs is missing: {}", rootfs.display()),
                ));
            }
            let install = self.config.local_root.join("runtime/wsl-data");
            fs::create_dir_all(&install)?;
            self.wsl(
                &[
                    "--import",
                    &distribution,
                    &install.to_string_lossy(),
                    &rootfs.to_string_lossy(),
                    "--version",
                    "2",
                ],
                None,
            )?;
        }
        self.wsl(
            &[
                "--distribution",
                &distribution,
                "--user",
                "root",
                "--exec",
                "/bin/sh",
                "-c",
                PUBLISH_DATA_SHARE,
            ],
            None,
        )?;
        Ok(())
    }

    /// Move an existing installation's data out of the runtime distribution.
    ///
    /// Runs in whichever distribution currently holds the data, before
    /// anything replaces it, and does nothing at all once the holder says it
    /// is ready. A fresh install runs it too and copies nothing: what it is
    /// really doing there is recording that the holder is now the home, so the
    /// first upgrade afterwards knows it may replace the runtime.
    #[cfg(windows)]
    fn migrate_data_into_holder(&self) -> io::Result<()> {
        self.wsl(
            &[
                "--distribution",
                self.wsl_distribution(),
                "--user",
                "root",
                "--exec",
                "/bin/sh",
                "-c",
                MIGRATE_DATA_INTO_HOLDER,
            ],
            None,
        )?;
        Ok(())
    }

    /// Replace the runtime distribution with this release's rootfs.
    ///
    /// The gate is not a formality. `--unregister` deletes an ext4.vhdx and
    /// everything in it, so this refuses to run until the holder itself says
    /// the data is out -- asked of the holder, not inferred from a file on the
    /// Windows side that could be stale, or from having just run the migration
    /// and assumed it worked.
    #[cfg(windows)]
    fn replace_runtime_distribution(&self, install: &Path, rootfs: &Path) -> io::Result<()> {
        refuse_replacement_without_holder(self.data_holder_is_ready()?)?;
        let _ = self.wsl_allowing_failure(&["--terminate", self.wsl_distribution()], None);
        self.wsl(&["--unregister", self.wsl_distribution()], None)?;
        self.wsl(
            &[
                "--import",
                self.wsl_distribution(),
                &install.to_string_lossy(),
                &rootfs.to_string_lossy(),
                "--version",
                "2",
            ],
            None,
        )?;
        fs::write(self.guest_release_marker(), rootfs_stamp(rootfs)?)?;
        Ok(())
    }

    /// Ask the holder whether it holds the data.
    #[cfg(windows)]
    fn data_holder_is_ready(&self) -> io::Result<bool> {
        let output = self.wsl_allowing_failure(
            &[
                "--distribution",
                &self.data_distribution(),
                "--user",
                "root",
                "--exec",
                "/bin/sh",
                "-c",
                "test -f /data/.lemma-data-holder",
            ],
            None,
        )?;
        Ok(output.status.success())
    }

    /// Remove the private distributions, and everything inside them.
    ///
    /// Both of them, and that is the whole point now: the data moved out of
    /// the runtime distribution into a holder of its own so that upgrades stop
    /// destroying it, and a wipe that removed only the runtime would leave
    /// every workspace, database and volume registered and full while the
    /// dialog above it said "Everything Lemma keeps on this PC is deleted".
    /// That exact sentence was false once before, for the same reason.
    ///
    /// The only lifecycle verbs used to be `--import` and `--terminate`, so a
    /// corrupt guest could not be rebuilt from inside the app and uninstalling
    /// Lemma left a registered distribution and a multi-gigabyte ext4.vhdx that
    /// only `wsl --unregister` from a terminal could remove.
    ///
    /// This destroys guest state. Callers must have asked first.
    #[cfg(windows)]
    pub fn unregister_windows_guest(&self) -> io::Result<()> {
        let output = match self.wsl_allowing_failure(&["--list", "--quiet"], None) {
            Ok(output) => output,
            Err(error)
                if error.kind() == io::ErrorKind::NotFound
                    && !self.config.local_root.join("runtime/wsl").exists() =>
            {
                return Ok(())
            }
            Err(error) => return Err(error),
        };
        // An unavailable WSL service is not evidence that the distribution is
        // absent. Preserve its registration and cleanup records on ambiguity.
        if registered_guest(
            output.status.success(),
            &output.stdout,
            self.wsl_distribution(),
        )? {
            let _ = self.wsl_allowing_failure(&["--terminate", self.wsl_distribution()], None);
            self.wsl(&["--unregister", self.wsl_distribution()], None)?;
        }
        let data = self.data_distribution();
        if registered_guest(output.status.success(), &output.stdout, &data)? {
            let _ = self.wsl_allowing_failure(&["--terminate", &data], None);
            self.wsl(&["--unregister", &data], None)?;
        }
        let _ = fs::remove_file(self.guest_release_marker());
        Ok(())
    }

    /// Whether WSL lists a distribution by this exact name.
    ///
    /// Deliberately not `?`. `wsl --list --quiet` exits non-zero when there are
    /// no distributions at all -- which is exactly the state
    /// prepare_windows_host engineers with `--install --no-distribution` -- so
    /// treating that as fatal aborted the very first start before the import
    /// could ever run, permanently.
    ///
    /// A false "absent" is safe here, and that is not an assumption: `--import`
    /// refuses a name that already exists rather than replacing it, so the
    /// mistake surfaces as an error from the next command and never as a
    /// distribution that was overwritten.
    #[cfg(windows)]
    fn guest_is_registered(&self, name: &str) -> bool {
        self.wsl_allowing_failure(&["--list", "--quiet"], None)
            .map(|output| {
                decode_wsl_output(&output.stdout)
                    .lines()
                    .any(|line| line.trim() == name)
            })
            .unwrap_or(false)
    }

    #[cfg(windows)]
    fn start_windows(&self) -> io::Result<()> {
        if !self.windows_wsl_ready() {
            let pending = self.wsl_setup_marker().is_file();
            return Err(io::Error::new(
                io::ErrorKind::NotFound,
                if pending {
                    "Windows must restart to finish enabling WSL 2; restart Windows, then reopen Lemma"
                } else {
                    "WSL 2 is required for Lemma's private runtime; choose Set up Windows runtime and approve the Windows prompt"
                },
            ));
        }
        let _ = fs::remove_file(self.wsl_setup_marker());
        let install = self.config.local_root.join("runtime/wsl");
        fs::create_dir_all(&install)?;
        let rootfs = self.config.artifact_root.join("windows-x86_64/rootfs.tar");
        // The holder first, and its share published, because the runtime
        // distribution's init refuses to start without it -- and because the
        // upgrade below is only safe once the data is somewhere else.
        self.ensure_data_distribution(&rootfs)?;
        let installed = self.guest_is_registered(self.wsl_distribution());
        if !installed {
            if !rootfs.is_file() {
                return Err(io::Error::new(
                    io::ErrorKind::NotFound,
                    format!("private WSL rootfs is missing: {}", rootfs.display()),
                ));
            }
            let install_path = install.to_string_lossy().into_owned();
            let rootfs_path = rootfs.to_string_lossy().into_owned();
            self.wsl(
                &[
                    "--import",
                    self.wsl_distribution(),
                    &install_path,
                    &rootfs_path,
                    "--version",
                    "2",
                ],
                None,
            )?;
            let stamp = rootfs_stamp(&rootfs)?;
            fs::write(self.guest_release_marker(), stamp)?;
            // Copies nothing here. It records that the holder is the home, so
            // the first upgrade after this one knows it may replace the
            // runtime distribution.
            self.migrate_data_into_holder()?;
        } else {
            // Always before the replacement, and against the distribution that
            // currently holds the data rather than the one about to.
            self.migrate_data_into_holder()?;
            if !self.installed_guest_is_current(&rootfs)? {
                self.replace_runtime_distribution(&install, &rootfs)?;
            }
        }
        let capability = fs::read(&self.capability_file)?;
        self.wsl(
            &[
                "--distribution",
                self.wsl_distribution(),
                "--user",
                "root",
                "--exec",
                "/bin/sh",
                "-c",
                "umask 077; mkdir -p /etc/lemma; cat > /etc/lemma/guest.capability",
            ],
            Some(&capability),
        )?;
        self.wsl(
            &[
                "--distribution",
                self.wsl_distribution(),
                "--user",
                "root",
                "--exec",
                "/usr/local/bin/lemma-runtime-init",
            ],
            None,
        )?;
        Ok(())
    }

    /// Whether WSL 2 is installed and its service will answer.
    ///
    /// The first wsl.exe call `start_windows` makes, and so the one a wedged
    /// WSL blocks first. It ran unbounded and discarded stderr, which is the
    /// worst combination available: the start path stopped here with nothing
    /// written anywhere. Now it is bounded, and logged like every other call.
    #[cfg(windows)]
    fn windows_wsl_ready(&self) -> bool {
        self.wsl_allowing_failure(&["--status"], None)
            .is_ok_and(|output| output.status.success())
    }

    #[cfg(windows)]
    fn wsl_setup_marker(&self) -> PathBuf {
        self.config
            .local_root
            .join("runtime/wsl-setup-pending.json")
    }

    #[cfg(windows)]
    fn prepare_windows_host(&self) -> io::Result<Value> {
        if self.windows_wsl_ready() {
            let _ = fs::remove_file(self.wsl_setup_marker());
            return Ok(json!({
                "ready": true,
                "reboot_required": false,
                "platform": "windows",
            }));
        }
        write_private_atomic(
            &self.wsl_setup_marker(),
            br#"{"schema_version":1,"operation":"wsl-install"}"#,
        )?;
        let script = concat!(
            "$ErrorActionPreference='Stop'; ",
            "try { $p=Start-Process -FilePath (Join-Path $env:WINDIR 'System32\\wsl.exe') ",
            "-ArgumentList @('--install','--no-distribution','--no-launch') ",
            "-Verb RunAs -Wait -PassThru; exit $p.ExitCode } ",
            "catch { Write-Error 'Windows administrator approval was cancelled or failed'; exit 1223 }"
        );
        let status = match Command::new("powershell.exe")
            .no_console_window()
            .args([
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
        {
            Ok(status) => status,
            Err(error) => {
                let _ = fs::remove_file(self.wsl_setup_marker());
                return Err(error);
            }
        };
        if !status.success() {
            let _ = fs::remove_file(self.wsl_setup_marker());
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "Windows did not approve or complete WSL 2 setup",
            ));
        }
        let ready = self.windows_wsl_ready();
        if ready {
            let _ = fs::remove_file(self.wsl_setup_marker());
        }
        Ok(json!({
            "ready": ready,
            "reboot_required": !ready,
            "platform": "windows",
        }))
    }

    /// Run wsl.exe and fail unless it succeeded.
    ///
    /// The status check is the whole point of this wrapper, and for a long time
    /// it was missing: every caller treated a non-zero `wsl.exe` exit as
    /// success. A failed `--import` returned Ok, `start_windows` then wrote the
    /// guest release marker recording a distribution that had never been
    /// created, and the user waited out the 120s `wait_ready` timeout to be told
    /// only that the runtime did not come up. The cause was in logs/wsl.log and
    /// nowhere else.
    ///
    /// `wsl.exe` writes its diagnostics as UTF-16, so the message is decoded
    /// rather than passed through as bytes.
    #[cfg(windows)]
    fn wsl(&self, arguments: &[&str], input: Option<&[u8]>) -> io::Result<std::process::Output> {
        let output = self.wsl_allowing_failure(arguments, input)?;
        if !output.status.success() {
            let message = wsl_message(&output.stderr);
            let detail = if message.trim().is_empty() {
                format!("wsl.exe {} failed ({})", arguments.join(" "), output.status)
            } else {
                format!(
                    "wsl.exe {} failed ({}): {}",
                    arguments.join(" "),
                    output.status,
                    message.trim()
                )
            };
            return Err(io::Error::other(detail));
        }
        Ok(output)
    }
}

fn cache_repair_required(error: &io::Error) -> bool {
    error
        .to_string()
        .contains("container cache repair required")
}

#[cfg(target_os = "macos")]
fn validate_macos_release(source: &Path) -> io::Result<()> {
    let source_marker = source.join("runtime.json");
    if !source_marker.is_file() {
        return Err(io::Error::new(
            io::ErrorKind::NotFound,
            format!(
                "managed runtime metadata is missing: {}",
                source_marker.display()
            ),
        ));
    }
    for name in ["vmlinuz", "initrd", "disk.raw"] {
        let path = source.join(name);
        if !path.is_file() || path.metadata()?.len() == 0 {
            return Err(io::Error::new(
                io::ErrorKind::NotFound,
                format!("managed runtime artifact is missing: {}", path.display()),
            ));
        }
    }
    let metadata: Value = serde_json::from_slice(&fs::read(&source_marker)?)?;
    if metadata
        .get("service_transport_version")
        .and_then(Value::as_u64)
        != Some(1)
    {
        return Err(io::Error::new(
            io::ErrorKind::Unsupported,
            "This local runtime does not support the app's private service transport. Install the matching runtime update, then retry. Your stored data has not been changed.",
        ));
    }
    Ok(())
}

#[cfg(target_os = "macos")]
fn process_identity(pid: u32) -> io::Result<ProcessIdentity> {
    let pid = pid.to_string();
    let executable = Command::new("/bin/ps")
        .args(["-p", &pid, "-o", "comm="])
        .output()?;
    let started = Command::new("/bin/ps")
        .args(["-p", &pid, "-o", "lstart="])
        .output()?;
    if !executable.status.success() || !started.status.success() {
        return Err(io::Error::new(io::ErrorKind::NotFound, "process not found"));
    }
    let executable = String::from_utf8(executable.stdout)
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))?;
    let executable = Path::new(executable.trim())
        .canonicalize()?
        .to_string_lossy()
        .into_owned();
    let start_identity = String::from_utf8(started.stdout)
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))?
        .trim()
        .to_owned();
    if start_identity.is_empty() {
        return Err(io::Error::other("process start identity was empty"));
    }
    Ok(ProcessIdentity {
        executable,
        start_identity,
    })
}

#[cfg(target_os = "macos")]
fn terminate_verified_process(pid: u32) -> io::Result<()> {
    let pid = i32::try_from(pid).map_err(|_| io::Error::other("invalid process id"))?;
    // SAFETY: the caller matched the recorded executable and OS start identity.
    let result = unsafe { libc::kill(pid, libc::SIGTERM) };
    if result != 0 {
        let error = io::Error::last_os_error();
        if error.raw_os_error() == Some(libc::ESRCH) {
            return Ok(());
        }
        return Err(error);
    }
    let deadline = Instant::now() + Duration::from_secs(10);
    while Instant::now() < deadline {
        // SAFETY: signal zero only checks whether this exact PID still exists.
        if unsafe { libc::kill(pid, 0) } != 0 {
            return Ok(());
        }
        thread::sleep(Duration::from_millis(50));
    }
    // SAFETY: identity was checked immediately before termination.
    if unsafe { libc::kill(pid, libc::SIGKILL) } != 0 {
        let error = io::Error::last_os_error();
        if error.raw_os_error() != Some(libc::ESRCH) {
            return Err(error);
        }
    }
    let deadline = Instant::now() + Duration::from_secs(5);
    while Instant::now() < deadline {
        // Wait for launchd to reap an orphaned helper. Starting a replacement
        // as soon as SIGKILL is delivered can race Virtualization.framework's
        // release of the exclusive data-disk attachment.
        if unsafe { libc::kill(pid, 0) } != 0 {
            thread::sleep(Duration::from_millis(500));
            return Ok(());
        }
        thread::sleep(Duration::from_millis(50));
    }
    Err(io::Error::new(
        io::ErrorKind::TimedOut,
        "terminated VM helper was not reaped",
    ))
}

#[cfg(target_os = "macos")]
fn remove_if_present(path: &Path) -> io::Result<()> {
    match fs::remove_file(path) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error),
    }
}

#[cfg(target_os = "macos")]
/// Returns whether the disk was created by *this* call.
///
/// Only the host knows that. The guest sees a block device either way, and it
/// has to decide whether an unrecognised one is a brand-new disk to format or
/// user data it must not touch -- so the answer is written into the control
/// share for the boot script to read.
fn create_private_sparse_file(path: &Path, size: u64) -> io::Result<bool> {
    if path.exists() {
        if path.metadata()?.len() != size {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                format!(
                    "managed data disk has an unexpected size: {}",
                    path.display()
                ),
            ));
        }
        ensure_private_file(path)?;
        return Ok(false);
    }
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    use std::os::unix::fs::OpenOptionsExt;
    options.mode(0o600);
    let file = options.open(path)?;
    file.set_len(size)?;
    file.sync_all()?;
    ensure_private_file(path)?;
    Ok(true)
}

fn write_private_atomic(path: &Path, contents: &[u8]) -> io::Result<()> {
    let parent = path
        .parent()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "path has no parent"))?;
    fs::create_dir_all(parent)?;
    let temporary = path.with_extension(format!("tmp-{}", std::process::id()));
    let _ = fs::remove_file(&temporary);
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(&temporary)?;
    file.write_all(contents)?;
    file.sync_all()?;
    fs::rename(temporary, path)?;
    ensure_private_file(path)
}

fn set_private_directory(path: &Path) -> io::Result<()> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o700))?;
    }
    #[cfg(not(unix))]
    let _ = path;
    Ok(())
}

fn ensure_private_file(path: &Path) -> io::Result<()> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        let metadata = fs::symlink_metadata(path)?;
        if !metadata.file_type().is_file() || metadata.mode() & 0o077 != 0 {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                format!(
                    "private runtime file has unsafe permissions: {}",
                    path.display()
                ),
            ));
        }
    }
    #[cfg(not(unix))]
    let _ = path;
    Ok(())
}

fn private_appending_log(path: &Path) -> io::Result<std::fs::File> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let mut options = OpenOptions::new();
    options.create(true).append(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    options.open(path)
}

fn rotate_log(path: &Path, max_bytes: u64) -> io::Result<()> {
    if path
        .metadata()
        .is_ok_and(|metadata| metadata.len() >= max_bytes)
    {
        // Copy aside and truncate in place, rather than rename and let a new
        // file appear. The writer is a running child holding this handle: after
        // a rename it keeps writing into the rotated file, so the live log
        // stops growing and the rotated one never stops. Removing the previous
        // file first also fails outright on Windows if anything still has it
        // open. Truncating the file the writer already holds moves it back to
        // zero without either problem.
        fs::copy(path, path.with_extension("previous.log"))?;
        OpenOptions::new().write(true).open(path)?.set_len(0)?;
    }
    Ok(())
}

/// Lines that mean "still booting", not "went wrong".
///
/// The host dials the guest's control socket before guestd is listening, so a
/// normal boot always writes several of these. They are the *first* thing in
/// `vz.log`, which is why quoting the first line reported a healthy boot's retry
/// as the cause of an exit that happened minutes later.
#[cfg(any(target_os = "macos", test))]
fn is_boot_retry(line: &str) -> bool {
    line.contains("guest connect failed")
}

fn first_diagnostic(value: &[u8], fallback: &str) -> String {
    let value = String::from_utf8_lossy(value);
    let diagnostic = value
        .lines()
        .map(str::trim)
        .find(|line| !line.is_empty())
        .unwrap_or(fallback);
    diagnostic
        .strip_prefix("lemma-runtime: ")
        .unwrap_or(diagnostic)
        .to_owned()
}

/// Why the runtime most recently complained.
///
/// An exit is explained by what the log said last, not first. Boot retries are
/// skipped entirely: if they are all there is, the log holds no explanation and
/// saying so is more honest than quoting one.
#[cfg(any(target_os = "macos", test))]
fn last_diagnostic(value: &[u8], fallback: &str) -> String {
    let value = String::from_utf8_lossy(value);
    let diagnostic = value
        .lines()
        .map(str::trim)
        .rfind(|line| !line.is_empty() && !is_boot_retry(line))
        .unwrap_or(fallback);
    diagnostic
        .strip_prefix("lemma-runtime: ")
        .unwrap_or(diagnostic)
        .to_owned()
}

#[cfg(any(windows, test))]
fn registered_guest(success: bool, output: &[u8], distribution: &str) -> io::Result<bool> {
    if !success {
        return Err(io::Error::other("Windows could not list its local runtimes. The installation has been kept. Restart Windows and retry Recovery."));
    }
    Ok(decode_wsl_output(output)
        .lines()
        .any(|line| line.trim() == distribution))
}

#[cfg(any(windows, test))]
fn decode_wsl_output(value: &[u8]) -> String {
    let decoded = if value.len() >= 2 && value.iter().skip(1).step_by(2).any(|byte| *byte == 0) {
        // `as_chunks`, not `chunks_exact(2)`: clippy 1.98 rejects a constant
        // chunk size, and the typed pair drops the indexing this used to do.
        let (pairs, _odd_trailing_byte) = value.as_chunks::<2>();
        let words: Vec<u16> = pairs.iter().map(|pair| u16::from_le_bytes(*pair)).collect();
        String::from_utf16_lossy(&words).replace('\0', "")
    } else {
        String::from_utf8_lossy(value).into_owned()
    };
    // A UTF-16 BOM survives decoding as U+FEFF, and it is not whitespace, so
    // `trim()` leaves it on the first line -- enough to stop the first
    // distribution listed from ever matching its own name.
    decoded.replace('\u{feff}', "")
}

/// What to collect from a guest that has just failed.
///
/// Held in `guest-diagnostics.sh` rather than inline, because both ends need
/// the same text and neither can import the other: Windows runs it through
/// `wsl.exe`, and macOS has no exec channel at all, so `lemma-guestd` compiles
/// the same file in and runs it from inside the guest. One file, included
/// twice, is the only arrangement where the two cannot drift.
///
/// This used to be `journalctl --lines 300`, and on Windows it collected
/// nothing at all, ever. The guest ships `/etc/wsl.conf` with
/// `systemd=false` -- deliberately, because `lemma-runtime-init` starts
/// containerd itself -- so there is no journal to read. Every Windows failure
/// wrote the literal text "-- No entries --" into `logs/guest.log` and that
/// was the whole record. A start that failed because the guest reported an
/// unreachable address left nothing on the machine to say so.
///
/// So: the logs the guest actually writes, and the two pieces of state that
/// explain most of what goes wrong here -- what addresses the guest has, and
/// which containers are up. Bounded per file, and `2>&1` throughout, because a
/// collector that fails halfway is still worth what it printed first.
/// The gate between an upgrade and somebody's work.
///
/// `--unregister` deletes a distribution's ext4.vhdx and everything in it, and
/// the upgrade path calls it deliberately. What makes that safe is only that
/// the data is already somewhere else, so this asks -- and the answer comes
/// from the holder itself, not from having just run the migration and assumed
/// it worked, and not from a file on the Windows side that could be left over
/// from an installation that no longer exists.
#[cfg(any(windows, test))]
fn refuse_replacement_without_holder(holder_ready: bool) -> io::Result<()> {
    if holder_ready {
        return Ok(());
    }
    Err(io::Error::other(
        "Lemma will not replace its private runtime until your workspaces and \
         databases have moved out of it. Start Lemma again to retry; nothing \
         has been changed.",
    ))
}

/// Publish the data distribution's storage into the shared namespace.
///
/// `/mnt/wsl` is a tmpfs every distribution in the WSL VM sees, and a bind
/// made into it from one is usable from the others. This is how Lemma's data
/// reaches the runtime distribution without a second disk and without asking
/// for administrator rights.
///
/// Idempotent, and run on every start rather than once at import: the mount
/// belongs to the VM, so `wsl --shutdown` or a reboot removes it while leaving
/// the distribution registered.
#[cfg(any(windows, test))]
const PUBLISH_DATA_SHARE: &str = "\
set -eu
mkdir -p /data /mnt/wsl/lemma-data
if ! /usr/bin/mountpoint -q /mnt/wsl/lemma-data; then
  /usr/bin/mount --bind /data /mnt/wsl/lemma-data
fi
";

/// Move an existing installation's data out of the runtime distribution.
///
/// Runs inside whichever distribution holds the data today. Everything before
/// the holder existed lives on that distribution's own disk at these four
/// paths; the holder's marker is written last, so an interrupted copy is
/// retried rather than mistaken for a finished one -- and the replacement that
/// deletes the old distribution refuses until that marker exists.
///
/// `cp -a` rather than a move: the source distribution is about to be deleted
/// anyway on the upgrade path, and on the path where it is not, leaving the
/// old copy in place costs disk and keeps a way back.
#[cfg(any(windows, test))]
const MIGRATE_DATA_INTO_HOLDER: &str = "\
set -eu
share=/mnt/wsl/lemma-data
if [ ! -d \"$share\" ]; then
  echo 'lemma-data: needs-repair: the data holder is not published' >&2
  exit 1
fi
if [ -f \"$share/.lemma-data-holder\" ]; then
  exit 0
fi
mkdir -p \"$share/lemma\" \"$share/containerd\" \"$share/nerdctl\" \"$share/cni/net.d\"
copy_tree() {
  source=$1
  target=$2
  if [ -d \"$source\" ] && [ -n \"$(ls -A \"$source\" 2>/dev/null)\" ]; then
    cp -a \"$source/.\" \"$target/\"
  fi
}
copy_tree /var/lib/lemma \"$share/lemma\"
copy_tree /var/lib/containerd \"$share/containerd\"
copy_tree /var/lib/nerdctl \"$share/nerdctl\"
copy_tree /etc/cni/net.d \"$share/cni/net.d\"
chmod 0700 \"$share/lemma\"
touch \"$share/.lemma-data-holder\"
";

#[cfg(any(windows, test))]
const GUEST_DIAGNOSTICS: &str = include_str!("../../guest-diagnostics.sh");

/// How long a `wsl.exe` invocation is given before it is killed.
///
/// Backstops against a hang, not performance targets, so deliberately
/// generous: a budget that is too tight fails a slow machine that would have
/// succeeded, and that is a worse trade than the wait it saves.
#[cfg(any(windows, test))]
fn wsl_budget(arguments: &[&str]) -> Duration {
    match arguments.first().copied().unwrap_or_default() {
        // Unpacks a several-hundred-megabyte rootfs onto a fresh ext4.vhdx.
        "--import" => Duration::from_secs(20 * 60),
        // Deletes that disk again.
        "--unregister" => Duration::from_secs(10 * 60),
        // Questions about state. These are what a wedged WSL service hangs,
        // and what the start path is waiting on when it does, so this budget
        // is what decides how long a broken installation stays silent.
        "--list" | "--status" | "--version" => Duration::from_secs(60),
        "--terminate" | "--shutdown" => Duration::from_secs(2 * 60),
        // `--distribution <name> --exec ...`: work inside the guest, up to and
        // including lemma-runtime-init bringing the whole stack up.
        _ => Duration::from_secs(15 * 60),
    }
}

/// Run wsl.exe under a time limit, and record what it said.
///
/// Every call used to end in `child.wait_with_output()` with nothing bounding
/// it. A wedged WSL service -- the most ordinary Windows failure there is, and
/// the state the machine is in for as long as a `wsl --shutdown` is in flight
/// -- makes even `--status` block forever. The start path then stopped dead:
/// no error, no log line, no way for the user to tell a hang from slow work,
/// and no way for the daemon to give up and say so.
///
/// Feeding stdin was unbounded in a second way. The bytes went out through a
/// blocking `write_all` *before* anything drained stdout, so a child that
/// filled its output pipe while this end was still filling its input pipe
/// deadlocked both halves. Today's only input is a 32-byte capability file, so
/// it fits; nothing said it had to keep fitting.
///
/// `lemma_desktop_process` answers both: it pumps stdin, stdout and stderr
/// concurrently, and on expiry kills the job object rather than leaving a
/// wsl.exe running that outlives the daemon which started it.
#[cfg(any(windows, test))]
fn run_wsl_command(
    executable: &Path,
    arguments: &[&str],
    input: Option<&[u8]>,
    budget: Duration,
    log_path: &Path,
) -> io::Result<std::process::Output> {
    rotate_log(log_path, 5 * 1024 * 1024)?;
    let mut command = Command::new(executable);
    command.no_console_window().args(arguments);
    let outcome = match input {
        Some(input) => lemma_desktop_process::run_with_input(
            command,
            input.to_vec(),
            budget,
            MAX_RESPONSE_BYTES,
        ),
        None => lemma_desktop_process::run(command, budget, MAX_RESPONSE_BYTES),
    };
    let mut log = private_appending_log(log_path)?;
    let output = match outcome {
        Ok(output) => output,
        Err(error) => {
            // Written before returning. The point of bounding these is that a
            // hang leaves evidence behind, and by the time the error reaches a
            // person it has usually been reshaped into something friendlier
            // that no longer names the command.
            writeln!(
                log,
                "lemma-runtime: wsl.exe {} -> {error}",
                arguments.join(" ")
            )?;
            return Err(wsl_run_failure(error, arguments, budget));
        }
    };
    writeln!(
        log,
        "lemma-runtime: wsl.exe {} -> {}",
        arguments.join(" "),
        output.status
    )?;
    if !output.stderr.is_empty() {
        writeln!(log, "{}", wsl_message(&output.stderr))?;
    }
    Ok(output)
}

/// Turn a supervision failure into the `io::Error` the callers expect.
///
/// The `Io` arm hands the original error back rather than restating it. Its
/// kind is load-bearing: `unregister_windows_guest` treats a `NotFound` from
/// spawning wsl.exe as "there is no WSL here to unregister from", and wrapping
/// it in `io::Error::other` would turn a clean uninstall into a failure.
#[cfg(any(windows, test))]
fn wsl_run_failure(
    error: lemma_desktop_process::SetupProcessError,
    arguments: &[&str],
    budget: Duration,
) -> io::Error {
    use lemma_desktop_process::SetupProcessError;
    match error {
        SetupProcessError::Io(error) => error,
        SetupProcessError::TimedOut => io::Error::new(
            io::ErrorKind::TimedOut,
            format!(
                "Windows did not answer `wsl {}` within {} seconds. WSL itself \
                 is usually stuck when this happens: run `wsl --shutdown` in a \
                 terminal, then start Lemma again.",
                arguments.join(" "),
                budget.as_secs()
            ),
        ),
        SetupProcessError::OutputLimit => io::Error::other(format!(
            "`wsl {}` produced more output than Lemma will read",
            arguments.join(" ")
        )),
        SetupProcessError::Cancelled => io::Error::new(
            io::ErrorKind::Interrupted,
            format!("`wsl {}` was cancelled", arguments.join(" ")),
        ),
    }
}

/// The first line of something wsl.exe said, in a form a person can read.
///
/// wsl.exe writes UTF-16LE. Decoding that as UTF-8 succeeds -- the NUL halves
/// are valid, they just become U+0000 -- so every WSL error reached the user as
/// text with a NUL between each letter. It also defeated the substring matching
/// that turns a message into an actionable error code, so no WSL failure could
/// ever be recognised as one.
#[cfg(any(windows, test))]
fn wsl_message(value: &[u8]) -> String {
    decode_wsl_output(value)
        .lines()
        .map(str::trim)
        .find(|line| !line.is_empty())
        .unwrap_or("managed runtime command failed")
        .to_owned()
}

#[cfg(test)]
mod tests {

    /// wsl.exe writes UTF-16LE. Decoding it as UTF-8 succeeds -- the NUL halves
    /// are valid code points -- so the result was text with a NUL between every
    /// letter, and `trim()` does not remove NUL because it is not whitespace.
    /// Every WSL error reached the user that way, and the substring matching
    /// that turns a message into an actionable error code never fired.
    #[test]
    fn wsl_errors_are_decoded_from_utf16_before_anyone_reads_them() {
        let utf16: Vec<u8> = "There is no distribution with the supplied name.\r\n"
            .encode_utf16()
            .flat_map(u16::to_le_bytes)
            .collect();
        assert_eq!(
            wsl_message(&utf16),
            "There is no distribution with the supplied name."
        );
        assert!(!wsl_message(&utf16).contains('\0'));
    }

    #[test]
    fn a_byte_order_mark_does_not_survive_into_a_distribution_name() {
        // With the BOM left on, the first distribution listed could never match
        // its own name -- so an existing guest looked absent and Lemma tried to
        // import over it.
        let mut bytes = vec![0xFF, 0xFE];
        bytes.extend("LemmaRuntime\r\n".encode_utf16().flat_map(u16::to_le_bytes));
        let listed = decode_wsl_output(&bytes);
        assert!(
            listed.lines().any(|line| line.trim() == "LemmaRuntime"),
            "decoded as {listed:?}"
        );
    }

    #[test]
    fn plain_utf8_output_is_left_alone() {
        assert_eq!(wsl_message(b"docker: not found\n"), "docker: not found");
    }
    use super::*;
    use tempfile::tempdir;

    #[cfg(target_os = "macos")]
    #[test]
    fn kernel_fault_blocks_work_before_dispatch_but_not_recovery_or_next_boot() {
        let root = tempdir().unwrap();
        let runtime = ManagedRuntime::new(ManagedRuntimeConfig {
            wsl_distribution: DEFAULT_WSL_DISTRIBUTION.to_string(),
            local_root: root.path().join("local"),
            artifact_root: root.path().join("artifacts"),
            bridge_executable: root.path().join("missing-bridge"),
            vz_executable: root.path().join("missing-vz"),
        })
        .unwrap();
        let console = runtime.config.local_root.join("runtime/macos/console.log");
        fs::create_dir_all(console.parent().unwrap()).unwrap();
        fs::write(&console, "Internal error: Oops: 0000000096000004\n").unwrap();
        for error in [
            runtime.health().unwrap_err(),
            runtime.request("container.start", json!({})).unwrap_err(),
            runtime.wait_ready().unwrap_err(),
        ] {
            assert!(
                error.to_string().contains("guest kernel crashed"),
                "{error}"
            );
        }
        for operation in ["system.shutdown", "diagnostics.logs"] {
            let error = runtime.request(operation, json!({})).unwrap_err();
            assert_eq!(error.kind(), io::ErrorKind::NotFound);
        }
        rotate_log(&console, 0).unwrap();
        runtime.check_guest_kernel().unwrap();
        assert_eq!(
            runtime
                .request("container.start", json!({}))
                .unwrap_err()
                .kind(),
            io::ErrorKind::NotFound
        );
    }

    #[test]
    fn an_exit_is_explained_by_the_last_complaint_not_the_first_boot_retry() {
        // Every healthy boot dials the guest before guestd is listening, so
        // these are always the first lines in the log. Quoting them made an
        // exit minutes later read as though a connection reset had caused it.
        let log = b"lemma-vz: guest connect failed: Connection reset by peer\n\
                    lemma-vz: guest connect failed: Connection reset by peer\n\
                    lemma-vz: disk image is corrupt\n" as &[u8];
        assert_eq!(
            last_diagnostic(log, "fallback"),
            "lemma-vz: disk image is corrupt"
        );
    }

    #[test]
    fn a_log_of_only_boot_retries_explains_nothing_and_says_so() {
        let log = b"lemma-vz: guest connect failed: Connection reset by peer\n\
                    lemma-vz: guest connect failed: Connection reset by peer\n"
            as &[u8];
        assert_eq!(
            last_diagnostic(log, "the runtime log holds no explanation"),
            "the runtime log holds no explanation"
        );
    }

    #[test]
    fn capability_is_stable_private_and_not_in_command_arguments() {
        let root = tempdir().unwrap();
        let artifacts = root.path().join("artifacts/macos-aarch64");
        fs::create_dir_all(&artifacts).unwrap();
        let config = ManagedRuntimeConfig {
            wsl_distribution: DEFAULT_WSL_DISTRIBUTION.to_string(),
            local_root: root.path().join("local"),
            artifact_root: root.path().join("artifacts"),
            bridge_executable: root.path().join("lemma-runtime"),
            #[cfg(target_os = "macos")]
            vz_executable: root.path().join("lemma-vz"),
            #[cfg(windows)]
            wsl_executable: PathBuf::from("wsl.exe"),
        };
        let runtime = ManagedRuntime::new(config).unwrap();
        runtime.ensure_capability().unwrap();
        let first = fs::read_to_string(runtime.capability_file()).unwrap();
        runtime.ensure_capability().unwrap();

        assert_eq!(first.len(), 64);
        assert_eq!(
            first,
            fs::read_to_string(runtime.capability_file()).unwrap()
        );
        ensure_private_file(runtime.capability_file()).unwrap();
    }

    /// Windows users were pinned to whichever release they installed first.
    ///
    /// The data lived inside the runtime distribution, so an upgrade could
    /// only refuse -- and it did, telling people to reset the runtime and lose
    /// every workspace, database and pod. Now the data has a distribution of
    /// its own and the upgrade replaces the runtime, which means the upgrade
    /// path calls `--unregister` on purpose. This is the only thing standing
    /// between that call and somebody's work.
    #[test]
    fn an_upgrade_will_not_delete_a_runtime_that_still_holds_the_data() {
        refuse_replacement_without_holder(true).expect("the holder has it; proceed");

        let refused = refuse_replacement_without_holder(false).unwrap_err();
        let message = refused.to_string();
        assert!(
            message.contains("workspaces") && message.contains("databases"),
            "the refusal has to say what is at stake: {message}"
        );
        assert!(
            message.contains("nothing has been changed"),
            "and that it stopped before doing any of it: {message}"
        );
    }

    /// The gate is worth nothing if it runs after the deletion.
    #[test]
    fn the_gate_runs_before_the_unregister_it_guards() {
        let source = include_str!("lib.rs").replace("\r\n", "\n");
        let start = source
            .find("fn replace_runtime_distribution")
            .expect("the replacement exists");
        let body = &source[start..];
        let end = body.find("\n    }\n").expect("the function ends");
        let body = &body[..end];

        let gate = body
            .find("refuse_replacement_without_holder")
            .expect("the replacement is gated at all");
        let unregister = body.find("\"--unregister\"").expect("it does unregister");
        assert!(
            gate < unregister,
            "the gate has to come first, or it guards nothing:\n{body}"
        );
    }

    /// "Everything Lemma keeps on this PC is deleted" has to stay true.
    ///
    /// It was false once already, when the reset removed the state directory
    /// and left the distribution registered and full. Splitting the data into
    /// its own distribution is exactly the shape of change that would make it
    /// false a second time.
    #[test]
    fn starting_over_removes_the_data_distribution_too() {
        let source = include_str!("lib.rs").replace("\r\n", "\n");
        let start = source
            .find("pub fn unregister_windows_guest")
            .expect("the wipe exists");
        let body = &source[start..];
        let end = body.find("\n    }\n").expect("the function ends");
        let body = &body[..end];

        assert!(
            body.contains("self.data_distribution()"),
            "a wipe that leaves the data holder registered deletes nothing that \
             matters:\n{body}"
        );
        assert_eq!(
            body.matches("\"--unregister\"").count(),
            2,
            "both distributions, or the promise is false again:\n{body}"
        );
    }

    /// The marker is the holder's own word that the copy finished.
    #[test]
    fn the_migration_claims_nothing_until_the_copy_is_done() {
        let script = MIGRATE_DATA_INTO_HOLDER;
        let marker = script
            .rfind(".lemma-data-holder")
            .expect("it writes the marker");
        for tree in [
            "/var/lib/lemma ",
            "/var/lib/containerd ",
            "/var/lib/nerdctl ",
            "/etc/cni/net.d ",
        ] {
            let copy = script
                .find(tree)
                .unwrap_or_else(|| panic!("{tree} has to be carried across: {script}"));
            assert!(
                copy < marker,
                "{tree} is copied after the marker that says the copy is done, \
                 so an interrupted migration would look finished: {script}"
            );
        }
        assert!(
            script.find("exit 0").expect("it is idempotent") < marker,
            "a second run has to stop before copying over what it already moved"
        );
    }

    /// Publishing runs on every start, so it has to survive running twice.
    #[test]
    fn publishing_the_data_share_is_idempotent() {
        let script = PUBLISH_DATA_SHARE;
        let guard = script
            .find("mountpoint -q /mnt/wsl/lemma-data")
            .expect("it checks first");
        let bind = script.find("mount --bind").expect("it binds");
        assert!(
            guard < bind,
            "stacking a second bind on the same path every start: {script}"
        );
    }

    /// A macOS start that failed used to leave nothing but the boot log.
    ///
    /// `capture_diagnostics` returned success on macOS having written nothing
    /// whatsoever -- no addresses, no routes, no listening sockets, no
    /// containers, and none of the guest's own service logs -- while the
    /// Windows arm collected all of it. A guest that boots fine and then fails
    /// to start its services is the common failure, and it is precisely the
    /// one the serial console cannot explain.
    #[cfg(target_os = "macos")]
    #[test]
    fn a_failed_macos_start_writes_down_what_the_guest_saw() {
        use std::os::unix::fs::PermissionsExt;

        let root = tempdir().unwrap();
        fs::create_dir_all(root.path().join("artifacts/macos-aarch64")).unwrap();
        let bridge = root.path().join("lemma-runtime");
        fs::write(
            &bridge,
            "#!/bin/sh\ncat >/dev/null\ncat <<'RESPONSE'\n\
             {\"ok\":true,\"result\":{\"text\":\"--- addresses ---\\n\
             2: enp0s1    inet 192.168.64.2/24 scope global\\n\"}}\nRESPONSE\n",
        )
        .unwrap();
        fs::set_permissions(&bridge, std::fs::Permissions::from_mode(0o755)).unwrap();
        let config = ManagedRuntimeConfig {
            wsl_distribution: DEFAULT_WSL_DISTRIBUTION.to_string(),
            local_root: root.path().join("local"),
            artifact_root: root.path().join("artifacts"),
            bridge_executable: bridge,
            vz_executable: root.path().join("lemma-vz"),
        };
        let runtime = ManagedRuntime::new(config).unwrap();

        runtime.capture_diagnostics().unwrap();

        let log = fs::read_to_string(root.path().join("local/logs/guest.log")).unwrap();
        assert!(
            log.contains("--- addresses ---") && log.contains("192.168.64.2/24"),
            "the guest's own account of the failure has to reach the log: {log}"
        );
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn immutable_release_requires_all_boot_artifacts() {
        let root = tempdir().unwrap();
        let release = root.path().join("release");
        fs::create_dir_all(&release).unwrap();
        for name in ["vmlinuz", "initrd", "disk.raw"] {
            fs::write(release.join(name), format!("{name}-contents")).unwrap();
        }
        fs::write(
            release.join("runtime.json"),
            br#"{"service_transport_version":1}"#,
        )
        .unwrap();
        validate_macos_release(&release).unwrap();
        fs::remove_file(release.join("disk.raw")).unwrap();
        assert!(validate_macos_release(&release).is_err());
    }

    /// Re-writing the same archive must not look like a different release.
    ///
    /// A mismatch here prompts the user to reset the Windows runtime, which
    /// deletes the distribution their workspaces and databases live in. The
    /// stamp used to include modification time, so a repair, a re-download or a
    /// reinstall of the identical release all changed it — and offered to
    /// destroy the only copy of the data over a timestamp.
    #[test]
    fn a_rootfs_rewritten_in_place_is_still_the_same_guest() {
        let root = tempdir().unwrap();
        let rootfs = root.path().join("rootfs.tar");
        fs::write(&rootfs, b"a guest filesystem").unwrap();
        let before = rootfs_stamp(&rootfs).unwrap();

        // What a repair or a re-download does: identical bytes, written again.
        std::thread::sleep(std::time::Duration::from_millis(1100));
        fs::write(&rootfs, b"a guest filesystem").unwrap();

        assert_eq!(
            rootfs_stamp(&rootfs).unwrap(),
            before,
            "the same archive must not read as a different release"
        );
    }

    /// It must still notice a genuinely different one, or the check is theatre.
    #[test]
    fn a_different_rootfs_is_still_recognised_as_different() {
        let root = tempdir().unwrap();
        let rootfs = root.path().join("rootfs.tar");
        fs::write(&rootfs, b"the guest from 0.7.2").unwrap();
        let before = rootfs_stamp(&rootfs).unwrap();

        fs::write(&rootfs, b"the rather larger guest from 0.8.0").unwrap();

        assert_ne!(rootfs_stamp(&rootfs).unwrap(), before);
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn incompatible_service_transport_is_rejected_without_changing_the_release() {
        let root = tempdir().unwrap();
        for name in ["vmlinuz", "initrd", "disk.raw"] {
            fs::write(root.path().join(name), b"keep").unwrap();
        }
        for metadata in [
            r#"{}"#,
            r#"{"service_transport_version":0}"#,
            r#"{"service_transport_version":2}"#,
        ] {
            fs::write(root.path().join("runtime.json"), metadata).unwrap();
            let error = validate_macos_release(root.path()).unwrap_err();
            assert_eq!(error.kind(), io::ErrorKind::Unsupported);
            assert!(error.to_string().contains("matching runtime update"));
            assert_eq!(fs::read(root.path().join("disk.raw")).unwrap(), b"keep");
            assert_eq!(
                fs::read_to_string(root.path().join("runtime.json")).unwrap(),
                metadata
            );
        }
    }

    /// Discarding the data disk must unlink it, never shrink it.
    ///
    /// `create_private_sparse_file` refuses a file whose length is not exactly
    /// `DATA_DISK_BYTES`, so a reset that truncated instead of removing would
    /// leave the installation permanently unable to start, with "managed data
    /// disk has an unexpected size" and no way back. This asserts the property
    /// directly: after discarding, the next start can create the disk again.
    #[cfg(target_os = "macos")]
    #[test]
    fn a_discarded_data_disk_can_be_created_again() {
        let root = tempdir().unwrap();
        let disk = root.path().join("data.raw");
        create_private_sparse_file(&disk, 1024 * 1024).unwrap();
        assert!(disk.exists());

        // What `discard_data_disk` does to the file, without booting a VM.
        remove_if_present(&disk).unwrap();

        assert!(!disk.exists(), "the disk is unlinked, not truncated");
        create_private_sparse_file(&disk, 1024 * 1024)
            .expect("a fresh disk of the expected size is creatable after a reset");
        // A truncate-instead-of-remove reset would land here, and this is the
        // error the user would be stuck with forever.
        std::fs::File::options()
            .write(true)
            .open(&disk)
            .unwrap()
            .set_len(0)
            .unwrap();
        let error = create_private_sparse_file(&disk, 1024 * 1024).unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::InvalidData);
        assert!(error.to_string().contains("unexpected size"), "{error}");
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn creates_private_sparse_data_disk_once() {
        use std::io::{Read, Seek, SeekFrom};

        let root = tempdir().unwrap();
        let disk = root.path().join("data.raw");

        create_private_sparse_file(&disk, 1024 * 1024).unwrap();
        let mut file = OpenOptions::new()
            .read(true)
            .write(true)
            .open(&disk)
            .unwrap();
        file.seek(SeekFrom::End(-5)).unwrap();
        file.write_all(b"state").unwrap();
        create_private_sparse_file(&disk, 1024 * 1024).unwrap();
        file.seek(SeekFrom::End(-5)).unwrap();
        let mut state = String::new();
        file.read_to_string(&mut state).unwrap();

        assert_eq!(state, "state");
        ensure_private_file(&disk).unwrap();
    }

    #[test]
    fn cache_repair_signal_is_exact_and_does_not_match_generic_failures() {
        assert!(cache_repair_required(&io::Error::other(
            "container cache repair required"
        )));
        assert!(!cache_repair_required(&io::Error::other(
            "container engine unavailable"
        )));
    }

    /// A repair verdict from a previous boot cannot condemn this one.
    ///
    /// `guest_needs_data_repair` scans the console for
    /// `lemma-data: needs-repair:` and returns *before* health is ever polled,
    /// and the console is append-only. So one bad boot condemned every boot
    /// after it -- including the boot that follows a successful reset, which
    /// found the old line, refused to start, and offered the same reset again.
    /// The only escape was a full reinstall.
    ///
    /// The fix is in `start`, which now rotates the console unconditionally
    /// rather than at 5 MiB, so this method can only ever see the current boot.
    #[cfg(target_os = "macos")]
    #[test]
    fn a_repair_verdict_does_not_outlive_the_boot_that_produced_it() {
        let root = tempdir().unwrap();
        let runtime = ManagedRuntime::new(ManagedRuntimeConfig {
            wsl_distribution: DEFAULT_WSL_DISTRIBUTION.to_string(),
            local_root: root.path().join("local"),
            artifact_root: root.path().join("artifacts"),
            bridge_executable: root.path().join("lemma-runtime"),
            vz_executable: root.path().join("lemma-vz"),
        })
        .unwrap();
        let console = runtime.config.local_root.join("runtime/macos/console.log");
        fs::create_dir_all(console.parent().unwrap()).unwrap();
        fs::write(
            &console,
            "[    0.10] booting\nlemma-data: needs-repair: no filesystem signature on /dev/vdb\n",
        )
        .unwrap();

        assert_eq!(
            runtime.guest_needs_data_repair().as_deref(),
            Some("no filesystem signature on /dev/vdb"),
            "the verdict is read while it is this boot's",
        );

        // What `start` does on the next boot.
        rotate_log(&console, 0).unwrap();

        assert_eq!(
            runtime.guest_needs_data_repair(),
            None,
            "a verdict from a previous boot must not refuse this one",
        );
        // And it is kept, because the boot that failed is the one worth reading.
        assert!(fs::read_to_string(console.with_extension("previous.log"))
            .unwrap()
            .contains("needs-repair"),);
    }

    /// The rotation `start` relies on fires for any non-empty log.
    #[test]
    fn rotating_at_zero_moves_every_line_aside() {
        let root = tempdir().unwrap();
        let path = root.path().join("console.log");

        // A log that does not exist yet is not an error and leaves nothing.
        rotate_log(&path, 0).unwrap();
        assert!(!path.with_extension("previous.log").exists());

        fs::write(&path, "one line\n").unwrap();
        rotate_log(&path, 0).unwrap();
        assert_eq!(fs::read_to_string(&path).unwrap(), "");
        assert_eq!(
            fs::read_to_string(path.with_extension("previous.log")).unwrap(),
            "one line\n"
        );
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn refreshes_private_host_epoch_for_direct_boot_guests() {
        let root = tempdir().unwrap();
        let runtime = ManagedRuntime::new(ManagedRuntimeConfig {
            wsl_distribution: DEFAULT_WSL_DISTRIBUTION.to_string(),
            local_root: root.path().join("local"),
            artifact_root: root.path().join("artifacts"),
            bridge_executable: root.path().join("lemma-runtime"),
            vz_executable: root.path().join("lemma-vz"),
        })
        .unwrap();

        runtime.refresh_host_epoch().unwrap();

        let epoch: u64 = fs::read_to_string(&runtime.host_epoch_file)
            .unwrap()
            .trim()
            .parse()
            .unwrap();
        assert!(epoch > 1_700_000_000);
        ensure_private_file(&runtime.host_epoch_file).unwrap();
    }

    #[cfg(target_os = "macos")]
    struct RecoveryTestChild(std::process::Child);

    #[cfg(target_os = "macos")]
    impl Drop for RecoveryTestChild {
        fn drop(&mut self) {
            let _ = self.0.kill();
            let _ = self.0.wait();
        }
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn confirmed_recovery_reclaims_a_verified_helper_from_a_replaced_bundle() {
        let root = tempdir().unwrap();
        let mut runtime = ManagedRuntime::new(ManagedRuntimeConfig {
            wsl_distribution: DEFAULT_WSL_DISTRIBUTION.to_string(),
            local_root: root.path().join("local"),
            artifact_root: root.path().join("artifacts"),
            bridge_executable: root.path().join("lemma-runtime"),
            vz_executable: PathBuf::from("/bin/sleep"),
        })
        .unwrap();
        let mut child = RecoveryTestChild(Command::new("/bin/sleep").arg("10").spawn().unwrap());
        runtime.record_macos_vm(&child.0).unwrap();
        runtime.config.vz_executable = root.path().join("new-app/lemma-vz");
        assert!(runtime.reclaim_owned_macos_vm().is_err());
        assert!(runtime.vm_process_marker.exists());
        thread::scope(|scope| {
            let reclaim = scope.spawn(|| runtime.reclaim_owned_macos_vm_for_reset());
            let status = child.0.wait().unwrap();
            reclaim.join().unwrap().unwrap();
            assert!(!status.success());
        });
        assert!(!runtime.vm_process_marker.exists());
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn recovery_never_signals_a_reused_process_identity() {
        let root = tempdir().unwrap();
        let runtime = ManagedRuntime::new(ManagedRuntimeConfig {
            wsl_distribution: DEFAULT_WSL_DISTRIBUTION.to_string(),
            local_root: root.path().join("local"),
            artifact_root: root.path().join("artifacts"),
            bridge_executable: root.path().join("lemma-runtime"),
            vz_executable: PathBuf::from("/bin/sleep"),
        })
        .unwrap();
        let mut child = RecoveryTestChild(Command::new("/bin/sleep").arg("10").spawn().unwrap());
        runtime.record_macos_vm(&child.0).unwrap();
        let mut marker: serde_json::Value =
            serde_json::from_slice(&fs::read(&runtime.vm_process_marker).unwrap()).unwrap();
        marker["start_identity"] = serde_json::json!("different process start");
        fs::write(
            &runtime.vm_process_marker,
            serde_json::to_vec(&marker).unwrap(),
        )
        .unwrap();
        runtime.reclaim_owned_macos_vm_for_reset().unwrap();
        assert!(child.0.try_wait().unwrap().is_none());
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn reclaims_only_the_exact_recorded_vm_helper_across_daemon_replacement() {
        let root = tempdir().unwrap();
        let runtime = ManagedRuntime::new(ManagedRuntimeConfig {
            wsl_distribution: DEFAULT_WSL_DISTRIBUTION.to_string(),
            local_root: root.path().join("local"),
            artifact_root: root.path().join("artifacts"),
            bridge_executable: root.path().join("lemma-runtime"),
            vz_executable: PathBuf::from("/bin/sleep"),
        })
        .unwrap();
        let mut child = Command::new("/bin/sleep").arg("30").spawn().unwrap();
        runtime.record_macos_vm(&child).unwrap();
        let waiter = thread::spawn(move || child.wait().unwrap());

        runtime.reclaim_owned_macos_vm().unwrap();

        assert!(!waiter.join().unwrap().success());
        assert!(!runtime.vm_process_marker.exists());
    }

    #[test]
    fn recovery_requires_positive_evidence_of_guest_presence_or_absence() {
        assert!(registered_guest(false, b"", "LemmaRuntime").is_err());
        assert!(registered_guest(false, b"LemmaRuntime", "LemmaRuntime").is_err());
        assert!(
            !registered_guest(true, b"Ubuntu\r\nLemmaRuntime-dev\r\n", "LemmaRuntime").unwrap()
        );
        assert!(registered_guest(true, b"Ubuntu\r\nLemmaRuntime\r\n", "LemmaRuntime").unwrap());
        let utf16: Vec<u8> = "LemmaRuntime\r\n"
            .encode_utf16()
            .flat_map(u16::to_le_bytes)
            .collect();
        assert!(registered_guest(true, &utf16, "LemmaRuntime").unwrap());
    }

    #[cfg(windows)]
    #[test]
    fn decodes_legacy_utf16_wsl_distribution_output() {
        let encoded: Vec<u8> = "LemmaRuntime\r\nUbuntu\r\n"
            .encode_utf16()
            .flat_map(u16::to_le_bytes)
            .collect();
        assert!(decode_wsl_output(&encoded).contains("LemmaRuntime"));
    }
    /// A Windows failure used to record the words "-- No entries --".
    ///
    /// Diagnostics ran `journalctl`, and the Windows guest ships
    /// `systemd=false` -- on purpose, since `lemma-runtime-init` starts
    /// containerd itself -- so there was no journal and never had been. Every
    /// failure on that platform captured nothing, which is how a start that
    /// failed because the guest reported an unreachable address left no
    /// account of itself anywhere on the machine it happened on.
    ///
    /// The two state lines are not padding: the address list is what makes
    /// that exact failure obvious at a glance.
    #[test]
    fn a_windows_guest_is_asked_for_the_logs_it_actually_writes() {
        assert!(
            !GUEST_DIAGNOSTICS.contains("journalctl"),
            "there is no journal in a guest that runs without systemd"
        );
        assert!(
            GUEST_DIAGNOSTICS.contains("/var/log/lemma/"),
            "these are the logs the guest does write"
        );
        for state in ["ip -4 -o addr show", "nerdctl ps -a"] {
            assert!(
                GUEST_DIAGNOSTICS.contains(state),
                "a failed guest has to be asked `{state}`"
            );
        }
        assert!(
            GUEST_DIAGNOSTICS.contains("tail -n 200"),
            "bounded per file: this runs on a machine that is already unhappy"
        );
        assert!(
            GUEST_DIAGNOSTICS.contains("set +e"),
            "a collector that fails halfway is still worth what it printed first"
        );
    }

    /// A first install downloads about a gigabyte, and that is the user's
    /// connection's business, not the guest's.
    ///
    /// Eight minutes bounded a slow download rather than a stuck one. On a
    /// real Windows machine the image phase failed on every attempt while
    /// making progress on every attempt -- 317 MB in the content store after
    /// ten minutes, no image completed, and no way for it ever to finish.
    ///
    /// Larger than the guest's own pull timeout on purpose. If the host gave
    /// up first the guest would keep pulling into a request nobody is waiting
    /// on, and the retry would contend with it.
    #[test]
    fn pulling_images_is_given_longer_than_the_guest_spends_pulling_them() {
        // `ENGINE_PULL_TIMEOUT` in the guest daemon; a different crate, so the
        // number is named rather than imported.
        let guest_pull_timeout = Duration::from_secs(60 * 60);
        for operation in ["core.images", "core.sandbox_images"] {
            assert!(
                guest_request_budget(operation) > guest_pull_timeout,
                "{operation} must outlast the guest's own pull"
            );
        }
        // And nothing else grew: a wedged health probe still fails fast.
        assert_eq!(guest_request_budget("health"), Duration::from_secs(5));
        assert_eq!(
            guest_request_budget("core.postgres"),
            Duration::from_secs(8 * 60)
        );
    }

    #[test]
    fn budgets_leave_room_for_slow_work_without_letting_a_query_hang() {
        assert!(wsl_budget(&["--import", "LemmaRuntime"]) >= Duration::from_secs(15 * 60));
        // The queries are what a wedged WSL blocks, and what the start path
        // waits on when it does, so their budget is how long a broken install
        // stays silent.
        assert!(wsl_budget(&["--list", "--quiet"]) <= Duration::from_secs(2 * 60));
        assert!(wsl_budget(&["--status"]) <= Duration::from_secs(2 * 60));
        // Guest work is not a query: `--exec lemma-runtime-init` brings the
        // whole stack up and must not be held to the query budget.
        assert!(
            wsl_budget(&[
                "--distribution",
                "LemmaRuntime",
                "--exec",
                "/usr/local/bin/lemma-runtime-init",
            ]) >= Duration::from_secs(10 * 60)
        );
        assert!(wsl_budget(&[]) > Duration::ZERO);
    }

    /// A wedged WSL service hangs even `--status`, and every wsl.exe call used
    /// to end in an unbounded `wait_with_output()`. The start path stopped
    /// there with no error and no log line, and the daemon had no way to give
    /// up and report one.
    ///
    /// Driven against `/bin/sh` rather than wsl.exe, because the defect was
    /// never in wsl.exe -- it was in waiting for it. That also makes this the
    /// only executed coverage the Windows runner has anywhere.
    #[cfg(unix)]
    #[test]
    fn a_command_that_never_returns_is_killed_and_reported() {
        let root = tempdir().unwrap();
        let log = root.path().join("wsl.log");
        let started = Instant::now();
        let error = run_wsl_command(
            Path::new("/bin/sh"),
            &["-c", "sleep 120"],
            None,
            Duration::from_millis(400),
            &log,
        )
        .expect_err("a command past its budget must fail, not be waited on");
        assert_eq!(error.kind(), io::ErrorKind::TimedOut);
        assert!(
            started.elapsed() < Duration::from_secs(30),
            "waited {:?}, so nothing bounded it",
            started.elapsed()
        );
        let recorded = fs::read_to_string(&log).unwrap();
        assert!(
            recorded.contains("sleep 120"),
            "a hang has to leave evidence behind; the log says {recorded:?}"
        );
    }

    /// `wsl_allowing_failure` exists so a non-zero exit can be an *answer*:
    /// `--terminate` against a distribution that is not running fails, and
    /// that failure means "already stopped".
    #[cfg(unix)]
    #[test]
    fn a_non_zero_exit_is_returned_rather_than_raised() {
        let root = tempdir().unwrap();
        let output = run_wsl_command(
            Path::new("/bin/sh"),
            &["-c", "echo nope >&2; exit 3"],
            None,
            Duration::from_secs(20),
            &root.path().join("wsl.log"),
        )
        .expect("a failing command is still a completed command");
        assert_eq!(output.status.code(), Some(3));
        assert_eq!(wsl_message(&output.stderr), "nope");
    }

    /// stdin used to be written with a blocking `write_all` before anything
    /// drained stdout. A child that echoes what it reads fills its output pipe
    /// at 64 KiB and blocks; this end is still blocked filling the input pipe;
    /// neither side moves again. A megabyte through `cat` is exactly that
    /// shape, and hangs forever against the old implementation.
    #[cfg(unix)]
    #[test]
    fn input_larger_than_a_pipe_buffer_does_not_deadlock() {
        let root = tempdir().unwrap();
        let payload = vec![b'x'; 1024 * 1024];
        let output = run_wsl_command(
            Path::new("/bin/sh"),
            &["-c", "cat"],
            Some(&payload),
            Duration::from_secs(30),
            &root.path().join("wsl.log"),
        )
        .expect("a megabyte of stdin must not wedge the runner");
        assert!(output.status.success());
        assert_eq!(output.stdout.len(), payload.len());
    }

    /// The kind of a spawn failure is load-bearing: `unregister_windows_guest`
    /// reads `NotFound` as "there is no WSL on this machine to unregister
    /// from" and completes the uninstall. Wrapping it would turn a clean
    /// uninstall into a failure the user cannot clear.
    #[cfg(unix)]
    #[test]
    fn a_missing_wsl_executable_keeps_its_not_found_kind() {
        let root = tempdir().unwrap();
        let error = run_wsl_command(
            &root.path().join("no-such-wsl"),
            &["--list", "--quiet"],
            None,
            Duration::from_secs(5),
            &root.path().join("wsl.log"),
        )
        .expect_err("a missing executable cannot succeed");
        assert_eq!(error.kind(), io::ErrorKind::NotFound);
    }
}
