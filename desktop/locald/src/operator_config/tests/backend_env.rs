//! What the backend is started with, and what it must never be asked.

use super::*;

#[test]
fn the_backend_is_never_asked_to_open_a_keychain_it_cannot_see() {
    // The packaged backend runs with HOME pointed at an app-owned directory
    // so it neither depends on nor mutates the user's home. macOS resolves
    // the login keychain out of $HOME/Library/Keychains, so a backend told
    // to use the keychain provider finds none — and the Security framework
    // answers with a modal ("a keychain cannot be found to store
    // secret-encryption-keyset") whose Reset To Defaults fails the same
    // way. locald reads the vault instead and hands the keyset over.
    let root = tempdir().unwrap();
    let vault = Arc::new(MemoryVault::default());
    let store = OperatorConfigStore::load_probing(
        root.path().join("operator.json"),
        vault.clone(),
        Arc::new(FixedModelProviderProbe),
    )
    .unwrap();

    let environment = store.backend_environment().unwrap();
    assert_eq!(
        environment.get("SECRET_KEY_PROVIDER").map(String::as_str),
        Some("static"),
        "a packaged backend must never reach for the keychain itself"
    );

    let keyset = environment
        .get("SECRET_ENCRYPTION_KEYSET")
        .expect("the keyset travels with the provider that needs it");
    let entries: Value = serde_json::from_str(keyset).expect("keyset is JSON");
    assert_eq!(entries[0]["primary"], json!(true));
    // 32 random bytes in url-safe base64 — what a Fernet key is, and what
    // the backend's parse_keyset accepts.
    assert_eq!(entries[0]["key"].as_str().unwrap().len(), 44);

    // Minted once: rows encrypted on one run have to stay readable on the
    // next, so a second call must return the stored keyset, not a new one.
    assert_eq!(
        store
            .backend_environment()
            .unwrap()
            .get("SECRET_ENCRYPTION_KEYSET"),
        Some(keyset),
    );
}

#[test]
fn applies_profile_with_vault_secret_and_renders_backend_environment() {
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
        base_url: "https://api.openai.com/v1".into(),
        default_model: "gpt-test".into(),
        models: vec!["gpt-test".into()],
        vision_models: vec![],
        ..Default::default()
    };

    store
        .apply(ApplyOperatorConfig {
            config,
            secrets: BTreeMap::from([("ai.api_key".into(), Some("secret-key".into()))]),
        })
        .unwrap();

    let environment = store.backend_environment().unwrap();
    assert_eq!(environment["LEMMA_OPENAI_API_KEY"], "secret-key");
    assert_eq!(environment["LEMMA_OPENAI_DEFAULT_MODEL"], "gpt-test");
    assert!(!fs::read_to_string(root.path().join("operator.json"))
        .unwrap()
        .contains("secret-key"));
}

/// Every secret that can be set has somewhere to go.
///
/// Two hand-maintained arrays with hand-maintained lengths describe one
/// thing: SECRET_NAMES says what may be stored, secret_environment() says
/// what the backend is told. Adding a name to the first and forgetting the
/// second gives a field in Local settings that saves happily and changes
/// nothing -- the exact shape of the Deepgram gap, where the key had no
/// mapping at all and `say` and `listen` could only fail.
///
/// `ai.api_key` is the one deliberate exclusion: it is rendered per
/// protocol above rather than copied through verbatim.
#[test]
fn every_settable_secret_reaches_the_backend_environment() {
    let mapped: std::collections::HashSet<&str> = secret_environment()
        .into_iter()
        .map(|(name, _)| name)
        .collect();
    for name in SECRET_NAMES {
        if name == "ai.api_key" {
            continue;
        }
        assert!(
            mapped.contains(name),
            "{name} can be stored but is never passed to the backend; \
             add it to secret_environment()"
        );
    }
}

/// Speech needs its key under the name the backend actually reads.
#[test]
fn the_deepgram_key_is_exported_as_deepgram_api_key() {
    let mapping: std::collections::HashMap<&str, &str> = secret_environment().into_iter().collect();
    assert_eq!(
        mapping.get("integrations.deepgram_api_key"),
        Some(&"DEEPGRAM_API_KEY")
    );
}

#[test]
fn rendering_the_backend_environment_reads_each_secret_at_most_once() {
    // On macOS every stored secret carries its own access control, and the
    // vault answers one item at a time. A second read of the same secret is
    // therefore not a wasted function call, it is a second authorisation
    // prompt in front of someone who is trying to open the app. This once
    // cost every configured secret twice over: `secret_presence` walked all
    // sixteen names to decide a single readiness boolean, and the loop that
    // renders the environment then read the same items again.
    let root = tempdir().unwrap();
    let vault = Arc::new(CountingVault::default());
    let store =
        OperatorConfigStore::load_with_vault(root.path().join("operator.json"), vault.clone())
            .unwrap();
    let mut config: OperatorConfig =
        serde_json::from_value(store.snapshot().unwrap()["config"].clone()).unwrap();
    config.ai = AiProfile {
        protocol: "openai_compat".into(),
        base_url: "https://api.openai.com/v1".into(),
        default_model: "gpt-test".into(),
        models: vec!["gpt-test".into()],
        vision_models: vec![],
        ..Default::default()
    };
    store
        .apply(ApplyOperatorConfig {
            config,
            secrets: BTreeMap::from([
                ("ai.api_key".into(), Some("secret-key".into())),
                ("surfaces.slack_bot_token".into(), Some("xoxb-test".into())),
            ]),
        })
        .unwrap();

    vault.reads.lock().unwrap().clear();
    let environment = store.backend_environment().unwrap();

    // Still renders everything the backend depends on.
    assert_eq!(environment["LEMMA_OPENAI_API_KEY"], "secret-key");
    assert_eq!(environment["SLACK_BOT_TOKEN"], "xoxb-test");
    assert_eq!(environment["LEMMA_LOCAL_AI_READY"], "true");

    for name in SECRET_NAMES {
        assert!(
            vault.reads_of(name) <= 1,
            "{name} was read {} times while rendering the backend environment",
            vault.reads_of(name)
        );
    }
}

#[test]
fn loopback_openai_profile_uses_nonsecret_compatibility_sentinel() {
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
        base_url: "http://127.0.0.1:11434/v1".into(),
        default_model: "local".into(),
        models: vec!["local".into()],
        vision_models: vec![],
        ..Default::default()
    };

    store
        .apply(ApplyOperatorConfig {
            config,
            secrets: BTreeMap::new(),
        })
        .unwrap();

    assert_eq!(
        store.backend_environment().unwrap()["LEMMA_OPENAI_API_KEY"],
        "lemma-local"
    );
}
