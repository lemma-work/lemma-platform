//! Removing an installation's secrets, and retrying after a failure.

use super::*;

#[test]
fn failed_cleanup_stops_prompting_and_keeps_remaining_credentials() {
    struct Denied(Mutex<Vec<String>>);
    impl SecretVault for Denied {
        fn get(&self, _: &str, _: &str) -> io::Result<Option<String>> {
            unreachable!()
        }
        fn set(&self, _: &str, _: &str, _: &str) -> io::Result<()> {
            unreachable!()
        }
        fn delete(&self, _: &str, name: &str) -> io::Result<()> {
            self.0.lock().unwrap().push(name.into());
            Err(io::ErrorKind::TimedOut.into())
        }
    }
    let vault = Denied(Mutex::new(Vec::new()));
    assert_eq!(purge_vault_secrets(&vault, "installation").len(), 1);
    assert_eq!(*vault.0.lock().unwrap(), vec!["ai.api_key"]);
}

#[test]
fn aborted_cleanup_preserves_the_encrypted_vault_key_until_retry_succeeds() {
    struct PurgeVault {
        denied: bool,
        deleted: Mutex<Vec<String>>,
    }
    impl SecretVault for PurgeVault {
        fn get(&self, _: &str, _: &str) -> io::Result<Option<String>> {
            unreachable!()
        }
        fn set(&self, _: &str, _: &str, _: &str) -> io::Result<()> {
            unreachable!()
        }
        fn delete(&self, _: &str, name: &str) -> io::Result<()> {
            if self.denied && name == "secret.encryption_keyset" {
                return Err(io::ErrorKind::PermissionDenied.into());
            }
            self.deleted.lock().unwrap().push(name.into());
            Ok(())
        }
    }
    let mut vault = PurgeVault {
        denied: true,
        deleted: Mutex::new(Vec::new()),
    };
    let master = crate::credential_vault::MASTER_KEY_NAME;
    assert_eq!(purge_vault_secrets(&vault, "installation").len(), 1);
    assert!(!vault
        .deleted
        .lock()
        .unwrap()
        .iter()
        .any(|name| name == master));
    vault.denied = false;
    assert!(purge_vault_secrets(&vault, "installation").is_empty());
    assert_eq!(vault.deleted.lock().unwrap().last().unwrap(), master);
}

#[test]
fn restores_committed_config_and_every_vault_secret() {
    let root = tempdir().unwrap();
    let store = OperatorConfigStore::load_with_vault(
        root.path().join("operator.json"),
        Arc::new(MemoryVault::default()),
    )
    .unwrap();
    let mut config: OperatorConfig =
        serde_json::from_value(store.snapshot().unwrap()["config"].clone()).unwrap();
    config.ai = AiProfile {
        protocol: "openai_compat".into(),
        base_url: "https://models.example.test/v1".into(),
        default_model: "stable-model".into(),
        models: vec!["stable-model".into()],
        vision_models: vec![],
        ..Default::default()
    };
    store
        .apply(ApplyOperatorConfig {
            config,
            secrets: BTreeMap::from([("ai.api_key".into(), Some("old-secret".into()))]),
        })
        .unwrap();
    let old_snapshot = store.snapshot().unwrap();
    let old_state = store.capture_state().unwrap();

    let mut replacement: OperatorConfig =
        serde_json::from_value(old_snapshot["config"].clone()).unwrap();
    replacement.ai.default_model = "replacement-model".into();
    replacement.ai.models = vec!["replacement-model".into()];
    store
        .apply(ApplyOperatorConfig {
            config: replacement,
            secrets: BTreeMap::from([("ai.api_key".into(), Some("new-secret".into()))]),
        })
        .unwrap();

    let restored = store.restore_state(old_state).unwrap();
    assert_eq!(restored["config"], old_snapshot["config"]);
    assert_eq!(
        store.backend_environment().unwrap()["LEMMA_OPENAI_API_KEY"],
        "old-secret"
    );
    assert!(!fs::read_to_string(root.path().join("operator.json"))
        .unwrap()
        .contains("old-secret"));
}
