//! The shipped adapter list, and resolving one of its entries.

use super::{
    AdapterManifest, AdapterSpec, Arc, BUILTIN_MANIFEST, Digest, HarnessSnapshot, HashMap, Mutex,
    Path, PathBuf, ResolvedAdapter, Sha256, TRANSIENT_MARKER, VERSION_PROBE_TIMEOUT,
    VersionUnknown, cached_adapter_directory, cached_adapter_executable, ensure_adapter_node_runs,
    fingerprint_path, probe_version, reason_is_transient, reason_without_marker,
    resolve_executable, snapshot_installing, snapshot_ready, snapshot_unavailable,
    verify_cached_adapter, version_is_at_least,
};

impl AdapterManifest {
    pub fn builtin() -> anyhow::Result<Self> {
        let parsed: Self = serde_json::from_str(BUILTIN_MANIFEST)?;
        parsed.validate()?;
        Ok(parsed)
    }

    pub fn from_path(path: &Path) -> anyhow::Result<Self> {
        let contents = std::fs::read_to_string(path)?;
        let parsed: Self = serde_json::from_str(&contents)?;
        parsed.validate()?;
        Ok(parsed)
    }

    #[must_use]
    pub fn with_cache_root(mut self, cache_root: impl Into<PathBuf>) -> Self {
        self.cache_root = Some(cache_root.into());
        // A different cache root resolves to different executables, so nothing
        // learned under the previous one still applies.
        self.resolved = Arc::new(Mutex::new(HashMap::new()));
        self
    }

    pub fn validate(&self) -> anyhow::Result<()> {
        anyhow::ensure!(
            self.manifest_version == 1,
            "unsupported adapter manifest version"
        );
        anyhow::ensure!(self.protocol == "ACP", "adapter manifest must use ACP");
        anyhow::ensure!(!self.adapters.is_empty(), "adapter manifest is empty");
        let mut keys = std::collections::BTreeSet::new();
        for adapter in &self.adapters {
            anyhow::ensure!(!adapter.key.trim().is_empty(), "adapter key is empty");
            anyhow::ensure!(
                keys.insert(&adapter.key),
                "duplicate adapter {}",
                adapter.key
            );
            anyhow::ensure!(
                adapter.distribution == "native" || adapter.distribution.contains('@'),
                "adapter {} distribution is not pinned",
                adapter.key
            );
            anyhow::ensure!(
                !adapter.args.iter().any(|argument| argument == "latest"),
                "adapter {} uses an unpinned latest version",
                adapter.key
            );
            if adapter.distribution.starts_with("npm:") {
                anyhow::ensure!(
                    adapter
                        .artifact_integrity
                        .as_deref()
                        .is_some_and(|value| value.starts_with("sha512-")),
                    "npm adapter {} is missing its registry SRI digest",
                    adapter.key
                );
            }
        }
        Ok(())
    }

    #[must_use]
    pub fn content_digest(&self) -> String {
        let canonical = serde_json::to_vec(self).expect("manifest serialization is infallible");
        hex::encode(Sha256::digest(canonical))
    }

    pub fn resolve(&self, key: &str) -> anyhow::Result<ResolvedAdapter> {
        if let Some(cached) = self
            .resolved
            .lock()
            .expect("adapter cache poisoned")
            .get(key)
        {
            return Ok(cached.clone());
        }
        let resolved = self.resolve_uncached(key)?;
        self.resolved
            .lock()
            .expect("adapter cache poisoned")
            .insert(key.to_owned(), resolved.clone());
        Ok(resolved)
    }

    pub(crate) fn resolve_uncached(&self, key: &str) -> anyhow::Result<ResolvedAdapter> {
        let spec = self
            .adapters
            .iter()
            .find(|adapter| adapter.key == key)
            .cloned()
            .ok_or_else(|| anyhow::anyhow!("unknown adapter {key}"))?;
        let command = if spec.distribution.starts_with("npm:") {
            let cache_root = self
                .cache_root
                .as_ref()
                .ok_or_else(|| anyhow::anyhow!("adapter cache is not configured"))?;
            let command = cached_adapter_executable(cache_root, &spec);
            verify_cached_adapter(&command)?;
            // After the cache is known good and before anything launches it.
            // A cache installed under a Node that has since been replaced is
            // the same problem as one installed under a Node that never
            // qualified, and this is the one place both pass through.
            ensure_adapter_node_runs(&spec, cache_root)?;
            command
        } else {
            resolve_executable(&spec.command).ok_or_else(|| {
                anyhow::anyhow!("adapter executable {} was not found", spec.command)
            })?
        };
        let upstream_command = resolve_executable(&spec.upstream_command)
            .ok_or_else(|| anyhow::anyhow!("{} executable was not found", spec.upstream_command))?;
        let probed = probe_version(&upstream_command, &spec.upstream_version_args);
        let upstream_version = probed.clone().ok();
        if let Some(minimum) = spec.minimum_upstream_version.as_deref() {
            let installed = probed.map_err(|reason| match reason {
                // Says what actually happened, so nobody goes looking at their
                // agent's version over a busy machine. The marker is what tells
                // the refresh loop this one is worth trying again shortly.
                VersionUnknown::TimedOut => anyhow::anyhow!(
                    "{} did not answer `{}` within {}s{TRANSIENT_MARKER}",
                    spec.display_name,
                    spec.upstream_version_args.join(" "),
                    VERSION_PROBE_TIMEOUT.as_secs(),
                ),
                VersionUnknown::Failed => anyhow::anyhow!(
                    "{} version could not be determined (minimum {minimum})",
                    spec.display_name
                ),
            })?;
            anyhow::ensure!(
                version_is_at_least(&installed, minimum),
                "{} {} is unsupported; version {minimum} or newer is required",
                spec.display_name,
                installed
            );
        }
        Ok(ResolvedAdapter {
            spec,
            command,
            upstream_command,
            upstream_version,
        })
    }

    /// Why warming this adapter last failed, if it did.
    pub(crate) fn install_failure(&self, key: &str) -> Option<String> {
        self.install_failures
            .lock()
            .expect("install failures")
            .get(key)
            .cloned()
    }

    /// A cheap answer to "have the agents on this machine changed?".
    ///
    /// Detection and probing were the same operation, so noticing a newly
    /// installed Claude Code meant spawning every agent -- far too expensive to
    /// do often, which is why it ran on a fifteen-minute timer and why
    /// installing an agent could take a quarter of an hour to show up.
    ///
    /// This separates them. Resolving four commands is a handful of `stat`
    /// calls against directories already being searched; it can run every couple
    /// of seconds without noticeable cost, and only a *change* pays for a probe.
    /// Size and mtime are included so an in-place upgrade counts as a change,
    /// not just an install or an uninstall.
    #[must_use]
    pub fn installed_fingerprint(&self) -> String {
        let mut digest = Sha256::new();
        for adapter in &self.adapters {
            digest.update(adapter.key.as_bytes());
            if let Some(failure) = self.install_failure(&adapter.key) {
                digest.update(failure.as_bytes());
            }
            fingerprint_path(&mut digest, resolve_executable(&adapter.upstream_command));
            // The adapter cache counts too, and leaving it out was a bug with a
            // very visible symptom: warming the cache in the background means a
            // probe can run before it lands, so the harness publishes as
            // `Installing` -- correctly -- and then the install finishes and
            // *nothing has changed* as far as a fingerprint watching only the
            // agent binaries is concerned. It sat at "Installing" until the
            // fifteen-minute sweep, which is the exact wait this was meant to
            // remove.
            if adapter.distribution.starts_with("npm:")
                && let Some(cache_root) = self.cache_root.as_ref()
            {
                let executable = cached_adapter_executable(cache_root, adapter);
                fingerprint_path(&mut digest, executable.is_file().then_some(executable));
            }
        }
        hex::encode(digest.finalize())
    }

    /// Every certified adapter, resolved against this machine.
    ///
    /// Logged per adapter, because a GUI-launched app inherits
    /// `/usr/bin:/bin:/usr/sbin:/sbin` and never a login shell's `PATH` -- so
    /// the well-known-directory search *is* detection here, and the cost of it
    /// missing a directory is an agent the user can see installed that Lemma
    /// insists does not exist. Which path answered, or why none did, is the
    /// first thing worth knowing about that and was previously written nowhere.
    ///
    /// One thread per adapter, because resolving one means *spawning* it.
    ///
    /// `probe_version` waits up to `VERSION_PROBE_TIMEOUT` for an agent to
    /// answer -- thirty seconds, and its own doc says why five was not enough
    /// -- and an agent that is not installed spends the whole budget. In
    /// sequence that was four timeouts end to end before the list could say
    /// anything; concurrently
    /// the slowest adapter sets the floor and the rest are free.
    ///
    /// This is also the step that raises the macOS file-access prompt, since it
    /// is the first time an agent's own binary runs. Overlapping the probes
    /// brings that prompt forward for every agent at once rather than staggering
    /// it behind whichever one is slowest to answer.
    #[must_use]
    pub fn discover(&self) -> Vec<HarnessSnapshot> {
        std::thread::scope(|scope| {
            let handles: Vec<_> = self
                .adapters
                .iter()
                .map(|adapter| scope.spawn(move || self.snapshot_for(adapter)))
                .collect();
            handles
                .into_iter()
                .zip(&self.adapters)
                .map(|(handle, adapter)| {
                    handle.join().unwrap_or_else(|_| {
                        snapshot_unavailable(adapter, "adapter probe thread panicked")
                    })
                })
                .collect()
        })
    }

    pub(crate) fn snapshot_for(&self, adapter: &AdapterSpec) -> HarnessSnapshot {
        // "Not there yet" is not "broken". Warming the cache in the background
        // means discovery can now run while an adapter is still downloading, and
        // resolving one that has not landed fails exactly like an agent that
        // cannot start -- which would report a five-minute npm install as
        // "Agent Host could not start this agent. Check the log."
        //
        // A *missing* cache directory is the install still running; a cache that
        // exists and fails verification is a real integrity failure and keeps
        // reporting as one.
        //
        // Unless warming has already tried and failed, which is the case this
        // state could not previously express. `install_cache` is detached and
        // best-effort — a machine with no npm must still serve the agents it
        // already has — so its failure used to be a log line and nothing else,
        // and a missing cache looked identical whether the download was in
        // flight or impossible. A user behind a proxy that blocks the registry
        // was told "Setting up, usually under a minute" for as long as the app
        // stayed open. Say what happened instead; it names a cause they can act
        // on and `doctor --repair` is the retry.
        if adapter.distribution.starts_with("npm:")
            && let Some(cache_root) = self.cache_root.as_ref()
            && !cached_adapter_directory(cache_root, adapter).exists()
        {
            if let Some(failure) = self.install_failure(&adapter.key) {
                tracing::warn!(
                    harness = %adapter.key,
                    error = %failure,
                    "adapter could not be installed"
                );
                return snapshot_unavailable(
                    adapter,
                    &format!("Lemma could not install this agent's adapter: {failure}"),
                );
            }
            tracing::info!(harness = %adapter.key, "adapter is still installing");
            return snapshot_installing(adapter);
        }
        match self.resolve(&adapter.key) {
            Ok(resolved) => {
                tracing::info!(
                    harness = %adapter.key,
                    command = %resolved.command.display(),
                    upstream = %resolved.upstream_command.display(),
                    version = resolved.upstream_version.as_deref().unwrap_or("unknown"),
                    "adapter resolved"
                );
                snapshot_ready(&resolved)
            }
            Err(error) => {
                let reason = error.to_string();
                tracing::info!(
                    harness = %adapter.key,
                    error = %reason_without_marker(&reason),
                    transient = reason_is_transient(&reason),
                    "adapter not available on this computer"
                );
                snapshot_unavailable(adapter, &reason)
            }
        }
    }
}
