from uuid import uuid4

import pytest

from app.modules.identity.domain.organization_entities import OrganizationRole
from app.modules.identity.infrastructure.adapters import email_adapter
from app.core.email.transactional import render_transactional_email
from app.modules.identity.infrastructure.adapters.email_adapter import (
    SmtpIdentityEmailAdapter,
    invitation_note,
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
        kind="test",
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
    assert sent["subject"] == "Invitation to join Acme Support AI"
    assert "Work with Acme Support AI." in html
    assert "a Lemma at Acme" in html
    assert "pod" not in sent["subject"].lower() and "Pod invitation" not in html
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

    assert sent["subject"] == "Invitation to join Email Test Pod"
    assert "Work with Email Test Pod." in sent["html_content"]
    assert "Open Email Test Pod" in sent["html_content"]
    assert "Email-test_pod" not in sent["html_content"]
    assert "Email-test_pod" not in sent["text_content"]
    assert "Pod Pod" not in sent["html_content"]


def test_an_invitation_quotes_the_inviters_note_and_escapes_it():
    """The note is the inviter's own words: quoted after the invite, escaped
    where the email draws it, and left out when it says nothing."""
    said = invitation_note("Ada", "  You answered most of these <b>customers</b>. ")
    assert said == (
        "Ada wrote: \u201cYou answered most of these <b>customers</b>.\u201d",
    )
    rendered = render_transactional_email(
        preheader="p", eyebrow="e", heading="h", body=("Invited.", *said)
    )
    assert "You answered most of these" in rendered.text
    assert "&lt;b&gt;customers&lt;/b&gt;" in rendered.html
    assert "<b>customers</b>" not in rendered.html
    assert invitation_note("Ada", "   ") == ()
    assert invitation_note("Ada", None) == ()


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
    assert "Invitation" in html
    assert sent["subject"] == "Invitation to join Acme Corp"
    assert "Join Acme Corp on Lemma." in html
    assert "Accept invitation" in html
    assert "Lemmas that own a piece of the work" in html


@pytest.mark.asyncio
async def test_pod_join_request_email_humanizes_and_includes_requester(monkeypatch):
    adapter = SmtpIdentityEmailAdapter()
    sent: dict[str, str] = {}
    pod_id = uuid4()

    async def capture_send(**kwargs):
        sent.update(kwargs)
        return True

    monkeypatch.setattr(adapter, "_send", capture_send)

    result = await adapter.send_pod_join_request_email(
        to_email="admin@acme.com",
        pod_id=pod_id,
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
    # The button opens the Lemma's People settings, where the request is.
    assert f"/l/{pod_id}/settings/people" in sent["html_content"]
    assert "pod" not in sent["text_content"].lower()


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
    assert "Organization: Customer Success Team" in sent["text_content"]


async def _capture(monkeypatch, adapter: SmtpIdentityEmailAdapter) -> dict[str, str]:
    sent: dict[str, str] = {}

    async def capture_send(**kwargs):
        sent.update(kwargs)
        return True

    monkeypatch.setattr(adapter, "_send", capture_send)
    return sent


@pytest.mark.asyncio
async def test_join_approved_email_opens_the_lemma(monkeypatch):
    adapter = SmtpIdentityEmailAdapter()
    sent = await _capture(monkeypatch, adapter)
    pod_id = uuid4()

    await adapter.send_pod_join_decided_email(
        to_email="jane@acme.com",
        pod_id=pod_id,
        pod_name="support_app",
        organization_name="acme_corp",
        approved=True,
        decided_by_name="Ada Lovelace",
    )

    assert sent["subject"] == "You're in Support App"
    assert "Ada Lovelace let you into Support App" in sent["text_content"]
    assert f"/l/{pod_id}" in sent["html_content"]
    assert "pod" not in sent["text_content"].lower()


@pytest.mark.asyncio
async def test_join_declined_email_says_so_and_offers_no_door(monkeypatch):
    adapter = SmtpIdentityEmailAdapter()
    sent = await _capture(monkeypatch, adapter)
    pod_id = uuid4()

    await adapter.send_pod_join_decided_email(
        to_email="jane@acme.com",
        pod_id=pod_id,
        pod_name="support_app",
        organization_name="acme_corp",
        approved=False,
        decided_by_name="",
    )

    assert sent["subject"] == "Your request to join Support App"
    assert "was declined" in sent["text_content"]
    assert "Someone who runs it declined it" in sent["text_content"]
    assert f"/l/{pod_id}" not in sent["html_content"]


@pytest.mark.asyncio
async def test_member_added_email_names_who_added_you(monkeypatch):
    adapter = SmtpIdentityEmailAdapter()
    sent = await _capture(monkeypatch, adapter)
    pod_id = uuid4()

    await adapter.send_pod_member_added_email(
        to_email="jane@acme.com",
        pod_id=pod_id,
        pod_name="support_app",
        organization_name="acme_corp",
        added_by_name="Ada Lovelace",
    )

    assert sent["subject"] == "Ada Lovelace added you to Support App"
    assert f"/l/{pod_id}" in sent["html_content"]
