//! The runtime overlay's directory: kept across containers, gone with the sandbox.

use super::*;

fn service(root: &Path) -> GuestService<FakeEngine> {
    GuestService::new(
        FakeEngine::new(vec![output(true, "")]),
        root.into(),
        Some("192.168.64.2".into()),
        "192.168.64.1".into(),
        None,
    )
    .unwrap()
}

fn installed_overlay(root: &Path, sandbox_id: &str) -> PathBuf {
    let overlay = root.join("runtime").join(sandbox_id);
    fs::create_dir_all(overlay.join("sha256-abc/site-packages")).unwrap();
    fs::write(overlay.join("sha256-abc/.stamp"), b"sha256:abc").unwrap();
    overlay
}

/// Purging a sandbox's storage takes its overlay with it.
///
/// The overlay is kept only so the sandbox's next container starts with it
/// installed. A purged sandbox has no next container, and an overlay left
/// behind would be adopted by nothing and removed by nothing.
#[test]
fn purging_a_sandbox_removes_its_overlay_with_its_home() {
    let root = tempdir().unwrap();
    let home = root.path().join("workspaces/box-1");
    fs::create_dir_all(&home).unwrap();
    let overlay = installed_overlay(root.path(), "box-1");
    let neighbour = installed_overlay(root.path(), "box-2");

    assert!(service(root.path()).purge_workspace("box-1").unwrap());

    assert!(!home.exists());
    assert!(!overlay.exists());
    assert!(
        neighbour.exists(),
        "another sandbox's overlay is not this one's"
    );
}

#[test]
fn an_overlay_without_a_home_is_still_purged() {
    let root = tempdir().unwrap();
    let overlay = installed_overlay(root.path(), "box-1");

    assert!(!service(root.path()).purge_workspace("box-1").unwrap());
    assert!(!overlay.exists());
}

#[test]
fn a_data_reset_removes_every_overlay_and_counts_only_homes() {
    let root = tempdir().unwrap();
    fs::create_dir_all(root.path().join("workspaces/box-1")).unwrap();
    installed_overlay(root.path(), "box-1");
    installed_overlay(root.path(), "box-2");

    let removed = service(root.path()).remove_all_workspaces().unwrap();

    assert_eq!(removed, 1);
    assert_eq!(
        fs::read_dir(root.path().join("runtime")).unwrap().count(),
        0
    );
}

#[test]
fn an_overlay_cannot_be_named_outside_its_root() {
    let root = tempdir().unwrap();
    let service = service(root.path());

    assert!(service.runtime_overlay("box-1/../../escape").is_err());
    assert!(service.purge_workspace("box-1/../../escape").is_err());
}
