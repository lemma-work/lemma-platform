//! The link against a stand-in for Lemma's end of it.

use std::time::Duration;

use serde_json::json;
use uuid::Uuid;

use super::protocol::{close, server};
use super::stub::StubLink;
use super::*;
use crate::protocol::{Command, CommandKind, HostCapacity};

fn capacity() -> HostCapacity {
    HostCapacity {
        max_runs: 1,
        active_runs: 0,
        available_runs: 1,
    }
}

async fn connected(stub: &StubLink) -> Connected {
    connect(
        &stub.url,
        "secret",
        HostHello::current("installation"),
        capacity(),
    )
    .await
    .expect("the stand-in welcomes every hello")
}

/// Pairing is the one exchange without a secret, because it is the exchange
/// that issues one, and it leaves nothing open behind it.
#[tokio::test]
async fn pairing_returns_a_usable_pairing() {
    let stub = StubLink::start().await;
    let target = pair(stub.url.clone(), "code", "This Mac", "installation", true)
        .await
        .unwrap();
    assert_eq!(target.host_secret, "stub-secret");
    assert_eq!(target.base_url, stub.url);
    assert!(target.enabled);
}

/// Plain HTTP is only for this machine's own workspace.
#[tokio::test]
async fn pairing_refuses_plain_http_to_another_machine() {
    let result = pair(
        Url::parse("http://lemma.example").unwrap(),
        "code",
        "This Mac",
        "installation",
        true,
    )
    .await;
    assert!(result.is_err());
}

/// A request is answered on the same link, matched by id, however many are in
/// flight.
#[tokio::test]
async fn concurrent_requests_each_get_their_own_answer() {
    let stub = StubLink::start().await;
    stub.state
        .mcp_answers
        .lock()
        .unwrap()
        .insert("tools/list".into(), json!({ "tools": [] }));
    let link = connected(&stub).await.handle;
    let body = |method: &str| super::protocol::McpBody {
        run_id: Uuid::new_v4(),
        conversation_id: Uuid::new_v4(),
        token: "token".into(),
        method: method.into(),
        params: json!({}),
    };
    let (list, call) = (body("tools/list"), body("tools/call"));
    let (listed, called) = tokio::join!(link.mcp(&list), link.mcp(&call));
    assert_eq!(listed.unwrap(), json!({ "tools": [] }));
    assert_eq!(called.unwrap(), json!({}));
    assert_eq!(stub.state.mcp_requests.lock().unwrap().len(), 2);
}

/// Commands arrive without being asked for: the push is what makes a Stop
/// reach the host in milliseconds instead of on the next poll.
#[tokio::test]
async fn pushed_commands_reach_the_host() {
    let stub = StubLink::start().await;
    let mut connected = connected(&stub).await;
    let command = Command {
        command_id: Uuid::new_v4(),
        kind: CommandKind::CancelRun,
        created_at: chrono::Utc::now(),
        expires_at: chrono::Utc::now() + chrono::Duration::minutes(1),
        run_id: Some(Uuid::new_v4()),
        lease_epoch: Some(1),
        payload: serde_json::Value::Null,
    };
    assert!(
        stub.state
            .push(server::COMMANDS, json!({ "commands": [command.clone()] }))
    );
    let push = tokio::time::timeout(Duration::from_secs(5), connected.pushes.recv())
        .await
        .expect("the push must arrive")
        .expect("the link is still open");
    let Push::Commands(commands) = push else {
        panic!("expected commands, got {push:?}");
    };
    assert_eq!(commands[0].command_id, command.command_id);
}

/// Lemma asking the host to come back later is carried through, so a deploy
/// does not reconnect every host at the same instant.
#[tokio::test]
async fn a_reconnect_push_carries_its_delay() {
    let stub = StubLink::start().await;
    let mut connected = connected(&stub).await;
    assert!(
        stub.state
            .push(server::RECONNECT, json!({ "after_ms": 1234 }))
    );
    let push = tokio::time::timeout(Duration::from_secs(5), connected.pushes.recv())
        .await
        .unwrap()
        .unwrap();
    assert!(matches!(push, Push::Reconnect(after) if after == Duration::from_millis(1234)));
}

/// How Lemma turns a host away decides what the host does next, so the close
/// code has to survive the handshake intact.
#[tokio::test]
async fn a_refused_hello_says_why() {
    for (code, check) in [
        (
            close::REVOKED_OR_MISSING,
            LinkError::is_revoked_or_missing as fn(&LinkError) -> bool,
        ),
        (close::INVALID_CREDENTIAL, LinkError::is_invalid_credential),
        (close::UPGRADE_REQUIRED, LinkError::is_upgrade_required),
    ] {
        let stub = StubLink::start().await;
        *stub.state.refuse_hello_with.lock().unwrap() = Some(code);
        let error = connect(
            &stub.url,
            "secret",
            HostHello::current("installation"),
            capacity(),
        )
        .await
        .err()
        .expect("the hello was refused");
        assert!(check(&error), "close {code} read as {error:?}");
    }
}

/// Everything waiting on a link learns that it went, rather than hanging.
#[tokio::test]
async fn a_closed_link_fails_what_is_waiting_on_it() {
    let stub = StubLink::start().await;
    let link = connected(&stub).await.handle;
    stub.stop();
    link.close(close::NORMAL, "test");
    let error = tokio::time::timeout(Duration::from_secs(5), link.closed())
        .await
        .expect("closing must be noticed");
    assert!(!error.is_revoked_or_missing());
    assert!(link.is_closed());
    let result = link.control(&super::protocol::ControlBody::default()).await;
    assert!(result.is_err());
}

/// The slot hands a waiting task the next link to open, and never a closed one.
#[tokio::test]
async fn the_slot_waits_for_an_open_link() {
    let stub = StubLink::start().await;
    let (owner, mut slot) = LinkSlotOwner::new();
    assert!(slot.now().is_none());
    let waiting = tokio::spawn(async move { slot.wait().await.is_some() });
    tokio::time::sleep(Duration::from_millis(20)).await;
    owner.set(Some(connected(&stub).await.handle));
    assert!(
        tokio::time::timeout(Duration::from_secs(5), waiting)
            .await
            .unwrap()
            .unwrap()
    );
}

/// Closing the link from the host's end ends the connection Lemma holds,
/// rather than leaving it open until a heartbeat lapses.
#[tokio::test]
async fn closing_the_link_ends_the_connection_lemma_holds() {
    let stub = StubLink::start().await;
    let link = connected(&stub).await.handle;
    assert!(stub.state.connected());
    link.close(close::NORMAL, "test");
    tokio::time::timeout(Duration::from_secs(5), async {
        while stub.state.connected() {
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .expect("the stand-in must see the host go");
}

/// A link opened without a handler -- pairing, revocation -- still answers an
/// `op`, so Lemma is never left waiting on one.
#[tokio::test]
async fn an_op_on_a_link_that_runs_nothing_is_answered_unavailable() {
    let stub = StubLink::start().await;
    let _link = connected(&stub).await;
    assert!(stub.state.request(
        server::OP,
        "s1",
        json!({ "workspace": "w", "method": "process.list", "params": {} }),
    ));
    let answer = stub.state.answer("s1").await;
    assert_eq!(answer.kind, super::protocol::host::ERROR);
    assert_eq!(answer.body["code"], "OP_FAILED");
    assert_eq!(answer.body["detail"]["kind"], "exec_server_unavailable");
    assert_eq!(answer.body["retryable"], true);
}

/// The hello says whether Lemma may route an owner's commands here.
#[tokio::test]
async fn the_hello_carries_host_execution() {
    let stub = StubLink::start().await;
    let status = crate::host_exec::wire::HostExecutionStatus::current(true);
    let _link = connect_with(
        &stub.url,
        "secret",
        super::protocol::HelloBody {
            hello: HostHello::current("installation"),
            capacity: capacity(),
            host_execution: Some(status.clone()),
        },
        None,
    )
    .await
    .unwrap();
    let reports = stub.state.host_execution_reports.lock().unwrap().clone();
    assert_eq!(reports, [serde_json::to_value(status).unwrap()]);
}

#[cfg(unix)]
mod host_execution {
    use std::sync::Arc;
    use std::sync::atomic::Ordering;

    use base64::Engine;
    use base64::engine::general_purpose::STANDARD;
    use serde_json::{Value, json};

    use super::super::protocol::{Frame, HelloBody, host, server};
    use super::super::stub::StubLink;
    use super::super::{Connected, connect_with};
    use super::capacity;
    use crate::host_exec::relay::{ExecRelay, InProcessLauncher, RelayPaths};
    use crate::protocol::HostHello;

    struct Setup {
        stub: StubLink,
        relay: Arc<ExecRelay>,
        launcher: Arc<InProcessLauncher>,
        _link: Connected,
        directory: tempfile::TempDir,
        next: std::sync::atomic::AtomicU64,
    }

    async fn setup(enabled: bool) -> Setup {
        let directory = tempfile::tempdir().unwrap();
        let home = directory.path().join("home");
        let tmp = directory.path().join("tmp");
        std::fs::create_dir_all(home.join("lemma")).unwrap();
        std::fs::create_dir_all(&tmp).unwrap();
        let launcher = Arc::new(InProcessLauncher::default());
        let relay = ExecRelay::new(
            launcher.clone(),
            RelayPaths {
                root_base: home.join("lemma"),
                cache: directory.path().join("cache"),
                home,
                tmp,
                folders: directory.path().join("conversation-folders.json"),
                roots: directory.path().join("conversation-roots.json"),
                target: uuid::Uuid::from_u128(1),
            },
        );
        relay.set_enabled(enabled);
        let stub = StubLink::start().await;
        let link = connect_with(
            &stub.url,
            "secret",
            HelloBody {
                hello: HostHello::current("installation"),
                capacity: capacity(),
                host_execution: None,
            },
            Some(relay.clone()),
        )
        .await
        .unwrap();
        Setup {
            stub,
            relay,
            launcher,
            _link: link,
            directory,
            next: std::sync::atomic::AtomicU64::new(1),
        }
    }

    impl Setup {
        async fn op(&self, workspace: &str, method: &str, params: Value) -> Frame {
            let id = format!("s{}", self.next.fetch_add(1, Ordering::SeqCst));
            assert!(self.stub.state.request(
                server::OP,
                &id,
                json!({
                    "workspace": workspace,
                    "method": method,
                    "params": params,
                    "deadline_ms": 20_000,
                }),
            ));
            self.stub.state.answer(&id).await
        }

        async fn ok(&self, method: &str, params: Value) -> Value {
            let answer = self.op("w", method, params).await;
            assert_eq!(answer.kind, host::OP_OK, "{method}: {}", answer.body);
            answer.body["result"].clone()
        }

        async fn failure_kind(&self, workspace: &str, method: &str, params: Value) -> String {
            let answer = self.op(workspace, method, params).await;
            assert_eq!(answer.kind, host::ERROR, "{method}: {}", answer.body);
            assert_eq!(answer.body["code"], "OP_FAILED");
            answer.body["detail"]["kind"].as_str().unwrap().to_owned()
        }
    }

    fn output(read: &Value) -> String {
        read["chunks"]
            .as_array()
            .unwrap()
            .iter()
            .map(|chunk| {
                String::from_utf8(STANDARD.decode(chunk["data"].as_str().unwrap()).unwrap())
                    .unwrap()
            })
            .collect()
    }

    /// `op` in, exec-server, `op_ok` out: a command runs in the workspace's
    /// root and its output comes back through the link.
    #[tokio::test]
    async fn an_op_runs_a_command_and_answers_op_ok() {
        let setup = setup(true).await;
        let opened = setup
            .ok(
                "workspace.open",
                json!({ "slug": "linked", "date": "2026-09-25" }),
            )
            .await;
        let root = opened["root"].as_str().unwrap().to_owned();
        assert!(root.ends_with("lemma/c/2026-09-25/linked"), "{root}");
        let started = setup
            .ok(
                "process.start",
                json!({ "shell_command": "echo from-the-host" }),
            )
            .await;
        let process_id = started["process_id"].clone();
        let mut text = String::new();
        for _ in 0..20 {
            let read = setup
                .ok(
                    "process.read",
                    json!({ "process_id": process_id, "after_sequence": 0, "wait_ms": 1000 }),
                )
                .await;
            text = output(&read);
            if read["state"] == "exited" {
                break;
            }
        }
        assert_eq!(text, "from-the-host\n");
        assert_eq!(setup.relay.open_workspaces().await, 1);
        setup.ok("workspace.close", json!({})).await;
        assert_eq!(setup.relay.open_workspaces().await, 0);
    }

    /// A long-waiting read does not hold up the ops behind it: each op is its
    /// own task, and the reader never waits on one.
    #[tokio::test]
    async fn a_waiting_read_does_not_hold_up_other_ops() {
        let setup = Arc::new(setup(true).await);
        setup.ok("workspace.open", json!({ "slug": "busy" })).await;
        let started = setup
            .ok("process.start", json!({ "argv": ["/bin/sleep", "30"] }))
            .await;
        let waiting = {
            let setup = Arc::clone(&setup);
            let process_id = started["process_id"].clone();
            tokio::spawn(async move {
                setup
                    .ok(
                        "process.read",
                        json!({ "process_id": process_id, "after_sequence": 0, "wait_ms": 5000 }),
                    )
                    .await
            })
        };
        tokio::time::sleep(std::time::Duration::from_millis(100)).await;
        let quick = std::time::Instant::now();
        setup.ok("process.list", json!({})).await;
        assert!(quick.elapsed() < std::time::Duration::from_secs(2));
        assert!(!waiting.is_finished());
        setup
            .ok(
                "process.terminate",
                json!({ "process_id": started["process_id"], "grace_ms": 100 }),
            )
            .await;
        waiting.await.unwrap();
    }

    /// Turned off, the host refuses to run anything, and says so in a way the
    /// provider can tell apart from a failed command.
    #[tokio::test]
    async fn a_disabled_host_refuses_every_op() {
        let setup = setup(false).await;
        let kind = setup
            .failure_kind("w", "workspace.open", json!({ "slug": "x" }))
            .await;
        assert_eq!(kind, "exec_server_unavailable");
        assert_eq!(setup.launcher.launches.load(Ordering::SeqCst), 0);
    }

    /// Turning it off stops what is already running.
    #[tokio::test]
    async fn turning_it_off_closes_every_workspace() {
        let setup = setup(true).await;
        setup.ok("workspace.open", json!({ "slug": "x" })).await;
        assert_eq!(setup.relay.open_workspaces().await, 1);
        setup.relay.set_enabled(false);
        for _ in 0..100 {
            if setup.relay.open_workspaces().await == 0 {
                break;
            }
            tokio::time::sleep(std::time::Duration::from_millis(10)).await;
        }
        assert_eq!(setup.relay.open_workspaces().await, 0);
        let kind = setup.failure_kind("w", "process.list", json!({})).await;
        assert_eq!(kind, "exec_server_unavailable");
    }

    #[tokio::test]
    async fn an_op_for_a_workspace_nobody_opened_says_so() {
        let setup = setup(true).await;
        let kind = setup.failure_kind("other", "process.list", json!({})).await;
        assert_eq!(kind, "workspace_not_open");
    }

    /// An exec-server that dies is restarted and its workspace reopened. The
    /// commands it ran died with it, and saying `process_not_found` is the
    /// truth about them.
    #[tokio::test]
    async fn an_exec_server_that_exits_is_restarted_with_its_workspace() {
        let setup = setup(true).await;
        setup
            .ok("workspace.open", json!({ "slug": "crashy" }))
            .await;
        let started = setup
            .ok("process.start", json!({ "argv": ["/bin/sleep", "30"] }))
            .await;
        assert_eq!(setup.launcher.launches.load(Ordering::SeqCst), 1);
        setup.launcher.crash_all();
        let mut listed = None;
        for _ in 0..100 {
            let answer = setup.op("w", "process.list", json!({})).await;
            if answer.kind == host::OP_OK {
                listed = Some(answer.body["result"].clone());
                break;
            }
            assert_eq!(
                answer.body["detail"]["kind"], "exec_server_unavailable",
                "{}",
                answer.body
            );
            tokio::time::sleep(std::time::Duration::from_millis(50)).await;
        }
        let listed = listed.expect("the exec-server must come back");
        assert_eq!(listed["processes"], json!([]));
        assert_eq!(setup.launcher.launches.load(Ordering::SeqCst), 2);
        let kind = setup
            .failure_kind(
                "w",
                "process.read",
                json!({ "process_id": started["process_id"] }),
            )
            .await;
        assert_eq!(kind, "process_not_found");
    }

    /// A command outlives an exec-server that crashed -- it leads its own
    /// process group -- unless the relay, outside the sandbox, stops it.
    #[tokio::test]
    async fn what_a_crashed_exec_server_started_does_not_outlive_it() {
        let setup = setup(true).await;
        let opened = setup
            .ok("workspace.open", json!({ "slug": "orphans" }))
            .await;
        let pid_file = std::path::Path::new(opened["root"].as_str().unwrap()).join("pid");
        let started = setup
            .ok(
                "process.start",
                json!({ "shell_command": format!("echo $$ > {}; exec sleep 30", pid_file.display()) }),
            )
            .await;
        assert!(started.get("group").is_none(), "{started}");
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
        let pid = loop {
            if let Some(pid) = std::fs::read_to_string(&pid_file)
                .ok()
                .and_then(|raw| raw.trim().parse::<i32>().ok())
            {
                break rustix::process::Pid::from_raw(pid).unwrap();
            }
            assert!(
                std::time::Instant::now() < deadline,
                "the command never started"
            );
            tokio::time::sleep(std::time::Duration::from_millis(20)).await;
        };
        setup.launcher.crash_all();
        while rustix::process::test_kill_process(pid).is_ok() {
            assert!(
                std::time::Instant::now() < deadline + std::time::Duration::from_secs(5),
                "the command outlived its exec-server"
            );
            tokio::time::sleep(std::time::Duration::from_millis(20)).await;
        }
    }

    /// A folder under `~/lemma` is given only to the paired workspace that
    /// owns it: not the directory registry, not a folder no run of this
    /// workspace claimed, and not one another workspace owns.
    #[tokio::test]
    async fn a_folder_under_the_base_is_given_only_to_the_workspace_that_owns_it() {
        let setup = setup(true).await;
        let base = std::fs::canonicalize(setup.directory.path().join("home/lemma")).unwrap();
        let unclaimed = base.join("c/2026-09-25/someone-elses");
        std::fs::create_dir_all(&unclaimed).unwrap();
        for hint in [base.join(".lemma"), unclaimed.clone()] {
            let opened = setup
                .ok(
                    "workspace.open",
                    json!({ "root_hint": hint, "slug": "mine", "date": "2026-09-25" }),
                )
                .await;
            assert_eq!(
                opened["root"],
                json!(base.join("c/2026-09-25/mine")),
                "{hint:?} was used"
            );
        }
        // The default folder is claimed for this workspace as it opens, so a
        // second paired workspace cannot open it.
        let taken = base.join("c/2026-09-25/taken");
        std::fs::create_dir_all(&taken).unwrap();
        crate::conversation_directory::claim_for_host_execution(
            &base,
            uuid::Uuid::from_u128(2),
            &taken,
        )
        .unwrap();
        let kind = setup
            .failure_kind(
                "w",
                "workspace.open",
                json!({ "slug": "taken", "date": "2026-09-25" }),
            )
            .await;
        assert_eq!(kind, "permission_denied");
        assert!(crate::conversation_directory::owned_by(
            &base,
            uuid::Uuid::from_u128(1),
            &base.join("c/2026-09-25/mine")
        ));
    }

    /// The backend naming a folder is not the owner choosing it: a root hint
    /// outside `~/lemma` that no conversation is bound to is not used.
    #[tokio::test]
    async fn a_root_the_owner_did_not_choose_is_not_used() {
        let setup = setup(true).await;
        let elsewhere = tempfile::tempdir().unwrap();
        let opened = setup
            .ok(
                "workspace.open",
                json!({ "root_hint": elsewhere.path(), "slug": "fallback", "date": "2026-09-25" }),
            )
            .await;
        assert!(
            opened["root"]
                .as_str()
                .unwrap()
                .ends_with("lemma/c/2026-09-25/fallback"),
            "{opened}"
        );
    }

    /// A folder the owner bound the conversation to on this machine is the
    /// root, and the commands run there.
    #[tokio::test]
    async fn a_folder_the_owner_bound_is_the_root() {
        let setup = setup(true).await;
        let project = tempfile::tempdir().unwrap();
        let conversation = uuid::Uuid::new_v4();
        std::fs::write(
            setup.directory.path().join("conversation-folders.json"),
            serde_json::to_vec(&json!({ conversation.to_string(): project.path() })).unwrap(),
        )
        .unwrap();
        let opened = setup
            .ok(
                "workspace.open",
                json!({ "root_hint": project.path(), "conversation_id": conversation }),
            )
            .await;
        assert_eq!(
            opened["root"].as_str().unwrap(),
            std::fs::canonicalize(project.path())
                .unwrap()
                .to_str()
                .unwrap()
        );
    }

    /// §5: the Mac remembers the folder a conversation opened in. A re-open --
    /// after a restart forgot every workspace -- lands there whatever day,
    /// slug or under-`~/lemma` hint Lemma sends now, and a default folder
    /// deleted in between is made again rather than replaced.
    #[tokio::test]
    async fn a_conversation_reopens_in_the_folder_it_already_opened() {
        let setup = setup(true).await;
        let conversation = uuid::Uuid::new_v4();
        let first = setup
            .ok(
                "workspace.open",
                json!({ "conversation_id": conversation, "slug": "first", "date": "2026-09-25" }),
            )
            .await["root"]
            .as_str()
            .unwrap()
            .to_owned();
        assert!(first.ends_with("lemma/c/2026-09-25/first"), "{first}");

        // A restart: nothing is open any more.
        setup.relay.close_all().await;
        assert_eq!(setup.relay.open_workspaces().await, 0);
        let elsewhere = setup.directory.path().join("home/lemma/c/2026-09-26/other");
        std::fs::create_dir_all(&elsewhere).unwrap();
        let reopened = setup
            .ok(
                "workspace.open",
                json!({
                    "conversation_id": conversation,
                    "slug": "renamed",
                    "date": "2026-09-26",
                    "root_hint": elsewhere,
                }),
            )
            .await;
        assert_eq!(reopened["root"].as_str().unwrap(), first);

        setup.relay.close_all().await;
        std::fs::remove_dir(&first).unwrap();
        let recreated = setup
            .ok("workspace.open", json!({ "conversation_id": conversation }))
            .await;
        assert_eq!(recreated["root"].as_str().unwrap(), first);
        assert!(std::path::Path::new(&first).is_dir());
    }

    /// The owner binding the conversation to a folder is the owner choosing:
    /// the next open goes there, remembered folder or not, and is remembered.
    #[tokio::test]
    async fn a_folder_the_owner_binds_later_wins_over_the_remembered_one() {
        let setup = setup(true).await;
        let conversation = uuid::Uuid::new_v4();
        setup
            .ok(
                "workspace.open",
                json!({ "conversation_id": conversation, "slug": "first" }),
            )
            .await;
        setup.relay.close_all().await;
        let project = tempfile::tempdir().unwrap();
        std::fs::write(
            setup.directory.path().join("conversation-folders.json"),
            serde_json::to_vec(&json!({ conversation.to_string(): project.path() })).unwrap(),
        )
        .unwrap();
        let canonical = std::fs::canonicalize(project.path()).unwrap();

        let opened = setup
            .ok(
                "workspace.open",
                json!({ "conversation_id": conversation, "root_hint": project.path() }),
            )
            .await;
        assert_eq!(
            opened["root"].as_str().unwrap(),
            canonical.to_str().unwrap()
        );

        // And it is what a later re-open without the hint finds.
        setup.relay.close_all().await;
        let again = setup
            .ok("workspace.open", json!({ "conversation_id": conversation }))
            .await;
        assert_eq!(again["root"].as_str().unwrap(), canonical.to_str().unwrap());
    }
}

/// Wait for the stand-in to be serving `count` sockets, or fail.
async fn until_open_sockets(stub: &StubLink, count: usize, what: &str) {
    tokio::time::timeout(Duration::from_secs(5), async {
        while stub.state.open_sockets() != count {
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .unwrap_or_else(|_| {
        panic!(
            "{what}: the stand-in still serves {} socket(s)",
            stub.state.open_sockets()
        )
    });
}

/// A connection nobody holds any more is closed, not left running.
///
/// The worker abandons a link whenever a request on it times out, and simply
/// drops what it held. The reader and writer were detached tasks, and the
/// reader kept the writer's channel alive, so neither ever ended: the socket
/// stayed open on Lemma's side, still counted as this host's connection.
#[tokio::test]
async fn dropping_a_connection_closes_its_socket() {
    let stub = StubLink::start().await;
    let connected = connected(&stub).await;
    until_open_sockets(&stub, 1, "after the handshake").await;
    drop(connected);
    until_open_sockets(&stub, 0, "after the host dropped the link").await;
}

/// Clones of the handle keep the link open; the last one to go closes it.
#[tokio::test]
async fn the_last_handle_to_go_closes_the_socket() {
    let stub = StubLink::start().await;
    let connected = connected(&stub).await;
    let kept = connected.handle.clone();
    drop(connected);
    tokio::time::sleep(Duration::from_millis(100)).await;
    assert_eq!(stub.state.open_sockets(), 1, "a live handle keeps its link");
    assert!(
        kept.control(&super::protocol::ControlBody::default())
            .await
            .is_ok()
    );
    drop(kept);
    until_open_sockets(&stub, 0, "after the last handle went").await;
}

/// A handshake Lemma refuses with an `error` frame leaves the socket open on
/// its side; the host has to hang up rather than abandon it.
#[tokio::test]
async fn a_failed_handshake_closes_its_socket() {
    let stub = StubLink::start().await;
    *stub.state.reject_hello.lock().unwrap() = true;
    let error = connect(
        &stub.url,
        "secret",
        HostHello::current("installation"),
        capacity(),
    )
    .await
    .err()
    .expect("the hello was refused");
    assert!(matches!(error, LinkError::Rejected { .. }), "{error:?}");
    until_open_sockets(&stub, 0, "after the refused handshake").await;
}

/// Closing from this side fails what is waiting at once, without waiting for
/// Lemma to echo the close -- a peer that has stopped answering never will.
#[tokio::test]
async fn closing_fails_waiters_without_an_echo() {
    let stub = StubLink::start().await;
    let link = connected(&stub).await.handle;
    let body = super::protocol::InteractionWaitBody {
        run_id: Uuid::new_v4(),
        conversation_id: Uuid::new_v4(),
        token: "token".into(),
        tool_call_id: "call".into(),
    };
    let waiting = {
        let link = link.clone();
        tokio::spawn(async move { link.interaction_wait(&body).await })
    };
    tokio::time::sleep(Duration::from_millis(50)).await;
    link.close(close::NORMAL, "abandoned");
    let result = tokio::time::timeout(Duration::from_secs(5), waiting)
        .await
        .expect("a waiter must learn the link is gone")
        .unwrap();
    assert!(result.is_err());
    assert!(link.is_closed());
}
