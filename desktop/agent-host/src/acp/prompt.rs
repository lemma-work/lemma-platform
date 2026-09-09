//! What the agent is asked, and how its updates are read back.

use super::{
    ContentBlock, Digest, Duration, EventType, Future, JsonMap, Map, RunSpec, Sha256, TextContent,
    Value,
};

/// The prompt as ACP content blocks.
///
/// Text is still assembled into one leading block, because that is what every
/// certified adapter has been receiving and splitting it changes how agents
/// read the boundary between the system framing and the user's words.
///
/// Anything that is *not* text is then carried through as itself. It used to be
/// flattened by `extract_text` along with everything else, which for an image
/// or an embedded resource means silently dropped: `PromptRequest` takes a
/// `Vec<ContentBlock>` precisely so those can travel, and a block this build
/// cannot parse falls back to its text rather than disappearing.
pub(crate) fn prompt_blocks(spec: &RunSpec, origin: SessionOrigin) -> Vec<ContentBlock> {
    let mut blocks = Vec::new();
    let text = render_prompt(spec, origin);
    // Only when there is something to say. The system block used to guarantee
    // this was non-empty, and now that it is conditional an image-only turn on
    // a resumed session would otherwise open with an empty text block — which
    // not every adapter accepts.
    if !text.is_empty() {
        blocks.push(ContentBlock::Text(TextContent::new(text)));
    }
    blocks.extend(spec.prompt.iter().filter_map(structured_block));
    blocks
}

/// A non-text content block, or `None` for anything `render_prompt` covered.
pub(crate) fn structured_block(value: &Value) -> Option<ContentBlock> {
    let kind = value.as_object()?.get("type")?.as_str()?;
    if kind == "text" {
        return None;
    }
    match serde_json::from_value::<ContentBlock>(value.clone()) {
        Ok(block) => Some(block),
        Err(error) => {
            tracing::warn!(
                %error,
                kind,
                "dropping a prompt content block this Agent Host cannot represent"
            );
            None
        }
    }
}

/// Whether the session this turn is about to prompt was opened or resumed.
///
/// Not the same question as "did Lemma ask us to resume": a provider is free to
/// forget a session, so a resume Lemma expected can still end in a new one.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum SessionOrigin {
    New,
    Loaded,
}

impl SessionOrigin {
    pub(crate) fn as_str(self) -> &'static str {
        match self {
            Self::New => "new",
            Self::Loaded => "loaded",
        }
    }
}

/// Whether this turn has to carry Lemma's instructions.
///
/// A conversation is one provider session, and the session keeps its own
/// history — so instructions delivered on the turn that opened it are still
/// there on every later turn. Re-sending them each time put another copy of a
/// multi-kilobyte block into the provider's transcript per message, which the
/// model then re-reads in full on every turn after.
///
/// Two independent reasons to send them anyway, and both matter. A new session
/// has never seen them — including the case Lemma cannot predict, where a
/// `session/load` it expected to succeed failed and left us with a fresh
/// session that has neither history nor instructions. And Lemma asks outright
/// when the instructions have changed since the session was told them, because
/// a user can edit an agent mid-conversation.
pub(crate) fn sends_instructions(spec: &RunSpec, origin: SessionOrigin) -> bool {
    origin == SessionOrigin::New || spec.instructions_every_turn()
}

pub(crate) fn render_prompt(spec: &RunSpec, origin: SessionOrigin) -> String {
    let mut sections = Vec::new();
    if sends_instructions(spec, origin) && !spec.system_prompt.trim().is_empty() {
        sections.push(format!(
            "<system>\n{}\n</system>",
            spec.system_prompt.trim()
        ));
    }
    let prompt = spec
        .prompt
        .iter()
        .filter_map(extract_text)
        .collect::<Vec<_>>()
        .join("\n");
    if !prompt.trim().is_empty() {
        sections.push(prompt);
    }
    sections.join("\n\n")
}

pub(crate) fn extract_text(value: &Value) -> Option<String> {
    if let Some(text) = value.as_str() {
        return Some(text.to_owned());
    }
    let object = value.as_object()?;
    if let Some(text) = object.get("text").and_then(Value::as_str) {
        return Some(text.to_owned());
    }
    match object.get("content") {
        Some(Value::String(text)) => Some(text.clone()),
        Some(Value::Array(items)) => Some(
            items
                .iter()
                .filter_map(extract_text)
                .collect::<Vec<_>>()
                .join("\n"),
        ),
        Some(value) => extract_text(value),
        None => None,
    }
}

/// One ACP session update as the event Lemma stores.
///
/// Public so `tests/wire_contract.rs` can hold it to the same shared fixture
/// the backend's tool-call reader is held to. The two halves are separable and
/// both load-bearing: this one promises that nothing an adapter reported is
/// dropped on the way, and the backend's promises that it is read back out of
/// the fields it landed in.
#[must_use]
pub fn normalize_session_update(
    update: &agent_client_protocol::schema::v1::SessionUpdate,
) -> Option<(EventType, Option<String>, JsonMap)> {
    let mut value = serde_json::to_value(update).ok()?;
    let object = value.as_object_mut()?;
    let update_type = object.remove("sessionUpdate")?.as_str()?.to_owned();
    let event_type = match update_type.as_str() {
        "user_message_chunk" => EventType::UserMessage,
        "agent_message_chunk" => EventType::AgentMessageChunk,
        "agent_thought_chunk" => EventType::AgentThoughtChunk,
        "tool_call" => EventType::ToolCallUpsert,
        "tool_call_update" => EventType::ToolCallUpdate,
        "plan" | "plan_update" => EventType::PlanUpsert,
        "usage_update" => EventType::UsageUpdate,
        "config_option_update" | "current_mode_update" => EventType::ConfigUpdate,
        "available_commands_update" | "session_info_update" => EventType::RunState,
        _ => return None,
    };
    if event_type == EventType::ToolCallUpsert {
        // ACP omits its default Pending status during serialization. The
        // backend must still know that absent arguments are not final yet.
        object
            .entry("status")
            .or_insert_with(|| Value::String("pending".to_owned()));
    }
    flatten_content_text(object);
    let object_id = find_string(object, &["toolCallId", "tool_call_id", "id", "contentId"])
        .map(shorten_object_id);
    Some((
        event_type,
        object_id,
        object
            .iter()
            .map(|(key, value)| (key.clone(), value.clone()))
            .collect(),
    ))
}

/// How long each request made before the prompt may take.
///
/// `initialize`, `session/new` and `session/load` are the three round trips
/// between spawning an agent and dispatching the user's prompt, and none of
/// them had a deadline of its own. An adapter that hangs on one -- Claude Code
/// waiting on a TTY for onboarding is the shape that has actually been seen --
/// held its capacity permit for the run's entire deadline, up to an hour, while
/// the conversation showed nothing at all. Failing here is safe: it is before
/// `before_prompt`, so nothing has been dispatched and the run is retryable.
pub(crate) const SETUP_REQUEST_TIMEOUT: Duration = Duration::from_secs(30);

/// Bound one of those requests.
pub(crate) async fn before_prompt_deadline<T>(
    what: &str,
    request: impl Future<Output = Result<T, agent_client_protocol::schema::v1::Error>>,
) -> Result<T, agent_client_protocol::schema::v1::Error> {
    match tokio::time::timeout(SETUP_REQUEST_TIMEOUT, request).await {
        Ok(result) => result,
        Err(_) => Err(
            agent_client_protocol::schema::v1::Error::internal_error().data(format!(
                "the agent did not answer {what} within {}s",
                SETUP_REQUEST_TIMEOUT.as_secs()
            )),
        ),
    }
}

/// The backend stores an event's `object_id` in a 255-character column, and
/// nothing stopped an adapter's tool-call id from being longer.
///
/// The cost was out of all proportion to the cause: the batch carrying that id
/// is refused as malformed, the host reads a refusal as the run's own fault,
/// replays once, and then discards the whole transcript. One verbose id from an
/// adapter and the user's entire conversation turn disappears.
///
/// Truncating alone would collide -- ids that share a long prefix are exactly
/// the shape adapters generate -- so the tail becomes a hash of the original.
/// The id only has to be stable and unique within a run, which this is.
pub(crate) fn shorten_object_id(id: String) -> String {
    const LIMIT: usize = 255;
    if id.len() <= LIMIT {
        return id;
    }
    let digest = Sha256::digest(id.as_bytes());
    let suffix = format!("-{digest:x}");
    // Cut the kept prefix on a character boundary, so a multi-byte id does not
    // panic here on its way to being reported.
    let mut keep = LIMIT - suffix.len();
    while keep > 0 && !id.is_char_boundary(keep) {
        keep -= 1;
    }
    format!("{}{suffix}", &id[..keep])
}

pub(crate) fn flatten_content_text(object: &mut Map<String, Value>) {
    let text = object
        .get("content")
        .and_then(|content| {
            content
                .as_object()
                .and_then(|content| content.get("text"))
                .and_then(Value::as_str)
        })
        .map(str::to_owned);
    if let Some(text) = text {
        object.insert("text".to_owned(), Value::String(text));
    }
    if let Some(Value::Object(tool_call)) = object.get("toolCall").cloned() {
        for (key, value) in tool_call {
            object.entry(key).or_insert(value);
        }
    }
}

/// A blank value counts as absent.
///
/// An adapter may send `toolCallId: ""`. Treating that as a real id collapses
/// every permission request in a run onto one gate key, so concurrent prompts
/// merge into one approval card and only one of them can ever be answered -
/// the other blocks until its timeout and the run never terminalises.
pub(crate) fn find_string(object: &Map<String, Value>, keys: &[&str]) -> Option<String> {
    keys.iter().find_map(|key| {
        object
            .get(*key)
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(str::to_owned)
    })
}
