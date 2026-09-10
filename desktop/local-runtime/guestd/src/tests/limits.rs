//! Bounded waits, and a ceiling that is not a reservation.

use super::*;

/// No single request may sit on the guest's only control channel for
/// minutes.
///
/// The host bridge holds one vsock connection behind a process-wide mutex
/// and `serve_vsock` handles each connection inline on its accept loop, so
/// a request in flight is the whole machine's guest traffic. `ensure` used
/// to poll readiness for up to 180 seconds inside the request: a slow
/// sandbox start blocked every other sandbox operation, including
/// read-only ones, and burned callers' deadlines while they waited to be
/// heard rather than to be served.
#[test]
fn no_request_holds_the_control_channel_for_minutes() {
    // The bounds themselves are checked at compile time beside the
    // constant; what is worth asserting here is that nothing reintroduced
    // an unbounded wait elsewhere in `ensure`.
    let source = guest_source();
    let ensure = {
        let start = source
            .find("let deadline = Instant::now() + SANDBOX_READY_POLL_BUDGET;")
            .expect("ensure polls readiness against the shared budget");
        &source[start..start + 900]
    };
    assert!(
        !ensure.contains("Duration::from_secs(180)"),
        "the readiness wait must not go back to holding the channel for minutes",
    );
}

/// A sandbox that is merely slow is retryable; one that died is not.
///
/// Bounding the wait only helps if "not yet" is distinguishable from
/// "never" -- a bounded wait that reported failure would turn every slow
/// start into a hard error.
#[test]
fn a_slow_start_is_reported_as_retryable() {
    let source = guest_source();
    let start = source
        .find("code: \"not_ready\".into(),")
        .expect("ensure hands back a not_ready when the budget expires");
    // To the end of the struct literal rather than a fixed span, so the
    // test does not start failing because the message got a line longer.
    let end = source[start..]
        .find("\n            });")
        .expect("the not_ready error is a struct literal");
    let window = &source[start..start + end];
    assert!(
        window.contains("retryable: true"),
        "a sandbox that is still starting must be retried, not failed",
    );
    assert!(
        window.contains("status_code: 503"),
        "not_ready is a availability answer, not a client error",
    );
}

/// A ceiling is not a claim.
///
/// Admission used to add up every running sandbox's `--memory` and treat
/// the sum as spent. With the shipped numbers -- 6 GiB guest, 1536 MiB core
/// reservation, 2048 MiB default ceiling -- that admitted exactly two
/// sandboxes and refused the third, so one open workspace plus a function
/// was the whole machine. The arithmetic is kept here because it is the
/// thing that must never come back.
#[test]
fn a_sandbox_ceiling_is_not_a_reservation() {
    // The numbers that shipped, kept as literals because the constant they
    // came from is gone: admission no longer reserves for core services at
    // all, it asks the kernel what is free.
    const SHIPPED_GUEST: u64 = 6 * 1024 * 1024 * 1024;
    const SHIPPED_CORE_RESERVATION: u64 = 1536 * 1024 * 1024;

    let old_style =
        SHIPPED_GUEST.saturating_sub(SHIPPED_CORE_RESERVATION) / DEFAULT_SANDBOX_MEMORY_BYTES;
    assert_eq!(
        old_style, 2,
        "summing ceilings capped a 6 GiB guest at two sandboxes, which is \
         what made one open workspace plus a function the whole machine",
    );

    // Admission now asks for a request-sized slice of what is actually
    // free, so the same guest fits an order of magnitude more before
    // memory is the binding constraint.
    let request_style =
        SHIPPED_GUEST.saturating_sub(GUEST_MEMORY_HEADROOM_BYTES) / SANDBOX_MEMORY_REQUEST_BYTES;
    assert!(
        request_style >= 8,
        "a request-based admission must not stop at two: got {request_style}",
    );
}

/// Availability is read, not inferred from the total.
///
/// `MemTotal` is the wrong number: virtio-balloon adjusts it while the host
/// reclaims memory, so a total says nothing about what is spare.
#[test]
fn available_memory_comes_from_the_kernels_own_estimate() {
    let meminfo = "MemTotal:        6109184 kB\nMemFree:          201234 kB\nMemAvailable:    4194304 kB\nBuffers:           1024 kB\n";
    assert_eq!(
        parse_mem_available(meminfo),
        Some(4194304 * 1024),
        "MemAvailable is what decides whether a container can start",
    );
    // A kernel too old to report it is an error, not a zero that would
    // refuse every sandbox for ever.
    assert_eq!(parse_mem_available("MemTotal: 100 kB\n"), None);
}

/// The concurrency ceiling is a backstop, and it can be raised.
#[test]
fn the_sandbox_ceiling_is_configurable() {
    assert_eq!(max_sandboxes(), DEFAULT_MAX_SANDBOXES);
}

/// The guest says how much room is left where it keeps everything.
///
/// Nothing reported this at all, and the disk it describes is a fixed size
/// shared by every image, every unpacked snapshot, every workspace and the
/// database. The first sign it had run out was whatever broke first, which is
/// usually Postgres refusing to write.
#[test]
fn health_reports_what_is_left_of_the_data_disk() {
    let root = tempdir().unwrap();
    let service = GuestService::new(
        FakeEngine::new(vec![output(true, "")]),
        root.path().into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap();

    let health = service.health().expect("a healthy guest answers");
    let disk = &health["data_disk"];
    let free = disk["free_bytes"].as_u64().expect("free bytes reported");
    let total = disk["total_bytes"].as_u64().expect("total bytes reported");
    assert!(total > 0, "a real filesystem has a size: {disk}");
    assert!(free <= total, "free cannot exceed total: {disk}");
}

/// A filesystem that cannot be measured is reported as unknown, not as full.
///
/// A fabricated zero reads as "out of space" and a fabricated large number
/// reads as "fine". Both are worse than saying nothing, and the host already
/// treats an absent field as a guest too old to have one.
#[test]
fn an_unmeasurable_filesystem_reports_nothing_rather_than_a_guess() {
    assert!(crate::capacity::data_disk_space(std::path::Path::new(
        "/definitely/not/a/directory/on/this/machine"
    ))
    .is_none());
}

/// The shutdown reply says what it stopped, split by class, and what it was
/// willing to spend.
///
/// The relationships between the constants are compile-time assertions beside
/// them. What a test adds is that the numbers reach the host at all: a stop
/// that was cut short is otherwise indistinguishable from one that finished.
#[test]
fn the_stop_budget_and_the_split_counts_are_reportable() {
    assert_eq!(
        GUEST_STOP_WORST_CASE_SECONDS,
        SANDBOX_STOP_GRACE_SECONDS * MAX_SANDBOX_CEILING as u32
            + CORE_STOP_GRACE_SECONDS * CORE_CONTAINERS.len() as u32,
    );
    // The override cannot ask for more than the budget covers. Without the
    // clamp, `LEMMA_GUEST_MAX_SANDBOXES=32` needed seventeen seconds more than
    // the host waits, and the guest would be terminated mid-shutdown.
    assert!(max_sandboxes() <= MAX_SANDBOX_CEILING);
    let stopped = StoppedContainers {
        sandboxes: 4,
        core: 3,
    };
    assert_eq!(stopped.total(), 7);
}

/// An id the engine did not print is not stopped, and one it printed in a
/// shape we do not recognise stops the whole thing rather than being guessed
/// at -- these ids go into an engine command line.
#[test]
fn only_real_container_ids_reach_the_stop_command() {
    assert_eq!(
        parse_container_ids("abc123\n\n  def456  \n").unwrap(),
        vec!["abc123".to_owned(), "def456".to_owned()],
    );
    assert_eq!(parse_container_ids("").unwrap(), Vec::<String>::new());
    for hostile in ["--time", "abc; rm -rf /", "abcg", &"a".repeat(129)] {
        assert!(parse_container_ids(hostile).is_err(), "{hostile}");
    }
}
