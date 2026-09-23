//! Accepting connections over vsock.

use super::*;

#[cfg(target_os = "linux")]
pub fn serve_vsock<E: Engine + 'static>(service: &GuestService<E>) -> io::Result<()> {
    // Named rather than reached through the crate root: this is the only
    // caller outside `protocol`, and only on Linux, so a re-export at the
    // root would be dead code on every other host -- which `-D warnings`
    // makes an error rather than a warning.
    use crate::protocol::handle_stream;
    use std::sync::atomic::{AtomicUsize, Ordering};

    let tunnels = service.clone();
    thread::Builder::new()
        .name("guestd-tunnels".into())
        .spawn(move || {
            if let Err(error) = serve_tunnels(&tunnels) {
                eprintln!("lemma-guestd: sandbox tunnel listener stopped: {error}");
            }
        })?;

    let listener = vsock::listen(VSOCK_PORT)?;
    let connections = Arc::new(AtomicUsize::new(0));
    loop {
        let connection = vsock::accept(&listener)?;
        let reader = std::fs::File::from(connection.try_clone()?);
        let writer = std::fs::File::from(connection);
        // One thread per connection, rather than serving each to completion
        // inside the accept loop: a long operation -- a callback wait, an image
        // pull -- must not leave the health probe in the listen backlog.
        // Mutating operations are still serialised, inside `handle`.
        if connections.load(Ordering::Acquire) >= MAX_CONCURRENT_CONNECTIONS {
            // Closing is the honest answer: the host retries, and an
            // unbounded thread per connection is a worse failure than a
            // refused one.
            continue;
        }
        connections.fetch_add(1, Ordering::AcqRel);
        let service = service.clone();
        let owned = Arc::clone(&connections);
        if let Err(error) = thread::Builder::new()
            .name("guestd-connection".into())
            .spawn(move || {
                let _ = handle_stream(reader, writer, &service);
                owned.fetch_sub(1, Ordering::AcqRel);
            })
        {
            connections.fetch_sub(1, Ordering::AcqRel);
            return Err(error);
        }
    }
}

/// Streams from the host to sandbox ports. See `sandbox_tunnel`.
#[cfg(target_os = "linux")]
fn serve_tunnels<E: Engine + 'static>(service: &GuestService<E>) -> io::Result<()> {
    use crate::sandbox_tunnel::{connect_published, serve_tunnel, TUNNEL_VSOCK_PORT};
    use std::sync::atomic::{AtomicUsize, Ordering};

    let listener = vsock::listen(TUNNEL_VSOCK_PORT)?;
    let open = Arc::new(AtomicUsize::new(0));
    loop {
        // A failed accept (descriptors short, a peer gone) must not end the
        // listener: every sandbox port would stay unreachable until restart.
        let connection = match vsock::accept(&listener) {
            Ok(connection) => std::fs::File::from(connection),
            Err(error) => {
                eprintln!("lemma-guestd: tunnel accept failed: {error}");
                thread::sleep(std::time::Duration::from_millis(100));
                continue;
            }
        };
        // Each tunnel is two threads for its lifetime -- a browser viewer holds
        // one open for as long as it watches -- so they are bounded like the
        // control connections, with room for a page's worth of assets.
        if open.load(Ordering::Acquire) >= MAX_OPEN_TUNNELS {
            continue;
        }
        open.fetch_add(1, Ordering::AcqRel);
        let service = service.clone();
        let owned = Arc::clone(&open);
        let spawned = thread::Builder::new()
            .name("guestd-tunnel".into())
            .spawn(move || {
                let _ = serve_tunnel(connection, |port| {
                    let host = service.routable_endpoint_host().map_err(|error| {
                        io::Error::new(io::ErrorKind::NotConnected, error.message)
                    })?;
                    connect_published(&host, port)
                });
                owned.fetch_sub(1, Ordering::AcqRel);
            });
        if spawned.is_err() {
            open.fetch_sub(1, Ordering::AcqRel);
        }
    }
}

/// Tunnels open at once. A viewer, a terminal and a page of app assets each
/// hold some; beyond this a new one is refused and the host retries.
#[cfg(target_os = "linux")]
const MAX_OPEN_TUNNELS: usize = 128;

/// Listening and accepting on `AF_VSOCK`.
#[cfg(target_os = "linux")]
mod vsock {
    use std::io;
    use std::mem::{size_of, zeroed};
    use std::os::fd::{AsRawFd, FromRawFd, OwnedFd};

    pub(super) fn listen(port: u32) -> io::Result<OwnedFd> {
        // SAFETY: an initialized sockaddr_vm, checked return codes, and the
        // descriptor owned by `OwnedFd` from the moment it exists.
        unsafe {
            let raw = libc::socket(libc::AF_VSOCK, libc::SOCK_STREAM | libc::SOCK_CLOEXEC, 0);
            if raw < 0 {
                return Err(io::Error::last_os_error());
            }
            let listener = OwnedFd::from_raw_fd(raw);
            let mut address: libc::sockaddr_vm = zeroed();
            address.svm_family = libc::AF_VSOCK as libc::sa_family_t;
            address.svm_cid = libc::VMADDR_CID_ANY;
            address.svm_port = port;
            if libc::bind(
                raw,
                &address as *const _ as *const libc::sockaddr,
                size_of::<libc::sockaddr_vm>() as libc::socklen_t,
            ) != 0
            {
                return Err(io::Error::last_os_error());
            }
            if libc::listen(raw, 16) != 0 {
                return Err(io::Error::last_os_error());
            }
            Ok(listener)
        }
    }

    pub(super) fn accept(listener: &OwnedFd) -> io::Result<OwnedFd> {
        loop {
            // SAFETY: a listening descriptor we own; the accepted one is owned
            // by `OwnedFd` immediately.
            let accepted = unsafe {
                libc::accept4(
                    listener.as_raw_fd(),
                    std::ptr::null_mut(),
                    std::ptr::null_mut(),
                    libc::SOCK_CLOEXEC,
                )
            };
            if accepted >= 0 {
                // SAFETY: just returned by accept4 and owned by nothing else.
                return Ok(unsafe { OwnedFd::from_raw_fd(accepted) });
            }
            let error = io::Error::last_os_error();
            if error.kind() != io::ErrorKind::Interrupted {
                return Err(error);
            }
        }
    }
}

#[cfg(not(target_os = "linux"))]
pub fn serve_vsock<E: Engine + 'static>(_service: &GuestService<E>) -> io::Result<()> {
    Err(io::Error::new(
        io::ErrorKind::Unsupported,
        "AF_VSOCK guest service is Linux-only",
    ))
}
