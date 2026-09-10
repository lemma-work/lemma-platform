//! Updating one section without disturbing the others.

use super::*;

#[test]
fn section_updates_isolate_fields_and_credential_actions() {
    let root = tempdir().unwrap();
    let vault = Arc::new(MemoryVault::default());
    let store =
        OperatorConfigStore::load_with_vault(root.path().join("operator.json"), vault.clone())
            .unwrap();
    let initial = store.snapshot().unwrap();
    let revision = initial["config"]["revision"].as_u64().unwrap();
    let id = initial["config"]["install_id"].as_str().unwrap();
    let patch = |revision, secrets| {
        serde_json::from_value::<OperatorConfigUpdate>(json!({
            "expected_revision": revision,
            "section": {"name": "integrations", "value": initial["config"]["integrations"]},
            "secrets": secrets
        }))
        .unwrap()
    };
    store
        .update(patch(
            revision,
            json!({"integrations.deepgram_api_key": {"action":"replace", "value":"test-key"}}),
        ))
        .unwrap();
    assert_eq!(
        vault
            .get(id, "integrations.deepgram_api_key")
            .unwrap()
            .as_deref(),
        Some("test-key")
    );
    store
        .update(patch(
            revision + 1,
            json!({"integrations.deepgram_api_key": {"action":"keep"}}),
        ))
        .unwrap();
    assert_eq!(
        vault
            .get(id, "integrations.deepgram_api_key")
            .unwrap()
            .as_deref(),
        Some("test-key")
    );
    let before = store.snapshot().unwrap();
    assert!(store
        .update(patch(
            revision + 2,
            json!({"surfaces.slack_bot_token": {"action":"remove"}})
        ))
        .is_err());
    assert_eq!(before, store.snapshot().unwrap());
    let after = store
        .update(patch(
            revision + 2,
            json!({"integrations.deepgram_api_key": {"action":"remove"}}),
        ))
        .unwrap();
    assert!(vault
        .get(id, "integrations.deepgram_api_key")
        .unwrap()
        .is_none());
    assert_eq!(initial["config"]["ai"], after["config"]["ai"]);
    assert_eq!(initial["config"]["surfaces"], after["config"]["surfaces"]);
}

#[test]
fn concurrent_settings_writers_cannot_both_commit_the_same_revision() {
    let root = tempdir().unwrap();
    let store = OperatorConfigStore::load_with_vault(
        root.path().join("operator.json"),
        Arc::new(MemoryVault::default()),
    )
    .unwrap();
    let config: OperatorConfig =
        serde_json::from_value(store.snapshot().unwrap()["config"].clone()).unwrap();
    let barrier = Arc::new(std::sync::Barrier::new(2));
    let writers: Vec<_> = (0..2)
        .map(|_| {
            let store = store.clone();
            let config = config.clone();
            let barrier = barrier.clone();
            std::thread::spawn(move || {
                barrier.wait();
                store.apply(ApplyOperatorConfig {
                    config,
                    secrets: BTreeMap::new(),
                })
            })
        })
        .collect();
    let results: Vec<_> = writers
        .into_iter()
        .map(|writer| writer.join().unwrap())
        .collect();
    assert_eq!(results.iter().filter(|result| result.is_ok()).count(), 1);
    assert_eq!(
        store.snapshot().unwrap()["config"]["revision"],
        config.revision + 1
    );
}

#[test]
fn stale_settings_save_cannot_overwrite_a_new_revision() {
    let root = tempdir().unwrap();
    let store = OperatorConfigStore::load_with_vault(
        root.path().join("operator.json"),
        Arc::new(MemoryVault::default()),
    )
    .unwrap();
    let config: OperatorConfig =
        serde_json::from_value(store.snapshot().unwrap()["config"].clone()).unwrap();
    store
        .apply(ApplyOperatorConfig {
            config: config.clone(),
            secrets: BTreeMap::new(),
        })
        .unwrap();
    let before = store.snapshot().unwrap();
    let error = store
        .apply(ApplyOperatorConfig {
            config,
            secrets: BTreeMap::new(),
        })
        .unwrap_err();
    assert_eq!(error.kind(), io::ErrorKind::AlreadyExists);
    assert_eq!(before, store.snapshot().unwrap());
}

#[test]
fn setting_the_ai_profile_leaves_every_other_section_alone() {
    // Onboarding is trusted with the model and nothing else. If this took a
    // whole configuration, a caller that echoed back a stale copy would
    // silently reset the user's sharing and integration settings.
    let root = tempdir().unwrap();
    let store = OperatorConfigStore::load_probing(
        root.path().join("operator.json"),
        Arc::new(MemoryVault::default()),
        Arc::new(FixedModelProviderProbe),
    )
    .unwrap();

    let mut config: OperatorConfig =
        serde_json::from_value(store.snapshot().unwrap()["config"].clone()).unwrap();
    config.integrations.google_client_id = "google-client".into();
    config.surfaces.teams_app_id = "teams-app".into();
    store
        .apply(ApplyOperatorConfig {
            config,
            secrets: BTreeMap::new(),
        })
        .unwrap();

    let snapshot = store
        .set_ai(json!({
            "ai": {
                "protocol": "openai_compat",
                "base_url": "http://127.0.0.1:11434/v1",
                "default_model": "",
                "models": [],
                "vision_models": [],
            },
        }))
        .unwrap();

    assert_eq!(snapshot["config"]["ai"]["default_model"], "alpha-model");
    assert_eq!(
        snapshot["config"]["integrations"]["google_client_id"],
        "google-client"
    );
    assert_eq!(snapshot["config"]["surfaces"]["teams_app_id"], "teams-app");
}
