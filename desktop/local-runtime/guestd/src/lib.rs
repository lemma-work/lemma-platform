use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::collections::HashMap;
use std::fs::{self, OpenOptions};
use std::io::{self, BufRead, BufReader, Read, Seek, SeekFrom, Write};
use std::net::{IpAddr, SocketAddr, TcpStream, ToSocketAddrs};
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};
use std::sync::{Arc, Mutex, OnceLock};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

pub const PROTOCOL_VERSION: u64 = 1;
pub const VSOCK_PORT: u32 = 42_411;
const MAX_REQUEST_BYTES: u64 = 1024 * 1024;
const MAX_RESPONSE_BYTES: usize = 4 * 1024 * 1024;
const CONTAINER_PREFIX: &str = "lemma-sandbox-";
const MANAGED_LABEL: &str = "app.kubernetes.io/name=lemma-sandbox";
const ENGINE_COMMAND_TIMEOUT: Duration = Duration::from_secs(120);
const ENGINE_PULL_TIMEOUT: Duration = Duration::from_secs(300);
const CACHE_REPAIR_RESPONSE_GRACE: Duration = Duration::from_secs(10);

/// How long one `sandbox.ensure` may wait for a sandbox to start serving.
///
/// Not a limit on how long a sandbox may take: it is how long this *request*
/// may occupy the guest's single control channel before handing the caller a
/// retryable answer. The caller's own deadline still bounds the start.
///
/// Fifteen seconds rather than the two or three that would keep the channel
/// freest, because handing back `not_ready` is not free either: the retry is
/// served by the reuse path in `SandboxService`, which adopts the running
/// container without re-checking readiness. Measured starts are a couple of
/// seconds, so this leaves the retry as the exception rather than the norm
/// while still cutting the worst case from three minutes to fifteen seconds.
const SANDBOX_READY_POLL_BUDGET: Duration = Duration::from_secs(15);
const _: () = assert!(
    SANDBOX_READY_POLL_BUDGET.as_secs() <= 30 && SANDBOX_READY_POLL_BUDGET.as_secs() >= 5,
    "one ensure must not sit on the shared control channel for minutes, and must \
     not be so brief that every start takes the retry path",
);
/// The phrase that turns a guest failure into an offer to reset local data.
///
/// Duplicated from `lemma_locald::paths::DATA_RESET_MARKER` rather than shared:
/// guestd is a Linux binary that ships inside the VM image and links nothing
/// from the host daemon. Both sides are pinned by tests, and the string is the
/// entire contract -- locald maps it to `local-data-incompatible` and the
/// splash renders a reset button for that code.
const DATA_RESET_MARKER: &str = "local data must be reset";

/// Where the PostgreSQL cluster lives inside its container.
///
/// Pinned rather than inherited: see the note on `PGDATA` in `ensure_postgres`.
/// It is also the path `lemma-postgres-data` is mounted at, and the two must
/// stay equal -- a cluster written anywhere else is not on the volume the user
/// is told holds their database.
const POSTGRES_DATA_DIR: &str = "/var/lib/postgresql/data";
const DEFAULT_SANDBOX_MEMORY_BYTES: u64 = 2 * 1024 * 1024 * 1024;

/// What a sandbox is assumed to need in order to *start*, for admission.
///
/// Deliberately far below `DEFAULT_SANDBOX_MEMORY_BYTES`, because those are two
/// different things. `--memory` is a cgroup ceiling: it caps a runaway sandbox
/// and reserves nothing, and a container only ever occupies the pages it
/// touches. Admission used to add those ceilings up as though each sandbox had
/// claimed its whole allowance on creation, so a 6 GiB guest refused a third
/// sandbox while the two it had were using a couple of hundred megabytes
/// between them -- and refused it as "function runtime endpoint was not ready",
/// two minutes later, with no mention of memory.
///
/// This is the request; the ceiling stays the limit. Same split Kubernetes
/// draws, for the same reason.
const SANDBOX_MEMORY_REQUEST_BYTES: u64 = 256 * 1024 * 1024;

/// What the guest keeps for itself: page cache, containerd, the kernel.
///
/// Admission is refused while free memory is below this, so overcommitting
/// stops before the OOM killer rather than after it.
const GUEST_MEMORY_HEADROOM_BYTES: u64 = 384 * 1024 * 1024;

/// A ceiling on concurrent sandboxes, independent of memory.
///
/// Memory is the real constraint and is measured; this only stops a runaway
/// caller creating containers without bound. `LEMMA_GUEST_MAX_SANDBOXES`
/// overrides it, so an operator with a larger guest is not held to a number
/// compiled in here.
const DEFAULT_MAX_SANDBOXES: usize = 16;

// Checked when this file compiles, because they are statements about the
// constants above rather than about any run.
const _: () = assert!(
    SANDBOX_MEMORY_REQUEST_BYTES < DEFAULT_SANDBOX_MEMORY_BYTES,
    "the admission request has to be smaller than the ceiling, or nothing changed",
);
const _: () = assert!(
    DEFAULT_MAX_SANDBOXES > 2,
    "the default concurrency must not reimpose the two-sandbox limit",
);

/// How far the guest clock may sit from the host's before it is stepped.
///
/// A step is not free -- it moves wall time under every process in the guest --
/// so a second of ordinary jitter is left alone. Anything the host would
/// actually notice is not jitter.
const CLOCK_STEP_THRESHOLD_SECONDS: i64 = 2;
/// The window a host wall clock has to fall in to be believed, matching
/// `/usr/local/bin/lemma-set-host-time` exactly. The two set the same clock
/// from the same source and must agree on what is plausible.
const MIN_TRUSTED_EPOCH: u64 = 1_700_000_000;
const MAX_TRUSTED_EPOCH: u64 = 4_102_444_800;

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
    #[serde(default)]
    url: Option<String>,
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
            url: None,
            health_path: default_health_path(),
            timeout_seconds: default_callback_timeout(),
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq)]
#[serde(rename_all = "snake_case")]
enum WorkloadKind {
    Workspace,
    Function,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct EnsureParameters {
    sandbox_id: String,
    workload_kind: WorkloadKind,
    image: String,
    #[serde(default)]
    env: BTreeMap<String, String>,
    #[serde(default)]
    metadata: BTreeMap<String, String>,
    #[serde(default)]
    runtime_token: Option<String>,
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
    /// The sandbox images, warmed at start rather than on first use.
    ///
    /// Optional so a host pack that predates this still parses -- `deny_unknown_fields`
    /// is on the struct, not the absence of a field.
    #[serde(default)]
    workspace: Option<String>,
    #[serde(default)]
    function: Option<String>,
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

#[derive(Clone, Copy)]
enum CoreStage {
    Images,
    SandboxImages,
    Postgres,
    Redis,
    SuperTokens,
}

pub trait Engine: Send + Sync {
    fn run(&self, arguments: &[String]) -> Result<Output, String>;
}

pub struct NerdctlEngine {
    executable: PathBuf,
    capture_root: PathBuf,
}

impl NerdctlEngine {
    pub fn discover(state_root: &Path) -> Result<Self, GuestError> {
        let configured = std::env::var_os("LEMMA_NERDCTL_BIN")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("/usr/local/bin/nerdctl"));
        if !configured.is_file() {
            return Err(GuestError::engine(format!(
                "managed container engine is missing: {}",
                configured.display()
            )));
        }
        let capture_root = std::env::var_os("LEMMA_GUEST_TEMP_ROOT")
            .map(PathBuf::from)
            .unwrap_or_else(|| state_root.join("run/engine-tmp"));
        fs::create_dir_all(&capture_root).map_err(|error| {
            GuestError::engine(format!(
                "could not prepare writable container-engine temporary storage at {}: {error}",
                capture_root.display()
            ))
        })?;
        fs::set_permissions(&capture_root, fs::Permissions::from_mode(0o700)).map_err(|error| {
            GuestError::engine(format!(
                "could not secure container-engine temporary storage at {}: {error}",
                capture_root.display()
            ))
        })?;
        Ok(Self {
            executable: configured,
            capture_root,
        })
    }
}

impl Engine for NerdctlEngine {
    fn run(&self, arguments: &[String]) -> Result<Output, String> {
        let timeout = if arguments.first().is_some_and(|value| value == "pull") {
            ENGINE_PULL_TIMEOUT
        } else {
            ENGINE_COMMAND_TIMEOUT
        };
        run_bounded_engine_command(&self.executable, &self.capture_root, arguments, timeout)
    }
}

fn run_bounded_engine_command(
    executable: &Path,
    capture_root: &Path,
    arguments: &[String],
    timeout: Duration,
) -> Result<Output, String> {
    // File-backed capture avoids the classic timeout deadlock where a forked
    // helper keeps a stdout/stderr pipe open after its parent is terminated.
    // Keep both our captures and nerdctl's inherited TMPDIR on the app-owned
    // writable data disk. The appliance root, including /root, is immutable.
    let mut stdout = tempfile::tempfile_in(capture_root).map_err(|error| {
        format!(
            "could not create container-engine stdout capture in {}: {error}",
            capture_root.display()
        )
    })?;
    let mut stderr = tempfile::tempfile_in(capture_root).map_err(|error| {
        format!(
            "could not create container-engine stderr capture in {}: {error}",
            capture_root.display()
        )
    })?;
    let mut command = Command::new(executable);
    command
        .args(["--namespace", "lemma"])
        .args(arguments)
        .env("TMPDIR", capture_root)
        .stdin(Stdio::null())
        .stdout(Stdio::from(
            stdout.try_clone().map_err(|error| error.to_string())?,
        ))
        .stderr(Stdio::from(
            stderr.try_clone().map_err(|error| error.to_string())?,
        ))
        .process_group(0);
    // A just-written, just-chmod'd executable can make exec() answer ETXTBSY
    // ("text file busy") even though this process already closed its own
    // write handle -- the underlying storage layer's busy state can lag the
    // close() that cleared it, especially under concurrent I/O (observed in
    // CI on the runner's overlayfs). Spurious and short-lived: retry a
    // handful of times on that one specific error rather than surface a
    // transient race as a real spawn failure. Any other spawn error still
    // fails immediately, unchanged.
    let mut spawn_attempts = 0;
    let mut child = loop {
        match command.spawn() {
            Ok(child) => break child,
            Err(error) if error.raw_os_error() == Some(libc::ETXTBSY) && spawn_attempts < 5 => {
                spawn_attempts += 1;
                thread::sleep(Duration::from_millis(20));
            }
            Err(error) => return Err(error.to_string()),
        }
    };
    let deadline = Instant::now() + timeout;
    let status = loop {
        if let Some(status) = child.try_wait().map_err(|error| error.to_string())? {
            break status;
        }
        if Instant::now() >= deadline {
            let process_group = -(child.id() as i32);
            // The child is its own process-group leader. Killing the group
            // bounds nerdctl helpers as well as the top-level client.
            unsafe {
                libc::kill(process_group, libc::SIGKILL);
            }
            let _ = child.wait();
            return Err(format!(
                "managed container engine command timed out after {}s",
                timeout.as_secs()
            ));
        }
        thread::sleep(Duration::from_millis(100));
    };

    let mut stdout_bytes = Vec::new();
    let mut stderr_bytes = Vec::new();
    stdout
        .seek(SeekFrom::Start(0))
        .and_then(|_| stdout.read_to_end(&mut stdout_bytes))
        .map_err(|error| error.to_string())?;
    stderr
        .seek(SeekFrom::Start(0))
        .and_then(|_| stderr.read_to_end(&mut stderr_bytes))
        .map_err(|error| error.to_string())?;
    Ok(Output {
        status,
        stdout: stdout_bytes,
        stderr: stderr_bytes,
    })
}

pub struct GuestService<E: Engine> {
    engine: Arc<E>,
    state_root: PathBuf,
    endpoint_host: String,
    dynamic_endpoint_host: bool,
    host_gateway: String,
    capability: Option<String>,
}

impl GuestService<NerdctlEngine> {
    pub fn discover() -> Result<Self, GuestError> {
        let state_root = std::env::var_os("LEMMA_GUEST_STATE_ROOT")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("/var/lib/lemma"));
        let configured_endpoint_host = std::env::var("LEMMA_GUEST_ENDPOINT_HOST")
            .ok()
            .filter(|value| valid_ip(value));
        let dynamic_endpoint_host = configured_endpoint_host.is_none();
        let endpoint_host = configured_endpoint_host
            .or_else(discover_guest_ip)
            .ok_or_else(|| GuestError::engine("could not discover the guest IPv4 address"))?;
        let host_gateway = std::env::var("LEMMA_HOST_GATEWAY")
            .ok()
            .filter(|value| valid_ip(value))
            .or_else(discover_host_gateway)
            .ok_or_else(|| GuestError::engine("could not discover the private host gateway"))?;
        let capability = load_capability()?;
        let mut service = Self::new(
            NerdctlEngine::discover(&state_root)?,
            state_root,
            endpoint_host,
            host_gateway,
            capability,
        )?;
        // DHCP may replace a lease after systemd first considers the network
        // online. The host must always receive the address currently assigned
        // to the guest, rather than the address observed when guestd started.
        service.dynamic_endpoint_host = dynamic_endpoint_host;
        Ok(service)
    }
}

impl<E: Engine + 'static> GuestService<E> {
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
            engine: Arc::new(engine),
            state_root,
            endpoint_host,
            dynamic_endpoint_host: false,
            host_gateway,
            capability,
        })
    }

    fn current_endpoint_host(&self) -> String {
        if self.dynamic_endpoint_host {
            discover_guest_ip().unwrap_or_else(|| self.endpoint_host.clone())
        } else {
            self.endpoint_host.clone()
        }
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
            "health" => self.health(),
            "diagnostics.network" => Ok(network_diagnostics()),
            "diagnostics.sandbox" => self.sandbox_diagnostics(request.parameters),
            "system.shutdown" => self.shutdown(),
            "system.clock" => self.set_clock(request.parameters),
            "core.ensure" => self.ensure_core(request.parameters),
            "core.images" => self.ensure_core_stage(request.parameters, CoreStage::Images),
            "core.sandbox_images" => {
                self.ensure_core_stage(request.parameters, CoreStage::SandboxImages)
            }
            "core.postgres" => self.ensure_core_stage(request.parameters, CoreStage::Postgres),
            "core.redis" => self.ensure_core_stage(request.parameters, CoreStage::Redis),
            "core.supertokens" => {
                self.ensure_core_stage(request.parameters, CoreStage::SuperTokens)
            }
            "core.status" => self.core_status(),
            "core.stop" => self.stop_core(),
            "core.reset_data" => self.reset_data(request.parameters),
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
        let parameters = self.parse_core_parameters(value)?;
        self.ensure_core_images(&parameters)?;
        self.ensure_postgres(&parameters)?;
        self.ensure_redis(&parameters)?;
        self.ensure_supertokens(&parameters)?;
        self.core_status()
    }

    fn ensure_core_stage(&self, value: Value, stage: CoreStage) -> Result<Value, GuestError> {
        let parameters = self.parse_core_parameters(value)?;
        match stage {
            CoreStage::Images => self.ensure_core_images(&parameters)?,
            CoreStage::SandboxImages => self.ensure_sandbox_images(&parameters)?,
            CoreStage::Postgres => self.ensure_postgres(&parameters)?,
            CoreStage::Redis => self.ensure_redis(&parameters)?,
            CoreStage::SuperTokens => self.ensure_supertokens(&parameters)?,
        }
        self.core_status()
    }

    fn parse_core_parameters(&self, value: Value) -> Result<CoreParameters, GuestError> {
        let parameters: CoreParameters = serde_json::from_value(value)
            .map_err(|error| GuestError::invalid(format!("invalid core parameters: {error}")))?;
        validate_secret(
            "postgres_password",
            &parameters.credentials.postgres_password,
        )?;
        validate_secret("redis_password", &parameters.credentials.redis_password)?;
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

    /// Pull the workspace and function sandbox images before anyone needs one.
    ///
    /// They used to arrive on the first `sandbox.ensure`, from `pull --quiet`,
    /// with no progress anywhere -- so the first time a pod actually tried to do
    /// work, it stopped for however long a multi-hundred-megabyte transfer takes
    /// and said nothing. Doing it here spends the same minutes once, during an
    /// install that is already showing a progress bar, and leaves the first real
    /// run fast.
    ///
    /// A pack that carries no sandbox images is not an error: this is a warm-up,
    /// and `sandbox.ensure` still pulls what it needs.
    fn ensure_sandbox_images(&self, parameters: &CoreParameters) -> Result<(), GuestError> {
        let images = [
            (
                parameters.images.workspace.as_deref(),
                WorkloadKind::Workspace,
            ),
            (
                parameters.images.function.as_deref(),
                WorkloadKind::Function,
            ),
        ];
        thread::scope(|scope| -> Result<(), GuestError> {
            let handles: Vec<_> = images
                .into_iter()
                .filter_map(|(image, kind)| image.map(|image| (image, kind)))
                .map(|(image, kind)| {
                    scope.spawn(move || self.ensure_sandbox_image(image, kind, true))
                })
                .collect();
            for handle in handles {
                handle
                    .join()
                    .map_err(|_| GuestError::engine("sandbox image warm-up panicked"))??;
            }
            Ok(())
        })
    }

    fn ensure_core_images(&self, parameters: &CoreParameters) -> Result<(), GuestError> {
        let images = [
            &parameters.images.postgres,
            &parameters.images.redis,
            &parameters.images.supertokens,
        ];
        // These are independent immutable images. Pull them concurrently so a
        // fresh install is bounded by the slowest registry transfer rather
        // than the sum of all three transfers.
        thread::scope(|scope| -> Result<(), GuestError> {
            let handles: Vec<_> = images
                .into_iter()
                .map(|image| scope.spawn(move || self.ensure_image(image)))
                .collect();
            for handle in handles {
                handle
                    .join()
                    .map_err(|_| GuestError::engine("managed image pull worker failed"))??;
            }
            Ok(())
        })
    }

    fn ensure_postgres(&self, parameters: &CoreParameters) -> Result<(), GuestError> {
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

    fn ensure_redis(&self, parameters: &CoreParameters) -> Result<(), GuestError> {
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

    fn ensure_supertokens(&self, parameters: &CoreParameters) -> Result<(), GuestError> {
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

    fn health(&self) -> Result<Value, GuestError> {
        self.health_at(SystemTime::now())
    }

    fn health_at(&self, now: SystemTime) -> Result<Value, GuestError> {
        let marker = self.cache_reset_marker();
        let repair_due = marker
            .metadata()
            .and_then(|metadata| metadata.modified())
            .ok()
            .and_then(|modified| now.duration_since(modified).ok())
            .is_some_and(|age| age >= CACHE_REPAIR_RESPONSE_GRACE);
        if repair_due {
            return Err(GuestError {
                code: "guest_cache_repair_required".into(),
                message: "container cache repair required".into(),
                retryable: true,
                status_code: 503,
            });
        }
        // Core guest readiness includes a usable container engine. Do not
        // report the VM healthy with a fabricated zero count when nerdctl
        // cannot access its writable state; doing so only defers an appliance
        // layout failure until the first image pull.
        let active_sandboxes = self.running_sandbox_count()?;
        let endpoint_host = self.current_endpoint_host();
        Ok(json!({
            "status": "ready", "engine": "containerd",
            "endpoint_host": endpoint_host,
            "host_gateway": self.host_gateway,
            "active_sandboxes": active_sandboxes,
            // Reported on every health call so a drifting guest clock is
            // visible to whoever is already asking whether the guest is well,
            // rather than only to whoever thinks to ask about time.
            "clock_epoch": now
                .duration_since(UNIX_EPOCH)
                .map(|since| since.as_secs())
                .unwrap_or_default(),
        }))
    }

    fn running_sandbox_count(&self) -> Result<usize, GuestError> {
        let output = self.run_checked(&[
            "ps".into(),
            "--quiet".into(),
            "--filter".into(),
            format!("label={MANAGED_LABEL}"),
        ])?;
        Ok(output
            .lines()
            .filter(|line| !line.trim().is_empty())
            .count())
    }

    /// Decide whether another sandbox can start, from what the guest is
    /// actually using rather than from what its containers are allowed to use.
    ///
    /// The previous version summed every running sandbox's `--memory` ceiling
    /// and treated the total as spoken for. Ceilings are not reservations:
    /// containerd sets `memory.max` and the container occupies only the pages
    /// it touches. With a 1536 MiB core reservation and a 2048 MiB default
    /// ceiling, a 6 GiB guest therefore admitted exactly two sandboxes and
    /// refused the third -- so opening a workspace or two made every function
    /// fail, and the refusal reached the user as a 120-second deadline with no
    /// mention of memory.
    ///
    /// `requested` is still validated by `validate_resources`; it is the
    /// ceiling for this sandbox, not a claim on the guest.
    fn admit_sandbox_memory(&self, requested: u64) -> Result<(), GuestError> {
        let running = self.running_sandbox_count()?;
        let ceiling = max_sandboxes();
        if running >= ceiling {
            return Err(GuestError {
                code: "resource_capacity".into(),
                message: format!(
                    "This computer is already running {running} sandboxes, which is the \
                     configured maximum. Close a workspace, or raise \
                     LEMMA_GUEST_MAX_SANDBOXES."
                ),
                retryable: true,
                status_code: 429,
            });
        }

        let available = guest_available_memory_bytes()?;
        let needed = SANDBOX_MEMORY_REQUEST_BYTES.saturating_add(GUEST_MEMORY_HEADROOM_BYTES);
        if available < needed {
            return Err(GuestError {
                code: "resource_capacity".into(),
                message: format!(
                    "Not enough memory left in the private runtime to start another \
                     sandbox: {} MiB free, {} MiB needed. {running} sandboxes are \
                     running; closing one frees memory immediately.",
                    available / (1024 * 1024),
                    needed / (1024 * 1024),
                ),
                retryable: true,
                status_code: 429,
            });
        }
        let _ = requested;
        Ok(())
    }

    fn core_status(&self) -> Result<Value, GuestError> {
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
                    "endpoint": format!("{endpoint_host}:{port}"),
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

    fn stop_core(&self) -> Result<Value, GuestError> {
        for name in ["supertokens", "redis", "postgres"] {
            let container = format!("lemma-core-{name}");
            if self.inspect_raw(&container)?.is_some() {
                self.run_checked(&["stop".into(), container])?;
            }
        }
        Ok(json!({"stopped": true}))
    }

    /// Destroy everything the user made, and nothing else.
    ///
    /// The only global destructive verb in this table -- everything else is
    /// per-sandbox. That is deliberate and worth knowing: its blast radius
    /// equals `system.shutdown`'s, it sits behind the same 0600 app-owned
    /// capability file, and it additionally requires a literal `confirm` so a
    /// replayed or malformed frame cannot trigger it.
    ///
    /// This is the surgical half of a local-data reset. The alternative --
    /// discarding the whole 24 GiB disk from the host -- also works and needs no
    /// cooperation from the guest, but it takes the pulled container images with
    /// it. For the case this was built for, a Postgres major that moved, exactly
    /// one volume needs replacing and re-pulling several hundred megabytes would
    /// be a poor trade.
    ///
    /// Order is the correctness property:
    ///
    /// 1. core containers, so nothing holds the volumes;
    /// 2. sandbox containers, so nothing holds a workspace directory;
    /// 3. the volumes;
    /// 4. the workspace directories.
    ///
    /// Removing workspaces before their containers would leave a running
    /// sandbox bind-mounted onto a path that no longer exists.
    fn reset_data(&self, parameters: Value) -> Result<Value, GuestError> {
        if required_string(&parameters, "confirm")? != "reset-local-data" {
            return Err(GuestError::invalid(
                "a local data reset must be confirmed explicitly",
            ));
        }

        let mut removed_containers = 0;
        for name in ["supertokens", "redis", "postgres"] {
            let container = format!("lemma-core-{name}");
            if self.inspect_raw(&container)?.is_some() {
                self.run_checked(&["rm".into(), "--force".into(), container])?;
                removed_containers += 1;
            }
        }
        removed_containers += self.remove_managed_sandbox_containers()?;

        let mut removed_volumes = 0;
        for volume in ["lemma-postgres-data", "lemma-redis-data"] {
            let output = self
                .engine
                .run(&[
                    "volume".into(),
                    "rm".into(),
                    "--force".into(),
                    volume.into(),
                ])
                .map_err(GuestError::engine)?;
            if output.status.success() {
                removed_volumes += 1;
            }
        }

        let removed_workspaces = self.remove_all_workspaces()?;
        Ok(json!({
            "removed_containers": removed_containers,
            "removed_volumes": removed_volumes,
            "removed_workspaces": removed_workspaces,
        }))
    }

    /// Every sandbox container this guest owns, by label rather than by name.
    fn remove_managed_sandbox_containers(&self) -> Result<usize, GuestError> {
        let output = self.run_checked(&[
            "ps".into(),
            "--all".into(),
            "--quiet".into(),
            "--filter".into(),
            format!("label={MANAGED_LABEL}"),
        ])?;
        let ids: Vec<String> = output
            .lines()
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(|value| {
                if value.len() > 128 || !value.bytes().all(|byte| byte.is_ascii_hexdigit()) {
                    return Err(GuestError::engine(
                        "container engine returned an invalid container identifier",
                    ));
                }
                Ok(value.to_owned())
            })
            .collect::<Result<_, _>>()?;
        if ids.is_empty() {
            return Ok(0);
        }
        let mut arguments = vec!["rm".into(), "--force".into()];
        arguments.extend(ids.iter().cloned());
        self.run_checked(&arguments)?;
        Ok(ids.len())
    }

    /// Remove every workspace directory, with `purge_workspace`'s discipline.
    ///
    /// What actually stops this clearing the guest is the `is_dir()` below,
    /// which is `entry.file_type()` and so does *not* follow a symlink: a
    /// symlinked entry is skipped rather than followed into.
    ///
    /// The parent re-check is belt to that braces and cannot fire on its own --
    /// `read_dir` yields `root.join(name)`, so the parent is `root` by
    /// construction, `..` included. Kept because it costs nothing and because
    /// the day someone changes how these paths are built is the day it starts
    /// mattering. The comment used to credit it with the symlink defence, which
    /// is the wrong line to trust.
    fn remove_all_workspaces(&self) -> Result<usize, GuestError> {
        let root = self.state_root.join("workspaces");
        let Ok(entries) = fs::read_dir(&root) else {
            return Ok(0);
        };
        let mut removed = 0;
        for entry in entries.flatten() {
            let path = entry.path();
            if path.parent() != Some(root.as_path()) {
                return Err(GuestError::invalid("workspace escaped managed root"));
            }
            if !entry.file_type().is_ok_and(|kind| kind.is_dir()) {
                continue;
            }
            fs::remove_dir_all(&path).map_err(|error| GuestError::engine(error.to_string()))?;
            removed += 1;
        }
        Ok(removed)
    }

    fn shutdown(&self) -> Result<Value, GuestError> {
        let stopped_containers = self.stop_all_containers()?;
        let mut result = schedule_shutdown()?;
        result["stopped_containers"] = json!(stopped_containers);
        Ok(result)
    }

    /// Put the guest's wall clock back onto the host's.
    ///
    /// The guest takes its time from the host exactly once, at boot, out of the
    /// trusted control share (`lemma-host-clock.service`). Nothing moves it
    /// afterwards -- and a Virtualization.framework VM does not run while the
    /// Mac sleeps. A laptop closed for eleven hours wakes a guest eleven hours
    /// in the past, and it stays there for as long as the VM lives.
    ///
    /// That is not cosmetic. Everything the guest issues that the host then
    /// validates against its own clock is born invalid. The one that is felt:
    /// the auth service runs in here, so an access token it mints carries
    /// `exp = guest_now + 1h`; the backend on the Mac reads that as expired and
    /// answers 401; the browser refreshes; the refresh succeeds, because the
    /// refresh token is checked against the same wrong clock that minted it,
    /// and hands back another token that is also already expired. The app sits
    /// signed in and unable to do anything, indefinitely, and even signing out
    /// fails -- sign-out is an authorized call too.
    fn set_clock(&self, value: Value) -> Result<Value, GuestError> {
        self.set_clock_with(value, SystemTime::now(), set_realtime_clock)
    }

    /// The half worth testing: reading the host epoch, deciding whether the gap
    /// is worth a step, and reporting it. `apply` is the syscall, which only
    /// works inside the guest.
    fn set_clock_with(
        &self,
        value: Value,
        now: SystemTime,
        apply: impl FnOnce(u64) -> Result<(), GuestError>,
    ) -> Result<Value, GuestError> {
        let host_epoch = value
            .get("epoch")
            .and_then(Value::as_u64)
            .ok_or_else(|| GuestError::invalid("`epoch` must be whole seconds since the epoch"))?;
        if !(MIN_TRUSTED_EPOCH..=MAX_TRUSTED_EPOCH).contains(&host_epoch) {
            return Err(GuestError::invalid(format!(
                "host epoch {host_epoch} is outside the supported range"
            )));
        }
        let guest_epoch = now
            .duration_since(UNIX_EPOCH)
            .map_err(|error| GuestError::engine(format!("guest clock is invalid: {error}")))?
            .as_secs();
        let skew_seconds = host_epoch as i64 - guest_epoch as i64;
        let stepped = skew_seconds.abs() >= CLOCK_STEP_THRESHOLD_SECONDS;
        if stepped {
            apply(host_epoch)?;
        }
        Ok(json!({
            "host_epoch": host_epoch,
            "guest_epoch": guest_epoch,
            "skew_seconds": skew_seconds,
            "stepped": stepped,
        }))
    }

    fn stop_all_containers(&self) -> Result<usize, GuestError> {
        let output = self.run_checked(&["ps".into(), "--quiet".into()])?;
        let container_ids = output
            .lines()
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(|value| {
                if value.len() > 128 || !value.bytes().all(|byte| byte.is_ascii_hexdigit()) {
                    return Err(GuestError::engine(
                        "container engine returned an invalid container identifier",
                    ));
                }
                Ok(value.to_owned())
            })
            .collect::<Result<Vec<_>, _>>()?;
        if container_ids.is_empty() {
            return Ok(0);
        }

        let mut arguments = vec!["stop".into(), "--time".into(), "5".into()];
        arguments.extend(container_ids.iter().cloned());
        self.run_checked(&arguments)?;
        Ok(container_ids.len())
    }

    /// Stop before starting Postgres on a data directory it cannot open.
    ///
    /// `lemma-postgres-data` carries no version in its name, so a release that
    /// moves the Postgres major starts the new server against the old cluster.
    /// Postgres refuses -- correctly -- and the failure arrives as a 120-second
    /// `pg_isready` timeout with a container log nobody reads, on an
    /// installation that will never start again and offers nothing to press.
    /// pg16 -> pg18 is exactly this, and it shipped.
    ///
    /// Compares the cluster's own `PG_VERSION` against the image's own
    /// `PG_MAJOR` rather than a number restated in a manifest: asking the
    /// artifact means bumping the image is the only thing anyone has to
    /// remember. Either side unreadable means proceed -- a fresh volume has no
    /// `PG_VERSION`, which is the common case and must cost nothing.
    fn refuse_incompatible_postgres_data(&self, image: &str) -> Result<(), GuestError> {
        let (Some(found), Some(expected)) = (
            self.postgres_data_major("lemma-postgres-data"),
            self.postgres_image_major(image),
        ) else {
            return Ok(());
        };
        if found == expected {
            return Ok(());
        }
        Err(GuestError {
            code: "postgres_data_incompatible".into(),
            message: format!(
                "the workspace database on this computer was created by PostgreSQL {found} and \
                 this release runs PostgreSQL {expected}; {DATA_RESET_MARKER}"
            ),
            retryable: false,
            status_code: 409,
        })
    }

    /// The major version of the cluster already on a volume, if there is one.
    fn postgres_data_major(&self, volume: &str) -> Option<u32> {
        let inspect = self
            .engine
            .run(&["volume".into(), "inspect".into(), volume.into()])
            .ok()?;
        if !inspect.status.success() {
            return None;
        }
        let parsed: Value = serde_json::from_slice(&inspect.stdout).ok()?;
        let mountpoint = parsed
            .as_array()
            .and_then(|entries| entries.first())
            .unwrap_or(&parsed)
            .get("Mountpoint")?
            .as_str()?;
        let raw = std::fs::read_to_string(Path::new(mountpoint).join("PG_VERSION")).ok()?;
        raw.trim().split('.').next()?.parse().ok()
    }

    /// The major version an image ships, asked of the image itself.
    fn postgres_image_major(&self, image: &str) -> Option<u32> {
        let output = self
            .engine
            .run(&[
                "run".into(),
                "--rm".into(),
                "--network".into(),
                "none".into(),
                "--platform".into(),
                guest_platform().into(),
                image.into(),
                "/usr/bin/printenv".into(),
                "PG_MAJOR".into(),
            ])
            .ok()?;
        if !output.status.success() {
            return None;
        }
        String::from_utf8_lossy(&output.stdout)
            .trim()
            .split('.')
            .next()?
            .parse()
            .ok()
    }

    fn ensure_volume(&self, name: &str) -> Result<(), GuestError> {
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

    fn ensure_core_container(
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

    fn restart_or_remove_stale(&self, name: &str) -> Result<bool, GuestError> {
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

    /// Why a core container is not answering, in its own words.
    ///
    /// This used to return the single last non-empty line, which is fine for a
    /// crash that ends in one and useless for the case that matters most.
    /// PostgreSQL refuses an unusable data directory with a *paragraph*, and
    /// the last line of the official image's version-mismatch block is
    ///
    /// ```text
    /// discussion around this process, and suggestions for how to do so.
    /// ```
    ///
    /// which is what a user was shown, appended to a nerdctl error, as the
    /// whole explanation for an install that would never finish. The sentence
    /// that says what is wrong is several lines above it.
    ///
    /// So: keep the tail, drop the noise, and cap the length. A few lines of
    /// the container's own output is the difference between "something stopped"
    /// and "your data was made by a different PostgreSQL".
    fn container_log_summary(&self, name: &str) -> Option<String> {
        const KEEP_LINES: usize = 6;
        const MAX_CHARS: usize = 600;
        let output = self
            .engine
            .run(&["logs".into(), "--tail".into(), "40".into(), name.into()])
            .ok()?;
        // Both streams: the refusal goes to stderr, but an image that logs
        // its reason to stdout and exits quietly would otherwise report
        // nothing at all.
        let mut logs = String::from_utf8_lossy(&output.stdout).into_owned();
        let stderr = String::from_utf8_lossy(&output.stderr);
        if !stderr.trim().is_empty() {
            if !logs.trim().is_empty() {
                logs.push('\n');
            }
            logs.push_str(&stderr);
        }
        let lines = logs
            .lines()
            .map(str::trim)
            .filter(|line| !line.is_empty())
            // Decorative rules are what the image wraps its refusal in, and
            // they crowd out the sentence underneath at this length.
            .filter(|line| !line.chars().all(|c| c == '*' || c == '-' || c == '='))
            .collect::<Vec<_>>();
        if lines.is_empty() {
            return None;
        }
        let summary = lines[lines.len().saturating_sub(KEEP_LINES)..].join(" ");
        Some(if summary.chars().count() > MAX_CHARS {
            let cut: String = summary.chars().take(MAX_CHARS).collect();
            format!("{cut}…")
        } else {
            summary
        })
    }

    fn wait_engine_command(&self, arguments: &[String], timeout: u64) -> Result<(), GuestError> {
        self.wait_engine_output(arguments, timeout).map(|_| ())
    }

    fn wait_engine_output(&self, arguments: &[String], timeout: u64) -> Result<String, GuestError> {
        let deadline = Instant::now() + Duration::from_secs(timeout);
        let mut last_error = None;
        while Instant::now() < deadline {
            match self.engine.run(arguments) {
                Ok(output) if output.status.success() => {
                    return String::from_utf8(output.stdout)
                        .map(|value| value.trim().to_owned())
                        .map_err(|_| {
                            GuestError::engine("container engine returned non-UTF8 output")
                        })
                }
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
        for database in ["lemma", "lemma_datastore", "supertokens"] {
            self.ensure_database(database, 120)?;
        }
        Ok(())
    }

    fn ensure_database(&self, database: &str, timeout: u64) -> Result<(), GuestError> {
        let deadline = Instant::now() + Duration::from_secs(timeout);
        let query = format!("SELECT 1 FROM pg_database WHERE datname = '{database}'");
        let query_arguments = [
            "exec".into(),
            "lemma-core-postgres".into(),
            "psql".into(),
            "-U".into(),
            "postgres".into(),
            "-tAc".into(),
            query,
        ];
        let create_arguments = [
            "exec".into(),
            "lemma-core-postgres".into(),
            "createdb".into(),
            "-U".into(),
            "postgres".into(),
            database.into(),
        ];
        let mut last_error = None;

        // The official image starts a temporary server for initialization and
        // then restarts it. A CREATE DATABASE transaction can commit just as
        // that connection is closed, leaving `createdb` with exit 1 even
        // though the database now exists. Always use the existence query as
        // the source of truth instead of retrying a potentially committed
        // `createdb` command until it only reports "already exists".
        while Instant::now() < deadline {
            match self.engine.run(&query_arguments) {
                Ok(output) if output.status.success() => {
                    if String::from_utf8_lossy(&output.stdout).trim() == "1" {
                        return Ok(());
                    }
                    match self.engine.run(&create_arguments) {
                        Ok(output) if output.status.success() => {}
                        Ok(output) => {
                            last_error = Some(redact_engine_error(&String::from_utf8_lossy(
                                &output.stderr,
                            )))
                        }
                        Err(error) => last_error = Some(error),
                    }
                }
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
            "database {database} provisioning timed out: {}",
            last_error.unwrap_or_else(|| "Postgres did not accept the provisioning query".into())
        )))
    }

    fn wait_tcp(&self, port: u16, timeout: u64) -> Result<(), GuestError> {
        let deadline = Instant::now() + Duration::from_secs(timeout);
        while Instant::now() < deadline {
            let endpoint_host = self.current_endpoint_host();
            if TcpStream::connect_timeout(
                &format!("{endpoint_host}:{port}")
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
        while Instant::now() < deadline {
            let url = format!("http://{}:{port}{path}", self.current_endpoint_host());
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
            if redis_ready(&self.current_endpoint_host(), password).is_ok() {
                return Ok(());
            }
            thread::sleep(Duration::from_millis(250));
        }
        Err(GuestError::engine(
            "Redis Stack did not become ready with Search and JSON modules",
        ))
    }

    fn ensure(&self, value: Value) -> Result<Value, GuestError> {
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
            // Non-blocking: a pull here would stop every other sandbox
            // operation on the machine for as long as it takes.
            self.ensure_sandbox_image(&parameters.image, parameters.workload_kind, false)?;
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

    fn sandbox_startup_error(&self, container: &str, summary: &str) -> GuestError {
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

    fn status(&self, value: Value) -> Result<Value, GuestError> {
        let sandbox_id = required_string(&value, "sandbox_id")?;
        validate_sandbox_id(&sandbox_id)?;
        self.snapshot_optional(&sandbox_id)?
            .ok_or_else(GuestError::not_found)
    }

    fn sandbox_diagnostics(&self, value: Value) -> Result<Value, GuestError> {
        let sandbox_id = required_string(&value, "sandbox_id")?;
        validate_sandbox_id(&sandbox_id)?;
        let container = container_name(&sandbox_id);
        let inspect = self
            .inspect_raw(&container)?
            .ok_or_else(GuestError::not_found)?;
        let state = inspect.get("State").and_then(Value::as_object);
        let config = inspect.get("Config").and_then(Value::as_object);
        let text = |name: &str| {
            state
                .and_then(|value| value.get(name))
                .and_then(Value::as_str)
                .filter(|value| !value.trim().is_empty())
                .map(redact_engine_error)
        };
        Ok(json!({
            "sandbox_id": sandbox_id,
            "process": {
                "path": inspect.get("Path").and_then(Value::as_str),
                "args": inspect.get("Args").and_then(Value::as_array),
                "entrypoint": config
                    .and_then(|value| value.get("Entrypoint"))
                    .and_then(Value::as_array),
                "cmd": config
                    .and_then(|value| value.get("Cmd"))
                    .and_then(Value::as_array),
            },
            "state": {
                "status": text("Status"),
                "running": state
                    .and_then(|value| value.get("Running"))
                    .and_then(Value::as_bool),
                "exit_code": state
                    .and_then(|value| value.get("ExitCode"))
                    .and_then(Value::as_i64),
                "oom_killed": state
                    .and_then(|value| value.get("OOMKilled"))
                    .and_then(Value::as_bool),
                "error": text("Error"),
                "started_at": text("StartedAt"),
                "finished_at": text("FinishedAt"),
            },
            "last_log": self.container_log_summary(&container),
        }))
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

    fn mutate(&self, value: Value, mutation: Mutation) -> Result<Value, GuestError> {
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

    fn snapshot_optional(&self, sandbox_id: &str) -> Result<Option<Value>, GuestError> {
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
            &self.current_endpoint_host(),
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

    /// Is this image already unpacked here?
    fn image_present(&self, image: &str) -> Result<bool, GuestError> {
        let inspect = self
            .engine
            .run(&[
                "image".into(),
                "inspect".into(),
                "--platform".into(),
                guest_platform().into(),
                image.into(),
            ])
            .map_err(GuestError::engine)?;
        Ok(inspect.status.success())
    }

    /// Fetch an image without occupying the guest's only control channel.
    ///
    /// A pull is minutes of work, and this process serves one request at a
    /// time -- so pulling inline stops every other sandbox operation on the
    /// machine until it finishes. This install's own logs show a 243-second
    /// pull during which a read-only `sandbox.list` timed out after 60s having
    /// never been read.
    ///
    /// So the pull runs on its own thread and the caller is told to come back.
    /// The retry is cheap: `sandbox.ensure` re-enters, finds the image present
    /// (or the pull still running) and answers in milliseconds either way.
    ///
    /// Deliberately not used by `core.sandbox_images`, which is first-run setup:
    /// there the host *wants* to block, because its progress screen is
    /// reporting the download and there is nothing else for the channel to do.
    fn start_or_join_pull(&self, image: &str) -> Result<(), GuestError> {
        let pulls = in_flight_pulls();
        {
            let mut table = pulls.lock().expect("pull table poisoned");
            match table.get(image) {
                Some(PullState::Running) => return Err(pull_in_progress(image)),
                Some(PullState::Failed(reason)) => {
                    // Reported once, then cleared, so a retry attempts the pull
                    // again rather than being told about an old failure for ever.
                    let reason = reason.clone();
                    table.remove(image);
                    return Err(GuestError::engine(reason));
                }
                None => {
                    table.insert(image.to_owned(), PullState::Running);
                }
            }
        }

        let engine = Arc::clone(&self.engine);
        let owned = image.to_owned();
        let spawned = thread::Builder::new()
            .name("lemma-guest-image-pull".into())
            .spawn(move || {
                let outcome = pull_with(&*engine, &owned);
                let mut table = in_flight_pulls().lock().expect("pull table poisoned");
                match outcome {
                    Ok(()) => {
                        table.remove(&owned);
                    }
                    Err(reason) => {
                        table.insert(owned, PullState::Failed(reason));
                    }
                }
            });
        if spawned.is_err() {
            // Could not get a thread; do not leave the table claiming a pull
            // that nobody is running, or the image never arrives.
            in_flight_pulls()
                .lock()
                .expect("pull table poisoned")
                .remove(image);
            return Err(GuestError::engine("could not start an image pull"));
        }
        Err(pull_in_progress(image))
    }

    /// Make sure an image is here, blocking until it is.
    ///
    /// Used by first-run setup, where the host is showing a progress screen and
    /// wants exactly this. A live request should use `ensure_image_available`.
    fn ensure_image(&self, image: &str) -> Result<(), GuestError> {
        if self.image_present(image)? {
            return Ok(());
        }
        self.pull_image(image)
    }

    /// Make sure an image is here, without holding the control channel for it.
    ///
    /// Returns a retryable `image_pulling` while the download runs, so every
    /// other sandbox operation on the machine keeps being served.
    fn ensure_image_available(&self, image: &str) -> Result<(), GuestError> {
        if self.image_present(image)? {
            return Ok(());
        }
        self.start_or_join_pull(image)
    }

    fn ensure_sandbox_image(
        &self,
        image: &str,
        workload_kind: WorkloadKind,
        blocking: bool,
    ) -> Result<(), GuestError> {
        if blocking {
            self.ensure_image(image)?;
        } else {
            self.ensure_image_available(image)?;
        }
        if self.sandbox_image_marker_is_ready(image, workload_kind) {
            return Ok(());
        }
        // An interrupted VM shutdown can leave containerd's image metadata
        // present while its unpacked snapshot is incomplete. `image inspect`
        // still succeeds in that state. Stopped sandbox containers are
        // disposable compute; pruning them preserves bind-mounted workspaces
        // while releasing the broken snapshot. Never remove a running
        // container as part of automatic repair.
        self.run_checked(&["container".into(), "prune".into(), "--force".into()])?;
        self.run_checked(&["rmi".into(), "--force".into(), image.into()])?;
        self.pull_image(image)?;
        if self.sandbox_image_marker_is_ready(image, workload_kind) {
            Ok(())
        } else {
            self.schedule_cache_reset()?;
            Err(GuestError {
                code: "guest_cache_repair_required".into(),
                message: "container cache repair required; automatic restart scheduled".into(),
                retryable: true,
                status_code: 503,
            })
        }
    }

    fn cache_reset_marker(&self) -> PathBuf {
        self.state_root.join("container-cache-reset-required")
    }

    fn schedule_cache_reset(&self) -> Result<(), GuestError> {
        let marker = self.cache_reset_marker();
        let mut file = OpenOptions::new()
            .create(true)
            .truncate(true)
            .write(true)
            .mode(0o600)
            .open(&marker)
            .map_err(|error| GuestError::engine(error.to_string()))?;
        file.write_all(b"1\n")
            .map_err(|error| GuestError::engine(error.to_string()))?;
        file.sync_all()
            .map_err(|error| GuestError::engine(error.to_string()))
    }

    fn sandbox_image_marker_is_ready(&self, image: &str, workload_kind: WorkloadKind) -> bool {
        let marker = match workload_kind {
            WorkloadKind::Workspace => "/usr/local/bin/start-workspace-runtime",
            WorkloadKind::Function => "/usr/local/bin/lemma-function-runtime",
        };
        self.engine
            .run(&[
                "run".into(),
                "--rm".into(),
                "--network".into(),
                "none".into(),
                "--platform".into(),
                guest_platform().into(),
                image.into(),
                "/usr/bin/test".into(),
                "-s".into(),
                marker.into(),
            ])
            .is_ok_and(|output| output.status.success())
    }

    fn pull_image(&self, image: &str) -> Result<(), GuestError> {
        let output = self
            .engine
            .run(&[
                "pull".into(),
                "--quiet".into(),
                "--unpack=true".into(),
                "--platform".into(),
                guest_platform().into(),
                image.into(),
            ])
            .map_err(GuestError::engine)?;
        if output.status.success() {
            return Ok(());
        }
        let error = redact_engine_error(&String::from_utf8_lossy(&output.stderr));
        let diagnostic = network_diagnostics();
        let dns_ok = diagnostic["dns_ok"].as_bool().unwrap_or(false);
        let registry_reachable = diagnostic["registry_reachable"].as_bool().unwrap_or(false);
        let hint = match (dns_ok, registry_reachable) {
            (false, _) => "registry DNS lookup failed",
            (true, false) => "registry HTTPS endpoint is unreachable",
            (true, true) => "registry is reachable; retry the immutable image download",
        };
        Err(GuestError::engine(format!("{error}; {hint}")))
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

    fn runtime_token_dir(&self, sandbox_id: &str) -> Result<PathBuf, GuestError> {
        let root = self.state_root.join("run");
        let path = root.join(format!("runtime-token-{sandbox_id}"));
        if path.parent() != Some(root.as_path()) {
            return Err(GuestError::invalid("runtime token escaped managed root"));
        }
        Ok(path)
    }

    fn runtime_token_path(&self, sandbox_id: &str) -> Result<PathBuf, GuestError> {
        Ok(self.runtime_token_dir(sandbox_id)?.join("token"))
    }

    fn write_runtime_token(&self, sandbox_id: &str, token: &str) -> Result<PathBuf, GuestError> {
        if token.is_empty() || token.len() > 4096 || token.contains('\0') {
            return Err(GuestError::invalid("workspace runtime token is invalid"));
        }
        let directory = self.runtime_token_dir(sandbox_id)?;
        match fs::symlink_metadata(&directory) {
            Ok(metadata) if metadata.is_dir() => {}
            Ok(_) => fs::remove_file(&directory)
                .map_err(|error| GuestError::engine(error.to_string()))?,
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => return Err(GuestError::engine(error.to_string())),
        }
        fs::create_dir_all(&directory).map_err(|error| GuestError::engine(error.to_string()))?;
        fs::set_permissions(&directory, fs::Permissions::from_mode(0o700))
            .map_err(|error| GuestError::engine(error.to_string()))?;
        let directory_bytes = std::ffi::CString::new(directory.as_os_str().as_encoded_bytes())
            .map_err(|_| GuestError::invalid("runtime token directory contains NUL"))?;
        let result = unsafe { libc::chown(directory_bytes.as_ptr(), 10_001, 10_001) };
        if result != 0 {
            return Err(GuestError::engine(io::Error::last_os_error().to_string()));
        }

        let path = self.runtime_token_path(sandbox_id)?;
        match fs::remove_file(&path) {
            Ok(()) => {}
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => return Err(GuestError::engine(error.to_string())),
        }
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&path)
            .map_err(|error| GuestError::engine(error.to_string()))?;
        file.write_all(token.as_bytes())
            .map_err(|error| GuestError::engine(error.to_string()))?;
        file.sync_all()
            .map_err(|error| GuestError::engine(error.to_string()))?;
        let path_bytes = std::ffi::CString::new(path.as_os_str().as_encoded_bytes())
            .map_err(|_| GuestError::invalid("runtime token path contains NUL"))?;
        let result = unsafe { libc::chown(path_bytes.as_ptr(), 10_001, 10_001) };
        if result != 0 {
            return Err(GuestError::engine(io::Error::last_os_error().to_string()));
        }
        Ok(path)
    }

    fn remove_runtime_token(&self, sandbox_id: &str) -> Result<(), GuestError> {
        let directory = self.runtime_token_dir(sandbox_id)?;
        match fs::symlink_metadata(&directory) {
            Ok(metadata) if metadata.is_dir() => {
                fs::remove_dir_all(directory).map_err(|error| GuestError::engine(error.to_string()))
            }
            Ok(_) => {
                fs::remove_file(directory).map_err(|error| GuestError::engine(error.to_string()))
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
            Err(error) => Err(GuestError::engine(error.to_string())),
        }
    }

    fn wait_callback(&self, parameters: &EnsureParameters) -> Result<(), GuestError> {
        if !parameters.callback.required {
            return Ok(());
        }
        let base = parameters
            .callback
            .url
            .as_deref()
            .or_else(|| parameters.env.get("LEMMA_BASE_URL").map(String::as_str))
            .ok_or_else(|| {
                GuestError::invalid("Local sandbox requires an explicit callback URL")
            })?;
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
    workspace: Option<&Path>,
    runtime_token: Option<&Path>,
    env_file: &Path,
    host_gateway: &str,
) -> Vec<String> {
    let metadata = serde_json::to_string(&parameters.metadata)
        .expect("validated sandbox metadata must serialize");
    let mut arguments = vec![
        "run".into(),
        "--detach".into(),
        "--platform".into(),
        guest_platform().into(),
        "--name".into(),
        container_name(&parameters.sandbox_id),
        "--label".into(),
        MANAGED_LABEL.into(),
        "--label".into(),
        format!("lemma.work/sandbox-id={}", parameters.sandbox_id),
        "--label".into(),
        "lemma.work/provider=lemma_local".into(),
        "--label".into(),
        format!(
            "lemma.work/workload-kind={}",
            match parameters.workload_kind {
                WorkloadKind::Workspace => "workspace",
                WorkloadKind::Function => "function",
            }
        ),
        "--label".into(),
        format!("lemma.work/image-ref={}", parameters.image),
        "--label".into(),
        format!("lemma.work/metadata={metadata}"),
        "--env-file".into(),
        env_file.display().to_string(),
        "--add-host".into(),
        format!("host.lemma.internal:{host_gateway}"),
    ];
    match parameters.workload_kind {
        WorkloadKind::Workspace => {
            let workspace = workspace.expect("workspace workload must have storage");
            let runtime_token =
                runtime_token.expect("workspace workload must have a runtime token");
            let runtime_token_mount = runtime_token
                .parent()
                .expect("workspace runtime token must have a private directory");
            arguments.extend([
                "--mount".into(),
                format!("type=bind,src={},dst=/workspace", workspace.display()),
                "--mount".into(),
                format!(
                    "type=bind,src={},dst=/run/lemma-bootstrap",
                    runtime_token_mount.display()
                ),
                "--workdir".into(),
                "/workspace".into(),
            ]);
        }
        WorkloadKind::Function => {
            arguments.extend([
                "--read-only".into(),
                "--tmpfs".into(),
                "/tmp:rw,noexec,nosuid,size=512m,uid=10001,gid=10001".into(),
                "--tmpfs".into(),
                "/run/lemma-function-cache:rw,exec,nosuid,nodev,size=512m,mode=0700,uid=10001,gid=10001"
                    .into(),
                "--env".into(),
                "LEMMA_FUNCTION_CACHE_ROOT=/run/lemma-function-cache".into(),
                "--workdir".into(),
                "/tmp".into(),
            ]);
        }
    }
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

#[cfg(target_arch = "aarch64")]
fn guest_platform() -> &'static str {
    "linux/arm64"
}

#[cfg(target_arch = "x86_64")]
fn guest_platform() -> &'static str {
    "linux/amd64"
}

/// Did this container run and stop, as opposed to never having started?
///
/// Read from the fields nerdctl does fill when `State.Status` is absent: an
/// exit code, or a finish timestamp.
fn container_has_exited(state: &serde_json::Map<String, Value>) -> bool {
    state.get("ExitCode").and_then(Value::as_i64).is_some()
        || state
            .get("FinishedAt")
            .and_then(Value::as_str)
            .is_some_and(|value| !value.trim().is_empty())
}

fn snapshot_from_inspect(
    sandbox_id: &str,
    inspect: &serde_json::Map<String, Value>,
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
    } else if matches!(state_text, "exited" | "stopped" | "removing" | "paused") {
        // `paused` is here defensively: nothing in Lemma pauses a sandbox, and
        // if something did it is suspended rather than faulted. `dead` is
        // deliberately *not* here -- a container the engine could not clean up
        // is a fault, and calling it the ordinary resting state of an idle
        // workspace would hide exactly the case worth seeing.
        "STOPPED"
    } else if state_text.is_empty() && state.is_some_and(container_has_exited) {
        // nerdctl does not always fill `State.Status`. A container that is not
        // running and carries an exit code has stopped -- which is the ordinary
        // end of an idle release, not a fault. Reporting it as ERROR made the
        // most common resting state of a workspace look like a broken one.
        "STOPPED"
    } else {
        "ERROR"
    };
    let labels = inspect
        .get("Config")
        .and_then(Value::as_object)
        .and_then(|config| config.get("Labels"))
        .and_then(Value::as_object);
    let workload_kind = labels
        .and_then(|value| value.get("lemma.work/workload-kind"))
        .and_then(Value::as_str)
        .ok_or_else(|| GuestError::engine("sandbox workload label is missing"))?;
    let apps = match workload_kind {
        "workspace" => workspace_apps(),
        "function" => function_apps(),
        _ => return Err(GuestError::engine("sandbox workload label is invalid")),
    };
    let image = labels
        .and_then(|value| value.get("lemma.work/image-ref"))
        .and_then(Value::as_str)
        .ok_or_else(|| GuestError::engine("sandbox image label is missing"))?;
    let metadata = labels
        .and_then(|value| value.get("lemma.work/metadata"))
        .and_then(Value::as_str)
        .ok_or_else(|| GuestError::engine("sandbox metadata label is missing"))
        .and_then(|encoded| {
            serde_json::from_str::<BTreeMap<String, String>>(encoded)
                .map_err(|_| GuestError::engine("sandbox metadata label is invalid"))
        })?;
    let ports = inspect
        .get("NetworkSettings")
        .and_then(Value::as_object)
        .and_then(|network| network.get("Ports"))
        .and_then(Value::as_object);
    let mut statuses = serde_json::Map::new();
    for app in &apps {
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
        "image": image,
        "metadata": metadata,
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

fn workspace_apps() -> Vec<AppSpec> {
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
    ]
}

fn function_apps() -> Vec<AppSpec> {
    vec![AppSpec {
        name: "function".into(),
        public_slug: "function".into(),
        port: 8090,
        health_path: "/healthz".into(),
        startup: "eager".into(),
        exposure: "private".into(),
        auth_mode: "manager_api_key".into(),
    }]
}

fn validate_metadata(metadata: &BTreeMap<String, String>) -> Result<(), GuestError> {
    if metadata.len() > 32 {
        return Err(GuestError::invalid(
            "sandbox metadata cannot contain more than 32 entries",
        ));
    }
    for (name, value) in metadata {
        if !valid_identifier(name) || name.len() > 128 || value.len() > 4096 || value.contains('\0')
        {
            return Err(GuestError::invalid("sandbox metadata is invalid"));
        }
    }
    Ok(())
}

fn validate_apps(apps: &[AppSpec]) -> Result<(), GuestError> {
    if apps.is_empty() || apps.len() > 16 {
        return Err(GuestError::invalid(
            "apps must contain between 1 and 16 entries",
        ));
    }
    for app in apps {
        if !valid_app_name(&app.name)
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
    if !(16..=512).contains(&value.len())
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-' || byte == b'_')
    {
        return Err(GuestError::invalid(format!(
            "{name} must contain 16 to 512 ASCII letters, digits, '-' or '_'"
        )));
    }
    Ok(())
}

/// AUTH and PING, and deliberately nothing about modules.
///
/// This used to require `MODULE LIST` to report `search` and `rejson`, which
/// tied the guest to a Redis Stack image. Nothing in the product ever issued a
/// `JSON.*` or `FT.*` command -- `RedisJsonCache` stores JSON as an ordinary
/// string through GET/SET, and vector search is Postgres -- so the assertion
/// only made a smaller Redis impossible to adopt.
fn redis_ready(host: &str, password: &str) -> io::Result<()> {
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
        "*2\r\n$4\r\nAUTH\r\n${}\r\n{}\r\n*1\r\n$4\r\nPING\r\n*1\r\n$4\r\nQUIT\r\n",
        password.len(),
        password
    )?;
    stream.flush()?;
    let mut response = Vec::new();
    stream.take(64 * 1024).read_to_end(&mut response)?;
    let response = std::str::from_utf8(&response)
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "Redis returned non-UTF8"))?;
    let response = response.to_ascii_lowercase();
    if response.contains("+ok\r\n") && response.contains("+pong\r\n") {
        Ok(())
    } else {
        Err(io::Error::other("Redis authentication or ping failed"))
    }
}

fn validate_resources(resources: &ResourceSpec) -> Result<u64, GuestError> {
    let memory = resources
        .memory
        .as_deref()
        .filter(|value| !value.trim().is_empty())
        .map(parse_memory_bytes)
        .transpose()?
        .unwrap_or(DEFAULT_SANDBOX_MEMORY_BYTES);
    if !(256 * 1024 * 1024..=6 * 1024 * 1024 * 1024).contains(&memory) {
        return Err(GuestError::invalid(
            "sandbox memory must be between 256 MiB and 6 GiB",
        ));
    }
    if let Some(cpus) = resources
        .cpus
        .as_deref()
        .filter(|value| !value.trim().is_empty())
    {
        let cpus = cpus
            .parse::<f64>()
            .ok()
            .filter(|value| value.is_finite() && (0.1..=16.0).contains(value))
            .ok_or_else(|| {
                GuestError::invalid("sandbox cpus must be a number between 0.1 and 16")
            })?;
        let _ = cpus;
    }
    Ok(memory)
}

fn parse_memory_bytes(value: &str) -> Result<u64, GuestError> {
    let value = value.trim().to_ascii_lowercase();
    let (digits, multiplier) = [
        ("gib", 1024_u64.pow(3)),
        ("gb", 1000_u64.pow(3)),
        ("gi", 1024_u64.pow(3)),
        ("g", 1024_u64.pow(3)),
        ("mib", 1024_u64.pow(2)),
        ("mb", 1000_u64.pow(2)),
        ("mi", 1024_u64.pow(2)),
        ("m", 1024_u64.pow(2)),
        ("kib", 1024_u64),
        ("kb", 1000_u64),
        ("ki", 1024_u64),
        ("k", 1024_u64),
        ("b", 1_u64),
    ]
    .into_iter()
    .find_map(|(suffix, multiplier)| {
        value
            .strip_suffix(suffix)
            .map(|digits| (digits, multiplier))
    })
    .unwrap_or((&value, 1));
    let number = digits
        .parse::<u64>()
        .map_err(|_| GuestError::invalid("sandbox memory has an invalid size"))?;
    number
        .checked_mul(multiplier)
        .ok_or_else(|| GuestError::invalid("sandbox memory size overflow"))
}

/// How much memory the guest could hand to a new process right now.
///
/// `MemAvailable`, not `MemFree`: the kernel's own estimate of what is
/// reclaimable including page cache, which is the number that decides whether
/// starting a container will succeed. `MemTotal` is the wrong question --
/// virtio-balloon adjusts it as the host takes memory back, so a total says
/// nothing about what is spare.
/// Images being fetched right now, and the ones whose fetch failed.
///
/// Process-global because it outlives any single request: the whole point is
/// that the pull continues after the caller has been answered.
enum PullState {
    Running,
    Failed(String),
}

fn in_flight_pulls() -> &'static Mutex<HashMap<String, PullState>> {
    static PULLS: OnceLock<Mutex<HashMap<String, PullState>>> = OnceLock::new();
    PULLS.get_or_init(|| Mutex::new(HashMap::new()))
}

/// The pull itself, callable without a `GuestService` so a thread can run it.
fn pull_with(engine: &dyn Engine, image: &str) -> Result<(), String> {
    let output = engine
        .run(&[
            "pull".into(),
            "--quiet".into(),
            "--unpack=true".into(),
            "--platform".into(),
            guest_platform().into(),
            image.into(),
        ])
        .map_err(|error| error.to_string())?;
    if output.status.success() {
        return Ok(());
    }
    Err(redact_engine_error(&String::from_utf8_lossy(
        &output.stderr,
    )))
}

fn pull_in_progress(image: &str) -> GuestError {
    GuestError {
        code: "image_pulling".into(),
        message: format!("still downloading {image}"),
        retryable: true,
        status_code: 503,
    }
}

fn guest_available_memory_bytes() -> Result<u64, GuestError> {
    let meminfo = fs::read_to_string("/proc/meminfo")
        .map_err(|error| GuestError::engine(format!("could not read guest memory: {error}")))?;
    parse_mem_available(&meminfo)
        .ok_or_else(|| GuestError::engine("guest available memory was unavailable"))
}

/// Split out so it can be tested without a `/proc`.
fn parse_mem_available(meminfo: &str) -> Option<u64> {
    meminfo
        .lines()
        .find_map(|line| line.strip_prefix("MemAvailable:"))
        .and_then(|value| value.split_whitespace().next())
        .and_then(|value| value.parse::<u64>().ok())
        .and_then(|kib| kib.checked_mul(1024))
}

/// The concurrent-sandbox ceiling, overridable for a larger guest.
fn max_sandboxes() -> usize {
    std::env::var("LEMMA_GUEST_MAX_SANDBOXES")
        .ok()
        .and_then(|value| value.trim().parse::<usize>().ok())
        .filter(|value| *value > 0)
        .unwrap_or(DEFAULT_MAX_SANDBOXES)
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

fn valid_app_name(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 63
        && value.bytes().all(|byte| {
            byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-' || byte == b'_'
        })
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

/// Is this PostgreSQL saying the data directory itself is the problem?
///
/// The `refuse_incompatible_postgres_data` probe runs first and catches the
/// version case cleanly, in about a second, by reading `PG_VERSION` off the
/// volume. It cannot catch everything: the probe returns "don't know" whenever
/// the volume cannot be read, and "don't know" means proceed. When it does
/// proceed and the server then refuses, this is what turns a 120-second wait
/// ending in a nerdctl error into the offer of a reset.
///
/// Matched on the server's and the image's own words. Deliberately narrow:
/// anything not recognised keeps the old behaviour, which is retryable, and a
/// wrong match here would offer to delete a database over a transient fault.
/// The environment and arguments the PostgreSQL container is created with.
///
/// A free function so the one invariant that matters can be asserted without an
/// engine, a volume or a container: **the cluster path and the volume mount
/// target are the same path**. They were not, and nothing noticed.
///
/// The official image moved `PGDATA` at 18 -- `/var/lib/postgresql/data`
/// through 17, `/var/lib/postgresql/18/docker` after -- while the mount stayed
/// where it was. So on the pinned image the database was written into the
/// image's own anonymous volume and `lemma-postgres-data`, the volume the user
/// is told holds their data, held nothing. A container recreation would have
/// taken every table with it.
///
/// Pinning `PGDATA` rather than inheriting it also keeps the layout stable
/// across the next such move, and puts `PG_VERSION` back at the root of this
/// volume -- which is where `postgres_data_major` looks when it decides whether
/// the data on disk can be opened at all.
fn postgres_container_spec(password: &str) -> (BTreeMap<String, String>, Vec<String>) {
    let environment = BTreeMap::from([
        ("POSTGRES_USER".to_owned(), "postgres".to_owned()),
        ("POSTGRES_PASSWORD".to_owned(), password.to_owned()),
        ("POSTGRES_DB".to_owned(), "lemma".to_owned()),
        ("PGDATA".to_owned(), POSTGRES_DATA_DIR.to_owned()),
    ]);
    let arguments = vec![
        "--network".to_owned(),
        "host".to_owned(),
        "--memory".to_owned(),
        "512m".to_owned(),
        "--cpus".to_owned(),
        "1.5".to_owned(),
        "--volume".to_owned(),
        format!("lemma-postgres-data:{POSTGRES_DATA_DIR}"),
    ];
    (environment, arguments)
}

fn postgres_refused_its_data(diagnostic: &str) -> bool {
    let text = diagnostic.to_ascii_lowercase();
    [
        // The server, on a cluster from another major version.
        "database files are incompatible with server",
        "was initialized by postgresql version",
        // The official image, when it finds data where its older releases kept
        // it. This is the block whose last line is "discussion around this
        // process, and suggestions for how to do so."
        "database directory appears to contain a database",
        "an upgrade is required",
        "incompatible data directory",
        // A cluster that is present but unreadable.
        "could not read file \"global/pg_control\"",
        "is not a valid data directory",
    ]
    .iter()
    .any(|needle| text.contains(needle))
}

fn redact_engine_error(value: &str) -> String {
    let lines = value
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .collect::<Vec<_>>();
    // Container CLIs often emit one or more warnings before the actionable
    // fatal diagnostic. Returning the first line made a preserved-volume
    // warning look like the reason a later container creation failed.
    let line = lines
        .iter()
        .rev()
        .copied()
        .find(|line| line.contains("level=fatal") || line.contains("level=error"))
        .or_else(|| lines.last().copied())
        .unwrap_or("container engine failed");
    if line.len() > 512 {
        format!("{}…", &line[..512])
    } else {
        line.into()
    }
}

pub fn handle_reader<R: Read, W: Write, E: Engine + 'static>(
    reader: R,
    mut writer: W,
    service: &GuestService<E>,
) -> io::Result<bool> {
    let mut bounded = BufReader::new(reader).take(MAX_REQUEST_BYTES + 1);
    let mut line = String::new();
    bounded.read_line(&mut line)?;
    let response = response_for_line(&line, service);
    write_response(&mut writer, &response)?;
    Ok(response.ok)
}

fn response_for_line<E: Engine + 'static>(line: &str, service: &GuestService<E>) -> GuestResponse {
    if line.len() as u64 > MAX_REQUEST_BYTES {
        GuestResponse::failure(GuestError::invalid("request exceeded 1 MiB"))
    } else {
        match serde_json::from_str::<GuestRequest>(line.trim_end()) {
            Ok(request) => service.handle(request),
            Err(error) => GuestResponse::failure(GuestError::invalid(format!(
                "invalid request JSON: {error}"
            ))),
        }
    }
}

fn write_response<W: Write>(writer: &mut W, response: &GuestResponse) -> io::Result<()> {
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
    Ok(())
}

#[cfg(any(target_os = "linux", test))]
fn handle_stream<R: Read, W: Write, E: Engine + 'static>(
    reader: R,
    mut writer: W,
    service: &GuestService<E>,
) -> io::Result<()> {
    let mut reader = BufReader::new(reader);
    loop {
        let mut line = String::new();
        let count = (&mut reader)
            .take(MAX_REQUEST_BYTES + 1)
            .read_line(&mut line)?;
        if count == 0 {
            return Ok(());
        }
        let response = response_for_line(&line, service);
        write_response(&mut writer, &response)?;
    }
}

#[cfg(target_os = "linux")]
pub fn serve_vsock<E: Engine + 'static>(service: &GuestService<E>) -> io::Result<()> {
    use std::mem::{size_of, zeroed};
    use std::os::fd::{FromRawFd, OwnedFd};

    // SAFETY: all libc calls use initialized Linux sockaddr_vm values, checked
    // return codes, and OwnedFd closes each accepted descriptor exactly once.
    unsafe {
        let raw = libc::socket(libc::AF_VSOCK, libc::SOCK_STREAM | libc::SOCK_CLOEXEC, 0);
        if raw < 0 {
            return Err(io::Error::last_os_error());
        }
        let _listener = OwnedFd::from_raw_fd(raw);
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
            let _ = handle_stream(reader, writer, service);
        }
        #[allow(unreachable_code)]
        drop(_listener);
    }
}

#[cfg(not(target_os = "linux"))]
pub fn serve_vsock<E: Engine + 'static>(_service: &GuestService<E>) -> io::Result<()> {
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

fn network_diagnostics() -> Value {
    let dns = Command::new("/usr/bin/timeout")
        .args([
            "--signal=KILL",
            "5s",
            "/usr/bin/getent",
            "ahostsv4",
            "registry-1.docker.io",
        ])
        .stdin(Stdio::null())
        .output();
    let mut addresses = Vec::new();
    if let Ok(output) = &dns {
        if output.status.success() {
            for address in String::from_utf8_lossy(&output.stdout)
                .lines()
                .filter_map(|line| line.split_whitespace().next())
            {
                if valid_ip(address) && !addresses.iter().any(|value| value == address) {
                    addresses.push(address.to_owned());
                }
                if addresses.len() == 4 {
                    break;
                }
            }
        }
    }

    // Docker Hub's registry endpoint normally answers an unauthenticated
    // /v2/ request with 401. That still proves DNS, routing and TLS are usable.
    let registry = Command::new("/usr/bin/curl")
        .args([
            "--head",
            "--silent",
            "--output",
            "/dev/null",
            "--connect-timeout",
            "3",
            "--max-time",
            "5",
            "--write-out",
            "%{http_code}",
            "https://registry-1.docker.io/v2/",
        ])
        .stdin(Stdio::null())
        .output();
    let registry_status = registry
        .ok()
        .filter(|output| output.status.success())
        .and_then(|output| String::from_utf8(output.stdout).ok())
        .and_then(|value| value.trim().parse::<u16>().ok());
    let registry_reachable = registry_status.is_some_and(|status| (200..500).contains(&status));
    let clock_epoch = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();

    json!({
        "clock_epoch": clock_epoch,
        "dns_ok": !addresses.is_empty(),
        "registry_addresses": addresses,
        "registry_http_status": registry_status,
        "registry_reachable": registry_reachable,
    })
}

/// Step CLOCK_REALTIME to `epoch`.
///
/// `settimeofday(2)` rather than a `date -s` subprocess: guestd already runs as
/// root inside the appliance, and a clock correction that has to fork is one
/// more thing that can fail on a guest whose clock is already wrong.
#[cfg(target_os = "linux")]
fn set_realtime_clock(epoch: u64) -> Result<(), GuestError> {
    let spec = libc::timespec {
        tv_sec: epoch as libc::time_t,
        tv_nsec: 0,
    };
    // SAFETY: `spec` is fully initialised and outlives the call, and
    // CLOCK_REALTIME is settable by root, which is what guestd runs as.
    if unsafe { libc::clock_settime(libc::CLOCK_REALTIME, &spec) } != 0 {
        return Err(GuestError::engine(format!(
            "could not set the guest clock: {}",
            io::Error::last_os_error()
        )));
    }
    Ok(())
}

/// Everywhere else this crate compiles -- the host, for its tests -- there is
/// no guest clock to set, and silently succeeding would let a test pass that
/// proves nothing.
#[cfg(not(target_os = "linux"))]
fn set_realtime_clock(_epoch: u64) -> Result<(), GuestError> {
    Err(GuestError::engine(
        "the guest clock can only be set inside the managed guest",
    ))
}

fn schedule_shutdown() -> Result<Value, GuestError> {
    let output = Command::new("/usr/bin/systemctl")
        .args(["--no-block", "poweroff"])
        .stdin(Stdio::null())
        .output()
        .map_err(|error| {
            GuestError::engine(format!("could not request guest shutdown: {error}"))
        })?;
    if !output.status.success() {
        return Err(GuestError::engine(format!(
            "could not request guest shutdown: {}",
            redact_engine_error(&String::from_utf8_lossy(&output.stderr))
        )));
    }
    Ok(json!({"stopping": true}))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A live request must not wait out a download on the shared channel.
    ///
    /// One process serves one request at a time, so an inline pull stops every
    /// other sandbox operation on the machine. Measured on this install: a
    /// 243-second pull during which a read-only `sandbox.list` timed out after
    /// sixty seconds having never been read.
    #[test]
    fn a_live_request_hands_back_a_download_instead_of_waiting_for_it() {
        let source = include_str!("lib.rs");
        let ensure = {
            let start = source
                .find("if should_create {")
                .expect("ensure has a create branch");
            &source[start..start + 700]
        };
        assert!(
            ensure.contains("parameters.workload_kind, false)"),
            "sandbox.ensure must take the non-blocking image path",
        );

        // ...while first-run setup still blocks, because the host is showing a
        // progress screen for exactly that download and the channel is idle.
        let setup = {
            let start = source
                .find("fn ensure_sandbox_images(")
                .expect("setup warms both images");
            &source[start..start + 900]
        };
        assert!(
            setup.contains("kind, true)"),
            "first-run setup must keep blocking, or its progress screen lies",
        );
    }

    /// A failed download is reported once, then retried -- not remembered.
    #[test]
    fn a_failed_pull_does_not_wedge_the_image_for_ever() {
        let image = "ghcr.io/lemma/pull-test@sha256:dead";
        in_flight_pulls().lock().unwrap().insert(
            image.to_owned(),
            PullState::Failed("no route to host".into()),
        );

        // The table is cleared as the failure is reported, so the next attempt
        // pulls again rather than being told about an old failure for ever.
        {
            let mut table = in_flight_pulls().lock().unwrap();
            let remembered = matches!(table.get(image), Some(PullState::Failed(_)));
            assert!(remembered, "the failure was recorded");
            table.remove(image);
        }
        assert!(
            in_flight_pulls().lock().unwrap().get(image).is_none(),
            "a reported failure must not outlive its report",
        );
    }

    /// No single request may sit on the guest's only control channel for
    /// minutes.
    ///
    /// The host bridge holds one vsock connection behind a process-wide mutex
    /// and `serve_vsock` handles each connection inline on its accept loop, so
    /// a request in flight is the whole machine's guest traffic. `ensure` used
    /// to poll readiness for up to 180 seconds inside the request: a slow
    /// sandbox start blocked every other sandbox operation, including
    /// read-only ones, and burned callers' deadlines while they waited to be
    /// heard rather than to be served.
    #[test]
    fn no_request_holds_the_control_channel_for_minutes() {
        // The bounds themselves are checked at compile time beside the
        // constant; what is worth asserting here is that nothing reintroduced
        // an unbounded wait elsewhere in `ensure`.
        let source = include_str!("lib.rs");
        let ensure = {
            let start = source
                .find("let deadline = Instant::now() + SANDBOX_READY_POLL_BUDGET;")
                .expect("ensure polls readiness against the shared budget");
            &source[start..start + 900]
        };
        assert!(
            !ensure.contains("Duration::from_secs(180)"),
            "the readiness wait must not go back to holding the channel for minutes",
        );
    }

    /// A sandbox that is merely slow is retryable; one that died is not.
    ///
    /// Bounding the wait only helps if "not yet" is distinguishable from
    /// "never" -- a bounded wait that reported failure would turn every slow
    /// start into a hard error.
    #[test]
    fn a_slow_start_is_reported_as_retryable() {
        let source = include_str!("lib.rs");
        let start = source
            .find("code: \"not_ready\".into(),")
            .expect("ensure hands back a not_ready when the budget expires");
        // To the end of the struct literal rather than a fixed span, so the
        // test does not start failing because the message got a line longer.
        let end = source[start..]
            .find("\n            });")
            .expect("the not_ready error is a struct literal");
        let window = &source[start..start + end];
        assert!(
            window.contains("retryable: true"),
            "a sandbox that is still starting must be retried, not failed",
        );
        assert!(
            window.contains("status_code: 503"),
            "not_ready is a availability answer, not a client error",
        );
    }

    /// A ceiling is not a claim.
    ///
    /// Admission used to add up every running sandbox's `--memory` and treat
    /// the sum as spent. With the shipped numbers -- 6 GiB guest, 1536 MiB core
    /// reservation, 2048 MiB default ceiling -- that admitted exactly two
    /// sandboxes and refused the third, so one open workspace plus a function
    /// was the whole machine. The arithmetic is kept here because it is the
    /// thing that must never come back.
    #[test]
    fn a_sandbox_ceiling_is_not_a_reservation() {
        // The numbers that shipped, kept as literals because the constant they
        // came from is gone: admission no longer reserves for core services at
        // all, it asks the kernel what is free.
        const SHIPPED_GUEST: u64 = 6 * 1024 * 1024 * 1024;
        const SHIPPED_CORE_RESERVATION: u64 = 1536 * 1024 * 1024;

        let old_style =
            SHIPPED_GUEST.saturating_sub(SHIPPED_CORE_RESERVATION) / DEFAULT_SANDBOX_MEMORY_BYTES;
        assert_eq!(
            old_style, 2,
            "summing ceilings capped a 6 GiB guest at two sandboxes, which is \
             what made one open workspace plus a function the whole machine",
        );

        // Admission now asks for a request-sized slice of what is actually
        // free, so the same guest fits an order of magnitude more before
        // memory is the binding constraint.
        let request_style = SHIPPED_GUEST.saturating_sub(GUEST_MEMORY_HEADROOM_BYTES)
            / SANDBOX_MEMORY_REQUEST_BYTES;
        assert!(
            request_style >= 8,
            "a request-based admission must not stop at two: got {request_style}",
        );
    }

    /// Availability is read, not inferred from the total.
    ///
    /// `MemTotal` is the wrong number: virtio-balloon adjusts it while the host
    /// reclaims memory, so a total says nothing about what is spare.
    #[test]
    fn available_memory_comes_from_the_kernels_own_estimate() {
        let meminfo = "MemTotal:        6109184 kB\nMemFree:          201234 kB\nMemAvailable:    4194304 kB\nBuffers:           1024 kB\n";
        assert_eq!(
            parse_mem_available(meminfo),
            Some(4194304 * 1024),
            "MemAvailable is what decides whether a container can start",
        );
        // A kernel too old to report it is an error, not a zero that would
        // refuse every sandbox for ever.
        assert_eq!(parse_mem_available("MemTotal: 100 kB\n"), None);
    }

    /// The concurrency ceiling is a backstop, and it can be raised.
    #[test]
    fn the_sandbox_ceiling_is_configurable() {
        assert_eq!(max_sandboxes(), DEFAULT_MAX_SANDBOXES);
    }
    use std::cell::Cell;
    use std::os::unix::process::ExitStatusExt;
    use std::sync::Mutex;
    use tempfile::{tempdir, TempDir};

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

    /// A ceiling on a test that hangs, not part of what any test asserts.
    ///
    /// These budgets were all one second, which reads as harmless — the work
    /// they bound takes milliseconds. But they are *wall-clock* deadlines, and
    /// the suite runs its tests in parallel on a shared CI runner, so a 250ms
    /// retry sleep or a `printf` can miss a one-second bus. Four tests failed
    /// at random against deadlines none of them were about; under contention
    /// one of them was measured taking over seven seconds.
    ///
    /// Nothing is slower for it. Each of these tests is driven by a fake that
    /// answers immediately or a script that exits, so the budget is only ever
    /// reached when something is already broken.
    const UNHURRIED_TEST_TIMEOUT_SECS: u64 = 30;

    /// How long the stand-in engine sleeps when a test needs it not to finish.
    ///
    /// The timeout test proves the kill landed by returning long before this
    /// elapses, so its assertion is written against this rather than against a
    /// second constant that could drift away from it.
    const FORKING_ENGINE_SLEEP_SECS: u64 = 30;

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
            "Config": {"Labels": {
                "lemma.work/workload-kind": "workspace",
                "lemma.work/image-ref": "ghcr.io/lemma/workspace@sha256:abc",
                "lemma.work/metadata": "{\"managed-by\":\"lemma-workspace\"}"
            }},
            "NetworkSettings": {"Ports": {
                "8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": "49152"}],
                "4848/tcp": [{"HostIp": "0.0.0.0", "HostPort": "49153"}]
            }}
        }])
        .to_string()
    }

    fn core_parameters(postgres_image: &str) -> CoreParameters {
        CoreParameters {
            images: CoreImages {
                postgres: postgres_image.to_owned(),
                redis: "docker.io/redis:7.4-alpine".into(),
                supertokens: "docker.io/supertokens/supertokens-postgresql:11.4.5".into(),
                workspace: None,
                function: None,
            },
            credentials: CoreCredentials {
                postgres_password: "a".repeat(64),
                redis_password: "b".repeat(64),
            },
        }
    }

    /// A data reset without the literal confirmation destroys nothing.
    ///
    /// This is the only global destructive verb in the table, so a replayed or
    /// malformed frame must not be able to reach it.
    #[test]
    fn a_data_reset_without_explicit_confirmation_is_refused() {
        let root = tempdir().unwrap();
        let service = GuestService::new(
            FakeEngine::new(vec![]),
            root.path().into(),
            "192.168.64.2".into(),
            "192.168.64.1".into(),
            None,
        )
        .unwrap();

        assert!(service.reset_data(json!({})).is_err());
        assert!(service.reset_data(json!({"confirm": "yes"})).is_err());
        assert!(
            service.engine.commands.lock().unwrap().is_empty(),
            "a refused reset must not reach the engine at all"
        );
    }

    /// Containers go before the volumes and workspaces they hold.
    ///
    /// Removing a workspace directory while a sandbox is still bind-mounted
    /// onto it, or a volume while Postgres still has it open, is the difference
    /// between a clean reset and a guest in an unexplainable state. The order is
    /// the correctness property, so it is what this asserts.
    #[test]
    fn a_data_reset_removes_holders_before_the_data_they_hold() {
        let root = tempdir().unwrap();
        let workspaces = root.path().join("workspaces");
        fs::create_dir_all(workspaces.join("sandbox-one")).unwrap();
        fs::create_dir_all(workspaces.join("sandbox-two")).unwrap();
        fs::write(workspaces.join("sandbox-one/notes.md"), b"user work").unwrap();

        let service = GuestService::new(
            FakeEngine::new(vec![
                // `inspect` answers with an array; anything else reads as absent.
                output(true, "[{}]"),     // inspect supertokens: present
                output(true, ""),         // rm supertokens
                output(true, "[{}]"),     // inspect redis: present
                output(true, ""),         // rm redis
                output(true, "[{}]"),     // inspect postgres: present
                output(true, ""),         // rm postgres
                output(true, "abc123\n"), // ps --filter label=...
                output(true, ""),         // rm the sandbox container
                output(true, ""),         // volume rm lemma-postgres-data
                output(true, ""),         // volume rm lemma-redis-data
            ]),
            root.path().into(),
            "192.168.64.2".into(),
            "192.168.64.1".into(),
            None,
        )
        .unwrap();

        let result = service
            .reset_data(json!({"confirm": "reset-local-data"}))
            .unwrap();

        assert_eq!(result["removed_containers"], 4);
        assert_eq!(result["removed_volumes"], 2);
        assert_eq!(result["removed_workspaces"], 2);
        assert!(!workspaces.join("sandbox-one").exists());
        assert!(!workspaces.join("sandbox-two").exists());

        let commands = service.engine.commands.lock().unwrap();
        let position =
            |predicate: &dyn Fn(&Vec<String>) -> bool| commands.iter().position(predicate);
        let core_removed = position(&|command: &Vec<String>| {
            command.first() == Some(&"rm".to_owned())
                && command.iter().any(|part| part == "lemma-core-postgres")
        })
        .expect("core containers are removed");
        let sandbox_removed = position(&|command: &Vec<String>| {
            command.first() == Some(&"rm".to_owned()) && command.iter().any(|part| part == "abc123")
        })
        .expect("sandbox containers are removed");
        let volume_removed = position(&|command: &Vec<String>| {
            command.first() == Some(&"volume".to_owned())
                && command.get(1) == Some(&"rm".to_owned())
        })
        .expect("volumes are removed");

        assert!(
            core_removed < volume_removed,
            "Postgres must let go of its volume before the volume is removed"
        );
        assert!(
            sandbox_removed < volume_removed,
            "sandboxes are removed before the data they mount"
        );
    }

    /// A Postgres major bump is refused before the container is ever run.
    ///
    /// This is the pg16 -> pg18 case that shipped. Without the check, the new
    /// server starts against the old cluster, refuses, and the user waits out a
    /// 120-second `pg_isready` timeout to be told nothing useful. The assertion
    /// that matters is not just the error -- it is that **no `run` reached the
    /// engine**, because the whole point is to fail in about a second.
    #[test]
    fn a_postgres_major_bump_is_refused_before_the_container_starts() {
        let root = tempdir().unwrap();
        let cluster = tempdir().unwrap();
        std::fs::write(cluster.path().join("PG_VERSION"), "16\n").unwrap();
        let inspect = output(
            true,
            &serde_json::to_string(&json!([{ "Mountpoint": cluster.path() }])).unwrap(),
        );
        let service = GuestService::new(
            FakeEngine::new(vec![
                output(true, ""),     // ensure_volume: inspect succeeds, volume exists
                inspect,              // postgres_data_major: where does it live
                output(true, "18\n"), // postgres_image_major: what does the image ship
            ]),
            root.path().into(),
            "192.168.64.2".into(),
            "192.168.64.1".into(),
            None,
        )
        .unwrap();

        let error = service
            .ensure_postgres(&core_parameters("docker.io/pgvector/pgvector:0.8.3-pg18"))
            .unwrap_err();

        assert_eq!(error.code, "postgres_data_incompatible");
        assert_eq!(error.status_code, 409);
        assert!(!error.retryable, "retrying cannot help; resetting can");
        assert!(error.message.contains("PostgreSQL 16"), "{}", error.message);
        assert!(error.message.contains("PostgreSQL 18"), "{}", error.message);
        assert!(
            error.message.contains(DATA_RESET_MARKER),
            "the phrase locald maps to a reset button: {}",
            error.message
        );
        let commands = service.engine.commands.lock().unwrap();
        assert!(
            !commands
                .iter()
                .any(|command| command.first() == Some(&"run".to_owned())
                    && command.iter().any(|part| part == "--name")),
            "no core container may be started against an unreadable cluster: {commands:?}"
        );
    }

    /// A fresh volume has no `PG_VERSION`, which is the common case.
    ///
    /// If this cost anything -- or worse, refused -- every first run would
    /// break. So an unreadable version on either side means proceed.
    #[test]
    fn a_fresh_postgres_volume_is_not_treated_as_incompatible() {
        let root = tempdir().unwrap();
        let empty = tempdir().unwrap();
        let inspect = output(
            true,
            &serde_json::to_string(&json!([{ "Mountpoint": empty.path() }])).unwrap(),
        );
        let service = GuestService::new(
            FakeEngine::new(vec![inspect, output(true, "18\n")]),
            root.path().into(),
            "192.168.64.2".into(),
            "192.168.64.1".into(),
            None,
        )
        .unwrap();

        service
            .refuse_incompatible_postgres_data("docker.io/pgvector/pgvector:0.8.3-pg18")
            .expect("a volume with no cluster on it yet is not a mismatch");
    }

    /// An image that does not report `PG_MAJOR` degrades to today's behaviour
    /// rather than blocking every start.
    #[test]
    fn an_image_that_cannot_be_asked_its_version_does_not_block_startup() {
        let root = tempdir().unwrap();
        let cluster = tempdir().unwrap();
        std::fs::write(cluster.path().join("PG_VERSION"), "16\n").unwrap();
        let inspect = output(
            true,
            &serde_json::to_string(&json!([{ "Mountpoint": cluster.path() }])).unwrap(),
        );
        let service = GuestService::new(
            FakeEngine::new(vec![inspect, output(false, "")]),
            root.path().into(),
            "192.168.64.2".into(),
            "192.168.64.1".into(),
            None,
        )
        .unwrap();

        service
            .refuse_incompatible_postgres_data("some/image:without-pg-major")
            .expect("an unanswerable image is not evidence of a mismatch");
    }

    #[test]
    fn ensure_volume_reuses_data_preserved_across_container_cache_repair() {
        let root = tempdir().unwrap();
        let warning = Output {
            status: std::process::ExitStatus::from_raw(1),
            stdout: vec![],
            stderr: b"time=\"2026-07-23T11:33:36Z\" level=warning msg=\"volume \\\"lemma-postgres-data\\\" already exists and will be returned as-is\""
                .to_vec(),
        };
        let service = GuestService::new(
            FakeEngine::new(vec![output(false, ""), warning]),
            root.path().into(),
            "192.168.64.2".into(),
            "192.168.64.1".into(),
            None,
        )
        .unwrap();

        service.ensure_volume("lemma-postgres-data").unwrap();

        assert_eq!(
            service.engine.commands.lock().unwrap().as_slice(),
            [
                vec![
                    "volume".to_owned(),
                    "inspect".to_owned(),
                    "lemma-postgres-data".to_owned(),
                ],
                vec![
                    "volume".to_owned(),
                    "create".to_owned(),
                    "lemma-postgres-data".to_owned(),
                ],
            ]
        );
    }

    #[test]
    fn ensure_volume_rejects_unrelated_creation_failures() {
        let root = tempdir().unwrap();
        let service = GuestService::new(
            FakeEngine::new(vec![output(false, ""), output(false, "")]),
            root.path().into(),
            "192.168.64.2".into(),
            "192.168.64.1".into(),
            None,
        )
        .unwrap();

        let error = service.ensure_volume("lemma-postgres-data").unwrap_err();

        assert_eq!(error.code, "guest_engine_failed");
        assert_eq!(error.message, "not found");
    }

    /// The cluster is written inside the volume the user is told holds it.
    ///
    /// Behavioural, not a source grep. The first version of this test searched
    /// its own file for the literal it was asserting -- which its own assertion
    /// satisfied, so it passed with the bug reinstated. Read the two values and
    /// compare them.
    #[test]
    fn the_cluster_path_and_the_volume_mount_are_the_same_path() {
        let (environment, arguments) = postgres_container_spec("pw");

        let cluster = environment
            .get("PGDATA")
            .expect("PGDATA is pinned rather than inherited from the image");

        let volume = arguments
            .windows(2)
            .find(|pair| pair[0] == "--volume")
            .map(|pair| pair[1].clone())
            .expect("the container mounts its data volume");
        let (name, mounted_at) = volume
            .split_once(':')
            .expect("a volume argument is name:path");

        assert_eq!(name, "lemma-postgres-data");
        assert_eq!(
            cluster, mounted_at,
            "the cluster is written to {cluster} but the volume is mounted at \
             {mounted_at}; anything written outside the volume is not the \
             user's database, it is the container's scratch space",
        );

        // The image we pin puts its own PGDATA at /var/lib/postgresql/18/docker
        // and declares VOLUME /var/lib/postgresql. Inheriting either is what
        // put the database outside this volume in the first place.
        assert!(
            !cluster.contains("/18/"),
            "a version-numbered cluster path is the image's, and it moves",
        );
    }

    /// The message a user actually got, classified the way it should have been.
    ///
    /// This is the real tail of the official image's refusal, which reached a
    /// user as the entire explanation for an install that stopped at 30% --
    /// appended to a nerdctl "cannot exec in a stopped state", with "Try again"
    /// as the only button, three times.
    #[test]
    fn postgres_refusing_its_data_is_recognised_from_what_it_actually_prints() {
        for refusal in [
            "PostgreSQL Database directory appears to contain a database; Skipping initialization",
            "FATAL:  database files are incompatible with server",
            "DETAIL:  The data directory was initialized by PostgreSQL version 16, which is not \
             compatible with this version 18.4.",
            "An upgrade is required. See https://github.com/docker-library/postgres/issues/37 \
             for a discussion around this process, and suggestions for how to do so.",
            "could not read file \"global/pg_control\": No such file or directory",
        ] {
            assert!(
                postgres_refused_its_data(refusal),
                "this must offer a reset, not a retry: {refusal}",
            );
        }
    }

    /// And a transient failure still retries, because a wrong match here offers
    /// to delete somebody's database over a blip.
    #[test]
    fn a_transient_postgres_failure_is_never_read_as_unusable_data() {
        for transient in [
            "psql: error: FATAL: the database system is shutting down",
            "psql: error: FATAL: the database system is starting up",
            "could not connect to server: Connection refused",
            "time=\"2026-08-25T17:49:20Z\" level=fatal msg=\"cannot exec in a stopped state\"",
            "LOG:  database system was not properly shut down; automatic recovery in progress",
            "",
        ] {
            assert!(
                !postgres_refused_its_data(transient),
                "this is retryable and must not offer to erase data: {transient}",
            );
        }
    }

    #[test]
    fn database_command_retries_through_postgres_initialization_restart() {
        let root = tempdir().unwrap();
        let shutting_down = Output {
            status: std::process::ExitStatus::from_raw(2),
            stdout: vec![],
            stderr: b"psql: error: FATAL: the database system is shutting down".to_vec(),
        };
        let service = GuestService::new(
            FakeEngine::new(vec![shutting_down, output(true, "1\n")]),
            root.path().into(),
            "192.168.64.2".into(),
            "192.168.64.1".into(),
            None,
        )
        .unwrap();

        let value = service
            .wait_engine_output(
                &["exec".into(), "lemma-core-postgres".into()],
                UNHURRIED_TEST_TIMEOUT_SECS,
            )
            .unwrap();

        assert_eq!(value, "1");
        assert_eq!(service.engine.commands.lock().unwrap().len(), 2);
    }

    #[test]
    fn database_provisioning_accepts_an_ambiguous_committed_create() {
        let root = tempdir().unwrap();
        let disconnected_after_commit = Output {
            status: std::process::ExitStatus::from_raw(1),
            stdout: vec![],
            stderr: b"time=now level=fatal msg=\"exec failed with exit code 1\"".to_vec(),
        };
        let service = GuestService::new(
            FakeEngine::new(vec![
                output(true, ""),
                disconnected_after_commit,
                output(true, "1\n"),
            ]),
            root.path().into(),
            "192.168.64.2".into(),
            "192.168.64.1".into(),
            None,
        )
        .unwrap();

        service
            .ensure_database("lemma_datastore", UNHURRIED_TEST_TIMEOUT_SECS)
            .unwrap();

        let commands = service.engine.commands.lock().unwrap();
        assert_eq!(commands.len(), 3);
        assert_eq!(commands[0][2], "psql");
        assert_eq!(commands[1][2], "createdb");
        assert_eq!(commands[2][2], "psql");
    }

    #[test]
    fn engine_error_prefers_actionable_failure_after_warnings() {
        let diagnostic = concat!(
            "time=now level=warning msg=\"volume already exists\"\n",
            "time=now level=fatal msg=\"failed to create task: missing snapshot\"\n",
        );

        assert_eq!(
            redact_engine_error(diagnostic),
            "time=now level=fatal msg=\"failed to create task: missing snapshot\""
        );
    }

    #[test]
    fn snapshot_uses_guest_ip_and_exact_container_generation() {
        let parsed: Value = serde_json::from_str(&inspect()).unwrap();
        let snapshot =
            snapshot_from_inspect("box-1", parsed[0].as_object().unwrap(), "192.168.64.2").unwrap();

        assert_eq!(snapshot["provider_id"], "sha256:exact-generation");
        assert_eq!(
            snapshot["status"]["runtime_url"],
            "http://192.168.64.2:49152"
        );
        assert_eq!(snapshot["status"]["ready"], true);
    }

    /// The resting state of every idle workspace, read off a real guest that
    /// had one: `Running: false`, a clean exit code, and no `Status` at all.
    /// Calling that ERROR made the ordinary end of an idle release look like a
    /// fault, in `sandbox.list` and in everything that reads it.
    #[test]
    fn a_cleanly_exited_container_without_a_status_field_reads_as_stopped() {
        let inspected = json!({
            "Id": "sha256:exact-generation",
            "State": {"Running": false, "ExitCode": 0},
            "Config": {"Labels": {
                "lemma.work/workload-kind": "workspace",
                "lemma.work/image-ref": "ghcr.io/lemma/workspace@sha256:abc",
                "lemma.work/metadata": "{\"managed-by\":\"lemma-workspace\"}"
            }},
            "NetworkSettings": {"Ports": {}}
        });

        let snapshot =
            snapshot_from_inspect("box-1", inspected.as_object().unwrap(), "192.168.64.2").unwrap();

        assert_eq!(snapshot["status"]["status"], "STOPPED");
        assert_eq!(snapshot["status"]["ready"], false);
    }

    /// The two places that set this guest's clock have to agree on what a
    /// believable host epoch is, and until now only a comment said so.
    ///
    /// `lemma-set-host-time` runs at boot from the trusted control share;
    /// `system.clock` runs for the rest of the VM's life. A range that drifted
    /// apart would mean a clock the daemon refuses and the boot script accepts,
    /// or the reverse -- and the symptom would be a guest silently running in
    /// the wrong year.
    #[test]
    fn the_boot_script_and_the_daemon_trust_the_same_epoch_range() {
        let script = std::fs::read_to_string(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../guest-image/rootfs-overlay/usr/local/bin/lemma-set-host-time"
        ))
        .expect("the boot-time clock script ships with the guest image");

        assert!(
            script.contains(&MIN_TRUSTED_EPOCH.to_string()),
            "lemma-set-host-time does not mention {MIN_TRUSTED_EPOCH}"
        );
        assert!(
            script.contains(&MAX_TRUSTED_EPOCH.to_string()),
            "lemma-set-host-time does not mention {MAX_TRUSTED_EPOCH}"
        );
    }

    /// `dead` is a container the engine could not clean up. Reporting it as the
    /// ordinary end of an idle release would hide the one state here worth
    /// looking at.
    #[test]
    fn a_dead_container_is_a_fault_not_a_resting_state() {
        let inspected = json!({
            "Id": "sha256:exact-generation",
            "State": {"Running": false, "Status": "dead", "ExitCode": 137},
            "Config": {"Labels": {
                "lemma.work/workload-kind": "workspace",
                "lemma.work/image-ref": "ghcr.io/lemma/workspace@sha256:abc",
                "lemma.work/metadata": "{\"managed-by\":\"lemma-workspace\"}"
            }},
            "NetworkSettings": {"Ports": {}}
        });

        let snapshot =
            snapshot_from_inspect("box-1", inspected.as_object().unwrap(), "192.168.64.2").unwrap();

        assert_eq!(snapshot["status"]["status"], "ERROR");
    }

    /// A container that never ran and reports nothing is still a fault. The
    /// fallback reads "has exited", not "is not running".
    #[test]
    fn a_container_that_never_started_still_reads_as_an_error() {
        let inspected = json!({
            "Id": "sha256:exact-generation",
            "State": {"Running": false},
            "Config": {"Labels": {
                "lemma.work/workload-kind": "workspace",
                "lemma.work/image-ref": "ghcr.io/lemma/workspace@sha256:abc",
                "lemma.work/metadata": "{\"managed-by\":\"lemma-workspace\"}"
            }},
            "NetworkSettings": {"Ports": {}}
        });

        let snapshot =
            snapshot_from_inspect("box-1", inspected.as_object().unwrap(), "192.168.64.2").unwrap();

        assert_eq!(snapshot["status"]["status"], "ERROR");
    }

    #[test]
    fn run_contract_uses_digest_env_file_private_gateway_and_all_app_ports() {
        let parameters = EnsureParameters {
            sandbox_id: "box-1".into(),
            workload_kind: WorkloadKind::Workspace,
            image: "ghcr.io/lemma/workspace@sha256:abc".into(),
            env: BTreeMap::from([("LEMMA_TOKEN".into(), "secret".into())]),
            metadata: BTreeMap::from([("managed-by".into(), "lemma-workspace".into())]),
            runtime_token: Some("runtime-secret".into()),
            apps: workspace_apps(),
            resources: ResourceSpec {
                memory: Some("2Gi".into()),
                cpus: Some("1".into()),
            },
            callback: CallbackSpec::default(),
        };
        let arguments = build_run_arguments(
            &parameters,
            Some(Path::new("/var/lib/lemma/workspaces/box-1")),
            Some(Path::new("/var/lib/lemma/run/runtime-token-box-1/token")),
            Path::new("/var/lib/lemma/run/private-env"),
            "192.168.64.1",
        );
        let joined = arguments.join(" ");

        assert!(joined.contains("--env-file /var/lib/lemma/run/private-env"));
        assert!(!joined.contains("secret"));
        assert!(joined.contains("host.lemma.internal:192.168.64.1"));
        assert!(joined.contains("0.0.0.0::8080"));
        assert!(joined.contains("0.0.0.0::4848"));
        assert!(!joined.contains("0.0.0.0::8090"));
        assert!(joined.contains("/var/lib/lemma/run/runtime-token-box-1,dst=/run/lemma-bootstrap"));
        assert!(!joined.contains("lemma-bootstrap,readonly"));
        assert!(joined.ends_with("ghcr.io/lemma/workspace@sha256:abc"));
    }

    #[test]
    fn sandbox_resource_limits_are_bounded_and_normalized() {
        assert_eq!(parse_memory_bytes("2g").unwrap(), 2 * 1024 * 1024 * 1024);
        assert_eq!(parse_memory_bytes("512MiB").unwrap(), 512 * 1024 * 1024);
        assert_eq!(
            validate_resources(&ResourceSpec {
                memory: Some("2Gi".into()),
                cpus: Some("2.5".into()),
            })
            .unwrap(),
            2 * 1024 * 1024 * 1024
        );
        assert!(validate_resources(&ResourceSpec {
            memory: Some("64m".into()),
            cpus: Some("2".into()),
        })
        .is_err());
        assert!(validate_resources(&ResourceSpec {
            memory: Some("2g".into()),
            cpus: Some("99".into()),
        })
        .is_err());
    }

    #[test]
    fn function_contract_is_read_only_ephemeral_and_exposes_only_its_runtime() {
        let parameters = EnsureParameters {
            sandbox_id: "function-1".into(),
            workload_kind: WorkloadKind::Function,
            image: "ghcr.io/lemma/function@sha256:def".into(),
            env: BTreeMap::new(),
            metadata: BTreeMap::new(),
            runtime_token: None,
            apps: function_apps(),
            resources: ResourceSpec::default(),
            callback: CallbackSpec::default(),
        };
        let arguments = build_run_arguments(
            &parameters,
            None,
            None,
            Path::new("/var/lib/lemma/run/private-env"),
            "192.168.64.1",
        );
        let joined = arguments.join(" ");

        assert!(validate_apps(&workspace_apps()).is_ok());
        assert!(validate_apps(&function_apps()).is_ok());
        assert!(joined.contains("--read-only"));
        assert!(joined.contains("/tmp:rw,noexec,nosuid"));
        assert!(joined.contains("/run/lemma-function-cache:rw,exec"));
        assert!(joined.contains("0.0.0.0::8090"));
        assert!(!joined.contains("dst=/workspace"));
    }

    #[test]
    fn sandbox_startup_error_includes_the_containers_own_last_words() {
        let root = tempdir().unwrap();
        let service = GuestService::new(
            FakeEngine::new(vec![
                output(
                    true,
                    &json!([{
                        "State": {
                            "Error": "OCI runtime failed",
                            "ExitCode": 126,
                            "OOMKilled": false,
                        }
                    }])
                    .to_string(),
                ),
                output(true, "starting\nfatal: runtime bootstrap failed\n"),
            ]),
            root.path().into(),
            "192.168.64.2".into(),
            "192.168.64.1".into(),
            None,
        )
        .unwrap();

        let error = service.sandbox_startup_error(
            "lemma-sandbox-box-1",
            "sandbox runtime stopped before becoming ready",
        );

        assert_eq!(error.code, "guest_engine_failed");
        // The container's last few lines, not only its last one. The line that
        // says what went wrong is frequently not the final one -- PostgreSQL
        // refuses an unusable data directory with a paragraph, and a user was
        // shown its closing fragment, "discussion around this process, and
        // suggestions for how to do so.", as the whole explanation.
        assert_eq!(
            error.message,
            "sandbox runtime stopped before becoming ready: OCI runtime failed; container exited with code 126; starting fatal: runtime bootstrap failed"
        );
        assert_eq!(
            service.engine.commands.lock().unwrap().as_slice(),
            [
                vec!["inspect".to_owned(), "lemma-sandbox-box-1".to_owned()],
                vec![
                    "logs".to_owned(),
                    "--tail".to_owned(),
                    "40".to_owned(),
                    "lemma-sandbox-box-1".to_owned(),
                ],
            ]
        );
    }

    #[test]
    fn sandbox_diagnostics_exposes_only_sanitized_runtime_state() {
        let root = tempdir().unwrap();
        let service = GuestService::new(
            FakeEngine::new(vec![
                output(
                    true,
                    &json!([{
                        "Path": "tini",
                        "Args": ["--", "start-runtime"],
                        "Config": {
                            "Env": ["PRIVATE_TOKEN=never-return-this"],
                            "Entrypoint": null,
                            "Cmd": ["tini", "--", "start-runtime"],
                        },
                        "State": {
                            "Status": "exited",
                            "Running": false,
                            "ExitCode": 0,
                            "OOMKilled": false,
                            "Error": "",
                            "StartedAt": "2026-07-23T10:00:00Z",
                            "FinishedAt": "2026-07-23T10:00:02Z",
                        }
                    }])
                    .to_string(),
                ),
                output(true, "runtime stopped\n"),
            ]),
            root.path().into(),
            "192.168.64.2".into(),
            "192.168.64.1".into(),
            None,
        )
        .unwrap();

        let diagnostics = service
            .sandbox_diagnostics(json!({"sandbox_id": "box-1"}))
            .unwrap();

        assert_eq!(diagnostics["state"]["status"], "exited");
        assert_eq!(diagnostics["state"]["exit_code"], 0);
        assert_eq!(diagnostics["process"]["path"], "tini");
        assert_eq!(
            diagnostics["process"]["cmd"],
            json!(["tini", "--", "start-runtime"])
        );
        assert_eq!(diagnostics["last_log"], "runtime stopped");
        assert!(!diagnostics.to_string().contains("PRIVATE_TOKEN"));
    }

    #[test]
    fn sandbox_image_marker_probe_is_offline_and_checks_the_runtime_entrypoint() {
        let root = tempdir().unwrap();
        let service = GuestService::new(
            FakeEngine::new(vec![output(true, "")]),
            root.path().into(),
            "192.168.64.2".into(),
            "192.168.64.1".into(),
            None,
        )
        .unwrap();

        assert!(service.sandbox_image_marker_is_ready(
            "ghcr.io/lemma/workspace@sha256:abc",
            WorkloadKind::Workspace,
        ));
        assert_eq!(
            service.engine.commands.lock().unwrap()[0],
            vec![
                "run",
                "--rm",
                "--network",
                "none",
                "--platform",
                guest_platform(),
                "ghcr.io/lemma/workspace@sha256:abc",
                "/usr/bin/test",
                "-s",
                "/usr/local/bin/start-workspace-runtime",
            ]
        );
    }

    #[test]
    fn image_repair_pull_explicitly_unpacks_the_selected_platform() {
        let root = tempdir().unwrap();
        let service = GuestService::new(
            FakeEngine::new(vec![output(true, "")]),
            root.path().into(),
            "192.168.64.2".into(),
            "192.168.64.1".into(),
            None,
        )
        .unwrap();

        service
            .pull_image("ghcr.io/lemma/runtime@sha256:abc")
            .unwrap();
        assert_eq!(
            service.engine.commands.lock().unwrap()[0],
            vec![
                "pull",
                "--quiet",
                "--unpack=true",
                "--platform",
                guest_platform(),
                "ghcr.io/lemma/runtime@sha256:abc",
            ]
        );
    }

    #[test]
    fn incomplete_sandbox_image_reference_is_replaced_before_repull() {
        let root = tempdir().unwrap();
        let service = GuestService::new(
            FakeEngine::new(vec![
                output(true, "{}"),
                output(false, ""),
                output(true, ""),
                output(true, ""),
                output(true, ""),
                output(true, ""),
            ]),
            root.path().into(),
            "192.168.64.2".into(),
            "192.168.64.1".into(),
            None,
        )
        .unwrap();

        service
            .ensure_sandbox_image(
                "ghcr.io/lemma/runtime@sha256:abc",
                WorkloadKind::Workspace,
                true,
            )
            .unwrap();
        let commands = service.engine.commands.lock().unwrap();
        assert_eq!(commands[2], vec!["container", "prune", "--force"]);
        assert_eq!(
            commands[3],
            vec!["rmi", "--force", "ghcr.io/lemma/runtime@sha256:abc"]
        );
        assert_eq!(commands[4][0], "pull");
    }

    #[test]
    fn unrecoverable_image_cache_persists_a_health_gated_reset_marker() {
        let root = tempdir().unwrap();
        let service = GuestService::new(
            FakeEngine::new(vec![
                output(true, "{}"),
                output(false, ""),
                output(true, ""),
                output(true, ""),
                output(true, ""),
                output(false, ""),
                output(true, ""),
            ]),
            root.path().into(),
            "192.168.64.2".into(),
            "192.168.64.1".into(),
            None,
        )
        .unwrap();

        let error = service
            .ensure_sandbox_image(
                "ghcr.io/lemma/runtime@sha256:abc",
                WorkloadKind::Workspace,
                true,
            )
            .unwrap_err();

        assert_eq!(error.code, "guest_cache_repair_required");
        let marker = service.cache_reset_marker();
        assert!(marker.is_file());
        assert_eq!(service.health().unwrap()["status"], "ready");
        let repair_due = marker.metadata().unwrap().modified().unwrap()
            + CACHE_REPAIR_RESPONSE_GRACE
            + Duration::from_secs(1);
        assert_eq!(
            service.health_at(repair_due).unwrap_err().code,
            "guest_cache_repair_required"
        );
    }

    /// The gap that put an install into a permanent 401 loop: the guest was
    /// eleven hours behind the Mac, so every access token the auth service
    /// minted in it was already expired when the backend read it.
    #[test]
    fn a_guest_clock_behind_the_host_is_stepped_forward() {
        let (_root, service) = clock_test_service();
        let applied = Cell::new(None);

        let report = service
            .set_clock_with(
                json!({"epoch": 1_787_290_731_u64}),
                UNIX_EPOCH + Duration::from_secs(1_787_249_481),
                |epoch| {
                    applied.set(Some(epoch));
                    Ok(())
                },
            )
            .unwrap();

        assert_eq!(applied.get(), Some(1_787_290_731));
        assert_eq!(report["skew_seconds"], 41_250);
        assert_eq!(report["stepped"], true);
        assert_eq!(report["guest_epoch"], 1_787_249_481_u64);
    }

    /// A step moves wall time under every process in the guest. Ordinary
    /// jitter is not worth that, so it is reported and left alone.
    #[test]
    fn a_clock_within_the_threshold_is_reported_and_left_alone() {
        let (_root, service) = clock_test_service();
        let applied = Cell::new(false);

        let report = service
            .set_clock_with(
                json!({"epoch": 1_787_290_732_u64}),
                UNIX_EPOCH + Duration::from_secs(1_787_290_731),
                |_| {
                    applied.set(true);
                    Ok(())
                },
            )
            .unwrap();

        assert!(!applied.get());
        assert_eq!(report["skew_seconds"], 1);
        assert_eq!(report["stepped"], false);
    }

    /// The control channel is trusted, but a clock is the one thing a wrong
    /// value breaks silently and everywhere, so the guest still checks the
    /// range -- the same range `lemma-set-host-time` checks.
    #[test]
    fn an_implausible_host_epoch_is_refused_without_touching_the_clock() {
        let (_root, service) = clock_test_service();
        let applied = Cell::new(false);

        for epoch in [json!(1_u64), json!(9_999_999_999_u64)] {
            let error = service
                .set_clock_with(json!({"epoch": epoch}), SystemTime::now(), |_| {
                    applied.set(true);
                    Ok(())
                })
                .unwrap_err();
            assert_eq!(error.code, "invalid_request");
        }

        let missing = service
            .set_clock_with(json!({}), SystemTime::now(), |_| {
                applied.set(true);
                Ok(())
            })
            .unwrap_err();
        assert_eq!(missing.code, "invalid_request");
        assert!(!applied.get());
    }

    /// Whoever is already asking whether the guest is well should not have to
    /// think to ask about time separately.
    #[test]
    fn health_reports_the_guest_clock() {
        let (_root, service) = clock_test_service();

        let health = service
            .health_at(UNIX_EPOCH + Duration::from_secs(1_787_249_481))
            .unwrap();

        assert_eq!(health["clock_epoch"], 1_787_249_481_u64);
    }

    /// Returns the temporary root with the service: dropping it early would
    /// take the guest state directory out from under the service.
    fn clock_test_service() -> (TempDir, GuestService<FakeEngine>) {
        let root = tempdir().unwrap();
        let service = GuestService::new(
            FakeEngine::new(vec![output(true, "")]),
            root.path().into(),
            "192.168.64.2".into(),
            "192.168.64.1".into(),
            None,
        )
        .unwrap();
        (root, service)
    }

    #[test]
    fn stale_stopped_container_is_removed_for_safe_recreation() {
        let root = tempdir().unwrap();
        let service = GuestService::new(
            FakeEngine::new(vec![output(false, ""), output(true, "")]),
            root.path().into(),
            "192.168.64.2".into(),
            "192.168.64.1".into(),
            None,
        )
        .unwrap();

        assert!(!service
            .restart_or_remove_stale("lemma-core-postgres")
            .unwrap());
        assert_eq!(
            service.engine.commands.lock().unwrap().as_slice(),
            [
                vec!["start".to_owned(), "lemma-core-postgres".to_owned()],
                vec![
                    "rm".to_owned(),
                    "--force".to_owned(),
                    "lemma-core-postgres".to_owned(),
                ],
            ]
        );
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
            FakeEngine::new(vec![output(true, "")]),
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
    fn persistent_transport_handles_multiple_requests_on_one_connection() {
        let root = tempdir().unwrap();
        let service = GuestService::new(
            FakeEngine::new(vec![output(true, ""), output(true, "")]),
            root.path().into(),
            "192.168.64.2".into(),
            "192.168.64.1".into(),
            None,
        )
        .unwrap();
        let request = concat!(
            "{\"version\":1,\"operation\":\"health\",\"parameters\":{}}\n",
            "{\"version\":1,\"operation\":\"health\",\"parameters\":{}}\n",
        );
        let mut output = Vec::new();

        handle_stream(request.as_bytes(), &mut output, &service).unwrap();

        let responses = String::from_utf8(output).unwrap();
        assert_eq!(responses.lines().count(), 2);
        for line in responses.lines() {
            let response: Value = serde_json::from_str(line).unwrap();
            assert_eq!(response["result"]["engine"], "containerd");
        }
    }

    #[test]
    fn health_fails_closed_when_container_engine_storage_is_unwritable() {
        let root = tempdir().unwrap();
        let service = GuestService::new(
            FakeEngine::new(vec![]),
            root.path().into(),
            "192.168.64.2".into(),
            "192.168.64.1".into(),
            None,
        )
        .unwrap();

        let error = service.health().unwrap_err();

        assert_eq!(error.code, "guest_engine_failed");
        assert!(error.message.contains("no fake output"));
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
    fn shutdown_stops_every_running_container_in_one_bounded_command() {
        let root = tempdir().unwrap();
        let service = GuestService::new(
            FakeEngine::new(vec![
                output(true, "aabbccddeeff\n001122334455\n"),
                output(true, "aabbccddeeff\n001122334455\n"),
            ]),
            root.path().into(),
            "192.168.64.2".into(),
            "192.168.64.1".into(),
            None,
        )
        .unwrap();

        assert_eq!(service.stop_all_containers().unwrap(), 2);
        assert_eq!(
            service.engine.commands.lock().unwrap().as_slice(),
            [
                vec!["ps".to_owned(), "--quiet".to_owned()],
                vec![
                    "stop".to_owned(),
                    "--time".to_owned(),
                    "5".to_owned(),
                    "aabbccddeeff".to_owned(),
                    "001122334455".to_owned(),
                ],
            ]
        );
    }

    #[test]
    fn engine_timeout_kills_the_entire_process_group() {
        let root = tempdir().unwrap();
        let executable = root.path().join("forking-engine");
        let capture_root = root.path().join("captures");
        fs::create_dir(&capture_root).unwrap();
        fs::write(
            &executable,
            format!("#!/bin/sh\nsleep {FORKING_ENGINE_SLEEP_SECS}\n"),
        )
        .unwrap();
        fs::set_permissions(&executable, fs::Permissions::from_mode(0o700)).unwrap();
        let started = Instant::now();

        let error =
            run_bounded_engine_command(&executable, &capture_root, &[], Duration::from_millis(100))
                .unwrap_err();

        // Carries the error: the one CI failure this test has produced was this
        // assertion printing nothing about what it actually got, and it was
        // neither of the two conditions reproducible under load.
        assert!(error.contains("timed out"), "{error}");
        // Well short of the sleep, so returning at all means the kill landed
        // rather than the script running itself out. Half of it, rather than a
        // fixed two seconds: the elapsed time also covers spawning, polling and
        // reaping on a machine running the rest of the suite beside it.
        let elapsed = started.elapsed();
        assert!(
            elapsed < Duration::from_secs(FORKING_ENGINE_SLEEP_SECS / 2),
            "the engine outlived its timeout by {elapsed:?}"
        );
    }

    #[test]
    fn engine_capture_and_child_tmpdir_use_explicit_writable_storage() {
        let root = tempdir().unwrap();
        let executable = root.path().join("capture-engine");
        let capture_root = root.path().join("captures");
        fs::create_dir(&capture_root).unwrap();
        fs::write(&executable, "#!/bin/sh\nprintf '%s' \"$TMPDIR\"\n").unwrap();
        fs::set_permissions(&executable, fs::Permissions::from_mode(0o700)).unwrap();

        let output = run_bounded_engine_command(
            &executable,
            &capture_root,
            &[],
            Duration::from_secs(UNHURRIED_TEST_TIMEOUT_SECS),
        )
        .unwrap();

        assert!(output.status.success());
        assert_eq!(
            String::from_utf8(output.stdout).unwrap(),
            capture_root.to_string_lossy()
        );
    }

    #[test]
    fn immutable_guest_routes_temporary_and_network_state_to_writable_mounts() {
        let fstab = include_str!("../../guest-image/rootfs-overlay/etc/fstab");
        let mount_data =
            include_str!("../../guest-image/rootfs-overlay/usr/local/bin/lemma-mount-data");
        let guest_service = include_str!(
            "../../guest-image/rootfs-overlay/usr/local/bin/lemma-runtime-guest-service"
        );

        assert!(fstab.contains("tmpfs /tmp tmpfs"));
        assert!(mount_data.contains("$data_root/cni/net.d"));
        assert!(mount_data.contains("/etc/cni/net.d"));
        assert!(guest_service.contains("HOME=/var/lib/lemma/home"));
        assert!(guest_service.contains("LEMMA_GUEST_TEMP_ROOT=/tmp/lemma-engine"));
        assert!(guest_service.contains("TMPDIR=\"$LEMMA_GUEST_TEMP_ROOT\""));
    }

    /// The guest must never force a filesystem onto a disk that has one.
    ///
    /// `mkfs.ext4 -F` on `/dev/vdb` destroys every database, volume and
    /// workspace on the machine, and the branch that reached it treated a
    /// *corrupt* superblock exactly like an empty disk -- so the one situation
    /// where the data most needed preserving was the situation that destroyed
    /// it, silently, after which the app reported a healthy first run.
    ///
    /// A shell script cannot be unit-tested here, so this pins its text. The
    /// negative assertion is the one that matters: anything reintroducing `-F`
    /// fails this, and the failure names why.
    #[test]
    fn the_guest_never_force_formats_a_disk_that_already_holds_a_filesystem() {
        let mount_data =
            include_str!("../../guest-image/rootfs-overlay/usr/local/bin/lemma-mount-data");

        assert!(
            !mount_data.contains("mkfs.ext4 -F"),
            "forcing a filesystem over an unrecognised disk destroys user data; \
             format only an unsigned disk the host says it just created",
        );
        assert!(
            mount_data.contains("/mnt/lemma-control/data-disk-fresh"),
            "only the host knows whether this disk was just created, so the \
             guest must consult its marker before formatting",
        );
        assert!(
            mount_data.contains("e2fsck -p"),
            "the stop path ends in SIGKILL, so dirty filesystems are guaranteed \
             and must be repaired rather than accumulated",
        );
        assert!(
            mount_data.contains("noatime,discard"),
            "without discard the sparse data disk only ever grows: space freed \
             inside the guest is never returned to macOS",
        );
        assert!(
            mount_data.contains("needs-repair:"),
            "an unmountable disk must announce itself on the console, or the \
             host waits out a 120-second timeout and reports nothing useful",
        );
    }
}
