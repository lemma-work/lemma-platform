//! Same-site aliases for pod apps framed by the workspace on macOS.
//!
//! # Why
//!
//! This installation serves under `lemma.localhost` (see
//! `crate::local_domain`). WebKit derives no site wider than the *host* from a
//! `*.localhost` name, so a pod app on `<slug>.apps.lemma.localhost` framed by
//! the workspace on `app.lemma.localhost` is a third-party frame, and WebKit
//! gives a third-party frame no cookies: the app loads signed out. The same
//! host on a different port *is* same-site (a site ignores the port), so the
//! macOS shell frames the app through `http://app.lemma.localhost:<alias
//! port>/...` instead. Chromium and WebView2 treat `*.lemma.localhost` as one
//! site and need none of this; the APIs keep returning the canonical app URL
//! and only the macOS frame is rewritten.
//!
//! # What an alias port is
//!
//! A loopback listener, owned by locald, that forwards every request to the
//! backend's app ingress with `Host` rewritten to the app's canonical
//! `<slug>.apps.lemma.localhost:<backend port>` -- so the backend's host
//! routing, and everything it serves, is unchanged. The app's SDK calls the
//! API through its own origin (`/_lemma`, a relative `apiUrl`), so on the
//! alias those calls go to the alias too and reach the backend the same way.
//! The `Domain=lemma.localhost` session cookie covers `app.lemma.localhost`
//! whatever the port, and the frame is same-site, so it is sent.
//!
//! # The registry
//!
//! One port per app host, persisted in `app-aliases.json` beside
//! `network.json` so an app keeps its alias -- and so its origin, and so its
//! `localStorage` -- across restarts. Not *in* `network.json`: that record
//! denies unknown fields, and an older locald reading one it cannot parse
//! reallocates the workspace's ports, which unpairs the Agent Host.
//!
//! Ports are the OS's, taken when first needed and asked for again by number on
//! the next start; a port somebody else took in between is replaced with a new
//! one (the shell asks for the alias every time it frames an app, so it always
//! frames the current one). A fixed range would collide with whatever else on
//! the machine happens to use it, which is worse than moving.
//!
//! Bounded: at most [`ALIAS_CAPACITY`] apps hold an alias. Asking for one more
//! evicts the least recently used, whose listener closes and whose port is
//! forgotten; that app gets a fresh alias (and a fresh origin, so empty
//! `localStorage`) the next time it is framed.
//!
//! # What an alias is not
//!
//! Never a way into the shell. The workspace capability is pinned to the
//! workspace's exact origin at runtime (see `desktop/src/workspace.rs`), and
//! every privileged command compares the caller's full origin, port included,
//! with the workspace's. An alias origin is a different origin and passes
//! neither. It is loopback only and serves nothing a canonical app host does
//! not already serve to anybody on this machine.

use std::collections::HashMap;
use std::fs;
use std::io;
use std::net::{Ipv4Addr, SocketAddr, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use hyper::client::HttpConnector;
use hyper::service::{make_service_fn, service_fn};
use hyper::{Body, Client, Server};
use serde::{Deserialize, Serialize};
use tokio::sync::oneshot;

use lemma_private_file::write_atomic as write_private_atomic;

use crate::local_domain::LocalDomain;
use crate::port_reservation::PortReservation;

mod proxy;
#[cfg(test)]
mod tests;

pub(crate) use proxy::{forward, AliasTarget};

/// How many apps can hold an alias at once.
///
/// Sixteen open app tabs is far past what anybody keeps next to an agent; the
/// bound exists so a long-lived install does not accumulate a listener for
/// every app it ever framed.
pub const ALIAS_CAPACITY: usize = 16;

const SCHEMA_VERSION: u64 = 1;
const FILE_NAME: &str = "app-aliases.json";

/// One app host's alias port.
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct AliasEntry {
    /// The canonical app host, without a port: `orders.apps.lemma.localhost`.
    pub host: String,
    pub port: u16,
    pub last_used_ms: u128,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct AliasFile {
    schema_version: u64,
    aliases: Vec<AliasEntry>,
}

/// The persisted host -> port map, and the eviction rule. No sockets.
#[derive(Debug)]
pub struct AliasRegistry {
    path: PathBuf,
    entries: Vec<AliasEntry>,
    capacity: usize,
}

impl AliasRegistry {
    /// The registry at `root/app-aliases.json`. A missing or unreadable file is
    /// an empty registry: every alias is re-derivable, and losing them costs
    /// only a fresh origin per app.
    #[must_use]
    pub fn load(root: &Path, capacity: usize) -> Self {
        let path = root.join(FILE_NAME);
        let mut entries = fs::read(&path)
            .ok()
            .and_then(|raw| serde_json::from_slice::<AliasFile>(&raw).ok())
            .filter(|file| file.schema_version == SCHEMA_VERSION)
            .map(|file| file.aliases)
            .unwrap_or_default();
        // A hand-edited or corrupted file must not bind two apps to one port,
        // or more listeners than the bound allows.
        let mut seen_hosts = Vec::new();
        let mut seen_ports = Vec::new();
        entries.retain(|entry| {
            let fresh = !seen_hosts.contains(&entry.host) && !seen_ports.contains(&entry.port);
            seen_hosts.push(entry.host.clone());
            seen_ports.push(entry.port);
            fresh && entry.port >= 1024
        });
        entries.sort_by_key(|entry| std::cmp::Reverse(entry.last_used_ms));
        entries.truncate(capacity);
        Self {
            path,
            entries,
            capacity,
        }
    }

    #[must_use]
    pub fn entries(&self) -> &[AliasEntry] {
        &self.entries
    }

    #[must_use]
    pub fn port_for(&self, host: &str) -> Option<u16> {
        self.entries
            .iter()
            .find(|entry| entry.host == host)
            .map(|entry| entry.port)
    }

    /// Record that `host` is served on `port` now, evicting the least recently
    /// used entry when that makes room. Returns what was evicted.
    pub fn assign(&mut self, host: &str, port: u16, now_ms: u128) -> Option<AliasEntry> {
        if let Some(entry) = self.entries.iter_mut().find(|entry| entry.host == host) {
            entry.port = port;
            entry.last_used_ms = now_ms;
            return None;
        }
        let evicted = if self.entries.len() >= self.capacity {
            let oldest = self
                .entries
                .iter()
                .enumerate()
                .min_by_key(|(_, entry)| entry.last_used_ms)
                .map(|(index, _)| index);
            oldest.map(|index| self.entries.remove(index))
        } else {
            None
        };
        self.entries.push(AliasEntry {
            host: host.to_owned(),
            port,
            last_used_ms: now_ms,
        });
        evicted
    }

    /// The oldest entry, which the next new host would evict when full.
    #[must_use]
    pub fn next_eviction(&self) -> Option<&AliasEntry> {
        if self.entries.len() < self.capacity {
            return None;
        }
        self.entries.iter().min_by_key(|entry| entry.last_used_ms)
    }

    /// Forget `host`, if it is here.
    pub fn remove(&mut self, host: &str) {
        self.entries.retain(|entry| entry.host != host);
    }

    pub fn save(&self) -> io::Result<()> {
        let file = AliasFile {
            schema_version: SCHEMA_VERSION,
            aliases: self.entries.clone(),
        };
        write_private_atomic(&self.path, &serde_json::to_vec_pretty(&file)?)
    }
}

/// A canonical app URL taken apart: which app host, and what to append.
#[derive(Debug, PartialEq, Eq)]
pub struct CanonicalApp {
    /// `orders.apps.lemma.localhost`.
    pub host: String,
    /// Path, query and fragment, as given: `/reports?q=1#top`.
    pub rest: String,
}

/// Parse `url` as one of this installation's app URLs on `backend_port`.
///
/// Refuses anything else, so an alias can only ever front the backend's app
/// ingress: plain `http`, a single-label app host under the apps domain, the
/// backend's own port, and no credentials.
pub fn parse_canonical_app_url(
    url: &str,
    domain: &LocalDomain,
    backend_port: u16,
) -> Result<CanonicalApp, String> {
    let parsed = reqwest::Url::parse(url).map_err(|_| "not a URL".to_owned())?;
    if parsed.scheme() != "http" {
        return Err("app URLs on this computer are plain http".into());
    }
    if !parsed.username().is_empty() || parsed.password().is_some() {
        return Err("an app URL carries no credentials".into());
    }
    let host = parsed.host_str().unwrap_or_default().to_ascii_lowercase();
    if domain.app_label(&host).is_none() {
        return Err(format!("{host} is not an app on this computer"));
    }
    if parsed.port() != Some(backend_port) {
        return Err("that app URL is not on this installation's port".into());
    }
    let mut rest = parsed.path().to_owned();
    if let Some(query) = parsed.query() {
        rest.push('?');
        rest.push_str(query);
    }
    if let Some(fragment) = parsed.fragment() {
        rest.push('#');
        rest.push_str(fragment);
    }
    Ok(CanonicalApp { host, rest })
}

struct Running {
    shutdown: oneshot::Sender<()>,
}

struct Inner {
    registry: AliasRegistry,
    listeners: HashMap<u16, Running>,
}

/// The alias listeners, and the registry that names them.
pub struct AppAliasService {
    domain: LocalDomain,
    frontend_port: u16,
    backend_port: u16,
    inner: Mutex<Inner>,
    runtime: tokio::runtime::Runtime,
    client: Client<HttpConnector, Body>,
    log: Arc<dyn Fn(&str) + Send + Sync>,
}

impl AppAliasService {
    pub fn new(
        root: &Path,
        frontend_port: u16,
        backend_port: u16,
        log: Arc<dyn Fn(&str) + Send + Sync>,
    ) -> io::Result<Self> {
        let runtime = tokio::runtime::Builder::new_multi_thread()
            .worker_threads(2)
            .thread_name("lemma-app-alias")
            .enable_all()
            .build()?;
        Ok(Self {
            domain: LocalDomain::current(),
            frontend_port,
            backend_port,
            inner: Mutex::new(Inner {
                registry: AliasRegistry::load(root, ALIAS_CAPACITY),
                listeners: HashMap::new(),
            }),
            runtime,
            client: Client::new(),
            log,
        })
    }

    /// The alias URL for a canonical app URL, starting its listener if needed.
    pub fn alias_url(&self, canonical: &str) -> Result<String, String> {
        let app = parse_canonical_app_url(canonical, &self.domain, self.backend_port)?;
        let port = self
            .ensure(&app.host)
            .map_err(|error| format!("could not open an alias for {}: {error}", app.host))?;
        Ok(format!(
            "http://{}:{port}{}",
            self.domain.frontend_host(),
            app.rest
        ))
    }

    /// Bring back every remembered alias, best effort, so frames the workspace
    /// restored before asking again still load after a restart.
    pub fn restore(&self) {
        let hosts: Vec<String> = {
            let inner = self.inner.lock().expect("alias lock poisoned");
            inner
                .registry
                .entries()
                .iter()
                .map(|entry| entry.host.clone())
                .collect()
        };
        for host in hosts {
            if let Err(error) = self.ensure_inner(&host, false) {
                (self.log)(&format!("app alias for {host} not restored: {error}"));
            }
        }
    }

    /// The alias ports listening right now.
    #[must_use]
    pub fn listening_ports(&self) -> Vec<u16> {
        let inner = self.inner.lock().expect("alias lock poisoned");
        inner.listeners.keys().copied().collect()
    }

    fn ensure(&self, host: &str) -> io::Result<u16> {
        self.ensure_inner(host, true)
    }

    fn ensure_inner(&self, host: &str, touch: bool) -> io::Result<u16> {
        let mut inner = self.inner.lock().expect("alias lock poisoned");
        let remembered = inner.registry.port_for(host);
        let now = now_ms();
        let stamp = if touch {
            now
        } else {
            inner
                .registry
                .entries()
                .iter()
                .find(|entry| entry.host == host)
                .map_or(now, |entry| entry.last_used_ms)
        };

        if let Some(port) = remembered {
            if inner.listeners.contains_key(&port) {
                inner.registry.assign(host, port, stamp);
                inner.registry.save()?;
                return Ok(port);
            }
        }

        // Make room first, so the evicted listener's port is not held while a
        // new one is bound -- and so a full registry never runs one over.
        if remembered.is_none() {
            if let Some(oldest) = inner.registry.next_eviction().cloned() {
                if let Some(running) = inner.listeners.remove(&oldest.port) {
                    let _ = running.shutdown.send(());
                }
                inner.registry.remove(&oldest.host);
                (self.log)(&format!(
                    "app alias for {} evicted to make room (port {})",
                    oldest.host, oldest.port
                ));
            }
        }

        let listener = self.bind(remembered)?;
        let port = listener.local_addr()?.port();
        if remembered.is_some_and(|previous| previous != port) {
            (self.log)(&format!(
                "app alias for {host} moved to port {port}: its old port is taken"
            ));
        }
        let shutdown = self.serve(listener, host, port)?;
        inner.listeners.insert(port, Running { shutdown });
        inner.registry.assign(host, port, stamp);
        inner.registry.save()?;
        Ok(port)
    }

    /// A loopback listener on `wanted` if it can be had, else a fresh one.
    ///
    /// Never Lemma's own two ports, which a stale record could otherwise name.
    fn bind(&self, wanted: Option<u16>) -> io::Result<TcpListener> {
        if let Some(port) = wanted.filter(|port| !self.reserved(*port)) {
            if let Ok(reservation) = PortReservation::at_loopback_port(port) {
                return reservation.listen();
            }
            // Refused is not proof somebody else has it. Our own previous run
            // closed connections on this port, and those linger in TIME_WAIT
            // for half a minute, during which a plain bind fails -- so every
            // quick restart would move every alias. Only when nothing answers
            // a connect is the port reclaimed with SO_REUSEADDR; a listener
            // anywhere on it, wildcard included, answers and keeps it.
            // Not on Windows, where SO_REUSEADDR means "take it from whoever
            // holds it" rather than "ignore TIME_WAIT".
            if cfg!(not(windows)) && !answering(port) {
                if let Ok(listener) = bind_reclaiming(port) {
                    return Ok(listener);
                }
            }
        }
        for _ in 0..16 {
            let reservation = PortReservation::ephemeral()?;
            if self.reserved(reservation.port()) {
                continue;
            }
            return reservation.listen();
        }
        Err(io::Error::other("no free loopback port for an app alias"))
    }

    fn reserved(&self, port: u16) -> bool {
        port == self.frontend_port || port == self.backend_port
    }

    fn serve(
        &self,
        listener: TcpListener,
        host: &str,
        port: u16,
    ) -> io::Result<oneshot::Sender<()>> {
        listener.set_nonblocking(true)?;
        let target = Arc::new(AliasTarget {
            alias_host: self.domain.frontend_host(),
            alias_port: port,
            canonical_host: host.to_owned(),
            backend_port: self.backend_port,
        });
        let client = self.client.clone();
        let (shutdown, receive) = oneshot::channel::<()>();
        let log = Arc::clone(&self.log);
        self.runtime.spawn(async move {
            let make_service = make_service_fn(move |_connection| {
                let client = client.clone();
                let target = Arc::clone(&target);
                async move {
                    Ok::<_, hyper::Error>(service_fn(move |request| {
                        forward(request, client.clone(), Arc::clone(&target))
                    }))
                }
            });
            let server = match Server::from_tcp(listener) {
                Ok(server) => server.serve(make_service),
                Err(error) => {
                    log(&format!("app alias port {port} could not serve: {error}"));
                    return;
                }
            };
            let _ = server
                .with_graceful_shutdown(async {
                    let _ = receive.await;
                })
                .await;
        });
        Ok(shutdown)
    }
}

impl Drop for AppAliasService {
    fn drop(&mut self) {
        let listeners: Vec<Running> = match self.inner.get_mut() {
            Ok(inner) => inner
                .listeners
                .drain()
                .map(|(_, running)| running)
                .collect(),
            Err(_) => Vec::new(),
        };
        for running in listeners {
            let _ = running.shutdown.send(());
        }
    }
}

/// A loopback listener on `port`, allowed to take it over TIME_WAIT leftovers.
fn bind_reclaiming(port: u16) -> io::Result<TcpListener> {
    use socket2::{Domain, Protocol, Socket, Type};
    let address = SocketAddr::from((Ipv4Addr::LOCALHOST, port));
    let socket = Socket::new(Domain::IPV4, Type::STREAM, Some(Protocol::TCP))?;
    socket.set_reuse_address(true)?;
    socket.bind(&address.into())?;
    socket.listen(128)?;
    Ok(socket.into())
}

/// Whether anything answers on a loopback port.
#[must_use]
pub fn answering(port: u16) -> bool {
    TcpStream::connect_timeout(
        &SocketAddr::from((Ipv4Addr::LOCALHOST, port)),
        Duration::from_millis(250),
    )
    .is_ok()
}

fn now_ms() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
}
