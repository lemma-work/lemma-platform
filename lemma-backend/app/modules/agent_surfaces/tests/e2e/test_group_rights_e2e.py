"""Who sees and changes a pod's groups, with two members in the pod -- end to end.

A group is a GROUP channel every member may read; what the bot told one member
there with that member's own access the others in it read too -- unless the
group keeps such answers to whoever asked (``answers_shared`` off): then they
see that it was answered, and for whom, not what was said. Its settings are its source's
(``/pods/{pod}/channels/{c}/sources/{s}``). A group's people outside the pod are
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
from app.modules.agent_surfaces.tests.e2e.group_timeline import (
    a_stranger,
    bot_for,
    group_by_id,
    say,
)
from app.modules.channels.contracts import Principal, Sender, channel_by_id, join
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
from app.modules.agent_surfaces.tests.e2e.surface_work_support import direct_lines
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
        f"/pods/{scenario.pod_id}/channels/whatsapp-groups",
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


@pytest.mark.parametrize("answers_shared", [True, False])
async def test_what_the_bot_told_one_member_the_others_read_unless_kept_to_them(
    scenario,
    db_session: AsyncSession,
    fake_whatsapp,
    message_store,
    monkeypatch,
    answers_shared: bool,
):
    _wire_whatsapp(monkeypatch, fake_whatsapp)
    _surface, group, editor = await _pod_with_a_group(scenario, message_store)
    changed = await scenario.owner_client.patch(
        f"/pods/{scenario.pod_id}/channels/{group['channel_id']}",
        json={"answers_shared": answers_shared},
    )
    assert changed.status_code == 200, changed.text
    owner = Principal.member(UUID(scenario.owner_user["id"]))
    the_group = await group_by_id(db_session, group["id"])
    tiago = await a_stranger(db_session, the_group, telegram_id="555", name="Tiago")
    bot = UUID(scenario.pod_id)  # the pod's own agent
    for text, sender in (
        (
            "What do salaries total?",
            Sender.member(owner.user_id, display_name="Deepak"),
        ),
        ("Salaries total 1.2M this quarter.", bot_for(owner, agent_id=bot)),
        (
            "How long are lead times?",
            Sender.contact(tiago.contact_id, display_name="Tiago"),
        ),
        ("Lead times are two weeks.", bot_for(tiago, agent_id=bot)),
    ):
        await say(db_session, the_group, text, sender=sender)
    # A WhatsApp group is private: its channel is read by the people in it,
    # and the editor is one of them -- but not whom the first answer was for.
    uow = SqlAlchemyUnitOfWork(db_session)
    channel = await channel_by_id(uow, the_group.channel_id)
    assert channel is not None
    await join(uow, channel=channel, principal=Principal.member(UUID(editor["id"])))
    await db_session.commit()
    path = f"/pods/{scenario.pod_id}/channels/{group['channel_id']}/messages"

    as_owner = (await scenario.owner_client.get(path)).json()["items"]
    as_editor_response = await scenario.async_client.get(
        path, headers=auth_headers(editor)
    )
    assert as_editor_response.status_code == 200, as_editor_response.text
    as_editor = as_editor_response.json()["items"]

    def from_bot(lines: list[dict]) -> list[dict]:
        return [line for line in lines if line["sender"]["kind"] == "AGENT"]

    assert [line["text"] for line in from_bot(as_owner)] == [
        "Salaries total 1.2M this quarter.",
        "Lead times are two weeks.",
    ]
    owners, public = from_bot(as_editor)
    assert owners["withheld"] is not answers_shared
    assert owners["text"] == (
        "Salaries total 1.2M this quarter." if answers_shared else None
    )
    assert owners["on_behalf_of"]["id"] == scenario.owner_user["id"]
    assert public["withheld"] is False
    assert public["text"] == "Lead times are two weeks."
    assert public["on_behalf_of"]["kind"] == "CONTACT"


async def test_only_the_owner_or_an_admin_changes_a_group_and_the_owner_is_told(
    scenario, db_session: AsyncSession, fake_whatsapp, message_store, monkeypatch
):
    _wire_whatsapp(monkeypatch, fake_whatsapp)
    _surface, group, editor = await _pod_with_a_group(scenario, message_store)
    path = (
        f"/pods/{scenario.pod_id}/channels/{group['channel_id']}/sources/{group['id']}"
    )

    # An editor may configure the bot, but this group is the owner's.
    shown = await scenario.async_client.get(path, headers=auth_headers(editor))
    assert shown.status_code == 200, shown.text
    assert shown.json()["can_manage"] is False
    refused = await scenario.async_client.patch(
        path, json={"owner": "me"}, headers=auth_headers(editor)
    )
    assert refused.status_code == 403, refused.text

    # The editor answers for it now; the pod's admin takes it back.
    await SurfaceGroupRepository(db_session).set_owner(
        UUID(group["id"]), UUID(editor["id"])
    )
    await db_session.commit()
    taken = await scenario.owner_client.patch(path, json={"owner": "me"})
    assert taken.status_code == 200, taken.text
    assert taken.json()["owner"]["id"] == scenario.owner_user["id"]

    # Told in their own channel, to them alone.
    [notice] = await direct_lines(
        db_session, pod_id=scenario.pod_id, user_id=editor["id"]
    )
    assert notice.members_only
    assert "Acme order 1182" in notice.body
    assert "now answers for the people outside the space" in notice.body


async def test_a_private_groups_settings_are_hidden_from_who_cannot_see_it(
    scenario, db_session: AsyncSession, fake_whatsapp, message_store, monkeypatch
):
    """A WhatsApp group is private to its people. A viewer reads the bot, but
    not this group: its settings -- the title, the invite link -- are not
    theirs, and they are told it does not exist rather than that it is
    forbidden."""
    _wire_whatsapp(monkeypatch, fake_whatsapp)
    _surface, group, _editor = await _pod_with_a_group(scenario, message_store)
    viewer = await scenario.create_user("group-viewer")
    await scenario.add_user_to_pod(user=viewer, role="POD_VIEWER")
    path = (
        f"/pods/{scenario.pod_id}/channels/{group['channel_id']}/sources/{group['id']}"
    )

    hidden = await scenario.async_client.get(path, headers=auth_headers(viewer))
    assert hidden.status_code == 404, hidden.text
    assert "Acme order 1182" not in hidden.text

    # Its own people read it as before.
    shown = await scenario.owner_client.get(path)
    assert shown.status_code == 200, shown.text
    assert shown.json()["title"] == "Acme order 1182"


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
        f"/pods/{scenario.pod_id}/channels/{group['channel_id']}/sources/{group['id']}",
        headers=auth_headers(editor),
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
        await scenario.owner_client.get(
            f"/pods/{scenario.pod_id}/channels/{group['channel_id']}/sources/{group['id']}"
        )
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
