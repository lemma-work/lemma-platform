//! `HostConfig`'s loading, migrations, locking and validation.

use super::*;
use tempfile::TempDir;

#[test]
fn concurrent_changes_do_not_lose_each_other() {
    // The bug: every mutation is load -> change -> save, and the writers are
    // in different processes -- the daemon's worker and supervisor, and the
    // `connect`/`disconnect`/`drain` subcommands the desktop app runs while
    // the daemon is up. Unsynchronised, the second saver writes a config it
    // loaded before the first one's change existed, so the first change is
    // silently gone. What that looked like was a pairing that reported
    // success and then was not there.
    let home = TempDir::new().unwrap();
    let paths = HostPaths::under(home.path());
    HostConfig::load_or_create(&paths).unwrap();

    let writers = 8;
    std::thread::scope(|scope| {
        for index in 0..writers {
            let paths = paths.clone();
            scope.spawn(move || {
                HostConfig::mutate(&paths, |config| {
                    config.targets.push(TargetConfig {
                        target_id: Uuid::new_v4(),
                        name: format!("target-{index}"),
                        base_url: url::Url::parse(&format!("https://{index}.example/")).unwrap(),
                        host_id: Uuid::new_v4(),
                        user_id: Uuid::new_v4(),
                        host_secret: "secret".into(),
                        enabled: true,
                        allow_insecure_http: false,
                        draining: false,
                        refresh_generation: 0,
                        session_paused: false,
                        host_execution: false,
                    });
                    Ok(true)
                })
                .unwrap();
            });
        }
    });

    let config = HostConfig::load_or_create(&paths).unwrap();
    assert_eq!(
        config.targets.len(),
        writers,
        "every change must survive: {:?}",
        config
            .targets
            .iter()
            .map(|target| &target.name)
            .collect::<Vec<_>>()
    );
}

#[test]
fn a_local_pairing_moves_off_the_retired_loopback_wildcard() {
    let target = |url: &str, insecure: bool| TargetConfig {
        target_id: Uuid::new_v4(),
        name: url.into(),
        base_url: url::Url::parse(url).unwrap(),
        host_id: Uuid::new_v4(),
        user_id: Uuid::new_v4(),
        host_secret: "secret".into(),
        enabled: true,
        allow_insecure_http: insecure,
        draining: false,
        refresh_generation: 0,
        session_paused: false,
        host_execution: false,
    };
    let mut config = HostConfig {
        installation_id: Uuid::new_v4().to_string(),
        targets: vec![
            target("http://app.127.0.0.1.sslip.io:61000/", true),
            target("http://api.127.0.0.1.sslip.io:61001/x", true),
            // Not a local pairing: plain http was never opted into.
            target("http://app.127.0.0.1.sslip.io:61002/", false),
            target("https://api.lemma.work/", false),
            target("http://app.10.0.0.7.sslip.io:61003/", true),
        ],
        max_runs: default_max_runs(),
        legacy_host_execution: false,
        max_runs_chosen: false,
        own_settings: std::collections::BTreeSet::new(),
    };
    config.migrate_retired_local_hosts();
    let urls: Vec<&str> = config
        .targets
        .iter()
        .map(|target| target.base_url.as_str())
        .collect();
    assert_eq!(
        urls,
        [
            "http://app.lemma.localhost:61000/",
            "http://app.lemma.localhost:61001/x",
            "http://app.127.0.0.1.sslip.io:61002/",
            "https://api.lemma.work/",
            "http://app.10.0.0.7.sslip.io:61003/",
        ]
    );
}

fn saved(paths: &HostPaths) -> serde_json::Value {
    serde_json::from_slice(&std::fs::read(&paths.config).unwrap()).unwrap()
}

/// Every config an older build made holds its default of two, which a
/// Mac outgrew: the third conversation queued behind the first two. A
/// limit nobody chose is lifted, and written back so the file says so.
#[test]
fn the_old_default_nobody_chose_is_lifted_and_saved() {
    let directory = TempDir::new().unwrap();
    let paths = HostPaths::under(directory.path());
    paths.ensure().unwrap();
    let pairing = serde_json::json!({
        "target_id": Uuid::new_v4(),
        "name": "hosted",
        "base_url": "https://lemma.example/",
        "host_id": Uuid::new_v4(),
        "user_id": Uuid::new_v4(),
        "host_secret": "secret",
    });
    std::fs::write(
        &paths.config,
        serde_json::json!({
            "installation_id": "installation",
            "max_runs": 2,
            "targets": [pairing],
        })
        .to_string(),
    )
    .unwrap();

    let config = HostConfig::load_or_create(&paths).unwrap();

    assert_eq!(config.max_runs, 20);
    assert!(!config.max_runs_chosen);
    assert_eq!(saved(&paths)["max_runs"], 20);
    // Nothing else in the file moved.
    assert_eq!(saved(&paths)["targets"][0]["name"], "hosted");
    assert_eq!(config.targets.len(), 1);
}

#[test]
fn a_new_config_takes_twenty_runs() {
    let directory = TempDir::new().unwrap();
    let paths = HostPaths::under(directory.path());
    let config = HostConfig::load_or_create(&paths).unwrap();
    assert_eq!((config.max_runs, config.max_runs_chosen), (20, false));
}

/// A limit the person set is theirs: two by choice stays two, and so does
/// any other number, chosen or not -- only the old default is lifted.
#[test]
fn a_chosen_limit_is_never_lifted() {
    let directory = TempDir::new().unwrap();
    let paths = HostPaths::under(directory.path());
    HostConfig::mutate(&paths, |config| {
        config.choose_max_runs(2)?;
        Ok(true)
    })
    .unwrap();
    let config = HostConfig::load_or_create(&paths).unwrap();
    assert_eq!((config.max_runs, config.max_runs_chosen), (2, true));
    assert_eq!(saved(&paths)["max_runs_chosen"], true);

    std::fs::write(
        &paths.config,
        serde_json::json!({ "installation_id": "installation", "max_runs": 5 }).to_string(),
    )
    .unwrap();
    assert_eq!(HostConfig::load_or_create(&paths).unwrap().max_runs, 5);
}

#[test]
fn a_choice_outside_what_lemma_accepts_is_refused() {
    let mut config = HostConfig {
        installation_id: "installation".into(),
        targets: Vec::new(),
        max_runs: 20,
        max_runs_chosen: false,
        legacy_host_execution: false,
        own_settings: std::collections::BTreeSet::new(),
    };
    assert!(config.choose_max_runs(0).is_err());
    assert!(config.choose_max_runs(MAX_SUPPORTED_RUNS + 1).is_err());
    assert_eq!((config.max_runs, config.max_runs_chosen), (20, false));
    config.choose_max_runs(MAX_SUPPORTED_RUNS).unwrap();
    assert!(config.validate().is_ok());
}

#[test]
fn a_change_that_declines_to_change_anything_leaves_the_file_alone() {
    let home = TempDir::new().unwrap();
    let paths = HostPaths::under(home.path());
    HostConfig::load_or_create(&paths).unwrap();
    let before = std::fs::read(&paths.config).unwrap();

    HostConfig::mutate(&paths, |config| {
        config.max_runs = 99;
        Ok(false)
    })
    .unwrap();

    assert_eq!(std::fs::read(&paths.config).unwrap(), before);
}

#[test]
fn saving_leaves_no_temporary_behind_for_another_writer_to_collide_with() {
    // A fixed `config.json.tmp` is a second race stacked on the first: two
    // savers interleave bytes into one file and both rename it, so the
    // survivor can be neither of the configs that were written.
    let home = TempDir::new().unwrap();
    let paths = HostPaths::under(home.path());
    HostConfig::load_or_create(&paths).unwrap();

    let leftovers: Vec<_> = std::fs::read_dir(home.path())
        .unwrap()
        .filter_map(|entry| entry.ok().map(|entry| entry.file_name()))
        .filter(|name| name.to_string_lossy().ends_with(".tmp"))
        .collect();
    assert!(
        leftovers.is_empty(),
        "temporaries left behind: {leftovers:?}"
    );
}

#[test]
fn a_second_agent_host_cannot_serve_the_same_data_directory() {
    // Two hosts share one pairing: commands split between them at random,
    // and whichever exits first reports available_runs=0 and marks the
    // machine draining. The workspace then flaps and dispatch latency looks
    // random, which is exactly what it did.
    let directory = TempDir::new().unwrap();
    let paths = HostPaths::under(directory.path());

    let first = paths
        .lock_single_instance()
        .expect("first host takes the lock");

    let second = paths.lock_single_instance();
    assert!(second.is_err(), "a second host was allowed to serve");
    let message = second.unwrap_err().to_string();
    assert!(
        message.contains("already serving"),
        "unhelpful message: {message}"
    );

    // Releasing it lets the next process in, so a restart is not blocked by
    // its predecessor.
    //
    // Not necessarily at once: other tests in this binary spawn processes,
    // and a child forked while `first` was open holds a copy of its
    // descriptor -- and so the lock -- until it execs and the copy closes.
    // A new process has no such children, so the wait is only a test's.
    drop(first);
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(2);
    while paths.lock_single_instance().is_err() {
        assert!(
            std::time::Instant::now() < deadline,
            "the lock was not released"
        );
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
}

/// Claiming more capacity than the backend accepts is not ambitious, it is
/// fatal: the backend closes every link whose `hello` carries it, and the
/// host stays offline for ever, with nothing on screen naming the config
/// value that caused it. Refusing at load says which field, once.
#[test]
fn rejects_a_capacity_the_backend_would_refuse_on_every_poll() {
    let config = |max_runs| HostConfig {
        legacy_host_execution: false,
        max_runs_chosen: false,
        own_settings: std::collections::BTreeSet::default(),
        installation_id: "installation".into(),
        max_runs,
        targets: Vec::new(),
    };

    assert!(config(MAX_SUPPORTED_RUNS).validate().is_ok());
    let error = config(MAX_SUPPORTED_RUNS + 1)
        .validate()
        .expect_err("a claim the backend rejects must not reach it");
    assert!(
        error.to_string().contains("max_runs"),
        "the message has to name the field: {error}"
    );
}

#[test]
fn rejects_remote_plain_http() {
    let config = HostConfig {
        legacy_host_execution: false,
        max_runs_chosen: false,
        own_settings: std::collections::BTreeSet::default(),
        installation_id: "installation".into(),
        max_runs: 1,
        targets: vec![TargetConfig {
            target_id: Uuid::new_v4(),
            name: "unsafe".into(),
            base_url: Url::parse("http://example.com").unwrap(),
            host_id: Uuid::new_v4(),
            user_id: Uuid::new_v4(),
            host_secret: "test-secret".into(),
            enabled: true,
            allow_insecure_http: true,
            draining: false,
            refresh_generation: 0,
            session_paused: false,
            host_execution: false,
        }],
    };
    assert!(config.validate().is_err());
}

#[test]
fn the_old_host_wide_switch_moves_onto_the_local_pairing_only() {
    let directory = tempfile::tempdir().unwrap();
    let paths = HostPaths::under(directory.path());
    paths.ensure().unwrap();
    let target = |name: &str, url: &str, insecure: bool| {
        serde_json::json!({
            "target_id": Uuid::new_v4(),
            "name": name,
            "base_url": url,
            "host_id": Uuid::new_v4(),
            "user_id": Uuid::new_v4(),
            "host_secret": "secret",
            "allow_insecure_http": insecure,
        })
    };
    let written = serde_json::json!({
        "installation_id": "installation",
        "host_execution": true,
        "targets": [
            target("this Mac", "http://127.0.0.1:8710/", true),
            target("hosted", "https://api.lemma.work/", false),
        ],
    });
    std::fs::write(&paths.config, serde_json::to_vec(&written).unwrap()).unwrap();

    let config = HostConfig::load_or_create(&paths).unwrap();
    assert!(config.targets[0].runs_host_commands());
    assert!(!config.targets[1].host_execution);
    assert!(!config.targets[1].is_local_install());
    assert!(config.host_execution());

    config.save(&paths).unwrap();
    let saved: serde_json::Value =
        serde_json::from_slice(&std::fs::read(&paths.config).unwrap()).unwrap();
    assert!(saved.get("host_execution").is_none(), "{saved}");
    assert_eq!(saved["targets"][0]["host_execution"], true);
}

#[test]
fn only_the_signed_in_persons_pairing_takes_work() {
    let url = Url::parse("http://127.0.0.1:8710/").unwrap();
    let pairing = |user: Uuid| TargetConfig {
        target_id: Uuid::new_v4(),
        name: "this Mac".into(),
        base_url: url.clone(),
        host_id: Uuid::new_v4(),
        user_id: user,
        host_secret: "secret".into(),
        enabled: true,
        allow_insecure_http: true,
        draining: false,
        refresh_generation: 0,
        session_paused: false,
        host_execution: true,
    };
    let (me, them) = (Uuid::new_v4(), Uuid::new_v4());
    let mut config = HostConfig {
        installation_id: "installation".into(),
        targets: vec![pairing(me), pairing(them)],
        max_runs: 1,
        legacy_host_execution: false,
        max_runs_chosen: false,
        own_settings: std::collections::BTreeSet::default(),
    };
    assert_eq!(config.apply_session(&url, Some(me)), 1);
    assert!(config.targets[0].takes_work() && config.targets[0].runs_host_commands());
    assert!(!config.targets[1].takes_work() && !config.targets[1].runs_host_commands());
    // Signing out pauses the rest; another server's pairings are untouched.
    let elsewhere = Url::parse("https://lemma.example/").unwrap();
    assert_eq!(config.apply_session(&elsewhere, None), 0);
    assert_eq!(config.apply_session(&url, None), 1);
    assert!(!config.targets[0].takes_work());
}

#[test]
fn a_remote_pairing_never_runs_host_commands_whatever_it_says() {
    let mut target: TargetConfig = serde_json::from_value(serde_json::json!({
        "target_id": Uuid::new_v4(),
        "name": "shared",
        "base_url": "https://teammate.example/",
        "host_id": Uuid::new_v4(),
        "user_id": Uuid::new_v4(),
        "host_secret": "secret",
        "host_execution": true,
    }))
    .unwrap();
    assert!(!target.runs_host_commands());
    target.allow_insecure_http = true;
    assert!(
        !target.runs_host_commands(),
        "https is never the local install"
    );
}

#[test]
fn a_target_from_before_host_secrets_is_skipped_not_fatal() {
    // Exactly the shape written by the keypair-era host. Failing the whole
    // load on it bricked every command, `connect` included, so the only
    // recovery was hand-editing the file.
    let legacy = serde_json::json!({
        "installation_id": "installation",
        "max_runs": 2,
        "targets": [
            {
                "target_id": Uuid::new_v4(),
                "name": "paired before the upgrade",
                "base_url": "http://localhost:8710/",
                "host_id": Uuid::new_v4(),
                "user_id": Uuid::new_v4(),
                "public_key_fingerprint": "0d009517e46ab181",
                "enabled": true,
                "allow_insecure_http": true,
                "draining": false,
                "refresh_generation": 0
            }
        ]
    });

    let config: HostConfig = serde_json::from_value(legacy).unwrap();

    assert!(config.targets.is_empty());
    assert_eq!(config.installation_id, "installation");
    assert_eq!(config.max_runs, 2);
}

#[test]
fn a_readable_target_survives_beside_an_unreadable_one() {
    let good = Uuid::new_v4();
    let mixed = serde_json::json!({
        "installation_id": "installation",
        "targets": [
            { "name": "unreadable", "target_id": Uuid::new_v4() },
            {
                "target_id": good,
                "name": "current",
                "base_url": "https://api.lemma.work/",
                "host_id": Uuid::new_v4(),
                "user_id": Uuid::new_v4(),
                "host_secret": "secret",
                "enabled": true,
                "allow_insecure_http": false,
                "draining": false,
                "refresh_generation": 0
            }
        ]
    });

    let config: HostConfig = serde_json::from_value(mixed).unwrap();

    assert_eq!(config.targets.len(), 1);
    assert_eq!(config.targets[0].target_id, good);
}
