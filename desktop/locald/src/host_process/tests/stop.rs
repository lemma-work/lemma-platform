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

/// A service stops only after everything that depends on it has.
#[test]
fn services_stop_after_their_dependents_and_independent_ones_together() {
    let specs = |services: Vec<HostProcessSpec>| -> HashMap<String, HostProcessSpec> {
        services
            .into_iter()
            .map(|spec| (spec.id.clone(), spec))
            .collect()
    };
    let ids = |values: &[&str]| values.iter().map(|id| (*id).to_owned()).collect::<Vec<_>>();

    let dependent = specs(vec![
        service("backend", &[]),
        service("frontend", &["backend"]),
    ]);
    assert_eq!(
        stop_tiers(&ids(&["backend", "frontend"]), &dependent),
        [ids(&["frontend"]), ids(&["backend"])]
    );

    // The shipped manifest declares no dependencies, so its two services
    // stop at once, in the reverse of the order they started in.
    let independent = specs(vec![service("backend", &[]), service("frontend", &[])]);
    assert_eq!(
        stop_tiers(&ids(&["backend", "frontend"]), &independent),
        [ids(&["frontend", "backend"])]
    );
}

/// Two independent services that each take a while to go cost one wait, not two.
#[cfg(unix)]
#[test]
fn independent_services_are_stopped_at_the_same_time() {
    // Takes about a second to honour SIGTERM, as a real server draining does.
    let slow_to_stop = vec![
        "/bin/sh".into(),
        "-c".into(),
        "trap 'sleep 1; exit 0' TERM; while :; do sleep 0.1; done".into(),
    ];
    let mut backend = service("backend", &[]);
    backend.command = slow_to_stop.clone();
    let mut frontend = service("frontend", &[]);
    frontend.command = slow_to_stop;
    let root = tempdir().unwrap();
    let mut value = manifest(vec![backend, frontend]);
    value.setup[0].command = vec!["/usr/bin/true".into()];
    let manager = manager_in(&root, value);
    manager.start_all().unwrap();

    let started = Instant::now();
    let (result, timings) = manager.stop_all_timed();
    let elapsed = started.elapsed();

    result.unwrap();
    assert!(manager.status().iter().all(|process| !process.running));
    let each: Vec<Duration> = timings.iter().map(|(_, duration)| *duration).collect();
    assert_eq!(timings.len(), 2, "every service reports its own time");
    assert!(each
        .iter()
        .all(|duration| *duration >= Duration::from_millis(900)));
    // In turn, the stop would take the sum; together, the longer of the two.
    assert!(
        elapsed + Duration::from_millis(500) < each[0] + each[1],
        "the services were stopped one after another: {elapsed:?} for {timings:?}"
    );
}

/// Quit leaves nothing behind: each service's whole process group goes, the
/// grandchildren a server forks included, not only the process locald started.
#[cfg(unix)]
#[test]
fn a_parallel_stop_takes_every_services_whole_tree() {
    // A leader with a child of its own, as `node` or `uvicorn` workers are.
    let forks_a_child = vec!["/bin/sh".into(), "-c".into(), "/bin/sleep 30 & wait".into()];
    let mut backend = service("backend", &[]);
    backend.command = forks_a_child.clone();
    let mut frontend = service("frontend", &[]);
    frontend.command = forks_a_child;
    let root = tempdir().unwrap();
    let mut value = manifest(vec![backend, frontend]);
    value.setup[0].command = vec!["/usr/bin/true".into()];
    let manager = manager_in(&root, value);
    manager.start_all().unwrap();
    let groups: Vec<i32> = manager
        .status()
        .iter()
        .filter_map(|process| process.pid)
        .map(|pid| i32::try_from(pid).unwrap())
        .collect();
    assert_eq!(groups.len(), 2);

    manager.stop_all_timed().0.unwrap();

    for group in groups {
        assert_ne!(
            unsafe { libc::kill(-group, 0) },
            0,
            "process group {group} outlived the stop"
        );
    }
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
