"""A consumer can cap its own retries below the inbox default."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.infrastructure.events.inbox import InboxConsumer, InboxStatus


class _RecordingInbox(InboxConsumer):
    """Records how a delivery was classified instead of writing it down."""

    def __init__(self) -> None:
        super().__init__(session_maker=lambda: None)  # type: ignore[arg-type,return-value]
        self.finished: list[InboxStatus] = []

    async def _finish(self, consumer, event_id, status, **_):  # type: ignore[override]
        self.finished.append(status)


async def _fail(inbox: _RecordingInbox, *, attempt: int, cap: int | None):
    return await inbox._retry_or_dead_letter(
        "consumer", uuid4(), "event", attempt, RuntimeError("boom"), cap
    )


@pytest.mark.asyncio
async def test_a_consumer_capped_at_one_gives_up_on_the_first_failure():
    inbox = _RecordingInbox()

    settled = await _fail(inbox, attempt=1, cap=1)

    assert settled is True, "a terminal failure is acknowledged, not redelivered"
    assert inbox.finished == [InboxStatus.DEAD_LETTER]


@pytest.mark.asyncio
async def test_without_a_cap_the_first_failure_is_retried():
    inbox = _RecordingInbox()

    with pytest.raises(RuntimeError):
        await _fail(inbox, attempt=1, cap=None)

    assert inbox.finished == [InboxStatus.RETRYING]
