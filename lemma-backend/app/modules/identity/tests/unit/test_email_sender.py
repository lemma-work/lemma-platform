from __future__ import annotations

import pytest

from app.core.email import email_sender
from app.core.email.email_sender import EmailDeliveryError, EmailSender


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
