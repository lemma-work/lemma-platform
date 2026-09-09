//! A journal file this build cannot read is rebuilt, not repaired.

use super::*;

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
