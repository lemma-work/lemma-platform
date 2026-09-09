//! The bridge that gives an agent Lemma's own tools over MCP.
//!
//! Was one 619-line file.

//! Adapter-private stdio bridge to a run-scoped Lemma MCP endpoint.

use anyhow::Context;
use reqwest::header::{ACCEPT, AUTHORIZATION, CONTENT_TYPE, HeaderMap, HeaderValue};
use serde_json::Value;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use uuid::Uuid;

use crate::config::HostPaths;
use crate::journal::Journal;

mod exchange;
mod parking;

pub(crate) use exchange::*;
pub(crate) use parking::*;

#[cfg(test)]
mod tests;

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

/// Key Lemma sets on a tool result when it is waiting for a person rather than
/// answering. `ask_user` and `request_approval` return it on an Agent Host run:
/// they cannot end the agent's turn from inside a tool call, so the wait
/// happens here instead.
const PARKED_KEY: &str = "parked_tool_call_id";

/// How long a parked tool response is held open.
///
/// The same half hour the host already gives a native ACP permission request,
/// because it is the same act from the person's side: they are being asked
/// something and the agent is waiting. Matching it keeps one answer to "how
/// long will this sit there" rather than two.
const PARK_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(30 * 60);

/// Gap between polls while parked. Slow on purpose: a person is deciding, and
/// nothing about answering sooner depends on asking more often.
const PARK_POLL_INTERVAL: std::time::Duration = std::time::Duration::from_secs(2);

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
        // Lemma may have answered "waiting for the person" instead of with a
        // result. Hold the tool response open until the decision lands, so the
        // model sits inside its turn exactly as it does for one of its own
        // native permission requests.
        let frames = resolve_parked_frames(&http, &endpoint, frames).await;
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
