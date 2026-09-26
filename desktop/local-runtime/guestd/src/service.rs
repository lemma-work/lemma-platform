//! The service itself, and the dispatch every request goes through.

use super::*;

/// Whether an operation only reads guest state.
///
/// Observation answers concurrently; everything else is serialised. Anything
/// not named here is treated as a mutation, so a new operation is safe by
/// default and only becomes concurrent when someone says it may.
pub(crate) fn is_observation(operation: &str) -> bool {
    matches!(
        operation,
        "health" | "core.status" | "core.sandbox_images_status" | "sandbox.status" | "sandbox.list"
    ) || operation.starts_with("diagnostics.")
}

pub struct GuestService<E: Engine> {
    pub(crate) engine: Arc<E>,
    pub(crate) state_root: PathBuf,
    pub(crate) endpoint_host: Option<String>,
    pub(crate) dynamic_endpoint_host: bool,
    pub(crate) host_gateway: String,
    pub(crate) capability: Option<String>,
    pub(crate) kernel_taint_path: Option<PathBuf>,
    pub(crate) image_warmups: Arc<Mutex<HashMap<SandboxImageSet, ImageWarmupState>>>,
    /// The last answer `running_sandbox_count` gave, and when.
    ///
    /// Counting running sandboxes forks `nerdctl ps`. The host polls guest
    /// health every five seconds forever, so an idle machine spawned a
    /// containerd CLI process 17,280 times a day to be told the same number.
    /// Admission does not read this -- deciding whether another sandbox may
    /// start has to see the present, not a cached past.
    pub(crate) sandbox_count_cache: Arc<Mutex<Option<(Instant, usize)>>>,
    /// Held for the duration of every mutating operation. See `handle`.
    pub(crate) mutations: Arc<Mutex<()>>,
    /// Whether this process exits as soon as it has answered.
    ///
    /// True for `lemma-guestd request`, which is how Windows reaches the
    /// guest: `wsl.exe --exec` starts one guestd per request and it ends with
    /// the reply. False for `serve-vsock`, which is resident and serves every
    /// request on the machine over one channel.
    ///
    /// It decides who owns a long download. A resident guest hands the caller
    /// a retryable answer and keeps fetching on a worker thread, so its single
    /// control channel stays free for health and for every other sandbox. A
    /// per-request guest has no thread that can outlive the reply -- `main`
    /// returning ends them all -- so the same code left `nerdctl` orphaned,
    /// recorded neither success nor failure anywhere a later request could
    /// read, and answered "still downloading" for ever however the transfer
    /// had actually gone.
    pub(crate) per_request_process: bool,
    /// Whether a new sandbox first ensures the guest's firewall keeps it away
    /// from PostgreSQL, Redis and SuperTokens (see `sandbox_firewall`).
    ///
    /// On for the real guest and off for a service built in a test, which has
    /// no kernel to program -- the rule set and the installer are tested
    /// directly instead.
    pub(crate) sandbox_isolation: bool,
    /// This boot of the guest, when known: an image that passed its unpack
    /// check is not checked again until the guest boots again (see
    /// `images::ImageCheck`). `None` checks every time.
    pub(crate) boot_id: Option<String>,
    /// Whether an image's registry answers, asked before an image is removed
    /// to be fetched again: removed while offline, it cannot come back.
    pub(crate) registry_reachable: fn(&str) -> bool,
}

impl<E: Engine> Clone for GuestService<E> {
    fn clone(&self) -> Self {
        Self {
            engine: Arc::clone(&self.engine),
            state_root: self.state_root.clone(),
            endpoint_host: self.endpoint_host.clone(),
            dynamic_endpoint_host: self.dynamic_endpoint_host,
            host_gateway: self.host_gateway.clone(),
            capability: self.capability.clone(),
            kernel_taint_path: self.kernel_taint_path.clone(),
            mutations: Arc::clone(&self.mutations),
            image_warmups: Arc::clone(&self.image_warmups),
            sandbox_count_cache: Arc::clone(&self.sandbox_count_cache),
            per_request_process: self.per_request_process,
            sandbox_isolation: self.sandbox_isolation,
            boot_id: self.boot_id.clone(),
            registry_reachable: self.registry_reachable,
        }
    }
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
        // Not fatal when it is absent.
        //
        // This address comes from the vmnet DHCP lease, which is exactly what
        // macOS Local Network privacy can withhold from the responsible app.
        // Refusing to start without it meant a denied permission put guestd
        // into a systemd restart loop: the vsock control port never listened,
        // the host waited out its two minutes and reported "managed guest did
        // not become ready", and the private service bridges -- which need no
        // lease at all -- were never given the chance to work. The address is
        // needed for the host to reach a *sandbox*, so that is what fails
        // without it, by name, and the rest of the guest serves.
        let endpoint_host = configured_endpoint_host.or_else(discover_guest_ip);
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
        service.kernel_taint_path = Some(PathBuf::from("/proc/sys/kernel/tainted"));
        service.sandbox_isolation = true;
        service.boot_id = fs::read_to_string("/proc/sys/kernel/random/boot_id")
            .ok()
            .map(|value| value.trim().to_owned())
            .filter(|value| !value.is_empty());
        service.registry_reachable = image_registry_reachable;
        Ok(service)
    }
}

impl<E: Engine + 'static> GuestService<E> {
    pub fn new(
        engine: E,
        state_root: PathBuf,
        endpoint_host: Option<String>,
        host_gateway: String,
        capability: Option<String>,
    ) -> Result<Self, GuestError> {
        if endpoint_host.as_deref().is_some_and(|host| !valid_ip(host)) || !valid_ip(&host_gateway)
        {
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
            kernel_taint_path: None,
            image_warmups: Arc::new(Mutex::new(HashMap::new())),
            sandbox_count_cache: Arc::new(Mutex::new(None)),
            mutations: Arc::new(Mutex::new(())),
            per_request_process: false,
            sandbox_isolation: false,
            boot_id: None,
            registry_reachable: |_| true,
        })
    }

    /// The directory holding the loopback relay's socket, which `sandbox.ensure`
    /// mounts into the one sandbox granted `host_loopback`.
    pub(crate) fn host_loopback_directory(&self) -> PathBuf {
        host_loopback_directory(&self.state_root)
    }

    /// Say that this process ends with the request it is answering.
    ///
    /// Called by the `request` subcommand, not inferred from the platform:
    /// what matters is the process model, and the binary is the only thing
    /// that knows which one it was started in.
    pub fn set_per_request_process(&mut self) {
        self.per_request_process = true;
    }

    /// The address the *host* can reach this guest on, if it has one.
    ///
    /// `None` means no DHCP lease -- see `discover`. Everything that only needs
    /// to reach the guest's own services uses `GUEST_LOOPBACK` instead and is
    /// unaffected.
    pub(crate) fn current_endpoint_host(&self) -> Option<String> {
        if self.dynamic_endpoint_host {
            discover_guest_ip().or_else(|| self.endpoint_host.clone())
        } else {
            self.endpoint_host.clone()
        }
    }

    /// The guest address, or a named failure for the callers that need one.
    ///
    /// Only reached by operations that hand the host somewhere to connect --
    /// a sandbox's URL. Core services do not, because the host reaches those
    /// over the private socket bridges.
    pub(crate) fn routable_endpoint_host(&self) -> Result<String, GuestError> {
        self.current_endpoint_host().ok_or_else(|| GuestError {
            code: "guest_network_unavailable".into(),
            message: "The private runtime has no network address, so sandboxes \
                      cannot be reached. On macOS this is what a denied Local \
                      Network permission looks like."
                .into(),
            retryable: true,
            status_code: 503,
        })
    }

    /// Where mutations take their cross-process lock.
    pub(crate) fn mutation_lock_path(&self) -> PathBuf {
        self.state_root.join("run/mutations.lock")
    }

    /// An exclusive `flock` held for one mutation, waiting for any other
    /// process's. Released by the kernel when the file is dropped or its
    /// holder dies, so a guestd killed mid-request never leaves it held.
    pub(crate) fn lock_mutations_across_processes(&self) -> Result<fs::File, GuestError> {
        use std::os::fd::AsRawFd;
        let file = OpenOptions::new()
            .create(true)
            .append(true)
            .mode(0o600)
            .open(self.mutation_lock_path())
            .map_err(|error| GuestError::engine(format!("mutation lock: {error}")))?;
        loop {
            // SAFETY: a descriptor this scope owns, for the call's duration.
            if unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX) } == 0 {
                return Ok(file);
            }
            let error = io::Error::last_os_error();
            if error.kind() != io::ErrorKind::Interrupted {
                return Err(GuestError::engine(format!("mutation lock: {error}")));
            }
        }
    }

    pub fn handle(&self, request: GuestRequest) -> GuestResponse {
        match self.try_handle(request) {
            Ok(result) => GuestResponse::success(result),
            Err(error) => GuestResponse::failure(error),
        }
    }

    pub(crate) fn try_handle(&self, request: GuestRequest) -> Result<Value, GuestError> {
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
        if request.operation != "system.shutdown" && !request.operation.starts_with("diagnostics.")
        {
            self.check_kernel_health()?;
        }
        // Everything that changes the guest runs one at a time, exactly as it
        // did when a single connection carried every request. Observation --
        // health above all -- deliberately does not take this lock: the host
        // probes health every five seconds with a five second budget, and a
        // `sandbox.ensure` waiting on a callback can legitimately hold the
        // guest for far longer. When one queue served both, that wait timed
        // the probe out, the host concluded the runtime was gone, and it tore
        // down the database forwarders under a running backend.
        //
        // And across processes. On Windows each request is its own guestd
        // (`wsl.exe --exec lemma-guestd request`), so the in-process mutex
        // above serialised nothing there: two `sandbox.ensure`s for the same
        // sandbox, or an ensure beside a `core.stop`, ran side by side. The
        // file lock is what does it on that transport, and is harmless on the
        // resident one.
        let _serialised = if is_observation(&request.operation) {
            None
        } else {
            let in_process = self
                .mutations
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            Some((in_process, self.lock_mutations_across_processes()?))
        };
        if !is_observation(&request.operation) {
            refuse_unbound_data()?;
        }
        match request.operation.as_str() {
            "health" => self.health(),
            "diagnostics.network" => Ok(network_diagnostics()),
            "diagnostics.guest" => Ok(guest_diagnostics()),
            "diagnostics.sandbox" => self.sandbox_diagnostics(request.parameters),
            "system.shutdown" => self.shutdown(),
            "system.clock" => self.set_clock(request.parameters),
            "core.ensure" => self.ensure_core(request.parameters),
            "core.images" => self.ensure_core_stage(request.parameters, CoreStage::Images),
            "core.sandbox_images" => {
                self.ensure_core_stage(request.parameters, CoreStage::SandboxImages)
            }
            "core.sandbox_images_status" => {
                let parameters = self.parse_core_parameters(request.parameters)?;
                let ready = self.poll_sandbox_images(&parameters)?;
                Ok(json!({"ready": ready}))
            }
            "core.postgres" => self.ensure_core_stage(request.parameters, CoreStage::Postgres),
            "core.redis" => self.ensure_core_stage(request.parameters, CoreStage::Redis),
            "core.supertokens" => {
                self.ensure_core_stage(request.parameters, CoreStage::SuperTokens)
            }
            "core.status" => self.core_status(),
            "core.stop" => self.stop_core(),
            "core.reset_data" => self.reset_data(request.parameters),
            "core.prune_images" => self.prune_images(request.parameters),
            "core.trim" => self.trim_data_disk(),
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
}
