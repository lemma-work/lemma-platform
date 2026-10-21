"""What a person is told after tapping Approve, and whether it stays true.

Two ways the answer to a tap used to be wrong:

- **The card was already cancelled.** The person is shown an approval, does not
  tap it, and types something else. Starting that turn denies the pause on
  their behalf. The card is still on their screen, and tapping Approve on it
  was answered "Done" while the action had not run and never would.
- **The work behind "running it now" failed.** An approved action is handed to
  a worker (an ``agent.approval.execute`` item), and the tap is answered before
  it runs. When it failed, nobody was told.

Driven through the real interaction path against Postgres, on Telegram, where
the fake records what a tap is answered with.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.work import WorkRegistry
from app.modules.agent.events import approval_work
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.tests.e2e.helpers import _messages_for_conversation
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    script_request_approval,
    script_text,
)
from app.modules.agent_surfaces.tests.e2e.surface_journey import (
    SurfaceStage,
    stage_surface,
)
from app.modules.test_support.e2e.agent_work import run_approval_work

pytestmark = pytest.mark.e2e

TOOL_CALL_ID = "call_tap_outcome_1"


def _approval_then(text: str) -> list:
    return [
        script_request_approval(
            tool_name="display_resource",
            args={"request": {"type": "WIDGET", "content": "<div>Ready</div>"}},
            title="Show a widget",
            reason="Needs your OK first",
            tool_call_id=TOOL_CALL_ID,
        ),
        script_text(text),
    ]


async def _telegram(platform_fake, **fixtures) -> SurfaceStage:
    return await stage_surface(
        SurfacePlatform.TELEGRAM,
        fake=platform_fake[SurfacePlatform.TELEGRAM],
        toolsets=["USER_INTERACTION"],
        **fixtures,
    )


def _last_tap_answer(message_store) -> str:
    answers = message_store.get_all("TELEGRAM_CALLBACK_ANSWER")
    assert answers, "the tap was answered with nothing"
    return answers[-1].get("text") or ""


async def test_approve_after_moving_on_says_cancelled_and_runs_nothing(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fixed_test_org,
    message_store,
    monkeypatch,
    platform_fake,
) -> None:
    stage = await _telegram(
        platform_fake,
        authenticated_client=authenticated_client,
        db_session=db_session,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fixed_test_org=fixed_test_org,
        message_store=message_store,
        monkeypatch=monkeypatch,
    )
    # One flat script for both turns. The second message reaches the model in
    # the same request as the denial it caused, so the mock counts it as the
    # first run's next turn rather than as a new conversation turn.
    context = await stage.say(
        "please show the widget",
        script=_approval_then("It is mid-afternoon in Lisbon."),
    )
    assert await stage.saw("Show a widget"), "the request never reached the person"
    approve = await stage.control("Approve")

    await stage.say("actually, what time is it in Lisbon?")
    assert await stage.saw("mid-afternoon in Lisbon")
    # The card is retired as the new turn starts, before anybody taps it.
    card = next(
        sent
        for sent in message_store.get_all("TELEGRAM")
        if (sent.get("reply_markup") or {}).get("inline_keyboard")
    )
    retired = message_store.get_all("TELEGRAM_EDIT_MARKUP")
    assert [edit["reply_markup"] for edit in retired] == [{"inline_keyboard": []}], (
        "the cancelled card kept its live buttons after the new message"
    )
    assert str(retired[0]["message_id"]) == str(
        message_store.get_all("TELEGRAM").index(card) + 1
    )

    await stage.press(approve)

    said = _last_tap_answer(message_store)
    assert said != "Done"
    assert "already cancelled" in said, said
    assert message_store.get_all("TELEGRAM_EDIT_MARKUP"), (
        "the cancelled card kept its live buttons"
    )

    messages = await _messages_for_conversation(
        authenticated_client,
        pod_id=stage.pod_id,
        conversation_id=str(context.conversation_id),
    )
    [result] = [
        message["tool_result"]
        for message in messages
        if message.get("tool_call_id") == TOOL_CALL_ID
        and message.get("kind") == "TOOL_RETURN"
    ]
    assert result["decision"] == "DENY"
    assert result["executed"] is False


@pytest.mark.drives_work
async def test_a_failed_job_after_running_it_now_is_reported_on_the_platform(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fixed_test_org,
    message_store,
    monkeypatch,
    platform_fake,
) -> None:
    stage = await _telegram(
        platform_fake,
        authenticated_client=authenticated_client,
        db_session=db_session,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fixed_test_org=fixed_test_org,
        message_store=message_store,
        monkeypatch=monkeypatch,
    )
    await stage.say("please show the widget", script=_approval_then("Shown."))
    await stage.press(await stage.control("Approve"))
    assert _last_tap_answer(message_store) == "Approved — running it now."

    # The item's last attempt fails before it can run anything: the database
    # refused its connection.
    async def _refused(ctx, payload) -> None:
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    failing = WorkRegistry()
    approval_work.register_approval_work(failing, execute=_refused)
    await db_session.execute(
        text(
            "UPDATE work_items SET attempts = :last "
            "WHERE kind = 'agent.approval.execute'"
        ),
        {"last": approval_work.APPROVAL_RETRY.max_attempts - 1},
    )
    await db_session.commit()
    await run_approval_work(registry=failing)

    assert await stage.saw("may not have happened"), (
        "the job failed after the tap was told it was running, and nobody heard"
    )
