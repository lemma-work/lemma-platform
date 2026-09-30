//! The guest's DNS, answered by the Mac's own resolver, over vsock.
//!
//! The VZ guest is on macOS NAT, and DHCP hands it vmnet's gateway as its only
//! name server. vmnet forwards those queries itself, and when the Mac's DNS is
//! a local proxy -- Cloudflare WARP, Tailscale MagicDNS, Zscaler, a VPN client
//! with split DNS -- that forwarding fails while the Mac resolves the same name
//! without trouble. A first install then cannot pull a single image.
//!
//! So the guest has a second server: this relay, on `127.0.0.2:53`, which
//! hands each query to `lemma-vz` over vsock, where the Mac's resolver answers
//! it exactly as it would for any app on the Mac.
//!
//! ```text
//! nerdctl --> resolved (127.0.0.53) --udp/tcp--> 127.0.0.2:53 (here)
//!     --vsock 42414--> lemma-vz HostDNSBridge --> the Mac's resolver
//!                   \-> the DHCP gateway, as before
//! ```
//!
//! systemd-resolved is given both (`resolved.conf.d/lemma.conf` names this
//! one, DHCP still supplies the gateway) and takes the first good answer, so a
//! relay that is down, slow or answering SERVFAIL leaves the guest resolving
//! exactly as it did before the relay existed. No public resolver is ever
//! added: that would bypass the split DNS and the DNS policy this exists to
//! respect.
//!
//! **Wire format to the host.** One query per vsock stream, framed as DNS over
//! TCP is (a two-byte big-endian length, then the message); the answer comes
//! back framed the same way. That is also exactly what a TCP client sends
//! here, so both listeners share one forwarding path.
//!
//! Only the resident `serve-vsock` guest serves it, which is the VZ guest on
//! macOS. WSL resolves through Windows (`generateResolvConf`) and never starts
//! it. Sandbox containers are in their own network namespaces, where this
//! address is their own loopback: they keep the gateway's DNS, which their
//! firewall already allows.

use std::fs::File;
use std::io::{self, Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream, UdpSocket};
use std::os::fd::{AsRawFd, OwnedFd};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::Duration;

/// Where the host's VM process answers DNS. Beside the loopback relay (42413).
pub const HOST_DNS_VSOCK_PORT: u32 = 42_414;

/// The guest address resolved is pointed at. Not `127.0.0.54`, which is
/// resolved's own proxy-mode stub; `127.0.0.2` is on `lo` like every other
/// 127/8 address and needs no configuration to bind.
pub(crate) const HOST_DNS_ADDRESS: &str = "127.0.0.2:53";

/// The largest query forwarded. The host refuses anything larger too
/// (`HostDNSBridge.maximumQueryBytes`); a real query is a few hundred bytes.
pub(crate) const MAX_QUERY_BYTES: usize = 4096;

/// How long one exchange with the host may take, each way. Longer than the
/// host's own four-second resolver deadline, so its SERVFAIL arrives first.
pub(crate) const HOST_EXCHANGE_TIMEOUT: Duration = Duration::from_secs(5);

/// Queries in flight at once, per listener. Beyond this a UDP query is dropped
/// (resolved retries it, or has its answer from the gateway already) and a
/// TCP connection is closed.
pub(crate) const MAX_IN_FLIGHT: usize = 64;

/// How long a TCP client may sit idle between queries.
const TCP_IDLE_TIMEOUT: Duration = Duration::from_secs(10);

/// The UDP answer size a client that does not say otherwise can take.
const CLASSIC_UDP_LIMIT: usize = 512;

const HEADER: usize = 12;
const TYPE_OPT: u16 = 41;
const RCODE_SERVFAIL: u16 = 2;

fn read16(message: &[u8], at: usize) -> u16 {
    u16::from_be_bytes([message[at], message[at + 1]])
}

/// Where the (single) question ends, if the message has one that parses.
fn question_end(message: &[u8]) -> Option<usize> {
    if message.len() < HEADER || read16(message, 4) != 1 {
        return None;
    }
    let mut at = HEADER;
    loop {
        let length = usize::from(*message.get(at)?);
        at += 1;
        if length == 0 {
            break;
        }
        // No compression pointers in a question, and labels are at most 63.
        if length > 63 {
            return None;
        }
        at += length;
    }
    (at + 4 <= message.len()).then_some(at + 4)
}

/// SERVFAIL for `query`, with its question echoed when it has one.
///
/// SERVFAIL is what tells resolved to use its other server; any other answer
/// would be believed. `None` for something with no id to answer, or that is
/// itself a response.
pub(crate) fn server_failure(query: &[u8]) -> Option<Vec<u8>> {
    if query.len() < HEADER || read16(query, 2) & 0x8000 != 0 {
        return None;
    }
    let flags = read16(query, 2);
    let reply = 0x8000 | (flags & 0x7800) | (flags & 0x0100) | 0x0080 | RCODE_SERVFAIL;
    let question = question_end(query).map(|end| &query[HEADER..end]);
    let mut out = Vec::with_capacity(HEADER + question.map_or(0, <[u8]>::len));
    out.extend_from_slice(&query[..2]);
    out.extend_from_slice(&reply.to_be_bytes());
    out.extend_from_slice(&u16::from(question.is_some()).to_be_bytes());
    out.extend_from_slice(&[0; 6]);
    out.extend_from_slice(question.unwrap_or_default());
    Some(out)
}

/// The answer to `query`: the host's, or SERVFAIL if the host did not give
/// one that belongs to it.
///
/// `dial_host` opens the vsock stream; passed in so a socket pair can stand in
/// for it. Its reads and writes must be bounded, which is how a host that never
/// answers becomes a SERVFAIL rather than a hung thread.
pub(crate) fn forward<H, F>(query: &[u8], dial_host: F) -> Option<Vec<u8>>
where
    H: Read + Write,
    F: FnOnce() -> io::Result<H>,
{
    if query.len() > MAX_QUERY_BYTES {
        return server_failure(query);
    }
    match ask_host(query, dial_host) {
        Ok(answer) if answer.len() >= HEADER && answer[..2] == query[..2] => Some(answer),
        _ => server_failure(query),
    }
}

fn ask_host<H: Read + Write, F: FnOnce() -> io::Result<H>>(
    query: &[u8],
    dial_host: F,
) -> io::Result<Vec<u8>> {
    let length = u16::try_from(query.len()).map_err(|_| io::ErrorKind::InvalidInput)?;
    let mut host = dial_host()?;
    let mut framed = Vec::with_capacity(query.len() + 2);
    framed.extend_from_slice(&length.to_be_bytes());
    framed.extend_from_slice(query);
    host.write_all(&framed)?;
    let mut length = [0_u8; 2];
    host.read_exact(&mut length)?;
    // At most 64 KiB, by the framing itself.
    let mut answer = vec![0_u8; usize::from(u16::from_be_bytes(length))];
    host.read_exact(&mut answer)?;
    Ok(answer)
}

/// The largest UDP answer the client asked for: its EDNS payload size, or 512.
pub(crate) fn udp_answer_limit(query: &[u8]) -> usize {
    let Some(end) = question_end(query) else {
        return CLASSIC_UDP_LIMIT;
    };
    let only_opt_follows = read16(query, 6) == 0 && read16(query, 8) == 0 && read16(query, 10) >= 1;
    if only_opt_follows
        && query.len() >= end + 11
        && query[end] == 0
        && read16(query, end + 1) == TYPE_OPT
    {
        usize::from(read16(query, end + 3)).max(CLASSIC_UDP_LIMIT)
    } else {
        CLASSIC_UDP_LIMIT
    }
}

/// `answer`, cut to what fits the client's UDP limit.
///
/// Too large, it becomes its header and question with TC set, which sends
/// the client back over TCP -- where the whole answer fits.
pub(crate) fn fit_udp(answer: Vec<u8>, query: &[u8]) -> Vec<u8> {
    if answer.len() <= udp_answer_limit(query) {
        return answer;
    }
    let end = question_end(&answer).unwrap_or(HEADER);
    let mut truncated = answer[..end].to_vec();
    truncated[2] |= 0x02;
    truncated[4..6].copy_from_slice(&u16::from(end > HEADER).to_be_bytes());
    truncated[6..12].fill(0);
    truncated
}

/// One slot of `MAX_IN_FLIGHT`, given back when dropped.
struct Slot(Arc<AtomicUsize>);

impl Slot {
    fn take(open: &Arc<AtomicUsize>) -> Option<Slot> {
        let taken = open.fetch_add(1, Ordering::AcqRel);
        if taken >= MAX_IN_FLIGHT {
            open.fetch_sub(1, Ordering::AcqRel);
            return None;
        }
        Some(Slot(Arc::clone(open)))
    }
}

impl Drop for Slot {
    fn drop(&mut self) {
        self.0.fetch_sub(1, Ordering::AcqRel);
    }
}

/// Answer UDP queries for as long as the socket lives. A thread per query,
/// because each waits on the host for up to `HOST_EXCHANGE_TIMEOUT`.
pub(crate) fn serve_udp<H, F>(socket: UdpSocket, dial_host: Arc<F>)
where
    H: Read + Write,
    F: Fn() -> io::Result<H> + Send + Sync + 'static,
{
    let socket = Arc::new(socket);
    let open = Arc::new(AtomicUsize::new(0));
    let mut buffer = vec![0_u8; MAX_QUERY_BYTES];
    loop {
        let (length, peer) = match socket.recv_from(&mut buffer) {
            Ok(received) => received,
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) => {
                eprintln!("lemma-guestd: host DNS receive failed: {error}");
                thread::sleep(Duration::from_millis(100));
                continue;
            }
        };
        let Some(slot) = Slot::take(&open) else {
            continue;
        };
        let query = buffer[..length].to_vec();
        let socket = Arc::clone(&socket);
        let dial_host = Arc::clone(&dial_host);
        let _ = thread::Builder::new()
            .name("guestd-host-dns-udp".into())
            .spawn(move || {
                let _slot = slot;
                if let Some(answer) = forward(&query, || dial_host()) {
                    let _ = socket.send_to(&fit_udp(answer, &query), peer);
                }
            });
    }
}

/// Answer TCP clients for as long as the listener lives, several queries per
/// connection as RFC 7766 allows.
pub(crate) fn serve_tcp<H, F>(listener: TcpListener, dial_host: Arc<F>)
where
    H: Read + Write,
    F: Fn() -> io::Result<H> + Send + Sync + 'static,
{
    let open = Arc::new(AtomicUsize::new(0));
    loop {
        let client = match listener.accept() {
            Ok((client, _)) => client,
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) => {
                eprintln!("lemma-guestd: host DNS accept failed: {error}");
                thread::sleep(Duration::from_millis(100));
                continue;
            }
        };
        let Some(slot) = Slot::take(&open) else {
            continue;
        };
        let dial_host = Arc::clone(&dial_host);
        let _ = thread::Builder::new()
            .name("guestd-host-dns-tcp".into())
            .spawn(move || {
                let _slot = slot;
                let _ = serve_tcp_client(client, &*dial_host);
            });
    }
}

fn serve_tcp_client<H, F>(mut client: TcpStream, dial_host: &F) -> io::Result<()>
where
    H: Read + Write,
    F: Fn() -> io::Result<H>,
{
    client.set_read_timeout(Some(TCP_IDLE_TIMEOUT))?;
    client.set_write_timeout(Some(HOST_EXCHANGE_TIMEOUT))?;
    loop {
        let mut length = [0_u8; 2];
        client.read_exact(&mut length)?;
        let length = usize::from(u16::from_be_bytes(length));
        if !(HEADER..=MAX_QUERY_BYTES).contains(&length) {
            return Ok(());
        }
        let mut query = vec![0_u8; length];
        client.read_exact(&mut query)?;
        let Some(answer) = forward(&query, dial_host) else {
            return Ok(());
        };
        let mut framed = Vec::with_capacity(answer.len() + 2);
        framed.extend_from_slice(&(answer.len() as u16).to_be_bytes());
        framed.extend_from_slice(&answer);
        client.write_all(&framed)?;
    }
}

/// Bind both listeners on `address` and serve them on their own threads.
///
/// An error only when neither could be bound; one alone still helps.
pub(crate) fn start<H, F>(address: &str, dial_host: F) -> io::Result<()>
where
    H: Read + Write + 'static,
    F: Fn() -> io::Result<H> + Send + Sync + 'static,
{
    let dial_host = Arc::new(dial_host);
    let udp = UdpSocket::bind(address);
    let tcp = TcpListener::bind(address);
    if let (Err(error), Err(_)) = (&udp, &tcp) {
        return Err(io::Error::new(error.kind(), error.to_string()));
    }
    if let Ok(socket) = udp {
        let dial_host = Arc::clone(&dial_host);
        thread::Builder::new()
            .name("guestd-host-dns-udp-accept".into())
            .spawn(move || serve_udp(socket, dial_host))?;
    }
    if let Ok(listener) = tcp {
        thread::Builder::new()
            .name("guestd-host-dns-tcp-accept".into())
            .spawn(move || serve_tcp(listener, dial_host))?;
    }
    Ok(())
}

/// A connected stream whose reads and writes give up after `timeout`.
///
/// The vsock stream is a plain descriptor, with no `set_read_timeout` of its
/// own, and an unbounded read is how a host that never answers would hold a
/// thread and a slot forever.
pub(crate) fn bounded_stream(stream: OwnedFd, timeout: Duration) -> io::Result<File> {
    let value = libc::timeval {
        tv_sec: timeout.as_secs() as libc::time_t,
        tv_usec: timeout.subsec_micros() as libc::suseconds_t,
    };
    for option in [libc::SO_RCVTIMEO, libc::SO_SNDTIMEO] {
        // SAFETY: a descriptor we own and a correctly sized timeval.
        let result = unsafe {
            libc::setsockopt(
                stream.as_raw_fd(),
                libc::SOL_SOCKET,
                option,
                &value as *const _ as *const libc::c_void,
                std::mem::size_of::<libc::timeval>() as libc::socklen_t,
            )
        };
        if result != 0 {
            return Err(io::Error::last_os_error());
        }
    }
    Ok(File::from(stream))
}

/// A standard recursive query for `name`'s A record.
pub(crate) fn address_query(name: &str, id: u16) -> Vec<u8> {
    let mut query = Vec::with_capacity(HEADER + name.len() + 6);
    query.extend_from_slice(&id.to_be_bytes());
    query.extend_from_slice(&[0x01, 0x00, 0, 1, 0, 0, 0, 0, 0, 0]);
    for label in name.split('.').filter(|label| !label.is_empty()) {
        query.push(label.len().min(63) as u8);
        query.extend_from_slice(&label.as_bytes()[..label.len().min(63)]);
    }
    query.extend_from_slice(&[0, 0, 1, 0, 1]);
    query
}

/// Whether the server at `server` resolves `name` to at least one address.
///
/// For diagnostics: asked of this relay directly, it says whether the Mac's
/// resolver can answer a name the guest cannot, which is the difference
/// between "the gateway's DNS is broken" and "there is no network".
pub(crate) fn answers_address(server: SocketAddr, name: &str, timeout: Duration) -> bool {
    let probe = || -> io::Result<bool> {
        let socket = UdpSocket::bind(if server.is_ipv4() {
            "127.0.0.1:0"
        } else {
            "[::1]:0"
        })?;
        socket.set_read_timeout(Some(timeout))?;
        socket.connect(server)?;
        let query = address_query(name, 0x4c4d);
        socket.send(&query)?;
        let mut answer = [0_u8; 1500];
        let length = socket.recv(&mut answer)?;
        let answer = &answer[..length];
        Ok(length >= HEADER
            && answer[..2] == query[..2]
            && read16(answer, 2) & 0x000F == 0
            && read16(answer, 6) > 0)
    };
    probe().unwrap_or(false)
}
