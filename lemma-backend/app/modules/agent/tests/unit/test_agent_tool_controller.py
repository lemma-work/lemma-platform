from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.agent.api.controllers import tool_controller
from app.modules.agent.tools.feedback.models import (
    FeedbackCategory,
    ReportFeedbackRequest,
)
from app.core.web_search.web_search import WebSearchRequest, WebSearchResponse


@pytest.mark.asyncio
async def test_agent_web_search_controller_delegates_to_internal(monkeypatch):
    async def fake_web_search_internal(
        _deps, request: WebSearchRequest
    ) -> WebSearchResponse:
        assert request.query == "pydantic ai"
        return WebSearchResponse(success=True, results=[], message="ok")

    monkeypatch.setattr(
        tool_controller, "web_search_internal", fake_web_search_internal
    )

    response = await tool_controller.web_search(
        data=WebSearchRequest(query="pydantic ai", max_results=3),
    )

    assert response.success is True
    assert response.message == "ok"


@pytest.mark.asyncio
async def test_agent_report_feedback_links_delegated_agent():
    added: list[object] = []
    agent_id = uuid4()

    class FakeSession:
        def add(self, value):
            added.append(value)
            value.id = uuid4()

    class FakeUow:
        def __init__(self):
            self.session = FakeSession()
            self.committed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def commit(self):
            self.committed = True

    request = SimpleNamespace(
        state=SimpleNamespace(
            delegation_claims=SimpleNamespace(
                actor_type=tool_controller.WorkloadPrincipalType.AGENT,
                actor_id=agent_id,
            )
        )
    )
    uow = FakeUow()

    response = await tool_controller.report_feedback(
        request=request,
        data=ReportFeedbackRequest(
            category=FeedbackCategory.TOOLING_ISSUE,
            subject=" Tool failed ",
            issue_encountered="The helper endpoint returned stale data.",
            expected_behavior="It should return fresh results.",
            actual_behavior="It returned stale results.",
        ),
        user=SimpleNamespace(id=uuid4()),
        uow=uow,
    )

    assert response.success is True
    assert response.agent_id == agent_id
    assert uow.committed is True
    assert len(added) == 1
    assert added[0].agent_id == agent_id
    assert added[0].subject == "Tool failed"
