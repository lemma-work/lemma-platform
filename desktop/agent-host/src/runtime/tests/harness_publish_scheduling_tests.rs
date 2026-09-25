use super::{
    DISK_SCAN_INTERVAL, FIRST_HARNESS_WAIT, HARNESS_REFRESH_INTERVAL, HARNESS_RETRY_INTERVAL,
    TransientBackoff,
};
use std::time::Duration;

#[test]
fn noticing_a_new_agent_is_not_gated_on_the_refresh_interval() {
    // The refresh interval used to be the only thing that noticed a newly
    // installed agent, which put a quarter of an hour between installing
    // Claude Code and being able to use it. The supervisor's sweep answers
    // that question now, cheaply enough to ask every couple of seconds, and
    // the interval is a safety net behind it.
    assert!(
        DISK_SCAN_INTERVAL * 30 <= HARNESS_REFRESH_INTERVAL,
        "detection must be orders of magnitude faster than the safety net",
    );
}

#[test]
fn only_a_change_after_the_baseline_is_worth_re_probing() {
    use super::InstalledAgents;

    let mut installed = InstalledAgents::default();
    // The baseline is not news: every worker probes on startup, so
    // announcing the first sweep too spawns every agent twice for one event.
    assert!(!installed.note("aaa".to_owned()));
    // A sweep that finds the same machine is not news either. This is the
    // one that has to hold at two-second intervals forever.
    assert!(!installed.note("aaa".to_owned()));
    // An agent installed, upgraded in place, or removed is.
    assert!(installed.note("bbb".to_owned()));
    assert!(!installed.note("bbb".to_owned()));
    assert!(installed.note("aaa".to_owned()));
}

#[test]
fn a_probe_that_ran_out_of_time_is_tried_again_in_seconds() {
    // The failure this exists for: the version probe lost a race with the
    // adapter install landing, Claude Code published as unusable, and the
    // next refresh was a quarter of an hour away because `refresh_due` is
    // pushed out when the probe is *spawned* and a successful publish of a
    // bad snapshot never brings it back.
    let mut backoff = TransientBackoff::new();

    let delay = backoff
        .note(true)
        .expect("a transient failure is worth retrying");

    assert!(
        delay <= Duration::from_secs(30),
        "seconds, not a quarter of an hour: {delay:?}"
    );
    assert!(delay < HARNESS_REFRESH_INTERVAL);
}

#[test]
fn an_agent_that_is_simply_not_installed_is_left_alone() {
    // Cursor is absent on most machines and fails identically every time.
    // Retrying that re-probes it for the life of the process; the installed
    // agents fingerprint is what notices if it ever appears.
    let mut backoff = TransientBackoff::new();

    assert_eq!(backoff.note(false), None);
}

#[test]
fn a_machine_that_keeps_losing_the_race_is_asked_less_often() {
    // Backing off rather than hammering, and never past the sweep the host
    // already had -- so the worst case is exactly the old behaviour.
    let mut backoff = TransientBackoff::new();

    let first = backoff.note(true).unwrap();
    let second = backoff.note(true).unwrap();
    assert!(second > first, "{second:?} should be longer than {first:?}");

    for _ in 0..20 {
        let delay = backoff.note(true).unwrap();
        assert!(
            delay <= HARNESS_REFRESH_INTERVAL,
            "ran past the sweep: {delay:?}"
        );
    }

    // And one clean round puts it back where it started, so an agent that
    // recovers is not punished for having been slow once.
    assert_eq!(backoff.note(false), None);
    assert_eq!(backoff.note(true), Some(first));
}

#[test]
fn a_failed_publish_is_retried_in_seconds_not_a_quarter_of_an_hour() {
    // The refresh interval is the safety net behind fingerprint detection,
    // so it fires rarely. It is the wrong answer to "the publish failed":
    // the backend restarts whenever its configuration changes, and a
    // publish that landed during one used to leave this host with nothing
    // published until the next refresh — rejecting every command in
    // between for referencing a harness it had never announced.
    assert!(
        HARNESS_RETRY_INTERVAL * 6 <= HARNESS_REFRESH_INTERVAL,
        "a failure must not wait anything like a full refresh",
    );
    // And a command already in hand has to be able to outlast a retry,
    // otherwise waiting for one is pointless.
    assert!(
        HARNESS_RETRY_INTERVAL < FIRST_HARNESS_WAIT,
        "a command's wait must cover at least one retry",
    );
}

/// A revision is not ours to assume is hex.
///
/// `short_revision` sliced at byte eight. The value arrives in a start
/// command's payload, so a multi-byte character crossing that boundary
/// panicked -- and a panic here ends the target worker, taking every other run
/// on that target with it, to shorten a log line.
#[test]
fn a_revision_is_shortened_on_a_character_boundary() {
    use crate::runtime::short_revision;

    assert_eq!(short_revision("a1a1a1a1cafef00d"), "a1a1a1a1");
    assert_eq!(short_revision("short"), "short");
    assert_eq!(short_revision(""), "");
    // Four-byte characters: the boundary at byte eight falls between them.
    assert_eq!(short_revision("🧪🧪🧪"), "🧪🧪");
    // And one that straddles it.
    assert_eq!(short_revision("abcdef🧪x"), "abcdef");
}
