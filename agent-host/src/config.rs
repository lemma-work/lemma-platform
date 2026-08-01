//! Host paths and non-secret target configuration.

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use url::Url;
use uuid::Uuid;

#[derive(Clone, Debug)]
pub struct HostPaths {
    pub root: PathBuf,
    pub config: PathBuf,
    pub journal: PathBuf,
    pub log: PathBuf,
    pub adapters: PathBuf,
}

impl HostPaths {
    pub fn platform_default() -> anyhow::Result<Self> {
        if let Some(root) =
            std::env::var_os("LEMMA_AGENT_HOST_DATA_DIR").filter(|value| !value.is_empty())
        {
            return Ok(Self::under(root));
        }
        #[cfg(target_os = "macos")]
        let root = home_directory()?.join("Library/Application Support/Lemma/agent-host");

        #[cfg(target_os = "windows")]
        let root = std::env::var_os("LOCALAPPDATA")
            .map(PathBuf::from)
            .ok_or_else(|| anyhow::anyhow!("LOCALAPPDATA is not set"))?
            .join("Lemma/agent-host");

        #[cfg(all(unix, not(target_os = "macos")))]
        let root = std::env::var_os("XDG_STATE_HOME")
            .map(PathBuf::from)
            .unwrap_or(home_directory()?.join(".local/state"))
            .join("lemma/agent-host");

        Ok(Self::under(root))
    }

    #[must_use]
    pub fn under(root: impl AsRef<Path>) -> Self {
        let root = root.as_ref().to_path_buf();
        Self {
            config: root.join("config.json"),
            journal: root.join("journal.sqlite3"),
            log: root.join("agent-host.log"),
            adapters: root.join("adapters"),
            root,
        }
    }

    pub fn ensure(&self) -> std::io::Result<()> {
        std::fs::create_dir_all(&self.root)
    }
}

#[cfg(not(windows))]
fn home_directory() -> anyhow::Result<PathBuf> {
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
        .ok_or_else(|| anyhow::anyhow!("home directory is not set"))
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct HostConfig {
    pub installation_id: String,
    #[serde(default, deserialize_with = "targets_skipping_unreadable")]
    pub targets: Vec<TargetConfig>,
    #[serde(default = "default_max_runs")]
    pub max_runs: u16,
}

/// Drop targets this build cannot read, rather than failing the whole config.
///
/// A target written by an older Agent Host can lack a field this one requires -
/// pairing moved from a keypair to `host_secret`, so every pre-upgrade entry is
/// unreadable. Failing the load made *every* command exit with a serde error
/// pointing at a line number, including the `connect` you would run to recover:
/// the only way out was to hand-edit the file. A target we cannot read is a
/// target we cannot use, so skipping it loses nothing and leaves the host able
/// to pair again.
fn targets_skipping_unreadable<'de, D>(deserializer: D) -> Result<Vec<TargetConfig>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    let raw = Vec::<serde_json::Value>::deserialize(deserializer)?;
    Ok(raw
        .into_iter()
        .filter_map(|value| {
            let name = value
                .get("name")
                .and_then(serde_json::Value::as_str)
                .unwrap_or("<unnamed>")
                .to_owned();
            match serde_json::from_value::<TargetConfig>(value) {
                Ok(target) => Some(target),
                Err(error) => {
                    tracing::warn!(
                        target_name = %name,
                        %error,
                        "ignoring a paired workspace this version cannot read; pair it again"
                    );
                    None
                }
            }
        })
        .collect())
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TargetConfig {
    pub target_id: Uuid,
    pub name: String,
    pub base_url: Url,
    pub host_id: Uuid,
    pub user_id: Uuid,
    pub organization_id: Option<Uuid>,
    /// Bearer credential issued once at pairing; rotatable by re-pairing.
    pub host_secret: String,
    #[serde(default = "default_enabled")]
    pub enabled: bool,
    #[serde(default)]
    pub allow_insecure_http: bool,
    #[serde(default)]
    pub draining: bool,
    #[serde(default)]
    pub refresh_generation: u64,
}

const fn default_max_runs() -> u16 {
    2
}

const fn default_enabled() -> bool {
    true
}

impl HostConfig {
    pub fn load_or_create(paths: &HostPaths) -> anyhow::Result<Self> {
        paths.ensure()?;
        if paths.config.exists() {
            let value = serde_json::from_slice(&std::fs::read(&paths.config)?)?;
            return Ok(value);
        }
        let config = Self {
            installation_id: Uuid::new_v4().to_string(),
            targets: Vec::new(),
            max_runs: default_max_runs(),
        };
        config.save(paths)?;
        Ok(config)
    }

    pub fn save(&self, paths: &HostPaths) -> anyhow::Result<()> {
        paths.ensure()?;
        let temporary = paths.config.with_extension("json.tmp");
        let mut bytes = serde_json::to_vec_pretty(self)?;
        bytes.push(b'\n');
        std::fs::write(&temporary, bytes)?;
        // The config carries host secrets; keep it owner-only.
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&temporary, std::fs::Permissions::from_mode(0o600))?;
        }
        std::fs::rename(temporary, &paths.config)?;
        Ok(())
    }

    pub fn validate(&self) -> anyhow::Result<()> {
        anyhow::ensure!(
            !self.installation_id.trim().is_empty(),
            "installation ID is empty"
        );
        anyhow::ensure!(self.max_runs > 0, "max_runs must be positive");
        let mut identifiers = std::collections::BTreeSet::new();
        for target in &self.targets {
            anyhow::ensure!(
                identifiers.insert(target.target_id),
                "duplicate target ID {}",
                target.target_id
            );
            if target.base_url.scheme() != "https" {
                anyhow::ensure!(
                    target.allow_insecure_http
                        && matches!(
                            target.base_url.host_str(),
                            Some("localhost" | "127.0.0.1" | "::1")
                        ),
                    "target {} must use HTTPS (HTTP is allowed only for an explicitly opted-in loopback target)",
                    target.name
                );
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_remote_plain_http() {
        let config = HostConfig {
            installation_id: "installation".into(),
            max_runs: 1,
            targets: vec![TargetConfig {
                target_id: Uuid::new_v4(),
                name: "unsafe".into(),
                base_url: Url::parse("http://example.com").unwrap(),
                host_id: Uuid::new_v4(),
                user_id: Uuid::new_v4(),
                organization_id: None,
                host_secret: "test-secret".into(),
                enabled: true,
                allow_insecure_http: true,
                draining: false,
                refresh_generation: 0,
            }],
        };
        assert!(config.validate().is_err());
    }

    #[test]
    fn a_target_from_before_host_secrets_is_skipped_not_fatal() {
        // Exactly the shape written by the keypair-era host. Failing the whole
        // load on it bricked every command, `connect` included, so the only
        // recovery was hand-editing the file.
        let legacy = serde_json::json!({
            "installation_id": "installation",
            "max_runs": 2,
            "targets": [
                {
                    "target_id": Uuid::new_v4(),
                    "name": "paired before the upgrade",
                    "base_url": "http://localhost:8710/",
                    "host_id": Uuid::new_v4(),
                    "user_id": Uuid::new_v4(),
                    "organization_id": null,
                    "public_key_fingerprint": "0d009517e46ab181",
                    "enabled": true,
                    "allow_insecure_http": true,
                    "draining": false,
                    "refresh_generation": 0
                }
            ]
        });

        let config: HostConfig = serde_json::from_value(legacy).unwrap();

        assert!(config.targets.is_empty());
        assert_eq!(config.installation_id, "installation");
        assert_eq!(config.max_runs, 2);
    }

    #[test]
    fn a_readable_target_survives_beside_an_unreadable_one() {
        let good = Uuid::new_v4();
        let mixed = serde_json::json!({
            "installation_id": "installation",
            "targets": [
                { "name": "unreadable", "target_id": Uuid::new_v4() },
                {
                    "target_id": good,
                    "name": "current",
                    "base_url": "https://api.lemma.work/",
                    "host_id": Uuid::new_v4(),
                    "user_id": Uuid::new_v4(),
                    "organization_id": null,
                    "host_secret": "secret",
                    "enabled": true,
                    "allow_insecure_http": false,
                    "draining": false,
                    "refresh_generation": 0
                }
            ]
        });

        let config: HostConfig = serde_json::from_value(mixed).unwrap();

        assert_eq!(config.targets.len(), 1);
        assert_eq!(config.targets[0].target_id, good);
    }
}
