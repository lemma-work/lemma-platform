//! Ending the daemon because the OS asked, not because a client did.
use super::*;

impl Daemon {
    /// Stop everything when the OS asks this process to end.
    ///
    /// A logout or shutdown SIGTERMs whatever is left, and the app is not
    /// always there to send `shutdown-daemon` first -- so without this the
    /// default action killed locald outright and left the VM, and Postgres
    /// inside it, to be cut off rather than stopped. This runs the same
    /// shutdown the app's quit does, with nobody on the other end of the reply.
    #[cfg(unix)]
    pub fn stop_on_termination_signals(self: &Arc<Self>) -> io::Result<()> {
        static RECEIVED: AtomicBool = AtomicBool::new(false);
        extern "C" fn note(_: libc::c_int) {
            // Async-signal-safe: one atomic store, nothing else.
            RECEIVED.store(true, Ordering::SeqCst);
        }
        for signal in [libc::SIGTERM, libc::SIGINT, libc::SIGHUP] {
            // SAFETY: installs a handler that only stores to a static atomic.
            let previous = unsafe { libc::signal(signal, note as *const () as libc::sighandler_t) };
            if previous == libc::SIG_ERR {
                return Err(io::Error::last_os_error());
            }
        }
        let daemon = Arc::clone(self);
        thread::spawn(move || loop {
            thread::sleep(std::time::Duration::from_millis(200));
            if RECEIVED.load(Ordering::SeqCst) {
                let _ = daemon.write_daemon_log("termination signal received; stopping");
                daemon.stop_without_client("signal-shutdown");
                return;
            }
        });
        Ok(())
    }

    #[cfg(not(unix))]
    pub fn stop_on_termination_signals(self: &Arc<Self>) -> io::Result<()> {
        Ok(())
    }

    /// `start_daemon_shutdown` with its reply drained, for a stop nobody asked
    /// for over the socket. Only the Unix signal handler has such a stop.
    #[cfg(unix)]
    pub(crate) fn stop_without_client(self: &Arc<Self>, id: &str) {
        let (client, replies) = mpsc::sync_channel::<String>(SUBSCRIBER_BACKLOG);
        thread::spawn(move || for _ in replies {});
        self.start_daemon_shutdown(Some(json!(id)), client);
    }
}
