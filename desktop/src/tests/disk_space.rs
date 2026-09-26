//! This Mac's disk row: the figures a page may see, and the two commands.

use super::*;
use crate::disk_space::*;

#[test]
fn both_disk_commands_check_their_caller_and_are_granted_and_registered() {
    let source = include_str!("../disk_space.rs").replace("\r\n", "\n");
    for command in [
        "pub(crate) async fn delete_update_backup(",
        "pub(crate) async fn free_up_disk_space(",
    ] {
        let body = function_body(&source, command);
        assert!(
            body.contains("require_local_settings_caller(&window, &app)?;"),
            "{command} does not check its caller",
        );
    }
    let capability = include_str!("../../capabilities/workspace.json").replace("\r\n", "\n");
    let app = include_str!("../app.rs").replace("\r\n", "\n");
    let build = include_str!("../../build.rs").replace("\r\n", "\n");
    for (grant, handler, name) in [
        (
            "allow-delete-update-backup",
            "disk_space::delete_update_backup",
            "delete_update_backup",
        ),
        (
            "allow-free-up-disk-space",
            "disk_space::free_up_disk_space",
            "free_up_disk_space",
        ),
    ] {
        assert!(capability.contains(&format!("\"{grant}\"")));
        assert!(app.contains(handler));
        assert!(build.contains(&format!("\"{name}\"")));
    }
}

/// The page asking is not the person agreeing: the backup goes only after
/// the shell's own question, in both commands that can delete it.
#[test]
fn deleting_the_backup_is_asked_natively_in_both_commands() {
    let source = include_str!("../disk_space.rs").replace("\r\n", "\n");
    for implementation in [
        "fn delete_update_backup_impl(",
        "fn free_up_disk_space_impl(",
    ] {
        let body = function_body(&source, implementation);
        let asked = body
            .find("backup_consent(&backup).ask(&app)?")
            .expect("asks");
        let sent = body.find("disk_cleanup_request(").expect("sends");
        assert!(asked < sent, "{implementation} asks before it sends");
    }
}

#[test]
fn a_cleanup_request_carries_two_booleans_and_nothing_else() {
    let request = disk_cleanup_request(true, false);
    assert_eq!(request["cmd"], "disk.cleanup");
    assert_eq!(request["delete_backup"], true);
    assert_eq!(request["prune_images"], false);
    let mut keys: Vec<_> = request.as_object().unwrap().keys().cloned().collect();
    keys.sort();
    assert_eq!(keys, ["cmd", "delete_backup", "id", "prune_images"]);
}

#[test]
fn the_disk_figures_reach_the_page_narrowed() {
    let view = workspace_settings_view(&json!({
        "disk_usage": {
            "data_disk": {"allocated_bytes": 18_791_067_648_u64, "path": "/secret"},
            "update_backup": {
                "allocated_bytes": 16_033_103_872_u64,
                "reclaimable_bytes": 183_316_480_u64,
                "size_is_upper_bound": false,
                "taken_at_unix": 1,
                "expires_at_unix": 259_201,
                "path": "/secret",
            },
            "last_image_prune_unix": 5,
        },
    }));
    let disk = &view["disk_usage"];
    assert_eq!(
        disk["data_disk"],
        json!({"allocated_bytes": 18_791_067_648_u64})
    );
    assert_eq!(disk["update_backup"]["reclaimable_bytes"], 183_316_480_u64);
    assert!(disk["update_backup"].get("path").is_none());
    assert!(disk["update_backup"].get("taken_at_unix").is_none());
    assert!(disk.get("last_image_prune_unix").is_none());
    // An older daemon sends none, and the page is told "none".
    let empty = workspace_settings_view(&json!({}));
    assert!(empty["disk_usage"]["update_backup"].is_null());
    assert!(empty["disk_usage"]["data_disk"].is_null());
}

#[test]
fn the_question_names_what_deleting_frees_or_says_up_to() {
    assert_eq!(
        backup_size_words(
            &json!({"allocated_bytes": 16_033_103_872_u64, "reclaimable_bytes": 183_316_480_u64})
        ),
        "183 MB"
    );
    assert_eq!(
        backup_size_words(
            &json!({"allocated_bytes": 16_033_103_872_u64, "reclaimable_bytes": null})
        ),
        "up to 16 GB"
    );
    let consent = backup_consent(&json!({"allocated_bytes": 2_000_000_000_u64}));
    assert!(consent.message.contains("up to 2.0 GB"));
    assert_eq!(consent.confirm, "Delete Backup");
}

#[test]
fn sizes_read_the_way_finder_shows_them() {
    assert_eq!(format_bytes(512), "512 bytes");
    assert_eq!(format_bytes(1_500), "1.5 KB");
    assert_eq!(format_bytes(999_999), "1.0 MB");
    assert_eq!(format_bytes(2_200_000_000), "2.2 GB");
}

#[test]
fn no_recorded_runtime_means_no_release_is_pruned() {
    assert!(retained_release_roots(&json!({})).is_empty());
    // A recorded runtime whose directory is gone is no runtime either.
    assert!(retained_release_roots(&json!({
        "installedRuntime": {"release": "0.8.0", "root": "/nonexistent/releases/0.8.0-a"}
    }))
    .is_empty());
}

#[test]
fn releases_are_sized_by_their_blocks_and_marked_when_kept() {
    let root = tempfile::tempdir().unwrap();
    let releases = root.path().join("releases");
    for (name, bytes) in [("0.8.0-aaaa", 200_000), ("0.7.9-bbbb", 100_000)] {
        let dir = releases.join(name).join("local-runtime");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("blob"), vec![1_u8; bytes]).unwrap();
    }
    let usage = runtime_releases_usage(root.path(), &[releases.join("0.8.0-aaaa")]);
    let listed = usage["releases"].as_array().unwrap();
    assert_eq!(listed.len(), 2);
    let kept: Vec<_> = listed
        .iter()
        .filter(|release| release["kept"] == true)
        .map(|release| release["name"].as_str().unwrap())
        .collect();
    assert_eq!(kept, ["0.8.0-aaaa"]);
    assert!(usage["allocated_bytes"].as_u64().unwrap() >= 300_000);
}
