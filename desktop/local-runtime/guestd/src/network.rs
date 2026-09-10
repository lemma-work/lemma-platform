//! Which addresses this guest has, and which one the host reaches it on.

use super::*;

pub(crate) fn valid_ip(value: &str) -> bool {
    value.parse::<IpAddr>().is_ok_and(|address| match address {
        IpAddr::V4(address) => !address.is_unspecified() && !address.is_multicast(),
        IpAddr::V6(_) => false,
    })
}

pub(crate) fn discover_guest_ip() -> Option<String> {
    command_stdout("ip", &["-4", "-o", "addr", "show", "scope", "global"])
        .as_deref()
        .and_then(first_reachable_address)
}

/// The first address the *host* could actually connect to.
///
/// This used to scan the whole of `ip -o addr`'s output for the first token
/// that looked like an address, which is right exactly as long as the first
/// line is a real interface. On the VZ guest it is, so this was correct for
/// years and on one platform.
///
/// WSL puts `10.255.255.254/32` on `lo`, with `scope global`, and `ip` lists
/// `lo` first. So on Windows the guest reported a loopback address as the
/// place to reach it, the host dialled it, and every connection timed out --
/// while PostgreSQL and Redis sat listening on `eth0` and on the host's own
/// `127.0.0.1` the whole time. Measured: during a real start, both
/// `127.0.0.1:5432` and `172.24.27.216:5432` accepted connections from the
/// host on every one of 44 polls, and the start still failed with
/// "PostgreSQL: connection timed out".
///
/// Loopback is skipped by interface, not by address range, because that is the
/// thing that is actually wrong with it: an address on `lo` is not somewhere
/// another machine can reach, whatever its scope says.
pub(crate) fn first_reachable_address(output: &str) -> Option<String> {
    output.lines().find_map(|line| {
        let mut fields = line.split_whitespace();
        // `<index>: <interface> inet <address>/<prefix> ...`
        let _index = fields.next()?;
        // `eth0@if5` on a veth; the name is the part before the `@`.
        let interface = fields.next()?.split('@').next()?;
        if interface == "lo" {
            return None;
        }
        fields
            .find_map(|value| value.split_once('/'))
            .map(|(address, _)| address)
            .filter(|address| valid_ip(address))
            .map(str::to_owned)
    })
}

pub(crate) fn discover_host_gateway() -> Option<String> {
    command_stdout("ip", &["-4", "route", "show", "default"]).and_then(|output| {
        let mut fields = output.split_whitespace();
        while let Some(field) = fields.next() {
            if field == "via" {
                return fields
                    .next()
                    .filter(|value| valid_ip(value))
                    .map(str::to_owned);
            }
        }
        None
    })
}

pub(crate) fn command_stdout(command: &str, args: &[&str]) -> Option<String> {
    let output = Command::new(command).args(args).output().ok()?;
    output
        .status
        .success()
        .then(|| String::from_utf8_lossy(&output.stdout).into_owned())
}

pub(crate) fn load_capability() -> Result<Option<String>, GuestError> {
    let Some(path) = std::env::var_os("LEMMA_GUEST_CAPABILITY_FILE") else {
        return Ok(None);
    };
    let value = fs::read_to_string(path)
        .map_err(|error| GuestError::engine(format!("could not read capability: {error}")))?;
    let value = value.trim();
    if value.len() < 32 || value.len() > 512 {
        return Err(GuestError::engine("guest capability has invalid length"));
    }
    Ok(Some(value.into()))
}
