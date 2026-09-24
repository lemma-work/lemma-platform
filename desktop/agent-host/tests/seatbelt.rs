//! The host-execution Seatbelt profile, proven with real processes.
//!
//! The profile is data, and data that is wrong fails open or fails closed
//! without anyone noticing: a missing deny leaks `~/.ssh`, a missing allow
//! breaks `git commit`. Each rule that matters is exercised here, under
//! `sandbox-exec` with the exact profile the binary embeds. See
//! docs/architecture/desktop-host-execution.md §6 and §8.
//!
//! `HOME` is a parameter, so these run against a home folder the test makes,
//! with a fake `.ssh` in it -- never the real one. It lives under Cargo's
//! target directory rather than `$TMPDIR`, because the profile lets commands
//! write anywhere in `$TMPDIR`, and a home inside it would make "writing to
//! the home folder is denied" untestable.

#![cfg(target_os = "macos")]

use std::io::{Read, Write};
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::sync::Arc;

use lemma_agent_host::host_exec::relay::{ExecRelay, ProcessLauncher, RelayPaths};
use lemma_agent_host::host_exec::seatbelt::{Confinement, SANDBOX_EXEC};
use lemma_agent_host::link::OpHandler;
use lemma_agent_host::link::protocol::OpBody;
use serde_json::{Value, json};

struct Sandbox {
    _directory: tempfile::TempDir,
    home: PathBuf,
    root: PathBuf,
    confinement: Confinement,
}

fn sandbox() -> Sandbox {
    let directory = tempfile::Builder::new()
        .prefix("seatbelt-")
        .tempdir_in(env!("CARGO_TARGET_TMPDIR"))
        .unwrap();
    let base = std::fs::canonicalize(directory.path()).unwrap();
    let home = base.join("home");
    let root = home.join("lemma/c/2026-09-25/seatbelt");
    std::fs::create_dir_all(home.join(".ssh")).unwrap();
    std::fs::write(home.join(".ssh/id_ed25519"), "PRIVATE KEY").unwrap();
    std::fs::create_dir_all(&root).unwrap();
    let tmp = std::fs::canonicalize(std::env::temp_dir()).unwrap();
    Sandbox {
        confinement: Confinement {
            root: root.clone(),
            home: home.clone(),
            tmp,
            grants: Vec::new(),
        },
        _directory: directory,
        home,
        root,
    }
}

impl Sandbox {
    /// `script` under the profile, with bash, in the root.
    fn run(&self, script: &str) -> Output {
        Command::new(SANDBOX_EXEC)
            .args(self.confinement.sandbox_arguments())
            .args(["/bin/bash", "-c", script])
            .current_dir(&self.root)
            .env("HOME", &self.home)
            .output()
            .expect("sandbox-exec runs")
    }

    fn succeeds(&self, script: &str) -> String {
        let output = self.run(script);
        assert!(
            output.status.success(),
            "{script} failed under the profile: {}",
            String::from_utf8_lossy(&output.stderr)
        );
        String::from_utf8_lossy(&output.stdout).into_owned()
    }

    fn is_denied(&self, script: &str) {
        let output = self.run(script);
        let stderr = String::from_utf8_lossy(&output.stderr);
        assert!(!output.status.success(), "{script} was allowed");
        assert!(
            stderr.contains("Operation not permitted"),
            "{script} failed, but not by a Seatbelt denial: {stderr}"
        );
    }
}

#[test]
fn credentials_in_the_home_folder_cannot_be_read() {
    let sandbox = sandbox();
    sandbox.is_denied("cat ~/.ssh/id_ed25519");
    sandbox.is_denied("ls ~/.ssh");
    // Neither by way of a link the command makes in its own root.
    sandbox.is_denied("ln -sf ~/.ssh/id_ed25519 ./key && cat ./key");
}

#[test]
fn the_home_folder_is_not_writable() {
    let sandbox = sandbox();
    sandbox.is_denied("touch ~/x");
    sandbox.is_denied("echo evil >> ~/.zshrc");
    sandbox.is_denied("mkdir -p ~/Library/LaunchAgents && touch ~/Library/LaunchAgents/x.plist");
    assert!(!sandbox.home.join("x").exists());
}

#[test]
fn the_root_and_package_caches_are_writable() {
    let sandbox = sandbox();
    sandbox.succeeds("echo made > made.txt && mkdir -p deep/er && touch deep/er/f");
    assert_eq!(
        std::fs::read_to_string(sandbox.root.join("made.txt")).unwrap(),
        "made\n"
    );
    sandbox.succeeds("mkdir -p ~/.npm/_cacache && touch ~/.npm/_cacache/entry");
    assert!(sandbox.home.join(".npm/_cacache/entry").exists());
    sandbox.succeeds("f=$(mktemp) && echo t > \"$f\" && rm \"$f\"");
}

#[test]
fn git_can_init_and_commit_in_the_root() {
    let sandbox = sandbox();
    let log = sandbox.succeeds(
        "git init -q repo && cd repo && echo hi > a.txt && git add a.txt \
         && git -c user.name=Lemma -c user.email=agent@lemma.invalid commit -qm first \
         && git log --oneline",
    );
    assert!(log.contains("first"), "{log}");
}

#[test]
fn loopback_is_reachable() {
    let sandbox = sandbox();
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    let server = std::thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        let mut request = [0_u8; 1024];
        let _ = stream.read(&mut request);
        stream
            .write_all(b"HTTP/1.0 200 OK\r\nContent-Length: 8\r\n\r\nloopback")
            .unwrap();
    });
    let body = sandbox.succeeds(&format!("curl -s http://127.0.0.1:{port}/"));
    assert_eq!(body, "loopback");
    server.join().unwrap();
}

#[test]
fn a_granted_folder_is_writable_and_an_ungranted_one_is_not() {
    let mut sandbox = sandbox();
    let granted = sandbox.home.join("project");
    let other = sandbox.home.join("other");
    std::fs::create_dir_all(&granted).unwrap();
    std::fs::create_dir_all(&other).unwrap();
    sandbox.confinement.grants = vec![granted.clone()];
    sandbox.succeeds(&format!("touch {}/ok", granted.display()));
    sandbox.is_denied(&format!("touch {}/no", other.display()));
}

async fn op(relay: &ExecRelay, method: &str, params: Value) -> Result<Value, String> {
    relay
        .handle(OpBody {
            workspace: "seatbelt".into(),
            method: method.into(),
            params,
            deadline_ms: Some(30_000),
        })
        .await
        .map_err(|failure| format!("{}: {}", failure.kind, failure.message))
}

async fn run_to_exit(relay: &ExecRelay, command: &str) -> (i64, String) {
    run_with(relay, json!({ "shell_command": command })).await
}

async fn run_with(relay: &ExecRelay, params: Value) -> (i64, String) {
    let command = params.to_string();
    let started = op(relay, "process.start", params).await.unwrap();
    let mut output = String::new();
    let mut after = 0;
    for _ in 0..60 {
        let read = op(
            relay,
            "process.read",
            json!({ "process_id": started["process_id"], "after_sequence": after, "wait_ms": 1000 }),
        )
        .await
        .unwrap();
        for chunk in read["chunks"].as_array().unwrap() {
            use base64::Engine;
            let data = base64::engine::general_purpose::STANDARD
                .decode(chunk["data"].as_str().unwrap())
                .unwrap();
            output.push_str(&String::from_utf8_lossy(&data));
        }
        after = read["next_sequence"].as_u64().unwrap() - 1;
        if read["state"] != "running" {
            return (read["exit_code"].as_i64().unwrap_or(-1), output);
        }
    }
    panic!("{command} never finished: {output}");
}

/// The real thing, end to end: the exec-server binary started under
/// `sandbox-exec` by the relay, and commands run through ops.
#[tokio::test]
async fn the_exec_server_runs_confined() {
    let sandbox = sandbox();
    let data = sandbox.home.join("agent-host-data");
    std::fs::create_dir_all(&data).unwrap();
    let relay = ExecRelay::new(
        Arc::new(ProcessLauncher {
            executable: PathBuf::from(env!("CARGO_BIN_EXE_lemma-agent-host")),
            data_root: data,
            sandboxed: true,
        }),
        RelayPaths {
            root_base: sandbox.home.join("lemma"),
            home: sandbox.home.clone(),
            tmp: sandbox.confinement.tmp.clone(),
            folders: sandbox.home.join("no-folders.json"),
        },
    );
    relay.set_enabled(true);
    let opened = op(
        &relay,
        "workspace.open",
        json!({ "slug": "seatbelt", "date": "2026-09-25" }),
    )
    .await
    .unwrap();
    assert_eq!(opened["root"], json!(sandbox.root));
    assert_eq!(opened["platform"], "macos");

    let (code, output) = run_to_exit(
        &relay,
        "git init -q r && cd r && touch a && git add a \
         && git -c user.name=L -c user.email=l@l.invalid commit -qm c && echo committed",
    )
    .await;
    assert_eq!(code, 0, "{output}");
    assert!(output.contains("committed"));

    // A terminal is allocated inside the sandbox, too.
    let (code, output) = run_with(
        &relay,
        json!({ "shell_command": "tty", "tty": { "rows": 24, "cols": 80 } }),
    )
    .await;
    assert_eq!(code, 0, "{output}");
    assert!(output.contains("/dev/ttys"), "{output}");

    let (code, output) = run_to_exit(&relay, "cat ~/.ssh/id_ed25519").await;
    assert_ne!(code, 0);
    assert!(output.contains("Operation not permitted"), "{output}");
    assert!(!output.contains("PRIVATE KEY"));

    // The path check answers first, in words the agent can act on.
    let refused = op(
        &relay,
        "file.read",
        json!({ "path": sandbox.home.join(".ssh/id_ed25519") }),
    )
    .await
    .unwrap_err();
    assert!(refused.starts_with("outside_workspace"), "{refused}");

    op(&relay, "workspace.close", json!({})).await.unwrap();
    assert!(Path::new(&sandbox.root).join("r/.git").is_dir());
}
