"""Reaching a named person on a named surface, and the six ways it can refuse.

Split from ``test_ingress_service`` with ``MemberReach``. Two of the original
refusals are gone: both meant "a collaborator was never wired", and a required
constructor argument now says so at wiring time instead.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceConversationLink,
    AgentSurfaceStatus,
)
from app.modules.agent_surfaces.services.notification_delivery import (
    UndeliverableReason,
)
from app.modules.agent_surfaces.tests.unit.surface_doubles import (
    _ask_user_link,
    _delivering_adapter,
    _slack_event,
    _slack_surface,
    _telegram_event,
    _telegram_surface,
    build_member_reach,
    conversation_operations,  # noqa: F401  (autouse fixture)
)

pytestmark = pytest.mark.asyncio


async def test_send_to_member_reuses_existing_thread():
    """surface.send reaches a pod member by reusing their existing thread."""
    surface = _slack_surface()
    conversation_id = uuid4()
    parsed_event = _slack_event()
    link = await _ask_user_link(surface, conversation_id, parsed_event)
    adapter = _delivering_adapter()
    reach = build_member_reach(adapter=adapter, surfaces=[surface], existing_link=link)
    reach.conversation_link_repository.get_by_conversation_id.return_value = link
    reach.pod_membership_port = SimpleNamespace(
        get_user_pod_ids=AsyncMock(return_value=[surface.pod_id])
    )
    reach.external_user_repository = AsyncMock(
        list_by_resolved_users=AsyncMock(
            return_value=[
                SimpleNamespace(external_user_id=link.external_user_id, tenant_id=None)
            ]
        )
    )
    reach.conversation_link_repository.get_latest_by_surface_and_external_user = (
        AsyncMock(return_value=link)
    )

    undeliverable = await reach.send_to_member(
        surface=surface,
        user_id=uuid4(),
        message="Your report is ready.",
    )
    assert undeliverable is None
    assert "Your report is ready." in adapter.send_message.await_args.kwargs["message"]


async def test_send_to_member_uses_requested_surface_latest_thread():
    surface = _telegram_surface()
    user_id = uuid4()
    older_link = AgentSurfaceConversationLink(
        surface_id=surface.id,
        conversation_id=uuid4(),
        platform="TELEGRAM",
        external_channel_id="older-chat",
        external_thread_id="older-chat",
        external_user_id="777",
        last_event=_telegram_event(
            chat_id="older-chat", message_id="older-message"
        ).model_dump(mode="json"),
    )
    latest_link = AgentSurfaceConversationLink(
        surface_id=surface.id,
        conversation_id=uuid4(),
        platform="TELEGRAM",
        external_channel_id="latest-chat",
        external_thread_id="latest-chat",
        external_user_id="777",
        last_event=_telegram_event(
            chat_id="latest-chat", message_id="latest-message"
        ).model_dump(mode="json"),
    )
    adapter = _delivering_adapter()
    reach = build_member_reach(
        adapter=adapter, surfaces=[surface], existing_link=older_link
    )
    reach.pod_membership_port = SimpleNamespace(
        get_user_pod_ids=AsyncMock(return_value=[surface.pod_id])
    )
    reach.external_user_repository = AsyncMock(
        list_by_resolved_users=AsyncMock(
            return_value=[SimpleNamespace(external_user_id="777", tenant_id=None)]
        )
    )
    reach.conversation_link_repository.get_latest_by_surface_and_external_user = (
        AsyncMock(return_value=latest_link)
    )
    reach.conversation_link_repository.get_by_conversation_id.return_value = latest_link

    undeliverable = await reach.send_to_member(
        surface=surface,
        user_id=user_id,
        message="Use the newest thread.",
    )

    assert undeliverable is None
    reach.conversation_link_repository.get_latest_by_surface_and_external_user.assert_awaited_once_with(
        surface_id=surface.id,
        external_user_id="777",
    )
    event = adapter.send_message.await_args.kwargs["event"]
    assert event.external_thread_id == "latest-chat"
    assert event.reply_target["chat_id"] == "latest-chat"


async def test_send_to_member_does_not_confuse_system_and_custom_threads():
    system_surface = _telegram_surface()
    custom_surface = _telegram_surface()
    user_id = uuid4()
    system_link = AgentSurfaceConversationLink(
        surface_id=system_surface.id,
        conversation_id=uuid4(),
        platform="TELEGRAM",
        external_channel_id="system-chat",
        external_thread_id="system-chat",
        external_user_id="777",
        last_event=_telegram_event(
            chat_id="system-chat", message_id="system-message"
        ).model_dump(mode="json"),
    )
    custom_link = AgentSurfaceConversationLink(
        surface_id=custom_surface.id,
        conversation_id=uuid4(),
        platform="TELEGRAM",
        external_channel_id="custom-chat",
        external_thread_id="custom-chat",
        external_user_id="777",
        last_event=_telegram_event(
            chat_id="custom-chat", message_id="custom-message"
        ).model_dump(mode="json"),
    )
    links_by_surface = {
        system_surface.id: system_link,
        custom_surface.id: custom_link,
    }
    links_by_conversation = {
        system_link.conversation_id: system_link,
        custom_link.conversation_id: custom_link,
    }
    adapter = _delivering_adapter()
    reach = build_member_reach(
        adapter=adapter,
        surfaces=[system_surface, custom_surface],
        existing_link=system_link,
    )
    reach.pod_membership_port = SimpleNamespace(
        get_user_pod_ids=AsyncMock(
            return_value=[system_surface.pod_id, custom_surface.pod_id]
        )
    )
    reach.external_user_repository = AsyncMock(
        list_by_resolved_users=AsyncMock(
            return_value=[SimpleNamespace(external_user_id="777", tenant_id=None)]
        )
    )
    reach.conversation_link_repository.get_latest_by_surface_and_external_user = (
        AsyncMock(
            side_effect=lambda *, surface_id, external_user_id: links_by_surface[
                surface_id
            ]
        )
    )
    reach.conversation_link_repository.get_by_conversation_id.side_effect = (
        lambda conversation_id: links_by_conversation[conversation_id]
    )

    custom_sent = await reach.send_to_member(
        surface=custom_surface,
        user_id=user_id,
        message="custom only",
    )
    system_sent = await reach.send_to_member(
        surface=system_surface,
        user_id=user_id,
        message="system only",
    )

    assert custom_sent is None
    assert system_sent is None
    first_event = adapter.send_message.await_args_list[0].kwargs["event"]
    second_event = adapter.send_message.await_args_list[1].kwargs["event"]
    assert first_event.external_thread_id == "custom-chat"
    assert second_event.external_thread_id == "system-chat"


async def test_send_to_member_says_the_person_is_not_in_the_pod():
    surface = _slack_surface()
    adapter = AsyncMock()
    reach = build_member_reach(adapter=adapter, surfaces=[surface])
    reach.pod_membership_port = SimpleNamespace(
        get_user_pod_ids=AsyncMock(return_value=[uuid4()])  # a different pod
    )

    undeliverable = await reach.send_to_member(
        surface=surface,
        user_id=uuid4(),
        message="x",
    )
    # One 404 for six causes told a caller nothing: "no reachable conversation"
    # is not what happened to somebody who is not in the pod at all.
    assert undeliverable == UndeliverableReason.NOT_A_POD_MEMBER
    adapter.send_message.assert_not_awaited()


async def test_send_to_member_says_they_have_never_written_in():
    surface = _slack_surface()
    adapter = AsyncMock()
    reach = build_member_reach(adapter=adapter, surfaces=[surface])
    reach.pod_membership_port = SimpleNamespace(
        get_user_pod_ids=AsyncMock(return_value=[surface.pod_id])
    )
    reach.external_user_repository = AsyncMock(
        list_by_resolved_users=AsyncMock(
            return_value=[SimpleNamespace(external_user_id="U-MEMBER", tenant_id=None)]
        )
    )
    reach.conversation_link_repository.get_latest_by_surface_and_external_user = (
        AsyncMock(return_value=None)
    )

    undeliverable = await reach.send_to_member(
        surface=surface,
        user_id=uuid4(),
        message="x",
    )
    assert undeliverable == UndeliverableReason.never_interacted_on("SLACK")
    adapter.send_message.assert_not_awaited()


async def test_send_to_member_says_the_surface_is_switched_off():
    surface = _slack_surface()
    surface.status = AgentSurfaceStatus.INACTIVE
    adapter = AsyncMock()
    reach = build_member_reach(adapter=adapter, surfaces=[surface])

    undeliverable = await reach.send_to_member(
        surface=surface, user_id=uuid4(), message="x"
    )

    assert undeliverable == UndeliverableReason.SURFACE_NOT_ACTIVE
    adapter.send_message.assert_not_awaited()


async def test_send_to_member_says_the_person_is_in_another_workspace():
    """Slack ids are per workspace, so "never written in" would be a lie here.

    They have written in — to a different Slack workspace than the one this
    surface is connected to, which is not something they can fix by messaging
    the bot again.
    """
    surface = _slack_surface().model_copy(update={"external_workspace_id": "T-HOME"})
    adapter = AsyncMock()
    reach = build_member_reach(adapter=adapter, surfaces=[surface])
    reach.pod_membership_port = SimpleNamespace(
        get_user_pod_ids=AsyncMock(return_value=[surface.pod_id])
    )
    reach.external_user_repository = AsyncMock(
        list_by_resolved_users=AsyncMock(
            return_value=[
                SimpleNamespace(external_user_id="U-MEMBER", tenant_id="T-ELSEWHERE")
            ]
        )
    )

    undeliverable = await reach.send_to_member(
        surface=surface, user_id=uuid4(), message="x"
    )

    assert undeliverable == UndeliverableReason.wrong_tenant_on("SLACK")
    assert undeliverable != UndeliverableReason.never_interacted_on("SLACK")
    adapter.send_message.assert_not_awaited()
