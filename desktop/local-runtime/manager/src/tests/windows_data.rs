//! The data distribution: migrating into it, and refusing to replace a
//! runtime that still holds the data.

use super::*;

/// Windows users were pinned to whichever release they installed first.
///
/// The data lived inside the runtime distribution, so an upgrade could
/// only refuse -- and it did, telling people to reset the runtime and lose
/// every workspace, database and pod. Now the data has a distribution of
/// its own and the upgrade replaces the runtime, which means the upgrade
/// path calls `--unregister` on purpose. This is the only thing standing
/// between that call and somebody's work.
#[test]
fn an_upgrade_will_not_delete_a_runtime_that_still_holds_the_data() {
    refuse_replacement_without_holder(true).expect("the holder has it; proceed");

    let refused = refuse_replacement_without_holder(false).unwrap_err();
    let message = refused.to_string();
    assert!(
        message.contains("workspaces") && message.contains("databases"),
        "the refusal has to say what is at stake: {message}"
    );
    assert!(
        message.contains("nothing has been changed"),
        "and that it stopped before doing any of it: {message}"
    );
}

/// The gate is worth nothing if it runs after the deletion.
#[test]
fn the_gate_runs_before_the_unregister_it_guards() {
    let source = manager_source();
    let start = source
        .find("fn replace_runtime_distribution")
        .expect("the replacement exists");
    let body = &source[start..];
    let end = body.find("\n    }\n").expect("the function ends");
    let body = &body[..end];

    let gate = body
        .find("refuse_replacement_without_holder")
        .expect("the replacement is gated at all");
    let unregister = body.find("\"--unregister\"").expect("it does unregister");
    assert!(
        gate < unregister,
        "the gate has to come first, or it guards nothing:\n{body}"
    );
}

/// "Everything Lemma keeps on this PC is deleted" has to stay true.
///
/// It was false once already, when the reset removed the state directory
/// and left the distribution registered and full. Splitting the data into
/// its own distribution is exactly the shape of change that would make it
/// false a second time.
#[test]
fn starting_over_removes_the_data_distribution_too() {
    let source = manager_source();
    let start = source
        .find("pub fn unregister_windows_guest")
        .expect("the wipe exists");
    let body = &source[start..];
    let end = body.find("\n    }\n").expect("the function ends");
    let body = &body[..end];

    assert!(
        body.contains("self.data_distribution()"),
        "a wipe that leaves the data holder registered deletes nothing that \
         matters:\n{body}"
    );
    assert_eq!(
        body.matches("\"--unregister\"").count(),
        2,
        "both distributions, or the promise is false again:\n{body}"
    );
}

/// The marker is the holder's own word that the copy finished.
#[test]
fn the_migration_claims_nothing_until_the_copy_is_done() {
    let script = MIGRATE_DATA_INTO_HOLDER;
    let marker = script
        .rfind(".lemma-data-holder")
        .expect("it writes the marker");
    assert!(
        !script.contains("/var/lib/containerd"),
        "the container store is rebuilt on the first start after the move \
         whatever this does, so copying it moves gigabytes to delete them: \
         {script}"
    );
    for tree in ["/var/lib/lemma ", "/var/lib/nerdctl ", "/etc/cni/net.d "] {
        let copy = script
            .find(tree)
            .unwrap_or_else(|| panic!("{tree} has to be carried across: {script}"));
        assert!(
            copy < marker,
            "{tree} is copied after the marker that says the copy is done, \
             so an interrupted migration would look finished: {script}"
        );
    }
    assert!(
        script.find("exit 0").expect("it is idempotent") < marker,
        "a second run has to stop before copying over what it already moved"
    );
}

/// A live container store is a moving target.
///
/// On a warm start containerd is running with several gigabytes of overlay
/// mounts stacked on the store -- 3.6 GB on the machine this was written
/// against. Copying that out from underneath it would copy a filesystem
/// mid-write, and the copy is the thing the upgrade then trusts enough to
/// delete the original.
#[test]
fn the_migration_quiesces_the_distribution_before_copying_it() {
    let source = manager_source();
    let start = source
        .find("fn migrate_data_into_holder")
        .expect("the migration exists");
    let body = &source[start..];
    let end = body.find("\n    }\n").expect("the function ends");
    let body = &body[..end];

    let skip = body
        .find("data_holder_is_ready")
        .expect("it does nothing once the holder has the data");
    let terminate = body
        .find("\"--terminate\"")
        .expect("it quiesces before copying");
    let copy = body
        .find("MIGRATE_DATA_INTO_HOLDER")
        .expect("it runs the copy");
    assert!(
        skip < terminate && terminate < copy,
        "a warm start must not be terminated when there is nothing to \
         move, and the copy must not run against a live store:\n{body}"
    );
}

/// Publishing runs on every start, so it has to survive running twice.
#[test]
fn publishing_the_data_share_is_idempotent() {
    let script = PUBLISH_DATA_SHARE;
    let guard = script
        .find("mountpoint -q /mnt/wsl/lemma-data")
        .expect("it checks first");
    let bind = script.find("mount --bind").expect("it binds");
    assert!(
        guard < bind,
        "stacking a second bind on the same path every start: {script}"
    );
}

/// The uninstaller names the distributions; nothing else knows them.
///
/// When `reset` cannot run -- an installation too damaged to reset is
/// still one somebody asked to remove -- the uninstaller falls back to
/// telling them what to unregister by hand. That text is the only place
/// those names appear outside this file, and the one that matters is the
/// holder: the runtime distribution is replaced by every upgrade, so
/// naming only it would say "delete the disposable half and keep the
/// several gigabytes you were trying to remove".
#[test]
fn the_uninstaller_names_the_distribution_that_holds_the_data() {
    let hooks = include_str!("../../../../installer/hooks.nsh").replace("\r\n", "\n");
    let root = tempdir().unwrap();
    let runtime = ManagedRuntime::new(ManagedRuntimeConfig {
        wsl_distribution: DEFAULT_WSL_DISTRIBUTION.to_string(),
        local_root: root.path().join("local"),
        artifact_root: root.path().join("artifacts"),
        bridge_executable: root.path().join("lemma-runtime"),
        #[cfg(target_os = "macos")]
        vz_executable: root.path().join("lemma-vz"),
        #[cfg(windows)]
        wsl_executable: PathBuf::from("wsl.exe"),
    })
    .unwrap();

    // Derived the same way the running code derives it, so a rename here
    // fails rather than silently leaving the installer pointing at a name
    // that no longer exists.
    let holder = format!("{}Data", runtime.wsl_distribution());
    assert!(
        hooks.contains(&holder),
        "the manual fallback has to name {holder}, or it sends people to \
         delete the wrong one:\n{hooks}"
    );
}
