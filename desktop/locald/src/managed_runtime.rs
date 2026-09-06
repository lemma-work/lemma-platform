use std::collections::HashMap;
use std::env;
use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::net::{IpAddr, Ipv4Addr, Shutdown, SocketAddr, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use lemma_runtime_manager::{
    ManagedRuntime, ManagedRuntimeConfig, ManagedRuntimeStatus, DEFAULT_WSL_DISTRIBUTION,
};
use serde::{Deserialize, Serialize};
use serde_json::json;

use crate::host_process::ManagedRuntimeSpec;
use crate::native_host_pack::ManagedManifestMaterial;
use crate::paths::LocalPaths;

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct InfraSecrets {
    postgres_password: String,
    redis_password: String,
}

#[derive(Clone, Debug)]
pub struct ManagedRuntimeBootstrap {
    artifact_root: PathBuf,
    bridge_executable: PathBuf,
    #[cfg(target_os = "macos")]
    vz_executable: PathBuf,
    #[cfg(windows)]
    wsl_executable: PathBuf,
    secrets: InfraSecrets,
}

impl ManagedRuntimeBootstrap {
    pub fn discover(paths: &LocalPaths, healed: &mut Vec<String>) -> io::Result<Option<Self>> {
        let Some(artifact_root) = env::var_os("LEMMA_LOCALD_MANAGED_RUNTIME_ARTIFACT_ROOT")
            .filter(|value| !value.is_empty())
            .map(PathBuf::from)
        else {
            return Ok(None);
        };
        if !artifact_root.is_dir() {
            return Err(io::Error::new(
                io::ErrorKind::NotFound,
                format!(
                    "managed runtime artifact root is missing: {}",
                    artifact_root.display()
                ),
            ));
        }

        #[cfg(not(any(target_os = "macos", windows)))]
        return Err(io::Error::new(
            io::ErrorKind::Unsupported,
            "managed local runtime is supported on macOS and Windows",
        ));

        #[cfg(any(target_os = "macos", windows))]
        {
            let bridge_executable = bundled_executable(
                "LEMMA_LOCALD_RUNTIME_BRIDGE_BIN",
                if cfg!(windows) {
                    "lemma-runtime.exe"
                } else {
                    "lemma-runtime"
                },
            )?;
            #[cfg(target_os = "macos")]
            let vz_executable = bundled_executable("LEMMA_LOCALD_VZ_BIN", "lemma-vz")?;
            #[cfg(windows)]
            let wsl_executable = env::var_os("LEMMA_LOCALD_WSL_BIN")
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from("wsl.exe"));

            Ok(Some(Self {
                artifact_root,
                bridge_executable,
                #[cfg(target_os = "macos")]
                vz_executable,
                #[cfg(windows)]
                wsl_executable,
                secrets: load_or_create_secrets(&paths.root.join("infra.secrets.json"), healed)?,
            }))
        }
    }

    pub(crate) fn manifest_material(&self) -> ManagedManifestMaterial {
        ManagedManifestMaterial {
            postgres_password: self.secrets.postgres_password.clone(),
            redis_password: self.secrets.redis_password.clone(),
            bridge_executable: self.bridge_executable.clone(),
        }
    }

    pub fn controller(
        &self,
        paths: &LocalPaths,
        spec: ManagedRuntimeSpec,
    ) -> io::Result<Arc<ManagedRuntimeController>> {
        if spec.credentials.postgres_password != self.secrets.postgres_password
            || spec.credentials.redis_password != self.secrets.redis_password
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "host manifest credentials do not match the private local installation",
            ));
        }
        let runtime = ManagedRuntime::new(ManagedRuntimeConfig {
            wsl_distribution: wsl_distribution_for(&paths.root),
            local_root: paths.root.clone(),
            artifact_root: self.artifact_root.clone(),
            bridge_executable: self.bridge_executable.clone(),
            #[cfg(target_os = "macos")]
            vz_executable: self.vz_executable.clone(),
            #[cfg(windows)]
            wsl_executable: self.wsl_executable.clone(),
        })?;
        Ok(Arc::new(ManagedRuntimeController {
            runtime,
            spec,
            forwarders: Mutex::new(Vec::new()),
            status: Mutex::new(None),
            clock_keeper: Mutex::new(None),
            last_clock_error: Mutex::new(None),
            sandbox_images: Mutex::new(SandboxImageStatus::default()),
            pending_auth: Mutex::new(None),
        }))
    }
}

/// How often the guest's wall clock is put back on this machine's.
///
/// The guest sets its time once, at boot, and nothing moves it afterwards. A
/// Virtualization.framework VM does not run while the Mac sleeps, so the guest
/// clock falls behind by however long the lid was closed and stays there. That
/// broke sign-in outright: the auth service runs inside the guest, so every
/// access token it minted carried an `exp` computed from the wrong clock, the
/// backend on the Mac read it as already expired, and the browser refreshed --
/// getting another already-expired token from the same wrong clock, forever.
///
/// Thirty seconds is a bound on drift, not a poll for it: the request is one
/// small round trip over the control socket, and the sleep case is caught
/// within a tick anyway.
const CLOCK_SYNC_INTERVAL: Duration = Duration::from_secs(30);
/// The slice the keeper sleeps in, so stopping does not wait out an interval.
const CLOCK_KEEPER_TICK: Duration = Duration::from_secs(1);
/// Wall time that ran further than the monotonic clock across one tick means
/// the Mac was asleep in between -- `Instant` does not advance while it is.
/// The guest was not running for that stretch, so it is now exactly that far
/// behind and should not wait for the interval to find out.
const HOST_SLEEP_MARGIN: Duration = Duration::from_secs(5);

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum ClockSyncReason {
    HostSlept,
    Interval,
}

/// Should this tick put the guest clock back on the host's, and why?
///
/// Split out from the loop because the loop is a thread and this is the part
/// worth asserting on.
fn clock_sync_due(
    since_last_sync: Duration,
    monotonic: Duration,
    wall: Duration,
) -> Option<ClockSyncReason> {
    if wall > monotonic + HOST_SLEEP_MARGIN {
        return Some(ClockSyncReason::HostSlept);
    }
    if since_last_sync >= CLOCK_SYNC_INTERVAL {
        return Some(ClockSyncReason::Interval);
    }
    None
}

/// Where the sandbox image warm-up has got to.
///
/// Reported rather than waited on. The image a pod runs its work in is several
/// hundred megabytes and is not needed until something actually runs, so
/// fetching it used to sit in the middle of the startup bar and hold "Lemma is
/// ready" behind a download nobody had asked for yet. It now runs behind the
/// workspace, and this is what the app shows about it.
/// Nothing has been said yet, and the workspace should keep asking.
pub const SANDBOX_IMAGES_PENDING: &str = "pending";
pub const SANDBOX_IMAGES_DOWNLOADING: &str = "downloading";
pub const SANDBOX_IMAGES_READY: &str = "ready";
pub const SANDBOX_IMAGES_FAILED: &str = "failed";
/// This deployment does not manage sandbox images at all -- there is no guest
/// to warm. Terminal, so the workspace stops asking rather than polling a
/// question nothing will ever answer.
pub const SANDBOX_IMAGES_UNSUPPORTED: &str = "unsupported";

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SandboxImageStatus {
    /// One of the `SANDBOX_IMAGES_*` constants above.
    pub state: String,
    pub detail: String,
}

impl SandboxImageStatus {
    fn new(state: &str, detail: &str) -> Self {
        Self {
            state: state.to_owned(),
            detail: detail.to_owned(),
        }
    }
}

impl Default for SandboxImageStatus {
    fn default() -> Self {
        Self::new(SANDBOX_IMAGES_PENDING, "")
    }
}

struct ClockKeeper {
    stop: Arc<AtomicBool>,
    handle: JoinHandle<()>,
}

pub struct ManagedRuntimeController {
    runtime: ManagedRuntime,
    spec: ManagedRuntimeSpec,
    forwarders: Mutex<Vec<TcpForwarder>>,
    status: Mutex<Option<ManagedRuntimeStatus>>,
    clock_keeper: Mutex<Option<ClockKeeper>>,
    /// The last clock-sync failure written to the log, so a standing one is
    /// said once rather than twice a minute for as long as the stack runs.
    last_clock_error: Mutex<Option<String>>,
    sandbox_images: Mutex<SandboxImageStatus>,
    /// The auth service, still coming up while the backend boots.
    ///
    /// See `start_with_progress`. Joined by `await_private_services` before
    /// anything reports ready, so this is a reordering and not a weakening.
    pending_auth: Mutex<Option<thread::JoinHandle<io::Result<()>>>>,
}

impl ManagedRuntimeController {
    pub fn prepare_host(&self) -> io::Result<serde_json::Value> {
        self.runtime.prepare_host()
    }

    pub fn start(self: &Arc<Self>) -> io::Result<()> {
        self.start_with_progress(|_, _, _, _| {})
    }

    pub fn start_with_progress(
        self: &Arc<Self>,
        mut progress: impl FnMut(&str, &str, u64, &str),
    ) -> io::Result<()> {
        validate_spec(&self.spec)?;
        progress(
            "vm",
            "Starting private runtime",
            32,
            "booting the app-owned Linux appliance",
        );
        let status = self.runtime.start().inspect_err(|_error| {
            let _ = self.runtime.capture_diagnostics();
        })?;
        // Before PostgreSQL, Redis or the auth service exist in there. `start`
        // only boots a guest that is not already running, and a reused guest
        // keeps whatever clock it drifted to while the Mac was asleep -- so the
        // one place the clock is guaranteed correct cannot be boot alone.
        self.sync_guest_clock(None);
        let parameters = json!({
            "images": self.spec.images,
            "credentials": self.spec.credentials,
        });
        // Postgres and Redis first, and waited for: migrations run against the
        // database before the backend starts, and the backend reaches for both
        // as it boots.
        for (operation, component, label, percentage, detail) in [
            (
                "core.images",
                "infrastructure-images",
                "Preparing infrastructure images",
                40,
                "downloading missing PostgreSQL, Redis, and auth layers",
            ),
            (
                "core.postgres",
                "postgres",
                "Starting PostgreSQL",
                50,
                "preparing Lemma, datastore, the sandbox runtime, and auth databases",
            ),
            (
                "core.redis",
                "redis",
                "Starting Redis",
                58,
                "preparing local streams, cache, and pub/sub",
            ),
        ] {
            progress(component, label, percentage, detail);
            if let Err(error) = self.runtime.request(operation, parameters.clone()) {
                let _ = self.runtime.capture_diagnostics();
                let _ = self.runtime.stop();
                return Err(error);
            }
        }

        // The auth service starts here and is *waited for* later, because the
        // backend does not need it to boot.
        //
        // `core.supertokens` does not return when the container starts; it
        // returns when the service answers, and getting a JVM to answer took
        // 5.13s of a 19.9s cold start on the machine this was measured on. That
        // wait sat on the critical path in front of a backend that spends its
        // own ~4s importing and binding, and `initialize_supertokens` only
        // writes local configuration -- the first call to the auth service
        // happens on the first authenticated request, long after.
        //
        // On a thread rather than by reordering the request, because the guest
        // control channel is single: the host bridge holds one vsock connection
        // behind a process-wide mutex and guestd handles connections inline on
        // its accept loop, so this request occupies that channel either way.
        // What it must not also occupy is *this* thread, which is what the
        // daemon needs back in order to start the backend at all.
        progress(
            "supertokens",
            "Starting local authentication",
            64,
            "preparing the private auth service",
        );
        let auth = {
            let controller = Arc::clone(self);
            let parameters = parameters.clone();
            thread::Builder::new()
                .name("lemma-locald-supertokens".into())
                .spawn(move || {
                    controller
                        .runtime
                        .request("core.supertokens", parameters)
                        .map(|_| ())
                })?
        };
        *self
            .pending_auth
            .lock()
            .expect("pending auth lock poisoned") = Some(auth);
        if let Err(error) = self.ensure_forwarders(&status) {
            let _ = self.runtime.capture_diagnostics();
            let _ = self.runtime.stop();
            return Err(error);
        }
        *self.status.lock().expect("managed runtime status poisoned") = Some(status);
        self.start_clock_keeper();
        Ok(())
    }

    /// Wait for everything `start_with_progress` left in flight.
    ///
    /// The auth service is started there and joined here, so it comes up beside
    /// the backend instead of in front of it. Nothing may report ready before
    /// this returns: a workspace whose first action is signing in would meet an
    /// auth service that is not answering yet, which is a worse failure than
    /// the wait this removes.
    ///
    /// Called after the host processes are up rather than before, which is the
    /// whole point -- and it is also why a failure here has to stop them. The
    /// caller owns that, because it owns the processes.
    pub fn await_private_services(&self) -> io::Result<()> {
        let pending = self
            .pending_auth
            .lock()
            .expect("pending auth lock poisoned")
            .take();
        if let Some(handle) = pending {
            match handle.join() {
                Ok(Ok(())) => {}
                Ok(Err(error)) => {
                    let _ = self.runtime.capture_diagnostics();
                    let _ = self.runtime.stop();
                    return Err(error);
                }
                // A panicked worker is not a runtime the caller should keep
                // using, and joining loses the payload, so say which thread.
                Err(_) => {
                    let _ = self.runtime.capture_diagnostics();
                    let _ = self.runtime.stop();
                    return Err(io::Error::other(
                        "the private auth service failed to start (worker panicked)",
                    ));
                }
            }
        }
        let status = self
            .status
            .lock()
            .expect("managed runtime status poisoned")
            .clone();
        let Some(status) = status else {
            return Ok(());
        };
        // The same check as before, in the same place in the sequence relative
        // to anything that uses these services -- only now the services had the
        // backend's boot to finish coming up in, so it usually finds them ready.
        if let Err(error) = wait_for_private_services(&status, Duration::from_secs(90)) {
            let _ = self.runtime.capture_diagnostics();
            let _ = self.runtime.stop();
            return Err(error);
        }
        Ok(())
    }

    /// Ask the guest to destroy every database, volume and workspace it holds.
    ///
    /// The surgical half of a local-data reset: the guest tidies itself, so the
    /// pulled container images survive and coming back up is seconds rather
    /// than a re-download. Only reached when `probe` has already answered, so a
    /// guest that cannot be asked falls to `discard_data_disk` instead of
    /// retrying this.
    pub fn reset_guest_data(&self) -> io::Result<serde_json::Value> {
        self.runtime
            .request("core.reset_data", json!({"confirm": "reset-local-data"}))
    }

    /// Throw the whole data disk away, returning the bytes reclaimed.
    ///
    /// The blunt half, for a guest that will not answer -- a torn filesystem, a
    /// VM that will not boot. Takes the container images with it.
    #[cfg(target_os = "macos")]
    pub fn discard_data_disk(&self) -> io::Result<u64> {
        self.stop_clock_keeper();
        self.clear_forwarders();
        self.runtime.discard_data_disk()
    }

    pub fn stop_infrastructure(&self) -> io::Result<()> {
        self.stop_clock_keeper();
        if self.status().is_none() {
            self.clear_forwarders();
            return self.runtime.stop();
        }
        let core_result = self.runtime.request("core.stop", json!({})).map(|_| ());
        self.clear_forwarders();
        let runtime_result = self.runtime.stop();
        core_result.and(runtime_result)
    }

    pub fn shutdown(&self) -> io::Result<()> {
        // Before the VM goes, like `stop_infrastructure`. The keeper holds an
        // `Arc` to this controller, so leaving it running outlives the guest it
        // is correcting and ticks once a second at a control socket with
        // nothing behind it. Today the process exits immediately afterwards and
        // nobody notices; the first caller to use this for a soft stop would
        // inherit a thread that never ends.
        self.stop_clock_keeper();
        self.clear_forwarders();
        self.runtime.stop()
    }

    pub fn status(&self) -> Option<ManagedRuntimeStatus> {
        self.status
            .lock()
            .expect("managed runtime status poisoned")
            .clone()
    }

    pub fn probe(&self) -> io::Result<ManagedRuntimeStatus> {
        match self.runtime.health() {
            Ok(status) => {
                *self.status.lock().expect("managed runtime status poisoned") =
                    Some(status.clone());
                Ok(status)
            }
            Err(error) => {
                self.clear_forwarders();
                Err(error)
            }
        }
    }

    pub fn backend_environment(&self) -> io::Result<HashMap<String, String>> {
        let status = self.status().ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::NotConnected,
                "private runtime is not ready for host processes",
            )
        })?;
        let host = private_ipv4(&status.endpoint_host, "guest endpoint")?;
        let capability_file = runtime_path_value(self.runtime.capability_file())?;
        let control_socket = runtime_path_value(self.runtime.control_socket())?;
        Ok(HashMap::from([
            (
                "DATABASE_URL".into(),
                format!(
                    "postgresql+asyncpg://postgres:{}@{host}:5432/lemma",
                    self.spec.credentials.postgres_password
                ),
            ),
            (
                "DATASTORE_DATABASE_URL".into(),
                format!(
                    "postgresql+asyncpg://postgres:{}@{host}:5432/lemma_datastore",
                    self.spec.credentials.postgres_password
                ),
            ),
            (
                "REDIS_URL".into(),
                format!(
                    "redis://:{}@{host}:6379",
                    self.spec.credentials.redis_password
                ),
            ),
            ("SUPERTOKENS_CORE_URL".into(), format!("http://{host}:3567")),
            // The backend invokes the narrow runtime bridge for the sandbox runtime
            // lifecycle operations. Pass explicit paths to the app-owned
            // capability and transport; the bridge must never guess from a
            // developer checkout or rewrite a localhost URL.
            ("LEMMA_GUEST_CAPABILITY_FILE".into(), capability_file),
            ("LEMMA_GUEST_CONTROL_SOCKET".into(), control_socket),
            ("LEMMA_WSL_DISTRIBUTION".into(), "LemmaRuntime".into()),
        ]))
    }

    /// What the app should currently say about the sandbox image.
    pub fn sandbox_image_status(&self) -> SandboxImageStatus {
        self.sandbox_images
            .lock()
            .expect("sandbox image status poisoned")
            .clone()
    }

    /// Fetch the images pods run their work in, behind the workspace.
    ///
    /// Deliberately not part of starting. Nothing needs these images until a
    /// pod runs something, and they are several hundred megabytes -- so doing
    /// it inline held "Lemma is ready" behind a download the user had not asked
    /// for yet, on a first run, on whatever connection they happened to have.
    ///
    /// Equally deliberately not fatal. Someone installing on a plane gets a
    /// working local Lemma; `sandbox.ensure` still pulls what it needs on first
    /// use, exactly as it did before any of this existed. `report` is how the
    /// app is told, and is called for every state this passes through so a
    /// caller can show it and then take it away again.
    pub fn warm_sandbox_images(
        self: &Arc<Self>,
        report: impl Fn(&SandboxImageStatus) + Send + 'static,
    ) {
        let Some(started) = self.claim_sandbox_image_warmup() else {
            return;
        };
        report(&started);

        let controller = Arc::clone(self);
        thread::spawn(move || {
            let parameters = json!({
                "images": controller.spec.images,
                "credentials": controller.spec.credentials,
            });
            let status = match controller
                .runtime
                .request("core.sandbox_images", parameters)
            {
                Ok(_) => {
                    SandboxImageStatus::new(SANDBOX_IMAGES_READY, "The workspace sandbox is ready")
                }
                Err(error) => {
                    eprintln!("locald: sandbox images could not be warmed up: {error}");
                    SandboxImageStatus::new(
                        SANDBOX_IMAGES_FAILED,
                        "Lemma is ready; the first task in a pod will fetch it",
                    )
                }
            };
            controller.publish_sandbox_images(status, &report);
        });
    }

    /// Take the warm-up, or decline because one is already running.
    ///
    /// A compare-and-set under the status lock. Both the ready path and the
    /// recovery path call `warm_sandbox_images`, and two runs would interleave
    /// their downloading/ready events into one stream the app reads as a single
    /// download finishing twice.
    fn claim_sandbox_image_warmup(&self) -> Option<SandboxImageStatus> {
        let mut current = self
            .sandbox_images
            .lock()
            .expect("sandbox image status poisoned");
        if current.state == SANDBOX_IMAGES_DOWNLOADING {
            return None;
        }
        let started = SandboxImageStatus::new(
            SANDBOX_IMAGES_DOWNLOADING,
            "Downloading the image pods run their work in",
        );
        *current = started.clone();
        Some(started)
    }

    fn publish_sandbox_images(
        &self,
        status: SandboxImageStatus,
        report: &impl Fn(&SandboxImageStatus),
    ) {
        *self
            .sandbox_images
            .lock()
            .expect("sandbox image status poisoned") = status.clone();
        report(&status);
    }

    /// Hold the guest clock on this machine's for as long as the stack runs.
    ///
    /// Idempotent: a second call while one is running is a no-op, so a recovery
    /// path that starts an already-started stack does not leave two threads
    /// stepping the same clock.
    fn start_clock_keeper(self: &Arc<Self>) {
        let mut slot = self
            .clock_keeper
            .lock()
            .expect("clock keeper lock poisoned");
        if slot.is_some() {
            return;
        }
        let stop = Arc::new(AtomicBool::new(false));
        let controller = Arc::clone(self);
        let flag = Arc::clone(&stop);
        let handle = thread::spawn(move || controller.keep_clock(&flag));
        *slot = Some(ClockKeeper { stop, handle });
    }

    fn stop_clock_keeper(&self) {
        let keeper = self
            .clock_keeper
            .lock()
            .expect("clock keeper lock poisoned")
            .take();
        if let Some(keeper) = keeper {
            keeper.stop.store(true, Ordering::Release);
            let _ = keeper.handle.join();
        }
    }

    fn keep_clock(&self, stop: &AtomicBool) {
        let mut last_sync = Instant::now();
        let mut last_tick = Instant::now();
        let mut last_wall = SystemTime::now();
        while !stop.load(Ordering::Acquire) {
            thread::sleep(CLOCK_KEEPER_TICK);
            if stop.load(Ordering::Acquire) {
                return;
            }
            let tick = Instant::now();
            let wall = SystemTime::now();
            let reason = clock_sync_due(
                tick.duration_since(last_sync),
                tick.duration_since(last_tick),
                wall.duration_since(last_wall).unwrap_or_default(),
            );
            last_tick = tick;
            last_wall = wall;
            let Some(reason) = reason else {
                continue;
            };
            last_sync = tick;
            self.sync_guest_clock(Some(reason));
        }
    }

    /// One correction, reported only when there was something to correct.
    ///
    /// Never fatal. A guest that will not take a clock is a guest with a
    /// problem this cannot fix, and tearing the stack down over it would turn a
    /// recoverable drift into an outage.
    fn sync_guest_clock(&self, reason: Option<ClockSyncReason>) {
        match self.runtime.sync_clock() {
            Ok(report) => {
                if !report
                    .get("stepped")
                    .and_then(serde_json::Value::as_bool)
                    .unwrap_or(false)
                {
                    return;
                }
                let skew = report
                    .get("skew_seconds")
                    .and_then(serde_json::Value::as_i64)
                    .unwrap_or_default();
                let cause = match reason {
                    Some(ClockSyncReason::HostSlept) => " after this Mac slept",
                    Some(ClockSyncReason::Interval) | None => "",
                };
                eprintln!("locald: the guest clock was {skew}s behind this Mac{cause}; corrected");
                self.last_clock_error
                    .lock()
                    .expect("clock error lock poisoned")
                    .take();
            }
            Err(error) => {
                // Once per distinct failure. A guest too old to know
                // `system.clock` refuses every attempt, and at this cadence
                // saying so each time is a line twice a minute for as long as
                // the stack runs -- which is the shape of log flood this
                // codebase has already paid for once.
                let message = error.to_string();
                let mut last = self
                    .last_clock_error
                    .lock()
                    .expect("clock error lock poisoned");
                if last.as_deref() == Some(message.as_str()) {
                    return;
                }
                eprintln!("locald: could not put the guest clock back on this Mac's: {message}");
                *last = Some(message);
            }
        }
    }

    fn ensure_forwarders(&self, status: &ManagedRuntimeStatus) -> io::Result<()> {
        let host_gateway = private_ipv4(&status.host_gateway, "guest host gateway")?;
        let mut current = self.forwarders.lock().expect("forwarder lock poisoned");
        if !current.is_empty() {
            return Ok(());
        }

        // Host applications use the guest's private NAT address directly.
        // Only sandbox callbacks need guest-to-host bridges; no database,
        // cache, or auth service is published on a host loopback port.
        let bindings = [
            (
                "sandbox-api-callback",
                SocketAddr::from((host_gateway, self.spec.ports.backend)),
                SocketAddr::from((Ipv4Addr::LOCALHOST, self.spec.ports.backend)),
            ),
            (
                "sandbox-frontend-callback",
                SocketAddr::from((host_gateway, self.spec.ports.frontend)),
                SocketAddr::from((Ipv4Addr::LOCALHOST, self.spec.ports.frontend)),
            ),
        ];
        for (label, bind, target) in bindings {
            match TcpForwarder::start(label, bind, target) {
                Ok(forwarder) => current.push(forwarder),
                Err(error) => {
                    current.clear();
                    return Err(error);
                }
            }
        }
        Ok(())
    }

    fn clear_forwarders(&self) {
        self.forwarders
            .lock()
            .expect("forwarder lock poisoned")
            .clear();
        *self.status.lock().expect("managed runtime status poisoned") = None;
    }
}

const PRIVATE_SERVICE_PORTS: [(&str, u16); 3] =
    [("PostgreSQL", 5432), ("Redis", 6379), ("SuperTokens", 3567)];

/// Which private WSL distribution this installation owns.
///
/// The default installation keeps the historic name, so an upgrade finds the
/// guest it already imported rather than orphaning a multi-gigabyte disk full
/// of the user's workspaces. Every other root -- a second Windows profile, a
/// dev root, a relocated install -- gets its own, because the distribution
/// holds that installation's databases and workspaces and two installations
/// sharing one guest means each overwrites the other's capability file, either
/// one's stop kills the other's runtime, and the second silently runs against
/// the first's data.
#[cfg(windows)]
pub(crate) fn wsl_distribution_for(root: &Path) -> String {
    if let Some(name) = env::var_os("LEMMA_RUNTIME_WSL_DISTRIBUTION")
        .map(|value| value.to_string_lossy().into_owned())
        .filter(|value| !value.trim().is_empty())
    {
        return name;
    }
    let is_default = crate::paths::LocalPaths::default_root().is_ok_and(|default| {
        crate::paths::stable_hash(&default) == crate::paths::stable_hash(root)
    });
    if is_default {
        DEFAULT_WSL_DISTRIBUTION.to_string()
    } else {
        format!(
            "{DEFAULT_WSL_DISTRIBUTION}-{:016x}",
            crate::paths::stable_hash(root)
        )
    }
}

#[cfg(not(windows))]
fn wsl_distribution_for(_root: &Path) -> String {
    DEFAULT_WSL_DISTRIBUTION.to_string()
}

fn wait_for_private_services(status: &ManagedRuntimeStatus, timeout: Duration) -> io::Result<()> {
    let host = private_ipv4(&status.endpoint_host, "guest endpoint")?;
    wait_for_tcp_services(host, &PRIVATE_SERVICE_PORTS, timeout)
}

fn wait_for_tcp_services(
    host: Ipv4Addr,
    services: &[(&str, u16)],
    timeout: Duration,
) -> io::Result<()> {
    let deadline = Instant::now() + timeout;
    let mut pending = services.to_vec();
    let mut last_error = None;
    while !pending.is_empty() {
        pending.retain(|(_, port)| {
            let address = SocketAddr::from((host, *port));
            match TcpStream::connect_timeout(&address, Duration::from_millis(500)) {
                Ok(_) => false,
                Err(error) => {
                    last_error = Some(error);
                    true
                }
            }
        });
        if pending.is_empty() {
            return Ok(());
        }
        if Instant::now() >= deadline {
            let pending = pending
                .iter()
                .map(|(label, port)| format!("{label} ({host}:{port})"))
                .collect::<Vec<_>>()
                .join(", ");
            return Err(io::Error::new(
                io::ErrorKind::TimedOut,
                format!(
                    "private runtime services did not become reachable within {} seconds: {pending}; last error: {}",
                    timeout.as_secs(),
                    last_error
                        .map(|error| error.to_string())
                        .unwrap_or_else(|| "connection timed out".into())
                ),
            ));
        }
        thread::sleep(Duration::from_millis(200));
    }
    Ok(())
}

fn runtime_path_value(path: &Path) -> io::Result<String> {
    path.to_str().map(str::to_owned).ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            format!("managed runtime path is not Unicode: {}", path.display()),
        )
    })
}

struct TcpForwarder {
    stop: Arc<AtomicBool>,
    local_address: SocketAddr,
    thread: Option<JoinHandle<()>>,
}

impl TcpForwarder {
    fn start(label: &'static str, bind: SocketAddr, target: SocketAddr) -> io::Result<Self> {
        let listener = bind_forwarder_listener(bind).map_err(|error| {
            io::Error::new(
                error.kind(),
                format!("could not bind managed {label} route at {bind}: {error}"),
            )
        })?;
        let local_address = listener.local_addr()?;
        listener.set_nonblocking(true)?;
        let stop = Arc::new(AtomicBool::new(false));
        let thread_stop = Arc::clone(&stop);
        let worker = thread::spawn(move || {
            while !thread_stop.load(Ordering::Acquire) {
                match listener.accept() {
                    Ok((stream, _)) => {
                        if let Err(error) = stream.set_nonblocking(false) {
                            eprintln!(
                                "managed {label} route could not configure accepted socket: {error}"
                            );
                            continue;
                        }
                        thread::spawn(move || {
                            if let Err(error) = proxy_connection(stream, target) {
                                eprintln!("managed {label} route to {target} failed: {error}");
                            }
                        });
                    }
                    Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(25));
                    }
                    Err(_) => break,
                }
            }
        });
        Ok(Self {
            stop,
            local_address,
            thread: Some(worker),
        })
    }
}

#[cfg(unix)]
fn bind_forwarder_listener(address: SocketAddr) -> io::Result<TcpListener> {
    use socket2::{Domain, Protocol, Socket, Type};

    let socket = Socket::new(
        Domain::for_address(address),
        Type::STREAM,
        Some(Protocol::TCP),
    )?;
    // The forwarder is always bound to Lemma's private guest-to-host gateway.
    // Reuse permits an immediate controlled restart after a real callback
    // connection leaves TCP state behind; it does not relax locald's separate
    // ownership checks for host loopback application ports.
    socket.set_reuse_address(true)?;
    socket.bind(&address.into())?;
    socket.listen(128)?;
    Ok(socket.into())
}

#[cfg(not(unix))]
fn bind_forwarder_listener(address: SocketAddr) -> io::Result<TcpListener> {
    TcpListener::bind(address)
}

impl Drop for TcpForwarder {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
        // The listener is nonblocking and observes `stop` within 25 ms. Do not
        // wake it with a synthetic TCP connection: that connection can enter
        // TIME_WAIT and prevent an immediate restart from reclaiming the exact
        // callback port on macOS.
        if let Some(worker) = self.thread.take() {
            if worker.join().is_err() {
                eprintln!(
                    "managed callback forwarder at {} stopped unexpectedly",
                    self.local_address
                );
            }
        }
    }
}

fn proxy_connection(mut inbound: TcpStream, target: SocketAddr) -> io::Result<()> {
    let mut outbound = TcpStream::connect_timeout(&target, Duration::from_secs(5))?;
    let mut inbound_writer = inbound.try_clone()?;
    let mut outbound_reader = outbound.try_clone()?;
    let upload = thread::spawn(move || {
        let result = io::copy(&mut inbound, &mut outbound);
        let _ = outbound.shutdown(Shutdown::Write);
        result
    });
    let download = io::copy(&mut outbound_reader, &mut inbound_writer);
    let _ = inbound_writer.shutdown(Shutdown::Write);
    let upload = upload
        .join()
        .map_err(|_| io::Error::other("managed TCP route worker panicked"))?;
    upload.and(download).map(|_| ())
}

fn validate_spec(spec: &ManagedRuntimeSpec) -> io::Result<()> {
    for (name, image) in [
        ("postgres", &spec.images.postgres),
        ("redis", &spec.images.redis),
        ("supertokens", &spec.images.supertokens),
    ] {
        if !image.contains("@sha256:") || image.bytes().any(|byte| byte.is_ascii_whitespace()) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                format!("managed {name} image must be digest-pinned"),
            ));
        }
    }
    validate_secret("postgres_password", &spec.credentials.postgres_password)?;
    validate_secret("redis_password", &spec.credentials.redis_password)?;
    let ports = [
        spec.ports.postgres,
        spec.ports.redis,
        spec.ports.supertokens,
        spec.ports.backend,
        spec.ports.frontend,
    ];
    if ports.contains(&0) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "managed runtime ports must be non-zero",
        ));
    }
    Ok(())
}

fn private_ipv4(value: &str, label: &str) -> io::Result<Ipv4Addr> {
    let address = value.parse::<IpAddr>().map_err(|_| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            format!("{label} must be a literal IPv4 address"),
        )
    })?;
    match address {
        IpAddr::V4(address)
            if !address.is_unspecified()
                && !address.is_loopback()
                && !address.is_multicast()
                && (address.is_private() || address.is_link_local()) =>
        {
            Ok(address)
        }
        _ => Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            format!("{label} must be a private non-loopback IPv4 address"),
        )),
    }
}

/// Read the infrastructure passwords, replacing them only if unreadable.
///
/// An unreadable file used to end the daemon permanently. Healing it needs one
/// extra step that ordinary self-healing does not: `postgres_password` was
/// baked into the `lemma-postgres-data` volume at `initdb`, so a new password
/// does not open the existing database. `ensure_core_container` only replaces a
/// container when its image or config generation changes, and `ensure_database`
/// connects over the local socket with no password at all -- so the mismatch
/// survives the entire guest start and first surfaces deep in the backend's
/// migrations as an opaque auth error.
///
/// So the replacement is recorded as "this installation's data can no longer be
/// read", which `start_host_packs` refuses on, with a reset the user can press.
fn load_or_create_secrets(path: &Path, healed: &mut Vec<String>) -> io::Result<InfraSecrets> {
    if path.is_file() {
        match read_existing_secrets(path) {
            Ok(secrets) => return Ok(secrets),
            Err(reason) => {
                let aside = crate::paths::quarantine_aside(path)?;
                if let Some(root) = path.parent() {
                    crate::paths::require_data_reset(
                        root,
                        "the private infrastructure passwords were replaced, and the existing \
                         workspace database was created with the previous ones",
                    )?;
                }
                healed.push(format!(
                    "the infrastructure passwords were unreadable ({reason}); kept as {} and \
                     replaced. The existing local data cannot be opened with the new ones",
                    aside.display()
                ));
            }
        }
    }
    // Missing, rather than unreadable. Same consequence: the password baked
    // into the Postgres volume at `initdb` does not change because this file
    // was recreated, so the new one opens nothing. Only a genuine first run may
    // mint quietly, and a first run has no data.
    else if path
        .parent()
        .is_some_and(crate::paths::installation_has_data)
    {
        let root = path.parent().expect("checked just above");
        crate::paths::require_data_reset(
            root,
            "the private infrastructure passwords are missing, and the existing workspace \
             database was created with the previous ones",
        )?;
        healed.push(
            "the infrastructure passwords were missing while local data was still present; \
             new ones were created and the existing database cannot be opened with them"
                .to_owned(),
        );
    }
    let secrets = InfraSecrets {
        postgres_password: random_hex()?,
        redis_password: random_hex()?,
    };
    write_private_atomic(path, &serde_json::to_vec(&secrets)?)?;
    Ok(secrets)
}

fn read_existing_secrets(path: &Path) -> Result<InfraSecrets, String> {
    ensure_private_file(path).map_err(|error| error.to_string())?;
    let raw = fs::read(path).map_err(|error| error.to_string())?;
    let secrets: InfraSecrets =
        serde_json::from_slice(&raw).map_err(|error| format!("invalid JSON: {error}"))?;
    validate_secret("postgres_password", &secrets.postgres_password)
        .map_err(|error| error.to_string())?;
    validate_secret("redis_password", &secrets.redis_password)
        .map_err(|error| error.to_string())?;
    Ok(secrets)
}

fn random_hex() -> io::Result<String> {
    let mut bytes = [0_u8; 32];
    getrandom::fill(&mut bytes)
        .map_err(|error| io::Error::other(format!("secure randomness failed: {error}")))?;
    Ok(bytes.iter().map(|byte| format!("{byte:02x}")).collect())
}

fn validate_secret(name: &str, value: &str) -> io::Result<()> {
    if value.len() != 64
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            format!("managed {name} must be a 64-character lowercase hex secret"),
        ));
    }
    Ok(())
}

fn write_private_atomic(path: &Path, contents: &[u8]) -> io::Result<()> {
    let parent = path
        .parent()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "secret path has no parent"))?;
    fs::create_dir_all(parent)?;
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    let temporary = parent.join(format!(".infra-secrets-{}-{nonce}.tmp", std::process::id()));
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(&temporary)?;
    file.write_all(contents)?;
    file.sync_all()?;
    fs::rename(&temporary, path)?;
    ensure_private_file(path)
}

fn ensure_private_file(path: &Path) -> io::Result<()> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        let metadata = fs::symlink_metadata(path)?;
        if !metadata.file_type().is_file() || metadata.mode() & 0o077 != 0 {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                format!("managed secret file is not private: {}", path.display()),
            ));
        }
    }
    #[cfg(not(unix))]
    let _ = path;
    Ok(())
}

#[cfg(any(target_os = "macos", windows))]
fn bundled_executable(variable: &str, sibling_name: &str) -> io::Result<PathBuf> {
    let path = match env::var_os(variable).filter(|value| !value.is_empty()) {
        Some(path) => PathBuf::from(path),
        None => env::current_exe()?
            .parent()
            .ok_or_else(|| io::Error::other("locald executable has no parent"))?
            .join(sibling_name),
    };
    if !path.is_file() {
        return Err(io::Error::new(
            io::ErrorKind::NotFound,
            format!(
                "bundled managed runtime executable is missing: {}",
                path.display()
            ),
        ));
    }
    Ok(path)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Read;
    use std::sync::mpsc;
    use tempfile::tempdir;

    #[test]
    fn secrets_are_stable_private_and_not_accepted_when_tampered() {
        let root = tempdir().unwrap();
        let path = root.path().join("infra.secrets.json");
        let first = load_or_create_secrets(&path, &mut Vec::new()).unwrap();
        let second = load_or_create_secrets(&path, &mut Vec::new()).unwrap();

        assert_eq!(first.postgres_password, second.postgres_password);
        assert_eq!(first.redis_password, second.redis_password);
        assert_ne!(first.postgres_password, first.redis_password);
        #[cfg(unix)]
        {
            use std::os::unix::fs::MetadataExt;
            assert_eq!(fs::metadata(&path).unwrap().mode() & 0o777, 0o600);
        }
    }

    /// Replacing the infrastructure passwords is recorded as stranded data.
    ///
    /// This is the one self-heal that deliberately makes the failure *harder*.
    /// A new `postgres_password` does not open a volume that was `initdb`'d
    /// with the old one, and nothing downstream notices: `ensure_core_container`
    /// only replaces on an image or config-generation change, and
    /// `ensure_database` connects over the local socket with no password. So
    /// healing quietly would surface hours later as an opaque auth error deep
    /// in the backend's migrations. The marker is what turns that into a button.
    #[test]
    fn replacing_the_infrastructure_passwords_records_that_data_must_be_reset() {
        let root = tempdir().unwrap();
        let path = root.path().join("infra.secrets.json");
        let original = load_or_create_secrets(&path, &mut Vec::new()).unwrap();
        fs::write(&path, b"{\"postgres_password\": \"too-short\"").unwrap();

        let mut healed = Vec::new();
        let replaced = load_or_create_secrets(&path, &mut healed).unwrap();

        assert_ne!(replaced.postgres_password, original.postgres_password);
        assert_eq!(healed.len(), 1);
        assert!(healed[0].contains("cannot be opened"), "{}", healed[0]);
        let reason = crate::paths::data_reset_reason(root.path())
            .expect("a replaced password strands the existing database");
        assert!(reason.contains("previous ones"), "{reason}");
        // The unreadable original is kept, not destroyed.
        assert_eq!(
            fs::read_dir(root.path())
                .unwrap()
                .filter_map(Result::ok)
                .filter(|entry| entry.file_name().to_string_lossy().contains(".invalid-"))
                .count(),
            1
        );
    }

    #[test]
    fn tcp_forwarder_relays_bytes_and_releases_its_port() {
        let target = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        let target_address = target.local_addr().unwrap();
        // The test keeps `target` so it can still watch for relayed connections
        // after the forwarder is gone; the upstream half of the exchange runs on
        // a clone.
        let upstream = target.try_clone().unwrap();
        let (reached_target, relay_is_waiting) = mpsc::channel();
        let server = thread::spawn(move || {
            let (mut stream, _) = upstream.accept().unwrap();
            // macOS inherits O_NONBLOCK from the listener onto accepted sockets.
            // A relay that mistook the resulting EAGAIN for EOF would already
            // have hung up on this connection before the client said anything,
            // so require a read that runs out of time rather than one that
            // reports EOF. The duration only bounds how long a healthy relay is
            // watched; a broken one reports EOF immediately.
            stream
                .set_read_timeout(Some(Duration::from_millis(100)))
                .unwrap();
            let mut discard = [0_u8; 1];
            let idle = stream.read(&mut discard).map_err(|error| error.kind());
            assert!(
                matches!(
                    idle,
                    Err(io::ErrorKind::WouldBlock) | Err(io::ErrorKind::TimedOut)
                ),
                "relay stopped waiting for a client that had not spoken yet: {idle:?}"
            );
            stream.set_read_timeout(None).unwrap();
            reached_target.send(()).unwrap();

            let mut request = [0_u8; 4];
            stream.read_exact(&mut request).unwrap();
            assert_eq!(&request, b"ping");
            stream.write_all(b"pong").unwrap();

            // Hang up only after the client has, so the relay is the passive
            // closer on both of its sockets and leaves no TCP state of its own
            // behind on the port the release check below is about.
            let mut trailing = Vec::new();
            stream.read_to_end(&mut trailing).unwrap();
            assert!(trailing.is_empty());
        });

        let forwarder = TcpForwarder::start(
            "test",
            SocketAddr::from((Ipv4Addr::LOCALHOST, 0)),
            target_address,
        )
        .unwrap();
        let local = forwarder.local_address;
        let mut client = TcpStream::connect(local).unwrap();
        // Bounded only so a relay that never dials out fails this test instead
        // of parking the whole test binary on a blocking accept forever.
        relay_is_waiting
            .recv_timeout(Duration::from_secs(10))
            .expect("relay never opened its upstream connection");
        client.write_all(b"ping").unwrap();
        let mut response = [0_u8; 4];
        client.read_exact(&mut response).unwrap();
        assert_eq!(&response, b"pong");

        // Half-close, then read to EOF: the relay passing the upstream hang-up
        // back down is the observable proof that it finished both directions,
        // which is what makes the release check below race-free.
        client.shutdown(Shutdown::Write).unwrap();
        let mut trailing = Vec::new();
        client.read_to_end(&mut trailing).unwrap();
        assert!(trailing.is_empty());
        server.join().unwrap();
        drop(client);

        drop(forwarder);

        // `local` is an ephemeral port, so its number is back in the machine's
        // pool the instant Drop returns, and this binary keeps taking ephemeral
        // ports - every `PortReservation::ephemeral` and every test that binds
        // port 0 - so one of them can and does take `local` in the microseconds
        // that follow. "Nothing can bind `local`" is therefore a claim about the
        // whole test binary rather than about this forwarder, and asserting it
        // is what made this test flake. Assert instead what is only ever true of
        // the forwarder: it stopped serving that port. A reservation that took
        // the number lands in the refused branch below, since reservations bind
        // without listening.
        target.set_nonblocking(true).unwrap();
        match TcpStream::connect_timeout(&local, Duration::from_secs(5)) {
            Err(error) => assert_eq!(
                error.kind(),
                io::ErrorKind::ConnectionRefused,
                "a released forwarder port must refuse connections, got {error}"
            ),
            Ok(_held_open) => {
                // Something else already owns the number. That is only a failure
                // if the something else is this forwarder, and the forwarder is
                // identifiable by behaviour: it dials `target_address`, which
                // nothing but this test knows. Hold the connection open while
                // watching, so a relay that is still running has something to
                // relay rather than a reset socket.
                let deadline = Instant::now() + Duration::from_secs(1);
                while Instant::now() < deadline {
                    match target.accept() {
                        Ok(_) => panic!(
                            "forwarder kept relaying {local} to {target_address} after it was dropped"
                        ),
                        Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                            thread::sleep(Duration::from_millis(10));
                        }
                        Err(error) => panic!("target listener stopped working: {error}"),
                    }
                }
            }
        }
    }

    #[test]
    fn managed_endpoints_must_stay_on_private_ipv4() {
        assert_eq!(
            private_ipv4("192.168.64.2", "guest").unwrap(),
            Ipv4Addr::new(192, 168, 64, 2)
        );
        assert!(private_ipv4("127.0.0.1", "guest").is_err());
        assert!(private_ipv4("8.8.8.8", "guest").is_err());
        assert!(private_ipv4("::1", "guest").is_err());
    }

    #[test]
    fn private_service_gate_waits_for_every_endpoint() {
        let first = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        let second = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        let services = [
            ("first", first.local_addr().unwrap().port()),
            ("second", second.local_addr().unwrap().port()),
        ];

        wait_for_tcp_services(Ipv4Addr::LOCALHOST, &services, Duration::from_millis(250)).unwrap();
    }

    /// The case that shipped broken: the Mac slept for eleven hours, so wall
    /// time ran eleven hours while the monotonic clock ran a tick. The guest
    /// was not running for any of it and is now exactly that far behind.
    #[test]
    fn a_wall_clock_jump_past_the_monotonic_clock_is_read_as_host_sleep() {
        assert_eq!(
            clock_sync_due(
                Duration::from_secs(1),
                Duration::from_secs(1),
                Duration::from_secs(41_250),
            ),
            Some(ClockSyncReason::HostSlept),
        );
    }

    #[test]
    fn an_ordinary_tick_inside_the_interval_does_not_sync() {
        assert_eq!(
            clock_sync_due(
                Duration::from_secs(1),
                Duration::from_secs(1),
                Duration::from_secs(1),
            ),
            None,
        );
    }

    /// Drift that is not a sleep still accumulates, so the interval is a
    /// ceiling on how far the guest may be off before it is put back.
    #[test]
    fn the_interval_bounds_drift_that_was_not_a_sleep() {
        assert_eq!(
            clock_sync_due(
                CLOCK_SYNC_INTERVAL,
                Duration::from_secs(1),
                Duration::from_secs(1),
            ),
            Some(ClockSyncReason::Interval),
        );
    }

    /// A controller with no VM behind it. Enough for anything that only reads
    /// or writes the controller's own state.
    fn test_controller() -> (tempfile::TempDir, ManagedRuntimeController) {
        let root = tempdir().unwrap();
        let controller = ManagedRuntimeController {
            runtime: ManagedRuntime::new(ManagedRuntimeConfig {
                wsl_distribution: DEFAULT_WSL_DISTRIBUTION.to_string(),
                local_root: root.path().join("local"),
                artifact_root: root.path().join("artifacts"),
                bridge_executable: root.path().join("lemma-runtime"),
                #[cfg(target_os = "macos")]
                vz_executable: root.path().join("lemma-vz"),
                #[cfg(windows)]
                wsl_executable: PathBuf::from("wsl.exe"),
            })
            .unwrap(),
            spec: ManagedRuntimeSpec {
                images: crate::host_process::ManagedRuntimeImages {
                    postgres: "postgres@sha256:test".into(),
                    redis: "redis@sha256:test".into(),
                    supertokens: "supertokens@sha256:test".into(),
                    workspace: Some("workspace@sha256:test".into()),
                    function: Some("function@sha256:test".into()),
                },
                credentials: crate::host_process::ManagedRuntimeCredentials {
                    postgres_password: "a".repeat(64),
                    redis_password: "b".repeat(64),
                },
                ports: crate::host_process::ManagedRuntimePorts {
                    postgres: 55432,
                    redis: 56379,
                    supertokens: 53567,
                    backend: 8711,
                    frontend: 3711,
                },
            },
            forwarders: Mutex::new(Vec::new()),
            clock_keeper: Mutex::new(None),
            last_clock_error: Mutex::new(None),
            sandbox_images: Mutex::new(SandboxImageStatus::default()),
            pending_auth: Mutex::new(None),
            status: Mutex::new(Some(ManagedRuntimeStatus {
                endpoint_host: "192.168.64.10".into(),
                host_gateway: "192.168.64.1".into(),
                engine: "containerd".into(),
                active_sandboxes: 0,
                balloon_state: None,
                balloon_target_bytes: None,
            })),
        };

        (root, controller)
    }

    /// Both the ready path and the recovery path warm the images. Two runs
    /// would interleave their downloading/ready events into one stream the app
    /// reads as a single download finishing twice.
    #[test]
    fn only_one_sandbox_image_warmup_is_claimed_at_a_time() {
        let (_root, controller) = test_controller();

        let first = controller.claim_sandbox_image_warmup();
        let second = controller.claim_sandbox_image_warmup();

        assert!(first.is_some());
        assert!(second.is_none(), "a second warm-up ran alongside the first");
        assert_eq!(
            controller.sandbox_image_status().state,
            SANDBOX_IMAGES_DOWNLOADING
        );
    }

    /// Once one has ended, the next start is free to warm again -- a recovered
    /// stack may be looking at a different guest.
    #[test]
    fn a_finished_warmup_does_not_block_the_next_one() {
        let (_root, controller) = test_controller();

        controller.claim_sandbox_image_warmup();
        controller.publish_sandbox_images(
            SandboxImageStatus::new(SANDBOX_IMAGES_READY, "ready"),
            &|_: &SandboxImageStatus| {},
        );

        assert!(controller.claim_sandbox_image_warmup().is_some());
    }

    #[test]
    fn host_processes_use_private_guest_services_without_published_infra_ports() {
        let root = tempdir().unwrap();
        let controller = ManagedRuntimeController {
            runtime: ManagedRuntime::new(ManagedRuntimeConfig {
                wsl_distribution: DEFAULT_WSL_DISTRIBUTION.to_string(),
                local_root: root.path().join("local"),
                artifact_root: root.path().join("artifacts"),
                bridge_executable: root.path().join("lemma-runtime"),
                #[cfg(target_os = "macos")]
                vz_executable: root.path().join("lemma-vz"),
                #[cfg(windows)]
                wsl_executable: PathBuf::from("wsl.exe"),
            })
            .unwrap(),
            spec: ManagedRuntimeSpec {
                images: crate::host_process::ManagedRuntimeImages {
                    postgres: "postgres@sha256:test".into(),
                    redis: "redis@sha256:test".into(),
                    supertokens: "supertokens@sha256:test".into(),
                    workspace: Some("workspace@sha256:test".into()),
                    function: Some("function@sha256:test".into()),
                },
                credentials: crate::host_process::ManagedRuntimeCredentials {
                    postgres_password: "a".repeat(64),
                    redis_password: "b".repeat(64),
                },
                ports: crate::host_process::ManagedRuntimePorts {
                    postgres: 55432,
                    redis: 56379,
                    supertokens: 53567,
                    backend: 8711,
                    frontend: 3711,
                },
            },
            forwarders: Mutex::new(Vec::new()),
            clock_keeper: Mutex::new(None),
            last_clock_error: Mutex::new(None),
            sandbox_images: Mutex::new(SandboxImageStatus::default()),
            pending_auth: Mutex::new(None),
            status: Mutex::new(Some(ManagedRuntimeStatus {
                endpoint_host: "192.168.64.10".into(),
                host_gateway: "192.168.64.1".into(),
                engine: "containerd".into(),
                active_sandboxes: 0,
                balloon_state: None,
                balloon_target_bytes: None,
            })),
        };

        let environment = controller.backend_environment().unwrap();
        assert!(environment["DATABASE_URL"].contains("@192.168.64.10:5432/lemma"));
        assert_eq!(
            environment["SUPERTOKENS_CORE_URL"],
            "http://192.168.64.10:3567"
        );
        assert!(Path::new(&environment["LEMMA_GUEST_CAPABILITY_FILE"])
            .ends_with("local/run/guest-control/guest.capability"));
        assert!(
            Path::new(&environment["LEMMA_GUEST_CONTROL_SOCKET"]).ends_with("local/run/guest.sock")
        );
        assert_eq!(environment["LEMMA_WSL_DISTRIBUTION"], "LemmaRuntime");
        assert!(!environment.values().any(|value| value.contains(":55432")));
    }
}
