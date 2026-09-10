//! A starved control channel is not a dead runtime.

use super::*;

/// The bug this fixes: the guest control channel carries one request at a
/// time, `health` gives it five seconds, and a sandbox image pull or a
/// callback wait legitimately holds it for longer. That timeout used to
/// count as "the runtime is gone", which dropped the Postgres and Redis
/// forwarders the running backend was mid-query on, and then restarted the
/// whole stack underneath it.
#[test]
fn a_starved_control_channel_does_not_look_like_a_dead_runtime() {
    let timeout = io::Error::new(io::ErrorKind::TimedOut, "guest did not answer in 5s");

    for attempt in 1..PROBE_FAILURE_TOLERANCE {
        assert_eq!(
            classify_probe_failure(&timeout, attempt),
            ProbeVerdict::Transient,
            "probe {attempt} of {PROBE_FAILURE_TOLERANCE} must not declare the runtime lost"
        );
    }
}

/// Tolerance, not blindness: a channel that never comes back is a real
/// failure and still has to reach recovery.
#[test]
fn a_control_channel_that_never_answers_is_eventually_lost() {
    let timeout = io::Error::new(io::ErrorKind::TimedOut, "guest did not answer in 5s");

    assert_eq!(
        classify_probe_failure(&timeout, PROBE_FAILURE_TOLERANCE),
        ProbeVerdict::Lost
    );
}

/// A helper process that exited, a faulted kernel and a refused connection
/// all say something a timeout does not, so none of them waits out the
/// tolerance.
#[test]
fn a_runtime_that_really_died_is_reported_at_once() {
    for error in [
        io::Error::other("Lemma's private runtime exited (exit status: 1): kernel panic"),
        io::Error::new(io::ErrorKind::ConnectionRefused, "guest refused"),
        io::Error::new(io::ErrorKind::InvalidData, "invalid guest health response"),
        io::Error::new(io::ErrorKind::BrokenPipe, "control channel closed"),
    ] {
        assert_eq!(
            classify_probe_failure(&error, 1),
            ProbeVerdict::Lost,
            "{error} must be acted on immediately"
        );
    }
}

/// The count is consecutive, so a guest that answers between two slow
/// stretches spends its whole tolerance again rather than accumulating
/// across minutes of healthy service toward a teardown it never earned.
#[test]
fn a_healthy_probe_forgives_the_failures_before_it() {
    let timeout = || io::Error::new(io::ErrorKind::TimedOut, "guest is busy");
    let mut probes = ProbeTracker::default();

    for _ in 1..PROBE_FAILURE_TOLERANCE {
        assert_eq!(probes.failed(&timeout()), ProbeVerdict::Transient);
    }
    probes.succeeded();

    // Back to a full budget rather than one probe from recovery.
    for _ in 1..PROBE_FAILURE_TOLERANCE {
        assert_eq!(probes.failed(&timeout()), ProbeVerdict::Transient);
    }
    assert_eq!(probes.failed(&timeout()), ProbeVerdict::Lost);
}

/// A guest with no DHCP lease is healthy, and must parse as such.
///
/// The guest stopped refusing to start without a vmnet lease, because a
/// denied macOS Local Network permission is exactly how it loses one -- and
/// core services reach the host over the private socket bridges, which need
/// no address at all. That fix only holds if the host can read the health
/// response: with `endpoint_host` typed as a required string, a null failed
/// to deserialise, became "invalid guest health response", and the probe
/// read that as a dead runtime.
#[test]
fn a_guest_reporting_no_network_address_is_not_a_broken_health_response() {
    let status: ManagedRuntimeStatus = serde_json::from_value(serde_json::json!({
        "status": "ready",
        "engine": "containerd",
        "endpoint_host": null,
        "network": {"leased": false},
        "host_gateway": "192.168.64.1",
        "active_sandboxes": 0,
    }))
    .expect("a lease-less guest must still report a readable health status");

    assert_eq!(status.endpoint_host, None);
    assert_eq!(status.host_gateway, "192.168.64.1");
}

/// A fresh controller starts owing nothing to a previous guest.
#[test]
fn a_new_controller_starts_with_a_full_probe_budget() {
    let (_root, controller) = test_controller();
    assert_eq!(
        controller.probes.lock().unwrap().consecutive_failures,
        0,
        "a controller must not inherit failures"
    );
}
