//! The guest clock, which drifts across a host suspend.

use super::*;

impl ManagedRuntime {
    /// Put the guest's wall clock back on this machine's.
    ///
    /// The guest sets its time once, at boot, from the trusted control share.
    /// A Virtualization.framework VM does not run while the Mac sleeps, so
    /// every hour the lid is closed is an hour the guest clock falls behind and
    /// never makes up. Callers run this on a cadence and after a detected
    /// sleep; the guest reports the gap it found, so a correction worth knowing
    /// about can be logged.
    ///
    /// The control-share file is rewritten too. It is what the *next* boot
    /// reads, and leaving it on the epoch of the install would hand a freshly
    /// booted guest a clock that is already stale.
    pub fn sync_clock(&self) -> io::Result<Value> {
        #[cfg(target_os = "macos")]
        self.refresh_host_epoch()?;
        let epoch = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|error| io::Error::other(format!("host clock is invalid: {error}")))?
            .as_secs();
        self.request("system.clock", json!({"epoch": epoch}))
    }

    #[cfg(target_os = "macos")]
    pub(crate) fn refresh_host_epoch(&self) -> io::Result<()> {
        let epoch = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|error| io::Error::other(format!("host clock is invalid: {error}")))?
            .as_secs();
        write_private_atomic(&self.host_epoch_file, format!("{epoch}\n").as_bytes())
    }
}
