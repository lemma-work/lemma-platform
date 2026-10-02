"""A pod's Groups page, end to end: every chat its bots are in, as the pod sees it.

A member starts a WhatsApp group from the page. It is pending until Meta
confirms it, and then carries the link to hand out. A client in it asks what
the pod cannot answer from what it made Public; the question waits on the
member's page, and the bot's line in the group says whom it answered and on
what. A Telegram group joins through a one-use link, and the account that used
the link is never taken for the member.

Only the model's next move is scripted; Meta and Telegram are the fake servers.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
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
    """Start one from the page, and let Meta confirm it."""
    response = await client.post(
        f"/pods/{pod_id}/groups",
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


async def test_a_group_started_from_the_page_is_pending_until_meta_confirms_it(
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
    assert started["owner"]["user_id"] == fixed_test_user["id"]
    assert started["welcomes_outsiders"] is True

    response = await authenticated_client.get(f"/pods/{pod_id}/groups")
    assert response.status_code == 200, response.text
    [listed] = response.json()["items"]
    assert listed["id"] == started["id"]
    assert listed["platform"] == "WHATSAPP"
    assert listed["title"] == "Acme order 1182"
    assert listed["pending"] is False
    assert listed["invite_link"] == f"https://chat.whatsapp.com/{GROUP}"


async def test_what_a_client_is_waiting_on_shows_on_the_page_of_whoever_answers(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_whatsapp,
    message_store,
    monkeypatch,
):
    _wire_whatsapp(monkeypatch, fake_whatsapp)
    pod_id = test_pod["id"]
    owner = fixed_test_user["id"]
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
                "message_user",
                {
                    "to": owner,
                    "message": "Tiago asks whether order 1182 can arrive by Friday.",
                },
                tool_call_id="ask-1",
            ),
            script_text("I've asked the team, and will answer here."),
        ],
    )
    await wait_for_messages(message_store, "WHATSAPP", min_count=1)

    detail = await authenticated_client.get(f"/pods/{pod_id}/groups/{group['id']}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    [waiting] = body["waiting"]
    assert waiting["question"] == "Tiago asks whether order 1182 can arrive by Friday."
    assert body["waiting_for_you"] == 1
    assert (body["people_in_pod"], body["people_outside"]) == (0, 1)
    [person] = body["people"]
    assert (person["name"], person["in_pod"]) == ("Tiago", False)

    timeline = await authenticated_client.get(
        f"/pods/{pod_id}/groups/{group['id']}/timeline"
    )
    assert timeline.status_code == 200, timeline.text
    asked, answered = timeline.json()["items"][-2:]
    assert (asked["author_name"], asked["from_bot"], asked["in_pod"]) == (
        "Tiago",
        False,
        False,
    )
    assert answered["from_bot"] is True
    assert answered["text"] == "I've asked the team, and will answer here."
    # Whom it answered, and that it answered from what the pod made Public.
    assert (answered["answered_name"], answered["answered_from_public"]) == (
        "Tiago",
        True,
    )


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

    detail = await client.get(f"/pods/{pod_id}/groups/{group['id']}")

    assert detail.status_code == 200, detail.text
    public = detail.json()["public"]
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
        f"/pods/{pod_id}/groups/{group['id']}", json={"answers_outsiders": False}
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
        f"/pods/{pod_id}/groups/{group['id']}", json={"answers_outsiders": True}
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
        f"/pods/{pod_id}/groups",
        json={"surface_name": telegram["name"], "title": "Launch crew"},
    )
    linked = await authenticated_client.post(
        f"/pods/{pod_id}/groups/links", json={"surface_name": whatsapp["name"]}
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
        f"/pods/{pod_id}/groups",
        json={"surface_name": surface["name"], "title": "Acme order 1182"},
    )

    assert response.status_code == 422, response.text
    assert "doesn't let this number start groups" in response.text
    listed = await authenticated_client.get(f"/pods/{pod_id}/groups")
    assert listed.json()["items"] == []


async def test_a_telegram_group_joins_through_a_one_use_link(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
    monkeypatch,
):
    _wire_native_telegram(monkeypatch, fake_telegram)
    pod_id = test_pod["id"]
    surface = await _create_surface(
        authenticated_client, pod_id, config={"type": "TELEGRAM"}
    )

    response = await authenticated_client.post(
        f"/pods/{pod_id}/groups/links", json={"surface_name": surface["name"]}
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

    adapters = SurfacePlatformAdapterRegistry()
    claimed = await claim_telegram_group_join(
        SessionUnitOfWorkFactory(async_session_maker), _start(8001), adapters=adapters
    )
    assert claimed is True

    listed = (await authenticated_client.get(f"/pods/{pod_id}/groups")).json()["items"]
    [group] = [item for item in listed if item["platform"] == "TELEGRAM"]
    assert group["title"] == "Launch crew"
    assert group["owner"]["user_id"] == fixed_test_user["id"]
    assert group["welcomes_outsiders"] is True

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
