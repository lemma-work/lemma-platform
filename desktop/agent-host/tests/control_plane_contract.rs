//! What the stand-in control plane promises to behave like.
//!
//! Every other test in this crate drives the real host binary against the
//! double in `support`. That only proves anything about the shipped host while
//! the double behaves like the backend it stands in for -- and one place where
//! it did not cost this suite an intermittent 90-second hang across at least
//! four different tests in two files, on branches touching none of them.
//!
//! So the properties the host actually depends on are asserted here, against
//! the double's real link, rather than left implicit in tests that are about
//! something else. The frames are sent with the host's own link client, so a
//! request here is shaped exactly as the host shapes it.

mod support;

use std::time::Duration;

use lemma_agent_host::link::protocol::host;
use lemma_agent_host::link::{self, Connected, LinkError, LinkHandle, Push};
use lemma_agent_host::protocol::{Command, HostCapacity, HostHello};
use serde_json::{Value, json};
use support::{ControlPlane, HOST_SECRET, PermissionAnswer};

const ANSWER: Option<Duration> = Some(Duration::from_secs(10));

/// One harness as the host describes it in a `harnesses` frame.
fn snapshot(harness_key: &str, health: &str, revision: &str) -> Value {
    json!({
        "harness_key": harness_key,
        "display_name": harness_key,
        "adapter_version": "1.0.0",
        "upstream_version": null,
        "health": health,
        "capabilities": {},
        "config_revision": revision,
        "config_options": [],
        "stale_after": "2099-01-01T00:00:00Z",
        "stale_reason": null,
    })
}

async fn control_plane() -> ControlPlane {
    ControlPlane::start(
        "cursor",
        "unused",
        json!({"server_name": "lemma_tools"}),
        PermissionAnswer::Ignore,
    )
    .await
}

async fn connect(control: &ControlPlane) -> Connected {
    link::connect(
        &control.base_url,
        HOST_SECRET,
        HostHello::current("contract-test"),
        HostCapacity {
            max_runs: 1,
            active_runs: 0,
            available_runs: 1,
        },
    )
    .await
    .unwrap()
}

async fn publish(link: &LinkHandle, harnesses: Vec<Value>) -> Value {
    link.request(host::HARNESSES, &json!({ "harnesses": harnesses }), ANSWER)
        .await
        .unwrap()
}

async fn send_control(link: &LinkHandle, body: Value) -> Value {
    let mut body = body;
    body["capacity"] = json!({"max_runs": 1, "active_runs": 0, "available_runs": 1});
    link.request(host::CONTROL, &body, ANSWER).await.unwrap()
}

/// The next `commands` push, if one arrives within `within`.
async fn next_push(connected: &mut Connected, within: Duration) -> Option<Vec<Command>> {
    let deadline = tokio::time::Instant::now() + within;
    loop {
        match tokio::time::timeout_at(deadline, connected.pushes.recv()).await {
            Ok(Some(Push::Commands(commands))) if !commands.is_empty() => return Some(commands),
            Ok(Some(_)) => {}
            Ok(None) | Err(_) => return None,
        }
    }
}

/// The host no longer polls, so work reaches it only if Lemma pushes it.
///
/// A double that merely answered `control` frames would still pass most of
/// the suite -- the host sends one every heartbeat -- but twenty seconds at a
/// time, which is exactly the latency the link was built to remove, and every
/// flow test would be measuring the heartbeat instead of the host.
#[tokio::test]
async fn work_is_pushed_without_being_asked_for() {
    let control = control_plane().await;
    let mut connected = connect(&control).await;
    publish(
        &connected.handle,
        vec![snapshot("cursor", "READY", "rev-1")],
    )
    .await;

    let pushed = next_push(&mut connected, Duration::from_secs(5))
        .await
        .expect("a published harness should be offered its run without a control frame");
    assert_eq!(pushed.len(), 1);
    assert_eq!(
        serde_json::to_value(pushed[0].kind).unwrap(),
        "START_RUN",
        "the run is what was owed"
    );
}

#[tokio::test]
async fn redelivery_preserves_the_entire_command_until_acknowledged() {
    let control = control_plane().await;
    let mut connected = connect(&control).await;
    publish(
        &connected.handle,
        vec![snapshot("cursor", "READY", "rev-1")],
    )
    .await;

    // Offered on every answer, and pushed again on its own, until it comes
    // back acknowledged -- and every copy is the same command, not a new one
    // built to look like it.
    let first = send_control(&connected.handle, json!({})).await;
    let second = send_control(&connected.handle, json!({})).await;
    assert_eq!(first["commands"].as_array().unwrap().len(), 1);
    assert_eq!(first["commands"], second["commands"]);
    let pushed = next_push(&mut connected, Duration::from_secs(5))
        .await
        .expect("an unacknowledged run is pushed");
    let again = next_push(&mut connected, Duration::from_secs(5))
        .await
        .expect("and pushed again while it stays unacknowledged");
    assert_eq!(serde_json::to_value(&pushed).unwrap(), first["commands"]);
    assert_eq!(serde_json::to_value(&again).unwrap(), first["commands"]);

    let acknowledged = send_control(
        &connected.handle,
        json!({"acknowledged_command_ids": [first["commands"][0]["command_id"]]}),
    )
    .await;
    assert_eq!(acknowledged["commands"], json!([]));
    // A push chosen before the acknowledgement landed may still be on its way.
    tokio::time::sleep(Duration::from_millis(200)).await;
    while connected.pushes.try_recv().is_ok() {}
    assert!(
        next_push(&mut connected, Duration::from_millis(1_500))
            .await
            .is_none(),
        "an acknowledged command must not be pushed again"
    );
}

fn event(control: &ControlPlane, sequence: u64, text: &str) -> Value {
    json!({
        "run_id": control.run_id, "lease_epoch": 1, "sequence": sequence,
        "type": "agent_message_chunk", "object_id": null, "payload": {"text": text},
    })
}

async fn append(link: &LinkHandle, events: Vec<Value>) -> Result<Value, LinkError> {
    link.request(host::EVENTS, &json!({ "events": events }), ANSWER)
        .await
}

/// An acknowledgement lost after the batch was committed.
///
/// On the link that is a connection that goes between Lemma committing a
/// batch and `events_ok` reaching the host. The host cannot tell that from a
/// batch that never arrived, so it replays from its outbox on the next link --
/// and the replay must not replace the text that was already accepted.
#[tokio::test]
async fn a_lost_append_ack_replays_without_replacing_accepted_text() {
    let control = control_plane().await;
    control.drop_the_link_after_the_first_append();
    let link = connect(&control).await.handle;
    let lost = append(&link, vec![event(&control, 1, "first")]).await;
    assert!(
        lost.is_err(),
        "the answer must be lost with the link: {lost:?}"
    );
    assert_eq!(control.assistant_text(), "first");

    let link = connect(&control).await.handle;
    let retried = append(
        &link,
        vec![
            event(&control, 1, "replacement"),
            event(&control, 2, " second"),
        ],
    )
    .await
    .unwrap();
    assert_eq!(retried["ack"]["acked_through"], 2);
    assert_eq!(control.assistant_text(), "first second");
    assert_eq!(control.events().len(), 2);
}

#[tokio::test]
async fn an_event_gap_rejects_the_whole_batch_before_accepting_text() {
    let control = control_plane().await;
    let link = connect(&control).await.handle;
    let refused = append(
        &link,
        vec![event(&control, 1, "first"), event(&control, 3, "third")],
    )
    .await
    .unwrap_err();
    assert!(
        matches!(
            &refused,
            LinkError::Rejected { code, retryable: false, .. } if code == "SEQUENCE_GAP"
        ),
        "a gap is refused as the backend refuses it: {refused:?}"
    );
    assert!(control.events().is_empty());
    append(
        &link,
        vec![event(&control, 1, "first"), event(&control, 2, "second")],
    )
    .await
    .unwrap();
    assert_eq!(control.events().len(), 2);
}

/// A harness keeps one id, however many times it is published.
///
/// `agent_host_harnesses` is unique on `(host_id, harness_key)`, so the backend
/// upserts and the id is stable for the life of the host. The double used to
/// mint a fresh UUID per snapshot per publish.
///
/// That is not a cosmetic difference. The host builds its harness map from the
/// publish *answer*, so a re-published id is unknown to it until that answer
/// lands. `START_RUN` naming the new id in the meantime is rejected as
/// `HARNESS_NOT_FOUND`, which `command_rejection` marks `retryable: false` --
/// and a command refused that way is never offered again. The run never
/// starts, and the test waits out its whole timeout for a terminal event
/// nobody was going to send.
#[tokio::test]
async fn a_harness_keeps_its_id_across_republishes() {
    let control = control_plane().await;
    let link = connect(&control).await.handle;

    let first = publish(
        &link,
        vec![
            snapshot("cursor", "READY", "rev-1"),
            snapshot("codex", "INSTALLING", "rev-installing"),
        ],
    )
    .await;

    // The second publish is what a finished adapter install produces: one
    // harness changes state, and every harness is sent again.
    let second = publish(
        &link,
        vec![
            snapshot("cursor", "READY", "rev-1"),
            snapshot("codex", "READY", "rev-2"),
        ],
    )
    .await;

    let id_of = |answer: &Value, key: &str| {
        answer["items"]
            .as_array()
            .unwrap()
            .iter()
            .find(|item| item["harness_key"] == key)
            .unwrap_or_else(|| panic!("{key} is missing from the publish answer"))["id"]
            .as_str()
            .unwrap()
            .to_owned()
    };

    assert_eq!(
        id_of(&first, "cursor"),
        id_of(&second, "cursor"),
        "a harness that did not change must keep its id"
    );
    assert_eq!(
        id_of(&first, "codex"),
        id_of(&second, "codex"),
        "and so must one that did: INSTALLING to READY is the same harness"
    );
    assert_ne!(
        id_of(&first, "cursor"),
        id_of(&first, "codex"),
        "two harnesses are still two harnesses"
    );
}

/// The id `START_RUN` names is one the host was told about.
///
/// The end-to-end version of the test above, and the one that actually failed:
/// `published` is what the offer sends, and it must never get ahead of what
/// the publish answers have handed out.
#[tokio::test]
async fn the_run_is_started_against_an_id_the_host_has_been_given() {
    let control = control_plane().await;
    let link = connect(&control).await.handle;

    let first = publish(&link, vec![snapshot("cursor", "READY", "rev-1")]).await;
    let announced = first["items"][0]["id"].as_str().unwrap().to_owned();

    // Re-publish, then ask -- the order that used to strand the run.
    publish(&link, vec![snapshot("cursor", "READY", "rev-1")]).await;
    let answer = send_control(&link, json!({})).await;

    let start = answer["commands"]
        .as_array()
        .unwrap()
        .iter()
        .find(|command| command["kind"] == "START_RUN")
        .expect("the answer after a publish carries the run");
    assert_eq!(
        start["payload"]["harness_id"].as_str().unwrap(),
        announced,
        "START_RUN must name an id a publish answer has already returned; \
         anything else is HARNESS_NOT_FOUND, which is permanent"
    );
}

/// A refusal reaches the control plane instead of being dropped on the floor.
///
/// The `control` frame carries `rejections`, as the poll body did, and this
/// double used to take no body at all, so the one field that explains a run
/// which never starts was discarded on arrival. Recording it is what turned a
/// 90-second timeout with `events=[]` into a panic that names the cause.
#[tokio::test]
async fn a_refused_command_is_recorded_rather_than_discarded() {
    let control_plane = control_plane().await;
    assert!(control_plane.rejections().is_empty());
    let link = connect(&control_plane).await.handle;

    let answer = send_control(
        &link,
        json!({
            "rejections": [{
                "command_id": uuid::Uuid::new_v4(),
                "run_id": control_plane.run_id,
                "lease_epoch": 1,
                "code": "HARNESS_NOT_FOUND",
                "retryable": false,
                "detail": "command references an unknown harness",
            }],
        }),
    )
    .await;
    assert_eq!(answer["refused"], json!([]), "nothing in it was unreadable");

    let rejections = control_plane.rejections();
    assert_eq!(rejections.len(), 1);
    assert_eq!(rejections[0]["code"], "HARNESS_NOT_FOUND");
    assert_eq!(rejections[0]["retryable"], false);
}

/// The double answers promptly, because the tests wait on real time.
///
/// Not a deadlock check so much as a shape check: every handler here takes
/// `std::sync::Mutex` guards while the link's pusher runs beside them, so a
/// guard held across an await would stall the link the host is using.
#[tokio::test]
async fn control_frames_answer_while_commands_are_being_pushed() {
    let control_plane = control_plane().await;
    let link = connect(&control_plane).await.handle;
    publish(&link, vec![snapshot("cursor", "READY", "rev-1")]).await;

    let controls = (0..8).map(|_| {
        let link = link.clone();
        async move {
            let mut body = json!({});
            body["capacity"] = json!({"max_runs": 1, "active_runs": 0, "available_runs": 1});
            link.request::<Value, Value>(host::CONTROL, &body, ANSWER)
                .await
        }
    });

    let answers = tokio::time::timeout(
        Duration::from_secs(10),
        futures_util::future::join_all(controls),
    )
    .await
    .expect("eight concurrent control frames should not take ten seconds");
    assert!(answers.iter().all(Result::is_ok), "{answers:?}");
}
