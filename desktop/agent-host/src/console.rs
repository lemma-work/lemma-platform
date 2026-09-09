//! Printing a run's events to a terminal.

use lemma_agent_host::acp::AcpCallbacks;
use lemma_agent_host::protocol::{EventType, JsonMap};
use serde_json::Value;

pub(crate) struct ConsoleCallbacks {
    pub(crate) json: bool,
}

impl AcpCallbacks for ConsoleCallbacks {
    fn before_prompt(&self, provider_session_id: &str) -> anyhow::Result<()> {
        if self.json {
            println!(
                "{}",
                serde_json::json!({
                    "kind": "session",
                    "provider_session_id": provider_session_id,
                })
            );
        } else {
            eprintln!("ACP session: {provider_session_id}");
        }
        Ok(())
    }

    fn event(
        &self,
        event_type: EventType,
        object_id: Option<String>,
        payload: JsonMap,
    ) -> anyhow::Result<()> {
        if self.json {
            println!(
                "{}",
                serde_json::json!({
                    "kind": "event",
                    "event_type": event_type,
                    "object_id": object_id,
                    "payload": payload,
                })
            );
        } else if event_type == EventType::AgentMessageChunk
            && let Some(text) = payload.get("text").and_then(Value::as_str)
        {
            print!("{text}");
        }
        Ok(())
    }
}
