//! Starting the guest, stopping it, and taking its data away.

use super::*;

impl ManagedRuntimeController {
    pub fn prepare_host(&self) -> io::Result<serde_json::Value> {
        self.runtime.prepare_host()
    }

    pub fn start(self: &Arc<Self>) -> io::Result<()> {
        self.start_with_progress(|_, _, _, _| {})
    }

    pub fn start_with_progress(
        self: &Arc<Self>,
        progress: impl FnMut(&str, &str, u64, &str),
    ) -> io::Result<()> {
        self.start_cancellable(progress, || Ok(()))
    }

    pub fn start_cancellable(
        self: &Arc<Self>,
        mut progress: impl FnMut(&str, &str, u64, &str),
        checkpoint: impl Fn() -> io::Result<()>,
    ) -> io::Result<()> {
        checkpoint()?;
        validate_spec(&self.spec)?;
        progress(
            "vm",
            "Starting private runtime",
            32,
            "booting the app-owned Linux appliance",
        );
        self.runtime.start().inspect_err(|_error| {
            let _ = self.runtime.capture_diagnostics();
        })?;
        checkpoint()?;
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
            checkpoint()?;
            progress(component, label, percentage, detail);
            if let Err(error) = self.runtime.request_cancellable(
                operation,
                parameters.clone(),
                self.cancellation.clone(),
            ) {
                let _ = self.runtime.capture_diagnostics();
                let _ = self.runtime.stop();
                return Err(error);
            }
        }

        checkpoint()?;
        let status = self.runtime.health()?;
        progress(
            "private-connectivity",
            "Connecting to local services",
            61,
            "checking this computer can reach the private database and cache",
        );
        // Guest health proves the services are running inside the VM. Host
        // reachability is a separate gate, including when migrations are cached.
        if let Err(error) = self.ensure_forwarders(&status).and_then(|()| {
            self.wait_for_service_connections(
                &status,
                &PRIVATE_SERVICE_PORTS[..2],
                Duration::from_secs(30),
                &checkpoint,
            )
        }) {
            let _ = self.runtime.capture_diagnostics();
            self.clear_forwarders();
            let _ = self.runtime.stop();
            return Err(error);
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
        checkpoint()?;
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
                        .request_cancellable(
                            "core.supertokens",
                            parameters,
                            controller.cancellation.clone(),
                        )
                        .map(|_| ())
                })?
        };
        *self
            .pending_auth
            .lock()
            .expect("pending auth lock poisoned") = Some(auth);
        *self.status.lock().expect("managed runtime status poisoned") = Some(status);
        self.start_clock_keeper();
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
        self.cancel_pending_requests();
        self.stop_clock_keeper();
        // Taken out under each lock; joined outside them. Joining a thread
        // while holding the mutex that thread may itself want is how a
        // shutdown turns into a deadlock, and at best it makes every reader of
        // these two wait for work that is finishing anyway.
        let auth = self
            .pending_auth
            .lock()
            .expect("pending auth lock poisoned")
            .take();
        if let Some(auth) = auth {
            let _ = auth.join();
        }
        let images = self
            .pending_images
            .lock()
            .expect("pending images lock poisoned")
            .take();
        if let Some(images) = images {
            let _ = images.join();
        }
        self.clear_forwarders();
        self.runtime.stop()
    }

    pub fn cancel_pending_requests(&self) {
        self.cancellation.cancel();
    }

    pub fn status(&self) -> Option<ManagedRuntimeStatus> {
        self.status
            .lock()
            .expect("managed runtime status poisoned")
            .clone()
    }

    pub fn check_guest_kernel(&self) -> io::Result<()> {
        self.runtime.check_guest_kernel()
    }
}
