"""A surface tool answers the model with a result even when the lookup blows up."""

from __future__ import annotations

import pytest

from app.modules.agent_surfaces.platforms.tool_guard import guarded_tool_result


async def _ok() -> str:
    return "found"


async def _boom() -> str:
    raise RuntimeError("upstream exploded")


@pytest.mark.asyncio
async def test_a_healthy_call_passes_its_result_through() -> None:
    assert await guarded_tool_result(_ok(), tool="t", failure="failed") == "found"


@pytest.mark.asyncio
async def test_a_failing_call_yields_the_failure_result_instead_of_raising() -> None:
    assert await guarded_tool_result(_boom(), tool="t", failure="failed") == "failed"
