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

/// The clone and worktree paths as this platform joins them (`\` on Windows).
fn clone_path() -> &'static str {
    let path = Path::new("/home/me/lemma")
        .join("repos")
        .join("octo-org")
        .join("widgets.js");
    Box::leak(path.to_string_lossy().into_owned().into_boxed_str())
}

fn worktree_path() -> &'static str {
    let path = Path::new("/home/me/lemma")
        .join("worktrees")
        .join("octo-org")
        .join("widgets.js")
        .join("ab3f2k7q");
    Box::leak(path.to_string_lossy().into_owned().into_boxed_str())
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
            clone_path(),
        ]
        .map(OsString::from)
    );
    assert_eq!(
        fetch_args(clone),
        ["-C", clone_path(), "fetch", "--quiet", "origin"].map(OsString::from)
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
            clone_path(),
            "worktree",
            "add",
            "--quiet",
            "-b",
            "lemma/ab3f2k7q",
            worktree_path(),
            "origin/release/v1.2",
        ]
        .map(OsString::from)
    );
    // A branch a lost worktree left behind is checked out, not re-made.
    assert_eq!(
        worktree_add_args(clone, worktree, &checkout.branch(), None),
        [
            "-C",
            clone_path(),
            "worktree",
            "add",
            "--quiet",
            worktree_path(),
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
            clone_path(),
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
    assert!(instructions.contains(&serde_json::to_string(clone_path()).unwrap()));
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

    // A folder that already held something else is not the repository, and
    // the agent starting in it is told so rather than left to assume it is.
    let left_alone = Report::of(&checkout, &Outcome::LeftAlone);
    assert_eq!(left_alone.instructions, None);
    let notice = left_alone.notice.expect("a folder left alone is said");
    assert!(notice.starts_with("<workspace-notice>\n"));
    assert!(notice.contains("octo-org/widgets.js was not checked out in this directory"));
    assert!(notice.contains("left exactly as it is"));
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
    let folder = temp.path().join("worktrees/ab3f2k7q");
    std::fs::create_dir_all(&folder).unwrap();

    let ours = |dot_git: &str| belongs_to(dot_git, &folder, &clone);
    assert!(ours(&format!("gitdir: {}", record.display())));
    assert!(!ours(&format!("gitdir: {}", elsewhere.display())));
    assert!(!ours(&format!("gitdir: {}", clone.join(".git").display())));
    assert!(!ours("gitdir: /nowhere/at/all"));
    assert!(!ours(&record.display().to_string()));
    // Relative to the worktree, as `worktree.useRelativePaths` writes it.
    assert!(ours("gitdir: ../../clone/.git/worktrees/ab3f2k7q"));
    assert!(!ours("gitdir: ../../other/.git/worktrees/ab3f2k7q"));
}

/// A failure is remembered for one conversation and one ref, and for a few
/// minutes only: long enough that turn after turn does not clone again, short
/// enough that connecting the right account and carrying on works.
#[test]
fn a_failure_is_remembered_for_a_few_minutes_and_for_its_own_checkout_only() {
    let target = Uuid::new_v4();
    let main = plan(Some("main"));
    let feature = plan(Some("feature"));
    let mut failures = Failures::default();
    let failed_at = Instant::now();
    failures.record(
        failure_key(target, &main),
        "cloning it failed.".to_owned(),
        failed_at,
    );

    let later = failed_at + Duration::from_secs(60);
    assert_eq!(
        failures.recent(&failure_key(target, &main), later),
        Some("cloning it failed.".to_owned())
    );
    assert_eq!(failures.recent(&failure_key(target, &feature), later), None);
    assert_eq!(
        failures.recent(&failure_key(Uuid::new_v4(), &main), later),
        None
    );
    assert_eq!(
        failures.recent(&failure_key(target, &main), failed_at + RETRY_AFTER),
        None
    );
    // An expired failure is dropped when the next one is recorded.
    failures.record(
        failure_key(target, &feature),
        "git failed.".to_owned(),
        failed_at + RETRY_AFTER,
    );
    assert_eq!(failures.0.len(), 1);
}

#[test]
fn a_failure_notice_says_it_will_be_tried_again() {
    let report = Report::of(
        &plan(None),
        &Outcome::Failed("cloning it failed.".to_owned()),
    );
    assert!(
        report
            .notice
            .expect("a failure is said")
            .contains("tried again in a few minutes")
    );
}
