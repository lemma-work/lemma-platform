//! Turning sharing on and off, one committed step at a time.

use super::*;

impl SharingController {
    pub fn snapshot(&self, include_preflight: bool) -> SharingSnapshot {
        self.observe_tunnel_exit();
        let interfaces = private_ipv4_interfaces();
        let state = self.state.lock().expect("sharing state lock poisoned");
        let who_can_join = state.preferences.who_can_join;
        let mut warnings = Vec::new();
        match state.mode {
            SharingMode::ThisComputer => {}
            SharingMode::LocalNetwork => {
                warnings.push(LOCAL_WARNING.into());
                warnings.push(local_join_warning(who_can_join).into());
            }
            SharingMode::Public => warnings.push(public_warning(who_can_join).into()),
        }
        if state.mode != SharingMode::ThisComputer {
            warnings.push(APPS_LIMITATION.into());
        }
        let qr_svg = if state.mode == SharingMode::LocalNetwork
            && state.phase == "ready"
            && !state.canonical_url.is_empty()
        {
            render_qr(&state.canonical_url)
        } else {
            None
        };
        let mut provider_readiness = HashMap::new();
        if include_preflight {
            provider_readiness.insert("ngrok".into(), preflight_ngrok());
            provider_readiness.insert("cloudflare".into(), preflight_cloudflare());
        }
        SharingSnapshot {
            mode: state.mode,
            phase: state.phase.clone(),
            progress: state.progress,
            canonical_url: state.canonical_url.clone(),
            provider: state.provider,
            provider_readiness,
            tunnel_status: state.tunnel_status.clone(),
            warnings,
            last_error: state.last_error.clone(),
            started_at_ms: state.started_at_ms,
            interfaces,
            selected_interface: state.selected_interface.clone(),
            qr_svg,
            preferences: state.preferences.clone(),
            transition_running: self.transition_running.load(Ordering::Acquire),
            who_can_join,
            public_confirmation: public_warning(who_can_join).into(),
            apps_limitation: APPS_LIMITATION.into(),
        }
    }

    pub fn preflight(&self, provider: Option<TunnelProvider>) -> Value {
        let interfaces = private_ipv4_interfaces();
        match provider {
            Some(TunnelProvider::Ngrok) => json!({
                "provider": "ngrok",
                "readiness": preflight_ngrok(),
                "interfaces": interfaces,
            }),
            Some(TunnelProvider::Cloudflare) => json!({
                "provider": "cloudflare",
                "readiness": preflight_cloudflare(),
                "interfaces": interfaces,
            }),
            None => json!({
                "providers": {
                    "ngrok": preflight_ngrok(),
                    "cloudflare": preflight_cloudflare(),
                },
                "interfaces": interfaces,
            }),
        }
    }

    pub fn prepare_enable(&self, request: &EnableSharingRequest) -> io::Result<PreparedSharing> {
        if request.mode == SharingMode::ThisComputer {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "use sharing.disable to return to This computer",
            ));
        }
        self.begin_transition()?;
        let result = self.prepare_enable_inner(request);
        if let Err(error) = &result {
            self.fail_transition(error.to_string());
        }
        result
    }

    pub(crate) fn prepare_enable_inner(
        &self,
        request: &EnableSharingRequest,
    ) -> io::Result<PreparedSharing> {
        if self
            .active
            .lock()
            .expect("sharing active lock poisoned")
            .is_some()
        {
            return Err(io::Error::new(
                io::ErrorKind::AlreadyExists,
                "sharing is already active; disable it before changing modes",
            ));
        }
        if request.mode == SharingMode::Public && !request.public_warning_confirmed {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                public_warning(self.who_can_join_for(request)),
            ));
        }
        {
            let mut state = self.state.lock().expect("sharing state lock poisoned");
            state.phase = "preflight".into();
            state.progress = 10;
            state.last_error = None;
            state.provider = request.provider;
        }

        let (bind_ip, selected_interface) = match request.mode {
            SharingMode::LocalNetwork => {
                let selection = request.interface.as_deref().ok_or_else(|| {
                    io::Error::new(
                        io::ErrorKind::InvalidInput,
                        "choose a private IPv4 network interface",
                    )
                })?;
                let interface = resolve_private_interface(selection)?;
                (
                    IpAddr::V4(Ipv4Addr::from_str(&interface.address).map_err(io::Error::other)?),
                    Some(interface.name),
                )
            }
            SharingMode::Public => (IpAddr::V4(Ipv4Addr::LOCALHOST), None),
            SharingMode::ThisComputer => unreachable!(),
        };

        let probe_token = crate::native_host_pack::random_hex(32)?;
        let gateway = GatewayHandle::start(
            bind_ip,
            self.frontend_port,
            self.backend_port,
            request.mode,
            request.provider,
            probe_token.clone(),
        )?;
        let gateway_origin = format!("http://{}:{}", display_ip(bind_ip), gateway.address.port());
        {
            let mut state = self.state.lock().expect("sharing state lock poisoned");
            state.phase = "gateway".into();
            state.progress = 30;
            state.tunnel_status = if request.mode == SharingMode::Public {
                "starting"
            } else {
                "not_required"
            }
            .into();
        }

        let (canonical_url, tunnel) = match request.mode {
            SharingMode::LocalNetwork => (gateway_origin, None),
            SharingMode::Public => {
                let provider = request.provider.ok_or_else(|| {
                    io::Error::new(io::ErrorKind::InvalidInput, "choose ngrok or Cloudflare")
                })?;
                let (url, tunnel) = match provider {
                    TunnelProvider::Ngrok => self.start_ngrok(&gateway_origin)?,
                    TunnelProvider::Cloudflare => {
                        self.start_cloudflare(request, &gateway_origin)?
                    }
                };
                (url, Some(tunnel))
            }
            SharingMode::ThisComputer => unreachable!(),
        };

        {
            let mut state = self.state.lock().expect("sharing state lock poisoned");
            state.mode = request.mode;
            state.phase = "restarting".into();
            state.progress = 55;
            state.canonical_url = canonical_url.clone();
            state.tunnel_status = if tunnel.is_some() {
                "connected"
            } else {
                "not_required"
            }
            .into();
            state.started_at_ms = Some(now_ms());
            state.selected_interface = selected_interface.clone();
        }
        *self.active.lock().expect("sharing active lock poisoned") =
            Some(ActiveSharing { gateway, tunnel });
        Ok(PreparedSharing {
            mode: request.mode,
            origin: canonical_url,
            probe_token,
        })
    }

    pub fn commit_enable(&self, request: &EnableSharingRequest) -> io::Result<()> {
        {
            let mut state = self.state.lock().expect("sharing state lock poisoned");
            state.phase = "ready".into();
            state.progress = 100;
            state.last_error = None;
            if let Some(interface) = request.interface.as_ref() {
                state.preferences.selected_interface = Some(interface.clone());
            }
            if let Some(provider) = request.provider {
                state.preferences.last_provider = Some(provider);
            }
            if let Some(who_can_join) = request.who_can_join {
                state.preferences.who_can_join = who_can_join;
            }
            if request.provider == Some(TunnelProvider::Cloudflare) {
                state.preferences.cloudflare_setup = request.cloudflare_setup;
                if request.cloudflare_setup == CloudflareSetup::Existing {
                    state.preferences.cloudflare_tunnel_id = request.cloudflare_tunnel_id.clone();
                    state.preferences.cloudflare_tunnel_name =
                        request.cloudflare_tunnel_name.clone();
                    state.preferences.cloudflare_hostname = request.hostname.clone();
                    state.preferences.cloudflare_tunnel_owned = false;
                    state.preferences.cloudflare_dns_routed = false;
                }
            }
            state.preferences.schema_version = SHARING_SCHEMA_VERSION;
            persist_private_json(&self.preferences_path, &state.preferences)?;
        }
        // Only now: the stack behind the gateway is the hardened one, checked
        // through the gateway itself, and the change is recorded.
        self.set_gateway_open(true);
        self.transition_running.store(false, Ordering::Release);
        Ok(())
    }

    /// Serve visitors, or answer them 503 while the stack behind is restarting.
    ///
    /// Held for every restart that happens while something outside this Mac
    /// can reach the gateway: turning sharing on (until the hardened stack is
    /// verified), turning it off (the stack comes back in local mode while the
    /// tunnel is still up), and a join-policy change.
    pub(crate) fn set_gateway_open(&self, open: bool) {
        // An atomic store under the lock, and nothing that waits.
        let active = self.active.lock().expect("sharing active lock poisoned");
        if let Some(active) = active.as_ref() {
            active.gateway.set_open(open);
        }
    }

    pub fn rollback_enable(&self, message: impl Into<String>) {
        self.stop_active();
        self.fail_transition(message.into());
    }

    pub fn begin_disable(&self) -> io::Result<bool> {
        self.begin_transition()?;
        let active = self
            .active
            .lock()
            .expect("sharing active lock poisoned")
            .is_some();
        if !active {
            self.transition_running.store(false, Ordering::Release);
            return Ok(false);
        }
        // The stack restarts into local mode -- DEBUG, no abuse controls --
        // before the tunnel is torn down, so visitors are turned away first.
        self.set_gateway_open(false);
        let mut state = self.state.lock().expect("sharing state lock poisoned");
        state.phase = "restarting".into();
        state.progress = 40;
        state.last_error = None;
        Ok(true)
    }

    pub fn commit_disable(&self) {
        self.stop_active();
        {
            let mut state = self.state.lock().expect("sharing state lock poisoned");
            state.mode = SharingMode::ThisComputer;
            state.phase = "ready".into();
            state.progress = 100;
            state.canonical_url = self.local_origin.clone();
            state.tunnel_status = "stopped".into();
            state.started_at_ms = None;
            state.last_error = None;
        }
        self.transition_running.store(false, Ordering::Release);
    }

    pub fn abort_disable(&self, message: impl Into<String>) {
        // Sharing stays on, behind the shared stack it was rolled back to.
        self.set_gateway_open(true);
        let mut state = self.state.lock().expect("sharing state lock poisoned");
        state.phase = "ready".into();
        state.progress = 100;
        state.last_error = Some(message.into());
        self.transition_running.store(false, Ordering::Release);
    }

    pub fn force_disable(&self) {
        self.stop_active();
        {
            let mut state = self.state.lock().expect("sharing state lock poisoned");
            state.mode = SharingMode::ThisComputer;
            state.phase = "ready".into();
            state.progress = 100;
            state.canonical_url = self.local_origin.clone();
            state.tunnel_status = "stopped".into();
            state.started_at_ms = None;
        }
        self.transition_running.store(false, Ordering::Release);
    }

    /// The join policy an enable request will run with: its own, or the saved one.
    pub fn who_can_join_for(&self, request: &EnableSharingRequest) -> WhoCanJoin {
        request.who_can_join.unwrap_or_else(|| self.who_can_join())
    }

    pub fn who_can_join(&self) -> WhoCanJoin {
        self.state
            .lock()
            .expect("sharing state lock poisoned")
            .preferences
            .who_can_join
    }

    /// Save a new join policy, and say where sharing is live so it can be applied.
    ///
    /// Taken as a transition, so it cannot interleave with an enable that has
    /// already computed its environment from the old value -- the enable would
    /// then commit a preference its running backend does not reflect. Returns
    /// the live origin and mode when sharing is on, `None` when the change only
    /// needs to be remembered for next time. The caller finishes the transition
    /// with `finish_who_can_join`, after it has applied the change or failed to.
    pub fn begin_set_who_can_join(
        &self,
        who_can_join: WhoCanJoin,
    ) -> io::Result<(WhoCanJoin, Option<(String, SharingMode)>)> {
        self.begin_transition()?;
        let mut state = self.state.lock().expect("sharing state lock poisoned");
        let previous = state.preferences.who_can_join;
        state.preferences.who_can_join = who_can_join;
        state.preferences.schema_version = SHARING_SCHEMA_VERSION;
        if let Err(error) = persist_private_json(&self.preferences_path, &state.preferences) {
            state.preferences.who_can_join = previous;
            drop(state);
            self.transition_running.store(false, Ordering::Release);
            return Err(error);
        }
        let live = (state.mode != SharingMode::ThisComputer)
            .then(|| (state.canonical_url.clone(), state.mode));
        Ok((previous, live))
    }

    /// End a join-policy change. On failure the previous value is restored and saved.
    pub fn finish_who_can_join(&self, restore: Option<WhoCanJoin>) {
        if let Some(previous) = restore {
            let mut state = self.state.lock().expect("sharing state lock poisoned");
            state.preferences.who_can_join = previous;
            // Best effort: the running backend already has the previous value
            // back, and a preference file that disagrees with it is corrected
            // by the next successful change.
            let _ = persist_private_json(&self.preferences_path, &state.preferences);
        }
        self.transition_running.store(false, Ordering::Release);
    }

    pub fn local_origin(&self) -> &str {
        &self.local_origin
    }

    pub fn active_mode(&self) -> SharingMode {
        self.state.lock().expect("sharing state lock poisoned").mode
    }

    pub fn poll_failure(&self) -> Option<String> {
        self.observe_tunnel_exit()
    }

    pub(crate) fn begin_transition(&self) -> io::Result<()> {
        self.transition_running
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .map(|_| ())
            .map_err(|_| {
                io::Error::new(
                    io::ErrorKind::WouldBlock,
                    "another sharing transition is already running",
                )
            })
    }

    pub(crate) fn fail_transition(&self, message: String) {
        let mut state = self.state.lock().expect("sharing state lock poisoned");
        state.mode = SharingMode::ThisComputer;
        state.phase = "error".into();
        state.progress = 0;
        state.canonical_url = self.local_origin.clone();
        state.tunnel_status = "stopped".into();
        state.started_at_ms = None;
        state.last_error = Some(redact_error(&message));
        self.transition_running.store(false, Ordering::Release);
    }

    pub(crate) fn stop_active(&self) {
        // Taken out under the lock, stopped outside it. The guard used to live
        // to the end of the `if let`, which held it across a tunnel process's
        // termination and the gateway's shutdown -- and `snapshot` takes the
        // same lock to say whether sharing is on, so turning it off froze the
        // page that turned it off.
        let taken = self
            .active
            .lock()
            .expect("sharing active lock poisoned")
            .take();
        if let Some(mut active) = taken {
            if let Some(tunnel) = active.tunnel.as_mut() {
                tunnel.stop();
            }
            active.gateway.stop();
        }
    }

    pub(crate) fn observe_tunnel_exit(&self) -> Option<String> {
        let mut active = self.active.lock().expect("sharing active lock poisoned");
        let active = active.as_mut()?;
        if active.tunnel.is_none() && active.gateway.address.ip() != IpAddr::V4(Ipv4Addr::LOCALHOST)
        {
            let address = active.gateway.address.ip().to_string();
            let available = private_ipv4_interfaces()
                .iter()
                .any(|interface| interface.address == address);
            if !available {
                let message =
                    "the selected local-network interface is no longer available".to_owned();
                let mut state = self.state.lock().expect("sharing state lock poisoned");
                state.phase = "error".into();
                state.tunnel_status = "interface_lost".into();
                state.last_error = Some(message.clone());
                return Some(message);
            }
        }
        let tunnel = active.tunnel.as_mut()?;
        match tunnel.child.try_wait() {
            Ok(Some(status)) => {
                let message = format!(
                    "{} tunnel exited unexpectedly with {status}",
                    provider_name(tunnel.provider)
                );
                let mut state = self.state.lock().expect("sharing state lock poisoned");
                state.phase = "error".into();
                state.tunnel_status = "exited".into();
                state.last_error = Some(message.clone());
                Some(message)
            }
            Ok(None) => None,
            Err(error) => Some(format!("could not inspect tunnel process: {error}")),
        }
    }
}
