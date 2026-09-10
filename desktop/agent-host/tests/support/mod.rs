//! Stand-ins for the two things an Agent Host talks to: Lemma's control plane
//! and Lemma's run-scoped MCP endpoint.
//!
//! Both are deliberately faithful to the real contracts rather than convenient:
//! the MCP endpoint answers the same stateless JSON-RPC-over-HTTP shape that
//! `app/mcp_server.py` mounts at `/agent-runtime/conversations/{id}/mcp`, and
//! the control plane speaks the same pairing / publish / poll / append-events
//! endpoints as `app/modules/agent`'s Agent Host API. A test that passes here
//! has exercised the host's real code paths end to end; what it has *not*
//! proven is that Lemma's own implementations of those contracts are correct.

// Each test binary uses a different part of this, so what one of them does
// not touch is dead for that build -- and since the split, whole modules are.
#![allow(dead_code, unused_imports)]

use std::collections::BTreeMap;

mod agents;
mod control;
mod control_routes;
mod host;
mod mcp;

pub use agents::*;
pub use control::*;
pub(crate) use control_routes::*;
pub use host::*;
pub use mcp::*;

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use axum::extract::{Path as AxumPath, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{post, put};
use axum::{Json, Router};
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
