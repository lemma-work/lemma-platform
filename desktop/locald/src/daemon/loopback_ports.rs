//! Which of this Mac's loopback ports are Lemma's, for the loopback relay.
//!
//! The relay (`crate::loopback_relay`) refuses them so the owner's VM browser
//! cannot be pointed at Lemma itself. The managed runtime knows its own
//! ports; this adds the ones only the daemon can see, each read when a relay
//! connection asks rather than once at startup, because sharing and the Agent
//! Host choose theirs while the stack runs.

use std::collections::BTreeSet;
use std::sync::Arc;

use crate::agent_host::AgentHostSupervisor;
use crate::host_process::HostProcessManager;
use crate::loopback_relay::LemmaPorts;
use crate::sharing::SharingController;

pub(super) fn daemon_lemma_ports(
    host_processes: Option<Arc<HostProcessManager>>,
    sharing: Option<Arc<SharingController>>,
    agent_host: Arc<AgentHostSupervisor>,
) -> LemmaPorts {
    Arc::new(move || {
        let mut ports = BTreeSet::new();
        if let Some(manager) = host_processes.as_ref() {
            ports.extend(manager.declared_loopback_ports());
        }
        if let Some(sharing) = sharing.as_ref() {
            ports.extend(sharing.listening_ports());
        }
        ports.extend(agent_host.mcp_relay_ports());
        ports
    })
}
