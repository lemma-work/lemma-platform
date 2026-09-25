//! The host's link client against Lemma's end of the link, one exchange each.
//!
//! Pairing, the authenticated `hello`, a `control` heartbeat, harness
//! publication, an event batch and revocation, each through the same public
//! functions the worker uses, against the stand-in control plane in
//! `support`. The flows above this drive the whole binary; this is the place
//! a broken frame shape fails on its own, named.

mod support;

use lemma_agent_host::adapters::AdapterManifest;
use lemma_agent_host::link::{self, protocol::ControlBody};
use lemma_agent_host::protocol::{
    Event, EventBatch, EventType, HarnessHealth, HostCapacity, HostHello, JsonMap,
};
use serde_json::{Value, json};
use support::{ControlPlane, HOST_SECRET, PermissionAnswer};

const PAIRING_CODE: &str = "pairing-code-with-enough-entropy";
const INSTALLATION: &str = "installation-test";

fn capacity() -> HostCapacity {
    HostCapacity {
        max_runs: 2,
        active_runs: 0,
        available_runs: 2,
    }
}

#[tokio::test]
async fn pairing_and_every_link_request_interoperate() {
    let control =
        ControlPlane::start("cursor", "unused", json!({}), PermissionAnswer::Ignore).await;

    // Pairing: the one exchange without a host secret, because it issues one.
    let target = link::pair(
        control.base_url.clone(),
        PAIRING_CODE,
        "Integration test",
        INSTALLATION,
        true,
    )
    .await
    .unwrap();
    assert_eq!(target.host_secret, HOST_SECRET);
    assert_eq!(target.host_id, control.host_id);
    assert_eq!(target.user_id, control.user_id);
    let pairings = control.pairings();
    assert_eq!(pairings.len(), 1);
    assert_eq!(pairings[0]["pairing_code"], PAIRING_CODE);
    assert_eq!(
        pairings[0]["hello"]["protocol_version"],
        lemma_agent_host::PROTOCOL_VERSION
    );

    // `hello`, with the secret pairing issued.
    let connected = link::connect(
        &target.base_url,
        &target.host_secret,
        HostHello::current(INSTALLATION),
        capacity(),
    )
    .await
    .unwrap();
    assert_eq!(connected.welcome.host_id, control.host_id);
    assert_eq!(
        connected.welcome.protocol_version,
        lemma_agent_host::PROTOCOL_VERSION
    );
    let hellos = control.hellos();
    assert_eq!(hellos.len(), 1);
    assert_eq!(hellos[0]["hello"]["installation_id"], INSTALLATION);
    assert_eq!(hellos[0]["capacity"]["max_runs"], 2);
    let link = connected.handle;

    // The heartbeat. Nothing is published, so nothing is owed.
    let answer = link
        .control(&ControlBody {
            capacity: capacity(),
            ..ControlBody::default()
        })
        .await
        .unwrap();
    assert!(answer.commands.is_empty());
    assert!(answer.refused.is_empty());

    // Harness publication hands back the id each harness is dispatched by.
    let manifest = AdapterManifest::builtin().unwrap();
    let snapshots = manifest.discover();
    let expected = snapshots[0].clone();
    let published = link.publish_harnesses(snapshots).await.unwrap();
    assert_eq!(published[0].harness_key, expected.harness_key);
    assert_eq!(published[0].config_revision, expected.config_revision);
    assert_eq!(
        control.published_snapshots()[0]["harness_key"],
        Value::String(expected.harness_key.clone())
    );

    // An event batch is acknowledged through its last sequence.
    let ack = link
        .append_events(&EventBatch {
            events: vec![Event {
                run_id: control.run_id,
                lease_epoch: 1,
                sequence: 1,
                event_type: EventType::RunState,
                object_id: None,
                payload: JsonMap::new(),
            }],
        })
        .await
        .unwrap();
    assert_eq!(ack.run_id, control.run_id);
    assert_eq!(ack.acked_through, 1);
    assert_eq!(control.events().len(), 1);

    // Revocation retires the pairing's own credential, on a link of its own.
    link::revoke(&target, INSTALLATION).await.unwrap();
    assert_eq!(control.revocations(), 1);
}

/// A refused `hello` is a close code the host can act on, not a generic
/// failure. The worker gives up on a malformed credential at once, and drops a
/// pairing Lemma does not know only after it is refused three times running;
/// reading the two apart is what decides between those.
#[tokio::test]
async fn a_refused_hello_says_why() {
    let control =
        ControlPlane::start("cursor", "unused", json!({}), PermissionAnswer::Ignore).await;

    let wrong_secret = link::connect(
        &control.base_url,
        "not-the-host-secret",
        HostHello::current(INSTALLATION),
        capacity(),
    )
    .await
    .err()
    .expect("a hello with the wrong secret must be refused");
    assert!(
        wrong_secret.is_invalid_credential(),
        "a bad secret closes 4403: {wrong_secret}"
    );

    control.refuse_the_next_hello_with(link::protocol::close::REVOKED_OR_MISSING);
    let unknown = link::connect(
        &control.base_url,
        HOST_SECRET,
        HostHello::current(INSTALLATION),
        capacity(),
    )
    .await
    .err()
    .expect("a hello Lemma refuses must fail");
    assert!(
        unknown.is_revoked_or_missing(),
        "an unknown pairing closes 4401: {unknown}"
    );
    assert!(control.hellos().is_empty(), "neither hello was welcomed");
}

#[test]
fn harness_health_enum_stays_wire_compatible() {
    assert_eq!(
        serde_json::to_value(HarnessHealth::Ready).unwrap(),
        Value::String("READY".into())
    );
}
