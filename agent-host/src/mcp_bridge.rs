//! Adapter-private stdio bridge to a run-scoped Lemma MCP endpoint.

use std::sync::Arc;

use anyhow::Context;
use reqwest::header::{ACCEPT, AUTHORIZATION, CONTENT_TYPE, HeaderMap, HeaderValue};
use serde_json::Value;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use uuid::Uuid;

use crate::adapters::AdapterManifest;
use crate::api::TargetClient;
use crate::config::{HostConfig, HostPaths};
use crate::crypto::SecretVault;
use crate::protocol::McpRoute;

const MAX_MCP_MESSAGE_BYTES: usize = 4 * 1024 * 1024;
const MAX_MCP_RESPONSE_BYTES: usize = 8 * 1024 * 1024;

pub async fn run_bridge(
    paths: &HostPaths,
    target_id: Uuid,
    route_id: Uuid,
    vault: Arc<dyn SecretVault>,
) -> anyhow::Result<()> {
    let config = HostConfig::load_or_create(paths)?;
    config.validate()?;
    let target = config
        .targets
        .iter()
        .find(|target| target.target_id == target_id && target.enabled)
        .cloned()
        .ok_or_else(|| anyhow::anyhow!("MCP bridge target is missing or disabled"))?;
    let manifest = AdapterManifest::builtin()?.with_cache_root(paths.adapters.clone());
    let target_client = TargetClient::new(
        target,
        config.installation_id,
        Uuid::new_v4(),
        &manifest,
        vault.as_ref(),
    )?;
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
        // Resolve on every JSON-RPC request. The control plane performs the
        // run/epoch/expiry/revocation checks, so a credential cached at process
        // startup cannot outlive its lease.
        let endpoint = resolve_endpoint(&target_client, route_id).await?;
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
    if let Some(session_id) = session_id
        && let Ok(endpoint) = resolve_endpoint(&target_client, route_id).await
    {
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

async fn resolve_endpoint(
    target_client: &TargetClient,
    route_id: Uuid,
) -> anyhow::Result<ResolvedEndpoint> {
    let route = target_client.resolve_mcp_route(route_id).await?;
    endpoint_from_route(route_id, &route)
}

fn endpoint_from_route(route_id: Uuid, route: &McpRoute) -> anyhow::Result<ResolvedEndpoint> {
    anyhow::ensure!(route.route_id == route_id, "MCP route ID does not match");
    let object = route
        .mcp
        .as_object()
        .ok_or_else(|| anyhow::anyhow!("MCP route configuration is not an object"))?;
    let url = object
        .get("url")
        .and_then(Value::as_str)
        .ok_or_else(|| anyhow::anyhow!("MCP route is missing url"))?
        .to_owned();
    let authorization = object
        .get("authorization")
        .and_then(Value::as_str)
        .or_else(|| object.get("token").and_then(Value::as_str))
        .ok_or_else(|| anyhow::anyhow!("MCP route is missing authorization"))?
        .to_owned();
    Ok(ResolvedEndpoint {
        url,
        authorization,
        run_id: route.run_id,
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
    use chrono::Utc;
    use serde_json::json;

    use super::*;

    #[test]
    fn route_resolution_keeps_the_run_fence() {
        let route_id = Uuid::new_v4();
        let run_id = Uuid::new_v4();
        let endpoint = endpoint_from_route(
            route_id,
            &McpRoute {
                route_id,
                run_id,
                lease_epoch: 2,
                expires_at: Utc::now(),
                mcp: json!({
                    "url": "https://lemma.example/mcp",
                    "authorization": "Bearer secret"
                }),
            },
        )
        .unwrap();
        assert_eq!(endpoint.run_id, run_id);
        assert_eq!(endpoint.url, "https://lemma.example/mcp");
    }

    #[test]
    fn route_resolution_rejects_a_mismatched_route() {
        let result = endpoint_from_route(
            Uuid::new_v4(),
            &McpRoute {
                route_id: Uuid::new_v4(),
                run_id: Uuid::new_v4(),
                lease_epoch: 1,
                expires_at: Utc::now(),
                mcp: json!({
                    "url": "https://lemma.example/mcp",
                    "authorization": "Bearer secret"
                }),
            },
        );
        assert!(result.is_err());
    }
}
