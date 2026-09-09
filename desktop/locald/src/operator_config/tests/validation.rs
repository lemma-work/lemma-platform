//! Refusing a configuration before any of it is written.

use super::*;

#[test]
fn rejects_unknown_secrets_and_incomplete_surface_modes() {
    let root = tempdir().unwrap();
    let store = OperatorConfigStore::load_with_vault(
        root.path().join("operator.json"),
        Arc::new(MemoryVault::default()),
    )
    .unwrap();
    let mut config: OperatorConfig =
        serde_json::from_value(store.snapshot().unwrap()["config"].clone()).unwrap();
    config.surfaces.telegram_polling = true;

    assert!(store
        .apply(ApplyOperatorConfig {
            config: config.clone(),
            secrets: BTreeMap::new(),
        })
        .is_err());
    assert!(store
        .apply(ApplyOperatorConfig {
            config,
            secrets: BTreeMap::from([("surfaces.unknown".into(), Some("secret".into()))]),
        })
        .is_err());
}

/// A secret longer than Credential Manager can hold is refused up front,
/// in characters, rather than by the vault in mis-stated bytes.
///
/// Windows-only because the limit is: the macOS Keychain would take all of
/// these. CI's Windows job runs the workspace suite, which is what makes
/// this an actual gate rather than documentation.
#[cfg(windows)]
#[test]
fn refuses_a_secret_the_windows_credential_vault_could_not_store() {
    let at_limit = "k".repeat(WINDOWS_MAX_VAULT_BLOB_BYTES / 2);
    let over_limit = "k".repeat(WINDOWS_MAX_VAULT_BLOB_BYTES / 2 + 1);
    assert!(validate_vault_capacity("ai.api_key", &at_limit).is_ok());

    let error = validate_vault_capacity("ai.api_key", &over_limit).unwrap_err();
    let message = error.to_string();
    // Names the field, the ceiling, and the actual size -- in characters.
    assert!(message.contains("ai.api_key"), "{message}");
    assert!(message.contains("1280"), "{message}");
    assert!(message.contains("1281"), "{message}");

    // Counted as the vault counts it: UTF-16 units, not UTF-8 bytes. Each
    // of these is 3 bytes in UTF-8 and 2 in UTF-16, so a value that fits
    // must not be rejected for its UTF-8 length.
    let bmp = "\u{20ac}".repeat(WINDOWS_MAX_VAULT_BLOB_BYTES / 2);
    assert!(bmp.len() > WINDOWS_MAX_VAULT_BLOB_BYTES);
    assert!(validate_vault_capacity("ai.api_key", &bmp).is_ok());
}
