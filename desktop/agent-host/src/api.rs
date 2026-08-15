//! Authenticated HTTPS client for a single Lemma target.

use reqwest::{Client, Method, StatusCode};
use serde::Serialize;
use serde::de::DeserializeOwned;
use serde_json::Value;
use url::Url;
use uuid::Uuid;

use crate::config::TargetConfig;
use crate::protocol::{
    CommandRejection, EventAck, EventBatch, HarnessPublishRequest, HarnessSnapshot, HostCapacity,
    HostHello, POLL_HOLD, PairingCompleteRequest, PairingCompleteResponse, PollRequest,
    PollResponse, RunCheckpoint,
};

/// How much longer than `POLL_HOLD` a request may take before it is a failure.
///
/// The poll is answered at the end of the hold, so the timeout has to clear it
/// with room for the round trip and a slow network. Anything less turns every
/// idle poll into a client-side timeout.
const POLL_MARGIN: std::time::Duration = std::time::Duration::from_secs(10);

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

    /// Whether Lemma no longer knows this pairing.
    ///
    /// A revoked host is refused exactly like an unknown one — deliberately, so
    /// a stolen credential learns nothing — and both answer 401 with
    /// `AGENT_HOST_REVOKED_OR_MISSING`. `HostStatus::Revoked` exists in the
    /// protocol for this and is never reachable on the poll path, because
    /// authentication fails before a body is ever composed.
    ///
    /// Read the name literally: the backend raises this for `host is None` *or*
    /// `revoked_at is not None`, and does not say which. Revoked never becomes
    /// valid again; missing might — a host pointed at the wrong backend, a
    /// database restored behind its writes, a workspace rebuilt — so this alone
    /// is not grounds for dropping the pairing. `runtime.rs` waits for the
    /// refusal to repeat before acting on it.
    ///
    /// The code is read out of the parsed body rather than searched for in the
    /// raw text, so a message that merely quotes it cannot be mistaken for one.
    #[must_use]
    pub fn is_revoked_or_missing(&self) -> bool {
        let Self::Status {
            status: StatusCode::UNAUTHORIZED,
            body,
        } = self
        else {
            return false;
        };
        serde_json::from_str::<Value>(body)
            .ok()
            .and_then(|body| body.get("detail")?.get("code")?.as_str().map(str::to_owned))
            .is_some_and(|code| code == "AGENT_HOST_REVOKED_OR_MISSING")
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
        matches!(self, Self::Status { status, .. } if status_is_request_rejected(*status))
    }
}

/// The same judgement as [`ApiError::is_request_rejected`], over a bare status.
///
/// Split out for the MCP bridge, which holds a `reqwest::Response` rather than
/// an `ApiError` and had no policy of its own: it treated every non-2xx as
/// fatal and exited, so one 502 during a backend restart took the agent's whole
/// Lemma toolset down with it for the rest of the run. Two callers, one rule,
/// rather than a second opinion about which failures are worth another try.
#[must_use]
pub fn status_is_request_rejected(status: StatusCode) -> bool {
    status.is_client_error()
        && status != StatusCode::UNAUTHORIZED
        && status != StatusCode::TOO_MANY_REQUESTS
        && status != StatusCode::REQUEST_TIMEOUT
}

impl TargetClient {
    pub fn new(target: TargetConfig, installation_id: impl Into<String>) -> anyhow::Result<Self> {
        let http = Client::builder()
            .connect_timeout(std::time::Duration::from_secs(10))
            // Derived, not chosen. This client's slowest request by far is the
            // poll, which Lemma answers only at the end of the hold.
            .timeout(POLL_HOLD + POLL_MARGIN)
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

/// Whether a host name can only ever mean this machine.
///
/// `.localhost` is reserved for exactly that by RFC 6761 — resolvers must not
/// send it to DNS and must answer loopback — so `app.lemma.localhost`, which is
/// the hostname Lemma Desktop serves its own workspace and API on, is as
/// loopback as `127.0.0.1`. Accepting only the three literal spellings meant a
/// desktop install could not pair with itself: locald handed the host its own
/// API URL and the host refused it as a non-loopback plain-HTTP target.
#[must_use]
pub fn is_loopback_host(host: Option<&str>) -> bool {
    matches!(host, Some("localhost" | "127.0.0.1" | "::1"))
        || host.is_some_and(|host| host.ends_with(".localhost"))
}

pub fn validate_target_url(url: &Url, allow_insecure_http: bool) -> anyhow::Result<()> {
    if url.scheme() == "https" {
        return Ok(());
    }
    anyhow::ensure!(
        allow_insecure_http && url.scheme() == "http" && is_loopback_host(url.host_str()),
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
    fn a_forgotten_pairing_is_distinguished_from_any_other_rejection() {
        // Both are 401 and the backend makes them deliberately identical to a
        // caller holding a bad secret. Only the code tells them apart.
        let forgotten = ApiError::Status {
            status: StatusCode::UNAUTHORIZED,
            body: r#"{"detail":{"code":"AGENT_HOST_REVOKED_OR_MISSING","message":"Agent Host is unavailable"}}"#
                .to_owned(),
        };
        assert!(forgotten.is_revoked_or_missing());
        assert!(forgotten.is_unauthorized());

        let malformed = ApiError::Status {
            status: StatusCode::UNAUTHORIZED,
            body: r#"{"detail":{"code":"INVALID_AGENT_HOST_CREDENTIAL"}}"#.to_owned(),
        };
        assert!(
            !malformed.is_revoked_or_missing(),
            "a malformed credential may become valid again and must keep retrying"
        );
        assert!(malformed.is_unauthorized());

        // And a 403 carrying the same words is not one.
        let forbidden = ApiError::Status {
            status: StatusCode::FORBIDDEN,
            body: r#"{"detail":{"code":"AGENT_HOST_REVOKED_OR_MISSING"}}"#.to_owned(),
        };
        assert!(!forbidden.is_revoked_or_missing());
    }

    #[test]
    fn the_code_is_read_from_the_body_not_searched_for_in_it() {
        // Dropping a pairing is destructive, so the trigger for it must be the
        // backend saying so in the field that means it -- not the string turning
        // up somewhere in a proxy's error page or quoted inside a message.
        let quoted = ApiError::Status {
            status: StatusCode::UNAUTHORIZED,
            body: r#"{"detail":{"code":"INVALID_AGENT_HOST_CREDENTIAL",
                     "message":"not AGENT_HOST_REVOKED_OR_MISSING"}}"#
                .to_owned(),
        };
        assert!(!quoted.is_revoked_or_missing());

        let unparseable = ApiError::Status {
            status: StatusCode::UNAUTHORIZED,
            body: "<html>AGENT_HOST_REVOKED_OR_MISSING</html>".to_owned(),
        };
        assert!(!unparseable.is_revoked_or_missing());
    }

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

    #[test]
    fn a_desktop_install_can_pair_with_its_own_workspace() {
        // Lemma Desktop serves its workspace and API on app.lemma.localhost.
        // Accepting only the three literal loopback spellings meant the host
        // refused the very workspace that had just handed it a pairing code,
        // so "Connect this computer" could never succeed in local mode.
        validate_target_url(
            &Url::parse("http://app.lemma.localhost:52502").unwrap(),
            true,
        )
        .unwrap();
        // Still opt-in: plain HTTP without the flag is refused wherever it points.
        assert!(
            validate_target_url(
                &Url::parse("http://app.lemma.localhost:52502").unwrap(),
                false
            )
            .is_err()
        );
    }

    #[test]
    fn only_a_real_localhost_suffix_counts_as_loopback() {
        assert!(is_loopback_host(Some("app.lemma.localhost")));
        assert!(is_loopback_host(Some("localhost")));
        // A name someone else can own must not pass because it merely contains
        // the word.
        assert!(!is_loopback_host(Some("localhost.attacker.example")));
        assert!(!is_loopback_host(Some("notlocalhost")));
        assert!(!is_loopback_host(None));
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
