"""Publish a pod bundle to a new GitHub repository.

The publisher is written against a small :class:`GithubOps` port (create repo,
put file) so it is fully unit-testable with a fake — the Composio-backed adapter
:class:`ComposioGithubOps` is the only piece that touches the connector. Uploads
are checkpointed per file and large files fall back to a chunked layout
(reassembled on import), matching a per-request size ceiling.
"""

from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable
from typing import Protocol

from app.core.log.log import get_logger
from app.modules.connectors.contracts import OperationExecutionNotFoundError
from app.modules.pod_bundle.domain.errors import PodBundleDomainError

logger = get_logger(__name__)

# Composio's per-request body ceiling is undocumented; start well under it and
# halve on rejection. Files above the threshold are split into .chunk parts.
_CHUNK_THRESHOLD_BYTES = 150_000
_CHUNK_MIN_BYTES = 8_000


class GithubPublishError(PodBundleDomainError):
    def __init__(self, message: str):
        super().__init__(message, code="POD_BUNDLE_PUBLISH_FAILED", status_code=502)


class RepoCreateResult:
    def __init__(self, *, owner: str, repo: str, html_url: str):
        self.owner = owner
        self.repo = repo
        self.html_url = html_url


class GithubOps(Protocol):
    async def resolve_repo(self, *, name: str) -> RepoCreateResult | None: ...

    async def create_repo(
        self, *, name: str, private: bool, description: str | None
    ) -> RepoCreateResult: ...

    async def put_file(
        self, *, owner: str, repo: str, path: str, content: bytes, message: str
    ) -> None: ...

    async def file_matches(
        self, *, owner: str, repo: str, path: str, content: bytes
    ) -> bool: ...


ProgressCallback = Callable[[str, int, int], Awaitable[None]]


class GithubPublisher:
    def __init__(self, ops: GithubOps):
        self._ops = ops

    async def create_repo(
        self, *, repo_name: str, private: bool, description: str | None
    ) -> RepoCreateResult:
        """Create the repo and return its location (owner/repo/url). Split out from
        :meth:`publish` so a caller that needs the real GitHub **owner** before
        pushing — e.g. to render an install link into the README — can create
        first, then push with ``already_created=repo``."""
        existing = await self._ops.resolve_repo(name=repo_name)
        if existing is not None:
            return existing
        try:
            return await self._ops.create_repo(
                name=repo_name, private=private, description=description
            )
        except Exception as exc:  # provider boundary: resolve ambiguous response
            existing = await self._ops.resolve_repo(name=repo_name)
            if existing is not None:
                return existing
            raise GithubPublishError("Could not create the GitHub repository") from exc

    async def publish(
        self,
        *,
        repo_name: str,
        private: bool,
        description: str | None,
        files: dict[str, bytes],
        readme: str,
        on_progress: ProgressCallback | None = None,
        already_created: RepoCreateResult | None = None,
        completed_paths: set[str] | None = None,
    ) -> RepoCreateResult:
        """Create the repo (tolerating an existing one we already made) and push
        every file plus the README. Returns the repo location."""
        repo = already_created or await self.create_repo(
            repo_name=repo_name, private=private, description=description
        )

        payload = {"README.md": readme.encode("utf-8"), **files}
        total = len(payload)
        done = 0
        for path, content in payload.items():
            if path in (completed_paths or set()):
                done += 1
                continue
            await self._put_with_chunking(repo, path, content)
            done += 1
            if on_progress is not None:
                await on_progress(path, done, total)
        return repo

    async def _put_with_chunking(
        self, repo: RepoCreateResult, path: str, content: bytes
    ) -> None:
        if len(content) <= _CHUNK_THRESHOLD_BYTES:
            await self._put_one(repo, path, content)
            return
        # Split into deterministic .chunkNNNNofMMMM parts the importer reassembles.
        size = _CHUNK_THRESHOLD_BYTES
        parts = [content[i : i + size] for i in range(0, len(content), size)]
        count = len(parts)
        for idx, part in enumerate(parts, start=1):
            chunk_path = f"{path}.chunk{idx:04d}of{count:04d}"
            await self._put_one(repo, chunk_path, part)

    async def _put_one(self, repo: RepoCreateResult, path: str, content: bytes) -> None:
        if await self._ops.file_matches(
            owner=repo.owner,
            repo=repo.repo,
            path=path,
            content=content,
        ):
            return
        try:
            await self._ops.put_file(
                owner=repo.owner,
                repo=repo.repo,
                path=path,
                content=content,
                message=f"Add {path}",
            )
        except Exception as exc:  # provider boundary: resolve ambiguous response
            if await self._ops.file_matches(
                owner=repo.owner,
                repo=repo.repo,
                path=path,
                content=content,
            ):
                return
            raise GithubPublishError(f"Failed to upload {path}") from exc


class ComposioGithubOps:
    """Production :class:`GithubOps` over the Composio GitHub connector.

    ``operation_runner(operation_name, payload) -> dict`` is the injected call to
    ``ConnectorOperationService.execute_operation`` (already bound to the pod's
    GitHub account), so this adapter stays free of connector wiring.
    """

    _OP_CREATE_REPO = "GITHUB_CREATE_A_REPOSITORY_FOR_THE_AUTHENTICATED_USER"
    _OP_PUT_FILE = "GITHUB_CREATE_OR_UPDATE_FILE_CONTENTS"
    _OP_GET_USER = "GITHUB_GET_THE_AUTHENTICATED_USER"
    _OP_GET_REPO = "GITHUB_GET_A_REPOSITORY"
    _OP_GET_CONTENT = "GITHUB_GET_REPOSITORY_CONTENT"

    def __init__(self, operation_runner: Callable[[str, dict], Awaitable[dict]]):
        self._run = operation_runner

    async def resolve_repo(self, *, name: str) -> RepoCreateResult | None:
        user_result = _unwrap(await self._run(self._OP_GET_USER, {}))
        owner = str(user_result.get("login") or "")
        if not owner:
            return None
        try:
            result = _unwrap(
                await self._run(self._OP_GET_REPO, {"owner": owner, "repo": name})
            )
        except OperationExecutionNotFoundError:
            return None
        if not result or result.get("id") is None:
            return None
        return _repo_result(result, fallback_owner=owner, fallback_repo=name)

    async def create_repo(
        self, *, name: str, private: bool, description: str | None
    ) -> RepoCreateResult:
        result = await self._run(
            self._OP_CREATE_REPO,
            {"name": name, "private": private, "description": description or "", "auto_init": False},
        )
        return _repo_result(_unwrap(result), fallback_owner="", fallback_repo=name)

    async def put_file(
        self, *, owner: str, repo: str, path: str, content: bytes, message: str
    ) -> None:
        existing = await self._get_content(owner=owner, repo=repo, path=path)
        payload = {
            "owner": owner,
            "repo": repo,
            "path": path,
            "message": message,
            "content": base64.b64encode(content).decode("ascii"),
        }
        sha = existing.get("sha") if existing else None
        if isinstance(sha, str) and sha:
            payload["sha"] = sha
        await self._run(
            self._OP_PUT_FILE,
            payload,
        )

    async def file_matches(
        self, *, owner: str, repo: str, path: str, content: bytes
    ) -> bool:
        result = await self._get_content(owner=owner, repo=repo, path=path)
        if result is None:
            return False
        encoded = result.get("content")
        if not isinstance(encoded, str):
            return False
        try:
            existing = base64.b64decode(encoded.replace("\n", ""), validate=True)
        except (ValueError, TypeError):
            return False
        return existing == content

    async def _get_content(
        self, *, owner: str, repo: str, path: str
    ) -> dict | None:
        try:
            return _unwrap(
                await self._run(
                    self._OP_GET_CONTENT,
                    {"owner": owner, "repo": repo, "path": path},
                )
            )
        except OperationExecutionNotFoundError:
            return None


def _unwrap(result: dict) -> dict:
    """Composio responses wrap the payload under ``data``/``response_data``."""
    if not isinstance(result, dict):
        return {}
    for key in ("data", "response_data", "result"):
        inner = result.get(key)
        if isinstance(inner, dict):
            return inner
    return result


def _repo_result(
    data: dict,
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
    repo = full.split("/")[1] if "/" in full else fallback_repo
    html_url = str(data.get("html_url") or f"https://github.com/{owner}/{repo}")
    return RepoCreateResult(owner=owner, repo=repo, html_url=html_url)
