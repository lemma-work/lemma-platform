use super::*;

impl Daemon {
    // Desktop calls this on full quit. The daemon deliberately outlives the
    // app, so anything that must not survive the app - an open LAN or public
    // exposure, and the Agent Host - is torn down here rather than at daemon
    // shutdown.
    pub(super) fn release_for_desktop_exit(
        &self,
        id: Option<&Value>,
        client: &mpsc::SyncSender<String>,
    ) {
        self.send_direct(
            client,
            json!({
                "v": PROTOCOL_VERSION,
                "event": "ack",
                "cmd": "desktop.release",
                "id": id,
            }),
        );
        // Both teardowns run regardless of the other's outcome, and both
        // reasons are reported: a failure to close a public tunnel must never
        // be hidden by a failure to stop the Agent Host, or the reverse.
        let mut failures: Vec<String> = Vec::new();
        // The Agent Host runs while the app is open. Quitting is not the user
        // turning it off, so stop the process but leave the preference alone.
        if let Err(error) = self.agent_host.suspend() {
            failures.push(format!("could not stop the Agent Host: {error}"));
        }
        if let Some(sharing) = self.sharing.as_ref() {
            // Under the lifecycle, because closing the exposure restarts the
            // backend and frontend to put their origins back -- and `stop_all`
            // runs whether or not a start is in progress. Quitting during
            // startup therefore killed the very processes that start was
            // health-gating, and the user who had just asked to quit was shown
            // "process exited" for their trouble.
            if let Some(result) = crate::lifecycle::guarded(&self.lifecycle, || {
                self.disable_sharing_transaction(sharing)
            }) {
                if let Err(error) = result {
                    // Full Desktop exit must close the exposure even if
                    // restoring the app origin failed. It is safer to leave
                    // the local stack stopped or misconfigured than to leave a
                    // public tunnel alive.
                    sharing.force_disable();
                    failures.push(format!("could not stop sharing: {error}"));
                }
            } else {
                // Something else owns the lifecycle. Waiting for it would hold
                // the exposure open for as long as that takes, so close the
                // tunnel on its own and say the origins were not restored --
                // the app is exiting, and this is the half that must not
                // outlive it.
                sharing.force_disable();
                failures.push(
                    "closed sharing without restoring local origins, because another \
                     local operation was running"
                        .to_owned(),
                );
            }
        }
        let failure = (!failures.is_empty()).then(|| failures.join("; "));
        match failure {
            None => self.send_direct(
                client,
                json!({
                    "v": PROTOCOL_VERSION,
                    "event": "done",
                    "cmd": "desktop.release",
                    "id": id,
                    "ok": true,
                }),
            ),
            Some(error) => self.send_direct(
                client,
                error_event(
                    "desktop-release-failed",
                    format!("could not release local services before desktop exit: {error}"),
                    id,
                ),
            ),
        }
    }

    /// Run an Agent Host lifecycle or pairing command off the client thread.
    ///
    /// `pair` and `unpair` reach the backend, so they can take seconds. The
    /// caller gets an immediate ack and the outcome as a later `done`/`error`,
    /// the same shape every other slow locald operation uses.
    /// Run an Agent Host lifecycle or pairing command off the client thread.
    ///
    /// `pair` and `unpair` reach the backend, so they can take seconds. The
    /// caller gets an immediate ack and the outcome as a later `done`/`error`,
    /// the same shape every other slow locald operation uses.
    pub(super) fn start_agent_host_operation(
        self: &Arc<Self>,
        command: String,
        request: Value,
        client: mpsc::SyncSender<String>,
    ) {
        let id = request.get("id").cloned();
        if self.agent_lifecycle.begin().is_err() {
            self.send_direct(
                &client,
                error_event("busy", "Agent Host is busy or stopping", id.as_ref()),
            );
            return;
        }
        self.send_direct(
            &client,
            json!({
                "v": PROTOCOL_VERSION,
                "event": "ack",
                "cmd": command,
                "id": id,
            }),
        );
        let daemon = Arc::clone(self);
        thread::spawn(move || {
            let text = |key: &str| {
                request
                    .get(key)
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_string()
            };
            let result = match command.as_str() {
                "agent-host.start" => daemon.agent_host.start(),
                "agent-host.stop" => daemon.agent_host.stop(),
                "agent-host.restart" => daemon.agent_host.restart(),
                "agent-host.pair" => {
                    daemon
                        .agent_host
                        .pair(&text("url"), &text("pairing_code"), &text("name"))
                }
                "agent-host.unpair" => {
                    let target = text("target_id");
                    daemon
                        .agent_host
                        .unpair((!target.is_empty()).then_some(target.as_str()))
                }
                _ => daemon.agent_host.refresh(),
            };
            let outcome = result.map(|()| daemon.agent_host.detailed_status());
            daemon.agent_lifecycle.finish();
            match outcome {
                Ok(status) => {
                    daemon.send_direct(
                        &client,
                        json!({
                            "v": PROTOCOL_VERSION,
                            "event": "done",
                            "cmd": command,
                            "id": id,
                            "ok": true,
                            "agent_host": status.clone(),
                        }),
                    );
                    // Every open surface - Local settings, the tray, the
                    // workspace page - shows this state, and only one of them
                    // asked for the change.
                    daemon.broadcast(json!({
                        "v": PROTOCOL_VERSION,
                        "event": "agent-host.status",
                        "agent_host": status,
                    }));
                }
                Err(error) => daemon.send_direct(
                    &client,
                    error_event(
                        "agent-host-operation-failed",
                        error.to_string(),
                        id.as_ref(),
                    ),
                ),
            }
        });
    }
}
