//! The Mac's end of the loopback relay: the owner's VM browser reaching a
//! server on this computer's `127.0.0.1`.
//!
//! With host execution the owner's agent runs `npm run dev` on the Mac, and the
//! browser it checks the result with runs in a sandbox in the guest. The
//! sandbox's `host_fallback` proxy sends a loopback port nothing in the sandbox
//! serves to guestd, which forwards it over vsock to `lemma-vz`, which connects
//! here. The path, and who can use it, is described in
//! `docs/architecture/desktop-security.md#the-loopback-relay`; this end decides
//! *what* can be reached.
//!
//! The protocol is one line, then bytes: the port and a newline in; `ok` and a
//! newline, then the stream, or `error <reason>` and a close, out.
//!
//! What it will connect to:
//!
//! - **Nothing, unless the owner has turned on "Run commands on this Mac".**
//!   The relay exists so the owner's agent can check a server it started on
//!   the Mac; with host execution off there is no such server to check, and
//!   the relay admits nothing. Read from the Agent Host's config on every
//!   connection (see [`HostExecution`]), so turning the switch off closes the
//!   relay for the next request without a restart.
//! - **Loopback only.** `127.0.0.1`, then `::1` -- a dev server started with
//!   `localhost` binds whichever one the resolver gave it first. Never a name
//!   and never another address: the request carries a port and nothing else.
//! - **Not a privileged port.** Below 1024 on a Mac is the system's, and
//!   nothing a person starts with `npm run dev` binds one.
//! - **Not one of Lemma's own ports.** The backend, the frontend, the private
//!   service forwards, the sharing gateway and its tunnel's local API, the
//!   Agent Host's MCP relays -- asked for afresh on every connection (see
//!   [`LemmaPorts`]), because several are chosen at run time and one that
//!   starts after this relay must still be refused. The owner's own sandbox is
//!   still a place web content runs, and a page that could make it fetch
//!   `localhost:<backend>` would be talking to Lemma with nobody's session.

use std::collections::BTreeSet;
use std::future::Future;
use std::io;
use std::net::{Ipv4Addr, Ipv6Addr, SocketAddr};
#[cfg(unix)]
use std::path::{Path, PathBuf};
use std::sync::Arc;
#[cfg(unix)]
use std::thread::{self, JoinHandle};
use std::time::Duration;

use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};
use tokio::net::TcpStream;
#[cfg(unix)]
use tokio::sync::oneshot;
#[cfg(unix)]
use tokio::task::JoinSet;

/// Relays open at once, across every sandbox connection.
const MAX_RELAYS: usize = 128;
/// How long a connection may take to say which port it wants.
const REQUEST_TIMEOUT: Duration = Duration::from_secs(5);
/// How long to wait for the Mac's own server. It is loopback: it answers or
/// refuses at once, and this only bounds a listener with a full backlog.
const CONNECT_TIMEOUT: Duration = Duration::from_secs(5);
/// Five digits and a newline, with room.
const MAX_REQUEST_BYTES: usize = 16;
/// The lowest port the relay will connect to.
pub(crate) const FIRST_UNPRIVILEGED_PORT: u16 = 1024;

/// The ports Lemma itself is listening on right now.
///
/// A function rather than a set because the answer changes while the relay
/// runs: sharing starts a gateway on a port the OS picks, a tunnel's local API
/// with it, and the Agent Host opens an MCP relay per paired workspace.
pub(crate) type LemmaPorts = Arc<dyn Fn() -> BTreeSet<u16> + Send + Sync>;

/// Whether the owner has host execution on, as of now.
pub(crate) type HostExecution = Arc<dyn Fn() -> bool + Send + Sync>;

/// What every connection is judged against, each part asked for afresh.
#[derive(Clone)]
pub(crate) struct RelayPolicy {
    pub(crate) host_execution: HostExecution,
    pub(crate) lemma_ports: LemmaPorts,
}

/// Why a request was refused. The words are what the sandbox is told.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Refusal {
    HostExecutionOff,
    NotAPort,
    Privileged,
    LemmaPort,
}

impl Refusal {
    pub(crate) fn reason(self) -> &'static str {
        match self {
            Self::HostExecutionOff => "running commands on this Mac is turned off",
            Self::NotAPort => "not a port",
            Self::Privileged => "privileged ports are not relayed",
            Self::LemmaPort => "that port is one of Lemma's own",
        }
    }
}

/// The port a request names, if the relay may connect to it.
///
/// `line` is the request without its newline. Digits only: this is the one
/// place a sandbox's bytes are interpreted on the Mac, and it interprets as
/// little as it can.
pub(crate) fn admit(line: &[u8], lemma_ports: &BTreeSet<u16>) -> Result<u16, Refusal> {
    if line.is_empty() || line.len() > 5 || !line.iter().all(u8::is_ascii_digit) {
        return Err(Refusal::NotAPort);
    }
    let port: u16 = std::str::from_utf8(line)
        .ok()
        .and_then(|digits| digits.parse().ok())
        .ok_or(Refusal::NotAPort)?;
    if port < FIRST_UNPRIVILEGED_PORT {
        return Err(Refusal::Privileged);
    }
    if lemma_ports.contains(&port) {
        return Err(Refusal::LemmaPort);
    }
    Ok(port)
}

/// Read the request line a byte at a time.
///
/// Byte by byte so nothing past the newline is consumed: a client may send its
/// first bytes right behind the request, and those belong to the server.
async fn read_request<C: AsyncRead + Unpin>(client: &mut C) -> io::Result<Option<Vec<u8>>> {
    let mut line = Vec::with_capacity(MAX_REQUEST_BYTES);
    loop {
        let mut byte = [0_u8; 1];
        if client.read(&mut byte).await? == 0 {
            return Ok(None);
        }
        if byte[0] == b'\n' {
            return Ok(Some(line));
        }
        if line.len() >= MAX_REQUEST_BYTES {
            return Ok(None);
        }
        line.push(byte[0]);
    }
}

/// The Mac's own server on `port`: IPv4 loopback, then IPv6 loopback.
pub(crate) async fn connect_loopback(port: u16) -> io::Result<TcpStream> {
    let mut last = io::Error::from(io::ErrorKind::ConnectionRefused);
    for address in [
        SocketAddr::from((Ipv4Addr::LOCALHOST, port)),
        SocketAddr::from((Ipv6Addr::LOCALHOST, port)),
    ] {
        match tokio::time::timeout(CONNECT_TIMEOUT, TcpStream::connect(address)).await {
            Ok(Ok(stream)) => return Ok(stream),
            Ok(Err(error)) => last = error,
            Err(_) => last = io::Error::from(io::ErrorKind::TimedOut),
        }
    }
    Err(last)
}

/// What became of one connection, for the log line and the tests.
#[derive(Debug, PartialEq, Eq)]
pub(crate) enum Outcome {
    /// Spliced to this port until both sides finished.
    Relayed(u16),
    Refused(Refusal),
    /// Nothing on the Mac is listening on this port.
    Unreachable(u16),
    /// The connection closed or stalled before naming a port.
    NoRequest,
}

/// Serve one connection from the guest.
///
/// `connect` reaches a port on the Mac, and is passed in so the policy and the
/// splice can be tested without a real server on a fixed port. It is only
/// called for a port the policy accepted.
pub(crate) async fn serve_connection<C, U, F, Fut>(
    mut client: C,
    policy: &RelayPolicy,
    connect: F,
) -> io::Result<Outcome>
where
    C: AsyncRead + AsyncWrite + Unpin,
    U: AsyncRead + AsyncWrite + Unpin,
    F: FnOnce(u16) -> Fut,
    Fut: Future<Output = io::Result<U>>,
{
    let line = match tokio::time::timeout(REQUEST_TIMEOUT, read_request(&mut client)).await {
        Ok(Ok(Some(line))) => line,
        Ok(Ok(None)) | Err(_) => {
            let _ = client.write_all(b"error not a port\n").await;
            return Ok(Outcome::NoRequest);
        }
        Ok(Err(error)) => return Err(error),
    };
    let admitted = if (policy.host_execution)() {
        admit(&line, &(policy.lemma_ports)())
    } else {
        Err(Refusal::HostExecutionOff)
    };
    let port = match admitted {
        Ok(port) => port,
        Err(refusal) => {
            client
                .write_all(format!("error {}\n", refusal.reason()).as_bytes())
                .await?;
            let _ = client.shutdown().await;
            return Ok(Outcome::Refused(refusal));
        }
    };
    let mut upstream = match connect(port).await {
        Ok(upstream) => upstream,
        Err(_) => {
            client
                .write_all(b"error nothing on this Mac is listening on that port\n")
                .await?;
            let _ = client.shutdown().await;
            return Ok(Outcome::Unreachable(port));
        }
    };
    client.write_all(b"ok\n").await?;
    // Half-close is carried both ways: when one side finishes sending, the
    // other is told so and may still answer.
    tokio::io::copy_bidirectional(&mut client, &mut upstream).await?;
    Ok(Outcome::Relayed(port))
}

/// The relay's Unix listener and the thread serving it.
#[cfg(unix)]
///
/// Owned the way `TcpForwarder` owns its connections: one runtime per
/// listener, bounded admission, and a stop that cancels and joins every
/// connection before the runtime goes.
pub(crate) struct LoopbackRelay {
    shutdown: Option<oneshot::Sender<()>>,
    thread: Option<JoinHandle<()>>,
    path: PathBuf,
}

#[cfg(unix)]
impl LoopbackRelay {
    /// Listen at `path`, which `lemma-vz` connects to for every guest request.
    ///
    /// The socket is this user's alone (0600): the VM helper runs as this
    /// user, and nothing else on the Mac has any business asking the relay for
    /// anything.
    pub(crate) fn start(path: PathBuf, policy: RelayPolicy) -> io::Result<Self> {
        let listener = bind_private_socket(&path)?;
        listener.set_nonblocking(true)?;
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()?;
        let listener = {
            let _entered = runtime.enter();
            tokio::net::UnixListener::from_std(listener)?
        };
        let (shutdown, mut stopped) = oneshot::channel();
        let worker = thread::Builder::new()
            .name("lemma-loopback-relay".into())
            .spawn(move || {
                runtime.block_on(async move {
                    let mut connections = JoinSet::new();
                    loop {
                        tokio::select! {
                            biased;
                            _ = &mut stopped => break,
                            result = connections.join_next(), if !connections.is_empty() => {
                                if let Some(Err(error)) = result {
                                    eprintln!("loopback relay connection failed: {error}");
                                }
                            }
                            accepted = listener.accept(), if connections.len() < MAX_RELAYS => {
                                let stream = match accepted {
                                    Ok((stream, _)) => stream,
                                    Err(error) => {
                                        eprintln!("loopback relay accept failed: {error}");
                                        tokio::time::sleep(Duration::from_millis(100)).await;
                                        continue;
                                    }
                                };
                                let policy = policy.clone();
                                connections.spawn(async move {
                                    match serve_connection(stream, &policy, connect_loopback).await {
                                        Ok(Outcome::Refused(Refusal::LemmaPort)) => {
                                            // Worth a line: a sandbox asked for
                                            // Lemma itself, which a person
                                            // following a link does not do.
                                            eprintln!("loopback relay refused one of Lemma's own ports");
                                        }
                                        Ok(_) => {}
                                        Err(error) => {
                                            eprintln!("loopback relay connection ended: {error}");
                                        }
                                    }
                                });
                            }
                        }
                    }
                    connections.abort_all();
                    while connections.join_next().await.is_some() {}
                });
            })?;
        Ok(Self {
            shutdown: Some(shutdown),
            thread: Some(worker),
            path,
        })
    }
}

#[cfg(unix)]
impl Drop for LoopbackRelay {
    fn drop(&mut self) {
        if let Some(shutdown) = self.shutdown.take() {
            let _ = shutdown.send(());
        }
        if let Some(worker) = self.thread.take() {
            if worker.join().is_err() {
                eprintln!(
                    "loopback relay at {} stopped unexpectedly",
                    self.path.display()
                );
            }
        }
        let _ = std::fs::remove_file(&self.path);
    }
}

/// Bind a Unix socket only this user can connect to, replacing a stale one.
#[cfg(unix)]
fn bind_private_socket(path: &Path) -> io::Result<std::os::unix::net::UnixListener> {
    use std::os::unix::fs::PermissionsExt;
    match std::fs::remove_file(path) {
        Ok(()) => {}
        Err(error) if error.kind() == io::ErrorKind::NotFound => {}
        Err(error) => return Err(error),
    }
    let listener = std::os::unix::net::UnixListener::bind(path).map_err(|error| {
        io::Error::new(
            error.kind(),
            format!(
                "could not bind the loopback relay at {}: {error}",
                path.display()
            ),
        )
    })?;
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600))?;
    Ok(listener)
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use tokio::net::{TcpListener, UnixStream};

    /// Host execution on, refusing `ports`.
    fn lemma_ports(ports: &[u16]) -> RelayPolicy {
        let ports: BTreeSet<u16> = ports.iter().copied().collect();
        RelayPolicy {
            host_execution: Arc::new(|| true),
            lemma_ports: Arc::new(move || ports.clone()),
        }
    }

    fn no_lemma_ports() -> RelayPolicy {
        lemma_ports(&[])
    }

    #[test]
    fn a_request_is_digits_and_nothing_else() {
        let none = BTreeSet::new();
        assert_eq!(admit(b"3000", &none), Ok(3000));
        assert_eq!(admit(b"65535", &none), Ok(65535));
        for refused in [
            &b""[..],
            b"+3000",
            b" 3000",
            b"3000 ",
            b"3000\r",
            b"65536",
            b"123456",
            b"localhost:3000",
            b"127.0.0.1",
            b"-1",
            b"0x10",
        ] {
            assert_eq!(admit(refused, &none), Err(Refusal::NotAPort), "{refused:?}");
        }
    }

    #[test]
    fn privileged_ports_are_refused() {
        let none = BTreeSet::new();
        for port in [&b"0"[..], b"22", b"80", b"443", b"1023"] {
            assert_eq!(admit(port, &none), Err(Refusal::Privileged), "{port:?}");
        }
        assert_eq!(admit(b"1024", &none), Ok(1024));
    }

    #[test]
    fn lemma_ports_are_refused_and_nothing_else_is() {
        let ports: BTreeSet<u16> = [49152, 49153, 5432, 6379, 3567].into_iter().collect();
        for port in &ports {
            assert_eq!(
                admit(port.to_string().as_bytes(), &ports),
                Err(Refusal::LemmaPort),
                "{port}"
            );
        }
        assert_eq!(admit(b"3000", &ports), Ok(3000));
        assert_eq!(admit(b"49154", &ports), Ok(49154));
    }

    /// The deny list is asked for on every connection, so a port that became
    /// Lemma's after the relay started is refused too.
    #[tokio::test]
    async fn the_lemma_port_list_is_read_per_connection() {
        let current = Arc::new(std::sync::Mutex::new(BTreeSet::new()));
        let seen = Arc::clone(&current);
        let ports = RelayPolicy {
            host_execution: Arc::new(|| true),
            lemma_ports: Arc::new(move || seen.lock().unwrap().clone()),
        };
        let upstream = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = upstream.local_addr().unwrap().port();

        current.lock().unwrap().insert(port);
        let (mut guest, relay) = UnixStream::pair().unwrap();
        let served = tokio::spawn(async move {
            serve_connection(relay, &ports, |_| async {
                Err::<TcpStream, _>(io::Error::other("must not connect"))
            })
            .await
        });
        guest
            .write_all(format!("{port}\n").as_bytes())
            .await
            .unwrap();
        let mut answer = String::new();
        guest.read_to_string(&mut answer).await.unwrap();
        assert_eq!(answer, "error that port is one of Lemma's own\n");
        assert_eq!(
            served.await.unwrap().unwrap(),
            Outcome::Refused(Refusal::LemmaPort)
        );
    }

    /// With "Run commands on this Mac" off the relay admits nothing, and the
    /// switch is read per connection: turning it on or off takes effect on
    /// the next request.
    #[tokio::test]
    async fn nothing_is_relayed_while_host_execution_is_off() {
        let enabled = Arc::new(std::sync::atomic::AtomicBool::new(false));
        let switch = Arc::clone(&enabled);
        let policy = RelayPolicy {
            host_execution: Arc::new(move || switch.load(std::sync::atomic::Ordering::SeqCst)),
            lemma_ports: Arc::new(BTreeSet::new),
        };
        let upstream = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = upstream.local_addr().unwrap().port();

        let ask = |policy: RelayPolicy| async move {
            let (mut guest, relay) = UnixStream::pair().unwrap();
            let served = tokio::spawn(async move {
                serve_connection(relay, &policy, |_| async {
                    Err::<TcpStream, _>(io::Error::other("unreachable in this test"))
                })
                .await
            });
            guest
                .write_all(format!("{port}\n").as_bytes())
                .await
                .unwrap();
            let mut answer = String::new();
            guest.read_to_string(&mut answer).await.unwrap();
            (answer, served.await.unwrap().unwrap())
        };

        let (answer, outcome) = ask(policy.clone()).await;
        assert_eq!(answer, "error running commands on this Mac is turned off\n");
        assert_eq!(outcome, Outcome::Refused(Refusal::HostExecutionOff));

        // On: the request gets as far as connecting (which this test's
        // `connect` fails, so it is reported unreachable -- not refused).
        enabled.store(true, std::sync::atomic::Ordering::SeqCst);
        let (_, outcome) = ask(policy.clone()).await;
        assert_eq!(outcome, Outcome::Unreachable(port));

        enabled.store(false, std::sync::atomic::Ordering::SeqCst);
        let (_, outcome) = ask(policy).await;
        assert_eq!(outcome, Outcome::Refused(Refusal::HostExecutionOff));
    }

    #[tokio::test]
    async fn a_refused_request_never_connects() {
        for request in ["80\n", "localhost\n", "49153\n"] {
            let (mut guest, relay) = UnixStream::pair().unwrap();
            let ports = lemma_ports(&[49153]);
            let served = tokio::spawn(async move {
                // Had it connected, this would be reported as unreachable.
                serve_connection(relay, &ports, |_| async {
                    Err::<TcpStream, _>(io::Error::other("must not connect"))
                })
                .await
            });
            guest.write_all(request.as_bytes()).await.unwrap();
            let mut answer = String::new();
            guest.read_to_string(&mut answer).await.unwrap();
            assert!(answer.starts_with("error "), "{request:?}: {answer:?}");
            assert!(matches!(
                served.await.unwrap().unwrap(),
                Outcome::Refused(_)
            ));
        }
    }

    #[tokio::test]
    async fn a_request_with_no_newline_is_refused_not_waited_on_forever() {
        let (mut guest, relay) = UnixStream::pair().unwrap();
        let served = tokio::spawn(async move {
            serve_connection(relay, &no_lemma_ports(), connect_loopback).await
        });
        guest.write_all(b"30000000000000000000").await.unwrap();
        let mut answer = String::new();
        guest.read_to_string(&mut answer).await.unwrap();
        assert_eq!(answer, "error not a port\n");
        assert_eq!(served.await.unwrap().unwrap(), Outcome::NoRequest);
    }

    /// The whole path on the Mac's side: a real loopback server, reached by
    /// the port alone, bytes both ways, and the first bytes sent in the same
    /// write as the request line reaching the server.
    #[tokio::test]
    async fn an_admitted_port_is_reached_on_loopback_and_spliced() {
        let server = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = server.local_addr().unwrap().port();
        let echo = tokio::spawn(async move {
            let (mut stream, _) = server.accept().await.unwrap();
            let mut request = Vec::new();
            stream.read_to_end(&mut request).await.unwrap();
            stream.write_all(&request).await.unwrap();
            stream.shutdown().await.unwrap();
        });
        let (mut guest, relay) = UnixStream::pair().unwrap();
        let served = tokio::spawn(async move {
            serve_connection(relay, &no_lemma_ports(), connect_loopback).await
        });

        guest
            .write_all(format!("{port}\nGET / HTTP/1.1\r\n\r\n").as_bytes())
            .await
            .unwrap();
        // Half-close: the server reads to EOF before it answers.
        guest.shutdown().await.unwrap();
        let mut answer = String::new();
        guest.read_to_string(&mut answer).await.unwrap();

        assert_eq!(answer, "ok\nGET / HTTP/1.1\r\n\r\n");
        echo.await.unwrap();
        assert_eq!(served.await.unwrap().unwrap(), Outcome::Relayed(port));
    }

    /// A dev server started with `localhost` may be on `::1` only.
    #[tokio::test]
    async fn an_ipv6_only_server_is_reached_too() {
        let Ok(server) = TcpListener::bind("[::1]:0").await else {
            return; // No IPv6 loopback on this machine; nothing to prove.
        };
        let port = server.local_addr().unwrap().port();
        tokio::spawn(async move {
            let (mut stream, _) = server.accept().await.unwrap();
            stream.write_all(b"six").await.unwrap();
        });
        let (mut guest, relay) = UnixStream::pair().unwrap();
        tokio::spawn(
            async move { serve_connection(relay, &no_lemma_ports(), connect_loopback).await },
        );
        guest
            .write_all(format!("{port}\n").as_bytes())
            .await
            .unwrap();
        let mut answer = [0_u8; 6];
        guest.read_exact(&mut answer).await.unwrap();
        assert_eq!(&answer, b"ok\nsix");
    }

    #[tokio::test]
    async fn a_port_nothing_listens_on_is_an_error_line() {
        let port = {
            let probe = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
            probe.local_addr().unwrap().port()
        };
        let (mut guest, relay) = UnixStream::pair().unwrap();
        let served = tokio::spawn(async move {
            serve_connection(relay, &no_lemma_ports(), connect_loopback).await
        });
        guest
            .write_all(format!("{port}\n").as_bytes())
            .await
            .unwrap();
        let mut answer = String::new();
        guest.read_to_string(&mut answer).await.unwrap();
        assert_eq!(
            answer,
            "error nothing on this Mac is listening on that port\n"
        );
        assert_eq!(served.await.unwrap().unwrap(), Outcome::Unreachable(port));
    }

    /// A large response after the guest has finished sending arrives whole:
    /// the guest's half-close does not end the other direction.
    #[tokio::test]
    async fn a_half_closed_guest_still_receives_a_large_response() {
        let server = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = server.local_addr().unwrap().port();
        tokio::spawn(async move {
            let (mut stream, _) = server.accept().await.unwrap();
            let mut request = Vec::new();
            stream.read_to_end(&mut request).await.unwrap();
            stream.write_all(&vec![b'x'; 1024 * 1024]).await.unwrap();
        });
        let (mut guest, relay) = UnixStream::pair().unwrap();
        tokio::spawn(
            async move { serve_connection(relay, &no_lemma_ports(), connect_loopback).await },
        );
        guest
            .write_all(format!("{port}\nbody").as_bytes())
            .await
            .unwrap();
        guest.shutdown().await.unwrap();
        let mut answer = Vec::new();
        guest.read_to_end(&mut answer).await.unwrap();
        assert_eq!(&answer[..3], b"ok\n");
        assert_eq!(answer.len(), 3 + 1024 * 1024);
    }

    /// The listener end to end: a private socket, served until dropped, and
    /// removed when it is.
    #[test]
    fn the_relay_listens_privately_and_cleans_up_after_itself() {
        use std::io::{Read, Write};
        use std::os::unix::fs::PermissionsExt;
        let root = tempfile::Builder::new()
            .prefix("lemma-relay-")
            .tempdir_in("/tmp")
            .unwrap();
        let path = root.path().join("host-loopback.sock");
        // A stale socket from a previous daemon is replaced, not an error.
        drop(std::os::unix::net::UnixListener::bind(&path).unwrap());
        let relay = LoopbackRelay::start(path.clone(), lemma_ports(&[4000])).unwrap();
        let mode = std::fs::metadata(&path).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode, 0o600);

        let mut guest = std::os::unix::net::UnixStream::connect(&path).unwrap();
        guest
            .set_read_timeout(Some(Duration::from_secs(5)))
            .unwrap();
        guest.write_all(b"4000\n").unwrap();
        let mut answer = String::new();
        guest.read_to_string(&mut answer).unwrap();
        assert_eq!(answer, "error that port is one of Lemma's own\n");

        drop(relay);
        assert!(!path.exists(), "the socket outlived its relay");
    }
}
