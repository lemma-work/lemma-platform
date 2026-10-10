from __future__ import annotations

from uuid import UUID

import time
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from mcp.server.auth.provider import AuthorizationParams, AuthorizeError, TokenError
from pydantic import AnyUrl

from app.modules.mcp_access.domain.resources import pod_resource_url
from app.modules.mcp_access.infrastructure.ephemeral import EphemeralStore, IssuedCode
from app.modules.mcp_access.services.authorization_server import (
    LemmaAuthorizationServer,
)
from app.modules.mcp_access.services.clients import LemmaOAuthClient

pytestmark = pytest.mark.unit

API = "https://api.lemma.work"
CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set(self, name: str, value: str, ex: int) -> None:
        self.values[name] = value

    async def get(self, name: str) -> str | None:
        return self.values.get(name)

    async def getdel(self, name: str) -> str | None:
        return self.values.pop(name, None)


async def _yes(_: UUID) -> bool:
    return True


def _server(redis: _FakeRedis) -> LemmaAuthorizationServer:
    return LemmaAuthorizationServer(
        uow_factory=None,  # type: ignore[arg-type]  # these paths never open one
        clients=None,  # type: ignore[arg-type]
        ephemeral=EphemeralStore(redis),
        api_url=API,
        auth_frontend_url="https://lemma.work",
        account_may_sign_in=_yes,
        pod_is_live=_yes,
    )


def _client() -> LemmaOAuthClient:
    return LemmaOAuthClient(
        client_id="client-1",
        redirect_uris=[AnyUrl("https://claude.ai/api/mcp/auth_callback")],
    )


def _params(
    resource: str | None, scopes: list[str] | None = None
) -> AuthorizationParams:
    return AuthorizationParams(
        state="st",
        scopes=scopes,
        code_challenge=CHALLENGE,
        redirect_uri=AnyUrl("https://claude.ai/api/mcp/auth_callback"),
        redirect_uri_provided_explicitly=True,
        resource=resource,
    )


@pytest.mark.asyncio
async def test_authorize_holds_the_request_and_sends_the_browser_to_consent():
    redis = _FakeRedis()
    pod_id = uuid4()
    url = await _server(redis).authorize(
        _client(), _params(pod_resource_url(API, pod_id))
    )
    parts = urlsplit(url)
    assert (
        f"{parts.scheme}://{parts.netloc}{parts.path}"
        == "https://lemma.work/auth/authorize"
    )
    request_id = parse_qs(parts.query)["request"][0]
    held = await EphemeralStore(redis).read_pending(request_id)
    assert held is not None
    assert held.pod_id == str(pod_id)
    # Nothing asked for means everything the server offers, for the person to
    # see on the consent screen.
    assert held.scopes == ["pod:read", "pod:write", "pod:events"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("code_challenge", "short"),
        ("code_challenge", "x" * 2_000_000),
        ("code_challenge", "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw+cM"),
        ("state", "s" * 2_049),
    ],
    # Named, never derived from the values: pytest makes a string parameter
    # the test's id, and a two-million-character id stalled CI's unit job for
    # its whole half hour while it wrote the verbose log line and the report.
    ids=["short-challenge", "huge-challenge", "not-base64url", "long-state"],
)
async def test_authorize_holds_nothing_oversized_or_malformed(field, value):
    """The request waits in Redis until the person answers, so what it carries
    is bounded before it is held."""
    redis = _FakeRedis()
    params = _params(pod_resource_url(API, uuid4())).model_copy(update={field: value})
    with pytest.raises(AuthorizeError) as raised:
        await _server(redis).authorize(_client(), params)
    assert raised.value.error == "invalid_request"
    assert redis.values == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", [None, "https://evil.example/mcp/x"])
async def test_authorize_refuses_a_request_that_names_no_pod_here(resource):
    with pytest.raises(AuthorizeError) as raised:
        await _server(_FakeRedis()).authorize(_client(), _params(resource))
    assert raised.value.error == "invalid_target"


@pytest.mark.asyncio
async def test_a_code_belongs_to_the_client_it_was_issued_to_and_redeems_once():
    redis = _FakeRedis()
    store = EphemeralStore(redis)
    code = await store.issue_code(
        IssuedCode(
            grant_id=str(uuid4()),
            client_id="client-1",
            scopes=["pod:read"],
            code_challenge=CHALLENGE,
            redirect_uri="https://claude.ai/api/mcp/auth_callback",
            redirect_uri_provided_explicitly=True,
            resource=pod_resource_url(API, uuid4()),
            expires_at=time.time() + 60,
        )
    )
    server = _server(redis)
    stranger = LemmaOAuthClient(
        client_id="client-2", redirect_uris=[AnyUrl("https://x.test/")]
    )
    assert await server.load_authorization_code(stranger, code) is None

    loaded = await server.load_authorization_code(_client(), code)
    assert loaded is not None
    await store.take_code(code)  # a concurrent redemption got there first
    with pytest.raises(TokenError) as raised:
        await server.exchange_authorization_code(_client(), loaded)
    assert raised.value.error == "invalid_grant"


@pytest.mark.parametrize(
    "auth_frontend_url",
    [
        # The portal itself: what deployments set, and what the CLI uses.
        "https://app.example.com/auth",
        "https://app.example.com/auth/",
        # The bare site: what the configuration guide shows.
        "https://app.example.com",
        "https://app.example.com/",
    ],
)
def test_the_consent_page_is_on_the_portal_however_its_url_is_given(auth_frontend_url):
    """A deployment that sets the portal URL got /auth/auth/authorize -- a 404."""
    from app.modules.mcp_access.services.authorization_server import consent_page_url

    assert (
        consent_page_url(auth_frontend_url, "r1")
        == "https://app.example.com/auth/authorize?request=r1"
    )
