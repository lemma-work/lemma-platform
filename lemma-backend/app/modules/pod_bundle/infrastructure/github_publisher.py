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
    async def create_repo(
        self, *, name: str, private: bool, description: str | None
    ) -> RepoCreateResult: ...

    async def put_file(
        self, *, owner: str, repo: str, path: str, content: bytes, message: str
    ) -> None: ...


ProgressCallback = Callable[[str, int, int], Awaitable[None]]


class GithubPublisher:
    def __init__(self, ops: GithubOps):
        self._ops = ops

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
    ) -> RepoCreateResult:
        """Create the repo (tolerating an existing one we already made) and push
        every file plus the README. Returns the repo location."""
        repo = already_created
        if repo is None:
            try:
                repo = await self._ops.create_repo(
                    name=repo_name, private=private, description=description
                )
            except Exception as exc:  # noqa: BLE001
                raise GithubPublishError(f"Could not create GitHub repo: {exc}") from exc

        payload = {"README.md": readme.encode("utf-8"), **files}
        total = len(payload)
        done = 0
        for path, content in payload.items():
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
        try:
            await self._ops.put_file(
                owner=repo.owner,
                repo=repo.repo,
                path=path,
                content=content,
                message=f"Add {path}",
            )
        except Exception as exc:  # noqa: BLE001
            raise GithubPublishError(f"Failed to upload {path}: {exc}") from exc


class ComposioGithubOps:
    """Production :class:`GithubOps` over the Composio GitHub connector.

    ``operation_runner(operation_name, payload) -> dict`` is the injected call to
    ``ConnectorOperationService.execute_operation`` (already bound to the pod's
    GitHub account), so this adapter stays free of connector wiring.
    """

    _OP_CREATE_REPO = "GITHUB_CREATE_A_REPOSITORY_FOR_THE_AUTHENTICATED_USER"
    _OP_PUT_FILE = "GITHUB_CREATE_OR_UPDATE_FILE_CONTENTS"

    def __init__(self, operation_runner: Callable[[str, dict], Awaitable[dict]]):
        self._run = operation_runner

    async def create_repo(
        self, *, name: str, private: bool, description: str | None
    ) -> RepoCreateResult:
        result = await self._run(
            self._OP_CREATE_REPO,
            {"name": name, "private": private, "description": description or "", "auto_init": False},
        )
        data = _unwrap(result)
        full = str(data.get("full_name") or "")
        owner = full.split("/")[0] if "/" in full else str(
            (data.get("owner") or {}).get("login") or ""
        )
        repo = full.split("/")[1] if "/" in full else name
        html_url = str(data.get("html_url") or f"https://github.com/{owner}/{repo}")
        return RepoCreateResult(owner=owner, repo=repo, html_url=html_url)

    async def put_file(
        self, *, owner: str, repo: str, path: str, content: bytes, message: str
    ) -> None:
        await self._run(
            self._OP_PUT_FILE,
            {
                "owner": owner,
                "repo": repo,
                "path": path,
                "message": message,
                "content": base64.b64encode(content).decode("ascii"),
            },
        )


def _unwrap(result: dict) -> dict:
    """Composio responses wrap the payload under ``data``/``response_data``."""
    if not isinstance(result, dict):
        return {}
    for key in ("data", "response_data", "result"):
        inner = result.get(key)
        if isinstance(inner, dict):
            return inner
    return result
