//! Pinned ACP adapter manifest and local harness discovery.

use std::collections::{BTreeMap, HashSet};
use std::env;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::Duration;

use chrono::{Duration as ChronoDuration, Utc};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};

use crate::protocol::{ConfigOption, HarnessCapabilities, HarnessHealth, HarnessSnapshot};

const BUILTIN_MANIFEST: &str = include_str!("../agent-adapters.lock.json");
const SNAPSHOT_TTL: ChronoDuration = ChronoDuration::hours(24);

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct AdapterManifest {
    pub manifest_version: u16,
    pub manifest_id: String,
    pub protocol: String,
    pub adapters: Vec<AdapterSpec>,
    #[serde(skip)]
    cache_root: Option<PathBuf>,
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
        let upstream_version = probe_version(&upstream_command, &spec.upstream_version_args);
        if let Some(minimum) = spec.minimum_upstream_version.as_deref() {
            let installed = upstream_version.as_deref().ok_or_else(|| {
                anyhow::anyhow!(
                    "{} version could not be determined (minimum {minimum})",
                    spec.display_name
                )
            })?;
            anyhow::ensure!(
                version_is_at_least(installed, minimum),
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

    pub fn install_cache(&self, cache_root: &Path, repair: bool) -> anyhow::Result<()> {
        std::fs::create_dir_all(cache_root)?;
        for spec in &self.adapters {
            if !spec.distribution.starts_with("npm:") {
                continue;
            }
            let destination = cached_adapter_directory(cache_root, spec);
            let executable = cached_adapter_executable(cache_root, spec);
            if verify_cached_adapter(&executable).is_ok() {
                continue;
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
                install_npm_adapter(spec, &staging)?;
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
            verify_cached_adapter(&executable)?;
        }
        Ok(())
    }

    #[must_use]
    pub fn discover(&self) -> Vec<HarnessSnapshot> {
        self.adapters
            .iter()
            .map(|adapter| match self.resolve(&adapter.key) {
                Ok(resolved) => snapshot_ready(&resolved),
                Err(error) => snapshot_unavailable(adapter, &error.to_string()),
            })
            .collect()
    }
}

fn cached_adapter_directory(cache_root: &Path, spec: &AdapterSpec) -> PathBuf {
    cache_root.join(&spec.key).join(&spec.adapter_version)
}

fn cached_adapter_executable(cache_root: &Path, spec: &AdapterSpec) -> PathBuf {
    platform_cached_executable(&cached_adapter_directory(cache_root, spec), &spec.command)
}

fn platform_cached_executable(root: &Path, command: &str) -> PathBuf {
    let executable = root.join("node_modules").join(".bin").join(command);
    #[cfg(windows)]
    {
        return executable.with_extension("cmd");
    }
    #[cfg(not(windows))]
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
    let status = Command::new(npm)
        .args([
            "install",
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
            "--package-lock=true",
            "--prefix",
        ])
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
        environment
    }
}

fn snapshot_ready(adapter: &ResolvedAdapter) -> HarnessSnapshot {
    let now = Utc::now();
    let config_options = Vec::<ConfigOption>::new();
    let revision_input = serde_json::json!({
        "adapter": adapter.spec.adapter_version,
        "upstream": adapter.upstream_version,
        "config": config_options,
    });
    let config_revision = hex::encode(Sha256::digest(
        serde_json::to_vec(&revision_input).expect("snapshot serialization"),
    ));
    HarnessSnapshot {
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
        config_revision,
        config_options,
        stale_after: now + SNAPSHOT_TTL,
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

fn probe_version(executable: &Path, arguments: &[String]) -> Option<String> {
    let mut command = Command::new(executable);
    command
        .args(arguments)
        .stdin(Stdio::null())
        .stderr(Stdio::piped())
        .stdout(Stdio::piped());
    let mut child = command.spawn().ok()?;
    let started = std::time::Instant::now();
    loop {
        if started.elapsed() > Duration::from_secs(5) {
            return None;
        }
        match child.try_wait() {
            Ok(Some(status)) if status.success() => {
                let output = child.wait_with_output().ok()?;
                let stdout = String::from_utf8_lossy(&output.stdout);
                let stderr = String::from_utf8_lossy(&output.stderr);
                let text = if stdout.trim().is_empty() {
                    stderr.trim().to_owned()
                } else {
                    stdout.trim().to_owned()
                };
                return (!text.is_empty()).then_some(text);
            }
            Ok(Some(_)) | Err(_) => return None,
            Ok(None) => std::thread::sleep(Duration::from_millis(25)),
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
        let candidate = directory.join(command);
        if candidate.is_file() {
            return Some(candidate);
        }
        #[cfg(windows)]
        for extension in ["exe", "cmd", "bat"] {
            let candidate = directory.join(format!("{command}.{extension}"));
            if candidate.is_file() {
                return Some(candidate);
            }
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
        for relative in [
            ".local/bin",
            ".cargo/bin",
            ".volta/bin",
            ".asdf/shims",
            ".local/share/mise/shims",
            ".local/share/pnpm",
            ".bun/bin",
            ".npm-global/bin",
        ] {
            push_unique(&mut paths, &mut seen, home.join(relative));
        }
        #[cfg(target_os = "macos")]
        push_unique(&mut paths, &mut seen, home.join("Library/pnpm"));
        for path in nvm_node_bins(home) {
            push_unique(&mut paths, &mut seen, path);
        }
        #[cfg(windows)]
        push_unique(&mut paths, &mut seen, home.join("AppData/Roaming/npm"));
    }

    #[cfg(unix)]
    for path in ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"] {
        push_unique(&mut paths, &mut seen, PathBuf::from(path));
    }
    paths
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
