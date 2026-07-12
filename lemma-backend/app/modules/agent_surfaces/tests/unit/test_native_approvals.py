"""Native request_approval rendering + decision round-trips per platform.

Each platform renders Approve/Deny (and optionally Approve-for-session) buttons
and a tapped button routes back a canonical AgentRunApprovalDecision value via
``ParsedSurfaceInteraction.approval_decision``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.services.display_resource_renderer import (
    build_approval_render_plan,
)


def _plan(*, allow_session: bool = False):
    return build_approval_render_plan(
        conversation_id=uuid4(),
        tool_call_id="tc-approval",
        title="Delete order 42",
        reason="This permanently removes the record.",
        tool_name="pod_write_record",
        allow_session=allow_session,
    )


# --- renderer -------------------------------------------------------------

def test_build_approval_render_plan_defaults_to_approve_deny():
    plan = _plan()
    assert [b.decision for b in plan.buttons] == ["APPROVE_ONCE", "DENY"]
    assert plan.callback_id.endswith("|tc-approval")
    assert "Delete order 42" in plan.to_plain_text()
    assert "approve" in plan.to_plain_text().lower()


def test_build_approval_render_plan_session_button_gated():
    plan = _plan(allow_session=True)
    assert [b.decision for b in plan.buttons] == [
        "APPROVE_ONCE",
        "DENY",
        "APPROVE_FOR_SESSION",
    ]


# --- Slack ----------------------------------------------------------------

def test_slack_approval_blocks_and_parse_round_trip():
    from app.modules.agent_surfaces.platforms.slack.service import _approval_blocks
    from app.modules.agent_surfaces.platforms.slack.parser import SlackMessageParser

    plan = _plan(allow_session=True)
    blocks = _approval_blocks(plan)
    elements = [e for b in blocks if b["type"] == "actions" for e in b["elements"]]
    assert [e["action_id"] for e in elements] == [
        "lemma_approval_approve",
        "lemma_approval_deny",
        "lemma_approval_session",
    ]
    assert all(e["value"] == plan.callback_id for e in elements)

    for action_id, expected in [
        ("lemma_approval_approve", "APPROVE_ONCE"),
        ("lemma_approval_deny", "DENY"),
        ("lemma_approval_session", "APPROVE_FOR_SESSION"),
    ]:
        payload = {
            "type": "block_actions",
            "user": {"id": "U1"},
            "channel": {"id": "C1"},
            "team": {"id": "T1"},
            "container": {"message_ts": "1.2"},
            "message": {"ts": "1.2"},
            "actions": [
                {"action_id": action_id, "value": plan.callback_id, "action_ts": "9"}
            ],
        }
        parsed = SlackMessageParser().parse_interaction(payload)
        assert parsed is not None
        assert parsed.approval_decision == expected
        assert parsed.callback_id == plan.callback_id
        assert parsed.values == {}


# --- Teams ----------------------------------------------------------------

def test_teams_approval_card_and_parse_round_trip():
    from app.modules.agent_surfaces.platforms.teams.adapter import _teams_approval_card
    from app.modules.agent_surfaces.platforms.teams.parser import TeamsMessageParser

    plan = _plan()
    card = _teams_approval_card(plan)
    assert [a["title"] for a in card["actions"]] == ["Approve", "Deny"]

    submit = card["actions"][1]["data"]  # Deny
    parsed = TeamsMessageParser().parse_interaction(
        {
            "value": submit,
            "from": {"id": "u"},
            "conversation": {"id": "c"},
            "channelData": {"tenant": {"id": "t"}},
        }
    )
    assert parsed is not None
    assert parsed.approval_decision == "DENY"
    assert parsed.callback_id == plan.callback_id
    assert parsed.values == {}


# --- WhatsApp -------------------------------------------------------------

def test_whatsapp_approval_buttons_and_parse_round_trip():
    from app.modules.agent_surfaces.platforms.whatsapp.service import (
        _build_whatsapp_approval_interactive,
    )
    from app.modules.agent_surfaces.platforms.whatsapp.parser import (
        WhatsAppMessageParser,
    )

    plan = _plan()
    interactive = _build_whatsapp_approval_interactive(plan)
    assert interactive["type"] == "button"
    button = interactive["action"]["buttons"][0]
    reply_id = button["reply"]["id"]
    assert "__approval__" in reply_id

    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": "15551234567",
                                    "type": "interactive",
                                    "id": "m-1",
                                    "interactive": {
                                        "button_reply": {
                                            "id": reply_id,
                                            "title": "Approve",
                                        }
                                    },
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    parsed = WhatsAppMessageParser().parse_interaction(payload)
    assert parsed is not None
    assert parsed.approval_decision == "APPROVE_ONCE"
    assert parsed.callback_id == plan.callback_id
    assert parsed.values == {}


# --- Telegram (Redis token store mocked) ----------------------------------

@pytest.mark.asyncio
async def test_telegram_approval_token_parse():
    from app.modules.agent_surfaces.platforms.telegram.adapter import (
        TelegramSurfaceAdapter,
    )

    adapter = TelegramSurfaceAdapter()
    payload = {
        "callback_query": {
            "id": "q1",
            "data": "tok",
            "message": {"chat": {"id": "123"}},
            "from": {"id": "999"},
        }
    }
    with patch(
        "app.modules.agent_surfaces.platforms.telegram.adapter.get_callback_token",
        new=AsyncMock(return_value={"callback_id": "conv|tc", "decision": "DENY"}),
    ):
        parsed = await adapter.parse_inbound_interaction(payload)
    assert parsed is not None
    assert parsed.approval_decision == "DENY"
    assert parsed.callback_id == "conv|tc"
    assert parsed.values == {}
