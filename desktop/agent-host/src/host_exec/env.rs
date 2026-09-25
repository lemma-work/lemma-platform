//! The environment host commands run with: the owner's login shell's, minus
//! anything that is a credential.
//!
//! The Agent Host is started by locald, not from a terminal, so its own
//! environment has none of what the owner's shell profile sets up -- no
//! Homebrew on `PATH`, no nvm, no asdf. A command an agent runs should find
//! the same tools the owner's terminal does, so the environment is taken from
//! a login shell once, cached, and refreshed on request.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::time::Duration;

use serde::{Deserialize, Serialize};

/// How long the login shell may take to print its environment. A profile
/// that prompts, or starts something that never returns, must not wedge
/// host execution; it gets the Agent Host's own environment instead.
pub const SNAPSHOT_TIMEOUT: Duration = Duration::from_secs(10);

/// Separates whatever the profile prints from the environment itself.
const MARKER: &str = "__LEMMA_HOST_ENV_BEGIN__";

/// Whether a variable must not reach a host command.
///
/// Lemma's and the Agent Host's own settings name its credentials and data
/// directories. The suffix rules catch the common spellings of a secret in
/// someone's shell profile; `AWS_*` is all of it, since the region and
/// profile names are what point a stray `aws` call at a real account.
#[must_use]
pub fn is_scrubbed(name: &str) -> bool {
    let upper = name.to_ascii_uppercase();
    upper.starts_with("LEMMA_")
        || upper.starts_with("AGENT_HOST_")
        || upper.starts_with("AWS_")
        || upper.ends_with("_TOKEN")
        || upper.ends_with("_SECRET")
        || upper.ends_with("_API_KEY")
}

/// `environment` without anything `is_scrubbed` names.
#[must_use]
pub fn scrub(environment: BTreeMap<String, String>) -> BTreeMap<String, String> {
    environment
        .into_iter()
        .filter(|(name, _)| !is_scrubbed(name))
        .collect()
}

/// The cached snapshot, beside the Agent Host's configuration.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct EnvironmentSnapshot {
    pub taken_at: chrono::DateTime<chrono::Utc>,
    pub shell: String,
    pub variables: BTreeMap<String, String>,
}

#[must_use]
pub fn cache_path(data_root: &Path) -> PathBuf {
    data_root.join("host-environment.json")
}

impl EnvironmentSnapshot {
    /// The cached snapshot, or a fresh one if there is none.
    pub async fn load_or_take(data_root: &Path) -> Self {
        if let Some(cached) = Self::cached(data_root) {
            return cached;
        }
        Self::refresh(data_root).await
    }

    #[must_use]
    pub fn cached(data_root: &Path) -> Option<Self> {
        let raw = std::fs::read(cache_path(data_root)).ok()?;
        serde_json::from_slice(&raw).ok()
    }

    /// Take a new snapshot and cache it. Falls back to this process's own
    /// environment, scrubbed, when the shell cannot be asked.
    pub async fn refresh(data_root: &Path) -> Self {
        let shell = std::env::var("SHELL")
            .ok()
            .filter(|shell| !shell.is_empty())
            .unwrap_or_else(|| "/bin/zsh".to_owned());
        let variables = match take(&shell).await {
            Ok(variables) => variables,
            Err(error) => {
                tracing::warn!(%error, %shell, "could not read the login shell's environment; using the Agent Host's own");
                std::env::vars().collect()
            }
        };
        let snapshot = Self {
            taken_at: chrono::Utc::now(),
            shell,
            variables: scrub(variables),
        };
        if let Err(error) = snapshot.save(data_root) {
            tracing::warn!(%error, "could not cache the host environment");
        }
        snapshot
    }

    fn save(&self, data_root: &Path) -> anyhow::Result<()> {
        std::fs::create_dir_all(data_root)?;
        let path = cache_path(data_root);
        let temporary = path.with_extension(format!("json.{}.tmp", uuid::Uuid::new_v4()));
        std::fs::write(&temporary, serde_json::to_vec_pretty(self)?)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&temporary, std::fs::Permissions::from_mode(0o600))?;
        }
        std::fs::rename(&temporary, &path).inspect_err(|_| {
            let _ = std::fs::remove_file(&temporary);
        })?;
        Ok(())
    }
}

/// Ask `shell` for its environment as a login, interactive shell would set it.
///
/// `env -0` after a marker: an interactive profile is free to print banners,
/// and a value is free to contain newlines; neither may be mistaken for a
/// variable.
async fn take(shell: &str) -> anyhow::Result<BTreeMap<String, String>> {
    let mut command = tokio::process::Command::new(shell);
    command
        .args([
            "-l",
            "-i",
            "-c",
            &format!("printf '%s\\0' {MARKER}; env -0"),
        ])
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null())
        .kill_on_drop(true);
    #[cfg(unix)]
    command.process_group(0);
    let child = command.spawn()?;
    let output = tokio::time::timeout(SNAPSHOT_TIMEOUT, child.wait_with_output())
        .await
        .map_err(|_| anyhow::anyhow!("the login shell did not answer in {SNAPSHOT_TIMEOUT:?}"))??;
    anyhow::ensure!(
        output.status.success(),
        "the login shell exited with {}",
        output.status
    );
    parse(&output.stdout)
}

fn parse(raw: &[u8]) -> anyhow::Result<BTreeMap<String, String>> {
    let text = String::from_utf8_lossy(raw);
    let start = text
        .find(&format!("{MARKER}\0"))
        .ok_or_else(|| anyhow::anyhow!("the login shell printed no environment"))?;
    let body = &text[start + MARKER.len() + 1..];
    Ok(body
        .split('\0')
        .filter_map(|entry| entry.split_once('='))
        .filter(|(name, _)| !name.is_empty() && !name.contains('\n'))
        .map(|(name, value)| (name.to_owned(), value.to_owned()))
        .collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn credentials_and_lemma_settings_are_scrubbed_and_the_path_is_not() {
        let environment: BTreeMap<String, String> = [
            "PATH",
            "HOME",
            "LEMMA_API_URL",
            "AGENT_HOST_SECRET_FILE",
            "GITHUB_TOKEN",
            "Stripe_Secret",
            "OPENAI_API_KEY",
            "AWS_PROFILE",
            "NVM_DIR",
            "TOKENIZERS_PARALLELISM",
        ]
        .into_iter()
        .map(|name| (name.to_owned(), "x".to_owned()))
        .collect();
        let kept: Vec<_> = scrub(environment).into_keys().collect();
        assert_eq!(kept, ["HOME", "NVM_DIR", "PATH", "TOKENIZERS_PARALLELISM"]);
    }

    #[test]
    fn a_profile_that_prints_does_not_leak_into_the_environment() {
        let raw = b"Welcome back!\nPATH=/nope\n__LEMMA_HOST_ENV_BEGIN__\0PATH=/opt/homebrew/bin:/usr/bin\0MULTI=a\nb\0";
        let parsed = parse(raw).unwrap();
        assert_eq!(parsed["PATH"], "/opt/homebrew/bin:/usr/bin");
        assert_eq!(parsed["MULTI"], "a\nb");
        assert_eq!(parsed.len(), 2);
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn a_snapshot_is_taken_scrubbed_and_cached() {
        let directory = tempfile::tempdir().unwrap();
        let variables = take("/bin/bash").await.unwrap();
        assert!(variables.contains_key("PATH"));
        let snapshot = EnvironmentSnapshot::refresh(directory.path()).await;
        assert!(snapshot.variables.keys().all(|name| !is_scrubbed(name)));
        let cached = EnvironmentSnapshot::cached(directory.path()).unwrap();
        assert_eq!(cached.variables, snapshot.variables);
    }
}
