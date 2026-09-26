//! A model of `iptables`'s filter table for the firewall tests: the verbs the
//! installers use, what nerdctl's CNI firewall plugin inserts when a container
//! starts, and a packet walker that says where a packet ends up.

use super::*;
use std::collections::HashMap;

/// `iptables`'s filter table, as far as the installers and nerdctl's CNI
/// firewall plugin use it, with a packet walker to say what a rule set does.
pub(super) struct FakeTables {
    pub(super) chains: HashMap<String, Vec<Vec<String>>>,
    pub(super) calls: Vec<String>,
    /// Checked after every call: once a jump is in place, one always is.
    pub(super) ever_guarded: bool,
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
pub(super) struct Packet<'a> {
    pub(super) input: &'a str,
    pub(super) output: &'a str,
    pub(super) source: &'a str,
    pub(super) destination: &'a str,
    pub(super) protocol: &'a str,
    pub(super) port: u16,
    pub(super) state: &'a str,
}

#[derive(Debug, PartialEq)]
pub(super) enum Verdict {
    Accept,
    Reject,
}

impl FakeTables {
    pub(super) fn jumps_from(&self, chain: &str) -> Vec<String> {
        self.chains
            .get(chain)
            .into_iter()
            .flatten()
            .filter_map(|rule| rule.last().cloned())
            .filter(|target| target.starts_with(HOST_GATEWAY_CHAIN_PREFIX))
            .collect()
    }

    pub(super) fn jumps(&self) -> Vec<String> {
        self.jumps_from("FORWARD")
    }

    pub(super) fn apply(&mut self, arguments: &[String]) -> bool {
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

    pub(super) fn list(&self, chain: &str) -> String {
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
    pub(super) fn cni_container_started(&mut self, address: &str) {
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
    pub(super) fn forward(&self, packet: &Packet) -> Verdict {
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
