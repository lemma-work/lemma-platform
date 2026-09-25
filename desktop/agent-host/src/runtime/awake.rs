//! Keeping this computer awake while it runs somebody's agent.
//!
//! A run lives on a lease Lemma renews from this machine's heartbeat. A Mac
//! that goes to sleep stops sending it, and after the lease and the recovery
//! grace Lemma gives the run up -- a person who stepped away from a long task
//! came back to a failure. So while any run is active the host holds a power
//! assertion, and lets it go when the last one ends.
//!
//! Through `caffeinate`, rather than calling `IOKit`: this crate has no `unsafe`,
//! and `caffeinate -w <this process>` ends by itself if the host dies without
//! cleaning up, so an assertion can never outlive the process that wanted it.
//! `-i` keeps an idle Mac awake; `-s` keeps one on power awake with its lid
//! closed. A Mac on battery with its lid closed sleeps anyway -- macOS does
//! not let an application prevent that.

use std::path::PathBuf;

/// The program and its arguments, without the process to wait on.
#[derive(Clone, Debug)]
pub(crate) struct Assertion {
    pub(crate) program: PathBuf,
    pub(crate) arguments: Vec<String>,
}

impl Assertion {
    /// `caffeinate`, where this machine has it.
    pub(crate) fn system() -> Option<Self> {
        let program = PathBuf::from("/usr/bin/caffeinate");
        (cfg!(target_os = "macos") && program.is_file()).then(|| Self {
            program,
            arguments: vec!["-i".to_owned(), "-s".to_owned()],
        })
    }
}

/// Holds the assertion while it is wanted.
pub(crate) struct KeepAwake {
    assertion: Option<Assertion>,
    holder: Option<tokio::process::Child>,
}

impl KeepAwake {
    pub(crate) fn new(assertion: Option<Assertion>) -> Self {
        Self {
            assertion,
            holder: None,
        }
    }

    /// Hold the assertion when `wanted`, release it when not. Cheap to call
    /// on every scan: it does something only when the answer changes, or when
    /// the holder exited on its own.
    pub(crate) fn hold(&mut self, wanted: bool) {
        if let Some(holder) = &mut self.holder
            && !matches!(holder.try_wait(), Ok(None))
        {
            self.holder = None;
        }
        match (wanted, self.holder.is_some()) {
            (true, false) => self.start(),
            (false, true) => self.release(),
            _ => {}
        }
    }

    #[cfg(all(test, unix))]
    pub(crate) fn holding(&self) -> bool {
        self.holder.is_some()
    }

    fn start(&mut self) {
        let Some(assertion) = &self.assertion else {
            return;
        };
        let mut command = tokio::process::Command::new(&assertion.program);
        command
            .args(&assertion.arguments)
            .arg("-w")
            .arg(std::process::id().to_string())
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .kill_on_drop(true);
        match command.spawn() {
            Ok(child) => {
                tracing::info!("keeping this computer awake while runs are active");
                self.holder = Some(child);
            }
            Err(error) => {
                tracing::warn!(%error, "could not keep this computer awake for its runs");
            }
        }
    }

    fn release(&mut self) {
        if let Some(mut holder) = self.holder.take() {
            let _ = holder.start_kill();
            // Reaped in the background; `kill_on_drop` covers a runtime that
            // is going away.
            tokio::spawn(async move {
                let _ = holder.wait().await;
            });
            tracing::info!("no runs are active; this computer may sleep again");
        }
    }
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;

    fn alive(pid: u32) -> bool {
        i32::try_from(pid)
            .ok()
            .and_then(rustix::process::Pid::from_raw)
            .is_some_and(|pid| rustix::process::test_kill_process(pid).is_ok())
    }

    async fn gone(pid: u32) {
        for _ in 0..200 {
            if !alive(pid) {
                return;
            }
            tokio::time::sleep(std::time::Duration::from_millis(10)).await;
        }
        panic!("the assertion outlived being released");
    }

    /// Held once however often it is asked for, and released when the last
    /// run ends.
    #[tokio::test]
    async fn the_assertion_is_held_while_wanted_and_released_after() {
        let mut awake = KeepAwake::new(Some(Assertion {
            program: PathBuf::from("/bin/sh"),
            // `sh -c 'sleep 60' -w <pid>`: the trailing arguments land in $0/$1.
            arguments: vec!["-c".to_owned(), "sleep 60".to_owned()],
        }));
        awake.hold(false);
        assert!(!awake.holding());
        awake.hold(true);
        let pid = awake
            .holder
            .as_ref()
            .and_then(tokio::process::Child::id)
            .unwrap();
        awake.hold(true);
        assert_eq!(
            awake.holder.as_ref().and_then(tokio::process::Child::id),
            Some(pid)
        );
        awake.hold(false);
        assert!(!awake.holding());
        gone(pid).await;
    }

    /// The real one, where there is one: it runs, and it goes.
    #[tokio::test]
    async fn caffeinate_is_started_and_stopped() {
        let Some(assertion) = Assertion::system() else {
            return;
        };
        let mut awake = KeepAwake::new(Some(assertion));
        awake.hold(true);
        let pid = awake
            .holder
            .as_ref()
            .and_then(tokio::process::Child::id)
            .unwrap();
        assert!(alive(pid));
        awake.hold(false);
        gone(pid).await;
    }
}
