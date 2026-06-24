from __future__ import annotations

from ..openapi_client.api.agent_tools import (
    agent_tool_report_feedback,
    agent_tool_web_search,
)
from ..openapi_client.models.report_feedback_request import ReportFeedbackRequest
from ..openapi_client.models.report_feedback_response import ReportFeedbackResponse
from ..openapi_client.models.web_search_request import WebSearchRequest
from ..openapi_client.models.web_search_response import WebSearchResponse
from .base import Resource


class Tools(Resource):
    def web_search(self, query: str, *, max_results: int = 10) -> WebSearchResponse:
        return self._call(
            agent_tool_web_search,
            body={"query": query, "max_results": max_results},
            body_model=WebSearchRequest,
        )

    def report_feedback(
        self,
        *,
        category: str,
        subject: str,
        issue_encountered: str,
        expected_behavior: str,
        actual_behavior: str,
        suggested_next_steps: str | None = None,
    ) -> ReportFeedbackResponse:
        body: dict[str, object] = {
            "category": category,
            "subject": subject,
            "issue_encountered": issue_encountered,
            "expected_behavior": expected_behavior,
            "actual_behavior": actual_behavior,
        }
        if suggested_next_steps is not None:
            body["suggested_next_steps"] = suggested_next_steps
        return self._call(
            agent_tool_report_feedback,
            body=body,
            body_model=ReportFeedbackRequest,
        )
