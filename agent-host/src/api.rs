//! Authenticated HTTPS client for a single Lemma target.

use std::sync::Arc;

use chrono::{Duration as ChronoDuration, Utc};
use reqwest::{Client, Method, StatusCode};
use serde::Serialize;
use serde::de::DeserializeOwned;
use tokio::sync::Mutex;
use url::Url;
use uuid::Uuid;

use crate::adapters::AdapterManifest;
use crate::config::TargetConfig;
use crate::crypto::{DeviceIdentity, SecretVault, random_nonce};
use crate::protocol::{
    EventAck, EventBatch, HostCapacity, HostHello, IntegrationPublishRequest, IntegrationSnapshot,
    McpRoute, PairingCompleteRequest, PairingCompleteResponse, PollRequest, PollResponse,
    RunCheckpoint, TokenExchangeRequest, TokenResponse,
};

#[derive(Clone, Debug, serde::Deserialize)]
pub struct PublishedIntegration {
    pub id: Uuid,
    pub integration_key: String,
    pub adapter_version: String,
    pub config_revision: String,
}

#[derive(Clone, Debug, serde::Deserialize)]
struct IntegrationPublishResponse {
    items: Vec<PublishedIntegration>,
}

#[derive(Clone)]
pub struct TargetClient {
    target: TargetConfig,
    installation_id: String,
    instance_id: Uuid,
    manifest_id: String,
    identity: DeviceIdentity,
    http: Client,
    token: Arc<Mutex<Option<TokenResponse>>>,
}

#[derive(Debug, thiserror::Error)]
pub enum ApiError {
    #[error("target URL error: {0}")]
    Url(#[from] url::ParseError),
    #[error("Agent Host HTTP request failed: {0}")]
    Http(#[from] reqwest::Error),
    #[error("Lemma returned HTTP {status}: {body}")]
    Status { status: StatusCode, body: String },
    #[error("target requested Agent Host protocol {0}, but this host supports only v2")]
    Protocol(u16),
}

impl TargetClient {
    pub fn new(
        target: TargetConfig,
        installation_id: impl Into<String>,
        instance_id: Uuid,
        manifest: &AdapterManifest,
        vault: &dyn SecretVault,
    ) -> anyhow::Result<Self> {
        let identity = DeviceIdentity::load_or_create(vault, target.target_id)?;
        anyhow::ensure!(
            identity.fingerprint() == target.public_key_fingerprint,
            "stored device identity does not match target {}",
            target.name
        );
        let http = Client::builder()
            .connect_timeout(std::time::Duration::from_secs(10))
            .timeout(std::time::Duration::from_secs(35))
            .user_agent(format!("lemma-agent-host/{}", crate::HOST_RELEASE))
            .build()?;
        Ok(Self {
            target,
            installation_id: installation_id.into(),
            instance_id,
            manifest_id: manifest.manifest_id.clone(),
            identity,
            http,
            token: Arc::new(Mutex::new(None)),
        })
    }

    pub async fn pair(
        base_url: Url,
        pairing_code: &str,
        display_name: &str,
        installation_id: &str,
        manifest: &AdapterManifest,
        vault: &dyn SecretVault,
        allow_insecure_http: bool,
    ) -> anyhow::Result<TargetConfig> {
        validate_target_url(&base_url, allow_insecure_http)?;
        let target_id = Uuid::new_v4();
        let identity = DeviceIdentity::load_or_create(vault, target_id)?;
        let nonce = random_nonce();
        let timestamp = Utc::now().timestamp();
        let hello = HostHello::current(&manifest.manifest_id, installation_id, Uuid::new_v4());
        let request = PairingCompleteRequest {
            pairing_code: pairing_code.to_owned(),
            public_key: identity.public_key(),
            display_name: display_name.to_owned(),
            hello,
            nonce: nonce.clone(),
            timestamp,
            signature: identity.sign_pairing(pairing_code, installation_id, timestamp, &nonce),
        };
        let client = Client::builder()
            .connect_timeout(std::time::Duration::from_secs(10))
            .timeout(std::time::Duration::from_secs(30))
            .user_agent(format!("lemma-agent-host/{}", crate::HOST_RELEASE))
            .build()?;
        let endpoint = endpoint(&base_url, "agent-host/v2/pairings:complete")?;
        let response = client.post(endpoint).json(&request).send().await?;
        let response: PairingCompleteResponse = decode(response).await?;
        anyhow::ensure!(
            response.public_key_fingerprint == identity.fingerprint(),
            "server returned a different device-key fingerprint"
        );
        Ok(TargetConfig {
            target_id,
            name: display_name.to_owned(),
            base_url,
            host_id: response.host_id,
            user_id: response.user_id,
            organization_id: response.organization_id,
            public_key_fingerprint: response.public_key_fingerprint,
            enabled: true,
            allow_insecure_http,
            draining: false,
            refresh_generation: 0,
        })
    }

    #[must_use]
    pub fn target(&self) -> &TargetConfig {
        &self.target
    }

    #[must_use]
    pub fn hello(&self) -> HostHello {
        HostHello::current(&self.manifest_id, &self.installation_id, self.instance_id)
    }

    pub async fn poll(
        &self,
        capacity: HostCapacity,
        acknowledged_command_ids: Vec<Uuid>,
        checkpoints: Vec<RunCheckpoint>,
    ) -> Result<PollResponse, ApiError> {
        let request = PollRequest {
            hello: self.hello(),
            capacity,
            acknowledged_command_ids,
            checkpoints,
        };
        let response = self
            .authenticated(Method::POST, "agent-host/v2/poll", Some(&request))
            .await?;
        let result: PollResponse = decode(response).await?;
        if result.protocol_version != crate::PROTOCOL_VERSION {
            return Err(ApiError::Protocol(result.protocol_version));
        }
        Ok(result)
    }

    pub async fn append_events(&self, batch: &EventBatch) -> Result<EventAck, ApiError> {
        let response = self
            .authenticated(Method::POST, "agent-host/v2/events:append", Some(batch))
            .await?;
        decode(response).await
    }

    pub async fn resolve_mcp_route(&self, route_id: Uuid) -> Result<McpRoute, ApiError> {
        let path = format!("agent-host/v2/mcp-routes/{route_id}");
        let response = self.authenticated::<()>(Method::GET, &path, None).await?;
        decode(response).await
    }

    pub async fn publish_integrations(
        &self,
        snapshots: Vec<IntegrationSnapshot>,
    ) -> Result<Vec<PublishedIntegration>, ApiError> {
        let request = IntegrationPublishRequest {
            integrations: snapshots,
        };
        let response = self
            .authenticated(Method::PUT, "agent-host/v2/integrations", Some(&request))
            .await?;
        let response: IntegrationPublishResponse = decode(response).await?;
        Ok(response.items)
    }

    pub async fn revoke(&self) -> Result<(), ApiError> {
        let response = self
            .authenticated::<()>(Method::POST, "agent-host/v2/revoke", None)
            .await?;
        if response.status().is_success() {
            return Ok(());
        }
        Err(status_error(response).await)
    }

    async fn authenticated<T: Serialize + ?Sized>(
        &self,
        method: Method,
        path: &str,
        body: Option<&T>,
    ) -> Result<reqwest::Response, ApiError> {
        let access_token = self.access_token().await?;
        let url = endpoint(&self.target.base_url, path)?;
        let mut request = self
            .http
            .request(method.clone(), url)
            .bearer_auth(&access_token);
        if let Some(body) = body {
            request = request.json(body);
        }
        let response = request.send().await?;
        if response.status() != StatusCode::UNAUTHORIZED {
            return Ok(response);
        }

        *self.token.lock().await = None;
        let access_token = self.access_token().await?;
        let url = endpoint(&self.target.base_url, path)?;
        let mut retry = self.http.request(method, url).bearer_auth(access_token);
        if let Some(body) = body {
            retry = retry.json(body);
        }
        Ok(retry.send().await?)
    }

    async fn access_token(&self) -> Result<String, ApiError> {
        let mut cache = self.token.lock().await;
        if let Some(token) = cache.as_ref()
            && token.expires_at > Utc::now() + ChronoDuration::seconds(30)
        {
            return Ok(token.access_token.clone());
        }
        let nonce = random_nonce();
        let timestamp = Utc::now().timestamp();
        let request = TokenExchangeRequest {
            host_id: self.target.host_id,
            nonce: nonce.clone(),
            timestamp,
            signature: self
                .identity
                .sign_token_exchange(self.target.host_id, timestamp, &nonce),
        };
        let url = endpoint(&self.target.base_url, "agent-host/v2/token:exchange")?;
        let response = self.http.post(url).json(&request).send().await?;
        let token: TokenResponse = decode(response).await?;
        let access_token = token.access_token.clone();
        *cache = Some(token);
        Ok(access_token)
    }
}

pub fn validate_target_url(url: &Url, allow_insecure_http: bool) -> anyhow::Result<()> {
    if url.scheme() == "https" {
        return Ok(());
    }
    anyhow::ensure!(
        allow_insecure_http
            && url.scheme() == "http"
            && matches!(url.host_str(), Some("localhost" | "127.0.0.1" | "::1")),
        "targets must use HTTPS; plain HTTP requires --allow-insecure-http and a loopback host"
    );
    Ok(())
}

fn endpoint(base: &Url, path: &str) -> Result<Url, url::ParseError> {
    let mut base = base.clone();
    if !base.path().ends_with('/') {
        let path_with_slash = format!("{}/", base.path());
        base.set_path(&path_with_slash);
    }
    base.join(path.trim_start_matches('/'))
}

async fn decode<T: DeserializeOwned>(response: reqwest::Response) -> Result<T, ApiError> {
    if response.status().is_success() {
        return Ok(response.json().await?);
    }
    Err(status_error(response).await)
}

async fn status_error(response: reqwest::Response) -> ApiError {
    let status = response.status();
    let body = response
        .text()
        .await
        .unwrap_or_else(|error| format!("<could not read response: {error}>"));
    ApiError::Status { status, body }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn endpoint_preserves_api_prefix() {
        let url = endpoint(
            &Url::parse("https://example.com/api").unwrap(),
            "agent-host/v2/poll",
        )
        .unwrap();
        assert_eq!(url.as_str(), "https://example.com/api/agent-host/v2/poll");
    }

    #[test]
    fn insecure_network_target_is_rejected() {
        assert!(validate_target_url(&Url::parse("http://example.com").unwrap(), true).is_err());
        validate_target_url(&Url::parse("http://127.0.0.1:8000").unwrap(), true).unwrap();
    }
}
