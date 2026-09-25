"""Browser OTP issues ordinary cookies for the same canonical identity."""

from uuid import uuid4

import pytest
from fastapi import FastAPI, Request
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.modules.identity.api.controllers import email_login_controller
from app.modules.identity.infrastructure.models.user_models import User
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
    assert refused.json()["code"] == "EMAIL_LOGIN_ORIGIN_NOT_ALLOWED"
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
    wrong_code = "000000" if mailbox.code != "000000" else "000001"
    rejected_code = await async_client.post(
        "/auth/email-code/verify",
        json={"challenge_id": challenge_id, "nonce": nonce, "code": wrong_code},
        headers=headers,
    )
    assert rejected_code.status_code == 400
    assert rejected_code.json()["code"] == "EMAIL_CODE_INVALID"
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


async def test_browser_origin_normalizes_default_https_port(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        settings, "auth_frontend_url", "https://auth.example.test:443/auth"
    )
    response = await async_client.post(
        "/auth/email-code/browser",
        json={},
        headers={"origin": "https://auth.example.test"},
    )
    assert response.status_code == 200, response.text


@pytest.mark.parametrize(
    "origin", ["https://[invalid", "null", "https://attacker.example"]
)
async def test_browser_rejects_invalid_origins_with_a_code(
    async_client: AsyncClient, origin: str
) -> None:
    response = await async_client.post(
        "/auth/email-code/browser", json={}, headers={"origin": origin}
    )
    assert response.status_code == 403
    assert response.json()["code"] == "EMAIL_LOGIN_ORIGIN_NOT_ALLOWED"


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


async def _no_limits(request: Request, email: str) -> None:
    """The method-lookup budget, stood down.

    Every test here arrives from the same client IP, so the real ceiling would
    be a shared allowance spent by whichever tests ran first --
    `test_continue_meters_the_method_lookup` is where it is exercised instead.
    """


def _with_email_code_service(
    test_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    service: EmailChallengeService,
) -> None:
    monkeypatch.setitem(
        test_app.dependency_overrides,
        email_login_controller.get_email_login_challenges,
        lambda: service,
    )
    monkeypatch.setitem(
        test_app.dependency_overrides,
        email_login_controller.get_method_lookup_limits,
        lambda: _no_limits,
    )


def _challenge_service(
    db_session: AsyncSession, mailbox: Mailbox
) -> EmailChallengeService:
    assert db_session.bind is not None
    return EmailChallengeService(
        async_sessionmaker(db_session.bind, expire_on_commit=False),
        send_email=mailbox.send,
        enforce_send_limits=allow_test_delivery,
    )


async def _bind_browser(async_client: AsyncClient, origin: str) -> str:
    initialized = await async_client.post(
        "/auth/email-code/browser", json={}, headers={"origin": origin}
    )
    assert initialized.status_code == 200, initialized.text
    return initialized.json()["nonce"]


@pytest.mark.parametrize(
    ("recipe", "expected"),
    [
        ("passwordless", "code"),
        ("absent", "code"),
        ("emailpassword", "password"),
        ("thirdparty", "thirdparty"),
    ],
)
async def test_continue_sends_each_account_to_the_door_that_opens(
    async_client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    test_app,
    recipe: str,
    expected: str,
) -> None:
    """One email box, and the answer is whichever method is not refused.

    `passwordless` and `absent` deliberately answer the same thing. That is the
    whole reason this endpoint can exist without being an account-existence
    oracle: `complete_verified_account` creates the account when there is none,
    so a code is a true answer either way and neither reply says whether anybody
    was there.
    """
    from supertokens_python.recipe.emailpassword.asyncio import sign_up
    from supertokens_python.recipe.passwordless.asyncio import signinup
    from supertokens_python.recipe.thirdparty.asyncio import (
        manually_create_or_update_user,
    )

    email = f"door-{uuid4().hex}@gmail.com"
    if recipe == "passwordless":
        await signinup("public", email=email, phone_number=None)
    elif recipe == "emailpassword":
        await sign_up("public", email, "DoorTest@12345")
    elif recipe == "thirdparty":
        await manually_create_or_update_user(
            "public", "google", uuid4().hex, email, False
        )

    mailbox = Mailbox()
    _with_email_code_service(
        test_app, monkeypatch, _challenge_service(db_session, mailbox)
    )
    origin = settings.auth_frontend_url.rstrip("/")
    nonce = await _bind_browser(async_client, origin)

    answered = await async_client.post(
        "/auth/email-code/continue",
        json={"email": email, "nonce": nonce},
        headers={"origin": origin},
    )
    assert answered.status_code == 200, answered.text
    body = answered.json()
    assert body["method"] == expected, body
    if expected == "thirdparty":
        assert body["provider"] == "google"
    # Mail is sent on the code branch and nowhere else: a person who owns a
    # password must not be made to read their inbox to find that out.
    assert bool(mailbox.code) is (expected == "code")


async def test_continue_signs_in_the_account_whatsapp_made(
    async_client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    test_app,
) -> None:
    """The whole point, end to end: a chat signup reaching the web.

    The account here is exactly what `complete_verified_account` leaves behind
    for a WhatsApp signup -- one passwordless login method and no password --
    which is the shape that could not sign in anywhere before this.
    """
    from supertokens_python.recipe.passwordless.asyncio import signinup

    email = f"chat-{uuid4().hex}@gmail.com"
    created = await signinup("public", email=email, phone_number=None)
    mailbox = Mailbox()
    _with_email_code_service(
        test_app, monkeypatch, _challenge_service(db_session, mailbox)
    )
    origin = settings.auth_frontend_url.rstrip("/")
    nonce = await _bind_browser(async_client, origin)

    answered = await async_client.post(
        "/auth/email-code/continue",
        json={"email": email, "nonce": nonce},
        headers={"origin": origin},
    )
    assert answered.status_code == 200, answered.text
    assert answered.json()["method"] == "code"

    verified = await async_client.post(
        "/auth/email-code/verify",
        json={
            "challenge_id": answered.json()["challenge_id"],
            "nonce": nonce,
            "code": mailbox.code,
        },
        headers={"origin": origin},
    )
    assert verified.status_code == 200, verified.text
    assert any(
        "sAccessToken=" in value for value in verified.headers.get_list("set-cookie")
    )
    # The same canonical identity, not a second account beside it.
    me = await async_client.get("/users/me", headers={"rid": "session"})
    assert me.status_code == 200, me.text
    assert me.json()["id"] == created.user.id


async def test_continue_lets_a_corrected_address_past_the_binding_cooldown(
    async_client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    test_app,
) -> None:
    """Typing the wrong address must not cost sixty seconds.

    `start_challenge` holds its cooldown against the *binding*, not the address,
    so a second `/continue` from the same browser is refused however different
    the email is. Naming the challenge being walked away from retires it first,
    which is what makes "change email" an ordinary correction.
    """
    mailbox = Mailbox()
    _with_email_code_service(
        test_app, monkeypatch, _challenge_service(db_session, mailbox)
    )
    origin = settings.auth_frontend_url.rstrip("/")
    nonce = await _bind_browser(async_client, origin)

    first = await async_client.post(
        "/auth/email-code/continue",
        json={"email": f"typo-{uuid4().hex}@gmail.com", "nonce": nonce},
        headers={"origin": origin},
    )
    assert first.status_code == 200, first.text
    abandoned = first.json()["challenge_id"]

    refused = await async_client.post(
        "/auth/email-code/continue",
        json={"email": f"fixed-{uuid4().hex}@gmail.com", "nonce": nonce},
        headers={"origin": origin},
    )
    assert refused.status_code == 400, refused.text
    # `{"message": ...}`, not `{"detail": ...}`: every response from the main
    # app goes through the unified envelope in `core.api.exception_handlers`.
    assert "sixty seconds" in refused.json()["message"]
    assert refused.json()["code"] == "EMAIL_CHALLENGE_COOLDOWN"

    corrected = await async_client.post(
        "/auth/email-code/continue",
        json={
            "email": f"fixed-{uuid4().hex}@gmail.com",
            "nonce": nonce,
            "abandon_challenge_id": abandoned,
        },
        headers={"origin": origin},
    )
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["method"] == "code"
    assert corrected.json()["challenge_id"] != abandoned


async def test_continue_answers_a_suspended_account_without_sending_mail(
    async_client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    test_app,
) -> None:
    """A suspended account is shown the password field it can never satisfy.

    Deliberate: `sign_in_post` refuses it in words indistinguishable from a
    wrong password, so this discloses nothing that answering honestly would not
    -- and unlike routing it to a code, it puts no mail in the mailbox of an
    account somebody has switched off.
    """
    assert db_session.bind is not None
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    mailbox = Mailbox()
    _with_email_code_service(
        test_app, monkeypatch, _challenge_service(db_session, mailbox)
    )
    origin = settings.auth_frontend_url.rstrip("/")
    nonce = await _bind_browser(async_client, origin)

    email = f"suspended-{uuid4().hex}@gmail.com"
    opened = await async_client.post(
        "/auth/email-code/continue",
        json={"email": email, "nonce": nonce},
        headers={"origin": origin},
    )
    assert opened.status_code == 200, opened.text
    verified = await async_client.post(
        "/auth/email-code/verify",
        json={
            "challenge_id": opened.json()["challenge_id"],
            "nonce": nonce,
            "code": mailbox.code,
        },
        headers={"origin": origin},
    )
    assert verified.status_code == 200, verified.text

    async with sessions() as session:
        local = await session.scalar(select(User).where(User.email == email))
        assert local is not None
        local.is_active = False
        await session.commit()

    mailbox.code = ""
    fresh_nonce = await _bind_browser(async_client, origin)
    answered = await async_client.post(
        "/auth/email-code/continue",
        json={"email": email, "nonce": fresh_nonce},
        headers={"origin": origin},
    )
    assert answered.status_code == 200, answered.text
    assert answered.json()["method"] == "password"
    assert mailbox.code == ""


async def test_continue_requires_this_browser_and_lemmas_own_page(
    async_client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    test_app,
) -> None:
    mailbox = Mailbox()
    _with_email_code_service(
        test_app, monkeypatch, _challenge_service(db_session, mailbox)
    )
    origin = settings.auth_frontend_url.rstrip("/")
    nonce = await _bind_browser(async_client, origin)
    email = f"bound-{uuid4().hex}@gmail.com"

    elsewhere = await async_client.post(
        "/auth/email-code/continue",
        json={"email": email, "nonce": nonce},
        headers={"origin": "https://attacker.example"},
    )
    assert elsewhere.status_code == 403
    assert elsewhere.json()["code"] == "EMAIL_LOGIN_ORIGIN_NOT_ALLOWED"

    forged = await async_client.post(
        "/auth/email-code/continue",
        json={"email": email, "nonce": "x" * 43},
        headers={"origin": origin},
    )
    assert forged.status_code == 403
    assert forged.json()["code"] == "EMAIL_LOGIN_EXPIRED"
    assert mailbox.code == ""


async def test_continue_meters_the_method_lookup(
    async_client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    test_app,
) -> None:
    """The budget the real dependency applies, exercised once with it in place.

    The lane turns abuse protection off wholesale (`e2e_base` sets
    `auth_abuse_protection_enabled = False`), which makes every limiter in the
    process a no-op -- so the only way to see this one work is to switch it back
    on for the length of this test. Asserted on the per-address ceiling rather
    than the per-IP one so it cannot spend the shared client-IP allowance that
    the rest of the module runs on.
    """
    monkeypatch.setattr(settings, "auth_abuse_protection_enabled", True)
    mailbox = Mailbox()
    monkeypatch.setitem(
        test_app.dependency_overrides,
        email_login_controller.get_email_login_challenges,
        lambda: _challenge_service(db_session, mailbox),
    )
    origin = settings.auth_frontend_url.rstrip("/")
    email = f"metered-{uuid4().hex}@gmail.com"

    statuses = []
    for _ in range(12):
        nonce = await _bind_browser(async_client, origin)
        answered = await async_client.post(
            "/auth/email-code/continue",
            json={"email": email, "nonce": nonce},
            headers={"origin": origin},
        )
        statuses.append(answered.status_code)
        if answered.status_code == 429:
            assert answered.headers.get("Retry-After")
            assert answered.json()["code"] == "EMAIL_CODE_RATE_LIMITED"
            break

    assert 429 in statuses, statuses


async def test_password_reset_tells_a_chat_account_which_door_it_has(
    async_client: AsyncClient,
) -> None:
    """ "Forgot password" used to promise mail that could never be sent.

    A passwordless account has no password to reset, so Core mints no token and
    sends nothing -- while the page, unable to tell that from success, says to
    go and check an inbox that stays empty. For somebody who signed up on
    WhatsApp that was a dead end with no way out of it.
    """
    from supertokens_python.recipe.passwordless.asyncio import signinup

    email = f"noreset-{uuid4().hex}@gmail.com"
    await signinup("public", email=email, phone_number=None)

    answered = await async_client.post(
        "/st/auth/user/password/reset/token",
        json={"formFields": [{"id": "email", "value": email}]},
    )
    assert answered.status_code == 200, answered.text
    body = answered.json()
    assert body["status"] == "GENERAL_ERROR", body
    assert "email-code login" in body["message"], body
