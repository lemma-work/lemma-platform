//! What surrounds the migrations setup: progress-based timeouts, a newer
//! database named as such, a stamp bound to its disk, and a record of the run.

use super::*;

#[cfg(unix)]
fn migrations_running(root: &TempDir, script: &str) -> Arc<HostProcessManager> {
    let mut value = manifest(vec![service("backend", &[]), service("frontend", &[])]);
    value.setup[0].command = vec!["/bin/sh".into(), "-c".into(), script.into()];
    manager_in(root, value)
}

/// A migration that is still writing to its log is not ended at the budget
/// a silent one gets.
#[cfg(unix)]
#[test]
fn a_setup_making_progress_outlives_its_idle_limit() {
    let root = tempdir().unwrap();
    let mut value = manifest(vec![service("backend", &[]), service("frontend", &[])]);
    value.setup[0].command = vec![
        "/bin/sh".into(),
        "-c".into(),
        "for i in 1 2 3 4 5 6 7 8; do echo step $i; sleep 0.3; done".into(),
    ];
    value.setup[0].idle_timeout_seconds = Some(1);
    value.setup[0].max_attempts = 1;
    let manager = manager_in(&root, value);
    manager
        .run_setups()
        .expect("a setup that keeps logging must not be killed as idle");
}

#[cfg(unix)]
#[test]
fn a_silent_setup_is_ended_by_its_idle_limit_not_its_ceiling() {
    let root = tempdir().unwrap();
    let mut value = manifest(vec![service("backend", &[]), service("frontend", &[])]);
    value.setup[0].command = vec!["/bin/sh".into(), "-c".into(), "sleep 30".into()];
    value.setup[0].idle_timeout_seconds = Some(1);
    value.setup[0].timeout_seconds = 60;
    value.setup[0].max_attempts = 1;
    let manager = manager_in(&root, value);
    let started = Instant::now();
    let error = manager.run_setups().unwrap_err();
    assert!(started.elapsed() < Duration::from_secs(10), "{error}");
    assert!(error.to_string().contains("made no progress"), "{error}");
}

/// A database migrated by a newer Lemma is said to be one, once.
#[cfg(unix)]
#[test]
fn a_database_from_a_newer_release_is_named_and_not_retried() {
    let root = tempdir().unwrap();
    let marker = root.path().join("attempts");
    std::fs::write(root.path().join("schema-release"), "0.9.0\n").unwrap();
    let manager = migrations_running(
        &root,
        &format!(
            "echo x >> {}; echo \"FAILED: Can't locate revision identified by '0099_future'\" >&2; exit 1",
            marker.display()
        ),
    );
    let error = manager.run_setups().unwrap_err().to_string();
    assert!(error.contains("last updated by Lemma 0.9.0"), "{error}");
    assert!(error.contains("0099_future"), "{error}");
    assert!(error.contains("cannot read it"), "{error}");
    assert_eq!(
        std::fs::read_to_string(&marker).unwrap().lines().count(),
        1,
        "retrying cannot teach this build a future revision"
    );
    let update = std::fs::read(root.path().join("update.json"));
    assert!(
        update.is_err(),
        "nothing was migrated, so nothing may be left recorded as interrupted"
    );
}

#[test]
fn the_unknown_revision_is_read_out_of_alembics_error() {
    assert_eq!(
        unknown_revision("x\nERROR [alembic] Can't locate revision identified by '0041_abc'\n")
            .as_deref(),
        Some("0041_abc")
    );
    assert_eq!(unknown_revision("some other failure"), None);
    assert!(newer_database_message("r", "0.8.0", None).contains("a newer version of Lemma"));
}

/// A run is recorded while it is under way, and the release that completed
/// it is kept for the message above.
#[cfg(unix)]
#[test]
fn a_migration_is_recorded_while_running_and_cleared_when_it_succeeds() {
    let root = tempdir().unwrap();
    let update = root.path().join("update.json");
    let manager = migrations_running(
        &root,
        &format!("cat {} > {}.seen", update.display(), update.display()),
    );
    manager.run_setups().unwrap();
    let seen = std::fs::read_to_string(format!("{}.seen", update.display())).unwrap();
    assert!(seen.contains("\"phase\":\"migrating\""), "{seen}");
    assert!(
        !update.exists(),
        "a finished migration leaves no record behind"
    );
    assert_eq!(read_schema_release(root.path()).as_deref(), Some("test"));
}

/// A failure mid-migration stays recorded, so the next start says so.
#[cfg(unix)]
#[test]
fn a_failed_migration_stays_recorded_as_interrupted() {
    let root = tempdir().unwrap();
    let mut value = manifest(vec![service("backend", &[]), service("frontend", &[])]);
    value.setup[0].command = vec!["/bin/sh".into(), "-c".into(), "exit 3".into()];
    value.setup[0].max_attempts = 1;
    let manager = manager_in(&root, value);
    assert!(manager.run_setups().is_err());
    let transaction =
        crate::update_transaction::UpdateTransaction::load(root.path().join("update.json"))
            .unwrap();
    assert!(transaction.blocking_reason().is_some());
    // The next start migrates forward again rather than being refused.
    let manager = migrations_running(&root, "true");
    manager.run_setups().unwrap();
    assert!(!root.path().join("update.json").exists());
}

/// A stamp recorded against one data disk does not skip the setup on another.
#[cfg(unix)]
#[cfg(target_os = "macos")]
#[test]
fn a_replaced_data_disk_runs_its_migrations_again() {
    let root = tempdir().unwrap();
    let disk_dir = root.path().join("runtime/macos");
    std::fs::create_dir_all(&disk_dir).unwrap();
    std::fs::write(disk_dir.join("data.raw"), b"one").unwrap();
    let marker = root.path().join("ran");
    let mut value = manifest(vec![service("backend", &[]), service("frontend", &[])]);
    value.setup[0].command = vec![
        "/bin/sh".into(),
        "-c".into(),
        format!("echo x >> {}", marker.display()),
    ];
    value.setup[0].stamp = Some("release-0.8.0".into());
    let manager = manager_in(&root, value);

    manager.run_setups().unwrap();
    manager.run_setups().unwrap();
    assert_eq!(std::fs::read_to_string(&marker).unwrap().lines().count(), 1);

    // A reset or repair replaces the disk: an empty database behind the stamp.
    std::fs::remove_file(disk_dir.join("data.raw")).unwrap();
    std::thread::sleep(Duration::from_millis(20));
    std::fs::write(disk_dir.join("data.raw"), b"two").unwrap();
    manager.run_setups().unwrap();
    assert_eq!(std::fs::read_to_string(&marker).unwrap().lines().count(), 2);
    assert!(
        root.path().join(PRE_MIGRATION_DISK).exists(),
        "a database that had been migrated before is copied before migrating again"
    );
}
