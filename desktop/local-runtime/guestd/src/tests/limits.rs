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
