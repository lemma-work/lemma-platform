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
use std::time::{Duration, Instant};
#[cfg(target_os = "macos")]
use std::time::{SystemTime, UNIX_EPOCH};

const CAPABILITY_BYTES: usize = 32;
const MAX_RESPONSE_BYTES: usize = 4 * 1024 * 1024;
const WSL_DISTRIBUTION: &str = "LemmaRuntime";
#[cfg(target_os = "macos")]
const DATA_DISK_BYTES: u64 = 24 * 1024 * 1024 * 1024;

#[derive(Clone, Debug)]
pub struct ManagedRuntimeConfig {
    pub local_root: PathBuf,
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
            .arg("request")
            .env("LEMMA_GUEST_CAPABILITY_FILE", &self.capability_file)
            .env("LEMMA_GUEST_CONTROL_SOCKET", &self.control_socket)
            .env("LEMMA_WSL_DISTRIBUTION", WSL_DISTRIBUTION)
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
            let output = self.wsl(
                &[
                    "--distribution",
                    WSL_DISTRIBUTION,
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

    pub fn stop(&self) -> io::Result<()> {
        #[cfg(target_os = "macos")]
        {
            if let Some(mut child) = self.vm.lock().expect("VM lock poisoned").take() {
                let _ = self.request("system.shutdown", json!({}));
                let deadline = Instant::now() + Duration::from_secs(20);
                while Instant::now() < deadline {
                    if child.try_wait()?.is_some() {
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
                        return Ok(());
                    }
                    thread::sleep(Duration::from_millis(100));
                }
                child.kill()?;
                child.wait()?;
            }
        }
        #[cfg(windows)]
        {
            // WSL's private distribution is terminated by the host rather
            // than systemd poweroff. Ask the guest to stop every managed
            // container first so databases and sandboxes flush cleanly.
            let _ = self.request("system.shutdown", json!({}));
            let output = Command::new(&self.config.wsl_executable)
                .args(["--terminate", WSL_DISTRIBUTION])
                .output()?;
            if !output.status.success() {
                return Err(io::Error::other(first_line(&output.stderr)));
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

    fn wait_ready(&self) -> io::Result<ManagedRuntimeStatus> {
        let deadline = Instant::now() + Duration::from_secs(120);
        let mut last_error = None;
        while Instant::now() < deadline {
            #[cfg(target_os = "macos")]
            if let Some(error) = self.macos_exit_error()? {
                return Err(error);
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
        }
        create_private_sparse_file(&state.join("data.raw"), DATA_DISK_BYTES)?;
        let _ = fs::remove_file(&self.control_socket);
        let log_path = self.config.local_root.join("logs/vz.log");
        rotate_log(&log_path, 5 * 1024 * 1024)?;
        rotate_log(&state.join("console.log"), 5 * 1024 * 1024)?;
        let child = Command::new(&self.config.vz_executable)
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
        *guard = Some(child);
        Ok(())
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
        let log = fs::read(self.config.local_root.join("logs/vz.log")).unwrap_or_default();
        let detail = first_diagnostic(&log, "VM helper exited without a diagnostic");
        Ok(Some(io::Error::other(format!(
            "Lemma's private runtime exited ({status}): {detail}"
        ))))
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
        let distributions = self.wsl(&["--list", "--quiet"], None)?;
        let installed = decode_wsl_output(&distributions.stdout)
            .lines()
            .any(|line| line.trim() == WSL_DISTRIBUTION);
        if !installed {
            let rootfs = self.config.artifact_root.join("windows-x86_64/rootfs.tar");
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
                    WSL_DISTRIBUTION,
                    &install_path,
                    &rootfs_path,
                    "--version",
                    "2",
                ],
                None,
            )?;
        }
        let capability = fs::read(&self.capability_file)?;
        self.wsl(
            &[
                "--distribution",
                WSL_DISTRIBUTION,
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
                WSL_DISTRIBUTION,
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

    #[cfg(windows)]
    fn wsl(&self, arguments: &[&str], input: Option<&[u8]>) -> io::Result<std::process::Output> {
        let log_path = self.config.local_root.join("logs/wsl.log");
        rotate_log(&log_path, 5 * 1024 * 1024)?;
        let mut command = Command::new(&self.config.wsl_executable);
        command
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
                writeln!(log, "{}", first_line(&output.stderr))?;
            }
        }
        if !output.status.success() {
            return Err(io::Error::other(first_line(&output.stderr)));
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
fn create_private_sparse_file(path: &Path, size: u64) -> io::Result<()> {
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
        return ensure_private_file(path);
    }
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    use std::os::unix::fs::OpenOptionsExt;
    options.mode(0o600);
    let file = options.open(path)?;
    file.set_len(size)?;
    file.sync_all()?;
    ensure_private_file(path)
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
        let previous = path.with_extension("previous.log");
        let _ = fs::remove_file(&previous);
        fs::rename(path, previous)?;
    }
    Ok(())
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

#[cfg(windows)]
fn first_line(value: &[u8]) -> String {
    String::from_utf8_lossy(value)
        .lines()
        .next()
        .unwrap_or("managed runtime command failed")
        .trim()
        .to_owned()
}

#[cfg(windows)]
fn decode_wsl_output(value: &[u8]) -> String {
    if value.len() >= 2 && value.iter().skip(1).step_by(2).any(|byte| *byte == 0) {
        let words: Vec<u16> = value
            .chunks_exact(2)
            .map(|pair| u16::from_le_bytes([pair[0], pair[1]]))
            .collect();
        String::from_utf16_lossy(&words).replace('\0', "")
    } else {
        String::from_utf8_lossy(value).into_owned()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn capability_is_stable_private_and_not_in_command_arguments() {
        let root = tempdir().unwrap();
        let artifacts = root.path().join("artifacts/macos-aarch64");
        fs::create_dir_all(&artifacts).unwrap();
        let config = ManagedRuntimeConfig {
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

    #[cfg(target_os = "macos")]
    #[test]
    fn refreshes_private_host_epoch_for_direct_boot_guests() {
        let root = tempdir().unwrap();
        let runtime = ManagedRuntime::new(ManagedRuntimeConfig {
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
