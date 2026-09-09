//! Asking a provider what it can do, and never with the wrong key.

use super::*;

#[test]
fn discovery_never_sends_a_saved_key_to_a_changed_destination() {
    let root = tempdir().unwrap();
    let vault = Arc::new(MemoryVault::default());
    let store =
        OperatorConfigStore::load_with_vault(root.path().join("operator.json"), vault).unwrap();
    store
        .set_ai(json!({"ai": {
        "protocol": "openai_compat", "base_url": "https://saved.example/v1",
        "default_model": "test", "models": ["test"], "vision_models": []
    }, "api_key": "saved-secret"}))
        .unwrap();
    let mut ai = store.snapshot().unwrap()["config"]["ai"].clone();
    assert!(store.discover_models(json!({"ai": ai})).is_ok());
    ai["base_url"] = json!("https://changed.example/v1");
    assert!(store.discover_models(json!({"ai": ai})).is_err());
    assert!(store.set_ai(json!({"ai": ai})).is_err());
    assert!(store
        .discover_models(json!({"ai": ai, "api_key": "new-secret"}))
        .is_ok());
}

#[test]
fn successful_provider_probe_discovers_default_and_marks_profile_ready() {
    let root = tempdir().unwrap();
    let store = OperatorConfigStore::load_probing(
        root.path().join("operator.json"),
        Arc::new(MemoryVault::default()),
        Arc::new(FixedModelProviderProbe),
    )
    .unwrap();
    let mut config: OperatorConfig =
        serde_json::from_value(store.snapshot().unwrap()["config"].clone()).unwrap();
    config.ai.protocol = "openai_compat".into();
    config.ai.base_url = "http://127.0.0.1:11434/v1".into();

    let snapshot = store
        .apply(ApplyOperatorConfig {
            config,
            secrets: BTreeMap::new(),
        })
        .unwrap();

    assert_eq!(snapshot["config"]["ai"]["default_model"], "alpha-model");
    assert_eq!(
        snapshot["config"]["ai"]["models"],
        json!(["alpha-model", "zeta-model"])
    );
    assert!(snapshot["config"]["ai"]["last_validated_at_unix_ms"].is_number());
    assert_eq!(snapshot["readiness"]["ai"], "ready");
}
