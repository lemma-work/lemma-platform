//! A host suspend, and the drift that is not one.

use super::*;

/// The case that shipped broken: the Mac slept for eleven hours, so wall
/// time ran eleven hours while the monotonic clock ran a tick. The guest
/// was not running for any of it and is now exactly that far behind.
#[test]
fn a_wall_clock_jump_past_the_monotonic_clock_is_read_as_host_sleep() {
    assert_eq!(
        clock_sync_due(
            Duration::from_secs(1),
            Duration::from_secs(1),
            Duration::from_secs(41_250),
        ),
        Some(ClockSyncReason::HostSlept),
    );
}

#[test]
fn an_ordinary_tick_inside_the_interval_does_not_sync() {
    assert_eq!(
        clock_sync_due(
            Duration::from_secs(1),
            Duration::from_secs(1),
            Duration::from_secs(1),
        ),
        None,
    );
}

/// Drift that is not a sleep still accumulates, so the interval is a
/// ceiling on how far the guest may be off before it is put back.
#[test]
fn the_interval_bounds_drift_that_was_not_a_sleep() {
    assert_eq!(
        clock_sync_due(
            CLOCK_SYNC_INTERVAL,
            Duration::from_secs(1),
            Duration::from_secs(1),
        ),
        Some(ClockSyncReason::Interval),
    );
}
