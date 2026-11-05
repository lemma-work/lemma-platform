//! Starting several runs of one agent at once.
//!
//! Every run spawns its own adapter process, and the expensive part of a run
//! that has not prompted yet is that process coming up: a Node runtime, the
//! agent's own configuration and credentials, and for some agents a network
//! round trip before `session/new` answers. Several conversations started
//! together cold-start that many copies at the same moment, on a machine that
//! may already be busy, and each setup request has its own deadline -- so the
//! burst itself was what made most of them miss it.
//!
//! Two things here keep a burst from failing:
//!
//! - a per-agent gate on start-up. A run waits for a slot before its process is
//!   spawned and gives the slot back once its session is configured, before
//!   its prompt goes out. Waiting is not failing: the run stays `ACCEPTED`
//!   while it queues, and the setup deadlines start counting only once it has a
//!   slot, so queueing time never counts against them. The run's own deadline
//!   still bounds the whole wait.
//! - one more attempt for a start-up that failed in a way a second try can fix:
//!   a setup request that ran out of time, or an agent that reported a timeout
//!   of its own. Never for a sign-in or configuration failure, which fail the
//!   same way twice, and never once the prompt has been dispatched.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use tokio::sync::{OwnedSemaphorePermit, Semaphore, watch};

use super::cancel_requested;

/// How many attempts a run's start-up gets in all.
pub(crate) const START_ATTEMPTS: u32 = 2;

/// How many runs of one agent may be starting at the same time.
///
/// Small, because a start is CPU-bound process start-up and contending starts
/// slow each other down: past a few, adding another makes every one of them
/// later rather than any of them sooner.
const DEFAULT_CONCURRENT_STARTS: usize = 3;

/// The agents whose start-up does not tolerate a second copy starting beside
/// it.
///
/// Codex resolves the person's account and workspace when a session starts,
/// against the shared `~/.codex` state and over the network, and concurrent
/// copies doing that together time out with "workspace routing discovery timed
/// out" from inside Codex. Once one session is open the rest start quickly, so
/// one at a time costs little.
fn concurrent_starts(harness: &str) -> usize {
    match harness {
        "codex" => 1,
        _ => DEFAULT_CONCURRENT_STARTS,
    }
}

/// A slot to start one run in. Released when dropped.
pub(crate) type StartPermit = OwnedSemaphorePermit;

/// The start-up gates, one per agent, shared by every run on this host.
#[derive(Default)]
pub struct StartGates {
    gates: Mutex<HashMap<String, Arc<Semaphore>>>,
    /// Overrides the per-agent limit; for tests.
    limit: Option<usize>,
}

impl StartGates {
    /// Gates that let at most `limit` runs of any one agent start together.
    #[must_use]
    pub fn with_limit(limit: usize) -> Self {
        Self {
            gates: Mutex::default(),
            limit: Some(limit.max(1)),
        }
    }

    fn gate(&self, harness: &str) -> Arc<Semaphore> {
        let limit = self.limit.unwrap_or_else(|| concurrent_starts(harness));
        Arc::clone(
            self.gates
                .lock()
                .expect("start gates poisoned")
                .entry(harness.to_owned())
                .or_insert_with(|| Arc::new(Semaphore::new(limit))),
        )
    }

    /// Wait for a slot to start a run of `harness` in.
    ///
    /// `None` when Lemma asked for the run to stop before it got one: a run
    /// that never started has nothing to cancel but its place in the queue.
    pub(crate) async fn acquire(
        &self,
        harness: &str,
        mut cancel: watch::Receiver<bool>,
    ) -> Option<StartPermit> {
        let gate = self.gate(harness);
        if let Ok(permit) = Arc::clone(&gate).try_acquire_owned() {
            return Some(permit);
        }
        tracing::info!(
            harness,
            "waiting for another run of this agent to finish starting"
        );
        tokio::select! {
            permit = gate.acquire_owned() => permit.ok(),
            () = cancel_requested(&mut cancel) => None,
        }
    }
}

/// Whether a run that failed before its prompt went out is worth starting
/// again.
///
/// Only a timeout is: the shape a busy machine produces, and the one a second
/// attempt -- after the burst that caused it has drained -- usually gets past.
/// A sign-in or configuration failure is deterministic and would only fail
/// again, a second time as slowly.
#[must_use]
pub fn is_transient_start_failure(error: &str) -> bool {
    let normalized = error.to_ascii_lowercase();
    let timed_out = normalized.contains("timed out")
        || normalized.contains("did not answer")
        || normalized.contains("timeout");
    let deterministic = normalized.contains("authenticate")
        || normalized.contains("authentication")
        || normalized.contains("not logged in")
        || normalized.contains("unauthorized")
        || normalized.contains("(401)")
        || normalized.contains("invalid params")
        || normalized.contains("invalid configuration");
    timed_out && !deterministic
}
