//! The managed guest, from locald's side: starting it, reaching the
//! services inside it, and keeping its clock and images current.
//!
//! Was one 2,239-line file. Split by what the controller is doing.

use std::collections::HashMap;
use std::env;
use std::fs::{self};
use std::io::{self};
#[cfg(any(not(target_os = "macos"), test))]
use std::net::TcpStream;
use std::net::{IpAddr, Ipv4Addr, SocketAddr};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant, SystemTime};

use lemma_runtime_manager::{
    ManagedRuntime, ManagedRuntimeConfig, ManagedRuntimeStatus, DEFAULT_WSL_DISTRIBUTION,
};
use serde::{Deserialize, Serialize};
use serde_json::json;

use crate::host_process::ManagedRuntimeSpec;
use crate::native_host_pack::ManagedManifestMaterial;
use crate::paths::LocalPaths;
use crate::tcp_forwarder::TcpForwarder;

mod bootstrap;
mod clock;
mod images;
mod lifecycle;
mod probe;
mod services;
mod spec;

pub(crate) use bootstrap::*;
pub(crate) use clock::*;
pub(crate) use images::*;
pub(crate) use probe::*;
pub(crate) use services::*;
pub(crate) use spec::*;

#[cfg(test)]
mod tests;

pub struct ManagedRuntimeController {
    runtime: ManagedRuntime,
    spec: ManagedRuntimeSpec,
    forwarders: Mutex<Vec<TcpForwarder>>,
    status: Mutex<Option<ManagedRuntimeStatus>>,
    /// Failed probes since the last healthy one. See [`Self::probe`].
    probes: Mutex<ProbeTracker>,
    clock_keeper: Mutex<Option<ClockKeeper>>,
    /// The last clock-sync failure written to the log, so a standing one is
    /// said once rather than twice a minute for as long as the stack runs.
    last_clock_error: Mutex<Option<String>>,
    sandbox_images: Mutex<SandboxImageStatus>,
    /// The auth service, still coming up while the backend boots.
    ///
    /// See `start_with_progress`. Joined by `await_private_services` before
    /// anything reports ready, so this is a reordering and not a weakening.
    pending_auth: Mutex<Option<thread::JoinHandle<io::Result<()>>>>,
    pending_images: Mutex<Option<thread::JoinHandle<()>>>,
    cancellation: lemma_desktop_process::Cancellation,
}
