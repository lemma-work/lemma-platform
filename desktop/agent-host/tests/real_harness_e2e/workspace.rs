//! Where a run works, and where a resumed one carries on.

use super::*;

#[test]
fn live_agent_selection_rejects_typoes_empty_and_duplicate_runs_before_startup() {
    let error = parse_agents("claude").unwrap_err().to_string();
    assert!(error.contains("unknown agent"));
    assert!(error.contains("claude-code"));
    assert!(parse_agents(" , ").is_err());
    assert!(parse_agents("codex,codex").is_err());
    assert_eq!(
        parse_agents(" codex,claude-code, opencode ").unwrap(),
        ["codex", "claude-code", "opencode"]
    );
}

/// Exercise provider-native pwd and session continuity in the mapped layout.
#[tokio::test]
#[ignore = "requires authenticated local agents and spends real provider quota"]
async fn real_harnesses_resume_in_the_saved_workspace_directory() {
    let source = HostPaths::under(agent_host_data_directory());
    let manifest = AdapterManifest::builtin()
        .unwrap()
        .with_cache_root(source.adapters);
    let directory = TempDir::new().unwrap();
    let root = directory.path().join("lemma");
    let target = Uuid::new_v4();
    for agent in configured_agents() {
        let conversation = Uuid::new_v4();
        let saved = format!(
            "/workspace/c/{}/{}",
            Utc::now().format("%Y-%m-%d"),
            &conversation.simple().to_string()[..8]
        );
        let cwd = lemma_agent_host::conversation_directory::prepare(&root, target, &saved).unwrap();
        let (session, answer) = one_turn(&manifest, &cwd, &agent, conversation,
            "Run pwd once using your native shell and report its exact output. Remember my name is Ada. Do not modify files.", None).await;
        assert!(
            answer.contains(cwd.to_str().unwrap())
                || answer.contains(cwd.canonicalize().unwrap().to_str().unwrap()),
            "{agent}: {answer}"
        );
        let reopened =
            lemma_agent_host::conversation_directory::prepare(&root, target, &saved).unwrap();
        assert_eq!(cwd, reopened);
        let (next_session, answer) = one_turn(&manifest, &reopened, &agent, conversation,
            "What is my name? Run pwd once using your native shell and report its exact output. Do not modify files.", Some(session.clone())).await;
        assert_eq!(session, next_session, "{agent} changed provider session");
        assert!(answer.contains("Ada"), "{agent}: {answer}");
        assert!(
            answer.contains(cwd.to_str().unwrap())
                || answer.contains(cwd.canonicalize().unwrap().to_str().unwrap()),
            "{agent}: {answer}"
        );
    }
}
