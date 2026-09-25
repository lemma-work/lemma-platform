//! The Mac's end of the loopback relay: the paired user's VM browser reaching a
//! server on this computer's `127.0.0.1`.
//!
//! With host execution the paired user's agent runs `npm run dev` on the Mac, and the
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
//! - **Nothing, unless "Run commands on this Mac" is on for this Mac's Agent Host.**
//!   The relay exists so the paired user's agent can check a server it started on
//!   the Mac; with host execution off there is no such server to check, and
//!   the relay admits nothing. Read from the Agent Host's config on every
//!   connection (see [`HostExecution`]), so turning the switch off closes the
//!   relay for the next request without a restart.
//! - **Loopback only.** `127.0.0.1`, then `::1` -- a dev server started with
//!   `localhost` binds whichever one the resolver gave it first. Never a name
//!   and never another address: the request carries a port and nothing else.
//! - **Not a privileged port.** Below 1024 on a Mac is the system's, and
//!   nothing a person starts with `npm run dev` binds one.
//! - **Only a server the agent started.** Every process listening on the
//!   port must descend from the Agent Host process locald supervises -- the
//!   exec-server and the coding agents it runs, and whatever they start (see
//!   [`owner`]). Anything else on the Mac's loopback -- the person's own
//!   database, a local admin page, a password manager's helper -- is refused
//!   whether or not anybody thought to list it.
//! - **Not one of Lemma's own ports.** The backend, the frontend, the private
//!   service forwards, the sharing gateway and its tunnel's local API, the
//!   Agent Host's MCP relays -- asked for afresh on every connection (see
//!   [`LemmaPorts`]), because several are chosen at run time and one that
//!   starts after this relay must still be refused. The paired user's own sandbox is
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

pub(crate) mod owner;
use owner::NotOwned;
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
/// How long, and how much, a refused connection is read after its answer.
/// See `refuse`.
const DRAIN_TIMEOUT: Duration = Duration::from_secs(1);
const MAX_DRAIN_BYTES: u64 = 64 * 1024;
/// The lowest port the relay will connect to.
pub(crate) const FIRST_UNPRIVILEGED_PORT: u16 = 1024;

/// The ports Lemma itself is listening on right now.
///
/// A function rather than a set because the answer changes while the relay
/// runs: sharing starts a gateway on a port the OS picks, a tunnel's local API
/// with it, and the Agent Host opens an MCP relay per paired workspace.
pub(crate) type LemmaPorts = Arc<dyn Fn() -> BTreeSet<u16> + Send + Sync>;

/// Whether this Mac's Agent Host has host execution on, as of now.
pub(crate) type HostExecution = Arc<dyn Fn() -> bool + Send + Sync>;

/// Whether whatever listens on a port was started by the agent, as of now.
pub(crate) type ListenerOwner = Arc<dyn Fn(u16) -> Result<(), NotOwned> + Send + Sync>;

/// The Agent Host process locald is running right now, if any.
pub(crate) type AgentHostProcess = Arc<dyn Fn() -> Option<u32> + Send + Sync>;

/// The real ownership check: this Mac's processes, against the Agent Host
/// process `agent_host` names at the moment of asking.
pub(crate) fn agent_listener_owner(agent_host: AgentHostProcess) -> ListenerOwner {
    Arc::new(move |port| {
        owner::agent_owns(port, agent_host(), &owner::listeners_on, &owner::parent_of)
    })
}

/// What every connection is judged against, each part asked for afresh.
#[derive(Clone)]
pub(crate) struct RelayPolicy {
    pub(crate) host_execution: HostExecution,
    pub(crate) lemma_ports: LemmaPorts,
    pub(crate) listener_owner: ListenerOwner,
    /// The idle limit, when not `RELAY_IDLE`; only tests shorten it.
    pub(crate) idle: Option<Duration>,
}

/// Why a request was refused. The words are what the sandbox is told.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Refusal {
    HostExecutionOff,
    NotAPort,
    Privileged,
    LemmaPort,
    /// Something listens there that the agent did not start.
    NotTheAgents,
    /// No Agent Host is running, so nothing on the Mac is the agent's.
    NoAgentHost,
}

impl Refusal {
    pub(crate) fn reason(self) -> &'static str {
        match self {
            Self::HostExecutionOff => "running commands on this Mac is turned off",
            Self::NotAPort => "not a port",
            Self::Privileged => "privileged ports are not relayed",
            Self::LemmaPort => "that port is one of Lemma's own",
            Self::NotTheAgents => "that port's server was not started by Lemma's agent on this Mac",
            Self::NoAgentHost => "Lemma's agent is not running on this Mac",
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

/// Answer a connection that will not be relayed, and end it.
///
/// Half-closes first, so the client reads the answer and then EOF, and then
/// reads whatever the client had already sent -- an overlong request, or the
/// first bytes it sent behind the request line -- until it closes. Dropping a
/// Unix socket with unread input makes Linux report `ECONNRESET` to the peer
/// in place of EOF, so without this the answer was followed by a reset.
/// Bounded in time and bytes, so a client that never stops sending cannot hold
/// the connection open.
async fn refuse<C: AsyncRead + AsyncWrite + Unpin>(client: &mut C, answer: &str) -> io::Result<()> {
    client.write_all(answer.as_bytes()).await?;
    let _ = client.shutdown().await;
    let mut unread = client.take(MAX_DRAIN_BYTES);
    let _ = tokio::time::timeout(
        DRAIN_TIMEOUT,
        tokio::io::copy(&mut unread, &mut tokio::io::sink()),
    )
    .await;
    Ok(())
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
    /// Relayed to this port, and then ended by the relay.
    Closed(u16, Closed),
}

/// Why the relay ended a connection it had admitted.
#[derive(Debug, PartialEq, Eq)]
pub(crate) enum Closed {
    /// "Run commands on this Mac" was turned off, or the Mac unpaired.
    SwitchedOff,
    /// Nothing crossed it for the idle limit.
    Idle,
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
            let _ = refuse(&mut client, "error not a port\n").await;
            return Ok(Outcome::NoRequest);
        }
        Ok(Err(error)) => return Err(error),
    };
    let admitted = if (policy.host_execution)() {
        admit(&line, &(policy.lemma_ports)())
    } else {
        Err(Refusal::HostExecutionOff)
    };
    // Checked after the cheap refusals and just before connecting: it walks
    // every process's sockets, which is a few milliseconds of syscalls and so
    // is done off the relay's own thread.
    let admitted = match admitted {
        Ok(port) => {
            let owner = Arc::clone(&policy.listener_owner);
            match tokio::task::spawn_blocking(move || owner(port)).await {
                Ok(Ok(())) => Ok(port),
                Ok(Err(NotOwned::NoListener)) => {
                    refuse(
                        &mut client,
                        "error nothing on this Mac is listening on that port\n",
                    )
                    .await?;
                    return Ok(Outcome::Unreachable(port));
                }
                Ok(Err(NotOwned::NotTheAgents)) => Err(Refusal::NotTheAgents),
                Ok(Err(NotOwned::NoAgentHost)) | Err(_) => Err(Refusal::NoAgentHost),
            }
        }
        Err(refusal) => Err(refusal),
    };
    let port = match admitted {
        Ok(port) => port,
        Err(refusal) => {
            refuse(&mut client, &format!("error {}\n", refusal.reason())).await?;
            return Ok(Outcome::Refused(refusal));
        }
    };
    let mut upstream = match connect(port).await {
        Ok(upstream) => upstream,
        Err(_) => {
            refuse(
                &mut client,
                "error nothing on this Mac is listening on that port\n",
            )
            .await?;
            return Ok(Outcome::Unreachable(port));
        }
    };
    client.write_all(b"ok\n").await?;
    // Half-close is carried both ways: when one side finishes sending, the
    // other is told so and may still answer.
    //
    // Admission is not the only check. A relay admitted while the switch was
    // on used to go on carrying bytes for as long as both ends kept it open --
    // a dev server's hot-reload socket for days -- after "Run commands on this
    // Mac" was turned off or the Mac unpaired. So the switch is read again
    // while it runs, and a relay nobody has used for `RELAY_IDLE` is ended.
    let last_active = Arc::new(std::sync::Mutex::new(tokio::time::Instant::now()));
    let mut client = Active {
        inner: client,
        last_active: Arc::clone(&last_active),
    };
    let watch = async {
        let mut tick = tokio::time::interval(RELAY_RECHECK);
        tick.tick().await;
        loop {
            tick.tick().await;
            if !(policy.host_execution)() {
                return Outcome::Closed(port, Closed::SwitchedOff);
            }
            let idle = last_active
                .lock()
                .expect("relay activity lock poisoned")
                .elapsed();
            if idle >= policy_idle(policy) {
                return Outcome::Closed(port, Closed::Idle);
            }
        }
    };
    tokio::select! {
        copied = tokio::io::copy_bidirectional(&mut client, &mut upstream) => {
            copied?;
            Ok(Outcome::Relayed(port))
        }
        closed = watch => Ok(closed),
    }
}

/// How often a running relay re-reads the switch and its idle time.
const RELAY_RECHECK: Duration = Duration::from_secs(1);
/// How long a relay may carry nothing before it is ended. Long enough for a
/// dev server's quiet hot-reload socket between edits; short enough that a
/// forgotten one does not hold the Mac open for days.
const RELAY_IDLE: Duration = Duration::from_secs(30 * 60);

fn policy_idle(policy: &RelayPolicy) -> Duration {
    policy.idle.unwrap_or(RELAY_IDLE)
}

/// A stream that records when bytes last crossed it, either way.
struct Active<S> {
    inner: S,
    last_active: Arc<std::sync::Mutex<tokio::time::Instant>>,
}

impl<S> Active<S> {
    fn touch(&self) {
        *self
            .last_active
            .lock()
            .expect("relay activity lock poisoned") = tokio::time::Instant::now();
    }
}

impl<S: AsyncRead + Unpin> AsyncRead for Active<S> {
    fn poll_read(
        mut self: std::pin::Pin<&mut Self>,
        context: &mut std::task::Context<'_>,
        buffer: &mut tokio::io::ReadBuf<'_>,
    ) -> std::task::Poll<io::Result<()>> {
        let before = buffer.filled().len();
        let polled = std::pin::Pin::new(&mut self.inner).poll_read(context, buffer);
        if buffer.filled().len() > before {
            self.touch();
        }
        polled
    }
}

impl<S: AsyncWrite + Unpin> AsyncWrite for Active<S> {
    fn poll_write(
        mut self: std::pin::Pin<&mut Self>,
        context: &mut std::task::Context<'_>,
        bytes: &[u8],
    ) -> std::task::Poll<io::Result<usize>> {
        let polled = std::pin::Pin::new(&mut self.inner).poll_write(context, bytes);
        if matches!(polled, std::task::Poll::Ready(Ok(written)) if written > 0) {
            self.touch();
        }
        polled
    }

    fn poll_flush(
        mut self: std::pin::Pin<&mut Self>,
        context: &mut std::task::Context<'_>,
    ) -> std::task::Poll<io::Result<()>> {
        std::pin::Pin::new(&mut self.inner).poll_flush(context)
    }

    fn poll_shutdown(
        mut self: std::pin::Pin<&mut Self>,
        context: &mut std::task::Context<'_>,
    ) -> std::task::Poll<io::Result<()>> {
        std::pin::Pin::new(&mut self.inner).poll_shutdown(context)
    }
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
            listener_owner: anybodys(),
            idle: None,
        }
    }

    /// Every listener counts as the agent's: for tests about everything else.
    fn anybodys() -> ListenerOwner {
        Arc::new(|_| Ok(()))
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
            listener_owner: anybodys(),
            idle: None,
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
            listener_owner: anybodys(),
            idle: None,
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

    /// A server the agent did not start is refused, never connected to --
    /// the person's own database on its usual port included -- and the check
    /// is made for the port asked for, at the moment of asking.
    #[tokio::test]
    async fn a_server_the_agent_did_not_start_is_refused() {
        let asked = Arc::new(std::sync::Mutex::new(Vec::new()));
        let seen = Arc::clone(&asked);
        let policy = RelayPolicy {
            host_execution: Arc::new(|| true),
            lemma_ports: Arc::new(BTreeSet::new),
            listener_owner: Arc::new(move |port| {
                seen.lock().unwrap().push(port);
                match port {
                    3000 => Ok(()),
                    5432 => Err(NotOwned::NotTheAgents),
                    4000 => Err(NotOwned::NoAgentHost),
                    _ => Err(NotOwned::NoListener),
                }
            }),
            idle: None,
        };
        let ask = |request: &'static str| {
            let policy = policy.clone();
            async move {
                let (mut guest, relay) = UnixStream::pair().unwrap();
                let served = tokio::spawn(async move {
                    serve_connection(relay, &policy, |_| async {
                        Err::<TcpStream, _>(io::Error::other("stand-in for the Mac's server"))
                    })
                    .await
                });
                guest.write_all(request.as_bytes()).await.unwrap();
                let mut answer = String::new();
                guest.read_to_string(&mut answer).await.unwrap();
                (answer, served.await.unwrap().unwrap())
            }
        };

        let (answer, outcome) = ask("5432\n").await;
        assert_eq!(
            answer,
            "error that port's server was not started by Lemma's agent on this Mac\n"
        );
        assert_eq!(outcome, Outcome::Refused(Refusal::NotTheAgents));
        let (_, outcome) = ask("4000\n").await;
        assert_eq!(outcome, Outcome::Refused(Refusal::NoAgentHost));
        let (answer, outcome) = ask("4001\n").await;
        assert_eq!(
            answer,
            "error nothing on this Mac is listening on that port\n"
        );
        assert_eq!(outcome, Outcome::Unreachable(4001));
        // The agent's own server gets as far as connecting.
        let (_, outcome) = ask("3000\n").await;
        assert_eq!(outcome, Outcome::Unreachable(3000));

        // Privileged and malformed requests are refused before any process
        // is looked at.
        let (_, outcome) = ask("22\n").await;
        assert_eq!(outcome, Outcome::Refused(Refusal::Privileged));
        assert_eq!(*asked.lock().unwrap(), vec![5432, 4000, 4001, 3000]);
    }

    /// The real check, against this process standing in for the Agent Host:
    /// our own listener is ours, and with a different Agent Host it is not.
    #[cfg(target_os = "macos")]
    #[tokio::test]
    async fn the_real_owner_check_follows_the_process_tree() {
        let server = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = server.local_addr().unwrap().port();
        let ours = std::process::id();
        let check = agent_listener_owner(Arc::new(move || Some(ours)));
        let answer = tokio::task::spawn_blocking(move || check(port))
            .await
            .unwrap();
        assert_eq!(answer, Ok(()));

        // PID 1 is launchd, which is nobody's descendant but its own.
        let check = agent_listener_owner(Arc::new(|| Some(u32::MAX)));
        let answer = tokio::task::spawn_blocking(move || check(port))
            .await
            .unwrap();
        assert_eq!(answer, Err(NotOwned::NotTheAgents));

        let check = agent_listener_owner(Arc::new(|| None));
        let answer = tokio::task::spawn_blocking(move || check(port))
            .await
            .unwrap();
        assert_eq!(answer, Err(NotOwned::NoAgentHost));
        drop(server);
    }

    /// A relay admitted while the switch was on is ended when it goes off,
    /// not left carrying bytes until somebody closes it.
    #[tokio::test]
    async fn a_running_relay_ends_when_the_switch_goes_off() {
        let enabled = Arc::new(std::sync::atomic::AtomicBool::new(true));
        let switch = Arc::clone(&enabled);
        let policy = RelayPolicy {
            host_execution: Arc::new(move || switch.load(std::sync::atomic::Ordering::SeqCst)),
            lemma_ports: Arc::new(BTreeSet::new),
            listener_owner: anybodys(),
            idle: None,
        };
        let server = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = server.local_addr().unwrap().port();
        let held = tokio::spawn(async move {
            // Accepts and holds the connection open, sending nothing.
            let (stream, _) = server.accept().await.unwrap();
            tokio::time::sleep(Duration::from_secs(30)).await;
            drop(stream);
        });
        let (mut guest, relay) = UnixStream::pair().unwrap();
        let served =
            tokio::spawn(async move { serve_connection(relay, &policy, connect_loopback).await });
        guest
            .write_all(format!("{port}\n").as_bytes())
            .await
            .unwrap();
        let mut ok = [0_u8; 3];
        guest.read_exact(&mut ok).await.unwrap();
        assert_eq!(&ok, b"ok\n");

        enabled.store(false, std::sync::atomic::Ordering::SeqCst);
        let outcome = tokio::time::timeout(Duration::from_secs(5), served)
            .await
            .expect("the relay outlived the switch")
            .unwrap()
            .unwrap();
        assert_eq!(outcome, Outcome::Closed(port, Closed::SwitchedOff));
        held.abort();
    }

    #[tokio::test]
    async fn a_relay_nobody_uses_is_ended() {
        let mut policy = no_lemma_ports();
        policy.idle = Some(Duration::from_millis(1500));
        let server = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = server.local_addr().unwrap().port();
        let held = tokio::spawn(async move {
            let (stream, _) = server.accept().await.unwrap();
            tokio::time::sleep(Duration::from_secs(30)).await;
            drop(stream);
        });
        let (mut guest, relay) = UnixStream::pair().unwrap();
        let served =
            tokio::spawn(async move { serve_connection(relay, &policy, connect_loopback).await });
        guest
            .write_all(format!("{port}\n").as_bytes())
            .await
            .unwrap();
        let outcome = tokio::time::timeout(Duration::from_secs(10), served)
            .await
            .expect("an idle relay was never ended")
            .unwrap()
            .unwrap();
        assert_eq!(outcome, Outcome::Closed(port, Closed::Idle));
        held.abort();
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
