use std::env;
use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};

use crate::paths::LocalPaths;
use crate::port_reservation::PortReservation;

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
        format!(
            "http://{}:{}",
            crate::local_domain::LocalDomain::from_env().frontend_host(),
            self.frontend_port
        )
    }

    pub fn backend_url(self) -> String {
        format!(
            "http://{}:{}",
            crate::local_domain::LocalDomain::from_env().frontend_host(),
            self.backend_port
        )
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
            if existing.valid() {
                // Reserving is how the remembered pair is tested: claiming both
                // at once answers a question a pair of separate probes cannot,
                // and a reservation never listens, so a client still pointed at
                // the old address is refused here rather than accepted by a
                // socket that is about to disappear.
                if let Some(reserved) = reserve_pair(existing) {
                    reserved.release();
                    return Ok(existing);
                }
            }
        }
    }

    let (ports, reserved) = allocate_ports()?;
    // Hold both ports until the record naming them is durable. The services
    // that finally bind them are child processes started later — sometimes in a
    // later run entirely — so no reservation can reach all the way to their
    // bind; what it can do is stop a second locald racing this one from being
    // handed the very pair this call is about to write down and return.
    write_private_atomic(&path, &serde_json::to_vec_pretty(&ports)?)?;
    reserved.release();
    Ok(ports)
}

struct ReservedPair {
    frontend: PortReservation,
    backend: PortReservation,
}

impl ReservedPair {
    fn release(self) {
        self.frontend.release();
        self.backend.release();
    }

    /// Turn the frontend claim into a live listener and give the backend back.
    #[cfg(test)]
    fn occupy_frontend(self) -> io::Result<std::net::TcpListener> {
        self.backend.release();
        self.frontend.listen()
    }
}

/// Take the remembered pair back, or report that someone else owns one of them.
fn reserve_pair(ports: NetworkPorts) -> Option<ReservedPair> {
    // Claim both before judging either: a port that is tested and let go says
    // nothing about whether the pair is still ours by the time we answer.
    let frontend = PortReservation::at_loopback_port(ports.frontend_port).ok()?;
    let backend = PortReservation::at_loopback_port(ports.backend_port).ok()?;
    Some(ReservedPair { frontend, backend })
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

fn allocate_ports() -> io::Result<(NetworkPorts, ReservedPair)> {
    for _ in 0..64 {
        // Keep both reservations alive until the pair is complete so the OS
        // cannot return the same ephemeral port twice, and hand them out with
        // the numbers so the caller decides when the claim ends.
        let frontend = PortReservation::ephemeral()?;
        let backend = PortReservation::ephemeral()?;
        let frontend_port = frontend.port();
        let backend_port = backend.port();
        if frontend_port >= MIN_DYNAMIC_PORT
            && backend_port >= MIN_DYNAMIC_PORT
            && frontend_port != backend_port
        {
            return Ok((
                NetworkPorts {
                    schema_version: NETWORK_SCHEMA_VERSION,
                    frontend_port,
                    backend_port,
                    allocated_at_ms: now_ms(),
                },
                ReservedPair { frontend, backend },
            ));
        }
    }
    Err(io::Error::new(
        io::ErrorKind::AddrNotAvailable,
        "could not allocate high local ports for Lemma",
    ))
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
    use std::net::{Ipv4Addr, TcpListener};
    use tempfile::tempdir;

    #[test]
    fn a_fresh_allocation_still_owns_its_ports_while_it_is_being_recorded() {
        let (ports, _reserved) = allocate_ports().unwrap();

        // The gap between choosing the numbers and recording them is exactly
        // where a bare pair of numbers can be stolen, so nothing may bind here.
        // What happens after the release is deliberately not asserted: that
        // instant belongs to whoever asks the OS for a port next.
        assert!(TcpListener::bind((Ipv4Addr::LOCALHOST, ports.frontend_port)).is_err());
        assert!(TcpListener::bind((Ipv4Addr::LOCALHOST, ports.backend_port)).is_err());
    }

    #[test]
    fn allocated_ports_are_high_distinct_and_persistent() {
        // Persistence is only observable while the recorded ports stay free,
        // and the ports are unheld between the two calls by construction: the
        // first call has to let go before it can return numbers. If something
        // else on the machine takes one in that gap, rotating is the correct
        // answer rather than the answer under test, so the scenario is retried.
        // Code that genuinely failed to reuse its record would fail every
        // attempt.
        const ATTEMPTS: usize = 8;
        for attempt in 1..=ATTEMPTS {
            let root = tempdir().unwrap();
            let paths = LocalPaths::new(root.path().join("locald"));
            paths.ensure().unwrap();

            let first = load_or_allocate(&paths).unwrap();
            let second = load_or_allocate(&paths).unwrap();

            assert!(first.frontend_port >= MIN_DYNAMIC_PORT);
            assert!(first.backend_port >= MIN_DYNAMIC_PORT);
            assert_ne!(first.frontend_port, first.backend_port);
            if first != second {
                assert!(
                    attempt < ATTEMPTS,
                    "load_or_allocate never reused its persisted record in {ATTEMPTS} attempts"
                );
                continue;
            }
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
            return;
        }
    }

    #[test]
    fn occupied_persisted_port_rotates_without_touching_owner() {
        let root = tempdir().unwrap();
        let paths = LocalPaths::new(root.path().join("locald"));
        paths.ensure().unwrap();
        // Record a pair and let an unrelated owner take the frontend port, all
        // without the port being free for an instant: allocating and then
        // re-binding the number would leave this test racing the rest of the
        // binary for the very port it is trying to prove is occupied.
        let (first, reserved) = allocate_ports().unwrap();
        write_private_atomic(
            &paths.root.join("network.json"),
            &serde_json::to_vec_pretty(&first).unwrap(),
        )
        .unwrap();
        let listener = reserved.occupy_frontend().unwrap();

        let second = load_or_allocate(&paths).unwrap();

        assert_ne!(first.frontend_port, second.frontend_port);
        assert!(listener.local_addr().is_ok());
    }
}
