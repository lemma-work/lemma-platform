from __future__ import annotations


import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_surfaces.tests.e2e.helpers import (
    _ensure_connector_account,
    _load_slack_dm_fixture,
)

pytestmark = pytest.mark.e2e


_SLACK_CONFIG_ACCOUNT_CREDENTIALS = {
    "access_token": "xoxb-config-e2e",
    "scope": "assistant:write,chat:write.customize",
}


async def _slack_config_account(db_session, fixed_test_user, fake_slack):
    return await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="slack",
        credentials={
            **_SLACK_CONFIG_ACCOUNT_CREDENTIALS,
            "api_base_url": fake_slack.base_url,
            "raw_response": {
                "bot_user_id": "U0AGSSTQZLH",
                "team_id": "T0123456",
                "api_base_url": fake_slack.base_url,
            },
        },
    )


def _slack_channel_payload(*, text: str, channel_id: str, ts: str) -> dict:
    payload = _load_slack_dm_fixture(text=f"<@U0AGSSTQZLH> {text}", ts=ts)
    event = payload["event"]
    event["type"] = "app_mention"
    event["channel"] = channel_id
    event["channel_type"] = "channel"
    event.pop("assistant_thread", None)
    return payload


async def test_slack_identity_policy_blocks_then_allows_sender_domain(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    message_store,
    monkeypatch,
):
    """A restricted surface sends setup guidance without running the agent;
    widening the allow-list to the sender's domain lets the chat through."""
