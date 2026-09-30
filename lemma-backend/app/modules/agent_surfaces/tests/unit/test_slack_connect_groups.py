"""A Slack channel shared with another company is a group with outsiders in it.

Everyone in the pod's own Slack workspace is a colleague: somebody there who is
not in the pod is somebody to invite, never a stranger to be answered from what
the pod made Public. Only a channel Slack marks as shared with another company
(Slack Connect), and only a sender from that other company, is an outsider.
Getting either half wrong answers a colleague as a stranger, or a stranger with
somebody's access.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.domain.entities import (
    ParsedInboundSurfaceEvent,
    ResolvedSurfaceUser,
    SurfaceChannelRoute,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.groups import SurfaceGroup
from app.modules.agent_surfaces.platforms.slack.parser import SlackMessageParser
from app.modules.agent_surfaces.services.outsiders import OutsiderDoor
from app.modules.agent_surfaces.services.slack_groups import (
    answers_slack_outsider,
    slack_group_title,
)

pytestmark = pytest.mark.unit

POD = uuid4()
HOME = "T0HOME"
THEIRS = "T0ACME"
CHANNEL = "C0SHARED"


def _said(
    *, shared: bool, sender_team: str | None, channel_type: str = "channel"
) -> ParsedInboundSurfaceEvent:
    event = {
        "type": "message",
        "user": "U0SENDER",
        "text": "<@U0BOT> what are your lead times?",
        "ts": "1700000000.000100",
        "channel": CHANNEL,
        "channel_type": channel_type,
    }
    if sender_team:
        event["user_team"] = sender_team
    payload = {"type": "event_callback", "team_id": HOME, "event": event}
    if shared:
        payload["is_ext_shared_channel"] = True
    parsed = SlackMessageParser().parse(payload)
    assert parsed is not None
    return parsed


# ------------------------------------------------------ what Slack says


def test_a_sender_from_the_other_company_is_from_outside():
    parsed = _said(shared=True, sender_team=THEIRS)

    assert parsed.metadata["is_ext_shared_channel"] is True
    assert parsed.metadata["sender_is_external"] is True


def test_a_colleague_in_a_shared_channel_is_not():
    parsed = _said(shared=True, sender_team=HOME)

    assert parsed.metadata["is_ext_shared_channel"] is True
    assert "sender_is_external" not in parsed.metadata


def test_nobody_is_from_outside_in_a_channel_that_is_not_shared():
    """A foreign team id on an unshared channel is not Slack Connect."""
    parsed = _said(shared=False, sender_team=THEIRS)

    assert "is_ext_shared_channel" not in parsed.metadata
    assert "sender_is_external" not in parsed.metadata


def test_a_sender_whose_team_slack_did_not_name_is_not_taken_for_outside():
    parsed = _said(shared=True, sender_team=None)

    assert "sender_is_external" not in parsed.metadata


@pytest.mark.parametrize(
    ("group_shared", "sender_team", "answered"),
    [
        (True, THEIRS, True),
        (True, HOME, False),
        # The row not yet marked shared: nothing opens until it is.
        (False, THEIRS, False),
    ],
)
def test_only_the_other_company_in_a_shared_channel_is_answered_as_outsiders(
    group_shared, sender_team, answered
):
    parsed = _said(shared=True, sender_team=sender_team)

    assert answers_slack_outsider(group_shared, parsed) is answered


# ------------------------------------------------------- at the door


def _door(group: SurfaceGroup) -> OutsiderDoor:
    async def _get(*, surface_id, external_channel_id):
        return group

    async def _pod_ids(user_id):
        return []

    async def _allow(*, group_id, sender_external_id):
        return True

    door = OutsiderDoor(
        uow=SimpleNamespace(session=None),
        membership=SimpleNamespace(get_user_pod_ids=_pod_ids),
        limiter=SimpleNamespace(allow=_allow),
    )
    door.groups = SimpleNamespace(get=_get)
    return door


def _slack_group(*, shared: bool) -> SurfaceGroup:
    return SurfaceGroup(
        pod_id=POD,
        surface_id=uuid4(),
        platform="SLACK",
        external_channel_id=CHANNEL,
        owner_user_id=uuid4(),
        shared_externally=shared,
    )


def _slack_surface():
    return SimpleNamespace(id=uuid4(), pod_id=POD, surface_type=SurfacePlatform.SLACK)


def _unknown(external_user_id: str = "U0SENDER") -> ResolvedSurfaceUser:
    return ResolvedSurfaceUser(external_user_id=external_user_id)


async def test_the_other_company_is_answered_for_the_pod_in_a_shared_channel():
    group = _slack_group(shared=True)

    found = await _door(group).group_welcoming(
        surface=_slack_surface(),
        parsed=_said(shared=True, sender_team=THEIRS),
        sender=_unknown(),
    )

    assert found == group


async def test_a_colleague_outside_the_pod_is_not_answered_as_a_stranger():
    found = await _door(_slack_group(shared=True)).group_welcoming(
        surface=_slack_surface(),
        parsed=_said(shared=True, sender_team=HOME),
        sender=_unknown(),
    )

    assert found is None


async def test_nobody_is_an_outsider_in_the_workspaces_own_channels():
    found = await _door(_slack_group(shared=False)).group_welcoming(
        surface=_slack_surface(),
        parsed=_said(shared=False, sender_team=THEIRS),
        sender=_unknown(),
    )

    assert found is None


# ------------------------------------------------------- what it is called


@pytest.mark.parametrize(
    ("channel_name", "title"),
    [("launch", "#launch"), ("#launch", "#launch"), ("", None), (None, None)],
)
def test_a_channel_is_listed_under_its_slack_name(channel_name, title):
    route = SurfaceChannelRoute(channel_id=CHANNEL, channel_name=channel_name)

    assert slack_group_title(route, _said(shared=False, sender_team=None)) == title


def test_a_group_dm_has_no_name_of_its_own():
    parsed = _said(shared=False, sender_team=None, channel_type="mpim")

    assert slack_group_title(None, parsed) == "Group DM"
