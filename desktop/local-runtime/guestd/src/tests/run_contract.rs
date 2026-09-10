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
    assert!(!joined.contains("0.0.0.0::8090"));
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
