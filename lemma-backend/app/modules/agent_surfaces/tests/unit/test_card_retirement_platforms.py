"""What each platform does to a card whose pause is over.

The journey -- a new message cancels a pause, and the card on every platform
loses its controls -- is `tests/e2e/test_card_retirement_e2e.py`. These pin the
shapes it cannot assert cheaply: what a rewritten card keeps, where the Teams
token may go, and when WhatsApp says nothing at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.modules.agent_surfaces.domain.decision_card import (
    CardRetirement,
    DecisionCardHandle,
)
from app.modules.agent_surfaces.domain.errors import AgentSurfaceValidationError
from app.modules.agent_surfaces.domain.models import (
    SurfaceApprovalRenderPlan,
    SurfaceQuestionRenderPlan,
)
from app.modules.agent_surfaces.platforms.slack.decision_cards import (
    retired_card,
    without_controls,
)
from app.modules.agent_surfaces.platforms.slack.message_blocks import (
    _approval_blocks,
    _question_blocks,
)
from app.modules.agent_surfaces.platforms.teams.cards import _teams_question_card
from app.modules.agent_surfaces.platforms.teams.decision_cards import (
    _readable_body,
    activity_url,
    retired_activity,
)
from app.modules.agent_surfaces.platforms.whatsapp.decision_cards import (
    retire_whatsapp_card,
)

pytestmark = pytest.mark.asyncio


def _approval_plan() -> SurfaceApprovalRenderPlan:
    return SurfaceApprovalRenderPlan.model_validate(
        {
            "callback_id": "conv|call-a",
            "title": "Delete the report",
            "reason": "It is out of date",
            "buttons": [
                {"label": "Approve", "decision": "APPROVE_ONCE"},
                {"label": "Deny", "decision": "DENY"},
            ],
        }
    )


def _question_plan() -> SurfaceQuestionRenderPlan:
    return SurfaceQuestionRenderPlan.model_validate(
        {
            "callback_id": "conv|call-a",
            "title": "A couple of questions",
            "questions": [
                {
                    "header": "size",
                    "question": "Which size?",
                    "options": [{"label": "Small"}, {"label": "Large"}],
                }
            ],
        }
    )


def _types(blocks: list[dict[str, object]]) -> list[object]:
    return [block.get("type") for block in blocks]


async def test_a_retired_slack_approval_keeps_its_words_and_loses_its_buttons():
    kept = without_controls(_approval_blocks(_approval_plan()))
    handle = DecisionCardHandle(
        platform="SLACK", channel="C1", message_id="1.000100", blocks=kept
    )

    update = retired_card(handle, CardRetirement.SUPERSEDED)

    assert (update["channel"], update["ts"]) == ("C1", "1.000100")
    blocks = update["blocks"]
    assert isinstance(blocks, list)
    assert "actions" not in _types(blocks)
    assert "Delete the report" in str(blocks)
    assert "No longer needed." in str(blocks[-1])


async def test_a_retired_slack_question_loses_its_inputs_as_well():
    """A question card's selects are input blocks, not actions."""
    kept = without_controls(_question_blocks(_question_plan()))

    assert not {"actions", "input"} & set(_types(kept))
    assert "A couple of questions" in str(kept)


async def test_a_retired_teams_card_has_no_actions_and_no_inputs():
    handle = DecisionCardHandle(
        platform="TEAMS",
        channel="19:abc",
        message_id="activity-7",
        blocks=_readable_body(_teams_question_card(_question_plan())),
        extra={"service_url": "https://smba.trafficmanager.net/emea/"},
    )

    activity = retired_activity(handle, CardRetirement.ANSWERED)

    [attachment] = activity["attachments"]  # type: ignore[misc]
    card = attachment["content"]
    assert "actions" not in card
    assert not [
        element
        for element in card["body"]
        if str(element.get("type")).startswith("Input.")
    ]
    assert card["body"][-1]["text"] == "Answered."


async def test_the_teams_token_only_goes_to_a_bot_framework_host():
    """A remembered service URL is held to the inbound rule, so a handle that
    names another host is refused before the token is attached."""
    handle = DecisionCardHandle(
        platform="TEAMS",
        channel="19:abc",
        message_id="activity-7",
        extra={"service_url": "https://attacker.example.com/"},
    )

    with pytest.raises(AgentSurfaceValidationError):
        activity_url(handle)


@dataclass
class _RecordingClient:
    """Stands in for Meta: records what would have been sent."""

    sent: list[dict[str, object]] = field(default_factory=list)

    async def send_text(self, **kwargs: object) -> str:
        self.sent.append(kwargs)
        return "wamid.reply"


def _whatsapp_handle() -> DecisionCardHandle:
    return DecisionCardHandle(
        platform="WHATSAPP",
        channel="15551230000",
        message_id="wamid.card",
        extra={"phone_number_id": "pn-1"},
    )


async def test_a_cancelled_whatsapp_card_is_answered_with_a_quote_of_it():
    """No edit API: the line that says so replies to the card itself."""
    client = _RecordingClient()

    await retire_whatsapp_card(client, _whatsapp_handle(), CardRetirement.SUPERSEDED)  # type: ignore[arg-type]

    [sent] = client.sent
    assert sent["reply_to_message_id"] == "wamid.card"
    assert sent["to"] == "15551230000"
    assert sent["phone_number_id"] == "pn-1"
    assert "No longer needed" in str(sent["body"])


async def test_a_whatsapp_card_answered_in_words_gets_no_extra_line():
    """The typed answer is already right under it."""
    client = _RecordingClient()

    await retire_whatsapp_card(client, _whatsapp_handle(), CardRetirement.ANSWERED)  # type: ignore[arg-type]

    assert client.sent == []
