"""A member is asked whether somebody new in a group, unlinked, is them.

Names never bind anybody; they only decide who is asked. Each test pins one
rule: the member whose first name the newcomer shares is told once (the
notice's key), nobody else is, nothing happens off Telegram or in a group
nobody answers for, and a failed read costs the notice, never the line.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import OperationalError

from app.modules.agent_surfaces.domain.groups import SurfaceGroup
from app.modules.agent_surfaces.services import link_nudges

pytestmark = pytest.mark.unit


def _group(*, platform: str = "TELEGRAM", owner: UUID | None = None) -> SurfaceGroup:
    return SurfaceGroup(
        id=uuid4(),
        channel_id=uuid4(),
        pod_id=uuid4(),
        surface_id=uuid4(),
        platform=platform,
        external_channel_id="-100",
        title="Kochi trip",
        owner_user_id=owner or uuid4(),
    )


class _Pod:
    """The pod's directory and the notices it sent, in memory."""

    def __init__(self) -> None:
        self.members = [
            SimpleNamespace(name="Anu Kumar", user_id=uuid4()),
            SimpleNamespace(name="Anupama Rao", user_id=uuid4()),
            SimpleNamespace(name="Deepa", user_id=uuid4()),
        ]
        self.told: list[dict[str, object]] = []

    async def directory(self, *, pod_id, requester_user_id, search):
        return SimpleNamespace(
            members=[m for m in self.members if search in m.name.casefold()]
        )

    async def notice(self, uow, **said):
        self.told.append(said)

    async def nudge(self, group: SurfaceGroup, contact: UUID, name: str) -> None:
        await link_nudges.nudge_lookalikes(
            object(),
            group=group,
            contact_id=contact,
            name=name,
            directory=self.directory,
            notice=self.notice,
        )


async def test_the_member_who_shares_the_newcomers_first_name_is_asked_once():
    pod, contact = _Pod(), uuid4()

    await pod.nudge(_group(), contact, "  Anu  ")

    assert [said["user_id"] for said in pod.told] == [pod.members[0].user_id]
    notice = pod.told[0]
    assert notice["key"] == f"link-nudge:{contact}:{pod.members[0].user_id}"
    assert "“Anu” is writing in “Kochi trip”" in str(notice["text"])
    assert "This is me" in str(notice["text"])


@pytest.mark.parametrize(
    ("group", "name"),
    [
        (_group(platform="SLACK"), "Anu"),
        (SurfaceGroup(**{**_group().__dict__, "owner_user_id": None}), "Anu"),
        (_group(), ""),
        (_group(), "Zed"),
    ],
)
async def test_nobody_is_asked_where_nothing_could_be_linked(group, name):
    pod = _Pod()

    await pod.nudge(group, uuid4(), name)

    assert pod.told == []


async def test_a_failed_read_costs_the_notice_not_the_line():
    async def broken(**_):
        raise OperationalError("select", {}, Exception("down"))

    await link_nudges.nudge_lookalikes(
        object(), group=_group(), contact_id=uuid4(), name="Anu", directory=broken
    )
