//! Lemma Agent Host.
//!
//! The host is the durable local boundary between one or more Lemma targets and
//! user-owned ACP agents. Network commands and event acknowledgements are
//! persisted before side effects so restarts cannot silently repeat prompts.

pub mod acp;
pub mod adapters;
pub mod api;
pub mod config;
pub mod crypto;
pub mod journal;
pub mod mcp_bridge;
pub mod protocol;
pub mod runtime;
pub mod service;

pub const HOST_RELEASE: &str = env!("CARGO_PKG_VERSION");
pub const PROTOCOL_VERSION: u16 = 2;
