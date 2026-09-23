//! What a run puts into its agent's environment.
//!
//! A host agent runs the same skills as a sandbox agent, and their `lemma`
//! commands need the same `LEMMA_*` environment to authenticate.

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
    use crate::runtime::credentials::{RunCredential, agent_environment};

    let root = tempfile::tempdir().expect("temp dir");
    let credential = RunCredential::new(root.path(), uuid::Uuid::new_v4());
    let mcp = json!({"token": "first", "environment": {"LEMMA_TOKEN": "first"}});

    let environment = agent_environment(&credential, &mcp, run_environment(&mcp));
    let path = environment
        .get("LEMMA_TOKEN_FILE")
        .expect("a run publishes the path its credential can be re-read from");
    assert_eq!(std::fs::read_to_string(path).unwrap(), "first");

    // A mid-run refresh rewrites the same path, which is the only way a
    // credential reaches a process that has already been spawned.
    credential.write("second").expect("rewrite");
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
}

#[test]
fn a_dropped_run_takes_its_credential_with_it() {
    use crate::runtime::credentials::{RetireOnDrop, RunCredential};

    let root = tempfile::tempdir().expect("temp dir");
    let credential = RunCredential::new(root.path(), uuid::Uuid::new_v4());
    let path = credential.write("delegated").expect("write").expect("live");
    assert!(path.exists());

    // `handle.abort()` drops the run's task wherever it happens to be awaiting,
    // so the removal at the end of the run body is never reached. Drop is.
    drop(RetireOnDrop(std::sync::Arc::clone(&credential)));

    assert!(
        !path.exists(),
        "an aborted run left a delegated credential on disk"
    );
}

#[test]
fn a_refresh_after_the_run_ended_does_not_resurrect_the_file() {
    use crate::runtime::credentials::{RetireOnDrop, RunCredential};

    let root = tempfile::tempdir().expect("temp dir");
    let credential = RunCredential::new(root.path(), uuid::Uuid::new_v4());
    let path = credential.write("first").expect("write").expect("live");
    drop(RetireOnDrop(std::sync::Arc::clone(&credential)));

    // An aborted run is not terminal in the journal until `reap_finished`
    // catches up, so a `REFRESH_CREDENTIAL` can still arrive -- and nothing
    // would remove a file it wrote back.
    assert_eq!(credential.write("refreshed").expect("no error"), None);
    assert!(
        !path.exists(),
        "a refresh recreated a retired run's credential"
    );
}

#[test]
fn a_refresh_never_leaves_a_half_written_token() {
    use crate::runtime::credentials::RunCredential;

    let root = tempfile::tempdir().expect("temp dir");
    let credential = RunCredential::new(root.path(), uuid::Uuid::new_v4());
    let path = credential
        .write("first-token")
        .expect("write")
        .expect("live");

    // A refresh rewrites the same path while an agent may be reading it. The
    // rename means a reader sees one whole token or the other, never an empty
    // file or a prefix.
    for token in ["a-much-longer-second-token", "third"] {
        credential.write(token).expect("rewrite");
        assert_eq!(std::fs::read_to_string(&path).unwrap(), token);
    }

    // And nothing is left beside it.
    let strays: Vec<_> = std::fs::read_dir(path.parent().unwrap())
        .unwrap()
        .filter_map(Result::ok)
        .map(|entry| entry.file_name().to_string_lossy().into_owned())
        .filter(|name| name.contains("tmp-"))
        .collect();
    assert!(
        strays.is_empty(),
        "staging files were left behind: {strays:?}"
    );
}

#[test]
fn a_run_with_no_token_publishes_no_file() {
    use crate::runtime::credentials::{RunCredential, agent_environment};

    let root = tempfile::tempdir().expect("temp dir");
    let credential = RunCredential::new(root.path(), uuid::Uuid::new_v4());
    let mcp = json!({"environment": {"LEMMA_BASE_URL": "http://localhost"}});
    let environment = agent_environment(&credential, &mcp, run_environment(&mcp));
    assert!(!environment.contains_key("LEMMA_TOKEN_FILE"));
    assert!(environment.contains_key("LEMMA_BASE_URL"));
}
