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

/// What `sandbox.ensure` does with a container that already exists.
#[derive(Debug, PartialEq, Eq)]
pub(crate) enum ExistingContainer {
    /// Running, and exactly what was asked for.
    Reuse,
    /// Stopped, or running with different grants or older hardening: made
    /// again, and the old one removed only once the new one's preflight has
    /// passed (`GuestService::replace_and_run`).
    Replace,
    /// Running a different generation (image or metadata): refused.
    Conflict,
}

/// The run-time hardening every sandbox container is created with, as a
/// number: `--cap-drop ALL`, `no-new-privileges`, and the isolation chain
/// installed before any sandbox starts. Stamped on the container as
/// `lemma.work/hardening`, and raised whenever what `build_run_arguments`
/// hardens changes, so a running container made before that change is
/// replaced on its next ensure rather than reused with the old, weaker
/// arguments. A container with no label predates all of it and reads as 0.
pub(crate) const SANDBOX_HARDENING_VERSION: u64 = 1;

/// The grants a container is made with, as `sandbox.ensure` asks for them and
/// as `snapshot_from_inspect` reads them back off its labels.
///
/// Compared on every ensure, because a grant is fixed into the container when
/// it is created: reusing a running one made with a different grant would
/// silently keep the old reach -- the alias it was meant to lose, the relay it
/// was no longer granted -- or lack the one it was meant to gain.
pub(crate) fn requested_grants(parameters: &EnsureParameters) -> Value {
    json!({
        "host_access": parameters.host_access,
        "host_loopback": parameters.host_loopback,
    })
}

pub(crate) fn existing_container_verdict(
    snapshot: &Value,
    parameters: &EnsureParameters,
) -> ExistingContainer {
    if snapshot["status"]["status"] != "RUNNING" {
        return ExistingContainer::Replace;
    }
    if snapshot["metadata"] != json!(parameters.metadata) || snapshot["image"] != parameters.image {
        return ExistingContainer::Conflict;
    }
    // Same generation. A grant is not part of it -- it is who may reach what,
    // not what runs -- so a change is applied by making the container again
    // rather than refused. Nor is hardening: a container made before the
    // current hardening is replaced, never reused.
    if snapshot["grants"] != requested_grants(parameters) {
        return ExistingContainer::Replace;
    }
    if snapshot["hardening"].as_u64().unwrap_or(0) < SANDBOX_HARDENING_VERSION {
        return ExistingContainer::Replace;
    }
    ExistingContainer::Reuse
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
        if parameters.workload_kind == WorkloadKind::Function && parameters.host_loopback {
            // Only a person's workspace has a browser in it. A function runs an
            // immutable artifact and has no reason to reach anyone's Mac.
            return Err(GuestError::invalid(
                "function sandboxes cannot receive the host loopback relay",
            ));
        }

        let container = container_name(&parameters.sandbox_id);
        // What is there now, and so what has to happen. Nothing is removed
        // here: a replacement only takes the old container away once
        // everything that can fail before `run` has passed.
        let existing = match self.snapshot_optional(&parameters.sandbox_id)? {
            Some(snapshot) => match existing_container_verdict(&snapshot, &parameters) {
                ExistingContainer::Reuse => None,
                ExistingContainer::Conflict => {
                    return Err(GuestError {
                        code: "generation_conflict".into(),
                        message: "Sandbox generation changed while it is running".into(),
                        retryable: false,
                        status_code: 409,
                    });
                }
                ExistingContainer::Replace => Some(Some(snapshot["status"]["status"] == "RUNNING")),
            },
            None => Some(None),
        };
        if let Some(replacing) = existing {
            if self.sandbox_isolation {
                self.ensure_network_isolation()?;
            }
            // A running container being replaced is a swap, not another
            // sandbox: counting it against the ceiling would refuse exactly
            // the replacement that keeps the machine at the same count.
            if replacing != Some(true) {
                self.admit_sandbox_memory(requested_memory)?;
            }
            self.ensure_sandbox_image_for_start(&parameters.image, parameters.workload_kind)?;
            let workspace = match parameters.workload_kind {
                WorkloadKind::Workspace => Some(self.workspace(&parameters.sandbox_id)?),
                WorkloadKind::Function => None,
            };
            let runtime_token = match parameters.runtime_token.as_deref() {
                Some(token) => Some(self.write_runtime_token(&parameters.sandbox_id, token)?),
                None => None,
            };
            // Created whether or not a relay is listening in it: the engine
            // refuses a bind mount whose source does not exist, and on WSL,
            // where nothing ever listens, it simply stays empty.
            let relay_directory = self.host_loopback_directory();
            if parameters.host_loopback {
                prepare_relay_directory(&relay_directory)
                    .map_err(|error| GuestError::engine(error.to_string()))?;
            }
            let env_file = self.write_env_file(&parameters.sandbox_id, &parameters.env)?;
            let arguments = build_run_arguments(
                &parameters,
                workspace.as_deref(),
                runtime_token.as_deref(),
                &env_file,
                &self.host_gateway,
                &relay_directory,
            );
            let result = match replacing {
                None => self.run_checked(&arguments).map(|_| ()),
                Some(running) => self.replace_and_run(&container, &arguments, running),
            };
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
        // that is RUNNING with matching metadata, image, grants and hardening
        // is reused above, skipping creation, the image check and the
        // admission check, and lands straight back here.
        let deadline = Instant::now() + SANDBOX_READY_POLL_BUDGET;
        let mut last_snapshot = None;
        let mut applications_healthy = false;
        while Instant::now() < deadline {
            match self.snapshot_optional(&parameters.sandbox_id)? {
                // `ready` is the eager apps answering their health paths, so it
                // needs no second probe here.
                Some(snapshot) if snapshot["status"]["ready"] == true => {
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

    /// Replace `container` with a new one made from `arguments`, without
    /// ever leaving the sandbox with neither.
    ///
    /// Called only after every fallible preflight has passed. A running
    /// container is renamed aside, not removed, so that when `run` fails the
    /// user's sandbox is put back exactly as it was; it is removed only once
    /// the new one exists. A stopped container has nothing running to keep,
    /// and is removed immediately before `run`.
    pub(crate) fn replace_and_run(
        &self,
        container: &str,
        arguments: &[String],
        running: bool,
    ) -> Result<(), GuestError> {
        if !running {
            self.run_checked(&["rm".into(), "--force".into(), container.into()])?;
            return self.run_checked(arguments).map(|_| ());
        }
        let aside = format!("{container}-replaced");
        // A leftover from an earlier replacement that died half-way; absent
        // is the ordinary case, so its failure means nothing.
        let _ = self.run_checked(&["rm".into(), "--force".into(), aside.clone()]);
        self.run_checked(&["rename".into(), container.into(), aside.clone()])?;
        match self.run_checked(arguments) {
            Ok(_) => {
                // The new one is up; the old one is only in the way now, and
                // a failure to remove it leaves a stray container rather than
                // a broken sandbox. The next replacement removes it first.
                let _ = self.run_checked(&["rm".into(), "--force".into(), aside]);
                Ok(())
            }
            Err(error) => {
                // Whatever `run` left behind under the name, then the old one
                // back under it.
                let _ = self.run_checked(&["rm".into(), "--force".into(), container.into()]);
                self.run_checked(&["rename".into(), aside, container.into()])?;
                Err(error)
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
