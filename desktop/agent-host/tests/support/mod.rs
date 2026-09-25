//! Stand-ins for the two things an Agent Host talks to: Lemma's control plane
//! and Lemma's run-scoped MCP tools.
//!
//! Both are deliberately faithful to the real contracts rather than convenient.
//! The control plane serves the same WebSocket link as
//! `app/modules/agent`'s `agent_host_link.py` -- `pair`, `hello`, `control`,
//! `events`, `harnesses`, pushed `commands` -- with the same close codes, and
//! the MCP stand-in answers the `mcp` and `interaction_wait` frames that link
//! carries with the result objects `app/mcp_server.py` produces. A test that
//! passes here has exercised the host's real code paths end to end; what it
//! has *not* proven is that Lemma's own implementations of those contracts are
//! correct.

// Each test binary uses a different part of this, so what one of them does
// not touch is dead for that build -- and since the split, whole modules are.
#![allow(dead_code, unused_imports)]

use std::collections::BTreeMap;

mod agents;
mod control;
mod control_link;
mod host;
mod mcp;

pub use agents::*;
pub use control::*;
pub(crate) use control_link::*;
pub use host::*;
pub use mcp::*;

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use axum::Router;
use axum::extract::State;
use axum::http::HeaderMap;
use axum::response::Response;
use axum::routing::get;
use chrono::Utc;
use lemma_agent_host::protocol::{
    Command, CommandKind, Event, EventBatch, EventType, JsonMap, RunSpec,
};
use serde_json::{Value, json};
use tokio::net::TcpListener;
use uuid::Uuid;

pub const HOST_SECRET: &str = "hermetic-agent-host-secret-with-entropy";
pub const MCP_BEARER: &str = "hermetic-run-scoped-mcp-token";
pub const ECHO_TOOL: &str = "lemma_echo";
