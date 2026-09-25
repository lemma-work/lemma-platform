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
