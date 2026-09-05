//! Shutdown work never runs on the caller's UI thread and admits one exit only.
use std::sync::{
    atomic::{AtomicBool, Ordering},
    mpsc, Arc,
};
use std::time::Duration;

#[derive(Default)]
pub struct Shutdown {
    started: AtomicBool,
}
impl Shutdown {
    pub fn start(
        &self,
        work: impl FnOnce() + Send + 'static,
        exit: impl Fn() + Send + Sync + 'static,
        budget: Duration,
    ) -> bool {
        if self.started.swap(true, Ordering::AcqRel) {
            return false;
        }
        let exit = Arc::new(exit);
        let exited = Arc::new(AtomicBool::new(false));
        let (finished, waiting) = mpsc::sync_channel(1);
        let worker_exit = Arc::clone(&exit);
        let worker_exited = Arc::clone(&exited);
        std::thread::spawn(move || {
            work();
            if !worker_exited.swap(true, Ordering::AcqRel) {
                worker_exit();
            }
            let _ = finished.send(());
        });
        std::thread::spawn(move || {
            let _ = waiting.recv_timeout(budget);
            if !exited.swap(true, Ordering::AcqRel) {
                exit();
            }
        });
        true
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn a_blocked_cleanup_does_not_block_the_ui_or_start_twice() {
        let state = Shutdown::default();
        let (release, wait) = mpsc::channel();
        let (exited, result) = mpsc::channel();
        let caller = std::thread::current().id();
        assert!(state.start(
            move || {
                assert_ne!(caller, std::thread::current().id());
                let _ = wait.recv();
            },
            move || {
                let _ = exited.send(());
            },
            Duration::from_millis(20)
        ));
        assert!(!state.start(
            || panic!("duplicate shutdown"),
            || panic!("duplicate exit"),
            Duration::ZERO
        ));
        result.recv_timeout(Duration::from_secs(1)).unwrap();
        release.send(()).unwrap();
        assert!(result.recv_timeout(Duration::from_millis(50)).is_err());
    }
    #[test]
    fn completed_cleanup_exits_without_waiting_out_the_budget() {
        let (exit, result) = mpsc::channel();
        Shutdown::default().start(
            || {},
            move || {
                let _ = exit.send(());
            },
            Duration::from_secs(60),
        );
        result.recv_timeout(Duration::from_secs(1)).unwrap();
        assert!(result.recv_timeout(Duration::from_millis(50)).is_err());
    }
}
