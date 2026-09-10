//! Where secrets live, and what the OS keychain will accept.

use super::*;

pub(crate) trait SecretVault: Send + Sync {
    fn get(&self, install_id: &str, name: &str) -> io::Result<Option<String>>;
    fn set(&self, install_id: &str, name: &str, value: &str) -> io::Result<()>;
    fn delete(&self, install_id: &str, name: &str) -> io::Result<()>;
}

pub(crate) struct PlatformVault;

impl PlatformVault {
    pub(crate) fn entry(install_id: &str, name: &str) -> io::Result<keyring::v1::Entry> {
        keyring::v1::Entry::new(VAULT_SERVICE, &format!("{install_id}:{name}")).map_err(vault_error)
    }
}

impl SecretVault for PlatformVault {
    fn get(&self, install_id: &str, name: &str) -> io::Result<Option<String>> {
        crate::vault_process::invoke(install_id, name, crate::vault_process::Operation::Get)
    }

    fn set(&self, install_id: &str, name: &str, value: &str) -> io::Result<()> {
        crate::vault_process::invoke(
            install_id,
            name,
            crate::vault_process::Operation::Set {
                value: value.into(),
            },
        )
        .map(|_| ())
    }

    fn delete(&self, install_id: &str, name: &str) -> io::Result<()> {
        crate::vault_process::invoke(install_id, name, crate::vault_process::Operation::Delete)
            .map(|_| ())
    }
}

/// Cache successful OS-vault reads for this daemon's lifetime. The encrypted
/// credential file uses this for its wrapping key and legacy migration; set and
/// delete must update cached values so settings changes take effect immediately.
pub(crate) struct CachingVault {
    inner: Arc<dyn SecretVault>,
    seen: Mutex<HashMap<(String, String), Option<String>>>,
}

impl CachingVault {
    pub(crate) fn new(inner: Arc<dyn SecretVault>) -> Self {
        Self {
            inner,
            seen: Mutex::new(HashMap::new()),
        }
    }

    pub(crate) fn key(install_id: &str, name: &str) -> (String, String) {
        (install_id.to_owned(), name.to_owned())
    }
}

impl SecretVault for CachingVault {
    fn get(&self, install_id: &str, name: &str) -> io::Result<Option<String>> {
        let key = Self::key(install_id, name);
        if let Some(known) = self
            .seen
            .lock()
            .expect("secret cache poisoned")
            .get(&key)
            .cloned()
        {
            return Ok(known);
        }
        // Deliberately outside the lock: a keychain read can block on a modal
        // prompt, and holding the cache across it would stall every other
        // secret this process wants behind the one the user is looking at.
        let value = self.inner.get(install_id, name)?;
        // A save may have replaced or removed the secret during the native
        // read. Its newer cache entry must win over this delayed result.
        Ok(self
            .seen
            .lock()
            .expect("secret cache poisoned")
            .entry(key)
            .or_insert(value)
            .clone())
    }

    fn set(&self, install_id: &str, name: &str, value: &str) -> io::Result<()> {
        self.inner.set(install_id, name, value)?;
        self.seen
            .lock()
            .expect("secret cache poisoned")
            .insert(Self::key(install_id, name), Some(value.to_owned()));
        Ok(())
    }

    fn delete(&self, install_id: &str, name: &str) -> io::Result<()> {
        self.inner.delete(install_id, name)?;
        self.seen
            .lock()
            .expect("secret cache poisoned")
            .insert(Self::key(install_id, name), None);
        Ok(())
    }
}

pub(crate) fn vault_error(error: keyring::v1::Error) -> io::Error {
    io::Error::other(format!("operating-system credential vault failed: {error}"))
}

/// Remove every secret this installation stored, reporting what would not go.
///
/// Used only by `lemma-locald reset`. It runs inside *this* binary rather than
/// the app because the OS credential vault keys an item's access control to the
/// code identity that created it -- `work.lemma.locald`, fixed by the
/// `Info.plist` linked into this executable. A delete issued from the Tauri
/// shell is a different program as far as the vault is concerned, and would
/// prompt or fail.
///
/// Retain the installation identity until every credential has been removed.
pub fn purge_secrets(install_id: &str) -> Vec<String> {
    purge_vault_secrets(&PlatformVault, install_id)
}

pub(crate) fn purge_vault_secrets(vault: &dyn SecretVault, install_id: &str) -> Vec<String> {
    let mut failures = Vec::new();
    for name in SECRET_NAMES.into_iter().chain(["secret.encryption_keyset"]) {
        if let Err(error) = vault.delete(install_id, name) {
            failures.push(format!("{name}: {error}"));
            // A denied or stalled store must not produce another consent prompt
            // for every remaining item. Recovery can retry the idempotent sweep.
            break;
        }
    }
    // A failed reset retains local data. Keep its decryption key until every
    // legacy item has been removed, so an aborted cleanup remains recoverable.
    if failures.is_empty() {
        let name = crate::credential_vault::MASTER_KEY_NAME;
        if let Err(error) = vault.delete(install_id, name) {
            failures.push(format!("{name}: {error}"));
        }
    }
    failures
}

/// Refuse a secret the platform vault cannot hold, before it is offered one.
///
/// Windows Credential Manager caps a credential blob at
/// CRED_MAX_CREDENTIAL_BLOB_SIZE -- 2560 bytes -- and the keyring backend
/// writes the value as UTF-16, so the real ceiling is 1280 ASCII characters
/// against the 16 KiB validated just above. macOS Keychain has no comparable
/// limit, which is why nothing here noticed: a long bearer token (an Entra
/// access token carrying group claims, a corporate LLM-gateway JWT) passed
/// validation on both platforms and then failed at the vault on Windows only.
/// What the user got was the backend's own words -- "longer than the platform
/// limit of 2560 chars" -- a number that is off by a factor of two, because it
/// counts bytes and calls them chars.
///
/// Checked here so the limit is refused up front and named in the units of the
/// thing being pasted.
#[cfg(windows)]
pub(crate) fn validate_vault_capacity(name: &str, value: &str) -> io::Result<()> {
    // Counted the way the vault counts it. `validate_text` measures UTF-8
    // bytes, which is a different number for anything non-ASCII.
    let blob_bytes = value.encode_utf16().count() * 2;
    if blob_bytes > WINDOWS_MAX_VAULT_BLOB_BYTES {
        return Err(invalid(format!(
            "{name} is too long to store on Windows: the credential vault holds \
             at most {} characters and this is {}",
            WINDOWS_MAX_VAULT_BLOB_BYTES / 2,
            value.chars().count()
        )));
    }
    Ok(())
}

#[cfg(windows)]
pub(crate) const WINDOWS_MAX_VAULT_BLOB_BYTES: usize = 2560;

/// Nothing to check. The macOS Keychain takes values far larger than anything
/// a credential field can hold, so there is no limit here to fail early on.
#[cfg(not(windows))]
pub(crate) fn validate_vault_capacity(_name: &str, _value: &str) -> io::Result<()> {
    Ok(())
}

pub(crate) fn restore_secrets(
    vault: &dyn SecretVault,
    install_id: &str,
    previous: &BTreeMap<String, Option<String>>,
) -> io::Result<()> {
    let mut first_error = None;
    for (name, value) in previous {
        if let Err(error) = set_secret(vault, install_id, name, value.as_deref()) {
            first_error.get_or_insert(error);
        }
    }
    match first_error {
        Some(error) => Err(error),
        None => Ok(()),
    }
}

pub(crate) fn set_secret(
    vault: &dyn SecretVault,
    install_id: &str,
    name: &str,
    value: Option<&str>,
) -> io::Result<()> {
    match value {
        Some(value) => vault.set(install_id, name, value),
        None => vault.delete(install_id, name),
    }
}

pub(crate) fn with_restore_error(error: io::Error, restore: io::Result<()>) -> io::Error {
    match restore {
        Ok(()) => error,
        Err(restore_error) => io::Error::other(format!(
            "{error}; restoring the previous credential state also failed: {restore_error}"
        )),
    }
}

impl OperatorConfigStore {
    /// The backend's encryption keyset, minted once and kept in the OS vault.
    ///
    /// The backend used to fetch this itself with `SECRET_KEY_PROVIDER=keychain`,
    /// which cannot work in a packaged install: the packaged backend runs with
    /// `HOME` pointed at an app-owned directory so it neither depends on nor
    /// mutates the user's home, and macOS resolves the login keychain out of
    /// `$HOME/Library/Keychains`. Finding none, the Security framework does not
    /// fail quietly — it puts a modal "a keychain cannot be found to store
    /// secret-encryption-keyset" in front of the user, and its Reset To
    /// Defaults button fails the same way for the same reason.
    ///
    /// locald has no such problem: it runs as an ordinary process of the
    /// signed-in user and already keeps `ai.api_key` here. So it fetches the
    /// keyset and hands it over, and the material still lives in the OS vault
    /// exactly as intended — the backend simply stops reaching for it.
    pub(crate) fn secret_encryption_keyset(&self, install_id: &str) -> io::Result<String> {
        const NAME: &str = "secret.encryption_keyset";
        if let Some(existing) = self.vault.get(install_id, NAME)? {
            if !existing.trim().is_empty() {
                return Ok(existing);
            }
        }
        // Same shape the backend's own keychain provider used, so an install
        // that already has rows encrypted under a keyset it minted itself stays
        // readable if that keyset is ever migrated in here.
        let keyset = format!(
            r#"[{{"kid":"lk1","key":"{}","primary":true}}]"#,
            fernet_key()?
        );
        self.vault.set(install_id, NAME, &keyset)?;
        Ok(keyset)
    }

    pub(crate) fn secret_presence(
        &self,
        config: &OperatorConfig,
    ) -> io::Result<BTreeMap<String, bool>> {
        SECRET_NAMES
            .iter()
            .map(|name| {
                self.vault.get(&config.install_id, name).map(|value| {
                    (
                        (*name).to_owned(),
                        value.is_some_and(|value| !value.is_empty()),
                    )
                })
            })
            .collect()
    }

    pub(crate) fn read_secrets<'a>(
        &self,
        config: &OperatorConfig,
        names: impl Iterator<Item = &'a str>,
    ) -> io::Result<BTreeMap<String, Option<String>>> {
        names
            .map(|name| {
                self.vault
                    .get(&config.install_id, name)
                    .map(|value| (name.to_owned(), value))
            })
            .collect()
    }
}
