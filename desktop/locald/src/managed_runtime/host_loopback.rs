//! The paired user's loopback relay, started and stopped with the other routes.
//!
//! The relay itself is `crate::loopback_relay`. What lives here is its place
//! in the managed runtime's lifecycle -- it is a route the VM helper uses,
//! like the private service forwarders, so it comes up and goes down with
//! them -- and the answer to "which ports are Lemma's?" that it is given.

use std::collections::BTreeSet;

use super::*;
use crate::loopback_relay::{HostExecution, LemmaPorts, RelayPolicy};

/// What the controller keeps for the relay.
#[derive(Default)]
pub(crate) struct HostLoopbackState {
    /// Ports other parts of the daemon own -- the sharing gateway, the Agent
    /// Host's relays -- supplied by the daemon, which can see them.
    lemma_ports: Arc<Mutex<Option<LemmaPorts>>>,
    /// The paired user's "Run commands on this Mac" switch, supplied by the daemon,
    /// which owns the Agent Host. Unset means off: the relay admits nothing
    /// until it is told otherwise.
    host_execution: Arc<Mutex<Option<HostExecution>>>,
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

    /// Tell the relay where to read the host-execution switch from.
    pub(crate) fn set_host_execution(&self, gate: HostExecution) {
        *self
            .host_loopback
            .host_execution
            .lock()
            .expect("host execution gate lock poisoned") = Some(gate);
    }

    /// Whether the relay may admit anything, as of now.
    pub(crate) fn host_execution(&self) -> HostExecution {
        let gate = Arc::clone(&self.host_loopback.host_execution);
        Arc::new(move || {
            let gate = gate
                .lock()
                .expect("host execution gate lock poisoned")
                .clone();
            gate.is_some_and(|enabled| enabled())
        })
    }

    /// What the relay judges each connection against.
    pub(crate) fn relay_policy(&self) -> RelayPolicy {
        RelayPolicy {
            host_execution: self.host_execution(),
            lemma_ports: self.lemma_ports(),
        }
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
            self.relay_policy(),
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
