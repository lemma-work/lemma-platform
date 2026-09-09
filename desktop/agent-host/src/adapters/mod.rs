//! The ACP adapters this build ships: what they are, where they install,
//! and whether this machine can run them.
//!
//! Was one 1,595-line file.

//! Pinned ACP adapter manifest and local harness discovery.

use lemma_desktop_process::{self as setup_process, Cancellation, SetupProcessError};
use std::collections::{BTreeMap, HashMap, HashSet};
use std::env;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use chrono::{Duration as ChronoDuration, Utc};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};

use crate::protocol::{ConfigOption, HarnessCapabilities, HarnessHealth, HarnessSnapshot};

mod cache;
mod discovery;
mod manifest;
mod snapshots;

pub use cache::*;
pub(crate) use discovery::*;
pub use snapshots::*;

#[cfg(test)]
mod tests;

const BUILTIN_MANIFEST: &str = include_str!("../../agent-adapters.lock.json");

/// Appended to the reason a harness failed for something that may not fail twice.
///
/// Carried in the text because that is the only field surviving the trip from
/// `resolve` through a `HarnessSnapshot` to the poll loop that decides when to
/// try again. It is stripped before the reason is stored, so nobody reads it.
const TRANSIENT_MARKER: &str = " [transient]";
const SNAPSHOT_TTL: ChronoDuration = ChronoDuration::hours(24);

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct AdapterManifest {
    pub manifest_version: u16,
    pub manifest_id: String,
    pub protocol: String,
    pub adapters: Vec<AdapterSpec>,
    #[serde(skip)]
    cache_root: Option<PathBuf>,
    /// Adapters already resolved by this process, shared across clones.
    ///
    /// Resolving is expensive and was being paid on every use: it hashes the
    /// whole npm package for the integrity check and execs the agent binary to
    /// read its version. Measured at 21.7s for four adapters, and `handle_start`
    /// paid a share of it on the poll loop before *every* run — which is where
    /// most of the per-message latency came from.
    ///
    /// Verifying once per process is the deliberate trade: an adapter swapped
    /// underneath a running host is no longer caught, but every restart
    /// re-verifies, and the host restarts often.
    #[serde(skip)]
    resolved: Arc<Mutex<HashMap<String, ResolvedAdapter>>>,
    /// Why the last attempt to warm the cache failed, per adapter, shared
    /// across clones.
    ///
    /// Warming moved off the pairing path and became a detached, best-effort
    /// thread, which is right — a machine with no npm must still serve the
    /// agents it already has. But it left nothing anywhere to distinguish "the
    /// download has not landed yet" from "the download cannot land", and both
    /// present as a missing cache directory. So a machine behind a proxy that
    /// blocks the registry reported *Setting up · usually under a minute*, and
    /// went on reporting it for as long as the app stayed open.
    #[serde(skip)]
    install_failures: Arc<Mutex<HashMap<String, String>>>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct AdapterSpec {
    pub key: String,
    pub display_name: String,
    pub adapter_version: String,
    pub command: String,
    #[serde(default)]
    pub args: Vec<String>,
    pub upstream_command: String,
    #[serde(default)]
    pub upstream_version_args: Vec<String>,
    /// The environment variable this adapter reads to be told which upstream
    /// agent binary to run.
    ///
    /// Without it an adapter picks its own, and the two certified ones pick
    /// differently: `codex-acp` falls back to the bare name `codex` and so
    /// resolves through `PATH`, while `claude-agent-acp` falls back to a copy
    /// vendored inside its own `node_modules` and never consults `PATH` at all.
    /// That second case is why Lemma could report the version of the Claude Code
    /// on this machine and then run a different one.
    #[serde(default)]
    pub upstream_path_env: Option<String>,
    /// Whether to install this adapter without its optional dependencies.
    ///
    /// True for both certified adapters, whose optional dependencies are
    /// per-platform *vendored builds of the agents themselves* — a 259 MB
    /// `codex` and a 245 MB `claude`. They reach the real agent through
    /// `upstream_path_env` instead, so those copies were 548 MB fetched to run
    /// code the host never invokes.
    ///
    /// Per adapter rather than always, because `--omit=optional` is not
    /// generally safe: platform-specific native binaries are conventionally
    /// declared as optional dependencies, and omitting those breaks the package
    /// rather than slimming it. Every adapter that sets this has to be one
    /// someone checked.
    #[serde(default)]
    pub omit_optional_dependencies: bool,
    pub minimum_upstream_version: Option<String>,
    pub distribution: String,
    pub artifact_integrity: Option<String>,
    pub license: String,
}

#[derive(Clone, Debug)]
pub struct ResolvedAdapter {
    pub spec: AdapterSpec,
    pub command: PathBuf,
    pub upstream_command: PathBuf,
    pub upstream_version: Option<String>,
}
