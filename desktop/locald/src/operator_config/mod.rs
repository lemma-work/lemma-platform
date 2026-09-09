//! The operator configuration: what this installation is pointed at, the
//! secrets behind it, and the environment the backend is started with.
//!
//! Was one 2,423-line file. Split by what is being done to the config.

use std::collections::{BTreeMap, HashMap};
use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::provider_probe::{HttpModelProviderProbe, ModelProviderProbe};

const CONFIG_SCHEMA_VERSION: u64 = 1;
const VAULT_SERVICE: &str = "work.lemma.local";

pub(crate) const SECRET_NAMES: [&str; 19] = [
    "ai.api_key",
    "integrations.deepgram_api_key",
    "integrations.composio_api_key",
    "integrations.composio_webhook_secret",
    "integrations.google_client_secret",
    "integrations.microsoft_client_secret",
    "integrations.github_client_secret",
    "integrations.slack_client_secret",
    "surfaces.slack_app_token",
    "surfaces.slack_bot_token",
    "surfaces.slack_signing_secret",
    "surfaces.telegram_bot_token",
    "surfaces.telegram_webhook_secret",
    "surfaces.teams_app_password",
    "surfaces.whatsapp_access_token",
    "surfaces.whatsapp_verify_token",
    "surfaces.whatsapp_app_secret",
    "surfaces.resend_api_key",
    "surfaces.resend_signing_secret",
];

mod apply;
mod backend_env;
mod config;
mod validate;
mod vault;

pub(crate) use apply::*;
pub(crate) use backend_env::*;
pub(crate) use config::*;
pub(crate) use validate::*;
pub(crate) use vault::*;

#[cfg(test)]
mod tests;

pub struct OperatorConfigStore {
    path: PathBuf,
    vault: Arc<dyn SecretVault>,
    provider_probe: Arc<dyn ModelProviderProbe>,
    config: Mutex<OperatorConfig>,
    writes: Mutex<()>,
}

#[derive(Clone)]
pub(crate) struct OperatorConfigState {
    config: OperatorConfig,
    secrets: BTreeMap<String, Option<String>>,
}

pub(crate) fn write_private_atomic(path: &Path, contents: &[u8]) -> io::Result<()> {
    let parent = path
        .parent()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "config path has no parent"))?;
    fs::create_dir_all(parent)?;
    let temporary = path.with_extension(format!("next-{}", std::process::id()));
    let _ = fs::remove_file(&temporary);
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(&temporary)?;
    file.write_all(contents)?;
    file.sync_all()?;
    fs::rename(temporary, path)?;
    #[cfg(unix)]
    fs::File::open(parent)?.sync_all()?;
    ensure_private_file(path)
}

/// Make sure the config is a regular file only this user can read.
///
/// Over-broad permissions are *repaired* rather than rejected. Refusing to
/// start does not make the file any less readable -- it just means the
/// installation never runs again -- and the way this actually happens is
/// somebody moving their state directory with `cp -R` instead of `cp -Rp`,
/// which lands 0644 and used to be permanently fatal, silently.
///
/// Anything that is not a regular file still fails: a symlink or a directory
/// here is not a permissions accident, and following one would be the bug.
pub(crate) fn ensure_private_file(path: &Path) -> io::Result<()> {
    let metadata = fs::symlink_metadata(path)?;
    if !metadata.file_type().is_file() {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            format!(
                "private configuration is not a regular file: {}",
                path.display()
            ),
        ));
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::{MetadataExt, PermissionsExt};
        if metadata.mode() & 0o077 != 0 {
            fs::set_permissions(path, fs::Permissions::from_mode(0o600))?;
        }
    }
    #[cfg(not(unix))]
    let _ = path;
    Ok(())
}

impl OperatorConfigStore {
    /// `load`, plus a note for anything it had to repair on the way.
    ///
    /// The public entry point takes the vault as an implementation detail; only
    /// tests substitute one.
    pub fn load_reporting(path: PathBuf, healed: &mut Vec<String>) -> io::Result<Arc<Self>> {
        let vault = crate::credential_vault::EncryptedVault::new(
            path.with_file_name("credentials.enc"),
            Arc::new(CachingVault::new(Arc::new(PlatformVault))),
        );
        Self::load_healing(
            path,
            Arc::new(vault),
            Arc::new(HttpModelProviderProbe),
            healed,
        )
    }

    #[cfg(test)]
    fn load_with_vault(path: PathBuf, vault: Arc<dyn SecretVault>) -> io::Result<Arc<Self>> {
        Self::load_with_vault_reporting(path, vault, &mut Vec::new())
    }

    /// A store with both collaborators substituted, for tests that assert on
    /// provider probing rather than on loading.
    #[cfg(test)]
    fn load_probing(
        path: PathBuf,
        vault: Arc<dyn SecretVault>,
        provider_probe: Arc<dyn ModelProviderProbe>,
    ) -> io::Result<Arc<Self>> {
        Self::load_healing(path, vault, provider_probe, &mut Vec::new())
    }

    #[cfg(test)]
    fn load_with_vault_reporting(
        path: PathBuf,
        vault: Arc<dyn SecretVault>,
        healed: &mut Vec<String>,
    ) -> io::Result<Arc<Self>> {
        Self::load_healing(path, vault, Arc::new(EchoModelProviderProbe), healed)
    }

    /// Load the operator's configuration, replacing it only if it is unusable.
    ///
    /// This file used to be able to end the daemon three separate ways: a
    /// permissions check, a strict parse, and an exact `schema_version` match.
    /// Every nested struct carries `deny_unknown_fields`, so running a build
    /// that adds a field and then going back to one that does not was enough to
    /// make an installation refuse to start -- silently, because the reason went
    /// to a null stderr. `state.json` has self-healed all along; the file on the
    /// critical path did not.
    ///
    /// Order matters: migrate a known older shape, and only quarantine what
    /// cannot be understood at all. A *newer* schema is quarantined rather than
    /// downgraded, which is what makes downgrade-then-upgrade recoverable.
    fn load_healing(
        path: PathBuf,
        vault: Arc<dyn SecretVault>,
        provider_probe: Arc<dyn ModelProviderProbe>,
        healed: &mut Vec<String>,
    ) -> io::Result<Arc<Self>> {
        let config = if path.is_file() {
            match Self::read_existing(&path) {
                Ok(config) => config,
                Err(reason) => {
                    // Salvage the identity before the file naming it goes away.
                    let salvaged = fs::read(&path)
                        .ok()
                        .and_then(|raw| salvage_install_id(&raw));
                    let aside = crate::paths::quarantine_aside(&path)?;
                    let config = match salvaged {
                        Some(install_id) => {
                            healed.push(format!(
                                "the operator configuration was unusable ({reason}); kept as {} \
                                 and replaced, keeping this installation's stored secrets",
                                aside.display()
                            ));
                            OperatorConfig::fresh_with_install_id(install_id)?
                        }
                        None => {
                            healed.push(format!(
                                "the operator configuration was unusable ({reason}) and its \
                                 installation id could not be recovered; kept as {} and replaced. \
                                 Previously stored secrets are unreachable and a full reinstall \
                                 is the only thing that will clear them",
                                aside.display()
                            ));
                            OperatorConfig::fresh()?
                        }
                    };
                    validate_config(&config)?;
                    write_private_atomic(&path, &serde_json::to_vec_pretty(&config)?)?;
                    config
                }
            }
        } else {
            let config = OperatorConfig::fresh()?;
            validate_config(&config)?;
            write_private_atomic(&path, &serde_json::to_vec_pretty(&config)?)?;
            config
        };
        Ok(Arc::new(Self {
            path,
            vault,
            provider_probe,
            config: Mutex::new(config),
            writes: Mutex::new(()),
        }))
    }

    /// Parse an existing file, migrating an older schema rather than rejecting
    /// it. Returns the reason it is unusable, for the caller to record.
    fn read_existing(path: &Path) -> Result<OperatorConfig, String> {
        ensure_private_file(path).map_err(|error| error.to_string())?;
        let raw = fs::read(path).map_err(|error| error.to_string())?;
        let value: Value =
            serde_json::from_slice(&raw).map_err(|error| format!("invalid JSON: {error}"))?;
        let config = migrate_config(value).ok_or("unsupported configuration shape")?;
        validate_config(&config).map_err(|error| error.to_string())?;
        Ok(config)
    }
}
