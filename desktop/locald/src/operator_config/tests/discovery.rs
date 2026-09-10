//! Asking a provider what it can do, and never with the wrong key.

use super::*;
use std::time::Duration;

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

/// A model list must not hold up a settings save.
///
/// `discover_models` took the write lock for the whole call, including the
/// probe -- an HTTP round trip to a provider the caller chose, with a timeout
/// measured in seconds. Every other write waited behind it: opening Local
/// settings and pressing "Discover models" made the Save button do nothing
/// until the provider answered.
///
/// What the lock is actually for is pairing the destination with the stored
/// key off one committed revision, and that is done before the probe.
#[test]
fn discovering_models_does_not_hold_up_the_settings_that_are_being_saved() {
    /// A probe that parks on its *first* call until the test lets it through.
    ///
    /// Only the first: `apply` validates a profile through the same probe, so
    /// a probe that parked on every call would block the very save this test
    /// is timing -- for a reason that has nothing to do with the lock.
    struct BlockingProbe {
        release: Mutex<std::sync::mpsc::Receiver<()>>,
        entered: std::sync::mpsc::SyncSender<()>,
        parked: std::sync::atomic::AtomicBool,
    }
    impl ModelProviderProbe for BlockingProbe {
        fn discover(
            &self,
            _profile: &AiProfile,
            _api_key: Option<&str>,
        ) -> io::Result<Vec<String>> {
            if !self.parked.swap(true, std::sync::atomic::Ordering::SeqCst) {
                self.entered.send(()).expect("the test is waiting");
                self.release
                    .lock()
                    .expect("probe lock poisoned")
                    .recv()
                    .expect("the test releases the probe");
            }
            Ok(vec!["alpha-model".into(), "zeta-model".into()])
        }
    }

    let (release_sender, release) = std::sync::mpsc::channel();
    let (entered, entered_receiver) = std::sync::mpsc::sync_channel(1);
    let root = tempdir().unwrap();
    let store = OperatorConfigStore::load_probing(
        root.path().join("operator-config.json"),
        Arc::new(MemoryVault::default()),
        Arc::new(BlockingProbe {
            release: Mutex::new(release),
            entered,
            parked: std::sync::atomic::AtomicBool::new(false),
        }),
    )
    .unwrap();

    std::thread::scope(|scope| {
        let probing = scope.spawn(|| {
            store.discover_models(json!({
                "ai": {
                    "protocol": "openai_compat",
                    "base_url": "http://127.0.0.1:1234/v1",
                    "default_model": "alpha-model",
                    "models": ["alpha-model"],
                    "vision_models": [],
                },
            }))
        });
        entered_receiver
            .recv_timeout(Duration::from_secs(5))
            .expect("the probe is reached");

        // The probe is now inside `discover`, going nowhere. A save must not
        // wait for it.
        //
        // On its own thread with a deadline, because the failure this guards
        // against is an unbounded wait: asserting on elapsed time in this
        // thread would hang here rather than fail.
        let (saved_sender, saved) = std::sync::mpsc::sync_channel(1);
        let store_for_save = &store;
        let saving = scope.spawn(move || {
            let outcome = store_for_save.set_ai(json!({
                "ai": {
                    "protocol": "openai_compat",
                    "base_url": "http://127.0.0.1:1234/v1",
                    "default_model": "zeta-model",
                    "models": ["zeta-model"],
                    "vision_models": [],
                },
            }));
            let _ = saved_sender.send(outcome.is_ok());
        });
        let in_time = saved.recv_timeout(Duration::from_secs(5));

        // Released before anything is asserted: a panic inside the scope waits
        // for every thread in it, and one parked on this channel never
        // returns, so a failing assertion would hang instead of failing.
        release_sender.send(()).expect("the probe is still waiting");
        assert_eq!(
            in_time,
            Ok(true),
            "the save waited on a model list instead of going through",
        );
        saving.join().unwrap();
        probing.join().unwrap().expect("the probe finishes");
    });
}
