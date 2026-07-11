from __future__ import annotations

from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.modules.function.domain.events import FunctionRunExecutionRequestedEvent
from app.modules.function.events import handlers
from app.modules.test_support.fakes import PassthroughEventInbox


@pytest.mark.asyncio
async def test_unknown_function_event_is_ignored_before_inbox_claim():
    inbox = SimpleRecordingInbox()

    await handlers.handle_function_run_event(
        {"event_type": "function.run.future_event"},
        Mock(),
        uow_factory=Mock(),
        job_queue=Mock(),
        inbox=inbox,
    )

    assert inbox.calls == []


@pytest.mark.asyncio
async def test_known_function_event_runs_projection_inside_inbox(monkeypatch):
    event = FunctionRunExecutionRequestedEvent(
        run_id=uuid4(),
        function_id=uuid4(),
    ).model_dump(mode="json")
    projection = AsyncMock()
    monkeypatch.setattr(handlers, "_process_function_run_event", projection)
    logger = Mock()
    uow_factory = Mock()
    job_queue = Mock()

    await handlers.handle_function_run_event(
        event,
        logger,
        uow_factory=uow_factory,
        job_queue=job_queue,
        inbox=PassthroughEventInbox(),
    )

    projection.assert_awaited_once_with(
        event,
        fs_logger=logger,
        uow_factory=uow_factory,
        job_queue=job_queue,
    )


class SimpleRecordingInbox:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    async def process(self, *args):
        self.calls.append(args)
        return True
