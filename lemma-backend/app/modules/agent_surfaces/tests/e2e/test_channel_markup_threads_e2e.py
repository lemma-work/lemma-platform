"""E2E for how a Slack or Teams channel reads in Lemma, and how Lemma writes back.

* **Markup becomes parts.** ``<@U…>`` is a mention of the member the surface
  resolved, written ``@Name``; ``<url|label>`` a link. A member named who may
  see the channel has an unread mention; one who may not keeps their name on
  the line and gets nothing.
* **Threads go home.** A member's reply in Lemma to a line heard in a Slack
  thread is posted in that thread (``thread_ts``), and in Teams as a reply to
  its root; a line written at the top of the channel goes to the top. A Slack
  reply to a line written in Lemma lands under it.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import unquote
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _load_teams_channel_mention_fixture,
    _seed_external_user,
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
    script_text,
)
from app.modules.agent_surfaces.tests.e2e.surface_work_support import (
    deliver_replies,
    deliveries_of_line,
    member_writes,
    send_deliveries,
)
from app.modules.agent_surfaces.tests.e2e.test_contact_care_e2e import _member
from app.modules.agent_surfaces.tests.e2e.test_workspace_channels_e2e import (
    SUPPORT_CHANNEL,
    _hear,
    _slack_in_channel,
    _slack_surface,
    _stage_teams,
    _teams_surface,
    _the_group_channel,
    _thread,
    _timeline,
    _ts,
)
from app.modules.test_support.e2e_authz import auth_headers

pytestmark = [pytest.mark.e2e, pytest.mark.drives_work]

PRIYA = "U0PRIYA001"
NOBODY = "U0NOBODY01"


async def _slack_with_priya(
    client: AsyncClient,
    async_client: AsyncClient,
    db_session: AsyncSession,
    *,
    pod: dict,
    org: dict,
    owner: dict,
    fake_slack: Any,
) -> tuple[dict, dict]:
    """A routed Slack channel; the owner and Priya are both resolved members."""
    agent, _ = await _slack_surface(
        client, db_session, pod_id=pod["id"], user_id=owner["id"], fake_slack=fake_slack
    )
    priya = await _member(
        client,
        async_client,
        org_id=org["id"],
        pod_id=pod["id"],
        role="POD_EDITOR",
        prefix="priya",
    )
    for external, user in ((slack_payloads.SENDER_ID, owner), (PRIYA, priya)):
        await _seed_external_user(
            db_session,
            platform="SLACK",
            external_user_id=external,
            tenant_id=slack_payloads.TEAM_ID,
            resolved_user_id=UUID(user["id"]),
        )
    return agent, priya


async def _inbox_mentions(client: AsyncClient, pod_id: str, user: dict) -> list[str]:
    inbox = await client.get(f"/pods/{pod_id}/inbox", headers=auth_headers(user))
    assert inbox.status_code == 200, inbox.text
    return [item["message"]["id"] for item in inbox.json()["mentions"]]


async def test_slack_markup_is_text_and_parts_and_mentions_who_can_see(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_org,
    fixed_test_user,
    fake_slack,
):
    pod_id = test_pod["id"]
    agent, priya = await _slack_with_priya(
        authenticated_client,
        async_client,
        db_session,
        pod=test_pod,
        org=fixed_test_org,
        owner=fixed_test_user,
        fake_slack=fake_slack,
    )
    said = (
        f"<@{PRIYA}> can you check <https://docs.example/plan|the plan> "
        f"&amp; ping <@{NOBODY}> in <#C0OTHER01|launch>? "
        f"<@{slack_payloads.BOT_USER_ID}> keep notes"
    )
    payload = _slack_in_channel(said, ts=_ts())
    payload["authorizations"] = [
        {"user_id": slack_payloads.BOT_USER_ID, "is_bot": True}
    ]
    await _hear(db_session, "slack", payload)

    channel = await _the_group_channel(authenticated_client, pod_id, platform="SLACK")
    [line] = await _timeline(authenticated_client, pod_id, channel["id"])
    assert line["text"] == (
        "@Surface Test User can you check the plan & ping @U0NOBODY01 in "
        f"#launch? @{agent['name']} keep notes"
    )
    parts = {(part["type"], part.get("kind")): part for part in line["parts"]}
    assert parts[("mention", "MEMBER")]["id"] == priya["id"]
    assert parts[("mention", "AGENT")]["id"] == agent["id"]
    assert parts[("link", None)] == {
        "type": "link",
        "url": "https://docs.example/plan",
        "label": "the plan",
    }
    assert len(line["parts"]) == 3

    # Priya can see a public channel's mirror: she is told she was named.
    assert line["id"] in await _inbox_mentions(async_client, pod_id, priya)


async def test_a_member_who_cannot_see_the_channel_is_named_but_not_told(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_org,
    fixed_test_user,
    fake_slack,
):
    pod_id = test_pod["id"]
    _agent, priya = await _slack_with_priya(
        authenticated_client,
        async_client,
        db_session,
        pod=test_pod,
        org=fixed_test_org,
        owner=fixed_test_user,
        fake_slack=fake_slack,
    )
    # A private Slack channel: its mirror is its people's only.
    payload = _slack_in_channel(f"<@{PRIYA}> not for everyone", ts=_ts())
    payload["event"]["channel_type"] = "group"
    await _hear(db_session, "slack", payload)

    channel = await _the_group_channel(authenticated_client, pod_id, platform="SLACK")
    assert channel["visibility"] == "PRIVATE"
    [line] = await _timeline(authenticated_client, pod_id, channel["id"])
    assert line["text"] == "@Surface Test User not for everyone"
    assert [part["id"] for part in line["parts"]] == [priya["id"]]
    assert line["id"] not in await _inbox_mentions(async_client, pod_id, priya)


async def test_an_outsider_named_in_a_shared_channel_is_their_contact(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_org,
    fixed_test_user,
    fake_slack,
):
    """A Slack mention names a user, not their workspace: somebody from the
    other company the pod knows as a contact (``{team}:{user}``) is that
    contact, named on the line."""
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.contacts.contracts import (
        IdentityKind,
        IdentityStrength,
        open_contact,
    )

    pod_id = test_pod["id"]
    await _slack_with_priya(
        authenticated_client,
        async_client,
        db_session,
        pod=test_pod,
        org=fixed_test_org,
        owner=fixed_test_user,
        fake_slack=fake_slack,
    )
    outsider = await open_contact(
        SqlAlchemyUnitOfWork(db_session),
        pod_id=UUID(pod_id),
        kind=IdentityKind.SLACK,
        value="T0VENDOR01:U0VENDOR01",
        strength=IdentityStrength.CHANNEL,
        display_name="Vera Vendor",
    )
    await db_session.commit()

    payload = _slack_in_channel("<@U0VENDOR01> can you confirm?", ts=_ts())
    await _hear(db_session, "slack", payload)

    channel = await _the_group_channel(authenticated_client, pod_id, platform="SLACK")
    [line] = await _timeline(authenticated_client, pod_id, channel["id"])
    assert line["text"] == "@Vera Vendor can you confirm?"
    [part] = line["parts"]
    assert (part["kind"], part["id"]) == ("CONTACT", str(outsider.id))


async def test_a_reply_written_in_lemma_goes_to_its_slack_thread(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    message_store,
):
    pod_id = test_pod["id"]
    await _slack_surface(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user_id=fixed_test_user["id"],
        fake_slack=fake_slack,
    )
    # A mention answered: the bot has a conversation in the channel.
    asked_ts = _ts()
    answer = f"Noted [{uuid4().hex[:6]}]"
    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="slack",
            payload=_slack_in_channel("track the deploy", ts=asked_ts, mention=True),
            headers={},
        ),
        script=[script_text(answer)],
    )
    assert isinstance(context, SurfaceChatContext)
    await wait_for_slack_text(message_store, answer)
    root_ts = _ts()
    await _hear(
        db_session, "slack", _slack_in_channel("the deploy moved to five", ts=root_ts)
    )
    channel = await _the_group_channel(authenticated_client, pod_id, platform="SLACK")
    root = next(
        line
        for line in await _timeline(authenticated_client, pod_id, channel["id"])
        if line["text"] == "the deploy moved to five"
    )

    in_thread = f"Thanks, updating the runbook [{uuid4().hex[:6]}]"
    written = await member_writes(
        authenticated_client,
        pod_id,
        channel["id"],
        in_thread,
        thread_root_id=root["id"],
    )
    on_top = f"Release notes are out [{uuid4().hex[:6]}]"
    top = await member_writes(authenticated_client, pod_id, channel["id"], on_top)
    await send_deliveries()

    [threaded] = await deliveries_of_line(written["id"])
    assert threaded.status.value == "SENT", threaded.reason
    calls = [
        call
        for call in message_store.get_all("SLACK")
        if call.get("_path") == "/api/chat.postMessage"
    ]
    [reply] = [call for call in calls if in_thread in str(call.get("text"))]
    assert reply["channel"] == SUPPORT_CHANNEL
    assert reply["thread_ts"] == root_ts
    [posted] = [call for call in calls if on_top in str(call.get("text"))]
    assert posted["channel"] == SUPPORT_CHANNEL
    assert not posted.get("thread_ts")

    # A Slack reply to the line written in Lemma is a reply under it here.
    [top_row] = await deliveries_of_line(top["id"])
    assert top_row.status.value == "SENT", top_row.reason
    assert top_row.external_message_id
    await _hear(
        db_session,
        "slack",
        _slack_in_channel(
            "great, thanks", ts=_ts(), thread_ts=top_row.external_message_id
        ),
    )
    replies = await _thread(authenticated_client, pod_id, channel["id"], top["id"])
    assert [line["text"] for line in replies] == ["great, thanks"]


async def test_a_reply_written_in_lemma_goes_to_its_teams_thread(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_teams,
    message_store,
    monkeypatch,
):
    _stage_teams(monkeypatch, fake_teams)
    pod_id = test_pod["id"]
    await _teams_surface(
        authenticated_client, db_session, pod_id=pod_id, user_id=fixed_test_user["id"]
    )
    payload = _load_teams_channel_mention_fixture(fake_teams)
    answer = "E2E agent reply [TEAMS thread root]"
    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(source="teams", payload=payload, headers={}),
        script=[script_text(answer)],
    )
    assert isinstance(context, SurfaceChatContext)
    # The answer is a line, carried to Teams like any other.
    await deliver_replies()
    await send_deliveries()
    await wait_for_messages(
        message_store,
        "TEAMS",
        predicate=lambda item: answer in str((item.get("body") or {}).get("text")),
    )
    channel = await _the_group_channel(authenticated_client, pod_id, platform="TEAMS")
    root = next(
        line
        for line in await _timeline(authenticated_client, pod_id, channel["id"])
        if "Do you know shera?" in (line["text"] or "")
    )
    # The <at> tag is the bot, named as the agent it answers for.
    assert root["text"].startswith("@")
    assert any(part.get("kind") == "AGENT" for part in root["parts"])

    in_thread = f"Following up here [{uuid4().hex[:6]}]"
    written = await member_writes(
        authenticated_client,
        pod_id,
        channel["id"],
        in_thread,
        thread_root_id=root["id"],
    )
    await send_deliveries()
    [row] = await deliveries_of_line(written["id"])
    assert row.status.value == "SENT", row.reason
    [sent] = [
        item
        for item in message_store.get_all("TEAMS")
        if in_thread in str((item.get("body") or {}).get("text"))
    ]
    root_id = payload["id"]
    assert (
        f";messageid={root_id}" in unquote(sent["path"])
        or (sent.get("body") or {}).get("replyToId") == root_id
    ), sent
