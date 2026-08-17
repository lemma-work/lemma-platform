//! Durable host-side command, run, checkpoint, and event journal.

use std::path::{Path, PathBuf};
use std::time::Duration;

use chrono::{DateTime, Utc};
use rusqlite::{Connection, OptionalExtension, TransactionBehavior, params};
use serde_json::Value;
use uuid::Uuid;

use crate::protocol::{
    Command, CommandRejection, Event, EventAck, EventBatch, EventType, JsonMap, RunCheckpoint,
    RunSpec, RunState,
};

const SCHEMA_VERSION: i64 = 1;

/// The columns `initialize` creates, checked against what is on disk.
///
/// Deliberately not a version counter. `event_outbox` lost a NOT NULL
/// `event_id` column without anyone bumping one, so the number said "same"
/// while the shape had changed — and because every table is
/// `CREATE TABLE IF NOT EXISTS`, the old shape survived and failed every insert.
/// Comparing the real columns needs no one to remember anything.
const EXPECTED_COLUMNS: &[(&str, &[&str])] = &[
    (
        "targets",
        &[
            "target_id",
            "connection_state",
            "last_error",
            "last_connected_at",
            "updated_at",
        ],
    ),
    (
        "command_receipts",
        &[
            "target_id",
            "command_id",
            "kind",
            "payload_digest",
            "state",
            "ack_pending",
            "received_at",
            "updated_at",
        ],
    ),
    (
        "event_outbox",
        &[
            "target_id",
            "run_id",
            "lease_epoch",
            "sequence",
            "event_json",
            "acknowledged_at",
            "created_at",
        ],
    ),
];

type PendingControl = (Vec<Uuid>, Vec<RunCheckpoint>, Vec<CommandRejection>);

/// Local dispatch-progress bookkeeping for a journaled run.
///
/// This is host-internal only (crash recovery and replay deduplication); the
/// server tracks run progress through the reported run state alone.
#[derive(Clone, Copy, Debug, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum Checkpoint {
    Accepted,
    DispatchIntent,
    ProviderAccepted,
    Running,
    Recovering,
    Terminal,
}

impl Checkpoint {
    fn for_state(state: RunState) -> Self {
        match state {
            RunState::Dispatching => Self::DispatchIntent,
            RunState::Running => Self::ProviderAccepted,
            RunState::Recovering => Self::Recovering,
            RunState::WaitingInput
            | RunState::Succeeded
            | RunState::Failed
            | RunState::Cancelled
            | RunState::DispatchUnknown => Self::Terminal,
            RunState::QueuedForHost | RunState::Leased | RunState::Accepted => Self::Accepted,
        }
    }
}

#[derive(Clone, Debug)]
pub struct Journal {
    path: PathBuf,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum AcceptOutcome {
    New,
    Duplicate,
}

#[derive(Clone, Debug)]
pub struct JournalRun {
    pub target_id: Uuid,
    pub run_id: Uuid,
    pub lease_epoch: u32,
    pub command_id: Uuid,
    pub harness_key: String,
    pub adapter_version: String,
    pub state: RunState,
    pub checkpoint: Checkpoint,
    pub spec: RunSpec,
    pub provider_session_id: Option<String>,
    pub prompt_dispatched: bool,
}

#[derive(Clone, Debug, serde::Serialize)]
pub struct TargetJournalStatus {
    pub target_id: Uuid,
    pub connection_state: String,
    pub last_error: Option<String>,
    pub last_connected_at: Option<String>,
    pub active_runs: u64,
    pub pending_events: u64,
}

#[derive(Debug, thiserror::Error)]
pub enum JournalError {
    #[error("SQLite journal error: {0}")]
    Sql(#[from] rusqlite::Error),
    #[error("journal JSON error: {0}")]
    Json(#[from] serde_json::Error),
    #[error("command {0} was replayed with a different payload")]
    CommandConflict(Uuid),
    #[error("run {0} was replayed with a different lease")]
    LeaseConflict(Uuid),
    #[error("run {0} does not exist in the journal")]
    RunMissing(Uuid),
    #[error("invalid persisted enum value {0}")]
    InvalidEnum(String),
    #[error("event acknowledgement does not match an active run")]
    AckMismatch,
}

fn table_columns(connection: &Connection, table: &str) -> Result<Vec<String>, JournalError> {
    let mut statement = connection.prepare(&format!("PRAGMA table_info({table})"))?;
    let rows = statement.query_map([], |row| row.get::<_, String>(1))?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

impl Journal {
    pub fn open(path: impl AsRef<Path>) -> Result<Self, JournalError> {
        let path = path.as_ref().to_path_buf();
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)
                .map_err(|error| rusqlite::Error::ToSqlConversionFailure(Box::new(error)))?;
        }
        let journal = Self { path };
        journal.discard_if_incompatible()?;
        journal.initialize()?;
        Ok(journal)
    }

    /// Rebuild the journal when the file on disk has a different shape.
    ///
    /// `initialize` is all `CREATE TABLE IF NOT EXISTS`, so an older table
    /// survives untouched. That is not theoretical: `event_outbox` once had a
    /// NOT NULL `event_id` column, and a host carrying it failed *every* event
    /// insert with a constraint error — accepting runs, renewing their leases,
    /// and delivering nothing, so conversations hung on "thinking" forever with
    /// no failure anyone could see.
    ///
    /// This is a local outbox for crash recovery, not a source of truth: the
    /// server holds the run leases and re-drives what it needs. Losing
    /// undelivered events is strictly better than never delivering again.
    fn discard_if_incompatible(&self) -> Result<(), JournalError> {
        if !self.path.exists() {
            return Ok(());
        }
        let mismatch = {
            let connection = self.connection()?;
            EXPECTED_COLUMNS.iter().find_map(|(table, expected)| {
                let found = table_columns(&connection, table).ok()?;
                // An absent table is fine: initialize creates it.
                (!found.is_empty() && found != *expected).then_some((*table, found))
            })
        };
        let Some((table, found)) = mismatch else {
            return Ok(());
        };
        tracing::warn!(
            %table,
            found = ?found,
            "rebuilding the Agent Host journal: it was written with a different schema"
        );
        for suffix in ["", "-wal", "-shm"] {
            let mut companion = self.path.clone().into_os_string();
            companion.push(suffix);
            let _ = std::fs::remove_file(std::path::PathBuf::from(companion));
        }
        Ok(())
    }

    #[must_use]
    pub fn path(&self) -> &Path {
        &self.path
    }

    fn connection(&self) -> Result<Connection, JournalError> {
        let connection = Connection::open(&self.path)?;
        connection.busy_timeout(Duration::from_secs(5))?;
        connection.pragma_update(None, "foreign_keys", "ON")?;
        connection.pragma_update(None, "journal_mode", "WAL")?;
        connection.pragma_update(None, "synchronous", "FULL")?;
        Ok(connection)
    }

    fn initialize(&self) -> Result<(), JournalError> {
        let mut connection = self.connection()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        transaction.execute_batch(
            r#"
            CREATE TABLE IF NOT EXISTS targets (
                target_id TEXT PRIMARY KEY,
                connection_state TEXT NOT NULL,
                last_error TEXT,
                last_connected_at TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS command_receipts (
                target_id TEXT NOT NULL,
                command_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                payload_digest TEXT NOT NULL,
                state TEXT NOT NULL,
                ack_pending INTEGER NOT NULL DEFAULT 1,
                received_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (target_id, command_id),
                FOREIGN KEY (target_id) REFERENCES targets(target_id)
            );

            CREATE TABLE IF NOT EXISTS runs (
                target_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                lease_epoch INTEGER NOT NULL,
                command_id TEXT NOT NULL,
                harness_key TEXT NOT NULL,
                adapter_version TEXT NOT NULL,
                state TEXT NOT NULL,
                checkpoint TEXT NOT NULL,
                checkpoint_detail TEXT NOT NULL,
                checkpoint_pending INTEGER NOT NULL DEFAULT 1,
                spec_json TEXT NOT NULL,
                provider_session_id TEXT,
                prompt_dispatched INTEGER NOT NULL DEFAULT 0,
                next_sequence INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (target_id, run_id),
                FOREIGN KEY (target_id, command_id)
                    REFERENCES command_receipts(target_id, command_id)
            );

            CREATE TABLE IF NOT EXISTS command_rejections (
                target_id TEXT NOT NULL,
                command_id TEXT NOT NULL,
                rejection_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (target_id, command_id),
                FOREIGN KEY (target_id) REFERENCES targets(target_id)
            );

            CREATE TABLE IF NOT EXISTS event_outbox (
                target_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                lease_epoch INTEGER NOT NULL,
                sequence INTEGER NOT NULL,
                event_json TEXT NOT NULL,
                acknowledged_at TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY (target_id, run_id, lease_epoch, sequence),
                FOREIGN KEY (target_id, run_id) REFERENCES runs(target_id, run_id)
            );

            CREATE INDEX IF NOT EXISTS ix_event_outbox_pending
              ON event_outbox(target_id, acknowledged_at, run_id, sequence);
            CREATE INDEX IF NOT EXISTS ix_runs_non_terminal
              ON runs(target_id, state, updated_at);
            "#,
        )?;
        transaction.pragma_update(None, "user_version", SCHEMA_VERSION)?;
        transaction.commit()?;
        self.integrity_check()?;
        Ok(())
    }

    pub fn integrity_check(&self) -> Result<(), JournalError> {
        let connection = self.connection()?;
        let result: String = connection.query_row("PRAGMA quick_check", [], |row| row.get(0))?;
        if result != "ok" {
            return Err(JournalError::Sql(rusqlite::Error::InvalidQuery));
        }
        Ok(())
    }

    pub fn register_target(&self, target_id: Uuid) -> Result<(), JournalError> {
        let connection = self.connection()?;
        let now = Utc::now().to_rfc3339();
        connection.execute(
            r#"
            INSERT INTO targets(target_id, connection_state, updated_at)
            VALUES (?1, 'OFFLINE', ?2)
            ON CONFLICT(target_id) DO NOTHING
            "#,
            params![target_id.to_string(), now],
        )?;
        Ok(())
    }

    pub fn remove_target(&self, target_id: Uuid) -> Result<(), JournalError> {
        let mut connection = self.connection()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let id = target_id.to_string();
        transaction.execute("DELETE FROM event_outbox WHERE target_id=?1", params![id])?;
        transaction.execute("DELETE FROM runs WHERE target_id=?1", params![id])?;
        transaction.execute(
            "DELETE FROM command_receipts WHERE target_id=?1",
            params![id],
        )?;
        transaction.execute("DELETE FROM targets WHERE target_id=?1", params![id])?;
        transaction.commit()?;
        Ok(())
    }

    pub fn update_target_state(
        &self,
        target_id: Uuid,
        state: &str,
        error: Option<&str>,
    ) -> Result<(), JournalError> {
        self.register_target(target_id)?;
        let now = Utc::now().to_rfc3339();
        let connected_at = (state == "ONLINE").then_some(now.as_str());
        self.connection()?.execute(
            r#"
            UPDATE targets
               SET connection_state = ?2,
                   last_error = ?3,
                   last_connected_at = COALESCE(?4, last_connected_at),
                   updated_at = ?5
             WHERE target_id = ?1
            "#,
            params![target_id.to_string(), state, error, connected_at, now],
        )?;
        Ok(())
    }

    pub fn accept_start(
        &self,
        target_id: Uuid,
        command: &Command,
        spec: &RunSpec,
        harness_key: &str,
        adapter_version: &str,
    ) -> Result<AcceptOutcome, JournalError> {
        self.register_target(target_id)?;
        let run_id = command
            .run_id
            .ok_or(JournalError::RunMissing(spec.agent_run_id))?;
        let lease_epoch = command
            .lease_epoch
            .ok_or(JournalError::RunMissing(spec.agent_run_id))?;
        if run_id != spec.agent_run_id {
            return Err(JournalError::LeaseConflict(run_id));
        }
        let mut connection = self.connection()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let exists: bool = transaction.query_row(
            "SELECT EXISTS(SELECT 1 FROM command_receipts WHERE target_id=?1 AND command_id=?2)",
            params![target_id.to_string(), command.command_id.to_string()],
            |row| row.get(0),
        )?;
        if exists {
            transaction.commit()?;
            return Ok(AcceptOutcome::Duplicate);
        }

        let existing_run: Option<i64> = transaction
            .query_row(
                "SELECT lease_epoch FROM runs WHERE target_id=?1 AND run_id=?2",
                params![target_id.to_string(), run_id.to_string()],
                |row| row.get(0),
            )
            .optional()?;
        if existing_run.is_some_and(|epoch| epoch != i64::from(lease_epoch)) {
            return Err(JournalError::LeaseConflict(run_id));
        }

        let now = Utc::now().to_rfc3339();
        transaction.execute(
            r#"
            INSERT INTO command_receipts(
                target_id, command_id, kind, payload_digest, state,
                ack_pending, received_at, updated_at
            ) VALUES (?1, ?2, 'START_RUN', '', 'ACCEPTED', 1, ?3, ?3)
            "#,
            params![target_id.to_string(), command.command_id.to_string(), now],
        )?;
        transaction.execute(
            r#"
            INSERT INTO runs(
                target_id, run_id, lease_epoch, command_id, harness_key,
                adapter_version, state, checkpoint, checkpoint_detail,
                checkpoint_pending, spec_json, created_at, updated_at
            ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, 'ACCEPTED', 'ACCEPTED', '{}', 1, ?7, ?8, ?8)
            "#,
            params![
                target_id.to_string(),
                run_id.to_string(),
                i64::from(lease_epoch),
                command.command_id.to_string(),
                harness_key,
                adapter_version,
                serde_json::to_string(spec)?,
                now
            ],
        )?;
        transaction.commit()?;
        Ok(AcceptOutcome::New)
    }

    pub fn record_simple_command(
        &self,
        target_id: Uuid,
        command: &Command,
    ) -> Result<AcceptOutcome, JournalError> {
        self.register_target(target_id)?;
        let connection = self.connection()?;
        let now = Utc::now().to_rfc3339();
        let changed = connection.execute(
            r#"
            INSERT INTO command_receipts(
                target_id, command_id, kind, payload_digest, state,
                ack_pending, received_at, updated_at
            ) VALUES (?1, ?2, ?3, '', 'ACCEPTED', 1, ?4, ?4)
            ON CONFLICT(target_id, command_id) DO NOTHING
            "#,
            params![
                target_id.to_string(),
                command.command_id.to_string(),
                format!("{:?}", command.kind).to_uppercase(),
                now
            ],
        )?;
        if changed == 1 {
            return Ok(AcceptOutcome::New);
        }
        Ok(AcceptOutcome::Duplicate)
    }

    pub fn checkpoint(
        &self,
        target_id: Uuid,
        run_id: Uuid,
        lease_epoch: u32,
        state: RunState,
        detail: &JsonMap,
    ) -> Result<(), JournalError> {
        let changed = self.connection()?.execute(
            r#"
            UPDATE runs
               SET checkpoint=?4, state=?5, checkpoint_detail=?6,
                   checkpoint_pending=1, updated_at=?7
             WHERE target_id=?1 AND run_id=?2 AND lease_epoch=?3
            "#,
            params![
                target_id.to_string(),
                run_id.to_string(),
                i64::from(lease_epoch),
                enum_json(Checkpoint::for_state(state))?,
                enum_json(state)?,
                serde_json::to_string(detail)?,
                Utc::now().to_rfc3339()
            ],
        )?;
        if changed == 0 {
            return Err(JournalError::RunMissing(run_id));
        }
        Ok(())
    }

    pub fn mark_dispatch_intent(
        &self,
        target_id: Uuid,
        run_id: Uuid,
        lease_epoch: u32,
        provider_session_id: &str,
    ) -> Result<(), JournalError> {
        let changed = self.connection()?.execute(
            r#"
            UPDATE runs
               SET checkpoint='DISPATCH_INTENT', state='DISPATCHING',
                   provider_session_id=?4, prompt_dispatched=1,
                   checkpoint_pending=1, updated_at=?5
             WHERE target_id=?1 AND run_id=?2 AND lease_epoch=?3
                   AND prompt_dispatched=0
            "#,
            params![
                target_id.to_string(),
                run_id.to_string(),
                i64::from(lease_epoch),
                provider_session_id,
                Utc::now().to_rfc3339()
            ],
        )?;
        if changed == 0 {
            let exists: bool = self.connection()?.query_row(
                "SELECT EXISTS(SELECT 1 FROM runs WHERE target_id=?1 AND run_id=?2 AND lease_epoch=?3)",
                params![target_id.to_string(), run_id.to_string(), i64::from(lease_epoch)],
                |row| row.get(0),
            )?;
            return if exists {
                Err(JournalError::LeaseConflict(run_id))
            } else {
                Err(JournalError::RunMissing(run_id))
            };
        }
        Ok(())
    }

    pub fn append_event(
        &self,
        target_id: Uuid,
        run_id: Uuid,
        lease_epoch: u32,
        event_type: EventType,
        object_id: Option<String>,
        payload: JsonMap,
    ) -> Result<Event, JournalError> {
        let mut connection = self.connection()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let sequence: i64 = transaction
            .query_row(
                r#"
                SELECT next_sequence
                  FROM runs WHERE target_id=?1 AND run_id=?2 AND lease_epoch=?3
                "#,
                params![
                    target_id.to_string(),
                    run_id.to_string(),
                    i64::from(lease_epoch)
                ],
                |row| row.get(0),
            )
            .optional()?
            .ok_or(JournalError::RunMissing(run_id))?;
        let event = Event {
            run_id,
            lease_epoch,
            sequence: u64::try_from(sequence)
                .map_err(|_| JournalError::InvalidEnum("negative sequence".to_owned()))?,
            event_type,
            object_id,
            payload,
        };
        transaction.execute(
            r#"
            INSERT INTO event_outbox(
                target_id, run_id, lease_epoch, sequence,
                event_json, created_at
            ) VALUES (?1, ?2, ?3, ?4, ?5, ?6)
            "#,
            params![
                target_id.to_string(),
                run_id.to_string(),
                i64::from(lease_epoch),
                sequence,
                serde_json::to_string(&event)?,
                Utc::now().to_rfc3339()
            ],
        )?;
        transaction.execute(
            "UPDATE runs SET next_sequence=next_sequence+1, updated_at=?4 WHERE target_id=?1 AND run_id=?2 AND lease_epoch=?3",
            params![
                target_id.to_string(),
                run_id.to_string(),
                i64::from(lease_epoch),
                Utc::now().to_rfc3339()
            ],
        )?;
        transaction.commit()?;
        Ok(event)
    }

    pub fn pending_events(
        &self,
        target_id: Uuid,
        limit: usize,
    ) -> Result<Vec<EventBatch>, JournalError> {
        let connection = self.connection()?;
        let mut statement = connection.prepare(
            r#"
            SELECT event_json FROM event_outbox
             WHERE target_id=?1 AND acknowledged_at IS NULL
             ORDER BY run_id, lease_epoch, sequence
             LIMIT ?2
            "#,
        )?;
        let events = statement
            .query_map(
                params![
                    target_id.to_string(),
                    i64::try_from(limit).unwrap_or(i64::MAX)
                ],
                |row| row.get::<_, String>(0),
            )?
            .collect::<Result<Vec<_>, _>>()?
            .into_iter()
            .map(|encoded| serde_json::from_str::<Event>(&encoded))
            .collect::<Result<Vec<_>, _>>()?;
        let mut batches = Vec::<EventBatch>::new();
        for event in events {
            if let Some(batch) = batches.last_mut()
                && batch.events.first().is_some_and(|first| {
                    first.run_id == event.run_id
                        && first.lease_epoch == event.lease_epoch
                        && first.sequence + u64::try_from(batch.events.len()).unwrap_or(0)
                            == event.sequence
                        && batch.events.len() < 256
                })
            {
                batch.events.push(event);
            } else {
                batches.push(EventBatch {
                    events: vec![event],
                });
            }
        }
        Ok(batches)
    }

    /// Record the backend's watermark, keeping the acknowledged events until
    /// the run terminalizes.
    ///
    /// The acknowledged copy is what makes a resend possible. The event stream
    /// on the server side is transient transport with no persistence: after a
    /// flush, eviction, or restart its watermark drops back to zero and it
    /// expects sequence 1 again. Deleting on acknowledgement threw away the
    /// only copy that could answer that, so the run could never be replayed and
    /// stayed permanently rejected. Retention is bounded by the run's own
    /// lifetime, not by the journal's.
    pub fn acknowledge_events(&self, target_id: Uuid, ack: &EventAck) -> Result<(), JournalError> {
        let mut connection = self.connection()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let state: Option<String> = transaction
            .query_row(
                "SELECT state FROM runs WHERE target_id=?1 AND run_id=?2 AND lease_epoch=?3",
                params![
                    target_id.to_string(),
                    ack.run_id.to_string(),
                    i64::from(ack.lease_epoch)
                ],
                |row| row.get(0),
            )
            .optional()?;
        let Some(state) = state else {
            return Err(JournalError::AckMismatch);
        };
        transaction.execute(
            r#"
            UPDATE event_outbox
               SET acknowledged_at=?5
             WHERE target_id=?1 AND run_id=?2 AND lease_epoch=?3
                   AND sequence<=?4 AND acknowledged_at IS NULL
            "#,
            params![
                target_id.to_string(),
                ack.run_id.to_string(),
                i64::from(ack.lease_epoch),
                i64::try_from(ack.acked_through).unwrap_or(i64::MAX),
                Utc::now().to_rfc3339()
            ],
        )?;
        // A terminal run will never be replayed: the server refuses events for
        // one, so its acknowledged rows have no further purpose.
        if enum_parse::<RunState>(&state).is_ok_and(RunState::is_terminal) {
            transaction.execute(
                r#"
                DELETE FROM event_outbox
                 WHERE target_id=?1 AND run_id=?2 AND lease_epoch=?3
                       AND acknowledged_at IS NOT NULL
                "#,
                params![
                    target_id.to_string(),
                    ack.run_id.to_string(),
                    i64::from(ack.lease_epoch)
                ],
            )?;
        }
        transaction.commit()?;
        Ok(())
    }

    /// Make every retained event for one run pending again.
    ///
    /// Used when the server rejects a batch because its stream no longer holds
    /// the history the sequence numbers assume. Resending from the start is
    /// safe: the server deduplicates by sequence and keeps the first write.
    /// Returns how many events were re-queued.
    pub fn rewind_acknowledgements(
        &self,
        target_id: Uuid,
        run_id: Uuid,
        lease_epoch: u32,
    ) -> Result<u64, JournalError> {
        let changed = self.connection()?.execute(
            r#"
            UPDATE event_outbox SET acknowledged_at=NULL
             WHERE target_id=?1 AND run_id=?2 AND lease_epoch=?3
                   AND acknowledged_at IS NOT NULL
            "#,
            params![
                target_id.to_string(),
                run_id.to_string(),
                i64::from(lease_epoch)
            ],
        )?;
        Ok(u64::try_from(changed).unwrap_or_default())
    }

    /// Give up on delivering one run's events.
    ///
    /// The rows are marked delivered without ever reaching the server, so a run
    /// whose stream the server keeps refusing stops blocking its own terminal
    /// checkpoint and stops being retried on every flush. Returns how many
    /// events were dropped.
    pub fn discard_events(
        &self,
        target_id: Uuid,
        run_id: Uuid,
        lease_epoch: u32,
    ) -> Result<u64, JournalError> {
        let changed = self.connection()?.execute(
            r#"
            DELETE FROM event_outbox
             WHERE target_id=?1 AND run_id=?2 AND lease_epoch=?3
            "#,
            params![
                target_id.to_string(),
                run_id.to_string(),
                i64::from(lease_epoch)
            ],
        )?;
        Ok(u64::try_from(changed).unwrap_or_default())
    }

    pub fn cleanup_retained(&self, now: DateTime<Utc>) -> Result<u64, JournalError> {
        let mut connection = self.connection()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let cutoff = (now - chrono::Duration::days(30)).to_rfc3339();
        let terminal_states = "'WAITING_INPUT','SUCCEEDED','FAILED','CANCELLED','DISPATCH_UNKNOWN'";
        let old_terminal_runs = format!(
            "SELECT target_id, run_id FROM runs \
             WHERE state IN ({terminal_states}) AND updated_at < ?1"
        );

        let mut deleted = 0_u64;
        deleted += u64::try_from(transaction.execute(
            &format!("DELETE FROM event_outbox WHERE (target_id, run_id) IN ({old_terminal_runs})"),
            params![cutoff],
        )?)
        .unwrap_or_default();
        deleted += u64::try_from(transaction.execute(
            &format!("DELETE FROM runs WHERE state IN ({terminal_states}) AND updated_at < ?1"),
            params![cutoff],
        )?)
        .unwrap_or_default();
        // Receipts for retained active runs are still referenced by `runs`, so
        // the NOT EXISTS guard protects them even if their own timestamp is old.
        deleted += u64::try_from(transaction.execute(
            r#"
            DELETE FROM command_receipts
             WHERE updated_at < ?1
               AND NOT EXISTS (
                    SELECT 1 FROM runs
                     WHERE runs.target_id=command_receipts.target_id
                       AND runs.command_id=command_receipts.command_id
               )
            "#,
            params![cutoff],
        )?)
        .unwrap_or_default();
        deleted += u64::try_from(transaction.execute(
            "DELETE FROM command_rejections WHERE created_at < ?1",
            params![cutoff],
        )?)
        .unwrap_or_default();
        transaction.commit()?;
        Ok(deleted)
    }

    pub fn pending_control(&self, target_id: Uuid) -> Result<PendingControl, JournalError> {
        let connection = self.connection()?;
        let mut command_statement = connection.prepare(
            "SELECT command_id FROM command_receipts WHERE target_id=?1 AND ack_pending=1 ORDER BY received_at LIMIT 256",
        )?;
        let command_ids = command_statement
            .query_map(params![target_id.to_string()], |row| {
                row.get::<_, String>(0)
            })?
            .collect::<Result<Vec<_>, _>>()?
            .into_iter()
            .map(|raw| {
                Uuid::parse_str(&raw)
                    .map_err(|_| JournalError::InvalidEnum(format!("invalid command UUID {raw}")))
            })
            .collect::<Result<Vec<_>, _>>()?;
        // Non-terminal checkpoints are also the run lease heartbeat. Keep
        // sending them after the state transition itself has been acknowledged;
        // otherwise a long-running provider turn loses its server-side lease
        // even though the Agent Host remains connected and healthy.
        let mut checkpoint_statement = connection.prepare(
            r#"
            SELECT runs.run_id, runs.lease_epoch, runs.state, runs.checkpoint_detail,
                   runs.provider_session_id
              FROM runs
             WHERE runs.target_id=?1
               AND (
                    runs.checkpoint_pending=1
                    OR runs.state NOT IN (
                        'WAITING_INPUT', 'SUCCEEDED', 'FAILED',
                        'CANCELLED', 'DISPATCH_UNKNOWN'
                    )
               )
               AND NOT (
                    runs.state IN (
                        'WAITING_INPUT', 'SUCCEEDED', 'FAILED',
                        'CANCELLED', 'DISPATCH_UNKNOWN'
                    )
                    AND EXISTS (
                        SELECT 1
                          FROM event_outbox
                         WHERE event_outbox.target_id=runs.target_id
                           AND event_outbox.run_id=runs.run_id
                           AND event_outbox.lease_epoch=runs.lease_epoch
                           AND event_outbox.acknowledged_at IS NULL
                    )
               )
             ORDER BY runs.updated_at LIMIT 256
            "#,
        )?;
        let encoded = checkpoint_statement
            .query_map(params![target_id.to_string()], |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, i64>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, Option<String>>(4)?,
                ))
            })?
            .collect::<Result<Vec<_>, _>>()?;
        let checkpoints = encoded
            .into_iter()
            .map(
                |(run_id, lease_epoch, state, detail, provider_session_id)| {
                    let mut detail: JsonMap = serde_json::from_str(&detail)?;
                    // Carried on every checkpoint, not just the one written when the
                    // session opened. A run holds a single pending-checkpoint slot,
                    // so the `Running` state that follows the first streamed token
                    // overwrites the detail of the checkpoint before it - and the
                    // first token lands milliseconds after the prompt, well inside
                    // one poll. Lemma needs this id to keep the conversation on one
                    // provider session, and reads it idempotently.
                    if let Some(session_id) = provider_session_id {
                        detail.insert(
                            "provider_session_id".to_owned(),
                            serde_json::Value::String(session_id),
                        );
                    }
                    Ok(RunCheckpoint {
                        run_id: Uuid::parse_str(&run_id).map_err(|_| {
                            JournalError::InvalidEnum(format!("invalid run UUID {run_id}"))
                        })?,
                        lease_epoch: u32::try_from(lease_epoch).map_err(|_| {
                            JournalError::InvalidEnum(format!("invalid lease epoch {lease_epoch}"))
                        })?,
                        state: enum_parse(&state)?,
                        detail,
                    })
                },
            )
            .collect::<Result<Vec<_>, JournalError>>()?;
        let mut rejection_statement = connection.prepare(
            "SELECT rejection_json FROM command_rejections WHERE target_id=?1 ORDER BY created_at LIMIT 256",
        )?;
        let rejections = rejection_statement
            .query_map(params![target_id.to_string()], |row| {
                row.get::<_, String>(0)
            })?
            .collect::<Result<Vec<_>, _>>()?
            .into_iter()
            .map(|encoded| serde_json::from_str(&encoded).map_err(JournalError::from))
            .collect::<Result<Vec<_>, _>>()?;
        Ok((command_ids, checkpoints, rejections))
    }

    pub fn record_rejection(
        &self,
        target_id: Uuid,
        rejection: &CommandRejection,
    ) -> Result<(), JournalError> {
        let connection = self.connection()?;
        connection.execute(
            r#"
            INSERT INTO command_rejections(
                target_id, command_id, rejection_json, created_at
            ) VALUES (?1, ?2, ?3, ?4)
            ON CONFLICT(target_id, command_id) DO UPDATE SET
                rejection_json=excluded.rejection_json
            "#,
            params![
                target_id.to_string(),
                rejection.command_id.to_string(),
                serde_json::to_string(rejection)?,
                Utc::now().to_rfc3339(),
            ],
        )?;
        Ok(())
    }

    pub fn mark_control_applied(
        &self,
        target_id: Uuid,
        command_ids: &[Uuid],
        checkpoints: &[RunCheckpoint],
        rejections: &[CommandRejection],
    ) -> Result<(), JournalError> {
        let mut connection = self.connection()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        for command_id in command_ids {
            transaction.execute(
                "UPDATE command_receipts SET ack_pending=0, state='ACKNOWLEDGED', updated_at=?3 WHERE target_id=?1 AND command_id=?2",
                params![
                    target_id.to_string(),
                    command_id.to_string(),
                    Utc::now().to_rfc3339()
                ],
            )?;
        }
        for checkpoint in checkpoints {
            transaction.execute(
                r#"
                UPDATE runs SET checkpoint_pending=0
                 WHERE target_id=?1 AND run_id=?2 AND lease_epoch=?3
                       AND state=?4
                "#,
                params![
                    target_id.to_string(),
                    checkpoint.run_id.to_string(),
                    i64::from(checkpoint.lease_epoch),
                    enum_json(checkpoint.state)?
                ],
            )?;
        }
        for rejection in rejections {
            transaction.execute(
                "DELETE FROM command_rejections WHERE target_id=?1 AND command_id=?2",
                params![target_id.to_string(), rejection.command_id.to_string()],
            )?;
        }
        transaction.commit()?;
        Ok(())
    }

    /// Replace a live run's Lemma MCP configuration.
    ///
    /// The credential inside it expires while long turns are still running, so
    /// Lemma sends a replacement and this is where it lands. Durable rather
    /// than held in memory because the bridge is a *separate process* that
    /// reads its endpoint from here on every request — that is the whole
    /// delivery mechanism.
    ///
    /// Fenced on the lease epoch, so a credential minted for a superseded
    /// dispatch cannot overwrite the current one.
    pub fn refresh_run_mcp(
        &self,
        target_id: Uuid,
        run_id: Uuid,
        lease_epoch: u32,
        mcp: &Value,
    ) -> Result<bool, JournalError> {
        let Some(run) = self.get_run(target_id, run_id)? else {
            return Ok(false);
        };
        if run.lease_epoch != lease_epoch || run.state.is_terminal() {
            return Ok(false);
        }
        let mut spec = run.spec;
        spec.mcp = mcp.clone();
        let connection = self.connection()?;
        let updated = connection.execute(
            "UPDATE runs SET spec_json=?3, updated_at=?4 \
             WHERE target_id=?1 AND run_id=?2",
            params![
                target_id.to_string(),
                run_id.to_string(),
                serde_json::to_string(&spec)?,
                Utc::now().to_rfc3339(),
            ],
        )?;
        Ok(updated > 0)
    }

    pub fn get_run(
        &self,
        target_id: Uuid,
        run_id: Uuid,
    ) -> Result<Option<JournalRun>, JournalError> {
        let connection = self.connection()?;
        let row = connection
            .query_row(
                r#"
                SELECT lease_epoch, command_id, harness_key, adapter_version,
                       state, checkpoint, spec_json, provider_session_id, prompt_dispatched
                  FROM runs WHERE target_id=?1 AND run_id=?2
                "#,
                params![target_id.to_string(), run_id.to_string()],
                |row| {
                    Ok((
                        row.get::<_, i64>(0)?,
                        row.get::<_, String>(1)?,
                        row.get::<_, String>(2)?,
                        row.get::<_, String>(3)?,
                        row.get::<_, String>(4)?,
                        row.get::<_, String>(5)?,
                        row.get::<_, String>(6)?,
                        row.get::<_, Option<String>>(7)?,
                        row.get::<_, bool>(8)?,
                    ))
                },
            )
            .optional()?;
        row.map(
            |(
                lease_epoch,
                command_id,
                harness_key,
                adapter_version,
                state,
                checkpoint,
                spec_json,
                provider_session_id,
                prompt_dispatched,
            )| {
                Ok(JournalRun {
                    target_id,
                    run_id,
                    lease_epoch: u32::try_from(lease_epoch).map_err(|_| {
                        JournalError::InvalidEnum(format!("invalid lease epoch {lease_epoch}"))
                    })?,
                    command_id: Uuid::parse_str(&command_id).map_err(|_| {
                        JournalError::InvalidEnum(format!("invalid command UUID {command_id}"))
                    })?,
                    harness_key,
                    adapter_version,
                    state: enum_parse(&state)?,
                    checkpoint: enum_parse(&checkpoint)?,
                    spec: serde_json::from_str(&spec_json)?,
                    provider_session_id,
                    prompt_dispatched,
                })
            },
        )
        .transpose()
    }

    pub fn recoverable_runs(&self, target_id: Uuid) -> Result<Vec<JournalRun>, JournalError> {
        let connection = self.connection()?;
        let mut statement = connection.prepare(
            r#"
            SELECT run_id FROM runs
             WHERE target_id=?1
               AND state NOT IN ('WAITING_INPUT','SUCCEEDED','FAILED','CANCELLED','DISPATCH_UNKNOWN')
             ORDER BY created_at
            "#,
        )?;
        let run_ids = statement
            .query_map(params![target_id.to_string()], |row| {
                row.get::<_, String>(0)
            })?
            .collect::<Result<Vec<_>, _>>()?;
        run_ids
            .into_iter()
            .map(|raw| {
                let run_id = Uuid::parse_str(&raw)
                    .map_err(|_| JournalError::InvalidEnum(format!("invalid run UUID {raw}")))?;
                self.get_run(target_id, run_id)?
                    .ok_or(JournalError::RunMissing(run_id))
            })
            .collect()
    }

    pub fn target_status(&self, target_id: Uuid) -> Result<TargetJournalStatus, JournalError> {
        self.register_target(target_id)?;
        let connection = self.connection()?;
        let (connection_state, last_error, last_connected_at) = connection.query_row(
            "SELECT connection_state, last_error, last_connected_at FROM targets WHERE target_id=?1",
            params![target_id.to_string()],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
        )?;
        let active_runs: i64 = connection.query_row(
            r#"
            SELECT COUNT(*) FROM runs WHERE target_id=?1
              AND state NOT IN ('WAITING_INPUT','SUCCEEDED','FAILED','CANCELLED','DISPATCH_UNKNOWN')
            "#,
            params![target_id.to_string()],
            |row| row.get(0),
        )?;
        let pending_events: i64 = connection.query_row(
            "SELECT COUNT(*) FROM event_outbox WHERE target_id=?1 AND acknowledged_at IS NULL",
            params![target_id.to_string()],
            |row| row.get(0),
        )?;
        Ok(TargetJournalStatus {
            target_id,
            connection_state,
            last_error,
            last_connected_at,
            active_runs: u64::try_from(active_runs).unwrap_or_default(),
            pending_events: u64::try_from(pending_events).unwrap_or_default(),
        })
    }
}

fn enum_json<T: serde::Serialize>(value: T) -> Result<String, serde_json::Error> {
    serde_json::to_string(&value).map(|value| value.trim_matches('"').to_owned())
}

fn enum_parse<T: serde::de::DeserializeOwned>(value: &str) -> Result<T, JournalError> {
    serde_json::from_str(&format!("\"{value}\""))
        .map_err(|_| JournalError::InvalidEnum(value.to_owned()))
}

#[cfg(test)]
mod tests {
    use chrono::Duration as ChronoDuration;
    use tempfile::TempDir;

    use super::*;
    use crate::protocol::CommandKind;

    #[test]
    fn a_journal_from_an_older_build_is_rebuilt_rather_than_left_broken() {
        // The exact shape that shipped: event_outbox carrying a NOT NULL
        // event_id this build never writes, under the *same* schema version —
        // which is why a version counter could not catch it. Left in place,
        // every append fails a constraint and the host delivers nothing while
        // looking perfectly healthy.
        let directory = TempDir::new().unwrap();
        let path = directory.path().join("journal.sqlite3");
        {
            let connection = Connection::open(&path).unwrap();
            connection
                .execute_batch(
                    r#"
                    CREATE TABLE event_outbox (
                        target_id TEXT NOT NULL,
                        run_id TEXT NOT NULL,
                        lease_epoch INTEGER NOT NULL,
                        sequence INTEGER NOT NULL,
                        event_id TEXT NOT NULL,
                        event_json TEXT NOT NULL,
                        acknowledged_at TEXT,
                        created_at TEXT NOT NULL,
                        PRIMARY KEY (target_id, run_id, lease_epoch, sequence)
                    );
                    PRAGMA user_version = 1;
                    "#,
                )
                .unwrap();
        }

        let journal = Journal::open(&path).unwrap();

        let connection = Connection::open(&path).unwrap();
        let columns: Vec<String> = connection
            .prepare("PRAGMA table_info(event_outbox)")
            .unwrap()
            .query_map([], |row| row.get::<_, String>(1))
            .unwrap()
            .map(Result::unwrap)
            .collect();
        assert!(
            !columns.iter().any(|column| column == "event_id"),
            "the stale column survived: {columns:?}"
        );
        drop(journal);
    }

    #[test]
    fn a_journal_at_the_current_version_is_left_alone() {
        let directory = TempDir::new().unwrap();
        let path = directory.path().join("journal.sqlite3");
        let target = Uuid::new_v4();
        Journal::open(&path)
            .unwrap()
            .register_target(target)
            .unwrap();

        // Reopening must not throw the registered target away.
        let reopened = Journal::open(&path).unwrap();
        reopened.register_target(target).unwrap();
    }

    fn fixture() -> (TempDir, Journal, Uuid, Command, RunSpec) {
        let directory = TempDir::new().unwrap();
        let journal = Journal::open(directory.path().join("journal.db")).unwrap();
        let target_id = Uuid::new_v4();
        let run_id = Uuid::new_v4();
        let spec = RunSpec {
            agent_run_id: run_id,
            conversation_id: Uuid::new_v4(),
            harness_id: Uuid::new_v4(),
            profile_revision: "revision".into(),
            model_name: None,
            config_selections: JsonMap::new(),
            system_prompt: "system".into(),
            prompt: vec![serde_json::json!({"role": "user", "content": "hello"})],
            resume_session_id: None,
            context: JsonMap::new(),
            mcp: serde_json::json!({
                "url": "https://lemma.test/mcp",
                "authorization": "Bearer test"
            }),
            run_deadline: Utc::now() + ChronoDuration::minutes(5),
            system_prompt_delivery: None,
        };
        let payload = serde_json::to_value(&spec).unwrap();
        let command = Command {
            command_id: Uuid::new_v4(),
            kind: CommandKind::StartRun,
            created_at: Utc::now(),
            expires_at: Utc::now() + ChronoDuration::minutes(1),
            run_id: Some(run_id),
            lease_epoch: Some(1),
            payload,
        };
        (directory, journal, target_id, command, spec)
    }

    #[test]
    fn duplicate_command_is_idempotent() {
        let (_directory, journal, target, command, spec) = fixture();
        assert_eq!(
            journal
                .accept_start(target, &command, &spec, "codex", "1.0")
                .unwrap(),
            AcceptOutcome::New
        );
        assert_eq!(
            journal
                .accept_start(target, &command, &spec, "codex", "1.0")
                .unwrap(),
            AcceptOutcome::Duplicate
        );
    }

    #[test]
    fn dispatch_intent_survives_reopen_and_prevents_blind_retry() {
        let (directory, journal, target, command, spec) = fixture();
        journal
            .accept_start(target, &command, &spec, "codex", "1.0")
            .unwrap();
        journal
            .mark_dispatch_intent(target, spec.agent_run_id, 1, "session-1")
            .unwrap();
        drop(journal);
        let reopened = Journal::open(directory.path().join("journal.db")).unwrap();
        let run = reopened
            .get_run(target, spec.agent_run_id)
            .unwrap()
            .unwrap();
        assert!(run.prompt_dispatched);
        assert_eq!(run.checkpoint, Checkpoint::DispatchIntent);
        assert!(
            reopened
                .mark_dispatch_intent(target, spec.agent_run_id, 1, "session-2")
                .is_err()
        );
    }

    #[test]
    fn event_outbox_is_contiguous_and_replayable() {
        let (_directory, journal, target, command, spec) = fixture();
        journal
            .accept_start(target, &command, &spec, "codex", "1.0")
            .unwrap();
        for index in 0..3 {
            let mut payload = JsonMap::new();
            payload.insert("index".into(), serde_json::Value::from(index));
            journal
                .append_event(
                    target,
                    spec.agent_run_id,
                    1,
                    EventType::AgentMessageChunk,
                    None,
                    payload,
                )
                .unwrap();
        }
        let batches = journal.pending_events(target, 256).unwrap();
        assert_eq!(batches.len(), 1);
        assert_eq!(
            batches[0]
                .events
                .iter()
                .map(|event| event.sequence)
                .collect::<Vec<_>>(),
            vec![1, 2, 3]
        );
        journal
            .acknowledge_events(
                target,
                &EventAck {
                    run_id: spec.agent_run_id,
                    lease_epoch: 1,
                    acked_through: 2,
                },
            )
            .unwrap();
        assert_eq!(
            journal.pending_events(target, 256).unwrap()[0].events[0].sequence,
            3
        );
    }

    fn stored_events(journal: &Journal, target: Uuid) -> i64 {
        journal
            .connection()
            .unwrap()
            .query_row(
                "SELECT COUNT(*) FROM event_outbox WHERE target_id=?1",
                params![target.to_string()],
                |row| row.get(0),
            )
            .unwrap()
    }

    fn ack_through(journal: &Journal, target: Uuid, run_id: Uuid, sequence: u64) {
        journal
            .acknowledge_events(
                target,
                &EventAck {
                    run_id,
                    lease_epoch: 1,
                    acked_through: sequence,
                },
            )
            .unwrap();
    }

    fn append_three(journal: &Journal, target: Uuid, run_id: Uuid) {
        for index in 0..3 {
            let mut payload = JsonMap::new();
            payload.insert("index".into(), serde_json::Value::from(index));
            journal
                .append_event(
                    target,
                    run_id,
                    1,
                    EventType::AgentMessageChunk,
                    None,
                    payload,
                )
                .unwrap();
        }
    }

    /// The server's event stream is transient and unbacked by persistence, so
    /// a live run has to stay replayable from here after the stream is lost.
    #[test]
    fn acknowledged_events_are_retained_until_the_run_terminalizes() {
        let (_directory, journal, target, command, spec) = fixture();
        journal
            .accept_start(target, &command, &spec, "codex", "1.0")
            .unwrap();
        append_three(&journal, target, spec.agent_run_id);

        ack_through(&journal, target, spec.agent_run_id, 3);
        assert!(journal.pending_events(target, 256).unwrap().is_empty());
        assert_eq!(
            stored_events(&journal, target),
            3,
            "an active run must keep a replayable copy of its acknowledged events"
        );

        // Once the run is terminal the server refuses its events outright, so
        // the retained copy has nothing left to answer.
        journal
            .checkpoint(
                target,
                spec.agent_run_id,
                1,
                RunState::Succeeded,
                &JsonMap::new(),
            )
            .unwrap();
        ack_through(&journal, target, spec.agent_run_id, 3);
        assert_eq!(stored_events(&journal, target), 0);
    }

    #[test]
    fn rewinding_acknowledgements_replays_a_run_from_its_first_event() {
        let (_directory, journal, target, command, spec) = fixture();
        journal
            .accept_start(target, &command, &spec, "codex", "1.0")
            .unwrap();
        append_three(&journal, target, spec.agent_run_id);
        ack_through(&journal, target, spec.agent_run_id, 3);
        assert!(journal.pending_events(target, 256).unwrap().is_empty());

        let replayed = journal
            .rewind_acknowledgements(target, spec.agent_run_id, 1)
            .unwrap();

        assert_eq!(replayed, 3);
        let batches = journal.pending_events(target, 256).unwrap();
        assert_eq!(
            batches[0]
                .events
                .iter()
                .map(|event| event.sequence)
                .collect::<Vec<_>>(),
            vec![1, 2, 3],
            "a resend has to start at sequence 1, which is what an emptied \
             server stream expects"
        );
    }

    #[test]
    fn discarding_events_unblocks_a_run_the_server_keeps_refusing() {
        let (_directory, journal, target, command, spec) = fixture();
        journal
            .accept_start(target, &command, &spec, "codex", "1.0")
            .unwrap();
        append_three(&journal, target, spec.agent_run_id);
        journal
            .checkpoint(
                target,
                spec.agent_run_id,
                1,
                RunState::Failed,
                &JsonMap::new(),
            )
            .unwrap();
        // Undeliverable events would otherwise hold back the terminal
        // checkpoint, which is the only way Lemma learns the run ended.
        assert!(journal.pending_control(target).unwrap().1.is_empty());

        assert_eq!(
            journal
                .discard_events(target, spec.agent_run_id, 1)
                .unwrap(),
            3
        );

        assert!(journal.pending_events(target, 256).unwrap().is_empty());
        let (_, checkpoints, _) = journal.pending_control(target).unwrap();
        assert_eq!(checkpoints.len(), 1);
        assert_eq!(checkpoints[0].state, RunState::Failed);
    }

    #[test]
    fn acknowledging_an_unknown_run_is_rejected() {
        let (_directory, journal, target, command, spec) = fixture();
        journal
            .accept_start(target, &command, &spec, "codex", "1.0")
            .unwrap();
        assert!(matches!(
            journal.acknowledge_events(
                target,
                &EventAck {
                    run_id: Uuid::new_v4(),
                    lease_epoch: 1,
                    acked_through: 1,
                },
            ),
            Err(JournalError::AckMismatch)
        ));
    }

    #[test]
    fn command_ack_is_cleared_but_active_checkpoint_remains_a_heartbeat() {
        let (_directory, journal, target, command, spec) = fixture();
        journal
            .accept_start(target, &command, &spec, "codex", "1.0")
            .unwrap();
        let (commands, checkpoints, rejections) = journal.pending_control(target).unwrap();
        assert_eq!(commands, vec![command.command_id]);
        assert_eq!(checkpoints.len(), 1);
        journal
            .mark_control_applied(target, &commands, &checkpoints, &rejections)
            .unwrap();
        let (commands, checkpoints, rejections) = journal.pending_control(target).unwrap();
        assert!(commands.is_empty());
        assert!(rejections.is_empty());
        assert_eq!(checkpoints.len(), 1);
        assert_eq!(checkpoints[0].state, RunState::Accepted);

        journal
            .checkpoint(
                target,
                spec.agent_run_id,
                1,
                RunState::Succeeded,
                &JsonMap::new(),
            )
            .unwrap();
        let (_, terminal, rejections) = journal.pending_control(target).unwrap();
        journal
            .mark_control_applied(target, &[], &terminal, &rejections)
            .unwrap();
        assert_eq!(
            journal.pending_control(target).unwrap(),
            (vec![], vec![], vec![])
        );
    }

    #[test]
    fn the_provider_session_rides_every_checkpoint_not_just_the_one_that_opened_it() {
        // A run has one pending-checkpoint slot, so the RUNNING that follows the
        // first streamed token overwrites the detail written a moment earlier -
        // and the first token lands well inside a single poll. Putting the id on
        // the checkpoint that happened to be current lost it every time.
        let (_directory, journal, target, command, spec) = fixture();
        journal
            .accept_start(target, &command, &spec, "codex", "1.0")
            .unwrap();
        journal
            .mark_dispatch_intent(target, spec.agent_run_id, 1, "rollout-42")
            .unwrap();
        journal
            .checkpoint(
                target,
                spec.agent_run_id,
                1,
                RunState::Running,
                &JsonMap::new(),
            )
            .unwrap();

        let (_, checkpoints, _) = journal.pending_control(target).unwrap();
        assert_eq!(checkpoints.len(), 1);
        assert_eq!(checkpoints[0].state, RunState::Running);
        assert_eq!(
            checkpoints[0].detail.get("provider_session_id"),
            Some(&serde_json::Value::String("rollout-42".to_owned())),
            "the RUNNING checkpoint dropped the session the conversation needs"
        );
    }

    #[test]
    fn a_run_that_never_opened_a_session_reports_no_session_id() {
        let (_directory, journal, target, command, spec) = fixture();
        journal
            .accept_start(target, &command, &spec, "codex", "1.0")
            .unwrap();

        let (_, checkpoints, _) = journal.pending_control(target).unwrap();
        assert_eq!(checkpoints.len(), 1);
        assert!(!checkpoints[0].detail.contains_key("provider_session_id"));
    }

    #[test]
    fn terminal_checkpoint_waits_for_terminal_event_acknowledgement() {
        let (_directory, journal, target, command, spec) = fixture();
        journal
            .accept_start(target, &command, &spec, "codex", "1.0")
            .unwrap();
        let event = journal
            .append_event(
                target,
                spec.agent_run_id,
                1,
                EventType::Terminal,
                None,
                JsonMap::new(),
            )
            .unwrap();
        journal
            .checkpoint(
                target,
                spec.agent_run_id,
                1,
                RunState::Succeeded,
                &JsonMap::new(),
            )
            .unwrap();

        let (_, checkpoints, _) = journal.pending_control(target).unwrap();
        assert!(
            checkpoints.is_empty(),
            "terminal checkpoint must not race ahead of its durable terminal event"
        );

        journal
            .acknowledge_events(
                target,
                &EventAck {
                    run_id: spec.agent_run_id,
                    lease_epoch: 1,
                    acked_through: event.sequence,
                },
            )
            .unwrap();
        let (_, checkpoints, _) = journal.pending_control(target).unwrap();
        assert_eq!(checkpoints.len(), 1);
        assert_eq!(checkpoints[0].state, RunState::Succeeded);
    }

    #[test]
    fn retention_removes_only_old_terminal_runs() {
        let (_directory, journal, target, command, spec) = fixture();
        journal
            .accept_start(target, &command, &spec, "codex", "1.0")
            .unwrap();
        journal
            .checkpoint(
                target,
                spec.agent_run_id,
                1,
                RunState::Succeeded,
                &JsonMap::new(),
            )
            .unwrap();
        journal
            .connection()
            .unwrap()
            .execute(
                "UPDATE runs SET updated_at=?1 WHERE target_id=?2 AND run_id=?3",
                params![
                    (Utc::now() - ChronoDuration::days(31)).to_rfc3339(),
                    target.to_string(),
                    spec.agent_run_id.to_string()
                ],
            )
            .unwrap();
        journal
            .connection()
            .unwrap()
            .execute(
                "UPDATE command_receipts SET updated_at=?1 WHERE target_id=?2 AND command_id=?3",
                params![
                    (Utc::now() - ChronoDuration::days(31)).to_rfc3339(),
                    target.to_string(),
                    command.command_id.to_string()
                ],
            )
            .unwrap();
        assert!(journal.cleanup_retained(Utc::now()).unwrap() >= 2);
        assert!(
            journal
                .get_run(target, spec.agent_run_id)
                .unwrap()
                .is_none()
        );

        let (_active_directory, active_journal, active_target, active_command, active_spec) =
            fixture();
        active_journal
            .accept_start(active_target, &active_command, &active_spec, "codex", "1.0")
            .unwrap();
        active_journal
            .connection()
            .unwrap()
            .execute(
                "UPDATE runs SET updated_at=?1 WHERE target_id=?2 AND run_id=?3",
                params![
                    (Utc::now() - ChronoDuration::days(31)).to_rfc3339(),
                    active_target.to_string(),
                    active_spec.agent_run_id.to_string()
                ],
            )
            .unwrap();
        active_journal.cleanup_retained(Utc::now()).unwrap();
        assert!(
            active_journal
                .get_run(active_target, active_spec.agent_run_id)
                .unwrap()
                .is_some()
        );
    }
}
