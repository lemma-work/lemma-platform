//! A stop that has to interrupt something already in flight.

use super::*;

/// Stopping reported a failure for a stop that had worked.
///
/// The last thing `stop_all` does is re-reserve the workspace ports for
/// the next start. That bind is refused whenever anything still holds the
/// port -- and a service that served even one connection leaves TIME_WAIT
/// entries behind it, which a reservation cannot bind past because it sets
/// no SO_REUSEADDR. The backend always serves locald's own health gate, so
/// this was every stop that followed a real session: seen on macOS from
/// the installed app as "could not reserve Lemma's local port 53782:
/// Address already in use", from a Stop that had stopped everything.
#[test]
fn a_stop_that_cannot_retake_the_ports_is_still_a_stop() {
    let root = tempdir().unwrap();
    // Stands in for the TIME_WAIT the real backend leaves on its port:
    // both refuse the reservation's bind, for the same reason.
    let squatter = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let taken = squatter.local_addr().unwrap().port();

    let mut value = manifest(vec![
        service("backend", &[]),
        service("frontend", &["backend"]),
    ]);
    value.managed_runtime = Some(managed_runtime_spec(taken, taken));
    // Nothing is started, so the setup command is never run: stopping is
    // the whole subject, and it must survive a port it cannot retake.
    let manager = manager_in(&root, value);

    manager
        .stop_all()
        .expect("a port nobody could retake must not fail the stop");
    assert!(manager.status().iter().all(|service| !service.running));
}

#[cfg(unix)]
#[test]
fn a_stop_request_interrupts_an_inflight_service_health_wait() {
    let root = tempdir().unwrap();
    let mut backend = service("backend", &[]);
    backend.command = long_running_command();
    let mut frontend = service("frontend", &["backend"]);
    frontend.command = long_running_command();
    let mut value = manifest(vec![backend, frontend]);
    value.setup[0].command = vec!["/usr/bin/true".into()];
    let manager = manager_in(&root, value);
    manager.start_all().unwrap();
    let (mut health, server) = one_response(503, "not ready");
    health.timeout_seconds = 30;
    let waiting = Arc::clone(&manager);
    let (finished, outcome) = std::sync::mpsc::channel();
    let worker = thread::spawn(move || {
        let _ = finished.send(waiting.wait_process_health("backend", &health));
    });
    server.join().unwrap();
    manager.request_stop();
    let result = outcome.recv_timeout(Duration::from_secs(5));
    manager.stop_all().unwrap();
    worker.join().unwrap();
    assert_eq!(
        result.unwrap().unwrap_err().kind(),
        io::ErrorKind::Interrupted
    );
    assert!(manager.status().iter().all(|service| !service.running));
}
