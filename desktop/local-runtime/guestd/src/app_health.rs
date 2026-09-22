//! Whether a sandbox's app is answering, as opposed to merely published.

use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::time::Duration;

/// How long an app gets to answer before it is reported as not answering.
///
/// This runs inside the guest against a container on the same host, so a
/// healthy app answers in single-digit milliseconds and the bound is really
/// about how long a refusal is allowed to hold up a snapshot. A second rather
/// than a few hundred milliseconds because the same code runs in tests on
/// shared CI runners, where a thread may simply not be scheduled promptly, and
/// a health probe that reports a fault under load is worse than a slow one.
const PROBE_TIMEOUT: Duration = Duration::from_secs(1);

/// Ask an app's declared health path whether it is serving.
///
/// `ready` used to be `running && host_port.is_some()` -- a container that is
/// up and a port that is mapped. That is not the same claim, and the difference
/// is exactly what broke: the browser and its relay were reported `ready: true`
/// while both ports refused every connection, so the backend dialled an
/// endpoint the guest had just promised was good.
///
/// Any HTTP response counts, including 401. The runtime requires a credential
/// this process does not hold, and the question being asked is "is something
/// serving here", which a refusal answers as well as an acceptance. A refused
/// connection, a timeout, or a reply that is not HTTP answers it too.
pub(crate) fn app_is_answering(host: &str, port: u16, health_path: &str) -> bool {
    let Ok(address) = format!("{host}:{port}").parse::<SocketAddr>() else {
        return false;
    };
    let path = if health_path.starts_with('/') {
        health_path.to_owned()
    } else {
        format!("/{health_path}")
    };
    probe(&address, &path).unwrap_or(false)
}

fn probe(address: &SocketAddr, path: &str) -> std::io::Result<bool> {
    let mut stream = TcpStream::connect_timeout(address, PROBE_TIMEOUT)?;
    stream.set_read_timeout(Some(PROBE_TIMEOUT))?;
    stream.set_write_timeout(Some(PROBE_TIMEOUT))?;
    write!(
        stream,
        "GET {path} HTTP/1.1\r\nHost: {address}\r\nConnection: close\r\n\r\n"
    )?;
    // The status line is the whole answer, so only enough of it is read to see
    // one: a health probe that drains a body would make a large error page
    // expensive to receive.
    let mut head = [0_u8; 16];
    let mut filled = 0;
    while filled < head.len() {
        match stream.read(&mut head[filled..]) {
            Ok(0) => break,
            Ok(count) => filled += count,
            Err(error) if error.kind() == std::io::ErrorKind::Interrupted => continue,
            Err(error) => return Err(error),
        }
    }
    Ok(head[..filled].starts_with(b"HTTP/1."))
}
