//! The Cloudflare provider, and the tunnel it manages on the account.

use super::*;

pub(crate) fn preflight_cloudflare() -> ProviderReadiness {
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
        .no_console_window()
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
                String::from_utf8_lossy(&output.stderr).as_ref(),
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

pub(crate) fn parse_cloudflare_tunnels(raw: &[u8]) -> Vec<CloudflareTunnel> {
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

pub(crate) fn parse_created_cloudflare_tunnel(raw: &[u8]) -> Option<CloudflareTunnel> {
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

pub(crate) fn managed_cloudflare_tunnel_name(root: &Path) -> io::Result<String> {
    let installation_id = installation_identity(root)?;
    Ok(format!("lemma-desktop-{}", &installation_id[..12]))
}

pub(crate) fn cloudflare_credentials_path(tunnel_id: &str) -> io::Result<PathBuf> {
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

impl SharingController {
    pub(crate) fn start_cloudflare(
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
            .no_console_window()
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

    pub(crate) fn ensure_managed_cloudflare_tunnel(
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
                .no_console_window()
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
                .no_console_window()
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

    pub(crate) fn persist_managed_cloudflare_preferences(
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
