//! The guest agent: one JSON request per line over vsock, and the
//! containers it runs on the other side.
//!
//! Was one 6,257-line file. Split by what a request asks for.

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::collections::HashMap;
use std::fs::{self, OpenOptions};
use std::io::{self, BufRead, BufReader, Read, Seek, SeekFrom, Write};
use std::net::{IpAddr, SocketAddr, TcpStream, ToSocketAddrs};
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};
use std::sync::{Arc, Mutex, OnceLock};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

mod capacity;
mod core;
mod core_data;
mod diagnostics;
mod engine;
mod health;
mod host_control;
mod images;
mod network;
mod protocol;
mod pull_claim;
mod readiness;
mod sandbox;
mod sandbox_inspect;
mod sandbox_run;
mod serve;
mod service;
mod spec;
mod validate;

pub(crate) use core_data::*;
pub(crate) use diagnostics::*;
pub(crate) use engine::*;
pub use engine::{Engine, NerdctlEngine};
pub(crate) use images::*;
pub(crate) use network::*;
pub use protocol::{handle_reader, GuestError, GuestRequest, GuestResponse};
pub(crate) use pull_claim::*;
pub(crate) use readiness::*;
pub(crate) use sandbox::*;
pub(crate) use sandbox_inspect::*;
pub(crate) use sandbox_run::*;
pub use serve::serve_vsock;
pub use service::GuestService;
pub(crate) use spec::*;
pub(crate) use validate::*;

#[cfg(test)]
mod tests;

pub const PROTOCOL_VERSION: u64 = 1;
pub const VSOCK_PORT: u32 = 42_411;
pub(crate) const MAX_REQUEST_BYTES: u64 = 1024 * 1024;
/// Where guestd reaches the guest's own services.
///
/// The core containers run with host networking, so they listen on every
/// address in the guest's namespace and loopback reaches them exactly as the
/// DHCP-assigned address does -- without needing one. That matters because
/// the lease comes from vmnet, which macOS Local Network privacy can withhold
/// from the responsible app: readiness that went through the routable address
/// turned a denied permission into a guest that never reported ready at all.
pub(crate) const GUEST_LOOPBACK: &str = "127.0.0.1";
/// Connections served at once. The host keeps one control connection plus a
/// probe, so this is generous; it exists so a caller that opens sockets and
/// never closes them cannot spawn threads without limit.
///
/// Only the vsock listener accepts connections, and that is Linux-only.
#[cfg(target_os = "linux")]
pub(crate) const MAX_CONCURRENT_CONNECTIONS: usize = 32;
pub(crate) const MAX_RESPONSE_BYTES: usize = 4 * 1024 * 1024;
pub(crate) const CONTAINER_PREFIX: &str = "lemma-sandbox-";
pub(crate) const MANAGED_LABEL: &str = "app.kubernetes.io/name=lemma-sandbox";
pub(crate) const ENGINE_COMMAND_TIMEOUT: Duration = Duration::from_secs(120);
/// How long one image pull may take before it is treated as wedged.
///
/// Five minutes was not a limit on a hung pull, it was a limit on a slow one.
/// A first install pulls roughly a gigabyte of PostgreSQL, Redis and
/// SuperTokens, and on a connection that manages half a megabyte a second that
/// is half an hour. Measured on a real Windows machine: two attempts, ten
/// minutes, 317 MB in the content store and not one image completed --
/// progress every time, and failure every time, for ever.
///
/// An hour is still a bound: a pull that is genuinely stuck ends, and one
/// that is merely slow finishes. Measured on that machine, the rate had
/// dropped to about a third of a megabyte a second -- a gigabyte at that rate
/// is fifty minutes. The nested budget on the host side has to
/// be larger than this or it gives up first, which is a worse failure because
/// the guest carries on pulling into a request nobody is waiting on any more --
/// see `guest_request_budget` in the runtime manager.
pub(crate) const ENGINE_PULL_TIMEOUT: Duration = Duration::from_secs(60 * 60);
pub(crate) const CACHE_REPAIR_RESPONSE_GRACE: Duration = Duration::from_secs(10);

/// How long one `sandbox.ensure` may wait for a sandbox to start serving.
///
/// Not a limit on how long a sandbox may take: it is how long this *request*
/// may occupy the guest's single control channel before handing the caller a
/// retryable answer. The caller's own deadline still bounds the start.
///
/// Fifteen seconds rather than the two or three that would keep the channel
/// freest, because handing back `not_ready` is not free either: the retry is
/// served by the reuse path in `SandboxService`, which adopts the running
/// container without re-checking readiness. Measured starts are a couple of
/// seconds, so this leaves the retry as the exception rather than the norm
/// while still cutting the worst case from three minutes to fifteen seconds.
pub(crate) const SANDBOX_READY_POLL_BUDGET: Duration = Duration::from_secs(15);
const _: () = assert!(
    SANDBOX_READY_POLL_BUDGET.as_secs() <= 30 && SANDBOX_READY_POLL_BUDGET.as_secs() >= 5,
    "one ensure must not sit on the shared control channel for minutes, and must \
     not be so brief that every start takes the retry path",
);
/// The phrase that turns a guest failure into an offer to reset local data.
///
/// Duplicated from `lemma_locald::paths::DATA_RESET_MARKER` rather than shared:
/// guestd is a Linux binary that ships inside the VM image and links nothing
/// from the host daemon. Both sides are pinned by tests, and the string is the
/// entire contract -- locald maps it to `local-data-incompatible` and the
/// splash renders a reset button for that code.
pub(crate) const DATA_RESET_MARKER: &str = "local data must be reset";

/// Where the PostgreSQL cluster lives inside its container.
///
/// Pinned rather than inherited: see the note on `PGDATA` in `ensure_postgres`.
/// It is also the path `lemma-postgres-data` is mounted at, and the two must
/// stay equal -- a cluster written anywhere else is not on the volume the user
/// is told holds their database.
pub(crate) const POSTGRES_DATA_DIR: &str = "/var/lib/postgresql/data";
pub(crate) const DEFAULT_SANDBOX_MEMORY_BYTES: u64 = 2 * 1024 * 1024 * 1024;

/// What a sandbox is assumed to need in order to *start*, for admission.
///
/// Deliberately far below `DEFAULT_SANDBOX_MEMORY_BYTES`, because those are two
/// different things. `--memory` is a cgroup ceiling: it caps a runaway sandbox
/// and reserves nothing, and a container only ever occupies the pages it
/// touches. Admission used to add those ceilings up as though each sandbox had
/// claimed its whole allowance on creation, so a 6 GiB guest refused a third
/// sandbox while the two it had were using a couple of hundred megabytes
/// between them -- and refused it as "function runtime endpoint was not ready",
/// two minutes later, with no mention of memory.
///
/// This is the request; the ceiling stays the limit. Same split Kubernetes
/// draws, for the same reason.
pub(crate) const SANDBOX_MEMORY_REQUEST_BYTES: u64 = 256 * 1024 * 1024;

/// What the guest keeps for itself: page cache, containerd, the kernel.
///
/// Admission is refused while free memory is below this, so overcommitting
/// stops before the OOM killer rather than after it.
pub(crate) const GUEST_MEMORY_HEADROOM_BYTES: u64 = 384 * 1024 * 1024;

/// A ceiling on concurrent sandboxes, independent of memory.
///
/// Memory is the real constraint and is measured; this only stops a runaway
/// caller creating containers without bound. `LEMMA_GUEST_MAX_SANDBOXES`
/// overrides it, so an operator with a larger guest is not held to a number
/// compiled in here.
pub(crate) const DEFAULT_MAX_SANDBOXES: usize = 16;

// Checked when this file compiles, because they are statements about the
// constants above rather than about any run.
const _: () = assert!(
    SANDBOX_MEMORY_REQUEST_BYTES < DEFAULT_SANDBOX_MEMORY_BYTES,
    "the admission request has to be smaller than the ceiling, or nothing changed",
);
const _: () = assert!(
    DEFAULT_MAX_SANDBOXES > 2,
    "the default concurrency must not reimpose the two-sandbox limit",
);

/// How far the guest clock may sit from the host's before it is stepped.
///
/// A step is not free -- it moves wall time under every process in the guest --
/// so a second of ordinary jitter is left alone. Anything the host would
/// actually notice is not jitter.
pub(crate) const CLOCK_STEP_THRESHOLD_SECONDS: i64 = 2;
/// The window a host wall clock has to fall in to be believed, matching
/// `/usr/local/bin/lemma-set-host-time` exactly. The two set the same clock
/// from the same source and must agree on what is plausible.
pub(crate) const MIN_TRUSTED_EPOCH: u64 = 1_700_000_000;
pub(crate) const MAX_TRUSTED_EPOCH: u64 = 4_102_444_800;
