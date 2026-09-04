"""Put a conversation's project on disk before the agent looks for it.

A conversation started against a repo (``metadata.repo``) resolves its cwd to
``/workspace/repos/{owner}/{repo}``. The directory itself always exists -- every
``get_session`` calls ``_ensure_workspace_directory`` -- so without this step the
agent would open a project and find an empty folder, which is precisely the
ambiguity ``WORKSPACE_RECREATED_NOTICE`` exists to prevent elsewhere.

Two rules shape what this does and does not do:

- **Clone only when there is nothing there.** An existing checkout is never
  fetched, pulled, reset, or cleaned. One sandbox per user means two
  conversations can share a checkout, and it may hold uncommitted work from
  either of them; silently moving a working tree under an agent is worse than
  leaving it a commit behind.
- **Say when it failed.** A clone that fails (repo gone, account without access)
  returns a notice the caller shows the agent once, rather than leaving it to
  infer something from an empty directory.

Like the credential bridge next door, the work is marker-guarded in Redis so a
session running twenty commands pays for this once.

``prepare_project_directory`` at the bottom is the entry point both workspace
tools use, so the shell and the interpreter cannot drift into disagreeing about
what is in the directory they share.
"""

from __future__ import annotations

import shlex
from collections.abc import Awaitable, Callable

from app.core.config import settings
from app.core.infrastructure.redis.client import get_redis
from app.core.log.log import get_logger
from app.modules.agent.services.run_phase_spans import run_phase
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.workspace_cli.github_credential_bridge import (
    ensure_github_credentials,
)

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


def _clone_command(*, url: str, path: str, ref: str | None) -> str:
    branch = f"--branch {shlex.quote(ref)} " if ref else ""
    # The `[ -e ... ]` guard is what makes this idempotent and safe to run at the
    # head of any command: an existing checkout short-circuits to true and git is
    # never invoked at all.
    return (
        f"[ -e {shlex.quote(path + '/.git')} ] || "
        f"git clone {branch}{shlex.quote(url)} {shlex.quote(path)}"
    )


async def ensure_project_checkout(
    ctx: BaseAgentContext, workspace_session
) -> str | None:
    """Clone the conversation's repo if its directory is still empty.

    Returns a notice to show the agent once, or None when there is nothing to
    say -- which is the case both when the checkout is already there and when
    this conversation has no repo at all.

    Exceptions are deliberately not swallowed here. The caller decides whether a
    failed checkout should still let the underlying command run (it should), and
    that decision needs the failure to reach it.
    """
    repo = ctx.workspace_repo
    if repo is None:
        return None
    session_id = workspace_session.session_id
    if not session_id:
        return None

    redis = get_redis(url=settings.redis_url)
    marker_key = f"{_MARKER_KEY_PREFIX}:{session_id}:{repo.full_name}"
    if await redis.exists(marker_key):
        return None

    url = f"https://github.com/{repo.owner}/{repo.repo}.git"
    result = await workspace_session.exec_command(
        cmd=_clone_command(url=url, path=repo.cwd, ref=repo.ref),
        timeout=_CLONE_TIMEOUT_SECONDS,
    )
    if result.get("exit_code") == 0:
        await redis.set(marker_key, "present", ex=_PRESENT_TTL_SECONDS)
        return None

    await redis.set(marker_key, "failed", ex=_FAILED_TTL_SECONDS)
    logger.debug(
        "agent.workspace_cli.github_project_clone_failed.diagnostic",
        repo=repo.full_name,
        exit_code=result.get("exit_code"),
    )
    # git's own stderr is the most useful thing anyone can say here -- it already
    # distinguishes a missing repo from an access failure from a bad ref.
    detail = str(result.get("stderr") or "").strip()
    return (
        f"[workspace notice] {repo.full_name} could not be cloned into "
        f"{repo.cwd}, so this directory is empty for a reason. "
        f"{detail}".strip()
    )


async def prepare_project_directory(
    ctx: BaseAgentContext,
    workspace_session,
    *,
    wanted: bool,
    ensure_credentials: Callable[..., Awaitable[None]] | None = None,
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
            await ensure_credentials(ctx, workspace_session)
            return await ensure_checkout(ctx, workspace_session)
    except Exception:
        logger.warning(
            "agent.workspace_cli.github_credential_bridge_failed.degraded",
            exc_info=True,
        )
        return None
