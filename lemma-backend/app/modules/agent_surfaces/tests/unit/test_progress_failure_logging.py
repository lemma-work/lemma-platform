"""A progress line the platform refused is best-effort, and on the record.

Progress edits never raise into the run, which is right; they used to fail at
debug, which `LOG_LEVEL=INFO` drops -- so a stuck progress line and a working
one left the same trace.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.services.egress_progress import SurfaceProgress

pytestmark = pytest.mark.asyncio


def _delivery(adapter: AsyncMock) -> SimpleNamespace:
    target = SimpleNamespace(adapter=adapter, credentials={}, event=None)
    return SimpleNamespace(
        resolve_egress_target=AsyncMock(return_value=target),
        egress_metadata=AsyncMock(return_value={}),
        uow=SimpleNamespace(session=None),
    )


async def test_a_refused_progress_edit_returns_the_handle_and_warns(caplog):
    adapter = AsyncMock()
    adapter.stream_progress = AsyncMock(side_effect=RuntimeError("edit refused"))
    progress = SurfaceProgress(delivery=_delivery(adapter))  # type: ignore[arg-type]

    with caplog.at_level(logging.WARNING):
        handle = await progress.send_progress_update(
            conversation_id=uuid4(),
            progress_text="Reading the file",
            progress_handle={"message_id": 7},
        )

    assert handle == {"message_id": 7}
    assert any(
        r.levelno == logging.WARNING and "progress_update_failed" in r.getMessage()
        for r in caplog.records
    )
    assert "edit refused" in caplog.text


async def test_a_progress_message_that_could_not_be_cleared_warns(caplog):
    adapter = AsyncMock()
    adapter.end_progress = AsyncMock(side_effect=RuntimeError("delete refused"))
    progress = SurfaceProgress(delivery=_delivery(adapter))  # type: ignore[arg-type]

    with caplog.at_level(logging.WARNING):
        await progress.clear_progress(
            conversation_id=uuid4(), progress_handle={"message_id": 7}
        )

    assert any(
        r.levelno == logging.WARNING and "progress_clear_failed" in r.getMessage()
        for r in caplog.records
    )
