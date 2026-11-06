"""When a conversation is finished with its repository worktree.

Every conversation opened in a GitHub repository gets a worktree of its own
(``project_repo``): a whole checked-out tree, so a person who opens many fills
their sandbox. This decides which of them may go; the sweep that removes them
is ``workspace_cli/worktree_sweep``.

Removing a worktree's *directory* loses nothing as long as the tree is clean.
Its commits live on its branch, in the clone, and the branch is never removed:
it is the conversation's record, holds whatever never reached GitHub, and is
how a conversation that runs again gets its worktree back
(``github_project``). Only a clean tree is ever removed, which git itself
enforces. So this decides *when*, and can afford to be plain about it.

A worktree is finished when no conversation working in it has a run going, and
every one of them -- a root, and the sub-agents that share its directory -- is

- **archived, and quiet for an hour.** Archiving is how a person says they are
  done. The hour outlasts the checkout marker a conversation's last commands
  left (``github_project._PRESENT_TTL_SECONDS``), so one brought back from the
  archive makes its worktree again rather than trusting a marker that says it
  is there.
- **or quiet for two weeks.** Few conversations are ever archived. One left
  this long and then resumed pays for a fresh checkout of its branch and for
  reinstalling whatever its ignored files held, which is the price of not
  filling the sandbox with every conversation that was ever opened.

Quiet is ``last_activity_at``, which every run's start and finish moves.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.modules.agent.domain.project_repo import ProjectRepo, worktree_at

ARCHIVED_QUIET = timedelta(hours=1)
IDLE_QUIET = timedelta(days=14)
#: Worktrees removed by one sweep, at most. It runs daily, so a backlog drains
#: over days instead of in one command whose length nothing bounds.
SWEEP_LIMIT = 100


@dataclass(frozen=True, slots=True)
class WorktreeUse:
    """One conversation working in a directory, as much as the rule needs."""

    cwd: str
    archived: bool
    last_activity_at: datetime
    running: bool


def _finished(use: WorktreeUse, *, now: datetime) -> bool:
    if use.running:
        return False
    quiet = now - use.last_activity_at
    return quiet >= IDLE_QUIET or (use.archived and quiet >= ARCHIVED_QUIET)


def finished_worktrees(
    uses: Iterable[WorktreeUse],
    *,
    now: datetime,
    keep: str | None = None,
    limit: int = SWEEP_LIMIT,
) -> list[tuple[ProjectRepo, str]]:
    """The worktrees no conversation needs any more, longest quiet first.

    ``keep`` is the directory of the conversation asking, which is never
    finished whatever its row says: it is about to be worked in.

    A directory that is not a worktree -- a scratchpad, a conversation from
    before worktrees working in the clone itself -- is never returned, however
    quiet: the clone is every conversation's, and the rest are not this rule's.
    """
    by_cwd: dict[str, list[WorktreeUse]] = {}
    for use in uses:
        by_cwd.setdefault(use.cwd.rstrip("/"), []).append(use)
    kept = keep.rstrip("/") if keep else None
    finished: list[tuple[datetime, str, tuple[ProjectRepo, str]]] = []
    for cwd, sharing in by_cwd.items():
        worktree = worktree_at(cwd)
        if worktree is None or cwd == kept:
            continue
        if all(_finished(use, now=now) for use in sharing):
            last = max(use.last_activity_at for use in sharing)
            finished.append((last, cwd, worktree))
    finished.sort(key=lambda entry: (entry[0], entry[1]))
    return [worktree for _, _, worktree in finished[:limit]]
