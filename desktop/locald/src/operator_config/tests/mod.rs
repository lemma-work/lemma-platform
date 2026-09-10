//! The operator config's guards, grouped the way the code they cover is
//! grouped.

mod backend_env;
mod cleanup;
mod discovery;
mod migration;
mod sections;
mod validation;
mod vault_cache;

use super::*;
use tempfile::tempdir;

#[derive(Default)]
pub(super) struct MemoryVault(Mutex<BTreeMap<String, String>>);

impl SecretVault for MemoryVault {
    fn get(&self, install_id: &str, name: &str) -> io::Result<Option<String>> {
        Ok(self
            .0
            .lock()
            .unwrap()
            .get(&format!("{install_id}:{name}"))
            .cloned())
    }

    fn set(&self, install_id: &str, name: &str, value: &str) -> io::Result<()> {
        self.0
            .lock()
            .unwrap()
            .insert(format!("{install_id}:{name}"), value.into());
        Ok(())
    }

    fn delete(&self, install_id: &str, name: &str) -> io::Result<()> {
        self.0
            .lock()
            .unwrap()
            .remove(&format!("{install_id}:{name}"));
        Ok(())
    }
}

/// A vault that remembers what was asked of it, and in what order.
#[derive(Default)]
pub(super) struct CountingVault {
    inner: MemoryVault,
    reads: Mutex<Vec<String>>,
}

impl CountingVault {
    fn reads_of(&self, name: &str) -> usize {
        self.reads
            .lock()
            .unwrap()
            .iter()
            .filter(|read| *read == name)
            .count()
    }
}

impl SecretVault for CountingVault {
    fn get(&self, install_id: &str, name: &str) -> io::Result<Option<String>> {
        self.reads.lock().unwrap().push(name.to_owned());
        self.inner.get(install_id, name)
    }

    fn set(&self, install_id: &str, name: &str, value: &str) -> io::Result<()> {
        self.inner.set(install_id, name, value)
    }

    fn delete(&self, install_id: &str, name: &str) -> io::Result<()> {
        self.inner.delete(install_id, name)
    }
}

pub(super) struct FixedModelProviderProbe;

impl ModelProviderProbe for FixedModelProviderProbe {
    fn discover(&self, _profile: &AiProfile, _api_key: Option<&str>) -> io::Result<Vec<String>> {
        Ok(vec!["alpha-model".into(), "zeta-model".into()])
    }
}
