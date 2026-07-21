import pytest

from app.modules.identity.domain.organization_entities import OrganizationRole
from app.modules.identity.infrastructure.adapters import email_adapter
from app.modules.identity.infrastructure.adapters.email_adapter import (
    SmtpIdentityEmailAdapter,
)
from app.modules.identity.services.email_policy import (
    EmailPolicyError,
    EmailPolicyRejection,
)


@pytest.mark.asyncio
async def test_email_adapter_refuses_policy_rejected_recipient(monkeypatch):
    adapter = SmtpIdentityEmailAdapter()

    async def reject(_email):
        raise EmailPolicyError(
            EmailPolicyRejection("ACCOUNT_INACTIVE", "local-user-state")
        )

    monkeypatch.setattr(email_adapter, "validate_outbound_email", reject)
    monkeypatch.setattr(
        email_adapter.EmailSender,
        "from_settings",
        lambda: pytest.fail("sender must not be created for a rejected recipient"),
    )

    sent = await adapter._send(
        to_email="inactive@example.com",
        subject="Subject",
        html_content="Body",
        text_content="Body",
    )

    assert sent is False


@pytest.mark.asyncio
async def test_pod_invitation_email_uses_pod_layout(monkeypatch):
    adapter = SmtpIdentityEmailAdapter()
    sent: dict[str, str] = {}

    async def capture_send(**kwargs):
        sent.update(kwargs)
        return True

    monkeypatch.setattr(adapter, "_send", capture_send)

    result = await adapter.send_invitation_email(
        to_email="pc@example.com",
        organization_name="Acme",
        inviter_email="lemma@lemma.work",
        role=OrganizationRole.ORG_MEMBER,
        accept_url="https://lemma.work/invitations/test/accept",
        pod_name="Acme Support AI",
        pod_description=(
            "Ask product questions, find datasheets and certificates, and track "
            "support tickets from one place."
        ),
    )

    html = sent["html_content"]
    assert result is True
    assert sent["subject"] == "Invitation to join pod Acme Support AI"
    assert "Pod invitation" in html
    assert "Use Acme Support AI." in html
    assert "lemma" in html.lower()
    assert "Open Acme Support AI" in html
    assert "Ask product questions" in html
    assert "This invitation was sent to" in html
    assert "pc@example.com" in html


@pytest.mark.asyncio
async def test_pod_invitation_humanizes_partially_formatted_stored_name(monkeypatch):
    adapter = SmtpIdentityEmailAdapter()
    sent: dict[str, str] = {}

    async def capture_send(**kwargs):
        sent.update(kwargs)
        return True

    monkeypatch.setattr(adapter, "_send", capture_send)

    await adapter.send_invitation_email(
        to_email="pc@example.com",
        organization_name="Codex Email Workspace",
        inviter_email="anukul@lemma.work",
        role=OrganizationRole.ORG_MEMBER,
        accept_url="https://lemma.work/invitations/test/accept",
        pod_name="Email-test_pod Pod",
    )

    assert sent["subject"] == "Invitation to join pod Email Test Pod"
    assert "Use Email Test Pod." in sent["html_content"]
    assert "Open Email Test Pod" in sent["html_content"]
    assert "Email-test_pod" not in sent["html_content"]
    assert "Email-test_pod" not in sent["text_content"]
    assert "Pod Pod" not in sent["html_content"]


@pytest.mark.asyncio
async def test_workspace_invitation_email_uses_shared_layout(monkeypatch):
    adapter = SmtpIdentityEmailAdapter()
    sent: dict[str, str] = {}

    async def capture_send(**kwargs):
        sent.update(kwargs)
        return True

    monkeypatch.setattr(adapter, "_send", capture_send)

    await adapter.send_invitation_email(
        to_email="pc@example.com",
        organization_name="acme_corp",
        inviter_email="owner@acme.test",
        role=OrganizationRole.ORG_MEMBER,
        accept_url="https://lemma.work/invitations/test/accept",
    )

    html = sent["html_content"]
    assert "Workspace invitation" in html
    assert sent["subject"] == "Invitation to join Acme Corp"
    assert "Join Acme Corp on Lemma." in html
    assert "Accept invitation" in html
    assert "Shared agents, data, and workspace apps" in html


@pytest.mark.asyncio
async def test_pod_join_request_email_humanizes_and_includes_requester(monkeypatch):
    adapter = SmtpIdentityEmailAdapter()
    sent: dict[str, str] = {}

    async def capture_send(**kwargs):
        sent.update(kwargs)
        return True

    monkeypatch.setattr(adapter, "_send", capture_send)

    result = await adapter.send_pod_join_request_email(
        to_email="admin@acme.com",
        pod_name="support_app",
        organization_name="acme_corp",
        requester_name="Jane Doe",
        requester_email="jane@acme.com",
    )

    assert result is True
    # Machine-style names are humanized in the subject and body.
    assert sent["subject"] == "Request to join Support App"
    assert "Support App" in sent["html_content"]
    assert "Acme Corp" in sent["html_content"]
    # The requester is identified for the admin.
    assert "Jane Doe" in sent["html_content"]
    assert "jane@acme.com" in sent["html_content"]
    assert "Jane Doe" in sent["text_content"]


@pytest.mark.asyncio
async def test_welcome_email_uses_shared_layout_and_open_lemma_action(monkeypatch):
    adapter = SmtpIdentityEmailAdapter()
    sent: dict[str, str] = {}

    async def capture_send(**kwargs):
        sent.update(kwargs)
        return True

    monkeypatch.setattr(adapter, "_send", capture_send)

    await adapter.send_signup_welcome_email(
        to_email="jane@example.com",
        first_name="Jane",
    )

    assert sent["subject"] == "Welcome to Lemma"
    assert "Welcome to Lemma, Jane." in sent["html_content"]
    assert "Open Lemma" in sent["html_content"]
    assert "Open Lemma:" in sent["text_content"]


@pytest.mark.asyncio
async def test_invitation_accepted_email_humanizes_workspace(monkeypatch):
    adapter = SmtpIdentityEmailAdapter()
    sent: dict[str, str] = {}

    async def capture_send(**kwargs):
        sent.update(kwargs)
        return True

    monkeypatch.setattr(adapter, "_send", capture_send)

    await adapter.send_invitation_accepted_email(
        to_email="jane@example.com",
        organization_name="customer_success-team",
        role=OrganizationRole.ORG_MEMBER,
    )

    assert sent["subject"] == "You joined Customer Success Team"
    assert "Customer Success Team" in sent["html_content"]
    assert "Active workspace: Customer Success Team" in sent["text_content"]
