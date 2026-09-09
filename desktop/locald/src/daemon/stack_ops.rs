use super::*;

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
            daemon.broadcast(json!({
                "v": PROTOCOL_VERSION, "event": "phase", "key": "stopping",
                "label": "Stopping Lemma", "progress": 0,
                "detail": "Waiting for the current operation to reach a safe stopping point",
                "operation_id": id.as_ref(),
            }));
            daemon.lifecycle.wait_idle();
            daemon.agent_lifecycle.wait_idle();
            let mut failure = None;
            if let Some(sharing) = daemon.sharing.as_ref() {
                sharing.force_disable();
            }
            if let Some(manager) = daemon.host_processes.as_ref() {
                if let Err(error) = manager.stop_all() {
                    failure = Some(error.to_string());
                }
            }
            if let Err(error) = daemon.agent_host.suspend() {
                failure.get_or_insert_with(|| error.to_string());
            }
            if let Some(runtime) = daemon.managed_runtime.as_ref() {
                if let Err(error) = runtime.shutdown() {
                    failure.get_or_insert_with(|| error.to_string());
                }
            }
            if let Some(mut supervisor) = daemon
                .supervisor
                .lock()
                .expect("supervisor lock poisoned")
                .take()
            {
                let _ = supervisor.child.kill();
                let _ = supervisor.child.wait();
            }
            if let Some(message) = failure {
                daemon.shutdown_running.store(false, Ordering::Release);
                daemon.send_direct(
                    &client,
                    error_event("shutdown-failed", message, id.as_ref()),
                );
                return;
            }
            daemon.broadcast(json!({
                "v": PROTOCOL_VERSION, "event": "state", "status": "stopped",
                "running": false, "ready": false, "operation_id": id.as_ref(),
            }));
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

    pub(super) fn start_host_operation(
        self: &Arc<Self>,
        command: String,
        request: Value,
        client: mpsc::SyncSender<String>,
    ) {
        let id = request.get("id").cloned();
        if self.lifecycle.begin().is_err() {
            self.send_direct(
                &client,
                error_event(
                    "busy",
                    "Lemma is already working on a local operation; its progress will continue in this client",
                    id.as_ref(),
                ),
            );
            return;
        }
        self.send_direct(
            &client,
            json!({"v": PROTOCOL_VERSION, "event": "ack", "cmd": command, "id": id.as_ref()}),
        );

        let daemon = Arc::clone(self);
        thread::spawn(move || {
            let manager = daemon
                .host_processes
                .as_ref()
                .expect("host operation requires manager");
            let result = match command.as_str() {
                "start" => daemon.start_host_packs(manager, id.as_ref()),
                "stop" => {
                    let result = manager.stop_all();
                    if result.is_ok() && request.get("infra").and_then(Value::as_bool) == Some(true)
                    {
                        daemon.stop_private_infra()
                    } else {
                        result
                    }
                }
                "restart" => manager
                    .stop_all()
                    .and_then(|_| daemon.start_host_packs(manager, id.as_ref())),
                _ => unreachable!(),
            };

            match result {
                Ok(()) => {
                    let stopped_infra = request
                        .get("infra")
                        .and_then(Value::as_bool)
                        .unwrap_or(false);
                    if command == "stop" {
                        daemon.broadcast(json!({
                            "v": PROTOCOL_VERSION, "event": "state", "status": "stopped",
                            "running": false, "ready": false,
                            "operation_id": id.as_ref(),
                        }));
                        daemon.broadcast(json!({
                            "v": PROTOCOL_VERSION, "event": "stopped",
                            "infra": stopped_infra,
                            "operation_id": id.as_ref(),
                        }));
                    }
                    daemon.broadcast(json!({
                        "v": PROTOCOL_VERSION, "event": "done", "cmd": command,
                        "id": id.as_ref(), "ok": true,
                    }));
                }
                Err(error) => {
                    let message = error.to_string();
                    let mut event = error_event(
                        runtime_operation_error_code(&message, "host-operation-failed"),
                        message.clone(),
                        id.as_ref(),
                    );
                    event["operation_id"] = id.clone().unwrap_or(Value::Null);
                    let (component, log_source) = error_diagnostic_source(&message);
                    event["component"] = Value::String(component.into());
                    event["log_source"] = Value::String(log_source.into());
                    daemon.broadcast(event);
                    daemon.broadcast(json!({
                        "v": PROTOCOL_VERSION, "event": "done", "cmd": command,
                        "id": id.as_ref(), "ok": false,
                    }));
                }
            }
            daemon.lifecycle.finish();
        });
    }

    pub(super) fn start_runtime_prepare(
        self: &Arc<Self>,
        request: Value,
        client: mpsc::SyncSender<String>,
    ) {
        let id = request.get("id").cloned();
        let Some(runtime) = self.managed_runtime.as_ref().cloned() else {
            self.send_direct(
                &client,
                error_event(
                    "managed-runtime-unavailable",
                    "this installation does not include an app-owned runtime",
                    id.as_ref(),
                ),
            );
            return;
        };
        if self.lifecycle.begin().is_err() {
            self.send_direct(
                &client,
                error_event("busy", "another local operation is running", id.as_ref()),
            );
            return;
        }
        self.send_direct(
            &client,
            json!({
                "v": PROTOCOL_VERSION,
                "event": "ack",
                "cmd": "runtime.prepare",
                "id": id.as_ref(),
            }),
        );

        let daemon = Arc::clone(self);
        thread::spawn(move || {
            match runtime.prepare_host() {
                Ok(result) => {
                    let mut prepared = json!({
                        "v": PROTOCOL_VERSION,
                        "event": "runtime.prepared",
                        "id": id.as_ref(),
                    });
                    if let (Some(target), Some(fields)) =
                        (prepared.as_object_mut(), result.as_object())
                    {
                        target.extend(fields.clone());
                    }
                    daemon.broadcast(prepared);
                    daemon.broadcast(json!({
                        "v": PROTOCOL_VERSION,
                        "event": "done",
                        "cmd": "runtime.prepare",
                        "id": id.as_ref(),
                        "ok": true,
                    }));
                }
                Err(error) => {
                    let message = error.to_string();
                    daemon.broadcast(error_event(
                        runtime_operation_error_code(&message, "runtime-prepare-failed"),
                        message,
                        id.as_ref(),
                    ));
                    daemon.broadcast(json!({
                        "v": PROTOCOL_VERSION,
                        "event": "done",
                        "cmd": "runtime.prepare",
                        "id": id.as_ref(),
                        "ok": false,
                    }));
                }
            }
            daemon.lifecycle.finish();
        });
    }

    /// Destroy everything on this computer that the user made, then start clean.
    ///
    /// A locald verb rather than something the shell does, because only locald
    /// owns the VM lifecycle -- and because the progress the splash already
    /// renders comes from here. It takes `lifecycle`, the same
    /// guard `start`, `stop`, `restart` and `runtime.prepare` take, so a reset
    /// can never interleave with a start.
    ///
    /// There is no rollback arm. `repair_runtime` can roll back because a
    /// runtime is replaceable; data is not, and by the time anything here can
    /// fail it is already gone. A failed restart afterwards therefore reports
    /// that plainly and leaves the full-reinstall option on screen, rather than
    /// retrying and pretending.
    pub(super) fn start_host_packs(
        self: &Arc<Self>,
        manager: &HostProcessManager,
        operation_id: Option<&Value>,
    ) -> io::Result<()> {
        self.lifecycle.checkpoint()?;
        // Refuse before touching the guest. Something has already replaced a
        // credential this installation's data was written under, so starting
        // would fail deep inside migrations as an opaque auth error, or come up
        // unable to decrypt its own rows. Say so here, in words the shell turns
        // into a reset button.
        if let Some(reason) = crate::paths::data_reset_reason(&self.paths.root) {
            return Err(io::Error::other(format!(
                "{reason}; {}",
                crate::paths::DATA_RESET_MARKER
            )));
        }
        let runtime_generation = manager.prepare_runtime_generation()?;
        self.prepare_private_infra(operation_id, &runtime_generation)?;
        self.lifecycle.checkpoint()?;
        manager.mark_dependency_ready();
        manager.set_backend_environment(self.backend_environment()?);
        self.lifecycle.checkpoint()?;
        manager.start_all_cancellable(|component| {
            let (label, progress, detail, log_source) = match component {
                "migrations" => (
                    "Checking workspace data",
                    68,
                    "applying database migrations and seeding connectors",
                    "migrations",
                ),
                "backend" => (
                    "Starting the Lemma backend",
                    78,
                    "starting API, workers, schedules, the sandbox runtime, and document processing",
                    "backend",
                ),
                "frontend" => (
                    "Starting the Lemma interface",
                    90,
                    "starting the local Next.js application",
                    "frontend",
                ),
                _ => ("Starting Lemma", 75, "starting a host component", "locald"),
            };
            self.broadcast(json!({
                "v": PROTOCOL_VERSION,
                "event": "phase",
                "key": component,
                "stage": component,
                "label": label,
                "progress": progress,
                "detail": detail,
                "current": 0,
                "total": 1,
                "bytes": false,
                "operation_id": operation_id,
                "runtime_generation": runtime_generation,
                "component": component,
                "log_source": log_source,
            }));
        }, || self.lifecycle.checkpoint()).or_else(|error| {
            if let Some(runtime) = self.managed_runtime.as_ref() {
                runtime.check_guest_kernel()?;
            }
            Err(error)
        })?;
        // The auth service was started before the backend and is only now
        // waited for, so it came up alongside it rather than in front of it.
        // Nothing may report ready until it answers: a workspace whose first
        // act is signing in would otherwise meet an auth service that is not
        // there yet, which is worse than the wait this removes.
        //
        // Its failure has to take the host processes with it. They are running
        // by this point -- that is the whole point -- and a stack with no auth
        // is not a stack anybody can use.
        if let Some(runtime) = self.managed_runtime.as_ref() {
            if let Err(error) = runtime.await_private_services() {
                if let Some(manager) = self.host_processes.as_ref() {
                    let _ = manager.stop_all();
                }
                return Err(error);
            }
        }
        self.lifecycle.checkpoint()?;
        let state = self.state.lock().expect("state lock poisoned").clone();
        self.broadcast(json!({
            "v": PROTOCOL_VERSION, "event": "phase", "key": "ready",
            "label": "Lemma is ready", "progress": 100, "detail": "",
            "operation_id": operation_id,
            "runtime_generation": runtime_generation,
        }));
        self.broadcast(json!({
            "v": PROTOCOL_VERSION, "event": "state", "status": "running",
            "running": true, "ready": true,
            "operation_id": operation_id,
            "runtime_generation": runtime_generation,
        }));
        self.broadcast(json!({
            "v": PROTOCOL_VERSION, "event": "ready", "url": state.url,
            "api_url": state.api_url,
            "mode": if self.managed_runtime.is_some() { "managed-local" } else { "host-packs" },
            "release": manager.release(),
            "capabilities": manager.capabilities(),
            "operation_id": operation_id,
            "runtime_generation": runtime_generation,
        }));
        self.warm_sandbox_images();
        Ok(())
    }

    /// Start fetching the sandbox images behind the workspace, and say so.
    ///
    /// After `ready`, never before it. The images are only needed once a pod
    /// runs something; fetching them inline held the startup bar at 68% behind
    /// several hundred megabytes on a first run. The app shows this as a
    /// notice it can take away again, rather than as a phase of starting.
    /// Start fetching the sandbox images behind the workspace, and say so.
    ///
    /// After `ready`, never before it. The images are only needed once a pod
    /// runs something; fetching them inline held the startup bar at 68% behind
    /// several hundred megabytes on a first run. The app shows this as a
    /// notice it can take away again, rather than as a phase of starting.
    pub(super) fn warm_sandbox_images(self: &Arc<Self>) {
        let Some(runtime) = self.managed_runtime.as_ref() else {
            // No guest to warm -- this is a supervisor-mode stack. Said out
            // loud, and terminally, because the workspace polls until it hears
            // an answer that cannot change; silence here left it asking every
            // two seconds for the rest of the session.
            self.broadcast(json!({
                "v": PROTOCOL_VERSION,
                "event": "sandbox-images",
                "state": SANDBOX_IMAGES_UNSUPPORTED,
                "detail": "",
            }));
            return;
        };
        let daemon = Arc::clone(self);
        runtime.warm_sandbox_images(move |status| {
            daemon.broadcast(json!({
                "v": PROTOCOL_VERSION,
                "event": "sandbox-images",
                "state": status.state,
                "detail": status.detail,
            }));
        });
    }

    pub(super) fn recover_managed_stack(self: &Arc<Self>) -> io::Result<()> {
        let runtime = self
            .managed_runtime
            .as_ref()
            .ok_or_else(|| io::Error::other("managed runtime is unavailable"))?;
        let manager = self
            .host_processes
            .as_ref()
            .ok_or_else(|| io::Error::other("host process manager is unavailable"))?;
        runtime.start()?;
        manager.set_backend_environment(self.backend_environment()?);
        manager.restart_all()?;
        manager.mark_dependency_ready();

        let state = self.state.lock().expect("state lock poisoned").clone();
        self.broadcast(json!({
            "v": PROTOCOL_VERSION,
            "event": "state",
            "status": "running",
            "running": true,
            "ready": true,
        }));
        self.broadcast(json!({
            "v": PROTOCOL_VERSION,
            "event": "ready",
            "url": state.url,
            "api_url": state.api_url,
            "mode": "managed-local",
            "release": manager.release(),
        }));
        self.warm_sandbox_images();
        Ok(())
    }

    pub(super) fn backend_environment(&self) -> io::Result<HashMap<String, String>> {
        let operator = self.operator_config.backend_environment()?;
        let infrastructure = self
            .managed_runtime
            .as_ref()
            .map(|runtime| runtime.backend_environment())
            .transpose()?;
        Ok(compose_backend_environment(operator, infrastructure))
    }

    pub(super) fn prepare_private_infra(
        self: &Arc<Self>,
        operation_id: Option<&Value>,
        runtime_generation: &str,
    ) -> io::Result<()> {
        if let Some(runtime) = self.managed_runtime.as_ref() {
            return runtime.start_cancellable(
                |component, label, progress, detail| {
                    self.broadcast(json!({
                        "v": PROTOCOL_VERSION,
                        "event": "phase",
                        "key": component,
                        "stage": component,
                        "label": label,
                        "progress": progress,
                        "detail": detail,
                        "current": 0,
                        "total": 1,
                        "bytes": false,
                        "operation_id": operation_id,
                        "runtime_generation": runtime_generation,
                        "component": component,
                        "log_source": if component == "vm" { "vm" } else { "guest" },
                    }));
                },
                || self.lifecycle.checkpoint(),
            );
        }
        self.wait_for_supervisor(json!({
            "cmd": "start", "setup": false, "rebuild": false, "infra_only": true,
        }))
    }

    pub(super) fn stop_private_infra(self: &Arc<Self>) -> io::Result<()> {
        if let Some(runtime) = self.managed_runtime.as_ref() {
            return runtime.stop_infrastructure();
        }
        self.wait_for_supervisor(json!({"cmd": "stop", "infra": true}))
    }
}
