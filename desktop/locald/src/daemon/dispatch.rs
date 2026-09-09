use super::*;

impl Daemon {
    pub(super) fn dispatch(
        self: &Arc<Self>,
        request: Value,
        client: &mpsc::SyncSender<String>,
    ) -> bool {
        let command = request
            .get("cmd")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_owned();
        let id = request.get("id").cloned();
        if command == "shutdown-daemon" {
            self.start_daemon_shutdown(id, client.clone());
            return true;
        }
        if self.lifecycle.checkpoint().is_err()
            && !matches!(
                command.as_str(),
                "ping"
                    | "status"
                    | "control.snapshot"
                    | "sharing.snapshot"
                    | "agent-host.status"
                    | "disconnect"
            )
        {
            self.send_direct(
                client,
                error_event(
                    "stopping",
                    "Lemma is stopping; new work is not accepted",
                    id.as_ref(),
                ),
            );
            return true;
        }
        match command.as_str() {
            "runtime.prepare" => {
                self.start_runtime_prepare(request, client.clone());
                return true;
            }
            "local.reset-data" => {
                self.start_local_data_reset(request, client.clone());
                return true;
            }
            "control.snapshot" => {
                match self.control_snapshot(id.as_ref()) {
                    Ok(event) => self.send_direct(client, event),
                    Err(error) => self.send_direct(
                        client,
                        error_event("control-snapshot-failed", error.to_string(), id.as_ref()),
                    ),
                }
                return true;
            }
            "sharing.snapshot" => {
                match self.sharing.as_ref() {
                    Some(sharing) => self.send_direct(
                        client,
                        json!({
                            "v": PROTOCOL_VERSION,
                            "event": "sharing.snapshot",
                            "id": id.as_ref(),
                            "sharing": sharing.snapshot(true),
                        }),
                    ),
                    None => self.send_direct(
                        client,
                        error_event(
                            "sharing-unavailable",
                            "sharing requires the managed local desktop runtime",
                            id.as_ref(),
                        ),
                    ),
                }
                return true;
            }
            "sharing.preflight" => {
                self.sharing_preflight(request, client);
                return true;
            }
            "sharing.enable" => {
                self.start_sharing_enable(request, client.clone());
                return true;
            }
            "sharing.disable" => {
                self.start_sharing_disable(id, client.clone());
                return true;
            }
            "config.apply" => {
                self.apply_operator_config(request, client);
                return true;
            }
            "config.discover-models" => {
                self.discover_provider_models(request, client);
                return true;
            }
            "config.set-ai" => {
                self.set_ai_profile(request, client);
                return true;
            }
            "desktop.release" => {
                self.release_for_desktop_exit(id.as_ref(), client);
                return true;
            }
            "agent-host.status" => {
                self.send_direct(
                    client,
                    json!({
                        "v": PROTOCOL_VERSION,
                        "event": "agent-host.status",
                        "id": id.as_ref(),
                        "agent_host": self.agent_host.detailed_status(),
                    }),
                );
                return true;
            }
            "agent-host.start" | "agent-host.stop" | "agent-host.restart" | "agent-host.pair"
            | "agent-host.unpair" | "agent-host.refresh" => {
                self.start_agent_host_operation(command, request.clone(), client.clone());
                return true;
            }
            _ => {}
        }
        if let Some(manager) = self.host_processes.as_ref() {
            match command.as_str() {
                "status" => {
                    let mut event = manager.status_event(id.as_ref());
                    let state = self.state.lock().expect("state lock poisoned");
                    event["url"] = Value::String(state.url.clone());
                    event["api_url"] = Value::String(state.api_url.clone());
                    if let Some(runtime) = self.managed_runtime.as_ref() {
                        event["managed_runtime"] =
                            serde_json::to_value(runtime.status()).unwrap_or(Value::Null);
                    }
                    event["agent_host"] = self.agent_host.status();
                    self.send_direct(client, event);
                    return true;
                }
                "start" | "stop" | "restart" => {
                    self.start_host_operation(command, request, client.clone());
                    return true;
                }
                _ => {}
            }
        }
        match command.as_str() {
            "ping" => self.send_direct(
                client,
                json!({"v": PROTOCOL_VERSION, "event": "pong", "id": id.as_ref()}),
            ),
            "status" if !self.supervisor_running() => {
                let mut event = self
                    .state
                    .lock()
                    .expect("state lock poisoned")
                    .event(id.as_ref());
                event["agent_host"] = self.agent_host.status();
                self.send_direct(client, event);
            }
            "start" | "stop" | "restart" | "status" => {
                if let Err(error) = self.send_to_supervisor(request) {
                    self.send_direct(
                        client,
                        error_event("supervisor-unavailable", error.to_string(), id.as_ref()),
                    );
                }
            }
            "hello" => self.send_direct(
                client,
                error_event(
                    "already-authenticated",
                    "connection is already authenticated",
                    id.as_ref(),
                ),
            ),
            "disconnect" => {
                self.send_direct(
                    client,
                    json!({"v": PROTOCOL_VERSION, "event": "bye", "id": id.as_ref()}),
                );
                return false;
            }
            _ => self.send_direct(
                client,
                error_event(
                    "unknown-command",
                    format!("unknown command {command:?}"),
                    id.as_ref(),
                ),
            ),
        }
        true
    }

    // Desktop calls this on full quit. The daemon deliberately outlives the
    // app, so anything that must not survive the app - an open LAN or public
    // exposure, and the Agent Host - is torn down here rather than at daemon
    // shutdown.
}

pub(super) fn runtime_operation_error_code(message: &str, fallback: &'static str) -> &'static str {
    if message.contains("Linux guest kernel crashed") {
        "guest-kernel-failed"
    } else if message.contains("restart to finish enabling WSL 2") {
        "wsl-reboot-required"
    } else if message.contains("WSL 2 is required") {
        "wsl-required"
    } else if message.contains("did not approve or complete WSL 2 setup") {
        "wsl-setup-denied"
    } else if message.contains(crate::paths::DATA_RESET_MARKER) {
        // One phrase, one code, however many detectors raise it. Anything the
        // user cannot fix by retrying but can fix by discarding local data says
        // the marker phrase and lands here.
        "local-data-incompatible"
    } else {
        fallback
    }
}

pub(super) fn error_diagnostic_source(message: &str) -> (&'static str, &'static str) {
    let message = message.to_ascii_lowercase();
    if message.contains("guest kernel") {
        ("infrastructure", "infrastructure")
    } else if message.contains("migration") || message.contains("alembic") {
        ("migrations", "migrations")
    } else if message.contains("frontend") || message.contains("eaddrinuse") {
        ("frontend", "frontend")
    } else if message.contains("backend") || message.contains("health gate") {
        ("backend", "backend")
    } else if message.contains("runtime")
        || message.contains("container")
        || message.contains("registry")
        || message.contains("guest")
    {
        ("infrastructure", "infrastructure")
    } else {
        ("locald", "events")
    }
}
