"""Which repository a dispatched run asks the Agent Host to check out.

The cloud sandbox makes a repository conversation's directory a worktree of
it before the agent's first command (``workspace_cli/github_project``). A
coding agent on the person's own computer starts in the same directory under
``~/lemma``, and the host can do the same only if the run spec names the
repository and the worktree's slug (docs/architecture/agent-host.md, "A
repository conversation's folder").
"""

from __future__ import annotations

from app.modules.agent.agent_host.domain.host import AgentHostRunRepository
from app.modules.agent.domain.workspace_location import WorkspaceLocation


def run_repository(location: WorkspaceLocation) -> AgentHostRunRepository | None:
    """The repository the host should make ``location.cwd`` a worktree of.

    Only when the cwd is that repository's worktree: a conversation from before
    worktrees works in the clone itself, and a cwd set by hand is not a
    checkout anyone asked for, so neither is sent one.
    """
    repo = location.repo
    if repo is None:
        return None
    slug = repo.worktree_slug(location.cwd)
    if slug is None:
        return None
    return AgentHostRunRepository(
        owner=repo.owner, repo=repo.repo, ref=repo.ref, slug=slug
    )
