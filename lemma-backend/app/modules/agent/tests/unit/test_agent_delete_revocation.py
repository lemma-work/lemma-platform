"""Deleting an agent must revoke its in-flight delegated tokens."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

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

    # requester_user_id/ctx omitted: authorization is exercised elsewhere; here we
    # assert the revocation emit follows a successful delete.
    await service.delete_agent(pod_id=uuid4(), name="reporter")

    repo.delete.assert_awaited_once_with(agent.id)
    revoke_spy.assert_awaited_once_with(actor_id=agent.id)
