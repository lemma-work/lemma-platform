"""A card whose pause a new message cancelled loses its controls, everywhere.

The person is shown an approval, does not tap it, and types something else.
Starting that turn denies the pause on their behalf, and the card used to keep
live buttons on every platform but Telegram -- a tap on it could only be told
"already cancelled". Each platform retires it its own way: Telegram and Slack
edit the message, Teams replaces the activity, and WhatsApp, which cannot edit,
replies to the card saying it is no longer needed.

Driven through the real ingress and run path against Postgres, with each
platform's fake recording what it was asked to do.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_surfaces.domain.decision_card import CardRetirement
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    script_request_approval,
    script_text,
)
from app.modules.agent_surfaces.tests.e2e.surface_journey import (
    CHAT_PLATFORMS,
    _slack_blocks,
    stage_surface,
)

pytestmark = pytest.mark.e2e

TOOL_CALL_ID = "call_card_retirement_1"
SUPERSEDED = CardRetirement.SUPERSEDED.note
ANSWERED = CardRetirement.ANSWERED.note


def _approval_then(text: str) -> list:
    return [
        script_request_approval(
            tool_name="display_resource",
            args={"request": {"type": "WIDGET", "content": "<div>Ready</div>"}},
            title="Show a widget",
            reason="Needs your OK first",
            tool_call_id=TOOL_CALL_ID,
        ),
        script_text(text),
    ]


def _position(messages: list[dict[str, Any]], is_card) -> int:
    """Where the card is among what the platform received, counting from 1 --
    which is how each fake numbers the ids it hands back."""
    return next(index for index, sent in enumerate(messages, 1) if is_card(sent))


def _slack_retired(store, note: str = SUPERSEDED) -> None:
    posted = store.get_all("SLACK")
    card = _position(
        posted,
        lambda sent: any(b.get("type") == "actions" for b in _slack_blocks(sent)),
    )
    [update] = [
        sent
        for sent in store.get_all("SLACK_UPDATE")
        if sent.get("ts") == f"1700000000.{card:06d}"
    ]
    blocks = _slack_blocks(update)
    assert blocks, "the card was rewritten with nothing in it"
    assert not [block for block in blocks if block.get("type") == "actions"]
    assert "Show a widget" in str(blocks)
    assert note in str(blocks[-1])


def _teams_retired(store, note: str = SUPERSEDED) -> None:
    def has_actions(sent: dict[str, Any]) -> bool:
        return any(
            (attachment.get("content") or {}).get("actions")
            for attachment in (sent.get("body") or {}).get("attachments") or []
        )

    card = _position(store.get_all("TEAMS"), has_actions)
    [update] = [
        sent
        for sent in store.get_all("TEAMS_UPDATE")
        if sent["activity_id"] == f"activity-{card}"
    ]
    assert not has_actions(update)
    assert "Show a widget" in str(update["body"])
    assert note in str(update["body"])


def _telegram_retired(store, note: str = SUPERSEDED) -> None:
    del note  # Telegram takes the keyboard away and leaves the text alone.
    card = _position(
        store.get_all("TELEGRAM"),
        lambda sent: (sent.get("reply_markup") or {}).get("inline_keyboard"),
    )
    [edit] = store.get_all("TELEGRAM_EDIT_MARKUP")
    assert edit["reply_markup"] == {"inline_keyboard": []}
    assert str(edit["message_id"]) == str(card)


def _whatsapp_retired(store, note: str = SUPERSEDED) -> None:
    sent = store.get_all("WHATSAPP")
    card = _position(sent, lambda message: message.get("type") == "interactive")
    replies = [
        message
        for message in sent
        if (message.get("context") or {}).get("message_id") == f"wamid.{card}"
    ]
    if note == ANSWERED:
        # WhatsApp cannot edit the card, and only a cancelled one gets a line
        # of its own; a tap on an answered one is turned away when it lands.
        assert replies == [], replies
        return
    [reply] = replies
    assert note.rstrip(".") in reply["text"]["body"]


_RETIRED = {
    SurfacePlatform.SLACK: _slack_retired,
    SurfacePlatform.TEAMS: _teams_retired,
    SurfacePlatform.TELEGRAM: _telegram_retired,
    SurfacePlatform.WHATSAPP: _whatsapp_retired,
}


@pytest.mark.parametrize("platform", CHAT_PLATFORMS, ids=lambda p: p.value.lower())
async def test_a_card_a_new_message_cancelled_loses_its_controls(
    platform: SurfacePlatform,
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fixed_test_org,
    message_store,
    monkeypatch,
    platform_fake,
) -> None:
    stage = await stage_surface(
        platform,
        fake=platform_fake[platform],
        toolsets=["USER_INTERACTION"],
        authenticated_client=authenticated_client,
        db_session=db_session,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fixed_test_org=fixed_test_org,
        message_store=message_store,
        monkeypatch=monkeypatch,
    )
    # One flat script for both turns: the second message reaches the model in
    # the same request as the denial it caused.
    await stage.say(
        "please show the widget",
        script=_approval_then("It is mid-afternoon in Lisbon."),
    )
    assert await stage.saw("Show a widget"), "the request never reached the person"
    await stage.control("Approve")

    await stage.say("actually, what time is it in Lisbon?")
    assert await stage.saw("mid-afternoon in Lisbon")

    _RETIRED[platform](message_store)


async def test_a_card_answered_in_words_is_marked_answered(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fixed_test_org,
    message_store,
    monkeypatch,
    platform_fake,
) -> None:
    """A typed "deny" settles the pause without a tap, and the card says so
    instead of offering to decide it again."""
    stage = await stage_surface(
        SurfacePlatform.SLACK,
        fake=platform_fake[SurfacePlatform.SLACK],
        toolsets=["USER_INTERACTION"],
        authenticated_client=authenticated_client,
        db_session=db_session,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fixed_test_org=fixed_test_org,
        message_store=message_store,
        monkeypatch=monkeypatch,
    )
    await stage.say(
        "please show the widget",
        script=_approval_then("Understood — I did not run it."),
    )
    assert await stage.saw("Show a widget")

    await stage.say("deny")

    [update] = message_store.get_all("SLACK_UPDATE")
    blocks = _slack_blocks(update)
    assert not [block for block in blocks if block.get("type") == "actions"]
    assert "Answered." in str(blocks[-1])


@pytest.mark.parametrize("platform", CHAT_PLATFORMS, ids=lambda p: p.value.lower())
async def test_a_card_answered_in_the_web_app_is_marked_answered(
    platform: SurfacePlatform,
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fixed_test_org,
    message_store,
    monkeypatch,
    platform_fake,
) -> None:
    """The person decides in Lemma, not on the platform.

    A tap retires its own card and a typed reply retires it when its turn
    starts; a decision made in the web app went through neither, and the card
    on the platform kept buttons offering to decide it again.
    """
    stage = await stage_surface(
        platform,
        fake=platform_fake[platform],
        toolsets=["USER_INTERACTION"],
        authenticated_client=authenticated_client,
        db_session=db_session,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fixed_test_org=fixed_test_org,
        message_store=message_store,
        monkeypatch=monkeypatch,
    )
    context = await stage.say(
        "please show the widget",
        script=_approval_then("Understood — I did not run it."),
    )
    assert await stage.saw("Show a widget"), "the request never reached the person"

    decided = await authenticated_client.post(
        f"/pods/{stage.pod_id}/conversations/{context.conversation_id}"
        f"/approvals/{TOOL_CALL_ID}/decision",
        json={"decision": "DENY"},
    )
    assert decided.status_code == 200, decided.text

    _RETIRED[platform](message_store, ANSWERED)
