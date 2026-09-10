"""The GitHub side of publishing, as Lemma's own connector operations.

Split from `github_publisher` when that file outgrew the size gate. The seam is
the one that was already there: `GithubPublisher` decides *what* a publish
does, and this decides how each step reaches GitHub.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.domain.errors import DomainError
from app.modules.connectors.domain.errors import (
    OperationExecutionNotFoundError,
    OperationNotFoundError,
)
from app.modules.pod_bundle.domain.errors import (
    BundleInvalidError,
    GithubBranchRaceError,
    GithubPublishCapabilityUnavailableError,
)
from app.modules.pod_bundle.infrastructure.github_publisher import RepoCreateResult

_BLOB_CONCURRENCY = 8


class NativeGithubOps:
    """Production :class:`GithubOps` over Lemma's own GitHub connector.

    Names and payload shapes are the connector's curated operations, which
    mirror GitHub's REST API directly: path and query parameters are top-level,
    and a request body goes under ``body``.
    """

    _OP_GET_USER = "users_get_authenticated"
    _OP_GET_REPO = "repos_get"
    _OP_GET_CONTENT = "repos_get_content"
    _OP_GET_REF = "git_get_ref"
    _OP_GET_COMMIT = "repos_get_commit"
    _OP_CREATE_BLOB = "git_create_blob"
    _OP_CREATE_TREE = "git_create_tree"
    _OP_CREATE_COMMIT = "git_create_commit"
    _OP_UPDATE_REF = "git_update_ref"
    _OP_CREATE_REF = "git_create_ref"

    def __init__(self, operation_runner: Callable[[str, dict], Awaitable[dict]]):
        self._run = operation_runner

    async def _capability(self, operation_name: str, payload: dict) -> dict:
        try:
            return _unwrap_operation(await self._run(operation_name, payload))
        except OperationNotFoundError as exc:
            raise GithubPublishCapabilityUnavailableError(operation_name) from exc

    async def resolve_repo(
        self, *, name: str, owner: str | None = None
    ) -> RepoCreateResult | None:
        """Find the repository, under the owner named or the person connected.

        An explicit owner is what makes publishing to an organisation possible:
        this used to resolve the authenticated user's login and nothing else, so
        every publish targeted a personal namespace whether or not that was
        where the pod belonged.
        """
        if not owner:
            user_result = await self._capability(self._OP_GET_USER, {})
            owner = str(user_result.get("login") or "")
        if not owner:
            return None
        try:
            result = await self._capability(
                self._OP_GET_REPO,
                {"owner": owner, "repo": name},
            )
        except OperationExecutionNotFoundError:
            return None
        if not result or result.get("id") is None:
            return None
        return _repo_result(result, fallback_owner=owner, fallback_repo=name)

    async def get_head(self, *, owner: str, repo: str, branch: str) -> str | None:
        """The branch head, or None when the repository has no commits yet.

        An empty repository is the normal starting point now that Lemma does
        not create one: "New repository" without "Add a README" leaves a
        repository with no branch and no ref to resolve. Answering None rather
        than raising is what lets the first publish be the initial commit.
        """
        try:
            result = await self._capability(
                self._OP_GET_REF,
                {"owner": owner, "repo": repo, "ref": f"heads/{branch}"},
            )
        except OperationExecutionNotFoundError:
            return None
        obj = result.get("object")
        sha = obj.get("sha") if isinstance(obj, dict) else None
        if not isinstance(sha, str) or not sha:
            return None
        return sha

    async def get_file(
        self,
        *,
        owner: str,
        repo: str,
        path: str,
        ref: str | None = None,
    ) -> bytes | None:
        payload: dict[str, Any] = {"owner": owner, "repo": repo, "path": path}
        if ref:
            payload["ref"] = ref
        try:
            result = await self._capability(self._OP_GET_CONTENT, payload)
        except OperationExecutionNotFoundError:
            return None
        # GitHub answers with the file object itself; a directory answers with a
        # list, which `_unwrap_operation` flattens to an empty mapping.
        encoded = result.get("content")
        if not isinstance(encoded, str):
            return None
        try:
            return base64.b64decode(encoded.replace("\n", ""), validate=True)
        except (ValueError, TypeError) as exc:
            raise BundleInvalidError(
                f"GitHub returned invalid Base64 content for '{path}'."
            ) from exc

    async def commit_files(
        self,
        *,
        owner: str,
        repo: str,
        branch: str,
        upserts: dict[str, bytes],
        deletes: set[str],
        message: str,
        expected_head: str | None,
    ) -> str:
        """Write every file as one commit built out of GitHub's git-data API.

        Blobs, a tree and a commit are staged without touching the branch, and
        only the final ref update makes them visible -- so a publish that dies
        halfway leaves the repository exactly as it was.
        """
        current_head = await self.get_head(owner=owner, repo=repo, branch=branch)
        if current_head != expected_head:
            raise GithubBranchRaceError()

        base_tree = await self._base_tree(
            owner=owner, repo=repo, expected_head=expected_head
        )

        blobs = await self._create_blobs(
            owner=owner,
            repo=repo,
            upserts=upserts,
        )
        entries: list[dict[str, Any]] = [
            {"path": path, "mode": "100644", "type": "blob", "sha": sha}
            for path, sha in blobs.items()
        ]
        entries.extend(
            {
                "path": path,
                "mode": "100644",
                "type": "blob",
                "sha": None,
            }
            for path in sorted(deletes)
        )
        tree_result = await self._capability(
            self._OP_CREATE_TREE,
            {
                "owner": owner,
                "repo": repo,
                "body": {
                    **({"base_tree": base_tree} if base_tree else {}),
                    "tree": entries,
                },
            },
        )
        tree_sha = tree_result.get("sha")
        if not isinstance(tree_sha, str) or not tree_sha:
            raise BundleInvalidError("GitHub did not return the new tree SHA.")

        commit_result = await self._capability(
            self._OP_CREATE_COMMIT,
            {
                "owner": owner,
                "repo": repo,
                "body": {
                    "message": message,
                    "tree": tree_sha,
                    "parents": [expected_head] if expected_head else [],
                },
            },
        )
        commit_sha = commit_result.get("sha")
        if not isinstance(commit_sha, str) or not commit_sha:
            raise BundleInvalidError("GitHub did not return the new commit SHA.")

        await self._point_branch_at(
            owner=owner,
            repo=repo,
            branch=branch,
            commit_sha=commit_sha,
            existing_head=expected_head,
        )
        return commit_sha

    async def _base_tree(
        self, *, owner: str, repo: str, expected_head: str | None
    ) -> str | None:
        """The tree this commit builds on, or None for a first commit.

        No head means an empty repository, and the first commit has nothing to
        build on. The race check in the caller still holds: `expected_head` was
        also None when it looked, so somebody pushing meanwhile is still caught.
        """
        if expected_head is None:
            return None
        commit_data = await self._capability(
            self._OP_GET_COMMIT,
            {"owner": owner, "repo": repo, "ref": expected_head},
        )
        commit = commit_data.get("commit")
        tree = commit.get("tree") if isinstance(commit, dict) else None
        base_tree = tree.get("sha") if isinstance(tree, dict) else None
        if not isinstance(base_tree, str) or not base_tree:
            raise BundleInvalidError("GitHub did not return the base tree SHA.")
        return base_tree

    async def _point_branch_at(
        self,
        *,
        owner: str,
        repo: str,
        branch: str,
        commit_sha: str,
        existing_head: str | None,
    ) -> None:
        """Make the commit visible, creating the branch if it is not there yet.

        A branch that does not exist is created, not updated: `PATCH` on a
        missing ref is a 422, which is the whole reason an empty repository
        could not be published into.
        """
        operation, payload = (
            (
                self._OP_UPDATE_REF,
                {
                    "owner": owner,
                    "repo": repo,
                    "ref": f"heads/{branch}",
                    "body": {"sha": commit_sha, "force": False},
                },
            )
            if existing_head is not None
            else (
                self._OP_CREATE_REF,
                {
                    "owner": owner,
                    "repo": repo,
                    "body": {"ref": f"refs/heads/{branch}", "sha": commit_sha},
                },
            )
        )
        try:
            await self._capability(operation, payload)
        except DomainError as exc:
            # Creating a ref somebody else created meanwhile is the same race as
            # a rejected fast-forward, and reads as a conflict too.
            if _rejected_as_conflict(exc):
                raise GithubBranchRaceError() from exc
            raise

    async def _create_blobs(
        self,
        *,
        owner: str,
        repo: str,
        upserts: dict[str, bytes],
    ) -> dict[str, str]:
        semaphore = asyncio.Semaphore(_BLOB_CONCURRENCY)

        async def create_blob(path: str, content: bytes) -> tuple[str, str]:
            async with semaphore:
                result = await self._capability(
                    self._OP_CREATE_BLOB,
                    {
                        "owner": owner,
                        "repo": repo,
                        "body": {
                            "content": base64.b64encode(content).decode("ascii"),
                            "encoding": "base64",
                        },
                    },
                )
            sha = result.get("sha")
            if not isinstance(sha, str) or not sha:
                raise BundleInvalidError(
                    f"GitHub did not return a blob SHA for '{path}'."
                )
            return path, sha

        return dict(
            await asyncio.gather(
                *(create_blob(path, content) for path, content in upserts.items())
            )
        )


def _rejected_as_conflict(exc: DomainError) -> bool:
    """Did GitHub refuse this write because the branch moved under us?

    A non-fast-forward ref update is a 422 (409 on some paths). The connector
    layer maps those onto its own domain errors, so the provider's status can
    also arrive as `details["upstream_status"]` rather than on the error itself.
    """
    statuses = {getattr(exc, "status_code", None)}
    details = getattr(exc, "details", None)
    if isinstance(details, dict):
        statuses.add(details.get("upstream_status"))
    return bool(statuses & {409, 422})


def _unwrap_operation(result: object) -> dict[str, Any]:
    """Peel ``OperationExecutionResponse.result`` off GitHub's own response.

    The connector returns the provider's JSON verbatim under ``result``; a
    response that is not an object (a directory listing is an array) has nothing
    this module can read, so it reads as empty.
    """
    current = result
    if isinstance(current, dict) and "result" in current:
        current = current["result"]
    return current if isinstance(current, dict) else {}


def _repo_result(
    data: dict[str, Any],
    *,
    fallback_owner: str,
    fallback_repo: str,
) -> RepoCreateResult:
    full = str(data.get("full_name") or "")
    owner_data = data.get("owner")
    nested_owner = (
        str(owner_data.get("login") or "") if isinstance(owner_data, dict) else ""
    )
    owner = full.split("/")[0] if "/" in full else nested_owner or fallback_owner
    repo = full.split("/", 1)[1] if "/" in full else fallback_repo
    if not owner or not repo:
        raise BundleInvalidError("GitHub did not return the repository identity.")
    html_url = str(data.get("html_url") or f"https://github.com/{owner}/{repo}")
    default_branch = str(data.get("default_branch") or "main")
    return RepoCreateResult(
        owner=owner,
        repo=repo,
        html_url=html_url,
        default_branch=default_branch,
        private=(
            data["private"]
            if isinstance(data.get("private"), bool)
            else data.get("visibility") == "private"
            if data.get("visibility") in {"private", "public"}
            else None
        ),
    )
