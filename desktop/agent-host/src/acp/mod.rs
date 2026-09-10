//! The Agent Client Protocol side: one agent process per run, and the
//! translation between its session updates and Lemma's events.
//!
//! Was one 2,564-line file.

//! Generic ACP v1 adapter driver.

use std::collections::HashSet;
use std::future::Future;
use std::path::PathBuf;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::time::Duration;

use agent_client_protocol::schema::ProtocolVersion;
use agent_client_protocol::schema::v1::{
    CancelNotification, ContentBlock, InitializeRequest, LoadSessionRequest, McpServer,
    NewSessionRequest, PermissionOptionKind, PromptRequest, RequestPermissionOutcome,
    RequestPermissionRequest, RequestPermissionResponse, SelectedPermissionOutcome,
    SessionConfigOption, SessionConfigOptionValue, SessionNotification,
    SetSessionConfigOptionRequest, TextContent,
};
use agent_client_protocol::{AcpAgent, AcpAgentConfig, Agent, ByteStreams, ConnectionTo};
use async_trait::async_trait;
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};
use tokio::sync::watch;

use crate::adapters::ResolvedAdapter;
use crate::permissions::{AlwaysAllowOffer, AlwaysAllowScope, PermissionDecision, PermissionGate};
use crate::protocol::{ConfigOption, EventType, JsonMap, RunSpec, RunState};

mod driver;
mod options;
mod outcome;
mod permission;
mod prompt;
mod supervision;

pub use driver::*;
pub(crate) use options::*;
pub use outcome::*;
pub(crate) use permission::*;
pub use prompt::*;
pub(crate) use supervision::*;

#[cfg(test)]
mod tests;

#[derive(Clone)]
pub struct AcpRunRequest {
    pub adapter: ResolvedAdapter,
    pub run_spec: RunSpec,
    pub scratch_directory: PathBuf,
    pub mcp_server: Option<McpServer>,
    /// Whether this harness advertised `loadSession` at probe time. A run only
    /// tries to resume `run_spec.resume_session_id` when it did.
    pub can_load_session: bool,
    /// The configuration this harness was *published* as offering, from its
    /// probe.
    ///
    /// A run's own session is supposed to report the same thing, and mostly
    /// does — but ACP makes `configOptions` optional on `session/load`, and a
    /// Lemma conversation resumes on every turn after the first. An adapter
    /// that answers `session/new` with a model list and `session/load` with
    /// nothing left the second turn unable to find the model the first turn
    /// ran on, and failed it. This is the answer Lemma validated the profile
    /// against, so it is the one to fall back to.
    pub published_config_options: Vec<ConfigOption>,
    /// Where a native permission request parks while Lemma decides.
    pub permissions: PermissionGate,
    /// How long a parked request waits before it is denied, so a prompt
    /// nobody answers cannot pin the adapter open.
    pub permission_timeout: Duration,
    /// Raised when Lemma wants this run stopped. Watched rather than acted on
    /// by killing the process, so the turn can end through ACP.
    pub cancel: watch::Receiver<bool>,
    /// How long the agent has to honour `session/cancel` before the run is
    /// failed and the supervisor falls back to killing the process tree.
    pub cancel_grace: Duration,
}

#[derive(Clone, Debug)]
pub struct AcpRunOutcome {
    pub provider_session_id: String,
    pub state: RunState,
    pub stop_reason: String,
    /// Why the turn ended, when that is not self-evident from the state.
    ///
    /// ACP distinguishes five ways a turn can stop and only two of them are
    /// plain success or cancellation. The other three were all reported as
    /// `FAILED` with no detail, so a run that simply ran out of context told
    /// the user "Agent Host run ended in FAILED" while its partial answer sat
    /// directly above.
    pub message: Option<String>,
}

#[derive(Clone, Debug, serde::Serialize)]
pub struct AcpProbeOutcome {
    pub config_options: Vec<ConfigOption>,
    pub capabilities: Value,
    /// What the agent said about authenticating, from `initialize`.
    ///
    /// Not acted on: ACP's `authMethods` lists the ways a client *could* sign
    /// in, and no adapter is required to clear it once signed in — so an
    /// empty-or-not test would be a guess. It is recorded because a signed-out
    /// Claude Code was observed opening `session/new` quite happily and only
    /// failing at `session/prompt`, which is why it published as READY and the
    /// user learned it was signed out from a chat. Whether any adapter
    /// distinguishes the two states here is answerable from one build's log,
    /// and not from reading.
    #[serde(default)]
    pub auth_methods: Value,
}

pub trait AcpCallbacks: Send + Sync + 'static {
    fn before_prompt(&self, provider_session_id: &str) -> anyhow::Result<()>;
    fn event(
        &self,
        event_type: EventType,
        object_id: Option<String>,
        payload: JsonMap,
    ) -> anyhow::Result<()>;
}

#[async_trait]
pub trait AgentDriver: Send + Sync {
    async fn probe(
        &self,
        adapter: ResolvedAdapter,
        scratch_directory: PathBuf,
    ) -> anyhow::Result<AcpProbeOutcome>;

    async fn run(
        &self,
        request: AcpRunRequest,
        callbacks: Arc<dyn AcpCallbacks>,
    ) -> anyhow::Result<AcpRunOutcome>;
}
