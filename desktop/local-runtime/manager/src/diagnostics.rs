//! What to collect when the guest will not come up.

use super::*;

/// Lines that mean "still booting", not "went wrong".
///
/// The host dials the guest's control socket before guestd is listening, so a
/// normal boot always writes several of these. They are the *first* thing in
/// `vz.log`, which is why quoting the first line reported a healthy boot's retry
/// as the cause of an exit that happened minutes later.
#[cfg(any(target_os = "macos", test))]
pub(crate) fn is_boot_retry(line: &str) -> bool {
    line.contains("guest connect failed")
}

pub(crate) fn first_diagnostic(value: &[u8], fallback: &str) -> String {
    let value = String::from_utf8_lossy(value);
    let diagnostic = value
        .lines()
        .map(str::trim)
        .find(|line| !line.is_empty())
        .unwrap_or(fallback);
    diagnostic
        .strip_prefix("lemma-runtime: ")
        .unwrap_or(diagnostic)
        .to_owned()
}

/// Why the runtime most recently complained.
///
/// An exit is explained by what the log said last, not first. Boot retries are
/// skipped entirely: if they are all there is, the log holds no explanation and
/// saying so is more honest than quoting one.
#[cfg(any(target_os = "macos", test))]
pub(crate) fn last_diagnostic(value: &[u8], fallback: &str) -> String {
    let value = String::from_utf8_lossy(value);
    let diagnostic = value
        .lines()
        .map(str::trim)
        .rfind(|line| !line.is_empty() && !is_boot_retry(line))
        .unwrap_or(fallback);
    diagnostic
        .strip_prefix("lemma-runtime: ")
        .unwrap_or(diagnostic)
        .to_owned()
}

#[cfg(any(windows, test))]
pub(crate) const GUEST_DIAGNOSTICS: &str = include_str!("../../guest-diagnostics.sh");

impl ManagedRuntime {
    pub fn capture_diagnostics(&self) -> io::Result<()> {
        #[cfg(target_os = "macos")]
        {
            // The VZ serial console is continuously appended by the helper,
            // and for a guest that never reached userspace it is the only
            // record there is. It was also the *only* record macOS ever had:
            // everything the Windows arm below collects -- addresses, routes,
            // listening sockets, containers, the guest's own service logs --
            // was written nowhere at all, so a macOS start that failed with
            // the guest up and its services half-started left a boot log and
            // nothing else. That is the failure people actually hit.
            //
            // A VZ guest has no exec channel, so the collection cannot be
            // driven from here the way `wsl.exe --exec` drives it on Windows.
            // The guest runs the same script itself -- literally the same
            // file, compiled into `lemma-guestd` -- and hands back the text.
            //
            // Allowing failure on purpose, exactly as the Windows arm does:
            // this runs *because* something has already gone wrong, so the
            // guest is often too broken to answer, and a guest that cannot
            // answer still leaves the serial console. Failing here would add
            // an error about collecting errors.
            let Ok(result) = self.request("diagnostics.guest", json!({})) else {
                return Ok(());
            };
            let text = result.get("text").and_then(Value::as_str).unwrap_or("");
            if text.is_empty() {
                return Ok(());
            }
            self.append_guest_log(text.as_bytes())
        }
        #[cfg(windows)]
        {
            // Allowing failure on purpose. This runs *because* something has
            // already gone wrong, so the guest is often exactly the kind of
            // half-up that makes a collector exit non-zero -- and whatever it
            // managed to print before giving up is the reason anyone asked for
            // diagnostics. Failing here would throw away the evidence.
            let output = self.wsl_allowing_failure(
                &[
                    "--distribution",
                    self.wsl_distribution(),
                    "--user",
                    "root",
                    "--exec",
                    "/bin/sh",
                    "-c",
                    GUEST_DIAGNOSTICS,
                ],
                None,
            )?;
            self.append_guest_log(&output.stdout)
        }
        #[cfg(not(any(target_os = "macos", windows)))]
        {
            Ok(())
        }
    }

    /// Append one capture to `logs/guest.log`, keeping the tail of it.
    ///
    /// The tail rather than the head: a collector that ran long enough to
    /// produce more than this wrote the service logs last, and those are what
    /// says why the start failed.
    #[cfg(any(target_os = "macos", windows))]
    pub(crate) fn append_guest_log(&self, captured: &[u8]) -> io::Result<()> {
        let log_path = self.config.local_root.join("logs/guest.log");
        rotate_log(&log_path, 5 * 1024 * 1024)?;
        let mut log = private_appending_log(&log_path)?;
        let start = captured.len().saturating_sub(128 * 1024);
        log.write_all(&captured[start..])?;
        log.write_all(b"\n")?;
        Ok(())
    }
}
