"""Stream public or authenticated GitHub repository archives."""

from __future__ import annotations

import base64
import re
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.core.domain.errors import DomainError
from app.modules.pod_bundle.config import pod_bundle_settings
from app.modules.pod_bundle.domain.errors import BundleInvalidError, GithubImportError

_REPO_URL_RE = re.compile(
    r"^(?:https?://github\.com/)?(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?/?$"
)

OperationRunner = Callable[[str, dict], Awaitable[dict]]


def parse_repo_ref(
    *,
    repo_url: str | None,
    owner: str | None,
    repo: str | None,
) -> tuple[str, str]:
    if owner and repo:
        return owner, repo
    if repo_url:
        match = _REPO_URL_RE.match(repo_url.strip())
        if match:
            return match.group("owner"), match.group("repo")
    raise BundleInvalidError("Provide a GitHub repo_url or an owner and repo.")


class GithubBundleFetcher:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        operation_runner: OperationRunner | None = None,
    ):
        self._client = client
        self._run = operation_runner

    async def fetch_zipball(
        self,
        *,
        owner: str,
        repo: str,
        ref: str | None = None,
    ) -> bytes:
        if self._run is not None:
            return await self._fetch_authenticated(
                owner=owner,
                repo=repo,
                ref=ref,
            )

        base = pod_bundle_settings.pod_bundle_github_api_base.rstrip("/")
        path = f"/repos/{owner}/{repo}/zipball"
        if ref:
            path = f"{path}/{ref}"
        headers = self._github_headers()
        return await self._stream_url(
            f"{base}{path}",
            headers=headers,
            owner=owner,
            repo=repo,
        )

    async def _fetch_authenticated(
        self,
        *,
        owner: str,
        repo: str,
        ref: str | None,
    ) -> bytes:
        assert self._run is not None
        if not ref:
            repo_result = _unwrap_operation(
                await self._run(
                    "GITHUB_GET_A_REPOSITORY",
                    {"owner": owner, "repo": repo},
                )
            )
            ref_value = repo_result.get("default_branch")
            ref = ref_value if isinstance(ref_value, str) and ref_value else "main"

        try:
            result = _unwrap_operation(
                await self._run(
                    "GITHUB_DOWNLOAD_A_REPOSITORY_ARCHIVE_ZIP",
                    {"owner": owner, "repo": repo, "ref": ref},
                )
            )
        except DomainError as exc:
            raise _map_connector_error(exc, owner=owner, repo=repo) from exc

        inline = _extract_binary(result)
        if inline is not None:
            return _validate_archive(inline, owner=owner, repo=repo)

        headers = result.get("headers")
        location = None
        if isinstance(headers, dict):
            location = headers.get("Location") or headers.get("location")
        if not isinstance(location, str) or not location:
            raise GithubImportError(
                "GitHub did not return an archive download location.",
                code="GITHUB_ARCHIVE_INVALID",
                status_code=422,
            )
        # GitHub's archive redirect is a short-lived signed URL. Do not forward
        # Lemma's configured GitHub token to that different host.
        return await self._stream_url(
            location,
            headers={"User-Agent": "lemma-pod-bundle"},
            owner=owner,
            repo=repo,
        )

    def _github_headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": "lemma-pod-bundle",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        token = pod_bundle_settings.pod_bundle_github_token
        if token:
            headers["Authorization"] = f"Bearer {token.get_secret_value()}"
        return headers

    async def _stream_url(
        self,
        url: str,
        *,
        headers: dict[str, str],
        owner: str,
        repo: str,
    ) -> bytes:
        timeout = pod_bundle_settings.pod_bundle_github_fetch_timeout_seconds
        client = self._client
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(follow_redirects=True, timeout=timeout)
        try:
            async with client.stream("GET", url, headers=headers) as response:
                _raise_for_github_status(response, owner=owner, repo=repo)
                content_length = response.headers.get("content-length")
                if content_length:
                    try:
                        declared_size = int(content_length)
                    except ValueError:
                        declared_size = 0
                    if (
                        declared_size
                        > pod_bundle_settings.pod_bundle_max_archive_bytes
                    ):
                        raise GithubImportError(
                            f"The {owner}/{repo} archive exceeds the maximum allowed size.",
                            code="GITHUB_ARCHIVE_TOO_LARGE",
                            status_code=413,
                        )

                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if (
                        len(content)
                        > pod_bundle_settings.pod_bundle_max_archive_bytes
                    ):
                        raise GithubImportError(
                            f"The {owner}/{repo} archive exceeds the maximum allowed size.",
                            code="GITHUB_ARCHIVE_TOO_LARGE",
                            status_code=413,
                        )
        except GithubImportError:
            raise
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise GithubImportError(
                f"GitHub is temporarily unavailable for {owner}/{repo}.",
                code="GITHUB_IMPORT_TRANSIENT",
                status_code=503,
            ) from exc
        finally:
            if owns_client:
                await client.aclose()
        return _validate_archive(bytes(content), owner=owner, repo=repo)


def _raise_for_github_status(
    response: httpx.Response,
    *,
    owner: str,
    repo: str,
) -> None:
    status = response.status_code
    if status == 404:
        raise GithubImportError(
            f"Repository {owner}/{repo} was not found or requires a connected GitHub account.",
            code="GITHUB_REPOSITORY_NOT_FOUND",
            status_code=404,
        )
    if status in {401, 403}:
        code = (
            "GITHUB_RATE_LIMITED"
            if response.headers.get("x-ratelimit-remaining") == "0"
            else "GITHUB_IMPORT_UNAUTHORIZED"
        )
        raise GithubImportError(
            f"GitHub did not authorize access to {owner}/{repo}.",
            code=code,
            status_code=429 if code == "GITHUB_RATE_LIMITED" else 403,
        )
    if status == 429:
        raise GithubImportError(
            "GitHub rate-limited the repository download.",
            code="GITHUB_RATE_LIMITED",
            status_code=429,
        )
    if status >= 500:
        raise GithubImportError(
            "GitHub is temporarily unavailable.",
            code="GITHUB_IMPORT_TRANSIENT",
            status_code=503,
        )
    if status >= 400:
        raise GithubImportError(
            f"GitHub returned {status} fetching {owner}/{repo}.",
            code="GITHUB_ARCHIVE_INVALID",
            status_code=422,
        )


def _validate_archive(content: bytes, *, owner: str, repo: str) -> bytes:
    if len(content) > pod_bundle_settings.pod_bundle_max_archive_bytes:
        raise GithubImportError(
            f"The {owner}/{repo} archive exceeds the maximum allowed size.",
            code="GITHUB_ARCHIVE_TOO_LARGE",
            status_code=413,
        )
    if not content.startswith(b"PK"):
        raise GithubImportError(
            "GitHub did not return a zip archive.",
            code="GITHUB_ARCHIVE_INVALID",
            status_code=422,
        )
    return content


def _extract_binary(value: object) -> bytes | None:
    if isinstance(value, dict):
        if value.get("type") == "binary_content":
            encoded = value.get("content_base64")
            if isinstance(encoded, str):
                try:
                    return base64.b64decode(encoded, validate=True)
                except (ValueError, TypeError):
                    return None
        for key in ("data", "content", "body"):
            nested = _extract_binary(value.get(key))
            if nested is not None:
                return nested
    return None


def _unwrap_operation(result: object) -> dict[str, Any]:
    current = result
    for _ in range(5):
        if not isinstance(current, dict):
            return {}
        if isinstance(current.get("result"), dict):
            current = current["result"]
            continue
        if "successful" in current and isinstance(current.get("data"), dict):
            current = current["data"]
            continue
        return current
    return current if isinstance(current, dict) else {}


def _map_connector_error(
    exc: DomainError,
    *,
    owner: str,
    repo: str,
) -> GithubImportError:
    status = getattr(exc, "status_code", None)
    if status == 404:
        return GithubImportError(
            f"Repository {owner}/{repo} was not found.",
            code="GITHUB_REPOSITORY_NOT_FOUND",
            status_code=404,
        )
    if status in {401, 403}:
        return GithubImportError(
            f"GitHub did not authorize access to {owner}/{repo}.",
            code="GITHUB_IMPORT_UNAUTHORIZED",
            status_code=403,
        )
    if status == 429:
        return GithubImportError(
            "GitHub rate-limited the repository download.",
            code="GITHUB_RATE_LIMITED",
            status_code=429,
        )
    if isinstance(status, int) and status >= 500:
        return GithubImportError(
            "GitHub is temporarily unavailable.",
            code="GITHUB_IMPORT_TRANSIENT",
            status_code=503,
        )
    return GithubImportError(
        "GitHub could not download the repository archive.",
        code="GITHUB_ARCHIVE_INVALID",
        status_code=422,
    )
