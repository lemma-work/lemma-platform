//! Adapter-private stdio bridge to a run-scoped Lemma MCP endpoint.

use anyhow::Context;
use reqwest::header::{ACCEPT, AUTHORIZATION, CONTENT_TYPE, HeaderMap, HeaderValue};
use serde_json::Value;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use uuid::Uuid;

use crate::config::HostPaths;
use crate::journal::Journal;

const MAX_MCP_MESSAGE_BYTES: usize = 4 * 1024 * 1024;
const MAX_MCP_RESPONSE_BYTES: usize = 8 * 1024 * 1024;

/// How many times one MCP call is attempted before its error is handed to the
/// agent.
///
/// Four, not "until it works": an agent waiting on a tool has a run deadline,
/// and a Lemma that is genuinely down is better reported to the model -- which
/// can say so, or work around it -- than hidden behind a bridge that stalls.
const MAX_MCP_ATTEMPTS: u32 = 4;
const MCP_RETRY_MIN: std::time::Duration = std::time::Duration::from_millis(400);
const MCP_RETRY_MAX: std::time::Duration = std::time::Duration::from_secs(8);

pub async fn run_bridge(paths: &HostPaths, target_id: Uuid, run_id: Uuid) -> anyhow::Result<()> {
    // The run's MCP configuration was delivered inline with the start command
    // and journaled durably before dispatch, so the bridge is fully local.
    // Cancellation reaches it through the supervised process tree, and the
    // Lemma MCP endpoint re-validates the run-scoped token on every request.
    //
    // Note this bridge only ever *answers*: the Lemma MCP server is stateless
    // and JSON-only by construction (`app/mcp_server.py`), because a stateful
    // session lives in one replica's memory and a follow-up landing on another
    // 404s. There is therefore no server-initiated MCP traffic to carry, and
    // adding a persistent stream here would have nothing to receive.
    let journal = Journal::open(&paths.journal)?;
    let load_endpoint = || -> anyhow::Result<ResolvedEndpoint> {
        let run = journal
            .get_run(target_id, run_id)?
            .ok_or_else(|| anyhow::anyhow!("MCP bridge run is missing from the journal"))?;
        endpoint_from_mcp(run_id, &run.spec.mcp)
    };
    // Read once up front so a misconfigured run fails immediately rather than
    // on its first tool call.
    let mut endpoint = load_endpoint()?;
    let http = reqwest::Client::builder()
        .connect_timeout(std::time::Duration::from_secs(10))
        .timeout(std::time::Duration::from_secs(90))
        .user_agent(format!("lemma-agent-host-mcp/{}", crate::HOST_RELEASE))
        .build()?;
    let mut session_id: Option<String> = None;
    let mut protocol_version = "2025-06-18".to_owned();
    let stdin = tokio::io::stdin();
    let mut lines = BufReader::new(stdin).lines();
    let mut stdout = tokio::io::stdout();
    while let Some(line) = lines.next_line().await? {
        if line.len() > MAX_MCP_MESSAGE_BYTES {
            anyhow::bail!("MCP input exceeded the {MAX_MCP_MESSAGE_BYTES} byte limit");
        }
        let request: Value = serde_json::from_str(&line).context("MCP input was not valid JSON")?;
        // Re-read before every call, because this is how a refreshed credential
        // reaches us: Lemma sends REFRESH_CREDENTIAL, the supervisor journals
        // it, and we are a separate process with nothing else listening. A
        // local SQLite read per tool call is cheap next to the HTTP round trip
        // it precedes.
        match load_endpoint() {
            Ok(current) => endpoint = current,
            // Keep using the endpoint we already hold. A journal read failing
            // mid-run is not a reason to break tools that are still working.
            Err(error) => {
                tracing::warn!(%error, "could not re-read the run's MCP configuration");
            }
        }
        if request.get("method").and_then(Value::as_str) == Some("initialize")
            && let Some(version) = request
                .pointer("/params/protocolVersion")
                .and_then(Value::as_str)
        {
            version.clone_into(&mut protocol_version);
        }
        // Every attempt for one call, so a transient failure is survivable
        // rather than fatal. This loop replaces a bare `bail!` on any non-2xx,
        // which killed the whole bridge process -- and with it every Lemma tool
        // the agent had -- on a single 502 from a backend that was restarting.
        let mut attempt = 1;
        let mut backoff = MCP_RETRY_MIN;
        let mut reloaded_credential = false;
        let frames = loop {
            let attempted = exchange(
                &http,
                &endpoint,
                &request,
                &protocol_version,
                session_id.as_deref(),
            )
            .await;
            match attempted {
                Ok(exchanged) => {
                    if let Some(value) = exchanged.session_id {
                        session_id = Some(value);
                    }
                    // A dead run token does not arrive as 401. The MCP server
                    // only rejects an *empty* bearer at the transport; real
                    // authorization happens inside the JSON-RPC handler, so an
                    // expired one comes back as HTTP 200 carrying a JSON-RPC
                    // error. Nothing about the status could ever have seen it.
                    if !reloaded_credential && frames_report_unauthorized(&exchanged.frames) {
                        reloaded_credential = true;
                        match load_endpoint() {
                            Ok(current) => {
                                tracing::info!(
                                    "Lemma refused the run credential; retrying with the \
                                     journalled one"
                                );
                                endpoint = current;
                                continue;
                            }
                            Err(error) => {
                                tracing::warn!(
                                    %error,
                                    "could not re-read the run's MCP credential"
                                );
                            }
                        }
                    }
                    break Some(exchanged.frames);
                }
                Err(failure) => {
                    if failure.retryable && attempt < MAX_MCP_ATTEMPTS {
                        tracing::warn!(
                            attempt,
                            error = %failure.error,
                            "Lemma MCP call failed; retrying"
                        );
                        tokio::time::sleep(backoff).await;
                        backoff = (backoff * 2).min(MCP_RETRY_MAX);
                        attempt += 1;
                        continue;
                    }
                    // The agent is told, and the bridge lives. Returning the
                    // failure as this call's JSON-RPC error is what lets the
                    // model see a tool that failed instead of a toolset that
                    // vanished; a closed stdio pipe says nothing at all.
                    tracing::warn!(
                        attempts = attempt,
                        error = %failure.error,
                        "Lemma MCP call failed; reporting it to the agent"
                    );
                    break jsonrpc_error_frame(&request, &failure.error).map(|frame| vec![frame]);
                }
            }
        };
        let Some(frames) = frames else {
            continue;
        };
        for frame in frames {
            stdout.write_all(frame.as_bytes()).await?;
            stdout.write_all(b"\n").await?;
        }
        stdout.flush().await?;
    }
    if let Some(session_id) = session_id {
        let mut headers = HeaderMap::new();
        headers.insert(
            AUTHORIZATION,
            HeaderValue::from_str(normalize_authorization(&endpoint.authorization)?)?,
        );
        headers.insert("mcp-session-id", HeaderValue::from_str(&session_id)?);
        headers.insert(
            "x-lemma-agent-run-id",
            HeaderValue::from_str(&endpoint.run_id.to_string())?,
        );
        let _ = http.delete(&endpoint.url).headers(headers).send().await;
    }
    Ok(())
}

struct ResolvedEndpoint {
    url: String,
    authorization: String,
    run_id: Uuid,
}

/// One completed HTTP exchange: the JSON-RPC frames it produced, and any
/// session id the server minted along the way.
struct Exchanged {
    frames: Vec<String>,
    session_id: Option<String>,
}

/// A failed exchange, and whether trying it again could help.
struct ExchangeFailure {
    error: String,
    retryable: bool,
}

/// Post one JSON-RPC message and read back whatever frames it produced.
///
/// A `202 Accepted` -- the answer to a notification -- yields no frames rather
/// than an error, which is how a notification stays a notification.
async fn exchange(
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
fn parse_frames(bytes: &[u8], is_sse: bool) -> anyhow::Result<Vec<String>> {
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
fn frames_report_unauthorized(frames: &[String]) -> bool {
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
fn jsonrpc_error_frame(request: &Value, detail: &str) -> Option<String> {
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

fn endpoint_from_mcp(run_id: Uuid, mcp: &Value) -> anyhow::Result<ResolvedEndpoint> {
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

fn normalize_authorization(value: &str) -> anyhow::Result<&str> {
    anyhow::ensure!(
        !value.contains(['\r', '\n']),
        "MCP authorization contains forbidden characters"
    );
    Ok(value)
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn endpoint_uses_the_run_scoped_configuration() {
        let run_id = Uuid::new_v4();
        let endpoint = endpoint_from_mcp(
            run_id,
            &json!({
                "url": "https://lemma.example/mcp",
                "authorization": "Bearer secret"
            }),
        )
        .unwrap();
        assert_eq!(endpoint.run_id, run_id);
        assert_eq!(endpoint.url, "https://lemma.example/mcp");
    }

    #[test]
    fn endpoint_rejects_a_missing_credential() {
        let result =
            endpoint_from_mcp(Uuid::new_v4(), &json!({"url": "https://lemma.example/mcp"}));
        assert!(result.is_err());
    }
}
