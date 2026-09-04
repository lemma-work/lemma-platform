"""E2E coverage for conversation title generation against a real database.

The full worker-driven path (run completes -> AgentRunCompletedEvent ->
``generate_conversation_title`` job) needs a real provider model and is covered
by ``test_first_run_generates_title_with_real_worker_model`` (provider-gated).

This module exercises the same ``ConversationTitleService`` against the real
testcontainer Postgres + app stack, with only the LLM boundary stubbed, so the
DB read of the opening messages, the title persistence, and idempotency are
verified end-to-end without provider credentials.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    create_uow_from_session_maker,
)
from app.modules.agent.domain.value_objects import MessageDraft, MessageRole
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.agent.services import conversation_title_service as cts
from app.modules.agent.services.conversation_title_service import (
    ConversationTitleService,
)

pytestmark = pytest.mark.e2e


class _FakeResolved:
    credentials: dict[str, object] = {}
    model_name_for_harness = "deepseek-v4-flash"

    def public_snapshot(self) -> dict[str, object | None]:
        return {
            "profile_id": "system:lemma",
            "scope": "SYSTEM",
            "protocol": "OPENAI_COMPATIBLE",
            "model_name": "deepseek-v4-flash",
            "provider_model_name": "accounts/fireworks/models/deepseek-v4-flash",
            "config": {"base_url": "http://fireworks.test/v1"},
        }


class _FakeProfileService:
    async def resolve(self, *, runtime, organization_id, user_id):
        return _FakeResolved()


def _stub_llm(monkeypatch: pytest.MonkeyPatch, *, output: str) -> None:
    """Stub the title service's model/LLM/usage boundary (DB stays real)."""

    class _FakeLLMAgent:
        def __init__(self, model, system_prompt=None):
            pass

        async def run(self, prompt, *, usage_limits=None, model_settings=None):
            del usage_limits, model_settings
            return SimpleNamespace(output=output)

    @asynccontextmanager
    async def _noop_billed(model, *, source_type, runtime_profile, context):
        """The metering scope, doing none of it.

        One name where there were two. `billed` reserves on the way in, records
        each request and settles on the way out, so a test that wants titling
        without touching the spend counters replaces the scope rather than the
        pair of calls it used to be made of.
        """
        del source_type, runtime_profile, context
        yield model

    # The LLM title path is opt-in (agent_settings.conversation_title_model); without
    # it the service deliberately falls back to the first-message title. Enable
    # it so the stubbed model boundary is exercised.
    monkeypatch.setattr(
        cts.agent_settings, "conversation_title_model", "deepseek-v4-flash"
    )
    monkeypatch.setattr(cts, "PydanticAIAgent", _FakeLLMAgent)
    monkeypatch.setattr(cts, "AgentRuntimeProfileService", _FakeProfileService)
    monkeypatch.setattr(
        cts, "require_pydantic_ai_model_from_runtime_profile", lambda **_: object()
    )
    monkeypatch.setattr(cts, "billed", _noop_billed)


async def _create_pod(authenticated_client, fixed_test_org) -> str:
    response = await authenticated_client.post(
        "/pods",
        json={
            "name": f"Title Pod {uuid4().hex[:8]}",
            "description": "Conversation title E2E pod",
            "organization_id": fixed_test_org["id"],
            "type": "HYBRID",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _seed_opening_exchange(conversation_id: UUID) -> None:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        repo = ConversationRepository(uow)
        await repo.append_message(
            conversation_id=conversation_id,
            agent_run_id=None,
            draft=MessageDraft.of_text(
                "Help me plan a 3-day vegetarian food tour of Tokyo.",
                role=MessageRole.USER,
            ),
        )
        await repo.append_message(
            conversation_id=conversation_id,
            agent_run_id=None,
            draft=MessageDraft.of_text(
                "Sure! Here is a 3-day vegetarian itinerary for Tokyo...",
                role=MessageRole.ASSISTANT,
            ),
        )
        await uow.commit()


class TestConversationTitleServiceAgainstRealDb:
    async def test_generates_and_persists_title_idempotently(
        self,
        authenticated_client,
        fixed_test_org,
        monkeypatch,
    ):
        _stub_llm(monkeypatch, output='  "Tokyo Vegetarian Food Tour".  ')
        pod_id = await _create_pod(authenticated_client, fixed_test_org)

        # Pod-assistant conversation created WITHOUT a title -> eligible.
        create = await authenticated_client.post(
            f"/pods/{pod_id}/conversations",
            json={"agent_runtime": {"profile_id": "system:lemma"}},
        )
        assert create.status_code == 201, create.text
        conversation = create.json()
        conversation_id = UUID(conversation["id"])
        assert conversation["title"] is None

        await _seed_opening_exchange(conversation_id)

        service = ConversationTitleService(
            uow_factory=SessionUnitOfWorkFactory(async_session_maker)
        )
        title = await service.generate_title_if_absent(conversation_id)
        assert title == "Tokyo Vegetarian Food Tour"  # quotes/period sanitized

        # Persisted to Postgres and surfaced by the conversation API.
        fetched = await authenticated_client.get(
            f"/pods/{pod_id}/conversations/{conversation_id}"
        )
        assert fetched.status_code == 200, fetched.text
        assert fetched.json()["title"] == "Tokyo Vegetarian Food Tour"

        # Idempotent: a second invocation (even with a different stub) is a no-op.
        _stub_llm(monkeypatch, output="A COMPLETELY DIFFERENT TITLE")
        again = await service.generate_title_if_absent(conversation_id)
        assert again is None
        fetched_again = await authenticated_client.get(
            f"/pods/{pod_id}/conversations/{conversation_id}"
        )
        assert fetched_again.json()["title"] == "Tokyo Vegetarian Food Tour"

    async def test_skips_when_no_user_message(
        self,
        authenticated_client,
        fixed_test_org,
        monkeypatch,
    ):
        _stub_llm(monkeypatch, output="Should Not Be Used")
        pod_id = await _create_pod(authenticated_client, fixed_test_org)

        create = await authenticated_client.post(
            f"/pods/{pod_id}/conversations",
            json={"agent_runtime": {"profile_id": "system:lemma"}},
        )
        assert create.status_code == 201, create.text
        conversation_id = UUID(create.json()["id"])

        # Only an assistant message -> no user intent to title from.
        async with create_uow_from_session_maker(async_session_maker) as uow:
            await ConversationRepository(uow).append_message(
                conversation_id=conversation_id,
                agent_run_id=None,
                draft=MessageDraft.of_text("Hello!", role=MessageRole.ASSISTANT),
            )
            await uow.commit()

        service = ConversationTitleService(
            uow_factory=SessionUnitOfWorkFactory(async_session_maker)
        )
        assert await service.generate_title_if_absent(conversation_id) is None

        fetched = await authenticated_client.get(
            f"/pods/{pod_id}/conversations/{conversation_id}"
        )
        assert fetched.json()["title"] is None
