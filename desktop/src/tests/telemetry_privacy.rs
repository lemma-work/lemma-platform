//! The two telemetry promises that must not have races or holes in them.

use super::*;
use crate::telemetry::{destination_is_safe, set_enabled};

/// An event that is mid-update holds off every other change to the file.
///
/// Every write of `telemetry.json` is atomic, and that says nothing about two
/// of them overlapping. `install_id` and `set_enabled` each read the state,
/// change one field and write it back -- so an opt-out saved between an
/// event's read and its write was replaced by the state that event had read a
/// moment earlier, and telemetry carried on after the user turned it off.
///
/// Driven rather than raced. Two threads doing this quickly enough to collide
/// is luck, and a guard that only sometimes reproduces the bug is a guard that
/// passes on the broken code -- which this one did, until it was written this
/// way. Here the first update is held open on a channel, so the overlap is a
/// fact of the test rather than a hope.
#[test]
fn a_change_to_the_state_file_waits_for_the_one_in_flight() {
    let root = std::env::temp_dir().join(format!("lemma-tel-lock-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&root);
    set_enabled(&root, true).expect("opted in to begin with");

    let (entered, has_entered) = std::sync::mpsc::channel();
    let (release, may_finish) = std::sync::mpsc::channel::<()>();
    let holding = {
        let root = root.clone();
        std::thread::spawn(move || {
            crate::telemetry::update_state(&root, |state| {
                // What an event does before it writes: it has read `enabled`,
                // and it is about to write it back.
                entered.send(()).expect("the test is listening");
                let _ = may_finish.recv();
                state.install_id = Some("0123456789abcdef0123456789abcdef".into());
            })
        })
    };
    has_entered
        .recv_timeout(std::time::Duration::from_secs(10))
        .expect("the first update started");

    let (opted_out, has_opted_out) = std::sync::mpsc::channel();
    let disabling = {
        let root = root.clone();
        std::thread::spawn(move || {
            let outcome = set_enabled(&root, false);
            let _ = opted_out.send(());
            outcome
        })
    };
    let blocked = has_opted_out
        .recv_timeout(std::time::Duration::from_millis(250))
        .is_err();

    // Released before anything is asserted, so a guard that is going to fail
    // fails rather than hanging on a thread it left parked.
    let _ = release.send(());
    holding.join().expect("the event thread").expect("saved");
    disabling
        .join()
        .expect("the opt-out thread")
        .expect("saved");

    assert!(
        blocked,
        "a second change went ahead while one was mid-flight; the read and the \
         write are not one operation",
    );
    // The file, not `is_enabled`. `is_enabled` answers false whenever there is
    // no ingestion key, and a test environment has none -- the sibling guard
    // `disabled_without_a_key_even_when_opted_in` asserts exactly that -- so
    // asking it here passed whether or not the opt-out had survived. Which is
    // the same shape as the bug this test is about.
    assert_eq!(
        crate::telemetry::load_state(&root).enabled,
        Some(false),
        "and the opt-out has to be what the file ends up saying",
    );
    let _ = std::fs::remove_dir_all(&root);
}

/// Where a development override may point, and where it may not.
///
/// The payload carries the ingestion key and the install id. HTTPS anywhere is
/// fine; plain HTTP is fine only to this machine, because a local collector is
/// the only thing this override exists for. Anything else is cleartext to
/// somebody else's host.
#[test]
fn a_telemetry_destination_is_https_or_this_machine() {
    for allowed in [
        "https://eu.i.posthog.com",
        "https://collector.example",
        "http://127.0.0.1:8000",
        "http://localhost:9000",
        "http://[::1]:9000",
    ] {
        assert!(destination_is_safe(allowed), "{allowed} should be allowed");
    }

    for refused in [
        "http://posthog.example",
        "http://10.0.0.5:8000",
        // A loopback name in the path rather than the authority is somebody
        // else's host wearing it as a disguise.
        "http://evil.example/127.0.0.1",
        "http://evil.example?x=localhost",
        "ftp://localhost",
        "https://",
        "",
        "127.0.0.1:8000",
    ] {
        assert!(!destination_is_safe(refused), "{refused} should be refused");
    }
}

/// Every way out of a started install reports how it ended.
///
/// `RuntimeInstallFailed` used to be recorded only where the installer itself
/// failed. The activation that follows -- stopping the previous daemon, then
/// swapping the runtime in -- returned its error after `RuntimeInstallStarted`
/// with no terminal event, so an install that failed there looked, in the
/// numbers, like one that started and never finished. Install health is the
/// whole point of this telemetry.
#[test]
fn a_failed_activation_still_reports_the_install_as_failed() {
    let source = shell_source();
    let body = function_body(&source, "fn ensure_runtime_artifacts_inner(");
    let started = body
        .find("RuntimeInstallStarted")
        .expect("the install announces itself");
    let activation = body
        .find("activate_installed_runtime")
        .expect("the install activates what it fetched");
    assert!(
        started < activation,
        "activation comes after the start event"
    );
    let reported = body[activation..]
        .find("RuntimeInstallFailed")
        .map(|at| activation + at);
    let completed = body[activation..]
        .find("RuntimeInstallCompleted")
        .map(|at| activation + at);
    let (reported, completed) = (
        reported.expect("activation failure is reported"),
        completed.expect("the install still reports success"),
    );
    assert!(
        reported < completed,
        "the failure path has to be reported before the success one is reached",
    );
}
