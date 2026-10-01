"""Who sees and changes a pod's groups, with two members in the pod -- end to end.

Every member may open a group's page; what the bot told one member with that
member's own access stays that member's. A group's people outside the pod are
answered for by one member: only they, or an admin of the pod, may switch that
off or take it over, and an admin who does tells them. A member who has left
the pod answers for nobody. And the bot's own switch closes every group at once.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
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
from app.modules.agent_surfaces.infrastructure.repositories.group_repository import (
    SurfaceGroupRepository,
)
from app.modules.agent_surfaces.services.group_updates import apply_group_updates
from app.modules.agent_surfaces.tests.e2e.helpers import _create_surface
from app.modules.test_support.e2e_authz import auth_headers

pytestmark = pytest.mark.e2e

PHONE_NUMBER_ID = "1234567890"
BUSINESS_NUMBER = "15550001111"
GROUP = "HBgLMTY1MDM4Nzk0MzkVAgASGBQzQTRBNjU5OUFFRTAzODEwMTQ0RgA"
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


async def _pod_with_a_group(scenario, message_store) -> tuple[dict, dict, dict]:
    """A pod with a WhatsApp bot, a group its owner started, and an editor."""
    await scenario.create_org_with_pod(name_prefix="Group rights")
    surface = await _create_surface(
        scenario.owner_client, scenario.pod_id, config={"type": "WHATSAPP"}
    )
    started = await scenario.owner_client.post(
        f"/pods/{scenario.pod_id}/groups",
        json={"surface_name": surface["name"], "title": "Acme order 1182"},
    )
    assert started.status_code == 201, started.text
    request_id = message_store.get_all("WHATSAPP_GROUP_CREATE")[-1]["request_id"]
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        await apply_group_updates(
            uow,
            _whatsapp(
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
            ),
            adapters=SurfacePlatformAdapterRegistry(),
        )
    editor = await scenario.create_user("group-editor")
    await scenario.add_user_to_pod(user=editor, role="POD_EDITOR")
    return surface, started.json(), editor


async def test_what_the_bot_told_one_member_is_withheld_from_the_others(
    scenario, db_session: AsyncSession, fake_whatsapp, message_store, monkeypatch
):
    _wire_whatsapp(monkeypatch, fake_whatsapp)
    _surface, group, editor = await _pod_with_a_group(scenario, message_store)
    owner = UUID(scenario.owner_user["id"])
    lines = SurfaceGroupRepository(db_session)
    await lines.append_line(
        group_id=UUID(group["id"]),
        body="Salaries total 1.2M this quarter.",
        from_agent=True,
        answered_name="Deepak",
        answered_user_id=owner,
    )
    await lines.append_line(
        group_id=UUID(group["id"]),
        body="Lead times are two weeks.",
        from_agent=True,
        answered_name="Tiago",
        answered_from_public=True,
    )
    await db_session.commit()
    path = f"/pods/{scenario.pod_id}/groups/{group['id']}/timeline"

    as_owner = (await scenario.owner_client.get(path)).json()["items"]
    as_editor_response = await scenario.async_client.get(
        path, headers=auth_headers(editor)
    )
    assert as_editor_response.status_code == 200, as_editor_response.text
    as_editor = as_editor_response.json()["items"]

    assert [line["text"] for line in as_owner] == [
        "Salaries total 1.2M this quarter.",
        "Lead times are two weeks.",
    ]
    private, public = as_editor
    assert private["withheld"] is True
    assert private["text"] is None
    assert private["answered_name"] == "Deepak"
    assert public["text"] == "Lead times are two weeks."


async def test_only_the_owner_or_an_admin_changes_a_group_and_the_owner_is_told(
    scenario, db_session: AsyncSession, fake_whatsapp, message_store, monkeypatch
):
    _wire_whatsapp(monkeypatch, fake_whatsapp)
    _surface, group, editor = await _pod_with_a_group(scenario, message_store)
    path = f"/pods/{scenario.pod_id}/groups/{group['id']}"

    # An editor may configure the bot, but this group is the owner's.
    listed = await scenario.async_client.get(
        f"/pods/{scenario.pod_id}/groups", headers=auth_headers(editor)
    )
    assert listed.json()["items"][0]["can_manage"] is False
    refused = await scenario.async_client.patch(
        path, json={"take_over": True}, headers=auth_headers(editor)
    )
    assert refused.status_code == 403, refused.text

    # The editor answers for it now; the pod's admin takes it back.
    await SurfaceGroupRepository(db_session).set_owner(
        UUID(group["id"]), UUID(editor["id"])
    )
    await db_session.commit()
    taken = await scenario.owner_client.patch(path, json={"take_over": True})
    assert taken.status_code == 200, taken.text
    assert taken.json()["owner"]["user_id"] == scenario.owner_user["id"]

    inbox = await scenario.async_client.get(
        f"/pods/{scenario.pod_id}/notifications", headers=auth_headers(editor)
    )
    assert inbox.status_code == 200, inbox.text
    [notice] = inbox.json()["items"]
    assert "Acme order 1182" in notice["title"]
    assert "now answers for the people outside the space" in notice["body"]


async def test_a_group_whose_owner_left_the_pod_is_anybodys_to_take_on(
    scenario, db_session: AsyncSession, fake_whatsapp, message_store, monkeypatch
):
    _wire_whatsapp(monkeypatch, fake_whatsapp)
    _surface, group, editor = await _pod_with_a_group(scenario, message_store)
    gone = await scenario.create_user("left-the-pod")
    await SurfaceGroupRepository(db_session).set_owner(
        UUID(group["id"]), UUID(gone["id"])
    )
    await db_session.commit()

    response = await scenario.async_client.get(
        f"/pods/{scenario.pod_id}/groups/{group['id']}", headers=auth_headers(editor)
    )

    body = response.json()
    assert body["owner"] is None
    assert body["welcomes_outsiders"] is False
    assert body["can_manage"] is True


async def test_the_bots_own_switch_closes_every_group_to_people_outside(
    scenario, db_session: AsyncSession, fake_whatsapp, message_store, monkeypatch
):
    _wire_whatsapp(monkeypatch, fake_whatsapp)
    surface, group, _editor = await _pod_with_a_group(scenario, message_store)

    switched = await scenario.owner_client.patch(
        f"/pods/{scenario.pod_id}/surfaces/{surface['name']}",
        json={"config": {"groups": {"answers_outsiders": False}}},
    )
    assert switched.status_code == 200, switched.text
    assert switched.json()["config"]["groups"]["answers_outsiders"] is False

    body = (
        await scenario.owner_client.get(f"/pods/{scenario.pod_id}/groups/{group['id']}")
    ).json()
    assert body["bot_answers_outsiders"] is False
    assert body["welcomes_outsiders"] is False

    uow = SqlAlchemyUnitOfWork(db_session)
    context = await build_surface_ingress(uow).prepare_ingress(
        _whatsapp(
            "messages",
            {
                "messaging_product": "whatsapp",
                "metadata": _metadata(),
                "contacts": [{"profile": {"name": "Tiago"}, "wa_id": CLIENT}],
                "messages": [
                    {
                        "from": CLIENT,
                        "group_id": GROUP,
                        "id": f"wamid.{uuid4().hex}",
                        "timestamp": "1744344500",
                        "text": {"body": f"@{BUSINESS_NUMBER} are you there?"},
                        "type": "text",
                    }
                ],
            },
        )
    )
    await uow.commit()
    assert context is None
