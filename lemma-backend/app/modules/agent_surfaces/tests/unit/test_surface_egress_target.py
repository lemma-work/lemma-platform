"""Which pod an outbound message resolves its data against.

For a channel or a shared bot the surface and the conversation live in the same
pod, so the question never arises. A personal DM is where it does: the
installation belongs to the company that installed the app, the conversation
belongs to the person, and those are two different pods. Egress read the pod off
the installation, so `display_resource` looked for a person's files and table
rows in the company's pod -- and found the company's, or nothing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceConversationLink,
    AgentSurfaceEntity,
    ConversationType,
    ParsedInboundSurfaceEvent,
    SurfaceConfig,
    SurfaceMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.services.ingress_service import (
    AgentSurfaceIngressService,
)

pytestmark = pytest.mark.asyncio

#: Where `agent` publishes the conversation lookup. Doubled there rather than on
#: the surface module that calls it: the lookup belongs to another module.
_CONVERSATIONS = "app.modules.agent.contracts.conversations_for_surfaces"


def _event() -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform=SurfacePlatform.SLACK,
        conversation_type=ConversationType.EXTERNAL_DM,
        external_channel_id="D1",
        external_thread_id="D1",
        sender_external_user_id="U1",
        external_message_id="1700000000.1",
        message_text="show me the report",
        is_dm=True,
    )


def _installation(pod_id: UUID) -> AgentSurfaceEntity:
    """The company's Slack app: one surface, in the company's own pod."""
    return AgentSurfaceEntity(
        id=uuid4(),
        pod_id=pod_id,
        name="slack",
        agent_id=pod_id,
        surface_type=SurfacePlatform.SLACK,
        mode=SurfaceMode.DM,
        account_id=uuid4(),
        config=SurfaceConfig(),
    )


def _service(*, installation: AgentSurfaceEntity, link):
    surfaces = AsyncMock()
    surfaces.get.return_value = installation
    links = AsyncMock()
    links.get_by_conversation_id.return_value = link
    service = AgentSurfaceIngressService(
        uow=SimpleNamespace(session=None),
        surface_repository=surfaces,
        conversation_link_repository=links,
        adapter_registry=SimpleNamespace(get=lambda platform: AsyncMock()),
    )
    # The resolver the service built for itself reaches a database; this one
    # answers the same call. Not a private method of the subject -- it is the
    # collaborator the constructor picks when it is handed a unit of work.
    service.credential_resolver = SimpleNamespace(
        for_surface=AsyncMock(return_value={"access_token": "xoxb-company"})
    )
    return service


def _link(*, surface: AgentSurfaceEntity, conversation_id: UUID):
    return AgentSurfaceConversationLink(
        id=uuid4(),
        surface_id=surface.id,
        conversation_id=conversation_id,
        platform=surface.surface_type.value,
        external_channel_id="D1",
        external_thread_id="D1",
        external_user_id="U1",
        last_event=_event().model_dump(mode="json"),
        last_message_id="1700000000.1",
        last_inbound_at=datetime.now(timezone.utc),
    )


async def test_a_personal_dm_resolves_against_the_conversations_pod(monkeypatch):
    """The installation is the transport; the conversation is the destination."""
    company_pod = uuid4()
    personal_pod = uuid4()
    installation = _installation(company_pod)
    conversation_id = uuid4()
    monkeypatch.setattr(
        f"{_CONVERSATIONS}.surface_conversation",
        AsyncMock(
            return_value=SimpleNamespace(
                id=conversation_id, user_id=uuid4(), pod_id=personal_pod
            )
        ),
    )
    service = _service(
        installation=installation,
        link=_link(surface=installation, conversation_id=conversation_id),
    )

    target = await service._resolve_egress_target(conversation_id)

    assert target is not None
    assert target.pod_id == personal_pod, "egress resolved the wrong pod's data"
    # The surface is still the company's, because that is what the reply goes
    # out through. Both facts are true at once, which is the whole point.
    assert target.surface.pod_id == company_pod


async def test_there_is_no_target_without_a_conversation_to_answer_in(monkeypatch):
    """No pod, no destination -- and guessing one is what this replaced."""
    installation = _installation(uuid4())
    conversation_id = uuid4()
    monkeypatch.setattr(
        f"{_CONVERSATIONS}.surface_conversation", AsyncMock(return_value=None)
    )
    service = _service(
        installation=installation,
        link=_link(surface=installation, conversation_id=conversation_id),
    )

    assert await service._resolve_egress_target(conversation_id) is None
