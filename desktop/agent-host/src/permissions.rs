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

use std::collections::{HashMap, HashSet};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use tokio::sync::oneshot;
use uuid::Uuid;

/// One "always allow", in the agent's own words, for one provider session.
///
/// The label is the name the agent gave its always-allow option — `Always Allow
/// all WebSearch`, `Always Allow WebFetch(domain:en.wikipedia.org)`. The agent
/// writes it from the permission rules it would install, so two requests
/// offering the same label would install the same rules: it is the scope the
/// user actually consented to, and the only description of it either side has.
///
/// Keyed by session because a grant belongs to the conversation it was given
/// in, not to the machine.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct AlwaysAllowScope {
    pub session_id: String,
    pub label: String,
}

/// An always-allow the agent offered on one request: its scope, and the option
/// id that selects it on *this* request.
#[derive(Clone, Debug)]
pub struct AlwaysAllowOffer {
    pub scope: AlwaysAllowScope,
    pub option_id: String,
}

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
    /// The always-allow this request offered, if it offered one. Kept so a
    /// decision can be recognised as an "always" and so a grant given to one
    /// request can release its siblings.
    always: Option<AlwaysAllowOffer>,
}

impl PendingRequest {
    /// The scope this decision grants "always" to, if that is what it is.
    ///
    /// A decision is an "always" only when it selects the option the agent
    /// labelled as one — never inferred from the wording of the decision, which
    /// Lemma's own approval vocabulary would only approximate.
    fn granted_scope(&self, decision: &PermissionDecision) -> Option<AlwaysAllowScope> {
        let always = self.always.as_ref()?;
        match decision {
            PermissionDecision::Allow { option_id } if *option_id == always.option_id => {
                Some(always.scope.clone())
            }
            _ => None,
        }
    }
}

/// Take every parked request the given grant now covers, with the option id
/// that grants it on each one.
fn take_matching(
    pending: &mut PendingRequests,
    scope: &AlwaysAllowScope,
) -> Vec<(PendingRequest, String)> {
    let keys: Vec<PendingKey> = pending
        .iter()
        .filter(|(_, waiting)| {
            waiting
                .always
                .as_ref()
                .is_some_and(|always| always.scope == *scope)
        })
        .map(|(key, _)| key.clone())
        .collect();
    keys.into_iter()
        .filter_map(|key| {
            let waiting = pending.remove(&key)?;
            let option_id = waiting.always.as_ref()?.option_id.clone();
            Some((waiting, option_id))
        })
        .collect()
}

#[derive(Clone, Default)]
pub struct PermissionGate {
    pending: Arc<Mutex<PendingRequests>>,
    generations: Arc<AtomicU64>,
    /// What the user has said "always" to, per provider session.
    ///
    /// The agent's own always-allow lives in the adapter process, which is
    /// started fresh for every run and keeps the rule in memory, so an agent
    /// asked again on the next message — and again for each call of a parallel
    /// batch, since those are all in flight before any answer arrives. An
    /// "always" the user has to give repeatedly is not one, so the grant is
    /// held here, outside any single run, and answered from here.
    granted: Arc<Mutex<HashSet<AlwaysAllowScope>>>,
}

impl PermissionGate {
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    /// Has the user already said "always" to exactly this scope?
    #[must_use]
    pub fn is_granted(&self, scope: &AlwaysAllowScope) -> bool {
        self.granted
            .lock()
            .expect("permission gate poisoned")
            .contains(scope)
    }

    /// Park a request and wait for a decision, denying on timeout.
    ///
    /// `always` is the always-allow option this request offered, if any.
    pub async fn wait(
        &self,
        run_id: Uuid,
        request_id: String,
        timeout: Duration,
        always: Option<AlwaysAllowOffer>,
    ) -> PermissionDecision {
        let (sender, receiver) = oneshot::channel();
        let generation = self.generations.fetch_add(1, Ordering::Relaxed);
        {
            let mut pending = self.pending.lock().expect("permission gate poisoned");
            // A duplicate request id replaces the old waiter; dropping its
            // sender resolves that one as a denial.
            let waiting = PendingRequest {
                generation,
                sender,
                always,
            };
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
    ///
    /// An "always" answer is remembered for its session and applied to every
    /// other request already parked for the same scope, so a user who granted
    /// one call of a parallel batch does not answer the rest of the batch by
    /// hand.
    #[must_use = "a false result means nothing was waiting for this decision"]
    pub fn resolve(&self, run_id: Uuid, request_id: &str, decision: PermissionDecision) -> bool {
        let (waiting, siblings) = {
            let mut pending = self.pending.lock().expect("permission gate poisoned");
            let waiting = pending.remove(&(run_id, request_id.to_owned()));
            let scope = waiting
                .as_ref()
                .and_then(|waiting| waiting.granted_scope(&decision));
            let siblings = match scope {
                Some(scope) => {
                    self.granted
                        .lock()
                        .expect("permission gate poisoned")
                        .insert(scope.clone());
                    take_matching(&mut pending, &scope)
                }
                None => Vec::new(),
            };
            (waiting, siblings)
        };
        for (sibling, option_id) in siblings {
            let _ = sibling.sender.send(PermissionDecision::Allow { option_id });
        }
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

    /// Forget every grant given in one provider session.
    ///
    /// Not called on a run boundary: a grant outliving the run it was given in
    /// is the whole point. This is for a session that is gone.
    pub fn forget_session(&self, session_id: &str) {
        self.granted
            .lock()
            .expect("permission gate poisoned")
            .retain(|scope| scope.session_id != session_id);
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

    fn offer(session: &str, label: &str) -> AlwaysAllowOffer {
        AlwaysAllowOffer {
            scope: AlwaysAllowScope {
                session_id: session.to_owned(),
                label: label.to_owned(),
            },
            option_id: "allow_always".to_owned(),
        }
    }

    fn always() -> PermissionDecision {
        PermissionDecision::Allow {
            option_id: "allow_always".to_owned(),
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
                gate.wait(run_id, "req-1".to_owned(), Duration::from_secs(5), None)
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
                None,
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
                gate.wait(run_id, "req-1".to_owned(), Duration::from_secs(5), None)
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
                gate.wait(run_id, "same-key".to_owned(), Duration::from_secs(30), None)
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
                gate.wait(run_id, "same-key".to_owned(), Duration::from_secs(30), None)
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
                gate.wait(run_id, "req-1".to_owned(), Duration::from_millis(20), None)
                    .await
            })
        };
        assert_eq!(waiter.await.unwrap(), PermissionDecision::Deny);
        assert_eq!(gate.parked(), 0);
    }

    /// "Always" has to mean always, and the agent cannot make it mean that: its
    /// rule lives in an adapter process started fresh for every run, and is
    /// installed only once the answer arrives. So a grant given on Monday's
    /// message was asked for again on Tuesday's, and a grant given to one call
    /// of a parallel batch was asked for again by every other call in it.
    mod always_allow {
        use super::*;

        #[tokio::test]
        async fn a_granted_scope_is_remembered_for_its_session() {
            let gate = PermissionGate::new();
            let run_id = Uuid::now_v7();
            let scope = offer("session-a", "Always Allow all WebSearch").scope;
            assert!(!gate.is_granted(&scope));

            let waiter = {
                let gate = gate.clone();
                tokio::spawn(async move {
                    gate.wait(
                        run_id,
                        "req-1".to_owned(),
                        Duration::from_secs(5),
                        Some(offer("session-a", "Always Allow all WebSearch")),
                    )
                    .await
                })
            };
            parked_at_least(&gate, 1).await;
            assert!(gate.resolve(run_id, "req-1", always()));

            assert_eq!(waiter.await.unwrap(), always());
            assert!(gate.is_granted(&scope));
        }

        #[tokio::test]
        async fn allowing_once_grants_nothing() {
            let gate = PermissionGate::new();
            let run_id = Uuid::now_v7();
            let waiter = {
                let gate = gate.clone();
                tokio::spawn(async move {
                    gate.wait(
                        run_id,
                        "req-1".to_owned(),
                        Duration::from_secs(5),
                        Some(offer("session-a", "Always Allow all WebSearch")),
                    )
                    .await
                })
            };
            parked_at_least(&gate, 1).await;
            assert!(gate.resolve(run_id, "req-1", allow()));
            let _ = waiter.await.unwrap();

            assert!(!gate.is_granted(&offer("session-a", "Always Allow all WebSearch").scope));
        }

        #[tokio::test]
        async fn a_grant_releases_the_rest_of_a_parallel_batch() {
            // Four fetches of the same domain go out together, so all four are
            // asking before any of them is answered. Answering one "always"
            // answers them all; without this the user clicks four times for the
            // grant they already gave.
            let gate = PermissionGate::new();
            let run_id = Uuid::now_v7();
            let label = "Always Allow WebFetch(domain:en.wikipedia.org)";
            let siblings: Vec<_> = ["req-2", "req-3", "req-4"]
                .into_iter()
                .map(|request_id| {
                    let gate = gate.clone();
                    tokio::spawn(async move {
                        gate.wait(
                            run_id,
                            request_id.to_owned(),
                            Duration::from_secs(5),
                            Some(offer("session-a", label)),
                        )
                        .await
                    })
                })
                .collect();
            let answered = {
                let gate = gate.clone();
                tokio::spawn(async move {
                    gate.wait(
                        run_id,
                        "req-1".to_owned(),
                        Duration::from_secs(5),
                        Some(offer("session-a", label)),
                    )
                    .await
                })
            };
            parked_at_least(&gate, 4).await;

            assert!(gate.resolve(run_id, "req-1", always()));

            assert_eq!(answered.await.unwrap(), always());
            for sibling in siblings {
                assert_eq!(sibling.await.unwrap(), always());
            }
        }

        #[tokio::test]
        async fn a_narrower_scope_still_asks() {
            // "Always Allow WebFetch(domain:github.com)" is not consent for
            // another domain, and the agent says so in the label it wrote.
            let gate = PermissionGate::new();
            let run_id = Uuid::now_v7();
            let other = {
                let gate = gate.clone();
                tokio::spawn(async move {
                    gate.wait(
                        run_id,
                        "req-2".to_owned(),
                        Duration::from_secs(5),
                        Some(offer(
                            "session-a",
                            "Always Allow WebFetch(domain:evil.test)",
                        )),
                    )
                    .await
                })
            };
            let granted = {
                let gate = gate.clone();
                tokio::spawn(async move {
                    gate.wait(
                        run_id,
                        "req-1".to_owned(),
                        Duration::from_secs(5),
                        Some(offer(
                            "session-a",
                            "Always Allow WebFetch(domain:github.com)",
                        )),
                    )
                    .await
                })
            };
            parked_at_least(&gate, 2).await;

            assert!(gate.resolve(run_id, "req-1", always()));
            let _ = granted.await.unwrap();

            assert!(!other.is_finished(), "a different scope was answered for");
            assert!(gate.resolve(run_id, "req-2", PermissionDecision::Deny));
            assert_eq!(other.await.unwrap(), PermissionDecision::Deny);
        }

        #[tokio::test]
        async fn another_conversation_does_not_inherit_the_grant() {
            let gate = PermissionGate::new();
            let run_id = Uuid::now_v7();
            let waiter = {
                let gate = gate.clone();
                tokio::spawn(async move {
                    gate.wait(
                        run_id,
                        "req-1".to_owned(),
                        Duration::from_secs(5),
                        Some(offer("session-a", "Always Allow all Bash")),
                    )
                    .await
                })
            };
            parked_at_least(&gate, 1).await;
            assert!(gate.resolve(run_id, "req-1", always()));
            let _ = waiter.await.unwrap();

            assert!(!gate.is_granted(&offer("session-b", "Always Allow all Bash").scope));
        }

        #[tokio::test]
        async fn a_session_that_is_gone_takes_its_grants_with_it() {
            let gate = PermissionGate::new();
            let run_id = Uuid::now_v7();
            let waiter = {
                let gate = gate.clone();
                tokio::spawn(async move {
                    gate.wait(
                        run_id,
                        "req-1".to_owned(),
                        Duration::from_secs(5),
                        Some(offer("session-a", "Always Allow all Bash")),
                    )
                    .await
                })
            };
            parked_at_least(&gate, 1).await;
            assert!(gate.resolve(run_id, "req-1", always()));
            let _ = waiter.await.unwrap();

            gate.forget_session("session-a");

            assert!(!gate.is_granted(&offer("session-a", "Always Allow all Bash").scope));
        }
    }
}
