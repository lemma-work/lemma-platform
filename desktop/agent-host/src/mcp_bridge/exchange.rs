//! One request to the workspace's MCP endpoint, and the frames it answers with.

use super::{
    ACCEPT, AUTHORIZATION, CONTENT_TYPE, Context, HeaderMap, HeaderValue, MAX_MCP_RESPONSE_BYTES,
    Uuid, Value,
};

pub(crate) struct ResolvedEndpoint {
    pub(crate) url: String,
    pub(crate) authorization: String,
    pub(crate) run_id: Uuid,
}

/// One completed HTTP exchange: the JSON-RPC frames it produced, and any
/// session id the server minted along the way.
pub(crate) struct Exchanged {
    pub(crate) frames: Vec<String>,
    pub(crate) session_id: Option<String>,
}

/// A failed exchange, and whether trying it again could help.
pub(crate) struct ExchangeFailure {
    pub(crate) error: String,
    pub(crate) retryable: bool,
}

/// Post one JSON-RPC message and read back whatever frames it produced.
///
/// A `202 Accepted` -- the answer to a notification -- yields no frames rather
/// than an error, which is how a notification stays a notification.
pub(crate) async fn exchange(
    http: &reqwest::Client,
    endpoint: &ResolvedEndpoint,
    request: &Value,
    protocol_version: &str,
    session_id: Option<&str>,
) -> Result<Exchanged, ExchangeFailure> {
    let mut headers = HeaderMap::new();
    let authorization = normalize_authorization(&endpoint.authorization)
        .and_then(|value| Ok(HeaderValue::from_str(value)?))
        .map_err(|error: anyhow::Error| ExchangeFailure {
            error: error.to_string(),
            // A malformed credential is not going to become well-formed.
            retryable: false,
        })?;
    headers.insert(AUTHORIZATION, authorization);
    headers.insert(
        ACCEPT,
        HeaderValue::from_static("application/json, text/event-stream"),
    );
    headers.insert(CONTENT_TYPE, HeaderValue::from_static("application/json"));
    if let Ok(value) = HeaderValue::from_str(protocol_version) {
        headers.insert("mcp-protocol-version", value);
    }
    if let Ok(value) = HeaderValue::from_str(&endpoint.run_id.to_string()) {
        headers.insert("x-lemma-agent-run-id", value);
    }
    if let Some(value) = session_id.and_then(|value| HeaderValue::from_str(value).ok()) {
        headers.insert("mcp-session-id", value);
    }
    let response = http
        .post(&endpoint.url)
        .headers(headers)
        .json(request)
        .send()
        .await
        .map_err(|error| ExchangeFailure {
            error: error.to_string(),
            // Connect timeouts, resets, DNS -- the transport is exactly what a
            // second attempt is for.
            retryable: true,
        })?;
    let minted = response
        .headers()
        .get("mcp-session-id")
        .and_then(|value| value.to_str().ok())
        .map(str::to_owned);
    let status = response.status();
    if status == reqwest::StatusCode::ACCEPTED {
        return Ok(Exchanged {
            frames: Vec::new(),
            session_id: minted,
        });
    }
    if !status.is_success() {
        return Err(ExchangeFailure {
            error: format!("Lemma MCP endpoint returned HTTP {status}"),
            // The one policy, shared with the poll loop: a 4xx that is not
            // 401/429/408 is this request's own fault and will fail the same
            // way forever. Everything else -- 5xx, throttling, a restart -- is
            // the target's problem and is worth another attempt.
            retryable: !crate::api::status_is_request_rejected(status),
        });
    }
    let is_sse = response
        .headers()
        .get(CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .is_some_and(|value| value.starts_with("text/event-stream"));
    let bytes = response.bytes().await.map_err(|error| ExchangeFailure {
        error: error.to_string(),
        retryable: true,
    })?;
    if bytes.len() > MAX_MCP_RESPONSE_BYTES {
        return Err(ExchangeFailure {
            error: format!("MCP response exceeded the {MAX_MCP_RESPONSE_BYTES} byte limit"),
            retryable: false,
        });
    }
    parse_frames(&bytes, is_sse)
        .map(|frames| Exchanged {
            frames,
            session_id: minted,
        })
        .map_err(|error| ExchangeFailure {
            error: error.to_string(),
            retryable: false,
        })
}

/// Split a response body into JSON-RPC frames, validating each as it goes.
pub(crate) fn parse_frames(bytes: &[u8], is_sse: bool) -> anyhow::Result<Vec<String>> {
    if !is_sse {
        if bytes.is_empty() {
            return Ok(Vec::new());
        }
        serde_json::from_slice::<Value>(bytes)
            .context("Lemma MCP endpoint returned invalid JSON")?;
        return Ok(vec![String::from_utf8(bytes.to_vec())?]);
    }
    let text = std::str::from_utf8(bytes)?;
    let mut frames = Vec::new();
    for data in text
        .lines()
        .filter_map(|line| line.strip_prefix("data:"))
        .map(str::trim)
        .filter(|line| !line.is_empty())
    {
        serde_json::from_str::<Value>(data)
            .context("Lemma MCP endpoint returned invalid SSE JSON")?;
        frames.push(data.to_owned());
    }
    Ok(frames)
}

/// Whether Lemma answered a well-formed request by refusing the credential.
///
/// Read out of the JSON-RPC body rather than the status, because that is the
/// only place it appears: the MCP server authorizes inside the handler and
/// raises, which `FastMCP` renders as an error object on an HTTP 200.
pub(crate) fn frames_report_unauthorized(frames: &[String]) -> bool {
    frames.iter().any(|frame| {
        serde_json::from_str::<Value>(frame)
            .ok()
            .and_then(|value| {
                value
                    .pointer("/error/message")
                    .and_then(Value::as_str)
                    .map(str::to_ascii_lowercase)
            })
            .is_some_and(|message| message.contains("unauthorized"))
    })
}

/// Turn a failed call into the answer its own request was waiting for.
///
/// `None` for a notification, which has no id and therefore no reply: the
/// agent is not waiting on one, and inventing a frame for it would be a
/// response to a message that never asked.
pub(crate) fn jsonrpc_error_frame(request: &Value, detail: &str) -> Option<String> {
    let id = request.get("id").filter(|id| !id.is_null())?.clone();
    let frame = serde_json::json!({
        "jsonrpc": "2.0",
        "id": id,
        "error": {
            // -32603 (internal error): the request was well-formed and Lemma
            // could not answer it. The agent renders this as a failed tool
            // call, which is the truth and is recoverable.
            "code": -32603,
            "message": format!("Lemma could not be reached for this tool call: {detail}"),
        }
    });
    serde_json::to_string(&frame).ok()
}

pub(crate) fn endpoint_from_mcp(run_id: Uuid, mcp: &Value) -> anyhow::Result<ResolvedEndpoint> {
    let object = mcp
        .as_object()
        .ok_or_else(|| anyhow::anyhow!("run MCP configuration is not an object"))?;
    let url = object
        .get("url")
        .and_then(Value::as_str)
        .ok_or_else(|| anyhow::anyhow!("run MCP configuration is missing url"))?
        .to_owned();
    let authorization = object
        .get("authorization")
        .and_then(Value::as_str)
        .or_else(|| object.get("token").and_then(Value::as_str))
        .ok_or_else(|| anyhow::anyhow!("run MCP configuration is missing authorization"))?
        .to_owned();
    Ok(ResolvedEndpoint {
        url,
        authorization,
        run_id,
    })
}

pub(crate) fn normalize_authorization(value: &str) -> anyhow::Result<&str> {
    anyhow::ensure!(
        !value.contains(['\r', '\n']),
        "MCP authorization contains forbidden characters"
    );
    Ok(value)
}
