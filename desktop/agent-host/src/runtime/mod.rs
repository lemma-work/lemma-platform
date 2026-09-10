//! The agent host's runtime: a worker per paired target, the runs it
//! starts, and everything it reports back.
//!
//! Was one 5,126-line file. Split by what the worker is doing.

//! Multi-target Agent Host supervisor.

use std::collections::{BTreeMap, HashMap, HashSet};
use std::path::PathBuf;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

use agent_client_protocol::schema::v1::{EnvVariable, McpServer, McpServerStdio};
use base64::Engine;
use base64::engine::general_purpose::STANDARD;
use chrono::Utc;
use serde_json::Value;
use tokio::sync::{OwnedSemaphorePermit, Semaphore, mpsc, watch};
use tokio::task::JoinHandle;
use uuid::Uuid;

use crate::acp::{AcpCallbacks, AcpDriver, AcpRunRequest, AgentDriver};
use crate::adapters::{AdapterManifest, AdapterWarmup, ResolvedAdapter};
use crate::api::{ApiError, PublishedHarness, TargetClient};
use crate::config::{HostConfig, HostPaths, TargetConfig};
use crate::journal::{AcceptOutcome, Checkpoint, Journal};
use crate::permissions::{PermissionDecision, PermissionGate};
use crate::protocol::{
    Command, CommandKind, CommandRejection, ConfigOption, EventType, HarnessCapabilities,
    HarnessHealth, HarnessSnapshot, HostCapacity, HostStatus, JsonMap, PollResponse, RejectionCode,
    RunCheckpoint, RunSpec, RunState,
};

mod artifacts;
mod callbacks;
mod commands;
mod events;
mod failures;
mod harnesses;
mod host;
mod polling;
mod run;
mod worker;

pub(crate) use artifacts::*;
pub use callbacks::*;
pub(crate) use commands::*;
pub(crate) use events::*;
pub(crate) use failures::*;
pub(crate) use harnesses::*;
pub use host::*;
pub(crate) use worker::*;

#[cfg(test)]
mod tests;

pub(crate) const HARNESS_REFRESH_INTERVAL: Duration = Duration::from_secs(15 * 60);
/// How soon to try again when publishing harnesses fails.
///
/// The refresh interval is tuned for "has anything about the installed agents
/// changed", which is rarely. It is the wrong interval for a failure: the
/// backend restarts whenever its configuration changes, and a publish that
/// happened to land during one used to leave this host with nothing published
/// for the next fifteen minutes — during which every command was rejected for
/// referencing a harness it had never announced.
pub(crate) const HARNESS_RETRY_INTERVAL: Duration = Duration::from_secs(10);
/// How long a queued command waits for the first harness publish before it is
/// rejected.
///
/// Was 60s, when probing every adapter ran in sequence behind four five-second
/// timeouts. Probes are concurrent now, so the slowest adapter sets the floor
/// and the old number was measuring a shape that no longer exists.
pub(crate) const FIRST_HARNESS_WAIT: Duration = Duration::from_secs(15);
/// How soon to try again when a probe failed for a reason that may not recur.
///
/// Doubling from here, capped at the ordinary refresh, because the two failures
/// this covers want opposite things. A probe that lost a race with the adapter
/// install wants to be retried almost immediately; a machine that is simply
/// slow, and will lose that race repeatedly, must not be re-probed every ten
/// seconds forever. Backing off reaches the same ceiling the sweep already had,
/// so the worst case is exactly today's behaviour and the common case is
/// seconds.
pub(crate) const TRANSIENT_RETRY_INTERVAL: Duration = Duration::from_secs(10);
/// How often to check whether the agents installed on this machine changed.
///
/// This is the budget line for noticing a newly installed agent, and it is
/// affordable only because detection no longer means probing: it is a handful of
/// `stat` calls, not four spawned processes.
///
/// It only means anything because the poll loop has a tick of its own to wake it
/// — see `POLL_HOLD`. As a check performed once per iteration it would have been
/// a *ceiling* on frequency and nothing at all on latency, since an iteration is
/// however long the server holds the poll.
pub(crate) const DISK_SCAN_INTERVAL: Duration = Duration::from_secs(2);
/// How often to re-read the local control file (drain, resume, refresh).
///
/// Its own deadline because the scan tick made the loop twelve times faster, and
/// `apply_local_controls` reads and parses `config.json` on every pass. Noticing
/// a drain request within five seconds is well inside what asked for it; doing it
/// thirty times a minute is just file I/O.
pub(crate) const LOCAL_CONTROL_INTERVAL: Duration = Duration::from_secs(5);
/// How many consecutive `AGENT_HOST_REVOKED_OR_MISSING` refusals it takes
/// before this host drops the pairing.
///
/// More than one because the backend cannot say which of the two it means, and
/// "missing" is survivable: a host pointed at the wrong backend, a database
/// restored behind its own writes, a workspace mid-rebuild. Three, with the
/// retry backoff doubling between them, is long enough that nothing transient
/// spans it and short enough that a real revocation is over in seconds.
pub(crate) const REVOKED_REFUSALS: u32 = 3;
pub(crate) const JOURNAL_CLEANUP_INTERVAL: Duration = Duration::from_secs(24 * 60 * 60);
pub(crate) const RETRY_MIN: Duration = Duration::from_millis(500);
pub(crate) const RETRY_MAX: Duration = Duration::from_secs(30);
/// How far event delivery is allowed to back off, and why it is not `RETRY_MAX`.
///
/// The poll loop backs off to thirty seconds because a failing poll means the
/// target may be unreachable, and hammering it helps nobody. Event delivery is
/// not that: it runs *beside* a poll loop that is separately proving the target
/// answers, and everything it carries -- a run's output, its terminal state --
/// is what somebody is sitting and waiting for.
///
/// Sharing the thirty-second ceiling meant a handful of transient failures
/// compounded into more than a minute of silence: 0.5s, 1, 2, 4, 8, 16, 30 is
/// 61.5s before the eighth attempt, on a control plane the poll loop was
/// talking to successfully the whole time. That is how a run finishes with
/// nothing delivered and no sign of why -- the retries below this were logged
/// at `debug`, which the host does not emit.
pub(crate) const EVENT_RETRY_MAX: Duration = Duration::from_secs(2);
/// Consecutive delivery failures before saying so at a level that is emitted.
pub(crate) const EVENT_RETRY_QUIET: u32 = 3;
pub(crate) const SHUTDOWN_GRACE: Duration = Duration::from_secs(30);
// How long a native permission request waits for a human before it is denied.
// Long enough for someone to actually see and answer the prompt; bounded so a
// forgotten one cannot pin an adapter open for the run's whole deadline.
pub(crate) const PERMISSION_DECISION_TIMEOUT: Duration = Duration::from_secs(30 * 60);
// How long the agent has to honour `session/cancel` and end its turn cleanly.
pub(crate) const CANCEL_GRACE: Duration = Duration::from_secs(10);
// How long the supervisor waits for a signalled run to terminalize itself
// before killing its process tree. Comfortably past CANCEL_GRACE so the ACP
// path is what normally resolves a cancellation, and the kill is the backstop
// for an adapter that ignores the notification altogether.
pub(crate) const CANCEL_KILL_AFTER: Duration = Duration::from_secs(15);
// How many polls a run whose liveness checkpoint Lemma refused waits before
// trying again. A refusal is usually transient (a deploy, a blip); giving up on
// the heartbeat permanently sentences a healthy run to lease expiry, so the
// host backs off rather than stopping.
pub(crate) const REFUSED_HEARTBEAT_RETRY_POLLS: u32 = 20;
// How many times one run's event batch may be rejected outright before the
// host stops trying to deliver that run's transcript. The first rejection is
// answered with a full replay, so this allows exactly one repair attempt.
pub(crate) const MAX_EVENT_REJECTIONS: u32 = 2;
// How many extra requests one poll may spend bisecting a control batch the
// server refuses. Comfortably above the ceiling for the 256 updates a poll can
// carry, so the bound only ever bites on a batch that is refused wholesale.
pub(crate) const MAX_CONTROL_PROBES: u32 = 12;
pub(crate) const GENERATED_ARTIFACT_DIRECTORY: &str = ".lemma-artifacts";
pub(crate) const MAX_GENERATED_IMAGE_BYTES: u64 = 5 * 1024 * 1024;
pub(crate) const MAX_GENERATED_IMAGES: usize = 10;
