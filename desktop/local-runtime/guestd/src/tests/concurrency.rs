//! One slow request must not silence the channel.

use super::*;

#[test]
fn cold_image_downloads_leave_the_control_stream_and_health_responsive() {
    let root = tempdir().unwrap();
    let (release, receiver) = std::sync::mpsc::channel();
    let (started, downloads) = std::sync::mpsc::channel();
    let service = GuestService::new(
        GatedPullEngine {
            release: Mutex::new(receiver),
            started,
            present: Mutex::new(std::collections::HashSet::new()),
            invalid: Mutex::new(std::collections::HashSet::new()),
        },
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();
    let workspace = format!("ghcr.io/lemma/workspace@sha256:{}", root.path().display());
    let function = format!("ghcr.io/lemma/function@sha256:{}", root.path().display());
    let parameters = json!({
        "images": {"postgres": "pg@sha256:test", "redis": "redis@sha256:test",
            "supertokens": "auth@sha256:test", "workspace": workspace, "function": function},
        "credentials": {"postgres_password": "a".repeat(64), "redis_password": "b".repeat(64)},
    });
    let poll =
        json!({"version": 1, "operation": "core.sandbox_images_status", "parameters": parameters});
    let stream = format!(
        "{poll}\n{}\n{poll}\n",
        json!({"version": 1, "operation": "health", "parameters": {}})
    );
    let mut replies = Vec::new();
    handle_stream(stream.as_bytes(), &mut replies, &service).unwrap();
    let replies: Vec<Value> = String::from_utf8(replies)
        .unwrap()
        .lines()
        .map(|line| serde_json::from_str(line).unwrap())
        .collect();
    assert_eq!(replies[0]["result"]["ready"], false);
    assert_eq!(replies[1]["result"]["status"], "ready");
    assert_eq!(replies[2]["result"]["ready"], false);
    let mut pulled = vec![
        downloads.recv_timeout(Duration::from_secs(10)).unwrap(),
        downloads.recv_timeout(Duration::from_secs(10)).unwrap(),
    ];
    pulled.sort();
    assert_eq!(pulled, vec![function.clone(), workspace.clone()]);
    assert!(
        downloads.try_recv().is_err(),
        "polling must not duplicate downloads"
    );
    release.send(true).unwrap();
    release.send(true).unwrap();
    let deadline = Instant::now() + Duration::from_secs(10);
    while service
        .image_warmups
        .lock()
        .unwrap()
        .values()
        .any(|state| matches!(state, ImageWarmupState::Running))
    {
        assert!(Instant::now() < deadline, "download workers did not finish");
        thread::sleep(Duration::from_millis(1));
    }
    let mut replies = Vec::new();
    handle_reader(poll.to_string().as_bytes(), &mut replies, &service).unwrap();
    let response: Value = serde_json::from_slice(&replies).unwrap();
    assert_eq!(response["result"]["ready"], true);
}

/// Health must answer while a mutation is still running.
///
/// Context: the host probes health every five seconds and gives it five,
/// but the guest served one request at a time -- and a `sandbox.ensure`
/// waiting on a callback holds it for up to five minutes. Every probe
/// behind one of those timed out, the host read that as "the runtime is
/// gone", and tore down the Postgres and Redis forwarders under a backend
/// that was using them.
///
/// What this test covers, precisely: that the mutation lock added with the
/// fix does not itself become the queue that was just removed. The other
/// half -- serving each connection on its own thread -- lives in
/// `serve_vsock` behind a Linux `cfg` and needs a booted guest, so it is
/// qualified by `check_guest_lifecycle.py` rather than here. Do not read a
/// pass here as proof that the host's probe is safe end to end.
#[test]
fn health_answers_while_a_mutation_is_still_running() {
    let root = tempdir().unwrap();
    let (release, receiver) = std::sync::mpsc::channel();
    let (started, downloads) = std::sync::mpsc::channel();
    let service = GuestService::new(
        GatedPullEngine {
            release: Mutex::new(receiver),
            started,
            present: Mutex::new(std::collections::HashSet::new()),
            invalid: Mutex::new(std::collections::HashSet::new()),
        },
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();

    let parameters = json!({
        "images": {"postgres": "pg@sha256:test", "redis": "redis@sha256:test",
            "supertokens": "auth@sha256:test",
            "workspace": "ghcr.io/lemma/workspace@sha256:blocked",
            "function": "ghcr.io/lemma/function@sha256:blocked"},
        "credentials": {"postgres_password": "a".repeat(64), "redis_password": "b".repeat(64)},
    });

    // A mutation that will not return until the test releases it.
    let blocking = service.clone();
    let blocked = thread::spawn(move || {
        blocking.handle(GuestRequest {
            version: 1,
            operation: "core.images".into(),
            parameters: parameters.clone(),
            capability: None,
        })
    });
    // Wait until it is genuinely inside the engine, holding the lock.
    downloads.recv_timeout(Duration::from_secs(10)).unwrap();

    let answered = service.handle(GuestRequest {
        version: 1,
        operation: "health".into(),
        parameters: json!({}),
        capability: None,
    });
    assert!(
        answered.ok,
        "health must not queue behind a mutation: {:?}",
        answered.error
    );

    release.send(true).unwrap();
    release.send(true).unwrap();
    let _ = blocked.join().unwrap();
}

/// The other half: two mutations still may not interleave. Concurrency was
/// added for observation, not for `core.postgres` racing itself.
#[test]
fn mutations_are_still_served_one_at_a_time() {
    assert!(is_observation("health"));
    assert!(is_observation("core.status"));
    assert!(is_observation("core.sandbox_images_status"));
    assert!(is_observation("sandbox.status"));
    assert!(is_observation("sandbox.list"));
    assert!(is_observation("diagnostics.network"));
    assert!(is_observation("diagnostics.sandbox"));

    for mutation in [
        "core.ensure",
        "core.images",
        "core.sandbox_images",
        "core.postgres",
        "core.redis",
        "core.supertokens",
        "core.stop",
        "core.reset_data",
        "sandbox.ensure",
        "sandbox.release",
        "sandbox.delete",
        "sandbox.purge",
        "sandbox.purge_storage",
        "system.shutdown",
        "system.clock",
        // Unknown operations are mutations by default, so a new one is
        // safe until someone deliberately says it only reads.
        "core.something_added_later",
    ] {
        assert!(
            !is_observation(mutation),
            "{mutation} must stay serialised against other mutations"
        );
    }
}

#[test]
fn image_repair_and_failed_download_retries_leave_health_responsive() {
    for initially_corrupt in [false, true] {
        let root = tempdir().unwrap();
        let image = "ghcr.io/lemma/workspace@sha256:test".to_owned();
        let (release, receiver) = std::sync::mpsc::channel();
        let (started, downloads) = std::sync::mpsc::channel();
        let present = if initially_corrupt {
            std::collections::HashSet::from([image.clone()])
        } else {
            std::collections::HashSet::new()
        };
        let service = GuestService::new(
            GatedPullEngine {
                release: Mutex::new(receiver),
                started,
                present: Mutex::new(present.clone()),
                invalid: Mutex::new(present),
            },
            root.path().into(),
            Some("192.168.64.2".into()),
            "192.168.64.1".into(),
            None,
        )
        .unwrap();
        let parameters = service.parse_core_parameters(json!({
            "images": {"postgres": "pg@sha256:test", "redis": "redis@sha256:test",
                "supertokens": "auth@sha256:test", "workspace": image},
            "credentials": {"postgres_password": "a".repeat(64), "redis_password": "b".repeat(64)},
        })).unwrap();
        assert!(!service.poll_sandbox_images(&parameters).unwrap());
        assert_eq!(
            downloads.recv_timeout(Duration::from_secs(10)).unwrap(),
            image
        );
        assert!(!service.poll_sandbox_images(&parameters).unwrap());
        assert_eq!(service.health().unwrap()["status"], "ready");
        assert!(downloads.try_recv().is_err());
        release.send(false).unwrap();
        wait_for_image_warmup(&service);
        let error = service.poll_sandbox_images(&parameters).unwrap_err();
        assert!(error.message.contains("registry unavailable"));
        assert_eq!(service.health().unwrap()["status"], "ready");
        assert!(!service.poll_sandbox_images(&parameters).unwrap());
        assert_eq!(
            downloads.recv_timeout(Duration::from_secs(10)).unwrap(),
            image
        );
        release.send(true).unwrap();
        wait_for_image_warmup(&service);
        assert!(service.poll_sandbox_images(&parameters).unwrap());
        assert!(service.poll_sandbox_images(&parameters).unwrap());
        assert!(downloads.try_recv().is_err());
    }
}
