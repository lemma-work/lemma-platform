use super::*;

impl Daemon {
    /// Destroy everything on this computer that the user made, then start clean.
    ///
    /// A locald verb rather than something the shell does, because only locald
    /// owns the VM lifecycle -- and because the progress the splash already
    /// renders comes from here. It takes `lifecycle`, the same
    /// guard `start`, `stop`, `restart` and `runtime.prepare` take, so a reset
    /// can never interleave with a start.
    ///
    /// There is no rollback arm. `repair_runtime` can roll back because a
    /// runtime is replaceable; data is not, and by the time anything here can
    /// fail it is already gone. A failed restart afterwards therefore reports
    /// that plainly and leaves the full-reinstall option on screen, rather than
    /// retrying and pretending.
    pub(super) fn start_local_data_reset(
        self: &Arc<Self>,
        request: Value,
        client: mpsc::SyncSender<String>,
    ) {
        let id = request.get("id").cloned();
        if request.get("confirm").and_then(Value::as_str) != Some("reset-local-data") {
            self.send_direct(
                &client,
                error_event(
                    "confirmation-required",
                    "a local data reset must be confirmed explicitly",
                    id.as_ref(),
                ),
            );
            return;
        }
        let Some(manager) = self.host_processes.as_ref().cloned() else {
            self.send_direct(
                &client,
                error_event(
                    "host-pack-unavailable",
                    "this installation does not manage local services",
                    id.as_ref(),
                ),
            );
            return;
        };
        if self.lifecycle.begin().is_err() {
            self.send_direct(
                &client,
                error_event("busy", "another local operation is running", id.as_ref()),
            );
            return;
        }
        self.send_direct(
            &client,
            json!({
                "v": PROTOCOL_VERSION,
                "event": "ack",
                "cmd": "local.reset-data",
                "id": id.as_ref(),
            }),
        );

        let daemon = Arc::clone(self);
        thread::spawn(move || {
            let outcome = daemon.perform_local_data_reset(&manager, id.as_ref());
            match outcome {
                Ok(summary) => {
                    daemon.broadcast(json!({
                        "v": PROTOCOL_VERSION,
                        "event": "local.data-reset",
                        "id": id.as_ref(),
                        "summary": summary,
                    }));
                    daemon.broadcast(json!({
                        "v": PROTOCOL_VERSION,
                        "event": "done",
                        "cmd": "local.reset-data",
                        "id": id.as_ref(),
                        "ok": true,
                    }));
                }
                Err(error) => {
                    daemon.broadcast(error_event(
                        "local-data-reset-incomplete",
                        error.to_string(),
                        id.as_ref(),
                    ));
                    daemon.broadcast(json!({
                        "v": PROTOCOL_VERSION,
                        "event": "done",
                        "cmd": "local.reset-data",
                        "id": id.as_ref(),
                        "ok": false,
                    }));
                }
            }
            daemon.lifecycle.finish();
        });
    }

    /// The reset itself. Order is the safety property.
    /// The reset itself. Order is the safety property.
    pub(super) fn perform_local_data_reset(
        self: &Arc<Self>,
        manager: &Arc<HostProcessManager>,
        id: Option<&Value>,
    ) -> io::Result<Value> {
        // Stop the things holding the data before removing it. The backend
        // holds Postgres connections and the workspace bind mounts; the Agent
        // Host runs jobs against the workspace it is about to lose.
        manager.stop_all()?;
        // Best effort: an Agent Host that will not stop is not a reason to
        // leave the user stuck with data they cannot use. It is suspended
        // rather than disabled, so it comes back with the clean workspace.
        let _ = self.agent_host.suspend();

        self.broadcast(json!({
            "v": PROTOCOL_VERSION,
            "event": "phase",
            "id": id,
            "key": "reset-data",
            "label": "Erasing local data",
            "detail": "removing databases, files and workspaces on this computer",
            "progress": 20,
        }));

        let summary = self.discard_local_data()?;

        // The database these describe no longer exists. Leaving them would have
        // the next start skip migrations against an empty schema, and the
        // backend would come up against tables that were never created.
        manager.forget_setup_stamps()?;

        // Only once the data is actually gone. A marker cleared before a failed
        // wipe would let the next start run against data it cannot read, which
        // is the state this whole path exists to escape.
        crate::paths::clear_data_reset(&self.paths.root)?;

        self.broadcast(json!({
            "v": PROTOCOL_VERSION,
            "event": "phase",
            "id": id,
            "key": "reset-data",
            "label": "Setting up again",
            "detail": "starting Lemma with a clean workspace",
            "progress": 45,
        }));
        self.start_host_packs(manager, id)?;
        Ok(summary)
    }

    /// Ask the guest to tidy up; discard the whole disk if it cannot.
    ///
    /// Chosen by a precondition rather than by retrying a failure. The surgical
    /// path keeps the pulled container images, which for the case this exists
    /// for -- a Postgres major that moved -- is the difference between seconds
    /// and re-downloading several hundred megabytes.
    /// Ask the guest to tidy up; discard the whole disk if it cannot.
    ///
    /// Chosen by a precondition rather than by retrying a failure. The surgical
    /// path keeps the pulled container images, which for the case this exists
    /// for -- a Postgres major that moved -- is the difference between seconds
    /// and re-downloading several hundred megabytes.
    pub(super) fn discard_local_data(&self) -> io::Result<Value> {
        let Some(runtime) = self.managed_runtime.as_ref() else {
            // No guest and no data disk -- but files and object storage are on
            // the host either way, so they still have to go.
            let host_side = crate::paths::discard_host_side_data(&self.paths.root)?;
            return Ok(json!({"strategy": "none", "host_bytes": host_side}));
        };
        // The user's files live on the Mac, not in the guest, so neither
        // strategy below touches them. `LOCAL_FILE_STORAGE_ROOT` and
        // `LOCAL_OBJECT_STORAGE_ROOT` point at `<root>/data/...`, and clearing
        // only the guest erased the rows while leaving every uploaded byte on
        // disk -- under a button whose own text says it "erases your pods,
        // files and accounts". Someone resetting before handing the machine on
        // would have kept all of it.
        let host_side = crate::paths::discard_host_side_data(&self.paths.root)?;
        if runtime.probe().is_healthy() {
            let removed = runtime.reset_guest_data()?;
            runtime.stop_infrastructure()?;
            return Ok(json!({
                "strategy": "guest",
                "removed": removed,
                "host_bytes": host_side,
            }));
        }
        #[cfg(target_os = "macos")]
        {
            let reclaimed = runtime.discard_data_disk()?;
            Ok(json!({
                "strategy": "disk",
                "reclaimed_bytes": reclaimed + host_side,
            }))
        }
        // Elsewhere the guest is the only way in: a WSL distribution is
        // unregistered rather than having a disk file to unlink, and that path
        // is not wired up yet.
        #[cfg(not(target_os = "macos"))]
        Err(io::Error::other(
            "the private runtime is not responding, so local data cannot be reset from here",
        ))
    }
}
