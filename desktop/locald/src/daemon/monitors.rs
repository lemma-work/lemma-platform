use super::*;
use std::ops::ControlFlow;
use std::time::{Duration, Instant};

impl Daemon {
    /// Fill in the backend environment once the control socket is already up.
    ///
    /// Reading the operator's secrets can block on an OS credential-vault prompt,
    /// which is why this cannot happen during `Daemon::new`. Off the accept path
    /// the cost is invisible: the shell connects, the workspace renders, and the
    /// prompt — if there is one — arrives over a window that already works.
    ///
    /// Failure here is deliberately not fatal. Every caller that starts host
    /// processes rebuilds the environment first and surfaces its own error, so a
    /// vault the user dismissed costs a log line rather than the daemon.
    pub(super) fn prime_backend_environment(self: &Arc<Self>) {
        if self.host_processes.is_none() {
            return;
        }
        let daemon = Arc::clone(self);
        thread::spawn(move || {
            let Some(manager) = daemon.host_processes.as_ref() else {
                return;
            };
            match daemon.backend_environment() {
                Ok(environment) => manager.set_backend_environment(environment),
                Err(error) => {
                    let _ = daemon
                        .write_daemon_log(&format!("backend environment unavailable: {error}"));
                }
            }
        });
    }

    pub(super) fn start_agent_host_monitor(self: &Arc<Self>) {
        // Honour what the user last chose rather than starting unconditionally.
        // Turning the Agent Host off has to survive a daemon restart, and an
        // unpaired machine has nothing for it to do.
        let daemon = Arc::clone(self);
        thread::spawn(move || loop {
            if daemon.agent_lifecycle.checkpoint().is_err() {
                return;
            }
            if let Some(_finish) = daemon.agent_lifecycle.enter() {
                if let Err(error) = daemon.agent_host.reconcile() {
                    let _ =
                        daemon.write_daemon_log(&format!("Agent Host recovery failed: {error}"));
                }
            }
            thread::sleep(Duration::from_secs(1));
        });
    }

    pub(super) fn start_host_status_monitor(self: &Arc<Self>) {
        if self.host_processes.is_none() {
            return;
        }
        let daemon = Arc::clone(self);
        thread::spawn(move || {
            let manager = daemon
                .host_processes
                .as_ref()
                .expect("host monitor requires manager");
            let mut watch = RuntimeWatch::new(Instant::now());
            let mut previous = String::new();
            loop {
                if daemon.lifecycle.checkpoint().is_err() {
                    return;
                }
                if daemon.watch_managed_runtime(manager, &mut watch).is_break() {
                    return;
                }
                let event = manager.status_event(None);
                let current = event.to_string();
                if current != previous {
                    previous = current;
                    daemon.broadcast(event);
                }
                daemon.recover_failed_sharing();
                thread::sleep(Duration::from_secs(1));
            }
        });
    }

    /// Probe the private runtime when a probe is due, and act on what it says.
    ///
    /// `Break` means the daemon began shutting down while the probe ran, and
    /// the monitor must stop without touching the stack.
    fn watch_managed_runtime(
        self: &Arc<Self>,
        manager: &HostProcessManager,
        watch: &mut RuntimeWatch,
    ) -> ControlFlow<()> {
        let now = Instant::now();
        let Some(runtime) = self.managed_runtime.as_ref() else {
            return ControlFlow::Continue(());
        };
        if self.lifecycle.busy() || !watch.probe_due(now) {
            return ControlFlow::Continue(());
        }
        if runtime.status().is_none() && !manager.desired_running() {
            return ControlFlow::Continue(());
        }
        let probe = runtime.probe();
        if self.lifecycle.checkpoint().is_err() {
            return ControlFlow::Break(());
        }
        match probe {
            ProbeOutcome::Healthy(_) => {
                manager.mark_dependency_ready();
                watch.healthy();
            }
            // The guest did not answer in time, which a busy control channel
            // looks exactly like. Leave the running stack and its forwarders
            // alone; a real loss is reported by the next probes.
            ProbeOutcome::Transient(_) => {}
            ProbeOutcome::Lost(error) => self.runtime_lost(manager, watch, &error, now),
        }
        ControlFlow::Continue(())
    }

    fn runtime_lost(
        self: &Arc<Self>,
        manager: &HostProcessManager,
        watch: &mut RuntimeWatch,
        error: &std::io::Error,
        now: Instant,
    ) {
        let message = error.to_string();
        manager.mark_dependency_unavailable(message.clone());
        if watch.first_loss() {
            self.broadcast(error_event(
                "managed-runtime-lost",
                format!("Lemma's private runtime stopped unexpectedly: {message}"),
                None,
            ));
            self.broadcast(json!({
                "v": PROTOCOL_VERSION,
                "event": "state",
                "status": "error",
                "running": true,
                "ready": false,
            }));
        }
        if manager.desired_running() && watch.recovery_due(now) && self.lifecycle.begin().is_ok() {
            watch.recovery_started(now);
            manager.mark_dependency_recovering();
            self.broadcast(json!({
                "v": PROTOCOL_VERSION,
                "event": "phase",
                "key": "runtime-recovery",
                "label": "Recovering private runtime",
                "progress": 38,
                "detail": "restarting app-owned Linux services",
            }));
            self.spawn_runtime_recovery();
        }
    }

    /// Restart the stack on its own thread. The caller has already admitted
    /// the work with `lifecycle.begin()`.
    fn spawn_runtime_recovery(self: &Arc<Self>) {
        let recovery = Arc::clone(self);
        thread::spawn(move || {
            // Released however this thread ends -- see `lifecycle::Finish`.
            let _finish = recovery.lifecycle.finish_on_drop();
            let Err(error) = recovery.recover_managed_stack() else {
                return;
            };
            if let Some(manager) = recovery.host_processes.as_ref() {
                manager.mark_dependency_unavailable(error.to_string());
            }
            recovery.broadcast(error_event(
                "managed-runtime-recovery-failed",
                format!("Could not recover Lemma's private runtime: {error}"),
                None,
            ));
        });
    }

    /// A tunnel that exited on its own leaves sharing half-enabled; return the
    /// machine to This computer mode and say why.
    fn recover_failed_sharing(self: &Arc<Self>) {
        let Some(sharing) = self.sharing.as_ref() else {
            return;
        };
        let Some(message) = sharing.poll_failure() else {
            return;
        };
        if self.lifecycle.begin().is_err() {
            return;
        }
        let recovery = Arc::clone(self);
        let sharing = Arc::clone(sharing);
        thread::spawn(move || {
            // Released however this thread ends -- see `lifecycle::Finish`.
            let _finish = recovery.lifecycle.finish_on_drop();
            match recovery.disable_sharing_transaction(&sharing) {
                Ok(()) => {
                    let (url, api_url) = recovery.canonical_urls();
                    recovery.broadcast(json!({
                        "v": PROTOCOL_VERSION,
                        "event": "sharing.changed",
                        "reason": "tunnel-exited",
                        "message": message,
                        "url": url,
                        "api_url": api_url,
                        "sharing": sharing.snapshot(true),
                    }));
                }
                Err(error) => recovery.broadcast(scoped_error_event(
                    "sharing",
                    "sharing-recovery-failed",
                    format!(
                        "The tunnel exited and This computer mode could not be restored: {error}"
                    ),
                    None,
                )),
            }
        });
    }
}

const RUNTIME_PROBE_INTERVAL: Duration = Duration::from_secs(5);
const RUNTIME_RECOVERY_BACKOFF: Duration = Duration::from_secs(15);

/// What the host monitor remembers between runtime probes: when the next probe
/// and the next recovery attempt may happen, and whether the current outage
/// has already been announced.
///
/// Kept apart from the daemon so the schedule can be tested without a guest.
#[derive(Debug)]
pub(super) struct RuntimeWatch {
    next_probe: Instant,
    next_recovery: Instant,
    loss_reported: bool,
}

impl RuntimeWatch {
    pub(super) fn new(now: Instant) -> Self {
        Self {
            next_probe: now,
            next_recovery: now,
            loss_reported: false,
        }
    }

    /// Whether a probe is due, scheduling the next one when it is.
    pub(super) fn probe_due(&mut self, now: Instant) -> bool {
        if now < self.next_probe {
            return false;
        }
        self.next_probe = now + RUNTIME_PROBE_INTERVAL;
        true
    }

    /// The runtime answered, so the next loss is a new outage worth announcing.
    pub(super) fn healthy(&mut self) {
        self.loss_reported = false;
    }

    /// Whether this loss is the first since the runtime was last healthy --
    /// one announcement per outage, not one every five seconds.
    pub(super) fn first_loss(&mut self) -> bool {
        !std::mem::replace(&mut self.loss_reported, true)
    }

    pub(super) fn recovery_due(&self, now: Instant) -> bool {
        now >= self.next_recovery
    }

    pub(super) fn recovery_started(&mut self, now: Instant) {
        self.next_recovery = now + RUNTIME_RECOVERY_BACKOFF;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_first_probe_is_due_at_once_and_then_every_interval() {
        let start = Instant::now();
        let mut watch = RuntimeWatch::new(start);
        assert!(
            watch.probe_due(start),
            "the monitor probes as soon as it starts"
        );
        assert!(!watch.probe_due(start + Duration::from_secs(4)));
        assert!(watch.probe_due(start + RUNTIME_PROBE_INTERVAL));
    }

    #[test]
    fn an_outage_is_announced_once_until_the_runtime_recovers() {
        let mut watch = RuntimeWatch::new(Instant::now());
        assert!(watch.first_loss());
        assert!(
            !watch.first_loss(),
            "a continuing outage is not re-announced"
        );
        watch.healthy();
        assert!(
            watch.first_loss(),
            "a new outage after recovery is announced"
        );
    }

    #[test]
    fn recovery_is_attempted_at_most_once_per_backoff() {
        let start = Instant::now();
        let mut watch = RuntimeWatch::new(start);
        assert!(
            watch.recovery_due(start),
            "the first recovery is not delayed"
        );
        watch.recovery_started(start);
        assert!(!watch.recovery_due(start + Duration::from_secs(14)));
        assert!(watch.recovery_due(start + RUNTIME_RECOVERY_BACKOFF));
    }
}
