//! Bulk bytes to and from Lemma, over HTTP beside the link.
//!
//! Lemma's API reads no websocket message past 1 MiB
//! ([`MAX_MESSAGE_BYTES`](super::protocol::MAX_MESSAGE_BYTES)), so the link
//! carries only what moves -- an id, a size, a digest -- and the bytes go
//! here: a file chunk an `op` reads or writes (a *transfer*), and an image a
//! run's event names (a *blob*). Both are authenticated exactly as the link
//! is, by the host secret as a bearer token, and Lemma checks that a transfer
//! is this host's own and that a blob's run is one this host holds. See
//! docs/architecture/desktop-host-execution.md §4 and
//! docs/architecture/agent-host.md#bulk-bytes; the backend's half is
//! `app/modules/agent/agent_host/controllers/transfers.py`.

use std::time::Duration;

use sha2::{Digest as _, Sha256};
use url::Url;
use uuid::Uuid;

use serde_json::Value;

use super::protocol::{
    BLOB_MAX_BYTES, DIGEST_HEADER, STAGED_BODY_MAX_BYTES, STAGED_FIELD, TRANSFER_MAX_BYTES,
};

/// How long one transfer may take, end to end. A chunk is at most 1 MiB and a
/// blob 5 MiB; past this the network, not the size, is the problem.
const REQUEST_TIMEOUT: Duration = Duration::from_secs(60);
const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);

/// Why bytes did not move.
#[derive(Debug, thiserror::Error)]
pub enum TransferError {
    /// Lemma could not be reached, or the connection broke. Worth retrying.
    #[error("could not reach Lemma to move the data: {0}")]
    Transport(String),
    /// Lemma answered, and refused. `status` is the HTTP status.
    #[error("Lemma refused the data ({status}): {message}")]
    Refused { status: u16, message: String },
    /// The bytes were not the ones the op described.
    #[error("the data is not what the op described: {0}")]
    Mismatch(String),
}

/// The SHA-256 of `data`, as lowercase hex: how both sides name bytes.
#[must_use]
pub fn sha256_hex(data: &[u8]) -> String {
    hex::encode(Sha256::digest(data))
}

/// HTTP to the workspace this host is paired with, as this host.
#[derive(Clone)]
pub struct TransferClient {
    base: Url,
    host_secret: String,
    http: reqwest::Client,
}

impl std::fmt::Debug for TransferClient {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        // Never the secret.
        formatter
            .debug_struct("TransferClient")
            .field("base", &self.base.as_str())
            .finish_non_exhaustive()
    }
}

impl TransferClient {
    /// A client for the workspace at `base`, the same API root the link
    /// opens under.
    pub fn new(base: &Url, host_secret: &str) -> anyhow::Result<Self> {
        anyhow::ensure!(
            !host_secret.contains(['\r', '\n']),
            "the host secret contains a line break"
        );
        let mut base = base.clone();
        if !base.path().ends_with('/') {
            let path = format!("{}/", base.path());
            base.set_path(&path);
        }
        let http = reqwest::Client::builder()
            .timeout(REQUEST_TIMEOUT)
            .connect_timeout(CONNECT_TIMEOUT)
            .user_agent(format!("lemma-agent-host/{}", crate::HOST_RELEASE))
            .build()?;
        Ok(Self {
            base,
            host_secret: host_secret.to_owned(),
            http,
        })
    }

    fn url(&self, path: &str) -> Result<Url, TransferError> {
        self.base
            .join(path)
            .map_err(|error| TransferError::Transport(error.to_string()))
    }

    /// The bytes Lemma staged for an op, checked against the size and digest
    /// the op named.
    pub async fn fetch(
        &self,
        transfer_id: &str,
        size: usize,
        sha256: &str,
    ) -> Result<Vec<u8>, TransferError> {
        self.fetch_at_most(transfer_id, size, sha256, TRANSFER_MAX_BYTES)
            .await
    }

    /// Put back a part of a frame Lemma staged rather than sent, if `value`
    /// is one: `{"lemma_staged": {id, size, sha256}}` becomes the JSON it
    /// names, fetched and checked against that size and digest. Anything else
    /// is returned as it is.
    pub async fn resolve_staged(&self, value: Value) -> Result<Value, TransferError> {
        let Some(reference) = staged_reference(&value) else {
            return Ok(value);
        };
        let body = self
            .fetch_at_most(
                &reference.id,
                reference.size,
                &reference.sha256,
                STAGED_BODY_MAX_BYTES,
            )
            .await?;
        serde_json::from_slice(&body).map_err(|error| {
            TransferError::Mismatch(format!("the staged body is not JSON: {error}"))
        })
    }

    async fn fetch_at_most(
        &self,
        transfer_id: &str,
        size: usize,
        sha256: &str,
        limit: usize,
    ) -> Result<Vec<u8>, TransferError> {
        check_transfer_id(transfer_id)?;
        if size > limit {
            return Err(TransferError::Mismatch(format!(
                "a transfer carries at most {limit} bytes"
            )));
        }
        let url = self.url(&format!("agent-host/transfers/{transfer_id}"))?;
        let mut response = self
            .http
            .get(url)
            .bearer_auth(&self.host_secret)
            .send()
            .await
            .map_err(transport)?;
        refused(&response)?;
        let mut data = Vec::with_capacity(size);
        while let Some(piece) = response.chunk().await.map_err(transport)? {
            data.extend_from_slice(&piece);
            if data.len() > size {
                return Err(TransferError::Mismatch(format!(
                    "Lemma sent more than the {size} bytes the op named"
                )));
            }
        }
        if data.len() != size {
            return Err(TransferError::Mismatch(format!(
                "{} bytes arrived where the op named {size}",
                data.len()
            )));
        }
        if sha256_hex(&data) != sha256.to_ascii_lowercase() {
            return Err(TransferError::Mismatch(
                "the bytes do not match the op's digest".to_owned(),
            ));
        }
        Ok(data)
    }

    /// Hand Lemma the bytes an op read, into the slot the op named. Returns
    /// their digest, which the op's answer repeats.
    pub async fn deliver(&self, transfer_id: &str, data: Vec<u8>) -> Result<String, TransferError> {
        check_transfer_id(transfer_id)?;
        let url = self.url(&format!("agent-host/transfers/{transfer_id}"))?;
        self.put(url, data).await
    }

    /// Hand Lemma an attachment of one of this host's runs, before the event
    /// that names it. Idempotent: the same bytes may be sent again on a
    /// replay. Returns their digest, which the event names them by.
    pub async fn put_blob(&self, run_id: Uuid, data: Vec<u8>) -> Result<String, TransferError> {
        if data.len() > BLOB_MAX_BYTES {
            return Err(TransferError::Refused {
                status: 413,
                message: format!("a blob carries at most {BLOB_MAX_BYTES} bytes"),
            });
        }
        let digest = sha256_hex(&data);
        let url = self.url(&format!("agent-host/runs/{run_id}/blobs/{digest}"))?;
        self.put(url, data).await
    }

    async fn put(&self, url: Url, data: Vec<u8>) -> Result<String, TransferError> {
        let digest = sha256_hex(&data);
        let response = self
            .http
            .put(url)
            .bearer_auth(&self.host_secret)
            .header(DIGEST_HEADER, &digest)
            .header("content-type", "application/octet-stream")
            .body(data)
            .send()
            .await
            .map_err(transport)?;
        refused(&response)?;
        Ok(digest)
    }
}

/// What a staged part of a frame names.
#[derive(Debug, serde::Deserialize)]
struct StagedReference {
    id: String,
    size: usize,
    sha256: String,
}

/// The reference `value` is, when it is nothing but one.
fn staged_reference(value: &Value) -> Option<StagedReference> {
    let fields = value.as_object()?;
    if fields.len() != 1 {
        return None;
    }
    serde_json::from_value(fields.get(STAGED_FIELD)?.clone()).ok()
}

/// Whether `value` is a staged part of a frame, to be fetched.
#[must_use]
pub fn is_staged(value: &Value) -> bool {
    value
        .as_object()
        .is_some_and(|fields| fields.len() == 1 && fields.contains_key(STAGED_FIELD))
}

/// A transfer id is Lemma's, but it becomes part of a URL here: anything but
/// a plain token is refused rather than joined into a path.
fn check_transfer_id(transfer_id: &str) -> Result<(), TransferError> {
    if transfer_id.is_empty()
        || transfer_id.len() > 64
        || !transfer_id
            .chars()
            .all(|character| character.is_ascii_alphanumeric() || "_-".contains(character))
    {
        return Err(TransferError::Refused {
            status: 400,
            message: "the op named a transfer id that is not a plain token".to_owned(),
        });
    }
    Ok(())
}

#[allow(clippy::needless_pass_by_value)]
fn transport(error: reqwest::Error) -> TransferError {
    // Without the URL: it is ours, but the error text is all that is logged.
    TransferError::Transport(error.without_url().to_string())
}

fn refused(response: &reqwest::Response) -> Result<(), TransferError> {
    let status = response.status();
    if status.is_success() {
        return Ok(());
    }
    Err(TransferError::Refused {
        status: status.as_u16(),
        message: status
            .canonical_reason()
            .unwrap_or("unexpected status")
            .to_owned(),
    })
}

impl TransferError {
    /// Whether the same request may succeed if sent again: a transport
    /// failure, or Lemma itself failing.
    #[must_use]
    pub fn is_transient(&self) -> bool {
        match self {
            Self::Transport(_) => true,
            Self::Refused { status, .. } => *status >= 500 || *status == 429,
            Self::Mismatch(_) => false,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::link::stub::StubLink;

    #[test]
    fn a_transfer_id_is_never_joined_into_a_path_unless_it_is_a_plain_token() {
        for id in ["", "../link", "a/b", "a?b", &"x".repeat(65)] {
            assert!(check_transfer_id(id).is_err(), "{id}");
        }
        assert!(check_transfer_id("0f3a-_Z").is_ok());
    }

    #[test]
    fn the_secret_never_reaches_a_log_line() {
        let client =
            TransferClient::new(&Url::parse("https://api.lemma.work/api").unwrap(), "s3cret")
                .unwrap();
        assert!(!format!("{client:?}").contains("s3cret"));
        // The API root keeps its path: transfers sit beside the link.
        assert_eq!(
            client.url("agent-host/transfers/abc").unwrap().as_str(),
            "https://api.lemma.work/api/agent-host/transfers/abc"
        );
    }

    #[test]
    fn only_a_failure_that_may_pass_is_transient() {
        assert!(TransferError::Transport("reset".into()).is_transient());
        let refused = |status| TransferError::Refused {
            status,
            message: String::new(),
        };
        assert!(refused(503).is_transient());
        assert!(refused(429).is_transient());
        assert!(!refused(404).is_transient());
        assert!(!refused(409).is_transient());
        assert!(!TransferError::Mismatch(String::new()).is_transient());
    }

    #[tokio::test]
    async fn a_fetch_takes_exactly_what_the_op_named() {
        let stub = StubLink::start().await;
        let client = TransferClient::new(&stub.url, "secret").unwrap();
        let stage = |id: &str, data: &[u8]| {
            stub.state
                .staged
                .lock()
                .unwrap()
                .insert(id.to_owned(), data.to_vec());
        };
        stage("ok", b"hello");
        assert_eq!(
            client.fetch("ok", 5, &sha256_hex(b"hello")).await.unwrap(),
            b"hello"
        );
        // Fetched once; a second fetch finds nothing.
        assert!(matches!(
            client.fetch("ok", 5, &sha256_hex(b"hello")).await,
            Err(TransferError::Refused { status: 404, .. })
        ));
        stage("long", b"hello, and more");
        assert!(matches!(
            client.fetch("long", 5, &sha256_hex(b"hello")).await,
            Err(TransferError::Mismatch(_))
        ));
        stage("short", b"hell");
        assert!(matches!(
            client.fetch("short", 5, &sha256_hex(b"hello")).await,
            Err(TransferError::Mismatch(_))
        ));
        stage("swapped", b"jello");
        assert!(matches!(
            client.fetch("swapped", 5, &sha256_hex(b"hello")).await,
            Err(TransferError::Mismatch(_))
        ));
    }

    #[tokio::test]
    async fn a_delivery_and_a_blob_carry_their_digest() {
        let stub = StubLink::start().await;
        let client = TransferClient::new(&stub.url, "secret").unwrap();
        let digest = client.deliver("slot", b"read".to_vec()).await.unwrap();
        assert_eq!(digest, sha256_hex(b"read"));
        assert_eq!(
            stub.state.delivered.lock().unwrap().get("slot"),
            Some(&b"read".to_vec())
        );
        let digest = client
            .put_blob(Uuid::nil(), b"image".to_vec())
            .await
            .unwrap();
        assert_eq!(
            stub.state.blobs.lock().unwrap().get(&digest),
            Some(&b"image".to_vec())
        );
        // Larger than Lemma takes: refused here, without being sent.
        let before = stub.state.http_authorizations.lock().unwrap().len();
        assert!(matches!(
            client
                .put_blob(Uuid::nil(), vec![0; BLOB_MAX_BYTES + 1])
                .await,
            Err(TransferError::Refused { status: 413, .. })
        ));
        assert_eq!(stub.state.http_authorizations.lock().unwrap().len(), before);
    }
}
