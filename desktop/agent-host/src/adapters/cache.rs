//! Installing an adapter into the cache, and verifying what is there.
//!
//! Downloaded on first run rather than shipped inside the app, which is a
//! decision rather than an omission. Bundling the two npm adapters would add
//! about 67 MB to every installer, and a real no-Node path would mean shipping
//! Node itself on top of that -- roughly doubling it -- to serve a machine
//! that either has no Node at all or cannot reach the registry. The app
//! already downloads on first run, so an adapter is nothing new in kind, and
//! the size is paid by everyone to spare an edge case.
//!
//! What that trade needs in exchange is a failure that says so, and the path
//! is bounded at both ends: `npm` runs under a 300-second cap that kills the
//! process tree, and whatever went wrong is recorded per adapter in
//! `install_failures`, which `snapshot_for` turns into a named cause instead
//! of a "Setting up, usually under a minute" that never ends. `doctor
//! --repair` is the retry.

use super::{
    AdapterManifest, AdapterSpec, Cancellation, Command, Digest, Duration, Mutex, Path, PathBuf,
    Sha256, Value, resolve_executable, setup_process,
};

/// Fold one resolved path — or its absence — into a fingerprint.
///
/// Size and mtime are included so an upgrade in place counts as a change, not
/// only an install or an uninstall.
pub(crate) fn fingerprint_path(digest: &mut Sha256, path: Option<PathBuf>) {
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
pub(crate) fn install_cached_adapter(
    spec: &AdapterSpec,
    cache_root: &Path,
    repair: bool,
    registry: &Mutex<()>,
    cancellation: &Cancellation,
) -> anyhow::Result<()> {
    anyhow::ensure!(!cancellation.is_cancelled(), "adapter setup cancelled");
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
            anyhow::ensure!(!cancellation.is_cancelled(), "adapter setup cancelled");
            install_npm_adapter(spec, &staging, cancellation)?;
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
    if cancellation.is_cancelled() {
        let _ = std::fs::remove_dir_all(&staging);
        anyhow::bail!("adapter setup cancelled");
    }
    activate_staged_cache(&staging, &destination)?;
    verify_cached_adapter(&executable)
}

pub(crate) fn cached_adapter_directory(cache_root: &Path, spec: &AdapterSpec) -> PathBuf {
    cache_root.join(&spec.key).join(&spec.adapter_version)
}

pub(crate) fn cached_adapter_executable(cache_root: &Path, spec: &AdapterSpec) -> PathBuf {
    platform_cached_executable(&cached_adapter_directory(cache_root, spec), &spec.command)
}

pub(crate) fn platform_cached_executable(root: &Path, command: &str) -> PathBuf {
    let executable = root.join("node_modules").join(".bin").join(command);
    // npm writes a .cmd shim on Windows and a symlink everywhere else.
    #[cfg(windows)]
    let executable = executable.with_extension("cmd");
    executable
}

pub(crate) fn verify_cached_adapter(executable: &Path) -> anyhow::Result<()> {
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

pub(crate) fn directory_sha256(root: &Path) -> anyhow::Result<String> {
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

pub(crate) fn activate_staged_cache(staging: &Path, destination: &Path) -> anyhow::Result<()> {
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

pub(crate) fn install_npm_adapter(
    spec: &AdapterSpec,
    staging: &Path,
    cancellation: &Cancellation,
) -> anyhow::Result<()> {
    let package = spec
        .distribution
        .strip_prefix("npm:")
        .ok_or_else(|| anyhow::anyhow!("invalid npm distribution"))?;
    let npm = resolve_executable("npm")
        .ok_or_else(|| anyhow::anyhow!("npm is required to install ACP adapters"))?;
    std::fs::create_dir_all(staging)?;
    let mut command = Command::new(npm);
    command.args(["install", "--ignore-scripts", "--no-audit", "--no-fund"]);
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
    command
        .args(["--package-lock=true", "--prefix"])
        .arg(staging)
        .arg(package);
    let output = setup_process::run_cancellable(
        command,
        Duration::from_secs(300),
        4 * 1024 * 1024,
        cancellation.clone(),
    )
    .map_err(|error| {
        anyhow::anyhow!("npm adapter installation failed: {error}. Recheck your network and retry.")
    })?;
    anyhow::ensure!(
        output.status.success(),
        "npm adapter installation failed; check registry access and available disk space, then retry"
    );

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

pub struct AdapterWarmup {
    cancellation: Cancellation,
    worker: Option<std::thread::JoinHandle<()>>,
}

impl Drop for AdapterWarmup {
    fn drop(&mut self) {
        self.cancellation.cancel();
        if let Some(worker) = self.worker.take()
            && worker.join().is_err()
        {
            tracing::warn!("adapter setup worker failed during shutdown");
        }
    }
}

impl AdapterManifest {
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
        self.install_cache_cancellable(cache_root, repair, &Cancellation::default())
    }

    pub fn start_cache_warmup(&self, cache_root: PathBuf) -> std::io::Result<AdapterWarmup> {
        let manifest = self.clone();
        let cancellation = Cancellation::default();
        let worker_cancellation = cancellation.clone();
        let worker = std::thread::Builder::new()
            .name("adapter-install".into())
            .spawn(move || {
                match manifest.install_cache_cancellable(&cache_root, false, &worker_cancellation) {
                    Ok(()) => tracing::info!("adapter cache ready"),
                    Err(error) if !worker_cancellation.is_cancelled() => {
                        tracing::warn!(%error, "adapter setup failed");
                    }
                    Err(_) => tracing::info!("adapter setup cancelled"),
                }
            })?;
        Ok(AdapterWarmup {
            cancellation,
            worker: Some(worker),
        })
    }

    pub(crate) fn install_cache_cancellable(
        &self,
        cache_root: &Path,
        repair: bool,
        cancellation: &Cancellation,
    ) -> anyhow::Result<()> {
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
                        let result = install_cached_adapter(
                            spec,
                            cache_root,
                            repair,
                            registry,
                            cancellation,
                        );
                        let mut failures = self.install_failures.lock().expect("install failures");
                        match &result {
                            Ok(()) => {
                                failures.remove(&spec.key);
                            }
                            Err(error) => {
                                failures.insert(spec.key.clone(), error.to_string());
                            }
                        }
                        (spec.key.clone(), result)
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
        // Join every installer before returning; an early failure must not
        // leave another worker writing into the cache after shutdown.
        results
            .into_iter()
            .find_map(|(_, result)| result.err())
            .map_or(Ok(()), Err)
    }
}
