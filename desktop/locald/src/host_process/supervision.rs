//! Noticing that something died, and deciding whether to start it again.

use super::*;

impl HostProcessManager {
    pub fn mark_dependency_ready(&self) {
        self.dependency_ready.store(true, Ordering::Release);
        *self
            .dependency_error
            .lock()
            .expect("dependency error lock poisoned") = None;
    }

    pub fn mark_dependency_recovering(&self) {
        self.dependency_ready.store(false, Ordering::Release);
        *self
            .dependency_error
            .lock()
            .expect("dependency error lock poisoned") = None;
    }

    pub fn mark_dependency_unavailable(&self, message: String) {
        self.dependency_ready.store(false, Ordering::Release);
        *self
            .dependency_error
            .lock()
            .expect("dependency error lock poisoned") = Some(message);
    }

    pub(crate) fn inspect_exits(&self) {
        let mut state = self.state.lock().expect("host process lock poisoned");
        let mut exited = Vec::new();
        for (id, managed) in &mut state.children {
            match managed.child.try_wait() {
                Ok(Some(status)) => exited.push((
                    id.clone(),
                    format!(
                        "{status} after {} ms",
                        managed.started_at.elapsed().as_millis()
                    ),
                )),
                Err(error) => exited.push((id.clone(), format!("inspection failed: {error}"))),
                Ok(None) => {}
            }
        }
        if !exited.is_empty() {
            self.health_ready.store(false, Ordering::Release);
        }
        for (id, exit) in exited {
            state.children.remove(&id);
            state.last_exit.insert(id.clone(), exit);
            let _ = self.remove_ledger_entry(&id);
        }
    }

    /// Hold each service's log to its ceiling while the service is running.
    ///
    /// `process_log` rotates when it opens the file, which is once, at spawn.
    /// The handle it returns then *is* the child's stdout and stderr for the
    /// whole life of the process -- so a backend that stays up for days was
    /// never checked again. One install was found with a 16 MB `backend.log`
    /// still growing at 8 MB an hour, and the only thing that would ever have
    /// truncated it was a restart.
    ///
    /// Rotating a live log is exactly what `rotate_log` was built for: it
    /// copies aside and truncates in place rather than renaming, so the writer
    /// holding the descriptor keeps writing to the same file and simply finds
    /// it back at zero. That property was already relied on at spawn; nothing
    /// was calling it afterwards.
    pub(crate) fn rotate_service_logs(&self) {
        for id in &self.ordered_ids {
            let path = self.log_dir.join(format!("{id}.log"));
            // Best effort, per service. A log that cannot be rotated -- removed
            // out from under us, or briefly locked on Windows -- must not stop
            // the others being rotated, and must not take down the supervisor
            // thread that is also the crash monitor.
            let _ = rotate_log(&path, SERVICE_LOG_MAX_BYTES);
        }
    }

    pub(crate) fn reconcile_crashes(&self) {
        let _reconcile = self.reconcile_lock.lock().expect("reconcile lock poisoned");
        self.inspect_exits();
        if !self.desired_running.load(Ordering::Acquire)
            || self.startup_in_progress.load(Ordering::Acquire)
        {
            return;
        }

        for id in &self.ordered_ids {
            let spec = &self.by_id[id];
            let ready_to_spawn = {
                let mut state = self.state.lock().expect("host process lock poisoned");
                let now = Instant::now();
                let window = Duration::from_secs(spec.restart.window_seconds);
                // Prune *before* consulting the circuit, not after. The old
                // order tested `circuit_open` first, so once a service tripped,
                // its history was never trimmed again and no amount of elapsed
                // time could reopen it -- a single transient burst (a laptop
                // waking with the VM's forwarders not yet up) condemned the
                // service until the user restarted the whole app, and nothing on
                // screen said that was the remedy.
                //
                // Closing needs a full quiet window, so this is a real cooldown
                // rather than an unconditional reset.
                {
                    let history = state.restart_history.entry(id.clone()).or_default();
                    while history
                        .front()
                        .is_some_and(|started| now.duration_since(*started) > window)
                    {
                        history.pop_front();
                    }
                    if history.len() < spec.restart.max_restarts {
                        state.circuit_open.remove(id);
                    }
                }
                if state.children.contains_key(id)
                    || state.circuit_open.contains(id)
                    || spec
                        .dependencies
                        .iter()
                        .any(|dependency| !state.children.contains_key(dependency))
                {
                    false
                } else if let Some(deadline) = state.restart_not_before.get(id) {
                    *deadline <= now
                } else {
                    // Length first, so the borrow of `restart_history` is over
                    // before `circuit_open` and `circuit_trips` are touched.
                    let attempts = state.restart_history.entry(id.clone()).or_default().len();
                    if attempts >= spec.restart.max_restarts {
                        state.circuit_open.insert(id.clone());
                        *state.circuit_trips.entry(id.clone()).or_insert(0) += 1;
                    } else {
                        state
                            .restart_history
                            .entry(id.clone())
                            .or_default()
                            .push_back(now);
                        state.restart_not_before.insert(
                            id.clone(),
                            now + Duration::from_secs(spec.restart.backoff_seconds),
                        );
                    }
                    false
                }
            };
            if ready_to_spawn {
                let result = self.spawn_if_missing(id);
                let result = result.and_then(|_| {
                    if let Some(health) = self.health_spec(id) {
                        if let Err(error) = self.wait_process_health(id, &health) {
                            let _ = self.stop_process(id);
                            return Err(error);
                        }
                    }
                    Ok(())
                });
                let mut state = self.state.lock().expect("host process lock poisoned");
                state.restart_not_before.remove(id);
                if let Err(error) = result {
                    state
                        .last_exit
                        .insert(id.clone(), format!("restart failed: {error}"));
                } else if state.children.len() == self.ordered_ids.len()
                    && self.dependency_ready.load(Ordering::Acquire)
                {
                    self.health_ready.store(true, Ordering::Release);
                }
            }
        }
    }

    pub(crate) fn wait_process_health(&self, id: &str, spec: &HttpHealthSpec) -> io::Result<()> {
        let deadline = Instant::now() + Duration::from_secs(spec.timeout_seconds);
        let stabilization = Duration::from_secs(spec.stabilization_seconds);
        let mut healthy_since = None;
        let mut last_error = None;
        while Instant::now() < deadline {
            self.check_running_request()?;
            if let Some(exit) = self.take_process_exit(id)? {
                let excerpt = tail_log(&self.log_dir.join(format!("{id}.log")), 8 * 1024);
                let suffix = excerpt
                    .filter(|value| !value.trim().is_empty())
                    .map(|value| format!("; recent log:\n{}", self.redact_excerpt(value)))
                    .unwrap_or_default();
                return Err(io::Error::other(format!(
                    "process exited with {exit}{suffix}"
                )));
            }
            match probe_http(spec) {
                Ok(_) => {
                    let since = healthy_since.get_or_insert_with(Instant::now);
                    if since.elapsed() >= stabilization {
                        return Ok(());
                    }
                }
                Err(error) => {
                    healthy_since = None;
                    last_error = Some(error);
                }
            }
            thread::sleep(Duration::from_millis(250));
        }
        Err(last_error.unwrap_or_else(|| io::Error::new(io::ErrorKind::TimedOut, "health timeout")))
    }

    pub(crate) fn take_process_exit(&self, id: &str) -> io::Result<Option<String>> {
        let mut state = self.state.lock().expect("host process lock poisoned");
        let Some(managed) = state.children.get_mut(id) else {
            return Ok(Some("process is not running".into()));
        };
        let Some(status) = managed.child.try_wait()? else {
            return Ok(None);
        };
        let exit = format!(
            "{status} after {} ms",
            managed.started_at.elapsed().as_millis()
        );
        state.children.remove(id);
        state.last_exit.insert(id.to_owned(), exit.clone());
        self.health_ready.store(false, Ordering::Release);
        Ok(Some(exit))
    }
}
