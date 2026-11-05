"""The once-a-day ledger behind "Ask members to connect".

It is the whole of the button's promise -- nobody is asked about one connector
twice in a day, whoever presses it -- so it has to keep that promise while Redis
is away too: it asks nobody, rather than everybody again. And it is written
only once a line was posted, so it never says somebody was asked who was not.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.connectors.infrastructure.adapters.member_coverage import (
    ASK_AGAIN_AFTER_SECONDS,
    RedisAskLedger,
)

pytestmark = pytest.mark.unit


class _Cache:
    def __init__(self) -> None:
        self.held: dict[str, str] = {}

    async def get_raw(self, suffix: str) -> str | None:
        return self.held.get(suffix)

    async def set_raw(self, suffix: str, payload: str, **_kw) -> None:
        self.held[suffix] = payload


class _GoneCache:
    async def get_raw(self, *_a, **_kw) -> str | None:
        raise ConnectionError("redis is away")

    async def set_raw(self, *_a, **_kw) -> None:
        raise ConnectionError("redis is away")


async def test_reading_does_not_mark_a_member_asked() -> None:
    ledger = RedisAskLedger(cache=_Cache())
    connector, member = uuid4(), uuid4()

    assert await ledger.asked_today(pod_connector_id=connector, user_id=member) is False
    assert await ledger.asked_today(pod_connector_id=connector, user_id=member) is False


async def test_a_mark_is_per_connector_and_per_member() -> None:
    ledger = RedisAskLedger(cache=_Cache())
    connector, other, member = uuid4(), uuid4(), uuid4()

    await ledger.mark(pod_connector_id=connector, user_id=member)

    assert await ledger.asked_today(pod_connector_id=connector, user_id=member)
    assert not await ledger.asked_today(pod_connector_id=other, user_id=member)
    assert not await ledger.asked_today(pod_connector_id=connector, user_id=uuid4())


async def test_an_unreachable_ledger_asks_nobody() -> None:
    ledger = RedisAskLedger(cache=_GoneCache())

    # None, not False: "could not tell", so nobody is asked and the editor is
    # not told they were asked already.
    assert await ledger.asked_today(pod_connector_id=uuid4(), user_id=uuid4()) is None
    # A mark that cannot be written is dropped, not raised: the line is out.
    await ledger.mark(pod_connector_id=uuid4(), user_id=uuid4())


def test_the_ledger_forgets_after_a_day() -> None:
    assert ASK_AGAIN_AFTER_SECONDS == 24 * 60 * 60
