"""Who a fallback reply is allowed to reach, and how often.

The messages themselves are pinned by ``test_fallback_reply_urls``. These are
the two questions asked before one is sent at all: is anybody really at that
address, and have we already told them.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.config import settings
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ConversationType,
    ParsedInboundSurfaceEvent,
    SurfaceConfig,
    SurfaceMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.ingress_context import SurfaceReplyContext
from app.modules.agent_surfaces.platforms.email_authentication import (
    EmailAuthenticationVerdict,
)
from app.modules.agent_surfaces.platforms.slack.service import SlackPlatformService
from app.modules.agent_surfaces.platforms.whatsapp.adapter import WhatsAppSurfaceAdapter
from app.modules.agent_surfaces.services.fallback_reply_service import (
    deliver_fallback_reply,
    nonmember_context,
    private_reply_metadata,
    sender_can_be_answered,
    unresolved_sender_context,
)
from slack_sdk.web.async_client import AsyncWebClient

pytestmark = pytest.mark.unit


def _email_event(verdict: str | None) -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform="RESEND",
        conversation_type=ConversationType.EXTERNAL_DM,
        external_thread_id="thread-1",
        external_message_id="msg-1",
        sender_email="stranger@example.test",
        sender_external_user_id="stranger@example.test",
        sender_authentication=verdict,
        message_text="hello",
        is_dm=True,
    )


def _email_surface() -> AgentSurfaceEntity:
    return AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        name="mailbox",
        agent_id=uuid4(),
        surface_type=SurfacePlatform.RESEND,
        mode=SurfaceMode.EMAIL,
        account_id=uuid4(),
        config=SurfaceConfig(),
        is_active=True,
    )


@pytest.mark.parametrize(
    "verdict",
    [EmailAuthenticationVerdict.FAIL, EmailAuthenticationVerdict.UNKNOWN, None],
)
def test_an_unvouched_email_sender_is_not_written_to(verdict) -> None:
    """The address is probably not the sender's, so a reply mails a stranger.

    This is the whole backscatter defect: refusing to *resolve* an unvouched
    ``From:`` and then *replying* to it turned the safety check into the thing
    that sent the message.
    """
    assert sender_can_be_answered(_email_event(verdict)) is False
    assert (
        unresolved_sender_context(
            surface=_email_surface(),
            parsed=_email_event(verdict),
            adapter=SimpleNamespace(unresolved_sender_reply=lambda event: None),
            agent_display_name="Lem",
        )
        is None
    )


def test_a_vouched_email_sender_without_an_account_still_gets_help() -> None:
    """Silence is for forgeries, not for people the mail service vouched for."""
    context = unresolved_sender_context(
        surface=_email_surface(),
        parsed=_email_event(EmailAuthenticationVerdict.PASS),
        adapter=SimpleNamespace(unresolved_sender_reply=lambda event: None),
        agent_display_name="Lem",
    )
    assert context is not None
    assert context.reply_kind == "signup"


def test_a_chat_sender_is_never_doubted() -> None:
    """Only email lets the sender write their own name on the envelope."""
    telegram = ParsedInboundSurfaceEvent(
        platform="TELEGRAM",
        conversation_type=ConversationType.EXTERNAL_DM,
        external_thread_id="t",
        sender_authentication=None,
        message_text="hello",
        is_dm=True,
    )
    assert sender_can_be_answered(telegram) is True


def test_whatsapp_names_the_number_it_did_not_recognise(monkeypatch) -> None:
    """ "Please sign up" is wrong for the people who mostly hit this.

    Meta signed the payload that carried the number, so it can be stated as
    fact — and it is the one fact that lets someone fix this themselves.
    """
    monkeypatch.setattr(settings, "frontend_url", "https://app.example.test/")
    reply = WhatsAppSurfaceAdapter().unresolved_sender_reply(
        ParsedInboundSurfaceEvent(
            platform="WHATSAPP",
            conversation_type=ConversationType.EXTERNAL_DM,
            external_thread_id="14155552671",
            sender_phone="14155552671",
            message_text="hello",
            is_dm=True,
        )
    )
    assert reply is not None
    message, _metadata = reply
    assert "+14155552671" in message
    assert "https://app.example.test/profile" in message
    # Saying either answer would tell any sender whether a number is registered.
    assert "sign up" not in message.lower()


async def test_a_stranger_is_told_once_per_window_not_once_per_message() -> None:
    """Fifty messages was fifty replies, from a number every pod shares."""
    adapter = SimpleNamespace(send_message=AsyncMock(return_value=None))
    context = SurfaceReplyContext(
        platform=SurfacePlatform.WHATSAPP,
        surface_id=uuid4(),
        reply_kind="signup",
        reply_message="here is how to get access",
        event=ParsedInboundSurfaceEvent(
            platform="WHATSAPP",
            conversation_type=ConversationType.EXTERNAL_DM,
            external_thread_id="14155552671",
            sender_external_user_id="14155552671",
            message_text="hello",
            is_dm=True,
        ),
    )
    store = SimpleNamespace(claim_stranger_reply=AsyncMock(side_effect=[True, False]))

    for _ in range(2):
        await deliver_fallback_reply(
            adapter=adapter,
            context=context,
            credentials={"access_token": "token"},
            event_dedup_store=store,
        )

    assert adapter.send_message.await_count == 1


def _slack_channel_event() -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform="SLACK",
        conversation_type=ConversationType.EXTERNAL_GROUP,
        external_channel_id="C123",
        external_thread_id="C123",
        sender_external_user_id="U456",
        message_text="@lem can you help",
        is_dm=False,
    )


def _slack_surface() -> AgentSurfaceEntity:
    return AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        name="slack",
        agent_id=uuid4(),
        surface_type=SurfacePlatform.SLACK,
        mode=SurfaceMode.DM,
        account_id=uuid4(),
        config=SurfaceConfig(),
        is_active=True,
    )


def test_a_newcomer_in_a_slack_channel_is_answered_not_ignored() -> None:
    """Being added to a channel bought nothing if the first newcomer is ignored.

    Everyone in a Slack channel was admitted by a workspace an organisation
    authorised, so an unrecognised sender there is a colleague who has no Lemma
    account yet -- not a stranger off the internet.
    """
    context = unresolved_sender_context(
        surface=_slack_surface(),
        parsed=_slack_channel_event(),
        adapter=SimpleNamespace(unresolved_sender_reply=lambda event: None),
        agent_display_name="Lem",
    )
    assert context is not None
    # Answered -- and the channel does not have to read it.
    assert context.reply_metadata["ephemeral_to"] == "U456"


def test_a_non_member_in_a_slack_channel_is_answered_privately() -> None:
    context = nonmember_context(
        surface=_slack_surface(),
        parsed=_slack_channel_event(),
        agent_display_name="Lem",
    )
    assert context is not None
    assert context.reply_metadata["ephemeral_to"] == "U456"


def test_a_room_that_cannot_be_answered_privately_stays_quiet() -> None:
    """A public notice about somebody's account is worse than none.

    Teams has no ephemeral, so until there is a private way to answer there,
    silence remains the better of the two available answers.
    """
    teams_channel = ParsedInboundSurfaceEvent(
        platform="TEAMS",
        conversation_type=ConversationType.EXTERNAL_GROUP,
        external_channel_id="19:channel",
        external_thread_id="19:channel",
        sender_external_user_id="aad-1",
        message_text="hello",
        is_dm=False,
    )
    assert private_reply_metadata(teams_channel) is None


def test_a_direct_message_needs_no_special_routing() -> None:
    assert private_reply_metadata(_email_event(EmailAuthenticationVerdict.PASS)) == {}


async def test_slack_answers_one_person_in_a_channel_with_an_ephemeral(
    monkeypatch,
) -> None:
    """The room keeps its protection and the person still gets their answer."""
    ephemeral_payloads: list[dict] = []
    channel_payloads: list[dict] = []

    async def fake_post_ephemeral(self, **kwargs):
        ephemeral_payloads.append(kwargs)
        return {"ok": True}

    async def fake_post_message(self, **kwargs):
        channel_payloads.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(AsyncWebClient, "chat_postEphemeral", fake_post_ephemeral)
    monkeypatch.setattr(AsyncWebClient, "chat_postMessage", fake_post_message)

    event = _slack_channel_event()
    event.reply_target = {"channel": "C123"}
    await SlackPlatformService(credentials={"access_token": "xoxb-test"}).send_message(
        event=event,
        message="here is how to get access",
        metadata={"ephemeral_to": "U456"},
    )

    assert channel_payloads == []
    assert len(ephemeral_payloads) == 1
    assert ephemeral_payloads[0]["user"] == "U456"
    assert ephemeral_payloads[0]["channel"] == "C123"
