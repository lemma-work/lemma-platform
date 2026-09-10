//! The guest clock, which drifts across a host suspend.

use super::*;

/// How often the guest's wall clock is put back on this machine's.
///
/// The guest sets its time once, at boot, and nothing moves it afterwards. A
/// Virtualization.framework VM does not run while the Mac sleeps, so the guest
/// clock falls behind by however long the lid was closed and stays there. That
/// broke sign-in outright: the auth service runs inside the guest, so every
/// access token it minted carried an `exp` computed from the wrong clock, the
/// backend on the Mac read it as already expired, and the browser refreshed --
/// getting another already-expired token from the same wrong clock, forever.
///
/// Thirty seconds is a bound on drift, not a poll for it: the request is one
/// small round trip over the control socket, and the sleep case is caught
/// within a tick anyway.
pub(crate) const CLOCK_SYNC_INTERVAL: Duration = Duration::from_secs(30);
/// The slice the keeper sleeps in, so stopping does not wait out an interval.
pub(crate) const CLOCK_KEEPER_TICK: Duration = Duration::from_secs(1);
/// Wall time that ran further than the monotonic clock across one tick means
/// the Mac was asleep in between -- `Instant` does not advance while it is.
/// The guest was not running for that stretch, so it is now exactly that far
/// behind and should not wait for the interval to find out.
pub(crate) const HOST_SLEEP_MARGIN: Duration = Duration::from_secs(5);

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum ClockSyncReason {
    HostSlept,
    Interval,
}

/// Should this tick put the guest clock back on the host's, and why?
///
/// Split out from the loop because the loop is a thread and this is the part
/// worth asserting on.
pub(crate) fn clock_sync_due(
    since_last_sync: Duration,
    monotonic: Duration,
    wall: Duration,
) -> Option<ClockSyncReason> {
    if wall > monotonic + HOST_SLEEP_MARGIN {
        return Some(ClockSyncReason::HostSlept);
    }
    if since_last_sync >= CLOCK_SYNC_INTERVAL {
        return Some(ClockSyncReason::Interval);
    }
    None
}

pub(crate) struct ClockKeeper {
    pub(crate) stop: Arc<AtomicBool>,
    pub(crate) handle: JoinHandle<()>,
}

impl ManagedRuntimeController {
    /// Hold the guest clock on this machine's for as long as the stack runs.
    ///
    /// Idempotent: a second call while one is running is a no-op, so a recovery
    /// path that starts an already-started stack does not leave two threads
    /// stepping the same clock.
    pub(crate) fn start_clock_keeper(self: &Arc<Self>) {
        let mut slot = self
            .clock_keeper
            .lock()
            .expect("clock keeper lock poisoned");
        if slot.is_some() {
            return;
        }
        let stop = Arc::new(AtomicBool::new(false));
        let controller = Arc::clone(self);
        let flag = Arc::clone(&stop);
        let handle = thread::spawn(move || controller.keep_clock(&flag));
        *slot = Some(ClockKeeper { stop, handle });
    }

    pub(crate) fn stop_clock_keeper(&self) {
        let keeper = self
            .clock_keeper
            .lock()
            .expect("clock keeper lock poisoned")
            .take();
        if let Some(keeper) = keeper {
            keeper.stop.store(true, Ordering::Release);
            let _ = keeper.handle.join();
        }
    }

    pub(crate) fn keep_clock(&self, stop: &AtomicBool) {
        let mut last_sync = Instant::now();
        let mut last_tick = Instant::now();
        let mut last_wall = SystemTime::now();
        while !stop.load(Ordering::Acquire) {
            thread::sleep(CLOCK_KEEPER_TICK);
            if stop.load(Ordering::Acquire) {
                return;
            }
            let tick = Instant::now();
            let wall = SystemTime::now();
            let reason = clock_sync_due(
                tick.duration_since(last_sync),
                tick.duration_since(last_tick),
                wall.duration_since(last_wall).unwrap_or_default(),
            );
            last_tick = tick;
            last_wall = wall;
            let Some(reason) = reason else {
                continue;
            };
            last_sync = tick;
            self.sync_guest_clock(Some(reason));
        }
    }

    /// One correction, reported only when there was something to correct.
    ///
    /// Never fatal. A guest that will not take a clock is a guest with a
    /// problem this cannot fix, and tearing the stack down over it would turn a
    /// recoverable drift into an outage.
    pub(crate) fn sync_guest_clock(&self, reason: Option<ClockSyncReason>) {
        match self.runtime.sync_clock() {
            Ok(report) => {
                if !report
                    .get("stepped")
                    .and_then(serde_json::Value::as_bool)
                    .unwrap_or(false)
                {
                    return;
                }
                let skew = report
                    .get("skew_seconds")
                    .and_then(serde_json::Value::as_i64)
                    .unwrap_or_default();
                let cause = match reason {
                    Some(ClockSyncReason::HostSlept) => " after this Mac slept",
                    Some(ClockSyncReason::Interval) | None => "",
                };
                eprintln!("locald: the guest clock was {skew}s behind this Mac{cause}; corrected");
                self.last_clock_error
                    .lock()
                    .expect("clock error lock poisoned")
                    .take();
            }
            Err(error) => {
                // Once per distinct failure. A guest too old to know
                // `system.clock` refuses every attempt, and at this cadence
                // saying so each time is a line twice a minute for as long as
                // the stack runs -- which is the shape of log flood this
                // codebase has already paid for once.
                let message = error.to_string();
                let mut last = self
                    .last_clock_error
                    .lock()
                    .expect("clock error lock poisoned");
                if last.as_deref() == Some(message.as_str()) {
                    return;
                }
                eprintln!("locald: could not put the guest clock back on this Mac's: {message}");
                *last = Some(message);
            }
        }
    }
}
