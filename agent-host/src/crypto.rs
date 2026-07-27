//! Device identity and secret storage.

use std::collections::BTreeMap;
use std::sync::{Arc, Mutex};

use base64::Engine;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use ed25519_dalek::{Signer, SigningKey};
use sha2::{Digest, Sha256};
use uuid::Uuid;

const KEYRING_SERVICE: &str = "ai.lemma.agent-host";

pub trait SecretVault: Send + Sync {
    fn get(&self, key: &str) -> anyhow::Result<Option<String>>;
    fn set(&self, key: &str, value: &str) -> anyhow::Result<()>;
    fn delete(&self, key: &str) -> anyhow::Result<()>;
}

#[derive(Clone, Default)]
pub struct KeyringVault;

impl SecretVault for KeyringVault {
    fn get(&self, key: &str) -> anyhow::Result<Option<String>> {
        let entry = keyring::Entry::new(KEYRING_SERVICE, key)?;
        match entry.get_password() {
            Ok(value) => Ok(Some(value)),
            Err(keyring::Error::NoEntry) => Ok(None),
            Err(error) => Err(error.into()),
        }
    }

    fn set(&self, key: &str, value: &str) -> anyhow::Result<()> {
        keyring::Entry::new(KEYRING_SERVICE, key)?.set_password(value)?;
        Ok(())
    }

    fn delete(&self, key: &str) -> anyhow::Result<()> {
        let entry = keyring::Entry::new(KEYRING_SERVICE, key)?;
        match entry.delete_credential() {
            Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
            Err(error) => Err(error.into()),
        }
    }
}

#[derive(Clone, Default)]
pub struct MemoryVault(Arc<Mutex<BTreeMap<String, String>>>);

impl SecretVault for MemoryVault {
    fn get(&self, key: &str) -> anyhow::Result<Option<String>> {
        Ok(self
            .0
            .lock()
            .expect("memory vault poisoned")
            .get(key)
            .cloned())
    }

    fn set(&self, key: &str, value: &str) -> anyhow::Result<()> {
        self.0
            .lock()
            .expect("memory vault poisoned")
            .insert(key.to_owned(), value.to_owned());
        Ok(())
    }

    fn delete(&self, key: &str) -> anyhow::Result<()> {
        self.0.lock().expect("memory vault poisoned").remove(key);
        Ok(())
    }
}

#[derive(Clone)]
pub struct DeviceIdentity {
    signing_key: SigningKey,
}

impl DeviceIdentity {
    pub fn load_or_create(vault: &dyn SecretVault, target_id: Uuid) -> anyhow::Result<Self> {
        let vault_key = identity_vault_key(target_id);
        if let Some(encoded) = vault.get(&vault_key)? {
            return Self::decode(&encoded);
        }
        let mut bytes = [0_u8; 32];
        getrandom::fill(&mut bytes)
            .map_err(|error| anyhow::anyhow!("could not generate a device key: {error}"))?;
        let identity = Self {
            signing_key: SigningKey::from_bytes(&bytes),
        };
        vault.set(&vault_key, &identity.encode())?;
        Ok(identity)
    }

    #[must_use]
    pub fn public_key(&self) -> String {
        URL_SAFE_NO_PAD.encode(self.signing_key.verifying_key().as_bytes())
    }

    #[must_use]
    pub fn fingerprint(&self) -> String {
        hex::encode(Sha256::digest(self.signing_key.verifying_key().as_bytes()))
    }

    #[must_use]
    pub fn sign_pairing(
        &self,
        pairing_code: &str,
        installation_id: &str,
        timestamp: i64,
        nonce: &str,
    ) -> String {
        let payload = format!(
            "lemma-agent-host-pair-v2\n{pairing_code}\n{installation_id}\n{timestamp}\n{nonce}"
        );
        URL_SAFE_NO_PAD.encode(self.signing_key.sign(payload.as_bytes()).to_bytes())
    }

    #[must_use]
    pub fn sign_token_exchange(&self, host_id: Uuid, timestamp: i64, nonce: &str) -> String {
        let payload = format!("lemma-agent-host-v2\n{host_id}\n{timestamp}\n{nonce}");
        URL_SAFE_NO_PAD.encode(self.signing_key.sign(payload.as_bytes()).to_bytes())
    }

    fn encode(&self) -> String {
        URL_SAFE_NO_PAD.encode(self.signing_key.as_bytes())
    }

    fn decode(encoded: &str) -> anyhow::Result<Self> {
        let decoded = URL_SAFE_NO_PAD.decode(encoded)?;
        let bytes: [u8; 32] = decoded
            .try_into()
            .map_err(|_| anyhow::anyhow!("stored device identity has an invalid length"))?;
        Ok(Self {
            signing_key: SigningKey::from_bytes(&bytes),
        })
    }
}

#[must_use]
pub fn identity_vault_key(target_id: Uuid) -> String {
    format!("target:{target_id}:device-identity")
}

#[must_use]
pub fn random_nonce() -> String {
    let mut bytes = [0_u8; 24];
    getrandom::fill(&mut bytes).expect("operating-system randomness is unavailable");
    URL_SAFE_NO_PAD.encode(bytes)
}

#[cfg(test)]
mod tests {
    use ed25519_dalek::{Signature, Verifier, VerifyingKey};

    use super::*;

    #[test]
    fn identity_round_trips_through_vault() {
        let vault = MemoryVault::default();
        let target = Uuid::new_v4();
        let first = DeviceIdentity::load_or_create(&vault, target).unwrap();
        let second = DeviceIdentity::load_or_create(&vault, target).unwrap();
        assert_eq!(first.public_key(), second.public_key());
    }

    #[test]
    fn token_proof_uses_server_domain_separator() {
        let vault = MemoryVault::default();
        let identity = DeviceIdentity::load_or_create(&vault, Uuid::new_v4()).unwrap();
        let host_id = Uuid::new_v4();
        let encoded = identity.sign_token_exchange(host_id, 42, "nonce");
        let signature = Signature::from_slice(&URL_SAFE_NO_PAD.decode(encoded).unwrap()).unwrap();
        let key_bytes: [u8; 32] = URL_SAFE_NO_PAD
            .decode(identity.public_key())
            .unwrap()
            .try_into()
            .unwrap();
        let verifying_key = VerifyingKey::from_bytes(&key_bytes).unwrap();
        verifying_key
            .verify(
                format!("lemma-agent-host-v2\n{host_id}\n42\nnonce").as_bytes(),
                &signature,
            )
            .unwrap();
    }
}
