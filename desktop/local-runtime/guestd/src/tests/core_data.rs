//! Postgres' volume, its major version, and the databases in it.

use super::*;

/// A data reset without the literal confirmation destroys nothing.
///
/// This is the only global destructive verb in the table, so a replayed or
/// malformed frame must not be able to reach it.
#[test]
fn a_data_reset_without_explicit_confirmation_is_refused() {
    let root = tempdir().unwrap();
    let service = GuestService::new(
        FakeEngine::new(vec![]),
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();

    assert!(service.reset_data(json!({})).is_err());
    assert!(service.reset_data(json!({"confirm": "yes"})).is_err());
    assert!(
        service.engine.commands.lock().unwrap().is_empty(),
        "a refused reset must not reach the engine at all"
    );
}

/// Containers go before the volumes and workspaces they hold.
///
/// Removing a workspace directory while a sandbox is still bind-mounted
/// onto it, or a volume while Postgres still has it open, is the difference
/// between a clean reset and a guest in an unexplainable state. The order is
/// the correctness property, so it is what this asserts.
#[test]
fn a_data_reset_removes_holders_before_the_data_they_hold() {
    let root = tempdir().unwrap();
    let workspaces = root.path().join("workspaces");
    fs::create_dir_all(workspaces.join("sandbox-one")).unwrap();
    fs::create_dir_all(workspaces.join("sandbox-two")).unwrap();
    fs::write(workspaces.join("sandbox-one/notes.md"), b"user work").unwrap();

    let service = GuestService::new(
        FakeEngine::new(vec![
            // `inspect` answers with an array; anything else reads as absent.
            output(true, "[{}]"),     // inspect supertokens: present
            output(true, ""),         // rm supertokens
            output(true, "[{}]"),     // inspect redis: present
            output(true, ""),         // rm redis
            output(true, "[{}]"),     // inspect postgres: present
            output(true, ""),         // rm postgres
            output(true, "abc123\n"), // ps --filter label=...
            output(true, ""),         // rm the sandbox container
            output(true, ""),         // volume rm lemma-postgres-data
            output(true, ""),         // volume rm lemma-redis-data
        ]),
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();

    let result = service
        .reset_data(json!({"confirm": "reset-local-data"}))
        .unwrap();

    assert_eq!(result["removed_containers"], 4);
    assert_eq!(result["removed_volumes"], 2);
    assert_eq!(result["removed_workspaces"], 2);
    assert!(!workspaces.join("sandbox-one").exists());
    assert!(!workspaces.join("sandbox-two").exists());

    let commands = service.engine.commands.lock().unwrap();
    let position = |predicate: &dyn Fn(&Vec<String>) -> bool| commands.iter().position(predicate);
    let core_removed = position(&|command: &Vec<String>| {
        command.first() == Some(&"rm".to_owned())
            && command.iter().any(|part| part == "lemma-core-postgres")
    })
    .expect("core containers are removed");
    let sandbox_removed = position(&|command: &Vec<String>| {
        command.first() == Some(&"rm".to_owned()) && command.iter().any(|part| part == "abc123")
    })
    .expect("sandbox containers are removed");
    let volume_removed = position(&|command: &Vec<String>| {
        command.first() == Some(&"volume".to_owned()) && command.get(1) == Some(&"rm".to_owned())
    })
    .expect("volumes are removed");

    assert!(
        core_removed < volume_removed,
        "Postgres must let go of its volume before the volume is removed"
    );
    assert!(
        sandbox_removed < volume_removed,
        "sandboxes are removed before the data they mount"
    );
}

/// A Postgres major bump is refused before the container is ever run.
///
/// This is the pg16 -> pg18 case that shipped. Without the check, the new
/// server starts against the old cluster, refuses, and the user waits out a
/// 120-second `pg_isready` timeout to be told nothing useful. The assertion
/// that matters is not just the error -- it is that **no `run` reached the
/// engine**, because the whole point is to fail in about a second.
#[test]
fn a_postgres_major_bump_is_refused_before_the_container_starts() {
    let root = tempdir().unwrap();
    let cluster = tempdir().unwrap();
    std::fs::write(cluster.path().join("PG_VERSION"), "16\n").unwrap();
    let inspect = output(
        true,
        &serde_json::to_string(&json!([{ "Mountpoint": cluster.path() }])).unwrap(),
    );
    let service = GuestService::new(
        FakeEngine::new(vec![
            output(true, ""),     // ensure_volume: inspect succeeds, volume exists
            inspect,              // postgres_data_major: where does it live
            output(true, "18\n"), // postgres_image_major: what does the image ship
        ]),
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();

    let error = service
        .ensure_postgres(&core_parameters("docker.io/pgvector/pgvector:0.8.3-pg18"))
        .unwrap_err();

    assert_eq!(error.code, "postgres_data_incompatible");
    assert_eq!(error.status_code, 409);
    assert!(!error.retryable, "retrying cannot help; resetting can");
    assert!(error.message.contains("PostgreSQL 16"), "{}", error.message);
    assert!(error.message.contains("PostgreSQL 18"), "{}", error.message);
    assert!(
        error.message.contains(DATA_RESET_MARKER),
        "the phrase locald maps to a reset button: {}",
        error.message
    );
    let commands = service.engine.commands.lock().unwrap();
    assert!(
        !commands
            .iter()
            .any(|command| command.first() == Some(&"run".to_owned())
                && command.iter().any(|part| part == "--name")),
        "no core container may be started against an unreadable cluster: {commands:?}"
    );
}

/// A fresh volume has no `PG_VERSION`, which is the common case.
///
/// If this cost anything -- or worse, refused -- every first run would
/// break. So an unreadable version on either side means proceed.
#[test]
fn a_fresh_postgres_volume_is_not_treated_as_incompatible() {
    let root = tempdir().unwrap();
    let empty = tempdir().unwrap();
    let inspect = output(
        true,
        &serde_json::to_string(&json!([{ "Mountpoint": empty.path() }])).unwrap(),
    );
    let service = GuestService::new(
        FakeEngine::new(vec![inspect, output(true, "18\n")]),
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();

    service
        .refuse_incompatible_postgres_data("docker.io/pgvector/pgvector:0.8.3-pg18")
        .expect("a volume with no cluster on it yet is not a mismatch");
}

/// An image that does not report `PG_MAJOR` degrades to today's behaviour
/// rather than blocking every start.
#[test]
fn an_image_that_cannot_be_asked_its_version_does_not_block_startup() {
    let root = tempdir().unwrap();
    let cluster = tempdir().unwrap();
    std::fs::write(cluster.path().join("PG_VERSION"), "16\n").unwrap();
    let inspect = output(
        true,
        &serde_json::to_string(&json!([{ "Mountpoint": cluster.path() }])).unwrap(),
    );
    let service = GuestService::new(
        FakeEngine::new(vec![inspect, output(false, "")]),
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();

    service
        .refuse_incompatible_postgres_data("some/image:without-pg-major")
        .expect("an unanswerable image is not evidence of a mismatch");
}

#[test]
fn ensure_volume_reuses_data_preserved_across_container_cache_repair() {
    let root = tempdir().unwrap();
    let warning = Output {
        status: std::process::ExitStatus::from_raw(1),
        stdout: vec![],
        stderr: b"time=\"2026-07-23T11:33:36Z\" level=warning msg=\"volume \\\"lemma-postgres-data\\\" already exists and will be returned as-is\""
            .to_vec(),
    };
    let service = GuestService::new(
        FakeEngine::new(vec![output(false, ""), warning]),
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();

    service.ensure_volume("lemma-postgres-data").unwrap();

    assert_eq!(
        service.engine.commands.lock().unwrap().as_slice(),
        [
            vec![
                "volume".to_owned(),
                "inspect".to_owned(),
                "lemma-postgres-data".to_owned(),
            ],
            vec![
                "volume".to_owned(),
                "create".to_owned(),
                "lemma-postgres-data".to_owned(),
            ],
        ]
    );
}

#[test]
fn ensure_volume_rejects_unrelated_creation_failures() {
    let root = tempdir().unwrap();
    let service = GuestService::new(
        FakeEngine::new(vec![output(false, ""), output(false, "")]),
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();

    let error = service.ensure_volume("lemma-postgres-data").unwrap_err();

    assert_eq!(error.code, "guest_engine_failed");
    assert_eq!(error.message, "not found");
}

/// The cluster is written inside the volume the user is told holds it.
///
/// Behavioural, not a source grep. The first version of this test searched
/// its own file for the literal it was asserting -- which its own assertion
/// satisfied, so it passed with the bug reinstated. Read the two values and
/// compare them.
#[test]
fn the_cluster_path_and_the_volume_mount_are_the_same_path() {
    let (environment, arguments) = postgres_container_spec("pw");

    let cluster = environment
        .get("PGDATA")
        .expect("PGDATA is pinned rather than inherited from the image");

    let volume = arguments
        .windows(2)
        .find(|pair| pair[0] == "--volume")
        .map(|pair| pair[1].clone())
        .expect("the container mounts its data volume");
    let (name, mounted_at) = volume
        .split_once(':')
        .expect("a volume argument is name:path");

    assert_eq!(name, "lemma-postgres-data");
    assert_eq!(
        cluster, mounted_at,
        "the cluster is written to {cluster} but the volume is mounted at \
         {mounted_at}; anything written outside the volume is not the \
         user's database, it is the container's scratch space",
    );

    // The image we pin puts its own PGDATA at /var/lib/postgresql/18/docker
    // and declares VOLUME /var/lib/postgresql. Inheriting either is what
    // put the database outside this volume in the first place.
    assert!(
        !cluster.contains("/18/"),
        "a version-numbered cluster path is the image's, and it moves",
    );
}

/// The message a user actually got, classified the way it should have been.
///
/// This is the real tail of the official image's refusal, which reached a
/// user as the entire explanation for an install that stopped at 30% --
/// appended to a nerdctl "cannot exec in a stopped state", with "Try again"
/// as the only button, three times.
#[test]
fn postgres_refusing_its_data_is_recognised_from_what_it_actually_prints() {
    for refusal in [
        "PostgreSQL Database directory appears to contain a database; Skipping initialization",
        "FATAL:  database files are incompatible with server",
        "DETAIL:  The data directory was initialized by PostgreSQL version 16, which is not \
         compatible with this version 18.4.",
        "An upgrade is required. See https://github.com/docker-library/postgres/issues/37 \
         for a discussion around this process, and suggestions for how to do so.",
        "could not read file \"global/pg_control\": No such file or directory",
    ] {
        assert!(
            postgres_refused_its_data(refusal),
            "this must offer a reset, not a retry: {refusal}",
        );
    }
}

/// And a transient failure still retries, because a wrong match here offers
/// to delete somebody's database over a blip.
#[test]
fn a_transient_postgres_failure_is_never_read_as_unusable_data() {
    for transient in [
        "psql: error: FATAL: the database system is shutting down",
        "psql: error: FATAL: the database system is starting up",
        "could not connect to server: Connection refused",
        "time=\"2026-08-25T17:49:20Z\" level=fatal msg=\"cannot exec in a stopped state\"",
        "LOG:  database system was not properly shut down; automatic recovery in progress",
        "",
    ] {
        assert!(
            !postgres_refused_its_data(transient),
            "this is retryable and must not offer to erase data: {transient}",
        );
    }
}

#[test]
fn database_command_retries_through_postgres_initialization_restart() {
    let root = tempdir().unwrap();
    let shutting_down = Output {
        status: std::process::ExitStatus::from_raw(2),
        stdout: vec![],
        stderr: b"psql: error: FATAL: the database system is shutting down".to_vec(),
    };
    let service = GuestService::new(
        FakeEngine::new(vec![shutting_down, output(true, "1\n")]),
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();

    let value = service
        .wait_engine_output(
            &["exec".into(), "lemma-core-postgres".into()],
            UNHURRIED_TEST_TIMEOUT_SECS,
        )
        .unwrap();

    assert_eq!(value, "1");
    assert_eq!(service.engine.commands.lock().unwrap().len(), 2);
}

#[test]
fn database_provisioning_accepts_an_ambiguous_committed_create() {
    let root = tempdir().unwrap();
    let disconnected_after_commit = Output {
        status: std::process::ExitStatus::from_raw(1),
        stdout: vec![],
        stderr: b"time=now level=fatal msg=\"exec failed with exit code 1\"".to_vec(),
    };
    let service = GuestService::new(
        FakeEngine::new(vec![
            output(true, ""),
            disconnected_after_commit,
            output(true, "1\n"),
        ]),
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();

    service
        .ensure_database("lemma_datastore", UNHURRIED_TEST_TIMEOUT_SECS)
        .unwrap();

    let commands = service.engine.commands.lock().unwrap();
    assert_eq!(commands.len(), 3);
    assert_eq!(commands[0][2], "psql");
    assert_eq!(commands[1][2], "createdb");
    assert_eq!(commands[2][2], "psql");
}

#[test]
fn engine_error_prefers_actionable_failure_after_warnings() {
    let diagnostic = concat!(
        "time=now level=warning msg=\"volume already exists\"\n",
        "time=now level=fatal msg=\"failed to create task: missing snapshot\"\n",
    );

    assert_eq!(
        redact_engine_error(diagnostic),
        "time=now level=fatal msg=\"failed to create task: missing snapshot\""
    );
}
