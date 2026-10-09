"""A WhatsApp group the bot opened for a member -- end to end.

On WhatsApp the bot cannot be added to a group; it creates one. A member asks,
Meta answers with a request id only, and the group's id and invite link arrive
by webhook moments later. Then a client in that group asks the bot something.
They are answered in the group, for the pod, from what the pod marked Public --
by a run in the member's conversation for the group's outsiders.

Only the model's next move is scripted; Meta is the fake Graph server, at the
Groups API's own version.
"""

from __future__ import annotations

import json
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent_surfaces.composition import build_surface_ingress
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.groups import SurfaceGroup
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.infrastructure.repositories.group_repository import (
    SurfaceGroupRepository,
)
from app.modules.agent_surfaces.services.group_updates import apply_group_updates
from app.modules.agent_surfaces.services.whatsapp_groups import WhatsAppGroupOpener
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _create_surface,
    _messages_for_conversation,
    _set_user_mobile_number,
)
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
MEMBER = "15550555555"
CLIENT = "16505551234"


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


def _envelope(field: str, value: dict) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "waba-001", "changes": [{"field": field, "value": value}]}],
    }


def _metadata() -> dict:
    return {"display_phone_number": BUSINESS_NUMBER, "phone_number_id": PHONE_NUMBER_ID}


def _created(request_id: str) -> dict:
    """Meta's confirmation, without the link its spec does not promise."""
    return _envelope(
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


def _said_in_group(text: str, *, message_id: str, sender: str = CLIENT) -> dict:
    return _envelope(
        "messages",
        {
            "messaging_product": "whatsapp",
            "metadata": _metadata(),
            "contacts": [{"profile": {"name": "Tiago"}, "wa_id": sender}],
            "messages": [
                {
                    "from": sender,
                    "group_id": GROUP,
                    "id": message_id,
                    "timestamp": "1744344500",
                    "text": {"body": text},
                    "type": "text",
                }
            ],
        },
    )


def _ingress(payload: dict) -> SurfacePlatformWebhookIngress:
    return SurfacePlatformWebhookIngress(source="whatsapp", payload=payload, headers={})


async def _open_group(
    *, surface_id: UUID, owner: UUID, message_store
) -> tuple[WhatsAppGroupOpener, SurfaceGroup]:
    factory = SessionUnitOfWorkFactory(async_session_maker)
    opener = WhatsAppGroupOpener(factory, wait_seconds=0)
    opened = await opener.open(
        surface_id=surface_id, owner_user_id=owner, title="Acme order 1182"
    )
    assert opened.pending is True
    request_id = message_store.get_all("WHATSAPP_GROUP_CREATE")[-1]["request_id"]
    async with factory() as uow:
        await apply_group_updates(
            uow,
            _ingress(_created(request_id)),
            adapters=SurfacePlatformAdapterRegistry(),
        )
    async with factory() as uow:
        group = await SurfaceGroupRepository(uow.session).get(
            surface_id=surface_id, external_channel_id=GROUP
        )
    assert group is not None
    return opener, group


async def _table(client: AsyncClient, pod_id: str, *, name: str, visibility: str):
    response = await client.post(
        f"/pods/{pod_id}/datastore/tables",
        json={
            "name": name,
            "primary_key_column": "id",
            "enable_rls": False,
            "visibility": visibility,
            "columns": [
                {"name": "id", "type": "UUID", "required": True, "auto": True},
                {"name": "title", "type": "TEXT", "required": True},
            ],
        },
    )
    assert response.status_code == 201, response.text


async def test_a_member_opens_a_group_and_its_confirmation_brings_the_link(
    authenticated_client: AsyncClient,
    test_pod,
    fixed_test_user,
    fake_whatsapp,
    message_store,
    monkeypatch,
):
    _wire_whatsapp(monkeypatch, fake_whatsapp)
    owner = UUID(fixed_test_user["id"])
    surface = await _create_surface(
        authenticated_client, test_pod["id"], config={"type": "WHATSAPP"}
    )

    opener, group = await _open_group(
        surface_id=UUID(surface["id"]), owner=owner, message_store=message_store
    )

    created = message_store.get_all("WHATSAPP_GROUP_CREATE")[-1]
    assert created["subject"] == "Acme order 1182"
    assert created["_path"] == f"/v23.0/{PHONE_NUMBER_ID}/groups"
    assert group.owner_user_id == owner
    assert group.welcomes_outsiders
    # The confirmation carried no link, so it was asked for.
    assert group.invite_link == f"https://chat.whatsapp.com/{GROUP}"

    # Asking again answers from the group rather than making a second one.
    again = await opener.open(
        surface_id=UUID(surface["id"]), owner_user_id=owner, title="Acme order 1182"
    )
    assert again.already_open is True
    assert again.invite_link == group.invite_link
    assert len(message_store.get_all("WHATSAPP_GROUP_CREATE")) == 1

    # Listed for the pod, link and all.
    response = await authenticated_client.get(f"/pods/{test_pod['id']}/groups")
    assert response.status_code == 200, response.text
    [listed] = response.json()["items"]
    assert listed["invite_link"] == group.invite_link
    assert listed["pending"] is False


async def test_a_client_in_the_group_is_answered_there_from_what_is_public(
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
    owner = UUID(fixed_test_user["id"])
    surface = await _create_surface(
        authenticated_client, pod_id, config={"type": "WHATSAPP"}
    )
    _, group = await _open_group(
        surface_id=UUID(surface["id"]), owner=owner, message_store=message_store
    )
    await _table(authenticated_client, pod_id, name="customers", visibility="POD")
    await _table(authenticated_client, pod_id, name="price_list", visibility="PUBLIC")

    context = await process_ingress_and_run_scripted(
        db_session,
        _ingress(
            _said_in_group(
                f"@{BUSINESS_NUMBER} what can you tell me?", message_id="wamid.g1"
            )
        ),
        script=[
            script_tool_call("pod_tables", {}, tool_call_id="tables-1"),
            script_text("I can share the price list."),
        ],
    )

    # Answered as the pod: the member's conversation for this group's outsiders.
    assert isinstance(context, SurfaceChatContext)
    assert context.audience.answers_outsiders is True
    assert context.user_id == owner

    # In the group, through the Groups API, and never to the client's own number.
    sent = await wait_for_messages(message_store, "WHATSAPP", min_count=1)
    texts = [message for message in sent if message.get("type") == "text"]
    assert texts, sent
    assert all(message.get("to") != CLIENT for message in sent)
    answer = texts[-1]
    assert (answer["recipient_type"], answer["to"]) == ("group", GROUP)
    assert answer["_path"] == f"/v23.0/{PHONE_NUMBER_ID}/messages"
    assert "price list" in answer["text"]["body"]
    # No read receipt or typing bubble: Meta documents neither for groups.
    assert not [message for message in sent if message.get("status") == "read"]

    # The run saw what the pod marked Public, and nothing it did not.
    messages = await _messages_for_conversation(
        authenticated_client,
        pod_id=pod_id,
        conversation_id=str(context.conversation_id),
    )
    listed = [
        json.dumps(message["tool_result"])
        for message in messages
        if message.get("tool_name") == "pod_tables" and message.get("tool_result")
    ]
    assert listed, "the scripted pod_tables call left no result"
    assert "price_list" in listed[-1]
    assert "customers" not in listed[-1]

    # And the pod's log of the group holds both sides.
    lines = await SurfaceGroupRepository(db_session).recent_lines(group=group, limit=10)
    said = [line.text for line in lines]
    assert f"@{BUSINESS_NUMBER} what can you tell me?" in said
    assert "I can share the price list." in said


@pytest.mark.parametrize("called", ["the pod's name", "Lem"])
async def test_a_member_is_answered_as_themselves_when_they_name_the_bot(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_whatsapp,
    message_store,
    monkeypatch,
    called,
):
    """By the name the app shows the pod's bot under, or the one chats knew."""
    name = test_pod["name"] if called == "the pod's name" else called
    _wire_whatsapp(monkeypatch, fake_whatsapp)
    owner = UUID(fixed_test_user["id"])
    surface = await _create_surface(
        authenticated_client, test_pod["id"], config={"type": "WHATSAPP"}
    )
    await _set_user_mobile_number(
        db_session, user_id=fixed_test_user["id"], mobile_number=MEMBER
    )
    await _open_group(
        surface_id=UUID(surface["id"]), owner=owner, message_store=message_store
    )

    context = await process_ingress_and_run_scripted(
        db_session,
        _ingress(
            _said_in_group(
                f"{name}, send Tiago our lead times",
                message_id="wamid.g2",
                sender=MEMBER,
            )
        ),
        script=[script_text("Lead times are two weeks.")],
    )

    assert isinstance(context, SurfaceChatContext)
    assert context.audience.answers_outsiders is False
    assert context.user_id == owner
    sent = await wait_for_messages(message_store, "WHATSAPP", min_count=1)
    answer = [message for message in sent if message.get("type") == "text"][-1]
    assert (answer["recipient_type"], answer["to"]) == ("group", GROUP)


async def test_talk_nobody_put_to_the_bot_is_logged_and_left_alone(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_whatsapp,
    message_store,
    monkeypatch,
):
    _wire_whatsapp(monkeypatch, fake_whatsapp)
    surface = await _create_surface(
        authenticated_client, test_pod["id"], config={"type": "WHATSAPP"}
    )
    _, group = await _open_group(
        surface_id=UUID(surface["id"]),
        owner=UUID(fixed_test_user["id"]),
        message_store=message_store,
    )

    uow = SqlAlchemyUnitOfWork(db_session)
    context = await build_surface_ingress(uow).prepare_ingress(
        _ingress(_said_in_group("Thanks all, talk at 3", message_id="wamid.g3"))
    )
    await uow.commit()

    assert context is None
    lines = await SurfaceGroupRepository(db_session).recent_lines(group=group, limit=5)
    assert [line.text for line in lines] == ["Thanks all, talk at 3"]


async def test_a_group_no_pod_here_opened_is_ignored(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fake_whatsapp,
    monkeypatch,
):
    _wire_whatsapp(monkeypatch, fake_whatsapp)
    await _create_surface(
        authenticated_client, test_pod["id"], config={"type": "WHATSAPP"}
    )

    uow = SqlAlchemyUnitOfWork(db_session)
    context = await build_surface_ingress(uow).prepare_ingress(
        _ingress(_said_in_group(f"@{BUSINESS_NUMBER} hello", message_id="wamid.g4"))
    )

    assert context is None


@pytest.mark.parametrize(("kind", "offered"), [("DM", True), ("CHANNEL", False)])
async def test_only_a_members_own_chat_can_open_a_group(
    authenticated_client: AsyncClient,
    test_pod,
    fixed_test_user,
    fake_whatsapp,
    monkeypatch,
    kind,
    offered,
):
    """In a group, whoever is talking could open groups in a member's name."""
    from app.modules.agent.contracts import Conversation
    from app.modules.agent_surfaces.contracts.egress import build_surface_toolsets

    _wire_whatsapp(monkeypatch, fake_whatsapp)
    surface = await _create_surface(
        authenticated_client, test_pod["id"], config={"type": "WHATSAPP"}
    )
    conversation = Conversation(
        user_id=UUID(fixed_test_user["id"]),
        pod_id=UUID(test_pod["id"]),
        metadata={
            "surface_platform": "WHATSAPP",
            "surface_id": surface["id"],
            "conversation_kind": kind,
        },
    )

    toolsets = await build_surface_toolsets(
        SessionUnitOfWorkFactory(async_session_maker), conversation
    )

    names = {name for toolset in toolsets for name in getattr(toolset, "tools", {})}
    assert ("whatsapp_open_group" in names) is offered
