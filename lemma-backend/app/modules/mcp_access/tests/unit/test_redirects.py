"""A redirect URI is where the consent page sends a signed-in browser. One that
runs code would run it on the auth site with the person's session."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from mcp.server.auth.provider import RegistrationError
from mcp.shared.auth import InvalidRedirectUriError, OAuthClientInformationFull
from pydantic import AnyUrl

from app.modules.mcp_access.domain.entities import ClientRegistration
from app.modules.mcp_access.domain.redirects import redirect_allowed, redirect_matches
from app.modules.mcp_access.infrastructure.repositories import StoredClient
from app.modules.mcp_access.services.clients import LemmaOAuthClient, _from_stored

pytestmark = pytest.mark.unit

# The payload from the review: it has a scheme and a host, so a URL parser
# accepts it, and a browser runs it.
SCRIPT = "javascript://evil.example/%0aalert(document.domain)//"


@pytest.mark.parametrize(
    "uri",
    [
        "https://claude.ai/api/mcp/auth_callback",
        "https://chatgpt.com/connector_platform_oauth_redirect",
        "http://localhost:53172/callback",
        "http://127.0.0.1/callback",
        "http://[::1]:8080/cb",
        "cursor://anysphere.cursor-retrieval/oauth/callback",
        "com.example.app:/oauth2redirect",
    ],
)
def test_the_redirects_real_clients_use_are_allowed(uri):
    assert redirect_allowed(uri)


@pytest.mark.parametrize(
    "uri",
    [
        SCRIPT,
        "JavaScript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "vbscript:msgbox(1)",
        "blob:https://evil.example/uuid",
        "file:///etc/passwd",
        "about:blank",
        "view-source:https://lemma.work",
        "http://evil.example/callback",
        "https://user:pw@claude.ai/cb",
        "https:///no-host",
        "https://claude.ai/cb\nx",
        "not a url",
        "",
    ],
)
def test_redirects_that_run_code_or_leave_loopback_in_the_clear_are_refused(uri):
    assert not redirect_allowed(uri)


def _registered(*uris: str) -> LemmaOAuthClient:
    return LemmaOAuthClient.model_construct(
        client_id="dcr",
        redirect_uris=[AnyUrl(uri) for uri in uris],
        redirect_patterns=[],
        registration=ClientRegistration.DYNAMIC,
        grant_types=["authorization_code", "refresh_token"],
    )


def test_an_exact_match_is_not_enough_when_the_registered_uri_is_unsafe():
    client = _registered(SCRIPT)
    with pytest.raises(InvalidRedirectUriError):
        client.validate_redirect_uri(AnyUrl(SCRIPT))


def test_the_single_uri_default_is_checked_too():
    """Leaving `redirect_uri` out used to return the one registered URI
    without looking at it."""
    with pytest.raises(InvalidRedirectUriError):
        _registered(SCRIPT).validate_redirect_uri(None)
    assert (
        str(_registered("https://claude.ai/cb").validate_redirect_uri(None))
        == "https://claude.ai/cb"
    )


def test_a_metadata_documents_unsafe_redirects_are_not_patterns_either():
    client = _from_stored(
        StoredClient(
            client_id="https://evil.example/client.json",
            registration=ClientRegistration.METADATA_DOCUMENT.value,
            client_metadata={"redirect_uris": [SCRIPT, "https://evil.example/cb"]},
            client_secret_hash=None,
            updated_at=datetime.now(timezone.utc),
        )
    )
    with pytest.raises(InvalidRedirectUriError):
        client.validate_redirect_uri(AnyUrl(SCRIPT))


class _Redis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set(self, name, value, ex, nx=False):
        if nx and name in self.values:
            return None
        self.values[name] = value
        return True

    async def get(self, name):
        return self.values.get(name)

    async def getdel(self, name):
        return self.values.pop(name, None)


class _Fetcher:
    def __init__(self, redirect_uris: list[str]) -> None:
        self.redirect_uris = redirect_uris

    def is_cimd_client_id(self, client_id: str) -> bool:
        return client_id.startswith("https://")

    async def fetch(self, client_id: str):
        from fastmcp.server.auth.cimd import CIMDDocument

        return CIMDDocument.model_validate(
            {
                "client_id": client_id,
                "client_name": "Claude",
                "redirect_uris": self.redirect_uris,
            }
        )


def _directory(redirect_uris: list[str]):
    from app.modules.mcp_access.infrastructure.ephemeral import EphemeralStore
    from app.modules.mcp_access.services.clients import ClientDirectory

    return ClientDirectory(
        None,  # type: ignore[arg-type]
        ephemeral=EphemeralStore(_Redis()),
        fetcher=_Fetcher(redirect_uris),  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_a_document_listing_only_unsafe_redirects_names_no_client():
    assert await _directory([SCRIPT]).get("https://evil.example/client.json") is None


@pytest.mark.asyncio
async def test_a_documents_unsafe_redirects_are_dropped_and_the_rest_kept():
    client = await _directory([SCRIPT, "https://evil.example/cb"]).get(
        "https://evil.example/client.json"
    )
    assert client is not None
    assert client.redirect_patterns == ["https://evil.example/cb"]
    assert client.verified_host == "evil.example"


@pytest.mark.asyncio
async def test_registration_refuses_an_unsafe_redirect():
    with pytest.raises(RegistrationError):
        await _directory([]).register(
            OAuthClientInformationFull.model_construct(
                client_id="x", redirect_uris=[AnyUrl(SCRIPT)]
            )
        )


@pytest.mark.parametrize(
    ("candidate", "registered"),
    [
        (
            "https://claude.ai/api/mcp/auth_callback",
            "https://claude.ai/api/mcp/auth_callback",
        ),
        ("http://localhost:54321/callback", "http://localhost/callback"),
        ("http://127.0.0.1:1/callback", "http://127.0.0.1:8080/callback"),
        ("http://[::1]:9/cb", "http://[::1]/cb"),
    ],
)
def test_a_redirect_matches_exactly_or_on_another_loopback_port(candidate, registered):
    assert redirect_matches(candidate, registered)


@pytest.mark.parametrize(
    ("candidate", "registered"),
    [
        # A callback that forwards on a query parameter would hand the code on.
        (
            "https://claude.ai/api/mcp/auth_callback?next=https://evil.example",
            "https://claude.ai/api/mcp/auth_callback",
        ),
        # A registered root is not a registered prefix.
        ("https://app.example/anything", "https://app.example/"),
        (
            "https://claude.ai:8443/api/mcp/auth_callback",
            "https://claude.ai/api/mcp/auth_callback",
        ),
        ("http://localhost:1/elsewhere", "http://localhost/callback"),
        ("http://localhost:1/callback?x=1", "http://localhost/callback"),
        ("http://127.0.0.1:1/callback", "http://localhost/callback"),
        ("http://evil.example:1/callback", "http://evil.example/callback"),
        (
            "https://CLAUDE.ai/api/mcp/auth_callback",
            "https://claude.ai/api/mcp/auth_callback",
        ),
    ],
)
def test_anything_else_does_not_match(candidate, registered):
    assert not redirect_matches(candidate, registered)
