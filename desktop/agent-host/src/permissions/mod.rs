//! Whether an agent may do what it just asked to do.
//!
//! Was one 642-line file; the guards are a file of their own now.

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
mod tests;
