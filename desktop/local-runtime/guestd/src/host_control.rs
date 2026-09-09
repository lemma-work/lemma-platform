//! The guest as a machine: its clock, and shutting it down.

use super::*;

/// Step CLOCK_REALTIME to `epoch`.
///
/// `settimeofday(2)` rather than a `date -s` subprocess: guestd already runs as
/// root inside the appliance, and a clock correction that has to fork is one
/// more thing that can fail on a guest whose clock is already wrong.
#[cfg(target_os = "linux")]
pub(crate) fn set_realtime_clock(epoch: u64) -> Result<(), GuestError> {
    let spec = libc::timespec {
        tv_sec: epoch as libc::time_t,
        tv_nsec: 0,
    };
    // SAFETY: `spec` is fully initialised and outlives the call, and
    // CLOCK_REALTIME is settable by root, which is what guestd runs as.
    if unsafe { libc::clock_settime(libc::CLOCK_REALTIME, &spec) } != 0 {
        return Err(GuestError::engine(format!(
            "could not set the guest clock: {}",
            io::Error::last_os_error()
        )));
    }
    Ok(())
}

/// Everywhere else this crate compiles -- the host, for its tests -- there is
/// no guest clock to set, and silently succeeding would let a test pass that
/// proves nothing.
#[cfg(not(target_os = "linux"))]
pub(crate) fn set_realtime_clock(_epoch: u64) -> Result<(), GuestError> {
    Err(GuestError::engine(
        "the guest clock can only be set inside the managed guest",
    ))
}

pub(crate) fn schedule_shutdown() -> Result<Value, GuestError> {
    let output = Command::new("/usr/bin/systemctl")
        .args(["--no-block", "poweroff"])
        .stdin(Stdio::null())
        .output()
        .map_err(|error| {
            GuestError::engine(format!("could not request guest shutdown: {error}"))
        })?;
    if !output.status.success() {
        return Err(GuestError::engine(format!(
            "could not request guest shutdown: {}",
            redact_engine_error(&String::from_utf8_lossy(&output.stderr))
        )));
    }
    Ok(json!({"stopping": true}))
}

impl<E: Engine + 'static> GuestService<E> {
    pub(crate) fn shutdown(&self) -> Result<Value, GuestError> {
        let stopped_containers = self.stop_all_containers()?;
        let mut result = schedule_shutdown()?;
        result["stopped_containers"] = json!(stopped_containers);
        Ok(result)
    }

    /// Put the guest's wall clock back onto the host's.
    ///
    /// The guest takes its time from the host exactly once, at boot, out of the
    /// trusted control share (`lemma-host-clock.service`). Nothing moves it
    /// afterwards -- and a Virtualization.framework VM does not run while the
    /// Mac sleeps. A laptop closed for eleven hours wakes a guest eleven hours
    /// in the past, and it stays there for as long as the VM lives.
    ///
    /// That is not cosmetic. Everything the guest issues that the host then
    /// validates against its own clock is born invalid. The one that is felt:
    /// the auth service runs in here, so an access token it mints carries
    /// `exp = guest_now + 1h`; the backend on the Mac reads that as expired and
    /// answers 401; the browser refreshes; the refresh succeeds, because the
    /// refresh token is checked against the same wrong clock that minted it,
    /// and hands back another token that is also already expired. The app sits
    /// signed in and unable to do anything, indefinitely, and even signing out
    /// fails -- sign-out is an authorized call too.
    pub(crate) fn set_clock(&self, value: Value) -> Result<Value, GuestError> {
        self.set_clock_with(value, SystemTime::now(), set_realtime_clock)
    }

    /// The half worth testing: reading the host epoch, deciding whether the gap
    /// is worth a step, and reporting it. `apply` is the syscall, which only
    /// works inside the guest.
    pub(crate) fn set_clock_with(
        &self,
        value: Value,
        now: SystemTime,
        apply: impl FnOnce(u64) -> Result<(), GuestError>,
    ) -> Result<Value, GuestError> {
        let host_epoch = value
            .get("epoch")
            .and_then(Value::as_u64)
            .ok_or_else(|| GuestError::invalid("`epoch` must be whole seconds since the epoch"))?;
        if !(MIN_TRUSTED_EPOCH..=MAX_TRUSTED_EPOCH).contains(&host_epoch) {
            return Err(GuestError::invalid(format!(
                "host epoch {host_epoch} is outside the supported range"
            )));
        }
        let guest_epoch = now
            .duration_since(UNIX_EPOCH)
            .map_err(|error| GuestError::engine(format!("guest clock is invalid: {error}")))?
            .as_secs();
        let skew_seconds = host_epoch as i64 - guest_epoch as i64;
        let stepped = skew_seconds.abs() >= CLOCK_STEP_THRESHOLD_SECONDS;
        if stepped {
            apply(host_epoch)?;
        }
        Ok(json!({
            "host_epoch": host_epoch,
            "guest_epoch": guest_epoch,
            "skew_seconds": skew_seconds,
            "stepped": stepped,
        }))
    }
}
