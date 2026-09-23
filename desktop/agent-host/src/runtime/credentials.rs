//! The run's Lemma credential, on disk, for the agent process to read.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, PoisonError};

use uuid::Uuid;

/// Where a run's token lives, under the Agent Host's own private directory.
///
/// Deliberately not the run's working directory. That may be a folder the
/// person picked on this computer -- their own repository -- and a credential
/// written there is one `git add .` away from being published.
const CREDENTIAL_DIRECTORY: &str = "run-credentials";

/// The variable naming the file. Read in preference to `LEMMA_TOKEN` by the
/// Lemma SDK, because the process environment cannot be rewritten after spawn
/// and a run may outlive the ~1h credential it started with. Lemma refreshes
/// the credential mid-run; rewriting this file is how that reaches an agent
/// that has already started.
pub(crate) const TOKEN_FILE_VARIABLE: &str = "LEMMA_TOKEN_FILE";

fn token_path(root: &Path, run_id: Uuid) -> PathBuf {
    root.join(CREDENTIAL_DIRECTORY)
        .join(format!("{run_id}.token"))
}

/// One run's credential file, and whether the run is still alive to use it.
///
/// Shared between the run task and the command handler that applies
/// `REFRESH_CREDENTIAL`, and the lock is the point. An aborted run's journal
/// row is not terminal until `reap_finished` catches up, so a refresh can
/// arrive after the run has retired its file. Under the lock a retire and a
/// rewrite cannot interleave, and once retired a rewrite is refused -- nothing
/// would remove the file a late rewrite left behind.
///
/// The same lock serializes the first write against a refresh, which is what
/// lets the staging file be named per process rather than per write.
pub(crate) struct RunCredential {
    root: PathBuf,
    run_id: Uuid,
    live: Mutex<bool>,
}

impl RunCredential {
    pub(crate) fn new(root: &Path, run_id: Uuid) -> Arc<Self> {
        Arc::new(Self {
            root: root.to_path_buf(),
            run_id,
            live: Mutex::new(true),
        })
    }

    /// Write the token, unless the run has already been retired.
    ///
    /// `Ok(None)` is the retired case: not an error, because a refresh racing
    /// the end of its run has nothing left to refresh.
    pub(crate) fn write(&self, token: &str) -> std::io::Result<Option<PathBuf>> {
        let live = self.live.lock().unwrap_or_else(PoisonError::into_inner);
        if !*live {
            return Ok(None);
        }
        write_token_file(&token_path(&self.root, self.run_id), token).map(Some)
    }

    /// Remove the file and refuse every later write.
    fn retire(&self) {
        let mut live = self.live.lock().unwrap_or_else(PoisonError::into_inner);
        *live = false;
        let path = token_path(&self.root, self.run_id);
        if let Err(error) = std::fs::remove_file(&path)
            && error.kind() != std::io::ErrorKind::NotFound
        {
            let run_id = self.run_id;
            tracing::warn!(%run_id, %error, "could not remove a finished run's credential file");
        }
    }
}

/// Retires a run's credential when the run's task goes away, however it goes.
///
/// A run's own cleanup is not enough. `enforce_cancellations` calls
/// `handle.abort()`, and tokio then drops the task at its current await point,
/// so a line that removes the token at the end of the run body is never
/// reached. Drop runs on that path, and on an unwind.
pub(crate) struct RetireOnDrop(pub(crate) Arc<RunCredential>);

impl Drop for RetireOnDrop {
    fn drop(&mut self) {
        self.0.retire();
    }
}

/// Replace the token file atomically.
///
/// Through a temporary file and a rename, because a refresh rewrites this path
/// while an agent may be reading it, and `fs::write` truncates first: a reader
/// arriving mid-refresh would get an empty or partial token.
fn write_token_file(path: &Path, token: &str) -> std::io::Result<PathBuf> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
        restrict_directory(parent)?;
    }
    let staged = path.with_extension(format!("tmp-{}", std::process::id()));
    // A staging file left by a write that died half-way would make
    // `create_new` refuse every write after it.
    let _ = std::fs::remove_file(&staged);
    let written =
        write_private(&staged, token.as_bytes()).and_then(|()| std::fs::rename(&staged, path));
    if let Err(error) = written {
        let _ = std::fs::remove_file(&staged);
        return Err(error);
    }
    Ok(path.to_path_buf())
}

/// Create a file that is owner-only from the moment it exists.
///
/// Created with its mode rather than chmod-ed afterwards: a token written
/// first and restricted second sits at the umask's permissions in between.
#[cfg(unix)]
fn write_private(path: &Path, contents: &[u8]) -> std::io::Result<()> {
    use std::io::Write;
    use std::os::unix::fs::OpenOptionsExt;
    let mut file = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(path)?;
    file.write_all(contents)?;
    file.sync_all()
}

#[cfg(not(unix))]
fn write_private(path: &Path, contents: &[u8]) -> std::io::Result<()> {
    // Windows inherits the Agent Host data directory's ACL, which is already
    // the user's own.
    std::fs::write(path, contents)
}

#[cfg(unix)]
fn restrict_directory(path: &Path) -> std::io::Result<()> {
    use std::os::unix::fs::PermissionsExt;
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o700))
}

#[cfg(not(unix))]
// The signature mirrors the unix version above, which genuinely can fail.
// Narrowing it here would make every caller cfg-dependent to save a branch
// that is already free.
#[allow(clippy::unnecessary_wraps)]
fn restrict_directory(_path: &Path) -> std::io::Result<()> {
    Ok(())
}

/// The environment a run gives its agent: the published `LEMMA_*` values, plus
/// the path to the token file when one could be written.
pub(crate) fn agent_environment(
    credential: &RunCredential,
    mcp: &serde_json::Value,
    published: BTreeMap<String, String>,
) -> BTreeMap<String, String> {
    let mut environment = published;
    let Some(token) = mcp.get("token").and_then(serde_json::Value::as_str) else {
        return environment;
    };
    match credential.write(token) {
        Ok(Some(path)) => {
            environment.insert(
                TOKEN_FILE_VARIABLE.to_owned(),
                path.to_string_lossy().into_owned(),
            );
        }
        Ok(None) => {}
        Err(error) => {
            // The environment variable still carries the token, so the run
            // works; it just cannot survive a mid-run refresh.
            let run_id = credential.run_id;
            tracing::warn!(%run_id, %error, "could not write the run credential file");
        }
    }
    environment
}
