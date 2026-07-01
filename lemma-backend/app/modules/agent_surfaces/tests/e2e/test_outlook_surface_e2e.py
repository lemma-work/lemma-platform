from __future__ import annotations

import json
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.ingress_request import SurfaceScheduleIngress
from app.modules.agent_surfaces.infrastructure.models import AgentSurface
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _conversation_by_external_thread,
    _create_agent_surface,
    _ensure_connector_trigger,
    _ensure_connector_account,
    _outlook_payload,
)
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import wait_for_messages
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    process_ingress_and_run_scripted,
    script_text,
)
from app.modules.connectors.domain.connector import AuthProvider
from app.modules.schedule.infrastructure.schedule_managers.manager_factory import (
    ManagersFactory,
)

pytestmark = pytest.mark.e2e


async def test_outlook_email_surface_handles_trigger_payload_and_replies(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_outlook,
    fake_composio_email,
    message_store,
    monkeypatch,
):
    monkeypatch.setattr(ManagersFactory, "get_manager", lambda *args, **kwargs: _FakeScheduleManager())
    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="outlook",
        credentials={
            "access_token": "outlook-token",
            "api_base_url": fake_outlook.api_base,
        },
        email="assistant@outlook.test",
        provider=AuthProvider.COMPOSIO,
    )
    await _ensure_connector_trigger(
        db_session,
        connector_id="outlook",
        trigger_id="outlook_message_e2e",
        event_type="OUTLOOK_MESSAGE_TRIGGER",
    )
    agent, surface = await _create_agent_surface(
        authenticated_client,
        pod_id,
        config={"type": "OUTLOOK", "account_id": str(account.id)},
    )
    surface_model = await db_session.get(AgentSurface, UUID(surface["id"]))
    assert surface_model is not None
    assert surface_model.schedule_id is not None

    context = await process_ingress_and_run_scripted(
        db_session,
        SurfaceScheduleIngress(
            schedule_id=surface_model.schedule_id,
            payload=_outlook_payload(
                sender_email=fixed_test_user["email"],
                assistant_email="assistant@outlook.test",
                thread_id="outlook-thread-e2e",
                message_id="outlook-message-1",
                text="Can you help over Outlook?",
            ),
            account_id=account.id,
            pod_id=UUID(pod_id),
            user_id=UUID(fixed_test_user["id"]),
        ),
        script=[script_text("E2E agent reply [OUTLOOK]")],
    )
    assert isinstance(context, SurfaceChatContext)

    conversation = await _conversation_by_external_thread(
        authenticated_client,
        pod_id=pod_id,
        agent_name=agent["name"],
        external_thread_id="outlook-thread-e2e",
    )
    assert conversation is not None

    outlook_messages = await wait_for_messages(
        message_store,
        "OUTLOOK_REPLY",
        min_count=1,
    )
    reply = outlook_messages[-1]
    assert reply["operation_name"] == "OUTLOOK_REPLY_EMAIL"
    assert "E2E agent reply [OUTLOOK]" in json.dumps(reply["payload"])


class _FakeScheduleManager:
    async def create_schedule(self, *, account, app_trigger, config) -> str:
        return f"e2e-{app_trigger.id}"

    async def delete_schedule(self, account, provider_id: str) -> None:
        return None

    async def get_schedule(self, account, provider_id: str):
        return None
