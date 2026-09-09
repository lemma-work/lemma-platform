//! The ngrok provider.

use super::*;

pub(crate) fn preflight_ngrok() -> ProviderReadiness {
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

pub(crate) fn ngrok_config_path(executable: &Path) -> io::Result<PathBuf> {
    let output = command_text(executable, &["config", "check"]).map_err(io::Error::other)?;
    let marker = " at ";
    let path = output
        .lines()
        .find_map(|line| line.rsplit_once(marker).map(|(_, path)| path.trim()))
        .filter(|path| !path.is_empty())
        .ok_or_else(|| io::Error::other("ngrok did not report its configuration path"))?;
    Ok(PathBuf::from(path))
}

pub(crate) fn wait_for_ngrok_url(
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

pub(crate) fn find_public_https_url(value: &Value) -> Option<String> {
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

impl SharingController {
    pub(crate) fn start_ngrok(&self, gateway_origin: &str) -> io::Result<(String, OwnedTunnel)> {
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
        let inspection = PortReservation::ephemeral()?;
        let inspection_port = inspection.port();
        let supplemental = self.root.join("ngrok-lemma.yml");
        let contents = format!("version: 3\nagent:\n  web_addr: 127.0.0.1:{inspection_port}\n");
        write_private(&supplemental, contents.as_bytes())?;
        let log_path = self.root.join("logs/ngrok.log");
        let log = bounded_log(&log_path)?;
        let error_log = log.try_clone()?;
        let mut command = Command::new(&executable);
        command
            .no_console_window()
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
        // ngrok binds the inspection port in its own process, so the claim has
        // to end here — but not one step earlier. Everything above is a synced
        // config write and a log open, and a bare port number left unguarded
        // across those is long enough for something else to take it; ngrok
        // would then fail to serve its agent API, or worse, a stranger would
        // answer the tunnel-URL poll below on the port we told ngrok to use.
        inspection.release();
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
}
