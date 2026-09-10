use super::*;

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
            if daemon.agent_lifecycle.begin().is_ok() {
                if let Err(error) = daemon.agent_host.reconcile() {
                    let _ =
                        daemon.write_daemon_log(&format!("Agent Host recovery failed: {error}"));
                }
                daemon.agent_lifecycle.finish();
            }
            thread::sleep(std::time::Duration::from_secs(1));
        });
    }

    pub(super) fn start_host_status_monitor(self: &Arc<Self>) {
        if self.host_processes.is_none() {
            return;
        }
        let daemon = Arc::clone(self);
        thread::spawn(move || {
            let mut previous = String::new();
            let mut next_runtime_probe = std::time::Instant::now();
            let mut next_runtime_recovery = std::time::Instant::now();
            let mut runtime_failure_reported = false;
            loop {
                if daemon.lifecycle.checkpoint().is_err() {
                    return;
                }
                let manager = daemon
                    .host_processes
                    .as_ref()
                    .expect("host monitor requires manager");
                let now = std::time::Instant::now();
                if now >= next_runtime_probe && !daemon.lifecycle.busy() {
                    next_runtime_probe = now + std::time::Duration::from_secs(5);
                    if let Some(runtime) = daemon.managed_runtime.as_ref() {
                        let runtime_expected =
                            runtime.status().is_some() || manager.desired_running();
                        if runtime_expected {
                            let probe = runtime.probe();
                            if daemon.lifecycle.checkpoint().is_err() {
                                return;
                            }
                            match probe {
                                ProbeOutcome::Healthy(_) => {
                                    manager.mark_dependency_ready();
                                    runtime_failure_reported = false;
                                }
                                // The guest did not answer in time, which a
                                // busy control channel looks exactly like.
                                // Leave the running stack and its forwarders
                                // alone; a real loss is reported by the next
                                // probes.
                                ProbeOutcome::Transient(_) => {}
                                ProbeOutcome::Lost(error) => {
                                    let message = error.to_string();
                                    manager.mark_dependency_unavailable(message.clone());
                                    if !runtime_failure_reported {
                                        runtime_failure_reported = true;
                                        daemon.broadcast(error_event(
                                            "managed-runtime-lost",
                                            format!(
                                                "Lemma's private runtime stopped unexpectedly: {message}"
                                            ),
                                            None,
                                        ));
                                        daemon.broadcast(json!({
                                            "v": PROTOCOL_VERSION,
                                            "event": "state",
                                            "status": "error",
                                            "running": true,
                                            "ready": false,
                                        }));
                                    }
                                    if manager.desired_running()
                                        && now >= next_runtime_recovery
                                        && daemon.lifecycle.begin().is_ok()
                                    {
                                        next_runtime_recovery =
                                            now + std::time::Duration::from_secs(15);
                                        manager.mark_dependency_recovering();
                                        daemon.broadcast(json!({
                                            "v": PROTOCOL_VERSION,
                                            "event": "phase",
                                            "key": "runtime-recovery",
                                            "label": "Recovering private runtime",
                                            "progress": 38,
                                            "detail": "restarting app-owned Linux services",
                                        }));
                                        let recovery = Arc::clone(&daemon);
                                        thread::spawn(move || {
                                            let result = recovery.recover_managed_stack();
                                            if let Err(error) = result {
                                                if let Some(manager) =
                                                    recovery.host_processes.as_ref()
                                                {
                                                    manager.mark_dependency_unavailable(
                                                        error.to_string(),
                                                    );
                                                }
                                                recovery.broadcast(error_event(
                                                    "managed-runtime-recovery-failed",
                                                    format!(
                                                        "Could not recover Lemma's private runtime: {error}"
                                                    ),
                                                    None,
                                                ));
                                            }
                                            recovery.lifecycle.finish();
                                        });
                                    }
                                }
                            }
                        }
                    }
                }
                let event = manager.status_event(None);
                let current = event.to_string();
                if current != previous {
                    previous = current;
                    daemon.broadcast(event);
                }
                if let Some(sharing) = daemon.sharing.as_ref() {
                    if let Some(message) = sharing.poll_failure() {
                        if daemon.lifecycle.begin().is_ok() {
                            let recovery = Arc::clone(&daemon);
                            let sharing = Arc::clone(sharing);
                            thread::spawn(move || {
                                let result = recovery.disable_sharing_transaction(&sharing);
                                match result {
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
                                        }))
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
                                recovery.lifecycle.finish();
                            });
                        }
                    }
                }
                thread::sleep(std::time::Duration::from_secs(1));
            }
        });
    }
}
