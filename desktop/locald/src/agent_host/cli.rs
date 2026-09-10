//! Running the Agent Host's own CLI, and reading what it prints back.

use super::*;

/// `connect` and `disconnect` reach the backend; `refresh` only bumps a
/// generation counter locally but still opens the journal.
pub(crate) const CLI_TIMEOUT: Duration = Duration::from_secs(45);
/// `refresh` re-probes every installed agent, and a probe spawns the agent and
/// opens an ACP session with its own 20s ceiling.
pub(crate) const REFRESH_TIMEOUT: Duration = Duration::from_secs(180);

/// `connect` had a ten-minute deadline of its own, because it was not a request
/// but an installation: it fetched and verified a pinned adapter package for
/// every certified agent before it could report success. It no longer installs
/// anything — the cache is warmed when the app opens — so it is a pairing call
/// with one network round trip in it, and it takes the ordinary deadline.
pub(crate) fn cli_timeout(verb: &str) -> Duration {
    match verb {
        "refresh" => REFRESH_TIMEOUT,
        _ => CLI_TIMEOUT,
    }
}

/// Reduce one journal entry to what a status view needs.
///
/// The host's own report also carries its service-manager wiring and local
/// paths, which say nothing about whether this workspace is reachable.
pub(crate) fn summarize_target(target: &Value) -> Value {
    let journal = target.get("journal");
    let field = |key: &str| journal.and_then(|value| value.get(key)).cloned();
    json!({
        "target_id": target.get("target_id"),
        "host_id": target.get("host_id"),
        "name": target.get("name"),
        "url": target.get("url"),
        "enabled": target.get("enabled"),
        "connection_state": field("connection_state"),
        "last_connected_at": field("last_connected_at"),
        "last_error": field("last_error"),
        "active_runs": field("active_runs"),
        "pending_events": field("pending_events"),
    })
}

/// Loopback HTTP is the one plain-HTTP case the host accepts, and only when
/// asked. A development backend is served that way.
pub(crate) fn is_loopback_http(url: &str) -> bool {
    is_loopback_http_for(url, &crate::local_domain::LocalDomain::from_env())
}

/// The check with the install's domain handed in.
///
/// Split so the tests can state which domain they mean. `from_env` probes DNS
/// and caches the answer for the process, so a test that leaned on it would
/// assert one thing on a machine with a network and the opposite on one
/// without -- and a gate that flips with the weather gets switched off.
pub(crate) fn is_loopback_http_for(url: &str, domain: &crate::local_domain::LocalDomain) -> bool {
    let Some(rest) = url.strip_prefix("http://") else {
        return false;
    };
    let authority = rest.split(['/', '?', '#']).next().unwrap_or_default();
    let host = match authority.rsplit_once(':') {
        Some((host, port)) if !port.is_empty() && port.chars().all(|c| c.is_ascii_digit()) => host,
        _ => authority,
    };
    // Twice now. `.localhost` is reserved to loopback by RFC 6761, and matching
    // only the three literal spellings meant this flag was never passed for a
    // desktop install's own URL, so the host refused to pair with the very
    // workspace that asked it to. Adding `.localhost` fixed that -- and then the
    // base domain stopped being `.localhost`.
    //
    // An install now serves itself under whatever `LocalDomain` resolved,
    // because a browser derives no registrable domain from `*.localhost` and a
    // pod app framed by the workspace needs one. On such an install the URL is
    // `app.127.0.0.1.sslip.io:<port>`: loopback in every way that matters --
    // the name resolves to 127.0.0.1 and the backend binds there -- and matched
    // by none of the spellings above. Pairing failed silently, and the
    // onboarding step sat on "Connecting this computer" for ever.
    //
    // So the question this asks is the one it always meant: is this address
    // this installation's own? Asking `LocalDomain` means the next time the
    // domain moves, this moves with it.
    matches!(host, "localhost" | "127.0.0.1" | "[::1]")
        || host.ends_with(".localhost")
        || domain.owns_host(host)
}

/// Strip anything from a subprocess message that we passed in as a secret.
pub(crate) fn redact_secrets(detail: &str, arguments: &[&str]) -> String {
    let mut redacted = detail.to_string();
    let mut arguments = arguments.iter().peekable();
    while let Some(argument) = arguments.next() {
        if *argument != "--pairing-code" {
            continue;
        }
        if let Some(secret) = arguments.peek() {
            if !secret.is_empty() {
                redacted = redacted.replace(*secret, "<pairing code>");
            }
        }
    }
    redacted
}

impl AgentHostSupervisor {
    pub(crate) fn run_cli(&self, arguments: &[&str]) -> io::Result<String> {
        let executable = self.executable.as_ref().ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::NotFound,
                "lemma-agent-host sidecar is not installed",
            )
        })?;
        std::fs::create_dir_all(&self.data_dir)?;
        let mut command = Command::new(executable);
        command
            .no_console_window()
            .arg("--data-dir")
            .arg(&self.data_dir)
            .args(arguments)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            // Its own group, for the same reason `spawn_process` does it: this
            // is how `refresh` runs, and a refresh re-probes every installed
            // agent -- which spawns each one. `child.kill()` reaches the CLI and
            // nothing it started, so a `refresh` that hit its 180-second ceiling
            // used to leave a probe of every agent on the machine behind.
            command.process_group(0);
        }
        // Nothing below may return without reaping. `Child::drop` neither kills
        // nor waits, and two of the lines that follow used `?`.
        let mut child = Reaped(Some(command.spawn()?));

        let deadline = Instant::now() + cli_timeout(arguments[0]);
        loop {
            if child.get().try_wait()?.is_some() {
                break;
            }
            if Instant::now() >= deadline {
                return Err(io::Error::new(
                    io::ErrorKind::TimedOut,
                    format!("Agent Host did not answer `{}` in time", arguments[0]),
                ));
            }
            std::thread::sleep(Duration::from_millis(50));
        }

        let output = child.take().wait_with_output()?;
        if !output.status.success() {
            let detail = String::from_utf8_lossy(&output.stderr);
            let detail = detail.trim();
            return Err(io::Error::other(if detail.is_empty() {
                format!("Agent Host `{}` failed", arguments[0])
            } else {
                // stderr can quote the argument list, and one of those
                // arguments may be a live pairing code.
                redact_secrets(detail, arguments)
            }));
        }
        Ok(String::from_utf8_lossy(&output.stdout).into_owned())
    }
}
