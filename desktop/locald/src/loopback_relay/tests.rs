//! The relay's policy, splice and listener, against real sockets.

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
    let served =
        tokio::spawn(
            async move { serve_connection(relay, &no_lemma_ports(), connect_loopback).await },
        );
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
    let served =
        tokio::spawn(
            async move { serve_connection(relay, &no_lemma_ports(), connect_loopback).await },
        );

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
    tokio::spawn(async move { serve_connection(relay, &no_lemma_ports(), connect_loopback).await });
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
    let served =
        tokio::spawn(
            async move { serve_connection(relay, &no_lemma_ports(), connect_loopback).await },
        );
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
    tokio::spawn(async move { serve_connection(relay, &no_lemma_ports(), connect_loopback).await });
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
