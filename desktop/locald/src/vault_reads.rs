//! Bound callers waiting on native credential reads without accumulating workers.
use std::io;
use std::sync::{mpsc, Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

#[derive(Default)]
struct Gate {
    busy: Mutex<bool>,
    available: Condvar,
}

struct Permit(Arc<Gate>);

impl Drop for Permit {
    fn drop(&mut self) {
        if let Ok(mut busy) = self.0.busy.lock() {
            *busy = false;
            self.0.available.notify_one();
        }
    }
}

#[derive(Default)]
pub(crate) struct VaultReads(Arc<Gate>);

impl VaultReads {
    pub(crate) fn read(
        &self,
        budget: Duration,
        read: impl FnOnce() -> io::Result<Option<String>> + Send + 'static,
    ) -> io::Result<Option<String>> {
        let deadline = Instant::now() + budget;
        let mut busy = self.0.busy.lock().map_err(|_| unavailable())?;
        while *busy {
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                return Err(timed_out());
            }
            busy = self
                .0
                .available
                .wait_timeout(busy, remaining)
                .map_err(|_| unavailable())?
                .0;
        }
        if Instant::now() >= deadline {
            return Err(timed_out());
        }
        *busy = true;
        drop(busy);
        let permit = Permit(Arc::clone(&self.0));
        let (reply, result) = mpsc::sync_channel(1);
        std::thread::Builder::new()
            .name("lemma-vault-read".into())
            .spawn(move || {
                let _permit = permit;
                // Native Keychain calls cannot be cancelled. Keep this permit
                // until the call returns, even if its caller has timed out.
                let _ = reply.send(read());
            })?;
        result
            .recv_timeout(deadline.saturating_duration_since(Instant::now()))
            .map_err(|error| match error {
                mpsc::RecvTimeoutError::Timeout => timed_out(),
                mpsc::RecvTimeoutError::Disconnected => unavailable(),
            })?
    }
}

fn timed_out() -> io::Error {
    io::Error::new(
        io::ErrorKind::TimedOut,
        "The operating-system credential store has not answered. Unlock it and complete any \
         credential-access prompt, then try again. Your saved credentials and local data are \
         preserved. If no prompt appears, quit and reopen Lemma.",
    )
}

fn unavailable() -> io::Error {
    io::Error::other("The credential reader stopped unexpectedly. Quit and reopen Lemma to retry.")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn blocked_read_times_out_and_retry_does_not_spawn_another_native_call() {
        let reads = VaultReads::default();
        let (release, wait) = mpsc::channel();
        let (finished, done) = mpsc::channel();
        let result = reads.read(Duration::from_millis(50), move || {
            let _ = wait.recv();
            let _ = finished.send(());
            Ok(Some("late credential".into()))
        });
        assert_eq!(result.unwrap_err().kind(), io::ErrorKind::TimedOut);
        let result = reads.read(Duration::from_millis(20), || {
            panic!("overlapping native read")
        });
        assert_eq!(result.unwrap_err().kind(), io::ErrorKind::TimedOut);
        release.send(()).unwrap();
        done.recv_timeout(Duration::from_secs(1)).unwrap();
        assert_eq!(
            reads
                .read(Duration::from_secs(1), || Ok(Some(
                    "current credential".into()
                )))
                .unwrap(),
            Some("current credential".into())
        );
    }

    #[test]
    fn missing_and_failed_reads_are_distinct_and_release_the_gate() {
        let reads = VaultReads::default();
        assert_eq!(
            reads.read(Duration::from_secs(1), || Ok(None)).unwrap(),
            None
        );
        let failure = reads.read(Duration::from_secs(1), || {
            Err(io::Error::from(io::ErrorKind::PermissionDenied))
        });
        assert_eq!(failure.unwrap_err().kind(), io::ErrorKind::PermissionDenied);
        assert_eq!(
            reads.read(Duration::from_secs(1), || Ok(None)).unwrap(),
            None
        );
    }

    #[test]
    fn expired_read_budget_never_starts_the_native_call() {
        let failure = VaultReads::default().read(Duration::ZERO, || panic!("expired read started"));
        assert_eq!(failure.unwrap_err().kind(), io::ErrorKind::TimedOut);
    }
}
