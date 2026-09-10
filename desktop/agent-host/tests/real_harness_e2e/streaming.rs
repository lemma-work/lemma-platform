//! Real harnesses answering over ACP, and the session behind a
//! conversation.

use super::*;

#[tokio::test]
#[ignore = "requires authenticated local agents and spends real provider quota"]
async fn authenticated_harnesses_stream_real_answers_over_acp() {
    let paths = HostPaths::under(agent_host_data_directory());
    let manifest = AdapterManifest::builtin()
        .unwrap()
        .with_cache_root(paths.adapters.clone());

    if std::env::var_os("LEMMA_REAL_AGENT_E2E_SKIP_DIRECT").is_none() {
        for agent in configured_agents() {
            let marker = format!("LEMMA_{}_STREAM_OK", agent.replace('-', "_").to_uppercase());
            let callbacks = std::sync::Arc::new(StreamCapture::default());
            let run_id = Uuid::new_v4();
            let outcome = run_with_deadline(
                AcpRunRequest {
                    adapter: manifest.resolve(&agent).unwrap(),
                    run_spec: RunSpec {
                        agent_run_id: run_id,
                        conversation_id: Uuid::new_v4(),
                        harness_id: Uuid::new_v4(),
                        profile_revision: "real-harness-e2e".to_owned(),
                        model_name: None,
                        config_selections: JsonMap::new(),
                        system_prompt: "Follow the user's output format exactly.".to_owned(),
                        prompt: vec![serde_json::json!({
                            "type": "text",
                            "text": format!("Reply with exactly: {marker}"),
                        })],
                        resume_session_id: None,
                        workspace_cwd: None,
                        context: BTreeMap::new(),
                        mcp: Value::Null,
                        run_deadline: chrono::Utc::now() + chrono::Duration::minutes(5),
                        system_prompt_delivery: None,
                    },
                    scratch_directory: paths
                        .root
                        .join("real-e2e")
                        .join(agent.as_str())
                        .join(run_id.to_string()),
                    mcp_server: None,
                    can_load_session: false,
                    published_config_options: Vec::new(),
                    permissions: PermissionGate::new(),
                    permission_timeout: Duration::ZERO,
                    cancel: lemma_agent_host::acp::never_cancelled(),
                    cancel_grace: Duration::from_secs(5),
                },
                callbacks.clone(),
            )
            .await
            .unwrap_or_else(|error| panic!("{agent} ACP run failed: {error:#}"));

            assert_eq!(
                outcome.state,
                RunState::Succeeded,
                "{agent} did not succeed"
            );
            assert!(
                callbacks.session_started.load(Ordering::SeqCst),
                "{agent} never accepted an ACP session"
            );

            let events = callbacks.events.lock().unwrap();
            let answer = events
                .iter()
                .filter(|(event_type, _)| *event_type == EventType::AgentMessageChunk)
                .filter_map(|(_, payload)| payload.get("text").and_then(Value::as_str))
                .collect::<String>();
            assert!(
                answer.contains(&marker),
                "{agent} did not stream the marker in assistant-message chunks; answer={answer:?}"
            );
            assert!(
                events
                    .iter()
                    .any(|(event_type, _)| *event_type == EventType::AgentMessageChunk),
                "{agent} returned no streamed assistant-message event"
            );
            println!("{agent}: {marker} ({})", outcome.provider_session_id);
        }
    }

    for agent in configured_agents() {
        run_through_paired_agent_host(&paths, &agent).await;
    }
}

/// One Lemma conversation is one provider session.
///
/// Without this the agent meets the user again on every message: it never sees
/// what it just said, so it re-asks answered questions and contradicts itself.
/// The check is deliberately behavioural rather than structural — that
/// `session/load` was sent proves nothing if the provider ignored it.
#[tokio::test]
#[ignore = "requires authenticated local agents and spends real provider quota"]
async fn a_conversation_keeps_one_provider_session_across_turns() {
    let paths = HostPaths::under(agent_host_data_directory());
    let manifest = AdapterManifest::builtin()
        .unwrap()
        .with_cache_root(paths.adapters.clone());

    for agent in configured_agents() {
        let conversation_id = Uuid::new_v4();
        let workspace = conversation_workspace(&paths, &agent, conversation_id);
        let (session_id, _) = one_turn(
            &manifest,
            &workspace,
            &agent,
            conversation_id,
            "My name is Ada. Remember it.",
            None,
        )
        .await;

        let started = std::time::Instant::now();
        let (resumed_session_id, answer) = one_turn(
            &manifest,
            &workspace,
            &agent,
            conversation_id,
            "What is my name? Reply with just the name.",
            Some(session_id.clone()),
        )
        .await;
        let elapsed = started.elapsed();

        assert!(
            answer.to_lowercase().contains("ada"),
            "{agent} lost the conversation across turns; answer={answer:?}"
        );
        assert_eq!(
            resumed_session_id, session_id,
            "{agent} answered in a different session than the one it was asked to resume"
        );
        // Not a benchmark — a regression alarm. Resolving the adapter used to
        // re-hash the npm package on every run, which put ~5s of pure host
        // overhead in front of a model that answers this in one or two.
        assert!(
            elapsed < Duration::from_secs(30),
            "{agent} took {elapsed:?} for a warm follow-up turn"
        );
        println!("{agent}: resumed {session_id} in {elapsed:?} -> {answer:?}");
    }
}

/// A session the provider no longer has costs history, never the answer.
///
/// Providers expire sessions on their own schedule — Codex prunes rollout
/// files, a Claude Code session can be deleted from disk — so a stored id going
/// stale is normal operation, not an error. If a failed `session/load` failed
/// the run, a conversation would become permanently unusable the day its
/// provider forgot it.
#[tokio::test]
#[ignore = "requires authenticated local agents and spends real provider quota"]
async fn a_session_the_provider_has_forgotten_still_answers() {
    let paths = HostPaths::under(agent_host_data_directory());
    let manifest = AdapterManifest::builtin()
        .unwrap()
        .with_cache_root(paths.adapters.clone());

    for agent in configured_agents() {
        let conversation_id = Uuid::new_v4();
        let (session_id, answer) = one_turn(
            &manifest,
            &conversation_workspace(&paths, &agent, conversation_id),
            &agent,
            conversation_id,
            "Reply with exactly: LEMMA_FALLBACK_OK",
            Some(Uuid::new_v4().to_string()),
        )
        .await;
        assert!(
            answer.contains("LEMMA_FALLBACK_OK"),
            "{agent} did not answer after a failed resume; answer={answer:?}"
        );
        assert!(
            !session_id.is_empty(),
            "{agent} answered without reporting a session to store"
        );
        println!("{agent}: fell back to a new session {session_id}");
    }
}

/// A run that cannot resume a provider session must still know the conversation.
///
/// Lemma sends only the latest message when it knows the agent can resume. When
/// it cannot — a harness with no `loadSession`, or a session the provider has
/// since forgotten — the prompt carries the conversation instead. That rendering
/// is only worth anything if a real agent can actually use it, which is what
/// this checks: a fresh session, no resume id, and a question that can only be
/// answered from the history in the prompt.
#[tokio::test]
#[ignore = "requires authenticated local agents and spends real provider quota"]
async fn a_real_agent_answers_from_history_carried_in_the_prompt() {
    let paths = HostPaths::under(agent_host_data_directory());
    let manifest = AdapterManifest::builtin()
        .unwrap()
        .with_cache_root(paths.adapters.clone());

    for agent in configured_agents() {
        let (_, answer) = one_turn(
            &manifest,
            &conversation_workspace(&paths, &agent, Uuid::new_v4()),
            &agent,
            Uuid::new_v4(),
            // Exactly the shape `remote_payload._render_history` produces.
            "USER:\nMy favourite colour is teal.\n\n\
             ASSISTANT:\nNoted, teal it is.\n\n\
             USER:\nWhat is my favourite colour? Reply with only the colour.",
            None,
        )
        .await;

        assert!(
            answer.to_lowercase().contains("teal"),
            "{agent} could not use the history the prompt carried; it answered \
             {answer:?}"
        );
        println!("{agent}: answered from prompt-carried history");
    }
}
