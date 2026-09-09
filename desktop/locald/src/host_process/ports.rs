//! Holding a port idle so nothing else takes it between reserving and
//! binding.

use super::*;

pub(crate) fn loopback_http_port(url: &str) -> Option<u16> {
    let authority = url
        .strip_prefix("http://127.0.0.1:")
        .or_else(|| url.strip_prefix("http://localhost:"))?
        .split(['/', '?', '#'])
        .next()?;
    authority.parse().ok()
}

/// Hold the workspace ports, as far as that is possible.
///
/// Best effort, and deliberately so. A reservation closes the window between
/// "the manifest says port N" and "the service binds port N"; a port it cannot
/// take is a window left open, which is worse than it was and still far better
/// than refusing to run. And the usual reason it cannot be taken is not a
/// competitor at all: a service that served even one connection leaves
/// TIME_WAIT entries on its port, which a reservation cannot bind past because
/// it sets no SO_REUSEADDR -- while the service itself binds straight through
/// them. Treating that as fatal made starting again shortly after stopping
/// fail with "could not reserve Lemma's local port", for a port that was in
/// every practical sense free.
pub(crate) fn reserve_managed_app_ports(
    manifest: &HostPackManifest,
) -> HashMap<u16, PortReservation> {
    let Some(runtime) = manifest.managed_runtime.as_ref() else {
        return HashMap::new();
    };
    let mut reservations = HashMap::new();
    for port in [runtime.ports.backend, runtime.ports.frontend] {
        if let Ok(reservation) = bind_idle_port(port) {
            reservations.insert(port, reservation);
        }
    }
    reservations
}

/// Hold a workspace port while nothing is serving it.
///
/// A `PortReservation`, not a `TcpListener`, and the difference is the whole
/// of a defect. A listening socket with nobody accepting completes the TCP
/// handshake and then says nothing, so every caller that asks "is the backend
/// up yet?" -- locald's own health gate, the app, a browser -- connects
/// successfully and hangs until it gives up. On a Windows machine where the
/// backend had failed to start, that turned "the backend is not running" into
/// "backend failed health gate: connection timed out", which is the same
/// message a slow machine produces and points at nothing.
///
/// `PortReservation` binds without listening, which is exactly the behaviour
/// an idle port already has: the connection is refused. Its own module comment
/// says so, and this was the one place holding a port that did not use it.
pub(crate) fn bind_idle_port(port: u16) -> io::Result<PortReservation> {
    PortReservation::at_loopback_port(port).map_err(|error| {
        io::Error::new(
            error.kind(),
            format!("could not reserve Lemma's local port {port}: {error}"),
        )
    })
}

pub(crate) fn is_loopback(address: IpAddr) -> bool {
    address.is_loopback()
}

impl HostProcessManager {
    pub(crate) fn release_idle_port_for(&self, id: &str) {
        let Some(port) = self.managed_service_port(id) else {
            return;
        };
        self.idle_port_reservations
            .lock()
            .expect("idle port reservation lock poisoned")
            .remove(&port);
    }

    pub(crate) fn reserve_idle_ports(&self) -> io::Result<()> {
        let Some(runtime) = self.manifest.managed_runtime.as_ref() else {
            return Ok(());
        };
        let mut reservations = self
            .idle_port_reservations
            .lock()
            .expect("idle port reservation lock poisoned");
        for port in [runtime.ports.backend, runtime.ports.frontend] {
            if let std::collections::hash_map::Entry::Vacant(entry) = reservations.entry(port) {
                // Best effort, for the reasons on `reserve_managed_app_ports`.
                if let Ok(reservation) = bind_idle_port(port) {
                    entry.insert(reservation);
                }
            }
        }
        Ok(())
    }

    pub(crate) fn managed_service_port(&self, id: &str) -> Option<u16> {
        let ports = &self.manifest.managed_runtime.as_ref()?.ports;
        match id {
            "backend" => Some(ports.backend),
            "frontend" => Some(ports.frontend),
            _ => None,
        }
    }
}
