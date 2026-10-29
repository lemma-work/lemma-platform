"""Two pytest sessions sharing a temp root never stop each other's containers.

Shared e2e Postgres, Redis and Kreuzberg were keyed by the temp root alone, so
two sessions given the same ``--basetemp`` shared one refcount: whichever
finished last by its own count stopped a Postgres the other was still using.
The slot is keyed by the run's owner too. Exercised on the real files the
fixtures use, under the same protocol, without Docker.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.test_shared_containers import enter_slot, leave_slot, shared_slot

pytestmark = pytest.mark.unit


def test_overlapping_sessions_on_one_root_leave_each_other_running(
    tmp_path: Path,
) -> None:
    first = shared_slot(tmp_path, "postgres", "pg", owner="lemma-e2e-1001")
    second = shared_slot(tmp_path, "postgres", "pg", owner="lemma-e2e-2002")

    assert first.name != second.name
    assert enter_slot(first) is True  # session one starts its container
    assert enter_slot(first) is False  # its second worker reuses it
    assert enter_slot(second) is True  # session two starts its own

    assert leave_slot(first) is False
    assert leave_slot(first) is True  # session one's last worker out
    # Session two is untouched: still counted in, still running.
    assert second.refs.read_text() == "1"

    assert leave_slot(second) is True


def test_one_sessions_workers_share_one_container(tmp_path: Path) -> None:
    owner = "lemma-e2e-1001"
    workers = [shared_slot(tmp_path, "redis", "rd", owner=owner) for _ in range(3)]

    assert len({slot.name for slot in workers}) == 1
    assert [enter_slot(slot) for slot in workers] == [True, False, False]
    assert [leave_slot(slot) for slot in workers] == [False, False, True]
