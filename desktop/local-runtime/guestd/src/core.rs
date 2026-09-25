//! Postgres, Redis and SuperTokens: the containers a workspace needs
//! before any of its own can start.

use super::*;

/// More than locald will ever send (it sends two); a bound, not a budget.
const MAX_CALLBACK_PORTS: usize = 8;

impl<E: Engine + 'static> GuestService<E> {
    pub(crate) fn ensure_core(&self, value: Value) -> Result<Value, GuestError> {
        let parameters = self.parse_core_parameters(value)?;
        self.record_callback_ports(&parameters)?;
        self.ensure_core_images(&parameters)?;
        self.ensure_postgres(&parameters)?;
        self.ensure_redis(&parameters)?;
        self.ensure_supertokens(&parameters)?;
        self.core_status()
    }

    pub(crate) fn ensure_core_stage(
        &self,
        value: Value,
        stage: CoreStage,
    ) -> Result<Value, GuestError> {
        let parameters = self.parse_core_parameters(value)?;
        self.record_callback_ports(&parameters)?;
        match stage {
            CoreStage::Images => self.ensure_core_images(&parameters)?,
            CoreStage::SandboxImages => self.ensure_sandbox_images(&parameters)?,
            CoreStage::Postgres => self.ensure_postgres(&parameters)?,
            CoreStage::Redis => self.ensure_redis(&parameters)?,
            CoreStage::SuperTokens => self.ensure_supertokens(&parameters)?,
        }
        self.core_status()
    }

    pub(crate) fn parse_core_parameters(&self, value: Value) -> Result<CoreParameters, GuestError> {
        let parameters: CoreParameters = serde_json::from_value(value)
            .map_err(|error| GuestError::invalid(format!("invalid core parameters: {error}")))?;
        validate_secret(
            "postgres_password",
            &parameters.credentials.postgres_password,
        )?;
        validate_secret("redis_password", &parameters.credentials.redis_password)?;
        if parameters.callback_ports.len() > MAX_CALLBACK_PORTS
            || parameters.callback_ports.contains(&0)
        {
            return Err(GuestError::invalid(
                "callback_ports must be at most eight non-zero ports",
            ));
        }
        let images = [
            &parameters.images.postgres,
            &parameters.images.redis,
            &parameters.images.supertokens,
        ];
        for image in images {
            validate_image(image)?;
        }
        for image in [
            parameters.images.workspace.as_deref(),
            parameters.images.function.as_deref(),
        ]
        .into_iter()
        .flatten()
        {
            validate_image(image)?;
        }
        Ok(parameters)
    }

    /// Where the callback ports are kept, so a guestd that restarts between
    /// `core.*` and the next `sandbox.ensure` still knows them.
    pub(crate) fn callback_ports_path(&self) -> PathBuf {
        self.state_root.join("run").join("callback-ports.json")
    }

    /// Keep the ports a sandbox may reach on the host gateway. An empty list
    /// (an older locald) leaves whatever was recorded.
    pub(crate) fn record_callback_ports(
        &self,
        parameters: &CoreParameters,
    ) -> Result<(), GuestError> {
        if parameters.callback_ports.is_empty() {
            return Ok(());
        }
        let path = self.callback_ports_path();
        let staged = path.with_extension("json.tmp");
        let body = serde_json::to_vec(&parameters.callback_ports)
            .map_err(|error| GuestError::engine(error.to_string()))?;
        fs::write(&staged, body)
            .and_then(|()| fs::rename(&staged, &path))
            .map_err(|error| GuestError::engine(error.to_string()))
    }

    /// The recorded callback ports. None recorded is an error, not an empty
    /// list: a sandbox started without them could reach nothing it needs on
    /// the host, and would fail its callback wait with a misleading message.
    pub(crate) fn callback_ports(&self) -> Result<Vec<u16>, GuestError> {
        let raw = fs::read(self.callback_ports_path()).map_err(|_| GuestError {
            code: "sandbox_isolation_failed".into(),
            message: "the host's callback ports are not known yet; the managed \
                      runtime has not been started on this guest"
                .into(),
            retryable: true,
            status_code: 503,
        })?;
        serde_json::from_slice(&raw).map_err(|error| {
            GuestError::engine(format!(
                "the recorded callback ports are unreadable: {error}"
            ))
        })
    }

    pub(crate) fn ensure_postgres(&self, parameters: &CoreParameters) -> Result<(), GuestError> {
        self.ensure_volume("lemma-postgres-data")?;
        self.refuse_incompatible_postgres_data(&parameters.images.postgres)?;
        let (postgres_env, postgres_arguments) =
            postgres_container_spec(&parameters.credentials.postgres_password);
        self.ensure_core_container(
            "lemma-core-postgres",
            &parameters.images.postgres,
            "postgres-v1",
            &postgres_env,
            &postgres_arguments,
            &[],
        )?;
        if let Err(mut error) = self.wait_engine_command(
            &[
                "exec".into(),
                "lemma-core-postgres".into(),
                "pg_isready".into(),
                "-U".into(),
                "postgres".into(),
            ],
            120,
        ) {
            if let Some(diagnostic) = self.container_log_summary("lemma-core-postgres") {
                // The container's own account of why it will not start decides
                // what the user is offered. A cluster PostgreSQL refuses to
                // open cannot be retried into working -- and "Try again" was
                // the only button on screen for it, three times over, for a
                // wait that could never end.
                if postgres_refused_its_data(&diagnostic) {
                    return Err(GuestError {
                        code: "postgres_data_incompatible".into(),
                        message: format!(
                            "the workspace database on this computer cannot be opened by this \
                             release of PostgreSQL; {DATA_RESET_MARKER}. PostgreSQL said: \
                             {diagnostic}"
                        ),
                        retryable: false,
                        status_code: 409,
                    });
                }
                error.message = format!("{}: {diagnostic}", error.message);
            }
            return Err(error);
        }
        self.ensure_databases()
    }

    pub(crate) fn ensure_redis(&self, parameters: &CoreParameters) -> Result<(), GuestError> {
        self.ensure_volume("lemma-redis-data")?;
        // Passed as argv rather than through REDIS_ARGS: that variable is read
        // by redis-stack-server's own entrypoint script, and plain redis
        // ignores it entirely -- which would start an unauthenticated,
        // non-persistent Redis rather than failing loudly.
        let redis_command: Vec<String> = [
            "--bind",
            "0.0.0.0",
            "--protected-mode",
            "yes",
            "--appendonly",
            "yes",
            "--dir",
            "/data",
            "--requirepass",
            parameters.credentials.redis_password.as_str(),
        ]
        .iter()
        .map(|value| (*value).to_owned())
        .collect();
        self.ensure_core_container(
            "lemma-core-redis",
            &parameters.images.redis,
            // Bumped so an existing Stack container is replaced rather than
            // adopted: the configuration moved from the environment to argv.
            "redis-v3",
            &BTreeMap::new(),
            &[
                "--network".into(),
                "host".into(),
                "--memory".into(),
                "512m".into(),
                "--cpus".into(),
                "1".into(),
                "--volume".into(),
                "lemma-redis-data:/data".into(),
            ],
            &redis_command,
        )?;
        self.wait_redis(&parameters.credentials.redis_password, 120)
    }

    pub(crate) fn ensure_supertokens(&self, parameters: &CoreParameters) -> Result<(), GuestError> {
        let supertokens_env = BTreeMap::from([
            (
                "POSTGRESQL_CONNECTION_URI".into(),
                format!(
                    "postgresql://postgres:{}@127.0.0.1:5432/supertokens",
                    parameters.credentials.postgres_password
                ),
            ),
            ("JAVA_TOOL_OPTIONS".into(), "-Xms128m -Xmx512m".into()),
        ]);
        self.ensure_core_container(
            "lemma-core-supertokens",
            &parameters.images.supertokens,
            "supertokens-v1",
            &supertokens_env,
            &[
                "--network".into(),
                "host".into(),
                "--memory".into(),
                "768m".into(),
                "--cpus".into(),
                "1".into(),
            ],
            &[],
        )?;
        self.wait_tcp(5432, 120)?;
        self.wait_http_port(3567, "/hello", 120)
    }

    pub(crate) fn core_status(&self) -> Result<Value, GuestError> {
        let endpoint_host = self.current_endpoint_host();
        let mut components = serde_json::Map::new();
        let mut ready = true;
        for (name, port) in [
            ("postgres", 5432_u16),
            ("redis", 6379_u16),
            ("supertokens", 3567_u16),
        ] {
            let container = format!("lemma-core-{name}");
            let inspect = self.inspect_raw(&container)?;
            let state = inspect
                .as_ref()
                .and_then(|value| value.get("State"))
                .and_then(Value::as_object);
            let running = state
                .and_then(|value| value.get("Running"))
                .and_then(Value::as_bool)
                .unwrap_or(false);
            let state_name = state
                .and_then(|value| value.get("Status"))
                .and_then(Value::as_str)
                .unwrap_or("missing");
            let exit_code = state
                .and_then(|value| value.get("ExitCode"))
                .and_then(Value::as_i64);
            ready &= running;
            components.insert(
                name.into(),
                json!({
                    "running": running,
                    "state": state_name,
                    "exit_code": exit_code,
                    "endpoint": endpoint_host
                        .as_ref()
                        .map(|host| format!("{host}:{port}")),
                }),
            );
        }
        Ok(json!({
            "ready": ready,
            "endpoint_host": endpoint_host,
            "host_gateway": self.host_gateway,
            "components": components,
        }))
    }

    pub(crate) fn stop_core(&self) -> Result<Value, GuestError> {
        for name in ["supertokens", "redis", "postgres"] {
            let container = format!("lemma-core-{name}");
            if self.inspect_raw(&container)?.is_some() {
                self.run_checked(&["stop".into(), container])?;
            }
        }
        Ok(json!({"stopped": true}))
    }

    pub(crate) fn ensure_volume(&self, name: &str) -> Result<(), GuestError> {
        let inspect = self
            .engine
            .run(&["volume".into(), "inspect".into(), name.into()])
            .map_err(GuestError::engine)?;
        if !inspect.status.success() {
            let create = self
                .engine
                .run(&["volume".into(), "create".into(), name.into()])
                .map_err(GuestError::engine)?;
            if !create.status.success() {
                let stderr = String::from_utf8_lossy(&create.stderr);
                // A disposable container-cache repair intentionally preserves
                // named-volume data while replacing containerd's metadata.
                // nerdctl then rediscovers the on-disk volume and reports this
                // warning with a non-zero exit status. It is the desired
                // outcome: the existing user data must be reused.
                if !stderr.contains("already exists and will be returned as-is") {
                    return Err(GuestError::engine(redact_engine_error(&stderr)));
                }
            }
        }
        Ok(())
    }

    pub(crate) fn ensure_core_container(
        &self,
        name: &str,
        image: &str,
        config_generation: &str,
        environment: &BTreeMap<String, String>,
        options: &[String],
        command: &[String],
    ) -> Result<(), GuestError> {
        let current = self.inspect_raw(name)?;
        let current_image = current
            .as_ref()
            .and_then(|value| value.get("Config"))
            .and_then(Value::as_object)
            .and_then(|value| value.get("Labels"))
            .and_then(Value::as_object)
            .and_then(|value| value.get("work.lemma.image-ref"))
            .and_then(Value::as_str);
        let current_platform = current
            .as_ref()
            .and_then(|value| value.get("Config"))
            .and_then(Value::as_object)
            .and_then(|value| value.get("Labels"))
            .and_then(Value::as_object)
            .and_then(|value| value.get("work.lemma.platform"))
            .and_then(Value::as_str);
        let current_config_generation = current
            .as_ref()
            .and_then(|value| value.get("Config"))
            .and_then(Value::as_object)
            .and_then(|value| value.get("Labels"))
            .and_then(Value::as_object)
            .and_then(|value| value.get("work.lemma.config-generation"))
            .and_then(Value::as_str);
        if current.is_some()
            && (current_image != Some(image)
                || current_platform != Some(guest_platform())
                || current_config_generation != Some(config_generation))
        {
            self.run_checked(&["rm".into(), "--force".into(), name.into()])?;
        } else if let Some(current) = current {
            let running = current
                .get("State")
                .and_then(Value::as_object)
                .and_then(|value| value.get("Running"))
                .and_then(Value::as_bool)
                .unwrap_or(false);
            if !running {
                if self.restart_or_remove_stale(name)? {
                    return Ok(());
                }
                // A guest OS refresh can invalidate containerd's ephemeral
                // resolv.conf or mount paths. Recreate the stopped container
                // below while retaining its named data volume.
            } else {
                return Ok(());
            }
        }
        let env_file = self.write_env_file(name, environment)?;
        let mut arguments = vec![
            "run".into(),
            "--detach".into(),
            "--platform".into(),
            guest_platform().into(),
            "--name".into(),
            name.into(),
            "--label".into(),
            "work.lemma.component=core".into(),
            "--label".into(),
            format!("work.lemma.image-ref={image}"),
            "--label".into(),
            format!("work.lemma.platform={}", guest_platform()),
            "--label".into(),
            format!("work.lemma.config-generation={config_generation}"),
            // Last in line for the OOM killer: see `SANDBOX_OOM_SCORE_ADJ`.
            // Takes effect when a core container is next created; one that is
            // running already still sits below every sandbox, which is the
            // ordering that matters.
            "--oom-score-adj".into(),
            CORE_OOM_SCORE_ADJ.to_string(),
        ];
        arguments.extend_from_slice(options);
        if !environment.is_empty() {
            arguments.extend(["--env-file".into(), env_file.display().to_string()]);
        }
        arguments.push(image.into());
        arguments.extend_from_slice(command);
        let result = self.run_checked(&arguments);
        let _ = fs::remove_file(env_file);
        result.map(|_| ())
    }

    pub(crate) fn restart_or_remove_stale(&self, name: &str) -> Result<bool, GuestError> {
        let started = self
            .engine
            .run(&["start".into(), name.into()])
            .map(|output| output.status.success())
            .unwrap_or(false);
        if started {
            return Ok(true);
        }
        self.run_checked(&["rm".into(), "--force".into(), name.into()])?;
        Ok(false)
    }
}
