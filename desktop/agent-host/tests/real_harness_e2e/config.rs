use super::*;

use lemma_agent_host::protocol::{ConfigOption, MODEL_OPTION_SET_KEY};

/// The option a person picks a reasoning level with. ACP names the category
/// `thought_level`; an adapter that predates it is matched by id.
fn effort_option(options: &[ConfigOption]) -> Option<&ConfigOption> {
    options.iter().find(|option| {
        option.category == "thought_level"
            || ["effort", "reasoning_effort", "thought_level"].contains(&option.id.as_str())
    })
}

fn choice_values(option: &ConfigOption) -> Vec<String> {
    option
        .options
        .iter()
        .filter_map(|choice| choice.get("value").or_else(|| choice.get("id")))
        .filter_map(Value::as_str)
        .map(str::to_owned)
        .collect()
}

/// A model's own options when the probe could read them, else the top level's.
fn options_for_model(probed: &[ConfigOption], model: &str) -> Vec<ConfigOption> {
    probed
        .iter()
        .find(|option| option.category == "model")
        .and_then(|option| {
            option.options.iter().find(|choice| {
                choice
                    .get("value")
                    .or_else(|| choice.get("id"))
                    .and_then(Value::as_str)
                    == Some(model)
            })
        })
        .and_then(|choice| choice.get(MODEL_OPTION_SET_KEY))
        .and_then(|set| serde_json::from_value::<Vec<ConfigOption>>(set.clone()).ok())
        .unwrap_or_else(|| {
            probed
                .iter()
                .filter(|option| option.category != "model")
                .cloned()
                .collect()
        })
}

/// What a person can pick, per agent, from the machine's own catalogue -- and
/// that picking a model other than the default, with a reasoning level other
/// than that model's default, is honoured rather than skipped.
///
/// A selection the agent does not offer is reported as a `config_update` and
/// the turn goes on without it, which is right for a stale profile and wrong
/// for one built from this probe: so any `config_update` here is the failure.
#[tokio::test(flavor = "multi_thread")]
#[ignore = "requires authenticated local ACP harnesses and spends provider quota"]
async fn a_probed_model_and_reasoning_level_are_applied_to_a_real_turn() {
    let paths = HostPaths::under(agent_host_data_directory());
    let manifest = AdapterManifest::builtin()
        .unwrap()
        .with_cache_root(paths.adapters.clone());
    for agent in configured_agents() {
        let scratch = TempDir::new().unwrap();
        let probed = AcpDriver::default()
            .probe(
                manifest.resolve(&agent).unwrap(),
                scratch.path().join("probe"),
            )
            .await
            .unwrap_or_else(|error| panic!("{agent} probe failed: {error:#}"));
        let model = probed
            .config_options
            .iter()
            .find(|option| option.category == "model")
            .unwrap_or_else(|| panic!("{agent} offers no model to choose"));
        let models = choice_values(model);
        let default_model = model.current_value.as_str().unwrap_or_default().to_owned();
        eprintln!("{agent}: models {models:?} (default {default_model})");
        for option in probed
            .config_options
            .iter()
            .filter(|o| o.category != "model")
        {
            eprintln!(
                "{agent}: top-level {} ({}) = {} of {:?}",
                option.id,
                option.category,
                option.current_value,
                choice_values(option)
            );
        }
        for name in &models {
            let effort = effort_option(&options_for_model(&probed.config_options, name))
                .map(|option| (choice_values(option), option.current_value.clone()));
            eprintln!("{agent}:   {name}: effort {effort:?}");
        }
        // A model other than the default that has a reasoning level to pick,
        // so both halves of the choice are exercised in one turn. Never the
        // default model: a turn on it would pass without proving a choice.
        let (chosen_model, effort_id, chosen_effort) = models
            .iter()
            // `default` is a placeholder for whatever the agent picks, not a
            // model: choosing it proves nothing about honouring a choice.
            .filter(|name| **name != default_model && name.as_str() != "default")
            .find_map(|name| {
                let options = options_for_model(&probed.config_options, name);
                let effort = effort_option(&options)?;
                let current = effort.current_value.as_str().unwrap_or_default();
                let value = choice_values(effort)
                    .into_iter()
                    .find(|value| value != current && value != "default")?;
                Some((name.clone(), effort.id.clone(), value))
            })
            .unwrap_or_else(|| {
                panic!("{agent} offers no model besides its default with a reasoning level to pick")
            });
        eprintln!("{agent}: running on {chosen_model} with {effort_id}={chosen_effort}");

        let callbacks = Arc::new(StreamCapture::default());
        let outcome = run_with_deadline(
            AcpRunRequest {
                adapter: manifest.resolve(&agent).unwrap(),
                agent_environment: BTreeMap::default(),
                own_settings: false,
                lemma_cli_bin: None,
                run_spec: RunSpec {
                    agent_run_id: Uuid::new_v4(),
                    conversation_id: Uuid::new_v4(),
                    harness_id: Uuid::new_v4(),
                    profile_revision: "real-config-e2e".to_owned(),
                    model_name: Some(chosen_model.clone()),
                    config_selections: JsonMap::from_iter([(
                        effort_id.clone(),
                        Value::String(chosen_effort.clone()),
                    )]),
                    system_prompt: "Answer in one short sentence.".to_owned(),
                    prompt: vec![json!({"type": "text", "text": "Say ready."})],
                    resume_session_id: None,
                    workspace_cwd: None,
                    context: BTreeMap::new(),
                    mcp: Value::Null,
                    run_deadline: Utc::now() + chrono::Duration::minutes(5),
                    system_prompt_delivery: None,
                    pre_approvals: Vec::new(),
                },
                scratch_directory: scratch.path().join("run"),
                mcp_server: None,
                can_load_session: true,
                published_config_options: probed.config_options.clone(),
                permissions: PermissionGate::new(),
                permission_timeout: Duration::ZERO,
                cancel: lemma_agent_host::acp::never_cancelled(),
                cancel_grace: Duration::from_secs(5),
                steer: lemma_agent_host::acp::SteerInbox::default(),
            },
            callbacks.clone(),
        )
        .await
        .unwrap_or_else(|error| {
            panic!("{agent} on {chosen_model} {effort_id}={chosen_effort} failed: {error:#}")
        });
        assert_eq!(
            outcome.state,
            RunState::Succeeded,
            "{agent} did not succeed"
        );
        let events = callbacks.events.lock().unwrap();
        let skipped: Vec<&JsonMap> = events
            .iter()
            .filter(|(event_type, _)| *event_type == EventType::ConfigUpdate)
            .map(|(_, payload)| payload)
            .collect();
        assert!(
            skipped.is_empty(),
            "{agent} did not apply {chosen_model} / {effort_id}={chosen_effort}: {skipped:?}"
        );
        assert!(
            events
                .iter()
                .any(|(event_type, _)| *event_type == EventType::AgentMessageChunk),
            "{agent} answered nothing on {chosen_model}"
        );
    }
}
