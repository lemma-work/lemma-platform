use super::*;
use std::net::ToSocketAddrs;

impl Daemon {
    pub(super) fn dispatch(
        self: &Arc<Self>,
        request: Value,
        client: &mpsc::SyncSender<String>,
    ) -> bool {
        let command = request
            .get("cmd")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_owned();
        let id = request.get("id").cloned();
        if command == "shutdown-daemon" {
            self.start_daemon_shutdown(id, client.clone());
            return true;
        }
        if self.lifecycle.checkpoint().is_err()
            && !matches!(
                command.as_str(),
                "ping"
                    | "status"
                    | "control.snapshot"
                    | "sharing.snapshot"
                    | "agent-host.status"
                    | "disconnect"
            )
        {
            self.send_direct(
                client,
                error_event(
                    "stopping",
                    "Lemma is stopping; new work is not accepted",
                    id.as_ref(),
                ),
            );
            return true;
        }
        match command.as_str() {
            "runtime.prepare" => {
                self.start_runtime_prepare(request, client.clone());
                return true;
            }
            // Fetching the sandbox image is a decision now, not a side effect
            // of starting. The reply is the acknowledgement; progress arrives
            // on the `sandbox-images` broadcast like every other state here.
            "sandbox.prepare" => {
                self.warm_sandbox_images();
                self.send_direct(
                    client,
                    json!({
                        "v": PROTOCOL_VERSION,
                        "event": "sandbox-prepare-started",
                        "id": id.as_ref(),
                    }),
                );
                return true;
            }
            "local.reset-data" => {
                self.start_local_data_reset(request, client.clone());
                return true;
            }
            "disk.cleanup" => {
                self.start_disk_cleanup(request, client.clone());
                return true;
            }
            "control.snapshot" => {
                match self.control_snapshot(id.as_ref()) {
                    Ok(event) => self.send_direct(client, event),
                    Err(error) => self.send_direct(
                        client,
                        error_event("control-snapshot-failed", error.to_string(), id.as_ref()),
                    ),
                }
                return true;
            }
            "sharing.snapshot" => {
                match self.sharing.as_ref() {
                    Some(sharing) => self.send_direct(
                        client,
                        json!({
                            "v": PROTOCOL_VERSION,
                            "event": "sharing.snapshot",
                            "id": id.as_ref(),
                            "sharing": sharing.snapshot(true),
                        }),
                    ),
                    None => self.send_direct(
                        client,
                        error_event(
                            "sharing-unavailable",
                            "sharing requires the managed local desktop runtime",
                            id.as_ref(),
                        ),
                    ),
                }
                return true;
            }
            "sharing.preflight" => {
                self.sharing_preflight(request, client);
                return true;
            }
            "sharing.enable" => {
                self.start_sharing_enable(request, client.clone());
                return true;
            }
            "sharing.access" => {
                self.start_sharing_access(request, client.clone());
                return true;
            }
            "sharing.disable" => {
                self.start_sharing_disable(id, client.clone());
                return true;
            }
            "config.apply" => {
                self.apply_operator_config(request, client);
                return true;
            }
            "config.discover-models" => {
                self.discover_provider_models(request, client);
                return true;
            }
            "config.set-ai" => {
                self.set_ai_profile(request, client);
                return true;
            }
            "config.test" => {
                self.test_setup(request, client);
                return true;
            }
            // The same-site alias the macOS workspace frames a pod app
            // through. The shell only asks for the local workspace; see
            // `crate::app_alias` for why it exists at all.
            "app-alias.resolve" => {
                let url = request
                    .get("url")
                    .and_then(Value::as_str)
                    .unwrap_or_default();
                let event = match self.app_aliases.as_ref() {
                    None => error_event(
                        "app-alias-unavailable",
                        "app aliases need the local Lemma to be installed",
                        id.as_ref(),
                    ),
                    Some(aliases) => match aliases.alias_url(url) {
                        Ok(alias) => json!({
                            "v": PROTOCOL_VERSION,
                            "event": "app-alias",
                            "id": id.as_ref(),
                            "url": alias,
                        }),
                        Err(error) => error_event("app-alias-refused", error, id.as_ref()),
                    },
                };
                self.send_direct(client, event);
                return true;
            }
            "desktop.release" => {
                self.release_for_desktop_exit(id.as_ref(), client);
                return true;
            }
            "agent-host.status" => {
                self.send_direct(
                    client,
                    json!({
                        "v": PROTOCOL_VERSION,
                        "event": "agent-host.status",
                        "id": id.as_ref(),
                        "agent_host": self.agent_host.detailed_status(),
                    }),
                );
                return true;
            }
            "agent-host.start"
            | "agent-host.stop"
            | "agent-host.restart"
            | "agent-host.pair"
            | "agent-host.unpair"
            | "agent-host.refresh"
            | "agent-host.session"
            | "agent-host.host-execution"
            | "agent-host.own-settings" => {
                self.start_agent_host_operation(command, request.clone(), client.clone());
                return true;
            }
            _ => {}
        }
        if let Some(manager) = self.host_processes.as_ref() {
            match command.as_str() {
                "status" => {
                    let mut event = manager.status_event(id.as_ref());
                    let state = self.state.lock().expect("state lock poisoned");
                    event["url"] = Value::String(state.url.clone());
                    event["api_url"] = Value::String(state.api_url.clone());
                    if let Some(runtime) = self.managed_runtime.as_ref() {
                        event["managed_runtime"] =
                            serde_json::to_value(runtime.status()).unwrap_or(Value::Null);
                    }
                    event["agent_host"] = self.agent_host.status();
                    self.send_direct(client, event);
                    return true;
                }
                "start" | "stop" | "restart" => {
                    self.start_host_operation(command, request, client.clone());
                    return true;
                }
                _ => {}
            }
        }
        match command.as_str() {
            "ping" => self.send_direct(
                client,
                json!({"v": PROTOCOL_VERSION, "event": "pong", "id": id.as_ref()}),
            ),
            "status" if !self.supervisor_running() => {
                let mut event = self
                    .state
                    .lock()
                    .expect("state lock poisoned")
                    .event(id.as_ref());
                event["agent_host"] = self.agent_host.status();
                self.send_direct(client, event);
            }
            "start" | "stop" | "restart" | "status" => {
                if let Err(error) = self.send_to_supervisor(request) {
                    self.send_direct(
                        client,
                        error_event("supervisor-unavailable", error.to_string(), id.as_ref()),
                    );
                }
            }
            "hello" => self.send_direct(
                client,
                error_event(
                    "already-authenticated",
                    "connection is already authenticated",
                    id.as_ref(),
                ),
            ),
            "disconnect" => {
                self.send_direct(
                    client,
                    json!({"v": PROTOCOL_VERSION, "event": "bye", "id": id.as_ref()}),
                );
                return false;
            }
            _ => self.send_direct(
                client,
                error_event(
                    "unknown-command",
                    format!("unknown command {command:?}"),
                    id.as_ref(),
                ),
            ),
        }
        true
    }

    // Desktop calls this on full quit. The daemon deliberately outlives the
    // app, so anything that must not survive the app - an open LAN or public
    // exposure, and the Agent Host - is torn down here rather than at daemon
    // shutdown.
}

/// What guestd says when an image pull failed because the registry's name did
/// not resolve inside the guest.
pub(super) const GUEST_DNS_FAILURE: &str = "registry DNS lookup failed";

/// The phrase in `explain_dns_failure`'s wording for a guest whose lookups
/// fail while this computer's succeed. Both platforms' sentences contain it.
const GUEST_DNS_BLOCKED: &str = "Lemma's VM can't look up names";

/// The phrase for a computer that cannot resolve the name either.
const HOST_OFFLINE: &str = "can't reach the internet right now";

/// The name every image pull starts with, and the one both sides are asked.
const REGISTRY_HOST: &str = "registry-1.docker.io";

/// A failed operation's message, reworded for a person when it was DNS.
///
/// guestd can only say that the guest could not resolve the registry. Whether
/// that is a VPN or DNS filter standing between the VM and the network, or no
/// network at all, is only knowable here: resolve the same name on this
/// computer. The raw message stays on the end, for the log and for whoever is
/// asked to read it.
///
/// The lookup runs on its own thread with a deadline: this is called while the
/// operation still holds the lifecycle, and a resolver that hangs would keep
/// every later operation answering `busy`. A lookup that does not finish in
/// time is taken as this computer not resolving it either.
pub(super) fn explain_runtime_failure(message: String) -> String {
    explain_dns_failure(message, cfg!(windows), || {
        host_resolves_within(REGISTRY_HOST, HOST_LOOKUP_DEADLINE)
    })
}

/// How long the host's own lookup may take before it counts as a failure.
const HOST_LOOKUP_DEADLINE: std::time::Duration = std::time::Duration::from_secs(3);

pub(super) fn host_resolves_within(host: &'static str, deadline: std::time::Duration) -> bool {
    let (sender, receiver) = std::sync::mpsc::channel();
    let spawned = std::thread::Builder::new()
        .name("lemma-dns-probe".into())
        .spawn(move || {
            let resolved = (host, 443)
                .to_socket_addrs()
                .is_ok_and(|mut addresses| addresses.next().is_some());
            // The receiver is gone once the deadline passed; nothing to tell.
            let _ = sender.send(resolved);
        });
    spawned.is_ok() && receiver.recv_timeout(deadline).unwrap_or(false)
}

pub(super) fn explain_dns_failure(
    message: String,
    windows: bool,
    host_resolves: impl FnOnce() -> bool,
) -> String {
    let explained = message.contains(GUEST_DNS_BLOCKED) || message.contains(HOST_OFFLINE);
    if !message.contains(GUEST_DNS_FAILURE) || explained {
        return message;
    }
    let explanation = match (host_resolves(), windows) {
        (true, false) => {
            "Your Mac can reach the internet, but Lemma's VM can't look up names. A VPN \
             or DNS filter such as Cloudflare WARP is likely blocking it. Pause it and \
             press Try again, or allow Lemma's VM through it."
        }
        // WSL resolves through Windows, so the host relay is not involved;
        // what is in the way is a VPN, and WSL's own DNS tunnelling is the
        // setting that routes around it.
        (true, true) => {
            "Your PC can reach the internet, but Lemma's VM can't look up names. A VPN \
             or DNS filter is likely blocking WSL. Pause it and press Try again, or set \
             dnsTunneling=true under [wsl2] in your .wslconfig."
        }
        (false, _) => {
            "This computer can't reach the internet right now. Connect to a network, \
             then press Try again."
        }
    };
    format!("{explanation} ({message})")
}

pub(super) fn runtime_operation_error_code(message: &str, fallback: &'static str) -> &'static str {
    if message.contains("Linux guest kernel crashed") {
        "guest-kernel-failed"
    } else if message.contains(GUEST_DNS_BLOCKED) {
        "guest-dns-blocked"
    } else if message.contains(HOST_OFFLINE) {
        "network-dns-failed"
    } else if message.contains("restart to finish enabling WSL 2") {
        "wsl-reboot-required"
    } else if message.contains("WSL 2 is required") {
        "wsl-required"
    } else if message.contains("did not approve or complete WSL 2 setup") {
        "wsl-setup-denied"
    } else if message.contains(crate::paths::DATA_RESET_MARKER) {
        // One phrase, one code, however many detectors raise it. Anything the
        // user cannot fix by retrying but can fix by discarding local data says
        // the marker phrase and lands here.
        "local-data-incompatible"
    } else {
        fallback
    }
}

/// Which component failed, and which diagnostic log will say why.
///
/// The second half is a *log source id*, and the shell serves a fixed set of
/// them (`diagnostic_log_sources` in `desktop/src/diagnostics.rs`). It used to
/// answer "infrastructure", which is not one of them: `read_diagnostic_log`
/// refuses an id it does not serve, so a guest-kernel failure -- or anything
/// mentioning a container, a registry or the guest -- selected a tab that
/// could not be read. Those are the failures a person opens the log *for*.
///
/// `vm` is `logs/runtime.log`, which the runtime manager writes on both
/// platforms and is where all of these are recorded. The first half is a label,
/// not an id, and stays as it was.
///
/// `pub` so `every_log_source_the_daemon_names_is_one_the_shell_serves` can ask
/// it. The two halves of this contract are compiled into different binaries,
/// and nothing else can put them in the same room.
pub fn error_diagnostic_source(message: &str) -> (&'static str, &'static str) {
    let message = message.to_ascii_lowercase();
    // DNS first: the reworded message names neither the guest nor the
    // registry, and the raw report on its end could mention anything.
    let dns = message.contains(&GUEST_DNS_FAILURE.to_ascii_lowercase())
        || message.contains("look up names");
    if dns || message.contains("guest kernel") {
        ("infrastructure", "vm")
    } else if message.contains("migration") || message.contains("alembic") {
        ("migrations", "migrations")
    } else if message.contains("frontend") || message.contains("eaddrinuse") {
        ("frontend", "frontend")
    } else if message.contains("backend") || message.contains("health gate") {
        ("backend", "backend")
    } else if message.contains("runtime")
        || message.contains("container")
        || message.contains("registry")
        || message.contains("guest")
    {
        ("infrastructure", "vm")
    } else {
        ("locald", "events")
    }
}
