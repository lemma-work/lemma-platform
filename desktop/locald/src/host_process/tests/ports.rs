//! A port held idle, and what it must not answer.

use super::*;

/// A port nothing is serving must not accept a connection.
///
/// locald holds the workspace ports while the stack is down so nothing
/// else takes them. It held them with a *listening* socket, and a
/// listening socket with nobody accepting completes the handshake and then
/// says nothing at all. Everything that asks "is the backend up yet?" --
/// locald's own health gate first among them -- connected successfully and
/// then waited until it gave up.
///
/// Seen on Windows as `backend failed health gate: connection timed out`,
/// which is the same message a slow machine produces and points at
/// nothing. `netstat` showed lemma-locald LISTENING on both workspace
/// ports with no python or node process alive, and a connection to them
/// succeeding in 0 ms.
///
/// The test does both halves, so the difference is the assertion rather
/// than a claim about it. It asserts only that the held port does not
/// *accept*: whether the refusal arrives as a reset or as silence is the
/// platform's choice, and macOS drops the SYN where Windows resets.
#[test]
fn an_idle_workspace_port_does_not_accept_connections() {
    use std::net::{Ipv4Addr, SocketAddr, TcpListener, TcpStream};

    // A port the OS has just handed back, so nothing else is on it.
    let probe = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
    let port = probe.local_addr().unwrap().port();
    drop(probe);
    let address = SocketAddr::from((Ipv4Addr::LOCALHOST, port));

    // What it does now. First, because a completed connection leaves the
    // port in TIME_WAIT, and a reservation deliberately sets no
    // SO_REUSEADDR -- so demonstrating the old behaviour first would make
    // this half fail to bind for a reason that is not the point.
    let held = bind_idle_port(port).expect("the port is free to hold");
    assert!(
        TcpStream::connect_timeout(&address, Duration::from_secs(2)).is_err(),
        "a held port has to look like an idle one to anything asking \
         whether the backend is up"
    );
    // And it really is held: nothing else could take it meanwhile.
    assert!(TcpListener::bind(address).is_err());
    drop(held);

    // What holding it used to do.
    let listening = TcpListener::bind(address).unwrap();
    assert!(
        TcpStream::connect_timeout(&address, Duration::from_secs(2)).is_ok(),
        "a listening socket nobody accepts on still completes the handshake, \
         which is what turned 'the backend is not running' into a timeout"
    );
    drop(listening);
}
