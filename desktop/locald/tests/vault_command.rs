use std::process::{Child, Command};
use std::time::Duration;

struct OwnedHelper(Child);

impl Drop for OwnedHelper {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

#[test]
fn credential_helper_exits_even_when_a_peer_never_finishes_its_request() {
    let mut child = OwnedHelper(
        Command::new(env!("CARGO_BIN_EXE_lemma-locald"))
            .arg("credential-vault")
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn()
            .unwrap(),
    );
    let deadline = std::time::Instant::now() + Duration::from_secs(20);
    loop {
        if let Some(status) = child.0.try_wait().unwrap() {
            assert_eq!(status.code(), Some(124));
            break;
        }
        if std::time::Instant::now() >= deadline {
            panic!("credential helper outlived its own deadline");
        }
        std::thread::sleep(Duration::from_millis(100));
    }
}

#[test]
fn invalid_credential_cli_input_fails_without_starting_daemon_or_exposing_values() {
    let root = tempfile::tempdir().unwrap();
    let mut command = Command::new(env!("CARGO_BIN_EXE_lemma-locald"));
    command
        .arg("credential-vault")
        .env("LEMMA_LOCALD_ROOT", root.path());
    let output = lemma_desktop_process::run_with_input(
        command,
        br#"{"version":1,"install_id":"one","name":"unrelated","operation":{"action":"set","value":"private-fixture-value"}}"#.to_vec(),
        Duration::from_secs(5),
        4096,
    ).unwrap();
    assert!(!output.status.success());
    assert!(output.stdout.is_empty());
    assert!(String::from_utf8_lossy(&output.stderr).contains("Invalid credential request"));
    assert!(!String::from_utf8_lossy(&output.stderr).contains("private-fixture-value"));
    assert_eq!(std::fs::read_dir(root.path()).unwrap().count(), 0);
}
