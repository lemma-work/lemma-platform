//! One app-owned confirmation at a time, bound to its requesting operation.
use std::sync::{mpsc, Mutex};

#[derive(Default)]
pub struct Confirmations {
    pending: Mutex<Option<(String, mpsc::SyncSender<bool>)>>,
}

impl Confirmations {
    pub fn validate(&self, id: &str) -> Result<(), String> {
        let pending = self
            .pending
            .lock()
            .map_err(|_| "confirmation state unavailable")?;
        if pending.as_ref().is_some_and(|(current, _)| current == id) {
            Ok(())
        } else {
            Err("This confirmation is no longer active.".into())
        }
    }
    pub fn begin(&self, id: String) -> Result<mpsc::Receiver<bool>, String> {
        let mut pending = self
            .pending
            .lock()
            .map_err(|_| "confirmation state unavailable")?;
        if pending.is_some() {
            return Err("Finish or cancel the open confirmation first.".into());
        }
        let (sender, receiver) = mpsc::sync_channel(1);
        *pending = Some((id, sender));
        Ok(receiver)
    }

    pub fn resolve(&self, id: &str, confirmed: bool) -> Result<(), String> {
        let mut pending = self
            .pending
            .lock()
            .map_err(|_| "confirmation state unavailable")?;
        if !pending.as_ref().is_some_and(|(current, _)| current == id) {
            return Err("This confirmation is no longer active.".into());
        }
        if let Some((_, sender)) = pending.take() {
            let _ = sender.send(confirmed);
        }
        Ok(())
    }

    pub fn cancel(&self) {
        if let Ok(mut pending) = self.pending.lock() {
            if let Some((_, sender)) = pending.take() {
                let _ = sender.send(false);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn stale_and_duplicate_responses_cannot_authorize_another_operation() {
        let state = Confirmations::default();
        let first = state.begin("first".into()).unwrap();
        assert!(state.begin("second".into()).is_err());
        assert!(state.resolve("other", true).is_err());
        assert!(first.try_recv().is_err());
        state.resolve("first", false).unwrap();
        assert!(!first.recv().unwrap());
        let second = state.begin("second".into()).unwrap();
        assert!(state.resolve("first", true).is_err());
        assert!(second.try_recv().is_err());
        state.resolve("second", true).unwrap();
        assert!(second.recv().unwrap());
        assert!(state.resolve("second", true).is_err());
    }
    #[test]
    fn closing_the_app_window_cancels_and_releases_the_waiter() {
        let state = Confirmations::default();
        let result = state.begin("cleanup".into()).unwrap();
        state.cancel();
        assert!(!result.recv().unwrap());
        assert!(state.begin("retry".into()).is_ok());
    }
}
