//! What a runtime update was doing when this computer last stopped.
//!
//! An update replaces the host pack and guest runtime under a stack that owns a
//! database. Until now nothing recorded that it was happening, so an update
//! interrupted by a crash, a power cut or a Quit left no trace at all: the next
//! start simply ran whatever binaries it found against whatever the database had
//! become, and the two could disagree.
//!
//! The dangerous half is schema migration. `alembic upgrade head` is
//! forward-only, so a database that is partly or wholly migrated cannot be read
//! by the version that was there before — and starting the old version against
//! it is not a failed start, it is the shape of the failure that loses data.
//! Knowing an update was interrupted *and which phase it reached* is what makes
//! that refusable rather than discoverable.
//!
//! Deliberately the same shape as [`crate::config_operations`]: a record is
//! written before the work, an unfinished one becomes `Interrupted` on the next
//! load, and nothing is ever replayed on the operator's behalf. The lesson there
//! applies here with more at stake — a settings write that replays itself is an
//! annoyance, a migration that replays itself is a restore from backup.

use std::fs;
use std::io;
use std::path::PathBuf;
use std::sync::Mutex;

use serde::{Deserialize, Serialize};

use crate::operator_config::write_private_atomic;

/// How far an update had got.
///
/// Ordered by how much has been changed, because that is what decides what may
/// safely happen next.
#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum UpdatePhase {
    /// Downloading and staging beside the running install. Nothing the running
    /// version depends on has been touched.
    Staging,
    /// Waiting for in-flight work to finish so services can stop. Still nothing
    /// mutated.
    Draining,
    /// Running schema migrations. Forward-only, so an interruption here leaves a
    /// database no earlier version can read.
    Migrating,
    /// The candidate is up and being checked. The schema has already moved.
    Validating,
}

impl UpdatePhase {
    /// Whether reaching this phase changed anything the previous version needs.
    ///
    /// Staging and draining are reversible by doing nothing; from `Migrating`
    /// onwards the database has moved forward and the old version can no longer
    /// read it.
    #[must_use]
    pub fn mutated_durable_state(self) -> bool {
        matches!(self, UpdatePhase::Migrating | UpdatePhase::Validating)
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(tag = "status", rename_all = "snake_case")]
pub enum UpdateStatus {
    /// An update is under way in this process.
    Running { phase: UpdatePhase },
    /// An update was under way when the daemon last stopped, and did not
    /// finish. Never advanced automatically: see the module comment.
    Interrupted { phase: UpdatePhase },
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
pub struct UpdateRecord {
    /// Guards against a future shape being read as this one.
    pub schema_version: u32,
    pub from_version: String,
    pub to_version: String,
    pub started_at: u128,
    #[serde(flatten)]
    pub status: UpdateStatus,
}

const SCHEMA_VERSION: u32 = 1;

pub struct UpdateTransaction {
    path: PathBuf,
    record: Mutex<Option<UpdateRecord>>,
}

impl UpdateTransaction {
    /// Read what the last run left behind, marking anything unfinished.
    ///
    /// An unreadable or future-shaped record is treated as an interrupted
    /// update at its most severe phase rather than discarded. Being wrong in
    /// that direction costs a prompt; being wrong the other way starts a version
    /// against a database it may not be able to read.
    pub fn load(path: PathBuf) -> io::Result<Self> {
        let record = match fs::read(&path) {
            Ok(raw) => Some(Self::interpret(&raw)),
            Err(error) if error.kind() == io::ErrorKind::NotFound => None,
            Err(error) => return Err(error),
        };
        let transaction = Self {
            path,
            record: Mutex::new(record),
        };
        let current = transaction.record.lock().expect("update record poisoned");
        transaction.persist(current.as_ref())?;
        drop(current);
        Ok(transaction)
    }

    fn interpret(raw: &[u8]) -> UpdateRecord {
        match serde_json::from_slice::<UpdateRecord>(raw) {
            Ok(record) if record.schema_version == SCHEMA_VERSION => UpdateRecord {
                status: match record.status {
                    UpdateStatus::Running { phase } => UpdateStatus::Interrupted { phase },
                    interrupted => interrupted,
                },
                ..record
            },
            // Unreadable, or written by a version that knows more than this one.
            // Either way an update happened here and this process cannot say how
            // far it got, so it assumes the worst rather than the most
            // convenient.
            _ => UpdateRecord {
                schema_version: SCHEMA_VERSION,
                from_version: "unknown".into(),
                to_version: "unknown".into(),
                started_at: 0,
                status: UpdateStatus::Interrupted {
                    phase: UpdatePhase::Validating,
                },
            },
        }
    }

    /// Record that an update is starting. Refuses while one is outstanding.
    pub fn begin(&self, from_version: &str, to_version: &str) -> io::Result<()> {
        let mut record = self.record.lock().expect("update record poisoned");
        if record.is_some() {
            return Err(io::Error::new(
                io::ErrorKind::AlreadyExists,
                "an update is already recorded for this installation; resolve it first",
            ));
        }
        let next = UpdateRecord {
            schema_version: SCHEMA_VERSION,
            from_version: from_version.to_owned(),
            to_version: to_version.to_owned(),
            started_at: started_now(),
            status: UpdateStatus::Running {
                phase: UpdatePhase::Staging,
            },
        };
        // Persisted before it is believed: a record this process holds but never
        // wrote is exactly the amnesia this module exists to remove.
        self.persist(Some(&next))?;
        *record = Some(next);
        Ok(())
    }

    /// Move an update forward. Only ever forward.
    pub fn advance(&self, phase: UpdatePhase) -> io::Result<()> {
        let mut record = self.record.lock().expect("update record poisoned");
        let Some(current) = record.as_ref() else {
            return Err(io::Error::other("no update is in progress"));
        };
        if !matches!(current.status, UpdateStatus::Running { .. }) {
            return Err(io::Error::other(
                "this update was interrupted and has not been resolved",
            ));
        }
        let next = UpdateRecord {
            status: UpdateStatus::Running { phase },
            ..current.clone()
        };
        self.persist(Some(&next))?;
        *record = Some(next);
        Ok(())
    }

    /// The update finished. Clearing the record is what makes the install
    /// ordinary again.
    pub fn commit(&self) -> io::Result<()> {
        let mut record = self.record.lock().expect("update record poisoned");
        self.persist(None)?;
        *record = None;
        Ok(())
    }

    /// An operator has seen the interrupted update and chosen how to proceed.
    ///
    /// Separate from `commit` on purpose: this is the only way an interrupted
    /// record goes away, and it never happens on its own.
    pub fn resolve(&self) -> io::Result<()> {
        self.commit()
    }

    #[must_use]
    pub fn snapshot(&self) -> Option<UpdateRecord> {
        self.record.lock().expect("update record poisoned").clone()
    }

    /// Why the previous version must not be started, if it must not be.
    ///
    /// An update that stopped before `Migrating` left nothing behind and the
    /// install is simply the version it was. From `Migrating` onwards the
    /// database has moved forward, and forward-only migrations mean the earlier
    /// version cannot read it — so starting is not a retry, it is the step that
    /// turns a recoverable state into a support case.
    #[must_use]
    pub fn blocking_reason(&self) -> Option<String> {
        let record = self.snapshot()?;
        let UpdateStatus::Interrupted { phase } = record.status else {
            return None;
        };
        if !phase.mutated_durable_state() {
            return None;
        }
        Some(format!(
            "An update from {} to {} stopped while it was changing this \
             installation's database, so the database may no longer be readable by \
             {}. Finish the update to {} rather than starting the older version.",
            record.from_version, record.to_version, record.from_version, record.to_version
        ))
    }
}

impl UpdateTransaction {
    fn persist(&self, record: Option<&UpdateRecord>) -> io::Result<()> {
        match record {
            Some(record) => write_private_atomic(&self.path, &serde_json::to_vec(record)?),
            None => match fs::remove_file(&self.path) {
                Ok(()) => Ok(()),
                Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
                Err(error) => Err(error),
            },
        }
    }
}

/// Epoch milliseconds, as the rest of this crate stamps time.
fn started_now() -> u128 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn transaction() -> (tempfile::TempDir, UpdateTransaction, PathBuf) {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("update.json");
        let transaction = UpdateTransaction::load(path.clone()).unwrap();
        (root, transaction, path)
    }

    /// The ordinary case: an install with no update in flight says nothing and
    /// blocks nothing.
    #[test]
    fn an_installation_that_is_not_updating_carries_no_record() {
        let (_root, transaction, path) = transaction();
        assert!(transaction.snapshot().is_none());
        assert!(transaction.blocking_reason().is_none());
        assert!(!path.exists(), "a quiet install should not grow a file");
    }

    /// An update that stopped before touching the database is recoverable by
    /// doing nothing, and must not stand in the way of the version that is
    /// still installed.
    #[test]
    fn an_update_interrupted_before_migrating_does_not_block_the_old_version() {
        let (_root, transaction, path) = transaction();
        transaction.begin("0.7.2", "0.8.0").unwrap();
        transaction.advance(UpdatePhase::Draining).unwrap();
        drop(transaction);

        let reopened = UpdateTransaction::load(path).unwrap();
        assert_eq!(
            reopened.snapshot().unwrap().status,
            UpdateStatus::Interrupted {
                phase: UpdatePhase::Draining
            },
        );
        assert!(
            reopened.blocking_reason().is_none(),
            "nothing durable changed, so the installed version is still fine"
        );
    }

    /// The case this module exists for. `alembic upgrade head` is forward-only:
    /// once it has run, the previous version cannot read the database, and
    /// starting it anyway is what turns an interrupted update into lost data.
    #[test]
    fn an_update_interrupted_while_migrating_refuses_to_start_the_old_version() {
        let (_root, transaction, path) = transaction();
        transaction.begin("0.7.2", "0.8.0").unwrap();
        transaction.advance(UpdatePhase::Migrating).unwrap();
        drop(transaction);

        let reopened = UpdateTransaction::load(path).unwrap();
        let reason = reopened
            .blocking_reason()
            .expect("a half-migrated database must not be started against silently");
        assert!(
            reason.contains("0.7.2") && reason.contains("0.8.0"),
            "{reason}"
        );
        assert!(
            reason.contains("Finish the update"),
            "a refusal without a way forward is a dead end: {reason}"
        );
    }

    /// An interrupted update is never advanced on the daemon's own initiative;
    /// only an explicit resolution clears it.
    #[test]
    fn an_interrupted_update_is_not_resumed_by_restarting() {
        let (_root, transaction, path) = transaction();
        transaction.begin("0.7.2", "0.8.0").unwrap();
        transaction.advance(UpdatePhase::Migrating).unwrap();
        drop(transaction);

        let reopened = UpdateTransaction::load(path.clone()).unwrap();
        assert!(
            reopened.advance(UpdatePhase::Validating).is_err(),
            "a process that did not start this update must not continue it"
        );
        assert!(
            reopened.begin("0.7.2", "0.8.0").is_err(),
            "nor start it again over the top"
        );

        // And it survives another restart, rather than ageing out quietly.
        drop(reopened);
        let again = UpdateTransaction::load(path).unwrap();
        assert!(again.blocking_reason().is_some());

        again.resolve().unwrap();
        assert!(again.snapshot().is_none());
        assert!(again.blocking_reason().is_none());
    }

    /// A committed update leaves the install ordinary again.
    #[test]
    fn a_committed_update_leaves_nothing_behind() {
        let (_root, transaction, path) = transaction();
        transaction.begin("0.7.2", "0.8.0").unwrap();
        transaction.advance(UpdatePhase::Migrating).unwrap();
        transaction.advance(UpdatePhase::Validating).unwrap();
        transaction.commit().unwrap();

        assert!(transaction.snapshot().is_none());
        assert!(!path.exists());
        assert!(UpdateTransaction::load(path).unwrap().snapshot().is_none());
    }

    /// A record this process cannot read is still evidence that an update
    /// happened. Assuming the worst costs a prompt; assuming the best starts a
    /// version against a database it may not understand.
    #[test]
    fn an_unreadable_record_is_treated_as_the_most_severe_interruption() {
        let (_root, _transaction, path) = transaction();
        fs::write(&path, b"{ this is not the file we wrote").unwrap();

        let reopened = UpdateTransaction::load(path).unwrap();
        assert!(
            reopened.blocking_reason().is_some(),
            "an update we cannot describe is not an update we can dismiss"
        );
    }

    /// Same for a record from a future version, which may describe phases this
    /// one has never heard of.
    #[test]
    fn a_record_from_a_newer_version_is_not_read_as_this_versions_shape() {
        let (_root, _transaction, path) = transaction();
        fs::write(
            &path,
            br#"{"schema_version":99,"from_version":"9.0.0","to_version":"9.1.0",
                 "started_at":0,"status":"running","phase":"something_new"}"#,
        )
        .unwrap();

        let reopened = UpdateTransaction::load(path).unwrap();
        assert!(reopened.blocking_reason().is_some());
    }

    /// A failed write must not leave the process believing an update began.
    /// The same guarantee `config_operations` makes, for the same reason.
    #[test]
    fn a_failed_write_does_not_claim_an_update_started() {
        let (_root, transaction, path) = transaction();
        fs::create_dir(&path).unwrap();

        assert!(transaction.begin("0.7.2", "0.8.0").is_err());
        assert!(
            transaction.snapshot().is_none(),
            "an update nobody recorded is an update nobody can recover"
        );
    }
}
