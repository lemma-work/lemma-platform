"""The outbound URL guard.

A connector install names the address we connect to, and in a multi-tenant
deployment whoever names it is not necessarily someone we trust with the
cluster's internal network. Each case here is a way that has actually been used
to turn a server-side fetcher into a window onto private infrastructure.
"""

from __future__ import annotations

import pytest

from app.core.net.url_guard import (
    GuardPolicy,
    UnsafeUrlError,
    assert_safe_host,
    assert_safe_url,
    request_guarded,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

# The guard as it behaves in production: nothing private, https only, and an
# address that will not resolve is refused rather than attempted.
STRICT = GuardPolicy(allow_private=False, allow_http=False, allow_unresolvable=False)


async def _reason(url: str, policy: GuardPolicy = STRICT) -> str:
    with pytest.raises(UnsafeUrlError) as excinfo:
        await assert_safe_url(url, policy=policy)
    return excinfo.value.reason


class TestAddressesThatAreNotThePublicInternet:
    async def test_the_cloud_metadata_service_is_refused(self):
        # The one that matters most: on AWS/GCP this hands out credentials.
        assert await _reason("https://169.254.169.254/latest/meta-data/") == (
            "link_local_address"
        )

    async def test_ipv6_link_local_is_refused(self):
        assert await _reason("https://[fe80::1]/") == "link_local_address"

    @pytest.mark.parametrize(
        "url",
        [
            "https://127.0.0.1/",
            "https://[::1]/",
        ],
    )
    async def test_loopback_is_refused(self, url):
        assert await _reason(url) == "loopback_address"

    @pytest.mark.parametrize(
        "url",
        [
            "https://10.0.0.5/",
            "https://192.168.1.1/",
            "https://172.16.0.1/",
        ],
    )
    async def test_rfc1918_ranges_are_refused(self, url):
        assert await _reason(url) == "private_address"

    async def test_unique_local_ipv6_is_refused(self):
        assert await _reason("https://[fd00::1]/") == "private_address"

    async def test_the_unspecified_address_is_refused(self):
        # 0.0.0.0 routes to localhost on several stacks. Which bucket it lands
        # in matters less than that it is refused.
        assert await _reason("https://0.0.0.0/") in {
            "reserved_address",
            "private_address",
        }

    async def test_localhost_is_refused_because_it_resolves_to_loopback(self):
        # Proves the check follows resolution rather than matching on the name.
        assert await _reason("https://localhost/") == "loopback_address"


class TestUrlShape:
    async def test_plain_http_is_refused_in_production(self):
        assert await _reason("http://example.com/") == "scheme_not_allowed"

    @pytest.mark.parametrize("url", ["file:///etc/passwd", "gopher://x/", "ftp://x/"])
    async def test_non_http_schemes_are_refused(self, url):
        assert await _reason(url) == "scheme_not_allowed"

    async def test_embedded_credentials_are_refused(self):
        # These leak into logs and get forwarded on redirects.
        assert await _reason("https://user:pw@example.com/") == "credentials_in_url"

    async def test_a_url_without_a_host_is_refused(self):
        assert await _reason("https:///nowhere") == "missing_host"

    @pytest.mark.parametrize(
        "port", [22, 25, 3306, 5432, 6379, 9200, 11211, 27017]
    )
    async def test_non_http_service_ports_are_refused(self, port):
        # Aiming an HTTP fetcher at SSH, Postgres or Redis is protocol confusion,
        # not a connector target. Several of these sit above 1024, so a check on
        # port number alone would wave through exactly the interesting ones.
        assert await _reason(f"https://example.com:{port}/") == "port_not_allowed"

    @pytest.mark.parametrize("port", [80, 443, 8443, 3000])
    async def test_http_ports_are_allowed(self, port):
        # Resolution is permitted to fail here; the point is the port check.
        policy = GuardPolicy(allow_unresolvable=True)
        assert await assert_safe_url(
            f"https://api.example.com:{port}/", policy=policy
        )


class TestFailureModes:
    async def test_an_unresolvable_host_fails_closed_in_production(self):
        assert await _reason("https://nx.invalid-tld-that-does-not-exist/") == (
            "unresolvable_host"
        )

    async def test_it_fails_open_under_test_so_mock_transports_still_work(self):
        # Suites drive mock transports against hostnames never meant to resolve,
        # and CI need not have outbound DNS.
        policy = GuardPolicy(allow_unresolvable=True)
        assert await assert_safe_url("https://api.example.com/", policy=policy)

    async def test_but_a_literal_private_address_is_still_refused_under_test(self):
        # Failing open on DNS must not become failing open on everything.
        policy = GuardPolicy(allow_unresolvable=True)
        with pytest.raises(UnsafeUrlError):
            await assert_safe_url("https://169.254.169.254/", policy=policy)


class TestSelfHostingEscapeHatch:
    async def test_private_targets_are_permitted_when_explicitly_enabled(self):
        # A self-hosted deployment running connectors against its own network.
        policy = GuardPolicy(allow_private=True, allow_http=True)
        assert await assert_safe_url("http://10.0.0.5:8080/mcp", policy=policy)

    async def test_it_is_off_by_default(self):
        assert GuardPolicy().allow_private is False


class TestBareHosts:
    """SQL installs give a host and port rather than a URL."""

    async def test_a_private_database_host_is_refused(self):
        with pytest.raises(UnsafeUrlError) as excinfo:
            await assert_safe_host("10.0.0.5", 5432, policy=STRICT)
        assert excinfo.value.reason == "private_address"

    async def test_localhost_is_refused(self):
        with pytest.raises(UnsafeUrlError):
            await assert_safe_host("localhost", 5432, policy=STRICT)

    async def test_a_public_host_is_allowed(self):
        policy = GuardPolicy(allow_unresolvable=True)
        assert await assert_safe_host("db.example.com", 5432, policy=policy)


class TestTheSelfHostingEscapeHatchIsNarrow:
    """`allow_private` unlocks your own network, not the metadata service.

    It used to return early and skip address checking entirely, so a
    self-hosted deployment that turned it on to reach an internal MCP server
    also made 169.254.169.254 a legal connector target -- handing out instance
    credentials to anyone who could create an auth config.
    """

    @pytest.mark.asyncio
    async def test_a_private_address_is_allowed(self):
        policy = GuardPolicy(allow_private=True, allow_http=True, allow_unresolvable=True)
        assert await assert_safe_url("http://10.0.0.5:8080/mcp", policy=policy)

    @pytest.mark.asyncio
    async def test_loopback_is_allowed(self):
        policy = GuardPolicy(allow_private=True, allow_http=True, allow_unresolvable=True)
        assert await assert_safe_url("http://127.0.0.1:9000/mcp", policy=policy)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "url",
        [
            "http://169.254.169.254/latest/meta-data/",
            "http://[fe80::1]/",
        ],
    )
    async def test_link_local_stays_refused(self, url):
        policy = GuardPolicy(allow_private=True, allow_http=True, allow_unresolvable=True)
        with pytest.raises(UnsafeUrlError) as caught:
            await assert_safe_url(url, policy=policy)
        assert caught.value.reason == "link_local_address"

    @pytest.mark.asyncio
    async def test_a_private_database_host_is_allowed(self):
        policy = GuardPolicy(allow_private=True, allow_http=True, allow_unresolvable=True)
        assert await assert_safe_host("10.0.0.9", 5432, policy=policy)

    @pytest.mark.asyncio
    async def test_the_metadata_host_stays_refused_for_sql_too(self):
        policy = GuardPolicy(allow_private=True, allow_http=True, allow_unresolvable=True)
        with pytest.raises(UnsafeUrlError):
            await assert_safe_host("169.254.169.254", 5432, policy=policy)


class TestRedirectsAreFollowedWithoutCorruptingTheTarget:
    """`request_guarded` re-issues each hop itself, so it decides what the next
    request carries. Getting that wrong either leaks the caller's credentials to
    whoever they were redirected to, or mangles a target that was perfectly
    fine."""

    @staticmethod
    def _client(handler):
        import httpx

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    @pytest.mark.asyncio
    async def test_a_signed_redirect_target_keeps_its_own_query(self):
        """Regression: GitHub's archive endpoint redirects to a `codeload` URL
        whose query string is the only thing authorizing it. Handing httpx an
        empty param list made it rebuild the URL without that query, so every
        real archive download came back 404."""
        import httpx

        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            if request.url.host == "api.example.com":
                return httpx.Response(
                    302,
                    headers={"location": "https://cdn.example.com/a.zip?token=signed"},
                )
            if request.url.params.get("token") != "signed":
                return httpx.Response(404)
            return httpx.Response(200, content=b"PK\x03\x04")

        response = await request_guarded(
            self._client(handler),
            "GET",
            "https://api.example.com/repos/o/r/zipball/main",
            headers={"Authorization": "Bearer secret"},
            credential_header_names=["Authorization"],
        )

        assert response.status_code == 200
        assert response.content == b"PK\x03\x04"
        assert seen[-1] == "https://cdn.example.com/a.zip?token=signed"

    @pytest.mark.asyncio
    async def test_credentials_still_do_not_survive_leaving_the_origin(self):
        import httpx

        forwarded: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "api.example.com":
                return httpx.Response(
                    302, headers={"location": "https://elsewhere.example/collect"}
                )
            forwarded["auth"] = request.headers.get("authorization")
            forwarded["api_key"] = request.url.params.get("api_key")
            return httpx.Response(200)

        await request_guarded(
            self._client(handler),
            "GET",
            "https://api.example.com/thing",
            headers={"Authorization": "Bearer secret"},
            params=[("api_key", "secret"), ("page", "2")],
            credential_header_names=["Authorization"],
            credential_param_names=["api_key"],
        )

        assert forwarded["auth"] is None
        assert forwarded["api_key"] is None
