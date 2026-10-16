"""When a run fails on a surface, the person is told why and what they can do.

The runner always had the reason -- a quota spent, a provider down, a local
model server not running -- and the web transcript showed it. A surface got one
fixed apology that told everyone to "try it again without resending", which
only Telegram had a control for.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import OperationalError

from app.core.domain.errors import DomainError
from app.modules.agent.contracts.run_failures import user_safe_stored_reason
from app.modules.agent.services.run_finalizer import run_failure_message
from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
)
from app.modules.agent_surfaces.platforms.slack.parser import SlackMessageParser
from app.modules.agent_surfaces.platforms.slack.reply_blocks import reply_blocks
from app.modules.agent_surfaces.platforms.teams.cards import teams_retry_card
from app.modules.agent_surfaces.platforms.teams.parser import TeamsMessageParser
from app.modules.agent_surfaces.platforms.whatsapp.parser import (
    WhatsAppMessageParser,
)
from app.modules.agent_surfaces.platforms.whatsapp.payloads import (
    build_whatsapp_retry_interactive,
)
from app.modules.agent_surfaces.services import telegram_command_service
from app.modules.agent_surfaces.services.egress_progress import SurfaceProgress
from app.modules.agent_surfaces.services.progress_events import failure_notice
from app.modules.agent_surfaces.services.progress_observer import (
    SurfaceAgentRunProgressObserver,
)
from app.modules.agent_surfaces.services.progress_plan import (
    PlanItem,
    SurfacePlan,
    render_plan,
)
from app.modules.usage.contracts import UsageLimitExceededError


_NATIVE_RETRY = ["SLACK", "TEAMS", "WHATSAPP", "TELEGRAM"]


class _UowFactory:
    def __call__(self):
        return self

    async def __aenter__(self):
        return SimpleNamespace()

    async def __aexit__(self, *_: object) -> None:
        return None


def _notice(error: BaseException, platform: str) -> str:
    """The notice a failed run's delivery carries, from what the finalize stored.

    The run row keeps ``run_failure_message(exc)`` as its error, and the
    delivery's sender renders it with ``failure_notice`` for the platform.
    """
    return failure_notice(user_safe_stored_reason(run_failure_message(error)), platform)


@pytest.mark.parametrize("platform", [*_NATIVE_RETRY, "RESEND"])
def test_a_failed_run_says_why_and_offers_only_what_works_there(platform):
    reason = "Ollama isn't running on this computer. Start it, then try again."

    message = failure_notice(reason, platform)

    assert message.startswith(reason)
    assert "without resending" not in message
    assert "“retry”" in message
    if platform in _NATIVE_RETRY:
        assert "Tap Try again" in message
    else:
        # Email has no button; offering one is advice nobody can follow.
        assert "Tap" not in message


def test_a_failure_before_the_harness_names_a_spent_quota():
    assert "usage allowance" in _notice(UsageLimitExceededError(), "SLACK")


def test_a_failure_with_a_written_reason_carries_it():
    error = DomainError("The model this teammate uses was archived.", code="X")

    assert _notice(error, "TELEGRAM").startswith(
        "The model this teammate uses was archived."
    )


def test_an_unknown_failure_keeps_the_plain_apology():
    message = _notice(RuntimeError("Traceback: internal detail"), "SLACK")

    assert message.startswith("I couldn’t finish that request.")
    assert "internal detail" not in message


def test_a_reason_that_quotes_a_credential_is_masked():
    secret = "sk-" + "a1b2c3d4" * 3

    assert secret not in failure_notice(f"Provider refused key {secret}", "SLACK")


async def test_the_observer_says_nothing_itself_when_a_run_fails():
    """The notice is the run's delivery row; the observer only takes display down."""
    sent: list[dict] = []
    egress = SimpleNamespace(
        progress=SimpleNamespace(clear_progress=AsyncMock()),
        send_agent_message_for_conversation=AsyncMock(
            side_effect=lambda **kwargs: sent.append(kwargs)
        ),
    )

    observer = SurfaceAgentRunProgressObserver(
        uow_factory=_UowFactory(), egress_factory=lambda _uow: egress
    )
    await observer.on_run_failed(
        SimpleNamespace(id=uuid4(), metadata={"surface_platform": "SLACK"}),
        UsageLimitExceededError(),
    )

    assert sent == []


# ── the "Try again" control on each platform, and its tap ───────────────────


def test_slack_puts_a_try_again_button_under_the_notice_and_reads_its_tap():
    blocks = reply_blocks(
        "It failed.", {"retry_action": True}, is_dm=True, is_last=True
    )
    button = blocks[-1]["elements"][0]  # type: ignore[index]

    tap = SlackMessageParser().parse_interaction(
        {
            "type": "block_actions",
            "team": {"id": "T1"},
            "user": {"id": "U1"},
            "channel": {"id": "D1"},
            "container": {"message_ts": "1.2", "thread_ts": "1.0"},
            "message": {"ts": "1.2", "thread_ts": "1.0"},
            "actions": [{**button, "action_ts": "9"}],  # type: ignore[dict-item]
        }
    )

    assert tap is not None
    assert tap.action == "retry"


def test_teams_reads_a_tap_on_its_try_again_card():
    data = teams_retry_card("a:1")["actions"][0]["data"]

    tap = TeamsMessageParser().parse_interaction(
        {
            "type": "message",
            "value": data,
            "from": {"id": "29:1", "aadObjectId": "aad-1"},
            "conversation": {"id": "a:1", "conversationType": "personal"},
            "serviceUrl": "https://smba.trafficmanager.net/amer/",
        }
    )

    assert tap is not None
    assert tap.action == "retry"
    assert tap.values == {}
    # Keyed the way the chat's link is: the conversation is the channel.
    assert (tap.external_channel_id, tap.external_thread_id) == ("a:1", "a:1")


def test_whatsapp_reads_a_tap_on_its_try_again_button_in_the_chat_it_came_from():
    button = build_whatsapp_retry_interactive("It failed.")
    assert button is not None
    reply = button["action"]["buttons"][0]["reply"]

    tap = WhatsAppMessageParser().parse_interaction(
        {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "metadata": {"phone_number_id": "pn-1"},
                                "messages": [
                                    {
                                        "from": "15550001",
                                        "id": "wamid-9",
                                        "type": "interactive",
                                        "interactive": {
                                            "type": "button_reply",
                                            "button_reply": reply,
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                }
            ]
        }
    )

    assert tap is not None
    assert tap.action == "retry"
    # The key inbound DMs are linked by, or the tap finds no conversation.
    assert tap.external_thread_id == "15550001@pn-1"
    assert tap.external_channel_id == "pn-1"


def test_a_notice_too_long_for_a_whatsapp_button_goes_as_text():
    assert build_whatsapp_retry_interactive("x" * 2000) is None


# ── a typed "retry" works everywhere ────────────────────────────────────────


def _chat_context(text: str, *, outsider: bool = False):
    from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
    from app.modules.agent_surfaces.domain.models import SurfaceMessageMetadata

    return SurfaceChatContext(
        platform="RESEND",
        pod_id=uuid4(),
        conversation_id=uuid4(),
        user_id=uuid4(),
        message_text=text,
        message_metadata=SurfaceMessageMetadata(surface_platform="RESEND"),
        message_user_id=uuid4(),
        answers_outsider=outsider,
        event=ParsedInboundSurfaceEvent(
            platform="RESEND",
            conversation_type=ConversationType.EXTERNAL_DM,
            external_channel_id="c",
            external_thread_id="t",
            message_text=text,
            is_dm=True,
        ),
    )


@pytest.mark.parametrize(
    ("text", "outsider", "retried"),
    [
        ("retry", False, True),
        ("Retry.", False, True),
        ("please retry the export", False, False),
        ("retry", True, False),
    ],
    ids=["retry", "punctuated", "a-sentence", "outsider"],
)
async def test_a_typed_retry_reruns_the_failed_request(
    monkeypatch, text, outsider, retried
):
    rerun = AsyncMock()
    # Doubled where `agent` publishes it: the other module's contract, not a
    # part of the unit under test.
    monkeypatch.setattr(
        "app.modules.agent.contracts.conversations_for_surfaces.retry_failed_run",
        rerun,
    )
    adapter = AsyncMock()

    handled = await telegram_command_service.handle_typed_retry(
        context=_chat_context(text, outsider=outsider),
        adapter=adapter,
        credentials={},
        uow_factory=_UowFactory(),
    )

    assert handled is retried
    assert rerun.await_count == (1 if retried else 0)


# ── a database error opening the typing bubble does not escape the start ─────


async def test_a_database_error_while_showing_typing_is_contained():
    delivery = SimpleNamespace(
        resolve_egress_target=AsyncMock(
            side_effect=OperationalError("select", {}, Exception("gone"))
        )
    )

    assert (
        await SurfaceProgress(delivery=delivery).show_typing(  # type: ignore[arg-type]
            conversation_id=uuid4()
        )
        is False
    )


# ── the plan is sanitized like the activity line ───────────────────────────


def test_a_plan_item_never_shows_reasoning_or_a_credential():
    secret = "ghp_" + "Z9" * 10
    plan = SurfacePlan(
        items=(
            PlanItem(text=f"Push with token {secret}", done=False),
            PlanItem(text="<think>should I?</think>Open the PR", done=False),
        )
    )

    drawn = render_plan(plan)

    assert secret not in drawn
    assert "should I?" not in drawn
    assert "Open the PR" in drawn
