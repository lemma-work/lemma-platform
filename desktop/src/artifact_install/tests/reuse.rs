//! Reusing a component an installed release already has, and refusing to
//! when what is on disk is not what the manifest names.

use super::*;

/// One release's archives on disk and a manifest naming them.
///
/// `guest_source` overrides where the manifest says the guest archive is, so a
/// test can point it at a file that does not exist: an install that reaches
/// for it has made a request it should not have.
fn stage_release(
    root: &Path,
    release: &str,
    guest_entries: &[(String, Vec<u8>)],
    guest_source: Option<&Path>,
) -> PathBuf {
    let archive = |entries: &[(String, Vec<u8>)], name: &str| {
        let bytes = zip_of(
            &entries
                .iter()
                .map(|(name, bytes)| (name.as_str(), bytes.as_slice()))
                .collect::<Vec<_>>(),
        );
        let expanded: u64 = entries.iter().map(|(_, bytes)| bytes.len() as u64).sum();
        let path = root.join(format!("{name}-{release}.zip"));
        fs::write(&path, &bytes).unwrap();
        (path, bytes, expanded)
    };
    let (host_path, host_zip, host_expanded) = archive(&host_pack_entries(release), "host");
    let (guest_path, guest_zip, guest_expanded) = archive(guest_entries, "guest");
    let manifest = serde_json::json!({
        "schema_version": 1,
        "version": release,
        "host_packs": {
            host_target(): artifact_for_bytes(
                &host_path, &host_zip, host_expanded, host_platform(), release),
        },
        "guest_runtimes": {
            guest_target(): artifact_for_bytes(
                guest_source.unwrap_or(&guest_path),
                &guest_zip,
                guest_expanded,
                "linux",
                release,
            ),
        },
    });
    let manifest_path = root.join(format!("lemma-local-{release}.json"));
    fs::write(&manifest_path, serde_json::to_vec(&manifest).unwrap()).unwrap();
    manifest_path
}

/// Install `manifest`, returning the runtime and which components downloaded.
fn install(
    manifest: &Path,
    install_root: &Path,
    release: &str,
    reinstall: bool,
) -> (io::Result<InstalledRuntime>, Vec<String>, Vec<String>) {
    std::env::set_var("LEMMA_DESKTOP_ALLOW_LOCAL_ARTIFACTS", "1");
    std::env::set_var("LEMMA_DESKTOP_RELEASE_MANIFEST", manifest);
    let mut downloaded = Vec::new();
    let mut labels = Vec::new();
    let mut observe = |progress: InstallProgress<'_>| {
        if progress.stage == "download" && !downloaded.contains(&progress.component.to_owned()) {
            downloaded.push(progress.component.to_owned());
        }
        labels.push(progress.label.to_owned());
    };
    let result = if reinstall {
        reinstall_from_manifest(manifest, install_root, release, &mut observe)
    } else {
        install_from_manifest(manifest, install_root, release, &mut observe)
    };
    std::env::remove_var("LEMMA_DESKTOP_ALLOW_LOCAL_ARTIFACTS");
    std::env::remove_var("LEMMA_DESKTOP_RELEASE_MANIFEST");
    (result, downloaded, labels)
}

fn guest_file(runtime: &InstalledRuntime) -> PathBuf {
    let name = if cfg!(target_os = "macos") {
        "disk.raw"
    } else {
        "rootfs.tar"
    };
    runtime.managed_runtime_root.join(guest_target()).join(name)
}

fn changed_guest_entries() -> Vec<(String, Vec<u8>)> {
    guest_runtime_entries()
        .into_iter()
        .map(|(name, bytes)| {
            if name.ends_with("runtime.json") {
                (name, bytes)
            } else {
                (name, b"a newer guest".to_vec())
            }
        })
        .collect()
}

/// The point of the whole change: an update whose guest runtime did not
/// change does not download it. The manifest's guest source does not even
/// exist, so an install that reached for it would fail.
#[test]
fn an_unchanged_guest_is_reused_without_being_fetched() {
    let _guard = env_lock();
    let root = tempfile::tempdir().unwrap();
    let install_root = root.path().join("runtime");
    let first = stage_release(root.path(), "1.0.0", &guest_runtime_entries(), None);
    let (previous, downloaded, _) = install(&first, &install_root, "1.0.0", false);
    let previous = previous.unwrap();
    assert_eq!(downloaded, ["host", "guest"]);

    let missing = root.path().join("nowhere/guest.zip");
    let second = stage_release(
        root.path(),
        "1.1.0",
        &guest_runtime_entries(),
        Some(&missing),
    );
    let (installed, downloaded, _) = install(&second, &install_root, "1.1.0", false);
    let installed = installed.expect("the guest comes from the installed release");

    assert_eq!(downloaded, ["host"], "only the host pack changed");
    assert!(installed.is_complete());
    assert!(installed.has_recorded_artifact_identity());
    assert_eq!(
        fs::read(guest_file(&installed)).unwrap(),
        b"guest-bytes",
        "the reused tree is the one the archive installed"
    );
    // A copy, not the same directory: pruning the previous release, which
    // activation does, must not take the new one's guest with it.
    fs::remove_dir_all(previous.host_pack_root.parent().unwrap()).unwrap();
    assert!(installed.is_complete());
    // And the new release is itself a source for the next one.
    let recorded = read_installed_contents(installed.host_pack_root.parent().unwrap()).unwrap();
    assert!(recorded.get(Component::Guest).is_some());
}

#[test]
fn a_changed_guest_is_downloaded() {
    let _guard = env_lock();
    let root = tempfile::tempdir().unwrap();
    let install_root = root.path().join("runtime");
    let first = stage_release(root.path(), "1.0.0", &guest_runtime_entries(), None);
    install(&first, &install_root, "1.0.0", false).0.unwrap();

    let second = stage_release(root.path(), "1.1.0", &changed_guest_entries(), None);
    let (installed, downloaded, _) = install(&second, &install_root, "1.1.0", false);
    let installed = installed.unwrap();

    assert_eq!(downloaded, ["host", "guest"]);
    assert_eq!(fs::read(guest_file(&installed)).unwrap(), b"a newer guest");
}

/// A reused copy is hashed, and one that does not match what was recorded
/// when it was installed is thrown away and downloaded instead.
#[test]
fn a_damaged_installed_guest_is_downloaded_instead_of_reused() {
    let _guard = env_lock();
    let root = tempfile::tempdir().unwrap();
    let install_root = root.path().join("runtime");
    let first = stage_release(root.path(), "1.0.0", &guest_runtime_entries(), None);
    let previous = install(&first, &install_root, "1.0.0", false).0.unwrap();
    // Same size, different bytes: only a hash notices.
    fs::write(guest_file(&previous), b"guest-bytez").unwrap();

    let second = stage_release(root.path(), "1.1.0", &guest_runtime_entries(), None);
    let (installed, downloaded, labels) = install(&second, &install_root, "1.1.0", false);
    let installed = installed.unwrap();

    assert_eq!(downloaded, ["host", "guest"]);
    assert_eq!(fs::read(guest_file(&installed)).unwrap(), b"guest-bytes");
    assert!(
        labels
            .iter()
            .any(|label| label.contains("downloading it instead")),
        "the install log says why it downloaded: {labels:?}"
    );
}

/// A release installed before these records existed simply downloads, as
/// every update did before.
#[test]
fn a_release_without_a_contents_record_is_not_a_source() {
    let _guard = env_lock();
    let root = tempfile::tempdir().unwrap();
    let install_root = root.path().join("runtime");
    let first = stage_release(root.path(), "1.0.0", &guest_runtime_entries(), None);
    let previous = install(&first, &install_root, "1.0.0", false).0.unwrap();
    fs::remove_file(
        previous
            .host_pack_root
            .parent()
            .unwrap()
            .join(INSTALLED_CONTENTS_FILE),
    )
    .unwrap();

    let second = stage_release(root.path(), "1.1.0", &guest_runtime_entries(), None);
    let (installed, downloaded, _) = install(&second, &install_root, "1.1.0", false);
    installed.unwrap();
    assert_eq!(downloaded, ["host", "guest"]);
}

/// Repair exists to replace what is on this disk, so it reuses none of it.
#[test]
fn a_repair_downloads_everything() {
    let _guard = env_lock();
    let root = tempfile::tempdir().unwrap();
    let install_root = root.path().join("runtime");
    let first = stage_release(root.path(), "1.0.0", &guest_runtime_entries(), None);
    install(&first, &install_root, "1.0.0", false).0.unwrap();

    let second = stage_release(root.path(), "1.1.0", &guest_runtime_entries(), None);
    let (installed, downloaded, _) = install(&second, &install_root, "1.1.0", true);
    installed.unwrap();
    assert_eq!(downloaded, ["host", "guest"]);
}

/// The update dialog's figure is what will download, not the whole release.
#[test]
fn the_download_estimate_leaves_out_what_is_installed() {
    let _guard = env_lock();
    let root = tempfile::tempdir().unwrap();
    let install_root = root.path().join("runtime");
    let first = stage_release(root.path(), "1.0.0", &guest_runtime_entries(), None);
    let previous = install(&first, &install_root, "1.0.0", false).0.unwrap();
    let identity = read_installed_artifacts(previous.host_pack_root.parent().unwrap()).unwrap();

    let new_host = ("c".repeat(64), 300);
    let same_guest = (identity.guest_sha256.clone(), identity.guest_size);
    let estimate = bytes_to_download(
        &install_root,
        &[
            (Component::Host, new_host.0.clone(), new_host.1),
            (Component::Guest, same_guest.0.clone(), same_guest.1),
        ],
    );
    assert_eq!(estimate, 300);

    let other_guest = bytes_to_download(&install_root, &[(Component::Guest, "d".repeat(64), 77)]);
    assert_eq!(other_guest, 77);
    assert_eq!(
        bytes_to_download(
            &root.path().join("empty"),
            &[(Component::Guest, same_guest.0, same_guest.1)]
        ),
        same_guest.1,
        "nothing installed, everything downloads"
    );
}

#[test]
fn the_tree_digest_changes_with_any_content_and_nothing_else() {
    let root = tempfile::tempdir().unwrap();
    let tree = root.path().join("tree");
    fs::create_dir_all(tree.join("a/b")).unwrap();
    fs::write(tree.join("a/b/file"), b"one").unwrap();
    fs::write(tree.join("top"), b"two").unwrap();
    let digest = || tree_digest(&tree, &mut |_| {}).unwrap();

    let (original, bytes) = digest();
    assert_eq!(bytes, 6);
    assert_eq!(digest().0, original, "the same tree digests the same");

    let copy = root.path().join("copy");
    copy_tree(&tree, &copy).unwrap();
    assert_eq!(tree_digest(&copy, &mut |_| {}).unwrap().0, original);

    fs::write(tree.join("top"), b"tw0").unwrap();
    assert_ne!(digest().0, original, "same size, different bytes");
    fs::write(tree.join("top"), b"two").unwrap();

    fs::write(tree.join("extra"), b"").unwrap();
    assert_ne!(digest().0, original, "an added empty file");
    fs::remove_file(tree.join("extra")).unwrap();

    fs::create_dir(tree.join("empty")).unwrap();
    assert_ne!(digest().0, original, "an added directory");
    fs::remove_dir(tree.join("empty")).unwrap();

    fs::rename(tree.join("a/b/file"), tree.join("a/b/renamed")).unwrap();
    assert_ne!(digest().0, original, "a renamed file");
    fs::rename(tree.join("a/b/renamed"), tree.join("a/b/file")).unwrap();
    assert_eq!(digest().0, original);
}

#[cfg(unix)]
#[test]
fn the_tree_digest_covers_permissions_and_refuses_links() {
    use std::os::unix::fs::PermissionsExt;

    let root = tempfile::tempdir().unwrap();
    let tree = root.path().join("tree");
    fs::create_dir_all(&tree).unwrap();
    fs::write(tree.join("python3"), b"#!").unwrap();
    fs::set_permissions(tree.join("python3"), fs::Permissions::from_mode(0o755)).unwrap();
    let executable = tree_digest(&tree, &mut |_| {}).unwrap().0;
    fs::set_permissions(tree.join("python3"), fs::Permissions::from_mode(0o644)).unwrap();
    assert_ne!(tree_digest(&tree, &mut |_| {}).unwrap().0, executable);

    std::os::unix::fs::symlink("/etc/hosts", tree.join("link")).unwrap();
    assert!(tree_digest(&tree, &mut |_| {}).is_err());
    assert!(copy_tree(&tree, &root.path().join("copy")).is_err());
}
