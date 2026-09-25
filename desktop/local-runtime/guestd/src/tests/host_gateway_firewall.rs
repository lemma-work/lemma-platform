//! Sandboxes reach the Mac only on the callback ports: the rules, the
//! installer that swaps them in without a gap, and where the ports come from.

use super::*;
use std::cell::RefCell;
use std::collections::HashMap;

const GATEWAY: &str = "192.168.64.1";

/// `iptables`'s filter table, as far as the installers and nerdctl's CNI
/// firewall plugin use it, with a packet walker to say what a rule set does.
struct FakeTables {
    chains: HashMap<String, Vec<Vec<String>>>,
    calls: Vec<String>,
    /// Checked after every call: once a jump is in place, one always is.
    ever_guarded: bool,
}

impl Default for FakeTables {
    fn default() -> Self {
        let mut chains = HashMap::new();
        for builtin in ["INPUT", "FORWARD", "OUTPUT"] {
            chains.insert(builtin.to_owned(), Vec::new());
        }
        Self {
            chains,
            calls: Vec::new(),
            ever_guarded: false,
        }
    }
}

/// One packet, as the filter table sees it.
struct Packet<'a> {
    input: &'a str,
    output: &'a str,
    source: &'a str,
    destination: &'a str,
    protocol: &'a str,
    port: u16,
    state: &'a str,
}

#[derive(Debug, PartialEq)]
enum Verdict {
    Accept,
    Reject,
}

impl FakeTables {
    fn jumps_from(&self, chain: &str) -> Vec<String> {
        self.chains
            .get(chain)
            .into_iter()
            .flatten()
            .filter_map(|rule| rule.last().cloned())
            .filter(|target| target.starts_with(HOST_GATEWAY_CHAIN_PREFIX))
            .collect()
    }

    fn jumps(&self) -> Vec<String> {
        self.jumps_from("FORWARD")
    }

    fn apply(&mut self, arguments: &[String]) -> bool {
        self.calls.push(arguments.join(" "));
        let rest = |from: usize| arguments[from..].to_vec();
        let done = match arguments[0].as_str() {
            "-N" => {
                if self.chains.contains_key(&arguments[1]) {
                    false
                } else {
                    self.chains.insert(arguments[1].clone(), Vec::new());
                    true
                }
            }
            "-F" => match self.chains.get_mut(&arguments[1]) {
                Some(rules) => {
                    rules.clear();
                    true
                }
                None => false,
            },
            "-X" => {
                let referenced = self
                    .chains
                    .values()
                    .flatten()
                    .any(|rule| rule.last() == Some(&arguments[1]));
                !referenced && self.chains.remove(&arguments[1]).is_some()
            }
            "-A" => match self.chains.get_mut(&arguments[1]) {
                Some(rules) => {
                    rules.push(rest(2));
                    true
                }
                None => false,
            },
            "-I" => {
                assert_eq!(arguments[2], "1", "the jump goes at the top");
                assert!(
                    self.chains.contains_key(arguments.last().unwrap()),
                    "jumped to a chain that does not exist"
                );
                match self.chains.get_mut(&arguments[1]) {
                    Some(rules) => {
                        rules.insert(0, rest(3));
                        true
                    }
                    None => false,
                }
            }
            "-C" => self
                .chains
                .get(&arguments[1])
                .is_some_and(|rules| rules.contains(&rest(2))),
            "-D" => match self.chains.get_mut(&arguments[1]) {
                Some(rules) => {
                    let rule = rest(2);
                    let before = rules.len();
                    rules.retain(|existing| *existing != rule);
                    before != rules.len()
                }
                None => false,
            },
            other => panic!("unexpected iptables verb {other}"),
        };
        if !self.jumps().is_empty() {
            self.ever_guarded = true;
        } else {
            assert!(!self.ever_guarded, "a moment with no reject in place");
        }
        done
    }

    fn list(&self, chain: &str) -> String {
        self.chains
            .get(chain)
            .into_iter()
            .flatten()
            .map(|rule| format!("-A {chain} {}\n", rule.join(" ")))
            .collect()
    }

    /// What nerdctl's CNI `firewall` plugin does when a container starts:
    /// make its chains if they are missing (never flushing `CNI-ADMIN`), put
    /// `CNI-FORWARD` first in `FORWARD` and `CNI-ADMIN` first in it, and
    /// accept everything the new container sends.
    fn cni_container_started(&mut self, address: &str) {
        fn rule(parts: &[&str]) -> Vec<String> {
            parts.iter().map(|part| (*part).to_owned()).collect()
        }
        for chain in ["CNI-FORWARD", "CNI-ADMIN"] {
            self.chains.entry(chain.to_owned()).or_default();
        }
        let entry = rule(&[
            "-m",
            "comment",
            "--comment",
            "CNI firewall plugin rules",
            "-j",
            "CNI-FORWARD",
        ]);
        let forward = self.chains.get_mut("FORWARD").unwrap();
        if !forward.contains(&entry) {
            forward.insert(0, entry);
        }
        let admin = rule(&[
            "-m",
            "comment",
            "--comment",
            "CNI firewall plugin admin overrides",
            "-j",
            "CNI-ADMIN",
        ]);
        let private = self.chains.get_mut("CNI-FORWARD").unwrap();
        if !private.contains(&admin) {
            private.insert(0, admin);
        }
        private.push(rule(&[
            "-d",
            address,
            "-m",
            "conntrack",
            "--ctstate",
            "RELATED,ESTABLISHED",
            "-j",
            "ACCEPT",
        ]));
        private.push(rule(&["-s", address, "-j", "ACCEPT"]));
    }

    /// Where `packet` ends up, walking `FORWARD` the way the kernel would.
    fn forward(&self, packet: &Packet) -> Verdict {
        self.walk("FORWARD", packet, 0).unwrap_or(Verdict::Accept)
    }

    fn walk(&self, chain: &str, packet: &Packet, depth: usize) -> Option<Verdict> {
        assert!(depth < 16, "a jump loop");
        for rule in &self.chains[chain] {
            if !matches(rule, packet) {
                continue;
            }
            let target = rule
                .windows(2)
                .find(|pair| pair[0] == "-j")
                .map(|pair| pair[1].as_str())
                .expect("every rule has a target");
            match target {
                "ACCEPT" => return Some(Verdict::Accept),
                "REJECT" | "DROP" => return Some(Verdict::Reject),
                "RETURN" => return None,
                other => {
                    if let Some(verdict) = self.walk(other, packet, depth + 1) {
                        return Some(verdict);
                    }
                }
            }
        }
        None
    }
}

fn matches(rule: &[String], packet: &Packet) -> bool {
    let mut index = 0;
    while index < rule.len() {
        let value = rule.get(index + 1).map(String::as_str).unwrap_or("");
        let holds = match rule[index].as_str() {
            "-j" => return true,
            "-m" | "--comment" | "--reject-with" => true,
            "-i" => value == packet.input,
            "-o" => value == packet.output,
            "-s" => value == packet.source,
            "-d" => value == packet.destination,
            "-p" => value == packet.protocol,
            "--dport" => value == packet.port.to_string(),
            "--ctstate" => value.split(',').any(|state| state == packet.state),
            other => panic!("the model does not know {other}"),
        };
        if !holds {
            return false;
        }
        index += 2;
    }
    true
}

fn install(tables: &RefCell<FakeTables>, ports: &[u16]) -> Result<(), GuestError> {
    ensure_host_gateway_isolation(
        GATEWAY,
        ports,
        &|arguments| Ok(tables.borrow_mut().apply(arguments)),
        &|chain| Ok(tables.borrow().list(chain)),
    )
}

/// Everything `sandbox.ensure` installs before a sandbox starts.
fn install_all(tables: &RefCell<FakeTables>, ports: &[u16]) {
    ensure_sandbox_isolation(&|arguments| Ok(tables.borrow_mut().apply(arguments))).unwrap();
    install(tables, ports).unwrap();
}

const FIRST_SANDBOX: &str = "10.4.0.5";
const SECOND_SANDBOX: &str = "10.4.0.6";

fn from_sandbox<'a>(destination: &'a str, output: &'a str, port: u16) -> Packet<'a> {
    Packet {
        input: SANDBOX_BRIDGE_INTERFACE,
        output,
        source: FIRST_SANDBOX,
        destination,
        protocol: "tcp",
        port,
        state: "NEW",
    }
}

/// The guard survives nerdctl's own firewall plugin, which inserts
/// `CNI-FORWARD` above it on the first container and accepts everything
/// each container sends. It holds for the first sandbox, for every one after
/// it, and when the rules are re-checked after containers already run.
#[test]
fn the_host_gateway_guard_still_applies_after_cni_programs_its_own_chains() {
    let tables = RefCell::new(FakeTables::default());
    install_all(&tables, &[8711, 3711]);
    tables.borrow_mut().cni_container_started(FIRST_SANDBOX);
    tables.borrow_mut().cni_container_started(SECOND_SANDBOX);

    let check = |tables: &FakeTables| {
        assert_eq!(
            tables.forward(&from_sandbox(GATEWAY, "vmnet0", 22)),
            Verdict::Reject,
            "a sandbox reached the Mac's ssh past CNI's accept"
        );
        assert_eq!(
            tables.forward(&from_sandbox(GATEWAY, "vmnet0", 8711)),
            Verdict::Accept,
            "the backend's callback port must stay open"
        );
        let mut dns = from_sandbox(GATEWAY, "vmnet0", 53);
        dns.protocol = "udp";
        assert_eq!(tables.forward(&dns), Verdict::Accept);
        assert_eq!(
            tables.forward(&from_sandbox("1.1.1.1", "vmnet0", 443)),
            Verdict::Accept,
            "the internet is not the gateway"
        );
    };
    check(&tables.borrow());

    // guestd re-checks on the next sandbox's ensure; nothing moves.
    install_all(&tables, &[8711, 3711]);
    tables.borrow_mut().cni_container_started("10.4.0.7");
    check(&tables.borrow());

    // New ports once containers already run: the replacement lands in
    // CNI-ADMIN too, and the old chain's jump goes from both.
    install(&tables, &[9000, 3000]).unwrap();
    let tables = tables.borrow();
    assert_eq!(
        tables.forward(&from_sandbox(GATEWAY, "vmnet0", 8711)),
        Verdict::Reject
    );
    assert_eq!(
        tables.forward(&from_sandbox(GATEWAY, "vmnet0", 9000)),
        Verdict::Accept
    );
    let new = host_gateway_chain(GATEWAY, &[9000, 3000]);
    assert_eq!(tables.jumps_from("CNI-ADMIN"), vec![new.clone()]);
    assert_eq!(tables.jumps_from("FORWARD"), vec![new]);
}

/// One sandbox cannot open a connection to another -- a function sandbox's
/// runtime executes what it is sent -- whichever way the packet goes:
/// switched across the bridge, or hairpinned to a published port.
#[test]
fn a_sandbox_cannot_open_a_connection_to_another_sandbox() {
    let tables = RefCell::new(FakeTables::default());
    install_all(&tables, &[8711, 3711]);
    tables.borrow_mut().cni_container_started(FIRST_SANDBOX);
    tables.borrow_mut().cni_container_started(SECOND_SANDBOX);
    let tables = tables.borrow();

    for port in [8090, 8080, 4850] {
        assert_eq!(
            tables.forward(&from_sandbox(
                SECOND_SANDBOX,
                SANDBOX_BRIDGE_INTERFACE,
                port
            )),
            Verdict::Reject,
            "sandbox to sandbox on {port}"
        );
    }
    let mut udp = from_sandbox(SECOND_SANDBOX, SANDBOX_BRIDGE_INTERFACE, 5353);
    udp.protocol = "udp";
    assert_eq!(tables.forward(&udp), Verdict::Reject);

    // The backend's way in -- the host, through a published port, arriving
    // on the uplink -- is untouched, and so are the replies.
    let from_host = Packet {
        input: "enp0s1",
        output: SANDBOX_BRIDGE_INTERFACE,
        source: GATEWAY,
        destination: SECOND_SANDBOX,
        protocol: "tcp",
        port: 8090,
        state: "NEW",
    };
    assert_eq!(tables.forward(&from_host), Verdict::Accept);
    let reply = Packet {
        input: SANDBOX_BRIDGE_INTERFACE,
        output: "enp0s1",
        source: SECOND_SANDBOX,
        destination: GATEWAY,
        protocol: "tcp",
        port: 50000,
        state: "ESTABLISHED",
    };
    assert_eq!(tables.forward(&reply), Verdict::Accept);
}

/// Bridged frames only reach `FORWARD` with `br_netfilter`, so the peer rule
/// is only real with it: loaded when missing, switched on, and a guest where
/// it cannot be starts no sandbox.
#[test]
fn bridged_traffic_is_made_visible_to_the_firewall_or_nothing_starts() {
    let root = tempdir().unwrap();
    let bridge = root.path().join("net/bridge");
    let loaded = std::cell::Cell::new(false);
    let load = || {
        loaded.set(true);
        std::fs::create_dir_all(&bridge).unwrap();
        std::fs::write(bridge.join("bridge-nf-call-iptables"), "0\n").unwrap();
        std::fs::write(bridge.join("bridge-nf-call-ip6tables"), "0\n").unwrap();
        true
    };
    ensure_bridge_netfilter(root.path(), &load).unwrap();
    assert!(loaded.get(), "the module was not loaded");
    for setting in ["bridge-nf-call-iptables", "bridge-nf-call-ip6tables"] {
        assert_eq!(
            std::fs::read_to_string(bridge.join(setting))
                .unwrap()
                .trim(),
            "1"
        );
    }

    let missing = tempdir().unwrap();
    let error = ensure_bridge_netfilter(missing.path(), &|| false).unwrap_err();
    assert_eq!(error.code, "sandbox_isolation_failed");
}

/// Link-local IPv6 would walk around every IPv4 rule: nothing arriving on
/// the bridge over IPv6 is delivered or forwarded.
#[test]
fn ipv6_from_the_sandbox_bridge_is_dropped() {
    let installed: RefCell<Vec<Vec<String>>> = RefCell::new(Vec::new());
    let ip6tables = |arguments: &[String]| -> Result<bool, String> {
        match arguments[0].as_str() {
            "-C" => Ok(installed
                .borrow()
                .iter()
                .any(|rule| rule[..] == arguments[1..])),
            "-I" => {
                assert_eq!(arguments[2], "1");
                let mut rule = vec![arguments[1].clone()];
                rule.extend_from_slice(&arguments[3..]);
                installed.borrow_mut().push(rule);
                Ok(true)
            }
            other => panic!("unexpected ip6tables verb {other}"),
        }
    };
    ensure_sandbox_ipv6_isolation(&ip6tables).unwrap();
    ensure_sandbox_ipv6_isolation(&ip6tables).unwrap();
    let rules: Vec<String> = installed
        .borrow()
        .iter()
        .map(|rule| rule.join(" "))
        .collect();
    assert_eq!(
        rules,
        ["INPUT -i nerdctl0 -j DROP", "FORWARD -i nerdctl0 -j DROP"]
    );
}

#[test]
fn a_sandbox_reaches_the_gateway_only_on_the_callback_ports_and_dns() {
    let rules: Vec<String> = host_gateway_chain_rules(&[8711, 3711, 8711])
        .iter()
        .map(|rule| rule.join(" "))
        .collect();
    assert_eq!(
        rules,
        [
            "-m conntrack --ctstate ESTABLISHED,RELATED -j RETURN",
            "-p tcp --dport 3711 -j RETURN",
            "-p tcp --dport 8711 -j RETURN",
            "-p udp --dport 53 -j RETURN",
            "-p tcp --dport 53 -j RETURN",
            "-p tcp -j REJECT --reject-with tcp-reset",
            "-j REJECT --reject-with icmp-port-unreachable",
        ]
    );
}

#[test]
fn with_no_callback_ports_only_replies_and_dns_pass() {
    let returns: Vec<String> = host_gateway_chain_rules(&[])
        .iter()
        .filter(|rule| rule.ends_with(&["RETURN".to_owned()]))
        .map(|rule| rule.join(" "))
        .collect();
    assert_eq!(returns.len(), 3, "{returns:?}");
    assert!(returns
        .iter()
        .all(|rule| rule.contains("conntrack") || rule.contains("--dport 53")));
}

#[test]
fn the_chain_is_named_after_its_contents() {
    let chain = host_gateway_chain(GATEWAY, &[8711, 3711]);
    assert!(chain.starts_with(HOST_GATEWAY_CHAIN_PREFIX));
    assert!(
        chain.len() <= 28,
        "iptables caps chain names at 28: {chain}"
    );
    assert_eq!(chain, host_gateway_chain(GATEWAY, &[3711, 8711]));
    assert_ne!(chain, host_gateway_chain(GATEWAY, &[8712, 3711]));
    assert_ne!(chain, host_gateway_chain("192.168.65.1", &[8711, 3711]));
}

#[test]
fn the_rules_are_installed_once_and_jumped_to_from_the_sandbox_bridge() {
    let tables = RefCell::new(FakeTables::default());
    install(&tables, &[8711, 3711]).unwrap();
    let chain = host_gateway_chain(GATEWAY, &[8711, 3711]);
    {
        let tables = tables.borrow();
        let jump: Vec<String> = ["-i", "nerdctl0", "-d", GATEWAY, "-j", &chain]
            .iter()
            .map(|part| (*part).to_owned())
            .collect();
        assert_eq!(tables.chains["FORWARD"], vec![jump.clone()]);
        assert_eq!(tables.chains["CNI-ADMIN"], vec![jump]);
        assert_eq!(
            tables.chains[&chain],
            host_gateway_chain_rules(&[8711, 3711])
        );
    }

    tables.borrow_mut().calls.clear();
    install(&tables, &[8711, 3711]).unwrap();
    let calls = tables.borrow().calls.clone();
    assert_eq!(
        calls,
        [
            format!("-C FORWARD -i nerdctl0 -d {GATEWAY} -j {chain}"),
            format!("-C CNI-ADMIN -i nerdctl0 -d {GATEWAY} -j {chain}"),
        ],
        "an unchanged rule set is checked, not rebuilt"
    );
}

/// New ports: a new chain is in place before the old one's jump goes, and the
/// old one does go -- left behind, it would reject what the new one returns.
#[test]
fn new_callback_ports_replace_the_old_rules_without_a_gap() {
    let tables = RefCell::new(FakeTables::default());
    install(&tables, &[8711, 3711]).unwrap();
    let old = host_gateway_chain(GATEWAY, &[8711, 3711]);
    install(&tables, &[9000, 3000]).unwrap();
    let new = host_gateway_chain(GATEWAY, &[9000, 3000]);

    let tables = tables.borrow();
    assert_eq!(tables.jumps(), vec![new.clone()]);
    assert!(
        !tables.chains.contains_key(&old),
        "the old chain outlived its jump"
    );
    assert_eq!(tables.chains[&new], host_gateway_chain_rules(&[9000, 3000]));
}

/// A chain a crashed attempt left half-built, not yet jumped to, is emptied
/// and rebuilt rather than trusted.
#[test]
fn a_half_built_chain_from_a_previous_attempt_is_rebuilt() {
    let tables = RefCell::new(FakeTables::default());
    let chain = host_gateway_chain(GATEWAY, &[8711, 3711]);
    tables
        .borrow_mut()
        .chains
        .insert(chain.clone(), vec![vec!["-j".into(), "RETURN".into()]]);
    install(&tables, &[8711, 3711]).unwrap();
    assert_eq!(
        tables.borrow().chains[&chain],
        host_gateway_chain_rules(&[8711, 3711])
    );
}

#[test]
fn a_gateway_that_cannot_be_guarded_starts_no_sandbox() {
    let error = ensure_host_gateway_isolation(
        GATEWAY,
        &[8711],
        &|arguments| Ok(arguments[0] == "-N" || arguments[0] == "-F"),
        &|_| Ok(String::new()),
    )
    .unwrap_err();
    assert_eq!(error.code, "sandbox_isolation_failed");
    assert!(error.retryable);

    let error =
        ensure_host_gateway_isolation(GATEWAY, &[8711], &|_| Err("no iptables".into()), &|_| {
            Ok(String::new())
        })
        .unwrap_err();
    assert_eq!(error.code, "sandbox_isolation_failed");
}

fn service(root: &TempDir) -> GuestService<FakeEngine> {
    GuestService::new(
        FakeEngine::new(vec![]),
        root.path().into(),
        Some("192.168.64.2".into()),
        GATEWAY.into(),
        None,
    )
    .unwrap()
}

#[test]
fn the_callback_ports_arrive_with_core_and_outlive_a_guestd_restart() {
    let root = tempdir().unwrap();
    let error = service(&root).callback_ports().unwrap_err();
    assert_eq!(
        error.code, "sandbox_isolation_failed",
        "none recorded is not 'none allowed'"
    );
    assert!(error.retryable);

    service(&root)
        .record_callback_ports(&core_parameters("docker.io/postgres:17"))
        .unwrap();
    // A fresh service over the same state, as after a restart.
    assert_eq!(service(&root).callback_ports().unwrap(), vec![8711, 3711]);

    // An older locald sends none, which leaves the recorded ones alone.
    let mut older = core_parameters("docker.io/postgres:17");
    older.callback_ports.clear();
    service(&root).record_callback_ports(&older).unwrap();
    assert_eq!(service(&root).callback_ports().unwrap(), vec![8711, 3711]);
}

#[test]
fn callback_ports_are_bounded_and_never_zero() {
    let root = tempdir().unwrap();
    let service = service(&root);
    let base = json!({
        "images": {"postgres": "pg@sha256:test", "redis": "redis@sha256:test",
            "supertokens": "auth@sha256:test"},
        "credentials": {"postgres_password": "a".repeat(64), "redis_password": "b".repeat(64)},
    });
    let with = |ports: Value| {
        let mut value = base.clone();
        value["callback_ports"] = ports;
        service.parse_core_parameters(value)
    };
    assert_eq!(
        with(json!([8711, 3711])).unwrap().callback_ports,
        vec![8711, 3711]
    );
    assert!(service
        .parse_core_parameters(base.clone())
        .unwrap()
        .callback_ports
        .is_empty());
    assert!(with(json!([0])).is_err());
    assert!(with(json!([1, 2, 3, 4, 5, 6, 7, 8, 9])).is_err());
    assert!(with(json!([70000])).is_err());
}
