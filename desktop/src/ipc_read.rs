//! Bounded framing for an untrusted or stale daemon's initial reply.
use std::io::{self, Read};
use std::time::{Duration, Instant};

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
                "the background service did not complete its handshake",
            ));
        }
        let mut byte = [0];
        match reader.read(&mut byte) {
            Ok(0) => {
                return Err(io::Error::new(
                    io::ErrorKind::UnexpectedEof,
                    "the background service closed during its handshake",
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
                        "daemon handshake exceeded its size limit",
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
