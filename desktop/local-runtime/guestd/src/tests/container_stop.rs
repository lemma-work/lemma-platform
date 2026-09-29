//! How a guest stop spends its time: which container gets which grace, and
//! what runs at once.

use super::*;

fn service_over<E: Engine + 'static>(engine: E, root: &Path) -> GuestService<E> {
    GuestService::new(
        engine,
        root.into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap()
}

fn stops_in(service: &GuestService<FakeEngine>) -> Vec<Vec<String>> {
    service
        .engine
        .commands()
        .into_iter()
        .filter(|command| command.first().map(String::as_str) == Some("stop"))
        .collect()
}

fn stop(grace: u32, id: &str) -> Vec<String> {
    vec!["stop".into(), "--time".into(), grace.to_string(), id.into()]
}

/// SuperTokens gets a second, not the database's fifteen.
///
/// It never answers `SIGTERM` -- its PID 1 waits on a second JVM the signal
/// never reaches -- so whatever grace it is given is spent in full. It was
/// given the data services' fifteen seconds, which was most of every quit.
#[test]
fn a_stateless_service_is_stopped_briefly_beside_the_databases() {
    let root = tempdir().unwrap();
    let service = service_over(
        FakeEngine::new(vec![
            output(true, "aaaa11112222\nbbbb33334444\ncccc55556666\n"),
            output(true, ""),
            output(true, "cccc55556666\n"),
            output(true, ""),
            output(true, ""),
            output(true, ""),
        ]),
        root.path(),
    );

    let stopped = service.stop_all_containers().unwrap();

    assert_eq!((stopped.sandboxes, stopped.core), (0, 3));
    let name_query = service
        .engine
        .commands()
        .into_iter()
        .find(|command| command.iter().any(|argument| argument.starts_with("name=")))
        .expect("the stateless services are asked for by name");
    assert_eq!(
        name_query.last().map(String::as_str),
        Some("name=^lemma-core-supertokens$"),
        "anchored, so a container whose name merely contains it is not caught"
    );
    let mut stops = stops_in(&service);
    stops.sort();
    assert_eq!(
        stops,
        [
            // Sorted: "1" before "15".
            stop(STATELESS_STOP_GRACE_SECONDS, "cccc55556666"),
            stop(CORE_STOP_GRACE_SECONDS, "aaaa11112222"),
            stop(CORE_STOP_GRACE_SECONDS, "bbbb33334444"),
        ],
        "one stop per container, each with the grace its contents need",
    );
}

/// An engine that cannot say which container is SuperTokens costs time, not
/// safety: every core container keeps the longer grace.
#[test]
fn a_failed_name_lookup_leaves_every_core_container_on_the_long_grace() {
    let root = tempdir().unwrap();
    let service = service_over(
        FakeEngine::new(vec![
            output(true, "aaaa11112222\ncccc55556666\n"),
            output(true, ""),
            output(false, ""),
            output(true, ""),
            output(true, ""),
        ]),
        root.path(),
    );

    service.stop_all_containers().unwrap();

    let stops = stops_in(&service);
    assert_eq!(stops.len(), 2);
    assert!(stops
        .iter()
        .all(|command| command[2] == CORE_STOP_GRACE_SECONDS.to_string()));
}

/// Only an id the engine named as stateless is shortened.
#[test]
fn the_stop_plan_shortens_only_what_was_named() {
    let core = ["postgres1".to_owned(), "tokens1".to_owned()];
    assert_eq!(
        core_stop_plan(&core, &["tokens1".to_owned()]),
        [
            ("postgres1".to_owned(), CORE_STOP_GRACE_SECONDS),
            ("tokens1".to_owned(), STATELESS_STOP_GRACE_SECONDS),
        ]
    );
    assert!(core_stop_plan(&core, &[])
        .iter()
        .all(|(_, grace)| *grace == CORE_STOP_GRACE_SECONDS));
}

const SLOW_STOP: Duration = Duration::from_millis(300);

/// Every `stop` waits, up to a deadline, until all three have been asked;
/// everything else answers at once. Only stops issued side by side can all
/// see the others arrive -- one after another, the first gives up -- so the
/// overlap is proven without depending on how busy the machine is.
#[derive(Default)]
struct RendezvousStopEngine {
    arrived: Mutex<usize>,
    all_here: std::sync::Condvar,
    met: Mutex<usize>,
}

impl Engine for RendezvousStopEngine {
    fn run(&self, arguments: &[String]) -> Result<Output, String> {
        let answer = match (arguments[0].as_str(), arguments.len()) {
            // `ps --quiet`: three core containers, none of them a sandbox.
            ("ps", 2) => "aaaa11112222\nbbbb33334444\ncccc55556666\n",
            ("stop", _) => {
                let mut arrived = self.arrived.lock().unwrap();
                *arrived += 1;
                self.all_here.notify_all();
                let (arrived, _) = self
                    .all_here
                    .wait_timeout_while(arrived, Duration::from_secs(10), |count| *count < 3)
                    .unwrap();
                if *arrived >= 3 {
                    *self.met.lock().unwrap() += 1;
                }
                drop(arrived);
                std::thread::sleep(SLOW_STOP);
                ""
            }
            _ => "",
        };
        Ok(output(true, answer))
    }
}

/// Stopped side by side, the core costs its slowest member rather than the sum.
///
/// One `nerdctl stop` over several ids works through them in turn, so three
/// data services cost three graces.
#[test]
fn the_core_containers_are_stopped_at_the_same_time() {
    let root = tempdir().unwrap();
    let engine = std::sync::Arc::new(RendezvousStopEngine::default());
    let service = service_over(SharedEngine(std::sync::Arc::clone(&engine)), root.path());

    let stopped = service.stop_all_containers().unwrap();

    assert_eq!(stopped.core, 3);
    assert_eq!(
        *engine.met.lock().unwrap(),
        3,
        "the core stops ran one after another"
    );
    assert!(
        stopped.core_ms >= u64::try_from(SLOW_STOP.as_millis()).unwrap(),
        "the reported core time is the time actually spent"
    );
}

/// An engine the test keeps a handle on, to read what it saw afterwards.
struct SharedEngine<E>(std::sync::Arc<E>);

impl<E: Engine> Engine for SharedEngine<E> {
    fn run(&self, arguments: &[String]) -> Result<Output, String> {
        self.0.run(arguments)
    }
}

/// A failed stop is reported, but only after every other container was asked.
#[test]
fn one_failed_stop_does_not_leave_the_others_running() {
    let root = tempdir().unwrap();
    let service = service_over(
        FakeEngine::new(vec![
            output(true, "aaaa11112222\nbbbb33334444\n"),
            output(true, ""),
            output(true, ""),
            output(false, ""),
            output(true, ""),
        ]),
        root.path(),
    );

    assert!(service.stop_all_containers().is_err());
    assert_eq!(
        stops_in(&service).len(),
        2,
        "both core containers were asked"
    );
}
