use super::{Daemon, SUBSCRIBER_BACKLOG};
use crate::paths::LocalPaths;
use serde_json::{json, Value};
use std::sync::mpsc;

/// A daemon over a throwaway root, with no host stack behind it.
///
/// `host_processes`, `managed_runtime` and `sharing` are all `Option`, so
/// the arms that are pure protocol -- authentication, unknown commands, the
/// stopping gate, the shapes of acks and errors -- can be driven without a
/// VM, a backend or a tunnel. Those arms had no test at all.
pub(super) fn daemon() -> (tempfile::TempDir, std::sync::Arc<Daemon>) {
    let root = tempfile::tempdir().unwrap();
    let daemon = Daemon::new(LocalPaths::new(root.path().join("locald")))
        .expect("a daemon over an empty root");
    (root, daemon)
}

/// Drive one command and collect everything the daemon said back.
fn exchange(daemon: &std::sync::Arc<Daemon>, request: Value) -> Vec<Value> {
    let (sender, receiver) = mpsc::sync_channel::<String>(SUBSCRIBER_BACKLOG);
    daemon.dispatch(request, &sender);
    drop(sender);
    receiver
        .into_iter()
        .map(|line| serde_json::from_str(&line).expect("every reply is JSON"))
        .collect()
}

#[test]
fn an_unknown_command_is_refused_by_name_rather_than_ignored() {
    let (_root, daemon) = daemon();
    let replies = exchange(&daemon, json!({"cmd": "not-a-command", "id": "abc"}));
    assert!(!replies.is_empty(), "a client must never be left waiting");
    let reply = &replies[replies.len() - 1];
    assert_eq!(reply["id"], "abc", "the answer must carry the request id");
    assert_eq!(reply["event"], "error");
}

/// A daemon that is stopping refuses new work, but must keep answering the
/// questions a client asks *because* it is stopping.
///
/// Six commands are exempt on purpose -- an app watching a shutdown needs
/// status and snapshots, and needs to be able to disconnect. Getting that
/// list wrong in either direction is bad: too narrow and the app goes blind
/// mid-quit, too wide and a start races the cleanup draining it.
#[test]
fn a_stopping_daemon_refuses_new_work_and_still_answers_observation() {
    let (_root, daemon) = daemon();
    assert!(daemon.lifecycle.request_shutdown());

    let refused = exchange(&daemon, json!({"cmd": "start", "id": "s1"}));
    let last = refused.last().expect("a refusal is still an answer");
    assert_eq!(last["event"], "error");
    assert_eq!(last["code"], "stopping");
    assert_eq!(last["id"], "s1");

    for observation in ["ping", "status", "control.snapshot", "agent-host.status"] {
        let replies = exchange(&daemon, json!({"cmd": observation, "id": observation}));
        let last = replies
            .last()
            .unwrap_or_else(|| panic!("{observation} must answer while stopping"));
        assert_ne!(
            last["code"], "stopping",
            "{observation} is what a client watching a shutdown depends on"
        );
    }
}

/// Every reply carries back the id it was asked with.
///
/// The app matches answers to requests by id; one arm returning an
/// unlabelled reply leaves that request outstanding for ever, which is a
/// spinner that never resolves rather than an error anybody can see.
#[test]
fn every_answer_carries_the_id_it_was_asked_with() {
    let (_root, daemon) = daemon();
    for command in [
        "ping",
        "status",
        "control.snapshot",
        "agent-host.status",
        "sharing.snapshot",
        "config.models",
        "not-a-command",
    ] {
        let replies = exchange(&daemon, json!({"cmd": command, "id": "carried"}));
        let last = replies
            .last()
            .unwrap_or_else(|| panic!("{command} answered nothing at all"));
        assert_eq!(
            last["id"], "carried",
            "{command} lost the id, so the app waits for ever: {last}"
        );
    }
}

/// A command with no id at all must still be answered rather than dropped.
#[test]
fn a_request_without_an_id_is_still_answered() {
    let (_root, daemon) = daemon();
    let replies = exchange(&daemon, json!({"cmd": "ping"}));
    assert!(
        !replies.is_empty(),
        "a missing id is not a reason to say nothing"
    );
}

/// Commands that need a host stack must say so, not panic or hang.
///
/// This daemon has no `sharing` and no `host_processes`, which is the shape
/// of a fresh install and of a Cloud-mode machine. Every one of these arms
/// reaches for a collaborator that is `None`.
#[test]
fn commands_that_need_a_stack_that_is_not_there_answer_rather_than_hang() {
    let (_root, daemon) = daemon();
    for command in ["sharing.snapshot", "sharing.preflight"] {
        let replies = exchange(&daemon, json!({"cmd": command, "id": command}));
        assert!(
            !replies.is_empty(),
            "{command} must answer even with no sharing controller"
        );
    }
}

/// An update that stopped while it was migrating must be said out loud on
/// the next start, not absorbed.
///
/// `alembic upgrade head` is forward-only, so the version about to start may
/// no longer be able to read its own database. Starting quietly is how that
/// becomes a support case instead of a prompt.
#[test]
fn an_interrupted_update_is_reported_when_the_daemon_next_starts() {
    let root = tempfile::tempdir().unwrap();
    let locald = root.path().join("locald");
    std::fs::create_dir_all(&locald).unwrap();

    let update =
        crate::update_transaction::UpdateTransaction::load(locald.join("update.json")).unwrap();
    update.begin("0.7.2", "0.8.0").unwrap();
    update
        .advance(crate::update_transaction::UpdatePhase::Migrating)
        .unwrap();
    drop(update);

    let daemon = Daemon::new(LocalPaths::new(locald)).expect("a daemon still starts");
    let healed = daemon.healed.join(" | ");
    assert!(
        healed.contains("0.8.0"),
        "the interrupted update must reach the operator: {healed}"
    );
}

/// And an ordinary install says nothing, so the report means something when
/// it does appear.
#[test]
fn an_installation_with_no_update_history_starts_quietly() {
    let (_root, daemon) = daemon();
    assert!(
        !daemon.healed.iter().any(|line| line.contains("update")),
        "a healthy install must not mention updates: {:?}",
        daemon.healed
    );
}

/// A client that holds its socket open and stops reading must not be
/// carried for ever.
///
/// Subscribers used to get an unbounded channel, so every broadcast for the
/// rest of the daemon's life queued behind a reader that had gone away --
/// and events reach a megabyte each. Bounding it turns a slow client into a
/// disconnected one, which is what it already was.
#[test]
fn a_subscriber_that_stopped_reading_is_dropped_rather_than_queued_for_ever() {
    let (sender, receiver) = mpsc::sync_channel::<String>(SUBSCRIBER_BACKLOG);

    // Fill the backlog without draining, as a wedged client does.
    for index in 0..SUBSCRIBER_BACKLOG {
        sender
            .try_send(format!("event {index}"))
            .expect("the backlog should accept up to its depth");
    }

    assert!(
        matches!(
            sender.try_send("one too many".to_owned()),
            Err(mpsc::TrySendError::Full(_))
        ),
        "past its depth the send must fail rather than grow"
    );

    // Which is what `broadcast`'s retain reads as "drop this subscriber".
    drop(receiver);
    assert!(
        matches!(
            sender.try_send("after the reader has gone".to_owned()),
            Err(mpsc::TrySendError::Disconnected(_))
        ),
        "a departed reader must also be dropped, not retried"
    );
}
use std::collections::HashMap;

use super::compose_backend_environment;

#[test]
fn operator_updates_preserve_private_runtime_endpoints() {
    let environment = compose_backend_environment(
        HashMap::from([
            ("LEMMA_OPENAI_API_KEY".into(), "vault-secret".into()),
            (
                "DATABASE_URL".into(),
                "postgresql://127.0.0.1:55432/lemma".into(),
            ),
        ]),
        Some(HashMap::from([
            (
                "DATABASE_URL".into(),
                "postgresql://192.168.64.37:5432/lemma".into(),
            ),
            ("REDIS_URL".into(), "redis://192.168.64.37:6379".into()),
            (
                "SUPERTOKENS_CORE_URL".into(),
                "http://192.168.64.37:3567".into(),
            ),
        ])),
    );

    assert_eq!(environment["LEMMA_OPENAI_API_KEY"], "vault-secret");
    assert_eq!(
        environment["DATABASE_URL"],
        "postgresql://192.168.64.37:5432/lemma"
    );
    assert_eq!(environment["REDIS_URL"], "redis://192.168.64.37:6379");
    assert_eq!(
        environment["SUPERTOKENS_CORE_URL"],
        "http://192.168.64.37:3567"
    );
}

/// The one arm that erases somebody's work asks first, and had no test.
///
/// `local.reset-data` removes the pods, databases and workspaces of an
/// installation. The only thing between a stray or malformed request and
/// all of it is a literal confirmation string, and nothing checked that
/// the check was there -- so it could have been dropped, or its spelling
/// changed on one side, and every test in this file would still pass.
///
/// Wrong-but-present is the case worth having: an empty confirmation is
/// the obvious one to guard, and a client that sends a *different* phrase
/// is the one a refactor produces.
#[test]
fn erasing_local_data_refuses_anything_but_its_exact_confirmation() {
    let (_root, daemon) = daemon();

    for (label, request) in [
        (
            "no confirmation at all",
            json!({"cmd": "local.reset-data", "id": "r1"}),
        ),
        (
            "an empty confirmation",
            json!({"cmd": "local.reset-data", "id": "r1", "confirm": ""}),
        ),
        (
            "a different phrase",
            json!({"cmd": "local.reset-data", "id": "r1", "confirm": "reset-local-Data"}),
        ),
        (
            "the reset the uninstaller uses, which is a different one",
            json!({"cmd": "local.reset-data", "id": "r1", "confirm": "erase-local-lemma"}),
        ),
    ] {
        let replies = exchange(&daemon, request);
        let last = replies
            .last()
            .unwrap_or_else(|| panic!("{label} must be answered, not ignored"));
        assert_eq!(last["event"], "error", "{label} must not start a reset");
        assert_eq!(
            last["code"], "confirmation-required",
            "{label} has to be refused for the reason it was refused"
        );
        assert_eq!(last["id"], "r1", "{label}: the app matches answers by id");
        assert!(
            replies.iter().all(|reply| reply["event"] != "ack"),
            "{label} was acknowledged, which is how the app learns a reset began"
        );
    }
}

/// An installation that cannot share refuses before it takes anything.
///
/// The three sharing arms all begin by checking that this installation has
/// the managed local runtime, and the order matters more than the refusal
/// does. `sharing.enable` goes on to parse its payload and then take the
/// lifecycle lock -- the one a start needs -- so a reordering that put
/// either of those first would have a daemon with no sharing at all
/// holding that lock while it worked out it had nothing to do.
///
/// So this asserts the refusal, that nothing was acknowledged, and that
/// the lifecycle is still free afterwards. The last is the one that
/// notices a reordering.
#[test]
fn sharing_commands_refuse_before_they_take_the_lock_a_start_needs() {
    let (_root, daemon) = daemon();

    for command in ["sharing.preflight", "sharing.enable", "sharing.disable"] {
        let replies = exchange(
            &daemon,
            json!({"cmd": command, "id": command, "payload": {}}),
        );
        let last = replies
            .last()
            .unwrap_or_else(|| panic!("{command} must be answered, not ignored"));
        assert_eq!(last["event"], "error", "{command}");
        assert_eq!(
            last["code"], "sharing-unavailable",
            "{command} has to say why, so the app can explain it"
        );
        assert!(
            replies.iter().all(|reply| reply["event"] != "ack"),
            "{command} was acknowledged, which tells the app it began"
        );
        assert!(
            daemon.lifecycle.begin().is_ok(),
            "{command} left the lifecycle held, so a start would now be refused"
        );
        daemon.lifecycle.finish();
    }
}

/// The body of one function in this directory, for the properties that are
/// about the shape of the code rather than a value it returns.
///
/// Named by module rather than scanned across the directory, deliberately: if
/// the function moves, this fails loudly on the `expect` instead of quietly
/// finding nothing.
fn function_body<'a>(source: &'a str, signature: &str) -> &'a str {
    let start = source.find(signature).expect("the function exists");
    let after = start + signature.len();
    let end = source[after..]
        .find("\n    }\n")
        .map_or(source.len(), |offset| after + offset);
    &source[start..end]
}

/// Nothing after the exposure is live may fail without rolling it back.
///
/// `enable_sharing_transaction` restarts the stack against the shared origin
/// and only then records it. Recording used to end in `?`, which returned
/// past the whole rollback below it: a state file that could not be written
/// left sharing serving, the sharing controller stuck in `prepared`, and the
/// client told only that enabling had failed.
///
/// Asserted on the source because reaching the line needs a live tunnel, a
/// running backend and a frontend; the property is "this failure takes the
/// rollback path", which is a shape.
#[test]
fn recording_a_live_share_cannot_fail_without_rolling_it_back() {
    let source = include_str!("sharing_ops.rs").replace("\r\n", "\n");
    let body = function_body(&source, "fn enable_sharing_transaction(");
    let activation = body
        .find("let activate = manager")
        .expect("activation is where the exposure goes live");
    let after = &body[activation..];
    assert!(
        !after.contains("state.persist(&self.paths.state)?"),
        "a failure after the share is live must reach the rollback, not \
         return past it:\n{after}"
    );
    assert!(
        after.contains("rollback_enable"),
        "the rollback is what this is protecting:\n{after}"
    );
}

/// One supervisor, however many clients ask for one at once.
///
/// `send_to_supervisor` runs on one thread per client connection, and a
/// desktop launch has several arriving together. `ensure_supervisor` used to
/// ask `supervisor_running()` -- which takes the lock and gives it straight
/// back -- and take the lock again only to record the child, so two threads
/// could both find no supervisor and both start one. The second assignment
/// dropped the first `Child`, and dropping a `Child` neither kills nor reaps
/// it: the first supervisor kept running untracked.
///
/// Asserted on the source. The failure is a race, and a test that loses it
/// on purpose is a test that passes by luck on a quiet machine; what can be
/// stated exactly is that the check and the spawn happen under one guard.
#[test]
fn a_supervisor_is_spawned_under_the_lock_that_records_it() {
    let source = include_str!("supervisor.rs").replace("\r\n", "\n");
    let body = function_body(&source, "fn ensure_supervisor(");
    let taken = body
        .find("self.supervisor.lock()")
        .expect("it takes the supervisor lock");
    let spawned = body
        .find("command.spawn()")
        .expect("it spawns a supervisor");
    assert!(
        taken < spawned,
        "the lock has to be held before the spawn, or two clients start two \
         supervisors:\n{body}"
    );
    assert!(
        !body.contains("self.supervisor_running()"),
        "checking through a helper gives the lock back between the check and \
         the spawn, which is the race itself:\n{body}"
    );
}

/// The compatibility supervisor is stopped as a tree, and can be.
///
/// It is `uv run --project lemma-stack lemma-stack supervise` in a checkout --
/// uv, then Python, then whatever the stack started. `child.kill()` reached the
/// leader and left the rest running with nothing to reap them, which is the
/// same defect the Agent Host had and fixed.
///
/// The two halves have to move together, which is why one test asserts both. A
/// tree teardown without its own process group is worse than the bug: on unix
/// it signals the negative PID, and without `process_group(0)` that is whatever
/// group locald itself is in.
///
/// Asserted on the source because the alternative is spawning a real
/// supervisor, which needs a checkout, uv, and a container runtime.
#[test]
fn the_supervisor_is_spawned_into_its_own_group_and_stopped_as_a_tree() {
    let supervisor_source = include_str!("supervisor.rs").replace("\r\n", "\n");
    let spawn = function_body(&supervisor_source, "fn ensure_supervisor(");
    assert!(
        spawn.contains("command.process_group(0)"),
        "the supervisor needs its own process group, or there is nothing to \
         signal but the leader:\n{spawn}"
    );

    let stack_source = include_str!("stack_ops.rs").replace("\r\n", "\n");
    let stop = function_body(&stack_source, "fn start_daemon_shutdown(");
    assert!(
        stop.contains("terminate_process_tree(&mut supervisor.child)"),
        "stopping the supervisor has to take the tree with it:\n{stop}"
    );
    assert!(
        !stop.contains("supervisor.child.kill()"),
        "killing the leader is what orphaned uv's children:\n{stop}"
    );
}
