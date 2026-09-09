//! The forwarders that reach the guest's private services.

use super::*;

#[test]
fn stopping_forwarder_closes_idle_connections_in_both_directions() {
    assert_forwarder_shutdown(false);
}

#[test]
fn stopping_forwarder_cancels_backpressured_connections() {
    assert_forwarder_shutdown(true);
}

fn assert_forwarder_shutdown(backpressured: bool) {
    let target = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
    target.set_nonblocking(true).unwrap();
    let forwarder = TcpForwarder::start(
        "idle-shutdown-test",
        SocketAddr::from((Ipv4Addr::LOCALHOST, 0)),
        target.local_addr().unwrap(),
    )
    .unwrap();
    let mut client = TcpStream::connect(forwarder.local_address).unwrap();
    let deadline = Instant::now() + Duration::from_secs(5);
    let mut upstream = loop {
        match target.accept() {
            Ok((stream, _)) => break stream,
            Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                assert!(Instant::now() < deadline, "relay did not connect upstream");
                thread::sleep(Duration::from_millis(10));
            }
            Err(error) => panic!("upstream accept failed: {error}"),
        }
    };
    upstream.set_nonblocking(false).unwrap();
    for stream in [&client, &upstream] {
        stream
            .set_read_timeout(Some(Duration::from_secs(2)))
            .unwrap();
    }

    if backpressured {
        client.set_nonblocking(true).unwrap();
        let bytes = [0_u8; 64 * 1024];
        let mut sent = 0;
        loop {
            match client.write(&bytes) {
                Ok(count) => {
                    assert!(count > 0);
                    sent += count;
                    assert!(
                        sent < 32 * 1024 * 1024,
                        "idle upstream must exert backpressure"
                    );
                }
                Err(error) if error.kind() == io::ErrorKind::WouldBlock => break,
                Err(error) => panic!("writing to relay failed: {error}"),
            }
        }
        client.set_nonblocking(false).unwrap();
    }

    drop(forwarder);

    let mut byte = [0_u8; 1];
    match client.read(&mut byte) {
        Ok(count) => assert_eq!(count, 0, "client must see EOF"),
        // Closing TCP with unread inbound data sends RST, not FIN. Both
        // prove cancellation; a timeout would leave the connection alive.
        Err(error) if backpressured && error.kind() == io::ErrorKind::ConnectionReset => {}
        Err(error) => panic!("client remained open or failed unexpectedly: {error}"),
    }
    // Drain already accepted bytes; EOF must arrive without either peer
    // having to close first, including when a copy was blocked on writing.
    io::copy(&mut upstream, &mut io::sink()).unwrap();
}

#[test]
fn tcp_forwarder_relays_bytes_and_releases_its_port() {
    let target = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
    let target_address = target.local_addr().unwrap();
    // The test keeps `target` so it can still watch for relayed connections
    // after the forwarder is gone; the upstream half of the exchange runs on
    // a clone.
    let upstream = target.try_clone().unwrap();
    let (reached_target, relay_is_waiting) = mpsc::channel();
    let server = thread::spawn(move || {
        let (mut stream, _) = upstream.accept().unwrap();
        // macOS inherits O_NONBLOCK from the listener onto accepted sockets.
        // A relay that mistook the resulting EAGAIN for EOF would already
        // have hung up on this connection before the client said anything,
        // so require a read that runs out of time rather than one that
        // reports EOF. The duration only bounds how long a healthy relay is
        // watched; a broken one reports EOF immediately.
        stream
            .set_read_timeout(Some(Duration::from_millis(100)))
            .unwrap();
        let mut discard = [0_u8; 1];
        let idle = stream.read(&mut discard).map_err(|error| error.kind());
        assert!(
            matches!(
                idle,
                Err(io::ErrorKind::WouldBlock) | Err(io::ErrorKind::TimedOut)
            ),
            "relay stopped waiting for a client that had not spoken yet: {idle:?}"
        );
        stream.set_read_timeout(None).unwrap();
        reached_target.send(()).unwrap();

        let mut request = [0_u8; 4];
        stream.read_exact(&mut request).unwrap();
        assert_eq!(&request, b"ping");
        stream.write_all(b"pong").unwrap();

        // Hang up only after the client has, so the relay is the passive
        // closer on both of its sockets and leaves no TCP state of its own
        // behind on the port the release check below is about.
        let mut trailing = Vec::new();
        stream.read_to_end(&mut trailing).unwrap();
        assert!(trailing.is_empty());
    });

    let forwarder = TcpForwarder::start(
        "test",
        SocketAddr::from((Ipv4Addr::LOCALHOST, 0)),
        target_address,
    )
    .unwrap();
    let local = forwarder.local_address;
    let mut client = TcpStream::connect(local).unwrap();
    // Bounded only so a relay that never dials out fails this test instead
    // of parking the whole test binary on a blocking accept forever.
    relay_is_waiting
        .recv_timeout(Duration::from_secs(10))
        .expect("relay never opened its upstream connection");
    client.write_all(b"ping").unwrap();
    let mut response = [0_u8; 4];
    client.read_exact(&mut response).unwrap();
    assert_eq!(&response, b"pong");

    // Half-close, then read to EOF: the relay passing the upstream hang-up
    // back down is the observable proof that it finished both directions,
    // which is what makes the release check below race-free.
    client.shutdown(std::net::Shutdown::Write).unwrap();
    let mut trailing = Vec::new();
    client.read_to_end(&mut trailing).unwrap();
    assert!(trailing.is_empty());
    server.join().unwrap();
    drop(client);

    drop(forwarder);

    // `local` is an ephemeral port, so its number is back in the machine's
    // pool the instant Drop returns, and this binary keeps taking ephemeral
    // ports - every `PortReservation::ephemeral` and every test that binds
    // port 0 - so one of them can and does take `local` in the microseconds
    // that follow. "Nothing can bind `local`" is therefore a claim about the
    // whole test binary rather than about this forwarder, and asserting it
    // is what made this test flake. Assert instead what is only ever true of
    // the forwarder: it stopped serving that port. A reservation that took
    // the number lands in the refused branch below, since reservations bind
    // without listening.
    target.set_nonblocking(true).unwrap();
    match TcpStream::connect_timeout(&local, Duration::from_secs(5)) {
        Err(error) => assert_eq!(
            error.kind(),
            io::ErrorKind::ConnectionRefused,
            "a released forwarder port must refuse connections, got {error}"
        ),
        Ok(_held_open) => {
            // Something else already owns the number. That is only a failure
            // if the something else is this forwarder, and the forwarder is
            // identifiable by behaviour: it dials `target_address`, which
            // nothing but this test knows. Hold the connection open while
            // watching, so a relay that is still running has something to
            // relay rather than a reset socket.
            let deadline = Instant::now() + Duration::from_secs(1);
            while Instant::now() < deadline {
                match target.accept() {
                    Ok(_) => panic!(
                        "forwarder kept relaying {local} to {target_address} after it was dropped"
                    ),
                    Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(10));
                    }
                    Err(error) => panic!("target listener stopped working: {error}"),
                }
            }
        }
    }
}
