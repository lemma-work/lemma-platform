//! What the agent is asked, and how its updates are read back.

use super::{ContentBlock, Duration, Future, Map, RunSpec, TextContent, Value};

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
    /// Lemma asked for a session the provider no longer has.
    ///
    /// Distinct from `New`, which the fresh session it left us with would
    /// otherwise be indistinguishable from, and the difference is what the
    /// turn is missing. Lemma leaves the conversation's history out of the
    /// prompt precisely when it expects a resume to supply it -- so this is
    /// the one case where the agent is asked to continue a conversation
    /// neither it nor the prompt has any record of.
    Recovered,
}

impl SessionOrigin {
    pub(crate) fn as_str(self) -> &'static str {
        match self {
            Self::New => "new",
            Self::Loaded => "loaded",
            Self::Recovered => "recovered",
        }
    }

    /// Whether this session has never been told anything by Lemma.
    pub(crate) fn is_fresh(self) -> bool {
        matches!(self, Self::New | Self::Recovered)
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
    origin.is_fresh() || spec.instructions_every_turn()
}

/// What an agent is told when the conversation it is continuing is gone.
///
/// Said out loud rather than papered over. Lemma omits the conversation's
/// history from the prompt exactly when it expects `session/load` to supply
/// it, so a load that failed leaves the agent holding this turn's words and
/// nothing else -- and, told nothing, it answers as though it remembers. The
/// user gets a confident reply to a question that referred to something the
/// agent has never seen, with no sign anything went wrong.
///
/// This is the same trade the model-unavailable path makes a few lines further
/// on: report it and answer anyway, because the answer is still worth having.
/// The difference is that this one degrades what the answer can be, so the
/// agent is the one that has to say so.
pub(crate) const LOST_CONVERSATION_NOTE: &str = "<conversation-recovered>\n    The earlier messages in this conversation could not be recovered: the \
    provider no longer has the session they were in. You are seeing this \
    turn's message and nothing before it. If answering needs that earlier \
    context, say so plainly and ask for what you need rather than guessing at \
    it.\n</conversation-recovered>";

pub(crate) fn render_prompt(spec: &RunSpec, origin: SessionOrigin) -> String {
    let mut sections = Vec::new();
    if sends_instructions(spec, origin) && !spec.system_prompt.trim().is_empty() {
        sections.push(format!(
            "<system>\n{}\n</system>",
            spec.system_prompt.trim()
        ));
    }
    // After the instructions and before the user's words: it is a fact about
    // this turn, not part of who the agent is.
    if origin == SessionOrigin::Recovered {
        sections.push(LOST_CONVERSATION_NOTE.to_owned());
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
