from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.domain.models import (
    SurfaceDisplayAction,
    SurfaceDisplayRenderPlan,
)
from app.modules.agent_surfaces.platforms.email_render import render_email_content


class _FakeHttpResponse:
    def __init__(self, *, json_data=None) -> None:
        self._json_data = json_data or {}

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._json_data


def _email_ctx(platform: str) -> SimpleNamespace:
    """A run context on an email surface with pause support available (so only
    the email guard, not the pause-signal guard, can trigger the fallback)."""
    return SimpleNamespace(
        deps=SimpleNamespace(
            agent_run_id=uuid4(),
            conversation_id=uuid4(),
            supports_pause_signal=True,
            surface_platform=platform,
        ),
        tool_call_id="tool-1",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["RESEND"])
async def test_ask_user_pauses_on_an_email_surface_too(platform):
    """Email can ask. It just cannot ask twice in one turn.

    This used to fail fast, on the reasoning that email "cannot pause". But the
    pause was never synchronous: the run ends, the question goes out inside the
    one reply, the person replies, and maybe_resume_pending_interaction resolves
    it exactly as a tapped Slack button does. The real constraint was only ever
    delivery cardinality -- the question has to ride in the reply -- and an
    agent facing a destructive action can now ask instead of guessing.
    """
    from app.modules.agent.tools.tool_errors import AgentInputRequired
    from app.modules.agent.tools.user_interaction.models import AskUserRequest
    from app.modules.agent.tools.user_interaction.pydantic_adapter import ask_user

    request = AskUserRequest.model_validate(
        {
            "questions": [
                {
                    "header": "color",
                    "question": "Which color?",
                    "options": [{"label": "Red"}, {"label": "Blue"}],
                }
            ]
        }
    )
    with pytest.raises(AgentInputRequired):
        await ask_user(_email_ctx(platform), request)


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["RESEND"])
async def test_request_approval_pauses_on_an_email_surface_too(platform):
    """The case that matters most: a destructive action on an email surface.

    It previously either happened unapproved or silently did not happen, because
    the agent was told to pick a default and proceed.
    """
    from app.modules.agent.tools.tool_errors import AgentInputRequired
    from app.modules.agent.tools.user_interaction.pydantic_adapter import (
        request_approval,
    )

    with pytest.raises(AgentInputRequired):
        await request_approval(
            _email_ctx(platform),
            tool_name="exec_command",
            args={"cmd": "lemma records delete orders --id 42"},
            title="Delete order 42",
        )


@pytest.mark.asyncio
async def test_ask_user_still_pauses_on_chat_surface():
    """The email guard must not affect chat surfaces — ask_user still pauses."""
    from app.modules.agent.tools.tool_errors import AgentInputRequired
    from app.modules.agent.tools.user_interaction.models import AskUserRequest
    from app.modules.agent.tools.user_interaction.pydantic_adapter import ask_user

    request = AskUserRequest.model_validate(
        {
            "questions": [
                {
                    "header": "color",
                    "question": "Which color?",
                    "options": [{"label": "Red"}, {"label": "Blue"}],
                }
            ]
        }
    )
    with pytest.raises(AgentInputRequired):
        await ask_user(_email_ctx("WHATSAPP"), request)


def test_render_email_content_adds_display_resource_html_card():
    plain, html = render_email_content(
        content="I prepared the report.",
        content_type="text",
        display_resource_plans=[
            SurfaceDisplayRenderPlan(
                resource_type="FILE",
                title="report.pdf",
                summary="PDF · 2.3 MB",
                actions=[
                    SurfaceDisplayAction(
                        label="Open file",
                        url="https://app.example.test/pod/p/files?file=/me/report.pdf",
                    )
                ],
            )
        ],
    )

    assert "I prepared the report." in plain
    assert "report.pdf" in plain
    assert "PDF · 2.3 MB" in plain
    assert html is not None
    assert "Open file" in html
    assert "https://app.example.test" in html


def _email_event(platform: str, **reply_target):
    from app.modules.agent_surfaces.domain.entities import (
        ConversationType,
        ParsedInboundSurfaceEvent,
    )

    return ParsedInboundSurfaceEvent(
        platform=platform,
        conversation_type=ConversationType.EXTERNAL_DM,
        external_thread_id=str(reply_target.get("thread_id") or "thread-1"),
        message_text="Need review",
        reply_target=dict(reply_target),
    )
