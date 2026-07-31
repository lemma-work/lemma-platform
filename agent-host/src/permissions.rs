//! Pending permission requests awaiting a decision from Lemma.
//!
//! An ACP agent asks for permission through a request the adapter holds open
//! until it is answered. Previously the host answered `Cancelled` immediately,
//! so every native tool call was denied and there was no way to approve one.
//!
//! Now the responder is parked here, keyed by the request's tool-call id, and
//! the run task awaits a decision that arrives as a `RESOLVE_PERMISSION`
//! command. A request that is never answered is denied when its timeout
//! elapses, so a forgotten prompt cannot pin an adapter open forever.

use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use tokio::sync::oneshot;
use uuid::Uuid;

/// What Lemma decided about one permission request.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum PermissionDecision {
    /// Approve, selecting one of the options the agent offered.
    Allow { option_id: String },
    /// Deny. Also the outcome when a request times out or the run ends.
    Deny,
}

/// Keyed by (run, request) so a decision cannot resolve another run's prompt.
type PendingKey = (Uuid, String);
type PendingRequests = HashMap<PendingKey, PendingRequest>;

/// One parked waiter, tagged so it can only be removed by itself.
///
/// The tag matters because the key is not unique in practice: `acp.rs` falls
/// back to the session id when a permission request carries no tool-call id, so
/// every parallel tool call in one session lands on the same key. Without the
/// tag a waiter that had already been displaced would remove its successor's
/// sender on the way out, stranding a live request that Lemma has answered.
struct PendingRequest {
    generation: u64,
    sender: oneshot::Sender<PermissionDecision>,
}

#[derive(Clone, Default)]
pub struct PermissionGate {
    pending: Arc<Mutex<PendingRequests>>,
    generations: Arc<AtomicU64>,
}

impl PermissionGate {
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    /// Park a request and wait for a decision, denying on timeout.
    pub async fn wait(
        &self,
        run_id: Uuid,
        request_id: String,
        timeout: Duration,
    ) -> PermissionDecision {
        let (sender, receiver) = oneshot::channel();
        let generation = self.generations.fetch_add(1, Ordering::Relaxed);
        {
            let mut pending = self.pending.lock().expect("permission gate poisoned");
            // A duplicate request id replaces the old waiter; dropping its
            // sender resolves that one as a denial.
            let waiting = PendingRequest { generation, sender };
            pending.insert((run_id, request_id.clone()), waiting);
        }
        let decision = match tokio::time::timeout(timeout, receiver).await {
            Ok(Ok(decision)) => decision,
            // Timed out, or the sender was dropped without deciding.
            _ => PermissionDecision::Deny,
        };
        self.forget(run_id, &request_id, generation);
        decision
    }

    /// Deliver a decision. Returns false when nothing was waiting for it,
    /// which happens for a duplicate or late resolution.
    #[must_use = "a false result means nothing was waiting for this decision"]
    pub fn resolve(&self, run_id: Uuid, request_id: &str, decision: PermissionDecision) -> bool {
        let waiting = {
            let mut pending = self.pending.lock().expect("permission gate poisoned");
            pending.remove(&(run_id, request_id.to_owned()))
        };
        match waiting {
            Some(waiting) => waiting.sender.send(decision).is_ok(),
            None => false,
        }
    }

    /// Deny everything still waiting for a run, e.g. on cancellation.
    pub fn abandon_run(&self, run_id: Uuid) {
        let mut pending = self.pending.lock().expect("permission gate poisoned");
        pending.retain(|(pending_run, _), _| *pending_run != run_id);
    }

    /// How many requests are parked. Used to prove a run leaves none behind.
    #[cfg(test)]
    pub(crate) fn parked(&self) -> usize {
        self.pending.lock().expect("permission gate poisoned").len()
    }

    /// Remove this waiter's own entry, leaving a newer one for the same key
    /// alone. Compare-and-remove rather than a blind `remove`.
    fn forget(&self, run_id: Uuid, request_id: &str, generation: u64) {
        let mut pending = self.pending.lock().expect("permission gate poisoned");
        let key = (run_id, request_id.to_owned());
        if pending
            .get(&key)
            .is_some_and(|waiting| waiting.generation == generation)
        {
            pending.remove(&key);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn allow() -> PermissionDecision {
        PermissionDecision::Allow {
            option_id: "allow-once".to_owned(),
        }
    }

    /// Let spawned waiters run until the gate holds `count` parked requests.
    async fn parked_at_least(gate: &PermissionGate, count: usize) {
        for _ in 0..10_000 {
            if gate.parked() >= count {
                return;
            }
            tokio::task::yield_now().await;
        }
        panic!("a waiter never parked (gate holds {})", gate.parked());
    }

    #[tokio::test]
    async fn resolving_an_awaited_request_returns_its_decision() {
        let gate = PermissionGate::new();
        let run_id = Uuid::now_v7();
        let waiter = {
            let gate = gate.clone();
            tokio::spawn(async move {
                gate.wait(run_id, "req-1".to_owned(), Duration::from_secs(5))
                    .await
            })
        };
        // Let the waiter register before resolving.
        tokio::task::yield_now().await;
        for _ in 0..50 {
            if gate.resolve(
                run_id,
                "req-1",
                PermissionDecision::Allow {
                    option_id: "allow-once".to_owned(),
                },
            ) {
                break;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        assert_eq!(
            waiter.await.unwrap(),
            PermissionDecision::Allow {
                option_id: "allow-once".to_owned()
            }
        );
    }

    #[tokio::test]
    async fn an_unanswered_request_denies_rather_than_hanging() {
        let gate = PermissionGate::new();
        let decision = gate
            .wait(
                Uuid::now_v7(),
                "req-1".to_owned(),
                Duration::from_millis(20),
            )
            .await;
        assert_eq!(decision, PermissionDecision::Deny);
    }

    #[tokio::test]
    async fn resolving_an_unknown_request_reports_that_nothing_waited() {
        let gate = PermissionGate::new();
        assert!(!gate.resolve(Uuid::now_v7(), "missing", PermissionDecision::Deny));
    }

    #[tokio::test]
    async fn abandoning_a_run_denies_its_waiters() {
        let gate = PermissionGate::new();
        let run_id = Uuid::now_v7();
        let waiter = {
            let gate = gate.clone();
            tokio::spawn(async move {
                gate.wait(run_id, "req-1".to_owned(), Duration::from_secs(5))
                    .await
            })
        };
        tokio::task::yield_now().await;
        for _ in 0..50 {
            gate.abandon_run(run_id);
            if waiter.is_finished() {
                break;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        assert_eq!(waiter.await.unwrap(), PermissionDecision::Deny);
    }

    /// `acp.rs` keys a permission request by its tool-call id and falls back to
    /// the session id, so parallel tool calls in one session collide on a
    /// single key. The displaced waiter must not take its successor with it.
    #[tokio::test]
    async fn a_displaced_waiter_leaves_its_successor_reachable() {
        let gate = PermissionGate::new();
        let run_id = Uuid::now_v7();
        let spawn_waiter = || {
            let gate = gate.clone();
            tokio::spawn(async move {
                gate.wait(run_id, "same-key".to_owned(), Duration::from_secs(30))
                    .await
            })
        };

        let first = spawn_waiter();
        parked_at_least(&gate, 1).await;
        // The second waiter displaces the first, whose sender is dropped: the
        // first resolves Deny and then tries to clean up after itself.
        let second = spawn_waiter();
        assert_eq!(
            first.await.unwrap(),
            PermissionDecision::Deny,
            "the displaced waiter is denied"
        );
        assert_eq!(
            gate.parked(),
            1,
            "the displaced waiter removed the live waiter's entry"
        );

        assert!(
            gate.resolve(run_id, "same-key", allow()),
            "Lemma's decision must still reach the live waiter"
        );
        assert_eq!(second.await.unwrap(), allow());
    }

    /// Claude Code issues parallel tool calls, so the same key can be
    /// displaced repeatedly. Every departing waiter must remove only itself.
    #[tokio::test]
    async fn repeated_displacement_keeps_the_newest_waiter_reachable() {
        let gate = PermissionGate::new();
        let run_id = Uuid::now_v7();
        let spawn_waiter = || {
            let gate = gate.clone();
            tokio::spawn(async move {
                gate.wait(run_id, "same-key".to_owned(), Duration::from_secs(30))
                    .await
            })
        };

        let mut newest = spawn_waiter();
        parked_at_least(&gate, 1).await;
        for round in 0..3 {
            let next = spawn_waiter();
            // The parked waiter can only finish once `next` has displaced it,
            // so awaiting it here also fences `next`'s registration.
            assert_eq!(newest.await.unwrap(), PermissionDecision::Deny);
            assert_eq!(gate.parked(), 1, "round {round} stranded the live waiter");
            newest = next;
        }

        assert!(gate.resolve(run_id, "same-key", allow()));
        assert_eq!(newest.await.unwrap(), allow());
        assert_eq!(gate.parked(), 0, "every waiter cleaned up after itself");
    }

    #[tokio::test]
    async fn a_timed_out_waiter_cleans_up_after_itself() {
        let gate = PermissionGate::new();
        let run_id = Uuid::now_v7();
        let waiter = {
            let gate = gate.clone();
            tokio::spawn(async move {
                gate.wait(run_id, "req-1".to_owned(), Duration::from_millis(20))
                    .await
            })
        };
        assert_eq!(waiter.await.unwrap(), PermissionDecision::Deny);
        assert_eq!(gate.parked(), 0);
    }
}
