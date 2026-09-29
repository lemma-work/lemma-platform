//! What to collect when something is wrong, and the one check that runs
//! before every mutation.

use super::*;

/// What a failed start collects from inside the guest.
///
/// The same text the Windows host runs through `wsl.exe --exec`, from the same
/// file, because a VZ guest has no exec channel and macOS otherwise collected
/// nothing whatsoever. Compiled in rather than accepted from the host: a host
/// that could post a shell script here would have a general-purpose exec
/// channel into the guest wearing a diagnostics label. The cost is that an
/// older guest collects with an older script, which is the right way round --
/// the guest decides what may run in it.
pub(crate) const GUEST_DIAGNOSTICS: &str = include_str!("../../guest-diagnostics.sh");

/// How long the guest gives its own collection before killing it.
///
/// Bounded here as well as by the host's request deadline, because the two
/// limits do different things: the host's stops waiting and returns nothing,
/// this one stops collecting and returns everything printed so far. `nerdctl
/// ps` against a wedged containerd is exactly the case that needs the
/// difference, and it is also the case someone is most likely collecting for.
pub(crate) const DIAGNOSTICS_TIMEOUT: &str = "20s";

/// Kept to the tail, matching what the host writes to `logs/guest.log`.
pub(crate) const MAX_DIAGNOSTICS_BYTES: usize = 128 * 1024;

/// Refuse to write anything while the data is not on the storage that keeps it.
///
/// A WSL guest can be brought up by anything that runs a command in it: the
/// bridge does exactly that for every request, and WSL restarts a terminated
/// distribution to serve one. Nothing in that path runs
/// `lemma-runtime-init`, so a distribution restarted that way has no binds --
/// and `/var/lib/lemma` is then an ordinary directory on the runtime
/// distribution's own disk, which the next upgrade deletes.
///
/// That is the failure the data holder exists to prevent, arriving by the one
/// door the holder does not stand in. So: if this guest has a holder at all,
/// the binds are not optional, and a mutation that would write outside them is
/// refused rather than quietly misplaced.
///
/// Retryable, because it is: the host's start path runs the init that fixes
/// it. Scoped to guests that have a holder, so it says nothing at all on macOS
/// or in a test, where `/mnt/wsl` does not exist.
pub(crate) fn refuse_unbound_data() -> Result<(), GuestError> {
    // The holder's own path is not what makes a guest WSL, and scoping on it
    // was a hole in the exact case this guard exists for: a share that was
    // never published leaves no directory, so the check said "no holder,
    // nothing to protect" and let the mutation through to write on the disk
    // the next upgrade deletes. Absent is not "not applicable", it is the
    // failure.
    if !crate::host_control::guest_is_wsl() {
        return Ok(());
    }
    if is_mountpoint(Path::new("/var/lib/lemma")) {
        return Ok(());
    }
    Err(GuestError {
        code: "guest_data_unbound".into(),
        message: "Lemma's private runtime is not holding your data yet; it is \
                  still starting."
            .into(),
        retryable: true,
        status_code: 503,
    })
}

pub(crate) fn is_mountpoint(path: &Path) -> bool {
    Command::new("/usr/bin/mountpoint")
        .arg("-q")
        .arg(path)
        .stdin(Stdio::null())
        .status()
        .is_ok_and(|status| status.success())
}

pub(crate) fn guest_diagnostics() -> Value {
    let output = Command::new("/usr/bin/timeout")
        .args([
            "--signal=KILL",
            DIAGNOSTICS_TIMEOUT,
            "/bin/sh",
            "-c",
            GUEST_DIAGNOSTICS,
        ])
        .stdin(Stdio::null())
        .output();
    let text = match output {
        // The exit status is ignored on purpose, killed-at-the-limit included.
        // The script runs under `set +e` precisely so that one collector
        // failing does not stop the next, and what it printed before giving up
        // is the reason anyone asked.
        Ok(output) => {
            let start = output.stdout.len().saturating_sub(MAX_DIAGNOSTICS_BYTES);
            String::from_utf8_lossy(&output.stdout[start..]).into_owned()
        }
        Err(error) => format!("guest diagnostics could not run: {error}\n"),
    };
    json!({ "text": text })
}

pub(crate) fn network_diagnostics() -> Value {
    let dns = Command::new("/usr/bin/timeout")
        .args([
            "--signal=KILL",
            "5s",
            "/usr/bin/getent",
            "ahostsv4",
            REGISTRY_HOST,
        ])
        .stdin(Stdio::null())
        .output();
    let mut addresses = Vec::new();
    if let Ok(output) = &dns {
        if output.status.success() {
            for address in String::from_utf8_lossy(&output.stdout)
                .lines()
                .filter_map(|line| line.split_whitespace().next())
            {
                if valid_ip(address) && !addresses.iter().any(|value| value == address) {
                    addresses.push(address.to_owned());
                }
                if addresses.len() == 4 {
                    break;
                }
            }
        }
    }

    // Docker Hub's registry endpoint normally answers an unauthenticated
    // /v2/ request with 401. That still proves DNS, routing and TLS are usable.
    let registry = Command::new("/usr/bin/curl")
        .args([
            "--head",
            "--silent",
            "--output",
            "/dev/null",
            "--connect-timeout",
            "3",
            "--max-time",
            "5",
            "--write-out",
            "%{http_code}",
            "https://registry-1.docker.io/v2/",
        ])
        .stdin(Stdio::null())
        .output();
    let registry_status = registry
        .ok()
        .filter(|output| output.status.success())
        .and_then(|output| String::from_utf8(output.stdout).ok())
        .and_then(|value| value.trim().parse::<u16>().ok());
    let registry_reachable = registry_status.is_some_and(|status| (200..500).contains(&status));
    let clock_epoch = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();

    json!({
        "clock_epoch": clock_epoch,
        "dns_ok": !addresses.is_empty(),
        "host_dns_relay": host_dns_relay_answers(),
        "name_servers": name_servers(),
        "registry_addresses": addresses,
        "registry_http_status": registry_status,
        "registry_reachable": registry_reachable,
    })
}

/// The name Docker Hub's images are pulled from, and the one every DNS check
/// here asks about.
pub(crate) const REGISTRY_HOST: &str = "registry-1.docker.io";

/// Whether the host DNS relay resolves the registry, asked directly rather
/// than through resolved: the Mac's resolver's own view. `None` on WSL, which
/// has no relay and resolves through Windows.
fn host_dns_relay_answers() -> Option<bool> {
    if crate::host_control::guest_is_wsl() {
        return None;
    }
    let relay = crate::host_dns::HOST_DNS_ADDRESS.parse().ok()?;
    Some(crate::host_dns::answers_address(
        relay,
        REGISTRY_HOST,
        Duration::from_secs(3),
    ))
}

/// The name servers the guest is using, as resolved reports them, or from
/// `/etc/resolv.conf` where resolved is not running (WSL).
fn name_servers() -> Vec<String> {
    let resolved = Command::new("/usr/bin/timeout")
        .args(["--signal=KILL", "3s", "resolvectl", "dns"])
        .stdin(Stdio::null())
        .output()
        .ok()
        .filter(|output| output.status.success())
        .map(|output| String::from_utf8_lossy(&output.stdout).into_owned());
    let resolv_conf = fs::read_to_string("/etc/resolv.conf").unwrap_or_default();
    parse_name_servers(resolved.as_deref(), &resolv_conf)
}

/// Every address in `resolvectl dns` output ("Global: 127.0.0.2", "Link 2
/// (enp0s1): 192.168.64.1"), or failing that the `nameserver` lines of
/// resolv.conf. resolved's own stub is left out: it says nothing about where
/// a lookup actually goes.
pub(crate) fn parse_name_servers(resolvectl: Option<&str>, resolv_conf: &str) -> Vec<String> {
    let mut servers: Vec<String> = Vec::new();
    let mut add = |value: &str| {
        let value = value.trim_end_matches(',');
        if value.parse::<IpAddr>().is_ok()
            && value != "127.0.0.53"
            && !servers.iter().any(|seen| seen == value)
        {
            servers.push(value.to_owned());
        }
    };
    match resolvectl {
        Some(output) => {
            for line in output.lines() {
                let values = line.split_once(':').map_or("", |(_, values)| values);
                values.split_whitespace().for_each(&mut add);
            }
        }
        None => {
            for line in resolv_conf.lines() {
                if let Some(address) = line.trim().strip_prefix("nameserver") {
                    add(address.trim());
                }
            }
        }
    }
    servers
}

/// What an image pull that failed on DNS says about it, in plain words.
///
/// Starts with the phrase locald keys on (`registry DNS lookup failed`), then
/// names the servers that were asked, and -- on macOS -- whether the Mac's own
/// resolver, through the relay, could answer. That last fact is what tells a
/// VPN or DNS filter blocking the VM apart from a computer with no network.
pub(crate) fn dns_failure_hint(name_servers: &[String], relay: Option<bool>) -> String {
    let asked = if name_servers.is_empty() {
        "no name server is configured".to_owned()
    } else {
        format!("asked {}", name_servers.join(", "))
    };
    let relay = match relay {
        Some(true) => "; the host DNS relay can resolve it, so the VM's resolver is not using it",
        Some(false) => "; the host DNS relay could not resolve it either",
        None => "",
    };
    format!(
        "registry DNS lookup failed: Lemma's VM could not look up {REGISTRY_HOST} \
         ({asked}{relay})"
    )
}
