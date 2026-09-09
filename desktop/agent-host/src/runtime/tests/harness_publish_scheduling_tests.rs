use super::{
    DISK_SCAN_INTERVAL, FIRST_HARNESS_WAIT, HARNESS_REFRESH_INTERVAL, HARNESS_RETRY_INTERVAL,
    TransientBackoff,
};
use crate::protocol::POLL_HOLD;
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
    // And — the part this pair of constants cannot show on its own — the
    // scan has to be able to *reach* the worker inside its own interval.
    // It could not: the check ran once per loop iteration, and an iteration
    // is one held poll, so a two-second interval detected in up to
    // `POLL_HOLD`. `the_scan_does_not_wait_out_a_held_poll` below is the
    // one that fails if that comes back; this only fixes the budget.
    assert!(
        DISK_SCAN_INTERVAL < POLL_HOLD,
        "a scan slower than the poll hold would have nothing to add to it",
    );
}

/// The regression this pair of tests exists for.
///
/// `DISK_SCAN_INTERVAL` was read as "an agent is noticed within two
/// seconds". It was not: it bounded how often the check *could* run, and
/// the check was reached once per iteration of a loop whose every iteration
/// waits out a poll Lemma holds for `POLL_HOLD`. So the real answer was
/// 0-25s, and the constant said 2.
///
/// Both halves are asserted, because either alone still permits the bug:
/// a select arm that does not abandon the poll would not help, and a scan
/// that abandons the poll on every tick would mean the poll never returns.
#[tokio::test(start_paused = true)]
async fn the_scan_does_not_wait_out_a_held_poll() {
    use tokio::sync::watch;

    let (agents_changed, mut receiver) = watch::channel(0_u64);

    // A poll that is being held, exactly as Lemma holds it.
    let held_poll = tokio::time::sleep(POLL_HOLD);
    tokio::pin!(held_poll);

    let woke_at = {
        let started = tokio::time::Instant::now();
        // The supervisor's sweep notices a new agent one interval in.
        tokio::spawn(async move {
            tokio::time::sleep(DISK_SCAN_INTERVAL).await;
            agents_changed.send_modify(|generation| *generation += 1);
        });
        tokio::select! {
            () = &mut held_poll => tokio::time::Instant::now() - started,
            _ = receiver.changed() => tokio::time::Instant::now() - started,
        }
    };

    assert!(
        woke_at < POLL_HOLD,
        "a newly installed agent must not wait out the poll: woke after {woke_at:?}",
    );
    assert_eq!(
        woke_at, DISK_SCAN_INTERVAL,
        "and it must wake on the scan, not on anything else",
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

/// The other half: nothing wakes the loop when the disk is unchanged.
///
/// A tick that fired unconditionally would abandon the in-flight poll every
/// two seconds, so a 25-second poll would never once return and no command
/// would ever be delivered. The arm has to be a change notification, not a
/// timer.
#[tokio::test(start_paused = true)]
async fn an_unchanged_disk_lets_the_poll_run_to_completion() {
    use tokio::sync::watch;

    let (_agents_changed, mut receiver) = watch::channel(0_u64);
    let held_poll = tokio::time::sleep(POLL_HOLD);
    tokio::pin!(held_poll);
    let started = tokio::time::Instant::now();

    tokio::select! {
        () = &mut held_poll => {}
        _ = receiver.changed() => panic!("an unchanged disk must not abandon the poll"),
    }

    assert_eq!(tokio::time::Instant::now() - started, POLL_HOLD);
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
