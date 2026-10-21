"""Run the agent's approval and wait work items in-process, for e2e tests.

The decision's transaction writes ``agent.approval.execute`` and ``wait_for``
writes ``agent.wait.fire`` / ``agent.wait.poll``. Tests that drive the harness
in-process (and monkeypatch it) run those items here, through the runtime's own
claim and execute, on a registry holding only these kinds -- so nothing else
queued in the database (agent turns, surface deliveries) runs as a side effect.
"""

from __future__ import annotations

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.work import WorkClass, WorkRegistry
from app.core.infrastructure.work.db import SessionMaker
from app.core.infrastructure.work.testing import make_due, run_until_idle


def agent_wait_and_approval_registry() -> WorkRegistry:
    from app.modules.agent.events.approval_work import register_approval_work
    from app.modules.agent.events.wait_work import register_wait_work

    registry = WorkRegistry()
    register_approval_work(registry)
    register_wait_work(registry)
    return registry


async def run_approval_work(
    session_maker: SessionMaker | None = None, *, registry: WorkRegistry | None = None
) -> int:
    """Run every due ``agent.approval.execute`` item; returns how many ran.

    ``registry``: one the caller registered the kind on itself, e.g. with a
    failing executor (``register_approval_work(registry, execute=...)``).
    """
    from app.modules.agent.events.approval_work import register_approval_work

    if registry is None:
        registry = WorkRegistry()
        register_approval_work(registry)
    return await run_until_idle(
        session_maker or async_session_maker,
        registry=registry,
        classes=[WorkClass.INTERACTIVE],
    )


async def run_wait_work(
    session_maker: SessionMaker | None = None, *, due_now: bool = False
) -> int:
    """Run due wait items (``due_now``: pull every queued one forward first)."""
    maker = session_maker or async_session_maker
    if due_now:
        for kind in ("agent.wait.fire", "agent.wait.poll", "agent.wait.resolve_child"):
            await make_due(maker, kind=kind)
    return await run_until_idle(
        maker,
        registry=agent_wait_and_approval_registry(),
        classes=[WorkClass.EVENTS, WorkClass.AUTOMATION],
    )


__all__ = [
    "agent_wait_and_approval_registry",
    "run_approval_work",
    "run_wait_work",
]
