"""Exceptions that are control flow, not failures, wherever a tool runs.

A tool call that raises one of these is not reported to the model as an error:
it carries meaning for the run loop (retry, deferral, a usage limit, an
unusable response), pauses the run (``AgentInputRequired``), or signals
cancellation and shutdown. ``LeaseLost`` is a commit refused because another
worker holds the turn now: the turn ends here, and the model must not be handed
an error to work around. Here, beside the providers, because pydantic-ai's own
signals are among them and a nested model call inside a tool can raise them.
"""

from __future__ import annotations

import asyncio

from pydantic_ai.exceptions import (
    ApprovalRequired,
    CallDeferred,
    ModelRetry,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)

from app.core.infrastructure.work import LeaseLost
from app.modules.agent.domain.tools import AgentInputRequired

_CONTROL_FLOW_EXCEPTIONS: tuple[type[BaseException], ...] = (
    ModelRetry,
    CallDeferred,
    ApprovalRequired,
    AgentInputRequired,
    UsageLimitExceeded,
    UnexpectedModelBehavior,
    LeaseLost,
    asyncio.CancelledError,
    KeyboardInterrupt,
    SystemExit,
)


def is_control_flow_exception(exc: BaseException) -> bool:
    """Return True if ``exc`` must propagate rather than become a tool response."""
    return isinstance(exc, _CONTROL_FLOW_EXCEPTIONS)


def is_usage_limit(exc: BaseException) -> bool:
    """A run or background call stopped at its usage limit."""
    from app.modules.agent.domain.background import BackgroundLimitReached

    return isinstance(exc, UsageLimitExceeded | BackgroundLimitReached)
