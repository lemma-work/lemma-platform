//! Which manifests this build will act on.

use super::*;

#[test]
fn manifest_requires_https_digest_size_format_and_safe_release_name() {
    let root = tempfile::tempdir().unwrap();
    let path = root.path().join("lemma-local.json");
    let artifact = serde_json::json!({
        "url": "https://downloads.example.test/runtime.zip",
        "sha256": "a".repeat(64),
        "size": 42,
        "expanded_size": 84,
        "format": "zip",
        "platform": "macos",
        "architecture": "aarch64",
        "runtime_version": "1.2.3",
    });
    fs::write(
        &path,
        serde_json::to_vec(&serde_json::json!({
            "schema_version": 1,
            "version": "1.2.3",
            "host_packs": {host_target(): artifact.clone()},
            "guest_runtimes": {guest_target(): artifact},
        }))
        .unwrap(),
    )
    .unwrap();
    assert_eq!(load_manifest(&path).unwrap().version, "1.2.3");
    assert!(
        install_from_manifest(&path, &root.path().join("install"), "9.9.9", &mut |_| {}).is_err()
    );

    let unsafe_manifest = fs::read_to_string(&path)
        .unwrap()
        .replace("1.2.3", "../escape");
    fs::write(&path, unsafe_manifest).unwrap();
    assert!(load_manifest(&path).is_err());
}

#[test]
fn a_ci_build_check_manifest_is_refused_by_name_not_by_connection_error() {
    // The CI desktop job builds a real, signed, launchable DMG whose
    // manifest points nowhere. Without the marker check the first launch
    // reports "could not connect to the artifact host", which reads as a
    // network fault and costs whoever hit it an hour.
    let root = tempfile::tempdir().unwrap();
    let path = root.path().join("lemma-local.json");
    let artifact = serde_json::json!({
        "url": "https://downloads.example.invalid/lemma-host-pack.zip",
        "sha256": "0".repeat(64),
        "size": 1,
        "expanded_size": 1,
        "format": "zip",
        "platform": "macos",
        "architecture": "aarch64",
        "runtime_version": "1.2.3",
    });
    fs::write(
        &path,
        serde_json::to_vec(&serde_json::json!({
            "schema_version": 1,
            "version": "1.2.3",
            "artifact_source": "ci-build-check",
            "host_packs": {host_target(): artifact.clone()},
            "guest_runtimes": {guest_target(): artifact},
        }))
        .unwrap(),
    )
    .unwrap();

    let error = load_manifest(&path).expect_err("a build-check manifest must not install");
    let message = error.to_string();
    assert!(
        message.contains("build-check") && message.contains("Release Local Images"),
        "the refusal must name what this is and how to get a real one: {message}"
    );
}

#[test]
fn a_manifest_without_the_marker_is_unaffected() {
    // `artifact_source` is optional and every real manifest omits it today.
    let root = tempfile::tempdir().unwrap();
    let path = root.path().join("lemma-local.json");
    let artifact = serde_json::json!({
        "url": "https://downloads.example.test/runtime.zip",
        "sha256": "a".repeat(64),
        "size": 42,
        "expanded_size": 84,
        "format": "zip",
        "platform": "macos",
        "architecture": "aarch64",
        "runtime_version": "1.2.3",
    });
    fs::write(
        &path,
        serde_json::to_vec(&serde_json::json!({
            "schema_version": 1,
            "version": "1.2.3",
            "host_packs": {host_target(): artifact.clone()},
            "guest_runtimes": {guest_target(): artifact},
        }))
        .unwrap(),
    )
    .unwrap();
    assert_eq!(load_manifest(&path).unwrap().version, "1.2.3");
}

#[test]
fn local_file_artifacts_are_explicitly_gated_and_still_digest_verified() {
    let root = tempfile::tempdir().unwrap();
    let source = root.path().join("runtime.zip");
    fs::write(&source, b"locally-built-runtime").unwrap();
    let artifact = ArtifactRef {
        url: Some(reqwest::Url::from_file_path(&source).unwrap().to_string()),
        resource: None,
        sha256: file_sha256(&source).unwrap(),
        size: source.metadata().unwrap().len(),
        expanded_size: source.metadata().unwrap().len(),
        format: "zip".into(),
        platform: "macos".into(),
        architecture: "aarch64".into(),
        runtime_version: "1.2.3".into(),
    };

    assert!(validate_artifact(&artifact, false).is_err());
    validate_artifact(&artifact, true).unwrap();

    let destination = root.path().join("downloads/runtime.zip");
    fs::create_dir_all(destination.parent().unwrap()).unwrap();
    let mut reports = Vec::new();
    let copied = download_artifact(
        &download_client().unwrap(),
        &artifact,
        &destination,
        "Local runtime",
        ProgressSpan {
            completed_before: 0,
            total: artifact.size,
        },
        root.path(),
        true,
        &mut |progress| reports.push((progress.current, progress.total)),
    )
    .unwrap();

    assert_eq!(fs::read(copied).unwrap(), b"locally-built-runtime");
    assert_eq!(reports.last(), Some(&(artifact.size, artifact.size)));

    let changed = ArtifactRef {
        sha256: "0".repeat(64),
        ..artifact
    };
    assert!(download_artifact(
        &download_client().unwrap(),
        &changed,
        &root.path().join("changed.zip"),
        "Local runtime",
        ProgressSpan {
            completed_before: 0,
            total: changed.size,
        },
        root.path(),
        true,
        &mut |_| {},
    )
    .is_err());
}
