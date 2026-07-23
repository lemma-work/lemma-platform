use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::fs::{self, OpenOptions};
use std::io::{self, BufRead, BufReader, Read, Write};
use std::net::{IpAddr, SocketAddr, TcpStream, ToSocketAddrs};
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

pub const PROTOCOL_VERSION: u64 = 1;
pub const VSOCK_PORT: u32 = 42_411;
const MAX_REQUEST_BYTES: u64 = 1024 * 1024;
const MAX_RESPONSE_BYTES: usize = 4 * 1024 * 1024;
const CONTAINER_PREFIX: &str = "agentbox-";
const MANAGED_LABEL: &str = "app.kubernetes.io/name=agentbox-sandbox";

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GuestRequest {
    pub version: u64,
    #[serde(default)]
    pub capability: Option<String>,
    pub operation: String,
    #[serde(default)]
    pub parameters: Value,
}

#[derive(Debug, Serialize)]
pub struct GuestResponse {
    pub ok: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<GuestError>,
}

#[derive(Clone, Debug, Serialize)]
pub struct GuestError {
    pub code: String,
    pub message: String,
    pub retryable: bool,
    pub status_code: u16,
}

impl GuestError {
    fn invalid(message: impl Into<String>) -> Self {
        Self {
            code: "invalid_request".into(),
            message: message.into(),
            retryable: false,
            status_code: 422,
        }
    }

    fn not_found() -> Self {
        Self {
            code: "not_found".into(),
            message: "Sandbox not found".into(),
            retryable: false,
            status_code: 404,
        }
    }

    fn engine(message: impl Into<String>) -> Self {
        Self {
            code: "guest_engine_failed".into(),
            message: message.into(),
            retryable: true,
            status_code: 503,
        }
    }
}

impl GuestResponse {
    fn success(result: Value) -> Self {
        Self {
            ok: true,
            result: Some(result),
            error: None,
        }
    }

    fn failure(error: GuestError) -> Self {
        Self {
            ok: false,
            result: None,
            error: Some(error),
        }
    }
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct AppSpec {
    name: String,
    public_slug: String,
    port: u16,
    #[serde(default = "default_health_path")]
    health_path: String,
    #[serde(default)]
    startup: String,
    #[serde(default)]
    exposure: String,
    #[serde(default)]
    auth_mode: String,
}

fn default_health_path() -> String {
    "/health".into()
}

#[derive(Clone, Debug, Default, Deserialize)]
#[serde(deny_unknown_fields)]
struct ResourceSpec {
    memory: Option<String>,
    cpus: Option<String>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct CallbackSpec {
    #[serde(default)]
    required: bool,
    #[serde(default = "default_health_path")]
    health_path: String,
    #[serde(default = "default_callback_timeout")]
    timeout_seconds: f64,
}

fn default_callback_timeout() -> f64 {
    30.0
}

impl Default for CallbackSpec {
    fn default() -> Self {
        Self {
            required: false,
            health_path: default_health_path(),
            timeout_seconds: default_callback_timeout(),
        }
    }
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct EnsureParameters {
    sandbox_id: String,
    image: String,
    #[serde(default)]
    env: BTreeMap<String, String>,
    apps: Vec<AppSpec>,
    #[serde(default)]
    resources: ResourceSpec,
    #[serde(default)]
    callback: CallbackSpec,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct CoreImages {
    postgres: String,
    redis: String,
    supertokens: String,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct CoreCredentials {
    postgres_password: String,
    redis_password: String,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct CoreParameters {
    images: CoreImages,
    credentials: CoreCredentials,
}

pub trait Engine: Send + Sync {
    fn run(&self, arguments: &[String]) -> Result<Output, String>;
}

pub struct NerdctlEngine {
    executable: PathBuf,
}

impl NerdctlEngine {
    pub fn discover() -> Result<Self, GuestError> {
        let configured = std::env::var_os("LEMMA_NERDCTL_BIN")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("/usr/local/bin/nerdctl"));
        if !configured.is_file() {
            return Err(GuestError::engine(format!(
                "managed container engine is missing: {}",
                configured.display()
            )));
        }
        Ok(Self {
            executable: configured,
        })
    }
}

impl Engine for NerdctlEngine {
    fn run(&self, arguments: &[String]) -> Result<Output, String> {
        Command::new(&self.executable)
            .args(["--namespace", "lemma"])
            .args(arguments)
            .stdin(Stdio::null())
            .output()
            .map_err(|error| error.to_string())
    }
}

pub struct GuestService<E: Engine> {
    engine: E,
    state_root: PathBuf,
    endpoint_host: String,
    host_gateway: String,
    capability: Option<String>,
}

impl GuestService<NerdctlEngine> {
    pub fn discover() -> Result<Self, GuestError> {
        let state_root = std::env::var_os("LEMMA_GUEST_STATE_ROOT")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("/var/lib/lemma"));
        let endpoint_host = std::env::var("LEMMA_GUEST_ENDPOINT_HOST")
            .ok()
            .filter(|value| valid_ip(value))
            .or_else(discover_guest_ip)
            .ok_or_else(|| GuestError::engine("could not discover the guest IPv4 address"))?;
        let host_gateway = std::env::var("LEMMA_HOST_GATEWAY")
            .ok()
            .filter(|value| valid_ip(value))
            .or_else(discover_host_gateway)
            .ok_or_else(|| GuestError::engine("could not discover the private host gateway"))?;
        let capability = load_capability()?;
        Self::new(
            NerdctlEngine::discover()?,
            state_root,
            endpoint_host,
            host_gateway,
            capability,
        )
    }
}

impl<E: Engine> GuestService<E> {
    pub fn new(
        engine: E,
        state_root: PathBuf,
        endpoint_host: String,
        host_gateway: String,
        capability: Option<String>,
    ) -> Result<Self, GuestError> {
        if !valid_ip(&endpoint_host) || !valid_ip(&host_gateway) {
            return Err(GuestError::invalid(
                "endpoint_host and host_gateway must be literal IP addresses",
            ));
        }
        for relative in ["workspaces", "run"] {
            fs::create_dir_all(state_root.join(relative))
                .map_err(|error| GuestError::engine(error.to_string()))?;
        }
        Ok(Self {
            engine,
            state_root,
            endpoint_host,
            host_gateway,
            capability,
        })
    }

    pub fn handle(&self, request: GuestRequest) -> GuestResponse {
        match self.try_handle(request) {
            Ok(result) => GuestResponse::success(result),
            Err(error) => GuestResponse::failure(error),
        }
    }

    fn try_handle(&self, request: GuestRequest) -> Result<Value, GuestError> {
        if request.version != PROTOCOL_VERSION {
            return Err(GuestError::invalid(format!(
                "unsupported protocol version {}",
                request.version
            )));
        }
        if let Some(expected) = &self.capability {
            if request.capability.as_deref() != Some(expected) {
                return Err(GuestError {
                    code: "unauthorized".into(),
                    message: "Invalid guest capability".into(),
                    retryable: false,
                    status_code: 401,
                });
            }
        }
        match request.operation.as_str() {
            "health" => Ok(json!({
                "status": "ready", "engine": "containerd",
                "endpoint_host": self.endpoint_host,
                "host_gateway": self.host_gateway,
            })),
            "core.ensure" => self.ensure_core(request.parameters),
            "core.status" => self.core_status(),
            "core.stop" => self.stop_core(),
            "sandbox.ensure" => self.ensure(request.parameters),
            "sandbox.status" => self.status(request.parameters),
            "sandbox.list" => self.list(),
            "sandbox.release" => self.mutate(request.parameters, Mutation::Release),
            "sandbox.delete" => self.mutate(request.parameters, Mutation::Delete),
            "sandbox.purge_storage" => self.mutate(request.parameters, Mutation::PurgeStorage),
            "sandbox.purge" => self.mutate(request.parameters, Mutation::PurgeExact),
            _ => Err(GuestError::invalid(format!(
                "unknown operation {:?}",
                request.operation
            ))),
        }
    }

    fn ensure_core(&self, value: Value) -> Result<Value, GuestError> {
        let parameters: CoreParameters = serde_json::from_value(value)
            .map_err(|error| GuestError::invalid(format!("invalid core parameters: {error}")))?;
        validate_secret(
            "postgres_password",
            &parameters.credentials.postgres_password,
        )?;
        validate_secret("redis_password", &parameters.credentials.redis_password)?;
        for image in [
            &parameters.images.postgres,
            &parameters.images.redis,
            &parameters.images.supertokens,
        ] {
            validate_image(image)?;
            self.run_checked(&["pull".into(), image.clone()])?;
        }

        self.ensure_network("lemma-core")?;
        self.ensure_volume("lemma-postgres-data")?;
        let postgres_env = BTreeMap::from([
            ("POSTGRES_USER".into(), "postgres".into()),
            (
                "POSTGRES_PASSWORD".into(),
                parameters.credentials.postgres_password.clone(),
            ),
            ("POSTGRES_DB".into(), "lemma".into()),
        ]);
        self.ensure_core_container(
            "lemma-core-postgres",
            &parameters.images.postgres,
            &postgres_env,
            &[
                "--network".into(),
                "lemma-core".into(),
                "--network-alias".into(),
                "postgres".into(),
                "--volume".into(),
                "lemma-postgres-data:/var/lib/postgresql/data".into(),
                "--publish".into(),
                "0.0.0.0:5432:5432".into(),
            ],
            &[],
        )?;

        let redis_config = self.state_root.join("redis.conf");
        write_private_atomic(
            &redis_config,
            format!(
                "bind 0.0.0.0\nprotected-mode yes\nappendonly yes\nrequirepass {}\n",
                parameters.credentials.redis_password
            )
            .as_bytes(),
        )?;
        self.ensure_core_container(
            "lemma-core-redis",
            &parameters.images.redis,
            &BTreeMap::new(),
            &[
                "--network".into(),
                "lemma-core".into(),
                "--network-alias".into(),
                "redis".into(),
                "--mount".into(),
                format!(
                    "type=bind,src={},dst=/usr/local/etc/redis/redis.conf,ro",
                    redis_config.display()
                ),
                "--publish".into(),
                "0.0.0.0:6379:6379".into(),
            ],
            &[
                "redis-server".into(),
                "/usr/local/etc/redis/redis.conf".into(),
            ],
        )?;

        self.wait_engine_command(
            &[
                "exec".into(),
                "lemma-core-postgres".into(),
                "pg_isready".into(),
                "-U".into(),
                "postgres".into(),
            ],
            120,
        )?;
        self.ensure_databases()?;

        let supertokens_env = BTreeMap::from([(
            "POSTGRESQL_CONNECTION_URI".into(),
            format!(
                "postgresql://postgres:{}@postgres:5432/supertokens",
                parameters.credentials.postgres_password
            ),
        )]);
        self.ensure_core_container(
            "lemma-core-supertokens",
            &parameters.images.supertokens,
            &supertokens_env,
            &[
                "--network".into(),
                "lemma-core".into(),
                "--network-alias".into(),
                "supertokens".into(),
                "--publish".into(),
                "0.0.0.0:3567:3567".into(),
            ],
            &[],
        )?;
        self.wait_tcp(5432, 120)?;
        self.wait_redis(&parameters.credentials.redis_password, 120)?;
        self.wait_http_port(3567, "/hello", 120)?;
        self.core_status()
    }

    fn core_status(&self) -> Result<Value, GuestError> {
        let mut components = serde_json::Map::new();
        let mut ready = true;
        for (name, port) in [
            ("postgres", 5432_u16),
            ("redis", 6379_u16),
            ("supertokens", 3567_u16),
        ] {
            let container = format!("lemma-core-{name}");
            let inspect = self.inspect_raw(&container)?;
            let running = inspect
                .as_ref()
                .and_then(|value| value.get("State"))
                .and_then(Value::as_object)
                .and_then(|value| value.get("Running"))
                .and_then(Value::as_bool)
                .unwrap_or(false);
            ready &= running;
            components.insert(
                name.into(),
                json!({
                    "running": running,
                    "endpoint": format!("{}:{port}", self.endpoint_host),
                }),
            );
        }
        Ok(json!({
            "ready": ready,
            "endpoint_host": self.endpoint_host,
            "host_gateway": self.host_gateway,
            "components": components,
        }))
    }

    fn stop_core(&self) -> Result<Value, GuestError> {
        for name in ["supertokens", "redis", "postgres"] {
            let container = format!("lemma-core-{name}");
            if self.inspect_raw(&container)?.is_some() {
                self.run_checked(&["stop".into(), container])?;
            }
        }
        Ok(json!({"stopped": true}))
    }

    fn ensure_network(&self, name: &str) -> Result<(), GuestError> {
        let inspect = self
            .engine
            .run(&["network".into(), "inspect".into(), name.into()])
            .map_err(GuestError::engine)?;
        if !inspect.status.success() {
            self.run_checked(&["network".into(), "create".into(), name.into()])?;
        }
        Ok(())
    }

    fn ensure_volume(&self, name: &str) -> Result<(), GuestError> {
        let inspect = self
            .engine
            .run(&["volume".into(), "inspect".into(), name.into()])
            .map_err(GuestError::engine)?;
        if !inspect.status.success() {
            self.run_checked(&["volume".into(), "create".into(), name.into()])?;
        }
        Ok(())
    }

    fn ensure_core_container(
        &self,
        name: &str,
        image: &str,
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
        if current.is_some() && current_image != Some(image) {
            self.run_checked(&["rm".into(), "--force".into(), name.into()])?;
        } else if let Some(current) = current {
            let running = current
                .get("State")
                .and_then(Value::as_object)
                .and_then(|value| value.get("Running"))
                .and_then(Value::as_bool)
                .unwrap_or(false);
            if !running {
                self.run_checked(&["start".into(), name.into()])?;
            }
            return Ok(());
        }
        let env_file = self.write_env_file(name, environment)?;
        let mut arguments = vec![
            "run".into(),
            "--detach".into(),
            "--name".into(),
            name.into(),
            "--label".into(),
            "work.lemma.component=core".into(),
            "--label".into(),
            format!("work.lemma.image-ref={image}"),
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

    fn inspect_raw(&self, name: &str) -> Result<Option<Value>, GuestError> {
        let output = self
            .engine
            .run(&["inspect".into(), name.into()])
            .map_err(GuestError::engine)?;
        if !output.status.success() {
            return Ok(None);
        }
        let parsed: Value = serde_json::from_slice(&output.stdout)
            .map_err(|error| GuestError::engine(format!("invalid inspect response: {error}")))?;
        Ok(parsed.as_array().and_then(|items| items.first()).cloned())
    }

    fn wait_engine_command(&self, arguments: &[String], timeout: u64) -> Result<(), GuestError> {
        let deadline = Instant::now() + Duration::from_secs(timeout);
        let mut last_error = None;
        while Instant::now() < deadline {
            match self.engine.run(arguments) {
                Ok(output) if output.status.success() => return Ok(()),
                Ok(output) => {
                    last_error = Some(redact_engine_error(&String::from_utf8_lossy(
                        &output.stderr,
                    )))
                }
                Err(error) => last_error = Some(error),
            }
            thread::sleep(Duration::from_millis(250));
        }
        Err(GuestError::engine(last_error.unwrap_or_else(|| {
            "core service readiness timed out".into()
        })))
    }

    fn ensure_databases(&self) -> Result<(), GuestError> {
        for database in ["lemma", "lemma_datastore", "agentbox", "supertokens"] {
            let query = format!("SELECT 1 FROM pg_database WHERE datname = '{database}'");
            let output = self.run_checked(&[
                "exec".into(),
                "lemma-core-postgres".into(),
                "psql".into(),
                "-U".into(),
                "postgres".into(),
                "-tAc".into(),
                query,
            ])?;
            if output.trim() != "1" {
                self.run_checked(&[
                    "exec".into(),
                    "lemma-core-postgres".into(),
                    "createdb".into(),
                    "-U".into(),
                    "postgres".into(),
                    database.into(),
                ])?;
            }
        }
        Ok(())
    }

    fn wait_tcp(&self, port: u16, timeout: u64) -> Result<(), GuestError> {
        let deadline = Instant::now() + Duration::from_secs(timeout);
        while Instant::now() < deadline {
            if TcpStream::connect_timeout(
                &format!("{}:{port}", self.endpoint_host)
                    .to_socket_addrs()
                    .map_err(|error| GuestError::engine(error.to_string()))?
                    .next()
                    .ok_or_else(|| GuestError::engine("core endpoint did not resolve"))?,
                Duration::from_secs(1),
            )
            .is_ok()
            {
                return Ok(());
            }
            thread::sleep(Duration::from_millis(250));
        }
        Err(GuestError::engine(format!(
            "core TCP port {port} did not become ready"
        )))
    }

    fn wait_http_port(&self, port: u16, path: &str, timeout: u64) -> Result<(), GuestError> {
        let deadline = Instant::now() + Duration::from_secs(timeout);
        let url = format!("http://{}:{port}{path}", self.endpoint_host);
        while Instant::now() < deadline {
            if probe_http(&url).is_ok() {
                return Ok(());
            }
            thread::sleep(Duration::from_millis(250));
        }
        Err(GuestError::engine(format!(
            "core HTTP port {port} did not become ready"
        )))
    }

    fn wait_redis(&self, password: &str, timeout: u64) -> Result<(), GuestError> {
        let deadline = Instant::now() + Duration::from_secs(timeout);
        while Instant::now() < deadline {
            if redis_ping(&self.endpoint_host, password).is_ok() {
                return Ok(());
            }
            thread::sleep(Duration::from_millis(250));
        }
        Err(GuestError::engine("Redis did not become ready"))
    }

    fn ensure(&self, value: Value) -> Result<Value, GuestError> {
        let parameters: EnsureParameters = serde_json::from_value(value)
            .map_err(|error| GuestError::invalid(format!("invalid ensure parameters: {error}")))?;
        validate_sandbox_id(&parameters.sandbox_id)?;
        validate_image(&parameters.image)?;
        validate_apps(&parameters.apps)?;
        validate_environment(&parameters.env)?;

        if let Some(snapshot) = self.snapshot_optional(&parameters.sandbox_id, &parameters.apps)? {
            if snapshot["status"]["status"] != "RUNNING" {
                self.run_checked(&["start".into(), container_name(&parameters.sandbox_id)])?;
            }
        } else {
            let workspace = self.workspace(&parameters.sandbox_id)?;
            let env_file = self.write_env_file(&parameters.sandbox_id, &parameters.env)?;
            let arguments =
                build_run_arguments(&parameters, &workspace, &env_file, &self.host_gateway);
            let result = self.run_checked(&arguments);
            let _ = fs::remove_file(&env_file);
            result?;
        }

        let deadline = Instant::now() + Duration::from_secs(180);
        let mut last_snapshot = None;
        while Instant::now() < deadline {
            match self.snapshot_optional(&parameters.sandbox_id, &parameters.apps)? {
                Some(snapshot)
                    if snapshot["status"]["ready"] == true
                        && eager_apps_healthy(&snapshot, &parameters.apps) =>
                {
                    last_snapshot = Some(snapshot);
                    break;
                }
                snapshot => last_snapshot = snapshot,
            }
            thread::sleep(Duration::from_millis(250));
        }
        let snapshot = last_snapshot.ok_or_else(GuestError::not_found)?;
        if snapshot["status"]["ready"] != true {
            return Err(GuestError::engine("sandbox runtime did not become ready"));
        }
        self.wait_callback(&parameters)?;
        Ok(snapshot)
    }

    fn status(&self, value: Value) -> Result<Value, GuestError> {
        let sandbox_id = required_string(&value, "sandbox_id")?;
        validate_sandbox_id(&sandbox_id)?;
        let apps = default_apps();
        self.snapshot_optional(&sandbox_id, &apps)?
            .ok_or_else(GuestError::not_found)
    }

    fn list(&self) -> Result<Value, GuestError> {
        let output = self.run_checked(&[
            "ps".into(),
            "--all".into(),
            "--filter".into(),
            format!("label={MANAGED_LABEL}"),
            "--format".into(),
            "{{.Names}}".into(),
        ])?;
        let apps = default_apps();
        let mut sandboxes = Vec::new();
        for name in output
            .lines()
            .filter(|line| line.starts_with(CONTAINER_PREFIX))
        {
            let sandbox_id = name.trim().trim_start_matches(CONTAINER_PREFIX);
            if validate_sandbox_id(sandbox_id).is_ok() {
                if let Some(snapshot) = self.snapshot_optional(sandbox_id, &apps)? {
                    sandboxes.push(snapshot);
                }
            }
        }
        Ok(json!({"sandboxes": sandboxes}))
    }

    fn mutate(&self, value: Value, mutation: Mutation) -> Result<Value, GuestError> {
        let sandbox_id = required_string(&value, "sandbox_id")?;
        validate_sandbox_id(&sandbox_id)?;
        let existing = self.snapshot_optional(&sandbox_id, &default_apps())?;
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
                Ok(json!({"purged": existing.is_some()}))
            }
        }
    }

    fn snapshot_optional(
        &self,
        sandbox_id: &str,
        apps: &[AppSpec],
    ) -> Result<Option<Value>, GuestError> {
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
            apps,
            &self.endpoint_host,
        )?))
    }

    fn run_checked(&self, arguments: &[String]) -> Result<String, GuestError> {
        let output = self.engine.run(arguments).map_err(GuestError::engine)?;
        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr);
            return Err(GuestError::engine(redact_engine_error(&stderr)));
        }
        String::from_utf8(output.stdout)
            .map(|value| value.trim().to_owned())
            .map_err(|_| GuestError::engine("container engine returned non-UTF8 output"))
    }

    fn workspace(&self, sandbox_id: &str) -> Result<PathBuf, GuestError> {
        let root = self.state_root.join("workspaces");
        let path = root.join(sandbox_id);
        if path.parent() != Some(root.as_path()) {
            return Err(GuestError::invalid("workspace escaped managed root"));
        }
        fs::create_dir_all(&path).map_err(|error| GuestError::engine(error.to_string()))?;
        fs::set_permissions(&path, fs::Permissions::from_mode(0o700))
            .map_err(|error| GuestError::engine(error.to_string()))?;
        // SAFETY: the path is a freshly validated child of the private managed
        // root and the runtime image's fixed workspace UID/GID is 10001.
        let path_bytes = std::ffi::CString::new(path.as_os_str().as_encoded_bytes())
            .map_err(|_| GuestError::invalid("workspace path contains NUL"))?;
        let result = unsafe { libc::chown(path_bytes.as_ptr(), 10_001, 10_001) };
        if result != 0 {
            return Err(GuestError::engine(io::Error::last_os_error().to_string()));
        }
        Ok(path)
    }

    fn purge_workspace(&self, sandbox_id: &str) -> Result<bool, GuestError> {
        let root = self.state_root.join("workspaces");
        let path = root.join(sandbox_id);
        if path.parent() != Some(root.as_path()) {
            return Err(GuestError::invalid("workspace escaped managed root"));
        }
        if !path.exists() {
            return Ok(false);
        }
        fs::remove_dir_all(path).map_err(|error| GuestError::engine(error.to_string()))?;
        Ok(true)
    }

    fn write_env_file(
        &self,
        sandbox_id: &str,
        environment: &BTreeMap<String, String>,
    ) -> Result<PathBuf, GuestError> {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos();
        let path = self
            .state_root
            .join("run")
            .join(format!("env-{sandbox_id}-{}-{nonce}", std::process::id()));
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&path)
            .map_err(|error| GuestError::engine(error.to_string()))?;
        for (name, value) in environment {
            writeln!(file, "{name}={value}")
                .map_err(|error| GuestError::engine(error.to_string()))?;
        }
        Ok(path)
    }

    fn wait_callback(&self, parameters: &EnsureParameters) -> Result<(), GuestError> {
        if !parameters.callback.required {
            return Ok(());
        }
        let base = parameters
            .env
            .get("LEMMA_BASE_URL")
            .ok_or_else(|| GuestError::invalid("Local sandbox requires LEMMA_BASE_URL"))?;
        let probe_url = callback_probe_url(base, &parameters.callback.health_path)?;
        let deadline = Instant::now()
            + Duration::from_secs_f64(parameters.callback.timeout_seconds.clamp(1.0, 300.0));
        let script = concat!(
            "import sys,urllib.request;",
            "r=urllib.request.urlopen(sys.argv[1],timeout=2);",
            "raise SystemExit(0 if 200<=r.status<300 else 1)"
        );
        let mut last_error = None;
        while Instant::now() < deadline {
            let output = self.engine.run(&[
                "exec".into(),
                container_name(&parameters.sandbox_id),
                "python".into(),
                "-c".into(),
                script.into(),
                probe_url.clone(),
            ]);
            match output {
                Ok(output) if output.status.success() => return Ok(()),
                Ok(output) => {
                    last_error = Some(redact_engine_error(&String::from_utf8_lossy(
                        &output.stderr,
                    )))
                }
                Err(error) => last_error = Some(error),
            }
            thread::sleep(Duration::from_millis(250));
        }
        Err(GuestError::engine(format!(
            "sandbox cannot reach the Lemma API callback: {}",
            last_error.unwrap_or_else(|| "probe timed out".into())
        )))
    }
}

#[derive(Clone, Copy, PartialEq)]
enum Mutation {
    Release,
    Delete,
    PurgeStorage,
    PurgeExact,
}

fn build_run_arguments(
    parameters: &EnsureParameters,
    workspace: &Path,
    env_file: &Path,
    host_gateway: &str,
) -> Vec<String> {
    let mut arguments = vec![
        "run".into(),
        "--detach".into(),
        "--name".into(),
        container_name(&parameters.sandbox_id),
        "--label".into(),
        MANAGED_LABEL.into(),
        "--label".into(),
        format!("agentbox.work/sandbox-id={}", parameters.sandbox_id),
        "--label".into(),
        "agentbox.work/provider=lemma_local".into(),
        "--mount".into(),
        format!("type=bind,src={},dst=/workspace", workspace.display()),
        "--env-file".into(),
        env_file.display().to_string(),
        "--add-host".into(),
        format!("host.lemma.internal:{host_gateway}"),
    ];
    for app in &parameters.apps {
        arguments.extend(["--publish".into(), format!("0.0.0.0::{}", app.port)]);
    }
    if let Some(memory) = parameters
        .resources
        .memory
        .as_deref()
        .filter(|v| !v.is_empty())
    {
        arguments.extend(["--memory".into(), memory.into()]);
    }
    if let Some(cpus) = parameters
        .resources
        .cpus
        .as_deref()
        .filter(|v| !v.is_empty())
    {
        arguments.extend(["--cpus".into(), cpus.into()]);
    }
    arguments.push(parameters.image.clone());
    arguments
}

fn snapshot_from_inspect(
    sandbox_id: &str,
    inspect: &serde_json::Map<String, Value>,
    apps: &[AppSpec],
    endpoint_host: &str,
) -> Result<Value, GuestError> {
    let provider_id = inspect
        .get("Id")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| GuestError::engine("inspect response omitted container ID"))?;
    let state = inspect.get("State").and_then(Value::as_object);
    let running = state
        .and_then(|value| value.get("Running"))
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let state_text = state
        .and_then(|value| value.get("Status"))
        .and_then(Value::as_str)
        .unwrap_or_default();
    let lifecycle = if running {
        "RUNNING"
    } else if matches!(state_text, "created" | "restarting") {
        "CREATING"
    } else if matches!(state_text, "exited" | "stopped" | "removing") {
        "STOPPED"
    } else {
        "ERROR"
    };
    let ports = inspect
        .get("NetworkSettings")
        .and_then(Value::as_object)
        .and_then(|network| network.get("Ports"))
        .and_then(Value::as_object);
    let mut statuses = serde_json::Map::new();
    for app in apps {
        let host_port = ports.and_then(|value| mapped_port(value, app.port));
        statuses.insert(
            app.name.clone(),
            json!({
                "name": app.name,
                "public_slug": app.public_slug,
                "port": app.port,
                "ready": running && host_port.is_some(),
                "private_url": host_port.map(|port| format!("http://{endpoint_host}:{port}")),
            }),
        );
    }
    let runtime_url = statuses
        .get("runtime")
        .and_then(|value| value.get("private_url"))
        .cloned()
        .unwrap_or(Value::Null);
    let ready = running
        && apps
            .iter()
            .filter(|app| app.startup == "eager")
            .all(|app| statuses[&app.name]["ready"] == true);
    Ok(json!({
        "provider_id": provider_id,
        "metadata": {"engine": "containerd", "provider": "lemma_local"},
        "status": {
            "id": sandbox_id,
            "ready": ready,
            "status": lifecycle,
            "runtime_url": runtime_url,
            "pod_ip": if running { Value::String(endpoint_host.into()) } else { Value::Null },
            "apps": statuses,
        }
    }))
}

fn mapped_port(ports: &serde_json::Map<String, Value>, container_port: u16) -> Option<u16> {
    ports
        .get(&format!("{container_port}/tcp"))
        .and_then(Value::as_array)
        .and_then(|bindings| bindings.first())
        .and_then(Value::as_object)
        .and_then(|binding| binding.get("HostPort"))
        .and_then(Value::as_str)
        .and_then(|port| port.parse().ok())
}

fn eager_apps_healthy(snapshot: &Value, apps: &[AppSpec]) -> bool {
    apps.iter().filter(|app| app.startup == "eager").all(|app| {
        snapshot["status"]["apps"][&app.name]["private_url"]
            .as_str()
            .map(|base| {
                let path = if app.health_path.starts_with('/') {
                    app.health_path.clone()
                } else {
                    format!("/{}", app.health_path)
                };
                probe_http(&format!("{}{path}", base.trim_end_matches('/'))).is_ok()
            })
            .unwrap_or(false)
    })
}

fn default_apps() -> Vec<AppSpec> {
    vec![
        AppSpec {
            name: "runtime".into(),
            public_slug: "runtime".into(),
            port: 8080,
            health_path: "/health".into(),
            startup: "eager".into(),
            exposure: "private".into(),
            auth_mode: "manager_api_key".into(),
        },
        AppSpec {
            name: "browser".into(),
            public_slug: "browser".into(),
            port: 4848,
            health_path: "/health".into(),
            startup: "lazy".into(),
            exposure: "workspace_user".into(),
            auth_mode: "workspace_access_token".into(),
        },
        AppSpec {
            name: "function_executor".into(),
            public_slug: "function".into(),
            port: 8090,
            health_path: "/health".into(),
            startup: "eager".into(),
            exposure: "private".into(),
            auth_mode: "manager_api_key".into(),
        },
    ]
}

fn validate_apps(apps: &[AppSpec]) -> Result<(), GuestError> {
    if apps.is_empty() || apps.len() > 16 {
        return Err(GuestError::invalid(
            "apps must contain between 1 and 16 entries",
        ));
    }
    for app in apps {
        if !valid_identifier(&app.name)
            || !valid_identifier(&app.public_slug)
            || app.port == 0
            || !app.health_path.starts_with('/')
            || !matches!(app.startup.as_str(), "eager" | "lazy")
            || !matches!(app.exposure.as_str(), "private" | "workspace_user")
            || !matches!(
                app.auth_mode.as_str(),
                "manager_api_key" | "workspace_access_token"
            )
        {
            return Err(GuestError::invalid(format!(
                "invalid app specification {}",
                app.name
            )));
        }
    }
    Ok(())
}

fn validate_environment(environment: &BTreeMap<String, String>) -> Result<(), GuestError> {
    for (name, value) in environment {
        if name.is_empty()
            || !name
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_')
            || name.as_bytes()[0].is_ascii_digit()
            || value.contains(['\n', '\r', '\0'])
        {
            return Err(GuestError::invalid(format!(
                "invalid environment entry {name:?}"
            )));
        }
    }
    Ok(())
}

fn validate_secret(name: &str, value: &str) -> Result<(), GuestError> {
    if !(16..=512).contains(&value.len()) || value.contains(['\n', '\r', '\0']) {
        return Err(GuestError::invalid(format!(
            "{name} must contain 16 to 512 single-line characters"
        )));
    }
    Ok(())
}

fn write_private_atomic(path: &Path, contents: &[u8]) -> Result<(), GuestError> {
    let parent = path
        .parent()
        .ok_or_else(|| GuestError::engine("private file has no parent directory"))?;
    fs::create_dir_all(parent).map_err(|error| GuestError::engine(error.to_string()))?;
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    let temporary = parent.join(format!(
        ".{}.{}-{nonce}.tmp",
        path.file_name()
            .and_then(|value| value.to_str())
            .unwrap_or("lemma-private"),
        std::process::id()
    ));
    let result = (|| {
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&temporary)
            .map_err(|error| GuestError::engine(error.to_string()))?;
        file.write_all(contents)
            .map_err(|error| GuestError::engine(error.to_string()))?;
        file.sync_all()
            .map_err(|error| GuestError::engine(error.to_string()))?;
        fs::rename(&temporary, path).map_err(|error| GuestError::engine(error.to_string()))?;
        fs::set_permissions(path, fs::Permissions::from_mode(0o600))
            .map_err(|error| GuestError::engine(error.to_string()))
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}

fn redis_ping(host: &str, password: &str) -> io::Result<()> {
    validate_secret("redis_password", password)
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidInput, error.message))?;
    let address = (host, 6379)
        .to_socket_addrs()?
        .next()
        .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "Redis host not found"))?;
    let mut stream = TcpStream::connect_timeout(&address, Duration::from_secs(1))?;
    stream.set_read_timeout(Some(Duration::from_secs(2)))?;
    stream.set_write_timeout(Some(Duration::from_secs(2)))?;
    write!(
        stream,
        "*2\r\n$4\r\nAUTH\r\n${}\r\n{}\r\n*1\r\n$4\r\nPING\r\n",
        password.len(),
        password
    )?;
    stream.flush()?;
    let mut response = [0_u8; 256];
    let count = stream.read(&mut response)?;
    let response = std::str::from_utf8(&response[..count])
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "Redis returned non-UTF8"))?;
    if response.contains("+OK\r\n") && response.contains("+PONG\r\n") {
        Ok(())
    } else {
        Err(io::Error::other("Redis authentication or ping failed"))
    }
}

fn validate_image(image: &str) -> Result<(), GuestError> {
    if image.is_empty()
        || image.len() > 512
        || image.bytes().any(|byte| byte.is_ascii_whitespace())
        || !image.contains("@sha256:")
    {
        return Err(GuestError::invalid(
            "sandbox image must be a digest-pinned OCI reference",
        ));
    }
    Ok(())
}

fn validate_sandbox_id(value: &str) -> Result<(), GuestError> {
    if value.is_empty()
        || value.len() > 63
        || !value.as_bytes()[0].is_ascii_lowercase()
        || !value.as_bytes()[value.len() - 1].is_ascii_alphanumeric()
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-')
    {
        return Err(GuestError::invalid("invalid sandbox_id"));
    }
    Ok(())
}

fn valid_identifier(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 63
        && value
            .bytes()
            .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-')
}

fn required_string(value: &Value, key: &str) -> Result<String, GuestError> {
    value
        .get(key)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
        .ok_or_else(|| GuestError::invalid(format!("missing {key}")))
}

fn callback_probe_url(base: &str, health_path: &str) -> Result<String, GuestError> {
    if !(base.starts_with("http://") || base.starts_with("https://")) {
        return Err(GuestError::invalid("LEMMA_BASE_URL must use HTTP(S)"));
    }
    let path = if health_path.starts_with('/') {
        health_path.to_owned()
    } else {
        format!("/{health_path}")
    };
    Ok(format!("{}{path}", base.trim_end_matches('/')))
}

fn container_name(sandbox_id: &str) -> String {
    format!("{CONTAINER_PREFIX}{sandbox_id}")
}

fn valid_ip(value: &str) -> bool {
    value.parse::<IpAddr>().is_ok_and(|address| match address {
        IpAddr::V4(address) => !address.is_unspecified() && !address.is_multicast(),
        IpAddr::V6(_) => false,
    })
}

fn discover_guest_ip() -> Option<String> {
    command_stdout("ip", &["-4", "-o", "addr", "show", "scope", "global"]).and_then(|output| {
        output.split_whitespace().find_map(|value| {
            value
                .split_once('/')
                .map(|(address, _)| address)
                .filter(|address| valid_ip(address))
                .map(str::to_owned)
        })
    })
}

fn discover_host_gateway() -> Option<String> {
    command_stdout("ip", &["-4", "route", "show", "default"]).and_then(|output| {
        let mut fields = output.split_whitespace();
        while let Some(field) = fields.next() {
            if field == "via" {
                return fields
                    .next()
                    .filter(|value| valid_ip(value))
                    .map(str::to_owned);
            }
        }
        None
    })
}

fn command_stdout(command: &str, args: &[&str]) -> Option<String> {
    let output = Command::new(command).args(args).output().ok()?;
    output
        .status
        .success()
        .then(|| String::from_utf8_lossy(&output.stdout).into_owned())
}

fn load_capability() -> Result<Option<String>, GuestError> {
    let Some(path) = std::env::var_os("LEMMA_GUEST_CAPABILITY_FILE") else {
        return Ok(None);
    };
    let value = fs::read_to_string(path)
        .map_err(|error| GuestError::engine(format!("could not read capability: {error}")))?;
    let value = value.trim();
    if value.len() < 32 || value.len() > 512 {
        return Err(GuestError::engine("guest capability has invalid length"));
    }
    Ok(Some(value.into()))
}

fn redact_engine_error(value: &str) -> String {
    let line = value
        .lines()
        .next()
        .unwrap_or("container engine failed")
        .trim();
    if line.len() > 512 {
        format!("{}…", &line[..512])
    } else {
        line.into()
    }
}

pub fn handle_reader<R: Read, W: Write, E: Engine>(
    reader: R,
    mut writer: W,
    service: &GuestService<E>,
) -> io::Result<bool> {
    let mut bounded = BufReader::new(reader).take(MAX_REQUEST_BYTES + 1);
    let mut line = String::new();
    bounded.read_line(&mut line)?;
    let response = if line.len() as u64 > MAX_REQUEST_BYTES {
        GuestResponse::failure(GuestError::invalid("request exceeded 1 MiB"))
    } else {
        match serde_json::from_str::<GuestRequest>(line.trim_end()) {
            Ok(request) => service.handle(request),
            Err(error) => GuestResponse::failure(GuestError::invalid(format!(
                "invalid request JSON: {error}"
            ))),
        }
    };
    let encoded = serde_json::to_vec(&response)?;
    if encoded.len() > MAX_RESPONSE_BYTES {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "guest response exceeded 4 MiB",
        ));
    }
    writer.write_all(&encoded)?;
    writer.write_all(b"\n")?;
    writer.flush()?;
    Ok(response.ok)
}

#[cfg(target_os = "linux")]
pub fn serve_vsock<E: Engine>(service: &GuestService<E>) -> io::Result<()> {
    use std::mem::{size_of, zeroed};
    use std::os::fd::{FromRawFd, OwnedFd};

    // SAFETY: all libc calls use initialized Linux sockaddr_vm values, checked
    // return codes, and OwnedFd closes each accepted descriptor exactly once.
    unsafe {
        let raw = libc::socket(libc::AF_VSOCK, libc::SOCK_STREAM | libc::SOCK_CLOEXEC, 0);
        if raw < 0 {
            return Err(io::Error::last_os_error());
        }
        let listener = OwnedFd::from_raw_fd(raw);
        let mut address: libc::sockaddr_vm = zeroed();
        address.svm_family = libc::AF_VSOCK as libc::sa_family_t;
        address.svm_cid = libc::VMADDR_CID_ANY;
        address.svm_port = VSOCK_PORT;
        if libc::bind(
            raw,
            &address as *const _ as *const libc::sockaddr,
            size_of::<libc::sockaddr_vm>() as libc::socklen_t,
        ) != 0
        {
            return Err(io::Error::last_os_error());
        }
        if libc::listen(raw, 16) != 0 {
            return Err(io::Error::last_os_error());
        }
        loop {
            let accepted = libc::accept4(
                raw,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                libc::SOCK_CLOEXEC,
            );
            if accepted < 0 {
                let error = io::Error::last_os_error();
                if error.kind() == io::ErrorKind::Interrupted {
                    continue;
                }
                return Err(error);
            }
            let connection = OwnedFd::from_raw_fd(accepted);
            let reader = std::fs::File::from(connection.try_clone()?);
            let writer = std::fs::File::from(connection);
            let _ = handle_reader(reader, writer, service);
        }
        #[allow(unreachable_code)]
        drop(listener);
    }
}

#[cfg(not(target_os = "linux"))]
pub fn serve_vsock<E: Engine>(_service: &GuestService<E>) -> io::Result<()> {
    Err(io::Error::new(
        io::ErrorKind::Unsupported,
        "AF_VSOCK guest service is Linux-only",
    ))
}

pub fn probe_http(url: &str) -> io::Result<()> {
    let remainder = url
        .strip_prefix("http://")
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "probe requires HTTP"))?;
    let (authority, path) = remainder.split_once('/').unwrap_or((remainder, ""));
    let (host, port) = authority
        .rsplit_once(':')
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "probe requires port"))?;
    let port: u16 = port
        .parse()
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "invalid probe port"))?;
    let addresses: Vec<SocketAddr> = (host, port).to_socket_addrs()?.collect();
    let mut stream = TcpStream::connect_timeout(
        addresses
            .first()
            .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "host not found"))?,
        Duration::from_secs(1),
    )?;
    stream.set_read_timeout(Some(Duration::from_secs(2)))?;
    write!(
        stream,
        "GET /{} HTTP/1.1\r\nHost: {}\r\nConnection: close\r\n\r\n",
        path, authority
    )?;
    let mut response = [0_u8; 64];
    let count = stream.read(&mut response)?;
    let status = std::str::from_utf8(&response[..count])
        .ok()
        .and_then(|value| value.lines().next())
        .and_then(|line| line.split_whitespace().nth(1))
        .and_then(|value| value.parse::<u16>().ok())
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "invalid HTTP response"))?;
    if (200..500).contains(&status) {
        Ok(())
    } else {
        Err(io::Error::other(format!("health returned {status}")))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::process::ExitStatusExt;
    use std::sync::Mutex;
    use tempfile::tempdir;

    struct FakeEngine {
        commands: Mutex<Vec<Vec<String>>>,
        outputs: Mutex<Vec<Output>>,
    }

    impl FakeEngine {
        fn new(outputs: Vec<Output>) -> Self {
            Self {
                commands: Mutex::new(Vec::new()),
                outputs: Mutex::new(outputs.into_iter().rev().collect()),
            }
        }
    }

    impl Engine for FakeEngine {
        fn run(&self, arguments: &[String]) -> Result<Output, String> {
            self.commands.lock().unwrap().push(arguments.to_vec());
            self.outputs
                .lock()
                .unwrap()
                .pop()
                .ok_or_else(|| "no fake output".into())
        }
    }

    fn output(success: bool, stdout: &str) -> Output {
        Output {
            status: std::process::ExitStatus::from_raw(if success { 0 } else { 1 }),
            stdout: stdout.as_bytes().to_vec(),
            stderr: if success {
                vec![]
            } else {
                b"not found".to_vec()
            },
        }
    }

    fn inspect() -> String {
        json!([{
            "Id": "sha256:exact-generation",
            "State": {"Running": true, "Status": "running"},
            "NetworkSettings": {"Ports": {
                "8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": "49152"}],
                "4848/tcp": [{"HostIp": "0.0.0.0", "HostPort": "49153"}],
                "8090/tcp": [{"HostIp": "0.0.0.0", "HostPort": "49154"}]
            }}
        }])
        .to_string()
    }

    #[test]
    fn snapshot_uses_guest_ip_and_exact_container_generation() {
        let parsed: Value = serde_json::from_str(&inspect()).unwrap();
        let snapshot = snapshot_from_inspect(
            "box-1",
            parsed[0].as_object().unwrap(),
            &default_apps(),
            "192.168.64.2",
        )
        .unwrap();

        assert_eq!(snapshot["provider_id"], "sha256:exact-generation");
        assert_eq!(
            snapshot["status"]["runtime_url"],
            "http://192.168.64.2:49152"
        );
        assert_eq!(snapshot["status"]["ready"], true);
    }

    #[test]
    fn run_contract_uses_digest_env_file_private_gateway_and_all_app_ports() {
        let parameters = EnsureParameters {
            sandbox_id: "box-1".into(),
            image: "ghcr.io/lemma/runtime@sha256:abc".into(),
            env: BTreeMap::from([("LEMMA_TOKEN".into(), "secret".into())]),
            apps: default_apps(),
            resources: ResourceSpec {
                memory: Some("2Gi".into()),
                cpus: Some("1".into()),
            },
            callback: CallbackSpec::default(),
        };
        let arguments = build_run_arguments(
            &parameters,
            Path::new("/var/lib/lemma/workspaces/box-1"),
            Path::new("/var/lib/lemma/run/private-env"),
            "192.168.64.1",
        );
        let joined = arguments.join(" ");

        assert!(joined.contains("--env-file /var/lib/lemma/run/private-env"));
        assert!(!joined.contains("secret"));
        assert!(joined.contains("host.lemma.internal:192.168.64.1"));
        assert!(joined.contains("0.0.0.0::8080"));
        assert!(joined.contains("0.0.0.0::4848"));
        assert!(joined.contains("0.0.0.0::8090"));
        assert!(joined.ends_with("ghcr.io/lemma/runtime@sha256:abc"));
    }

    #[test]
    fn protocol_requires_capability_and_rejects_tags() {
        let root = tempdir().unwrap();
        let service = GuestService::new(
            FakeEngine::new(vec![]),
            root.path().into(),
            "192.168.64.2".into(),
            "192.168.64.1".into(),
            Some("a".repeat(32)),
        )
        .unwrap();
        let unauthorized = service.handle(GuestRequest {
            version: 1,
            capability: None,
            operation: "health".into(),
            parameters: json!({}),
        });
        let invalid_image = service.handle(GuestRequest {
            version: 1,
            capability: Some("a".repeat(32)),
            operation: "sandbox.ensure".into(),
            parameters: json!({
                "sandbox_id": "box-1", "image": "runtime:latest",
                "apps": [],
            }),
        });

        assert_eq!(unauthorized.error.unwrap().code, "unauthorized");
        assert_eq!(invalid_image.error.unwrap().code, "invalid_request");
    }

    #[test]
    fn status_and_exact_purge_fail_closed() {
        let root = tempdir().unwrap();
        let service = GuestService::new(
            FakeEngine::new(vec![output(true, &inspect()), output(true, &inspect())]),
            root.path().into(),
            "192.168.64.2".into(),
            "192.168.64.1".into(),
            None,
        )
        .unwrap();
        let status = service.handle(GuestRequest {
            version: 1,
            capability: None,
            operation: "sandbox.status".into(),
            parameters: json!({"sandbox_id": "box-1"}),
        });
        let conflict = service.handle(GuestRequest {
            version: 1,
            capability: None,
            operation: "sandbox.purge".into(),
            parameters: json!({
                "sandbox_id": "box-1", "provider_id": "different"
            }),
        });

        assert_eq!(status.result.unwrap()["status"]["ready"], true);
        assert_eq!(conflict.error.unwrap().code, "generation_conflict");
    }

    #[test]
    fn bounded_json_transport_returns_one_response() {
        let root = tempdir().unwrap();
        let service = GuestService::new(
            FakeEngine::new(vec![]),
            root.path().into(),
            "192.168.64.2".into(),
            "192.168.64.1".into(),
            None,
        )
        .unwrap();
        let mut output = Vec::new();
        let ok = handle_reader(
            br#"{"version":1,"operation":"health","parameters":{}}
"#
            .as_slice(),
            &mut output,
            &service,
        )
        .unwrap();

        assert!(ok);
        let response: Value = serde_json::from_slice(&output).unwrap();
        assert_eq!(response["result"]["engine"], "containerd");
    }

    #[test]
    fn missing_sandbox_is_not_found() {
        let root = tempdir().unwrap();
        let service = GuestService::new(
            FakeEngine::new(vec![output(false, "")]),
            root.path().into(),
            "192.168.64.2".into(),
            "192.168.64.1".into(),
            None,
        )
        .unwrap();
        let response = service.handle(GuestRequest {
            version: 1,
            capability: None,
            operation: "sandbox.status".into(),
            parameters: json!({"sandbox_id": "box-1"}),
        });

        assert_eq!(response.error.unwrap().code, "not_found");
    }

    #[test]
    fn core_secrets_are_validated_before_engine_operations() {
        let root = tempdir().unwrap();
        let service = GuestService::new(
            FakeEngine::new(vec![]),
            root.path().into(),
            "192.168.64.2".into(),
            "192.168.64.1".into(),
            None,
        )
        .unwrap();
        let response = service.handle(GuestRequest {
            version: 1,
            capability: None,
            operation: "core.ensure".into(),
            parameters: json!({
                "images": {
                    "postgres": "postgres@sha256:abc",
                    "redis": "redis@sha256:def",
                    "supertokens": "supertokens@sha256:123"
                },
                "credentials": {
                    "postgres_password": "too-short",
                    "redis_password": "also-too-short"
                }
            }),
        });

        assert_eq!(response.error.unwrap().code, "invalid_request");
        assert!(service.engine.commands.lock().unwrap().is_empty());
    }

    #[test]
    fn atomic_private_files_replace_contents_and_remain_owner_only() {
        let root = tempdir().unwrap();
        let path = root.path().join("redis.conf");
        write_private_atomic(&path, b"first").unwrap();
        write_private_atomic(&path, b"second").unwrap();

        assert_eq!(fs::read(&path).unwrap(), b"second");
        assert_eq!(
            fs::metadata(&path).unwrap().permissions().mode() & 0o777,
            0o600
        );
        assert_eq!(fs::read_dir(root.path()).unwrap().count(), 1);
    }
}
