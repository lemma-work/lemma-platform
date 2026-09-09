//! The control plane's HTTP surface.

use super::*;

pub(crate) fn assistant_text_of(events: &[Event]) -> String {
    events
        .iter()
        .filter(|event| event.event_type == EventType::AgentMessageChunk)
        .filter_map(|event| event.payload.get("text").and_then(Value::as_str))
        .collect()
}

pub(crate) fn require_auth(headers: &HeaderMap) -> Result<(), StatusCode> {
    (header(headers, "authorization").as_deref() == Some(&format!("Bearer {HOST_SECRET}")))
        .then_some(())
        .ok_or(StatusCode::UNAUTHORIZED)
}

pub(crate) async fn pairing(
    State(state): State<ControlState>,
    Json(_body): Json<Value>,
) -> Json<Value> {
    Json(json!({
        "host_id": state.host_id,
        "user_id": state.user_id,
        "organization_id": null,
        "host_secret": HOST_SECRET,
    }))
}

pub(crate) async fn publish(
    State(state): State<ControlState>,
    headers: HeaderMap,
    Json(body): Json<Value>,
) -> Result<Json<Value>, StatusCode> {
    require_auth(&headers)?;
    let snapshots = body["harnesses"].as_array().cloned().unwrap_or_default();
    state.snapshots.lock().unwrap().clone_from(&snapshots);
    let items = snapshots
        .iter()
        .map(|snapshot| {
            // One id per harness key, for the life of this control plane. See
            // `ControlState::harness_ids` for what minting a fresh one per
            // publish cost.
            let id = *state
                .harness_ids
                .lock()
                .unwrap()
                .entry(
                    snapshot["harness_key"]
                        .as_str()
                        .unwrap_or_default()
                        .to_owned(),
                )
                .or_insert_with(Uuid::new_v4);
            if snapshot["harness_key"].as_str() == Some(state.harness_key.as_str())
                && snapshot["health"].as_str() == Some("READY")
            {
                *state.published.lock().unwrap() = Some((
                    id,
                    snapshot["config_revision"]
                        .as_str()
                        .unwrap_or("")
                        .to_owned(),
                ));
            }
            json!({
                "id": id,
                "harness_key": snapshot["harness_key"],
                "adapter_version": snapshot["adapter_version"],
                "config_revision": snapshot["config_revision"],
            })
        })
        .collect::<Vec<_>>();
    Ok(Json(json!({"items": items})))
}

pub(crate) async fn poll(
    State(state): State<ControlState>,
    headers: HeaderMap,
    Json(body): Json<Value>,
) -> Result<Json<Value>, StatusCode> {
    require_auth(&headers)?;
    // Kept, not ignored. See `ControlState::rejections`.
    if let Some(rejections) = body.get("rejections").and_then(Value::as_array)
        && !rejections.is_empty()
    {
        state
            .rejections
            .lock()
            .unwrap()
            .extend(rejections.iter().cloned());
    }
    // Command acknowledgements, which is how a real control plane learns a
    // command landed. Without reading these the stub cannot tell "delivered"
    // from "written into a response that never arrived".
    let acked: Vec<Uuid> = body
        .get("acknowledged_command_ids")
        .and_then(Value::as_array)
        .map(|ids| {
            ids.iter()
                .filter_map(|id| id.as_str())
                .filter_map(|id| Uuid::parse_str(id).ok())
                .collect()
        })
        .unwrap_or_default();
    if !acked.is_empty()
        && let Some(offered) = state.start_command.lock().unwrap().as_ref()
        && acked.contains(&offered.command_id)
    {
        state.start_sent.store(true, Ordering::SeqCst);
    }

    let mut commands = Vec::new();
    let published = state.published.lock().unwrap().clone();

    if let Some((harness_id, revision)) = published
        && !state.start_sent.load(Ordering::SeqCst)
    {
        // Redelivery preserves the whole command, including its conversation
        // binding and deadline. Rebuilding those fields changes the work.
        let command = state
            .start_command
            .lock()
            .unwrap()
            .get_or_insert_with(|| Command {
                command_id: Uuid::new_v4(),
                kind: CommandKind::StartRun,
                created_at: Utc::now(),
                expires_at: Utc::now() + chrono::Duration::minutes(2),
                run_id: Some(state.run_id),
                lease_epoch: Some(1),
                payload: serde_json::to_value(RunSpec {
                    agent_run_id: state.run_id,
                    conversation_id: Uuid::new_v4(),
                    harness_id,
                    profile_revision: revision,
                    model_name: None,
                    config_selections: JsonMap::new(),
                    system_prompt: "Follow the runtime instructions exactly.".to_owned(),
                    prompt: vec![json!({"type": "text", "text": state.prompt})],
                    resume_session_id: None,
                    workspace_cwd: state.workspace_cwd.lock().unwrap().clone(),
                    context: BTreeMap::new(),
                    mcp: state.mcp.clone(),
                    run_deadline: Utc::now() + *state.run_budget.lock().unwrap(),
                    system_prompt_delivery: None,
                })
                .unwrap(),
            })
            .clone();
        commands.push(serde_json::to_value(command).unwrap());
    }
    let cancel_after = state.cancel_after.lock().unwrap().clone();
    if let Some(marker) = cancel_after {
        let seen = assistant_text_of(&state.events.lock().unwrap()).contains(&marker);
        if seen && !state.cancel_sent.swap(true, Ordering::SeqCst) {
            commands.push(json!({
                "command_id": Uuid::new_v4(),
                "kind": "CANCEL_RUN",
                "created_at": Utc::now(),
                "expires_at": Utc::now() + chrono::Duration::minutes(2),
                "run_id": state.run_id,
                "lease_epoch": 1,
                "payload": {"agent_run_id": state.run_id},
            }));
        }
    }
    let refresh_after = state.refresh_after.lock().unwrap().clone();
    if let Some((marker, mcp)) = refresh_after {
        let seen = assistant_text_of(&state.events.lock().unwrap()).contains(&marker);
        if seen && !state.refresh_sent.swap(true, Ordering::SeqCst) {
            commands.push(json!({
                "command_id": Uuid::new_v4(),
                "kind": "REFRESH_CREDENTIAL",
                "created_at": Utc::now(),
                "expires_at": Utc::now() + chrono::Duration::minutes(2),
                "run_id": state.run_id,
                "lease_epoch": 1,
                "payload": {"mcp": mcp},
            }));
        }
    }
    // Answer every parked request, not just the first: a real agent asks again
    // for each tool it wants, and a control plane that answered once would
    // leave the second request to time out.
    if !matches!(state.permission_answer, PermissionAnswer::Ignore) {
        let mut parked = state
            .events
            .lock()
            .unwrap()
            .iter()
            .filter(|event| event.event_type == EventType::PermissionRequest)
            .filter_map(|event| {
                event
                    .object_id
                    .clone()
                    .map(|request_id| (request_id, event.payload.clone()))
            })
            .collect::<Vec<_>>();
        if matches!(state.permission_answer, PermissionAnswer::AllowThenDeny) && parked.len() < 2 {
            // A concurrency test must observe both requests before answering
            // either. Immediate answers also pass a serial, blocked receiver.
            parked.clear();
        }
        let mut answered = state.answered.lock().unwrap();
        for (request_id, payload) in parked {
            let index = answered.len();
            if !answered.insert(request_id.clone()) {
                continue;
            }
            let option_id = state
                .permission_answer
                .option_for(index, &payload)
                .map_or(Value::Null, Value::String);
            let events = state.events.lock().unwrap();
            state.decisions.lock().unwrap().push(DecisionSnapshot {
                request_id: request_id.clone(),
                option_id: option_id.as_str().map(str::to_owned),
                assistant_text: assistant_text_of(&events),
                saw_terminal: events
                    .iter()
                    .any(|event| event.event_type == EventType::Terminal),
            });
            drop(events);
            commands.push(json!({
                "command_id": Uuid::new_v4(),
                "kind": "RESOLVE_PERMISSION",
                "created_at": Utc::now(),
                "expires_at": Utc::now() + chrono::Duration::minutes(2),
                "run_id": state.run_id,
                "lease_epoch": 1,
                "payload": {"request_id": request_id, "option_id": option_id},
            }));
        }
    }
    if !commands.is_empty() && state.drop_first_command.swap(false, Ordering::SeqCst) {
        // The response the host never received. A real one is lost to a dropped
        // connection or a poll cancelled under load; the effect is the same, and
        // the command has to be offered again.
        return Err(StatusCode::SERVICE_UNAVAILABLE);
    }
    Ok(Json(json!({
        "protocol_version": 2,
        "host_status": "ONLINE",
        "commands": commands,
        "poll_after_ms": 25,
    })))
}

pub(crate) async fn append_events(
    State(state): State<ControlState>,
    headers: HeaderMap,
    Json(body): Json<Value>,
) -> Result<Json<Value>, StatusCode> {
    require_auth(&headers)?;
    let batch: EventBatch = serde_json::from_value(body).unwrap();
    if batch.events.is_empty()
        || batch
            .events
            .iter()
            .any(|event| event.run_id != state.run_id || event.lease_epoch != 1)
    {
        return Err(StatusCode::BAD_REQUEST);
    }
    state
        .append_attempts
        .lock()
        .unwrap()
        .push(batch.events.iter().map(|event| event.sequence).collect());
    let mut events = state.events.lock().unwrap();
    let mut watermark = events.last().map_or(0, |event| event.sequence);
    for event in &batch.events {
        if event.sequence > watermark + 1 {
            return Err(StatusCode::CONFLICT);
        }
        watermark = watermark.max(event.sequence);
    }
    for event in batch.events {
        if events
            .last()
            .is_none_or(|last| event.sequence > last.sequence)
        {
            events.push(event);
        }
    }
    // A response can be lost after the receiver committed the batch. Retries
    // must preserve the first event for a sequence, just as the backend does.
    if state.lose_append_ack.swap(false, Ordering::SeqCst) {
        return Err(StatusCode::SERVICE_UNAVAILABLE);
    }
    let response = json!({
        "run_id": state.run_id,
        "lease_epoch": 1,
        "acked_through": watermark,
    });
    Ok(Json(response))
}
