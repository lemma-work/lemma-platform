"""Remove the worktrees a person's conversations are finished with.

Which worktrees are finished is ``domain/worktree_sweep``'s rule; this carries
it out in the person's sandbox, and three things shape how:

- **Only in a sandbox that is already awake.** The sweep rides on a repository
  conversation's checkout (``github_project.ensure_project_checkout``), so a
  sandbox that is asleep is never woken to tidy it. Worktrees are cleared where
  they are made, so their number follows the conversations in use.
- **Out of the agent's way.** The removals are started in the sandbox and left
  to run -- deleting a tree with its ``node_modules`` takes seconds -- and are
  started at most once a day per sandbox, marked in Redis.
- **Nothing git would call losing work.** ``git worktree remove`` without
  ``--force`` refuses a tree with changes or untracked files; that tree stays,
  and the next day's sweep tries it again. The branch is never deleted, so
  commits that never reached GitHub stay in the clone, and a conversation that
  comes back gets its worktree made again on that branch.

The conversations are read in a short unit of work of their own, which has
closed before the sandbox is asked anything. Everything here is best effort:
a sweep that fails is logged and the agent's command runs regardless.
"""

from __future__ import annotations

import shlex
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID

from app.core.config import settings
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.redis.client import get_redis
from app.core.log.log import get_logger
from app.modules.agent.domain.context import ToolContext
from app.modules.agent.domain.worktree_sweep import WorktreeUse, finished_worktrees
from app.modules.agent.infrastructure.db.worktree_uses import WorktreeUses

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = get_logger(__name__)

_MARKER_KEY_PREFIX = "workspace:worktree-sweep:v1"
_SWEEP_INTERVAL_SECONDS = 24 * 60 * 60
# How long the call waits on the sweep it started: enough to hand it to the
# sandbox, not to see it finish. It goes on running after this returns.
_START_WAIT_SECONDS = 5

#: The read the rule weighs: a person's conversations in one workspace.
ReadUses = Callable[[UUID, str], Awaitable[list[WorktreeUse]]]


async def _uses_of(user_id: UUID, workspace_id: str) -> list[WorktreeUse]:
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        return await WorktreeUses(uow).of_person(user_id, workspace_id=workspace_id)


def sweep_command(worktrees: Sequence[tuple[str, str]]) -> str:
    """Remove each worktree, given as ``(clone, worktree)``, that git agrees is
    clean; say which went.

    One line per worktree on stdout, ``removed <path>`` or ``kept <path>``, for
    whoever reads the process's output. A directory that is already gone is
    neither: there is nothing there to remove or keep.
    """
    steps = []
    for clone, path in worktrees:
        steps.append(
            f"if [ -e {shlex.quote(path + '/.git')} ]; then "
            f"if git -C {shlex.quote(clone)} worktree remove "
            f"{shlex.quote(path)} >/dev/null 2>&1; "
            f"then echo {shlex.quote('removed ' + path)}; "
            f"else echo {shlex.quote('kept ' + path)}; fi; fi"
        )
    return "; ".join(steps)


async def sweep_finished_worktrees(
    ctx: ToolContext,
    workspace_session,
    *,
    redis: "Redis | None" = None,
    read_uses: ReadUses | None = None,
    now: datetime | None = None,
) -> None:
    """Start removing the person's finished worktrees, once a day per sandbox.

    Never raises: it runs ahead of the agent's own command, which must not
    fail because a tidy-up did. ``redis``, ``read_uses`` and ``now`` are
    arguments so a test reads what was marked and answers for the database
    without replacing anything in this module.
    """
    try:
        if redis is None:
            redis = get_redis(url=settings.redis_url)
        marker_key = f"{_MARKER_KEY_PREFIX}:{ctx.user_id}:{ctx.workspace_id}"
        # Marked before looking, so two conversations starting together do not
        # both sweep; a sweep that then fails waits for tomorrow's.
        if not await redis.set(
            marker_key, "swept", ex=_SWEEP_INTERVAL_SECONDS, nx=True
        ):
            return
        uses = await (read_uses or _uses_of)(ctx.user_id, ctx.workspace_id)
        finished = finished_worktrees(
            uses,
            now=now or datetime.now(timezone.utc),
            keep=ctx.get_workspace_cwd(),
        )
        if not finished:
            return
        result = await workspace_session.exec_command(
            cmd=sweep_command(
                [(repo.clone_dir, repo.worktree_dir(slug)) for repo, slug in finished]
            ),
            timeout=_START_WAIT_SECONDS,
            yield_time_ms=0,
        )
        # Still running is success here: that is what being left to run means.
        if not result.get("success"):
            logger.warning(
                "agent.workspace_cli.worktree_sweep_not_started.degraded",
                worktrees=len(finished),
                error=str(result.get("error")),
            )
            return
        logger.info(
            "agent.workspace_cli.worktree_sweep_started", worktrees=len(finished)
        )
    except Exception:
        logger.warning(
            "agent.workspace_cli.worktree_sweep_failed.degraded", exc_info=True
        )
