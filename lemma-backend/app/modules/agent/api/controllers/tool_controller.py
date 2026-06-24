"""Agent tool API controller."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Request, status

from app.core.api.dependencies import CurrentUser, UoWDep
from app.modules.agent.infrastructure.models import AgentFeedbackModel
from app.modules.agent.tools.feedback.models import (
    ReportFeedbackRequest,
    ReportFeedbackResponse,
)
from app.modules.agent.tools.web.web import (
    web_search_internal,
)
from app.core.authorization.delegation import WorkloadPrincipalType
from app.core.web_search.web_search import WebSearchRequest, WebSearchResponse

router = APIRouter(prefix="/tools", tags=["agent-tools"])


def _delegated_agent_id(request: Request) -> UUID | None:
    claims = getattr(request.state, "delegation_claims", None)
    if not claims:
        return None
    if claims.actor_type == WorkloadPrincipalType.AGENT:
        return claims.actor_id
    return None


@router.post(
    "/web-search",
    response_model=WebSearchResponse,
    status_code=status.HTTP_200_OK,
    operation_id="agent.tool.web_search",
    summary="Agent Web Search",
    description="Run a raw web search and return structured results.",
)
async def web_search(
    data: WebSearchRequest,
) -> WebSearchResponse:
    return await web_search_internal(None, data)


@router.post(
    "/report-feedback",
    response_model=ReportFeedbackResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="agent.tool.report_feedback",
    summary="Agent Report Feedback",
    description=(
        "Record a maintainer-facing feedback report about system issues, skill "
        "issues, incorrect knowledge, or other unexpected behavior."
    ),
)
async def report_feedback(
    request: Request,
    data: ReportFeedbackRequest,
    user: CurrentUser,
    uow: UoWDep,
) -> ReportFeedbackResponse:
    agent_id = _delegated_agent_id(request)
    feedback = AgentFeedbackModel(
        user_id=user.id,
        agent_id=agent_id,
        category=data.category.value,
        subject=data.subject.strip(),
        issue_encountered=data.issue_encountered.strip(),
        expected_behavior=data.expected_behavior.strip(),
        actual_behavior=data.actual_behavior.strip(),
        suggested_next_steps=(
            data.suggested_next_steps.strip() if data.suggested_next_steps else None
        ),
    )

    async with uow:
        uow.session.add(feedback)
        await uow.commit()

    return ReportFeedbackResponse(
        success=True,
        feedback_id=feedback.id,
        user_id=user.id,
        agent_id=agent_id,
        message="Feedback report recorded successfully.",
    )
