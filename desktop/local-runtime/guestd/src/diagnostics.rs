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
    // `/mnt/wsl` is the tmpfs WSL mounts in every distribution, and nothing
    // else has it, so it is what "this guest is WSL" means here.
    //
    // The holder's own path is not that, and scoping on it was a hole in the
    // exact case this guard exists for: a share that was never published
    // leaves no directory, so the check said "no holder, nothing to protect"
    // and let the mutation through to write on the disk the next upgrade
    // deletes. Absent is not "not applicable", it is the failure.
    if !Path::new("/mnt/wsl").is_dir() {
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
            "registry-1.docker.io",
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
        "registry_addresses": addresses,
        "registry_http_status": registry_status,
        "registry_reachable": registry_reachable,
    })
}
