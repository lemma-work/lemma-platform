//! A journal file this build cannot read is rebuilt, not repaired.

use super::*;
use crate::journal::schema::{EXPECTED_COLUMNS, table_columns};
use tempfile::tempdir;

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

/// The shape check has to cover every table, not the three it started with.
///
/// `runs` is the table that gained `provider_session_id`,
/// `prompt_dispatched`, `next_sequence`, `checkpoint_detail` and
/// `checkpoint_pending`. A file predating any of them passed
/// `discard_if_incompatible`, survived `CREATE TABLE IF NOT EXISTS`, and then
/// failed on every access -- the exact silent failure the check exists for, on
/// the one table it was not looking at.
#[test]
fn the_shape_check_covers_every_table_the_journal_creates() {
    let root = tempdir().unwrap();
    let path = root.path().join("journal.sqlite3");
    let journal = Journal::open(&path).unwrap();
    let created: Vec<String> = {
        let connection = journal.connection();
        let mut statement = connection
            .prepare(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'",
            )
            .unwrap();
        let rows = statement
            .query_map([], |row| row.get::<_, String>(0))
            .unwrap();
        rows.map(Result::unwrap).collect()
    };
    let checked: Vec<&str> = EXPECTED_COLUMNS.iter().map(|(table, _)| *table).collect();
    for table in &created {
        assert!(
            checked.contains(&table.as_str()),
            "{table} is created and never shape-checked, which is how a file \
             from an older build passes and then fails on every access",
        );
    }

    // And the columns named are the columns there.
    for (table, expected) in EXPECTED_COLUMNS {
        let connection = journal.connection();
        let actual = table_columns(&connection, table).unwrap();
        assert_eq!(
            actual,
            expected
                .iter()
                .map(|name| (*name).to_owned())
                .collect::<Vec<_>>(),
            "{table}'s expected columns do not match what `initialize` creates",
        );
    }
}

/// A journal whose `runs` table predates a column is rebuilt, not adopted.
#[test]
fn a_runs_table_from_an_older_build_is_discarded() {
    let root = tempdir().unwrap();
    let path = root.path().join("journal.sqlite3");
    {
        // The shape before `next_sequence` existed, and nothing else.
        let connection = rusqlite::Connection::open(&path).unwrap();
        connection
            .execute_batch(
                "CREATE TABLE runs (
                    target_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    state TEXT NOT NULL
                );",
            )
            .unwrap();
    }
    let journal = Journal::open(&path).expect("an incompatible journal is rebuilt, not refused");
    let connection = journal.connection();
    let columns = table_columns(&connection, "runs").unwrap();
    assert!(
        columns.iter().any(|column| column == "next_sequence"),
        "the rebuilt table has the shape this build needs: {columns:?}",
    );
}
