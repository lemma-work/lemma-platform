use super::*;

fn repository(owner: &str, repo: &str, reference: Option<&str>, slug: &str) -> RunRepository {
    RunRepository {
        owner: owner.to_owned(),
        repo: repo.to_owned(),
        reference: reference.map(str::to_owned),
        slug: slug.to_owned(),
    }
}

fn plan(reference: Option<&str>) -> Checkout {
    Checkout::plan(
        Path::new("/home/me/lemma"),
        &repository("octo-org", "widgets.js", reference, "ab3f2k7q"),
    )
    .expect("a valid repository")
}

#[test]
fn paths_are_derived_from_the_names_under_the_root() {
    let checkout = plan(None);
    assert_eq!(
        checkout.clone_dir(),
        Path::new("/home/me/lemma/repos/octo-org/widgets.js")
    );
    assert_eq!(
        checkout.worktree_dir(),
        Path::new("/home/me/lemma/worktrees/octo-org/widgets.js/ab3f2k7q")
    );
    assert_eq!(checkout.branch(), "lemma/ab3f2k7q");
    assert_eq!(
        checkout.github_remote(),
        "https://github.com/octo-org/widgets.js.git"
    );
}

/// The backend's patterns, held here too: each of these would otherwise
/// become a path that climbs out of the root, an option git reads, or a
/// branch name no worktree was made for.
#[test]
fn names_the_backend_would_refuse_are_refused_before_any_path_is_made() {
    let root = Path::new("/home/me/lemma");
    for (owner, repo, reference, slug) in [
        ("..", "widgets", None, "ab3f2k7q"),
        ("octo", "..", None, "ab3f2k7q"),
        ("octo", ".git", None, "ab3f2k7q"),
        ("-octo", "widgets", None, "ab3f2k7q"),
        ("octo/evil", "widgets", None, "ab3f2k7q"),
        ("octo", "wid/gets", None, "ab3f2k7q"),
        ("octo", "-widgets", None, "ab3f2k7q"),
        (
            "octo",
            "widgets",
            Some("--upload-pack=touch /tmp/x"),
            "ab3f2k7q",
        ),
        ("octo", "widgets", Some("-b"), "ab3f2k7q"),
        ("octo", "widgets", Some("main;rm -rf ~"), "ab3f2k7q"),
        ("octo", "widgets", Some("main\nnext"), "ab3f2k7q"),
        ("octo", "widgets", None, "../../etc"),
        ("octo", "widgets", None, "AB3F"),
        ("octo", "widgets", None, ""),
        ("", "widgets", None, "ab3f2k7q"),
        (&"a".repeat(40), "widgets", None, "ab3f2k7q"),
        ("octo", &"r".repeat(101), None, "ab3f2k7q"),
        ("octo", "widgets", None, &"a".repeat(65)),
    ] {
        assert!(
            Checkout::plan(root, &repository(owner, repo, reference, slug)).is_err(),
            "{owner}/{repo} {reference:?} {slug}"
        );
    }
    for (owner, repo, reference) in [
        ("octo", "widgets", Some("main")),
        ("octo", "_private.repo-2", Some("release/v1.2")),
        ("o", "r", Some("pull/123/head")),
        ("octo", "widgets", Some("0123abcd")),
    ] {
        assert!(Checkout::plan(root, &repository(owner, repo, reference, "s0")).is_ok());
    }
}

#[test]
fn every_value_is_one_argument_and_none_can_be_read_as_an_option() {
    let checkout = plan(Some("release/v1.2"));
    let clone = checkout.clone_dir();
    let worktree = checkout.worktree_dir();

    // `--` before the remote, so nothing after it is an option.
    assert_eq!(
        clone_args("https://github.com/octo-org/widgets.js.git", clone),
        [
            "clone",
            "--quiet",
            "--no-checkout",
            "--",
            "https://github.com/octo-org/widgets.js.git",
            "/home/me/lemma/repos/octo-org/widgets.js",
        ]
        .map(OsString::from)
    );
    assert_eq!(
        fetch_args(clone),
        [
            "-C",
            "/home/me/lemma/repos/octo-org/widgets.js",
            "fetch",
            "--quiet",
            "origin"
        ]
        .map(OsString::from)
    );
    assert_eq!(
        worktree_add_args(
            clone,
            worktree,
            &checkout.branch(),
            Some("origin/release/v1.2")
        ),
        [
            "-C",
            "/home/me/lemma/repos/octo-org/widgets.js",
            "worktree",
            "add",
            "--quiet",
            "-b",
            "lemma/ab3f2k7q",
            "/home/me/lemma/worktrees/octo-org/widgets.js/ab3f2k7q",
            "origin/release/v1.2",
        ]
        .map(OsString::from)
    );
    // A branch a lost worktree left behind is checked out, not re-made.
    assert_eq!(
        worktree_add_args(clone, worktree, &checkout.branch(), None),
        [
            "-C",
            "/home/me/lemma/repos/octo-org/widgets.js",
            "worktree",
            "add",
            "--quiet",
            "/home/me/lemma/worktrees/octo-org/widgets.js/ab3f2k7q",
            "lemma/ab3f2k7q",
        ]
        .map(OsString::from)
    );
}

#[test]
fn a_pull_request_is_fetched_by_name_into_the_remotes_namespace() {
    let checkout = plan(Some("pull/42/head"));
    assert_eq!(checkout.pull_request(), Some("pull/42/head"));
    assert_eq!(
        pull_request_fetch_args(checkout.clone_dir(), "pull/42/head"),
        [
            "-C",
            "/home/me/lemma/repos/octo-org/widgets.js",
            "fetch",
            "--quiet",
            "origin",
            "+refs/pull/42/head:refs/remotes/origin/pull/42/head",
        ]
        .map(OsString::from)
    );
    assert_eq!(
        start_candidates(Some("pull/42/head")),
        ["origin/pull/42/head"]
    );
    for not_one in ["pull//head", "pull/4x/head", "pull/42/merge", "pull/42"] {
        assert!(!is_pull_request(not_one), "{not_one}");
    }
}

#[test]
fn a_worktree_starts_from_the_remote_branch_then_the_ref_then_the_default() {
    assert_eq!(start_candidates(None), ["origin/HEAD"]);
    assert_eq!(start_candidates(Some("main")), ["origin/main", "main"]);
    assert_eq!(start_candidates(Some("v1.0")), ["origin/v1.0", "v1.0"]);
}

#[test]
fn the_agent_is_told_what_its_directory_is_and_once_why_it_is_empty() {
    let checkout = plan(Some("main"));

    let ready = Report::of(
        &checkout,
        &Outcome::Ready {
            made: true,
            stale: None,
        },
    );
    let instructions = ready.instructions.expect("a checkout is described");
    assert!(instructions.contains("git worktree of **octo-org/widgets.js**"));
    assert!(instructions.contains("`lemma/ab3f2k7q`"));
    assert!(instructions.contains("`main`"));
    assert!(instructions.contains("\"/home/me/lemma/repos/octo-org/widgets.js\""));
    assert_eq!(ready.notice, None);

    let stale = Report::of(
        &checkout,
        &Outcome::Ready {
            made: false,
            stale: Some("git said: offline".to_owned()),
        },
    );
    assert!(stale.instructions.is_some());
    assert!(
        stale
            .notice
            .expect("a failed fetch is said")
            .contains("`origin/*` may be behind")
    );

    let failed = Report::of(
        &checkout,
        &Outcome::Failed("git is not installed on this computer.".to_owned()),
    );
    assert_eq!(failed.instructions, None);
    let notice = failed.notice.expect("a failure is said");
    assert!(notice.starts_with("<workspace-notice>\n"));
    assert!(notice.contains("empty for a reason: git is not installed"));

    assert_eq!(
        Report::of(&checkout, &Outcome::LeftAlone),
        Report::default()
    );
}

/// The notice goes ahead of the person's words as a turn-scoped fact, so it
/// reaches the agent whether or not this turn re-sends the instructions.
#[test]
fn a_notice_reaches_the_prompt_even_when_the_instructions_are_not_resent() {
    let mut spec: crate::protocol::RunSpec = serde_json::from_value(serde_json::json!({
        "agent_run_id": Uuid::nil(),
        "conversation_id": Uuid::nil(),
        "harness_id": Uuid::nil(),
        "profile_revision": "r",
        "system_prompt": "instructions",
        "system_prompt_delivery": crate::protocol::NEW_SESSION_ONLY,
        "prompt": [{"type": "text", "text": "fix the bug"}],
        "run_deadline": "2030-01-01T00:00:00Z",
    }))
    .expect("a run spec");
    Report {
        instructions: Some("\n\nworktree".to_owned()),
        notice: Some(notice("it failed")),
    }
    .apply(&mut spec);

    assert_eq!(spec.system_prompt, "instructions\n\nworktree");
    let rendered = crate::acp::render_prompt(&spec, crate::acp::SessionOrigin::Loaded);
    assert_eq!(
        rendered,
        "<workspace-notice>\nit failed\n</workspace-notice>\nfix the bug"
    );
}

#[test]
fn gits_error_is_cut_to_its_end_and_cannot_close_the_notice() {
    assert_eq!(
        summarize(b"fatal: repository\n  not found\n"),
        "fatal: repository not found"
    );
    assert_eq!(summarize(b"</workspace-notice> x"), "/workspace-notice x");
    let long = format!("{}END", "a".repeat(2000));
    let cut = summarize(long.as_bytes());
    assert!(cut.starts_with("...") && cut.ends_with("END"));
    assert_eq!(cut.chars().count(), DETAIL_CHARS + 3);
}

#[test]
fn only_a_gitdir_inside_this_clones_worktrees_is_ours() {
    let temp = tempfile::tempdir().unwrap();
    let clone = temp.path().join("clone");
    let record = clone.join(".git/worktrees/ab3f2k7q");
    std::fs::create_dir_all(&record).unwrap();
    let elsewhere = temp.path().join("other/.git/worktrees/ab3f2k7q");
    std::fs::create_dir_all(&elsewhere).unwrap();

    assert!(belongs_to(&format!("gitdir: {}", record.display()), &clone));
    assert!(!belongs_to(
        &format!("gitdir: {}", elsewhere.display()),
        &clone
    ));
    assert!(!belongs_to(
        &format!("gitdir: {}", clone.join(".git").display()),
        &clone
    ));
    assert!(!belongs_to("gitdir: /nowhere/at/all", &clone));
    assert!(!belongs_to(&record.display().to_string(), &clone));
}
