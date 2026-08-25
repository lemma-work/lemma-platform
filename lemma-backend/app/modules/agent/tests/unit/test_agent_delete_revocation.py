"""Deleting an agent must revoke its tokens and take its mailbox with it."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.composition import agent_email_surface
from app.modules.agent.services import agent_service as agent_service_module
from app.modules.agent.services.agent_service import AgentService


@pytest.mark.asyncio
async def test_delete_agent_revokes_delegation(monkeypatch):
    agent = SimpleNamespace(id=uuid4(), user_id=uuid4())
    repo = AsyncMock()
    service = AgentService(agent_repository=repo, authorization_service=AsyncMock())
    monkeypatch.setattr(service, "get_agent_by_name", AsyncMock(return_value=agent))
    revoke_spy = AsyncMock()
    monkeypatch.setattr(agent_service_module, "revoke_delegation", revoke_spy)
    # Surface teardown reaches a real repository over this mock's session; it has
    # its own test below.
    monkeypatch.setattr(
        agent_email_surface, "teardown_agent_surfaces", AsyncMock(return_value=0)
    )

    # requester_user_id/ctx omitted: authorization is exercised elsewhere; here we
    # assert the revocation emit follows a successful delete.
    await service.delete_agent(pod_id=uuid4(), name="reporter")

    repo.delete.assert_awaited_once_with(agent.id)
    revoke_spy.assert_awaited_once_with(actor_id=agent.id)


@pytest.mark.asyncio
async def test_delete_agent_takes_its_surfaces_with_it(monkeypatch):
    """Otherwise the mailbox does not go away — it changes owner.

    ``agent_surfaces.agent_id`` is ON DELETE SET NULL, so a surface left behind
    reads as one with no agent of its own, which is exactly what the pod
    assistant's mailbox is. `surfaces_for_agent` then finds two for the
    assistant and the pod can answer from a deleted agent's address.
    """
    pod_id, agent = uuid4(), SimpleNamespace(id=uuid4(), user_id=uuid4())
    repo = AsyncMock()
    service = AgentService(agent_repository=repo, authorization_service=AsyncMock())
    monkeypatch.setattr(service, "get_agent_by_name", AsyncMock(return_value=agent))
    monkeypatch.setattr(agent_service_module, "revoke_delegation", AsyncMock())
    teardown = AsyncMock(return_value=1)
    monkeypatch.setattr(agent_email_surface, "teardown_agent_surfaces", teardown)

    await service.delete_agent(pod_id=pod_id, name="reporter")

    teardown.assert_awaited_once_with(repo.uow, pod_id=pod_id, agent_id=agent.id)


@pytest.mark.asyncio
async def test_the_surfaces_go_before_the_agent_row_does(monkeypatch):
    """Order is the whole trick, not an incidental.

    The teardown finds the surfaces *by* ``agent_id``. Deleting the agent first
    would have the database null that column out from under it, leaving nothing
    to match and the mailbox behind.
    """
    agent = SimpleNamespace(id=uuid4(), user_id=uuid4())
    repo = AsyncMock()
    service = AgentService(agent_repository=repo, authorization_service=AsyncMock())
    monkeypatch.setattr(service, "get_agent_by_name", AsyncMock(return_value=agent))
    monkeypatch.setattr(agent_service_module, "revoke_delegation", AsyncMock())

    order: list[str] = []
    monkeypatch.setattr(
        agent_email_surface,
        "teardown_agent_surfaces",
        AsyncMock(side_effect=lambda *a, **k: order.append("surfaces") or 0),
    )
    repo.delete.side_effect = lambda _id: order.append("agent")

    await service.delete_agent(pod_id=uuid4(), name="reporter")

    assert order == ["surfaces", "agent"], (
        f"the agent row must go last, or its surfaces are unfindable: {order}"
    )
