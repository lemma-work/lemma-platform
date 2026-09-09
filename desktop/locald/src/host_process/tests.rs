//! The host processes' guards.

use super::*;
use crate::host_process::setups::*;
// Only the unix tests spawn a real supervised process to bind a port.
#[cfg(unix)]
use crate::port_reservation::PortReservation;

/// A port nothing is serving must not accept a connection.
///
/// locald holds the workspace ports while the stack is down so nothing
/// else takes them. It held them with a *listening* socket, and a
/// listening socket with nobody accepting completes the handshake and then
/// says nothing at all. Everything that asks "is the backend up yet?" --
/// locald's own health gate first among them -- connected successfully and
/// then waited until it gave up.
///
/// Seen on Windows as `backend failed health gate: connection timed out`,
/// which is the same message a slow machine produces and points at
/// nothing. `netstat` showed lemma-locald LISTENING on both workspace
/// ports with no python or node process alive, and a connection to them
/// succeeding in 0 ms.
///
/// The test does both halves, so the difference is the assertion rather
/// than a claim about it. It asserts only that the held port does not
/// *accept*: whether the refusal arrives as a reset or as silence is the
/// platform's choice, and macOS drops the SYN where Windows resets.
#[test]
fn an_idle_workspace_port_does_not_accept_connections() {
    use std::net::{Ipv4Addr, SocketAddr, TcpListener, TcpStream};

    // A port the OS has just handed back, so nothing else is on it.
    let probe = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
    let port = probe.local_addr().unwrap().port();
    drop(probe);
    let address = SocketAddr::from((Ipv4Addr::LOCALHOST, port));

    // What it does now. First, because a completed connection leaves the
    // port in TIME_WAIT, and a reservation deliberately sets no
    // SO_REUSEADDR -- so demonstrating the old behaviour first would make
    // this half fail to bind for a reason that is not the point.
    let held = bind_idle_port(port).expect("the port is free to hold");
    assert!(
        TcpStream::connect_timeout(&address, Duration::from_secs(2)).is_err(),
        "a held port has to look like an idle one to anything asking \
         whether the backend is up"
    );
    // And it really is held: nothing else could take it meanwhile.
    assert!(TcpListener::bind(address).is_err());
    drop(held);

    // What holding it used to do.
    let listening = TcpListener::bind(address).unwrap();
    assert!(
        TcpStream::connect_timeout(&address, Duration::from_secs(2)).is_ok(),
        "a listening socket nobody accepts on still completes the handshake, \
         which is what turned 'the backend is not running' into a timeout"
    );
    drop(listening);
}
use std::net::{Ipv4Addr, TcpListener};
use tempfile::{tempdir, TempDir};

/// The log directory to hand a manager under test: a directory *inside*
/// `root`, never `root` itself.
///
/// A manager takes its installation state root — `installation.id` and the
/// process ledger — from the log directory's *parent*, because in
/// production it is handed `<state root>/logs`. Passing `root.path()` here
/// therefore made the parent the system temporary directory, so every
/// manager in every test, and in every test binary running at the same
/// time, shared one `$TMPDIR/processes.json` under one installation id.
///
/// That is a live weapon: constructing a manager runs
/// `reclaim_verified_processes`, which SIGTERMs any pid in the ledger that
/// still matches its recorded executable and start time. With the ledger
/// shared, one test's `HostProcessManager::new` killed the `/bin/sleep`
/// services another test had spawned seconds earlier — which is exactly
/// how `opens_restart_circuit_after_crash_budget_is_exhausted` came to
/// fail on a loaded runner with its frontend dead 50ms after it started,
/// and why it never failed on an idle machine, where the tests do not
/// overlap. One directory deeper gives every manager its own installation,
/// which is what the reclaim was written to assume.
fn log_dir_in(root: &TempDir) -> PathBuf {
    root.path().join("logs")
}

/// A manager whose installation state stays inside `root`.
fn manager_in(root: &TempDir, value: HostPackManifest) -> Arc<HostProcessManager> {
    // No supervisor thread. These tests step the state machine themselves,
    // and a second driver of it once a second is what made the restart
    // circuit tests fail on CI and never here.
    HostProcessManager::without_supervisor(value, log_dir_in(root)).unwrap()
}

/// A running service's log is truncated under the writer that holds it.
///
/// This is the property the whole rotation scheme depends on and the reason
/// it copies aside instead of renaming: the child owns this descriptor for
/// its entire life, so a rename would leave it writing into the rotated
/// file and the live one frozen at zero forever. Asserted with a handle
/// still open, because that is the only state that ever actually occurs.
#[test]
fn a_live_service_log_is_rotated_under_the_process_writing_it() {
    use std::io::Write;

    let root = tempdir().unwrap();
    let path = root.path().join("backend.log");
    let mut writer = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
        .unwrap();
    writer.write_all(&vec![b'x'; 1024]).unwrap();

    // Under the ceiling: left exactly alone.
    rotate_log(&path, 4096).unwrap();
    assert_eq!(path.metadata().unwrap().len(), 1024);
    assert!(!path.with_extension("previous.log").exists());

    // Over it: copied aside and truncated back to zero.
    writer.write_all(&vec![b'x'; 4096]).unwrap();
    rotate_log(&path, 4096).unwrap();
    assert_eq!(path.metadata().unwrap().len(), 0);
    assert_eq!(
        path.with_extension("previous.log")
            .metadata()
            .unwrap()
            .len(),
        5120
    );

    // And the writer that never let go keeps appending to the same file,
    // which is now counting up from zero rather than from 5 KiB.
    writer.write_all(b"after").unwrap();
    writer.flush().unwrap();
    assert_eq!(path.metadata().unwrap().len(), 5);
}

fn managed_runtime_spec(backend: u16, frontend: u16) -> ManagedRuntimeSpec {
    ManagedRuntimeSpec {
        images: ManagedRuntimeImages {
            postgres: "postgres@sha256:test".into(),
            redis: "redis@sha256:test".into(),
            supertokens: "supertokens@sha256:test".into(),
            workspace: None,
            function: None,
        },
        credentials: ManagedRuntimeCredentials {
            postgres_password: "a".repeat(64),
            redis_password: "b".repeat(64),
        },
        ports: ManagedRuntimePorts {
            postgres: 55432,
            redis: 56379,
            supertokens: 53567,
            backend,
            frontend,
        },
    }
}

fn manifest(services: Vec<HostProcessSpec>) -> HostPackManifest {
    HostPackManifest {
        schema_version: 1,
        release: "test".into(),
        managed_runtime: None,
        setup: vec![setup("migrations")],
        services,
    }
}

fn setup(id: &str) -> HostSetupSpec {
    HostSetupSpec {
        id: id.into(),
        command: vec!["test-program".into()],
        cwd: None,
        env: HashMap::new(),
        timeout_seconds: 10,
        max_attempts: 3,
        retry_backoff_seconds: 0,
        optional: false,
        stamp: None,
    }
}

fn service(id: &str, dependencies: &[&str]) -> HostProcessSpec {
    HostProcessSpec {
        id: id.into(),
        command: vec!["test-program".into()],
        cwd: None,
        env: HashMap::new(),
        dependencies: dependencies.iter().map(|value| (*value).into()).collect(),
        health: None,
        restart: RestartSpec::default(),
    }
}

/// A service process that stays up until it is asked to stop.
///
/// Spawned by absolute path and not through a shell, because the manager
/// identifies a process by the executable `ps` reports for it. `sh -c
/// "sleep 30"` replaces the shell with `sleep`, whose `argv[0]` is the bare
/// word the shell resolved on `PATH` — and a bare word is not a path that
/// canonicalizes, so ownership could only be recorded in the window before
/// the child exec'd. That is a race against the child, and a loaded machine
/// loses it.
#[cfg(unix)]
fn long_running_command() -> Vec<String> {
    vec!["/bin/sleep".into(), "30".into()]
}

/// A health endpoint that answers only after `delay_ms` of being asked, and
/// reports when it first said yes.
///
/// The delay runs from the first probe rather than from construction, so it
/// models a service that takes a moment to come up rather than a deadline
/// the test itself has to beat.
///
/// The body is shared because the manager mints the runtime generation and
/// rewrites every health spec's expected body to it, so what counts as
/// healthy is not known until the generation exists.
#[cfg(unix)]
struct HealthServer {
    stop: std::sync::mpsc::Sender<()>,
    worker: Option<thread::JoinHandle<()>>,
}

#[cfg(unix)]
impl Drop for HealthServer {
    fn drop(&mut self) {
        let _ = self.stop.send(());
        if let Some(worker) = self.worker.take() {
            crate::join_within(worker, "the disposable health endpoint");
        }
    }
}

#[cfg(unix)]
fn slow_response(
    delay_ms: u64,
    body: Arc<Mutex<String>>,
) -> (HttpHealthSpec, Arc<Mutex<Option<Instant>>>, HealthServer) {
    let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
    listener.set_nonblocking(true).unwrap();
    let address = listener.local_addr().unwrap();
    let served = Arc::clone(&body);
    let healthy_at = Arc::new(Mutex::new(None));
    let observed = Arc::clone(&healthy_at);
    let (stop, stopped) = std::sync::mpsc::channel();
    let worker = thread::spawn(move || {
        let mut ready_at = None;
        loop {
            if !matches!(
                stopped.try_recv(),
                Err(std::sync::mpsc::TryRecvError::Empty)
            ) {
                break;
            }
            let mut stream = match listener.accept() {
                Ok((stream, _)) => stream,
                Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                    if !matches!(
                        stopped.recv_timeout(Duration::from_millis(10)),
                        Err(std::sync::mpsc::RecvTimeoutError::Timeout)
                    ) {
                        break;
                    }
                    continue;
                }
                Err(_) => break,
            };
            stream
                .set_write_timeout(Some(Duration::from_secs(1)))
                .unwrap();
            let ready = *ready_at.get_or_insert(Instant::now() + Duration::from_millis(delay_ms));
            if Instant::now() < ready {
                // Refuse rather than answer: the prober retries, which is
                // what a service that has not finished booting looks like.
                drop(stream);
                continue;
            }
            observed.lock().unwrap().get_or_insert_with(Instant::now);
            let payload = served.lock().unwrap().clone();
            let _ = stream.write_all(
                format!(
                    "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{payload}",
                    payload.len()
                )
                .as_bytes(),
            );
            let _ = stream.flush();
        }
    });
    (
        HttpHealthSpec {
            url: format!("http://{address}/health"),
            timeout_seconds: 30,
            expected_body: Some("placeholder".into()),
            stabilization_seconds: 0,
        },
        healthy_at,
        HealthServer {
            stop,
            worker: Some(worker),
        },
    )
}

fn one_response(status: u16, body: &str) -> (HttpHealthSpec, thread::JoinHandle<()>) {
    let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
    let address = listener.local_addr().unwrap();
    let body = body.to_owned();
    let server = thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        // Consume the whole request head before answering. A single read can
        // return before the client has finished writing, and a discarded read
        // error hides that entirely. Responding and then dropping the socket
        // while request bytes are still unread makes the kernel close with RST
        // instead of FIN, so the client's in-flight write fails with EPIPE
        // rather than reading the healthy response.
        stream
            .set_read_timeout(Some(Duration::from_secs(10)))
            .unwrap();
        let mut request = Vec::new();
        let mut byte = [0_u8; 1];
        while !request.ends_with(b"\r\n\r\n") {
            match stream.read(&mut byte) {
                Ok(0) => break,
                Ok(_) => request.extend_from_slice(&byte),
                Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
                Err(_) => break,
            }
        }
        write!(
            stream,
            "HTTP/1.1 {status} Test\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
            body.len()
        )
        .unwrap();
        stream.flush().unwrap();
        stream.shutdown(std::net::Shutdown::Write).unwrap();
        // Hold the socket open until the client has read the response and
        // closed its end, so the drop below is a graceful FIN rather than an
        // RST that could discard buffered response bytes mid-read.
        let mut drained = Vec::new();
        let _ = stream.read_to_end(&mut drained);
    });
    (
        HttpHealthSpec {
            url: format!("http://{address}/health"),
            timeout_seconds: 1,
            expected_body: Some("runtime-123".into()),
            stabilization_seconds: 0,
        },
        server,
    )
}

#[test]
fn health_requires_two_xx_and_the_expected_runtime_identity() {
    for status in [401, 404, 503] {
        let (unhealthy, server) = one_response(status, "runtime-123");
        assert!(probe_http(&unhealthy).is_err());
        crate::join_within(server, "the health endpoint");
    }

    let (stale, stale_server) = one_response(200, "runtime-old");
    let error = probe_http(&stale).unwrap_err();
    assert!(
        error.to_string().contains("different runtime instance"),
        "{error}"
    );
    crate::join_within(stale_server, "the stale health endpoint");

    let (healthy, healthy_server) = one_response(200, "runtime-123");
    probe_http(&healthy).unwrap();
    crate::join_within(healthy_server, "the healthy endpoint");
}

#[test]
fn validates_exact_two_process_contract_and_dependency_order() {
    let manifest = manifest(vec![
        service("frontend", &["backend"]),
        service("backend", &[]),
    ]);
    let order = validate_and_order(&manifest).unwrap();
    assert_eq!(order, vec!["backend", "frontend"]);
}

#[test]
fn compatibility_host_packs_expose_app_ports_for_the_sharing_gateway() {
    let mut frontend = service("frontend", &["backend"]);
    frontend.health = Some(HttpHealthSpec {
        url: "http://127.0.0.1:3711/runtime-config.js".into(),
        timeout_seconds: 1,
        expected_body: None,
        stabilization_seconds: 0,
    });
    let mut backend = service("backend", &[]);
    backend.health = Some(HttpHealthSpec {
        url: "http://localhost:8711/health/ready".into(),
        timeout_seconds: 1,
        expected_body: None,
        stabilization_seconds: 0,
    });
    let root = tempdir().unwrap();
    let manager = manager_in(&root, manifest(vec![frontend, backend]));

    assert_eq!(manager.application_ports(), Some((3711, 8711)));
    assert_eq!(loopback_http_port("https://127.0.0.1:3711/"), None);
    assert_eq!(loopback_http_port("http://0.0.0.0:3711/"), None);
}

#[test]
fn rejects_missing_processes_and_cycles() {
    let missing = manifest(vec![service("backend", &[])]);
    assert!(validate_and_order(&missing)
        .unwrap_err()
        .to_string()
        .contains("frontend"));

    let cycle = manifest(vec![
        service("backend", &["frontend"]),
        service("frontend", &["backend"]),
    ]);
    assert!(validate_and_order(&cycle)
        .unwrap_err()
        .to_string()
        .contains("cycle"));
}

#[test]
fn rejects_missing_migration_setup() {
    let mut value = manifest(vec![
        service("backend", &[]),
        service("frontend", &["backend"]),
    ]);
    value.setup.clear();

    assert!(validate_and_order(&value)
        .unwrap_err()
        .to_string()
        .contains("migrations setup"));
}

#[test]
fn operator_secrets_are_ephemeral_and_backend_scoped() {
    let root = tempdir().unwrap();
    let manager = manager_in(
        &root,
        manifest(vec![
            service("frontend", &["backend"]),
            service("backend", &[]),
        ]),
    );
    manager.set_backend_environment(HashMap::from([(
        "LEMMA_OPENAI_API_KEY".into(),
        "vault-secret".into(),
    )]));

    assert_eq!(
        manager.process_spec_for_spawn("backend").unwrap().env["LEMMA_OPENAI_API_KEY"],
        "vault-secret"
    );
    assert!(!manager
        .process_spec_for_spawn("frontend")
        .unwrap()
        .env
        .contains_key("LEMMA_OPENAI_API_KEY"));
    assert!(!manager.by_id["backend"]
        .env
        .contains_key("LEMMA_OPENAI_API_KEY"));
}

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

#[cfg(unix)]
#[test]
fn migration_setup_receives_the_same_dynamic_backend_environment() {
    let root = tempdir().unwrap();
    let mut value = manifest(vec![
        service("frontend", &["backend"]),
        service("backend", &[]),
    ]);
    value.setup[0].command = vec!["/usr/bin/env".into()];
    let manager = manager_in(&root, value);
    manager.set_backend_environment(HashMap::from([(
        "DATABASE_URL".into(),
        "postgresql://private-guest/lemma".into(),
    )]));

    manager.run_setups().unwrap();

    let log = std::fs::read_to_string(log_dir_in(&root).join("migrations.log")).unwrap();
    assert!(log.contains("DATABASE_URL=postgresql://private-guest/lemma"));
}

#[cfg(unix)]
#[test]
fn an_optional_setup_that_fails_does_not_stop_the_stack() {
    // Seeding the connector catalog reaches the network whenever a Composio
    // key is set. A workspace that will not start because a third-party
    // catalog was unreachable would be a bad trade for a feature this
    // session may not even use, so the failure is logged and the start
    // continues. Migrations stay required: a backend running against a
    // schema it does not expect is worse than one that refuses to start.
    let root = tempdir().unwrap();
    let mut value = manifest(vec![
        service("frontend", &["backend"]),
        service("backend", &[]),
    ]);
    value.setup[0].command = vec!["/bin/sh".into(), "-c".into(), "exit 9".into()];
    value.setup[0].max_attempts = 1;
    value.setup[0].optional = true;
    let manager = manager_in(&root, value);

    manager
        .run_setups()
        .expect("an optional setup must not fail the start");
}

#[cfg(unix)]
#[test]
fn a_required_setup_that_fails_still_stops_the_stack() {
    let root = tempdir().unwrap();
    let mut value = manifest(vec![
        service("frontend", &["backend"]),
        service("backend", &[]),
    ]);
    value.setup[0].command = vec!["/bin/sh".into(), "-c".into(), "exit 9".into()];
    value.setup[0].max_attempts = 1;
    value.setup[0].optional = false;
    let manager = manager_in(&root, value);

    assert!(manager.run_setups().is_err());
}

#[cfg(unix)]
#[test]
fn migration_setup_retries_a_transient_cold_guest_failure() {
    let root = tempdir().unwrap();
    let marker = root.path().join("route-ready");
    let mut value = manifest(vec![
        service("frontend", &["backend"]),
        service("backend", &[]),
    ]);
    value.setup[0].command = vec![
        "/bin/sh".into(),
        "-c".into(),
        "if [ -f \"$1\" ]; then exit 0; fi; touch \"$1\"; exit 65".into(),
        "lemma-migration-retry".into(),
        marker.to_string_lossy().into_owned(),
    ];
    value.setup[0].max_attempts = 2;
    let manager = manager_in(&root, value);

    manager.run_setups().unwrap();

    let log = std::fs::read_to_string(log_dir_in(&root).join("migrations.log")).unwrap();
    assert!(log.contains("setup attempt 1 exited"));
    assert!(marker.is_file());
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

#[cfg(unix)]
#[test]
fn shutdown_finishes_migration_but_does_not_launch_services() {
    let root = tempdir().unwrap();
    let completed = root.path().join("migration-completed");
    let mut value = manifest(vec![
        service("backend", &[]),
        service("frontend", &["backend"]),
    ]);
    value.setup[0].command = vec![
        "/bin/sh".into(),
        "-c".into(),
        "printf committed > \"$1\"".into(),
        "migration".into(),
        completed.to_string_lossy().into_owned(),
    ];
    let manager = manager_in(&root, value);
    let lifecycle = crate::lifecycle::Lifecycle::default();
    lifecycle.begin().unwrap();
    let error = manager
        .start_all_cancellable(
            |stage| {
                if stage == "migrations" {
                    lifecycle.request_shutdown();
                }
            },
            || lifecycle.checkpoint(),
        )
        .unwrap_err();
    lifecycle.finish();
    lifecycle.wait_idle();
    assert_eq!(error.kind(), io::ErrorKind::Interrupted);
    assert_eq!(fs::read_to_string(completed).unwrap(), "committed");
    assert!(manager.status().iter().all(|process| !process.running));
    assert!(!manager.desired_running());
    assert!(lifecycle.begin().is_err());
}

#[cfg(unix)]
#[test]
fn shutdown_before_start_does_not_run_migrations() {
    let root = tempdir().unwrap();
    let mut value = manifest(vec![
        service("backend", &[]),
        service("frontend", &["backend"]),
    ]);
    value.setup[0].command = vec!["/usr/bin/false".into()];
    let manager = manager_in(&root, value);
    let lifecycle = crate::lifecycle::Lifecycle::default();
    lifecycle.request_shutdown();
    let error = manager
        .start_all_cancellable(
            |_| panic!("cancelled startup cannot enter a stage"),
            || lifecycle.checkpoint(),
        )
        .unwrap_err();
    assert_eq!(error.kind(), io::ErrorKind::Interrupted);
    assert!(manager.status().iter().all(|process| !process.running));
}

#[cfg(unix)]
#[test]
fn starts_and_stops_backend_and_frontend_process_groups() {
    let command = vec![
        "/bin/sh".into(),
        "-c".into(),
        "trap 'exit 0' TERM; while :; do sleep 1; done".into(),
    ];
    let mut backend = service("backend", &[]);
    backend.command = command.clone();
    let mut frontend = service("frontend", &["backend"]);
    frontend.command = command;
    let root = tempdir().unwrap();
    let mut value = manifest(vec![frontend, backend]);
    value.setup[0].command = vec!["/usr/bin/true".into()];
    let manager = manager_in(&root, value);

    manager.start_all().unwrap();
    assert!(manager.status().iter().all(|process| process.running));
    assert_eq!(manager.status_event(None)["ready"], true);
    manager.stop_all().unwrap();
    assert!(manager.status().iter().all(|process| !process.running));
    assert_eq!(manager.status_event(None)["ready"], false);
}

/// `/proc/<pid>/stat` field 22, past a command name that fights back.
///
/// This is the parse the Linux ownership ledger depends on, and it has one
/// trap: field 2 is the command name in parentheses and a process may name
/// itself anything at all, spaces and parentheses included. Splitting the
/// whole line on whitespace mis-numbers every field after it -- which would
/// mean recording a nonsense start identity, which would mean an ownership
/// record that never matches and a child this daemon can never reclaim.
///
/// Run on every platform on purpose: the failure it guards is a string
/// parse, and the machine that most needs it is the one that cannot run
/// the code around it.
#[test]
fn a_process_start_time_survives_a_command_name_full_of_parentheses() {
    let stat = |comm: &str| {
        let mut fields = vec!["4242".to_string(), format!("({comm})")];
        // Fields 3..21, then starttime at 22.
        fields.push("S".into());
        fields.extend((4..=21).map(|field| field.to_string()));
        fields.push("987654".into());
        // And the tail the kernel keeps writing after it.
        fields.extend((23..=30).map(|field| field.to_string()));
        fields.join(" ") + "\n"
    };

    assert_eq!(
        process_start_time(&stat("sleep")).as_deref(),
        Some("987654")
    );
    assert_eq!(
        process_start_time(&stat("my (weird) name")).as_deref(),
        Some("987654"),
        "the split has to happen after the LAST close paren"
    );
    assert_eq!(
        process_start_time(&stat("node --run start")).as_deref(),
        Some("987654"),
        "a name with spaces must not shift the field numbering"
    );
    // A truncated read is not a start identity, and must not be treated as
    // one -- an empty identity matches nothing and would be recorded as if
    // it did.
    assert_eq!(process_start_time("4242 (sleep) S 4 5"), None);
    assert_eq!(process_start_time("nonsense with no paren"), None);
    assert_eq!(process_start_time(""), None);
}

/// A manager that goes out of scope takes its services with it.
///
/// `stop_all()` on the success path used to be the only thing that stopped
/// them, so a test that panicked before reaching it -- and these tests
/// assert on live process state, which is exactly what flakes under load --
/// left two `sh` loops running forever, each forking a `sleep` every
/// second. `spawn_command` sets `process_group(0)`, so closing the terminal
/// sends them nothing, and the ownership ledger that could reclaim them is
/// in a `TempDir` that is already gone. This is how a laptop ends up warm
/// for a week.
///
/// Two things had to change for this to be assertable at all: the `Drop`
/// existed only on Windows, and the monitor thread held an `Arc` of the
/// manager, so the refcount never reached zero and no `Drop` could fire.
#[cfg(unix)]
#[test]
fn dropping_a_manager_stops_the_services_it_started() {
    let root = tempdir().unwrap();
    let mut backend = service("backend", &[]);
    backend.command = long_running_command();
    let mut frontend = service("frontend", &["backend"]);
    frontend.command = long_running_command();
    let mut value = manifest(vec![backend, frontend]);
    value.setup[0].command = vec!["/usr/bin/true".into()];

    let pids: Vec<u32> = {
        let manager = manager_in(&root, value);
        manager.start_all().unwrap();
        let pids = manager
            .status()
            .iter()
            .filter_map(|process| process.pid)
            .collect();
        // No `stop_all`. This is the unwind path, written as a scope.
        pids
    };

    assert_eq!(pids.len(), 2, "both services should have been running");
    // Asserted on the process *group*, which is what `spawn_command`
    // creates and what would still hold the `sleep` children.
    for pid in pids {
        let group = i32::try_from(pid).expect("a pid fits in i32");
        let deadline = Instant::now() + Duration::from_secs(5);
        while unsafe { libc::kill(-group, 0) } == 0 && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(50));
        }
        assert_ne!(
            unsafe { libc::kill(-group, 0) },
            0,
            "process group {group} outlived the manager that started it",
        );
    }
}

/// The stamp decision itself, on every platform.
///
/// The four tests that prove this end to end spawn `/bin/sh`, so they are
/// `#[cfg(unix)]` -- three failed on Windows for exactly that reason, and
/// the fourth passed there without proving anything. This is the half that
/// needs no process, and it is the half that decides whether a migration
/// runs.
#[test]
fn a_setup_reruns_unless_its_exact_stamp_was_recorded() {
    let recorded = |value: &str| Some(value.to_owned());

    // No stamp is how a setup opts out of this entirely.
    assert!(!setup_is_already_done(None, None));
    assert!(!setup_is_already_done(
        None,
        recorded("release-0.7.0").as_ref()
    ));

    // Never run before.
    assert!(!setup_is_already_done(Some("release-0.7.0"), None));

    // Run before, same work.
    assert!(setup_is_already_done(
        Some("release-0.7.0"),
        recorded("release-0.7.0").as_ref()
    ));

    // Run before, different work: a new pack, or migrations that changed
    // inside one. Skipping here is a backend starting against tables that
    // were never created.
    assert!(!setup_is_already_done(
        Some("release-0.8.0"),
        recorded("release-0.7.0").as_ref()
    ));
    // And no accidental prefix or case matching.
    assert!(!setup_is_already_done(
        Some("release-0.7.0"),
        recorded("release-0.7.0-rc1").as_ref()
    ));
    assert!(!setup_is_already_done(
        Some("release-0.7.0"),
        recorded("RELEASE-0.7.0").as_ref()
    ));
}

/// Every test that drives a real process is gated to the platforms that
/// can drive one.
///
/// The helpers below -- `long_running_command`, `crash`, `wait_for_running`,
/// `wait_for_recorded_exit` -- are `#[cfg(unix)]`, because they signal
/// process groups and shell out to `sh`. A test that uses one without the
/// same gate does not fail on Windows, it fails to *compile*, and the only
/// place that shows up is the Windows CI job -- which is not in the desktop
/// filter, so the feedback arrives a push or two later. It has now cost
/// three round trips.
///
/// A source lint rather than a convention, in the shape `lib.rs` already
/// uses for the console-window rule.
#[test]
fn a_test_that_drives_a_real_process_is_gated_to_unix() {
    const HELPERS: [&str; 4] = [
        "long_running_command(",
        "crash(&",
        "wait_for_running(&",
        "wait_for_recorded_exit(&",
    ];
    // A POSIX binary is the other way a test needs a unix host, and it is
    // the one that cost the third round: four stamp tests ran `/bin/sh` and
    // `/usr/bin/false`. Three failed on Windows with "The system cannot
    // find the path specified"; the fourth *passed*, because it asserts the
    // setup fails and a missing binary fails too -- proving nothing, in
    // green.
    const POSIX_BINARIES: [&str; 3] = ["\"/bin/", "\"/usr/bin/", "\"/sbin/"];
    const NAMED_NOT_RUN: [&str; 1] = ["a_checkout_never_reaches_for_the_keychain"];
    // Every source file in this crate, not just this one. Both rounds of
    // Windows failures were in here, but the next one need not be -- and a
    // lint that only reads its own file is a lint that moves the problem.
    // Read from disk rather than listed: this file used to name sixteen, and
    // two of those sixteen have since become directories.
    let sources = crate::locald_sources();

    let mut ungated = Vec::new();
    for (file, source) in &sources {
        let lines: Vec<&str> = source.lines().collect();
        for (index, line) in lines.iter().enumerate() {
            let Some(name) = line.trim().strip_prefix("fn ") else {
                continue;
            };
            // A test function: `#[test]` on one of the few lines above it.
            let preamble = &lines[index.saturating_sub(4)..index];
            if !preamble.iter().any(|line| line.trim() == "#[test]") {
                continue;
            }
            let gated = preamble.iter().any(|line| line.trim() == "#[cfg(unix)]");
            if gated {
                continue;
            }
            // The body, to its closing brace at the function's own
            // indentation. That used to be spelled `"    }"`, because every
            // test in this crate lived inside a `mod tests {`. They are
            // files now, indented at nothing -- and a terminator that never
            // matches does not fail, it swallows the rest of the file, so
            // every ungated test inherited the POSIX binaries of the ones
            // below it.
            let indent = line.len() - line.trim_start().len();
            let closing = format!("{}}}", " ".repeat(indent));
            let body: String = lines[index..]
                .iter()
                .take_while(|line| **line != closing)
                .copied()
                .collect::<Vec<_>>()
                .join("\n");
            let name = name.split('(').next().unwrap_or(name);
            // This test names the helpers in order to look for them.
            if name == "a_test_that_drives_a_real_process_is_gated_to_unix" {
                continue;
            }
            // A POSIX path that is named and never run. `source_bindings_with`
            // is *told* where `uv` and `node` would be and asked where secrets
            // go, which is not a question about this host. Exempt by name
            // rather than gated: gating it would stop it running on Windows,
            // where the keychain question it answers is just as real. Found by
            // this guard once it started reading the whole crate instead of a
            // list of sixteen files.
            if NAMED_NOT_RUN.contains(&name) {
                continue;
            }
            let needs_unix = HELPERS.iter().any(|helper| body.contains(helper))
                || POSIX_BINARIES.iter().any(|path| body.contains(path));
            if needs_unix {
                ungated.push(format!("{file}::{name}"));
            }
        }
    }

    assert!(
        ungated.is_empty(),
        "these tests need a unix host -- a helper that signals a process \
         group, or a POSIX binary to run -- and are not #[cfg(unix)]. On \
         Windows they either fail to compile or fail to find the binary: \
         {ungated:?}",
    );
}

#[cfg(unix)]
#[test]
fn process_ledger_reclaims_only_an_exact_owned_process() {
    use std::os::unix::process::ExitStatusExt;

    let root = tempdir().unwrap();
    let ledger_path = root.path().join("processes.json");
    let installation_id = "0123456789abcdef0123456789abcdef";
    let mut child = Command::new("/bin/sleep").arg("30").spawn().unwrap();
    let identity = process_identity(child.id()).unwrap();
    let mut backend = service("backend", &[]);
    backend.command = vec!["/bin/sleep".into(), "30".into()];
    let value = manifest(vec![backend, service("frontend", &["backend"])]);
    write_process_ledger(
        &ledger_path,
        &ProcessLedger {
            schema_version: PROCESS_LEDGER_SCHEMA_VERSION,
            installation_id: installation_id.into(),
            entries: vec![ProcessLedgerEntry {
                service_id: "backend".into(),
                pid: child.id(),
                executable: identity.executable,
                start_identity: identity.start_identity,
                installation_id: installation_id.into(),
                runtime_generation: "0123456789abcdef0123456789abcdef".into(),
            }],
        },
    )
    .unwrap();

    // Reaped in parallel, which is the whole trick.
    //
    // `terminate_verified_process` signals, then waits for the PID to stop
    // existing before escalating. A dead child nobody has reaped is a
    // zombie, and a zombie still answers `kill(pid, 0)` -- so this test's
    // process could never be observed to exit, the wait ran its full five
    // seconds every time, and the assertion that followed was left racing
    // whatever the runner did next. Production never has this problem: a
    // reclaimed process belonged to a previous locald and is reaped by
    // init, so its PID really does go away.
    //
    // Reaping here restores that, and the exit status is then an exact
    // answer rather than a deadline: signalled means reclaimed, and a
    // process that was missed runs out its own 30 seconds and fails
    // saying so.
    let reaper = thread::spawn(move || child.wait().unwrap());
    reclaim_verified_processes(&ledger_path, installation_id, &value).unwrap();
    let status = reaper.join().unwrap();
    assert!(
        status.signal().is_some(),
        "the reclaimed process exited on its own rather than being killed: \
         {status:?}",
    );
    assert!(read_process_ledger(&ledger_path)
        .unwrap()
        .entries
        .is_empty());
}

#[cfg(unix)]
#[test]
fn process_ledger_never_kills_a_pid_with_the_wrong_start_identity() {
    let root = tempdir().unwrap();
    let ledger_path = root.path().join("processes.json");
    let installation_id = "0123456789abcdef0123456789abcdef";
    let mut child = Command::new("/bin/sleep").arg("30").spawn().unwrap();
    let identity = process_identity(child.id()).unwrap();
    let mut backend = service("backend", &[]);
    backend.command = vec!["/bin/sleep".into(), "30".into()];
    let value = manifest(vec![backend, service("frontend", &["backend"])]);
    write_process_ledger(
        &ledger_path,
        &ProcessLedger {
            schema_version: PROCESS_LEDGER_SCHEMA_VERSION,
            installation_id: installation_id.into(),
            entries: vec![ProcessLedgerEntry {
                service_id: "backend".into(),
                pid: child.id(),
                executable: identity.executable,
                start_identity: "different-process-start".into(),
                installation_id: installation_id.into(),
                runtime_generation: "0123456789abcdef0123456789abcdef".into(),
            }],
        },
    )
    .unwrap();

    reclaim_verified_processes(&ledger_path, installation_id, &value).unwrap();

    assert!(child.try_wait().unwrap().is_none());
    child.kill().unwrap();
    child.wait().unwrap();
}

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

#[cfg(unix)]
fn process_status(manager: &HostProcessManager, id: &str) -> HostProcessStatus {
    manager
        .status()
        .into_iter()
        .find(|process| process.id == id)
        .unwrap_or_else(|| panic!("{id} is not a managed service"))
}

/// Blocks until the supervisor reports `id` running, and answers with its
/// pid.
///
/// The mirror of `wait_for_recorded_exit`, and needed for the same reason.
/// "Started" is not a moment the caller of `start_all` or `reconcile_crashes`
/// observes — a spawn is recorded when the supervisor gets to it — so a test
/// that reads the pid straight afterwards is racing it. That read is not
/// even the assertion in most cases; it is the setup for one, so on a loaded
/// runner these failed as `backend is not running` from inside a helper,
/// several lines away from anything the test was actually about.
#[cfg(unix)]
fn wait_for_running(manager: &HostProcessManager, id: &str) -> u32 {
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        if let Some(pid) = process_status(manager, id).pid {
            return pid;
        }
        assert!(
            Instant::now() < deadline,
            "{id} never started; the supervisor still reports no process for it"
        );
        thread::sleep(Duration::from_millis(10));
    }
}

/// Kills a running service the way a crash takes one: no notice, no chance
/// to shut down. The whole process group goes, so nothing of it survives to
/// be reported as still running.
///
/// Waits for the service to be up first, so "crash it" means what it says
/// whether or not the supervisor has caught up with a start or a restart.
#[cfg(unix)]
fn crash(manager: &HostProcessManager, id: &str) {
    let pid = wait_for_running(manager, id);
    let group = -i32::try_from(pid).expect("process id fits a signed integer");
    // SAFETY: the pid names a child this manager spawned into its own
    // process group and has not yet reaped, so the group is still ours.
    assert_eq!(
        unsafe { libc::kill(group, libc::SIGKILL) },
        0,
        "could not crash {id}: {}",
        io::Error::last_os_error()
    );
}

/// Blocks until the supervisor has recorded that `id` exited.
///
/// Reporting status is what reaps an exited child, so this is the same
/// observation the supervision loop makes — waited for, rather than assumed
/// to have happened by the end of a sleep that a loaded machine outruns.
#[cfg(unix)]
fn wait_for_recorded_exit(manager: &HostProcessManager, id: &str) {
    let deadline = Instant::now() + Duration::from_secs(10);
    while process_status(manager, id).running {
        assert!(
            Instant::now() < deadline,
            "{id} never reported the exit it was killed for"
        );
        thread::sleep(Duration::from_millis(10));
    }
}

/// Reconcile until the supervisor reaches `what`, or fail saying what it
/// actually reached.
///
/// Drive to the state, do not count the steps. Two calls happen to be
/// what a restart costs today -- one to spend the budget, one to act on it
/// -- but that is an implementation detail of `reconcile_crashes`, and a
/// test that hard-codes it asserts against whichever state the count lands
/// on rather than the one it means. Waiting for the state also fails
/// legibly: it says what was actually reached instead of `assertion failed:
/// !backend.running`, which is all CI got.
///
/// `manager_in` builds these without a supervisor (see
/// `without_supervisor`), so nothing else is stepping the machine while
/// this runs.
///
/// Unix-gated because `process_status` is: it reaps, which needs waitpid.
#[cfg(unix)]
fn reconcile_until(
    manager: &HostProcessManager,
    id: &str,
    what: &str,
    reached: impl Fn(&HostProcessStatus) -> bool,
) -> HostProcessStatus {
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        manager.reconcile_crashes();
        let status = process_status(manager, id);
        if reached(&status) {
            return status;
        }
        assert!(
            Instant::now() < deadline,
            "{id} never {what}. running={} circuit_open={} restart_count={} \
             last_exit={:?}",
            status.running,
            status.circuit_open,
            status.restart_count,
            status.last_exit,
        );
        thread::sleep(Duration::from_millis(20));
    }
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

/// A stamped setup runs once and is skipped while its stamp holds.
///
/// Both setups ran on every start. The cost is not the SQL -- alembic's
/// no-op is one `SELECT` -- it is `env.py` importing the whole ORM graph
/// before it can decide there is nothing to do, on every launch.
#[cfg(unix)]
#[test]
fn a_stamped_setup_is_not_repeated_while_its_stamp_holds() {
    let root = tempdir().unwrap();
    let marker = root.path().join("ran");
    let mut value = manifest(vec![service("backend", &[]), service("frontend", &[])]);
    value.setup[0].command = vec![
        "/bin/sh".into(),
        "-c".into(),
        format!("echo x >> {}", marker.display()),
    ];
    value.setup[0].stamp = Some("release-0.7.0".into());
    let manager = manager_in(&root, value);

    manager.run_setups().unwrap();
    manager.run_setups().unwrap();
    manager.run_setups().unwrap();

    let runs = std::fs::read_to_string(&marker).unwrap().lines().count();
    assert_eq!(runs, 1, "a stamped setup runs once, not once per start");
}

/// A changed stamp runs it again; so does an unstamped setup.
#[cfg(unix)]
#[test]
fn a_changed_stamp_runs_the_setup_again() {
    let root = tempdir().unwrap();
    let marker = root.path().join("ran");
    let command = vec![
        "/bin/sh".into(),
        "-c".into(),
        format!("echo x >> {}", marker.display()),
    ];

    let mut first = manifest(vec![service("backend", &[]), service("frontend", &[])]);
    first.setup[0].command = command.clone();
    first.setup[0].stamp = Some("release-0.7.0".into());
    manager_in(&root, first).run_setups().unwrap();

    // A new release: the migrations it ships are not the ones already run.
    let mut second = manifest(vec![service("backend", &[]), service("frontend", &[])]);
    second.setup[0].command = command.clone();
    second.setup[0].stamp = Some("release-0.8.0".into());
    manager_in(&root, second).run_setups().unwrap();

    // No stamp at all behaves exactly as before stamps existed.
    let mut third = manifest(vec![service("backend", &[]), service("frontend", &[])]);
    third.setup[0].command = command;
    third.setup[0].stamp = None;
    manager_in(&root, third).run_setups().unwrap();

    assert_eq!(std::fs::read_to_string(&marker).unwrap().lines().count(), 3);
}

/// A failed setup is never stamped.
///
/// Stamping anything but success would let a half-finished migration be
/// skipped on the next start, which is strictly worse than running it
/// again.
#[cfg(unix)]
#[test]
fn a_failing_setup_is_not_stamped_as_done() {
    let root = tempdir().unwrap();
    let mut value = manifest(vec![service("backend", &[]), service("frontend", &[])]);
    value.setup[0].command = vec!["/usr/bin/false".into()];
    value.setup[0].stamp = Some("release-0.7.0".into());
    value.setup[0].max_attempts = 1;
    let manager = manager_in(&root, value);

    assert!(manager.run_setups().is_err());
    assert!(
        manager.recorded_setup_stamps().is_empty(),
        "a setup that failed must run again next time"
    );
}

/// A data reset makes every setup run again.
///
/// The database the migrations stamp describes is gone, but a Tier 1 reset
/// leaves the locald root standing -- so without forgetting the stamps the
/// next start would skip migrations against an empty schema and the backend
/// would come up against tables that were never created.
#[cfg(unix)]
#[test]
fn forgetting_stamps_makes_a_reset_installation_migrate_again() {
    let root = tempdir().unwrap();
    let marker = root.path().join("ran");
    let mut value = manifest(vec![service("backend", &[]), service("frontend", &[])]);
    value.setup[0].command = vec![
        "/bin/sh".into(),
        "-c".into(),
        format!("echo x >> {}", marker.display()),
    ];
    value.setup[0].stamp = Some("release-0.7.0".into());
    let manager = manager_in(&root, value);

    manager.run_setups().unwrap();
    manager.forget_setup_stamps().unwrap();
    manager.run_setups().unwrap();

    assert_eq!(std::fs::read_to_string(&marker).unwrap().lines().count(), 2);
    // Clearing twice is what a retried reset does; it must not fail.
    manager.forget_setup_stamps().unwrap();
}

/// An optional setup that *hangs* is tolerated, exactly like one that fails.
///
/// `optional` was honoured at only one of the three places a setup can
/// fail. A non-zero exit was swallowed; running out of time was not — so
/// `connector-catalog`, which is declared optional with a 600-second budget
/// precisely so an unreachable third-party catalog cannot stop a workspace,
/// took the whole stack down whenever the network blackholed instead of
/// refusing.
#[cfg(unix)]
#[test]
fn an_optional_setup_that_hangs_does_not_stop_the_stack() {
    let root = tempdir().unwrap();
    let mut value = manifest(vec![service("backend", &[]), service("frontend", &[])]);
    value.setup[0].command = vec!["/usr/bin/true".into()];
    let mut catalog = setup("connector-catalog");
    catalog.command = long_running_command();
    catalog.optional = true;
    catalog.timeout_seconds = 1;
    catalog.max_attempts = 1;
    value.setup.push(catalog);
    let manager = manager_in(&root, value);

    manager
        .run_setups()
        .expect("an optional setup that never finishes must not fail the start");
}

/// The same hang, not marked optional, still stops the start.
///
/// Without this the test above would pass just as well against a
/// `run_setups` that had stopped enforcing timeouts at all.
#[cfg(unix)]
#[test]
fn a_required_setup_that_hangs_still_stops_the_stack() {
    let root = tempdir().unwrap();
    let mut value = manifest(vec![service("backend", &[]), service("frontend", &[])]);
    value.setup[0].command = long_running_command();
    value.setup[0].optional = false;
    value.setup[0].timeout_seconds = 1;
    value.setup[0].max_attempts = 1;
    let manager = manager_in(&root, value);

    let error = manager.run_setups().unwrap_err();
    assert_eq!(error.kind(), io::ErrorKind::TimedOut);
    assert!(error.to_string().contains("migrations"), "{error}");
}
