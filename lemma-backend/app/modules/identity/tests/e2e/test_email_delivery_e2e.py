"""Whether email works, asked by the person who set it up.

Through the real routes and the real sender. Only the SMTP connection is
stood in for: a test cannot reach a mail provider, and what is under test is
what the endpoint does with the provider's answer -- above all that a refusal
reaches the screen as something to check, never as the provider's own words.
"""

from __future__ import annotations

import logging
from email.message import Message
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import SecretStr

from app.core.config import settings
from app.modules.identity.domain.organization_entities import OrganizationRole

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

PATH = "/users/me/email-delivery"


class _Relay:
    """An SMTP server that accepts, or refuses with a message nobody should see."""

    sent_to: list[str] = []
    refuse = False

    def __init__(self, **_kwargs: object) -> None:
        pass

    async def __aenter__(self) -> _Relay:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def login(self, _user: str, _password: str) -> None:
        if self.refuse:
            raise OSError("535 5.7.8 relay.internal.example.test rejected key re_***")

    async def send_message(self, message: Message) -> None:
        type(self).sent_to.append(str(message["To"]))


@pytest.fixture
def resend_configured(monkeypatch: pytest.MonkeyPatch) -> type[_Relay]:
    monkeypatch.setattr(settings, "email_transport", "smtp")
    monkeypatch.setattr(settings, "smtp_user", None)
    monkeypatch.setattr(settings, "smtp_password", None)
    monkeypatch.setattr(settings, "smtp_from_email", None)
    monkeypatch.setattr(settings, "resend_api_key", SecretStr("re_test_key"))
    monkeypatch.setattr(settings, "resend_from_email", "lemma@example.com")
    monkeypatch.setattr(_Relay, "sent_to", [])
    monkeypatch.setattr(_Relay, "refuse", False)
    # At the library, which the sender looks up per send: the sender and the
    # endpoint are the subject here, the wire is not.
    monkeypatch.setattr("aiosmtplib.SMTP", _Relay)
    return _Relay


async def test_a_hosted_server_does_not_have_these_routes(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "environment", "development")

    assert (await authenticated_client.get(PATH)).status_code == 404
    assert (await authenticated_client.post(PATH + "/test")).status_code == 404


async def test_the_local_spool_is_not_email_that_works(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Desktop's default: mail is written to disk, and nobody receives it."""
    monkeypatch.setattr(settings, "email_transport", "filesystem")

    status = await authenticated_client.get(PATH)
    assert status.status_code == 200, status.text
    assert status.json() == {"configured": False}

    test = await authenticated_client.post(PATH + "/test")
    assert test.status_code == 200, test.text
    assert test.json()["ok"] is False
    assert "isn't set up" in test.json()["message"]


async def test_a_half_configured_provider_says_what_is_missing(
    authenticated_client: AsyncClient,
    resend_configured: type[_Relay],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "resend_from_email", None)

    assert (await authenticated_client.get(PATH)).json() == {"configured": False}
    test = (await authenticated_client.post(PATH + "/test")).json()
    assert test["ok"] is False
    assert "RESEND_FROM_EMAIL" in test["message"]
    assert resend_configured.sent_to == []


async def test_a_working_provider_mails_the_caller_and_nobody_else(
    authenticated_client: AsyncClient,
    fixed_test_user: dict[str, str],
    resend_configured: type[_Relay],
) -> None:
    assert (await authenticated_client.get(PATH)).json() == {"configured": True}

    test = await authenticated_client.post(PATH + "/test")

    assert test.status_code == 200, test.text
    assert test.json()["ok"] is True
    assert fixed_test_user["email"] in test.json()["message"]
    assert resend_configured.sent_to == [fixed_test_user["email"]]


async def test_a_refusing_provider_is_reported_without_its_own_words(
    authenticated_client: AsyncClient, resend_configured: type[_Relay]
) -> None:
    resend_configured.refuse = True

    test = await authenticated_client.post(PATH + "/test")

    assert test.status_code == 200, test.text
    body = test.json()
    assert body["ok"] is False
    assert "Check the host" in body["message"]
    for leaked in ("535", "relay.internal", "re_"):
        assert leaked not in body["message"]


async def test_test_emails_are_rate_limited_when_abuse_controls_are_on(
    authenticated_client: AsyncClient,
    resend_configured: type[_Relay],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sharing turns the abuse controls on, and visitors can reach this then."""
    monkeypatch.setattr(settings, "auth_abuse_protection_enabled", True)

    answers = [
        (await authenticated_client.post(PATH + "/test")).status_code for _ in range(6)
    ]

    assert answers == [200] * 5 + [429]


async def test_an_invitation_says_whether_it_was_emailed_and_carries_its_link(
    authenticated_client: AsyncClient,
    fixed_test_org: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without email, the link is the only way the invitee hears of it."""
    monkeypatch.setattr(settings, "email_transport", "filesystem")
    org_id = fixed_test_org["id"]

    invite = await authenticated_client.post(
        f"/organizations/{org_id}/invitations",
        json={"email": f"guest-{uuid4().hex[:10]}@example.com", "role": "ORG_MEMBER"},
    )

    assert invite.status_code == 201, invite.text
    created = invite.json()
    assert created["emailed"] is False
    assert created["accept_url"].endswith(f"/invitations/{created['id']}/accept")

    listed = await authenticated_client.get(f"/organizations/{org_id}/invitations")
    assert listed.status_code == 200, listed.text
    [item] = [row for row in listed.json()["items"] if row["id"] == created["id"]]
    assert item["accept_url"] == created["accept_url"]
    assert item["emailed"] is False


@pytest.fixture
def no_email(monkeypatch: pytest.MonkeyPatch) -> None:
    """A server with no provider at all -- not the spool, which does "send"."""
    monkeypatch.setattr(settings, "email_transport", "smtp")
    monkeypatch.setattr(settings, "smtp_user", None)
    monkeypatch.setattr(settings, "smtp_password", None)
    monkeypatch.setattr(settings, "smtp_from_email", None)
    monkeypatch.setattr(settings, "resend_api_key", None)
    monkeypatch.setattr(settings, "resend_from_email", None)


async def test_without_email_an_email_code_is_refused_before_it_is_minted(
    async_client: AsyncClient, test_app: FastAPI, no_email: None
) -> None:
    from app.modules.identity.api.controllers import email_login_controller

    async def no_limits(_request: object, _email: str) -> None:
        return None

    test_app.dependency_overrides[email_login_controller.get_method_lookup_limits] = (
        lambda: no_limits
    )
    try:
        origin = settings.auth_frontend_url.rstrip("/")
        bound = await async_client.post(
            "/auth/email-code/browser", json={}, headers={"origin": origin}
        )
        assert bound.status_code == 200, bound.text
        answered = await async_client.post(
            "/auth/email-code/continue",
            json={
                "email": f"new-{uuid4().hex[:10]}@gmail.com",
                "nonce": bound.json()["nonce"],
            },
            headers={"origin": origin},
        )
    finally:
        test_app.dependency_overrides.pop(
            email_login_controller.get_method_lookup_limits, None
        )

    assert answered.status_code == 400, answered.text
    assert answered.json()["code"] == "EMAIL_NOT_CONFIGURED"
    assert "Use a password instead" in answered.json()["message"]


async def test_without_email_a_password_reset_says_so_instead_of_check_your_inbox(
    async_client: AsyncClient, fixed_test_user: dict[str, str], no_email: None
) -> None:
    from app.modules.identity.infrastructure.supertokens_auth.override_email_password_apis import (
        PASSWORD_RESET_NOT_CONFIGURED_MESSAGE,
    )

    for address in (fixed_test_user["email"], f"nobody-{uuid4().hex[:8]}@gmail.com"):
        answered = await async_client.post(
            "/st/auth/user/password/reset/token",
            json={"formFields": [{"id": "email", "value": address}]},
        )
        assert answered.status_code == 200, answered.text
        assert answered.json() == {
            "status": "GENERAL_ERROR",
            "message": PASSWORD_RESET_NOT_CONFIGURED_MESSAGE,
        }


async def test_mail_that_cannot_be_sent_is_a_warning_that_says_why(
    no_email: None, caplog: pytest.LogCaptureFixture
) -> None:
    """At INFO a debug line is never formatted, so the drop was invisible."""
    from app.modules.identity.infrastructure.adapters.email_adapter import (
        SmtpIdentityEmailAdapter,
    )

    with caplog.at_level(logging.WARNING):
        sent = await SmtpIdentityEmailAdapter().send_invitation_email(
            to_email=f"guest-{uuid4().hex[:8]}@gmail.com",
            organization_name="Acme",
            inviter_email="owner@example.com",
            role=OrganizationRole.ORG_MEMBER,
            accept_url="https://lemma.work/invitations/test/accept",
        )

    assert sent is False
    [entry] = [
        record.msg
        for record in caplog.records
        if isinstance(record.msg, dict)
        and record.msg.get("event") == "identity.email.not_sent"
    ]
    assert entry["kind"] == "invitation"
    assert "RESEND_API_KEY" in entry["reason"]
