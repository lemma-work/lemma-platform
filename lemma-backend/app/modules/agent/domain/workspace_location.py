"""Single source of truth for a conversation's workspace + working directory.

Both the agent run and the approval executor must run in the *same* workspace
session and cwd, so the resolution lives here instead of being duplicated. The
location is the conversation's own columns:

- ``cwd`` — explicit working directory; a fresh root conversation gets a default
  of ``/workspace/c/{date}/{slug}``. Stamped at creation
  (``ConversationService._apply_inherited_cwd``) and read back thereafter.
- ``repo`` — a GitHub repository this conversation works in, as
  ``{owner, repo, ref?}``. It *derives* the cwd
  (``/workspace/repos/{owner}/{repo}``), so picking a project is the same act as
  picking a directory and there is no second source of truth to keep in step.
- ``workspace_id`` / ``workspace_name`` — selects the workspace; defaults to the
  single per-user workspace today.

None of them is a client's to set: a conversation's directory is where its
agent's shell starts and its files land, so a client that could write it could
point a run at another conversation's files.

A repo path is deliberately *not* conversation-scoped. One sandbox per user
means two conversations on the same project share one checkout, the same way two
terminals opened on one folder do — that is the point of picking a project
rather than a scratchpad, and the working directories stay separate because each
conversation already gets its own session and cwd inside that shared sandbox.

The pod filesystem's working directory (``/me/{suffix}``) is derived from the
workspace cwd by swapping the ``/workspace`` prefix for ``/me`` — so an agent's
scratchpad (``/workspace/c/{date}/{slug}``) and its pod filesystem
(``/me/c/{date}/{slug}``) line up under the same short, human-readable path
instead of a raw conversation UUID. No extra persisted field is needed: the cwd
already lives on the conversation.
"""

from __future__ import annotations

import secrets
import string
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sandbox_runtime.paths import WORKSPACE_ROOT
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.project_repo import (
    ProjectRepo as ProjectRepo,
    parse_project_repo as parse_project_repo,
)

_SLUG_ALPHABET = string.ascii_lowercase + string.digits
_SLUG_LENGTH = 8
_WORKSPACE_ROOT = WORKSPACE_ROOT
_POD_ROOT = "/me"


@dataclass(slots=True)
class WorkspaceLocation:
    workspace_id: str
    cwd: str
    repo: ProjectRepo | None = None


def generate_cwd_slug() -> str:
    """A short random alphanumeric slug for conversation cwd paths."""
    return "".join(secrets.choice(_SLUG_ALPHABET) for _ in range(_SLUG_LENGTH))


def default_cwd_for(*, conversation_id: UUID, created_at: datetime) -> str:
    """A deterministic fallback for legacy rows missing a persisted cwd."""
    date = created_at.date().isoformat()
    return f"{_WORKSPACE_ROOT}/c/{date}/{conversation_id.hex[:_SLUG_LENGTH]}"


def default_workspace_cwd(conversation: Conversation) -> str:
    """A deterministic fallback for legacy rows missing a persisted cwd."""
    return default_cwd_for(
        conversation_id=conversation.id, created_at=conversation.created_at
    )


def new_workspace_cwd(conversation: Conversation) -> str:
    """Generate the human-friendly cwd stamped onto a new root conversation."""
    date = conversation.created_at.date().isoformat()
    return f"{_WORKSPACE_ROOT}/c/{date}/{generate_cwd_slug()}"


def resolve_workspace_location(conversation: Conversation) -> WorkspaceLocation:
    """Where ``conversation`` works.

    An explicit cwd still wins over the repo's derived one. Creation normally
    stamps the two to agree (`_apply_inherited_cwd`), so a disagreement here
    means the server deliberately overrode the directory -- honour it, and let
    the checkout land where it was asked rather than somewhere it wasn't.
    """
    repo = conversation.repo
    return WorkspaceLocation(
        workspace_id=conversation.workspace_id
        or conversation.workspace_name
        or "default",
        cwd=conversation.cwd
        or (repo.cwd if repo else None)
        or default_workspace_cwd(conversation),
        repo=repo,
    )


async def apply_location(
    conversation: Conversation,
    *,
    fetch_parent: Callable[[], Awaitable[Conversation | None]],
) -> None:
    """Write the conversation's workspace location onto it, once.

    A child (a sub-agent, or a conversation pinned under a PROJECT) inherits the
    parent's resolved cwd, project, and workspace selection, so it works
    alongside the parent instead of in a directory of its own. A root
    conversation gets its own ``/workspace/c/{date}/{slug}``. A cwd its opener
    set always wins over both.

    A conversation opened against a ``repo`` gets that repository's directory:
    picking a project *is* picking a directory, so there is only ever one thing
    to keep in step.

    The cwd is the single source of truth for both filesystems — the pod working
    directory (``/me/{suffix}``) is derived from it at read time — so nothing
    pod-specific is persisted here.
    """
    if conversation.cwd:
        return
    if conversation.repo is not None:
        conversation.cwd = conversation.repo.cwd
        return

    parent = await fetch_parent()
    if parent is None:
        conversation.cwd = new_workspace_cwd(conversation)
        return

    parent_location = resolve_workspace_location(parent)
    conversation.cwd = parent_location.cwd
    if conversation.repo is None:
        conversation.repo = parent_location.repo
    conversation.workspace_id = conversation.workspace_id or parent.workspace_id
    conversation.workspace_name = conversation.workspace_name or parent.workspace_name


def has_recorded_cwd(conversation: Conversation) -> bool:
    """Whether this conversation's own row names its directory."""
    return bool(conversation.cwd)


async def ensure_recorded_location(
    conversation: Conversation,
    *,
    record: Callable[[UUID, str], Awaitable[None]],
) -> WorkspaceLocation:
    """Resolve the location, writing the cwd down if nothing had written it.

    The row is meant to be the single source of truth for where a conversation
    works, and creation stamps it (`apply_location`). A row that predates that,
    or that was created by some path which did not stamp, falls back to
    `default_workspace_cwd` -- deterministic, so stable, but recomputed forever
    and recorded nowhere. That is a source of truth in name only: the moment the
    fallback's formula changes, every such conversation moves house, and the
    files from its previous turns do not move with it.

    So the first run that resolves one writes the answer down, as the ``cwd``
    column alone, so nothing else a concurrent writer is setting on the row is
    overwritten. After that this is a pure read.
    """
    location = resolve_workspace_location(conversation)
    if has_recorded_cwd(conversation):
        return location
    conversation.cwd = location.cwd
    await record(conversation.id, location.cwd)
    return location


def pod_cwd_from_workspace_cwd(workspace_cwd: str) -> str:
    """Mirror a workspace cwd into the pod filesystem under ``/me``.

    ``<root>/c/{date}/{slug}`` -> ``/me/c/{date}/{slug}``.

    Both roots are stripped, not just the current one. Every conversation
    A cwd outside the workspace root is placed under ``/me`` as-is, which is
    defensive rather than expected.
    """
    if workspace_cwd == _WORKSPACE_ROOT:
        return _POD_ROOT
    if workspace_cwd.startswith(f"{_WORKSPACE_ROOT}/"):
        return f"{_POD_ROOT}/{workspace_cwd[len(_WORKSPACE_ROOT) + 1 :]}"
    return f"{_POD_ROOT}/{workspace_cwd.lstrip('/')}"


def resolve_pod_cwd(conversation: Conversation) -> str:
    """Default pod-filesystem cwd, sharing the workspace cwd's suffix."""
    return pod_cwd_from_workspace_cwd(resolve_workspace_location(conversation).cwd)
