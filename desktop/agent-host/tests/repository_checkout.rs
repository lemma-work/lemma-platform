//! A repository conversation's folder made a worktree by real `git`.
//!
//! Against a bare repository on disk standing in for GitHub, in a temporary
//! workspace root: the first conversation clones, a second only fetches, a
//! pull request's head is fetched by name, and a folder that already holds
//! something -- the conversation's own worktree from an earlier turn, or files
//! that are not a worktree -- is left exactly as it was. Failures start
//! nothing and leave the folder empty for the agent to be told about.
//!
//! The fixture's commits are made with an identity and signing switched off on
//! the command line, so the person's own git configuration does not decide
//! whether the fixture can be built; the checkout under test runs with
//! whatever configuration the machine has, as it does for a person.

use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::Duration;

use lemma_agent_host::protocol::RunRepository;
use lemma_agent_host::repository_checkout::{Checkout, Outcome, Request, ensure};
use uuid::Uuid;

fn git(directory: &Path, arguments: &[&str]) -> String {
    let output = Command::new("git")
        .arg("-C")
        .arg(directory)
        .args([
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.com",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "tag.gpgsign=false",
            "-c",
            "init.defaultBranch=main",
        ])
        .args(arguments)
        .env("GIT_TERMINAL_PROMPT", "0")
        .output()
        .expect("git runs");
    assert!(
        output.status.success(),
        "git {arguments:?}: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    String::from_utf8_lossy(&output.stdout).trim().to_owned()
}

fn git_program() -> PathBuf {
    PathBuf::from("git")
}

/// A bare repository with `main`, a `feature` branch, a tag, and a pull
/// request head that no branch contains. Returns its path and the pull
/// request's commit.
struct Fixture {
    _temp: tempfile::TempDir,
    remote: PathBuf,
    root: PathBuf,
    pull_request_commit: String,
    feature_commit: String,
    main_commit: String,
}

fn fixture() -> Fixture {
    let temp = tempfile::tempdir().expect("temp dir");
    let remote = temp.path().join("origin.git");
    let seed = temp.path().join("seed");
    std::fs::create_dir_all(&remote).unwrap();
    std::fs::create_dir_all(&seed).unwrap();
    git(&remote, &["init", "--quiet", "--bare"]);
    git(&seed, &["init", "--quiet"]);
    std::fs::write(seed.join("README.md"), "main\n").unwrap();
    git(&seed, &["add", "README.md"]);
    git(&seed, &["commit", "--quiet", "-m", "main"]);
    let main_commit = git(&seed, &["rev-parse", "HEAD"]);
    git(&seed, &["tag", "v1.0"]);
    git(&seed, &["checkout", "--quiet", "-b", "feature"]);
    std::fs::write(seed.join("feature.txt"), "feature\n").unwrap();
    git(&seed, &["add", "feature.txt"]);
    git(&seed, &["commit", "--quiet", "-m", "feature"]);
    let feature_commit = git(&seed, &["rev-parse", "HEAD"]);
    git(&seed, &["checkout", "--quiet", "--detach", "main"]);
    std::fs::write(seed.join("pr.txt"), "pull request\n").unwrap();
    git(&seed, &["add", "pr.txt"]);
    git(&seed, &["commit", "--quiet", "-m", "pull request"]);
    let pull_request_commit = git(&seed, &["rev-parse", "HEAD"]);
    let remote_arg = remote.to_str().unwrap();
    git(&seed, &["remote", "add", "origin", remote_arg]);
    git(
        &seed,
        &["push", "--quiet", "origin", "main", "feature", "v1.0"],
    );
    git(
        &seed,
        &["push", "--quiet", "origin", "HEAD:refs/pull/7/head"],
    );
    // As GitHub's: HEAD names the default branch.
    git(&remote, &["symbolic-ref", "HEAD", "refs/heads/main"]);
    // A home folder, as there always is; `~/lemma` itself is made on first use.
    std::fs::create_dir_all(temp.path().join("home")).unwrap();
    let root = temp.path().join("home/lemma");
    Fixture {
        _temp: temp,
        remote,
        root,
        pull_request_commit,
        feature_commit,
        main_commit,
    }
}

/// The folder `conversation_directory::prepare` would have made for the run.
fn conversation(fixture: &Fixture, target: Uuid, reference: Option<&str>, slug: &str) -> Checkout {
    let cwd = format!("/home/user/lemma/worktrees/octo/widgets/{slug}");
    let folder = lemma_agent_host::conversation_directory::prepare(&fixture.root, target, &cwd)
        .expect("the conversation folder");
    let checkout = Checkout::plan(
        &fixture.root,
        &RunRepository {
            owner: "octo".to_owned(),
            repo: "widgets".to_owned(),
            reference: reference.map(str::to_owned),
            slug: slug.to_owned(),
        },
    )
    .expect("a valid repository");
    assert_eq!(checkout.worktree_dir(), folder);
    checkout
}

async fn check_out(
    fixture: &Fixture,
    target: Uuid,
    checkout: &Checkout,
    remote: &str,
    git: &Path,
    timeout: Duration,
) -> Outcome {
    ensure(&Request {
        root: &fixture.root,
        target,
        checkout,
        git,
        remote,
        timeout,
    })
    .await
}

fn remote_of(fixture: &Fixture) -> String {
    fixture.remote.to_string_lossy().into_owned()
}

#[tokio::test]
async fn the_first_conversation_clones_and_gets_a_worktree_on_its_own_branch() {
    let fixture = fixture();
    let target = Uuid::new_v4();
    let checkout = conversation(&fixture, target, None, "first0");

    let outcome = check_out(
        &fixture,
        target,
        &checkout,
        &remote_of(&fixture),
        &git_program(),
        Duration::from_secs(60),
    )
    .await;

    assert_eq!(
        outcome,
        Outcome::Ready {
            made: true,
            stale: None
        }
    );
    let worktree = checkout.worktree_dir();
    assert_eq!(
        std::fs::read_to_string(worktree.join("README.md")).unwrap(),
        "main\n"
    );
    assert_eq!(git(worktree, &["branch", "--show-current"]), "lemma/first0");
    assert_eq!(git(worktree, &["rev-parse", "HEAD"]), fixture.main_commit);
    // The clone holds the objects and no working tree, and nothing is left
    // over from the clone made beside it.
    assert!(checkout.clone_dir().join(".git").is_dir());
    assert!(!checkout.clone_dir().join("README.md").exists());
    let leftovers: Vec<_> = std::fs::read_dir(checkout.clone_dir().parent().unwrap())
        .unwrap()
        .flatten()
        .map(|entry| entry.file_name())
        .collect();
    assert_eq!(leftovers, ["widgets"]);
}

#[tokio::test]
async fn a_second_conversation_fetches_the_clone_and_starts_from_its_ref() {
    let fixture = fixture();
    let target = Uuid::new_v4();
    let remote = remote_of(&fixture);
    let first = conversation(&fixture, target, None, "first0");
    check_out(
        &fixture,
        target,
        &first,
        &remote,
        &git_program(),
        Duration::from_secs(60),
    )
    .await;

    let second = conversation(&fixture, target, Some("feature"), "second0");
    let outcome = check_out(
        &fixture,
        target,
        &second,
        &remote,
        &git_program(),
        Duration::from_secs(60),
    )
    .await;

    assert_eq!(
        outcome,
        Outcome::Ready {
            made: true,
            stale: None
        }
    );
    assert_eq!(
        git(second.worktree_dir(), &["rev-parse", "HEAD"]),
        fixture.feature_commit
    );
    assert_eq!(
        git(second.worktree_dir(), &["branch", "--show-current"]),
        "lemma/second0"
    );
    // Its own tree: the first conversation's is still on main.
    assert!(!first.worktree_dir().join("feature.txt").exists());

    let tagged = conversation(&fixture, target, Some("v1.0"), "tagged0");
    check_out(
        &fixture,
        target,
        &tagged,
        &remote,
        &git_program(),
        Duration::from_secs(60),
    )
    .await;
    assert_eq!(
        git(tagged.worktree_dir(), &["rev-parse", "HEAD"]),
        fixture.main_commit
    );
}

#[tokio::test]
async fn a_pull_request_head_is_fetched_by_name_and_checked_out() {
    let fixture = fixture();
    let target = Uuid::new_v4();
    let checkout = conversation(&fixture, target, Some("pull/7/head"), "review0");

    let outcome = check_out(
        &fixture,
        target,
        &checkout,
        &remote_of(&fixture),
        &git_program(),
        Duration::from_secs(60),
    )
    .await;

    assert_eq!(
        outcome,
        Outcome::Ready {
            made: true,
            stale: None
        }
    );
    assert_eq!(
        git(checkout.worktree_dir(), &["rev-parse", "HEAD"]),
        fixture.pull_request_commit
    );
    assert_eq!(
        git(
            checkout.clone_dir(),
            &["rev-parse", "refs/remotes/origin/pull/7/head"]
        ),
        fixture.pull_request_commit
    );
}

/// A later turn finds its worktree and touches nothing in it: no fetch, no
/// reset, the agent's uncommitted work exactly where it was -- even with the
/// remote gone.
#[tokio::test]
async fn an_existing_worktree_is_left_exactly_as_it_is() {
    let fixture = fixture();
    let target = Uuid::new_v4();
    let remote = remote_of(&fixture);
    let checkout = conversation(&fixture, target, None, "again0");
    check_out(
        &fixture,
        target,
        &checkout,
        &remote,
        &git_program(),
        Duration::from_secs(60),
    )
    .await;
    std::fs::write(checkout.worktree_dir().join("README.md"), "edited\n").unwrap();
    std::fs::write(checkout.worktree_dir().join("new.txt"), "mine\n").unwrap();
    std::fs::remove_dir_all(&fixture.remote).unwrap();

    let outcome = check_out(
        &fixture,
        target,
        &checkout,
        &remote,
        Path::new("/nonexistent/git"),
        Duration::from_secs(60),
    )
    .await;

    assert_eq!(
        outcome,
        Outcome::Ready {
            made: false,
            stale: None
        }
    );
    assert_eq!(
        std::fs::read_to_string(checkout.worktree_dir().join("README.md")).unwrap(),
        "edited\n"
    );
    assert!(checkout.worktree_dir().join("new.txt").exists());
}

/// git 2.48's `worktree.useRelativePaths` writes the worktree's `.git` file
/// with a path relative to the worktree, which git resolves against that
/// folder and not against whatever directory the host happens to run in. The
/// file is rewritten here as such a git writes it, so the test holds on a git
/// that predates the setting.
#[tokio::test]
async fn a_worktree_whose_git_file_is_relative_is_still_this_clones() {
    let fixture = fixture();
    let target = Uuid::new_v4();
    let remote = remote_of(&fixture);
    let checkout = conversation(&fixture, target, None, "relative0");
    check_out(
        &fixture,
        target,
        &checkout,
        &remote,
        &git_program(),
        Duration::from_secs(60),
    )
    .await;
    let dot_git = checkout.worktree_dir().join(".git");
    let absolute = std::fs::read_to_string(&dot_git).unwrap();
    let record = Path::new(absolute.trim().strip_prefix("gitdir:").unwrap().trim());
    let under_root = record
        .strip_prefix(std::fs::canonicalize(&fixture.root).unwrap())
        .expect("the clone's record of the worktree is under the root");
    // worktrees/octo/widgets/relative0 -> back up to the root.
    let relative = Path::new("../../../..").join(under_root);
    std::fs::write(&dot_git, format!("gitdir: {}\n", relative.display())).unwrap();
    assert_eq!(
        git(checkout.worktree_dir(), &["branch", "--show-current"]),
        "lemma/relative0"
    );

    let outcome = check_out(
        &fixture,
        target,
        &checkout,
        &remote,
        Path::new("/nonexistent/git"),
        Duration::from_secs(60),
    )
    .await;

    assert_eq!(
        outcome,
        Outcome::Ready {
            made: false,
            stale: None
        }
    );
}

#[tokio::test]
async fn a_folder_holding_anything_else_is_never_cloned_over() {
    let fixture = fixture();
    let target = Uuid::new_v4();
    let checkout = conversation(&fixture, target, None, "busy0");
    std::fs::write(checkout.worktree_dir().join("notes.txt"), "mine\n").unwrap();

    let outcome = check_out(
        &fixture,
        target,
        &checkout,
        &remote_of(&fixture),
        &git_program(),
        Duration::from_secs(60),
    )
    .await;

    assert_eq!(outcome, Outcome::LeftAlone);
    let entries: Vec<_> = std::fs::read_dir(checkout.worktree_dir())
        .unwrap()
        .flatten()
        .map(|entry| entry.file_name())
        .collect();
    assert_eq!(entries, ["notes.txt"]);
    assert!(!checkout.clone_dir().exists());

    // Nor is a `.git` that points at some other repository taken for this one.
    let other = conversation(&fixture, target, None, "other0");
    std::fs::write(other.worktree_dir().join(".git"), "gitdir: /elsewhere\n").unwrap();
    let outcome = check_out(
        &fixture,
        target,
        &other,
        &remote_of(&fixture),
        &git_program(),
        Duration::from_secs(60),
    )
    .await;
    assert_eq!(outcome, Outcome::LeftAlone);
}

/// No access, no git, a ref that is not there, no time: the folder is left
/// empty and in place for the agent, with a reason to give it, and a failed
/// clone leaves nothing half-made behind for the next turn to trust.
#[tokio::test]
async fn every_failure_leaves_an_empty_folder_and_a_reason() {
    struct Case {
        slug: &'static str,
        reference: Option<&'static str>,
        remote: String,
        program: PathBuf,
        timeout: Duration,
        expected: &'static str,
    }
    let fixture = fixture();
    let target = Uuid::new_v4();
    let missing = fixture.remote.with_file_name("missing.git");
    let cases = [
        Case {
            slug: "noaccess0",
            reference: None,
            remote: missing.to_string_lossy().into_owned(),
            program: git_program(),
            timeout: Duration::from_secs(60),
            expected: "cloning it failed",
        },
        Case {
            slug: "nogit0",
            reference: None,
            remote: remote_of(&fixture),
            program: PathBuf::from("/nonexistent/git"),
            timeout: Duration::from_secs(60),
            expected: "git could not be started",
        },
        Case {
            slug: "notime0",
            reference: None,
            remote: remote_of(&fixture),
            program: git_program(),
            timeout: Duration::ZERO,
            expected: "did not finish within 0 seconds",
        },
        // Last: it is the one case whose clone succeeds.
        Case {
            slug: "badref0",
            reference: Some("no-such-branch"),
            remote: remote_of(&fixture),
            program: git_program(),
            timeout: Duration::from_secs(60),
            expected: "`no-such-branch` is not a branch, tag or commit",
        },
    ];
    for Case {
        slug,
        reference,
        remote,
        program,
        timeout,
        expected,
    } in cases
    {
        let checkout = conversation(&fixture, target, reference, slug);
        let outcome = check_out(&fixture, target, &checkout, &remote, &program, timeout).await;
        let Outcome::Failed(reason) = outcome else {
            panic!("{slug}: {outcome:?}");
        };
        assert!(reason.contains(expected), "{slug}: {reason}");
        assert!(checkout.worktree_dir().is_dir(), "{slug}");
        assert_eq!(
            std::fs::read_dir(checkout.worktree_dir()).unwrap().count(),
            0,
            "{slug}"
        );
        if slug != "badref0" {
            let clone = checkout.clone_dir();
            assert!(
                !clone.exists() || std::fs::read_dir(clone).unwrap().count() == 0,
                "{slug}: a failed clone left files behind"
            );
        }
    }
}

/// The clone is one workspace's: a second paired workspace on the same
/// computer is refused it rather than handed its history and hooks.
#[tokio::test]
async fn another_paired_workspaces_clone_is_not_used() {
    let fixture = fixture();
    let remote = remote_of(&fixture);
    let first = Uuid::new_v4();
    let mine = conversation(&fixture, first, None, "mine0");
    check_out(
        &fixture,
        first,
        &mine,
        &remote,
        &git_program(),
        Duration::from_secs(60),
    )
    .await;

    let second = Uuid::new_v4();
    let theirs = conversation(&fixture, second, None, "theirs0");
    let outcome = check_out(
        &fixture,
        second,
        &theirs,
        &remote,
        &git_program(),
        Duration::from_secs(60),
    )
    .await;

    let Outcome::Failed(reason) = outcome else {
        panic!("{outcome:?}");
    };
    assert!(reason.contains("another paired workspace"), "{reason}");
}

/// A checkout that failed is not tried again on every turn: within a few
/// minutes the next turn gets the same reason without git being run, as the
/// cloud sandbox's failure marker does. The second call here has a working git
/// and remote, so only a remembered failure can still fail it.
#[tokio::test]
async fn a_failed_checkout_is_not_retried_on_the_next_turn() {
    let fixture = fixture();
    let target = Uuid::new_v4();
    let remote = remote_of(&fixture);
    let checkout = conversation(&fixture, target, None, "retry0");

    let first = check_out(
        &fixture,
        target,
        &checkout,
        &remote,
        Path::new("/nonexistent/git"),
        Duration::from_secs(60),
    )
    .await;
    let Outcome::Failed(reason) = &first else {
        panic!("{first:?}");
    };
    assert!(reason.contains("git could not be started"), "{reason}");

    let again = check_out(
        &fixture,
        target,
        &checkout,
        &remote,
        &git_program(),
        Duration::from_secs(60),
    )
    .await;

    assert_eq!(again, first);
    assert!(!checkout.clone_dir().join(".git").exists());
    assert_eq!(
        std::fs::read_dir(checkout.worktree_dir()).unwrap().count(),
        0
    );

    // Another conversation in the same repository is its own attempt.
    let other = conversation(&fixture, target, None, "retry1");
    let outcome = check_out(
        &fixture,
        target,
        &other,
        &remote,
        &git_program(),
        Duration::from_secs(60),
    )
    .await;
    assert!(
        matches!(outcome, Outcome::Ready { made: true, .. }),
        "{outcome:?}"
    );
}

/// A clone git cannot read is not reported as a ref that does not exist: the
/// agent is given git's own error, not told its branch is missing.
#[tokio::test]
async fn a_damaged_clone_is_not_mistaken_for_a_missing_ref() {
    let fixture = fixture();
    let target = Uuid::new_v4();
    let remote = remote_of(&fixture);
    let first = conversation(&fixture, target, None, "intact0");
    check_out(
        &fixture,
        target,
        &first,
        &remote,
        &git_program(),
        Duration::from_secs(60),
    )
    .await;
    std::fs::write(
        first.clone_dir().join(".git").join("config"),
        "this is not git configuration\n",
    )
    .unwrap();

    let damaged = conversation(&fixture, target, Some("feature"), "damaged0");
    let outcome = check_out(
        &fixture,
        target,
        &damaged,
        &remote,
        &git_program(),
        Duration::from_secs(60),
    )
    .await;

    let Outcome::Failed(reason) = outcome else {
        panic!("{outcome:?}");
    };
    assert!(
        !reason.contains("is not a branch, tag or commit"),
        "{reason}"
    );
    assert!(reason.contains("config"), "{reason}");
}
