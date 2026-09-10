//! File cleanup after the confirmed runtime and credential reset has finished.
use std::fs;
use std::io;
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};

use serde::Serialize;
#[derive(Clone, Copy, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum RecoveryOutcome {
    Cancelled,
    Started,
    Completed,
}

pub struct RecoveryGuard<'a>(&'a AtomicBool);

impl<'a> RecoveryGuard<'a> {
    pub fn enter(flag: &'a AtomicBool) -> io::Result<Self> {
        flag.compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .map_err(|_| io::Error::other("installation cleanup is already running"))?;
        Ok(Self(flag))
    }
}

impl Drop for RecoveryGuard<'_> {
    fn drop(&mut self) {
        self.0.store(false, Ordering::Release);
    }
}

/// Each known directory is detached before deletion. An interrupted deletion
/// remains a cleanup target on retry and cannot be loaded as a valid install.
pub fn clear_reinstall_files(support: &Path, agent_host: &Path) -> io::Result<()> {
    if fs::symlink_metadata(support.join("runtime"))
        .is_ok_and(|metadata| metadata.file_type().is_symlink())
    {
        return Err(io::Error::other("the runtime directory is a symbolic link; remove the link or restore its original location before cleanup"));
    }
    remove_owned_path(&support.join("runtime/releases"))?;
    remove_owned_path(agent_host)?;
    remove_owned_path(&support.join("desktop-config.json"))?;
    remove_owned_path(&support.join("recovery-mode"))?;
    Ok(())
}

fn remove_owned_path(path: &Path) -> io::Result<()> {
    let name = path
        .file_name()
        .ok_or_else(|| io::Error::other("cleanup target has no name"))?;
    let mut retired_name = std::ffi::OsString::from(".");
    retired_name.push(name);
    retired_name.push(".cleanup");
    let retired = path.with_file_name(retired_name);
    remove_detached(&retired)?;
    match fs::symlink_metadata(path) {
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(()),
        Err(error) => return Err(error),
        Ok(_) => {}
    }
    fs::rename(path, &retired)?;
    remove_detached(&retired)
}

fn remove_detached(path: &Path) -> io::Result<()> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.is_dir() && !metadata.file_type().is_symlink() => {
            fs::remove_dir_all(path)
        }
        Ok(_) => fs::remove_file(path),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cleanup_admission_is_exclusive_and_failure_releases_it() {
        let flag = AtomicBool::new(false);
        let guard = RecoveryGuard::enter(&flag).unwrap();
        assert!(RecoveryGuard::enter(&flag).is_err());
        assert!(flag.load(Ordering::Acquire));
        drop(guard);
        assert!(!flag.load(Ordering::Acquire));
        assert!(RecoveryGuard::enter(&flag).is_ok());
    }

    #[cfg(unix)]
    #[test]
    fn a_redirected_runtime_parent_cannot_delete_an_external_project() {
        let root = tempfile::tempdir().unwrap();
        let support = root.path().join("Lemma");
        let external = root.path().join("external");
        fs::create_dir_all(external.join("releases")).unwrap();
        fs::create_dir(&support).unwrap();
        fs::write(external.join("releases/work"), b"keep").unwrap();
        std::os::unix::fs::symlink(&external, support.join("runtime")).unwrap();
        assert!(clear_reinstall_files(&support, &support.join("agent-host")).is_err());
        assert_eq!(fs::read(external.join("releases/work")).unwrap(), b"keep");
    }

    #[test]
    fn clean_reinstall_removes_old_pairing_and_corrupt_config_but_keeps_logs_and_projects() {
        let root = tempfile::tempdir().unwrap();
        let support = root.path().join("Lemma ü");
        let agent = support.join("agent-host");
        for path in [
            support.join("runtime/releases/broken"),
            agent.clone(),
            support.join("desktop-config.json"),
        ] {
            fs::create_dir_all(&path).unwrap();
            fs::write(path.join("state"), b"old installation").unwrap();
        }
        fs::write(support.join("runtime/install.log"), b"diagnostics").unwrap();
        let project = root.path().join("my-project");
        fs::create_dir(&project).unwrap();
        fs::write(project.join("work.txt"), b"user project").unwrap();
        clear_reinstall_files(&support, &agent).unwrap();
        assert!(!agent.exists());
        assert!(!support.join("runtime/releases").exists());
        assert!(!support.join("desktop-config.json").exists());
        assert_eq!(
            fs::read(support.join("runtime/install.log")).unwrap(),
            b"diagnostics"
        );
        assert_eq!(fs::read(project.join("work.txt")).unwrap(), b"user project");
        clear_reinstall_files(&support, &agent).unwrap();
    }

    #[test]
    fn retry_finishes_a_previously_detached_installation() {
        let root = tempfile::tempdir().unwrap();
        let leftovers = root.path().join("runtime/.releases.cleanup/partial");
        fs::create_dir_all(&leftovers).unwrap();
        fs::write(leftovers.join("state"), b"partial removal").unwrap();
        clear_reinstall_files(root.path(), &root.path().join("agent-host")).unwrap();
        assert!(!root.path().join("runtime/.releases.cleanup").exists());
    }

    #[cfg(unix)]
    #[test]
    fn cleanup_unlinks_a_replaced_directory_without_following_it() {
        let root = tempfile::tempdir().unwrap();
        let outside = root.path().join("external-project");
        fs::create_dir(&outside).unwrap();
        fs::write(outside.join("work"), b"keep me").unwrap();
        let agent = root.path().join("agent-host");
        std::os::unix::fs::symlink(&outside, &agent).unwrap();
        clear_reinstall_files(root.path(), &agent).unwrap();
        assert!(fs::symlink_metadata(&agent).is_err());
        assert_eq!(fs::read(outside.join("work")).unwrap(), b"keep me");
    }
}
