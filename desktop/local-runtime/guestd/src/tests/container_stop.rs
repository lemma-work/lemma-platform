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

/// Every `stop` takes a while; everything else answers at once.
struct SlowStopEngine;

impl Engine for SlowStopEngine {
    fn run(&self, arguments: &[String]) -> Result<Output, String> {
        let answer = match (arguments[0].as_str(), arguments.len()) {
            // `ps --quiet`: three core containers, none of them a sandbox.
            ("ps", 2) => "aaaa11112222\nbbbb33334444\ncccc55556666\n",
            ("stop", _) => {
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
    let service = service_over(SlowStopEngine, root.path());

    let started = Instant::now();
    let stopped = service.stop_all_containers().unwrap();
    let elapsed = started.elapsed();

    assert_eq!(stopped.core, 3);
    assert!(
        elapsed < SLOW_STOP * 2,
        "the core stops ran one after another: {elapsed:?}"
    );
    assert!(
        stopped.core_ms >= u64::try_from(SLOW_STOP.as_millis()).unwrap(),
        "the reported core time is the time actually spent"
    );
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
