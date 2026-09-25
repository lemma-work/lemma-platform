//! The loopback relay's guest end: its request policy, what it hands the
//! host, and the bytes both ways. Unix socket pairs stand in for vsock.

use super::*;
use crate::host_loopback::{
    bind_relay_socket, requested_host_port, serve_relay, serve_relay_client, HOST_LOOPBACK_SOCKET,
};
use std::os::unix::net::UnixStream;
use std::sync::atomic::{AtomicBool, Ordering};

/// A read that never ends fails the test instead of hanging it. Generous on
/// purpose: it guards against a hang, not a slow machine, and a shared CI
/// runner once took longer than five seconds to move 256 KiB through the relay.
fn bounded(stream: &UnixStream) {
    stream
        .set_read_timeout(Some(Duration::from_secs(30)))
        .unwrap();
}

/// A sandbox connection (the test's end) and the end guestd is handed.
fn sandbox_link() -> (UnixStream, UnixStream) {
    let (sandbox, guest) = UnixStream::pair().unwrap();
    bounded(&sandbox);
    (sandbox, guest)
}

/// A stand-in host: reads the request line, answers it, then echoes until the
/// guest half-closes, and closes after answering.
fn echoing_host(answer: &'static str) -> (UnixStream, thread::JoinHandle<String>) {
    let (guest_end, mut host) = UnixStream::pair().unwrap();
    bounded(&host);
    let served = thread::spawn(move || {
        let mut reader = BufReader::new(host.try_clone().unwrap());
        let mut request = String::new();
        reader.read_line(&mut request).unwrap();
        host.write_all(answer.as_bytes()).unwrap();
        if answer.starts_with("ok") {
            let mut body = Vec::new();
            reader.read_to_end(&mut body).unwrap();
            host.write_all(&body).unwrap();
        }
        host.shutdown(std::net::Shutdown::Write).unwrap();
        request
    });
    (guest_end, served)
}

#[test]
fn a_request_is_a_port_and_a_newline_and_nothing_else() {
    assert_eq!(requested_host_port("3000\n"), Ok(3000));
    assert_eq!(requested_host_port("65535\n"), Ok(65535));
    assert_eq!(requested_host_port("1024\n"), Ok(1024));
    for refused in [
        "3000",
        "+3000\n",
        " 3000\n",
        "3000 \n",
        "0x10\n",
        "\n",
        "65536\n",
        "999999\n",
        "3000\r\n",
        "-1\n",
        "localhost:3000\n",
    ] {
        assert_eq!(
            requested_host_port(refused),
            Err("not a port"),
            "{refused:?} was accepted"
        );
    }
}

#[test]
fn privileged_ports_are_refused_in_the_guest_as_well() {
    for port in ["0\n", "22\n", "80\n", "443\n", "1023\n"] {
        assert_eq!(
            requested_host_port(port),
            Err("privileged ports are not relayed"),
            "{port:?}"
        );
    }
}

#[test]
fn a_refused_request_never_reaches_the_host() {
    for request in ["80\n", "not-a-port\n", "3000"] {
        let (mut sandbox, guest) = sandbox_link();
        let dialled = Arc::new(AtomicBool::new(false));
        let seen = Arc::clone(&dialled);
        sandbox.write_all(request.as_bytes()).unwrap();
        sandbox.shutdown(std::net::Shutdown::Write).unwrap();
        serve_relay_client(guest, move || -> io::Result<UnixStream> {
            seen.store(true, Ordering::SeqCst);
            Err(io::Error::other("must not dial"))
        })
        .unwrap();
        let mut answer = String::new();
        sandbox.read_to_string(&mut answer).unwrap();
        assert!(answer.starts_with("error "), "{request:?}: {answer:?}");
        assert!(
            !dialled.load(Ordering::SeqCst),
            "{request:?} dialled the host"
        );
    }
}

#[test]
fn the_host_is_asked_for_the_port_and_its_answer_passes_through() {
    let (mut sandbox, guest) = sandbox_link();
    let (host, served) = echoing_host("ok\n");
    let relay = thread::spawn(move || serve_relay_client(guest, move || Ok(host)).unwrap());

    // The request and the first bytes in one write, as a proxy would send them.
    sandbox.write_all(b"3000\nGET / HTTP/1.1\r\n\r\n").unwrap();
    sandbox.shutdown(std::net::Shutdown::Write).unwrap();
    let mut answer = String::new();
    sandbox.read_to_string(&mut answer).unwrap();

    assert_eq!(served.join().unwrap(), "3000\n");
    assert_eq!(answer, "ok\nGET / HTTP/1.1\r\n\r\n");
    relay.join().unwrap();
}

/// The host's refusal -- one of Lemma's own ports, say -- reaches the sandbox
/// word for word, and nothing else follows it.
#[test]
fn a_host_refusal_reaches_the_sandbox_verbatim() {
    let (mut sandbox, guest) = sandbox_link();
    let (host, served) = echoing_host("error that port is one of Lemma's own\n");
    let relay = thread::spawn(move || serve_relay_client(guest, move || Ok(host)).unwrap());

    sandbox.write_all(b"49153\n").unwrap();
    let mut answer = String::new();
    sandbox.read_to_string(&mut answer).unwrap();

    assert_eq!(served.join().unwrap(), "49153\n");
    assert_eq!(answer, "error that port is one of Lemma's own\n");
    drop(sandbox);
    relay.join().unwrap();
}

#[test]
fn an_unreachable_host_is_an_error_line_not_a_hang() {
    let (mut sandbox, guest) = sandbox_link();
    sandbox.write_all(b"3000\n").unwrap();
    serve_relay_client(guest, || -> io::Result<UnixStream> {
        Err(io::Error::from(io::ErrorKind::ConnectionReset))
    })
    .unwrap();
    let mut answer = String::new();
    sandbox.read_to_string(&mut answer).unwrap();
    assert_eq!(answer, "error the host is not reachable\n");
}

/// A request whose body is finished still gets its response: the sandbox's
/// half-close reaches the host as EOF, and the host's bytes still come back.
#[test]
fn a_half_close_from_the_sandbox_still_gets_the_whole_response() {
    let (mut sandbox, guest) = sandbox_link();
    let (guest_end, mut host) = UnixStream::pair().unwrap();
    bounded(&host);
    let served = thread::spawn(move || {
        let mut reader = BufReader::new(host.try_clone().unwrap());
        let mut request = String::new();
        reader.read_line(&mut request).unwrap();
        host.write_all(b"ok\n").unwrap();
        // Waits for EOF before answering: only a relayed half-close ends this.
        let mut body = Vec::new();
        reader.read_to_end(&mut body).unwrap();
        let response = vec![b'x'; 256 * 1024];
        host.write_all(&response).unwrap();
        host.shutdown(std::net::Shutdown::Write).unwrap();
        body
    });
    let relay = thread::spawn(move || serve_relay_client(guest, move || Ok(guest_end)).unwrap());

    sandbox.write_all(b"3000\nupload").unwrap();
    sandbox.shutdown(std::net::Shutdown::Write).unwrap();
    let mut answer = Vec::new();
    sandbox.read_to_end(&mut answer).unwrap();

    assert_eq!(served.join().unwrap(), b"upload");
    assert_eq!(&answer[..3], b"ok\n");
    assert_eq!(answer.len(), 3 + 256 * 1024, "the response was cut short");
    relay.join().unwrap();
}

/// The socket is the policy: its directory is not writable by the sandbox
/// user, and the socket itself is connectable by it.
#[test]
fn the_relay_socket_is_usable_but_not_replaceable_by_a_sandbox() {
    let root = tempdir().unwrap();
    let directory = root.path().join("host-loopback");
    let listener = bind_relay_socket(&directory).unwrap();
    let mode = |path: &std::path::Path| fs::metadata(path).unwrap().permissions().mode() & 0o777;
    assert_eq!(mode(&directory), 0o755);
    assert_eq!(mode(&directory.join(HOST_LOOPBACK_SOCKET)), 0o666);
    drop(listener);
}

/// A guestd that restarts rebinds over the socket its predecessor left, in
/// the same directory -- which is what running containers have mounted.
#[test]
fn a_restarted_guest_rebinds_the_socket_in_place() {
    let root = tempdir().unwrap();
    let directory = root.path().join("host-loopback");
    drop(bind_relay_socket(&directory).unwrap());
    assert!(
        directory.join(HOST_LOOPBACK_SOCKET).exists(),
        "stale socket left"
    );
    let listener = bind_relay_socket(&directory).unwrap();
    let path = directory.join(HOST_LOOPBACK_SOCKET);
    thread::spawn(move || {
        serve_relay(listener, || -> io::Result<UnixStream> {
            Err(io::Error::from(io::ErrorKind::ConnectionRefused))
        })
    });
    let mut sandbox = UnixStream::connect(&path).unwrap();
    bounded(&sandbox);
    sandbox.write_all(b"3000\n").unwrap();
    let mut answer = String::new();
    sandbox.read_to_string(&mut answer).unwrap();
    assert_eq!(answer, "error the host is not reachable\n");
}

/// `sandbox.ensure` refuses the relay for a function sandbox before it touches
/// the engine: only a person's workspace has a browser in it.
#[test]
fn a_function_sandbox_cannot_be_granted_the_relay() {
    let root = tempdir().unwrap();
    let service = GuestService::new(
        FakeEngine::new(vec![]),
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();
    let error = service
        .ensure(json!({
            "sandbox_id": "fn-1",
            "workload_kind": "function",
            "image": "ghcr.io/lemma/function@sha256:abc",
            "apps": function_apps(),
            "host_loopback": true,
        }))
        .unwrap_err();
    assert!(error.message.contains("host loopback"), "{}", error.message);
    assert!(service.engine.commands.lock().unwrap().is_empty());
}
