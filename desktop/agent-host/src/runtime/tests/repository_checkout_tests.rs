//! Which run folders a repository checkout may touch at all.

fn spec(conversation: uuid::Uuid, slug: &str) -> super::RunSpec {
    serde_json::from_value(serde_json::json!({
        "agent_run_id": uuid::Uuid::new_v4(), "conversation_id": conversation,
        "harness_id": uuid::Uuid::new_v4(), "profile_revision": "test",
        "system_prompt": "test", "prompt": [],
        "workspace_cwd": format!("/home/user/lemma/worktrees/octo/widgets/{slug}"),
        "repository": {"owner": "octo", "repo": "widgets", "slug": slug},
        "run_deadline": chrono::Utc::now(),
    }))
    .unwrap()
}

/// Every case here is decided before any git runs, so none needs a network.
#[tokio::test]
async fn only_the_folder_the_run_spec_resolved_to_is_ever_checked_out() {
    let temporary = tempfile::tempdir().unwrap();
    let paths = super::HostPaths::under(temporary.path().join("host"));
    std::fs::create_dir_all(&paths.root).unwrap();
    let root = temporary.path().join("lemma");
    let target = uuid::Uuid::new_v4();
    let budget = std::time::Duration::from_secs(1);

    // A folder holding something else is left alone, and the agent is told
    // the folder is not the repository.
    let run = spec(uuid::Uuid::now_v7(), "busy0");
    let folder = crate::conversation_directory::prepare(
        &root,
        target,
        run.workspace_cwd.as_deref().unwrap(),
    )
    .unwrap();
    std::fs::write(folder.join("notes.txt"), "mine").unwrap();
    let report = super::repository_checkout_under(&root, &paths, target, &run, &folder, budget)
        .await
        .expect("a checkout was asked for");
    assert_eq!(report.instructions, None);
    assert!(
        report
            .notice
            .expect("a folder left alone is said")
            .contains("was not checked out in this directory")
    );
    assert_eq!(
        std::fs::read_dir(&folder)
            .unwrap()
            .flatten()
            .map(|entry| entry.file_name())
            .collect::<Vec<_>>(),
        ["notes.txt"]
    );

    // The run started somewhere else -- a legacy scratch folder -- so the
    // worktree is not where the agent is, and nothing is made.
    let elsewhere = temporary.path().join("scratch");
    std::fs::create_dir(&elsewhere).unwrap();
    let fresh = spec(uuid::Uuid::now_v7(), "fresh0");
    assert_eq!(
        super::repository_checkout_under(&root, &paths, target, &fresh, &elsewhere, budget).await,
        None
    );
    assert!(!root.join("repos").exists());

    // A folder the person bound is theirs: no checkout, even in the worktree.
    let bound = spec(uuid::Uuid::now_v7(), "bound0");
    let project = temporary.path().join("my project");
    std::fs::create_dir(&project).unwrap();
    std::fs::write(
        &paths.folders,
        serde_json::to_vec(&serde_json::json!({
            bound.conversation_id.to_string(): project.to_str().unwrap(),
        }))
        .unwrap(),
    )
    .unwrap();
    let worktree = root.join("worktrees/octo/widgets/bound0");
    assert_eq!(
        super::repository_checkout_under(&root, &paths, target, &bound, &worktree, budget).await,
        None
    );

    // Names this host would not pass to git: nothing is made, nothing said.
    let mut refused = spec(uuid::Uuid::now_v7(), "refused0");
    refused.repository.as_mut().unwrap().owner = "../..".to_owned();
    let folder = root.join("worktrees/octo/widgets/refused0");
    assert_eq!(
        super::repository_checkout_under(&root, &paths, target, &refused, &folder, budget).await,
        None
    );
    assert!(!root.join("repos").exists());
}
