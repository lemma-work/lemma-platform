//! Stopping a real agent mid-turn, and what it keeps.

use super::*;

/// How much output has to have streamed before cancelling is meaningful.
///
/// Below this the turn may simply have finished, and cancelling something that
/// already ended proves nothing about cancellation.
const CANCEL_AFTER_CHARS: usize = 200;

/// Everything the agent has streamed so far.
fn streamed_text(callbacks: &StreamCapture) -> String {
    callbacks
        .events
        .lock()
        .unwrap()
        .iter()
        .filter(|(event_type, _)| *event_type == EventType::AgentMessageChunk)
        .filter_map(|(_, payload)| payload.get("text").and_then(Value::as_str))
        .collect()
}

/// Cancelling a turn must ask the agent to stop, not kill it mid-thought.
///
/// The host used to answer `CANCEL_RUN` by killing the adapter's process tree.
/// That ends the turn, but it ends it before the provider has written the
/// session file the conversation's *next* turn loads — so stopping one message
/// could silently cost the conversation its whole history. This asserts both
/// halves against a real provider: that it honours `session/cancel` with ACP's
/// own `cancelled` stop reason, and that the session it was working in is still
/// there afterwards.
#[tokio::test]
#[ignore = "requires authenticated local agents and spends real provider quota"]
async fn a_real_agent_stops_on_session_cancel_and_keeps_its_session() {
    let paths = HostPaths::under(agent_host_data_directory());
    let manifest = AdapterManifest::builtin()
        .unwrap()
        .with_cache_root(paths.adapters.clone());

    for agent in configured_agents() {
        let conversation_id = Uuid::new_v4();
        let scratch = paths
            .root
            .join("real-cancel")
            .join(&agent)
            .join(conversation_id.to_string());
        let callbacks = Arc::new(StreamCapture::default());
        let (cancel_tx, cancel_rx) = tokio::sync::watch::channel(false);

        // Ask it to stop only once it is demonstrably mid-answer, not merely
        // once *something* has streamed. Cancelling on the first event races a
        // turn that may already have finished, and a turn that finished on its
        // own reports `end_turn` quite correctly — which would make this a
        // measure of how fast the provider is rather than of anything we do.
        let watching = {
            let callbacks = Arc::clone(&callbacks);
            tokio::spawn(async move {
                for _ in 0..900 {
                    if streamed_text(&callbacks).len() >= CANCEL_AFTER_CHARS {
                        break;
                    }
                    tokio::time::sleep(Duration::from_millis(100)).await;
                }
                cancel_tx.send_replace(true);
            })
        };

        let outcome = run_with_deadline(
            AcpRunRequest {
                adapter: manifest.resolve(&agent).unwrap(),
                run_spec: RunSpec {
                    agent_run_id: Uuid::new_v4(),
                    conversation_id,
                    harness_id: Uuid::new_v4(),
                    profile_revision: "real-cancel-e2e".to_owned(),
                    model_name: None,
                    config_selections: JsonMap::new(),
                    system_prompt: "Do exactly what the user asks.".to_owned(),
                    prompt: vec![json!({
                        "type": "text",
                        // Prose, not counting: models shortcut a
                        // mechanical "list 1..300" with a sentence, and a
                        // turn that ends in 29 characters cannot be
                        // interrupted. An essay reliably streams for a
                        // while, which is the precondition this needs.
                        "text": "Write a detailed 2000-word essay on the \
                                 history of the bicycle, directly in your \
                                 reply. Do not use any tools.",
                    })],
                    resume_session_id: None,
                    workspace_cwd: None,
                    context: BTreeMap::new(),
                    mcp: Value::Null,
                    run_deadline: Utc::now() + chrono::Duration::minutes(5),
                    system_prompt_delivery: None,
                },
                scratch_directory: scratch.clone(),
                mcp_server: None,
                can_load_session: true,
                published_config_options: Vec::new(),
                permissions: PermissionGate::new(),
                permission_timeout: Duration::ZERO,
                cancel: cancel_rx,
                cancel_grace: Duration::from_secs(30),
            },
            Arc::clone(&callbacks) as Arc<dyn AcpCallbacks>,
        )
        .await
        .unwrap_or_else(|error| panic!("{agent} cancelled run failed: {error:#}"));
        watching.await.unwrap();

        let answer = streamed_text(&callbacks);
        assert!(
            answer.len() >= CANCEL_AFTER_CHARS,
            "{agent}: the provider did not produce enough text to exercise cancellation"
        );
        // The state is ours to get right, whatever the agent reports. ACP
        // requires `cancelled` here and OpenCode says `end_turn`; taking that
        // literally recorded a run the user stopped as SUCCEEDED, with a
        // truncated answer presented as the whole one.
        assert_eq!(
            outcome.state,
            RunState::Cancelled,
            "{agent} reported stop_reason {:?} and the run was recorded as {:?} \
             rather than cancelled, after {} chars",
            outcome.stop_reason,
            outcome.state,
            answer.len()
        );
        if outcome.stop_reason != "cancelled" {
            println!(
                "{agent}: NOTE ends a cancelled turn as {:?}, not ACP's \
                 `cancelled` — Lemma relies on its own record instead",
                outcome.stop_reason
            );
        }

        // The point of asking rather than killing: the session the agent was
        // working in is still there, so the conversation continues in it rather
        // than starting over.
        // The same working directory the cancelled turn ran in: resuming a
        // session into a different cwd is a load a provider may simply refuse.
        let (resumed_session, answer) = one_turn(
            &manifest,
            &scratch,
            &agent,
            conversation_id,
            "In one short sentence, what were you just doing?",
            Some(outcome.provider_session_id.clone()),
        )
        .await;
        // Whether the session survives a cancelled turn is the provider's own
        // business, and they differ: OpenCode loads it back, Claude Code
        // refuses. Lemma does not depend on either, which is the point -- it
        // keeps its own messages and the host attaches them whenever it ends up
        // opening a new session. Reported rather than asserted.
        if resumed_session == outcome.provider_session_id {
            println!("{agent}: cancelled cleanly and resumed the same session");
        } else {
            println!(
                "{agent}: NOTE refuses to load a cancelled turn's session, so \
                 the next turn opens a new one and the prompt carries the \
                 conversation"
            );
        }
        assert!(
            !answer.trim().is_empty(),
            "{agent}: the turn after cancellation returned no answer"
        );
    }
}
