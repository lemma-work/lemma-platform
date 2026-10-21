//! Keeping each `events` frame inside what Lemma reads.
//!
//! Lemma's API takes no websocket message past 1 MiB, and one `events` frame
//! that crossed it would close the link for every run on it. Three bounds keep
//! a frame far inside it:
//!
//! - **Batches are bounded by bytes**, not just count:
//!   `Journal::pending_events` closes a batch before it passes
//!   [`EVENT_BATCH_MAX_BYTES`].
//! - **Attachments go over HTTP.** An image an agent produced (or any inline
//!   `data` / resource `blob` of size) is uploaded as a blob of the run first,
//!   and the event names it -- `{"lemma_blob": {"sha256", "size"}}` in place
//!   of the bytes. See `link::transfer`.
//! - **What is still too big is cut.** An event over [`EVENT_MAX_BYTES`] once
//!   its attachments are out -- a tool's enormous output, say -- has its
//!   longest strings shortened until it fits, and says so. Losing the end of
//!   one string beats losing the run: a frame Lemma refused would be retried,
//!   then dropped with every event of the run after it.
//!
//! Only the copy sent is changed. The journal keeps the event as the agent
//! wrote it, so a replay uploads again (blobs are idempotent) rather than
//! naming a blob that may have expired.

use base64::Engine as _;
use base64::engine::general_purpose::STANDARD;
use serde_json::{Map, Value, json};
use uuid::Uuid;

use crate::link::protocol::{BLOB_FIELD, BLOB_MAX_BYTES, EVENT_MAX_BYTES};
use crate::link::transfer::{TransferClient, TransferError};
use crate::protocol::{Event, EventBatch, JsonMap};

/// Inline data smaller than this, encoded, stays inline: an upload costs more
/// than the bytes it would move.
const INLINE_ATTACHMENT_BYTES: usize = 8 * 1024;

/// What a cut string ends with, so a reader knows it was cut.
const CUT_MARKER: &str = "\n[… cut by Lemma Agent Host to fit the link]";

/// Upload a batch's attachments and cut what is still too large, in place.
///
/// Only a transient upload failure is an error -- the batch is then not sent,
/// and is tried again with the rest of the journal. An attachment Lemma
/// refuses outright is left out of the event instead.
pub(crate) async fn prepare_batch(
    batch: &mut EventBatch,
    blobs: Option<&TransferClient>,
) -> Result<(), TransferError> {
    for event in &mut batch.events {
        if let Some(blobs) = blobs {
            for value in event.payload.values_mut() {
                offload(value, event.run_id, blobs).await?;
            }
        }
        fit(event, EVENT_MAX_BYTES);
    }
    Ok(())
}

/// Replace every inline attachment under `value` with a blob reference.
async fn offload(
    value: &mut Value,
    run_id: Uuid,
    blobs: &TransferClient,
) -> Result<(), TransferError> {
    // Iterative, so a deeply nested payload cannot overflow the stack.
    let mut pending: Vec<&mut Value> = vec![value];
    while let Some(current) = pending.pop() {
        match current {
            Value::Object(fields) => {
                if let Some(field) = attachment_field(fields) {
                    upload(fields, field, run_id, blobs).await?;
                }
                pending.extend(fields.values_mut());
            }
            Value::Array(items) => pending.extend(items.iter_mut()),
            _ => {}
        }
    }
    Ok(())
}

/// Which field of this object is an attachment worth uploading, if any: an
/// `image` or `audio` block's `data`, or a resource's `blob`.
fn attachment_field(fields: &Map<String, Value>) -> Option<&'static str> {
    let large = |field: &str| {
        fields
            .get(field)
            .and_then(Value::as_str)
            .is_some_and(|data| data.len() >= INLINE_ATTACHMENT_BYTES)
    };
    let block_type = fields.get("type").and_then(Value::as_str);
    if matches!(block_type, Some("image" | "audio")) && large("data") {
        return Some("data");
    }
    if fields.get("mimeType").is_some_and(Value::is_string) && large("blob") {
        return Some("blob");
    }
    None
}

async fn upload(
    fields: &mut Map<String, Value>,
    field: &str,
    run_id: Uuid,
    blobs: &TransferClient,
) -> Result<(), TransferError> {
    let Some(Value::String(encoded)) = fields.remove(field) else {
        return Ok(());
    };
    let Ok(bytes) = STANDARD.decode(encoded.as_bytes()) else {
        // Not base64: nothing Lemma could decode either. Left for `fit`.
        fields.insert(field.to_owned(), Value::String(encoded));
        return Ok(());
    };
    if bytes.len() > BLOB_MAX_BYTES {
        tracing::warn!(%run_id, bytes = bytes.len(), "left out an attachment larger than Lemma takes");
        return Ok(());
    }
    let size = bytes.len();
    match blobs.put_blob(run_id, bytes).await {
        Ok(sha256) => {
            fields.insert(
                BLOB_FIELD.to_owned(),
                json!({ "sha256": sha256, "size": size }),
            );
            Ok(())
        }
        Err(error) if error.is_transient() => {
            fields.insert(field.to_owned(), Value::String(encoded));
            Err(error)
        }
        Err(error) => {
            // Refused for good -- the run is no longer this host's, say.
            // Sending it inline would only break the frame instead.
            tracing::warn!(%run_id, %error, "left out an attachment Lemma refused");
            Ok(())
        }
    }
}

/// Cut the event's longest strings until its encoding is at most `limit`.
pub(crate) fn fit(event: &mut Event, limit: usize) {
    loop {
        let size = serde_json::to_string(event).map_or(0, |text| text.len());
        if size <= limit {
            return;
        }
        let excess = size - limit;
        let Some(longest) = longest_string(&mut event.payload) else {
            break;
        };
        // JSON escaping can make the encoded string longer than the text, so
        // cut by what the encoding is over, plus room for the marker.
        let keep = longest.len().saturating_sub(excess + CUT_MARKER.len() + 16);
        let boundary = (0..=keep)
            .rev()
            .find(|index| longest.is_char_boundary(*index))
            .unwrap_or(0);
        longest.truncate(boundary);
        longest.push_str(CUT_MARKER);
    }
    // Nothing left to cut, and still too big: the shape itself is the bulk.
    // Say what happened rather than send what Lemma will not read.
    event.payload = JsonMap::from([(
        "text".to_owned(),
        Value::String("[event too large for the link; left out by Lemma Agent Host]".to_owned()),
    )]);
}

fn longest_string(payload: &mut JsonMap) -> Option<&mut String> {
    let mut longest: Option<&mut String> = None;
    let mut pending: Vec<&mut Value> = payload.values_mut().collect();
    while let Some(current) = pending.pop() {
        match current {
            Value::String(text) => {
                if longest.as_ref().is_none_or(|best| text.len() > best.len()) {
                    longest = Some(text);
                }
            }
            Value::Object(fields) => pending.extend(fields.values_mut()),
            Value::Array(items) => pending.extend(items.iter_mut()),
            _ => {}
        }
    }
    longest.filter(|text| text.len() > CUT_MARKER.len())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::protocol::EventType;

    fn event(payload: Value) -> Event {
        let Value::Object(payload) = payload else {
            panic!("payload must be an object");
        };
        Event {
            run_id: Uuid::nil(),
            lease_epoch: 1,
            sequence: 1,
            event_type: EventType::AgentMessageChunk,
            object_id: None,
            payload: payload.into_iter().collect(),
        }
    }

    #[test]
    fn an_event_that_fits_is_left_alone() {
        let mut small = event(json!({ "text": "hello" }));
        let before = serde_json::to_string(&small).unwrap();
        fit(&mut small, EVENT_MAX_BYTES);
        assert_eq!(serde_json::to_string(&small).unwrap(), before);
    }

    #[test]
    fn an_oversized_string_is_cut_to_fit_and_says_so() {
        let mut big = event(json!({ "text": "x".repeat(3 * EVENT_MAX_BYTES), "kind": "tool" }));
        fit(&mut big, EVENT_MAX_BYTES);
        let encoded = serde_json::to_string(&big).unwrap();
        assert!(encoded.len() <= EVENT_MAX_BYTES, "{}", encoded.len());
        let text = big.payload["text"].as_str().unwrap();
        assert!(text.ends_with(CUT_MARKER));
        assert_eq!(big.payload["kind"], "tool");
    }

    #[test]
    fn escaping_heavy_and_multibyte_text_still_fits() {
        // Each `"` encodes as two bytes and each `é` is two bytes of UTF-8,
        // so a cut by characters alone would land over the limit.
        let text = "\"é".repeat(EVENT_MAX_BYTES);
        let mut big = event(json!({ "content": [{ "type": "text", "text": text }] }));
        fit(&mut big, EVENT_MAX_BYTES);
        assert!(serde_json::to_string(&big).unwrap().len() <= EVENT_MAX_BYTES);
    }

    #[test]
    fn a_payload_of_many_small_strings_still_ends_up_under_the_limit() {
        let many: Vec<Value> = (0..200_000)
            .map(|index| json!(format!("{index}")))
            .collect();
        let mut big = event(json!({ "items": many }));
        fit(&mut big, 64 * 1024);
        assert!(serde_json::to_string(&big).unwrap().len() <= 64 * 1024);
    }

    #[test]
    fn only_large_image_audio_and_resource_payloads_are_attachments() {
        let large = "A".repeat(INLINE_ATTACHMENT_BYTES);
        let as_map = |value: Value| value.as_object().unwrap().clone();
        assert_eq!(
            attachment_field(&as_map(
                json!({ "type": "image", "data": large, "mimeType": "image/png" })
            )),
            Some("data")
        );
        assert_eq!(
            attachment_field(&as_map(json!({ "type": "audio", "data": large }))),
            Some("data")
        );
        assert_eq!(
            attachment_field(&as_map(
                json!({ "uri": "file:///x", "blob": large, "mimeType": "image/png" })
            )),
            Some("blob")
        );
        assert_eq!(
            attachment_field(&as_map(json!({ "type": "image", "data": "AAAA" }))),
            None
        );
        assert_eq!(
            attachment_field(&as_map(json!({ "type": "text", "data": large }))),
            None
        );
    }
}
