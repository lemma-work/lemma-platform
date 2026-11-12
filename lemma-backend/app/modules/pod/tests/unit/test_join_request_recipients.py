"""Who a request to join is sent to is read across every page of members."""

from __future__ import annotations

import pytest

from app.modules.pod.events.join_request_work import _every


@pytest.mark.asyncio
async def test_every_page_is_read_until_the_cursor_runs_out():
    pages = {None: (["a", "b"], "c1"), "c1": (["c"], "c2"), "c2": (["d"], None)}
    asked: list[str | None] = []

    async def page(cursor: str | None):
        asked.append(cursor)
        return pages[cursor]

    assert await _every(page) == ["a", "b", "c", "d"]
    assert asked == [None, "c1", "c2"]


@pytest.mark.asyncio
async def test_a_single_page_is_read_once():
    async def page(cursor: str | None):
        assert cursor is None
        return (["only"], None)

    assert await _every(page) == ["only"]
