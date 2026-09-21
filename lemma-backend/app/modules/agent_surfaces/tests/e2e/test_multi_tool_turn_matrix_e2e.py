"""Two real tool calls, then one final answer, on every chat platform.

Two things are being proved, and only the first is about tools: that both tool
side effects land in sequence, and that **exactly one** content message closes
the turn. The second is the regression that matters — the run observer has a
fallback delivery path, and a turn that ends through both of them answers the
person twice.

"Exactly one final answer" is the same sentence on every platform, so it is
asserted once. How a side effect shows up is not: a widget is a posted card on
Slack, an attachment-bearing activity on Teams, a link card on WhatsApp; speech
is a file upload on Slack and a voice note on Telegram. Those readers sit
together below so the differences are visible rather than averaged away.

Email is not a chat platform and is not in the matrix: `display_resource` is
refused there, and the turn still has to reply. That case keeps its own test at
the bottom, unchanged.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.infrastructure.models import AgentSurface
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _create_agent_surface,
    _ensure_connector_account,
    _messages_for_conversation,
    _resend_payload,
)
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
    wait_for_messages,
    wait_for_slack_text,
)
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    process_ingress_and_run_scripted,
    script_display_resource,
    script_say,
    script_text,
)
from app.modules.agent_surfaces.tests.e2e.surface_journey import (
    CHAT_PLATFORMS,
    stage_surface,
)
from app.modules.connectors.domain.connector import AuthProvider


pytestmark = pytest.mark.e2e

FINAL = "All done."
WIDGET_ARGS = {
    "type": "WIDGET",
    "content": "<div class='status'><span>Ready</span></div>",
}

#: The second tool of the turn. Speech where the platform has a voice to use,
#: a second widget where it does not -- either way two side effects precede the
#: answer, which is what the turn is about.
SPEAKS = frozenset({SurfacePlatform.SLACK, SurfacePlatform.TELEGRAM})


def turn_script(platform: SurfacePlatform) -> list:
    second = (
        script_say("Here's what I found.", tool_call_id="tool-say-1")
        if platform in SPEAKS
        else script_display_resource(**WIDGET_ARGS, tool_call_id="tool-display-2")
    )
    return [
        script_display_resource(**WIDGET_ARGS, tool_call_id="tool-display-1"),
        second,
        script_text(FINAL),
    ]


def toolsets_for(platform: SurfacePlatform) -> list[str]:
    return (
        ["USER_INTERACTION", "SPEECH"] if platform in SPEAKS else ["USER_INTERACTION"]
    )


# ── what one platform's side effects look like in the store ───────────────


async def _slack_side_effects(store: Any) -> None:
    """A posted widget card, and a file upload for the spoken part."""
    delivered = await wait_for_slack_text(store, FINAL)
    widgets = [index for index, text in enumerate(delivered) if "Widget ready" in text]
    assert widgets, f"the widget card was never delivered: {delivered}"
    uploads = await wait_for_messages(store, "SLACK_FILE_UPLOAD_URL", min_count=1)
    assert uploads, "the spoken part never uploaded"
    finals = [index for index, text in enumerate(delivered) if FINAL in text]
    # Ordering spans two Slack APIs -- the widget is posted, the answer is
    # streamed -- so this is arrival order, not one bucket's index.
    assert finals[0] > widgets[0], "the answer arrived before the widget"


async def _teams_side_effects(store: Any) -> None:
    """Two attachment-bearing activities, both before the answer."""
    messages = await wait_for_messages(store, "TEAMS", min_count=1)
    bodies = [
        message["body"]
        for message in messages
        if message.get("body", {}).get("type") == "message"
    ]
    widgets = [body for body in bodies if body.get("attachments")]
    assert len(widgets) == 2, "both widget calls must render their own message"
    finals = [body for body in bodies if body.get("text") == FINAL]
    assert bodies.index(finals[0]) > bodies.index(widgets[-1])


async def _telegram_side_effects(store: Any) -> None:
    """A voice note for the spoken part."""
    assert await wait_for_messages(store, "TELEGRAM_VOICE", min_count=1)


async def _whatsapp_side_effects(store: Any) -> None:
    """Two link cards: a WIDGET has no path to upload as native media.

    That is FILE-type only, see `send_display_resource_for_conversation` -- so
    both calls render as an "open widget" interactive, distinct from the text.
    """
    messages = await wait_for_messages(store, "WHATSAPP", min_count=3)
    widgets = [m for m in messages if m.get("type") == "interactive"]
    assert len(widgets) == 2, "both widget calls must render their own message"


SIDE_EFFECTS: dict[SurfacePlatform, Callable[[Any], Any]] = {
    SurfacePlatform.SLACK: _slack_side_effects,
    SurfacePlatform.TEAMS: _teams_side_effects,
    SurfacePlatform.TELEGRAM: _telegram_side_effects,
    SurfacePlatform.WHATSAPP: _whatsapp_side_effects,
}


def _final_answer_count(platform: SurfacePlatform, store: Any) -> int:
    """How many times the closing message reached the person."""
    if platform is SurfacePlatform.SLACK:
        from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
            slack_delivered,
        )

        return sum(1 for text in slack_delivered(store) if FINAL in text)
    if platform is SurfacePlatform.TEAMS:
        return sum(
            1
            for message in store.get_all("TEAMS")
            if message.get("body", {}).get("text") == FINAL
        )
    if platform is SurfacePlatform.TELEGRAM:
        return sum(
            1
            for message in store.get_all("TELEGRAM")
            if FINAL[:-1] in (message.get("text") or "")
        )
    return sum(
        1
        for message in store.get_all("WHATSAPP")
        if (message.get("text") or {}).get("body") == FINAL
    )


@pytest.mark.parametrize("platform", CHAT_PLATFORMS, ids=lambda p: p.value)
async def test_both_tools_land_and_exactly_one_answer_closes_the_turn(
    platform: SurfacePlatform,
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fixed_test_org,
    message_store,
    monkeypatch,
    platform_fake,
    fake_speech_provider,
) -> None:
    # `fake_speech_provider` is only used by the platforms that speak, and is
    # requested unconditionally because a fixture is cheaper than a branch.
    del fake_speech_provider
    stage = await stage_surface(
        platform,
        fake=platform_fake[platform],
        toolsets=toolsets_for(platform),
        authenticated_client=authenticated_client,
        db_session=db_session,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fixed_test_org=fixed_test_org,
        message_store=message_store,
        monkeypatch=monkeypatch,
    )

    await stage.say("show me and tell me", script=turn_script(platform))

    assert await stage.saw(FINAL[:-1]), "the turn never reached the person"
    await SIDE_EFFECTS[platform](message_store)

    delivered = _final_answer_count(platform, message_store)
    assert delivered == 1, (
        f"{platform.value}: the final answer must close the turn exactly once, "
        f"got {delivered} -- the run observer's fallback path is delivering twice"
    )


# ── Email: the tools are refused, and the turn still answers ──────────────


async def test_two_widgets_on_email_are_refused_and_the_turn_still_replies(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    message_store,
    monkeypatch,
):
    """Same shape as Gmail/Outlook: two no-op-delivery WIDGET calls, then the
    single reply-tool call is the only actual outbound send — proven here via
    a real (non-Composio) send, unlike Gmail/Outlook's intercepted calls."""
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")
    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="resend",
        credentials={
            "api_key": "resend-token",
            "api_base_url": fake_resend.api_base,
        },
        email="assistant@resend.test",
        provider=AuthProvider.LEMMA,
    )
    _agent, surface = await _create_agent_surface(
        authenticated_client,
        pod_id,
        config={"type": "RESEND", "account_id": str(account.id)},
        toolsets=["USER_INTERACTION"],
    )
    assistant_address = surface.get("surface_identity_email")
    if not assistant_address:
        surface_model = await db_session.get(AgentSurface, UUID(surface["id"]))
        assistant_address = surface_model.surface_identity_email
    assert assistant_address

    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="resend",
            payload=_resend_payload(
                sender_email=fixed_test_user["email"],
                assistant_address=assistant_address,
                message_id="resend-message-multi-1",
                text="Can you help over email?",
            ),
            headers={},
        ),
        script=[
            script_display_resource(**WIDGET_ARGS, tool_call_id="tool-display-1"),
            script_display_resource(**WIDGET_ARGS, tool_call_id="tool-display-2"),
            script_text("Here is my answer."),
        ],
    )

    messages = await _messages_for_conversation(
        authenticated_client,
        pod_id=pod_id,
        conversation_id=str(context.conversation_id),
    )
    display_returns = [
        m
        for m in messages
        if m.get("kind") == "TOOL_RETURN" and m.get("tool_name") == "display_resource"
    ]
    assert len(display_returns) == 2
    # A widget is a link into Lemma, and there is nothing on an email to link
    # from. Both calls say so instead of reporting a success that delivered
    # nothing -- which is what they used to do, leaving the model believing it
    # had shown the person something.
    assert not any(r["tool_result"]["success"] for r in display_returns)
    assert all(
        "email conversation" in (r["tool_result"].get("error") or "")
        for r in display_returns
    )

    resend_messages = await wait_for_messages(message_store, "RESEND", min_count=1)
    assert len(resend_messages) == 1, "exactly one email must be sent for the turn"
    assert "Here is my answer." in json.dumps(resend_messages[0])
