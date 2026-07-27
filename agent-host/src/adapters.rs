//! Pinned ACP adapter manifest and local integration discovery.

use std::collections::BTreeMap;
use std::env;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::Duration;

use chrono::{Duration as ChronoDuration, Utc};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};

use crate::protocol::{
    AdapterProtocol, ConfigOption, IntegrationCapabilities, IntegrationHealth, IntegrationSnapshot,
    JsonMap,
};

const BUILTIN_MANIFEST: &str = include_str!("../agent-adapters.lock.json");
const SNAPSHOT_TTL: ChronoDuration = ChronoDuration::hours(24);

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct AdapterManifest {
    pub manifest_version: u16,
    pub manifest_id: String,
    pub protocol: String,
    pub adapters: Vec<AdapterSpec>,
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

    pub fn validate(&self) -> anyhow::Result<()> {
        anyhow::ensure!(
            self.manifest_version == 1,
            "unsupported adapter manifest version"
        );
        anyhow::ensure!(
            self.protocol == "ACP_V1",
            "adapter manifest must use ACP_V1"
        );
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
        let command = resolve_executable(&spec.command)
            .ok_or_else(|| anyhow::anyhow!("adapter launcher {} was not found", spec.command))?;
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

    #[must_use]
    pub fn discover(&self) -> Vec<IntegrationSnapshot> {
        self.adapters
            .iter()
            .map(|adapter| match self.resolve(&adapter.key) {
                Ok(resolved) => snapshot_ready(&resolved),
                Err(error) => snapshot_unavailable(adapter, &error.to_string()),
            })
            .collect()
    }
}

impl ResolvedAdapter {
    #[must_use]
    pub fn args(&self) -> Vec<String> {
        self.spec.args.clone()
    }

    #[must_use]
    pub fn environment(&self) -> BTreeMap<String, String> {
        let mut environment = BTreeMap::new();
        if let Some(parent) = self.upstream_command.parent() {
            let inherited = env::var_os("PATH").unwrap_or_default();
            let mut paths = vec![parent.to_path_buf()];
            paths.extend(env::split_paths(&inherited));
            if let Ok(joined) = env::join_paths(paths) {
                environment.insert("PATH".to_owned(), joined.to_string_lossy().into_owned());
            }
        }
        environment
    }
}

fn snapshot_ready(adapter: &ResolvedAdapter) -> IntegrationSnapshot {
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
    let mut metadata = JsonMap::new();
    metadata.insert(
        "distribution".to_owned(),
        Value::String(adapter.spec.distribution.clone()),
    );
    metadata.insert(
        "license".to_owned(),
        Value::String(adapter.spec.license.clone()),
    );
    IntegrationSnapshot {
        integration_key: adapter.spec.key.clone(),
        display_name: adapter.spec.display_name.clone(),
        adapter_protocol: AdapterProtocol::AcpV1,
        adapter_version: adapter.spec.adapter_version.clone(),
        upstream_version: adapter.upstream_version.clone(),
        auth_state: "LOCAL_CREDENTIALS".to_owned(),
        health: IntegrationHealth::Ready,
        capabilities: IntegrationCapabilities {
            plans: true,
            usage: true,
            ..IntegrationCapabilities::default()
        },
        config_revision,
        config_options,
        fetched_at: now,
        stale_after: now + SNAPSHOT_TTL,
        stale_reason: None,
        metadata,
    }
}

fn snapshot_unavailable(spec: &AdapterSpec, reason: &str) -> IntegrationSnapshot {
    let now = Utc::now();
    IntegrationSnapshot {
        integration_key: spec.key.clone(),
        display_name: spec.display_name.clone(),
        adapter_protocol: AdapterProtocol::AcpV1,
        adapter_version: spec.adapter_version.clone(),
        upstream_version: None,
        auth_state: "UNKNOWN".to_owned(),
        health: IntegrationHealth::ProbeFailed,
        capabilities: IntegrationCapabilities::default(),
        config_revision: hex::encode(Sha256::digest(reason.as_bytes())),
        config_options: Vec::new(),
        fetched_at: now,
        stale_after: now + ChronoDuration::minutes(5),
        stale_reason: Some(reason.to_owned()),
        metadata: JsonMap::new(),
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
    let path = env::var_os("PATH")?;
    for directory in env::split_paths(&path) {
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
}
