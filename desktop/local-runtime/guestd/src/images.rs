//! Having an image before it is needed: warm-up, pulls that several
//! callers join, and the marker that says a cache is ready.

use super::*;

pub(crate) type SandboxImageSet = (Option<String>, Option<String>);

pub(crate) enum ImageWarmupState {
    Running,
    Finished(Result<(), GuestError>),
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
pub(crate) enum PullState {
    Running,
    Failed(String),
}

pub(crate) fn in_flight_pulls() -> &'static Mutex<HashMap<String, PullState>> {
    pub(crate) static PULLS: OnceLock<Mutex<HashMap<String, PullState>>> = OnceLock::new();
    PULLS.get_or_init(|| Mutex::new(HashMap::new()))
}

/// The pull itself, callable without a `GuestService` so a thread can run it.
pub(crate) fn pull_with(engine: &dyn Engine, image: &str) -> Result<(), String> {
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

pub(crate) fn pull_in_progress(image: &str) -> GuestError {
    GuestError {
        code: "image_pulling".into(),
        message: format!("still downloading {image}"),
        retryable: true,
        status_code: 503,
    }
}

pub(crate) fn validate_image(image: &str) -> Result<(), GuestError> {
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

impl<E: Engine + 'static> GuestService<E> {
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
    pub(crate) fn ensure_sandbox_images(
        &self,
        parameters: &CoreParameters,
    ) -> Result<(), GuestError> {
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

    // A persistent guest serves health and sandbox requests on this channel
    // while the workspace opens. Inspection and cache repair can also wait
    // on the engine, so the entire preparation runs off the control channel.
    pub(crate) fn poll_sandbox_images(
        &self,
        parameters: &CoreParameters,
    ) -> Result<bool, GuestError> {
        let key = (
            parameters.images.workspace.clone(),
            parameters.images.function.clone(),
        );
        let mut jobs = self
            .image_warmups
            .lock()
            .expect("image warm-up table poisoned");
        match jobs.get(&key) {
            Some(ImageWarmupState::Running) => return Ok(false),
            Some(ImageWarmupState::Finished(Ok(()))) => return Ok(true),
            Some(ImageWarmupState::Finished(Err(error))) => {
                let error = error.clone();
                jobs.remove(&key);
                return Err(error);
            }
            None => {}
        }
        jobs.insert(key.clone(), ImageWarmupState::Running);
        let service = self.clone();
        let parameters = parameters.clone();
        let worker_key = key.clone();
        if thread::Builder::new()
            .name("lemma-guest-image-warmup".into())
            .spawn(move || {
                let result = service.ensure_sandbox_images(&parameters);
                service
                    .image_warmups
                    .lock()
                    .expect("image warm-up table poisoned")
                    .insert(worker_key, ImageWarmupState::Finished(result));
            })
            .is_err()
        {
            jobs.remove(&key);
            return Err(GuestError::engine(
                "could not start sandbox image preparation",
            ));
        }
        Ok(false)
    }

    pub(crate) fn ensure_core_images(&self, parameters: &CoreParameters) -> Result<(), GuestError> {
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

    /// Is this image already unpacked here?
    pub(crate) fn image_present(&self, image: &str) -> Result<bool, GuestError> {
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
    /// Persistent guests poll this work so health and other sandbox requests
    /// remain responsive during registry transfers. One-shot WSL requests use
    /// the blocking path because their process exits with the response.
    pub(crate) fn start_or_join_pull(&self, image: &str) -> Result<(), GuestError> {
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
    pub(crate) fn ensure_image(&self, image: &str) -> Result<(), GuestError> {
        if self.image_present(image)? {
            return Ok(());
        }
        self.pull_image(image)
    }

    /// Make sure an image is here, without holding the control channel for it.
    ///
    /// Returns a retryable `image_pulling` while the download runs, so every
    /// other sandbox operation on the machine keeps being served.
    pub(crate) fn ensure_image_available(&self, image: &str) -> Result<(), GuestError> {
        if self.image_present(image)? {
            return Ok(());
        }
        self.start_or_join_pull(image)
    }

    pub(crate) fn ensure_sandbox_image(
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

    pub(crate) fn cache_reset_marker(&self) -> PathBuf {
        self.state_root.join("container-cache-reset-required")
    }

    pub(crate) fn schedule_cache_reset(&self) -> Result<(), GuestError> {
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

    pub(crate) fn sandbox_image_marker_is_ready(
        &self,
        image: &str,
        workload_kind: WorkloadKind,
    ) -> bool {
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

    pub(crate) fn pull_image(&self, image: &str) -> Result<(), GuestError> {
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
}
