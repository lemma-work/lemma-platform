"""Minting a token that acts as the GitHub App.

The two hops that matter: a JWT GitHub will accept, and an installation token
cached long enough to be worth caching but not long enough to be presented
dead.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.modules.connectors.services.auth import github_app

pytestmark = pytest.mark.asyncio


@pytest.fixture
def app_key(monkeypatch) -> str:
    """A throwaway RSA key, generated here rather than committed.

    A PEM in the repository is a secret-shaped string that scanners cannot
    distinguish from a real leak, and this test needs a key only to prove the
    signature verifies.
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()

    github_app._signing_key.cache_clear()
    monkeypatch.setattr(
        github_app.connector_settings,
        "connector_github_app_private_key",
        type("S", (), {"get_secret_value": lambda self: pem})(),
        raising=False,
    )
    monkeypatch.setenv("CONNECTOR_GITHUB_CLIENT_ID", "Iv1.testclientid")
    yield pem
    github_app._signing_key.cache_clear()


def _public_pem(private_pem: str) -> str:
    key = serialization.load_pem_private_key(private_pem.encode(), password=None)
    return (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )


async def test_the_jwt_is_one_github_will_accept(app_key):
    """Three things GitHub checks and rejects on: the algorithm, a lifetime
    inside ten minutes, and an `iat` that is not in its future."""
    token = github_app.app_jwt()

    claims = jwt.decode(token, _public_pem(app_key), algorithms=["RS256"])

    assert claims["iss"] == "Iv1.testclientid"
    assert claims["exp"] - claims["iat"] <= 10 * 60, "GitHub refuses a longer one"
    # Backdated, because a clock a few seconds fast is the ordinary way to be
    # refused for an `iat` in the future.
    assert claims["iat"] < datetime.now(timezone.utc).timestamp()


async def test_no_private_key_is_a_fallback_not_a_failure(monkeypatch):
    """An install with no key configured is a perfectly good user-token
    install. The caller falls back; it must not look like breakage.

    The OAuth half IS configured here, because that is the real shape of this:
    a deployment that can connect a GitHub account and simply cannot act as the
    App. With neither set the client-id check fires first and this would be
    asserting on the wrong absence.
    """
    monkeypatch.setenv("CONNECTOR_GITHUB_CLIENT_ID", "Iv1.testclientid")
    github_app._signing_key.cache_clear()
    monkeypatch.setattr(
        github_app.connector_settings,
        "connector_github_app_private_key",
        None,
        raising=False,
    )
    monkeypatch.setattr(
        github_app.connector_settings,
        "connector_github_app_private_key_path",
        None,
        raising=False,
    )

    with pytest.raises(github_app.GitHubAppUnavailable, match="private key"):
        github_app.app_jwt()


async def test_an_escaped_newline_key_is_restored(monkeypatch, app_key):
    """A PEM pasted into an env file arrives with literal backslash-n. Signing
    with it unrepaired fails inside the crypto library, far from the cause."""
    github_app._signing_key.cache_clear()
    monkeypatch.setattr(
        github_app.connector_settings,
        "connector_github_app_private_key",
        type(
            "S", (), {"get_secret_value": lambda self: app_key.replace("\n", "\\n")}
        )(),
        raising=False,
    )

    jwt.decode(github_app.app_jwt(), _public_pem(app_key), algorithms=["RS256"])


def _minting_client(captured: list[httpx.Request], *, expires_in_seconds=3600):
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds)
        return httpx.Response(
            201,
            json={
                "token": "ghs_installation_token",
                "expires_at": expiry.isoformat().replace("+00:00", "Z"),
            },
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_minting_presents_the_jwt_and_returns_the_token(app_key):
    captured: list[httpx.Request] = []
    async with _minting_client(captured) as client:
        minted = await github_app._mint("158040062", client=client)

    assert minted.token == "ghs_installation_token"
    assert captured[0].url.path == "/app/installations/158040062/access_tokens"
    assert captured[0].headers["Authorization"].startswith("Bearer ey")


async def test_a_refused_mint_says_so_rather_than_returning_nothing(app_key):
    """The commonest cause is an App uninstalled since the id was stored, so
    the caller needs to distinguish it from a token it can use."""

    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(refuse)) as client:
        with pytest.raises(github_app.GitHubAppUnavailable, match="404"):
            await github_app._mint("158040062", client=client)


async def test_a_missing_expiry_still_caches_for_the_documented_hour(app_key):
    """Reading the expiry as "now" would compute a negative TTL and quietly
    defeat the cache -- every call would mint again."""

    def no_expiry(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"token": "ghs_x"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(no_expiry)) as client:
        minted = await github_app._mint("1", client=client)

    remaining = (minted.expires_at - datetime.now(timezone.utc)).total_seconds()
    assert remaining > github_app._TOKEN_EXPIRY_SKEW_SECONDS


def test_the_cache_drops_a_token_before_it_expires():
    """A token handed out at the last second expires mid-call. The skew is what
    stops an operation starting with one that will not survive it."""
    assert github_app._TOKEN_EXPIRY_SKEW_SECONDS > 0
    assert (
        github_app._token_cache()._ttl_seconds
        == 3600 - github_app._TOKEN_EXPIRY_SKEW_SECONDS
    )
