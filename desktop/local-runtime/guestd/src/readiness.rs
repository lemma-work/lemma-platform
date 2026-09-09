//! Waiting for something to answer, and saying what it was when nothing
//! did.

use super::*;

/// AUTH and PING, and deliberately nothing about modules.
///
/// This used to require `MODULE LIST` to report `search` and `rejson`, which
/// tied the guest to a Redis Stack image. Nothing in the product ever issued a
/// `JSON.*` or `FT.*` command -- `RedisJsonCache` stores JSON as an ordinary
/// string through GET/SET, and vector search is Postgres -- so the assertion
/// only made a smaller Redis impossible to adopt.
pub(crate) fn redis_ready(host: &str, password: &str) -> io::Result<()> {
    validate_secret("redis_password", password)
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidInput, error.message))?;
    let address = (host, 6379)
        .to_socket_addrs()?
        .next()
        .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "Redis host not found"))?;
    let mut stream = TcpStream::connect_timeout(&address, Duration::from_secs(1))?;
    stream.set_read_timeout(Some(Duration::from_secs(2)))?;
    stream.set_write_timeout(Some(Duration::from_secs(2)))?;
    write!(
        stream,
        "*2\r\n$4\r\nAUTH\r\n${}\r\n{}\r\n*1\r\n$4\r\nPING\r\n*1\r\n$4\r\nQUIT\r\n",
        password.len(),
        password
    )?;
    stream.flush()?;
    let mut response = Vec::new();
    stream.take(64 * 1024).read_to_end(&mut response)?;
    let response = std::str::from_utf8(&response)
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "Redis returned non-UTF8"))?;
    let response = response.to_ascii_lowercase();
    if response.contains("+ok\r\n") && response.contains("+pong\r\n") {
        Ok(())
    } else {
        Err(io::Error::other("Redis authentication or ping failed"))
    }
}

pub fn probe_http(url: &str) -> io::Result<()> {
    let remainder = url
        .strip_prefix("http://")
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "probe requires HTTP"))?;
    let (authority, path) = remainder.split_once('/').unwrap_or((remainder, ""));
    let (host, port) = authority
        .rsplit_once(':')
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "probe requires port"))?;
    let port: u16 = port
        .parse()
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "invalid probe port"))?;
    let addresses: Vec<SocketAddr> = (host, port).to_socket_addrs()?.collect();
    let mut stream = TcpStream::connect_timeout(
        addresses
            .first()
            .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "host not found"))?,
        Duration::from_secs(1),
    )?;
    stream.set_read_timeout(Some(Duration::from_secs(2)))?;
    write!(
        stream,
        "GET /{} HTTP/1.1\r\nHost: {}\r\nConnection: close\r\n\r\n",
        path, authority
    )?;
    let mut response = [0_u8; 64];
    let count = stream.read(&mut response)?;
    let status = std::str::from_utf8(&response[..count])
        .ok()
        .and_then(|value| value.lines().next())
        .and_then(|line| line.split_whitespace().nth(1))
        .and_then(|value| value.parse::<u16>().ok())
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "invalid HTTP response"))?;
    if (200..500).contains(&status) {
        Ok(())
    } else {
        Err(io::Error::other(format!("health returned {status}")))
    }
}

/// Explain a failed sandbox callback, including which side of it is broken.
///
/// "sandbox cannot reach the Lemma API callback: probe timed out" is equally
/// true of a denied Local Network permission, a VPN that took the default
/// route, a host relay that failed to bind, and a sandbox that has not
/// finished starting -- and it distinguishes none of them. `diagnostics.network`
/// could answer that, and until now nothing ever called it.
pub(crate) fn callback_failure_message(last_error: Option<&str>, guest_egress_ok: bool) -> String {
    let cause = last_error.unwrap_or("probe timed out");
    let egress = if guest_egress_ok {
        "the guest's own network is working, so this is the route back to this computer"
    } else {
        "the guest cannot resolve names either, so its network is unavailable rather than \
         just this route"
    };
    format!("sandbox cannot reach the Lemma API callback: {cause}. {egress}.")
}

impl<E: Engine + 'static> GuestService<E> {
    pub(crate) fn wait_tcp(&self, port: u16, timeout: u64) -> Result<(), GuestError> {
        let deadline = Instant::now() + Duration::from_secs(timeout);
        while Instant::now() < deadline {
            if TcpStream::connect_timeout(
                &format!("{GUEST_LOOPBACK}:{port}")
                    .to_socket_addrs()
                    .map_err(|error| GuestError::engine(error.to_string()))?
                    .next()
                    .ok_or_else(|| GuestError::engine("core endpoint did not resolve"))?,
                Duration::from_secs(1),
            )
            .is_ok()
            {
                return Ok(());
            }
            thread::sleep(Duration::from_millis(250));
        }
        Err(GuestError::engine(format!(
            "core TCP port {port} did not become ready"
        )))
    }

    pub(crate) fn wait_http_port(
        &self,
        port: u16,
        path: &str,
        timeout: u64,
    ) -> Result<(), GuestError> {
        let deadline = Instant::now() + Duration::from_secs(timeout);
        while Instant::now() < deadline {
            let url = format!("http://{GUEST_LOOPBACK}:{port}{path}");
            if probe_http(&url).is_ok() {
                return Ok(());
            }
            thread::sleep(Duration::from_millis(250));
        }
        Err(GuestError::engine(format!(
            "core HTTP port {port} did not become ready"
        )))
    }

    pub(crate) fn wait_redis(&self, password: &str, timeout: u64) -> Result<(), GuestError> {
        let deadline = Instant::now() + Duration::from_secs(timeout);
        while Instant::now() < deadline {
            if redis_ready(GUEST_LOOPBACK, password).is_ok() {
                return Ok(());
            }
            thread::sleep(Duration::from_millis(250));
        }
        Err(GuestError::engine(
            "Redis Stack did not become ready with Search and JSON modules",
        ))
    }

    pub(crate) fn wait_callback(&self, parameters: &EnsureParameters) -> Result<(), GuestError> {
        if !parameters.callback.required {
            return Ok(());
        }
        let base = parameters
            .callback
            .url
            .as_deref()
            .or_else(|| parameters.env.get("LEMMA_BASE_URL").map(String::as_str))
            .ok_or_else(|| {
                GuestError::invalid("Local sandbox requires an explicit callback URL")
            })?;
        let probe_url = callback_probe_url(base, &parameters.callback.health_path)?;
        let deadline = Instant::now()
            + Duration::from_secs_f64(parameters.callback.timeout_seconds.clamp(1.0, 300.0));
        let script = concat!(
            "import sys,urllib.request;",
            "r=urllib.request.urlopen(sys.argv[1],timeout=2);",
            "raise SystemExit(0 if 200<=r.status<300 else 1)"
        );
        let mut last_error = None;
        while Instant::now() < deadline {
            let output = self.engine.run(&[
                "exec".into(),
                container_name(&parameters.sandbox_id),
                "python".into(),
                "-c".into(),
                script.into(),
                probe_url.clone(),
            ]);
            match output {
                Ok(output) if output.status.success() => return Ok(()),
                Ok(output) => {
                    last_error = Some(redact_engine_error(&String::from_utf8_lossy(
                        &output.stderr,
                    )))
                }
                Err(error) => last_error = Some(error),
            }
            thread::sleep(Duration::from_millis(250));
        }
        // Say which way the network is broken, while the evidence is fresh.
        //
        // "sandbox cannot reach the Lemma API callback: probe timed out" is
        // true of a denied Local Network permission, a VPN that took the
        // default route, a host relay that failed to bind, and a sandbox that
        // simply has not finished starting -- and it distinguishes none of
        // them. `diagnostics.network` could answer that, and nothing called
        // it. Guest egress working while the callback fails points at the
        // host's relay; egress failing points outside Lemma altogether.
        let network = network_diagnostics();
        Err(GuestError::engine(callback_failure_message(
            last_error.as_deref(),
            network["dns_ok"] == json!(true),
        )))
    }
}
