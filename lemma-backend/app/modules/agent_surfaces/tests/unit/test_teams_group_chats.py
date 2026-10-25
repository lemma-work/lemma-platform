"""A Teams group chat is a group with the bot in it, not a DM.

Teams marks a chat of several people ``groupChat``. It has no channel id, which
the parser used to read as "a DM" -- so everyone in the chat was answered as if
alone with the bot, each in a private conversation. A group chat is the Slack
``mpim`` case: nobody configures a route for it, being in it is the
authorization, and the bot answers only when it is addressed.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ConversationType,
    SurfaceConfig,
    SurfacePlatform,
    is_group_dm,
)
from app.modules.agent_surfaces.platforms.teams.parser import TeamsMessageParser
from app.modules.agent_surfaces.services.workspace_channels import (
    note_workspace_channel,
    workspace_channel_title,
)
from app.modules.channels.contracts import SourceKind

pytestmark = pytest.mark.unit

CHAT = "19:meeting_abc@thread.v2"
TENANT = "tenant-123"


def _activity(*, conversation_type: str, mentioned: bool = True) -> dict:
    activity = {
        "type": "message",
        "id": "1700000000001",
        "text": "<at>Lemma</at> what did we agree?" if mentioned else "lunch?",
        "from": {"id": "29:asha", "name": "Asha", "aadObjectId": "aad-asha"},
        "recipient": {"id": "28:bot", "name": "Lemma"},
        "conversation": {
            "id": CHAT,
            "conversationType": conversation_type,
            "tenantId": TENANT,
        },
        "channelData": {"tenant": {"id": TENANT}},
        "serviceUrl": "https://smba.trafficmanager.net/amer/",
    }
    if mentioned:
        activity["entities"] = [
            {
                "type": "mention",
                "mentioned": {"id": "28:bot", "name": "Lemma"},
                "text": "<at>Lemma</at>",
            }
        ]
    return activity


def _surface() -> AgentSurfaceEntity:
    return AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        name="teams",
        agent_id=uuid4(),
        surface_type=SurfacePlatform.TEAMS,
        pod_connector_slug="own-bot",
        external_tenant_id=TENANT,
        surface_identity_id="28:bot",
        config=SurfaceConfig(),
        is_active=True,
    )


def test_a_group_chat_is_a_group_not_a_dm():
    parsed = TeamsMessageParser().parse(_activity(conversation_type="groupChat"))

    assert parsed is not None
    assert parsed.is_dm is False
    assert parsed.conversation_type is ConversationType.EXTERNAL_GROUP
    assert is_group_dm(parsed)
    # The chat is the one place: no thread, answered into the chat itself.
    assert parsed.external_channel_id == CHAT
    assert parsed.external_thread_id == CHAT
    assert parsed.metadata["channel_type"] == "groupChat"
    assert parsed.metadata["reply_to_id"] is None


def test_a_personal_chat_is_still_a_dm():
    parsed = TeamsMessageParser().parse(_activity(conversation_type="personal"))

    assert parsed is not None
    assert parsed.is_dm is True
    assert not is_group_dm(parsed)


def test_a_group_chat_needs_no_route_but_does_need_the_bot_addressed():
    surface = _surface()
    addressed = TeamsMessageParser().parse(_activity(conversation_type="groupChat"))
    chatter = TeamsMessageParser().parse(
        _activity(conversation_type="groupChat", mentioned=False)
    )

    assert addressed is not None and chatter is not None
    assert surface.allows_inbound_event(addressed)
    assert not surface.allows_inbound_event(chatter, thread_is_ours=False)


class _Seats:
    def __init__(self) -> None:
        self.made: list = []

    async def places_hearing(self, *, platform, external_channel_id, surface_ids):
        return [s for s in self.made if s.external_channel_id == external_channel_id]

    async def source_for(self, *, pod_id, place, home):
        source = SimpleNamespace(
            id=uuid4(),
            pod_id=pod_id,
            platform=place.platform,
            kind=place.kind,
            external_channel_id=place.external_channel_id,
            title=home.title,
            is_private=place.is_private,
            shared_externally=False,
        )
        self.made.append(source)
        return source


async def test_a_group_chat_becomes_a_group_channel_once():
    seats = _Seats()
    surface = _surface()
    parsed = TeamsMessageParser().parse(
        _activity(conversation_type="groupChat", mentioned=False)
    )
    assert parsed is not None

    first = await note_workspace_channel(SimpleNamespace(), seats, [surface], parsed)
    again = await note_workspace_channel(SimpleNamespace(), seats, [surface], parsed)

    assert (first, again) == (True, False)
    [source] = seats.made
    assert source.platform == "TEAMS"
    assert source.kind is SourceKind.CHAT
    assert source.title == "Group chat"
    # A group chat is its members' alone.
    assert source.is_private is True


async def test_a_teams_channel_nobody_routed_is_not_a_channel_here():
    seats = _Seats()
    activity = _activity(conversation_type="channel")
    activity["conversation"]["id"] = "19:general@thread.tacv2"
    activity["channelData"]["channel"] = {"id": "19:general@thread.tacv2"}
    parsed = TeamsMessageParser().parse(activity)
    assert parsed is not None and not is_group_dm(parsed)

    wrote = await note_workspace_channel(SimpleNamespace(), seats, [_surface()], parsed)

    assert wrote is False
    assert seats.made == []
    assert workspace_channel_title(None, parsed) is None
