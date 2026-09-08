//! Return this installation to the state of one that has never run.
//!
//! The second of the two reset tiers. Tier 1 (`local.reset-data`) destroys what
//! the user made and keeps the downloaded runtime, the operator's configuration
//! and their stored secrets; this destroys all of it.
//!
//! It is a subcommand rather than a daemon verb for two independent reasons,
//! and either one alone would be enough:
//!
//! - It has to work when `Daemon::new` cannot be constructed. That is the state
//!   it exists for: a control token that will not parse, an operator config
//!   from a build that no longer runs, an infrastructure secret that no longer
//!   opens the database. A verb on a daemon that cannot start is not reachable.
//! - The OS credential vault keys each item's access control to the code
//!   identity that created it -- `work.lemma.locald`, fixed by the `Info.plist`
//!   linked into this binary. A delete issued from the Tauri shell is a
//!   different program as far as the vault is concerned.

use std::io::{self, Write};
// Both managed-guest reclaims name executable paths; no other platform has one.
#[cfg(any(target_os = "macos", windows))]
use std::path::Path;

use serde_json::json;

use crate::operator_config::{purge_secrets, recover_install_id};
use crate::paths::LocalPaths;

/// Wipe this installation, printing a JSON summary for the shell to read.
///
/// Never constructs a `Daemon`. Nothing here may depend on anything that could
/// itself be what is broken.
pub fn reset_install(paths: LocalPaths) -> io::Result<()> {
    let summary = perform_reset(paths)?;
    let mut stdout = io::stdout();
    writeln!(stdout, "{}", serde_json::to_string(&summary)?)?;
    stdout.flush()
}

/// The reset itself, returning what it did rather than printing it.
///
/// Split from `reset_install` so a test can assert on the summary instead of
/// scraping stdout -- the summary is a contract the shell reads, and getting a
/// field wrong is silent.
fn perform_reset(paths: LocalPaths) -> io::Result<serde_json::Value> {
    perform_reset_with(paths, &SystemReset)
}

trait ResetEnvironment {
    fn reclaim_vm(&self, paths: &LocalPaths) -> io::Result<()>;
    fn purge_credentials(&self, install_id: &str) -> Vec<String>;
}

struct SystemReset;

impl ResetEnvironment for SystemReset {
    fn reclaim_vm(&self, paths: &LocalPaths) -> io::Result<()> {
        use interprocess::local_socket::prelude::*;
        if LocalSocketStream::connect(paths.socket_name()?).is_ok() {
            return Err(io::Error::other("the installation's background service is still running; quit it or restart this computer before cleanup"));
        }
        crate::host_process::reclaim_persisted_installation_processes(&paths.root)?;
        reclaim_running_vm(paths)
    }

    fn purge_credentials(&self, install_id: &str) -> Vec<String> {
        purge_secrets(install_id)
    }
}

fn perform_reset_with(
    paths: LocalPaths,
    environment: &impl ResetEnvironment,
) -> io::Result<serde_json::Value> {
    for path in [&paths.root, &paths.root.join("operator-config.json")] {
        if std::fs::symlink_metadata(path).is_ok_and(|metadata| metadata.file_type().is_symlink()) {
            return Err(io::Error::other("local state is redirected by a symbolic link; restore its original location before cleanup"));
        }
    }
    let mut summary = json!({
        "root": paths.root.display().to_string(),
        "vm_reclaim_attempted": false,
        // Whether the sweep ran, not how many items it found. The vault
        // reports deleting a secret that was never there as success, so a
        // count here would claim 19 removals on an installation that had
        // stored none.
        "secrets_swept": false,
        "secret_failures": [],
        "install_id_recovered": false,
        "root_removed": false,
    });

    // 1. Nothing may still be holding the data disk. The daemon that owned this
    //    VM may be long gone -- that is the ordinary case here -- so the marker
    //    on disk is the only way to find the helper, and it is verified by pid,
    //    executable and start identity before anything is signalled.
    // Reported as attempted-without-error, which is not the same as "a VM was
    // reclaimed". The macOS path returns `Ok(())` when there was nothing
    // running, and the Windows stub returns it having done nothing at all. The
    // neighbouring `secrets_swept` is careful about exactly this distinction,
    // and this field was not -- a summary that says `true` where it means "no
    // error" is worse than no field, because somebody reads it as evidence.
    environment.reclaim_vm(&paths).map_err(|error| io::Error::other(format!(
        "Could not stop this installation's runtime: {error}. Its cleanup records have been kept. Close Lemma processes for this installation and retry."
    )))?;
    summary["vm_reclaim_attempted"] = json!(true);

    // 2. The keychain, BEFORE the file that names it.
    //
    //    Every secret is stored under `{install_id}:{name}`, and `keyring`
    //    needs the account name to address an entry. Delete the state directory
    //    first and the id is gone with it -- leaving 19 items in the user's
    //    login keychain that nothing can ever find again, and that cannot even
    //    be enumerated to clean up by hand. This ordering is the whole reason
    //    the steps are numbered.
    let config_path = paths.root.join("operator-config.json");
    if let Some(install_id) = recover_install_id(&config_path) {
        summary["install_id_recovered"] = json!(true);
        let failures = environment.purge_credentials(&install_id);
        if !failures.is_empty() {
            return Err(io::Error::other(format!(
                "Could not remove stored credentials: {}. The installation identity has been kept so cleanup can be retried after unlocking the operating-system credential store.",
                failures.join("; ")
            )));
        }
        summary["secrets_swept"] = json!(true);
        summary["secret_failures"] = json!(failures);
    }

    // 3. The state directory, which carries the data disk with it: control
    //    token, state, operator config, both secret files, ports, the process
    //    ledger, logs, and `runtime/macos/data.raw`.
    if paths.root.exists() {
        std::fs::remove_dir_all(&paths.root)?;
    }
    // Reported last, so every field describes what actually happened rather
    // than what was about to be attempted.
    summary["root_removed"] = json!(!paths.root.exists());
    Ok(summary)
}

/// Terminate a VM helper this installation left running, if there is one.
///
/// Identity-verified rather than matched by name: `pkill -x lemma-vz` would
/// also kill a developer's separate dev-root VM, and a reset of one
/// installation must not touch another.
#[cfg(target_os = "macos")]
fn reclaim_running_vm(paths: &LocalPaths) -> io::Result<()> {
    use lemma_runtime_manager::{ManagedRuntime, ManagedRuntimeConfig, DEFAULT_WSL_DISTRIBUTION};

    // A bare runtime, only to reach the reclaim. The executables named here are
    // not run: `reclaim_owned_macos_vm` reads the marker this installation
    // wrote and verifies whatever is running against it.
    let runtime = ManagedRuntime::new(ManagedRuntimeConfig {
        wsl_distribution: DEFAULT_WSL_DISTRIBUTION.to_owned(),
        local_root: paths.root.clone(),
        artifact_root: paths.root.join("runtime"),
        bridge_executable: Path::new("lemma-runtime").to_path_buf(),
        vz_executable: Path::new("lemma-vz").to_path_buf(),
    })?;
    runtime.reclaim_owned_macos_vm_for_reset()
}

/// Windows: unregister the private distribution, which is where the data is.
///
/// This used to be a no-op with a comment saying the path "is not wired up
/// yet" -- while the dialog above it promised "Everything Lemma keeps on this
/// PC is deleted: your pods and files, your AI provider settings and stored
/// keys, and the downloaded runtime". Removing the state directory leaves the
/// `LemmaRuntime` distribution and its multi-gigabyte `ext4.vhdx` registered
/// and full, and the next install reuses the same name -- so "Start Over"
/// returned to the old data, having said it had deleted it.
///
/// `unregister_windows_guest` existed on the manager the whole time; nothing
/// called it from here.
#[cfg(windows)]
fn reclaim_running_vm(paths: &LocalPaths) -> io::Result<()> {
    use lemma_runtime_manager::{ManagedRuntime, ManagedRuntimeConfig};

    let runtime = ManagedRuntime::new(ManagedRuntimeConfig {
        wsl_distribution: crate::managed_runtime::wsl_distribution_for(&paths.root),
        local_root: paths.root.clone(),
        artifact_root: paths.root.join("runtime"),
        bridge_executable: Path::new("lemma-runtime").to_path_buf(),
        wsl_executable: std::env::var_os("LEMMA_LOCALD_WSL_BIN")
            .map(std::path::PathBuf::from)
            .unwrap_or_else(|| Path::new("wsl.exe").to_path_buf()),
    })?;
    runtime.unregister_windows_guest()
}

#[cfg(not(any(target_os = "macos", windows)))]
fn reclaim_running_vm(_paths: &LocalPaths) -> io::Result<()> {
    // No managed guest on any other platform, so there is nothing to reclaim.
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[derive(Default)]
    struct ResetFixture {
        vm_failure: bool,
        credential_failure: bool,
    }

    impl ResetEnvironment for ResetFixture {
        fn reclaim_vm(&self, _paths: &LocalPaths) -> io::Result<()> {
            if self.vm_failure {
                Err(io::Error::other("VM is still running"))
            } else {
                Ok(())
            }
        }
        fn purge_credentials(&self, _install_id: &str) -> Vec<String> {
            if self.credential_failure {
                vec!["vault locked".into()]
            } else {
                Vec::new()
            }
        }
    }

    #[test]
    fn failed_vm_cleanup_keeps_the_installation_available_for_retry() {
        let root = tempdir().unwrap();
        let paths = LocalPaths::new(root.path().join("locald"));
        paths.ensure().unwrap();
        std::fs::write(paths.root.join("data.raw"), b"recoverable data").unwrap();
        let result = perform_reset_with(
            paths.clone(),
            &ResetFixture {
                vm_failure: true,
                credential_failure: false,
            },
        );
        assert!(
            result.is_err(),
            "cleanup must not claim success: {result:?}"
        );
        assert_eq!(
            std::fs::read(paths.root.join("data.raw")).unwrap(),
            b"recoverable data"
        );
    }

    #[test]
    fn failed_vault_cleanup_keeps_the_identity_needed_to_retry() {
        let root = tempdir().unwrap();
        let paths = LocalPaths::new(root.path().join("locald"));
        paths.ensure().unwrap();
        let config = paths.root.join("operator-config.json");
        std::fs::write(
            &config,
            r#"{"install_id":"0123456789abcdef0123456789abcdef"}"#,
        )
        .unwrap();
        let result = perform_reset_with(
            paths.clone(),
            &ResetFixture {
                vm_failure: false,
                credential_failure: true,
            },
        );
        assert!(
            result.is_err(),
            "failed vault cleanup must be actionable: {result:?}"
        );
        assert!(recover_install_id(&config).is_some());
        perform_reset_with(
            paths.clone(),
            &ResetFixture {
                vm_failure: false,
                credential_failure: false,
            },
        )
        .unwrap();
        assert!(!paths.root.exists());
    }

    /// A reset removes the state directory, data disk included, and says so.
    ///
    /// The summary is a contract the shell reads, and it was wrong the first
    /// time this ran: `root_removed` was serialized *before* the removal, so it
    /// reported `false` on a reset that had in fact succeeded. A field that
    /// describes an intention rather than an outcome is worse than no field.
    #[test]
    fn resetting_removes_the_whole_state_directory_and_reports_it() {
        let root = tempdir().unwrap();
        let paths = LocalPaths::new(root.path().join("locald"));
        paths.ensure().unwrap();
        std::fs::write(&paths.token, "a".repeat(64)).unwrap();
        std::fs::create_dir_all(paths.root.join("runtime/macos")).unwrap();
        std::fs::write(paths.root.join("runtime/macos/data.raw"), b"database").unwrap();

        let summary = perform_reset_with(paths.clone(), &ResetFixture::default()).unwrap();

        assert!(!paths.root.exists(), "nothing of the installation survives");
        assert_eq!(
            summary["root_removed"], true,
            "the summary must describe the outcome, not the intention"
        );
    }

    /// An installation that stored no secrets does not claim any were removed.
    ///
    /// The vault reports deleting a secret that was never there as success, so
    /// a naive count would tell every user 19 secrets had been purged from a
    /// machine that had stored none.
    #[test]
    fn a_reset_does_not_claim_to_have_removed_secrets_it_never_found() {
        let root = tempdir().unwrap();
        let paths = LocalPaths::new(root.path().join("locald"));
        paths.ensure().unwrap();

        let summary = perform_reset_with(paths, &ResetFixture::default()).unwrap();

        assert_eq!(
            summary["install_id_recovered"], false,
            "there is no config, so there is no identity to sweep under"
        );
        assert_eq!(summary["secrets_swept"], false);
        assert!(summary.get("secrets_removed").is_none());
    }

    /// Resetting a machine that never ran Lemma is not an error.
    ///
    /// This is reachable: the button exists precisely because things are
    /// broken, and "broken" includes a state directory somebody already
    /// deleted by hand.
    #[test]
    fn resetting_an_installation_that_is_already_gone_succeeds() {
        let root = tempdir().unwrap();
        let paths = LocalPaths::new(root.path().join("never-ran"));
        perform_reset_with(paths, &ResetFixture::default()).unwrap();
    }

    /// The identity is read before the file naming it is destroyed.
    ///
    /// Getting this backwards strands all 19 keychain items under an id nothing
    /// will ever mint again -- invisible to the app and not enumerable to clean
    /// up. The test asserts the recovery happened, which can only be true if it
    /// ran while the file still existed.
    #[test]
    fn the_installation_identity_is_recovered_before_the_config_is_deleted() {
        let root = tempdir().unwrap();
        let paths = LocalPaths::new(root.path().join("locald"));
        paths.ensure().unwrap();
        let install_id = "0123456789abcdef0123456789abcdef";
        std::fs::write(
            paths.root.join("operator-config.json"),
            format!("{{\"install_id\": \"{install_id}\", truncated"),
        )
        .unwrap();

        // Recovery must work on the unparseable file, because that is the
        // state a reset is usually reached from.
        assert_eq!(
            recover_install_id(&paths.root.join("operator-config.json")).as_deref(),
            Some(install_id)
        );

        struct IdentitySweep(std::path::PathBuf, std::sync::Mutex<Vec<String>>);
        impl ResetEnvironment for IdentitySweep {
            fn reclaim_vm(&self, _: &LocalPaths) -> io::Result<()> {
                Ok(())
            }
            fn purge_credentials(&self, install_id: &str) -> Vec<String> {
                assert!(
                    self.0.is_file(),
                    "identity must survive until the vault sweep succeeds"
                );
                self.1.lock().unwrap().push(install_id.to_owned());
                Vec::new()
            }
        }
        let environment = IdentitySweep(
            paths.root.join("operator-config.json"),
            std::sync::Mutex::new(Vec::new()),
        );
        let summary = perform_reset_with(paths.clone(), &environment).unwrap();
        assert_eq!(*environment.1.lock().unwrap(), vec![install_id]);
        assert_eq!(summary["secrets_swept"], true);
        assert!(!paths.root.exists());
    }
}
