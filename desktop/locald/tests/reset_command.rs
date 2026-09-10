use std::fs;
use std::process::Command;
use std::time::Duration;

fn reset_command(root: &std::path::Path) -> Command {
    let mut command = Command::new(env!("CARGO_BIN_EXE_lemma-locald"));
    command
        .arg("reset")
        .env("LEMMA_LOCALD_ROOT", root)
        .env("LEMMA_LOCALD_WSL_BIN", root.join("not-a-wsl-executable"));
    command
}

#[test]
fn reset_without_explicit_confirmation_preserves_local_data() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("workspace-data"), b"keep me").unwrap();
    let output =
        lemma_desktop_process::run(reset_command(root.path()), Duration::from_secs(10), 4096)
            .unwrap();
    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr).contains("--confirm=erase-local-lemma"));
    assert_eq!(
        fs::read(root.path().join("workspace-data")).unwrap(),
        b"keep me"
    );
}

#[test]
fn confirmed_reset_works_with_unreadable_configuration_and_no_daemon() {
    let root = tempfile::tempdir().unwrap();
    let locald = root.path().join("broken locald ü");
    fs::create_dir(&locald).unwrap();
    fs::write(
        locald.join("operator-config.json"),
        b"broken old configuration",
    )
    .unwrap();
    fs::write(locald.join("control.token"), b"invalid old token").unwrap();
    fs::create_dir_all(locald.join("runtime/macos")).unwrap();
    fs::write(locald.join("runtime/macos/data.raw"), b"old database").unwrap();
    let mut command = reset_command(&locald);
    command.arg("--confirm=erase-local-lemma");
    let output = lemma_desktop_process::run(command, Duration::from_secs(10), 4096).unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let summary: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(summary["root_removed"], true);
    assert!(!locald.exists());
}

#[test]
fn even_confirmed_cleanup_refuses_a_live_service_with_a_broken_token() {
    use interprocess::local_socket::{prelude::*, ListenerOptions};
    let root = tempfile::tempdir_in(if cfg!(windows) {
        std::env::temp_dir()
    } else {
        "/tmp".into()
    })
    .unwrap();
    let paths = lemma_locald::paths::LocalPaths::new(root.path().to_path_buf());
    fs::write(paths.root.join("workspace-data"), b"keep active data").unwrap();
    fs::write(&paths.token, b"damaged credential").unwrap();
    let listener = ListenerOptions::new()
        .name(paths.socket_name().unwrap())
        .create_sync_as::<LocalSocketListener>()
        .unwrap();
    let mut command = reset_command(root.path());
    command.arg("--confirm=erase-local-lemma");
    let output = lemma_desktop_process::run(command, Duration::from_secs(10), 4096).unwrap();
    drop(listener);
    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr).contains("still running"));
    assert_eq!(
        fs::read(paths.root.join("workspace-data")).unwrap(),
        b"keep active data"
    );
}
