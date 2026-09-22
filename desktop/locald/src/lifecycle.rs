//! Closing admission before draining prevents startup and recovery racing cleanup.
use std::io;
use std::sync::{Condvar, Mutex, PoisonError};

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
        let mut state = self.state.lock().unwrap_or_else(PoisonError::into_inner);
        state.active = false;
        self.idle.notify_all();
    }

    /// Take the lifecycle for the life of the returned guard.
    ///
    /// `None` when another operation already owns it, exactly as `begin` fails.
    pub fn enter(&self) -> Option<Finish<'_>> {
        self.begin().ok().map(|()| Finish(self))
    }

    /// Release a lifecycle that was begun elsewhere, when this guard drops.
    ///
    /// For the common shape where admission is decided on the request thread
    /// -- so a busy caller is told so immediately -- and the work runs on a
    /// spawned one. Taken as the first line of that thread, so the release
    /// happens however the thread ends.
    pub fn finish_on_drop(&self) -> Finish<'_> {
        Finish(self)
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

/// Run something that touches running services, but only while holding the
/// lifecycle. Returns `None` when another operation already owns it.
///
/// The caller decides what to do instead; there is no waiting here, because
/// every caller so far is on an exit path where waiting is the wrong answer.
pub(crate) fn guarded<T>(lifecycle: &Lifecycle, work: impl FnOnce() -> T) -> Option<T> {
    let _finish = lifecycle.enter()?;
    Some(work())
}

/// Releases the lifecycle when dropped, including while unwinding.
///
/// Every operation used to pair `begin` and `finish` by hand -- nineteen
/// sites, each ending its work with a bare `finish()`. A panic or early return
/// between the two left `active` set for the rest of the process: every later
/// operation was refused as "already working", and `wait_idle` on the quit
/// path blocked forever, so the app could not even be closed. The existing
/// `guarded` helper had the same gap, and its test asserted the guard was
/// released "however the work ended" while only ever ending it normally.
#[must_use = "the lifecycle is released when this guard is dropped"]
pub(crate) struct Finish<'a>(&'a Lifecycle);

impl Drop for Finish<'_> {
    fn drop(&mut self) {
        self.0.finish();
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

    /// Quitting while a start is in flight must not kill that start.
    ///
    /// Closing a public exposure restarts the backend and frontend to put
    /// their origins back, and `stop_all` runs whether or not a startup is in
    /// progress -- so the quit path did exactly that to the processes a start
    /// was still health-gating, and reported "process exited" to the user who
    /// had asked to quit. The caller now gets `None` and closes the tunnel on
    /// its own instead, which is the half that must not outlive the app.
    #[test]
    fn work_that_restarts_services_stands_aside_for_an_operation_in_flight() {
        let lifecycle = Lifecycle::default();

        let ran = std::cell::Cell::new(false);
        assert_eq!(guarded(&lifecycle, || ran.set(true)), Some(()));
        assert!(ran.get(), "an idle lifecycle admits the work");
        assert!(
            !lifecycle.busy(),
            "the guard must be released however the work ended"
        );

        lifecycle.begin().expect("a start takes the lifecycle");
        let ran_during = std::cell::Cell::new(false);
        assert_eq!(guarded(&lifecycle, || ran_during.set(true)), None);
        assert!(
            !ran_during.get(),
            "a restart must not run underneath a start that is still gating"
        );

        lifecycle.finish();
        assert_eq!(guarded(&lifecycle, || 7), Some(7), "and it recovers after");
    }

    /// The case the old test's own comment claimed and never exercised.
    ///
    /// "The guard must be released however the work ended" -- and then it only
    /// ever ended the work normally. A panic inside `guarded` left the
    /// lifecycle taken for the life of the process.
    #[test]
    fn work_that_panics_still_releases_the_lifecycle() {
        let lifecycle = Lifecycle::default();

        let outcome = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            guarded(&lifecycle, || panic!("the work failed"))
        }));

        assert!(outcome.is_err(), "the panic still reaches the caller");
        assert!(!lifecycle.busy(), "and the lifecycle is free again");
        assert!(
            lifecycle.enter().is_some(),
            "so the next operation is admitted"
        );
    }

    /// A thread that was admitted elsewhere releases on the way out, whatever
    /// the way out is.
    #[test]
    fn a_thread_that_panics_after_admission_releases_the_lifecycle() {
        let lifecycle = Arc::new(Lifecycle::default());
        lifecycle.begin().expect("admitted on the request thread");

        let worker = Arc::clone(&lifecycle);
        let joined = std::thread::spawn(move || {
            let _finish = worker.finish_on_drop();
            panic!("host operation requires manager");
        })
        .join();

        assert!(joined.is_err());
        assert!(
            !lifecycle.busy(),
            "a panicking operation wedged every later one"
        );
        lifecycle.wait_idle();
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
