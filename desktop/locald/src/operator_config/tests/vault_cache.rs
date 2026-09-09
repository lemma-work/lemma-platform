//! Reading a secret once, however often it is asked for.

use super::*;

/// A vault that remembers what was asked of it, and in what order.
#[test]
fn delayed_native_read_cannot_undo_a_saved_replacement_or_removal() {
    use std::sync::mpsc;
    use std::time::Duration;
    struct DelayedVault {
        inner: MemoryVault,
        started: mpsc::Sender<()>,
        resume: Mutex<mpsc::Receiver<()>>,
    }
    impl SecretVault for DelayedVault {
        fn get(&self, install: &str, name: &str) -> io::Result<Option<String>> {
            let value = self.inner.get(install, name)?;
            self.started.send(()).unwrap();
            self.resume
                .lock()
                .unwrap()
                .recv_timeout(Duration::from_secs(2))
                .unwrap();
            Ok(value)
        }
        fn set(&self, install: &str, name: &str, value: &str) -> io::Result<()> {
            self.inner.set(install, name, value)
        }
        fn delete(&self, install: &str, name: &str) -> io::Result<()> {
            self.inner.delete(install, name)
        }
    }
    for replacement in [Some("new"), None] {
        let (started, ready) = mpsc::channel();
        let (resume, wait) = mpsc::channel();
        let inner = MemoryVault::default();
        inner.set("install", "ai.api_key", "old").unwrap();
        let vault = Arc::new(CachingVault::new(Arc::new(DelayedVault {
            inner,
            started,
            resume: Mutex::new(wait),
        })));
        let reader = Arc::clone(&vault);
        let read = std::thread::spawn(move || reader.get("install", "ai.api_key").unwrap());
        ready.recv_timeout(Duration::from_secs(1)).unwrap();
        match replacement {
            Some(value) => vault.set("install", "ai.api_key", value).unwrap(),
            None => vault.delete("install", "ai.api_key").unwrap(),
        }
        resume.send(()).unwrap();
        assert_eq!(read.join().unwrap().as_deref(), replacement);
        assert_eq!(
            vault.get("install", "ai.api_key").unwrap().as_deref(),
            replacement
        );
    }
}

/// The prompt this exists to stop. macOS authorizes keychain access per
/// item per read, and an ad-hoc-signed build is not remembered between
/// them, so a second read of one secret is a second modal password box.
#[test]
fn a_secret_is_read_from_the_vault_once_however_often_it_is_asked_for() {
    let counting = Arc::new(CountingVault::default());
    counting.set("install", "ai.api_key", "sk-test").unwrap();
    let vault = CachingVault::new(counting.clone());

    for _ in 0..7 {
        assert_eq!(
            vault.get("install", "ai.api_key").unwrap().as_deref(),
            Some("sk-test")
        );
    }

    assert_eq!(counting.reads_of("ai.api_key"), 1);
}

/// An absent secret costs a round trip too, and `backend_environment` asks
/// for one on every call whether the user has set it or not.
#[test]
fn a_secret_that_is_not_there_is_only_looked_for_once() {
    let counting = Arc::new(CountingVault::default());
    let vault = CachingVault::new(counting.clone());

    assert!(vault.get("install", "ai.api_key").unwrap().is_none());
    assert!(vault.get("install", "ai.api_key").unwrap().is_none());

    assert_eq!(counting.reads_of("ai.api_key"), 1);
}

/// This process is the only writer, so a write is the freshest answer there
/// is -- and going back to the vault to confirm it would be another prompt.
#[test]
fn writing_a_secret_answers_the_next_read_without_asking_again() {
    let counting = Arc::new(CountingVault::default());
    let vault = CachingVault::new(counting.clone());

    vault.set("install", "ai.api_key", "sk-new").unwrap();

    assert_eq!(
        vault.get("install", "ai.api_key").unwrap().as_deref(),
        Some("sk-new")
    );
    assert_eq!(counting.reads_of("ai.api_key"), 0);
}

#[test]
fn deleting_a_secret_answers_the_next_read_as_absent() {
    let counting = Arc::new(CountingVault::default());
    counting.set("install", "ai.api_key", "sk-old").unwrap();
    let vault = CachingVault::new(counting.clone());

    vault.delete("install", "ai.api_key").unwrap();

    assert!(vault.get("install", "ai.api_key").unwrap().is_none());
    assert_eq!(counting.reads_of("ai.api_key"), 0);
}

/// Keyed by both halves: a reinstall gets a new install id, and answering
/// its reads from the previous one's secrets would be worse than a prompt.
#[test]
fn two_installs_do_not_share_an_answer() {
    let counting = Arc::new(CountingVault::default());
    counting.set("first", "ai.api_key", "sk-first").unwrap();
    counting.set("second", "ai.api_key", "sk-second").unwrap();
    let vault = CachingVault::new(counting.clone());

    assert_eq!(
        vault.get("first", "ai.api_key").unwrap().as_deref(),
        Some("sk-first")
    );
    assert_eq!(
        vault.get("second", "ai.api_key").unwrap().as_deref(),
        Some("sk-second")
    );
}
