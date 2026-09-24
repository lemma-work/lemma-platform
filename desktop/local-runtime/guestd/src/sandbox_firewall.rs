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
//!
//! # The host gateway
//!
//! The second set of rules keeps sandboxes away from the *Mac*. The host
//! gateway (`host.lemma.internal`) is the Mac's own address on the VM's
//! network, so a container dialling it reaches every Mac service listening on
//! all interfaces -- an invited person's sandbox included. What a sandbox
//! actually needs there is two ports: the backend's and the frontend's
//! callback forwarders, which locald binds on that address and names in
//! `core.*` (`callback_ports`). DNS is the gateway too, on vmnet. So traffic
//! from the sandbox bridge to the gateway passes a chain that returns for
//! replies, those ports and DNS, and rejects the rest.
//!
//! One rule for every sandbox, the owner's included: the owner's way onto the
//! Mac's loopback is the relay socket (`host_loopback`), never the gateway.
//! Because the rule is the same for every container it is keyed on the bridge
//! rather than on each container's address, which nerdctl assigns at run
//! time and reuses.
//!
//! The ports change when locald picks new ones, so the chain is named after
//! its contents (`LEMMA-HOST-<hash>`): a new set is built in full in a fresh
//! chain, jumped to from the top of `FORWARD`, and only then are the old
//! chain's jump and the old chain removed. There is never a moment with no
//! reject in place. The old one must go, not merely be shadowed: the new
//! chain *returns* for allowed traffic, and a packet returned to `FORWARD`
//! would meet the old chain next and be rejected there for a port that is no
//! longer the old one's.

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

/// Prefix of the host-gateway chains; the rest is a hash of the contents.
pub(crate) const HOST_GATEWAY_CHAIN_PREFIX: &str = "LEMMA-HOST-";
/// The gateway's DNS, which vmnet serves and containers are given.
const DNS_PORT: u16 = 53;

/// The chain for this gateway and these callback ports.
///
/// FNV-1a over the rule text, so the name is stable across builds and any
/// change to the rules -- not just the ports -- names a new chain.
pub(crate) fn host_gateway_chain(gateway: &str, callback_ports: &[u16]) -> String {
    let mut text = String::from(gateway);
    for rule in host_gateway_chain_rules(callback_ports) {
        text.push('\n');
        text.push_str(&rule.join(" "));
    }
    let hash = text.bytes().fold(0x811c_9dc5_u32, |hash, byte| {
        (hash ^ u32::from(byte)).wrapping_mul(0x0100_0193)
    });
    format!("{HOST_GATEWAY_CHAIN_PREFIX}{hash:08x}")
}

/// The chain's rules in order, each without its `-A <chain>`.
///
/// Pure, like `sandbox_isolation_rules`, so the exact set is testable.
pub(crate) fn host_gateway_chain_rules(callback_ports: &[u16]) -> Vec<Vec<String>> {
    fn rule(parts: &[&str]) -> Vec<String> {
        parts.iter().map(|part| (*part).to_owned()).collect()
    }
    let mut ports: Vec<u16> = callback_ports.to_vec();
    ports.sort_unstable();
    ports.dedup();
    // Replies first: the Mac opening a connection *to* a sandbox (a published
    // port) must still get its answers back.
    let mut rules = vec![rule(&[
        "-m",
        "conntrack",
        "--ctstate",
        "ESTABLISHED,RELATED",
        "-j",
        "RETURN",
    ])];
    for port in ports {
        let port = port.to_string();
        rules.push(rule(&["-p", "tcp", "--dport", &port, "-j", "RETURN"]));
    }
    let dns = DNS_PORT.to_string();
    for protocol in ["udp", "tcp"] {
        rules.push(rule(&["-p", protocol, "--dport", &dns, "-j", "RETURN"]));
    }
    rules.push(rule(&[
        "-p",
        "tcp",
        "-j",
        "REJECT",
        "--reject-with",
        "tcp-reset",
    ]));
    rules.push(rule(&[
        "-j",
        "REJECT",
        "--reject-with",
        "icmp-port-unreachable",
    ]));
    rules
}

/// The jump from `FORWARD` into `chain`, in its `-C`heck form.
fn host_gateway_jump(gateway: &str, chain: &str) -> Vec<String> {
    [
        "-C",
        "FORWARD",
        "-i",
        SANDBOX_BRIDGE_INTERFACE,
        "-d",
        gateway,
        "-j",
        chain,
    ]
    .iter()
    .map(|value| (*value).to_owned())
    .collect()
}

/// Make sure sandboxes reach the host gateway only on `callback_ports` (and
/// DNS). Idempotent: a guest already carrying this exact rule set does one
/// `-C` and nothing else.
///
/// `list` is `iptables -S <chain>`'s output, used only to find a previous
/// rule set's jump to remove. Fails closed, like `ensure_sandbox_isolation`.
pub(crate) fn ensure_host_gateway_isolation(
    gateway: &str,
    callback_ports: &[u16],
    iptables: &dyn Fn(&[String]) -> Result<bool, String>,
    list: &dyn Fn(&str) -> Result<String, String>,
) -> Result<(), GuestError> {
    let chain = host_gateway_chain(gateway, callback_ports);
    let jump = host_gateway_jump(gateway, &chain);
    let run = |arguments: Vec<String>| -> Result<(), GuestError> {
        if iptables(&arguments).map_err(isolation_error)? {
            Ok(())
        } else {
            Err(isolation_error(format!(
                "iptables refused `{}`",
                arguments.join(" ")
            )))
        }
    };
    if !iptables(&jump).map_err(isolation_error)? {
        // Built in full before anything jumps to it. `-N` fails when a
        // previous attempt left the chain behind, and the flush makes that
        // leftover whatever it was into an empty chain again -- safe, since
        // nothing jumps to it yet.
        let _ = iptables(&["-N".into(), chain.clone()]);
        run(vec!["-F".into(), chain.clone()])?;
        for rule in host_gateway_chain_rules(callback_ports) {
            let mut add = vec!["-A".to_owned(), chain.clone()];
            add.extend(rule);
            run(add)?;
        }
        let mut insert = jump.clone();
        insert.splice(
            0..2,
            ["-I".to_owned(), "FORWARD".to_owned(), "1".to_owned()],
        );
        run(insert)?;
    }
    // Any other rule set's jump goes, then its chain.
    let forward = list("FORWARD").map_err(isolation_error)?;
    let mut stale = Vec::new();
    for line in forward.lines() {
        let words: Vec<&str> = line.split_whitespace().collect();
        let target = words
            .windows(2)
            .find(|pair| pair[0] == "-j")
            .map(|pair| pair[1]);
        let Some(target) = target else { continue };
        if words.first() != Some(&"-A")
            || !target.starts_with(HOST_GATEWAY_CHAIN_PREFIX)
            || target == chain
        {
            continue;
        }
        let mut delete: Vec<String> = words.iter().map(|word| (*word).to_owned()).collect();
        delete[0] = "-D".into();
        run(delete)?;
        stale.push(target.to_owned());
    }
    for old in stale {
        run(vec!["-F".into(), old.clone()])?;
        // Deleting can race a second jump that no longer exists; a chain left
        // empty and unreferenced is harmless, so this one is not fatal.
        let _ = iptables(&["-X".into(), old]);
    }
    Ok(())
}

/// The real `iptables -S <chain>`.
pub(crate) fn list_iptables(chain: &str) -> Result<String, String> {
    let output = Command::new("iptables")
        .args(["-w", "5", "-S", chain])
        .stderr(Stdio::null())
        .output()
        .map_err(|error| format!("could not run iptables: {error}"))?;
    if !output.status.success() {
        return Err(format!("iptables could not list {chain}"));
    }
    Ok(String::from_utf8_lossy(&output.stdout).into_owned())
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
