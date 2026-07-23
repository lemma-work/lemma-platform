use std::env;
use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::net::{IpAddr, Ipv4Addr, Shutdown, SocketAddr, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use lemma_runtime_manager::{ManagedRuntime, ManagedRuntimeConfig, ManagedRuntimeStatus};
use serde::{Deserialize, Serialize};
use serde_json::json;

use crate::host_process::ManagedRuntimeSpec;
use crate::paths::LocalPaths;

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct InfraSecrets {
    postgres_password: String,
    redis_password: String,
}

#[derive(Clone, Debug)]
pub struct ManagedRuntimeBootstrap {
    artifact_root: PathBuf,
    bridge_executable: PathBuf,
    #[cfg(target_os = "macos")]
    vz_executable: PathBuf,
    #[cfg(windows)]
    wsl_executable: PathBuf,
    secrets: InfraSecrets,
}

impl ManagedRuntimeBootstrap {
    pub fn discover(paths: &LocalPaths) -> io::Result<Option<Self>> {
        let Some(artifact_root) = env::var_os("LEMMA_LOCALD_MANAGED_RUNTIME_ARTIFACT_ROOT")
            .filter(|value| !value.is_empty())
            .map(PathBuf::from)
        else {
            return Ok(None);
        };
        if !artifact_root.is_dir() {
            return Err(io::Error::new(
                io::ErrorKind::NotFound,
                format!(
                    "managed runtime artifact root is missing: {}",
                    artifact_root.display()
                ),
            ));
        }

        #[cfg(not(any(target_os = "macos", windows)))]
        return Err(io::Error::new(
            io::ErrorKind::Unsupported,
            "managed local runtime is supported on macOS and Windows",
        ));

        #[cfg(any(target_os = "macos", windows))]
        {
            let bridge_executable = bundled_executable(
                "LEMMA_LOCALD_RUNTIME_BRIDGE_BIN",
                if cfg!(windows) {
                    "lemma-runtime.exe"
                } else {
                    "lemma-runtime"
                },
            )?;
            #[cfg(target_os = "macos")]
            let vz_executable = bundled_executable("LEMMA_LOCALD_VZ_BIN", "lemma-vz")?;
            #[cfg(windows)]
            let wsl_executable = env::var_os("LEMMA_LOCALD_WSL_BIN")
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from("wsl.exe"));
            #[cfg(windows)]
            ensure_wsl_available(&wsl_executable)?;

            Ok(Some(Self {
                artifact_root,
                bridge_executable,
                #[cfg(target_os = "macos")]
                vz_executable,
                #[cfg(windows)]
                wsl_executable,
                secrets: load_or_create_secrets(&paths.root.join("infra.secrets.json"))?,
            }))
        }
    }

    pub fn apply_manifest_environment(&self, command: &mut Command) {
        command
            .env(
                "LEMMA_MANAGED_POSTGRES_PASSWORD",
                &self.secrets.postgres_password,
            )
            .env("LEMMA_MANAGED_REDIS_PASSWORD", &self.secrets.redis_password)
            .env("LEMMA_MANAGED_RUNTIME_CLI", &self.bridge_executable);
    }

    pub fn controller(
        &self,
        paths: &LocalPaths,
        spec: ManagedRuntimeSpec,
    ) -> io::Result<Arc<ManagedRuntimeController>> {
        if spec.credentials.postgres_password != self.secrets.postgres_password
            || spec.credentials.redis_password != self.secrets.redis_password
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "host manifest credentials do not match the private local installation",
            ));
        }
        let runtime = ManagedRuntime::new(ManagedRuntimeConfig {
            local_root: paths.root.clone(),
            artifact_root: self.artifact_root.clone(),
            bridge_executable: self.bridge_executable.clone(),
            #[cfg(target_os = "macos")]
            vz_executable: self.vz_executable.clone(),
            #[cfg(windows)]
            wsl_executable: self.wsl_executable.clone(),
        })?;
        Ok(Arc::new(ManagedRuntimeController {
            runtime,
            spec,
            forwarders: Mutex::new(Vec::new()),
            status: Mutex::new(None),
        }))
    }
}

pub struct ManagedRuntimeController {
    runtime: ManagedRuntime,
    spec: ManagedRuntimeSpec,
    forwarders: Mutex<Vec<TcpForwarder>>,
    status: Mutex<Option<ManagedRuntimeStatus>>,
}

impl ManagedRuntimeController {
    pub fn start(&self) -> io::Result<()> {
        validate_spec(&self.spec)?;
        let status = self.runtime.start()?;
        let ensure_result = self.runtime.request(
            "core.ensure",
            json!({
                "images": self.spec.images,
                "credentials": self.spec.credentials,
            }),
        );
        if let Err(error) = ensure_result {
            let _ = self.runtime.stop();
            return Err(error);
        }
        if let Err(error) = self.ensure_forwarders(&status) {
            let _ = self.runtime.stop();
            return Err(error);
        }
        *self.status.lock().expect("managed runtime status poisoned") = Some(status);
        Ok(())
    }

    pub fn stop_infrastructure(&self) -> io::Result<()> {
        if self.status().is_none() {
            self.clear_forwarders();
            return self.runtime.stop();
        }
        let core_result = self.runtime.request("core.stop", json!({})).map(|_| ());
        self.clear_forwarders();
        let runtime_result = self.runtime.stop();
        core_result.and(runtime_result)
    }

    pub fn shutdown(&self) -> io::Result<()> {
        self.clear_forwarders();
        self.runtime.stop()
    }

    pub fn status(&self) -> Option<ManagedRuntimeStatus> {
        self.status
            .lock()
            .expect("managed runtime status poisoned")
            .clone()
    }

    fn ensure_forwarders(&self, status: &ManagedRuntimeStatus) -> io::Result<()> {
        let endpoint_host = private_ipv4(&status.endpoint_host, "guest endpoint")?;
        let host_gateway = private_ipv4(&status.host_gateway, "guest host gateway")?;
        let mut current = self.forwarders.lock().expect("forwarder lock poisoned");
        if !current.is_empty() {
            return Ok(());
        }

        let bindings = [
            (
                "postgres",
                SocketAddr::from((Ipv4Addr::LOCALHOST, self.spec.ports.postgres)),
                SocketAddr::from((endpoint_host, 5432)),
            ),
            (
                "redis",
                SocketAddr::from((Ipv4Addr::LOCALHOST, self.spec.ports.redis)),
                SocketAddr::from((endpoint_host, 6379)),
            ),
            (
                "supertokens",
                SocketAddr::from((Ipv4Addr::LOCALHOST, self.spec.ports.supertokens)),
                SocketAddr::from((endpoint_host, 3567)),
            ),
            (
                "sandbox-api-callback",
                SocketAddr::from((host_gateway, self.spec.ports.backend)),
                SocketAddr::from((Ipv4Addr::LOCALHOST, self.spec.ports.backend)),
            ),
            (
                "sandbox-frontend-callback",
                SocketAddr::from((host_gateway, self.spec.ports.frontend)),
                SocketAddr::from((Ipv4Addr::LOCALHOST, self.spec.ports.frontend)),
            ),
        ];
        for (label, bind, target) in bindings {
            match TcpForwarder::start(label, bind, target) {
                Ok(forwarder) => current.push(forwarder),
                Err(error) => {
                    current.clear();
                    return Err(error);
                }
            }
        }
        Ok(())
    }

    fn clear_forwarders(&self) {
        self.forwarders
            .lock()
            .expect("forwarder lock poisoned")
            .clear();
        *self.status.lock().expect("managed runtime status poisoned") = None;
    }
}

struct TcpForwarder {
    stop: Arc<AtomicBool>,
    local_address: SocketAddr,
    thread: Option<JoinHandle<()>>,
}

impl TcpForwarder {
    fn start(label: &'static str, bind: SocketAddr, target: SocketAddr) -> io::Result<Self> {
        let listener = TcpListener::bind(bind).map_err(|error| {
            io::Error::new(
                error.kind(),
                format!("could not bind managed {label} route at {bind}: {error}"),
            )
        })?;
        let local_address = listener.local_addr()?;
        listener.set_nonblocking(true)?;
        let stop = Arc::new(AtomicBool::new(false));
        let thread_stop = Arc::clone(&stop);
        let worker = thread::spawn(move || {
            while !thread_stop.load(Ordering::Acquire) {
                match listener.accept() {
                    Ok((stream, _)) => {
                        thread::spawn(move || {
                            let _ = proxy_connection(stream, target);
                        });
                    }
                    Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(25));
                    }
                    Err(_) => break,
                }
            }
        });
        Ok(Self {
            stop,
            local_address,
            thread: Some(worker),
        })
    }
}

impl Drop for TcpForwarder {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
        let _ = TcpStream::connect_timeout(&self.local_address, Duration::from_millis(100));
        if let Some(worker) = self.thread.take() {
            let _ = worker.join();
        }
    }
}

fn proxy_connection(mut inbound: TcpStream, target: SocketAddr) -> io::Result<()> {
    let mut outbound = TcpStream::connect_timeout(&target, Duration::from_secs(5))?;
    let mut inbound_writer = inbound.try_clone()?;
    let mut outbound_reader = outbound.try_clone()?;
    let upload = thread::spawn(move || {
        let result = io::copy(&mut inbound, &mut outbound);
        let _ = outbound.shutdown(Shutdown::Write);
        result
    });
    let download = io::copy(&mut outbound_reader, &mut inbound_writer);
    let _ = inbound_writer.shutdown(Shutdown::Write);
    let upload = upload
        .join()
        .map_err(|_| io::Error::other("managed TCP route worker panicked"))?;
    upload.and(download).map(|_| ())
}

fn validate_spec(spec: &ManagedRuntimeSpec) -> io::Result<()> {
    for (name, image) in [
        ("postgres", &spec.images.postgres),
        ("redis", &spec.images.redis),
        ("supertokens", &spec.images.supertokens),
    ] {
        if !image.contains("@sha256:") || image.bytes().any(|byte| byte.is_ascii_whitespace()) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                format!("managed {name} image must be digest-pinned"),
            ));
        }
    }
    validate_secret("postgres_password", &spec.credentials.postgres_password)?;
    validate_secret("redis_password", &spec.credentials.redis_password)?;
    let ports = [
        spec.ports.postgres,
        spec.ports.redis,
        spec.ports.supertokens,
        spec.ports.backend,
        spec.ports.frontend,
    ];
    if ports.contains(&0) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "managed runtime ports must be non-zero",
        ));
    }
    Ok(())
}

fn private_ipv4(value: &str, label: &str) -> io::Result<Ipv4Addr> {
    let address = value.parse::<IpAddr>().map_err(|_| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            format!("{label} must be a literal IPv4 address"),
        )
    })?;
    match address {
        IpAddr::V4(address)
            if !address.is_unspecified()
                && !address.is_loopback()
                && !address.is_multicast()
                && (address.is_private() || address.is_link_local()) =>
        {
            Ok(address)
        }
        _ => Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            format!("{label} must be a private non-loopback IPv4 address"),
        )),
    }
}

fn load_or_create_secrets(path: &Path) -> io::Result<InfraSecrets> {
    if path.is_file() {
        ensure_private_file(path)?;
        let secrets: InfraSecrets = serde_json::from_slice(&fs::read(path)?)?;
        validate_secret("postgres_password", &secrets.postgres_password)?;
        validate_secret("redis_password", &secrets.redis_password)?;
        return Ok(secrets);
    }
    let secrets = InfraSecrets {
        postgres_password: random_hex()?,
        redis_password: random_hex()?,
    };
    write_private_atomic(path, &serde_json::to_vec(&secrets)?)?;
    Ok(secrets)
}

fn random_hex() -> io::Result<String> {
    let mut bytes = [0_u8; 32];
    getrandom::fill(&mut bytes)
        .map_err(|error| io::Error::other(format!("secure randomness failed: {error}")))?;
    Ok(bytes.iter().map(|byte| format!("{byte:02x}")).collect())
}

fn validate_secret(name: &str, value: &str) -> io::Result<()> {
    if value.len() != 64
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            format!("managed {name} must be a 64-character lowercase hex secret"),
        ));
    }
    Ok(())
}

fn write_private_atomic(path: &Path, contents: &[u8]) -> io::Result<()> {
    let parent = path
        .parent()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "secret path has no parent"))?;
    fs::create_dir_all(parent)?;
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    let temporary = parent.join(format!(".infra-secrets-{}-{nonce}.tmp", std::process::id()));
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
    fs::rename(&temporary, path)?;
    ensure_private_file(path)
}

fn ensure_private_file(path: &Path) -> io::Result<()> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        let metadata = fs::symlink_metadata(path)?;
        if !metadata.file_type().is_file() || metadata.mode() & 0o077 != 0 {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                format!("managed secret file is not private: {}", path.display()),
            ));
        }
    }
    Ok(())
}

#[cfg(any(target_os = "macos", windows))]
fn bundled_executable(variable: &str, sibling_name: &str) -> io::Result<PathBuf> {
    let path = match env::var_os(variable).filter(|value| !value.is_empty()) {
        Some(path) => PathBuf::from(path),
        None => env::current_exe()?
            .parent()
            .ok_or_else(|| io::Error::other("locald executable has no parent"))?
            .join(sibling_name),
    };
    if !path.is_file() {
        return Err(io::Error::new(
            io::ErrorKind::NotFound,
            format!(
                "bundled managed runtime executable is missing: {}",
                path.display()
            ),
        ));
    }
    Ok(path)
}

#[cfg(windows)]
fn ensure_wsl_available(executable: &Path) -> io::Result<()> {
    let output = Command::new(executable).arg("--status").output()?;
    if output.status.success() {
        Ok(())
    } else {
        Err(io::Error::new(
            io::ErrorKind::Unsupported,
            "Windows Subsystem for Linux 2 is required for local sandboxes",
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Read;
    use tempfile::tempdir;

    #[test]
    fn secrets_are_stable_private_and_not_accepted_when_tampered() {
        let root = tempdir().unwrap();
        let path = root.path().join("infra.secrets.json");
        let first = load_or_create_secrets(&path).unwrap();
        let second = load_or_create_secrets(&path).unwrap();

        assert_eq!(first.postgres_password, second.postgres_password);
        assert_eq!(first.redis_password, second.redis_password);
        assert_ne!(first.postgres_password, first.redis_password);
        #[cfg(unix)]
        {
            use std::os::unix::fs::MetadataExt;
            assert_eq!(fs::metadata(&path).unwrap().mode() & 0o777, 0o600);
        }
    }

    #[test]
    fn tcp_forwarder_relays_bytes_and_releases_its_port() {
        let target = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        let target_address = target.local_addr().unwrap();
        let server = thread::spawn(move || {
            let (mut stream, _) = target.accept().unwrap();
            let mut input = [0_u8; 4];
            stream.read_exact(&mut input).unwrap();
            assert_eq!(&input, b"ping");
            stream.write_all(b"pong").unwrap();
        });
        let forwarder = TcpForwarder::start(
            "test",
            SocketAddr::from((Ipv4Addr::LOCALHOST, 0)),
            target_address,
        )
        .unwrap();
        let local = forwarder.local_address;
        let mut client = TcpStream::connect(local).unwrap();
        client.write_all(b"ping").unwrap();
        let mut output = [0_u8; 4];
        client.read_exact(&mut output).unwrap();

        assert_eq!(&output, b"pong");
        server.join().unwrap();
        drop(client);
        drop(forwarder);
        assert!(TcpListener::bind(local).is_ok());
    }

    #[test]
    fn managed_endpoints_must_stay_on_private_ipv4() {
        assert_eq!(
            private_ipv4("192.168.64.2", "guest").unwrap(),
            Ipv4Addr::new(192, 168, 64, 2)
        );
        assert!(private_ipv4("127.0.0.1", "guest").is_err());
        assert!(private_ipv4("8.8.8.8", "guest").is_err());
        assert!(private_ipv4("::1", "guest").is_err());
    }
}
