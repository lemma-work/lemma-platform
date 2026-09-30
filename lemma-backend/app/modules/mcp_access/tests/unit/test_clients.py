from __future__ import annotations

import base64
from datetime import datetime, timezone

import pytest
from mcp.server.auth.middleware.client_auth import AuthenticationError
from mcp.shared.auth import InvalidRedirectUriError, InvalidScopeError
from pydantic import AnyUrl
from starlette.requests import Request

from app.modules.mcp_access.domain.entities import ClientRegistration
from app.modules.mcp_access.domain.tokens import digest
from app.modules.mcp_access.infrastructure.repositories import StoredClient
from app.modules.mcp_access.services.client_auth import DigestClientAuthenticator
from app.modules.mcp_access.services.clients import LemmaOAuthClient, _from_stored

pytestmark = pytest.mark.unit

ISSUER = "https://api.lemma.work"
AUDIENCES = (ISSUER + "/oauth/token", ISSUER)


def _document_client(*redirects: str) -> LemmaOAuthClient:
    return _from_stored(
        StoredClient(
            client_id="https://claude.ai/oauth/claude-code-client-metadata",
            registration=ClientRegistration.METADATA_DOCUMENT.value,
            client_metadata={
                "client_name": "Claude Code",
                "redirect_uris": list(redirects),
                "grant_types": ["authorization_code"],
                "token_endpoint_auth_method": "none",
            },
            client_secret_hash=None,
            updated_at=datetime.now(timezone.utc),
        )
    )


def test_a_loopback_redirect_in_a_metadata_document_accepts_any_port():
    """Claude Code's document lists `http://localhost/callback` and then
    listens on whatever port is free (RFC 8252 §7.3)."""
    client = _document_client("http://localhost/callback", "http://127.0.0.1/callback")
    for uri in ("http://localhost:53172/callback", "http://127.0.0.1:1/callback"):
        assert str(client.validate_redirect_uri(AnyUrl(uri))) == uri


@pytest.mark.parametrize(
    "uri",
    [
        "http://localhost:53172/elsewhere",
        "http://localhost@evil.example/callback",
        "https://evil.example/callback",
        "http://localhost:53172/callback/../../steal",
    ],
)
def test_a_metadata_document_redirect_does_not_stretch_beyond_its_host_and_path(uri):
    client = _document_client("http://localhost/callback")
    with pytest.raises(InvalidRedirectUriError):
        client.validate_redirect_uri(AnyUrl(uri))


def test_a_registered_https_redirect_must_match_exactly():
    client = LemmaOAuthClient(
        client_id="abc",
        redirect_uris=[AnyUrl("https://claude.ai/api/mcp/auth_callback")],
    )
    assert client.validate_redirect_uri(None) == AnyUrl(
        "https://claude.ai/api/mcp/auth_callback"
    )
    with pytest.raises(InvalidRedirectUriError):
        client.validate_redirect_uri(
            AnyUrl("https://claude.ai:444/api/mcp/auth_callback")
        )


def test_scopes_are_checked_against_the_server_not_the_registration():
    client = LemmaOAuthClient(
        client_id="abc", redirect_uris=[AnyUrl("https://x.test/cb")]
    )
    assert client.validate_scope(None) is None
    assert client.validate_scope("pod:read offline_access") == ["pod:read"]
    with pytest.raises(InvalidScopeError):
        client.validate_scope("pod:read admin")


def test_a_client_that_did_not_list_refresh_can_still_refresh():
    assert "refresh_token" in _document_client("http://localhost/cb").grant_types


class _Directory:
    def __init__(self, client: LemmaOAuthClient) -> None:
        self.client = client

    async def get(self, client_id: str) -> LemmaOAuthClient | None:
        return self.client if client_id == self.client.client_id else None


def _request(form: str, headers: dict[str, str] | None = None) -> Request:
    body = form.encode()

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    raw_headers = [(b"content-type", b"application/x-www-form-urlencoded")]
    raw_headers += [
        (k.lower().encode(), v.encode()) for k, v in (headers or {}).items()
    ]
    return Request(
        {"type": "http", "method": "POST", "headers": raw_headers, "path": "/"},
        receive,
    )


@pytest.mark.asyncio
async def test_secrets_are_compared_by_digest_for_both_post_and_basic():
    client = LemmaOAuthClient(
        client_id="abc",
        redirect_uris=[AnyUrl("https://x.test/cb")],
        token_endpoint_auth_method="client_secret_post",
        client_secret_hash=digest("s3cret"),
    )
    authenticator = DigestClientAuthenticator(
        _Directory(client), assertion_audiences=AUDIENCES
    )  # type: ignore[arg-type]
    assert await authenticator.authenticate_request(
        _request("client_id=abc&client_secret=s3cret")
    )
    with pytest.raises(AuthenticationError):
        await authenticator.authenticate_request(
            _request("client_id=abc&client_secret=no")
        )

    basic = client.model_copy(
        update={"token_endpoint_auth_method": "client_secret_basic"}
    )
    authenticator = DigestClientAuthenticator(
        _Directory(basic), assertion_audiences=AUDIENCES
    )  # type: ignore[arg-type]
    header = "Basic " + base64.b64encode(b"abc:s3cret").decode()
    assert await authenticator.authenticate_request(
        _request("grant_type=refresh_token", {"Authorization": header})
    )


@pytest.mark.asyncio
async def test_a_public_client_needs_no_secret_and_an_unknown_one_is_refused():
    client = LemmaOAuthClient(
        client_id="pub",
        redirect_uris=[AnyUrl("http://localhost/cb")],
        token_endpoint_auth_method="none",
    )
    authenticator = DigestClientAuthenticator(
        _Directory(client), assertion_audiences=AUDIENCES
    )  # type: ignore[arg-type]
    assert await authenticator.authenticate_request(_request("client_id=pub"))
    with pytest.raises(AuthenticationError):
        await authenticator.authenticate_request(_request("client_id=other"))


# --- private_key_jwt, as ChatGPT's metadata document asks for ---------------

CHATGPT = "https://chatgpt.com/oauth/client.json"


class _Fetcher:
    """Serves one metadata document with its keys inline, as a fetch would."""

    def __init__(self, document) -> None:
        self.document = document

    def is_cimd_client_id(self, client_id: str) -> bool:
        return client_id == CHATGPT

    async def fetch(self, client_id: str):
        return self.document


def _signing_key():
    from joserfc import jwk

    return jwk.RSAKey.generate_key(2048, parameters={"kid": "k1"})


def _document(key):
    from fastmcp.server.auth.cimd import CIMDDocument

    return CIMDDocument.model_validate(
        {
            "client_id": CHATGPT,
            "client_name": "ChatGPT",
            "redirect_uris": ["https://chatgpt.com/connector_platform_oauth_redirect"],
            "token_endpoint_auth_method": "private_key_jwt",
            "jwks": {"keys": [key.as_dict(private=False)]},
        }
    )


def _assertion(key, *, audience: str, issuer: str = CHATGPT, jti: str = "j1") -> str:
    import time as _time

    from joserfc import jwt

    now = int(_time.time())
    return jwt.encode(
        {"alg": "RS256", "kid": "k1"},
        {
            "iss": issuer,
            "sub": issuer,
            "aud": audience,
            "iat": now,
            "exp": now + 60,
            "jti": jti,
        },
        key,
    )


class _Redis:
    """Just enough of Redis for claims and held registrations."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set(self, name: str, value: str, ex: int, nx: bool = False):
        if nx and name in self.values:
            return None
        self.values[name] = value
        return True

    async def get(self, name: str) -> str | None:
        return self.values.get(name)

    async def getdel(self, name: str) -> str | None:
        return self.values.pop(name, None)


def _directory(key, redis: "_Redis | None" = None):
    from app.modules.mcp_access.infrastructure.ephemeral import EphemeralStore
    from app.modules.mcp_access.services.clients import ClientDirectory

    return ClientDirectory(
        None,  # type: ignore[arg-type]  # these paths never open a unit of work
        ephemeral=EphemeralStore(redis or _Redis()),
        fetcher=_Fetcher(_document(key)),  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("audience", AUDIENCES)
async def test_a_signed_assertion_is_accepted_with_either_audience(audience):
    key = _signing_key()
    await _directory(key).verify_assertion(
        client_id=CHATGPT,
        assertion=_assertion(key, audience=audience),
        audiences=AUDIENCES,
    )


@pytest.mark.asyncio
async def test_an_assertion_is_refused_when_replayed_or_signed_for_someone_else():
    key = _signing_key()
    directory = _directory(key)
    once = _assertion(key, audience=AUDIENCES[0], jti="only-once")
    await directory.verify_assertion(
        client_id=CHATGPT, assertion=once, audiences=AUDIENCES
    )
    with pytest.raises(ValueError):
        await directory.verify_assertion(
            client_id=CHATGPT, assertion=once, audiences=AUDIENCES
        )
    with pytest.raises(ValueError):
        await directory.verify_assertion(
            client_id=CHATGPT,
            assertion=_assertion(
                key,
                audience=AUDIENCES[0],
                issuer="https://evil.example/c.json",
                jti="j2",
            ),
            audiences=AUDIENCES,
        )
    with pytest.raises(ValueError):
        await _directory(_signing_key()).verify_assertion(
            client_id=CHATGPT,
            assertion=_assertion(key, audience=AUDIENCES[0], jti="j3"),
            audiences=AUDIENCES,
        )


@pytest.mark.asyncio
async def test_a_registered_client_cannot_claim_private_key_jwt():
    client = LemmaOAuthClient(
        client_id="dcr",
        redirect_uris=[AnyUrl("https://x.test/cb")],
        token_endpoint_auth_method="private_key_jwt",
    )
    authenticator = DigestClientAuthenticator(
        _Directory(client), assertion_audiences=AUDIENCES
    )  # type: ignore[arg-type]
    with pytest.raises(AuthenticationError):
        await authenticator.authenticate_request(
            _request(
                "client_id=dcr&client_assertion_type="
                "urn%3Aietf%3Aparams%3Aoauth%3Aclient-assertion-type%3Ajwt-bearer"
                "&client_assertion=x"
            )
        )


@pytest.mark.asyncio
async def test_an_assertion_used_on_one_replica_is_refused_on_another():
    """fastmcp remembers assertion ids per process; the claim in Redis is what
    two replicas share."""
    key = _signing_key()
    shared = _Redis()
    assertion = _assertion(key, audience=AUDIENCES[0], jti="across-replicas")
    await _directory(key, shared).verify_assertion(
        client_id=CHATGPT, assertion=assertion, audiences=AUDIENCES
    )
    with pytest.raises(ValueError, match="already used"):
        await _directory(key, shared).verify_assertion(
            client_id=CHATGPT, assertion=assertion, audiences=AUDIENCES
        )
