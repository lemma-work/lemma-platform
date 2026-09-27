//! Stopping several things at once, and saying how long each took.
//!
//! A quit stops the sharing tunnel, the Agent Host, the backend and frontend,
//! and the private runtime. They were stopped one after another and nothing
//! recorded how long any of them took, so a quit that took seventeen seconds
//! could not say where they went. Here a stop is a list of tiers: every step in
//! a tier runs at the same time, a tier starts only when the one before it has
//! finished, and every step is reported with its duration the moment it ends.

use std::io;
use std::time::{Duration, Instant};

/// One step of a stop, once it has finished.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct StepOutcome {
    pub(crate) step: String,
    pub(crate) duration: Duration,
    /// Why it failed; `None` when it did not.
    pub(crate) error: Option<String>,
    /// What the step itself said about where its time went, if anything.
    pub(crate) detail: Option<String>,
}

impl StepOutcome {
    /// A step timed somewhere else -- inside a component that knows its own
    /// phases -- and reported here like any other.
    pub(crate) fn measured(
        step: impl Into<String>,
        duration: Duration,
        error: Option<String>,
    ) -> Self {
        Self {
            step: step.into(),
            duration,
            error,
            detail: None,
        }
    }

    pub(crate) fn duration_ms(&self) -> u64 {
        u64::try_from(self.duration.as_millis()).unwrap_or(u64::MAX)
    }
}

/// Something to stop, by name.
pub(crate) struct Step<'a> {
    name: &'static str,
    work: Box<dyn FnOnce() -> io::Result<()> + Send + 'a>,
}

impl<'a> Step<'a> {
    pub(crate) fn new(
        name: &'static str,
        work: impl FnOnce() -> io::Result<()> + Send + 'a,
    ) -> Self {
        Self {
            name,
            work: Box::new(work),
        }
    }
}

/// Where each finished step is reported, from whichever thread finished it.
pub(crate) type Report<'r> = &'r (dyn Fn(&StepOutcome) + Sync);

/// Run one tier's steps side by side, returning once the slowest has finished.
///
/// A failed step does not stop its neighbours, and the caller runs the next
/// tier regardless. Everything later still has to be stopped -- the database
/// most of all -- and giving up part-way is how a quit used to leave a VM
/// running with nobody to stop it.
pub(crate) fn run_tier(steps: Vec<Step<'_>>, report: Report<'_>) -> Vec<StepOutcome> {
    if steps.len() <= 1 {
        return steps.into_iter().map(|step| timed(step, report)).collect();
    }
    std::thread::scope(|scope| {
        let workers: Vec<_> = steps
            .into_iter()
            .map(|step| {
                let name = step.name;
                (name, scope.spawn(move || timed(step, report)))
            })
            .collect();
        workers
            .into_iter()
            .map(|(name, worker)| {
                // A step that panicked has still ended, and the plan goes on.
                // The panic is reported rather than re-raised: re-raising here
                // would end the stop before the database had been stopped.
                worker.join().unwrap_or_else(|_| {
                    let outcome = StepOutcome::measured(
                        name,
                        Duration::ZERO,
                        Some(format!("{name} panicked")),
                    );
                    report(&outcome);
                    outcome
                })
            })
            .collect()
    })
}

fn timed(step: Step<'_>, report: Report<'_>) -> StepOutcome {
    let started = Instant::now();
    let error = (step.work)().err().map(|error| error.to_string());
    let outcome = StepOutcome::measured(step.name, started.elapsed(), error);
    report(&outcome);
    outcome
}

/// The first failure, in plan order, as the stop's own error.
pub(crate) fn first_error(outcomes: &[StepOutcome]) -> Option<String> {
    outcomes.iter().find_map(|outcome| {
        outcome
            .error
            .as_ref()
            .map(|error| format!("{}: {error}", outcome.step))
    })
}

/// One line for the daemon log: every step and its milliseconds, in order.
pub(crate) fn summary(outcomes: &[StepOutcome], total: Duration) -> String {
    let steps: Vec<String> = outcomes
        .iter()
        .map(|outcome| {
            let failed = if outcome.error.is_some() {
                " (failed)"
            } else {
                ""
            };
            format!("{} {}ms{failed}", outcome.step, outcome.duration_ms())
        })
        .collect();
    format!(
        "shutdown took {}ms: {}",
        total.as_millis(),
        steps.join(", ")
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;
    use std::thread::sleep;

    const PAUSE: Duration = Duration::from_millis(150);

    /// Tiers in order, the way the daemon's shutdown runs them.
    fn run_tiers(tiers: Vec<Vec<Step<'_>>>, report: Report<'_>) -> Vec<StepOutcome> {
        tiers
            .into_iter()
            .flat_map(|tier| run_tier(tier, report))
            .collect()
    }

    /// What ran, in the order its steps started and ended.
    #[derive(Default)]
    struct Trace(Mutex<Vec<String>>);

    impl Trace {
        fn note(&self, entry: String) {
            self.0.lock().unwrap().push(entry);
        }
        fn entries(&self) -> Vec<String> {
            self.0.lock().unwrap().clone()
        }
    }

    fn pausing<'a>(name: &'static str, trace: &'a Trace) -> Step<'a> {
        Step::new(name, move || {
            trace.note(format!("start {name}"));
            sleep(PAUSE);
            trace.note(format!("end {name}"));
            Ok(())
        })
    }

    /// Steps that each wait, up to a deadline, for all of them to have
    /// started. Only steps that run at the same time can all see that; run in
    /// turn, the first gives up. A proof of overlap that does not depend on
    /// how busy the machine is.
    #[derive(Default)]
    struct Rendezvous {
        arrived: Mutex<usize>,
        all_here: std::sync::Condvar,
    }

    impl Rendezvous {
        fn meet(&self, expected: usize) -> bool {
            let mut arrived = self.arrived.lock().unwrap();
            *arrived += 1;
            self.all_here.notify_all();
            let (arrived, waited) = self
                .all_here
                .wait_timeout_while(arrived, Duration::from_secs(10), |count| *count < expected)
                .unwrap();
            !waited.timed_out() || *arrived >= expected
        }
    }

    fn meeting<'a>(
        name: &'static str,
        trace: &'a Trace,
        rendezvous: &'a Rendezvous,
        met: &'a Mutex<Vec<&'static str>>,
    ) -> Step<'a> {
        Step::new(name, move || {
            trace.note(format!("start {name}"));
            if rendezvous.meet(2) {
                met.lock().unwrap().push(name);
            }
            trace.note(format!("end {name}"));
            Ok(())
        })
    }

    /// Steps in a tier overlap; tiers do not.
    #[test]
    fn a_tier_runs_together_and_waits_for_the_one_before_it() {
        let trace = Trace::default();
        let rendezvous = Rendezvous::default();
        let met = Mutex::new(Vec::new());
        let outcomes = run_tiers(
            vec![
                vec![
                    meeting("agent-host", &trace, &rendezvous, &met),
                    meeting("sharing", &trace, &rendezvous, &met),
                ],
                vec![pausing("host-processes", &trace)],
            ],
            &|_| {},
        );

        let mut met = met.into_inner().unwrap();
        met.sort_unstable();
        assert_eq!(
            met,
            ["agent-host", "sharing"],
            "the first tier ran its steps in turn"
        );
        let entries = trace.entries();
        let position = |entry: &str| entries.iter().position(|seen| seen == entry).unwrap();
        assert!(
            position("start host-processes") > position("end agent-host")
                && position("start host-processes") > position("end sharing"),
            "the backend must not stop before the Agent Host has reported to it: {entries:?}"
        );
        assert_eq!(
            outcomes
                .iter()
                .map(|outcome| outcome.step.as_str())
                .collect::<Vec<_>>(),
            ["agent-host", "sharing", "host-processes"],
            "outcomes come back in plan order, whatever order they finished in"
        );
        assert!(outcomes[2].duration >= PAUSE);
    }

    /// A failure is reported and the rest of the stop still happens.
    #[test]
    fn a_failed_step_does_not_skip_what_comes_after_it() {
        let trace = Trace::default();
        let outcomes = run_tiers(
            vec![
                vec![Step::new("host-processes", || {
                    Err(io::Error::other("backend would not stop"))
                })],
                vec![pausing("runtime", &trace)],
            ],
            &|_| {},
        );

        assert!(trace.entries().contains(&"end runtime".to_owned()));
        assert_eq!(
            first_error(&outcomes).as_deref(),
            Some("host-processes: backend would not stop")
        );
    }

    /// A panicking step ends as a failure, and its neighbours still finish.
    #[test]
    fn a_panicking_step_is_a_failure_not_an_abandoned_stop() {
        let trace = Trace::default();
        let outcomes = run_tier(
            vec![
                Step::new("agent-host", || panic!("poisoned")),
                pausing("sharing", &trace),
            ],
            &|_| {},
        );

        assert_eq!(outcomes[0].error.as_deref(), Some("agent-host panicked"));
        assert!(outcomes[1].error.is_none());
        assert!(trace.entries().contains(&"end sharing".to_owned()));
    }

    /// Each step is reported as it ends, not when the whole stop does.
    #[test]
    fn every_step_is_reported_once_with_its_own_duration() {
        let reported = Mutex::new(Vec::new());
        let trace = Trace::default();
        run_tiers(
            vec![
                vec![Step::new("quick", || Ok(()))],
                vec![pausing("slow", &trace)],
            ],
            &|outcome| reported.lock().unwrap().push(outcome.clone()),
        );

        let reported = reported.into_inner().unwrap();
        assert_eq!(reported.len(), 2);
        assert_eq!(reported[0].step, "quick");
        assert!(reported[0].duration < PAUSE);
        assert!(reported[1].duration >= PAUSE);
    }

    #[test]
    fn the_summary_names_every_step_and_its_time() {
        let outcomes = vec![
            StepOutcome::measured("agent-host", Duration::from_millis(12), None),
            StepOutcome::measured("runtime", Duration::from_millis(1500), Some("vm".into())),
        ];
        assert_eq!(
            summary(&outcomes, Duration::from_millis(1520)),
            "shutdown took 1520ms: agent-host 12ms, runtime 1500ms (failed)"
        );
    }
}
