use std::env;
use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::net::{Ipv4Addr, SocketAddr, TcpStream};
use std::path::Path;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

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

/// Whether both remembered ports are free of a live server.
///
/// Deliberately a connect rather than a bind: a bind cannot tell a port that is
/// being served from one that merely held a connection recently, and it is the
/// difference between the two that decides whether reusing the pair is safe.
/// Loopback only, which is where the services themselves listen.
fn nothing_is_serving(ports: NetworkPorts) -> bool {
    [ports.frontend_port, ports.backend_port]
        .into_iter()
        .all(|port| {
            let address = SocketAddr::from((Ipv4Addr::LOCALHOST, port));
            TcpStream::connect_timeout(&address, Duration::from_millis(250)).is_err()
        })
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
                // A refused reservation is not proof the pair is taken.
                //
                // The reservation binds without SO_REUSEADDR, so it also fails
                // on the TIME_WAIT our *own* previous run leaves behind: the
                // backend served requests, exited, and for the next half minute
                // nothing can bind that port even though nothing is serving on
                // it. Every restart inside that window abandoned the remembered
                // pair and allocated a new one.
                //
                // The cost was not cosmetic. The Agent Host stores its pairing
                // as a URL with the port in it, so a reallocation left it
                // talking to a port nothing answers -- this computer stopped
                // being reachable until it was paired again. The recorded
                // resume target went stale the same way, which is why every
                // launch landed on the auth portal instead of the workspace.
                //
                // So ask the question the reservation was standing in for: is
                // anything actually serving here? A listener answers a connect;
                // a TIME_WAIT socket refuses it. Only the second is safe to
                // reuse, and it is the common case.
                if nothing_is_serving(existing) {
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
    /// A restart must keep the ports the last run used.
    ///
    /// The Agent Host stores its pairing as a URL with the port in it, and the
    /// resume target records one too. Reallocating on restart strands both --
    /// this computer stops being reachable, and every launch lands on the auth
    /// portal instead of the workspace.
    ///
    /// The trap is that the reservation binds without SO_REUSEADDR, so it fails
    /// on the TIME_WAIT our own previous run leaves: a port that served a
    /// connection cannot be re-bound for about half a minute even though
    /// nothing is serving on it. This reproduces that exactly -- serve a
    /// connection, close everything, then ask.
    #[test]
    fn a_port_our_last_run_used_is_still_reusable_while_it_lingers() {
        use std::io::Read;
        use std::net::{Ipv4Addr, TcpListener, TcpStream};

        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        let port = listener.local_addr().unwrap().port();
        let mut client = TcpStream::connect((Ipv4Addr::LOCALHOST, port)).unwrap();
        let (server, _) = listener.accept().unwrap();
        drop(server);
        let mut sink = Vec::new();
        let _ = client.read_to_end(&mut sink);
        drop(client);
        drop(listener);

        let ports = NetworkPorts {
            schema_version: NETWORK_SCHEMA_VERSION,
            frontend_port: port,
            backend_port: port,
            allocated_at_ms: 0,
        };
        assert!(
            nothing_is_serving(ports),
            "a port whose server has gone must read as free, or every restart \
             inside TIME_WAIT hands out new ports and strands the Agent Host",
        );

        // And a port that *is* being served must not.
        let live = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        let live_port = live.local_addr().unwrap().port();
        let served = NetworkPorts {
            schema_version: NETWORK_SCHEMA_VERSION,
            frontend_port: live_port,
            backend_port: live_port,
            allocated_at_ms: 0,
        };
        assert!(!nothing_is_serving(served));
        drop(live);

        // A pair is only reusable if *both* halves are free.
        let mixed = NetworkPorts {
            schema_version: NETWORK_SCHEMA_VERSION,
            frontend_port: port,
            backend_port: TcpListener::bind((Ipv4Addr::LOCALHOST, 0))
                .map(|l| {
                    let p = l.local_addr().unwrap().port();
                    std::mem::forget(l);
                    p
                })
                .unwrap(),
            allocated_at_ms: 0,
        };
        assert!(!nothing_is_serving(mixed));
    }

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
