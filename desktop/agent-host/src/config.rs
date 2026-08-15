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
    pub lock: PathBuf,
    pub config_lock: PathBuf,
}

/// Proof that this process is the only Agent Host for its data directory.
///
/// One process serves every paired workspace, so two of them mean two pollers
/// on one credential: whichever wins a command runs it, and whichever exits
/// first reports `available_runs = 0` and marks the machine DRAINING. The
/// symptoms are a workspace that flaps between online and unavailable and
/// dispatch latency that looks random. Held for the lifetime of `serve`; the
/// operating system drops it if the process dies, so there is no stale PID to
/// clean up.
#[derive(Debug)]
pub struct SingleInstance {
    _file: std::fs::File,
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
            lock: root.join("agent-host.lock"),
            config_lock: root.join("config.lock"),
            root,
        }
    }

    pub fn ensure(&self) -> std::io::Result<()> {
        std::fs::create_dir_all(&self.root)
    }

    /// Hold the config write lock for the duration of one read-modify-write.
    ///
    /// Separate from `lock_single_instance`, which only `serve` takes and which
    /// answers a different question. Every mutation of `config.json` is a load,
    /// a change and a save, and the writers are in different *processes*: the
    /// daemon's worker and supervisor tasks, and every CLI subcommand — `connect`
    /// and `disconnect` most of all, which the desktop app invokes while the
    /// daemon is running. Unsynchronised, whoever saved second silently reverted
    /// the other, and the visible form of that was a pairing that appeared to
    /// have worked and then was not there.
    ///
    /// Blocking rather than `try_lock`: the critical section is a small file
    /// read and write, so waiting for it is measured in milliseconds, and
    /// failing a pairing because another task was mid-save would trade a rare
    /// race for a common one.
    fn lock_config(&self) -> anyhow::Result<std::fs::File> {
        self.ensure()?;
        let file = std::fs::OpenOptions::new()
            .create(true)
            .read(true)
            .write(true)
            .truncate(false)
            .open(&self.config_lock)?;
        // `lock`, not `try_lock`: the exclusive one that waits. See above for
        // why waiting is the right answer here and refusing is not.
        fs4::FileExt::lock(&file)?;
        Ok(file)
    }

    /// Take the single-instance lock, or explain who already holds it.
    ///
    /// `File::try_lock` is an advisory OS lock: `Err(WouldBlock)` means another
    /// process holds it, and it is released automatically when that process
    /// dies — so unlike a PID file there is nothing stale to clean up after a
    /// crash.
    pub fn lock_single_instance(&self) -> anyhow::Result<SingleInstance> {
        self.ensure()?;
        let file = std::fs::OpenOptions::new()
            .create(true)
            .read(true)
            .write(true)
            .truncate(false)
            .open(&self.lock)?;
        // fs4 rather than the std inherent method: that one is stable only
        // from 1.89 and this crate supports 1.88.
        if fs4::FileExt::try_lock(&file).is_err() {
            anyhow::bail!(
                "another Agent Host is already serving {}. Stop it first \
                 (quit Lemma, or stop the other process); running two \
                 against one workspace makes them fight over the same pairing.",
                self.root.display()
            );
        }
        Ok(SingleInstance { _file: file })
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

    /// Change the config under the write lock, so no other writer can lose it.
    ///
    /// Every mutation of `config.json` is a load, a change and a save. Doing
    /// those three unsynchronised means the last saver silently reverts whatever
    /// landed in between: a pairing erased by a `drain` that started first, a
    /// revoked target restored by a `refresh`, a `refresh_generation` bump lost
    /// so a requested re-probe never happens and nothing reports why.
    ///
    /// The closure sees a config loaded *inside* the lock, so it is reading what
    /// it is about to write to. Return `false` to leave the file untouched.
    pub fn mutate(
        paths: &HostPaths,
        change: impl FnOnce(&mut Self) -> anyhow::Result<bool>,
    ) -> anyhow::Result<Self> {
        let _guard = paths.lock_config()?;
        let mut config = Self::load_or_create(paths)?;
        if change(&mut config)? {
            config.save(paths)?;
        }
        Ok(config)
    }

    pub fn save(&self, paths: &HostPaths) -> anyhow::Result<()> {
        paths.ensure()?;
        // Unique per write. A fixed `config.json.tmp` is a second race on top of
        // the read-modify-write one: two savers interleave their bytes in one
        // file and then both rename it, so the survivor can be neither of the
        // two configs that were written.
        let temporary = paths
            .config
            .with_extension(format!("json.{}.tmp", Uuid::new_v4()));
        let mut bytes = serde_json::to_vec_pretty(self)?;
        bytes.push(b'\n');
        std::fs::write(&temporary, bytes)?;
        // The config carries host secrets; keep it owner-only.
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&temporary, std::fs::Permissions::from_mode(0o600))?;
        }
        std::fs::rename(&temporary, &paths.config).inspect_err(|_| {
            let _ = std::fs::remove_file(&temporary);
        })?;
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
                        && crate::api::is_loopback_host(target.base_url.host_str()),
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
    use tempfile::TempDir;

    #[test]
    fn concurrent_changes_do_not_lose_each_other() {
        // The bug: every mutation is load -> change -> save, and the writers are
        // in different processes -- the daemon's worker and supervisor, and the
        // `connect`/`disconnect`/`drain` subcommands the desktop app runs while
        // the daemon is up. Unsynchronised, the second saver writes a config it
        // loaded before the first one's change existed, so the first change is
        // silently gone. What that looked like was a pairing that reported
        // success and then was not there.
        let home = TempDir::new().unwrap();
        let paths = HostPaths::under(home.path());
        HostConfig::load_or_create(&paths).unwrap();

        let writers = 8;
        std::thread::scope(|scope| {
            for index in 0..writers {
                let paths = paths.clone();
                scope.spawn(move || {
                    HostConfig::mutate(&paths, |config| {
                        config.targets.push(TargetConfig {
                            target_id: Uuid::new_v4(),
                            name: format!("target-{index}"),
                            base_url: url::Url::parse(&format!("https://{index}.example/"))
                                .unwrap(),
                            host_id: Uuid::new_v4(),
                            user_id: Uuid::new_v4(),
                            host_secret: "secret".into(),
                            enabled: true,
                            allow_insecure_http: false,
                            draining: false,
                            refresh_generation: 0,
                        });
                        Ok(true)
                    })
                    .unwrap();
                });
            }
        });

        let config = HostConfig::load_or_create(&paths).unwrap();
        assert_eq!(
            config.targets.len(),
            writers,
            "every change must survive: {:?}",
            config
                .targets
                .iter()
                .map(|target| &target.name)
                .collect::<Vec<_>>()
        );
    }

    #[test]
    fn a_change_that_declines_to_change_anything_leaves_the_file_alone() {
        let home = TempDir::new().unwrap();
        let paths = HostPaths::under(home.path());
        HostConfig::load_or_create(&paths).unwrap();
        let before = std::fs::read(&paths.config).unwrap();

        HostConfig::mutate(&paths, |config| {
            config.max_runs = 99;
            Ok(false)
        })
        .unwrap();

        assert_eq!(std::fs::read(&paths.config).unwrap(), before);
    }

    #[test]
    fn saving_leaves_no_temporary_behind_for_another_writer_to_collide_with() {
        // A fixed `config.json.tmp` is a second race stacked on the first: two
        // savers interleave bytes into one file and both rename it, so the
        // survivor can be neither of the configs that were written.
        let home = TempDir::new().unwrap();
        let paths = HostPaths::under(home.path());
        HostConfig::load_or_create(&paths).unwrap();

        let leftovers: Vec<_> = std::fs::read_dir(home.path())
            .unwrap()
            .filter_map(|entry| entry.ok().map(|entry| entry.file_name()))
            .filter(|name| name.to_string_lossy().ends_with(".tmp"))
            .collect();
        assert!(
            leftovers.is_empty(),
            "temporaries left behind: {leftovers:?}"
        );
    }

    #[test]
    fn a_second_agent_host_cannot_serve_the_same_data_directory() {
        // Two hosts share one pairing: commands split between them at random,
        // and whichever exits first reports available_runs=0 and marks the
        // machine draining. The workspace then flaps and dispatch latency looks
        // random, which is exactly what it did.
        let directory = TempDir::new().unwrap();
        let paths = HostPaths::under(directory.path());

        let first = paths
            .lock_single_instance()
            .expect("first host takes the lock");

        let second = paths.lock_single_instance();
        assert!(second.is_err(), "a second host was allowed to serve");
        let message = second.unwrap_err().to_string();
        assert!(
            message.contains("already serving"),
            "unhelpful message: {message}"
        );

        // Releasing it lets the next process in, so a restart is not blocked by
        // its predecessor.
        drop(first);
        assert!(paths.lock_single_instance().is_ok());
    }

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
