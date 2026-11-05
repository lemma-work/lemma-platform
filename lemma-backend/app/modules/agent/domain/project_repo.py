"""A GitHub repository a conversation works in, and where it lives on disk.

A leaf of its own because the conversation entity carries one as a typed
field, and ``workspace_location`` -- which decides what the repository means
for the conversation's directory -- reads conversations.

Two places per repository. One clone, ``clone_dir``, shared by every
conversation of the person in it: the objects and ``origin/*`` are fetched
once for all of them. And a worktree per conversation, ``worktree_dir``, on a
branch of its own (``worktree_branch``), so no conversation's working tree or
uncommitted changes are another's.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from sandbox_runtime.paths import WORKSPACE_ROOT

_REPOS_ROOT = f"{WORKSPACE_ROOT}/repos"
WORKTREES_ROOT = f"{WORKSPACE_ROOT}/worktrees"
# A worktree's slug names its directory and, after `lemma/`, its branch, so it
# is held to what both can take without quoting.
_WORKTREE_SLUG_PATTERN = re.compile(r"^[a-z0-9]{1,64}$")

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
    # No account here: the credential bridge clones as whatever the pod's
    # `github` connector resolves to for the person acting, never as an account
    # a conversation names.

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def clone_dir(self) -> str:
        """The one clone of this repository, which every worktree of it shares."""
        return f"{_REPOS_ROOT}/{self.owner}/{self.repo}"

    def worktree_dir(self, slug: str) -> str:
        """One conversation's worktree of this repository."""
        return f"{WORKTREES_ROOT}/{self.owner}/{self.repo}/{slug}"

    def worktree_slug(self, cwd: str) -> str | None:
        """The slug of this repository's worktree at ``cwd``, if it is one.

        ``None`` for anything else -- a conversation from before worktrees,
        which works in ``clone_dir`` itself, or a directory set by hand.
        """
        prefix = f"{WORKTREES_ROOT}/{self.owner}/{self.repo}/"
        if not cwd.startswith(prefix):
            return None
        slug = cwd[len(prefix) :].rstrip("/")
        return slug if _WORKTREE_SLUG_PATTERN.match(slug) else None

    def to_json(self) -> dict[str, str]:
        value = {"owner": self.owner, "repo": self.repo}
        if self.ref:
            value["ref"] = self.ref
        return value


@dataclass(frozen=True, slots=True)
class RepositoryChoice:
    """A repository a person could open a conversation in, as a picker lists it."""

    repo: ProjectRepo
    private: bool = False
    default_branch: str | None = None


@dataclass(frozen=True, slots=True)
class RepositoryChoices:
    """What a picker offers: the repositories, and whether that is all of them.

    A large installation is listed in part, and opening one that was not shown
    is still accepted.
    """

    choices: tuple[RepositoryChoice, ...]
    complete: bool = True


def worktree_branch(slug: str) -> str:
    """The branch a conversation's worktree starts on."""
    return f"lemma/{slug}"


def worktree_at(path: str) -> tuple[ProjectRepo, str] | None:
    """The repository and slug of the worktree at ``path``, or ``None``.

    The way back from a recorded cwd to the clone it was made from. Held to
    ``worktree_dir`` exactly -- a path it could not have produced is not a
    worktree -- so a hand-set cwd under the root is never taken for one.
    """
    if not path.startswith(f"{WORKTREES_ROOT}/"):
        return None
    owner, _, rest = path[len(WORKTREES_ROOT) + 1 :].rstrip("/").partition("/")
    name, _, slug = rest.partition("/")
    repo = parse_project_repo({"owner": owner, "repo": name})
    if repo is None or not _WORKTREE_SLUG_PATTERN.match(slug):
        return None
    if repo.worktree_dir(slug) != path.rstrip("/"):
        return None
    return repo, slug


def parse_project_repo(value: object) -> ProjectRepo | None:
    """Read a repo out of untrusted JSON, or ``None`` if it isn't one.

    Invalid input is dropped rather than raised on: a webhook's repository
    context, or a row an older release wrote, can hold anything, and a
    malformed repo should leave the conversation on a normal scratchpad cwd
    rather than fail the whole run.
    """
    if not isinstance(value, Mapping):
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
    return ProjectRepo(owner=owner, repo=repo, ref=ref)


def parse_repository_name(value: str) -> ProjectRepo | None:
    """``owner/name`` as a person picks it, or ``None`` if it isn't one.

    Held to the same rules as ``parse_project_repo``: a third segment, a
    ``..`` or an option-shaped name is not a repository, whoever typed it.
    """
    owner, separator, repo = value.strip().partition("/")
    if not separator:
        return None
    return parse_project_repo({"owner": owner, "repo": repo})
