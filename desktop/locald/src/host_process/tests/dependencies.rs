//! Waiting for the database route, and explaining it when it never comes.

use super::*;

#[test]
fn dependency_failure_clears_readiness_and_surfaces_the_cause() {
    let root = tempdir().unwrap();
    let manager = manager_in(
        &root,
        manifest(vec![
            service("frontend", &["backend"]),
            service("backend", &[]),
        ]),
    );
    manager.desired_running.store(true, Ordering::Release);
    manager.health_ready.store(true, Ordering::Release);

    manager.mark_dependency_unavailable("private VM exited".into());
    let failed = manager.status_event(None);
    assert_eq!(failed["ready"], false);
    assert_eq!(failed["status"], "error");
    assert_eq!(failed["dependency_error"], "private VM exited");

    manager.mark_dependency_recovering();
    let recovering = manager.status_event(None);
    assert_eq!(recovering["status"], "starting");
    assert!(recovering["dependency_error"].is_null());

    manager.mark_dependency_ready();
    let recovered = manager.status_event(None);
    assert_eq!(recovered["dependency_ready"], true);
    assert!(recovered["dependency_error"].is_null());
}

#[test]
fn parses_only_literal_database_endpoints_for_the_private_route_gate() {
    assert_eq!(
        database_socket_address("postgresql+asyncpg://postgres:secret@192.168.64.10:5432/lemma"),
        Some("192.168.64.10:5432".parse().unwrap())
    );
    assert_eq!(
        database_socket_address("postgresql://postgres:secret@127.0.0.1/lemma"),
        Some("127.0.0.1:5432".parse().unwrap())
    );
    assert_eq!(
        database_socket_address("postgresql://private-guest/lemma"),
        None
    );
}

#[cfg(target_os = "macos")]
#[test]
fn unreachable_guest_explains_privacy_recovery_without_claiming_denial() {
    for code in [
        libc::EHOSTUNREACH,
        libc::ENETUNREACH,
        libc::EACCES,
        libc::EPERM,
    ] {
        let message = setup_dependency_error(
            "192.168.64.10:5432".parse().unwrap(),
            &io::Error::from_raw_os_error(code),
        );
        assert!(message.contains("System Settings > Privacy & Security > Local Network"));
        assert!(message.contains("Try again"));
        assert!(message.contains("If access is already allowed"));
        assert!(message.contains("factory reset is not needed"));
        assert!(message.contains("192.168.64.10:5432"));
    }
}

#[test]
fn database_refusal_and_loopback_errors_do_not_misdiagnose_privacy() {
    for (endpoint, error) in [
        (
            "192.168.64.10:5432",
            io::Error::from(io::ErrorKind::ConnectionRefused),
        ),
        (
            "127.0.0.1:5432",
            io::Error::from(io::ErrorKind::PermissionDenied),
        ),
    ] {
        let message = setup_dependency_error(endpoint.parse().unwrap(), &error);
        assert!(!message.contains("Local Network"));
        assert!(message.contains("Try again"));
        assert!(message.contains("Your local data is preserved"));
    }
}

#[cfg(unix)]
#[test]
fn migration_setup_waits_for_its_exact_database_route() {
    let root = tempdir().unwrap();
    let reservation = PortReservation::ephemeral().unwrap();
    let address = reservation.address();
    let mut value = manifest(vec![
        service("frontend", &["backend"]),
        service("backend", &[]),
    ]);
    value.setup[0].command = vec!["/usr/bin/true".into()];
    value.setup[0].timeout_seconds = 5;
    let manager = manager_in(&root, value);
    manager.set_backend_environment(HashMap::from([(
        "DATABASE_URL".into(),
        format!("postgresql://postgres:secret@{address}/lemma"),
    )]));

    // The route opens only after the setup has started waiting, which is the
    // behaviour under test. Everything here is bounded: a blocking `accept()`
    // joined unconditionally hangs the whole test binary forever whenever the
    // probe does not connect exactly once, which is how this burned 44
    // minutes of a CI runner before it was cancelled rather than failing.
    let route = thread::spawn(move || {
        thread::sleep(Duration::from_millis(150));
        // The port stays reserved for the whole wait, so no other test in
        // this binary can be handed it. Until this line the reservation is
        // bound but not listening, so `run_setups` is refused exactly as an
        // absent route would refuse it; here the same socket starts
        // listening, so the route opens without the port ever being free.
        let listener = reservation.listen().unwrap();
        listener.set_nonblocking(true).unwrap();
        let deadline = Instant::now() + Duration::from_secs(10);
        while Instant::now() < deadline {
            match listener.accept() {
                Ok(_) => return true,
                Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                    thread::sleep(Duration::from_millis(10));
                }
                Err(_) => return false,
            }
        }
        false
    });
    manager.run_setups().unwrap();
    assert!(
        route.join().unwrap(),
        "run_setups should have connected to the database route it was told to wait for"
    );
}
