"""The group roster a run is handed, from whatever record the pod has of it.

Where the pod keeps no log of a group, the platform's own history is the only
record of who is there, and the platform is what marks a line from another
workspace as outside the pod. Where it does keep one, the roster comes from the
whole log (``group_background``), so a member who spoke last week is still
somebody the run knows is in the room.
"""

from __future__ import annotations

import pytest

from app.modules.agent_surfaces.services.group_log import (
    _MAX_PARTICIPANTS,
    participants_in_lines,
)

pytestmark = pytest.mark.unit


def _line(author: str, *, outside: bool = False) -> dict[str, object]:
    return {"author": author, "text": "something", "outside_pod": outside}


def test_each_person_appears_once_and_outside_the_pod_is_marked():
    roster = participants_in_lines(
        [
            _line("Asha"),
            _line("Tom", outside=True),
            _line("Asha"),
        ]
    )

    assert [(person.name, person.in_pod) for person in roster] == [
        ("Asha", True),
        ("Tom", False),
    ]


def test_a_line_with_no_author_names_nobody():
    assert participants_in_lines([_line(""), _line("   ")]) == ()


def test_the_roster_stops_at_a_group_that_has_outgrown_a_list():
    roster = participants_in_lines(
        [_line(f"person-{index}") for index in range(_MAX_PARTICIPANTS + 5)]
    )

    assert len(roster) == _MAX_PARTICIPANTS
