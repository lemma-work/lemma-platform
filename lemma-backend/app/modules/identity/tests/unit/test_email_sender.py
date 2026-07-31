from __future__ import annotations

import json
import stat

import pytest
from pydantic import SecretStr

from app.core.config import settings
from app.core.email import email_sender
from app.core.email.email_sender import (
    EmailDeliveryError,
    EmailNotConfiguredError,
    EmailSender,
)


class _FakeSMTP:
    kwargs: dict = {}
    fail = False

    def __init__(self, **kwargs):
        type(self).kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def login(self, _user, _password):
        if self.fail:
            raise OSError("SMTP unavailable")

    async def send_message(self, _message):
        return None


def _sender(port: int) -> EmailSender:
    return EmailSender(
        smtp_host="smtp.example.com",
        smtp_port=port,
        smtp_user="user",
        smtp_password="password",
        from_email="no-reply@example.com",
        use_tls=True,
    )


@pytest.mark.asyncio
async def test_smtp_uses_starttls_for_submission_and_implicit_tls_for_465(monkeypatch):
    monkeypatch.setattr(email_sender.aiosmtplib, "SMTP", _FakeSMTP)

    assert await _sender(587).send_email("person@example.com", "Subject", "Body")
    assert _FakeSMTP.kwargs["start_tls"] is True
    assert _FakeSMTP.kwargs["use_tls"] is False

    assert await _sender(465).send_email("person@example.com", "Subject", "Body")
    assert _FakeSMTP.kwargs["start_tls"] is False
    assert _FakeSMTP.kwargs["use_tls"] is True


@pytest.mark.asyncio
async def test_security_email_failure_is_not_silently_swallowed(monkeypatch):
    monkeypatch.setattr(email_sender.aiosmtplib, "SMTP", _FakeSMTP)
    _FakeSMTP.fail = True
    try:
        with pytest.raises(EmailDeliveryError):
            await _sender(587).send_email(
                "person@example.com",
                "Subject",
                "Body",
                raise_on_failure=True,
            )
    finally:
        _FakeSMTP.fail = False


@pytest.mark.asyncio
async def test_filesystem_transport_uses_owner_only_spool(tmp_path):
    sender = EmailSender(
        smtp_host="",
        smtp_port=587,
        smtp_user="",
        smtp_password="",
        from_email="no-reply@example.com",
        transport="filesystem",
        output_dir=str(tmp_path / "emails"),
    )

    assert await sender.send_email(
        "person@example.com",
        "Reset your password",
        '<a href="https://example.com/reset?token=secret">Reset</a>',
        "Reset: https://example.com/reset?token=secret",
        raise_on_failure=True,
    )

    output_dir = tmp_path / "emails"
    message_path = next(output_dir.glob("*.json"))
    assert stat.S_IMODE(output_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(message_path.stat().st_mode) == 0o600
    assert json.loads(message_path.read_text(encoding="utf-8"))["subject"] == (
        "Reset your password"
    )


def test_filesystem_transport_is_rejected_in_production(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")

    with pytest.raises(EmailNotConfiguredError, match="not permitted"):
        EmailSender(
            smtp_host="",
            smtp_port=587,
            smtp_user="",
            smtp_password="",
            from_email="no-reply@example.com",
            transport="filesystem",
        )


def test_resend_key_configures_documented_smtp_relay(monkeypatch):
    monkeypatch.setattr(settings, "email_transport", "smtp")
    monkeypatch.setattr(settings, "smtp_user", None)
    monkeypatch.setattr(settings, "smtp_password", None)
    monkeypatch.setattr(settings, "smtp_from_email", None)
    monkeypatch.setattr(settings, "resend_api_key", SecretStr("re_test_key"))
    monkeypatch.setattr(settings, "resend_from_email", "local@ops.asur.work")

    sender = EmailSender.from_settings()

    assert sender.smtp_host == "smtp.resend.com"
    assert sender.smtp_port == 465
    assert sender.smtp_user == "resend"
    assert sender.smtp_password == "re_test_key"
    assert sender.from_email == "local@ops.asur.work"
    assert sender.use_tls is True


def test_explicit_smtp_configuration_takes_precedence_over_resend(monkeypatch):
    monkeypatch.setattr(settings, "email_transport", "smtp")
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_port", 587)
    monkeypatch.setattr(settings, "smtp_user", "explicit-user")
    monkeypatch.setattr(settings, "smtp_password", "explicit-password")
    monkeypatch.setattr(settings, "smtp_from_email", "mail@example.com")
    monkeypatch.setattr(settings, "resend_api_key", SecretStr("re_test_key"))

    sender = EmailSender.from_settings()

    assert sender.smtp_host == "smtp.example.com"
    assert sender.smtp_port == 587
    assert sender.smtp_user == "explicit-user"
    assert sender.from_email == "mail@example.com"
