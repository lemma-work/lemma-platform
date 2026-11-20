"""Put a conversation's project on disk before the agent looks for it.

A conversation started against a repo works in a git worktree of its own,
``<workspace root>/worktrees/{owner}/{repo}/{slug}`` on branch ``lemma/{slug}``,
made from one clone per repository at ``<workspace root>/repos/{owner}/{repo}``
(``domain/project_repo``). The directory itself always exists -- every
``get_session`` calls ``_ensure_workspace_directory`` -- so without this step the
agent would open a project and find an empty folder, which is precisely the
ambiguity ``WORKSPACE_RECREATED_NOTICE`` exists to prevent elsewhere.

Three rules shape what this does and does not do:

- **Clone when there is nothing there; otherwise only fetch.** The clone is
  never pulled, reset, switched, or cleaned, and neither is a worktree once
  made. A fetch moves none of that -- only the remote-tracking refs, which every
  worktree of the clone shares -- so a conversation opened on a clone made days
  ago still starts from where `origin` is now.
- **A worktree is made once, from `origin`.** On a branch of its own, from the
  conversation's ref or the repository's default branch, so no conversation
  inherits another's uncommitted work or its branch. A worktree whose directory
  was lost comes back on the same branch, with the commits it had.
- **Say when it failed.** A clone that fails (repo gone, account without access),
  a worktree that could not be made, and a fetch that left `origin/*` stale each
  return a notice the caller shows the agent once, rather than leaving it to
  infer something from an empty directory.

A run on a shared pod machine already starts in a directory of its own
(``agent_machines.run_workdir``), which is made a worktree of the same clone,
detached rather than on a branch: run directories are cleared by the machine
once nothing has written to them for an hour, so a branch per run would only
pile up. The person's own worktree sweep does not run there.

A pull request (``pull/123/head``) is not among what a clone fetches, so it is
fetched by name, into ``origin/pull/123/head``, and checked out from there --
a review sees the pull request itself rather than a branch that may be gone.

Once a checkout has run, the person's finished worktrees are cleared
(``worktree_sweep``): this is where worktrees are made, and the one moment the
sandbox is known to be awake for it.

A conversation from before worktrees recorded the clone itself as its cwd and
keeps working there, as it always has.

Like the credential bridge next door, the work is marker-guarded in Redis so a
session running twenty commands pays for this once.

``prepare_project_directory`` at the bottom is the entry point both workspace
tools use, so the shell and the interpreter cannot drift into disagreeing about
what is in the directory they share.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from app.core.config import settings
from app.core.infrastructure.redis.client import get_redis
from app.core.log.log import get_logger
from app.modules.agent.domain.project_repo import ProjectRepo, worktree_branch
from app.modules.agent.services.run_phase_spans import run_phase
from app.modules.agent.domain.context import ToolContext
from app.modules.agent.infrastructure.tools.workspace_cli.github_credential_bridge import (
    ensure_github_credentials,
)
from app.modules.agent.infrastructure.tools.workspace_cli.worktree_sweep import (
    sweep_finished_worktrees,
)
from sandbox_runtime.paths import RUNS_ROOT

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = get_logger(__name__)

_MARKER_KEY_PREFIX = "workspace:github-project:v1"
# A present checkout is a durable fact about the disk, not a credential, so this
# only needs to outlive a working session rather than be re-verified often.
_PRESENT_TTL_SECONDS = 45 * 60
# A failed clone is cached briefly: long enough that a burst of commands doesn't
# re-attempt a slow network clone every time, short enough that connecting the
# right account and carrying on works without waiting out a long TTL.
_FAILED_TTL_SECONDS = 5 * 60
_CLONE_TIMEOUT_SECONDS = 300
# The step's own exit statuses, told apart from a clone that failed, which git
# reports as 128: only the fetch failed (sysexits' EX_TEMPFAIL), and the
# worktree could not be made.
_FETCH_FAILED_EXIT = 75
_WORKTREE_FAILED_EXIT = 76
# A pull request's head, which only a fetch by name brings into a clone.
_PULL_REQUEST_REF = re.compile(r"^pull/[0-9]+/head$")


def _clone_or_fetch(*, url: str, path: str, ref: str | None) -> str:
    """Clone into ``path`` if nothing is there, else fetch; mark a failed fetch.

    The `[ -e ... ]` guard is what makes this idempotent and safe to run at the
    head of any command: an existing clone is never cloned over. A fetch that
    fails sets `stale` for the end of the step, so a worktree is still made.
    """
    pull_request = bool(ref and _PULL_REQUEST_REF.match(ref))
    branch = f"--branch {shlex.quote(ref)} " if ref and not pull_request else ""
    clone = shlex.quote(path)
    command = (
        f"if [ -e {shlex.quote(path + '/.git')} ]; then "
        f"git -C {clone} fetch --quiet origin || stale=1; "
        f"else git clone {branch}{shlex.quote(url)} {clone} || exit; fi"
    )
    if pull_request:
        refspec = shlex.quote(f"+refs/{ref}:refs/remotes/origin/{ref}")
        command += f"; git -C {clone} fetch --quiet origin {refspec} || stale=1"
    return command


def _start_point(git: str, ref: str | None) -> str:
    """Set `start` to `origin/<ref>`, or the ref itself for a tag or a commit."""
    target = ref or "HEAD"
    return (
        f"start={shlex.quote('origin/' + target)}; "
        f'{git} rev-parse --verify --quiet "$start^{{commit}}" >/dev/null '
        f"|| start={shlex.quote(target)}"
    )


def _checkout_command(
    *,
    url: str,
    clone_dir: str,
    ref: str | None,
    worktree: str | None,
    slug: str | None,
    run_dir: str | None = None,
) -> str:
    """The whole step, as one shell command.

    With a worktree: the clone, then this conversation's worktree on
    ``lemma/{slug}`` -- from `origin/<ref>` (or the ref itself, for a tag or a
    commit), else from `origin/HEAD` -- unless it is already there. `worktree
    prune` first, so a worktree whose directory went missing can be made again;
    its branch, if it survived, is reused rather than re-made, so its commits
    are where they were.

    With a run directory instead: that directory, detached at the same start.
    """
    steps = [_clone_or_fetch(url=url, path=clone_dir, ref=ref)]
    git = f"git -C {shlex.quote(clone_dir)}"
    if run_dir is not None:
        steps.append(
            f"if [ ! -e {shlex.quote(run_dir + '/.git')} ]; then "
            f"{git} worktree prune; {_start_point(git, ref)}; "
            f'{git} worktree add --quiet --detach {shlex.quote(run_dir)} "$start" '
            f"|| exit {_WORKTREE_FAILED_EXIT}; fi"
        )
    elif worktree is not None and slug is not None:
        branch = worktree_branch(slug)
        path = shlex.quote(worktree)
        steps.append(
            f"if [ ! -e {shlex.quote(worktree + '/.git')} ]; then "
            f"{git} worktree prune; {_start_point(git, ref)}; "
            f"if {git} show-ref --verify --quiet {shlex.quote('refs/heads/' + branch)}; "
            f"then {git} worktree add --quiet {path} {shlex.quote(branch)}; "
            f'else {git} worktree add --quiet -b {shlex.quote(branch)} {path} "$start"; '
            f"fi || exit {_WORKTREE_FAILED_EXIT}; fi"
        )
    steps.append(f'[ -z "${{stale-}}" ] || exit {_FETCH_FAILED_EXIT}')
    return "; ".join(steps)


def _worktree_of(ctx: ToolContext, repo: ProjectRepo) -> tuple[str, str] | None:
    """This conversation's worktree and its slug, or None if it works in the clone."""
    cwd = ctx.get_workspace_cwd()
    slug = repo.worktree_slug(cwd)
    return (repo.worktree_dir(slug), slug) if slug is not None else None


def _run_dir_of(workspace_session) -> str | None:
    """The run's own directory on a shared pod machine, which the session
    already starts in (`get_workspace_session`), or None everywhere else."""
    cwd = getattr(workspace_session, "cwd", None)
    if isinstance(cwd, str) and cwd.startswith(f"{RUNS_ROOT}/"):
        return cwd.rstrip("/")
    return None


async def ensure_project_checkout(
    ctx: ToolContext,
    workspace_session,
    *,
    redis: "Redis | None" = None,
    sweep: Callable[[ToolContext, object], Awaitable[None]] | None = None,
) -> str | None:
    """Clone or fetch the conversation's repo, and make its worktree if it has none.

    Returns a notice to show the agent once, or None when there is nothing to
    say -- which is the case both when the checkout is already there and when
    this conversation has no repo at all.

    Exceptions are deliberately not swallowed here. The caller decides whether a
    failed checkout should still let the underlying command run (it should), and
    that decision needs the failure to reach it.

    ``redis`` is the marker store and ``sweep`` the step that clears finished
    worktrees after this one is made, both arguments so a test can read what
    was marked, and leave the database alone, without replacing anything in
    this module.
    """
    repo = ctx.workspace_repo
    if repo is None:
        return None
    session_id = workspace_session.session_id
    if not session_id:
        return None

    if redis is None:
        redis = get_redis(url=settings.redis_url)
    marker_key = f"{_MARKER_KEY_PREFIX}:{session_id}:{repo.full_name}"
    if await redis.exists(marker_key):
        return None

    run_dir = _run_dir_of(workspace_session)
    worktree = None if run_dir else _worktree_of(ctx, repo)
    here = run_dir or (worktree[0] if worktree else repo.clone_dir)
    url = f"https://github.com/{repo.owner}/{repo.repo}.git"
    result = await workspace_session.exec_command(
        cmd=_checkout_command(
            url=url,
            clone_dir=repo.clone_dir,
            ref=repo.ref,
            worktree=worktree[0] if worktree else None,
            slug=worktree[1] if worktree else None,
            run_dir=run_dir,
        ),
        timeout=_CLONE_TIMEOUT_SECONDS,
    )
    notice = await _settle(
        result, redis=redis, marker_key=marker_key, repo=repo, here=here
    )
    if run_dir is not None:
        # A shared machine clears its own run directories (`machine_lifecycle`).
        return notice
    # The sandbox is awake, which is the only time the person's finished
    # worktrees are cleared (`worktree_sweep`); it never raises.
    await (sweep or sweep_finished_worktrees)(ctx, workspace_session)
    return notice


async def _settle(
    result: dict, *, redis: "Redis", marker_key: str, repo: ProjectRepo, here: str
) -> str | None:
    """Mark how the checkout went, and say what the agent needs to hear of it."""
    # git's own stderr is the most useful thing anyone can say here -- it already
    # distinguishes a missing repo from an access failure from a bad ref.
    detail = str(result.get("stderr") or "").strip()
    exit_code = result.get("exit_code")
    if exit_code == 0:
        await redis.set(marker_key, "present", ex=_PRESENT_TTL_SECONDS)
        return None
    if exit_code == _FETCH_FAILED_EXIT:
        # The checkout is there and as usable as before, so it is marked present
        # and said once: an agent comparing against `origin/*` needs to know it
        # may be comparing against an old one.
        await redis.set(marker_key, "present", ex=_PRESENT_TTL_SECONDS)
        return (
            f"[workspace notice] {repo.full_name} is checked out in {here}, "
            "but fetching it failed, so `origin/*` may be behind GitHub. "
            f"{detail}".strip()
        )

    await redis.set(marker_key, "failed", ex=_FAILED_TTL_SECONDS)
    logger.warning(
        "agent.workspace_cli.github_project_clone_failed.degraded",
        repo=repo.full_name,
        exit_code=exit_code,
    )
    if exit_code == _WORKTREE_FAILED_EXIT:
        return (
            f"[workspace notice] {repo.full_name} is cloned in {repo.clone_dir}, "
            f"but this conversation's worktree could not be made in {here}, so "
            f"this directory is empty for a reason. {detail}".strip()
        )
    return (
        f"[workspace notice] {repo.full_name} could not be cloned into "
        f"{repo.clone_dir}, so {here} is empty for a reason. "
        f"{detail}".strip()
    )


async def prepare_project_directory(
    ctx: ToolContext,
    workspace_session,
    *,
    wanted: bool,
    ensure_credentials: Callable[..., Awaitable[str | None]] | None = None,
    ensure_checkout: Callable[..., Awaitable[str | None]] | None = None,
) -> str | None:
    """Put the conversation's project on disk before either tool looks for it.

    Shared by the shell and the interpreter deliberately. The two run in the
    same directory, so they have to find the same thing in it: only
    `exec_command` used to clone, and an agent whose first call was
    `execute_python` opened a project and found an empty folder with nothing
    said about why. Returns a notice to show once, or None.

    A broken bridge (DB/Redis hiccup, a sandbox write failure) is logged and
    swallowed rather than blocking the work: the command or the code runs
    uncredentialed and fails with git's own native error, exactly as it would
    with no bridge at all.

    The two steps are arguments so that swallow can be exercised for real: a
    test replaces only the failing step and the exception still travels out of
    it, through the handler below, and back to the tool that called this.

    Defaulted to ``None`` and resolved here rather than in the signature, so
    the two names still resolve at call time: a default argument binds at
    import, which would silently make a double installed on this module's
    ``ensure_github_credentials`` unreachable — and leave the test that
    installed it passing.
    """
    if not wanted:
        return None
    if ensure_credentials is None:
        ensure_credentials = ensure_github_credentials
    if ensure_checkout is None:
        ensure_checkout = ensure_project_checkout
    try:
        with run_phase("tool.workspace.credentials"):
            # Both told, credential first: a refused credential is usually
            # why the clone after it failed.
            notices = [
                await ensure_credentials(ctx, workspace_session),
                await ensure_checkout(ctx, workspace_session),
            ]
            return "\n".join(n for n in notices if n) or None
    except Exception:
        logger.warning(
            "agent.workspace_cli.github_credential_bridge_failed.degraded",
            exc_info=True,
        )
        return None
