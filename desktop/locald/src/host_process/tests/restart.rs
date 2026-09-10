//! Restarting the backend, and the circuit that stops trying.

use super::*;

#[cfg(unix)]
#[test]
fn backend_config_restart_keeps_the_frontend_process_running() {
    let body = Arc::new(Mutex::new(String::new()));
    let (health, _, _server) = slow_response(0, Arc::clone(&body));
    let mut backend = service("backend", &[]);
    backend.command = long_running_command();
    backend.health = Some(health);
    let mut frontend = service("frontend", &["backend"]);
    frontend.command = long_running_command();
    let root = tempdir().unwrap();
    let mut value = manifest(vec![frontend, backend]);
    value.setup[0].command = vec!["/usr/bin/true".into()];
    let manager = manager_in(&root, value);
    *body.lock().unwrap() = manager.prepare_runtime_generation().unwrap();
    manager.start_all().unwrap();
    assert!(manager.backend_restart_available());
    manager.mark_dependency_unavailable("private runtime is cold".into());
    assert!(!manager.backend_restart_available());
    manager.mark_dependency_ready();
    assert!(manager.backend_restart_available());
    // Named rather than unwrapped: when this fails on a loaded machine the
    // useful question is *which* process lost its pid, and a bare unwrap
    // answers neither that nor what the rest of the stack was doing.
    fn pids(manager: &HostProcessManager, when: &str) -> HashMap<String, u32> {
        let status = manager.status();
        status
            .iter()
            .map(|process| {
                let pid = process.pid.unwrap_or_else(|| {
                    panic!("{} has no pid {when}; full status: {status:#?}", process.id)
                });
                (process.id.clone(), pid)
            })
            .collect()
    }

    let before = pids(&manager, "before the restart");

    manager.restart_backend().unwrap();

    let after = pids(&manager, "after the restart");
    assert_ne!(before["backend"], after["backend"]);
    assert_eq!(before["frontend"], after["frontend"]);
    assert_eq!(manager.status_event(None)["ready"], true);
    manager.stop_all().unwrap();
}

#[cfg(unix)]
#[test]
fn backend_config_restart_does_not_reverse_a_stop_request() {
    let root = tempdir().unwrap();
    let mut backend = service("backend", &[]);
    backend.command = long_running_command();
    let mut frontend = service("frontend", &["backend"]);
    frontend.command = long_running_command();
    let mut value = manifest(vec![backend, frontend]);
    value.setup[0].command = vec!["/usr/bin/true".into()];
    let manager = manager_in(&root, value);
    manager.start_all().unwrap();
    let before = process_status(&manager, "backend").pid;
    manager.request_stop();
    let result = manager.restart_backend();
    let after = process_status(&manager, "backend").pid;
    manager.stop_all().unwrap();
    assert_eq!(result.unwrap_err().kind(), io::ErrorKind::Interrupted);
    assert_eq!(before, after);
    assert!(!manager.desired_running.load(Ordering::Acquire));
}

#[cfg(unix)]
#[test]
fn backend_config_restart_can_recover_after_a_failed_health_gate() {
    let root = tempdir().unwrap();
    let body = Arc::new(Mutex::new(String::new()));
    let (mut health, _, _server) = slow_response(0, Arc::clone(&body));
    health.timeout_seconds = 1;
    let mut backend = service("backend", &[]);
    backend.command = long_running_command();
    backend.health = Some(health);
    let mut frontend = service("frontend", &["backend"]);
    frontend.command = long_running_command();
    let mut value = manifest(vec![backend, frontend]);
    value.setup[0].command = vec!["/usr/bin/true".into()];
    let manager = manager_in(&root, value);
    let generation = manager.prepare_runtime_generation().unwrap();
    *body.lock().unwrap() = generation.clone();
    manager.start_all().unwrap();
    let frontend_pid = process_status(&manager, "frontend").pid;
    *body.lock().unwrap() = "wrong-runtime".into();
    assert!(manager.restart_backend().is_err());
    assert_eq!(manager.status_event(None)["ready"], false);
    assert!(!process_status(&manager, "backend").running);
    *body.lock().unwrap() = generation;
    manager.restart_backend().unwrap();
    assert_eq!(manager.status_event(None)["ready"], true);
    assert_eq!(process_status(&manager, "frontend").pid, frontend_pid);
    manager.stop_all().unwrap();
}

#[cfg(unix)]
#[test]
fn backend_config_restart_health_wait_can_be_stopped() {
    let root = tempdir().unwrap();
    let body = Arc::new(Mutex::new(String::new()));
    let (health, probed, _server) = slow_response(0, Arc::clone(&body));
    let mut backend = service("backend", &[]);
    backend.command = long_running_command();
    backend.health = Some(health);
    let mut frontend = service("frontend", &["backend"]);
    frontend.command = long_running_command();
    let mut value = manifest(vec![backend, frontend]);
    value.setup[0].command = vec!["/usr/bin/true".into()];
    let manager = manager_in(&root, value);
    *body.lock().unwrap() = manager.prepare_runtime_generation().unwrap();
    manager.start_all().unwrap();
    *body.lock().unwrap() = "not-ready".into();
    *probed.lock().unwrap() = None;
    let restarting = Arc::clone(&manager);
    let worker = thread::spawn(move || restarting.restart_backend());
    let deadline = Instant::now() + Duration::from_secs(5);
    while probed.lock().unwrap().is_none() && Instant::now() < deadline {
        thread::sleep(Duration::from_millis(10));
    }
    let saw_probe = probed.lock().unwrap().is_some();
    manager.request_stop();
    let result = worker.join().unwrap();
    manager.stop_all().unwrap();
    assert!(
        saw_probe,
        "the restarted backend never reached its health gate"
    );
    assert_eq!(result.unwrap_err().kind(), io::ErrorKind::Interrupted);
    assert!(!manager.desired_running.load(Ordering::Acquire));
    assert!(manager.status().iter().all(|service| !service.running));
}

/// The supervisor restarts a crashed service on its own.
///
/// The one thing about it that production depends on, and until this test
/// nothing asserted it: every other test drives `reconcile_crashes` by
/// hand, so the thread that calls it once a second could have failed to
/// spawn at all and the suite would have stayed green.
///
/// Waits for the restart instead of timing it. That direction is safe --
/// a slow machine only makes the wait longer, never wrong -- which is the
/// distinction that made the circuit tests flaky: they counted the
/// supervisor's steps, and a loaded runner gave it more of them.
#[cfg(unix)]
#[test]
fn the_supervisor_restarts_a_crashed_service_with_nobody_driving_it() {
    let mut backend = service("backend", &[]);
    backend.command = long_running_command();
    backend.restart = RestartSpec {
        max_restarts: 5,
        window_seconds: 60,
        backoff_seconds: 0,
    };
    // A frontend too: the manifest is required to have one.
    let mut frontend = service("frontend", &["backend"]);
    frontend.command = long_running_command();
    let root = tempdir().unwrap();
    let mut value = manifest(vec![frontend, backend]);
    value.setup[0].command = vec!["/usr/bin/true".into()];
    // Supervised, unlike `manager_in` -- the thread is the subject here.
    let manager = HostProcessManager::new(value, log_dir_in(&root)).unwrap();

    manager.start_all().unwrap();
    let first = wait_for_running(&manager, "backend");

    crash(&manager, "backend");

    // Nothing is called on `manager` from here: if it comes back, the
    // supervisor is what brought it back. It ticks once a second, so this
    // deadline is ~15 ticks of slack.
    let deadline = Instant::now() + Duration::from_secs(15);
    loop {
        let status = process_status(&manager, "backend");
        if status.running && status.pid != Some(first) {
            break;
        }
        assert!(
            Instant::now() < deadline,
            "supervisor never restarted the crashed backend \
             (running={} pid={:?} was={:?} restart_count={})",
            status.running,
            status.pid,
            Some(first),
            status.restart_count,
        );
        thread::sleep(Duration::from_millis(50));
    }
}

#[cfg(unix)]
#[test]
fn opens_restart_circuit_after_crash_budget_is_exhausted() {
    let mut backend = service("backend", &[]);
    backend.command = long_running_command();
    backend.restart = RestartSpec {
        max_restarts: 1,
        window_seconds: 60,
        backoff_seconds: 0,
    };
    let mut frontend = service("frontend", &["backend"]);
    frontend.command = long_running_command();
    let root = tempdir().unwrap();
    let mut value = manifest(vec![frontend, backend]);
    value.setup[0].command = vec!["/usr/bin/true".into()];
    let manager = manager_in(&root, value);

    manager.start_all().unwrap();
    // Both, before anything is crashed. The frontend depends on the backend
    // and is asserted at the end to have survived the backend's circuit
    // opening — an assertion that reads as a real failure when in fact the
    // frontend had never finished starting.
    wait_for_running(&manager, "backend");
    wait_for_running(&manager, "frontend");

    // One restart is budgeted, so it takes two crashes to exhaust it. Each
    // crash is followed by the supervisor actually observing the exit,
    // because reconciling one it has not seen yet is a no-op, and a run
    // that does that silently ends up asserting against a supervisor still
    // a step behind.
    crash(&manager, "backend");
    wait_for_recorded_exit(&manager, "backend");
    reconcile_until(&manager, "backend", "used its one budgeted restart", |s| {
        s.running
    });

    crash(&manager, "backend");
    wait_for_recorded_exit(&manager, "backend");
    let backend = reconcile_until(
        &manager,
        "backend",
        "opened its circuit with the budget exhausted",
        |s| s.circuit_open,
    );
    assert!(
        !backend.running,
        "an open circuit must not leave it running"
    );
    assert_eq!(backend.restart_count, 1);
    assert!(backend.last_exit.is_some());
    assert!(process_status(&manager, "frontend").running);
    manager.stop_all().unwrap();
}

/// A tripped circuit reopens once the crash window has gone quiet.
///
/// It never used to. `reconcile_crashes` tested `circuit_open` before it
/// pruned `restart_history`, so the history was frozen the moment the
/// circuit tripped and no amount of elapsed time could clear it. A single
/// transient burst — a laptop waking before the VM's port forwarders are
/// back — condemned the service until the user restarted the whole app,
/// and nothing on screen said so.
///
/// The window has to outlast the two crashes that exhaust the budget --
/// otherwise the first restart ages out before the second crash and the
/// budget silently resets, which is a green test proving nothing. Four
/// seconds is comfortably longer than a spawn plus two observed exits, and
/// short enough that waiting it out does not dominate the suite.
// Unix-only for the same reason as its sibling above: `crash` signals a
// process group and `long_running_command` is a shell one-liner.
#[cfg(unix)]
#[test]
fn a_tripped_restart_circuit_reopens_after_a_quiet_window() {
    let mut backend = service("backend", &[]);
    backend.command = long_running_command();
    backend.restart = RestartSpec {
        max_restarts: 1,
        window_seconds: 4,
        backoff_seconds: 0,
    };
    let mut frontend = service("frontend", &[]);
    frontend.command = long_running_command();
    let root = tempdir().unwrap();
    let mut value = manifest(vec![frontend, backend]);
    value.setup[0].command = vec!["/usr/bin/true".into()];
    let manager = manager_in(&root, value);

    manager.start_all().unwrap();
    wait_for_running(&manager, "backend");

    crash(&manager, "backend");
    wait_for_recorded_exit(&manager, "backend");
    reconcile_until(&manager, "backend", "used its one budgeted restart", |s| {
        s.running
    });

    crash(&manager, "backend");
    wait_for_recorded_exit(&manager, "backend");
    reconcile_until(
        &manager,
        "backend",
        "opened its circuit with the budget exhausted",
        |s| s.circuit_open,
    );

    // Nothing deliberate happens here — only the window elapsing.
    thread::sleep(Duration::from_millis(4500));
    reconcile_until(&manager, "backend", "reopened its circuit", |s| {
        !s.circuit_open
    });

    let backend = process_status(&manager, "backend");
    assert!(
        !backend.circuit_open,
        "a quiet window reopens the circuit without an app restart"
    );
    // ...but the trip is remembered. This is the durable half: without it,
    // a service that trips once per window would report healthy in every
    // gap and oscillate the splash between "error" and "starting".
    //
    // Deliberately not asserted through `status_event` here. Once the
    // service actually comes back, `ready` wins over `failed` and the
    // status is legitimately "running" -- so an assertion on the status
    // string would be racing the very recovery this test is proving works.
    // The trip count is what the UI keys on while a component is still
    // down, and it is what this pins.
    assert_eq!(backend.circuit_trips, 1);

    manager.stop_all().unwrap();
}

/// A remembered trip keeps a component reading as failed after its circuit
/// closes.
///
/// Asserted on the predicate rather than through a live manager: the state
/// that matters -- circuit closed again, trip remembered, service still
/// down -- exists for a fraction of a second in a real supervisor, and a
/// test that raced it would be a flake pretending to be coverage.
#[test]
fn a_component_that_has_tripped_still_reads_as_failed_after_the_circuit_closes() {
    let component = |circuit_open: bool, circuit_trips: u32| HostProcessStatus {
        id: "backend".into(),
        running: false,
        pid: None,
        circuit_open,
        circuit_trips,
        restart_count: 0,
        last_exit: None,
    };

    assert!(
        !HostProcessManager::components_report_failure(&[component(false, 0)]),
        "a component that has never tripped is not a failure"
    );
    assert!(
        HostProcessManager::components_report_failure(&[component(true, 1)]),
        "an open circuit is a failure"
    );
    assert!(
        HostProcessManager::components_report_failure(&[component(false, 1)]),
        "a closed circuit with a remembered trip is still a failure, or a \
         service that flaps once a window would read healthy in every gap"
    );
}

/// The generation a start reports is the generation it runs.
///
/// `prepare_runtime_generation` and `start_all_inner` used to disagree on a
/// partially-running stack -- one saw "some children" and kept the old
/// value, the other saw "not all children" and minted a new one. Every
/// phase, state and ready event then carried the old generation while the
/// services ran the new one.
///
/// That value decides, on the next launch, whether the workspace recorded
/// last time is still the one serving. A mixture made that question
/// answerable by processes from two different runs.
#[cfg(unix)]
#[test]
fn a_partially_running_stack_reports_the_generation_it_actually_runs() {
    let mut backend = service("backend", &[]);
    backend.command = long_running_command();
    let mut frontend = service("frontend", &[]);
    frontend.command = long_running_command();
    let root = tempdir().unwrap();
    let mut value = manifest(vec![frontend, backend]);
    value.setup[0].command = vec!["/usr/bin/true".into()];
    let manager = manager_in(&root, value);

    manager.start_all().unwrap();
    wait_for_running(&manager, "backend");
    wait_for_running(&manager, "frontend");

    // Kill one service, leaving the stack partially up -- the state a user
    // is in when they press Start after a crash loop.
    crash(&manager, "frontend");
    wait_for_recorded_exit(&manager, "frontend");

    let announced = manager.prepare_runtime_generation().unwrap();
    manager.start_all().unwrap();
    wait_for_running(&manager, "backend");
    wait_for_running(&manager, "frontend");

    let running = manager.status_event(None)["runtime_generation"]
        .as_str()
        .unwrap()
        .to_owned();
    assert_eq!(
        announced, running,
        "the generation announced to the app must be the one the services were given"
    );
    manager.stop_all().unwrap();
}
