//! A workspace sandbox: creating one, reporting on it, and taking it
//! away again.

use super::*;

#[derive(Clone, Copy, PartialEq)]
pub(crate) enum Mutation {
    Release,
    Delete,
    PurgeStorage,
    PurgeExact,
}

impl<E: Engine + 'static> GuestService<E> {
    pub(crate) fn ensure(&self, value: Value) -> Result<Value, GuestError> {
        let parameters: EnsureParameters = serde_json::from_value(value)
            .map_err(|error| GuestError::invalid(format!("invalid ensure parameters: {error}")))?;
        validate_sandbox_id(&parameters.sandbox_id)?;
        validate_image(&parameters.image)?;
        validate_apps(&parameters.apps)?;
        validate_environment(&parameters.env)?;
        validate_metadata(&parameters.metadata)?;
        let requested_memory = validate_resources(&parameters.resources)?;
        if parameters.workload_kind == WorkloadKind::Workspace
            && parameters
                .runtime_token
                .as_deref()
                .is_none_or(|value| value.is_empty())
        {
            return Err(GuestError::invalid(
                "workspace runtime token must be configured",
            ));
        }
        if parameters.workload_kind == WorkloadKind::Function && parameters.runtime_token.is_some()
        {
            return Err(GuestError::invalid(
                "function sandboxes cannot receive a workspace runtime token",
            ));
        }

        let container = container_name(&parameters.sandbox_id);
        let should_create = match self.snapshot_optional(&parameters.sandbox_id)? {
            Some(snapshot)
                if snapshot["status"]["status"] == "RUNNING"
                    && snapshot["metadata"] == json!(parameters.metadata)
                    && snapshot["image"] == parameters.image =>
            {
                false
            }
            Some(snapshot) if snapshot["status"]["status"] == "RUNNING" => {
                return Err(GuestError {
                    code: "generation_conflict".into(),
                    message: "Sandbox generation changed while it is running".into(),
                    retryable: false,
                    status_code: 409,
                });
            }
            Some(_) => {
                self.run_checked(&["rm".into(), "--force".into(), container.clone()])?;
                true
            }
            None => true,
        };
        if should_create {
            self.admit_sandbox_memory(requested_memory)?;
            self.ensure_sandbox_image_for_start(&parameters.image, parameters.workload_kind)?;
            let workspace = match parameters.workload_kind {
                WorkloadKind::Workspace => Some(self.workspace(&parameters.sandbox_id)?),
                WorkloadKind::Function => None,
            };
            let runtime_token = match parameters.runtime_token.as_deref() {
                Some(token) => Some(self.write_runtime_token(&parameters.sandbox_id, token)?),
                None => None,
            };
            let env_file = self.write_env_file(&parameters.sandbox_id, &parameters.env)?;
            let arguments = build_run_arguments(
                &parameters,
                workspace.as_deref(),
                runtime_token.as_deref(),
                &env_file,
                &self.host_gateway,
            );
            let result = self.run_checked(&arguments);
            let _ = fs::remove_file(&env_file);
            result?;
        }

        // Bounded, because this request holds the guest's only control channel.
        //
        // The host bridge keeps a single vsock connection behind a
        // process-wide mutex, and `serve_vsock` handles each connection inline
        // on its accept loop -- so one request in flight is the whole
        // machine's guest traffic. Waiting here for up to three minutes meant
        // a slow sandbox start blocked every other sandbox operation on the
        // computer, including read-only ones: a `sandbox.list` was measured
        // timing out after 60s having never reached this process.
        //
        // Most starts finish well inside this window, so the common case still
        // returns ready in one round trip. A slower one is handed back as
        // retryable rather than waited out, and re-entry is cheap: a container
        // that is RUNNING with matching metadata and image takes the
        // `should_create == false` path above, skipping creation, the image
        // check and the admission check, and lands straight back here.
        let deadline = Instant::now() + SANDBOX_READY_POLL_BUDGET;
        let mut last_snapshot = None;
        let mut applications_healthy = false;
        while Instant::now() < deadline {
            match self.snapshot_optional(&parameters.sandbox_id)? {
                Some(snapshot)
                    if snapshot["status"]["ready"] == true
                        && eager_apps_healthy(&snapshot, &parameters.apps) =>
                {
                    last_snapshot = Some(snapshot);
                    applications_healthy = true;
                    break;
                }
                Some(snapshot)
                    if matches!(
                        snapshot["status"]["status"].as_str(),
                        Some("STOPPED" | "ERROR")
                    ) =>
                {
                    return Err(self.sandbox_startup_error(
                        &container,
                        "sandbox runtime stopped before becoming ready",
                    ));
                }
                snapshot => last_snapshot = snapshot,
            }
            thread::sleep(Duration::from_millis(250));
        }
        let snapshot = last_snapshot.ok_or_else(GuestError::not_found)?;
        if snapshot["status"]["ready"] != true || !applications_healthy {
            // Still coming up, as far as anything here can tell: a container
            // that had died would have been caught by the STOPPED/ERROR arm
            // above and reported as a startup failure. So this is "not yet",
            // and saying so releases the channel for everyone else instead of
            // holding it until the sandbox is either ready or hopeless.
            return Err(GuestError {
                code: "not_ready".into(),
                message: format!(
                    "sandbox {} is still starting",
                    parameters.sandbox_id.as_str()
                ),
                retryable: true,
                status_code: 503,
            });
        }
        self.wait_callback(&parameters)?;
        Ok(snapshot)
    }

    pub(crate) fn sandbox_startup_error(&self, container: &str, summary: &str) -> GuestError {
        let mut diagnostics = Vec::new();
        if let Ok(Some(inspect)) = self.inspect_raw(container) {
            if let Some(state) = inspect.get("State").and_then(Value::as_object) {
                if state
                    .get("OOMKilled")
                    .and_then(Value::as_bool)
                    .unwrap_or(false)
                {
                    diagnostics.push("container exceeded its memory limit".to_owned());
                }
                if let Some(error) = state
                    .get("Error")
                    .and_then(Value::as_str)
                    .filter(|value| !value.trim().is_empty())
                {
                    diagnostics.push(redact_engine_error(error));
                }
                if let Some(exit_code) = state
                    .get("ExitCode")
                    .and_then(Value::as_i64)
                    .filter(|value| *value != 0)
                {
                    diagnostics.push(format!("container exited with code {exit_code}"));
                }
            }
        }
        if let Some(log) = self.container_log_summary(container) {
            if !diagnostics.iter().any(|value| value == &log) {
                diagnostics.push(log);
            }
        }
        if diagnostics.is_empty() {
            GuestError::engine(summary)
        } else {
            GuestError::engine(format!("{summary}: {}", diagnostics.join("; ")))
        }
    }

    pub(crate) fn status(&self, value: Value) -> Result<Value, GuestError> {
        let sandbox_id = required_string(&value, "sandbox_id")?;
        validate_sandbox_id(&sandbox_id)?;
        self.snapshot_optional(&sandbox_id)?
            .ok_or_else(GuestError::not_found)
    }

    pub(crate) fn list(&self) -> Result<Value, GuestError> {
        let output = self.run_checked(&[
            "ps".into(),
            "--all".into(),
            "--filter".into(),
            format!("label={MANAGED_LABEL}"),
            "--format".into(),
            "{{.Names}}".into(),
        ])?;
        let mut sandboxes = Vec::new();
        for name in output
            .lines()
            .filter(|line| line.starts_with(CONTAINER_PREFIX))
        {
            let sandbox_id = name.trim().trim_start_matches(CONTAINER_PREFIX);
            if validate_sandbox_id(sandbox_id).is_ok() {
                if let Some(snapshot) = self.snapshot_optional(sandbox_id)? {
                    sandboxes.push(snapshot);
                }
            }
        }
        Ok(json!({"sandboxes": sandboxes}))
    }

    pub(crate) fn mutate(&self, value: Value, mutation: Mutation) -> Result<Value, GuestError> {
        let sandbox_id = required_string(&value, "sandbox_id")?;
        validate_sandbox_id(&sandbox_id)?;
        let existing = self.snapshot_optional(&sandbox_id)?;
        if mutation == Mutation::PurgeExact {
            let expected = required_string(&value, "provider_id")?;
            if let Some(snapshot) = &existing {
                if snapshot["provider_id"].as_str() != Some(&expected) {
                    return Err(GuestError {
                        code: "generation_conflict".into(),
                        message: "Sandbox generation changed".into(),
                        retryable: false,
                        status_code: 409,
                    });
                }
            }
        }
        match mutation {
            Mutation::Release => {
                if existing.is_none() {
                    return Err(GuestError::not_found());
                }
                self.run_checked(&["stop".into(), container_name(&sandbox_id)])?;
                Ok(json!({"released": true}))
            }
            Mutation::Delete => {
                if existing.is_none() {
                    return Err(GuestError::not_found());
                }
                self.run_checked(&["rm".into(), "--force".into(), container_name(&sandbox_id)])?;
                self.remove_runtime_token(&sandbox_id)?;
                Ok(json!({"deleted": true}))
            }
            Mutation::PurgeStorage => {
                let purged = self.purge_workspace(&sandbox_id)?;
                Ok(json!({"purged": purged}))
            }
            Mutation::PurgeExact => {
                if existing.is_some() {
                    self.run_checked(&[
                        "rm".into(),
                        "--force".into(),
                        container_name(&sandbox_id),
                    ])?;
                }
                self.purge_workspace(&sandbox_id)?;
                self.remove_runtime_token(&sandbox_id)?;
                Ok(json!({"purged": existing.is_some()}))
            }
        }
    }

    pub(crate) fn snapshot_optional(&self, sandbox_id: &str) -> Result<Option<Value>, GuestError> {
        let output = self
            .engine
            .run(&["inspect".into(), container_name(sandbox_id)])
            .map_err(GuestError::engine)?;
        if !output.status.success() {
            return Ok(None);
        }
        let parsed: Value = serde_json::from_slice(&output.stdout)
            .map_err(|error| GuestError::engine(format!("invalid inspect response: {error}")))?;
        let inspect = parsed
            .as_array()
            .and_then(|items| items.first())
            .and_then(Value::as_object)
            .ok_or_else(|| GuestError::engine("empty inspect response"))?;
        Ok(Some(snapshot_from_inspect(
            sandbox_id,
            inspect,
            &self.routable_endpoint_host()?,
        )?))
    }
}
