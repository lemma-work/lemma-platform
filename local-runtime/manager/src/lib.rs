use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

const CAPABILITY_BYTES: usize = 32;
const MAX_RESPONSE_BYTES: usize = 4 * 1024 * 1024;
const WSL_DISTRIBUTION: &str = "LemmaRuntime";

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
}

pub struct ManagedRuntime {
    config: ManagedRuntimeConfig,
    capability_file: PathBuf,
    control_socket: PathBuf,
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
            control_socket: config.local_root.join("run/guest.sock"),
            config,
            #[cfg(target_os = "macos")]
            vm: Mutex::new(None),
        })
    }

    pub fn start(&self) -> io::Result<ManagedRuntimeStatus> {
        self.ensure_capability()?;
        #[cfg(target_os = "macos")]
        self.start_macos()?;
        #[cfg(windows)]
        self.start_windows()?;
        self.wait_ready()
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

    pub fn stop(&self) -> io::Result<()> {
        #[cfg(target_os = "macos")]
        {
            if let Some(mut child) = self.vm.lock().expect("VM lock poisoned").take() {
                child.kill()?;
                child.wait()?;
            }
        }
        #[cfg(windows)]
        {
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
            match self.request("health", json!({})) {
                Ok(result) => {
                    return serde_json::from_value(result).map_err(|error| {
                        io::Error::new(
                            io::ErrorKind::InvalidData,
                            format!("invalid guest health response: {error}"),
                        )
                    })
                }
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
    fn start_macos(&self) -> io::Result<()> {
        let source = self.config.artifact_root.join("macos-aarch64");
        let runtime = self.config.local_root.join("runtime/macos");
        fs::create_dir_all(&runtime)?;
        for name in ["vmlinuz", "initrd"] {
            copy_immutable(&source.join(name), &runtime.join(name))?;
        }
        let disk = runtime.join("disk.raw");
        if !disk.exists() {
            copy_immutable(&source.join("disk.raw"), &disk)?;
        }
        let mut guard = self.vm.lock().expect("VM lock poisoned");
        if let Some(child) = guard.as_mut() {
            if child.try_wait()?.is_none() {
                return Ok(());
            }
        }
        let _ = fs::remove_file(&self.control_socket);
        let child = Command::new(&self.config.vz_executable)
            .arg("serve")
            .arg("--runtime")
            .arg(&runtime)
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
            .stderr(Stdio::from(private_log(
                &self.config.local_root.join("logs/vz.log"),
            )?))
            .spawn()?;
        *guard = Some(child);
        Ok(())
    }

    #[cfg(windows)]
    fn start_windows(&self) -> io::Result<()> {
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
    fn wsl(&self, arguments: &[&str], input: Option<&[u8]>) -> io::Result<std::process::Output> {
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
        if !output.status.success() {
            return Err(io::Error::other(first_line(&output.stderr)));
        }
        Ok(output)
    }
}

fn copy_immutable(source: &Path, destination: &Path) -> io::Result<()> {
    if !source.is_file() {
        return Err(io::Error::new(
            io::ErrorKind::NotFound,
            format!("managed runtime artifact is missing: {}", source.display()),
        ));
    }
    if destination.exists() {
        return Ok(());
    }
    let temporary = destination.with_extension(format!("staging-{}", std::process::id()));
    fs::copy(source, &temporary)?;
    fs::rename(temporary, destination)
}

fn write_private_atomic(path: &Path, contents: &[u8]) -> io::Result<()> {
    let parent = path
        .parent()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "path has no parent"))?;
    fs::create_dir_all(parent)?;
    let temporary = path.with_extension(format!("tmp-{}", std::process::id()));
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
    Ok(())
}

#[cfg(target_os = "macos")]
fn private_log(path: &Path) -> io::Result<std::fs::File> {
    let parent = path
        .parent()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "log has no parent"))?;
    fs::create_dir_all(parent)?;
    let mut options = OpenOptions::new();
    options.create(true).append(true);
    use std::os::unix::fs::OpenOptionsExt;
    options.mode(0o600).open(path)
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

    #[test]
    fn atomic_copy_never_overwrites_mutable_guest_disk() {
        let root = tempdir().unwrap();
        let source = root.path().join("base.raw");
        let destination = root.path().join("disk.raw");
        fs::write(&source, "base").unwrap();
        fs::write(&destination, "user-data").unwrap();

        copy_immutable(&source, &destination).unwrap();

        assert_eq!(fs::read_to_string(destination).unwrap(), "user-data");
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
