//! What the ACP driver reports back while a run is in flight.

use super::{
    AcpCallbacks, Arc, AtomicBool, EventType, Journal, JsonMap, Ordering, RunState, Uuid, Value,
};

pub(crate) struct JournalCallbacks {
    pub(crate) journal: Journal,
    pub(crate) target_id: Uuid,
    pub(crate) run_id: Uuid,
    pub(crate) lease_epoch: u32,
    pub(crate) host_cwd: Option<String>,
    pub(crate) provider_seen: AtomicBool,
    /// Whether the prompt has actually gone out. See `event`.
    pub(crate) dispatched: AtomicBool,
    pub(crate) stream_segments: std::sync::Mutex<StreamSegments>,
    /// Raised whenever this run journals an event, so the poll loop stops
    /// waiting and flushes. Without it a run's output sits in the journal until
    /// the current 25s long poll returns — the agent answered in eight seconds
    /// and the conversation still waited twenty.
    pub(crate) events_ready: Arc<tokio::sync::Notify>,
}

/// How much streamed text is held before it is sealed into an upsert.
///
/// `recover_event` replays multiple upserts correctly, so sealing early costs
/// nothing beyond an extra row.
pub(crate) const STREAM_SEGMENT_SEAL_BYTES: usize = 64 * 1024;

/// Accumulated per-kind streamed text awaiting a full-text upsert.
///
/// Chunks are the cosmetic live lane (the server publishes them without
/// journaling); the upserts synthesized here are the durable, authoritative
/// text records. A segment is sealed and emitted before any event that is not
/// a text chunk of the same kind, so replaying only durable events rebuilds
/// the exact final text.
#[derive(Default)]
pub(crate) struct StreamSegments {
    pub(crate) message: String,
    pub(crate) thought: String,
}

impl StreamSegments {
    pub(crate) fn recover_event(&mut self, event: &crate::protocol::Event) {
        match event.event_type {
            EventType::AgentMessageChunk => self.message.push_str(&chunk_text(&event.payload)),
            EventType::AgentThoughtChunk => self.thought.push_str(&chunk_text(&event.payload)),
            EventType::AgentMessageUpsert => self.message.clear(),
            EventType::AgentThoughtUpsert => self.thought.clear(),
            _ => {}
        }
    }
}

impl JournalCallbacks {
    pub(crate) fn flush_stream_segment(&self, message: bool) -> anyhow::Result<()> {
        let text = {
            let mut segments = self
                .stream_segments
                .lock()
                .expect("stream segments poisoned");
            let segment = if message {
                &mut segments.message
            } else {
                &mut segments.thought
            };
            std::mem::take(segment)
        };
        if text.is_empty() {
            return Ok(());
        }
        let mut payload = JsonMap::new();
        payload.insert("text".to_owned(), Value::String(text));
        self.journal.append_event(
            self.target_id,
            self.run_id,
            self.lease_epoch,
            if message {
                EventType::AgentMessageUpsert
            } else {
                EventType::AgentThoughtUpsert
            },
            None,
            payload,
        )?;
        self.events_ready.notify_one();
        Ok(())
    }

    pub(crate) fn flush_stream_segments(&self) -> anyhow::Result<()> {
        self.flush_stream_segment(true)?;
        self.flush_stream_segment(false)
    }

    pub(crate) fn stream_matches_failure(&self, error: &str) -> anyhow::Result<bool> {
        let expected = error
            .strip_prefix("Internal error: ")
            .unwrap_or(error)
            .trim();
        if expected.is_empty() {
            return Ok(false);
        }
        let mut text = String::new();
        let mut differs = false;
        self.journal
            .visit_run_events(self.target_id, self.run_id, self.lease_epoch, |event| {
                if event.event_type == EventType::AgentMessageUpsert && !differs {
                    let segment = chunk_text(&event.payload);
                    // A reply longer than the error cannot be its duplicate;
                    // avoid retaining an entire long conversation to compare it.
                    if text.len().saturating_add(segment.len()) > expected.len() {
                        differs = true;
                    } else {
                        text.push_str(&segment);
                    }
                }
            })?;
        Ok(!differs && text == expected)
    }
}

impl AcpCallbacks for JournalCallbacks {
    fn before_prompt(&self, provider_session_id: &str) -> anyhow::Result<()> {
        self.dispatched.store(true, Ordering::SeqCst);
        self.journal.mark_dispatch_intent(
            self.target_id,
            self.run_id,
            self.lease_epoch,
            provider_session_id,
        )?;
        let mut detail = JsonMap::from([
            ("state".to_owned(), Value::String("DISPATCHING".to_owned())),
            (
                "provider_session_id".to_owned(),
                Value::String(provider_session_id.to_owned()),
            ),
        ]);
        if let Some(cwd) = &self.host_cwd {
            detail.insert("host_cwd".to_owned(), Value::String(cwd.clone()));
        }
        // Control polling and streaming are independent. Put the binding at
        // the head of this run's event stream so the backend saves it before
        // processing any answer, even while its control poll is waiting.
        self.journal.append_event(
            self.target_id,
            self.run_id,
            self.lease_epoch,
            EventType::RunState,
            None,
            detail,
        )?;
        self.events_ready.notify_one();
        Ok(())
    }

    fn event(
        &self,
        event_type: EventType,
        object_id: Option<String>,
        payload: JsonMap,
    ) -> anyhow::Result<()> {
        // Only once the prompt is actually on its way.
        //
        // Lemma reads a RUNNING checkpoint as proof the prompt landed, and
        // promotes the conversation's pending instructions to delivered on the
        // strength of it. Not every event comes after dispatch: a model the
        // harness will not take is reported as a config update *before*
        // `before_prompt`, and letting that write RUNNING marked the
        // instructions delivered before a prompt existed. A run that then died
        // before dispatch left them skipped for the rest of the conversation.
        if self.dispatched.load(Ordering::SeqCst)
            && !self.provider_seen.swap(true, Ordering::SeqCst)
        {
            self.journal.checkpoint(
                self.target_id,
                self.run_id,
                self.lease_epoch,
                RunState::Running,
                &JsonMap::new(),
            )?;
        }
        match event_type {
            EventType::AgentMessageChunk | EventType::AgentThoughtChunk => {
                let is_message = event_type == EventType::AgentMessageChunk;
                let text = chunk_text(&payload);
                if text.is_empty() && !payload.is_empty() {
                    // Rich content (e.g. an image block) is durable and seals
                    // the current text segment ahead of itself.
                    self.flush_stream_segment(is_message)?;
                } else {
                    let outgrew_segment = {
                        let mut segments = self
                            .stream_segments
                            .lock()
                            .expect("stream segments poisoned");
                        let segment = if is_message {
                            &mut segments.message
                        } else {
                            &mut segments.thought
                        };
                        segment.push_str(&text);
                        segment.len() >= STREAM_SEGMENT_SEAL_BYTES
                    };
                    // Sealed on size as well as on a change of kind. A turn that
                    // only ever streams text never changes kind, so the whole
                    // answer was held in memory, written again in full as one
                    // upsert row, and sent twice -- once as chunks and once as
                    // that row. Long answers are exactly when that hurts.
                    if outgrew_segment {
                        self.flush_stream_segment(is_message)?;
                    }
                }
                self.journal.append_event(
                    self.target_id,
                    self.run_id,
                    self.lease_epoch,
                    event_type,
                    object_id,
                    payload,
                )?;
                self.events_ready.notify_one();
            }
            _ => {
                self.flush_stream_segments()?;
                self.journal.append_event(
                    self.target_id,
                    self.run_id,
                    self.lease_epoch,
                    event_type,
                    object_id,
                    payload,
                )?;
                self.events_ready.notify_one();
            }
        }
        Ok(())
    }
}

/// Extract streamed text the same way the backend normalizer does.
///
/// Public so `tests/wire_contract.rs` can hold it to the same shared fixture
/// the backend's `event_text` is held to. The two accumulate the same stream
/// into separate buffers that are reconciled at every segment boundary, so a
/// disagreement between them does not raise anything — it silently truncates a
/// persisted message.
pub fn chunk_text(payload: &JsonMap) -> String {
    for key in ["text", "delta"] {
        if let Some(text) = payload.get(key).and_then(Value::as_str) {
            return text.to_owned();
        }
    }
    match payload.get("content") {
        Some(Value::String(text)) => text.clone(),
        Some(Value::Object(content)) => content
            .get("text")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_owned(),
        _ => String::new(),
    }
}

pub(crate) fn redact_error(value: &str) -> String {
    let mut redacted = value.to_owned();
    for marker in ["Bearer ", "authorization", "token="] {
        if let Some(index) = redacted
            .to_ascii_lowercase()
            .find(&marker.to_ascii_lowercase())
        {
            redacted.truncate(index);
            redacted.push_str("[redacted]");
        }
    }
    redacted.chars().take(2048).collect()
}
