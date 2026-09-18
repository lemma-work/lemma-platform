"""``ask_user``: the question renders natively, and the answer comes back.

One journey, four platforms. The agent asks; the platform renders the choices
in whatever native control it has; the person answers; the run resumes with the
real ``AskUserResponse`` in its history. `stage.answer` covers both shapes of
"answer" -- Telegram and WhatsApp give each option its own button, so answering
is pressing one, while Slack renders a select and Teams an Adaptive Card, so
answering is a submission. Neither restates a token: both read it off what the
agent actually rendered.

Unlike the old ``AskUserHarness``, which hand-crafted a WAITING ``AgentEvent``
without ever calling the tool, these script the LLM only. The real ``ask_user``
runs, genuinely raises ``AgentInputRequired``, and the synthesized response
genuinely flows back through history -- which is what the final assertion here
reads, and what the old fake could never have proved.

Two cases are not that journey and keep their own tests below: Slack refusing
the native render and falling back to text, and email, where the tool is
suppressed entirely and the agent must complete in its single reply.
"""

from __future__ import annotations

import json
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.infrastructure.models import AgentSurface
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _create_agent_surface,
    _ensure_connector_account,
    _messages_for_conversation,
    _resend_payload,
)
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
    wait_for_messages,
    wait_for_slack_text,
)
from app.modules.agent_surfaces.tests.e2e.platform_payloads import (
    slack as slack_payloads,
)
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    process_ingress_and_run_scripted,
    script_ask_user,
    script_text,
)
from app.modules.agent_surfaces.tests.e2e.surface_journey import (
    CHAT_PLATFORMS,
    stage_surface,
)
from app.modules.connectors.domain.connector import AuthProvider

pytestmark = pytest.mark.e2e

_TOOL_CALL_ID = "tool-ask-1"
ANSWERED = "Thanks — recorded your answer."

QUESTIONS = [
    {
        "question": "Pick a color",
        "header": "color",
        "options": [{"label": "Red"}, {"label": "Blue"}],
    }
]

#: Two questions, one of them multi-select and one option carrying a
#: description and a recommendation -- the shape the text fallback below has to
#: render readably when the native control is refused.
_MULTI_QUESTIONS = [
    {
        "question": "Which incident priorities should the agent monitor?",
        "header": "priorities",
        "options": [
            {
                "label": "Critical",
                "description": "Page the on-call engineer immediately.",
                "recommended": True,
            },
            {
                "label": "High",
                "description": "Include high-priority incidents too.",
            },
        ],
        "multi_select": True,
    },
    {
        "question": "Which response channel should receive the summary?",
        "header": "channel",
        "options": [
            {"label": "Slack", "description": "Keep the response in this thread."},
            {"label": "Email", "description": "Send a follow-up email summary."},
        ],
    },
]

#: What a native render looks like in the wire payload, per platform. This is
#: the assertion the matrix exists for -- "natively where the platform supports
#: it" is only a promise if something checks the control is native rather than
#: a paragraph of text.
NATIVE_CONTROL = {
    SurfacePlatform.SLACK: "static_select",
    SurfacePlatform.TEAMS: "AdaptiveCard",
    SurfacePlatform.TELEGRAM: "inline_keyboard",
    SurfacePlatform.WHATSAPP: "interactive",
}


class _FakeScheduleManager:
    async def create_schedule(self, *, account, app_trigger, config) -> str:
        return f"e2e-{app_trigger.id}"

    async def delete_schedule(self, account, provider_id: str) -> None:
        return None

    async def get_schedule(self, account, provider_id: str):
        return None


@pytest.fixture
def platform_fake(fake_slack, fake_teams, fake_telegram, fake_whatsapp):
    return {
        SurfacePlatform.SLACK: fake_slack,
        SurfacePlatform.TEAMS: fake_teams,
        SurfacePlatform.TELEGRAM: fake_telegram,
        SurfacePlatform.WHATSAPP: fake_whatsapp,
    }


@pytest.mark.parametrize("platform", CHAT_PLATFORMS, ids=lambda p: p.value)
async def test_the_question_renders_natively_and_the_answer_resumes_the_run(
    platform: SurfacePlatform,
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fixed_test_org,
    message_store,
    monkeypatch,
    platform_fake,
) -> None:
    stage = await stage_surface(
        platform,
        fake=platform_fake[platform],
        toolsets=["USER_INTERACTION"],
        authenticated_client=authenticated_client,
        db_session=db_session,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fixed_test_org=fixed_test_org,
        message_store=message_store,
        monkeypatch=monkeypatch,
    )
    context = await stage.say(
        "ask me something",
        script=[
            script_ask_user(QUESTIONS, tool_call_id=_TOOL_CALL_ID),
            script_text(ANSWERED),
        ],
    )

    assert await stage.saw("Pick a color"), "the question never reached the person"
    rendered = json.dumps(await stage.delivered(), default=str, ensure_ascii=False)
    assert NATIVE_CONTROL[platform] in rendered, (
        f"{platform.value}: the question was not rendered as a native control"
    )
    assert "Blue" in rendered, "the options were not offered"

    await stage.answer({"color": "Blue"})
    await stage.resume(context)

    assert await stage.saw(ANSWERED[:-1]), "the resumed run never reached the person"

    # The proof the old fake harness could never give: the real AskUserResponse
    # shape flowed through persisted history.
    messages = await _messages_for_conversation(
        authenticated_client,
        pod_id=stage.pod_id,
        conversation_id=str(context.conversation_id),
    )
    tool_return = next(
        message
        for message in messages
        if message.get("tool_call_id") == _TOOL_CALL_ID
        and message.get("kind") == "TOOL_RETURN"
    )
    assert tool_return["tool_result"]["answers"] == {"color": "Blue"}


# -- Not that journey: a refused native render, and email -------------------


async def test_ask_user_slack_native_failure_falls_back_to_text_and_typed_reply(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    message_store,
    monkeypatch,
):
    """A rejected Block Kit payload degrades to usable text and still resumes.

    Slack can reject a structurally valid native card as its platform limits
    evolve. The user must still see every question and be able to answer by
    typing, while the original tool call remains the durable interaction that
    the resumed run completes.
    """
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")
    monkeypatch.setattr(surface_settings, "slack_signing_secret", "slack-secret")
    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="slack",
        credentials={
            "access_token": "xoxb-ask-fallback",
            "scope": "chat:write",
            "api_base_url": fake_slack.base_url,
            "raw_response": {
                "bot_user_id": "U0AGSSTQZLH",
                "team_id": "T0123456",
                "api_base_url": fake_slack.base_url,
            },
        },
    )
    await _create_agent_surface(
        authenticated_client,
        pod_id,
        config={"type": "SLACK", "account_id": str(account.id)},
        toolsets=["USER_INTERACTION"],
    )

    fake_slack.chat_post_blocks_error = "invalid_blocks"
    first_payload = slack_payloads.dm(
        text="Help me configure incident notifications",
        ts="1700000000.610610",
    )
    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="slack", payload=first_payload, headers={}
        ),
        script=[
            script_ask_user(_MULTI_QUESTIONS, tool_call_id=_TOOL_CALL_ID),
            script_text("Incident notification preferences saved."),
        ],
    )
    assert isinstance(context, SurfaceChatContext)
    failed = message_store.get_all("SLACK_FAILED")
    assert failed[-1]["error"] == "invalid_blocks"
    # Every question and the multi-select hint have to survive the degrade —
    # in the body, which is the markdown block; `text` is only the
    # notification preview and is truncated.
    delivered = await wait_for_slack_text(message_store, "1. Which incident priorities")
    fallback = "\n".join(delivered)
    assert "1. Which incident priorities" in fallback
    assert "Critical — Page the on-call engineer immediately. (recommended)" in fallback
    assert "2. Which response channel" in fallback
    assert "you can pick more than one" in fallback

    reply_payload = slack_payloads.dm(
        text="Use standard incident defaults",
        ts="1700000000.610611",
        thread_ts="1700000000.610610",
    )
    resumed = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="slack", payload=reply_payload, headers={}
        ),
    )
    assert isinstance(resumed, SurfaceChatContext)

    delivered = await wait_for_slack_text(
        message_store, "Incident notification preferences saved."
    )
    assert any(
        "Incident notification preferences saved." in text for text in delivered
    ), f"the typed answer never resumed the run: {delivered}"
    messages = await _messages_for_conversation(
        authenticated_client,
        pod_id=pod_id,
        conversation_id=str(context.conversation_id),
    )
    tool_return = next(
        message
        for message in messages
        if message.get("tool_call_id") == _TOOL_CALL_ID
        and message.get("kind") == "TOOL_RETURN"
    )
    assert tool_return["tool_result"]["answers"] == {
        "priorities": "Use standard incident defaults",
        "channel": "Use standard incident defaults",
    }


async def test_ask_user_on_resend_completes_in_the_one_reply(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    message_store,
    monkeypatch,
):
    """Email surfaces never offer ask_user (agent has no USER_INTERACTION
    toolset) — the agent must complete via its single reply-tool call."""
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")
    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="resend",
        credentials={
            "api_key": "resend-token",
            "api_base_url": fake_resend.api_base,
        },
        email="assistant@resend.test",
        provider=AuthProvider.LEMMA,
    )
    _agent, surface = await _create_agent_surface(
        authenticated_client,
        pod_id,
        config={"type": "RESEND", "account_id": str(account.id)},
    )
    assistant_address = surface.get("surface_identity_email")
    if not assistant_address:
        surface_model = await db_session.get(AgentSurface, UUID(surface["id"]))
        assistant_address = surface_model.surface_identity_email
    assert assistant_address

    await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="resend",
            payload=_resend_payload(
                sender_email=fixed_test_user["email"],
                assistant_address=assistant_address,
                message_id="resend-message-ask-user-1",
                text="Can you help over email?",
            ),
            headers={},
        ),
        script=[script_text("Here is my answer.")],
    )

    resend_messages = await wait_for_messages(message_store, "RESEND", min_count=1)
    assert "Here is my answer." in json.dumps(resend_messages[-1])
