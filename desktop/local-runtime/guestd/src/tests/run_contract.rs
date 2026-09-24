//! What a sandbox is actually run with.

use super::*;

#[test]
fn run_contract_uses_digest_env_file_private_gateway_and_all_app_ports() {
    let parameters = EnsureParameters {
        sandbox_id: "box-1".into(),
        workload_kind: WorkloadKind::Workspace,
        image: "ghcr.io/lemma/workspace@sha256:abc".into(),
        env: BTreeMap::from([("LEMMA_TOKEN".into(), "secret".into())]),
        metadata: BTreeMap::from([("managed-by".into(), "lemma-workspace".into())]),
        runtime_token: Some("runtime-secret".into()),
        apps: workspace_apps(),
        resources: ResourceSpec {
            memory: Some("2Gi".into()),
            cpus: Some("1".into()),
        },
        callback: CallbackSpec::default(),
        host_access: true,
    };
    let arguments = build_run_arguments(
        &parameters,
        Some(Path::new("/var/lib/lemma/workspaces/box-1")),
        Some(Path::new("/var/lib/lemma/run/runtime-token-box-1/token")),
        Path::new("/var/lib/lemma/run/private-env"),
        "192.168.64.1",
    );
    let joined = arguments.join(" ");

    assert!(joined.contains("--env-file /var/lib/lemma/run/private-env"));
    assert!(!joined.contains("secret"));
    assert!(joined.contains("host.lemma.internal:192.168.64.1"));
    assert!(joined.contains("0.0.0.0::8080"));
    assert!(joined.contains("0.0.0.0::4848"));
    assert!(joined.contains("0.0.0.0::4850"));
    assert!(!joined.contains("0.0.0.0::8090"));
    // Recorded on the container, so that reading a sandbox back does not mean
    // trusting a second copy of this list compiled into the guest.
    assert!(
        joined.contains(r#"lemma.work/apps=[{"name":"runtime""#),
        "the declared apps are not written to the container: {joined}"
    );
    assert!(joined.contains(r#""port":4850"#));
    assert!(joined.contains("/var/lib/lemma/run/runtime-token-box-1,dst=/run/lemma-bootstrap"));
    assert!(!joined.contains("lemma-bootstrap,readonly"));
    assert!(joined.ends_with("ghcr.io/lemma/workspace@sha256:abc"));
}

#[test]
fn sandbox_resource_limits_are_bounded_and_normalized() {
    assert_eq!(parse_memory_bytes("2g").unwrap(), 2 * 1024 * 1024 * 1024);
    assert_eq!(parse_memory_bytes("512MiB").unwrap(), 512 * 1024 * 1024);
    assert_eq!(
        validate_resources(&ResourceSpec {
            memory: Some("2Gi".into()),
            cpus: Some("2.5".into()),
        })
        .unwrap(),
        2 * 1024 * 1024 * 1024
    );
    assert!(validate_resources(&ResourceSpec {
        memory: Some("64m".into()),
        cpus: Some("2".into()),
    })
    .is_err());
    assert!(validate_resources(&ResourceSpec {
        memory: Some("2g".into()),
        cpus: Some("99".into()),
    })
    .is_err());
}

#[test]
fn function_contract_is_read_only_ephemeral_and_exposes_only_its_runtime() {
    let parameters = EnsureParameters {
        sandbox_id: "function-1".into(),
        workload_kind: WorkloadKind::Function,
        image: "ghcr.io/lemma/function@sha256:def".into(),
        env: BTreeMap::new(),
        metadata: BTreeMap::new(),
        runtime_token: None,
        apps: function_apps(),
        resources: ResourceSpec::default(),
        callback: CallbackSpec::default(),
        host_access: true,
    };
    let arguments = build_run_arguments(
        &parameters,
        None,
        None,
        Path::new("/var/lib/lemma/run/private-env"),
        "192.168.64.1",
    );
    let joined = arguments.join(" ");

    assert!(validate_apps(&workspace_apps()).is_ok());
    assert!(validate_apps(&function_apps()).is_ok());
    assert!(joined.contains("--read-only"));
    assert!(joined.contains("/tmp:rw,noexec,nosuid"));
    assert!(joined.contains("/run/lemma-function-cache:rw,exec"));
    assert!(joined.contains("0.0.0.0::8090"));
    assert!(!joined.contains("dst=/workspace"));
}

/// A sandbox cannot fill the guest's disk with its own log.
///
/// The data disk is a fixed size, and a container's log lives on it. Nothing
/// bounded that log, so a sandbox with a chatty loop in it -- an agent
/// retrying, a dependency printing a warning per file -- could grow one until
/// the disk was full. A full data disk is not a lost sandbox: Postgres and
/// everything else in the guest stop with it.
#[test]
fn every_sandbox_runs_with_a_bounded_log() {
    for kind in [WorkloadKind::Workspace, WorkloadKind::Function] {
        let workspace = kind == WorkloadKind::Workspace;
        let parameters = EnsureParameters {
            sandbox_id: "box-1".into(),
            workload_kind: kind,
            image: "ghcr.io/lemma/workspace@sha256:abc".into(),
            env: BTreeMap::new(),
            metadata: BTreeMap::new(),
            runtime_token: workspace.then(|| "runtime-secret".into()),
            apps: if workspace {
                workspace_apps()
            } else {
                Vec::new()
            },
            resources: ResourceSpec::default(),
            callback: CallbackSpec::default(),
            host_access: true,
        };
        let arguments = build_run_arguments(
            &parameters,
            workspace.then_some(Path::new("/var/lib/lemma/workspaces/box-1")),
            workspace.then_some(Path::new("/var/lib/lemma/run/runtime-token-box-1/token")),
            Path::new("/var/lib/lemma/run/private-env"),
            "192.168.64.1",
        );
        let joined = arguments.join(" ");
        assert!(
            joined.contains("--log-opt max-size=16m"),
            "{kind:?} runs with no size cap on its log: {joined}"
        );
        assert!(
            joined.contains("--log-opt max-file=3"),
            "{kind:?} keeps one file, so rotation truncates instead of freeing: {joined}"
        );
    }
}

fn workspace_parameters(host_access: bool) -> EnsureParameters {
    EnsureParameters {
        sandbox_id: "box-1".into(),
        workload_kind: WorkloadKind::Workspace,
        image: "ghcr.io/lemma/workspace@sha256:abc".into(),
        env: BTreeMap::new(),
        metadata: BTreeMap::new(),
        runtime_token: Some("runtime-secret".into()),
        apps: workspace_apps(),
        resources: ResourceSpec::default(),
        callback: CallbackSpec::default(),
        host_access,
    }
}

fn run_arguments(parameters: &EnsureParameters) -> Vec<String> {
    let workspace = parameters.workload_kind == WorkloadKind::Workspace;
    build_run_arguments(
        parameters,
        workspace.then_some(Path::new("/var/lib/lemma/workspaces/box-1")),
        workspace.then_some(Path::new("/var/lib/lemma/run/runtime-token-box-1/token")),
        Path::new("/var/lib/lemma/run/private-env"),
        "192.168.64.1",
    )
}

/// Every sandbox runs with no capabilities and no way to gain one.
///
/// Both images run as an unprivileged user and need none; what the engine's
/// default set would have given is the start of an escape. Asserted as
/// adjacent pairs, because `--cap-drop` followed by something other than
/// `ALL` is a different, weaker statement that a `contains` would accept.
#[test]
fn every_sandbox_drops_every_capability_and_cannot_regain_one() {
    let mut function = workspace_parameters(true);
    function.workload_kind = WorkloadKind::Function;
    function.runtime_token = None;
    function.apps = function_apps();
    for parameters in [workspace_parameters(true), function] {
        let arguments = run_arguments(&parameters);
        let pairs: Vec<(&str, &str)> = arguments
            .windows(2)
            .map(|pair| (pair[0].as_str(), pair[1].as_str()))
            .collect();
        let kind = parameters.workload_kind;
        assert!(
            pairs.contains(&("--cap-drop", "ALL")),
            "{kind:?}: {arguments:?}"
        );
        assert!(
            pairs.contains(&("--security-opt", "no-new-privileges")),
            "{kind:?}: {arguments:?}"
        );
        assert!(
            !arguments.iter().any(|argument| argument == "--cap-add"),
            "{kind:?} adds a capability back: {arguments:?}"
        );
        assert!(
            !arguments.iter().any(|argument| argument == "--privileged"),
            "{kind:?}: {arguments:?}"
        );
        // Options, all of them, before the image: the engine reads anything
        // after it as the container's command.
        assert_eq!(arguments.last().unwrap(), &parameters.image);
    }
}

/// The host alias is per sandbox, and recorded on the container either way.
#[test]
fn the_host_alias_is_given_only_to_a_sandbox_that_asks_for_it() {
    let with = run_arguments(&workspace_parameters(true)).join(" ");
    let without = run_arguments(&workspace_parameters(false)).join(" ");

    assert!(with.contains("--add-host host.lemma.internal:192.168.64.1"));
    assert!(with.contains("lemma.work/host-access=true"));
    assert!(!without.contains("host.lemma.internal"), "{without}");
    assert!(without.contains("lemma.work/host-access=false"));
}

/// A caller that predates the flag -- every caller today -- keeps the alias.
#[test]
fn an_ensure_that_does_not_mention_host_access_keeps_it() {
    let parameters: EnsureParameters = serde_json::from_value(json!({
        "sandbox_id": "box-1",
        "workload_kind": "workspace",
        "image": "ghcr.io/lemma/workspace@sha256:abc",
        "runtime_token": "runtime-secret",
        "apps": [],
    }))
    .unwrap();
    assert!(parameters.host_access);

    let narrowed: Result<EnsureParameters, _> = serde_json::from_value(json!({
        "sandbox_id": "box-1",
        "workload_kind": "workspace",
        "image": "ghcr.io/lemma/workspace@sha256:abc",
        "apps": [],
        "host_access": false,
    }));
    assert!(!narrowed.unwrap().host_access);
}

/// The isolation rules name the bridge, the three core ports, and nothing else.
#[test]
fn sandbox_isolation_rejects_the_core_ports_from_the_sandbox_bridge_only() {
    let rules = sandbox_isolation_rules();
    for port in ["5432", "6379", "3567"] {
        assert!(
            rules.iter().any(|rule| rule.join(" ")
                == format!(
                    "-C LEMMA-SANDBOX-ISOLATION -p tcp --dport {port} -j REJECT --reject-with tcp-reset"
                )),
            "no rule for {port}: {rules:?}"
        );
    }
    assert!(rules
        .iter()
        .any(|rule| rule.join(" ") == "-C INPUT -i nerdctl0 -j LEMMA-SANDBOX-ISOLATION"));
    assert_eq!(rules.len(), 4, "a rule beyond the core ports: {rules:?}");
}

/// The installer adds what is missing, leaves what is present, and never flushes.
#[test]
fn sandbox_isolation_is_installed_idempotently_and_fails_closed() {
    use std::cell::RefCell;

    let installed: RefCell<Vec<Vec<String>>> = RefCell::new(Vec::new());
    let calls: RefCell<Vec<String>> = RefCell::new(Vec::new());
    let iptables = |arguments: &[String]| -> Result<bool, String> {
        calls.borrow_mut().push(arguments.join(" "));
        match arguments[0].as_str() {
            "-N" => Ok(true),
            "-C" => Ok(installed
                .borrow()
                .iter()
                .any(|rule| rule[1..] == arguments[1..])),
            "-A" => {
                installed.borrow_mut().push(arguments.to_vec());
                Ok(true)
            }
            "-I" => {
                // `-I INPUT 1 ...` is checked as `-C INPUT ...`.
                let mut rule = vec!["-I".to_owned(), "INPUT".to_owned()];
                rule.extend_from_slice(&arguments[3..]);
                installed.borrow_mut().push(rule);
                Ok(true)
            }
            other => panic!("unexpected iptables verb {other}"),
        }
    };

    ensure_sandbox_isolation(&iptables).unwrap();
    assert_eq!(installed.borrow().len(), 4);
    assert!(calls
        .borrow()
        .contains(&"-I INPUT 1 -i nerdctl0 -j LEMMA-SANDBOX-ISOLATION".to_owned()));

    calls.borrow_mut().clear();
    ensure_sandbox_isolation(&iptables).unwrap();
    assert_eq!(
        installed.borrow().len(),
        4,
        "a second pass added duplicates"
    );
    assert!(
        calls.borrow().iter().all(|call| !call.starts_with("-F")),
        "a flush leaves a window with no rule while sandboxes run"
    );

    let refusing = |arguments: &[String]| -> Result<bool, String> { Ok(arguments[0] == "-N") };
    let error = ensure_sandbox_isolation(&refusing).unwrap_err();
    assert_eq!(error.code, "sandbox_isolation_failed");
    assert!(error.retryable);
}
