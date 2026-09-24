//! The owner's loopback relay, started and stopped with the other routes.
//!
//! The relay itself is `crate::loopback_relay`. What lives here is its place
//! in the managed runtime's lifecycle -- it is a route the VM helper uses,
//! like the private service forwarders, so it comes up and goes down with
//! them -- and the answer to "which ports are Lemma's?" that it is given.

use std::collections::BTreeSet;

use super::*;
use crate::loopback_relay::LemmaPorts;

/// What the controller keeps for the relay.
#[derive(Default)]
pub(crate) struct HostLoopbackState {
    /// Ports other parts of the daemon own -- the sharing gateway, the Agent
    /// Host's relays -- supplied by the daemon, which can see them.
    lemma_ports: Arc<Mutex<Option<LemmaPorts>>>,
    #[cfg(target_os = "macos")]
    relay: Mutex<Option<crate::loopback_relay::LoopbackRelay>>,
}

/// The managed runtime's own ports: the application's pair and the private
/// service forwards on this Mac's loopback.
pub(crate) fn runtime_ports(spec: &ManagedRuntimeSpec) -> BTreeSet<u16> {
    let ports = &spec.ports;
    [
        ports.backend,
        ports.frontend,
        ports.postgres,
        ports.redis,
        ports.supertokens,
    ]
    .into_iter()
    .collect()
}

impl ManagedRuntimeController {
    /// Tell the relay about ports the rest of the daemon owns.
    ///
    /// Read on every relay connection rather than copied, so it may be set
    /// before or after the runtime starts.
    pub(crate) fn set_lemma_ports(&self, ports: LemmaPorts) {
        *self
            .host_loopback
            .lemma_ports
            .lock()
            .expect("lemma port provider lock poisoned") = Some(ports);
    }

    /// Every port the relay must refuse, as of now.
    pub(crate) fn lemma_ports(&self) -> LemmaPorts {
        let own = runtime_ports(&self.spec);
        let others = Arc::clone(&self.host_loopback.lemma_ports);
        Arc::new(move || {
            let mut ports = own.clone();
            let provider = others
                .lock()
                .expect("lemma port provider lock poisoned")
                .clone();
            if let Some(provider) = provider {
                ports.extend(provider());
            }
            ports
        })
    }

    /// Start the relay if it is not running. A relay that cannot start costs
    /// the fall-through and nothing else, so it is reported, not raised.
    #[cfg(target_os = "macos")]
    pub(crate) fn ensure_loopback_relay(&self) {
        let mut relay = self
            .host_loopback
            .relay
            .lock()
            .expect("loopback relay lock poisoned");
        if relay.is_some() {
            return;
        }
        match crate::loopback_relay::LoopbackRelay::start(
            self.runtime.host_loopback_socket(),
            self.lemma_ports(),
        ) {
            Ok(started) => *relay = Some(started),
            Err(error) => eprintln!("the loopback relay did not start: {error}"),
        }
    }

    pub(crate) fn stop_loopback_relay(&self) {
        #[cfg(target_os = "macos")]
        {
            // Taken under the lock, dropped outside it: stopping joins the
            // relay's thread.
            let relay = self
                .host_loopback
                .relay
                .lock()
                .expect("loopback relay lock poisoned")
                .take();
            drop(relay);
        }
    }
}
