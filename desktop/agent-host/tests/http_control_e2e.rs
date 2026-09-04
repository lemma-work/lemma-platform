use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};

use axum::extract::State;
use axum::http::{HeaderMap, StatusCode};
use axum::routing::{post, put};
use axum::{Json, Router};
use lemma_agent_host::adapters::AdapterManifest;
use lemma_agent_host::api::TargetClient;
use lemma_agent_host::protocol::{
    Event, EventBatch, EventType, HarnessHealth, HostCapacity, JsonMap,
};
use serde_json::{Value, json};
use tokio::net::TcpListener;
use uuid::Uuid;

const HOST_SECRET: &str = "test-host-secret-with-enough-entropy";

#[derive(Clone)]
struct ServerState {
    host_id: Uuid,
    user_id: Uuid,
    harness_id: Uuid,
    reject_next_authenticated: Arc<AtomicBool>,
}

async fn pairing(State(state): State<ServerState>, Json(body): Json<Value>) -> Json<Value> {
    assert_eq!(body["pairing_code"], "pairing-code-with-enough-entropy");
    assert_eq!(body["hello"]["protocol_version"], 2);
    Json(json!({
        "host_id": state.host_id,
        "user_id": state.user_id,
        "organization_id": null,
        "host_secret": HOST_SECRET,
    }))
}

fn authenticated(state: &ServerState, headers: &HeaderMap) -> Result<(), StatusCode> {
    if state
        .reject_next_authenticated
        .swap(false, Ordering::SeqCst)
    {
        return Err(StatusCode::UNAUTHORIZED);
    }
    let value = headers
        .get("authorization")
        .and_then(|value| value.to_str().ok())
        .unwrap_or_default();
    if value == format!("Bearer {HOST_SECRET}") {
        Ok(())
    } else {
        Err(StatusCode::UNAUTHORIZED)
    }
}

async fn poll(
    State(state): State<ServerState>,
    headers: HeaderMap,
    Json(body): Json<Value>,
) -> Result<Json<Value>, StatusCode> {
    authenticated(&state, &headers)?;
    assert_eq!(body["hello"]["protocol_version"], 2);
    Ok(Json(json!({
        "protocol_version": 2,
        "host_status": "ONLINE",
        "commands": [],
        "poll_after_ms": 0,
    })))
}

async fn publish(
    State(state): State<ServerState>,
    headers: HeaderMap,
    Json(body): Json<Value>,
) -> Result<Json<Value>, StatusCode> {
    authenticated(&state, &headers)?;
    let snapshot = &body["harnesses"][0];
    Ok(Json(json!({
        "items": [{
            "id": state.harness_id,
            "harness_key": snapshot["harness_key"],
            "adapter_version": snapshot["adapter_version"],
            "config_revision": snapshot["config_revision"],
        }]
    })))
}

async fn append_events(
    State(state): State<ServerState>,
    headers: HeaderMap,
    Json(body): Json<Value>,
) -> Result<Json<Value>, StatusCode> {
    authenticated(&state, &headers)?;
    let event = &body["events"][0];
    Ok(Json(json!({
        "run_id": event["run_id"],
        "lease_epoch": event["lease_epoch"],
        "acked_through": event["sequence"],
    })))
}

async fn revoke(
    State(state): State<ServerState>,
    headers: HeaderMap,
) -> Result<StatusCode, StatusCode> {
    authenticated(&state, &headers)?;
    Ok(StatusCode::NO_CONTENT)
}

#[tokio::test]
async fn pairing_and_all_control_endpoints_interoperate() {
    let state = ServerState {
        host_id: Uuid::new_v4(),
        user_id: Uuid::new_v4(),
        harness_id: Uuid::new_v4(),
        reject_next_authenticated: Arc::new(AtomicBool::new(false)),
    };
    let app = Router::new()
        .route("/agent-host/pairings/complete", post(pairing))
        .route("/agent-host/poll", post(poll))
        .route("/agent-host/harnesses", put(publish))
        .route("/agent-host/events/append", post(append_events))
        .route("/agent-host/revoke", post(revoke))
        .with_state(state.clone());
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });

    let base_url = url::Url::parse(&format!("http://{address}/")).unwrap();
    let manifest = AdapterManifest::builtin().unwrap();
    let target = TargetClient::pair(
        base_url,
        "pairing-code-with-enough-entropy",
        "Integration test",
        "installation-test",
        true,
    )
    .await
    .unwrap();
    assert_eq!(target.host_secret, HOST_SECRET);
    let client = TargetClient::new(target, "installation-test").unwrap();
    client
        .poll(
            HostCapacity {
                max_runs: 2,
                active_runs: 0,
                available_runs: 2,
            },
            vec![],
            vec![],
            vec![],
        )
        .await
        .unwrap();

    let snapshots = manifest.discover();
    let expected_revision = snapshots[0].config_revision.clone();
    let published = client.publish_harnesses(snapshots).await.unwrap();
    assert_eq!(published[0].id, state.harness_id);
    assert_eq!(published[0].config_revision, expected_revision);

    let run_id = Uuid::new_v4();
    let event = Event {
        run_id,
        lease_epoch: 1,
        sequence: 1,
        event_type: EventType::RunState,
        object_id: None,
        payload: JsonMap::new(),
    };
    let ack = client
        .append_events(&EventBatch {
            events: vec![event],
        })
        .await
        .unwrap();
    assert_eq!(ack.acked_through, 1);

    state
        .reject_next_authenticated
        .store(true, Ordering::SeqCst);
    let rejected = client
        .poll(HostCapacity::default(), vec![], vec![], vec![])
        .await;
    assert!(rejected.unwrap_err().is_unauthorized());
    client.revoke().await.unwrap();
    server.abort();
}

#[test]
fn harness_health_enum_stays_wire_compatible() {
    assert_eq!(
        serde_json::to_value(HarnessHealth::Ready).unwrap(),
        Value::String("READY".into())
    );
}
