//! Whether what is on disk is the release it claims to be.

use super::*;

#[test]
fn installed_runtime_requires_every_native_and_guest_marker() {
    let root = tempfile::tempdir().unwrap();
    let runtime = installed_runtime(root.path(), "1.2.3");
    fs::create_dir_all(&runtime.host_pack_root).unwrap();
    fs::write(
        runtime.host_pack_root.join("release.json"),
        br#"{"version":"1.2.3"}"#,
    )
    .unwrap();
    assert!(!runtime.is_complete());
    assert!(complete_runtime(root.path(), "1.2.3").is_complete());
}

#[test]
fn cached_runtime_must_match_the_signed_artifact_identity() {
    let root = tempfile::tempdir().unwrap();
    let release = root.path().join("1.2.3");
    let runtime = complete_runtime(&release, "1.2.3");
    let host = ArtifactRef {
        url: Some("https://downloads.example.test/host.zip".into()),
        resource: None,
        sha256: "a".repeat(64),
        size: 42,
        expanded_size: 84,
        format: "zip".into(),
        platform: "macos".into(),
        architecture: "aarch64".into(),
        runtime_version: "1.2.3".into(),
    };
    let guest = ArtifactRef {
        url: Some("https://downloads.example.test/guest.zip".into()),
        resource: None,
        sha256: "b".repeat(64),
        size: 84,
        expanded_size: 168,
        format: "zip".into(),
        platform: "linux".into(),
        architecture: "aarch64".into(),
        runtime_version: "1.2.3".into(),
    };
    let expected = artifact_identity("1.2.3", &host, &guest);

    assert!(runtime.is_complete());
    assert!(!runtime.has_recorded_artifact_identity());
    assert!(!installed_artifacts_match(&release, &expected));
    write_installed_artifacts(&release, &expected).unwrap();
    assert!(runtime.has_recorded_artifact_identity());
    assert!(installed_artifacts_match(&release, &expected));

    let changed = InstalledArtifactIdentity {
        guest_sha256: "c".repeat(64),
        ..expected
    };
    assert!(!installed_artifacts_match(&release, &changed));
}

#[test]
fn recorded_runtime_identity_rejects_placeholders_and_wrong_targets() {
    let root = tempfile::tempdir().unwrap();
    let release = root.path().join("1.2.3");
    let runtime = complete_runtime(&release, "1.2.3");
    let placeholder = InstalledArtifactIdentity {
        schema_version: MANIFEST_SCHEMA_VERSION,
        release: "1.2.3".into(),
        host_target: host_target().into(),
        host_sha256: "0".repeat(64),
        host_size: 1,
        guest_target: guest_target().into(),
        guest_sha256: "0".repeat(64),
        guest_size: 1,
    };
    write_installed_artifacts(&release, &placeholder).unwrap();
    assert!(!runtime.has_recorded_artifact_identity());

    fs::remove_file(release.join(INSTALLED_ARTIFACTS_FILE)).unwrap();
    let wrong_target = InstalledArtifactIdentity {
        host_sha256: "a".repeat(64),
        guest_sha256: "b".repeat(64),
        host_target: "another-host".into(),
        ..placeholder
    };
    write_installed_artifacts(&release, &wrong_target).unwrap();
    assert!(!runtime.has_recorded_artifact_identity());
}
