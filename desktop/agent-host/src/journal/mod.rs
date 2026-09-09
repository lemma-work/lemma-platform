//! The journal: every command, run and event written down before it has an
//! effect, so a restart cannot repeat one.
//!
//! Was one 2,093-line file.

//! Durable host-side command, run, checkpoint, and event journal.

use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};
use std::time::Duration;

use chrono::{DateTime, Utc};
use rusqlite::{Connection, OptionalExtension, TransactionBehavior, params};
use serde_json::Value;
use uuid::Uuid;

use crate::protocol::{
    Command, CommandRejection, Event, EventAck, EventBatch, EventType, JsonMap, RunCheckpoint,
    RunSpec, RunState,
};

mod control;
mod events;
mod runs;
mod schema;
mod types;

pub use types::*;

#[cfg(test)]
mod tests;

#[derive(Clone, Debug)]
pub struct Journal {
    path: PathBuf,
    /// See [`Journal::connection`]. Shared by every clone, so the whole
    /// process serialises on one handle instead of racing several.
    connection: Arc<Mutex<Connection>>,
}
