"""The active-users window bounds are UUIDv7 floors on the primary key."""

from __future__ import annotations

import time
from uuid import uuid7

from app.modules.usage.infrastructure.active_users_gauge import uuid7_floor


def test_the_floor_sorts_before_ids_minted_after_it_and_after_older_ones() -> None:
    before = uuid7()
    time.sleep(0.005)
    floor = uuid7_floor(time.time())
    time.sleep(0.005)
    after = uuid7()
    assert before < floor <= after
    assert floor.version == 7
