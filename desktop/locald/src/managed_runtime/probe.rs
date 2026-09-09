//! Whether the guest is answering, and how many misses are a fault.

use super::*;

/// Consecutive transient probe failures tolerated before the runtime is
/// declared lost.
///
/// The status monitor probes every five seconds, so this is a fifteen second
/// window. It exists because `health` shares one control channel with every
/// other guest request: see [`ManagedRuntimeController::probe`].
pub(crate) const PROBE_FAILURE_TOLERANCE: u32 = 3;

/// What a health probe established about the runtime.
#[derive(Debug)]
pub enum ProbeOutcome {
    Healthy(ManagedRuntimeStatus),
    /// The probe failed without establishing that the guest is gone, and not
    /// yet often enough to matter. Forwarders and the last known status are
    /// deliberately retained.
    Transient(io::Error),
    /// The runtime is gone. Forwarders and cached status have been cleared.
    Lost(io::Error),
}

impl ProbeOutcome {
    pub fn is_healthy(&self) -> bool {
        matches!(self, ProbeOutcome::Healthy(_))
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum ProbeVerdict {
    Transient,
    Lost,
}

/// Decide what a failed probe means.
///
/// A timeout says the guest did not answer within the budget, which is what a
/// busy control channel looks like from here -- not that the guest is gone.
/// Every other failure (the helper process exited, the kernel faulted, the
/// connection was refused) does carry that meaning and is acted on at once,
/// preserving the fast detection of a runtime that really has died.
pub(crate) fn classify_probe_failure(error: &io::Error, consecutive_failures: u32) -> ProbeVerdict {
    let starved = matches!(
        error.kind(),
        io::ErrorKind::TimedOut | io::ErrorKind::Interrupted | io::ErrorKind::WouldBlock
    );
    if starved && consecutive_failures < PROBE_FAILURE_TOLERANCE {
        ProbeVerdict::Transient
    } else {
        ProbeVerdict::Lost
    }
}

/// Consecutive failed probes, and what the next one means.
///
/// Split out from the controller so the sequence that matters -- slow, slow,
/// answered, slow -- can be driven without a guest to be slow.
#[derive(Debug, Default)]
pub(crate) struct ProbeTracker {
    pub(crate) consecutive_failures: u32,
}

impl ProbeTracker {
    pub(crate) fn succeeded(&mut self) {
        self.consecutive_failures = 0;
    }

    pub(crate) fn failed(&mut self, error: &io::Error) -> ProbeVerdict {
        self.consecutive_failures = self.consecutive_failures.saturating_add(1);
        classify_probe_failure(error, self.consecutive_failures)
    }
}

impl ManagedRuntimeController {
    /// Ask the guest whether it is alive, and say what the answer means.
    ///
    /// A failed probe is not by itself evidence that the runtime is gone. The
    /// guest control channel carries one request at a time and `health` has a
    /// five second budget, so a sandbox image pull or a callback wait can time
    /// a probe out while the VM is perfectly healthy. This used to tear down
    /// the Postgres and Redis forwarders out from under a running backend and
    /// then restart the whole stack on top of it.
    ///
    /// So only a failure that says something about liveness -- or one that has
    /// repeated past [`PROBE_FAILURE_TOLERANCE`] -- clears the forwarders and
    /// the cached status.
    pub fn probe(&self) -> ProbeOutcome {
        match self.runtime.health() {
            Ok(status) => {
                *self.status.lock().expect("managed runtime status poisoned") =
                    Some(status.clone());
                self.probes
                    .lock()
                    .expect("probe tracker poisoned")
                    .succeeded();
                ProbeOutcome::Healthy(status)
            }
            Err(error) => {
                let verdict = self
                    .probes
                    .lock()
                    .expect("probe tracker poisoned")
                    .failed(&error);
                match verdict {
                    ProbeVerdict::Transient => ProbeOutcome::Transient(error),
                    ProbeVerdict::Lost => {
                        self.clear_forwarders();
                        ProbeOutcome::Lost(error)
                    }
                }
            }
        }
    }
}
