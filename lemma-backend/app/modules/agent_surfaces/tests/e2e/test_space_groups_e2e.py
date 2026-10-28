"""A pod's groups, end to end: every chat its bots are in, as a GROUP channel.

A group is a GROUP channel, read and written in like any channel; the bot's
side of it -- whether outsiders are answered there, by whom, and from what --
is its source's settings (``/pods/{pod}/channels/{c}/sources/{s}``). A member starts a WhatsApp
group from Lemma. It is pending until Meta confirms it, and then carries the
link to hand out. A client in it asks something; the bot answers in the group,
on the channel's timeline. A Telegram group joins through a one-use link, and
the account that used the link is never taken for the member.

Only the model's next move is scripted; Meta and Telegram are the fake servers.
"""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent.infrastructure.tools.messaging.people import CALLED_IN_GROUP
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent_surfaces.composition import build_surface_ingress
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.infrastructure.models import AgentSurfaceExternalUser
from app.modules.agent_surfaces.services.group_updates import apply_group_updates
from app.modules.agent_surfaces.services.telegram_group_join import (
    claim_telegram_group_join,
)
from app.modules.agent_surfaces.tests.e2e.helpers import _create_surface
from app.modules.channels.infrastructure.models import (
    ChannelMemberModel,
    ChannelModel,
    ChannelSourceModel,
)
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
    FakeTelegramServer,
    wait_for_messages,
)
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    process_ingress_and_run_scripted,
    script_text,
    script_tool_call,
)

pytestmark = pytest.mark.e2e

PHONE_NUMBER_ID = "1234567890"
BUSINESS_NUMBER = "15550001111"
GROUP = "HBgLMTY1MDM4Nzk0MzkVAgASGBQzQTRBNjU5OUFFRTAzODEwMTQ0RgA"
CLIENT = "16505551234"
TELEGRAM_GROUP = -1009876543210
LINK_USER_TELEGRAM_ID = 900100


def _wire_whatsapp(monkeypatch, fake_whatsapp) -> None:
    from app.core.config import settings as app_settings

    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.whatsapp.client._WHATSAPP_API_BASE",
        f"{fake_whatsapp.api_base}/v21.0",
    )
    monkeypatch.setattr(surface_settings, "whatsapp_access_token", "wa-token")
    monkeypatch.setattr(surface_settings, "whatsapp_phone_number_id", "1234567890")
    monkeypatch.setattr(surface_settings, "whatsapp_waba_id", "waba-001")
    monkeypatch.setattr(surface_settings, "whatsapp_app_secret", "wa-secret")
    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")


def _wire_native_telegram(monkeypatch, fake_telegram) -> None:
    monkeypatch.setattr(surface_settings, "telegram_bot_token", "native-telegram")
    monkeypatch.setattr(surface_settings, "enable_telegram_polling_mode", True)
    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.telegram.client._TELEGRAM_API_BASE",
        f"{fake_telegram.api_base}/bot",
    )


def _whatsapp(field: str, value: dict) -> SurfacePlatformWebhookIngress:
    return SurfacePlatformWebhookIngress(
        source="whatsapp",
        payload={
            "object": "whatsapp_business_account",
            "entry": [
                {"id": "waba-001", "changes": [{"field": field, "value": value}]}
            ],
        },
        headers={},
    )


def _metadata() -> dict:
    return {"display_phone_number": BUSINESS_NUMBER, "phone_number_id": PHONE_NUMBER_ID}


def _confirmed(request_id: str) -> SurfacePlatformWebhookIngress:
    return _whatsapp(
        "group_lifecycle_update",
        {
            "messaging_product": "whatsapp",
            "metadata": _metadata(),
            "groups": [
                {
                    "timestamp": "1744344496",
                    "group_id": GROUP,
                    "type": "group_create",
                    "request_id": request_id,
                    "subject": "Acme order 1182",
                }
            ],
        },
    )


def _client_says(text: str, *, message_id: str) -> SurfacePlatformWebhookIngress:
    return _whatsapp(
        "messages",
        {
            "messaging_product": "whatsapp",
            "metadata": _metadata(),
            "contacts": [{"profile": {"name": "Tiago"}, "wa_id": CLIENT}],
            "messages": [
                {
                    "from": CLIENT,
                    "group_id": GROUP,
                    "id": message_id,
                    "timestamp": "1744344500",
                    "text": {"body": text},
                    "type": "text",
                }
            ],
        },
    )


async def _start_group(
    client: AsyncClient, pod_id: str, *, surface_name: str, message_store
) -> dict:
    """Start one from Lemma, and let Meta confirm it."""
    response = await client.post(
        f"/pods/{pod_id}/channels/whatsapp-groups",
        json={"surface_name": surface_name, "title": "Acme order 1182"},
    )
    assert response.status_code == 201, response.text
    started = response.json()
    request_id = message_store.get_all("WHATSAPP_GROUP_CREATE")[-1]["request_id"]
    factory = SessionUnitOfWorkFactory(async_session_maker)
    async with factory() as uow:
        await apply_group_updates(
            uow, _confirmed(request_id), adapters=SurfacePlatformAdapterRegistry()
        )
    return started


async def _groups(client: AsyncClient, pod_id: str) -> list[dict]:
    listed = await client.get(f"/pods/{pod_id}/channels", params={"kind": "GROUP"})
    assert listed.status_code == 200, listed.text
    return listed.json()["items"]


async def _settings(client: AsyncClient, pod_id: str, source: dict) -> dict:
    shown = await client.get(
        f"/pods/{pod_id}/channels/{source['channel_id']}/sources/{source['id']}"
    )
    assert shown.status_code == 200, shown.text
    return shown.json()


async def test_a_group_started_from_lemma_is_pending_until_meta_confirms_it(
    authenticated_client: AsyncClient,
    test_pod,
    fixed_test_user,
    fake_whatsapp,
    message_store,
    monkeypatch,
):
    _wire_whatsapp(monkeypatch, fake_whatsapp)
    pod_id = test_pod["id"]
    surface = await _create_surface(
        authenticated_client, pod_id, config={"type": "WHATSAPP"}
    )

    started = await _start_group(
        authenticated_client,
        pod_id,
        surface_name=surface["name"],
        message_store=message_store,
    )

    assert started["pending"] is True
    assert started["invite_link"] is None
    assert started["owner"]["id"] == fixed_test_user["id"]
    assert started["welcomes_outsiders"] is True

    [listed] = await _groups(authenticated_client, pod_id)
    assert listed["id"] == started["channel_id"]
    assert listed["sources"] == [{"platform": "WHATSAPP", "title": "Acme order 1182"}]
    confirmed = await _settings(authenticated_client, pod_id, started)
    assert confirmed["platform"] == "WHATSAPP"
    assert confirmed["title"] == "Acme order 1182"
    assert confirmed["pending"] is False
    assert confirmed["invite_link"] == f"https://chat.whatsapp.com/{GROUP}"

    # The bot's own groups, read in one request, as its settings page shows them.
    by_bot = await authenticated_client.get(
        f"/pods/{pod_id}/channels", params={"source_surface": surface["name"]}
    )
    assert by_bot.status_code == 200, by_bot.text
    [channel] = by_bot.json()["items"]
    mine = channel["source_settings"]
    assert channel["id"] == started["channel_id"]
    assert (mine["id"], mine["channel_id"]) == (started["id"], started["channel_id"])
    assert mine["can_manage"] is True
    # A bot the pod does not have is missing.
    missing = await authenticated_client.get(
        f"/pods/{pod_id}/channels", params={"source_surface": "no-such-bot"}
    )
    assert missing.status_code == 404, missing.text


async def test_a_start_retried_with_its_key_is_the_same_group(
    authenticated_client: AsyncClient,
    test_pod,
    fake_whatsapp,
    message_store,
    monkeypatch,
):
    """WhatsApp is asked once: the retry answers with the group it started."""
    _wire_whatsapp(monkeypatch, fake_whatsapp)
    pod_id = test_pod["id"]
    surface = await _create_surface(
        authenticated_client, pod_id, config={"type": "WHATSAPP"}
    )
    asked_before = len(message_store.get_all("WHATSAPP_GROUP_CREATE"))
    path = f"/pods/{pod_id}/channels/whatsapp-groups"
    key = {"Idempotency-Key": f"start-{uuid4().hex}"}

    first = await authenticated_client.post(
        path,
        json={"surface_name": surface["name"], "title": "Acme order 1182"},
        headers=key,
    )
    again = await authenticated_client.post(
        path,
        json={"surface_name": surface["name"], "title": "Renamed in between"},
        headers=key,
    )

    assert first.status_code == 201, first.text
    assert again.status_code == 201, again.text
    assert again.json()["id"] == first.json()["id"]
    assert again.json()["channel_id"] == first.json()["channel_id"]
    assert len(message_store.get_all("WHATSAPP_GROUP_CREATE")) == asked_before + 1


async def test_a_clients_question_and_the_bots_answer_are_the_groups_lines(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fake_whatsapp,
    message_store,
    monkeypatch,
):
    """The group's channel shows who is in it and what was said.

    A question the bot cannot answer is passed to a person with
    ``ask_a_person``: the member who looks after the asker is told, and
    answering is stepping into the group. The group itself has no case to
    flag -- only a contact's own channel waits on a person.
    """
    _wire_whatsapp(monkeypatch, fake_whatsapp)
    pod_id = test_pod["id"]
    surface = await _create_surface(
        authenticated_client, pod_id, config={"type": "WHATSAPP"}
    )
    group = await _start_group(
        authenticated_client,
        pod_id,
        surface_name=surface["name"],
        message_store=message_store,
    )

    await process_ingress_and_run_scripted(
        db_session,
        _client_says(
            f"@{BUSINESS_NUMBER} can you deliver order 1182 by Friday?",
            message_id="wamid.w1",
        ),
        script=[
            script_tool_call(
                "ask_a_person",
                {"reason": "Tiago asks whether order 1182 can arrive by Friday."},
                tool_call_id="ask-1",
            ),
            script_text("I've asked the team, and will answer here."),
        ],
    )
    await wait_for_messages(message_store, "WHATSAPP", min_count=1)

    channel = f"/pods/{pod_id}/channels/{group['channel_id']}"
    detail = await authenticated_client.get(channel)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["kind"] == "GROUP"
    # A group has no case to flag: only a contact's channel waits on a person.
    assert body["attention"] is None
    db_session.expire_all()
    waiting = await db_session.get(ChannelModel, UUID(group["channel_id"]))
    assert waiting is not None and waiting.current_case_id is None
    people = {(p["person"]["kind"], p["person"]["name"]) for p in body["members"]}
    assert ("CONTACT", "Tiago") in people

    timeline = await authenticated_client.get(f"{channel}/messages")
    assert timeline.status_code == 200, timeline.text
    said = [
        (line["sender"]["kind"], line["text"])
        for line in timeline.json()["items"]
        if not line["members_only"]
    ]
    asked, answered = said[-2:]
    assert asked[0] == "CONTACT" and "order 1182 by Friday" in asked[1]
    assert answered == ("AGENT", "I've asked the team, and will answer here.")

    # The keeper is told in their own channel, pointed at the group, not Contacts.
    mine = await authenticated_client.get(f"/pods/{pod_id}/channels/me")
    assert mine.status_code == 200, mine.text
    notes = await authenticated_client.get(
        f"/pods/{pod_id}/channels/{mine.json()['id']}/messages"
    )
    assert [line["text"] for line in notes.json()["items"]][-1:] == [CALLED_IN_GROUP]


async def _file(
    client: AsyncClient, pod_id: str, name: str, *, folder: str = "/", visibility: str
) -> None:
    response = await client.post(
        f"/pods/{pod_id}/datastore/files",
        data={
            "directory_path": folder,
            "search_enabled": "false",
            "visibility": visibility,
        },
        files={"data": (name, b"# notes", "text/markdown")},
    )
    assert response.status_code == 201, response.text


async def _table(client: AsyncClient, pod_id: str, name: str, visibility: str) -> None:
    response = await client.post(
        f"/pods/{pod_id}/datastore/tables",
        json={
            "name": name,
            "primary_key_column": "id",
            "visibility": visibility,
            "columns": [
                {"name": "id", "type": "UUID", "required": True, "auto": True},
                {"name": "title", "type": "TEXT", "required": True},
            ],
        },
    )
    assert response.status_code == 201, response.text


async def test_the_page_lists_what_people_outside_can_be_answered_from(
    authenticated_client: AsyncClient,
    test_pod,
    fake_whatsapp,
    message_store,
    monkeypatch,
):
    """Public is also the Share sheet's "anyone with a Lemma account", so the
    page says what carries it -- at any depth, and nothing that does not."""
    _wire_whatsapp(monkeypatch, fake_whatsapp)
    client, pod_id = authenticated_client, test_pod["id"]
    surface = await _create_surface(client, pod_id, config={"type": "WHATSAPP"})
    group = await _start_group(
        client, pod_id, surface_name=surface["name"], message_store=message_store
    )
    folder = await client.post(
        f"/pods/{pod_id}/datastore/files/folders", json={"path": "/launch"}
    )
    assert folder.status_code == 201, folder.text
    await _file(client, pod_id, "changelog.md", visibility="PUBLIC")
    await _file(client, pod_id, "brief.md", folder="/launch", visibility="PUBLIC")
    await _file(client, pod_id, "salaries.md", visibility="POD")
    await _table(client, pod_id, "price_list", "PUBLIC")
    await _table(client, pod_id, "customers", "POD")

    public = (await _settings(client, pod_id, group))["public"]
    assert [entry["path"] for entry in public["files"]] == [
        "/changelog.md",
        "/launch/brief.md",
    ]
    assert public["tables"] == ["price_list"]
    assert public["more"] is False


async def test_switching_outsiders_off_leaves_the_group_to_its_members(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fake_whatsapp,
    message_store,
    monkeypatch,
):
    _wire_whatsapp(monkeypatch, fake_whatsapp)
    pod_id = test_pod["id"]
    surface = await _create_surface(
        authenticated_client, pod_id, config={"type": "WHATSAPP"}
    )
    group = await _start_group(
        authenticated_client,
        pod_id,
        surface_name=surface["name"],
        message_store=message_store,
    )

    response = await authenticated_client.patch(
        f"/pods/{pod_id}/channels/{group['channel_id']}/sources/{group['id']}",
        json={"answers_outsiders": False},
    )
    assert response.status_code == 200, response.text
    assert response.json()["welcomes_outsiders"] is False

    uow = SqlAlchemyUnitOfWork(db_session)
    context = await build_surface_ingress(uow).prepare_ingress(
        _client_says(f"@{BUSINESS_NUMBER} are you there?", message_id="wamid.w2")
    )
    await uow.commit()
    assert context is None

    response = await authenticated_client.patch(
        f"/pods/{pod_id}/channels/{group['channel_id']}/sources/{group['id']}",
        json={"answers_outsiders": True},
    )
    assert response.json()["welcomes_outsiders"] is True


async def test_whatsapp_groups_are_started_and_telegram_groups_are_joined(
    authenticated_client: AsyncClient,
    test_pod,
    fake_whatsapp,
    fake_telegram,
    monkeypatch,
):
    """A business number cannot join a group, and a Telegram bot cannot start one."""
    _wire_whatsapp(monkeypatch, fake_whatsapp)
    _wire_native_telegram(monkeypatch, fake_telegram)
    pod_id = test_pod["id"]
    whatsapp = await _create_surface(
        authenticated_client, pod_id, config={"type": "WHATSAPP"}
    )
    telegram = await _create_surface(
        authenticated_client, pod_id, config={"type": "TELEGRAM"}
    )

    started = await authenticated_client.post(
        f"/pods/{pod_id}/channels/whatsapp-groups",
        json={"surface_name": telegram["name"], "title": "Launch crew"},
    )
    linked = await authenticated_client.post(
        f"/pods/{pod_id}/channels/telegram-links",
        json={"surface_name": whatsapp["name"]},
    )

    assert started.status_code == 422, started.text
    assert linked.status_code == 422, linked.text


async def test_a_number_meta_keeps_out_of_groups_is_told_so_not_to_retry(
    authenticated_client: AsyncClient,
    test_pod,
    fake_whatsapp,
    monkeypatch,
):
    """Meta refuses an ineligible number outright; asking again changes nothing."""
    _wire_whatsapp(monkeypatch, fake_whatsapp)
    fake_whatsapp.groups_not_eligible = True
    pod_id = test_pod["id"]
    surface = await _create_surface(
        authenticated_client, pod_id, config={"type": "WHATSAPP"}
    )

    response = await authenticated_client.post(
        f"/pods/{pod_id}/channels/whatsapp-groups",
        json={"surface_name": surface["name"], "title": "Acme order 1182"},
    )

    assert response.status_code == 422, response.text
    assert "doesn't let this number start groups" in response.text
    assert await _groups(authenticated_client, pod_id) == []


async def _telegram_join_link(
    client: AsyncClient,
    pod_id: str,
    monkeypatch: pytest.MonkeyPatch,
    fake: FakeTelegramServer,
) -> tuple[str, Callable[[int], SurfacePlatformWebhookIngress]]:
    """A Telegram surface's name, and the one-use link that brings its bot
    into a group: the ``/start`` each use of it arrives as."""
    _wire_native_telegram(monkeypatch, fake)
    surface = await _create_surface(client, pod_id, config={"type": "TELEGRAM"})

    response = await client.post(
        f"/pods/{pod_id}/channels/telegram-links",
        json={"surface_name": surface["name"]},
    )
    assert response.status_code == 200, response.text
    url = response.json()["url"]
    # The bot's name was asked of Telegram: nobody had asked before.
    assert url.startswith("https://t.me/lemmabot?startgroup=")
    code = url.split("startgroup=", 1)[1]

    def _start(update_id: int) -> SurfacePlatformWebhookIngress:
        return SurfacePlatformWebhookIngress(
            source="telegram",
            payload={
                "update_id": update_id,
                "message": {
                    "message_id": update_id,
                    "from": {
                        "id": LINK_USER_TELEGRAM_ID,
                        "is_bot": False,
                        "first_name": "Arjun",
                    },
                    "chat": {
                        "id": TELEGRAM_GROUP,
                        "type": "supergroup",
                        "title": "Launch crew",
                    },
                    "date": 1700000100,
                    "text": f"/start@lemmabot {code}",
                    "entities": [{"type": "bot_command", "offset": 0, "length": 15}],
                },
            },
            headers={},
        )

    return surface["name"], _start


async def test_a_telegram_group_joins_through_a_one_use_link(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
    monkeypatch,
):
    pod_id = test_pod["id"]
    _bot, _start = await _telegram_join_link(
        authenticated_client, pod_id, monkeypatch, fake_telegram
    )
    adapters = SurfacePlatformAdapterRegistry()
    claimed = await claim_telegram_group_join(
        SessionUnitOfWorkFactory(async_session_maker), _start(8001), adapters=adapters
    )
    assert claimed is True

    place = await db_session.scalar(
        select(ChannelSourceModel).where(
            ChannelSourceModel.external_channel_id == str(TELEGRAM_GROUP)
        )
    )
    assert place is not None and place.title == "Launch crew"
    settings = await _settings(
        authenticated_client,
        pod_id,
        {"id": str(place.id), "channel_id": str(place.channel_id)},
    )
    assert settings["owner"]["id"] == fixed_test_user["id"]
    assert settings["welcomes_outsiders"] is True

    # The bot says it is there, in the group.
    sent = await wait_for_messages(message_store, "TELEGRAM", min_count=1)
    assert sent[-1]["chat_id"] == str(TELEGRAM_GROUP)
    assert "Mention me or reply to me" in sent[-1]["text"]

    # Whoever used the link is still nobody in particular.
    linked = (
        await db_session.execute(
            select(AgentSurfaceExternalUser).where(
                AgentSurfaceExternalUser.platform == "TELEGRAM",
                AgentSurfaceExternalUser.external_user_id == str(LINK_USER_TELEGRAM_ID),
                AgentSurfaceExternalUser.resolved_user_id.is_not(None),
            )
        )
    ).scalars()
    assert list(linked) == []

    # Spent: a second use is swallowed, and the bot says nothing more.
    again = await claim_telegram_group_join(
        SessionUnitOfWorkFactory(async_session_maker), _start(8002), adapters=adapters
    )
    assert again is True
    assert len(message_store.get_all("TELEGRAM")) == len(sent)


async def test_the_member_who_brought_the_bot_in_reads_the_groups_channel(
    authenticated_client: AsyncClient,
    test_pod,
    fake_telegram,
    monkeypatch,
):
    """A Telegram group is private: its channel is read by its people, and the
    member who used the link is one of them from the start."""
    pod_id = test_pod["id"]
    _bot, _start = await _telegram_join_link(
        authenticated_client, pod_id, monkeypatch, fake_telegram
    )

    assert await claim_telegram_group_join(
        SessionUnitOfWorkFactory(async_session_maker),
        _start(8101),
        adapters=SurfacePlatformAdapterRegistry(),
    )

    [group] = await _groups(authenticated_client, pod_id)
    assert group["sources"] == [{"platform": "TELEGRAM", "title": "Launch crew"}]


async def test_a_pod_admin_finds_a_private_group_and_joins_it_on_request(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    monkeypatch,
):
    """An admin nobody let into a bot's private group sees it on the bot's
    settings -- not joined, with nothing of what was said -- and may join it
    by asking, to take it over."""
    pod_id = test_pod["id"]
    bot, _start = await _telegram_join_link(
        authenticated_client, pod_id, monkeypatch, fake_telegram
    )
    assert await claim_telegram_group_join(
        SessionUnitOfWorkFactory(async_session_maker),
        _start(8201),
        adapters=SurfacePlatformAdapterRegistry(),
    )
    channel_id = await db_session.scalar(
        select(ChannelSourceModel.channel_id).where(
            ChannelSourceModel.external_channel_id == str(TELEGRAM_GROUP)
        )
    )
    # The admin is somebody the group never had in it.
    await db_session.execute(
        delete(ChannelMemberModel).where(
            ChannelMemberModel.channel_id == channel_id,
            ChannelMemberModel.user_id == UUID(fixed_test_user["id"]),
        )
    )
    await db_session.commit()
    assert await _groups(authenticated_client, pod_id) == []

    by_bot = await authenticated_client.get(
        f"/pods/{pod_id}/channels", params={"source_surface": bot}
    )
    assert by_bot.status_code == 200, by_bot.text
    [listed] = by_bot.json()["items"]
    assert listed["id"] == str(channel_id)
    assert listed["visibility"] == "PRIVATE"
    assert listed["joined"] is False
    assert listed["last_line"] is None
    assert listed["source_settings"]["can_manage"] is True
    unread = await authenticated_client.get(
        f"/pods/{pod_id}/channels/{channel_id}/messages"
    )
    assert unread.status_code == 404, unread.text

    joined = await authenticated_client.post(
        f"/pods/{pod_id}/channels/{channel_id}/members/me"
    )
    assert joined.status_code == 200, joined.text
    assert joined.json()["joined"] is True
    [group] = await _groups(authenticated_client, pod_id)
    assert group["id"] == str(channel_id) and group["joined"] is True
