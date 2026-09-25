//! The link: one WebSocket per paired workspace, carrying everything.
//!
//! It replaced a 25-second HTTP long-poll plus one POST per streamed event,
//! which cost a database session and a secret-hash lookup for every chunk an
//! agent streamed. The link authenticates once and pushes in both directions
//! as soon as there is something to say. See
//! docs/architecture/agent-host.md#the-link for the frames, close codes and
//! delivery rules; this module is the host's half of it.

mod connection;
pub mod protocol;
#[cfg(test)]
pub(crate) mod stub;
mod target;
#[cfg(test)]
mod tests;

use tokio::sync::watch;
use url::Url;
use uuid::Uuid;

pub use connection::{
    Connected, DEFAULT_OP_DEADLINE, HANDSHAKE_TIMEOUT, LinkError, LinkHandle, MAX_CONCURRENT_OPS,
    OpHandler, Push, REQUEST_TIMEOUT, connect, connect_with, link_url,
};
pub use target::{is_loopback_host, validate_target_url};

use crate::config::TargetConfig;
use crate::protocol::HostHello;
use protocol::{PairBody, PairedBody, host};

/// Consume a one-time pairing code, and return the pairing it creates.
///
/// Pairing is the one exchange on a link without a host secret, because it is
/// the exchange that issues one. The link is closed straight after; the host
/// reconnects with the secret like any paired host.
pub async fn pair(
    base_url: Url,
    pairing_code: &str,
    display_name: &str,
    installation_id: &str,
    allow_insecure_http: bool,
) -> anyhow::Result<TargetConfig> {
    pair_reenabling(
        base_url,
        pairing_code,
        display_name,
        installation_id,
        allow_insecure_http,
        false,
    )
    .await
}

/// `pair`, saying whether the person asked to turn a computer they removed
/// back on. See `PairBody::reenable`.
pub async fn pair_reenabling(
    base_url: Url,
    pairing_code: &str,
    display_name: &str,
    installation_id: &str,
    allow_insecure_http: bool,
    reenable: bool,
) -> anyhow::Result<TargetConfig> {
    validate_target_url(&base_url, allow_insecure_http)?;
    let body = serde_json::to_value(PairBody {
        pairing_code: pairing_code.to_owned(),
        display_name: display_name.to_owned(),
        hello: HostHello::current(installation_id),
        reenable,
    })?;
    let (connected, answer) = connection::open(&base_url, None, (host::PAIR, body), None).await?;
    connected.handle.close(protocol::close::NORMAL, "paired");
    let paired: PairedBody = serde_json::from_value(answer.body)?;
    anyhow::ensure!(
        !paired.host_secret.is_empty(),
        "Lemma returned an empty host secret"
    );
    Ok(TargetConfig {
        target_id: Uuid::new_v4(),
        name: display_name.to_owned(),
        base_url,
        host_id: paired.host_id,
        user_id: paired.user_id,
        host_secret: paired.host_secret,
        enabled: true,
        allow_insecure_http,
        draining: false,
        refresh_generation: 0,
        session_paused: false,
        host_execution: false,
    })
}

/// Retire this host's own credential, e.g. on uninstall.
pub async fn revoke(target: &TargetConfig, installation_id: &str) -> anyhow::Result<()> {
    let connected = connect(
        &target.base_url,
        &target.host_secret,
        HostHello::current(installation_id),
        crate::protocol::HostCapacity::default(),
    )
    .await?;
    connected.handle.revoke().await?;
    Ok(())
}

/// The link a worker currently holds, for the tasks that need one.
///
/// Event delivery, harness publication and the MCP relay all run beside the
/// worker loop and outlive any one connection. They wait here for a link
/// rather than each holding a handle that goes stale on reconnect.
#[derive(Clone)]
pub struct LinkSlot {
    current: watch::Receiver<Option<LinkHandle>>,
}

/// The worker's side of a [`LinkSlot`].
pub struct LinkSlotOwner {
    current: watch::Sender<Option<LinkHandle>>,
}

impl LinkSlotOwner {
    #[must_use]
    pub fn new() -> (Self, LinkSlot) {
        let (current, receiver) = watch::channel(None);
        (Self { current }, LinkSlot { current: receiver })
    }

    pub fn set(&self, handle: Option<LinkHandle>) {
        self.current.send_replace(handle);
    }
}

impl LinkSlot {
    /// The link, waiting for one if there is none open. `None` once the worker
    /// that owns the slot has gone.
    pub async fn wait(&mut self) -> Option<LinkHandle> {
        loop {
            if let Some(handle) = self
                .current
                .borrow_and_update()
                .clone()
                .filter(|handle| !handle.is_closed())
            {
                return Some(handle);
            }
            if self.current.changed().await.is_err() {
                return None;
            }
        }
    }

    /// The link if one is open right now.
    #[must_use]
    pub fn now(&self) -> Option<LinkHandle> {
        self.current
            .borrow()
            .clone()
            .filter(|handle| !handle.is_closed())
    }
}
