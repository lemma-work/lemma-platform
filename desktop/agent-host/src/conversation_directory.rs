//! Resolve a persisted workspace cwd without accepting remote host paths.

use std::path::{Path, PathBuf};

use uuid::Uuid;

pub fn workspace_root() -> anyhow::Result<PathBuf> {
    let root = std::env::var_os("LEMMA_AGENT_HOST_WORKSPACE_ROOT")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(|home| PathBuf::from(home).join("lemma")))
        .or_else(|| std::env::var_os("USERPROFILE").map(|home| PathBuf::from(home).join("lemma")))
        .ok_or_else(|| anyhow::anyhow!("cannot determine the Lemma working directory"))?;
    anyhow::ensure!(root.is_absolute(), "Lemma workspace root must be absolute");
    Ok(root)
}

fn suffix(cwd: &str) -> anyhow::Result<&str> {
    let suffix = cwd
        .strip_prefix("/workspace/")
        .ok_or_else(|| anyhow::anyhow!("conversation cwd must be beneath /workspace"))?;
    anyhow::ensure!(
        suffix.len() <= 4096
            && suffix.split('/').all(|part| {
                !part.is_empty()
                    && part != "."
                    && part != ".."
                    && part != ".lemma"
                    && !part.contains(['\\', ':'])
                    && !part.chars().any(char::is_control)
            }),
        "conversation cwd contains an unsafe path component"
    );
    Ok(suffix)
}

fn directory(path: &Path) -> anyhow::Result<()> {
    let mut builder = std::fs::DirBuilder::new();
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        builder.mode(0o700);
    }
    match builder.create(path) {
        Ok(()) => (),
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => (),
        Err(error) => return Err(error.into()),
    }
    let metadata = std::fs::symlink_metadata(path)?;
    anyhow::ensure!(
        metadata.is_dir() && !metadata.file_type().is_symlink(),
        "conversation directory cannot contain symbolic links or files: {}",
        path.display()
    );
    Ok(())
}

/// Bind paths to a paired workspace before opening an agent. Conversations
/// within the same workspace can share their parent's cwd, just as in a VM.
/// This registry prevents accidental collisions; it is not an OS sandbox.
pub fn prepare(root: &Path, target: Uuid, cwd: &str) -> anyhow::Result<PathBuf> {
    let relative = suffix(cwd)?;
    anyhow::ensure!(root.is_absolute(), "Lemma workspace root must be absolute");
    directory(root)?;
    let state = root.join(".lemma");
    directory(&state)?;
    let database = state.join("directories.sqlite3");
    if let Ok(metadata) = std::fs::symlink_metadata(&database) {
        anyhow::ensure!(
            metadata.is_file() && !metadata.file_type().is_symlink(),
            "invalid directory registry"
        );
    }
    let mut connection = rusqlite::Connection::open(database)?;
    connection.busy_timeout(std::time::Duration::from_secs(5))?;
    connection.execute_batch(
        "CREATE TABLE IF NOT EXISTS directory_owners (path TEXT PRIMARY KEY, target TEXT NOT NULL)",
    )?;
    let transaction =
        connection.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
    let target = target.to_string();
    let owners = transaction
        .prepare("SELECT path, target FROM directory_owners")?
        .query_map([], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
        })?
        .collect::<Result<Vec<_>, _>>()?;
    for (path, owner) in &owners {
        let overlapping = path == relative
            || relative.starts_with(&format!("{path}/"))
            || path.starts_with(&format!("{relative}/"));
        anyhow::ensure!(
            !overlapping || owner == &target,
            "conversation directory belongs to another paired workspace"
        );
    }
    let path = root.join(relative);
    if !owners.iter().any(|(owned, owner)| {
        owner == &target && (owned == relative || relative.starts_with(&format!("{owned}/")))
    }) && path.exists()
    {
        anyhow::ensure!(
            std::fs::read_dir(&path)?.next().is_none(),
            "conversation directory already contains unclaimed files; choose another conversation cwd"
        );
    }
    let mut component_path = root.to_path_buf();
    for part in relative.split('/') {
        component_path.push(part);
        directory(&component_path)?;
    }
    transaction.execute(
        "INSERT OR IGNORE INTO directory_owners (path, target) VALUES (?1, ?2)",
        [relative, &target],
    )?;
    transaction.commit()?;
    Ok(path)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn saved_suffix_and_files_survive_new_runs_and_host_restarts() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("lemma");
        let target = Uuid::new_v4();
        let cwd = "/workspace/c/2026-09-07/Δ project";
        let first = prepare(&root, target, cwd).unwrap();
        assert_eq!(first, root.join("c/2026-09-07/Δ project"));
        std::fs::write(first.join("work.txt"), "preserved").unwrap();
        assert_eq!(prepare(&root, target, cwd).unwrap(), first);
        assert_eq!(
            std::fs::read_to_string(first.join("work.txt")).unwrap(),
            "preserved"
        );
        assert!(prepare(&root, target, "/workspace/c/2026-09-07/other").is_ok());
        assert!(prepare(&root, Uuid::new_v4(), cwd).is_err());
        assert!(prepare(&root, Uuid::new_v4(), "/workspace/c").is_err());
        assert!(
            prepare(
                &root,
                Uuid::new_v4(),
                "/workspace/c/2026-09-07/Δ project/child"
            )
            .is_err()
        );
    }

    #[test]
    fn unsafe_paths_fail_before_creating_anything() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("lemma");
        for cwd in [
            "/workspace",
            "/workspace/",
            "/etc",
            "/workspace/../escape",
            "/workspace/a/./b",
            "/workspace//b",
            "/workspace/a\\b",
            "/workspace/C:drive",
            "/workspace/a\nb",
            "/workspace/.lemma/state",
        ] {
            assert!(prepare(&root, Uuid::new_v4(), cwd).is_err(), "{cwd}");
            assert!(!root.exists());
        }
    }

    #[test]
    fn existing_user_files_are_not_claimed() {
        let temp = tempfile::tempdir().unwrap();
        let project = temp.path().join("project");
        std::fs::create_dir(&project).unwrap();
        std::fs::write(project.join("work.txt"), "mine").unwrap();
        assert!(prepare(temp.path(), Uuid::new_v4(), "/workspace/project").is_err());
        assert_eq!(
            std::fs::read_to_string(project.join("work.txt")).unwrap(),
            "mine"
        );
    }

    #[cfg(unix)]
    #[test]
    fn symlink_escape_is_rejected_on_first_use_and_reopen() {
        let temp = tempfile::tempdir().unwrap();
        let outside = tempfile::tempdir().unwrap();
        let target = Uuid::new_v4();
        let project = prepare(temp.path(), target, "/workspace/project").unwrap();
        std::fs::remove_dir(&project).unwrap();
        std::os::unix::fs::symlink(outside.path(), project).unwrap();
        assert!(prepare(temp.path(), target, "/workspace/project/child").is_err());
        assert!(!outside.path().join("child").exists());
        assert!(prepare(temp.path(), target, "/workspace/project").is_err());
    }
}
