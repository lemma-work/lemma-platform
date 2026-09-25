//! The local gateway a tunnel points at, and what it forwards.

use super::*;

/// The header locald's own activation probe carries while the gateway is held.
pub(crate) const ACTIVATION_PROBE_HEADER: &str = "x-lemma-activation-probe";

/// Whether the gateway serves visitors yet, and the one caller it serves before.
///
/// A gateway starts held. Sharing has to bring the tunnel up before it knows
/// the public origin, and only then restart the backend and frontend with the
/// environment for that origin -- so for the length of a restart the tunnel
/// was live in front of the *previous* stack: the local one, with `DEBUG` on,
/// no rate limit and no ALTCHA. Held, every visitor is answered 503 until the
/// hardened stack has been checked through this very gateway and the change
/// committed. The check itself gets through by presenting a random token only
/// locald knows, which is stripped before anything is forwarded.
pub(crate) struct GatewayHold {
    open: AtomicBool,
    probe_token: String,
}

impl GatewayHold {
    pub(crate) fn new(probe_token: String) -> Self {
        Self {
            open: AtomicBool::new(false),
            probe_token,
        }
    }

    pub(crate) fn set_open(&self, open: bool) {
        self.open.store(open, Ordering::Release);
    }

    pub(crate) fn is_open(&self) -> bool {
        self.open.load(Ordering::Acquire)
    }

    /// Whether this request may pass, and the probe header gone either way.
    pub(crate) fn admit(&self, headers: &mut hyper::HeaderMap) -> bool {
        let presented = headers
            .remove(ACTIVATION_PROBE_HEADER)
            .and_then(|value| value.to_str().ok().map(str::to_owned));
        if self.is_open() {
            return true;
        }
        presented.is_some_and(|value| {
            !self.probe_token.is_empty()
                && constant_time_eq(value.as_bytes(), self.probe_token.as_bytes())
        })
    }
}

fn constant_time_eq(left: &[u8], right: &[u8]) -> bool {
    left.len() == right.len()
        && left
            .iter()
            .zip(right)
            .fold(0_u8, |acc, (a, b)| acc | (a ^ b))
            == 0
}

/// Where a request really came from, for the backend's per-client limits.
///
/// On the LAN the TCP peer is the visitor. Through a tunnel it is not: the
/// tunnel process connects from this Mac's loopback, so every visitor on the
/// internet arrived as `127.0.0.1` and shared one sign-in rate limit -- one
/// person guessing passwords locked everybody out, and spreading guesses over
/// many addresses cost nothing. The tunnel says who its client was, and only
/// the tunnel may: the header is believed only from a loopback peer, in Public
/// mode, from the header the provider in use sets.
pub(crate) fn client_address(
    mode: SharingMode,
    provider: Option<TunnelProvider>,
    headers: &hyper::HeaderMap,
    remote: SocketAddr,
) -> IpAddr {
    if mode != SharingMode::Public || !remote.ip().is_loopback() {
        return remote.ip();
    }
    let header = |name: &str| {
        headers
            .get(name)
            .and_then(|value| value.to_str().ok())
            .map(str::to_owned)
    };
    let claimed = match provider {
        Some(TunnelProvider::Cloudflare) => header("cf-connecting-ip"),
        // ngrok appends the address it accepted the connection from, so the
        // last entry is ngrok's own and anything before it is the visitor's
        // to write.
        Some(TunnelProvider::Ngrok) => header("x-forwarded-for")
            .and_then(|value| value.rsplit(',').next().map(|last| last.trim().to_owned())),
        None => None,
    };
    claimed
        .and_then(|value| value.trim().parse::<IpAddr>().ok())
        .unwrap_or_else(|| remote.ip())
}

impl GatewayHandle {
    pub(crate) fn start(
        bind_ip: IpAddr,
        frontend_port: u16,
        backend_port: u16,
        mode: SharingMode,
        provider: Option<TunnelProvider>,
        probe_token: String,
    ) -> io::Result<Self> {
        let hold = Arc::new(GatewayHold::new(probe_token));
        let listener = TcpListener::bind(SocketAddr::new(bind_ip, 0))?;
        listener.set_nonblocking(true)?;
        let address = listener.local_addr()?;
        let (shutdown_send, shutdown_receive) = oneshot::channel::<()>();
        let thread = thread::Builder::new()
            .name("lemma-sharing-gateway".into())
            .spawn({
                let hold = Arc::clone(&hold);
                move || {
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
                            let hold = Arc::clone(&hold);
                            async move {
                                Ok::<_, hyper::Error>(service_fn(move |request| {
                                    let target = GatewayTarget {
                                        frontend_port,
                                        backend_port,
                                        mode,
                                        provider,
                                    };
                                    held_proxy_request(
                                        request,
                                        client.clone(),
                                        remote,
                                        target,
                                        Arc::clone(&hold),
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
                }
            })?;
        Ok(Self {
            address,
            shutdown: Some(shutdown_send),
            thread: Some(thread),
            hold,
        })
    }

    /// Start serving visitors, or stop again while the stack changes under it.
    pub(crate) fn set_open(&self, open: bool) {
        self.hold.set_open(open);
    }

    pub(crate) fn stop(&mut self) {
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

#[derive(Clone, Copy)]
pub(crate) struct GatewayTarget {
    pub(crate) frontend_port: u16,
    pub(crate) backend_port: u16,
    pub(crate) mode: SharingMode,
    pub(crate) provider: Option<TunnelProvider>,
}

pub(crate) async fn held_proxy_request(
    mut request: Request<Body>,
    client: Client<HttpConnector, Body>,
    remote: SocketAddr,
    target: GatewayTarget,
    hold: Arc<GatewayHold>,
) -> Result<Response<Body>, hyper::Error> {
    if !hold.admit(request.headers_mut()) {
        let mut response = simple_response(
            StatusCode::SERVICE_UNAVAILABLE,
            "This Lemma is getting ready to share. Try again in a moment.",
        );
        response
            .headers_mut()
            .insert("retry-after", HeaderValue::from_static("5"));
        return Ok(response);
    }
    proxy_request(request, client, remote, target).await
}

pub(crate) async fn proxy_request(
    mut request: Request<Body>,
    client: Client<HttpConnector, Body>,
    remote: SocketAddr,
    target: GatewayTarget,
) -> Result<Response<Body>, hyper::Error> {
    let GatewayTarget {
        frontend_port,
        backend_port,
        mode,
        provider,
    } = target;
    let client_ip = client_address(mode, provider, request.headers(), remote);
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
        forwarded_for(client_ip),
        host_text.replace('"', "")
    );
    insert_header(request.headers_mut(), "forwarded", &forwarded);
    insert_header(
        request.headers_mut(),
        "x-forwarded-for",
        &client_ip.to_string(),
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

/// RFC 7239 wants an IPv6 node quoted and bracketed.
fn forwarded_for(ip: IpAddr) -> String {
    match ip {
        IpAddr::V4(ip) => ip.to_string(),
        IpAddr::V6(ip) => format!("\"[{ip}]\""),
    }
}

pub(crate) fn proxy_target(
    path_and_query: &str,
    frontend_port: u16,
    backend_port: u16,
) -> (u16, String) {
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

pub(crate) fn strip_forwarding_headers(headers: &mut hyper::HeaderMap) {
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

pub(crate) fn insert_header(headers: &mut hyper::HeaderMap, name: &'static str, value: &str) {
    if let Ok(value) = HeaderValue::from_str(value) {
        headers.insert(HeaderName::from_static(name), value);
    }
}

pub(crate) fn is_websocket_upgrade(request: &Request<Body>) -> bool {
    request
        .headers()
        .get("upgrade")
        .and_then(|value| value.to_str().ok())
        .is_some_and(|value| value.eq_ignore_ascii_case("websocket"))
}

pub(crate) fn simple_response(status: StatusCode, message: &str) -> Response<Body> {
    Response::builder()
        .status(status)
        .header("content-type", "text/plain; charset=utf-8")
        .body(Body::from(message.to_owned()))
        .unwrap_or_else(|_| Response::new(Body::empty()))
}
