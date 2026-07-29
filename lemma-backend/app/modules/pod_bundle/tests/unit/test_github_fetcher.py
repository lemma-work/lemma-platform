"""GitHub fetcher: repo-ref parsing + zipball fetch over an httpx MockTransport."""

import base64

import httpx
import pytest

from app.modules.connectors.domain.errors import (
    OperationExecutionInfrastructureError,
    OperationExecutionUnauthorizedError,
)
from app.modules.pod_bundle.domain.errors import BundleInvalidError, GithubImportError
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

    with pytest.raises(GithubImportError) as exc:
        await GithubBundleFetcher(client=_client(handler)).fetch_zipball(
            owner="acme", repo="missing"
        )
    assert exc.value.code == "GITHUB_REPOSITORY_NOT_FOUND"


@pytest.mark.parametrize(
    ("status", "headers", "expected_code", "expected_status"),
    [
        (403, {"x-ratelimit-remaining": "0"}, "GITHUB_RATE_LIMITED", 429),
        (403, {}, "GITHUB_IMPORT_UNAUTHORIZED", 403),
        (502, {}, "GITHUB_IMPORT_TRANSIENT", 503),
    ],
)
async def test_fetch_zipball_maps_provider_failures(
    status,
    headers,
    expected_code,
    expected_status,
):
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(status, headers=headers)

    with pytest.raises(GithubImportError) as exc:
        await GithubBundleFetcher(client=_client(handler)).fetch_zipball(
            owner="acme",
            repo="crm",
        )
    assert exc.value.code == expected_code
    assert exc.value.status_code == expected_status


async def test_fetch_zipball_non_zip_rejected():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not a zip</html>")

    with pytest.raises(GithubImportError) as exc:
        await GithubBundleFetcher(client=_client(handler)).fetch_zipball(
            owner="acme", repo="crm"
        )
    assert exc.value.code == "GITHUB_ARCHIVE_INVALID"


async def test_fetch_zipball_oversize_rejected(monkeypatch):
    from app.modules.pod_bundle.infrastructure import github_fetcher as gf

    monkeypatch.setattr(gf.pod_bundle_settings, "pod_bundle_max_archive_bytes", 4)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"PK\x03\x04" + b"x" * 100)

    with pytest.raises(GithubImportError) as exc:
        await GithubBundleFetcher(client=_client(handler)).fetch_zipball(
            owner="acme", repo="crm"
        )
    assert exc.value.code == "GITHUB_ARCHIVE_TOO_LARGE"


async def test_authenticated_fetch_uses_current_archive_response_shape():
    zip_bytes = b"PK\x03\x04private"
    calls = []

    async def runner(op, payload):
        calls.append((op, payload))
        if op == "GITHUB_GET_A_REPOSITORY":
            data = {"default_branch": "trunk"}
        else:
            data = {
                "status": 200,
                "data": {
                    "type": "binary_content",
                    "content_base64": base64.b64encode(zip_bytes).decode(),
                },
            }
        return {"result": {"data": data, "successful": True}}

    got = await GithubBundleFetcher(operation_runner=runner).fetch_zipball(
        owner="acme",
        repo="private",
    )
    assert got == zip_bytes
    assert calls[-1] == (
        "GITHUB_DOWNLOAD_A_REPOSITORY_ARCHIVE_ZIP",
        {"owner": "acme", "repo": "private", "ref": "trunk"},
    )


async def test_authenticated_fetch_follows_signed_location_without_auth_header():
    zip_bytes = b"PK\x03\x04signed"

    async def runner(op, payload):
        del op, payload
        return {
            "result": {
                "data": {
                    "status": 302,
                    "headers": {"Location": "https://objects.example/archive.zip"},
                },
                "successful": True,
            }
        }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://objects.example/archive.zip"
        assert "authorization" not in request.headers
        return httpx.Response(200, content=zip_bytes)

    got = await GithubBundleFetcher(
        client=_client(handler),
        operation_runner=runner,
    ).fetch_zipball(owner="acme", repo="private", ref="main")
    assert got == zip_bytes


@pytest.mark.parametrize(
    ("provider_error", "expected_code", "expected_status"),
    [
        (
            OperationExecutionUnauthorizedError("expired"),
            "GITHUB_IMPORT_UNAUTHORIZED",
            403,
        ),
        (
            OperationExecutionInfrastructureError("provider down"),
            "GITHUB_IMPORT_TRANSIENT",
            503,
        ),
    ],
)
async def test_authenticated_fetch_maps_terminal_and_retryable_connector_errors(
    provider_error,
    expected_code,
    expected_status,
):
    async def runner(op, payload):
        del op, payload
        raise provider_error

    with pytest.raises(GithubImportError) as exc:
        await GithubBundleFetcher(operation_runner=runner).fetch_zipball(
            owner="acme",
            repo="private",
            ref="main",
        )
    assert exc.value.code == expected_code
    assert exc.value.status_code == expected_status


async def test_stream_stops_when_size_cap_is_crossed(monkeypatch):
    from app.modules.pod_bundle.infrastructure import github_fetcher as gf

    monkeypatch.setattr(gf.pod_bundle_settings, "pod_bundle_max_archive_bytes", 8)

    class StreamingBody(httpx.AsyncByteStream):
        yielded = 0

        async def __aiter__(self):
            for chunk in (b"PK\x03\x04", b"1234", b"must-not-buffer"):
                self.yielded += 1
                yield chunk

    body = StreamingBody()

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, stream=body)

    with pytest.raises(GithubImportError) as exc:
        await GithubBundleFetcher(client=_client(handler)).fetch_zipball(
            owner="acme",
            repo="crm",
        )
    assert exc.value.code == "GITHUB_ARCHIVE_TOO_LARGE"
    assert body.yielded == 3
