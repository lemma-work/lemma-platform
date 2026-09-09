use super::*;

impl Daemon {
    pub(super) fn restore_sharing_after_desktop_disconnect(self: &Arc<Self>) {
        let Some(sharing) = self.sharing.as_ref() else {
            return;
        };
        if sharing.active_mode() == SharingMode::ThisComputer {
            return;
        }
        if self.lifecycle.begin().is_err() {
            return;
        }
        let daemon = Arc::clone(self);
        let sharing = Arc::clone(sharing);
        thread::spawn(move || {
            let result = daemon.disable_sharing_transaction(&sharing);
            match result {
                Ok(()) => {
                    let (url, api_url) = daemon.canonical_urls();
                    daemon.broadcast(json!({
                        "v": PROTOCOL_VERSION,
                        "event": "sharing.changed",
                        "reason": "desktop-disconnected",
                        "url": url,
                        "api_url": api_url,
                        "sharing": sharing.snapshot(true),
                    }))
                }
                Err(error) => daemon.broadcast(scoped_error_event(
                    "sharing",
                    "sharing-disconnect-cleanup-failed",
                    format!("Desktop disconnected and sharing cleanup failed: {error}"),
                    None,
                )),
            }
            daemon.lifecycle.finish();
        });
    }

    pub(super) fn control_snapshot(&self, id: Option<&Value>) -> io::Result<Value> {
        let mut event = json!({
            "v": PROTOCOL_VERSION,
            "event": "control.snapshot",
            "operator": self.operator_config.snapshot()?,
            "config_operations": self.config_operations.as_ref().map(ConfigOperations::snapshot),
            "state": self.state.lock().expect("state lock poisoned").event(None),
            "services": self.host_processes.as_ref().map(|manager| manager.status()),
            "capabilities": self.host_processes.as_ref().and_then(|manager| manager.capabilities()),
            "release": self.host_processes.as_ref().map(|manager| manager.release()),
            "managed_runtime": self.managed_runtime.as_ref().and_then(|runtime| runtime.status()),
            "sharing": self.sharing.as_ref().map(|sharing| sharing.snapshot(true)),
            "agent_host": self.agent_host.detailed_status(),
            "paths": {
                "locald": &self.paths.root,
                "logs": self.paths.root.join("logs"),
            },
        });
        if let Some(id) = id {
            event["id"] = id.clone();
        }
        Ok(event)
    }

    pub(super) fn sharing_preflight(&self, request: Value, client: &mpsc::SyncSender<String>) {
        let id = request.get("id");
        let provider = match request
            .get("provider")
            .cloned()
            .map(serde_json::from_value::<TunnelProvider>)
            .transpose()
        {
            Ok(provider) => provider,
            Err(error) => {
                self.send_direct(
                    client,
                    error_event("bad-input", format!("unknown tunnel provider: {error}"), id),
                );
                return;
            }
        };
        match self.sharing.as_ref() {
            Some(sharing) => self.send_direct(
                client,
                json!({
                    "v": PROTOCOL_VERSION,
                    "event": "sharing.preflight",
                    "id": id,
                    "preflight": sharing.preflight(provider),
                    "sharing": sharing.snapshot(false),
                }),
            ),
            None => self.send_direct(
                client,
                error_event(
                    "sharing-unavailable",
                    "sharing requires the managed local desktop runtime",
                    id,
                ),
            ),
        }
    }

    pub(super) fn start_sharing_enable(
        self: &Arc<Self>,
        request: Value,
        client: mpsc::SyncSender<String>,
    ) {
        let id = request.get("id").cloned();
        let Some(sharing) = self.sharing.as_ref().cloned() else {
            self.send_direct(
                &client,
                error_event(
                    "sharing-unavailable",
                    "sharing requires the managed local desktop runtime",
                    id.as_ref(),
                ),
            );
            return;
        };
        let payload = request.get("payload").cloned().unwrap_or(Value::Null);
        let enable: EnableSharingRequest = match serde_json::from_value(payload) {
            Ok(enable) => enable,
            Err(error) => {
                self.send_direct(
                    &client,
                    error_event(
                        "bad-input",
                        format!("invalid sharing request: {error}"),
                        id.as_ref(),
                    ),
                );
                return;
            }
        };
        let stack_ready = {
            let state = self.state.lock().expect("state lock poisoned");
            state.ready && state.running
        };
        if !stack_ready {
            self.send_direct(
                &client,
                error_event(
                    "sharing-stack-not-ready",
                    "Start Lemma and wait until the local stack is healthy before enabling sharing.",
                    id.as_ref(),
                ),
            );
            return;
        }
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
                "cmd": "sharing.enable",
                "id": id.as_ref(),
            }),
        );
        let daemon = Arc::clone(self);
        thread::spawn(move || {
            let (progress_stop, progress_receive) = mpsc::channel::<()>();
            let progress_daemon = Arc::clone(&daemon);
            let progress_sharing = Arc::clone(&sharing);
            let progress_id = id.clone();
            let progress_monitor = thread::spawn(move || loop {
                match progress_receive.recv_timeout(std::time::Duration::from_millis(250)) {
                    Ok(()) | Err(mpsc::RecvTimeoutError::Disconnected) => break,
                    Err(mpsc::RecvTimeoutError::Timeout) => {
                        progress_daemon.broadcast(json!({
                            "v": PROTOCOL_VERSION,
                            "event": "sharing.progress",
                            "id": progress_id.as_ref(),
                            "sharing": progress_sharing.snapshot(false),
                        }));
                    }
                }
            });
            let result = daemon.enable_sharing_transaction(&sharing, &enable);
            let _ = progress_stop.send(());
            let _ = progress_monitor.join();
            match result {
                Ok(()) => {
                    let (url, api_url) = daemon.canonical_urls();
                    daemon.broadcast(json!({
                        "v": PROTOCOL_VERSION,
                        "event": "sharing.changed",
                        "id": id.as_ref(),
                        "ok": true,
                        "url": url,
                        "api_url": api_url,
                        "sharing": sharing.snapshot(true),
                    }))
                }
                Err(error) => daemon.broadcast(scoped_error_event(
                    "sharing",
                    "sharing-enable-failed",
                    error.to_string(),
                    id.as_ref(),
                )),
            }
            daemon.lifecycle.finish();
        });
    }

    pub(super) fn enable_sharing_transaction(
        &self,
        sharing: &SharingController,
        request: &EnableSharingRequest,
    ) -> io::Result<()> {
        let manager = self
            .host_processes
            .as_ref()
            .ok_or_else(|| io::Error::other("host process manager is unavailable"))?;
        let prepared = sharing.prepare_enable(request)?;
        let previous_backend = manager.service_environment("backend");
        let previous_frontend = manager.service_environment("frontend");
        let (backend, frontend) = sharing_environment(&prepared.origin, prepared.mode);
        manager.replace_service_environment("backend", backend);
        manager.replace_service_environment("frontend", frontend);

        let activate = manager
            .restart_all()
            .and_then(|_| validate_canonical_origin(&prepared.origin));
        if let Err(error) = activate {
            manager.replace_service_environment("backend", previous_backend);
            manager.replace_service_environment("frontend", previous_frontend);
            let rollback = manager.restart_all();
            sharing.rollback_enable(error.to_string());
            self.restore_local_canonical_state()?;
            return match rollback {
                Ok(()) => Err(io::Error::other(format!(
                    "sharing could not be activated and was rolled back: {error}"
                ))),
                Err(rollback_error) => Err(io::Error::other(format!(
                    "sharing could not be activated: {error}; rollback also failed: {rollback_error}"
                ))),
            };
        }

        {
            let mut state = self.state.lock().expect("state lock poisoned");
            state.url = prepared.origin.clone();
            state.api_url = format!("{}/_lemma/api", prepared.origin.trim_end_matches('/'));
            state.persist(&self.paths.state)?;
        }
        if let Err(error) = sharing.commit_enable(request) {
            manager.replace_service_environment("backend", previous_backend);
            manager.replace_service_environment("frontend", previous_frontend);
            let rollback = manager.restart_all();
            sharing.rollback_enable(error.to_string());
            self.restore_local_canonical_state()?;
            return match rollback {
                Ok(()) => Err(io::Error::other(format!(
                    "sharing preferences could not be saved and activation was rolled back: {error}"
                ))),
                Err(rollback_error) => Err(io::Error::other(format!(
                    "sharing preferences could not be saved: {error}; rollback also failed: {rollback_error}"
                ))),
            };
        }
        Ok(())
    }

    pub(super) fn start_sharing_disable(
        self: &Arc<Self>,
        id: Option<Value>,
        client: mpsc::SyncSender<String>,
    ) {
        let Some(sharing) = self.sharing.as_ref().cloned() else {
            self.send_direct(
                &client,
                error_event(
                    "sharing-unavailable",
                    "sharing requires the managed local desktop runtime",
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
                "cmd": "sharing.disable",
                "id": id.as_ref(),
            }),
        );
        let daemon = Arc::clone(self);
        thread::spawn(move || {
            let result = daemon.disable_sharing_transaction(&sharing);
            match result {
                Ok(()) => {
                    let (url, api_url) = daemon.canonical_urls();
                    daemon.broadcast(json!({
                        "v": PROTOCOL_VERSION,
                        "event": "sharing.changed",
                        "id": id.as_ref(),
                        "ok": true,
                        "url": url,
                        "api_url": api_url,
                        "sharing": sharing.snapshot(true),
                    }))
                }
                Err(error) => daemon.broadcast(scoped_error_event(
                    "sharing",
                    "sharing-disable-failed",
                    error.to_string(),
                    id.as_ref(),
                )),
            }
            daemon.lifecycle.finish();
        });
    }

    pub(super) fn disable_sharing_transaction(
        &self,
        sharing: &SharingController,
    ) -> io::Result<()> {
        if !sharing.begin_disable()? {
            self.restore_local_canonical_state()?;
            return Ok(());
        }
        let manager = self
            .host_processes
            .as_ref()
            .ok_or_else(|| io::Error::other("host process manager is unavailable"))?;
        let previous_backend = manager.replace_service_environment("backend", HashMap::new());
        let previous_frontend = manager.replace_service_environment("frontend", HashMap::new());
        if let Err(error) = manager.restart_all() {
            manager.replace_service_environment("backend", previous_backend);
            manager.replace_service_environment("frontend", previous_frontend);
            let rollback = manager.restart_all();
            sharing.abort_disable(error.to_string());
            return match rollback {
                Ok(()) => Err(io::Error::other(format!(
                    "This computer mode could not be restored; sharing remains active: {error}"
                ))),
                Err(rollback_error) => Err(io::Error::other(format!(
                    "This computer mode could not be restored: {error}; shared-origin rollback also failed: {rollback_error}"
                ))),
            };
        }
        self.restore_local_canonical_state()?;
        sharing.commit_disable();
        Ok(())
    }

    pub(super) fn restore_local_canonical_state(&self) -> io::Result<()> {
        let Some(sharing) = self.sharing.as_ref() else {
            return Ok(());
        };
        let mut state = self.state.lock().expect("state lock poisoned");
        state.url = sharing.local_origin().to_owned();
        state.api_url = self
            .host_processes
            .as_ref()
            .and_then(|manager| manager.application_ports())
            .map(|(_, backend_port)| {
                format!(
                    "http://{}:{backend_port}",
                    crate::local_domain::LocalDomain::from_env().frontend_host()
                )
            })
            .unwrap_or_else(|| state.api_url.clone());
        state.persist(&self.paths.state)
    }

    pub(super) fn canonical_urls(&self) -> (String, String) {
        let state = self.state.lock().expect("state lock poisoned");
        (state.url.clone(), state.api_url.clone())
    }
}
