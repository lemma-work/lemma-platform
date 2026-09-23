//! A stream from the host to one of this guest's sandbox ports, over vsock.
//!
//! The host's backend reaches a sandbox's runtime, browser relay and apps at
//! ports the engine publishes on this guest's address. Dialling that address
//! from the host is a connection to a device on the local network, which macOS
//! gates behind a per-executable Local Network permission that a background
//! process cannot be prompted for -- and the backend's executable changes with
//! every release. Core services never needed it: they arrive over vsock. This
//! gives sandbox ports the same route.
//!
//! The protocol is one line, then bytes. The host sends the port it wants and a
//! newline; this answers `ok` and a newline, then splices the two streams, or
//! answers `error <reason>` and closes.

use std::io::{self, BufRead, BufReader, Read, Write};
use std::net::{Shutdown, TcpStream};
use std::thread;
use std::time::Duration;

/// Where the host opens a tunnel. Beside the control port, `VSOCK_PORT`.
pub const TUNNEL_VSOCK_PORT: u32 = 42_412;

/// The guest's own services, which are reached over their own vsock ports and
/// must not be reachable through this one.
const RESERVED_PORTS: [u16; 3] = [5432, 6379, 3567];

/// The longest request line accepted: a port and a newline, with room to spare.
const MAX_REQUEST_BYTES: u64 = 16;

/// The port a request names, if it is one a sandbox could publish.
///
/// Published ports are unprivileged, and the guest's own services are refused
/// by number: they bind the guest's loopback and have vsock routes of their
/// own, so a tunnel to one could only ever be a mistake or a probe.
pub(crate) fn requested_port(line: &str) -> Result<u16, &'static str> {
    let port: u16 = line.trim().parse().map_err(|_| "not a port")?;
    if port < 1024 {
        return Err("privileged ports are not sandbox ports");
    }
    if RESERVED_PORTS.contains(&port) {
        return Err("that port is one of the guest's own services");
    }
    Ok(port)
}

/// Serve one tunnel: read the request, connect, answer, and splice.
///
/// `connect` is how the guest reaches a port on itself, passed in so the
/// protocol can be tested without an engine or a vsock listener.
pub(crate) fn serve_tunnel<S, F>(stream: S, connect: F) -> io::Result<()>
where
    S: Read + Write + Send + TryCloneStream + 'static,
    F: FnOnce(u16) -> io::Result<TcpStream>,
{
    let mut reader = BufReader::new(stream.try_clone_stream()?);
    let mut line = String::new();
    (&mut reader).take(MAX_REQUEST_BYTES).read_line(&mut line)?;
    let mut writer = stream;
    let port = match requested_port(&line) {
        Ok(port) => port,
        Err(reason) => {
            let _ = writer.write_all(format!("error {reason}\n").as_bytes());
            return Ok(());
        }
    };
    let upstream = match connect(port) {
        Ok(upstream) => upstream,
        Err(error) => {
            let _ = writer.write_all(format!("error {error}\n").as_bytes());
            return Ok(());
        }
    };
    writer.write_all(b"ok\n")?;
    splice(reader, writer, upstream)
}

/// Copy both directions until either side closes.
///
/// Anything the reader already buffered past the request line is forwarded
/// first: a client may send its HTTP request immediately after the port.
fn splice<S>(mut from_host: BufReader<S>, mut to_host: S, upstream: TcpStream) -> io::Result<()>
where
    S: Read + Write + Send + 'static,
{
    let mut to_sandbox = upstream.try_clone()?;
    let mut from_sandbox = upstream;
    let outbound = thread::Builder::new()
        .name("guestd-tunnel-out".into())
        .spawn(move || {
            let _ = io::copy(&mut from_host, &mut to_sandbox);
            let _ = to_sandbox.shutdown(Shutdown::Write);
        })?;
    let _ = io::copy(&mut from_sandbox, &mut to_host);
    let _ = from_sandbox.shutdown(Shutdown::Both);
    let _ = outbound.join();
    Ok(())
}

/// Connect to `port` on this guest's own address, where the engine publishes.
pub(crate) fn connect_published(host: &str, port: u16) -> io::Result<TcpStream> {
    let address = format!("{host}:{port}");
    let target = std::net::ToSocketAddrs::to_socket_addrs(&address)?
        .next()
        .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "no address for the guest"))?;
    TcpStream::connect_timeout(&target, Duration::from_secs(5))
}

/// A stream that can be split into a reader and a writer.
///
/// `TcpStream` and the vsock connection's `File` both can, but through
/// different methods; this names the one thing `serve_tunnel` needs.
pub(crate) trait TryCloneStream: Sized {
    fn try_clone_stream(&self) -> io::Result<Self>;
}

impl TryCloneStream for TcpStream {
    fn try_clone_stream(&self) -> io::Result<Self> {
        self.try_clone()
    }
}

impl TryCloneStream for std::fs::File {
    fn try_clone_stream(&self) -> io::Result<Self> {
        self.try_clone()
    }
}
