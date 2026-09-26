use super::super::tests::daemon;
use super::*;
use crate::disk_hygiene::UPDATE_BACKUP;

fn exchange(daemon: &Arc<Daemon>, request: Value) -> Vec<Value> {
    let (sender, receiver) = mpsc::sync_channel::<String>(64);
    daemon.dispatch(request, &sender);
    drop(sender);
    receiver
        .into_iter()
        .map(|line| serde_json::from_str(&line).expect("every reply is JSON"))
        .collect()
}

fn write_backup(daemon: &Daemon) -> std::path::PathBuf {
    let path = daemon.paths.root.join(UPDATE_BACKUP);
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(&path, vec![0_u8; 1 << 16]).unwrap();
    path
}

#[test]
fn the_snapshot_reports_the_backup_this_mac_offers_to_delete() {
    let (_root, daemon) = daemon();
    // `control.snapshot` itself reads the credential vault, which a test
    // cannot; it carries this value under `disk_usage` unchanged.
    assert!(daemon.disk_usage_snapshot()["update_backup"].is_null());
    write_backup(&daemon);
    let usage = daemon.disk_usage_snapshot();
    assert!(usage["update_backup"]["allocated_bytes"].as_u64().unwrap() > 0);
    let source = include_str!("sharing_ops.rs");
    assert!(source.contains("\"disk_usage\": self.disk_usage_snapshot(),"));
}

#[test]
fn a_cleanup_deletes_the_backup_only_when_asked_and_answers_with_fresh_figures() {
    let (_root, daemon) = daemon();
    let backup = write_backup(&daemon);

    let replies = exchange(
        &daemon,
        json!({"cmd": "disk.cleanup", "id": "c1", "prune_images": true}),
    );
    let answer = replies.last().unwrap();
    assert_eq!(answer["event"], "disk.cleanup");
    assert_eq!(answer["id"], "c1");
    assert!(
        backup.exists(),
        "the backup goes only when the request says so"
    );
    // No stack here, so the images are left and the answer says why.
    assert!(answer["images_error"].is_string());

    let replies = exchange(
        &daemon,
        json!({"cmd": "disk.cleanup", "id": "c2", "delete_backup": true}),
    );
    let answer = replies.last().unwrap();
    assert_eq!(answer["event"], "disk.cleanup");
    assert!(!backup.exists());
    assert!(answer["disk_usage"]["update_backup"].is_null());
}

/// A start that is migrating replaces this very file; deleting it under a
/// running operation could remove the copy that start just took.
#[test]
fn a_cleanup_refuses_the_backup_while_another_operation_runs() {
    let (_root, daemon) = daemon();
    let backup = write_backup(&daemon);
    let _busy = daemon.lifecycle.enter().expect("the lifecycle is free");
    let replies = exchange(
        &daemon,
        json!({"cmd": "disk.cleanup", "id": "c3", "delete_backup": true}),
    );
    assert_eq!(replies.last().unwrap()["event"], "error");
    assert!(backup.exists());
}

#[test]
fn a_backup_younger_than_three_days_survives_a_sweep_without_a_clean_start() {
    let (_root, daemon) = daemon();
    let backup = write_backup(&daemon);
    daemon.sweep_update_backup(false);
    assert!(backup.exists());
    // A clean start with nothing recorded as migrated is not evidence either.
    daemon.sweep_update_backup(true);
    assert!(backup.exists());
}
