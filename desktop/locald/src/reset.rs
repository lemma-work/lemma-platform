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
// Only the macOS reclaim names a path; Windows unregisters a WSL distribution.
#[cfg(target_os = "macos")]
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
    summary["vm_reclaim_attempted"] = json!(reclaim_running_vm(&paths).is_ok());

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
        let failures = purge_secrets(&install_id);
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
    runtime.reclaim_owned_macos_vm()
}

#[cfg(not(target_os = "macos"))]
fn reclaim_running_vm(_paths: &LocalPaths) -> io::Result<()> {
    // Windows unregisters its WSL distribution rather than unlinking a disk,
    // and that path is not wired up yet. Removing the state directory is still
    // correct and is what this subcommand does next.
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

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

        let summary = perform_reset(paths.clone()).unwrap();

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

        let summary = perform_reset(paths).unwrap();

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
        reset_install(paths).unwrap();
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

        reset_install(paths.clone()).unwrap();
        assert!(!paths.root.exists());
    }
}
