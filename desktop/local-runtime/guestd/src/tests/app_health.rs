//! The readiness probe itself, against real sockets.
//!
//! `app_answers` over the guest's one HTTP prober, `readiness::probe_http`.
//!
//! The only place in this suite that opens one. Everything else decides the
//! answer, because fixtures elsewhere map ports like 49152-49154 — inside
//! Linux's ephemeral range — so a listener another test had just been assigned
//! could answer a probe meant for nothing, and readiness assertions passed on
//! macOS and failed on Linux for reasons unrelated to the code under test.
//!
//! These bind their own ports and probe only those, so there is nothing to
//! collide with.

use std::io::{Read, Write};
use std::net::TcpListener;

use crate::sandbox_inspect::app_answers;

/// Serves one HTTP response per connection, forever.
fn http_listener(response: &'static [u8]) -> u16 {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    std::thread::spawn(move || {
        for stream in listener.incoming() {
            let Ok(mut stream) = stream else { return };
            let mut discard = [0_u8; 256];
            let _ = stream.read(&mut discard);
            let _ = stream.write_all(response);
        }
    });
    port
}

#[test]
fn an_app_that_answers_is_answering() {
    let port = http_listener(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n");
    assert!(app_answers("127.0.0.1", port, "/health"));
}

#[test]
fn a_refusal_still_counts_as_serving() {
    // The runtime wants a credential this process does not hold. The question
    // is whether something is serving, which a 401 answers as well as a 200.
    let port = http_listener(b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\n\r\n");
    assert!(app_answers("127.0.0.1", port, "/health"));
}

#[test]
fn nothing_listening_is_not_answering() {
    // Bound and dropped, so the port is almost certainly free and definitely
    // not served by us.
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    drop(listener);

    assert!(!app_answers("127.0.0.1", port, "/health"));
}

#[test]
fn a_server_error_is_not_answering() {
    // The case the two probers disagreed about: the duplicate that was merged
    // away accepted any status line, so a runtime failing every request with
    // a 500 was reported ready while the ensure loop's own check rejected it.
    let port = http_listener(b"HTTP/1.1 500 Internal Server Error\r\nContent-Length: 0\r\n\r\n");
    assert!(!app_answers("127.0.0.1", port, "/health"));
}

#[test]
fn something_that_is_not_http_is_not_answering() {
    let port = http_listener(b"gibberish, not a status line\r\n\r\n");
    assert!(!app_answers("127.0.0.1", port, "/health"));
}

#[test]
fn a_health_path_without_a_leading_slash_is_still_requested() {
    let port = http_listener(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n");
    assert!(app_answers("127.0.0.1", port, "health"));
}
