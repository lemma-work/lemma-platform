//! The private services inside the guest, and the forwarders that reach
//! them.

use super::*;

pub(crate) const PRIVATE_SERVICE_PORTS: [(&str, u16); 3] =
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

#[cfg(any(not(target_os = "macos"), test))]
pub(crate) fn wait_for_tcp_services(
    host: Ipv4Addr,
    services: &[(&str, u16)],
    timeout: Duration,
    checkpoint: impl Fn() -> io::Result<()>,
) -> io::Result<()> {
    wait_for_services(services, timeout, checkpoint, |port, budget| {
        TcpStream::connect_timeout(&SocketAddr::from((host, port)), budget).map(|_| ())
    })
}

pub(crate) fn wait_for_services(
    services: &[(&str, u16)],
    timeout: Duration,
    checkpoint: impl Fn() -> io::Result<()>,
    mut connect: impl FnMut(u16, Duration) -> io::Result<()>,
) -> io::Result<()> {
    let deadline = Instant::now() + timeout;
    let mut pending = services
        .iter()
        .map(|&(label, port)| (label, port, io::ErrorKind::TimedOut))
        .collect::<Vec<_>>();
    while !pending.is_empty() {
        checkpoint()?;
        let mut index = 0;
        while index < pending.len() {
            checkpoint()?;
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                break;
            }
            let (_, port, kind) = &mut pending[index];
            match connect(*port, remaining.min(Duration::from_millis(200))) {
                Ok(_) => {
                    pending.remove(index);
                }
                Err(error) => {
                    *kind = error.kind();
                    index += 1;
                }
            }
        }
        checkpoint()?;
        if pending.is_empty() {
            return Ok(());
        }
        if Instant::now() >= deadline {
            let pending = pending
                .iter()
                .map(|(label, _, kind)| format!("{label}: {}", private_connection_reason(*kind)))
                .collect::<Vec<_>>()
                .join(", ");
            let recovery = if cfg!(target_os = "macos") {
                "Restart the local runtime, then retry. If it persists, use Repair installation in Desktop settings."
            } else {
                "Check local network permissions and firewall settings, then retry."
            };
            return Err(io::Error::new(
                io::ErrorKind::TimedOut,
                format!(
                    "This computer could not connect to its private Lemma services ({pending}). {recovery} Your stored data has not been reset."
                ),
            ));
        }
        thread::sleep(
            deadline
                .saturating_duration_since(Instant::now())
                .min(Duration::from_millis(100)),
        );
    }
    Ok(())
}

pub(crate) fn private_connection_reason(kind: io::ErrorKind) -> &'static str {
    match kind {
        io::ErrorKind::PermissionDenied => "connection denied",
        io::ErrorKind::HostUnreachable | io::ErrorKind::NetworkUnreachable => {
            "network route unavailable"
        }
        io::ErrorKind::ConnectionRefused => "service not accepting connections",
        io::ErrorKind::TimedOut => "connection timed out",
        _ => "connection unavailable",
    }
}

impl ManagedRuntimeController {
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
        if let Err(error) = self.wait_for_service_connections(
            &status,
            &PRIVATE_SERVICE_PORTS,
            Duration::from_secs(90),
            || {
                if self.cancellation.is_cancelled() {
                    Err(io::Error::new(
                        io::ErrorKind::Interrupted,
                        "Local startup was cancelled.",
                    ))
                } else {
                    Ok(())
                }
            },
        ) {
            let _ = self.runtime.capture_diagnostics();
            let _ = self.runtime.stop();
            return Err(error);
        }
        Ok(())
    }

    pub(crate) fn ensure_forwarders(&self, status: &ManagedRuntimeStatus) -> io::Result<()> {
        let host_gateway = private_ipv4(&status.host_gateway, "guest host gateway")?;
        let mut current = self.forwarders.lock().expect("forwarder lock poisoned");
        if !current.is_empty() {
            return Ok(());
        }

        // Internal Mac services use the VM's virtual socket; callbacks still
        // use the guest network. WSL retains its existing private service route.
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
        #[cfg(target_os = "macos")]
        for (label, port, guest_port) in [
            ("postgres", self.spec.ports.postgres, 5432),
            ("redis", self.spec.ports.redis, 6379),
            ("supertokens", self.spec.ports.supertokens, 3567),
        ] {
            match TcpForwarder::start_private(
                label,
                SocketAddr::from((Ipv4Addr::LOCALHOST, port)),
                self.runtime.service_socket(guest_port),
            ) {
                Ok(forwarder) => current.push(forwarder),
                Err(error) => {
                    current.clear();
                    return Err(error);
                }
            }
        }
        Ok(())
    }

    pub(crate) fn wait_for_service_connections(
        &self,
        _status: &ManagedRuntimeStatus,
        services: &[(&str, u16)],
        timeout: Duration,
        checkpoint: impl Fn() -> io::Result<()>,
    ) -> io::Result<()> {
        #[cfg(target_os = "macos")]
        {
            let runtime = tokio::runtime::Builder::new_current_thread()
                .enable_all()
                .build()?;
            wait_for_services(services, timeout, checkpoint, |port, budget| {
                // Probe the bridge acknowledgement, not the host TCP listener:
                // a listener can accept while its VM connection is still pending.
                let path = self.runtime.service_socket(port);
                runtime.block_on(async {
                    tokio::time::timeout(
                        budget,
                        crate::tcp_forwarder::connect_private_service(&path),
                    )
                    .await??;
                    Ok(())
                })
            })
        }
        #[cfg(not(target_os = "macos"))]
        wait_for_tcp_services(
            // Unlike macOS, this route reaches the guest's services by address
            // rather than over the private socket bridges, so it does need one.
            private_ipv4(
                _status.endpoint_host.as_deref().ok_or_else(|| {
                    io::Error::new(
                        io::ErrorKind::AddrNotAvailable,
                        "the private runtime reported no network address, so its services \
                         cannot be reached",
                    )
                })?,
                "guest endpoint",
            )?,
            services,
            timeout,
            checkpoint,
        )
    }

    pub(crate) fn clear_forwarders(&self) {
        self.forwarders
            .lock()
            .expect("forwarder lock poisoned")
            .clear();
        *self.status.lock().expect("managed runtime status poisoned") = None;
    }
}
