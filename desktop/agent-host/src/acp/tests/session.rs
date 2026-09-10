use agent_client_protocol::schema::v1::{ContentChunk, ImageContent, SessionUpdate, TextContent};

use super::*;

/// ACP names five ways a turn can end. Three of them used to arrive as an
/// undifferentiated `FAILED` with no detail, so a run that merely ran out
/// of context told the user only that it had failed.
#[test]
fn every_acp_stop_reason_is_reported_distinguishably() {
    assert_eq!(outcome_for("end_turn"), (RunState::Succeeded, None));
    assert_eq!(outcome_for("cancelled"), (RunState::Cancelled, None));

    for reason in ["max_tokens", "max_turn_requests", "refusal"] {
        let (state, message) = outcome_for(reason);
        assert_eq!(state, RunState::Failed, "{reason} did not fail");
        assert!(
            message.is_some_and(|message| !message.is_empty()),
            "{reason} gave the user nothing to act on"
        );
    }
}

/// A cancelled run is cancelled, whatever the agent calls it.
///
/// `OpenCode` ends a turn stopped by `session/cancel` with `end_turn`
/// rather than the `cancelled` ACP requires — observed against the real
/// agent. Mapping that literally recorded a run the user had stopped as
/// SUCCEEDED, with a truncated answer standing in for the whole one.
#[test]
fn a_run_we_asked_to_stop_is_cancelled_whatever_the_agent_reports() {
    for reported in ["cancelled", "end_turn", "max_tokens", "unknown"] {
        assert_eq!(
            run_outcome(true, reported),
            (RunState::Cancelled, None),
            "a cancelled run reported as {reported:?} was not recorded as cancelled"
        );
    }
}

/// The override must not reach a turn nobody interrupted, or an ordinary
/// failure would be filed as a user cancellation.
#[test]
fn a_run_nobody_stopped_keeps_its_own_stop_reason() {
    assert_eq!(run_outcome(false, "end_turn"), (RunState::Succeeded, None));
    assert_eq!(run_outcome(false, "max_tokens").0, RunState::Failed);
}

/// A reason this build has never heard of must still say something, and
/// must name what it saw rather than swallowing it.
#[test]
fn an_unknown_stop_reason_still_explains_itself() {
    let (state, message) = outcome_for("some_future_reason");
    assert_eq!(state, RunState::Failed);
    assert!(message.is_some_and(|message| message.contains("some_future_reason")));
}

fn model_option(values: &[&str]) -> ConfigOption {
    ConfigOption {
        id: "model".into(),
        category: "model".into(),
        name: "Model".into(),
        description: None,
        current_value: Value::Null,
        options: values
            .iter()
            .map(|value| {
                let mut entry = JsonMap::new();
                entry.insert("value".to_owned(), Value::from(*value));
                entry
            })
            .collect(),
        metadata: JsonMap::new(),
    }
}

/// The turn-two failure, in the form the user actually reported it.
///
/// A Lemma conversation resumes its provider session on every turn after
/// the first, and ACP makes `configOptions` optional on `session/load`.
/// Turn one opened with `opus[1m]` in the list and ran; turn two loaded a
/// session that reported nothing and the run died on
/// "selected model is not offered by this harness" -- a model the picker
/// had offered a minute earlier.
#[test]
fn a_model_the_session_stopped_reporting_is_reported_not_fatal() {
    let none_offered: Vec<ConfigOption> = Vec::new();
    let payload = model_unavailable_payload("opus[1m]", &none_offered);

    assert_eq!(
        payload.get("status").and_then(Value::as_str),
        Some("model_unavailable")
    );
    assert_eq!(
        payload.get("requested_model").and_then(Value::as_str),
        Some("opus[1m]")
    );
    let detail = payload
        .get("detail")
        .and_then(Value::as_str)
        .expect("a status the user can read");
    assert!(detail.contains("opus[1m]"), "the detail named no model");
    assert!(detail.contains("own default"), "{detail}");
}

/// Naming the alternatives is what tells the two causes apart afterwards:
/// an agent that renamed its models still lists some, a session that
/// reported nothing lists none.
#[test]
fn an_unavailable_model_says_what_the_harness_does_offer() {
    let detail = model_unavailable_payload("opus[1m]", &[model_option(&["sonnet", "haiku"])])
        .get("detail")
        .and_then(Value::as_str)
        .map(str::to_owned)
        .expect("a status the user can read");

    assert!(detail.contains("sonnet"), "{detail}");
    assert!(detail.contains("haiku"), "{detail}");
}

/// An empty option list is permissive by design -- a harness that
/// enumerates nothing constrains nothing -- so the fallback must only ever
/// be reached when the session genuinely reported no options at all.
#[test]
fn a_model_the_session_still_offers_is_left_alone() {
    let option = model_option(&["opus[1m]", "sonnet"]);

    assert!(selection_is_allowed(
        &option,
        &Value::String("opus[1m]".into())
    ));
    assert!(!selection_is_allowed(
        &option,
        &Value::String("claude-fable-5[1m]".into())
    ));
}

/// `PromptRequest` takes a `Vec<ContentBlock>` so that non-text content can
/// travel. Flattening the whole prompt to one string meant an image or an
/// embedded resource was not degraded but discarded.
#[test]
fn a_non_text_prompt_block_survives_into_the_request() {
    let mut spec = spec_resuming(None);
    spec.system_prompt = "Be brief.".to_owned();
    spec.prompt = vec![
        serde_json::json!({"type": "text", "text": "What is in this picture?"}),
        serde_json::json!({
            "type": "image",
            "data": "aGk=",
            "mimeType": "image/png",
        }),
    ];

    let blocks = prompt_blocks(&spec, SessionOrigin::New);

    assert_eq!(blocks.len(), 2, "expected the text block and the image");
    assert!(matches!(blocks[0], ContentBlock::Text(_)));
    assert!(
        matches!(blocks[1], ContentBlock::Image(_)),
        "the image was dropped rather than sent"
    );
}

/// The text half must keep behaving exactly as it did, since every
/// certified adapter has been reading that one assembled block.
#[test]
fn text_blocks_are_still_assembled_into_one_leading_block() {
    let mut spec = spec_resuming(None);
    spec.system_prompt = "Be brief.".to_owned();
    spec.prompt = vec![serde_json::json!({"type": "text", "text": "Hello."})];

    let blocks = prompt_blocks(&spec, SessionOrigin::New);

    assert_eq!(blocks.len(), 1);
    let ContentBlock::Text(text) = &blocks[0] else {
        panic!("expected a text block");
    };
    assert!(text.text.contains("Be brief."));
    assert!(text.text.contains("Hello."));
}

pub(super) fn spec_delivered_once() -> RunSpec {
    let mut spec = spec_resuming(Some("sess-1"));
    spec.system_prompt_delivery = Some(crate::protocol::NEW_SESSION_ONLY.to_owned());
    spec
}

/// A conversation is one provider session, and that session keeps its own
/// history — so instructions delivered when it opened are still there.
#[test]
fn a_resumed_turn_leaves_out_instructions_the_session_already_has() {
    let rendered = render_prompt(&spec_delivered_once(), SessionOrigin::Loaded);

    assert_eq!(rendered, "Hello");
    assert!(!rendered.contains("<system>"));
}

/// The case Lemma cannot predict, and the reason this decision lives here.
///
/// A provider is free to forget a session, so a `session/load` Lemma fully
/// expected to succeed can still leave us opening a fresh one. That session
/// has never seen the instructions, and Lemma has no way to know in
/// advance — deciding this at dispatch would produce a turn with neither
/// history nor instructions, which is the worst outcome available.
#[test]
fn a_session_that_had_to_be_recreated_still_gets_its_instructions() {
    let rendered = render_prompt(&spec_delivered_once(), SessionOrigin::New);

    assert!(
        rendered.contains("<system>"),
        "a session opened after a failed load has never seen them"
    );
}

/// Absent or unrecognised means send them. An older Lemma does not set the
/// field at all, and a newer one may name a policy this build predates;
/// both have to degrade to the behaviour that is merely wasteful rather
/// than the one that silently un-instructs the agent.
#[test]
fn an_unknown_delivery_policy_still_sends_the_instructions() {
    let mut absent = spec_resuming(Some("sess-1"));
    absent.system_prompt_delivery = None;
    assert!(render_prompt(&absent, SessionOrigin::Loaded).contains("<system>"));

    let mut unknown = spec_resuming(Some("sess-1"));
    unknown.system_prompt_delivery = Some("EVERY_THIRD_TUESDAY".to_owned());
    assert!(render_prompt(&unknown, SessionOrigin::Loaded).contains("<system>"));
}

/// With the system block conditional, a turn can now render to nothing —
/// and an empty leading text block is not something every adapter accepts.
#[test]
fn a_resumed_image_only_turn_sends_no_empty_text_block() {
    let mut spec = spec_delivered_once();
    spec.prompt = vec![serde_json::json!({
        "type": "image",
        "data": "aGk=",
        "mimeType": "image/png",
    })];

    let blocks = prompt_blocks(&spec, SessionOrigin::Loaded);

    assert_eq!(blocks.len(), 1, "only the image should have been sent");
    assert!(matches!(blocks[0], ContentBlock::Image(_)));
}

/// A block shape this build cannot parse must not take the turn down with
/// it, and must be visible in the logs rather than vanishing quietly.
#[test]
fn an_unparseable_block_is_skipped_not_fatal() {
    let mut spec = spec_resuming(None);
    spec.prompt = vec![
        serde_json::json!({"type": "text", "text": "Hello."}),
        serde_json::json!({"type": "something_new", "payload": 1}),
    ];

    let blocks = prompt_blocks(&spec, SessionOrigin::New);

    assert_eq!(blocks.len(), 1);
}

/// The three explained failures must not read alike, or the distinction
/// exists only in the code.
#[test]
fn the_explanations_differ_from_each_other() {
    let messages =
        ["max_tokens", "max_turn_requests", "refusal"].map(|reason| outcome_for(reason).1.unwrap());
    assert_ne!(messages[0], messages[1]);
    assert_ne!(messages[1], messages[2]);
    assert_ne!(messages[0], messages[2]);
}

pub(super) fn spec_resuming(resume_session_id: Option<&str>) -> RunSpec {
    RunSpec {
        agent_run_id: uuid::Uuid::new_v4(),
        conversation_id: uuid::Uuid::new_v4(),
        harness_id: uuid::Uuid::new_v4(),
        profile_revision: "r".into(),
        model_name: None,
        config_selections: JsonMap::new(),
        system_prompt: "Be exact.".into(),
        prompt: vec![serde_json::json!({"type": "text", "text": "Hello"})],
        resume_session_id: resume_session_id.map(str::to_owned),
        workspace_cwd: None,
        context: JsonMap::new(),
        mcp: serde_json::Value::Null,
        run_deadline: chrono::Utc::now(),
        system_prompt_delivery: None,
    }
}

#[test]
fn prompt_rendering_keeps_system_and_text() {
    assert_eq!(
        render_prompt(&spec_resuming(None), SessionOrigin::New),
        "<system>\nBe exact.\n</system>\n\nHello"
    );
}

#[test]
fn a_conversation_with_a_session_resumes_it() {
    assert_eq!(
        session_to_resume(&spec_resuming(Some("sess-1")), true),
        Some("sess-1".to_owned())
    );
}

#[test]
fn a_conversations_first_turn_opens_a_new_session() {
    assert_eq!(session_to_resume(&spec_resuming(None), true), None);
}

#[test]
fn a_harness_that_cannot_load_never_tries_to() {
    assert_eq!(
        session_to_resume(&spec_resuming(Some("sess-1")), false),
        None
    );
}

#[test]
fn a_blank_session_id_is_not_worth_a_round_trip() {
    assert_eq!(session_to_resume(&spec_resuming(Some("  ")), true), None);
}

#[test]
fn message_chunk_is_flattened_for_backend_normalizer() {
    let update = SessionUpdate::AgentMessageChunk(ContentChunk::new(ContentBlock::Text(
        TextContent::new("hi"),
    )));
    let (kind, _, payload) = normalize_session_update(&update).unwrap();
    assert_eq!(kind, EventType::AgentMessageChunk);
    assert_eq!(payload.get("text"), Some(&Value::String("hi".into())));
}

#[test]
fn an_initial_tool_call_retains_acps_implicit_pending_status() {
    let update: SessionUpdate = serde_json::from_value(serde_json::json!({
        "sessionUpdate": "tool_call",
        "toolCallId": "read-project",
        "title": "Read README.md"
    }))
    .unwrap();
    let (kind, id, payload) = normalize_session_update(&update).unwrap();
    assert_eq!(kind, EventType::ToolCallUpsert);
    assert_eq!(id.as_deref(), Some("read-project"));
    assert_eq!(payload["status"], "pending");
}

#[test]
fn image_chunk_preserves_the_standard_acp_content_block() {
    let update = SessionUpdate::AgentMessageChunk(ContentChunk::new(ContentBlock::Image(
        ImageContent::new("cG5n", "image/png"),
    )));
    let (kind, _, payload) = normalize_session_update(&update).unwrap();
    assert_eq!(kind, EventType::AgentMessageChunk);
    assert_eq!(
        payload.get("content"),
        Some(&serde_json::json!({
            "type": "image",
            "data": "cG5n",
            "mimeType": "image/png",
        }))
    );
    assert!(!payload.contains_key("text"));
}

#[test]
fn codex_scoped_mcp_approval_is_recognized() {
    let request: RequestPermissionRequest = serde_json::from_value(serde_json::json!({
        "sessionId": "session",
        "toolCall": {
            "kind": "execute",
            "status": "pending",
            "toolCallId": "call-1"
        },
        "options": [
            {
                "kind": "allow_once",
                "name": "Allow",
                "optionId": "allow_once"
            },
            {
                "kind": "reject_once",
                "name": "Decline",
                "optionId": "decline"
            }
        ],
        "_meta": {"is_mcp_tool_approval": true}
    }))
    .unwrap();

    assert!(is_scoped_mcp_tool_approval(&request, &HashSet::new()));
}

#[test]
fn native_tool_permission_is_not_treated_as_scoped_mcp() {
    let request: RequestPermissionRequest = serde_json::from_value(serde_json::json!({
        "sessionId": "session",
        "toolCall": {
            "kind": "execute",
            "status": "pending",
            "toolCallId": "call-1",
            "rawInput": {"command": "python -c 'print(42)'"}
        },
        "options": [
            {
                "kind": "allow_once",
                "name": "Allow once",
                "optionId": "allow_once"
            }
        ],
        "_meta": {"codex": {"reason": "run local shell"}}
    }))
    .unwrap();

    assert!(!is_scoped_mcp_tool_approval(&request, &HashSet::new()));
}

#[test]
fn provider_full_access_modes_are_filtered_and_overridden() {
    let option: SessionConfigOption = serde_json::from_value(serde_json::json!({
        "id": "mode",
        "name": "Mode",
        "category": "mode",
        "type": "select",
        "currentValue": "agent-full-access",
        "options": [
            {"value": "plan", "name": "Plan"},
            {"value": "agent", "name": "Agent"},
            {"value": "acceptEdits", "name": "Accept edits"},
            {"value": "agent-full-access", "name": "Agent (full access)"},
            {"value": "bypassPermissions", "name": "Bypass permissions"}
        ]
    }))
    .unwrap();

    let converted = convert_config_option("codex", &option).unwrap();
    assert_eq!(converted.current_value, Value::String("agent".into()));
    assert_eq!(converted.options.len(), 2);
    assert_eq!(
        converted.metadata.get("hostPolicyDefaultOverride"),
        Some(&Value::Bool(true))
    );
    assert!(!selection_is_allowed(
        &converted,
        &Value::String("agent-full-access".into())
    ));
}

/// An agent is usually a wrapper with the real agent underneath it.
///
/// `npx` and `uvx` are how these things ship, so the process this host
/// holds is the launcher and the agent is its child. Ending only the
/// launcher leaves the agent running with the workspace open and the
/// provider credential in hand, after the run it belonged to is over.
///
/// Driven against `/bin/sh` in exactly that shape: a wrapper that starts
/// something and then waits for it. The Windows half of `kill_agent_tree`
/// cannot be run from here; this pins the contract it has to meet.
#[cfg(unix)]
#[test]
fn ending_an_agent_ends_the_wrapper_and_what_it_started() {
    use std::os::unix::process::CommandExt as _;

    let directory = tempfile::tempdir().expect("temporary directory");
    let recorded = directory.path().join("real-agent.pid");
    let mut wrapper = std::process::Command::new("/bin/sh")
        .arg("-c")
        .arg(format!(
            "sleep 120 & printf '%s' \"$!\" > {}; wait",
            recorded.display()
        ))
        // What the protocol crate does when it spawns an adapter, and what
        // makes one signal reach all of it.
        .process_group(0)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn()
        .expect("the wrapper starts");

    let agent = wait_for_pid(&recorded);
    assert!(
        process_exists(agent),
        "the agent should be running to begin with"
    );

    kill_agent_tree(wrapper.id());
    let _ = wrapper.wait();

    let deadline = std::time::Instant::now() + Duration::from_secs(10);
    while process_exists(agent) && std::time::Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(50));
    }
    assert!(
        !process_exists(agent),
        "the agent behind the wrapper is still running; \
         killing the wrapper alone orphans it"
    );
}

#[cfg(unix)]
fn wait_for_pid(path: &std::path::Path) -> u32 {
    let deadline = std::time::Instant::now() + Duration::from_secs(10);
    while std::time::Instant::now() < deadline {
        if let Ok(text) = std::fs::read_to_string(path)
            && let Ok(pid) = text.trim().parse::<u32>()
        {
            return pid;
        }
        std::thread::sleep(Duration::from_millis(20));
    }
    panic!("the wrapper never recorded what it started");
}

/// `kill -0` rather than rustix, so this crate keeps `unsafe_code` at
/// `forbid` and the test does not depend on how the signal is sent.
#[cfg(unix)]
fn process_exists(pid: u32) -> bool {
    std::process::Command::new("kill")
        .args(["-0", &pid.to_string()])
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .is_ok_and(|status| status.success())
}

/// `"model".contains("mode")`, which is why a model called `auto` disappeared.
///
/// `is_policy_bearing_option` matched markers as substrings, so an option
/// whose id or category is `model` was classified as a permission control.
/// Several harnesses offer a model named `auto`, and `auto` is on the
/// disallowed-policy list -- so that model was struck from the published
/// options, a `SetSessionConfigOptionRequest` was sent to move off it, and
/// every selection of it came back `model_unavailable`. The turn ran on the
/// harness default and nothing said why.
#[test]
fn a_model_option_is_not_a_permission_control() {
    for (id, category) in [
        ("model", "model"),
        ("model", ""),
        ("", "model"),
        ("modelId", "selection"),
        ("default_model", ""),
    ] {
        assert!(
            !is_policy_bearing_option(id, category),
            "{id:?}/{category:?} is about which model, not about what it may do",
        );
    }

    // And the ones that are, however their harness spells them.
    for (id, category) in [
        ("mode", ""),
        ("permissionMode", ""),
        ("permission_mode", ""),
        ("permission-mode", ""),
        ("", "sandbox"),
        ("approvalPolicy", ""),
        ("", "Approval Policy"),
        ("modes", ""),
    ] {
        assert!(
            is_policy_bearing_option(id, category),
            "{id:?}/{category:?} decides what the agent may do",
        );
    }
}

/// A model named `auto` survives the conversion whole.
#[test]
fn a_harness_offering_an_auto_model_keeps_it() {
    let option: SessionConfigOption = serde_json::from_value(serde_json::json!({
        "id": "model",
        "name": "Model",
        "category": "model",
        "type": "select",
        "currentValue": "auto",
        "options": [
            {"value": "auto", "name": "Auto"},
            {"value": "sonnet", "name": "Sonnet"}
        ]
    }))
    .expect("the option parses");
    let converted = convert_config_option("claude-code", &option).expect("it is published");
    assert_eq!(converted.current_value, serde_json::json!("auto"));
    assert_eq!(
        converted.options.len(),
        2,
        "no value was filtered as a policy the host disallows"
    );
    assert!(
        !converted.metadata.contains_key("hostPolicyDefaultOverride"),
        "nothing was rewritten under the user"
    );
    assert!(selection_is_allowed(&converted, &serde_json::json!("auto")));
}
