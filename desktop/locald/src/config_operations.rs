//! Settings writes are not replayed when the UI loses its event stream.
//! Persist their outcome so a reconnect can distinguish completion from an
//! interrupted apply that needs the operator to inspect the current settings.
use std::collections::BTreeMap;
use std::fs;
use std::io;
use std::path::PathBuf;
use std::sync::Mutex;

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::operator_config::write_private_atomic;

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(tag = "status", rename_all = "snake_case")]
pub enum ConfigOperation {
    Applying,
    Succeeded { operator: Value },
    Failed { code: String, message: String },
    Interrupted,
}

pub struct ConfigOperations {
    path: PathBuf,
    records: Mutex<BTreeMap<String, ConfigOperation>>,
}

impl ConfigOperations {
    pub fn load(path: PathBuf) -> io::Result<Self> {
        let mut records: BTreeMap<String, ConfigOperation> = if path.exists() {
            serde_json::from_slice(&fs::read(&path)?)?
        } else {
            BTreeMap::new()
        };
        for record in records.values_mut() {
            if matches!(record, ConfigOperation::Applying) {
                *record = ConfigOperation::Interrupted;
            }
        }
        let journal = Self {
            path,
            records: Mutex::new(records),
        };
        journal.persist(&journal.records.lock().expect("config operations poisoned"))?;
        Ok(journal)
    }

    pub fn begin(&self, id: &str) -> io::Result<()> {
        if id.is_empty() || id.len() > 128 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "settings operation needs an id of 1–128 bytes",
            ));
        }
        let mut records = self.records.lock().expect("config operations poisoned");
        if records.contains_key(id) {
            return Err(io::Error::new(
                io::ErrorKind::AlreadyExists,
                "settings operation already recorded; refresh its status before trying again",
            ));
        }
        let mut next = records.clone();
        next.insert(id.into(), ConfigOperation::Applying);
        self.persist(&next)?;
        *records = next;
        Ok(())
    }

    pub fn finish(&self, id: &str, outcome: ConfigOperation) -> io::Result<()> {
        let mut records = self.records.lock().expect("config operations poisoned");
        if !matches!(records.get(id), Some(ConfigOperation::Applying)) {
            return Err(io::Error::other("settings operation is not applying"));
        }
        let mut next = records.clone();
        next.insert(id.into(), outcome);
        self.persist(&next)?;
        *records = next;
        Ok(())
    }

    pub fn snapshot(&self) -> BTreeMap<String, ConfigOperation> {
        self.records
            .lock()
            .expect("config operations poisoned")
            .clone()
    }

    fn persist(&self, records: &BTreeMap<String, ConfigOperation>) -> io::Result<()> {
        write_private_atomic(&self.path, &serde_json::to_vec(records)?)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn failed_journal_write_does_not_claim_an_operation_was_started() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("operations.json");
        let journal = ConfigOperations::load(path.clone()).unwrap();
        fs::remove_file(&path).unwrap();
        fs::create_dir(&path).unwrap();
        assert!(journal.begin("save").is_err());
        assert!(journal.snapshot().is_empty());
    }

    #[test]
    fn restart_preserves_outcomes_and_never_replays_uncertain_writes() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("operations.json");
        let journal = ConfigOperations::load(path.clone()).unwrap();
        journal.begin("complete").unwrap();
        journal
            .finish(
                "complete",
                ConfigOperation::Succeeded {
                    operator: serde_json::json!({"config":{"revision":2}}),
                },
            )
            .unwrap();
        journal.begin("unfinished").unwrap();
        drop(journal);
        let journal = ConfigOperations::load(path).unwrap();
        assert!(matches!(
            journal.snapshot()["complete"],
            ConfigOperation::Succeeded { .. }
        ));
        assert!(matches!(
            journal.snapshot()["unfinished"],
            ConfigOperation::Interrupted
        ));
        assert!(journal.begin("complete").is_err());
        assert!(journal.begin("unfinished").is_err());
    }
}
