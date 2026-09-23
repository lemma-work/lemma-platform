//! The configuration a run sends before its prompt, decided as a whole.

use super::*;
use agent_client_protocol::schema::v1::PermissionOption;
use serde_json::json;

fn option(id: &str, category: &str, values: &[&str]) -> ConfigOption {
    ConfigOption {
        id: id.into(),
        category: category.into(),
        name: id.into(),
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

fn selections(pairs: &[(&str, Value)]) -> JsonMap {
    pairs
        .iter()
        .map(|(key, value)| ((*key).to_owned(), value.clone()))
        .collect()
}

fn written(writes: &[ConfigWrite]) -> Vec<(String, Value)> {
    writes
        .iter()
        .map(|write| {
            (
                write.option_id.clone(),
                serde_json::to_value(&write.value).expect("a config value serializes"),
            )
        })
        .collect()
}

#[test]
fn a_profile_with_nothing_to_say_writes_nothing() {
    let plan = plan_configuration(&[option("model", "model", &["a"])], None, &JsonMap::new())
        .expect("an empty profile is valid");
    assert!(plan.policy_resets.is_empty());
    assert!(matches!(plan.model, ModelChoice::Unrequested));
    assert!(plan.selections.is_empty());
}

#[test]
fn an_offered_model_is_set_on_the_model_option() {
    let options = [option("model-picker", "model", &["sonnet", "opus"])];
    let plan = plan_configuration(&options, Some("opus"), &JsonMap::new()).unwrap();
    let ModelChoice::Set(write) = plan.model else {
        panic!("an offered model is set");
    };
    assert_eq!(
        write.option_id, "model-picker",
        "the option's own id, not its category"
    );
}

/// The turn still has an answer on the harness's own default.
#[test]
fn a_model_not_offered_is_reported_rather_than_failing_the_plan() {
    let options = [option("model", "model", &["sonnet"])];
    let plan = plan_configuration(&options, Some("opus[1m]"), &JsonMap::new())
        .expect("an unavailable model is not a planning error");
    let ModelChoice::Unavailable(payload) = plan.model else {
        panic!("an unoffered model is reported");
    };
    assert_eq!(payload["status"], "model_unavailable");
}

#[test]
fn a_selection_is_matched_by_id_or_by_category() {
    let options = [option("reasoning", "thought_level", &["low", "high"])];
    for key in ["reasoning", "thought_level"] {
        let plan =
            plan_configuration(&options, None, &selections(&[(key, Value::from("high"))])).unwrap();
        assert_eq!(
            written(&plan.selections),
            [("reasoning".to_owned(), json!({"value": "high"}))],
            "selected through {key}"
        );
    }
}

#[test]
fn a_selection_nobody_offers_fails_the_run_by_name() {
    let error = plan_configuration(
        &[option("model", "model", &[])],
        None,
        &selections(&[("sandbox", Value::from("off"))]),
    )
    .err()
    .expect("an unknown option is refused");
    assert!(error.contains("sandbox"), "{error}");
}

#[test]
fn a_model_cannot_be_smuggled_in_as_a_selection() {
    let error = plan_configuration(
        &[option("model", "model", &["opus"])],
        None,
        &selections(&[("model", Value::from("opus"))]),
    )
    .err()
    .expect("the model has its own field");
    assert!(error.contains("model_name"), "{error}");
}

#[test]
fn a_value_outside_the_offered_list_is_refused() {
    let error = plan_configuration(
        &[option("reasoning", "thought_level", &["low", "high"])],
        None,
        &selections(&[("reasoning", Value::from("max"))]),
    )
    .err()
    .expect("only offered values are allowed");
    assert!(error.contains("reasoning"), "{error}");
}

/// Planning is what makes the run fail before it writes anything: a bad
/// selection must not arrive after the model was already set.
#[test]
fn one_bad_selection_fails_the_whole_plan() {
    let options = [
        option("model", "model", &["opus"]),
        option("reasoning", "thought_level", &["low"]),
    ];
    assert!(
        plan_configuration(
            &options,
            Some("opus"),
            &selections(&[("reasoning", Value::from(3))]),
        )
        .is_err(),
        "a number is not a config value"
    );
}

#[test]
fn a_policy_option_left_unrestricted_is_reset_first() {
    let mut mode = option("mode", "mode", &["ask", "auto"]);
    mode.current_value = Value::from("ask");
    mode.metadata
        .insert("hostPolicyDefaultOverride".to_owned(), Value::Bool(true));
    let plan = plan_configuration(
        &[mode, option("model", "model", &[])],
        None,
        &JsonMap::new(),
    )
    .unwrap();
    assert_eq!(
        written(&plan.policy_resets),
        [("mode".to_owned(), json!({"value": "ask"}))]
    );
}

#[test]
fn the_session_s_own_options_win_over_the_probe() {
    let published = vec![option("probed", "model", &["a"])];
    let options = effective_options("codex", None, published.clone(), SessionOrigin::Loaded);
    assert_eq!(
        options
            .iter()
            .map(|option| option.id.as_str())
            .collect::<Vec<_>>(),
        ["probed"],
        "a session that reported nothing falls back to what was published"
    );
    assert!(
        effective_options("codex", Some(Vec::new()), Vec::new(), SessionOrigin::New).is_empty(),
        "nothing reported and nothing published is nothing to validate against"
    );
}

fn permission_options() -> Vec<PermissionOption> {
    vec![
        PermissionOption::new("always", "Always", PermissionOptionKind::AllowAlways),
        PermissionOption::new("once", "Once", PermissionOptionKind::AllowOnce),
        PermissionOption::new("no", "No", PermissionOptionKind::RejectOnce),
    ]
}

fn selected(outcome: &RequestPermissionOutcome) -> Option<String> {
    match outcome {
        RequestPermissionOutcome::Selected(selected) => Some(selected.option_id.to_string()),
        _ => None,
    }
}

#[test]
fn an_allow_selects_the_option_the_person_picked() {
    let outcome = outcome_for_decision(
        PermissionDecision::Allow {
            option_id: "always".into(),
        },
        &permission_options(),
    );
    assert_eq!(selected(&outcome).as_deref(), Some("always"));
}

/// The narrowest reading of "yes" when the picked option has gone.
#[test]
fn an_allow_for_an_option_no_longer_offered_allows_once() {
    let outcome = outcome_for_decision(
        PermissionDecision::Allow {
            option_id: "vanished".into(),
        },
        &permission_options(),
    );
    assert_eq!(selected(&outcome).as_deref(), Some("once"));
}

#[test]
fn a_deny_and_an_agent_offering_no_allow_once_both_cancel() {
    assert!(
        selected(&outcome_for_decision(
            PermissionDecision::Deny,
            &permission_options()
        ))
        .is_none()
    );
    let no_once = vec![PermissionOption::new(
        "no",
        "No",
        PermissionOptionKind::RejectOnce,
    )];
    assert!(selected(&allow_once(&no_once)).is_none());
}
