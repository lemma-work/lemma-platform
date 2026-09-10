use super::*;

impl Daemon {
    pub(super) fn apply_operator_config(
        self: &Arc<Self>,
        request: Value,
        client: &mpsc::SyncSender<String>,
    ) {
        let id = request.get("id").cloned();
        if self.lifecycle.begin().is_err() {
            self.send_direct(
                client,
                error_event("busy", "another local operation is running", id.as_ref()),
            );
            return;
        }
        let payload = request.get("payload").cloned().unwrap_or(Value::Null);
        let apply: OperatorConfigUpdate = match serde_json::from_value(payload) {
            Ok(apply) => apply,
            Err(error) => {
                self.lifecycle.finish();
                self.send_direct(
                    client,
                    error_event(
                        "bad-input",
                        format!("invalid config patch: {error}"),
                        id.as_ref(),
                    ),
                );
                return;
            }
        };
        if let Err(error) = self.begin_config_operation(id.as_ref()) {
            self.lifecycle.finish();
            self.send_direct(
                client,
                error_event(
                    "config-operation-unavailable",
                    error.to_string(),
                    id.as_ref(),
                ),
            );
            return;
        }
        self.send_direct(
            client,
            json!({"v": PROTOCOL_VERSION, "event":"ack", "cmd":"config.apply", "id": id.as_ref()}),
        );
        let daemon = Arc::clone(self);
        thread::spawn(move || {
            let result = daemon.write_operator_config(|store| store.update(apply));
            daemon.finish_config_write(result, id.as_ref());
        });
    }

    /// Persist an operator-config change and restart the backend behind it.
    ///
    /// The write itself differs by caller — a whole configuration from the
    /// settings page, one section from onboarding — but everything around it
    /// is the same and is the part that is easy to get wrong: capture the
    /// previous state, re-render the backend environment, restart, and put the
    /// old configuration back if the restart does not come up.
    /// Persist an operator-config change and restart the backend behind it.
    ///
    /// The write itself differs by caller — a whole configuration from the
    /// settings page, one section from onboarding — but everything around it
    /// is the same and is the part that is easy to get wrong: capture the
    /// previous state, re-render the backend environment, restart, and put the
    /// old configuration back if the restart does not come up.
    pub(super) fn write_operator_config(
        self: &Arc<Self>,
        write: impl FnOnce(&OperatorConfigStore) -> io::Result<Value>,
    ) -> io::Result<Value> {
        let daemon = self;
        {
            let previous = daemon.operator_config.capture_state();
            let backend_restart_available = daemon
                .host_processes
                .as_ref()
                .is_some_and(|manager| manager.backend_restart_available());
            previous.and_then(|previous| {
                let snapshot = write(daemon.operator_config.as_ref())?;
                let activate: io::Result<Value> = (|| {
                    if let Some(manager) = daemon.host_processes.as_ref() {
                        if backend_restart_available {
                            manager.set_backend_environment(daemon.backend_environment()?);
                            manager.restart_backend()?;
                        }
                    }
                    Ok(snapshot)
                })();
                match activate {
                    Ok(snapshot) => Ok(snapshot),
                    Err(error) => {
                        let rollback = daemon.operator_config.restore_state(previous).and_then(|_| {
                            if let Some(manager) = daemon.host_processes.as_ref() {
                                if backend_restart_available {
                                    manager.set_backend_environment(daemon.backend_environment()?);
                                    manager.restart_backend()?;
                                }
                            }
                            Ok(())
                        });
                        match rollback {
                            Ok(()) => Err(io::Error::other(format!(
                                "new configuration could not be activated and was rolled back: {error}"
                            ))),
                            Err(rollback_error) => Err(io::Error::other(format!(
                                "new configuration could not be activated: {error}; rollback also failed: {rollback_error}"
                            ))),
                        }
                    }
                }
            })
        }
    }

    /// Announce the outcome of an operator-config write and release the guard.
    /// Announce the outcome of an operator-config write and release the guard.
    pub(super) fn finish_config_write(
        self: &Arc<Self>,
        result: io::Result<Value>,
        id: Option<&Value>,
    ) {
        let code = if result
            .as_ref()
            .is_err_and(|error| error.kind() == io::ErrorKind::AlreadyExists)
        {
            "config-conflict"
        } else {
            "config-apply-failed"
        };
        let outcome = match &result {
            Ok(operator) => ConfigOperation::Succeeded {
                operator: operator.clone(),
            },
            Err(error) => ConfigOperation::Failed {
                code: code.into(),
                message: error.to_string(),
            },
        };
        if let (Some(journal), Some(id)) = (&self.config_operations, id.and_then(Value::as_str)) {
            if let Err(error) = journal.finish(id, outcome) {
                self.broadcast(error_event("config-outcome-unknown", format!("settings may have been applied, but completion could not be recorded: {error}; review settings before retrying"), Some(&json!(id))));
                self.lifecycle.finish();
                return;
            }
        }
        // A completion can immediately trigger the next section's save.
        self.lifecycle.finish();
        match result {
            Ok(snapshot) => self.broadcast(json!({
                "v": PROTOCOL_VERSION,
                "event": "config.applied",
                "id": id,
                "operator": snapshot,
                "restart": "backend",
            })),
            Err(error) => self.broadcast(error_event(code, error.to_string(), id)),
        }
    }

    pub(super) fn begin_config_operation(&self, id: Option<&Value>) -> io::Result<()> {
        let id = id.and_then(Value::as_str).ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::InvalidInput,
                "settings write needs a string operation id",
            )
        })?;
        self.config_operations
            .as_ref()
            .ok_or_else(|| {
                io::Error::other(
                    "settings operation history is unavailable; repair it before saving",
                )
            })?
            .begin(id)
    }

    /// Change only the AI profile.
    ///
    /// Onboarding runs in the workspace, on a remote origin, and is trusted
    /// with this one section and nothing else — not sharing, not tunnels, not
    /// the runtime. Keeping that narrow is the reason this is its own command
    /// rather than a `config.apply` with the rest of the configuration echoed
    /// back by the caller.
    /// Change only the AI profile.
    ///
    /// Onboarding runs in the workspace, on a remote origin, and is trusted
    /// with this one section and nothing else — not sharing, not tunnels, not
    /// the runtime. Keeping that narrow is the reason this is its own command
    /// rather than a `config.apply` with the rest of the configuration echoed
    /// back by the caller.
    pub(super) fn set_ai_profile(
        self: &Arc<Self>,
        request: Value,
        client: &mpsc::SyncSender<String>,
    ) {
        let id = request.get("id").cloned();
        if self.lifecycle.begin().is_err() {
            self.send_direct(
                client,
                error_event("busy", "another local operation is running", id.as_ref()),
            );
            return;
        }
        let payload = request.get("payload").cloned().unwrap_or(Value::Null);
        if let Err(error) = self.begin_config_operation(id.as_ref()) {
            self.lifecycle.finish();
            self.send_direct(
                client,
                error_event(
                    "config-operation-unavailable",
                    error.to_string(),
                    id.as_ref(),
                ),
            );
            return;
        }
        self.send_direct(
            client,
            json!({"v": PROTOCOL_VERSION, "event":"ack", "cmd":"config.set-ai", "id": id.as_ref()}),
        );
        let daemon = Arc::clone(self);
        thread::spawn(move || {
            let result = daemon.write_operator_config(|store| store.set_ai(payload));
            daemon.finish_config_write(result, id.as_ref());
        });
    }

    /// Ask a provider what it can run, without committing to anything.
    ///
    /// `config.apply` already probes, but it probes as one step of a write that
    /// restarts the backend — so the only way to find out a provider's model
    /// names was to guess one, apply, and read the error. That is why both the
    /// onboarding step and Local settings asked people to type model ids from
    /// memory. This is the same probe with no write behind it: connect, list,
    /// then let the user pick before anything is saved.
    ///
    /// Deliberately not guarded by `lifecycle` — it mutates
    /// nothing, and making a read-only lookup wait behind an unrelated start is
    /// how a model picker ends up feeling broken.
    /// Ask a provider what it can run, without committing to anything.
    ///
    /// `config.apply` already probes, but it probes as one step of a write that
    /// restarts the backend — so the only way to find out a provider's model
    /// names was to guess one, apply, and read the error. That is why both the
    /// onboarding step and Local settings asked people to type model ids from
    /// memory. This is the same probe with no write behind it: connect, list,
    /// then let the user pick before anything is saved.
    ///
    /// Deliberately not guarded by `lifecycle` — it mutates
    /// nothing, and making a read-only lookup wait behind an unrelated start is
    /// how a model picker ends up feeling broken.
    pub(super) fn discover_provider_models(
        self: &Arc<Self>,
        request: Value,
        client: &mpsc::SyncSender<String>,
    ) {
        let id = request.get("id").cloned();
        let payload = request.get("payload").cloned().unwrap_or(Value::Null);
        let daemon = Arc::clone(self);
        let client = client.clone();
        thread::spawn(move || {
            match daemon.operator_config.discover_models(payload) {
                Ok(models) => daemon.send_direct(
                    &client,
                    json!({
                        "v": PROTOCOL_VERSION,
                        "event": "config.models",
                        "id": id.as_ref(),
                        "models": models,
                    }),
                ),
                Err(error) => daemon.send_direct(
                    &client,
                    error_event("config-discover-failed", error.to_string(), id.as_ref()),
                ),
            };
        });
    }
}
