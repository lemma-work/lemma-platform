"""A GitHub repository a conversation works in.

A leaf of its own because the conversation entity carries one as a typed
field, and ``workspace_location`` -- which decides what the repository means
for the conversation's directory -- reads conversations.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from sandbox_runtime.paths import WORKSPACE_ROOT

_REPOS_ROOT = f"{WORKSPACE_ROOT}/repos"

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
    def cwd(self) -> str:
        return f"{_REPOS_ROOT}/{self.owner}/{self.repo}"

    def to_json(self) -> dict[str, str]:
        value = {"owner": self.owner, "repo": self.repo}
        if self.ref:
            value["ref"] = self.ref
        return value


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
