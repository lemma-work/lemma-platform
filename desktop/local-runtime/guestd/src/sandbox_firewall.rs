//! Keeping sandbox containers away from the guest's own services.
//!
//! PostgreSQL, Redis and SuperTokens run with host networking and listen on
//! every address in the guest -- that is how the host reaches them, over the
//! vsock proxies on macOS and the guest's address on WSL. A sandbox container
//! sits on nerdctl's default bridge, and a packet from it to *any* of the
//! guest's own addresses (the bridge gateway, the DHCP address) is delivered
//! locally: it traverses `INPUT` arriving on the bridge interface. So without
//! a rule, code running in any sandbox -- including one belonging to somebody
//! the owner invited onto a shared installation -- could open the database
//! every account lives in, and SuperTokens' core, which has no API key here
//! and will mint a session for any user id it is asked to.
//!
//! The rule is narrow on purpose. It rejects new TCP connections *from the
//! sandbox bridge* to those three ports and nothing else: the internet is
//! forwarded, not delivered locally, so it never reaches `INPUT`; the backend's
//! way into a sandbox is a published port, which is a connection *to* the
//! container and only its replies cross the bridge; and callbacks to the host
//! go through `host.lemma.internal`, the host's address, which is forwarded
//! too. A blanket reject on the bridge would also have been correct today, and
//! would have broken silently the first time a sandbox needed something the
//! guest itself serves -- this one fails only for the thing it names.
//!
//! Rejected rather than dropped, so a sandbox that tries gets an immediate
//! "connection refused" rather than a thirty-second timeout that reads as a
//! network fault.

use super::*;

/// The interface nerdctl gives its default `bridge` network, which is the one
/// every sandbox is run on (`build_run_arguments` names no other).
pub(crate) const SANDBOX_BRIDGE_INTERFACE: &str = "nerdctl0";
/// A chain of our own, so the rules can be recognised, checked and replaced
/// without reading anybody else's.
pub(crate) const SANDBOX_ISOLATION_CHAIN: &str = "LEMMA-SANDBOX-ISOLATION";
/// The guest's core services. Kept beside the containers that listen on them
/// in `core.rs`; a new core service belongs here too.
pub(crate) const GUEST_CORE_PORTS: [u16; 3] = [5432, 6379, 3567];

/// The rules, as `iptables` argument lists, each in its `-C`heck form.
///
/// Pure so the exact rule set is testable without a kernel; the installer
/// swaps `-C` for `-A` or `-I` as each one turns out to be missing.
pub(crate) fn sandbox_isolation_rules() -> Vec<Vec<String>> {
    let mut rules: Vec<Vec<String>> = GUEST_CORE_PORTS
        .iter()
        .map(|port| {
            [
                "-C",
                SANDBOX_ISOLATION_CHAIN,
                "-p",
                "tcp",
                "--dport",
                &port.to_string(),
                "-j",
                "REJECT",
                "--reject-with",
                "tcp-reset",
            ]
            .iter()
            .map(|value| (*value).to_owned())
            .collect()
        })
        .collect();
    rules.push(
        [
            "-C",
            "INPUT",
            "-i",
            SANDBOX_BRIDGE_INTERFACE,
            "-j",
            SANDBOX_ISOLATION_CHAIN,
        ]
        .iter()
        .map(|value| (*value).to_owned())
        .collect(),
    );
    rules
}

/// Make sure the isolation rules are in place. Idempotent and never flushes.
///
/// `iptables` answers `true` when the command succeeded. Check-then-add per
/// rule rather than flush-and-rebuild, because a flush opens a window with no
/// rule at all while a sandbox may already be running. The jump from `INPUT`
/// is inserted at the top, ahead of anything that might accept first.
///
/// Fails closed: a sandbox is not started on a guest that could not be
/// isolated. nerdctl programs the same `iptables` for every bridge container
/// it runs, so a guest where this cannot work cannot run sandboxes anyway.
pub(crate) fn ensure_sandbox_isolation(
    iptables: &dyn Fn(&[String]) -> Result<bool, String>,
) -> Result<(), GuestError> {
    // Creating a chain that already exists fails, and that is the common case.
    let _ = iptables(&["-N".into(), SANDBOX_ISOLATION_CHAIN.into()]);
    for rule in sandbox_isolation_rules() {
        let present = iptables(&rule).map_err(isolation_error)?;
        if present {
            continue;
        }
        let mut add = rule.clone();
        if rule[1] == "INPUT" {
            add.splice(0..2, ["-I".to_owned(), "INPUT".to_owned(), "1".to_owned()]);
        } else {
            add[0] = "-A".into();
        }
        if !iptables(&add).map_err(isolation_error)? {
            return Err(isolation_error(format!(
                "iptables refused `{}`",
                add.join(" ")
            )));
        }
    }
    Ok(())
}

fn isolation_error(detail: String) -> GuestError {
    GuestError {
        code: "sandbox_isolation_failed".into(),
        message: format!(
            "sandboxes could not be isolated from the guest's own services, so \
             none was started: {detail}"
        ),
        retryable: true,
        status_code: 503,
    }
}

/// The real `iptables`, waiting briefly for the xtables lock nerdctl's CNI
/// plugins also take when a container starts.
pub(crate) fn run_iptables(arguments: &[String]) -> Result<bool, String> {
    Command::new("iptables")
        .args(["-w", "5"])
        .args(arguments)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map(|status| status.success())
        .map_err(|error| format!("could not run iptables: {error}"))
}
