//! Whether the sidecar should be running, and making that true.

use super::*;

impl AgentHostSupervisor {
    /// Whether the user wants the Agent Host running, across daemon restarts.
    pub fn desired_running(&self) -> bool {
        self.state
            .lock()
            .expect("Agent Host state lock poisoned")
            .desired_running
    }

    pub fn start(&self) -> io::Result<()> {
        let _transition = self
            .transition
            .lock()
            .expect("Agent Host transition lock poisoned");
        {
            let mut state = self.state.lock().expect("Agent Host state lock poisoned");
            state.desired_running = true;
            // A deliberate start forgives the past, the way `start_all` does
            // for the host processes. Somebody pressing the button has, in
            // effect, said the cause is fixed.
            state.circuit_open = false;
            state.window_restarts = 0;
            state.window_started = Instant::now();
            state.next_restart = Instant::now();
            if child_running(&mut state) {
                return Ok(());
            }
        }
        self.spawn()
    }

    /// Stop the process and stop wanting it back, for this daemon's lifetime.
    ///
    /// Nothing is written down: the next daemon derives what it wants from
    /// whether this machine is paired, so "stopped" never outlives the process
    /// that decided it.
    pub fn stop(&self) -> io::Result<()> {
        self.halt(false)
    }

    /// Stop the process while still wanting it back.
    ///
    /// The Agent Host runs while the app is open, so quitting has to stop it —
    /// but quitting is not a decision about the Agent Host, and it must come
    /// back on the next launch.
    pub fn suspend(&self) -> io::Result<()> {
        self.halt(true)
    }

    pub(crate) fn halt(&self, keep_desire: bool) -> io::Result<()> {
        // The transition lock, not the state lock, is what makes a stop and a
        // start exclusive. The child comes out under `state`; it dies outside
        // it, because dying takes up to eleven seconds and `status` -- which
        // the shell polls to draw the tray -- takes the same `state`.
        let _transition = self
            .transition
            .lock()
            .expect("Agent Host transition lock poisoned");
        let mut child = {
            let mut state = self.state.lock().expect("Agent Host state lock poisoned");
            if !keep_desire {
                state.desired_running = false;
            }
            state.started_at = None;
            state.started_at_ms = None;
            state.child.take()
        };

        let exit = child.as_mut().map(terminate_process_tree).transpose();

        {
            let mut state = self.state.lock().expect("Agent Host state lock poisoned");
            if let Ok(Some(code)) = exit {
                state.last_exit_code = code;
            }
        }
        // Stopped on purpose is not a leftover: clear the record whether or
        // not there was a child, so a stale one cannot outlive the thing it
        // describes and get some later daemon to kill an innocent pid.
        self.forget_running();
        self.invalidate_details();
        exit.map(|_| ())
    }

    pub fn restart(&self) -> io::Result<()> {
        self.stop()?;
        self.start()
    }

    pub fn reconcile(&self) -> io::Result<()> {
        // A missing sidecar cannot become available without restarting locald,
        // and `status()` already reports it, so retrying every tick would only
        // grow the daemon log without ever recovering.
        if self.executable.is_none() {
            return Ok(());
        }
        // Taken before `state`, and before the decision to spawn: a tick that
        // decided to restart while a stop was still terminating the old child
        // would put a second one on top of it.
        let _transition = self
            .transition
            .lock()
            .expect("Agent Host transition lock poisoned");
        // A healthy Agent Host is spawned once and never again, so rotating
        // only at spawn means it never rotates at all. This tick is the only
        // thing that bounds the log of a host that simply keeps running.
        let rotation = rotate_log(&self.log_path);
        let mut state = self.state.lock().expect("Agent Host state lock poisoned");
        if let Err(error) = rotation {
            state.last_error = Some(format!("could not rotate the Agent Host log: {error}"));
        }
        if child_running(&mut state) || !state.desired_running {
            return Ok(());
        }
        if state.next_restart > Instant::now() {
            return Ok(());
        }
        // Prune the window before consulting the circuit, so a quiet stretch
        // reopens it on its own. Doing it the other way round is how
        // `host_process`'s circuit came to be permanently latched.
        let now = Instant::now();
        if now.duration_since(state.window_started) > RESTART_WINDOW {
            state.window_started = now;
            state.window_restarts = 0;
            state.circuit_open = false;
        }
        if state.circuit_open {
            return Ok(());
        }
        if state.window_restarts >= RESTART_BUDGET {
            state.circuit_open = true;
            state.last_error = Some(format!(
                "the Agent Host stopped {RESTART_BUDGET} times in a row;                  not restarting it again. See the Agent Host log"
            ));
            return Ok(());
        }
        state.window_restarts = state.window_restarts.saturating_add(1);
        drop(state);
        self.spawn()
    }

    /// Stop a sidecar this installation started and never got to stop.
    ///
    /// Runs before every spawn rather than once at startup, so it covers the
    /// restart and reconcile paths too, and costs one small file read when
    /// there is nothing to do.
    ///
    /// The identity check is against the *record*, not against today's
    /// discovered executable: what has to be proved is that this pid is the
    /// very process we wrote down -- the guard is against a pid the kernel has
    /// since handed to something else, which `start_identity` (the process
    /// start time) settles. Comparing against today's sidecar path instead
    /// would refuse to reclaim across an update that moved the binary, which is
    /// exactly a case where the leftover has to go.
    pub(crate) fn reclaim_leftover(&self) {
        let Some(installation_id) = self.installation_id.as_deref() else {
            return;
        };
        let Ok(raw) = std::fs::read(&self.record_path) else {
            return;
        };
        let Ok(record) = serde_json::from_slice::<AgentHostRecord>(&raw) else {
            // Unreadable means unusable, and a record naming nothing is worse
            // than none at all: clear it rather than reading it every spawn.
            let _ = std::fs::remove_file(&self.record_path);
            return;
        };
        if record.schema_version != AGENT_HOST_RECORD_SCHEMA_VERSION
            || record.installation_id != installation_id
        {
            return;
        }
        if let Ok(identity) = crate::host_process::process_identity(record.pid) {
            if identity.executable == record.executable
                && identity.start_identity == record.start_identity
            {
                let _ = crate::host_process::terminate_verified_process(record.pid);
            }
        }
        let _ = std::fs::remove_file(&self.record_path);
    }

    /// Write down what is running, so the next daemon can find it.
    ///
    /// Failing to record is not failing to start. The sidecar is up either
    /// way; all that is lost is the next daemon's ability to reclaim it, which
    /// is where this started.
    ///
    /// `settled_process_identity` rather than a bare `process_identity`, and
    /// for the reason this record exists at all: a child that has forked but
    /// not yet finished `exec` still reports its parent's binary, so recording
    /// the moment `spawn` returns writes down the wrong executable -- and a
    /// record whose executable does not match is one `reclaim_leftover`
    /// declines to signal. The window is small and entirely load-dependent,
    /// which is the worst size for it to be.
    pub(crate) fn record_running(&self, child: &mut Child) {
        let Some(installation_id) = self.installation_id.clone() else {
            return;
        };
        let Ok(identity) = crate::host_process::settled_process_identity(child) else {
            return;
        };
        let record = AgentHostRecord {
            schema_version: AGENT_HOST_RECORD_SCHEMA_VERSION,
            installation_id,
            pid: child.id(),
            executable: identity.executable,
            start_identity: identity.start_identity,
        };
        if let Ok(encoded) = serde_json::to_vec(&record) {
            let _ = std::fs::write(&self.record_path, encoded);
        }
    }

    pub(crate) fn forget_running(&self) {
        let _ = std::fs::remove_file(&self.record_path);
    }

    /// Start the sidecar. The caller holds `transition`; this takes `state`
    /// only to record what happened.
    pub(crate) fn spawn(&self) -> io::Result<()> {
        // Before taking the lock for ourselves, give up any we already hold.
        // A sidecar left over from a daemon that did not live to stop it holds
        // that lock against every future launch, and the new one can do
        // nothing but log and retry.
        self.reclaim_leftover();
        // Every failure arms the backoff, not just a failed `spawn`: an
        // unwritable data directory would otherwise be retried every tick.
        match self.spawn_process() {
            Ok(mut child) => {
                self.record_running(&mut child);
                let mut state = self.state.lock().expect("Agent Host state lock poisoned");
                state.child = Some(child);
                state.restart_count = state.restart_count.saturating_add(1);
                state.started_at = Some(Instant::now());
                state.started_at_ms = Some(now_ms());
                state.next_restart = Instant::now() + RESTART_BACKOFF;
                state.last_error = None;
                state.last_exit_code = None;
                Ok(())
            }
            Err(error) => {
                let mut state = self.state.lock().expect("Agent Host state lock poisoned");
                state.last_error = Some(error.to_string());
                state.next_restart = Instant::now() + RESTART_BACKOFF;
                Err(error)
            }
        }
    }

    pub(crate) fn spawn_process(&self) -> io::Result<Child> {
        let executable = self.executable.as_ref().ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::NotFound,
                "lemma-agent-host sidecar is not installed",
            )
        })?;
        std::fs::create_dir_all(&self.data_dir)?;
        rotate_log(&self.log_path)?;
        let stdout = append_log(&self.log_path)?;
        let stderr = stdout.try_clone()?;
        let mut command = Command::new(executable);
        command
            .no_console_window()
            .arg("--data-dir")
            .arg(&self.data_dir)
            .arg("serve")
            .stdin(Stdio::null())
            .stdout(Stdio::from(stdout))
            .stderr(Stdio::from(stderr));
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            // Own process group so `stop` can signal every ACP adapter the host
            // spawned, not just the host itself.
            command.process_group(0);
        }
        command.spawn()
    }
}
