use super::*;

fn allow() -> PermissionDecision {
    PermissionDecision::Allow {
        option_id: "allow-once".to_owned(),
    }
}

fn offer(session: &str, label: &str) -> AlwaysAllowOffer {
    AlwaysAllowOffer {
        scope: AlwaysAllowScope {
            session_id: session.to_owned(),
            label: label.to_owned(),
        },
        option_id: "allow_always".to_owned(),
    }
}

fn always() -> PermissionDecision {
    PermissionDecision::Allow {
        option_id: "allow_always".to_owned(),
    }
}

/// Let spawned waiters run until the gate holds `count` parked requests.
async fn parked_at_least(gate: &PermissionGate, count: usize) {
    for _ in 0..10_000 {
        if gate.parked() >= count {
            return;
        }
        tokio::task::yield_now().await;
    }
    panic!("a waiter never parked (gate holds {})", gate.parked());
}

#[tokio::test]
async fn resolving_an_awaited_request_returns_its_decision() {
    let gate = PermissionGate::new();
    let run_id = Uuid::now_v7();
    let waiter = {
        let gate = gate.clone();
        tokio::spawn(async move {
            gate.wait(run_id, "req-1".to_owned(), Duration::from_secs(5), None)
                .await
        })
    };
    // Let the waiter register before resolving.
    tokio::task::yield_now().await;
    for _ in 0..50 {
        if gate.resolve(
            run_id,
            "req-1",
            PermissionDecision::Allow {
                option_id: "allow-once".to_owned(),
            },
        ) {
            break;
        }
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    assert_eq!(
        waiter.await.unwrap(),
        PermissionDecision::Allow {
            option_id: "allow-once".to_owned()
        }
    );
}

#[tokio::test]
async fn an_unanswered_request_denies_rather_than_hanging() {
    let gate = PermissionGate::new();
    let decision = gate
        .wait(
            Uuid::now_v7(),
            "req-1".to_owned(),
            Duration::from_millis(20),
            None,
        )
        .await;
    assert_eq!(decision, PermissionDecision::Deny);
}

#[tokio::test]
async fn resolving_an_unknown_request_reports_that_nothing_waited() {
    let gate = PermissionGate::new();
    assert!(!gate.resolve(Uuid::now_v7(), "missing", PermissionDecision::Deny));
}

#[tokio::test]
async fn abandoning_a_run_denies_its_waiters() {
    let gate = PermissionGate::new();
    let run_id = Uuid::now_v7();
    let waiter = {
        let gate = gate.clone();
        tokio::spawn(async move {
            gate.wait(run_id, "req-1".to_owned(), Duration::from_secs(5), None)
                .await
        })
    };
    tokio::task::yield_now().await;
    for _ in 0..50 {
        gate.abandon_run(run_id);
        if waiter.is_finished() {
            break;
        }
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    assert_eq!(waiter.await.unwrap(), PermissionDecision::Deny);
}

/// `acp.rs` keys a permission request by its tool-call id and falls back to
/// the session id, so parallel tool calls in one session collide on a
/// single key. The displaced waiter must not take its successor with it.
#[tokio::test]
async fn a_displaced_waiter_leaves_its_successor_reachable() {
    let gate = PermissionGate::new();
    let run_id = Uuid::now_v7();
    let spawn_waiter = || {
        let gate = gate.clone();
        tokio::spawn(async move {
            gate.wait(run_id, "same-key".to_owned(), Duration::from_secs(30), None)
                .await
        })
    };

    let first = spawn_waiter();
    parked_at_least(&gate, 1).await;
    // The second waiter displaces the first, whose sender is dropped: the
    // first resolves Deny and then tries to clean up after itself.
    let second = spawn_waiter();
    assert_eq!(
        first.await.unwrap(),
        PermissionDecision::Deny,
        "the displaced waiter is denied"
    );
    assert_eq!(
        gate.parked(),
        1,
        "the displaced waiter removed the live waiter's entry"
    );

    assert!(
        gate.resolve(run_id, "same-key", allow()),
        "Lemma's decision must still reach the live waiter"
    );
    assert_eq!(second.await.unwrap(), allow());
}

/// Claude Code issues parallel tool calls, so the same key can be
/// displaced repeatedly. Every departing waiter must remove only itself.
#[tokio::test]
async fn repeated_displacement_keeps_the_newest_waiter_reachable() {
    let gate = PermissionGate::new();
    let run_id = Uuid::now_v7();
    let spawn_waiter = || {
        let gate = gate.clone();
        tokio::spawn(async move {
            gate.wait(run_id, "same-key".to_owned(), Duration::from_secs(30), None)
                .await
        })
    };

    let mut newest = spawn_waiter();
    parked_at_least(&gate, 1).await;
    for round in 0..3 {
        let next = spawn_waiter();
        // The parked waiter can only finish once `next` has displaced it,
        // so awaiting it here also fences `next`'s registration.
        assert_eq!(newest.await.unwrap(), PermissionDecision::Deny);
        assert_eq!(gate.parked(), 1, "round {round} stranded the live waiter");
        newest = next;
    }

    assert!(gate.resolve(run_id, "same-key", allow()));
    assert_eq!(newest.await.unwrap(), allow());
    assert_eq!(gate.parked(), 0, "every waiter cleaned up after itself");
}

#[tokio::test]
async fn a_timed_out_waiter_cleans_up_after_itself() {
    let gate = PermissionGate::new();
    let run_id = Uuid::now_v7();
    let waiter = {
        let gate = gate.clone();
        tokio::spawn(async move {
            gate.wait(run_id, "req-1".to_owned(), Duration::from_millis(20), None)
                .await
        })
    };
    assert_eq!(waiter.await.unwrap(), PermissionDecision::Deny);
    assert_eq!(gate.parked(), 0);
}

/// "Always" has to mean always, and the agent cannot make it mean that: its
/// rule lives in an adapter process started fresh for every run, and is
/// installed only once the answer arrives. So a grant given on Monday's
/// message was asked for again on Tuesday's, and a grant given to one call
/// of a parallel batch was asked for again by every other call in it.
mod always_allow {
    use super::*;

    #[tokio::test]
    async fn a_granted_scope_is_remembered_for_its_session() {
        let gate = PermissionGate::new();
        let run_id = Uuid::now_v7();
        let scope = offer("session-a", "Always Allow all WebSearch").scope;
        assert!(!gate.is_granted(&scope));

        let waiter = {
            let gate = gate.clone();
            tokio::spawn(async move {
                gate.wait(
                    run_id,
                    "req-1".to_owned(),
                    Duration::from_secs(5),
                    Some(offer("session-a", "Always Allow all WebSearch")),
                )
                .await
            })
        };
        parked_at_least(&gate, 1).await;
        assert!(gate.resolve(run_id, "req-1", always()));

        assert_eq!(waiter.await.unwrap(), always());
        assert!(gate.is_granted(&scope));
    }

    #[tokio::test]
    async fn allowing_once_grants_nothing() {
        let gate = PermissionGate::new();
        let run_id = Uuid::now_v7();
        let waiter = {
            let gate = gate.clone();
            tokio::spawn(async move {
                gate.wait(
                    run_id,
                    "req-1".to_owned(),
                    Duration::from_secs(5),
                    Some(offer("session-a", "Always Allow all WebSearch")),
                )
                .await
            })
        };
        parked_at_least(&gate, 1).await;
        assert!(gate.resolve(run_id, "req-1", allow()));
        let _ = waiter.await.unwrap();

        assert!(!gate.is_granted(&offer("session-a", "Always Allow all WebSearch").scope));
    }

    #[tokio::test]
    async fn a_grant_releases_the_rest_of_a_parallel_batch() {
        // Four fetches of the same domain go out together, so all four are
        // asking before any of them is answered. Answering one "always"
        // answers them all; without this the user clicks four times for the
        // grant they already gave.
        let gate = PermissionGate::new();
        let run_id = Uuid::now_v7();
        let label = "Always Allow WebFetch(domain:en.wikipedia.org)";
        let siblings: Vec<_> = ["req-2", "req-3", "req-4"]
            .into_iter()
            .map(|request_id| {
                let gate = gate.clone();
                tokio::spawn(async move {
                    gate.wait(
                        run_id,
                        request_id.to_owned(),
                        Duration::from_secs(5),
                        Some(offer("session-a", label)),
                    )
                    .await
                })
            })
            .collect();
        let answered = {
            let gate = gate.clone();
            tokio::spawn(async move {
                gate.wait(
                    run_id,
                    "req-1".to_owned(),
                    Duration::from_secs(5),
                    Some(offer("session-a", label)),
                )
                .await
            })
        };
        parked_at_least(&gate, 4).await;

        assert!(gate.resolve(run_id, "req-1", always()));

        assert_eq!(answered.await.unwrap(), always());
        for sibling in siblings {
            assert_eq!(sibling.await.unwrap(), always());
        }
    }

    #[tokio::test]
    async fn a_narrower_scope_still_asks() {
        // "Always Allow WebFetch(domain:github.com)" is not consent for
        // another domain, and the agent says so in the label it wrote.
        let gate = PermissionGate::new();
        let run_id = Uuid::now_v7();
        let other = {
            let gate = gate.clone();
            tokio::spawn(async move {
                gate.wait(
                    run_id,
                    "req-2".to_owned(),
                    Duration::from_secs(5),
                    Some(offer(
                        "session-a",
                        "Always Allow WebFetch(domain:evil.test)",
                    )),
                )
                .await
            })
        };
        let granted = {
            let gate = gate.clone();
            tokio::spawn(async move {
                gate.wait(
                    run_id,
                    "req-1".to_owned(),
                    Duration::from_secs(5),
                    Some(offer(
                        "session-a",
                        "Always Allow WebFetch(domain:github.com)",
                    )),
                )
                .await
            })
        };
        parked_at_least(&gate, 2).await;

        assert!(gate.resolve(run_id, "req-1", always()));
        let _ = granted.await.unwrap();

        assert!(!other.is_finished(), "a different scope was answered for");
        assert!(gate.resolve(run_id, "req-2", PermissionDecision::Deny));
        assert_eq!(other.await.unwrap(), PermissionDecision::Deny);
    }

    #[tokio::test]
    async fn another_conversation_does_not_inherit_the_grant() {
        let gate = PermissionGate::new();
        let run_id = Uuid::now_v7();
        let waiter = {
            let gate = gate.clone();
            tokio::spawn(async move {
                gate.wait(
                    run_id,
                    "req-1".to_owned(),
                    Duration::from_secs(5),
                    Some(offer("session-a", "Always Allow all Bash")),
                )
                .await
            })
        };
        parked_at_least(&gate, 1).await;
        assert!(gate.resolve(run_id, "req-1", always()));
        let _ = waiter.await.unwrap();

        assert!(!gate.is_granted(&offer("session-b", "Always Allow all Bash").scope));
    }

    #[tokio::test]
    async fn a_session_that_is_gone_takes_its_grants_with_it() {
        let gate = PermissionGate::new();
        let run_id = Uuid::now_v7();
        let waiter = {
            let gate = gate.clone();
            tokio::spawn(async move {
                gate.wait(
                    run_id,
                    "req-1".to_owned(),
                    Duration::from_secs(5),
                    Some(offer("session-a", "Always Allow all Bash")),
                )
                .await
            })
        };
        parked_at_least(&gate, 1).await;
        assert!(gate.resolve(run_id, "req-1", always()));
        let _ = waiter.await.unwrap();

        gate.forget_session("session-a");

        assert!(!gate.is_granted(&offer("session-a", "Always Allow all Bash").scope));
    }
}
