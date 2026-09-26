//! Server setup: the AI side-job models, mail, the listening modes a saved
//! bot switches on, and the Test buttons.

use super::*;
use crate::setup_probe::{SetupProbe, SetupService};
use std::sync::atomic::{AtomicUsize, Ordering};

/// Counts discoveries, so a test can tell whether a save asked the provider.
#[derive(Default)]
struct CountingProbe {
    discoveries: AtomicUsize,
    completions: Mutex<Vec<String>>,
}

impl ModelProviderProbe for CountingProbe {
    fn discover(&self, _profile: &AiProfile, _api_key: Option<&str>) -> io::Result<Vec<String>> {
        self.discoveries.fetch_add(1, Ordering::SeqCst);
        Ok(vec!["big".into(), "eyes".into(), "quick".into()])
    }

    fn complete(
        &self,
        _profile: &AiProfile,
        _api_key: Option<&str>,
        model: &str,
    ) -> io::Result<()> {
        self.completions.lock().unwrap().push(model.to_owned());
        Ok(())
    }
}

/// Records what it was asked to check and answers yes.
#[derive(Default)]
struct RecordingSetupProbe(Mutex<Vec<(SetupService, String, String)>>);

impl SetupProbe for RecordingSetupProbe {
    fn check(
        &self,
        service: SetupService,
        credential: &str,
        from_email: &str,
    ) -> io::Result<String> {
        self.0
            .lock()
            .unwrap()
            .push((service, credential.to_owned(), from_email.to_owned()));
        Ok("ok".into())
    }
}

fn store_with(
    vault: Arc<MemoryVault>,
    probe: Arc<CountingProbe>,
    setup: Arc<RecordingSetupProbe>,
) -> (tempfile::TempDir, Arc<OperatorConfigStore>) {
    let root = tempdir().unwrap();
    let store =
        OperatorConfigStore::load_testing(root.path().join("operator.json"), vault, probe, setup)
            .unwrap();
    (root, store)
}

fn section(
    store: &OperatorConfigStore,
    name: &str,
    value: Value,
    secrets: Value,
) -> io::Result<Value> {
    let revision = store.snapshot().unwrap()["config"]["revision"].clone();
    store.update(
        serde_json::from_value(json!({
            "expected_revision": revision,
            "section": {"name": name, "value": value},
            "secrets": secrets,
        }))
        .unwrap(),
    )
}

fn loopback_ai(default_model: &str, image: &str, fast: &str) -> Value {
    json!({
        "protocol": "openai_compat",
        "base_url": "http://127.0.0.1:11434/v1",
        "default_model": default_model,
        "models": [],
        "vision_models": [],
        "allow_private_network": false,
        "image_model": image,
        "fast_model": fast,
    })
}

#[test]
fn the_ai_section_names_the_side_job_models_and_declares_the_image_model() {
    let probe = Arc::new(CountingProbe::default());
    let (_root, store) = store_with(Default::default(), probe.clone(), Default::default());
    let saved = section(&store, "ai", loopback_ai("big", "eyes", "quick"), json!({})).unwrap();
    assert_eq!(saved["config"]["ai"]["vision_models"], json!(["eyes"]));
    assert_eq!(saved["readiness"]["ai"], "ready");

    let environment = store.backend_environment().unwrap();
    assert_eq!(environment["LEMMA_OPENAI_DEFAULT_MODEL"], "big");
    assert_eq!(environment["LEMMA_OPENAI_VISION_MODEL_NAMES"], "eyes");
    assert_eq!(environment["VISION_MODEL"], "eyes");
    assert_eq!(environment["CONVERSATION_TITLE_MODEL"], "quick");
    assert_eq!(environment["HISTORY_SUMMARIZATION_MODEL"], "quick");
}

#[test]
fn without_a_fast_model_titles_use_the_default_and_summaries_are_left_alone() {
    let (_root, store) = store_with(Default::default(), Default::default(), Default::default());
    section(&store, "ai", loopback_ai("big", "", ""), json!({})).unwrap();
    let environment = store.backend_environment().unwrap();
    assert_eq!(environment["CONVERSATION_TITLE_MODEL"], "big");
    assert!(!environment.contains_key("HISTORY_SUMMARIZATION_MODEL"));
    // The default cannot read images and none was chosen, so nothing claims to.
    assert!(!environment.contains_key("VISION_MODEL"));
}

#[test]
fn an_unconfigured_install_names_no_side_job_models() {
    let (_root, store) = store_with(Default::default(), Default::default(), Default::default());
    let environment = store.backend_environment().unwrap();
    for key in [
        "VISION_MODEL",
        "CONVERSATION_TITLE_MODEL",
        "HISTORY_SUMMARIZATION_MODEL",
    ] {
        assert!(!environment.contains_key(key), "{key} set with no provider");
    }
}

#[test]
fn choosing_another_listed_model_does_not_ask_the_provider_again() {
    let probe = Arc::new(CountingProbe::default());
    let (_root, store) = store_with(Default::default(), probe.clone(), Default::default());
    section(&store, "ai", loopback_ai("big", "", ""), json!({})).unwrap();
    assert_eq!(probe.discoveries.load(Ordering::SeqCst), 1);

    let mut ai = store.snapshot().unwrap()["config"]["ai"].clone();
    ai["fast_model"] = json!("quick");
    // A page cannot widen the list by sending one back.
    ai["models"] = json!(["big", "eyes", "quick", "invented"]);
    let saved = section(&store, "ai", ai, json!({})).unwrap();
    assert_eq!(probe.discoveries.load(Ordering::SeqCst), 1);
    assert_eq!(saved["config"]["ai"]["fast_model"], "quick");
    assert_eq!(
        saved["config"]["ai"]["models"],
        json!(["big", "eyes", "quick"])
    );
}

#[test]
fn a_fast_or_image_model_the_provider_does_not_list_is_refused() {
    let (_root, store) = store_with(Default::default(), Default::default(), Default::default());
    let error = section(&store, "ai", loopback_ai("big", "", "missing"), json!({})).unwrap_err();
    assert!(error.to_string().contains("missing"));
    assert_eq!(
        store.snapshot().unwrap()["config"]["ai"]["protocol"],
        "unconfigured"
    );
}

fn email(provider: &str) -> Value {
    json!({
        "provider": provider,
        "from_email": "lemma@example.com",
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "smtp_user": "lemma",
        "smtp_use_tls": true,
    })
}

#[test]
fn mail_stays_in_the_local_spool_until_it_is_set_up() {
    let (_root, store) = store_with(Default::default(), Default::default(), Default::default());
    let snapshot = store.snapshot().unwrap();
    assert_eq!(snapshot["config"]["email"]["provider"], "none");
    assert_eq!(snapshot["readiness"]["email"], "optional");
    assert!(!store
        .backend_environment()
        .unwrap()
        .contains_key("EMAIL_TRANSPORT"));
}

#[test]
fn resend_needs_its_key_and_then_relays_as_the_sender() {
    let (_root, store) = store_with(Default::default(), Default::default(), Default::default());
    assert!(section(&store, "email", email("resend"), json!({})).is_err());

    let surfaces = store.snapshot().unwrap()["config"]["surfaces"].clone();
    section(
        &store,
        "surfaces",
        surfaces,
        json!({"surfaces.resend_api_key": {"action": "replace", "value": "re_test"}}),
    )
    .unwrap();
    let saved = section(&store, "email", email("resend"), json!({})).unwrap();
    assert_eq!(saved["readiness"]["email"], "ready");

    let environment = store.backend_environment().unwrap();
    assert_eq!(environment["EMAIL_TRANSPORT"], "smtp");
    assert_eq!(environment["RESEND_FROM_EMAIL"], "lemma@example.com");
    assert_eq!(environment["RESEND_API_KEY"], "re_test");
    assert!(!environment.contains_key("SMTP_HOST"));
}

#[test]
fn smtp_renders_its_server_and_takes_its_password_from_the_vault() {
    let (_root, store) = store_with(Default::default(), Default::default(), Default::default());
    assert!(section(&store, "email", email("smtp"), json!({})).is_err());
    section(
        &store,
        "email",
        email("smtp"),
        json!({"email.smtp_password": {"action": "replace", "value": "hunter2"}}),
    )
    .unwrap();
    let environment = store.backend_environment().unwrap();
    assert_eq!(environment["EMAIL_TRANSPORT"], "smtp");
    assert_eq!(environment["SMTP_HOST"], "smtp.example.com");
    assert_eq!(environment["SMTP_PORT"], "587");
    assert_eq!(environment["SMTP_USER"], "lemma");
    assert_eq!(environment["SMTP_PASSWORD"], "hunter2");
    assert_eq!(environment["SMTP_FROM_EMAIL"], "lemma@example.com");
}

#[test]
fn the_email_section_refuses_a_malformed_sender_or_host() {
    let (_root, store) = store_with(Default::default(), Default::default(), Default::default());
    let mut value = email("none");
    value["from_email"] = json!("not an address");
    assert!(section(&store, "email", value, json!({})).is_err());
    let mut value = email("none");
    value["smtp_host"] = json!("smtp://smtp.example.com:25");
    assert!(section(&store, "email", value, json!({})).is_err());
    let mut value = email("none");
    value["provider"] = json!("carrier-pigeon");
    assert!(section(&store, "email", value, json!({})).is_err());
    // And no other section's credentials ride along with it.
    assert!(section(
        &store,
        "email",
        email("none"),
        json!({"surfaces.resend_api_key": {"action": "replace", "value": "x"}}),
    )
    .is_err());
}

#[test]
fn a_saved_bot_listens_without_a_public_address() {
    let (_root, store) = store_with(Default::default(), Default::default(), Default::default());
    let environment = store.backend_environment().unwrap();
    assert_eq!(environment["ENABLE_TELEGRAM_POLLING_MODE"], "false");
    assert_eq!(environment["ENABLE_SLACK_SOCKET_MODE"], "false");

    let surfaces = store.snapshot().unwrap()["config"]["surfaces"].clone();
    section(
        &store,
        "surfaces",
        surfaces,
        json!({
            "surfaces.telegram_bot_token": {"action": "replace", "value": "1:abc"},
            "surfaces.slack_app_token": {"action": "replace", "value": "xapp-1"},
        }),
    )
    .unwrap();
    let environment = store.backend_environment().unwrap();
    assert_eq!(environment["ENABLE_TELEGRAM_POLLING_MODE"], "true");
    assert_eq!(environment["ENABLE_SLACK_SOCKET_MODE"], "true");
}

#[test]
fn a_brave_key_switches_web_search_to_brave() {
    let (_root, store) = store_with(Default::default(), Default::default(), Default::default());
    assert!(!store
        .backend_environment()
        .unwrap()
        .contains_key("WEB_SEARCH_PROVIDER"));
    let integrations = store.snapshot().unwrap()["config"]["integrations"].clone();
    section(
        &store,
        "integrations",
        integrations,
        json!({"integrations.brave_search_api_key": {"action": "replace", "value": "b-1"}}),
    )
    .unwrap();
    let environment = store.backend_environment().unwrap();
    assert_eq!(environment["WEB_SEARCH_PROVIDER"], "brave");
    assert_eq!(environment["BRAVE_SEARCH_API_KEY"], "b-1");
}

#[test]
fn testing_the_ai_provider_lists_models_and_asks_the_default_to_answer() {
    let probe = Arc::new(CountingProbe::default());
    let (_root, store) = store_with(Default::default(), probe.clone(), Default::default());
    let outcome = store
        .test_setup(json!({"service": "ai", "ai": loopback_ai("eyes", "", ""), "api_key": ""}))
        .unwrap();
    assert_eq!(outcome["models"], json!(["big", "eyes", "quick"]));
    assert_eq!(outcome["detail"], "Found 3 models, and eyes answered.");
    assert_eq!(*probe.completions.lock().unwrap(), vec!["eyes".to_owned()]);
    // Writing nothing.
    assert_eq!(store.snapshot().unwrap()["config"]["revision"], json!(0));
}

#[test]
fn testing_a_model_the_provider_stopped_listing_says_so() {
    let (_root, store) = store_with(Default::default(), Default::default(), Default::default());
    let error = store
        .test_setup(json!({"service": "ai", "ai": loopback_ai("gone", "", ""), "api_key": ""}))
        .unwrap_err();
    assert!(error.to_string().contains("no longer offers"));
}

#[test]
fn a_service_test_uses_the_typed_key_or_else_the_stored_one() {
    let vault = Arc::new(MemoryVault::default());
    let setup = Arc::new(RecordingSetupProbe::default());
    let (_root, store) = store_with(vault.clone(), Default::default(), setup.clone());

    assert!(
        store.test_setup(json!({"service": "telegram"})).is_err(),
        "nothing stored"
    );
    store
        .test_setup(json!({"service": "telegram", "credential": "typed"}))
        .unwrap();

    let install_id = store.snapshot().unwrap()["config"]["install_id"]
        .as_str()
        .unwrap()
        .to_owned();
    vault
        .set(&install_id, "surfaces.telegram_bot_token", "stored")
        .unwrap();
    store.test_setup(json!({"service": "telegram"})).unwrap();
    store
        .test_setup(json!({"service": "resend", "credential": "re", "from_email": "a@example.com"}))
        .unwrap();

    let seen = setup.0.lock().unwrap().clone();
    assert_eq!(
        seen,
        vec![
            (SetupService::Telegram, "typed".to_owned(), String::new()),
            (SetupService::Telegram, "stored".to_owned(), String::new()),
            (
                SetupService::Resend,
                "re".to_owned(),
                "a@example.com".to_owned()
            ),
        ]
    );
    assert!(store.test_setup(json!({"service": "sharing"})).is_err());
}

#[test]
fn a_config_written_before_server_setup_still_reads() {
    let root = tempdir().unwrap();
    let path = root.path().join("operator.json");
    let old = json!({
        "schema_version": 1,
        "install_id": "0123456789abcdef0123456789abcdef",
        "revision": 4,
        "onboarding_complete": true,
        "ai": {"protocol": "unconfigured", "base_url": "", "default_model": "", "models": [], "vision_models": []},
        "integrations": {"composio_enabled": false, "google_client_id": "", "microsoft_client_id": ""},
        "surfaces": {"slack_socket_mode": false, "telegram_polling": false, "teams_app_id": "", "teams_tenant_id": "",
            "whatsapp_phone_number_id": "", "whatsapp_waba_id": "", "resend_inbound_domain": ""},
    });
    std::fs::write(&path, serde_json::to_vec(&old).unwrap()).unwrap();
    ensure_private_file(&path).unwrap();
    let store =
        OperatorConfigStore::load_with_vault(path, Arc::new(MemoryVault::default())).unwrap();
    let snapshot = store.snapshot().unwrap();
    assert_eq!(snapshot["config"]["revision"], json!(4));
    assert_eq!(snapshot["config"]["email"]["provider"], "none");
    assert_eq!(snapshot["config"]["email"]["smtp_port"], json!(587));
    assert_eq!(snapshot["config"]["ai"]["fast_model"], "");
}

#[test]
fn a_resend_key_and_the_email_section_save_as_one_change() {
    let vault = Arc::new(MemoryVault::default());
    let (_root, store) = store_with(vault.clone(), Default::default(), Default::default());
    let snapshot = store.snapshot().unwrap();
    let revision = snapshot["config"]["revision"].as_u64().unwrap();
    let saved = store
        .update(
            serde_json::from_value(json!({
                "expected_revision": revision,
                "sections": [
                    {"name": "surfaces", "value": snapshot["config"]["surfaces"]},
                    {"name": "email", "value": email("resend")},
                ],
                "secrets": {"surfaces.resend_api_key": {"action": "replace", "value": "re_1"}},
            }))
            .unwrap(),
        )
        .unwrap();
    // One write, one revision: one backend restart.
    assert_eq!(saved["config"]["revision"].as_u64(), Some(revision + 1));
    assert_eq!(saved["readiness"]["email"], "ready");

    // A credential still has to belong to a section being saved.
    let refused = store.update(
        serde_json::from_value(json!({
            "expected_revision": revision + 1,
            "sections": [{"name": "email", "value": email("resend")}],
            "secrets": {"integrations.deepgram_api_key": {"action": "replace", "value": "d"}},
        }))
        .unwrap(),
    );
    assert!(refused.is_err());
    let empty = store.update(
        serde_json::from_value(json!({"expected_revision": revision + 1, "sections": []})).unwrap(),
    );
    assert!(empty.is_err());
}
