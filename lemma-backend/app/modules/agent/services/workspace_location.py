"""Single source of truth for a conversation's workspace + working directory.

Both the agent run and the approval executor must run in the *same* workspace
session and cwd, so the resolution lives here instead of being duplicated. The
location is configurable per conversation via ``metadata``:

- ``cwd`` — explicit working directory; a fresh root conversation gets a default
  of ``/workspace/c/{date}/{slug}``. Stamped into metadata at creation
  (``ConversationService._apply_inherited_cwd``) and read back thereafter.
- ``workspace_name`` / ``workspace_id`` — selects the workspace; defaults to the
  single per-user workspace today. Kept metadata-driven so multi-workspace
  switching becomes a metadata-only change later.

The pod filesystem's working directory (``/me/{suffix}``) is derived from the
workspace cwd by swapping the ``/workspace`` prefix for ``/me`` — so an agent's
scratchpad (``/workspace/c/{date}/{slug}``) and its pod filesystem
(``/me/c/{date}/{slug}``) line up under the same short, human-readable path
instead of a raw conversation UUID. No extra persisted field is needed: the cwd
already lives in conversation metadata.
"""

from __future__ import annotations

import secrets
import string
from dataclasses import dataclass

from app.modules.agent.domain.entities import Conversation

_SLUG_ALPHABET = string.ascii_lowercase + string.digits
_SLUG_LENGTH = 8
_WORKSPACE_ROOT = "/workspace"
_POD_ROOT = "/me"


@dataclass(slots=True)
class WorkspaceLocation:
    workspace_id: str
    cwd: str


def generate_cwd_slug() -> str:
    """A short random alphanumeric slug for conversation cwd paths."""
    return "".join(secrets.choice(_SLUG_ALPHABET) for _ in range(_SLUG_LENGTH))


def default_workspace_cwd(conversation: Conversation) -> str:
    """The default ``/workspace/c/{date}/{slug}`` cwd for a root conversation.

    Generates a fresh slug; callers persist the result into conversation
    metadata (at creation) so it stays stable across later runs.
    """
    date = conversation.created_at.date().isoformat()
    return f"{_WORKSPACE_ROOT}/c/{date}/{generate_cwd_slug()}"


def resolve_workspace_location(conversation: Conversation) -> WorkspaceLocation:
    metadata = conversation.metadata if isinstance(conversation.metadata, dict) else {}
    workspace = metadata.get("workspace")
    workspace = workspace if isinstance(workspace, dict) else {}
    workspace_id = str(
        workspace.get("id")
        or metadata.get("workspace_id")
        or metadata.get("workspace_name")
        or "default"
    )
    cwd = str(
        workspace.get("cwd")
        or metadata.get("cwd")
        or default_workspace_cwd(conversation)
    )
    return WorkspaceLocation(workspace_id=workspace_id, cwd=cwd)


def pod_cwd_from_workspace_cwd(workspace_cwd: str) -> str:
    """Mirror a workspace cwd into the pod filesystem under ``/me``.

    ``/workspace/c/{date}/{slug}`` -> ``/me/c/{date}/{slug}``. A cwd not under
    ``/workspace`` is placed under ``/me`` as-is (defensive; overrides today are
    always under ``/workspace``).
    """
    if workspace_cwd == _WORKSPACE_ROOT:
        return _POD_ROOT
    if workspace_cwd.startswith(f"{_WORKSPACE_ROOT}/"):
        return f"{_POD_ROOT}/{workspace_cwd[len(_WORKSPACE_ROOT) + 1:]}"
    return f"{_POD_ROOT}/{workspace_cwd.lstrip('/')}"


def resolve_pod_cwd(conversation: Conversation) -> str:
    """Default pod-filesystem cwd, sharing the workspace cwd's suffix."""
    return pod_cwd_from_workspace_cwd(resolve_workspace_location(conversation).cwd)
