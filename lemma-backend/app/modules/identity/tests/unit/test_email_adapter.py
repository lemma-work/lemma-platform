import pytest

from app.modules.identity.domain.organization_entities import OrganizationRole
from app.modules.identity.infrastructure.adapters.email_adapter import (
    SmtpIdentityEmailAdapter,
)


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
        organization_name="LEDflex",
        inviter_email="lemma@lemma.work",
        role=OrganizationRole.ORG_MEMBER,
        accept_url="https://lemma.work/invitations/test/accept",
        pod_name="LEDflex Support AI",
        pod_description=(
            "Ask product questions, find datasheets and certificates, and track "
            "support tickets from one place."
        ),
    )

    html = sent["html_content"]
    assert result is True
    assert sent["subject"] == "Invitation to join pod LEDflex Support AI"
    assert "Pod invitation" in html
    assert "Use LEDflex Support AI." in html
    assert "<strong" in html and "Lemma" in html
    assert "Open LEDflex Support AI" in html
    assert "Ask product questions" in html
    assert "This invite was sent to" in html
    assert "pc@example.com" in html


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
        organization_name="Acme",
        inviter_email="owner@acme.test",
        role=OrganizationRole.ORG_MEMBER,
        accept_url="https://lemma.work/invitations/test/accept",
    )

    html = sent["html_content"]
    assert "Workspace invitation" in html
    assert "Join Acme on Lemma." in html
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
