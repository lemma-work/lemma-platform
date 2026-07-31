"""Atomic GitHub publishing through the Composio connector."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from app.core.domain.errors import DomainError
from app.modules.connectors.domain.errors import (
    OperationExecutionNotFoundError,
    OperationNotFoundError,
)
from app.modules.pod_bundle.domain.errors import (
    BundleInvalidError,
    GithubBranchRaceError,
    GithubPublishCapabilityUnavailableError,
    GithubRepositoryExistsError,
    GithubRepositoryNotFoundError,
)
from app.modules.pod_bundle.domain.state import PublishMode
from app.modules.pod_bundle.infrastructure.publish_manifest import (
    PUBLISH_MANIFEST_PATH,
    build_publish_layout,
    manifest_managed_paths,
    manifest_publish_id,
    parse_publish_manifest,
)

# Keep the JSON/base64 request well below typical connector request ceilings.
_MULTI_FILE_MAX_REQUEST_BYTES = 450_000
_BLOB_CONCURRENCY = 8


class RepoCreateResult:
    def __init__(
        self,
        *,
        owner: str,
        repo: str,
        html_url: str,
        default_branch: str = "main",
        private: bool | None = None,
    ):
        self.owner = owner
        self.repo = repo
        self.html_url = html_url
        self.default_branch = default_branch or "main"
        self.private = private


class GithubOps(Protocol):
    async def resolve_repo(self, *, name: str) -> RepoCreateResult | None: ...

    async def create_repo(
        self, *, name: str, private: bool, description: str | None
    ) -> RepoCreateResult: ...

    async def get_head(
        self, *, owner: str, repo: str, branch: str
    ) -> str: ...

    async def get_file(
        self,
        *,
        owner: str,
        repo: str,
        path: str,
        ref: str | None = None,
    ) -> bytes | None: ...

    async def commit_files(
        self,
        *,
        owner: str,
        repo: str,
        branch: str,
        upserts: dict[str, bytes],
        deletes: set[str],
        message: str,
        expected_head: str,
        prefer_multi_file: bool,
    ) -> str: ...


ProgressCallback = Callable[[str, int, int], Awaitable[None]]


class GithubPublisher:
    def __init__(self, ops: GithubOps):
        self._ops = ops

    async def create_repo(
        self,
        *,
        repo_name: str,
        private: bool,
        description: str | None,
        mode: PublishMode = PublishMode.CREATE,
    ) -> RepoCreateResult:
        """Resolve the requested create/update policy before writing anything."""
        existing = await self._ops.resolve_repo(name=repo_name)
        if mode is PublishMode.UPDATE:
            if existing is None:
                raise GithubRepositoryNotFoundError(repo_name)
            return existing
        if existing is not None:
            raise GithubRepositoryExistsError(repo_name)

        try:
            return await self._ops.create_repo(
                name=repo_name,
                private=private,
                description=description,
            )
        except DomainError as exc:
            status_code = getattr(exc, "status_code", None)
            retryable = status_code in {408, 429} or (
                isinstance(status_code, int) and status_code >= 500
            )
            if not retryable:
                raise
            # Connector timeouts/provider failures are DomainErrors too. The
            # create may nevertheless have reached GitHub, so resolve before a
            # worker retry can incorrectly turn our repository into a CREATE
            # conflict.
            try:
                resolved = await self._ops.resolve_repo(name=repo_name)
            except Exception:
                raise exc
            if resolved is not None:
                return resolved
            raise
        except Exception:
            # A timeout after GitHub accepted the create is ambiguous. Resolve it
            # once; this is safe because we established non-existence above.
            resolved = await self._ops.resolve_repo(name=repo_name)
            if resolved is not None:
                return resolved
            raise

    async def publish(
        self,
        *,
        publish_id: str,
        mode: PublishMode,
        repo_name: str,
        private: bool,
        description: str | None,
        files: dict[str, bytes],
        readme: str,
        on_progress: ProgressCallback | None = None,
        already_created: RepoCreateResult | None = None,
        completed_paths: set[str] | None = None,
    ) -> RepoCreateResult:
        del completed_paths  # Atomic commits checkpoint as one unit.
        repo = already_created or await self.create_repo(
            repo_name=repo_name,
            private=private,
            description=description,
            mode=mode,
        )

        head = await self._ops.get_head(
            owner=repo.owner,
            repo=repo.repo,
            branch=repo.default_branch,
        )
        old_manifest_raw = await self._ops.get_file(
            owner=repo.owner,
            repo=repo.repo,
            path=PUBLISH_MANIFEST_PATH,
            ref=head,
        )
        old_manifest = (
            parse_publish_manifest(old_manifest_raw)
            if old_manifest_raw is not None
            else None
        )
        # Response-lost retry: the repository itself is the durable checkpoint.
        if manifest_publish_id(old_manifest) == publish_id:
            await self._emit_completed_progress(
                ["README.md", *files],
                on_progress=on_progress,
            )
            return repo

        logical_files = {"README.md": readme.encode("utf-8"), **files}
        layout_options: dict[str, int] = {}
        if mode is PublishMode.UPDATE and old_manifest is None:
            # A legacy repository may already contain a large logical path.
            # Its first manifest-backed update must overwrite that path without
            # deleting anything it cannot prove Lemma owned. Keep logical files
            # unchunked for this one transition; later manifests make stale
            # managed-path cleanup safe.
            layout_options["chunk_threshold_bytes"] = max(
                (len(content) for content in logical_files.values()),
                default=0,
            )
        physical_files, new_manifest = build_publish_layout(
            logical_files,
            publish_id=publish_id,
            **layout_options,
        )
        old_managed = manifest_managed_paths(old_manifest) if old_manifest else set()
        new_managed = manifest_managed_paths(new_manifest)
        stale_paths = old_managed - new_managed

        request_size = sum(
            len(path) + len(base64.b64encode(content))
            for path, content in physical_files.items()
        )
        prefer_multi_file = (
            mode is PublishMode.CREATE
            and not stale_paths
            and request_size <= _MULTI_FILE_MAX_REQUEST_BYTES
        )
        await self._ops.commit_files(
            owner=repo.owner,
            repo=repo.repo,
            branch=repo.default_branch,
            upserts=physical_files,
            deletes=stale_paths,
            message=f"Publish Lemma pod ({publish_id})",
            expected_head=head,
            prefer_multi_file=prefer_multi_file,
        )
        await self._emit_completed_progress(
            list(logical_files),
            on_progress=on_progress,
        )
        return repo

    @staticmethod
    async def _emit_completed_progress(
        paths: list[str],
        *,
        on_progress: ProgressCallback | None,
    ) -> None:
        if on_progress is None:
            return
        total = len(paths)
        for done, path in enumerate(paths, start=1):
            await on_progress(path, done, total)


class ComposioGithubOps:
    """Production :class:`GithubOps` over current Composio GitHub operations."""

    _OP_CREATE_REPO = "GITHUB_CREATE_A_REPOSITORY_FOR_THE_AUTHENTICATED_USER"
    _OP_GET_USER = "GITHUB_GET_THE_AUTHENTICATED_USER"
    _OP_GET_REPO = "GITHUB_GET_A_REPOSITORY"
    _OP_GET_CONTENT = "GITHUB_GET_REPOSITORY_CONTENT"
    _OP_GET_REF = "GITHUB_GET_A_REFERENCE"
    _OP_GET_COMMIT = "GITHUB_GET_A_COMMIT"
    _OP_COMMIT_MULTIPLE = "GITHUB_COMMIT_MULTIPLE_FILES"
    _OP_CREATE_BLOB = "GITHUB_CREATE_A_BLOB"
    _OP_CREATE_TREE = "GITHUB_CREATE_A_TREE"
    _OP_CREATE_COMMIT = "GITHUB_CREATE_A_COMMIT"
    _OP_UPDATE_REF = "GITHUB_UPDATE_A_REFERENCE"

    def __init__(self, operation_runner: Callable[[str, dict], Awaitable[dict]]):
        self._run = operation_runner

    async def _capability(self, operation_name: str, payload: dict) -> dict:
        try:
            return _unwrap_operation(await self._run(operation_name, payload))
        except OperationNotFoundError as exc:
            raise GithubPublishCapabilityUnavailableError(operation_name) from exc

    async def resolve_repo(self, *, name: str) -> RepoCreateResult | None:
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

    async def create_repo(
        self, *, name: str, private: bool, description: str | None
    ) -> RepoCreateResult:
        result = await self._capability(
            self._OP_CREATE_REPO,
            {
                "name": name,
                "private": private,
                "description": description or "",
                # Seed the default branch. All Lemma-managed files still land in
                # the following single atomic commit.
                "auto_init": True,
            },
        )
        return _repo_result(result, fallback_owner="", fallback_repo=name)

    async def get_head(self, *, owner: str, repo: str, branch: str) -> str:
        result = await self._capability(
            self._OP_GET_REF,
            {"owner": owner, "repo": repo, "ref": f"heads/{branch}"},
        )
        obj = result.get("object")
        sha = obj.get("sha") if isinstance(obj, dict) else None
        if not isinstance(sha, str) or not sha:
            raise BundleInvalidError("GitHub did not return the branch head.")
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
        item = result.get("content")
        if not isinstance(item, dict):
            return None
        encoded = item.get("content")
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
        expected_head: str,
        prefer_multi_file: bool,
    ) -> str:
        if prefer_multi_file:
            return await self._commit_multiple(
                owner=owner,
                repo=repo,
                branch=branch,
                upserts=upserts,
                deletes=deletes,
                message=message,
            )
        return await self._commit_git_data(
            owner=owner,
            repo=repo,
            branch=branch,
            upserts=upserts,
            deletes=deletes,
            message=message,
            expected_head=expected_head,
        )

    async def _commit_multiple(
        self,
        *,
        owner: str,
        repo: str,
        branch: str,
        upserts: dict[str, bytes],
        deletes: set[str],
        message: str,
    ) -> str:
        result = await self._capability(
            self._OP_COMMIT_MULTIPLE,
            {
                "owner": owner,
                "repo": repo,
                "branch": branch,
                "message": message,
                "force": False,
                "max_retries": 0,
                "upserts": [
                    {
                        "path": path,
                        "content": base64.b64encode(content).decode("ascii"),
                        "encoding": "base64",
                    }
                    for path, content in upserts.items()
                ],
                "deletes": sorted(deletes),
            },
        )
        sha = result.get("new_commit_sha")
        if not isinstance(sha, str) or not sha:
            commit = result.get("commit")
            sha = commit.get("sha") if isinstance(commit, dict) else None
        if not isinstance(sha, str) or not sha:
            raise BundleInvalidError("GitHub did not return the new commit SHA.")
        return sha

    async def _commit_git_data(
        self,
        *,
        owner: str,
        repo: str,
        branch: str,
        upserts: dict[str, bytes],
        deletes: set[str],
        message: str,
        expected_head: str,
    ) -> str:
        current_head = await self.get_head(owner=owner, repo=repo, branch=branch)
        if current_head != expected_head:
            raise GithubBranchRaceError()

        commit_data = await self._capability(
            self._OP_GET_COMMIT,
            {"owner": owner, "repo": repo, "ref": expected_head},
        )
        commit = commit_data.get("commit")
        tree = commit.get("tree") if isinstance(commit, dict) else None
        base_tree = tree.get("sha") if isinstance(tree, dict) else None
        if not isinstance(base_tree, str) or not base_tree:
            raise BundleInvalidError("GitHub did not return the base tree SHA.")

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
                "base_tree": base_tree,
                "tree": entries,
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
                "message": message,
                "tree": tree_sha,
                "parents": [expected_head],
            },
        )
        commit_sha = commit_result.get("sha")
        if not isinstance(commit_sha, str) or not commit_sha:
            raise BundleInvalidError("GitHub did not return the new commit SHA.")

        try:
            await self._capability(
                self._OP_UPDATE_REF,
                {
                    "owner": owner,
                    "repo": repo,
                    "ref": f"heads/{branch}",
                    "sha": commit_sha,
                    "force": False,
                },
            )
        except DomainError as exc:
            if getattr(exc, "status_code", None) in {409, 422}:
                raise GithubBranchRaceError() from exc
            raise
        return commit_sha

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
                        "content": base64.b64encode(content).decode("ascii"),
                        "encoding": "base64",
                    },
                )
            sha = result.get("sha")
            if not isinstance(sha, str) or not sha:
                raise BundleInvalidError(
                    f"GitHub did not return a blob SHA for '{path}'."
                )
            return path, sha

        blobs = dict(
            await asyncio.gather(
                *(create_blob(path, content) for path, content in upserts.items())
            )
        )
        return blobs


def _unwrap_operation(result: object) -> dict[str, Any]:
    """Normalize ``OperationExecutionResponse.result`` and provider wrappers."""
    current = result
    for _ in range(5):
        if not isinstance(current, dict):
            return {}
        nested: object | None = None
        if isinstance(current.get("result"), dict):
            nested = current["result"]
        elif "successful" in current and "data" in current:
            nested = current["data"]
        elif len(current) == 1 and isinstance(current.get("data"), dict):
            nested = current["data"]
        elif "response_data" in current:
            nested = current["response_data"]
        if isinstance(nested, dict):
            current = nested
            continue
        return current
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
