"""Browser OTP issues ordinary cookies for the same canonical identity."""

from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.modules.identity.api.controllers import email_login_controller
from app.modules.identity.services.email_challenges import EmailChallengeService
from app.modules.identity.tests.e2e.test_email_challenges_e2e import (
    Mailbox,
    allow_test_delivery,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


async def test_browser_otp_requires_binding_and_creates_a_usable_session(
    async_client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    test_app,
) -> None:
    assert db_session.bind is not None
    mailbox = Mailbox()
    service = EmailChallengeService(
        async_sessionmaker(db_session.bind, expire_on_commit=False),
        send_email=mailbox.send,
        enforce_send_limits=allow_test_delivery,
    )
    monkeypatch.setitem(
        test_app.dependency_overrides,
        email_login_controller.get_email_login_challenges,
        lambda: service,
    )
    origin = settings.auth_frontend_url.rstrip("/")
    headers = {"origin": origin}
    refused = await async_client.post(
        "/auth/email-code/browser",
        json={},
        headers={"origin": "https://attacker.example"},
    )
    assert refused.status_code == 403
    initialized = await async_client.post(
        "/auth/email-code/browser", json={}, headers=headers
    )
    assert initialized.status_code == 200, initialized.text
    nonce = initialized.json()["nonce"]
    started = await async_client.post(
        "/auth/email-code/start",
        json={"email": f"browser-{uuid4().hex}@gmail.com", "nonce": nonce},
        headers=headers,
    )
    assert started.status_code == 200, started.text
    challenge_id = started.json()["challenge_id"]
    wrong_binding = await async_client.post(
        "/auth/email-code/verify",
        json={"challenge_id": challenge_id, "nonce": "x" * 43, "code": mailbox.code},
        headers=headers,
    )
    assert wrong_binding.status_code == 403
    verified = await async_client.post(
        "/auth/email-code/verify",
        json={"challenge_id": challenge_id, "nonce": nonce, "code": mailbox.code},
        headers=headers,
    )
    assert verified.status_code == 200, verified.text
    assert any(
        "sAccessToken=" in value for value in verified.headers.get_list("set-cookie")
    )
    workspace = await async_client.post(
        "/users/me/first-workspace",
        headers={"st-auth-mode": "cookie", "rid": "session"},
    )
    assert workspace.status_code == 200, workspace.text
    assert workspace.json()["pod_id"] and workspace.json()["assistant_id"]


async def test_stock_passwordless_endpoints_cannot_create_accounts(
    async_client: AsyncClient,
) -> None:
    for path, body in [
        ("/auth/signinup/code", {"email": f"bypass-{uuid4().hex}@gmail.com"}),
        (
            "/auth/signinup/code/consume",
            {
                "preAuthSessionId": "forged",
                "deviceId": "forged",
                "userInputCode": "123456",
            },
        ),
    ]:
        response = await async_client.post(
            path, json=body, headers={"rid": "passwordless"}
        )
        assert response.status_code in (401, 404, 405), response.text
