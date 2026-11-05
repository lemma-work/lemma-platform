"""A private note in a channel's conversation is answered in Lemma only.

A conversation that started in a Teams channel answers there: that is how the
channel sees what it asked for. A note typed in Lemma ("what should we tell
them?") is the one exception, and its run must put nothing on the platform --
not its answer, not a resource it shows, not an approval card.

The unit test of the observer hands it a context already marked as not
delivering. This drives the whole path instead: the note goes in through the
endpoint the web app uses, the run is built and executed for real, and the
fake platform is asked what it received.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from app.modules.agent.domain.private_notes import PRIVATE_NOTE_REQUEST_KEY
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    run_scripted_agent_run,
    script_display_resource,
    script_request_approval,
    script_text,
    suppress_agent_run_enqueue,
)
from app.modules.agent_surfaces.tests.e2e.surface_journey import stage_surface

pytestmark = pytest.mark.e2e

_ANSWER = "Tell them Friday, and leave the discount out."
_SHOWN = "<div class='plan'>Friday rollout</div>"


def _carries(messages: list[dict[str, object]], needle: str) -> bool:
    return any(needle in str(message) for message in messages)


async def test_a_private_notes_answer_card_and_resource_stay_out_of_the_channel(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fixed_test_org,
    message_store,
    monkeypatch,
    fake_teams,
) -> None:
    stage = await stage_surface(
        SurfacePlatform.TEAMS,
        authenticated_client=authenticated_client,
        db_session=db_session,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fixed_test_org=fixed_test_org,
        message_store=message_store,
        monkeypatch=monkeypatch,
        fake=fake_teams,
        toolsets=["USER_INTERACTION"],
    )
    context = await stage.say(
        "Where are we with the rollout?", script=[script_text("Testing this week.")]
    )
    # Read without waiting: delivery happens inside the run, so by the time the
    # run is over its answer is on the platform. That is what lets the check
    # below read "nothing arrived" as "nothing was sent".
    before = list(message_store.get_all(SurfacePlatform.TEAMS.value))
    assert _carries(before, "Testing this week."), before

    with suppress_agent_run_enqueue():
        noted = await authenticated_client.post(
            f"/pods/{stage.pod_id}/conversations/{context.conversation_id}"
            "/messages/append",
            json={
                "content": "What should we tell them about the date?",
                "metadata": {PRIVATE_NOTE_REQUEST_KEY: True},
            },
        )
    assert noted.status_code == status.HTTP_200_OK, noted.text
    assert noted.json()["started_new_run"] is True
    await run_scripted_agent_run(
        db_session,
        conversation_id=context.conversation_id,
        user_id=context.user_id,
        pod_id=context.pod_id,
        agent_name=context.agent_name,
        script=[
            script_display_resource(
                type="WIDGET",
                content=_SHOWN,
                tool_call_id="tool-note-display",
                text=_ANSWER,
            ),
            script_request_approval(
                tool_name="display_resource",
                args={"request": {"type": "WIDGET", "content": _SHOWN}},
                title="Post the plan to the channel",
                tool_call_id="tool-note-approval",
            ),
        ],
    )

    after = message_store.get_all(SurfacePlatform.TEAMS.value)
    assert after == before, (
        f"the note's run reached the channel: {after[len(before) :]}"
    )

    # ...and the note's run did all of it, in Lemma.
    transcript = await authenticated_client.get(
        f"/pods/{stage.pod_id}/conversations/{context.conversation_id}/messages",
        params={"limit": 200},
    )
    assert transcript.status_code == status.HTTP_200_OK, transcript.text
    texts = [str(item.get("text") or "") for item in transcript.json()["items"]]
    assert any("Friday" in text for text in texts), texts
    approvals = await authenticated_client.get(
        f"/pods/{stage.pod_id}/conversations/{context.conversation_id}/approvals"
    )
    assert [item["tool_call_id"] for item in approvals.json()["items"]] == [
        "tool-note-approval"
    ]
