//! Closing admission before draining prevents startup and recovery racing cleanup.
use std::io;
use std::sync::{Condvar, Mutex};

#[derive(Default)]
struct State {
    active: bool,
    closing: bool,
}

#[derive(Default)]
pub(crate) struct Lifecycle {
    state: Mutex<State>,
    idle: Condvar,
}

impl Lifecycle {
    pub fn begin(&self) -> Result<(), ()> {
        let mut state = self.state.lock().expect("lifecycle lock poisoned");
        if state.active || state.closing {
            return Err(());
        }
        state.active = true;
        Ok(())
    }

    pub fn finish(&self) {
        let mut state = self.state.lock().expect("lifecycle lock poisoned");
        state.active = false;
        self.idle.notify_all();
    }

    pub fn busy(&self) -> bool {
        let state = self.state.lock().expect("lifecycle lock poisoned");
        state.active || state.closing
    }

    pub fn request_shutdown(&self) -> bool {
        let mut state = self.state.lock().expect("lifecycle lock poisoned");
        if state.closing {
            return false;
        }
        state.closing = true;
        true
    }

    pub fn checkpoint(&self) -> io::Result<()> {
        if self.state.lock().expect("lifecycle lock poisoned").closing {
            Err(io::Error::new(
                io::ErrorKind::Interrupted,
                "local operation cancelled because Lemma is stopping",
            ))
        } else {
            Ok(())
        }
    }

    pub fn wait_idle(&self) {
        let mut state = self.state.lock().expect("lifecycle lock poisoned");
        while state.active {
            state = self.idle.wait(state).expect("lifecycle lock poisoned");
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{mpsc, Arc};
    use std::time::Duration;

    #[test]
    fn shutdown_during_work_closes_admission_and_drains_before_cleanup() {
        let lifecycle = Arc::new(Lifecycle::default());
        lifecycle.begin().unwrap();
        assert!(lifecycle.request_shutdown());
        assert!(!lifecycle.request_shutdown());
        assert!(lifecycle.begin().is_err());
        assert_eq!(
            lifecycle.checkpoint().unwrap_err().kind(),
            io::ErrorKind::Interrupted
        );
        let (cleaned, result) = mpsc::channel();
        let worker_lifecycle = Arc::clone(&lifecycle);
        let worker = std::thread::spawn(move || {
            worker_lifecycle.wait_idle();
            cleaned.send(()).unwrap();
        });
        assert!(result.recv_timeout(Duration::from_millis(30)).is_err());
        lifecycle.finish();
        result.recv_timeout(Duration::from_secs(1)).unwrap();
        worker.join().unwrap();
        assert!(
            lifecycle.begin().is_err(),
            "completion must not reopen admission"
        );
    }

    #[test]
    fn completion_before_shutdown_does_not_lose_a_wakeup() {
        let lifecycle = Lifecycle::default();
        lifecycle.begin().unwrap();
        lifecycle.finish();
        lifecycle.begin().unwrap();
        lifecycle.finish();
        assert!(lifecycle.request_shutdown());
        lifecycle.wait_idle();
        assert!(lifecycle.busy());
    }
}
