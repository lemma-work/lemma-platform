//! The paired user's browser reaching a port on the Mac's own loopback, over vsock.
//!
//! On Desktop the paired user's agent can run commands on the Mac itself (host
//! execution), so `npm run dev` listens on the Mac's `127.0.0.1:3000`. The
//! browser stays in the guest, in the paired user's workspace sandbox, where
//! `localhost` is the container. `sandbox_runtime.host_fallback` in that
//! sandbox answers a loopback port nothing there is serving by asking this
//! relay for the same port on the Mac.
//!
//! ```text
//! sandbox (host_fallback) --unix--> guestd --vsock 42413--> lemma-vz
//!     --unix--> locald loopback_relay --tcp--> 127.0.0.1:<port> on the Mac
//! ```
//!
//! **Who can use it** is decided by where the socket is, not by who asks. It
//! lives in a directory that `sandbox.ensure` bind-mounts into a container
//! only when the backend granted `host_loopback` -- which it does for the
//! workspace of the user this Mac's Agent Host is paired to and nothing else. Every other sandbox
//! has no path to it: there is no address to dial and no file to open.
//!
//! **What it can reach** is decided on the Mac, by locald, which knows which
//! ports are Lemma's own. This end only checks the request is shaped like one
//! and is not a privileged port, so a malformed or obviously refused request
//! never costs a vsock connection.
//!
//! The protocol is the tunnel's, in the other direction: the sandbox sends
//! the port and a newline; the host answers `ok` and a newline and then the
//! bytes, or `error <reason>` and closes. This end forwards the host's answer
//! untouched, so the reason a sandbox sees is the one locald gave.
//!
//! Served only by the resident `serve-vsock` guest, which is the VZ guest on
//! macOS. WSL runs guestd per request and has no host listener, so there the
//! directory stays empty, the sandbox finds no socket, and the fall-through
//! is not started at all.

use std::fs;
use std::io::{self, BufRead, BufReader, Read, Write};
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::UnixListener;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::thread;

use crate::sandbox_tunnel::{splice, TryCloneStream};

/// Where the host's VM process listens for relay connections from the guest.
/// Beside the control port (42411) and the sandbox tunnel (42412).
pub const HOST_LOOPBACK_VSOCK_PORT: u32 = 42_413;

/// The directory under the guest's state root that holds the relay socket.
pub(crate) const HOST_LOOPBACK_DIRECTORY: &str = "host-loopback";

/// The socket's name inside that directory.
pub(crate) const HOST_LOOPBACK_SOCKET: &str = "relay.sock";

/// Where the directory appears inside a granted container. The workspace
/// image's `lemma-ensure-display` and `host_fallback` look for `relay.sock`
/// here.
pub(crate) const HOST_LOOPBACK_MOUNT: &str = "/run/lemma-host-loopback";

/// The longest request line accepted: five digits and a newline, with room.
const MAX_REQUEST_BYTES: u64 = 16;

/// Relays open at once. A page's worth of assets from one dev server, plus a
/// few long-lived ones (a websocket for hot reload); beyond this a connection
/// is closed and the browser retries.
pub(crate) const MAX_OPEN_RELAYS: usize = 64;

/// The port a sandbox asked for, if the request is shaped like one.
///
/// Strict on purpose: digits and a newline, nothing else. A sandbox runs
/// whatever the web put in front of its browser, and a parser that accepted
/// `+3000` or ` 3000` would be one more thing to reason about on the host.
/// Privileged ports are refused here as well as on the host: nothing a person
/// starts with `npm run dev` binds one, and the ones below 1024 on a Mac are
/// the system's.
pub(crate) fn requested_host_port(line: &str) -> Result<u16, &'static str> {
    let digits = line.strip_suffix('\n').ok_or("not a port")?;
    if digits.is_empty() || digits.len() > 5 || !digits.bytes().all(|b| b.is_ascii_digit()) {
        return Err("not a port");
    }
    let port: u16 = digits.parse().map_err(|_| "not a port")?;
    if port < 1024 {
        return Err("privileged ports are not relayed");
    }
    Ok(port)
}

/// Serve one sandbox connection: read its request, ask the host, splice.
///
/// `dial_host` opens the vsock connection to the host's VM process; passed in
/// so the exchange can be tested with a socket pair standing in for vsock. It
/// is only called for a request that passed `requested_host_port`.
pub(crate) fn serve_relay_client<S, H, F>(client: S, dial_host: F) -> io::Result<()>
where
    S: Read + Write + Send + TryCloneStream + 'static,
    H: Read + Write + Send + TryCloneStream + 'static,
    F: FnOnce() -> io::Result<H>,
{
    let mut reader = BufReader::new(client.try_clone_stream()?);
    let mut line = String::new();
    (&mut reader).take(MAX_REQUEST_BYTES).read_line(&mut line)?;
    let mut writer = client;
    let port = match requested_host_port(&line) {
        Ok(port) => port,
        Err(reason) => {
            let _ = writer.write_all(format!("error {reason}\n").as_bytes());
            return Ok(());
        }
    };
    let mut host = match dial_host() {
        Ok(host) => host,
        Err(_) => {
            let _ = writer.write_all(b"error the host is not reachable\n");
            return Ok(());
        }
    };
    if host.write_all(format!("{port}\n").as_bytes()).is_err() {
        let _ = writer.write_all(b"error the host is not reachable\n");
        return Ok(());
    }
    // The host's `ok` or `error` travels back as the first bytes of the
    // splice, so its answer reaches the sandbox exactly as it was given.
    splice(reader, writer, host)
}

/// The directory a granted sandbox has mounted, under `state_root`.
pub(crate) fn host_loopback_directory(state_root: &Path) -> PathBuf {
    state_root.join(HOST_LOOPBACK_DIRECTORY)
}

/// Create the relay directory, writable only by whoever runs guestd.
///
/// Readable and searchable by everyone, because the sandbox user (uid 10001)
/// has to reach the socket inside; writable only by its owner, so a sandbox
/// that has it mounted can use the socket but cannot remove it or put one of
/// its own in its place.
pub(crate) fn prepare_relay_directory(directory: &Path) -> io::Result<()> {
    fs::create_dir_all(directory)?;
    fs::set_permissions(directory, fs::Permissions::from_mode(0o755))
}

/// Bind the relay socket, replacing one a previous guestd left behind.
///
/// Mode 0666 so the unprivileged sandbox user can connect. That grants
/// nothing by itself: the only containers that can see the file are the ones
/// its directory was mounted into.
pub(crate) fn bind_relay_socket(directory: &Path) -> io::Result<UnixListener> {
    prepare_relay_directory(directory)?;
    let path = directory.join(HOST_LOOPBACK_SOCKET);
    match fs::remove_file(&path) {
        Ok(()) => {}
        Err(error) if error.kind() == io::ErrorKind::NotFound => {}
        Err(error) => return Err(error),
    }
    let listener = UnixListener::bind(&path)?;
    fs::set_permissions(&path, fs::Permissions::from_mode(0o666))?;
    Ok(listener)
}

/// Accept sandbox connections for as long as the listener lives.
///
/// A thread per connection, bounded like the tunnel's: each relay is two
/// threads for its lifetime, and a browser holds some open for a while.
pub(crate) fn serve_relay<H, F>(listener: UnixListener, dial_host: F)
where
    H: Read + Write + Send + TryCloneStream + 'static,
    F: Fn() -> io::Result<H> + Send + Sync + 'static,
{
    let dial_host = Arc::new(dial_host);
    let open = Arc::new(AtomicUsize::new(0));
    loop {
        let client = match listener.accept() {
            Ok((client, _)) => client,
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) => {
                // Descriptors short, a peer gone: not a reason to stop serving.
                eprintln!("lemma-guestd: host loopback accept failed: {error}");
                thread::sleep(std::time::Duration::from_millis(100));
                continue;
            }
        };
        if open.load(Ordering::Acquire) >= MAX_OPEN_RELAYS {
            continue;
        }
        open.fetch_add(1, Ordering::AcqRel);
        let dial_host = Arc::clone(&dial_host);
        let owned = Arc::clone(&open);
        let spawned = thread::Builder::new()
            .name("guestd-host-loopback".into())
            .spawn(move || {
                let _ = serve_relay_client(client, || dial_host());
                owned.fetch_sub(1, Ordering::AcqRel);
            });
        if spawned.is_err() {
            open.fetch_sub(1, Ordering::AcqRel);
        }
    }
}
