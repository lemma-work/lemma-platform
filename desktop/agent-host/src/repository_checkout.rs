//! Make a repository conversation's directory a real worktree of it.
//!
//! A conversation opened against a GitHub repository works in
//! `~/lemma/worktrees/{owner}/{repo}/{slug}`, which `conversation_directory`
//! makes as an empty folder. In the cloud sandbox the workspace tools turn that
//! folder into a worktree before the agent's first command
//! (`lemma-backend/.../workspace_cli/github_project.py`); this is the same job
//! for a coding agent on the person's own computer, done with the person's own
//! `git` -- their credential helper, their SSH keys, their `gh` sign-in --
//! before the agent starts in the folder. The layout is the sandbox's, so one
//! conversation means the same thing in both places:
//!
//! - **One clone per repository**, `~/lemma/repos/{owner}/{repo}`, shared by
//!   every worktree of it and claimed for the paired workspace that made it.
//!   Cloned once, without a working tree of its own (nobody works in it), and
//!   only fetched after that: never pulled, reset, switched or cleaned.
//! - **A worktree per conversation** on `lemma/{slug}`, from `origin/<ref>` --
//!   or the ref itself for a tag or a commit, or `origin/HEAD` with no ref. A
//!   pull request (`pull/<n>/head`) is fetched by name into
//!   `origin/pull/<n>/head` first, since a clone does not fetch it.
//! - **A folder that already holds something is left exactly as it is.** The
//!   conversation's own worktree is not fetched, reset or even looked into on
//!   a later turn, and a folder holding anything else is not cloned over.
//! - **Failing never stops the run.** No `git`, no access, no network, a ref
//!   that does not exist: the agent starts in the empty folder anyway and is
//!   told once, in plain words, why it is empty.
//!
//! Every value from the run spec is checked against the backend's own patterns
//! (`domain/project_repo.py`) before it reaches a path or an argument, every
//! `git` is run with an argument list rather than through a shell, and the
//! paths are derived here: the checkout happens only when the worktree derived
//! from the names is the folder `workspace_cwd` already resolved to. So a run
//! spec can name no folder for git that it could not already name for the
//! agent.

use std::collections::HashMap;
use std::ffi::OsString;
use std::path::{Path, PathBuf};
use std::sync::{Arc, LazyLock, Mutex, PoisonError};
use std::time::Duration;

use uuid::Uuid;

use crate::protocol::RunRepository;

/// The longest a run waits for its checkout, clone included.
///
/// The cloud sandbox's figure (`_CLONE_TIMEOUT_SECONDS`), for the same reason:
/// a first clone of a large repository over a slow link is legitimately
/// minutes, and past this it is more useful to start the agent in the empty
/// folder and say so than to keep the person waiting. A later turn only reads
/// a file, so this is paid on the first turn at most. The run's own deadline
/// bounds it further.
pub const CHECKOUT_TIMEOUT: Duration = Duration::from_secs(300);

/// How much of git's own error is passed on to the agent. Its last lines are
/// the useful ones: they already tell a missing repository from a refused
/// credential from a bad ref.
const DETAIL_CHARS: usize = 600;

/// A repository checkout, with every name checked and every path derived.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Checkout {
    owner: String,
    repo: String,
    reference: Option<String>,
    slug: String,
    clone_dir: PathBuf,
    worktree_dir: PathBuf,
}

impl Checkout {
    /// The checkout `repository` asks for under `root` (`~/lemma`), or why
    /// this host will not make it.
    pub fn plan(root: &Path, repository: &RunRepository) -> Result<Self, String> {
        let RunRepository {
            owner,
            repo,
            reference,
            slug,
        } = repository;
        if !valid_owner(owner) {
            return Err("the repository owner is not a GitHub account name".to_owned());
        }
        if !valid_repo(repo) {
            return Err("the repository name is not a GitHub repository name".to_owned());
        }
        if reference.as_deref().is_some_and(|value| !valid_ref(value)) {
            return Err("the ref is not one this host will pass to git".to_owned());
        }
        if !valid_slug(slug) {
            return Err("the worktree name is not one Lemma makes".to_owned());
        }
        Ok(Self {
            owner: owner.clone(),
            repo: repo.clone(),
            reference: reference.clone(),
            slug: slug.clone(),
            clone_dir: root.join("repos").join(owner).join(repo),
            worktree_dir: root.join("worktrees").join(owner).join(repo).join(slug),
        })
    }

    #[must_use]
    pub fn full_name(&self) -> String {
        format!("{}/{}", self.owner, self.repo)
    }

    /// The branch the worktree starts on.
    #[must_use]
    pub fn branch(&self) -> String {
        format!("lemma/{}", self.slug)
    }

    #[must_use]
    pub fn clone_dir(&self) -> &Path {
        &self.clone_dir
    }

    #[must_use]
    pub fn worktree_dir(&self) -> &Path {
        &self.worktree_dir
    }

    /// The clone's path relative to the workspace root, as the directory
    /// registry keys it.
    fn clone_relative(&self) -> String {
        format!("repos/{}/{}", self.owner, self.repo)
    }

    /// Where the repository is cloned from. HTTPS, as in the cloud sandbox: the
    /// person's credential helper answers for it, and a person who uses SSH
    /// has `url.<ssh>.insteadOf` set for exactly this.
    #[must_use]
    pub fn github_remote(&self) -> String {
        format!("https://github.com/{}/{}.git", self.owner, self.repo)
    }

    fn pull_request(&self) -> Option<&str> {
        self.reference
            .as_deref()
            .filter(|reference| is_pull_request(reference))
    }
}

/// What came of a checkout.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Outcome {
    /// The folder is the conversation's worktree: made now (`made`), or
    /// already there from an earlier turn. `stale` is a fetch that failed, so
    /// `origin/*` may be behind.
    Ready { made: bool, stale: Option<String> },
    /// The folder already holds something that is not this worktree, and is
    /// left exactly as it is.
    LeftAlone,
    /// No worktree; the folder is as it was. The reason is for the agent.
    Failed(String),
}

/// What the agent is told about its checkout.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Report {
    /// Appended to the run's instructions, beside the native directory.
    pub instructions: Option<String>,
    /// Said once, on this turn, ahead of the person's words.
    pub notice: Option<String>,
}

impl Report {
    #[must_use]
    pub fn of(checkout: &Checkout, outcome: &Outcome) -> Self {
        let name = checkout.full_name();
        match outcome {
            Outcome::Ready { stale, .. } => Self {
                instructions: Some(worktree_instructions(checkout)),
                notice: stale.as_ref().map(|detail| {
                    notice(&format!(
                        "{name} is checked out in this directory, but fetching it \
                         failed, so `origin/*` may be behind GitHub. {detail}"
                    ))
                }),
            },
            Outcome::LeftAlone => Self::default(),
            Outcome::Failed(reason) => Self {
                instructions: None,
                notice: Some(notice(&format!(
                    "{name} could not be checked out in this directory with this \
                     computer's git, so the directory is empty for a reason: \
                     {reason} Your native tools work here as usual."
                ))),
            },
        }
    }

    /// Put the report into the run it was made for.
    pub fn apply(self, spec: &mut crate::protocol::RunSpec) {
        if let Some(instructions) = self.instructions {
            spec.system_prompt.push_str(&instructions);
        }
        if let Some(notice) = self.notice {
            spec.prompt
                .insert(0, serde_json::json!({"type": "text", "text": notice}));
        }
    }
}

fn worktree_instructions(checkout: &Checkout) -> String {
    let start = checkout.reference.as_ref().map_or_else(
        || "the repository's default branch".to_owned(),
        |reference| format!("`{reference}`"),
    );
    // A path is data: encoded, as the native directory itself is.
    let clone = serde_json::to_string(&checkout.clone_dir.to_string_lossy())
        .expect("strings serialize to JSON");
    format!(
        "\n\nThis native directory is a git worktree of **{name}**, started on \
         branch `{branch}` from {start} for this conversation, by this \
         computer's own git and with the person's own GitHub access. Other \
         conversations in this repository have worktrees of their own. The \
         clone behind them, {clone} (JSON-encoded path), is shared: never work \
         in it directly. Push your branch, or rename it, when the work is ready.",
        name = checkout.full_name(),
        branch = checkout.branch(),
    )
}

fn notice(text: &str) -> String {
    format!("<workspace-notice>\n{}\n</workspace-notice>", text.trim())
}

/// One checkout to make.
pub struct Request<'a> {
    /// The workspace root, `~/lemma`.
    pub root: &'a Path,
    /// The paired workspace the run belongs to, which claims the clone.
    pub target: Uuid,
    pub checkout: &'a Checkout,
    pub git: &'a Path,
    /// Where to clone from: `Checkout::github_remote`, except in tests.
    pub remote: &'a str,
    pub timeout: Duration,
}

/// Make the conversation's worktree if its folder is empty, and say how it went.
pub async fn ensure(request: &Request<'_>) -> Outcome {
    let checkout = request.checkout;
    if let Some(outcome) = already_there(checkout) {
        return outcome;
    }
    // One checkout of a clone at a time: two conversations in one repository
    // started together would otherwise both find no clone and both make one.
    let lock = clone_lock(checkout.clone_dir());
    let _guard = lock.lock().await;
    // Asked again under the lock: a sub-agent's run shares its parent's
    // folder, and the parent's run may have made the worktree meanwhile.
    if let Some(outcome) = already_there(checkout) {
        return outcome;
    }
    let git = Git {
        program: request.git,
        deadline: tokio::time::Instant::now() + request.timeout,
        timeout: request.timeout,
    };
    let outcome = match make_worktree(request, &git).await {
        Ok(stale) => Outcome::Ready { made: true, stale },
        Err(reason) => Outcome::Failed(reason),
    };
    // A failed `worktree add` may take the folder with it, and the agent is
    // started in it regardless.
    if !checkout.worktree_dir().exists()
        && let Err(error) = std::fs::create_dir_all(checkout.worktree_dir())
    {
        tracing::warn!(%error, "could not make the conversation folder again");
    }
    outcome
}

fn already_there(checkout: &Checkout) -> Option<Outcome> {
    match existing_worktree(checkout) {
        Existing::Ours => Some(Outcome::Ready {
            made: false,
            stale: None,
        }),
        Existing::Other => Some(Outcome::LeftAlone),
        Existing::Empty => None,
    }
}

async fn make_worktree(request: &Request<'_>, git: &Git<'_>) -> Result<Option<String>, String> {
    let checkout = request.checkout;
    let clone = crate::conversation_directory::claim_shared(
        request.root,
        request.target,
        &checkout.clone_relative(),
    )
    .map_err(|error| format!("{error}."))?;
    let mut stale = None;
    if clone.join(".git").exists() {
        if let Err(error) = git.run(&fetch_args(&clone)).await {
            stale = Some(error.describe(git));
        }
    } else {
        clone_into(&clone, request.remote, git).await?;
    }
    if let Some(pull_request) = checkout.pull_request()
        && let Err(error) = git
            .run(&pull_request_fetch_args(&clone, pull_request))
            .await
    {
        stale.get_or_insert_with(|| error.describe(git));
    }
    // A worktree whose folder went missing is still registered, and would
    // refuse to be made again until it is pruned.
    let _ = git.run(&git_in(&clone, ["worktree", "prune"])).await;
    let branch = checkout.branch();
    let branch_ref = format!("refs/heads/{branch}");
    let branch_exists = git
        .run(&git_in(
            &clone,
            ["show-ref", "--verify", "--quiet", branch_ref.as_str()],
        ))
        .await
        .is_ok();
    let start = if branch_exists {
        None
    } else {
        Some(start_point(&clone, checkout, git).await?)
    };
    git.run(&worktree_add_args(
        &clone,
        checkout.worktree_dir(),
        &branch,
        start.as_deref(),
    ))
    .await
    .map_err(|error| {
        format!(
            "the worktree could not be made from the clone at {}: {}",
            clone.display(),
            error.describe(git)
        )
    })?;
    Ok(stale)
}

/// Clone beside the clone's folder and move it into place, so a clone cut
/// short -- the timeout, or the run stopped -- never leaves a half-written
/// repository where the next turn would take it for a finished one.
async fn clone_into(clone: &Path, remote: &str, git: &Git<'_>) -> Result<(), String> {
    let has_files = std::fs::read_dir(clone)
        .map_err(|error| format!("{} could not be read: {error}.", clone.display()))?
        .next()
        .is_some();
    if has_files {
        return Err(format!(
            "{} already holds files and is not a clone, so it was left alone.",
            clone.display()
        ));
    }
    let parent = clone
        .parent()
        .ok_or_else(|| "the clone has no parent folder.".to_owned())?;
    let name = clone
        .file_name()
        .map(|name| name.to_string_lossy().into_owned())
        .unwrap_or_default();
    // What a clone killed mid-way (the host itself stopping) left behind.
    let leftover = format!(".{name}.partial-");
    for entry in std::fs::read_dir(parent).into_iter().flatten().flatten() {
        if entry.file_name().to_string_lossy().starts_with(&leftover) {
            let _ = std::fs::remove_dir_all(entry.path());
        }
    }
    let partial = Partial(parent.join(format!("{leftover}{}", Uuid::new_v4())));
    git.run(&clone_args(remote, &partial.0))
        .await
        .map_err(|error| format!("cloning it failed: {}", error.describe(git)))?;
    // Removed and replaced rather than renamed over, which Windows refuses
    // even for an empty folder. Under the clone's lock, so nobody is between.
    std::fs::remove_dir(clone)
        .and_then(|()| std::fs::rename(&partial.0, clone))
        .map_err(|error| format!("the finished clone could not be moved into place: {error}."))
}

/// A clone in progress, removed on every way out; once it has been moved into
/// place there is nothing left at its path to remove.
struct Partial(PathBuf);

impl Drop for Partial {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

/// The first of `start_candidates` that names a commit in the clone.
async fn start_point(clone: &Path, checkout: &Checkout, git: &Git<'_>) -> Result<String, String> {
    for candidate in start_candidates(checkout.reference.as_deref()) {
        let commit = format!("{candidate}^{{commit}}");
        let found = git
            .run(&git_in(
                clone,
                ["rev-parse", "--verify", "--quiet", commit.as_str()],
            ))
            .await;
        match found {
            Ok(_) => return Ok(candidate),
            Err(GitError::TimedOut) => return Err(GitError::TimedOut.describe(git)),
            Err(_) => {}
        }
    }
    Err(match &checkout.reference {
        Some(reference) => format!("`{reference}` is not a branch, tag or commit of it."),
        None => "it has no default branch to start from.".to_owned(),
    })
}

/// Where a worktree may start, best first: the remote's branch, then the ref
/// itself (a tag or a commit), as `_start_point` does in the cloud sandbox.
#[must_use]
pub fn start_candidates(reference: Option<&str>) -> Vec<String> {
    match reference {
        None => vec!["origin/HEAD".to_owned()],
        Some(reference) if is_pull_request(reference) => vec![format!("origin/{reference}")],
        Some(reference) => vec![format!("origin/{reference}"), reference.to_owned()],
    }
}

#[must_use]
pub fn clone_args(remote: &str, into: &Path) -> Vec<OsString> {
    vec![
        "clone".into(),
        "--quiet".into(),
        "--no-checkout".into(),
        "--".into(),
        remote.into(),
        into.into(),
    ]
}

#[must_use]
pub fn fetch_args(clone: &Path) -> Vec<OsString> {
    git_in(clone, ["fetch", "--quiet", "origin"])
}

#[must_use]
pub fn pull_request_fetch_args(clone: &Path, pull_request: &str) -> Vec<OsString> {
    let refspec = format!("+refs/{pull_request}:refs/remotes/origin/{pull_request}");
    git_in(clone, ["fetch", "--quiet", "origin", refspec.as_str()])
}

#[must_use]
pub fn worktree_add_args(
    clone: &Path,
    worktree: &Path,
    branch: &str,
    start: Option<&str>,
) -> Vec<OsString> {
    let mut arguments = git_in(clone, ["worktree", "add", "--quiet"]);
    match start {
        // A branch left by a worktree whose folder was lost keeps its commits.
        None => {
            arguments.push(worktree.into());
            arguments.push(branch.into());
        }
        Some(start) => {
            arguments.push("-b".into());
            arguments.push(branch.into());
            arguments.push(worktree.into());
            arguments.push(start.into());
        }
    }
    arguments
}

fn git_in<'a>(clone: &Path, rest: impl IntoIterator<Item = &'a str>) -> Vec<OsString> {
    let mut arguments: Vec<OsString> = vec!["-C".into(), clone.into()];
    arguments.extend(rest.into_iter().map(OsString::from));
    arguments
}

enum Existing {
    Empty,
    Ours,
    Other,
}

/// What the conversation's folder already holds.
///
/// "Ours" is read from the worktree's `.git` file, which names the clone's
/// record of it, so a later turn runs no git at all.
fn existing_worktree(checkout: &Checkout) -> Existing {
    let folder = checkout.worktree_dir();
    let dot_git = folder.join(".git");
    if let Ok(metadata) = std::fs::symlink_metadata(&dot_git) {
        let ours = metadata.is_file()
            && std::fs::read_to_string(&dot_git)
                .is_ok_and(|text| belongs_to(text.trim(), checkout.clone_dir()));
        return if ours {
            Existing::Ours
        } else {
            Existing::Other
        };
    }
    let holds_anything =
        std::fs::read_dir(folder).is_ok_and(|mut entries| entries.next().is_some());
    if holds_anything {
        Existing::Other
    } else {
        Existing::Empty
    }
}

fn belongs_to(dot_git: &str, clone: &Path) -> bool {
    let Some(gitdir) = dot_git.strip_prefix("gitdir:") else {
        return false;
    };
    // git writes the path it resolved, which on macOS is `/private/var/...`
    // for a `/var/...` it was given.
    let (Ok(gitdir), Ok(clone)) = (
        std::fs::canonicalize(gitdir.trim()),
        std::fs::canonicalize(clone),
    ) else {
        return false;
    };
    gitdir.starts_with(clone.join(".git").join("worktrees"))
}

fn clone_lock(clone: &Path) -> Arc<tokio::sync::Mutex<()>> {
    static LOCKS: LazyLock<Mutex<HashMap<PathBuf, Arc<tokio::sync::Mutex<()>>>>> =
        LazyLock::new(Mutex::default);
    Arc::clone(
        LOCKS
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .entry(clone.to_path_buf())
            .or_default(),
    )
}

struct Git<'a> {
    program: &'a Path,
    deadline: tokio::time::Instant,
    timeout: Duration,
}

enum GitError {
    Spawn(std::io::Error),
    TimedOut,
    Failed(String),
}

impl GitError {
    fn describe(&self, git: &Git<'_>) -> String {
        match self {
            Self::Spawn(error) => format!("git could not be started: {error}."),
            Self::TimedOut => format!(
                "git did not finish within {} seconds.",
                git.timeout.as_secs()
            ),
            Self::Failed(detail) if detail.is_empty() => "git failed.".to_owned(),
            Self::Failed(detail) => format!("git said: {detail}"),
        }
    }
}

impl Git<'_> {
    async fn run(&self, arguments: &[OsString]) -> Result<String, GitError> {
        let mut command = tokio::process::Command::new(self.program);
        command
            .args(arguments)
            // Never wait on a person who is not there: no terminal prompt, no
            // Git Credential Manager dialog. A helper that already holds a
            // credential still answers.
            .env("GIT_TERMINAL_PROMPT", "0")
            .env("GCM_INTERACTIVE", "never")
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .kill_on_drop(true);
        // Its own group, so the helpers a clone starts (`git-remote-https`,
        // `index-pack`, ssh) go with it on a timeout.
        #[cfg(unix)]
        command.process_group(0);
        #[cfg(windows)]
        command.creation_flags(0x0800_0000);
        let child = command.spawn().map_err(GitError::Spawn)?;
        let group = child.id();
        match tokio::time::timeout_at(self.deadline, child.wait_with_output()).await {
            Err(_) => {
                kill_group(group);
                Err(GitError::TimedOut)
            }
            Ok(Err(error)) => Err(GitError::Spawn(error)),
            Ok(Ok(output)) if output.status.success() => {
                Ok(String::from_utf8_lossy(&output.stdout).into_owned())
            }
            Ok(Ok(output)) => Err(GitError::Failed(summarize(&output.stderr))),
        }
    }
}

#[cfg(unix)]
fn kill_group(group: Option<u32>) {
    if let Some(pid) = group
        .and_then(|group| i32::try_from(group).ok())
        .and_then(rustix::process::Pid::from_raw)
    {
        let _ = rustix::process::kill_process_group(pid, rustix::process::Signal::KILL);
    }
}

#[cfg(not(unix))]
fn kill_group(_group: Option<u32>) {}

/// git's error as one line the agent can read: its last lines, whitespace
/// collapsed, nothing that could close the notice around it.
fn summarize(stderr: &[u8]) -> String {
    let text = String::from_utf8_lossy(stderr)
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .replace(['<', '>'], "");
    let count = text.chars().count();
    if count <= DETAIL_CHARS {
        return text;
    }
    let tail: String = text.chars().skip(count - DETAIL_CHARS).collect();
    format!("...{tail}")
}

/// The person's own `git`, or `None` when this computer has none.
///
/// Homebrew's before the system's, since that is the one a person who
/// installed it runs. On macOS `/usr/bin/git` is a stub until the Command
/// Line Tools are installed, and running the stub opens an installer dialog
/// in front of someone who only started a conversation -- so it counts only
/// when `xcode-select` reports a developer directory, which it does without
/// any dialog.
pub async fn find_git() -> Option<PathBuf> {
    let mut search = vec![
        PathBuf::from("/opt/homebrew/bin"),
        PathBuf::from("/usr/local/bin"),
    ];
    search.extend(crate::adapters::executable_search_paths());
    let git = crate::adapters::resolve_executable_in("git", search)?;
    #[cfg(target_os = "macos")]
    if git == Path::new("/usr/bin/git") && !developer_tools_installed().await {
        return None;
    }
    Some(git)
}

#[cfg(target_os = "macos")]
async fn developer_tools_installed() -> bool {
    let probe = tokio::process::Command::new("/usr/bin/xcode-select")
        .arg("-p")
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .kill_on_drop(true)
        .status();
    matches!(
        tokio::time::timeout(Duration::from_secs(10), probe).await,
        Ok(Ok(status)) if status.success()
    )
}

// The backend's patterns (`domain/project_repo.py`), written out: GitHub's own
// naming rules, which leave no room for a path separator, a `..` or a leading
// `-` that git would read as an option.

fn valid_owner(value: &str) -> bool {
    let mut characters = value.chars();
    characters
        .next()
        .is_some_and(|first| first.is_ascii_alphanumeric())
        && value.len() <= 39
        && characters.all(|character| character.is_ascii_alphanumeric() || character == '-')
}

fn valid_repo(value: &str) -> bool {
    let mut characters = value.chars();
    characters
        .next()
        .is_some_and(|first| first.is_ascii_alphanumeric() || first == '_')
        && value.len() <= 100
        && characters
            .all(|character| character.is_ascii_alphanumeric() || "_.-".contains(character))
}

fn valid_ref(value: &str) -> bool {
    let mut characters = value.chars();
    characters
        .next()
        .is_some_and(|first| first.is_ascii_alphanumeric() || first == '_')
        && value.len() <= 255
        && characters
            .all(|character| character.is_ascii_alphanumeric() || "_./-".contains(character))
}

fn valid_slug(value: &str) -> bool {
    (1..=64).contains(&value.len())
        && value
            .chars()
            .all(|character| character.is_ascii_lowercase() || character.is_ascii_digit())
}

/// `pull/<n>/head`, which only a fetch by name brings into a clone.
fn is_pull_request(reference: &str) -> bool {
    reference
        .strip_prefix("pull/")
        .and_then(|rest| rest.strip_suffix("/head"))
        .is_some_and(|number| !number.is_empty() && number.chars().all(|c| c.is_ascii_digit()))
}

#[cfg(test)]
mod tests;
