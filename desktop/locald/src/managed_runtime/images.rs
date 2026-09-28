//! Having the sandbox image before a workspace needs it.

use super::*;

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
/// There is a guest that could hold the image, and nobody has asked for it.
///
/// Also terminal, and for the same reason as `UNSUPPORTED`: the workspace has
/// its answer and stops asking. The distinction matters to Settings rather
/// than to the poll -- this is the one state where offering to fetch the image
/// makes sense, because it is the only one where a fetch is both possible and
/// not already happening.
pub const SANDBOX_IMAGES_NOT_PREPARED: &str = "not-prepared";

#[derive(Clone, Debug, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SandboxImageStatus {
    /// One of the `SANDBOX_IMAGES_*` constants above.
    pub state: String,
    pub detail: String,
    /// How far a download has got, in MB, once the guest can measure it.
    #[serde(rename = "done_mb", skip_serializing_if = "Option::is_none")]
    pub done_mb: Option<u64>,
    #[serde(rename = "total_mb", skip_serializing_if = "Option::is_none")]
    pub total_mb: Option<u64>,
}

impl SandboxImageStatus {
    pub(crate) fn new(state: &str, detail: &str) -> Self {
        Self {
            state: state.to_owned(),
            detail: detail.to_owned(),
            done_mb: None,
            total_mb: None,
        }
    }

    pub(crate) fn downloading(done_mb: u64, total_mb: u64) -> Self {
        Self {
            done_mb: Some(done_mb),
            total_mb: Some(total_mb),
            ..Self::new(
                SANDBOX_IMAGES_DOWNLOADING,
                &format!(
                    "Downloading the image pods run their work in ({done_mb} MB of {total_mb} MB)"
                ),
            )
        }
    }
}

/// The sandbox images this computer last fetched, and the ones a start last
/// fetched unasked. Kept beside locald's other state as `sandbox-images.json`.
///
/// The first says this person uses sandboxes: someone who only runs coding
/// agents never fetched one, and must not be sent several hundred megabytes
/// on an update. The second makes the unasked fetch happen once per release:
/// a download that failed is not retried on every start, only offered.
#[derive(Clone, Debug, Default, Deserialize, PartialEq, Serialize)]
pub(crate) struct PreparedSandboxImages {
    #[serde(default)]
    pub(crate) fetched: Option<PinnedSandboxImages>,
    #[serde(default)]
    pub(crate) fetched_unasked: Option<PinnedSandboxImages>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub(crate) struct PinnedSandboxImages {
    pub(crate) workspace: Option<String>,
    pub(crate) function: Option<String>,
}

impl Default for SandboxImageStatus {
    fn default() -> Self {
        Self::new(SANDBOX_IMAGES_PENDING, "")
    }
}

/// Ask the guest until the images are ready, passing on how far it has got.
///
/// `progress` hears the guest's MB figures while it is downloading. A guest
/// from before it measured them sends none, and is waited on exactly as before.
pub(crate) fn poll_sandbox_image_warmup(
    cancellation: &lemma_desktop_process::Cancellation,
    budget: Duration,
    interval: Duration,
    mut request: impl FnMut() -> io::Result<serde_json::Value>,
    mut progress: impl FnMut(u64, u64),
) -> io::Result<()> {
    let deadline = Instant::now() + budget;
    loop {
        if cancellation.is_cancelled() {
            return Err(io::Error::new(
                io::ErrorKind::Interrupted,
                "image preparation cancelled",
            ));
        }
        if Instant::now() >= deadline {
            return Err(io::Error::new(
                io::ErrorKind::TimedOut,
                "image preparation timed out",
            ));
        }
        let response = request()?;
        if cancellation.is_cancelled() || Instant::now() >= deadline {
            continue;
        }
        match response.get("ready").and_then(serde_json::Value::as_bool) {
            Some(true) => return Ok(()),
            Some(false) => {
                let done = response.get("done_mb").and_then(serde_json::Value::as_u64);
                let total = response.get("total_mb").and_then(serde_json::Value::as_u64);
                if let (Some(done), Some(total)) = (done, total) {
                    if total > 0 {
                        progress(done.min(total), total);
                    }
                }
            }
            None => {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "guest omitted image readiness",
                ))
            }
        }
        thread::sleep(interval.min(deadline.saturating_duration_since(Instant::now())));
    }
}

impl ManagedRuntimeController {
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
        let mut pending = self
            .pending_images
            .lock()
            .expect("pending images lock poisoned");
        if self.cancellation.is_cancelled() {
            return;
        }
        let Some(started) = self.claim_sandbox_image_warmup() else {
            return;
        };
        report(&started);

        let controller = Arc::clone(self);
        if let Some(previous) = pending.take() {
            let _ = previous.join();
        }
        *pending = Some(thread::spawn(move || {
            let parameters = json!({
                "images": controller.spec.images,
                "credentials": controller.spec.credentials,
                "callback_ports": [controller.spec.ports.backend, controller.spec.ports.frontend],
            });
            let result = if cfg!(target_os = "macos") {
                let mut last = None;
                poll_sandbox_image_warmup(
                    &controller.cancellation,
                    Duration::from_secs(8 * 60),
                    Duration::from_secs(1),
                    || {
                        controller.runtime.request_cancellable(
                            "core.sandbox_images_status",
                            parameters.clone(),
                            controller.cancellation.clone(),
                        )
                    },
                    |done, total| {
                        // Said when the figure moves, not on every poll.
                        if last != Some((done, total)) {
                            last = Some((done, total));
                            controller.publish_sandbox_images(
                                SandboxImageStatus::downloading(done, total),
                                &report,
                            );
                        }
                    },
                )
            } else {
                // WSL launches a guest process per request. Background threads
                // cannot outlive that process; its independent channel can wait.
                controller
                    .runtime
                    .request_cancellable(
                        "core.sandbox_images",
                        parameters,
                        controller.cancellation.clone(),
                    )
                    .map(|_| ())
            };
            let status = match result {
                Ok(_) => {
                    controller.record_fetched_sandbox_images();
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
        }));
    }

    /// Record that nothing is fetching the image, and hand back what to say.
    ///
    /// Not a plain assignment: `ready` and a fetch already in flight are both
    /// better answers than "nobody asked". Recovery re-announces after a
    /// restart, and overwriting a running download there would have told the
    /// workspace to stop watching one that was still going.
    pub fn note_sandbox_images_not_prepared(&self) -> SandboxImageStatus {
        let mut current = self
            .sandbox_images
            .lock()
            .expect("sandbox image status poisoned");
        if current.state == SANDBOX_IMAGES_DOWNLOADING || current.state == SANDBOX_IMAGES_READY {
            return current.clone();
        }
        let status = SandboxImageStatus::new(SANDBOX_IMAGES_NOT_PREPARED, "");
        *current = status.clone();
        status
    }

    /// Take the warm-up, or decline because one is already running.
    ///
    /// A compare-and-set under the status lock. Both the ready path and the
    /// recovery path call `warm_sandbox_images`, and two runs would interleave
    /// their downloading/ready events into one stream the app reads as a single
    /// download finishing twice.
    pub(crate) fn claim_sandbox_image_warmup(&self) -> Option<SandboxImageStatus> {
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

    /// Whether this start should fetch the sandbox images nobody asked for,
    /// and if so, the note that it has.
    ///
    /// Only for someone who uses sandboxes -- this computer fetched them
    /// before -- and whose release pins different ones: an update. Without it
    /// the new image arrived on the first Wake up after the update, and the
    /// person waited on the download there. Once per release, recorded before
    /// the fetch starts, so a failure is offered in Settings rather than
    /// retried on every start. With the image reused across releases, the
    /// workspace image's reference usually has not moved and the fetch finds
    /// it already there.
    pub fn claim_unasked_sandbox_image_fetch(&self) -> bool {
        let pinned = self.pinned_sandbox_images();
        if pinned.workspace.is_none() && pinned.function.is_none() {
            return false;
        }
        let mut record = self.prepared_sandbox_images();
        let Some(fetched) = record.fetched.as_ref() else {
            return false;
        };
        if *fetched == pinned || record.fetched_unasked.as_ref() == Some(&pinned) {
            return false;
        }
        record.fetched_unasked = Some(pinned);
        self.write_prepared_sandbox_images(&record);
        true
    }

    fn record_fetched_sandbox_images(&self) {
        let mut record = self.prepared_sandbox_images();
        record.fetched = Some(self.pinned_sandbox_images());
        self.write_prepared_sandbox_images(&record);
    }

    fn pinned_sandbox_images(&self) -> PinnedSandboxImages {
        PinnedSandboxImages {
            workspace: self.spec.images.workspace.clone(),
            function: self.spec.images.function.clone(),
        }
    }

    /// The record, or an empty one: unreadable reads as "never fetched", which
    /// is the answer that downloads nothing.
    pub(crate) fn prepared_sandbox_images(&self) -> PreparedSandboxImages {
        fs::read(&self.prepared_images)
            .ok()
            .and_then(|bytes| serde_json::from_slice(&bytes).ok())
            .unwrap_or_default()
    }

    /// Best effort: losing it costs one unasked fetch, never a broken start.
    fn write_prepared_sandbox_images(&self, record: &PreparedSandboxImages) {
        let written = serde_json::to_vec_pretty(record)
            .map_err(io::Error::other)
            .and_then(|bytes| fs::write(&self.prepared_images, bytes));
        if let Err(error) = written {
            eprintln!("locald: could not record the sandbox images: {error}");
        }
    }

    pub(crate) fn publish_sandbox_images(
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
}
