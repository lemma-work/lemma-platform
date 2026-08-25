"""A surface that gets one reply, and what a run may put in it.

`display_resource` on an email surface used to return
`success=True, "FILE resource ready for display."` and deliver nothing. The
model believed it had shown the file; the recipient never saw one. These pin
both halves of the fix -- the file is held, and the reply carries it.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent.tools.user_interaction.models import (
    DisplayResourceRequest,
    DisplayResourceResponse,
    DisplayResourceType,
)
from app.modules.agent.tools.user_interaction.pydantic_adapter import (
    _maybe_deliver_to_surface,
)
from app.modules.agent_surfaces.platforms.email_attachments import (
    outbound_paths_for_reply,
)
from app.modules.agent_surfaces.platforms.platform_capabilities import (
    PLATFORM_CAPABILITIES,
    DeliveryCardinality,
)
from app.modules.agent_surfaces.services.pending_envelope import (
    discard_display_paths,
    remember_display_path,
    take_display_paths,
)

pytestmark = pytest.mark.unit


# --- the capability -------------------------------------------------------


def test_email_gets_one_delivery_and_chat_gets_many() -> None:
    for platform in ("RESEND",):
        caps = PLATFORM_CAPABILITIES[platform]
        assert caps.delivery_cardinality is DeliveryCardinality.ONE
    for platform in ("SLACK", "TEAMS", "TELEGRAM", "WHATSAPP"):
        caps = PLATFORM_CAPABILITIES[platform]
        assert caps.delivery_cardinality is DeliveryCardinality.MANY


def test_cardinality_and_pausing_are_asked_separately() -> None:
    """They agree on every platform today and are still different questions.

    Reading them as one rule is how "email cannot pause" became "email cannot be
    asked anything", and a destructive action on an email surface either
    happened unapproved or silently did not happen.
    """
    caps = PLATFORM_CAPABILITIES["RESEND"]
    assert caps.delivery_cardinality is DeliveryCardinality.ONE
    assert caps.can_pause_for_a_person is False
    assert PLATFORM_CAPABILITIES["SLACK"].can_pause_for_a_person is True


# --- holding, and letting go ---------------------------------------------


def test_a_file_shown_twice_is_attached_once() -> None:
    conversation = uuid4()
    assert remember_display_path(conversation, "/me/q3.pdf")
    assert remember_display_path(conversation, "/me/q3.pdf")
    assert take_display_paths(conversation) == ["/me/q3.pdf"]


def test_draining_is_final_so_a_second_reply_does_not_re_attach() -> None:
    conversation = uuid4()
    remember_display_path(conversation, "/me/q3.pdf")
    assert take_display_paths(conversation) == ["/me/q3.pdf"]
    assert take_display_paths(conversation) == []


def test_a_runaway_run_is_bounded_rather_than_growing_forever() -> None:
    conversation = uuid4()
    accepted = [remember_display_path(conversation, f"/me/{i}.pdf") for i in range(40)]
    assert accepted.count(True) == 20
    assert accepted[-1] is False
    discard_display_paths(conversation)


def test_the_reply_carries_what_the_agent_asked_for_and_what_it_showed() -> None:
    conversation = uuid4()
    remember_display_path(conversation, "/me/shown.pdf")
    deps = SimpleNamespace(conversation_id=conversation)
    assert outbound_paths_for_reply(deps, ["/me/asked.csv"]) == [
        "/me/asked.csv",
        "/me/shown.pdf",
    ]


def test_a_file_both_asked_for_and_shown_is_one_attachment() -> None:
    conversation = uuid4()
    remember_display_path(conversation, "/me/q3.pdf")
    deps = SimpleNamespace(conversation_id=conversation)
    assert outbound_paths_for_reply(deps, ["/me/q3.pdf"]) == ["/me/q3.pdf"]


# --- the tool's own answer ------------------------------------------------


async def _display(request: DisplayResourceRequest, *, platform: str, conversation):
    response = DisplayResourceResponse(success=True, message="ready")
    ctx = SimpleNamespace(
        deps=SimpleNamespace(
            surface_platform=platform, conversation_id=conversation, pod_id=uuid4()
        ),
        tool_call_id="tool-1",
    )
    await _maybe_deliver_to_surface(ctx, request, response)
    return response


async def test_showing_a_file_on_email_holds_it_and_says_so() -> None:
    conversation = uuid4()
    response = await _display(
        DisplayResourceRequest(type=DisplayResourceType.FILE, path="/me/q3.pdf"),
        platform="RESEND",
        conversation=conversation,
    )
    assert response.success is True
    assert "attached to your email reply" in (response.message or "")
    assert take_display_paths(conversation) == ["/me/q3.pdf"]


async def test_showing_a_table_on_email_reports_failure_rather_than_success() -> None:
    """There is nothing to display in, and a false success is worse than a no.

    This is the exact shape of the original bug: the tool had already returned
    success before the email branch silently gave up.
    """
    conversation = uuid4()
    response = await _display(
        DisplayResourceRequest(type=DisplayResourceType.TABLE, name="orders"),
        platform="RESEND",
        conversation=conversation,
    )
    assert response.success is False
    assert "email conversation" in (response.error or "")
    assert take_display_paths(conversation) == []


async def test_a_chat_surface_is_untouched_by_any_of_this() -> None:
    conversation = uuid4()
    from unittest.mock import patch

    with patch(
        "app.composition.agent_surface_runtime.deliver_display_resource",
        new=AsyncMock(return_value=True),
    ) as delivered:
        response = await _display(
            DisplayResourceRequest(type=DisplayResourceType.FILE, path="/me/q3.pdf"),
            platform="SLACK",
            conversation=conversation,
        )
    assert response.success is True
    delivered.assert_awaited_once()
    assert take_display_paths(conversation) == [], "chat delivers now, it does not hold"
