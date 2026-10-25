"""Provider deadlines are met in the HTTP request, with no worker running.

Slack expires a ``trigger_id`` about three seconds after the click; a tapped
Telegram button spins until ``answerCallbackQuery``. Behind any queue those
deadlines are a coin toss, so the request does the work: open the modal, save
the setup, record the decision (and write the approval's execution item),
acknowledge the tap. Nothing here runs a work item -- no dispatcher, no worker
-- and each request is timed.
"""

from __future__ import annotations

import json
import time

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.tests.e2e import surface_journey
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _bound_through_installation,
    _create_agent_surface,
    _ensure_connector_account,
)
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
    build_slack_signature_headers,
)
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    script_request_approval,
    script_text,
)
from app.modules.agent_surfaces.tests.e2e.surface_journey import stage_surface
from app.modules.agent_surfaces.tests.e2e.surface_work_support import (
    clean_surface_work,  # noqa: F401  (fixture)
    scalar,
)

pytestmark = [pytest.mark.e2e, pytest.mark.usefixtures("clean_surface_work")]

#: Slack's own limit is ~3 s; this is the budget the request must keep.
DEADLINE_SECONDS = 1.0


async def _timed_post(client: AsyncClient, path: str, **kwargs) -> float:
    started = time.perf_counter()
    response = await client.post(path, **kwargs)
    elapsed = time.perf_counter() - started
    assert response.status_code == 200, response.text
    return elapsed


async def _no_work_ran(kind_prefix: str) -> None:
    running = await scalar(
        "SELECT count(*) FROM work_items WHERE state = 'running' AND kind LIKE :k",
        k=f"{kind_prefix}%",
    )
    assert running == 0


async def test_the_slack_setup_modal_opens_and_saves_in_the_request(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    message_store,
    monkeypatch,
):
    monkeypatch.setattr(surface_settings, "slack_signing_secret", "slack-secret")
    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="slack",
        credentials={
            "access_token": "xoxb-deadline-e2e",
            "api_base_url": fake_slack.base_url,
            "raw_response": {
                "bot_user_id": "U0AGSSTQZLH",
                "team_id": "T0123456",
                "api_base_url": fake_slack.base_url,
            },
        },
    )
    _, surface = await _create_agent_surface(
        authenticated_client,
        pod_id,
        config={"type": "SLACK", "account_id": str(account.id)},
    )
    # Proven to this installation: a Slack profile email names nobody, since a
    # workspace admin can set it, so the person configuring has to be bound.
    await _bound_through_installation(
        db_session,
        platform="SLACK",
        tenant_id="T0123456",
        installation_id=account.id,
        sender="U0123456",
        user_id=fixed_test_user["id"],
    )

    def signed(payload: dict) -> dict:
        raw = json.dumps(payload).encode()
        return {
            "content": raw,
            "headers": build_slack_signature_headers(
                raw_body=raw, signing_secret="slack-secret"
            ),
        }

    opened = await _timed_post(
        authenticated_client,
        "/surfaces/webhooks/slack",
        **signed(
            {
                "type": "block_actions",
                "trigger_id": "trigger-deadline-1",
                "team": {"id": "T0123456"},
                "api_app_id": "A0123456",
                "user": {"id": "U0123456"},
                "channel": {"id": "C-DEADLINE"},
                "actions": [
                    {"action_id": "lemma_channel_setup", "value": "C-DEADLINE"}
                ],
            }
        ),
    )
    views = message_store.get_all("SLACK_VIEWS_OPEN")
    assert views and "C-DEADLINE" in str(views[-1]["view"]), (
        "the modal was not opened inside the request"
    )

    saved = await _timed_post(
        authenticated_client,
        "/surfaces/webhooks/slack",
        **signed(
            {
                "type": "view_submission",
                "team": {"id": "T0123456"},
                "api_app_id": "A0123456",
                "user": {"id": "U0123456"},
                "view": {
                    "callback_id": "lemma_channel_setup_view",
                    "private_metadata": json.dumps(
                        {"channel_id": "C-DEADLINE", "surface_id": str(surface["id"])}
                    ),
                    "state": {"values": {}},
                },
            }
        ),
    )
    updated = await authenticated_client.get(
        f"/pods/{pod_id}/surfaces/{surface['name']}"
    )
    routes = updated.json()["config"]["channels"]
    assert any(route["channel_id"] == "C-DEADLINE" for route in routes), routes
    # Neither became an ingest item: they were the request's own work.
    assert await scalar("SELECT count(*) FROM surface_inbound_messages") == 0
    await _no_work_ran("surface.")
    print(f"deadline: slack modal open {opened:.3f}s, setup save {saved:.3f}s")
    assert opened < DEADLINE_SECONDS and saved < DEADLINE_SECONDS


async def test_an_approve_tap_is_decided_and_acknowledged_in_the_request(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fixed_test_org,
    message_store,
    monkeypatch,
    platform_fake,
):
    stage = await stage_surface(
        SurfacePlatform.TELEGRAM,
        fake=platform_fake[SurfacePlatform.TELEGRAM],
        toolsets=["USER_INTERACTION"],
        authenticated_client=authenticated_client,
        db_session=db_session,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fixed_test_org=fixed_test_org,
        message_store=message_store,
        monkeypatch=monkeypatch,
    )
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", False)
    await stage.say(
        "please show the widget",
        script=[
            script_request_approval(
                tool_name="display_resource",
                args={"request": {"type": "WIDGET", "content": "<div>Ready</div>"}},
                title="Show a widget",
                reason="Needs your OK first",
                tool_call_id="call_deadline_1",
            ),
            script_text("Shown."),
        ],
    )
    approve = await stage.control("Approve")
    press = surface_journey._PRESS_BUILDERS[SurfacePlatform.TELEGRAM](stage, approve)
    answers_before = len(message_store.get_all("TELEGRAM_CALLBACK_ANSWER"))
    rows_before = await scalar("SELECT count(*) FROM surface_inbound_messages")

    elapsed = await _timed_post(
        authenticated_client,
        "/surfaces/webhooks/telegram",
        content=json.dumps(press).encode(),
        headers={"Content-Type": "application/json"},
    )

    answers = message_store.get_all("TELEGRAM_CALLBACK_ANSWER")
    assert len(answers) == answers_before + 1, "the tap was not answered in the request"
    # The decision is recorded and the approved call's execution is queued --
    # by the request, for a worker that has not run.
    queued = await scalar(
        "SELECT count(*) FROM work_items WHERE kind = 'agent.approval.execute' "
        "AND state = 'queued'"
    )
    assert queued == 1
    # A tap is not a message: it never became an inbound row or an ingest.
    assert await scalar("SELECT count(*) FROM surface_inbound_messages") == rows_before
    await _no_work_ran("agent.approval")
    print(f"deadline: telegram approve tap {elapsed:.3f}s")
    assert elapsed < DEADLINE_SECONDS
