//! Stopping before the services were ever launched.

// Every guard in this file drives a real process group, so all of them
// are `#[cfg(unix)]` -- which leaves the module empty on Windows, and an
// import with nothing to import is an error under `-D warnings`.
#[cfg(unix)]
use super::*;

#[cfg(unix)]
#[test]
fn shutdown_finishes_migration_but_does_not_launch_services() {
    let root = tempdir().unwrap();
    let completed = root.path().join("migration-completed");
    let mut value = manifest(vec![
        service("backend", &[]),
        service("frontend", &["backend"]),
    ]);
    value.setup[0].command = vec![
        "/bin/sh".into(),
        "-c".into(),
        "printf committed > \"$1\"".into(),
        "migration".into(),
        completed.to_string_lossy().into_owned(),
    ];
    let manager = manager_in(&root, value);
    let lifecycle = crate::lifecycle::Lifecycle::default();
    lifecycle.begin().unwrap();
    let error = manager
        .start_all_cancellable(
            |stage| {
                if stage == "migrations" {
                    lifecycle.request_shutdown();
                }
            },
            || lifecycle.checkpoint(),
        )
        .unwrap_err();
    lifecycle.finish();
    lifecycle.wait_idle();
    assert_eq!(error.kind(), io::ErrorKind::Interrupted);
    assert_eq!(fs::read_to_string(completed).unwrap(), "committed");
    assert!(manager.status().iter().all(|process| !process.running));
    assert!(!manager.desired_running());
    assert!(lifecycle.begin().is_err());
}

#[cfg(unix)]
#[test]
fn shutdown_before_start_does_not_run_migrations() {
    let root = tempdir().unwrap();
    let mut value = manifest(vec![
        service("backend", &[]),
        service("frontend", &["backend"]),
    ]);
    value.setup[0].command = vec!["/usr/bin/false".into()];
    let manager = manager_in(&root, value);
    let lifecycle = crate::lifecycle::Lifecycle::default();
    lifecycle.request_shutdown();
    let error = manager
        .start_all_cancellable(
            |_| panic!("cancelled startup cannot enter a stage"),
            || lifecycle.checkpoint(),
        )
        .unwrap_err();
    assert_eq!(error.kind(), io::ErrorKind::Interrupted);
    assert!(manager.status().iter().all(|process| !process.running));
}
