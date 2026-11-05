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
    /// Folders the person bound conversations to, written by the desktop shell.
    /// See `conversation_folders` for why the path is recorded here rather than
    /// carried on the run.
    pub folders: PathBuf,
    /// The folder each conversation's host workspace opened in, written by
    /// this process. See `host_exec::roots`.
    pub conversation_roots: PathBuf,
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
            folders: root.join("conversation-folders.json"),
            conversation_roots: root.join("conversation-roots.json"),
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

/// The most concurrent runs the backend will accept a claim for.
///
/// Mirrors `AgentHostCapacity.max_runs`'s `le=128`. Asserted against the
/// published contract in `tests/wire_contract.rs`.
pub const MAX_SUPPORTED_RUNS: u16 = 128;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct HostConfig {
    pub installation_id: String,
    #[serde(default, deserialize_with = "targets_skipping_unreadable")]
    pub targets: Vec<TargetConfig>,
    #[serde(default = "default_max_runs")]
    pub max_runs: u16,
    /// Whether a person chose `max_runs` (`lemma-agent-host max-runs`),
    /// rather than it being whatever default the build that wrote the file
    /// had. Only an unchosen value is lifted when the default rises
    /// (`migrate_max_runs`); a chosen one is the person's and stays.
    #[serde(default)]
    pub max_runs_chosen: bool,
    /// The host-wide switch older builds wrote. It applied to every pairing
    /// -- a hosted workspace's and a teammate's shared install's included --
    /// so it is read once, moved onto the local pairing (`TargetConfig::
    /// host_execution`), and never written again.
    #[serde(
        default,
        rename = "host_execution",
        skip_serializing_if = "std::ops::Not::not"
    )]
    pub legacy_host_execution: bool,
    /// The coding agents (adapter keys) whose person chose "Use my own skills
    /// and settings": they start as they would in the person's terminal,
    /// loading their own instructions, skills, plugins, hooks and MCP servers.
    /// Every other agent leaves those out (`acp::session_options`). A setting
    /// of this machine's, not of a pairing: it is about the agents installed
    /// here, whichever workspace runs them.
    #[serde(default, skip_serializing_if = "std::collections::BTreeSet::is_empty")]
    pub own_settings: std::collections::BTreeSet<String>,
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
    /// Someone other than this pairing's person is signed in to the app, or
    /// nobody is: take no new runs and run no commands until they are back.
    /// Set by the app (`session`), not by the person; `draining` is theirs.
    #[serde(default)]
    pub session_paused: bool,
    /// Whether the owner's Lemma agents may run commands on this computer for
    /// this pairing, under Seatbelt. Off until the owner turns it on (Settings,
    /// or `lemma-agent-host host-execution enable`), and only ever honoured on
    /// the local pairing: see `is_local_install`.
    #[serde(default)]
    pub host_execution: bool,
}

impl TargetConfig {
    /// Whether this pairing is the Lemma installed on this computer: plain
    /// HTTP, which pairing and `HostConfig::validate` allow only to a
    /// loopback address and only when opted into. Not resolved again here:
    /// being offline must not turn the local pairing into a remote one.
    ///
    /// Host execution is offered on this pairing alone. Every other pairing --
    /// a hosted workspace, a teammate's shared install -- is a server
    /// somewhere else, and a server somewhere else sending `process.start` to
    /// this Mac is exactly what the switch must never mean.
    #[must_use]
    pub fn is_local_install(&self) -> bool {
        self.allow_insecure_http && self.base_url.scheme() == "http"
    }

    /// Whether host execution is on for this pairing and this pairing can
    /// have it.
    #[must_use]
    pub fn runs_host_commands(&self) -> bool {
        self.host_execution && self.is_local_install() && !self.session_paused
    }

    /// Whether this pairing takes new runs: neither drained by its person nor
    /// paused because somebody else is signed in to the app.
    #[must_use]
    pub fn takes_work(&self) -> bool {
        !self.draining && !self.session_paused
    }
}

/// How many runs one computer takes at once unless its person chose otherwise.
///
/// Each run is one coding-agent process waiting on a model most of the time,
/// so a Mac holds many; two made the third conversation of a busy afternoon
/// queue behind the first two.
const fn default_max_runs() -> u16 {
    20
}

/// The default older builds wrote into every config they created. A file
/// still holding it, with no choice recorded, holds it only because nobody
/// changed it.
const RETIRED_DEFAULT_MAX_RUNS: u16 = 2;

const fn default_enabled() -> bool {
    true
}

impl HostConfig {
    pub fn load_or_create(paths: &HostPaths) -> anyhow::Result<Self> {
        let config = Self::load_or_create_unlocked(paths)?;
        if config.max_runs_needs_lifting() {
            // Saved, not only lifted in memory, and under the write lock: a
            // save from here without it could write back a config loaded
            // before another process's pairing landed, and lose the pairing.
            // `mutate` reloads inside the lock and lifts what it finds.
            return Self::mutate(paths, |_| Ok(false));
        }
        Ok(config)
    }

    /// `load_or_create` for a caller already holding the write lock, or one
    /// that only reads. Fixups that need no save happen here, in memory.
    fn load_or_create_unlocked(paths: &HostPaths) -> anyhow::Result<Self> {
        paths.ensure()?;
        if paths.config.exists() {
            let mut value: Self = serde_json::from_slice(&std::fs::read(&paths.config)?)?;
            value.migrate_host_execution();
            value.migrate_retired_local_hosts();
            return Ok(value);
        }
        let config = Self {
            installation_id: Uuid::new_v4().to_string(),
            targets: Vec::new(),
            max_runs: default_max_runs(),
            legacy_host_execution: false,
            max_runs_chosen: false,
            own_settings: std::collections::BTreeSet::new(),
        };
        config.save(paths)?;
        Ok(config)
    }

    fn max_runs_needs_lifting(&self) -> bool {
        !self.max_runs_chosen && self.max_runs == RETIRED_DEFAULT_MAX_RUNS
    }

    /// Lift a concurrency limit nobody chose to the current default. Returns
    /// whether it changed anything.
    ///
    /// Every config an older build created was written with that build's
    /// default of two, so on disk it reads exactly like a choice. A file
    /// holding two with no choice recorded is the old default; one holding
    /// any other number, or holding two by choice, is left alone.
    pub(crate) fn migrate_max_runs(&mut self) -> bool {
        if !self.max_runs_needs_lifting() {
            return false;
        }
        self.max_runs = default_max_runs();
        true
    }

    /// Set the concurrency limit as the person's own choice, which no later
    /// change of default overrides.
    pub fn choose_max_runs(&mut self, max_runs: u16) -> anyhow::Result<()> {
        anyhow::ensure!(
            (1..=MAX_SUPPORTED_RUNS).contains(&max_runs),
            "max_runs must be between 1 and {MAX_SUPPORTED_RUNS}"
        );
        self.max_runs = max_runs;
        self.max_runs_chosen = true;
        Ok(())
    }

    /// Move the host-wide switch an older build wrote onto the local pairing.
    /// In memory; the next save writes it that way.
    fn migrate_host_execution(&mut self) {
        if !std::mem::take(&mut self.legacy_host_execution) {
            return;
        }
        for target in &mut self.targets {
            if target.is_local_install() {
                target.host_execution = true;
            }
        }
    }

    /// Move a local pairing off a hostname Lemma Desktop no longer serves.
    ///
    /// Desktop served itself on the public loopback wildcard
    /// `app.127.0.0.1.sslip.io` for a while, and now serves `app.lemma.localhost`
    /// on the same ports. A pairing recorded under the old name still reached
    /// the same backend, but only while public DNS answered -- and the app now
    /// matches pairings against its own origin, so the old spelling read as a
    /// pairing with somebody else. Only the local pairing, and only the host:
    /// scheme, port and path are kept exactly. In memory; the next save writes
    /// it that way.
    pub(crate) fn migrate_retired_local_hosts(&mut self) {
        const RETIRED: [&str; 3] = [
            "app.127.0.0.1.sslip.io",
            "api.127.0.0.1.sslip.io",
            "127.0.0.1.sslip.io",
        ];
        for target in &mut self.targets {
            let retired = target
                .base_url
                .host_str()
                .is_some_and(|host| RETIRED.contains(&host.to_ascii_lowercase().as_str()));
            if retired && target.is_local_install() {
                let _ = target.base_url.set_host(Some("app.lemma.localhost"));
            }
        }
    }

    /// Record who is signed in to the app on `url`: pause that server's
    /// pairings of anybody else, and resume that person's own. Nobody signed in
    /// pauses them all. Returns how many changed.
    pub fn apply_session(&mut self, url: &Url, user: Option<Uuid>) -> usize {
        let mut changed = 0;
        for target in &mut self.targets {
            if target.base_url.origin() != url.origin() {
                continue;
            }
            let paused = user != Some(target.user_id);
            if target.session_paused != paused {
                target.session_paused = paused;
                changed += 1;
            }
        }
        changed
    }

    /// The local pairings, whose host-execution switch is this machine's.
    pub fn local_targets_mut(&mut self) -> impl Iterator<Item = &mut TargetConfig> {
        self.targets
            .iter_mut()
            .filter(|target| target.is_local_install())
    }

    /// Whether host execution is on for any pairing that can have it.
    #[must_use]
    pub fn host_execution(&self) -> bool {
        self.targets.iter().any(TargetConfig::runs_host_commands)
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
        let mut config = Self::load_or_create_unlocked(paths)?;
        let migrated = config.migrate_max_runs();
        if change(&mut config)? || migrated {
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
        // The backend caps capacity at 128. A `hello` frame that claims more
        // fails validation and the backend closes the link as a protocol
        // violation, so a larger number here does not buy concurrency -- every
        // reconnect is refused the same way and the host stays offline for
        // ever, with nothing on screen naming the config field that did it.
        anyhow::ensure!(
            self.max_runs <= MAX_SUPPORTED_RUNS,
            "max_runs must be at most {MAX_SUPPORTED_RUNS}; Lemma refuses a larger claim"
        );
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
                        && crate::link::is_loopback_host(target.base_url.host_str()),
                    "target {} must use HTTPS (HTTP is allowed only for an explicitly opted-in loopback target)",
                    target.name
                );
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests;
