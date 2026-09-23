//! The host-to-sandbox tunnel's protocol, with TCP standing in for vsock.

use crate::sandbox_tunnel::{requested_port, serve_tunnel};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::thread;
use std::time::Duration;

/// A connected pair: the host's end, and the end `serve_tunnel` is given.
fn link() -> (TcpStream, TcpStream) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let host = TcpStream::connect(listener.local_addr().unwrap()).unwrap();
    let (guest, _) = listener.accept().unwrap();
    host.set_read_timeout(Some(Duration::from_secs(5))).unwrap();
    (host, guest)
}

/// A stand-in sandbox port that echoes what it reads, once.
fn echo_port() -> u16 {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        let mut buffer = [0_u8; 64];
        let read = stream.read(&mut buffer).unwrap();
        stream.write_all(&buffer[..read]).unwrap();
    });
    port
}

fn tunnel(guest: TcpStream) -> thread::JoinHandle<()> {
    thread::spawn(move || {
        serve_tunnel(guest, |port| TcpStream::connect(("127.0.0.1", port))).unwrap();
    })
}

#[test]
fn a_request_for_a_published_port_is_answered_and_spliced() {
    let port = echo_port();
    let (mut host, guest) = link();
    let served = tunnel(guest);

    host.write_all(format!("{port}\n").as_bytes()).unwrap();
    let mut reader = BufReader::new(host.try_clone().unwrap());
    let mut answer = String::new();
    reader.read_line(&mut answer).unwrap();
    assert_eq!(answer, "ok\n");

    host.write_all(b"GET /health").unwrap();
    let mut echoed = [0_u8; 11];
    reader.read_exact(&mut echoed).unwrap();
    assert_eq!(&echoed, b"GET /health");
    drop(host);
    drop(reader);
    served.join().unwrap();
}

/// A client may send its request in the same write as the port.
#[test]
fn bytes_sent_with_the_request_line_are_not_lost() {
    let port = echo_port();
    let (mut host, guest) = link();
    let served = tunnel(guest);

    host.write_all(format!("{port}\nhello").as_bytes()).unwrap();
    let mut reader = BufReader::new(host.try_clone().unwrap());
    let mut answer = String::new();
    reader.read_line(&mut answer).unwrap();
    assert_eq!(answer, "ok\n");
    let mut echoed = [0_u8; 5];
    reader.read_exact(&mut echoed).unwrap();
    assert_eq!(&echoed, b"hello");
    drop(host);
    drop(reader);
    served.join().unwrap();
}

#[test]
fn a_port_nothing_listens_on_is_an_error_not_a_hang() {
    let closed = {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.local_addr().unwrap().port()
    };
    let (mut host, guest) = link();
    let served = tunnel(guest);
    host.write_all(format!("{closed}\n").as_bytes()).unwrap();
    let mut answer = String::new();
    BufReader::new(host).read_line(&mut answer).unwrap();
    assert!(answer.starts_with("error "), "{answer}");
    served.join().unwrap();
}

#[test]
fn only_unprivileged_ports_outside_the_guests_own_services_are_reachable() {
    assert_eq!(requested_port("49153\n"), Ok(49153));
    assert_eq!(requested_port(" 49153 "), Ok(49153));
    for refused in [
        "22",
        "80",
        "5432",
        "6379",
        "3567",
        "",
        "abc",
        "70000",
        "49153; rm",
    ] {
        assert!(
            requested_port(refused).is_err(),
            "{refused:?} must be refused"
        );
    }
}

#[test]
fn a_refused_request_is_told_why() {
    let (mut host, guest) = link();
    let served = tunnel(guest);
    host.write_all(b"5432\n").unwrap();
    let mut answer = String::new();
    BufReader::new(host).read_line(&mut answer).unwrap();
    assert!(
        answer.starts_with("error ") && answer.contains("own services"),
        "{answer}"
    );
    served.join().unwrap();
}

/// A sandbox that answers and closes -- an HTTP response ending the
/// connection -- must reach the host as EOF while the host is still open.
#[test]
fn a_sandbox_that_closes_first_reaches_the_host_as_eof() {
    let port = echo_port();
    let (mut host, guest) = link();
    let served = tunnel(guest);

    host.write_all(format!("{port}\nbye").as_bytes()).unwrap();
    let mut received = Vec::new();
    host.read_to_end(&mut received)
        .expect("EOF within the read timeout, not a hang");
    assert_eq!(received, b"ok\nbye");
    drop(host);
    served.join().unwrap();
}
