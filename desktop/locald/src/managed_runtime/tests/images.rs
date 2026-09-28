//! Warming the sandbox image once, and stopping when asked.

use super::*;

/// Both the ready path and the recovery path warm the images. Two runs
/// would interleave their downloading/ready events into one stream the app
/// reads as a single download finishing twice.
#[test]
fn only_one_sandbox_image_warmup_is_claimed_at_a_time() {
    let (_root, controller) = test_controller();

    let first = controller.claim_sandbox_image_warmup();
    let second = controller.claim_sandbox_image_warmup();

    assert!(first.is_some());
    assert!(second.is_none(), "a second warm-up ran alongside the first");
    assert_eq!(
        controller.sandbox_image_status().state,
        SANDBOX_IMAGES_DOWNLOADING
    );
}

/// Once one has ended, the next start is free to warm again -- a recovered
/// stack may be looking at a different guest.
#[test]
fn a_finished_warmup_does_not_block_the_next_one() {
    let (_root, controller) = test_controller();

    controller.claim_sandbox_image_warmup();
    controller.publish_sandbox_images(
        SandboxImageStatus::new(SANDBOX_IMAGES_READY, "ready"),
        &|_: &SandboxImageStatus| {},
    );

    assert!(controller.claim_sandbox_image_warmup().is_some());
}

/// Starting says "nobody asked for this" rather than fetching it.
///
/// Fetching on every start spent several hundred megabytes on a capability a
/// person may never use: the coding agents run natively, so someone using only
/// those has no pod workload to sandbox. They got the download and a toast for
/// it anyway.
#[test]
fn a_start_that_asks_for_nothing_records_that_nothing_was_asked_for() {
    let (_root, controller) = test_controller();

    let status = controller.note_sandbox_images_not_prepared();

    assert_eq!(status.state, SANDBOX_IMAGES_NOT_PREPARED);
    assert_eq!(
        controller.sandbox_image_status().state,
        SANDBOX_IMAGES_NOT_PREPARED,
        "Settings reads the stored status, so it has to match what was said"
    );
}

/// Recovery re-announces after a restart, and a download may still be running.
///
/// Overwriting it with "nobody asked" would tell the workspace to stop watching
/// a fetch that was still going, and would offer a second download of the image
/// already being fetched.
#[test]
fn announcing_never_overwrites_a_fetch_that_is_already_underway() {
    let (_root, controller) = test_controller();

    controller.claim_sandbox_image_warmup();
    assert_eq!(
        controller.note_sandbox_images_not_prepared().state,
        SANDBOX_IMAGES_DOWNLOADING
    );

    controller.publish_sandbox_images(
        SandboxImageStatus::new(SANDBOX_IMAGES_READY, "ready"),
        &|_: &SandboxImageStatus| {},
    );
    assert_eq!(
        controller.note_sandbox_images_not_prepared().state,
        SANDBOX_IMAGES_READY,
        "an image already here is a better answer than nobody asking for one"
    );
}

#[test]
fn shutdown_prevents_late_image_warmup_from_starting() {
    let (_root, controller) = test_controller();
    let controller = Arc::new(controller);
    controller.cancel_pending_requests();
    controller.warm_sandbox_images(|_| panic!("shutdown must not admit a download"));
    assert!(controller.pending_images.lock().unwrap().is_none());
    assert_eq!(
        controller.sandbox_image_status().state,
        SANDBOX_IMAGES_PENDING
    );
}

#[test]
fn image_warmup_polls_until_ready_and_rejects_invalid_responses() {
    let cancellation = lemma_desktop_process::Cancellation::default();
    let mut calls = 0;
    poll_sandbox_image_warmup(
        &cancellation,
        Duration::from_secs(5),
        Duration::ZERO,
        || {
            calls += 1;
            Ok(json!({"ready": calls == 3}))
        },
        |_, _| {},
    )
    .unwrap();
    assert_eq!(calls, 3);
    for response in [json!({}), json!({"ready": "true"})] {
        let error = poll_sandbox_image_warmup(
            &cancellation,
            Duration::from_secs(5),
            Duration::ZERO,
            || Ok(response.clone()),
            |_, _| {},
        )
        .unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::InvalidData);
    }
}

#[test]
fn image_warmup_stops_on_cancellation_deadline_and_guest_failure() {
    let cancellation = lemma_desktop_process::Cancellation::default();
    let error = poll_sandbox_image_warmup(
        &cancellation,
        Duration::ZERO,
        Duration::ZERO,
        || panic!("expired work must not dispatch"),
        |_, _| {},
    )
    .unwrap_err();
    assert_eq!(error.kind(), io::ErrorKind::TimedOut);
    let error = poll_sandbox_image_warmup(
        &cancellation,
        Duration::from_secs(5),
        Duration::ZERO,
        || Err(io::Error::new(io::ErrorKind::ConnectionReset, "guest lost")),
        |_, _| {},
    )
    .unwrap_err();
    assert_eq!(error.kind(), io::ErrorKind::ConnectionReset);
    let mut calls = 0;
    let error = poll_sandbox_image_warmup(
        &cancellation,
        Duration::from_secs(5),
        Duration::ZERO,
        || {
            calls += 1;
            cancellation.cancel();
            Ok(json!({"ready": false}))
        },
        |_, _| {},
    )
    .unwrap_err();
    assert_eq!(error.kind(), io::ErrorKind::Interrupted);
    assert_eq!(calls, 1);
}

#[test]
fn image_warmup_rejects_success_after_cancellation_or_deadline() {
    for cancel in [false, true] {
        let cancellation = lemma_desktop_process::Cancellation::default();
        let budget = if cancel {
            Duration::from_secs(5)
        } else {
            Duration::from_millis(1)
        };
        let error = poll_sandbox_image_warmup(
            &cancellation,
            budget,
            Duration::ZERO,
            || {
                if cancel {
                    cancellation.cancel();
                } else {
                    thread::sleep(budget);
                }
                Ok(json!({"ready": true}))
            },
            |_, _| {},
        )
        .unwrap_err();
        assert_eq!(
            error.kind(),
            if cancel {
                io::ErrorKind::Interrupted
            } else {
                io::ErrorKind::TimedOut
            }
        );
    }
}

/// The guest's MB figures reach the app while it downloads, clamped, and a
/// guest that sends none is waited on as before.
#[test]
fn image_warmup_passes_on_how_far_the_download_has_got() {
    let cancellation = lemma_desktop_process::Cancellation::default();
    let answers = [
        json!({"ready": false}),
        json!({"ready": false, "done_mb": 120, "total_mb": 700}),
        json!({"ready": false, "done_mb": 900, "total_mb": 700}),
        json!({"ready": false, "done_mb": 5, "total_mb": 0}),
        json!({"ready": true}),
    ];
    let mut calls = 0;
    let mut heard = Vec::new();
    poll_sandbox_image_warmup(
        &cancellation,
        Duration::from_secs(5),
        Duration::ZERO,
        || {
            calls += 1;
            Ok(answers[calls - 1].clone())
        },
        |done, total| heard.push((done, total)),
    )
    .unwrap();

    assert_eq!(heard, [(120, 700), (700, 700)]);
}

#[test]
fn a_downloading_status_carries_its_progress_to_the_app() {
    let (_root, controller) = test_controller();
    let heard = Mutex::new(Vec::new());

    controller.publish_sandbox_images(
        SandboxImageStatus::downloading(120, 700),
        &|status: &SandboxImageStatus| heard.lock().unwrap().push(status.clone()),
    );

    let status = controller.sandbox_image_status();
    assert_eq!(status.state, SANDBOX_IMAGES_DOWNLOADING);
    assert_eq!((status.done_mb, status.total_mb), (Some(120), Some(700)));
    assert!(
        status.detail.contains("120 MB of 700 MB"),
        "{}",
        status.detail
    );
    let serialized = serde_json::to_value(&status).unwrap();
    assert_eq!(serialized["done_mb"], 120);
    assert_eq!(heard.lock().unwrap().len(), 1);
}

fn record(
    controller: &ManagedRuntimeController,
    fetched: Option<(&str, &str)>,
) -> PreparedSandboxImages {
    let record = PreparedSandboxImages {
        fetched: fetched.map(|(workspace, function)| PinnedSandboxImages {
            workspace: Some(workspace.into()),
            function: Some(function.into()),
        }),
        fetched_unasked: None,
    };
    fs::write(
        &controller.prepared_images,
        serde_json::to_vec(&record).unwrap(),
    )
    .unwrap();
    record
}

/// Someone who has never fetched a sandbox image is never sent one unasked.
///
/// A person using only the coding agents has no pod workload to sandbox;
/// fetching on their start is the several hundred megabytes that
/// `announce_sandbox_images` exists to not spend.
#[test]
fn a_computer_that_never_fetched_the_images_is_not_sent_them() {
    let (_root, controller) = test_controller();

    assert!(!controller.claim_unasked_sandbox_image_fetch());
    fs::write(&controller.prepared_images, b"not json").unwrap();
    assert!(!controller.claim_unasked_sandbox_image_fetch());
}

#[test]
fn the_images_already_fetched_are_not_fetched_again() {
    let (_root, controller) = test_controller();
    record(
        &controller,
        Some(("workspace@sha256:test", "function@sha256:test")),
    );

    assert!(!controller.claim_unasked_sandbox_image_fetch());
    assert_eq!(
        controller.note_sandbox_images_not_prepared().state,
        SANDBOX_IMAGES_READY,
        "Settings offered to download the images this release already fetched"
    );
}

/// An update's images are fetched on its first start, once.
///
/// A failure is not retried on every start after it: Settings offers it, and
/// the first task that needs the image fetches it anyway.
#[test]
fn an_update_fetches_its_images_once_for_someone_who_uses_them() {
    let (_root, controller) = test_controller();
    record(
        &controller,
        Some(("workspace@sha256:old", "function@sha256:old")),
    );

    assert!(controller.claim_unasked_sandbox_image_fetch());
    assert!(
        !controller.claim_unasked_sandbox_image_fetch(),
        "a second start of the same release fetched again"
    );
    let saved = controller.prepared_sandbox_images();
    assert_eq!(
        saved.fetched_unasked,
        Some(PinnedSandboxImages {
            workspace: Some("workspace@sha256:test".into()),
            function: Some("function@sha256:test".into()),
        })
    );
    assert_eq!(
        saved.fetched.unwrap().workspace.as_deref(),
        Some("workspace@sha256:old"),
        "claiming the fetch is not the same as having finished it"
    );
}
