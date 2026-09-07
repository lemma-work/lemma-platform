use std::fs;
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

use interprocess::local_socket::prelude::*;
use lemma_locald::paths::LocalPaths;
use serde_json::Value;

struct OwnedDaemon(Child);

impl Drop for OwnedDaemon {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

#[test]
fn authenticated_shutdown_exits_the_daemon_and_preserves_installation_data() {
    let directory = tempfile::tempdir_in(if cfg!(windows) {
        std::env::temp_dir()
    } else {
        "/tmp".into()
    })
    .unwrap();
    let paths = LocalPaths::new(directory.path().join("locald"));
    paths.ensure().unwrap();
    fs::write(paths.root.join("workspace-data"), "retained").unwrap();
    let mut serve = Command::new(env!("CARGO_BIN_EXE_lemma-locald"));
    serve
        .arg("serve")
        .env("LEMMA_LOCALD_ROOT", &paths.root)
        .env_remove("LEMMA_LOCALD_HOST_PACK_ROOT")
        .env_remove("LEMMA_LOCALD_HOST_PACK_MANIFEST")
        .env_remove("LEMMA_LOCALD_MANAGED_RUNTIME_ARTIFACT_ROOT")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    let mut daemon = OwnedDaemon(serve.spawn().unwrap());
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        if paths.token.exists() && LocalSocketStream::connect(paths.socket_name().unwrap()).is_ok()
        {
            break;
        }
        assert!(
            daemon.0.try_wait().unwrap().is_none(),
            "daemon exited before opening its endpoint"
        );
        assert!(
            Instant::now() < deadline,
            "daemon did not open its endpoint"
        );
        std::thread::sleep(Duration::from_millis(20));
    }
    let mut shutdown = Command::new(env!("CARGO_BIN_EXE_lemma-locald"));
    shutdown
        .args(["send", r#"{"cmd":"shutdown-daemon","id":"test-quit"}"#])
        .env("LEMMA_LOCALD_ROOT", &paths.root);
    let result = lemma_desktop_process::run(shutdown, Duration::from_secs(10), 64 * 1024).unwrap();
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
    let events: Vec<Value> = String::from_utf8(result.stdout)
        .unwrap()
        .lines()
        .map(|line| serde_json::from_str(line).unwrap())
        .collect();
    assert!(events
        .iter()
        .any(|event| event["event"] == "ack" && event["id"] == "test-quit"));
    assert!(events.iter().any(|event| event["event"] == "done"
        && event["id"] == "test-quit"
        && event["ok"] == true));
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        if let Some(status) = daemon.0.try_wait().unwrap() {
            assert!(status.success());
            break;
        }
        assert!(
            Instant::now() < deadline,
            "acknowledged shutdown left the daemon running"
        );
        std::thread::sleep(Duration::from_millis(20));
    }
    assert_eq!(
        fs::read_to_string(paths.root.join("workspace-data")).unwrap(),
        "retained"
    );
    let state: Value = serde_json::from_slice(&fs::read(&paths.state).unwrap()).unwrap();
    assert_eq!(state["status"], "stopped");
    assert_eq!(state["running"], false);
    assert!(LocalSocketStream::connect(paths.socket_name().unwrap()).is_err());
}
