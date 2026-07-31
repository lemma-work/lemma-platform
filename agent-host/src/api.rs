//! Authenticated HTTPS client for a single Lemma target.

use reqwest::{Client, Method, StatusCode};
use serde::Serialize;
use serde::de::DeserializeOwned;
use url::Url;
use uuid::Uuid;

use crate::config::TargetConfig;
use crate::protocol::{
    CommandRejection, EventAck, EventBatch, HarnessPublishRequest, HarnessSnapshot, HostCapacity,
    HostHello, PairingCompleteRequest, PairingCompleteResponse, PollRequest, PollResponse,
    RunCheckpoint,
};

#[derive(Clone, Debug, serde::Deserialize)]
pub struct PublishedHarness {
    pub id: Uuid,
    pub harness_key: String,
    pub adapter_version: String,
    pub config_revision: String,
}

#[derive(Clone, Debug, serde::Deserialize)]
struct HarnessPublishResponse {
    items: Vec<PublishedHarness>,
}

#[derive(Clone)]
pub struct TargetClient {
    target: TargetConfig,
    installation_id: String,
    http: Client,
}

#[derive(Debug, thiserror::Error)]
pub enum ApiError {
    #[error("target URL error: {0}")]
    Url(#[from] url::ParseError),
    #[error("Agent Host HTTP request failed: {0}")]
    Http(#[from] reqwest::Error),
    #[error("Lemma returned HTTP {status}: {body}")]
    Status { status: StatusCode, body: String },
    #[error("target requested Agent Host protocol {0} is unsupported")]
    Protocol(u16),
}

impl ApiError {
    #[must_use]
    pub fn is_unauthorized(&self) -> bool {
        matches!(
            self,
            Self::Status {
                status: StatusCode::UNAUTHORIZED,
                ..
            }
        )
    }

    /// Whether Lemma rejected this request on its own merits rather than
    /// because the host cannot reach or authenticate with the target.
    ///
    /// Only a rejection of the request itself is attributable to the one run it
    /// carried. Everything else -- transport failures, credentials, throttling,
    /// server faults -- is the target's problem and retrying it is the right
    /// response.
    #[must_use]
    pub fn is_request_rejected(&self) -> bool {
        matches!(
            self,
            Self::Status { status, .. }
                if status.is_client_error()
                    && *status != StatusCode::UNAUTHORIZED
                    && *status != StatusCode::TOO_MANY_REQUESTS
                    && *status != StatusCode::REQUEST_TIMEOUT
        )
    }
}

impl TargetClient {
    pub fn new(target: TargetConfig, installation_id: impl Into<String>) -> anyhow::Result<Self> {
        let http = Client::builder()
            .connect_timeout(std::time::Duration::from_secs(10))
            .timeout(std::time::Duration::from_secs(35))
            .user_agent(format!("lemma-agent-host/{}", crate::HOST_RELEASE))
            .build()?;
        Ok(Self {
            target,
            installation_id: installation_id.into(),
            http,
        })
    }

    /// Consume a one-time pairing code and persist the issued host secret.
    pub async fn pair(
        base_url: Url,
        pairing_code: &str,
        display_name: &str,
        installation_id: &str,
        allow_insecure_http: bool,
    ) -> anyhow::Result<TargetConfig> {
        validate_target_url(&base_url, allow_insecure_http)?;
        let request = PairingCompleteRequest {
            pairing_code: pairing_code.to_owned(),
            display_name: display_name.to_owned(),
            hello: HostHello::current(installation_id),
        };
        let client = Client::builder()
            .connect_timeout(std::time::Duration::from_secs(10))
            .timeout(std::time::Duration::from_secs(30))
            .user_agent(format!("lemma-agent-host/{}", crate::HOST_RELEASE))
            .build()?;
        let endpoint = endpoint(&base_url, "agent-host/pairings:complete")?;
        let response = client.post(endpoint).json(&request).send().await?;
        let response: PairingCompleteResponse = decode(response).await?;
        anyhow::ensure!(
            !response.host_secret.is_empty(),
            "server returned an empty host secret"
        );
        Ok(TargetConfig {
            target_id: Uuid::new_v4(),
            name: display_name.to_owned(),
            base_url,
            host_id: response.host_id,
            user_id: response.user_id,
            organization_id: response.organization_id,
            host_secret: response.host_secret,
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
        HostHello::current(&self.installation_id)
    }

    pub async fn poll(
        &self,
        capacity: HostCapacity,
        acknowledged_command_ids: Vec<Uuid>,
        checkpoints: Vec<RunCheckpoint>,
        rejections: Vec<CommandRejection>,
    ) -> Result<PollResponse, ApiError> {
        let request = PollRequest {
            hello: self.hello(),
            capacity,
            acknowledged_command_ids,
            checkpoints,
            rejections,
        };
        let response = self
            .authenticated(Method::POST, "agent-host/poll", Some(&request))
            .await?;
        let result: PollResponse = decode(response).await?;
        if result.protocol_version != crate::PROTOCOL_VERSION {
            return Err(ApiError::Protocol(result.protocol_version));
        }
        Ok(result)
    }

    pub async fn append_events(&self, batch: &EventBatch) -> Result<EventAck, ApiError> {
        let response = self
            .authenticated(Method::POST, "agent-host/events:append", Some(batch))
            .await?;
        decode(response).await
    }

    pub async fn publish_harnesses(
        &self,
        snapshots: Vec<HarnessSnapshot>,
    ) -> Result<Vec<PublishedHarness>, ApiError> {
        let request = HarnessPublishRequest {
            harnesses: snapshots,
        };
        let response = self
            .authenticated(Method::PUT, "agent-host/harnesses", Some(&request))
            .await?;
        let response: HarnessPublishResponse = decode(response).await?;
        Ok(response.items)
    }

    pub async fn revoke(&self) -> Result<(), ApiError> {
        let response = self
            .authenticated::<()>(Method::POST, "agent-host/revoke", None)
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
        let url = endpoint(&self.target.base_url, path)?;
        let mut request = self
            .http
            .request(method, url)
            .bearer_auth(&self.target.host_secret);
        if let Some(body) = body {
            request = request.json(body);
        }
        Ok(request.send().await?)
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
            "agent-host/poll",
        )
        .unwrap();
        assert_eq!(url.as_str(), "https://example.com/api/agent-host/poll");
    }

    #[test]
    fn insecure_network_target_is_rejected() {
        assert!(validate_target_url(&Url::parse("http://example.com").unwrap(), true).is_err());
        validate_target_url(&Url::parse("http://127.0.0.1:8000").unwrap(), true).unwrap();
    }

    fn status(status: StatusCode) -> ApiError {
        ApiError::Status {
            status,
            body: String::new(),
        }
    }

    #[test]
    fn only_a_rejected_request_is_attributable_to_its_run() {
        // A sequence conflict or a missing lease belongs to one run.
        assert!(status(StatusCode::CONFLICT).is_request_rejected());
        assert!(status(StatusCode::NOT_FOUND).is_request_rejected());
        assert!(status(StatusCode::UNPROCESSABLE_ENTITY).is_request_rejected());
        // These say nothing about the run, so the whole target retries.
        assert!(!status(StatusCode::UNAUTHORIZED).is_request_rejected());
        assert!(!status(StatusCode::TOO_MANY_REQUESTS).is_request_rejected());
        assert!(!status(StatusCode::REQUEST_TIMEOUT).is_request_rejected());
        assert!(!status(StatusCode::INTERNAL_SERVER_ERROR).is_request_rejected());
        assert!(!status(StatusCode::BAD_GATEWAY).is_request_rejected());
        assert!(!ApiError::Protocol(2).is_request_rejected());
    }
}
