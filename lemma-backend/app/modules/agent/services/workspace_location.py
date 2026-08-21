"""Single source of truth for a conversation's workspace + working directory.

Both the agent run and the approval executor must run in the *same* workspace
session and cwd, so the resolution lives here instead of being duplicated. The
location is configurable per conversation via ``metadata``:

- ``cwd`` — explicit working directory; a fresh root conversation gets a default
  of ``/workspace/c/{date}/{slug}``. Stamped into metadata at creation
  (``ConversationService._apply_inherited_cwd``) and read back thereafter.
- ``repo`` — a GitHub repository this conversation works in, as
  ``{owner, repo, ref?, account_id?}``. It *derives* the cwd
  (``/workspace/repos/{owner}/{repo}``), so picking a project is the same act as
  picking a directory and there is no second source of truth to keep in step.
- ``workspace_name`` / ``workspace_id`` — selects the workspace; defaults to the
  single per-user workspace today. Kept metadata-driven so multi-workspace
  switching becomes a metadata-only change later.

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
already lives in conversation metadata.
"""

from __future__ import annotations

import re
import secrets
import string
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

from app.modules.agent.domain.entities import Conversation

_SLUG_ALPHABET = string.ascii_lowercase + string.digits
_SLUG_LENGTH = 8
_WORKSPACE_ROOT = "/workspace"
_POD_ROOT = "/me"
_REPOS_ROOT = f"{_WORKSPACE_ROOT}/repos"

# An owner/repo pair becomes both a filesystem path and part of a clone URL, so
# these are validated rather than trusted. The patterns are GitHub's own naming
# rules, which have no room for a path separator, a `..`, or a leading `-` that
# git would read as an option.
_OWNER_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_REPO_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}$")
# A ref is passed to `git clone --branch`, so it must not begin with `-`.
_REF_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]{0,254}$")


@dataclass(frozen=True, slots=True)
class ProjectRepo:
    """A GitHub repository a conversation works in."""

    owner: str
    repo: str
    ref: str | None = None
    # Which connected GitHub account to clone as. Optional, and when absent the
    # credential bridge falls back to resolving the user's account -- which is
    # ambiguous for a user with two connected accounts, so callers that know the
    # account should say so.
    account_id: UUID | None = None

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def cwd(self) -> str:
        return f"{_REPOS_ROOT}/{self.owner}/{self.repo}"

    def as_metadata(self) -> dict[str, str]:
        value = {"owner": self.owner, "repo": self.repo}
        if self.ref:
            value["ref"] = self.ref
        if self.account_id:
            value["account_id"] = str(self.account_id)
        return value


def parse_project_repo(value: object) -> ProjectRepo | None:
    """Read a repo out of untrusted metadata, or ``None`` if it isn't one.

    Invalid input is dropped rather than raised on: metadata is a free-form JSON
    object a client can put anything in, and a malformed repo should leave the
    conversation on a normal scratchpad cwd rather than fail the whole run.
    """
    if not isinstance(value, dict):
        return None
    owner = str(value.get("owner") or "").strip()
    repo = str(value.get("repo") or "").strip()
    # `.git` is how a clone URL ends and how people paste repo names; it is
    # never part of the directory a clone produces.
    repo = repo.removesuffix(".git")
    if not _OWNER_PATTERN.match(owner) or not _REPO_PATTERN.match(repo):
        return None
    if repo in {".", ".."}:
        return None
    raw_ref = str(value.get("ref") or "").strip()
    ref = raw_ref if _REF_PATTERN.match(raw_ref) else None
    raw_account = value.get("account_id")
    try:
        account_id = UUID(str(raw_account)) if raw_account else None
    except TypeError, ValueError:
        account_id = None
    return ProjectRepo(owner=owner, repo=repo, ref=ref, account_id=account_id)


@dataclass(slots=True)
class WorkspaceLocation:
    workspace_id: str
    cwd: str
    repo: ProjectRepo | None = None


def generate_cwd_slug() -> str:
    """A short random alphanumeric slug for conversation cwd paths."""
    return "".join(secrets.choice(_SLUG_ALPHABET) for _ in range(_SLUG_LENGTH))


def default_workspace_cwd(conversation: Conversation) -> str:
    """A deterministic fallback for legacy rows missing persisted cwd metadata."""
    date = conversation.created_at.date().isoformat()
    return f"{_WORKSPACE_ROOT}/c/{date}/{conversation.id.hex[:_SLUG_LENGTH]}"


def new_workspace_cwd(conversation: Conversation) -> str:
    """Generate the human-friendly cwd stamped onto a new root conversation."""
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
    repo = parse_project_repo(workspace.get("repo") or metadata.get("repo"))
    # An explicit cwd still wins over the repo's derived one. Creation normally
    # stamps the two to agree (`_apply_inherited_cwd`), so a disagreement here
    # means a caller deliberately overrode the directory -- honour it, and let
    # the checkout land where they asked rather than somewhere they didn't.
    cwd = str(
        workspace.get("cwd")
        or metadata.get("cwd")
        or (repo.cwd if repo else None)
        or default_workspace_cwd(conversation)
    )
    return WorkspaceLocation(workspace_id=workspace_id, cwd=cwd, repo=repo)


async def apply_location_metadata(
    conversation: Conversation,
    *,
    fetch_parent: Callable[[], Awaitable[Conversation | None]],
) -> None:
    """Write the conversation's workspace location into its metadata, once.

    A child (a sub-agent, or a conversation pinned under a PROJECT) inherits the
    parent's resolved cwd, project, and workspace selection, so it works
    alongside the parent instead of in a directory of its own. A root
    conversation gets its own ``/workspace/c/{date}/{slug}``. An explicit ``cwd``
    always wins over both.

    A conversation created against a ``repo`` gets that repository's directory:
    picking a project *is* picking a directory, so there is only ever one thing
    to keep in step. The repo is rewritten from its parsed form rather than
    stored as the client sent it, so what is persisted is exactly what
    ``resolve_workspace_location`` will read back.

    The cwd is the single source of truth for both filesystems — the pod working
    directory (``/me/{suffix}``) is derived from it at read time — so nothing
    pod-specific is persisted here.
    """
    metadata = conversation.metadata if isinstance(conversation.metadata, dict) else {}
    conversation.metadata = metadata

    repo = parse_project_repo(metadata.get("repo"))
    if repo is not None:
        _write_repo(metadata, repo)
    else:
        # An unparseable repo would resolve to nothing anyway; dropping it keeps
        # metadata honest about what is actually in effect.
        metadata.pop("repo", None)
        metadata.pop("repo_full_name", None)

    if metadata.get("cwd"):
        return
    if repo is not None:
        metadata["cwd"] = repo.cwd
        return

    parent = await fetch_parent()
    if parent is None:
        metadata["cwd"] = new_workspace_cwd(conversation)
        return

    parent_meta = parent.metadata if isinstance(parent.metadata, dict) else {}
    parent_location = resolve_workspace_location(parent)
    metadata["cwd"] = parent_location.cwd
    if parent_location.repo is not None:
        _write_repo(metadata, parent_location.repo, overwrite=False)
    for key in ("workspace", "workspace_id", "workspace_name"):
        if key in parent_meta:
            metadata.setdefault(key, parent_meta[key])


def _write_repo(metadata: dict, repo: ProjectRepo, *, overwrite: bool = True) -> None:
    write = metadata.__setitem__ if overwrite else metadata.setdefault
    write("repo", repo.as_metadata())
    # Flat and denormalized on purpose: conversation listing filters metadata by
    # JSONB containment over string values only, so
    # `?metadata.repo_full_name=owner/name` is the query that finds every
    # conversation on a project. A nested key cannot be filtered at all.
    write("repo_full_name", repo.full_name)


def has_recorded_cwd(conversation: Conversation) -> bool:
    """Whether this conversation's own metadata names its directory."""
    metadata = conversation.metadata if isinstance(conversation.metadata, dict) else {}
    workspace = metadata.get("workspace")
    workspace = workspace if isinstance(workspace, dict) else {}
    return bool(workspace.get("cwd") or metadata.get("cwd"))


async def ensure_recorded_location(
    conversation: Conversation,
    *,
    record: Callable[[UUID, str, str], Awaitable[None]],
) -> WorkspaceLocation:
    """Resolve the location, writing the cwd down if nothing had written it.

    Metadata is meant to be the single source of truth for where a conversation
    works, and creation stamps it (`apply_location_metadata`). A row that
    predates that, or that was created by some path which did not stamp, falls
    back to `default_workspace_cwd` -- deterministic, so stable, but recomputed
    forever and recorded nowhere. That is a source of truth in name only: the
    moment the fallback's formula changes, every such conversation moves house,
    and the files from its previous turns do not move with it.

    So the first run that resolves one writes the answer down, through
    `set_conversation_metadata_key` rather than a whole-metadata update, because
    sibling keys (`is_sub_agent`, `surface_platform`) are written concurrently by
    other paths. After that this is a pure read.
    """
    location = resolve_workspace_location(conversation)
    if has_recorded_cwd(conversation):
        return location
    metadata = conversation.metadata if isinstance(conversation.metadata, dict) else {}
    metadata["cwd"] = location.cwd
    conversation.metadata = metadata
    await record(conversation.id, "cwd", location.cwd)
    return location


def pod_cwd_from_workspace_cwd(workspace_cwd: str) -> str:
    """Mirror a workspace cwd into the pod filesystem under ``/me``.

    ``/workspace/c/{date}/{slug}`` -> ``/me/c/{date}/{slug}``. A cwd not under
    ``/workspace`` is placed under ``/me`` as-is (defensive; overrides today are
    always under ``/workspace``).
    """
    if workspace_cwd == _WORKSPACE_ROOT:
        return _POD_ROOT
    if workspace_cwd.startswith(f"{_WORKSPACE_ROOT}/"):
        return f"{_POD_ROOT}/{workspace_cwd[len(_WORKSPACE_ROOT) + 1 :]}"
    return f"{_POD_ROOT}/{workspace_cwd.lstrip('/')}"


def resolve_pod_cwd(conversation: Conversation) -> str:
    """Default pod-filesystem cwd, sharing the workspace cwd's suffix."""
    return pod_cwd_from_workspace_cwd(resolve_workspace_location(conversation).cwd)
