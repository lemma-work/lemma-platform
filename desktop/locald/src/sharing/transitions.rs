//! Turning sharing on and off, one committed step at a time.

use super::*;

impl SharingController {
    pub fn snapshot(&self, include_preflight: bool) -> SharingSnapshot {
        self.observe_tunnel_exit();
        let interfaces = private_ipv4_interfaces();
        let state = self.state.lock().expect("sharing state lock poisoned");
        let mut warnings = Vec::new();
        match state.mode {
            SharingMode::ThisComputer => {}
            SharingMode::LocalNetwork => warnings.push(LOCAL_WARNING.into()),
            SharingMode::Public => warnings.push(PUBLIC_WARNING.into()),
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
            public_confirmation: PUBLIC_WARNING.into(),
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
                PUBLIC_WARNING,
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

        let gateway =
            GatewayHandle::start(bind_ip, self.frontend_port, self.backend_port, request.mode)?;
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
        self.transition_running.store(false, Ordering::Release);
        Ok(())
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
