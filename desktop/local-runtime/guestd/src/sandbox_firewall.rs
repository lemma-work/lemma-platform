//! Keeping sandbox containers away from the guest's own services.
//!
//! PostgreSQL, Redis and SuperTokens run with host networking and listen on
//! every address in the guest -- that is how the host reaches them, over the
//! vsock proxies on macOS and the guest's address on WSL. A sandbox container
//! sits on nerdctl's default bridge, and a packet from it to *any* of the
//! guest's own addresses (the bridge gateway, the DHCP address) is delivered
//! locally: it traverses `INPUT` arriving on the bridge interface. So without
//! a rule, code running in any sandbox -- including one belonging to somebody
//! invited onto a shared installation -- could open the database
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
//! One rule for every sandbox, the paired user's included: the paired user's way onto the
//! Mac's loopback is the relay socket (`host_loopback`), never the gateway.
//! Because the rule is the same for every container it is keyed on the bridge
//! rather than on each container's address, which nerdctl assigns at run
//! time and reuses.
//!
//! The ports change when locald picks new ones, so the chain is named after
//! its contents (`LEMMA-HOST-<hash>`): a new set is built in full in a fresh
//! chain, jumped to from the top of `FORWARD` and of `CNI-ADMIN`, and only
//! then are the old chain's jumps and the old chain removed. There is never a moment with no
//! reject in place. The old one must go, not merely be shadowed: the new
//! chain *returns* for allowed traffic, and a packet returned to `FORWARD`
//! would meet the old chain next and be rejected there for a port that is no
//! longer the old one's.
//!
//! # Where the jumps live
//!
//! Top of `FORWARD` is not enough on its own. nerdctl's CNI firewall plugin
//! inserts `CNI-FORWARD` above it when the first container starts, and that
//! chain accepts everything a container sends. Its first rule is a jump to
//! `CNI-ADMIN`, which the plugin creates if missing and never touches
//! otherwise, so every forward jump of ours is in both (`FORWARD_HOOKS`).
//!
//! # Sandbox to sandbox, and IPv6
//!
//! `LEMMA-SANDBOX-PEERS` refuses new connections from the bridge to the
//! bridge, with `br_netfilter` loaded so traffic switched between two
//! containers is filtered at all. `ip6tables` drops everything arriving on the
//! bridge: nothing here uses IPv6, and link-local addresses would otherwise
//! walk around every IPv4 rule.

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

/// A second chain of ours: sandboxes opening connections to each other.
pub(crate) const SANDBOX_PEER_CHAIN: &str = "LEMMA-SANDBOX-PEERS";
/// The CNI firewall plugin's chain for administrators.
///
/// The plugin that nerdctl runs for every bridge container inserts its own
/// `CNI-FORWARD` chain at the top of `FORWARD` the first time a container
/// starts, and appends a per-container `-s <address> -j ACCEPT` to it -- so a
/// jump we put at the top of `FORWARD` before that first container was
/// pushed below an accept for everything the container sends, and every
/// `FORWARD` rule of ours stopped applying from the first sandbox on. The
/// plugin makes `CNI-ADMIN` the *first* rule of `CNI-FORWARD` and never
/// flushes or edits it: that is what the chain is for. So every forward
/// rule of ours is hooked there as well as at the top of `FORWARD`, and
/// holds whichever of the two a packet meets first.
pub(crate) const CNI_ADMIN_CHAIN: &str = "CNI-ADMIN";
/// Where a jump into one of our `FORWARD` chains is hooked, in check form's
/// chain position.
pub(crate) const FORWARD_HOOKS: [&str; 2] = ["FORWARD", CNI_ADMIN_CHAIN];

fn words(parts: &[&str]) -> Vec<String> {
    parts.iter().map(|part| (*part).to_owned()).collect()
}

/// Sandboxes may not open connections to one another.
///
/// Every sandbox sits on the one bridge, whoever it belongs to, and each one
/// publishes a runtime that takes instructions -- a function sandbox's
/// executes code. Nothing a sandbox does needs another sandbox: the backend
/// reaches them from the host through published ports, which arrives on the
/// guest's uplink rather than the bridge. So a *new* connection from the
/// bridge to the bridge is refused, whichever way it travels: bridged
/// directly between two containers (which only reaches `iptables` with
/// `br_netfilter`, see `ensure_bridge_netfilter`), or hairpinned through the
/// guest to another sandbox's published port.
pub(crate) fn sandbox_peer_rules() -> Vec<Vec<String>> {
    let mut rules = vec![
        words(&[
            "-C",
            SANDBOX_PEER_CHAIN,
            "-p",
            "tcp",
            "-m",
            "conntrack",
            "--ctstate",
            "NEW",
            "-j",
            "REJECT",
            "--reject-with",
            "tcp-reset",
        ]),
        words(&[
            "-C",
            SANDBOX_PEER_CHAIN,
            "-m",
            "conntrack",
            "--ctstate",
            "NEW",
            "-j",
            "REJECT",
            "--reject-with",
            "icmp-port-unreachable",
        ]),
    ];
    for hook in FORWARD_HOOKS {
        rules.push(words(&[
            "-C",
            hook,
            "-i",
            SANDBOX_BRIDGE_INTERFACE,
            "-o",
            SANDBOX_BRIDGE_INTERFACE,
            "-j",
            SANDBOX_PEER_CHAIN,
        ]));
    }
    rules
}

/// The IPv6 rules, for `ip6tables`, each in its `-C`heck form.
///
/// Nothing here uses IPv6: nerdctl's bridge is IPv4-only and the guest's
/// uplink takes no router advertisements. But the kernel still gives the
/// bridge and every container a link-local address, and PostgreSQL and
/// SuperTokens listen on every address -- `::` included -- so without these a
/// sandbox could dial the guest's core services, or another sandbox, at
/// `fe80::…%eth0` and pass none of the IPv4 rules above. Dropped rather than
/// rejected, because there is no IPv6 service here for a refusal to be
/// helpful about.
pub(crate) fn sandbox_ipv6_rules() -> Vec<Vec<String>> {
    ["INPUT", "FORWARD"]
        .iter()
        .map(|hook| words(&["-C", hook, "-i", SANDBOX_BRIDGE_INTERFACE, "-j", "DROP"]))
        .collect()
}

/// Add each `-C`heck-form rule that is missing: a jump from a hook chain at
/// the top of it, anything else at the end of its own chain.
fn install_rules(
    rules: Vec<Vec<String>>,
    iptables: &dyn Fn(&[String]) -> Result<bool, String>,
) -> Result<(), GuestError> {
    for rule in rules {
        let present = iptables(&rule).map_err(isolation_error)?;
        if present {
            continue;
        }
        let chain = rule[1].clone();
        let mut add = rule.clone();
        if ["INPUT", "FORWARD", CNI_ADMIN_CHAIN].contains(&chain.as_str()) {
            add.splice(0..2, ["-I".to_owned(), chain, "1".to_owned()]);
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

/// Make sure the isolation rules are in place. Idempotent and never flushes.
///
/// `iptables` answers `true` when the command succeeded. Check-then-add per
/// rule rather than flush-and-rebuild, because a flush opens a window with no
/// rule at all while a sandbox may already be running. Jumps are inserted at
/// the top of the chain they hook, ahead of anything that might accept first.
///
/// Fails closed: a sandbox is not started on a guest that could not be
/// isolated. nerdctl programs the same `iptables` for every bridge container
/// it runs, so a guest where this cannot work cannot run sandboxes anyway.
pub(crate) fn ensure_sandbox_isolation(
    iptables: &dyn Fn(&[String]) -> Result<bool, String>,
) -> Result<(), GuestError> {
    // Creating a chain that already exists fails, and that is the common case.
    // `CNI-ADMIN` is created here when no container has run yet, so the jumps
    // are in it before the plugin first puts it on the path; the plugin finds
    // it and leaves it alone.
    for chain in [SANDBOX_ISOLATION_CHAIN, SANDBOX_PEER_CHAIN, CNI_ADMIN_CHAIN] {
        let _ = iptables(&["-N".into(), chain.into()]);
    }
    let mut rules = sandbox_isolation_rules();
    rules.extend(sandbox_peer_rules());
    install_rules(rules, iptables)
}

/// The IPv6 half of `ensure_sandbox_isolation`, for `ip6tables`.
pub(crate) fn ensure_sandbox_ipv6_isolation(
    ip6tables: &dyn Fn(&[String]) -> Result<bool, String>,
) -> Result<(), GuestError> {
    install_rules(sandbox_ipv6_rules(), ip6tables)
}

/// Make traffic bridged between two containers visible to `iptables`.
///
/// Without `br_netfilter` a frame from one container to another on the same
/// bridge is switched at layer 2 and never meets `FORWARD`, so the peer rule
/// would only ever see the hairpinned route. `sysctl` is the root of
/// `/proc/sys`, passed in so this is testable; `load_module` loads
/// `br_netfilter` and is only called when its settings are missing. Fails
/// closed, like the rules it exists for.
pub(crate) fn ensure_bridge_netfilter(
    sysctl: &Path,
    load_module: &dyn Fn() -> bool,
) -> Result<(), GuestError> {
    let bridge = sysctl.join("net/bridge");
    let settings = ["bridge-nf-call-iptables", "bridge-nf-call-ip6tables"];
    if !bridge.join(settings[0]).exists() {
        let _ = load_module();
    }
    for setting in settings {
        let path = bridge.join(setting);
        let current = fs::read_to_string(&path).map_err(|error| {
            isolation_error(format!(
                "bridged traffic cannot be filtered ({}: {error})",
                path.display()
            ))
        })?;
        if current.trim() == "1" {
            continue;
        }
        fs::write(&path, "1").map_err(|error| {
            isolation_error(format!("could not set {}: {error}", path.display()))
        })?;
    }
    Ok(())
}

/// The real module load. `modprobe` is absent on WSL, whose kernel has
/// `br_netfilter` built in, and there the settings already exist.
pub(crate) fn load_br_netfilter() -> bool {
    Command::new("modprobe")
        .arg("br_netfilter")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok_and(|status| status.success())
}

impl<E: Engine + 'static> GuestService<E> {
    /// Every network rule a sandbox is started behind, installed before it
    /// starts.
    pub(crate) fn ensure_network_isolation(&self) -> Result<(), GuestError> {
        ensure_bridge_netfilter(Path::new("/proc/sys"), &load_br_netfilter)?;
        ensure_sandbox_isolation(&run_iptables)?;
        ensure_sandbox_ipv6_isolation(&run_ip6tables)?;
        ensure_host_gateway_isolation(
            &self.host_gateway,
            &self.callback_ports()?,
            &run_iptables,
            &list_iptables,
        )
    }
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

/// The jump from `hook` into `chain`, in its `-C`heck form.
fn host_gateway_jump(hook: &str, gateway: &str, chain: &str) -> Vec<String> {
    [
        "-C",
        hook,
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
    let jumps: Vec<Vec<String>> = FORWARD_HOOKS
        .iter()
        .map(|hook| host_gateway_jump(hook, gateway, &chain))
        .collect();
    let mut present = Vec::with_capacity(jumps.len());
    for jump in &jumps {
        present.push(iptables(jump).map_err(isolation_error)?);
    }
    if !present.contains(&true) {
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
    }
    for (jump, present) in jumps.iter().zip(present) {
        if present {
            continue;
        }
        if jump[1] == CNI_ADMIN_CHAIN {
            // Absent until the first container runs, unless we made it.
            let _ = iptables(&["-N".into(), CNI_ADMIN_CHAIN.into()]);
        }
        let mut insert = jump.clone();
        let hook = insert[1].clone();
        insert.splice(0..2, ["-I".to_owned(), hook, "1".to_owned()]);
        run(insert)?;
    }
    // Any other rule set's jumps go, then its chain.
    let mut stale = Vec::new();
    for hook in FORWARD_HOOKS {
        let listed = list(hook).map_err(isolation_error)?;
        for line in listed.lines() {
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
            if !stale.iter().any(|old: &String| old == target) {
                stale.push(target.to_owned());
            }
        }
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
    run_xtables("iptables", arguments)
}

/// The real `ip6tables`, likewise.
pub(crate) fn run_ip6tables(arguments: &[String]) -> Result<bool, String> {
    run_xtables("ip6tables", arguments)
}

fn run_xtables(binary: &str, arguments: &[String]) -> Result<bool, String> {
    Command::new(binary)
        .args(["-w", "5"])
        .args(arguments)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map(|status| status.success())
        .map_err(|error| format!("could not run {binary}: {error}"))
}
