"""The group roster a run is handed, from whatever record the pod has of it.

Where the pod keeps no log of a group, the platform's own history is the only
record of who is there -- and it names who spoke, not who holds access. So the
roster asks the pod which of those speakers are its own people; a platform's
silence about somebody is not a claim that they are. Where the pod does keep a
log, the roster comes from the whole log (``group_background``), so a member who
spoke last week is still somebody the run knows is in the room.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent_surfaces.services.group_log import (
    _MAX_PARTICIPANTS,
    participants_in_lines,
    pod_members_in_lines,
)
from app.modules.agent_surfaces.tests.unit.surface_doubles import (
    ExternalIdentityDouble,
    MembershipDouble,
)

pytestmark = pytest.mark.unit


def _line(
    author: str, *, external: str | None = None, outside: bool = False
) -> dict[str, object]:
    return {
        "author": author,
        "author_external_id": external,
        "text": "something",
        "outside_pod": outside,
    }


def test_each_person_appears_once_and_the_pods_verdict_is_what_is_reported():
    roster = participants_in_lines(
        [
            _line("Asha", external="U1"),
            _line("Tom", external="U2", outside=True),
            _line("Asha", external="U1"),
        ],
        verified={"U1"},
    )

    assert [(person.name, person.in_pod) for person in roster] == [
        ("Asha", True),
        ("Tom", False),
    ]


def test_a_speaker_the_pod_never_vouched_for_is_not_a_member():
    """The regression this roster exists for: a line the platform did not mark
    as another company's -- a colleague in the pod's own workspace, with no
    Lemma account -- used to be reported as a member holding member access."""
    roster = participants_in_lines([_line("Dana", external="U9")])

    assert [(person.name, person.in_pod) for person in roster] == [("Dana", False)]


def test_a_line_with_no_author_names_nobody():
    assert participants_in_lines([_line(""), _line("   ")]) == ()


def test_the_roster_stops_at_a_group_that_has_outgrown_a_list():
    roster = participants_in_lines(
        [
            _line(f"person-{index}", external=f"U{index}")
            for index in range(_MAX_PARTICIPANTS + 5)
        ],
        verified={"U0"},
    )

    assert len(roster) == _MAX_PARTICIPANTS


async def test_only_speakers_the_pod_resolved_to_a_member_come_back_verified():
    member, someone_else = uuid4(), uuid4()
    identities = ExternalIdentityDouble({"U1": member, "U2": someone_else})
    membership = MembershipDouble({member})

    verified = await pod_members_in_lines(
        lines=[
            _line("Asha", external="U1"),
            _line("Tom", external="U2"),
            _line("Dana", external="U3"),
            _line("Asha", external="U1"),
        ],
        pod_id=uuid4(),
        platform="SLACK",
        tenant_id="T-HOME",
        membership=membership,
        external_users=identities,
    )

    assert verified == {"U1"}
    assert identities.asked == [
        {
            "platform": "SLACK",
            "tenant_id": "T-HOME",
            "external_user_ids": ["U1", "U2", "U3"],
        }
    ]


async def test_a_window_with_no_platform_ids_asks_the_pod_nothing():
    identities = ExternalIdentityDouble({})

    verified = await pod_members_in_lines(
        lines=[_line("Asha"), _line("Tom", outside=True)],
        pod_id=uuid4(),
        platform="TEAMS",
        tenant_id="tenant-1",
        membership=MembershipDouble(set()),
        external_users=identities,
    )

    assert verified == set()
    assert identities.asked == []
