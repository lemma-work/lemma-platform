//! One open link: a WebSocket, a reader, a writer, and the requests in flight.

use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use futures_util::{SinkExt, StreamExt};
use serde::Serialize;
use serde::de::DeserializeOwned;
use serde_json::Value;
use tokio::sync::{mpsc, oneshot, watch};
use tokio_tungstenite::tungstenite::Message;
use tokio_tungstenite::tungstenite::client::IntoClientRequest;
use tokio_tungstenite::tungstenite::http::HeaderValue;
use tokio_tungstenite::tungstenite::protocol::CloseFrame;
use tokio_tungstenite::tungstenite::protocol::frame::coding::CloseCode;
use url::Url;
use uuid::Uuid;

use super::protocol::{
    CommandsBody, ControlBody, ControlOkBody, ErrorBody, EventsOkBody, Frame, HarnessesBody,
    HarnessesOkBody, HelloBody, InteractionOkBody, InteractionWaitBody, LINK_PATH, McpBody,
    McpOkBody, PublishedHarness, ReconnectBody, WelcomeBody, close, host, server,
};
use crate::protocol::{Command, EventAck, EventBatch, HarnessSnapshot, HostCapacity, HostHello};

/// How long an ordinary request waits for its answer. Control, events and
/// harness publication are all a single short transaction on Lemma's side;
/// one that has not answered in this long is on a link that is not coming
/// back, and reconnecting is the fix.
pub const REQUEST_TIMEOUT: Duration = Duration::from_secs(60);
/// How long the handshake -- connect plus `hello`/`pair` -- may take.
pub const HANDSHAKE_TIMEOUT: Duration = Duration::from_secs(20);
/// How long an abandoned link may spend saying goodbye: sending its close
/// frame, and finishing whatever write it was in the middle of, before its
/// tasks are stopped regardless.
const CLOSE_GRACE: Duration = Duration::from_secs(2);

/// Why a request, or the link itself, failed.
#[derive(Clone, Debug, thiserror::Error)]
pub enum LinkError {
    /// The socket could not be opened, or failed underneath us.
    #[error("the link to Lemma failed: {0}")]
    Transport(String),
    /// Lemma closed the link, saying why. See `protocol::close`.
    #[error("Lemma closed the link ({code}): {reason}")]
    Closed { code: u16, reason: String },
    /// Lemma answered this request with an `error` frame.
    #[error("Lemma refused the request ({code}): {message}")]
    Rejected {
        code: String,
        message: String,
        retryable: bool,
    },
    #[error("Lemma did not answer {0} in time")]
    Timeout(&'static str),
    /// Lemma sent something this host cannot read.
    #[error("the link carried something unreadable: {0}")]
    Protocol(String),
}

impl LinkError {
    fn closed_with(&self, code: u16) -> bool {
        matches!(self, Self::Closed { code: closed, .. } if *closed == code)
    }

    /// Lemma does not know this pairing: unknown or revoked, deliberately
    /// indistinguishable so a stolen credential learns nothing. "Missing" can
    /// heal -- a host pointed at the wrong backend, a database restored behind
    /// its writes -- which is why the worker waits for this to repeat before
    /// dropping the pairing.
    #[must_use]
    pub fn is_revoked_or_missing(&self) -> bool {
        self.closed_with(close::REVOKED_OR_MISSING)
    }

    /// The credential itself is malformed or missing.
    #[must_use]
    pub fn is_invalid_credential(&self) -> bool {
        self.closed_with(close::INVALID_CREDENTIAL)
    }

    /// Lemma speaks a newer protocol than this host.
    #[must_use]
    pub fn is_upgrade_required(&self) -> bool {
        self.closed_with(close::UPGRADE_REQUIRED)
    }

    /// Whether Lemma refused this one request on its own merits, as opposed to
    /// the link failing underneath it. Only a refusal is attributable to the
    /// run the request was about.
    #[must_use]
    pub fn is_request_rejected(&self) -> bool {
        matches!(
            self,
            Self::Rejected {
                retryable: false,
                ..
            }
        )
    }
}

/// Something Lemma sent without being asked.
#[derive(Clone, Debug)]
pub enum Push {
    /// Work for this host. Acknowledged on the next `control` frame.
    Commands(Vec<Command>),
    /// Lemma is restarting; reconnect after this long.
    Reconnect(Duration),
}

enum Outgoing {
    Frame(String),
    /// Flush what the socket has queued -- the pong for a ping Lemma sent.
    Flush,
    Close(u16, String),
}

type Pending = Arc<Mutex<HashMap<String, oneshot::Sender<Result<Frame, LinkError>>>>>;

/// Mark the link closed, then tell everyone waiting why.
///
/// In this order a request either sees the link closed when it re-checks
/// after registering, or is registered in time to be drained here -- never
/// neither. The first reason wins.
fn mark_closed(closed: &watch::Sender<Option<LinkError>>, pending: &Pending, error: &LinkError) {
    closed.send_if_modified(|closed| {
        if closed.is_none() {
            *closed = Some(error.clone());
            return true;
        }
        false
    });
    let waiters: Vec<_> = pending
        .lock()
        .expect("pending requests poisoned")
        .drain()
        .map(|(_, waiter)| waiter)
        .collect();
    for waiter in waiters {
        let _ = waiter.send(Err(error.clone()));
    }
}

/// The socket's two tasks, owned by the handles that talk on it.
///
/// Every [`LinkHandle`] clone holds this, so it drops with the last of them.
/// By then the writer's channel has no senders left -- the reader holds only
/// a weak one -- so the writer sends a close frame and ends, and its ending
/// stops the reader. Dropping this bounds that goodbye: a writer still stuck
/// on a peer that stopped reading is aborted after [`CLOSE_GRACE`], and the
/// reader with it, so an abandoned link never outlives its owners.
struct LinkTasks {
    writer: Option<tokio::task::JoinHandle<()>>,
    reader: Option<tokio::task::JoinHandle<()>>,
}

impl Drop for LinkTasks {
    fn drop(&mut self) {
        let (Some(mut writer), Some(mut reader)) = (self.writer.take(), self.reader.take()) else {
            return;
        };
        if let Ok(runtime) = tokio::runtime::Handle::try_current() {
            runtime.spawn(async move {
                if tokio::time::timeout(CLOSE_GRACE, &mut writer)
                    .await
                    .is_err()
                {
                    writer.abort();
                }
                // Normally already ending, woken by the writer's exit.
                if tokio::time::timeout(CLOSE_GRACE, &mut reader)
                    .await
                    .is_err()
                {
                    reader.abort();
                }
            });
        } else {
            // No runtime left to say goodbye on: stop both now.
            writer.abort();
            reader.abort();
        }
    }
}

/// A connected link. Cheap to clone; every clone talks on the same socket,
/// and the socket is closed once the last clone is dropped.
#[derive(Clone)]
pub struct LinkHandle {
    // Declared before `tasks`: the sender has to be gone by the time the
    // tasks are released, so the writer sees its channel close.
    outgoing: mpsc::UnboundedSender<Outgoing>,
    pending: Pending,
    next_id: Arc<AtomicU64>,
    closed: watch::Receiver<Option<LinkError>>,
    _tasks: Arc<LinkTasks>,
}

/// A link that has just completed its handshake.
pub struct Connected {
    pub handle: LinkHandle,
    pub pushes: mpsc::UnboundedReceiver<Push>,
    pub welcome: WelcomeBody,
}

impl LinkHandle {
    /// Whether the link has gone away. Every request after that fails.
    #[must_use]
    pub fn is_closed(&self) -> bool {
        self.closed.borrow().is_some()
    }

    /// Resolves once the link has gone away, with why.
    pub async fn closed(&self) -> LinkError {
        let mut closed = self.closed.clone();
        loop {
            if let Some(error) = closed.borrow().clone() {
                return error;
            }
            if closed.changed().await.is_err() {
                return LinkError::Transport("the link task ended".to_owned());
            }
        }
    }

    /// Close the link from this side.
    pub fn close(&self, code: u16, reason: &str) {
        let _ = self.outgoing.send(Outgoing::Close(code, reason.to_owned()));
    }

    /// Send a request and wait for its answer.
    pub async fn request<B: Serialize, R: DeserializeOwned>(
        &self,
        kind: &'static str,
        body: &B,
        timeout: Option<Duration>,
    ) -> Result<R, LinkError> {
        if let Some(error) = self.closed.borrow().clone() {
            return Err(error);
        }
        let id = self.next_id.fetch_add(1, Ordering::Relaxed).to_string();
        let frame = Frame {
            kind: kind.to_owned(),
            id: Some(id.clone()),
            re: None,
            body: serde_json::to_value(body)
                .map_err(|error| LinkError::Protocol(error.to_string()))?,
        };
        let text = serde_json::to_string(&frame)
            .map_err(|error| LinkError::Protocol(error.to_string()))?;
        let (answer, answered) = oneshot::channel();
        self.pending
            .lock()
            .expect("pending requests poisoned")
            .insert(id.clone(), answer);
        // Checked again now the waiter is registered. The link is marked closed
        // before its waiters are drained, so a request either sees the mark
        // here or is registered in time to be drained. Without this second
        // look, one registered just after the drain would be answered by
        // nobody -- and an MCP call has no timeout of its own, so the agent's
        // tool call would hang for good.
        if let Some(error) = self.closed.borrow().clone() {
            self.pending
                .lock()
                .expect("pending requests poisoned")
                .remove(&id);
            return Err(error);
        }
        if self.outgoing.send(Outgoing::Frame(text)).is_err() {
            self.pending
                .lock()
                .expect("pending requests poisoned")
                .remove(&id);
            return Err(self.closed.borrow().clone().unwrap_or_else(|| {
                LinkError::Transport("the link writer has stopped".to_owned())
            }));
        }
        let reply = match timeout {
            Some(limit) => {
                let Ok(reply) = tokio::time::timeout(limit, answered).await else {
                    self.pending
                        .lock()
                        .expect("pending requests poisoned")
                        .remove(&id);
                    return Err(LinkError::Timeout(kind));
                };
                reply
            }
            None => answered.await,
        };
        let frame = reply.map_err(|_| {
            self.closed.borrow().clone().unwrap_or_else(|| {
                LinkError::Transport("the link closed before answering".to_owned())
            })
        })??;
        if frame.kind == server::ERROR {
            let error: ErrorBody = serde_json::from_value(frame.body)
                .map_err(|error| LinkError::Protocol(error.to_string()))?;
            return Err(LinkError::Rejected {
                code: error.code,
                message: error.message,
                retryable: error.retryable,
            });
        }
        serde_json::from_value(frame.body).map_err(|error| {
            LinkError::Protocol(format!("{} answer did not parse: {error}", frame.kind))
        })
    }

    /// Report acknowledgements, checkpoints and rejections; the heartbeat.
    pub async fn control(&self, body: &ControlBody) -> Result<ControlOkBody, LinkError> {
        self.request(host::CONTROL, body, Some(REQUEST_TIMEOUT))
            .await
    }

    pub async fn append_events(&self, batch: &EventBatch) -> Result<EventAck, LinkError> {
        let answer: EventsOkBody = self
            .request(host::EVENTS, batch, Some(REQUEST_TIMEOUT))
            .await?;
        Ok(answer.ack)
    }

    pub async fn publish_harnesses(
        &self,
        harnesses: Vec<HarnessSnapshot>,
    ) -> Result<Vec<PublishedHarness>, LinkError> {
        let answer: HarnessesOkBody = self
            .request(
                host::HARNESSES,
                &HarnessesBody { harnesses },
                Some(REQUEST_TIMEOUT),
            )
            .await?;
        Ok(answer.items)
    }

    pub async fn revoke(&self) -> Result<(), LinkError> {
        let _: Value = self
            .request(host::REVOKE, &serde_json::json!({}), Some(REQUEST_TIMEOUT))
            .await?;
        Ok(())
    }

    /// One MCP request. No timeout of its own: a tool call takes as long as
    /// the tool does, and the run's deadline is what bounds it.
    pub async fn mcp(&self, body: &McpBody) -> Result<Value, LinkError> {
        let answer: McpOkBody = self.request(host::MCP, body, None).await?;
        Ok(answer.result)
    }

    /// Wait for a person's answer to a parked tool call. Lemma answers when
    /// they decide; it bounds the wait itself.
    pub async fn interaction_wait(&self, body: &InteractionWaitBody) -> Result<Value, LinkError> {
        let answer: InteractionOkBody = self.request(host::INTERACTION_WAIT, body, None).await?;
        Ok(answer.answer)
    }
}

/// The link's URL: the workspace's API root, `ws`/`wss`, plus the link path.
pub fn link_url(base: &Url) -> Result<Url, LinkError> {
    let mut url = base.clone();
    let scheme = match url.scheme() {
        "https" | "wss" => "wss",
        "http" | "ws" => "ws",
        other => {
            return Err(LinkError::Transport(format!(
                "cannot open a link over {other}"
            )));
        }
    };
    url.set_scheme(scheme)
        .map_err(|()| LinkError::Transport("could not set the link scheme".to_owned()))?;
    if !url.path().ends_with('/') {
        let path = format!("{}/", url.path());
        url.set_path(&path);
    }
    url.join(LINK_PATH)
        .map_err(|error| LinkError::Transport(error.to_string()))
}

/// Open the link and complete the handshake with `first`.
///
/// `first` is `hello` for a paired host and `pair` for one that is pairing;
/// `authorization` is the host secret, which only `hello` carries.
pub async fn open(
    base: &Url,
    authorization: Option<&str>,
    first: (&'static str, Value),
) -> Result<(Connected, Frame), LinkError> {
    let url = link_url(base)?;
    let mut request = url
        .as_str()
        .into_client_request()
        .map_err(|error| LinkError::Transport(error.to_string()))?;
    let headers = request.headers_mut();
    headers.insert(
        "user-agent",
        HeaderValue::from_str(&format!("lemma-agent-host/{}", crate::HOST_RELEASE))
            .map_err(|error| LinkError::Transport(error.to_string()))?,
    );
    if let Some(secret) = authorization {
        reject_line_breaks(secret)?;
        headers.insert(
            "authorization",
            HeaderValue::from_str(&format!("Bearer {secret}"))
                .map_err(|error| LinkError::Transport(error.to_string()))?,
        );
    }
    let (socket, _response) =
        tokio::time::timeout(HANDSHAKE_TIMEOUT, tokio_tungstenite::connect_async(request))
            .await
            .map_err(|_| LinkError::Timeout("the connection"))?
            .map_err(|error| LinkError::Transport(error.to_string()))?;
    let (mut sink, mut stream) = socket.split();
    let (outgoing, mut outgoing_rx) = mpsc::unbounded_channel::<Outgoing>();
    let (pushes_tx, pushes) = mpsc::unbounded_channel::<Push>();
    let (closed_tx, closed) = watch::channel::<Option<LinkError>>(None);
    let pending: Pending = Arc::new(Mutex::new(HashMap::new()));
    // Dropped when the writer ends, however it ends; that is what stops the
    // reader, whose half of the socket would otherwise keep it open.
    let (writer_alive, mut writer_gone) = oneshot::channel::<()>();

    // The writer. One task owns the sink, so frames are never interleaved.
    // It ends when told to close, when a write fails, or when every handle
    // has been dropped -- the last is an abandoned link, and it still says
    // goodbye.
    let writer_closed = closed_tx.clone();
    let writer_pending = Arc::clone(&pending);
    let writer = tokio::spawn(async move {
        let _alive = writer_alive;
        let error = loop {
            let Some(message) = outgoing_rx.recv().await else {
                let _ = tokio::time::timeout(
                    CLOSE_GRACE,
                    sink.send(Message::Close(Some(CloseFrame {
                        code: CloseCode::from(close::NORMAL),
                        reason: "abandoned".into(),
                    }))),
                )
                .await;
                break LinkError::Transport("the link was abandoned".to_owned());
            };
            let result = match message {
                Outgoing::Frame(text) => sink.send(Message::Text(text.into())).await,
                Outgoing::Flush => sink.flush().await,
                Outgoing::Close(code, reason) => {
                    let _ = tokio::time::timeout(
                        CLOSE_GRACE,
                        sink.send(Message::Close(Some(CloseFrame {
                            code: CloseCode::from(code),
                            reason: reason.clone().into(),
                        }))),
                    )
                    .await;
                    // Closed as of now. Waiting for Lemma to echo the close
                    // would leave requests hanging on a peer that may never
                    // answer again -- the reason a link is abandoned.
                    break LinkError::Transport(format!(
                        "this host closed the link ({code}): {reason}"
                    ));
                }
            };
            if let Err(error) = result {
                // A socket that cannot be written to may still read, so the
                // reader is not guaranteed to end soon; what is waiting on an
                // answer learns now that none is coming.
                break LinkError::Transport(error.to_string());
            }
        };
        mark_closed(&writer_closed, &writer_pending, &error);
    });

    // The reader. Answers go to whoever asked; pushes go to the worker. It
    // holds only a weak sender to the writer, for pongs: a strong one would
    // keep the writer's channel open after every handle had gone.
    let reader_pending = Arc::clone(&pending);
    let reader_outgoing = outgoing.downgrade();
    let reader = tokio::spawn(async move {
        let error = loop {
            let message = tokio::select! {
                message = stream.next() => message,
                _ = &mut writer_gone => {
                    break LinkError::Transport("the link's writer has stopped".to_owned());
                }
            };
            let Some(message) = message else {
                break LinkError::Transport("Lemma ended the link without closing it".to_owned());
            };
            let message = match message {
                Ok(message) => message,
                Err(error) => break LinkError::Transport(error.to_string()),
            };
            let text = match message {
                Message::Text(text) => text.to_string(),
                Message::Ping(_) => {
                    // tungstenite queues the pong itself; it goes out on the
                    // next flush, which an idle link would not otherwise do.
                    if let Some(outgoing) = reader_outgoing.upgrade() {
                        let _ = outgoing.send(Outgoing::Flush);
                    }
                    continue;
                }
                Message::Close(frame) => {
                    break frame.map_or_else(
                        || LinkError::Closed {
                            code: close::NORMAL,
                            reason: String::new(),
                        },
                        |frame| LinkError::Closed {
                            code: u16::from(frame.code),
                            reason: frame.reason.to_string(),
                        },
                    );
                }
                _ => continue,
            };
            let frame: Frame = match serde_json::from_str(&text) {
                Ok(frame) => frame,
                Err(error) => {
                    tracing::warn!(%error, "ignored a link frame that did not parse");
                    continue;
                }
            };
            if let Some(re) = frame.re.clone() {
                let waiter = reader_pending
                    .lock()
                    .expect("pending requests poisoned")
                    .remove(&re);
                if let Some(waiter) = waiter {
                    let _ = waiter.send(Ok(frame));
                } else {
                    tracing::debug!(re, kind = %frame.kind, "an answer arrived for a request nobody is waiting on");
                }
                continue;
            }
            if let Some(push) = push_from(frame) {
                let _ = pushes_tx.send(push);
            }
        };
        mark_closed(&closed_tx, &reader_pending, &error);
        // Stop the writer too, if anything still holds the link.
        if let Some(outgoing) = reader_outgoing.upgrade() {
            let _ = outgoing.send(Outgoing::Close(close::NORMAL, String::new()));
        }
    });

    let handle = LinkHandle {
        outgoing,
        pending,
        next_id: Arc::new(AtomicU64::new(1)),
        closed,
        _tasks: Arc::new(LinkTasks {
            writer: Some(writer),
            reader: Some(reader),
        }),
    };
    // From here every early return drops `handle`, the only one there is,
    // which closes the socket: a refused or garbled handshake does not leave
    // a connection open on Lemma's side.
    let (kind, body) = first;
    let answer = handshake(&handle, kind, body).await?;
    let welcome = if kind == host::HELLO {
        serde_json::from_value(answer.body.clone())
            .map_err(|error| LinkError::Protocol(format!("welcome did not parse: {error}")))?
    } else {
        WelcomeBody {
            host_id: Uuid::nil(),
            user_id: Uuid::nil(),
            protocol_version: crate::PROTOCOL_VERSION,
            heartbeat_ms: 20_000,
        }
    };
    Ok((
        Connected {
            handle,
            pushes,
            welcome,
        },
        answer,
    ))
}

/// Send the first frame and wait for its answer, however the link ends.
async fn handshake(
    handle: &LinkHandle,
    kind: &'static str,
    body: Value,
) -> Result<Frame, LinkError> {
    let id = "0".to_owned();
    let frame = Frame {
        kind: kind.to_owned(),
        id: Some(id.clone()),
        re: None,
        body,
    };
    let (answer, answered) = oneshot::channel();
    handle
        .pending
        .lock()
        .expect("pending requests poisoned")
        .insert(id, answer);
    let text =
        serde_json::to_string(&frame).map_err(|error| LinkError::Protocol(error.to_string()))?;
    let _ = handle.outgoing.send(Outgoing::Frame(text));
    let reply = tokio::time::timeout(HANDSHAKE_TIMEOUT, answered)
        .await
        .map_err(|_| LinkError::Timeout(kind))?
        .map_err(|_| {
            handle.closed.borrow().clone().unwrap_or_else(|| {
                LinkError::Transport("the link closed during the handshake".to_owned())
            })
        })??;
    if reply.kind == server::ERROR {
        let error: ErrorBody = serde_json::from_value(reply.body)
            .map_err(|error| LinkError::Protocol(error.to_string()))?;
        return Err(LinkError::Rejected {
            code: error.code,
            message: error.message,
            retryable: error.retryable,
        });
    }
    Ok(reply)
}

/// A paired host's handshake.
pub async fn connect(
    base: &Url,
    host_secret: &str,
    hello: HostHello,
    capacity: HostCapacity,
) -> Result<Connected, LinkError> {
    let body = serde_json::to_value(HelloBody { hello, capacity })
        .map_err(|error| LinkError::Protocol(error.to_string()))?;
    let (connected, _) = open(base, Some(host_secret), (host::HELLO, body)).await?;
    Ok(connected)
}

fn push_from(frame: Frame) -> Option<Push> {
    match frame.kind.as_str() {
        server::COMMANDS => match serde_json::from_value::<CommandsBody>(frame.body) {
            Ok(body) => Some(Push::Commands(body.commands)),
            Err(error) => {
                tracing::warn!(%error, "ignored a commands frame that did not parse");
                None
            }
        },
        server::RECONNECT => {
            let after = serde_json::from_value::<ReconnectBody>(frame.body)
                .map_or(1_000, |body| body.after_ms);
            Some(Push::Reconnect(Duration::from_millis(after)))
        }
        other => {
            tracing::debug!(kind = other, "ignored a push this host does not act on");
            None
        }
    }
}

/// A secret that would split a header line is refused before it is sent.
fn reject_line_breaks(secret: &str) -> Result<(), LinkError> {
    if secret.contains(['\r', '\n']) {
        return Err(LinkError::Transport(
            "the host secret contains a line break".to_owned(),
        ));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_link_url_follows_the_api_root() {
        let url = link_url(&Url::parse("https://api.lemma.work/api").unwrap()).unwrap();
        assert_eq!(url.as_str(), "wss://api.lemma.work/api/agent-host/link");
        let url = link_url(&Url::parse("http://app.lemma.localhost:52502/").unwrap()).unwrap();
        assert_eq!(
            url.as_str(),
            "ws://app.lemma.localhost:52502/agent-host/link"
        );
    }

    #[test]
    fn close_codes_are_read_as_the_conditions_they_name() {
        let closed = |code| LinkError::Closed {
            code,
            reason: String::new(),
        };
        assert!(closed(close::REVOKED_OR_MISSING).is_revoked_or_missing());
        assert!(closed(close::INVALID_CREDENTIAL).is_invalid_credential());
        assert!(closed(close::UPGRADE_REQUIRED).is_upgrade_required());
        assert!(!closed(close::RESTARTING).is_revoked_or_missing());
        assert!(
            LinkError::Rejected {
                code: "SEQUENCE_GAP".into(),
                message: String::new(),
                retryable: false
            }
            .is_request_rejected()
        );
        assert!(!LinkError::Transport("reset".into()).is_request_rejected());
    }
}
