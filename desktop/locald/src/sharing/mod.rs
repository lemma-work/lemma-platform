//! Making this installation reachable from somewhere else: a private
//! address on the network, or a public tunnel.
//!
//! Was one 2,254-line file. Split by provider, and by what is being done.

use crate::NoConsoleWindow;
use std::collections::HashMap;
use std::fs::{self, File, OpenOptions};
use std::io;
use std::net::{IpAddr, Ipv4Addr, SocketAddr, TcpListener};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::str::FromStr;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use hyper::client::HttpConnector;
use hyper::header::{HeaderName, HeaderValue, HOST};
use hyper::server::conn::AddrStream;
use hyper::service::{make_service_fn, service_fn};
use hyper::{Body, Client, Request, Response, Server, StatusCode, Uri};
use qrcode::render::svg;
use qrcode::QrCode;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tokio::sync::oneshot;

use crate::host_process::{installation_identity, process_identity, terminate_verified_process};
use crate::port_reservation::PortReservation;

const SHARING_SCHEMA_VERSION: u64 = 2;
const PROCESS_MARKER_SCHEMA_VERSION: u64 = 1;
const PUBLIC_WARNING_OPEN: &str =
    "Anyone with this link can create an account and use this Lemma installation.";
const PUBLIC_WARNING_INVITE_ONLY: &str =
    "Anyone with this link can reach this Lemma's sign-in page. \
     Only people you invite can create an account.";
const LOCAL_WARNING: &str = "Use Local network only on a private Wi-Fi network that you trust.";
const LOCAL_JOIN_OPEN: &str = "Anyone on this network can create an account.";
const LOCAL_JOIN_INVITE_ONLY: &str = "Only people you invite can create an account.";

/// What to confirm before a public link is created, for this join policy.
///
/// The sentence the person agrees to has to describe what will actually be
/// true. It used to be one constant saying anyone could create an account,
/// which was accurate while signup was always open and would be a false
/// alarm now -- and a warning that is wrong in the alarming direction is how
/// people learn to click through the ones that are right.
pub(crate) fn public_warning(who_can_join: WhoCanJoin) -> &'static str {
    match who_can_join {
        WhoCanJoin::Open => PUBLIC_WARNING_OPEN,
        WhoCanJoin::InviteOnly => PUBLIC_WARNING_INVITE_ONLY,
    }
}

pub(crate) fn local_join_warning(who_can_join: WhoCanJoin) -> &'static str {
    match who_can_join {
        WhoCanJoin::Open => LOCAL_JOIN_OPEN,
        WhoCanJoin::InviteOnly => LOCAL_JOIN_INVITE_ONLY,
    }
}
const APPS_LIMITATION: &str =
    "Published pod apps remain local-only because they require wildcard subdomains.";

#[derive(Clone, Copy, Debug, Default, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SharingMode {
    #[default]
    ThisComputer,
    LocalNetwork,
    Public,
}

mod cloudflare;
mod files;
mod gateway;
mod interfaces;
mod ngrok;
mod process;
mod transitions;
mod types;

pub(crate) use cloudflare::*;
pub(crate) use files::*;
pub(crate) use interfaces::*;
pub(crate) use ngrok::*;
pub(crate) use process::*;
pub(crate) use types::*;

#[cfg(test)]
mod tests;

pub struct SharingController {
    root: PathBuf,
    preferences_path: PathBuf,
    frontend_port: u16,
    backend_port: u16,
    local_origin: String,
    state: Mutex<SharingState>,
    active: Mutex<Option<ActiveSharing>>,
    transition_running: AtomicBool,
}

pub(crate) struct SharingState {
    mode: SharingMode,
    phase: String,
    progress: u64,
    canonical_url: String,
    provider: Option<TunnelProvider>,
    tunnel_status: String,
    last_error: Option<String>,
    started_at_ms: Option<u128>,
    selected_interface: Option<String>,
    preferences: SharingPreferences,
}

pub(crate) struct ActiveSharing {
    gateway: GatewayHandle,
    tunnel: Option<OwnedTunnel>,
}

pub(crate) struct OwnedTunnel {
    provider: TunnelProvider,
    /// Ports the tunnel process listens on, on this Mac's loopback: ngrok's
    /// agent API, cloudflared's metrics. Named so the loopback relay can
    /// refuse them -- ngrok's API can start a tunnel.
    local_ports: Vec<u16>,
    executable: PathBuf,
    started_at: Instant,
    child: Child,
    stopped: bool,
    marker_path: PathBuf,
}

pub(crate) struct CloudflareTunnelSelection {
    id: String,
    hostname: String,
    credentials: PathBuf,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct TunnelProcessMarker {
    schema_version: u64,
    installation_id: String,
    provider: TunnelProvider,
    pid: u32,
    executable: String,
    start_identity: String,
}

pub(crate) struct GatewayHandle {
    address: SocketAddr,
    shutdown: Option<oneshot::Sender<()>>,
    thread: Option<thread::JoinHandle<()>>,
}

#[derive(Debug)]
pub struct PreparedSharing {
    pub mode: SharingMode,
    pub origin: String,
}

impl Drop for SharingController {
    fn drop(&mut self) {
        self.stop_active();
    }
}

impl OwnedTunnel {
    pub(crate) fn stop(&mut self) {
        if self.stopped {
            return;
        }
        self.stopped = true;
        let _identity = (&self.executable, self.started_at);
        terminate_owned_child(&mut self.child);
        let _ = fs::remove_file(&self.marker_path);
    }
}

impl Drop for OwnedTunnel {
    fn drop(&mut self) {
        self.stop();
    }
}

impl SharingController {
    /// The loopback ports sharing is listening on right now: its gateway and
    /// whatever the tunnel process serves locally. Empty while sharing is off.
    pub(crate) fn listening_ports(&self) -> Vec<u16> {
        let active = self.active.lock().expect("sharing active lock poisoned");
        let Some(active) = active.as_ref() else {
            return Vec::new();
        };
        let mut ports = vec![active.gateway.address.port()];
        if let Some(tunnel) = active.tunnel.as_ref() {
            ports.extend(&tunnel.local_ports);
        }
        ports
    }

    pub fn load(
        root: &Path,
        local_origin: String,
        frontend_port: u16,
        backend_port: u16,
    ) -> io::Result<Arc<Self>> {
        fs::create_dir_all(root)?;
        reclaim_owned_tunnel(root)?;
        let preferences_path = root.join("sharing.json");
        let mut preferences = fs::read(&preferences_path)
            .ok()
            .and_then(|raw| serde_json::from_slice::<SharingPreferences>(&raw).ok())
            .unwrap_or_default();
        let migrated = preferences.schema_version != SHARING_SCHEMA_VERSION;
        if migrated
            && preferences.cloudflare_tunnel_id.is_some()
            && !preferences.cloudflare_tunnel_owned
        {
            preferences.cloudflare_setup = CloudflareSetup::Existing;
            preferences.cloudflare_dns_routed = false;
        }
        if preferences.schema_version != SHARING_SCHEMA_VERSION {
            preferences.schema_version = SHARING_SCHEMA_VERSION;
        }
        if migrated {
            persist_private_json(&preferences_path, &preferences)?;
        }
        Ok(Arc::new(Self {
            root: root.to_path_buf(),
            preferences_path,
            frontend_port,
            backend_port,
            local_origin: local_origin.clone(),
            state: Mutex::new(SharingState {
                mode: SharingMode::ThisComputer,
                phase: "ready".into(),
                progress: 100,
                canonical_url: local_origin,
                provider: preferences.last_provider,
                tunnel_status: "stopped".into(),
                last_error: None,
                started_at_ms: None,
                selected_interface: preferences.selected_interface.clone(),
                preferences,
            }),
            active: Mutex::new(None),
            transition_running: AtomicBool::new(false),
        }))
    }
}
