"""One place a surface tool turns an unexpected failure into a tool result.

A tool the model calls has to answer with something the model can read, whatever
went wrong behind it: an exception escaping a tool call aborts the run's step and
the model never learns the lookup failed. Each surface tool used to carry its own
copy of this, with its own log event; the guard is the copy that stays.
"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import TypeVar

from app.core.log.log import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


async def guarded_tool_result(call: Awaitable[T], *, tool: str, failure: T) -> T:
    """Await ``call``; on any failure log it and hand back ``failure`` instead."""
    try:
        return await call
    except Exception:
        logger.debug("surface.tool.failed", tool=tool, exc_info=True)
        return failure
