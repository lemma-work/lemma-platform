//! Bringing the stack up: concurrently, and reporting what died.

// Every guard in this file drives a real process group, so all of them
// are `#[cfg(unix)]` -- which leaves the module empty on Windows, and an
// import with nothing to import is an error under `-D warnings`.
#[cfg(unix)]
use super::*;

#[cfg(unix)]
#[test]
fn startup_reports_child_exit_and_recent_log_without_waiting_for_health_timeout() {
    let mut backend = service("backend", &[]);
    backend.command = vec![
        "/bin/sh".into(),
        "-c".into(),
        "echo exact-backend-failure; exit 17".into(),
    ];
    backend.health = Some(HttpHealthSpec {
        url: "http://127.0.0.1:9/health".into(),
        timeout_seconds: 30,
        expected_body: Some("runtime-123".into()),
        stabilization_seconds: 0,
    });
    let frontend = service("frontend", &["backend"]);
    let root = tempdir().unwrap();
    let mut value = manifest(vec![frontend, backend]);
    value.setup[0].command = vec!["/usr/bin/true".into()];
    let manager = manager_in(&root, value);

    let started = Instant::now();
    let error = manager.start_all().unwrap_err().to_string();

    assert!(started.elapsed() < Duration::from_secs(5));
    assert!(error.contains("process exited"));
    assert!(error.contains("exact-backend-failure"));
}

#[cfg(unix)]
#[test]
fn services_boot_alongside_each_other_rather_than_one_after_another() {
    // The frontend used to be spawned only after the backend passed its
    // full health gate, which put its entire boot on the critical path for
    // no reason: `next start` serves a prebuilt app and does not wait on
    // the backend. What that cost is not a number of milliseconds but an
    // ordering — the second service could not begin until the first had
    // finished — so that is what this asserts. A total-elapsed budget would
    // instead be a claim about the machine, and this suite runs its tests
    // against each other.
    let body = Arc::new(Mutex::new(String::new()));
    let (backend_health, backend_healthy_at, backend_server) =
        slow_response(700, Arc::clone(&body));
    let (frontend_health, frontend_healthy_at, frontend_server) =
        slow_response(700, Arc::clone(&body));
    let mut backend = service("backend", &[]);
    backend.command = long_running_command();
    backend.health = Some(backend_health);
    let mut frontend = service("frontend", &["backend"]);
    frontend.command = long_running_command();
    frontend.health = Some(frontend_health);

    let root = tempdir().unwrap();
    let mut value = manifest(vec![frontend, backend]);
    value.setup[0].command = vec!["/usr/bin/true".into()];
    let manager = manager_in(&root, value);
    // The production path mints the generation before starting, and every
    // health spec is rewritten to expect it.
    *body.lock().unwrap() = manager.prepare_runtime_generation().unwrap();

    // The manager announces each service as it reaches it, which is the
    // only account of when a boot began that does not depend on guessing.
    let mut boot_started = HashMap::new();
    manager
        .start_all_with_progress(|stage| {
            boot_started.insert(stage.to_owned(), Instant::now());
        })
        .unwrap();
    manager.stop_all().unwrap();
    drop(backend_server);
    drop(frontend_server);

    // The first health gate to pass is the earliest moment any service can
    // be said to have finished booting. Both had to be under way by then.
    let first_healthy = [&backend_healthy_at, &frontend_healthy_at]
        .into_iter()
        .filter_map(|healthy_at| *healthy_at.lock().unwrap())
        .min()
        .expect("a service answered its health gate");
    for id in REQUIRED_SERVICES {
        let started = boot_started[id];
        assert!(
            started < first_healthy,
            "{id} only began booting {:?} after another service was already healthy",
            started.saturating_duration_since(first_healthy),
        );
    }
}

/// A service that came up early must not restart the dwell clock late.
///
/// Each gate requires `stabilization_seconds` of *continuously observed*
/// health, measured from the first probe that gate itself saw succeed. Run
/// one after another, that charges the dwell once per service: the frontend
/// is healthy within a fraction of a second and then waits, idle and
/// unwatched, for the backend's gate to finish -- and only then does its own
/// gate start counting, spending the full window again on a service that
/// had been healthy the whole time.
///
/// Asserted on the *gap* between the two services' first probes, not on how
/// long startup took. An absolute bound is a claim about the machine: this
/// began life as "under 1800ms", passed locally at 1.28s, and failed a CI
/// runner at 4.31s while the gates were concurrent exactly as intended. The
/// gap is the thing the defect is actually about, and a slow machine slows
/// both services together, so it stays true wherever it runs.
#[cfg(unix)]
#[test]
fn one_slow_service_does_not_make_every_other_service_wait_its_dwell_again() {
    let body = Arc::new(Mutex::new(String::new()));
    let (mut backend_health, backend_probed, backend_server) = slow_response(0, Arc::clone(&body));
    let (mut frontend_health, frontend_probed, frontend_server) =
        slow_response(0, Arc::clone(&body));
    backend_health.stabilization_seconds = 1;
    frontend_health.stabilization_seconds = 1;

    let mut backend = service("backend", &[]);
    backend.command = long_running_command();
    backend.health = Some(backend_health);
    let mut frontend = service("frontend", &["backend"]);
    frontend.command = long_running_command();
    frontend.health = Some(frontend_health);

    let root = tempdir().unwrap();
    let mut value = manifest(vec![frontend, backend]);
    value.setup[0].command = vec!["/usr/bin/true".into()];
    let manager = manager_in(&root, value);
    *body.lock().unwrap() = manager.prepare_runtime_generation().unwrap();

    manager.start_all().unwrap();
    manager.stop_all().unwrap();
    drop(backend_server);
    drop(frontend_server);

    let backend_probed = backend_probed.lock().unwrap().expect("backend was probed");
    let frontend_probed = frontend_probed
        .lock()
        .unwrap()
        .expect("frontend was probed");

    // Both gates start watching together, so both services see their first
    // probe at about the same moment. Serially, the second is not probed
    // until the first has finished its whole dwell -- a second later, by
    // construction, whatever the machine.
    let gap = if backend_probed > frontend_probed {
        backend_probed - frontend_probed
    } else {
        frontend_probed - backend_probed
    };
    assert!(
        gap < Duration::from_millis(500),
        "the two services were first probed {gap:?} apart, which is about \
         the 1s dwell -- the gates are being charged one after another \
         rather than watched together",
    );
}
