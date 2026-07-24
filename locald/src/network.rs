use std::env;
use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::net::{Ipv4Addr, TcpListener};
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};

use crate::paths::LocalPaths;

const NETWORK_SCHEMA_VERSION: u64 = 1;
const MIN_DYNAMIC_PORT: u16 = 49_152;
const FRONTEND_OVERRIDE: &str = "LEMMA_LOCALD_FRONTEND_PORT";
const BACKEND_OVERRIDE: &str = "LEMMA_LOCALD_BACKEND_PORT";

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct NetworkPorts {
    pub schema_version: u64,
    pub frontend_port: u16,
    pub backend_port: u16,
    pub allocated_at_ms: u128,
}

impl NetworkPorts {
    pub fn frontend_url(self) -> String {
        format!("http://app.lemma.localhost:{}", self.frontend_port)
    }

    pub fn backend_url(self) -> String {
        format!("http://app.lemma.localhost:{}", self.backend_port)
    }

    fn valid(self) -> bool {
        self.schema_version == NETWORK_SCHEMA_VERSION
            && self.frontend_port >= MIN_DYNAMIC_PORT
            && self.backend_port >= MIN_DYNAMIC_PORT
            && self.frontend_port != self.backend_port
    }
}

pub fn load_or_allocate(paths: &LocalPaths) -> io::Result<NetworkPorts> {
    if let Some(overrides) = override_ports()? {
        return Ok(overrides);
    }
    let path = paths.root.join("network.json");
    if let Ok(raw) = fs::read(&path) {
        if let Ok(existing) = serde_json::from_slice::<NetworkPorts>(&raw) {
            if existing.valid() && ports_available(existing) {
                return Ok(existing);
            }
        }
    }

    let ports = allocate_ports()?;
    write_private_atomic(&path, &serde_json::to_vec_pretty(&ports)?)?;
    Ok(ports)
}

fn override_ports() -> io::Result<Option<NetworkPorts>> {
    let frontend = env::var(FRONTEND_OVERRIDE).ok();
    let backend = env::var(BACKEND_OVERRIDE).ok();
    if !cfg!(debug_assertions) {
        // Packaged Desktop releases always use app-owned dynamic allocation.
        // Overrides exist only to make source-development and test runs
        // deterministic.
        return Ok(None);
    }
    match (frontend, backend) {
        (None, None) => Ok(None),
        (Some(frontend), Some(backend)) => {
            let ports = NetworkPorts {
                schema_version: NETWORK_SCHEMA_VERSION,
                frontend_port: parse_override(FRONTEND_OVERRIDE, &frontend)?,
                backend_port: parse_override(BACKEND_OVERRIDE, &backend)?,
                allocated_at_ms: now_ms(),
            };
            if ports.frontend_port == ports.backend_port {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidInput,
                    "local frontend and backend port overrides must differ",
                ));
            }
            Ok(Some(ports))
        }
        _ => Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("{FRONTEND_OVERRIDE} and {BACKEND_OVERRIDE} must be set together"),
        )),
    }
}

fn parse_override(name: &str, value: &str) -> io::Result<u16> {
    value
        .parse::<u16>()
        .ok()
        .filter(|port| *port > 1024)
        .ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::InvalidInput,
                format!("{name} must be an unprivileged TCP port"),
            )
        })
}

fn allocate_ports() -> io::Result<NetworkPorts> {
    for _ in 0..64 {
        // Keep both reservations alive until the pair is complete so the OS
        // cannot return the same ephemeral port twice.
        let frontend = TcpListener::bind((Ipv4Addr::LOCALHOST, 0))?;
        let backend = TcpListener::bind((Ipv4Addr::LOCALHOST, 0))?;
        let frontend_port = frontend.local_addr()?.port();
        let backend_port = backend.local_addr()?.port();
        if frontend_port >= MIN_DYNAMIC_PORT
            && backend_port >= MIN_DYNAMIC_PORT
            && frontend_port != backend_port
        {
            return Ok(NetworkPorts {
                schema_version: NETWORK_SCHEMA_VERSION,
                frontend_port,
                backend_port,
                allocated_at_ms: now_ms(),
            });
        }
    }
    Err(io::Error::new(
        io::ErrorKind::AddrNotAvailable,
        "could not allocate high local ports for Lemma",
    ))
}

fn ports_available(ports: NetworkPorts) -> bool {
    let frontend = TcpListener::bind((Ipv4Addr::LOCALHOST, ports.frontend_port));
    let backend = TcpListener::bind((Ipv4Addr::LOCALHOST, ports.backend_port));
    frontend.is_ok() && backend.is_ok()
}

fn write_private_atomic(path: &Path, contents: &[u8]) -> io::Result<()> {
    let parent = path
        .parent()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "network path has no parent"))?;
    fs::create_dir_all(parent)?;
    let temporary = parent.join(format!(".network-{}.tmp", std::process::id()));
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
    replace_file(&temporary, path)?;
    #[cfg(unix)]
    {
        if let Ok(directory) = fs::File::open(parent) {
            let _ = directory.sync_all();
        }
    }
    Ok(())
}

#[cfg(not(windows))]
fn replace_file(source: &Path, destination: &Path) -> io::Result<()> {
    fs::rename(source, destination)
}

#[cfg(windows)]
fn replace_file(source: &Path, destination: &Path) -> io::Result<()> {
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
        Err(io::Error::last_os_error())
    } else {
        Ok(())
    }
}

fn now_ms() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn allocated_ports_are_high_distinct_and_persistent() {
        let root = tempdir().unwrap();
        let paths = LocalPaths::new(root.path().join("locald"));
        paths.ensure().unwrap();

        let first = load_or_allocate(&paths).unwrap();
        let second = load_or_allocate(&paths).unwrap();

        assert!(first.frontend_port >= MIN_DYNAMIC_PORT);
        assert!(first.backend_port >= MIN_DYNAMIC_PORT);
        assert_ne!(first.frontend_port, first.backend_port);
        assert_eq!(first, second);
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(
                fs::metadata(paths.root.join("network.json"))
                    .unwrap()
                    .permissions()
                    .mode()
                    & 0o777,
                0o600
            );
        }
    }

    #[test]
    fn occupied_persisted_port_rotates_without_touching_owner() {
        let root = tempdir().unwrap();
        let paths = LocalPaths::new(root.path().join("locald"));
        paths.ensure().unwrap();
        let first = load_or_allocate(&paths).unwrap();
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, first.frontend_port)).unwrap();

        let second = load_or_allocate(&paths).unwrap();

        assert_ne!(first.frontend_port, second.frontend_port);
        assert!(listener.local_addr().is_ok());
    }
}
