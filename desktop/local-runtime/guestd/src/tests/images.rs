//! Image warm-up, repair, and the marker that says a cache is ready.

use super::*;

#[test]
fn sandbox_image_marker_probe_is_offline_and_checks_the_runtime_entrypoint() {
    let root = tempdir().unwrap();
    let service = GuestService::new(
        FakeEngine::new(vec![output(true, "")]),
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();

    assert!(service.sandbox_image_marker_is_ready(
        "ghcr.io/lemma/workspace@sha256:abc",
        WorkloadKind::Workspace,
    ));
    assert_eq!(
        service.engine.commands.lock().unwrap()[0],
        vec![
            "run",
            "--rm",
            "--network",
            "none",
            "--platform",
            guest_platform(),
            "ghcr.io/lemma/workspace@sha256:abc",
            "/usr/bin/test",
            "-s",
            "/usr/local/bin/start-workspace-runtime",
        ]
    );
}

#[test]
fn image_repair_pull_explicitly_unpacks_the_selected_platform() {
    let root = tempdir().unwrap();
    let service = GuestService::new(
        FakeEngine::new(vec![output(true, "")]),
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();

    service
        .pull_image("ghcr.io/lemma/runtime@sha256:abc")
        .unwrap();
    assert_eq!(
        service.engine.commands.lock().unwrap()[0],
        vec![
            "pull",
            "--quiet",
            "--unpack=true",
            "--platform",
            guest_platform(),
            "ghcr.io/lemma/runtime@sha256:abc",
        ]
    );
}

#[test]
fn incomplete_sandbox_image_reference_is_replaced_before_repull() {
    let root = tempdir().unwrap();
    let service = GuestService::new(
        FakeEngine::new(vec![
            output(true, "{}"),
            output(false, ""),
            output(true, ""),
            output(true, ""),
            output(true, ""),
            output(true, ""),
        ]),
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();

    service
        .ensure_sandbox_image(
            "ghcr.io/lemma/runtime@sha256:abc",
            WorkloadKind::Workspace,
            true,
        )
        .unwrap();
    let commands = service.engine.commands.lock().unwrap();
    assert_eq!(commands[2], vec!["container", "prune", "--force"]);
    assert_eq!(
        commands[3],
        vec!["rmi", "--force", "ghcr.io/lemma/runtime@sha256:abc"]
    );
    assert_eq!(commands[4][0], "pull");
}

#[test]
fn unrecoverable_image_cache_persists_a_health_gated_reset_marker() {
    let root = tempdir().unwrap();
    let service = GuestService::new(
        FakeEngine::new(vec![
            output(true, "{}"),
            output(false, ""),
            output(true, ""),
            output(true, ""),
            output(true, ""),
            output(false, ""),
            output(true, ""),
        ]),
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();

    let error = service
        .ensure_sandbox_image(
            "ghcr.io/lemma/runtime@sha256:abc",
            WorkloadKind::Workspace,
            true,
        )
        .unwrap_err();

    assert_eq!(error.code, "guest_cache_repair_required");
    let marker = service.cache_reset_marker();
    assert!(marker.is_file());
    assert_eq!(service.health().unwrap()["status"], "ready");
    let repair_due = marker.metadata().unwrap().modified().unwrap()
        + CACHE_REPAIR_RESPONSE_GRACE
        + Duration::from_secs(1);
    assert_eq!(
        service.health_at(repair_due).unwrap_err().code,
        "guest_cache_repair_required"
    );
}

/// A download already running is joined, not started again.
///
/// The table that used to answer this lived in process memory, which is the
/// wrong place on Windows: `wsl.exe --exec lemma-guestd request` gives every
/// request its own process, so the table was empty each time and each
/// `sandbox.ensure` began another transfer of the gigabyte the previous one
/// was still fetching.
///
/// The claim is a `flock`, which excludes across open descriptions whether or
/// not they belong to the same process -- so holding one here is the same
/// obstacle a second guestd meets.
#[test]
fn a_download_already_under_way_is_joined_rather_than_started_again() {
    let root = tempdir().unwrap();
    let service = GuestService::new(
        FakeEngine::new(vec![output(true, "")]),
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();
    let image = "ghcr.io/lemma/workspace@sha256:abc";

    let held = claim_pull(&service.pull_claims(), image)
        .unwrap()
        .expect("the first claim is free");

    let error = service.pull_image(image).expect_err("somebody else has it");
    assert_eq!(error.code, "image_pulling");
    assert!(error.retryable);
    assert!(
        service.engine.commands.lock().unwrap().is_empty(),
        "a second download must not be started beside the first"
    );

    drop(held);
    // Immediately, with nothing in between. Releasing a `flock` is closing a
    // descriptor, and the kernel does not make that visible to the next attempt
    // instantly -- measured at 489 microseconds, under the contention of a
    // parallel test run. `claim_pull` allows for it; without that this line
    // fails roughly once in eight runs at `--test-threads=8`, and a guest that
    // had just finished a download would tell the next caller it was still
    // going.
    service.pull_image(image).expect("the claim was released");
    assert_eq!(service.engine.commands.lock().unwrap()[0][0], "pull");
}

/// Two images are two claims, not one queue.
#[test]
fn one_download_does_not_hold_up_a_different_image() {
    let root = tempdir().unwrap();
    let claims = root.path().join("pulls");
    let _held = claim_pull(&claims, "ghcr.io/lemma/workspace@sha256:abc")
        .unwrap()
        .expect("the first claim is free");
    assert!(
        claim_pull(&claims, "ghcr.io/lemma/function@sha256:abc")
            .unwrap()
            .is_some(),
        "a different image is a different download"
    );
    assert_ne!(
        claim_name("ghcr.io/lemma/workspace@sha256:abc"),
        claim_name("ghcr.io/lemma/function@sha256:abc"),
    );
}

/// A guest whose process ends with its reply downloads before it replies.
///
/// The contrast is the whole test. Given the same missing image and the same
/// engine, a resident guest answers "still downloading" and keeps fetching on
/// a thread -- correct, because that thread outlives the request. A
/// per-request guest that answered the same way would be answering about a
/// download it was about to kill.
#[test]
fn a_per_request_guest_finishes_the_download_before_it_answers() {
    let image = "ghcr.io/lemma/workspace@sha256:abc";
    let (release, downloads) = std::sync::mpsc::channel();
    let (started, _observed) = std::sync::mpsc::channel();
    let resident = GuestService::new(
        GatedPullEngine::new(downloads, started),
        tempdir().unwrap().path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();
    release.send(true).unwrap();
    let deferred = resident
        .ensure_sandbox_image_for_start(image, WorkloadKind::Workspace)
        .expect_err("a resident guest defers the download to a worker");
    assert_eq!(deferred.code, "image_pulling");

    let root = tempdir().unwrap();
    let (release, downloads) = std::sync::mpsc::channel();
    let (started, _observed) = std::sync::mpsc::channel();
    let mut per_request = GuestService::new(
        GatedPullEngine::new(downloads, started),
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();
    per_request.set_per_request_process();
    release.send(true).unwrap();
    per_request
        .ensure_sandbox_image_for_start(image, WorkloadKind::Workspace)
        .expect("the image is here by the time the caller is answered");
}

/// A download that fails is reported to whoever asked for it.
///
/// It used to be written into a table in a process that was already exiting,
/// so nothing ever read it: a workspace whose image could not be fetched at
/// all was told "still downloading", and told it again on every retry, for
/// ever.
#[test]
fn a_failed_download_reaches_the_caller_that_asked_for_it() {
    let root = tempdir().unwrap();
    let (release, downloads) = std::sync::mpsc::channel();
    let (started, _observed) = std::sync::mpsc::channel();
    let mut service = GuestService::new(
        GatedPullEngine::new(downloads, started),
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();
    service.set_per_request_process();
    release.send(false).unwrap();

    let error = service
        .ensure_sandbox_image_for_start(
            "ghcr.io/lemma/workspace@sha256:abc",
            WorkloadKind::Workspace,
        )
        .expect_err("the registry refused");
    assert_ne!(
        error.code, "image_pulling",
        "a download that failed is not a download still running"
    );
    assert!(
        error.message.contains("registry unavailable"),
        "the caller is told what actually went wrong: {}",
        error.message
    );
}
