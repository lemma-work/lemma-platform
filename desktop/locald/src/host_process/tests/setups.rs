//! One-time work before the services, and the stamp that says it is done.

use super::*;

#[test]
fn operator_secrets_are_ephemeral_and_backend_scoped() {
    let root = tempdir().unwrap();
    let manager = manager_in(
        &root,
        manifest(vec![
            service("frontend", &["backend"]),
            service("backend", &[]),
        ]),
    );
    manager.set_backend_environment(HashMap::from([(
        "LEMMA_OPENAI_API_KEY".into(),
        "vault-secret".into(),
    )]));

    assert_eq!(
        manager.process_spec_for_spawn("backend").unwrap().env["LEMMA_OPENAI_API_KEY"],
        "vault-secret"
    );
    assert!(!manager
        .process_spec_for_spawn("frontend")
        .unwrap()
        .env
        .contains_key("LEMMA_OPENAI_API_KEY"));
    assert!(!manager.by_id["backend"]
        .env
        .contains_key("LEMMA_OPENAI_API_KEY"));
}

#[cfg(unix)]
#[test]
fn migration_setup_receives_the_same_dynamic_backend_environment() {
    let root = tempdir().unwrap();
    let mut value = manifest(vec![
        service("frontend", &["backend"]),
        service("backend", &[]),
    ]);
    value.setup[0].command = vec!["/usr/bin/env".into()];
    let manager = manager_in(&root, value);
    manager.set_backend_environment(HashMap::from([(
        "DATABASE_URL".into(),
        "postgresql://private-guest/lemma".into(),
    )]));

    manager.run_setups().unwrap();

    let log = std::fs::read_to_string(log_dir_in(&root).join("migrations.log")).unwrap();
    assert!(log.contains("DATABASE_URL=postgresql://private-guest/lemma"));
}

#[cfg(unix)]
#[test]
fn an_optional_setup_that_fails_does_not_stop_the_stack() {
    // Seeding the connector catalog reaches the network whenever a Composio
    // key is set. A workspace that will not start because a third-party
    // catalog was unreachable would be a bad trade for a feature this
    // session may not even use, so the failure is logged and the start
    // continues. Migrations stay required: a backend running against a
    // schema it does not expect is worse than one that refuses to start.
    let root = tempdir().unwrap();
    let mut value = manifest(vec![
        service("frontend", &["backend"]),
        service("backend", &[]),
    ]);
    value.setup[0].command = vec!["/bin/sh".into(), "-c".into(), "exit 9".into()];
    value.setup[0].max_attempts = 1;
    value.setup[0].optional = true;
    let manager = manager_in(&root, value);

    manager
        .run_setups()
        .expect("an optional setup must not fail the start");
}

#[cfg(unix)]
#[test]
fn a_required_setup_that_fails_still_stops_the_stack() {
    let root = tempdir().unwrap();
    let mut value = manifest(vec![
        service("frontend", &["backend"]),
        service("backend", &[]),
    ]);
    value.setup[0].command = vec!["/bin/sh".into(), "-c".into(), "exit 9".into()];
    value.setup[0].max_attempts = 1;
    value.setup[0].optional = false;
    let manager = manager_in(&root, value);

    assert!(manager.run_setups().is_err());
}

#[cfg(unix)]
#[test]
fn migration_setup_retries_a_transient_cold_guest_failure() {
    let root = tempdir().unwrap();
    let marker = root.path().join("route-ready");
    let mut value = manifest(vec![
        service("frontend", &["backend"]),
        service("backend", &[]),
    ]);
    value.setup[0].command = vec![
        "/bin/sh".into(),
        "-c".into(),
        "if [ -f \"$1\" ]; then exit 0; fi; touch \"$1\"; exit 65".into(),
        "lemma-migration-retry".into(),
        marker.to_string_lossy().into_owned(),
    ];
    value.setup[0].max_attempts = 2;
    let manager = manager_in(&root, value);

    manager.run_setups().unwrap();

    let log = std::fs::read_to_string(log_dir_in(&root).join("migrations.log")).unwrap();
    assert!(log.contains("setup attempt 1 exited"));
    assert!(marker.is_file());
}

/// The stamp decision itself, on every platform.
///
/// The four tests that prove this end to end spawn `/bin/sh`, so they are
/// `#[cfg(unix)]` -- three failed on Windows for exactly that reason, and
/// the fourth passed there without proving anything. This is the half that
/// needs no process, and it is the half that decides whether a migration
/// runs.
#[test]
fn a_setup_reruns_unless_its_exact_stamp_was_recorded() {
    let recorded = |value: &str| Some(value.to_owned());

    // No stamp is how a setup opts out of this entirely.
    assert!(!setup_is_already_done(None, None));
    assert!(!setup_is_already_done(
        None,
        recorded("release-0.7.0").as_ref()
    ));

    // Never run before.
    assert!(!setup_is_already_done(Some("release-0.7.0"), None));

    // Run before, same work.
    assert!(setup_is_already_done(
        Some("release-0.7.0"),
        recorded("release-0.7.0").as_ref()
    ));

    // Run before, different work: a new pack, or migrations that changed
    // inside one. Skipping here is a backend starting against tables that
    // were never created.
    assert!(!setup_is_already_done(
        Some("release-0.8.0"),
        recorded("release-0.7.0").as_ref()
    ));
    // And no accidental prefix or case matching.
    assert!(!setup_is_already_done(
        Some("release-0.7.0"),
        recorded("release-0.7.0-rc1").as_ref()
    ));
    assert!(!setup_is_already_done(
        Some("release-0.7.0"),
        recorded("RELEASE-0.7.0").as_ref()
    ));
}

/// A stamped setup runs once and is skipped while its stamp holds.
///
/// Both setups ran on every start. The cost is not the SQL -- alembic's
/// no-op is one `SELECT` -- it is `env.py` importing the whole ORM graph
/// before it can decide there is nothing to do, on every launch.
#[cfg(unix)]
#[test]
fn a_stamped_setup_is_not_repeated_while_its_stamp_holds() {
    let root = tempdir().unwrap();
    let marker = root.path().join("ran");
    let mut value = manifest(vec![service("backend", &[]), service("frontend", &[])]);
    value.setup[0].command = vec![
        "/bin/sh".into(),
        "-c".into(),
        format!("echo x >> {}", marker.display()),
    ];
    value.setup[0].stamp = Some("release-0.7.0".into());
    let manager = manager_in(&root, value);

    manager.run_setups().unwrap();
    manager.run_setups().unwrap();
    manager.run_setups().unwrap();

    let runs = std::fs::read_to_string(&marker).unwrap().lines().count();
    assert_eq!(runs, 1, "a stamped setup runs once, not once per start");
}

/// A changed stamp runs it again; so does an unstamped setup.
#[cfg(unix)]
#[test]
fn a_changed_stamp_runs_the_setup_again() {
    let root = tempdir().unwrap();
    let marker = root.path().join("ran");
    let command = vec![
        "/bin/sh".into(),
        "-c".into(),
        format!("echo x >> {}", marker.display()),
    ];

    let mut first = manifest(vec![service("backend", &[]), service("frontend", &[])]);
    first.setup[0].command = command.clone();
    first.setup[0].stamp = Some("release-0.7.0".into());
    manager_in(&root, first).run_setups().unwrap();

    // A new release: the migrations it ships are not the ones already run.
    let mut second = manifest(vec![service("backend", &[]), service("frontend", &[])]);
    second.setup[0].command = command.clone();
    second.setup[0].stamp = Some("release-0.8.0".into());
    manager_in(&root, second).run_setups().unwrap();

    // No stamp at all behaves exactly as before stamps existed.
    let mut third = manifest(vec![service("backend", &[]), service("frontend", &[])]);
    third.setup[0].command = command;
    third.setup[0].stamp = None;
    manager_in(&root, third).run_setups().unwrap();

    assert_eq!(std::fs::read_to_string(&marker).unwrap().lines().count(), 3);
}

/// A failed setup is never stamped.
///
/// Stamping anything but success would let a half-finished migration be
/// skipped on the next start, which is strictly worse than running it
/// again.
#[cfg(unix)]
#[test]
fn a_failing_setup_is_not_stamped_as_done() {
    let root = tempdir().unwrap();
    let mut value = manifest(vec![service("backend", &[]), service("frontend", &[])]);
    value.setup[0].command = vec!["/usr/bin/false".into()];
    value.setup[0].stamp = Some("release-0.7.0".into());
    value.setup[0].max_attempts = 1;
    let manager = manager_in(&root, value);

    assert!(manager.run_setups().is_err());
    assert!(
        manager.recorded_setup_stamps().is_empty(),
        "a setup that failed must run again next time"
    );
}

/// A data reset makes every setup run again.
///
/// The database the migrations stamp describes is gone, but a Tier 1 reset
/// leaves the locald root standing -- so without forgetting the stamps the
/// next start would skip migrations against an empty schema and the backend
/// would come up against tables that were never created.
#[cfg(unix)]
#[test]
fn forgetting_stamps_makes_a_reset_installation_migrate_again() {
    let root = tempdir().unwrap();
    let marker = root.path().join("ran");
    let mut value = manifest(vec![service("backend", &[]), service("frontend", &[])]);
    value.setup[0].command = vec![
        "/bin/sh".into(),
        "-c".into(),
        format!("echo x >> {}", marker.display()),
    ];
    value.setup[0].stamp = Some("release-0.7.0".into());
    let manager = manager_in(&root, value);

    manager.run_setups().unwrap();
    manager.forget_setup_stamps().unwrap();
    manager.run_setups().unwrap();

    assert_eq!(std::fs::read_to_string(&marker).unwrap().lines().count(), 2);
    // Clearing twice is what a retried reset does; it must not fail.
    manager.forget_setup_stamps().unwrap();
}

/// An optional setup that *hangs* is tolerated, exactly like one that fails.
///
/// `optional` was honoured at only one of the three places a setup can
/// fail. A non-zero exit was swallowed; running out of time was not — so
/// `connector-catalog`, which is declared optional with a 600-second budget
/// precisely so an unreachable third-party catalog cannot stop a workspace,
/// took the whole stack down whenever the network blackholed instead of
/// refusing.
#[cfg(unix)]
#[test]
fn an_optional_setup_that_hangs_does_not_stop_the_stack() {
    let root = tempdir().unwrap();
    let mut value = manifest(vec![service("backend", &[]), service("frontend", &[])]);
    value.setup[0].command = vec!["/usr/bin/true".into()];
    let mut catalog = setup("connector-catalog");
    catalog.command = long_running_command();
    catalog.optional = true;
    catalog.timeout_seconds = 1;
    catalog.max_attempts = 1;
    value.setup.push(catalog);
    let manager = manager_in(&root, value);

    manager
        .run_setups()
        .expect("an optional setup that never finishes must not fail the start");
}

/// The same hang, not marked optional, still stops the start.
///
/// Without this the test above would pass just as well against a
/// `run_setups` that had stopped enforcing timeouts at all.
#[cfg(unix)]
#[test]
fn a_required_setup_that_hangs_still_stops_the_stack() {
    let root = tempdir().unwrap();
    let mut value = manifest(vec![service("backend", &[]), service("frontend", &[])]);
    value.setup[0].command = long_running_command();
    value.setup[0].optional = false;
    value.setup[0].timeout_seconds = 1;
    value.setup[0].max_attempts = 1;
    let manager = manager_in(&root, value);

    let error = manager.run_setups().unwrap_err();
    assert_eq!(error.kind(), io::ErrorKind::TimedOut);
    assert!(error.to_string().contains("migrations"), "{error}");
}
