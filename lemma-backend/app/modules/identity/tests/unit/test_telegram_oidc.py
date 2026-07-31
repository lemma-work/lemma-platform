from __future__ import annotations

import json
import time
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import SecretStr
from jwt.algorithms import RSAAlgorithm

from app.core.config import settings
from app.modules.identity.services import telegram_oidc
from app.modules.identity.services.telegram_oidc import (
    TelegramOIDCError,
    TelegramOIDCService,
    TelegramTransaction,
    normalize_e164,
    safe_return_to,
)


class _FakeRedis:
    def __init__(self):
        self.values: dict[str, str] = {}

    async def set(self, key, value, *, ex=None, nx=False):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def getdel(self, key):
        return self.values.pop(key, None)

    async def get(self, key):
        return self.values.get(key)


def _service() -> TelegramOIDCService:
    service = TelegramOIDCService.__new__(TelegramOIDCService)
    service._redis = _FakeRedis()
    return service


def test_normalize_e164_rejects_ambiguous_numbers():
    assert normalize_e164("+1 (415) 555-2671") == "+14155552671"
    with pytest.raises(TelegramOIDCError):
        normalize_e164("020 1234 5678")
    with pytest.raises(TelegramOIDCError):
        normalize_e164("123")


def test_safe_return_to_allows_only_lemma_origins(monkeypatch):
    monkeypatch.setattr(settings, "auth_frontend_url", "https://auth.lemma.test")
    monkeypatch.setattr(settings, "frontend_url", "https://app.lemma.test")

    assert safe_return_to("/auth/done") == "https://auth.lemma.test/auth/done"
    assert safe_return_to("https://app.lemma.test/profile") == (
        "https://app.lemma.test/profile"
    )
    assert safe_return_to("https://evil.example/steal") == "https://auth.lemma.test/"
    assert safe_return_to("//evil.example/steal") == "https://auth.lemma.test/"


@pytest.mark.asyncio
async def test_oidc_transaction_is_pkce_backed_bound_and_single_use(monkeypatch):
    monkeypatch.setattr(settings, "telegram_oidc_client_id", "telegram-client")
    monkeypatch.setattr(
        settings, "telegram_oidc_client_secret", SecretStr("telegram-secret")
    )
    monkeypatch.setattr(
        settings,
        "telegram_oidc_redirect_uri",
        "https://api.lemma.test/auth/telegram/callback",
    )
    monkeypatch.setattr(settings, "auth_frontend_url", "https://auth.lemma.test")
    monkeypatch.setattr(settings, "frontend_url", "https://app.lemma.test")
    service = _service()
    user_id = uuid4()

    authorization_url = await service.start(
        purpose="verify_mobile",
        return_to="https://app.lemma.test/profile",
        user_id=user_id,
    )
    query = parse_qs(urlparse(authorization_url).query)
    assert query["scope"] == ["openid profile phone"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["nonce"]
    assert query["code_challenge"]

    transaction = await service.consume(query["state"][0])
    assert transaction.purpose == "verify_mobile"
    assert transaction.user_id == str(user_id)
    assert transaction.return_to == "https://app.lemma.test/profile"
    with pytest.raises(TelegramOIDCError, match="already used"):
        await service.consume(query["state"][0])


@pytest.mark.asyncio
async def test_oidc_id_token_validates_signature_nonce_and_verified_phone(monkeypatch):
    monkeypatch.setattr(settings, "telegram_oidc_client_id", "telegram-client")
    monkeypatch.setattr(
        settings, "telegram_oidc_client_secret", SecretStr("telegram-secret")
    )
    monkeypatch.setattr(
        settings,
        "telegram_oidc_redirect_uri",
        "https://api.lemma.test/auth/telegram/callback",
    )
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    public_jwk["kid"] = "telegram-test-key"
    now = int(time.time())

    def make_token(*, nonce="expected-nonce", phone_verified=True):
        return jwt.encode(
            {
                "iss": settings.telegram_oidc_issuer,
                "aud": "telegram-client",
                "sub": "telegram-user-1",
                "iat": now,
                "exp": now + 300,
                "nonce": nonce,
                "phone_number": "+14155552671",
                "phone_number_verified": phone_verified,
            },
            private_key,
            algorithm="RS256",
            headers={"kid": "telegram-test-key"},
        )

    class _Response:
        def __init__(self, payload):
            self.status_code = 200
            self._payload = payload

        def json(self):
            return self._payload

    class _Client:
        token = make_token()

        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return _Response({"id_token": self.token})

        async def get(self, *_args, **_kwargs):
            return _Response({"keys": [public_jwk]})

    monkeypatch.setattr(telegram_oidc.httpx, "AsyncClient", _Client)
    service = _service()
    transaction = TelegramTransaction(
        state="state",
        nonce="expected-nonce",
        code_verifier="verifier",
        purpose="signin",
        return_to="https://auth.lemma.test/",
        user_id=None,
    )

    claims = await service.exchange_and_validate(code="code", transaction=transaction)
    assert claims["phone_number"] == "+14155552671"

    _Client.token = make_token(nonce="altered")
    with pytest.raises(TelegramOIDCError, match="nonce"):
        await service.exchange_and_validate(code="code", transaction=transaction)

    _Client.token = make_token(phone_verified=False)
    with pytest.raises(TelegramOIDCError, match="verify the mobile"):
        await service.exchange_and_validate(code="code", transaction=transaction)

    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    _Client.token = jwt.encode(
        {
            "iss": settings.telegram_oidc_issuer,
            "aud": "telegram-client",
            "sub": "telegram-user-1",
            "iat": now,
            "exp": now + 300,
            "nonce": "expected-nonce",
            "phone_number": "+14155552671",
            "phone_number_verified": True,
        },
        other_key,
        algorithm="RS256",
        headers={"kid": "telegram-test-key"},
    )
    with pytest.raises(TelegramOIDCError, match="identity token is invalid"):
        await service.exchange_and_validate(code="code", transaction=transaction)
