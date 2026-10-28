"""Slack and Teams channels, and group DMs, as the pod's GROUP channels.

Every message the bot hears in a routed Slack or Teams channel, or in a group
chat somebody started with it, is a line of a GROUP channel in Lemma; a
platform thread is a thread there, and the bot's answers join it. These read
that timeline back the way a member does, through the channels API.

A mirror is private unless the platform says the place is open to its
workspace -- asked of Slack or Graph when the first event does not say -- and
turns private when the platform does. Its people are who the platform shows
are there: somebody leaving the place leaves the mirror, unless somebody in
Lemma put them there; the bot taken out archives it. Two pods on one tenant
never see each other's places.
"""

from __future__ import annotations

from app.modules.agent_surfaces.tests.e2e.helpers import share_account_as_connector

import json
import secrets
from typing import Any
from urllib.parse import unquote
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.composition import build_surface_ingress
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.tests.e2e.helpers import (
    REAL_TEAMS_CHANNEL_ID,
    REAL_TEAMS_TENANT_ID,
    _create_agent,
    _create_agent_surface,
    _ensure_connector_account,
    _load_teams_channel_mention_fixture,
    _seed_external_user,
)
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.services.group_updates import apply_group_updates
from app.modules.channels.infrastructure.models import (
    ChannelMemberModel,
    ChannelModel,
    ChannelSourceModel,
    ExternalAccountModel,
)
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
    slack_delivered,
    wait_for_messages,
    wait_for_slack_text,
)
from app.modules.agent_surfaces.tests.e2e.platform_payloads import (
    slack as slack_payloads,
    teams as teams_payloads,
)
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    process_ingress_and_run_scripted,
    script_text,
)

pytestmark = pytest.mark.e2e

SUPPORT_CHANNEL = "C0WSCHAN01"
SUPPORT_NAME = "support"
GROUP_CHAT_ID = "19:abc0workspacechannels@thread.v2"


# ── staging ───────────────────────────────────────────────────────────────


def _ts() -> str:
    """A Slack ``ts`` no other test has used: a message is claimed once."""
    return f"17{secrets.randbelow(10**8):08d}.{secrets.randbelow(10**6):06d}"


async def _slack_surface(
    client: AsyncClient,
    db_session: AsyncSession,
    *,
    pod_id: str,
    user_id: str,
    fake_slack: Any,
) -> tuple[dict, dict]:
    """A Slack surface with one routed channel, named."""
    account = await _ensure_connector_account(
        db_session,
        user_id=user_id,
        connector_id="slack",
        credentials={
            "access_token": "xoxb-workspace-channels",
            "scope": "chat:write",
            "api_base_url": fake_slack.base_url,
            "raw_response": {
                "bot_user_id": slack_payloads.BOT_USER_ID,
                "team_id": slack_payloads.TEAM_ID,
                "api_base_url": fake_slack.base_url,
            },
        },
    )
    agent = await _create_agent(client, pod_id)
    response = await client.post(
        f"/pods/{pod_id}/surfaces",
        json={
            "platform": "SLACK",
            "pod_connector": await share_account_as_connector(
                client, pod_id, account.id
            ),
            "default_agent_name": agent["name"],
            "config": {
                "channels": [
                    {"channel_id": SUPPORT_CHANNEL, "channel_name": SUPPORT_NAME}
                ]
            },
        },
    )
    assert response.status_code == 200, response.text
    return agent, response.json()


def _slack_in_channel(
    text: str, *, ts: str, thread_ts: str | None = None, mention: bool = False
) -> dict[str, Any]:
    return slack_payloads.envelope(
        text=f"<@{slack_payloads.BOT_USER_ID}> {text}" if mention else text,
        ts=ts,
        channel=SUPPORT_CHANNEL,
        channel_type="channel",
        event_type="app_mention" if mention else "message",
        thread_ts=thread_ts,
    )


def _stage_teams(monkeypatch, fake_teams) -> None:
    from app.core.config import settings as app_settings
    from app.modules.agent_surfaces.platforms.teams.adapter import (
        TeamsSurfaceAdapter,
    )

    async def _fake_bot_token(self, tenant_id: str) -> str | None:
        del self, tenant_id
        return "teams-bot-token"

    async def _disable_graph(self, tenant_id: str) -> str | None:
        del self, tenant_id
        return None

    monkeypatch.setattr(TeamsSurfaceAdapter, "_get_bot_token", _fake_bot_token)
    monkeypatch.setattr(TeamsSurfaceAdapter, "_get_graph_token", _disable_graph)
    monkeypatch.setattr(
        surface_settings,
        "microsoft_bot_openid_config_url",
        fake_teams.openid_config_url,
    )
    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")
    monkeypatch.setattr(surface_settings, "microsoft_bot_app_id", "teams-app-id")


async def _teams_surface(
    client: AsyncClient, db_session: AsyncSession, *, pod_id: str, user_id: str
) -> tuple[dict, dict]:
    """A Teams surface routed to the captured channel only."""
    account = await _ensure_connector_account(
        db_session,
        user_id=user_id,
        connector_id="microsoft_teams",
        credentials={
            "access_token": "teams-token",
            "user_data": {"tenant_id": REAL_TEAMS_TENANT_ID},
        },
    )
    return await _create_agent_surface(
        client,
        pod_id,
        config={
            "type": "TEAMS",
            "account_id": str(account.id),
            "allowed_channel_ids": [REAL_TEAMS_CHANNEL_ID],
        },
    )


def _teams_group_chat(
    *, service_url: str, text: str, activity_id: str, mention: bool
) -> dict[str, Any]:
    """The captured activity, said in a group chat: no team, no channel."""
    payload = teams_payloads.channel_mention(
        service_url=service_url, text=text, activity_id=activity_id
    )
    payload["conversation"] = {
        "isGroup": True,
        "conversationType": "groupChat",
        "tenantId": teams_payloads.TENANT_ID,
        "id": GROUP_CHAT_ID,
    }
    payload["channelData"] = {"tenant": {"id": teams_payloads.TENANT_ID}}
    if not mention:
        payload["text"] = text
        payload.pop("entities", None)
        payload.pop("attachments", None)
    return payload


async def _hear(db_session: AsyncSession, source: str, payload: dict) -> Any:
    """One inbound message through ingress, without driving a run."""
    uow = SqlAlchemyUnitOfWork(db_session)
    context = await build_surface_ingress(uow).prepare_ingress(
        SurfacePlatformWebhookIngress(source=source, payload=payload, headers={})
    )
    await uow.commit()
    return context


# ── reading the timeline as a member ──────────────────────────────────────


async def _group_channels(
    client: AsyncClient, pod_id: str, *, platform: str
) -> list[dict]:
    response = await client.get(f"/pods/{pod_id}/channels", params={"kind": "GROUP"})
    assert response.status_code == 200, response.text
    return [
        item
        for item in response.json()["items"]
        if any(source["platform"] == platform for source in item["sources"])
    ]


async def _the_group_channel(
    client: AsyncClient, pod_id: str, *, platform: str
) -> dict:
    channels = await _group_channels(client, pod_id, platform=platform)
    assert len(channels) == 1, channels
    return channels[0]


async def _timeline(client: AsyncClient, pod_id: str, channel_id: str) -> list[dict]:
    response = await client.get(f"/pods/{pod_id}/channels/{channel_id}/messages")
    assert response.status_code == 200, response.text
    return response.json()["items"]


async def _thread(
    client: AsyncClient, pod_id: str, channel_id: str, message_id: str
) -> list[dict]:
    response = await client.get(
        f"/pods/{pod_id}/channels/{channel_id}/messages/{message_id}/thread"
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _said(lines: list[dict], text: str) -> dict:
    found = [line for line in lines if text in (line["text"] or "")]
    assert len(found) == 1, f"{text!r} in {[line['text'] for line in lines]}"
    return found[0]


# ── Slack ─────────────────────────────────────────────────────────────────


async def test_slack_channel_talk_is_logged_and_its_thread_kept(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    message_store,
):
    """Unaddressed talk in a routed Slack channel is lines, not answers, and a
    thread reply is a reply under its root."""
    pod_id = test_pod["id"]
    await _slack_surface(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user_id=fixed_test_user["id"],
        fake_slack=fake_slack,
    )
    await _seed_external_user(
        db_session,
        platform="SLACK",
        external_user_id=slack_payloads.SENDER_ID,
        tenant_id=slack_payloads.TEAM_ID,
        resolved_user_id=UUID(fixed_test_user["id"]),
    )

    root_ts = _ts()
    await _hear(
        db_session, "slack", _slack_in_channel("the deploy is at four", ts=root_ts)
    )
    await _hear(
        db_session,
        "slack",
        _slack_in_channel("moved to five", ts=_ts(), thread_ts=root_ts),
    )

    assert slack_delivered(message_store) == []
    channel = await _the_group_channel(authenticated_client, pod_id, platform="SLACK")
    assert channel["title"] == f"#{SUPPORT_NAME}"

    lines = await _timeline(authenticated_client, pod_id, channel["id"])
    assert [line["text"] for line in lines] == ["the deploy is at four"]
    root = lines[0]
    assert root["sender"]["kind"] == "MEMBER"
    assert root["sender"]["id"] == fixed_test_user["id"]
    assert root["reply_count"] == 1

    replies = await _thread(authenticated_client, pod_id, channel["id"], root["id"])
    assert [line["text"] for line in replies] == ["moved to five"]
    assert replies[0]["thread_root_id"] == root["id"]
    assert replies[0]["sender"]["kind"] == "MEMBER"


async def test_slack_channel_mention_is_answered_in_its_thread(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    message_store,
):
    """A mention in a routed Slack channel is answered, and the answer is a
    reply under the person's line."""
    pod_id = test_pod["id"]
    agent, _ = await _slack_surface(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user_id=fixed_test_user["id"],
        fake_slack=fake_slack,
    )
    # Known in the workspace already: a Slack profile email names nobody.
    await _seed_external_user(
        db_session,
        platform="SLACK",
        external_user_id=slack_payloads.SENDER_ID,
        tenant_id=slack_payloads.TEAM_ID,
        resolved_user_id=UUID(fixed_test_user["id"]),
    )
    answer = f"Four o'clock, in #support [{uuid4().hex[:6]}]"

    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="slack",
            payload=_slack_in_channel("when is the deploy?", ts=_ts(), mention=True),
            headers={},
        ),
        script=[script_text(answer)],
    )
    assert isinstance(context, SurfaceChatContext)
    await wait_for_slack_text(message_store, answer)

    channel = await _the_group_channel(authenticated_client, pod_id, platform="SLACK")
    lines = await _timeline(authenticated_client, pod_id, channel["id"])
    asked = _said(lines, "when is the deploy?")
    assert asked["sender"]["kind"] == "MEMBER"
    assert asked["reply_count"] == 1
    assert not any(answer in (line["text"] or "") for line in lines)

    replies = await _thread(authenticated_client, pod_id, channel["id"], asked["id"])
    answered = _said(replies, answer)
    assert answered["sender"]["kind"] == "AGENT"
    assert answered["sender"]["id"] == agent["id"]
    assert answered["thread_root_id"] == asked["id"]


# ── Teams ─────────────────────────────────────────────────────────────────


async def test_teams_channel_mention_and_answer_are_the_channel_timeline(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_teams,
    message_store,
    monkeypatch,
):
    """The captured Teams channel mention, answered; both are lines of the
    channel's GROUP timeline, the answer in the mention's thread."""
    _stage_teams(monkeypatch, fake_teams)
    pod_id = test_pod["id"]
    await _teams_surface(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user_id=fixed_test_user["id"],
    )

    payload = _load_teams_channel_mention_fixture(fake_teams)
    response = await authenticated_client.post(
        "/surfaces/webhooks/teams",
        content=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": (
                f"Bearer {fake_teams.issue_webhook_token(audience='teams-app-id')}"
            ),
        },
    )
    assert response.status_code == 200, response.text
    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(source="teams", payload=payload, headers={}),
        script=[script_text("E2E agent reply [TEAMS channel line]")],
    )
    assert isinstance(context, SurfaceChatContext)
    await wait_for_messages(
        message_store,
        "TEAMS",
        predicate=lambda item: (
            "E2E agent reply [TEAMS channel line]"
            in str((item.get("body") or {}).get("text") or "")
        ),
    )

    channel = await _the_group_channel(authenticated_client, pod_id, platform="TEAMS")
    lines = await _timeline(authenticated_client, pod_id, channel["id"])
    asked = _said(lines, "Do you know shera?")
    assert asked["sender"]["kind"] == "MEMBER"
    replies = await _thread(authenticated_client, pod_id, channel["id"], asked["id"])
    answered = _said(replies, "E2E agent reply [TEAMS channel line]")
    assert answered["sender"]["kind"] == "AGENT"


async def test_teams_group_chat_needs_no_route_and_answers_only_a_mention(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_teams,
    message_store,
    monkeypatch,
):
    """A Teams group chat is a GROUP channel without a route: a mention is
    answered into the chat, and other talk is logged and left alone."""
    _stage_teams(monkeypatch, fake_teams)
    pod_id = test_pod["id"]
    await _teams_surface(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user_id=fixed_test_user["id"],
    )
    answer = "E2E agent reply [TEAMS group chat]"

    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="teams",
            payload=_teams_group_chat(
                service_url=fake_teams.service_url,
                text="who has the release notes?",
                activity_id=f"group-chat-{uuid4().hex[:8]}",
                mention=True,
            ),
            headers={},
        ),
        script=[script_text(answer)],
    )
    assert isinstance(context, SurfaceChatContext)
    delivered = await wait_for_messages(
        message_store,
        "TEAMS",
        predicate=lambda item: answer in str((item.get("body") or {}).get("text")),
    )
    to_chat = [
        item
        for item in delivered
        if answer in str((item.get("body") or {}).get("text"))
    ]
    assert GROUP_CHAT_ID in unquote(to_chat[-1]["path"])

    sent_before = len(message_store.get_all("TEAMS"))
    unaddressed = await _hear(
        db_session,
        "teams",
        _teams_group_chat(
            service_url=fake_teams.service_url,
            text="thanks, found them",
            activity_id=f"group-chat-{uuid4().hex[:8]}",
            mention=False,
        ),
    )
    assert not isinstance(unaddressed, SurfaceChatContext)
    assert len(message_store.get_all("TEAMS")) == sent_before

    channel = await _the_group_channel(authenticated_client, pod_id, platform="TEAMS")
    assert channel["title"] == "Group chat"
    lines = await _timeline(authenticated_client, pod_id, channel["id"])
    asked = _said(lines, "who has the release notes?")
    answered = _said(lines, answer)
    unaddressed_line = _said(lines, "thanks, found them")
    assert answered["sender"]["kind"] == "AGENT"
    assert asked["sender"]["kind"] == "MEMBER"
    assert unaddressed_line["sender"]["kind"] == "MEMBER"
    # A group chat has no threads: everything is said in the chat itself.
    assert all(line["thread_root_id"] is None for line in lines)


async def test_teams_personal_chat_is_still_a_dm(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_teams,
    message_store,
    monkeypatch,
):
    """A Teams personal chat is answered as a DM and makes no GROUP channel."""
    _stage_teams(monkeypatch, fake_teams)
    pod_id = test_pod["id"]
    await _teams_surface(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user_id=fixed_test_user["id"],
    )

    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="teams",
            payload=teams_payloads.personal_dm(
                service_url=fake_teams.service_url,
                text="just between us",
                activity_id=f"personal-{uuid4().hex[:8]}",
                conversation_id=f"a:personal-{uuid4().hex[:8]}",
            ),
            headers={},
        ),
        script=[script_text("E2E agent reply [TEAMS personal]")],
    )
    assert isinstance(context, SurfaceChatContext)
    await wait_for_messages(
        message_store,
        "TEAMS",
        predicate=lambda item: (
            "E2E agent reply [TEAMS personal]"
            in str((item.get("body") or {}).get("text"))
        ),
    )

    assert await _group_channels(authenticated_client, pod_id, platform="TEAMS") == []


# ── privacy: only the platform's word opens a place ───────────────────────


@pytest.fixture(autouse=True)
def _own_place_privacy_cache():
    """Each test its own memory of what the platform said: the fakes answer
    one channel id differently from test to test."""
    from app.core.config import settings
    from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
    from app.modules.agent_surfaces.caches import PLACE_PRIVACY, surface_caches

    cache = RedisJsonCache(
        redis_url=settings.redis_url,
        key_prefix=f"test:place-privacy:{uuid4().hex}",
        ttl_seconds=PLACE_PRIVACY.ttl_seconds,
    )
    with surface_caches.substituted(PLACE_PRIVACY, cache):
        yield


async def _mirror(db_session: AsyncSession, external_channel_id: str) -> dict:
    """The mirror of a place, as the database keeps it: its channel's
    visibility and archive, and its source's privacy."""
    rows = (
        await db_session.execute(
            select(
                ChannelModel.id,
                ChannelModel.pod_id,
                ChannelModel.visibility,
                ChannelModel.archived_at,
                ChannelSourceModel.is_private,
                ChannelSourceModel.shared_externally,
                ChannelSourceModel.account_id,
            )
            .join(ChannelSourceModel, ChannelSourceModel.channel_id == ChannelModel.id)
            .where(ChannelSourceModel.external_channel_id == external_channel_id)
        )
    ).all()
    assert len(rows) == 1, rows
    row = rows[0]
    return {
        "channel_id": row[0],
        "pod_id": row[1],
        "visibility": row[2],
        "archived": row[3] is not None,
        "is_private": row[4],
        "shared_externally": row[5],
        "account_id": row[6],
    }


async def _seated(db_session: AsyncSession, channel_id: UUID, user_id: str) -> bool:
    found = await db_session.scalar(
        select(ChannelMemberModel.id).where(
            ChannelMemberModel.channel_id == channel_id,
            ChannelMemberModel.user_id == UUID(user_id),
        )
    )
    return found is not None


def _app_mention(text: str, *, ts: str) -> dict[str, Any]:
    """Slack's ``app_mention``: unlike a ``message``, it names no channel type."""
    payload = _slack_in_channel(text, ts=ts, mention=True)
    payload["event"].pop("channel_type")
    return payload


def _slack_event(event: dict[str, Any]) -> SurfacePlatformWebhookIngress:
    """A Slack event about the channel itself, in the captured envelope."""
    payload = slack_payloads.envelope()
    payload["event"] = event
    payload["event_id"] = f"Ev{uuid4().hex[:10]}"
    return SurfacePlatformWebhookIngress(source="slack", payload=payload, headers={})


async def _platform_says(db_session: AsyncSession, request) -> None:
    await apply_group_updates(
        SqlAlchemyUnitOfWork(db_session),
        request,
        adapters=SurfacePlatformAdapterRegistry(),
    )


async def test_an_app_mention_first_in_a_private_slack_channel_makes_a_private_mirror(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    message_store,
):
    """The first thing the bot hears in a private channel is a mention, which
    says nothing of the channel's type. Slack is asked, and its mirror is
    private -- not public to the whole pod."""
    fake_slack.private_channels.add(SUPPORT_CHANNEL)
    await _slack_surface(
        authenticated_client,
        db_session,
        pod_id=test_pod["id"],
        user_id=fixed_test_user["id"],
        fake_slack=fake_slack,
    )

    await _hear(db_session, "slack", _app_mention("is the deploy on?", ts=_ts()))

    mirror = await _mirror(db_session, SUPPORT_CHANNEL)
    assert (mirror["visibility"], mirror["is_private"]) == ("PRIVATE", True)
    asked = [c["channel"] for c in message_store.get_all("SLACK_CHANNEL_INFO")]
    assert asked == [SUPPORT_CHANNEL]


async def test_a_public_slack_channel_is_public_on_slacks_word_asked_once(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    message_store,
):
    await _slack_surface(
        authenticated_client,
        db_session,
        pod_id=test_pod["id"],
        user_id=fixed_test_user["id"],
        fake_slack=fake_slack,
    )

    await _hear(db_session, "slack", _app_mention("first", ts=_ts()))
    await _hear(db_session, "slack", _app_mention("second", ts=_ts()))

    mirror = await _mirror(db_session, SUPPORT_CHANNEL)
    assert (mirror["visibility"], mirror["is_private"]) == ("PUBLIC", False)
    # Asked before the mirror was made, and not again once it was.
    assert len(message_store.get_all("SLACK_CHANNEL_INFO")) == 1


async def test_a_slack_channel_turned_private_takes_its_mirror_private_for_good(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
):
    """Slack names the new type on every message once a channel is converted.
    The mirror follows -- and a stray message naming it public again (one sent
    before the conversion, delivered late) opens nothing."""
    await _slack_surface(
        authenticated_client,
        db_session,
        pod_id=test_pod["id"],
        user_id=fixed_test_user["id"],
        fake_slack=fake_slack,
    )
    await _hear(db_session, "slack", _slack_in_channel("open talk", ts=_ts()))
    assert (await _mirror(db_session, SUPPORT_CHANNEL))["visibility"] == "PUBLIC"

    converted = _slack_in_channel("now among ourselves", ts=_ts())
    converted["event"]["channel_type"] = "group"
    await _hear(db_session, "slack", converted)
    await _hear(db_session, "slack", _slack_in_channel("late, from before", ts=_ts()))

    mirror = await _mirror(db_session, SUPPORT_CHANNEL)
    assert (mirror["visibility"], mirror["is_private"]) == ("PRIVATE", True)


async def test_a_teams_channel_graph_cannot_vouch_for_is_private(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_teams,
    monkeypatch,
):
    """The captured Teams channel message names no membership, and with no
    Graph to ask the mirror fails closed."""
    _stage_teams(monkeypatch, fake_teams)
    await _teams_surface(
        authenticated_client,
        db_session,
        pod_id=test_pod["id"],
        user_id=fixed_test_user["id"],
    )

    await _hear(db_session, "teams", _load_teams_channel_mention_fixture(fake_teams))

    mirror = await _mirror(db_session, REAL_TEAMS_CHANNEL_ID)
    assert (mirror["visibility"], mirror["is_private"]) == ("PRIVATE", True)


def _graph(monkeypatch, fake_teams) -> None:
    from app.modules.agent_surfaces.platforms.teams import client as teams_client
    from app.modules.agent_surfaces.platforms.teams.adapter import (
        TeamsSurfaceAdapter,
    )

    async def _graph_token(self, tenant_id: str) -> str | None:
        del self, tenant_id
        return "graph-token"

    monkeypatch.setattr(TeamsSurfaceAdapter, "_get_graph_token", _graph_token)
    monkeypatch.setattr(teams_client, "GRAPH_BASE", fake_teams.graph_base_url)


@pytest.mark.parametrize(
    ("membership", "visibility"),
    [("standard", "PUBLIC"), ("private", "PRIVATE"), ("shared", "PRIVATE")],
)
async def test_a_teams_channel_is_what_graph_says_its_membership_is(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_teams,
    message_store,
    monkeypatch,
    membership,
    visibility,
):
    _stage_teams(monkeypatch, fake_teams)
    _graph(monkeypatch, fake_teams)
    fake_teams.channel_membership[REAL_TEAMS_CHANNEL_ID] = membership
    await _teams_surface(
        authenticated_client,
        db_session,
        pod_id=test_pod["id"],
        user_id=fixed_test_user["id"],
    )

    await _hear(db_session, "teams", _load_teams_channel_mention_fixture(fake_teams))

    assert (await _mirror(db_session, REAL_TEAMS_CHANNEL_ID))["visibility"] == (
        visibility
    )
    [asked] = message_store.get_all("TEAMS_GRAPH_CHANNEL")
    assert asked["channel_id"] == REAL_TEAMS_CHANNEL_ID


# ── membership: who the platform says left, leaves ────────────────────────


async def test_a_member_who_leaves_a_private_slack_channel_leaves_its_mirror(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
):
    """Seated because they spoke there, out when Slack says they left. An
    admin then finds the group on the bot's settings, joins it on request, and
    stays when the platform's roster moves -- the pod put them there. The bot
    taken out archives the mirror; invited back, it returns."""
    pod_id = test_pod["id"]
    fake_slack.private_channels.add(SUPPORT_CHANNEL)
    _, surface = await _slack_surface(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user_id=fixed_test_user["id"],
        fake_slack=fake_slack,
    )
    await _seed_external_user(
        db_session,
        platform="SLACK",
        external_user_id=slack_payloads.SENDER_ID,
        tenant_id=slack_payloads.TEAM_ID,
        resolved_user_id=UUID(fixed_test_user["id"]),
    )
    said = _slack_in_channel("in the private room", ts=_ts())
    said["event"]["channel_type"] = "group"
    await _hear(db_session, "slack", said)
    mirror = await _mirror(db_session, SUPPORT_CHANNEL)
    assert mirror["visibility"] == "PRIVATE"
    assert await _seated(db_session, mirror["channel_id"], fixed_test_user["id"])

    left = {
        "type": "member_left_channel",
        "user": slack_payloads.SENDER_ID,
        "channel": SUPPORT_CHANNEL,
        "channel_type": "G",
        "team": slack_payloads.TEAM_ID,
    }
    await _platform_says(db_session, _slack_event(left))
    assert not await _seated(db_session, mirror["channel_id"], fixed_test_user["id"])
    channel_path = f"/pods/{pod_id}/channels/{mirror['channel_id']}"
    assert (await authenticated_client.get(channel_path)).status_code == 404

    # A mirror nobody in Lemma is in any more is still an admin's to find.
    by_bot = await authenticated_client.get(
        f"/pods/{pod_id}/channels", params={"source_surface": surface["name"]}
    )
    assert by_bot.status_code == 200, by_bot.text
    [listed] = by_bot.json()["items"]
    assert (listed["id"], listed["joined"]) == (str(mirror["channel_id"]), False)
    source_id = await db_session.scalar(
        select(ChannelSourceModel.id).where(
            ChannelSourceModel.external_channel_id == SUPPORT_CHANNEL
        )
    )
    # Its settings too: the admin manages the pod's channels.
    source_settings = await authenticated_client.get(
        f"{channel_path}/sources/{source_id}"
    )
    assert source_settings.status_code == 200, source_settings.text
    joined = await authenticated_client.post(f"{channel_path}/members/me")
    assert joined.status_code == 200, joined.text
    assert (await authenticated_client.get(channel_path)).status_code == 200

    # Joined on request: the pod's choice, which the platform's roster does
    # not undo.
    await _platform_says(db_session, _slack_event(left))
    assert await _seated(db_session, mirror["channel_id"], fixed_test_user["id"])

    await _platform_says(
        db_session,
        _slack_event({"type": "group_left", "channel": SUPPORT_CHANNEL}),
    )
    assert (await _mirror(db_session, SUPPORT_CHANNEL))["archived"] is True
    await _platform_says(
        db_session,
        _slack_event(
            {
                "type": "member_joined_channel",
                "user": slack_payloads.BOT_USER_ID,
                "channel": SUPPORT_CHANNEL,
                "channel_type": "G",
                "team": slack_payloads.TEAM_ID,
            }
        ),
    )
    assert (await _mirror(db_session, SUPPORT_CHANNEL))["archived"] is False


async def test_a_member_who_joins_a_slack_channel_is_in_its_mirror_before_speaking(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
):
    """Out when Slack says they left, back in when it says they joined --
    seated by the platform, so the next leave takes them out again."""
    pod_id = test_pod["id"]
    await _slack_surface(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user_id=fixed_test_user["id"],
        fake_slack=fake_slack,
    )
    await _seed_external_user(
        db_session,
        platform="SLACK",
        external_user_id=slack_payloads.SENDER_ID,
        tenant_id=slack_payloads.TEAM_ID,
        resolved_user_id=UUID(fixed_test_user["id"]),
    )
    await _hear(db_session, "slack", _slack_in_channel("morning", ts=_ts()))
    mirror = await _mirror(db_session, SUPPORT_CHANNEL)
    moved = {
        "user": slack_payloads.SENDER_ID,
        "channel": SUPPORT_CHANNEL,
        "channel_type": "C",
        "team": slack_payloads.TEAM_ID,
    }
    await _platform_says(
        db_session, _slack_event({"type": "member_left_channel", **moved})
    )
    assert not await _seated(db_session, mirror["channel_id"], fixed_test_user["id"])

    await _platform_says(
        db_session, _slack_event({"type": "member_joined_channel", **moved})
    )
    assert await _seated(db_session, mirror["channel_id"], fixed_test_user["id"])

    await _platform_says(
        db_session, _slack_event({"type": "member_left_channel", **moved})
    )
    assert not await _seated(db_session, mirror["channel_id"], fixed_test_user["id"])


async def test_a_slack_leave_from_another_workspace_changes_nothing_here(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
):
    """A channel id is the workspace's: an event naming another team is not
    about this pod's place."""
    fake_slack.private_channels.add(SUPPORT_CHANNEL)
    await _slack_surface(
        authenticated_client,
        db_session,
        pod_id=test_pod["id"],
        user_id=fixed_test_user["id"],
        fake_slack=fake_slack,
    )
    await _seed_external_user(
        db_session,
        platform="SLACK",
        external_user_id=slack_payloads.SENDER_ID,
        tenant_id=slack_payloads.TEAM_ID,
        resolved_user_id=UUID(fixed_test_user["id"]),
    )
    said = _slack_in_channel("in the private room", ts=_ts())
    said["event"]["channel_type"] = "group"
    await _hear(db_session, "slack", said)
    mirror = await _mirror(db_session, SUPPORT_CHANNEL)

    elsewhere = _slack_event(
        {
            "type": "member_left_channel",
            "user": slack_payloads.SENDER_ID,
            "channel": SUPPORT_CHANNEL,
        }
    )
    elsewhere.payload["team_id"] = "T0OTHER99"
    await _platform_says(db_session, elsewhere)

    assert await _seated(db_session, mirror["channel_id"], fixed_test_user["id"])


def _teams_removed(member: dict[str, str]) -> SurfacePlatformWebhookIngress:
    """Bot Framework's ``conversationUpdate`` for a group chat member removed."""
    return SurfacePlatformWebhookIngress(
        source="teams",
        payload={
            "membersRemoved": [member],
            "type": "conversationUpdate",
            "timestamp": "2026-04-15T07:03:58.1046271Z",
            "id": f"f:{uuid4()}",
            "channelId": "msteams",
            "serviceUrl": "https://smba.trafficmanager.net/amer/",
            "from": {"id": "29:1remover"},
            "conversation": {
                "isGroup": True,
                "conversationType": "groupChat",
                "tenantId": teams_payloads.TENANT_ID,
                "id": GROUP_CHAT_ID,
            },
            "recipient": {
                "id": "28:c3e201f2-a8ef-4026-bef4-d3da0153b191",
                "name": "lemma-bot",
            },
            "channelData": {"tenant": {"id": teams_payloads.TENANT_ID}},
        },
        headers={},
    )


async def test_a_member_removed_from_a_teams_group_chat_leaves_its_mirror(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_teams,
    monkeypatch,
):
    _stage_teams(monkeypatch, fake_teams)
    await _teams_surface(
        authenticated_client,
        db_session,
        pod_id=test_pod["id"],
        user_id=fixed_test_user["id"],
    )
    # The captured sender, by the AAD object id Teams resolves people by.
    fixture = _load_teams_channel_mention_fixture(fake_teams)
    await _seed_external_user(
        db_session,
        platform="TEAMS",
        external_user_id=fixture["from"]["aadObjectId"],
        tenant_id=REAL_TEAMS_TENANT_ID,
        resolved_user_id=UUID(fixed_test_user["id"]),
    )
    await _hear(
        db_session,
        "teams",
        _teams_group_chat(
            service_url=fake_teams.service_url,
            text="morning all",
            activity_id=f"group-chat-{uuid4().hex[:8]}",
            mention=False,
        ),
    )
    mirror = await _mirror(db_session, GROUP_CHAT_ID)
    assert mirror["visibility"] == "PRIVATE"
    assert await _seated(db_session, mirror["channel_id"], fixed_test_user["id"])

    await _platform_says(
        db_session,
        _teams_removed(
            {"id": fixture["from"]["id"], "aadObjectId": fixture["from"]["aadObjectId"]}
        ),
    )

    assert not await _seated(db_session, mirror["channel_id"], fixed_test_user["id"])


# ── two pods, one Teams tenant ─────────────────────────────────────────────


async def test_two_pods_on_one_teams_tenant_keep_their_places_apart(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    scenario,
    fake_teams,
    monkeypatch,
):
    """Two organisations' pods, each with a bot routed to its own channel of
    one Teams tenant. What is said in one is the other pod's nowhere -- no
    place, no account, no channel -- and the tenant's word about one pod's
    place touches only that pod."""
    _stage_teams(monkeypatch, fake_teams)
    pod_a = test_pod["id"]
    await scenario.create_org_with_pod(name_prefix="Second Teams pod")
    pod_b = scenario.pod_id
    other_channel = "19:0therchannel0000000000000000000@thread.tacv2"
    await _teams_surface(
        authenticated_client, db_session, pod_id=pod_a, user_id=fixed_test_user["id"]
    )
    account_b = await _ensure_connector_account(
        db_session,
        user_id=scenario.owner_user["id"],
        connector_id="microsoft_teams",
        credentials={
            "access_token": "teams-token-b",
            "user_data": {"tenant_id": REAL_TEAMS_TENANT_ID},
        },
        organization_id=scenario.org_id,
    )
    await _create_agent_surface(
        scenario.owner_client,
        pod_b,
        config={
            "type": "TEAMS",
            "account_id": str(account_b.id),
            "allowed_channel_ids": [other_channel],
        },
    )
    in_a = _load_teams_channel_mention_fixture(fake_teams)
    await _seed_external_user(
        db_session,
        platform="TEAMS",
        external_user_id=in_a["from"]["aadObjectId"],
        tenant_id=REAL_TEAMS_TENANT_ID,
        resolved_user_id=UUID(fixed_test_user["id"]),
    )
    in_b = _load_teams_channel_mention_fixture(fake_teams)
    in_b["id"] = f"{in_b['id']}9"
    in_b["conversation"]["id"] = f"{other_channel};messageid={in_b['id']}"
    in_b["channelData"]["teamsChannelId"] = other_channel
    in_b["channelData"]["channel"] = {"id": other_channel}
    await _hear(db_session, "teams", in_a)
    await _hear(db_session, "teams", in_b)

    mirror_a = await _mirror(db_session, REAL_TEAMS_CHANNEL_ID)
    mirror_b = await _mirror(db_session, other_channel)
    assert (str(mirror_a["pod_id"]), str(mirror_b["pod_id"])) == (pod_a, pod_b)
    accounts = dict(
        (
            await db_session.execute(
                select(ExternalAccountModel.id, ExternalAccountModel.pod_id).where(
                    ExternalAccountModel.id.in_(
                        [mirror_a["account_id"], mirror_b["account_id"]]
                    )
                )
            )
        ).all()
    )
    assert accounts == {
        mirror_a["account_id"]: mirror_a["pod_id"],
        mirror_b["account_id"]: mirror_b["pod_id"],
    }
    listed_a = await _group_channels(authenticated_client, pod_a, platform="TEAMS")
    assert [c["id"] for c in listed_a] == [str(mirror_a["channel_id"])]
    by_b = await scenario.owner_client.get(
        f"/pods/{pod_b}/channels", params={"kind": "GROUP"}
    )
    assert by_b.status_code == 200, by_b.text
    assert str(mirror_a["channel_id"]) not in {c["id"] for c in by_b.json()["items"]}
    seats_b = await _seats(db_session, mirror_b["channel_id"])

    # The tenant says the captured sender left pod A's channel only.
    removed = _teams_removed(
        {"id": in_a["from"]["id"], "aadObjectId": in_a["from"]["aadObjectId"]}
    )
    removed.payload["conversation"] = {
        "isGroup": True,
        "conversationType": "channel",
        "tenantId": teams_payloads.TENANT_ID,
        "id": REAL_TEAMS_CHANNEL_ID,
    }
    removed.payload["channelData"] = {
        "eventType": "channelMemberRemoved",
        "channel": {"id": REAL_TEAMS_CHANNEL_ID},
        "tenant": {"id": teams_payloads.TENANT_ID},
    }
    await _platform_says(db_session, removed)
    assert not await _seated(db_session, mirror_a["channel_id"], fixed_test_user["id"])
    assert await _seats(db_session, mirror_b["channel_id"]) == seats_b


async def _seats(db_session: AsyncSession, channel_id: UUID) -> set[tuple]:
    rows = await db_session.execute(
        select(
            ChannelMemberModel.user_id,
            ChannelMemberModel.contact_id,
            ChannelMemberModel.agent_id,
        ).where(ChannelMemberModel.channel_id == channel_id)
    )
    return {tuple(row) for row in rows}
