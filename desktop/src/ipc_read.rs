//! Bounded framing for an untrusted or stale daemon's replies.
use std::io::{self, Read};
use std::time::{Duration, Instant};

pub fn response(
    reader: &mut impl Read,
    id: &str,
    budget: Duration,
) -> Result<serde_json::Value, String> {
    let deadline = Instant::now() + budget;
    loop {
        let line = handshake_line(
            reader,
            deadline.saturating_duration_since(Instant::now()),
            4 * 1024 * 1024,
        )
        .map_err(|error| format!("could not read Lemma's answer: {error}"))?;
        let event: serde_json::Value = serde_json::from_str(&line)
            .map_err(|_| "Lemma returned an invalid response".to_string())?;
        if event["id"].as_str() != Some(id) {
            continue;
        }
        match event["event"].as_str() {
            Some("ack") | None => continue,
            Some("error") => {
                return Err(event["message"]
                    .as_str()
                    .unwrap_or("Lemma could not complete that")
                    .into())
            }
            Some("done") if event["ok"] != true => {
                return Err(event["message"]
                    .as_str()
                    .unwrap_or("Lemma could not complete that")
                    .into())
            }
            Some(_) => return Ok(event),
        }
    }
}

pub fn handshake_line(
    reader: &mut impl Read,
    budget: Duration,
    limit: usize,
) -> io::Result<String> {
    let deadline = Instant::now() + budget;
    let mut bytes = Vec::new();
    loop {
        if Instant::now() >= deadline {
            return Err(io::Error::new(
                io::ErrorKind::TimedOut,
                "the background service did not answer before the deadline",
            ));
        }
        let mut byte = [0];
        match reader.read(&mut byte) {
            Ok(0) => {
                return Err(io::Error::new(
                    io::ErrorKind::UnexpectedEof,
                    "the background service closed before completing its reply",
                ))
            }
            Ok(_) => {
                if byte[0] == b'\n' {
                    return String::from_utf8(bytes)
                        .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error));
                }
                if bytes.len() == limit {
                    return Err(io::Error::new(
                        io::ErrorKind::InvalidData,
                        "daemon reply exceeded its size limit",
                    ));
                }
                bytes.push(byte[0]);
            }
            Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                std::thread::sleep(Duration::from_millis(5))
            }
            Err(error) if error.kind() == io::ErrorKind::Interrupted => {}
            Err(error) => return Err(error),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn an_acknowledged_operation_can_still_fail_and_unrelated_replies_do_not_complete_it() {
        let mut replies = &b"{\"event\":\"done\",\"id\":\"other\",\"ok\":true}\n{\"event\":\"ack\",\"id\":\"pair\"}\n{\"event\":\"error\",\"id\":\"pair\",\"message\":\"Pairing was rejected\"}\n"[..];
        assert_eq!(
            response(&mut replies, "pair", Duration::from_secs(1)).unwrap_err(),
            "Pairing was rejected"
        );
        let mut disconnected = &b"{\"event\":\"ack\",\"id\":\"start\"}\n"[..];
        assert!(response(&mut disconnected, "start", Duration::from_secs(1))
            .unwrap_err()
            .contains("closed"));
        let mut failed = &b"{\"event\":\"done\",\"id\":\"start\",\"ok\":false}\n"[..];
        assert!(response(&mut failed, "start", Duration::from_secs(1)).is_err());
    }

    #[cfg(unix)]
    #[test]
    fn the_caller_waits_for_completion_and_closes_a_timed_out_connection() {
        use std::io::Write;
        use std::os::unix::net::UnixStream;
        use std::sync::mpsc;
        let (mut server, mut client) = UnixStream::pair().unwrap();
        client.set_nonblocking(true).unwrap();
        let (sender, receiver) = mpsc::channel();
        let worker = std::thread::spawn(move || {
            sender
                .send(response(&mut client, "start", Duration::from_secs(2)))
                .unwrap();
        });
        server
            .write_all(b"{\"event\":\"ack\",\"id\":\"start\"}\n")
            .unwrap();
        assert!(receiver.recv_timeout(Duration::from_millis(30)).is_err());
        server
            .write_all(b"{\"event\":\"done\",\"id\":\"start\",\"ok\":true}\n")
            .unwrap();
        assert_eq!(
            receiver
                .recv_timeout(Duration::from_secs(2))
                .unwrap()
                .unwrap()["ok"],
            true
        );
        worker.join().unwrap();

        let (mut server, mut client) = UnixStream::pair().unwrap();
        client.set_nonblocking(true).unwrap();
        assert!(response(&mut client, "silent", Duration::from_millis(15))
            .unwrap_err()
            .contains("deadline"));
        drop(client);
        assert_eq!(server.read(&mut [0]).unwrap(), 0);
    }

    #[test]
    fn framing_does_not_consume_the_event_after_hello_and_rejects_unbounded_data() {
        let mut input = io::Cursor::new(b"hello\nevent\n");
        assert_eq!(
            handshake_line(&mut input, Duration::from_secs(1), 5).unwrap(),
            "hello"
        );
        assert_eq!(input.position(), 6);
        assert!(handshake_line(&mut io::repeat(b'x'), Duration::from_secs(1), 12).is_err());
        assert!(handshake_line(&mut &b"truncated"[..], Duration::from_secs(1), 32).is_err());
    }
    #[test]
    fn a_service_that_never_answers_has_a_deadline() {
        struct Silent;
        impl Read for Silent {
            fn read(&mut self, _: &mut [u8]) -> io::Result<usize> {
                Err(io::ErrorKind::WouldBlock.into())
            }
        }
        let started = Instant::now();
        assert_eq!(
            handshake_line(&mut Silent, Duration::from_millis(15), 1024)
                .unwrap_err()
                .kind(),
            io::ErrorKind::TimedOut
        );
        assert!(started.elapsed() < Duration::from_secs(1));
    }
}
