//! Bringing the stack up, down, and back.

use super::*;

impl HostProcessManager {
    pub fn start_all(&self) -> io::Result<()> {
        self.start_all_with_progress(|_| {})
    }

    pub fn start_all_with_progress(&self, progress: impl FnMut(&str)) -> io::Result<()> {
        self.start_all_cancellable(progress, || Ok(()))
    }

    pub fn start_all_cancellable(
        &self,
        mut progress: impl FnMut(&str),
        checkpoint: impl Fn() -> io::Result<()>,
    ) -> io::Result<()> {
        if self
            .startup_in_progress
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .is_err()
        {
            return Err(io::Error::new(
                io::ErrorKind::WouldBlock,
                "host process startup is already running",
            ));
        }
        let result = self.start_all_inner(&mut progress, &checkpoint);
        if result
            .as_ref()
            .is_err_and(|error| error.kind() == io::ErrorKind::Interrupted)
        {
            let _ = self.stop_all();
        }
        self.startup_in_progress.store(false, Ordering::Release);
        result
    }

    pub fn prepare_runtime_generation(&self) -> io::Result<String> {
        self.inspect_exits();
        // The same question `start_all_inner` asks, asked the same way. These
        // two used to disagree on a *partially* running stack: this one saw
        // "some children" and kept the old generation, while `start_all_inner`
        // saw "not all children" and minted a new one. Every phase, state and
        // ready event then carried the old value while the manager ran the new
        // one -- and the generation is not cosmetic. It is injected into each
        // service at spawn and checked on the next launch to decide whether the
        // recorded workspace is still the one serving, so a mixture of old and
        // new made that check answerable by processes from two different runs.
        let fully_up = self.stack_is_fully_up();
        let mut generation = self
            .runtime_generation
            .lock()
            .expect("runtime generation lock poisoned");
        if !fully_up {
            *generation = random_generation()?;
            self.generation_prepared.store(true, Ordering::Release);
        }
        Ok(generation.clone())
    }

    /// Whether nothing at all is running.
    pub(crate) fn state_is_empty(&self) -> bool {
        self.state
            .lock()
            .expect("host process lock poisoned")
            .children
            .is_empty()
    }

    /// Whether every managed service is currently running.
    ///
    /// Callers must have just called `inspect_exits`, so the child map reflects
    /// processes that have already died.
    pub(crate) fn stack_is_fully_up(&self) -> bool {
        self.state
            .lock()
            .expect("host process lock poisoned")
            .children
            .len()
            == self.ordered_ids.len()
    }

    pub(crate) fn start_all_inner(
        &self,
        progress: &mut dyn FnMut(&str),
        checkpoint: &dyn Fn() -> io::Result<()>,
    ) -> io::Result<()> {
        checkpoint()?;
        self.inspect_exits();
        if self.stack_is_fully_up() {
            self.desired_running.store(true, Ordering::Release);
            // Everything is already up, so this is a reconcile, not a start.
            // `verify_all_health_now` runs the same probes with the same retry
            // budget; what it drops is the per-service stabilization dwell,
            // which is ~2s each, serialized, on every warm launch. That dwell
            // exists to catch a process that dies seconds after it starts
            // listening — a service that has been serving since the last
            // session has already proven it. The cold path below still pays it.
            self.verify_all_health_now()?;
            self.health_ready.store(true, Ordering::Release);
            return Ok(());
        }
        self.health_ready.store(false, Ordering::Release);
        self.desired_running.store(false, Ordering::Release);
        // Survivors of a partial stack are stopped before a new generation is
        // minted. Otherwise `spawn_if_missing` leaves them alone -- they are
        // already running -- while the health gate rewrites its expected body
        // to the new generation, so the live service is rejected as "a
        // different runtime instance" and retried for its whole timeout before
        // the start fails. That is the ordinary recovery path after a backend
        // crash loop: press Start, wait two minutes, get an error that reads
        // like a security failure. Pressing Start again then works, because by
        // then everything is down.
        if !self.state_is_empty() {
            self.stop_all()?;
        }
        if !self.generation_prepared.swap(false, Ordering::AcqRel) {
            *self
                .runtime_generation
                .lock()
                .expect("runtime generation lock poisoned") = random_generation()?;
        }
        {
            let mut state = self.state.lock().expect("host process lock poisoned");
            state.circuit_open.clear();
            // A deliberate start is the one thing that forgives past trips.
            state.circuit_trips.clear();
            state.restart_history.clear();
            state.restart_not_before.clear();
        }

        checkpoint()?;
        progress("migrations");
        self.run_setups()?;
        // A migration finishes with a known outcome before cancellation takes
        // effect. No application process may start after that stopping point.
        checkpoint()?;
        self.desired_running.store(true, Ordering::Release);

        // Spawn first, gate afterwards.
        //
        // This used to spawn a service, wait for it to pass its full health
        // gate, and only then spawn the next — which put the frontend's entire
        // boot after the backend's, for about 2.7s of a 20s cold start that it
        // never needed to wait for. `next start` serves a prebuilt app and does
        // not call the backend to come up; its health check reads a static file
        // it serves itself.
        //
        // Spawning in `ordered_ids` order still honours declared dependencies,
        // and honours them exactly as the supervision loop does: it requires a
        // dependency's *process to exist*, not to be healthy. Readiness is
        // unchanged — every service still passes the same gate in the same
        // order before this returns.
        // A spawn failure is held rather than returned, so that a service which
        // started and then died still gets to report its exit status and log
        // first. Spawning concurrently means both can be true at once, and the
        // process that crashed is nearly always the more useful answer than the
        // one that could not start because of it.
        let mut spawn_failure = None;
        for id in &self.ordered_ids {
            checkpoint()?;
            progress(id);
            self.release_idle_port_for(id);
            if let Err(error) = self.spawn_if_missing(id) {
                spawn_failure.get_or_insert((id.clone(), error));
            }
        }
        // Gate every service at once, not one after another.
        //
        // Each gate requires `stabilization_seconds` of *continuously observed*
        // health, and `healthy_since` is local to the call -- so a serial loop
        // charges that dwell once per service. The frontend is ready in about
        // 0.3s and then sits there healthy while the backend's gate runs, and
        // only afterwards does its own gate start watching and spend a fresh
        // two seconds confirming what was already true. Two services, four
        // seconds, for a guard that needs two.
        //
        // Watching them concurrently costs nothing and weakens nothing: every
        // service still proves the same uninterrupted dwell against the same
        // probe. It just stops the clock starting late on services that came up
        // early. This is the same move as spawning before gating above, applied
        // to the half that was still serial.
        //
        // Results are collected in `ordered_ids` order, so the service reported
        // on a failure does not depend on which thread lost the race.
        let gates: Vec<(String, io::Result<()>)> = thread::scope(|scope| {
            let running: Vec<_> = self
                .ordered_ids
                .iter()
                // Nothing to wait for on a service that never started; waiting
                // would just spend its whole health timeout to say so.
                .filter(|id| {
                    !spawn_failure
                        .as_ref()
                        .is_some_and(|(failed, _)| failed == *id)
                })
                .filter_map(|id| self.health_spec(id).map(|health| (id, health)))
                .map(|(id, health)| {
                    scope.spawn(move || (id.clone(), self.wait_process_health(id, &health)))
                })
                .collect();
            running
                .into_iter()
                .map(|handle| handle.join().expect("health gate thread panicked"))
                .collect()
        });
        for (id, result) in gates {
            if let Err(error) = result {
                let _ = self.stop_all();
                return Err(io::Error::other(format!(
                    "{id} failed health gate: {error}"
                )));
            }
        }
        if let Some((_, error)) = spawn_failure {
            let _ = self.stop_all();
            return Err(error);
        }
        if let Err(error) = self.verify_all_health_now() {
            let _ = self.stop_all();
            return Err(error);
        }
        checkpoint()?;
        self.health_ready.store(true, Ordering::Release);
        Ok(())
    }

    pub(crate) fn verify_all_health_now(&self) -> io::Result<()> {
        for id in &self.ordered_ids {
            if let Some(mut health) = self.health_spec(id) {
                health.stabilization_seconds = 0;
                self.wait_process_health(id, &health).map_err(|error| {
                    io::Error::other(format!("{id} failed final health gate: {error}"))
                })?;
            }
        }
        Ok(())
    }

    /// Whether any component should be reported as having failed.
    ///
    /// `circuit_trips`, not just `circuit_open`. The circuit now closes on its own
    /// after a quiet window, so reading only the live flag would let a service that
    /// trips once per window report healthy in every gap -- oscillating the splash
    /// between "error" and "starting" while nothing actually improved.
    ///
    /// A free function so this is testable without racing a real supervisor: the
    /// interesting state (circuit closed again, trip remembered, service still
    /// down) exists for a fraction of a second in a live manager.
    pub(crate) fn components_report_failure(components: &[HostProcessStatus]) -> bool {
        components
            .iter()
            .any(|component| component.circuit_open || component.circuit_trips > 0)
    }

    pub fn request_stop(&self) {
        self.health_ready.store(false, Ordering::Release);
        self.desired_running.store(false, Ordering::Release);
    }

    pub fn stop_all(&self) -> io::Result<()> {
        self.request_stop();
        let _reconcile = self.reconcile_lock.lock().expect("reconcile lock poisoned");
        let mut first_error = None;
        for id in self.ordered_ids.iter().rev() {
            if let Err(error) = self.stop_process(id) {
                first_error.get_or_insert(error);
            }
        }
        // Re-taking the ports is a courtesy to the next start, not part of
        // stopping, and it routinely cannot be done. A service that has served
        // even one connection leaves TIME_WAIT entries on its port, and a
        // reservation deliberately sets no SO_REUSEADDR, so the bind is
        // refused for a minute or two after the process is gone -- and the
        // backend has always served locald's own health gate.
        //
        // Reported as a stop failure, that turned every Stop into "could not
        // reserve Lemma's local port 53782: Address already in use" from a
        // Stop that had in fact stopped everything. Seen on macOS, from the
        // installed app, on the first stop that followed a real session.
        if first_error.is_none() {
            let _ = self.reserve_idle_ports();
        }
        if let Some(error) = first_error {
            Err(error)
        } else {
            Ok(())
        }
    }

    pub fn restart_all(&self) -> io::Result<()> {
        self.stop_all()?;
        self.start_all()
    }

    pub fn restart_backend(&self) -> io::Result<()> {
        // Suppress crash reconciliation with ownership, not the stop signal:
        // health probes must still run and a real Stop must remain authoritative.
        let _reconcile = self.reconcile_lock.lock().expect("reconcile lock poisoned");
        self.check_running_request()?;
        self.health_ready.store(false, Ordering::Release);
        self.stop_process("backend")?;
        {
            let mut state = self.state.lock().expect("host process lock poisoned");
            state.circuit_open.remove("backend");
            state.circuit_trips.remove("backend");
            state.restart_history.remove("backend");
            state.restart_not_before.remove("backend");
        }
        self.check_running_request()?;
        self.spawn_if_missing("backend")?;
        if let Some(health) = self.health_spec("backend") {
            if let Err(error) = self.wait_process_health("backend", &health) {
                let _ = self.stop_process("backend");
                return Err(io::Error::new(
                    error.kind(),
                    format!("backend failed health gate after configuration: {error}"),
                ));
            }
        }
        self.health_ready.store(true, Ordering::Release);
        Ok(())
    }

    pub(crate) fn check_running_request(&self) -> io::Result<()> {
        if self.desired_running.load(Ordering::Acquire) {
            Ok(())
        } else {
            Err(io::Error::new(
                io::ErrorKind::Interrupted,
                "service health check cancelled by stop",
            ))
        }
    }
}
