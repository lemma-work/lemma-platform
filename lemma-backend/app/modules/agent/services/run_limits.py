"""What bounds one agent run: when it must stop, and what it may spend.

Two policies, together because they answer the same question from opposite
ends. ``make_stop_checker`` is the person saying stop; ``budget_for_run`` is the
run running out of the allowance nobody gave it before.

Extracted from ``agent_runner_service`` rather than added to it: that file sits
against the architecture ratchet's file-size limit, and policy that can be read
without the orchestration around it is the part worth moving out.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.config import agent_settings
from app.modules.agent.domain.run_budget import RunBudget, RunSpend
from app.modules.agent.domain.value_objects import AgentRunStatus
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.agent.services.brief_lines import run_is_unattended, run_source_of


def budget_for_run(run: object) -> RunSpend:
    """What this run may spend before it pauses and asks whether to carry on.

    An unattended run gets a longer clock, not an unlimited one: nobody is
    waiting for it, so a slow run costs nothing anybody feels — and nobody is
    watching the spend either, which is exactly why it still needs a ceiling.
    Getting this wrong in the other direction would turn every overnight
    automation into a silent stall, so it has its own test.
    """
    wall_clock = (
        agent_settings.agent_run_budget_unattended_wall_clock_seconds
        if run_is_unattended(run_source_of(run))
        else agent_settings.agent_run_budget_wall_clock_seconds
    )
    return RunSpend(
        budget=RunBudget(
            model_requests=agent_settings.agent_run_budget_model_requests,
            wall_clock_seconds=wall_clock,
            consecutive_tool_failures=agent_settings.agent_run_budget_tool_failures,
        )
    )


def make_stop_checker(
    agent_run_id: UUID, *, uow_factory: UnitOfWorkFactory
) -> Callable[[], Awaitable[bool]]:
    """The stop signal the harness polls, throttled and sticky."""

    async def _stop_requested() -> bool:
        async with uow_factory() as uow:
            agent_run = await ConversationRepository(uow).get_agent_run(agent_run_id)
        return agent_run is not None and agent_run.status in {
            AgentRunStatus.STOP_REQUESTED,
            AgentRunStatus.STOPPED,
        }

    return throttled_sticky(
        _stop_requested,
        interval=agent_settings.agent_run_stop_poll_interval_seconds,
    )


def throttled_sticky(
    check: Callable[[], Awaitable[bool]],
    *,
    interval: float,
    clock: Callable[[], float] = time.monotonic,
) -> Callable[[], Awaitable[bool]]:
    """Ask ``check`` at most once per ``interval``, and stop asking once true.

    The harness polls this at every streaming checkpoint — per token delta, per
    part, per tool call. Asking the database each time issues one ``SELECT`` per
    token across every concurrent run, which was the dominant per-token database
    load under streaming. A stop request is still honoured within the interval,
    because being at most one interval late to stop is not something anybody can
    perceive.

    ``check`` and ``clock`` are parameters so the throttling and the stickiness
    can be tested for what they are — timing logic — without a database and
    without replacing a name inside the module under test.
    """
    stopped = False
    last_checked: float | None = None

    async def _check() -> bool:
        nonlocal stopped, last_checked
        if stopped:
            return True
        now = clock()
        if last_checked is not None and (now - last_checked) < interval:
            return False
        last_checked = now
        if await check():
            stopped = True
            return True
        return False

    return _check
