"""Fetch a pod bundle from a public GitHub repository.

A public repo needs no authentication, so the fetch is a plain HTTP GET of the
repo's zipball — simpler and less brittle than routing an archive download
through the Composio connector, and it matches how the published bundle is laid
out at the repo root. The downloaded zip is staged verbatim; ``extract_bundle``
locates the bundle root by its ``pod.json`` even though GitHub wraps everything
in a top-level ``{owner}-{repo}-{sha}/`` directory.
"""

from __future__ import annotations

import re

import httpx

from app.core.log.log import get_logger
from app.modules.pod_bundle.config import pod_bundle_settings
from app.modules.pod_bundle.domain.errors import (
    BundleInvalidError,
    BundleTooLargeError,
)

logger = get_logger(__name__)

_REPO_URL_RE = re.compile(
    r"^(?:https?://github\.com/)?(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?/?$"
)


def parse_repo_ref(*, repo_url: str | None, owner: str | None, repo: str | None) -> tuple[str, str]:
    """Resolve (owner, repo) from either a URL or explicit parts. Raises
    :class:`BundleInvalidError` (422) on an unparseable reference."""
    if owner and repo:
        return owner, repo
    if repo_url:
        match = _REPO_URL_RE.match(repo_url.strip())
        if match:
            return match.group("owner"), match.group("repo")
    raise BundleInvalidError("Provide a GitHub repo_url or an owner and repo.")


class GithubBundleFetcher:
    def __init__(self, *, client: httpx.AsyncClient | None = None):
        # Injectable client so tests can supply an httpx MockTransport.
        self._client = client

    async def fetch_zipball(
        self, *, owner: str, repo: str, ref: str | None = None
    ) -> bytes:
        """Download the repo's zipball bytes, enforcing the archive size cap."""
        base = pod_bundle_settings.pod_bundle_github_api_base.rstrip("/")
        path = f"/repos/{owner}/{repo}/zipball"
        if ref:
            path = f"{path}/{ref}"
        url = f"{base}{path}"
        headers = {
            "User-Agent": "lemma-pod-bundle",
            "Accept": "application/vnd.github+json",
        }
        timeout = pod_bundle_settings.pod_bundle_github_fetch_timeout_seconds

        client = self._client
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(follow_redirects=True, timeout=timeout)
        try:
            response = await client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise BundleInvalidError(
                f"Could not reach GitHub for {owner}/{repo}: {exc}"
            ) from exc
        finally:
            if owns_client:
                await client.aclose()

        if response.status_code == 404:
            raise BundleInvalidError(
                f"Repository {owner}/{repo} was not found or is not public."
            )
        if response.status_code >= 400:
            raise BundleInvalidError(
                f"GitHub returned {response.status_code} fetching {owner}/{repo}."
            )
        content = response.content
        if len(content) > pod_bundle_settings.pod_bundle_max_archive_bytes:
            raise BundleTooLargeError(
                f"The {owner}/{repo} archive exceeds the maximum allowed size."
            )
        if not content.startswith(b"PK"):
            raise BundleInvalidError("GitHub did not return a zip archive.")
        return content
