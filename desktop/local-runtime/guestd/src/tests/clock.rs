//! The guest clock, and the health that reports it.

use super::*;

/// The gap that put an install into a permanent 401 loop: the guest was
/// eleven hours behind the Mac, so every access token the auth service
/// minted in it was already expired when the backend read it.
#[test]
fn a_guest_clock_behind_the_host_is_stepped_forward() {
    let (_root, service) = clock_test_service();
    let applied = Cell::new(None);

    let report = service
        .set_clock_with(
            json!({"epoch": 1_787_290_731_u64}),
            UNIX_EPOCH + Duration::from_secs(1_787_249_481),
            |epoch| {
                applied.set(Some(epoch));
                Ok(())
            },
        )
        .unwrap();

    assert_eq!(applied.get(), Some(1_787_290_731));
    assert_eq!(report["skew_seconds"], 41_250);
    assert_eq!(report["stepped"], true);
    assert_eq!(report["guest_epoch"], 1_787_249_481_u64);
}

/// A step moves wall time under every process in the guest. Ordinary
/// jitter is not worth that, so it is reported and left alone.
#[test]
fn a_clock_within_the_threshold_is_reported_and_left_alone() {
    let (_root, service) = clock_test_service();
    let applied = Cell::new(false);

    let report = service
        .set_clock_with(
            json!({"epoch": 1_787_290_732_u64}),
            UNIX_EPOCH + Duration::from_secs(1_787_290_731),
            |_| {
                applied.set(true);
                Ok(())
            },
        )
        .unwrap();

    assert!(!applied.get());
    assert_eq!(report["skew_seconds"], 1);
    assert_eq!(report["stepped"], false);
}

/// The control channel is trusted, but a clock is the one thing a wrong
/// value breaks silently and everywhere, so the guest still checks the
/// range -- the same range `lemma-set-host-time` checks.
#[test]
fn an_implausible_host_epoch_is_refused_without_touching_the_clock() {
    let (_root, service) = clock_test_service();
    let applied = Cell::new(false);

    for epoch in [json!(1_u64), json!(9_999_999_999_u64)] {
        let error = service
            .set_clock_with(json!({"epoch": epoch}), SystemTime::now(), |_| {
                applied.set(true);
                Ok(())
            })
            .unwrap_err();
        assert_eq!(error.code, "invalid_request");
    }

    let missing = service
        .set_clock_with(json!({}), SystemTime::now(), |_| {
            applied.set(true);
            Ok(())
        })
        .unwrap_err();
    assert_eq!(missing.code, "invalid_request");
    assert!(!applied.get());
}

/// Whoever is already asking whether the guest is well should not have to
/// think to ask about time separately.
#[test]
fn health_reports_the_guest_clock() {
    let (_root, service) = clock_test_service();

    let health = service
        .health_at(UNIX_EPOCH + Duration::from_secs(1_787_249_481))
        .unwrap();

    assert_eq!(health["clock_epoch"], 1_787_249_481_u64);
}

#[test]
fn kernel_faults_block_health_and_new_work_before_engine_dispatch() {
    let (root, mut service) = clock_test_service();
    let taint = root.path().join("tainted");
    service.kernel_taint_path = Some(taint.clone());
    for bits in [16, 32, 128, 128 | 512] {
        fs::write(&taint, format!("{bits}\n")).unwrap();
        for operation in ["health", "core.ensure", "sandbox.ensure"] {
            let response = service.handle(GuestRequest {
                version: PROTOCOL_VERSION,
                capability: None,
                operation: operation.into(),
                parameters: json!({}),
            });
            assert!(!response.ok);
            let error = response.error.unwrap();
            assert_eq!(error.code, "guest_kernel_failed");
            assert!(!error.retryable);
            assert_eq!(error.status_code, 503);
            assert!(!error.message.contains(DATA_RESET_MARKER));
        }
    }
    // Clearing the fault on a fresh boot restores normal engine health.
    fs::write(&taint, "0\n").unwrap();
    assert_eq!(service.health().unwrap()["status"], "ready");
}

#[test]
fn kernel_warnings_are_not_crashes_and_unreadable_health_is_not_ready() {
    let (root, mut service) = clock_test_service();
    let taint = root.path().join("tainted");
    service.kernel_taint_path = Some(taint.clone());
    assert!(service.health().is_err());
    fs::write(&taint, "invalid\n").unwrap();
    assert!(service.health().is_err());
    fs::write(&taint, "512\n").unwrap();
    assert_eq!(service.health().unwrap()["status"], "ready");
}
