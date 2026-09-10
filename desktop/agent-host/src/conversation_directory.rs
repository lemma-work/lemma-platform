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
    #[cfg(unix)]
    let created = {
        use std::os::unix::fs::DirBuilderExt;
        std::fs::DirBuilder::new().mode(0o700).create(path)
    };
    #[cfg(not(unix))]
    let created = std::fs::create_dir(path);
    match created {
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

/// `path` is the primary key, so SQLite keeps a unique index on it and both
/// queries below are ranges of that index. Nothing else is stored, and nothing
/// here needs a migration: this is the table that has always been written.
const REGISTRY_SCHEMA: &str =
    "CREATE TABLE IF NOT EXISTS directory_owners (path TEXT PRIMARY KEY, target TEXT NOT NULL)";

/// One directory, by its exact path.
const OWNER_OF: &str = "SELECT target FROM directory_owners WHERE path = ?1";

/// Everything registered beneath a directory.
const OWNERS_BENEATH: &str =
    "SELECT path, target FROM directory_owners WHERE path > ?1 AND path < ?2";

/// `a/b/c` as `a`, `a/b`, `a/b/c` -- every directory that contains it, and it.
fn ancestors(relative: &str) -> impl Iterator<Item = &str> {
    relative
        .match_indices('/')
        .map(|(index, _)| &relative[..index])
        .chain(std::iter::once(relative))
}

/// Every registered directory that overlaps `relative`, and who owns it.
///
/// Overlapping means one contains the other, so the answer is the ancestors of
/// `relative` -- itself included -- and its descendants. Both are ranges of the
/// primary key: the ancestors are at most one point lookup per path component,
/// and the descendants are a single scan bounded to the keys that begin
/// `relative/`.
///
/// This used to be `SELECT path, target` with no `WHERE` and the prefix work
/// done in Rust, which meant every conversation start read every directory the
/// workspace had ever registered. The cost of opening a conversation grew with
/// how much the workspace had been used, on the path a person is waiting on.
///
/// `0` is the byte after `/`, so the upper bound stops exactly where the
/// `relative/...` keys stop. A sibling that merely shares a textual prefix --
/// `proj` beside `project` -- sorts outside it, which is the same boundary the
/// `format!("{path}/")` comparisons drew before.
fn overlapping_owners(
    transaction: &rusqlite::Transaction<'_>,
    relative: &str,
) -> rusqlite::Result<Vec<(String, String)>> {
    use rusqlite::OptionalExtension;

    let mut found = Vec::new();
    let mut owner_of = transaction.prepare(OWNER_OF)?;
    for prefix in ancestors(relative) {
        if let Some(owner) = owner_of
            .query_row([prefix], |row| row.get::<_, String>(0))
            .optional()?
        {
            found.push((prefix.to_owned(), owner));
        }
    }
    let mut beneath = transaction.prepare(OWNERS_BENEATH)?;
    let rows = beneath.query_map([format!("{relative}/"), format!("{relative}0")], |row| {
        Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
    })?;
    for row in rows {
        found.push(row?);
    }
    Ok(found)
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
    connection.execute_batch(REGISTRY_SCHEMA)?;
    let transaction =
        connection.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
    let target = target.to_string();
    let owners = overlapping_owners(&transaction, relative)?;
    for (_, owner) in &owners {
        anyhow::ensure!(
            owner == &target,
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

    /// The registry is consulted on every conversation start, so it has to be
    /// answered from the index. A plan that says SCAN is one that reads every
    /// directory the workspace has ever registered.
    #[test]
    fn overlap_is_answered_from_the_index_rather_than_by_reading_every_row() {
        let connection = rusqlite::Connection::open_in_memory().unwrap();
        connection.execute_batch(REGISTRY_SCHEMA).unwrap();
        for (sql, parameters) in [(OWNER_OF, 1), (OWNERS_BENEATH, 2)] {
            // SQLite plans a prepared statement, so the placeholders have to
            // be bound even though nothing is read back.
            let bound = vec!["a/b"; parameters];
            let plan = connection
                .prepare(&format!("EXPLAIN QUERY PLAN {sql}"))
                .unwrap()
                .query_map(rusqlite::params_from_iter(bound), |row| {
                    row.get::<_, String>(3)
                })
                .unwrap()
                .collect::<Result<Vec<_>, _>>()
                .unwrap()
                .join("; ");
            assert!(plan.contains("SEARCH"), "{sql}\n{plan}");
            assert!(!plan.contains("SCAN"), "{sql}\n{plan}");
        }
    }

    /// The bound between "beneath" and "merely spelled similarly" is a path
    /// separator, not a string prefix. `project` is not inside `proj`, and two
    /// paired workspaces are entitled to one each.
    #[test]
    fn a_sibling_that_only_shares_a_prefix_is_not_an_overlap() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("lemma");
        prepare(&root, Uuid::new_v4(), "/workspace/proj").unwrap();
        prepare(&root, Uuid::new_v4(), "/workspace/project").unwrap();
        prepare(&root, Uuid::new_v4(), "/workspace/proj0").unwrap();
        // And the real containment is still refused.
        assert!(prepare(&root, Uuid::new_v4(), "/workspace/proj/inside").is_err());
    }

    /// Narrowing the query must not narrow what it finds. A conflict buried
    /// among directories that do not overlap is still a conflict, and a
    /// descendant registered by someone else still blocks its parent.
    #[test]
    fn a_conflict_is_still_found_among_directories_that_do_not_overlap() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("lemma");
        let mine = Uuid::new_v4();
        for index in 0..64 {
            prepare(&root, Uuid::new_v4(), &format!("/workspace/other-{index}")).unwrap();
        }
        prepare(&root, mine, "/workspace/a/b/c").unwrap();

        // An ancestor of a directory somebody else owns.
        assert!(prepare(&root, Uuid::new_v4(), "/workspace/a").is_err());
        // A descendant of one.
        assert!(prepare(&root, Uuid::new_v4(), "/workspace/a/b/c/d").is_err());
        // The same directory.
        assert!(prepare(&root, Uuid::new_v4(), "/workspace/a/b/c").is_err());
        // And its own owner is still let back in.
        assert!(prepare(&root, mine, "/workspace/a/b/c/d").is_ok());
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
