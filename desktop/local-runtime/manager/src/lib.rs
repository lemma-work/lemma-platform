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
    pub endpoint_host: String,
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
        let request = json!({
            "version": 1,
            "operation": operation,
            "parameters": parameters,
        });
        let encoded = serde_json::to_vec(&request)?;
        let mut child = Command::new(&self.config.bridge_executable)
            .no_console_window()
            .arg("request")
            .env("LEMMA_GUEST_CAPABILITY_FILE", &self.capability_file)
            .env("LEMMA_GUEST_CONTROL_SOCKET", &self.control_socket)
            .env("LEMMA_WSL_DISTRIBUTION", &self.config.wsl_distribution)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()?;
        let mut stdin = child
            .stdin
            .take()
            .ok_or_else(|| io::Error::other("runtime bridge stdin unavailable"))?;
        stdin.write_all(&encoded)?;
        stdin.write_all(b"\n")?;
        drop(stdin);
        let output = child.wait_with_output()?;
        if output.stdout.len() > MAX_RESPONSE_BYTES {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "guest response exceeded 4 MiB",
            ));
        }
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
            // The VZ serial console is continuously appended by the helper.
            Ok(())
        }
        #[cfg(windows)]
        {
            // Allowing failure on purpose. This runs *because* something has
            // already gone wrong, so the guest is often exactly the kind of
            // half-up that makes journalctl exit non-zero -- and whatever it
            // managed to print before giving up is the reason anyone asked for
            // diagnostics. Failing here would throw away the evidence.
            let output = self.wsl_allowing_failure(
                &[
                    "--distribution",
                    self.distribution(),
                    "--user",
                    "root",
                    "--exec",
                    "/usr/bin/journalctl",
                    "--no-pager",
                    "--lines",
                    "300",
                ],
                None,
            )?;
            let log_path = self.config.local_root.join("logs/guest.log");
            rotate_log(&log_path, 5 * 1024 * 1024)?;
            let mut log = private_appending_log(&log_path)?;
            let start = output.stdout.len().saturating_sub(128 * 1024);
            log.write_all(&output.stdout[start..])?;
            log.write_all(b"\n")?;
            Ok(())
        }
        #[cfg(not(any(target_os = "macos", windows)))]
        {
            Ok(())
        }
    }

    /// Verify both the platform runtime process and the guest control plane.
    ///
    /// This is intentionally stronger than checking whether the last start
    /// succeeded: the VM or WSL distribution may disappear while the native
    /// backend and frontend processes remain alive.
    pub fn health(&self) -> io::Result<ManagedRuntimeStatus> {
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
            let output = Command::new(&self.config.wsl_executable)
                .no_console_window()
                .args(["--terminate", self.distribution()])
                .output()?;
            if !output.status.success() {
                return Err(io::Error::other(wsl_message(&output.stderr)));
            }
        }
        Ok(())
    }

    pub fn capability_file(&self) -> &Path {
        &self.capability_file
    }

    pub fn control_socket(&self) -> &Path {
        &self.control_socket
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

    fn wait_ready(&self) -> io::Result<ManagedRuntimeStatus> {
        let deadline = Instant::now() + Duration::from_secs(120);
        let mut last_error = None;
        while Instant::now() < deadline {
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
        let _ = fs::remove_file(&self.control_socket);
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

    #[cfg(windows)]
    fn distribution(&self) -> &str {
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
    /// Length and modification time answer "is this the same file" without
    /// reading a gigabyte on every launch. They can differ for a file whose
    /// contents did not change -- a reinstall of the same release rewrites the
    /// archive -- so a mismatch is a reason to look closer, not a conclusion.
    #[cfg(windows)]
    fn rootfs_stamp(rootfs: &Path) -> io::Result<String> {
        let metadata = fs::metadata(rootfs)?;
        let modified = metadata
            .modified()?
            .duration_since(std::time::UNIX_EPOCH)
            .map_err(io::Error::other)?
            .as_secs();
        Ok(format!("{}:{}", metadata.len(), modified))
    }

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
        let log_path = self.config.local_root.join("logs/wsl.log");
        rotate_log(&log_path, 5 * 1024 * 1024)?;
        let mut command = Command::new(&self.config.wsl_executable);
        command
            .no_console_window()
            .args(arguments)
            .stdin(if input.is_some() {
                Stdio::piped()
            } else {
                Stdio::null()
            })
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        let mut child = command.spawn()?;
        if let Some(input) = input {
            child
                .stdin
                .take()
                .ok_or_else(|| io::Error::other("WSL stdin unavailable"))?
                .write_all(input)?;
        }
        let output = child.wait_with_output()?;
        {
            let mut log = private_appending_log(&log_path)?;
            writeln!(
                log,
                "lemma-runtime: wsl.exe {} -> {}",
                arguments.join(" "),
                output.status
            )?;
            if !output.stderr.is_empty() {
                writeln!(log, "{}", wsl_message(&output.stderr))?;
            }
        }
        Ok(output)
    }

    /// Refuse to run this release's host against a guest from another one.
    ///
    /// The distribution is only ever created once, and its name says nothing
    /// about which release built it, so an upgrade used to skip the import and
    /// leave the new host talking to the old guestd. Every guest operation then
    /// failed as "could not reach Lemma's private runtime", with nothing
    /// pointing at the cause.
    ///
    /// This does not re-import. Under WSL there is no second data disk: unlike
    /// the VZ guest, /var/lib/lemma and the container store live inside the
    /// distribution's own ext4.vhdx, so `--import` over it would take every
    /// workspace and database with it. Replacing the guest is therefore a
    /// decision for the user, not something an upgrade does on its own.
    #[cfg(windows)]
    fn check_installed_guest_is_current(&self, rootfs: &Path) -> io::Result<()> {
        if !rootfs.is_file() {
            // Nothing to compare against. An installed guest with no artifact
            // is the repair path's problem, not this one's.
            return Ok(());
        }
        let marker = self.guest_release_marker();
        let recorded = fs::read_to_string(&marker).unwrap_or_default();
        let current = Self::rootfs_stamp(rootfs)?;
        if recorded == current {
            return Ok(());
        }
        if recorded.is_empty() {
            // A distribution imported before this marker existed. Adopt it
            // rather than declaring every existing installation broken.
            fs::write(&marker, &current)?;
            return Ok(());
        }
        Err(io::Error::other(format!(
            "Lemma's private runtime was installed by a different release and \
             cannot be upgraded in place, because your workspaces and databases \
             live inside it. Open Local settings and reset the Windows runtime \
             to rebuild it from this release ({}).",
            self.distribution()
        )))
    }

    /// Remove the private distribution, and everything inside it.
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
        if registered_guest(output.status.success(), &output.stdout, self.distribution())? {
            let _ = self.wsl_allowing_failure(&["--terminate", self.distribution()], None);
            self.wsl(&["--unregister", self.distribution()], None)?;
        }
        let _ = fs::remove_file(self.guest_release_marker());
        Ok(())
    }

    /// Whether Lemma's private distribution exists right now.
    ///
    /// Deliberately not `?`. `wsl --list --quiet` exits non-zero when there are
    /// no distributions at all -- which is exactly the state
    /// prepare_windows_host engineers with `--install --no-distribution` -- so
    /// treating that as fatal aborted the very first start before the import
    /// could ever run, permanently.
    ///
    /// Listing is only ever asked whether *our* distribution is there. If the
    /// question cannot be answered, assume it is not and let the caller's next
    /// command report the real problem.
    #[cfg(windows)]
    fn distribution_is_registered(&self) -> bool {
        self.wsl_allowing_failure(&["--list", "--quiet"], None)
            .map(|output| {
                decode_wsl_output(&output.stdout)
                    .lines()
                    .any(|line| line.trim() == self.distribution())
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
        let installed = self.distribution_is_registered();
        let rootfs = self.config.artifact_root.join("windows-x86_64/rootfs.tar");
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
                    self.distribution(),
                    &install_path,
                    &rootfs_path,
                    "--version",
                    "2",
                ],
                None,
            )?;
            let stamp = Self::rootfs_stamp(&rootfs)?;
            fs::write(self.guest_release_marker(), stamp)?;
        } else {
            self.check_installed_guest_is_current(&rootfs)?;
        }
        let capability = fs::read(&self.capability_file)?;
        self.wsl(
            &[
                "--distribution",
                self.distribution(),
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
                self.distribution(),
                "--user",
                "root",
                "--exec",
                "/usr/local/bin/lemma-runtime-init",
            ],
            None,
        )?;
        Ok(())
    }

    #[cfg(windows)]
    fn windows_wsl_ready(&self) -> bool {
        Command::new(&self.config.wsl_executable)
            .no_console_window()
            .arg("--status")
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .is_ok_and(|status| status.success())
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

    #[cfg(target_os = "macos")]
    #[test]
    fn immutable_release_requires_all_boot_artifacts() {
        let root = tempdir().unwrap();
        let release = root.path().join("release");
        fs::create_dir_all(&release).unwrap();
        for name in ["vmlinuz", "initrd", "disk.raw"] {
            fs::write(release.join(name), format!("{name}-contents")).unwrap();
        }
        fs::write(release.join("runtime.json"), b"release-two").unwrap();
        validate_macos_release(&release).unwrap();
        fs::remove_file(release.join("disk.raw")).unwrap();
        assert!(validate_macos_release(&release).is_err());
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
}
