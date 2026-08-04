//! Loopback port reservations that live until the moment of use.
//!
//! Taking an ephemeral port from the OS, keeping only the number, and binding
//! that number again later leaves a window in which anything else on the
//! machine can claim it. A reservation instead holds the port with a real bound
//! socket and is released explicitly at the handoff, so the window shrinks to
//! the single step that genuinely needs the port free.
//!
//! A reservation is bound but never listening. A connection that arrives while
//! one is held is refused exactly as it would be against an idle port, rather
//! than accepted by a socket that is about to vanish; callers that gate on
//! "can I connect yet?" therefore keep seeing the answer they would have seen
//! if the reservation did not exist.

use std::io;
use std::net::{Ipv4Addr, SocketAddr, TcpListener};

use socket2::{Domain, Protocol, Socket, Type};

#[derive(Debug)]
pub struct PortReservation {
    socket: Socket,
    address: SocketAddr,
}

impl PortReservation {
    /// Reserve an OS-chosen loopback port.
    pub fn ephemeral() -> io::Result<Self> {
        Self::bind(SocketAddr::from((Ipv4Addr::LOCALHOST, 0)))
    }

    /// Reserve one exact loopback port, failing if anything else holds it.
    pub fn at_loopback_port(port: u16) -> io::Result<Self> {
        Self::bind(SocketAddr::from((Ipv4Addr::LOCALHOST, port)))
    }

    fn bind(address: SocketAddr) -> io::Result<Self> {
        let socket = Socket::new(
            Domain::for_address(address),
            Type::STREAM,
            Some(Protocol::TCP),
        )?;
        // Deliberately no SO_REUSEADDR: exclusivity is the whole point, and a
        // reservation that a second binder can join reserves nothing. Nor
        // `listen`, so the port stays indistinguishable from an idle one.
        socket.bind(&address.into())?;
        let address = socket
            .local_addr()?
            .as_socket()
            .ok_or_else(|| io::Error::other("reserved socket has no IP address"))?;
        Ok(Self { socket, address })
    }

    pub fn port(&self) -> u16 {
        self.address.port()
    }

    pub fn address(&self) -> SocketAddr {
        self.address
    }

    /// Hand the port back at the exact point its real owner takes it. Dropping
    /// a reservation does the same thing; this names the handoff so the
    /// released-too-early bug is visible at the call site rather than implied
    /// by a scope ending.
    pub fn release(self) {
        drop(self.socket);
    }

    /// Become the listener that owns the port. There is no window at all here:
    /// this is the reserving socket itself starting to listen, so the port goes
    /// from reserved to served without passing through free. Use it whenever
    /// the eventual owner lives in this process.
    pub fn listen(self) -> io::Result<TcpListener> {
        self.socket.listen(128)?;
        Ok(self.socket.into())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};
    use std::net::TcpStream;
    use std::time::Duration;

    #[test]
    fn a_held_reservation_cannot_be_bound_by_anyone_else() {
        let reservation = PortReservation::ephemeral().unwrap();

        assert!(TcpListener::bind(reservation.address()).is_err());
        assert!(PortReservation::at_loopback_port(reservation.port()).is_err());

        // Nothing is asserted after the release on purpose. "The port is free
        // once I let go" is not this type's promise to keep — the instant after
        // a release belongs to whoever asks next, and asserting otherwise would
        // make this test the very race the module exists to remove.
    }

    #[test]
    fn a_held_reservation_refuses_connections_like_an_idle_port() {
        let reservation = PortReservation::ephemeral().unwrap();

        let refused =
            TcpStream::connect_timeout(&reservation.address(), Duration::from_millis(750));

        // Callers gate on connectability to decide a service is up. A listening
        // placeholder would answer for a service that does not exist yet.
        assert!(
            refused.is_err(),
            "a reservation must not accept connections on behalf of the port's real owner"
        );
    }

    #[test]
    fn a_reservation_can_become_the_listener_without_ever_freeing_the_port() {
        let reservation = PortReservation::ephemeral().unwrap();
        let address = reservation.address();

        let listener = reservation.listen().unwrap();
        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            stream.write_all(b"served").unwrap();
        });

        let mut client = TcpStream::connect_timeout(&address, Duration::from_secs(5)).unwrap();
        let mut answer = String::new();
        client.read_to_string(&mut answer).unwrap();

        assert_eq!(answer, "served");
        server.join().unwrap();
    }
}
