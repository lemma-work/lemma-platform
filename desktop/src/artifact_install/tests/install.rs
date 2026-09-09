//! Installing a release end to end, and what an upgrade leaves behind.

use super::*;

/// Every upgrade used to leave another expanded runtime behind forever.
///
/// Installing deliberately removes nothing, and activation records exactly
/// two releases -- the live one and the one it replaced. Nothing ever
/// removed the third, so an installation's runtime directory grew by about
/// 2.2 GB per upgrade for as long as it was used, and none of that space
/// was reachable by anything.
#[test]
fn upgrading_stops_leaving_a_runtime_behind_every_time() {
    let root = tempfile::tempdir().unwrap();
    let install_root = root.path().join("runtime");
    let releases = install_root.join("releases");

    let release = |name: &str| {
        let path = releases.join(name);
        fs::create_dir_all(path.join("local-runtime")).unwrap();
        fs::create_dir_all(path.join("managed-runtime")).unwrap();
        path
    };
    let live = release("0.7.2-aaaaaaaa");
    let previous = release("0.7.2-bbbbbbbb");
    let retired = release("0.7.1-cccccccc");
    // Two things it must not touch: an install that was interrupted and
    // may still be being written, and a directory that is not ours.
    let staging = release(".0.7.2-1234-5678.staging");
    let stranger = releases.join("notes");
    fs::create_dir_all(&stranger).unwrap();

    let removed = prune_retired_releases(&install_root, &[live.clone(), previous.clone()]);

    assert_eq!(removed, vec![retired.clone()]);
    assert!(
        !retired.exists(),
        "the retired release is what this reclaims"
    );
    for kept in [&live, &previous, &staging, &stranger] {
        assert!(kept.exists(), "{} must survive", kept.display());
    }
}

#[test]
fn staging_candidates_retains_every_prior_runtime_until_health_validation() {
    let _guard = env_lock();
    let root = tempfile::tempdir().unwrap();
    let install_root = root.path().join("runtime");

    let install = |release: &str| {
        let host_entries = host_pack_entries(release);
        let host_zip = zip_of(
            &host_entries
                .iter()
                .map(|(name, bytes)| (name.as_str(), bytes.as_slice()))
                .collect::<Vec<_>>(),
        );
        let host_expanded: u64 = host_entries.iter().map(|(_, b)| b.len() as u64).sum();
        let host_path = root.path().join(format!("host-{release}.zip"));
        fs::write(&host_path, &host_zip).unwrap();

        let guest_entries = guest_runtime_entries();
        let guest_zip = zip_of(
            &guest_entries
                .iter()
                .map(|(name, bytes)| (name.as_str(), bytes.as_slice()))
                .collect::<Vec<_>>(),
        );
        let guest_expanded: u64 = guest_entries.iter().map(|(_, b)| b.len() as u64).sum();
        let guest_path = root.path().join(format!("guest-{release}.zip"));
        fs::write(&guest_path, &guest_zip).unwrap();

        let manifest = serde_json::json!({
            "schema_version": 1,
            "version": release,
            "host_packs": {
                host_target(): artifact_for_bytes(
                    &host_path, &host_zip, host_expanded, host_platform(), release),
            },
            "guest_runtimes": {
                guest_target(): artifact_for_bytes(
                    &guest_path, &guest_zip, guest_expanded, "linux", release),
            },
        });
        let manifest_path = root.path().join(format!("lemma-local-{release}.json"));
        fs::write(&manifest_path, serde_json::to_vec(&manifest).unwrap()).unwrap();
        std::env::set_var("LEMMA_DESKTOP_ALLOW_LOCAL_ARTIFACTS", "1");
        std::env::set_var("LEMMA_DESKTOP_RELEASE_MANIFEST", &manifest_path);
        install_from_manifest(&manifest_path, &install_root, release, &mut |_| {})
            .unwrap_or_else(|error| panic!("{release} installs: {error}"))
    };

    let releases = ["0.7.1-nightly.1", "0.7.1-nightly.2", "0.7.1-nightly.3"];
    let installed: Vec<_> = releases.iter().map(|release| install(release)).collect();
    for runtime in installed {
        assert!(runtime.is_complete());
        assert!(runtime.has_recorded_artifact_identity());
    }

    std::env::remove_var("LEMMA_DESKTOP_ALLOW_LOCAL_ARTIFACTS");
    std::env::remove_var("LEMMA_DESKTOP_RELEASE_MANIFEST");
}

/// The whole install, end to end, with no network and no VM.
///
/// `install_from_manifest` is the single most consequential function in the
/// app -- it is what turns a 23 MB download into a working installation --
/// and nothing exercised it as a unit. Its parts were tested individually
/// (digest checks, zip safety, the local-artifact gate) while the sequence
/// they form was proven only by somebody installing the app by hand.
///
/// This runs the real thing over fabricated archives: download, verify,
/// extract, validate, record identity, activate. Then it runs it again to
/// prove the second call is free, which is the promise a warm launch
/// depends on.
///
/// Serialised with the other env-var test in this module: the local
/// artifact gate reads process-global state, and cargo runs tests in
/// threads.
#[test]
fn a_manifest_installs_end_to_end_and_a_second_install_is_free() {
    let _guard = env_lock();
    let root = tempfile::tempdir().unwrap();
    let release = "1.2.3";

    let host_entries = host_pack_entries(release);
    let host_zip = zip_of(
        &host_entries
            .iter()
            .map(|(name, bytes)| (name.as_str(), bytes.as_slice()))
            .collect::<Vec<_>>(),
    );
    let host_expanded: u64 = host_entries.iter().map(|(_, b)| b.len() as u64).sum();
    let host_path = root.path().join("host.zip");
    fs::write(&host_path, &host_zip).unwrap();

    let guest_entries = guest_runtime_entries();
    let guest_zip = zip_of(
        &guest_entries
            .iter()
            .map(|(name, bytes)| (name.as_str(), bytes.as_slice()))
            .collect::<Vec<_>>(),
    );
    let guest_expanded: u64 = guest_entries.iter().map(|(_, b)| b.len() as u64).sum();
    let guest_path = root.path().join("guest.zip");
    fs::write(&guest_path, &guest_zip).unwrap();

    let manifest = serde_json::json!({
        "schema_version": 1,
        "version": release,
        "host_packs": {
            host_target(): artifact_for_bytes(
                &host_path, &host_zip, host_expanded, host_platform(), release),
        },
        "guest_runtimes": {
            guest_target(): artifact_for_bytes(
                &guest_path, &guest_zip, guest_expanded, "linux", release),
        },
    });
    let manifest_path = root.path().join("lemma-local.json");
    fs::write(&manifest_path, serde_json::to_vec(&manifest).unwrap()).unwrap();

    // The only way a `file://` artifact is honoured, and only for this
    // exact manifest.
    std::env::set_var("LEMMA_DESKTOP_ALLOW_LOCAL_ARTIFACTS", "1");
    std::env::set_var("LEMMA_DESKTOP_RELEASE_MANIFEST", &manifest_path);

    let install_root = root.path().join("runtime");
    let mut stages = Vec::new();
    let installed = install_from_manifest(&manifest_path, &install_root, release, &mut |p| {
        stages.push(p.stage.to_owned());
    })
    .expect("a well-formed manifest installs");

    assert!(installed.is_complete(), "the installed tree validates");
    assert!(installed.has_recorded_artifact_identity());
    assert_eq!(installed.release, release);
    assert!(
        installed.host_pack_root.join("release.json").is_file(),
        "the host pack is extracted where locald looks for it",
    );
    assert!(installed
        .managed_runtime_root
        .join(guest_target())
        .join("runtime.json")
        .is_file());
    // Real progress, in order, not a bar that jumps to done.
    for stage in [
        "download",
        "verify",
        "host-extract",
        "guest-extract",
        "validate",
    ] {
        assert!(
            stages.iter().any(|seen| seen == stage),
            "missing {stage}: {stages:?}"
        );
    }
    // The archives are cleaned up once they are no longer needed.
    assert!(
        !install_root
            .join("downloads")
            .join(release)
            .join("host-pack.zip")
            .exists(),
        "a successful install does not leave its downloads behind",
    );

    // A second call must be a no-op. This is what a warm launch depends on:
    // recognising an already-installed runtime by recorded identity rather
    // than re-downloading half a gigabyte.
    let mut second_stages = Vec::new();
    let again = install_from_manifest(&manifest_path, &install_root, release, &mut |p| {
        second_stages.push(p.stage.to_owned());
    })
    .expect("an already-installed runtime is reused");
    assert_eq!(again.host_pack_root, installed.host_pack_root);
    assert!(
        second_stages.is_empty(),
        "reinstalling did work it did not need to: {second_stages:?}",
    );

    let original_root = installed.host_pack_root.parent().unwrap();
    let legacy_root = install_root.join("releases").join(release);
    fs::rename(original_root, &legacy_root).unwrap();
    let legacy = install_from_manifest(&manifest_path, &install_root, release, &mut |_| {
        panic!("verified legacy runtime must be reused without downloading")
    })
    .unwrap();
    assert_eq!(legacy.host_pack_root.parent(), Some(legacy_root.as_path()));

    let mut changed_entries = host_entries.clone();
    changed_entries.push(("local-runtime/candidate.txt".into(), b"new build".to_vec()));
    let changed_zip = zip_of(
        &changed_entries
            .iter()
            .map(|(name, bytes)| (name.as_str(), bytes.as_slice()))
            .collect::<Vec<_>>(),
    );
    let mut changed_manifest = manifest.clone();
    changed_manifest["host_packs"][host_target()] = artifact_for_bytes(
        &host_path,
        &changed_zip,
        changed_entries
            .iter()
            .map(|(_, bytes)| bytes.len() as u64)
            .sum(),
        host_platform(),
        release,
    );
    fs::write(
        &manifest_path,
        serde_json::to_vec(&changed_manifest).unwrap(),
    )
    .unwrap();
    assert!(install_from_manifest(&manifest_path, &install_root, release, &mut |_| {}).is_err());
    assert!(legacy.is_complete());
    assert!(legacy.has_recorded_artifact_identity());

    fs::write(&host_path, &changed_zip).unwrap();
    let candidate =
        install_from_manifest(&manifest_path, &install_root, release, &mut |_| {}).unwrap();
    assert_ne!(candidate.host_pack_root, legacy.host_pack_root);
    assert!(candidate.host_pack_root.join("candidate.txt").is_file());
    assert!(!legacy.host_pack_root.join("candidate.txt").exists());
    assert!(legacy.is_complete());
    assert!(legacy.has_recorded_artifact_identity());

    fs::remove_file(candidate.host_pack_root.join("release.json")).unwrap();
    let repaired =
        install_from_manifest(&manifest_path, &install_root, release, &mut |_| {}).unwrap();
    assert_ne!(repaired.host_pack_root, candidate.host_pack_root);
    assert!(repaired.is_complete());
    assert!(candidate.host_pack_root.join("candidate.txt").is_file());
    assert!(legacy.is_complete());

    let fresh =
        reinstall_from_manifest(&manifest_path, &install_root, release, &mut |_| {}).unwrap();
    assert_ne!(fresh.host_pack_root, repaired.host_pack_root);
    assert!(fresh.is_complete());
    assert!(repaired.is_complete());
    assert!(legacy.is_complete());

    std::env::remove_var("LEMMA_DESKTOP_ALLOW_LOCAL_ARTIFACTS");
    std::env::remove_var("LEMMA_DESKTOP_RELEASE_MANIFEST");
}

/// A manifest whose version disagrees with the app installs nothing.
#[test]
fn a_manifest_for_another_release_is_refused_before_anything_is_downloaded() {
    let _guard = env_lock();
    let root = tempfile::tempdir().unwrap();
    let manifest_path = root.path().join("lemma-local.json");
    fs::write(
        &manifest_path,
        serde_json::to_vec(&serde_json::json!({
            "schema_version": 1,
            "version": "9.9.9",
            "host_packs": {},
            "guest_runtimes": {},
        }))
        .unwrap(),
    )
    .unwrap();

    let error = install_from_manifest(
        &manifest_path,
        &root.path().join("runtime"),
        "1.2.3",
        &mut |_| {},
    )
    .unwrap_err();
    assert!(error.to_string().contains("does not match"), "{error}");
    assert!(
        !root.path().join("runtime/downloads").exists(),
        "nothing is fetched for a runtime this app cannot use",
    );
}
