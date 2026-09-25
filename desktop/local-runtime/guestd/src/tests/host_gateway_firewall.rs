//! Sandboxes reach the Mac only on the callback ports: the rules, the
//! installer that swaps them in without a gap, and where the ports come from.

use super::*;
use std::cell::RefCell;
use std::collections::HashMap;

const GATEWAY: &str = "192.168.64.1";

/// `iptables` as far as the installer uses it: `FORWARD` and our chains.
#[derive(Default)]
struct FakeTables {
    forward: Vec<Vec<String>>,
    chains: HashMap<String, Vec<Vec<String>>>,
    calls: Vec<String>,
    /// Checked after every call: once a jump is in place, one always is.
    ever_guarded: bool,
}

impl FakeTables {
    fn jumps(&self) -> Vec<String> {
        self.forward
            .iter()
            .filter_map(|rule| rule.last().cloned())
            .filter(|target| target.starts_with(HOST_GATEWAY_CHAIN_PREFIX))
            .collect()
    }

    fn apply(&mut self, arguments: &[String]) -> bool {
        self.calls.push(arguments.join(" "));
        let rest = |from: usize| arguments[from..].to_vec();
        let done = match arguments[0].as_str() {
            "-N" => self
                .chains
                .insert(arguments[1].clone(), Vec::new())
                .is_none(),
            "-F" => match self.chains.get_mut(&arguments[1]) {
                Some(rules) => {
                    rules.clear();
                    true
                }
                None => false,
            },
            "-X" => self.chains.remove(&arguments[1]).is_some(),
            "-A" => match self.chains.get_mut(&arguments[1]) {
                Some(rules) => {
                    rules.push(rest(2));
                    true
                }
                None => false,
            },
            "-I" => {
                assert_eq!(arguments[1], "FORWARD");
                assert_eq!(arguments[2], "1", "the jump goes at the top");
                assert!(
                    self.chains.contains_key(arguments.last().unwrap()),
                    "jumped to a chain that does not exist"
                );
                self.forward.insert(0, rest(3));
                true
            }
            "-C" => self.forward.contains(&rest(2)),
            "-D" => {
                let rule = rest(2);
                let before = self.forward.len();
                self.forward.retain(|existing| *existing != rule);
                before != self.forward.len()
            }
            other => panic!("unexpected iptables verb {other}"),
        };
        if !self.jumps().is_empty() {
            self.ever_guarded = true;
        } else {
            assert!(!self.ever_guarded, "a moment with no reject in place");
        }
        done
    }

    fn list_forward(&self) -> String {
        self.forward
            .iter()
            .map(|rule| format!("-A FORWARD {}\n", rule.join(" ")))
            .collect()
    }
}

fn install(tables: &RefCell<FakeTables>, ports: &[u16]) -> Result<(), GuestError> {
    ensure_host_gateway_isolation(
        GATEWAY,
        ports,
        &|arguments| Ok(tables.borrow_mut().apply(arguments)),
        &|chain| {
            assert_eq!(chain, "FORWARD");
            Ok(tables.borrow().list_forward())
        },
    )
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
        assert_eq!(
            tables.forward,
            vec![["-i", "nerdctl0", "-d", GATEWAY, "-j", &chain]
                .iter()
                .map(|part| (*part).to_owned())
                .collect::<Vec<_>>()]
        );
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
        [format!("-C FORWARD -i nerdctl0 -d {GATEWAY} -j {chain}")],
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
