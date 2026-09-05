//! Pinned ACP adapter manifest and local harness discovery.

use crate::NoConsoleWindow;
use std::collections::{BTreeMap, HashMap, HashSet};
use std::env;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use chrono::{Duration as ChronoDuration, Utc};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};

use crate::protocol::{ConfigOption, HarnessCapabilities, HarnessHealth, HarnessSnapshot};

const BUILTIN_MANIFEST: &str = include_str!("../agent-adapters.lock.json");

/// Appended to the reason a harness failed for something that may not fail twice.
///
/// Carried in the text because that is the only field surviving the trip from
/// `resolve` through a `HarnessSnapshot` to the poll loop that decides when to
/// try again. It is stripped before the reason is stored, so nobody reads it.
const TRANSIENT_MARKER: &str = " [transient]";
const SNAPSHOT_TTL: ChronoDuration = ChronoDuration::hours(24);

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct AdapterManifest {
    pub manifest_version: u16,
    pub manifest_id: String,
    pub protocol: String,
    pub adapters: Vec<AdapterSpec>,
    #[serde(skip)]
    cache_root: Option<PathBuf>,
    /// Adapters already resolved by this process, shared across clones.
    ///
    /// Resolving is expensive and was being paid on every use: it hashes the
    /// whole npm package for the integrity check and execs the agent binary to
    /// read its version. Measured at 21.7s for four adapters, and `handle_start`
    /// paid a share of it on the poll loop before *every* run — which is where
    /// most of the per-message latency came from.
    ///
    /// Verifying once per process is the deliberate trade: an adapter swapped
    /// underneath a running host is no longer caught, but every restart
    /// re-verifies, and the host restarts often.
    #[serde(skip)]
    resolved: Arc<Mutex<HashMap<String, ResolvedAdapter>>>,
    /// Why the last attempt to warm the cache failed, per adapter, shared
    /// across clones.
    ///
    /// Warming moved off the pairing path and became a detached, best-effort
    /// thread, which is right — a machine with no npm must still serve the
    /// agents it already has. But it left nothing anywhere to distinguish "the
    /// download has not landed yet" from "the download cannot land", and both
    /// present as a missing cache directory. So a machine behind a proxy that
    /// blocks the registry reported *Setting up · usually under a minute*, and
    /// went on reporting it for as long as the app stayed open.
    #[serde(skip)]
    install_failures: Arc<Mutex<HashMap<String, String>>>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct AdapterSpec {
    pub key: String,
    pub display_name: String,
    pub adapter_version: String,
    pub command: String,
    #[serde(default)]
    pub args: Vec<String>,
    pub upstream_command: String,
    #[serde(default)]
    pub upstream_version_args: Vec<String>,
    /// The environment variable this adapter reads to be told which upstream
    /// agent binary to run.
    ///
    /// Without it an adapter picks its own, and the two certified ones pick
    /// differently: `codex-acp` falls back to the bare name `codex` and so
    /// resolves through `PATH`, while `claude-agent-acp` falls back to a copy
    /// vendored inside its own `node_modules` and never consults `PATH` at all.
    /// That second case is why Lemma could report the version of the Claude Code
    /// on this machine and then run a different one.
    #[serde(default)]
    pub upstream_path_env: Option<String>,
    /// Whether to install this adapter without its optional dependencies.
    ///
    /// True for both certified adapters, whose optional dependencies are
    /// per-platform *vendored builds of the agents themselves* — a 259 MB
    /// `codex` and a 245 MB `claude`. They reach the real agent through
    /// `upstream_path_env` instead, so those copies were 548 MB fetched to run
    /// code the host never invokes.
    ///
    /// Per adapter rather than always, because `--omit=optional` is not
    /// generally safe: platform-specific native binaries are conventionally
    /// declared as optional dependencies, and omitting those breaks the package
    /// rather than slimming it. Every adapter that sets this has to be one
    /// someone checked.
    #[serde(default)]
    pub omit_optional_dependencies: bool,
    pub minimum_upstream_version: Option<String>,
    pub distribution: String,
    pub artifact_integrity: Option<String>,
    pub license: String,
}

#[derive(Clone, Debug)]
pub struct ResolvedAdapter {
    pub spec: AdapterSpec,
    pub command: PathBuf,
    pub upstream_command: PathBuf,
    pub upstream_version: Option<String>,
}

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

    fn resolve_uncached(&self, key: &str) -> anyhow::Result<ResolvedAdapter> {
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

    /// Ensure every npm-distributed adapter is present and verified.
    ///
    /// One thread per adapter, because the expensive half of each is a
    /// whole-tree SHA-256 and running those in sequence made the wall-clock
    /// their sum -- which is most of what "it takes minutes the first time" was.
    ///
    /// The hashing is deliberately *not* cheapened. It is what stands between a
    /// tampered `node_modules` and a process that runs with the user's own
    /// credentials and file access, and the honest way to make it faster is to
    /// overlap it, not to check less of it.
    ///
    /// Each thread's outcome is recorded per adapter, so discovery can tell an
    /// install that has not finished from one that cannot.
    pub fn install_cache(&self, cache_root: &Path, repair: bool) -> anyhow::Result<()> {
        std::fs::create_dir_all(cache_root)?;
        let pending: Vec<&AdapterSpec> = self
            .adapters
            .iter()
            .filter(|spec| spec.distribution.starts_with("npm:"))
            .collect();
        if pending.is_empty() {
            return Ok(());
        }
        // `npm install` itself is serialized across adapters: they share one
        // `~/.npm/_cacache`, and two npm processes writing it concurrently is a
        // known source of `EEXIST`/`ENOTEMPTY` on exactly the cold cache this
        // runs against. The parallelism that mattered was the hashing, which
        // touches only its own staging directory and still overlaps.
        let registry = Mutex::new(());
        let results: Vec<(String, anyhow::Result<()>)> = std::thread::scope(|scope| {
            let handles: Vec<_> = pending
                .iter()
                .map(|spec| {
                    let registry = &registry;
                    scope.spawn(move || {
                        (
                            spec.key.clone(),
                            install_cached_adapter(spec, cache_root, repair, registry),
                        )
                    })
                })
                .collect();
            handles
                .into_iter()
                .map(|handle| {
                    handle.join().unwrap_or_else(|_| {
                        (
                            String::new(),
                            Err(anyhow::anyhow!("adapter install thread panicked")),
                        )
                    })
                })
                .collect()
        });
        // Recorded and reported only after every thread has been joined, so an
        // early return cannot leave one still writing into the cache.
        let mut first_failure = None;
        {
            let mut failures = self.install_failures.lock().expect("install failures");
            for (key, result) in results {
                match result {
                    Ok(()) => {
                        failures.remove(&key);
                    }
                    Err(error) => {
                        failures.insert(key, error.to_string());
                        first_failure.get_or_insert(error);
                    }
                }
            }
        }
        first_failure.map_or(Ok(()), Err)
    }

    /// Why warming this adapter last failed, if it did.
    fn install_failure(&self, key: &str) -> Option<String> {
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
    /// `probe_version` waits up to five seconds for an agent to answer, and an
    /// agent that is not installed spends the whole five. In sequence that was
    /// four timeouts end to end before the list could say anything; concurrently
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

    fn snapshot_for(&self, adapter: &AdapterSpec) -> HarnessSnapshot {
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

/// Fold one resolved path — or its absence — into a fingerprint.
///
/// Size and mtime are included so an upgrade in place counts as a change, not
/// only an install or an uninstall.
fn fingerprint_path(digest: &mut Sha256, path: Option<PathBuf>) {
    let Some(path) = path else {
        digest.update(b"absent");
        return;
    };
    digest.update(path.as_os_str().as_encoded_bytes());
    if let Ok(metadata) = std::fs::metadata(&path) {
        digest.update(metadata.len().to_le_bytes());
        if let Ok(modified) = metadata.modified()
            && let Ok(since) = modified.duration_since(std::time::UNIX_EPOCH)
        {
            digest.update(since.as_secs().to_le_bytes());
        }
    }
}

/// Install and verify one npm adapter into the cache. Runs on its own thread.
///
/// `registry` serializes the `npm install` step only; the hashing either side of
/// it overlaps with every other adapter's.
fn install_cached_adapter(
    spec: &AdapterSpec,
    cache_root: &Path,
    repair: bool,
    registry: &Mutex<()>,
) -> anyhow::Result<()> {
    let destination = cached_adapter_directory(cache_root, spec);
    let executable = cached_adapter_executable(cache_root, spec);
    if verify_cached_adapter(&executable).is_ok() {
        return Ok(());
    }
    if destination.exists() && !repair {
        anyhow::bail!(
            "cached adapter {} failed integrity validation; run doctor --repair",
            spec.key
        );
    }
    let staging = cache_root.join(format!(".{}.{}.tmp", spec.key, uuid::Uuid::new_v4()));
    if staging.exists() {
        std::fs::remove_dir_all(&staging)?;
    }
    let staged = (|| -> anyhow::Result<()> {
        {
            let _one_npm_at_a_time = registry.lock().unwrap_or_else(|poisoned| {
                // A panicking install has nothing to corrupt here: the guard
                // protects a shared npm cache, not any state of ours.
                poisoned.into_inner()
            });
            install_npm_adapter(spec, &staging)?;
        }
        let staged_executable = platform_cached_executable(&staging, &spec.command);
        anyhow::ensure!(
            staged_executable.is_file(),
            "installed adapter {} did not provide executable {}",
            spec.key,
            spec.command
        );
        let digest = directory_sha256(&staging)?;
        std::fs::write(staging.join(".lemma-cache.sha256"), &digest)?;
        verify_cached_adapter(&staged_executable)
    })();
    if let Err(error) = staged {
        let _ = std::fs::remove_dir_all(&staging);
        return Err(error);
    }
    if let Some(parent) = destination.parent() {
        std::fs::create_dir_all(parent)?;
    }
    activate_staged_cache(&staging, &destination)?;
    verify_cached_adapter(&executable)
}

fn cached_adapter_directory(cache_root: &Path, spec: &AdapterSpec) -> PathBuf {
    cache_root.join(&spec.key).join(&spec.adapter_version)
}

fn cached_adapter_executable(cache_root: &Path, spec: &AdapterSpec) -> PathBuf {
    platform_cached_executable(&cached_adapter_directory(cache_root, spec), &spec.command)
}

fn platform_cached_executable(root: &Path, command: &str) -> PathBuf {
    let executable = root.join("node_modules").join(".bin").join(command);
    // npm writes a .cmd shim on Windows and a symlink everywhere else.
    #[cfg(windows)]
    let executable = executable.with_extension("cmd");
    executable
}

fn verify_cached_adapter(executable: &Path) -> anyhow::Result<()> {
    anyhow::ensure!(
        executable.is_file(),
        "verified adapter cache is missing {}",
        executable.display()
    );
    let root = executable
        .parent()
        .and_then(Path::parent)
        .and_then(Path::parent)
        .ok_or_else(|| anyhow::anyhow!("invalid adapter cache path"))?;
    let expected = std::fs::read_to_string(root.join(".lemma-cache.sha256"))?;
    anyhow::ensure!(
        directory_sha256(root)? == expected.trim(),
        "cached adapter cache failed integrity validation"
    );
    Ok(())
}

fn directory_sha256(root: &Path) -> anyhow::Result<String> {
    fn collect(directory: &Path, entries: &mut Vec<PathBuf>) -> anyhow::Result<()> {
        for entry in std::fs::read_dir(directory)? {
            let path = entry?.path();
            if path
                .file_name()
                .is_some_and(|name| name == ".lemma-cache.sha256")
            {
                continue;
            }
            entries.push(path.clone());
            if std::fs::symlink_metadata(&path)?.is_dir() {
                collect(&path, entries)?;
            }
        }
        Ok(())
    }

    let mut entries = Vec::new();
    collect(root, &mut entries)?;
    entries.sort();
    let mut digest = Sha256::new();
    for path in entries {
        let relative = path.strip_prefix(root)?;
        digest.update(relative.to_string_lossy().as_bytes());
        digest.update([0]);
        let metadata = std::fs::symlink_metadata(&path)?;
        if metadata.file_type().is_symlink() {
            digest.update(b"link");
            digest.update(std::fs::read_link(&path)?.to_string_lossy().as_bytes());
        } else if metadata.is_dir() {
            digest.update(b"directory");
        } else if metadata.is_file() {
            digest.update(b"file");
            digest.update(std::fs::read(&path)?);
        }
        digest.update([0]);
    }
    Ok(hex::encode(digest.finalize()))
}

fn activate_staged_cache(staging: &Path, destination: &Path) -> anyhow::Result<()> {
    if !destination.exists() {
        std::fs::rename(staging, destination)?;
        return Ok(());
    }
    let backup = destination.with_extension(format!("backup-{}", uuid::Uuid::new_v4()));
    std::fs::rename(destination, &backup)?;
    if let Err(error) = std::fs::rename(staging, destination) {
        let _ = std::fs::rename(&backup, destination);
        return Err(error.into());
    }
    let _ = std::fs::remove_dir_all(backup);
    Ok(())
}

fn install_npm_adapter(spec: &AdapterSpec, staging: &Path) -> anyhow::Result<()> {
    let package = spec
        .distribution
        .strip_prefix("npm:")
        .ok_or_else(|| anyhow::anyhow!("invalid npm distribution"))?;
    let npm = resolve_executable("npm")
        .ok_or_else(|| anyhow::anyhow!("npm is required to install ACP adapters"))?;
    std::fs::create_dir_all(staging)?;
    let mut command = Command::new(npm);
    command
        .no_console_window()
        .args(["install", "--ignore-scripts", "--no-audit", "--no-fund"]);
    // Lemma exists to drive the agent the user already has, holding the user's
    // own credentials and configuration. Downloading a second copy contradicts
    // that even when it works, and it was most of why a first run took minutes.
    //
    // Declared per adapter rather than passed unconditionally: see
    // `omit_optional_dependencies`. Omitting optional dependencies is safe for
    // an adapter whose optional dependencies are a whole vendored agent, and
    // breaks one whose optional dependencies are its platform's native binary.
    if spec.omit_optional_dependencies {
        command.arg("--omit=optional");
    }
    let status = command
        .args(["--package-lock=true", "--prefix"])
        .arg(staging)
        .arg(package)
        .stdin(Stdio::null())
        .status()?;
    anyhow::ensure!(status.success(), "npm adapter installation failed");

    let package_name = package
        .rsplit_once('@')
        .map(|(name, _)| name)
        .filter(|name| !name.is_empty())
        .ok_or_else(|| anyhow::anyhow!("npm adapter distribution is not version-pinned"))?;
    let lock: Value = serde_json::from_slice(&std::fs::read(staging.join("package-lock.json"))?)?;
    let packages = lock
        .get("packages")
        .and_then(Value::as_object)
        .ok_or_else(|| anyhow::anyhow!("npm lock did not contain package records"))?;
    let package_suffix = format!("/node_modules/{package_name}");
    let installed = packages
        .iter()
        .find(|(path, _)| {
            path.as_str() == format!("node_modules/{package_name}")
                || path.ends_with(&package_suffix)
        })
        .map(|(_, entry)| entry)
        .ok_or_else(|| anyhow::anyhow!("npm lock did not contain the pinned adapter"))?;
    let actual_integrity = installed.get("integrity").and_then(Value::as_str);
    let actual_version = installed.get("version").and_then(Value::as_str);
    anyhow::ensure!(
        actual_integrity == spec.artifact_integrity.as_deref()
            && actual_version == Some(spec.adapter_version.as_str()),
        "npm registry integrity did not match the pinned adapter lock"
    );
    Ok(())
}

impl ResolvedAdapter {
    #[must_use]
    pub fn args(&self) -> Vec<String> {
        self.spec.args.clone()
    }

    #[must_use]
    pub fn environment(&self) -> BTreeMap<String, String> {
        let mut environment = BTreeMap::new();
        let mut paths = Vec::new();
        let mut seen = HashSet::new();
        for executable in [&self.command, &self.upstream_command] {
            if let Some(parent) = executable.parent() {
                push_unique(&mut paths, &mut seen, parent.to_path_buf());
            }
        }
        for path in executable_search_paths() {
            push_unique(&mut paths, &mut seen, path);
        }
        if let Ok(joined) = env::join_paths(paths) {
            environment.insert("PATH".to_owned(), joined.to_string_lossy().into_owned());
        }
        // Name the upstream binary outright rather than hoping `PATH` order
        // decides it.
        //
        // Prepending the agent's directory above is necessary but not
        // sufficient: an adapter that resolves its agent through `require`
        // rather than `PATH` never sees it, which is exactly what
        // `claude-agent-acp` does. So Lemma probed the version of the agent on
        // this machine, published it, and then ran a vendored copy of a
        // different one -- carrying none of the user's own configuration, which
        // is the entire premise of running agents locally.
        if let Some(variable) = self.spec.upstream_path_env.as_deref() {
            environment.insert(
                variable.to_owned(),
                self.upstream_command.to_string_lossy().into_owned(),
            );
        }
        environment
    }
}

fn snapshot_ready(adapter: &ResolvedAdapter) -> HarnessSnapshot {
    let now = Utc::now();
    let mut snapshot = HarnessSnapshot {
        harness_key: adapter.spec.key.clone(),
        display_name: adapter.spec.display_name.clone(),
        adapter_version: adapter.spec.adapter_version.clone(),
        upstream_version: adapter.upstream_version.clone(),
        health: HarnessHealth::Ready,
        capabilities: HarnessCapabilities {
            plans: true,
            usage: true,
            ..HarnessCapabilities::default()
        },
        // Replaced immediately below, and again by the probe once it lands.
        // `HarnessSnapshot::revision` reads the whole snapshot, so it cannot be
        // computed before there is one.
        config_revision: String::new(),
        config_options: Vec::<ConfigOption>::new(),
        stale_after: now + SNAPSHOT_TTL,
        stale_reason: None,
    };
    snapshot.config_revision = snapshot.revision();
    snapshot
}

/// An adapter whose cache is still being fetched.
///
/// `HarnessHealth::Installing` and the copy for it both already existed; nothing
/// ever produced it, because installing used to finish before anything could
/// look. It goes stale quickly on purpose -- the install is expected to land in
/// the next minute or two, and the point of the state is that it changes.
fn snapshot_installing(spec: &AdapterSpec) -> HarnessSnapshot {
    let now = Utc::now();
    HarnessSnapshot {
        harness_key: spec.key.clone(),
        display_name: spec.display_name.clone(),
        adapter_version: spec.adapter_version.clone(),
        upstream_version: None,
        health: HarnessHealth::Installing,
        capabilities: HarnessCapabilities::default(),
        config_revision: hex::encode(Sha256::digest(b"installing")),
        config_options: Vec::new(),
        stale_after: now + ChronoDuration::seconds(30),
        // No reason. `stale_reason` is rendered underneath the health copy, and
        // the health already *is* "Installing" with a sentence to match — so
        // saying it again just put two lines of the same thing on one row.
        // It carries a reason when the state alone does not explain itself.
        stale_reason: None,
    }
}

fn snapshot_unavailable(spec: &AdapterSpec, reason: &str) -> HarnessSnapshot {
    let now = Utc::now();
    HarnessSnapshot {
        harness_key: spec.key.clone(),
        display_name: spec.display_name.clone(),
        adapter_version: spec.adapter_version.clone(),
        upstream_version: None,
        health: HarnessHealth::ProbeFailed,
        capabilities: HarnessCapabilities::default(),
        config_revision: hex::encode(Sha256::digest(reason.as_bytes())),
        config_options: Vec::new(),
        stale_after: now + ChronoDuration::minutes(5),
        stale_reason: Some(reason.to_owned()),
    }
}

/// Whether this reason describes a moment rather than an installation.
///
/// Read by the poll loop to choose between trying again in seconds and waiting
/// out the ordinary refresh. An agent that is simply not installed must answer
/// `false`, or the host re-probes it forever: on a machine without Cursor that
/// is every refresh for the life of the process.
#[must_use]
pub fn reason_is_transient(reason: &str) -> bool {
    reason.ends_with(TRANSIENT_MARKER)
}

/// The reason with the marker taken off, for anywhere a person will read it.
#[must_use]
pub fn reason_without_marker(reason: &str) -> &str {
    reason.trim_end_matches(TRANSIENT_MARKER)
}

/// How long an agent gets to answer `--version`.
///
/// A ceiling on a hang, not a measurement of anything. The command itself takes
/// milliseconds warm and a couple of seconds cold -- but this runs while the
/// adapter cache is being downloaded and hashed and the other agents are being
/// probed, because landing that cache is exactly what triggers the re-probe.
///
/// At five seconds that was a coin toss on first launch after an update, and
/// losing it published Claude Code as unusable with a message blaming the user's
/// install. Nothing is slower for the larger budget: a healthy agent answers and
/// the loop exits, so this is only ever reached by one that never will.
const VERSION_PROBE_TIMEOUT: Duration = Duration::from_secs(30);
const VERSION_PROBE_POLL: Duration = Duration::from_millis(25);

/// Why an agent's version is unknown.
///
/// The distinction is the whole point. "Did not answer in time" is about this
/// machine at this moment and is worth retrying in seconds; "would not run" is
/// about the installation and is not. They used to be the same `None`, so a busy
/// laptop and a broken agent produced the same sentence and the same
/// quarter-hour wait.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum VersionUnknown {
    TimedOut,
    Failed,
}

fn probe_version(executable: &Path, arguments: &[String]) -> Result<String, VersionUnknown> {
    probe_version_within(executable, arguments, VERSION_PROBE_TIMEOUT)
}

fn probe_version_within(
    executable: &Path,
    arguments: &[String],
    timeout: Duration,
) -> Result<String, VersionUnknown> {
    let mut command = Command::new(executable);
    command
        .no_console_window()
        .args(arguments)
        .stdin(Stdio::null())
        .stderr(Stdio::piped())
        .stdout(Stdio::piped());
    let mut child = command.spawn().map_err(|_| VersionUnknown::Failed)?;
    let started = std::time::Instant::now();
    loop {
        if started.elapsed() > timeout {
            // Killed, not abandoned. Dropping a `Child` neither reaps nor stops
            // it, so every timeout used to leave the agent running against a
            // question nobody was waiting for an answer to any more.
            let _ = child.kill();
            let _ = child.wait();
            return Err(VersionUnknown::TimedOut);
        }
        match child.try_wait() {
            Ok(Some(status)) if status.success() => {
                let output = child
                    .wait_with_output()
                    .map_err(|_| VersionUnknown::Failed)?;
                let stdout = String::from_utf8_lossy(&output.stdout);
                let stderr = String::from_utf8_lossy(&output.stderr);
                let text = if stdout.trim().is_empty() {
                    stderr.trim().to_owned()
                } else {
                    stdout.trim().to_owned()
                };
                return (!text.is_empty())
                    .then_some(text)
                    .ok_or(VersionUnknown::Failed);
            }
            Ok(Some(_)) | Err(_) => return Err(VersionUnknown::Failed),
            Ok(None) => std::thread::sleep(VERSION_PROBE_POLL),
        }
    }
}

fn version_is_at_least(installed: &str, minimum: &str) -> bool {
    fn find_version(value: &str) -> Option<[u64; 3]> {
        value.split_whitespace().find_map(|token| {
            let token = token.trim_start_matches(|character: char| !character.is_ascii_digit());
            let token = token
                .split_once('-')
                .map_or(token, |(version, _)| version)
                .trim_end_matches(|character: char| {
                    !character.is_ascii_digit() && character != '.'
                });
            let mut parts = token.split('.');
            Some([
                parts.next()?.parse().ok()?,
                parts.next()?.parse().ok()?,
                parts.next()?.parse().ok()?,
            ])
        })
    }

    find_version(installed)
        .zip(find_version(minimum))
        .is_some_and(|(installed, minimum)| installed >= minimum)
}

fn resolve_executable(command: &str) -> Option<PathBuf> {
    let candidate = Path::new(command);
    if candidate.components().count() > 1 {
        return candidate.is_file().then(|| candidate.to_path_buf());
    }
    resolve_executable_in(command, executable_search_paths())
}

fn resolve_executable_in(
    command: &str,
    search_paths: impl IntoIterator<Item = PathBuf>,
) -> Option<PathBuf> {
    for directory in search_paths {
        // Windows first. Node installs an extension-less `npm` shell script
        // beside `npm.cmd`, and CreateProcess cannot run it: the adapter cache
        // warm-up failed with "%1 is not a valid Win32 application" (os error
        // 193) because the bare name matched before the launcher did. The same
        // shape applies to any agent shipping a POSIX shim next to its .cmd.
        #[cfg(windows)]
        for extension in ["exe", "cmd", "bat"] {
            let candidate = directory.join(format!("{command}.{extension}"));
            if candidate.is_file() {
                return Some(candidate);
            }
        }
        let candidate = directory.join(command);
        if candidate.is_file() {
            return Some(candidate);
        }
    }
    None
}

fn executable_search_paths() -> Vec<PathBuf> {
    let mut paths = Vec::new();
    let mut seen = HashSet::new();
    for variable in ["LEMMA_AGENT_HOST_PATH", "PATH"] {
        if let Some(value) = env::var_os(variable) {
            for path in env::split_paths(&value) {
                push_unique(&mut paths, &mut seen, path);
            }
        }
    }

    let home = env::var_os("HOME")
        .or_else(|| env::var_os("USERPROFILE"))
        .map(PathBuf::from);
    if let Some(home) = home.as_ref() {
        for directory in home_executable_directories(home) {
            push_unique(&mut paths, &mut seen, directory);
        }
        for path in nvm_node_bins(home) {
            push_unique(&mut paths, &mut seen, path);
        }
    }

    #[cfg(unix)]
    for path in ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"] {
        push_unique(&mut paths, &mut seen, PathBuf::from(path));
    }
    paths
}

/// Where an agent lives when it is installed under a user's home directory.
///
/// Two kinds of directory. Most are toolchain and package-manager `bin` paths —
/// where an agent lands when installed *by* something. The rest are what these
/// agents' own installers create, which no package manager knows about:
/// `OpenCode`'s script writes to `~/.opencode/bin` and Claude Code's local
/// install uses `~/.claude/local`.
///
/// This list is the whole of detection in practice. The Agent Host is a sidecar
/// of a GUI app, so it inherits `/usr/bin:/bin:/usr/sbin:/sbin` and nothing
/// else — never a login shell's `PATH` — and none of these are on it.
fn home_executable_directories(home: &Path) -> Vec<PathBuf> {
    // The platform's own additions are a cfg-selected list rather than
    // cfg-gated pushes. Pushing needs a `mut` binding, and on a platform that
    // matches neither arm — Linux, which is what CI lints on — every mutation
    // compiles out and the `mut` becomes an `unused_mut` the lint gate treats
    // as an error. Selecting the list instead leaves nothing to be unused.
    #[cfg(target_os = "macos")]
    let platform: &[&str] = &["Library/pnpm"];
    #[cfg(windows)]
    let platform: &[&str] = &["AppData/Roaming/npm"];
    #[cfg(not(any(target_os = "macos", windows)))]
    let platform: &[&str] = &[];

    [
        ".local/bin",
        ".cargo/bin",
        ".volta/bin",
        ".asdf/shims",
        ".local/share/mise/shims",
        ".local/share/pnpm",
        ".bun/bin",
        ".npm-global/bin",
        ".opencode/bin",
        ".claude/local",
        ".codex/bin",
    ]
    .iter()
    .chain(platform)
    .map(|relative| home.join(relative))
    .collect()
}

fn nvm_node_bins(home: &Path) -> Vec<PathBuf> {
    let root = home.join(".nvm/versions/node");
    let Ok(entries) = std::fs::read_dir(root) else {
        return Vec::new();
    };
    let mut versions = entries
        .filter_map(Result::ok)
        .filter_map(|entry| {
            let version = entry.file_name();
            let version = version.to_str()?.trim_start_matches('v');
            semver::Version::parse(version)
                .ok()
                .map(|version| (version, entry.path().join("bin")))
        })
        .collect::<Vec<_>>();
    versions.sort_unstable_by(|left, right| right.0.cmp(&left.0));
    versions.into_iter().map(|(_, path)| path).collect()
}

fn push_unique(paths: &mut Vec<PathBuf>, seen: &mut HashSet<PathBuf>, path: PathBuf) {
    if seen.insert(path.clone()) {
        paths.push(path);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A script that reports it started, waits, and reports it finished.
    ///
    /// The second marker is the one that matters: it only ever appears if the
    /// process outlived the probe that gave up on it.
    #[cfg(unix)]
    fn slow_agent(directory: &Path, seconds: u32) -> PathBuf {
        use std::os::unix::fs::PermissionsExt;
        let executable = directory.join("slow-agent");
        std::fs::write(
            &executable,
            format!(
                "#!/bin/sh\ntouch '{0}/started'\nsleep {seconds}\ntouch '{0}/finished'\n",
                directory.display()
            ),
        )
        .unwrap();
        std::fs::set_permissions(&executable, std::fs::Permissions::from_mode(0o700)).unwrap();
        executable
    }

    #[test]
    #[cfg(unix)]
    fn an_agent_that_does_not_answer_in_time_is_reported_as_a_timeout() {
        // These were the same `None`, so a busy laptop and a broken install
        // produced one sentence -- "version could not be determined (minimum
        // 2.1.0)" -- which names the user's agent for something the host did to
        // itself. It sent us reading Claude Code release notes over a probe that
        // simply ran while the adapter cache was still being hashed.
        let directory = tempfile::tempdir().unwrap();
        let executable = slow_agent(directory.path(), 30);

        let outcome = probe_version_within(&executable, &[], Duration::from_millis(200));

        assert_eq!(outcome, Err(VersionUnknown::TimedOut));
    }

    #[test]
    #[cfg(unix)]
    fn giving_up_on_a_probe_does_not_leave_the_agent_running() {
        // `return None` dropped the `Child`, and dropping neither reaps nor
        // kills -- so every timeout left an agent running against a question
        // nobody was waiting for an answer to.
        // Three spans, sized off one budget so they cannot eat into each other:
        // the script sleeps twice the budget, so the timeout is always what ends
        // it, and the wait afterwards is a second longer than the sleep the
        // script had left -- which is the window a kill that did not land shows
        // up in.
        //
        // Retried, because one span is not ours to size: the budget also has to
        // cover forking a shell, and under enough load it does not. That is a
        // trial with nothing in it -- the stand-in was killed before its first
        // line, so neither outcome is evidence -- and it used to be an outright
        // failure on `the stand-in agent never ran`. Measured at roughly one run
        // in forty on a saturated machine, which is exactly the rate this was
        // costing CI. The budget grows per attempt, so a slow machine converges
        // on one it can meet instead of retrying at a number it cannot.
        for attempt in 1u32..=4 {
            let directory = tempfile::tempdir().unwrap();
            let budget = Duration::from_millis(1500 * u64::from(attempt));
            let executable = slow_agent(directory.path(), 3 * attempt);

            let outcome = probe_version_within(&executable, &[], budget);
            assert_eq!(outcome, Err(VersionUnknown::TimedOut));

            // Checked, not assumed: "nothing finished" proves nothing about a
            // kill if the script never ran in the first place.
            if !directory.path().join("started").exists() {
                continue;
            }
            std::thread::sleep(budget + Duration::from_secs(1));
            assert!(
                !directory.path().join("finished").exists(),
                "the agent outlived the probe that gave up on it"
            );
            return;
        }
        panic!("the stand-in agent never ran, even given four times the budget");
    }

    #[test]
    fn only_a_probe_that_ran_out_of_time_is_worth_trying_again_soon() {
        // An agent that is not installed fails identically on every refresh. On
        // a machine without Cursor, treating that as worth retrying re-probes it
        // for the life of the process.
        let timed_out = "Claude Code did not answer `--version` within 30s [transient]";
        let missing = "adapter executable cursor-agent was not found";

        assert!(reason_is_transient(timed_out));
        assert!(!reason_is_transient(missing));
        // And the bookkeeping never reaches a reader.
        assert_eq!(
            reason_without_marker(timed_out),
            "Claude Code did not answer `--version` within 30s"
        );
        assert_eq!(reason_without_marker(missing), missing);
    }

    #[test]
    fn an_unreachable_agent_is_not_described_as_a_slow_one() {
        // The published reason is what a person reads in the app, so the marker
        // has to be gone by the time a snapshot carries it.
        let manifest = AdapterManifest::builtin().unwrap();
        let spec = manifest.adapters[0].clone();
        let snapshot = snapshot_unavailable(
            &spec,
            "Claude Code did not answer `--version` within 30s [transient]",
        );

        let reason = snapshot.stale_reason.expect("a reason");
        assert!(reason_is_transient(&reason), "the loop still needs to know");
        assert!(
            !reason_without_marker(&reason).contains("transient"),
            "but nobody should read the bookkeeping: {reason}"
        );
    }

    #[test]
    fn an_adapter_is_resolved_once_and_then_served_from_cache() {
        // Resolving hashes the whole npm package and execs the agent binary to
        // read its version. Paying that per run put ~5s on the poll loop before
        // every message, which was most of the latency users saw.
        let manifest = AdapterManifest::builtin().unwrap();
        let key = manifest.adapters[0].key.clone();

        // A non-npm adapter resolves straight off PATH; if this machine has no
        // such binary there is nothing to cache and nothing to assert.
        let Ok(first) = manifest.resolve(&key) else {
            return;
        };

        assert_eq!(
            manifest.resolved.lock().unwrap().len(),
            1,
            "resolving did not populate the cache"
        );

        let second = manifest.resolve(&key).unwrap();
        assert_eq!(first.command, second.command);
        assert_eq!(first.upstream_version, second.upstream_version);

        // Clones share the cache: the poll loop, the probe task and each run
        // task all hold their own clone of the manifest.
        let cloned = manifest.clone();
        assert_eq!(cloned.resolved.lock().unwrap().len(), 1);
    }

    #[test]
    fn changing_the_cache_root_discards_what_was_resolved_under_the_old_one() {
        let manifest = AdapterManifest::builtin().unwrap();
        let key = manifest.adapters[0].key.clone();
        if manifest.resolve(&key).is_err() {
            return;
        }
        assert_eq!(manifest.resolved.lock().unwrap().len(), 1);

        let moved = manifest.with_cache_root("/nonexistent-adapter-cache");

        assert!(
            moved.resolved.lock().unwrap().is_empty(),
            "a stale resolution survived a cache-root change"
        );
    }

    #[test]
    fn builtin_manifest_is_valid_and_pinned() {
        let manifest = AdapterManifest::builtin().unwrap();
        assert_eq!(manifest.adapters.len(), 4);
        assert!(manifest.adapters.iter().all(|item| {
            item.distribution == "native"
                || item
                    .distribution
                    .rsplit_once('@')
                    .is_some_and(|(_, version)| semver::Version::parse(version).is_ok())
        }));
    }

    #[test]
    fn every_npm_adapter_is_told_which_agent_to_run() {
        // The bug this exists to stop coming back: an npm adapter that is not
        // told where the agent is picks one for itself, and `claude-agent-acp`
        // picks a copy vendored inside its own package. Lemma then probed the
        // agent on this machine, published *that* version, and ran a different
        // binary carrying none of the user's configuration.
        //
        // Native adapters are exempt: they are the agent, so there is nothing to
        // point them at.
        let manifest = AdapterManifest::builtin().unwrap();
        for adapter in &manifest.adapters {
            if adapter.distribution.starts_with("npm:") {
                assert!(
                    adapter
                        .upstream_path_env
                        .as_deref()
                        .is_some_and(|name| !name.trim().is_empty()),
                    "npm adapter {} must name the variable that points at the agent",
                    adapter.key
                );
                // And the other half of the same decision: an adapter told where
                // the agent is has no use for the vendored copy in its optional
                // dependencies, which is 548 MB across the two certified ones.
                assert!(
                    adapter.omit_optional_dependencies,
                    "npm adapter {} points at the real agent, so it must not also fetch a vendored one",
                    adapter.key
                );
            } else {
                // Native adapters install nothing, so the flag would describe an
                // install that never happens.
                assert!(
                    !adapter.omit_optional_dependencies,
                    "native adapter {} has no npm install to omit anything from",
                    adapter.key
                );
            }
        }
    }

    #[test]
    fn the_adapter_environment_names_the_upstream_binary() {
        let manifest = AdapterManifest::builtin().unwrap();
        let spec = manifest
            .adapters
            .iter()
            .find(|adapter| adapter.key == "claude-code")
            .expect("claude-code is a certified adapter")
            .clone();
        let variable = spec
            .upstream_path_env
            .clone()
            .expect("claude-code names its variable");
        let resolved = ResolvedAdapter {
            spec,
            command: PathBuf::from("/cache/claude-agent-acp"),
            upstream_command: PathBuf::from("/usr/local/bin/claude"),
            upstream_version: Some("2.1.0".to_owned()),
        };

        let environment = resolved.environment();
        assert_eq!(
            environment.get(&variable).map(String::as_str),
            Some("/usr/local/bin/claude"),
            "the adapter must be pointed at the agent the host actually probed"
        );
        // PATH still leads with the agent's directory; the variable is belt and
        // braces for adapters that never consult it.
        assert!(
            environment
                .get("PATH")
                .is_some_and(|path| path.starts_with("/cache")),
            "the adapter and agent directories still lead PATH"
        );
    }

    #[test]
    fn the_installed_fingerprint_is_stable_and_covers_every_adapter() {
        // Stability is the whole point: an unstable fingerprint would re-probe
        // every couple of seconds forever, which is worse than the fifteen
        // minute timer it replaces.
        let manifest = AdapterManifest::builtin().unwrap();
        assert_eq!(
            manifest.installed_fingerprint(),
            manifest.installed_fingerprint()
        );
        assert_eq!(manifest.installed_fingerprint().len(), 64);

        // And it has to distinguish machines, or nothing is ever detected.
        //
        // Demonstrated through the adapter cache, which this test owns, rather
        // than by renaming `upstream_command` to something that cannot resolve.
        // That version passed only on a machine with an agent installed: where
        // none is, the real manifest and the renamed one both resolve every
        // command to "absent" and fingerprint identically. It was green
        // everywhere a developer ran it and red on every CI runner, which is the
        // worst way round.
        let cache = tempfile::tempdir().unwrap();
        let manifest = manifest.with_cache_root(cache.path().to_path_buf());
        let spec = manifest
            .adapters
            .iter()
            .find(|adapter| adapter.distribution.starts_with("npm:"))
            .expect("a certified npm adapter")
            .clone();

        let before = manifest.installed_fingerprint();
        let executable = cached_adapter_executable(cache.path(), &spec);
        std::fs::create_dir_all(executable.parent().unwrap()).unwrap();
        std::fs::write(&executable, b"#!/bin/sh\n").unwrap();
        let after = manifest.installed_fingerprint();
        assert_ne!(
            before, after,
            "a different set of installed agents must fingerprint differently"
        );

        // Size is folded in, so an agent replaced in place counts as a change
        // even at the same path. Without it an upgrade would be invisible until
        // the fifteen-minute sweep.
        std::fs::write(&executable, b"#!/bin/sh\necho a bigger one\n").unwrap();
        assert_ne!(
            after,
            manifest.installed_fingerprint(),
            "an agent replaced in place must fingerprint differently"
        );
    }

    #[test]
    fn an_adapter_landing_in_the_cache_counts_as_a_change() {
        // The bug: warming the cache in the background lets a probe run first,
        // so the harness publishes as Installing — correctly — and then the
        // install completes and nothing re-probes, because a fingerprint over
        // the *agent* binaries alone cannot see an *adapter* arrive. It stayed
        // "Installing" until the fifteen-minute sweep, which is the wait the
        // fingerprint exists to remove.
        let cache = tempfile::tempdir().unwrap();
        let manifest = AdapterManifest::builtin()
            .unwrap()
            .with_cache_root(cache.path().to_path_buf());
        let empty = manifest.installed_fingerprint();

        let spec = manifest
            .adapters
            .iter()
            .find(|adapter| adapter.distribution.starts_with("npm:"))
            .expect("a certified npm adapter");
        let executable = cached_adapter_executable(cache.path(), spec);
        std::fs::create_dir_all(executable.parent().unwrap()).unwrap();
        std::fs::write(&executable, b"#!/bin/sh\n").unwrap();

        assert_ne!(
            empty,
            manifest.installed_fingerprint(),
            "an adapter appearing in the cache must trigger a re-probe"
        );
    }

    #[test]
    fn an_install_that_cannot_succeed_stops_reporting_as_one_in_progress() {
        // "Setting up · usually under a minute" was every missing cache
        // directory, whether the download was in flight or impossible. Warming
        // is detached and best-effort by design, so its failure was a log line
        // and nothing the user could see -- and a machine behind a proxy that
        // blocks the npm registry read as permanently one minute from ready.
        let cache = tempfile::tempdir().unwrap();
        let manifest = AdapterManifest::builtin()
            .unwrap()
            .with_cache_root(cache.path().to_path_buf());
        let spec = manifest
            .adapters
            .iter()
            .find(|adapter| adapter.distribution.starts_with("npm:"))
            .expect("a certified npm adapter")
            .clone();

        // Nothing has been tried yet: not there is not the same as broken.
        let waiting = manifest.snapshot_for(&spec);
        assert_eq!(waiting.health, HarnessHealth::Installing);
        assert!(waiting.stale_reason.is_none());

        manifest.install_failures.lock().unwrap().insert(
            spec.key.clone(),
            "npm is required to install ACP adapters".into(),
        );

        let failed = manifest.snapshot_for(&spec);
        assert_eq!(
            failed.health,
            HarnessHealth::ProbeFailed,
            "an install that already failed is not an install in progress"
        );
        assert!(
            failed
                .stale_reason
                .as_deref()
                .is_some_and(|reason| reason.contains("npm is required")),
            "and it has to name the cause: {:?}",
            failed.stale_reason
        );

        // A later success clears it, so `doctor --repair` is a real remedy
        // rather than something that leaves the row saying it failed.
        manifest.install_failures.lock().unwrap().remove(&spec.key);
        assert_eq!(
            manifest.snapshot_for(&spec).health,
            HarnessHealth::Installing
        );
    }

    #[test]
    fn manifest_digest_is_stable() {
        let manifest = AdapterManifest::builtin().unwrap();
        assert_eq!(manifest.content_digest(), manifest.content_digest());
        assert_eq!(manifest.content_digest().len(), 64);
    }

    #[test]
    fn provider_version_output_is_compared_without_guessing() {
        assert!(version_is_at_least("claude 2.1.220", "2.1.0"));
        assert!(version_is_at_least("2026.07.09-a3815c0", "2026.3.11"));
        assert!(!version_is_at_least("opencode 1.16.9", "1.17.0"));
        assert!(!version_is_at_least("development build", "1.0.0"));
    }

    #[test]
    fn executable_resolution_uses_explicit_search_paths() {
        let root = tempfile::tempdir().unwrap();
        let executable = root.path().join("codex");
        std::fs::write(&executable, b"fixture").unwrap();
        assert_eq!(
            resolve_executable_in("codex", [root.path().to_path_buf()]),
            Some(executable)
        );
    }

    // npm installs `npm` and `npm.cmd` side by side, and only the second is a
    // thing CreateProcess can run. Resolving the bare name first is what made
    // the adapter cache warm-up report "%1 is not a valid Win32 application",
    // leaving every npm-distributed adapter stuck at Installing.
    #[cfg(windows)]
    #[test]
    fn a_windows_launcher_beats_the_posix_shim_beside_it() {
        let root = tempfile::tempdir().unwrap();
        std::fs::write(root.path().join("npm"), b"#!/bin/sh\n").unwrap();
        let launcher = root.path().join("npm.cmd");
        std::fs::write(&launcher, b"@echo off\n").unwrap();
        assert_eq!(
            resolve_executable_in("npm", [root.path().to_path_buf()]),
            Some(launcher)
        );
    }

    #[test]
    fn detection_covers_the_directories_these_agents_install_themselves_into() {
        // The Agent Host is a sidecar of a GUI app, so it inherits
        // /usr/bin:/bin:/usr/sbin:/sbin and nothing else — not a login shell's
        // PATH. Every agent people actually run is therefore invisible unless
        // this list names where it lives, and the ones shipping their own
        // installer are easiest to miss: OpenCode was reported as "adapter
        // executable opencode was not found" on a machine that had it at
        // ~/.opencode/bin/opencode.
        let home = Path::new("/home/example");
        let directories = home_executable_directories(home);

        for expected in [".opencode/bin", ".claude/local", ".local/bin"] {
            assert!(
                directories.contains(&home.join(expected)),
                "{expected} must be searched; it is where an agent's own installer puts it",
            );
        }
    }

    #[test]
    fn cache_integrity_covers_files_beyond_the_executable() {
        let root = tempfile::tempdir().unwrap();
        let executable = root.path().join("node_modules/.bin/codex-acp");
        let implementation = root.path().join("node_modules/codex-acp/dist/index.js");
        std::fs::create_dir_all(executable.parent().unwrap()).unwrap();
        std::fs::create_dir_all(implementation.parent().unwrap()).unwrap();
        std::fs::write(&executable, b"#!/usr/bin/env node\n").unwrap();
        std::fs::write(&implementation, b"export const safe = true;\n").unwrap();
        let digest = directory_sha256(root.path()).unwrap();
        std::fs::write(root.path().join(".lemma-cache.sha256"), digest).unwrap();
        verify_cached_adapter(&executable).unwrap();

        std::fs::write(&implementation, b"export const safe = false;\n").unwrap();
        assert!(
            verify_cached_adapter(&executable)
                .unwrap_err()
                .to_string()
                .contains("failed integrity validation")
        );
    }

    #[test]
    fn nvm_search_prefers_the_newest_installed_node() {
        let root = tempfile::tempdir().unwrap();
        for version in ["v18.20.1", "v22.14.0", "not-a-version"] {
            std::fs::create_dir_all(root.path().join(".nvm/versions/node").join(version)).unwrap();
        }
        let paths = nvm_node_bins(root.path());
        assert!(paths[0].ends_with("v22.14.0/bin"));
        assert!(paths[1].ends_with("v18.20.1/bin"));
        assert_eq!(paths.len(), 2);
    }
}
