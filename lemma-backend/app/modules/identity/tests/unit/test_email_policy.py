from __future__ import annotations

from types import SimpleNamespace

import pytest
from email_validator import EmailUndeliverableError

from app.core.config import settings
from app.modules.identity.services import email_policy
from app.modules.identity.services.email_policy import EmailPolicyError


@pytest.mark.asyncio
async def test_email_policy_normalizes_and_rejects_invalid_syntax(monkeypatch):
    monkeypatch.setattr(settings, "auth_email_deliverability_checks_enabled", False)

    assert (
        await email_policy.validate_auth_email("Person@Example.COM")
        == "person@example.com"
    )
    with pytest.raises(EmailPolicyError) as exc:
        await email_policy.validate_auth_email("not-an-email")
    assert exc.value.rejection.reason == "INVALID_SYNTAX"


@pytest.mark.asyncio
async def test_email_policy_rejects_disposable_domain_with_allowlist(monkeypatch):
    monkeypatch.setattr(settings, "auth_email_deliverability_checks_enabled", False)
    monkeypatch.setattr(settings, "auth_disposable_email_domains_enabled", True)
    monkeypatch.setattr(settings, "auth_disposable_email_allowlist", [])

    with pytest.raises(EmailPolicyError) as exc:
        await email_policy.validate_auth_email("person@mailinator.com")
    assert exc.value.rejection.reason == "DISPOSABLE_DOMAIN"

    monkeypatch.setattr(settings, "auth_disposable_email_allowlist", ["mailinator.com"])
    assert (
        await email_policy.validate_auth_email("person@mailinator.com")
        == "person@mailinator.com"
    )


@pytest.mark.asyncio
async def test_dns_invalid_is_permanent_but_unexpected_dns_failure_is_retryable(
    monkeypatch,
):
    monkeypatch.setattr(settings, "auth_email_deliverability_checks_enabled", True)
    monkeypatch.setattr(settings, "auth_disposable_email_domains_enabled", False)

    async def invalid_domain(_email):
        raise EmailUndeliverableError("The domain name does not exist")

    monkeypatch.setattr(email_policy, "_validate_with_dns", invalid_domain)
    with pytest.raises(EmailPolicyError) as exc:
        await email_policy.validate_auth_email("person@missing.example")
    assert exc.value.rejection.reason == "INVALID_DOMAIN"
    assert exc.value.rejection.permanent is True

    async def temporary_failure(_email):
        raise TimeoutError("resolver timed out")

    monkeypatch.setattr(email_policy, "_validate_with_dns", temporary_failure)
    assert (
        await email_policy.validate_auth_email("person@example.com")
        == "person@example.com"
    )


@pytest.mark.asyncio
async def test_email_policy_requires_explicit_mx_without_deactivation_evidence(
    monkeypatch,
):
    monkeypatch.setattr(settings, "auth_email_deliverability_checks_enabled", True)
    monkeypatch.setattr(settings, "auth_disposable_email_domains_enabled", False)

    async def a_record_fallback(_email):
        return SimpleNamespace(
            normalized="person@legacy.example",
            mx_fallback_type="A",
        )

    monkeypatch.setattr(email_policy, "_validate_with_dns", a_record_fallback)
    with pytest.raises(EmailPolicyError) as exc:
        await email_policy.validate_auth_email("person@legacy.example")

    assert exc.value.rejection.reason == "MISSING_MX"
    assert exc.value.rejection.evidence_source == "dns"
    assert exc.value.rejection.permanent is False


@pytest.mark.asyncio
async def test_only_explicit_null_mx_is_deactivation_safe(monkeypatch):
    monkeypatch.setattr(settings, "auth_email_deliverability_checks_enabled", True)
    monkeypatch.setattr(settings, "auth_disposable_email_domains_enabled", False)

    async def null_mx(_email):
        raise EmailUndeliverableError(
            "The domain name null-mx.example does not accept email."
        )

    monkeypatch.setattr(email_policy, "_validate_with_dns", null_mx)
    with pytest.raises(EmailPolicyError) as explicit:
        await email_policy.validate_auth_email("person@null-mx.example")
    assert explicit.value.rejection.reason == "NULL_MX"
    assert explicit.value.rejection.permanent is True

    async def no_mail_records(_email):
        try:
            raise OSError("no MX, A, or AAAA records")
        except OSError as cause:
            raise EmailUndeliverableError(
                "The domain name no-mail.example does not accept email."
            ) from cause

    monkeypatch.setattr(email_policy, "_validate_with_dns", no_mail_records)
    with pytest.raises(EmailPolicyError) as ambiguous:
        await email_policy.validate_auth_email("person@no-mail.example")
    assert ambiguous.value.rejection.reason == "UNDELIVERABLE_DOMAIN"
    assert ambiguous.value.rejection.permanent is False


@pytest.mark.asyncio
async def test_outbound_email_refuses_deactivated_local_user(monkeypatch):
    monkeypatch.setattr(settings, "auth_email_deliverability_checks_enabled", False)

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def scalar(self, _query):
            return SimpleNamespace(is_active=False, is_deleted=False)

    monkeypatch.setattr(email_policy, "async_session_maker", lambda: _Session())

    with pytest.raises(EmailPolicyError) as exc:
        await email_policy.validate_outbound_email("person@example.com")

    assert exc.value.rejection.reason == "ACCOUNT_INACTIVE"
