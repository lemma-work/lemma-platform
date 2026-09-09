//! The database file, its shape, and refusing one this build cannot read.

use super::{
    Arc, Connection, Duration, Journal, JournalError, Mutex, MutexGuard, Path, PoisonError,
    TransactionBehavior,
};

pub(crate) const SCHEMA_VERSION: i64 = 1;

/// The columns `initialize` creates, checked against what is on disk.
///
/// Deliberately not a version counter. `event_outbox` lost a NOT NULL
/// `event_id` column without anyone bumping one, so the number said "same"
/// while the shape had changed — and because every table is
/// `CREATE TABLE IF NOT EXISTS`, the old shape survived and failed every insert.
/// Comparing the real columns needs no one to remember anything.
pub(crate) const EXPECTED_COLUMNS: &[(&str, &[&str])] = &[
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

/// Free rather than a method, so callers already holding the connection guard
/// can run it without locking a second, non-reentrant time.
pub(crate) fn check_integrity(connection: &Connection) -> Result<(), JournalError> {
    let result: String = connection.query_row("PRAGMA quick_check", [], |row| row.get(0))?;
    if result != "ok" {
        return Err(JournalError::Sql(rusqlite::Error::InvalidQuery));
    }
    Ok(())
}

pub(crate) fn open_connection(path: &Path) -> Result<Connection, JournalError> {
    let connection = Connection::open(path)?;
    connection.busy_timeout(Duration::from_secs(5))?;
    connection.pragma_update(None, "foreign_keys", "ON")?;
    connection.pragma_update(None, "journal_mode", "WAL")?;
    // NORMAL, not FULL. Under WAL, NORMAL still survives a process crash --
    // which is the failure this outbox exists for -- and gives up only the
    // guarantee that a host losing power mid-write keeps its last events. The
    // server holds the run leases and re-drives what it needs, and the cost of
    // FULL is an fsync on every streamed chunk.
    connection.pragma_update(None, "synchronous", "NORMAL")?;
    Ok(connection)
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
pub(crate) fn discard_if_incompatible(path: &Path) -> Result<(), JournalError> {
    if !path.exists() {
        return Ok(());
    }
    let mismatch = {
        let connection = open_connection(path)?;
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
        let mut companion = path.to_path_buf().into_os_string();
        companion.push(suffix);
        let _ = std::fs::remove_file(std::path::PathBuf::from(companion));
    }
    Ok(())
}

pub(crate) fn table_columns(
    connection: &Connection,
    table: &str,
) -> Result<Vec<String>, JournalError> {
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
        // Both of these run on their own short-lived connection, before the
        // process takes a long-lived one: a rebuild deletes the file, which no
        // open handle may be holding when it happens.
        discard_if_incompatible(&path)?;
        let journal = Self {
            connection: Arc::new(Mutex::new(open_connection(&path)?)),
            path,
        };
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
    #[must_use]
    pub fn path(&self) -> &Path {
        &self.path
    }

    /// Borrow this process's single journal connection.
    ///
    /// One connection, held for the life of the process, rather than one per
    /// operation. Opening and closing per call meant every streamed chunk paid
    /// an open, three pragmas and a close -- and closing the *last* connection
    /// checkpoints and unlinks the WAL, which is precisely the open/close path
    /// the bundled SQLite 3.51.1 deadlocked in. The upgraded dependency fixed
    /// that deadlock; not walking into it hundreds of times a turn is the
    /// other half.
    ///
    /// A panic elsewhere can poison the mutex, but not the connection: an
    /// in-flight transaction rolls back on drop and SQLite is left consistent.
    /// Refusing to serve the journal after an unrelated panic would turn a
    /// recoverable error into a host that can no longer record anything, so
    /// the guard is recovered rather than propagated.
    pub(crate) fn connection(&self) -> MutexGuard<'_, Connection> {
        self.connection
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
    }

    pub(crate) fn initialize(&self) -> Result<(), JournalError> {
        let mut connection = self.connection();
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
        // `check_integrity(&connection)`, not `self.integrity_check()`: the
        // guard is still held here, and the mutex behind it is not reentrant.
        check_integrity(&connection)?;
        Ok(())
    }

    pub fn integrity_check(&self) -> Result<(), JournalError> {
        check_integrity(&self.connection())
    }
}
