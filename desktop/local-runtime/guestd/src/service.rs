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
            per_request_process: self.per_request_process,
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
            mutations: Arc::new(Mutex::new(())),
            per_request_process: false,
        })
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
        let _serialised = if is_observation(&request.operation) {
            None
        } else {
            Some(
                self.mutations
                    .lock()
                    .unwrap_or_else(std::sync::PoisonError::into_inner),
            )
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
