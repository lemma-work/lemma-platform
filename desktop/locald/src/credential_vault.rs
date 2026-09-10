use std::collections::BTreeMap;
use std::fs;
use std::io;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use aes_gcm::{
    aead::{Aead, KeyInit, Payload},
    Aes256Gcm, Nonce,
};
use base64::{engine::general_purpose::STANDARD, Engine as _};

use crate::operator_config::{ensure_private_file, write_private_atomic, SecretVault};

pub(crate) const MASTER_KEY_NAME: &str = "credentials.master_key.v1";
const HEADER: &[u8] = b"LEMMA-VAULT-V1\0";
const NONCE_BYTES: usize = 12;
const MAX_VAULT_BYTES: u64 = 4 * 1024 * 1024;
type Entries = BTreeMap<String, Option<String>>;

struct Unlocked {
    install_id: String,
    key: Aes256Gcm,
    entries: Entries,
}

/// One OS-protected key unlocks the installation's encrypted credential file.
/// The lock also makes initial unlock and legacy migration single-flight.
pub(crate) struct EncryptedVault {
    path: PathBuf,
    platform: Arc<dyn SecretVault>,
    unlocked: Mutex<Option<Unlocked>>,
}

impl EncryptedVault {
    pub(crate) fn new(path: PathBuf, platform: Arc<dyn SecretVault>) -> Self {
        Self {
            path,
            platform,
            unlocked: Mutex::new(None),
        }
    }

    fn unlock(&self, install_id: &str) -> io::Result<Unlocked> {
        let mut encrypted = match fs::symlink_metadata(&self.path) {
            Ok(metadata) => {
                ensure_private_file(&self.path)?;
                if metadata.len() > MAX_VAULT_BYTES {
                    return Err(unavailable("The encrypted credential file is too large"));
                }
                Some(fs::read(&self.path)?)
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => None,
            Err(error) => return Err(error),
        };
        let pending = self.path.with_extension("initializing");
        let key = match self.platform.get(install_id, MASTER_KEY_NAME)? {
            Some(encoded) => {
                if encrypted.is_none() {
                    ensure_private_file(&pending)
                        .map_err(|_| unavailable("The encrypted credential file is missing"))?;
                    if fs::metadata(&pending)?.len() > MAX_VAULT_BYTES {
                        return Err(unavailable("The pending credential file is invalid"));
                    }
                    encrypted = Some(fs::read(&pending)?);
                }
                STANDARD
                    .decode(encoded)
                    .map_err(|_| unavailable("The stored credential key is invalid"))?
            }
            None if encrypted.is_some() => {
                return Err(unavailable(
                    "The encryption key for the credential file is missing",
                ));
            }
            None => {
                let mut bytes = [0; 32];
                getrandom::fill(&mut bytes)
                    .map_err(|_| unavailable("Could not generate a credential key"))?;
                let key = Aes256Gcm::new_from_slice(&bytes).expect("32-byte key");
                // Only an empty vault can be pending. If the process exits
                // after storing the OS key, the next launch can finish the rename.
                write_private_atomic(&pending, &encrypt(&key, install_id, &Entries::new())?)?;
                self.platform
                    .set(install_id, MASTER_KEY_NAME, &STANDARD.encode(bytes))?;
                encrypted = Some(fs::read(&pending)?);
                bytes.to_vec()
            }
        };
        let key = Aes256Gcm::new_from_slice(&key)
            .map_err(|_| unavailable("The stored credential key is invalid"))?;
        let entries = if let Some(bytes) = encrypted {
            decrypt(&key, install_id, bytes)?
        } else {
            Entries::new()
        };
        if !self.path.exists() {
            if !entries.is_empty() {
                return Err(unavailable("The pending credential file is not empty"));
            }
            fs::rename(&pending, &self.path)?;
            #[cfg(unix)]
            fs::File::open(self.path.parent().expect("credential path has parent"))?.sync_all()?;
        }
        Ok(Unlocked {
            install_id: install_id.into(),
            key,
            entries,
        })
    }

    fn with_unlocked<T>(
        &self,
        install_id: &str,
        action: impl FnOnce(&mut Unlocked) -> io::Result<T>,
    ) -> io::Result<T> {
        let mut guard = self
            .unlocked
            .lock()
            .map_err(|_| unavailable("The credential reader stopped"))?;
        if guard.is_none() {
            *guard = Some(self.unlock(install_id)?);
        }
        let state = guard.as_mut().expect("unlocked above");
        if state.install_id != install_id {
            return Err(unavailable(
                "The credential file belongs to another installation",
            ));
        }
        action(state)
    }

    fn persist(&self, state: &mut Unlocked, name: &str, value: Option<String>) -> io::Result<()> {
        let mut next = state.entries.clone();
        next.insert(name.into(), value);
        write_private_atomic(&self.path, &encrypt(&state.key, &state.install_id, &next)?)?;
        state.entries = next;
        Ok(())
    }
}

fn encrypt(key: &Aes256Gcm, install_id: &str, entries: &Entries) -> io::Result<Vec<u8>> {
    let plaintext =
        serde_json::to_vec(entries).map_err(|_| unavailable("Could not encode credentials"))?;
    if plaintext.len() as u64 > MAX_VAULT_BYTES - 64 {
        return Err(unavailable("The encrypted credential file is too large"));
    }
    let mut nonce = [0; NONCE_BYTES];
    getrandom::fill(&mut nonce).map_err(|_| unavailable("Could not encrypt credentials"))?;
    let payload = key
        .encrypt(
            &Nonce::from(nonce),
            Payload {
                aad: install_id.as_bytes(),
                msg: &plaintext,
            },
        )
        .map_err(|_| unavailable("Could not encrypt credentials"))?;
    let mut bytes = HEADER.to_vec();
    bytes.extend_from_slice(&nonce);
    bytes.extend_from_slice(&payload);
    Ok(bytes)
}

impl SecretVault for EncryptedVault {
    fn get(&self, install_id: &str, name: &str) -> io::Result<Option<String>> {
        self.with_unlocked(install_id, |state| {
            if let Some(value) = state.entries.get(name) {
                return Ok(value.clone());
            }
            // Keep the legacy item for recovery until explicit removal. A
            // tombstone remembers absence and prevents resurrection.
            let value = self.platform.get(install_id, name)?;
            self.persist(state, name, value.clone())?;
            Ok(value)
        })
    }

    fn set(&self, install_id: &str, name: &str, value: &str) -> io::Result<()> {
        self.with_unlocked(install_id, |state| {
            self.persist(state, name, Some(value.into()))
        })
    }

    fn delete(&self, install_id: &str, name: &str) -> io::Result<()> {
        self.with_unlocked(install_id, |state| {
            self.platform.delete(install_id, name)?;
            self.persist(state, name, None)
        })
    }
}

fn decrypt(key: &Aes256Gcm, install_id: &str, bytes: Vec<u8>) -> io::Result<Entries> {
    if !bytes.starts_with(HEADER) || bytes.len() < HEADER.len() + NONCE_BYTES {
        return Err(unavailable("The encrypted credential file is invalid"));
    }
    let nonce: [u8; NONCE_BYTES] = bytes[HEADER.len()..HEADER.len() + NONCE_BYTES]
        .try_into()
        .expect("length checked");
    let payload = &bytes[HEADER.len() + NONCE_BYTES..];
    let plaintext = key
        .decrypt(
            &Nonce::from(nonce),
            Payload {
                aad: install_id.as_bytes(),
                msg: payload,
            },
        )
        .map_err(|_| unavailable("The credential file could not be decrypted"))?;
    serde_json::from_slice(&plaintext)
        .map_err(|_| unavailable("The decrypted credential file is invalid"))
}

fn unavailable(reason: &str) -> io::Error {
    io::Error::other(format!("{reason}. Your existing credentials have been preserved. Unlock the OS credential store and retry; do not reset application data."))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[derive(Default)]
    struct Platform {
        values: Mutex<BTreeMap<(String, String), String>>,
        reads: Mutex<Vec<String>>,
        deny_reads: std::sync::atomic::AtomicBool,
    }
    impl SecretVault for Platform {
        fn get(&self, id: &str, name: &str) -> io::Result<Option<String>> {
            self.reads.lock().unwrap().push(name.into());
            if self.deny_reads.load(std::sync::atomic::Ordering::Acquire) {
                return Err(io::Error::from(io::ErrorKind::PermissionDenied));
            }
            Ok(self
                .values
                .lock()
                .unwrap()
                .get(&(id.into(), name.into()))
                .cloned())
        }
        fn set(&self, id: &str, name: &str, value: &str) -> io::Result<()> {
            self.values
                .lock()
                .unwrap()
                .insert((id.into(), name.into()), value.into());
            Ok(())
        }
        fn delete(&self, id: &str, name: &str) -> io::Result<()> {
            self.values
                .lock()
                .unwrap()
                .remove(&(id.into(), name.into()));
            Ok(())
        }
    }

    #[test]
    fn secrets_survive_restart_with_one_platform_read_and_no_plaintext_file() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("credentials.enc");
        let platform = Arc::new(Platform::default());
        let vault = EncryptedVault::new(path.clone(), platform.clone());
        vault
            .set("one", "provider", "private-provider-key")
            .unwrap();
        vault
            .set("one", "connector", "private-connector-key")
            .unwrap();
        let encrypted = fs::read(&path).unwrap();
        assert!(!String::from_utf8_lossy(&encrypted).contains("private-provider-key"));
        drop(vault);
        platform.reads.lock().unwrap().clear();
        let reopened = EncryptedVault::new(path, platform.clone());
        for _ in 0..3 {
            assert_eq!(
                reopened.get("one", "provider").unwrap().as_deref(),
                Some("private-provider-key")
            );
            assert_eq!(
                reopened.get("one", "connector").unwrap().as_deref(),
                Some("private-connector-key")
            );
        }
        assert_eq!(*platform.reads.lock().unwrap(), [MASTER_KEY_NAME]);
    }

    #[test]
    fn migrates_legacy_secrets_and_removal_cannot_resurrect_them() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("credentials.enc");
        let platform = Arc::new(Platform::default());
        platform.set("one", "provider", "old-key").unwrap();
        let vault = EncryptedVault::new(path.clone(), platform.clone());
        assert_eq!(
            vault.get("one", "provider").unwrap().as_deref(),
            Some("old-key")
        );
        vault.set("one", "provider", "new-key").unwrap();
        assert_eq!(
            vault.get("one", "provider").unwrap().as_deref(),
            Some("new-key")
        );
        vault.delete("one", "provider").unwrap();
        assert_eq!(platform.get("one", "provider").unwrap(), None);
        drop(vault);
        platform.reads.lock().unwrap().clear();
        assert_eq!(
            EncryptedVault::new(path, platform.clone())
                .get("one", "provider")
                .unwrap(),
            None
        );
        assert_eq!(*platform.reads.lock().unwrap(), [MASTER_KEY_NAME]);
    }

    #[test]
    fn missing_key_corruption_and_wrong_install_never_replace_credentials() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("credentials.enc");
        let platform = Arc::new(Platform::default());
        EncryptedVault::new(path.clone(), platform.clone())
            .set("one", "provider", "private-key")
            .unwrap();
        let original = fs::read(&path).unwrap();
        let master = platform.get("one", MASTER_KEY_NAME).unwrap().unwrap();
        platform.delete("one", MASTER_KEY_NAME).unwrap();
        let vault = EncryptedVault::new(path.clone(), platform.clone());
        assert!(vault.get("one", "provider").is_err());
        assert!(platform.get("one", MASTER_KEY_NAME).unwrap().is_none());
        assert_eq!(fs::read(&path).unwrap(), original);
        platform.set("one", MASTER_KEY_NAME, &master).unwrap();
        assert_eq!(
            vault.get("one", "provider").unwrap().as_deref(),
            Some("private-key")
        );
        platform.set("two", MASTER_KEY_NAME, &master).unwrap();
        assert!(EncryptedVault::new(path.clone(), platform.clone())
            .get("two", "provider")
            .is_err());
        let mut corrupted = original;
        *corrupted.last_mut().unwrap() ^= 1;
        fs::write(&path, &corrupted).unwrap();
        assert!(EncryptedVault::new(path.clone(), platform)
            .get("one", "provider")
            .is_err());
        assert_eq!(fs::read(&path).unwrap(), corrupted);
    }

    #[test]
    fn a_failed_save_leaves_the_cached_value_unchanged() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("credentials.enc");
        let vault = EncryptedVault::new(path.clone(), Arc::new(Platform::default()));
        vault.set("one", "provider", "old-key").unwrap();
        fs::remove_file(&path).unwrap();
        fs::create_dir(&path).unwrap();
        assert!(vault.set("one", "provider", "new-key").is_err());
        assert_eq!(
            vault.get("one", "provider").unwrap().as_deref(),
            Some("old-key")
        );
    }

    #[test]
    fn denied_unlock_preserves_the_file_and_allows_retry() {
        use std::sync::atomic::Ordering;
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("credentials.enc");
        let platform = Arc::new(Platform::default());
        EncryptedVault::new(path.clone(), platform.clone())
            .set("one", "provider", "key")
            .unwrap();
        let original = fs::read(&path).unwrap();
        platform.deny_reads.store(true, Ordering::Release);
        let vault = EncryptedVault::new(path.clone(), platform.clone());
        assert_eq!(
            vault.get("one", "provider").unwrap_err().kind(),
            io::ErrorKind::PermissionDenied
        );
        assert_eq!(fs::read(&path).unwrap(), original);
        platform.deny_reads.store(false, Ordering::Release);
        assert_eq!(
            vault.get("one", "provider").unwrap().as_deref(),
            Some("key")
        );
    }

    #[test]
    fn concurrent_readers_share_one_unlock_and_migration() {
        let root = tempfile::tempdir().unwrap();
        let platform = Arc::new(Platform::default());
        platform.set("one", "provider", "key").unwrap();
        let vault = EncryptedVault::new(root.path().join("credentials.enc"), platform.clone());
        std::thread::scope(|scope| {
            for _ in 0..8 {
                scope.spawn(|| {
                    assert_eq!(
                        vault.get("one", "provider").unwrap().as_deref(),
                        Some("key")
                    )
                });
            }
        });
        assert_eq!(
            *platform.reads.lock().unwrap(),
            [MASTER_KEY_NAME, "provider"]
        );
    }

    #[test]
    fn initialization_resumes_after_the_os_key_is_saved() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("credentials.enc");
        let platform = Arc::new(Platform::default());
        let bytes = [7_u8; 32];
        let key = Aes256Gcm::new_from_slice(&bytes).unwrap();
        platform
            .set("one", MASTER_KEY_NAME, &STANDARD.encode(bytes))
            .unwrap();
        write_private_atomic(
            &path.with_extension("initializing"),
            &encrypt(&key, "one", &Entries::new()).unwrap(),
        )
        .unwrap();
        let vault = EncryptedVault::new(path.clone(), platform);
        vault.set("one", "provider", "key").unwrap();
        assert!(path.is_file());
        assert!(!path.with_extension("initializing").exists());
        assert_eq!(
            vault.get("one", "provider").unwrap().as_deref(),
            Some("key")
        );
    }

    #[test]
    fn losing_the_credential_file_never_falls_back_to_stale_legacy_values() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("credentials.enc");
        let platform = Arc::new(Platform::default());
        platform.set("one", "provider", "old-key").unwrap();
        EncryptedVault::new(path.clone(), platform.clone())
            .set("one", "provider", "new-key")
            .unwrap();
        fs::remove_file(&path).unwrap();
        let error = EncryptedVault::new(path.clone(), platform)
            .get("one", "provider")
            .unwrap_err();
        assert!(error.to_string().contains("credential file is missing"));
        assert!(!path.exists());
    }

    #[cfg(unix)]
    #[test]
    fn private_file_permissions_and_symlink_rejection() {
        use std::os::unix::fs::{symlink, PermissionsExt};
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("credentials.enc");
        let platform = Arc::new(Platform::default());
        EncryptedVault::new(path.clone(), platform.clone())
            .set("one", "provider", "key")
            .unwrap();
        assert_eq!(
            fs::metadata(&path).unwrap().permissions().mode() & 0o777,
            0o600
        );
        let link = root.path().join("link");
        symlink(&path, &link).unwrap();
        assert!(EncryptedVault::new(link, platform)
            .get("one", "provider")
            .is_err());
    }
}
