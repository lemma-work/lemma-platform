"""Hermetic public-boundary journeys for standalone agent tools."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from aiohttp import web
from fastapi import status
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.config import settings
from app.modules.agent.infrastructure.models import AgentFeedbackModel
from app.modules.identity.infrastructure.supertokens_auth.helpers import get_user_token
from app.modules.identity.infrastructure.supertokens_auth.token_factory import (
    build_delegation_claims,
)

pytestmark = pytest.mark.e2e


@dataclass
class _FakeSearchProvider:
    base_url: str = ""
    queries: list[str] = field(default_factory=list)
    _runner: web.AppRunner | None = None

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/search", self._search)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        sockets = site._server.sockets if site._server else []  # noqa: SLF001
        assert sockets
        self.base_url = f"http://127.0.0.1:{sockets[0].getsockname()[1]}"

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()

    async def _search(self, request: web.Request) -> web.Response:
        query = request.query["q"]
        self.queries.append(query)
        return web.json_response(
            {
                "results": [
                    {
                        "title": "Lemma documentation",
                        "url": "https://example.test/lemma",
                        "content": "Deterministic fake-provider result.",
                        "score": 0.95,
                    }
                ]
            }
        )


@pytest_asyncio.fixture
async def fake_search_provider() -> AsyncIterator[_FakeSearchProvider]:
    provider = _FakeSearchProvider()
    await provider.start()
    try:
        yield provider
    finally:
        await provider.stop()


async def _delegated_client(
    test_app,
    *,
    user_id: str,
    pod_id: str,
    workload_type: str,
    workload_id: str,
    workload_name: str,
) -> AsyncClient:
    claims = build_delegation_claims(
        workload_type=workload_type,
        workload_id=UUID(workload_id),
        workload_name=workload_name,
        pod_id=UUID(pod_id),
        session_id=f"agent-tool-e2e-{uuid4().hex}",
        invoked_by_user_id=UUID(user_id),
    )
    token = await get_user_token(UUID(user_id), delegation_claims=claims)
    return AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {token}"},
    )


def _feedback_payload(subject: str) -> dict[str, str]:
    return {
        "category": "TOOLING_ISSUE",
        "subject": f"  {subject}  ",
        "issue_encountered": "  The tool encountered a deterministic issue.  ",
        "expected_behavior": "  Feedback is stored.  ",
        "actual_behavior": "  The public API responded.  ",
        "suggested_next_steps": "  Keep the boundary healthy.  ",
    }


async def test_web_search_api_uses_configured_provider_boundary(
    authenticated_client: AsyncClient,
    fake_search_provider: _FakeSearchProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "web_search_provider", "searxng")
    monkeypatch.setattr(settings, "searxng_url", fake_search_provider.base_url)

    response = await authenticated_client.post(
        "/tools/web-search",
        json={"query": "Lemma architecture", "max_results": 1},
    )

    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json() == {
        "success": True,
        "results": [
            {
                "title": "Lemma documentation",
                "url": "https://example.test/lemma",
                "snippet": "Deterministic fake-provider result.",
                "source": "searxng",
                "score": 0.95,
            }
        ],
        "message": "Web search completed successfully",
        "error": None,
    }
    assert fake_search_provider.queries == ["Lemma architecture"]


async def test_feedback_api_attributes_user_agent_and_non_agent_workloads(
    authenticated_client: AsyncClient,
    test_app,
    fixed_test_org,
    fixed_test_user,
    db_session,
) -> None:
    pod_response = await authenticated_client.post(
        "/pods",
        json={
            "name": f"Agent tool boundaries {uuid4().hex[:8]}",
            "description": "Feedback attribution E2E",
            "organization_id": fixed_test_org["id"],
            "type": "HYBRID",
        },
    )
    assert pod_response.status_code == status.HTTP_201_CREATED, pod_response.text
    pod_id = pod_response.json()["id"]
    agent_response = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={"name": "feedback_agent", "instruction": "Report tool failures."},
    )
    assert agent_response.status_code == status.HTTP_201_CREATED, agent_response.text
    agent_id = agent_response.json()["id"]

    user_response = await authenticated_client.post(
        "/tools/report-feedback",
        json=_feedback_payload("User feedback"),
    )
    assert user_response.status_code == status.HTTP_201_CREATED, user_response.text
    assert user_response.json()["agent_id"] is None

    async with await _delegated_client(
        test_app,
        user_id=fixed_test_user["id"],
        pod_id=pod_id,
        workload_type="agent",
        workload_id=agent_id,
        workload_name="feedback_agent",
    ) as agent_client:
        agent_response = await agent_client.post(
            "/tools/report-feedback",
            json=_feedback_payload("Agent feedback"),
        )
    assert agent_response.status_code == status.HTTP_201_CREATED, agent_response.text
    assert agent_response.json()["agent_id"] == agent_id

    async with await _delegated_client(
        test_app,
        user_id=fixed_test_user["id"],
        pod_id=pod_id,
        workload_type="function",
        workload_id=str(uuid4()),
        workload_name="feedback_function",
    ) as function_client:
        function_response = await function_client.post(
            "/tools/report-feedback",
            json=_feedback_payload("Function feedback"),
        )
    assert function_response.status_code == status.HTTP_201_CREATED
    assert function_response.json()["agent_id"] is None

    feedback_ids = {
        UUID(user_response.json()["feedback_id"]),
        UUID(agent_response.json()["feedback_id"]),
        UUID(function_response.json()["feedback_id"]),
    }
    persisted = (
        await db_session.execute(
            select(AgentFeedbackModel).where(AgentFeedbackModel.id.in_(feedback_ids))
        )
    ).scalars().all()
    assert len(persisted) == 3
    by_subject = {feedback.subject: feedback for feedback in persisted}
    assert by_subject["User feedback"].agent_id is None
    assert by_subject["Agent feedback"].agent_id == UUID(agent_id)
    assert by_subject["Function feedback"].agent_id is None
    assert all(feedback.issue_encountered == "The tool encountered a deterministic issue." for feedback in persisted)
