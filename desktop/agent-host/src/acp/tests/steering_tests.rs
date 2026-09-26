//! What a host reads as "this agent can be steered", and what it sends.

use super::*;
use crate::protocol::SteerRunPayload;

/// Where the pinned Claude Code and Codex adapters both put it: the top-level
/// `_meta` of `initialize`, beside `agentCapabilities` rather than inside it.
#[test]
fn steering_is_read_from_the_initialize_meta_the_adapters_send() {
    let advertised = serde_json::json!({"steering": {"supported": true}});
    assert!(steering_advertised(advertised.as_object()));

    for absent in [
        serde_json::json!({}),
        serde_json::json!({"steering": {}}),
        serde_json::json!({"steering": {"supported": "yes"}}),
        serde_json::json!({"steering": true}),
    ] {
        assert!(
            !steering_advertised(absent.as_object()),
            "{absent} must not read as steerable"
        );
    }
    assert!(!steering_advertised(None));
}

#[test]
fn a_steer_carries_lemmas_text_and_its_message_id() {
    let steer = Steer::from_payload(&SteerRunPayload {
        message_id: "message-1".into(),
        prompt: vec![serde_json::json!({"type": "text", "text": "Also check the tests."})],
    });

    assert_eq!(steer.message_id, "message-1");
    assert_eq!(
        serde_json::to_value(&steer.prompt).unwrap(),
        serde_json::json!([{"type": "text", "text": "Also check the tests."}])
    );
}

#[test]
fn an_inbox_is_taken_once() {
    let (sender, inbox) = SteerInbox::channel();
    let clone = inbox.clone();
    assert!(inbox.take().is_some());
    assert!(clone.take().is_none(), "two turns must not share one inbox");
    drop(sender);
    assert!(SteerInbox::default().take().is_none());
}
