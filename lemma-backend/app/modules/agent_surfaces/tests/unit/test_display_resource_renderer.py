from __future__ import annotations

from uuid import uuid4

from app.core.config import settings
from app.modules.agent.tools.user_interaction.models import (
    DisplayResourceRequest,
    DisplayResourceResponse,
)
from app.modules.agent_surfaces.services.display_resource_renderer import (
    build_display_resource_render_plan,
)


def test_display_resource_renderer_builds_table_filter_url(monkeypatch):
    monkeypatch.setattr(settings, "frontend_url", "https://app.example.test")
    pod_id = uuid4()
    conversation_id = uuid4()

    plan = build_display_resource_render_plan(
        pod_id=pod_id,
        conversation_id=conversation_id,
        tool_call_id="tool-display-1",
        request=DisplayResourceRequest.model_validate(
            {
                "type": "TABLE",
                "name": "deals",
                "filters": [{"field": "stage", "op": "eq", "value": "won"}],
            }
        ),
    )

    assert plan.title == "Table: deals"
    assert plan.primary_action is not None
    assert plan.primary_action.url.startswith(
        f"https://app.example.test/pod/{pod_id}/data?tab=deals&filter="
    )
    assert "assistantConversationId=" in plan.primary_action.url
    assert "stage" in plan.to_plain_text()


def test_display_resource_renderer_file_url_is_bare_path(monkeypatch):
    monkeypatch.setattr(settings, "frontend_url", "https://app.example.test")
    pod_id = uuid4()
    conversation_id = uuid4()

    plan = build_display_resource_render_plan(
        pod_id=pod_id,
        conversation_id=conversation_id,
        tool_call_id="tool-file-1",
        request=DisplayResourceRequest.model_validate(
            {"type": "FILE", "path": "/LEDFLEX_SKILLS/product-catalog-reference.md"}
        ),
    )

    assert plan.primary_action is not None
    url = plan.primary_action.url
    # File links carry ONLY the file path: no folder (the viewer derives it) and
    # no assistantConversationId (which would trigger the header-less viewer).
    assert url == (
        f"https://app.example.test/pod/{pod_id}/files"
        "?file=%2FLEDFLEX_SKILLS%2Fproduct-catalog-reference.md"
    )
    assert "assistantConversationId" not in url
    assert "folder=" not in url


def test_display_resource_renderer_reads_browser_url_from_model_output():
    pod_id = uuid4()

    plan = build_display_resource_render_plan(
        pod_id=pod_id,
        request=DisplayResourceRequest.model_validate({"type": "BROWSER"}),
        tool_output=DisplayResourceResponse(
            success=True,
            url="https://browser.example.test/live",
        ),
    )

    assert plan.primary_action is not None
    assert plan.primary_action.url == "https://browser.example.test/live"
