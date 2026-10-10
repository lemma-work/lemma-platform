from __future__ import annotations

from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest

from app.core.config import settings
from app.modules.agent.tools.user_interaction.models import (
    DisplayResourceRequest,
    DisplayResourceResponse,
)
from app.modules.agent_surfaces.services.display_resource_renderer import (
    build_display_resource_render_plan,
)


def test_a_filtered_table_links_to_the_conversation_that_showed_it(monkeypatch):
    """The workspace has no route for a filtered table, so the link goes where
    that exact view was drawn rather than to the unfiltered table."""
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
    assert plan.primary_action.url == (
        f"https://app.example.test/t/{pod_id}/conversation/{conversation_id}"
    )
    assert "stage" in plan.to_plain_text()


def test_a_file_link_spells_the_pod_path_as_route_segments(monkeypatch):
    monkeypatch.setattr(settings, "frontend_url", "https://app.example.test")
    pod_id = uuid4()

    plan = build_display_resource_render_plan(
        pod_id=pod_id,
        conversation_id=uuid4(),
        tool_call_id="tool-file-1",
        request=DisplayResourceRequest.model_validate(
            {"type": "FILE", "path": "/LEDFLEX_SKILLS/product catalog #2.md"}
        ),
    )

    assert plan.primary_action is not None
    assert plan.primary_action.url == (
        f"https://app.example.test/t/{pod_id}/file/LEDFLEX_SKILLS/"
        "product%20catalog%20%232.md"
    )


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


def test_display_resource_renderer_links_external_widget_directly():
    plan = build_display_resource_render_plan(
        pod_id=uuid4(),
        conversation_id=uuid4(),
        tool_call_id="tool-widget-1",
        request=DisplayResourceRequest.model_validate(
            {
                "type": "WIDGET",
                "public_url": "https://widgets.example.test/board",
            }
        ),
    )

    assert plan.primary_action is not None
    assert plan.primary_action.url == "https://widgets.example.test/board"
    # A widget is HTML; the card can only say where it opens, and a hosted one
    # opens in the browser rather than inside Lemma.
    assert plan.summary == "Opens in your browser."


def test_display_resource_renderer_links_inline_widget_to_its_conversation(
    monkeypatch,
):
    monkeypatch.setattr(settings, "frontend_url", "https://app.example.test")
    pod_id = uuid4()
    conversation_id = uuid4()

    plan = build_display_resource_render_plan(
        pod_id=pod_id,
        conversation_id=conversation_id,
        tool_call_id="tool-widget-inline",
        request=DisplayResourceRequest.model_validate(
            {"type": "WIDGET", "content": "<div>Ready</div>"}
        ),
    )

    assert plan.primary_action is not None
    assert plan.primary_action.url == (
        f"https://app.example.test/t/{pod_id}/conversation/{conversation_id}"
    )
    assert plan.summary == "Opens in Lemma."


@pytest.mark.parametrize(
    ("request_payload", "path_suffix", "expected_query"),
    [
        (
            {"type": "AGENT", "name": "incident triage"},
            "/profile/incident%20triage",
            {},
        ),
        ({"type": "AGENT"}, "/about", {}),
        ({"type": "FUNCTION", "name": "summarize/incident"}, "", {}),
        (
            {"type": "WORKFLOW", "name": "incident response"},
            "/workflow/incident%20response",
            {},
        ),
        ({"type": "WORKFLOW"}, "/workflows", {}),
        (
            {"type": "APP", "name": "incident dashboard"},
            "/app/incident%20dashboard",
            {},
        ),
        ({"type": "APP"}, "/apps", {}),
        (
            {"type": "SCHEDULE", "name": "daily triage"},
            "/about",
            {"section": ["schedules"]},
        ),
        ({"type": "TABLE", "name": "incident log"}, "/table/incident%20log", {}),
        ({"type": "TABLE"}, "/tables", {}),
        (
            {"type": "FILE", "path": "/me/reports/incident review.pdf"},
            "/file/me/reports/incident%20review.pdf",
            {},
        ),
        ({"type": "FILE"}, "/files", {}),
        ({"type": "WIDGET", "content": "<div>Ready</div>"}, "", {}),
    ],
)
def test_display_resource_internal_urls_match_frontend_route_contract(
    monkeypatch,
    request_payload,
    path_suffix,
    expected_query,
):
    """Every internal link shared by all surface adapters is a `/t/{pod}/...`
    route that `lemma-frontend/src/shell/address.ts` reads."""
    monkeypatch.setattr(settings, "frontend_url", "https://app.example.test/")
    pod_id = uuid4()

    plan = build_display_resource_render_plan(
        pod_id=pod_id,
        request=DisplayResourceRequest.model_validate(request_payload),
    )

    assert plan.primary_action is not None
    parsed = urlparse(plan.primary_action.url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "app.example.test"
    assert parsed.path == f"/t/{pod_id}{path_suffix}"
    assert parse_qs(parsed.query) == expected_query


def test_a_file_card_is_headed_by_the_file_name_not_the_path(monkeypatch):
    """The headline is what the file is called; the path is a detail line.

    The full pod path used to be the title, and the title is also the caption
    stamped onto the native attachment — so a line of directory noise led every
    file the agent sent, whether or not delivery worked.
    """
    monkeypatch.setattr(settings, "frontend_url", "https://app.example.test")

    plan = build_display_resource_render_plan(
        pod_id=uuid4(),
        conversation_id=uuid4(),
        tool_call_id="tool-file-2",
        request=DisplayResourceRequest.model_validate(
            {"type": "FILE", "path": "/me/reports/lemma-aug-2026-shiplog.pdf"}
        ),
    )

    assert plan.title == "lemma-aug-2026-shiplog.pdf"
    assert plan.detail_lines == ["In /me/reports"]
    # The canned sentence is gone: the surface fills the summary with the file's
    # real kind and size, and says nothing when it cannot.
    assert plan.summary is None


def test_a_file_at_the_pod_root_has_no_folder_to_name(monkeypatch):
    monkeypatch.setattr(settings, "frontend_url", "https://app.example.test")

    plan = build_display_resource_render_plan(
        pod_id=uuid4(),
        request=DisplayResourceRequest.model_validate(
            {"type": "FILE", "path": "/readme.md"}
        ),
    )

    assert plan.title == "readme.md"
    assert plan.detail_lines == []


def test_every_card_says_something_specific_about_its_kind(monkeypatch):
    """No resource falls back to "A Lemma resource is ready.".

    A sentence with the shape of an answer and none of the content is the thing
    this card was doing wrong on every surface it reached.
    """
    monkeypatch.setattr(settings, "frontend_url", "https://app.example.test")
    pod_id = uuid4()

    summaries = {
        kind: build_display_resource_render_plan(
            pod_id=pod_id,
            conversation_id=uuid4(),
            request=DisplayResourceRequest.model_validate(
                {"type": kind, "name": "thing"}
            ),
        ).summary
        for kind in ("AGENT", "FUNCTION", "WORKFLOW", "APP", "SCHEDULE")
    }

    assert "A Lemma resource is ready." not in set(summaries.values())
    assert summaries["AGENT"] == "An agent in this pod."
    assert summaries["WORKFLOW"] == "A workflow in this pod."


def test_a_query_result_is_titled_as_one(monkeypatch):
    monkeypatch.setattr(settings, "frontend_url", "https://app.example.test")

    plan = build_display_resource_render_plan(
        pod_id=uuid4(),
        conversation_id=uuid4(),
        tool_call_id="tool-query-1",
        request=DisplayResourceRequest.model_validate(
            {"type": "TABLE", "query": "SELECT id FROM deals"}
        ),
    )

    assert plan.title == "Query results"
    assert plan.detail_lines == ["Query: SELECT id FROM deals"]


def test_a_preview_block_reaches_the_plain_text_render():
    """Platforms without a card render the plan as text; the rows come along."""
    plan = build_display_resource_render_plan(
        pod_id=uuid4(),
        request=DisplayResourceRequest.model_validate(
            {"type": "TABLE", "name": "deals"}
        ),
    ).model_copy(update={"preview_block": "id  stage\n--  -----\n1   won"})

    assert "1   won" in plan.to_plain_text()


def test_every_button_names_what_it_opens(monkeypatch):
    """No card is left with the generic label.

    "Open resource" names a category that exists only inside Lemma, so on a
    chat surface it is a button whose text tells the reader nothing.
    """
    monkeypatch.setattr(settings, "frontend_url", "https://app.example.test")
    pod_id = uuid4()

    labels = {
        kind: build_display_resource_render_plan(
            pod_id=pod_id,
            conversation_id=uuid4(),
            request=DisplayResourceRequest.model_validate(
                {"type": kind, "name": "thing"}
            ),
        ).primary_action
        for kind in ("AGENT", "FUNCTION", "WORKFLOW", "APP", "SCHEDULE")
    }

    assert all(action is not None for action in labels.values())
    named = {kind: action.label for kind, action in labels.items() if action}
    assert "Open resource" not in set(named.values())
    assert named["AGENT"] == "Open agent"
    assert named["APP"] == "Open app"


def test_a_widget_tells_a_chat_app_what_it_shows_when_the_agent_said(monkeypatch):
    """A chat app cannot draw HTML; the agent's own lines are what it can show."""
    monkeypatch.setattr(settings, "frontend_url", "https://app.example.test")

    plan = build_display_resource_render_plan(
        pod_id=uuid4(),
        conversation_id=uuid4(),
        tool_call_id="tool-widget-fallback",
        request=DisplayResourceRequest.model_validate(
            {
                "type": "WIDGET",
                "content": "<div>chart</div>",
                "fallback": "Pipeline: 12 open deals, $340k.\n\n  Acme   is the largest.",
            }
        ),
    )

    assert plan.summary == "Pipeline: 12 open deals, $340k.\nAcme is the largest."
    assert "Opens in Lemma." not in plan.to_plain_text()


def test_a_widget_without_a_fallback_still_says_where_it_opens(monkeypatch):
    monkeypatch.setattr(settings, "frontend_url", "https://app.example.test")

    plan = build_display_resource_render_plan(
        pod_id=uuid4(),
        conversation_id=uuid4(),
        request=DisplayResourceRequest.model_validate(
            {"type": "WIDGET", "content": "<div>chart</div>"}
        ),
    )

    assert plan.summary == "Opens in Lemma."
