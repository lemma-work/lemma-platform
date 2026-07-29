use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

use axum::extract::{Path, State};
use axum::http::{HeaderMap, StatusCode};
use axum::routing::{get, post, put};
use axum::{Json, Router};
use chrono::{Duration, Utc};
use lemma_agent_host::adapters::AdapterManifest;
use lemma_agent_host::api::TargetClient;
use lemma_agent_host::crypto::MemoryVault;
use lemma_agent_host::protocol::{
    Event, EventBatch, EventType, HarnessHealth, HostCapacity, JsonMap,
};
use serde_json::{Value, json};
use tokio::net::TcpListener;
use uuid::Uuid;

#[derive(Clone)]
struct ServerState {
    host_id: Uuid,
    user_id: Uuid,
    harness_id: Uuid,
    route_id: Uuid,
    token_exchanges: Arc<AtomicUsize>,
    reject_next_authenticated: Arc<AtomicBool>,
}

async fn pairing(State(state): State<ServerState>, Json(body): Json<Value>) -> Json<Value> {
    assert!(body["signature"].as_str().unwrap().len() >= 80);
    assert_eq!(body["hello"]["protocol_min"], 2);
    Json(json!({
        "host_id": state.host_id,
        "user_id": state.user_id,
        "organization_id": null,
        "public_key_fingerprint": lemma_fingerprint(body["public_key"].as_str().unwrap()),
    }))
}

async fn token(State(state): State<ServerState>, Json(body): Json<Value>) -> Json<Value> {
    assert_eq!(body["host_id"], state.host_id.to_string());
    state.token_exchanges.fetch_add(1, Ordering::SeqCst);
    Json(json!({
        "access_token": format!("token-{}", state.token_exchanges.load(Ordering::SeqCst)),
        "expires_at": Utc::now() + Duration::minutes(10),
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
    if value.starts_with("Bearer token-") {
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
    assert_eq!(body["hello"]["protocol_max"], 2);
    Ok(Json(json!({
        "protocol_version": 2,
        "policy_revision": "test-policy",
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

async fn mcp_route(
    State(state): State<ServerState>,
    headers: HeaderMap,
    Path(route_id): Path<Uuid>,
) -> Result<Json<Value>, StatusCode> {
    authenticated(&state, &headers)?;
    assert_eq!(route_id, state.route_id);
    Ok(Json(json!({
        "route_id": route_id,
        "run_id": Uuid::nil(),
        "lease_epoch": 1,
        "expires_at": Utc::now() + Duration::minutes(5),
        "mcp": {
            "server_name": "lemma",
            "url": "https://example.test/mcp",
            "authorization": "Bearer run-secret"
        }
    })))
}

async fn revoke(
    State(state): State<ServerState>,
    headers: HeaderMap,
) -> Result<StatusCode, StatusCode> {
    authenticated(&state, &headers)?;
    Ok(StatusCode::NO_CONTENT)
}

fn lemma_fingerprint(public_key: &str) -> String {
    use base64::Engine;
    use sha2::{Digest, Sha256};

    let key = base64::engine::general_purpose::URL_SAFE_NO_PAD
        .decode(public_key)
        .unwrap();
    hex::encode(Sha256::digest(key))
}

#[tokio::test]
async fn pairing_token_refresh_and_all_control_endpoints_interoperate() {
    let state = ServerState {
        host_id: Uuid::new_v4(),
        user_id: Uuid::new_v4(),
        harness_id: Uuid::new_v4(),
        route_id: Uuid::new_v4(),
        token_exchanges: Arc::new(AtomicUsize::new(0)),
        reject_next_authenticated: Arc::new(AtomicBool::new(false)),
    };
    let app = Router::new()
        .route("/agent-host/pairings:complete", post(pairing))
        .route("/agent-host/token:exchange", post(token))
        .route("/agent-host/poll", post(poll))
        .route("/agent-host/harnesses", put(publish))
        .route("/agent-host/events:append", post(append_events))
        .route("/agent-host/mcp-routes/{route_id}", get(mcp_route))
        .route("/agent-host/revoke", post(revoke))
        .with_state(state.clone());
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });

    let base_url = url::Url::parse(&format!("http://{address}/")).unwrap();
    let manifest = AdapterManifest::builtin().unwrap();
    let vault = MemoryVault::default();
    let target = TargetClient::pair(
        base_url,
        "pairing-code-with-enough-entropy",
        "Integration test",
        "installation-test",
        &manifest,
        &vault,
        true,
    )
    .await
    .unwrap();
    let client = TargetClient::new(
        target,
        "installation-test",
        Uuid::new_v4(),
        &manifest,
        &vault,
    )
    .unwrap();
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
    assert_eq!(state.token_exchanges.load(Ordering::SeqCst), 1);

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
        event_id: Uuid::now_v7(),
        occurred_at: Utc::now(),
        event_type: EventType::RunState,
        object_id: None,
        payload: JsonMap::new(),
        harness_key: "codex".into(),
        adapter_version: "1.1.7".into(),
    };
    let ack = client
        .append_events(&EventBatch {
            events: vec![event],
        })
        .await
        .unwrap();
    assert_eq!(ack.acked_through, 1);
    assert_eq!(
        client
            .resolve_mcp_route(state.route_id)
            .await
            .unwrap()
            .route_id,
        state.route_id
    );
    assert_eq!(state.token_exchanges.load(Ordering::SeqCst), 1);

    state
        .reject_next_authenticated
        .store(true, Ordering::SeqCst);
    client
        .poll(HostCapacity::default(), vec![], vec![], vec![])
        .await
        .unwrap();
    assert_eq!(state.token_exchanges.load(Ordering::SeqCst), 2);
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
