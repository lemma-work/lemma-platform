//! Reading a config an older or newer build wrote.

use super::*;

/// A config written by a newer build does not brick the older one.
///
/// Every nested struct is `deny_unknown_fields`, so running a build that
/// adds a field and then going back was enough to make the daemon refuse to
/// start -- silently. The unknown key is dropped and the install comes up.
#[test]
fn a_config_from_a_newer_build_is_migrated_rather_than_rejected() {
    let root = tempdir().unwrap();
    let path = root.path().join("operator-config.json");
    // Built from a real config rather than a hand-written literal, so this
    // tests the migration and not my memory of the schema.
    let mut document = serde_json::to_value(OperatorConfig::fresh().unwrap()).unwrap();
    let install_id = document["install_id"].as_str().unwrap().to_owned();
    document["revision"] = json!(3);
    document["a_field_a_later_build_added"] = json!({"nested": true});
    std::fs::write(&path, serde_json::to_vec(&document).unwrap()).unwrap();

    let mut healed = Vec::new();
    let store = OperatorConfigStore::load_with_vault_reporting(
        path.clone(),
        Arc::new(MemoryVault::default()),
        &mut healed,
    )
    .unwrap();

    assert!(
        healed.is_empty(),
        "a migration is not a repair; nothing was quarantined: {healed:?}"
    );
    let snapshot = store.snapshot().unwrap();
    assert_eq!(snapshot["config"]["install_id"], install_id);
    assert_eq!(
        snapshot["config"]["revision"], 3,
        "the operator's own settings survive the migration"
    );
}

/// An unreadable config keeps the identity that addresses the keychain.
///
/// Minting a fresh `install_id` here would strand all 19 secrets under
/// `{old_id}:{name}` -- present in the user's login keychain, addressable by
/// nothing, and not even enumerable to clean up.
#[test]
fn healing_an_unparseable_config_keeps_the_installation_identity() {
    let root = tempdir().unwrap();
    let path = root.path().join("operator-config.json");
    let install_id = "fedcba9876543210fedcba9876543210";
    std::fs::write(&path, format!("{{\"install_id\":\"{install_id}\", oops")).unwrap();

    let vault = Arc::new(MemoryVault::default());
    vault
        .set(install_id, "ai.api_key", "sk-still-here")
        .unwrap();

    let mut healed = Vec::new();
    let store =
        OperatorConfigStore::load_with_vault_reporting(path.clone(), vault.clone(), &mut healed)
            .unwrap();

    assert_eq!(healed.len(), 1, "the replacement is reported");
    assert!(healed[0].contains("stored secrets"), "{}", healed[0]);
    let snapshot = store.snapshot().unwrap();
    assert_eq!(snapshot["config"]["install_id"], install_id);
    // The secret is still reachable through the recovered identity.
    assert_eq!(
        vault.get(install_id, "ai.api_key").unwrap().as_deref(),
        Some("sk-still-here")
    );
}

/// A schema from the future is quarantined, never silently downgraded --
/// so upgrading again gets the operator's configuration back.
#[test]
fn a_config_from_an_unknown_future_schema_is_kept_not_downgraded() {
    let root = tempdir().unwrap();
    let path = root.path().join("operator-config.json");
    std::fs::write(
        &path,
        serde_json::to_vec(&json!({
            "schema_version": 99,
            "install_id": "0123456789abcdef0123456789abcdef",
            "revision": 7,
            "onboarding_complete": true,
            "ai": {"protocol": "unconfigured"},
            "integrations": {},
            "surfaces": {"resend_inbound_domain": ""},
        }))
        .unwrap(),
    )
    .unwrap();

    let mut healed = Vec::new();
    OperatorConfigStore::load_with_vault_reporting(
        path.clone(),
        Arc::new(MemoryVault::default()),
        &mut healed,
    )
    .unwrap();

    assert_eq!(healed.len(), 1);
    let aside: Vec<_> = std::fs::read_dir(root.path())
        .unwrap()
        .filter_map(Result::ok)
        .filter(|entry| entry.file_name().to_string_lossy().contains(".invalid-"))
        .collect();
    assert_eq!(aside.len(), 1, "the future config is kept, not deleted");
    let kept: Value = serde_json::from_slice(&std::fs::read(aside[0].path()).unwrap()).unwrap();
    assert_eq!(kept["schema_version"], 99);
    assert_eq!(kept["revision"], 7);
}
