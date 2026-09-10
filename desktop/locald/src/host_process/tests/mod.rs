//! The host processes' guards, grouped the way the code they cover is
//! grouped.

mod dependencies;
mod gating;
mod ledger;
mod logs;
mod manifest;
mod ports;
mod process_groups;
mod restart;
mod setups;
mod shutdown;
mod startup;
mod stop;

use super::*;
use crate::host_process::setups::*;
// Only the unix tests spawn a real supervised process to bind a port.
#[cfg(unix)]
use crate::port_reservation::PortReservation;
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
pub(super) fn log_dir_in(root: &TempDir) -> PathBuf {
    root.path().join("logs")
}

/// A manager whose installation state stays inside `root`.
pub(super) fn manager_in(root: &TempDir, value: HostPackManifest) -> Arc<HostProcessManager> {
    // No supervisor thread. These tests step the state machine themselves,
    // and a second driver of it once a second is what made the restart
    // circuit tests fail on CI and never here.
    HostProcessManager::without_supervisor(value, log_dir_in(root)).unwrap()
}

pub(super) fn managed_runtime_spec(backend: u16, frontend: u16) -> ManagedRuntimeSpec {
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

pub(super) fn manifest(services: Vec<HostProcessSpec>) -> HostPackManifest {
    HostPackManifest {
        schema_version: 1,
        release: "test".into(),
        managed_runtime: None,
        setup: vec![setup("migrations")],
        services,
    }
}

pub(super) fn setup(id: &str) -> HostSetupSpec {
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

pub(super) fn service(id: &str, dependencies: &[&str]) -> HostProcessSpec {
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
pub(super) fn long_running_command() -> Vec<String> {
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
pub(super) struct HealthServer {
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
pub(super) fn slow_response(
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

pub(super) fn one_response(status: u16, body: &str) -> (HttpHealthSpec, thread::JoinHandle<()>) {
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

#[cfg(unix)]
pub(super) fn process_status(manager: &HostProcessManager, id: &str) -> HostProcessStatus {
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
pub(super) fn wait_for_running(manager: &HostProcessManager, id: &str) -> u32 {
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
pub(super) fn crash(manager: &HostProcessManager, id: &str) {
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
pub(super) fn wait_for_recorded_exit(manager: &HostProcessManager, id: &str) {
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
pub(super) fn reconcile_until(
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
