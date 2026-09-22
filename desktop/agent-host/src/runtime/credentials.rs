//! The run's Lemma credential, on disk, for the agent process to read.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

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

/// Write the run's token, replacing any previous one, and return its path.
///
/// Through a temporary file and a rename, because a refresh rewrites this path
/// while an agent may be reading it. `fs::write` truncates first, so a reader
/// that arrives mid-refresh gets an empty or half-written token and fails to
/// authenticate -- for a credential whose whole purpose is surviving a
/// refresh, that is the one moment it must not do.
pub(crate) fn write_run_token(root: &Path, run_id: Uuid, token: &str) -> std::io::Result<PathBuf> {
    let path = token_path(root, run_id);
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
        restrict(parent, 0o700)?;
    }
    let staged = path.with_extension(format!("tmp-{}", std::process::id()));
    // Restricted before it holds anything: a file that is briefly readable by
    // others is readable by others.
    std::fs::write(&staged, token)?;
    restrict(&staged, 0o600)?;
    if let Err(error) = std::fs::rename(&staged, &path) {
        let _ = std::fs::remove_file(&staged);
        return Err(error);
    }
    Ok(path)
}

/// Removes a run's token when it goes out of scope, however it goes.
///
/// A run's own cleanup is not enough. `enforce_cancellations` calls
/// `handle.abort()`, and tokio then drops the task at its current await point,
/// so the line that removes the token is never reached and a delegated
/// credential is left on disk. Drop runs on that path, and on an unwind.
pub(crate) struct RunCredential {
    root: PathBuf,
    run_id: Uuid,
}

impl RunCredential {
    pub(crate) fn new(root: &Path, run_id: Uuid) -> Self {
        Self {
            root: root.to_path_buf(),
            run_id,
        }
    }
}

impl Drop for RunCredential {
    fn drop(&mut self) {
        remove_run_token(&self.root, self.run_id);
    }
}

/// Remove a finished run's token. Best effort: a token left behind is a
/// credential left behind, but failing to delete one must not fail the run.
pub(crate) fn remove_run_token(root: &Path, run_id: Uuid) {
    let path = token_path(root, run_id);
    if let Err(error) = std::fs::remove_file(&path)
        && error.kind() != std::io::ErrorKind::NotFound
    {
        tracing::warn!(%run_id, %error, "could not remove a finished run's credential file");
    }
}

#[cfg(unix)]
fn restrict(path: &Path, mode: u32) -> std::io::Result<()> {
    use std::os::unix::fs::PermissionsExt;
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(mode))
}

#[cfg(not(unix))]
// The signature mirrors the unix version above, which genuinely can fail.
// Narrowing it here would make every caller cfg-dependent to save a branch
// that is already free.
#[allow(clippy::unnecessary_wraps)]
fn restrict(_path: &Path, _mode: u32) -> std::io::Result<()> {
    // Windows inherits the Agent Host data directory's ACL, which is already
    // the user's own.
    Ok(())
}

/// The environment a run gives its agent: the published `LEMMA_*` values, plus
/// the path to the token file when one could be written.
pub(crate) fn agent_environment(
    root: &Path,
    run_id: Uuid,
    mcp: &serde_json::Value,
    published: BTreeMap<String, String>,
) -> BTreeMap<String, String> {
    let mut environment = published;
    let Some(token) = mcp.get("token").and_then(serde_json::Value::as_str) else {
        return environment;
    };
    match write_run_token(root, run_id, token) {
        Ok(path) => {
            environment.insert(
                TOKEN_FILE_VARIABLE.to_owned(),
                path.to_string_lossy().into_owned(),
            );
        }
        Err(error) => {
            // The environment variable still carries the token, so the run
            // works; it just cannot survive a mid-run refresh.
            tracing::warn!(%run_id, %error, "could not write the run credential file");
        }
    }
    environment
}
