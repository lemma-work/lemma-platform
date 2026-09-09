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
    )
    .unwrap();
    assert_eq!(calls, 3);
    for response in [json!({}), json!({"ready": "true"})] {
        let error = poll_sandbox_image_warmup(
            &cancellation,
            Duration::from_secs(5),
            Duration::ZERO,
            || Ok(response.clone()),
        )
        .unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::InvalidData);
    }
}

#[test]
fn image_warmup_stops_on_cancellation_deadline_and_guest_failure() {
    let cancellation = lemma_desktop_process::Cancellation::default();
    let error = poll_sandbox_image_warmup(&cancellation, Duration::ZERO, Duration::ZERO, || {
        panic!("expired work must not dispatch")
    })
    .unwrap_err();
    assert_eq!(error.kind(), io::ErrorKind::TimedOut);
    let error = poll_sandbox_image_warmup(
        &cancellation,
        Duration::from_secs(5),
        Duration::ZERO,
        || Err(io::Error::new(io::ErrorKind::ConnectionReset, "guest lost")),
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
        let error = poll_sandbox_image_warmup(&cancellation, budget, Duration::ZERO, || {
            if cancel {
                cancellation.cancel();
            } else {
                thread::sleep(budget);
            }
            Ok(json!({"ready": true}))
        })
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
