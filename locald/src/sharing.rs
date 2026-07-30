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

const SHARING_SCHEMA_VERSION: u64 = 2;
const PROCESS_MARKER_SCHEMA_VERSION: u64 = 1;
const PUBLIC_WARNING: &str =
    "Anyone with this link can create an account and use this Lemma installation.";
const LOCAL_WARNING: &str = "Use Local network only on a private Wi-Fi network that you trust.";
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

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum TunnelProvider {
    Ngrok,
    Cloudflare,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CloudflareSetup {
    #[default]
    Automatic,
    Existing,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
#[serde(default, deny_unknown_fields)]
pub struct SharingPreferences {
    pub schema_version: u64,
    pub last_provider: Option<TunnelProvider>,
    pub selected_interface: Option<String>,
    pub cloudflare_setup: CloudflareSetup,
    pub cloudflare_tunnel_id: Option<String>,
    pub cloudflare_tunnel_name: Option<String>,
    pub cloudflare_hostname: Option<String>,
    pub cloudflare_tunnel_owned: bool,
    pub cloudflare_dns_routed: bool,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct EnableSharingRequest {
    pub mode: SharingMode,
    pub interface: Option<String>,
    pub provider: Option<TunnelProvider>,
    pub cloudflare_setup: CloudflareSetup,
    pub cloudflare_tunnel_id: Option<String>,
    pub cloudflare_tunnel_name: Option<String>,
    pub hostname: Option<String>,
    pub public_warning_confirmed: bool,
}

impl Default for EnableSharingRequest {
    fn default() -> Self {
        Self {
            mode: SharingMode::ThisComputer,
            interface: None,
            provider: None,
            cloudflare_setup: CloudflareSetup::Automatic,
            cloudflare_tunnel_id: None,
            cloudflare_tunnel_name: None,
            hostname: None,
            public_warning_confirmed: false,
        }
    }
}

#[derive(Clone, Debug, Serialize)]
pub struct NetworkInterface {
    pub name: String,
    pub address: String,
    pub label: String,
}

#[derive(Clone, Debug, Default, Serialize)]
pub struct ProviderReadiness {
    pub installed: bool,
    pub authenticated: bool,
    pub executable: Option<String>,
    pub version: Option<String>,
    pub message: Option<String>,
    pub instructions: Vec<String>,
    pub tunnels: Vec<CloudflareTunnel>,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct CloudflareTunnel {
    pub id: String,
    pub name: String,
}

#[derive(Clone, Debug, Serialize)]
pub struct SharingSnapshot {
    pub mode: SharingMode,
    pub phase: String,
    pub progress: u64,
    pub canonical_url: String,
    pub provider: Option<TunnelProvider>,
    pub provider_readiness: HashMap<String, ProviderReadiness>,
    pub tunnel_status: String,
    pub warnings: Vec<String>,
    pub last_error: Option<String>,
    pub started_at_ms: Option<u128>,
    pub interfaces: Vec<NetworkInterface>,
    pub selected_interface: Option<String>,
    pub qr_svg: Option<String>,
    pub preferences: SharingPreferences,
    pub transition_running: bool,
    pub public_confirmation: String,
    pub apps_limitation: String,
}

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

struct SharingState {
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

struct ActiveSharing {
    gateway: GatewayHandle,
    tunnel: Option<OwnedTunnel>,
}

struct OwnedTunnel {
    provider: TunnelProvider,
    executable: PathBuf,
    started_at: Instant,
    child: Child,
    stopped: bool,
    marker_path: PathBuf,
}

struct CloudflareTunnelSelection {
    id: String,
    hostname: String,
    credentials: PathBuf,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct TunnelProcessMarker {
    schema_version: u64,
    installation_id: String,
    provider: TunnelProvider,
    pid: u32,
    executable: String,
    start_identity: String,
}

struct GatewayHandle {
    address: SocketAddr,
    shutdown: Option<oneshot::Sender<()>>,
    thread: Option<thread::JoinHandle<()>>,
}

#[derive(Debug)]
pub struct PreparedSharing {
    pub mode: SharingMode,
    pub origin: String,
}

impl SharingController {
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

    pub fn snapshot(&self, include_preflight: bool) -> SharingSnapshot {
        self.observe_tunnel_exit();
        let interfaces = private_ipv4_interfaces();
        let state = self.state.lock().expect("sharing state lock poisoned");
        let mut warnings = Vec::new();
        match state.mode {
            SharingMode::ThisComputer => {}
            SharingMode::LocalNetwork => warnings.push(LOCAL_WARNING.into()),
            SharingMode::Public => warnings.push(PUBLIC_WARNING.into()),
        }
        if state.mode != SharingMode::ThisComputer {
            warnings.push(APPS_LIMITATION.into());
        }
        let qr_svg = if state.mode == SharingMode::LocalNetwork
            && state.phase == "ready"
            && !state.canonical_url.is_empty()
        {
            render_qr(&state.canonical_url)
        } else {
            None
        };
        let mut provider_readiness = HashMap::new();
        if include_preflight {
            provider_readiness.insert("ngrok".into(), preflight_ngrok());
            provider_readiness.insert("cloudflare".into(), preflight_cloudflare());
        }
        SharingSnapshot {
            mode: state.mode,
            phase: state.phase.clone(),
            progress: state.progress,
            canonical_url: state.canonical_url.clone(),
            provider: state.provider,
            provider_readiness,
            tunnel_status: state.tunnel_status.clone(),
            warnings,
            last_error: state.last_error.clone(),
            started_at_ms: state.started_at_ms,
            interfaces,
            selected_interface: state.selected_interface.clone(),
            qr_svg,
            preferences: state.preferences.clone(),
            transition_running: self.transition_running.load(Ordering::Acquire),
            public_confirmation: PUBLIC_WARNING.into(),
            apps_limitation: APPS_LIMITATION.into(),
        }
    }

    pub fn preflight(&self, provider: Option<TunnelProvider>) -> Value {
        let interfaces = private_ipv4_interfaces();
        match provider {
            Some(TunnelProvider::Ngrok) => json!({
                "provider": "ngrok",
                "readiness": preflight_ngrok(),
                "interfaces": interfaces,
            }),
            Some(TunnelProvider::Cloudflare) => json!({
                "provider": "cloudflare",
                "readiness": preflight_cloudflare(),
                "interfaces": interfaces,
            }),
            None => json!({
                "providers": {
                    "ngrok": preflight_ngrok(),
                    "cloudflare": preflight_cloudflare(),
                },
                "interfaces": interfaces,
            }),
        }
    }

    pub fn prepare_enable(&self, request: &EnableSharingRequest) -> io::Result<PreparedSharing> {
        if request.mode == SharingMode::ThisComputer {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "use sharing.disable to return to This computer",
            ));
        }
        self.begin_transition()?;
        let result = self.prepare_enable_inner(request);
        if let Err(error) = &result {
            self.fail_transition(error.to_string());
        }
        result
    }

    fn prepare_enable_inner(&self, request: &EnableSharingRequest) -> io::Result<PreparedSharing> {
        if self
            .active
            .lock()
            .expect("sharing active lock poisoned")
            .is_some()
        {
            return Err(io::Error::new(
                io::ErrorKind::AlreadyExists,
                "sharing is already active; disable it before changing modes",
            ));
        }
        if request.mode == SharingMode::Public && !request.public_warning_confirmed {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                PUBLIC_WARNING,
            ));
        }
        {
            let mut state = self.state.lock().expect("sharing state lock poisoned");
            state.phase = "preflight".into();
            state.progress = 10;
            state.last_error = None;
            state.provider = request.provider;
        }

        let (bind_ip, selected_interface) = match request.mode {
            SharingMode::LocalNetwork => {
                let selection = request.interface.as_deref().ok_or_else(|| {
                    io::Error::new(
                        io::ErrorKind::InvalidInput,
                        "choose a private IPv4 network interface",
                    )
                })?;
                let interface = resolve_private_interface(selection)?;
                (
                    IpAddr::V4(Ipv4Addr::from_str(&interface.address).map_err(io::Error::other)?),
                    Some(interface.name),
                )
            }
            SharingMode::Public => (IpAddr::V4(Ipv4Addr::LOCALHOST), None),
            SharingMode::ThisComputer => unreachable!(),
        };

        let gateway =
            GatewayHandle::start(bind_ip, self.frontend_port, self.backend_port, request.mode)?;
        let gateway_origin = format!("http://{}:{}", display_ip(bind_ip), gateway.address.port());
        {
            let mut state = self.state.lock().expect("sharing state lock poisoned");
            state.phase = "gateway".into();
            state.progress = 30;
            state.tunnel_status = if request.mode == SharingMode::Public {
                "starting"
            } else {
                "not_required"
            }
            .into();
        }

        let (canonical_url, tunnel) = match request.mode {
            SharingMode::LocalNetwork => (gateway_origin, None),
            SharingMode::Public => {
                let provider = request.provider.ok_or_else(|| {
                    io::Error::new(io::ErrorKind::InvalidInput, "choose ngrok or Cloudflare")
                })?;
                let (url, tunnel) = match provider {
                    TunnelProvider::Ngrok => self.start_ngrok(&gateway_origin)?,
                    TunnelProvider::Cloudflare => {
                        self.start_cloudflare(request, &gateway_origin)?
                    }
                };
                (url, Some(tunnel))
            }
            SharingMode::ThisComputer => unreachable!(),
        };

        {
            let mut state = self.state.lock().expect("sharing state lock poisoned");
            state.mode = request.mode;
            state.phase = "restarting".into();
            state.progress = 55;
            state.canonical_url = canonical_url.clone();
            state.tunnel_status = if tunnel.is_some() {
                "connected"
            } else {
                "not_required"
            }
            .into();
            state.started_at_ms = Some(now_ms());
            state.selected_interface = selected_interface.clone();
        }
        *self.active.lock().expect("sharing active lock poisoned") =
            Some(ActiveSharing { gateway, tunnel });
        Ok(PreparedSharing {
            mode: request.mode,
            origin: canonical_url,
        })
    }

    pub fn commit_enable(&self, request: &EnableSharingRequest) -> io::Result<()> {
        {
            let mut state = self.state.lock().expect("sharing state lock poisoned");
            state.phase = "ready".into();
            state.progress = 100;
            state.last_error = None;
            if let Some(interface) = request.interface.as_ref() {
                state.preferences.selected_interface = Some(interface.clone());
            }
            if let Some(provider) = request.provider {
                state.preferences.last_provider = Some(provider);
            }
            if request.provider == Some(TunnelProvider::Cloudflare) {
                state.preferences.cloudflare_setup = request.cloudflare_setup;
                if request.cloudflare_setup == CloudflareSetup::Existing {
                    state.preferences.cloudflare_tunnel_id = request.cloudflare_tunnel_id.clone();
                    state.preferences.cloudflare_tunnel_name =
                        request.cloudflare_tunnel_name.clone();
                    state.preferences.cloudflare_hostname = request.hostname.clone();
                    state.preferences.cloudflare_tunnel_owned = false;
                    state.preferences.cloudflare_dns_routed = false;
                }
            }
            state.preferences.schema_version = SHARING_SCHEMA_VERSION;
            persist_private_json(&self.preferences_path, &state.preferences)?;
        }
        self.transition_running.store(false, Ordering::Release);
        Ok(())
    }

    pub fn rollback_enable(&self, message: impl Into<String>) {
        self.stop_active();
        self.fail_transition(message.into());
    }

    pub fn begin_disable(&self) -> io::Result<bool> {
        self.begin_transition()?;
        let active = self
            .active
            .lock()
            .expect("sharing active lock poisoned")
            .is_some();
        if !active {
            self.transition_running.store(false, Ordering::Release);
            return Ok(false);
        }
        let mut state = self.state.lock().expect("sharing state lock poisoned");
        state.phase = "restarting".into();
        state.progress = 40;
        state.last_error = None;
        Ok(true)
    }

    pub fn commit_disable(&self) {
        self.stop_active();
        {
            let mut state = self.state.lock().expect("sharing state lock poisoned");
            state.mode = SharingMode::ThisComputer;
            state.phase = "ready".into();
            state.progress = 100;
            state.canonical_url = self.local_origin.clone();
            state.tunnel_status = "stopped".into();
            state.started_at_ms = None;
            state.last_error = None;
        }
        self.transition_running.store(false, Ordering::Release);
    }

    pub fn abort_disable(&self, message: impl Into<String>) {
        let mut state = self.state.lock().expect("sharing state lock poisoned");
        state.phase = "ready".into();
        state.progress = 100;
        state.last_error = Some(message.into());
        self.transition_running.store(false, Ordering::Release);
    }

    pub fn force_disable(&self) {
        self.stop_active();
        {
            let mut state = self.state.lock().expect("sharing state lock poisoned");
            state.mode = SharingMode::ThisComputer;
            state.phase = "ready".into();
            state.progress = 100;
            state.canonical_url = self.local_origin.clone();
            state.tunnel_status = "stopped".into();
            state.started_at_ms = None;
        }
        self.transition_running.store(false, Ordering::Release);
    }

    pub fn local_origin(&self) -> &str {
        &self.local_origin
    }

    pub fn active_mode(&self) -> SharingMode {
        self.state.lock().expect("sharing state lock poisoned").mode
    }

    pub fn poll_failure(&self) -> Option<String> {
        self.observe_tunnel_exit()
    }

    fn begin_transition(&self) -> io::Result<()> {
        self.transition_running
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .map(|_| ())
            .map_err(|_| {
                io::Error::new(
                    io::ErrorKind::WouldBlock,
                    "another sharing transition is already running",
                )
            })
    }

    fn fail_transition(&self, message: String) {
        let mut state = self.state.lock().expect("sharing state lock poisoned");
        state.mode = SharingMode::ThisComputer;
        state.phase = "error".into();
        state.progress = 0;
        state.canonical_url = self.local_origin.clone();
        state.tunnel_status = "stopped".into();
        state.started_at_ms = None;
        state.last_error = Some(redact_error(&message));
        self.transition_running.store(false, Ordering::Release);
    }

    fn stop_active(&self) {
        if let Some(mut active) = self
            .active
            .lock()
            .expect("sharing active lock poisoned")
            .take()
        {
            if let Some(tunnel) = active.tunnel.as_mut() {
                tunnel.stop();
            }
            active.gateway.stop();
        }
    }

    fn observe_tunnel_exit(&self) -> Option<String> {
        let mut active = self.active.lock().expect("sharing active lock poisoned");
        let Some(active) = active.as_mut() else {
            return None;
        };
        if active.tunnel.is_none() && active.gateway.address.ip() != IpAddr::V4(Ipv4Addr::LOCALHOST)
        {
            let address = active.gateway.address.ip().to_string();
            let available = private_ipv4_interfaces()
                .iter()
                .any(|interface| interface.address == address);
            if !available {
                let message =
                    "the selected local-network interface is no longer available".to_owned();
                let mut state = self.state.lock().expect("sharing state lock poisoned");
                state.phase = "error".into();
                state.tunnel_status = "interface_lost".into();
                state.last_error = Some(message.clone());
                return Some(message);
            }
        }
        let Some(tunnel) = active.tunnel.as_mut() else {
            return None;
        };
        match tunnel.child.try_wait() {
            Ok(Some(status)) => {
                let message = format!(
                    "{} tunnel exited unexpectedly with {status}",
                    provider_name(tunnel.provider)
                );
                let mut state = self.state.lock().expect("sharing state lock poisoned");
                state.phase = "error".into();
                state.tunnel_status = "exited".into();
                state.last_error = Some(message.clone());
                Some(message)
            }
            Ok(None) => None,
            Err(error) => Some(format!("could not inspect tunnel process: {error}")),
        }
    }

    fn start_ngrok(&self, gateway_origin: &str) -> io::Result<(String, OwnedTunnel)> {
        let readiness = preflight_ngrok();
        let executable = readiness
            .executable
            .as_ref()
            .map(PathBuf::from)
            .ok_or_else(|| {
                io::Error::new(
                    io::ErrorKind::NotFound,
                    "ngrok is not installed; install it and run `ngrok config add-authtoken …`",
                )
            })?;
        if !readiness.authenticated {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                readiness
                    .message
                    .unwrap_or_else(|| "ngrok is not authenticated".into()),
            ));
        }
        let user_config = ngrok_config_path(&executable)?;
        let inspection_port = reserve_loopback_port()?;
        let supplemental = self.root.join("ngrok-lemma.yml");
        let contents = format!("version: 3\nagent:\n  web_addr: 127.0.0.1:{inspection_port}\n");
        write_private(&supplemental, contents.as_bytes())?;
        let log_path = self.root.join("logs/ngrok.log");
        let log = bounded_log(&log_path)?;
        let error_log = log.try_clone()?;
        let mut command = Command::new(&executable);
        command
            .arg("http")
            .arg(gateway_origin)
            .arg("--config")
            .arg(&user_config)
            .arg("--config")
            .arg(&supplemental)
            .arg("--name")
            .arg("lemma-local")
            .arg("--metadata")
            .arg("owner=lemma-locald")
            .arg("--log")
            .arg("stdout")
            .arg("--log-format")
            .arg("json")
            .stdin(Stdio::null())
            .stdout(Stdio::from(log))
            .stderr(Stdio::from(error_log));
        prepare_owned_command(&mut command);
        let mut child = command.spawn().map_err(|error| {
            io::Error::other(format!(
                "could not start ngrok at {}: {error}",
                executable.display()
            ))
        })?;
        let marker_path = self.root.join("sharing-process.json");
        record_owned_tunnel(
            &self.root,
            &marker_path,
            TunnelProvider::Ngrok,
            &executable,
            &child,
        )
        .inspect_err(|_| terminate_owned_child(&mut child))?;
        let agent_base = format!("http://127.0.0.1:{inspection_port}");
        let url = wait_for_ngrok_url(&agent_base, &mut child, Duration::from_secs(30))
            .inspect_err(|_| {
                terminate_owned_child(&mut child);
                let _ = fs::remove_file(&marker_path);
            })?;
        Ok((
            url,
            OwnedTunnel {
                provider: TunnelProvider::Ngrok,
                executable,
                started_at: Instant::now(),
                child,
                stopped: false,
                marker_path,
            },
        ))
    }

    fn start_cloudflare(
        &self,
        request: &EnableSharingRequest,
        gateway_origin: &str,
    ) -> io::Result<(String, OwnedTunnel)> {
        let readiness = preflight_cloudflare();
        let executable = readiness
            .executable
            .as_ref()
            .map(PathBuf::from)
            .ok_or_else(|| {
                io::Error::new(io::ErrorKind::NotFound, "cloudflared is not installed")
            })?;
        if !readiness.authenticated {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "Run `cloudflared tunnel login` once, then return to Lemma.",
            ));
        }
        let hostname = normalize_hostname(request.hostname.as_deref().unwrap_or_default())?;
        {
            let mut state = self.state.lock().expect("sharing state lock poisoned");
            state.phase = if request.cloudflare_setup == CloudflareSetup::Automatic {
                "provisioning_tunnel"
            } else {
                "checking_tunnel"
            }
            .into();
            state.progress = 35;
        }
        let selection = match request.cloudflare_setup {
            CloudflareSetup::Automatic => {
                self.ensure_managed_cloudflare_tunnel(&executable, &readiness, &hostname)?
            }
            CloudflareSetup::Existing => {
                let tunnel_id = request
                    .cloudflare_tunnel_id
                    .as_deref()
                    .map(str::trim)
                    .filter(|value| !value.is_empty())
                    .ok_or_else(|| {
                        io::Error::new(io::ErrorKind::InvalidInput, "choose a named tunnel")
                    })?;
                if !readiness
                    .tunnels
                    .iter()
                    .any(|tunnel| tunnel.id == tunnel_id)
                {
                    return Err(io::Error::new(
                        io::ErrorKind::NotFound,
                        "the selected named Cloudflare tunnel is not available locally",
                    ));
                }
                CloudflareTunnelSelection {
                    id: tunnel_id.to_owned(),
                    hostname,
                    credentials: cloudflare_credentials_path(tunnel_id)?,
                }
            }
        };
        {
            let mut state = self.state.lock().expect("sharing state lock poisoned");
            state.phase = "starting_tunnel".into();
            state.progress = 47;
        }
        let config = self.root.join("cloudflared-lemma.yml");
        let config_json_path =
            serde_json::to_string(selection.credentials.to_string_lossy().as_ref())
                .map_err(io::Error::other)?;
        let contents = format!(
            "tunnel: {}\ncredentials-file: {config_json_path}\ningress:\n  - hostname: {}\n    service: {gateway_origin}\n  - service: http_status:404\n",
            selection.id, selection.hostname
        );
        write_private(&config, contents.as_bytes())?;
        let log_path = self.root.join("logs/cloudflared.log");
        let log = bounded_log(&log_path)?;
        let error_log = log.try_clone()?;
        let mut command = Command::new(&executable);
        command
            .arg("tunnel")
            .arg("--config")
            .arg(&config)
            .arg("--no-autoupdate")
            .arg("--metrics")
            .arg("127.0.0.1:0")
            .arg("--loglevel")
            .arg("info")
            .arg("run")
            .arg(&selection.id)
            .stdin(Stdio::null())
            .stdout(Stdio::from(log))
            .stderr(Stdio::from(error_log));
        prepare_owned_command(&mut command);
        let mut child = command.spawn().map_err(|error| {
            io::Error::other(format!(
                "could not start cloudflared at {}: {error}",
                executable.display()
            ))
        })?;
        let marker_path = self.root.join("sharing-process.json");
        record_owned_tunnel(
            &self.root,
            &marker_path,
            TunnelProvider::Cloudflare,
            &executable,
            &child,
        )
        .inspect_err(|_| terminate_owned_child(&mut child))?;
        thread::sleep(Duration::from_millis(900));
        if let Some(status) = child.try_wait()? {
            let _ = fs::remove_file(&marker_path);
            return Err(io::Error::other(format!(
                "cloudflared exited during startup with {status}; see {}",
                log_path.display()
            )));
        }
        Ok((
            format!("https://{}", selection.hostname),
            OwnedTunnel {
                provider: TunnelProvider::Cloudflare,
                executable,
                started_at: Instant::now(),
                child,
                stopped: false,
                marker_path,
            },
        ))
    }

    fn ensure_managed_cloudflare_tunnel(
        &self,
        executable: &Path,
        readiness: &ProviderReadiness,
        hostname: &str,
    ) -> io::Result<CloudflareTunnelSelection> {
        let name = managed_cloudflare_tunnel_name(&self.root)?;
        let credentials_dir = self.root.join("cloudflare");
        let credentials = credentials_dir.join("lemma-tunnel.json");
        let saved = self
            .state
            .lock()
            .expect("sharing state lock poisoned")
            .preferences
            .clone();

        let mut tunnel = if saved.cloudflare_tunnel_owned
            && saved.cloudflare_setup == CloudflareSetup::Automatic
        {
            let id = saved.cloudflare_tunnel_id.clone().ok_or_else(|| {
                io::Error::other("Lemma's Cloudflare tunnel identity is incomplete")
            })?;
            let saved_name = saved
                .cloudflare_tunnel_name
                .clone()
                .ok_or_else(|| io::Error::other("Lemma's Cloudflare tunnel name is incomplete"))?;
            if !credentials.is_file() {
                return Err(io::Error::new(
                    io::ErrorKind::NotFound,
                    "Lemma's Cloudflare tunnel credential is missing. Remove the stale tunnel in the Cloudflare dashboard before setting it up again.",
                ));
            }
            let listed = readiness.tunnels.iter().any(|item| item.id == id);
            if !listed {
                return Err(io::Error::new(
                    io::ErrorKind::NotFound,
                    "Lemma's managed tunnel no longer exists in this Cloudflare account. Remove its stale DNS record in the Cloudflare dashboard, then set it up again.",
                ));
            }
            CloudflareTunnel {
                id,
                name: saved_name,
            }
        } else if let Some(existing) = readiness
            .tunnels
            .iter()
            .find(|item| item.name == name)
            .cloned()
        {
            if !credentials.is_file() {
                return Err(io::Error::new(
                    io::ErrorKind::AlreadyExists,
                    format!(
                        "Cloudflare already has the installation-owned tunnel `{name}`, but its local credential is missing. Delete that tunnel in the Cloudflare dashboard before retrying."
                    ),
                ));
            }
            existing
        } else {
            if credentials.exists() {
                return Err(io::Error::new(
                    io::ErrorKind::AlreadyExists,
                    "A stale Lemma Cloudflare credential exists without its tunnel identity. Remove the stale Cloudflare setup before retrying.",
                ));
            }
            ensure_private_directory(&credentials_dir)?;
            let mut command = Command::new(executable);
            command
                .arg("tunnel")
                .arg("--no-autoupdate")
                .arg("create")
                .arg("--output")
                .arg("json")
                .arg("--credentials-file")
                .arg(&credentials)
                .arg(&name);
            let stdout = checked_command_output(
                &mut command,
                "Cloudflare could not create Lemma's named tunnel",
            )?;
            let created = parse_created_cloudflare_tunnel(&stdout).or_else(|| {
                preflight_cloudflare()
                    .tunnels
                    .into_iter()
                    .find(|item| item.name == name)
            });
            let created = created.ok_or_else(|| {
                io::Error::other(
                    "Cloudflare created the tunnel but did not report its identity. Inspect `cloudflared tunnel list` before retrying.",
                )
            })?;
            if !credentials.is_file() {
                return Err(io::Error::other(
                    "Cloudflare created the tunnel without writing its requested credential file",
                ));
            }
            make_private_file(&credentials)?;
            created
        };

        if tunnel.name.is_empty() {
            tunnel.name = name;
        }
        if saved.cloudflare_dns_routed {
            let saved_hostname = saved.cloudflare_hostname.as_deref().unwrap_or_default();
            if saved_hostname != hostname {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidInput,
                    format!(
                        "This Lemma tunnel is already routed at `{saved_hostname}`. Use that hostname, or remove its DNS record in the Cloudflare dashboard before changing it."
                    ),
                ));
            }
            self.persist_managed_cloudflare_preferences(&tunnel, hostname, true)?;
        } else {
            self.persist_managed_cloudflare_preferences(&tunnel, hostname, false)?;
            {
                let mut state = self.state.lock().expect("sharing state lock poisoned");
                state.phase = "provisioning_dns".into();
                state.progress = 42;
            }
            let mut command = Command::new(executable);
            command
                .arg("tunnel")
                .arg("--no-autoupdate")
                .arg("route")
                .arg("dns")
                .arg(&tunnel.id)
                .arg(hostname);
            checked_command_output(
                &mut command,
                "Cloudflare could not create the hostname without overwriting an existing DNS record. Choose an unused hostname",
            )?;
            self.persist_managed_cloudflare_preferences(&tunnel, hostname, true)?;
        }
        Ok(CloudflareTunnelSelection {
            id: tunnel.id,
            hostname: hostname.to_owned(),
            credentials,
        })
    }

    fn persist_managed_cloudflare_preferences(
        &self,
        tunnel: &CloudflareTunnel,
        hostname: &str,
        dns_routed: bool,
    ) -> io::Result<()> {
        let mut state = self.state.lock().expect("sharing state lock poisoned");
        state.preferences.schema_version = SHARING_SCHEMA_VERSION;
        state.preferences.cloudflare_setup = CloudflareSetup::Automatic;
        state.preferences.cloudflare_tunnel_id = Some(tunnel.id.clone());
        state.preferences.cloudflare_tunnel_name = Some(tunnel.name.clone());
        state.preferences.cloudflare_hostname = Some(hostname.to_owned());
        state.preferences.cloudflare_tunnel_owned = true;
        state.preferences.cloudflare_dns_routed = dns_routed;
        persist_private_json(&self.preferences_path, &state.preferences)
    }
}

impl Drop for SharingController {
    fn drop(&mut self) {
        self.stop_active();
    }
}

impl OwnedTunnel {
    fn stop(&mut self) {
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

impl GatewayHandle {
    fn start(
        bind_ip: IpAddr,
        frontend_port: u16,
        backend_port: u16,
        mode: SharingMode,
    ) -> io::Result<Self> {
        let listener = TcpListener::bind(SocketAddr::new(bind_ip, 0))?;
        listener.set_nonblocking(true)?;
        let address = listener.local_addr()?;
        let (shutdown_send, shutdown_receive) = oneshot::channel::<()>();
        let thread = thread::Builder::new()
            .name("lemma-sharing-gateway".into())
            .spawn(move || {
                let runtime = match tokio::runtime::Builder::new_multi_thread()
                    .worker_threads(2)
                    .enable_all()
                    .build()
                {
                    Ok(runtime) => runtime,
                    Err(_) => return,
                };
                runtime.block_on(async move {
                    let client = Client::new();
                    let make_service = make_service_fn(move |connection: &AddrStream| {
                        let client = client.clone();
                        let remote = connection.remote_addr();
                        async move {
                            Ok::<_, hyper::Error>(service_fn(move |request| {
                                proxy_request(
                                    request,
                                    client.clone(),
                                    remote,
                                    frontend_port,
                                    backend_port,
                                    mode,
                                )
                            }))
                        }
                    });
                    let server = match Server::from_tcp(listener) {
                        Ok(server) => server.serve(make_service),
                        Err(_) => return,
                    };
                    let _ = server
                        .with_graceful_shutdown(async {
                            let _ = shutdown_receive.await;
                        })
                        .await;
                });
            })?;
        Ok(Self {
            address,
            shutdown: Some(shutdown_send),
            thread: Some(thread),
        })
    }

    fn stop(&mut self) {
        if let Some(shutdown) = self.shutdown.take() {
            let _ = shutdown.send(());
        }
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

impl Drop for GatewayHandle {
    fn drop(&mut self) {
        self.stop();
    }
}

async fn proxy_request(
    mut request: Request<Body>,
    client: Client<HttpConnector, Body>,
    remote: SocketAddr,
    frontend_port: u16,
    backend_port: u16,
    mode: SharingMode,
) -> Result<Response<Body>, hyper::Error> {
    let original_host = request
        .headers()
        .get(HOST)
        .cloned()
        .unwrap_or_else(|| HeaderValue::from_static("localhost"));
    strip_forwarding_headers(request.headers_mut());
    let forwarded_proto = if mode == SharingMode::Public {
        "https"
    } else {
        "http"
    };
    let host_text = original_host.to_str().unwrap_or("localhost");
    let forwarded = format!(
        "for={};proto={forwarded_proto};host=\"{}\"",
        remote.ip(),
        host_text.replace('"', "")
    );
    insert_header(request.headers_mut(), "forwarded", &forwarded);
    insert_header(
        request.headers_mut(),
        "x-forwarded-for",
        &remote.ip().to_string(),
    );
    insert_header(request.headers_mut(), "x-forwarded-proto", forwarded_proto);
    insert_header(request.headers_mut(), "x-forwarded-host", host_text);
    request.headers_mut().insert(HOST, original_host);

    let path_and_query = request
        .uri()
        .path_and_query()
        .map(|value| value.as_str())
        .unwrap_or("/");
    let (port, upstream_path) = proxy_target(path_and_query, frontend_port, backend_port);
    let target = format!("http://127.0.0.1:{port}{upstream_path}");
    *request.uri_mut() = match Uri::from_str(&target) {
        Ok(uri) => uri,
        Err(_) => {
            return Ok(simple_response(
                StatusCode::BAD_GATEWAY,
                "invalid upstream URI",
            ))
        }
    };

    let websocket = is_websocket_upgrade(&request);
    let downstream_upgrade = websocket.then(|| hyper::upgrade::on(&mut request));
    let mut response = client.request(request).await?;
    if websocket && response.status() == StatusCode::SWITCHING_PROTOCOLS {
        let upstream_upgrade = hyper::upgrade::on(&mut response);
        if let Some(downstream_upgrade) = downstream_upgrade {
            tokio::spawn(async move {
                if let (Ok(mut downstream), Ok(mut upstream)) =
                    (downstream_upgrade.await, upstream_upgrade.await)
                {
                    let _ = tokio::io::copy_bidirectional(&mut downstream, &mut upstream).await;
                }
            });
        }
    }
    Ok(response)
}

fn proxy_target(path_and_query: &str, frontend_port: u16, backend_port: u16) -> (u16, String) {
    if let Some(path) = path_and_query.strip_prefix("/_lemma/api") {
        (
            backend_port,
            if path.is_empty() {
                "/".to_owned()
            } else if path.starts_with('/') {
                path.to_owned()
            } else {
                format!("/{path}")
            },
        )
    } else {
        (frontend_port, path_and_query.to_owned())
    }
}

fn strip_forwarding_headers(headers: &mut hyper::HeaderMap) {
    let names: Vec<HeaderName> = headers
        .keys()
        .filter(|name| {
            let name = name.as_str();
            name == "forwarded" || name.starts_with("x-forwarded-")
        })
        .cloned()
        .collect();
    for name in names {
        headers.remove(name);
    }
}

fn insert_header(headers: &mut hyper::HeaderMap, name: &'static str, value: &str) {
    if let Ok(value) = HeaderValue::from_str(value) {
        headers.insert(HeaderName::from_static(name), value);
    }
}

fn is_websocket_upgrade(request: &Request<Body>) -> bool {
    request
        .headers()
        .get("upgrade")
        .and_then(|value| value.to_str().ok())
        .is_some_and(|value| value.eq_ignore_ascii_case("websocket"))
}

fn simple_response(status: StatusCode, message: &str) -> Response<Body> {
    Response::builder()
        .status(status)
        .header("content-type", "text/plain; charset=utf-8")
        .body(Body::from(message.to_owned()))
        .unwrap_or_else(|_| Response::new(Body::empty()))
}

fn private_ipv4_interfaces() -> Vec<NetworkInterface> {
    let mut interfaces: Vec<_> = if_addrs::get_if_addrs()
        .unwrap_or_default()
        .into_iter()
        .filter_map(|interface| match interface.ip() {
            IpAddr::V4(address)
                if address.is_private() && !address.is_loopback() && !address.is_link_local() =>
            {
                Some(NetworkInterface {
                    label: format!("{} · {}", interface.name, address),
                    name: interface.name,
                    address: address.to_string(),
                })
            }
            _ => None,
        })
        .collect();
    interfaces.sort_by(|left, right| {
        left.name
            .cmp(&right.name)
            .then(left.address.cmp(&right.address))
    });
    interfaces.dedup_by(|left, right| left.name == right.name && left.address == right.address);
    interfaces
}

fn resolve_private_interface(selection: &str) -> io::Result<NetworkInterface> {
    private_ipv4_interfaces()
        .into_iter()
        .find(|interface| interface.name == selection || interface.address == selection)
        .ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::AddrNotAvailable,
                "the selected private IPv4 interface is no longer available",
            )
        })
}

fn preflight_ngrok() -> ProviderReadiness {
    let Some(executable) = find_executable("ngrok") else {
        return ProviderReadiness {
            message: Some("ngrok is not installed.".into()),
            instructions: vec![
                "Install ngrok using its official package or `brew install ngrok/ngrok/ngrok`."
                    .into(),
                "Then run `ngrok config add-authtoken …` in Terminal.".into(),
            ],
            ..Default::default()
        };
    };
    let version = command_text(&executable, &["version"]);
    match command_text(&executable, &["config", "check"]) {
        Ok(config) if config.to_ascii_lowercase().contains("valid configuration") => {
            ProviderReadiness {
                installed: true,
                authenticated: true,
                executable: Some(executable.to_string_lossy().into_owned()),
                version: version.ok().map(first_line),
                message: Some(first_line(config)),
                instructions: Vec::new(),
                tunnels: Vec::new(),
            }
        }
        Ok(message) | Err(message) => ProviderReadiness {
            installed: true,
            authenticated: false,
            executable: Some(executable.to_string_lossy().into_owned()),
            version: version.ok().map(first_line),
            message: Some(redact_error(&message)),
            instructions: vec!["Run `ngrok config add-authtoken …` in Terminal.".into()],
            tunnels: Vec::new(),
        },
    }
}

fn preflight_cloudflare() -> ProviderReadiness {
    let Some(executable) = find_executable("cloudflared") else {
        return ProviderReadiness {
            message: Some("cloudflared is not installed.".into()),
            instructions: vec![
                "Install cloudflared using Cloudflare's official package or `brew install cloudflared`."
                    .into(),
                "Then run `cloudflared tunnel login`.".into(),
            ],
            ..Default::default()
        };
    };
    let version = command_text(&executable, &["--version"])
        .ok()
        .map(first_line);
    match Command::new(&executable)
        .args(["tunnel", "list", "--output", "json"])
        .output()
    {
        Ok(output) if output.status.success() => {
            let tunnels = parse_cloudflare_tunnels(&output.stdout);
            ProviderReadiness {
                installed: true,
                authenticated: true,
                executable: Some(executable.to_string_lossy().into_owned()),
                version,
                message: Some(
                    "Cloudflare login is ready. Lemma can create and route its own named tunnel."
                        .into(),
                ),
                instructions: Vec::new(),
                tunnels,
            }
        }
        Ok(output) => ProviderReadiness {
            installed: true,
            authenticated: false,
            executable: Some(executable.to_string_lossy().into_owned()),
            version,
            message: Some(redact_error(
                &String::from_utf8_lossy(&output.stderr).to_string(),
            )),
            instructions: vec!["Run `cloudflared tunnel login` in Terminal.".into()],
            tunnels: Vec::new(),
        },
        Err(error) => ProviderReadiness {
            installed: true,
            authenticated: false,
            executable: Some(executable.to_string_lossy().into_owned()),
            version,
            message: Some(error.to_string()),
            instructions: vec!["Run `cloudflared tunnel login` in Terminal.".into()],
            tunnels: Vec::new(),
        },
    }
}

fn parse_cloudflare_tunnels(raw: &[u8]) -> Vec<CloudflareTunnel> {
    serde_json::from_slice::<Vec<Value>>(raw)
        .unwrap_or_default()
        .into_iter()
        .filter_map(|value| {
            Some(CloudflareTunnel {
                id: value.get("id")?.as_str()?.to_owned(),
                name: value.get("name")?.as_str()?.to_owned(),
            })
        })
        .collect()
}

fn parse_created_cloudflare_tunnel(raw: &[u8]) -> Option<CloudflareTunnel> {
    fn from_value(value: &Value) -> Option<CloudflareTunnel> {
        if let (Some(id), Some(name)) = (
            value.get("id").and_then(Value::as_str),
            value.get("name").and_then(Value::as_str),
        ) {
            return Some(CloudflareTunnel {
                id: id.to_owned(),
                name: name.to_owned(),
            });
        }
        if let Some(result) = value.get("result") {
            if let Some(found) = from_value(result) {
                return Some(found);
            }
        }
        value.as_array()?.iter().find_map(from_value)
    }

    serde_json::from_slice::<Value>(raw)
        .ok()
        .as_ref()
        .and_then(from_value)
}

fn managed_cloudflare_tunnel_name(root: &Path) -> io::Result<String> {
    let installation_id = installation_identity(root)?;
    Ok(format!("lemma-desktop-{}", &installation_id[..12]))
}

fn ngrok_config_path(executable: &Path) -> io::Result<PathBuf> {
    let output = command_text(executable, &["config", "check"]).map_err(io::Error::other)?;
    let marker = " at ";
    let path = output
        .lines()
        .find_map(|line| line.rsplit_once(marker).map(|(_, path)| path.trim()))
        .filter(|path| !path.is_empty())
        .ok_or_else(|| io::Error::other("ngrok did not report its configuration path"))?;
    Ok(PathBuf::from(path))
}

fn wait_for_ngrok_url(
    agent_base: &str,
    child: &mut Child,
    timeout: Duration,
) -> io::Result<String> {
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(2))
        .no_proxy()
        .build()
        .map_err(io::Error::other)?;
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if let Some(status) = child.try_wait()? {
            return Err(io::Error::other(format!(
                "ngrok exited during startup with {status}"
            )));
        }
        for path in ["/api/endpoints", "/api/tunnels"] {
            if let Ok(response) = client.get(format!("{agent_base}{path}")).send() {
                if let Ok(value) = response.json::<Value>() {
                    if let Some(url) = find_public_https_url(&value) {
                        return Ok(url);
                    }
                }
            }
        }
        thread::sleep(Duration::from_millis(250));
    }
    Err(io::Error::new(
        io::ErrorKind::TimedOut,
        "ngrok started but its dedicated local Agent API did not report an HTTPS endpoint",
    ))
}

fn find_public_https_url(value: &Value) -> Option<String> {
    match value {
        Value::String(value)
            if value.starts_with("https://")
                && !value.contains("127.0.0.1")
                && !value.contains("localhost") =>
        {
            Some(value.clone())
        }
        Value::Array(values) => values.iter().find_map(find_public_https_url),
        Value::Object(values) => {
            for key in ["url", "public_url", "uri"] {
                if let Some(found) = values.get(key).and_then(find_public_https_url) {
                    return Some(found);
                }
            }
            values.values().find_map(find_public_https_url)
        }
        _ => None,
    }
}

fn cloudflare_credentials_path(tunnel_id: &str) -> io::Result<PathBuf> {
    let mut roots = Vec::new();
    if let Some(home) = home_dir() {
        roots.push(home.join(".cloudflared"));
        roots.push(home.join("Library/Application Support/cloudflared"));
    }
    for root in roots {
        let candidate = root.join(format!("{tunnel_id}.json"));
        if candidate.is_file() {
            return Ok(candidate);
        }
    }
    Err(io::Error::new(
        io::ErrorKind::NotFound,
        "the selected tunnel credentials file is not available locally",
    ))
}

fn normalize_hostname(value: &str) -> io::Result<String> {
    let value = value.trim().trim_end_matches('.');
    if value.is_empty()
        || value.contains('/')
        || value.contains(':')
        || value.chars().any(char::is_whitespace)
        || !value.contains('.')
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "enter a public hostname such as lemma.example.com",
        ));
    }
    Ok(value.to_ascii_lowercase())
}

fn find_executable(name: &str) -> Option<PathBuf> {
    let mut candidates = Vec::new();
    if let Some(path) = std::env::var_os("PATH") {
        for root in std::env::split_paths(&path) {
            candidates.push(root.join(executable_name(name)));
        }
    }
    #[cfg(target_os = "macos")]
    {
        candidates.push(PathBuf::from("/opt/homebrew/bin").join(name));
        candidates.push(PathBuf::from("/usr/local/bin").join(name));
    }
    candidates.into_iter().find(|candidate| candidate.is_file())
}

fn record_owned_tunnel(
    root: &Path,
    marker_path: &Path,
    provider: TunnelProvider,
    executable: &Path,
    child: &Child,
) -> io::Result<()> {
    let expected = executable.canonicalize()?;
    let identity = process_identity(child.id())?;
    if Path::new(&identity.executable).canonicalize()? != expected {
        return Err(io::Error::other(
            "the started tunnel executable identity did not match the selected CLI",
        ));
    }
    persist_private_json(
        marker_path,
        &TunnelProcessMarker {
            schema_version: PROCESS_MARKER_SCHEMA_VERSION,
            installation_id: installation_identity(root)?,
            provider,
            pid: child.id(),
            executable: identity.executable,
            start_identity: identity.start_identity,
        },
    )
}

fn reclaim_owned_tunnel(root: &Path) -> io::Result<()> {
    let marker_path = root.join("sharing-process.json");
    let raw = match fs::read(&marker_path) {
        Ok(raw) if raw.len() <= 64 * 1024 => raw,
        Ok(_) => return Ok(()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(()),
        Err(error) => return Err(error),
    };
    let Ok(marker) = serde_json::from_slice::<TunnelProcessMarker>(&raw) else {
        return Ok(());
    };
    if marker.schema_version != PROCESS_MARKER_SCHEMA_VERSION {
        return Ok(());
    }
    let current_installation = installation_identity(root)?;
    let identity = match process_identity(marker.pid) {
        Ok(identity) => identity,
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            let _ = fs::remove_file(&marker_path);
            return Ok(());
        }
        Err(error) => return Err(error),
    };
    if marker.installation_id != current_installation {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "a sharing process marker belongs to another Lemma installation",
        ));
    }
    let expected_name = provider_name(marker.provider);
    let expected = find_executable(expected_name).and_then(|path| path.canonicalize().ok());
    let exact = expected.is_some_and(|expected| {
        Path::new(&identity.executable)
            .canonicalize()
            .is_ok_and(|actual| actual == expected)
    }) && identity.executable == marker.executable
        && identity.start_identity == marker.start_identity;
    if exact {
        terminate_verified_process(marker.pid)?;
    }
    let _ = fs::remove_file(&marker_path);
    Ok(())
}

fn executable_name(name: &str) -> String {
    #[cfg(windows)]
    {
        format!("{name}.exe")
    }
    #[cfg(not(windows))]
    {
        name.to_owned()
    }
}

fn command_text(executable: &Path, args: &[&str]) -> Result<String, String> {
    let output = Command::new(executable)
        .args(args)
        .output()
        .map_err(|error| error.to_string())?;
    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    let combined = format!("{}\n{}", stdout.trim(), stderr.trim())
        .trim()
        .to_owned();
    if output.status.success() {
        Ok(combined)
    } else {
        Err(combined)
    }
}

fn checked_command_output(command: &mut Command, context: &str) -> io::Result<Vec<u8>> {
    let output = command
        .output()
        .map_err(|error| io::Error::other(format!("{context}: {error}")))?;
    const MAX_OUTPUT: usize = 1024 * 1024;
    if output.stdout.len() > MAX_OUTPUT || output.stderr.len() > MAX_OUTPUT {
        return Err(io::Error::other(format!(
            "{context}: cloudflared returned unexpectedly large output"
        )));
    }
    if output.status.success() {
        return Ok(output.stdout);
    }
    let detail = format!(
        "{} {}",
        String::from_utf8_lossy(&output.stderr),
        String::from_utf8_lossy(&output.stdout)
    );
    Err(io::Error::other(format!(
        "{context}: {}",
        redact_error(detail.trim())
    )))
}

fn ensure_private_directory(path: &Path) -> io::Result<()> {
    fs::create_dir_all(path)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o700))?;
    }
    Ok(())
}

fn make_private_file(path: &Path) -> io::Result<()> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o600))?;
    }
    Ok(())
}

fn bounded_log(path: &Path) -> io::Result<File> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let truncate = fs::metadata(path)
        .map(|metadata| metadata.len() > 2 * 1024 * 1024)
        .unwrap_or(false);
    let mut options = OpenOptions::new();
    options.create(true).write(true);
    if truncate {
        options.truncate(true);
    } else {
        options.append(true);
    }
    options.open(path)
}

fn reserve_loopback_port() -> io::Result<u16> {
    TcpListener::bind((Ipv4Addr::LOCALHOST, 0))?
        .local_addr()
        .map(|address| address.port())
}

fn render_qr(value: &str) -> Option<String> {
    QrCode::new(value.as_bytes()).ok().map(|code| {
        code.render::<svg::Color>()
            .min_dimensions(176, 176)
            .dark_color(svg::Color("#1f1d18"))
            .light_color(svg::Color("#ffffff"))
            .build()
    })
}

fn persist_private_json<T: Serialize>(path: &Path, value: &T) -> io::Result<()> {
    let contents = serde_json::to_vec_pretty(value).map_err(io::Error::other)?;
    write_private(path, &contents)
}

fn write_private(path: &Path, contents: &[u8]) -> io::Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let temporary = path.with_extension(format!("{}.next", std::process::id()));
    let mut options = OpenOptions::new();
    options.write(true).create(true).truncate(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    {
        use std::io::Write;
        let mut file = options.open(&temporary)?;
        file.write_all(contents)?;
        file.sync_all()?;
    }
    fs::rename(temporary, path)
}

fn redact_error(value: &str) -> String {
    value
        .split_whitespace()
        .map(|part| {
            if part.to_ascii_lowercase().contains("token") && part.len() > 18 {
                "[redacted]"
            } else {
                part
            }
        })
        .collect::<Vec<_>>()
        .join(" ")
}

fn first_line(value: String) -> String {
    value.lines().next().unwrap_or_default().trim().to_owned()
}

fn provider_name(provider: TunnelProvider) -> &'static str {
    match provider {
        TunnelProvider::Ngrok => "ngrok",
        TunnelProvider::Cloudflare => "cloudflared",
    }
}

fn display_ip(ip: IpAddr) -> String {
    match ip {
        IpAddr::V4(ip) => ip.to_string(),
        IpAddr::V6(ip) => format!("[{ip}]"),
    }
}

fn now_ms() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
}

fn home_dir() -> Option<PathBuf> {
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
}

#[cfg(unix)]
fn prepare_owned_command(command: &mut Command) {
    use std::os::unix::process::CommandExt;
    command.process_group(0);
}

#[cfg(windows)]
fn prepare_owned_command(_command: &mut Command) {}

#[cfg(unix)]
fn terminate_owned_child(child: &mut Child) {
    let pid = child.id() as i32;
    unsafe {
        libc::kill(-pid, libc::SIGTERM);
    }
    let deadline = Instant::now() + Duration::from_secs(3);
    while Instant::now() < deadline {
        if child.try_wait().ok().flatten().is_some() {
            return;
        }
        thread::sleep(Duration::from_millis(50));
    }
    unsafe {
        libc::kill(-pid, libc::SIGKILL);
    }
    let _ = child.wait();
}

#[cfg(windows)]
fn terminate_owned_child(child: &mut Child) {
    let _ = child.kill();
    let _ = child.wait();
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};
    use std::sync::mpsc;

    fn read_http_head(stream: &mut std::net::TcpStream) -> Vec<u8> {
        let mut received = Vec::new();
        let mut byte = [0_u8; 1];
        while !received.ends_with(b"\r\n\r\n") {
            stream.read_exact(&mut byte).unwrap();
            received.push(byte[0]);
            assert!(received.len() < 64 * 1024, "HTTP head exceeded test bound");
        }
        received
    }

    #[test]
    fn public_activation_requires_fresh_confirmation() {
        let root = tempfile::tempdir().unwrap();
        let controller = SharingController::load(
            root.path(),
            "http://app.lemma.localhost:3711".into(),
            3711,
            8711,
        )
        .unwrap();
        let error = controller
            .prepare_enable(&EnableSharingRequest {
                mode: SharingMode::Public,
                provider: Some(TunnelProvider::Ngrok),
                ..Default::default()
            })
            .unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::PermissionDenied);
        assert!(!controller.transition_running.load(Ordering::Acquire));
    }

    #[test]
    fn forwarding_headers_are_replaced_not_appended() {
        let mut headers = hyper::HeaderMap::new();
        headers.insert("forwarded", HeaderValue::from_static("for=attacker"));
        headers.insert("x-forwarded-for", HeaderValue::from_static("attacker"));
        headers.insert("x-forwarded-custom", HeaderValue::from_static("attacker"));
        strip_forwarding_headers(&mut headers);
        assert!(headers.get("forwarded").is_none());
        assert!(headers.get("x-forwarded-for").is_none());
        assert!(headers.get("x-forwarded-custom").is_none());
    }

    #[test]
    fn hostname_validation_rejects_urls_and_ports() {
        assert_eq!(
            normalize_hostname("Lemma.Example.com.").unwrap(),
            "lemma.example.com"
        );
        assert!(normalize_hostname("https://lemma.example.com").is_err());
        assert!(normalize_hostname("lemma.example.com:443").is_err());
    }

    #[test]
    fn only_public_https_urls_are_selected_from_agent_output() {
        let value = json!({
            "endpoints": [
                {"url": "http://127.0.0.1:4040"},
                {"public_url": "https://example.ngrok.app"}
            ]
        });
        assert_eq!(
            find_public_https_url(&value).as_deref(),
            Some("https://example.ngrok.app")
        );
    }

    #[test]
    fn preferences_never_persist_an_active_mode() {
        let preferences = SharingPreferences {
            schema_version: SHARING_SCHEMA_VERSION,
            selected_interface: Some("en0".into()),
            last_provider: Some(TunnelProvider::Ngrok),
            cloudflare_setup: CloudflareSetup::Automatic,
            cloudflare_tunnel_owned: true,
            ..Default::default()
        };
        let value = serde_json::to_value(preferences).unwrap();
        assert!(value.get("mode").is_none());
        assert!(value.get("desired_state").is_none());
        assert!(value.get("credentials").is_none());
        assert!(value.get("origin_certificate").is_none());
    }

    #[test]
    fn sharing_transitions_are_single_flight() {
        let root = tempfile::tempdir().unwrap();
        let controller = SharingController::load(
            root.path(),
            "http://app.lemma.localhost:3711".into(),
            3711,
            8711,
        )
        .unwrap();
        controller.begin_transition().unwrap();
        assert_eq!(
            controller.begin_transition().unwrap_err().kind(),
            io::ErrorKind::WouldBlock
        );
        controller.fail_transition("test complete".into());
        controller.begin_transition().unwrap();
        controller.fail_transition("test complete".into());
    }

    #[test]
    fn cloudflare_output_parser_keeps_only_named_tunnel_identity() {
        let tunnels = parse_cloudflare_tunnels(
            br#"[{"id":"8f1","name":"lemma","connections":[]},{"name":"missing-id"}]"#,
        );
        assert_eq!(tunnels.len(), 1);
        assert_eq!(tunnels[0].id, "8f1");
        assert_eq!(tunnels[0].name, "lemma");
    }

    #[test]
    fn cloudflare_create_parser_accepts_direct_and_wrapped_json() {
        assert_eq!(
            parse_created_cloudflare_tunnel(
                br#"{"id":"8f1","name":"lemma-desktop-abcd","credentials_file":"/secret"}"#
            )
            .unwrap()
            .id,
            "8f1"
        );
        assert_eq!(
            parse_created_cloudflare_tunnel(
                br#"{"result":{"id":"8f2","name":"lemma-desktop-efgh"}}"#
            )
            .unwrap()
            .name,
            "lemma-desktop-efgh"
        );
    }

    #[test]
    fn managed_cloudflare_name_is_stable_and_installation_scoped() {
        let root = tempfile::tempdir().unwrap();
        let first = managed_cloudflare_tunnel_name(root.path()).unwrap();
        let second = managed_cloudflare_tunnel_name(root.path()).unwrap();
        assert_eq!(first, second);
        assert!(first.starts_with("lemma-desktop-"));
        assert_eq!(first.len(), "lemma-desktop-".len() + 12);
    }

    #[test]
    fn sharing_preferences_migrate_existing_tunnels_to_advanced_mode() {
        let root = tempfile::tempdir().unwrap();
        write_private(
            &root.path().join("sharing.json"),
            br#"{
              "schema_version": 1,
              "last_provider": "cloudflare",
              "cloudflare_tunnel_id": "existing-id",
              "cloudflare_tunnel_name": "existing-name",
              "cloudflare_hostname": "lemma.example.com"
            }"#,
        )
        .unwrap();
        let controller = SharingController::load(
            root.path(),
            "http://app.lemma.localhost:3711".into(),
            3711,
            8711,
        )
        .unwrap();
        let preferences = controller.snapshot(false).preferences;
        assert_eq!(preferences.schema_version, SHARING_SCHEMA_VERSION);
        assert_eq!(preferences.cloudflare_setup, CloudflareSetup::Existing);
        assert!(!preferences.cloudflare_tunnel_owned);
        assert!(!preferences.cloudflare_dns_routed);
    }

    #[cfg(unix)]
    #[test]
    fn automatic_cloudflare_setup_creates_routes_and_then_reuses_one_owned_tunnel() {
        use std::os::unix::fs::PermissionsExt;

        let root = tempfile::tempdir().unwrap();
        let controller = SharingController::load(
            root.path(),
            "http://app.lemma.localhost:3711".into(),
            3711,
            8711,
        )
        .unwrap();
        let name = managed_cloudflare_tunnel_name(root.path()).unwrap();
        let executable = root.path().join("cloudflared-test");
        let calls = root.path().join("cloudflared-calls.log");
        fs::write(
            &executable,
            format!(
                "#!/bin/sh\n\
                 if [ \"$3\" = \"create\" ]; then\n\
                   printf '{{}}' > \"$7\"\n\
                   printf '{{\"id\":\"managed-id\",\"name\":\"{name}\"}}'\n\
                   printf 'create\\n' >> '{}'\n\
                   exit 0\n\
                 fi\n\
                 if [ \"$3\" = \"route\" ] && [ \"$4\" = \"dns\" ]; then\n\
                   printf 'route:%s:%s\\n' \"$5\" \"$6\" >> '{}'\n\
                   exit 0\n\
                 fi\n\
                 exit 2\n",
                calls.display(),
                calls.display()
            ),
        )
        .unwrap();
        fs::set_permissions(&executable, fs::Permissions::from_mode(0o700)).unwrap();
        let readiness = ProviderReadiness {
            installed: true,
            authenticated: true,
            executable: Some(executable.to_string_lossy().into_owned()),
            ..Default::default()
        };

        let first = controller
            .ensure_managed_cloudflare_tunnel(&executable, &readiness, "lemma.example.com")
            .unwrap();
        assert_eq!(first.id, "managed-id");
        assert!(first.credentials.is_file());
        assert_eq!(
            fs::metadata(&first.credentials)
                .unwrap()
                .permissions()
                .mode()
                & 0o777,
            0o600
        );
        let preferences = controller.snapshot(false).preferences;
        assert_eq!(preferences.cloudflare_setup, CloudflareSetup::Automatic);
        assert!(preferences.cloudflare_tunnel_owned);
        assert!(preferences.cloudflare_dns_routed);
        assert_eq!(
            fs::read_to_string(&calls).unwrap(),
            "create\nroute:managed-id:lemma.example.com\n"
        );

        let reusable = ProviderReadiness {
            installed: true,
            authenticated: true,
            executable: Some(executable.to_string_lossy().into_owned()),
            tunnels: vec![CloudflareTunnel {
                id: "managed-id".into(),
                name,
            }],
            ..Default::default()
        };
        controller
            .ensure_managed_cloudflare_tunnel(&executable, &reusable, "lemma.example.com")
            .unwrap();
        assert_eq!(
            fs::read_to_string(&calls).unwrap(),
            "create\nroute:managed-id:lemma.example.com\n"
        );
    }

    #[test]
    fn error_redaction_does_not_echo_token_shaped_words() {
        let redacted = redact_error("authtoken=abcdefghijklmnopqrstuvwxyz rejected");
        assert!(!redacted.contains("abcdefghijklmnopqrstuvwxyz"));
        assert!(redacted.contains("[redacted]"));
    }

    #[test]
    fn gateway_routes_only_the_reserved_api_prefix_to_backend() {
        assert_eq!(
            proxy_target("/_lemma/api/v1/files?limit=2", 3711, 8711),
            (8711, "/v1/files?limit=2".into())
        );
        assert_eq!(proxy_target("/_lemma/api", 3711, 8711), (8711, "/".into()));
        assert_eq!(
            proxy_target("/pod/demo", 3711, 8711),
            (3711, "/pod/demo".into())
        );
    }

    #[test]
    fn gateway_streams_sse_before_the_response_finishes_and_replaces_forwarding_headers() {
        let upstream = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        let upstream_port = upstream.local_addr().unwrap().port();
        let (first_sent, first_received) = mpsc::channel();
        let (release_send, release_receive) = mpsc::channel();
        let server = thread::spawn(move || {
            let (mut stream, _) = upstream.accept().unwrap();
            let head = String::from_utf8(read_http_head(&mut stream)).unwrap();
            assert!(head.starts_with("GET /events?conversation=1 HTTP/1.1\r\n"));
            assert!(head
                .to_ascii_lowercase()
                .contains("\r\nhost: shared.example\r\n"));
            assert!(head
                .to_ascii_lowercase()
                .contains("\r\nx-forwarded-for: 127.0.0.1\r\n"));
            assert!(!head.contains("for=attacker"));
            stream
                .write_all(
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\nB\r\ndata: one\n\n\r\n",
                )
                .unwrap();
            stream.flush().unwrap();
            first_sent.send(()).unwrap();
            release_receive
                .recv_timeout(Duration::from_secs(2))
                .unwrap();
            stream
                .write_all(b"B\r\ndata: two\n\n\r\n0\r\n\r\n")
                .unwrap();
            stream.flush().unwrap();
        });
        let mut gateway = GatewayHandle::start(
            IpAddr::V4(Ipv4Addr::LOCALHOST),
            9,
            upstream_port,
            SharingMode::Public,
        )
        .unwrap();
        let mut client = std::net::TcpStream::connect(gateway.address).unwrap();
        client
            .set_read_timeout(Some(Duration::from_secs(2)))
            .unwrap();
        client
            .write_all(
                b"GET /_lemma/api/events?conversation=1 HTTP/1.1\r\nHost: shared.example\r\nX-Forwarded-For: attacker\r\nForwarded: for=attacker\r\nConnection: close\r\n\r\n",
            )
            .unwrap();
        first_received.recv_timeout(Duration::from_secs(2)).unwrap();
        let mut observed = Vec::new();
        let mut buffer = [0_u8; 512];
        while !String::from_utf8_lossy(&observed).contains("data: one") {
            let read = client.read(&mut buffer).unwrap();
            assert!(read > 0, "gateway closed before the first SSE event");
            observed.extend_from_slice(&buffer[..read]);
        }
        release_send.send(()).unwrap();
        while !String::from_utf8_lossy(&observed).contains("data: two") {
            let read = client.read(&mut buffer).unwrap();
            assert!(read > 0, "gateway closed before the second SSE event");
            observed.extend_from_slice(&buffer[..read]);
        }
        gateway.stop();
        server.join().unwrap();
    }

    #[test]
    fn gateway_preserves_large_uploads_and_downloads() {
        let upstream = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        let upstream_port = upstream.local_addr().unwrap().port();
        let payload = vec![b'L'; 2 * 1024 * 1024];
        let expected = payload.clone();
        let server = thread::spawn(move || {
            let (mut stream, _) = upstream.accept().unwrap();
            let head = String::from_utf8(read_http_head(&mut stream)).unwrap();
            assert!(head.starts_with("POST /files/large?roundtrip=1 HTTP/1.1\r\n"));
            let content_length = head
                .lines()
                .find_map(|line| {
                    line.to_ascii_lowercase()
                        .strip_prefix("content-length:")
                        .map(str::trim)
                        .and_then(|value| value.parse::<usize>().ok())
                })
                .unwrap();
            let mut body = vec![0_u8; content_length];
            stream.read_exact(&mut body).unwrap();
            assert_eq!(body, expected);
            write!(
                stream,
                "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                body.len()
            )
            .unwrap();
            stream.write_all(&body).unwrap();
            stream.flush().unwrap();
        });
        let mut gateway = GatewayHandle::start(
            IpAddr::V4(Ipv4Addr::LOCALHOST),
            9,
            upstream_port,
            SharingMode::LocalNetwork,
        )
        .unwrap();
        let response = reqwest::blocking::Client::builder()
            .no_proxy()
            .build()
            .unwrap()
            .post(format!(
                "http://{}/_lemma/api/files/large?roundtrip=1",
                gateway.address
            ))
            .body(payload.clone())
            .send()
            .unwrap();
        assert_eq!(response.status(), reqwest::StatusCode::OK);
        assert_eq!(response.bytes().unwrap().as_ref(), payload.as_slice());
        gateway.stop();
        server.join().unwrap();
    }

    #[test]
    fn gateway_relays_websocket_upgrades_bidirectionally() {
        let upstream = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        let upstream_port = upstream.local_addr().unwrap().port();
        let server = thread::spawn(move || {
            let (mut stream, _) = upstream.accept().unwrap();
            let head = String::from_utf8(read_http_head(&mut stream)).unwrap();
            assert!(head.starts_with("GET /socket HTTP/1.1\r\n"));
            assert!(head
                .to_ascii_lowercase()
                .contains("\r\nupgrade: websocket\r\n"));
            stream
                .write_all(
                    b"HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\nUpgrade: websocket\r\nSec-WebSocket-Accept: test\r\n\r\n",
                )
                .unwrap();
            stream.flush().unwrap();
            let mut bytes = [0_u8; 5];
            stream.read_exact(&mut bytes).unwrap();
            stream.write_all(&bytes).unwrap();
            stream.flush().unwrap();
        });
        let mut gateway = GatewayHandle::start(
            IpAddr::V4(Ipv4Addr::LOCALHOST),
            upstream_port,
            9,
            SharingMode::Public,
        )
        .unwrap();
        let mut client = std::net::TcpStream::connect(gateway.address).unwrap();
        client
            .set_read_timeout(Some(Duration::from_secs(2)))
            .unwrap();
        client
            .write_all(
                b"GET /socket HTTP/1.1\r\nHost: shared.example\r\nConnection: Upgrade\r\nUpgrade: websocket\r\nSec-WebSocket-Key: dGVzdA==\r\nSec-WebSocket-Version: 13\r\n\r\n",
            )
            .unwrap();
        let head = String::from_utf8(read_http_head(&mut client)).unwrap();
        assert!(head.starts_with("HTTP/1.1 101"));
        client.write_all(b"hello").unwrap();
        let mut echoed = [0_u8; 5];
        client.read_exact(&mut echoed).unwrap();
        assert_eq!(&echoed, b"hello");
        gateway.stop();
        server.join().unwrap();
    }
}
