//! Stopping everything this daemon runs, for a quit, an update, or a signal.
//!
//! In tiers, each timed. A tier's steps run at once; a tier waits for the one
//! before it only where the later one depends on the earlier:
//!
//! 1. Whatever operation is in flight reaches its next checkpoint. Instant
//!    when nothing is running.
//! 2. The sharing tunnel and the Agent Host. The Agent Host goes before the
//!    backend because its last act is reporting its runs' final states to it.
//! 3. The backend and frontend, side by side.
//! 4. The private runtime -- the guest's services, then the VM -- and, on a
//!    developer stack, the supervisor. After the backend, whose last writes
//!    go to the database the guest holds.
//!
//! Every step is broadcast as a `shutdown.step` event and logged with its
//! duration, so a slow quit names the step it waited on.
use super::*;
use crate::stop_plan::{first_error, run_tier, summary, Step, StepOutcome};
// The Agent Host's teardown, reused rather than rewritten: it already knows how
// to stop a tree on both platforms, and there is only one right way to do it.
use crate::agent_host::terminate_process_tree;
use std::time::Instant;

impl Daemon {
    pub(super) fn start_daemon_shutdown(
        self: &Arc<Self>,
        id: Option<Value>,
        client: mpsc::SyncSender<String>,
    ) {
        if self.shutdown_running.swap(true, Ordering::AcqRel) {
            self.send_direct(
                &client,
                error_event(
                    "shutdown-in-progress",
                    "the local daemon is already stopping",
                    id.as_ref(),
                ),
            );
            return;
        }
        // Read before admission closes: afterwards `busy` is always true.
        let operation_in_flight = self.lifecycle.in_use() || self.agent_lifecycle.in_use();
        self.lifecycle.request_shutdown();
        self.agent_lifecycle.request_shutdown();
        if let Some(manager) = self.host_processes.as_ref() {
            manager.request_stop();
        }
        if let Some(runtime) = self.managed_runtime.as_ref() {
            runtime.cancel_pending_requests();
        }
        self.send_direct(
            &client,
            json!({
                "v": PROTOCOL_VERSION, "event": "ack",
                "cmd": "shutdown-daemon", "id": id.as_ref(),
            }),
        );
        let daemon = Arc::clone(self);
        thread::spawn(move || {
            let outcomes = daemon.stop_everything(id.as_ref(), operation_in_flight);
            if let Some(message) = first_error(&outcomes) {
                // Exit anyway. By here admission is closed for good, the
                // supervisor is gone and pending runtime requests are
                // cancelled, so a daemon that stayed up refused every later
                // start with "Lemma is stopping" until something killed it.
                // Whatever did not stop is reclaimed by identity on the next
                // start: the VM through its process marker, host services
                // through their ledger.
                let _ = daemon.write_daemon_log(&format!(
                    "shutdown finished with an error; exiting anyway: {message}"
                ));
                daemon.send_direct(
                    &client,
                    error_event("shutdown-failed", message, id.as_ref()),
                );
                thread::sleep(std::time::Duration::from_millis(100));
                std::process::exit(1);
            }
            daemon.send_direct(
                &client,
                json!({
                    "v": PROTOCOL_VERSION, "event": "done",
                    "cmd": "shutdown-daemon", "id": id.as_ref(), "ok": true,
                }),
            );
            // Give the authenticated client writer a moment to flush the
            // acknowledgement before ending this dedicated daemon process.
            thread::sleep(std::time::Duration::from_millis(100));
            std::process::exit(0);
        });
    }

    /// Stop everything, in tiers, reporting each step as it finishes.
    ///
    /// Returns every step's outcome in plan order. Broadcasts `state stopped`
    /// only when nothing failed; the caller decides how the process ends.
    pub(super) fn stop_everything(
        self: &Arc<Self>,
        id: Option<&Value>,
        operation_in_flight: bool,
    ) -> Vec<StepOutcome> {
        let started = Instant::now();
        let report = |outcome: &StepOutcome| self.report_shutdown_step(outcome, id);
        let mut outcomes = Vec::new();

        // The wait is only named when there is something to wait for. It was
        // announced on every quit, so a stop that spent its time on the VM
        // read as one stuck behind an operation that did not exist.
        if operation_in_flight {
            self.announce_stopping(
                id,
                0,
                "Waiting for the current operation to reach a safe stopping point",
            );
        }
        outcomes.extend(run_tier(
            vec![Step::new("operations", || {
                self.lifecycle.wait_idle();
                self.agent_lifecycle.wait_idle();
                Ok(())
            })],
            &report,
        ));

        self.announce_stopping(id, 10, "Stopping the Agent Host and sharing");
        let mut first = vec![Step::new("agent-host", || self.agent_host.suspend())];
        if let Some(sharing) = self.sharing.as_ref() {
            first.push(Step::new("sharing", || {
                sharing.force_disable();
                Ok(())
            }));
        }
        outcomes.extend(run_tier(first, &report));

        if let Some(manager) = self.host_processes.as_ref() {
            self.announce_stopping(id, 30, "Stopping the workspace services");
            outcomes.extend(run_tier(
                vec![Step::new("host-processes", || {
                    let (result, services) = manager.stop_all_timed();
                    for (service, duration) in services {
                        report(&StepOutcome::measured(
                            format!("host.{service}"),
                            duration,
                            None,
                        ));
                    }
                    result
                })],
                &report,
            ));
        }

        self.announce_stopping(id, 60, "Stopping the database and the private runtime");
        let mut last = Vec::new();
        if let Some(runtime) = self.managed_runtime.as_ref() {
            last.push(Step::new("runtime", || runtime.shutdown_reporting(&report)));
        }
        // A developer stack's supervisor, which a managed install never has.
        if self
            .supervisor
            .lock()
            .expect("supervisor lock poisoned")
            .is_some()
        {
            last.push(Step::new("supervisor", || {
                self.stop_supervisor();
                Ok(())
            }));
        }
        outcomes.extend(run_tier(last, &report));

        let total = started.elapsed();
        let _ = self.write_daemon_log(&summary(&outcomes, total));
        if first_error(&outcomes).is_none() {
            self.broadcast(json!({
                "v": PROTOCOL_VERSION, "event": "state", "status": "stopped",
                "running": false, "ready": false, "operation_id": id,
                "duration_ms": u64::try_from(total.as_millis()).unwrap_or(u64::MAX),
            }));
        }
        outcomes
    }

    fn announce_stopping(&self, id: Option<&Value>, progress: u64, detail: &str) {
        self.broadcast(json!({
            "v": PROTOCOL_VERSION, "event": "phase", "key": "stopping",
            "label": "Stopping Lemma", "progress": progress,
            "detail": detail, "operation_id": id,
        }));
    }

    /// One `shutdown.step` event and one log line per finished step.
    fn report_shutdown_step(&self, outcome: &StepOutcome, id: Option<&Value>) {
        let mut event = json!({
            "v": PROTOCOL_VERSION, "event": "shutdown.step",
            "step": outcome.step, "duration_ms": outcome.duration_ms(),
            "ok": outcome.error.is_none(), "operation_id": id,
        });
        if let Some(error) = outcome.error.as_ref() {
            event["error"] = Value::String(error.clone());
        }
        if let Some(detail) = outcome.detail.as_ref() {
            event["detail"] = Value::String(detail.clone());
        }
        let _ = self.write_daemon_log(&format!(
            "shutdown step {} took {}ms{}{}",
            outcome.step,
            outcome.duration_ms(),
            outcome
                .error
                .as_ref()
                .map(|error| format!(" and failed: {error}"))
                .unwrap_or_default(),
            outcome
                .detail
                .as_ref()
                .map(|detail| format!(" ({detail})"))
                .unwrap_or_default(),
        ));
        self.broadcast(event);
    }

    fn stop_supervisor(&self) {
        // Taken out under the lock; killed and reaped outside it. `wait`
        // blocks until the process is gone, and every client thread that
        // wants the supervisor takes this same lock.
        let taken = self
            .supervisor
            .lock()
            .expect("supervisor lock poisoned")
            .take();
        if let Some(mut supervisor) = taken {
            // The tree, not the leader. `uv run ... lemma-stack supervise`
            // is uv, then Python, then whatever the stack started; killing
            // only the leader left the rest running with nothing to reap
            // them. This is the same teardown the Agent Host uses, and the
            // spawn puts the supervisor in its own group so it can be
            // asked for.
            let _ = terminate_process_tree(&mut supervisor.child);
        }
    }
}
