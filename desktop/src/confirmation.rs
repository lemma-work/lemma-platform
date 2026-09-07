//! One app-owned confirmation at a time, bound to its requesting operation.
use serde::{Deserialize, Serialize};
use std::sync::{mpsc, Mutex};

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum Decision {
    Confirm,
    Cancel,
    Discard,
}

struct Pending {
    id: String,
    allow_discard: bool,
    sender: mpsc::SyncSender<Decision>,
}

#[derive(Default)]
pub struct Confirmations {
    pending: Mutex<Option<Pending>>,
}

impl Confirmations {
    pub fn begin(
        &self,
        id: String,
        allow_discard: bool,
    ) -> Result<mpsc::Receiver<Decision>, String> {
        let mut pending = self
            .pending
            .lock()
            .map_err(|_| "confirmation state unavailable")?;
        if pending.is_some() {
            return Err("Finish or cancel the open confirmation first.".into());
        }
        let (sender, receiver) = mpsc::sync_channel(1);
        *pending = Some(Pending {
            id,
            allow_discard,
            sender,
        });
        Ok(receiver)
    }

    /// Run on the UI thread: closing must not wait on a thread that can cancel
    /// this decision. Keep the slot reserved until the old overlay is gone.
    pub fn resolve(
        &self,
        id: &str,
        decision: Decision,
        close: impl FnOnce() -> Result<(), String>,
    ) -> Result<(), String> {
        let mut pending = self
            .pending
            .lock()
            .map_err(|_| "confirmation state unavailable")?;
        if !pending.as_ref().is_some_and(|current| {
            current.id == id && (decision != Decision::Discard || current.allow_discard)
        }) {
            return Err("This confirmation is no longer active.".into());
        }
        close()?;
        if let Some(current) = pending.take() {
            let _ = current.sender.send(decision);
        }
        Ok(())
    }

    pub fn cancel(&self) {
        if let Ok(mut pending) = self.pending.lock() {
            if let Some(current) = pending.take() {
                let _ = current.sender.send(Decision::Cancel);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn only_the_active_decision_can_close_the_overlay() {
        let state = Confirmations::default();
        let result = state.begin("current".into(), false).unwrap();
        for (id, decision) in [("old", Decision::Confirm), ("current", Decision::Discard)] {
            assert!(state
                .resolve(id, decision, || panic!("closed an unrelated prompt"))
                .is_err());
        }
        assert!(state
            .resolve("current", Decision::Confirm, || Err(
                "could not close".into()
            ))
            .is_err());
        assert!(result.try_recv().is_err());
        assert!(state.begin("next".into(), false).is_err());
        state
            .resolve("current", Decision::Cancel, || Ok(()))
            .unwrap();
        assert_eq!(result.recv().unwrap(), Decision::Cancel);
        let next = state.begin("next".into(), false).unwrap();
        assert!(state
            .resolve("current", Decision::Confirm, || panic!(
                "closed the new prompt"
            ))
            .is_err());
        assert!(next.try_recv().is_err());
    }
    #[test]
    fn discard_is_only_valid_for_a_settings_decision() {
        let state = Confirmations::default();
        let result = state.begin("erase".into(), false).unwrap();
        assert!(state
            .resolve("erase", Decision::Discard, || Ok(()))
            .is_err());
        assert!(result.try_recv().is_err());
        state.cancel();
        assert_eq!(result.recv().unwrap(), Decision::Cancel);

        let result = state.begin("settings".into(), true).unwrap();
        state
            .resolve("settings", Decision::Discard, || Ok(()))
            .unwrap();
        assert_eq!(result.recv().unwrap(), Decision::Discard);
    }
    #[test]
    fn stale_and_duplicate_responses_cannot_authorize_another_operation() {
        let state = Confirmations::default();
        let first = state.begin("first".into(), false).unwrap();
        assert!(state.begin("second".into(), false).is_err());
        assert!(state
            .resolve("other", Decision::Confirm, || Ok(()))
            .is_err());
        assert!(first.try_recv().is_err());
        state.resolve("first", Decision::Cancel, || Ok(())).unwrap();
        assert_eq!(first.recv().unwrap(), Decision::Cancel);
        let second = state.begin("second".into(), false).unwrap();
        assert!(state
            .resolve("first", Decision::Confirm, || Ok(()))
            .is_err());
        assert!(second.try_recv().is_err());
        state
            .resolve("second", Decision::Confirm, || Ok(()))
            .unwrap();
        assert_eq!(second.recv().unwrap(), Decision::Confirm);
        assert!(state
            .resolve("second", Decision::Confirm, || Ok(()))
            .is_err());
    }
    #[test]
    fn closing_the_app_window_cancels_and_releases_the_waiter() {
        let state = Confirmations::default();
        let result = state.begin("cleanup".into(), false).unwrap();
        state.cancel();
        assert_eq!(result.recv().unwrap(), Decision::Cancel);
        assert!(state.begin("retry".into(), false).is_ok());
    }
}
