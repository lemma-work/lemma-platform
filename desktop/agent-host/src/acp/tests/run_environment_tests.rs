//! What a run puts into its agent's environment.
//!
//! A host agent used to be spawned with `PATH` and nothing else, while a
//! sandbox agent running the same skills got the user's whole `LEMMA_*`
//! environment. Every `lemma` command a host agent was instructed to run had
//! no credential, and a user saw that as the agent reporting that the pod
//! connection would not authenticate.

use super::*;
use serde_json::json;

#[test]
fn the_run_credential_reaches_the_agent() {
    let mcp = json!({
        "environment": {
            "LEMMA_TOKEN": "a-delegated-session",
            "LEMMA_BASE_URL": "http://app.127.0.0.1.sslip.io:53664",
            "LEMMA_POD_ID": "pod-1",
        }
    });
    let environment = run_environment(&mcp);
    assert_eq!(
        environment.get("LEMMA_TOKEN").map(String::as_str),
        Some("a-delegated-session")
    );
    assert_eq!(environment.len(), 3);
}

#[test]
fn a_run_without_published_credentials_gets_none() {
    assert!(run_environment(&json!({})).is_empty());
    assert!(run_environment(&json!({"environment": "not-an-object"})).is_empty());
    assert!(run_environment(&serde_json::Value::Null).is_empty());
}

#[test]
fn only_lemma_names_carrying_strings_are_accepted() {
    // This process puts these into the environment of something that runs the
    // user's own tooling, so the backend's allowlist is not taken on trust.
    let mcp = json!({
        "environment": {
            "LEMMA_TOKEN": "keep",
            "PATH": "/evil/bin",
            "LD_PRELOAD": "/evil/lib.so",
            "LEMMA_NESTED": {"not": "a string"},
            "LEMMA_COUNT": 3,
        }
    });
    let environment = run_environment(&mcp);
    assert_eq!(
        environment.keys().collect::<Vec<_>>(),
        vec!["LEMMA_TOKEN"],
        "only LEMMA_* string values may be published into the agent process"
    );
}

#[test]
fn the_token_is_written_where_a_refresh_can_replace_it() {
    use crate::runtime::credentials::{agent_environment, remove_run_token, write_run_token};

    let root = tempfile::tempdir().expect("temp dir");
    let run_id = uuid::Uuid::new_v4();
    let mcp = json!({"token": "first", "environment": {"LEMMA_TOKEN": "first"}});

    let environment = agent_environment(root.path(), run_id, &mcp, run_environment(&mcp));
    let path = environment
        .get("LEMMA_TOKEN_FILE")
        .expect("a run publishes the path its credential can be re-read from");
    assert_eq!(std::fs::read_to_string(path).unwrap(), "first");

    // A mid-run refresh rewrites the same path, which is the only way a
    // credential reaches a process that has already been spawned.
    write_run_token(root.path(), run_id, "second").expect("rewrite");
    assert_eq!(std::fs::read_to_string(path).unwrap(), "second");

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mode = std::fs::metadata(path).unwrap().permissions().mode();
        assert_eq!(
            mode & 0o777,
            0o600,
            "a credential file must not be readable by others"
        );
    }

    remove_run_token(root.path(), run_id);
    assert!(
        !std::path::Path::new(path).exists(),
        "a finished run must not leave its credential behind"
    );
}

#[test]
fn a_run_with_no_token_publishes_no_file() {
    use crate::runtime::credentials::agent_environment;

    let root = tempfile::tempdir().expect("temp dir");
    let mcp = json!({"environment": {"LEMMA_BASE_URL": "http://localhost"}});
    let environment = agent_environment(
        root.path(),
        uuid::Uuid::new_v4(),
        &mcp,
        run_environment(&mcp),
    );
    assert!(!environment.contains_key("LEMMA_TOKEN_FILE"));
    assert!(environment.contains_key("LEMMA_BASE_URL"));
}
