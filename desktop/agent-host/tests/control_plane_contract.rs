//! What the stand-in control plane promises to behave like.
//!
//! Every other test in this crate drives the real host binary against the
//! double in `support`. That only proves anything about the shipped host while
//! the double behaves like the backend it stands in for -- and one place where
//! it did not cost this suite an intermittent 90-second hang across at least
//! four different tests in two files, on branches touching none of them.
//!
//! So the properties the host actually depends on are asserted here, against
//! the double's real HTTP surface, rather than left implicit in tests that are
//! about something else.

mod support;

use std::time::Duration;

use serde_json::{Value, json};
use support::{ControlPlane, HOST_SECRET, PermissionAnswer};

/// One publish of two harnesses, as the host sends it.
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

async fn publish(control: &ControlPlane, body: Value) -> Value {
    reqwest::Client::new()
        .put(control.base_url.join("agent-host/harnesses").unwrap())
        .bearer_auth(HOST_SECRET)
        .json(&body)
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap()
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

fn event(control: &ControlPlane, sequence: u64, text: &str) -> Value {
    json!({
        "run_id": control.run_id, "lease_epoch": 1, "sequence": sequence,
        "type": "agent_message_chunk", "object_id": null, "payload": {"text": text},
    })
}

async fn append(control: &ControlPlane, events: Vec<Value>) -> reqwest::Response {
    reqwest::Client::new()
        .post(control.base_url.join("agent-host/events/append").unwrap())
        .bearer_auth(HOST_SECRET)
        .json(&json!({"events": events}))
        .send()
        .await
        .unwrap()
}

#[tokio::test]
async fn a_lost_append_ack_replays_without_replacing_accepted_text() {
    let control = control_plane().await;
    control.lose_the_first_append_ack();
    let first = append(&control, vec![event(&control, 1, "first")]).await;
    assert_eq!(first.status(), reqwest::StatusCode::SERVICE_UNAVAILABLE);
    assert_eq!(control.assistant_text(), "first");
    let retried = append(
        &control,
        vec![
            event(&control, 1, "replacement"),
            event(&control, 2, " second"),
        ],
    )
    .await;
    assert!(retried.status().is_success());
    assert_eq!(retried.json::<Value>().await.unwrap()["acked_through"], 2);
    assert_eq!(control.assistant_text(), "first second");
    assert_eq!(control.events().len(), 2);
}

#[tokio::test]
async fn an_event_gap_rejects_the_whole_batch_before_accepting_text() {
    let control = control_plane().await;
    let response = append(
        &control,
        vec![event(&control, 1, "first"), event(&control, 3, "third")],
    )
    .await;
    assert_eq!(response.status(), reqwest::StatusCode::CONFLICT);
    assert!(control.events().is_empty());
    assert!(
        append(
            &control,
            vec![event(&control, 1, "first"), event(&control, 2, "second")]
        )
        .await
        .status()
        .is_success()
    );
    assert_eq!(control.events().len(), 2);
}

/// A harness keeps one id, however many times it is published.
///
/// `agent_host_harnesses` is unique on `(host_id, harness_key)`, so the backend
/// upserts and the id is stable for the life of the host. The double used to
/// mint a fresh UUID per snapshot per publish.
///
/// That is not a cosmetic difference. The host builds its harness map from the
/// publish *response*, so a re-published id is unknown to it until that
/// response lands. `START_RUN` naming the new id in the meantime is rejected as
/// `HARNESS_NOT_FOUND`, which `command_rejection` marks `retryable: false` --
/// and this double sends `START_RUN` exactly once. The run never starts, and
/// the test waits out its whole timeout for a terminal event nobody was going
/// to send.
#[tokio::test]
async fn a_harness_keeps_its_id_across_republishes() {
    let control = control_plane().await;

    let first = publish(
        &control,
        json!({"harnesses": [
            snapshot("cursor", "READY", "rev-1"),
            snapshot("codex", "INSTALLING", "rev-installing"),
        ]}),
    )
    .await;

    // The second publish is what a finished adapter install produces: one
    // harness changes state, and every harness is sent again.
    let second = publish(
        &control,
        json!({"harnesses": [
            snapshot("cursor", "READY", "rev-1"),
            snapshot("codex", "READY", "rev-2"),
        ]}),
    )
    .await;

    let id_of = |response: &Value, key: &str| {
        response["items"]
            .as_array()
            .unwrap()
            .iter()
            .find(|item| item["harness_key"] == key)
            .unwrap_or_else(|| panic!("{key} is missing from the publish response"))["id"]
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
/// `published` is what the poll sends, and it must never get ahead of what the
/// publish responses have handed out.
#[tokio::test]
async fn the_run_is_started_against_an_id_the_host_has_been_given() {
    let control = control_plane().await;

    let first = publish(
        &control,
        json!({"harnesses": [snapshot("cursor", "READY", "rev-1")]}),
    )
    .await;
    let announced = first["items"][0]["id"].as_str().unwrap().to_owned();

    // Re-publish, then poll -- the order that used to strand the run.
    publish(
        &control,
        json!({"harnesses": [snapshot("cursor", "READY", "rev-1")]}),
    )
    .await;

    let poll: Value = reqwest::Client::new()
        .post(control.base_url.join("agent-host/poll").unwrap())
        .bearer_auth(HOST_SECRET)
        .json(&json!({
            "hello": {},
            "capacity": {"max_runs": 1, "active_runs": 0, "available_runs": 1},
        }))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();

    let start = poll["commands"]
        .as_array()
        .unwrap()
        .iter()
        .find(|command| command["kind"] == "START_RUN")
        .expect("the poll after a publish carries the run");
    assert_eq!(
        start["payload"]["harness_id"].as_str().unwrap(),
        announced,
        "START_RUN must name an id a publish response has already returned; \
         anything else is HARNESS_NOT_FOUND, which is permanent"
    );
}

/// A refusal reaches the control plane instead of being dropped on the floor.
///
/// The poll body carries `rejections` and this double used to take no body at
/// all, so the one field that explains a run which never starts was discarded
/// on arrival. Recording it is what turned a 90-second timeout with `events=[]`
/// into a panic that names the cause.
#[tokio::test]
async fn a_refused_command_is_recorded_rather_than_discarded() {
    let control = control_plane().await;
    assert!(control.rejections().is_empty());

    reqwest::Client::new()
        .post(control.base_url.join("agent-host/poll").unwrap())
        .bearer_auth(HOST_SECRET)
        .json(&json!({
            "hello": {},
            "capacity": {"max_runs": 1, "active_runs": 0, "available_runs": 1},
            "rejections": [{
                "command_id": uuid::Uuid::new_v4(),
                "run_id": control.run_id,
                "lease_epoch": 1,
                "code": "HARNESS_NOT_FOUND",
                "retryable": false,
                "detail": "command references an unknown harness",
            }],
        }))
        .send()
        .await
        .unwrap()
        .error_for_status()
        .unwrap();

    let rejections = control.rejections();
    assert_eq!(rejections.len(), 1);
    assert_eq!(rejections[0]["code"], "HARNESS_NOT_FOUND");
    assert_eq!(rejections[0]["retryable"], false);
}

/// The double answers a poll promptly, because the tests wait on real time.
///
/// Not a deadlock check so much as a shape check: every handler here takes
/// `std::sync::Mutex` guards on a `#[tokio::test]` current-thread runtime, so a
/// guard held across an await would stall the server the host is polling.
#[tokio::test]
async fn a_poll_answers_while_events_are_being_appended() {
    let control = control_plane().await;
    publish(
        &control,
        json!({"harnesses": [snapshot("cursor", "READY", "rev-1")]}),
    )
    .await;

    let client = reqwest::Client::new();
    let polls = (0..8).map(|_| {
        let client = client.clone();
        let url = control.base_url.join("agent-host/poll").unwrap();
        async move {
            client
                .post(url)
                .bearer_auth(HOST_SECRET)
                .json(&json!({
                    "hello": {},
                    "capacity": {"max_runs": 1, "active_runs": 0, "available_runs": 1},
                }))
                .send()
                .await
                .unwrap()
                .status()
        }
    });

    let statuses = tokio::time::timeout(
        Duration::from_secs(10),
        futures_util::future::join_all(polls),
    )
    .await
    .expect("eight concurrent polls should not take ten seconds");
    assert!(statuses.iter().all(reqwest::StatusCode::is_success));
}
