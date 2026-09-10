//! The session options an adapter offers, and the ones Lemma refuses.

use super::{ConfigOption, JsonMap, SessionConfigOption, SessionConfigOptionValue, Value};

pub(crate) fn convert_config_option(
    adapter_key: &str,
    option: &SessionConfigOption,
) -> Option<ConfigOption> {
    let value = serde_json::to_value(option).ok()?;
    let object = value.as_object()?;
    let option_id = object.get("id")?.as_str()?.to_owned();
    let category = object
        .get("category")
        .and_then(|value| value.as_str())
        .unwrap_or(&option_id)
        .to_owned();
    let name = object
        .get("name")
        .and_then(Value::as_str)
        .unwrap_or(&option_id)
        .to_owned();
    let description = object
        .get("description")
        .and_then(Value::as_str)
        .map(str::to_owned);
    let mut current_value = object.get("currentValue").cloned().unwrap_or(Value::Null);
    let raw_options = object
        .get("options")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|value| {
            value.as_object().map(|object| {
                object
                    .iter()
                    .map(|(key, value)| (key.clone(), value.clone()))
                    .collect()
            })
        })
        .collect::<Vec<JsonMap>>();
    let mut metadata = JsonMap::new();
    if let Some(kind) = object.get("type") {
        metadata.insert("type".to_owned(), kind.clone());
    }
    let policy_bearing = is_policy_bearing_option(&option_id, &category);
    let mut options = raw_options;
    if policy_bearing {
        let original_count = options.len();
        options.retain(|candidate| {
            option_value(candidate).is_none_or(|value| !is_disallowed_policy_value(value))
        });
        let removed = original_count.saturating_sub(options.len());
        if removed > 0 {
            metadata.insert(
                "hostPolicyFilteredValueCount".to_owned(),
                Value::from(removed),
            );
        }
        if is_disallowed_policy_value(&current_value) {
            let replacement = preferred_safe_value(adapter_key, &options)?;
            current_value = replacement;
            metadata.insert("hostPolicyDefaultOverride".to_owned(), Value::Bool(true));
        }
    }
    Some(ConfigOption {
        id: option_id,
        category,
        name,
        description,
        current_value,
        options,
        metadata,
    })
}

/// Whether an option decides what an agent is allowed to do.
///
/// Matched on whole words, not on substrings. `"model".contains("mode")` is
/// true, so an option whose id or category is `model` was classified as a
/// permission control -- and a harness offering a model named `auto`, which
/// several do, then had that model removed from its published list, had a
/// `SetSessionConfigOptionRequest` sent to change away from it, and had every
/// selection of it refused as `model_unavailable`. The turn ran on the
/// harness default and nothing said why.
pub(crate) fn is_policy_bearing_option(option_id: &str, category: &str) -> bool {
    const MARKERS: [&str; 4] = ["mode", "permission", "approval", "sandbox"];
    [option_id, category].iter().any(|value| {
        words(value).any(|word| {
            MARKERS
                .iter()
                .any(|marker| word == *marker || word == format!("{marker}s"))
        })
    })
}

/// The words in an identifier, however it spells them.
///
/// `permission_mode`, `permissionMode`, `permission-mode` and `Permission Mode`
/// are the same two words; an id arrives in whichever shape its harness
/// prefers.
fn words(value: &str) -> impl Iterator<Item = String> + '_ {
    let mut out: Vec<String> = Vec::new();
    let mut current = String::new();
    for character in value.chars() {
        if !character.is_alphanumeric() {
            if !current.is_empty() {
                out.push(std::mem::take(&mut current));
            }
            continue;
        }
        // A capital starts a new word, so `permissionMode` is two.
        if character.is_uppercase() && !current.is_empty() {
            out.push(std::mem::take(&mut current));
        }
        current.extend(character.to_lowercase());
    }
    if !current.is_empty() {
        out.push(current);
    }
    out.into_iter()
}

pub(crate) fn is_disallowed_policy_value(value: &Value) -> bool {
    let Some(value) = value.as_str() else {
        return false;
    };
    let normalized = value
        .chars()
        .filter(char::is_ascii_alphanumeric)
        .flat_map(char::to_lowercase)
        .collect::<String>();
    matches!(
        normalized.as_str(),
        "bypasspermissions"
            | "agentfullaccess"
            | "acceptedits"
            | "auto"
            | "autoapprove"
            | "fullaccess"
            | "unrestricted"
            | "yolo"
            | "dangerouslyskippermissions"
    )
}

pub(crate) fn option_value(option: &JsonMap) -> Option<&Value> {
    option.get("value").or_else(|| option.get("id"))
}

pub(crate) fn preferred_safe_value(adapter_key: &str, options: &[JsonMap]) -> Option<Value> {
    let preferences: &[&str] = match adapter_key {
        "claude-code" => &["default", "plan"],
        "codex" => &["agent", "plan", "read-only"],
        "opencode" => &["build", "plan"],
        "cursor" => &["default", "agent", "plan", "ask"],
        _ => &["default", "plan", "ask", "read-only"],
    };
    preferences
        .iter()
        .find_map(|preference| {
            options
                .iter()
                .filter_map(option_value)
                .find(|value| value.as_str() == Some(*preference))
                .cloned()
        })
        .or_else(|| options.iter().find_map(option_value).cloned())
}

/// Say which model was asked for, and what the harness has instead.
///
/// Naming the alternatives matters more than it looks: the two ways to reach
/// this are an agent renaming its models between releases and a resumed session
/// reporting fewer than it opened with, and the list is what tells those apart
/// for whoever reads the transcript afterwards.
pub(crate) fn model_unavailable_payload(requested: &str, options: &[ConfigOption]) -> JsonMap {
    let offered = options
        .iter()
        .filter(|option| option.category == "model")
        .flat_map(|option| option.options.iter())
        .filter_map(option_value)
        .filter_map(Value::as_str)
        .take(12)
        .collect::<Vec<_>>()
        .join(", ");
    let detail = if offered.is_empty() {
        format!(
            "This agent did not offer {requested}, or any other model, for this \
             turn. It answered on its own default."
        )
    } else {
        format!(
            "This agent no longer offers {requested}. It answered on its own \
             default; it does offer: {offered}."
        )
    };
    let mut payload = JsonMap::new();
    payload.insert("status".to_owned(), Value::from("model_unavailable"));
    payload.insert("requested_model".to_owned(), Value::from(requested));
    payload.insert("detail".to_owned(), Value::from(detail));
    payload
}

pub(crate) fn selection_is_allowed(option: &ConfigOption, selection: &Value) -> bool {
    if is_policy_bearing_option(&option.id, &option.category)
        && is_disallowed_policy_value(selection)
    {
        return false;
    }
    let allowed = option
        .options
        .iter()
        .filter_map(option_value)
        .collect::<Vec<_>>();
    allowed.is_empty() || allowed.contains(&selection)
}

pub(crate) fn session_config_value(
    key: &str,
    selection: &Value,
) -> Result<SessionConfigOptionValue, String> {
    match selection {
        Value::Bool(value) => Ok(SessionConfigOptionValue::boolean(*value)),
        Value::String(value) => Ok(SessionConfigOptionValue::value_id(value.clone())),
        other => Err(format!(
            "unsupported configuration value for {key}: {other}"
        )),
    }
}

/// What Lemma is told when a conversation's provider session is gone.
///
/// Reported rather than raised, like `model_unavailable_payload` above, and
/// for the same reason: the turn still has an answer in it. The difference is
/// that this one is a fact about the answer's quality, so it belongs where a
/// person can see it rather than in a `warn!` on the machine that noticed.
///
/// It used to be exactly that `warn!`. Lemma leaves the conversation's history
/// out of the prompt when it expects the resume to supply it, so a failed load
/// produced a turn answered out of context, with no record anywhere that the
/// context had been lost -- neither the user nor Lemma had any way to tell
/// that reply apart from an ordinary one.
pub(crate) fn session_lost_payload(requested: &str) -> JsonMap {
    let mut payload = JsonMap::new();
    payload.insert("status".to_owned(), Value::from("session_lost"));
    payload.insert("requested_session".to_owned(), Value::from(requested));
    payload.insert(
        "detail".to_owned(),
        Value::from(
            "This agent no longer has the session this conversation was in, so \
             the earlier messages could not be recovered. It answered this turn \
             on its own, and was told that it is missing the conversation.",
        ),
    );
    payload
}
