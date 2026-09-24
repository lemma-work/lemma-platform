//! The exec-server's ops, driven through `ExecServer::handle` as the relay
//! drives them, against real processes and a real filesystem.

use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::{Duration, Instant};

use base64::Engine;
use base64::engine::general_purpose::STANDARD;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

use super::server::{ExecServer, ServerConfig};
use super::wire::{ExecRequest, OpFailure, kind, method};

struct Fixture {
    _directory: tempfile::TempDir,
    server: Arc<ExecServer>,
    root: PathBuf,
    outside: PathBuf,
}

async fn call(server: &ExecServer, method: &str, params: Value) -> Result<Value, OpFailure> {
    server
        .handle(ExecRequest {
            id: "1".into(),
            workspace: "w".into(),
            method: method.into(),
            params,
            deadline_ms: Some(30_000),
        })
        .await
}

async fn fixture() -> Fixture {
    let directory = tempfile::tempdir().unwrap();
    let base = directory.path().join("lemma");
    let tmp = directory.path().join("tmp");
    let outside = directory.path().join("outside");
    for folder in [&base, &tmp, &outside] {
        std::fs::create_dir_all(folder).unwrap();
    }
    let server = ExecServer::new(ServerConfig {
        root_base: base,
        home: directory.path().to_path_buf(),
        tmp,
    });
    let opened = call(
        &server,
        method::WORKSPACE_OPEN,
        json!({ "slug": "demo", "date": "2026-09-25", "conversation_id": null }),
    )
    .await
    .unwrap();
    let root = PathBuf::from(opened["root"].as_str().unwrap());
    Fixture {
        server,
        root,
        outside: std::fs::canonicalize(outside).unwrap(),
        _directory: directory,
    }
}

fn text(chunks: &Value) -> String {
    chunks
        .as_array()
        .unwrap()
        .iter()
        .map(|chunk| {
            String::from_utf8(STANDARD.decode(chunk["data"].as_str().unwrap()).unwrap()).unwrap()
        })
        .collect()
}

/// Read until the process has exited, gathering everything it wrote.
async fn read_to_exit(server: &ExecServer, process_id: &str) -> (Value, String) {
    let mut after = 0;
    let mut output = String::new();
    let deadline = Instant::now() + Duration::from_secs(20);
    loop {
        let read = call(
            server,
            method::PROCESS_READ,
            json!({ "process_id": process_id, "after_sequence": after, "wait_ms": 2000 }),
        )
        .await
        .unwrap();
        output.push_str(&text(&read["chunks"]));
        after = read["next_sequence"].as_u64().unwrap() - 1;
        if read["state"] != "running" {
            return (read, output);
        }
        assert!(Instant::now() < deadline, "the process never exited");
    }
}

async fn start(server: &ExecServer, params: Value) -> String {
    call(server, method::PROCESS_START, params).await.unwrap()["process_id"]
        .as_str()
        .unwrap()
        .to_owned()
}

#[tokio::test]
async fn a_workspace_opens_at_the_conversation_folder_and_ops_need_it_open() {
    let fixture = fixture().await;
    assert!(fixture.root.ends_with("lemma/c/2026-09-25/demo"));
    assert!(fixture.root.is_dir());
    let error = fixture
        .server
        .handle(ExecRequest {
            id: "1".into(),
            workspace: "never-opened".into(),
            method: method::FILE_STAT.into(),
            params: json!({ "path": "x" }),
            deadline_ms: None,
        })
        .await
        .unwrap_err();
    assert_eq!(error.kind, kind::WORKSPACE_NOT_OPEN);
    let error = call(&fixture.server, "file.shred", json!({}))
        .await
        .unwrap_err();
    assert_eq!(error.kind, kind::INVALID_REQUEST);
}

#[tokio::test]
async fn a_command_runs_in_the_root_with_its_streams_and_exit_code() {
    let fixture = fixture().await;
    let id = start(
        &fixture.server,
        json!({
            "shell_command": "pwd; echo out; echo err >&2; echo $GREETING; exit 3",
            "environment": [{ "name": "GREETING", "value": "hello" }],
        }),
    )
    .await;
    let (last, output) = read_to_exit(&fixture.server, &id).await;
    assert_eq!(last["state"], "exited");
    assert_eq!(last["exit_code"], 3);
    assert!(output.contains(fixture.root.to_str().unwrap()), "{output}");
    assert!(output.contains("out") && output.contains("err") && output.contains("hello"));
    // stderr is its own stream when there is no tty.
    let all = call(
        &fixture.server,
        method::PROCESS_READ,
        json!({ "process_id": id, "after_sequence": 0 }),
    )
    .await
    .unwrap();
    let streams: Vec<_> = all["chunks"]
        .as_array()
        .unwrap()
        .iter()
        .map(|chunk| chunk["stream"].as_str().unwrap().to_owned())
        .collect();
    assert!(streams.contains(&"stderr".to_owned()), "{streams:?}");
    assert_eq!(all["chunks"][0]["sequence"], 1);
}

#[tokio::test]
async fn a_retried_start_finds_the_process_it_already_started() {
    let fixture = fixture().await;
    let params = json!({ "operation_id": "op-1", "argv": ["/bin/sleep", "5"] });
    let first = start(&fixture.server, params.clone()).await;
    let second = start(&fixture.server, params).await;
    assert_eq!(first, second);
    let listed = call(&fixture.server, method::PROCESS_LIST, json!({}))
        .await
        .unwrap();
    assert_eq!(listed["processes"].as_array().unwrap().len(), 1);
    assert_eq!(listed["processes"][0]["state"], "running");
    assert_eq!(listed["processes"][0]["command"], "/bin/sleep 5");
    call(
        &fixture.server,
        method::PROCESS_TERMINATE,
        json!({ "process_id": first, "grace_ms": 100 }),
    )
    .await
    .unwrap();
}

#[tokio::test]
async fn a_read_waits_for_output_instead_of_returning_empty() {
    let fixture = fixture().await;
    let id = start(
        &fixture.server,
        json!({ "shell_command": "sleep 0.3; echo late; sleep 5" }),
    )
    .await;
    let started = Instant::now();
    let read = call(
        &fixture.server,
        method::PROCESS_READ,
        json!({ "process_id": id, "after_sequence": 0, "wait_ms": 10_000 }),
    )
    .await
    .unwrap();
    assert!(text(&read["chunks"]).contains("late"));
    assert!(started.elapsed() < Duration::from_secs(4));
    assert_eq!(read["state"], "running");
    call(
        &fixture.server,
        method::PROCESS_TERMINATE,
        json!({ "process_id": id, "grace_ms": 100 }),
    )
    .await
    .unwrap();
}

#[tokio::test]
async fn terminate_reaches_children_that_outlive_the_leader() {
    let fixture = fixture().await;
    let pid_file = fixture.root.join("child.pid");
    let id = start(
        &fixture.server,
        json!({
            "shell_command": format!(
                "trap '' TERM; sleep 60 & echo $! > {}; wait",
                pid_file.display()
            ),
        }),
    )
    .await;
    let deadline = Instant::now() + Duration::from_secs(5);
    while !pid_file.exists()
        || std::fs::read_to_string(&pid_file)
            .unwrap()
            .trim()
            .is_empty()
    {
        assert!(Instant::now() < deadline, "the child never started");
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
    let child: i32 = std::fs::read_to_string(&pid_file)
        .unwrap()
        .trim()
        .parse()
        .unwrap();
    call(
        &fixture.server,
        method::PROCESS_TERMINATE,
        json!({ "process_id": id, "grace_ms": 200 }),
    )
    .await
    .unwrap();
    let (last, _) = read_to_exit(&fixture.server, &id).await;
    assert_eq!(last["state"], "killed");
    let child = rustix::process::Pid::from_raw(child).unwrap();
    let deadline = Instant::now() + Duration::from_secs(5);
    while rustix::process::test_kill_process(child).is_ok() {
        assert!(Instant::now() < deadline, "the background child survived");
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
}

#[tokio::test]
async fn output_past_the_limit_is_dropped_from_the_front_and_says_where() {
    let fixture = fixture().await;
    let id = start(
        &fixture.server,
        json!({
            "shell_command": "for i in $(seq 1 200); do echo line-$i; sleep 0.001; done",
            "output_limit_bytes": 256,
        }),
    )
    .await;
    read_to_exit(&fixture.server, &id).await;
    let all = call(
        &fixture.server,
        method::PROCESS_READ,
        json!({ "process_id": id, "after_sequence": 0 }),
    )
    .await
    .unwrap();
    let truncated = all["truncated_before_sequence"].as_u64().unwrap();
    assert!(truncated > 1);
    assert_eq!(all["chunks"][0]["sequence"].as_u64().unwrap(), truncated);
    assert!(text(&all["chunks"]).ends_with("line-200\n"));
    assert!(text(&all["chunks"]).len() <= 256);
}

#[tokio::test]
async fn a_tty_process_has_a_terminal_takes_input_and_resizes() {
    let fixture = fixture().await;
    let id = start(
        &fixture.server,
        json!({
            "shell_command": "tty; read line; echo got:$line; stty size",
            "tty": { "rows": 24, "cols": 80 },
        }),
    )
    .await;
    call(
        &fixture.server,
        method::PROCESS_RESIZE,
        json!({ "process_id": id, "rows": 40, "cols": 100 }),
    )
    .await
    .unwrap();
    call(
        &fixture.server,
        method::PROCESS_INPUT,
        json!({ "process_id": id, "data": STANDARD.encode("hello\n") }),
    )
    .await
    .unwrap();
    let (last, output) = read_to_exit(&fixture.server, &id).await;
    assert_eq!(last["exit_code"], 0, "{output}");
    assert!(output.contains("/dev/"), "{output}");
    assert!(output.contains("got:hello"), "{output}");
    assert!(output.contains("40 100"), "{output}");
    let error = call(
        &fixture.server,
        method::PROCESS_RESIZE,
        json!({ "process_id": "nope", "rows": 1, "cols": 1 }),
    )
    .await
    .unwrap_err();
    assert_eq!(error.kind, kind::PROCESS_NOT_FOUND);
}

fn digest(data: &[u8]) -> String {
    format!("sha256:{}", hex::encode(Sha256::digest(data)))
}

#[tokio::test]
async fn a_chunked_upload_lands_whole_and_verified() {
    let fixture = fixture().await;
    let path = fixture.root.join("deep/dir/file.txt");
    let path = path.to_str().unwrap();
    let first = call(
        &fixture.server,
        method::FILE_WRITE,
        json!({ "path": path, "upload_id": "u1", "offset": 0, "data": STANDARD.encode("hello "), "final": false }),
    )
    .await
    .unwrap();
    assert_eq!(first, json!({}));
    // Nobody reading the path sees half a file.
    assert!(!Path::new(path).exists());
    let stat = call(
        &fixture.server,
        method::FILE_WRITE,
        json!({
            "path": path, "upload_id": "u1", "offset": 6, "data": STANDARD.encode("world"),
            "final": true, "expected_sha256": digest(b"hello world"),
        }),
    )
    .await
    .unwrap();
    assert_eq!(std::fs::read_to_string(path).unwrap(), "hello world");
    assert_eq!(stat["sha256"], digest(b"hello world"));
    assert_eq!(stat["kind"], "file");
    assert_eq!(stat["size_bytes"], 11);
    let listed = std::fs::read_dir(Path::new(path).parent().unwrap())
        .unwrap()
        .count();
    assert_eq!(listed, 1, "the temporary was left behind");

    let read = call(
        &fixture.server,
        method::FILE_READ,
        json!({ "path": path, "offset": 6, "length": 3 }),
    )
    .await
    .unwrap();
    assert_eq!(
        STANDARD.decode(read["data"].as_str().unwrap()).unwrap(),
        b"wor"
    );
    assert_eq!(read["eof"], false);
    let read = call(
        &fixture.server,
        method::FILE_READ,
        json!({ "path": path, "offset": 6, "length": 100 }),
    )
    .await
    .unwrap();
    assert_eq!(read["eof"], true);
    let error = call(
        &fixture.server,
        method::FILE_READ,
        json!({ "path": path, "length": 2 * 1024 * 1024 }),
    )
    .await
    .unwrap_err();
    assert_eq!(error.kind, kind::TOO_LARGE);
}

#[tokio::test]
async fn a_digest_mismatch_leaves_nothing_behind() {
    let fixture = fixture().await;
    let path = fixture.root.join("file.txt");
    std::fs::write(&path, "original").unwrap();
    let error = call(
        &fixture.server,
        method::FILE_WRITE,
        json!({
            "path": path, "upload_id": "u2", "data": STANDARD.encode("changed"),
            "final": true, "expected_sha256": digest(b"something else"),
        }),
    )
    .await
    .unwrap_err();
    assert_eq!(error.kind, kind::DIGEST_MISMATCH);
    assert_eq!(std::fs::read_to_string(&path).unwrap(), "original");
    assert_eq!(std::fs::read_dir(&fixture.root).unwrap().count(), 1);
}

#[tokio::test]
async fn an_abandoned_upload_is_swept() {
    let uploads = super::files::Uploads::default();
    let directory = tempfile::tempdir().unwrap();
    let policy = super::paths::PathPolicy::new(directory.path(), directory.path(), &[]).unwrap();
    super::files::file_write(
        &policy,
        &uploads,
        json!({ "path": "big.bin", "upload_id": "u3", "data": STANDARD.encode("part"), "final": false }),
    )
    .await
    .unwrap();
    assert_eq!(std::fs::read_dir(directory.path()).unwrap().count(), 1);
    uploads.sweep(super::files::UPLOAD_TTL);
    assert_eq!(std::fs::read_dir(directory.path()).unwrap().count(), 1);
    uploads.sweep(Duration::ZERO);
    assert_eq!(std::fs::read_dir(directory.path()).unwrap().count(), 0);
}

#[tokio::test]
async fn folders_are_made_listed_moved_and_deleted() {
    let fixture = fixture().await;
    let server = &fixture.server;
    call(server, method::FILE_MKDIR, json!({ "path": "a/b/c" }))
        .await
        .unwrap();
    std::fs::write(fixture.root.join("a/b/c/x.txt"), "x").unwrap();
    let listed = call(server, method::FILE_LIST, json!({ "path": "a/b/c" }))
        .await
        .unwrap();
    assert_eq!(listed["entries"].as_array().unwrap().len(), 1);
    assert_eq!(listed["entries"][0]["kind"], "file");
    let stat = call(server, method::FILE_STAT, json!({ "path": "a" }))
        .await
        .unwrap();
    assert_eq!(stat["kind"], "directory");
    call(
        server,
        method::FILE_MOVE,
        json!({ "source": "a/b/c/x.txt", "destination": "moved/y.txt" }),
    )
    .await
    .unwrap();
    assert!(fixture.root.join("moved/y.txt").exists());
    let error = call(server, method::FILE_DELETE, json!({ "path": "a" }))
        .await
        .unwrap_err();
    assert_eq!(error.kind, kind::IS_A_DIRECTORY);
    let deleted = call(
        server,
        method::FILE_DELETE,
        json!({ "path": "a", "recursive": true }),
    )
    .await
    .unwrap();
    assert_eq!(deleted["existed"], true);
    let deleted = call(server, method::FILE_DELETE, json!({ "path": "a" }))
        .await
        .unwrap();
    assert_eq!(deleted["existed"], false);
    let error = call(server, method::FILE_STAT, json!({ "path": "a" }))
        .await
        .unwrap_err();
    assert_eq!(error.kind, kind::NOT_FOUND);
}

#[tokio::test]
async fn nothing_outside_the_workspace_is_reachable_by_path_dot_dot_or_link() {
    let fixture = fixture().await;
    let server = &fixture.server;
    std::fs::write(fixture.outside.join("secret"), "s").unwrap();
    std::os::unix::fs::symlink(&fixture.outside, fixture.root.join("escape")).unwrap();
    let outside = fixture.outside.join("secret");
    for (method, params) in [
        (method::FILE_READ, json!({ "path": outside })),
        (
            method::FILE_READ,
            json!({ "path": "../../../../outside/secret" }),
        ),
        (method::FILE_READ, json!({ "path": "escape/secret" })),
        (
            method::FILE_WRITE,
            json!({ "path": "escape/new", "upload_id": "u", "data": "", "final": true }),
        ),
        (method::FILE_MKDIR, json!({ "path": "escape/dir" })),
        (
            method::FILE_DELETE,
            json!({ "path": outside, "recursive": true }),
        ),
        (
            method::SECRET_DELIVER,
            json!({ "path": "escape/key", "data": STANDARD.encode("k") }),
        ),
        (
            method::PROCESS_START,
            json!({ "shell_command": "true", "cwd": fixture.outside }),
        ),
    ] {
        let error = call(server, method, params.clone()).await.unwrap_err();
        assert_eq!(error.kind, kind::OUTSIDE_WORKSPACE, "{method} {params}");
    }
    assert!(!fixture.outside.join("new").exists());
    assert!(fixture.outside.join("secret").exists());
    // The link itself is the workspace's, and can go.
    let deleted = call(server, method::FILE_DELETE, json!({ "path": "escape" }))
        .await
        .unwrap();
    assert_eq!(deleted["existed"], true);
    assert!(fixture.outside.join("secret").exists());
}

#[tokio::test]
async fn a_secret_is_owner_only() {
    use std::os::unix::fs::PermissionsExt;
    let fixture = fixture().await;
    call(
        &fixture.server,
        method::SECRET_DELIVER,
        json!({ "path": "creds/token", "data": STANDARD.encode("t0k3n") }),
    )
    .await
    .unwrap();
    let file = fixture.root.join("creds/token");
    assert_eq!(std::fs::read_to_string(&file).unwrap(), "t0k3n");
    let mode = |path: &Path| std::fs::metadata(path).unwrap().permissions().mode() & 0o777;
    assert_eq!(mode(&file), 0o600);
    assert_eq!(mode(&fixture.root.join("creds")), 0o700);
}

#[tokio::test]
async fn closing_a_workspace_kills_what_runs_in_it() {
    let fixture = fixture().await;
    let id = start(&fixture.server, json!({ "argv": ["/bin/sleep", "60"] })).await;
    let listed = call(&fixture.server, method::PROCESS_LIST, json!({}))
        .await
        .unwrap();
    assert_eq!(listed["processes"][0]["process_id"], id);
    call(&fixture.server, method::WORKSPACE_CLOSE, json!({}))
        .await
        .unwrap();
    let error = call(&fixture.server, method::PROCESS_LIST, json!({}))
        .await
        .unwrap_err();
    assert_eq!(error.kind, kind::WORKSPACE_NOT_OPEN);
}

/// Every method and failure kind here is one the backend's provider knows,
/// and the other way round: both sides are held to the same fixture.
#[test]
fn the_ops_vocabulary_matches_the_contract() {
    let raw = std::fs::read_to_string(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/fixtures/wire_contract.json"
    ))
    .unwrap();
    let contract: Value = serde_json::from_str(&raw).unwrap();
    let sorted = |values: &[&str]| {
        let mut values: Vec<String> = values.iter().map(|value| (*value).to_owned()).collect();
        values.sort();
        values
    };
    let listed = |key: &str| {
        let mut values: Vec<String> = contract["host_execution"][key]
            .as_array()
            .unwrap_or_else(|| panic!("host_execution.{key} is missing"))
            .iter()
            .map(|value| value.as_str().unwrap().to_owned())
            .collect();
        values.sort();
        values
    };
    assert_eq!(sorted(&method::ALL), listed("methods"));
    assert_eq!(sorted(&kind::ALL), listed("failure_kinds"));
    assert_eq!(
        contract["host_execution"]["error_code"],
        super::wire::OP_FAILED
    );
    assert_eq!(
        contract["host_execution"]["max_data_bytes"],
        super::wire::OP_MAX_DATA_BYTES
    );
}
