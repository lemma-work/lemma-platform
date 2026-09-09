//! This installation's own secret, and where it is not kept.

use super::*;

/// A secret that vanished beside surviving data stops the install.
///
/// `installation_secret` derives the Fernet key for every encrypted column.
/// Reminting it while the data directory is still there does not fail --
/// that is the problem. Rows decrypt to garbage, and the only sign is
/// whatever breaks first, weeks later.
#[test]
fn a_missing_installation_secret_beside_real_data_demands_a_reset() {
    let root = tempfile::tempdir().unwrap();
    let root = root.path();
    std::fs::create_dir_all(root.join("data/files")).unwrap();
    std::fs::write(root.join("data/files/uploaded"), b"a user's file").unwrap();
    let path = root.join("host.secrets.json");

    let mut healed = Vec::new();
    let secrets = load_or_create_host_secrets(&path, &mut healed).unwrap();

    assert_eq!(
        secrets.installation_secret.len(),
        64,
        "a key is still minted"
    );
    assert!(
        crate::paths::data_reset_reason(root).is_some(),
        "the install must stop with a reset offered, not carry on quietly",
    );
    assert_eq!(healed.len(), 1);
    assert!(healed[0].contains("missing"), "{}", healed[0]);
}

/// And a genuine first run says nothing at all.
#[test]
fn a_first_run_mints_its_secret_without_ceremony() {
    let root = tempfile::tempdir().unwrap();
    let root = root.path();
    std::fs::create_dir_all(root.join("data/files")).unwrap();
    let path = root.join("host.secrets.json");

    let mut healed = Vec::new();
    load_or_create_host_secrets(&path, &mut healed).unwrap();

    assert!(healed.is_empty(), "nothing was lost: {healed:?}");
    assert!(
        crate::paths::data_reset_reason(root).is_none(),
        "a first run must not be told to reset the data it does not have",
    );
}

#[test]
fn a_checkout_never_reaches_for_the_keychain() {
    // The source-mode backend is a `uv run` child of locald with no GUI
    // session. Asking macOS for the login keychain there does not fail
    // quietly — it puts up "a keychain cannot be found to store
    // secret-encryption-keyset" and the whole run stalls behind a dialog.
    let root = tempfile::tempdir().unwrap();
    for directory in ["lemma-backend", "lemma-frontend"] {
        fs::create_dir_all(root.path().join(directory)).unwrap();
    }
    fs::create_dir_all(root.path().join("desktop/runtime")).unwrap();
    fs::write(
        root.path().join("desktop/runtime/frontend-launcher.mjs"),
        "",
    )
    .unwrap();

    // Named rather than resolved: a CI runner has neither tool installed,
    // and where secrets live does not depend on them.
    let source = source_bindings_with(
        root.path(),
        Path::new("/usr/bin/uv"),
        Path::new("/usr/bin/node"),
    )
    .unwrap();
    assert_eq!(source.secret_key_provider, "static");

    let pack = tempfile::tempdir().unwrap();
    // A packaged install holds real provider credentials and must keep them
    // in the OS keychain, so the two must not converge.
    assert_ne!(
        source.secret_key_provider,
        packaged_bindings(pack.path())
            .map(|bindings| bindings.secret_key_provider)
            .unwrap_or("keychain")
    );
}

/// A secret anyone on the machine could read is narrowed, kept, and reported.
///
/// Three things have to be true at once, and each is a way this has been wrong.
/// The secret must survive: refusing it sends this file into the quarantine
/// path, which remints the key and requires a data reset -- every encrypted row
/// in the installation, permanently unreadable, arrived at from a mode bit.
/// The mode must be repaired, or the next start finds it exactly as wide. And
/// it must be said out loud, which it never was: the mode was set on every read
/// and nothing recorded that anything had been wrong.
#[cfg(unix)]
#[test]
fn a_world_readable_installation_secret_is_narrowed_and_reported_not_reminted() {
    use std::os::unix::fs::PermissionsExt;
    let root = tempfile::tempdir().unwrap();
    let root = root.path();
    std::fs::create_dir_all(root.join("data/files")).unwrap();
    let path = root.join("host.secrets.json");

    let mut healed = Vec::new();
    let original = load_or_create_host_secrets(&path, &mut healed).unwrap();
    assert!(healed.is_empty(), "a first run says nothing: {healed:?}");

    // However it got this way -- `cp -R` without `-p` is the usual answer.
    std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o644)).unwrap();

    let mut healed = Vec::new();
    let reopened = load_or_create_host_secrets(&path, &mut healed).unwrap();

    assert_eq!(
        reopened.installation_secret, original.installation_secret,
        "the key must survive: reminting it makes every encrypted row unreadable",
    );
    let mode = std::fs::metadata(&path).unwrap().permissions().mode();
    assert_eq!(mode & 0o077, 0, "mode was left at {mode:o}");
    assert_eq!(healed.len(), 1, "{healed:?}");
    assert!(
        healed[0].contains("more than this user"),
        "the operator has to be told: {}",
        healed[0]
    );
    assert!(
        crate::paths::data_reset_reason(root).is_none(),
        "a mode bit is not a reason to destroy the data",
    );
}

/// And a symlink where the secret should be is still refused.
///
/// Not a permissions accident, and following one is the bug the repair path
/// must not introduce.
#[cfg(unix)]
#[test]
fn a_symbolic_link_where_the_secret_belongs_is_not_repaired() {
    let root = tempfile::tempdir().unwrap();
    let root = root.path();
    std::fs::create_dir_all(root.join("data/files")).unwrap();
    let elsewhere = root.join("elsewhere.json");
    std::fs::write(&elsewhere, br#"{"installation_secret":"00"}"#).unwrap();
    let path = root.join("host.secrets.json");
    std::os::unix::fs::symlink(&elsewhere, &path).unwrap();

    let mut healed = Vec::new();
    load_or_create_host_secrets(&path, &mut healed).unwrap();
    assert_eq!(healed.len(), 1, "{healed:?}");
    assert!(
        healed[0].contains("unreadable"),
        "a link is quarantined, not narrowed: {}",
        healed[0]
    );
}
