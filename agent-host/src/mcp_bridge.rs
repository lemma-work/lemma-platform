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

pub async fn run_bridge(paths: &HostPaths, target_id: Uuid, run_id: Uuid) -> anyhow::Result<()> {
    // The run's MCP configuration was delivered inline with the start command
    // and journaled durably before dispatch, so the bridge is fully local.
    // Cancellation reaches it through the supervised process tree, and the
    // Lemma MCP endpoint re-validates the run-scoped token on every request.
    let journal = Journal::open(&paths.journal)?;
    let run = journal
        .get_run(target_id, run_id)?
        .ok_or_else(|| anyhow::anyhow!("MCP bridge run is missing from the journal"))?;
    let endpoint = endpoint_from_mcp(run_id, &run.spec.mcp)?;
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
        if request.get("method").and_then(Value::as_str) == Some("initialize")
            && let Some(version) = request
                .pointer("/params/protocolVersion")
                .and_then(Value::as_str)
        {
            version.clone_into(&mut protocol_version);
        }
        let mut headers = HeaderMap::new();
        headers.insert(
            AUTHORIZATION,
            HeaderValue::from_str(normalize_authorization(&endpoint.authorization)?)?,
        );
        headers.insert(
            ACCEPT,
            HeaderValue::from_static("application/json, text/event-stream"),
        );
        headers.insert(CONTENT_TYPE, HeaderValue::from_static("application/json"));
        headers.insert(
            "mcp-protocol-version",
            HeaderValue::from_str(&protocol_version)?,
        );
        headers.insert(
            "x-lemma-agent-run-id",
            HeaderValue::from_str(&endpoint.run_id.to_string())?,
        );
        if let Some(value) = session_id.as_ref() {
            headers.insert("mcp-session-id", HeaderValue::from_str(value)?);
        }
        let response = http
            .post(&endpoint.url)
            .headers(headers)
            .json(&request)
            .send()
            .await?;
        if let Some(value) = response.headers().get("mcp-session-id") {
            session_id = Some(value.to_str()?.to_owned());
        }
        if response.status() == reqwest::StatusCode::ACCEPTED {
            continue;
        }
        if !response.status().is_success() {
            anyhow::bail!("Lemma MCP endpoint returned HTTP {}", response.status());
        }
        let is_sse = response
            .headers()
            .get(CONTENT_TYPE)
            .and_then(|value| value.to_str().ok())
            .is_some_and(|value| value.starts_with("text/event-stream"));
        let bytes = response.bytes().await?;
        if bytes.len() > MAX_MCP_RESPONSE_BYTES {
            anyhow::bail!("MCP response exceeded the {MAX_MCP_RESPONSE_BYTES} byte limit");
        }
        if is_sse {
            let text = String::from_utf8(bytes.to_vec())?;
            for data in text
                .lines()
                .filter_map(|line| line.strip_prefix("data:"))
                .map(str::trim)
                .filter(|line| !line.is_empty())
            {
                serde_json::from_str::<Value>(data)
                    .context("Lemma MCP endpoint returned invalid SSE JSON")?;
                stdout.write_all(data.as_bytes()).await?;
                stdout.write_all(b"\n").await?;
            }
        } else if !bytes.is_empty() {
            serde_json::from_slice::<Value>(&bytes)
                .context("Lemma MCP endpoint returned invalid JSON")?;
            stdout.write_all(&bytes).await?;
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
