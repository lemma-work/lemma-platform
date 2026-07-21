from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient, Response
import pytest
from pydantic import SecretStr
from sqlalchemy import select, update
from supertokens_python.recipe.thirdparty.providers import config_utils
from supertokens_python.recipe.thirdparty.providers.custom import GenericProvider
from supertokens_python.recipe.thirdparty.types import (
    RawUserInfoFromProvider,
    UserInfo,
    UserInfoEmail,
)

from app.modules.identity.infrastructure.models.organization_models import (
    OrganizationInvitation,
)
from app.modules.identity.infrastructure.models.user_models import User
from app.core.infrastructure.events.models import DomainEventOutbox
from app.core.infrastructure.db.session import async_session_maker
from app.modules.identity.domain.events import UserSignedUpEvent
from app.modules.identity.events.handlers import _dispatch_identity_event
from app.modules.identity.infrastructure.adapters.email_adapter import (
    SmtpIdentityEmailAdapter,
)
from app.modules.identity.services.telegram_oidc import (
    TelegramOIDCError,
    TelegramOIDCService,
)
from app.modules.identity.services import telegram_oidc
from app.modules.identity.services import email_policy
from app.modules.identity.services.auth_abuse import get_auth_abuse_store
from app.core.config import settings
from app.modules.test_support.e2e_base import verify_emailpassword_for_tests

pytestmark = pytest.mark.e2e


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _emailpassword_payload(email: str, password: str) -> dict:
    return {
        "formFields": [
            {"id": "email", "value": email},
            {"id": "password", "value": password},
        ]
    }


def _filesystem_emails(output_dir: str, recipient: str) -> list[dict]:
    messages: list[dict] = []
    for path in sorted(Path(output_dir).glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except OSError, json.JSONDecodeError:
            continue
        if payload.get("to_email") == recipient:
            messages.append(payload)
    return messages


async def _wait_for_email(
    output_dir: str,
    recipient: str,
    subject: str,
    *,
    timeout_seconds: float = 10,
) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        matches = [
            message
            for message in _filesystem_emails(output_dir, recipient)
            if message.get("subject") == subject
        ]
        if matches:
            return matches[-1]
        await asyncio.sleep(0.1)
    raise AssertionError(f"Timed out waiting for {subject!r} filesystem email")


def _email_link(message: dict) -> str:
    text = str(message.get("text_content") or "")
    link = next((word for word in text.split() if "token=" in word), "")
    assert link, message
    return link


def _email_link_token(message: dict) -> str:
    token = parse_qs(urlparse(_email_link(message)).query).get("token", [])
    assert token, message
    return token[0]


def _solve_altcha(challenge: dict) -> str:
    number = next(
        candidate
        for candidate in range(challenge["maxnumber"] + 1)
        if hashlib.sha256(f"{challenge['salt']}{candidate}".encode()).hexdigest()
        == challenge["challenge"]
    )
    payload = {
        "algorithm": challenge["algorithm"],
        "challenge": challenge["challenge"],
        "number": number,
        "salt": challenge["salt"],
        "signature": challenge["signature"],
    }
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")


@pytest.fixture
def mock_google_provider(monkeypatch):
    async def _discover_oidc_endpoints(config):
        return config

    async def _get_user_info(self, oauth_tokens, user_context):
        email = oauth_tokens["testEmail"]
        third_party_user_id = oauth_tokens["testThirdPartyUserId"]
        return UserInfo(
            third_party_user_id=third_party_user_id,
            email=UserInfoEmail(email, True),
            raw_user_info_from_provider=RawUserInfoFromProvider(
                from_id_token_payload={
                    "sub": third_party_user_id,
                    "email": email,
                    "email_verified": True,
                },
                from_user_info_api=None,
            ),
        )

    monkeypatch.setattr(
        config_utils, "discover_oidc_endpoints", _discover_oidc_endpoints
    )
    monkeypatch.setattr(GenericProvider, "get_user_info", _get_user_info)


@pytest.fixture
def google_signinup(async_client: AsyncClient):
    async def _google_signinup(
        email: str,
        *,
        third_party_user_id: str = "google-user-1",
    ):
        return await async_client.post(
            "/st/auth/signinup",
            json={
                "thirdPartyId": "google",
                "oAuthTokens": {
                    "testEmail": email,
                    "testThirdPartyUserId": third_party_user_id,
                },
            },
        )

    return _google_signinup


@pytest.mark.asyncio
async def test_user_me_and_profile_lifecycle(
    async_client: AsyncClient,
    signup_user,
):
    owner = await signup_user()
    headers = _auth_headers(owner["token"])

    me_resp = await async_client.get("/users/me", headers=headers)
    assert me_resp.status_code == 200
    me_data = me_resp.json()
    assert me_data["email"] == owner["email"]

    profile_before_resp = await async_client.get("/users/me/profile", headers=headers)
    assert profile_before_resp.status_code == 200

    profile_payload = {
        "first_name": "Anukul",
        "last_name": "Test",
        "mobile_number": "+1234567890",
        "country": "US",
        "timezone": "UTC",
        "date_of_birth": "1990-01-01",
    }
    upsert_resp = await async_client.post(
        "/users/me/profile",
        headers=headers,
        json=profile_payload,
    )
    assert upsert_resp.status_code == 201

    profile_after_resp = await async_client.get("/users/me/profile", headers=headers)
    assert profile_after_resp.status_code == 200
    profile_data = profile_after_resp.json()
    assert profile_data["first_name"] == "Anukul"
    assert profile_data["last_name"] == "Test"


@pytest.mark.asyncio
async def test_signup_does_not_create_personal_org(
    async_client: AsyncClient,
    signup_user,
):
    user = await signup_user(email=f"test+no-personal-{uuid4().hex[:8]}@gmail.com")
    headers = _auth_headers(user["token"])

    list_org_resp = await async_client.get("/organizations", headers=headers)
    assert list_org_resp.status_code == 200
    assert list_org_resp.json()["items"] == []


@pytest.mark.asyncio
async def test_desktop_browser_handoff_creates_cookie_session(
    async_client: AsyncClient,
    signup_user,
    test_app,
):
    user = await signup_user(email=f"desktop-handoff-{uuid4().hex[:8]}@example.com")
    verifier = "desktop-verifier-" + uuid4().hex + uuid4().hex
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("utf-8")).digest())
        .decode("ascii")
        .rstrip("=")
    )

    create_response = await async_client.post(
        "/auth/desktop/requests",
        json={"code_challenge": challenge},
    )
    assert create_response.status_code == 200, create_response.text
    request_id = create_response.json()["request_id"]

    complete_response = await async_client.post(
        f"/auth/desktop/requests/{request_id}/complete",
        headers=_auth_headers(user["token"]),
    )
    assert complete_response.status_code == 200, complete_response.text

    retry_complete_response = await async_client.post(
        f"/auth/desktop/requests/{request_id}/complete",
        headers=_auth_headers(user["token"]),
    )
    assert retry_complete_response.status_code == 200, retry_complete_response.text

    replacement_user = await signup_user(
        email=f"desktop-handoff-replacement-{uuid4().hex[:8]}@example.com"
    )
    replacement_response = await async_client.post(
        f"/auth/desktop/requests/{request_id}/complete",
        headers=_auth_headers(replacement_user["token"]),
    )
    assert replacement_response.status_code == 409, replacement_response.text

    async with AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://test",
    ) as webview_client:
        exchange_response = await webview_client.post(
            "/auth/desktop/session",
            headers={"st-auth-mode": "cookie"},
            json={
                "request_id": request_id,
                "code_verifier": verifier,
            },
        )
        assert exchange_response.status_code == 200, exchange_response.text
        assert webview_client.cookies.get("sAccessToken")
        assert webview_client.cookies.get("sRefreshToken")

        me_response = await webview_client.get("/users/me")
        assert me_response.status_code == 200, me_response.text
        assert me_response.json()["email"] == user["email"]


@pytest.mark.asyncio
async def test_emailpassword_signup_and_signin_normalize_email(
    async_client: AsyncClient,
):
    unique = uuid4().hex[:10]
    signup_email = f"TEST+EMAIL-CASE-{unique}@EXAMPLE.COM"
    signin_email = f"Test+Email-Case-{unique}@Example.Com"
    normalized_email = signup_email.lower()
    password = "TestPassword@123"

    signup_response = await async_client.post(
        "/st/auth/signup",
        json=_emailpassword_payload(signup_email, password),
    )
    signup_payload = signup_response.json()
    assert signup_response.status_code == 200
    assert signup_payload["status"] == "OK", signup_payload
    assert signup_payload["user"]["emails"] == [normalized_email]

    signup_token = signup_response.headers.get(
        "st-access-token"
    ) or signup_response.cookies.get("sAccessToken")
    assert signup_token

    unverified_me_response = await async_client.get(
        "/users/me",
        headers=_auth_headers(signup_token),
    )
    assert unverified_me_response.status_code in {401, 403}

    await verify_emailpassword_for_tests(signup_payload["user"]["id"], normalized_email)

    async_client.cookies.clear()
    verified_signin_response = await async_client.post(
        "/st/auth/signin",
        json=_emailpassword_payload(signin_email, password),
    )
    verified_token = verified_signin_response.headers.get(
        "st-access-token"
    ) or verified_signin_response.cookies.get("sAccessToken")
    assert verified_signin_response.status_code == 200
    assert verified_token

    me_response = await async_client.get(
        "/users/me",
        headers=_auth_headers(verified_token),
    )
    assert me_response.status_code == 200, me_response.text
    assert me_response.json()["email"] == normalized_email

    async_client.cookies.clear()
    signin_response = await async_client.post(
        "/st/auth/signin",
        json=_emailpassword_payload(signin_email, password),
    )
    signin_payload = signin_response.json()
    assert signin_response.status_code == 200
    assert signin_payload["status"] == "OK", signin_payload
    assert signin_payload["user"]["id"] == signup_payload["user"]["id"]
    assert signin_payload["user"]["emails"] == [normalized_email]


@pytest.mark.asyncio
async def test_disposable_email_is_rejected_before_account_creation(
    async_client: AsyncClient,
):
    email = f"blocked-{uuid4().hex[:10]}@mailinator.com"
    response = await async_client.post(
        "/st/auth/signup",
        json=_emailpassword_payload(email, "TestPassword@123"),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "SIGN_UP_NOT_ALLOWED"

    async with async_session_maker() as session:
        local_user = await session.scalar(select(User).where(User.email == email))
    assert local_user is None


@pytest.mark.asyncio
async def test_domain_without_explicit_mx_is_rejected_before_account_creation(
    async_client: AsyncClient,
    monkeypatch,
):
    email = f"blocked-{uuid4().hex[:10]}@legacy.example"
    monkeypatch.setattr(settings, "auth_email_deliverability_checks_enabled", True)
    monkeypatch.setattr(settings, "auth_disposable_email_domains_enabled", False)

    async def a_record_fallback(_email):
        return SimpleNamespace(normalized=email, mx_fallback_type="A")

    monkeypatch.setattr(email_policy, "_validate_with_dns", a_record_fallback)
    response = await async_client.post(
        "/st/auth/signup",
        json=_emailpassword_payload(email, "TestPassword@123"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "SIGN_UP_NOT_ALLOWED",
        "reason": "Please use a valid, non-disposable email address",
    }
    async with async_session_maker() as session:
        local_user = await session.scalar(select(User).where(User.email == email))
    assert local_user is None


@pytest.mark.asyncio
async def test_emailpassword_verification_welcome_and_password_reset_filesystem_flow(
    async_client: AsyncClient,
    e2e_settings,
):
    email = f"realistic-auth-{uuid4().hex[:10]}@example.com"
    original_password = "OriginalPassword@123"
    replacement_password = "ReplacementPassword@123"
    signup_body = _emailpassword_payload(email, original_password)

    signup = await async_client.post("/st/auth/signup", json=signup_body)
    signup_payload = signup.json()
    assert signup.status_code == 200
    assert signup_payload["status"] == "OK"
    signup_token = signup.headers.get("st-access-token") or signup.cookies.get(
        "sAccessToken"
    )
    assert signup_token
    auth_headers = _auth_headers(signup_token)

    # Signup alone must neither grant application access nor enqueue a welcome.
    blocked = await async_client.get("/users/me", headers=auth_headers)
    assert blocked.status_code == 403
    assert blocked.json()["claimValidationErrors"][0]["id"] == "st-ev"
    assert _filesystem_emails(e2e_settings.email_output_dir, email) == []

    send_verification = await async_client.post(
        "/st/auth/user/email/verify/token",
        headers=auth_headers,
    )
    assert send_verification.status_code == 200
    assert send_verification.json()["status"] == "OK"
    verification_message = await _wait_for_email(
        e2e_settings.email_output_dir,
        email,
        "Verify your Lemma email",
    )
    assert "Verify email" in verification_message["html_content"]
    verification_link = urlparse(_email_link(verification_message))
    assert verification_link.path == "/auth/verify-email"
    assert parse_qs(verification_link.query)["tenantId"] == ["public"]
    assert "Button not working?" in verification_message["html_content"]
    assert "Account security" in verification_message["text_content"]
    verification_token = _email_link_token(verification_message)

    verify = await async_client.post(
        "/st/auth/user/email/verify",
        headers=auth_headers,
        json={"token": verification_token},
    )
    assert verify.status_code == 200
    assert verify.json()["status"] == "OK"

    consumed_verification = await async_client.post(
        "/st/auth/user/email/verify",
        json={"token": verification_token},
    )
    assert consumed_verification.status_code == 200
    assert (
        consumed_verification.json()["status"]
        == "EMAIL_VERIFICATION_INVALID_TOKEN_ERROR"
    )

    # Verification creates one durable welcome event. Deliver it through the
    # production event handler and filesystem email adapter; the platform's
    # generic outbox/Redis worker boundary has its own container E2E coverage.
    async with async_session_maker() as session:
        welcome_events = list(
            (
                await session.scalars(
                    select(DomainEventOutbox).where(
                        DomainEventOutbox.event_type
                        == UserSignedUpEvent.get_event_type()
                    )
                )
            ).all()
        )
    assert len(welcome_events) == 1
    assert welcome_events[0].payload["email"] == email
    await _dispatch_identity_event(
        welcome_events[0].payload,
        logging.getLogger("identity-e2e"),
        SmtpIdentityEmailAdapter(),
    )

    welcome = await _wait_for_email(
        e2e_settings.email_output_dir,
        email,
        "Welcome to Lemma",
    )
    assert "Your account is ready" in welcome["text_content"]
    assert (
        len(
            [
                message
                for message in _filesystem_emails(e2e_settings.email_output_dir, email)
                if message["subject"] == "Welcome to Lemma"
            ]
        )
        == 1
    )

    # A fresh sign-in now creates an application-eligible session.
    async_client.cookies.clear()
    verified_signin = await async_client.post("/st/auth/signin", json=signup_body)
    verified_token = verified_signin.headers.get(
        "st-access-token"
    ) or verified_signin.cookies.get("sAccessToken")
    assert verified_signin.json()["status"] == "OK"
    assert verified_token
    me = await async_client.get("/users/me", headers=_auth_headers(verified_token))
    assert me.status_code == 200
    assert me.json()["email"] == email

    request_reset = await async_client.post(
        "/st/auth/user/password/reset/token",
        json={"formFields": [{"id": "email", "value": email}]},
    )
    assert request_reset.status_code == 200
    assert request_reset.json()["status"] == "OK"
    reset_message = await _wait_for_email(
        e2e_settings.email_output_dir,
        email,
        "Reset your Lemma password",
    )
    assert "Reset password" in reset_message["html_content"]
    reset_link = urlparse(_email_link(reset_message))
    assert reset_link.path == "/auth/reset-password"
    assert parse_qs(reset_link.query)["tenantId"] == ["public"]
    assert "Button not working?" in reset_message["html_content"]
    assert "Account security" in reset_message["text_content"]
    reset_token = _email_link_token(reset_message)

    reset = await async_client.post(
        "/st/auth/user/password/reset",
        json={
            "token": reset_token,
            "formFields": [{"id": "password", "value": replacement_password}],
        },
    )
    assert reset.status_code == 200
    assert reset.json()["status"] == "OK"
    consumed_reset = await async_client.post(
        "/st/auth/user/password/reset",
        json={
            "token": reset_token,
            "formFields": [{"id": "password", "value": replacement_password}],
        },
    )
    assert consumed_reset.status_code == 200
    assert consumed_reset.json()["status"] == "RESET_PASSWORD_INVALID_TOKEN_ERROR"

    old_password = await async_client.post("/st/auth/signin", json=signup_body)
    assert old_password.json()["status"] == "WRONG_CREDENTIALS_ERROR"
    new_password = await async_client.post(
        "/st/auth/signin",
        json=_emailpassword_payload(email, replacement_password),
    )
    assert new_password.json()["status"] == "OK"


@pytest.mark.asyncio
async def test_signup_altcha_replay_tampering_and_ip_rate_limit(
    async_client: AsyncClient,
    monkeypatch,
):
    monkeypatch.setattr(settings, "auth_abuse_protection_enabled", True)
    monkeypatch.setattr(settings, "auth_altcha_enabled", True)
    monkeypatch.setattr(settings, "auth_altcha_hmac_key", SecretStr("e2e-altcha-key"))
    monkeypatch.setattr(settings, "auth_altcha_max_number", 100)

    async def challenge() -> dict:
        response = await async_client.get(
            "/auth/altcha/challenge", params={"purpose": "signup"}
        )
        assert response.status_code == 200
        assert response.json()["enabled"] is True
        return response.json()

    first_challenge = await challenge()
    first_proof = _solve_altcha(first_challenge)
    first_email = f"altcha-first-{uuid4().hex[:8]}@example.com"
    first = await async_client.post(
        "/st/auth/signup",
        headers={"x-altcha-payload": first_proof},
        json=_emailpassword_payload(first_email, "TestPassword@123"),
    )
    assert first.status_code == 200
    assert first.json()["status"] == "OK"

    async_client.cookies.clear()
    replay = await async_client.post(
        "/st/auth/signup",
        headers={"x-altcha-payload": first_proof},
        json=_emailpassword_payload(
            f"altcha-replay-{uuid4().hex[:8]}@example.com", "TestPassword@123"
        ),
    )
    assert replay.status_code == 400
    assert "already used" in replay.text

    tampered_challenge = await challenge()
    solved_proof = _solve_altcha(tampered_challenge)
    tampered_payload = json.loads(
        base64.urlsafe_b64decode(solved_proof + "=" * (-len(solved_proof) % 4))
    )
    tampered_payload["signature"] = "0" * 64
    tampered_proof = (
        base64.urlsafe_b64encode(json.dumps(tampered_payload).encode())
        .decode()
        .rstrip("=")
    )
    tampered = await async_client.post(
        "/st/auth/signup",
        headers={"x-altcha-payload": tampered_proof},
        json=_emailpassword_payload(
            f"altcha-tampered-{uuid4().hex[:8]}@example.com", "TestPassword@123"
        ),
    )
    assert tampered.status_code == 400
    assert "Invalid proof-of-work" in tampered.text

    # Isolate the boundary check from the replay/tampering requests above.
    store = get_auth_abuse_store()
    ip_hash = store.digest("127.0.0.1")
    await store.clear(
        f"identity:rate:global:{ip_hash}",
        f"identity:rate:email-action:ip:15m:{ip_hash}",
        f"identity:rate:email-action:ip:day:{ip_hash}",
    )

    for index in range(5):
        async_client.cookies.clear()
        proof = _solve_altcha(await challenge())
        accepted = await async_client.post(
            "/st/auth/signup",
            headers={"x-altcha-payload": proof},
            json=_emailpassword_payload(
                f"altcha-limit-{index}-{uuid4().hex[:8]}@example.com",
                "TestPassword@123",
            ),
        )
        assert accepted.status_code == 200, accepted.text

    async_client.cookies.clear()
    limited = await async_client.post(
        "/st/auth/signup",
        headers={"x-altcha-payload": _solve_altcha(await challenge())},
        json=_emailpassword_payload(
            f"altcha-limited-{uuid4().hex[:8]}@example.com", "TestPassword@123"
        ),
    )
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) >= 1


@pytest.mark.asyncio
async def test_signed_bounce_events_only_deactivate_on_hard_bounce(
    async_client: AsyncClient,
    signup_user,
    monkeypatch,
):
    webhook_secret = "e2e-bounce-secret"
    monkeypatch.setattr(
        settings, "auth_bounce_webhook_secret", SecretStr(webhook_secret)
    )
    hard_bounced = await signup_user(email=f"hard-bounce-{uuid4().hex[:8]}@example.com")
    soft_bounced = await signup_user(email=f"soft-bounce-{uuid4().hex[:8]}@example.com")

    async def send_event(payload: dict) -> Response:
        body = json.dumps(payload, separators=(",", ":")).encode()
        timestamp = str(int(time.time()))
        signature = hmac.new(
            webhook_secret.encode(),
            f"{timestamp}.".encode() + body,
            hashlib.sha256,
        ).hexdigest()
        return await async_client.post(
            "/auth/email/bounces",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Lemma-Timestamp": timestamp,
                "X-Lemma-Signature": f"sha256={signature}",
            },
        )

    hard_payload = {
        "email": hard_bounced["email"],
        "event": "hard_bounce",
    }
    hard_response = await send_event(hard_payload)
    assert hard_response.status_code == 204
    # Provider retries are idempotent against the already-deactivated user.
    assert (await send_event(hard_payload)).status_code == 204

    soft_response = await send_event(
        {
            "email": soft_bounced["email"],
            "event": "soft_bounce",
        }
    )
    assert soft_response.status_code == 204

    async with async_session_maker() as session:
        hard_user = await session.get(User, UUID(hard_bounced["id"]))
        soft_user = await session.get(User, UUID(soft_bounced["id"]))
    assert hard_user is not None
    assert hard_user.is_active is False
    assert hard_user.deactivation_reason == "HARD_BOUNCE"
    assert soft_user is not None and soft_user.is_active is True

    # Password-reset remains enumeration-safe but produces no further email
    # after the account has been deactivated by a confirmed hard bounce.
    existing_messages = _filesystem_emails(
        settings.email_output_dir, hard_bounced["email"]
    )
    reset_response = await async_client.post(
        "/st/auth/user/password/reset/token",
        json={"formFields": [{"id": "email", "value": hard_bounced["email"]}]},
    )
    assert reset_response.status_code == 200
    assert reset_response.json()["status"] == "OK"
    assert (
        _filesystem_emails(settings.email_output_dir, hard_bounced["email"])
        == existing_messages
    )

    revoked_session = await async_client.get(
        "/users/me", headers=_auth_headers(hard_bounced["token"])
    )
    assert revoked_session.status_code in {401, 403}
    blocked_signin = await async_client.post(
        "/st/auth/signin",
        json=_emailpassword_payload(hard_bounced["email"], hard_bounced["password"]),
    )
    assert blocked_signin.json()["status"] == "SIGN_IN_NOT_ALLOWED"


@pytest.mark.asyncio
async def test_telegram_oidc_verifies_mobile_then_creates_global_cookie_session(
    async_client: AsyncClient,
    signup_user,
    monkeypatch,
):
    monkeypatch.setattr(settings, "telegram_oidc_client_id", "telegram-e2e-client")
    monkeypatch.setattr(
        settings,
        "telegram_oidc_client_secret",
        SecretStr("telegram-e2e-secret"),
    )
    monkeypatch.setattr(
        settings,
        "telegram_oidc_redirect_uri",
        "http://testserver/auth/telegram/callback",
    )
    phone = "+14155552671"

    async def exchange_and_validate(self, *, code, transaction):
        assert code == "telegram-authorization-code"
        assert transaction.code_verifier
        assert transaction.nonce
        return {
            "sub": "telegram-user-123",
            "phone_number": phone,
            "phone_number_verified": True,
        }

    monkeypatch.setattr(
        telegram_oidc.TelegramOIDCService,
        "exchange_and_validate",
        exchange_and_validate,
    )

    user = await signup_user(email=f"telegram-login-{uuid4().hex[:8]}@example.com")
    profile_return_to = settings.frontend_url.rstrip("/") + "/profile"
    verify_start = await async_client.get(
        "/auth/telegram/start",
        headers=_auth_headers(user["token"]),
        params={
            "purpose": "verify_mobile",
            "return_to": profile_return_to,
        },
    )
    assert verify_start.status_code == 303
    verify_authorization = urlparse(verify_start.headers["location"])
    verify_query = parse_qs(verify_authorization.query)
    assert verify_query["scope"] == ["openid profile phone"]
    assert verify_query["code_challenge_method"] == ["S256"]
    assert verify_query["nonce"]

    verify_callback = await async_client.get(
        "/auth/telegram/callback",
        headers=_auth_headers(user["token"]),
        params={
            "state": verify_query["state"][0],
            "code": "telegram-authorization-code",
        },
    )
    assert verify_callback.status_code == 303
    assert verify_callback.headers["location"] == profile_return_to

    async with async_session_maker() as session:
        verified_user = await session.get(User, UUID(user["id"]))
    assert verified_user is not None
    assert verified_user.mobile_number == phone
    assert verified_user.mobile_verified_at is not None

    async_client.cookies.clear()
    signin_start = await async_client.get(
        "/auth/telegram/start",
        params={
            "purpose": "signin",
            "return_to": "https://attacker.example/steal-session",
        },
    )
    assert signin_start.status_code == 303
    signin_query = parse_qs(urlparse(signin_start.headers["location"]).query)
    signin_callback = await async_client.get(
        "/auth/telegram/callback",
        params={
            "state": signin_query["state"][0],
            "code": "telegram-authorization-code",
        },
    )
    assert signin_callback.status_code == 303
    assert signin_callback.headers["location"] == (
        settings.auth_frontend_url.rstrip("/") + "/"
    )
    assert async_client.cookies.get("sAccessToken")
    telegram_me = await async_client.get("/users/me")
    assert telegram_me.status_code == 200
    assert telegram_me.json()["id"] == user["id"]

    async_client.cookies.clear()
    replay = await async_client.get(
        "/auth/telegram/callback",
        params={
            "state": signin_query["state"][0],
            "code": "telegram-authorization-code",
        },
    )
    assert replay.status_code == 303
    assert "telegram_error=unable_to_authenticate" in replay.headers["location"]


@pytest.mark.asyncio
async def test_org_domain_slug_availability_and_suggestions(
    async_client: AsyncClient,
    signup_user,
):
    owner = await signup_user(email=f"owner-{uuid4().hex[:8]}@acme-example.com")
    coworker = await signup_user(email=f"teammate-{uuid4().hex[:8]}@acme-example.com")
    outsider = await signup_user(email=f"outsider-{uuid4().hex[:8]}@other-example.com")
    gmail_user = await signup_user(email=f"personal-{uuid4().hex[:8]}@gmail.com")

    owner_headers = _auth_headers(owner["token"])

    available_before_resp = await async_client.get(
        "/organizations/slug-availability",
        headers=owner_headers,
        params={"slug": "Acme Auto Join"},
    )
    assert available_before_resp.status_code == 200
    assert available_before_resp.json() == {
        "slug": "acme-auto-join",
        "available": True,
    }

    create_org_resp = await async_client.post(
        "/organizations",
        headers=owner_headers,
        json={"name": "Acme Auto Join", "join_policy": "EMAIL_DOMAIN"},
    )
    assert create_org_resp.status_code == 201, create_org_resp.text
    org = create_org_resp.json()
    assert org["slug"] == "acme-auto-join"
    assert org["email_domain"] == "acme-example.com"
    assert org["join_policy"] == "EMAIL_DOMAIN"

    special_name_resp = await async_client.post(
        "/organizations",
        headers=owner_headers,
        json={"name": f"Acme's Special Ops {uuid4().hex[:6]}"},
    )
    assert special_name_resp.status_code == 201, special_name_resp.text
    assert "'" not in special_name_resp.json()["slug"]
    assert special_name_resp.json()["slug"].startswith("acme-s-special-ops-")

    invalid_slug_resp = await async_client.post(
        "/organizations",
        headers=owner_headers,
        json={"name": f"Invalid Slug Org {uuid4().hex[:6]}", "slug": "bad'slug"},
    )
    assert invalid_slug_resp.status_code == 400, invalid_slug_resp.text
    assert "slug" in invalid_slug_resp.json()["message"].lower()

    available_after_resp = await async_client.get(
        "/organizations/slug-availability",
        headers=owner_headers,
        params={"slug": "acme-auto-join"},
    )
    assert available_after_resp.status_code == 200
    assert available_after_resp.json()["available"] is False

    coworker_suggestions_resp = await async_client.get(
        "/organizations/suggested",
        headers=_auth_headers(coworker["token"]),
    )
    assert coworker_suggestions_resp.status_code == 200
    assert [item["id"] for item in coworker_suggestions_resp.json()["items"]] == [
        org["id"]
    ]

    outsider_suggestions_resp = await async_client.get(
        "/organizations/suggested",
        headers=_auth_headers(outsider["token"]),
    )
    assert outsider_suggestions_resp.status_code == 200
    assert outsider_suggestions_resp.json()["items"] == []

    # A second same-domain user cannot claim the domain for EMAIL_DOMAIN...
    duplicate_domain_resp = await async_client.post(
        "/organizations",
        headers=_auth_headers(coworker["token"]),
        json={"name": "Acme Duplicate Domain", "join_policy": "EMAIL_DOMAIN"},
    )
    assert duplicate_domain_resp.status_code == 409
    assert "email domain" in duplicate_domain_resp.json()["message"].lower()

    # ...but can still create their own org with the default INVITE_ONLY policy.
    coworker_org_resp = await async_client.post(
        "/organizations",
        headers=_auth_headers(coworker["token"]),
        json={"name": "Acme Coworker Org"},
    )
    assert coworker_org_resp.status_code == 201, coworker_org_resp.text
    assert coworker_org_resp.json()["join_policy"] == "INVITE_ONLY"
    assert coworker_org_resp.json()["email_domain"] is None

    # Personal email domains are not eligible for the EMAIL_DOMAIN policy.
    gmail_domain_resp = await async_client.post(
        "/organizations",
        headers=_auth_headers(gmail_user["token"]),
        json={"name": f"Gmail Domain {uuid4().hex[:8]}", "join_policy": "EMAIL_DOMAIN"},
    )
    assert gmail_domain_resp.status_code == 400

    gmail_org_resp = await async_client.post(
        "/organizations",
        headers=_auth_headers(gmail_user["token"]),
        json={"name": f"Gmail Org {uuid4().hex[:8]}"},
    )
    assert gmail_org_resp.status_code == 201
    assert gmail_org_resp.json()["email_domain"] is None


@pytest.mark.asyncio
async def test_organization_slug_is_globally_unique(
    async_client: AsyncClient,
    signup_user,
):
    first_owner = await signup_user(email=f"slug-a-{uuid4().hex[:8]}@slug-a.example")
    second_owner = await signup_user(email=f"slug-b-{uuid4().hex[:8]}@slug-b.example")

    first = await async_client.post(
        "/organizations",
        headers=_auth_headers(first_owner["token"]),
        json={"name": "Global Slug Collision"},
    )
    assert first.status_code == 201, first.text
    assert first.json()["slug"] == "global-slug-collision"

    second = await async_client.post(
        "/organizations",
        headers=_auth_headers(second_owner["token"]),
        json={"name": "Global-Slug Collision"},
    )
    assert second.status_code == 409, second.text
    assert "slug" in second.json()["message"].lower()


@pytest.mark.asyncio
async def test_organization_full_api_flow(
    async_client: AsyncClient,
    signup_user,
):
    owner = await signup_user()
    invitee = await signup_user()
    third_user = await signup_user()

    owner_headers = _auth_headers(owner["token"])
    invitee_headers = _auth_headers(invitee["token"])

    create_org_resp = await async_client.post(
        "/organizations",
        headers=owner_headers,
        json={"name": "Identity Refactor Org"},
    )
    assert create_org_resp.status_code == 201, create_org_resp.text
    org = create_org_resp.json()
    org_id = org["id"]

    list_org_resp = await async_client.get("/organizations", headers=owner_headers)
    assert list_org_resp.status_code == 200
    assert any(item["id"] == org_id for item in list_org_resp.json()["items"])

    get_org_resp = await async_client.get(
        f"/organizations/{org_id}",
        headers=owner_headers,
    )
    assert get_org_resp.status_code == 200
    assert get_org_resp.json()["id"] == org_id

    members_resp = await async_client.get(
        f"/organizations/{org_id}/members",
        headers=owner_headers,
    )
    assert members_resp.status_code == 200
    assert len(members_resp.json()["items"]) == 1

    invite_resp = await async_client.post(
        f"/organizations/{org_id}/invitations",
        headers=owner_headers,
        json={"email": invitee["email"], "role": "ORG_MEMBER"},
    )
    assert invite_resp.status_code == 201, invite_resp.text
    invitation = invite_resp.json()
    invitation_id = invitation["id"]
    assert invitation["status"] == "PENDING"
    assert invitation["expires_at"] is not None
    assert invitation["organization_name"] == "Identity Refactor Org"

    list_invites_resp = await async_client.get(
        f"/organizations/{org_id}/invitations",
        headers=owner_headers,
    )
    assert list_invites_resp.status_code == 200
    assert any(
        item["id"] == invitation_id for item in list_invites_resp.json()["items"]
    )

    list_my_invites_resp = await async_client.get(
        "/organizations/invitations",
        headers=invitee_headers,
    )
    assert list_my_invites_resp.status_code == 200
    listed_invitation = next(
        item
        for item in list_my_invites_resp.json()["items"]
        if item["id"] == invitation_id
    )
    assert listed_invitation["organization_name"] == "Identity Refactor Org"

    get_invite_resp = await async_client.get(
        f"/organizations/invitations/{invitation_id}",
        headers=owner_headers,
    )
    assert get_invite_resp.status_code == 200
    assert get_invite_resp.json()["id"] == invitation_id
    assert get_invite_resp.json()["organization_name"] == "Identity Refactor Org"

    accept_resp = await async_client.post(
        f"/organizations/invitations/{invitation_id}/accept",
        headers=invitee_headers,
    )
    assert accept_resp.status_code == 200, accept_resp.text

    get_accepted_invite_resp = await async_client.get(
        f"/organizations/invitations/{invitation_id}",
        headers=owner_headers,
    )
    assert get_accepted_invite_resp.status_code == 200
    assert get_accepted_invite_resp.json()["status"] == "ACCEPTED"

    members_after_accept_resp = await async_client.get(
        f"/organizations/{org_id}/members",
        headers=owner_headers,
    )
    assert members_after_accept_resp.status_code == 200
    members = members_after_accept_resp.json()["items"]
    invitee_member = next(
        member
        for member in members
        if member.get("user", {}).get("email") == invitee["email"]
    )

    update_role_resp = await async_client.patch(
        f"/organizations/{org_id}/members/{invitee_member['id']}/role",
        headers=owner_headers,
        json={"role": "ORG_EDITOR"},
    )
    assert update_role_resp.status_code == 200
    assert update_role_resp.json()["role"] == "ORG_EDITOR"

    remove_member_resp = await async_client.delete(
        f"/organizations/{org_id}/members/{invitee_member['id']}",
        headers=owner_headers,
    )
    assert remove_member_resp.status_code == 204

    invite_third_resp = await async_client.post(
        f"/organizations/{org_id}/invitations",
        headers=owner_headers,
        json={"email": third_user["email"], "role": "ORG_MEMBER"},
    )
    assert invite_third_resp.status_code == 201
    third_invitation_id = invite_third_resp.json()["id"]

    revoke_resp = await async_client.delete(
        f"/organizations/invitations/{third_invitation_id}",
        headers=owner_headers,
    )
    assert revoke_resp.status_code == 204

    revoked_invite_resp = await async_client.get(
        f"/organizations/invitations/{third_invitation_id}",
        headers=owner_headers,
    )
    assert revoked_invite_resp.status_code == 200
    assert revoked_invite_resp.json()["status"] == "REVOKED"


@pytest.mark.asyncio
async def test_invitation_email_validation_and_normalization(
    async_client: AsyncClient,
    signup_user,
):
    owner = await signup_user()
    owner_headers = _auth_headers(owner["token"])

    create_org_resp = await async_client.post(
        "/organizations",
        headers=owner_headers,
        json={"name": f"Email Normalize Org {uuid4().hex[:8]}"},
    )
    assert create_org_resp.status_code == 201, create_org_resp.text
    org_id = create_org_resp.json()["id"]

    invalid_resp = await async_client.post(
        f"/organizations/{org_id}/invitations",
        headers=owner_headers,
        json={"email": "not-an-email", "role": "ORG_MEMBER"},
    )
    assert invalid_resp.status_code == 422, invalid_resp.text

    invite_resp = await async_client.post(
        f"/organizations/{org_id}/invitations",
        headers=owner_headers,
        json={"email": "Invitee+Case@Example.COM", "role": "ORG_MEMBER"},
    )
    assert invite_resp.status_code == 201, invite_resp.text
    assert invite_resp.json()["email"] == "invitee+case@example.com"

    duplicate_resp = await async_client.post(
        f"/organizations/{org_id}/invitations",
        headers=owner_headers,
        json={"email": "invitee+case@example.com", "role": "ORG_MEMBER"},
    )
    assert duplicate_resp.status_code == 409, duplicate_resp.text


@pytest.mark.asyncio
async def test_concurrent_invitations_for_normalized_email_create_only_one(
    async_client: AsyncClient,
    signup_user,
):
    owner = await signup_user()
    owner_headers = _auth_headers(owner["token"])

    create_org_resp = await async_client.post(
        "/organizations",
        headers=owner_headers,
        json={"name": f"Concurrent Invite Org {uuid4().hex[:8]}"},
    )
    assert create_org_resp.status_code == 201, create_org_resp.text
    org_id = create_org_resp.json()["id"]

    unique = uuid4().hex[:10]
    normalized_email = f"concurrent-{unique}@example.com"
    responses = await asyncio.gather(
        async_client.post(
            f"/organizations/{org_id}/invitations",
            headers=owner_headers,
            json={"email": normalized_email.upper(), "role": "ORG_MEMBER"},
        ),
        async_client.post(
            f"/organizations/{org_id}/invitations",
            headers=owner_headers,
            json={"email": normalized_email, "role": "ORG_MEMBER"},
        ),
    )

    assert sorted(response.status_code for response in responses) == [201, 409], [
        response.text for response in responses
    ]
    created = next(response for response in responses if response.status_code == 201)
    assert created.json()["email"] == normalized_email

    list_response = await async_client.get(
        f"/organizations/{org_id}/invitations",
        headers=owner_headers,
    )
    assert list_response.status_code == 200, list_response.text
    matching = [
        invitation
        for invitation in list_response.json()["items"]
        if invitation["email"] == normalized_email
    ]
    assert len(matching) == 1


@pytest.mark.asyncio
async def test_revoked_and_expired_invitations_do_not_block_reinvite(
    async_client: AsyncClient,
    signup_user,
    db_session,
):
    owner = await signup_user()
    invitee = await signup_user()
    owner_headers = _auth_headers(owner["token"])
    invitee_headers = _auth_headers(invitee["token"])

    create_org_resp = await async_client.post(
        "/organizations",
        headers=owner_headers,
        json={"name": f"Reinvite Org {uuid4().hex[:8]}"},
    )
    assert create_org_resp.status_code == 201, create_org_resp.text
    org_id = create_org_resp.json()["id"]

    first_invite_resp = await async_client.post(
        f"/organizations/{org_id}/invitations",
        headers=owner_headers,
        json={"email": invitee["email"].upper(), "role": "ORG_MEMBER"},
    )
    assert first_invite_resp.status_code == 201, first_invite_resp.text
    first_invite_id = first_invite_resp.json()["id"]

    revoke_resp = await async_client.delete(
        f"/organizations/invitations/{first_invite_id}",
        headers=owner_headers,
    )
    assert revoke_resp.status_code == 204, revoke_resp.text

    second_invite_resp = await async_client.post(
        f"/organizations/{org_id}/invitations",
        headers=owner_headers,
        json={"email": invitee["email"], "role": "ORG_MEMBER"},
    )
    assert second_invite_resp.status_code == 201, second_invite_resp.text
    second_invite_id = second_invite_resp.json()["id"]
    assert second_invite_id != first_invite_id

    await db_session.execute(
        update(OrganizationInvitation)
        .where(OrganizationInvitation.id == UUID(second_invite_id))
        .values(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    )
    await db_session.commit()

    third_invite_resp = await async_client.post(
        f"/organizations/{org_id}/invitations",
        headers=owner_headers,
        json={"email": invitee["email"], "role": "ORG_MEMBER"},
    )
    assert third_invite_resp.status_code == 201, third_invite_resp.text
    third_invite_id = third_invite_resp.json()["id"]
    assert third_invite_id not in {first_invite_id, second_invite_id}

    accept_resp = await async_client.post(
        f"/organizations/invitations/{third_invite_id}/accept",
        headers=invitee_headers,
    )
    assert accept_resp.status_code == 200, accept_resp.text


@pytest.mark.asyncio
async def test_accepted_invitation_does_not_block_reinvite_after_member_removed(
    async_client: AsyncClient,
    signup_user,
):
    owner = await signup_user()
    invitee = await signup_user()
    owner_headers = _auth_headers(owner["token"])
    invitee_headers = _auth_headers(invitee["token"])

    create_org_resp = await async_client.post(
        "/organizations",
        headers=owner_headers,
        json={"name": f"Accepted Reinvite Org {uuid4().hex[:8]}"},
    )
    assert create_org_resp.status_code == 201, create_org_resp.text
    org_id = create_org_resp.json()["id"]

    invite_resp = await async_client.post(
        f"/organizations/{org_id}/invitations",
        headers=owner_headers,
        json={"email": invitee["email"], "role": "ORG_MEMBER"},
    )
    assert invite_resp.status_code == 201, invite_resp.text
    accept_resp = await async_client.post(
        f"/organizations/invitations/{invite_resp.json()['id']}/accept",
        headers=invitee_headers,
    )
    assert accept_resp.status_code == 200, accept_resp.text

    members_resp = await async_client.get(
        f"/organizations/{org_id}/members",
        headers=owner_headers,
    )
    assert members_resp.status_code == 200, members_resp.text
    invitee_member = next(
        member
        for member in members_resp.json()["items"]
        if member.get("user", {}).get("email") == invitee["email"]
    )

    remove_resp = await async_client.delete(
        f"/organizations/{org_id}/members/{invitee_member['id']}",
        headers=owner_headers,
    )
    assert remove_resp.status_code == 204, remove_resp.text

    reinvite_resp = await async_client.post(
        f"/organizations/{org_id}/invitations",
        headers=owner_headers,
        json={"email": invitee["email"].upper(), "role": "ORG_MEMBER"},
    )
    assert reinvite_resp.status_code == 201, reinvite_resp.text
    assert reinvite_resp.json()["email"] == invitee["email"]


@pytest.mark.asyncio
async def test_identity_error_translation_payload(
    async_client: AsyncClient,
    signup_user,
):
    owner = await signup_user()
    outsider = await signup_user()
    invitee = await signup_user()

    owner_headers = _auth_headers(owner["token"])
    outsider_headers = _auth_headers(outsider["token"])

    create_org_resp = await async_client.post(
        "/organizations",
        headers=owner_headers,
        json={"name": "Private Org"},
    )
    assert create_org_resp.status_code == 201
    org_id = create_org_resp.json()["id"]

    no_access_resp = await async_client.get(
        f"/organizations/{org_id}",
        headers=outsider_headers,
    )
    assert no_access_resp.status_code == 403
    no_access_payload = no_access_resp.json()
    assert no_access_payload["code"] == "IDENTITY_ACCESS_DENIED"
    assert "message" in no_access_payload

    invite_resp = await async_client.post(
        f"/organizations/{org_id}/invitations",
        headers=owner_headers,
        json={"email": invitee["email"], "role": "ORG_MEMBER"},
    )
    assert invite_resp.status_code == 201
    invitation_id = invite_resp.json()["id"]

    mismatch_accept_resp = await async_client.post(
        f"/organizations/invitations/{invitation_id}/accept",
        headers=outsider_headers,
    )
    assert mismatch_accept_resp.status_code == 403
    mismatch_payload = mismatch_accept_resp.json()
    assert mismatch_payload["code"] == "IDENTITY_ACCESS_DENIED"
    assert "not for your email" in mismatch_payload["message"].lower()


@pytest.mark.asyncio
async def test_google_signinup_is_blocked_for_existing_emailpassword_user(
    async_client: AsyncClient,
    signup_user,
    google_signinup,
    mock_google_provider,
):
    existing_user = await signup_user()

    response = await google_signinup(
        existing_user["email"],
        third_party_user_id="google-existing-emailpassword-conflict",
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "SIGN_IN_UP_NOT_ALLOWED",
        "reason": "This email is already registered with email and password. Please sign in using your password.",
    }


@pytest.mark.asyncio
async def test_email_signup_is_blocked_for_existing_google_user(
    async_client: AsyncClient,
    google_signinup,
    mock_google_provider,
):
    email = "test+google-signup-conflict@example.com"

    google_response = await google_signinup(
        email,
        third_party_user_id="google-signup-conflict",
    )
    google_payload = google_response.json()
    assert google_response.status_code == 200
    assert google_payload["status"] == "OK", google_payload

    emailpassword_response = await async_client.post(
        "/st/auth/signup",
        json=_emailpassword_payload(email, "TestPassword@123"),
    )

    assert emailpassword_response.status_code == 200
    assert emailpassword_response.json() == {
        "status": "SIGN_UP_NOT_ALLOWED",
        "reason": "This email is already registered with Google. Please sign in using Google.",
    }


@pytest.mark.asyncio
async def test_email_signin_is_blocked_for_existing_google_user(
    async_client: AsyncClient,
    google_signinup,
    mock_google_provider,
):
    email = "test+google-signin-conflict@example.com"

    google_response = await google_signinup(
        email,
        third_party_user_id="google-signin-conflict",
    )
    google_payload = google_response.json()
    assert google_response.status_code == 200
    assert google_payload["status"] == "OK", google_payload

    emailpassword_response = await async_client.post(
        "/st/auth/signin",
        json=_emailpassword_payload(email, "TestPassword@123"),
    )

    assert emailpassword_response.status_code == 200
    assert emailpassword_response.json() == {
        "status": "SIGN_IN_NOT_ALLOWED",
        "reason": "This email is already registered with Google. Please sign in using Google.",
    }


@pytest.mark.asyncio
async def test_existing_google_user_can_signinup_again(
    google_signinup,
    mock_google_provider,
):
    email = "test+google-repeat@example.com"

    first_response = await google_signinup(
        email,
        third_party_user_id="google-repeat-user",
    )
    first_payload = first_response.json()
    assert first_response.status_code == 200
    assert first_payload["status"] == "OK", first_payload
    assert first_payload["createdNewRecipeUser"] is True

    second_response = await google_signinup(
        email,
        third_party_user_id="google-repeat-user",
    )
    second_payload = second_response.json()
    assert second_response.status_code == 200
    assert second_payload["status"] == "OK", second_payload
    assert second_payload["createdNewRecipeUser"] is False


@pytest.mark.asyncio
async def test_invite_with_pod_id_adds_user_to_pod_on_accept(
    async_client: AsyncClient,
    signup_user,
):
    owner = await signup_user()
    invitee = await signup_user()
    owner_headers = _auth_headers(owner["token"])
    invitee_headers = _auth_headers(invitee["token"])

    create_org_resp = await async_client.post(
        "/organizations",
        headers=owner_headers,
        json={"name": f"Pod Invite Org {uuid4().hex[:8]}"},
    )
    assert create_org_resp.status_code == 201, create_org_resp.text
    org_id = create_org_resp.json()["id"]

    create_pod_resp = await async_client.post(
        "/pods",
        headers=owner_headers,
        json={
            "name": f"Pod Invite Pod {uuid4().hex[:8]}",
            "description": "Pod invitation description",
            "organization_id": org_id,
        },
    )
    assert create_pod_resp.status_code == 201, create_pod_resp.text
    pod_id = create_pod_resp.json()["id"]

    invite_resp = await async_client.post(
        f"/organizations/{org_id}/invitations",
        headers=owner_headers,
        json={
            "email": invitee["email"],
            "role": "ORG_MEMBER",
            "pod_id": pod_id,
            "pod_role": "POD_EDITOR",
            "redirect_uri": "https://app.example.com/invite/accepted",
        },
    )
    assert invite_resp.status_code == 201, invite_resp.text
    invitation = invite_resp.json()
    assert invitation["pod_id"] == pod_id
    assert invitation["pod_role"] == "POD_EDITOR"
    assert invitation["redirect_uri"] == "https://app.example.com/invite/accepted"
    assert invitation["organization_name"].startswith("Pod Invite Org")

    get_invite_resp = await async_client.get(
        f"/organizations/invitations/{invitation['id']}",
        headers=invitee_headers,
    )
    assert get_invite_resp.status_code == 200
    invite_detail = get_invite_resp.json()
    assert invite_detail["organization_name"] == invitation["organization_name"]
    assert invite_detail["pod_name"].startswith("Pod Invite Pod")
    assert invite_detail["pod_description"] == "Pod invitation description"
    assert invite_detail["redirect_uri"] == "https://app.example.com/invite/accepted"

    accept_resp = await async_client.post(
        f"/organizations/invitations/{invitation['id']}/accept",
        headers=invitee_headers,
    )
    assert accept_resp.status_code == 200, accept_resp.text
    assert (
        accept_resp.json()["redirect_uri"] == "https://app.example.com/invite/accepted"
    )

    members_resp = await async_client.get(
        f"/organizations/{org_id}/members",
        headers=owner_headers,
    )
    assert members_resp.status_code == 200
    members = members_resp.json()["items"]
    invitee_member = next(
        m for m in members if m.get("user", {}).get("email") == invitee["email"]
    )
    assert invitee_member is not None

    pod_members_resp = await async_client.get(
        f"/pods/{pod_id}/members",
        headers=owner_headers,
    )
    assert pod_members_resp.status_code == 200
    pod_members = pod_members_resp.json().get("items", [])
    invitee_pod_member = next(
        (m for m in pod_members if m["user_id"] == invitee_member["user"]["id"]),
        None,
    )
    assert invitee_pod_member is not None
    assert invitee_pod_member["roles"] == ["POD_EDITOR"]


@pytest.mark.asyncio
async def test_revoked_pod_invitation_can_be_reinvited_and_accepted(
    async_client: AsyncClient,
    signup_user,
):
    owner = await signup_user()
    invitee = await signup_user()
    owner_headers = _auth_headers(owner["token"])
    invitee_headers = _auth_headers(invitee["token"])

    create_org_resp = await async_client.post(
        "/organizations",
        headers=owner_headers,
        json={"name": f"Pod Reinvite Org {uuid4().hex[:8]}"},
    )
    assert create_org_resp.status_code == 201, create_org_resp.text
    org_id = create_org_resp.json()["id"]

    create_pod_resp = await async_client.post(
        "/pods",
        headers=owner_headers,
        json={
            "name": f"Pod Reinvite Pod {uuid4().hex[:8]}",
            "organization_id": org_id,
        },
    )
    assert create_pod_resp.status_code == 201, create_pod_resp.text
    pod_id = create_pod_resp.json()["id"]

    first_invite_resp = await async_client.post(
        f"/organizations/{org_id}/invitations",
        headers=owner_headers,
        json={
            "email": invitee["email"].upper(),
            "role": "ORG_MEMBER",
            "pod_id": pod_id,
            "pod_role": "POD_EDITOR",
        },
    )
    assert first_invite_resp.status_code == 201, first_invite_resp.text

    revoke_resp = await async_client.delete(
        f"/organizations/invitations/{first_invite_resp.json()['id']}",
        headers=owner_headers,
    )
    assert revoke_resp.status_code == 204, revoke_resp.text

    second_invite_resp = await async_client.post(
        f"/organizations/{org_id}/invitations",
        headers=owner_headers,
        json={
            "email": invitee["email"],
            "role": "ORG_MEMBER",
            "pod_id": pod_id,
            "pod_role": "POD_EDITOR",
        },
    )
    assert second_invite_resp.status_code == 201, second_invite_resp.text

    accept_resp = await async_client.post(
        f"/organizations/invitations/{second_invite_resp.json()['id']}/accept",
        headers=invitee_headers,
    )
    assert accept_resp.status_code == 200, accept_resp.text

    pod_members_resp = await async_client.get(
        f"/pods/{pod_id}/members",
        headers=owner_headers,
    )
    assert pod_members_resp.status_code == 200, pod_members_resp.text
    invitee_pod_member = next(
        (m for m in pod_members_resp.json()["items"] if m["email"] == invitee["email"]),
        None,
    )
    assert invitee_pod_member is not None
    assert invitee_pod_member["roles"] == ["POD_EDITOR"]


@pytest.mark.asyncio
async def test_invite_with_pod_id_defaults_role_to_POD_USER(
    async_client: AsyncClient,
    signup_user,
):
    owner = await signup_user()
    invitee = await signup_user()
    owner_headers = _auth_headers(owner["token"])
    invitee_headers = _auth_headers(invitee["token"])

    create_org_resp = await async_client.post(
        "/organizations",
        headers=owner_headers,
        json={"name": f"Pod Default Role Org {uuid4().hex[:8]}"},
    )
    assert create_org_resp.status_code == 201
    org_id = create_org_resp.json()["id"]

    create_pod_resp = await async_client.post(
        "/pods",
        headers=owner_headers,
        json={
            "name": f"Pod Default Role Pod {uuid4().hex[:8]}",
            "organization_id": org_id,
        },
    )
    assert create_pod_resp.status_code == 201
    pod_id = create_pod_resp.json()["id"]

    invite_resp = await async_client.post(
        f"/organizations/{org_id}/invitations",
        headers=owner_headers,
        json={
            "email": invitee["email"],
            "role": "ORG_MEMBER",
            "pod_id": pod_id,
        },
    )
    assert invite_resp.status_code == 201
    invitation = invite_resp.json()
    assert invitation["pod_id"] == pod_id
    assert invitation["pod_role"] is None

    accept_resp = await async_client.post(
        f"/organizations/invitations/{invitation['id']}/accept",
        headers=invitee_headers,
    )
    assert accept_resp.status_code == 200

    members_resp = await async_client.get(
        f"/organizations/{org_id}/members",
        headers=owner_headers,
    )
    assert members_resp.status_code == 200
    invitee_member = next(
        m
        for m in members_resp.json()["items"]
        if m.get("user", {}).get("email") == invitee["email"]
    )

    pod_members_resp = await async_client.get(
        f"/pods/{pod_id}/members",
        headers=owner_headers,
    )
    assert pod_members_resp.status_code == 200
    invitee_pod_member = next(
        m
        for m in pod_members_resp.json().get("items", [])
        if m["user_id"] == invitee_member["user"]["id"]
    )
    assert invitee_pod_member["roles"] == ["POD_USER"]


@pytest.mark.asyncio
async def test_invite_with_pod_id_from_different_org_is_rejected(
    async_client: AsyncClient,
    signup_user,
):
    owner = await signup_user(email=f"cross-org-owner-{uuid4().hex[:8]}@gmail.com")
    owner_headers = _auth_headers(owner["token"])

    create_org_a_resp = await async_client.post(
        "/organizations",
        headers=owner_headers,
        json={"name": f"Org A {uuid4().hex[:8]}"},
    )
    assert create_org_a_resp.status_code == 201
    org_a_id = create_org_a_resp.json()["id"]

    create_org_b_resp = await async_client.post(
        "/organizations",
        headers=owner_headers,
        json={"name": f"Org B {uuid4().hex[:8]}"},
    )
    assert create_org_b_resp.status_code == 201
    org_b_id = create_org_b_resp.json()["id"]

    create_pod_resp = await async_client.post(
        "/pods",
        headers=owner_headers,
        json={
            "name": f"Org B Pod {uuid4().hex[:8]}",
            "organization_id": org_b_id,
        },
    )
    assert create_pod_resp.status_code == 201
    pod_b_id = create_pod_resp.json()["id"]

    invite_resp = await async_client.post(
        f"/organizations/{org_a_id}/invitations",
        headers=owner_headers,
        json={
            "email": "test+cross-org-pod@example.com",
            "role": "ORG_MEMBER",
            "pod_id": pod_b_id,
        },
    )
    assert invite_resp.status_code == 400
    assert "Pod does not belong" in invite_resp.json()["message"]


@pytest.mark.asyncio
async def test_profile_unverified_mobile_and_telegram_uniqueness(
    async_client: AsyncClient,
    signup_user,
):
    first = await signup_user(email=f"first-{uuid4().hex[:8]}@uniq-example.com")
    second = await signup_user(email=f"second-{uuid4().hex[:8]}@uniq-example.com")
    first_headers = _auth_headers(first["token"])
    second_headers = _auth_headers(second["token"])

    set_first = await async_client.post(
        "/users/me/profile",
        headers=first_headers,
        json={"mobile_number": "+1 555 123 4567", "telegram_username": "AnukulT"},
    )
    assert set_first.status_code == 201

    # User-entered mobile numbers are explicitly unverified, so duplicates are
    # allowed until Telegram proves ownership and the partial unique index applies.
    dup_mobile = await async_client.post(
        "/users/me/profile",
        headers=second_headers,
        json={"mobile_number": "1(555)123-4567"},
    )
    assert dup_mobile.status_code == 201

    async with async_session_maker() as session:
        verified_owner = await session.get(User, UUID(first["id"]))
        assert verified_owner is not None
        verified_owner.mobile_number = "+15551234567"
        verified_owner.mobile_verified_at = datetime.now(timezone.utc)
        await session.commit()

    telegram_service = TelegramOIDCService()
    try:
        with pytest.raises(TelegramOIDCError, match="already in use"):
            await telegram_service.verify_mobile(UUID(second["id"]), "+15551234567")
    finally:
        await telegram_service.close()

    # Same telegram username, different case -> conflict.
    dup_telegram = await async_client.post(
        "/users/me/profile",
        headers=second_headers,
        json={"telegram_username": "anukult"},
    )
    assert dup_telegram.status_code == 409

    # Unique values are accepted.
    unique = await async_client.post(
        "/users/me/profile",
        headers=second_headers,
        json={"mobile_number": "+1 555 987 6543", "telegram_username": "someone_else"},
    )
    assert unique.status_code == 201

    # Re-saving one's own unchanged values is fine.
    resave = await async_client.post(
        "/users/me/profile",
        headers=first_headers,
        json={"mobile_number": "+1 555 123 4567", "telegram_username": "AnukulT"},
    )
    assert resave.status_code == 201

    # A user-editable change can never inherit Telegram's verification proof.
    changed = await async_client.post(
        "/users/me/profile",
        headers=first_headers,
        json={"mobile_number": "+1 555 000 9999"},
    )
    assert changed.status_code == 201
    async with async_session_maker() as session:
        changed_owner = await session.get(User, UUID(first["id"]))
    assert changed_owner is not None
    assert changed_owner.mobile_verified_at is None


@pytest.mark.asyncio
async def test_org_public_join_and_policy_update(
    async_client: AsyncClient,
    signup_user,
):
    owner = await signup_user(email=f"owner-{uuid4().hex[:8]}@pubco-example.com")
    outsider = await signup_user(
        email=f"outsider-{uuid4().hex[:8]}@elsewhere-example.com"
    )
    owner_headers = _auth_headers(owner["token"])
    outsider_headers = _auth_headers(outsider["token"])

    create = await async_client.post(
        "/organizations",
        headers=owner_headers,
        json={"name": f"PubCo {uuid4().hex[:8]}"},
    )
    assert create.status_code == 201, create.text
    org = create.json()
    assert org["join_policy"] == "INVITE_ONLY"

    # Invite-only org rejects self-join.
    denied = await async_client.post(
        f"/organizations/{org['id']}/join", headers=outsider_headers
    )
    assert denied.status_code == 403

    # Owner opens the org to any Lemma user.
    patched = await async_client.patch(
        f"/organizations/{org['id']}",
        headers=owner_headers,
        json={"join_policy": "PUBLIC"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["join_policy"] == "PUBLIC"

    # Any user can now self-join.
    joined = await async_client.post(
        f"/organizations/{org['id']}/join", headers=outsider_headers
    )
    assert joined.status_code == 200

    # Non-owners cannot change the policy.
    forbidden = await async_client.patch(
        f"/organizations/{org['id']}",
        headers=outsider_headers,
        json={"join_policy": "INVITE_ONLY"},
    )
    assert forbidden.status_code == 403
