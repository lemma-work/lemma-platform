"""GitHub fetcher: repo-ref parsing + zipball fetch over an httpx MockTransport."""

import httpx
import pytest

from app.modules.pod_bundle.domain.errors import BundleInvalidError, BundleTooLargeError
from app.modules.pod_bundle.infrastructure.github_fetcher import (
    GithubBundleFetcher,
    parse_repo_ref,
)


def test_parse_repo_ref_from_url():
    assert parse_repo_ref(repo_url="https://github.com/acme/crm", owner=None, repo=None) == (
        "acme",
        "crm",
    )
    assert parse_repo_ref(repo_url="acme/crm.git", owner=None, repo=None) == ("acme", "crm")


def test_parse_repo_ref_from_parts():
    assert parse_repo_ref(repo_url=None, owner="acme", repo="crm") == ("acme", "crm")


def test_parse_repo_ref_invalid():
    with pytest.raises(BundleInvalidError):
        parse_repo_ref(repo_url="not a url", owner=None, repo=None)
    with pytest.raises(BundleInvalidError):
        parse_repo_ref(repo_url=None, owner=None, repo=None)


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


async def test_fetch_zipball_success():
    zip_bytes = b"PK\x03\x04rest-of-a-zip"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/acme/crm/zipball"
        return httpx.Response(200, content=zip_bytes)

    fetcher = GithubBundleFetcher(client=_client(handler))
    got = await fetcher.fetch_zipball(owner="acme", repo="crm")
    assert got == zip_bytes


async def test_fetch_zipball_with_ref_in_path():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/acme/crm/zipball/main"
        return httpx.Response(200, content=b"PK\x03\x04x")

    await GithubBundleFetcher(client=_client(handler)).fetch_zipball(
        owner="acme", repo="crm", ref="main"
    )


async def test_fetch_zipball_404_is_invalid():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with pytest.raises(BundleInvalidError):
        await GithubBundleFetcher(client=_client(handler)).fetch_zipball(
            owner="acme", repo="missing"
        )


async def test_fetch_zipball_non_zip_rejected():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not a zip</html>")

    with pytest.raises(BundleInvalidError):
        await GithubBundleFetcher(client=_client(handler)).fetch_zipball(
            owner="acme", repo="crm"
        )


async def test_fetch_zipball_oversize_rejected(monkeypatch):
    from app.modules.pod_bundle.infrastructure import github_fetcher as gf

    monkeypatch.setattr(gf.pod_bundle_settings, "pod_bundle_max_archive_bytes", 4)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"PK\x03\x04" + b"x" * 100)

    with pytest.raises(BundleTooLargeError):
        await GithubBundleFetcher(client=_client(handler)).fetch_zipball(
            owner="acme", repo="crm"
        )
