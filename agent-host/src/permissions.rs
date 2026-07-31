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
type PendingRequests = HashMap<PendingKey, oneshot::Sender<PermissionDecision>>;

#[derive(Clone, Default)]
pub struct PermissionGate {
    pending: Arc<Mutex<PendingRequests>>,
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
        {
            let mut pending = self.pending.lock().expect("permission gate poisoned");
            // A duplicate request id replaces the old waiter; dropping its
            // sender resolves that one as a denial.
            pending.insert((run_id, request_id.clone()), sender);
        }
        let decision = match tokio::time::timeout(timeout, receiver).await {
            Ok(Ok(decision)) => decision,
            // Timed out, or the sender was dropped without deciding.
            _ => PermissionDecision::Deny,
        };
        self.forget(run_id, &request_id);
        decision
    }

    /// Deliver a decision. Returns false when nothing was waiting for it,
    /// which happens for a duplicate or late resolution.
    #[must_use = "a false result means nothing was waiting for this decision"]
    pub fn resolve(&self, run_id: Uuid, request_id: &str, decision: PermissionDecision) -> bool {
        let sender = {
            let mut pending = self.pending.lock().expect("permission gate poisoned");
            pending.remove(&(run_id, request_id.to_owned()))
        };
        match sender {
            Some(sender) => sender.send(decision).is_ok(),
            None => false,
        }
    }

    /// Deny everything still waiting for a run, e.g. on cancellation.
    pub fn abandon_run(&self, run_id: Uuid) {
        let mut pending = self.pending.lock().expect("permission gate poisoned");
        pending.retain(|(pending_run, _), _| *pending_run != run_id);
    }

    fn forget(&self, run_id: Uuid, request_id: &str) {
        let mut pending = self.pending.lock().expect("permission gate poisoned");
        pending.remove(&(run_id, request_id.to_owned()));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

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
}
