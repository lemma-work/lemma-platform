//! The local gateway a tunnel points at, and what it forwards.

use super::*;

impl GatewayHandle {
    pub(crate) fn start(
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

pub(crate) async fn proxy_request(
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
