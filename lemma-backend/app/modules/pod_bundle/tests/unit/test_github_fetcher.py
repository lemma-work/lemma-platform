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
    assert parse_repo_ref(
        repo_url="https://github.com/acme/crm", owner=None, repo=None
    ) == (
        "acme",
        "crm",
    )
    assert parse_repo_ref(repo_url="acme/crm.git", owner=None, repo=None) == (
        "acme",
        "crm",
    )


def test_parse_repo_ref_from_parts():
    assert parse_repo_ref(repo_url=None, owner="acme", repo="crm") == ("acme", "crm")


def test_parse_repo_ref_invalid():
    with pytest.raises(BundleInvalidError):
        parse_repo_ref(repo_url="not a url", owner=None, repo=None)
    with pytest.raises(BundleInvalidError):
        parse_repo_ref(repo_url=None, owner=None, repo=None)


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    )


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


async def test_authenticated_fetch_uses_the_native_archive_operation():
    zip_bytes = b"PK\x03\x04private"
    calls = []

    async def runner(op, payload):
        calls.append((op, payload))
        if op == "repos_get":
            result = {"default_branch": "trunk"}
        else:
            # The connector's HTTP executor follows GitHub's redirect to the
            # signed archive URL itself, so the bytes arrive inline.
            result = {
                "type": "binary_content",
                "content_base64": base64.b64encode(zip_bytes).decode(),
                "media_type": "application/zip",
                "size_bytes": len(zip_bytes),
            }
        return {"result": result}

    got = await GithubBundleFetcher(operation_runner=runner).fetch_zipball(
        owner="acme",
        repo="private",
    )
    assert got == zip_bytes
    assert calls == [
        ("repos_get", {"owner": "acme", "repo": "private"}),
        (
            "repos_download_zipball_archive",
            {"owner": "acme", "repo": "private", "ref": "trunk"},
        ),
    ]


async def test_authenticated_fetch_without_archive_bytes_is_an_invalid_archive():
    async def runner(op, payload):
        del op, payload
        return {"result": {"status": 302}}

    with pytest.raises(GithubImportError) as exc:
        await GithubBundleFetcher(operation_runner=runner).fetch_zipball(
            owner="acme",
            repo="private",
            ref="main",
        )
    assert exc.value.code == "GITHUB_ARCHIVE_INVALID"


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


# The pod's `github` connector refusing -- no connector declared, the person
# has not connected -- is not the end of an import: a public repository needs
# no account. The refusal is what the person sees only when it is the remedy.

_CONNECT = "https://app.example/pods/p/connections?connector=github"


def _refusing_runner(error):
    async def run(operation_name, payload):
        del operation_name, payload
        raise error

    return run


async def test_a_public_repo_imports_anonymously_when_the_connector_refuses():
    from app.modules.connectors.contracts.pod_connectors import (
        ConnectionRequiredError,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"PK\x03\x04x")

    fetcher = GithubBundleFetcher(
        client=_client(handler),
        operation_runner=_refusing_runner(
            ConnectionRequiredError(
                slug="github", connector_id="github", connect_url=_CONNECT
            )
        ),
    )

    assert await fetcher.fetch_zipball(owner="acme", repo="crm") == b"PK\x03\x04x"
    assert fetcher.used_anonymous_access is True


async def test_a_private_repo_reports_the_connection_it_needs():
    """GitHub answers 404 for a private repository it was asked for
    anonymously; the thing to tell the person is to connect, with the link."""
    from app.modules.connectors.contracts.pod_connectors import (
        ConnectionRequiredError,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    fetcher = GithubBundleFetcher(
        client=_client(handler),
        operation_runner=_refusing_runner(
            ConnectionRequiredError(
                slug="github", connector_id="github", connect_url=_CONNECT
            )
        ),
    )

    with pytest.raises(ConnectionRequiredError) as refused:
        await fetcher.fetch_zipball(owner="acme", repo="secret")
    assert refused.value.details["connect_url"] == _CONNECT


async def test_a_pod_without_a_github_connector_says_so_for_a_private_repo():
    from app.modules.connectors.contracts.pod_connectors import (
        PodConnectorNotFoundError,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    fetcher = GithubBundleFetcher(
        client=_client(handler),
        operation_runner=_refusing_runner(PodConnectorNotFoundError("github")),
    )

    with pytest.raises(PodConnectorNotFoundError):
        await fetcher.fetch_zipball(owner="acme", repo="secret")


async def test_a_rate_limit_stays_a_rate_limit_when_the_connector_refused():
    """It is retried as one; the connector's refusal would end the import."""
    from app.modules.connectors.contracts.pod_connectors import (
        PodConnectorNotFoundError,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    fetcher = GithubBundleFetcher(
        client=_client(handler),
        operation_runner=_refusing_runner(PodConnectorNotFoundError("github")),
    )

    with pytest.raises(GithubImportError) as exc:
        await fetcher.fetch_zipball(owner="acme", repo="busy")
    assert exc.value.code == "GITHUB_RATE_LIMITED"
